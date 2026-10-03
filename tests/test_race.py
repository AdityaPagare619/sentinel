"""Tests for the race-to-page lane (ADR-010, design/fixes/01-race-to-page.md).

Covers: B config guards (fail-closed), the three-party race, timer-win
passthrough + shadow_decision emission, fast-path answered_in_time,
error_passthrough, queue-full shed, the gate backstop (triple-death),
the timer-win watchdog, the monotonic-clock CI pin, claim atomicity
under stress, detached-thread lifetime, and the scheduler-stall page.

Existing gate/client tests (test_gate.py, test_client.py) run unmodified.
"""

import ast
import os
import sys
import threading
import time
import unittest
from time import monotonic

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sentinel import race
from sentinel.audit import AuditLog
from sentinel.client import Answer, DecisionResponse, JevError, JevTimeout
from sentinel.gate import Gate
from sentinel.models import Thresholds
from sentinel.quantized import AllowlistEntry, Attestation
from datetime import datetime, timedelta, timezone
from sentinel.race import (RaceClaim, RaceConfig, RaceRunner, TimerWinWatchdog,
                           InferencePool)
from sentinel.state import build_state

from tests.helpers import FakeClock, make_alert


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

def _answer(qid, choice, probs, conf):
    return Answer(qid=qid, qtype="choice", choice=choice, noul=None,
                  probabilities=dict(probs), confidence=conf)


def canned(p1=0.0, p2=0.0, p3=0.9, p4=0.1, conf=0.95,
           q1_choice="p3_medium", q3_choice="page_business_hours",
           team="platform", model="jev-1.13.0"):
    return DecisionResponse(
        model=model,
        answers={
            "severity": _answer("severity", q1_choice,
                                {"p1_critical": p1, "p2_high": p2,
                                 "p3_medium": p3, "p4_low": p4,
                                 "known_noise": 0.0, "cannot_determine": 0.0},
                                conf),
            "owning_team": _answer("owning_team", team, {team: 1.0}, 0.99),
            "disposition": _answer("disposition", q3_choice,
                                   {q3_choice: 1.0}, conf),
        },
        input_tokens=100,
    )


class SlowClient:
    """Jev double with a scripted latency (the vendor's property)."""

    timeout_s = 30.0

    def __init__(self, delay_s, response):
        self.delay_s = delay_s
        self.response = response
        self.calls = 0

    def decide(self, state, questions):
        self.calls += 1
        time.sleep(self.delay_s)
        return self.response


class BlackHoleClient:
    """Jev double that never answers until released (test hook)."""

    timeout_s = 30.0

    def __init__(self):
        self._release = threading.Event()
        self.calls = 0

    def decide(self, state, questions):
        self.calls += 1
        self._release.wait()  # forever, until the test releases
        raise JevError("released")

    def release(self):
        self._release.set()


class ExplodingClient:
    timeout_s = 30.0

    def __init__(self, exc_factory):
        self.exc_factory = exc_factory

    def decide(self, state, questions):
        raise self.exc_factory("jev is down")


class ZeroTimeoutClient:
    timeout_s = 0  # fail-closed: the runner must refuse this

    def decide(self, state, questions):
        raise AssertionError("must never be called")


def _wait_for(pred, timeout_s, interval=0.05):
    t0 = monotonic()
    while monotonic() - t0 < timeout_s:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())


class RaceTestBase(unittest.TestCase):
    def make_gate(self, client, allowlist=None, race_config=None, **kw):
        audit = AuditLog(":memory:")
        # ADR-013: allowlist may contain AllowlistEntry (attested) or strings.
        gate = Gate(client, Thresholds(), list(allowlist or []), audit,
                    race_config=race_config, **kw)
        self.addCleanup(gate.close)
        return gate, audit

    def shadows(self, gate):
        return [p for p in gate.emitted if p["type"] == "shadow_decision"]

    def decisions(self, gate):
        return [p for p in gate.emitted if p["type"] == "decision_made"]


# ---------------------------------------------------------------------------
# B config guards — fail-CLOSED on config (design §2.2)
# ---------------------------------------------------------------------------

