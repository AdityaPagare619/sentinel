"""Tests for sentinel.audit — the v0.1-compat facade over the event log.

The schema changed (ADR-011): decisions are decision_made EVENTS in the
hash-chained log, read back through the disposable `decisions` VIEW. The
suite's intent is unchanged: exact schema, append-only, thread-safe,
record/get roundtrip.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import json
import os
import sqlite3
import tempfile
import threading
import unittest

from sentinel.audit import AuditLog, body_of
from sentinel.client import Answer
from sentinel.models import DecisionRecord, Disposition

from tests.helpers import make_alert


def _record(audit, alert=None, action="page_now", reason="threshold"):
    alert = alert or make_alert()
    disp = Disposition(action=action, reason=reason, team="platform",
                       confidence=0.9, latency_ms=12.5)
    q1 = Answer(qid="severity", qtype="choice", choice="p1_critical", noul=None,
                probabilities={"p1_critical": 0.9, "p2_high": 0.1}, confidence=0.9)
    rec = DecisionRecord(alert=alert, input_sha256="abc123", jev_model="jev-1.13.0",
                         q_severity=q1, q_team=None, q_disposition=None,
                         disposition=disp)
    return audit.record(rec)


class TestSchema(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.audit = AuditLog(self.tmp.name)
        self.addCleanup(os.unlink, self.tmp.name)

    def test_tables_indexes_and_view_exist(self):
        conn = sqlite3.connect(self.tmp.name)
        tables = {r[0] for r in
                  conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("events", "outbox", "raw_payloads", "outcomes"):
            self.assertIn(t, tables, f"missing table {t}")
        views = {r[0] for r in
                 conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        self.assertIn("decisions", views)  # disposable compat shim (§6.4)
        indexes = {r[0] for r in
                   conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for i in ("idx_events_fp", "idx_events_alert", "idx_events_type",
                  "idx_outbox_episode_live", "idx_outbox_due"):
            self.assertIn(i, indexes, f"missing index {i}")
        conn.close()

    def test_events_envelope_columns_match_spec(self):
        conn = sqlite3.connect(self.tmp.name)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(events)")]
        self.assertEqual(cols, ["seq", "event_id", "schema_v", "ts", "actor",
                                "type", "alert_id", "fingerprint",
                                "episode_id", "outbox_id", "body",
                                "prev_hash", "row_hash"])
        conn.close()

    def test_outcomes_columns_match_spec(self):
        conn = sqlite3.connect(self.tmp.name)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(outcomes)")]
        self.assertEqual(cols, ["alert_id", "fingerprint", "became_sev12",
                                "auto_cleared", "mttr_min", "labeled_at"])
        conn.close()

    def test_append_only_no_update_delete_methods(self):
        for name in ("update", "delete", "remove", "purge", "clear"):
            self.assertFalse(hasattr(self.audit, name),
                             f"AuditLog must not have {name}()")


class TestRecordGet(unittest.TestCase):
    def setUp(self):
        self.audit = AuditLog(":memory:")

    def test_record_returns_seq_and_get_roundtrips(self):
        seq = _record(self.audit)
        self.assertEqual(seq, 1)
        row = self.audit.get(seq)
        self.assertEqual(row["alert_id"], "a1")
        self.assertEqual(row["type"], "decision_made")
        self.assertEqual(row["actor"], "engine")
        body = body_of(row)
        self.assertEqual(body["disposition"], "page_now")
        self.assertEqual(body["budget_outcome"], "answered_in_time")
        self.assertEqual(body["jev_model"], "jev-1.13.0")
        self.assertEqual(body["input_sha256"], "abc123")
        self.assertAlmostEqual(body["latency_ms"], 12.5)
        # v0.1 never recorded lock evidence — the shim says so, not invents.
        self.assertIn("pre-lock era", body["lock_evaluation"]["note"])
        # The chain columns are populated.
        self.assertEqual(row["prev_hash"], "GENESIS")
        self.assertEqual(len(row["row_hash"]), 64)

    def test_get_missing_row_raises_keyerror(self):
        with self.assertRaises(KeyError):
            self.audit.get(999)

    def test_null_answers_stored_as_null(self):
        alert = make_alert()
        disp = Disposition(action="passthrough", reason="error:timeout",
                           team=None, confidence=None, latency_ms=1.0)
        rec = DecisionRecord(alert=alert, input_sha256="x", jev_model=None,
                             q_severity=None, q_team=None, q_disposition=None,
                             disposition=disp)
        seq = self.audit.record(rec)
        body = body_of(self.audit.get(seq))
        self.assertIsNone(body["q2_team"])
        self.assertIsNone(body["jev_model"])
        self.assertEqual(body["budget_outcome"], "error_passthrough")

    def test_structural_passthrough_outcome(self):
        alert = make_alert(alert_id="s1")
        disp = Disposition(action="suppress", reason="allowlist", team=None,
                           confidence=0.95, latency_ms=0.5)
        rec = DecisionRecord(alert=alert, input_sha256="y", jev_model=None,
                             q_severity=None, q_team=None, q_disposition=None,
                             disposition=disp)
        seq2 = self.audit.record(rec)
        body = body_of(self.audit.get(seq2))
        self.assertEqual(body["budget_outcome"], "structural_passthrough")
        self.assertEqual(body["disposition"], "suppress")


class TestDecisionsForFingerprint(unittest.TestCase):
    def setUp(self):
        self.audit = AuditLog(":memory:")

    def test_newest_first_and_limit(self):
        fp = make_alert().fingerprint
        for i in range(5):
            _record(self.audit, alert=make_alert(alert_id=f"a{i}"),
                    action="page_now")
        _record(self.audit, alert=make_alert(alert_id="other", service="db"),
                action="suppress")
        rows = self.audit.decisions_for_fingerprint(fp)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["alert_id"], "a4")  # newest first
        rows = self.audit.decisions_for_fingerprint(fp, limit=2)
        self.assertEqual(len(rows), 2)

    def test_empty_for_unknown_fingerprint(self):
        self.assertEqual(self.audit.decisions_for_fingerprint("deadbeef"), [])


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_records(self):
        audit = AuditLog(":memory:")
        errors = []

        def worker(n):
            try:
                for i in range(20):
                    _record(audit, alert=make_alert(alert_id=f"w{n}-{i}"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        rows = audit.decisions_for_fingerprint(make_alert().fingerprint,
                                               limit=1000)
        self.assertEqual(len(rows), 160)
        # Chain intact after concurrent writes: seqs unique and ordered.
        seqs = sorted(r["id"] for r in rows)
        self.assertEqual(len(set(seqs)), 160)


if __name__ == "__main__":
    unittest.main()
