"""Kill-switch coverage: EVERY paging path kill-wired, proven per path.

RFC docs/planning/rfc/engine-kill-coverage.md — the inventory. Each test
below names its inventory row. All "PagerDuty"/secondary traffic goes to
loopback doubles. Zero real PagerDuty contact, ever.

Paths already proven elsewhere (not re-proven here):
  row 1 (legacy Forwarder) .... tests/test_kill_topology.py (10 tests)
  row 2 (DurableForwarder._attempt) .. tests/test_safety.py::TestForwarderKill
  row 3 (degraded ladder) ..... tests/test_safety.py::test_degraded_send_absorbed_while_engaged
  row 8 (shadow read-only) .... tests/test_shadow.py::TestReadOnlyProof
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import json
import tempfile
import unittest

from sentinel import safety as _safety
from sentinel.eventlog import EventLog
from sentinel.forwarder import (
    DurableForwarder,
    ForwarderConfig,
    SecondaryNotReady,
)
from sentinel.safety import KillSwitch

from tests.helpers import CaptureServer, make_alert
from tests.test_kill_topology import _disp


class _StubPD:
    """DurableForwarder pd_client double: records, always 'accepted'."""

    def __init__(self):
        self.calls = []

    def send(self, *, payload_frozen, dedup_key, routing_key, attempt_no=1):
        self.calls.append({"dedup_key": dedup_key, "attempt_no": attempt_no})
        return _Accepted()

    def send_event(self, *, event, dedup_key, routing_key):
        self.calls.append({"dedup_key": dedup_key, "direct": True})
        return _DirectAccepted()


class _Accepted:
    outcome = "accepted"
    status_code = 202
    vendor_status = "success"
    latency_ms = 1.0
    wire_sha256 = "abc"
    error = None
    error_class = None


class _DirectAccepted:
    outcome = "accepted"
    status_code = 202
    error = None
    latency_ms = 1.0


class _FakeSecondary:
    """WebhookSecondary double: records sends, always ok."""

    def __init__(self):
        self.calls = []

    def send(self, page):
        self.calls.append(dict(page))
        return _SecondaryResult()


class _SecondaryResult:
    ok = True
    detail = "fake-accepted"
    latency_ms = 1.0


def _enqueue(log, i, **kw):
    fp = f"cov-fp-{i:04d}"
    frozen = json.dumps({"event_action": "trigger",
                         "dedup_key": f"sentinel/cov/{fp}",
                         "payload": {"summary": f"cov {i}",
                                     "severity": "critical"}},
                        sort_keys=True)
    import hashlib
    from sentinel.eventlog import utcnow_iso
    from datetime import datetime as _dt, timedelta as _td
    now = utcnow_iso()
    max_age = (_dt.fromisoformat(now.replace("Z", "+00:00")) +
               _td(seconds=86400)).strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"
    _seq, obid = log.record_decision_and_enqueue(
        alert_id=f"cov-a{i}", fingerprint=fp, episode_id=fp,
        body={"disposition": "page_now", "budget_outcome": "answered_in_time",
              "lock_evaluation": {}, "freshness": {},
              "threshold_counterfactual": {}, "links": {}},
        outbox={"alert_id": f"cov-a{i}", "fingerprint": fp,
                "episode_id": fp, "dedup_key": f"sentinel/cov/{fp}",
                "routing_key_ref": "COV_TEST_PD_KEY",
                "payload_frozen": frozen,
                "payload_sha256": hashlib.sha256(frozen.encode()).hexdigest(),
                "priority": 0, "next_attempt_at": now,
                "max_age_at": max_age})
    return obid


class DurableSecondaryKillBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = EventLog(os.path.join(self.tmp.name, "t.db"))
        self.ks = KillSwitch()
        self.pd = _StubPD()
        self.secondary = _FakeSecondary()
        self._old_env = dict(os.environ)
        os.environ["COV_TEST_PD_KEY"] = "cov-routing-key"
        os.environ["COV_TEST_CONTROL_KEY"] = "cov-control-key"
        # Control-plane rows resolve the control-plane routing-key ref via
        # the secret-store env convention (production wiring).
        os.environ["SENTINEL_SECRET_PD_CONTROL_ROUTING_KEY"] = "cov-control-key"
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._old_env)

    def _fw(self, **kw):
        cfg = dict(env="test", pd_endpoint="http://127.0.0.1:1/",
                   pd_timeout_s=1.0, workers=1, poll_s=3600.0,
                   backoff_s=(0.05,), jitter=0.0,
                   secondary_fire_after_s=0.0,  # rows are immediately past X
                   spill_dir=os.path.join(self.tmp.name, "spill"),
                   stage="shadow", control_routing_key_ref="COV_TEST_CONTROL_KEY",
                   secret_mapping={"COV_TEST_CONTROL_KEY": "COV_TEST_CONTROL_KEY"})
        cfg.update(kw)
        return DurableForwarder(self.log, ForwarderConfig(**cfg),
                                pd_client=self.pd,
                                secondary=self.secondary,
                                kill_switch=self.ks)


class TestSecondaryScanKill(DurableSecondaryKillBase):
    """Row 4a: _secondary_scan — undelivered-past-X secondary fire."""

    def test_scan_fires_when_disengaged(self):
        fw = self._fw()
        _enqueue(self.log, 1)
        self.assertEqual(fw._secondary_scan("2099-01-01T00:00:00Z"), 1)
        self.assertEqual(len(self.secondary.calls), 1)

    def test_scan_halted_while_killed(self):
        fw = self._fw()
        _enqueue(self.log, 2)
        self.ks.engage(actor_id="op-1")
        # Engaged: the scan fires nothing — the operator deliberately
        # stopped paging, and the secondary is a paging path.
        self.assertEqual(fw._secondary_scan("2099-01-01T00:00:00Z"), 0)
        self.assertEqual(self.secondary.calls, [])
        # Re-arm: the scan refires for rows still past X.
        self.ks.disengage(actor_id="op-1", confirm=True)
        self.assertEqual(fw._secondary_scan("2099-01-01T00:00:00Z"), 1)
        self.assertEqual(len(self.secondary.calls), 1)


class TestTerminalSecondaryKill(DurableSecondaryKillBase):
    """Row 4b: _terminal — primary terminally failed, secondary fire."""

    def test_terminal_secondary_absorbed_while_killed(self):
        fw = self._fw()
        obid = _enqueue(self.log, 3)
        row = self.log.outbox_row(obid)
        self.ks.engage(actor_id="op-1")
        fw._terminal(row, 1, "payload_formation_error", "boom")
        # The morgue keeps its record...
        row2 = self.log.outbox_row(obid)
        self.assertEqual(row2["status"], "dead_letter")
        # ...but nothing goes out while engaged.
        self.assertEqual(self.secondary.calls, [])

    def test_terminal_secondary_fires_when_disengaged(self):
        fw = self._fw()
        obid = _enqueue(self.log, 4)
        row = self.log.outbox_row(obid)
        fw._terminal(row, 1, "payload_formation_error", "boom")
        self.assertEqual(len(self.secondary.calls), 1)


class TestSecondaryDrillKill(DurableSecondaryKillBase):
    """Row 5: run_secondary_drill refused while killed."""

    def test_drill_allowed_when_disengaged(self):
        fw = self._fw()
        drill_id = fw.run_secondary_drill()
        self.assertTrue(drill_id)
        self.assertEqual(len(self.secondary.calls), 1)

    def test_drill_refused_while_killed(self):
        fw = self._fw()
        self.ks.engage(actor_id="op-1")
        with self.assertRaises(SecondaryNotReady) as ctx:
            fw.run_secondary_drill()
        self.assertIn("kill switch", str(ctx.exception).lower())
        self.assertEqual(self.secondary.calls, [])
        # Re-arm, then the drill verifies the resumed path.
        self.ks.disengage(actor_id="op-1", confirm=True)
        fw.run_secondary_drill()
        self.assertEqual(len(self.secondary.calls), 1)


class TestSendDirectKill(DurableSecondaryKillBase):
    """Row 6: send_direct — public standby direct-to-PD, defense in depth."""

    def test_send_direct_absorbed_while_killed(self):
        fw = self._fw()
        self.ks.engage(actor_id="op-1")
        outcome = fw.send_direct({"kind": "drill", "summary": "x"},
                                 reason="test")
        self.assertEqual(outcome["pd_outcome"], "killed")
        self.assertTrue(outcome["killed"])
        self.assertEqual(self.pd.calls, [])

    def test_send_direct_sends_when_disengaged(self):
        fw = self._fw()
        outcome = fw.send_direct({"kind": "drill", "summary": "x"},
                                 reason="test")
        self.assertEqual(outcome["pd_outcome"], "accepted")
        self.assertEqual(len(self.pd.calls), 1)


class TestControlPlanePageKill(DurableSecondaryKillBase):
    """Row 11: control-plane pages ride the outbox -> _attempt (kill-wired)."""

    def test_control_plane_page_queued_not_sent_while_killed(self):
        fw = self._fw()
        self.ks.engage(actor_id="op-1")
        obid = fw.enqueue_control_plane_page(
            kind="secondary_drill_missed",
            summary="forwarder: secondary drill missed",
            detail={"drill_id": "d1"})
        # The scheduler refuses to claim while engaged (row 2's machinery).
        self.assertEqual(fw.run_once_sync(), 0)
        self.assertEqual(self.pd.calls, [])
        row = self.log.outbox_row(obid)
        self.assertEqual(row["status"], "queued")
        # Re-arm: the control-plane page goes out.
        self.ks.disengage(actor_id="op-1", confirm=True)
        self.assertEqual(fw.run_once_sync(), 1)
        self.assertEqual(len(self.pd.calls), 1)


class TestSimForwarderKill(unittest.TestCase):
    """Row 7: sim_runner's forwarder is kill-wired (topological fidelity)."""

    def test_sim_pipeline_kill_absorbs_fakepd_sends(self):
        sim_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "platform", "server", "sim")
        sys.path.insert(0, sim_path)
        try:
            import sim_runner
        finally:
            sys.path.remove(sim_path)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        capture = CaptureServer()
        self.addCleanup(capture.close)
        manifest = {"seed": 7, "_start_epoch": 1_700_000_000}
        judge = ("fake-jev", sim_runner.MockSystemOneClient(), None)
        pipeline, _vclock, _info = sim_runner.build_sim_pipeline(
            manifest, judge,
            f"http://127.0.0.1:{capture.port}/v2/enqueue", tmp.name)
        # The sim pipeline exposes the same flip handle as the receiver.
        self.assertIsNotNone(pipeline.kill_switch)
        alert = make_alert(alert_id="sim-k1")
        disp = _disp("page_now")
        # Disengaged: the page reaches the FakePD sink.
        res = pipeline.forwarder.forward(alert, disp)
        self.assertTrue(res.forwarded)
        self.assertEqual(len(capture.requests), 1)
        # Engaged: absorbed — the sink sees nothing.
        pipeline.kill_switch.engage(actor_id="sim-test")
        res = pipeline.forwarder.forward(alert, disp)
        self.assertTrue(res.killed)
        self.assertFalse(res.forwarded)
        self.assertEqual(len(capture.requests), 1)
        # Re-arm: the sim pages again.
        pipeline.kill_switch.disengage(actor_id="sim-test", confirm=True)
        res = pipeline.forwarder.forward(alert, disp)
        self.assertTrue(res.forwarded)
        self.assertEqual(len(capture.requests), 2)


if __name__ == "__main__":
    unittest.main()
