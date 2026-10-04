"""Event log — append-only, hash-chained lifecycle events (ADR-011).

Implements design/fixes/02-event-log.md. The irreducible design:

    The log is an append-only event stream; the river is a projection;
    `forward_confirmed` is the ONLY event that means "paged."

Type-1 vocabulary (the seven event types + envelope) is frozen by this
module. There is NO UPDATE against `events`, ever — corrections are new
events that reference the old seq. (Enforced by tests/test_eventlog.py's
grep guard.)

Write-ordering invariants (transaction boundaries, not code paths):
    I1: decision_made(page_*) + outbox row commit in ONE transaction.
    I2: forward_confirmed/forward_failed + outbox scheduler update in ONE txn.
    I3: the receiver 202s ONLY after decision_requested commits.

The `events` table lives in the SAME WAL-mode SQLite file as the outbox
(file 03 §2.1) — the shared file is load-bearing: I1/I2 each need one
transaction, and one transaction needs one database.

Downstream lanes (gate, forwarder, receiver) integrate through:
    EventLog.record_request            (I3)
    EventLog.record_decision_and_enqueue (I1)
    EventLog.record_receipt_and_update  (I2)
    EventLog.append_event               (shadow/flip/checkpoint etc.)
    Reaper                              (Pair-B crash-window closer)
    EventLog.render_alert/render_episode (the river fold)
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .spill import write_spill

# ---------------------------------------------------------------- constants

SCHEMA_V = 1
GENESIS_PREV_HASH = "GENESIS"

EVENT_TYPES = frozenset({
    "decision_requested",
    "decision_made",
    "shadow_decision",
    "model_drift",          # ADR-015 (D2): response.model != pinned model
    "forward_confirmed",
    "forward_failed",
    "flip_observed",
    "checkpoint",
})

ACTORS = frozenset({
    "engine", "forwarder", "watchdog", "checkpoint", "calibration", "migration",
})

DISPOSITIONS = frozenset({
    "passthrough", "page_now", "page_business_hours", "suppress",
    # D3: "folded" — storm-continuation absorbed into the aggregate page.
    # Not suppression: no model decided anything about this alert.
    "folded",
})

PAGE_DISPOSITIONS = frozenset({"page_now", "page_business_hours", "passthrough"})

BUDGET_OUTCOMES = frozenset({
    "answered_in_time", "timer_won", "timer_won_shed", "error_passthrough",
    "reaper_redrive", "structural_passthrough", "pre_race_unknown",
})

# Required body keys per event type (Type 1 vocabulary — design §2).
_BODY_REQUIRED = {
    "decision_requested": ("input_sha256", "source_integration",
                           "severity_in", "received_ts", "raw_payload_bytes"),
    "decision_made": ("disposition", "budget_outcome", "lock_evaluation",
                      "freshness", "threshold_counterfactual", "links"),
    "shadow_decision": ("links",),
    # ADR-015 (D2): the named drift event. The links block lets the
    # dispatcher join the drift to its sibling decision_made row; the
    # phase names whether the hot path (gate) or the detached late answer
    # observed the drift.
    "model_drift": ("input_sha256", "expected_model", "observed_model",
                    "decision_phase", "links"),
    "forward_confirmed": ("outbox_id", "channel", "attempt_no",
                          "vendor_status", "latency_ms"),
    "forward_failed": ("outbox_id", "channel", "attempt_no", "error_class"),
    "flip_observed": ("input_sha256", "first_seq", "second_seq",
                      "differing_field", "first_value", "second_value"),
    "checkpoint": ("head_seq", "head_hash", "event_count", "window_start_ts",
                   "window_end_ts", "hmac_hex", "sink_uri", "sink_push_ok"),
}

# Type 2 tunables.
REAPER_AGE_S = 30.0          # R: decision_requested older than this with no
                             # decision_made is an orphan (design §3, Pair B).
RIVER_ANOMALY_SLA_S = 60.0   # T_sla: decided-but-unconfirmed older than this
                             # renders PAGED_ANOMALY (design §3, river rule).
COMMIT_WATCHDOG_MS = 50.0    # §4: hot-path COMMIT budget. Trips only on a
                             # sick disk (~50x normal). Load-bearing: CI must
                             # have seen it fire (tests fault-inject it).
P99_WRITE_BUDGET_MS = 5.0    # §4: CI assertion bound on p99 event-write.

# D14 interim disk guard (ADR-024/O-1 — Type 2: reversible, simple, loud).
# The Vault-led retention RFC is the real answer; until it lands, a WAL-size
# watermark pages the operator + writes a spill record before the disk fills.
DISKGUARD_BYTES_DEFAULT = 100 * 1024 * 1024  # 100 MiB — conservative early
                             # warning: ~70k events of headroom at ~1.5 KB
                             # per event; weeks of runway at design-partner
                             # volumes, never a near-full-disk alarm.
DISKGUARD_ENV = "SENTINEL_DISKGUARD_BYTES"  # env override (bytes, integer).
DISKGUARD_CHECK_EVERY_S = 5.0  # hot path stats the files at most this often.
DISKGUARD_SPILL_SUBDIR = "disk-guard"  # under spillover_dir — kept apart
                             # from forward-replay spills so replay_spills
                             # never misreads a guard record as a PD send.


def utcnow_iso() -> str:
    """UTC ISO-8601 with millisecond precision (wall-clock, humans only)."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _canonical(obj: dict) -> bytes:
    """Canonical form: sorted keys, no whitespace, UTF-8 (design §5)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _row_hash(envelope_no_hashes: dict, body: dict, prev_hash: str) -> str:
    """sha256(canonical(envelope-without-hashes + body) || prev_hash)."""
    canonical = _canonical({**envelope_no_hashes, "body": body})
    return hashlib.sha256(canonical + prev_hash.encode("utf-8")).hexdigest()


def derive_dedup_key(env: str, fingerprint: str, episode_seq: int) -> str:
    """Stable vendor dedup_key (design 03 §3): sentinel/{env}/{fp}/{seq:04d}.

    Deterministic and human-debuggable. Falls back to the fixed 41-char
    hashed form when the composed key would exceed PD's 255-char limit.
    The SAME episode always yields the SAME key — across retries AND across
    process restarts — so crash-Pair-D retries append to the open PD alert
    instead of creating duplicates. Correctness property, tested.
    """
    composed = f"sentinel/{env}/{fingerprint}/{episode_seq:04d}"
    if len(composed) <= 255:
        return composed
    digest = hashlib.sha256(
        f"{env}|{fingerprint}|{episode_seq}".encode("utf-8")).hexdigest()[:32]
    return f"sentinel/{digest}"


def _resolve_diskguard_bytes(explicit: int | None) -> int:
    """D14 watermark resolution: explicit ctor arg -> env -> default.

    An explicit non-positive value is a programming error (fail loud at
    construction). A bad env value can never refuse to start the process —
    the guard is Type 2; it falls back to the default and says so on stderr.
    """
    if explicit is not None:
        if not isinstance(explicit, int) or isinstance(explicit, bool) \
                or explicit <= 0:
            raise EventLogError(
                f"diskguard_bytes must be a positive int, got {explicit!r}")
        return explicit
    raw = os.environ.get(DISKGUARD_ENV)
    if raw is None or not raw.strip():
        return DISKGUARD_BYTES_DEFAULT
    try:
        val = int(raw.strip())
        if val <= 0:
            raise ValueError("non-positive")
        return val
    except ValueError:
        print(f"[sentinel] {DISKGUARD_ENV}={raw!r} unparseable; using "
              f"default {DISKGUARD_BYTES_DEFAULT}", file=sys.stderr)
        return DISKGUARD_BYTES_DEFAULT


# ------------------------------------------------------------------ schema

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    schema_v     INTEGER NOT NULL DEFAULT 1,
    ts           TEXT NOT NULL,
    actor        TEXT NOT NULL,
    type         TEXT NOT NULL,
    alert_id     TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    episode_id   TEXT NOT NULL,
    outbox_id    TEXT,
    body         TEXT NOT NULL,
    prev_hash    TEXT NOT NULL,
    row_hash     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_fp    ON events(fingerprint, seq);
CREATE INDEX IF NOT EXISTS idx_events_alert ON events(alert_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_type  ON events(type, seq);

-- The durable outbox (design 03 §2.1). Same file as events: I1/I2 need one
-- transaction, and one transaction needs one database. Owned by the event-log
-- lane; the forwarder lane consumes it via record_receipt_and_update() and
-- the due-row query documented below.
CREATE TABLE IF NOT EXISTS outbox (
    outbox_id        TEXT PRIMARY KEY,
    alert_id         TEXT NOT NULL,
    fingerprint      TEXT NOT NULL,
    episode_id       TEXT NOT NULL,
    decision_seq     INTEGER NOT NULL,
    dedup_key        TEXT NOT NULL,
    routing_key_ref  TEXT NOT NULL,
    payload_frozen   TEXT NOT NULL,
    payload_sha256   TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued','in_flight','delivered','dead_letter')),
    priority         INTEGER NOT NULL DEFAULT 0,
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TEXT NOT NULL,
    lease_at         TEXT,
    secondary_fired_at TEXT,
    created_at       TEXT NOT NULL,
    delivered_at     TEXT,
    max_age_at       TEXT NOT NULL,
    last_error       TEXT
);
-- One live row per episode: a duplicate page decision for an already-queued
-- episode COALESCESES onto the existing row (its decision_made.outbox_id
-- points at it). No second row, no second PD alert (design 03 §2.1).
CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_episode_live
    ON outbox(episode_id) WHERE status IN ('queued', 'in_flight');
CREATE INDEX IF NOT EXISTS idx_outbox_due
    ON outbox(status, priority DESC, next_attempt_at);

-- Raw inbound payloads, keyed by input_sha256 (design §2.2 — Type 2).
-- Keeps event rows small; the payload itself never enters the chain.
CREATE TABLE IF NOT EXISTS raw_payloads (
    input_sha256  TEXT PRIMARY KEY,
    payload_bytes INTEGER NOT NULL,
    payload       TEXT NOT NULL,
    stored_at     TEXT NOT NULL
);

-- v0.1 compat: kept (harmless, Type 2). The tuner reads outcomes, not the log.
CREATE TABLE IF NOT EXISTS outcomes (
    alert_id     TEXT PRIMARY KEY,
    fingerprint  TEXT NOT NULL,
    became_sev12 INTEGER,
    auto_cleared INTEGER,
    mttr_min     REAL,
    labeled_at   TEXT
);
"""