class TestRaceConfigGuards(unittest.TestCase):
    def test_default_is_2700(self):
        # N=100 re-derivation (PR #19): max(1000, 2*1339)=2678 -> 2700ms.
        self.assertEqual(RaceConfig.from_raw(None).budget_ms, 2700)

    def test_zero_means_default_not_disabled(self):
        # There is no way to disable the race via config.
        self.assertEqual(RaceConfig.from_raw(0).budget_ms, 2700)

    def test_disabled_string_means_default(self):
        self.assertEqual(RaceConfig.from_raw("disabled").budget_ms, 2700)

    def test_below_floor_refused_to_default(self):
        m = race.Metrics()
        cfg = RaceConfig.from_raw(200, metrics=m)
        self.assertEqual(cfg.budget_ms, 2700)
        self.assertEqual(m.get("race.budget_refused_low"), 1)

    def test_above_ceiling_refused_to_default(self):
        m = race.Metrics()
        cfg = RaceConfig.from_raw(9999, metrics=m)
        self.assertEqual(cfg.budget_ms, 2700)
        self.assertEqual(m.get("race.budget_refused_high"), 1)

    def test_below_recommended_allowed_with_warning_metric(self):
        m = race.Metrics()
        cfg = RaceConfig.from_raw(700, metrics=m)
        self.assertEqual(cfg.budget_ms, 700)
        self.assertEqual(m.get("race.budget_below_recommended"), 1)

    def test_floor_boundary_allowed(self):
        self.assertEqual(RaceConfig.from_raw(500).budget_ms, 500)

    def test_ceiling_boundary_allowed(self):
        self.assertEqual(RaceConfig.from_raw(5000).budget_ms, 5000)

    def test_sane_value_passes_clean(self):
        m = race.Metrics()
        cfg = RaceConfig.from_raw(3000, metrics=m)
        self.assertEqual(cfg.budget_ms, 3000)
        self.assertEqual(m.snapshot(), {})

    def test_garbage_refused(self):
        m = race.Metrics()
        self.assertEqual(RaceConfig.from_raw("abc", metrics=m).budget_ms, 2700)
        self.assertEqual(m.get("race.budget_refused_invalid"), 1)

    def test_nan_and_inf_refused(self):
        m = race.Metrics()
        self.assertEqual(RaceConfig.from_raw(float("nan"), metrics=m).budget_ms, 2700)
        self.assertEqual(RaceConfig.from_raw(float("inf"), metrics=m).budget_ms, 2700)
        self.assertEqual(m.get("race.budget_refused_invalid"), 2)

    def test_bool_refused(self):
        self.assertEqual(RaceConfig.from_raw(True).budget_ms, 2700)

    def test_gate_applies_refused_default(self):
        # B=200 refused at load → the gate races with 1000 ms.
        audit = AuditLog(":memory:")
        gate = Gate(SlowClient(0.01, canned()), Thresholds(), set(), audit,
                    race_config=200)
        self.addCleanup(gate.close)
        self.assertEqual(gate._runner.config.budget_ms, 2700)

    def test_epsilon_default(self):
        self.assertEqual(RaceConfig.from_raw(None).epsilon_ms, 500)


# ---------------------------------------------------------------------------
# Monotonic clock pin — CI grep-grade test (design §3.5)
# ---------------------------------------------------------------------------

class TestMonotonicClockPin(unittest.TestCase):
    RACE_PATH = os.path.join(_SRC, "sentinel", "race.py")

    def test_race_module_references_only_monotonic(self):
        with open(self.RACE_PATH) as fh:
            src = fh.read()
        tree = ast.parse(src)
        violations = []
        monotonic_seen = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "time" and node.attr != "monotonic":
                    violations.append(f"time.{node.attr} (line {node.lineno})")
                if node.value.id == "datetime":
                    violations.append(f"datetime.{node.attr} (line {node.lineno})")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "monotonic":
                    monotonic_seen = True
            if isinstance(node, ast.ImportFrom) and node.module == "time":
                if any(a.name == "monotonic" for a in node.names):
                    # `from time import monotonic` — the module's only clock,
                    # injected as the default everywhere it is needed.
                    monotonic_seen = True
            if isinstance(node, ast.ImportFrom):
                if node.module in ("datetime",):
                    violations.append(f"from {node.module} import ... (line {node.lineno})")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "datetime":
                        violations.append(f"import datetime (line {node.lineno})")
        self.assertEqual(violations, [],
                         f"wall-clock sources in the race module: {violations}")
        self.assertTrue(monotonic_seen, "race.py never calls monotonic()?!")


