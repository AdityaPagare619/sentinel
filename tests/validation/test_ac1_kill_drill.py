"""AC-1 — Kill-switch drill: measured, sticky, attributed.

Contract C3. Until Track 3 merges (kill endpoint + `mode` field + drill
artifact), every test here is BLOCKED with the owning track named. The
moment Track 3 lands, the probes below go live and the drill executes
for real — measured from the decision log, never asserted.
"""

import json
import os
import re
import subprocess
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
DRILL_SCRIPT = os.path.join(REPO, "scripts", "kill_drill.py")
APP_PY = os.path.join(REPO, "platform", "server", "app.py")

T3_MISSING = ("BLOCKED: Track 3 not merged — no kill endpoint "
              "(/api/v1/safety/kill), no `mode` field on the disposition "
              "record, no scripts/kill_drill.py on this branch")


def _track3_present():
    if not os.path.exists(DRILL_SCRIPT):
        return False, "scripts/kill_drill.py absent"
    with open(APP_PY) as fh:
        src = fh.read()
    if "/api/v1/safety/kill" not in src:
        return False, "kill endpoint absent from platform/server/app.py"
    return True, ""


class TestAC1KillDrill(unittest.TestCase):
    """The drill, in full, gated on Track 3's contract landing."""

    def test_1a_drill_artifact_runs_and_measures(self):
        """Track 3's scripts/kill_drill.py runs against the real gate and
        prints the measured flip->halt ms. PASS: ms < 5000."""
        present, why = _track3_present()
        if not present:
            self.skipTest(f"{T3_MISSING} ({why})")
        proc = subprocess.run(
            [sys.executable, DRILL_SCRIPT, "--json"],
            cwd=REPO, capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0,
                         f"kill_drill.py failed:\n{proc.stderr[-2000:]}")
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"kill_drill.py did not print JSON:\n"
                      f"{proc.stdout[-2000:]}")
        ms = data.get("flip_to_halt_ms")
        self.assertIsNotNone(ms, "drill printed no flip_to_halt_ms")
        self.assertLess(ms, 5000,
                        f"measured flip->halt {ms} ms >= 5000 ms")
        print(f"\n[AC-1a] measured flip->halt: {ms:.0f} ms")

    def test_1b_sticky_no_self_revive(self):
        """After the flip, suppressible alerts keep paging until the
        SEPARATE rearm action. A second flip must not disarm."""
        present, why = _track3_present()
        if not present:
            self.skipTest(f"{T3_MISSING} ({why})")
        self.fail("Track 3 landed but the stickiness harness is not wired "
                  "to its API yet — wire me before sign-off")

    def test_1c_rearm_separate_deliberate(self):
        present, why = _track3_present()
        if not present:
            self.skipTest(f"{T3_MISSING} ({why})")
        self.fail("Track 3 landed but the re-arm harness is not wired "
                  "to its API yet — wire me before sign-off")

    def test_1d_kill_above_jev_control_principle(self):
        """Code-path inspection: the kill check sits above the race,
        before any Jev call — Jev can never flip, delay, or reinterpret
        the kill."""
        present, why = _track3_present()
        if not present:
            self.skipTest(f"{T3_MISSING} ({why})")
        gate_py = os.path.join(REPO, "src", "sentinel", "gate.py")
        with open(gate_py) as fh:
            src = fh.read()
        # The kill gate must be consulted before the race is armed.
        kill_pos = src.find("kill")
        race_pos = src.find("runner.run(")
        self.assertGreater(kill_pos, 0, "no kill reference in gate.py")
        self.assertLess(kill_pos, race_pos,
                        "kill check must precede the race arm in gate.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
