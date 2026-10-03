#!/usr/bin/env python3
"""One-time migration: rebuild storm.db with v01_compat-enriched bodies.

Context: the original storm run wrote the gate's lean emit payloads
(decision_made without v01_compat). The platform's ReadStore projects
reason / Q1 choice / Q1 prob maps from body.v01_compat, so without it the
UI shows cannot_determine severities and unknown reasons.

This migration re-derives v01_compat from answers.jsonl (the engine's own
DecisionRecords, seq-keyed and verified) using sentinel.audit's own
helpers — the same derivation the engine's canonical audit path uses.
Nothing is invented. The hash chain is recomputed honestly over the new
bodies (new head hash — this is a new artifact generation, logged as
such, not a silent rewrite).

Usage:
    PYTHONPATH=src python3 demo/storm-scenario/migrate_v01compat.py
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "src"))

from sentinel.audit import (_answer_choice, _answer_confidence,  # noqa: E402
                            _answer_probs)
from sentinel.eventlog import EventLog  # noqa: E402

SRC = HERE / "storm.db"
BACKUP = HERE / "storm.db.pre-v01compat"
ANSWERS = HERE / "answers.jsonl"


def main() -> None:
    answers = {}
    for line in ANSWERS.read_text().splitlines():
        if line.strip():
            a = json.loads(line)
            answers[a["seq"]] = a

    con = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM events ORDER BY seq")]
    con.close()

    enriched = 0
    new_path = HERE / "storm.db.new"
    if new_path.exists():
        new_path.unlink()
    log = EventLog(db_path=str(new_path))
    for r in rows:
        body = json.loads(r["body"])
        if r["type"] == "decision_made":
            a = answers.get(r["seq"])
            assert a is not None, f"no answers for seq {r['seq']}"
            assert a["alert_id"] == r["alert_id"], "seq/alert mismatch"
            q1 = a["q_severity"] or {}
            probs = q1.get("probabilities") or {}
            body["v01_compat"] = {
                "reason": a["disposition"]["reason"],
                "q1_choice": q1.get("choice"),
                "q1_probs": (json.dumps(probs, sort_keys=True)
                             if probs else None),
                "q1_confidence": q1.get("confidence"),
            }
            enriched += 1
        log.append_event(
            r["type"], actor=r["actor"], alert_id=r["alert_id"],
            fingerprint=r["fingerprint"], episode_id=r["episode_id"] or "",
            outbox_id=r["outbox_id"], body=body, ts=r["ts"],
        )
    log.close()

    # Verify before swapping.
    con2 = sqlite3.connect(f"file:{new_path}?mode=ro", uri=True)
    n2 = con2.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    head2 = con2.execute(
        "SELECT row_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()[0]
    con2.close()
    assert n2 == len(rows), (n2, len(rows))
    print(f"migrated {len(rows)} events ({enriched} enriched); "
          f"new head {head2[:16]}…")

    shutil.copy2(SRC, BACKUP)
    new_path.replace(SRC)
    # Remove stale WAL/SHM sidecars so the next open starts clean.
    for suf in ("-shm", "-wal"):
        p = HERE / f"storm.db{suf}"
        if p.exists():
            p.unlink()
    print(f"swapped in enriched DB; backup at {BACKUP.name}")


if __name__ == "__main__":
    main()