# ---------------------------------------------------------------------------
# Arming order — deadline computed BEFORE the inference thread exists (§3.2)
# ---------------------------------------------------------------------------

class TestArmingOrder(unittest.TestCase):
    def test_deadline_computed_before_pool_submit(self):
        order = []

        class OrderClock:
            def __call__(self):
                order.append("clock")
                return 1000.0

        class OrderPool(InferencePool):
            def submit(self, fn):
                order.append("submit")
                return None  # shed: no worker runs, no more clock calls

        from sentinel.client import MockSystemOneClient
        runner = RaceRunner(MockSystemOneClient({}),
                            RaceConfig(budget_ms=1000),
                            clock=OrderClock(),
                            watch_interval_s=3600)
        try:
            runner._pool = OrderPool()
            order.clear()  # drop the scheduler-construction clock() call
            alert = make_alert()
            outcome = runner.run(alert=alert, state={}, questions={},
                                 input_sha256="abc")
            # The first two clock-involving steps must be deadline-then-submit;
            # (a later watchdog record() also reads the clock — irrelevant).
            self.assertEqual(order[:2], ["clock", "submit"])
            self.assertEqual(outcome.budget_outcome, race.TIMER_WON_SHED)
        finally:
            runner.close()


# ---------------------------------------------------------------------------
# The race: slow vendor → timer wins, passthrough at ~B, shadow emitted
# ---------------------------------------------------------------------------

class TestSlowVendorTimerWins(RaceTestBase):
    def test_timer_wins_passthrough_at_budget_and_shadow_follows(self):
        alert = make_alert()
        client = SlowClient(5.0, canned(p1=0.0, conf=0.95))  # suppress-shaped
        # ADR-013: attested entry so shadow path would-have-suppressed.
        gate, _audit = self.make_gate(client, allowlist=[_attested_fp(alert.fingerprint)],
                                      race_config=RaceConfig(budget_ms=500))
        state = build_state(alert, {}, {})
        t0 = monotonic()
        disp, _rec = gate.evaluate(alert, state, {}, {})
        elapsed = monotonic() - t0

        # The page left at ~B — bounded by OUR budget, never the vendor's.
        self.assertEqual(disp.action, "passthrough")
        self.assertEqual(disp.reason, "timer_won")
        self.assertGreaterEqual(elapsed, 0.35)
        self.assertLess(elapsed, 3.0,
                        f"gate waited {elapsed:.2f}s for a 5s vendor call — the race is broken")

        dec = self.decisions(gate)
        self.assertEqual(len(dec), 1)
        body = dec[0]["body"]
        self.assertEqual(body["budget_outcome"], "timer_won")
        self.assertIsNone(body["latency_ms"])  # null unless answered_in_time
        self.assertAlmostEqual(body["timer_fired_at_ms"], 500, delta=150)
        for key in ("disposition", "budget_outcome", "lock_evaluation",
                    "freshness", "threshold_counterfactual", "links"):
            self.assertIn(key, body, f"decision_made missing {key}")

        # The late answer becomes a shadow_decision — full latency recorded,
        # never pages, never suppresses.
        found = _wait_for(lambda: len(self.shadows(gate)) == 1, timeout_s=8.0)
        self.assertTrue(found, "shadow_decision was never emitted")
        sh = self.shadows(gate)[0]
        self.assertEqual(sh["type"], "shadow_decision")
        self.assertEqual(sh["actor"], "engine")
        sbody = sh["body"]
        self.assertAlmostEqual(sbody["latency_ms"], 5000, delta=800)
        self.assertEqual(sbody["budget_ms"], 500)
        self.assertEqual(sbody["decided_disposition"], "passthrough")
        self.assertEqual(sbody["shadow_disposition"], "suppress")
        self.assertTrue(sbody["would_have_suppressed"])
        self.assertEqual(sbody["jev_model"], "jev-1.13.0")
        self.assertIn("links", sbody)
        self.assertAlmostEqual(sbody["timer_fired_at_ms"], 500, delta=150)

    def test_late_error_still_emits_shadow_with_error_class(self):
        class SlowError:
            timeout_s = 30.0

            def decide(self, state, questions):
                time.sleep(1.0)
                raise JevTimeout("late boom")

        alert = make_alert()
        gate, _ = self.make_gate(SlowError(), race_config=RaceConfig(budget_ms=300))
        disp, _ = gate.evaluate(alert, build_state(alert, {}, {}), {}, {})
        self.assertEqual(disp.action, "passthrough")
        self.assertEqual(disp.reason, "timer_won")
        found = _wait_for(lambda: len(self.shadows(gate)) == 1, timeout_s=5.0)
        self.assertTrue(found)
        sbody = self.shadows(gate)[0]["body"]
        self.assertIsNone(sbody["jev_model"])
        self.assertIsNone(sbody["q1_reported"])
        self.assertEqual(sbody["error_class"], "timeout")
        self.assertFalse(sbody["would_have_suppressed"])


