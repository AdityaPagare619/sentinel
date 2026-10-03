"""Liveness probes: /livez (shallow) vs /healthz (deep) — design 05, §1.

K8s-conventional split, and the two must never be conflated:

  GET /livez   — shallow. The process is alive and answering. No dependencies
                 are checked. The supervisor restarts on failure.
  GET /healthz — deep. The process can do its job *right now*: five named
                 predicates (§1.2). 200 with evidence iff all hold, else 503
                 with {"ok": false, "failed": [<names>]}. The external watcher
                 pages the *reasons*, not just "down".

The frozen spec's constant-true /healthz is retired: a lie detector that
never fires is worse than none.

v0.1 honesty notes (each predicate says what it actually measures):
  * forwarder_draining — v0.1 has no outbox (design 03 lane); the predicate
    is a consecutive-forward-error tripwire plus an explicit statement of
    what is not yet measured (outbox lag, synthetic canary).
  * evidence_flowing — v0.1 has no WAL (design 02 lane); the proxy is an
    audit round-trip (write a decision row, read it back) inside the
    gate self-test.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

from .config import count_restarts_10m
from .correlator import Correlator, fingerprint_of
from .models import Alert
from .state import build_state

VERSION = "0.1.0"

SELFTEST_INTERVAL_S = 300.0      # gate self-test cadence (design §1.2.2)
SELFTEST_STALE_SLOP_S = 60.0     # grace beyond the interval before "stale"
FORWARD_ERROR_WINDOW_S = 60.0    # forward-error tripwire window
FORWARD_ERROR_TRIP = 3           # consecutive errors inside the window -> 503
RESTART_WINDOW_S = 600.0         # crash-loop signature window (design §1.2.5)


class _DryRunForwarder:
    """Forwarder stand-in for the gate self-test: records, never POSTs."""

    def __init__(self):
        self.calls: list[dict] = []

    def forward(self, alert, disposition, raw_bytes=None):
        self.calls.append({"alert_id": alert.alert_id,
                           "action": disposition.action,
                           "ts": time.time()})
        return True

    def forward_raw(self, raw_bytes, alert_id="unknown", dedup_key=None):
        self.calls.append({"alert_id": alert_id, "action": "passthrough_raw",
                           "ts": time.time()})
        return True


class HealthMonitor:
    """Owns the deep-check predicates and the self-test loop.

    Constructed lazily per Pipeline (no threads until start()); thread-safe.
    """

    PREDICATES = (
        "config_current",
        "gate_constructed",
        "forwarder_draining",
        "evidence_flowing",
        "no_crashloop_signature",
    )

    def __init__(self, pipeline, state_dir: str | None = None,
                 clock=time.time,
                 selftest_interval_s: float | None = None,
                 forward_error_window_s: float = FORWARD_ERROR_WINDOW_S,
                 forward_error_trip: int = FORWARD_ERROR_TRIP):
        self._pipeline = pipeline
        self._state_dir = (state_dir
                           or os.environ.get("SENTINEL_STATE_DIR")
                           or "./sentinel-state")
        self._clock = clock
        if selftest_interval_s is None:
            try:
                selftest_interval_s = float(os.environ.get(
                    "SENTINEL_SELFTEST_INTERVAL_S", str(SELFTEST_INTERVAL_S)))
            except ValueError:
                selftest_interval_s = SELFTEST_INTERVAL_S
        self._selftest_interval = max(1.0, selftest_interval_s)
        self._forward_error_window = forward_error_window_s
        self._forward_error_trip = forward_error_trip
        self._started_at = self._clock()
        self._lock = threading.Lock()
        self._last_selftest: dict | None = None
        self._stop_event = threading.Event()
        self._timer_thread: threading.Thread | None = None

    # ------------------------------------------------------------------ probes

    def livez(self) -> dict:
        """Shallow: the process is alive and answering. No deps checked."""
        return {
            "alive": True,
            "uptime_s": int(self._clock() - self._started_at),
            "version": VERSION,
            "pid": os.getpid(),
        }

    def check(self) -> tuple[int, dict]:
        """Deep check. Returns (http_status, body)."""
        results: dict[str, dict] = {}
        failed: list[str] = []
        for name in self.PREDICATES:
            ok, evidence = getattr(self, "_pred_" + name)()
            results[name] = {"ok": ok, **evidence}
            if not ok:
                failed.append(name)
        degraded = bool(results["gate_constructed"].get("degraded"))
        degraded_reason = (results["gate_constructed"].get("degraded_reason")
                           if degraded else None)
        if failed:
            return 503, {
                "ok": False,
                "failed": failed,
                "degraded": degraded,
                "degraded_reason": degraded_reason,
                "checks": results,
            }
        return 200, {
            "ok": True,
            "degraded": degraded,
            "degraded_reason": degraded_reason,
            "config_generation": results["config_current"].get(
                "config_generation"),
            "gate_selftest_age_s": results["gate_constructed"].get(
                "gate_selftest_age_s"),
            "forwarder_outbox_lag_s": results["forwarder_draining"].get(
                "forwarder_outbox_lag_s"),
            "recent_forward_errors_60s": results["forwarder_draining"].get(
                "recent_forward_errors_60s"),
            "last_canary_age_s": results["forwarder_draining"].get(
                "last_canary_age_s"),
            "wal_flusher_lag_s": None,
            "restarts_10m": results["no_crashloop_signature"].get(
                "restarts_10m"),
            "checks": results,
        }

    # --------------------------------------------------------------- predicates

    def _pred_config_current(self) -> tuple[bool, dict]:
        loader = self._pipeline.config_loader
        if loader is None or loader.current is None:
            return False, {"reason": "no validated config generation loaded"}
        changed = loader.config_files_changed()
        return (not changed), {
            "config_generation": loader.current.generation,
            "loaded_at": loader.current.loaded_at,
            "config_changed_since_load": changed,
        }

    def _pred_gate_constructed(self) -> tuple[bool, dict]:
        st = self._ensure_selftest()
        age = self._clock() - st["ts"]
        fresh = age <= self._selftest_interval + SELFTEST_STALE_SLOP_S
        ok = bool(st["ok"]) and fresh
        degraded = st["client_error"] is not None
        return ok, {
            "gate_selftest_age_s": round(age, 1),
            "selftest_ok": st["ok"],
            "selftest_error": st["error"],
            "dry_run_forwarded": st["dry_run_forwarded"],
            "client_error": st["client_error"],
            "degraded": degraded,
            "degraded_reason": (
                f"gate self-test client fail-open ({st['client_error']})"
                if degraded else None),
        }

    def _pred_forwarder_draining(self) -> tuple[bool, dict]:
        now = self._clock()
        recent = [t for (t, failed) in self._pipeline.forward_outcomes
                  if failed and now - t <= self._forward_error_window]
        ok = len(recent) < self._forward_error_trip
        return ok, {
            "recent_forward_errors_60s": len(recent),
            # v0.1 has no outbox (design 03 lane): direct forward with a
            # consecutive-error tripwire instead of outbox-lag measurement.
            "forwarder_outbox_lag_s": 0,
            "outbox": "not_present_in_v0.1",
            "last_canary_age_s": None,
            "canary": "not_configured",
        }

    def _pred_evidence_flowing(self) -> tuple[bool, dict]:
        st = self._ensure_selftest()
        ok = bool(st["audit_roundtrip_ok"])
        return ok, {
            "audit_roundtrip_ok": st["audit_roundtrip_ok"],
            # v0.1 has no WAL (design 02 lane); the audit write+read-back
            # inside the self-test is the evidence-flow proxy.
            "wal": "not_present_in_v0.1",
        }

    def _pred_no_crashloop_signature(self) -> tuple[bool, dict]:
        n = count_restarts_10m(self._state_dir, self._clock, RESTART_WINDOW_S)
        # One entry = this process's own (re)start. Two or more inside the
        # window means something keeps dying and being restarted.
        return n <= 1, {"restarts_10m": n}

    # ---------------------------------------------------------------- self-test

    def _ensure_selftest(self) -> dict:
        with self._lock:
            st = self._last_selftest
        if st is None:
            st = self.run_selftest()
        return st

    def run_selftest(self) -> dict:
        """One synthetic alert through correlator -> gate -> forwarder dry-run,
        plus an audit round-trip. Never raises; records the outcome."""
        t0 = self._clock()
        result: dict = {
            "ts": t0,
            "ok": False,
            "error": None,
            "client_error": None,
            "dry_run_forwarded": False,
            "audit_roundtrip_ok": False,
            "duration_s": None,
        }
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            alert = Alert(
                alert_id=f"selftest-{int(t0 * 1000)}",
                received_at=now_iso,
                fingerprint="",
                service="sentinel-selftest",
                check="selftest",
                severity_in="info",
                title="sentinel gate self-test",
                source="sentinel",
                labels={},
            )
            alert.fingerprint = fingerprint_of(alert)
            # A dedicated correlator: the self-test must not see (or pollute)
            # live dedup/storm state, and must exercise the "new" Jev path.
            corr = Correlator().ingest(alert)
            state = build_state(alert, {}, {})
            disp, _rec = self._pipeline.gate.evaluate(
                alert, state, {}, {}, correlation=corr)
            if disp.reason.startswith("error:"):
                # Fail-open is a disposition policy, not death: the gate is
                # constructed and working, but degraded. (Design §1.4.)
                result["client_error"] = disp.reason
            _DryRunForwarder().forward(alert, disp)
            result["dry_run_forwarded"] = True
            rows = self._pipeline.audit.decisions_for_fingerprint(
                alert.fingerprint, limit=1)
            result["audit_roundtrip_ok"] = (
                bool(rows) and rows[0]["alert_id"] == alert.alert_id)
            result["ok"] = True
        except Exception as exc:  # the self-test must never kill the process
            result["error"] = f"{type(exc).__name__}: {exc}"
        result["duration_s"] = round(self._clock() - t0, 3)
        with self._lock:
            self._last_selftest = result
        return result

    # ------------------------------------------------------------ background

    def start(self) -> None:
        """Run the startup self-test and then every interval, in background."""
        with self._lock:
            if self._timer_thread is not None:
                return
            self._stop_event.clear()
            self._timer_thread = threading.Thread(
                target=self._loop, daemon=True, name="sentinel-selftest")
            self._timer_thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._timer_thread
            self._timer_thread = None
        if thread is not None:
            self._stop_event.set()
            thread.join(timeout=5)

    def _loop(self) -> None:
        self.run_selftest()
        while not self._stop_event.wait(self._selftest_interval):
            self.run_selftest()
