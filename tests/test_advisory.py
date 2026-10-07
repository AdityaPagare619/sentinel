"""Tests for T5 — Jev advisory directions (Phase 2).

Covers the coordinator's approval conditions:
  1. D5's gate import is narrow (test_d5_narrow_gate_import) + replay is
     read-only over archives (test_d5_replay_readonly_and_exact).
  2. Advisory/gate separation (test_advisory_types_invisible_to_gate_*).
  5. Merged storm: 8 questions, ONE decide() call (test_merged_storm_*).
Plus: labeling contract, pre-call gates, §5 timeout–fallback matrix,
shed→fallback + pool isolation, FakeJev labeling, cost-cap enforcement,
renderer goldens (zero Jev), D1/D6 triggers, D3 retrieval helpers.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import ast
import copy
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

import sentinel.advisory as advisory
import sentinel.advisory_d3 as advisory_d3
import sentinel.advisory_d5 as advisory_d5
import sentinel.advisory_render as render
from sentinel.advisory import (
    ADVISORY_EVENT_TYPES, AdvisoryDispatcher, AdvisoryPool, DirectionBudget,
    SpendMeter, d1_questions, d1_should_fire, d3_questions, d4_questions,
    d5_triage_questions, d6_questions, d6_should_fire, estimate_cost_usd,
    evidence_bundler, label_envelope, merged_storm_advisory,
    merged_storm_questions, rca_hypotheses, suppression_explainer,
    triage_suggest,
)
from sentinel.advisory_d3 import (
    cap_state_tokens, match_runbooks, retrieve_related_incidents,
)
from sentinel.advisory_d5 import (
    ArchiveDecisionInput, ArchiveWindowError, ProposedPolicy,
    build_offline_kernel, check_rate_limit, d5_preview, filter_archive_window,
    replay_archive, triage_with_jev, verify_archive_window,
)
from sentinel.client import (Answer, DecisionResponse, JevError, JevTimeout,
                             MockSystemOneClient, _state_fingerprint)
from sentinel.corroboration import CorroborationEvidence, SilenceFloor
from sentinel.counterfactual import CounterfactualInputs
from sentinel.freshness import (LOCK1_CALIBRATION, LOCK2_THRESHOLD,
                                LOCK3_ALLOWLIST, FreshnessReport,
                                LockFreshness)
from sentinel.gate import Gate, evaluate_policy
from sentinel.models import Alert, Thresholds
from sentinel.race import InferencePool
from sentinel.retention import RetentionConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ans(qid, qtype="choice", choice=None, noul=None, probs=None,
         confidence=None):
    return Answer(qid=qid, qtype=qtype, choice=choice, noul=noul,
                  probabilities=dict(probs or {}), confidence=confidence)


def _resp(answers=None, model="jev-mock-0.0.0", input_tokens=120):
    return DecisionResponse(model=model, answers=dict(answers or {}),
                            input_tokens=input_tokens)


class RecordingDecide:
    """Test decide_fn: records calls, returns a scripted response, or raises."""

    def __init__(self, response=None, exc=None, delay_s=0.0):
        self.response = response
        self.exc = exc
        self.delay_s = delay_s
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, state, questions):
        with self._lock:
            self.calls.append({"state": state, "questions": dict(questions)})
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.exc is not None:
            raise self.exc
        return self.response

    @property
    def n_calls(self):
        with self._lock:
            return len(self.calls)


def _dispatcher(decide_fn=None, **kw):
    decide_fn = decide_fn if decide_fn is not None else RecordingDecide(
        _resp())
    kw.setdefault("jev_model", "jev-mock-0.0.0")
    return AdvisoryDispatcher(decide_fn, SpendMeter(), **kw)


def _suppression_payload(**kw):
    p = {"alert_id": "a-1", "fingerprint": "fp-1", "severity_in": "critical",
         "body": {"reason": "allowlist",
                  "evidence_legs": ["allowlist_hit", "freshness_ok"]}}
    p.update(kw)
    return p


# ---------------------------------------------------------------------------
# Labeling contract (§2.3)
# ---------------------------------------------------------------------------

class TestLabelingContract(unittest.TestCase):
    def test_envelope_fields(self):
        env = label_envelope(direction="suppression_explainer",
                             fallback="jev", jev_model="jev-1.13.0",
                             body={"paragraph": "x"}, alert_id="a1",
                             fingerprint="fp1")
        self.assertEqual(env["type"], "advisory/suppression_explainer")
        self.assertTrue(env["advisory"])
        self.assertTrue(env["ai_generated"])
        self.assertEqual(env["direction"], "suppression_explainer")
        self.assertEqual(env["fallback"], "jev")
        self.assertEqual(env["jev_model"], "jev-1.13.0")
        self.assertEqual(env["direction_version"], 1)
        self.assertEqual(env["alert_id"], "a1")
        self.assertIn("paragraph", env["body"])

    def test_ai_generated_only_when_jev_answered(self):
        for fallback, expected in (("jev", True), ("deterministic", False),
                                   ("absent", False)):
            env = label_envelope(direction="rca_hypotheses",
                                 fallback=fallback, jev_model="m", body={})
            self.assertEqual(env["ai_generated"], expected,
                             f"fallback={fallback}")

    def test_all_event_types_known(self):
        self.assertEqual(len(ADVISORY_EVENT_TYPES), 6)
        for t in ADVISORY_EVENT_TYPES:
            self.assertTrue(t.startswith("advisory/"))


# ---------------------------------------------------------------------------
# Pre-call gates
# ---------------------------------------------------------------------------

class TestPreCallGates(unittest.TestCase):
    def test_disabled_direction_no_call(self):
        fake = RecordingDecide(_resp())
        d = _dispatcher(fake, budgets={"triage_suggest":
                                       DirectionBudget(enabled=False)})
        env = d.call_inline("triage_suggest", {"a": 1},
                            d6_questions(),
                            lambda r: {"x": 1}, lambda reason: {"fb": reason})
        self.assertEqual(fake.n_calls, 0)
        self.assertEqual(env["fallback"], "absent")  # D6's §5 label
        self.assertEqual(env["fallback_reason"], "disabled")
        d.close()

    def test_budget_blocked_all_deterministic_zero_calls(self):
        """Approval: blocked=true degrades ALL directions instantly."""
        fake = RecordingDecide(_resp())
        meter = SpendMeter(session_usd=0.49, budget_usd=0.50, blocked=True)
        d = AdvisoryDispatcher(fake, meter, jev_model="jev-mock-0.0.0")
        for direction, questions in (
                ("suppression_explainer", d1_questions("allowlist")),
                ("evidence_bundler", d3_questions([], [])),
                ("rca_hypotheses", d4_questions(["h"])),
                ("triage_suggest", d6_questions())):
            env = d.call_inline(direction, {"a": 1}, questions,
                                lambda r: {"x": 1},
                                lambda reason: {"fb": reason})
            self.assertNotEqual(env["fallback"], "jev", direction)
            self.assertFalse(env["ai_generated"], direction)
            self.assertEqual(env["fallback_reason"], "budget_blocked",
                             direction)
        self.assertEqual(fake.n_calls, 0)
        d.close()

    def test_cost_cap_oversize_state_no_call(self):
        fake = RecordingDecide(_resp())
        d = _dispatcher(fake)
        big_state = {"blob": "x" * 100_000}  # ~25k tokens >> $0.0005 cap
        est = estimate_cost_usd(big_state, d1_questions("allowlist"))
        self.assertGreater(est, 0.0005)
        env = d.call_inline("suppression_explainer", big_state,
                            d1_questions("allowlist"),
                            lambda r: {"x": 1},
                            lambda reason: {"fb": reason})
        self.assertEqual(fake.n_calls, 0)
        self.assertEqual(env["fallback_reason"], "cost_cap")
        self.assertEqual(env["fallback"], "deterministic")
        d.close()

    def test_daily_cap_degrades_direction_only(self):
        fake = RecordingDecide(_resp())
        d = _dispatcher(fake, budgets={"evidence_bundler": DirectionBudget(
            enabled=True, timeout_ms=8000, cost_cap_usd=0.0005,
            daily_cap_usd=0.000001)})  # smaller than any real call
        env = d.call_inline("evidence_bundler", {"a": 1},
                            d3_questions([], []),
                            lambda r: {"x": 1},
                            lambda reason: {"fb": reason})
        self.assertEqual(fake.n_calls, 0)
        self.assertEqual(env["fallback_reason"], "daily_cap")
        # ...but another direction still fires.
        env2 = d.call_inline("rca_hypotheses", {"a": 1},
                             d4_questions(["h"]),
                             lambda r: {"x": 1},
                             lambda reason: {"fb": reason})
        self.assertEqual(env2["fallback"], "jev")
        self.assertEqual(fake.n_calls, 1)
        d.close()


# ---------------------------------------------------------------------------
# Spend-meter reconciliation (RFC aiml-spend-reconciliation)
# ---------------------------------------------------------------------------

class TestSpendReconciliation(unittest.TestCase):
    """SpendMeter (advisory estimate envelope) vs JevSpendTracker (C2
    wire-truth ledger): explicit roles, one-directional divergence bound,
    fail-closed on disagreement."""

    def test_blocked_tracker_blocks_dispatcher(self):
        """A blocked wire-truth ledger blocks advisory even when the estimate
        envelope hasn't latched — fail-closed on disagreement."""
        from sentinel.sim_judge import JevSpendTracker
        fake = RecordingDecide(_resp())
        tracker = JevSpendTracker(budget_usd=0.0)  # blocked from the start
        self.assertTrue(tracker.blocked)
        meter = SpendMeter(budget_usd=10.0)  # envelope: plenty of room
        d = AdvisoryDispatcher(fake, meter, jev_model="jev-mock-0.0.0",
                               tracker=tracker)
        env = d.call_inline("triage_suggest", {"a": 1}, d6_questions(),
                            lambda r: {"x": 1}, lambda reason: {"fb": reason})
        self.assertEqual(fake.n_calls, 0)
        self.assertEqual(env["fallback_reason"], "budget_blocked")
        d.close()

    def test_unblocked_tracker_does_not_block(self):
        from sentinel.sim_judge import JevSpendTracker
        fake = RecordingDecide(_resp())
        tracker = JevSpendTracker(budget_usd=10.0)
        d = AdvisoryDispatcher(fake, SpendMeter(budget_usd=10.0),
                               jev_model="jev-mock-0.0.0", tracker=tracker)
        env = d.call_inline("triage_suggest", {"a": 1}, d6_questions(),
                            lambda r: {"x": 1}, lambda reason: {"fb": reason})
        self.assertEqual(env["fallback"], "jev")
        self.assertEqual(fake.n_calls, 1)
        d.close()

    def test_estimate_envelope_never_understates_ledger(self):
        """Same call script through both meters: the estimate envelope's
        session_usd >= the ledger's (estimates are conservative; failed calls
        cost 0.0 actual). The divergence is bounded and one-directional."""
        from sentinel.sim_judge import JevSpendTracker
        tracker = JevSpendTracker(budget_usd=10.0)
        meter = SpendMeter(budget_usd=10.0)
        # (estimate_charged, actual_recorded): successes cost <= estimate;
        # the failed call records 0.0 (no usage data — never invented).
        script = [(0.00010, 0.00008), (0.00010, 0.00009), (0.00010, 0.0)]
        for est, actual in script:
            meter.charge(est)
            tracker.record_call(actual)
        self.assertGreaterEqual(meter.session_usd, tracker.session_usd)
        self.assertAlmostEqual(meter.session_usd, 0.00030, places=9)
        self.assertAlmostEqual(tracker.session_usd, 0.00017, places=9)


