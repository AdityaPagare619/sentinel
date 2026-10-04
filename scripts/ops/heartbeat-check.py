#!/usr/bin/env python3
"""heartbeat-check.py — per-source dead-man's-switch for Sentinel (A3).

Reads the event log (SQLite) DIRECTLY — a file read, never an HTTP call
through the receiver. The verdict must not traverse the thing it watches:
if the receiver is down, this script still runs and reports the log stopped
growing.

Subjects: every `source_integration` seen on `decision_requested` events,
plus the synthetic `__pipeline__` subject (any event at all = the pipeline
is writing). Expectations come from heartbeats.json (versioned, in the
config dir):

    {"version": 1,
     "sources": {
        "pagerduty:prod":      {"expected_interval_s": 300,
                                "stale_after_s": 1800, "silent_after_s": 3600},
        "alertmanager:eu-west":{"expected_interval_s": 60,
                                "stale_after_s": 300,  "silent_after_s": 420}},
     "defaults": {"expected_interval_s": 3600,
                  "stale_after_s": 7200, "silent_after_s": 14400}}

States per subject:
  ok      — last event within expected_interval_s
  stale   — beyond expected, within stale_after_s (warning: investigate low-pri)
  silent  — beyond silent_after_s (dead-man TRIP: page)
  unknown — no rows for this subject yet (never cries wolf on missing
            instrumentation; the receiver may not emit decision_requested yet)

Exit codes (Nagios-style): 0 = all ok/unknown, 1 = any stale, 2 = any silent.

Usage:
  heartbeat-check.py --db SENTINEL_DB --heartbeats heartbeats.json [--json]
                     [--now ISO8601]   # --now is for tests only

L3 DevOps foundation, A3 (Petu binding 2026-10-04). Design: ops/devops-foundation.md §10.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys

HEARTBEATS_VERSION = 1
PIPELINE_SUBJECT = "__pipeline__"


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_ts(s: str) -> datetime.datetime:
    # Event-log ts values are ISO-8601 UTC ("...Z" or offset-aware).
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def load_expectations(path: str) -> dict:
    with open(path) as f:
        data = json.load(f)
    if data.get("version") != HEARTBEATS_VERSION:
        raise SystemExit(f"FAIL: {path}: version must be "
                         f"{HEARTBEATS_VERSION}")
    return data


def last_seen_by_subject(db_path: str) -> dict[str, datetime.datetime]:
    """Newest event ts per subject. Reads the DB read-only (URI mode)."""
    if not os.path.exists(db_path):
        raise SystemExit(f"FAIL: event-log DB not found: {db_path}")
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        tables = {r[0] for r in
                  con.execute("SELECT name FROM sqlite_master "
                              "WHERE type IN ('table','view')")}
        if "events" not in tables:
            raise SystemExit(f"FAIL: {db_path} has no events table")
        out: dict[str, datetime.datetime] = {}
        # Per-source: decision_requested carries the required source_integration.
        for (src, ts) in con.execute(
                "SELECT json_extract(body,'$.source_integration'), MAX(ts) "
                "FROM events WHERE type='decision_requested' "
                "GROUP BY json_extract(body,'$.source_integration')"):
            if src:
                out[str(src)] = _parse_ts(ts)
        # Pipeline liveness floor: any event at all.
        row = con.execute("SELECT MAX(ts) FROM events").fetchone()
        if row and row[0]:
            out[PIPELINE_SUBJECT] = _parse_ts(row[0])
    finally:
        con.close()
    return out


def evaluate(expect: dict, last_seen: dict,
             now: datetime.datetime) -> dict:
    defaults = expect.get("defaults", {})
    subjects_cfg = expect.get("sources", {})
    # Union: configured subjects plus any observed-but-unconfigured source
    # (an unconfigured source that suddenly appears is reported, using defaults).
    names = sorted(set(subjects_cfg) | set(last_seen) - {PIPELINE_SUBJECT})
    report: dict[str, dict] = {}
    worst = 0  # 0 ok, 1 stale, 2 silent

    def check(name: str, cfg: dict, seen: datetime.datetime | None):
        nonlocal worst
        exp = {**defaults, **cfg}
        if seen is None:
            return {"state": "unknown", "age_s": None,
                    "note": "no events observed for this subject"}
        age = (now - seen).total_seconds()
        if age <= exp["expected_interval_s"]:
            state, code = "ok", 0
        elif age <= exp["stale_after_s"]:
            state, code = "stale", 1
        else:
            # silent_after_s is the dead-man trip; anything beyond stale but
            # under silent stays "stale".
            state, code = ("silent", 2) if age > exp["silent_after_s"] \
                else ("stale", 1)
        worst = max(worst, code)
        return {"state": state, "age_s": round(age, 1),
                "last_seen": seen.isoformat(),
                "expected_interval_s": exp["expected_interval_s"],
                "stale_after_s": exp["stale_after_s"],
                "silent_after_s": exp["silent_after_s"]}

    for name in names:
        report[name] = check(name, subjects_cfg.get(name, {}),
                             last_seen.get(name))
    # The pipeline subject always uses defaults.
    report[PIPELINE_SUBJECT] = check(PIPELINE_SUBJECT, {},
                                    last_seen.get(PIPELINE_SUBJECT))
    return {"subjects": report, "worst": worst}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="per-source heartbeat check (A3)")
    ap.add_argument("--db", required=True, help="event-log SQLite path")
    ap.add_argument("--heartbeats", required=True,
                    help="heartbeats.json expectations file")
    ap.add_argument("--json", action="store_true",
                    help="print the full per-subject report as JSON")
    ap.add_argument("--now", default=None,
                    help="override now (ISO-8601, tests only)")
    args = ap.parse_args(argv)

    expect = load_expectations(args.heartbeats)
    now = _parse_ts(args.now) if args.now else _utcnow()
    last_seen = last_seen_by_subject(args.db)
    result = evaluate(expect, last_seen, now)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for name in sorted(result["subjects"]):
            s = result["subjects"][name]
            age = (f"{s['age_s']}s ago"
                   if s["age_s"] is not None else "never")
            print(f"{s['state'].upper():7} {name} (last event {age})")
    states = {s["state"] for s in result["subjects"].values()}
    if "silent" in states:
        print("DEAD-MAN TRIP: a source is silent — page the on-call (RB-8)",
              file=sys.stderr)
    elif "stale" in states:
        print("warning: a source is stale — investigate low-priority (RB-8)",
              file=sys.stderr)
    return result["worst"]


if __name__ == "__main__":
    sys.exit(main())
