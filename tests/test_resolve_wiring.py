"""Tests for D10 resolve wiring (lane/d10-resolve-wiring).

resolve_episode() has existed since D10 (#70) but nothing called it in
production — episodes opened and could never close (R9). These tests pin
the wiring:

  * a SIGNED PD resolve/acknowledge claim on /v2/enqueue closes the
    matching episode (reason="verified_resolve");
  * unsigned / badly-signed / legacy-signed claims NEVER close (the relay
    still returns 200 unchanged — only the close is refused);
  * unknown fingerprints and already-closed episodes are safe no-ops;
  * POST /episodes/resolve is the operator path (reason="operator_resolve"),
    bearer-token gated and fail-closed when the token is unset;
  * every close is event-logged (episode_resolved: who/what, when, reason).
"""

import hashlib
import hmac
import io
import json
import os
import time
import unittest
from contextlib import redirect_stderr
from unittest import mock

from sentinel.correlator import fingerprint_for

from tests.test_gate import canned
from tests.test_receiver import ReceiverTestBase, _pd_event, FixedClient

SECRET = "s3cret-long-enough-for-tests"
OP_TOKEN = "op-token-for-tests-123"


def _sign(body: bytes, secret: str = SECRET, ts=None) -> dict:
    """ADR-005 canonical scheme: sha256=<hex> over "<ts>.<raw body>"."""
    ts = int(time.time()) if ts is None else ts
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body,
                      hashlib.sha256).hexdigest()
    return {"X-Sentinel-Signature": f"sha256={digest}",
            "X-Sentinel-Timestamp": str(ts)}


def _resolve_claim(dedup_key: str, action: str = "resolve") -> dict:
    return {"routing_key": "rk-abc", "event_action": action,
            "dedup_key": dedup_key}


class ResolveWiringTestBase(ReceiverTestBase):
    def _start_signed(self, **kw):
        kw.setdefault("webhook_secret", SECRET)
        self._start(FixedClient(canned(p1=0.9, conf=0.95)), **kw)

    def _trigger(self, dedup_key="dk-resolve-1"):
        event = _pd_event(dedup_key=dedup_key)
        code, _ = self._post("/v2/enqueue", event)
        self.assertEqual(code, 200)
        return event

    def _fp(self):
        # The fingerprint _pd_event()'s defaults normalize to.
        return fingerprint_for("web", "http_5xx", "critical", "us-east",
                               env="", cluster="")

    def _post_resolve(self, dedup_key, action="resolve", headers=None):
        body = json.dumps(_resolve_claim(dedup_key, action)).encode()
        return self._post("/v2/enqueue", body, headers=headers or {})

    def _resolved_events(self):
        return self.pipeline.audit.log.events_of_type("episode_resolved")