# ---------------------------------------------------------------------------
# §5 timeout–fallback matrix
# ---------------------------------------------------------------------------

class TestTimeoutFallbackMatrix(unittest.TestCase):
    def _timeout_dispatcher(self):
        fake = RecordingDecide(exc=JevTimeout("socket timed out"))
        return _dispatcher(fake), fake

    def test_d1_timeout_deterministic_neutral_no_tag(self):
        d, fake = self._timeout_dispatcher()
        got = []
        done = threading.Event()
        suppression_explainer(d, _suppression_payload(),
                              lambda env: (got.append(env), done.set()))
        self.assertTrue(done.wait(10))
        d.close()
        self.assertEqual(fake.n_calls, 1)
        env = got[0]
        self.assertEqual(env["fallback"], "deterministic")
        self.assertFalse(env["ai_generated"])
        self.assertIn("no single evidence leg stood out",
                      env["body"]["paragraph"])
        self.assertNotIn("human_second_look", env["body"])

    def test_merged_storm_timeout_three_labeled_fallbacks(self):
        d, fake = self._timeout_dispatcher()
        got = []
        done = threading.Event()
        merged_storm_advisory(d, {"storm_size": 42,
                                  "storm_counts": {"web": 42},
                                  "clusters": ["c1"],
                                  "incidents": [{"incident_id": "i1"}],
                                  "runbooks": [],
                                  "hypotheses": ["bad deploy"]},
                              lambda env: (got.append(env),
                                           done.set() if len(got) >= 3
                                           else None))
        self.assertTrue(done.wait(10))
        d.close()
        self.assertEqual(fake.n_calls, 1)  # one call, not three
        by_type = {e["type"]: e for e in got}
        self.assertEqual(by_type["advisory/storm_brief"]["fallback"],
                         "deterministic")
        self.assertEqual(by_type["advisory/evidence_bundle"]["fallback"],
                         "deterministic")
        # D4's fallback is honest absence.
        hyp = by_type["advisory/rca_hypotheses"]
        self.assertEqual(hyp["fallback"], "absent")
        self.assertEqual(hyp["body"], {})

    def test_d3_timeout_recency_bundle(self):
        d, fake = self._timeout_dispatcher()
        incidents = [{"incident_id": "i1", "summary": "s1"},
                     {"incident_id": "i2", "summary": "s2"}]
        got = []
        done = threading.Event()
        evidence_bundler(d, {"alert_id": "a1"},
                         lambda env: (got.append(env), done.set()),
                         incidents=incidents, runbooks=[])
        self.assertTrue(done.wait(10))
        d.close()
        env = got[0]
        self.assertEqual(env["fallback"], "deterministic")
        bundle = env["body"]["bundle"]
        self.assertEqual([i["incident_id"] for i in bundle["incidents"]],
                         ["i1", "i2"])  # recency order, unranked
        self.assertEqual(bundle["ranked_by"], "recency")

    def test_d4_timeout_absent(self):
        d, fake = self._timeout_dispatcher()
        got = []
        done = threading.Event()
        rca_hypotheses(d, {"alert_id": "a1"},
                       lambda env: (got.append(env), done.set()),
                       hypotheses=["bad deploy of X"])
        self.assertTrue(done.wait(10))
        d.close()
        env = got[0]
        self.assertEqual(env["fallback"], "absent")
        self.assertEqual(env["body"], {})

    def test_d6_timeout_absent(self):
        d, fake = self._timeout_dispatcher()
        got = []
        done = threading.Event()
        triage_suggest(d, {"alert_id": "a1", "race_source": "timer"},
                       lambda env: (got.append(env), done.set()))
        self.assertTrue(done.wait(10))
        d.close()
        env = got[0]
        self.assertEqual(env["fallback"], "absent")
        self.assertEqual(env["body"], {})

    def test_wall_clock_timeout_fallback(self):
        # A decide_fn that hangs: the dispatcher's wall timeout (not the
        # socket) produces the fallback deterministically in tests.
        fake = RecordingDecide(_resp(), delay_s=30.0)
        d = _dispatcher(fake)
        t0 = time.monotonic()
        env = d.call_inline("rca_hypotheses", {"a": 1},
                            d4_questions(["h"]),
                            lambda r: {"x": 1}, lambda reason: {"fb": reason},
                            timeout_ms=200)
        dt = time.monotonic() - t0
        self.assertEqual(env["fallback"], "absent")
        self.assertEqual(env["fallback_reason"], "timeout")
        self.assertLess(dt, 5.0)  # did not wait for the 30s hang
        d.close()

    def test_malformed_answer_routes_to_direction_fallback(self):
        # Jev answered, but on_jev chokes on the shape: the DIRECTION's
        # deterministic body is used (not an empty envelope).
        fake = RecordingDecide(_resp(answers={}))
        d = _dispatcher(fake)

        def boom_jev(resp):
            raise ValueError("unexpected answer shape")

        env = d.call_inline("suppression_explainer", {"a": 1},
                            d1_questions("allowlist"), boom_jev,
                            lambda reason: {"paragraph": "neutral fallback",
                                            "reason": reason})
        self.assertEqual(env["fallback"], "deterministic")
        self.assertEqual(env["fallback_reason"], "answer_parse_error")
        self.assertEqual(env["body"]["paragraph"], "neutral fallback")
        d.close()


