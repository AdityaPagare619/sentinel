"""Tests for sentinel.sim_judge — Track 2, contract C2 (real Jev in sim).

Covers: FakeJev honesty (never fake-real) · spend cap enforcement
(budget=0 → zero Jev calls) · real race outcomes (judge-wins,
timer-wins with late answers powerless) · budget-exhaustion degradation
· JudgeResult contract validation · key hygiene (the key never leaves
the server process) · the /api/v1/jev/spend endpoint · the gate's
JudgeResult attachment.

Local CPU only. No live vendor calls: the "real" client is exercised
through canned stand-ins plus the real race machinery; live verification
against the actual Jev API needs TYPESAFE_API_KEY provisioning.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import contextlib
import io
import json
import threading
import time
import unittest
import unittest.mock
import urllib.request
import urllib.error

from sentinel import race as race_mod
from sentinel import sim_judge
from sentinel.audit import AuditLog
from sentinel.client import (Answer, DecisionResponse, JevError, JevTimeout,
                             SystemOneClient)
from sentinel.correlator import Correlator
from sentinel.forwarder import Forwarder
from sentinel.gate import Gate
from sentinel.models import Thresholds
from sentinel.questions import build_questions
from sentinel.receiver import Pipeline, ReceiverConfig, make_server
from sentinel.state import build_state, input_sha256

from tests.helpers import make_alert

_SENTINEL_KEY = "sk-test-SENTINELKEY-9f8e7d6c5b4a"


def _answer(qid, choice, confidence=None):
    return Answer(qid=qid, qtype="choice", choice=choice, noul=None,
                  probabilities={}, confidence=confidence)


def _canned_response(q3_choice="suppress", q3_conf=0.85, input_tokens=2000,
                     model="jev-1.13.0"):
    return DecisionResponse(
        model=model,
        answers={
            "severity": _answer("severity", "p1_critical", 0.9),
            "owning_team": _answer("owning_team", "platform", 0.99),
            "disposition": _answer("disposition", q3_choice, q3_conf),
        },
        input_tokens=input_tokens,
    )


class CannedClient:
    """Deterministic stand-in with controllable latency and cost."""

    timeout_s = 30.0
    model = "canned-0.0.0"

    def __init__(self, response=None, exc=None, delay_s=0.0):
        self.response = response
        self.exc = exc
        self.delay_s = delay_s
        self.calls = []
        self._lock = threading.Lock()

    def decide(self, state, questions):
        with self._lock:
            self.calls.append({"state": state})
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.exc is not None:
            raise self.exc("boom")
        return self.response

    @property
    def call_count(self):
        with self._lock:
            return len(self.calls)


class _EnvGuard:
    """Save/restore environ around key-provisioning tests."""

    def __init__(self, **overrides):
        self.overrides = overrides
        self.saved = {}

    def __enter__(self):
        for k, v in self.overrides.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _short_race_config():
    return race_mod.RaceConfig.from_raw(500)  # floor: 500 ms budget


class TestFakeJev(unittest.TestCase):
    def test_honest_labels(self):
        fake = sim_judge.FakeJev()
        self.assertEqual(fake.model, "fakejev-0.0.0")
        self.assertEqual(fake.judge_label, "simulated judge")
        resp = fake.decide({"a": 1}, build_questions())
        self.assertEqual(resp.model, "fakejev-0.0.0")
        self.assertEqual(resp.input_tokens, 0)
        for qid, ans in resp.answers.items():
            self.assertEqual(ans.choice, "cannot_determine",
                             f"{qid} must be cannot_determine (uncertainty "
                             "pages — a mock must never suppress)")
            self.assertIsNone(ans.confidence)
        self.assertEqual(fake.call_count, 1)

    def test_no_network_attributes(self):
        # The mock must not carry anything that looks like wire config.
        fake = sim_judge.FakeJev()
        self.assertFalse(hasattr(fake, "base_url"))
        self.assertFalse(hasattr(fake, "api_key"))


class TestSpendTracker(unittest.TestCase):
    def test_zero_budget_starts_blocked(self):
        t = sim_judge.JevSpendTracker(0.0)
        self.assertTrue(t.blocked)
        self.assertFalse(t.try_begin_call())
        snap = t.snapshot()
        self.assertEqual(snap, {"session_usd": 0.0, "budget_usd": 0.0,
                                "calls": 0, "blocked": True})

    def test_negative_and_garbage_budget_fail_closed(self):
        for bad in (-1.0, "nope", None):
            t = sim_judge.JevSpendTracker(bad)
            self.assertTrue(t.blocked, f"budget={bad!r} must start blocked")

    def test_latch_on_exhaustion(self):
        t = sim_judge.JevSpendTracker(0.05)
        self.assertTrue(t.try_begin_call())
        t.record_call(0.03)
        self.assertFalse(t.blocked)
        self.assertEqual(t.snapshot()["calls"], 1)
        t.record_call(0.03)  # 0.06 >= 0.05
        self.assertTrue(t.blocked)
        self.assertFalse(t.try_begin_call())
        snap = t.snapshot()
        self.assertAlmostEqual(snap["session_usd"], 0.06)
        self.assertEqual(snap["budget_usd"], 0.05)
        self.assertEqual(snap["calls"], 2)

    def test_snapshot_has_no_key_material(self):
        t = sim_judge.JevSpendTracker(0.5)
        t.record_call(0.000042)
        self.assertEqual(set(t.snapshot().keys()),
                         {"session_usd", "budget_usd", "calls", "blocked"})


class TestSpendCappedClient(unittest.TestCase):
    def test_cost_recorded_from_input_tokens(self):
        inner = CannedClient(response=_canned_response(input_tokens=2000))
        tracker = sim_judge.JevSpendTracker(0.50)
        client = sim_judge.SpendCappedJevClient(inner, tracker)
        resp = client.decide({"s": 1}, build_questions())
        self.assertIs(resp, inner.response)
        expected = 2000 * sim_judge.JEV_PRICE_PER_INPUT_TOKEN_USD
        self.assertAlmostEqual(tracker.session_usd, expected)
        self.assertEqual(tracker.snapshot()["calls"], 1)
        self.assertEqual(client.model, inner.model)
        self.assertEqual(client.timeout_s, 30.0)

    def test_blocked_raises_before_any_wire_call(self):
        inner = CannedClient(response=_canned_response())
        tracker = sim_judge.JevSpendTracker(0.0)  # blocked from birth
        client = sim_judge.SpendCappedJevClient(inner, tracker)
        with self.assertRaises(sim_judge.JevBudgetExhausted) as ctx:
            client.decide({"s": 1}, build_questions())
        self.assertIn("Jev budget exhausted", str(ctx.exception))
        self.assertIsInstance(ctx.exception, JevError)  # race fail-open path
        self.assertEqual(inner.call_count, 0)
        self.assertEqual(tracker.snapshot()["calls"], 0)

    def test_failed_call_counts_but_costs_zero(self):
        inner = CannedClient(exc=JevTimeout)
        tracker = sim_judge.JevSpendTracker(0.50)
        client = sim_judge.SpendCappedJevClient(inner, tracker)
        with self.assertRaises(JevTimeout):
            client.decide({"s": 1}, build_questions())
        snap = tracker.snapshot()
        self.assertEqual(snap["calls"], 1)  # it hit the wire
        self.assertEqual(snap["session_usd"], 0.0)  # no usage data: no cost

    def test_budget_exhaustion_mid_run(self):
        # $0.042/call at 1M input tokens; budget $0.05 → 1 ok, then blocked.
        inner = CannedClient(
            response=_canned_response(input_tokens=1_000_000))
        tracker = sim_judge.JevSpendTracker(0.05)
        client = sim_judge.SpendCappedJevClient(inner, tracker)
        client.decide({"s": 1}, build_questions())
        self.assertFalse(tracker.blocked)
        client.decide({"s": 2}, build_questions())
        self.assertTrue(tracker.blocked)
        with self.assertRaises(sim_judge.JevBudgetExhausted):
            client.decide({"s": 3}, build_questions())
        self.assertEqual(inner.call_count, 2)


class TestBuildSimJudge(unittest.TestCase):
    def test_no_key_gives_fakejev(self):
        with _EnvGuard(TYPESAFE_API_KEY=None):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                client, tracker, mode = sim_judge.build_sim_judge(
                    budget_usd=0.50)
        self.assertIsInstance(client, sim_judge.FakeJev)
        self.assertEqual(mode, "fakejev")
        self.assertIn("simulated judge", buf.getvalue())
        self.assertIn("NO real vendor calls", buf.getvalue())

    def test_key_gives_spend_capped_real_client(self):
        with _EnvGuard(TYPESAFE_API_KEY=_SENTINEL_KEY):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                client, tracker, mode = sim_judge.build_sim_judge(
                    budget_usd=0.50)
        self.assertIsInstance(client, sim_judge.SpendCappedJevClient)
        self.assertIsInstance(client.inner, SystemOneClient)
        self.assertEqual(mode, "jev-real")
        self.assertTrue(sim_judge.is_real_vendor_client(client))
        self.assertFalse(sim_judge.is_real_vendor_client(
            sim_judge.FakeJev()))
        out = buf.getvalue()
        self.assertIn("REAL", out)
        self.assertIn("source=env", out)
        self.assertNotIn(_SENTINEL_KEY, out)  # the value never logged

    def test_budget_from_env(self):
        with _EnvGuard(SENTINEL_JEV_BUDGET_USD="1.25",
                       TYPESAFE_API_KEY=None):
            _, tracker, _ = sim_judge.build_sim_judge()
        self.assertEqual(tracker.snapshot()["budget_usd"], 1.25)

    def test_explicit_budget_beats_env(self):
        with _EnvGuard(SENTINEL_JEV_BUDGET_USD="9.99",
                       TYPESAFE_API_KEY=None):
            _, tracker, _ = sim_judge.build_sim_judge(budget_usd=0.10)
        self.assertEqual(tracker.snapshot()["budget_usd"], 0.10)


class TestJudgeResultContract(unittest.TestCase):
    def test_valid_record(self):
        r = sim_judge.judge_result(
            judgment="page", confidence=0.85, latency_ms=123.4,
            source="jev", model_version="jev-1.13.0", cost_usd=0.000042)
        self.assertEqual(set(r.keys()),
                         {"judgment", "confidence", "latency_ms", "source",
                          "model_version", "cost_usd"})

    def test_rejects_bad_inputs(self):
        good = dict(judgment="page", confidence=0.5, latency_ms=1.0,
                    source="jev", model_version="m", cost_usd=0.0)
        for key, bad in [("judgment", "maybe"), ("source", "vendor"),
                         ("confidence", 1.5), ("confidence", -0.1),
                         ("latency_ms", "fast")]:
            kw = dict(good, **{key: bad})
            with self.assertRaises(ValueError):
                sim_judge.judge_result(**kw)
        # cost must be 0.0 when source != jev (contract C2)
        with self.assertRaises(ValueError):
            sim_judge.judge_result(**dict(good, source="timer",
                                          cost_usd=0.001))

    def test_source_mapping(self):
        self.assertEqual(
            sim_judge.source_from_budget_outcome("answered_in_time"), "jev")
        self.assertEqual(
            sim_judge.source_from_budget_outcome("timer_won"), "timer")
        self.assertEqual(
            sim_judge.source_from_budget_outcome("timer_won_shed"), "timer")
        for bo in ("error_passthrough", "failopen_stepped",
                   "structural_passthrough"):
            self.assertEqual(sim_judge.source_from_budget_outcome(bo),
                             "deterministic")


class TestSimJudgeRace(unittest.TestCase):
    def _judge(self, client, tracker, mode="jev-real"):
        j = sim_judge.SimJudge(client, tracker, mode=mode,
                               race_config=_short_race_config())
        self.addCleanup(j.close)
        return j

    def _alert_ctx(self, alert_id="t2-a1"):
        alert = make_alert(alert_id=alert_id)
        state = build_state(alert, {}, {})
        return alert, state, build_questions(), input_sha256(state)

    def test_judge_wins_real_race(self):
        inner = CannedClient(response=_canned_response(
            q3_choice="suppress", q3_conf=0.85, input_tokens=2000))
        tracker = sim_judge.JevSpendTracker(0.50)
        client = sim_judge.SpendCappedJevClient(inner, tracker)
        judge = self._judge(client, tracker)
        alert, state, questions, in_sha = self._alert_ctx()
        result = judge.judge(alert=alert, state=state, questions=questions,
                             input_sha256=in_sha)
        self.assertEqual(result["source"], "jev")
        self.assertEqual(result["judgment"], "suppress")
        self.assertAlmostEqual(result["confidence"], 0.85)  # ordinal, kept
        self.assertLess(result["latency_ms"], 500.0)  # beat the timer
        self.assertAlmostEqual(
            result["cost_usd"], 2000 * sim_judge.JEV_PRICE_PER_INPUT_TOKEN_USD)
        self.assertEqual(tracker.snapshot()["calls"], 1)

    def test_timer_win_is_real_and_late_answer_powerless(self):
        # The judge takes 3s; the budget is 500ms. The result must come
        # back in ~500ms — long before the judge answers — proving the
        # late answer can never override it.
        inner = CannedClient(
            response=_canned_response(q3_choice="suppress", q3_conf=0.99),
            delay_s=3.0)
        tracker = sim_judge.JevSpendTracker(0.50)
        client = sim_judge.SpendCappedJevClient(inner, tracker)
        judge = self._judge(client, tracker)
        alert, state, questions, in_sha = self._alert_ctx()
        t0 = time.monotonic()
        result = judge.judge(alert=alert, state=state, questions=questions,
                             input_sha256=in_sha)
        elapsed = time.monotonic() - t0
        self.assertEqual(result["source"], "timer")
        self.assertEqual(result["judgment"], "page")  # fail-open
        self.assertEqual(result["cost_usd"], 0.0)  # contract: 0 when != jev
        self.assertLess(elapsed, 2.5,
                        "result must arrive before the 3s judge answers")
        # The detached call still ran (spend is honest) but changed nothing.
        self.assertEqual(inner.call_count, 1)

    def test_budget_zero_means_zero_jev_calls(self):
        inner = CannedClient(response=_canned_response())
        tracker = sim_judge.JevSpendTracker(0.0)
        client = sim_judge.SpendCappedJevClient(inner, tracker)
        judge = self._judge(client, tracker)
        alert, state, questions, in_sha = self._alert_ctx()
        result = judge.judge(alert=alert, state=state, questions=questions,
                             input_sha256=in_sha)
        self.assertEqual(result["source"], "deterministic")
        self.assertEqual(result["judgment"], "page")
        self.assertEqual(result["confidence"], 1.0)
        self.assertEqual(result["cost_usd"], 0.0)
        self.assertEqual(inner.call_count, 0)
        self.assertEqual(tracker.snapshot()["calls"], 0)

    def test_exhaustion_degrades_mid_session(self):
        inner = CannedClient(
            response=_canned_response(q3_choice="suppress", q3_conf=0.9,
                                      input_tokens=1_000_000))
        tracker = sim_judge.JevSpendTracker(0.05)  # $0.042/call
        client = sim_judge.SpendCappedJevClient(inner, tracker)
        judge = self._judge(client, tracker)
        alert, state, questions, in_sha = self._alert_ctx()
        first = judge.judge(alert=alert, state=state, questions=questions,
                            input_sha256=in_sha)
        self.assertEqual(first["source"], "jev")
        # Call 2 is authorized (pre-check sees 0.042 < 0.05); its cost
        # lands after and trips the latch.
        second = judge.judge(alert=alert, state=state, questions=questions,
                             input_sha256=in_sha)
        self.assertEqual(second["source"], "jev")
        self.assertTrue(tracker.blocked)
        # Call 3: blocked BEFORE any wire call — deterministic, zero Jev.
        third = judge.judge(alert=alert, state=state, questions=questions,
                            input_sha256=in_sha)
        self.assertEqual(third["source"], "deterministic")
        self.assertEqual(third["judgment"], "page")
        self.assertEqual(inner.call_count, 2)  # no third wire call

    def test_fakejev_never_labeled_jev(self):
        fake = sim_judge.FakeJev()
        tracker = sim_judge.JevSpendTracker(0.50)
        judge = self._judge(fake, tracker, mode="fakejev")
        alert, state, questions, in_sha = self._alert_ctx()
        result = judge.judge(alert=alert, state=state, questions=questions,
                             input_sha256=in_sha)
        # The mock "won" the race, but the record must not say jev.
        self.assertEqual(result["source"], "deterministic")
        self.assertEqual(result["model_version"], "fakejev-0.0.0")
        self.assertEqual(result["judgment"], "page")  # cannot_determine
        self.assertEqual(result["cost_usd"], 0.0)

    def test_judge_error_fails_open(self):
        inner = CannedClient(exc=JevTimeout)
        tracker = sim_judge.JevSpendTracker(0.50)
        client = sim_judge.SpendCappedJevClient(inner, tracker)
        judge = self._judge(client, tracker)
        alert, state, questions, in_sha = self._alert_ctx()
        result = judge.judge(alert=alert, state=state, questions=questions,
                             input_sha256=in_sha)
        self.assertEqual(result["source"], "deterministic")
        self.assertEqual(result["judgment"], "page")  # never silence

    def test_decide_advisory_shares_spend_cap(self):
        inner = CannedClient(response=_canned_response(input_tokens=500))
        tracker = sim_judge.JevSpendTracker(0.50)
        client = sim_judge.SpendCappedJevClient(inner, tracker)
        judge = self._judge(client, tracker)
        resp = judge.decide_advisory({"s": 1}, build_questions())
        self.assertIs(resp, inner.response)
        self.assertEqual(tracker.snapshot()["calls"], 1)


class TestKeyHygiene(unittest.TestCase):
    """The key never leaves the server process: not in logs, responses,
    payloads, error messages, or the browser."""

    def test_key_absent_from_all_emissions(self):
        with _EnvGuard(TYPESAFE_API_KEY=_SENTINEL_KEY):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                client, tracker, _ = sim_judge.build_sim_judge(
                    budget_usd=0.0)  # blocked: decide raises, no wire call
            self.assertNotIn(_SENTINEL_KEY, buf.getvalue())
            self.assertNotIn(_SENTINEL_KEY,
                             json.dumps(tracker.snapshot()))
            with self.assertRaises(sim_judge.JevBudgetExhausted) as ctx:
                client.decide({"s": 1}, build_questions())
            self.assertNotIn(_SENTINEL_KEY, str(ctx.exception))
            self.assertNotIn(_SENTINEL_KEY, repr(client))
            self.assertNotIn(_SENTINEL_KEY, repr(tracker))

    def test_no_log_line_mentions_key_material(self):
        # Static guard: no logging/response line in the Track-2 surface
        # may interpolate anything named like a key.
        here = os.path.dirname(os.path.abspath(__file__))
        src = os.path.join(here, "..", "src", "sentinel")
        for fname in ("sim_judge.py",):
            lines = open(os.path.join(src, fname)).read().split("\n")
            for i, line in enumerate(lines, 1):
                if ("stderr.write" in line or "log." in line
                        or "_send_json" in line):
                    self.assertNotIn("api_key", line,
                                     f"{fname}:{i} may log key material")
                    self.assertNotIn("API_KEY", line,
                                     f"{fname}:{i} may log key material")


class TestGateJudgeRecord(unittest.TestCase):
    """The gate attaches the contract JudgeResult to decision payloads
    (sim mode only — jev_tracker=None keeps the path byte-identical)."""

    def _gate(self, client, tracker):
        audit = AuditLog(":memory:")
        emitted = []
        gate = Gate(client, Thresholds(), [], audit,
                    jev_tracker=tracker, emit=emitted.append)
        self.addCleanup(gate._runner.close)
        return gate, emitted

    def test_fakejev_decision_carries_deterministic_record(self):
        fake = sim_judge.FakeJev()
        tracker = sim_judge.JevSpendTracker(0.50)
        gate, emitted = self._gate(fake, tracker)
        alert = make_alert()
        state = build_state(alert, {}, {})
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "passthrough")  # uncertainty pages
        payload = next(p for p in emitted
                       if p.get("type") == "decision_made")
        jr = payload["body"].get("judge")
        self.assertIsNotNone(jr, "sim mode must attach the JudgeResult")
        self.assertEqual(jr["judgment"], "page")
        self.assertEqual(jr["source"], "deterministic")  # never fake-real
        self.assertEqual(jr["model_version"], "fakejev-0.0.0")
        self.assertEqual(jr["cost_usd"], 0.0)
        self.assertEqual(set(jr.keys()),
                         {"judgment", "confidence", "latency_ms", "source",
                          "model_version", "cost_usd"})

    def test_no_tracker_no_record(self):
        fake = sim_judge.FakeJev()
        gate, emitted = self._gate(fake, None)
        alert = make_alert()
        state = build_state(alert, {}, {})
        gate.evaluate(alert, state, {}, {})
        payload = next(p for p in emitted
                       if p.get("type") == "decision_made")
        self.assertNotIn("judge", payload["body"])

    def test_timer_win_record(self):
        inner = CannedClient(
            response=_canned_response(q3_choice="suppress", q3_conf=0.99),
            delay_s=3.0)
        tracker = sim_judge.JevSpendTracker(0.50)
        client = sim_judge.SpendCappedJevClient(inner, tracker)
        audit = AuditLog(":memory:")
        emitted = []
        gate = Gate(client, Thresholds(), [], audit,
                    race_config=race_mod.RaceConfig.from_raw(500),
                    jev_tracker=tracker, emit=emitted.append)
        self.addCleanup(gate._runner.close)
        alert = make_alert()
        state = build_state(alert, {}, {})
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "passthrough")
        self.assertEqual(disp.reason, "timer_won")
        payload = next(p for p in emitted
                       if p.get("type") == "decision_made")
        jr = payload["body"]["judge"]
        self.assertEqual(jr["source"], "timer")  # the honest timer-win label
        self.assertEqual(jr["judgment"], "page")


class TestSpendEndpoint(unittest.TestCase):
    def _serve(self, tracker):
        audit = AuditLog(":memory:")
        gate = Gate(sim_judge.FakeJev(), Thresholds(), [], audit)
        self.addCleanup(gate._runner.close)
        forwarder = Forwarder(pd_events_url="http://127.0.0.1:1/",
                              default_routing_key="rk-test")
        pipeline = Pipeline(Correlator(), gate, forwarder, audit,
                            ReceiverConfig())
        pipeline.jev_tracker = tracker
        server = make_server(0, pipeline)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def _get(self, base, path):
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.getcode(), json.loads(resp.read())

    def test_spend_endpoint_shape(self):
        tracker = sim_judge.JevSpendTracker(0.50)
        tracker.record_call(0.000042)
        base = self._serve(tracker)
        code, body = self._get(base, "/api/v1/jev/spend")
        self.assertEqual(code, 200)
        self.assertEqual(set(body.keys()),
                         {"session_usd", "budget_usd", "calls", "blocked"})
        self.assertAlmostEqual(body["session_usd"], 0.000042)
        self.assertEqual(body["budget_usd"], 0.50)
        self.assertEqual(body["calls"], 1)
        self.assertFalse(body["blocked"])
        self.assertNotIn(_SENTINEL_KEY, json.dumps(body))

    def test_spend_endpoint_without_tracker(self):
        base = self._serve(None)
        code, body = self._get(base, "/api/v1/jev/spend")
        self.assertEqual(code, 200)
        self.assertEqual(body, {"session_usd": 0.0, "budget_usd": 0.0,
                                "calls": 0, "blocked": False})

    def test_spend_endpoint_reflects_blocked(self):
        tracker = sim_judge.JevSpendTracker(0.0)
        base = self._serve(tracker)
        _code, body = self._get(base, "/api/v1/jev/spend")
        self.assertTrue(body["blocked"])


class TestSpendEndpointAuth(unittest.TestCase):
    """C1: the spend meter requires the operator bearer token when
    SENTINEL_OPERATOR_TOKEN is set (same token as the platform server)."""

    def _serve(self, tracker):
        audit = AuditLog(":memory:")
        gate = Gate(sim_judge.FakeJev(), Thresholds(), [], audit)
        self.addCleanup(gate._runner.close)
        forwarder = Forwarder(pd_events_url="http://127.0.0.1:1/",
                              default_routing_key="rk-test")
        pipeline = Pipeline(Correlator(), gate, forwarder, audit,
                            ReceiverConfig())
        pipeline.jev_tracker = tracker
        server = make_server(0, pipeline)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def _get(self, base, path, token=None):
        req = urllib.request.Request(base + path)
        if token is not None:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.getcode(), json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_spend_unauthenticated_401_when_token_set(self):
        with unittest.mock.patch.dict(os.environ,
                                      {"SENTINEL_OPERATOR_TOKEN": "op-secret"}):
            base = self._serve(sim_judge.JevSpendTracker(0.50))
            code, body = self._get(base, "/api/v1/jev/spend")
            self.assertEqual(code, 401)
            self.assertEqual(body["message"], "unauthorized")

    def test_spend_bogus_bearer_401(self):
        with unittest.mock.patch.dict(os.environ,
                                      {"SENTINEL_OPERATOR_TOKEN": "op-secret"}):
            base = self._serve(sim_judge.JevSpendTracker(0.50))
            code, _body = self._get(base, "/api/v1/jev/spend",
                                    token="wrong-token")
            self.assertEqual(code, 401)

    def test_spend_correct_bearer_200(self):
        with unittest.mock.patch.dict(os.environ,
                                      {"SENTINEL_OPERATOR_TOKEN": "op-secret"}):
            base = self._serve(sim_judge.JevSpendTracker(0.50))
            code, body = self._get(base, "/api/v1/jev/spend",
                                   token="op-secret")
            self.assertEqual(code, 200)
            self.assertEqual(body["budget_usd"], 0.50)


class TestCostCapPressure(unittest.TestCase):
    """Workstream 3 (RFC aiml-ordinality-sweep wave): prove the cost cap under
    thread pressure — not assertions about the mechanism, but a live run.

    Invariants under test:
      1. try_begin_call() gates BEFORE network I/O: no wire attempt may start
         after the tracker reports blocked (checked per-attempt, under threads).
      2. The blocked latch is permanent: no API unlatches it; further attempts
         raise JevBudgetExhausted.
      3. Budget 0.0 authorizes zero spend: N threads, zero wire attempts.
    The overshoot (session_usd - budget_usd) is MEASURED and reported, not
    asserted to zero: pre-authorization admits in-flight calls, so the bound
    is (threads-1) x per-call cost. Honest numbers, not a zero claim.
    """

    def _wire(self, tracker, latency_s=0.002, input_tokens=2000):
        """Fake inner client: records every wire attempt with the tracker's
        blocked state AT ATTEMPT TIME (the gate-before-I/O invariant)."""
        from sentinel.sim_judge import SpendCappedJevClient
        attempts = []
        lock = threading.Lock()

        class FakeWire:
            model = "jev-1.13.0"
            timeout_s = 8.0

            def decide(self, state, questions):
                with lock:
                    attempts.append(tracker.blocked)
                time.sleep(latency_s)  # the network round-trip
                return _canned_response(input_tokens=input_tokens)

        return SpendCappedJevClient(FakeWire(), tracker), attempts

    def _hammer(self, client, n_threads=32, n_per_thread=10):
        errors = []
        lock = threading.Lock()

        def worker():
            for _ in range(n_per_thread):
                try:
                    client.decide({}, {})
                except Exception as exc:  # noqa: BLE001 - counted, not hidden
                    with lock:
                        errors.append(type(exc).__name__)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return errors

    def test_gate_before_io_under_pressure(self):
        from sentinel.sim_judge import JevSpendTracker
        # Budget admits ~12 calls at $0.000084/call; 320 attempts race it.
        tracker = JevSpendTracker(budget_usd=0.001)
        client, attempts = self._wire(tracker)
        errors = self._hammer(client)
        # INVARIANT 1: no wire attempt started after the latch engaged.
        late = [a for a in attempts if a]
        self.assertEqual(late, [],
                         f"{len(late)} wire attempts started after blocked")
        # INVARIANT 2: the latch is permanent.
        self.assertTrue(tracker.blocked)
        for _ in range(10):
            with self.assertRaises(sim_judge.JevBudgetExhausted):
                client.decide({}, {})
        self.assertTrue(tracker.blocked)
        # MEASURED, not asserted: the pre-authorization overshoot.
        overshoot = tracker.session_usd - 0.001
        print(f"\n[pressure] attempts={len(attempts)} blocked-late={len(late)} "
              f"session_usd={tracker.session_usd:.6f} overshoot_usd={overshoot:.6f} "
              f"budget_errors={errors.count('JevBudgetExhausted')}")
        self.assertLessEqual(overshoot, 32 * 2000 * 0.042 / 1_000_000)

    def test_zero_budget_zero_spend_under_pressure(self):
        from sentinel.sim_judge import JevSpendTracker, JevBudgetExhausted
        tracker = JevSpendTracker(budget_usd=0.0)
        self.assertTrue(tracker.blocked)
        client, attempts = self._wire(tracker)
        errors = self._hammer(client, n_threads=16, n_per_thread=5)
        self.assertEqual(attempts, [], "wire I/O attempted on a 0.0 budget")
        self.assertEqual(tracker.session_usd, 0.0)
        self.assertTrue(all(e == "JevBudgetExhausted" for e in errors))
        self.assertEqual(len(errors), 80)

    def test_negative_budget_fail_closed(self):
        from sentinel.sim_judge import JevSpendTracker
        tracker = JevSpendTracker(budget_usd=-5.0)
        self.assertTrue(tracker.blocked)
        client, attempts = self._wire(tracker)
        with self.assertRaises(sim_judge.JevBudgetExhausted):
            client.decide({}, {})
        self.assertEqual(attempts, [])


if __name__ == "__main__":
    unittest.main()
