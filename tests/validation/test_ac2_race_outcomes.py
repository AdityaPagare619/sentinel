"""AC-2 — Race outcomes: all six exercised, correct disposition + source.

Executable now: 2a-2d + gate backstop (race.py + gate wiring exist).
BLOCKED on Track 2: 2e (FakeJev judge adapter), 2f (spend/budget),
the `source` field vocabulary on the record.
"""

import os
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from common import (FastJev, SlowJev, ExplodingJev, SuppressRig,  # noqa: E402
                    make_alert, canned, timed)  # noqa: E402
from sentinel.client import JevTimeout, JevOverloaded  # noqa: E402
from sentinel.race import (RaceConfig, RaceRunner, ANSWERED_IN_TIME,  # noqa: E402
                           TIMER_WON, TIMER_WON_SHED, ERROR_PASSTHROUGH,
                           CLAIMED_BY_INFERENCE, CLAIMED_BY_TIMER)  # noqa: E402

TRACK2_MISSING = ("BLOCKED: Track 2 judge adapter not merged — "
                  "FakeJev, spend endpoint, and the JudgeResult `source` "
                  "field are unavailable on this branch")


def _rig_with_client(client, budget_ms=2700, pool_size=4, pool_queue=64):
    rig = SuppressRig(model="fakejev-t7-ac2")
    alert = make_alert(alert_id="t7-ac2", service="web", check="http_5xx")
    rig.add_fingerprint(alert.fingerprint)
    rig.boot()
    # Rewire the gate's race config for the case under test. The Gate
    # builds its own RaceRunner from race_config; swap in a fresh one.
    from sentinel.race import Metrics
    old = rig.gate._runner
    cfg = RaceConfig(budget_ms=budget_ms, pool_size=pool_size,
                     pool_queue=pool_queue)
    rig.gate._runner = RaceRunner(
        client, cfg, clock=old._clock,
        late_answer_hook=rig.gate._on_late_answer)
    old.close()
    rig.client = client
    return rig, alert


