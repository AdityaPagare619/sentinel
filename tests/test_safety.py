"""Tests for contract C3 (Track 3): shadow attribution + kill switch.

Verification map (the 0-mistakes bar):
  (a) zero reason="shadow" in production code — static grep test
  (b) shadow dispositions: causal reason + mode=shadow; live: mode=live
  (c) double-write to reason raises
  (d) kill drill: flip → forwarder-halt, measured, audited
  (e) re-arm without confirmation is rejected (unit + HTTP)
  (f) control principle: safety.py never imports Jev/gate/race
"""

import ast
import io
import json
import os
import re
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
_SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "platform", "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

from sentinel import safety as _safety
from sentinel.eventlog import EventLog, EventLogError, utcnow_iso
from sentinel.forwarder import DurableForwarder, ForwarderConfig
from sentinel.models import Disposition, ReasonRewriteError

KillSwitch = _safety.KillSwitch

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------ fakes


class StubPD:
    """PagerDutyClient double: records sends, always 'accepted'."""

    def __init__(self):
        self.calls = []

    def send(self, *, payload_frozen, dedup_key, routing_key,
             attempt_no=1):
        self.calls.append({"dedup_key": dedup_key,
                           "attempt_no": attempt_no})
        return _Accepted()


class _Accepted:
    outcome = "accepted"
    status_code = 202
    vendor_status = "success"
    latency_ms = 0.5
    retry_after_s = None
    error = None
    error_class = "accepted_by_vendor_intake"
    wire_sha256 = "drill-wire-sha256"
    dedup_key = "sentinel/safety/stub"


def _decision_body(**kw):
    body = {"disposition": "page_now", "mode": "live",
            "budget_outcome": "answered_in_time",
            "lock_evaluation": {}, "freshness": {},
            "threshold_counterfactual": {}, "links": {},
            "v01_compat": {"reason": "threshold"}}
    body.update(kw)
    return body


def _enqueue(log, i, **kw):
    fp = f"safety-fp-{i:04d}"
    frozen = json.dumps({"event_action": "trigger",
                         "dedup_key": f"sentinel/safety/{fp}",
                         "payload": {"summary": f"safety {i}",
                                      "severity": "critical"}},
                        sort_keys=True)
    now = utcnow_iso()
    import hashlib
    from datetime import datetime as _dt, timedelta as _td
    max_age = (_dt.fromisoformat(now.replace("Z", "+00:00")) +
               _td(seconds=86400)).strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"
    _seq, obid = log.record_decision_and_enqueue(
        alert_id=f"safety-a{i}", fingerprint=fp, episode_id=fp,
        body=_decision_body(**kw),
        outbox={"alert_id": f"safety-a{i}", "fingerprint": fp,
                "episode_id": fp, "dedup_key": f"sentinel/safety/{fp}",
                "routing_key_ref": "SAFETY_TEST_PD_KEY",
                "payload_frozen": frozen,
                "payload_sha256": hashlib.sha256(
                    frozen.encode()).hexdigest(),
                "priority": 0, "next_attempt_at": now,
                "max_age_at": max_age})
    return obid


def _fwd(log, ks, pd):
    return DurableForwarder(
        log,
        config=ForwarderConfig(env="test", pd_timeout_s=5.0, workers=1,
                               poll_s=3600.0, backoff_s=(0.01,),
                               jitter=0.0,
                               secondary_fire_after_s=3600.0,
                               spill_dir=None, stage="shadow"),
        pd_client=pd, kill_switch=ks)


# ------------------------------------------------------------------ (a)


class TestNoShadowReasonLiteral(unittest.TestCase):
    def test_zero_reason_shadow_in_production_code(self):
        """Contract C3: reason="shadow" must not exist in production code.
        (Tests asserting the write-once guard are exempt — they must name
        the forbidden pattern to prove it raises.)"""
        pat = re.compile(r"""reason\s*=\s*['"]shadow['"]""")
        offenders = []
        for root in ("src", "platform", "scripts"):
            for dirpath, _dirs, files in os.walk(
                    os.path.join(_REPO, root)):
                for fn in files:
                    if not fn.endswith(".py"):
                        continue
                    p = os.path.join(dirpath, fn)
                    with open(p) as fh:
                        for n, line in enumerate(fh, 1):
                            if pat.search(line):
                                offenders.append(f"{p}:{n}:{line.strip()}")
        self.assertEqual(offenders, [],
                         f'reason="shadow" still present: {offenders}')