# Disposable Type-2 shim (design §6.4): platform queries not yet migrated read
# this VIEW, never the table. Projections are never the truth.
#
# The VIEW also projects v0.1-compat columns (action, reason, q1_*, jev_model,
# input_sha256) from the body JSON so pre-migration call sites and their
# tests keep working through the migration window. The compat namespace
# lives under body.v01_compat — the design's top-level body vocabulary is
# untouched; only this disposable VIEW reaches into the compat corner.
_DECISIONS_VIEW = """
DROP VIEW IF EXISTS decisions;
CREATE VIEW decisions AS
SELECT seq        AS id,
       ts         AS received_at,
       ts         AS created_at,
       alert_id,
       fingerprint,
       episode_id,
       event_id,
       actor,
       type,
       outbox_id,
       body,
       prev_hash,
       row_hash,
       json_extract(body, '$.disposition')        AS action,
       json_extract(body, '$.v01_compat.reason')  AS reason,
       json_extract(body, '$.input_sha256')       AS input_sha256,
       json_extract(body, '$.jev_model')          AS jev_model,
       json_extract(body, '$.v01_compat.q1_choice') AS q1_severity,
       json_extract(body, '$.v01_compat.q1_probs')  AS q1_probs,
       json_extract(body, '$.v01_compat.q1_confidence') AS q1_conf
FROM events;
"""