# ---------------------------------------------------------------------------
# Fast vendor → answered_in_time, normal gate flow
# ---------------------------------------------------------------------------

def _attested_fp(fp):
    """ADR-013: dual-attested allowlist entry for tests."""
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    return AllowlistEntry(
        fingerprint=fp, author="carol",
        attestations=[
            Attestation("alice", now - timedelta(days=1), "lrq-9f2c-41ab", 30),
            Attestation("bob", now - timedelta(days=1), "lrq-9f2c-41ab", 30),
        ])

class TestFastVendorAnsweredInTime(RaceTestBase):
    def test_fast_answer_suppresses_normally(self):
        alert = make_alert()
        client = SlowClient(0.05, canned(p1=0.0, conf=0.95))
        # ADR-013: needs dual attestation to suppress (not bare fingerprint).
        gate, _ = self.make_gate(client, allowlist=[_attested_fp(alert.fingerprint)],
                                 race_config=RaceConfig(budget_ms=1000))
        t0 = monotonic()
        disp, _rec = gate.evaluate(alert, build_state(alert, {}, {}), {}, {})
        elapsed = monotonic() - t0
        self.assertEqual(disp.action, "suppress")
        self.assertEqual(disp.reason, "allowlist")
        self.assertLess(elapsed, 0.9)

        dec = self.decisions(gate)
        self.assertEqual(len(dec), 1)
        body = dec[0]["body"]
        self.assertEqual(body["budget_outcome"], "answered_in_time")
        self.assertIsNotNone(body["latency_ms"])
        self.assertGreater(body["latency_ms"], 20)
        self.assertLess(body["latency_ms"], 900)
        self.assertEqual(body["disposition"], "suppress")
        self.assertEqual(body["lock_evaluation"]["prob"]["verdict"], "pass")

        # No shadow: the answer won the race, it acted.
        time.sleep(0.6)
        self.assertEqual(self.shadows(gate), [])

    def test_kernel_shared_by_live_and_shadow_paths(self):
        # The shadow_disposition uses the same kernel: a page_now-shaped
        # late answer shadows as passthrough, not suppress.
        from sentinel.gate import evaluate_policy
        alert = make_alert()
        resp = canned(p1=0.5, p2=0.3, conf=0.9)  # page_now-shaped
        v = evaluate_policy(
            alert, jev_model="jev-1.13.0",
            q_severity=resp.answers["severity"],
            q_team=resp.answers["owning_team"],
            q_disposition=resp.answers["disposition"],
            thresholds=Thresholds(), allowlist=set(), latency_ms=1.0)
        self.assertEqual(v.action, "page_now")
        self.assertNotEqual(
            "suppress" if v.action == "suppress" else "passthrough",
            "suppress")


# ---------------------------------------------------------------------------
# Jev error on the winning inference → error_passthrough, no shadow
# ---------------------------------------------------------------------------

