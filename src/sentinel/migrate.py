"""v0.1 migration — old rows become events without lying (design §6).

v0.1's `decisions` table recorded decisions but never forwards. The
migration (one-time, at upgrade):

1. Create `events` (+ envelope, §2.1).
2. For each v0.1 row, backfill ONE `decision_made` event:
   body.migrated_from = "v0.1-decisions",
   budget_outcome = "pre_race_unknown", actor = "migration",
   and NO forward_confirmed — we never observed the forward, and
   synthesizing a receipt for history we did not witness would be the
   exact lie this design exists to kill. (Type 1: the migration never
   synthesizes receipts.)
3. Rename the old table to `decisions_v0_1`, read-only, kept for one
   retention window.
4. Provide a `decisions` SQL VIEW over `events` for platform queries not
   yet migrated — disposable by definition; projections are never the
   truth. (Type 2: the view's shape.)

The river renders migrated rows as "decided (pre-event-log era) —
delivery unconfirmed" — honest, grey, never green.
"""

from __future__ import annotations

import json
import sqlite3

from .eventlog import EventLog, utcnow_iso

_MIGRATION_OUTCOME = "pre_race_unknown"
_MIGRATION_MARKER = "v0.1-decisions"


def migrate_v01(db_path: str) -> dict:
    """Run the v0.1 -> event-log migration. Idempotent (re-runs skip).

    Returns a summary dict: {"migrated": bool, "events_backfilled": int,
    "reason": str}.
    """
    probe = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in probe.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        views = {r[0] for r in probe.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")}
    finally:
        probe.close()

    if "decisions_v0_1" in tables and "decisions" in views:
        return {"migrated": False, "events_backfilled": 0,
                "reason": "already migrated"}
    if "decisions" not in tables:
        return {"migrated": False, "events_backfilled": 0,
                "reason": "no v0.1 decisions table present"}

    log = EventLog(db_path)
    try:
        rows = log._conn.execute("SELECT * FROM decisions").fetchall()
        cols = [d[0] for d in log._conn.execute(
            "SELECT * FROM decisions LIMIT 0").description]
        n = 0
        for row in rows:
            rec = dict(zip(cols, row))
            _backfill_one(log, rec)
            n += 1
        with log._lock:
            log._conn.execute("BEGIN IMMEDIATE;")
            try:
                log._conn.execute(
                    "ALTER TABLE decisions RENAME TO decisions_v0_1;")
                log._conn.execute("COMMIT;")
            except Exception:
                log._conn.execute("ROLLBACK;")
                raise
    finally:
        log.close()

    # Re-create the disposable decisions VIEW over events (EventLog.__init__
    # creates it too, but the RENAME above consumed the name).
    conn = sqlite3.connect(db_path)
    try:
        from .eventlog import _DECISIONS_VIEW
        conn.executescript(_DECISIONS_VIEW)
        conn.commit()
    finally:
        conn.close()
    return {"migrated": True, "events_backfilled": n,
            "reason": f"backfilled {n} decision_made events; old table "
                      f"renamed to decisions_v0_1"}


def _backfill_one(log: EventLog, rec: dict) -> None:
    """Backfill one v0.1 row as a decision_made event. Never synthesizes
    forward_confirmed (Type 1)."""
    legacy = {
        "q1": {"choice": rec.get("q1_severity"),
               "probs": _maybe_json(rec.get("q1_probs")),
               "confidence": rec.get("q1_conf")},
        "q2": {"choice": rec.get("q2_team"),
               "probs": _maybe_json(rec.get("q2_probs")),
               "confidence": rec.get("q2_conf")},
        "q3": {"choice": rec.get("q3_disposition"),
               "probs": _maybe_json(rec.get("q3_probs")),
               "confidence": rec.get("q3_conf")},
        "reason": rec.get("reason"),
        "received_at": rec.get("received_at"),
    }
    log.append_event(
        "decision_made", actor="migration",
        alert_id=rec.get("alert_id") or "unknown",
        fingerprint=rec.get("fingerprint") or "unknown",
        episode_id=rec.get("fingerprint") or "unknown",
        outbox_id=None,  # no receipt was ever observed — Type 1
        body={
            "input_sha256": rec.get("input_sha256"),
            "fingerprint": rec.get("fingerprint"),
            "episode_id": rec.get("fingerprint"),
            "jev_model": rec.get("jev_model"),
            "q1_reported": None,
            "q2_team": rec.get("q2_team"),
            "q3_confidence": rec.get("q3_conf"),
            "q3_disposition": rec.get("q3_disposition"),
            "disposition": rec.get("action") or "passthrough",
            "budget_outcome": _MIGRATION_OUTCOME,
            "latency_ms": rec.get("latency_ms"),
            "timer_fired_at_ms": None,
            "lock_evaluation": {
                "note": "pre-event-log era: no lock evidence recorded"},
            "freshness": {
                "note": "pre-event-log era: no freshness proofs"},
            "threshold_counterfactual": {},
            "outbox_id": None,
            "links": {"decision_requested_seq": None},
            "migrated_from": _MIGRATION_MARKER,
            "legacy": legacy,
        },
        ts=_coerce_ts(rec.get("created_at")))


def _maybe_json(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def _coerce_ts(value) -> str | None:
    """Keep the v0.1 created_at as the event ts when it looks sane."""
    if isinstance(value, str) and len(value) >= 10:
        return value
    return None


if __name__ == "__main__":  # one-shot CLI for the upgrade runbook
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "sentinel.db"
    print(json.dumps(migrate_v01(db), indent=2))