class TestPdResolveClosesEpisode(ResolveWiringTestBase):
    def setUp(self):
        self._start_signed()

    def test_signed_resolve_closes_episode_verified_resolve(self):
        fp = self._fp()
        self._trigger("dk-r1")
        ep = self.pipeline.correlator.get_episode(fp)
        self.assertIsNotNone(ep)
        self.assertEqual(ep.state, "open")

        code, body = self._post_resolve("dk-r1", headers=_sign(
            json.dumps(_resolve_claim("dk-r1")).encode()))
        self.assertEqual(code, 200)

        ep = self.pipeline.correlator.get_episode(fp)
        self.assertEqual(ep.state, "closed")
        self.assertEqual(ep.close_reason, "verified_resolve")
        self.assertEqual(self.pipeline.metrics["episodes_resolved"], 1)

        # Every close is event-logged: who/what, when, which reason.
        events = self._resolved_events()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["actor"], "engine")
        self.assertEqual(ev["fingerprint"], fp)
        self.assertEqual(ev["episode_id"], fp)
        ebody = json.loads(ev["body"])
        self.assertEqual(ebody["reason"], "verified_resolve")
        self.assertEqual(ebody["resolved_by"], "pagerduty-webhook")
        self.assertEqual(ebody["dedup_key"], "dk-r1")
        self.assertIn("closed_ts", ebody)

        # The PD relay still happened unchanged (contract untouched).
        self.assertEqual(len(self.pd.requests), 2)
        self.assertEqual(json.loads(self.pd.requests[-1]["body"]),
                         _resolve_claim("dk-r1"))

    def test_signed_acknowledge_also_closes(self):
        fp = self._fp()
        self._trigger("dk-r2")
        raw = json.dumps(_resolve_claim("dk-r2", "acknowledge")).encode()
        code, _ = self._post("/v2/enqueue", raw, headers=_sign(raw))
        self.assertEqual(code, 200)
        ep = self.pipeline.correlator.get_episode(fp)
        self.assertEqual(ep.state, "closed")
        self.assertEqual(ep.close_reason, "verified_resolve")

    def test_second_resolve_is_safe_noop(self):
        self._trigger("dk-r3")
        raw = json.dumps(_resolve_claim("dk-r3")).encode()
        self._post("/v2/enqueue", raw, headers=_sign(raw))
        code, _ = self._post("/v2/enqueue", raw, headers=_sign(raw))
        self.assertEqual(code, 200)
        self.assertEqual(self.pipeline.metrics["episodes_resolved"], 1)
        self.assertEqual(self.pipeline.metrics["resolve_noop"], 1)
        self.assertEqual(len(self._resolved_events()), 1)  # no second event


class TestResolveAuth(ResolveWiringTestBase):
    def setUp(self):
        self._start_signed()

    def test_hostile_dedup_key_cannot_forge_log_lines(self):
        # D10: dedup_key is sender-controlled. A newline in it must not
        # inject fake [sentinel] lines into stderr — the emission
        # boundary replaces control chars (fail-before: the raw newline
        # split the log line and a forged line appeared at line start).
        # The attacker's text may still appear INLINE (evidence), but it
        # must never begin a log line.
        hostile = "dk-evil\n[sentinel] FORGED LINE\n"
        raw = json.dumps(_resolve_claim(hostile)).encode()
        err = io.StringIO()
        with redirect_stderr(err):
            code, _ = self._post("/v2/enqueue", raw, headers=_sign(raw))
        self.assertEqual(code, 200)  # unknown key: safe no-op, never error
        out = err.getvalue()
        self.assertFalse(
            any(line.startswith("[sentinel] FORGED")
                for line in out.splitlines()),
            f"forged log line present in: {out!r}")
        # evidence preserved: the sanitized key is still recorded in-band
        self.assertIn("dk-evil?", out)

    def test_unsigned_resolve_does_not_close(self):
        fp = self._fp()
        self._trigger("dk-u1")
        code, _ = self._post_resolve("dk-u1")  # no signature headers
        self.assertEqual(code, 200)  # relay still 200
        ep = self.pipeline.correlator.get_episode(fp)
        self.assertEqual(ep.state, "open")  # but the episode stays open
        self.assertEqual(self.pipeline.metrics["resolve_auth_refused"], 1)
        self.assertEqual(self._resolved_events(), [])
        # Relay unchanged: PD still got the claim.
        self.assertEqual(len(self.pd.requests), 2)

    def test_bad_signature_resolve_does_not_close(self):
        fp = self._fp()
        self._trigger("dk-u2")
        raw = json.dumps(_resolve_claim("dk-u2")).encode()
        code, _ = self._post("/v2/enqueue", raw,
                             headers=_sign(raw, secret="wrong-secret-value!"))
        self.assertEqual(code, 200)
        self.assertEqual(self.pipeline.correlator.get_episode(fp).state,
                         "open")
        self.assertEqual(self.pipeline.metrics["resolve_auth_refused"], 1)
        self.assertEqual(self._resolved_events(), [])

    def test_legacy_timestamp_less_signature_refused(self):
        # Legacy raw-body HMAC has an indefinite replay window: it never
        # counts for the silence direction, even though the generic route
        # may accept it loudly during onboarding.
        fp = self._fp()
        self._trigger("dk-u3")
        raw = json.dumps(_resolve_claim("dk-u3")).encode()
        legacy = hmac.new(SECRET.encode(), raw,
                          hashlib.sha256).hexdigest()
        code, _ = self._post(
            "/v2/enqueue", raw,
            headers={"X-Sentinel-Signature": f"sha256={legacy}"})
        self.assertEqual(code, 200)
        self.assertEqual(self.pipeline.correlator.get_episode(fp).state,
                         "open")
        self.assertEqual(self.pipeline.metrics["resolve_auth_refused"], 1)

    def test_stale_timestamp_resolve_refused(self):
        fp = self._fp()
        self._trigger("dk-u4")
        raw = json.dumps(_resolve_claim("dk-u4")).encode()
        code, _ = self._post("/v2/enqueue", raw,
                             headers=_sign(raw, ts=int(time.time()) - 3600))
        self.assertEqual(code, 200)
        self.assertEqual(self.pipeline.correlator.get_episode(fp).state,
                         "open")
        self.assertEqual(self.pipeline.metrics["resolve_auth_refused"], 1)

    def test_signed_resolve_unknown_dedup_key_safe_noop(self):
        raw = json.dumps(_resolve_claim("dk-never-seen")).encode()
        code, body = self._post("/v2/enqueue", raw, headers=_sign(raw))
        self.assertEqual(code, 200)  # no exception, relay still 200
        self.assertEqual(self.pipeline.metrics["resolve_noop"], 1)
        self.assertEqual(self.pipeline.metrics["episodes_resolved"], 0)
        self.assertEqual(self._resolved_events(), [])
        self.assertEqual(len(self.pd.requests), 1)  # claim still relayed