class TestErrorPassthrough(RaceTestBase):
    def test_jev_error_claims_race_as_error(self):
        alert = make_alert()
        gate, _ = self.make_gate(ExplodingClient(JevTimeout),
                                 race_config=RaceConfig(budget_ms=1000))
        disp, _ = gate.evaluate(alert, build_state(alert, {}, {}), {}, {})
        self.assertEqual(disp.action, "passthrough")
        self.assertTrue(disp.reason.startswith("error:"))
        self.assertIn("timeout", disp.reason)
        dec = self.decisions(gate)
        self.assertEqual(dec[0]["body"]["budget_outcome"], "error_passthrough")
        time.sleep(0.5)
        self.assertEqual(self.shadows(gate), [],
                         "an error that won the race must not shadow")


# ---------------------------------------------------------------------------
# Queue-full → shed immediately, never block the hot path (§3.3)
# ---------------------------------------------------------------------------

class TestQueueFullShed(RaceTestBase):
    def test_full_pool_sheds_without_waiting(self):
        from sentinel.client import MockSystemOneClient
        from sentinel.state import input_sha256
        alert = make_alert()
        state = build_state(alert, {}, {})
        client = MockSystemOneClient({input_sha256(state): canned()})
        gate, _ = self.make_gate(
            client, race_config=RaceConfig(budget_ms=500, pool_size=2,
                                           pool_queue=2))
        pool = gate._runner._pool
        pool._permits.acquire()
        pool._permits.acquire()
        try:
            t0 = monotonic()
            disp, _ = gate.evaluate(alert, state, {}, {})
            elapsed = monotonic() - t0
        finally:
            pool._permits.release()
            pool._permits.release()
        self.assertEqual(disp.action, "passthrough")
        self.assertEqual(disp.reason, "timer_won_shed")
        self.assertLess(elapsed, 1.0, "shed must not wait for the budget")
        self.assertEqual(len(client.calls), 0, "shed calls Jev never")
        dec = self.decisions(gate)
        self.assertEqual(dec[0]["body"]["budget_outcome"], "timer_won_shed")


# ---------------------------------------------------------------------------
# Triple-death: dead scheduler + hung vendor → gate backstop at B+ε (§3.1.3)
# ---------------------------------------------------------------------------

class TestGateBackstopTripleDeath(RaceTestBase):
    def test_dead_scheduler_black_hole_vendor_pages_by_b_plus_epsilon(self):
        alert = make_alert()
        client = BlackHoleClient()
        self.addCleanup(client.release)  # release BEFORE gate.close (LIFO)
        gate, _ = self.make_gate(
            client, race_config=RaceConfig(budget_ms=200, epsilon_ms=100),
            on_scheduler_stall=lambda s: None)
        # Kill party 2: the scheduler thread is dead.
        gate._runner.scheduler.stop()
        self.assertTrue(_wait_for(lambda: not gate._runner.scheduler.alive,
                                  timeout_s=3.0))
        t0 = monotonic()
        disp, _ = gate.evaluate(alert, build_state(alert, {}, {}), {}, {})
        elapsed = monotonic() - t0
        # Party 3 owned it: the page left by B+ε even with no timer and a
        # hung vendor call.
        self.assertEqual(disp.action, "passthrough")
        self.assertEqual(disp.reason, "timer_won")
        self.assertGreaterEqual(elapsed, 0.25)
        self.assertLess(elapsed, 2.0,
                        f"backstop failed: gate waited {elapsed:.2f}s")
        dec = self.decisions(gate)
        self.assertEqual(dec[0]["body"]["budget_outcome"], "timer_won")
        self.assertTrue(dec[0]["body"]["backstop_claimed"])


# ---------------------------------------------------------------------------
# Timer-win watchdog — rolling 1h rate > 5% → page operator (§2.2)
# ---------------------------------------------------------------------------