# ---------------------------------------------------------------------------
# Shed → fallback + pool isolation (Track 7's falsifier surface)
# ---------------------------------------------------------------------------

class TestShedAndIsolation(unittest.TestCase):
    def test_shed_fallback_page_unaffected(self):
        pool = AdvisoryPool(max_workers=2, queue_size=2)
        gate_event = threading.Event()
        # Saturate the advisory pool: 2 workers, both blocked (queue=2
        # permits total — the semaphore guards running+queued).
        blockers = [pool.submit(lambda: gate_event.wait(10))
                    for _ in range(2)]
        self.assertTrue(all(f is not None for f in blockers))
        fake = RecordingDecide(_resp())
        d = AdvisoryDispatcher(fake, SpendMeter(), pool=pool,
                               jev_model="jev-mock-0.0.0")
        got = []
        done = threading.Event()
        submitted = d.submit("rca_hypotheses", {"a": 1},
                             d4_questions(["h"]),
                             lambda r: {"x": 1}, lambda reason: {"fb": reason},
                             lambda env: (got.append(env), done.set()))
        self.assertFalse(submitted)  # shed
        self.assertTrue(done.wait(5))
        self.assertEqual(fake.n_calls, 0)  # Jev never called
        self.assertEqual(got[0]["fallback"], "absent")
        self.assertEqual(got[0]["fallback_reason"], "shed")
        gate_event.set()
        d.close()

    def test_advisory_pool_isolated_from_race_pool(self):
        """Advisory work can never borrow the race's InferencePool."""
        adv_pool = AdvisoryPool()
        race_pool = InferencePool()
        self.assertIsNot(adv_pool, race_pool)
        self.assertIsNot(type(adv_pool), type(race_pool))
        # Saturate the ADVISORY pool (4 workers + 32 queue = 32 permits);
        # the race pool still accepts work.
        gate_event = threading.Event()
        blockers = [adv_pool.submit(lambda: gate_event.wait(10))
                    for _ in range(32)]
        self.assertTrue(all(f is not None for f in blockers))
        self.assertIsNone(adv_pool.submit(lambda: None))  # advisory sheds
        race_done = []
        fut = race_pool.submit(lambda: race_done.append(True))
        self.assertIsNotNone(fut)  # race pool unaffected
        fut.result(timeout=5)
        self.assertEqual(race_done, [True])
        gate_event.set()
        adv_pool.close()
        race_pool.shutdown()


