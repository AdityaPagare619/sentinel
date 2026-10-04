"""Tests for sentinel.gate (§3.6 + §4 policy table).

Includes the kill-the-client release-blocker test: a client raising
JevOverloaded/JevTimeout on EVERY call must still yield passthrough for every
alert, and the forwarder must relay the original payload byte-identical.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import json
import unittest

from sentinel.audit import AuditLog
from sentinel.client import (Answer, DecisionResponse, JevError,
                             JevOverloaded, JevTimeout, MockSystemOneClient)
from sentinel.correlator import CorrelationResult
from sentinel.forwarder import Forwarder
from sentinel.gate import Gate
from sentinel.models import Thresholds
from sentinel.quantized import (AllowlistEntry, Attestation, FitStore,
                                REFERENCE_CLASS, wilson_upper_onesided)
from sentinel.state import build_state, input_sha256

from tests.helpers import CaptureServer, make_alert

from datetime import datetime, timedelta, timezone


def _answer(qid, choice, probs, conf):
    return Answer(qid=qid, qtype="choice", choice=choice, noul=None,
                  probabilities=dict(probs), confidence=conf)


def canned(p1=0.0, p2=0.0, p3=0.9, p4=0.1, conf=0.95,
           q1_choice="p3_medium", q3_choice="page_business_hours",
           team="platform", model="jev-1.13.0"):
    return DecisionResponse(
        model=model,
        answers={
            "severity": _answer("severity", q1_choice,
                                {"p1_critical": p1, "p2_high": p2,
                                 "p3_medium": p3, "p4_low": p4,
                                 "known_noise": 0.0, "cannot_determine": 0.0},
                                conf),
            "owning_team": _answer("owning_team", team, {team: 1.0}, 0.99),
            "disposition": _answer("disposition", q3_choice,
                                   {q3_choice: 1.0}, conf),
        },
        input_tokens=100,
    )


class ExplodingClient:
    """Raises on every call — simulates a dead Jev backend."""

    def __init__(self, exc_factory):
        self.exc_factory = exc_factory
        self.calls = 0

    def decide(self, state, questions):
        self.calls += 1
        raise self.exc_factory("jev is down")


class GateTestBase(unittest.TestCase):
    def make_gate(self, client, allowlist=None, shadow=False,
                  freshness_monitor=None):
        self.audit = AuditLog(":memory:")
        self.gate = Gate(client, Thresholds(),
                         allowlist or [], self.audit, shadow=shadow,
                         freshness_monitor=freshness_monitor)
        return self.gate

    def scripted_gate(self, alert, response, **kw):
        state = build_state(alert, {}, {})
        client = MockSystemOneClient({input_sha256(state): response})
        gate = self.make_gate(client, **kw)
        return gate, client, state


def fresh_monitor_for(fingerprints, **bundle_kw):
    """Boot a V1-validated all-fresh bundle covering ``fingerprints``.

    D1: suppress requires a FreshnessReport — tests that expect the
    suppress path must wire one (the production receiver boots it from
    SENTINEL_FRESHNESS_BUNDLE).
    """
    import tempfile
    from sentinel.correlator import fingerprint_for
    from sentinel.freshness import FreshnessMonitor, FreshnessValidator
    from tests.freshness_fixtures import (
        LABEL_PIPELINE, FixtureClock, build_bundle, make_allowlist_entry,
    )
    tmp = tempfile.mkdtemp(prefix="sentinel-fresh-")
    build_bundle(
        tmp,
        entries=[(fp, "prod", make_allowlist_entry(fp))
                 for fp in fingerprints],
        **bundle_kw)
    validator = FreshnessValidator(
        clock=FixtureClock(),
        deployed_label_pipeline_version=LABEL_PIPELINE)
    mon = FreshnessMonitor(validator, tmp)
    mon.boot()
    assert mon.current_report().stale_locks() == [], \
        "test bundle must validate all-fresh"
    return mon


class TestSuppressTripleLock(GateTestBase):
    # ADR-013: the probability leg is the quantized lock — reported P(p1)
    # must be exactly 0.00 AND (a valid fit with p_hat_upper < 0.002 OR dual
    # human attestation on the allowlist entry). A bare fingerprint in the
    # allowlist no longer suppresses (that was the M-1 units error).

    def _attested(self, fingerprint, author="carol"):
        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        return AllowlistEntry(
            fingerprint=fingerprint, author=author,
            attestations=[
                Attestation("alice", now - timedelta(days=1),
                            "lrq-9f2c-41ab", 30),
                Attestation("bob", now - timedelta(days=1),
                            "lrq-9f2c-41ab", 30),
            ])

    def test_suppress_when_triple_lock_holds(self):
        # New contract: suppress via the dual-attestation interim path.
        alert = make_alert()
        gate, client, state = self.scripted_gate(
            alert, canned(p1=0.0, conf=0.95),
            allowlist=[self._attested(alert.fingerprint)],
            freshness_monitor=fresh_monitor_for([alert.fingerprint]))
        disp, rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        self.assertEqual(disp.reason, "allowlist")
        self.assertEqual(len(client.calls), 1)

    def test_suppress_via_valid_fit(self):
        # New contract: suppress via the fit path (k=0, n=1351 clears).
        from sentinel.quantized import FitArtifact, WILSON_Z
        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        art = FitArtifact(
            fit_id="", org="org-c", reference_class=REFERENCE_CLASS,
            window_start="2026-07-05T00:00:00+00:00",
            window_end="2026-10-02T00:00:00+00:00",
            n=1351, k=0, m=0,
            p_hat_upper=wilson_upper_onesided(0, 1351), z=WILSON_Z,
            model_pin="jev-1.13.0", gate_formula_version="adr013-v1",
            computed_at="2026-10-02T00:00:00+00:00",
            valid_until="2026-11-01T00:00:00+00:00",
            shift_status="OK", tuner_version="test").bind()
        store = FitStore()
        store.put(art)
        alert = make_alert()
        state = build_state(alert, {}, {})
        client = MockSystemOneClient(
            {input_sha256(state): canned(p1=0.0, conf=0.95)})
        audit = AuditLog(":memory:")
        gate = Gate(client, Thresholds(), {alert.fingerprint}, audit,
                    fit_store=store, pinned_model="jev-1.13.0", org="org-c",
                    clock=lambda: now,
                    freshness_monitor=fresh_monitor_for(
                        [alert.fingerprint]))
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")

    def test_suppress_denied_without_fit_or_attestation(self):
        # M-1: reported 0.00 with a bare allowlist entry must NOT suppress.
        alert = make_alert()
        gate, _c, state = self.scripted_gate(alert, canned(p1=0.0, conf=0.95),
                                             allowlist={alert.fingerprint})
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")

    def test_suppress_denied_without_allowlist(self):
        alert = make_alert()
        gate, _c, state = self.scripted_gate(alert, canned(p1=0.0, conf=0.95),
                                             allowlist=set())
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")

    def test_suppress_denied_when_p1_too_high(self):
        alert = make_alert()
        gate, _c, state = self.scripted_gate(
            alert, canned(p1=0.05, p2=0.0, p3=0.9, p4=0.05, conf=0.95),
            allowlist={alert.fingerprint})
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")

    def test_suppress_denied_when_confidence_low(self):
        alert = make_alert()
        gate, _c, state = self.scripted_gate(
            alert, canned(p1=0.0, conf=0.85), allowlist={alert.fingerprint})
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")


class TestPageNow(GateTestBase):
    def test_page_now_on_high_p1p2(self):
        alert = make_alert()
        gate, _c, state = self.scripted_gate(
            alert, canned(p1=0.2, p2=0.2, p3=0.5, p4=0.1, conf=0.9))
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "page_now")
        self.assertEqual(disp.reason, "threshold")

    def test_page_now_on_low_confidence(self):
        # Uncertainty pages: conf < 0.50 -> page_now even for p3-looking probs.
        alert = make_alert()
        gate, _c, state = self.scripted_gate(
            alert, canned(p1=0.0, p2=0.0, p3=0.9, p4=0.1, conf=0.40))
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "page_now")

    def test_page_now_on_none_confidence(self):
        resp = canned(p1=0.0, p2=0.0, p3=0.9, p4=0.1, conf=None)
        alert = make_alert()
        gate, _c, state = self.scripted_gate(alert, resp)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "page_now")


class TestBusinessHours(GateTestBase):
    def test_page_business_hours_middle_band(self):
        alert = make_alert()
        gate, _c, state = self.scripted_gate(
            alert, canned(p1=0.0, p2=0.0, p3=0.7, p4=0.3, conf=0.80))
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "page_business_hours")
        self.assertEqual(disp.reason, "threshold")

    def test_passthrough_when_conf_below_queue_bar(self):
        # conf 0.60: not uncertain enough to page, not confident enough to queue.
        alert = make_alert()
        gate, _c, state = self.scripted_gate(
            alert, canned(p1=0.0, p2=0.0, p3=0.7, p4=0.3, conf=0.60))
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "passthrough")
        self.assertEqual(disp.reason, "uncertain")


class TestPassthrough(GateTestBase):
    def test_cannot_determine_passes_through(self):
        alert = make_alert()
        gate, _c, state = self.scripted_gate(
            alert, canned(p1=0.0, p2=0.0, p3=0.0, p4=0.0, conf=0.99,
                          q1_choice="cannot_determine",
                          q3_choice="cannot_determine"))
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "passthrough")
        self.assertEqual(disp.reason, "uncertain")

    def test_team_propagates(self):
        alert = make_alert()
        gate, _c, state = self.scripted_gate(
            alert, canned(p1=0.5, p2=0.3, conf=0.9, team="data"))
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.team, "data")


class TestFailOpen(unittest.TestCase):
    def setUp(self):
        self.audit = AuditLog(":memory:")

    def _eval_with_exploding(self, exc_factory):
        gate = Gate(ExplodingClient(exc_factory), Thresholds(), set(), self.audit)
        alert = make_alert()
        state = build_state(alert, {}, {})
        return gate.evaluate(alert, state, {}, {})

    def test_jev_timeout_fails_open(self):
        disp, _rec = self._eval_with_exploding(JevTimeout)
        self.assertEqual(disp.action, "passthrough")
        self.assertTrue(disp.reason.startswith("error:"))
        self.assertIn("timeout", disp.reason)

    def test_jev_overloaded_fails_open(self):
        disp, _rec = self._eval_with_exploding(JevOverloaded)
        self.assertEqual(disp.action, "passthrough")
        self.assertTrue(disp.reason.startswith("error:"))

    def test_unexpected_exception_fails_open(self):
        disp, _rec = self._eval_with_exploding(RuntimeError)
        self.assertEqual(disp.action, "passthrough")
        self.assertTrue(disp.reason.startswith("error:"))

    def test_audit_row_written_on_error(self):
        disp, _rec = self._eval_with_exploding(JevTimeout)
        rows = self.audit.decisions_for_fingerprint(make_alert().fingerprint)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "passthrough")
        self.assertTrue(rows[0]["reason"].startswith("error:"))
        # error rows carry no Jev answers
        self.assertIsNone(rows[0]["q1_severity"])
        self.assertIsNone(rows[0]["jev_model"])

    def test_gate_never_raises(self):
        gate = Gate(ExplodingClient(JevOverloaded), Thresholds(), set(), self.audit)
        for i in range(5):
            alert = make_alert(alert_id=f"e{i}")
            state = build_state(alert, {}, {})
            disp, _rec = gate.evaluate(alert, state, {}, {})  # must not raise
            self.assertEqual(disp.action, "passthrough")


class TestKillTheClientReleaseBlocker(unittest.TestCase):
    """RELEASE BLOCKER: Jev dead on every call -> every alert passes through
    and the forwarder relays the ORIGINAL payload byte-identical."""

    def _run(self, exc_factory):
        capture = CaptureServer()
        self.addCleanup(capture.close)
        audit = AuditLog(":memory:")
        gate = Gate(ExplodingClient(exc_factory), Thresholds(), set(), audit)
        forwarder = Forwarder(pd_events_url=capture.url)
        alerts = []
        for i in range(3):
            raw = {"routing_key": "rk-secret-123", "event_action": "trigger",
                   "dedup_key": f"dk-{i}",
                   "payload": {"summary": f"outage {i}", "source": f"svc{i}",
                               "severity": "critical"}}
            raw_bytes = json.dumps(raw).encode()
            alert = make_alert(service=f"svc{i}", alert_id=f"dk-{i}",
                               title=f"outage {i}", raw=raw)
            state = build_state(alert, {}, {})
            disp, _rec = gate.evaluate(alert, state, {}, {})
            self.assertEqual(disp.action, "passthrough",
                             f"alert {i} did not fail open")
            result = forwarder.forward(alert, disp, raw_bytes=raw_bytes)
            self.assertTrue(result.forwarded, f"alert {i} was not forwarded")
            alerts.append((raw_bytes, alert))
        return capture, alerts

    def test_kill_client_jev_overloaded(self):
        capture, alerts = self._run(JevOverloaded)
        self.assertEqual(len(capture.requests), 3)
        for (raw_bytes, _alert), req in zip(alerts, capture.requests):
            self.assertEqual(req["body"], raw_bytes,
                             "forwarder altered the original payload")

    def test_kill_client_jev_timeout(self):
        capture, alerts = self._run(JevTimeout)
        self.assertEqual(len(capture.requests), 3)
        for (raw_bytes, _alert), req in zip(alerts, capture.requests):
            self.assertEqual(req["body"], raw_bytes,
                             "forwarder altered the original payload")


class TestDeterministicPaths(GateTestBase):
    def test_duplicate_inherits_without_jev_call(self):
        alert = make_alert()
        client = MockSystemOneClient({})
        gate = self.make_gate(client)
        prior_disp, _ = gate.evaluate(alert, build_state(alert, {}, {}), {}, {})
        corr = CorrelationResult(kind="duplicate", fingerprint=alert.fingerprint,
                                 prior=prior_disp)
        disp, _rec = gate.evaluate(alert, build_state(alert, {}, {}), {}, {},
                                   correlation=corr)
        self.assertEqual(disp.action, prior_disp.action)
        self.assertEqual(disp.reason, "dedup")
        self.assertEqual(len(client.calls), 1)  # only the first evaluate called Jev

    def test_change_window_pages_business_hours_without_jev(self):
        alert = make_alert()
        client = MockSystemOneClient({})
        gate = self.make_gate(client)
        corr = CorrelationResult(kind="change_window", fingerprint=alert.fingerprint)
        disp, _rec = gate.evaluate(alert, build_state(alert, {}, {}), {}, {},
                                   correlation=corr)
        self.assertEqual(disp.action, "page_business_hours")
        self.assertEqual(disp.reason, "change_window")
        self.assertEqual(len(client.calls), 0)

    def test_storm_continuation_folds_without_jev(self):
        # D3: storm-continuation is FOLDED into the aggregate page — not
        # suppressed. action="suppress" here used to read as model-driven
        # suppression to a 3 AM operator.
        alert = make_alert()
        client = MockSystemOneClient({})
        gate = self.make_gate(client)
        corr = CorrelationResult(kind="storm", fingerprint=alert.fingerprint,
                                 storm_declared=False)
        disp, _rec = gate.evaluate(alert, build_state(alert, {}, {}), {}, {},
                                   correlation=corr)
        self.assertEqual(disp.action, "folded")
        self.assertEqual(disp.reason, "storm")
        self.assertEqual(len(client.calls), 0)


class TestShadowMode(GateTestBase):
    def test_shadow_always_returns_passthrough(self):
        alert = make_alert()
        gate, _c, state = self.scripted_gate(
            alert, canned(p1=0.9, p2=0.0, p3=0.1, p4=0.0, conf=0.95),
            shadow=True)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "passthrough")
        self.assertEqual(disp.reason, "shadow")

    def test_shadow_audit_logs_would_be_disposition(self):
        alert = make_alert()
        gate, _c, state = self.scripted_gate(
            alert, canned(p1=0.9, p2=0.0, p3=0.1, p4=0.0, conf=0.95),
            shadow=True)
        _disp, _rec = gate.evaluate(alert, state, {}, {})
        rows = self.audit.decisions_for_fingerprint(alert.fingerprint)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "page_now")  # would-be
        self.assertEqual(rows[0]["reason"], "shadow")


class TestAuditAlwaysWritten(GateTestBase):
    def test_row_written_for_every_policy_outcome(self):
        from sentinel.correlator import fingerprint_for
        cases = [
            canned(p1=0.0, conf=0.95),                       # suppress (allowlisted)
            canned(p1=0.5, conf=0.9),                        # page_now
            canned(p1=0.0, p3=0.7, p4=0.3, conf=0.8),        # business hours
            canned(p1=0.0, p3=0.7, p4=0.3, conf=0.6),        # passthrough
        ]
        # D1: the suppress case needs a fresh report; the other cases page
        # regardless of freshness.
        fps = [fingerprint_for(f"svc{i}", "http_5xx", "critical", "us-east",
                               env="", cluster="")
               for i in range(len(cases))]
        monitor = fresh_monitor_for(fps)
        for i, resp in enumerate(cases):
            alert = make_alert(alert_id=f"row{i}", service=f"svc{i}")
            gate, _c, state = self.scripted_gate(
                alert, resp, allowlist={alert.fingerprint},
                freshness_monitor=monitor)
            _disp, _rec = gate.evaluate(alert, state, {}, {})
            rows = self.audit.decisions_for_fingerprint(alert.fingerprint)
            self.assertEqual(len(rows), 1, f"no audit row for case {i}")
            self.assertIsNotNone(rows[0]["input_sha256"])
            self.assertEqual(rows[0]["jev_model"], "jev-1.13.0")


if __name__ == "__main__":
    unittest.main()
