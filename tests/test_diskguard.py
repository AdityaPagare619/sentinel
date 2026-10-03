"""Tests for the D14 interim disk guard (ADR-024/O-1, Type 2).

The Vault-led retention RFC is the real answer; this is the interim:
a WAL-size watermark on the event-log database that pages the operator
(priority-1 control-plane outbox row, the commit-watchdog convention) and
writes a spill record via spill.py's existing write_spill — without ever
touching the paging hot path.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import json
import os
import tempfile
import time
import unittest
from unittest import mock

from sentinel import eventlog as ev
from sentinel.eventlog import EventLog
from sentinel.spill import SPILL_PREFIX


def _decision_body(**kw):
    body = {
        "input_sha256": "sh1",
        "fingerprint": "fp1",
        "episode_id": "ep1",
        "jev_model": "jev-1.13.0",
        "q1_reported": 0.9,
        "q2_team": "platform",
        "q3_confidence": 0.95,
        "q3_disposition": "page",
        "disposition": "page_now",
        "budget_outcome": "answered_in_time",
        "latency_ms": 120.0,
        "timer_fired_at_ms": None,
        "lock_evaluation": {"verdict": "pass"},
        "freshness": {"fit_as_of": "2026-10-03T00:00:00.000Z"},
        "threshold_counterfactual": {},
        "links": {"decision_requested_seq": 1},
    }
    body.update(kw)
    return body


def _outbox_row(**kw):
    row = {
        "alert_id": "a1", "fingerprint": "fp1", "episode_id": "ep1",
        "dedup_key": "sentinel/prod/fp1/0001",
        "routing_key_ref": "secret:pd/routing_key",
        "payload_frozen": '{"alert":"a1"}',
        "payload_sha256": "deadbeef",
        "priority": 0,
        "next_attempt_at": "2026-10-03T00:00:00.000Z",
        "max_age_at": "2026-10-04T00:00:00.000Z",
    }
    row.update(kw)
    return row


def _mini_body(**kw):
    body = {"input_sha256": "sh1", "links": {"x": 1}}
    body.update(kw)
    return body


class DiskGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "g.db")
        self.spill = os.path.join(self.tmp.name, "spill")

    def tearDown(self):
        self.tmp.cleanup()

    def _log(self, **kw):
        kw.setdefault("spillover_dir", self.spill)
        return EventLog(self.db, **kw)

    def _control_plane_rows(self, log):
        return [r for r in log.undelivered_outbox_rows()
                if r["alert_id"].startswith("control-plane/")]

    # ------------------------------------------------------------ the basics

    def test_fires_page_and_spill_at_threshold(self):
        log = self._log(diskguard_bytes=1)  # any write crosses it
        log.append_event("shadow_decision", actor="engine", alert_id="a1",
                         fingerprint="fp1", episode_id="ep1",
                         body=_mini_body(links={"x": 1}))
        self.assertEqual(log.metrics["disk_guard_fires"], 1)

        rows = self._control_plane_rows(log)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["alert_id"], "control-plane/disk_guard")
        self.assertEqual(rows[0]["priority"], 1)  # control-plane lane

        guard_dir = os.path.join(self.spill, ev.DISKGUARD_SPILL_SUBDIR)
        spills = [n for n in os.listdir(guard_dir)
                  if n.startswith(SPILL_PREFIX)]
        self.assertEqual(len(spills), 1)
        with open(os.path.join(guard_dir, spills[0])) as fh:
            rec = json.load(fh)
        self.assertEqual(rec["kind"], "disk_guard")
        self.assertEqual(rec["watermark_bytes"], 1)
        log.close()

    def test_below_threshold_nothing_fires(self):
        log = self._log(diskguard_bytes=10 ** 12)
        for _ in range(3):
            log.append_event("shadow_decision", actor="engine",
                             alert_id="a1", fingerprint="fp1",
                             episode_id="ep1",
                             body=_mini_body(links={"x": 1}))
        log.check_disk_watermark(force=True)
        self.assertEqual(log.metrics["disk_guard_fires"], 0)
        self.assertEqual(self._control_plane_rows(log), [])
        self.assertFalse(
            os.path.exists(os.path.join(self.spill, ev.DISKGUARD_SPILL_SUBDIR)))
        log.close()

    def test_exactly_once_per_process(self):
        log = self._log(diskguard_bytes=1)
        log.append_event("shadow_decision", actor="engine", alert_id="a1",
                         fingerprint="fp1", episode_id="ep1",
                         body=_mini_body(links={"x": 1}))
        for _ in range(3):  # more writes + forced re-checks stay silent
            log.append_event("shadow_decision", actor="engine",
                             alert_id="a1", fingerprint="fp1",
                             episode_id="ep1",
                             body=_mini_body(links={"x": 1}))
            log.check_disk_watermark(force=True)
        self.assertEqual(log.metrics["disk_guard_fires"], 1)
        self.assertEqual(len(self._control_plane_rows(log)), 1)
        log.close()

    # ------------------------------------------------- never blocks the page

    def test_paging_path_unaffected_when_guard_fires(self):
        """Triage (decision + outbox row) completes promptly while the
        guard fires — the guard observes post-commit and can never block,
        delay beyond noise, or sink a page."""
        log = self._log(diskguard_bytes=1)
        t0 = time.perf_counter()
        seq, outbox_id = log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(), outbox=_outbox_row())
        elapsed = time.perf_counter() - t0

        self.assertEqual(log.metrics["disk_guard_fires"], 1)
        self.assertIsNotNone(seq)
        self.assertIsNotNone(outbox_id)
        # The triage artifacts are intact: decision event + outbox row.
        self.assertEqual(log.get_event(seq)["type"], "decision_made")
        row = log.outbox_row(outbox_id)
        self.assertEqual(row["status"], "queued")
        # Bounded: the guard's post-commit work (one outbox insert + one
        # spill file) is milliseconds, not a paging-path tax. 2 s is a
        # generous ceiling on any machine — triage must never hang here.
        self.assertLess(elapsed, 2.0,
                        f"guard firing slowed the paging path: {elapsed:.2f}s")
        log.close()

    def test_guard_never_raises_when_page_enqueue_fails(self):
        log = self._log(diskguard_bytes=1)
        with mock.patch.object(log, "_enqueue_control_plane_page",
                               side_effect=RuntimeError("disk sick")):
            fired = log.check_disk_watermark(force=True)
        self.assertTrue(fired)  # latch set; the exception was swallowed
        self.assertEqual(log.metrics["disk_guard_fires"], 1)
        # Spill still landed (belt) even though the page (suspenders) failed.
        guard_dir = os.path.join(self.spill, ev.DISKGUARD_SPILL_SUBDIR)
        self.assertTrue(os.path.isdir(guard_dir))
        log.close()

    # ------------------------------------------------------------- env knob

    def test_env_override(self):
        with mock.patch.dict(os.environ,
                             {ev.DISKGUARD_ENV: str(10 ** 12)}):
            log = self._log()
            log.check_disk_watermark(force=True)
            self.assertEqual(log.metrics["disk_guard_fires"], 0)
            log.close()
        with mock.patch.dict(os.environ, {ev.DISKGUARD_ENV: "1"}):
            log = self._log()
            self.assertEqual(log.diskguard_bytes, 1)
            log.check_disk_watermark(force=True)
            self.assertEqual(log.metrics["disk_guard_fires"], 1)
            log.close()

    def test_bad_env_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {ev.DISKGUARD_ENV: "bogus"}):
            log = self._log()  # must not refuse to start over a tunable
            self.assertEqual(log.diskguard_bytes,
                             ev.DISKGUARD_BYTES_DEFAULT)
            log.close()


if __name__ == "__main__":
    unittest.main()
