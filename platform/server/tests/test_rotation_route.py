"""Rotation route wiring tests (Track 4, C4).

Proves POST /api/v1/keys/{name}/rotate is live behind C1 auth and runs
the full ceremony through HTTP: unauthenticated -> 401, authed ->
stage/verify/promote/retire with the old token dying at retire.
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
        return (False, "")


class RotationRouteCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["SENTINEL_STATE_DIR"] = self.tmp.name
        self.addCleanup(os.environ.pop, "SENTINEL_STATE_DIR", None)
        self.store = OperatorTokenStore(
            os.path.join(self.tmp.name, "operator_token.json"))
        self.token = self.store.first_boot_token
        self.app = PlatformApp(store=None, registry=None, gate=_Gate(),
                               degrade=_Degrade(),
                               operator_token_store=self.store)

    def call(self, path, body=None, auth_header="__UNSET__"):
        raw = json.dumps(body or {}).encode()
        env = {"PATH_INFO": path, "REQUEST_METHOD": "POST",
               "QUERY_STRING": "", "CONTENT_LENGTH": str(len(raw)),
               "wsgi.input": io.BytesIO(raw),
               "HTTP_ORIGIN": "https://AdityaPagare619.github.io"}
        if auth_header == "__UNSET__":
            env["HTTP_AUTHORIZATION"] = "Bearer " + self.token
        elif auth_header is not None:
            env["HTTP_AUTHORIZATION"] = auth_header
        captured = {}

        def start_response(status, headers, exc_info=None):
            captured["status"] = status

        out = b"".join(self.app(env, start_response))
        captured["body"] = out
        try:
            captured["json"] = json.loads(out.decode()) if out else None
        except ValueError:
            captured["json"] = None
        return captured

    def test_unauthenticated_rotate_is_401(self):
        r = self.call("/api/v1/keys/operator_bearer/rotate",
                      body={"stage": "status"}, auth_header=None)
        self.assertTrue(r["status"].startswith("401"), r["status"])
        self.assertEqual(r["json"], {"error": "unauthorized"})

    def test_full_ceremony_through_http(self):
        new = "rotated-operator-token-00000002"
        base = "/api/v1/keys/operator_bearer/rotate"
        r = self.call(base, body={"stage": "status"})
        self.assertTrue(r["status"].startswith("200"), r)
        self.assertEqual(r["json"]["data"]["generation"], 1)

        r = self.call(base, body={"stage": "stage_secondary",
                                 "value": new})
        self.assertTrue(r["status"].startswith("200"), r)
        self.assertTrue(r["json"]["data"]["secondary_staged"])

        r = self.call(base, body={"stage": "verify_secondary",
                                 "value": new})
        self.assertTrue(r["status"].startswith("200"), r)

        r = self.call(base, body={"stage": "promote"})
        self.assertTrue(r["status"].startswith("200"), r)
        self.assertEqual(r["json"]["data"]["generation_after"], 2)
        # dual-accept during grace: old AND new both verify
        self.assertTrue(self.store.verify(self.token))
        self.assertTrue(self.store.verify(new))

        r = self.call(base, body={"stage": "retire"})
        self.assertTrue(r["status"].startswith("200"), r)
        # old token dead, new token live
        self.assertFalse(self.store.verify(self.token))
        self.assertTrue(self.store.verify(new))

    def test_unknown_secret_is_404_and_bad_stage_is_400(self):
        r = self.call("/api/v1/keys/nope/rotate",
                      body={"stage": "status"})
        self.assertTrue(r["status"].startswith("404"), r["status"])
        r = self.call("/api/v1/keys/operator_bearer/rotate",
                      body={"stage": "bogus"})
        self.assertTrue(r["status"].startswith("400"), r["status"])

    def test_no_secret_values_in_responses(self):
        new = "rotated-operator-token-00000003"
        base = "/api/v1/keys/operator_bearer/rotate"
        self.call(base, body={"stage": "stage_secondary", "value": new})
        r = self.call(base, body={"stage": "status"})
        blob = json.dumps(r["json"]["data"])
        self.assertNotIn(new, blob)
        self.assertNotIn(self.token, blob)


if __name__ == "__main__":
    unittest.main()