class TestAC2RaceOutcomes(unittest.TestCase):
    """2a-2d + backstop against the real race."""

    def test_2a_judge_wins_suppress(self):
        """Fast fake, generous budget: judge wins, suppress is earned."""
        rig, alert = _rig_with_client(FastJev(canned(model="fakejev-t7")),
                                      budget_ms=2700)
        self.addCleanup(rig.close)
        (disp, rec, payload, state), ms = timed(
            lambda: rig.drive(alert))
        self.assertEqual(disp.action, "suppress", disp.reason)
        self.assertEqual(payload["body"]["budget_outcome"],
                         ANSWERED_IN_TIME)
        # Honest labeling: the fake names itself, never the real vendor.
        self.assertIn("fakejev", (payload["body"]["jev_model"] or ""))
        print(f"\n[AC-2a] judge-wins suppress in {ms:.1f} ms")

    def test_2b_timer_wins_forced_short_timeout(self):
        """Budget 500ms (floor), fake latency 1500ms: the timer MUST win."""
        rig, alert = _rig_with_client(SlowJev(canned(model="fakejev-t7")),
                                      budget_ms=500)
        self.addCleanup(rig.close)
        (disp, rec, payload, state), ms = timed(
            lambda: rig.drive(alert))
        self.assertEqual(disp.action, "passthrough", disp.reason)
        body = payload["body"]
        self.assertEqual(body["budget_outcome"], TIMER_WON)
        fired = body["timer_fired_at_ms"]
        self.assertIsNotNone(fired)
        # Measured, not asserted: the timer fired at ~the budget.
        self.assertLess(fired, 500 + 250,
                        f"timer fired at {fired} ms, budget was 500 ms")
        self.assertLess(ms, 500 + 500 + 750,
                        f"decision took {ms:.0f} ms, bound is B+eps+slack")
        print(f"\n[AC-2b] timer-wins passthrough: fired_at={fired:.0f} ms, "
              f"total={ms:.0f} ms")

    def test_2c_jev_error_passthrough(self):
        """Client raises on every call: deterministic error passthrough."""
        rig, alert = _rig_with_client(ExplodingJev(JevTimeout),
                                      budget_ms=500)
        self.addCleanup(rig.close)
        (disp, rec, payload, state), ms = timed(
            lambda: rig.drive(alert))
        self.assertEqual(disp.action, "passthrough", disp.reason)
        self.assertTrue(disp.reason.startswith("error:"),
                        f"reason {disp.reason!r} must name the error")
        self.assertEqual(payload["body"]["budget_outcome"],
                         ERROR_PASSTHROUGH)
        # Fail-open must be fast: no reason to wait out the budget.
        self.assertLess(ms, 500 + 500 + 750)
        print(f"\n[AC-2c] jev-error passthrough in {ms:.1f} ms "
              f"(reason={disp.reason})")

    def test_2d_overload_sheds_immediately(self):
        """Pool queue size 0: submit returns None -> timer_won_shed, no
        waiting on the hot path."""
        rig, alert = _rig_with_client(FastJev(canned(model="fakejev-t7")),
                                      budget_ms=2700,
                                      pool_size=1, pool_queue=0)
        self.addCleanup(rig.close)
        (disp, rec, payload, state), ms = timed(
            lambda: rig.drive(alert))
        self.assertEqual(disp.action, "passthrough", disp.reason)
        self.assertEqual(payload["body"]["budget_outcome"], TIMER_WON_SHED)
        # Overload sheds inference, never pages, never waits.
        self.assertLess(ms, 1000,
                        f"shed path took {ms:.0f} ms — must not wait")
        print(f"\n[AC-2d] overload shed in {ms:.1f} ms")

    def test_2x_backstop_dead_scheduler(self):
        """Triple-death shape: with the scheduler stopped, the gate's own
        B+eps backstop still resolves the decision."""
        rig, alert = _rig_with_client(SlowJev(canned(model="fakejev-t7")),
                                      budget_ms=500)
        self.addCleanup(rig.close)
        rig.gate._runner.scheduler.stop()
        time.sleep(0.1)
        (disp, rec, payload, state), ms = timed(
            lambda: rig.drive(alert))
        self.assertEqual(disp.action, "passthrough")
        self.assertEqual(payload["body"]["budget_outcome"], TIMER_WON)
        self.assertTrue(payload["body"]["backstop_claimed"],
                        "gate backstop must own the claim when the timer "
                        "is dead")
        self.assertLess(ms, 500 + 500 + 750,
                        f"backstop took {ms:.0f} ms, bound B+eps+slack")
        print(f"\n[AC-2x] dead-scheduler backstop resolved in {ms:.0f} ms")

    def test_2b_late_answer_becomes_shadow_only(self):
        """The detached slow call's late answer must never page, suppress,
        or re-open the decision."""
        rig, alert = _rig_with_client(SlowJev(canned(model="fakejev-t7")),
                                      budget_ms=500)
        self.addCleanup(rig.close)
        disp, rec, payload, state = rig.drive(alert)
        self.assertEqual(disp.action, "passthrough")
        # Wait for the detached 1.5 s call to land.
        time.sleep(2.0)
        shadows = [p for p in rig.gate.emitted
                   if p["type"] == "shadow_decision"]
        self.assertGreaterEqual(
            len(shadows), 1,
            "the late answer must be recorded as a shadow_decision")
        # Re-drive the same alert: the shadow must not change anything.
        disp2, rec2, payload2, _ = rig.drive(alert)
        self.assertEqual(disp2.action, disp.action)
        print(f"\n[AC-2b-late] {len(shadows)} shadow_decision row(s); "
              f"disposition unchanged after late answer")

    @unittest.skip(TRACK2_MISSING)
    def test_2e_key_absent_fakejev_honest(self):
        """Track 2: no key -> FakeJev, honestly labeled, zero spend."""

    @unittest.skip(TRACK2_MISSING)
    def test_2f_budget_exhausted_deterministic(self):
        """Track 2: spend blocked -> deterministic, no Jev calls."""


if __name__ == "__main__":
    unittest.main(verbosity=2)
