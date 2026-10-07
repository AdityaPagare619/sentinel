"""Vercel wrapper auth-ordering tests (security lane, 2026-10-07).

Failing-before/passing-after: unauthenticated /api/stream must 401.
The audit's P3: both Vercel wrappers (deploy/vercel/api/index.py and
index-prod.py) answered /api/stream with 501 stream_unsupported BEFORE
auth — a path oracle and a contract split vs the main app, which
enforces C1 first (PlatformApp.__call__ step 1 → step 3).

Each wrapper is imported in a FRESH subprocess against a temp dist
layout (_srv -> platform/server, _eng/sentinel -> src/sentinel) so the
_srv/_eng module aliases can't collide with the in-process test imports.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)

PRIMARY = "wrapper-test-primary-token-0001"
PREVIOUS = "wrapper-test-previous-token-0002"

_PROBE = r'''
import importlib.util, json, os, sys

api_dir, wrapper_file = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location(
    "wrapper_under_test", os.path.join(api_dir, wrapper_file))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def call(path, auth=None, origin=None):
    env = {"PATH_INFO": path, "REQUEST_METHOD": "GET",
           "QUERY_STRING": "", "CONTENT_LENGTH": "0",
           "wsgi.input": __import__("io").BytesIO(b"")}
    if auth:
        env["HTTP_AUTHORIZATION"] = auth
    if origin:
        env["HTTP_ORIGIN"] = origin
    cap = {}
    def start_response(status, headers, exc_info=None):
        cap["status"] = status
        cap["headers"] = {k.lower(): v for k, v in headers}
    body = b"".join(mod.app(env, start_response))
    cap["body"] = body.decode()
    try:
        cap["json"] = json.loads(cap["body"])
    except ValueError:
        cap["json"] = None
    return cap

cases = [
    # (path, auth header, origin, expect_status_prefix, expect_in_body)
    ("/api/stream", None, None, "401", "unauthorized"),
    ("/api/stream", "Bearer wrong-token", None, "401", "unauthorized"),
    ("/api/stream", "Bearer " + os.environ["PRIMARY"], None,
     "501", "stream_unsupported"),
    ("/api/stream", "Bearer " + os.environ["PREVIOUS"], None,
     "501", "stream_unsupported"),  # dual-accept through the wrapper
    ("/api/v1/ops/health", None, None, "401", "unauthorized"),
    ("/api/stream", None, "https://adityapagare619.github.io",
     "401", "unauthorized"),  # 401 carries the CORS allowlist echo
]
out = []
for path, auth, origin, want_status, want_body in cases:
    r = call(path, auth, origin)
    out.append({
        "path": path,
        "auth": "primary" if auth and "primary" in (auth or "")
                else "previous" if auth and "previous" in (auth or "")
                else ("wrong" if auth else "none"),
        "origin": origin,
        "status": r["status"],
        "headers": r["headers"],
        "body": r["body"][:120],
        "ok_status": r["status"].startswith(want_status),
        "ok_body": want_body in r["body"],
    })
print(json.dumps(out))
'''


def _run_wrapper(wrapper_file):
    tmp = tempfile.mkdtemp(prefix="wrapdist-")
    api = os.path.join(tmp, "api")
    os.makedirs(os.path.join(api, "_eng"))
    os.symlink(os.path.join(_REPO, "platform", "server"),
               os.path.join(api, "_srv"))
    os.symlink(os.path.join(_REPO, "src", "sentinel"),
               os.path.join(api, "_eng", "sentinel"))
    src = os.path.join(_REPO, "deploy", "vercel", "api", wrapper_file)
    dst = os.path.join(api, wrapper_file)
    with open(src) as f_in, open(dst, "w") as f_out:
        f_out.write(f_in.read())
    env = dict(os.environ)
    env["PRIMARY"] = PRIMARY
    env["PREVIOUS"] = PREVIOUS
    env["SENTINEL_OPERATOR_TOKEN"] = PRIMARY
    env["SENTINEL_OPERATOR_TOKEN_PREVIOUS"] = PREVIOUS
    env["SENTINEL_DB"] = os.path.join(tmp, "no-such.db")  # empty state OK
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, api, wrapper_file],
        capture_output=True, text=True, env=env, timeout=120, cwd=_REPO)
    if proc.returncode != 0:
        raise AssertionError(
            f"wrapper probe failed for {wrapper_file}:\n"
            f"STDOUT:\n{proc.stdout[-2000:]}\nSTDERR:\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


class WrapperAuthCase(unittest.TestCase):
    def _check(self, wrapper_file):
        results = _run_wrapper(wrapper_file)
        self.assertEqual(len(results), 6, results)
        for r in results:
            self.assertTrue(r["ok_status"],
                            f"{wrapper_file} {r['path']} auth={r['auth']}: "
                            f"status {r['status']}")
            self.assertTrue(r["ok_body"],
                            f"{wrapper_file} {r['path']} auth={r['auth']}: "
                            f"body {r['body']!r}")
        # the 401s carry the RFC 6750 challenge, exactly like the app's
        unauth = [r for r in results if r["auth"] == "none"
                  and r["path"] == "/api/stream"][0]
        challenge = unauth["headers"].get("www-authenticate", "")
        self.assertIn('error="invalid_token"', challenge,
                      f"{wrapper_file}: {challenge!r}")
        # the CORS allowlist echo rides the wrapper 401 too (uniform 401)
        cors_case = [r for r in results if r["origin"]][0]
        self.assertEqual(
            cors_case["headers"].get("access-control-allow-origin"),
            "https://adityapagare619.github.io",
            f"{wrapper_file}: CORS echo missing on wrapper 401")

    def test_index_prod_stream_behind_auth(self):
        self._check("index-prod.py")

    def test_index_demo_stream_behind_auth(self):
        self._check("index.py")


if __name__ == "__main__":
    unittest.main()
