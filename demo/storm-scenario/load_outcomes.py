#!/usr/bin/env python3
"""Load synthetic reference labels into the engine DB's outcomes table.

The platform's calibration panel joins decisions ⨝ outcomes (the Ledger's
join). In production the Ledger writes real incident labels; for the demo
rehearsal we load the synthetic generator's ground truth — the same
precedent as the platform's own labels-v3 ("seeded synthetic labels;
data_source=synthetic, loudly").

Honesty contract:
- labeled_at is the rehearsal timestamp; the rows are synthetic reference
  labels, never presented as real incident outcomes.
- The platform envelope already carries data_source=synthetic.

Usage:
    PYTHONPATH=src python3 demo/storm-scenario/load_outcomes.py \\
        --db demo/storm-scenario/storm.db --seed 42
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from sentinel.synthetic import generate_alerts  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.execute("""CREATE TABLE IF NOT EXISTS outcomes (
        alert_id    TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        became_sev12 INTEGER,
        auto_cleared INTEGER,
        mttr_min    REAL,
        labeled_at  TEXT NOT NULL
    )""")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    for alert, label in generate_alerts(40, args.seed):
        con.execute(
            "INSERT OR REPLACE INTO outcomes "
            "(alert_id, fingerprint, became_sev12, auto_cleared, mttr_min,"
            " labeled_at) VALUES (?,?,?,?,?,?)",
            (alert.alert_id, alert.fingerprint,
             int(label["became_sev12"]), int(label["auto_cleared"]),
             float(label.get("mttr_min") or 0.0), now),
        )
        n += 1
    con.commit()
    con.close()
    print(f"loaded {n} synthetic outcomes (labeled_at={now})")


if __name__ == "__main__":
    main()
