"""WSGI read API for the Sentinel platform tier (Forge's lane).

Implements platform/contracts/openapi.yaml v1.0.0 exactly:
  GET  /api/decisions[?limit&since_id&fingerprint&team&action&from&to]
  GET  /api/decision/<id>
  GET  /api/calibration[?team]          # ordinal rank-fidelity (AUC over p1 ranks),
                                       # rank deciles, coverage@tau — never a probability
  POST /api/simulate
  GET  /api/analytics/noise[?window]
  GET  /api/analytics/flips[?window]
  GET  /api/stream (SSE)

Plus: static UI at / when platform/ui/ exists (Prism's files; we serve,
they own).

Stdlib only (wsgiref-style app object; the entrypoint wraps it in
ThreadingHTTPServer). Read-only: the app never opens the engine DB
except through ReadStore's mode=ro connections, and never imports the
Jev client.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs

from . import simulate as sim
from . import auth as _authmod
from . import rotation_api as _rotmod
from . import safety_api
from . import ops_health
from .keystore import OPERATOR_TOKEN_NAME
from .datasets import UnknownDataset
from .integrations import (
    BadKey,
    EphemeralStoreError,
    IntegrationStore,
    JEV_KEY_NAME,
    PD_KEY_NAME,
)
from .shed import EXPENSIVE_PATHS
from .store import MAX_REPLAY, ReadStore

CONTRACT_VERSION = "1.0.0"

# Paths that bypass operator auth (contract C1 — these two and ONLY these).
_AUTH_EXEMPT = ("/api/v1/health/live", "/api/v1/health/ready")

TEAMS = ("platform", "network", "data", "product_backend", "security",
         "cannot_determine")
# D3: "folded" (storm-continuation absorbed into the aggregate page) is a
# stored action; the platform lane owns this file — flagged for awareness.
ACTIONS = ("page_now", "page_business_hours", "suppress", "passthrough",
           "folded")
WINDOW_RE = re.compile(r"^(\d+)([smhd])$")
FP_RE = re.compile(r"^[0-9a-f]{16}$")
WINDOW_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}
MAX_BODY = 64 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _window_seconds(window: str) -> int:
    m = WINDOW_RE.match(window or "")
    if not m:
        raise ValueError(f"window must match \\d+[smhd], got {window!r}")
    return int(m.group(1)) * WINDOW_S[m.group(2)]


class PlatformApp:
    """The frozen read API. Constructed once; __call__ is the WSGI app."""

    def __init__(self, *, store: ReadStore, registry, gate, degrade,
                 data_source: str = "shadow",
                 labels_version: str = "labels-v3",
                 ui_dir: str | None = None,
                 cors_origins: str = "https://adityapagare619.github.io",
                 operator_token_store=None,
                 kill_switch=None,
                 drill_dir: str | None = None):
        self.store = store
        self.registry = registry
        self.gate = gate
        self.degrade = degrade
        self.data_source = data_source
        self.labels_version = labels_version
        self.ui_dir = os.path.abspath(ui_dir) if ui_dir else None
        # C3 (Track 3): the kill switch + drill-artifact dir backing the
        # /api/v1/safety/* endpoints. None → the endpoints 503 with
        # safety_unavailable (the switch is only meaningful where the
        # forwarder it halts lives).
        self.kill_switch = kill_switch
        self.drill_dir = drill_dir
        if self.ui_dir and not os.path.isdir(self.ui_dir):
            self.ui_dir = None
        # CORS for the hosted-console deployment: the GitHub Pages prod
        # console fetches this API cross-origin, so the browser demands
        # Access-Control-Allow-Origin on every /api/* response plus an
        # OPTIONS preflight for POST/DELETE. Default (contract C1) is an
        # allowlist containing ONLY the hosted prod console — "*" is
        # available only via an explicit --cors-origins=* and prints a
        # loud startup warning. "" disables CORS (reverse proxy owns it).
        cors_origins = (cors_origins or "").strip()
        self._cors_off = (cors_origins == "")
        self._cors_star = (cors_origins == "*")
        self._cors_set = ({o.strip().lower() for o in cors_origins.split(",")
                           if o.strip()}
                          if not (self._cors_off or self._cors_star) else set())
        # BYOK integrations settings (the platform tier's ONE write surface —
        # keys only, namespaced under /api/v1/integrations/). Twin of
        # sentinel/integrations.py; same file, same format.
        self.integrations = IntegrationStore()
        # Per-install operator bearer token (Track 1, C1). Verified on
        # every /api/* request except the two health probes.
        self.operator_tokens = (operator_token_store
                                or _authmod.OperatorTokenStore())
        # Track 3 safety_api seam: install Track 1's real verifier now
        # that C1 is merged (was fail-closed on the lane branch). The
        # /api/* middleware above already enforces the bearer token;
        # this is defense-in-depth inside the safety handlers.
        _tokens = self.operator_tokens
        safety_api._safety.set_operator_verifier(
            lambda token: "operator" if _tokens.verify(token) else None)
        # Rotation ceremony (Track 4, C4) over the canonical keystore.
        # None when the token is env-provisioned or ephemeral — rotation
        # needs a file-backed store; the route then fails honestly.
        _ks = self.operator_tokens.keystore
        self.rotation = (_rotmod.RotationService({OPERATOR_TOKEN_NAME: _ks})
                         if _ks is not None else None)

    def _cors_headers(self, environ) -> list:
        """CORS headers for this request. "" disables (proxy owns the
        policy); "*" needs no Origin check; an allowlist echoes back only
        a listed Origin (with Vary).

        Comparison is case-insensitive on the whole origin: origins are
        scheme://host[:port] and the host is case-insensitive per RFC 6454
        / WHATWG URL (browsers lowercase it, so the real GitHub Pages
        origin arrives as https://adityapagare619.github.io even when the
        allowlist was written with capitals). Exact-match otherwise — no
        reflection, no wildcard — so the allowlist is not weakened.
        """
        if self._cors_off:
            return []
        if self._cors_star:
            return [("Access-Control-Allow-Origin", "*")]
        origin = environ.get("HTTP_ORIGIN", "")
        if origin and origin.lower() in self._cors_set:
            return [("Access-Control-Allow-Origin", origin),
                    ("Vary", "Origin")]
        return []

    # ------------------------------------------------------------- WSGI

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "/") or "/"
        method = environ.get("REQUEST_METHOD", "GET").upper()

        # CORS: inject Access-Control-Allow-Origin on EVERY response via a
        # wrapped start_response, so _ok/_error/_stream/_static all comply
        # without per-callsite changes. Preflight first — it is cheap and
        # must not consume admission-control slots.
        cors = self._cors_headers(environ)
        _wsgi_start = start_response  # capture before rebinding below

        def _sr(status, headers, exc_info=None):
            seen = {n.lower() for n, _ in headers}
            extra = [(n, v) for n, v in cors if n.lower() not in seen]
            # exc_info must only be passed when present: many test doubles
            # (and some servers) implement the 2-arg start_response form.
            if exc_info is None:
                return _wsgi_start(status, headers + extra)
            return _wsgi_start(status, headers + extra, exc_info)

        if method == "OPTIONS" and path.startswith("/api/"):
            _sr("204 No Content", [
                ("Content-Length", "0"),
                ("Access-Control-Allow-Methods",
                 "GET, POST, DELETE, OPTIONS"),
                ("Access-Control-Allow-Headers",
                 "Content-Type, Authorization"),
                ("Access-Control-Max-Age", "600"),
            ])
            return [b""]
        start_response = _sr

        # 0. Health probes — contract C1's ONLY auth exemption. Answered
        # BEFORE admission control so orchestrator probes never 503 under
        # load, and before auth so they stay open by design.
        if path in _AUTH_EXEMPT:
            return self._health(start_response, path)

        # 1. Operator auth (Track 1, C1): every /api/* request must carry
        # `Authorization: Bearer <operator-token>`. Rejects happen BEFORE
        # admission control — unauthenticated floods must not occupy
        # slots, and a 503 must never mask a 401.
        if path.startswith("/api/"):
            presented = self.operator_tokens.bearer_from_header(
                environ.get("HTTP_AUTHORIZATION"))
            if not self.operator_tokens.verify(presented):
                return self._unauthorized(start_response)

        # 2. Admission control — fail fast, never queue unboundedly.
        if not self.gate.try_acquire():
            return self._error(start_response, 503, "shed_admission",
                               "platform at capacity; retry shortly",
                               retryable=True,
                               headers=[("Retry-After", "2")])
        try:
            # 3. Load shedding — expensive endpoints shed first.
            shed, reason = self.degrade.should_shed(path)
            if shed:
                return self._error(start_response, 503, "shed_hot",
                                   reason, retryable=True,
                                   headers=[("Retry-After", "5")])
            if path == "/api/stream":
                return self._stream(environ, start_response)
            if path.startswith("/api/"):
                return self._api(environ, start_response, path, method)
            return self._static(environ, start_response, path, method)
        except Exception as e:
            # Eternal friction / graceful degradation: an unhandled handler
            # bug must degrade to a JSON 500 WITH CORS headers (the
            # start_response in scope is the CORS-injecting wrapper), never
            # a bare server 500 the console cannot read. The old shape —
            # uncaught exception, no CORS — made fetch() reject opaquely
            # and the console's sync silently blanked the Ops drawer.
            # Only the exception class is named; no internals leak.
            return self._error(start_response, 500, "internal",
                               f"unhandled {type(e).__name__}")
        finally:
            self.gate.release()

    # -------------------------------------------------------------- API

    def _api(self, environ, start_response, path, method):
        q = parse_qs(environ.get("QUERY_STRING", ""))
        try:
            if path == "/api/decisions" and method == "GET":
                return self._decisions(environ, start_response, q)
            m = re.fullmatch(r"/api/decision/(\d+)", path)
            if m and method == "GET":
                return self._decision(start_response, int(m.group(1)))
            if path == "/api/calibration" and method == "GET":
                return self._calibration(start_response, q)
            if path == "/api/simulate" and method == "POST":
                return self._simulate(environ, start_response)
            if path == "/api/analytics/noise" and method == "GET":
                return self._noise(start_response, q)
            if path == "/api/analytics/flips" and method == "GET":
                return self._flips(start_response, q)
            # BYOK integrations settings — the platform's one write surface
            # (keys only). Values are write-only; reads report last4.
            if path == "/api/v1/integrations/status" and method == "GET":
                return self._int_status(start_response)
            if path == "/api/v1/integrations/keys" and method == "POST":
                return self._int_keys_save(environ, start_response)
            if path == "/api/v1/integrations/simulated" and method == "POST":
                return self._int_simulated(environ, start_response)
            if path == "/api/v1/integrations/test-page" and method == "POST":
                return self._int_test_page(environ, start_response)
            m = re.fullmatch(r"/api/v1/integrations/keys/([a-z_]+)", path)
            if m and method == "DELETE":
                return self._int_keys_delete(start_response, m.group(1))
            # Rotation ceremony (Track 4, C4). C1 auth already enforced
            # in __call__ — every /api/* request carries the operator token.
            m = re.fullmatch(r"/api/v1/keys/([a-z_]+)/rotate", path)
            if m and method == "POST":
                return self._keys_rotate(environ, start_response, m.group(1))
            # C3 safety endpoints (Track 3): kill switch + drill status.
            # C1 auth enforced in __call__ (every /api/* except the two
            # health probes); safety_api additionally verifies via the
            # installed operator verifier (defense in depth).
            if path == "/api/v1/safety/kill" and method == "POST":
                return safety_api.handle_kill(self, environ, start_response)
            if path == "/api/v1/safety/rearm" and method == "POST":
                return safety_api.handle_rearm(self, environ, start_response)
            if path == "/api/v1/safety/status" and method == "GET":
                return safety_api.handle_status(self, environ, start_response)
            # Track 8: Operations Health aggregate for the console's
            # Operations Health surface. C1 auth enforced in __call__.
            if path == "/api/v1/ops/health" and method == "GET":
                return ops_health.handle_health(self, environ, start_response)
        except _BadParam as e:
            return self._error(start_response, 400, e.code, str(e))
        return self._error(start_response, 404, "not_found",
                           f"unknown API path {path}")

    # ---------------------------------------------------------- endpoints

    def _health(self, start_response, path):
        """Liveness/readiness — the ONLY unauthenticated surface (C1)."""
        if path == "/api/v1/health/live":
            return self._ok(start_response, {"status": "ok"})
        # /api/v1/health/ready: the serving path is ready when the read
        # store answers a cheap query. (store=None only exists in unit
        # tests that stub the store — there, boot itself is the check.)
        try:
            if self.store is not None:
                self.store.head_id()
        except Exception as e:  # name the failure class, not internals
            return self._error(start_response, 503, "not_ready",
                               f"read store not ready: {type(e).__name__}")
        return self._ok(start_response, {"status": "ok"})

    def _unauthorized(self, start_response):
        """401 with the EXACT contract body.

        Contract C1 / Track 8: the console's "operator sign-in required"
        state keys off this body verbatim — it is intentionally NOT the
        standard envelope. CORS headers still apply (the cross-origin
        console must be able to READ the 401 to show the sign-in state).
        """
        body = b'{"error":"unauthorized"}'
        start_response("401 Unauthorized", [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ])
        return [body]

    def _decisions(self, environ, start_response, q):
        limit = _int_param(q, "limit", 50, lo=1, hi=500)
        since_id = _int_param(q, "since_id", 0, lo=0)
        fp = _opt(q, "fingerprint")
        if fp is not None and not FP_RE.match(fp):
            raise _BadParam("bad_fingerprint",
                            "fingerprint must be 16 hex chars")
        team = _opt(q, "team")
        if team is not None and team not in TEAMS:
            raise _BadParam("bad_team", f"unknown team {team!r}")
        action = _opt(q, "action")
        if action is not None and action not in ACTIONS:
            raise _BadParam("bad_action", f"unknown action {action!r}")
        frm, to = _opt(q, "from"), _opt(q, "to")
        for name, val in (("from", frm), ("to", to)):
            if val is not None:
                try:
                    from .store import _norm_ts
                    _norm_ts(val)
                except ValueError:
                    raise _BadParam(f"bad_{name}",
                                    f"{name} must be ISO-8601")
        items = self.store.decisions(limit=limit, since_id=since_id,
                                     fingerprint=fp, team=team, action=action,
                                     since=frm, until=to)
        if items:
            oldest = min(i["id"] for i in items)
            has_more = self.store.has_older(oldest)
            pagination = {"limit": limit, "next_since_id": oldest,
                          "has_more": has_more}
        else:
            pagination = {"limit": limit, "next_since_id": None,
                          "has_more": False}
        return self._ok(start_response, items,
                        extra_meta={"pagination": pagination})

    def _decision(self, start_response, decision_id):
        detail = self.store.decision(decision_id)
        if detail is None:
            return self._error(start_response, 404, "not_found",
                               f"no decision {decision_id}")
        return self._ok(start_response, detail)

    def _calibration(self, start_response, q):
        team = _opt(q, "team")
        if team is not None and team not in TEAMS:
            raise _BadParam("bad_team", f"unknown team {team!r}")
        report = self.store.calibration(team=team,
                                        dataset_version=self.labels_version)
        return self._ok(start_response, report, data_source="synthetic")

    def _simulate(self, environ, start_response):
        # Bounded, EOF-terminating body read (safety_api.read_body_bytes):
        # never blocks on a short/blocking wsgi.input from a serverless
        # WSGI bridge — a bad body becomes a 422, never a hung request.
        raw = safety_api.read_body_bytes(environ, MAX_BODY)
        if raw is None:
            return self._error(start_response, 422, "body_too_large",
                               f"body exceeds {MAX_BODY} bytes")
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            return self._error(start_response, 422, "bad_json",
                               "request body must be JSON")
        if not isinstance(body, dict):
            return self._error(start_response, 422, "bad_body",
                               "request body must be a JSON object")
        try:
            thresholds = sim.validate_thresholds(body.get("thresholds"))
            c_fp, c_fn = sim.validate_cost_model(body.get("cost_model"))
        except ValueError as e:
            return self._error(start_response, 422, "bad_thresholds", str(e))
        version = body.get("dataset_version")
        if not isinstance(version, str) or not version:
            return self._error(start_response, 422, "bad_dataset",
                               "dataset_version is required")
        try:
            rows, sha, meta = self.registry.get(version)
        except UnknownDataset:
            return self._error(start_response, 422, "unknown_dataset",
                               f"unknown dataset_version {version!r}; "
                               f"known: {self.registry.versions()}")
        projection = sim.project(rows, thresholds, c_fp, c_fn,
                                 window_days=meta.get("window_days", 30))
        data = {
            "projection": projection,
            "provenance": {
                "dataset_version": version,
                "dataset_sha256": sha,
                "n_alerts": meta["n_alerts"],
                "tuner_rev": _tuner_rev(),
                "policy_version": sim.policy_version(),
                "computed_at": _now(),
            },
        }
        return self._ok(start_response, data, data_source="synthetic")

    def _noise(self, start_response, q):
        try:
            window_s = _window_seconds(_opt(q, "window") or "24h")
        except ValueError as e:
            raise _BadParam("bad_window", str(e))
        report = self.store.noise(window_s)
        report["window"] = _opt(q, "window") or "24h"
        return self._ok(start_response, report)

    def _flips(self, start_response, q):
        try:
            window_s = _window_seconds(_opt(q, "window") or "7d")
        except ValueError as e:
            raise _BadParam("bad_window", str(e))
        records = self.store.flip_records(window_s)
        flipped = sum(1 for r in records if r["flipped"])
        data = {
            "window": _opt(q, "window") or "7d",
            "repeats_total": len(records),
            "flip_rate": round(flipped / len(records), 4) if records else 0.0,
            "flips": records,
        }
        return self._ok(start_response, data)

    # --------------------------------------------- BYOK integrations (keys)
    # The platform tier's ONE write surface. Values are write-only: the API
    # reports configured/last4, never values. Twin of the engine's
    # sentinel/integrations.py — same file, same format.

    def _int_body(self, environ, start_response):
        """Parse a small JSON object body. Returns dict or an error response."""
        # Bounded, EOF-terminating body read — see _simulate.
        raw = safety_api.read_body_bytes(environ, MAX_BODY)
        if raw is None:
            return None, self._error(start_response, 422, "body_too_large",
                                     f"body exceeds {MAX_BODY} bytes")
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            return None, self._error(start_response, 422, "bad_json",
                                     "request body must be JSON")
        if not isinstance(body, dict):
            return None, self._error(start_response, 422, "bad_body",
                                     "request body must be a JSON object")
        return body, None

    def _int_status(self, start_response):
        return self._ok(start_response, {"integrations": self.integrations.status()})

    def _int_keys_save(self, environ, start_response):
        body, err = self._int_body(environ, start_response)
        if err:
            return err
        # Atomic: validate everything BEFORE persisting anything — a bad
        # second key must not leave the first one saved (Vault, PR #77).
        items = {name: body[name]
                 for name in (PD_KEY_NAME, JEV_KEY_NAME) if name in body}
        if not items:
            return self._error(start_response, 422, "bad_body",
                               "nothing to save — provide pagerduty_routing_key "
                               "and/or jev_api_key")
        for name, value in items.items():
            if not isinstance(value, str) or not value.strip():
                return self._error(start_response, 422, "bad_key",
                                   f"{name} must be a non-empty string")
        try:
            saved = self.integrations.set_many(items)
        except (KeyError, ValueError) as e:  # BadKey subclasses ValueError
            return self._error(start_response, 422, "bad_key", str(e))
        except EphemeralStoreError as e:
            return self._error(start_response, 501,
                               "persistence_unavailable",
                               str(e) + " In this hosted demo, keys are "
                               "session-scoped: pass routing_key with each "
                               "test-page request instead.")
        return self._ok(start_response, {"saved": saved,
                                         "integrations": self.integrations.status()})

    def _int_keys_delete(self, start_response, name):
        if name not in (PD_KEY_NAME, JEV_KEY_NAME):
            return self._error(start_response, 404, "not_found",
                               f"unknown integration key {name}")
        try:
            self.integrations.delete(name)
        except EphemeralStoreError as e:
            return self._error(start_response, 501, "persistence_unavailable", str(e))
        return self._ok(start_response, {"deleted": name,
                                         "integrations": self.integrations.status()})

    def _keys_rotate(self, environ, start_response, name):
        """POST /api/v1/keys/{name}/rotate — rotation ceremony (C4).

        Body: {"stage": "stage_secondary"|"verify_secondary"|"promote"|
        "retire"|"status", "value": "<new secret>"} — value only for
        stage_secondary. Every stage is audit-logged; no secret values
        ever appear in responses.
        """
        if self.rotation is None:
            return self._error(
                start_response, 422, "rotation_unavailable",
                "operator token is env-provisioned or ephemeral — "
                "rotation requires a file-backed store")
        body, err = self._int_body(environ, start_response)
        if err:
            return err
        stage = body.get("stage")
        if not isinstance(stage, str):
            return self._error(start_response, 422, "bad_body",
                               "stage must be one of: stage_secondary, "
                               "verify_secondary, promote, retire, status")
        code, resp = self.rotation.handle(name, stage, body,
                                          actor="operator")
        if code == 200:
            return self._ok(start_response, resp)
        detail = (resp.get("error") if isinstance(resp, dict)
                  else None) or "rotation failed"
        return self._error(start_response, code, "rotation_failed",
                           str(detail))

    def _int_simulated(self, environ, start_response):
        body, err = self._int_body(environ, start_response)
        if err:
            return err
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            return self._error(start_response, 422, "bad_body",
                               "enabled must be true or false")
        try:
            self.integrations.set_flag("simulated_paging", enabled)
        except EphemeralStoreError:
            # Serverless: simulated is forced on by env; the toggle can't
            # persist. Say so honestly instead of pretending.
            return self._error(start_response, 501, "persistence_unavailable",
                               "simulated mode is forced ON in this hosted demo "
                               "(SENTINEL_SIMULATED_PAGING=1) and cannot be "
                               "toggled here")
        state = "on — pages are logged, not sent" if enabled else "off — pages go to PagerDuty"
        return self._ok(start_response,
                        {"simulated_paging": enabled,
                         "message": f"Simulated paging {state}."})

    def _int_test_page(self, environ, start_response):
        """Send a clearly-labeled TEST event to PagerDuty.

        Key precedence: per-request `routing_key` (serverless session scope)
        → stored user key → PD_ROUTING_KEY env. In simulated mode the page
        is absorbed and labeled — never sent, honestly reported.
        """
        body, err = self._int_body(environ, start_response)
        if err:
            return err
        if self.integrations.simulated_paging():
            return self._ok(start_response, {
                "ok": True, "simulated": True,
                "dedup_key": None,
                "message": "Simulated paging is ON — no page was sent to "
                           "PagerDuty. This is what a test page would look "
                           "like: a trigger labeled [SENTINEL TEST].",
            })
        key = body.get("routing_key")
        if key is not None and (not isinstance(key, str) or not key.strip()):
            return self._error(start_response, 422, "bad_key",
                               "routing_key must be a non-empty string")
        if key:
            key = key.strip()
            # Validate the shape without echoing it back on failure.
            if not re.fullmatch(r"[0-9a-fA-F]{32}", key):
                return self._error(start_response, 422, "bad_key",
                                   "routing_key doesn't look like a PagerDuty "
                                   "Events API v2 routing key (32 hex chars)")
            source = "request"
        else:
            key = self.integrations.get(PD_KEY_NAME) or os.environ.get("PD_ROUTING_KEY")
            source = "stored" if self.integrations.get(PD_KEY_NAME) else "env"
        if not key:
            return self._error(start_response, 422, "no_routing_key",
                               "no PagerDuty routing key available — save one "
                               "in Integrations, pass routing_key with this "
                               "request, or set PD_ROUTING_KEY")
        dedup = "sentinel-test/%s/%d" % (os.urandom(8).hex(),
                                         int(time.time()))
        event = {
            "routing_key": key,
            "event_action": "trigger",
            "dedup_key": dedup,
            "payload": {
                "summary": "[SENTINEL TEST] Integrations test page — safe to acknowledge",
                "severity": "info",
                "source": "sentinel/integrations",
                "custom_details": {
                    "sentinel_test": True,
                    "key_source": source,
                    "note": "Sent from Sentinel's Integrations settings. "
                            "No incident — safe to acknowledge or resolve.",
                },
            },
        }
        ok, detail = _pd_enqueue(event, timeout_s=10.0)
        if ok:
            return self._ok(start_response, {
                "ok": True, "simulated": False, "dedup_key": dedup,
                "key_source": source,
                "message": "Test page accepted by PagerDuty. Look for "
                           "'[SENTINEL TEST]' in your PagerDuty service.",
            })
        # Honest failure: what happened and what to do — never the key.
        return self._ok(start_response, {
            "ok": False, "simulated": False, "dedup_key": dedup,
            "message": f"PagerDuty did not accept the test page ({detail}). "
                       "Check the routing key (Integrations tab → Events API "
                       "v2 in your PagerDuty service) and network egress.",
        })

    # --------------------------------------------------------------- SSE

    def _stream(self, environ, start_response):
        q = parse_qs(environ.get("QUERY_STRING", ""))
        since_id = 0
        last_event_id = environ.get("HTTP_LAST_EVENT_ID")
        if last_event_id:
            try:
                since_id = max(0, int(last_event_id))
            except ValueError:
                pass
        elif _opt(q, "since_id") is not None:
            try:
                since_id = max(0, int(_opt(q, "since_id")))
            except ValueError:
                pass
        head = self.store.head_id()
        resume_from = since_id
        gap = None
        if since_id < head - MAX_REPLAY:
            # Cursor predates retention: one gap event, then live.
            gap = {"resume_since_id": head - MAX_REPLAY,
                   "missed": (head - MAX_REPLAY) - since_id}
            resume_from = head - MAX_REPLAY

        def gen():
            # WSGI: the iterable must yield bytes, not str.
            yield b"retry: 3000\n\n"
            if gap is not None:
                yield (f"event: gap\ndata: {json.dumps(gap)}\n\n").encode()
            cursor = resume_from
            for item in self.store.tail(cursor, limit=MAX_REPLAY):
                yield (f"id: {item['id']}\nevent: decision\n"
                       f"data: {json.dumps(item)}\n\n").encode()
                cursor = item["id"]
            heartbeat_at = time.monotonic() + 25
            while True:
                time.sleep(1.0)
                for item in self.store.tail(cursor, limit=500):
                    yield (f"id: {item['id']}\nevent: decision\n"
                           f"data: {json.dumps(item)}\n\n").encode()
                    cursor = item["id"]
                if time.monotonic() >= heartbeat_at:
                    yield b": heartbeat\n\n"
                    heartbeat_at = time.monotonic() + 25

        start_response("200 OK", [
            ("Content-Type", "text/event-stream"),
            ("Cache-Control", "no-cache"),
            ("X-Accel-Buffering", "no"),
        ])
        return gen()

    # ------------------------------------------------------------ static

    def _static(self, environ, start_response, path, method):
        if method != "GET" or not self.ui_dir:
            return self._error(start_response, 404, "not_found",
                               f"unknown path {path}")
        rel = path.lstrip("/").split("/")
        if ".." in rel:
            return self._error(start_response, 404, "not_found", path)
        target = os.path.join(self.ui_dir, *rel) if rel != [""] else None
        if target is None or os.path.isdir(target):
            target = os.path.join(self.ui_dir, "index.html")
        if not os.path.isfile(target):
            # SPA fallback: client-side routes resolve to index.html.
            target = os.path.join(self.ui_dir, "index.html")
            if not os.path.isfile(target):
                return self._error(start_response, 404, "not_found", path)
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        with open(target, "rb") as fh:
            body = fh.read()
        start_response("200 OK", [
            ("Content-Type", ctype),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),  # demo: never serve a stale UI
        ])
        return [body]

    # ---------------------------------------------------------- envelopes

    def _meta(self, data_source=None, **extra):
        meta = {
            "contract_version": CONTRACT_VERSION,
            "data_source": data_source or self.data_source,
            "generated_at": _now(),
        }
        meta.update(extra)
        return meta

    def _ok(self, start_response, data, data_source=None, extra_meta=None):
        body = json.dumps({"data": data,
                           "meta": self._meta(data_source,
                                              **(extra_meta or {}))},
                          ).encode()
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ])
        return [body]

    def _error(self, start_response, status, code, message,
               retryable=False, headers=None):
        body = json.dumps({
            "data": None,
            "meta": self._meta(),
            "error": {"code": code, "message": message,
                      "retryable": retryable},
        }).encode()
        hdrs = [("Content-Type", "application/json"),
                ("Content-Length", str(len(body)))]
        hdrs.extend(headers or [])
        start_response(f"{status} {_STATUS_TEXT[status]}", hdrs)
        return [body]


_STATUS_TEXT = {200: "OK", 400: "Bad Request", 401: "Unauthorized",
                404: "Not Found", 422: "Unprocessable Entity",
                500: "Internal Server Error", 501: "Not Implemented",
                503: "Service Unavailable"}


class _BadParam(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _pd_enqueue(event: dict, timeout_s: float = 10.0) -> tuple[bool, str]:
    """POST one event to PagerDuty Events API v2. Returns (ok, detail).

    Never logs or returns the routing key — detail carries only the
    status/error class.

    Sim safety (audit P0, ruling X-B): the platform tier never imports the
    engine package (tier decoupling), so this is a self-contained mirror
    of sentinel/sim_pd_guard. Under SENTINEL_SIM=1 real PagerDuty is
    structurally disabled — the explicit operator test-page must not
    become a sim-mode paging path. Refuse, loudly.
    """
    if os.environ.get("SENTINEL_SIM") == "1":
        return False, ("refused: SENTINEL_SIM=1 — sim environments never "
                       "touch real PagerDuty (audit P0, ruling X-B)")
    import urllib.request
    import urllib.error
    body = json.dumps(event).encode("utf-8")
    req = urllib.request.Request(
        "https://events.pagerduty.com/v2/enqueue", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # timeout, DNS, TLS — name the class, not the body
        return False, f"{type(e).__name__}"
    return (200 <= status < 300), f"HTTP {status}"


def _opt(q, name):
    vals = q.get(name)
    return vals[0] if vals else None


def _int_param(q, name, default, lo=None, hi=None):
    raw = _opt(q, name)
    if raw is None:
        return default
    try:
        v = int(raw)
    except ValueError:
        raise _BadParam(f"bad_{name}", f"{name} must be an integer")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        bounds = f"{lo}..{hi}" if hi is not None else f">={lo}"
        raise _BadParam(f"bad_{name}", f"{name} must be in {bounds}")
    return v


def _tuner_rev() -> str:
    """Git rev of the tuner code (provenance). 'unknown' off-git."""
    import subprocess
    here = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=here, capture_output=True, text=True,
                             timeout=5)
        rev = out.stdout.strip()
        return rev if rev and out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"
