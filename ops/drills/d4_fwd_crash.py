#!/usr/bin/env python3
"""DRILL 4 — process death between event-log write and forward.

Phase A: enqueue a page decision (I1: decision_made + outbox row, one txn),
  start the DurableForwarder with crash_after_send=True -> the worker POSTs
  to the stub PD (202 accepted) then os._exit(1) BEFORE the I2 receipt.
  The log must show decision_made and NO forward_confirmed.
Phase B (new process): startup_scan() requeues the stranded in_flight row,
  run_once_sync() redelivers -> forward_confirmed. The log must read
  decision_made -> forward_failed(crash_window_requeue) -> forward_confirmed,
  the stub must have seen the SAME dedup_key twice (at-least-once + dedup,
  never a double page, never a silent forward_confirmed).

Bar: restart replays the outbox; the audit NEVER claims a forward_confirmed
that didn't happen.
"""
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from sentinel.eventlog import EventLog, utcnow_iso
from sentinel.forwarder import DurableForwarder, ForwarderConfig
from sentinel.pd_sender import PagerDutyClient

PD_STUB = "http://127.0.0.1:8099/v2/enqueue"


def enqueue(log: EventLog) -> str:
    frozen = json.dumps({
        "event_action": "trigger",
        "payload": {"summary": "drill 4 crash-window page",
                    "severity": "critical", "source": "drill",
                    "component": "drill", "custom_details": {}},
    }, sort_keys=True)
    now = utcnow_iso()
    later = utcnow_iso()  # placeholder; replaced below with +1h
    import datetime
    max_age = (datetime.datetime.now(datetime.timezone.utc)
               + datetime.timedelta(hours=1)).isoformat()
    seq, outbox_id = log.record_decision_and_enqueue(
        alert_id="d4-crash-1", fingerprint="d4fp000000000001",
        episode_id="d4ep000000000001",
        body={"input_sha256": "d4" * 32,
              "disposition": "page_now",
              "budget_outcome": "answered_in_time",
              "lock_evaluation": {"leg1": "n/a-drill"},
              "freshness": {"note": "drill"},
              "threshold_counterfactual": {},
              "links": {"decision_requested_seq": None}},
        outbox={"alert_id": "d4-crash-1",
                "fingerprint": "d4fp000000000001",
                "episode_id": "d4ep000000000001",
                "dedup_key": "sentinel/drill/d4/0001",
                "routing_key_ref": "PD_ROUTING_KEY",
                "payload_frozen": frozen,
                "payload_sha256": hashlib.sha256(frozen.encode()).hexdigest(),
                "priority": 0,
                "next_attempt_at": now,
                "max_age_at": max_age})
    return outbox_id


def phase_a(db: str):
    os.environ["PD_ROUTING_KEY"] = "drill-routing-key"
    log = EventLog(db)
    obid = enqueue(log)
    print(f"phase A: enqueued outbox {obid}")
    cfg = ForwarderConfig(env="drill", pd_endpoint=PD_STUB,
                          workers=1, poll_s=0.5, crash_after_send=True,
                          secret_mapping={"PD_ROUTING_KEY": "PD_ROUTING_KEY"})
    fwd = DurableForwarder(log, config=cfg,
                           pd_client=PagerDutyClient(endpoint=PD_STUB,
                                                     timeout_s=5.0))
    fwd.start()
    # The worker should crash the process within seconds. If we're still
    # alive after 12s, the crash hook didn't fire: loud failure.
    time.sleep(12)
    print("phase A: STILL ALIVE after 12s — crash hook did NOT fire. FAIL.")
    os._exit(2)


def phase_b(db: str, fwd_log_path: str):
    os.environ["PD_ROUTING_KEY"] = "drill-routing-key"
    log = EventLog(db)
    cfg = ForwarderConfig(env="drill", pd_endpoint=PD_STUB,
                          workers=1, poll_s=60.0, crash_after_send=False,
                          secret_mapping={"PD_ROUTING_KEY": "PD_ROUTING_KEY"})
    fwd = DurableForwarder(log, config=cfg,
                           pd_client=PagerDutyClient(endpoint=PD_STUB,
                                                     timeout_s=5.0))
    # 1. Fresh-lease honesty: the crash was seconds ago, so the 300s lease
    #    is still valid — startup must NOT steal it (a live worker might
    #    still hold it). Expect requeued == 0.
    scan_fresh = fwd.startup_scan()
    print(f"phase B: startup_scan (fresh lease — must not steal) -> {scan_fresh}")

    # 2. Simulate the operator restarting ~6 minutes after the crash:
    #    expire the lease directly in the drill-owned DB (exactly what time
    #    passage does — the check compares lease_at against now).
    import datetime
    import sqlite3
    stale_lease = (datetime.datetime.now(datetime.timezone.utc)
                   - datetime.timedelta(seconds=360)).isoformat()
    conn = sqlite3.connect(db)
    conn.execute("update outbox set lease_at = ? where status = 'in_flight'",
                 (stale_lease,))
    conn.commit()
    conn.close()
    scan_stale = fwd.startup_scan()
    print(f"phase B: startup_scan (stale lease — must requeue) -> {scan_stale}")
    claimed = fwd.run_once_sync()
    print(f"phase B: run_once_sync claimed {claimed} row(s)")

    events = log.events_for_alert("d4-crash-1")
    types = [e["type"] for e in events]
    confirmed = [e for e in events if e["type"] == "forward_confirmed"]
    failed = [e for e in events if e["type"] == "forward_failed"]
    row = log.outbox_row(events[0]["outbox_id"])

    # stub evidence: dedup keys seen
    keys = []
    with open(fwd_log_path) as fh:
        for line in fh:
            try:
                keys.append(json.loads(line).get("dedup_key"))
            except Exception:
                pass

    report = {
        "event_sequence": types,
        "startup_scan_fresh_lease": scan_fresh,
        "startup_scan_stale_lease": scan_stale,
        "run_once_claimed": claimed,
        "n_forward_confirmed": len(confirmed),
        "n_forward_failed": len(failed),
        "failed_classes": [json.loads(e["body"]).get("error_class")
                           for e in failed],
        "outbox_status": row["status"],
        "stub_posts": len(keys),
        "stub_dedup_keys": keys,
    }
    print(json.dumps(report, indent=1))
    ok = (
        scan_fresh["requeued"] == 0
        and scan_stale["requeued"] == 1
        and claimed == 1
        and types == ["decision_made", "forward_failed", "forward_confirmed"]
        and len(confirmed) == 1
        and report["failed_classes"] == ["crash_window_requeue"]
        and row["status"] == "delivered"
        and len(keys) == 2
        and len(set(keys)) == 1  # same dedup_key: retry, not a double page
    )
    print("verdict:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "A":
        phase_a(sys.argv[2])
    else:
        sys.exit(phase_b(sys.argv[2], sys.argv[3]))
