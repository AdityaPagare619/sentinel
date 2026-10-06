"""Stepped fail-open — C1 (design/rfc-c1-stepped-failopen.md).

One architectural change with the adaptive storm detector (correlator.py):
the detector decides WHEN (incident-storm vs busy-estate); this ladder
decides WHAT the gate does when the vendor path degrades. They ship
together because fixing the detector without the steps moves the failure
to the race pool, and the steps without the detector are theater (RFC §5.3
alternatives B/C — both rejected).

The ladder (RFC §3):
  step 0 — normal: the race runs; naive passthrough on timer-win (unchanged).
  step 1 — last-known-good compiled policy: deterministic severity routing
           from the last signed policy version, NO Jev call. Freshness-gated:
           a policy past valid_until auto-falls to step 2 + alarm (C5).
  step 2 — static severity floor: page only source-declared CRITICAL
           (never model-derived — the model is the thing that's down),
           deduped; everything else held for the digest.
  step 3 — rate-capped paging + scannable digest: critical-first,
           novelty-within-severity (per-fingerprint level-shift z-score vs
           its own 24h baseline), deduped; remainder held for the digest.

Type-1 invariants (not tunable):
  * every transition is a named, event-logged step_entered {cause, at}
    (full recovery emits failopen_recovered with the step history);
  * each step surfaces the violet banner (cause + start time UTC+local +
    detector-health line + per-step additions);
  * the degraded path is deterministic — no model output anywhere in
    steps 1-3 (Data/AI constitution);
  * a degraded step can NEVER suppress: step dispositions are
    page_now | page_business_hours | passthrough | folded — "suppress" is
    unreachable by construction of _step_disposition();
  * escalation on absolute page-rate pressure (I-3/D-2) is independent of
    the detector's storm state — a poisoned baseline cannot pin the
    system in step 2 while the digest fills.

Type-2 tunables live on FailopenConfig (decide fast, roll back fast).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Disposition
from .race_payloads import SCHEMA_V, utcnow_iso

logger = logging.getLogger("sentinel.failopen")

# ------------------------------------------------------------------ contract

# Per-row mode carried in-band on every decision_made body while degraded
# (RFC §3.2). The console keys the violet banner off this.
STEP_MODES = ("failopen_step1", "failopen_step2", "failopen_step3")

STEP_NAMES = {
    1: "last-known-good policy",
    2: "severity floor",
    3: "rate-capped paging + digest",
}

STEP_ONE_LINERS = {
    1: "Deterministic fallback — model unavailable. Paging on the last "
       "signed policy's severity routing; nothing here is model-derived.",
    2: "Severity floor — paging source-declared CRITICAL only. Everything "
       "else is held for the digest; nothing is suppressed.",
    3: "Rate-capped — paging the digest head (critical-first, level-shift "
       "ordered) within the page budget; the remainder stays held.",
}

# The suppress conjunction's paging set (mirrors eventlog.PAGE_DISPOSITIONS;
# defined locally so this module has no import cycle through eventlog).
_PAGE_ACTIONS = frozenset({"page_now", "page_business_hours", "passthrough"})

# Step-1 default severity routing. Deterministic, coarse, never suppress:
# without the model there is no probability evidence, so the only honest
# deterministic signal left is the source's own severity label. Unknown
# labels -> passthrough (uncertainty pages — Law 7).
DEFAULT_SEVERITY_ACTIONS = {
    "critical": "page_now",
    "high": "page_business_hours",
    "medium": "page_business_hours",
    "low": "passthrough",
    "info": "passthrough",
}

# Step-2/3 severity classes. Coarse and pre-Jev by design (DR-13's P1/P2
# carve-out, extended): on the degraded path the model's taxonomy is
# distrusted wholesale, so the floor reads the SOURCE label only.
_CRITICAL_LABELS = frozenset({"critical", "p1"})
_HIGH_LABELS = frozenset({"high", "p2"})

_SEVERITY_RANK = {
    "critical": 0, "p1": 0,
    "high": 1, "p2": 1,
    "medium": 2, "p3": 2,
    "low": 3, "p4": 3,
    "info": 4,
}


def _severity_rank(label: str | None) -> int:
    return _SEVERITY_RANK.get((label or "").strip().lower(), 5)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.") + f"{int(ts * 1000) % 1000:03d}Z"


def _iso_local(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


# ------------------------------------------------------- failover policy (C5)

@dataclass
class FailoverPolicy:
    """The last-known-good compiled policy step 1 runs (RFC §3.2).

    policy_id / version / signed_at identify the signed bundle; valid_until
    is the freshness gate (C5) — a policy past valid_until auto-falls to
    step 2 + alarm ("stale fallback is worse than no fallback").
    severity_action_map is the deterministic severity routing (source
    severity -> disposition). Values are validated at construction: a map
    that could suppress is refused — the degraded path never suppresses.
    severity_mapping_name/version are shown in the step-2 banner (RFC §3.3).
    """
    policy_id: str = "sentinel-failover-default"
    version: str = "1"
    signed_at: float | None = None
    valid_until: float | None = None  # None = no freshness bound (default map)
    severity_action_map: dict = field(
        default_factory=lambda: dict(DEFAULT_SEVERITY_ACTIONS))
    severity_mapping_name: str = "sentinel-default"
    severity_mapping_version: str = "1"

    def __post_init__(self):
        bad = {k: v for k, v in self.severity_action_map.items()
               if v not in ("page_now", "page_business_hours", "passthrough")}
        if bad:
            raise ValueError(
                f"failover severity map must never suppress or fold; "
                f"refused entries: {bad}")

    def is_fresh(self, now: float) -> bool:
        """C5 freshness gate. No valid_until => no bound to violate."""
        return self.valid_until is None or now <= self.valid_until

    def action_for(self, severity_in: str | None) -> str:
        return self.severity_action_map.get(
            (severity_in or "").strip().lower(), "passthrough")


# ------------------------------------------------------------------- config

@dataclass
class FailopenConfig:
    """Type-2 knobs for the ladder. Every one is tunable from production
    data without an architecture change (RFC §5.2)."""
    enabled: bool = True
    # Step-1 entry: timer-win rate over the trailing health window (RFC §3.1,
    # K6: strictly stronger than the existing watchdog trip — Pager P3).
    enter_timer_win_rate: float = 0.25
    health_window_s: float = 900.0
    min_decisions: int = 30  # do not step on tiny samples (cf. watchdog)
    # Hysteresis: exit needs the signal below half the entry rate, held;
    # every step dwells a minimum before ANY transition (K4 flap guard).
    dwell_s: float = 600.0
    exit_hold_s: float = 600.0
    # Page-rate pressure (I-3/D-2): absolute volume trigger, independent of
    # the detector's storm state.
    page_budget_per_min: float = 60.0
    pressure_window_s: float = 300.0
    pressure_multiplier: float = 3.0
    # Step-2/3 dedup: a fingerprint paged within this window is not paged
    # again (the correlator's dedup window is the first line; this is the
    # degraded path's own backstop).
    step_dedup_window_s: float = 300.0
    # Step-3 digest triage: "zscore" (level-shift z-score, RFC §4.1 M1 —
    # the panel majority's bet, falsifiable by K5) or "chronological"
    # (Tripwire's D-1 position; the K5 revert path is this one flip).
    digest_ordering: str = "zscore"
    # R15 F3: the digest is bounded (oldest-first eviction at the cap).
    # Step 2 pages criticals only — on an estate with no critical labels
    # the page rate is structurally 0, so the step-2→3 trigger ALSO fires
    # on digest backlog (>= pressure_backlog entries held): a full digest
    # is pressure even when nothing pages.
    digest_max_entries: int = 10000
    digest_pressure_backlog: int = 1000
    # Step-3 budget split: criticals may claim the whole page budget;
    # non-criticals are capped at this share of it. This is what makes
    # "critical-first" real under immediate per-alert decisions (no
    # reorder buffer — a buffer would delay pages and provisionalize
    # dispositions; the RFC's ordering is realized as budget reservation
    # + the ranked digest the operator triages, see _decide_step3).
    non_critical_share: float = 0.5
    runbook_url: str | None = None  # None renders honestly as "not configured"
    # C1/R15: canary probes while degraded. The health monitor is fed
    # only by vendor outcomes ("answered"/"timer_win"/"unhealthy_error"),
    # which never occur while the ladder is stepped down (no Jev calls) —
    # so step-1 recovery was impossible (the sensor was downstream of the
    # actuator). Every canary_every_n-th alert while degraded also runs a
    # lightweight vendor probe; its outcome feeds health ONLY (the stepped
    # disposition stands). This re-establishes the recovery signal.
    canary_every_n: int = 20
    canary_timeout_ms: float = 2000.0
    # Digest emission cadence (emission-channel payload for the console).
    digest_emit_every_held: int = 50
    digest_emit_every_s: float = 300.0

    def __post_init__(self):
        if self.digest_ordering not in ("zscore", "chronological"):
            raise ValueError(
                f"unknown digest_ordering {self.digest_ordering!r}")


# ----------------------------------------------------------------- monitors

class VendorHealthMonitor:
    """Timer-win rate over a trailing window — the RFC §3.1 step-1 trigger
    signal ("watchdog sustained-degradation signal").

    A timer win means the vendor was too slow; an unhealthy error (client
    exception / error_passthrough) means the vendor path failed outright —
    a dead vendor is the extreme of "vendor slow", and a pure timer-win
    measure would be blind to it (the RFC under-specifies this; counting
    errors as unhealthy is the fail-safe reading). Answered-in-time is the
    healthy sample. Structural/failopen outcomes never touched the vendor
    and are not samples.
    """

    def __init__(self, clock=None):
        self._now = clock
        self._samples: deque = deque()  # (ts, unhealthy: bool)
        # The gate is shared across triage threads: record() appends while
        # timer_win_rate() prunes+iterates. Unsynchronized this raises
        # "deque mutated during iteration" under concurrent load (caught
        # live by the millions-scale load test) and silently drops fail-open
        # observations — the ladder goes blind exactly when it matters.
        self._lock = threading.Lock()

    def _t(self, now):
        return now if now is not None else self._now()

    def record(self, unhealthy: bool, now: float | None = None) -> None:
        with self._lock:
            self._samples.append((self._t(now), bool(unhealthy)))

    def timer_win_rate(self, window_s: float,
                       now: float | None = None) -> tuple[float, int]:
        """(rate, n) over the trailing window. n < min_decisions => the
        controller refuses to step (noise, not signal — Pager P3)."""
        t = self._t(now)
        cutoff = t - window_s
        wins = n = 0
        # Prune from the left; count the rest. Bounded by construction:
        # entries older than the largest window any caller uses are dropped.
        # Snapshot under the lock so a concurrent record() cannot mutate
        # the deque mid-iteration.
        with self._lock:
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()
            samples = list(self._samples)
        for ts, bad in samples:
            if ts >= cutoff:
                n += 1
                wins += 1 if bad else 0
        return (wins / n if n else 0.0), n


class PageRateMonitor:
    """Page-eligible volume per minute over a trailing window — the I-3/D-2
    absolute pressure signal. Page-eligible = the suppress conjunction's
    paging set (page_now, page_business_hours, passthrough)."""

    def __init__(self, clock=None):
        self._now = clock
        self._pages: deque = deque()  # ts of page-eligible decisions
        # Same concurrency contract as VendorHealthMonitor: the gate is
        # shared across triage threads.
        self._lock = threading.Lock()

    def _t(self, now):
        return now if now is not None else self._now()

    def record(self, page_eligible: bool, now: float | None = None) -> None:
        if page_eligible:
            with self._lock:
                self._pages.append(self._t(now))

    def page_rate_per_min(self, window_s: float,
                          now: float | None = None) -> float:
        t = self._t(now)
        cutoff = t - window_s
        with self._lock:
            while self._pages and self._pages[0] < cutoff:
                self._pages.popleft()
            pages = list(self._pages)
        n = sum(1 for ts in pages if ts >= cutoff)
        return n / (window_s / 60.0)


# ------------------------------------------------------------------ payloads

def _envelope(event_type: str) -> dict:
    """Emission-channel envelope for fail-open payloads (alert-less: a step
    transition is about the gate, not one alert). Mirrors the race_payloads
    envelope shape so the dispatcher/event-log lane can persist the durable
    ones (failopen_step_entered / failopen_recovered are eventlog types)."""
    return {
        "type": event_type,
        "actor": "engine",
        "schema_v": SCHEMA_V,
        "ts": utcnow_iso(),
        "event_id": None,   # assigned by the event log
        "seq": None,        # assigned by the event log (authoritative order)
        "alert_id": None,
        "fingerprint": None,
        "episode_id": None,
        "outbox_id": None,
        "body": {},
    }


def format_detector_health_line(health: dict | None) -> str:
    """Pager P1: the banner always carries the detector's judgment, not
    just the steps' actions."""
    if not health:
        return ("Storm detector: health unavailable (no correlator wired to "
                "the gate — wire it so the banner can show W(t))")
    last = health.get("last_declared_ts")
    last_s = (f"{_iso(last)} ({_iso_local(last)} local)" if last
              else "never")
    frozen = " [BASELINE FROZEN]" if health.get("baseline_frozen") else ""
    # Defensive: the banner must never break on a partial health dict —
    # a degraded banner that crashes is worse than an approximate one.
    w = health.get("w_distinct_60s")
    thr = health.get("threshold")
    if thr is None:
        core = f"Storm detector: W(t)={w} distinct/60s, threshold unavailable"
    else:
        f = health.get("floor_f")
        k = health.get("k")
        b = health.get("baseline_b")
        fs = f"{f:.0f}" if isinstance(f, (int, float)) else "?"
        ks = f"{k}" if k is not None else "?"
        bs = f"{b:.1f}" if isinstance(b, (int, float)) else "?"
        core = (f"Storm detector: W(t)={w} distinct/60s, "
                f"threshold=max(F, k·B)={thr:.1f} "
                f"(F={fs}, k={ks}, B={bs})")
    return f"{core}, last declared {last_s}{frozen}"