class TestOnboardingStrictness(ResolveWiringTestBase):
    def setUp(self):
        # Onboarding fail-open applies to trigger migration (noise
        # direction). The silence direction stays fail-closed in EVERY mode.
        self._start_signed(webhook_onboarding=True)

    def test_unsigned_resolve_refused_during_onboarding(self):
        fp = self._fp()
        self._trigger("dk-ob1")
        code, _ = self._post_resolve("dk-ob1")
        self.assertEqual(code, 200)
        self.assertEqual(self.pipeline.correlator.get_episode(fp).state,
                         "open")
        self.assertEqual(self.pipeline.metrics["resolve_auth_refused"], 1)
        self.assertEqual(self._resolved_events(), [])

    def test_signed_resolve_still_closes_during_onboarding(self):
        fp = self._fp()
        self._trigger("dk-ob2")
        raw = json.dumps(_resolve_claim("dk-ob2")).encode()
        code, _ = self._post("/v2/enqueue", raw, headers=_sign(raw))
        self.assertEqual(code, 200)
        self.assertEqual(self.pipeline.correlator.get_episode(fp).state,
                         "closed")


class TestOperatorResolve(ResolveWiringTestBase):
    def setUp(self):
        self._start_signed()

    def _op_headers(self, token=OP_TOKEN, operator=None):
        h = {"Authorization": f"Bearer {token}"}
        if operator:
            h["X-Operator"] = operator
        return h

    def test_operator_resolve_closes_with_operator_reason(self):
        fp = self._fp()
        self._trigger("dk-op1")
        with mock.patch.dict(os.environ,
                             {"SENTINEL_HEALTH_TOKEN": OP_TOKEN}):
            code, body = self._post("/episodes/resolve",
                                    {"fingerprint": fp},
                                    headers=self._op_headers(
                                        operator="aditya"))
        self.assertEqual(code, 200)
        self.assertEqual(body, {"status": "ok", "fingerprint": fp,
                               "closed": True})
        ep = self.pipeline.correlator.get_episode(fp)
        self.assertEqual(ep.state, "closed")
        self.assertEqual(ep.close_reason, "operator_resolve")
        self.assertEqual(self.pipeline.metrics["episodes_resolved"], 1)
        events = self._resolved_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["actor"], "operator")
        ebody = json.loads(events[0]["body"])
        self.assertEqual(ebody["reason"], "operator_resolve")
        self.assertEqual(ebody["resolved_by"], "aditya")

    def test_operator_resolve_by_dedup_key(self):
        fp = self._fp()
        self._trigger("dk-op2")
        with mock.patch.dict(os.environ,
                             {"SENTINEL_HEALTH_TOKEN": OP_TOKEN}):
            code, body = self._post("/episodes/resolve",
                                    {"dedup_key": "dk-op2"},
                                    headers=self._op_headers())
        self.assertEqual(code, 200)
        self.assertEqual(body["closed"], True)
        self.assertEqual(body["fingerprint"], fp)
        self.assertEqual(
            self.pipeline.correlator.get_episode(fp).close_reason,
            "operator_resolve")

    def test_operator_resolve_unknown_fingerprint_noop(self):
        with mock.patch.dict(os.environ,
                             {"SENTINEL_HEALTH_TOKEN": OP_TOKEN}):
            code, body = self._post("/episodes/resolve",
                                    {"fingerprint": "0" * 16},
                                    headers=self._op_headers())
        self.assertEqual(code, 200)  # not an error
        self.assertEqual(body["closed"], False)
        self.assertEqual(self.pipeline.metrics["resolve_noop"], 1)
        self.assertEqual(self._resolved_events(), [])

    def test_operator_resolve_missing_identifier_400(self):
        with mock.patch.dict(os.environ,
                             {"SENTINEL_HEALTH_TOKEN": OP_TOKEN}):
            code, body = self._post("/episodes/resolve", {},
                                    headers=self._op_headers())
        self.assertEqual(code, 400)

    def test_operator_resolve_deeply_nested_json_400(self):
        # Same class of hole as R-8 (#93): RecursionError is not a
        # ValueError subclass, so it escaped _handle_episode_resolve's
        # except tuple and killed the connection instead of returning
        # 400. Now: invalid JSON object, honestly reported.
        raw = b"[" * 25000 + b"]" * 25000
        with mock.patch.dict(os.environ,
                             {"SENTINEL_HEALTH_TOKEN": OP_TOKEN}):
            code, body = self._post("/episodes/resolve", raw,
                                    headers=self._op_headers())
        self.assertEqual(code, 400)
        self.assertEqual(body["status"], "error")

    def test_operator_resolve_requires_token_configured(self):
        # Fail-closed when the token is unset: unlike /healthz (a read),
        # the silence-direction write is never open.
        fp = self._fp()
        self._trigger("dk-op3")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SENTINEL_HEALTH_TOKEN", None)
            code, _ = self._post("/episodes/resolve",
                                 {"fingerprint": fp},
                                 headers=self._op_headers())
        self.assertEqual(code, 401)
        self.assertEqual(self.pipeline.correlator.get_episode(fp).state,
                         "open")

    def test_operator_resolve_wrong_bearer_rejected(self):
        fp = self._fp()
        self._trigger("dk-op4")
        with mock.patch.dict(os.environ,
                             {"SENTINEL_HEALTH_TOKEN": OP_TOKEN}):
            code, _ = self._post("/episodes/resolve",
                                 {"fingerprint": fp},
                                 headers=self._op_headers(token="wrong"))
        self.assertEqual(code, 401)
        self.assertEqual(self.pipeline.correlator.get_episode(fp).state,
                         "open")


class TestResolveApiContract(ResolveWiringTestBase):
    def setUp(self):
        self._start_signed()

    def test_unknown_close_reason_raises(self):
        # The D10 API contract: a close must name its authority.
        with self.assertRaises(ValueError):
            self.pipeline.correlator.resolve_episode(
                self._fp(), reason="bogus_reason")

    def test_resolve_unknown_fingerprint_returns_false(self):
        self.assertFalse(self.pipeline.correlator.resolve_episode(
            "f" * 16, reason="operator_resolve"))


if __name__ == "__main__":
    unittest.main()
