#!/usr/bin/env python3
"""Dead-man's-switch watcher for the suppression watchdog (ADR-022, D8).

This is a SEPARATE process from the watchdog, in a different fate domain.
It shares nothing with the watchdog except the heartbeat file contract and
this script's one import (pd_sender). It must never import watchdog.py,
policy_lifecycle.py, or eventlog.py — a bug in the watchdog's code must not
be able to break its watcher.

It reads the heartbeat JSON file written by watchdog.HeartbeatEmitter and:

  - exit 0: heartbeat fresh.
  - exit 1: heartbeat stale but within clock-skew tolerance (warning; the
    next run may page — this is the grace, not the verdict).
  - exit 2: heartbeat missing or stale beyond tolerance -> page the human
    via PagerDutyClient.send_event DIRECTLY. This path does not traverse the
    watchdog, the forwarder, or the outbox. If the watchdog is dead, this is
    the only thing standing between the operator and silence.

Clock skew: if the heartbeat timestamp is in the FUTURE beyond tolerance,
the clocks disagree and the dead-man's semantics are void — page with
reason suspected_clock_skew (fail toward the human; a skewed clock is
itself an incident).

Dedup: the page uses a stable dedup_key per watchdog_id so repeated runs
coalesce on the vendor side instead of page-storming.

Deployment: run on a fixed interval (cron/systemd, <= heartbeat interval)
from a host/process that does not share fate with the watchdog process.
The routing key comes from SENTINEL_WATCHDOG_ROUTING_KEY (env); if unset,
exit 2 LOUD — a watcher that cannot page is theater, and theater must alarm.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from sentinel import pd_sender  # noqa: E402  (the ONE allowed import)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--heartbeat-file", required=True,
                   help="path to the watchdog heartbeat JSON file")
    p.add_argument("--interval-s", type=float, default=60.0,
                   help="expected heartbeat interval seconds")
    p.add_argument("--miss-factor", type=float, default=3.0,
                   help="stale beyond interval*miss-factor -> page")
    p.add_argument("--skew-tolerance-s", type=float, default=120.0,
                   help="clock-skew grace before paging")
    p.add_argument("--routing-key", default=os.environ.get(
        "SENTINEL_WATCHDOG_ROUTING_KEY", ""),
                   help="PD routing key (or env SENTINEL_WATCHDOG_ROUTING_KEY)")
    p.add_argument("--watchdog-id", default="watchdog-1")
    p.add_argument("--no-page", action="store_true",
                   help="dry run: report what would happen, do not page")
    return p.parse_args()


def _read_beat(path: str) -> tuple[dict | None, str | None]:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError) as exc:
        return None, f"unreadable: {exc}"


def _page(routing_key: str, watchdog_id: str, summary: str,
          detail: dict, dry_run: bool) -> bool:
    """Page via PagerDuty direct. Returns True if the page was accepted."""
    if dry_run:
        print(f"[watchdog-watcher] DRY RUN would page: {summary}")
        return True
    if not routing_key:
        print("[watchdog-watcher] CRITICAL: no routing key — cannot page. "
              "Set SENTINEL_WATCHDOG_ROUTING_KEY.", file=sys.stderr)
        return False
    event = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": f"sentinel/watchdog-heartbeat/{watchdog_id}",
        "payload": {
            "summary": summary,
            "severity": "critical",
            "source": "sentinel-watchdog-watcher",
            "component": "suppression-watchdog",
            "custom_details": detail,
        },
    }
    client = pd_sender.PagerDutyClient()
    try:
        result = client.send_event(
            event=event,
            dedup_key=event["dedup_key"],
            routing_key=routing_key)
    except Exception as exc:  # noqa: BLE001 — report, don't crash
        print(f"[watchdog-watcher] page attempt raised: {exc}",
              file=sys.stderr)
        return False
    ok = result.outcome == "accepted"
    print(f"[watchdog-watcher] page outcome={result.outcome} "
          f"detail={result.error or 'ok'}")
    return ok


def main() -> int:
    args = _parse_args()
    now = time.time()
    stale_after = args.interval_s * args.miss_factor

    beat, problem = _read_beat(args.heartbeat_file)
    if beat is None:
        summary = (f"suppression watchdog heartbeat {problem}: "
                   f"{args.heartbeat_file}")
        detail = {"watchdog_id": args.watchdog_id, "problem": problem,
                  "checked_at": _dt.datetime.fromtimestamp(
                      now, _dt.timezone.utc).isoformat()}
        paged = _page(args.routing_key, args.watchdog_id, summary, detail,
                      args.no_page)
        print(f"[watchdog-watcher] CRITICAL {summary} page_accepted={paged}")
        return 2

    beat_epoch = float(beat.get("at_epoch", 0))
    age = now - beat_epoch
    detail = {"watchdog_id": beat.get("watchdog_id", args.watchdog_id),
              "seq": beat.get("seq"), "beat_at": beat.get("at"),
              "age_s": round(age, 1), "stale_after_s": stale_after}

    # Future-dated heartbeat: the clocks disagree. Beyond tolerance the
    # dead-man's semantics are void -> page. Within tolerance -> WARNING:
    # a future beat is still an anomaly worth investigating before it
    # becomes a page.
    if age < -args.skew_tolerance_s:
        summary = (f"suppression watchdog heartbeat from the future "
                   f"(skew {age:.0f}s) — suspected clock skew")
        detail["reason"] = "suspected_clock_skew"
        paged = _page(args.routing_key, args.watchdog_id, summary, detail,
                      args.no_page)
        print(f"[watchdog-watcher] CRITICAL {summary} page_accepted={paged}")
        return 2
    if age < 0:
        print(f"[watchdog-watcher] WARNING heartbeat {age:.0f}s in the "
              f"future (within skew tolerance)")
        return 1

    if age > stale_after + args.skew_tolerance_s:
        summary = (f"suppression watchdog heartbeat stale: last beat "
                   f"{age:.0f}s ago (seq {beat.get('seq')})")
        paged = _page(args.routing_key, args.watchdog_id, summary, detail,
                      args.no_page)
        print(f"[watchdog-watcher] CRITICAL {summary} page_accepted={paged}")
        return 2

    if age > stale_after:
        print(f"[watchdog-watcher] WARNING heartbeat aging: "
              f"{age:.0f}s (tolerance covers skew)")
        return 1

    print(f"[watchdog-watcher] OK heartbeat age {age:.1f}s "
          f"(seq {beat.get('seq')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