def failopen_step_entered_payload(*, step: int, cause: str, entered_at: float,
                                  prior_step: int,
                                  detector_health: dict | None) -> dict:
    """The durable, event-logged transition (RFC §3.1)."""
    env = _envelope("failopen_step_entered")
    env["body"] = {
        "step": step,
        "step_name": STEP_NAMES[step],
        "cause": cause,
        "entered_at": _iso(entered_at),
        "entered_at_local": _iso_local(entered_at),
        "prior_step": prior_step,
        "detector_health": detector_health or {},
        "detector_health_line": format_detector_health_line(detector_health),
    }
    return env


def failopen_recovered_payload(*, recovered_at: float, step_history: list,
                               total_degraded_s: float) -> dict:
    """Full recovery to step 0 — carries the step history (RFC §3.1)."""
    env = _envelope("failopen_recovered")
    env["body"] = {
        "recovered_at": _iso(recovered_at),
        "recovered_at_local": _iso_local(recovered_at),
        "step_history": list(step_history),
        "total_degraded_s": total_degraded_s,
    }
    return env


def failopen_banner_payload(*, step: int, cause: str, started_at: float,
                            step_history: list, detector_health: dict | None,
                            config: FailopenConfig,
                            policy: FailoverPolicy,
                            paged: int, held: int,
                            source_critical_held: int,
                            digest_top: list) -> dict:
    """The violet banner (RFC §3.3) — an emission-channel payload for the
    console, NOT a durable event-log row (like storm_root_cause_payload:
    the console renders it; the transition's durability lives in
    failopen_step_entered).

    Per the Interface Principles DegradedBanner contract: what failed,
    what's shown instead, what to do. Per P3: the degraded state is
    explicit, never a blank screen. Per P2: the banner carries its
    evidence companions (detector health, policy identity, digest counts).
    """
    env = _envelope("failopen_banner")
    hist = " → ".join(
        f"step {h['step']} {h['entered_at']}–{h.get('exited_at', '…')}"
        for h in step_history) or "—"
    body = {
        # Rendering contract for the console: the system-channel violet
        # banner. Text-first (never color-only, per Interface Principles
        # §7): the accent is a hint, the words carry the meaning.
        "accent": "violet",
        "surface": "system_banner",
        "step": step,
        "step_name": STEP_NAMES.get(step, "recovered"),
        "cause": cause,
        "started_at": _iso(started_at),
        "started_at_local": _iso_local(started_at),
        "step_history": hist,
        "semantics": (STEP_ONE_LINERS.get(step)
                      or "Vendor path recovered — normal race behavior "
                         "restored."),
        "runbook_url": config.runbook_url,  # None renders as "not configured"
        "detector_health_line": format_detector_health_line(detector_health),
        "detector_health": detector_health or {},
        "digest": {
            "held": held,
            "paged": paged,
            "source_critical_held": source_critical_held,
            "top": digest_top,  # top-3 ranked; one click from the banner
        },
    }
    if step == 1:
        body["policy"] = {
            "policy_id": policy.policy_id,
            "version": policy.version,
            "signed_at": (_iso(policy.signed_at)
                          if policy.signed_at else None),
            "valid_until": (_iso(policy.valid_until)
                            if policy.valid_until else None),
        }
    elif step == 2:
        body["severity_mapping"] = {
            "name": policy.severity_mapping_name,
            "version": policy.severity_mapping_version,
        }
        body["held_for_digest"] = held
    elif step == 3:
        body["page_budget_per_min"] = config.page_budget_per_min
    env["body"] = body
    return env


