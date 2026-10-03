"""Tests for sentinel.receiver (§3.9).

All tests hit a real loopback ThreadingHTTPServer in-process. A second
loopback CaptureServer stands in for the PagerDuty Events API.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import hashlib
import hmac
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from sentinel.audit import AuditLog
from sentinel.config import ConfigLoader, record_restart
from sentinel.correlator import Correlator, fingerprint_for
from sentinel.forwarder import Forwarder
from sentinel.gate import Gate
from sentinel.models import Thresholds
from sentinel.quantized import AllowlistEntry, Attestation
from sentinel.receiver import Pipeline, ReceiverConfig, make_server
from datetime import datetime, timedelta, timezone
from tests.test_gate import fresh_monitor_for

from tests.helpers import CaptureServer
from tests.test_gate import ExplodingClient, canned
from sentinel.client import JevOverloaded


class FixedClient:
    """Deterministic stand-in: returns one canned response for every call."""

    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def decide(self, state, questions):
        self.calls.append({"state": state, "questions": questions})
        if self.exc is not None:
            raise self.exc("boom")
        return self.response


def _pd_event(summary="disk full", dedup_key="dk-test", severity="critical",
              component="web", klass="http_5xx", region="us-east"):
    return {"routing_key": "rk-abc", "event_action": "trigger",
            "dedup_key": dedup_key,
            "payload": {"summary": summary, "source": "web-1",
                        "severity": severity, "component": component,
                        "class": klass, "region": region}}


class ReceiverTestBase(unittest.TestCase):
    def _start(self, jev_client, allowlist=None, webhook_secret=None,
               shadow=False, webhook_onboarding=False):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        cfgdir = os.path.join(self._tmp.name, "cfg")
        os.makedirs(cfgdir, exist_ok=True)
        with open(os.path.join(cfgdir, "thresholds.json"), "w") as fh:
            json.dump({}, fh)
        # ADR-013: AllowlistEntry objects (with attestations) can't go through
        # JSON config; write plain fingerprints to file, pass entries directly.
        entries = [a for a in (allowlist or []) if not isinstance(a, str)]
        fp_strings = [a for a in (allowlist or []) if isinstance(a, str)]
        with open(os.path.join(cfgdir, "allowlist.json"), "w") as fh:
            json.dump(sorted(fp_strings), fh)
        statedir = os.path.join(self._tmp.name, "state")
        loader = ConfigLoader(config_dir=cfgdir, state_dir=statedir)
        policy = loader.load_startup()
        # Override with attested entries if provided (bypasses file format).
        allowlist_for_gate = entries if entries else policy.allowlist
        record_restart(statedir)
        self.pd = CaptureServer()
        audit = AuditLog(":memory:")
        # ADR-013: allowlist may be AllowlistEntry objects (with attestations)
        # or plain fingerprint strings. Pass through as list; Gate handles both.
        # D1: the kernel's suppress branch requires fresh evidence — give the
        # test gate the same freshness bundle production boots the live gate
        # with (receiver._freshness_monitor_from_env). Freshness only gates
        # suppress, so page-path tests are unaffected.
        allowlist_fps = [a.fingerprint if isinstance(a, AllowlistEntry) else a
                         for a in (allowlist or [])]
        gate = Gate(jev_client, policy.thresholds, list(allowlist_for_gate),
                    audit, shadow=shadow,
                    freshness_monitor=fresh_monitor_for(allowlist_fps))
        forwarder = Forwarder(pd_events_url=self.pd.url,
                              default_routing_key="rk-default")
        config = ReceiverConfig(webhook_secret=webhook_secret, shadow=shadow,
                                webhook_onboarding=webhook_onboarding)
        self.pipeline = Pipeline(Correlator(), gate, forwarder, audit, config,
                                 policy=policy, config_loader=loader,
                                 state_dir=statedir)
        self.server = make_server(0, self.pipeline)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _stop(self):
        try:
            self.pipeline.health.stop()
        except Exception:
            pass
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.pd.close()

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

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as resp:
                return resp.getcode(), json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())


class TestRoutes(ReceiverTestBase):
    def setUp(self):
        self._start(FixedClient(canned(p1=0.9, conf=0.95)))

    def test_healthz(self):
        # Deep check (design 05, §1.2): 200 with evidence, not constant-true.
        code, body = self._get("/healthz")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(sorted(body["checks"].keys()), [
            "config_current",
            "evidence_flowing",
            "forwarder_draining",
            "gate_constructed",
            "no_crashloop_signature",
        ])

    def test_livez(self):
        code, body = self._get("/livez")
        self.assertEqual(code, 200)
        self.assertTrue(body["alive"])

    def test_unknown_route_404(self):
        code, _body = self._get("/nope")
        self.assertEqual(code, 404)
        code, _body = self._post("/nope", b"{}")
        self.assertEqual(code, 404)


class TestPdEnqueue(ReceiverTestBase):
    def setUp(self):
        self._start(FixedClient(canned(p1=0.9, conf=0.95)))  # page_now

    def test_valid_event_pages_and_mirrors_pd_response(self):
        event = _pd_event()
        raw = json.dumps(event).encode()
        code, body = self._post("/v2/enqueue", raw)
        self.assertEqual(code, 200)
        self.assertEqual(body, {"status": "success",
                                "message": "Event processed",
                                "dedup_key": "dk-test"})
        self.assertEqual(len(self.pd.requests), 1)
        self.assertEqual(self.pd.requests[0]["body"], raw)  # original bytes

    def test_dedup_second_identical_event_skips_jev(self):
        client = self.pipeline.gate.client
        event = _pd_event()
        self._post("/v2/enqueue", event)
        code, body = self._post("/v2/enqueue", event)
        self.assertEqual(code, 200)
        self.assertEqual(len(client.calls), 1)  # duplicate inherited, no Jev call

    def test_missing_required_fields_fails_open(self):
        bad = {"routing_key": "rk-abc"}  # no payload
        raw = json.dumps(bad).encode()
        code, body = self._post("/v2/enqueue", raw)
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "success")
        # original bytes still reached PagerDuty
        self.assertEqual(len(self.pd.requests), 1)
        self.assertEqual(self.pd.requests[0]["body"], raw)
        self.assertEqual(self.pipeline.metrics["unparseable"], 1)

    def test_invalid_json_fails_open(self):
        raw = b"{this is not json"
        code, body = self._post("/v2/enqueue", raw,
                                headers={"Content-Type": "application/json"})
        self.assertEqual(code, 200)
        self.assertEqual(len(self.pd.requests), 1)
        self.assertEqual(self.pd.requests[0]["body"], raw)

    def test_non_trigger_action_relayed_untriaged(self):
        event = _pd_event()
        event["event_action"] = "acknowledge"
        raw = json.dumps(event).encode()
        code, body = self._post("/v2/enqueue", raw)
        self.assertEqual(code, 200)
        self.assertEqual(len(self.pipeline.gate.client.calls), 0)  # no triage
        self.assertEqual(self.pd.requests[0]["body"], raw)


class TestSuppressEndToEnd(ReceiverTestBase):
    def test_suppress_never_reaches_pagerduty(self):
        # ADR-013: suppression requires dual attestation (bare fingerprint no longer suppresses — M-1 fix).
        fp = fingerprint_for("web", "http_5xx", "critical", "us-east")
        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        entry = AllowlistEntry(
            fingerprint=fp, author="carol",
            attestations=[
                Attestation("alice", now - timedelta(days=1), "lrq-9f2c-41ab", 30),
                Attestation("bob", now - timedelta(days=1), "lrq-9f2c-41ab", 30),
            ])
        self._start(FixedClient(canned(p1=0.0, conf=0.95)), allowlist=[entry])
        code, body = self._post("/v2/enqueue", _pd_event())
        self.assertEqual(code, 200)
        self.assertEqual(self.pd.requests, [])  # suppressed: no forward
        rows = self.pipeline.audit.decisions_for_fingerprint(fp)
        self.assertEqual(rows[0]["action"], "suppress")


class TestGenericWebhook(ReceiverTestBase):
    def setUp(self):
        self._start(FixedClient(canned(p1=0.9, conf=0.95)),
                    webhook_secret="s3cret-long-enough-for-tests")

    def _generic(self):
        return {"service": "web", "check": "cpu", "title": "cpu hot",
                "severity": "warning", "labels": {"region": "us-east"},
                "metric": {"value": 97.5, "threshold": 90.0}}

    def _sig(self, body: bytes, secret="s3cret-long-enough-for-tests",
             ts=None):
        # ADR-005 canonical scheme: sha256=<hex> over "<ts>.<raw body>".
        import time as _time
        ts = int(_time.time()) if ts is None else ts
        digest = hmac.new(secret.encode(),
                          f"{ts}.".encode() + body,
                          hashlib.sha256).hexdigest()
        return {"X-Sentinel-Signature": f"sha256={digest}",
                "X-Sentinel-Timestamp": str(ts)}

    def test_valid_signature_accepted(self):
        body = json.dumps(self._generic()).encode()
        code, resp = self._post("/webhook/generic", body, self._sig(body))
        self.assertEqual(code, 200)
        self.assertEqual(resp["status"], "success")
        self.assertEqual(resp["disposition"], "page_now")
        sent = json.loads(self.pd.requests[0]["body"])
        self.assertEqual(sent["routing_key"], "rk-default")
        self.assertEqual(sent["payload"]["summary"], "cpu hot")

    def test_bad_signature_rejected(self):
        body = json.dumps(self._generic()).encode()
        code, resp = self._post("/webhook/generic", body,
                                self._sig(body, secret="wrong"))
        self.assertEqual(code, 403)
        self.assertEqual(self.pd.requests, [])

    def test_missing_signature_refused(self):
        # ADR-005 (D11): absent signature refuses in production — the old
        # fail-open (`if not sig: return True`) is the hole being closed.
        body = json.dumps(self._generic()).encode()
        code, resp = self._post("/webhook/generic", body)
        self.assertEqual(code, 403)
        self.assertEqual(resp["status"], "error")
        self.assertEqual(self.pd.requests, [])

    def test_unparseable_generic_fails_open(self):
        # Parse-level fail-open is unchanged: an AUTHENTICATED but
        # unparseable delivery still forwards the original bytes.
        raw = b"not json at all"
        code, resp = self._post("/webhook/generic", raw, self._sig(raw))
        self.assertEqual(code, 200)
        self.assertEqual(self.pd.requests[0]["body"], raw)


class TestReceiverFailOpen(unittest.TestCase):
    def test_dead_jev_still_returns_200_and_relays(self):
        base = ReceiverTestBase()
        base._start(ExplodingClient(JevOverloaded))
        self.addCleanup(base._stop)
        event = _pd_event()
        raw = json.dumps(event).encode()
        code, body = base._post("/v2/enqueue", raw)
        self.assertEqual(code, 200)  # never 5xx for a triage failure
        self.assertEqual(body["status"], "success")
        self.assertEqual(len(base.pd.requests), 1)
        self.assertEqual(base.pd.requests[0]["body"], raw)


class TestShadowEndToEnd(ReceiverTestBase):
    def test_shadow_never_pages(self):
        self._start(FixedClient(canned(p1=0.9, conf=0.95)), shadow=True)
        code, body = self._post("/v2/enqueue", _pd_event())
        self.assertEqual(code, 200)
        # shadow returns passthrough -> forwarder relays the original payload
        self.assertEqual(len(self.pd.requests), 1)
        rows = self.pipeline.audit.decisions_for_fingerprint(
            fingerprint_for("web", "http_5xx", "critical", "us-east"))
        self.assertEqual(rows[0]["action"], "page_now")  # would-be logged
        self.assertEqual(rows[0]["reason"], "shadow")


if __name__ == "__main__":
    unittest.main()
