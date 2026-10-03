"""The secondary notification channel (design 03 §5.2).

"No single notification path, ever." When an outbox row is undelivered past
X (default 15 min), the page ALSO goes out via a vendor-independent channel
(SMS / webhook / email — exactly one, for now). Structural rules from the
design, enforced here:

  1. ``secondary_fired_at`` is a TIMESTAMP, not a terminal status. Primary
     retries CONTINUE after the secondary fires — the status model makes
     "stop primary on secondary" unrepresentable, and the forwarder never
     implements it (design §8's pre-mortem: that line caused the SEV1 miss).
  2. The ``wakefulness`` attestation is REQUIRED config — the on-call writes,
     in their own words, what the channel does and does NOT guarantee. A
     secondary without that sentence is unconfigured.
  3. In Stage 2b+ / 2c the forwarder REFUSES TO START without a configured
     secondary whose last successful drill is within 30 days (cutover rule).
  4. The drill passes only on HUMAN ACK within 10 minutes. A delivery
     receipt is not an ack; a drill that delivers to a DND phone and calls
     it success is theater.

Only the ``webhook`` provider is implemented in this lane; ``sms``/``email``
are interface-ready (add a ``*Secondary`` class implementing ``send``).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

SECONDARY_TYPES = ("webhook", "sms", "email")

# Placeholders the wakefulness attestation must NOT be. The pre-mortem's
# lesson: a rushed onboarding filled this with "SMS delivery" and nobody
# noticed until the SEV1.
_WAKEFULNESS_PLACEHOLDERS = {
    "", "tbd", "todo", "n/a", "none", "sms delivery", "to be determined",
    "webhook delivery", "email delivery",
}

DRILL_ACK_WINDOW_S = 600        # 10 min — the drill passes only on human ack
DRILL_VALID_S = 30 * 86400      # 30 days — a drill older than this is stale


class SecondaryNotReady(Exception):
    """Raised when the cutover gate refuses to start the forwarder."""


class SecondaryConfigError(Exception):
    """Raised when the secondary channel is misconfigured."""


@dataclass
class SecondaryConfig:
    """Per-org secondary channel config (the interface is Type 1 — design
    §5.2; the choice of provider is Type 2)."""
    type: str = "webhook"          # exactly one of sms | webhook | email
    provider: str = ""             # MUST be vendor-independent of PagerDuty
    destination: str = ""          # phone | url | address
    credential_ref: str | None = None   # secrets ref — never inline
    wakefulness: str = ""          # REQUIRED free-text attestation
    test_cadence_days: int = 30

    def validate(self) -> None:
        if self.type not in SECONDARY_TYPES:
            raise SecondaryConfigError(
                f"secondary type must be one of {SECONDARY_TYPES}, "
                f"got {self.type!r}")
        if self.type == "webhook" and self.provider == "pagerduty":
            # Fate-sharing: when PD is down, PD's redundancy is what we are
            # waiting on. The secondary must be vendor-independent.
            raise SecondaryConfigError(
                "secondary provider must be vendor-independent of PagerDuty")
        if not self.destination:
            raise SecondaryConfigError(
                "secondary destination is required")
        wake = (self.wakefulness or "").strip()
        if wake.lower() in _WAKEFULNESS_PLACEHOLDERS:
            raise SecondaryConfigError(
                "secondary wakefulness attestation is required: the on-call "
                "must write, in their own words, what this channel does and "
                "does NOT guarantee (design §5.2). A placeholder is not an "
                "attestation.")
        if not (1 <= self.test_cadence_days <= 365):
            raise SecondaryConfigError("test_cadence_days out of range")


@dataclass
class SecondarySendResult:
    ok: bool
    detail: str
    latency_ms: float
    attempts: int


class WebhookSecondary:
    """POST the page as JSON to the configured webhook URL.

    Bounded retries (default 3, short backoff) — then the watchdog owns it.
    Primary retries continue regardless (structural rule 1)."""

    def __init__(self, config: SecondaryConfig, timeout_s: float = 5.0,
                 max_attempts: int = 3):
        config.validate()
        self.config = config
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts

    def send(self, page: dict) -> SecondarySendResult:
        body = json.dumps({
            "sentinel_secondary": True,
            "provider": self.config.provider,
            **page,
        }, sort_keys=True).encode("utf-8")
        last_error = "no attempts made"
        t0 = time.perf_counter()
        attempts = 0
        for attempts in range(1, self.max_attempts + 1):
            try:
                req = urllib.request.Request(
                    self.config.destination, data=body, method="POST",
                    headers={"Content-Type": "application/json",
                             "User-Agent": "sentinel/1.0 (secondary)"})
                with urllib.request.urlopen(req,
                                            timeout=self.timeout_s) as resp:
                    status = resp.getcode()
                if 200 <= status < 300:
                    return SecondarySendResult(
                        ok=True, detail=f"accepted status={status}",
                        latency_ms=(time.perf_counter() - t0) * 1000.0,
                        attempts=attempts)
                last_error = f"status={status}"
            except Exception as exc:  # never raise out of the secondary
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.2 * attempts)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        print(f"[sentinel] SECONDARY FAILED provider={self.config.provider} "
              f"attempts={attempts} error={last_error}", file=sys.stderr)
        return SecondarySendResult(ok=False, detail=last_error,
                                   latency_ms=latency_ms, attempts=attempts)


def build_secondary(config: SecondaryConfig | None) -> WebhookSecondary | None:
    """Instantiate the configured provider. sms/email raise a clear
    not-implemented (interface-ready, provider TBD) rather than silently
    degrading to nothing."""
    if config is None:
        return None
    if config.type == "webhook":
        return WebhookSecondary(config)
    raise SecondaryConfigError(
        f"secondary type {config.type!r} is interface-ready but no provider "
        f"is implemented in this lane yet")


# ---------------------------------------------------------------------------
# drills


class DrillTracker:
    """Tracks secondary-channel drills. A drill passes ONLY on human ack
    within 10 minutes (structural rule 4). Persisted as JSONL so restarts
    don't erase drill history."""

    def __init__(self, record_path: str | None = None, clock=None):
        self.record_path = record_path
        self.clock = clock or time.time

    def _append(self, rec: dict) -> None:
        if not self.record_path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.record_path)),
                        exist_ok=True)
            with open(self.record_path, "a") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
        except OSError as exc:
            print(f"[sentinel] DRILL RECORD FAILED err={exc}", file=sys.stderr)

    def start_drill(self) -> str:
        drill_id = uuid.uuid4().hex
        self._append({"event": "started", "drill_id": drill_id,
                      "ts": self.clock()})
        return drill_id

    def ack_drill(self, drill_id: str) -> bool:
        """Record a human ack. Returns True iff the ack landed inside the
        10-minute window (otherwise the drill already expired — theater
        is not counted)."""
        now = self.clock()
        started = self._started_at(drill_id)
        if started is None:
            return False
        if now - started > DRILL_ACK_WINDOW_S:
            return False
        self._append({"event": "passed", "drill_id": drill_id, "ts": now})
        return True

    def expire_drills(self, now: float | None = None) -> list[str]:
        """Drills started >10 min ago with no ack → expired. Returns their ids."""
        now = self.clock() if now is None else now
        expired = []
        for drill_id, started in self._started().items():
            if now - started > DRILL_ACK_WINDOW_S and not self._passed(
                    drill_id, started):
                expired.append(drill_id)
                self._append({"event": "expired", "drill_id": drill_id,
                              "ts": now})
        return expired

    def last_passed_ts(self) -> float | None:
        latest = None
        if self.record_path and os.path.exists(self.record_path):
            try:
                with open(self.record_path) as fh:
                    for line in fh:
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        if rec.get("event") == "passed":
                            ts = rec.get("ts")
                            if isinstance(ts, (int, float)) and (
                                    latest is None or ts > latest):
                                latest = ts
            except OSError:
                pass
        return latest

    # ------------------------------------------------------------ internals

    def _started(self) -> dict[str, float]:
        out: dict[str, float] = {}
        if self.record_path and os.path.exists(self.record_path):
            try:
                with open(self.record_path) as fh:
                    for line in fh:
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        if rec.get("event") == "started":
                            out[rec["drill_id"]] = rec["ts"]
            except OSError:
                pass
        return out

    def _started_at(self, drill_id: str) -> float | None:
        return self._started().get(drill_id)

    def _passed(self, drill_id: str, started_ts: float) -> bool:
        if self.record_path and os.path.exists(self.record_path):
            try:
                with open(self.record_path) as fh:
                    for line in fh:
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        if (rec.get("event") == "passed"
                                and rec.get("drill_id") == drill_id
                                and rec.get("ts", 0) - started_ts
                                <= DRILL_ACK_WINDOW_S):
                            return True
            except OSError:
                pass
        return False


def require_drilled_secondary(stage: str,
                              secondary: SecondaryConfig | None,
                              drills: DrillTracker | None,
                              now_ts: float | None = None) -> None:
    """Cutover rule (design §5.2, Type 1): in Stage 2b+ / 2c the forwarder
    refuses to start without a configured secondary whose last successful
    drill is within 30 days. In shadow mode the secondary is optional
    (nothing pages)."""
    if stage not in ("stage-2b", "stage-2c"):
        return
    if secondary is None:
        raise SecondaryNotReady(
            f"stage {stage} requires a configured secondary channel "
            f"(design 03 §5.2 — no single notification path, ever)")
    secondary.validate()
    now_ts = time.time() if now_ts is None else now_ts
    last = drills.last_passed_ts() if drills else None
    if last is None or now_ts - last > DRILL_VALID_S:
        raise SecondaryNotReady(
            f"stage {stage} requires a passed secondary drill within "
            f"30 days (last passed: {last})")
