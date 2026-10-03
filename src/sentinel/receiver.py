"""Receiver — HTTP ingress: PD Events API v2 + generic webhook (§3.9).

Stdlib ThreadingHTTPServer. Pipeline per request:
  parse -> normalize to Alert -> correlator -> gate -> forwarder -> respond.

  * Unparseable payload -> forward original bytes to PagerDuty + metric, never drop.
  * The receiver never returns 5xx for a triage failure: every step is wrapped
    and the last resort is forwarding the original bytes.
  * Non-"trigger" PD event_actions (ack/resolve) are relayed unchanged,
    not triaged.

Run:  PYTHONPATH=src python -m sentinel.receiver --port 8080
Env:  TYPESAFE_API_KEY (required, unless SENTINEL_MOCK=1), SENTINEL_WEBHOOK_SECRET (optional),
      PD_EVENTS_URL, PD_ROUTING_KEY (optional, for generic alerts),
      SENTINEL_DB (default ./sentinel.db), SENTINEL_SHADOW (0/1).
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import hmac
import json
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .audit import AuditLog
from .client import SystemOneClient, MockSystemOneClient, client_from_env
from .config import ConfigLoader, ConfigRejected, record_restart
from .correlator import Correlator, fingerprint_for, fingerprint_of
from .forwarder import Forwarder
from .gate import Gate
from .health import HealthMonitor
from .models import Alert, Thresholds
from .state import build_state

MAX_BODY_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_INFLIGHT = 64  # alert-ingress admission bound (503, never 429)


class Unparseable(Exception):
    """The payload could not be normalized to an Alert (fail open)."""


# ------------------------------------------------------------------ config

@dataclass
class ReceiverConfig:
    webhook_secret: str | None = None
    shadow: bool = False


class Pipeline:
    """Owns the correlator -> gate -> forwarder chain plus metrics."""

    def __init__(self, correlator: Correlator, gate: Gate,
                 forwarder: Forwarder, audit: AuditLog,
                 config: ReceiverConfig | None = None,
                 policy=None, config_loader: ConfigLoader | None = None,
                 state_dir: str | None = None):
        self.correlator = correlator
        self.gate = gate
        self.forwarder = forwarder
        self.audit = audit
        self.config = config or ReceiverConfig()
        # Live validated policy (design 05, §4). None only for ad-hoc
        # constructions that never serve traffic (unit tests); the real
        # entrypoint always supplies one — see build_pipeline_from_env.
        self.policy = policy
        self.config_loader = config_loader
        self.state_dir = (state_dir or os.environ.get("SENTINEL_STATE_DIR")
                          or "./sentinel-state")
        self.metrics: dict[str, int] = {
            "received": 0,
            "triaged": 0,
            "deduped": 0,
            "storms": 0,
            "change_window": 0,
            "unparseable": 0,
            "handler_panics": 0,
        }
        # (timestamp, failed) per forward attempt; feeds the
        # forwarder_draining health predicate (design §1.2.3).
        self.forward_outcomes: collections.deque = collections.deque(maxlen=1000)
        self._health: HealthMonitor | None = None
        self._health_lock = threading.Lock()

    # ------------------------------------------------------------------ health

    @property
    def health(self) -> HealthMonitor:
        """Lazily-created HealthMonitor (no threads until start())."""
        with self._health_lock:
            if self._health is None:
                self._health = HealthMonitor(self, state_dir=self.state_dir)
            return self._health

    def note_forward(self, result) -> None:
        """Record one forward attempt's outcome for the health predicates."""
        failed = result is not None and result.error is not None
        self.forward_outcomes.append((time.time(), failed))

    def apply_policy(self, policy) -> None:
        """Atomically swap the live policy (post-validation only)."""
        self.gate.thresholds = policy.thresholds
        self.gate.allowlist = set(policy.allowlist)
        self.policy = policy

    # ------------------------------------------------------------ entry points

    def handle_pd(self, body: bytes) -> dict:
        """POST /v2/enqueue. Returns the PD-mirror response dict."""
        self.metrics["received"] += 1
        try:
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict):
                raise Unparseable("top-level JSON is not an object")
            action = data.get("event_action")
            if action is not None and action != "trigger":
                # acks/resolves are not pages: relay unchanged, no triage.
                self.forwarder.forward_raw(
                    body, alert_id=str(data.get("dedup_key") or "non-trigger"),
                    dedup_key=data.get("dedup_key"))
                return _pd_ok(data.get("dedup_key") or _body_key(body))
            alert = _normalize_pd(data)
        except Unparseable:
            self.metrics["unparseable"] += 1
            self.note_forward(self.forwarder.forward_raw(
                body, alert_id="unparseable", dedup_key=_body_key(body)))
            return _pd_ok(_body_key(body))
        disp_action = self._triage(alert, body)
        return _pd_ok(alert.alert_id)

    def handle_generic(self, body: bytes) -> dict:
        """POST /webhook/generic. Returns a small JSON response dict."""
        self.metrics["received"] += 1
        try:
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict):
                raise Unparseable("top-level JSON is not an object")
            alert = _normalize_generic(data)
        except Unparseable:
            self.metrics["unparseable"] += 1
            self.note_forward(self.forwarder.forward_raw(
                body, alert_id="unparseable", dedup_key=_body_key(body)))
            return {"status": "success", "dedup_key": _body_key(body),
                    "disposition": "passthrough"}
        disp_action = self._triage(alert, body)
        return {"status": "success", "dedup_key": alert.alert_id,
                "disposition": disp_action}

    # --------------------------------------------------------------- pipeline

    def _triage(self, alert: Alert, raw_bytes: bytes) -> str:
        """Full pipeline for one normalized alert. Returns disposition action."""
        state = build_state(alert, history={}, context={})
        corr = self.correlator.ingest(alert)

        if corr.kind == "storm" and corr.storm_declared:
            # Single aggregate request: page once for the whole storm.
            self.metrics["storms"] += 1
            agg = _aggregate_alert(corr, alert)
            agg_state = build_state(agg, history={}, context={})
            disp, _rec = self.gate.evaluate(agg, agg_state, {}, {})
            if disp.action != "suppress":
                self.note_forward(self.forwarder.forward(agg, disp))
            self.correlator.note_disposition(agg.fingerprint, disp)
            return disp.action

        # new / duplicate / change_window / storm-continuation all flow through
        # the gate; deterministic kinds skip the Jev call inside the gate.
        if corr.kind == "duplicate":
            self.metrics["deduped"] += 1
        elif corr.kind == "change_window":
            self.metrics["change_window"] += 1
        disp, _rec = self.gate.evaluate(alert, state, {}, {},
                                        correlation=corr)
        self.metrics["triaged"] += 1
        self.correlator.note_disposition(alert.fingerprint, disp)
        if disp.action != "suppress":
            self.note_forward(
                self.forwarder.forward(alert, disp, raw_bytes=raw_bytes))
        return disp.action