class EventLogError(Exception):
    """The event log refused a write (validation, schema, invariant)."""


class WatchdogTrip(Exception):
    """Raised to the hot path when COMMIT exceeded the 50 ms budget (§4)."""


class EventLog:
    """Single-writer, append-only event log + outbox (SQLite WAL).

    Thread-safe: one connection, one lock — SQLite's single-writer is a
    feature here (design §5: no merge conflicts, ever). All hot-path
    writes go through _commit(), which the 50 ms watchdog times.
    """

    def __init__(self, db_path: str = "sentinel.db",
                 spillover_dir: str | None = None,
                 degraded_sender=None,
                 diskguard_bytes: int | None = None,
                 genesis_prev_hash: str = GENESIS_PREV_HASH):
        """
        spillover_dir: emergency spillover records land here on watchdog
            trips (design §4 degraded path). None disables file spillover.
        degraded_sender: callback(payload: dict) for the degraded direct
            inline send. The forwarder lane wires this; until then the
            spillover record + control-plane page carry the evidence.
        diskguard_bytes: D14 interim disk-guard watermark (ADR-024, Type 2).
            None -> SENTINEL_DISKGUARD_BYTES env -> 100 MiB default.
        genesis_prev_hash: D14 retention (RFC 2026-10-05) — chain-of-segments.
            A freshly-rolled segment starts its hash chain from the previous
            segment's head hash instead of GENESIS, so the whole history stays
            verifiable across segment files. Default GENESIS (unchanged
            behavior for the original single-file deployment).
        """
        self.db_path = db_path
        self.spillover_dir = spillover_dir
        self.degraded_sender = degraded_sender
        self.diskguard_bytes = _resolve_diskguard_bytes(diskguard_bytes)
        self._disk_guard_fired = False  # exactly-once per process (loud, not spammy)
        self._last_diskguard_check = 0.0
        self._lock = threading.RLock()
        # _test_commit_delay_s: fault-injection seam for the watchdog test.
        # Sleeps INSIDE the timed commit region. Test-only; never set in prod.
        self._test_commit_delay_s = 0.0
        self.metrics: dict[str, int] = {
            "events_written": 0,
            "watchdog_trips": 0,
            "evidence_loss_pages": 0,
            "reaper_redrives": 0,
            "shadow_drops": 0,
            "coalesced_duplicates": 0,
            "disk_guard_fires": 0,
        }
        # Re-entrancy guard: the degraded path's own commits must not
        # re-trip the watchdog (infinite recursion on a sick disk).
        self._in_watchdog_trip = False
        self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                     isolation_level=None)  # autocommit; we txn explicitly
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # The disposable decisions VIEW must not collide with a v0.1
            # `decisions` TABLE — migrate_v01() renames that table first and
            # re-creates the VIEW afterwards.
            tables = {r[0] for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "decisions" not in tables:
                self._conn.executescript(_DECISIONS_VIEW)
        # Chain head cache (single writer — no staleness possible).
        with self._lock:
            row = self._conn.execute(
                "SELECT seq, row_hash FROM events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        self._head_seq = row["seq"] if row else 0
        self._head_hash = (row["row_hash"] if row
                           else genesis_prev_hash)
        self._genesis_prev_hash = genesis_prev_hash

    def head(self) -> tuple[int, str]:
        """Current chain head: (seq, row_hash). D14 retention uses this to
        chain a rolled segment to its predecessor."""
        with self._lock:
            return self._head_seq, self._head_hash

    # ------------------------------------------------------------ internals

    def _commit(self) -> None:
        """COMMIT the open transaction under the 50 ms watchdog (design §4).

        On trip: the commit already happened (SQLite commits are synchronous
        — "abandoning the wait" is aspirational on one thread); the degraded
        path and the evidence-loss page are engaged synchronously inside
        _on_watchdog_trip (nothing raises WatchdogTrip — it is reserved, not
        raised). The event itself IS durable — this is recorded, not lost.
        """
        t0 = time.perf_counter()
        if self._test_commit_delay_s:
            # Fault-injection seam: simulate a sick disk INSIDE the timed
            # region so the watchdog sees the slow commit. Test-only.
            time.sleep(self._test_commit_delay_s)
        self._conn.execute("COMMIT;")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if elapsed_ms > COMMIT_WATCHDOG_MS and not self._in_watchdog_trip:
            self._in_watchdog_trip = True
            try:
                self._on_watchdog_trip(elapsed_ms)
            finally:
                self._in_watchdog_trip = False
        # D14 interim disk guard (ADR-024, Type 2): observes post-commit, so
        # the triage write above is already durable — the guard can never
        # block, delay, or sink a page. It never raises (see
        # check_disk_watermark's contract).
        self.check_disk_watermark()

    def _on_watchdog_trip(self, elapsed_ms: float) -> None:
        """Degraded path + evidence-loss page (design §4). Unsuppressible."""
        self.metrics["watchdog_trips"] += 1
        payload = {
            "kind": "evidence_loss",
            "reason": "commit_watchdog_trip",
            "commit_ms": round(elapsed_ms, 2),
            "budget_ms": COMMIT_WATCHDOG_MS,
            "ts": utcnow_iso(),
            "detail": ("Hot-path COMMIT exceeded 50 ms — the disk is sick. "
                       "The triggering event IS durable (commit completed); "
                       "this page exists because the commit-time bound that "
                       "keeps the WAL honest no longer holds."),
        }
        # 1. Emergency spillover record (durable evidence even if the DB dies
        #    right after — file 03 §6's degraded ladder).
        if self.spillover_dir:
            try:
                os.makedirs(self.spillover_dir, exist_ok=True)
                name = f"watchdog-{int(time.time()*1000)}.json"
                with open(os.path.join(self.spillover_dir, name), "w") as fh:
                    json.dump(payload, fh, sort_keys=True)
            except OSError:
                pass  # spillover is best-effort; the page below is the alarm
        # 2. Degraded direct inline send (wired by the forwarder lane).
        if self.degraded_sender is not None:
            try:
                self.degraded_sender(payload)
            except Exception:
                pass  # never let the degraded path sink the hot path
        # 3. Unsuppressible control-plane page via the outbox priority lane.
        try:
            self._enqueue_control_plane_page(
                kind="evidence_loss",
                summary=(f"audit degraded: commit watchdog tripped "
                         f"({elapsed_ms:.1f} ms > {COMMIT_WATCHDOG_MS:.0f} ms)"),
                detail=payload,
            )
            self.metrics["evidence_loss_pages"] += 1
        except Exception:
            pass  # disk-full etc: file 03's ladder + ADR-018 watcher are the backstop

    # ------------------------------------------------- D14 disk guard (Type 2)

    def _db_footprint_bytes(self) -> int:
        """On-disk footprint of the WAL-mode database: db + -wal + -shm.

        Missing files contribute 0 (":memory:" logs therefore never fire).
        """
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(self.db_path + suffix)
            except OSError:
                pass
        return total

    def check_disk_watermark(self, *, force: bool = False) -> bool:
        """D14 interim disk guard (ADR-024/O-1). True once it has fired.

        Runs on the hot path but can never affect it: it stats three files
        at most every DISKGUARD_CHECK_EVERY_S, does its work post-commit,
        latches exactly-once per process, and NEVER raises — any failure is
        swallowed after the latch, with the page/spill/stderr steps each
        individually best-effort.
        """
        try:
            return self._check_disk_watermark_inner(force=force)
        except Exception:
            return False

    def _check_disk_watermark_inner(self, *, force: bool) -> bool:
        if self._disk_guard_fired:
            return True
        now = time.monotonic()
        if not force and \
                now - self._last_diskguard_check < DISKGUARD_CHECK_EVERY_S:
            return False
        self._last_diskguard_check = now
        try:
            size = self._db_footprint_bytes()
        except Exception:
            return False
        if size < self.diskguard_bytes:
            return False
        self._fire_disk_guard(size)
        return True

    def _fire_disk_guard(self, size_bytes: int) -> None:
        """WAL-size watermark crossed: page the operator + spill over.

        The ruling's exact shape (ADR-024 conditions): control-plane page via
        the priority-1 outbox lane (one pager, prioritized — same mechanism
        as the commit watchdog) and a durable record via spill.py's existing
        write_spill, in a dedicated subdir so forward-replay never misreads
        it as a PD send. The latch is set FIRST: the enqueue below commits,
        which re-enters _commit -> check_disk_watermark, which must see the
        latch (exactly-once per process).
        """
        self._disk_guard_fired = True
        self.metrics["disk_guard_fires"] += 1
        detail = {
            "kind": "disk_guard",
            "reason": "wal_watermark_crossed",
            "db_path": self.db_path,
            "size_bytes": size_bytes,
            "watermark_bytes": self.diskguard_bytes,
            "ts": utcnow_iso(),
            "detail": ("Event-log WAL footprint crossed the interim D14 "
                       "disk-guard watermark. Triage is unaffected; this is "
                       "the ADR-024/O-1 early warning (the Vault-led "
                       "retention RFC is still open). Confirm the retention "
                       "policy or provision disk."),
        }
        # 1. Spill record (durable evidence; existing spill.py machinery).
        if self.spillover_dir:
            try:
                write_spill(
                    os.path.join(self.spillover_dir, DISKGUARD_SPILL_SUBDIR),
                    detail)
            except Exception:
                pass  # write_spill is best-effort already; the page is the alarm
        # 2. Control-plane page (priority-1 outbox row -> forwarder -> PD).
        try:
            self._enqueue_control_plane_page(
                kind="disk_guard",
                summary=(f"disk guard: event-log WAL footprint {size_bytes} "
                         f"bytes crossed watermark {self.diskguard_bytes} "
                         f"bytes (ADR-024 interim)"),
                detail=detail)
        except Exception:
            pass  # disk-sick: the spill + stderr below are the backstops
        # 3. Loud on stderr — unsuppressible, like the watchdog trip.
        print(f"[sentinel] DISK GUARD FIRED db={self.db_path} "
              f"size={size_bytes} watermark={self.diskguard_bytes}",
              file=sys.stderr)

    def _enqueue_control_plane_page(self, kind: str, summary: str,
                                    detail: dict) -> str:
        """Priority-1 outbox row for control-plane pages (design 03 §2.3).

        Same mechanism, ordered first — one pager, prioritized, not two.
        """
        outbox_id = uuid.uuid4().hex
        frozen = json.dumps({"kind": kind, "summary": summary,
                             "detail": detail}, sort_keys=True)
        now = utcnow_iso()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE;")
            try:
                self._conn.execute(
                    """INSERT INTO outbox
                       (outbox_id, alert_id, fingerprint, episode_id,
                        decision_seq, dedup_key, routing_key_ref,
                        payload_frozen, payload_sha256, status, priority,
                        attempt_count, next_attempt_at, created_at, max_age_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (outbox_id, f"control-plane/{kind}", "control-plane",
                     f"control-plane/{kind}/{now}", -1,
                     f"sentinel/control-plane/{kind}",
                     "secret:pd/control_routing_key", frozen,
                     hashlib.sha256(frozen.encode()).hexdigest(),
                     "queued", 1, 0, now, now, now),
                )
                self._commit()
            except BaseException:
                self._conn.execute("ROLLBACK;")
                raise
        return outbox_id

    def _validate(self, event_type: str, actor: str, body: dict) -> None:
        if event_type not in EVENT_TYPES:
            raise EventLogError(f"unknown event type: {event_type!r} "
                                f"(the 7 types are the Type-1 vocabulary)")
        if actor not in ACTORS:
            raise EventLogError(f"unknown actor: {actor!r}")
        if not isinstance(body, dict):
            raise EventLogError("body must be a JSON object")
        missing = [k for k in _BODY_REQUIRED[event_type] if k not in body]
        if missing:
            raise EventLogError(
                f"{event_type} body missing required keys: {missing}")
        if event_type == "decision_made":
            if body.get("disposition") not in DISPOSITIONS:
                raise EventLogError(
                    f"bad disposition: {body.get('disposition')!r}")
            if body.get("budget_outcome") not in BUDGET_OUTCOMES:
                raise EventLogError(
                    f"bad budget_outcome: {body.get('budget_outcome')!r}")

    def _insert_event(self, event_type: str, *, actor: str, alert_id: str,
                      fingerprint: str, episode_id: str,
                      outbox_id: str | None, body: dict,
                      ts: str | None = None) -> tuple[int, str]:
        """Insert one event into the OPEN transaction. Caller commits.

        (The hash chain is computed here too — sha256 over a small canonical
        JSON is microseconds; CI asserts p99 event-write < 5 ms, design §4.)
        """
        self._validate(event_type, actor, body)
        ts = ts or utcnow_iso()
        event_id = uuid.uuid4().hex
        envelope = {
            "event_id": event_id,
            "schema_v": SCHEMA_V,
            "ts": ts,
            "actor": actor,
            "type": event_type,
            "alert_id": alert_id,
            "fingerprint": fingerprint,
            "episode_id": episode_id,
            "outbox_id": outbox_id,
        }
        prev_hash = self._head_hash
        row_hash = _row_hash(envelope, body, prev_hash)
        cur = self._conn.execute(
            """INSERT INTO events
               (event_id, schema_v, ts, actor, type, alert_id, fingerprint,
                episode_id, outbox_id, body, prev_hash, row_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, SCHEMA_V, ts, actor, event_type, alert_id, fingerprint,
             episode_id, outbox_id, json.dumps(body, sort_keys=True),
             prev_hash, row_hash),
        )
        seq = cur.lastrowid
        self._head_seq, self._head_hash = seq, row_hash
        return seq, row_hash

    # ------------------------------------------------------- write API (I3)

    def append_event(self, event_type: str, *, actor: str, alert_id: str,
                     fingerprint: str, episode_id: str,
                     outbox_id: str | None = None, body: dict,
                     ts: str | None = None) -> int:
        """Append one event; commit; return seq. Generic writer for
        shadow_decision / flip_observed / checkpoint events and tests."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE;")
            # Blocker 1: snapshot the chain-head cache — if COMMIT fails,
            # the ROLLBACK below restores the row but the cache must be
            # restored too, or the next write chains from a phantom hash.
            _head_before = (self._head_seq, self._head_hash)
            try:
                seq, _ = self._insert_event(
                    event_type, actor=actor, alert_id=alert_id,
                    fingerprint=fingerprint, episode_id=episode_id,
                    outbox_id=outbox_id, body=body, ts=ts)
                self._commit()
            except BaseException:
                self._conn.execute("ROLLBACK;")
                self._head_seq, self._head_hash = _head_before
                raise
            self.metrics["events_written"] += 1
            return seq

    def record_request(self, *, alert_id: str, fingerprint: str,
                       episode_id: str, input_sha256: str,
                       source_integration: str, severity_in: str,
                       raw_payload_bytes: int,
                       received_ts: str | None = None) -> int:
        """I3 — write decision_requested and COMMIT. The receiver 202s ONLY
        after this returns (design §3, Pair A)."""
        return self.append_event(
            "decision_requested", actor="engine", alert_id=alert_id,
            fingerprint=fingerprint, episode_id=episode_id, body={
                "input_sha256": input_sha256,
                "source_integration": source_integration,
                "severity_in": severity_in,
                "received_ts": received_ts or utcnow_iso(),
                "raw_payload_bytes": raw_payload_bytes,
            })

    def store_raw_payload(self, input_sha256: str, payload: str) -> None:
        """Side table for inbound payloads (design §2.2, Type 2)."""
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO raw_payloads
                   (input_sha256, payload_bytes, payload, stored_at)
                   VALUES (?,?,?,?)""",
                (input_sha256, len(payload.encode("utf-8")), payload,
                 utcnow_iso()))

    # ------------------------------------------------------- write API (I1)

    def record_decision_and_enqueue(
            self, *, alert_id: str, fingerprint: str, episode_id: str,
            body: dict, outbox: dict | None) -> tuple[int, str | None]:
        """I1 — decision_made + outbox row in ONE transaction.

        body: the decision_made body per design §2.3 (outbox_id is filled
            in here — pass body WITHOUT outbox_id; it is set to the row's
            id, or the coalesced existing row's id).
        outbox: None for suppress (no outbox row, outbox_id stays NULL);
            otherwise the outbox row dict with keys: alert_id, fingerprint,
            episode_id, dedup_key, routing_key_ref, payload_frozen,
            payload_sha256, priority, next_attempt_at, max_age_at,
            optional attempt_count.
        Returns (decision_seq, outbox_id). Coalescing: a duplicate page
        decision for an already-live episode reuses the existing outbox row
        (design 03 §2.1) and the decision's outbox_id points at it.
        """
        body = dict(body)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE;")
            # Blocker 1: snapshot the chain-head cache (see append_event).
            _head_before = (self._head_seq, self._head_hash)
            try:
                outbox_id = None
                if outbox is not None:
                    outbox_id, coalesced = self._insert_outbox(outbox)
                    if coalesced:
                        self.metrics["coalesced_duplicates"] += 1
                body["outbox_id"] = outbox_id
                seq, _ = self._insert_event(
                    "decision_made", actor="engine", alert_id=alert_id,
                    fingerprint=fingerprint, episode_id=episode_id,
                    outbox_id=outbox_id, body=body)
                self._commit()
            except BaseException:
                self._conn.execute("ROLLBACK;")
                self._head_seq, self._head_hash = _head_before
                raise
            self.metrics["events_written"] += 1
            return seq, outbox_id

    def _insert_outbox(self, row: dict) -> tuple[str, bool]:
        """Insert an outbox row in the OPEN transaction. Returns
        (outbox_id, coalesced). Coalescing implements design 03 §2.1's
        UNIQUE(episode_id) WHERE live rule."""
        outbox_id = uuid.uuid4().hex
        now = utcnow_iso()
        try:
            self._conn.execute(
                """INSERT INTO outbox
                   (outbox_id, alert_id, fingerprint, episode_id,
                    decision_seq, dedup_key, routing_key_ref,
                    payload_frozen, payload_sha256, status, priority,
                    attempt_count, next_attempt_at, created_at, max_age_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?, ?,?,?,?)""",
                (outbox_id, row["alert_id"], row["fingerprint"],
                 row["episode_id"], row.get("decision_seq", -1),
                 row["dedup_key"], row["routing_key_ref"],
                 row["payload_frozen"], row["payload_sha256"],
                 row.get("status", "queued"), row.get("priority", 0),
                 row.get("attempt_count", 0), row["next_attempt_at"],
                 now, row["max_age_at"]),
            )
            return outbox_id, False
        except sqlite3.IntegrityError:
            # Duplicate page decision for a live episode → ride the
            # already-queued row (design 03 §2.1). No second row.
            existing = self._conn.execute(
                """SELECT outbox_id FROM outbox
                   WHERE episode_id = ? AND status IN ('queued','in_flight')
                   ORDER BY created_at LIMIT 1""",
                (row["episode_id"],)).fetchone()
            if existing is None:  # pragma: no cover — defensive
                raise
            return existing["outbox_id"], True

    # ------------------------------------------------------- write API (I2)

    def record_receipt_and_update(self, event_type: str, *, outbox_id: str,
                                  alert_id: str, fingerprint: str,
                                  episode_id: str, body: dict,
                                  scheduler: dict) -> int:
        """I2 — forward_confirmed/forward_failed + the outbox scheduler-column
        update in ONE transaction (design §3, Pair D). The event log and the
        work queue can never disagree about what happened — a database
        property, not a code convention.

        scheduler keys: status, next_attempt_at (optional), delivered_at
            (optional), attempt_count (optional), last_error (optional),
            secondary_fired_at (optional).
        """
        if event_type not in ("forward_confirmed", "forward_failed"):
            raise EventLogError(f"I2 takes a receipt type, got {event_type!r}")
        body = dict(body)
        body.setdefault("outbox_id", outbox_id)
        allowed = {"status", "next_attempt_at", "delivered_at",
                   "attempt_count", "last_error", "secondary_fired_at"}
        unknown = set(scheduler) - allowed
        if unknown:
            raise EventLogError(f"unknown scheduler keys: {sorted(unknown)}")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE;")
            # Blocker 1: snapshot the chain-head cache (see append_event).
            _head_before = (self._head_seq, self._head_hash)
            try:
                seq, _ = self._insert_event(
                    event_type, actor="forwarder", alert_id=alert_id,
                    fingerprint=fingerprint, episode_id=episode_id,
                    outbox_id=outbox_id, body=body)
                sets = ", ".join(f"{k} = ?" for k in scheduler)
                cur = self._conn.execute(
                    f"UPDATE outbox SET {sets} WHERE outbox_id = ?",
                    (*scheduler.values(), outbox_id))
                if cur.rowcount != 1:
                    raise EventLogError(
                        f"outbox row {outbox_id!r} not found for receipt")
                self._commit()
            except BaseException:
                self._conn.execute("ROLLBACK;")
                self._head_seq, self._head_hash = _head_before
                raise
            self.metrics["events_written"] += 1
            return seq

    def note_evidence_drop(self, kind: str = "shadow") -> None:
        """Count a dropped off-hot-path event (design §3, Pair F).

        A dropped shadow event is counted, and sustained loss pages via the
        evidence-loss machinery (wired by the coordinator). Calibration
        degrades gracefully; the paging path never depends on it.
        """
        self.metrics["shadow_drops"] += 1

    # ------------------------------------------------------------- reaper

    def startup_sweep(self) -> list[dict]:
        """Run the reaper once. Called at engine startup BEFORE the
        dispatcher resumes (design §3, Pair B)."""
        return Reaper(self).sweep()

    # ---------------------------------------------------------- reads

    def get_event(self, seq: int) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM events WHERE seq = ?", (seq,)).fetchone()
        if row is None:
            raise KeyError(f"no event seq {seq}")
        return dict(row)

    def events_for_alert(self, alert_id: str, limit: int = 1000) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM events WHERE alert_id = ? "
                "ORDER BY seq LIMIT ?", (alert_id, limit))
            return [dict(r) for r in cur.fetchall()]

    def events_of_type(self, event_type: str, limit: int = 1000) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM events WHERE type = ? "
                "ORDER BY seq LIMIT ?", (event_type, limit))
            return [dict(r) for r in cur.fetchall()]

    def head(self) -> tuple[int, str]:
        """(head_seq, head_hash) — the chain tip."""
        with self._lock:
            return self._head_seq, self._head_hash

    def outbox_row(self, outbox_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM outbox WHERE outbox_id = ?",
                (outbox_id,)).fetchone()
        if row is None:
            raise KeyError(f"no outbox row {outbox_id}")
        return dict(row)

    def due_outbox_rows(self, now_iso: str | None = None,
                       limit: int = 100) -> list[dict]:
        """Forwarder-lane query: due rows, priority first (design 03 §2.2)."""
        now_iso = now_iso or utcnow_iso()
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM outbox
                   WHERE status = 'queued' AND next_attempt_at <= ?
                   ORDER BY priority DESC, next_attempt_at LIMIT ?""",
                (now_iso, limit))
            return [dict(r) for r in cur.fetchall()]

    def undelivered_outbox_rows(self, limit: int = 1000) -> list[dict]:
        """Forwarder-lane startup scan: every non-delivered row (Pair C)."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM outbox
                   WHERE status IN ('queued','in_flight')
                   ORDER BY priority DESC, created_at LIMIT ?""",
                (limit,))
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------ the river fold

    def render_alert(self, alert_id: str,
                     now_iso: str | None = None) -> dict:
        """The river is a FOLD over the event stream (design §9.2), not a
        SELECT. Returns the honest state of one alert:

        requested | decided_unconfirmed | paged | suppressed |
        passthrough_decided | failed_unconfirmed | PAGED_ANOMALY | unknown

        THE RIVER RULE (design §3): a decision_made(page_*) with no matching
        forward_confirmed older than T_sla=60s renders PAGED_ANOMALY — red,
        pinned, and it fires the forwarder's watchdog. The transient state
        (crash windows) can exist; the SILENT state cannot.
        """
        now_iso = now_iso or utcnow_iso()
        events = self.events_for_alert(alert_id)
        state: dict = {"alert_id": alert_id, "status": "unknown",
                       "seq": 0, "outbox_id": None, "anomaly": False,
                       "detail": {}}
        if not events:
            return state
        bodies = [json.loads(e["body"]) for e in events]

        requested = [e for e, b in zip(events, bodies)
                     if e["type"] == "decision_requested"]
        decisions = [(e, b) for e, b in zip(events, bodies)
                     if e["type"] == "decision_made"]
        confirmed = {(e["outbox_id"], e["seq"]) for e in events
                     if e["type"] == "forward_confirmed" and e["outbox_id"]}
        failed = [e for e in events if e["type"] == "forward_failed"]

        state["seq"] = events[-1]["seq"]
        state["detail"]["n_requested"] = len(requested)
        state["detail"]["n_decisions"] = len(decisions)

        if decisions:
            dec, dbody = decisions[-1]  # latest decision is the truth
            disp = dbody.get("disposition")
            obid = dbody.get("outbox_id")
            state["outbox_id"] = obid
            state["detail"]["disposition"] = disp
            state["detail"]["budget_outcome"] = dbody.get("budget_outcome")
            is_confirmed = any(oid == obid for oid, _ in confirmed) \
                if obid else False
            if disp == "suppress":
                state["status"] = "suppressed"
            elif disp in ("page_now", "page_business_hours"):
                if is_confirmed:
                    state["status"] = "paged"
                else:
                    age_s = _iso_age_s(dec["ts"], now_iso)
                    if age_s is not None and age_s > RIVER_ANOMALY_SLA_S:
                        # THE RIVER RULE.
                        state["status"] = "PAGED_ANOMALY"
                        state["anomaly"] = True
                        state["detail"]["unconfirmed_age_s"] = round(age_s, 1)
                    else:
                        state["status"] = "decided_unconfirmed"
            elif disp == "passthrough":
                state["status"] = ("passthrough_unconfirmed"
                                   if obid and not is_confirmed
                                   else "passthrough_decided")
            elif disp == "folded":
                # D3: absorbed into the storm aggregate's page — neither
                # suppressed nor individually paged. Explicit status so the
                # river never misreads it as a pending decision.
                state["status"] = "folded_into_aggregate"
            if failed and not is_confirmed and disp in PAGE_DISPOSITIONS:
                state["detail"]["last_forward_error"] = json.loads(
                    failed[-1]["body"]).get("error_detail")
        elif requested:
            # Pair A: the sender retried; the correlator collapses via
            # fingerprint dedup — the projection shows ONE episode.
            state["status"] = "requested"
            state["detail"]["duplicate_receipts"] = len(requested) > 1
        return state

    def render_episode(self, episode_id: str,
                       now_iso: str | None = None) -> dict:
        """Fold over one episode (one alert_id in v0.1 terms)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT alert_id FROM events WHERE episode_id = ?",
                (episode_id,))
            alert_ids = [r[0] for r in cur.fetchall()]
        now_iso = now_iso or utcnow_iso()
        renders = [self.render_alert(a, now_iso) for a in alert_ids]
        anomaly = any(r["anomaly"] for r in renders)
        return {"episode_id": episode_id, "anomaly": anomaly,
                "alerts": renders}

    # ------------------------------------------------------------- close

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def decision_dispositions_since(self, since_seq: int,
                                    limit: int = 5000) -> tuple[list[dict], int]:
        """Tail decision_made dispositions for the ADR-022 watchdog.

        Returns ([{seq, ts_epoch, disposition, policy_id}], max_seq).
        policy_id defaults to "suppression" when the decision body does not
        carry one (the engine's single suppression policy).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, ts, body FROM events "
                "WHERE type = 'decision_made' AND seq > ? "
                "ORDER BY seq ASC LIMIT ?",
                (since_seq, limit)).fetchall()
        out: list[dict] = []
        max_seq = since_seq
        for seq, ts, body in rows:
            max_seq = max(max_seq, seq)
            try:
                b = json.loads(body)
            except ValueError:
                continue
            try:
                ts_epoch = datetime.fromisoformat(
                    ts.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                continue
            out.append({"seq": seq, "ts_epoch": ts_epoch,
                        "disposition": b.get("disposition"),
                        "policy_id": b.get("policy_id", "suppression")})
        return out, max_seq


def _iso_age_s(ts: str, now_iso: str) -> float | None:
    """Age in seconds between two ISO-8601 timestamps. None if unparsable."""
    try:
        a = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        b = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        return (b - a).total_seconds()
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------ reaper
# (Reaper class below)


def detect_flips(events: list[dict]) -> list[dict]:
    """Pure function of the log: find non-determinism (design §3, Pair G).

    Groups decision_made + shadow_decision answers by input_sha256 and
    reports differing q1/q3/disposition fields. The flip pipeline may live
    off the hot path precisely because it is re-derivable by replay —
    a crash loses at most one derived observation, never evidence.
    """
    by_input: dict[str, list[tuple[dict, dict]]] = {}
    for e in events:
        if e["type"] not in ("decision_made", "shadow_decision"):
            continue
        body = json.loads(e["body"])
        key = body.get("input_sha256")
        if key:
            by_input.setdefault(key, []).append((e, body))
    flips = []
    for key, answers in by_input.items():
        for i in range(len(answers)):
            for j in range(i + 1, len(answers)):
                (e1, b1), (e2, b2) = answers[i], answers[j]
                for field, v1, v2 in (
                        ("q1", b1.get("q1_reported"), b2.get("q1_reported")),
                        ("q3", b1.get("q3_confidence"),
                         b2.get("q3_confidence")),
                        ("disposition",
                         b1.get("disposition") or b1.get("decided_disposition"),
                         b2.get("disposition") or b2.get("shadow_disposition"))):
                    if v1 is not None and v2 is not None and v1 != v2:
                        flips.append({
                            "input_sha256": key,
                            "first_seq": e1["seq"], "second_seq": e2["seq"],
                            "differing_field": field,
                            "first_value": v1, "second_value": v2,
                        })
    return flips

class Reaper:
    """Pair-B crash-window closer (design §3).

    Sweeps decision_requested older than R=30s with no decision_made and
    redrives each through the gate as PASSTHROUGH (fail-open) with
    budget_outcome="reaper_redrive", in the I1 transaction (decision +
    outbox row, one txn).

    Why "older than R" is sound: the gate's worst-case processing time is
    B+eps+commit ≈ 2s << 30s (the race's bounded latency is what makes the
    age test sound — a composition named in design §3, Pair B). Without the
    race, "older than R" could not distinguish orphan from slow.
    """

    def __init__(self, log: EventLog, reaper_age_s: float = REAPER_AGE_S,
                 env: str = "prod"):
        self.log = log
        self.reaper_age_s = reaper_age_s
        self.env = env
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sweep(self, now_iso: str | None = None) -> list[dict]:
        """One sweep. Returns the redriven summaries."""
        now_iso = now_iso or utcnow_iso()
        orphans = self._find_orphans(now_iso)
        redriven = []
        for orphan in orphans:
            summary = self._redrive(orphan, now_iso)
            redriven.append(summary)
            self.log.metrics["reaper_redrives"] += 1
        return redriven

    def _find_orphans(self, now_iso: str) -> list[dict]:
        """decision_requested older than R with no decision_made answering it.

        The join is links.decision_requested_seq (design §2.3) — the
        decision names the request it answers, so the absence is a query,
        not a guess.
        """
        with self.log._lock:
            requested = self.log._conn.execute(
                "SELECT * FROM events WHERE type = 'decision_requested'"
            ).fetchall()
            answered = {
                json.loads(r["body"]).get("links", {}).get(
                    "decision_requested_seq")
                for r in self.log._conn.execute(
                    "SELECT body FROM events WHERE type = 'decision_made'")
            }
        orphans = []
        for r in requested:
            if r["seq"] in answered:
                continue
            age = _iso_age_s(r["ts"], now_iso)
            if age is not None and age > self.reaper_age_s:
                orphans.append(dict(r))
        return orphans

    def _redrive(self, orphan: dict, now_iso: str) -> dict:
        obody = json.loads(orphan["body"])
        alert_id = orphan["alert_id"]
        fingerprint = orphan["fingerprint"]
        episode_id = orphan["episode_id"]
        outbox_id = uuid.uuid4().hex
        frozen = json.dumps({
            "alert_id": alert_id,
            "reaper_redrive": True,
            "orphan_requested_seq": orphan["seq"],
            "input_sha256": obody.get("input_sha256"),
        }, sort_keys=True)
        seq, outbox_id = self.log.record_decision_and_enqueue(
            alert_id=alert_id, fingerprint=fingerprint, episode_id=episode_id,
            body={
                "input_sha256": obody.get("input_sha256"),
                "fingerprint": fingerprint,
                "episode_id": episode_id,
                "jev_model": None,          # no Jev call ran — fail-open
                "q1_reported": None,
                "q2_team": None,
                "q3_confidence": None,
                "q3_disposition": None,
                "disposition": "passthrough",
                "budget_outcome": "reaper_redrive",
                "latency_ms": None,
                "timer_fired_at_ms": None,
                "lock_evaluation": {"note": "reaper redrive: no lock "
                                            "evidence; fail-open"},
                "freshness": {"note": "reaper redrive: no freshness proofs"},
                "threshold_counterfactual": {},
                "links": {"decision_requested_seq": orphan["seq"]},
            },
            outbox={
                "alert_id": alert_id,
                "fingerprint": fingerprint,
                "episode_id": episode_id,
                "dedup_key": derive_dedup_key(
                    self.env, fingerprint, orphan["seq"]),
                "routing_key_ref": "secret:pd/routing_key",
                "payload_frozen": frozen,
                "payload_sha256": hashlib.sha256(
                    frozen.encode()).hexdigest(),
                "priority": 1,  # a crash-window page is control-plane urgent
                "next_attempt_at": now_iso,
                "max_age_at": now_iso,  # computed properly by forwarder lane
            })
        return {"orphan_requested_seq": orphan["seq"],
                "decision_seq": seq, "outbox_id": outbox_id,
                "alert_id": alert_id}

    def run_periodic(self, cadence_s: float = 60.0) -> threading.Thread:
        """Background sweep loop. The coordinator owns the thread lifecycle."""
        def _loop():
            while not self._stop.wait(cadence_s):
                try:
                    self.sweep()
                except Exception:
                    continue  # the reaper never sinks the engine
        self._thread = threading.Thread(target=_loop, name="sentinel-reaper",
                                        daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
