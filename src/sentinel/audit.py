"""AuditLog — v0.1-compat facade over the append-only event log (ADR-011).

The frozen v0.1 `decisions` row table is gone: decisions are now
`decision_made` EVENTS in the hash-chained log (sentinel.eventlog), and
"paged" is only ever `forward_confirmed`. This class keeps the v0.1 call
sites (gate.py, receiver.py) and their tests working while the gate lane
migrates to the I1 API (EventLog.record_decision_and_enqueue — decision +
outbox in one transaction).

Mapping notes (honest, documented):
  * record() writes a decision_made event, actor="engine", outbox_id=None.
    It does NOT enqueue — the gate lane owns enqueue via the I1 API.
  * budget_outcome: "answered_in_time" when a Jev model answered,
    "error_passthrough" when the reason is error:*, else
    "structural_passthrough" (deterministic pre-Jev path — no model ran).
  * lock_evaluation / freshness carry an explicit "pre-lock era" note:
    v0.1 never recorded lock evidence, and the shim does not invent it.
  * get()/decisions_for_fingerprint() read the disposable `decisions` VIEW
    over events (design §6.4) — projections are never the truth.

Append-only: there are no UPDATE/DELETE methods by design.
Thread-safe: delegates to EventLog's single-writer lock.
"""

from __future__ import annotations

import json
import sqlite3

from .eventlog import EventLog

_PRE_LOCK_NOTE = "v0.1 compat: no lock evidence (pre-lock era)"
_NO_FRESHNESS_NOTE = "v0.1 compat: no freshness proofs (pre-lock era)"


def _budget_outcome(rec) -> str:
    reason = rec.disposition.reason or ""
    if reason.startswith("error:"):
        return "error_passthrough"
    if rec.jev_model is not None:
        return "answered_in_time"
    return "structural_passthrough"


def _answer_choice(answer):
    return getattr(answer, "choice", None) if answer is not None else None


def _answer_confidence(answer):
    return getattr(answer, "confidence", None) if answer is not None else None


class AuditLog:
    def __init__(self, db_path: str = "sentinel.db"):
        self.db_path = db_path
        self._log = EventLog(db_path)

    # ------------------------------------------------------------- append-only

    def record(self, rec) -> int:
        """Write one decision_made event. Returns the event seq."""
        disp = rec.disposition
        return self._log.append_event(
            "decision_made", actor="engine",
            alert_id=rec.alert.alert_id,
            fingerprint=rec.alert.fingerprint,
            episode_id=rec.alert.fingerprint,  # v0.1 had no episodes
            outbox_id=None,
            body={
                "input_sha256": rec.input_sha256,
                "fingerprint": rec.alert.fingerprint,
                "episode_id": rec.alert.fingerprint,
                "jev_model": rec.jev_model,
                "q1_reported": None,  # v0.1 recorded choices+probs, not the
                                      # quantized reported P(p1); not invented
                "q2_team": _answer_choice(rec.q_team),
                "q3_confidence": _answer_confidence(rec.q_disposition),
                "q3_disposition": _answer_choice(rec.q_disposition),
                "disposition": disp.action,
                "budget_outcome": _budget_outcome(rec),
                "latency_ms": disp.latency_ms,
                "timer_fired_at_ms": None,
                "lock_evaluation": {"note": _PRE_LOCK_NOTE},
                "freshness": {"note": _NO_FRESHNESS_NOTE},
                "threshold_counterfactual": {},
                "outbox_id": None,
                "links": {"decision_requested_seq": None},
            })

    # ------------------------------------------------------------------ reads

    def get(self, row_id: int) -> dict:
        """Fetch one decision from the decisions VIEW by id (= event seq).
        Raises KeyError if missing."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM decisions WHERE id = ?", (row_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(f"no decision row {row_id}")
        return dict(row)

    def decisions_for_fingerprint(self, fp: str, limit: int = 100) -> list[dict]:
        """Most recent decision_made events for a fingerprint, newest first."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "SELECT * FROM decisions WHERE fingerprint = ? "
                "AND type = 'decision_made' ORDER BY id DESC LIMIT ?",
                (fp, limit))
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    @property
    def log(self) -> EventLog:
        """The underlying event log (for lanes that need the I1/I2/I3 API)."""
        return self._log

    def close(self) -> None:
        self._log.close()


def body_of(row: dict) -> dict:
    """Parse the VIEW's body JSON column."""
    return json.loads(row["body"])