# ------------------------------------------------------------------ normalize

def _normalize_pd(data: dict) -> Alert:
    """PD Events API v2 -> Alert. Raises Unparseable on missing fields."""
    payload = data.get("payload")
    if not isinstance(payload, dict):
        raise Unparseable("missing payload object")
    routing_key = data.get("routing_key")
    summary = payload.get("summary")
    source = payload.get("source")
    severity = payload.get("severity")
    if not routing_key or not summary or not source or not severity:
        raise Unparseable("missing routing_key/payload.summary/source/severity")
    service = (payload.get("component") or payload.get("group")
               or source or "unknown")
    check = payload.get("class") or "pagerduty.trigger"
    labels = _str_labels(payload.get("custom_details"))
    labels.setdefault("region", str(payload.get("region", "")))
    now = _utcnow_iso()
    alert = Alert(
        alert_id=str(data.get("dedup_key") or ""),
        received_at=now,
        fingerprint="",
        service=str(service),
        check=str(check),
        severity_in=str(severity),
        title=str(summary),
        source="pagerduty",
        labels=labels,
        metric_value=None,
        metric_threshold=None,
        breach_duration_s=None,
        raw=data,
    )
    alert.fingerprint = fingerprint_of(alert)
    if not alert.alert_id:
        alert.alert_id = alert.fingerprint
    return alert


