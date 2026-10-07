"""CORS tests for the platform read API (gh-pages-envs lane, F1).

The GitHub Pages prod console fetches this API cross-origin, so every
/api/* response must carry Access-Control-Allow-Origin and OPTIONS
preflights must be answered. Uses stub store/gate/degrade — no fixtures,
no DB (the pre-existing server suite has unrelated fixture drift).
"""
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
# Import the server package as top-level `server` to dodge the
# `platform` <-> stdlib module name clash.
sys.path.insert(0, os.path.join(REPO, "platform"))
sys.path.insert(0, os.path.join(REPO, "src"))  # server.simulate needs it

import server.app as appmod  # noqa: E402


class _Gate:
    def try_acquire(self):
        return True

    def release(self):
        pass


class _Degrade:
    def should_shed(self, path):
        return False, ""


def _make_app(**kw):
    tmp = tempfile.mkdtemp(prefix="sentinel-cors-test-")
    os.environ["SENTINEL_INTEGRATIONS_FILE"] = os.path.join(
        tmp, "integrations.json")
    args = dict(store=None, registry=None, gate=_Gate(),
                degrade=_Degrade())
    args.update(kw)
    return appmod.PlatformApp(**args)


def _call(app, path, method="GET", origin=None):
    env = {"PATH_INFO": path, "REQUEST_METHOD": method,
           "QUERY_STRING": "", "wsgi.input": None}
    if origin:
        env["HTTP_ORIGIN"] = origin
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = {k.lower(): v for k, v in headers}

    body = b"".join(app(env, start_response))
    captured["body"] = body
    return captured


class TestCors(unittest.TestCase):
    def test_default_cors_is_allowlist_not_star(self):
        # Contract C1 (Track 1): the DEFAULT allowlist contains ONLY the
        # hosted prod console. Arbitrary origins get no ACAO header —
        # their browsers cannot read /api/* responses.
        app = _make_app()
        r = _call(app, "/api/nope",
                  origin="https://AdityaPagare619.github.io")
        self.assertEqual(r["headers"].get("access-control-allow-origin"),
                         "https://AdityaPagare619.github.io")
        r2 = _call(app, "/api/nope", origin="https://evil.example.com")
        self.assertNotIn("access-control-allow-origin", r2["headers"])

    def test_options_preflight_answered(self):
        r = _call(_make_app(), "/api/simulate", method="OPTIONS",
                  origin="https://AdityaPagare619.github.io")
        self.assertEqual(r["status"].split()[0], "204")
        h = r["headers"]
        self.assertEqual(h.get("access-control-allow-origin"),
                         "https://AdityaPagare619.github.io")
        self.assertIn("POST", h.get("access-control-allow-methods", ""))
        self.assertIn("content-type",
                      h.get("access-control-allow-headers", "").lower())

    def test_explicit_star_is_opt_in(self):
        # "*" is available ONLY via an explicit opt-in (__main__ prints a
        # loud warning in that case).
        r = _call(_make_app(cors_origins="*"), "/api/nope",
                  origin="https://evil.example.com")
        self.assertEqual(r["headers"].get("access-control-allow-origin"),
                         "*")

    def test_allowlist_echoes_listed_origin_only(self):
        app = _make_app(cors_origins="https://AdityaPagare619.github.io")
        r = _call(app, "/api/nope",
                  origin="https://AdityaPagare619.github.io")
        self.assertEqual(r["headers"].get("access-control-allow-origin"),
                         "https://AdityaPagare619.github.io")
        self.assertEqual(r["headers"].get("vary"), "Origin")
        r2 = _call(app, "/api/nope", origin="https://evil.example.com")
        self.assertNotIn("access-control-allow-origin", r2["headers"])

    def test_cors_off_emits_nothing(self):
        r = _call(_make_app(cors_origins=""), "/api/nope",
                  origin="https://AdityaPagare619.github.io")
        self.assertNotIn("access-control-allow-origin", r["headers"])

    def test_origin_match_is_case_insensitive_on_host(self):
        # REGRESSION (2026-10-07): the deployed allowlist was written
        # "https://AdityaPagare619.github.io" but browsers send the
        # lowercased host "https://adityapagare619.github.io" (WHATWG URL).
        # Exact-case matching made every preflight from the real console
        # fail — the browser blocked all /api/* fetches and the Ops Health
        # drawer sat at "No backend data yet" with no error. Host case is
        # insignificant per RFC 6454; the allowlist still exact-matches
        # otherwise (evil origins get nothing).
        app = _make_app(cors_origins="https://AdityaPagare619.github.io")
        r = _call(app, "/api/v1/ops/health",
                  origin="https://adityapagare619.github.io")
        self.assertEqual(r["headers"].get("access-control-allow-origin"),
                         "https://adityapagare619.github.io")
        r2 = _call(app, "/api/nope", origin="https://evil.example.com")
        self.assertNotIn("access-control-allow-origin", r2["headers"])

    def test_preflight_does_not_touch_api(self):
        # Preflight must not reach the API router (no store needed even
        # for unknown paths).
        r = _call(_make_app(), "/api/definitely-not-real", method="OPTIONS")
        self.assertEqual(r["status"].split()[0], "204")
        self.assertEqual(r["body"], b"")


if __name__ == "__main__":
    unittest.main()
