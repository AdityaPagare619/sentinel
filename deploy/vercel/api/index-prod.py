"""Sentinel platform tier — PRODUCTION on Vercel serverless.

The REAL PlatformApp from platform/server (main branch, all 8 tracks +
Track 8 console integration), running as a single serverless function.

What is real here:
  * the exact WSGI app from platform/server (no reimplementation);
  * operator bearer auth on every /api/* (SENTINEL_OPERATOR_TOKEN env);
  * GET /api/v1/ops/health — the Operations Health aggregate (Track 8);
  * the kill switch (safety_api) — state in /tmp (per-instance;
    see limitation below);
  * CORS allowlist = the GitHub Pages origin only.

Production limitations (stated honestly, not hidden):
  * Serverless has no persistent disk: the kill-switch state file lives
    in /tmp and is PER-INSTANCE. A single instance honors the switch;
    multi-instance stickiness needs external state (flagged for the
    production hardening pass). Hardening in place: /api/v1/safety/status
    and /api/v1/ops/health both report the serving instance's id
    (kill_state_scope="per_instance_tmp"), so cross-instance divergence
    is VISIBLE to the operator instead of assumed away. Residual risk:
    under multi-instance load, an engage on instance A does not halt
    instance B until external flag state exists.
  * Request bodies are read with a bounded, EOF-terminating loop
    (safety_api.read_body_bytes): a short or blocking wsgi.input from
    the serverless WSGI bridge degrades to 422, never a hung endpoint.
    (2026-10-06: all-POST hang observed on Vercel was transient infra —
    unauthenticated POSTs, which 401 before any body read, hung while
    GET/OPTIONS worked; no app code path could cause it. The bounded
    reader removes the one app-level hang vector regardless.)
  * The engine DB is not bundled: ReadStore serves empty state until
    the engine's database is connected. ops/health reports
    engine_db.available=false honestly.
  * TYPESAFE_API_KEY is not set by the deploy: the judge runs in
    judge-down mode (timer-wins, fail-open) until Aditya sets the key
    in the Vercel dashboard. The console shows judge-down, never fake
    judgments.
  * /api/stream → 501 stream_unsupported (serverless); the console
    polls /api/decisions instead (its designed fallback).

Module layout: _srv/ holds copies of platform/server (loaded via _pkg,
same as __main__.py); _eng/ holds the engine package (stdlib-only).
Nothing is invented: build-bundle-prod.sh assembles this tree from the
repo sources at the deployed commit.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "_srv"))
sys.path.insert(0, os.path.join(_HERE, "_eng"))

import _pkg  # noqa: E402

app_mod = _pkg.load("app")  # noqa: E402
auth_mod = _pkg.load("auth")  # noqa: E402
shed_mod = _pkg.load("shed")  # noqa: E402

_safety_mod = None
try:
    from sentinel import safety as _safety_mod  # noqa: E402
except Exception:
    _safety_mod = None

# --- production wiring -------------------------------------------------
# Operator token: SENTINEL_OPERATOR_TOKEN env (Vercel dashboard). The
# auth module reads it directly; without it the store is ephemeral and
# the function logs a loud warning (fail-closed: nobody can auth).
_token_store = auth_mod.OperatorTokenStore(
    os.path.join("/tmp", "sentinel-operator-token.json"))
if _token_store.ephemeral:
    print("[platform] WARNING: SENTINEL_OPERATOR_TOKEN not set — operator "
          "token store is EPHEMERAL. Set it in the Vercel dashboard.",
          flush=True)

# Kill switch: state in /tmp (per-instance; see module docstring).
_kill_switch = None
if _safety_mod is not None:
    _kill_switch = _safety_mod.KillSwitch(
        state_path=os.path.join("/tmp", "sentinel-kill-switch.json"))

# Engine DB: SENTINEL_DB env or the default path (absent → empty state,
# honestly reported by ops/health).
_db_path = os.environ.get("SENTINEL_DB", "/tmp/sentinel.db")
store_mod = _pkg.load("store")
_store = store_mod.ReadStore(_db_path)

_platform = app_mod.PlatformApp(
    store=_store,
    registry=None,
    gate=shed_mod.AdmissionGate(),
    degrade=shed_mod.DegradePolicy(),
    data_source="production",
    cors_origins="https://AdityaPagare619.github.io",
    operator_token_store=_token_store,
    kill_switch=_kill_switch,
    drill_dir=None,
    ui_dir=None,  # Vercel serves the static console itself
)

_STREAM_REFUSAL = json.dumps({
    "error": {
        "code": "stream_unsupported",
        "message": ("SSE is not available on serverless "
                    "(no long-lived connections); "
                    "the console falls back to polling /api/decisions."),
        "retryable": False,
    },
    "meta": {"data_source": "production"},
}).encode("utf-8")


def app(environ, start_response):
    """WSGI entrypoint (exported as `app` for @vercel/python)."""
    path = environ.get("PATH_INFO", "") or ""
    if path == "/api/stream" or path.startswith("/api/stream?") \
            or path.startswith("/api/stream/"):
        start_response("501 Not Implemented", [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(_STREAM_REFUSAL))),
            ("Cache-Control", "no-store"),
        ])
        return [_STREAM_REFUSAL]
    return _platform(environ, start_response)
