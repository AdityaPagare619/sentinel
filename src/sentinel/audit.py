"""AuditLog — SQLite decision log (§3.8).

Append-only: there are no UPDATE/DELETE methods by design. Schema is
Postgres-compatible (plain types) for the SaaS upgrade. Thread-safe for the
ThreadingHTTPServer receiver (single connection + lock, check_same_thread off).
"""

from __future__ import annotations

import json
import sqlite3
import threading

from .models import DecisionRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY,
    received_at   TEXT NOT NULL,
    alert_id      TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    input_sha256  TEXT NOT NULL,
    jev_model     TEXT,
    q1_severity   TEXT,
    q1_probs      TEXT,
    q1_conf       REAL,
    q2_team       TEXT,
    q2_probs      TEXT,
    q2_conf       REAL,
    q3_disposition TEXT,
    q3_probs      TEXT,
    q3_conf       REAL,
    action        TEXT NOT NULL,
    reason        TEXT NOT NULL,
    latency_ms    REAL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_fp ON decisions(fingerprint);
CREATE TABLE IF NOT EXISTS outcomes (
    alert_id     TEXT PRIMARY KEY,
    fingerprint  TEXT NOT NULL,
    became_sev12 INTEGER,
    auto_cleared INTEGER,
    mttr_min     REAL,
    labeled_at   TEXT
);
"""


def _answer_cols(answer) -> tuple:
    """Flatten an Answer (or None) to (choice, probs_json, confidence)."""
    if answer is None:
        return None, None, None
    probs = getattr(answer, "probabilities", None)
    return (
        getattr(answer, "choice", None),
        json.dumps(probs, sort_keys=True) if probs else None,
        getattr(answer, "confidence", None),
    )


class AuditLog:
    def __init__(self, db_path: str = "sentinel.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------- append-only

    def record(self, rec: DecisionRecord) -> int:
        """Insert one decision row. Returns the row id."""
        alert = rec.alert
        disp = rec.disposition
        q1 = _answer_cols(rec.q_severity)
        q2 = _answer_cols(rec.q_team)
        q3 = _answer_cols(rec.q_disposition)
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO decisions
                   (received_at, alert_id, fingerprint, input_sha256, jev_model,
                    q1_severity, q1_probs, q1_conf,
                    q2_team, q2_probs, q2_conf,
                    q3_disposition, q3_probs, q3_conf,
                    action, reason, latency_ms)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    alert.received_at,
                    alert.alert_id,
                    alert.fingerprint,
                    rec.input_sha256,
                    rec.jev_model,
                    q1[0], q1[1], q1[2],
                    q2[0], q2[1], q2[2],
                    q3[0], q3[1], q3[2],
                    disp.action,
                    disp.reason,
                    disp.latency_ms,
                ),
            )
            self._conn.commit()
            return cur.lastrowid

    # ------------------------------------------------------------------ reads

    def get(self, row_id: int) -> dict:
        """Fetch one decision row by id. Raises KeyError if missing."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM decisions WHERE id = ?", (row_id,)
            )
            row = cur.fetchone()
        if row is None:
            raise KeyError(f"no decision row {row_id}")
        return dict(row)

    def decisions_for_fingerprint(self, fp: str, limit: int = 100) -> list[dict]:
        """Most recent decisions for a fingerprint, newest first."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM decisions WHERE fingerprint = ? "
                "ORDER BY id DESC LIMIT ?",
                (fp, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
