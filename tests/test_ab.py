"""Tests for the A/B variant harness (sentinel.ab) — lane L6.

Covers: variant spec loading/versioning, transforms + design-law validation,
K4 no-op gate, VariantClient recording, Wilson/McNemar math, cost accounting,
eval-set determinism, the paired dry-run end to end (real Gate + scripted
mock), and the K1/K3 live kill conditions. No network, no credentials.

stdlib-only: pure unittest, no pytest (frozen repo decision).
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import random
import tempfile
import unittest

from sentinel.ab import (
    RunAborted,
    Variant,
    VariantClient,
    VariantError,
    VariantNoOp,
    apply_variant,
    assert_variant_effective,
    build_evalset,
    cost_accounting,
    evalset_fingerprint,
    load_variant,
    mcnemar,
    paired_metrics,
    run_ab,
    variant_rng_seed,
    wilson,
    write_ab_report,
    write_manifest,
)
from sentinel.client import Answer, DecisionResponse, JevOverloaded, JevRateLimited
from sentinel.evalharness import FlipMock, script_response, state_key
from sentinel.questions import build_questions

VARIANTS_DIR = os.path.join(os.path.dirname(__file__), "..", "research",
                            "jev-behavior", "variants")


def _approx_eq(actual, expected, rel=1e-6, abs_tol=0.0):
    """unittest equivalent of `actual == pytest.approx(expected, ...)`.

    pytest.approx tolerance is max(rel * |expected|, abs).
    """
    return abs(actual - expected) <= max(rel * abs(expected), abs_tol)


class _TmpDirTestCase(unittest.TestCase):
    """unittest equivalent of pytest's tmp_path fixture."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = pathlib.Path(self._tmpdir.name)


def _vpath(stem: str) -> str:
    return os.path.join(VARIANTS_DIR, stem + ".json")


def _canned_response() -> DecisionResponse:
    return DecisionResponse(
        model="jev-test-1.0",
        answers={
            "severity": Answer(qid="severity", qtype="choice",
                               choice="p3_medium", noul=None,
                               probabilities={"p3_medium": 0.7,
                                              "p4_low": 0.3},
                               confidence=0.7),
            "owning_team": Answer(qid="owning_team", qtype="choice",
                                  choice="platform", noul=None,
                                  probabilities={"platform": 0.8,
                                                 "network": 0.2},
                                  confidence=0.8),
            "disposition": Answer(qid="disposition", qtype="choice",
                                  choice="page_business_hours", noul=None,
                                  probabilities={}, confidence=0.85),
        },
        input_tokens=900,
    )


class _CannedClient:
    def __init__(self, resp=None, exc=None):
        self.resp = resp or _canned_response()
        self.exc = exc
        self.seen: list[dict] = []

    def decide(self, state, questions):
        self.seen.append(questions)
        if self.exc is not None:
            raise self.exc
        return self.resp


# ---------------------------------------------------------------------------
# Variant specs
# ---------------------------------------------------------------------------

