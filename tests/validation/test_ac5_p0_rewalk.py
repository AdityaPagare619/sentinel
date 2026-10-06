"""AC-5 — P0 re-walk: Track 1's attack chain against final code.

Contract C1 (Track 1, merged). The probe is the code itself: the real
PlatformApp WSGI app is built in-process with T1's real token issuance
(OperatorTokenStore -> first_boot_token, exactly the operator's one
plaintext handle), and the exact attack chain from the infra domain
research is re-executed against it.

Wire contract (verbatim): every /api/* request except the two health
probes requires `Authorization: Bearer <operator-token>`; unauthenticated
-> 401 {"error":"unauthorized"}.
"""

import os
import sys
import tempfile
import shutil
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))

from platform_harness import (  # noqa: E402
    make_platform_app, wsgi_call, auth_headers, json_body)

T1_MISSING = ("BLOCKED: Track 1 not merged — /api/* has no operator "
              "bearer auth on this branch (the P0 from the infra domain "
              "research is still open)")

# A PagerDuty Events API v2-shaped routing key (32 hex) — write-only at
# rest; this one is a test fixture, never a real key.
_FIXTURE_PD_KEY = "0123456789abcdef0123456789abcdef"

_EVIL_ORIGIN = "https://evil.example.com"
_PAGES_ORIGIN = "https://AdityaPagare619.github.io"  # C1's CORS allowlist default


def _track1_present():
    app_py = os.path.join(REPO, "platform", "server", "app.py")
    with open(app_py) as fh:
        src = fh.read()
    # Track 1's contract: every /api/* request requires
    # Authorization: Bearer <operator-token> -> 401 {"error":"unauthorized"}.
    return ('"unauthorized"' in src and "Authorization" in src
            and "Bearer" in src)


