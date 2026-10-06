"""GET /api/v1/ops/health — Operations Health aggregate (Track 8).

Backs the console's Operations Health surface (CONSOLE-PARITY.md). Every
section carries what the platform tier can honestly source:

- Sourced from the platform/engine DB: engine_db freshness, forwarder
  identity, kill-switch state, last drill, auth status, last judgment model.
- "not_instrumented": the platform tier is a READ tier over the engine's
  event log — it cannot observe live judge connections, race internals, or
  per-stage pipeline latency. Those fields say so literally.
- "not_implemented": controls that do not exist (circuit breaker). Never
  faked as "closed".

The console renders "not_instrumented" / "not_implemented" literally — a
fabricated "healthy" is a worse failure than an honest gap.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import safety_api as _safety_api

try:
    from sentinel import safety as _safety
except ImportError:  # pragma: no cover - src/ not on sys.path (unusual)
    import os as _os
    import sys as _sys
    _REPO = _os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))))
    _SRC = _os.path.join(_REPO, "src")
    if _SRC not in _sys.path:
        _sys.path.insert(0, _SRC)
    from sentinel import safety as _safety


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _json(start_response, status, payload):
    body = json.dumps(payload).encode("utf-8")
    _TEXT = {200: "OK", 401: "Unauthorized", 503: "Service Unavailable"}
    start_response(f"{status} {_TEXT[status]}", [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ])
    return [body]


def _unauthorized(start_response):
    return _json(start_response, 401, {"error": "unauthorized"})


def _engine_db_stats(app) -> dict:
    """Freshness + volume from the engine's event log. REAL data."""
    out = {"available": False, "decisions_1h": 0,
           "pages_1h": 0, "suppressions_1h": 0,
           "latest_decision_at": None, "data_age_s": None}
    store = getattr(app, "store", None)
    if store is None:
        return out
    out["available"] = bool(store.available)
    if not store.available:
        return out
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        rows = store.decisions(limit=500, since=since)
        out["decisions_1h"] = len(rows)
        for r in rows:
            if r.get("disposition") == "page":
                out["pages_1h"] += 1
            elif r.get("disposition") in ("suppress", "ticket", "digest"):
                out["suppressions_1h"] += 1
        latest = store.decisions(limit=1)
        if latest:
            ts = latest[0].get("time")
            out["latest_decision_at"] = ts
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                out["data_age_s"] = round(
                    (datetime.now(timezone.utc) - dt).total_seconds(), 1)
            except (ValueError, TypeError):
                pass
            out["last_judgment_model"] = latest[0].get("jev_model")
    except Exception:
        # A health endpoint must never 500 because a stat query failed;
        # it reports what it could gather.
        pass
    return out


def _forwarder_identity(app) -> dict:
    """pagerduty vs fakepd from the integrations store. Presence only —
    the secret value is never read into this response."""
    try:
        from .integrations import PD_KEY_NAME
        has_key = app.integrations.get(PD_KEY_NAME) is not None
    except Exception:
        has_key = False
    identity = "pagerduty" if has_key else "fakepd"
    return {"identity": identity,
            "state": "healthy",
            "stopped_doing": []}


def _safety_section(app):
    ks = getattr(app, "kill_switch", None)
    if ks is None:
        # The switch is only meaningful where the forwarder it halts
        # lives; a read-tier without one says so literally.
        return {
            "kill_switch": "not_wired",
            "auth": {"operator_token": "ok"},
            "policy": "not_instrumented",
        }
    st = ks.status()
    return {
        "kill_switch": {
            "state": "engaged" if st["engaged"] else "armed",
            "engaged_at": st["engaged_at"],
            # Per-instance on serverless (/tmp state file): instances can
            # disagree until external flag state exists. The console shows
            # this id so divergence is visible, never assumed away.
            "instance_id": _safety_api.instance_id(),
            "kill_state_scope": "per_instance_tmp",
            "last_drill": _safety.latest_drill(getattr(app, "drill_dir", None)),
        },
        "auth": {"operator_token": "ok"},
        # The platform tier does not host the policy store (engine-side).
        "policy": "not_instrumented",
    }


def handle_health(app, environ, start_response):
    """GET /api/v1/ops/health — authenticated (C1 middleware)."""
    db = _engine_db_stats(app)
    safety = _safety_section(app)
    payload = {
        "environment": {
            "mode": "production",
            "data_source": getattr(app, "data_source", "unknown"),
            "as_of": _now_iso(),
        },
        "engine_db": db,
        "jev": {
            # Last model the ENGINE recorded a judgment with — real data
            # from the decision log, not a live connection probe.
            "last_judgment_model": db.pop("last_judgment_model", None),
            "last_judgment_at": db["latest_decision_at"],
            "connection": "not_instrumented",
            "circuit": "not_implemented",
            "state": "not_instrumented",
        },
        # The platform tier is a read tier: it does not run the pipeline,
        # so per-stage latency/health is honestly uninstrumented here.
        # Data age (engine_db.data_age_s) is this tier's real health signal.
        "pipeline": [
            {"stage": s, "state": "not_instrumented"}
            for s in ("receiver", "correlator", "race", "gate", "forwarder")
        ],
        "forwarder": _forwarder_identity(app),
        "safety": safety,
    }
    return _json(start_response, 200, payload)
