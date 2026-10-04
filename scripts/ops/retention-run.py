#!/usr/bin/env python3
"""retention-run.py — the retention job's caller (D14).

`RetentionJob.apply()` had no caller: the tiers were implemented but the
job never ran. This script is the entrypoint. Run it from cron:

    0 2 * * * /usr/bin/python3 /opt/sentinel/scripts/ops/retention-run.py \
        --live-db /var/lib/sentinel/sentinel.db \
        --warm-dir /var/lib/sentinel/warm --cold-dir /var/lib/sentinel/cold \
        --ledger /var/lib/sentinel/segment-ledger.json >>/var/log/sentinel/retention.log 2>&1

The HMAC sealing key comes from the environment (never a flag, never a
file the repo owns):

    SENTINEL_RETENTION_HMAC_KEY=<64 hex chars>

Exit codes (cron/monitoring contract):
  0 — plan applied (or dry-run clean), no pages raised.
  1 — the plan raised pages (under-provisioned, seal failure, 26h SLO
      breach): an operator must look. The plan JSON is printed to stdout.
  2 — misconfiguration / hard error (missing key, unreadable DB).

Dry-run is the default: `--apply` is required to actually move tiers.
Run `--dry-run` first after any config change (the RFC's shadow mode).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from sentinel.eventlog import EventLog
from sentinel.retention import RetentionConfig, RetentionJob, SegmentLedger


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the D14 retention job.")
    ap.add_argument("--live-db", required=True, help="live event-log SQLite path")
    ap.add_argument("--warm-dir", required=True)
    ap.add_argument("--cold-dir", required=True)
    ap.add_argument("--ledger", required=True, help="segment ledger JSON path")
    ap.add_argument("--customer", default=None, help="per-customer tier overrides")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="shadow mode: full plan, zero deletes (default)")
    ap.add_argument("--apply", action="store_true",
                    help="actually move tiers (required for writes)")
    args = ap.parse_args()

    key_hex = os.environ.get("SENTINEL_RETENTION_HMAC_KEY", "")
    if not key_hex:
        print("retention-run: SENTINEL_RETENTION_HMAC_KEY is not set — refusing.",
              file=sys.stderr)
        return 2
    try:
        hmac_key = bytes.fromhex(key_hex)
    except ValueError:
        print("retention-run: SENTINEL_RETENTION_HMAC_KEY is not valid hex — refusing.",
              file=sys.stderr)
        return 2
    if len(hmac_key) < 32:
        print("retention-run: HMAC key < 32 bytes — refusing.", file=sys.stderr)
        return 2

    dry_run = not args.apply  # --apply is the only way to write
    try:
        log = EventLog(db_path=args.live_db)
        config = RetentionConfig()
        if args.customer:
            config = config.for_customer(args.customer)
        ledger = SegmentLedger(args.ledger)
        job = RetentionJob(log, config, ledger, args.warm_dir, args.cold_dir,
                           hmac_key)
        plan = job.apply(dry_run=dry_run)
    except Exception as exc:  # fail loud, never silent
        print(f"retention-run: ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(plan, indent=2, sort_keys=True, default=str))
    if plan.get("pages"):
        print(f"retention-run: {len(plan['pages'])} page(s) raised — operator attention required.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