def failopen_digest_payload(*, step: int, digest_id: str, held: int,
                            paged: int, source_critical_held: int,
                            entries: list) -> dict:
    """The scannable digest snapshot (RFC §3.2 step 3) — emission-channel
    payload; the banner links to it. Triage, not a parking lot: ranked
    critical-first, then level-shift z-score within severity (or
    chronological when digest_ordering says so — the K5 revert path)."""
    env = _envelope("failopen_digest")
    env["body"] = {
        "digest_id": digest_id,
        "step": step,
        "emitted_at": utcnow_iso(),
        "held": held,
        "paged": paged,
        "source_critical_held": source_critical_held,
        "entries": entries,
    }
    return env


# ---------------------------------------------------------------- controller

@dataclass
class _DigestEntry:
    fingerprint: str
    service: str
    check: str
    severity_in: str
    alert_id: str
    first_held_ts: float
    count: int = 1
    paged: bool = False


class FailopenController:
    """The stepped-degradation state machine (RFC §3.1 trigger hierarchy).

    Owned by the gate; fed one observation per decision. All transition
    evaluation is synchronous and deterministic — no threads, no Jev, no
    I/O. Emits payloads; the gate routes them to its emission channel.
    """

    def __init__(self, config: FailopenConfig | None = None, *,
                 clock=None,
                 detector_health_fn=None,
                 zscore_fn=None,
                 failover_policy: FailoverPolicy | None = None,
                 alarm_fn=None):
        self.config = config or FailopenConfig()
        self._now_fn = clock
        self._detector_health_fn = detector_health_fn
        self._zscore_fn = zscore_fn
        self.policy = failover_policy or FailoverPolicy()
        self._alarm_fn = alarm_fn or _default_alarm
        self._health = VendorHealthMonitor(clock=clock)
        self._pages = PageRateMonitor(clock=clock)
        self._step = 0
        self._entered_at = self._t(None)
        self._degraded_since: float | None = None  # set on 0 -> N, cleared on recovery
        self._current_cause = "initial"
        self._step_history: list[dict] = []
        self._canary_count = 0  # R15: canary cadence while degraded
        self._since: dict[str, float] = {}  # sustained-condition tracking
        self._paged_fps: dict[str, float] = {}  # fp -> last paged ts (dedup)
        self._paged_tokens: deque = deque()  # step-3 (ts, is_critical), 60s cap
        self._digest: dict[str, _DigestEntry] = {}  # fp -> entry (ranked view)
        self._digest_seq = 0
        self._paged_total = 0
        self._held_total = 0
        self._critical_held = 0
        self._last_digest_emit_held = 0
        self._last_digest_emit_ts = 0.0
        # Serializes observe(): the gate is shared across triage threads.
        # RLock because _evaluate paths may re-enter via property reads.
        self._lock = threading.RLock()

    # ------------------------------------------------------------ plumbing

    def _t(self, now):
        if now is not None:
            return now
        return self._now_fn() if self._now_fn else time.time()

    @property
    def current_step(self) -> int:
        with self._lock:
            return self._step

    @property
    def step_history(self) -> list[dict]:
        with self._lock:
            return list(self._step_history)

    def should_canary(self) -> bool:
        """R15: True every canary_every_n-th observation while degraded.

        The gate calls this in _on_failopen_step; when True, it runs a
        lightweight vendor probe whose outcome feeds the health monitor
        ONLY (the stepped disposition stands). This is the recovery
        signal — without it, step-1 exit is impossible.
        """
        with self._lock:
            if self._step == 0:
                return False
            self._canary_count += 1
            return self._canary_count % self.config.canary_every_n == 0

    def _detector_health(self) -> dict | None:
        if self._detector_health_fn is None:
            return None
        try:
            return self._detector_health_fn()
        except Exception as exc:  # health must never sink the ladder
            logger.warning("detector health unreadable (%s)", exc)
            return None

    def _zscore(self, fp: str) -> float:
        if self._zscore_fn is None:
            return 0.0
        try:
            return float(self._zscore_fn(fp) or 0.0)
        except Exception:
            return 0.0

    # ------------------------------------------------------------ observe

    def observe(self, *, vendor_outcome: str, action: str,
                now: float | None = None) -> list[dict]:
        """One observation per gate decision. vendor_outcome: "timer_win" |
        "answered" | "unhealthy_error" | "structural" | "failopen".
        Returns emission payloads (transitions, banner, digest).

        The whole observation is serialized: the ladder keeps step state,
        sustained-condition timers, dedup maps and digest rankings in plain
        dicts/deques, and the gate is shared across triage threads. Without
        this, concurrent decisions corrupt the ladder's state (the deque
        mutation crash the load test caught was the loud symptom; dict
        races here would be silent). Observations stay synchronous and
        deterministic — just atomic.
        """
        with self._lock:
            t = self._t(now)
            if self.config.enabled:
                if vendor_outcome in ("timer_win", "unhealthy_error"):
                    self._health.record(True, t)
                elif vendor_outcome == "answered":
                    self._health.record(False, t)
                # "structural"/"failopen" never touched the vendor: not samples.
                self._pages.record(action in _PAGE_ACTIONS, t)
            return self._evaluate(t)

    # ------------------------------------------------------------ the ladder

    def _held(self, name: str, cond: bool, hold_s: float, now: float) -> bool:
        """True when cond has been continuously true for hold_s."""
        if cond:
            self._since.setdefault(name, now)
            return now - self._since[name] >= hold_s
        self._since.pop(name, None)
        return False

    def _evaluate(self, now: float) -> list[dict]:
        cfg = self.config
        rate, n = self._health.timer_win_rate(cfg.health_window_s, now)
        prate = self._pages.page_rate_per_min(cfg.pressure_window_s, now)
        budget = cfg.page_budget_per_min
        dwell_ok = (now - self._entered_at) >= cfg.dwell_s
        step = self._step

        if step == 0:
            if (n >= cfg.min_decisions
                    and rate > cfg.enter_timer_win_rate and dwell_ok):
                return self._enter(
                    1, now,
                    f"timer-win rate {rate:.1%} > {cfg.enter_timer_win_rate:.0%} "
                    f"over trailing {cfg.health_window_s / 60:.0f}min "
                    f"(n={n}) — vendor sustained-degradation signal (K6)")
            if prate > cfg.pressure_multiplier * budget and dwell_ok:
                # I-3/D-2: absolute volume trigger — independent of the
                # detector's storm state.
                return self._enter(
                    3, now,
                    f"absolute page pressure: {prate:.1f}/min > "
                    f"{cfg.pressure_multiplier:.0f}x page budget "
                    f"({budget:.0f}/min) for >{cfg.pressure_window_s / 60:.0f}min")
        elif step == 1:
            if not self.policy.is_fresh(now):
                # C5: stale fallback is worse than no fallback — auto-fall,
                # bypassing dwell (safety escalations never wait).
                return self._enter(
                    2, now,
                    f"step-1 policy stale (valid_until "
                    f"{_iso(self.policy.valid_until)}); auto-fall to "
                    f"severity floor (C5)",
                    bypass_dwell=True, alarm=True)
            if prate > budget and dwell_ok:
                return self._enter(
                    2, now,
                    f"step-1 page rate {prate:.1f}/min > page budget "
                    f"{budget:.0f}/min")
            if dwell_ok and self._held(
                    "recover", n >= cfg.min_decisions
                    and rate < cfg.enter_timer_win_rate / 2
                    and prate <= budget, cfg.exit_hold_s, now):
                return self._recover(now)
        elif step == 2:
            if dwell_ok and self._held(
                    "esc23", prate > budget
                    # R15 F3: step 2 pages criticals only — on an estate
                    # with no critical labels prate is structurally 0.
                    # A filling digest is pressure too: escalate on backlog.
                    or len(self._digest)
                    >= self.config.digest_pressure_backlog,
                    cfg.pressure_window_s, now):
                return self._enter(
                    3, now,
                    f"step-2 pressure: page rate {prate:.1f}/min > budget "
                    f"{budget:.0f}/min or digest backlog "
                    f"{len(self._digest)} >= "
                    f"{self.config.digest_pressure_backlog} for "
                    f">{cfg.pressure_window_s / 60:.0f}min")
            if (dwell_ok and self.policy.is_fresh(now)
                    and self._held("deesc21", prate <= budget,
                                   cfg.exit_hold_s, now)):
                return self._enter(
                    1, now,
                    f"page rate {prate:.1f}/min back under budget for "
                    f">={cfg.exit_hold_s / 60:.0f}min — vendor path recovering")
        elif step == 3:
            if dwell_ok and self._held(
                    "deesc32", prate <= budget, cfg.exit_hold_s, now):
                return self._enter(
                    2, now,
                    f"page rate {prate:.1f}/min back under budget for "
                    f">={cfg.exit_hold_s / 60:.0f}min")

        # Digest emission cadence while holding (steps 2/3).
        if step in (2, 3) and self._held_total:
            if (self._held_total - self._last_digest_emit_held
                    >= cfg.digest_emit_every_held
                    or now - self._last_digest_emit_ts >= cfg.digest_emit_every_s):
                return [self._digest_payload(now)]
        return []

    def _enter(self, step: int, now: float, cause: str, *,
               bypass_dwell: bool = False, alarm: bool = False) -> list[dict]:
        cfg = self.config
        if not bypass_dwell and (now - self._entered_at) < cfg.dwell_s:
            return []  # dwell gate: no transition yet (K4 flap guard)
        prior = self._step
        if prior == 0 and step > 0:
            self._degraded_since = now
        self._step_history.append({
            "step": prior,
            "entered_at": _iso(self._entered_at),
            "exited_at": _iso(now),
            "cause": self._current_cause,
        })
        self._step = step
        self._entered_at = now
        self._current_cause = cause
        self._since.clear()
        # Fresh activation: new digest, new counters, new dedup window.
        self._digest.clear()
        self._digest_seq += 1
        self._paged_total = 0
        self._held_total = 0
        self._critical_held = 0
        self._paged_fps.clear()
        self._paged_tokens.clear()
        self._last_digest_emit_held = 0
        self._last_digest_emit_ts = now
        health = self._detector_health()
        events = [failopen_step_entered_payload(
            step=step, cause=cause, entered_at=now, prior_step=prior,
            detector_health=health)]
        # Every step surfaces the violet banner — never silent (RFC §3.3).
        events.append(self._banner_payload(now, health))
        if alarm:
            self._alarm_fn(
                f"SENTINEL step-1 policy stale — auto-fell to step 2 "
                f"(severity floor). {cause}")
        logger.warning("failopen: step %d (%s) entered: %s",
                       step, STEP_NAMES[step], cause)
        return events

    def request_step(self, step: int, cause: str,
                     now: float | None = None) -> list[dict]:
        """External escalation (e.g. the gate's C5 auto-fall on a stale
        policy mid-decision). Safety escalations bypass dwell."""
        t = self._t(now)
        return self._enter(step, t, cause, bypass_dwell=True,
                           alarm=(step == 2 and "stale" in cause))

    def _recover(self, now: float) -> list[dict]:
        self._step_history.append({
            "step": self._step,
            "entered_at": _iso(self._entered_at),
            "exited_at": _iso(now),
            "cause": self._current_cause,
        })
        total = (now - self._degraded_since
                 if self._degraded_since is not None else 0.0)
        history = self.step_history
        self._step = 0
        self._entered_at = now
        self._degraded_since = None
        self._current_cause = "recovered"
        self._since.clear()
        self._digest.clear()
        logger.warning("failopen: recovered to step 0 after %.0fs degraded",
                       total)
        return [failopen_recovered_payload(
                    recovered_at=now, step_history=history,
                    total_degraded_s=total),
                self._banner_payload(now, self._detector_health())]

    # ------------------------------------------------------------ decisions

    def decide(self, alert, step: int, now: float | None = None) -> Disposition:
        """The deterministic degraded disposition for one alert. Never
        calls Jev, never raises (the company-ending bug is dropping the
        page — a bug here degrades to passthrough, loudly)."""
        t = self._t(now)
        try:
            if step == 1:
                return self._decide_step1(alert)
            if step == 2:
                return self._decide_step2(alert, t)
            if step == 3:
                return self._decide_step3(alert, t)
            raise ValueError(f"unknown failopen step {step}")
        except Exception as exc:  # fail open, loudly
            logger.exception("failopen step-%d decide failed: %s", step, exc)
            return Disposition(action="passthrough",
                               reason=f"failopen_step{step}_error",
                               team=None, confidence=None, latency_ms=0.0)

    def _decide_step1(self, alert) -> Disposition:
        # Last-known-good policy WITHOUT the Jev call: the deterministic
        # severity routing. Never suppress (FailoverPolicy refuses
        # suppress-capable maps at construction).
        action = self.policy.action_for(alert.severity_in)
        return Disposition(action=action, reason="failopen_step1",
                           team=None, confidence=None, latency_ms=0.0)

    def _is_critical(self, alert) -> bool:
        return (alert.severity_in or "").strip().lower() in _CRITICAL_LABELS

    def _deduped(self, fp: str, now: float) -> bool:
        """True when this fingerprint paged within the dedup window."""
        last = self._paged_fps.get(fp)
        return last is not None and now - last < self.config.step_dedup_window_s

    def _decide_step2(self, alert, now: float) -> Disposition:
        # Severity floor: source-declared CRITICAL only (never
        # model-derived — the model is the thing that's down). Deduped.
        # Everything else is held for the digest — folded, never suppressed.
        if self._is_critical(alert) and not self._deduped(alert.fingerprint,
                                                           now):
            self._paged_fps[alert.fingerprint] = now
            self._paged_total += 1
            return Disposition(action="page_now", reason="failopen_step2",
                               team=None, confidence=None, latency_ms=0.0)
        self._hold(alert, now)
        return Disposition(action="folded", reason="failopen_step2_digest",
                           team=None, confidence=None, latency_ms=0.0)

    def _decide_step3(self, alert, now: float) -> Disposition:
        # Rate-capped paging over the ranked digest (RFC §3.2 step 3).
        #
        # "Critical-first ordering" under immediate per-alert decisions:
        # criticals may claim the whole page budget; non-criticals are
        # capped at non_critical_share of it (budget reservation — a
        # reorder buffer would delay pages and provisionalize the audit
        # disposition, so the RFC's ordering is realized here as
        # reservation + the ranked digest the operator triages from the
        # banner). Within a class, admission prefers the digest head:
        # a candidate pages iff fewer same-class unpaged candidates rank
        # ahead of it than its class's remaining tokens
        # (novelty-within-severity via the level-shift z-score rank).
        # Deduped. The remainder stays held (folded): triage, not a
        # parking lot — held alerts never re-page later.
        if self._deduped(alert.fingerprint, now):
            self._hold(alert, now)
            return Disposition(action="folded", reason="failopen_step3_digest",
                               team=None, confidence=None, latency_ms=0.0)
        entry = self._hold(alert, now)
        cutoff = now - 60.0
        while self._paged_tokens and self._paged_tokens[0][0] < cutoff:
            self._paged_tokens.popleft()
        cap = int(self.config.page_budget_per_min)
        total_pages = len(self._paged_tokens)
        crit_pages = sum(1 for _, c in self._paged_tokens if c)
        is_crit = self._is_critical(alert)
        if is_crit:
            remaining = cap - total_pages
            admitted = (remaining > 0
                        and self._count_ahead_in_class(entry, True) < remaining)
        else:
            noncrit_cap = int(cap * self.config.non_critical_share)
            noncrit_pages = total_pages - crit_pages
            remaining = max(0, min(cap - total_pages,
                                   noncrit_cap - noncrit_pages))
            admitted = (remaining > 0
                        and self._count_ahead_in_class(entry, False)
                        < remaining)
        if admitted:
            entry.paged = True
            self._paged_tokens.append((now, is_crit))
            self._paged_fps[alert.fingerprint] = now
            self._paged_total += 1
            return Disposition(action="page_now", reason="failopen_step3",
                               team=None, confidence=None, latency_ms=0.0)
        return Disposition(action="folded", reason="failopen_step3_digest",
                           team=None, confidence=None, latency_ms=0.0)

    # ------------------------------------------------------------ the digest

    def _hold(self, alert, now: float) -> _DigestEntry:
        entry = self._digest.get(alert.fingerprint)
        if entry is None:
            # R15 F3: bounded digest — evict the oldest-held entry at cap.
            # The evicted entry's count is preserved in _held_total; the
            # digest is a triage view, not the audit trail (every hold is
            # event-logged at hold time).
            if len(self._digest) >= self.config.digest_max_entries:
                oldest = min(self._digest.values(),
                             key=lambda e: e.first_held_ts)
                del self._digest[oldest.fingerprint]
            entry = _DigestEntry(
                fingerprint=alert.fingerprint, service=alert.service,
                check=alert.check, severity_in=alert.severity_in or "",
                alert_id=alert.alert_id, first_held_ts=now)
            self._digest[alert.fingerprint] = entry
            self._held_total += 1
            if self._is_critical(alert):
                self._critical_held += 1
        else:
            entry.count += 1
            entry.alert_id = alert.alert_id
        return entry

    def _rank_key(self, entry: _DigestEntry):
        if self.config.digest_ordering == "chronological":
            return (0, entry.first_held_ts, entry.fingerprint)
        # zscore: critical-first, then level-shift z-score within severity
        # (RFC §4.1 M1 — the panel majority's bet; K5 is the falsifier).
        return (_severity_rank(entry.severity_in),
                -self._zscore(entry.fingerprint),
                entry.first_held_ts, entry.fingerprint)

    def _count_ahead(self, entry: _DigestEntry) -> int:
        key = self._rank_key(entry)
        return sum(1 for e in self._digest.values()
                   if not e.paged and e is not entry
                   and self._rank_key(e) < key)

    def _count_ahead_in_class(self, entry: _DigestEntry,
                              critical: bool) -> int:
        """Unpaged same-class digest members ranking strictly ahead —
        the within-class admission count for step 3."""
        key = self._rank_key(entry)
        return sum(
            1 for e in self._digest.values()
            if (not e.paged and e is not entry
                and (e.severity_in.strip().lower() in _CRITICAL_LABELS)
                == critical
                and self._rank_key(e) < key))

    def _ranked_unpaged(self, limit: int) -> list[_DigestEntry]:
        cands = [e for e in self._digest.values() if not e.paged]
        cands.sort(key=self._rank_key)
        return cands[:limit]

    def digest_snapshot(self, top_n: int = 10) -> dict:
        top = self._ranked_unpaged(top_n)
        # "Held" means currently held (unpaged) — entries that paged are
        # no longer held. _held_total/_critical_held are lifetime counters
        # for the audit trail, not the current digest depth.
        unpaged = [e for e in self._digest.values() if not e.paged]
        return {
            "digest_id": f"failopen-step{self._step}-{self._digest_seq}",
            "step": self._step,
            "held": len(unpaged),
            "paged": self._paged_total,
            "source_critical_held": sum(
                1 for e in unpaged if e.severity_in.strip().lower()
                in _CRITICAL_LABELS),
            "entries": [{
                "fingerprint": e.fingerprint,
                "service": e.service,
                "check": e.check,
                "severity_in": e.severity_in,
                "alert_id": e.alert_id,
                "first_held_at": _iso(e.first_held_ts),
                "count": e.count,
                "level_shift_z": (round(self._zscore(e.fingerprint), 2)
                                  if self.config.digest_ordering == "zscore"
                                  else None),
            } for e in top],
        }

    def _digest_payload(self, now: float) -> dict:
        snap = self.digest_snapshot(top_n=25)
        self._last_digest_emit_held = self._held_total
        self._last_digest_emit_ts = now
        return failopen_digest_payload(
            step=self._step, digest_id=snap["digest_id"], held=snap["held"],
            paged=snap["paged"],
            source_critical_held=snap["source_critical_held"],
            entries=snap["entries"])

    # ------------------------------------------------------------ the banner

    def banner(self, now: float | None = None) -> dict:
        """Current banner payload (the console polls this; transitions
        also push one)."""
        t = self._t(now)
        return self._banner_payload(t, self._detector_health())

    def _banner_payload(self, now: float, health: dict | None) -> dict:
        snap = self.digest_snapshot(top_n=3)
        return failopen_banner_payload(
            step=self._step, cause=self._current_cause, started_at=self._entered_at,
            step_history=self.step_history, detector_health=health,
            config=self.config, policy=self.policy,
            paged=self._paged_total, held=snap["held"],
            source_critical_held=snap["source_critical_held"],
            digest_top=snap["entries"])


def _default_alarm(msg: str) -> None:
    """Loud stderr until the engine owns an operator-paging channel
    (same placeholder contract as the watchdog page). Never silent."""
    print(f"[sentinel] FAILOPEN ALARM: {msg}", file=sys.stderr)