def _normalize_generic(data: dict) -> Alert:
    """Generic webhook JSON -> Alert. Raises Unparseable on missing fields."""
    service = data.get("service")
    if not service:
        raise Unparseable("missing service")
    check = data.get("check") or "generic"
    metric = data.get("metric") if isinstance(data.get("metric"), dict) else {}
    labels = _str_labels(data.get("labels"))
    now = _utcnow_iso()
    alert = Alert(
        alert_id=str(data.get("alert_id") or ""),
        received_at=now,
        fingerprint="",
        service=str(service),
        check=str(check),
        severity_in=str(data.get("severity") or "info"),
        title=str(data.get("title") or f"{service}:{check}"),
        source="generic",
        labels=labels,
        metric_value=_as_float(metric.get("value")),
        metric_threshold=_as_float(metric.get("threshold")),
        breach_duration_s=_as_int(metric.get("breach_duration_s")),
        raw=data,
    )
    alert.fingerprint = fingerprint_of(alert)
    if not alert.alert_id:
        alert.alert_id = alert.fingerprint
    return alert


def _aggregate_alert(corr, triggering: Alert) -> Alert:
    """One synthetic alert summarizing a declared storm (paged once)."""
    total = sum(corr.storm_counts.values())
    top = sorted(corr.storm_counts.items(), key=lambda kv: -kv[1])[:5]
    region = (triggering.labels or {}).get("region", "")
    now = _utcnow_iso()
    title = (f"Alert storm: {total} distinct alerts in 60s "
             f"({', '.join(f'{s}:{c}' for s, c in top)})")
    alert = Alert(
        alert_id=f"storm-{int(time.time())}",
        received_at=now,
        fingerprint="",
        service="storm-aggregate",
        check="storm",
        severity_in="critical",
        title=title,
        source=triggering.source,
        labels={"region": region},
        metric_value=float(total),
        metric_threshold=None,
        breach_duration_s=None,
        raw={"storm_counts": corr.storm_counts,
             "triggering_alert_id": triggering.alert_id},
    )
    alert.fingerprint = fingerprint_for("storm-aggregate", "storm",
                                        "critical", region)
    return alert


# ------------------------------------------------------------------ HTTP layer