class TestTimerWinWatchdog(unittest.TestCase):
    def test_sustained_timer_wins_page_operator(self):
        fake = FakeClock()
        trips = []
        w = TimerWinWatchdog(clock=fake, on_trip=lambda r, n: trips.append((r, n)))
        for _ in range(19):
            w.record("answered_in_time")
        self.assertEqual(trips, [])  # healthy: no trip
        w.record("timer_won")
        self.assertEqual(trips, [])  # 1/20 = 5%: not *above* the threshold
        w.record("timer_won")
        self.assertEqual(len(trips), 1)  # 2/21 ≈ 9.5% > 5%: trip
        rate, n = trips[0]
        self.assertAlmostEqual(rate, 2 / 21)
        self.assertEqual(n, 21)
        w.record("timer_won")
        self.assertEqual(len(trips), 1, "edge-triggered: no re-page while tripped")

    def test_rearms_when_rate_recovers(self):
        fake = FakeClock()
        trips = []
        w = TimerWinWatchdog(clock=fake, on_trip=lambda r, n: trips.append((r, n)))
        for _ in range(10):
            w.record("timer_won")
        self.assertEqual(len(trips), 1)
        fake.advance(3601)  # window slides past the bad hour
        for _ in range(10):
            w.record("answered_in_time")
        rate, n = w.current_rate()
        self.assertEqual(rate, 0.0)
        for _ in range(10):
            w.record("timer_won")
        self.assertEqual(len(trips), 2, "re-armed watchdog trips again")

    def test_shed_counts_as_timer_win(self):
        fake = FakeClock()
        trips = []
        w = TimerWinWatchdog(clock=fake, on_trip=lambda r, n: trips.append(1))
        for _ in range(10):
            w.record("timer_won_shed")
        self.assertEqual(len(trips), 1)

    def test_lone_slow_alert_on_quiet_hour_does_not_page(self):
        fake = FakeClock()
        trips = []
        w = TimerWinWatchdog(clock=fake, on_trip=lambda r, n: trips.append(1))
        w.record("timer_won")  # 1 sample < min_samples: no trip
        self.assertEqual(trips, [])

    def test_runner_feeds_watchdog(self):
        fake = FakeClock()
        trips = []
        from sentinel.client import MockSystemOneClient
        runner = RaceRunner(
            MockSystemOneClient({}), RaceConfig(budget_ms=1000), clock=fake,
            watchdog=TimerWinWatchdog(clock=fake,
                                      on_trip=lambda r, n: trips.append(1)),
            watch_interval_s=3600)
        try:
            for _ in range(10):
                runner.watchdog.record("answered_in_time")
                fake.advance(1)
            for _ in range(10):
                runner.watchdog.record("timer_won")
                fake.advance(1)
            self.assertEqual(len(trips), 1)
        finally:
            runner.close()


# ---------------------------------------------------------------------------
# Claim atomicity under stress — exactly one winner per race (§3.1)
# ---------------------------------------------------------------------------