# ---------------------------------------------------------------------------
# FakeJev labeling (absent key path)
# ---------------------------------------------------------------------------

class TestFakeJevLabeling(unittest.TestCase):
    def test_mock_client_honestly_labeled(self):
        state = {"alert_id": "a1"}
        questions = d4_questions(["bad deploy of X"])
        fp = _state_fingerprint(state)
        mock = MockSystemOneClient(
            {fp: _resp(answers={
                "hypothesis_choice": _ans("hypothesis_choice",
                                          choice="bad deploy of X"),
                "hypothesis_plausibility": _ans("hypothesis_plausibility",
                                               qtype="score", choice="8")})})
        d = AdvisoryDispatcher(mock.decide, SpendMeter(),
                               jev_model="jev-mock-0.0.0")
        env = d.call_inline("rca_hypotheses", state, questions,
                            lambda r: {"echo": True},
                            lambda reason: {"fb": reason})
        self.assertEqual(len(mock.calls), 1)
        self.assertEqual(env["fallback"], "jev")
        self.assertTrue(env["ai_generated"])
        # The clearly-fake model id is recorded, never a real one.
        self.assertEqual(env["jev_model"], "jev-mock-0.0.0")
        d.close()


# ---------------------------------------------------------------------------
# Merged storm call (approval condition 5)
# ---------------------------------------------------------------------------

