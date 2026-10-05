"""Durable forwarder — design 03 (ADR-012) implementation.

The delivery contract, in one paragraph: every decided page enters the
durable outbox (eventlog.py, I1); this forwarder loop delivers at-least-once
using PagerDuty's own ``dedup_key`` idempotency; undelivered past X, an
independent secondary channel fires; a crash between "page sent" and
"outbox marked delivered" can neither lose the page (the row is durable,
the startup scan re-queues it) nor double-page (the retry reuses the
pinned ``dedup_key`` — PD appends to the open alert, which is PD's
documented dedup behavior, not a new page). The bounded residual — a
retry after the human resolved the alert inside the crash window creates
one new alert — is loud-safe (a duplicate page, never a missed one),
self-describing (``sentinel_retry``), and accepted explicitly by the
design (§4.3 Case 2). Exactly-once is NOT claimed: PD offers
at-least-once + dedup, and this module claims exactly what the vendor
offers.

Type-1 rules enforced here (design §8):
  - At-least-once + stable dedup_key (never exactly-once).
  - dedup_key comes from the ROW, re-derived never; >255 chars is terminal.
  - routing_key_ref pinned at first attempt; retries reuse the pinned key
    even if the vault rotated mid-flight (rotation runbook: drain first).
  - 202 + status == "success" ⇒ forward_confirmed. Nothing else does.
  - Secondary fire NEVER stops primary retries (secondary_fired_at is a
    timestamp, not a status — there is no code path that cancels primary
    retries on secondary fire).
  - Payload frozen at enqueue; the sender injects the secret at send time
    (see pd_sender.py — secrets never touch disk).

Outbox ownership: the outbox TABLE is owned by the event-log lane
(lane/impl-event-log, pinned at b2e0005). This lane CONSUMES it through
the EventLog public API (due_outbox_rows / undelivered_outbox_rows /
record_receipt_and_update / append_event) plus one atomic claim UPDATE on
its own SQLite connection (claiming is not a receipt — it writes no
event). The schema is NEVER redefined here.

Two cross-lane couplings, both flagged to the coordinator:
  1. Control-plane pages go through EventLog._enqueue_control_plane_page
     (private). Pinned to b2e0005; the adapter below fails loudly if the
     method disappears so the coupling can't rot silently.
  2. Rows with max_age_at <= created_at are treated as IMMORTAL by the
     max-age sweep. The event-log lane's control-plane enqueue sets
     max_age_at = created_at (== now); the design says max_age_at =
     created_at + max_age. The immortal rule keeps control-plane pages
     alive (the safe direction) under either reading.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import random
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .eventlog import EventLog, EventLogError, utcnow_iso
from .integrations import (resolve_paging_key, paging_key_source,
                           simulated_paging, sanitize_error)
from .pd_sender import (
    DedupKeyInvalid,
    PagerDutyClient,
    PayloadNotKeyless,
    SecretMissing,
    resolve_routing_key,
)
from .secondary import (
    DrillTracker,
    SecondaryConfig,
    SecondaryNotReady,
    WebhookSecondary,
    build_secondary,
    require_drilled_secondary,
)
from .spill import replay_spills, write_spill

# ---------------------------------------------------------------------------
# Type-2 tunables (design §8 — values, not contract)

BACKOFF_S = (5, 15, 45, 120, 300, 900, 1800, 1800)  # attempt1→5s … capped 30m
BACKOFF_JITTER = 0.20                                # ±20% decorrelated
RETRY_AFTER_FLOOR_S = 60.0     # 429: max(scheduled, 60s) + Retry-After
LEASE_S = 300.0                # in_flight lease; startup requeues stale ones
# NOTE: max age is per-row (outbox.max_age_at), not a forwarder knob —
# the enqueue side sets created_at + max_age. There is intentionally no
# max_age_s here.
SECONDARY_FIRE_AFTER_S = 900.0  # X = 15 min undelivered ⇒ secondary fires
SECONDARY_MAX_ATTEMPTS = 3
WORKER_THREADS = 4
SCHEDULER_POLL_S = 1.0
CLAIM_BATCH = 50
_CLAIM_RETRIES = 6

CONTROL_ROUTING_KEY_REF = "secret:pd/control_routing_key"

_VALID_STAGES = ("shadow", "stage-2a", "stage-2b", "stage-2c")

# Crash-injection hook for the kill -9 test (design §4.3 Case 1/2).
# Honored ONLY when explicitly enabled via config or env. Test-only.
_CRASH_ENV = "SENTINEL_FWD_CRASH_AFTER_SEND"


# ---------------------------------------------------------------------------
# config


@dataclass
class ForwarderConfig:
    """All knobs. Type 1 lives in the code paths; these are Type-2 values."""
    env: str = "prod"
    pd_endpoint: str = "https://events.pagerduty.com/v2/enqueue"
    pd_timeout_s: float = 5.0
    workers: int = WORKER_THREADS
    poll_s: float = SCHEDULER_POLL_S
    lease_s: float = LEASE_S
    backoff_s: tuple = BACKOFF_S
    jitter: float = BACKOFF_JITTER
    retry_after_floor_s: float = RETRY_AFTER_FLOOR_S
    secondary_fire_after_s: float = SECONDARY_FIRE_AFTER_S
    secondary_max_attempts: int = SECONDARY_MAX_ATTEMPTS
    spill_dir: str | None = None
    stage: str = "shadow"          # shadow | stage-2a | stage-2b | stage-2c
    secondary: SecondaryConfig | None = None
    drill_record_path: str | None = None
    control_routing_key_ref: str = CONTROL_ROUTING_KEY_REF
    secret_mapping: dict = field(default_factory=dict)  # ref → env name
    crash_after_send: bool = False  # test-only fault injection

    def validate(self) -> None:
        if self.workers < 1:
            raise ValueError("workers must be >= 1")
        if self.stage not in _VALID_STAGES:
            raise ValueError(f"stage must be one of {_VALID_STAGES}")
        if not (0 <= self.jitter < 1):
            raise ValueError("jitter must be in [0, 1)")
        if self.secondary is not None:
            self.secondary.validate()


# ---------------------------------------------------------------------------
# helpers


def _iso_to_ts(iso: str) -> float | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def _ts_to_iso(ts: float) -> str:
    return (datetime.fromtimestamp(ts, timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")


def _lease_stale(lease_at: str | None, now_iso: str, lease_s: float) -> bool:
    if not lease_at:
        return True
    now_ts, lease_ts = _iso_to_ts(now_iso), _iso_to_ts(lease_at)
    if now_ts is None or lease_ts is None:
        return True  # unparseable lease — safe direction is requeue
    return (now_ts - lease_ts) > lease_s


def _hour_bucket() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H")


def _secondary_page_from_row(row: dict, annotations: list) -> dict:
    """Render the secondary page from the SAME frozen payload (design §5.2):
    one page, one content, two paths."""
    try:
        frozen = json.loads(row["payload_frozen"])
    except ValueError:
        frozen = {}
    payload = frozen.get("payload", {}) if isinstance(frozen, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    details = payload.get("custom_details", {})
    return {
        "summary": str(payload.get("summary")
                       or frozen.get("summary") or "sentinel page")[:1024],
        "severity": payload.get("severity", "critical"),
        "source": payload.get("source", "sentinel"),
        "dedup_key": row["dedup_key"],
        "alert_id": row["alert_id"],
        "episode_id": row["episode_id"],
        "annotations": list(annotations),
        "custom_details": details if isinstance(details, dict) else {},
    }


# ---------------------------------------------------------------------------
# the durable forwarder


class DurableForwarder:
    """The outbox relay: claims due rows, POSTs to PD, writes I2 receipts.

    Scheduler thread: startup scan → loop { claim due rows → workers;
    secondary scan; max-age sweep; drill check }. Worker pool: one attempt
    = one PD POST + one I2 transaction through the EventLog (the engine's
    single writer — design §2.2).
    """

    def __init__(self, log: EventLog,
                 config: ForwarderConfig | None = None,
                 pd_client: PagerDutyClient | None = None,
                 secondary: WebhookSecondary | None = None,
                 drills: DrillTracker | None = None):
        if log.db_path == ":memory:":
            raise ValueError(
                "DurableForwarder requires a file-backed EventLog "
                "(the claim connection needs the same database file)")
        self.log = log
        self.config = config or ForwarderConfig()
        self.config.validate()
        self.pd = pd_client or PagerDutyClient(
            endpoint=self.config.pd_endpoint,
            timeout_s=self.config.pd_timeout_s)
        self.secondary = (secondary if secondary is not None
                          else build_secondary(self.config.secondary))
        self.drills = drills or DrillTracker(self.config.drill_record_path)
        # The claim connection: a second SQLite connection to the same WAL
        # file. Claims are short atomic UPDATEs; on SQLITE_BUSY we back off
        # and retry — never a lost claim. All RECEIPTS (I2) go through the
        # EventLog's single writer, preserving the engine's write discipline.
        self._conn = sqlite3.connect(log.db_path, timeout=10.0,
                                     check_same_thread=False,
                                     isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=10000;")
        self._claim_lock = threading.Lock()
        self._work: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._threads: list = []
        self._key_pins: dict = {}   # outbox_id → key pinned at
        self._pins_lock = threading.Lock()    # first attempt (design §4.3)
        self._secondary_tries: dict = {}
        self.metrics: dict = {
            "claimed": 0, "confirmed": 0, "retryable": 0,
            "dead_letter": 0, "secondary_fired": 0, "spills_replayed": 0,
            "requeued": 0, "control_plane_pages": 0,
        }

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Start the forwarder: cutover gate → startup scan → threads."""
        require_drilled_secondary(self.config.stage, self.config.secondary,
                                  self.drills)
        self.startup_scan()
        # Wire the degraded ladder (design §6 F1): the event-log lane left
        # this seam for us. On a commit-watchdog trip the log calls this
        # with the evidence payload; we send it direct-to-PD + spill.
        if self.log.degraded_sender is not None:
            print("[sentinel] FORWARDER overwriting existing degraded_sender",
                  file=sys.stderr)
        self.log.degraded_sender = self._degraded_send
        self._stop.clear()
        sched = threading.Thread(target=self._scheduler_loop,
                                 name="fwd-scheduler", daemon=True)
        sched.start()
        self._threads.append(sched)
        for i in range(self.config.workers):
            w = threading.Thread(target=self._worker_loop,
                                 name=f"fwd-worker-{i}", daemon=True)
            w.start()
            self._threads.append(w)

    def stop(self, timeout_s: float = 30.0) -> None:
        self._stop.set()
        for _ in self._threads[1:]:
            self._work.put(None)  # worker sentinels (scheduler exits alone)
        for t in self._threads:
            t.join(timeout=timeout_s / max(len(self._threads), 1))
        self._threads = []
        try:
            self._conn.close()
        except Exception:
            pass

    def drain(self, timeout_s: float = 30.0) -> None:
        """Test seam: block until every claimed row has been attempted."""
        deadline = time.time() + timeout_s
        while self._work.unfinished_tasks:
            if time.time() > deadline:
                raise TimeoutError("forwarder drain timed out")
            time.sleep(0.01)

    # -------------------------------------------------------- startup scan

    def startup_scan(self, now_iso: str | None = None) -> dict:
        """Crash recovery, in order: replay spills (degraded sends need
        their audit), then re-queue stale in_flight rows (a crashed worker
        can never strand a page — design §2.1)."""
        now_iso = now_iso or utcnow_iso()
        replayed = (replay_spills(self.log, self.config.spill_dir)
                    if self.config.spill_dir else [])
        self.metrics["spills_replayed"] += len(replayed)
        requeued = 0
        for row in self.log.undelivered_outbox_rows():
            if row["status"] != "in_flight":
                continue
            if not _lease_stale(row.get("lease_at"), now_iso,
                                self.config.lease_s):
                continue
            # The crash-window attempt's fate is unknown. Record it honestly
            # (forward_failed/crash_window_requeue) and re-queue — the retry
            # reuses the pinned dedup_key, so PD dedups it onto the open
            # alert. The log shows crash → retry → forward_confirmed.
            self.log.record_receipt_and_update(
                "forward_failed", outbox_id=row["outbox_id"],
                alert_id=row["alert_id"], fingerprint=row["fingerprint"],
                episode_id=row["episode_id"],
                body={"outbox_id": row["outbox_id"], "channel": "forwarder",
                      "attempt_no": row["attempt_count"],
                      "error_class": "crash_window_requeue"},
                scheduler={"status": "queued",
                           "next_attempt_at": now_iso,
                           "last_error": "stale in_flight lease at startup "
                                         "(crash window) — requeued"})
            requeued += 1
        self.metrics["requeued"] += requeued
        return {"spills_replayed": len(replayed), "requeued": requeued}

    # ------------------------------------------------------------- scheduler

    def _scheduler_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # the scheduler never dies quietly
                print(f"[sentinel] FORWARDER scheduler error: {exc}",
                      file=sys.stderr)
            self._stop.wait(self.config.poll_s)

    def run_once(self, now_iso: str | None = None) -> int:
        """One scheduler iteration: claim due rows for the workers, then
        the secondary scan, max-age sweep, and drill check. Returns the
        number of rows claimed."""
        now_iso = now_iso or utcnow_iso()
        claimed = self.claim_due_rows(now_iso)
        for row in claimed:
            self._work.put(row)
        self._secondary_scan(now_iso)
        self._max_age_sweep(now_iso)
        self._drill_check()
        return len(claimed)

    def run_once_sync(self, now_iso: str | None = None) -> int:
        """Deterministic driver (tests): claim due rows and attempt each
        INLINE (no worker threads), then the scans. The attempt path is
        identical to the threaded one."""
        now_iso = now_iso or utcnow_iso()
        claimed = self.claim_due_rows(now_iso)
        for row in claimed:
            try:
                self._attempt(row)
            except Exception as exc:
                self._safe_requeue(row, exc)
        self._secondary_scan(now_iso)
        self._max_age_sweep(now_iso)
        self._drill_check()
        return len(claimed)

    def claim_due_rows(self, now_iso: str,
                       limit: int = CLAIM_BATCH) -> list:
        """Atomically claim due rows (queued → in_flight + lease). Each
        claim is one UPDATE guarded by `AND status='queued'` — two
        forwarders can never claim the same row."""
        claimed = []
        for row in self.log.due_outbox_rows(now_iso, limit=limit):
            if self._claim_one(row["outbox_id"], now_iso):
                claimed.append(self.log.outbox_row(row["outbox_id"]))
                self.metrics["claimed"] += 1
        return claimed

    def _claim_one(self, outbox_id: str, now_iso: str) -> bool:
        with self._claim_lock:
            for i in range(_CLAIM_RETRIES):
                try:
                    self._conn.execute("BEGIN IMMEDIATE;")
                    try:
                        cur = self._conn.execute(
                            "UPDATE outbox SET status='in_flight', "
                            "lease_at=?, attempt_count=attempt_count+1 "
                            "WHERE outbox_id=? AND status='queued'",
                            (now_iso, outbox_id))
                        self._conn.execute("COMMIT;")
                    except BaseException:
                        self._conn.execute("ROLLBACK;")
                        raise
                    return cur.rowcount == 1
                except sqlite3.OperationalError:
                    time.sleep(0.05 * (i + 1))  # SQLITE_BUSY: back off
            print(f"[sentinel] FORWARDER claim failed after retries "
                  f"outbox={outbox_id} — row stays queued, next loop retries",
                  file=sys.stderr)
            return False

    # --------------------------------------------------------------- workers

    def _worker_loop(self) -> None:
        while True:
            row = self._work.get()
            try:
                if row is None:
                    return
                try:
                    self._attempt(row)
                except Exception as exc:
                    # A worker must never strand a page on an unexpected
                    # exception: requeue loudly, keep the loop alive.
                    self._safe_requeue(row, exc)
            finally:
                self._work.task_done()

    def _safe_requeue(self, row: dict, exc: BaseException) -> None:
        try:
            self.log.record_receipt_and_update(
                "forward_failed", outbox_id=row["outbox_id"],
                alert_id=row["alert_id"], fingerprint=row["fingerprint"],
                episode_id=row["episode_id"],
                body={"outbox_id": row["outbox_id"], "channel": "forwarder",
                      "attempt_no": row["attempt_count"],
                      "error_class": "worker_exception"},
                scheduler={"status": "queued",
                           "next_attempt_at": utcnow_iso(),
                           "last_error": f"worker_exception: {exc}"})
        except Exception as exc2:
            print(f"[sentinel] FORWARDER requeue failed outbox="
                  f"{row['outbox_id']} err={exc2}", file=sys.stderr)

    # ---------------------------------------------------------------- attempt

    def _attempt(self, row: dict) -> None:
        """One delivery attempt for a claimed row: POST + I2 receipt."""
        obid = row["outbox_id"]
        n = row["attempt_count"]  # post-claim: this IS attempt n
        dk = row["dedup_key"]

        # 1. The idempotency property, asserted (design §3 — correctness,
        #    not convenience). The key comes from the ROW, re-derived never.
        if not dk or len(dk) > 255:
            self._terminal(row, n, "dedup_key_invalid",
                           "dedup_key missing or exceeds PD's 255-char limit")
            return

        # 2. Routing key — pinned at first attempt (design §4.3). Retries
        #    reuse the pinned key even if the vault rotated mid-flight.
        try:
            key = self._pinned_key(obid, row["routing_key_ref"])
        except SecretMissing as exc:
            # Config problem, possibly transient — retryable, loud.
            self._retryable(row, n, "secret_missing", str(exc),
                            delay_s=self.config.backoff_s[0])
            return

        # 3. Frozen-payload integrity: never send a tampered page.
        frozen = row["payload_frozen"].encode("utf-8")
        if hashlib.sha256(frozen).hexdigest() != row["payload_sha256"]:
            self._terminal(row, n, "payload_integrity_error",
                           "payload_sha256 mismatch — refusing to send")
            return

        # 4. The POST (at-least-once; PD's dedup_key is the idempotency).
        try:
            result = self.pd.send(payload_frozen=frozen, dedup_key=dk,
                                  routing_key=key, attempt_no=n)
        except (PayloadNotKeyless, DedupKeyInvalid) as exc:
            # Enqueue-side bug — terminal for the primary, loud everywhere.
            self._terminal(row, n, "payload_formation_error", str(exc))
            return

        # 5. Crash-injection hook (test-only): die AFTER the vendor
        #    accepted, BEFORE the I2 receipt — exactly the crash window the
        #    startup scan + dedup_key must survive.
        if result.outcome == "accepted" and (
                self.config.crash_after_send
                or os.environ.get(_CRASH_ENV) == "1"):
            os._exit(1)

        # 6. Classify and record.
        if result.outcome == "accepted":
            self._confirmed(row, n, result)
        elif result.outcome == "terminal":
            self._terminal(row, n, "payload_formation_error",
                           f"PD 400: {result.error}")
        else:
            delay = self._retry_delay_s(n, result)
            self._retryable(row, n, result.error_class,
                            result.error or result.error_class,
                            delay_s=delay)

    def _confirmed(self, row: dict, attempt_no: int, result) -> None:
        """forward_confirmed means exactly one thing: accepted by the
        vendor's durable intake (202 + status == "success"). It does NOT
        mean the human's phone rang (design §4.2)."""
        self.log.record_receipt_and_update(
            "forward_confirmed", outbox_id=row["outbox_id"],
            alert_id=row["alert_id"], fingerprint=row["fingerprint"],
            episode_id=row["episode_id"],
            body={"outbox_id": row["outbox_id"], "channel": "pagerduty",
                  "attempt_no": attempt_no,
                  "vendor_status": result.vendor_status,
                  "latency_ms": round(result.latency_ms, 2),
                  "wire_sha256": result.wire_sha256},
            scheduler={"status": "delivered",
                       "delivered_at": utcnow_iso(),
                       "last_error": None})
        self.metrics["confirmed"] += 1

    def _retryable(self, row: dict, attempt_no: int, error_class: str,
                   error: str, delay_s: float) -> None:
        now_ts = time.time()
        max_age_at = row["max_age_at"]
        max_age_ts = _iso_to_ts(max_age_at)
        created_ts = _iso_to_ts(row["created_at"])
        # Max age is enforced at schedule time: a retry that could never run
        # before the deadline becomes a dead_letter now, loudly.
        if (max_age_ts and created_ts and max_age_ts > created_ts
                and now_ts + delay_s > max_age_ts):
            self._dead_letter(row, attempt_no, "max_age_exceeded",
                              f"next retry would exceed max_age_at "
                              f"({max_age_at}); last error: {error}")
            return
        self.log.record_receipt_and_update(
            "forward_failed", outbox_id=row["outbox_id"],
            alert_id=row["alert_id"], fingerprint=row["fingerprint"],
            episode_id=row["episode_id"],
            body={"outbox_id": row["outbox_id"], "channel": "pagerduty",
                  "attempt_no": attempt_no, "error_class": error_class},
            scheduler={"status": "queued",
                       "next_attempt_at": _ts_to_iso(now_ts + delay_s),
                       "last_error": error[:500]})
        self.metrics["retryable"] += 1

    def _terminal(self, row: dict, attempt_no: int, error_class: str,
                  error: str) -> None:
        """Terminal for the PRIMARY (400 / our bug): dead_letter + the page
        fires via the secondary with an annotation + a control-plane page
        goes to the engineering on-call (design §4.4)."""
        self._dead_letter(row, attempt_no, error_class, error)
        try:
            annotations = [error_class]
            if self.secondary is not None:
                page = _secondary_page_from_row(row, annotations)
                res = self.secondary.send(page)
                self._secondary_receipt(row, res, annotations)
            self.enqueue_control_plane_page(
                kind="payload_formation_error",
                summary=f"forwarder: {error_class} on {row['alert_id']} "
                        f"(outbox {row['outbox_id']}) — primary terminal, "
                        f"page sent via secondary",
                detail={"outbox_id": row["outbox_id"], "error": error,
                        "dedup_key": row["dedup_key"]})
        except Exception as exc:
            # The row is already dead_lettered (the morgue has its record).
            # A failure of the LOUD path must never resurrect it — the
            # worker-exception handler would set it back to queued.
            print(f"[sentinel] FORWARDER terminal loud path failed "
                  f"outbox={row['outbox_id']} err={exc}", file=sys.stderr)

    def _dead_letter(self, row: dict, attempt_no: int, error_class: str,
                     error: str) -> None:
        self.log.record_receipt_and_update(
            "forward_failed", outbox_id=row["outbox_id"],
            alert_id=row["alert_id"], fingerprint=row["fingerprint"],
            episode_id=row["episode_id"],
            body={"outbox_id": row["outbox_id"], "channel": "pagerduty",
                  "attempt_no": attempt_no, "error_class": error_class},
            scheduler={"status": "dead_letter", "last_error": error[:500]})
        self.metrics["dead_letter"] += 1
        # dead_letter is a morgue with an alarm, not a trash can.
        print(f"[sentinel] FORWARDER dead_letter outbox={row['outbox_id']} "
              f"alert={row['alert_id']} error_class={error_class}",
              file=sys.stderr)

    # ------------------------------------------------------- retry schedule

    def _retry_delay_s(self, attempt_no: int, result) -> float:
        """Backoff schedule (Type 2): 5s → 15s → 45s → 2m → 5m → 15m →
        30m → 30m (capped), each ±20% decorrelated jitter. On 429:
        max(scheduled, 60s), honoring Retry-After (design §5.1)."""
        schedule = self.config.backoff_s
        idx = min(max(attempt_no - 1, 0), len(schedule) - 1)
        base = schedule[idx]
        delay = base * (1 + random.uniform(-self.config.jitter,
                                           self.config.jitter))
        if result is not None and result.error_class == "rate_limited":
            delay = max(delay, self.config.retry_after_floor_s)
            if result.retry_after_s:
                delay = max(delay, result.retry_after_s)
        return delay

    # ------------------------------------------------- secondary + sweeps

    def _secondary_scan(self, now_iso: str) -> int:
        """Fire the secondary for rows undelivered past X. Primary retries
        CONTINUE — secondary_fired_at is a timestamp, not a status, and no
        code path here cancels primary retries (design §5.2, §8)."""
        if self.secondary is None:
            return 0
        cutoff_ts = time.time() - self.config.secondary_fire_after_s
        fired = 0
        for row in self.log.undelivered_outbox_rows():
            if row["status"] not in ("queued", "in_flight"):
                continue
            if row["secondary_fired_at"]:
                continue
            created_ts = _iso_to_ts(row["created_at"])
            if created_ts is None or created_ts > cutoff_ts:
                continue
            tries = self._secondary_tries.get(row["outbox_id"], 0)
            if tries >= self.config.secondary_max_attempts:
                continue  # bounded; the watchdog owns it now
            self._secondary_tries[row["outbox_id"]] = tries + 1
            page = _secondary_page_from_row(row, ["undelivered_past_X"])
            res = self.secondary.send(page)
            self._secondary_receipt(row, res, ["undelivered_past_X"])
            fired += 1
        return fired

    def _secondary_receipt(self, row: dict, res, annotations: list) -> None:
        n = row["attempt_count"] + 1  # attempt_count counts both channels
        if res.ok:
            self.log.record_receipt_and_update(
                "forward_confirmed", outbox_id=row["outbox_id"],
                alert_id=row["alert_id"], fingerprint=row["fingerprint"],
                episode_id=row["episode_id"],
                body={"outbox_id": row["outbox_id"], "channel": "secondary",
                      "attempt_no": n, "vendor_status": "secondary_accepted",
                      "latency_ms": round(res.latency_ms, 2),
                      "annotations": annotations},
                scheduler={"secondary_fired_at": utcnow_iso(),
                           "attempt_count": n, "last_error": None})
            self.metrics["secondary_fired"] += 1
        else:
            # Secondary failed: bounded retries continue on later scans;
            # primary retries continue regardless. Both-down is
            # dead_letter-with-screaming (design §6 F6).
            self.log.record_receipt_and_update(
                "forward_failed", outbox_id=row["outbox_id"],
                alert_id=row["alert_id"], fingerprint=row["fingerprint"],
                episode_id=row["episode_id"],
                body={"outbox_id": row["outbox_id"], "channel": "secondary",
                      "attempt_no": n, "error_class": "secondary_failed"},
                scheduler={"attempt_count": n,
                           "last_error": f"secondary_failed: {res.detail}"[:500]})

    def _max_age_sweep(self, now_iso: str) -> int:
        """Rows older than max_age_at ⇒ dead_letter, loudly. Rows with
        max_age_at <= created_at are IMMORTAL (no max age) — this protects
        the event-log lane's control-plane rows; see module docstring."""
        now_ts = _iso_to_ts(now_iso) or time.time()
        swept = 0
        for row in self.log.undelivered_outbox_rows():
            max_age_ts = _iso_to_ts(row["max_age_at"])
            created_ts = _iso_to_ts(row["created_at"])
            if max_age_ts is None or created_ts is None:
                continue
            if max_age_ts <= created_ts:
                continue  # immortal: no max age configured
            if max_age_ts > now_ts:
                continue
            self._dead_letter(row, row["attempt_count"], "max_age_exceeded",
                              f"undelivered past max_age_at "
                              f"({row['max_age_at']})")
            try:
                self.enqueue_control_plane_page(
                    kind="forwarder_dead_letter",
                    summary=f"forwarder: outbox {row['outbox_id']} "
                            f"({row['alert_id']}) dead-lettered past max age",
                    detail={"outbox_id": row["outbox_id"],
                            "alert_id": row["alert_id"],
                            "dedup_key": row["dedup_key"]})
            except Exception as exc:
                # The row is already dead_lettered; a sick loud path must
                # not abort the sweep of the remaining rows.
                print(f"[sentinel] FORWARDER dead-letter page failed "
                      f"outbox={row['outbox_id']} err={exc}", file=sys.stderr)
            swept += 1
        return swept

    def _drill_check(self) -> list:
        """Unacked secondary drills past the 10-minute window are a paged
        SLO breach (design §5.2)."""
        expired = self.drills.expire_drills()
        for drill_id in expired:
            self.enqueue_control_plane_page(
                kind="secondary_drill_missed",
                summary="forwarder: secondary drill missed the 10-minute "
                        f"human-ack window (drill {drill_id}) — SLO breach",
                detail={"drill_id": drill_id})
        return expired

    # ------------------------------------------------- control-plane pages

    def enqueue_control_plane_page(self, kind: str, summary: str,
                                   detail: dict) -> str:
        """Priority-1 outbox row via the event-log lane's mechanism
        (design 03 §2.3 — one pager, prioritized, not two pagers).

        CROSS-LANE COUPLING (flagged to the coordinator): this calls the
        event-log lane's private _enqueue_control_plane_page (pinned at
        lane/impl-event-log b2e0005). Fails loudly if it disappears so the
        coupling can't rot silently.
        """
        fn = getattr(self.log, "_enqueue_control_plane_page", None)
        if fn is None:
            raise EventLogError(
                "event-log lane removed _enqueue_control_plane_page "
                "(was pinned at b2e0005) — coordinator: formalize the "
                "control-plane page API")
        outbox_id = fn(kind=kind, summary=summary, detail=detail)
        self.metrics["control_plane_pages"] += 1
        return outbox_id

    # ------------------------------------------------- degraded / standby

    def _pinned_key(self, outbox_id: str, routing_key_ref: str) -> str:
        """routing_key_ref pinned at first attempt (design §4.3): retries
        ALWAYS use the pinned key, even if the vault rotated mid-flight."""
        with self._pins_lock:
            if outbox_id in self._key_pins:
                return self._key_pins[outbox_id]
        key = resolve_routing_key(routing_key_ref, self.config.secret_mapping)
        with self._pins_lock:
            self._key_pins.setdefault(outbox_id, key)
        return self._key_pins[outbox_id]

    def _degraded_send(self, payload: dict) -> None:
        """The EventLog commit-watchdog seam (design §6 F1): never raises."""
        try:
            self.send_direct(payload, reason="commit_watchdog_trip")
        except Exception as exc:  # the degraded path never sinks the hot path
            print(f"[sentinel] FORWARDER degraded send failed: {exc}",
                  file=sys.stderr)

    def send_direct(self, payload: dict, *, reason: str = "degraded") -> dict:
        """Standby direct-to-PD (design §6 F1): the page MUST go out even
        when the outbox/queue is unavailable. Bypasses the outbox entirely,
        writes the emergency spillover record (stderr + spill file — the
        record the next startup replays into the log), and NEVER raises.

        This is the fail-open backstop: when the primary durable path is
        dead, the promise to the human is kept on the direct path and the
        audit is repaired at the next startup via spill replay.
        """
        outcome: dict = {"pd_outcome": None, "spill": None}
        dedup_key = (f"sentinel/{self.config.env}/degraded/"
                     f"{payload.get('kind', 'unknown')}/{_hour_bucket()}")
        # BYOK simulated mode: absorb even the standby path — a degraded
        # page must still be honestly labeled.
        if simulated_paging():
            # Source only, never the value: the simulated path has no
            # business resolving a key (C6 contract: "never resolves a
            # routing key"; Vault never-crosses rule).
            _src = paging_key_source()
            print(f"[sentinel] SIMULATED PAGE action=degraded-page "
                  f"dedup={dedup_key} key_source={_src} reason={reason} "
                  f"(simulated paging is ON — nothing was sent to PagerDuty)",
                  file=sys.stderr)
            outcome["pd_outcome"] = "simulated"
            outcome["simulated"] = True
            if self.config.spill_dir:
                outcome["spill"] = write_spill(self.config.spill_dir, {
                    "kind": payload.get("kind", "unknown"),
                    "alert_id": payload.get("alert_id", "degraded"),
                    "dedup_key": dedup_key,
                    "simulated": True,
                    "reason": reason,
                    "pd_outcome": "simulated",
                })
            return outcome
        try:
            # BYOK: the operator's own key first (their pager), then the
            # control-plane ref. The key value never enters this record.
            ukey, usource = resolve_paging_key()
            if ukey is not None:
                key, key_source = ukey, f"user:{usource}"
            else:
                key = resolve_routing_key(self.config.control_routing_key_ref,
                                          self.config.secret_mapping)
                key_source = "control-ref"
            summary = str(payload.get("summary") or payload.get("detail")
                          or payload.get("kind") or "sentinel degraded page")
            event = {
                "event_action": "trigger",
                "payload": {
                    "summary": summary[:1024],
                    "severity": "critical",
                    "source": "sentinel/degraded",
                    "component": "sentinel",
                    "custom_details": {
                        "sentinel_degraded": True,
                        "sentinel_degraded_reason": reason,
                        "sentinel_degraded_payload": payload,
                    },
                },
            }
            result = self.pd.send_event(event=event, dedup_key=dedup_key,
                                        routing_key=key)
            outcome["pd_outcome"] = result.outcome
            outcome["pd_status"] = result.status_code
            outcome["error"] = result.error
            outcome["latency_ms"] = round(result.latency_ms, 2)
        except Exception as exc:
            outcome["pd_outcome"] = "send_error"
            outcome["error"] = f"{type(exc).__name__}"
        if self.config.spill_dir:
            outcome["spill"] = write_spill(self.config.spill_dir, {
                "kind": payload.get("kind", "unknown"),
                "alert_id": payload.get("alert_id", "degraded"),
                "fingerprint": payload.get("fingerprint", "degraded"),
                "episode_id": payload.get("episode_id", "degraded"),
                "dedup_key": dedup_key,
                "payload_sha256": hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest(),
                "routing_key_ref": self.config.control_routing_key_ref,
                "reason": reason,
                "pd_outcome": outcome["pd_outcome"],
                "pd_status": outcome.get("pd_status"),
                "latency_ms": outcome.get("latency_ms", 0.0),
                "error": outcome.get("error"),
            })
        print(f"[sentinel] FORWARDER degraded direct-to-PD reason={reason} "
              f"outcome={outcome['pd_outcome']} dedup_key={dedup_key} "
              f"spill={outcome['spill']}", file=sys.stderr)
        return outcome

    # ------------------------------------------------- secondary drills

    def run_secondary_drill(self) -> str:
        """Send a drill page via the secondary. Returns the drill id — the
        drill passes ONLY when ack_secondary_drill() is called within
        10 minutes (a delivery receipt is not an ack)."""
        if self.secondary is None:
            raise SecondaryNotReady("no secondary channel configured")
        drill_id = self.drills.start_drill()
        page = {"summary": f"sentinel secondary drill {drill_id}",
                "severity": "info", "source": "sentinel/drill",
                "dedup_key": f"sentinel/{self.config.env}/drill/{drill_id}",
                "drill": True, "drill_id": drill_id}
        res = self.secondary.send(page)
        if not res.ok:
            print(f"[sentinel] FORWARDER drill send failed: {res.detail}",
                  file=sys.stderr)
        return drill_id

    def ack_secondary_drill(self, drill_id: str) -> bool:
        return self.drills.ack_drill(drill_id)


