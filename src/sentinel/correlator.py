"""Correlator — fingerprint dedup, storm collapse, change windows (§3.5).

Everything here is deterministic and runs *before* any Jev call, per the
non-negotiable design principle: never pay Jev for deterministic work.

In-memory for v0.1 (Redis in the SaaS upgrade). Thread-safe.

ADR-001 (ratified): the correlator owns the *episode lifecycle*:
  - flap-reopen bumps a visible flap count, never auto-promotes severity;
  - P1/P2 (critical/high) are carved out of change-window queuing (DR-13
    reconciliation: a P1 queued to business hours is a silenced P1);
  - a fingerprint unseen for >72h starts a FRESH episode, not a reopen;
  - episodes never auto-close on silence — only an explicit operator
    resolve or a verified resolve signal closes them.
See design/d10-correlator-craft.md for the state machine and rationale.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from .models import Alert, Disposition

logger = logging.getLogger("sentinel.fingerprint")

# ADR-017: fingerprint scheme v2. The v1 formula
# sha256("service|check|severity_in|region") was env-blind: a staging alert
# and a prod alert for the same check hashed identically (the staging→prod
# collision — a concrete missed-SEV1 pre-mortem). v2 appends env+cluster to
# the hash input so the dedup identity is estate-qualified. Any future
# consumer matching the raw fingerprint gets the namespaced hash, not a
# prefix convention that can be silently bypassed.
FINGERPRINT_SCHEME_VERSION = 2

# ADR-001: episode freshness bound. A fingerprint unseen longer than this
# starts a FRESH episode (flap_count reset, history ignored) rather than
# reopening the old one — 72h of silence means the incident's operational
# context is gone. Conceptually aligned with D7's 72h history bound.
EPISODE_FRESHNESS_S = 72 * 3600

# ADR-001: the only legal episode close conditions. "absence of alerts" is
# deliberately absent — silence never closes an episode.
CLOSE_REASONS = ("operator_resolve", "verified_resolve")

# DR-13 reconciliation: source severity labels that map to the P1/P2 class.
# These bypass change-window queuing entirely. This mapping is pre-Jev and
# deliberately coarse — a source-labeled "critical" pages now even if the
# model would have said P4. Fail-loud beats fail-silent for P1.
_P1P2_SEVERITIES = frozenset({"critical", "high", "p1", "p2"})


# ---------------------------------------------------------------------------
# D7 — history provenance (design/d7-history-provenance.md).
#
# Every history input the correlator reads (past alerts, episode records,
# label-pipeline labels) is read under ONE clock — the read time — and
# carries `history_as_of`, the knowledge cutoff: "everything the correlator
# knew at T". Correlator.read_history() is the public provenance path and
# stamps the HistoryView; the internal classifier reads the same maps under
# the same lock and the same `now` (truthful today — the view is a provenance
# sidecar, not yet the classifier's sole input; see R11). history_window_s
# is injectable for tests; production uses HISTORY_WINDOW_S, kept equal to
# EPISODE_FRESHNESS_S by test — one number, one law.

# D7: the single 72h bound. Aligned with ADR-001's EPISODE_FRESHNESS_S — one
# number, one law. Anything older is archaeology: ignored, not down-weighted.
HISTORY_WINDOW_S = 72 * 3600

# D7: label-pipeline freshness SLO. 900s is 3x the pipeline's ASSUMED 5-min
# heartbeat — the pipeline does not exist in-repo yet, so this is an
# unverified external assumption (R11): re-derive from the real heartbeat
# when the pipeline lands. Until then the SLO path is unreachable;
# LabelSnapshot is accepted at ingest but nothing produces it (honest:
# the staleness machinery is built and tested, not yet wired).
# 15 min also dominates the correlator's own short horizons (60s storm
# window, 5-min dedup).
LABEL_PIPELINE_SLO_S = 900

# D7: per-(reason, service) emission cooldown for pages. The correlator
# owns emission discipline (no per-alert page spam); the sink owns delivery
# dedup.
PAGE_COOLDOWN_S = 300

# D7: minimum shadow evidence before novelty may go live (promotion gate).
NOVELTY_SHADOW_MIN_EVENTS = 100


# ---------------------------------------------------------------------------
# C1 — adaptive storm threshold (design/rfc-c1-stepped-failopen.md §2).
#
# The fixed ">20 distinct / 60s" rule is a category error on a denominator
# that spans 55,000x across estates (C2 §9.3): at 1,000 distinct/min the
# window always holds ~1,000 distinct and the detector fires permanently —
# 98% of traffic folds and the proof engine sees almost none of it. The
# declaration rule is now estate-relative:
#
#   storm declares iff W(t) >= max(F, k * B(t))
#
# W(t): distinct fingerprints first-seen in the trailing 60s window (the
#   same measure as before — only arrivals that pass dedup count; folded
#   members do not append, preserving the C2-measured drain behavior).
# B(t): EWMA of the 60s-distinct measure over the trailing 24h, half-life
#   6h, ticked every 60s — self-calibrating from the estate's own
#   statistics (campaign law: self-calibrating over month-tuned constants).
# F:    absolute floor (default 20) — today's constant. On small estates
#   (B tiny) the rule is max(20, ~0) = 20: the floor preserves small-estate
#   declaration semantics (Forge F3 — the adaptive rule may only WIDEN
#   coverage on large estates, never narrow it on small ones). Note the
#   rule is >=, not the old >: at exactly F the storm now declares (the
#   RFC's explicit formula; a widening, not a narrowing).
# k:    velocity-ratio multiplier (default 5, Type 2). Per-tenant tuning
#   rule: k = P99.9(R) + 1.5 over 30 days of R samples, clamped [3, 12].
#
# Type-1 invariants:
#   I-1: B FREEZES while a storm is declared — the detector may not learn
#        from the thing it is detecting (the feedback loop is the failure).
#   I-2: declaration changes WHEN, never WHAT — the fold path stays
#        deterministic, pre-Jev, non-suppressing (D3/ADR-016).
# k and F are safety-affecting (they decide what fraction of traffic
# reaches the proof engine): manual changes need two-person attestation
# (ADR-022/B3 lifecycle); k carries the 30-day review clock.

# C1: baseline EWMA tuning (Type 2).
STORM_BASELINE_TICK_S = 60          # B re-estimated every 60s
STORM_BASELINE_HALF_LIFE_S = 6 * 3600  # 6h half-life over the trailing 24h
STORM_BASELINE_FROZEN_S = 300  # I-1: B frozen 5min after each declaration
# C1/R15: cold-start bootstrap. A fresh correlator has B=0, so the
# threshold is just the floor F — on a busy estate every tick declares
# storm (R15: 98.1% fold, the C2 cliff reproduced). After this window,
# if B is still ~0, the correlator bootstraps B from the storm
# declarations observed (loudly logged). Discrimination: declarations
# spread across the window → busy estate; clustered late → incident.
BOOTSTRAP_WINDOW_S = 1800  # 30min of observation before bootstrapping
STORM_K_DEFAULT = 5.0
STORM_K_MIN, STORM_K_MAX = 3.0, 12.0   # the P99.9+1.5 clamp
# C1/Vault V3: the anti-poisoning guard — baseline growth beyond 3x the
# 30-day median requires two-person attestation (the attestation UI is the
# ops lane's; this module exposes the machine-readable signal AND the
# attestation's effect, rebase_baseline).
#
# Implementation note (RFC gap, found building this): V3 as written is
# circular under a SUSTAINED >k× velocity shift — every tick declares, so
# I-1 freezes B forever and B can never grow 3× to trip the guard. The
# guard therefore watches two ratios: the baseline ratio (slow poisoning
# via non-declaring ticks — the RFC's literal case) and the velocity
# ratio (sustained tick-velocity vs the 30-day B median — the trap case).
# Either trips attestation; the attestation's effect is rebase_baseline
# (operator-approved), which lets B learn the new normal.
BASELINE_GROWTH_ATTEST_RATIO = 3.0
BASELINE_DAILY_SAMPLES = 40         # daily B samples ring (30d + headroom)
TICK_W_SAMPLES = 7 * 24 * 60        # trailing-7d tick-velocity ring
K_REVIEW_CLOCK_S = 30 * 86400       # k's ADR-022/B3 30-day review clock
# C1/RFC §4.1 M1: per-fingerprint hourly counts for the digest's
# level-shift z-score — bounded LRU reusing the correlator's own
# per-fingerprint state (no new state system).
FP_HOURLY_BUCKETS = 25              # 24h of history + the current hour
FP_HOURLY_FP_CAP = 50_000           # fingerprints tracked; oldest evicted


@dataclass
class LabelSnapshot:
    """A vintage-stamped label set from the label pipeline.

    labels: the label mapping the correlator would consume.
    version: the pipeline version that produced it (drift detection).
    labels_as_of: epoch seconds — the pipeline's knowledge clock. Judged
        against LABEL_PIPELINE_SLO_S at ingest; stale snapshots PAGE, they
        are never silently correlated on.
    """
    labels: dict
    version: str
    labels_as_of: float


@dataclass
class HistoryView:
    """Everything the correlator knew about one fingerprint at read time.

    history_as_of: the single clock — the knowledge cutoff of this read.
        Every input (past alerts, episodes, labels) shares this T.
    observed_72h / last_observed_ts: whether/when an alert for this
        fingerprint was ingested inside the 72h window.
    episode: the episode record, if it has activity inside 72h, else None.
        Records older than the bound are pruned — they get no vote.
    novel: True when the fingerprint has zero observations in 72h. Novelty
        is flag-only: it never suppresses, never delays paging.
    """
    fingerprint: str
    history_as_of: float
    observed_72h: bool
    last_observed_ts: float | None
    episode: Episode | None
    novel: bool


@dataclass
class Page:
    """A structured, loud page — emitted when the label pipeline is stale
    beyond the SLO. severity is always "page": staleness pages, it never
    degrades into a log line someone reads tomorrow."""
    severity: str  # always "page"
    reason: str    # "label_pipeline_stale"
    fingerprint: str
    service: str
    detail: str
    raised_at: float


@dataclass
class NoveltyShadowEvent:
    """What the novelty rule WOULD have done, recorded without doing it.

    fingerprint: the novel fingerprint.
    history_as_of: the single clock behind the shadow decision.
    would_do: "flag_review" — the live equivalent (novel=True) is flag-only.
    """
    fingerprint: str
    history_as_of: float
    would_do: str = "flag_review"


def _is_p1p2(alert: Alert) -> bool:
    """True when the source severity label puts this alert in the P1/P2
    class (critical/high). DR-13: these never enter change-window queuing."""
    return (alert.severity_in or "").strip().lower() in _P1P2_SEVERITIES


def fingerprint_for(service: str, check: str, severity_in: str, region: str,
                    *, env: str, cluster: str) -> str:
    """Scheme-v2 fingerprint: first 16 chars of
    sha256("service|check|severity_in|region|env|cluster").

    env/cluster are REQUIRED keyword-only — no call site may silently mint
    an env-blind hash. An empty string is a real namespace ("unknown"), never
    a wildcard: two alerts that both lack env still dedup together, and that
    is visible, not accidental.
    """
    key = f"{service}|{check}|{severity_in}|{region}|{env}|{cluster}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def legacy_fingerprint_for(service: str, check: str, severity_in: str,
                           region: str) -> str:
    """Scheme-v1 fingerprint (env-blind). MIGRATION-ONLY: used solely to
    resolve pre-migration allowlist entries during the bounded migration
    window (see docs/fingerprint-migration-adr017.md). Never use for new
    fingerprints. Target removal: after the migration window closes."""
    key = f"{service}|{check}|{severity_in}|{region}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def fingerprint_of(alert: Alert) -> str:
    labels = alert.labels or {}
    return fingerprint_for(alert.service, alert.check, alert.severity_in,
                           labels.get("region", ""),
                           env=labels.get("env", ""),
                           cluster=labels.get("cluster", ""))


def legacy_fingerprint_of(alert: Alert) -> str:
    """The v1 fingerprint this alert *would* have had pre-migration.

    Used only for dual-resolution during the migration window: an alert whose
    v2 fingerprint misses the allowlist may still match a carried-over v1
    entry (loudly logged). Never used for new writes.
    """
    labels = alert.labels or {}
    return legacy_fingerprint_for(alert.service, alert.check,
                                  alert.severity_in, labels.get("region", ""))


@dataclass
class Episode:
    """One incident's lifecycle, keyed by fingerprint (ADR-001).

    state: "open" | "closed". Episodes never auto-close on silence — they
    leave "open" only via resolve_episode(). (Identity expires: records are
    pruned after 72h without re-fire, but pruning deletes the record — it
    never flips state to "closed".)
    flap_count: visible relapse count — incremented each time a CLOSED
    episode re-fires. Never derived from, and never promotes, severity.
    """
    state: str
    flap_count: int
    opened_ts: float
    last_alert_ts: float
    closed_ts: float | None = None
    close_reason: str | None = None


@dataclass
class CorrelationResult:
    kind: str  # "new" | "duplicate" | "storm" | "change_window" | "reopened" |
               # "label_stale" (D7: label pipeline beyond its freshness SLO —
               # page the incident AND the pipeline owner; never suppress)
    fingerprint: str
    # Duplicates: the prior disposition to inherit (no Jev call).
    prior: Disposition | None = None
    # Storms: True on the ingest that declares the storm; False for later
    # alerts folded into an active storm.
    storm_declared: bool = False
    # Storms: service -> count of distinct fingerprints seen in the storm window.
    storm_counts: dict = field(default_factory=dict)
    # ADR-001: visible episode state for the operator. flap_count is the
    # relapse count (0 for a fresh episode); episode_state is
    # "open" | "closed" | None (None for change_window / storm folds, which
    # are pre-episode classifications).
    flap_count: int = 0
    episode_state: str | None = None
    # D7 — history provenance. history_as_of is the single clock behind this
    # decision: the knowledge cutoff of the history read that informed it.
    history_as_of: float = 0.0
    # D7 novelty. novel: live-mode flag — fingerprint unseen in 72h.
    # Flag-only: never suppresses, never delays paging. novel_shadow: the
    # same signal computed in shadow mode — observability, zero behavior.
    novel: bool = False
    novel_shadow: bool = False
    # D7 label-pipeline SLO. label_stale: the snapshot was beyond the SLO.
    # page_pipeline: a Page was emitted for the pipeline owner. labels_from:
    # "alert" (intrinsic labels; current behavior) | "label_pipeline".
    label_stale: bool = False
    page_pipeline: bool = False
    labels_from: str = "alert"


class Correlator:
    """In-memory dedup / storm / change-window triage + episode lifecycle.

    change_windows: list of dicts, each:
        {"service": "<name>" | "*", "start": "<ISO-8601>", "end": "<ISO-8601>"}
      A window matches when the alert's service equals "service" (or "*")
      and start <= alert.received_at <= end. Unparseable entries never match
      (fail open: no suppression on bad config).

    Sentinel-side windows ONLY: PagerDuty-side maintenance windows are
    invisible to the receiver (see docs/ONBOARDING.md). DR-13 reconciliation:
    P1/P2 (critical/high) alerts never match a change window — they bypass
    queuing and go through the full evaluation path immediately.

    The pipeline must call note_disposition() after the gate runs so that
    later duplicates can inherit the prior disposition, and must call
    resolve_episode() when the operator resolves the episode or a verified
    resolve signal arrives. Silence never closes an episode — there is no
    other close path.
    """

    def __init__(
        self,
        window_s: int = 300,
        storm_fingerprints: int = 20,
        storm_window_s: int = 60,
        change_windows: list[dict] | None = None,
        clock=None,
        # C1 — adaptive storm threshold (design/rfc-c1-stepped-failopen.md
        # §2). storm_fingerprints is now the absolute FLOOR F (default 20);
        # storm_k is the velocity-ratio multiplier (default 5, Type 2).
        storm_k: float = STORM_K_DEFAULT,
        # D7 — history provenance (design/d7-history-provenance.md). All
        # additive, all reversible. novelty_mode "shadow" (default) computes
        # the novelty signal without affecting dispositions; "live" sets
        # result.novel (flag-only, never suppresses). page_sink receives
        # Page records when the label pipeline breaches its freshness SLO.
        history_window_s: int = HISTORY_WINDOW_S,
        label_slo_s: int = LABEL_PIPELINE_SLO_S,
        novelty_mode: str = "shadow",
        page_sink=None,
        page_cooldown_s: int = PAGE_COOLDOWN_S,
    ):
        if novelty_mode not in ("shadow", "live"):
            raise ValueError(
                f"unknown novelty_mode {novelty_mode!r}; "
                'expected "shadow" or "live"')
        self.window_s = window_s
        self.storm_fingerprints = storm_fingerprints  # C1: the floor F
        self.storm_window_s = storm_window_s
        if not (STORM_K_MIN <= storm_k <= STORM_K_MAX):
            raise ValueError(
                f"storm_k {storm_k} outside the RFC clamp "
                f"[{STORM_K_MIN}, {STORM_K_MAX}]")
        self._storm_k = float(storm_k)
        self.change_windows = list(change_windows or [])
        self._now = clock or time.time
        self._k_set_at = self._now()  # the 30-day review clock starts here
        self.history_window_s = history_window_s
        self.label_slo_s = label_slo_s
        self.novelty_mode = novelty_mode
        self._page_sink = page_sink
        self.page_cooldown_s = page_cooldown_s
        self._lock = threading.Lock()
        # fingerprint -> {"first_seen": float, "last_seen": float, "prior": Disposition|None}
        self._seen: dict[str, dict] = {}
        # C1: insertion-ordered (last_seen, fp) for O(1)-amortized expiry
        # of _seen — same quadratic-avoidance as _observed_order (the
        # 300s window holds ~5k entries at 1k/min; the scan-per-ingest
        # would dominate at burst rates).
        self._seen_order: deque = deque()
        # fingerprint -> Episode (ADR-001 lifecycle)
        self._episodes: dict[str, Episode] = {}
        # C1: insertion-ordered (last_alert_ts, fp) for O(1)-amortized
        # episode expiry — same quadratic-avoidance as _observed_order.
        self._episodes_order: deque = deque()
        # fingerprint -> service name (for storm counts by service)
        self._fp_service: dict[str, str] = {}
        # D7: fingerprint -> last ingest timestamp. The "past alerts" history
        # input: when was this fingerprint last SEEN (ingested), regardless
        # of how it was classified (change_window / storm folds never touch
        # the episode records, but they are still observations). Pruned past
        # the 72h bound — older observations get no vote.
        self._observed: dict[str, float] = {}
        # C1: insertion-ordered (ts, fp) for O(1)-amortized expiry of
        # _observed. The 72h window holds ~4.3M entries at 1k/min — a
        # full dict scan per ingest (the old _prune) is quadratic and a
        # production cliff on the storm path (found via the C1 replay
        # tests: 20k ingests took 27s). Semantics identical: an entry
        # expires iff its recorded ts is past the bound.
        self._observed_order: deque = deque()
        # D7: shadow-mode novelty evidence (inspectable; promotion gate input).
        # A deque (not a list): _prune expires it in O(1)-amortized time —
        # the per-ingest list scan was quadratic at replay scale.
        self._shadow_log: deque = deque()
        # D7: (reason, service) -> last page emission ts (anti-fatigue).
        self._last_page: dict[tuple, float] = {}
        # sliding window of (timestamp, fingerprint) for storm detection
        self._recent: deque = deque()
        # storm state
        self._storm_active_until: float = 0.0
        self._storm_counts: dict[str, int] = {}
        # C1 — adaptive baseline state (RFC §2.1). B(t) is the EWMA of the
        # 60s-distinct measure. I-1's freeze is structural: during an
        # active storm every ingest folds at step 3, so no tick ever runs
        # mid-storm (the detector cannot learn from the thing it is
        # detecting because the drain never lets the spike into _recent).
        # The explicit active-window check below is belt-and-suspenders.
        self._storm_baseline: float = 0.0
        self._baseline_last_tick: float = self._now()
        # C1/R15: cold-start bootstrap state. _bootstrap_until marks the
        # end of the observation window; _bootstrapped flips once we've
        # attempted (successfully or not) so we don't retry forever.
        # _bootstrap_decls records (ts, w) at each storm declaration
        # during bootstrap — the direct velocity observations (tick_w is
        # sparse during storms because folds never reach the tick).
        self._bootstrap_until: float = self._now() + BOOTSTRAP_WINDOW_S
        self._bootstrapped: bool = False
        self._bootstrap_decls: list = []
        # C1/R15: true distinct-velocity sampling during bootstrap.
        # Declaration w values are always ~threshold (we declare as soon
        # as w >= threshold), so they don't reveal the true velocity.
        # Instead, track distinct fingerprints per 60s directly — even
        # folded alerts count (they're ingested before the storm check).
        self._bootstrap_fps: set = set()
        self._bootstrap_vels: list = []  # distinct/60s samples
        self._bootstrap_last_reset: float = self._now()
        self._last_declared_ts: float | None = None
        # C1/Vault V3 — daily B samples for the anti-poisoning growth guard.
        self._baseline_daily: deque = deque()  # (day_epoch, B)
        # The last attested baseline (via rebase_baseline): the growth
        # guard measures FURTHER 3x growth from the attested value, not
        # from pre-attestation history — the attestation moves the
        # reference, it doesn't just silence the alarm once.
        self._attested_baseline: float | None = None
        # Trailing-7d tick-velocity samples for the V3 velocity leg (see
        # the BASELINE_GROWTH_ATTEST_RATIO note above).
        self._tick_w: deque = deque()  # (ts, peak-W)
        self._w_peak_since_tick: int = 0  # max W observed this interval
        self._growth_3x_since: float | None = None
        # C1/RFC §4.1 M1 — per-fingerprint hourly counts for the digest's
        # level-shift z-score. fp -> deque[(hour_epoch, count)], bounded.
        self._fp_hourly: dict[str, deque] = {}

    # ------------------------------------------------------------------ API

    def ingest(self, alert: Alert,
               label_snapshot: LabelSnapshot | None = None) -> CorrelationResult:
        """Classify one alert. Never calls Jev.

        D7: every ingest reads history ONCE, under one clock, via
        read_history(). The returned result carries `history_as_of` — the
        knowledge cutoff behind the decision.

        label_snapshot: the label pipeline's current vintage (may be None).
        When present and older than label_slo_s, the correlator PAGES
        (Page to page_sink, kind="label_stale") — rotten labels never get a
        silent vote. When absent, the alert's intrinsic labels are used
        (current behavior; labels_from="alert" marks it).
        """
        fp = fingerprint_of(alert)
        now = self._now()
        with self._lock:
            # C1/R15: cold-start bootstrap sampling — track distinct
            # velocity directly (before any storm folding).
            if not self._bootstrapped:
                if now - self._bootstrap_last_reset >= 60.0:
                    if self._bootstrap_fps:
                        self._bootstrap_vels.append(
                            len(self._bootstrap_fps))
                    self._bootstrap_fps = set()
                    self._bootstrap_last_reset = now
                self._bootstrap_fps.add(fp)
            # C1/R15: cold-start bootstrap check — runs on EVERY ingest
            # (not just ticks), because sustained storms bypass the tick.
            if not self._bootstrapped and now >= self._bootstrap_until:
                self._bootstrapped = True
                self._maybe_bootstrap_baseline(now)
            self._prune(now)
            # D7: the ONE history read for this ingest, under the single
            # clock. Computed BEFORE recording this alert — novelty is about
            # what we knew before this alert arrived.
            view = self._read_history_locked(fp, now)
            # Seen is seen: this alert is an observation regardless of how
            # it classifies below (change_window / storm folds included).
            self._observed[fp] = now
            self._observed_order.append((now, fp))
            # C1/RFC §4.1 M1: per-fingerprint hourly count for the digest's
            # level-shift z-score (bounded LRU; every ingest counts).
            self._note_fp_count(fp, now)
            # D7: label-pipeline freshness SLO — stale labels PAGE, they are
            # never silently correlated on.
            stale, snapshot = self._check_label_freshness(label_snapshot, now)
            if stale:
                age = now - snapshot.labels_as_of
                page_emitted = self._emit_page_locked(Page(
                    severity="page",
                    reason="label_pipeline_stale",
                    fingerprint=fp,
                    service=alert.service,
                    detail=(f"label pipeline {snapshot.version!r} is "
                            f"{age:.0f}s old (SLO {self.label_slo_s}s) — "
                            f"correlating on rotten labels is fail-silent"),
                    raised_at=now,
                ))
                return CorrelationResult(
                    kind="label_stale", fingerprint=fp,
                    history_as_of=now, label_stale=True,
                    page_pipeline=page_emitted,
                    labels_from="label_pipeline",
                )
            res = self._classify_locked(alert, fp, now)
            res.history_as_of = now
            res.labels_from = ("label_pipeline" if snapshot is not None
                               else "alert")
            # D7 novelty: shadow first. In shadow mode the signal is logged
            # and DOES NOT affect the disposition; in live mode it sets the
            # flag-only novel marker (never suppresses, never delays).
            if view.novel:
                if self.novelty_mode == "live":
                    res.novel = True
                else:
                    self._shadow_log.append(NoveltyShadowEvent(
                        fingerprint=fp, history_as_of=now,
                        would_do="flag_review"))
                    res.novel_shadow = True
            return res

    def read_history(self, fingerprint: str) -> HistoryView:
        """D7: the ONE history read path. Returns everything the correlator
        knows about a fingerprint at the read time, under the single clock
        (history_as_of). Nothing older than history_window_s (72h) is
        returned — older history gets no vote."""
        now = self._now()
        with self._lock:
            self._prune(now)
            return self._read_history_locked(fingerprint, now)

    def shadow_log(self) -> list[NoveltyShadowEvent]:
        """D7: the recorded shadow-mode novelty evidence (promotion gate
        input). Returns copies — the log itself is append-only."""
        with self._lock:
            return [replace(e) for e in self._shadow_log]

    def promote_novelty_to_live(self,
                                min_shadow_events: int = NOVELTY_SHADOW_MIN_EVENTS
                                ) -> int:
        """D7: promote the novelty rule from shadow to live — gated on
        evidence. Raises ValueError when fewer than the required shadow
        events have been recorded. The floor is NOVELTY_SHADOW_MIN_EVENTS
        (100): passing a smaller min_shadow_events does NOT lower it —
        the parameter can only raise the bar, never lower it. (The ≥95%
        spot-audit agreement and the operator sign-off are process gates
        recorded in the decision log; this is the in-code minimum-evidence
        gate.)"""
        with self._lock:
            floor = max(min_shadow_events, NOVELTY_SHADOW_MIN_EVENTS)
            n = len(self._shadow_log)
            if n < floor:
                raise ValueError(
                    f"novelty promotion blocked: {n} shadow events < "
                    f"{floor} required — run longer in shadow")
            self.novelty_mode = "live"
            return n

    def _classify_locked(self, alert: Alert, fp: str,
                         now: float) -> CorrelationResult:
        """The deterministic classification pipeline (dedup / storm /
        change-window / episode). Caller must hold the lock; caller applies
        D7 provenance fields (history_as_of, novelty) to the result."""
        # 1. Change window — deterministic config beats everything,
        #    EXCEPT P1/P2 (DR-13 reconciliation: a P1/P2 queued to
        #    business hours is a silenced P1/P2 with extra steps).
        if self._in_change_window(alert) and not _is_p1p2(alert):
            return CorrelationResult(kind="change_window", fingerprint=fp)
        # 2. Duplicate — same fingerprint inside the dedup window of an
        #    open episode. Inherits the prior disposition (no Jev call).
        ep = self._episodes.get(fp)
        fresh = ep is None or now - ep.last_alert_ts > EPISODE_FRESHNESS_S
        if ep is not None and ep.state == "open" and not fresh:
            entry = self._seen.get(fp)
            if entry is not None and now - entry["first_seen"] <= self.window_s:
                entry["last_seen"] = now
                self._seen_order.append((now, fp))
                return CorrelationResult(
                    kind="duplicate", fingerprint=fp, prior=entry["prior"],
                    flap_count=ep.flap_count, episode_state="open",
                )
        # 3. Active storm — fold in. A declared storm never re-evaluates
        #    a fingerprint and never reopens an episode: the aggregate
        #    digest carries the page (one-call-per-storm, DR-14). The
        #    episode record is untouched — the flap will surface as a
        #    reopen when the storm clears and the fingerprint fires again.
        if now < self._storm_active_until:
            return CorrelationResult(
                kind="storm", fingerprint=fp, storm_declared=False
            )
        # 4. Episode outcome.
        if fresh:
            # No episode, or the fingerprint hasn't been seen in >72h:
            # FRESH episode. The old record's flap count is stale —
            # treat the alert as brand new.
            ep = Episode(state="open", flap_count=0,
                         opened_ts=now, last_alert_ts=now)
            self._episodes[fp] = ep
            self._episodes_order.append((now, fp))
            kind = "new"
        elif ep.state == "closed":
            # Flap-reopen: the closed episode's fingerprint fired again.
            # flap_count += 1 is VISIBLE to the operator; the episode
            # reopens but NEVER auto-promotes severity or disposition —
            # the fresh alert goes through the full gate evaluation, and
            # the closed episode's prior disposition is discarded so no
            # stale decision leaks across the resolve boundary.
            ep.state = "open"
            ep.flap_count += 1
            ep.last_alert_ts = now
            self._episodes_order.append((now, fp))
            ep.closed_ts = None
            ep.close_reason = None
            seen = self._seen.get(fp)
            if seen is not None:
                seen["prior"] = None
            kind = "reopened"
        else:
            ep.last_alert_ts = now
            self._episodes_order.append((now, fp))
            kind = "new"
        # 5. Storm — fold into an active storm (handled above), or
        #    declare a new one. New AND reopened alerts count: a burst
        #    of flapping fingerprints IS a storm.
        #
        #    C1 (RFC §2.1): the declaration rule is estate-relative —
        #    storm declares iff W(t) >= max(F, k*B(t)). W is the distinct
        #    first-seen count in the trailing 60s window (same measure as
        #    before); B is the adaptive baseline, ticked BEFORE this alert
        #    appends so the detector never learns from the alert it is
        #    judging. I-2: declaration changes WHEN, never WHAT — the fold
        #    path below is untouched (deterministic, pre-Jev, folded-not-
        #    suppressed per D3).
        self._maybe_tick_baseline(now)
        self._recent.append((now, fp))
        distinct = {f for _, f in self._recent}
        w = len(distinct)
        # Peak-W for the V3 velocity leg: the max 60s-distinct observed
        # this tick interval. (The tick fires on the first ingest after
        # 60s — often the first post-drain lull ingest — so the
        # instantaneous W there is systematically low; the peak is the
        # honest interval velocity.)
        if w > self._w_peak_since_tick:
            self._w_peak_since_tick = w
        threshold = max(float(self.storm_fingerprints),
                        self._storm_k * self._storm_baseline)
        if w >= threshold:
            # Declare the storm: snapshot counts by service, open the window.
            self._storm_active_until = now + self.storm_window_s
            self._last_declared_ts = now
            # C1/R15: record declaration velocity during bootstrap (the
            # tick is sparse during storms; declarations are the direct
            # observations).
            if not self._bootstrapped and now < self._bootstrap_until:
                self._bootstrap_decls.append((now, w))
            counts: dict[str, int] = {}
            for f in distinct:
                svc = self._fp_service.get(f, "unknown")
                counts[svc] = counts.get(svc, 0) + 1
            self._storm_counts = counts
            return CorrelationResult(
                kind="storm",
                fingerprint=fp,
                storm_declared=True,
                storm_counts=dict(counts),
                flap_count=ep.flap_count,
                episode_state="open",
            )
        # 6. Record the observation.
        self._seen[fp] = {"first_seen": now, "last_seen": now, "prior": None}
        self._seen_order.append((now, fp))
        self._fp_service[fp] = alert.service
        return CorrelationResult(kind=kind, fingerprint=fp,
                                 flap_count=ep.flap_count,
                                 episode_state="open")

    def note_disposition(self, fingerprint: str, disposition: Disposition) -> None:
        """Record the gate's disposition so later duplicates can inherit it."""
        with self._lock:
            entry = self._seen.get(fingerprint)
            if entry is not None:
                entry["prior"] = disposition

    def resolve_episode(self, fingerprint: str, *, reason: str) -> bool:
        """Close an episode — the ONLY close path besides nothing.

        reason must be "operator_resolve" (explicit operator action) or
        "verified_resolve" (an authenticated resolve signal from the source
        — e.g. a signed PagerDuty resolve webhook; an unsigned "resolve"
        never counts, and the absence of alerts never closes).

        Returns True when a live episode was closed, False when there was
        no open episode for the fingerprint. Raises ValueError on an
        unknown reason (a close must name its authority).
        """
        if reason not in CLOSE_REASONS:
            raise ValueError(
                f"unknown episode close reason {reason!r}; "
                f"expected one of {CLOSE_REASONS}")
        now = self._now()
        with self._lock:
            ep = self._episodes.get(fingerprint)
            if ep is None or ep.state == "closed":
                return False
            ep.state = "closed"
            ep.closed_ts = now
            ep.close_reason = reason
            # The dedup record's prior belonged to the closed episode:
            # discard it so a late duplicate can never resurrect a stale
            # disposition.
            seen = self._seen.get(fingerprint)
            if seen is not None:
                seen["prior"] = None
            return True

    def get_episode(self, fingerprint: str) -> Episode | None:
        """Read-only snapshot of an episode (operator visibility / tests).
        Returns None when no episode record exists."""
        with self._lock:
            ep = self._episodes.get(fingerprint)
            return None if ep is None else replace(ep)

    # ------------------------------------------------- C1 adaptive detector

    def _maybe_tick_baseline(self, now: float) -> None:
        """Tick the adaptive baseline B(t) (RFC §2.1).

        Called from the storm-evaluation step BEFORE the current alert
        appends to the window: the tick measures the estate's ambient
        60s-distinct velocity, never the alert under judgment. Ticks at
        most every STORM_BASELINE_TICK_S (60s); the update is the EWMA
        with 6h half-life. Caller must hold the lock.

        I-1 (Type 1) is structural here: while a storm is active every
        ingest folds at step 3 and never reaches this method, so no tick
        can learn the drained storm window. The explicit check is
        belt-and-suspenders for future callers.
        """
        if now - self._baseline_last_tick < STORM_BASELINE_TICK_S:
            return
        self._baseline_last_tick = now
        # The ambient 60s-distinct velocity — recorded on EVERY tick
        # evaluation (frozen or not) for the V3 velocity leg. Frozen ticks
        # skip only the B update, never the observation. The sample is
        # the interval PEAK (see the peak-W note at the storm check):
        # the instantaneous W at tick-fire time is biased low by drains.
        w = len({f for _, f in self._recent})
        self._tick_w.append((now, self._w_peak_since_tick))
        self._w_peak_since_tick = 0
        while len(self._tick_w) > TICK_W_SAMPLES:
            self._tick_w.popleft()
        # I-1 (Type 1), time-bounded: for STORM_BASELINE_FROZEN_S after
        # each declaration, B does not update. The storm's folds drain
        # the window — learning the drained w would erode the baseline,
        # and learning the rebuild would adapt to the shift without
        # attestation. The V3 attestation + rebase is the controlled
        # adaptation path for sustained shifts. (A single-tick skip
        # halves the learning rate under repeated declarations; a time
        # bound freezes continuously through the declare/fold cycle.)
        if (self._last_declared_ts is not None
                and now - self._last_declared_ts < STORM_BASELINE_FROZEN_S):
            return
        alpha = 1.0 - 2.0 ** (-STORM_BASELINE_TICK_S
                              / STORM_BASELINE_HALF_LIFE_S)
        self._storm_baseline += alpha * (w - self._storm_baseline)
        self._sample_baseline_daily(now)

    def _maybe_bootstrap_baseline(self, now: float) -> None:
        """R15: cold-start baseline bootstrap (caller holds the lock).

        After BOOTSTRAP_WINDOW_S with B still ~0, learn B from the true
        distinct velocity observed during bootstrap — a sustained high
        velocity means the baseline is too low (the C2 cliff on a fresh
        correlator). Loudly logged (this is a trust decision).

        Discrimination (busy estate vs incident) via window thirds:
        - Velocity high throughout all thirds → busy estate → bootstrap
          to median velocity.
        - Velocity low in early thirds, high late → new spike (incident)
          → refuse; manual rebase via attestation.
        - Few samples (< 5) → not enough signal; leave B=0.
        - Median velocity < floor F → quiet estate; B=0 is correct.
        """
        if self._storm_baseline > 0.01:
            return  # already learned or attested; nothing to do
        vels = self._bootstrap_vels
        if len(vels) < 5:
            logger.warning("storm baseline bootstrap: only %d velocity "
                           "samples in window; B stays 0", len(vels))
            return
        window_start = self._bootstrap_until - BOOTSTRAP_WINDOW_S
        # Assign samples to thirds by their sample time. Samples are
        # recorded every 60s; reconstruct times from the end backwards.
        third = BOOTSTRAP_WINDOW_S / 3
        n = len(vels)
        # vels[0] is oldest. Map index to time: oldest ≈ window_start.
        thirds = [[], [], []]
        for i, v in enumerate(vels):
            # Approximate: spread evenly across the window.
            ts = window_start + (i + 0.5) * (BOOTSTRAP_WINDOW_S / n)
            idx = min(2, int((ts - window_start) / third))
            thirds[idx].append(v)
        # Busy estate: velocity present in early thirds.
        early = sorted(thirds[0] + thirds[1])
        late = sorted(thirds[2])
        if not early or not late:
            logger.warning("storm baseline bootstrap: insufficient third "
                           "coverage; B stays 0")
            return
        early_med = early[len(early) // 2]
        late_med = late[len(late) // 2]
        if early_med < float(self.storm_fingerprints) and \
                late_med >= float(self.storm_fingerprints):
            logger.warning(
                "storm baseline bootstrap REFUSED: velocity low early "
                "(median %.0f) then high late (median %.0f) — likely "
                "incident in progress; manual rebase via attestation "
                "required", early_med, late_med)
            return
        median = sorted(vels)[len(vels) // 2]
        if median < float(self.storm_fingerprints):
            return  # quiet; B=0 (threshold=F) is correct
        self._storm_baseline = float(median)
        self._sample_baseline_daily(now)
        logger.warning(
            "storm baseline BOOTSTRAPPED to %.0f from %d velocity samples "
            "(cold start; V3 guard now watches 3x growth from here)",
            median, len(vels))

    def _sample_baseline_daily(self, now: float) -> None:
        """One B sample per UTC day for the V3 anti-poisoning guard."""
        day = int(now // 86400)
        if self._baseline_daily and self._baseline_daily[-1][0] == day:
            self._baseline_daily[-1] = (day, self._storm_baseline)
        else:
            self._baseline_daily.append((day, self._storm_baseline))
        while len(self._baseline_daily) > BASELINE_DAILY_SAMPLES:
            self._baseline_daily.popleft()

    def detector_health(self) -> dict:
        """Pager P1: the detector's judgment for the violet banner —
        W(t), the adaptive threshold and its parts, last declaration.
        Read-only; safe to call from the gate's banner path."""
        now = self._now()
        with self._lock:
            cutoff = now - self.storm_window_s
            w = len({f for ts, f in self._recent if ts >= cutoff})
            threshold = max(float(self.storm_fingerprints),
                            self._storm_k * self._storm_baseline)
            samples = sorted(b for _, b in self._baseline_daily)
            median = samples[len(samples) // 2] if samples else 0.0
            return {
                "w_distinct_60s": w,
                "threshold": threshold,
                "floor_f": float(self.storm_fingerprints),
                "k": self._storm_k,
                "baseline_b": self._storm_baseline,
                "last_declared_ts": self._last_declared_ts,
                "storm_active": now < self._storm_active_until,
                "baseline_frozen": (
                    self._last_declared_ts is not None
                    and now - self._last_declared_ts
                    < STORM_BASELINE_FROZEN_S),
                # V3: the growth guard rides along so the banner can nudge
                # the operator toward attestation when the estate outgrows
                # its baseline.
                "baseline_median_30d": median,
                "growth_attestation_required":
                    self._growth_3x_since is not None,
            }

    def set_storm_k(self, k: float, *, now: float | None = None) -> None:
        """Retune the velocity-ratio multiplier (Type 2 — but
        safety-affecting: manual changes require two-person attestation
        per ADR-022/B3; this method logs loudly so the attestation has
        something to point at). Restarts k's 30-day review clock."""
        if not (STORM_K_MIN <= k <= STORM_K_MAX):
            raise ValueError(
                f"storm_k {k} outside the RFC clamp "
                f"[{STORM_K_MIN}, {STORM_K_MAX}]")
        t = now if now is not None else self._now()
        with self._lock:
            old = self._storm_k
            self._storm_k = float(k)
            self._k_set_at = t
        logger.warning(
            "storm_k changed %.2f -> %.2f (safety-affecting per ADR-022/B3: "
            "requires two-person attestation; 30-day review clock restarted)",
            old, k)

    def k_review_due(self, now: float | None = None) -> bool:
        """True when k's 30-day review clock (ADR-022/B3) has elapsed."""
        t = now if now is not None else self._now()
        with self._lock:
            return t - self._k_set_at > K_REVIEW_CLOCK_S

    def baseline_growth_check(self, now: float | None = None) -> dict:
        """Vault V3 anti-poisoning signal: growth beyond 3x the 30-day
        median requires two-person attestation (the attestation UI is the
        ops lane's — this is the machine-readable signal it gates on;
        the attestation's effect is rebase_baseline()).

        Two legs (see the BASELINE_GROWTH_ATTEST_RATIO note): the baseline
        leg (slow poisoning via non-declaring ticks) and the velocity leg
        (sustained tick-velocity vs the baseline median — the trap where
        I-1's freeze would otherwise pin B forever under a sustained
        >k× shift). Either leg trips the requirement.
        """
        t = now if now is not None else self._now()
        with self._lock:
            samples = sorted(b for _, b in self._baseline_daily)
            median = samples[len(samples) // 2] if samples else 0.0
            # The attestation moves the guard's reference: growth is
            # measured from max(history, last attested baseline).
            ref = median
            if self._attested_baseline is not None:
                ref = max(ref, self._attested_baseline)
            ratio_b = (self._storm_baseline / ref
                       if ref > 0 else 0.0)
            cutoff = t - 86400
            recent_w = sorted(w for ts, w in self._tick_w if ts >= cutoff)
            med_w = (recent_w[len(recent_w) // 2] if recent_w else 0.0)
            ratio_w = med_w / ref if ref > 0 else 0.0
            ratio = max(ratio_b, ratio_w)
            if ratio > BASELINE_GROWTH_ATTEST_RATIO:
                if self._growth_3x_since is None:
                    self._growth_3x_since = t
            else:
                self._growth_3x_since = None
            return {
                "growth_ratio": ratio,
                "baseline_ratio": ratio_b,
                "velocity_ratio": ratio_w,
                "median_30d": median,
                "attested_baseline": self._attested_baseline,
                "baseline_b": self._storm_baseline,
                "attestation_required":
                    self._growth_3x_since is not None,
                "first_exceeded_at": self._growth_3x_since,
            }

    def rebase_baseline(self, new_b: float, *, reason: str,
                        now: float | None = None) -> None:
        """The V3 attestation's effect: operator-approved baseline reset.

        Safety-affecting (it changes what fraction of traffic reaches the
        proof engine): the two-person attestation is process, but this
        method is the mechanism — it logs loudly so the attestation has
        something to point at, and records the new B in the daily ring so
        the growth guard measures from the approved value.
        """
        if new_b < 0:
            raise ValueError("baseline cannot be negative")
        t = now if now is not None else self._now()
        with self._lock:
            old = self._storm_baseline
            self._storm_baseline = float(new_b)
            self._attested_baseline = float(new_b)
            self._growth_3x_since = None
            self._sample_baseline_daily(t)
        logger.warning(
            "storm baseline rebased %.1f -> %.1f (attested: %s)",
            old, new_b, reason)

    # --------------------------------------- C1 per-fingerprint level shift

    def _note_fp_count(self, fp: str, now: float) -> None:
        """Record one arrival in the fingerprint's hourly count ring
        (caller must hold the lock). Bounded: FP_HOURLY_BUCKETS per
        fingerprint, FP_HOURLY_FP_CAP fingerprints (oldest evicted)."""
        hour = int(now // 3600)
        dq = self._fp_hourly.get(fp)
        if dq is None:
            if len(self._fp_hourly) >= FP_HOURLY_FP_CAP:
                self._fp_hourly.pop(next(iter(self._fp_hourly)))
            dq = deque()
            self._fp_hourly[fp] = dq
        if dq and dq[-1][0] == hour:
            dq[-1] = (hour, dq[-1][1] + 1)
        else:
            dq.append((hour, 1))
            while len(dq) > FP_HOURLY_BUCKETS:
                dq.popleft()

    def fingerprint_level_shift_zscore(self, fingerprint: str,
                                       now: float | None = None) -> float:
        """M1: this fingerprint's current-hour count as a z-score vs its
        own trailing-24h baseline — the level-shift signature the
        pre-mortem's rate-of-change ordering missed (a step function has
        zero derivative after the step; its LEVEL is the anomaly).

        Returns 0.0 when fewer than 3 baseline hours exist (no baseline,
        no vote). Clamped to [-50, 50]. Read-only; the fail-open digest
        calls this for triage ranking (K5 is the drill falsifier; the
        chronological revert is FailopenConfig.digest_ordering).
        """
        t = now if now is not None else self._now()
        cur_hour = int(t // 3600)
        with self._lock:
            dq = self._fp_hourly.get(fingerprint)
            if not dq:
                return 0.0
            hist = [c for h, c in dq if cur_hour - 24 <= h < cur_hour]
            cur = sum(c for h, c in dq if h == cur_hour)
        if len(hist) < 3:
            return 0.0
        mu = sum(hist) / len(hist)
        var = sum((x - mu) ** 2 for x in hist) / len(hist)
        sd = math.sqrt(var)
        z = (cur - mu) / (sd if sd > 1e-9 else 1e-9)
        return max(-50.0, min(50.0, z))

    # -------------------------------------------------------------- internals

    def _read_history_locked(self, fingerprint: str, now: float) -> HistoryView:
        """D7: the single history read. Caller must hold the lock.

        Everything returned is bounded by history_window_s (72h): older
        observations and episode records are invisible here — they were
        pruned, and pruning means they get no vote in this decision.
        """
        cutoff = now - self.history_window_s
        last_ts = self._observed.get(fingerprint)
        observed = last_ts is not None and last_ts >= cutoff
        ep = self._episodes.get(fingerprint)
        ep_in_window = (ep if ep is not None and ep.last_alert_ts >= cutoff
                        else None)
        return HistoryView(
            fingerprint=fingerprint,
            history_as_of=now,
            observed_72h=observed,
            last_observed_ts=last_ts if observed else None,
            episode=(replace(ep_in_window) if ep_in_window is not None
                     else None),
            novel=not observed and ep_in_window is None,
        )

    def _check_label_freshness(self, snapshot: LabelSnapshot | None,
                               now: float
                               ) -> tuple[bool, LabelSnapshot | None]:
        """D7: label-pipeline freshness SLO.

        Returns (stale, snapshot). A missing snapshot is NOT stale —
        absence is visible (labels_from="alert"), not rotten. A present
        snapshot older than label_slo_s is stale: the caller pages and
        refuses to correlate on it.
        """
        if snapshot is None:
            return False, None
        return (now - snapshot.labels_as_of > self.label_slo_s), snapshot

    def _emit_page_locked(self, page: Page) -> bool:
        """D7: emit a Page to the page_sink, with per-(reason, service)
        emission cooldown (anti-fatigue). Returns True when emitted.

        Caller must hold the lock. The correlator owns emission discipline;
        the sink owns delivery dedup. Default sink: loud log line — a page
        must never vanish silently because nobody wired a sink.
        """
        key = (page.reason, page.service)
        last = self._last_page.get(key, 0.0)
        if page.raised_at - last < self.page_cooldown_s:
            return False
        self._last_page[key] = page.raised_at
        sink = self._page_sink
        if sink is None:
            logger.error("SENTINEL-PAGE severity=%s reason=%s service=%s "
                         "fingerprint=%s detail=%s",
                         page.severity, page.reason, page.service,
                         page.fingerprint, page.detail)
        else:
            sink(page)
        return True

    def _prune(self, now: float) -> None:
        cutoff = now - self.storm_window_s
        while self._recent and self._recent[0][0] < cutoff:
            self._recent.popleft()
        # C1: O(1)-amortized expiry via the insertion-ordered deques. An
        # order entry expires its dict entry iff the dict still records
        # the SAME timestamp (a re-observed fingerprint's older order
        # entries are stale markers, not deletions). Identical semantics
        # to the old scans: expire iff now - recorded_ts > window.
        seen_cutoff = now - self.window_s
        while self._seen_order and self._seen_order[0][0] < seen_cutoff:
            _, fp = self._seen_order.popleft()
            e = self._seen.get(fp)
            if e is not None and e["last_seen"] < seen_cutoff:
                del self._seen[fp]
                self._fp_service.pop(fp, None)
        # D7: observation history is bounded by the 72h window. Pruning here
        # is what makes the bound real: a pruned observation cannot make a
        # future alert a duplicate, a flap, or "familiar" — it is gone.
        obs_cutoff = now - self.history_window_s
        while self._observed_order and self._observed_order[0][0] < obs_cutoff:
            ts, fp = self._observed_order.popleft()
            if self._observed.get(fp) == ts:
                del self._observed[fp]
        # Episode records older than the freshness bound are dropped — this
        # is behavior-preserving (a re-fire would start a FRESH episode
        # anyway) and bounds memory. Dropping a record is NOT closing an
        # episode: silence never closes, it only expires identity.
        # C1: O(1)-amortized via the insertion-ordered deque (stale markers
        # for re-fired episodes are skipped, not deleted).
        ep_cutoff = now - EPISODE_FRESHNESS_S
        while self._episodes_order and self._episodes_order[0][0] < ep_cutoff:
            _, fp = self._episodes_order.popleft()
            ep = self._episodes.get(fp)
            if ep is not None and ep.last_alert_ts < ep_cutoff:
                del self._episodes[fp]
        # D7: the novelty shadow log is bounded by the same 72h window —
        # shadow evidence older than the history bound gets no vote in the
        # promotion decision. C1: deque prune, not a per-ingest list scan.
        while self._shadow_log and self._shadow_log[0].history_as_of < obs_cutoff:
            self._shadow_log.popleft()
        if self._storm_active_until and now >= self._storm_active_until:
            self._storm_active_until = 0.0
            self._storm_counts = {}

    def _in_change_window(self, alert: Alert) -> bool:
        received = _parse_iso(alert.received_at)
        if received is None:
            return False  # unparseable timestamp: never suppress
        for window in self.change_windows:
            try:
                svc = window.get("service", "*")
                if svc != "*" and svc != alert.service:
                    continue
                start = _parse_iso(window.get("start", ""))
                end = _parse_iso(window.get("end", ""))
                if start is None or end is None:
                    continue
                if start <= received <= end:
                    return True
            except Exception:
                continue  # bad config entry: fail open
        return False


def _parse_iso(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
