"""Tests for sentinel.evalharness — metrics on hand-built fixtures."""
import os
import tempfile
import unittest

from sentinel.evalharness import (
    accuracy,
    coverage_at,
    rank_fidelity,
    false_suppress_rate,
    run_batch,
    run_eval,
)
from sentinel.models import Alert
from sentinel.synthetic import generate_alerts
from tests.test_gate import fresh_monitor_for


def _monitor_for(pairs):
    """D1: fixtures that expect suppress must supply the freshness evidence
    production gives the live gate (a V1 bundle covering the allowlisted
    fingerprints)."""
    return fresh_monitor_for(
        [a.fingerprint for a, lab in pairs if lab.get("allowlist_candidate")])


def _mk_alert(i, fp, service, check, sev_in, title):
    return Alert(
        alert_id=f"t-{i}",
        received_at="2026-09-01T00:00:00+00:00",
        fingerprint=fp,
        service=service,
        check=check,
        severity_in=sev_in,
        title=title,
        source="alertmanager",
        labels={"env": "prod", "region": "us-east-1", "cluster": "us-east-1-a"},
        metric_value=90.0,
        metric_threshold=80.0,
        breach_duration_s=300,
        raw={"history_hint": {}},
    )


def _mk_label(sev_true, became, team, disp_true, allow=False):
    return {"severity_true": sev_true, "became_sev12": became,
            "team_true": team, "disposition_true": disp_true,
            "auto_cleared": 0, "allowlist_candidate": allow}


def _hand_fixture():
    pairs = []
    for i in range(2):  # known noise -> suppress (allowlisted)
        pairs.append((_mk_alert(i, f"noisefp{i:02d}", "redis-cache", "cpu_util",
                               "warning", "cpu spike self-cleared"),
                      _mk_label("known_noise", 0, "data", "suppress", allow=True)))
    for i in range(2, 4):  # p3 -> queue
        pairs.append((_mk_alert(i, f"p3fp{i:02d}", "checkout-api", "p99_latency",
                               "warning", "latency warning"),
                      _mk_label("p3_medium", 0, "product_backend",
                                "page_business_hours")))
    for i in range(4, 6):  # p1 -> page_now
        pairs.append((_mk_alert(i, f"p1fp{i:02d}", "postgres-primary", "error_rate",
                               "critical", "error rate critical"),
                      _mk_label("p1_critical", 1, "data", "page_now")))
    return pairs


class TestEvalMetrics(unittest.TestCase):
    def test_rank_fidelity_hand_computed(self):
        # Correct answers rank above incorrect ones: all 2x2 pairs concordant.
        auc, n = rank_fidelity([(0.9, True), (0.8, True), (0.3, False), (0.2, False)])
        self.assertAlmostEqual(auc, 1.0, places=9)
        self.assertEqual(n, 4)

    def test_rank_fidelity_chance(self):
        # Interleaved ranks: 2 of 4 pairs concordant -> 0.5.
        auc, _ = rank_fidelity([(0.9, True), (0.2, True), (0.8, False), (0.3, False)])
        self.assertAlmostEqual(auc, 0.5, places=9)

    def test_rank_fidelity_unscorable(self):
        # No negatives (or no positives) -> None, never a fabricated number.
        auc, n = rank_fidelity([(0.9, True), (0.8, True)])
        self.assertIsNone(auc)
        self.assertEqual(n, 2)

    def test_coverage_at(self):
        cov = coverage_at([0.9, 0.8, 0.6], taus=(0.7, 0.8, 0.9))
        self.assertAlmostEqual(cov[0.7], 2 / 3)
        self.assertAlmostEqual(cov[0.8], 2 / 3)
        self.assertAlmostEqual(cov[0.9], 1 / 3)


class TestEvalBatch(unittest.TestCase):
    def test_clean_fixture_perfect_scores(self):
        pairs = _hand_fixture()
        rows = run_batch(pairs, seed=11, flip_rate=0.0, label_noise=0.0,
                         freshness_monitor=_monitor_for(pairs))
        acc = accuracy(rows)
        self.assertEqual(acc["n"], 6)
        self.assertEqual(acc["severity"], 1.0)
        self.assertEqual(acc["team"], 1.0)
        self.assertEqual(acc["disposition"], 1.0)
        actions = [r["disposition"].action for r in rows]
        self.assertEqual(actions,
                         ["suppress", "suppress",
                          "page_business_hours", "page_business_hours",
                          "page_now", "page_now"])

    def test_false_suppress_zero_on_clean(self):
        rows = run_batch(_hand_fixture(), seed=11, flip_rate=0.0, label_noise=0.0)
        fs = false_suppress_rate(rows)
        self.assertEqual(fs["n_sev1"], 2)
        self.assertEqual(fs["n_false_suppress"], 0)
        self.assertEqual(fs["rate"], 0.0)

    def test_audit_rows_recorded(self):
        rows = run_batch(_hand_fixture(), seed=11)
        for r in rows:
            rec = r["record"]
            self.assertIsNotNone(rec.input_sha256)
            self.assertEqual(len(rec.input_sha256), 64)
            self.assertIsNotNone(rec.q_severity)
            self.assertIsNotNone(rec.q_disposition)


class TestEvalReport(unittest.TestCase):
    def test_report_written_with_limitations(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "judgment-fidelity-report.md")
            m = run_eval(n=60, seed=5, flip_rate=0.0, label_noise=0.0,
                         out_path=path, flip_n=20, flip_repeats=5, shuffle_n=20)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        self.assertIn("HONEST LIMITATIONS", text)
        self.assertIn("proves plumbing, not production accuracy", text)
        self.assertIn("50–300 customer labels", text)
        self.assertIn("rank AUC", text)
        # No ECE-as-honesty presentation: no ECE metric lines, no ECE bins.
        self.assertNotIn("## Calibration (ECE", text)
        self.assertNotIn("severity ECE:", text)
        # Metrics dict carries everything the report claims.
        self.assertEqual(m["n"], 60)
        self.assertIn("accuracy", m)
        self.assertIn("rank_auc_q1", m)
        self.assertIn("coverage_q1", m)
        self.assertIn("false_suppress", m)
        self.assertIn("flip_probe", m)
        self.assertIn("shuffle_probe", m)
        self.assertTrue(m["flip_probe"]["pass"])     # no flips injected
        self.assertTrue(m["shuffle_probe"]["pass"])  # mock is order-insensitive

    def test_synthetic_end_to_end_sane(self):
        # A real generator run: SEVs exist, none suppressed, noise suppressed.
        pairs = generate_alerts(400, seed=21)
        rows = run_batch(pairs, seed=21, flip_rate=0.0, label_noise=0.0,
                         freshness_monitor=_monitor_for(pairs))
        fs = false_suppress_rate(rows)
        self.assertGreater(fs["n_sev1"], 0)
        self.assertEqual(fs["rate"], 0.0)
        suppressed = [r for r in rows if r["disposition"].action == "suppress"]
        self.assertGreater(len(suppressed), 0)
        for r in suppressed:
            self.assertEqual(r["label"]["severity_true"], "known_noise")


if __name__ == "__main__":
    unittest.main()
