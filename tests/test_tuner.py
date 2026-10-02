"""Tests for sentinel.tuner — hand-verified expected-cost math.

Fixture (C_FP=100, C_FN=50000):

  fp "aa" x6 : known-noise-like. p1=0.0009, P(known_noise)=0.93,
               conf in [0.95, 0.95, 0.92, 0.88, 0.82, 0.81],
               became_sev12=0, baseline=1
  fp "bb" x6 : p3-like. p1=0.004, P(known_noise)=0.05, conf=0.90,
               became_sev12=0, baseline=1
  fp "cc" x2 : real SEV. p1=0.90, P(known_noise)=0.0, conf=0.95,
               became_sev12=1, baseline=1

Hand computation:
  p* = 100 / (100 + 50000) = 100/50100 ~= 0.001996008

  Allowlist (min_samples=5, min_noise_prob=0.6):
    aa: n=6, sev12=0, mean P(noise)=0.93 -> IN
    bb: n=6, sev12=0, mean P(noise)=0.05 -> OUT
    cc: n=2 < 5                          -> OUT
    => allowlist = {"aa"}

  Classification per conf_min (suppress needs p1 < p* AND conf >= min AND allowlist):
    aa rows: p1=0.0009 < p* always; suppressed iff conf >= conf_min
      0.80 -> 6 suppressed | exp_false = 6*0.0009 = 0.0054
                          | exp_cost  = 0.0054*50000 = 270.0 | avoided = 600
      0.85 -> 4 suppressed | exp_false = 0.0036 | cost 180.0 | avoided 400
      0.90 -> 3 suppressed | exp_false = 0.0027 | cost 135.0 | avoided 300
      0.95 -> 2 suppressed | exp_false = 0.0018 | cost  90.0 | avoided 200
    bb rows: p1=0.004 > p* -> never suppressed; p1+p2=0.084 <= 0.30;
             p3+p4=0.87 dominant, conf 0.90 >= 0.70 -> queue (6)
    cc rows: p1+p2=0.95 > 0.30 -> page_now (2)
  aa rows that miss the conf bar are NOT dropped: p34=0.06 >= p12=0.0049
  and conf >= 0.70 -> they fall through to queue. So queue counts per
  conf_min: 0.80 -> 6, 0.85 -> 8, 0.90 -> 9, 0.95 -> 10.

  baseline_pages = 14.
  Recommendation: min expected cost -> conf_min=0.95 (cost 90.0).
"""
import json
import os
import tempfile
import unittest

from sentinel.tuner import (
    candidate_allowlist,
    classify,
    expected_cost_threshold,
    fingerprint_stats,
    format_projection,
    thresholds_json,
    tune,
)

C_FP, C_FN = 100.0, 50000.0
P_STAR = C_FP / (C_FP + C_FN)  # 0.001996007984031936...


def _row(fp, p1, p_noise, conf, became, baseline,
         p2=0.08, p3=0.80, p4=0.07):
    return {
        "fingerprint": fp,
        "q1_probs": {"p1_critical": p1, "p2_high": p2, "p3_medium": p3,
                     "p4_low": p4, "known_noise": p_noise,
                     "cannot_determine": 0.006},
        "q3_confidence": conf,
        "became_sev12": became,
        "would_page_baseline": baseline,
    }


def _fixture():
    rows = []
    for conf in (0.95, 0.95, 0.92, 0.88, 0.82, 0.81):
        rows.append(_row("aa", 0.0009, 0.93, conf, 0, 1,
                         p2=0.004, p3=0.02, p4=0.04))
    for _ in range(6):
        rows.append(_row("bb", 0.004, 0.05, 0.90, 0, 1))
    for _ in range(2):
        rows.append(_row("cc", 0.90, 0.0, 0.95, 1, 1, p2=0.05, p3=0.03, p4=0.01))
    return rows