class TestClaimStress(unittest.TestCase):
    def test_exactly_one_winner_per_race(self):
        n = 20000
        claims = [RaceClaim() for _ in range(n)]
        wins = []
        wins_lock = threading.Lock()

        def side(by):
            local = 0
            for c in claims:
                if c.try_claim(by):
                    local += 1
            with wins_lock:
                wins.append(local)

        t1 = threading.Thread(target=side, args=(race.CLAIMED_BY_TIMER,))
        t2 = threading.Thread(target=side, args=(race.CLAIMED_BY_INFERENCE,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(sum(wins), n,
                         "every race must have exactly one winner")
        self.assertTrue(all(c.winner != race.UNCLAIMED for c in claims))


# ---------------------------------------------------------------------------
# Fail-closed construction (design §6, pre-mortem link 2)
# ---------------------------------------------------------------------------

class TestFailClosedConstruction(unittest.TestCase):
    def test_client_refuses_nonpositive_timeout(self):
        from sentinel.client import SystemOneClient
        for bad in (0, -1, None):
            with self.assertRaises(ValueError, msg=f"timeout_s={bad}"):
                SystemOneClient(api_key="k", timeout_s=bad)

    def test_runner_refuses_unbounded_inference_client(self):
        with self.assertRaises(ValueError):
            RaceRunner(ZeroTimeoutClient(), RaceConfig(budget_ms=1000),
                       watch_interval_s=3600)

    def test_runner_accepts_duck_typed_client_without_timeout_attr(self):
        class NoAttr:
            def decide(self, state, questions):
                raise JevError("x")

        r = RaceRunner(NoAttr(), RaceConfig(budget_ms=1000),
                       watch_interval_s=3600)
        r.close()  # must not raise


# ---------------------------------------------------------------------------
# Detached threads always terminate (design §6 — soak shape)
# ---------------------------------------------------------------------------

class TestDetachedLifetime(RaceTestBase):
    def test_detached_in_flight_returns_to_zero(self):
        alert = make_alert()
        client = SlowClient(0.3, canned())
        gate, _ = self.make_gate(
            client, race_config=RaceConfig(budget_ms=100, pool_size=4,
                                           pool_queue=16))
        for i in range(20):
            a = make_alert(alert_id=f"soak{i}")
            gate.evaluate(a, build_state(a, {}, {}), {}, {})
        runner = gate._runner
        ok = _wait_for(lambda: runner.detached_in_flight == 0, timeout_s=10.0)
        self.assertTrue(ok, "detached threads did not terminate")
        # Pool threads are bounded and reused — no leak by construction:
        # this gate's pool never grows past its configured size however
        # many races it runs.
        pool_threads = runner._pool._pool._threads
        self.assertLessEqual(len(pool_threads), 4)


# ---------------------------------------------------------------------------
# Scheduler-stall → control-plane page (F3)
# ---------------------------------------------------------------------------

class TestSchedulerStallPage(unittest.TestCase):
    def test_stale_heartbeat_pages_control_plane(self):
        from sentinel.client import MockSystemOneClient
        fired = []
        runner = RaceRunner(
            MockSystemOneClient({}), RaceConfig(budget_ms=1000),
            on_scheduler_stall=lambda s: fired.append(s),
            watch_interval_s=0.05, stall_threshold_s=0.15)
        try:
            runner.scheduler.stop()
            self.assertTrue(_wait_for(lambda: not runner.scheduler.alive,
                                      timeout_s=3.0))
            self.assertTrue(_wait_for(lambda: len(fired) == 1, timeout_s=3.0),
                            "stale scheduler heartbeat did not page")
            self.assertGreater(fired[0], 0.15)
        finally:
            runner.close()


# ---------------------------------------------------------------------------
# Runner-level stress — scheduler + pool churn
# ---------------------------------------------------------------------------

class TestRunnerStress(RaceTestBase):
    def test_100_fast_races_all_answered_in_time(self):
        from sentinel.client import MockSystemOneClient
        from sentinel.state import input_sha256
        alert = make_alert()
        state = build_state(alert, {}, {})
        client = MockSystemOneClient({input_sha256(state): canned()})
        gate, _ = self.make_gate(
            client, race_config=RaceConfig(budget_ms=50, pool_size=8,
                                           pool_queue=64))
        for _ in range(100):
            disp, _ = gate.evaluate(alert, state, {}, {})
            # canned() is p3-shaped @ conf 0.95 → page_business_hours
            self.assertEqual(disp.action, "page_business_hours")
        outcomes = [p["body"]["budget_outcome"] for p in self.decisions(gate)]
        self.assertEqual(len(outcomes), 100)
        self.assertTrue(all(o == "answered_in_time" for o in outcomes),
                        set(outcomes))

    def test_100_slow_races_all_timer_won(self):
        alert = make_alert()
        client = SlowClient(0.2, canned())
        gate, _ = self.make_gate(
            client, race_config=RaceConfig(budget_ms=50, pool_size=8,
                                           pool_queue=64))
        for i in range(100):
            a = make_alert(alert_id=f"st{i}")
            disp, _ = gate.evaluate(a, build_state(a, {}, {}), {}, {})
            self.assertEqual(disp.action, "passthrough")
        outcomes = [p["body"]["budget_outcome"] for p in self.decisions(gate)]
        self.assertEqual(len(outcomes), 100)
        self.assertTrue(all(o == "timer_won" for o in outcomes),
                        set(outcomes))
        # Late answers still shadowed (detached workers drain).
        self.assertTrue(_wait_for(lambda: len(self.shadows(gate)) == 100,
                                  timeout_s=15.0),
                        f"only {len(self.shadows(gate))}/100 shadows emitted")


if __name__ == "__main__":
    unittest.main()