# ===========================================================================
# LEGACY — v0.1 synchronous relay (kept for the receiver/gate lanes'
# existing call sites: receiver.py, test_gate.py, test_receiver.py).
#
# This is the frozen flaw's shape (synchronous forward on the request path);
# new code MUST use DurableForwarder + the outbox instead. The class is
# preserved behavior-for-behavior here so other lanes' tests keep passing.
# ===========================================================================

"""Forwarder — relay to PagerDuty/Opsgenie (§3.7).

  page_now            -> POST trigger to PD with the original routing key
  page_business_hours -> POST trigger, severity downgraded to "warning",
                         custom_details["sentinel_queue"] = "business_hours"
  suppress            -> do NOT forward; record only (audit already written)
  passthrough         -> POST the original payload unchanged

Forward errors are logged + counted, never raised, never silently dropped.
Key hygiene: the routing key lives in the request body; we never log request
bodies, and any Authorization header is stripped before request logging.
"""

import urllib.error as _urllib_error
import urllib.request as _urllib_request
from dataclasses import dataclass as _dataclass

from .models import Alert, Disposition

_USER_AGENT_LEGACY = "sentinel/0.1"
_PD_SEVERITIES = {"critical", "error", "warning", "info"}


@_dataclass
class ForwardResult:
    forwarded: bool           # True if an HTTP POST was attempted and accepted
    status_code: int | None
    error: str | None
    action: str               # the disposition action this forward served
    dedup_key: str | None = None
    simulated: bool = False    # True when simulated paging absorbed the send


