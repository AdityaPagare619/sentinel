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
import sys
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
        # B1: undelivered trip/lifecycle actions. The heartbeat attests
        # CAPABILITY, not liveness: it flows only when this is empty.
        self._pending: dict[str, dict] = {}
        self._pending_seq = 0
        # B3: baseless-policy pages are throttled per process lifetime;
        # a re-baselined policy leaves the set so a later loss re-alarms.
        self._baseless_paged: set[str] = set()
        # B3: re-register persisted baselines on boot — a restart must not
        # leave live policies unwatched. Explicitly-constructed baselines
        # win on conflict (current operator intent beats stored state).
        for pid, bdict in self.policy_store.baselines().items():
            if pid not in self.watchdog.baselines:
                try:
                    self.watchdog.baselines[pid] = \
                        SuppressionBaseline.from_dict(bdict)
                except Exception as exc:  # noqa: BLE001 — loud, not fatal
                    sys.stderr.write(
                        f"[sentinel] WARNING: persisted baseline for {pid} "
                        f"failed to parse ({exc}) — policy unwatched until "
                        f"re-registered\n")

    def register_baseline(self, baseline: SuppressionBaseline) -> None:
        """Sign + persist a watchdog baseline (B3.3 canary->live gate).

        Validates the 2-signer rule, registers with the live watchdog,
        AND persists to the policy-state file — one call, so a baseline
        can never again be in-memory-only (B3)."""
        self.watchdog.set_baseline(baseline)  # raises on <2 signers
        self.policy_store.set_baseline(baseline.to_dict())
        self.policy_store.save()

    def run_once(self) -> dict:
        """One control-loop iteration. Never raises.

        The heartbeat attests CAPABILITY, not liveness (B1): it flows only
        when no trip/lifecycle action is undelivered. A freeze that never
        persisted or a page that never went out stops the beat — the
        separate dead-man's-switch watcher then pages on the missing
        heartbeat. A watchdog that claims health while its trip actions
        are parked is theater.
        """
        report: dict = {"trips": [], "lifecycle": [], "errors": [],
                        "pending": len(self._pending)}
        now = self._clock()
        # Recovery first: deliver whatever the last loop couldn't, so a
        # healed disk/PD path resumes the heartbeat ASAP.
        self._flush_pending(report)
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

        # B3: baseless-policy alarm — a live/review-due policy with no signed
        # baseline (or a baseline for a different version) is UNWATCHED: no
        # trips will ever fire for it. LOUD, never silent. Emergency
        # overrides trip this by construction (no baseline survives the
        # bypass) — that is intended.
        self._alarm_baseless_policies(report)

        # Lifecycle clock: review-due pages owner, expiry fails loud.
        try:
            for emitted in self.policy_store.tick(
                    _dt.datetime.fromtimestamp(now, _dt.timezone.utc)):
                self._handle_lifecycle_event(emitted)
                report["lifecycle"].append(emitted)
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"lifecycle tick: {exc}")

        # B1: the beat attests that every trip/lifecycle action so far is
        # delivered. Anything parked in _pending stops the heartbeat.
        if not self._pending:
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
        else:
            report["errors"].append(
                f"heartbeat withheld: {len(self._pending)} undelivered "
                f"action(s) — capability degraded, watcher will page")
        try:
            self.policy_store.save()
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"policy store save: {exc}")
        report["pending"] = len(self._pending)
        return report

    # ------------------------------------------------------------ trip handling
    @staticmethod
    def _trip_summary(trip: WatchdogTrip) -> str:
        return (f"suppression watchdog TRIP: policy {trip.policy_id} "
                f"{trip.reason} (rate {trip.observed_rate:.3f} vs "
                f"expected {trip.expected_rate:.3f}, "
                f"n={trip.decisions_in_window})")

    def _handle_trip(self, trip: WatchdogTrip) -> dict:
        """Deliver a trip action: freeze (durable first), audited event,
        page. Returns the delivery status. Anything undelivered is parked
        in _pending — and a non-empty _pending stops the heartbeat (B1)."""
        entry: dict = {"kind": "trip", "trip": trip,
                       "freeze_done": False, "audit_done": False,
                       "page_done": False, "last_error": None}
        self._deliver_trip(entry)
        status = {"trip": trip.to_dict(),
                  "freeze_durable": entry["freeze_done"],
                  "audited": entry["audit_done"],
                  "paged": entry["page_done"],
                  "last_error": entry["last_error"]}
        if not (entry["freeze_done"] and entry["page_done"]):
            self._pending_seq += 1
            self._pending[f"trip:{trip.policy_id}:{self._pending_seq}"] = entry
        return status

    def _deliver_trip(self, entry: dict) -> None:
        """Attempt the missing pieces of a trip action. Idempotent —
        freeze() on an already-frozen version is a no-op. Never raises."""
        trip: WatchdogTrip = entry["trip"]
        if not entry["freeze_done"]:
            # 1. Freeze FIRST — durable before anything else, so a crash
            # after this point still leaves suppression blocked.
            try:
                self.policy_store.freeze(
                    trip.policy_id, f"watchdog_trip:{trip.reason}")
                self.policy_store.save()
                entry["freeze_done"] = True
            except Exception as exc:  # noqa: BLE001
                entry["last_error"] = f"freeze: {exc}"
        if not entry["audit_done"]:
            # 2. Audited event — records the detection truthfully,
            # including whether the freeze actually landed (audit parity:
            # the file mutation, when it lands, always has its event).
            try:
                body = dict(trip.to_dict())
                body["freeze_durable"] = entry["freeze_done"]
                self._append_event("watchdog_trip", body)
                entry["audit_done"] = True
            except Exception as exc:  # noqa: BLE001
                entry["last_error"] = f"audit: {exc}"
        if not entry["page_done"]:
            # 3. Page the human.
            try:
                self._page_human(self._trip_summary(trip), trip.to_dict())
                entry["page_done"] = True
            except Exception as exc:  # noqa: BLE001
                entry["last_error"] = f"page: {exc}"

    def _flush_pending(self, report: dict) -> None:
        """Retry every undelivered action. Drops entries only when fully
        delivered — freeze durable AND page sent. Never raises."""
        for key in list(self._pending):
            entry = self._pending[key]
            try:
                if entry["kind"] == "trip":
                    self._deliver_trip(entry)
                elif entry["kind"] == "page":
                    self._page_human(entry["summary"], entry["detail"])
                    entry["page_done"] = True
            except Exception as exc:  # noqa: BLE001
                entry["last_error"] = str(exc)
            if entry.get("freeze_done", True) and entry.get("page_done"):
                del self._pending[key]
            else:
                report["errors"].append(
                    f"pending {key} still undelivered: "
                    f"{entry.get('last_error')}")

    def _alarm_baseless_policies(self, report: dict) -> None:
        """B3: a live/review-due policy with no signed baseline — or a
        baseline signed for a different version — is UNWATCHED. No trips
        will ever fire for it. This pages LOUD (once per process lifetime
        per policy; a re-baselined policy re-arms the alarm) and writes an
        audited event. The page goes through the pending mechanism, so a
        failed baseless page degrades the heartbeat like any other."""
        for pid in self.policy_store.effective_policies():
            baseline = self.watchdog.baselines.get(pid)
            eff = self.policy_store.effective_version(pid)
            eff_version = eff.version if eff is not None else None
            baseless = (baseline is None
                        or baseline.version != eff_version)
            if not baseless:
                self._baseless_paged.discard(pid)
                continue
            if pid in self._baseless_paged:
                continue
            self._baseless_paged.add(pid)
            reason = ("no_baseline" if baseline is None
                      else "baseline_version_mismatch")
            try:
                self._append_event("watchdog_baseless_policy",
                                   {"policy_id": pid, "version": eff_version,
                                    "reason": reason})
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"baseless audit {pid}: {exc}")
            summary = (f"suppression watchdog BLIND: policy {pid} "
                       f"v{eff_version} has {reason} — no trips will fire")
            detail = {"policy_id": pid, "version": eff_version,
                      "reason": reason}
            # Attempt the page now; park only what fails (same capability
            # semantics as trips — a failed baseless page stops the beat).
            entry: dict = {"kind": "page", "summary": summary,
                           "detail": detail, "page_done": False,
                           "last_error": None}
            try:
                self._page_human(summary, detail)
                entry["page_done"] = True
            except Exception as exc:  # noqa: BLE001
                entry["last_error"] = f"page: {exc}"
            if not entry["page_done"]:
                self._pending_seq += 1
                self._pending[f"baseless:{pid}:{self._pending_seq}"] = entry

    def _handle_lifecycle_event(self, emitted: dict) -> None:
        etype = emitted.get("type", "")
        try:
            self._append_event(etype, emitted)
        except Exception:
            pass
        if emitted.get("page_owner") or emitted.get("page"):
            summary = emitted.get("summary", etype)
            try:
                self._page_human(summary, emitted)
            except Exception as exc:  # noqa: BLE001
                # The state transition already persisted via the
                # end-of-loop save; only the notification is missing.
                # Park it: a failed safety notification degrades the
                # heartbeat like any undelivered action (B1, generalized).
                self._pending_seq += 1
                self._pending[
                    f"lifecycle:{etype}:{self._pending_seq}"] = {
                        "kind": "page", "summary": summary,
                        "detail": dict(emitted),
                        "page_done": False,
                        "last_error": f"page: {exc}"}
