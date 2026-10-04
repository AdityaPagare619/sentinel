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
    leave "open" only via resolve_episode().
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
    kind: str  # "new" | "duplicate" | "storm" | "change_window" | "reopened"
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
    ):
        self.window_s = window_s
        self.storm_fingerprints = storm_fingerprints
        self.storm_window_s = storm_window_s
        self.change_windows = list(change_windows or [])
        self._now = clock or time.time
        self._lock = threading.Lock()
        # fingerprint -> {"first_seen": float, "last_seen": float, "prior": Disposition|None}
        self._seen: dict[str, dict] = {}
        # fingerprint -> Episode (ADR-001 lifecycle)
        self._episodes: dict[str, Episode] = {}
        # fingerprint -> service name (for storm counts by service)
        self._fp_service: dict[str, str] = {}
        # sliding window of (timestamp, fingerprint) for storm detection
        self._recent: deque = deque()
        # storm state
        self._storm_active_until: float = 0.0
        self._storm_counts: dict[str, int] = {}

    # ------------------------------------------------------------------ API

    def ingest(self, alert: Alert) -> CorrelationResult:
        """Classify one alert. Never calls Jev."""
        fp = fingerprint_of(alert)
        now = self._now()
        with self._lock:
            self._prune(now)
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
                seen = self._seen.get(fp)
                if seen is not None:
                    seen["prior"] = None
                kind = "reopened"
            else:
                ep.last_alert_ts = now
                kind = "new"
            # 5. Storm — fold into an active storm (handled above), or
            #    declare a new one. New AND reopened alerts count: a burst
            #    of flapping fingerprints IS a storm.
            self._recent.append((now, fp))
            distinct = {f for _, f in self._recent}
            if len(distinct) > self.storm_fingerprints:
                # Declare the storm: snapshot counts by service, open the window.
                self._storm_active_until = now + self.storm_window_s
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

    # -------------------------------------------------------------- internals

    def _prune(self, now: float) -> None:
        cutoff = now - self.storm_window_s
        while self._recent and self._recent[0][0] < cutoff:
            self._recent.popleft()
        stale = [
            fp for fp, e in self._seen.items() if now - e["last_seen"] > self.window_s
        ]
        for fp in stale:
            del self._seen[fp]
            self._fp_service.pop(fp, None)
        # Episode records older than the freshness bound are dropped — this
        # is behavior-preserving (a re-fire would start a FRESH episode
        # anyway) and bounds memory. Dropping a record is NOT closing an
        # episode: silence never closes, it only expires identity.
        for fp in [f for f, e in self._episodes.items()
                   if now - e.last_alert_ts > EPISODE_FRESHNESS_S]:
            del self._episodes[fp]
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