class SentinelHandler(BaseHTTPRequestHandler):
    pipeline: Pipeline | None = None  # set by make_server()
    server_version = "Sentinel/0.1"

    def log_message(self, fmt, *args):  # minimal access log, no headers/bodies
        sys.stderr.write(f"[sentinel] {self.address_string()} "
                         f"{self.command} {self.path} " + fmt % args + "\n")

    # -- GET ---------------------------------------------------------
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/livez":
            # Shallow: the process is alive and answering. No dependencies
            # checked, never authenticated (supervisors need it unconditionally).
            self._send_json(200, self.pipeline.health.livez())
        elif path == "/healthz":
            # Deep: can the process do its job right now? (design 05, §1.2)
            if not self._health_auth_ok():
                self._send_json(401, {"status": "error",
                                      "message": "unauthorized"})
                return
            code, body = self.pipeline.health.check()
            self._send_json(code, body)
        else:
            self._send_json(404, {"status": "error", "message": "not found"})

    # -- POST --------------------------------------------------------
    def do_POST(self):
        path = urlsplit(self.path).path
        if path in ("/v2/enqueue", "/webhook/generic"):
            self._handle_alert_post(path)
        elif path == "/-/reload":
            self._handle_reload()
        else:
            self._send_json(404, {"status": "error",
                                  "message": "not found"})

    def _handle_alert_post(self, path: str) -> None:
        # Admission control — the 503-not-429 receiver contract (design §3.1).
        # Alertmanager DROPS alerts on 429 (unrecoverable verdict) but RETRIES
        # on 503. Overload must therefore be 503, never 429 — this is
        # release-blocking: get it wrong and pages are silently lost.
        sem = self.server.inflight_sem
        if not sem.acquire(blocking=False):
            self._send_json(
                503,
                {"status": "error",
                 "message": "receiver overloaded; retry"},
                headers={"Retry-After": "1"},
            )
            return
        try:
            length = self.headers.get("Content-Length")
            try:
                length = int(length) if length else 0
            except ValueError:
                length = 0
            if length > MAX_BODY_BYTES:
                self._send_json(413, {"status": "error",
                                      "message": "payload too large"})
                return
            body = self.rfile.read(length) if length > 0 else b""
            try:
                if path == "/v2/enqueue":
                    resp = self.pipeline.handle_pd(body)
                    self._send_json(200, resp)
                elif path == "/webhook/generic":
                    if not self._signature_ok(body):
                        self._send_json(403, {"status": "error",
                                              "message": "bad signature"})
                        return
                    resp = self.pipeline.handle_generic(body)
                    self._send_json(200, resp)
            except Exception as exc:
                # Absolute last resort: a panicking request must not take down
                # the process (design §8.1). The request dies as passthrough;
                # the receiver lives. Never 5xx a triage failure.
                self.pipeline.metrics["handler_panics"] += 1
                sys.stderr.write(f"[sentinel] pipeline exception: {exc}\n")
                try:
                    self.pipeline.note_forward(
                        self.pipeline.forwarder.forward_raw(
                            body, alert_id="receiver-error"))
                except Exception:
                    pass
                self._send_json(200, _pd_ok(_body_key(body)))
        finally:
            sem.release()

    def _handle_reload(self) -> None:
        # SIGHUP-equivalent over HTTP: re-validate config; invalid loads are
        # rejected with 422 and the live generation is untouched (design §4).
        if not self._health_auth_ok():
            self._send_json(401, {"status": "error",
                                  "message": "unauthorized"})
            return
        loader = self.pipeline.config_loader
        if loader is None:
            self._send_json(503, {"status": "error",
                                  "message": "no config loader attached"})
            return
        try:
            policy = loader.reload()
        except ConfigRejected as exc:
            self._send_json(422, {"status": "error",
                                  "message": f"config rejected: {exc}"})
            return
        self.pipeline.apply_policy(policy)
        self._send_json(200, {"status": "ok",
                              "config_generation": policy.generation})

    # -- helpers -----------------------------------------------------
    def _signature_ok(self, body: bytes) -> bool:
        secret = self.pipeline.config.webhook_secret
        if not secret:
            return True
        sig = self.headers.get("X-Sentinel-Signature")
        if not sig:
            return True  # fail open: no signature presented
        if not sig.startswith("sha256="):
            return False
        expected = hmac.new(secret.encode("utf-8"), body,
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig[len("sha256="):], expected)

    def _health_auth_ok(self) -> bool:
        """Bearer <redacted> for /healthz and /-/reload (design §2.3).

        /livez is deliberately unauthenticated: supervisors must reach it
        unconditionally. When SENTINEL_HEALTH_TOKEN is unset the deep probe
        is open (documented; set the token in production).
        """
        token = os.environ.get("SENTINEL_HEALTH_TOKEN")
        if not token:
            return True
        presented = self.headers.get("Authorization") or ""
        return hmac.compare_digest(presented, f"Bearer {token}")

    def _send_json(self, code: int, obj: dict,
                   headers: dict | None = None) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)


class SentinelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(port: int, pipeline: Pipeline,
                bind: str = "127.0.0.1",
                max_inflight: int | None = None) -> SentinelServer:
    """Build a bound server (port=0 picks an ephemeral port — handy for tests).

    max_inflight bounds concurrent alert-ingress handlers; beyond it the
    receiver answers 503 (never 429). From SENTINEL_MAX_INFLIGHT when unset.
    """
    SentinelHandler.pipeline = pipeline
    server = SentinelServer((bind, port), SentinelHandler)
    if max_inflight is None:
        try:
            max_inflight = int(os.environ.get("SENTINEL_MAX_INFLIGHT",
                                              str(DEFAULT_MAX_INFLIGHT)))
        except ValueError:
            max_inflight = DEFAULT_MAX_INFLIGHT
    max_inflight = max(1, max_inflight)
    server.inflight_sem = threading.Semaphore(max_inflight)
    server.max_inflight = max_inflight
    return server


# ------------------------------------------------------------------ helpers

def _pd_ok(dedup_key: str) -> dict:
    return {"status": "success", "message": "Event processed",
            "dedup_key": dedup_key}


