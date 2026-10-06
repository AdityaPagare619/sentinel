"""C3 safety endpoints (Track 3) — handler bodies for the platform server.

Routed from ``app.PlatformApp._api`` (three lines); all logic lives here so
Track 1's auth work in app.py stays unentangled. Loaded via ``_pkg`` as
``sentinel_platform.safety_api``.

Wire contract (C1, verbatim): unauthenticated → ``401 {"error":
"unauthorized"}``. Track 1 installs the real token verifier via
``sentinel.safety.set_operator_verifier``; until then the seam is
fail-closed (every token 401s).

Endpoints
---------
POST /api/v1/safety/kill   — flip the kill switch immediately (idempotent).
POST /api/v1/safety/rearm  — re-arm; requires {"confirm": true}, else 422.
GET  /api/v1/safety/status — {engaged, engaged_at, last_drill}; last_drill
                             is None until a drill artifact exists, and the
                             UI must render "unmeasured" in that case (C3).
"""

from __future__ import annotations

import io
import json
import os
import sys

try:
    from sentinel import safety as _safety
except ImportError:  # src/ not on sys.path (unusual) — resolve it ourselves
    _REPO = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    _SRC = os.path.join(_REPO, "src")
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    from sentinel import safety as _safety

_MAX_BODY = 64 * 1024


# ------------------------------------------------------------------ helpers


def _json(start_response, status, payload):
    body = json.dumps(payload).encode("utf-8")
    start_response(f"{status} {_STATUS_TEXT[status]}", [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ])
    return [body]


_STATUS_TEXT = {200: "OK", 401: "Unauthorized", 404: "Not Found",
                422: "Unprocessable Entity", 503: "Service Unavailable"}


def _unauthorized(start_response):
    # C1 wire contract, verbatim: 401 JSON {"error": "unauthorized"}.
    return _json(start_response, 401, {"error": "unauthorized"})


def _actor_or_401(environ, start_response):
    try:
        return _safety.require_operator(environ)
    except _safety.Unauthorized:
        return None


def _read_body(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > _MAX_BODY:
        return None
    raw = environ["wsgi.input"].read(length) if length else b""
    if not raw:
        return {}
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _kill_switch_or_503(app, start_response):
    ks = getattr(app, "kill_switch", None)
    if ks is None:
        return None, _json(start_response, 503, {
            "error": "safety_unavailable",
            "message": "kill switch is not wired in this process",
        })
    return ks, None


# ------------------------------------------------------------------ handlers


def handle_kill(app, environ, start_response):
    """POST /api/v1/safety/kill — flip immediately. Idempotent."""
    actor_id = _actor_or_401(environ, start_response)
    if actor_id is None:
        return _unauthorized(start_response)
    ks, err = _kill_switch_or_503(app, start_response)
    if err:
        return err
    # Control principle: the kill path never calls Jev, never waits on the
    # race — KillSwitch.engage touches only the flag, the state file, and
    # the audit log.
    state = ks.engage(actor="operator", actor_id=actor_id)
    return _json(start_response, 200, {
        "engaged": True,
        "engaged_at": state["engaged_at"],
        "actor_id": actor_id,
        "transitioned": state["transitioned"],
    })


def handle_rearm(app, environ, start_response):
    """POST /api/v1/safety/rearm — SEPARATE deliberate action. Requires an
    explicit {"confirm": true} body; anything else is rejected (no silent
    auto-rearm, ever)."""
    actor_id = _actor_or_401(environ, start_response)
    if actor_id is None:
        return _unauthorized(start_response)
    ks, err = _kill_switch_or_503(app, start_response)
    if err:
        return err
    body = _read_body(environ)
    if body is None:
        return _json(start_response, 422, {
            "error": "bad_json",
            "message": "request body must be a JSON object",
        })
    if body.get("confirm") is not True:
        return _json(start_response, 422, {
            "error": "rearm_requires_confirmation",
            "message": 're-arm is a separate deliberate action: resend with '
                       '{"confirm": true}. No silent auto-rearm, ever.',
        })
    try:
        state = ks.disengage(actor="operator", actor_id=actor_id,
                             confirm=True)
    except _safety.RearmRefused as exc:  # unreachable via this path; be safe
        return _json(start_response, 422, {
            "error": "rearm_requires_confirmation", "message": str(exc)})
    return _json(start_response, 200, {
        "engaged": False,
        "actor_id": actor_id,
        "transitioned": state["transitioned"],
    })


def handle_status(app, environ, start_response):
    """GET /api/v1/safety/status — kill state + newest drill artifact.

    ``last_drill`` is None until a drill artifact exists; the console must
    render "unmeasured" in that case — the <5s claim is FORBIDDEN without
    an artifact (contract C3).
    """
    actor_id = _actor_or_401(environ, start_response)
    if actor_id is None:
        return _unauthorized(start_response)
    ks, err = _kill_switch_or_503(app, start_response)
    if err:
        return err
    st = ks.status()
    return _json(start_response, 200, {
        "engaged": st["engaged"],
        "engaged_at": st["engaged_at"],
        "last_drill": _safety.latest_drill(getattr(app, "drill_dir", None)),
    })
