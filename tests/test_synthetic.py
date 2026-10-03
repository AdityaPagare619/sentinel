"""Tests for sentinel.synthetic — determinism, mixture sanity, scripted answers."""
import json
import random
import unittest
from dataclasses import asdict

from sentinel.models import Alert
from sentinel.synthetic import (
    SEVERITY_OPTIONS,
    fingerprint_of,
    generate_alerts,
    script_answers,
)


def _canon(pairs):
    return json.dumps(
        [(asdict(a), lab) for a, lab in pairs], sort_keys=True, default=str)


class TestSynthetic(unittest.TestCase):
    def test_determinism_same_seed(self):
        a = generate_alerts(150, seed=42)
        b = generate_alerts(150, seed=42)
        self.assertEqual(_canon(a), _canon(b))

    def test_different_seed_differs(self):
        a = generate_alerts(150, seed=42)
        b = generate_alerts(150, seed=43)
        self.assertNotEqual(_canon(a), _canon(b))

    def test_mixture_proportions(self):
        pairs = generate_alerts(5000, seed=1)
        became = sum(lab["became_sev12"] for _, lab in pairs) / len(pairs)
        # 5% real SEVs (all became) + 20% deploy-adjacent x 5% became ~= 6%
        self.assertGreaterEqual(became, 0.04)
        self.assertLessEqual(became, 0.08)

    def test_noise_never_becomes_sev(self):
        pairs = generate_alerts(3000, seed=2)
        for _, lab in pairs:
            if lab["severity_true"] == "known_noise":
                self.assertEqual(lab["became_sev12"], 0)
                self.assertEqual(lab["auto_cleared"], 1)
                self.assertEqual(lab["disposition_true"], "suppress")
                self.assertTrue(lab["allowlist_candidate"])

    def test_alert_fields(self):
        pairs = generate_alerts(50, seed=3)
        for alert, lab in pairs:
            self.assertIsInstance(alert, Alert)
            self.assertEqual(len(alert.fingerprint), 16)
            int(alert.fingerprint, 16)  # valid hex
            self.assertIn("history_hint", alert.raw)
            self.assertIn(lab["severity_true"], SEVERITY_OPTIONS)
            self.assertIn(lab["became_sev12"], (0, 1))
            self.assertEqual(
                alert.fingerprint,
                fingerprint_of(alert.service, alert.check,
                               alert.severity_in, alert.labels["region"]))

    def test_script_answers_clean(self):
        rng = random.Random(9)
        pairs = generate_alerts(60, seed=4)
        for alert, lab in pairs:
            a = script_answers(alert, lab, rng, label_noise=0.0)
            self.assertAlmostEqual(sum(a["q1_probs"].values()), 1.0, places=9)
            self.assertEqual(a["q1_choice"], lab["severity_true"])
            self.assertEqual(a["q3_choice"], lab["disposition_true"])
            self.assertGreaterEqual(a["q3_conf"], 0.80)

    def test_script_answers_noise_suppressible(self):
        # The triple lock must be reachable for known_noise on clean scripts.
        rng = random.Random(10)
        pairs = generate_alerts(400, seed=5)
        noise = [(a, l) for a, l in pairs if l["severity_true"] == "known_noise"]
        self.assertTrue(noise)
        for alert, lab in noise:
            ans = script_answers(alert, lab, rng, label_noise=0.0)
            self.assertLess(ans["q1_probs"]["p1_critical"], 0.002)
            self.assertGreaterEqual(ans["q3_conf"], 0.90)

    def test_no_fingerprint_collision_with_noise_pool(self):
        # Regression: a non-noise alert must never share a fingerprint with a
        # known-noise flap, or it could join the suppression allowlist and be
        # wrongly suppressed. (Caught live: a p3 on a shared triple suppressed.)
        pairs = generate_alerts(5000, seed=77)
        noise_fps = {a.fingerprint for a, lab in pairs
                     if lab["severity_true"] == "known_noise"}
        for alert, lab in pairs:
            if lab["severity_true"] != "known_noise":
                self.assertNotIn(alert.fingerprint, noise_fps)

    def test_label_noise_flips_argmax(self):
        rng = random.Random(11)
        pairs = generate_alerts(200, seed=6)
        flipped = 0
        for alert, lab in pairs:
            a = script_answers(alert, lab, rng, label_noise=0.5)
            if a["q1_choice"] != lab["severity_true"]:
                flipped += 1
        # ~50% should flip; assert a wide band so the test isn't flaky.
        self.assertGreater(flipped, 50)
        self.assertLess(flipped, 150)


if __name__ == "__main__":
    unittest.main()