class TestVariantSpecs(_TmpDirTestCase):
    def test_load_variant_control(self):
        v = load_variant(_vpath("control-v1"))
        assert v.ref == "control@v1"
        assert v.transforms == ()
        # sha is stable across loads
        assert load_variant(_vpath("control-v1")).sha256 == v.sha256
        assert len(v.sha256) == 64

    def test_load_variant_sha_changes_on_tamper(self):
        src = _vpath("control-v1")
        import shutil
        dst = self.tmp_path / "control-v1.json"
        shutil.copy(src, dst)
        v1 = load_variant(str(dst))
        spec = json.loads(dst.read_text())
        spec["description"] = "tampered"
        dst.write_text(json.dumps(spec))
        v2 = load_variant(str(dst))
        assert v1.sha256 != v2.sha256

    def test_load_variant_rejects_bad_specs(self):
        def write(spec):
            p = self.tmp_path / "bad.json"
            p.write_text(json.dumps(spec))
            return str(p)

        base = {"id": "x", "version": "v1", "description": "d", "transforms": []}
        bad = dict(base); del bad["version"]
        with self.assertRaises(VariantError):
            load_variant(write(bad))
        bad = dict(base, transforms=[{"op": "nope", "qid": "severity"}])
        with self.assertRaises(VariantError):
            load_variant(write(bad))
        bad = dict(base, transforms=[{"op": "shuffle_options", "qid": "severity"}])
        with self.assertRaises(VariantError):  # seed missing
            load_variant(write(bad))
        bad = dict(base, transforms=[{"op": "reword_option", "qid": "severity",
                                              "option": "p1_critical",
                                              "description": "   "}])
        with self.assertRaises(VariantError):  # empty description
            load_variant(write(bad))

    def test_apply_variant_control_is_identity(self):
        q0 = build_questions()
        v = load_variant(_vpath("control-v1"))
        q1 = apply_variant(q0, v)
        assert q1 == q0
        assert q1 is not q0  # pure: no mutation of the input

    def test_apply_variant_shuffle_preserves_set_changes_order(self):
        v = load_variant(_vpath("q123-shuffle-v1"))
        q0 = build_questions()
        q1 = apply_variant(q0, v)
        changed = 0
        for qid in ("severity", "owning_team", "disposition"):
            before = list(q0[qid]["criteria"].keys())
            after = list(q1[qid]["criteria"].keys())
            assert set(after) == set(before)
            assert "cannot_determine" in after
            if after != before:
                changed += 1
        assert changed >= 1  # the seeded permutation is not the identity
        # deterministic across applications
        q2 = apply_variant(q0, v)
        assert q2 == q1

    def test_apply_variant_reword_option(self):
        v = load_variant(_vpath("q3-suppress-hedge-drop-v1"))
        q0 = build_questions()
        q1 = apply_variant(q0, v)
        assert "very high certainty" not in q1["disposition"]["criteria"]["suppress"]
        assert "very high certainty" in q0["disposition"]["criteria"]["suppress"]
        # everything else untouched
        assert q1["severity"] == q0["severity"]
        assert q1["disposition"]["criteria"]["page_now"] == \
            q0["disposition"]["criteria"]["page_now"]

    def test_apply_variant_validates_design_law(self):
        v = load_variant(_vpath("control-v1"))
        q0 = build_questions()
        q0["severity"]["criteria"] = {
            k: v_ for k, v_ in q0["severity"]["criteria"].items()
            if k != "cannot_determine"}
        with self.assertRaises(VariantError):
            apply_variant(q0, v)

    def test_apply_variant_rejects_unknown_option(self):
        v = Variant(id="x", version="v1", description="d",
                    transforms=({"op": "reword_option", "qid": "severity",
                                 "option": "nope", "description": "zzz"},),
                    sha256="00")
        with self.assertRaises(VariantError):
            apply_variant(build_questions(), v)

    def test_assert_variant_effective(self):
        a = load_variant(_vpath("control-v1"))
        b = load_variant(_vpath("q123-shuffle-v1"))
        info = assert_variant_effective(a, b)
        assert info["a_questions_sha"] != info["b_questions_sha"]
        with self.assertRaises(VariantNoOp):
            assert_variant_effective(a, a)

    def test_variant_rng_seed_stable(self):
        assert variant_rng_seed(7, "control@v1") == variant_rng_seed(7, "control@v1")
        assert variant_rng_seed(7, "control@v1") != variant_rng_seed(7, "q123-shuffle@v1")
        assert variant_rng_seed(7, "control@v1") != variant_rng_seed(8, "control@v1")


# ---------------------------------------------------------------------------
# VariantClient
# ---------------------------------------------------------------------------