class TestTunerMath(unittest.TestCase):
    def test_expected_cost_threshold_formula(self):
        # The core formula, verified by hand: p* = C_FP/(C_FP+C_FN).
        self.assertEqual(expected_cost_threshold(100.0, 50000.0), 100.0 / 50100.0)
        self.assertAlmostEqual(expected_cost_threshold(100.0, 50000.0),
                               0.001996007984031936)

    def test_allowlist_heuristic(self):
        stats = fingerprint_stats(_fixture())
        self.assertEqual(stats["aa"]["n"], 6)
        self.assertEqual(stats["aa"]["sev12"], 0)
        self.assertAlmostEqual(stats["aa"]["mean_noise_p"], 0.93)
        self.assertEqual(candidate_allowlist(stats), {"aa"})

    def test_classify_buckets(self):
        rows = _fixture()
        allow = {"aa"}
        # conf_min=0.90: aa rows with conf>=0.90 suppress (3), bb queue (6),
        # cc page_now (2).
        buckets = [classify(r, P_STAR, 0.90, allow) for r in rows]
        self.assertEqual(buckets.count("suppress"), 3)
        self.assertEqual(buckets.count("queue"), 9)  # 6 bb + 3 aa below the bar
        self.assertEqual(buckets.count("page_now"), 2)
        self.assertEqual(buckets.count("baseline"), 0)
        # p1 above p* is never suppressed even in the allowlist.
        bb_row = dict(rows[6]); bb_row["fingerprint"] = "aa"
        self.assertEqual(classify(bb_row, P_STAR, 0.80, allow), "queue")

    def test_tune_grid_hand_computed(self):
        res = tune(_fixture(), c_fp=C_FP, c_fn=C_FN)
        self.assertEqual(res["suppress_p1_max"], P_STAR)  # never below optimum
        self.assertEqual(res["baseline_pages"], 14)
        self.assertEqual(res["allowlist_size"], 1)
        expect = {
            0.80: (6, 0.0054, 270.0, 600.0, 6),
            0.85: (4, 0.0036, 180.0, 400.0, 8),
            0.90: (3, 0.0027, 135.0, 300.0, 9),
            0.95: (2, 0.0018, 90.0, 200.0, 10),
        }
        for g in res["grid"]:
            n_sup, exp_false, exp_cost, avoided, n_queue = expect[g["conf_min"]]
            self.assertEqual(g["suppress"], n_sup)
            self.assertAlmostEqual(g["exp_false_suppresses"], exp_false, places=9)
            # expected cost = expected false suppresses x C_FN (hand-checked)
            self.assertAlmostEqual(g["expected_cost"], exp_cost, places=6)
            self.assertAlmostEqual(g["expected_cost"],
                                   g["exp_false_suppresses"] * C_FN, places=6)
            # avoided page cost = suppressed x C_FP (hand-checked)
            self.assertAlmostEqual(g["avoided_page_cost"], avoided, places=6)
            self.assertAlmostEqual(g["avoided_page_cost"],
                                   g["suppress"] * C_FP, places=6)
            self.assertEqual(g["page_now"], 2)
            self.assertEqual(g["queue"], n_queue)

    def test_recommendation_is_cost_optimum(self):
        res = tune(_fixture(), c_fp=C_FP, c_fn=C_FN)
        # Min expected cost on the grid is 90.0 at conf_min=0.95.
        self.assertEqual(res["recommended_conf_min"], 0.95)
        self.assertAlmostEqual(res["recommended"]["expected_cost"], 90.0, places=6)

    def test_thresholds_json_roundtrip(self):
        res = tune(_fixture(), c_fp=C_FP, c_fn=C_FN)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "thresholds.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(thresholds_json(res), fh, indent=2)
            with open(path, encoding="utf-8") as fh:
                back = json.load(fh)
        self.assertEqual(back["suppress_p1_max"], P_STAR)
        self.assertEqual(back["suppress_conf_min"], 0.95)
        self.assertEqual(back["page_p1p2_min"], 0.30)

    def test_projection_prints_real_numbers(self):
        res = tune(_fixture(), c_fp=C_FP, c_fn=C_FN)
        text = format_projection(res)
        self.assertIn("0.0020", text)          # p* printed
        self.assertIn("100/50100", text)       # formula shown
        self.assertIn("14", text)              # baseline pages
        self.assertIn("recommended", text.lower())


if __name__ == "__main__":
    unittest.main()
