"""AC-5 — P0 re-walk: Track 1's attack chain against final code.

Contract C1. Until Track 1 merges (operator bearer auth on /api/*,
CORS allowlist default), every test here is BLOCKED. The probe is the
code itself: if the platform app does not enforce Authorization, the
attack chain is not re-walked — it is reported BLOCKED, never faked.
"""

import io
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
_SRC = os.path.join(REPO, "src")
for _p in (_SRC, os.path.join(REPO, "platform", "server"), REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

T1_MISSING = ("BLOCKED: Track 1 not merged — /api/* has no operator "
              "bearer auth on this branch (the P0 from the infra domain "
              "research is still open)")


def _track1_present():
    app_py = os.path.join(REPO, "platform", "server", "app.py")
    with open(app_py) as fh:
        src = fh.read()
    # Track 1's contract: every /api/* request requires
    # Authorization: Bearer <operator-token> -> 401 {"error":"unauthorized"}.
    return ('"unauthorized"' in src and "Authorization" in src
            and "Bearer" in src)


def _make_app():
    """Build the platform WSGI app the way Track 1's tests will."""
    sys.path.insert(0, os.path.join(REPO, "platform", "server"))
    import app as appmod  # noqa: E402
    return appmod


class TestAC5P0Rewalk(unittest.TestCase):
    """The exact attack chain from the infra research, re-executed."""

    def _require_t1(self):
        if not _track1_present():
            self.skipTest(T1_MISSING)

    def test_5_probe(self):
        """Single probe: is Track 1's auth on the branch?"""
        if not _track1_present():
            self.skipTest(T1_MISSING)
        print("\n[AC-5] Track 1 auth present — attack chain live")

    def test_5a_unauthenticated_key_overwrite_401(self):
        self._require_t1()
        self.fail("Track 1 landed but the attack harness is not wired "
                  "to its token issuance yet — wire me before sign-off")

    def test_5b_bogus_bearer_401(self):
        self._require_t1()
        self.fail("Track 1 landed but the attack harness is not wired "
                  "to its token issuance yet — wire me before sign-off")

    def test_5c_cross_origin_post_blocked(self):
        self._require_t1()
        self.fail("Track 1 landed but the attack harness is not wired "
                  "to its token issuance yet — wire me before sign-off")

    def test_5d_unauthenticated_key_delete_401(self):
        self._require_t1()
        self.fail("Track 1 landed but the attack harness is not wired "
                  "to its token issuance yet — wire me before sign-off")

    def test_5e_no_auth_on_non_exempt_surfaces(self):
        self._require_t1()
        self.fail("Track 1 landed but the attack harness is not wired "
                  "to its token issuance yet — wire me before sign-off")

    def test_5f_health_endpoints_exempt(self):
        self._require_t1()
        self.fail("Track 1 landed but the attack harness is not wired "
                  "to its token issuance yet — wire me before sign-off")

    def test_5g_authenticated_key_save_works(self):
        self._require_t1()
        self.fail("Track 1 landed but the attack harness is not wired "
                  "to its token issuance yet — wire me before sign-off")


if __name__ == "__main__":
    unittest.main(verbosity=2)
