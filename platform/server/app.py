"""WSGI read API for the Sentinel platform tier (Forge's lane).

Implements platform/contracts/openapi.yaml v1.0.0 exactly:
  GET  /api/decisions[?limit&since_id&fingerprint&team&action&from&to]
  GET  /api/decision/<id>
  GET  /api/calibration[?team]
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
from .datasets import UnknownDataset
from .shed import EXPENSIVE_PATHS
from .store import MAX_REPLAY, ReadStore

CONTRACT_VERSION = "1.0.0"

TEAMS = ("platform", "network", "data", "product_backend", "security",
         "cannot_determine")
ACTIONS = ("page_now", "page_business_hours", "suppress", "passthrough")
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
                 ui_dir: str | None = None):
        self.store = store
        self.registry = registry
        self.gate = gate
        self.degrade = degrade
        self.data_source = data_source
        self.labels_version = labels_version
        self.ui_dir = os.path.abspath(ui_dir) if ui_dir else None
        if self.ui_dir and not os.path.isdir(self.ui_dir):
            self.ui_dir = None

    # ------------------------------------------------------------- WSGI

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "/") or "/"
        method = environ.get("REQUEST_METHOD", "GET").upper()

        # 1. Admission control — fail fast, never queue unboundedly.
        if not self.gate.try_acquire():
            return self._error(start_response, 503, "shed_admission",
                               "platform at capacity; retry shortly",
                               retryable=True,
                               headers=[("Retry-After", "2")])
        try:
            # 2. Load shedding — expensive endpoints shed first.
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
        except _BadParam as e:
            return self._error(start_response, 400, e.code, str(e))
        return self._error(start_response, 404, "not_found",
                           f"unknown API path {path}")

    # ---------------------------------------------------------- endpoints

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
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            return self._error(start_response, 422, "body_too_large",
                               f"body exceeds {MAX_BODY} bytes")
        raw = environ["wsgi.input"].read(length) if length else b""
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


_STATUS_TEXT = {200: "OK", 400: "Bad Request", 404: "Not Found",
                422: "Unprocessable Entity", 503: "Service Unavailable"}


class _BadParam(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


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
