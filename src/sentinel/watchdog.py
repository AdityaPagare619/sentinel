"""Suppression-rate SLO watchdog + dead-man's-switch heartbeat (ADR-022, D8).

The watchdog is a control-plane component: it tails decision events, computes
the suppression rate per policy per window, and trips when the rate deviates
beyond the SLO band. Trip action, in order (belt and suspenders):

  1. freeze the offending policy's suppression ability — durable FIRST, so a
     crash after this point still leaves suppression blocked;
  2. append the audited ``watchdog_trip`` event to the event log;
  3. page a human via the control-plane page path.

If the watchdog dies anywhere in that sequence, the heartbeat stops, and the
SEPARATE dead-man's-switch watcher (scripts/ops/watchdog_watcher.py — a
different process, a different fate domain) pages the human on the missing
heartbeat via a path that does not traverse the watchdog. A watchdog that can
die silently is theater.

Review-due tightening (B3.5b): a review-due policy trips on >2-sigma drift
(alarm, page owner) rather than the full SLO band — it does not get the
benefit of the doubt.

Absolute guardrail: a suppression rate above ``absolute_max_rate`` trips
regardless of baseline. A policy suppressing most of the estate's alerts is
insane by construction; no baseline blesses it.

The watchdog NEVER pages or suppresses by itself on the paging path — it is
not in the paging fate domain. It reads the event log (the only cross-domain
channel) and writes freeze state + audited events.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import math
import os
import time

from . import policy_lifecycle as _pl


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


@dataclasses.dataclass
class SuppressionBaseline:
    """The signed baseline the watchdog alarms against (B3.3: signed at
    canary -> live promotion)."""
    policy_id: str
    version: int
    window_s: float
    expected_rate: float      # suppressions / decisions in the window
    sigma: float              # std dev of the rate at signing time
    band_sigmas: float = 3.0  # trip when |rate - expected| > band*sigma
    absolute_max_rate: float = 0.50  # trip regardless of baseline above this
    review_due_sigmas: float = 2.0   # B3.5b tightening
    signed_by: tuple[str, ...] = ()
    signed_at: str = ""

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["signed_by"] = list(self.signed_by)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SuppressionBaseline":
        return cls(policy_id=str(d["policy_id"]), version=int(d["version"]),
                   window_s=float(d["window_s"]),
                   expected_rate=float(d["expected_rate"]),
                   sigma=float(d["sigma"]),
                   band_sigmas=float(d.get("band_sigmas", 3.0)),
                   absolute_max_rate=float(d.get("absolute_max_rate", 0.50)),
                   review_due_sigmas=float(d.get("review_due_sigmas", 2.0)),
                   signed_by=tuple(d.get("signed_by", ())),
                   signed_at=str(d.get("signed_at", "")))


@dataclasses.dataclass
class WatchdogTrip:
    policy_id: str
    version: int | None
    window_s: float
    observed_rate: float
    expected_rate: float
    sigma: float
    band_sigmas: float
    deviation_sigmas: float
    reason: str  # "slo_band" | "absolute_guardrail" | "review_due_drift"
    at: str
    decisions_in_window: int
    suppressions_in_window: int

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class SuppressionWatchdog:
    """Pure rate computation + trip logic. I/O (event log tail, paging,
    freeze persistence) is injected so the kernel is unit-testable."""

    MIN_DECISIONS = 30  # do not trip on tiny samples — noise, not signal

    def __init__(self, baselines: dict[str, SuppressionBaseline] | None = None):
        self.baselines = dict(baselines or {})
        # policy_id -> list of (ts_epoch, suppressed: bool); pruned on evaluate
        self._windows: dict[str, list[tuple[float, bool]]] = {}

    def set_baseline(self, baseline: SuppressionBaseline) -> None:
        if len(set(baseline.signed_by)) < 2:
            raise ValueError("watchdog baseline requires 2 distinct signers "
                             "(B3.3 canary -> live gate)")
        self.baselines[baseline.policy_id] = baseline

    def ingest(self, policy_id: str, suppressed: bool,
               ts: float | None = None) -> None:
        self._windows.setdefault(policy_id, []).append(
            (ts if ts is not None else time.time(), bool(suppressed)))

    def evaluate(self, policy_id: str, *,
                 now: float | None = None,
                 review_due: bool = False) -> WatchdogTrip | None:
        """Return a trip if the suppression rate is out of band, else None."""
        baseline = self.baselines.get(policy_id)
        if baseline is None:
            return None  # no signed baseline: nothing to alarm against
        now = now if now is not None else time.time()
        cutoff = now - baseline.window_s
        events = [(t, s) for t, s in self._windows.get(policy_id, [])
                  if t >= cutoff]
        self._windows[policy_id] = events  # prune
        n = len(events)
        if n < self.MIN_DECISIONS:
            return None
        suppressed = sum(1 for _, s in events if s)
        rate = suppressed / n

        # Absolute guardrail first: insane by construction, no baseline blesses.
        if rate > baseline.absolute_max_rate:
            return self._trip(baseline, rate, n, suppressed, now,
                              "absolute_guardrail", float("inf"))
        band = (baseline.review_due_sigmas if review_due
                else baseline.band_sigmas)
        denom = baseline.sigma if baseline.sigma > 0 else 1e-9
        dev = abs(rate - baseline.expected_rate) / denom
        if dev > band:
            reason = "review_due_drift" if review_due else "slo_band"
            return self._trip(baseline, rate, n, suppressed, now,
                              reason, dev)
        return None

    @staticmethod
    def _trip(baseline, rate, n, suppressed, now, reason, dev) -> WatchdogTrip:
        return WatchdogTrip(
            policy_id=baseline.policy_id, version=baseline.version,
            window_s=baseline.window_s, observed_rate=rate,
            expected_rate=baseline.expected_rate, sigma=baseline.sigma,
            band_sigmas=baseline.band_sigmas, deviation_sigmas=dev,
            reason=reason,
            at=_dt.datetime.fromtimestamp(now, _dt.timezone.utc).isoformat(),
            decisions_in_window=n, suppressions_in_window=suppressed)


class HeartbeatEmitter:
    """Emits the watchdog heartbeat on a fixed interval.

    The heartbeat is a small JSON file (seq, watchdog_id, at) plus an
    audited ``watchdog_heartbeat`` event. The separate watcher process
    (scripts/ops/watchdog_watcher.py) reads the FILE — not the event log —
    so a wedged event-log writer cannot mask a dead watchdog.
    """

    def __init__(self, path: str, watchdog_id: str = "watchdog-1",
                 interval_s: float = 60.0,
                 clock=None):
        self.path = path
        self.watchdog_id = watchdog_id
        self.interval_s = interval_s
        self._clock = clock or time.time
        self._seq = 0
        self._last_emit = 0.0

    def maybe_emit(self, force: bool = False) -> dict | None:
        now = self._clock()
        if not force and now - self._last_emit < self.interval_s:
            return None
        self._seq += 1
        self._last_emit = now
        beat = {"watchdog_id": self.watchdog_id, "seq": self._seq,
                "at": _dt.datetime.fromtimestamp(
                    now, _dt.timezone.utc).isoformat(),
                "at_epoch": now}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(beat, fh, sort_keys=True)
        os.replace(tmp, self.path)
        return beat

    @property
    def seq(self) -> int:
        return self._seq


class WatchdogRunner:
    """Wires the pure pieces to the real world: tails the event log,
    evaluates, freezes, pages, ticks the lifecycle, emits heartbeats.

    Callbacks (injected for testability):
      - read_decisions(since_seq) -> (events, max_seq); each event has
        disposition + policy_id + ts.
      - page_human(summary, detail) — control-plane page path.
      - append_event(event_type, body) — audited event-log write.
    Trip ordering (crash-safe): freeze state file -> audited event -> page.
    """

    def __init__(self, *, watchdog: SuppressionWatchdog,
                 policy_store: "_pl.PolicyStore",
                 heartbeat: HeartbeatEmitter,
                 read_decisions, page_human, append_event,
                 clock=None):
        self.watchdog = watchdog
        self.policy_store = policy_store
        self.heartbeat = heartbeat
        self._read_decisions = read_decisions
        self._page_human = page_human
        self._append_event = append_event
        self._clock = clock or time.time
        self._since_seq = 0

    def run_once(self) -> dict:
        """One control-loop iteration. Never raises: a watchdog that crashes
        its loop is worse than one that logs and continues — the heartbeat
        keeps flowing while the loop degrades loudly."""
        report: dict = {"trips": [], "lifecycle": [], "errors": []}
        now = self._clock()
        try:
            events, max_seq = self._read_decisions(self._since_seq)
        except Exception as exc:  # noqa: BLE001 — loop must survive
            report["errors"].append(f"read_decisions: {exc}")
            events, max_seq = [], self._since_seq
        self._since_seq = max(self._since_seq, max_seq)
        for ev in events:
            try:
                self.watchdog.ingest(ev["policy_id"],
                                     ev["disposition"] == "suppress",
                                     ts=ev["ts"])
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"ingest: {exc}")

        for pid in list(self.watchdog.baselines):
            try:
                eff = self.policy_store.effective_version(pid)
                review_due = (eff is not None
                              and eff.state == _pl.PolicyState.REVIEW_DUE.value)
                trip = self.watchdog.evaluate(pid, now=now,
                                              review_due=review_due)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"evaluate {pid}: {exc}")
                continue
            if trip is not None:
                report["trips"].append(self._handle_trip(trip))

        # Lifecycle clock: review-due pages owner, expiry fails loud.
        try:
            for emitted in self.policy_store.tick(
                    _dt.datetime.fromtimestamp(now, _dt.timezone.utc)):
                self._handle_lifecycle_event(emitted)
                report["lifecycle"].append(emitted)
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"lifecycle tick: {exc}")

        try:
            beat = self.heartbeat.maybe_emit()
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"heartbeat: {exc}")
            beat = None
        if beat is not None:
            try:
                self._append_event("watchdog_heartbeat", beat)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"heartbeat event: {exc}")
        try:
            self.policy_store.save()
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"policy store save: {exc}")
        return report

    # ------------------------------------------------------------ trip handling
    def _handle_trip(self, trip: WatchdogTrip) -> dict:
        # 1. Freeze FIRST — durable before anything else.
        frozen = self.policy_store.freeze(trip.policy_id,
                                          f"watchdog_trip:{trip.reason}")
        try:
            self.policy_store.save()
        except Exception:
            pass  # the audited event below still records the intent
        # 2. Audited event.
        try:
            self._append_event("watchdog_trip", trip.to_dict())
        except Exception:
            pass
        # 3. Page the human. If THIS dies, the heartbeat stops and the
        # dead-man's-switch watcher pages instead — that is the design.
        summary = (f"suppression watchdog TRIP: policy {trip.policy_id} "
                   f"{trip.reason} (rate {trip.observed_rate:.3f} vs "
                   f"expected {trip.expected_rate:.3f}, "
                   f"n={trip.decisions_in_window})")
        try:
            self._page_human(summary, trip.to_dict())
        except Exception:
            pass
        return {"trip": trip.to_dict(),
                "frozen": frozen is not None}

    def _handle_lifecycle_event(self, emitted: dict) -> None:
        etype = emitted.get("type", "")
        try:
            self._append_event(etype, emitted)
        except Exception:
            pass
        if emitted.get("page_owner") or emitted.get("page"):
            try:
                self._page_human(emitted.get("summary", etype), emitted)
            except Exception:
                pass
