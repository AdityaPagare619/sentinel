"""Tests for the Stage-0 shadow tap (design 06 §a) and its read-only proof.

All tests hit a real loopback ThreadingHTTPServer in-process. The shadow
routes are additive: existing receiver behavior is untouched.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import hashlib
import hmac
import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from sentinel.audit import AuditLog
from sentinel.correlator import Correlator, fingerprint_for
from sentinel.forwarder import Forwarder
from sentinel.gate import Gate
from sentinel.models import Thresholds
from sentinel.quantized import AllowlistEntry, Attestation
from sentinel.receiver import Pipeline, ReceiverConfig, make_server
from sentinel.shadow import (
    REQUIRED_PD_EVENTS,
    ShadowConfig,
    ShadowPipeline,
    ShadowStore,
    policy_action,
    subscription_coverage_check,
    threshold_counterfactual,
    verify_bearer,
    verify_pd_signature,
)
from sentinel.state import build_state

from tests.test_gate import canned, fresh_monitor_for


# ---------------------------------------------------------------- fixtures

def _attested_entries(fingerprints):
    """Dual-attested allowlist entries (ADR-013 interim path).

    A bare fingerprint in the allowlist no longer suppresses — that was
    the M-1 units error. Fixtures that script would-suppress verdicts
    must carry the dual human attestation the quantized prob lock
    requires pre-fit (design §2.3). Attestations are dated relative to
    now so the 30-day TTL never rots the fixture.
    """
    now = datetime.now(timezone.utc)
    return [AllowlistEntry(
        fingerprint=fp, author="fixture-author",
        attestations=[
            Attestation("alice", now - timedelta(days=1),
                        "lrq-shadow-fixture-01", 30),
            Attestation("bob", now - timedelta(days=1),
                        "lrq-shadow-fixture-01", 30),
        ]) for fp in (fingerprints or [])]

class FixedClient:
    """Deterministic stand-in: one canned response for every call."""

    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def decide(self, state, questions):
        self.calls.append({"state": state, "questions": questions})
        if self.exc is not None:
            raise self.exc("boom")
        return self.response


class ScriptedClient:
    """Picks a canned response by alert title (state['title'])."""

    def __init__(self, by_title, default=None):
        self.by_title = by_title
        self.default = default
        self.calls = []

    def decide(self, state, questions):
        self.calls.append(state.get("title"))
        title = state.get("title") or ""
        return self.by_title.get(title, self.default)


class RefusingForwarder(Forwarder):
    """Write-path double that fails the test on ANY invocation.

    The shadow tap must never reach it — not even once.
    """

    def __init__(self):
        self.calls = []

    def forward(self, *args, **kwargs):
        self.calls.append(("forward", args, kwargs))
        raise AssertionError("shadow path invoked the write path (forward)")

    def forward_raw(self, *args, **kwargs):
        self.calls.append(("forward_raw", args, kwargs))
        raise AssertionError("shadow path invoked the write path (forward_raw)")


PD_SECRET = "pd-signing-secret"


def _pd_v3(event_type="incident.triggered", incident_id="PABC123",
           event_id=None, priority="P3", urgency="low",
           service="payments-api", title="HighErrorRate",
           occurred_at="2026-10-03T10:00:00Z"):
    return {"event": {
        "id": event_id or f"ev-{incident_id}-{event_type}",
        "event_type": event_type,
        "resource_type": "incident",
        "occurred_at": occurred_at,
        "data": {
            "id": incident_id,
            "type": "incident",
            "title": title,
            "status": event_type.split(".")[-1],
            "urgency": urgency,
            "priority": {"id": "PR1", "summary": priority, "name": priority},
            "service": {"id": "SVC1", "summary": service},
            "html_url": f"https://example.pagerduty.com/incidents/{incident_id}",
        }}}


def _pd_sig(body: bytes, secret=PD_SECRET):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-PagerDuty-Signature": digest,
            "X-Webhook-Subscription": "sub-1"}


def _og(action="Create", alert_id="og-1", priority="P3",
        message="disk full", with_details=True):
    alert = {"alertId": alert_id, "message": message, "priority": priority,
             "source": "web-1", "teams": [{"name": "payments"}]}
    if with_details:
        alert["description"] = "disk at 98%"
        alert["details"] = {"region": "us-east"}
    return {"alert": alert, "action": action, "actionTimestamp": 1720000000}


def _am(status="firing", fingerprint="fp-1", severity="warning",
        service="web", alertname="HighCpu", starts_at="2026-10-03T10:00:00Z"):
    return {
        "receiver": "sentinel-shadow",
        "status": status,
        "alerts": [{
            "status": status,
            "labels": {"alertname": alertname, "service": service,
                       "severity": severity, "region": "us-east"},
            "annotations": {"summary": f"{alertname} on {service}"},
            "startsAt": starts_at,
            "endsAt": "0001-01-01T00:00:00Z",
            "fingerprint": fingerprint,
        }],
        "commonLabels": {"service": service},
    }


class ShadowTestBase(unittest.TestCase):
    def _start(self, jev_client, allowlist=None, pd_secret=PD_SECRET,
               og_token=None, am_token="am-token"):
        self.write_path = RefusingForwarder()
        audit = AuditLog(":memory:")
        gate = Gate(jev_client, Thresholds(), _attested_entries(allowlist), audit,
                    shadow=True)
        pipeline = Pipeline(Correlator(), gate, self.write_path, audit,
                            ReceiverConfig())
        config = ShadowConfig(enabled=True, pd_secret=pd_secret,
                              opsgenie_token=og_token,
                              alertmanager_token=am_token)
        config.validate()
        pipeline.shadow_pipeline = ShadowPipeline(
            gate=Gate(jev_client, Thresholds(), _attested_entries(allowlist), audit,
                      shadow=True),
            correlator=Correlator(), store=ShadowStore(), config=config,
            allowlist=set(allowlist or []))
        self.pipeline = pipeline
        self.store = pipeline.shadow_pipeline.store
        self.server = make_server(0, pipeline)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _post(self, path, body, headers=None):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.getcode(), json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _pd_post(self, payload, secret=PD_SECRET, sign=True,
                 extra_headers=None):
        body = json.dumps(payload).encode()
        headers = dict(extra_headers or {})
        if sign:
            headers.update(_pd_sig(body, secret))
        return self._post("/shadow/pagerduty", body, headers)


# ---------------------------------------------------------------- HMAC

class TestPdSignature(ShadowTestBase):
    def setUp(self):
        self._start(FixedClient(canned(p1=0.9, conf=0.95)))

    def test_valid_signature_accepted(self):
        code, resp = self._pd_post(_pd_v3())
        self.assertEqual(code, 202)
        self.assertEqual(resp["status"], "accepted")
        self.assertEqual(resp["events"], 1)

    def test_tampered_body_rejected(self):
        payload = _pd_v3()
        body = json.dumps(payload).encode()
        headers = _pd_sig(body)
        tampered = body.replace(b"HighErrorRate", b"HighErrorRate!")
        code, resp = self._post("/shadow/pagerduty", tampered, headers)
        self.assertEqual(code, 403)
        self.assertEqual(self.store.tap_health()["dropped_by_reason"]
                         ["bad_signature"], 1)
        self.assertEqual(self.write_path.calls, [])

    def test_missing_signature_rejected(self):
        code, resp = self._pd_post(_pd_v3(), sign=False)
        self.assertEqual(code, 403)
        self.assertEqual(self.store.tap_health()["dropped_by_reason"]
                         ["bad_signature"], 1)

    def test_wrong_secret_rejected(self):
        code, _resp = self._pd_post(_pd_v3(), secret="wrong-secret")
        self.assertEqual(code, 403)

    def test_verify_unit(self):
        body = b'{"a":1}'
        ok, _ = verify_pd_signature(body, PD_SECRET,
                                    _pd_sig(body))
        self.assertTrue(ok)
        ok, _ = verify_pd_signature(body + b"!", PD_SECRET, _pd_sig(body))
        self.assertFalse(ok)
        ok, _ = verify_pd_signature(body, PD_SECRET, {})
        self.assertFalse(ok)
        ok, _ = verify_pd_signature(body, None, _pd_sig(body))
        self.assertFalse(ok)


# ---------------------------------------------------------------- lifecycle

class TestPdLifecycle(ShadowTestBase):
    def setUp(self):
        self._start(FixedClient(canned(p1=0.9, conf=0.95)))

    def test_full_lifecycle_set_parsed(self):
        # All five mandatory event types (F1) must parse and fold into the
        # episode — priority_updated is the one the pre-mortem names.
        for et in sorted(REQUIRED_PD_EVENTS):
            code, _ = self._pd_post(_pd_v3(event_type=et, incident_id="P-LIFE",
                                           priority="P4"))
            self.assertEqual(code, 202, et)
        ep = self.store.episodes["pd:inc/P-LIFE"]
        self.assertTrue(ep.human_paged)
        self.assertTrue(ep.human_acknowledged)
        self.assertTrue(ep.human_resolved)
        self.assertEqual(ep.event_count, 5)

    def test_priority_updated_moves_severity_and_reruns_gate(self):
        client = self.pipeline.shadow_pipeline.gate.client
        self._pd_post(_pd_v3(event_type="incident.triggered",
                             incident_id="P-SEV", priority="P4"))
        n_calls = len(client.calls)
        code, _ = self._pd_post(_pd_v3(event_type="incident.priority_updated",
                                       incident_id="P-SEV", priority="P2"))
        self.assertEqual(code, 202)
        ep = self.store.episodes["pd:inc/P-SEV"]
        self.assertEqual(ep.final_severity, "SEV2")
        self.assertEqual(len(ep.severity_history), 1)
        # The gate re-ran on the severity change (the F1 input contract).
        self.assertEqual(len(client.calls), n_calls + 1)
        obs = [o for o in self.store.observations
               if o.event_type == "incident.priority_updated"]
        self.assertEqual(len(obs), 1)
        self.assertIn(obs[0].gate_would, {"page_now", "page_business_hours",
                                          "suppress", "passthrough"})

    def test_unknown_event_type_recorded_not_evaluated(self):
        code, _ = self._pd_post(_pd_v3(event_type="incident.test_webhook",
                                       incident_id="P-TEST"))
        self.assertEqual(code, 202)
        obs = self.store.observations
        self.assertEqual(obs[-1].gate_would, "not_evaluated")
        self.assertEqual(self.store.tap_health()["unknown_event_type"], 1)

    def test_duplicate_delivery_collapses(self):
        payload = _pd_v3(incident_id="P-DUP", event_id="ev-dup-1")
        code, resp = self._pd_post(payload)
        self.assertEqual(code, 202)
        code, resp = self._pd_post(payload)  # PD retries the delivery
        self.assertEqual(code, 202)
        self.assertEqual(resp["duplicates"], 1)
        self.assertEqual(self.store.tap_health()["duplicate_collapsed"], 1)
        obs = [o for o in self.store.observations
               if o.event_id == "ev-dup-1"]
        self.assertEqual(len(obs), 1)  # one decision event, not two

    def test_subscription_coverage_check(self):
        seen = {"incident.triggered", "incident.acknowledged",
                "incident.resolved"}
        check = subscription_coverage_check(seen)
        self.assertFalse(check["parity_ok"])
        self.assertEqual(check["missing"],
                         ["incident.escalated", "incident.priority_updated"])
        full = subscription_coverage_check(REQUIRED_PD_EVENTS)
        self.assertTrue(full["parity_ok"])


# ---------------------------------------------------------------- opsgenie / am

class TestOpsgenie(ShadowTestBase):
    def setUp(self):
        self._start(FixedClient(canned(p1=0.9, conf=0.95)),
                    og_token="og-secret")

    def _og_post(self, payload, token="og-secret"):
        body = json.dumps(payload).encode()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self._post("/shadow/opsgenie", body, headers)

    def test_create_ack_close_lifecycle(self):
        for action in ("Create", "Acknowledge", "Close"):
            code, _ = self._og_post(_og(action=action))
            self.assertEqual(code, 202, action)
        ep = self.store.episodes["og:alert/og-1"]
        self.assertTrue(ep.human_paged)
        self.assertTrue(ep.human_acknowledged)
        self.assertTrue(ep.human_resolved)

    def test_bad_bearer_token_rejected(self):
        code, _ = self._og_post(_og(), token="wrong")
        self.assertEqual(code, 401)

    def test_custom_priority_action_reruns_gate(self):
        client = self.pipeline.shadow_pipeline.gate.client
        self._og_post(_og(action="Create"))
        n = len(client.calls)
        # A priority change alters the fingerprint, so the gate re-runs
        # (same-fingerprint repeats are correlator duplicates by design).
        code, _ = self._og_post(_og(action="Escalate_Priority_to_P1",
                                    priority="P1"))
        self.assertEqual(code, 202)
        self.assertEqual(len(client.calls), n + 1)
        ep = self.store.episodes["og:alert/og-1"]
        self.assertEqual(ep.final_severity, "SEV1")

    def test_payload_completeness_line(self):
        self._og_post(_og(with_details=True))
        self._og_post(_og(alert_id="og-2", with_details=False))
        health = self.store.tap_health()
        self.assertEqual(health["payload_total"], 2)
        self.assertAlmostEqual(health["payload_completeness_pct"], 50.0)


class TestAlertmanager(ShadowTestBase):
    def setUp(self):
        self._start(FixedClient(canned(p1=0.9, conf=0.95)))

    def _am_post(self, payload, token="am-token"):
        body = json.dumps(payload).encode()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self._post("/shadow/alertmanager", body, headers)

    def test_firing_batch_ingested(self):
        code, resp = self._am_post(_am())
        self.assertEqual(code, 202)
        self.assertEqual(resp["events"], 1)
        ep = self.store.episodes["am:fp/fp-1"]
        self.assertTrue(ep.human_paged)
        self.assertEqual(ep.final_severity, "SEV3")  # warning -> SEV3

    def test_resolved_closes_episode(self):
        self._am_post(_am())
        code, _ = self._am_post(_am(status="resolved"))
        self.assertEqual(code, 202)
        ep = self.store.episodes["am:fp/fp-1"]
        self.assertTrue(ep.human_resolved)

    def test_missing_bearer_token_rejected(self):
        code, _ = self._am_post(_am(), token=None)
        self.assertEqual(code, 401)
        self.assertEqual(self.store.tap_health()["dropped_by_reason"]
                         ["unauthenticated"], 1)

    def test_wrong_bearer_token_rejected(self):
        code, _ = self._am_post(_am(), token="wrong")
        self.assertEqual(code, 401)

    def test_batch_idempotency_per_alert(self):
        payload = _am()
        self._am_post(payload)
        code, resp = self._am_post(payload)  # AM retries the batch
        self.assertEqual(code, 202)
        self.assertEqual(resp["duplicates"], 1)


# ---------------------------------------------------------------- read-only proof

class TestReadOnlyProof(unittest.TestCase):
    """The tap MUST be provably read-only: shadow ingestion may never
    reach the write path."""

    def _build(self, jev_client, allowlist=None):
        write_path = RefusingForwarder()
        audit = AuditLog(":memory:")
        # D1: the shadow would-be verdict runs the live kernel, so the shadow
        # gate sees the same freshness evidence production gives the live gate
        # (receiver boots the monitor from SENTINEL_FRESHNESS_BUNDLE).
        gate = Gate(jev_client, Thresholds(), _attested_entries(allowlist), audit,
                    shadow=True,
                    freshness_monitor=fresh_monitor_for(list(allowlist or [])))
        pipeline = Pipeline(Correlator(), gate, write_path, audit,
                            ReceiverConfig())
        config = ShadowConfig(enabled=True, pd_secret=PD_SECRET,
                              alertmanager_token="am-token")
        config.validate()
        pipeline.shadow_pipeline = ShadowPipeline(
            gate=gate, correlator=Correlator(storm_fingerprints=10 ** 9),
            store=ShadowStore(),
            config=config, allowlist=set(allowlist or []))
        server = make_server(0, pipeline)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        self.addCleanup(lambda: (server.shutdown(), server.server_close(),
                                 thread.join(timeout=5)))
        return pipeline, base, write_path

    def _post(self, base, path, body, headers):
        req = urllib.request.Request(base + path, data=body, method="POST",
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.getcode(), json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_100_incidents_zero_write_path_invocations(self):
        # Half would-suppress (allowlisted, p1=0.0), half would-page
        # (p1=0.9): NEITHER may touch the write path.
        by_title = {}
        allowlist = set()
        services = {}
        for i in range(100):
            title = f"incident-{i:03d}"
            service = f"svc-{i:03d}"
            services[i] = service
            if i % 2 == 0:
                by_title[title] = canned(p1=0.0, conf=0.95,
                                        q3_choice="suppress",
                                        q1_choice="p4_low")
                fp = fingerprint_for(service, "pagerduty.incident",
                                     "P4", "")
                allowlist.add(fp)
            else:
                by_title[title] = canned(p1=0.9, conf=0.95)
        pipeline, base, write_path = self._build(
            ScriptedClient(by_title, default=canned(p1=0.9, conf=0.95)),
            allowlist=allowlist)
        for i in range(100):
            payload = _pd_v3(incident_id=f"P-PROOF-{i:03d}",
                             title=f"incident-{i:03d}", priority="P4",
                             service=services[i])
            body = json.dumps(payload).encode()
            code, resp = self._post(base, "/shadow/pagerduty", body,
                                    _pd_sig(body))
            self.assertEqual(code, 202, i)
            self.assertEqual(resp["events"], 1, i)
        store = pipeline.shadow_pipeline.store
        self.assertEqual(len(store.observations), 100)
        self.assertEqual(len(store.episodes), 100)
        # THE PROOF: zero write-path invocations across 100 ingestions,
        # including 50 would-suppress verdicts.
        self.assertEqual(write_path.calls, [])
        would_suppress = [o for o in store.observations
                          if o.gate_would == "suppress"]
        self.assertEqual(len(would_suppress), 50)
        # ...and every one of them was RECORDED as a shadow observation.
        self.assertTrue(all(o.gate_reason == "shadow" for o in would_suppress))

    def test_shadow_exception_never_falls_through_to_page(self):
        pipeline, base, write_path = self._build(
            FixedClient(canned(p1=0.9, conf=0.95)))
        sp = pipeline.shadow_pipeline
        orig = sp.handle

        def boom(*a, **k):
            raise RuntimeError("simulated tap bug")
        sp.handle = boom
        try:
            payload = _pd_v3(incident_id="P-BOOM")
            body = json.dumps(payload).encode()
            code, _ = self._post(base, "/shadow/pagerduty", body,
                                 _pd_sig(body))
        finally:
            sp.handle = orig
        # 500 (vendor retries; ingest is idempotent) — and crucially the
        # paging fail-open last resort did NOT run.
        self.assertEqual(code, 500)
        self.assertEqual(write_path.calls, [])

    def test_static_import_graph_guard(self):
        """shadow.py / shadow_report.py / backtest.py must not import or
        name the write path. "We forgot to call it" is weak; the code to
        call it must not exist in these modules (design 06 §a.4)."""
        forbidden = ("forwarder", "Forward(", ".forward(", "routing_key",
                     "events.pagerduty", "api.pagerduty", "api.opsgenie")
        src_dir = os.path.join(_SRC, "sentinel")
        for name in ("shadow.py", "shadow_report.py", "backtest.py"):
            with open(os.path.join(src_dir, name), encoding="utf-8") as fh:
                text = fh.read().lower()
            for token in forbidden:
                self.assertNotIn(token, text,
                                 f"{name} names the write path: {token!r}")

    def test_tap_disabled_is_404(self):
        write_path = RefusingForwarder()
        audit = AuditLog(":memory:")
        pipeline = Pipeline(Correlator(),
                            Gate(FixedClient(), Thresholds(), set(), audit),
                            write_path, audit, ReceiverConfig())
        # shadow_pipeline stays None: tap disabled.
        server = make_server(0, pipeline)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(),
                                 thread.join(timeout=5)))
        base = f"http://127.0.0.1:{server.server_address[1]}"
        payload = _pd_v3()
        body = json.dumps(payload).encode()
        code, _ = self._post(base, "/shadow/pagerduty", body, _pd_sig(body))
        self.assertEqual(code, 404)
        self.assertEqual(write_path.calls, [])


# ---------------------------------------------------------------- boot refusal

class TestBootRefusal(unittest.TestCase):
    def test_write_credentials_refuse_to_boot(self):
        cfg = ShadowConfig(enabled=True, pd_secret=PD_SECRET,
                           write_credentials=["PD_ROUTING_KEY"])
        with self.assertRaises(SystemExit):
            cfg.validate()

    def test_empty_write_credentials_boot(self):
        cfg = ShadowConfig(enabled=True, pd_secret=PD_SECRET,
                           write_credentials=[])
        cfg.validate()  # must not raise


# ---------------------------------------------------------------- policy mirror

class TestPolicyMirror(unittest.TestCase):
    """threshold_counterfactual's policy must agree with the real gate."""

    def test_counterfactual_matches_gate_at_live_threshold(self):
        cases = [
            # (p1, conf, q3_choice, allowlisted) -> expected live action
            (dict(p1_critical=0.0, p2_high=0.0, p3_medium=0.9, p4_low=0.1),
             0.95, "suppress", True, "suppress"),
            (dict(p1_critical=0.9, p2_high=0.0, p3_medium=0.1, p4_low=0.0),
             0.95, "page_now", True, "page_now"),
            (dict(p1_critical=0.0, p2_high=0.0, p3_medium=0.9, p4_low=0.1),
             0.40, "page_business_hours", True, "page_now"),  # uncertainty
            (dict(p1_critical=0.0, p2_high=0.0, p3_medium=0.9, p4_low=0.1),
             0.95, "cannot_determine", True, "passthrough"),
            (dict(p1_critical=0.0, p2_high=0.0, p3_medium=0.9, p4_low=0.1),
             0.95, "suppress", False, "page_business_hours"),  # no allowlist
        ]
        t = Thresholds()
        for probs, conf, choice, allowlisted, expected in cases:
            got = policy_action(probs["p1_critical"], probs["p2_high"],
                                probs["p3_medium"], probs["p4_low"],
                                conf, choice, allowlisted, t)
            self.assertEqual(got, expected, (probs, conf, choice))

    def test_counterfactual_presets_shape(self):
        t = Thresholds()
        cf = threshold_counterfactual(
            {"p1_critical": 0.0, "p2_high": 0.0,
             "p3_medium": 0.9, "p4_low": 0.1},
            0.95, "suppress", True, t, (0.85, 0.90, 0.95, 0.99))
        self.assertEqual(set(cf), {"conf>=0.85", "conf>=0.90",
                                   "conf>=0.95", "conf>=0.99"})
        self.assertEqual(cf["conf>=0.85"], "suppress")
        # At 0.99 the confidence lock fails -> falls through to business-hours.
        self.assertEqual(cf["conf>=0.99"], "page_business_hours")


