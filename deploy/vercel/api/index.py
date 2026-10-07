"""Sentinel platform tier on Vercel serverless (deploy lane).

The REAL PlatformApp — the frozen read API (platform/contracts/openapi.yaml
v1.0.0) — running as a single serverless function over a READ-ONLY demo
dataset snapshot. Stdlib only.

What is real here:
  * the exact WSGI app from platform/server (no reimplementation);
  * read-only: demo.db is opened mode=ro + query_only, exactly like the
    demo-box server; the serving path makes zero Jev calls;
  * every envelope carries data_source=synthetic (the UI renders it);
  * /api/simulate runs the REAL tuner math (sentinel.tuner, same code path
    as the engine) over the hash-pinned labels-v3 dataset.

What is NOT real (stated at the URL in ABOUT-THIS-DEPLOYMENT.md and in a
banner injected into the bundled index.html):
  * the data: a synthetic storm recorded by the real engine on 2026-10-03
    (43 events / 42 decisions), plus synthetic reference labels;
  * /api/stream: serverless has no long-lived connections, so it is refused
    with 501 stream_unsupported; the UI falls back to its 30s polling loop;
  * there is no paging receiver, no PagerDuty routing, no Jev key here —
    this URL can never page anyone.

Module layout: _srv/ holds copies of platform/server (loaded under the
sentinel_platform alias via _pkg, same as __main__.py); _eng/ holds the
engine package (stdlib-only); _data/ holds the read-only snapshot.
Nothing is invented: build-bundle.sh assembles this tree from the repo
sources.
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
store_mod = _pkg.load("store")  # noqa: E402
datasets_mod = _pkg.load("datasets")  # noqa: E402
shed_mod = _pkg.load("shed")  # noqa: E402

_DATA = os.path.join(_HERE, "_data")

_store = store_mod.ReadStore(
    os.path.join(_DATA, "demo.db"),
    context=store_mod.load_context_jsonl(os.path.join(_DATA, "context.jsonl")),
)
_registry = datasets_mod.DatasetRegistry(os.path.join(_DATA, "datasets"))
_platform = app_mod.PlatformApp(
    store=_store,
    registry=_registry,
    gate=shed_mod.AdmissionGate(),
    degrade=shed_mod.DegradePolicy(),
    data_source="synthetic",
    labels_version="labels-v3",
    ui_dir=None,  # Vercel serves the static UI itself
)

# Track 1 (C1): the platform API needs the operator bearer token even on
# serverless. Provision it via the SENTINEL_OPERATOR_TOKEN env var (Vercel
# dashboard) — without it the store is ephemeral and every cold start
# mints a token nobody holds.
if _platform.operator_tokens.ephemeral:
    print("[platform] WARNING: operator token store is EPHEMERAL — set "
          "SENTINEL_OPERATOR_TOKEN in the Vercel dashboard or the hosted "
          "console cannot authenticate", flush=True)

_STREAM_REFUSAL = json.dumps({
    "error": {
        "code": "stream_unsupported",
        "message": ("SSE is not available on the hosted demo "
                    "(serverless has no long-lived connections); "
                    "the UI falls back to 30s polling of /api/decisions."),
        "retryable": False,
    },
    "meta": {
        "contract_version": "1.0.0",
        "data_source": "synthetic",
    },
}).encode("utf-8")


def _c1_ok(environ):
    """C1 operator check for the serverless stream short-circuit.

    The main app enforces auth BEFORE the stream refusal
    (PlatformApp.__call__ step 1 → step 3); this wrapper must not answer
    unauthenticated callers with a 501 — that would be a path oracle and
    a contract split. Same store, same 401 shape as the app.
    """
    store = _platform.operator_tokens
    presented = store.bearer_from_header(environ.get("HTTP_AUTHORIZATION"))
    return store.verify(presented)


def _sr_with_cors(environ, start_response):
    """Wrap start_response with the platform's CORS headers.

    The main app injects CORS on EVERY response via its own wrapper; the
    stream short-circuit bypasses __call__, so the 401 it returns must
    carry the same CORS treatment — one 401 shape everywhere.
    """
    cors = _platform._cors_headers(environ)

    def _sr(status, headers, exc_info=None):
        seen = {n.lower() for n, _ in headers}
        extra = [(n, v) for n, v in cors if n.lower() not in seen]
        if exc_info is None:
            return start_response(status, headers + extra)
        return start_response(status, headers + extra, exc_info)

    return _sr


def app(environ, start_response):
    """WSGI entrypoint (exported as `app` for @vercel/python)."""
    path = environ.get("PATH_INFO", "") or ""
    if path == "/api/stream" or path.startswith("/api/stream?") \
            or path.startswith("/api/stream/"):
        if not _c1_ok(environ):
            return _platform._unauthorized(_sr_with_cors(environ,
                                                         start_response))
        start_response("501 Not Implemented", [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(_STREAM_REFUSAL))),
            ("Cache-Control", "no-store"),
        ])
        return [_STREAM_REFUSAL]
    return _platform(environ, start_response)