class TestAC5P0Rewalk(unittest.TestCase):
    """The exact attack chain from the infra research, re-executed."""

    def setUp(self):
        if not _track1_present():
            self.skipTest(T1_MISSING)
        self.app, self.token, self.tokens, self.tmpdir = make_platform_app()
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir,
                                              ignore_errors=True))

    # ------------------------------------------------------------ helpers

    def call(self, method, path, token=None, body=None, headers=None):
        hdrs = dict(headers or {})
        if token == "VALID":
            hdrs["Authorization"] = f"Bearer {self.token}"
        elif token is not None:
            hdrs["Authorization"] = token
        return wsgi_call(self.app, method, path, headers=hdrs, body=body)

    def data_of(self, payload):
        """Unwrap the platform's {"data": ..., "meta": ...} envelope."""
        parsed = json_body(payload)
        self.assertIn("data", parsed, "response is not the platform envelope")
        return parsed["data"]

    def assert_unauthorized(self, status, headers, payload, where):
        self.assertEqual(status, 401, f"{where}: expected 401, got {status}")
        self.assertEqual(json_body(payload), {"error": "unauthorized"},
                         f"{where}: body must be exactly "
                         '{"error":"unauthorized"}')

    # ---------------------------------------------------------------- 5

    def test_5_probe(self):
        """Single probe: is Track 1's auth on the branch?"""
        print("\n[AC-5] Track 1 auth present — attack chain live")

    def test_5a_unauthenticated_key_overwrite_401(self):
        """The P0: POST /api/v1/integrations/keys with NO token must 401,
        verbatim {"error":"unauthorized"} — the key is NOT overwritten."""
        # Pre-seed a key through the legitimate path so we can prove the
        # unauthenticated attempt does not clobber it.
        st, _, body = self.call(
            "POST", "/api/v1/integrations/keys", token="VALID",
            body={"pagerduty_routing_key": _FIXTURE_PD_KEY})
        self.assertEqual(st, 200, f"legit key save failed: {body!r}")

        attacker_key = "ffffffffffffffffffffffffffffffff"
        st, hdrs, body = self.call(
            "POST", "/api/v1/integrations/keys",
            body={"pagerduty_routing_key": attacker_key})
        self.assert_unauthorized(st, hdrs, body,
                                 "unauthenticated key overwrite")

        # The stored key is untouched (status is operator-authenticated and
        # only reports last4 — the write surface is write-only).
        st, _, body = self.call("GET", "/api/v1/integrations/status",
                                token="VALID")
        self.assertEqual(st, 200)
        status = self.data_of(body)["integrations"]
        self.assertEqual(status["pagerduty_routing_key"]["last4"],
                         _FIXTURE_PD_KEY[-4:],
                         "unauthenticated POST overwrote the key!")
        print("\n[AC-5a] unauthenticated key overwrite -> 401, stored key "
              "untouched")

    def test_5b_bogus_bearer_401(self):
        """Forged / malformed Authorization headers all 401."""
        bogus = [
            "Bearer deadbeef-dead-beef-dead-deadbeefdead",
            "Bearer ",                      # empty token
            "Bearer " + self.token + "x",   # near-miss
            "Token " + self.token,          # wrong scheme
            "Basic " + self.token,          # wrong scheme
            self.token,                     # no scheme at all
        ]
        for i, hdr in enumerate(bogus):
            st, _, body = self.call(
                "POST", "/api/v1/integrations/keys", token=hdr,
                body={"pagerduty_routing_key": _FIXTURE_PD_KEY})
            self.assert_unauthorized(st, {}, body,
                                     f"bogus bearer case {i} ({hdr[:12]}...)")
        # And the REAL token still works (no lockout / fail-open).
        st, _, body = self.call(
            "POST", "/api/v1/integrations/keys", token="VALID",
            body={"pagerduty_routing_key": _FIXTURE_PD_KEY})
        self.assertEqual(st, 200,
                         f"valid token rejected after bogus attempts: "
                         f"{body!r}")
        print(f"\n[AC-5b] {len(bogus)} bogus bearer variants -> 401; "
              f"valid token still 200")

    def test_5c_cross_origin_post_blocked(self):
        """A cross-origin POST from an unlisted origin gets no
        Access-Control-Allow-Origin echo — the browser refuses to expose
        the response, so a malicious page cannot drive the API even if it
        could trick a request through. The allowlisted Pages console origin
        IS echoed (the console keeps working)."""
        target = "/api/v1/integrations/keys"
        pd_body = {"pagerduty_routing_key": _FIXTURE_PD_KEY}

        # Evil origin: no ACAO echo on the 401 OR on a 200-with-token
        # response, and none on the OPTIONS preflight either.
        st, hdrs, _ = self.call("POST", target, token="VALID", body=pd_body,
                                headers={"Origin": _EVIL_ORIGIN})
        self.assertEqual(st, 200, "setup: valid-token POST must succeed")
        self.assertNotIn(_EVIL_ORIGIN, hdrs.get("Access-Control-Allow-Origin",
                                                ""),
                         "evil origin echoed in Access-Control-Allow-Origin!")
        self.assertNotEqual(hdrs.get("Access-Control-Allow-Origin"), "*",
                            "wildcard ACAO would let ANY origin read "
                            "responses")

        st, hdrs, _ = self.call("POST", target, body=pd_body,
                                headers={"Origin": _EVIL_ORIGIN})
        self.assertEqual(st, 401)
        self.assertNotIn("Access-Control-Allow-Origin", hdrs,
                         "401 leaks an ACAO header to the evil origin")

        st, hdrs, _ = self.call("OPTIONS", target,
                                headers={"Origin": _EVIL_ORIGIN})
        self.assertEqual(st, 204)
        self.assertNotIn("Access-Control-Allow-Origin", hdrs,
                         "preflight grants the evil origin")

        # Allowlisted console origin: echoed, so the real UI keeps working.
        st, hdrs, _ = self.call("POST", target, token="VALID", body=pd_body,
                                headers={"Origin": _PAGES_ORIGIN})
        self.assertEqual(st, 200)
        self.assertEqual(hdrs.get("Access-Control-Allow-Origin"),
                         _PAGES_ORIGIN)
        print("\n[AC-5c] evil origin: no ACAO echo (blocked by the browser); "
              "allowlisted origin echoed")

    def test_5d_unauthenticated_key_delete_401(self):
        """DELETE /api/v1/integrations/keys/<name> with no token 401s and
        deletes nothing."""
        st, _, body = self.call("POST", "/api/v1/integrations/keys",
                                token="VALID",
                                body={"pagerduty_routing_key":
                                      _FIXTURE_PD_KEY})
        self.assertEqual(st, 200)

        st, _, body = self.call(
            "DELETE", "/api/v1/integrations/keys/pagerduty_routing_key")
        self.assert_unauthorized(st, {}, body, "unauthenticated key delete")

        st, _, body = self.call("GET", "/api/v1/integrations/status",
                                token="VALID")
        self.assertEqual(
            self.data_of(body)["integrations"]["pagerduty_routing_key"]
            ["last4"], _FIXTURE_PD_KEY[-4:],
            "unauthenticated DELETE removed the key!")
        print("\n[AC-5d] unauthenticated key delete -> 401, key intact")

    def test_5e_no_auth_on_non_exempt_surfaces(self):
        """Every /api/* surface except the two health probes 401s without
        a token — including paths that don't exist (auth precedes routing
        and admission: a 404 or 503 must never mask a 401)."""
        surfaces = [
            ("GET", "/api/decisions"),
            ("GET", "/api/decision/1"),
            ("GET", "/api/calibration"),
            ("POST", "/api/simulate"),
            ("GET", "/api/analytics/noise"),
            ("GET", "/api/analytics/flips"),
            ("GET", "/api/stream"),
            ("GET", "/api/v1/integrations/status"),
            ("POST", "/api/v1/integrations/keys"),
            ("POST", "/api/v1/integrations/simulated"),
            ("POST", "/api/v1/integrations/test-page"),
            ("DELETE", "/api/v1/integrations/keys/pagerduty_routing_key"),
            ("POST", "/api/v1/keys/operator_token/rotate"),
            ("POST", "/api/v1/safety/kill"),
            ("POST", "/api/v1/safety/rearm"),
            ("GET", "/api/v1/safety/status"),
            ("GET", "/api/does-not-exist"),   # unknown path still 401s
        ]
        failures = []
        for method, path in surfaces:
            st, _, body = self.call(method, path)
            if st != 401 or json_body(body) != {"error": "unauthorized"}:
                failures.append(f"{method} {path} -> {st} {body[:60]!r}")
        self.assertEqual(failures, [],
                         "surfaces that did not 401 verbatim:\n"
                         + "\n".join(failures))
        print(f"\n[AC-5e] {len(surfaces)} /api/* surfaces -> 401 "
              f"{{\"error\":\"unauthorized\"}} without a token")

    def test_5f_health_endpoints_exempt(self):
        """The ONLY unauthenticated surface (contract C1): the two health
        probes answer 200 without a token."""
        for path in ("/api/v1/health/live", "/api/v1/health/ready"):
            st, _, body = self.call("GET", path)
            self.assertEqual(st, 200, f"{path} must be reachable unauth'd")
            self.assertEqual(self.data_of(body), {"status": "ok"})
            # ... and they stay open even with a hostile Origin header.
            st, _, body = self.call("GET", path,
                                    headers={"Origin": _EVIL_ORIGIN})
            self.assertEqual(st, 200)
        print("\n[AC-5f] health probes exempt (200 unauthenticated); "
              "everything else under /api/* is not")

    def test_5g_authenticated_key_save_works(self):
        """The legitimate operator flow still works: save -> status shows
        last4 only (write-only values) -> delete -> gone."""
        st, _, body = self.call("POST", "/api/v1/integrations/keys",
                                token="VALID",
                                body={"pagerduty_routing_key":
                                      _FIXTURE_PD_KEY})
        self.assertEqual(st, 200, f"authenticated save failed: {body!r}")
        saved = self.data_of(body)
        self.assertIn("pagerduty_routing_key", saved["saved"],
                      f"save response missing the key name: {saved!r}")

        st, _, body = self.call("GET", "/api/v1/integrations/status",
                                token="VALID")
        self.assertEqual(st, 200)
        pd_status = self.data_of(body)["integrations"]["pagerduty_routing_key"]
        self.assertTrue(pd_status["configured"])
        self.assertEqual(pd_status["last4"], _FIXTURE_PD_KEY[-4:])
        self.assertNotIn(_FIXTURE_PD_KEY, body.decode("utf-8"),
                         "full key value leaked in a read response!")

        st, _, body = self.call(
            "DELETE", "/api/v1/integrations/keys/pagerduty_routing_key",
            token="VALID")
        self.assertEqual(st, 200)
        st, _, body = self.call("GET", "/api/v1/integrations/status",
                                token="VALID")
        self.assertFalse(
            self.data_of(body)["integrations"]["pagerduty_routing_key"]
            ["configured"])
        print("\n[AC-5g] authenticated save/status/delete round-trip works; "
              "reads expose last4 only")


if __name__ == "__main__":
    unittest.main(verbosity=2)