class Forwarder:
    def __init__(self, pd_events_url: str = "https://events.pagerduty.com/v2/enqueue",
                 timeout_s: float = 5.0, default_routing_key: str | None = None,
                 key_resolver=None):
        self.pd_events_url = pd_events_url
        self.timeout_s = timeout_s
        self.default_routing_key = default_routing_key
        # key_resolver: () -> (key|None, source). Per-decision resolution:
        # user store → ctor default → env → unconfigured. The source is safe
        # to log; the key is NEVER logged (see integrations.sanitize_error).
        self._key_resolver = key_resolver
        self.metrics: dict = {
            "forwarded": 0,   # POSTs accepted (2xx)
            "suppressed": 0,  # suppress dispositions: intentionally not forwarded
            # D3: "folded" (storm-continuation absorbed into the aggregate
            # page) is intentionally not forwarded either — but it is NOT
            # suppression, so it gets its own counter. Lumping folded into
            # "suppressed" would lie to the 3 AM operator about how many
            # model-driven suppressions happened.
            "folded": 0,
            "errors": 0,      # forward attempts that failed
            "simulated": 0,   # pages absorbed by simulated mode (never sent)
        }

    # ------------------------------------------------------- key resolution
    def _resolve_key(self) -> tuple[str | None, str]:
        """Per-decision routing-key resolution (BYOK lane).

        Order: explicit key_resolver → user integrations store →
        PD_ROUTING_KEY env (inside resolve_paging_key) → constructor
        default → unconfigured.
        Returns (key_or_None, source); the source is safe to log.
        """
        if self._key_resolver is not None:
            return self._key_resolver()
        key, source = resolve_paging_key()
        if key:
            return key, source
        if self.default_routing_key:
            return self.default_routing_key, "ctor"
        return None, "unconfigured"

    # ------------------------------------------------------------------ API

    def forward(self, alert: Alert, disposition: Disposition,
                raw_bytes: bytes | None = None) -> ForwardResult:
        """Relay one triaged alert. Never raises."""
        action = disposition.action
        try:
            if action in ("suppress", "folded"):
                # D3: folded (storm-continuation) is not forwarded — absorbed
                # into the aggregate page — but counted separately from
                # model-driven suppressions.
                self.metrics["folded" if action == "folded" else "suppressed"] += 1
                return ForwardResult(forwarded=False, status_code=None,
                                     error=None, action=action,
                                     dedup_key=_dedup_key_of(alert))
            # BYOK simulated mode: absorb BEFORE key resolution — a simulated
            # page needs no key (that's the point of the showcase), and it is
            # always labeled, never silent.
            if simulated_paging():
                return self._simulated_send(action, _dedup_key_of(alert),
                                            alert.alert_id)
            if action == "page_business_hours":
                body = self._business_hours_body(alert)
            elif _is_pd_shaped(alert) and raw_bytes is not None:
                # PD-shaped alert: relay byte-identical to what arrived.
                body = raw_bytes
            else:
                # Non-PD-shaped alert (or no bytes retained): build the PD
                # trigger event, injecting the routing key resolved
                # PER DECISION (BYOK: user store → ctor/env → unconfigured).
                key, _source = self._resolve_key()
                event = _pd_event_for(alert, key)
                if event is None:
                    raise ValueError(
                        "no routing key configured — set one in Integrations "
                        "(your PagerDuty key), PD_ROUTING_KEY env, or enable "
                        "simulated paging")
                body = json.dumps(event, separators=(",", ":")).encode("utf-8")
            return self._post(body, action, _dedup_key_of(alert),
                              alert_id=alert.alert_id)
        except Exception as exc:  # never silently drop, never raise
            return self._fail(action, _dedup_key_of(alert), alert.alert_id, exc)

    def forward_raw(self, raw_bytes: bytes, alert_id: str = "unknown",
                    dedup_key: str | None = None) -> ForwardResult:
        """Best-effort relay of unparseable bytes (receiver fail-open path)."""
        try:
            return self._post(raw_bytes, "passthrough", dedup_key, alert_id=alert_id)
        except Exception as exc:
            return self._fail("passthrough", dedup_key, alert_id, exc)

    # -------------------------------------------------------------- internals

    def _business_hours_body(self, alert: Alert) -> bytes:
        key, _source = self._resolve_key()
        payload = _pd_event_for(alert, key)
        if payload is None:
            raise ValueError("no routing key configured for business-hours queue")
        inner = payload.setdefault("payload", {})
        inner["severity"] = "warning"
        details = inner.setdefault("custom_details", {})
        if isinstance(details, dict):
            details["sentinel_queue"] = "business_hours"
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def _post(self, body: bytes, action: str, dedup_key: str | None,
              alert_id: str) -> ForwardResult:
        # BYOK simulated mode: absorb the send — LOUDLY labeled, never
        # silent. The honesty law (P3): a simulated page must be visually
        # distinct from a real one everywhere it renders.
        if simulated_paging():
            return self._simulated_send(action, dedup_key, alert_id)
        req = _urllib_request.Request(
            self.pd_events_url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "User-Agent": _USER_AGENT_LEGACY},
        )
        _strip_auth_header(req)  # hygiene: no credentials in any request logging
        try:
            with _urllib_request.urlopen(req, timeout=self.timeout_s) as resp:
                status = resp.getcode()
        except _urllib_error.HTTPError as exc:
            # HTTPError is also a response: record the status, count the error.
            return self._fail(action, dedup_key, alert_id, exc,
                              status_code=exc.code)
        if 200 <= status < 300:
            self.metrics["forwarded"] += 1
            return ForwardResult(forwarded=True, status_code=status, error=None,
                                 action=action, dedup_key=dedup_key)
        return self._fail(action, dedup_key, alert_id,
                          RuntimeError(f"unexpected status {status}"),
                          status_code=status)

    def _simulated_send(self, action: str, dedup_key: str | None,
                        alert_id: str) -> ForwardResult:
        """Absorb a page in simulated mode: logged, never sent.

        The record carries key_source (safe) — never the key. Callers and
        the UI must render simulated pages as distinct from real ones.
        """
        # Source only, never the value: the simulated path never resolves a
        # key (C6 contract; Vault never-crosses rule).
        key_source = paging_key_source()
        self.metrics["simulated"] = self.metrics.get("simulated", 0) + 1
        print(f"[sentinel] SIMULATED PAGE action={action} alert={alert_id} "
              f"dedup={dedup_key} key_source={key_source} "
              f"(simulated paging is ON — nothing was sent to PagerDuty)",
              file=sys.stderr)
        return ForwardResult(forwarded=True, status_code=None, error=None,
                             action=action, dedup_key=dedup_key, simulated=True)

    def _fail(self, action: str, dedup_key: str | None, alert_id: str,
              exc: BaseException, status_code: int | None = None) -> ForwardResult:
        # The page was already decided; the operator must know the relay failed.
        # Log carries alert_id/action/status only — never the body (routing key).
        # The exception string is sanitized against known key values: a
        # hostile/synthetic exception carrying the key must not echo it.
        self.metrics["errors"] += 1
        err = sanitize_error(str(exc))
        print(f"[sentinel] FORWARD FAILED action={action} alert={alert_id} "
              f"status={status_code} error={err}", file=sys.stderr)
        return ForwardResult(forwarded=False, status_code=status_code,
                             error=err, action=action, dedup_key=dedup_key)


