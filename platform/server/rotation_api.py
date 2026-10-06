"""Rotation HTTP service — Track 4 (contract C4).

Framework-free: `RotationService` exposes the ceremony stages as pure
methods returning `(status_code, body_dict)`. Wiring (auth per C1, route
registration) belongs to Track 1 / the coordinator — this lane does not
touch middleware. One import in app.py is the whole integration::

    from .rotation_api import RotationService
    rotation = RotationService(registry)   # registry: {secret_name: store}
    code, body = rotation.handle(name, stage, payload, actor)

API contract (for Track 8's console), all C1-authed:

    POST /api/v1/keys/{name}/rotate   {"stage": "stage_secondary", "value": "..."}
        → 200 {"name","generation","secondary_staged": true}
    POST /api/v1/keys/{name}/rotate   {"stage": "verify_secondary", "value": "..."}
        → 200 {"ok": true, ...} | 422 {"ok": false, "reason": ...}
    POST /api/v1/keys/{name}/rotate   {"stage": "promote"}
        → 200 {"name","generation_before","generation_after"}
    POST /api/v1/keys/{name}/rotate   {"stage": "retire"}
        → 200 {"name","generation","retired": true}
    POST /api/v1/keys/{name}/rotate   {"stage": "status"}
        → 200 {"configured","secondary_staged","generation"}

Error shape: 400 unknown stage / 404 unknown secret / 409 illegal
transition / 422 bad value — always {"error": "..."} (never secret values).

Console contract (Track 8): the rotation panel for a secret shows its
`generation` and the staged state machine
idle → staged → verified → promoted → retired. Promote and retire are
separate deliberate buttons. Every transition surfaces its audit row
(actor, timestamp, generation before→after). On 401 the console shows the
C1 "operator sign-in required" state (console stores the token in
memory/session scope, never localStorage).
"""

from __future__ import annotations

from .keystore import RotationError

# Names this service will route. The registry maps each to its store; the
# coordinator wires the real stores (operator token, BYOK twins, webhook).
KNOWN_ROTATABLE = (
    "operator_bearer",
    "pagerduty_routing_key",
    "jev_api_key",
    "webhook_hmac",
)

STAGES = ("stage_secondary", "verify_secondary", "promote", "retire",
          "status")


class RotationService:
    """Ceremony stages over a {secret_name: store} registry.

    Each store must expose the RotatingKeyStore ceremony surface
    (stage_secondary / verify_secondary / promote / retire / status).
    `actor` is the authenticated operator identity (from C1 auth).
    """

    def __init__(self, registry: dict):
        self._registry = dict(registry)

    # ------------------------------------------------------------------ API
    def handle(self, name: str, stage: str, payload: dict,
               actor: str) -> tuple[int, dict]:
        """Dispatch one rotation stage. Returns (http_status, body)."""
        if name not in self._registry:
            return 404, {"error": f"unknown secret {name!r}",
                         "known": sorted(self._registry)}
        if stage not in STAGES:
            return 400, {"error": f"unknown stage {stage!r}",
                         "stages": list(STAGES)}
        store = self._registry[name]
        try:
            if stage == "stage_secondary":
                value = (payload or {}).get("value")
                if not value:
                    return 422, {"error": "stage_secondary requires 'value'"}
                return 200, {"name": name,
                             **store.stage_secondary(name, value, actor)}
            if stage == "verify_secondary":
                value = (payload or {}).get("value")
                if not value:
                    return 422, {"error": "verify_secondary requires 'value'"}
                result = store.verify_secondary(name, value, actor)
                if result["ok"]:
                    return 200, {"name": name, **result}
                return 422, {"name": name, **result,
                             "reason": "staged secret did not verify"}
            if stage == "promote":
                return 200, store.promote(name, actor)
            if stage == "retire":
                return 200, store.retire(name, actor)
            return 200, {"name": name, **store.status(name)}
        except RotationError as e:
            return 409, {"error": str(e)}
        except ValueError as e:
            # validator rejection (bad value shape) — safe to surface,
            # messages never echo the value.
            return 422, {"error": str(e)}

    def status_all(self) -> dict:
        """Public rotation overview for the console — no secret values."""
        return {name: store.status(name)
                for name, store in self._registry.items()}
