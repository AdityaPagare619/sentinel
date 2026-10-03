"""Load-shedding for the platform tier (Forge's lane).

The pager and the platform are SEPARATE processes on SEPARATE ports. The
pager never waits on the platform and the platform never waits on the
pager — process isolation is the primary bulkhead.

This module is the secondary bulkhead: when the box is hot, the platform
sheds ITS OWN load first, in a fixed order, so dashboard traffic can
never melt the machine out from under the paging receiver.

Shedding order (documented, fixed):
  1. Expensive analytics first: /api/calibration, /api/simulate,
     /api/analytics/* — these scan the whole log / run the bootstrap.
     Under load they 503 with a retryable envelope (the UI shows cached
     data + an honest "degraded" note, never a blank screen).
  2. Admission control: a bounded number of concurrent requests. When the
     slots are full the server fails FAST with 503 + Retry-After instead
     of queueing unboundedly (an unbounded queue is how one dashboard
     stampede becomes a box-wide outage).
  3. Never shed by load: the river (/api/decisions), the detail page,
     and the SSE tail stay up until admission control itself trips.

Two knobs, both environment-overridable:
  SENTINEL_PLATFORM_MAX_INFLIGHT (default 32) — admission bound.
  SENTINEL_PLATFORM_SHED_LOAD    (default 8.0) — 1-min loadavg per CPU
      above which expensive endpoints shed. 8.0 is deliberately high:
      on a shared dev box ambient load can sit at 3-5; shedding must
      mean "the box is actually melting", not "busy". Lower it to
      demonstrate the shed path (fault-injection drills do exactly this).
"""

from __future__ import annotations

import os
import threading

EXPENSIVE_PATHS = (
    "/api/calibration",
    "/api/simulate",
    "/api/analytics/noise",
    "/api/analytics/flips",
)

DEFAULT_MAX_INFLIGHT = int(os.environ.get(
    "SENTINEL_PLATFORM_MAX_INFLIGHT", "32"))
DEFAULT_SHED_LOAD = float(os.environ.get("SENTINEL_PLATFORM_SHED_LOAD",
                                         "8.0"))


def _cpu_count() -> int:
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def load_per_cpu() -> float:
    """1-minute loadavg normalized by CPU count. -1.0 when unreadable."""
    try:
        return os.getloadavg()[0] / _cpu_count()
    except (OSError, AttributeError):
        return -1.0


class AdmissionGate:
    """Bounded concurrency. try_acquire() fails fast — no queues."""

    def __init__(self, max_inflight: int = DEFAULT_MAX_INFLIGHT):
        self.max_inflight = max(1, max_inflight)
        self._sem = threading.BoundedSemaphore(self.max_inflight)
        self._active = 0
        self._lock = threading.Lock()
        self.shed_total = 0  # lifetime sheds (telemetry)

    def try_acquire(self) -> bool:
        ok = self._sem.acquire(blocking=False)
        with self._lock:
            if ok:
                self._active += 1
            else:
                self.shed_total += 1
        return ok

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)
        self._sem.release()

    @property
    def active(self) -> int:
        with self._lock:
            return self._active


class DegradePolicy:
    """Decides, per request, whether the platform sheds its own load.

    should_shed(path) -> (shed: bool, reason: str). The pager is never
    consulted and never affected: this policy only gates platform-tier
    reads.
    """

    def __init__(self, shed_load: float = DEFAULT_SHED_LOAD,
                 expensive_paths=EXPENSIVE_PATHS):
        self.shed_load = shed_load
        self.expensive_paths = tuple(expensive_paths)
        self.shed_total = 0
        self._lock = threading.Lock()

    def is_expensive(self, path: str) -> bool:
        return any(path == p or path.startswith(p + "/")
                   for p in self.expensive_paths)

    def should_shed(self, path: str,
                    load: float | None = None) -> tuple[bool, str]:
        if not self.is_expensive(path):
            return False, ""
        current = load if load is not None else load_per_cpu()
        if current >= 0 and current >= self.shed_load:
            with self._lock:
                self.shed_total += 1
            return True, (f"platform shedding expensive endpoint "
                          f"(load/cpu {current:.1f} >= {self.shed_load})")
        return False, ""
