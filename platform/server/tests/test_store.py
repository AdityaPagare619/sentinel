"""Tests for platform.server.store — the read-only event-log projection."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.dirname(_HERE)                      # platform/server
_REPO = os.path.dirname(os.path.dirname(_SERVER))     # repo root
sys.path.insert(0, _SERVER)  # _pkg
sys.path.insert(0, _HERE)  # fixtures
sys.path.insert(0, os.path.join(_REPO, "src"))  # sentinel.*
import _pkg

from fixtures import make_decision, make_store_db

ReadStore = _pkg.load("store").ReadStore


class TestRiver(unittest.TestCase):
    def setUp(self):
        self.db = make_store_db()
        self.addCleanup(os.unlink, self.db)
        self.store = ReadStore(self.db)

    def test_river_newest_first(self):
        items = self.store.decisions(limit=5)
        ids = [i["id"] for i in items]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual(len(items), 5)

    def test_since_id_cursor(self):
        first = self.store.decisions(limit=3)
        newest = first[0]["id"]
        later = self.store.decisions(since_id=newest)
        self.assertEqual(later, [])
        older = self.store.decisions(limit=3, since_id=first[-1]["id"] - 1)
        self.assertTrue(all(i["id"] > first[-1]["id"] - 1 for i in older))

    def test_limit_bound_enforced_by_caller(self):
        items = self.store.decisions(limit=500)
        self.assertLessEqual(len(items), 500)

    def test_filter_fingerprint(self):
        one = self.store.decisions(limit=1)[0]
        items = self.store.decisions(fingerprint=one["fingerprint"])
        self.assertTrue(items)
        self.assertTrue(all(i["fingerprint"] == one["fingerprint"]
                            for i in items))

    def test_filter_team(self):
        items = self.store.decisions(team="data", limit=500)
        self.assertTrue(items)
        self.assertTrue(all(i["team"] == "data" for i in items))

    def test_filter_action(self):
        items = self.store.decisions(action="suppress", limit=500)
        self.assertTrue(items)
        self.assertTrue(all(i["disposition"] == "suppress" for i in items))

    def test_filter_time_range(self):
        items = self.store.decisions(since="2026-10-01T00:00:00Z",
                                     until="2026-10-05T00:00:00Z", limit=500)
        self.assertTrue(items)
        empty = self.store.decisions(since="2030-01-01T00:00:00Z")
        self.assertEqual(empty, [])

    def test_summary_shape(self):
        item = self.store.decisions(limit=1)[0]
        required = {"id", "time", "title", "service", "severity", "team",
                    "disposition", "confidence", "reason", "prob_map",
                    "fingerprint", "input_sha256", "jev_model", "latency_ms"}
        self.assertTrue(required <= set(item),
                        f"missing: {required - set(item)}")
        pm = item["prob_map"]
        for q in ("severity", "owning_team", "disposition"):
            triple = pm[q]
            self.assertIn("choice", triple)
            self.assertIn("confidence", triple)
            self.assertIn("probs", triple)
            total = sum(triple["probs"].values())
            self.assertAlmostEqual(total, 1.0, places=6,
                                   msg=f"{q} probs sum to {total}")

    def test_q1_probs_are_the_recorded_map(self):
        items = self.store.decisions(action="page_now", limit=1)
        probs = items[0]["prob_map"]["severity"]["probs"]
        self.assertAlmostEqual(probs["p1_critical"], 0.90, places=6)

    def test_shadow_flag(self):
        items = self.store.decisions(action="passthrough", limit=500)
        shadows = [i for i in items if i["reason"] == "shadow"]
        self.assertTrue(shadows)
        self.assertTrue(all(i["shadow"] for i in shadows))
        nons = [i for i in items if i["reason"] != "shadow"]
        self.assertTrue(all(not i["shadow"] for i in nons))

    def test_has_older(self):
        items = self.store.decisions(limit=2)
        oldest = min(i["id"] for i in items)
        self.assertTrue(self.store.has_older(oldest))
        self.assertFalse(self.store.has_older(1))


class TestDetail(unittest.TestCase):
    def setUp(self):
        self.db = make_store_db()
        self.addCleanup(os.unlink, self.db)
        self.store = ReadStore(self.db)

    def test_detail_shape(self):
        item = self.store.decisions(limit=1)[0]
        detail = self.store.decision(item["id"])
        self.assertIn("alert", detail)
        self.assertIn("audit", detail)
        self.assertIn("outcome", detail)
        self.assertIn("received_at", detail["audit"])

    def test_detail_outcome_join(self):
        # alt-0001 has an outcome row (odd ids labeled).
        rows = self.store.decisions(limit=500)
        one = next(i for i in rows if i["alert_id"] == "alt-0001")
        detail = self.store.decision(one["id"])
        self.assertIsNotNone(detail["outcome"])
        self.assertIn("became_sev12", detail["outcome"])

    def test_detail_missing(self):
        self.assertIsNone(self.store.decision(10 ** 9))


class TestCalibration(unittest.TestCase):
    def setUp(self):
        self.db = make_store_db()
        self.addCleanup(os.unlink, self.db)
        self.store = ReadStore(self.db)

    def test_report_shape_and_denominators(self):
        rep = self.store.calibration()
        for key in ("team", "window", "dataset_version", "n_decisions",
                    "n_labeled", "bins", "ece", "coverage"):
            self.assertIn(key, rep)
        self.assertEqual(len(rep["bins"]), 10)
        self.assertEqual(rep["team"], "all")
        self.assertGreater(rep["n_labeled"], 0)
        self.assertLessEqual(rep["n_labeled"], rep["n_decisions"])
        self.assertGreaterEqual(rep["ece"], 0.0)
        self.assertLessEqual(rep["ece"], 1.0)
        self.assertEqual(set(rep["coverage"]), {"0.7", "0.8", "0.9"})
        lo, hi = rep["ece_ci95"]
        self.assertLessEqual(lo, rep["ece"] + 1e-9)
        self.assertGreaterEqual(hi, rep["ece"] - 1e-9)

    def test_team_scope(self):
        rep = self.store.calibration(team="data")
        self.assertEqual(rep["team"], "data")
        self.assertGreater(rep["n_labeled"], 0)

    def test_empty_db(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        conn = sqlite3.connect(tmp.name)
        conn.execute("CREATE TABLE t(x)")
        conn.commit()
        conn.close()
        rep = ReadStore(tmp.name).calibration()
        self.assertEqual(rep["n_labeled"], 0)
        self.assertEqual(rep["ece"], 0.0)


class TestNoise(unittest.TestCase):
    def setUp(self):
        self.db = make_store_db()
        self.addCleanup(os.unlink, self.db)
        self.store = ReadStore(self.db)

    def test_noise_shape(self):
        rep = self.store.noise(86400)
        self.assertIn("top_checks", rep)
        self.assertIn("team_load", rep)
        self.assertIn("suppression_breakdown", rep)
        self.assertTrue(rep["top_checks"])
        check = rep["top_checks"][0]
        for key in ("fingerprint", "service", "check", "region",
                    "volume", "suppressed"):
            self.assertIn(key, check)
        self.assertIn("suppress_rate", check)
        for t in rep["team_load"]:
            self.assertIn("pages_per_night_before", t)
            self.assertIn("pages_per_night_after", t)
        reasons = {b["reason"] for b in rep["suppression_breakdown"]}
        self.assertIn("triple-lock", reasons)

    def test_window_excludes_old(self):
        # An event from 2020 must not appear in any recent window.
        _repo = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        sys.path.insert(0, os.path.join(_repo, "src"))
        from sentinel.eventlog import EventLog
        log = EventLog(self.db)
        try:
            log.append_event(
                "decision_made", actor="engine", alert_id="alt-old",
                fingerprint="0" * 16, episode_id="ep-old",
                body={"disposition": "suppress",
                      "budget_outcome": "answered_in_time",
                      "lock_evaluation": {}, "freshness": {},
                      "threshold_counterfactual": {}, "links": {},
                      "input_sha256": "old",
                      "v01_compat": {"reason": "triple-lock"}},
                ts="2020-01-01T00:00:00.000Z")
        finally:
            log.close()
        rep = self.store.noise(86400)
        fps = {c["fingerprint"] for c in rep["top_checks"]}
        self.assertNotIn("0" * 16, fps)
        # ...but it IS in the river (no window there).
        river = self.store.decisions(limit=500)
        self.assertIn("0" * 16, {i["fingerprint"] for i in river})


class TestFlips(unittest.TestCase):
    def test_repeats_and_flip_flag(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        from sentinel.audit import AuditLog
        audit = AuditLog(tmp.name)
        # Same input twice, different disposition -> flip.
        make_decision(audit, alert_id="alt-f1", action="page_now",
                      reason="p1p2-mass", sev="p1_critical", confidence=0.9,
                      input_sha256="same-input")
        make_decision(audit, alert_id="alt-f2", action="suppress",
                      reason="triple-lock", sev="known_noise", confidence=0.96,
                      input_sha256="same-input")
        # Same input twice, identical -> no flip.
        make_decision(audit, alert_id="alt-g1", action="page_now",
                      reason="p1p2-mass", sev="p1_critical", confidence=0.9,
                      input_sha256="stable-input")
        make_decision(audit, alert_id="alt-g2", action="page_now",
                      reason="p1p2-mass", sev="p1_critical", confidence=0.9,
                      input_sha256="stable-input")
        audit.close()
        store = ReadStore(tmp.name)
        stats = store.flip_stats()
        self.assertEqual(stats["repeats_total"], 2)
        self.assertEqual(stats["flips_n"], 1)
        self.assertAlmostEqual(stats["flip_rate"], 0.5)
        records = store.flip_records(30 * 86400)
        by_input = {r["input_sha256"]: r for r in records}
        self.assertTrue(by_input["same-input"]["flipped"])
        self.assertFalse(by_input["stable-input"]["flipped"])
        self.assertEqual(by_input["same-input"]["repeats"], 2)
        # flips sort first
        self.assertTrue(records[0]["flipped"])

    def test_no_repeats(self):
        store = ReadStore(make_store_db())
        stats = store.flip_stats()
        self.assertEqual(stats["repeats_total"], 0)
        self.assertEqual(stats["flip_rate"], 0.0)


class TestContextFile(unittest.TestCase):
    def test_context_resolves_titles(self):
        from _pkg import load as _load
        load_context_jsonl = _load("store").load_context_jsonl
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl",
                                         delete=False) as fh:
            fh.write(json.dumps({
                "alert_id": "alt-0001", "title": "cpu_util warning on web",
                "service": "web", "check": "cpu_util",
                "severity_in": "warning", "region": "us-east",
                "labels": {"region": "us-east"}}) + "\n")
            fh.write("not json\n")
            fh.write(json.dumps({"no_alert_id": True}) + "\n")
            path = fh.name
        self.addCleanup(os.unlink, path)
        ctx = load_context_jsonl(path)
        self.assertEqual(set(ctx), {"alt-0001"})
        self.assertEqual(load_context_jsonl("/tmp/nope-missing.jsonl"), {})

        db = make_store_db()
        self.addCleanup(os.unlink, db)
        store = ReadStore(db, context=ctx)
        rows = store.decisions(limit=500)
        one = next(i for i in rows if i["alert_id"] == "alt-0001")
        self.assertEqual(one["title"], "cpu_util warning on web")
        self.assertEqual(one["service"], "web")
        other = next(i for i in rows if i["alert_id"] == "alt-0002")
        self.assertEqual(other["service"], "unknown")  # honest fallback


class TestMissingDb(unittest.TestCase):
    def test_empty_state_no_crash(self):
        store = ReadStore("/tmp/definitely-not-here-sentinel.db")
        self.assertFalse(store.available)
        self.assertEqual(store.decisions(), [])
        self.assertIsNone(store.decision(1))
        self.assertEqual(store.tail(0), [])
        self.assertEqual(store.head_id(), 0)
        self.assertEqual(store.calibration()["n_labeled"], 0)


if __name__ == "__main__":
    unittest.main()