# ------------------------------------------------------------- (b) + (c)


class TestDispositionMode(unittest.TestCase):
    def test_mode_defaults_live(self):
        d = Disposition(action="page_now", reason="threshold", team="t",
                        confidence=0.9, latency_ms=1.0)
        self.assertEqual(d.mode, "live")

    def test_mode_validated(self):
        with self.assertRaises(ValueError):
            Disposition(action="page_now", reason="threshold", team="t",
                        confidence=0.9, latency_ms=1.0, mode="nope")

    def test_as_shadow_carries_causal_reason(self):
        d = Disposition(action="page_now", reason="kill_switch", team="t",
                        confidence=0.9, latency_ms=1.0)
        s = d.as_shadow()
        self.assertEqual(s.mode, "shadow")
        self.assertEqual(s.reason, "kill_switch")  # causal, untouched
        self.assertEqual(s.action, "page_now")     # would-be kept

    def test_as_shadow_shell_forces_passthrough_keeps_reason(self):
        d = Disposition(action="suppress", reason="flap_debounce", team="t",
                        confidence=0.9, latency_ms=1.0)
        s = d.as_shadow_shell()
        self.assertEqual(s.action, "passthrough")
        self.assertEqual(s.reason, "flap_debounce")
        self.assertEqual(s.mode, "shadow")

    def test_reason_write_once_raises(self):
        d = Disposition(action="page_now", reason="duplicate", team="t",
                        confidence=0.9, latency_ms=1.0)
        with self.assertRaises(ReasonRewriteError):
            d.reason = "shadow"
        # The original value survives the refused write.
        self.assertEqual(d.reason, "duplicate")

    def test_other_fields_stay_mutable(self):
        d = Disposition(action="page_now", reason="threshold", team="t",
                        confidence=0.9, latency_ms=1.0)
        d.action = "suppress"  # only reason is write-once
        self.assertEqual(d.action, "suppress")


# ------------------------------------------------------------- kill switch


class KillSwitchCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "sentinel.db")
        self.log = EventLog(self.db)
        self.addCleanup(self.log.close)
        self.ks = KillSwitch(log=self.log)


class TestKillSwitch(KillSwitchCase):
    def test_engage_flips_immediately_and_audits(self):
        st = self.ks.engage(actor_id="op-1", reason="manual")
        self.assertTrue(st["engaged"])
        self.assertTrue(st["transitioned"])
        self.assertTrue(self.ks.engaged)
        evs = self.log.events_by_type("kill_switch_engaged")
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["actor"], "operator")
        body = json.loads(evs[0]["body"])
        self.assertEqual(body["actor_id"], "op-1")
        self.assertEqual(body["reason"], "manual")
        self.assertFalse(body["previous_engaged"])
        self.assertIn("engaged_at", body)

    def test_engage_idempotent_no_duplicate_audit(self):
        self.ks.engage(actor_id="op-1")
        st = self.ks.engage(actor_id="op-1")
        self.assertFalse(st["transitioned"])
        self.assertEqual(len(self.log.events_by_type("kill_switch_engaged")),
                         1)

    def test_disengage_requires_explicit_confirmation(self):
        self.ks.engage(actor_id="op-1")
        with self.assertRaises(_safety.RearmRefused):
            self.ks.disengage(actor_id="op-1")
        with self.assertRaises(_safety.RearmRefused):
            self.ks.disengage(actor_id="op-1", confirm=False)
        with self.assertRaises(_safety.RearmRefused):
            self.ks.disengage(actor_id="op-1", confirm="true")
        # Still engaged: no silent auto-rearm, ever.
        self.assertTrue(self.ks.engaged)
        self.assertEqual(len(self.log.events_by_type("kill_switch_rearmed")),
                         0)

    def test_disengage_with_confirm_rearms_and_audits(self):
        self.ks.engage(actor_id="op-1")
        st = self.ks.disengage(actor_id="op-1", confirm=True)
        self.assertFalse(st["engaged"])
        self.assertTrue(st["transitioned"])
        self.assertFalse(self.ks.engaged)
        evs = self.log.events_by_type("kill_switch_rearmed")
        self.assertEqual(len(evs), 1)
        body = json.loads(evs[0]["body"])
        self.assertTrue(body["confirmed"])
        self.assertTrue(body["previous_engaged"])
        self.assertEqual(body["actor_id"], "op-1")

    def test_sticky_reentry_no_auto_rearm(self):
        # Engaging, then waiting, never re-arms by itself.
        self.ks.engage(actor_id="op-1")
        import time as _t
        _t.sleep(0.05)
        self.assertTrue(self.ks.engaged)

    def test_state_file_cross_process(self):
        p = os.path.join(self.tmp.name, "kill-switch.json")
        a = KillSwitch(state_path=p)
        b = KillSwitch(state_path=p)
        self.assertFalse(b.engaged)
        a.engage(actor_id="op-1")
        self.assertTrue(b.engaged)  # separate instance, same file
        a.disengage(confirm=True, actor_id="op-1")
        self.assertFalse(b.engaged)
        st = os.stat(p)
        self.assertEqual(st.st_mode & 0o777, 0o600)


