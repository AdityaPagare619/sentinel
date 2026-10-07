"""Track 1 (P0): operator-auth exploit reproduction + regression tests.

Attack chain (from infra research): the platform server exposes
/api/v1/integrations/keys POST/DELETE, test-page, and /api/simulate with
ZERO authentication, and CORS defaults to "*". Any website the operator
visits can therefore:
  1. pass a preflight OPTIONS check from an arbitrary Origin,
  2. POST an attacker-controlled PagerDuty routing key to
     /api/v1/integrations/keys,
  3. after which every test-page/page triggered from the console routes
     to the attacker's PagerDuty service.

These tests perform that chain against the WSGI app. They assert the
FIXED behavior, so they FAIL on the unfixed code (proving the exploit
works) and PASS once the C1 fix lands.

Contract (program/full-build C1):
  - every /api/* request requires `Authorization: Bearer <token>`,
    else 401 with the EXACT body {"error": "unauthorized"}
    (Track 8 consumes this exact body for the sign-in state);
  - EXEMPT only: /api/v1/health/live and /api/v1/health/ready;
  - token: per-install, secrets.token_urlsafe(32) at first boot, file
    0600, never logged, never in responses;
  - CORS default allowlist = {https://AdityaPagare619.github.io}.
"""
import io
import json
import os
import stat
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
sys.path.insert(0, os.path.dirname(_HERE))  # platform/server for _pkg
sys.path.insert(0, os.path.join(_REPO, "src"))  # server.simulate needs it
import _pkg

_app = _pkg.load("app")
_shed = _pkg.load("shed")
_auth = _pkg.load("auth")

PlatformApp = _app.PlatformApp
OperatorTokenStore = _auth.OperatorTokenStore
AdmissionGate, DegradePolicy = _shed.AdmissionGate, _shed.DegradePolicy

ATTACKER_KEY = "deadbeef" * 4  # 32 hex chars: looks like a real PD key
EVIL_ORIGIN = "https://evil.example.com"
PAGES_ORIGIN = "https://AdityaPagare619.github.io"


class _Gate:
    def try_acquire(self):
        return True

    def release(self):
        pass


class _Degrade:
    def should_shed(self, path):
        return False, ""


class AuthCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["SENTINEL_INTEGRATIONS_FILE"] = os.path.join(
            self.tmp.name, "integrations.json")
        self.token_path = os.path.join(self.tmp.name, "operator_token.json")
        self.store = OperatorTokenStore(self.token_path)
        self.token = self.store.first_boot_token
        self.app = PlatformApp(store=None, registry=None, gate=_Gate(),
                               degrade=_Degrade(),
                               operator_token_store=self.store)

    # ------------------------------------------------------------ helpers

    def call(self, path, method="GET", body=None, origin=None,
             auth_header=None, app=None):
        app = app or self.app
        raw = json.dumps(body).encode() if body is not None else b""
        env = {"PATH_INFO": path, "REQUEST_METHOD": method,
               "QUERY_STRING": "", "CONTENT_LENGTH": str(len(raw)),
               "wsgi.input": io.BytesIO(raw)}
        if origin:
            env["HTTP_ORIGIN"] = origin
        if auth_header is not None:
            env["HTTP_AUTHORIZATION"] = auth_header
        captured = {}

        def start_response(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = {k.lower(): v for k, v in headers}

        out = b"".join(app(env, start_response))
        captured["body"] = out
        try:
            captured["json"] = json.loads(out.decode()) if out else None
        except ValueError:
            captured["json"] = None
        return captured

    def authed(self, path, **kw):
        kw.setdefault("auth_header", f"Bearer {self.token}")
        return self.call(path, **kw)

    def stored_pd_key(self):
        """Read back the persisted routing key (write-only via the API)."""
        return self.app.integrations.get("pagerduty_routing_key")

    # ----------------------------------------------------- the attack chain

    def test_exploit_key_overwrite_blocked(self):
        """The researched attack, walked end to end.

        Pre-fix: preflight passes from an arbitrary origin, the POST
        executes (200), and the operator's PagerDuty routing key becomes
        the attacker's. Post-fix: 401, key untouched.
        """
        # 1. Attacker's page passes a preflight (browsers don't send
        #    Authorization on preflights, so this stays 204 — but the
        #    follow-up POST must now die on auth).
        pre = self.call("/api/v1/integrations/keys", method="OPTIONS",
                        origin=EVIL_ORIGIN)
        self.assertTrue(pre["status"].startswith("204"))

        # 2. The malicious POST: no Authorization header, evil Origin.
        r = self.call("/api/v1/integrations/keys", method="POST",
                      body={"pagerduty_routing_key": ATTACKER_KEY},
                      origin=EVIL_ORIGIN)
        self.assertTrue(r["status"].startswith("401"),
                        f"exploit succeeded: {r['status']} {r['body'][:200]}")

        # 3. The stored key must NOT be the attacker's.
        self.assertNotEqual(self.stored_pd_key(), ATTACKER_KEY)

    def test_exploit_test_page_blocked(self):
        """Same chain against test-page (would page the attacker's PD)."""
        r = self.call("/api/v1/integrations/test-page", method="POST",
                      body={}, origin=EVIL_ORIGIN)
        self.assertTrue(r["status"].startswith("401"))

    def test_exploit_keys_delete_blocked(self):
        """DELETE of a stored key must also die on auth."""
        r = self.call("/api/v1/integrations/keys/pagerduty_routing_key",
                      method="DELETE", origin=EVIL_ORIGIN)
        self.assertTrue(r["status"].startswith("401"))

    def test_exploit_simulate_blocked(self):
        r = self.call("/api/simulate", method="POST",
                      body={"dataset_version": "labels-v3"},
                      origin=EVIL_ORIGIN)
        self.assertTrue(r["status"].startswith("401"))

    def test_401_body_is_exact_contract(self):
        """Track 8 keys off this exact body for the sign-in state."""
        r = self.call("/api/decisions", origin=EVIL_ORIGIN)
        self.assertTrue(r["status"].startswith("401"))
        self.assertEqual(r["json"], {"error": "unauthorized"})
        # ...and the 401 is still CORS-readable for the hosted console
        # (the console must SEE the 401 to show the sign-in state).
        r2 = self.call("/api/decisions", origin=PAGES_ORIGIN)
        self.assertEqual(r2["headers"].get("access-control-allow-origin"),
                         PAGES_ORIGIN)

    def test_evil_origin_gets_no_cors_headers(self):
        """Default allowlist: only the hosted console origin is echoed."""
        r = self.call("/api/v1/integrations/status", method="OPTIONS",
                      origin=EVIL_ORIGIN)
        self.assertNotIn("access-control-allow-origin", r["headers"])

    # ------------------------------------------------- the fixed happy path

    def test_authenticated_keys_save_works(self):
        r = self.authed("/api/v1/integrations/keys", method="POST",
                        body={"pagerduty_routing_key": ATTACKER_KEY},
                        origin=PAGES_ORIGIN)
        self.assertTrue(r["status"].startswith("200"), r["body"][:300])
        self.assertEqual(self.stored_pd_key(), ATTACKER_KEY)
        # ...but the value is never echoed back (last4 only).
        self.assertNotIn(ATTACKER_KEY, r["body"].decode())

    def test_wrong_token_rejected(self):
        r = self.call("/api/decisions", auth_header="Bearer wrong-token")
        self.assertTrue(r["status"].startswith("401"))
        self.assertEqual(r["json"], {"error": "unauthorized"})

    def test_malformed_auth_headers_rejected(self):
        for bad in ("", "Bearer", "bearer ", "Token abc", "Basic Zm9v"):
            r = self.call("/api/decisions", auth_header=bad)
            self.assertTrue(r["status"].startswith("401"), bad)

    # ------------------------------------------------------- health exempt

    def test_health_live_open(self):
        r = self.call("/api/v1/health/live")
        self.assertTrue(r["status"].startswith("200"))
        self.assertEqual(r["json"]["data"]["status"], "ok")

    def test_health_ready_open(self):
        r = self.call("/api/v1/health/ready")
        self.assertTrue(r["status"].startswith("200"))
        self.assertEqual(r["json"]["data"]["status"], "ok")

    # ------------------------------------------------- token store hygiene

    def test_first_boot_generates_token_file_0600(self):
        st = os.stat(self.token_path)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        self.assertGreaterEqual(len(self.token), 43)  # token_urlsafe(32)

    def test_second_boot_reuses_token(self):
        again = OperatorTokenStore(self.token_path)
        self.assertIsNone(again.first_boot_token)
        self.assertTrue(again.verify(self.token))
        self.assertEqual(again.generation, 1)

    def test_verify_rejects_garbage(self):
        self.assertFalse(self.store.verify(""))
        self.assertFalse(self.store.verify("x" * 43))

    def test_token_never_in_responses(self):
        """Sweep several endpoints: the token must appear nowhere."""
        r = self.authed("/api/v1/integrations/status", method="GET",
                        origin=PAGES_ORIGIN)
        body = r["body"].decode()
        self.assertNotIn(self.token, body)
        r = self.authed("/api/v1/integrations/keys", method="POST",
                        body={"jev_api_key": "sk-test-12345678"},
                        origin=PAGES_ORIGIN)
        self.assertNotIn(self.token, r["body"].decode())

    def test_dual_accept_seam_for_rotation(self):
        """C4 seam: the canonical keystore carries {primary, secondary,
        generation} per secret; verify accepts either. (Track 4 builds
        rotation on this.)"""
        with open(self.token_path) as fh:
            data = json.load(fh)
        rec = data["records"]["operator_bearer"]
        self.assertEqual(rec["generation"], 1)
        # hashed at rest (verify-only): digest keys, never the token
        self.assertIn("primary_sha256", rec)
        self.assertIn("secondary_sha256", rec)
        self.assertNotIn("primary", rec)
        self.assertNotIn(self.token, json.dumps(data))
        # secondary empty -> no second credential accepted
        self.assertFalse(self.store.verify("anything-else"))
        # operator presents primary -> accepted
        self.assertTrue(self.store.verify(self.token))

    def test_corrupt_token_file_regenerates(self):
        with open(self.token_path, "w") as fh:
            fh.write("not-json{{{")
        regen = OperatorTokenStore(self.token_path)
        self.assertIsNotNone(regen.first_boot_token)
        self.assertTrue(regen.verify(regen.first_boot_token))

    def test_env_provisioned_token(self):
        """Serverless: SENTINEL_OPERATOR_TOKEN provisions the token —
        no file, no banner, but the app accepts it."""
        os.environ["SENTINEL_OPERATOR_TOKEN"] = "env-provisioned-token-1"
        self.addCleanup(os.environ.pop, "SENTINEL_OPERATOR_TOKEN", None)
        store = OperatorTokenStore(os.path.join(self.tmp.name, "unused.json"))
        self.assertIsNone(store.first_boot_token)
        self.assertFalse(store.ephemeral)
        self.assertTrue(store.verify("env-provisioned-token-1"))
        self.assertFalse(store.verify(self.token))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name,
                                                     "unused.json")))
        app = PlatformApp(store=None, registry=None, gate=_Gate(),
                          degrade=_Degrade(), operator_token_store=store)
        ok = self.call("/api/v1/integrations/status", app=app,
                       auth_header="Bearer env-provisioned-token-1")
        self.assertTrue(ok["status"].startswith("200"), ok["status"])
        denied = self.call("/api/v1/integrations/status", app=app)
        self.assertTrue(denied["status"].startswith("401"))
        self.assertEqual(denied["json"], {"error": "unauthorized"})

    def test_unwritable_state_dir_degrades_ephemeral(self):
        """/proc is read-only: the store must not crash the boot, it
        degrades to an in-memory token and says so loudly."""
        store = OperatorTokenStore("/proc/sentinel-nope/operator_token.json")
        self.assertTrue(store.ephemeral)
        self.assertIsNone(store.first_boot_token)  # no banner to show
        token = store._tokens["primary"]  # white-box: in-memory token
        self.assertTrue(store.verify(token))
        self.assertFalse(store.verify("nope"))

    # --------------------------------- rotation RFC: serverless dual-accept

    def _env_store(self, primary, previous=None):
        os.environ["SENTINEL_OPERATOR_TOKEN"] = primary
        self.addCleanup(os.environ.pop, "SENTINEL_OPERATOR_TOKEN", None)
        if previous is not None:
            os.environ["SENTINEL_OPERATOR_TOKEN_PREVIOUS"] = previous
            self.addCleanup(os.environ.pop,
                            "SENTINEL_OPERATOR_TOKEN_PREVIOUS", None)
        return OperatorTokenStore(os.path.join(self.tmp.name, "unused.json"))

    def test_env_dual_accept_previous(self):
        """Rotation RFC (serverless): with SENTINEL_OPERATOR_TOKEN_PREVIOUS
        staged, BOTH the primary and the predecessor verify (overlap);
        anything else 401s. secondary_staged reports the overlap."""
        store = self._env_store("env-primary-token-0001",
                                "env-previous-token-0002")
        self.assertTrue(store.verify("env-primary-token-0001"))
        self.assertTrue(store.verify("env-previous-token-0002"))
        self.assertFalse(store.verify("env-primary-token-000"))
        self.assertFalse(store.verify(""))
        self.assertFalse(store.verify(None))
        self.assertTrue(store.secondary_staged)
        # file-mode seam has no staged secondary here
        self.assertFalse(self.store.secondary_staged)

    def test_env_no_previous_no_grace(self):
        """Without the PREVIOUS var there is no grace slot — exactly the
        old behavior, fail-closed."""
        store = self._env_store("env-primary-token-0001")
        self.assertTrue(store.verify("env-primary-token-0001"))
        self.assertFalse(store.verify("env-previous-token-0002"))
        self.assertFalse(store.secondary_staged)

    def test_401_carries_www_authenticate(self):
        """RFC 6750 S3: a 401 from the bearer-token resource server MUST
        carry a WWW-Authenticate challenge. The JSON body contract is
        unchanged (Track 8 keys off it verbatim)."""
        denied = self.call("/api/v1/integrations/status", app=self.app)
        self.assertTrue(denied["status"].startswith("401"))
        self.assertEqual(denied["json"], {"error": "unauthorized"})
        challenge = denied["headers"].get("www-authenticate", "")
        self.assertTrue(challenge.startswith("Bearer "),
                        f"missing Bearer challenge: {challenge!r}")
        self.assertIn('realm="sentinel-operator"', challenge)
        self.assertIn('error="invalid_token"', challenge)
        # the token value must never appear in the challenge
        self.assertNotIn(self.token, challenge)

    def test_stream_behind_auth_in_app(self):
        """Main-app ordering anchor for the wrapper fix: /api/stream
        401s before any stream logic for unauthenticated callers."""
        denied = self.call("/api/stream", app=self.app)
        self.assertTrue(denied["status"].startswith("401"))
        self.assertEqual(denied["json"], {"error": "unauthorized"})


if __name__ == "__main__":
    unittest.main()
