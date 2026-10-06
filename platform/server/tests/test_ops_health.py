"""Track 8: GET /api/v1/ops/health — Operations Health aggregate tests.

The endpoint backs the console's Operations Health surface. Contract:
- Authenticated (C1): unauthenticated -> 401 {"error": "unauthorized"}.
- Every section carries what the platform tier can honestly source.
- Unobservable signals read "not_instrumented" (never fabricated);
  nonexistent controls read "not_implemented" (never faked healthy).
- A health endpoint must never 500 because a stat query failed.
"""
import io
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
sys.path.insert(0, os.path.dirname(_HERE))  # platform/server for _pkg
sys.path.insert(0, os.path.join(_REPO, "src"))
import _pkg

_app = _pkg.load("app")
_auth = _pkg.load("auth")

PlatformApp = _app.PlatformApp
OperatorTokenStore = _auth.OperatorTokenStore


class _Gate:
    def try_acquire(self):
        return True

    def release(self):
        pass


class _Degrade:
    def should_shed(self, path):
        return False, ""


class OpsHealthCase(unittest.TestCase):
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

    def call(self, path, auth_header="__AUTH__"):
        if auth_header == "__AUTH__":
            auth_header = f"Bearer {self.token}"
        env = {"PATH_INFO": path, "REQUEST_METHOD": "GET",
               "QUERY_STRING": "", "CONTENT_LENGTH": "0",
               "wsgi.input": io.BytesIO(b"")}
        if auth_header is not None:
            env["HTTP_AUTHORIZATION"] = auth_header
        captured = {}

        def start_response(status, headers, exc_info=None):
            captured["status"] = status

        out = b"".join(self.app(env, start_response))
        captured["body"] = out
        captured["json"] = json.loads(out.decode()) if out else None
        return captured

    def test_unauthenticated_is_401(self):
        r = self.call("/api/v1/ops/health", auth_header=None)
        self.assertTrue(r["status"].startswith("401"))
        self.assertEqual(r["json"], {"error": "unauthorized"})

    def test_bogus_token_is_401(self):
        r = self.call("/api/v1/ops/health", auth_header="Bearer nope")
        self.assertTrue(r["status"].startswith("401"))

    def test_shape_and_honesty(self):
        r = self.call("/api/v1/ops/health")
        self.assertTrue(r["status"].startswith("200"), r["status"])
        j = r["json"]
        for section in ("environment", "engine_db", "jev", "pipeline",
                        "forwarder", "safety"):
            self.assertIn(section, j, f"missing section {section}")
        # environment is real
        self.assertEqual(j["environment"]["mode"], "production")
        self.assertIn("as_of", j["environment"])
        # store=None in tests -> engine DB honestly unavailable, no crash
        self.assertFalse(j["engine_db"]["available"])
        # honesty law: unobservable signals are literal, never fabricated
        self.assertEqual(j["jev"]["circuit"], "not_implemented")
        self.assertEqual(j["jev"]["connection"], "not_instrumented")
        for stage in j["pipeline"]:
            self.assertEqual(stage["state"], "not_instrumented")
            self.assertIn(stage["stage"],
                          ("receiver", "correlator", "race", "gate",
                           "forwarder"))
        # no PD key configured in test env -> fakepd, honestly
        self.assertEqual(j["forwarder"]["identity"], "fakepd")
        # safety: kill switch may be unwired here (503 path) or real;
        # either way the section must be present and honest
        self.assertIn("kill_switch", j["safety"])
        self.assertIn("auth", j["safety"])

    def test_never_500s(self):
        # Even with a hostile store, the endpoint answers (possibly with
        # degraded/empty data), never a 500.
        class _BadStore:
            available = True

            def decisions(self, **kw):
                raise RuntimeError("db exploded")

        self.app.store = _BadStore()
        r = self.call("/api/v1/ops/health")
        self.assertTrue(r["status"].startswith("200"), r["status"])
        self.assertIn("engine_db", r["json"])


if __name__ == "__main__":
    unittest.main()