# ------------------------------------------------------------- (d) forwarder


class TestForwarderKill(KillSwitchCase):
    def setUp(self):
        super().setUp()
        os.environ["SAFETY_TEST_PD_KEY"] = "safety-test-key"
        self.addCleanup(os.environ.pop, "SAFETY_TEST_PD_KEY", None)
        self.pd = StubPD()
        self.fwd = _fwd(self.log, self.ks, self.pd)

    def test_kill_halts_claims_and_attempts(self):
        _enqueue(self.log, 1)
        _enqueue(self.log, 2)
        self.assertEqual(self.fwd.run_once_sync(), 2)
        self.assertEqual(len(self.pd.calls), 2)

        self.ks.engage(actor_id="op-1")
        _enqueue(self.log, 3)
        # Halted: nothing claimed, nothing attempted.
        self.assertEqual(self.fwd.run_once_sync(), 0)
        self.assertEqual(len(self.pd.calls), 2)
        row = self.log.outbox_row(_obid(self.log, 3))
        self.assertEqual(row["status"], "queued")

        # The halt is audited exactly once.
        halted = self.log.events_by_type("forwarder_halted")
        self.assertEqual(len(halted), 1)
        self.assertEqual(halted[0]["actor"], "forwarder")
        hbody = json.loads(halted[0]["body"])
        self.assertIn("halted_at", hbody)
        self.assertIn("kill_engaged_at", hbody)

        # A second halt check does not duplicate the event.
        self.assertEqual(self.fwd.run_once_sync(), 0)
        self.assertEqual(len(self.log.events_by_type("forwarder_halted")),
                         1)

    def test_attempt_refused_while_engaged(self):
        obid = _enqueue(self.log, 4)
        row = self.log.outbox_row(obid)
        self.ks.engage(actor_id="op-1")
        with self.assertRaises(_safety.KillEngaged):
            self.fwd._attempt(row)

    def test_rearm_resumes_pipeline(self):
        _enqueue(self.log, 5)
        self.assertEqual(self.fwd.run_once_sync(), 1)
        self.ks.engage(actor_id="op-1")
        _enqueue(self.log, 6)
        self.assertEqual(self.fwd.run_once_sync(), 0)
        self.assertEqual(len(self.pd.calls), 1)
        self.ks.disengage(actor_id="op-1", confirm=True)
        self.fwd.run_once_sync()
        self.assertEqual(len(self.pd.calls), 2)  # resumed

    def test_degraded_send_absorbed_while_engaged(self):
        self.ks.engage(actor_id="op-1")
        # Must not raise and must not send: absorbed, loudly.
        self.fwd._degraded_send({"kind": "drill", "summary": "x"})
        self.assertEqual(self.pd.calls, [])

    def test_stop_start_cycle_supported(self):
        # The re-arm recovery path: stop() then start() must work.
        _enqueue(self.log, 7)
        self.assertEqual(self.fwd.run_once_sync(), 1)
        self.fwd.stop()
        self.fwd.start()
        _enqueue(self.log, 8)
        self.assertEqual(self.fwd.run_once_sync(), 1)
        self.assertEqual(len(self.pd.calls), 2)
        self.fwd.stop()