class TestVariantClient(unittest.TestCase):
    def test_variant_client_applies_transform_and_records(self):
        v = load_variant(_vpath("q123-shuffle-v1"))
        inner = _CannedClient()
        vc = VariantClient(inner, v)
        resp = vc.decide({"a": 1}, build_questions())
        assert resp.model == "jev-test-1.0"
        seen = inner.seen[0]
        assert list(seen["disposition"]["criteria"].keys()) != \
            list(build_questions()["disposition"]["criteria"].keys())
        call = vc.calls[0]
        assert call["variant_ref"] == "q123-shuffle@v1"
        assert call["variant_sha"] == v.sha256
        assert call["latency_ms"] >= 0
        assert call["input_tokens"] == 900
        assert call["model"] == "jev-test-1.0"
        assert call["error"] is None

    def test_variant_client_records_errors_and_reraises(self):
        v = load_variant(_vpath("control-v1"))
        vc = VariantClient(_CannedClient(exc=JevOverloaded("boom")), v)
        with self.assertRaises(JevOverloaded):
            vc.decide({"a": 1}, build_questions())
        assert vc.calls[0]["error"] == "JevOverloaded"
        assert vc.calls[0]["input_tokens"] is None


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class TestStatistics(unittest.TestCase):
    def test_wilson_known_values(self):
        p, lo, hi = wilson(50, 100)
        assert _approx_eq(p, 0.5)
        assert _approx_eq(lo, 0.4038, abs_tol=1e-3)
        assert _approx_eq(hi, 0.5962, abs_tol=1e-3)
        p0, lo0, hi0 = wilson(0, 100)
        assert p0 == 0.0 and lo0 == 0.0
        assert _approx_eq(hi0, 0.0370, abs_tol=1e-3)
        assert wilson(0, 0) == (0.0, 0.0, 0.0)

    def test_mcnemar_known_values(self):
        # b=10, c=2 -> chi2 = (8-1)^2/12 = 4.0833, p ~= 0.0433
        r = mcnemar(10, 2)
        assert _approx_eq(r["chi2"], 4.0833, abs_tol=1e-3)
        assert _approx_eq(r["p_value"], 0.0433, abs_tol=1e-3)
        assert r["underpowered"] is False
        # underpowered: no p-value claim
        r2 = mcnemar(2, 1)
        assert r2["underpowered"] is True
        assert r2["p_value"] is None
        r3 = mcnemar(0, 0)
        assert r3["underpowered"] is True

    def test_cost_accounting(self):
        c = cost_accounting(1000.0)
        assert _approx_eq(c["usd_per_decision"], 0.000042)
        assert _approx_eq(c["usd_per_1k_decisions"], 0.042)
        assert _approx_eq(c["usd_per_1m_decisions"], 42.0)
        assert _approx_eq(c["vs_gpt6_sol_2878_per_1m"], 2878.0 / 42.0,
                          rel=1e-3)
        assert "DIFFERENT task" in c["reference"]
        assert cost_accounting(None)["input_tokens_mean"] is None


# ---------------------------------------------------------------------------
# Eval set
# ---------------------------------------------------------------------------

class TestEvalSet(unittest.TestCase):
    def test_build_evalset_deterministic(self):
        a = build_evalset(16, 7)
        b = build_evalset(16, 7)
        assert evalset_fingerprint(a) == evalset_fingerprint(b)
        c = build_evalset(16, 8)
        assert evalset_fingerprint(a) != evalset_fingerprint(c)
        assert len({it.state_sha256 for it in a}) == 16  # distinct states


# ---------------------------------------------------------------------------
# Paired dry-run end to end (real Gate + scripted mock)
# ---------------------------------------------------------------------------

def _dryrun_factory(items, seed):
    script = {}
    rng = random.Random(seed + 1)
    for it in items:
        script[state_key(it.state)] = script_response(
            it.alert, it.label, rng, label_noise=0.0)

    def factory(variant, arm_name):
        return FlipMock(script=script, flip_rate=0.02,
                        rng=random.Random(
                            variant_rng_seed(seed, arm_name + "|" + variant.ref)))

    return factory


class TestPairedDryRun(_TmpDirTestCase):
    def test_paired_run_dryrun_small(self):
        arms = [load_variant(_vpath(s)) for s in
                ("control-v1", "control-v1", "q123-shuffle-v1")]
        items = build_evalset(24, 7)
        results, meta = run_ab(items, arms, _dryrun_factory(items, 7), seed=7)
        assert len(results) == 24 * 3
        assert meta["aborted"] is None
        assert len(meta["evalset_sha"]) == 64
        assert [a["arm"] for a in meta["arms"]] == ["A1", "A2", "B"]
        # every row carries the arm name + variant sha
        for r in results:
            assert r.arm in ("A1", "A2", "B")
            assert len(r.variant_sha) == 64
        # A1 and A2 ran the same variant but are independent arms
        a1_refs = {r.variant_ref for r in results if r.arm == "A1"}
        a2_refs = {r.variant_ref for r in results if r.arm == "A2"}
        assert a1_refs == a2_refs == {"control@v1"}

        m = paired_metrics(results)
        assert m["n_paired"] == 24
        assert m["arms"]["a1_variant"] == "control@v1"
        assert m["arms"]["b_variant"] == "q123-shuffle@v1"
        # dry-run: the mock keys answers off state fingerprints, not question
        # text — any A1<->B flips are injected mock noise, not variant effects
        # (documented). The machinery must still run and be deterministic.
        assert 0.0 <= m["variant"]["disposition_flip_rate"] <= 0.25
        assert set(m["choice_flip_per_question"]) == \
            {"severity", "owning_team", "disposition"}
        for arm in ("A1", "B"):
            cov = m["coverage_at_tau_q3"][arm]
            assert set(cov) == {0.7, 0.8, 0.9}
        lat = m["latency_ms_per_arm"]["A1"]
        assert lat["n"] == 24 and lat["p50"] >= 0

        # determinism: the whole pipeline reproduces exactly
        results2, _ = run_ab(items, arms, _dryrun_factory(items, 7), seed=7)
        m2 = paired_metrics(results2)
        assert m2["noise_floor"]["disposition_flip_rate"] == \
            m["noise_floor"]["disposition_flip_rate"]
        assert m2["variant"]["disposition_flip_rate"] == \
            m["variant"]["disposition_flip_rate"]
        assert [r.disposition for r in results2] == [r.disposition for r in results]

    def test_report_and_manifest_roundtrip(self):
        arms = [load_variant(_vpath(s)) for s in
                ("control-v1", "control-v1", "q123-shuffle-v1")]
        items = build_evalset(12, 7)
        results, meta = run_ab(items, arms, _dryrun_factory(items, 7), seed=7)
        meta["n_alerts_requested"] = 12
        meta["seed"] = 7
        m = paired_metrics(results)
        cost = cost_accounting(m["input_tokens_per_arm"]["A1"]["mean"])
        rp, mp = str(self.tmp_path / "r.md"), str(self.tmp_path / "m.json")
        write_ab_report(m, meta, cost, rp,
                        {"date": "2026-10-04", "mode": "dry-run",
                         "conditions_text": "test"})
        write_manifest(results, meta, {"mode": "dry-run"}, mp)
        text = open(rp).read()
        assert "HONEST LIMITATIONS" in text
        assert "q123-shuffle@v1" in text
        man = json.loads(open(mp).read())
        assert len(man["rows"]) == 36
        assert all(len(r["variant_sha"]) == 64 for r in man["rows"])