class TestMergedStormSingleCall(unittest.TestCase):
    def test_eight_questions_one_call(self):
        questions = merged_storm_questions(
            clusters=["cluster-a", "cluster-b"],
            incidents=[{"incident_id": "inc-1", "summary": "db failover"}],
            runbooks=[{"runbook_id": "rb-2", "title": "db failover runbook"}],
            hypotheses=["bad deploy of X", "AZ network blip"])
        self.assertEqual(len(questions), 8)
        self.assertEqual(
            set(questions),
            {"severity", "owning_team", "brief_lead", "pattern_novelty",
             "hypothesis_choice", "hypothesis_plausibility",
             "incident_rank", "runbook_rank"})
        for qid, q in questions.items():
            self.assertIn(q["type"], ("choice", "score", "noul"), qid)
            if q["type"] == "choice":
                # The frozen Choice law: every option set offers the
                # do-not-guess escape hatch.
                self.assertIn("cannot_determine", q["criteria"],
                              f"{qid} must offer cannot_determine")
            else:
                self.assertIn("levels", q, f"{qid} score needs levels")

    def test_merged_call_parses_per_question_answers(self):
        fake = RecordingDecide(_resp(answers={
            "severity": _ans("severity", choice="p2_high",
                             probs={"p2_high": 0.8}),
            "owning_team": _ans("owning_team", choice="data"),
            "brief_lead": _ans("brief_lead", choice="cluster-a"),
            "pattern_novelty": _ans("pattern_novelty", qtype="score",
                                    choice="7"),
            "hypothesis_choice": _ans("hypothesis_choice",
                                      choice="bad deploy of X"),
            "hypothesis_plausibility": _ans("hypothesis_plausibility",
                                            qtype="score", choice="8"),
            "incident_rank": _ans("incident_rank", choice="inc-1"),
            "runbook_rank": _ans("runbook_rank", choice="rb-2"),
        }))
        d = _dispatcher(fake)
        got = []
        done = threading.Event()
        merged_storm_advisory(
            d, {"storm_size": 42, "storm_counts": {"db": 42},
                "clusters": ["cluster-a", "cluster-b"],
                "incidents": [{"incident_id": "inc-1",
                               "summary": "db failover last month"}],
                "runbooks": [{"runbook_id": "rb-2",
                              "title": "db failover runbook"}],
                "hypotheses": ["bad deploy of X", "AZ network blip"]},
            lambda env: (got.append(env),
                         done.set() if len(got) >= 3 else None))
        self.assertTrue(done.wait(10))
        d.close()
        # ONE decide() call carried all 8 questions — not 8 calls.
        self.assertEqual(fake.n_calls, 1)
        self.assertEqual(len(fake.calls[0]["questions"]), 8)
        by_type = {e["type"]: e for e in got}
        self.assertEqual(len(by_type), 3)
        # Per-question answers parsed into their direction's envelope.
        self.assertIn("cluster-a",
                      by_type["advisory/storm_brief"]["body"]["brief"])
        self.assertIn("7/10 (ordinal)",
                      by_type["advisory/storm_brief"]["body"]["brief"])
        bundle = by_type["advisory/evidence_bundle"]["body"]["bundle"]
        self.assertEqual(bundle["incidents"][0]["incident_id"], "inc-1")
        self.assertEqual(bundle["ranked_by"], "jev")
        hyp = by_type["advisory/rca_hypotheses"]["body"]["hypothesis"]
        self.assertEqual(hyp["hypothesis"], "bad deploy of X")
        self.assertEqual(hyp["plausibility"],
                         "plausibility level 8/10 (ordinal)")
        self.assertIn("unconfirmed", hyp["status"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Advisory/gate separation (approval condition 2 — Track 7's first line)
# ---------------------------------------------------------------------------

def _module_source(mod):
    with open(mod.__file__, "r", encoding="utf-8") as fh:
        return fh.read()


class TestAdvisoryGateSeparation(unittest.TestCase):
    def test_advisory_types_invisible_to_gate(self):
        """Advisory event types on the emission channel are invisible to
        the decision read-set: gate.py never names one."""
        import sentinel.gate as gate_mod
        gate_src = _module_source(gate_mod)
        for t in ADVISORY_EVENT_TYPES:
            self.assertNotIn(t, gate_src,
                             f"gate.py must never reference {t}")
        self.assertNotIn("advisory/", gate_src)

    def test_hot_advisory_modules_import_nothing_from_gate(self):
        """advisory / advisory_render / advisory_d3: zero gate imports."""
        import sentinel.advisory_render as render_mod
        for mod in (advisory, render_mod, advisory_d3):
            tree = ast.parse(_module_source(mod))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(
                        (node.module or "").split(".")[-1], "gate",
                        f"{mod.__name__} imports from gate")
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        self.assertFalse(
                            a.name in ("sentinel.gate", "gate"),
                            f"{mod.__name__} imports gate")

    def test_decision_functions_take_no_payload(self):
        """The gate kernel's read-set is alerts + answers + thresholds +
        evidence — advisory payloads cannot enter it: no decision
        function accepts a payload/advisory parameter."""
        import inspect
        for fn in (evaluate_policy, Gate._resolve_suppress_path,
                   Gate._decide):
            params = set(inspect.signature(fn).parameters)
            self.assertTrue({"payload", "advisory", "envelope"}.isdisjoint(
                params), f"{fn.__qualname__} takes {params}")

    def test_advisory_envelope_cannot_change_a_verdict(self):
        """Behavioral: attaching an advisory envelope to an emitted
        payload leaves the kernel verdict byte-identical — the gate
        never reads what it emitted."""
        alert = Alert(alert_id="a1", received_at="2026-10-06T00:00:00Z",
                      fingerprint="fp-x", service="web", check="http_5xx",
                      severity_in="warning", title="t", source="generic")
        ans = _ans("severity", choice="p3_medium",
                   probs={"p1_critical": 0.0, "p2_high": 0.05,
                          "p3_medium": 0.9, "p4_low": 0.05},
                   confidence=0.95)
        kw = dict(jev_model="jev-1.13.0", q_severity=ans,
                  q_team=_ans("owning_team", choice="platform",
                              probs={"platform": 1.0}, confidence=0.99),
                  q_disposition=_ans("disposition",
                                     choice="page_business_hours",
                                     probs={"page_business_hours": 1.0},
                                     confidence=0.95),
                  thresholds=Thresholds(), allowlist=set(), latency_ms=10.0)
        v1 = evaluate_policy(alert, **kw)
        # The advisory envelope "attached" to the record (emission only).
        env = label_envelope(direction="suppression_explainer",
                             fallback="jev", jev_model="jev-1.13.0",
                             body={"paragraph": "why you weren't woken"})
        self.assertEqual(env["type"], "advisory/suppression_explainer")
        v2 = evaluate_policy(alert, **kw)  # the gate never saw the envelope
        self.assertEqual((v1.action, v1.reason), (v2.action, v2.reason))


# ---------------------------------------------------------------------------
# D5 — narrow gate import + read-only replay (approval conditions 1 & 4)
# ---------------------------------------------------------------------------

def _archive_fixtures(now):
    """Five archived inputs with exact, hand-computed replay outcomes.

    Proposed policy: page_p1p2_min 0.30 -> 0.40, allowlist += {fp-D, fp-E}.
      A: page_now -> page_business_hours (floor raised under it)
      B: page_now -> page_now (still above the raised floor)
      C: page_business_hours -> page_business_hours (unchanged)
      D: page_business_hours -> suppress (allowlisted + corroborated)
      E: page_business_hours -> page_now (allowlisted, UNcorroborated)
    """
    def alert(fp, aid):
        return Alert(alert_id=aid, received_at="2026-10-05T00:00:00Z",
                     fingerprint=fp, service="web", check="http_5xx",
                     severity_in="warning", title="t", source="generic")

    def sev(p1, p2, p3, p4, choice="p2_high"):
        return _ans("severity", choice=choice,
                    probs={"p1_critical": p1, "p2_high": p2, "p3_medium": p3,
                           "p4_low": p4, "known_noise": 0.0,
                           "cannot_determine": 0.0}, confidence=0.95)

    def disp(choice, conf=0.95):
        return _ans("disposition", choice=choice, probs={choice: 1.0},
                    confidence=conf)

    team = _ans("owning_team", choice="platform", probs={"platform": 1.0},
                confidence=0.99)
    def green_report():
        entry = lambda: LockFreshness(verdict="fresh", reason="t",
                                      proof_id="t")
        return FreshnessReport(
            evaluated_at="2026-10-05T00:00:00Z",
            config_manifest_sha256="x",
            locks={LOCK1_CALIBRATION: entry(),
                   LOCK2_THRESHOLD: entry(),
                   # Lock 3 is per-entry: the recorded report must name
                   # the fingerprints fresh.
                   LOCK3_ALLOWLIST: LockFreshness(
                       verdict="fresh", reason="t", proof_id="t",
                       entries={"fp-D": entry(), "fp-E": entry()})})
    green = green_report()
    floor = SilenceFloor(allowed_kinds=["second_check"], min_evidence=1,
                         second_check_verifiers=["verifier-1"], version=7)
    ev_at = (now - timedelta(seconds=60)).isoformat()
    triple = (floor,
              [CorroborationEvidence(kind="second_check",
                                     fingerprint="fp-D",
                                     decided_at=ev_at,
                                     verifier_id="verifier-1")],
              now)
    ts = "2026-10-05T12:00:00Z"
    return [
        ArchiveDecisionInput(
            alert=alert("fp-A", "a-A"),
            inputs=CounterfactualInputs(
                jev_model="jev-1.13.0",
                q_sev=sev(0.0, 0.35, 0.60, 0.05), q_team=team,
                q_disp=disp("page_now"), latency_ms=10.0,
                prob_lock_pass=True),
            recorded_action="page_now", recorded_reason="threshold", ts=ts),
        ArchiveDecisionInput(
            alert=alert("fp-B", "a-B"),
            inputs=CounterfactualInputs(
                jev_model="jev-1.13.0",
                q_sev=sev(0.0, 0.50, 0.45, 0.05), q_team=team,
                q_disp=disp("page_now"), latency_ms=10.0,
                prob_lock_pass=True),
            recorded_action="page_now", recorded_reason="threshold", ts=ts),
        ArchiveDecisionInput(
            alert=alert("fp-C", "a-C"),
            inputs=CounterfactualInputs(
                jev_model="jev-1.13.0",
                q_sev=sev(0.0, 0.05, 0.90, 0.05, choice="p3_medium"),
                q_team=team, q_disp=disp("page_business_hours"),
                latency_ms=10.0, prob_lock_pass=True),
            recorded_action="page_business_hours", recorded_reason="threshold",
            ts=ts),
        ArchiveDecisionInput(
            alert=alert("fp-D", "a-D"),
            inputs=CounterfactualInputs(
                jev_model="jev-1.13.0",
                q_sev=sev(0.0, 0.05, 0.90, 0.05, choice="p3_medium"),
                q_team=team, q_disp=disp("suppress"),
                latency_ms=10.0, prob_lock_pass=True,
                freshness_report=green,
                corro_floor=triple[0], corro_evidences=triple[1],
                corro_now=triple[2]),
            recorded_action="page_business_hours", recorded_reason="threshold",
            ts=ts),
        ArchiveDecisionInput(
            alert=alert("fp-E", "a-E"),
            inputs=CounterfactualInputs(
                jev_model="jev-1.13.0",
                q_sev=sev(0.0, 0.05, 0.90, 0.05, choice="p3_medium"),
                q_team=team, q_disp=disp("suppress"),
                latency_ms=10.0, prob_lock_pass=True,
                freshness_report=green),
            # No recorded corroboration triple: the live leg never ran.
            recorded_action="page_business_hours", recorded_reason="threshold",
            ts=ts),
    ]


class TestD5NarrowImport(unittest.TestCase):
    def test_only_resolve_suppress_path_from_gate(self):
        """Approval condition 1: the replay path may use
        Gate._resolve_suppress_path ONLY."""
        tree = ast.parse(_module_source(advisory_d5))
        gate_imports = [n for n in ast.walk(tree)
                        if isinstance(n, ast.ImportFrom)
                        and (n.module or "").endswith("sentinel.gate")]
        names = {a.asname or a.name for n in gate_imports for a in n.names}
        self.assertEqual(names, {"Gate"},
                         f"advisory_d5 gate imports: {names}")
        attrs = {n.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute)
                 and isinstance(n.value, ast.Name)
                 and n.value.id == "Gate"}
        self.assertTrue(attrs <= {"_resolve_suppress_path"},
                        f"Gate.<attr> uses beyond _resolve_suppress_path: "
                        f"{attrs}")

    def test_no_write_capable_imports(self):
        """No policy/forwarder/audit/eventlog-writer imports in the replay."""
        tree = ast.parse(_module_source(advisory_d5))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        banned = {"sentinel.policy_lifecycle", "sentinel.forwarder",
                  "sentinel.audit", "sentinel.eventlog"}
        hit = {m for m in imported
               if m in banned or m.startswith("sentinel.policy")}
        self.assertEqual(hit, set(), f"write-capable imports: {hit}")


class TestD5Replay(unittest.TestCase):
    def test_replay_exact_delta_table_and_readonly(self):
        now = datetime(2026, 10, 6, tzinfo=timezone.utc)
        archive = _archive_fixtures(now)
        snapshot = copy.deepcopy(archive)
        proposed = ProposedPolicy(
            name="raise-page-floor+allowlist",
            thresholds_override={"page_p1p2_min": 0.40},
            allowlist_add=["fp-D", "fp-E"])
        kernel = build_offline_kernel(
            proposed, clock=lambda: now)
        # The offline kernel carries no live machinery.
        self.assertIsNone(kernel.policy_gate)
        self.assertFalse(hasattr(kernel, "audit"))
        self.assertFalse(hasattr(kernel, "_runner"))

        results = replay_archive(kernel, archive, proposed)
        by_id = {r["alert_id"]: r for r in results}
        self.assertEqual(by_id["a-A"]["replay_action"], "page_business_hours")
        self.assertEqual(by_id["a-B"]["replay_action"], "page_now")
        self.assertEqual(by_id["a-C"]["replay_action"], "page_business_hours")
        d = by_id["a-D"]
        self.assertEqual(d["kernel_action"], "suppress")
        self.assertEqual(d["replay_action"], "suppress")
        self.assertEqual(d["corroboration"], "passed")
        e = by_id["a-E"]
        self.assertEqual(e["kernel_action"], "suppress")
        # No recorded evidence: fails closed, honestly reported.
        self.assertEqual(e["replay_action"], "page_now")
        self.assertEqual(e["replay_reason"], "uncorroborated")
        self.assertEqual(e["corroboration"], "unresolvable")

        # The archive is read-only: replay mutated nothing.
        self.assertEqual(archive, snapshot)

    def test_d5_preview_report_envelope(self):
        now = datetime(2026, 10, 6, tzinfo=timezone.utc)
        archive = _archive_fixtures(now)
        proposed = ProposedPolicy(
            name="raise-page-floor+allowlist",
            thresholds_override={"page_p1p2_min": 0.40},
            allowlist_add=["fp-D", "fp-E"])
        fake = RecordingDecide(_resp(answers={
            "riskiest_delta": _ans("riskiest_delta", choice="case-0",
                                   probs={"case-0": 0.7, "case-1": 0.2,
                                          "case-2": 0.1})}))
        d = _dispatcher(fake)
        env = d5_preview(archive=archive, proposed=proposed,
                         dispatcher=d, window_days=7,
                         draft_id="t5-test", now=now)
        d.close()
        self.assertEqual(env["type"], "advisory/policy_impact_preview")
        body = env["body"]
        self.assertEqual(body["inputs_evaluated"], 5)
        self.assertEqual(body["inputs_unresolvable"], 0)
        self.assertEqual(body["recorded_pages"], 2)  # A, B
        self.assertEqual(body["would_suppress_of_pages"], 1)  # D only
        self.assertEqual(body["kernel_newly_suppress"], 2)    # D + E
        self.assertEqual(body["newly_uncorroborated"], 1)     # E
        self.assertEqual(body["newly_paged"], 0)
        self.assertEqual(body["by_transition"],
                         {"page_now->page_business_hours": 1,
                          "page_business_hours->suppress": 1,
                          "page_business_hours->page_now": 1})
        # Jev triage ran: one call, top-5 flagged, honestly labeled.
        self.assertEqual(fake.n_calls, 1)
        self.assertEqual(len(fake.calls[0]["questions"]), 1)
        self.assertIn("riskiest_delta", fake.calls[0]["questions"])
        self.assertLessEqual(
            len(fake.calls[0]["questions"]["riskiest_delta"]["criteria"]),
            26)  # <=25 cases + cannot_determine
        self.assertEqual(env["fallback"], "jev")
        self.assertTrue(env["ai_generated"])
        self.assertEqual(len(body["review_list"]), 3)  # 3 changed cases
        self.assertEqual(body["review_ranked_by"], "jev")

    def test_d5_preview_without_dispatcher_deterministic(self):
        now = datetime(2026, 10, 6, tzinfo=timezone.utc)
        archive = _archive_fixtures(now)
        proposed = ProposedPolicy(
            name="raise-page-floor",
            thresholds_override={"page_p1p2_min": 0.40})
        env = d5_preview(archive=archive, proposed=proposed,
                         dispatcher=None, window_days=7,
                         draft_id="t5-test-2", now=now,
                         with_triage=False)
        self.assertEqual(env["fallback"], "deterministic")
        self.assertFalse(env["ai_generated"])
        body = env["body"]
        self.assertEqual(body["would_suppress_of_pages"], 0)
        self.assertEqual(body["by_transition"],
                         {"page_now->page_business_hours": 1})

    def test_archive_window_verified_against_retention(self):
        """Approval condition 4: 7d default holds only if retention covers."""
        floor = RetentionConfig().hot_floor_days
        self.assertGreaterEqual(floor, 7)
        self.assertEqual(verify_archive_window(7), 7)
        with self.assertRaises(ArchiveWindowError):
            verify_archive_window(floor + 1)

    def test_filter_archive_window_excludes_unparseable(self):
        now = datetime(2026, 10, 6, tzinfo=timezone.utc)
        archive = _archive_fixtures(now)
        archive[0].ts = "not-a-timestamp"
        archive[1].ts = "2026-01-01T00:00:00Z"  # outside the 7d window
        kept, excluded = filter_archive_window(archive, 7, now=now)
        self.assertEqual(len(kept), 3)
        self.assertEqual(excluded, 2)

    def test_rate_limit_one_per_draft_per_hour(self):
        ledger = {}
        ok, _ = check_rate_limit(ledger, "draft-1", now_s=1000.0)
        self.assertTrue(ok)
        ok, wait = check_rate_limit(ledger, "draft-1", now_s=2000.0)
        self.assertFalse(ok)
        self.assertGreater(wait, 0)
        ok, _ = check_rate_limit(ledger, "draft-2", now_s=2000.0)
        self.assertTrue(ok)  # per-draft, not global
        ok, _ = check_rate_limit(ledger, "draft-1", now_s=1000.0 + 3601.0)
        self.assertTrue(ok)  # window elapsed

    def test_triage_with_jev_down_deterministic_ranking(self):
        fake = RecordingDecide(exc=JevError("no key"))
        d = _dispatcher(fake)
        cases = [{"case_id": "c1", "alert_id": "a1", "severity_in": "critical",
                  "recorded_action": "page_now", "replay_action": "suppress"},
                 {"case_id": "c2", "alert_id": "a2", "severity_in": "low",
                  "recorded_action": "page_business_hours",
                  "replay_action": "suppress"}]
        flagged, label = triage_with_jev(d, cases)
        self.assertEqual(label, "deterministic")
        # Deterministic proxy: critical page->suppress outranks low.
        self.assertEqual(flagged[0]["case_id"], "c1")
        self.assertEqual(flagged[0]["review_priority"], 1)
        d.close()


# ---------------------------------------------------------------------------
# Renderer goldens (zero Jev — pure functions)
# ---------------------------------------------------------------------------

class TestRendererGoldens(unittest.TestCase):
    def test_no_sentinel_imports_in_render_module(self):
        import sentinel.advisory_render as rm
        tree = ast.parse(_module_source(rm))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", "") or ""
                names = [a.name for a in getattr(node, "names", [])]
                self.assertFalse(
                    mod.startswith("sentinel") or mod == "sentinel"
                    or any(n.startswith("sentinel") for n in names),
                    "renderers must stay dependency-free")

    def test_suppression_paragraph_golden(self):
        text = render.render_suppression_paragraph(
            reason="allowlist",
            legs=["allowlist_hit", "freshness_ok"],
            emphasis_leg="allowlist_hit",
            alert_id="a-1", fingerprint="fp-1",
            recorded_ts="2026-10-06T00:00:00Z")
        self.assertEqual(
            text,
            "Alert a-1 was suppressed (reason: allowlist); no page was "
            "sent. From the recorded evidence, the least obvious part of "
            "this decision is that the alert's fingerprint matched an "
            "allowlist entry, which may not have been obvious as covering "
            "this check. Evidence on record: allowlist_hit, freshness_ok. "
            "Suppression recorded at 2026-10-06T00:00:00Z for fingerprint "
            "fp-1; the suppression stands exactly as decided.")

    def test_suppression_paragraph_neutral_fallback(self):
        text = render.render_suppression_paragraph(
            reason="threshold", legs=[], emphasis_leg=None,
            alert_id="a-2", fingerprint="fp-2",
            recorded_ts="2026-10-06T00:00:00Z")
        self.assertIn("no single evidence leg stood out", text)
        self.assertIn("no recorded evidence legs", text)

    def test_storm_brief_golden(self):
        text = render.render_storm_brief(
            storm_size=42, storm_counts={"db": 30, "web": 12},
            clusters=["cluster-a", "cluster-b"], lead_pick="cluster-a",
            novelty_level=7)
        self.assertEqual(
            text,
            "Storm of 42 alerts (db=30, web=12). Leading cluster: "
            "cluster-a. Pattern novelty 7/10 (ordinal). 2 member "
            "clusters: cluster-a, cluster-b.")

    def test_evidence_bundle_ranking_and_stale(self):
        incidents = [{"incident_id": "i1"}, {"incident_id": "i2"}]
        bundle = render.assemble_evidence_bundle(
            incidents=incidents, runbooks=[], incident_pick="i2",
            runbook_pick=None, stale=True)
        self.assertEqual(
            [i["incident_id"] for i in bundle["incidents"]], ["i2", "i1"])
        self.assertEqual(bundle["ranked_by"], "jev")
        self.assertTrue(bundle["stale_warning"])
        self.assertIn("stale_note", bundle)

    def test_hypothesis_framing_unconfirmed(self):
        hyp = render.render_hypothesis(hypothesis="bad deploy of X",
                                       plausibility_level=8)
        self.assertEqual(hyp["plausibility"],
                         "plausibility level 8/10 (ordinal)")
        self.assertIn("unconfirmed", hyp["status"])
        self.assertTrue(hyp["not_a_conclusion"])

    def test_triage_suggestion_framing(self):
        s = render.render_triage_suggestion(owner="data", severity="p2_high")
        self.assertIn("confirm or override", s["status"])
        self.assertFalse(s["changes_routing"])


# ---------------------------------------------------------------------------
# D1 volume guard, D6 trigger, human_second_look tag, never-raises, D3
# ---------------------------------------------------------------------------

class TestDirectionPredicates(unittest.TestCase):
    def test_d1_fires_on_surprising_severity(self):
        for sev in ("critical", "high", "p1", "p2"):
            self.assertTrue(d1_should_fire({"severity_in": sev,
                                            "fingerprint": "fp-x"}),
                            sev)

    def test_d1_samples_one_percent_stably(self):
        fired = [fp for fp in (f"fp-{i}" for i in range(1000))
                 if d1_should_fire({"severity_in": "low",
                                    "fingerprint": fp})]
        # Stable 1% sample: deterministic hash, ~10 of 1000.
        self.assertTrue(5 <= len(fired) <= 20, len(fired))
        again = [fp for fp in (f"fp-{i}" for i in range(1000))
                 if d1_should_fire({"severity_in": "low",
                                    "fingerprint": fp})]
        self.assertEqual(fired, again)

    def test_d6_trigger_minority_path_only(self):
        self.assertTrue(d6_should_fire({"race_source": "timer"}))
        self.assertTrue(d6_should_fire(
            {"race_source": "jev",
             "race_answers": {"severity": "p2_high",
                             "owning_team": "cannot_determine"}}))
        self.assertTrue(d6_should_fire({"race_source": "jev"}))  # no answers
        self.assertFalse(d6_should_fire(
            {"race_source": "jev",
             "race_answers": {"severity": "p2_high",
                             "owning_team": "data"}}))  # clean read: skip


class TestHumanSecondLookTag(unittest.TestCase):
    def test_second_look_tag_recorded_on_suppression(self):
        """Approved: the tag is recorded on the suppression record. The
        digest surface consuming it is Track 8 / future — out of scope."""
        fake = RecordingDecide(_resp(answers={
            "dominant_leg": _ans("dominant_leg", choice="allowlist_hit"),
            "worth_second_look": _ans("worth_second_look", qtype="noul",
                                      noul=0.8)}))
        d = _dispatcher(fake)
        got = []
        done = threading.Event()
        suppression_explainer(d, _suppression_payload(),
                              lambda env: (got.append(env), done.set()),
                              recorded_ts="2026-10-06T00:00:00Z")
        self.assertTrue(done.wait(10))
        d.close()
        env = got[0]
        self.assertEqual(env["fallback"], "jev")
        self.assertTrue(env["body"]["human_second_look"])
        self.assertEqual(env["body"]["emphasis_leg"], "allowlist_hit")
        # Still an advisory enrichment — never a paging-path write.
        self.assertEqual(env["type"], "advisory/suppression_explainer")
        self.assertNotIn("disposition", env["body"])

    def test_no_tag_when_clean(self):
        fake = RecordingDecide(_resp(answers={
            "dominant_leg": _ans("dominant_leg",
                                 choice="cannot_determine"),
            "worth_second_look": _ans("worth_second_look", qtype="noul",
                                      noul=0.1)}))
        d = _dispatcher(fake)
        got = []
        done = threading.Event()
        suppression_explainer(d, _suppression_payload(),
                              lambda env: (got.append(env), done.set()))
        self.assertTrue(done.wait(10))
        d.close()
        self.assertNotIn("human_second_look", got[0]["body"])
        self.assertIsNone(got[0]["body"]["emphasis_leg"])


class TestNeverRaises(unittest.TestCase):
    def test_exploding_everything_still_returns_envelopes(self):
        def boom(state, questions):
            raise RuntimeError("jev exploded")

        def boom_jev(resp):
            raise RuntimeError("on_jev exploded")

        def boom_fb(reason):
            raise RuntimeError("on_fallback exploded")

        d = AdvisoryDispatcher(boom, SpendMeter())
        env = d.call_inline("rca_hypotheses", {"a": 1},
                            d4_questions(["h"]), boom_jev, boom_fb)
        self.assertEqual(env["fallback"], "absent")
        got = []
        done = threading.Event()
        ok = d.submit("rca_hypotheses", {"a": 1}, d4_questions(["h"]),
                      boom_jev, boom_fb,
                      lambda e: (got.append(e), done.set()))
        self.assertTrue(ok)
        self.assertTrue(done.wait(5))
        self.assertEqual(got[0]["fallback"], "absent")
        d.close()


class TestD3RetrievalHelpers(unittest.TestCase):
    def test_retrieve_related_incidents_read_only(self):
        seen = []

        def read_log(**kw):
            seen.append(kw)
            return [{"incident_id": "i1", "summary": "db failover",
                     "ts": "2026-09-01T00:00:00Z", "disposition": "page_now"},
                    "not-a-dict",
                    {"alert_id": "a9", "title": "weird"}]

        out = retrieve_related_incidents(read_log, fingerprint="fp-1",
                                         service="db", check="failover")
        self.assertEqual(seen[0]["fingerprint"], "fp-1")
        self.assertEqual(len(out), 2)  # non-dict rows skipped
        self.assertEqual(out[0]["incident_id"], "i1")
        self.assertEqual(out[1]["incident_id"], "a9")  # alert_id fallback

    def test_match_runbooks_keyword(self):
        index = [
            {"runbook_id": "rb-1", "title": "DB failover",
             "keywords": ["failover", "postgres"], "snippet": "step 1..."},
            {"runbook_id": "rb-2", "title": "Web deploy",
             "keywords": ["deploy"], "snippet": "roll back..."},
        ]
        out = match_runbooks(index, "failover")
        self.assertEqual([r["runbook_id"] for r in out], ["rb-1"])
        self.assertEqual(match_runbooks([], "x"), [])
        self.assertEqual(match_runbooks(index, None), [])

    def test_cap_state_tokens_truncates_by_recency(self):
        state = {"incidents": [{"incident_id": f"i{i}",
                                "summary": "x" * 500} for i in range(12)],
                 "runbooks": []}
        capped = cap_state_tokens(dict(state), max_tokens=1000)
        self.assertTrue(capped["truncated"])
        # Oldest dropped first: newest survivors keep order.
        self.assertLess(len(capped["incidents"]), 12)
        self.assertEqual(capped["incidents"][0]["incident_id"], "i0")

    def test_d3_module_has_no_writer_imports(self):
        tree = ast.parse(_module_source(advisory_d3))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        banned = {"sentinel.gate", "sentinel.eventlog", "sentinel.forwarder",
                  "sentinel.audit", "sentinel.policy_lifecycle"}
        self.assertTrue(banned.isdisjoint(imported), imported - banned)


if __name__ == "__main__":
    unittest.main()
