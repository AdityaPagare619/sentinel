"""ASR gate for the instruction firewall (ADR-020 / D6).

THE gate: fails the build if ANY adversarial corpus case bypasses the screen
(attack-success-rate > 0) or ANY benign case is flagged (false-positive
rate > 0). This is the ratification condition (b) — the corpus is the
falsifier, and a red test here is a successful outcome: it caught the screen
lying.

Corpus: tests/corpus/adversarial_corpus.json, owned by Vault, red-teamed by
Tripwire quarterly. See tests/corpus/README.md.
"""

import json
import os
import re
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import unittest

from sentinel import firewall
from sentinel.firewall import CORPUS_VERSION, DETECTORS, screen

from tests.helpers import make_alert

_CORPUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus")
_MANIFEST_PATH = os.path.join(_CORPUS_DIR, "manifest.json")
_CORPUS_PATH = os.path.join(_CORPUS_DIR, "adversarial_corpus.json")

_ID_RE = re.compile(r"^(IP|FR|DS|UL|C|B)-\d{3}$")
_CATEGORY_OF = {"IP": "instruction_phrase", "FR": "fake_resolve",
                "DS": "delimiter_smuggling", "UL": "unicode_lookalike",
                "C": "composite", "B": "benign"}


def load_corpus():
    with open(_MANIFEST_PATH, encoding="utf-8") as f:
        manifest = json.load(f)
    with open(_CORPUS_PATH, encoding="utf-8") as f:
        corpus = json.load(f)
    return manifest, corpus


def alert_for_case(case):
    field, text = case["field"], case["text"]
    if field == "title":
        return make_alert(title=text)
    if field == "check":
        return make_alert(check=text)
    if field == "service":
        return make_alert(service=text)
    if field.startswith("label:"):
        return make_alert(labels={field.split(":", 1)[1]: text})
    if field.startswith("raw:"):
        return make_alert(raw={field.split(":", 1)[1]: text})
    raise ValueError(f"unknown corpus field: {field!r}")


class TestCorpusIntegrity(unittest.TestCase):
    """The corpus itself is versioned config: validate it before trusting it."""

    def test_manifest_version_matches_module(self):
        manifest, _ = load_corpus()
        self.assertEqual(
            manifest["version"], CORPUS_VERSION,
            "corpus manifest version != firewall.CORPUS_VERSION: "
            "stale corpus or stale code — bump both in one commit")

    def test_corpus_version_matches_manifest(self):
        manifest, corpus = load_corpus()
        self.assertEqual(corpus["version"], manifest["version"])

    def test_ownership_declared(self):
        manifest, _ = load_corpus()
        self.assertEqual(manifest["owner"], "Vault")
        self.assertEqual(manifest["red_team"], "Tripwire")

    def test_case_schema_and_ids(self):
        _, corpus = load_corpus()
        seen = set()
        for case in corpus["cases"]:
            for key in ("id", "category", "field", "text",
                        "expect_flagged", "expect_detectors"):
                self.assertIn(key, case, f"case missing {key}: {case.get('id')}")
            self.assertRegex(case["id"], _ID_RE)
            self.assertNotIn(case["id"], seen, f"duplicate id {case['id']}")
            seen.add(case["id"])
            prefix = case["id"].split("-")[0]
            self.assertEqual(case["category"], _CATEGORY_OF[prefix],
                             f"id/category mismatch: {case['id']}")
            for d in case["expect_detectors"]:
                self.assertIn(d, DETECTORS)
            if not case["expect_flagged"]:
                self.assertEqual(case["expect_detectors"], [],
                                 f"benign case {case['id']} names detectors")
            # The loader must understand every field the corpus uses.
            alert_for_case(case)

    def test_corpus_has_minimum_size(self):
        _, corpus = load_corpus()
        attacks = [c for c in corpus["cases"] if c["expect_flagged"]]
        benign = [c for c in corpus["cases"] if not c["expect_flagged"]]
        self.assertGreaterEqual(len(attacks), 50,
                                "attack corpus shrank below the 50-case floor")
        self.assertGreaterEqual(len(benign), 20,
                                "benign corpus shrank below the 20-case floor")
        cats = {c["category"] for c in attacks}
        self.assertEqual(cats, {"instruction_phrase", "fake_resolve",
                                "delimiter_smuggling", "unicode_lookalike",
                                "composite"})


class TestAttackSuccessRateGate(unittest.TestCase):
    """ASR must be 0: every attack case is flagged, with an expected detector."""

    def test_no_attack_bypasses(self):
        _, corpus = load_corpus()
        bypasses = []
        wrong_detector = []
        for case in corpus["cases"]:
            if not case["expect_flagged"]:
                continue
            verdict = screen(alert_for_case(case))
            if not verdict.flagged:
                bypasses.append(case["id"])
            elif not any(d in verdict.detectors
                         for d in case["expect_detectors"]):
                wrong_detector.append(
                    (case["id"], case["expect_detectors"],
                     list(verdict.detectors)))
        self.assertEqual(
            bypasses, [],
            f"ATTACK BYPASSED THE SCREEN (ASR>0): {bypasses} — "
            f"fix the detector, not the case")
        self.assertEqual(
            wrong_detector, [],
            f"flagged by unexpected detector only: {wrong_detector}")


class TestFalsePositiveRateGate(unittest.TestCase):
    """FPR must be 0: no benign case is flagged. A false-positive page at
    3 AM destroys operator trust in the firewall — this gate is the contract."""

    def test_no_benign_case_flags(self):
        _, corpus = load_corpus()
        fps = []
        for case in corpus["cases"]:
            if case["expect_flagged"]:
                continue
            verdict = screen(alert_for_case(case))
            if verdict.flagged:
                fps.append((case["id"], list(verdict.detectors),
                            verdict.evidence))
        self.assertEqual(
            fps, [],
            f"FALSE POSITIVE (FPR>0): {fps} — "
            f"the detector is too aggressive; narrow it, don't delete the case")


if __name__ == "__main__":
    unittest.main()