# ---------------------------------------------------------------- event sink

class StrictFakeSink:
    """Strict duck-type of EventLog.append_event (eventlog.py, event-log
    lane). Keyword-only kwargs after event_type, exactly like the real
    one — a wrong call raises TypeError HERE instead of being swallowed
    by the tap's except-clause. Regression guard for the PR #18 review
    blocker (every shadow_decision silently dropped).

    If EventLog.append_event's signature changes, update this fake to
    match (the strict test below fails loudly until you do).
    """

    def __init__(self):
        self.calls = []

    def append_event(self, event_type, *, actor, alert_id, fingerprint,
                     episode_id, outbox_id=None, body, ts=None):
        call = {"event_type": event_type, "actor": actor,
                "alert_id": alert_id, "fingerprint": fingerprint,
                "episode_id": episode_id, "outbox_id": outbox_id,
                "body": body, "ts": ts}
        self.calls.append(call)
        return len(self.calls)


class ExplodingSink:
    """The sink must never sink the tap."""

    def append_event(self, *args, **kwargs):
        raise RuntimeError("disk is on fire")


class TestEventSink(unittest.TestCase):
    def _pipeline(self, sink):
        audit = AuditLog(":memory:")
        gate = Gate(FixedClient(canned(p1=0.9, conf=0.95)), Thresholds(),
                    set(), audit, shadow=True)
        config = ShadowConfig(enabled=True, pd_secret=PD_SECRET)
        config.validate()
        return ShadowPipeline(gate=gate, correlator=Correlator(),
                              store=ShadowStore(), config=config,
                              event_sink=sink)

    def _pd_handle(self, pipeline, payload, secret=PD_SECRET):
        body = json.dumps(payload).encode()
        headers = dict(_pd_sig(body, secret))
        headers["X-Webhook-Subscription"] = "sub-1"
        return pipeline.handle("pagerduty", body, headers)

    def test_shadow_decision_written_with_exact_contract(self):
        sink = StrictFakeSink()
        pipeline = self._pipeline(sink)
        code, _ = self._pd_handle(
            pipeline, _pd_v3(event_type="incident.triggered",
                             incident_id="P-SINK", priority="P4"))
        self.assertEqual(code, 202)
        self.assertEqual(len(sink.calls), 1)
        call = sink.calls[0]
        self.assertEqual(call["event_type"], "shadow_decision")
        self.assertEqual(call["actor"], "engine")
        self.assertEqual(call["alert_id"], "pagerduty:P-SINK")
        self.assertEqual(call["episode_id"], "pd:inc/P-SINK")
        # Exactly the append_event vocabulary — no bogus kwargs
        # (episode_key / vendor / event_type / links were the PR #18
        # blocker) and nothing missing.
        self.assertEqual(set(call), {"event_type", "actor", "alert_id",
                                     "fingerprint", "episode_id",
                                     "outbox_id", "body", "ts"})
        self.assertIsNone(call["outbox_id"])
        self.assertIsNone(call["ts"])
        # Fingerprint is the one the gate actually evaluated.
        self.assertEqual(call["fingerprint"],
                         fingerprint_for("payments-api", "pagerduty.incident",
                                         "P4", ""))
        # Body carries the shadow_decision vocabulary: "links" is REQUIRED
        # by the event-log schema.
        body = call["body"]
        self.assertEqual(set(body), {"would", "confidence",
                                     "threshold_counterfactual", "links"})
        obs = pipeline.store.observations[-1]
        self.assertEqual(body["would"], obs.gate_would)
        self.assertEqual(body["confidence"], obs.gate_confidence)
        self.assertEqual(body["links"]["incident_url"],
                         "https://example.pagerduty.com/incidents/P-SINK")
        self.assertEqual(body["links"]["observation_id"],
                         obs.observation_id)

    def test_sink_is_optional(self):
        # No sink wired: ingest still records the observation.
        pipeline = self._pipeline(None)
        code, _ = self._pd_handle(
            pipeline, _pd_v3(event_type="incident.triggered",
                             incident_id="P-NOSINK"))
        self.assertEqual(code, 202)
        self.assertEqual(len(pipeline.store.observations), 1)

    def test_exploding_sink_never_sinks_the_tap(self):
        pipeline = self._pipeline(ExplodingSink())
        code, resp = self._pd_handle(
            pipeline, _pd_v3(event_type="incident.triggered",
                             incident_id="P-BOOM"))
        self.assertEqual(code, 202)
        self.assertEqual(resp["events"], 1)
        self.assertEqual(len(pipeline.store.observations), 1)

    def test_non_evaluated_events_emit_nothing(self):
        sink = StrictFakeSink()
        pipeline = self._pipeline(sink)
        code, _ = self._pd_handle(
            pipeline, _pd_v3(event_type="incident.test_webhook",
                             incident_id="P-T"))
        self.assertEqual(code, 202)
        self.assertEqual(sink.calls, [])  # not_evaluated -> no decision


# ---------------------------------------------------------------- OG fail-closed

class TestOpsgenieFailClosed(ShadowTestBase):
    def setUp(self):
        # No OG token configured — the OG route must refuse every
        # delivery (401), never silently accept them unauthenticated
        # (coordinator's auth decision on the PR #18 review; consistent
        # with PD and AM, which already fail closed).
        self._start(FixedClient(canned(p1=0.9, conf=0.95)))  # og_token=None

    def test_missing_token_401s_og_deliveries(self):
        body = json.dumps(_og()).encode()
        code, resp = self._post("/shadow/opsgenie", body)
        self.assertEqual(code, 401)
        self.assertIn("no opsgenie token configured", resp["message"])
        health = self.store.tap_health()
        self.assertEqual(health["dropped"], 1)
        self.assertEqual(health["dropped_by_reason"]["unauthenticated"], 1)
        self.assertEqual(self.store.episodes, {})

    def test_missing_token_refuses_even_a_plausible_bearer(self):
        body = json.dumps(_og()).encode()
        code, _ = self._post("/shadow/opsgenie", body,
                             {"Authorization": "Bearer og-secret"})
        self.assertEqual(code, 401)
        self.assertEqual(self.store.episodes, {})


if __name__ == "__main__":
    unittest.main()