def _body_key(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()[:16]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _str_labels(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out = {}
    for k, v in value.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[str(k)] = "" if v is None else str(v)
    return out


def _as_float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ entrypoint


def build_pipeline_from_env(policy=None,
                            config_loader: ConfigLoader | None = None,
                            state_dir: str | None = None) -> Pipeline:
    """Wire correlator/audit/gate/forwarder from environment (§8).

    `policy` must be a validated PolicyConfig from ConfigLoader.load_startup()
    (or a reload). Fail-closed: without one we refuse to build a pipeline —
    Sentinel never supervises paging with an unvalidated policy.
    """
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if os.environ.get("SENTINEL_MOCK", "0") == "1":
        # Local dev/demo: mock client with no scripted answers. Every decide()
        # raises JevError inside the mock, which the gate converts to
        # passthrough (fail-open) — the full pipeline runs, nothing is ever
        # suppressed, and no network call is made.
        client = MockSystemOneClient()
    elif not api_key:
        raise SystemExit(
            "TYPESAFE_API_KEY is not set. Export it before starting the receiver "
            "(or set SENTINEL_MOCK=1 for local dev with a mock client).")
    else:
        client = SystemOneClient(api_key=api_key)
    if policy is None:
        raise SystemExit(
            "no validated policy config: refusing to start without one "
            "(load thresholds.json via ConfigLoader.load_startup()).")
    webhook_secret = os.environ.get("SENTINEL_WEBHOOK_SECRET")
    if webhook_secret is not None and len(webhook_secret) < 16:
        raise SystemExit(
            "SENTINEL_WEBHOOK_SECRET is set but shorter than 16 characters: "
            "refusing to start with a weak webhook secret.")
    db_path = os.environ.get("SENTINEL_DB", "./sentinel.db")
    audit = AuditLog(db_path)
    shadow = os.environ.get("SENTINEL_SHADOW", "0") == "1"
    gate = Gate(client, policy.thresholds, set(policy.allowlist), audit,
                shadow=shadow)
    forwarder = Forwarder(
        pd_events_url=os.environ.get("PD_EVENTS_URL",
                                     "https://events.pagerduty.com/v2/enqueue"),
        default_routing_key=os.environ.get("PD_ROUTING_KEY"),
    )
    config = ReceiverConfig(
        webhook_secret=webhook_secret,
        shadow=shadow,
    )
    return Pipeline(Correlator(), gate, forwarder, audit, config,
                    policy=policy, config_loader=config_loader,
                    state_dir=state_dir)


def _install_sighup(loader: ConfigLoader, pipeline: Pipeline) -> None:
    """SIGHUP -> validated reload; rejected loads keep the live generation."""
    if not hasattr(signal, "SIGHUP"):
        return

    def _on_hup(signum, frame):
        try:
            policy = loader.reload()
        except ConfigRejected as exc:
            print(f"[sentinel] SIGHUP reload rejected, live config kept: "
                  f"{exc}", file=sys.stderr)
            return
        pipeline.apply_policy(policy)
        print(f"[sentinel] SIGHUP reload applied "
              f"(generation {policy.generation})", file=sys.stderr)

    signal.signal(signal.SIGHUP, _on_hup)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Sentinel receiver (v0.1)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--config-dir", default=os.environ.get(
        "SENTINEL_CONFIG_DIR", "."),
        help="directory holding thresholds.json / allowlist.json")
    parser.add_argument("--state-dir", default=os.environ.get(
        "SENTINEL_STATE_DIR", "./sentinel-state"),
        help="writable state dir (generations, restarts, events)")
    args = parser.parse_args(argv)

    # Fail-closed config gate (design 05, §4): invalid config with no
    # last-good generation -> refuse to start. Never supervise paging with
    # an unvalidated policy.
    loader = ConfigLoader(config_dir=args.config_dir, state_dir=args.state_dir)
    try:
        policy = loader.load_startup()
    except ConfigRejected as exc:
        print(f"[sentinel] refusing to start: {exc}", file=sys.stderr)
        raise SystemExit(2)

    pipeline = build_pipeline_from_env(policy=policy, config_loader=loader,
                                       state_dir=args.state_dir)
    print(f"[sentinel] serving policy generation {policy.generation}",
          file=sys.stderr)
    record_restart(args.state_dir)
    _install_sighup(loader, pipeline)
    pipeline.health.start()  # startup gate self-test, then every 5 min
    server = make_server(args.port, pipeline, bind=args.bind)
    print(f"[sentinel] listening on {args.bind}:{server.server_port} "
          f"(shadow={pipeline.config.shadow}, "
          f"max_inflight={server.max_inflight})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.health.stop()


if __name__ == "__main__":
    main()