# ---------------------------------------------------------------------------
# Kill conditions
# ---------------------------------------------------------------------------

class _ExplodingClient:
    def __init__(self, exc):
        self.exc = exc

    def decide(self, state, questions):
        raise self.exc


class TestKillConditions(_TmpDirTestCase):
    def test_k1_aborts_on_rolling_error_rate(self):
        arms = [load_variant(_vpath(s)) for s in
                ("control-v1", "control-v1", "q123-shuffle-v1")]
        items = build_evalset(60, 7)

        def factory(variant, arm_name):
            return _ExplodingClient(JevOverloaded("vendor down"))

        with self.assertRaises(RunAborted) as ctx:
            run_ab(items, arms, factory, seed=7)
        assert "K1" in str(ctx.exception)
        assert len(ctx.exception.partial) == 50  # aborted at the window edge

    def test_k3_aborts_on_first_429(self):
        arms = [load_variant(_vpath(s)) for s in
                ("control-v1", "control-v1", "q123-shuffle-v1")]
        items = build_evalset(10, 7)

        def factory(variant, arm_name):
            return _ExplodingClient(JevRateLimited("slow down", retry_after=0.01))

        with self.assertRaises(RunAborted) as ctx:
            run_ab(items, arms, factory, seed=7)
        assert "K3" in str(ctx.exception)
        assert len(ctx.exception.partial) == 1  # immediate hard stop

    def test_k2_aborts_on_auth_error(self):
        from sentinel.client import JevAuthError
        arms = [load_variant(_vpath(s)) for s in
                ("control-v1", "control-v1", "q123-shuffle-v1")]
        items = build_evalset(10, 7)

        def factory(variant, arm_name):
            return _ExplodingClient(JevAuthError("bad key"))

        with self.assertRaises(RunAborted) as ctx:
            run_ab(items, arms, factory, seed=7)
        assert "K2" in str(ctx.exception)

    def test_ab_report_guard_refuses_to_clobber_live_report(self):
        # R5 review: evalharness --ab is always dry-run; it must never silently
        # overwrite the committed live report with mock data.
        import argparse
        from sentinel import evalharness as eh
        live = self.tmp_path / "ab-live.md"
        live.write_text("live results")
        # was: monkeypatch.setattr(eh, "_AB_REPORT_DEFAULT", str(live))
        _orig = eh._AB_REPORT_DEFAULT
        eh._AB_REPORT_DEFAULT = str(live)
        try:
            with self.assertRaisesRegex(SystemExit, "refusing to overwrite"):
                eh._ab_report_path(argparse.Namespace(ab_report=str(live)))
        finally:
            eh._AB_REPORT_DEFAULT = _orig
        # An explicit non-default path is the operator's own choice: allowed.
        other = self.tmp_path / "dryrun.md"
        assert eh._ab_report_path(argparse.Namespace(ab_report=str(other))) == str(other)


if __name__ == "__main__":
    unittest.main()
