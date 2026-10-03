"""Correlator — fingerprint dedup, storm collapse, change windows (§3.5).

Everything here is deterministic and runs *before* any Jev call, per the
non-negotiable design principle: never pay Jev for deterministic work.

In-memory for v0.1 (Redis in the SaaS upgrade). Thread-safe.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Alert, Disposition


def fingerprint_for(service: str, check: str, severity_in: str, region: str) -> str:
    """Fingerprint = first 16 chars of sha256("service|check|severity_in|region")."""
    key = f"{service}|{check}|{severity_in}|{region}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def fingerprint_of(alert: Alert) -> str:
    region = (alert.labels or {}).get("region", "")
    return fingerprint_for(alert.service, alert.check, alert.severity_in, region)


@dataclass
class CorrelationResult:
    kind: str  # "new" | "duplicate" | "storm" | "change_window"
    fingerprint: str
    # Duplicates: the prior disposition to inherit (no Jev call).
    prior: Disposition | None = None
    # Storms: True on the ingest that declares the storm; False for later
    # alerts folded into an active storm.
    storm_declared: bool = False
    # Storms: service -> count of distinct fingerprints seen in the storm window.
    storm_counts: dict = field(default_factory=dict)


class Correlator:
    """In-memory dedup / storm / change-window triage.

    change_windows: list of dicts, each:
        {"service": "<name>" | "*", "start": "<ISO-8601>", "end": "<ISO-8601>"}
      A window matches when the alert's service equals "service" (or "*")
      and start <= alert.received_at <= end. Unparseable entries never match
      (fail open: no suppression on bad config).

    The pipeline must call note_disposition() after the gate runs so that
    later duplicates can inherit the prior disposition.
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
            # 1. Change window — deterministic config beats everything.
            if self._in_change_window(alert):
                return CorrelationResult(kind="change_window", fingerprint=fp)
            # 2. Duplicate — same fingerprint inside the dedup window.
            entry = self._seen.get(fp)
            if entry is not None and now - entry["first_seen"] <= self.window_s:
                entry["last_seen"] = now
                return CorrelationResult(
                    kind="duplicate", fingerprint=fp, prior=entry["prior"]
                )
            # 3. Storm — fold into an active storm, or declare a new one.
            if now < self._storm_active_until:
                return CorrelationResult(
                    kind="storm", fingerprint=fp, storm_declared=False
                )
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
                )
            # 4. New.
            self._seen[fp] = {"first_seen": now, "last_seen": now, "prior": None}
            self._fp_service[fp] = alert.service
            return CorrelationResult(kind="new", fingerprint=fp)

    def note_disposition(self, fingerprint: str, disposition: Disposition) -> None:
        """Record the gate's disposition so later duplicates can inherit it."""
        with self._lock:
            entry = self._seen.get(fingerprint)
            if entry is not None:
                entry["prior"] = disposition

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
