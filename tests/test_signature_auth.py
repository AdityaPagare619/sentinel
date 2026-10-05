"""Tests for the ADR-005 (D11) webhook-auth fail-closed contract.

Canonical scheme: X-Sentinel-Timestamp (unix seconds) +
X-Sentinel-Signature: sha256=<hex> over b"<ts>.<raw body>",
|now - ts| <= 300s. Production refuses (403) on empty secret,
absent/malformed signature, absent/malformed/stale/future-skewed
timestamp, bad MAC, and legacy timestamp-less signatures.
SENTINEL_WEBHOOK_ONBOARDING=1 may fail open — loudly.
"""

import contextlib
import hashlib
import hmac
import io
import json
import os
import sys
import tempfile
import time
import unittest

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

from sentinel.config import ConfigLoader
from sentinel.receiver import (build_pipeline_from_env,
                               SIGNATURE_MAX_SKEW_S)

from tests.test_receiver import ReceiverTestBase, FixedClient
from tests.test_gate import canned
from tests.helpers import write_flags_json

SECRET = "test-webhook-secret-32-chars-min"


@contextlib.contextmanager
def _env(**overrides):
    """Temporarily set/unset env vars (None value unsets)."""
    sentinel = object()
    saved = {k: os.environ.get(k, sentinel) for k in overrides}
    for k, v in overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is sentinel:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _policy(tmpdir):
    cfgdir = os.path.join(tmpdir, "cfg")
    os.makedirs(cfgdir, exist_ok=True)
    with open(os.path.join(cfgdir, "thresholds.json"), "w") as fh:
        json.dump({}, fh)
    with open(os.path.join(cfgdir, "allowlist.json"), "w") as fh:
        json.dump([], fh)
    # R-10: the loader requires flags.json (fail-closed).
    write_flags_json(cfgdir)
    statedir = os.path.join(tmpdir, "state")
    loader = ConfigLoader(config_dir=cfgdir, state_dir=statedir)
    return loader.load_startup(), statedir


def _sign(body: bytes, secret: str, ts: int | None) -> dict:
    """Canonical ADR-005 signature headers (ts=None -> legacy raw-body)."""
    if ts is None:
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return {"X-Sentinel-Signature": f"sha256={digest}"}
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body,
                      hashlib.sha256).hexdigest()
    return {"X-Sentinel-Signature": f"sha256={digest}",
            "X-Sentinel-Timestamp": str(ts)}


class TestProductionFailClosed(ReceiverTestBase):
    def setUp(self):
        self._start(FixedClient(canned(p1=0.9, conf=0.95)),
                    webhook_secret=SECRET)

    def _generic(self):
        return {"service": "web", "check": "cpu", "title": "cpu hot",
                "severity": "warning"}

    def test_absent_signature_refused(self):
        body = json.dumps(self._generic()).encode()
        code, resp = self._post("/webhook/generic", body)
        self.assertEqual(code, 403)
        self.assertEqual(resp["status"], "error")
        self.assertEqual(self.pd.requests, [])  # nothing forwarded

    def test_empty_secret_refuses_requests(self):
        # Defense-in-depth: even a Pipeline constructed directly with no
        # secret refuses (startup already refuses too — see startup tests).
        self._stop()
        self._start(FixedClient(canned(p1=0.9, conf=0.95)),
                    webhook_secret=None)
        body = json.dumps(self._generic()).encode()
        code, _ = self._post("/webhook/generic", body)
        self.assertEqual(code, 403)
        self.assertEqual(self.pd.requests, [])

    def test_stale_timestamp_rejected(self):
        body = json.dumps(self._generic()).encode()
        ts = int(time.time()) - (SIGNATURE_MAX_SKEW_S + 100)
        code, _ = self._post("/webhook/generic", body, _sign(body, SECRET, ts))
        self.assertEqual(code, 403)
        self.assertEqual(self.pd.requests, [])

    def test_future_timestamp_rejected(self):
        body = json.dumps(self._generic()).encode()
        ts = int(time.time()) + (SIGNATURE_MAX_SKEW_S + 100)
        code, _ = self._post("/webhook/generic", body, _sign(body, SECRET, ts))
        self.assertEqual(code, 403)
        self.assertEqual(self.pd.requests, [])

    def test_malformed_timestamp_rejected(self):
        body = json.dumps(self._generic()).encode()
        headers = _sign(body, SECRET, int(time.time()))
        headers["X-Sentinel-Timestamp"] = "not-a-number"
        code, _ = self._post("/webhook/generic", body, headers)
        self.assertEqual(code, 403)

    def test_legacy_timestamp_less_signature_rejected(self):
        # Raw-body HMAC with no timestamp: indefinite replay window —
        # rejected in production even when the MAC itself is valid.
        body = json.dumps(self._generic()).encode()
        code, _ = self._post("/webhook/generic", body, _sign(body, SECRET, None))
        self.assertEqual(code, 403)
        self.assertEqual(self.pd.requests, [])

    def test_bad_mac_rejected(self):
        body = json.dumps(self._generic()).encode()
        ts = int(time.time())
        code, _ = self._post("/webhook/generic", body,
                             _sign(body, "wrong-secret-32-chars-min-ok", ts))
        self.assertEqual(code, 403)
        self.assertEqual(self.pd.requests, [])

    def test_inside_window_accepted(self):
        # ±5 min tolerance: fresh timestamps on both edges are accepted.
        for delta in (0, -(SIGNATURE_MAX_SKEW_S - 1),
                      SIGNATURE_MAX_SKEW_S - 1):
            body = json.dumps(self._generic()).encode()
            ts = int(time.time()) + delta
            code, resp = self._post("/webhook/generic", body,
                                    _sign(body, SECRET, ts))
            self.assertEqual(code, 200, f"delta={delta}")
            self.assertEqual(resp["status"], "success")