def _obid(log, i):
    fp = f"safety-fp-{i:04d}"
    rows = log.events_by_type("decision_made", limit=50)
    for r in rows:
        if r["fingerprint"] == fp:
            return r["outbox_id"]
    raise AssertionError(f"no decision_made for {fp}")


# ------------------------------------------------------------- (e) HTTP


class SafetyHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "sentinel.db")
        self.log = EventLog(self.db)
        self.addCleanup(self.log.close)
        self.ks = KillSwitch(log=self.log)

        import _pkg
        app_mod = _pkg.load("app")
        auth_mod = _pkg.load("auth")
        shed_mod = _pkg.load("shed")
        # Real C1 operator token (Track 1 merged): the app installs the
        # safety verifier from this store at construction.
        self.op_store = auth_mod.OperatorTokenStore(
            os.path.join(self.tmp.name, "operator_token.json"))
        self.token = self.op_store.first_boot_token
        self.addCleanup(_safety.reset_operator_verifier)
        self.app = app_mod.PlatformApp(
            store=object(), registry=object(),
            gate=shed_mod.AdmissionGate(), degrade=shed_mod.DegradePolicy(),
            kill_switch=self.ks, drill_dir=self.tmp.name,
            operator_token_store=self.op_store)

    def _call(self, path, method="GET", body=None, token="USE"):
        raw = json.dumps(body).encode() if body is not None else b""
        env = {"REQUEST_METHOD": method, "PATH_INFO": path,
               "QUERY_STRING": "", "CONTENT_LENGTH": str(len(raw)),
               "wsgi.input": io.BytesIO(raw),
               "wsgi.url_scheme": "http", "SERVER_NAME": "t",
               "SERVER_PORT": "1"}
        if token == "USE":
            token = self.token
        if token:
            env["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        captured = {}

        def start_response(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = headers

        out = b"".join(self.app(env, start_response))
        return int(captured["status"].split()[0]), json.loads(out or b"{}")

    def test_kill_requires_auth_verbatim_401(self):
        status, payload = self._call("/api/v1/safety/kill", "POST",
                                     token=None)
        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "unauthorized"})
        # ...and a wrong token too.
        status, payload = self._call("/api/v1/safety/kill", "POST",
                                     token="wrong")
        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "unauthorized"})
        self.assertFalse(self.ks.engaged)

    def test_fail_closed_with_no_verifier(self):
        _safety.reset_operator_verifier()  # Track 1 not merged: deny all
        status, payload = self._call("/api/v1/safety/kill", "POST")
        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "unauthorized"})

    def test_kill_flips_and_audits_actor(self):
        status, payload = self._call("/api/v1/safety/kill", "POST")
        self.assertEqual(status, 200)
        self.assertTrue(payload["engaged"])
        self.assertTrue(self.ks.engaged)
        evs = self.log.events_by_type("kill_switch_engaged")
        self.assertEqual(len(evs), 1)
        self.assertEqual(json.loads(evs[0]["body"])["actor_id"],
                         "operator")

    def test_rearm_without_confirm_rejected(self):
        self._call("/api/v1/safety/kill", "POST")
        for bad in ({}, {"confirm": False}, {"confirm": "true"},
                    {"confirm": 1}):
            status, payload = self._call("/api/v1/safety/rearm", "POST",
                                         body=bad)
            self.assertEqual(status, 422, bad)
            self.assertEqual(payload["error"],
                             "rearm_requires_confirmation")
        self.assertTrue(self.ks.engaged)  # still killed

    def test_rearm_with_confirm(self):
        self._call("/api/v1/safety/kill", "POST")
        status, payload = self._call("/api/v1/safety/rearm", "POST",
                                     body={"confirm": True})
        self.assertEqual(status, 200)
        self.assertFalse(payload["engaged"])
        self.assertFalse(self.ks.engaged)

    def test_status_unmeasured_without_artifact(self):
        status, payload = self._call("/api/v1/safety/status")
        self.assertEqual(status, 200)
        self.assertIn("engaged", payload)
        # No drill artifact in this dir → None: the UI must show
        # "unmeasured"; the <5s claim is forbidden.
        self.assertIsNone(payload["last_drill"])
        self.assertIsNone(_safety.latest_drill(self.tmp.name))

    def test_status_carries_drill_artifact(self):
        doc = {"artifact": "kill-drill", "measured_ms": 12.0,
               "passed": True}
        with open(os.path.join(self.tmp.name,
                               "kill-drill-latest.json"), "w") as fh:
            json.dump(doc, fh)
        status, payload = self._call("/api/v1/safety/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["last_drill"]["measured_ms"], 12.0)

    def test_safety_unavailable_without_kill_switch(self):
        import _pkg
        app_mod = _pkg.load("app")
        shed_mod = _pkg.load("shed")
        app = app_mod.PlatformApp(
            store=object(), registry=object(),
            gate=shed_mod.AdmissionGate(), degrade=shed_mod.DegradePolicy(),
            operator_token_store=self.op_store)
        env = {"REQUEST_METHOD": "POST",
               "PATH_INFO": "/api/v1/safety/kill", "QUERY_STRING": "",
               "CONTENT_LENGTH": "0", "wsgi.input": io.BytesIO(b""),
               "wsgi.url_scheme": "http", "SERVER_NAME": "t",
               "SERVER_PORT": "1",
               "HTTP_AUTHORIZATION": f"Bearer {self.token}"}
        captured = {}

        def sr(status, headers, exc_info=None):
            captured["status"] = status

        out = b"".join(app(env, sr))
        self.assertEqual(int(captured["status"].split()[0]), 503)
        self.assertEqual(json.loads(out)["error"], "safety_unavailable")


# ------------------------------------------------------------- (f) control


class TestControlPrinciple(unittest.TestCase):
    def test_safety_imports_no_jev_gate_race(self):
        """The kill path never calls Jev and never waits on the race:
        safety.py must not import the Jev client, the gate, or the race."""
        with open(os.path.join(_SRC, "sentinel", "safety.py")) as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        for banned in ("client", "gate", "race", "jev", "typesafe"):
            self.assertNotIn(
                banned, {m.split(".")[-1] for m in imported},
                f"safety.py must not import {banned} (control principle)")

    def test_kill_path_touches_no_gate_or_race(self):
        src = open(os.path.join(_SRC, "sentinel", "safety.py")).read()
        for name in ("Gate(", "Race(", "SystemOne", "decide("):
            self.assertNotIn(name, src)


# ------------------------------------------------------------- log guards


class TestEventLogGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = EventLog(os.path.join(self.tmp.name, "t.db"))
        self.addCleanup(self.log.close)

    def test_decision_made_rejects_shadow_reason(self):
        """The log itself refuses the old lie: 'shadow' as a reason."""
        body = _decision_body()
        body["v01_compat"] = {"reason": "shadow"}
        with self.assertRaises(EventLogError):
            self.log.append_event(
                "decision_made", actor="engine", alert_id="a",
                fingerprint="f", episode_id="e", body=body)

    def test_decision_made_rejects_bad_mode(self):
        body = _decision_body(mode="sideways")
        with self.assertRaises(EventLogError):
            self.log.append_event(
                "decision_made", actor="engine", alert_id="a",
                fingerprint="f", episode_id="e", body=body)

    def test_kill_events_round_trip(self):
        seq = self.log.append_event(
            "kill_switch_engaged", actor="operator", alert_id="kill-switch",
            fingerprint="kill-switch", episode_id="kill-switch",
            body={"engaged_at": utcnow_iso(), "previous_engaged": False,
                  "reason": "manual", "actor_id": "t"})
        self.assertGreater(seq, 0)
        evs = self.log.events_by_type("kill_switch_engaged")
        self.assertEqual(len(evs), 1)
        # Unknown event types are still rejected (closed vocabulary).
        with self.assertRaises(EventLogError):
            self.log.events_by_type("nope")


if __name__ == "__main__":
    unittest.main()