# ------------------------------------------------------------------ helpers

def _strip_auth_header(req: _urllib_request.Request) -> None:
    """Remove Authorization before any request logging (§7 HTTP redaction)."""
    try:
        req.remove_header("Authorization")
    except Exception:
        pass
    # urllib capitalizes variants; be thorough.
    for key in list(req.headers.keys()):
        if key.lower() == "authorization":
            del req.headers[key]


def _is_pd_shaped(alert: Alert) -> bool:
    """True when the retained payload is already a PD Events API v2 event."""
    return (alert.source == "pagerduty"
            and isinstance(alert.raw, dict)
            and bool(alert.raw.get("routing_key")))


def _dedup_key_of(alert: Alert) -> str | None:
    raw = alert.raw if isinstance(alert.raw, dict) else {}
    return raw.get("dedup_key") or alert.alert_id or alert.fingerprint


def _pd_event_for(alert: Alert, default_routing_key: str | None) -> dict | None:
    """Build a PD Events API v2 trigger dict for a non-PD-shaped alert.

    Returns None when no routing key is available (caller fails loudly).
    """
    raw = alert.raw if isinstance(alert.raw, dict) else {}
    if alert.source == "pagerduty" and raw.get("routing_key"):
        # Deep copy via JSON so we never mutate the retained original.
        return json.loads(json.dumps(raw))
    routing_key = raw.get("routing_key") or default_routing_key
    if not routing_key:
        return None
    severity = (alert.severity_in or "info").lower()
    if severity not in _PD_SEVERITIES:
        severity = "info"
    return {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": raw.get("dedup_key") or alert.alert_id,
        "payload": {
            "summary": alert.title or alert.alert_id,
            "source": alert.service,
            "severity": severity,
            "component": alert.service,
            "class": alert.check,
            "custom_details": {"sentinel_source": alert.source,
                               "labels": alert.labels or {}},
        },
    }
