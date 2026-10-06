"""AC-6 — Shadow audit: the shadow never touches the decision record.

Contract C3. Until Track 3 merges (the `mode` field + write-once
`reason`), every test here is BLOCKED. The architecture violation this
guards — shadow_mode rewriting kill-switch attribution to "shadow" —
gets ZERO tolerance: a single rewritten reason fails the criterion.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
RP_PY = os.path.join(REPO, "src", "sentinel", "race_payloads.py")

T3_MISSING = ("BLOCKED: Track 3 not merged — no `mode` field on the "
              "disposition record (shadow vs live) on this branch")


def _track3_mode_present():
    with open(RP_PY) as fh:
        src = fh.read()
    return '"mode"' in src or "'mode'" in src


class TestAC6ShadowAudit(unittest.TestCase):
    def _require_t3(self):
        if not _track3_mode_present():
            self.skipTest(T3_MISSING)

    def test_6a_mode_field_correct(self):
        """shadow_decision rows carry mode=shadow; live decision_made
        rows carry mode=live."""
        self._require_t3()
        self.fail("Track 3 landed but the shadow-audit harness is not "
                  "wired to its record shape yet — wire me before sign-off")

    def test_6b_zero_rewritten_reasons(self):
        """Sample min(50, all) shadow rows: reason is causal, never
        'shadow'."""
        self._require_t3()
        self.fail("Track 3 landed but the shadow-audit harness is not "
                  "wired to its record shape yet — wire me before sign-off")

    def test_6c_shadow_is_powerless(self):
        """No outbox_id on shadow rows; no forward_confirmed for shadow
        episodes."""
        self._require_t3()
        self.fail("Track 3 landed but the shadow-audit harness is not "
                  "wired to its record shape yet — wire me before sign-off")

    def test_6d_kill_attribution_intact(self):
        self._require_t3()
        self.fail("Track 3 landed but the shadow-audit harness is not "
                  "wired to its record shape yet — wire me before sign-off")


if __name__ == "__main__":
    unittest.main(verbosity=2)