class TestOnboardingMode(ReceiverTestBase):
    def setUp(self):
        self._start(FixedClient(canned(p1=0.9, conf=0.95)),
                    webhook_secret=SECRET, webhook_onboarding=True)

    def _generic(self):
        return {"service": "web", "check": "cpu", "title": "cpu hot",
                "severity": "warning"}

    def test_unsigned_accepted_in_onboarding(self):
        body = json.dumps(self._generic()).encode()
        code, resp = self._post("/webhook/generic", body)
        self.assertEqual(code, 200)
        self.assertEqual(resp["status"], "success")
        self.assertEqual(self.pipeline.metrics["webhook_auth_bypassed"], 1)

    def test_legacy_signature_accepted_in_onboarding(self):
        body = json.dumps(self._generic()).encode()
        code, _ = self._post("/webhook/generic", body,
                             _sign(body, SECRET, None))
        self.assertEqual(code, 200)
        self.assertEqual(self.pipeline.metrics["webhook_auth_bypassed"], 1)

    def test_bad_mac_still_refused_in_onboarding(self):
        # Forgery is not a migration: a bad MAC refuses even fail-open.
        body = json.dumps(self._generic()).encode()
        ts = int(time.time())
        code, _ = self._post("/webhook/generic", body,
                             _sign(body, "wrong-secret-32-chars-min-ok", ts))
        self.assertEqual(code, 403)
        self.assertEqual(self.pd.requests, [])

    def test_health_surfaces_onboarding(self):
        code, body = self._get("/healthz")
        self.assertEqual(code, 200)
        self.assertTrue(body["webhook_auth_fail_open"])


class TestHealthProduction(unittest.TestCase):
    def test_health_flag_false_in_production(self):
        base = ReceiverTestBase()
        base._start(FixedClient(canned(p1=0.9, conf=0.95)),
                    webhook_secret=SECRET)
        self.addCleanup(base._stop)
        code, body = base._get("/healthz")
        self.assertEqual(code, 200)
        self.assertFalse(body["webhook_auth_fail_open"])


class TestStartupGate(unittest.TestCase):
    def test_empty_secret_refuses_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy, statedir = _policy(tmp)
            with _env(SENTINEL_MOCK="1", SENTINEL_DB=":memory:",
                      SENTINEL_STATE_DIR=statedir,
                      SENTINEL_WEBHOOK_SECRET=None,
                      SENTINEL_WEBHOOK_ONBOARDING=None):
                with self.assertRaises(SystemExit):
                    build_pipeline_from_env(policy=policy)

    def test_onboarding_allows_empty_secret_with_loud_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy, statedir = _policy(tmp)
            with _env(SENTINEL_MOCK="1", SENTINEL_DB=":memory:",
                      SENTINEL_STATE_DIR=statedir,
                      SENTINEL_WEBHOOK_SECRET=None,
                      SENTINEL_WEBHOOK_ONBOARDING="1"):
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    pipeline = build_pipeline_from_env(policy=policy)
                warning = buf.getvalue()
                self.assertIn("CRITICAL", warning)
                self.assertIn("FAIL-OPEN", warning)
                self.assertIn("SENTINEL_WEBHOOK_ONBOARDING=1", warning)
                self.assertTrue(pipeline.config.webhook_onboarding)

    def test_weak_secret_still_refuses_in_onboarding(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy, statedir = _policy(tmp)
            with _env(SENTINEL_MOCK="1", SENTINEL_DB=":memory:",
                      SENTINEL_STATE_DIR=statedir,
                      SENTINEL_WEBHOOK_SECRET="short",
                      SENTINEL_WEBHOOK_ONBOARDING="1"):
                with self.assertRaises(SystemExit):
                    build_pipeline_from_env(policy=policy)


if __name__ == "__main__":
    unittest.main()
