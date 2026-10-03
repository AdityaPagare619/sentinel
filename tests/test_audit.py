"""Tests for sentinel.audit (§3.8): exact schema, append-only, thread-safe."""

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

from sentinel.audit import AuditLog
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

    def test_tables_and_index_exist(self):
        conn = sqlite3.connect(self.tmp.name)
        tables = {r[0] for r in
                  conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("decisions", tables)
        self.assertIn("outcomes", tables)
        indexes = {r[0] for r in
                   conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("idx_fp", indexes)
        conn.close()

    def test_decisions_columns_match_spec(self):
        conn = sqlite3.connect(self.tmp.name)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(decisions)")]
        expected = ["id", "received_at", "alert_id", "fingerprint", "input_sha256",
                    "jev_model", "q1_severity", "q1_probs", "q1_conf",
                    "q2_team", "q2_probs", "q2_conf",
                    "q3_disposition", "q3_probs", "q3_conf",
                    "action", "reason", "latency_ms", "created_at"]
        self.assertEqual(cols, expected)
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

    def test_record_returns_row_id_and_get_roundtrips(self):
        row_id = _record(self.audit)
        self.assertEqual(row_id, 1)
        row = self.audit.get(row_id)
        self.assertEqual(row["action"], "page_now")
        self.assertEqual(row["reason"], "threshold")
        self.assertEqual(row["q1_severity"], "p1_critical")
        self.assertEqual(json.loads(row["q1_probs"]),
                         {"p1_critical": 0.9, "p2_high": 0.1})
        self.assertAlmostEqual(row["q1_conf"], 0.9)
        self.assertEqual(row["jev_model"], "jev-1.13.0")
        self.assertEqual(row["input_sha256"], "abc123")
        self.assertIsNotNone(row["created_at"])

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
        row_id = self.audit.record(rec)
        row = self.audit.get(row_id)
        self.assertIsNone(row["q1_severity"])
        self.assertIsNone(row["q1_probs"])
        self.assertIsNone(row["jev_model"])


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


if __name__ == "__main__":
    unittest.main()
