#!/usr/bin/env python3
"""D13 first recorded standby drill + cutover runbook rehearsal (ADR-018).

Executes docs/runbook-cutover.md end to end against the LOCAL stack and
writes the timestamped record to docs/drills/drill-2026-10-05.md.

HONESTY CONTRACT (hard):
  - The PagerDuty endpoint is STUBBED (ops/drills/stub_pd.py) — a 202 +
    {"status":"success"} responder. It is NOT PagerDuty. The record says so.
  - The secondary channel is STUBBED (a second recording stub).
  - The human ack is SIMULATED by the drill operator (an agent). It is
    labeled as such everywhere. A real human-acked drill is REQUIRED before
    the first prod cutover (ADR-018 condition) — this drill does not satisfy
    that.
  - Drill completions ARE really written to the event log and read back.

Run: python3 ops/drills/d13_cutover_drill.py [--record PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
_SRC = os.path.join(_REPO, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sentinel.eventlog import EventLog, utcnow_iso  # noqa: E402
from sentinel.forwarder import DurableForwarder, ForwarderConfig  # noqa: E402
from sentinel.secondary import (  # noqa: E402
    DrillTracker,
    SecondaryConfig,
    SecondaryNotReady,
    WebhookSecondary,
    require_drilled_secondary,
)

IST = timezone(timedelta(hours=5, minutes=30))
STUB_PD = os.path.join(_REPO, "ops", "drills", "stub_pd.py")
CONTROL_REF = "secret:pd/control_routing_key"
CONTROL_ENV = "D13_DRILL_CONTROL_KEY"
PRIMARY_REF = "secret:pd/drill"
PRIMARY_ENV = "D13_DRILL_PD_KEY"


def ts() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")


class Recorder:
    def __init__(self):
        self.lines: list[str] = []

    def step(self, text: str) -> None:
        line = f"[{ts()}] {text}"
        print(line, flush=True)
        self.lines.append(line)


def free_port() -> int:
    """A port nothing is listening on — the simulated 'primary is dead'
    endpoint. (Connection-refused within milliseconds on loopback.)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_stub(port_label: str):
    out = tempfile.NamedTemporaryFile(
        prefix=f"d13-stub-{port_label}-", suffix=".jsonl", delete=False)
    out.close()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, STUB_PD, "--port", str(port), "--out", out.name],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/v2/enqueue"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        raise RuntimeError(f"stub {port_label} did not start on port {port}")
    return proc, url, out.name


def stub_records(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def _frozen(summary="d13-drill synthetic page", severity="critical") -> str:
    return json.dumps({
        "event_action": "trigger",
        "dedup_key": "sentinel/drill/d13/fp1/0001",
        "payload": {"summary": summary, "severity": severity,
                    "source": "d13-drill", "component": "drill",
                    "custom_details": {"drill": True, "lane": "d13"}},
    }, sort_keys=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record",
                    default=os.path.join(
                        _REPO, "docs", "drills", "drill-2026-10-05.md"))
    args = ap.parse_args()
    rec = Recorder()
    rec.step("DRILL START — D13 standby drill + cutover runbook rehearsal "
             "(ADR-018). Local stack only.")

    tmp = tempfile.mkdtemp(prefix="sentinel-d13-drill-")
    db = os.path.join(tmp, "sentinel.db")
    spill_dir = os.path.join(tmp, "spill")
    drill_path = os.path.join(tmp, "drills.jsonl")

    # Drill-scoped fake credentials (STUBBED — not real secrets).
    os.environ[CONTROL_ENV] = "d13-drill-control-key-STUB"
    os.environ[PRIMARY_ENV] = "d13-drill-primary-key-STUB"

    # --- Step 1: boot local stack -----------------------------------------
    rec.step("Step 1 — boot local stack: file-backed EventLog + two "
             "recording stubs.")
    log = EventLog(db)
    pd_proc, pd_stub_url, pd_stub_out = start_stub("pd")
    sec_proc, sec_stub_url, sec_stub_out = start_stub("secondary")
    rec.step(f"  stub PagerDuty endpoint (STUBBED, not PagerDuty): "
             f"{pd_stub_url}")
    rec.step(f"  stub secondary webhook (STUBBED): {sec_stub_url}")

    # --- Step 2: kill the primary forwarder path ---------------------------
    rec.step("Step 2 — KILL the primary forwarder path: point the forwarder "
             "at a dead port (simulates PD down / network partition).")
    dead_port = free_port()
    rec.step(f"  primary endpoint = http://127.0.0.1:{dead_port}/v2/enqueue "
             f"(nothing listening — connection-refused)")
    cfg_primary = ForwarderConfig(
        env="d13-drill", pd_endpoint=f"http://127.0.0.1:{dead_port}/v2/enqueue",
        pd_timeout_s=3.0, workers=1, poll_s=3600,
        spill_dir=spill_dir, stage="shadow",
        secret_mapping={PRIMARY_REF: PRIMARY_ENV, CONTROL_REF: CONTROL_ENV},
        drill_record_path=drill_path)
    fw_primary = DurableForwarder(log, cfg_primary)

    frozen = _frozen()
    now = utcnow_iso()
    _, obid = log.record_decision_and_enqueue(
        alert_id="d13-a1", fingerprint="d13-fp1", episode_id="d13-ep1",
        body={"disposition": "page_now", "budget_outcome": "answered_in_time",
              "lock_evaluation": {}, "freshness": {},
              "threshold_counterfactual": {}, "links": {}},
        outbox={"alert_id": "d13-a1", "fingerprint": "d13-fp1",
                "episode_id": "d13-ep1",
                "dedup_key": "sentinel/drill/d13/fp1/0001",
                "routing_key_ref": PRIMARY_REF,
                "payload_frozen": frozen,
                "payload_sha256": hashlib.sha256(frozen.encode()).hexdigest(),
                "priority": 0, "next_attempt_at": now,
                "max_age_at": utcnow_iso()})
    rec.step(f"  synthetic alert enqueued to outbox (outbox_id={obid[:8]}...)")
    rec.step("  draining once via run_once_sync() against the dead endpoint...")
    fw_primary.run_once_sync()
    row = log.outbox_row(obid)
    assert row["status"] != "delivered", (
        f"PRIMARY PATH LEAKED: row delivered via a dead endpoint?! "
        f"status={row['status']}")
    rec.step(f"  EXPECTED: primary attempt failed (connection-refused → "
             f"retryable). Row status={row['status']!r}, "
             f"attempt_count={row['attempt_count']}, NOT delivered. "
             f"Stub PD received {len(stub_records(pd_stub_out))} requests "
             f"(expected 0 — the primary path is dead).")
    assert not stub_records(pd_stub_out), "stub PD saw traffic on dead path!"

    # --- Step 3: fire the standby direct-to-PD path ------------------------
    rec.step("Step 3 — STANDBY: fire send_direct (outbox bypass) against the "
             "STUBBED PagerDuty endpoint (reason=drill:T1_primary_dead).")
    cfg_standby = ForwarderConfig(
        env="d13-drill", pd_endpoint=pd_stub_url, pd_timeout_s=5.0,
        workers=1, spill_dir=spill_dir, stage="shadow",
        secret_mapping={PRIMARY_REF: PRIMARY_ENV, CONTROL_REF: CONTROL_ENV},
        drill_record_path=drill_path)
    fw_standby = DurableForwarder(log, cfg_standby)
    payload = {"kind": "drill_standby_page", "alert_id": "d13-a1",
               "fingerprint": "d13-fp1", "episode_id": "d13-ep1",
               "summary": "D13 drill standby page: primary PD path dead",
               "dedup_key": "sentinel/drill/d13/standby/0001"}
    outcome = fw_standby.send_direct(
        payload, reason="drill:T1_primary_pd_unreachable")
    rec.step(f"  send_direct returned: pd_outcome={outcome['pd_outcome']!r}, "
             f"pd_status={outcome.get('pd_status')}, "
             f"latency_ms={outcome.get('latency_ms')}, "
             f"spill={os.path.basename(outcome['spill'] or 'NONE')}")
    assert outcome["pd_outcome"] == "accepted", (
        f"standby send not accepted by stub: {outcome}")
    assert outcome.get("pd_status") == 202
    assert outcome["spill"] and os.path.exists(outcome["spill"]), (
        "emergency spill record missing — audit repair path broken")
    with open(outcome["spill"], encoding="utf-8") as fh:
        spill_rec = json.load(fh)
    assert spill_rec["reason"] == "drill:T1_primary_pd_unreachable"
    stub_hits = stub_records(pd_stub_out)
    assert len(stub_hits) == 1, f"expected exactly 1 stub PD hit, got {len(stub_hits)}"
    hit = stub_hits[0]
    rec.step(f"  EXPECTED: stub PD recorded exactly 1 POST "
             f"(status={hit['status']}, dedup_key={hit['dedup_key']!r}). "
             f"Emergency spill written + parseable (reason field verified).")
    rec.step("  >> STUBBED: this 202 {status:success} came from "
             "ops/drills/stub_pd.py — NOT from PagerDuty. No real page went "
             "anywhere.")

    # --- Step 4: secondary-channel drill ----------------------------------
    rec.step("Step 4 — SECONDARY drill: send a drill page via the stubbed "
             "secondary webhook, then ack.")
    sec_cfg = SecondaryConfig(
        type="webhook", provider="d13-drill-stub-secondary",
        destination=sec_stub_url, credential_ref=None,
        wakefulness=("D13 drill: the stub secondary records the POST and "
                     "replies 202. It does NOT prove a human's phone rang; "
                     "reachability of the real channel is unproven."))
    sec = WebhookSecondary(sec_cfg)
    drills = DrillTracker(drill_path)
    fw_sec = DurableForwarder(log, cfg_standby, secondary=sec, drills=drills)
    drill_id = fw_sec.run_secondary_drill()
    rec.step(f"  drill started (drill_id={drill_id[:8]}...); secondary send "
             f"resulted in {len(stub_records(sec_stub_out))} stub receipt(s).")
    assert len(stub_records(sec_stub_out)) == 1, "secondary stub saw no POST"

    rec.step("Step 5 — human ack: SIMULATED human ack by drill operator "
             "(agent).")
    acked = fw_sec.ack_secondary_drill(drill_id)
    rec.step(f"  ack_secondary_drill -> {acked} (window: 10 min).")
    assert acked, "simulated ack landed outside the 10-minute window"
    rec.step("  >> HONESTY: this was a SIMULATED human ack by the drill "
             "operator (agent) — a real human-acked drill is REQUIRED before "
             "the first prod cutover (ADR-018 condition).")

    # --- Step 6: cutover gate ---------------------------------------------
    rec.step("Step 6 — CUTOVER GATE: require_drilled_secondary(stage-2c).")
    try:
        require_drilled_secondary("stage-2c", sec_cfg, drills)
        rec.step("  CUTOVER GATE PASS: secondary configured, last drill "
                 "passed <30d ago with ack inside 10 min.")
    except SecondaryNotReady as exc:
        rec.step(f"  CUTOVER GATE REFUSED (unexpected): {exc}")
        raise
    rec.step("  Negative control: a tracker with NO drill history must "
             "refuse cutover.")
    stale = DrillTracker(os.path.join(tmp, "empty.jsonl"))
    try:
        require_drilled_secondary("stage-2c", sec_cfg, stale)
        raise AssertionError("gate passed with no drill history — refused?")
    except SecondaryNotReady as exc:
        rec.step(f"  EXPECTED refusal: {exc}")

    # --- Step 7: event-log the drill completions --------------------------
    # The event-log vocabulary is a closed Type-1 set (7 types); inventing
    # "drill_completed" here would be a vocabulary change out of lane scope.
    # The standby drill's completion IS event-logged through the design's own
    # audit-repair path: replay the emergency spill (exactly what
    # startup_scan does at boot) -> forward_confirmed with
    # channel="direct-degraded". The secondary drill completion lives in the
    # drill-tracker JSONL — the record store require_drilled_secondary reads
    # (Tripwire's "drilled" verifiability condition).
    rec.step("Step 7 — drill completions event-logged (ADR-018 condition).")
    from sentinel.spill import replay_spills  # noqa: E402
    rec.step("  replaying the standby spill (boot-time audit repair path)...")
    replayed = replay_spills(log, spill_dir)
    assert len(replayed) == 1, f"expected 1 replayed spill, got {replayed}"
    rec.step(f"  replayed: event={replayed[0]['event']!r} "
             f"seq={replayed[0]['seq']}")
    assert replayed[0]["event"] == "forward_confirmed"
    got = [e for e in log.events_of_type("forward_confirmed")
           if json.loads(e["body"]).get("channel") == "direct-degraded"]
    bodies = [json.loads(e["body"]) for e in got]
    assert any(b.get("vendor_status") == 202 for b in bodies), (
        "standby completion not retrievable in the event log")
    rec.step(f"  VERIFIED retrievable: forward_confirmed with "
             f"channel='direct-degraded' present in the event log "
             f"({len(got)} matching). Standby drill completion is "
             f"event-logged for real.")
    rec.step("  Secondary drill completion: recorded in the drill-tracker "
             "JSONL (the store the cutover gate reads). No fitting Type-1 "
             "event type exists, so it is NOT shoehorned into the log — a "
             "dedicated drill event type is flagged as follow-up for the "
             "event-log lane (Type-1 vocabulary change needs its own ADR).")
    rec.step("  Drill tracker record (verifiable per Tripwire): " +
             json.dumps([json.loads(l) for l in
                         open(drill_path, encoding="utf-8")
                         if json.loads(l).get("event") in
                         ("started", "passed")]))

    # --- cleanup + record ---------------------------------------------------
    fw_primary._conn.close()
    fw_standby._conn.close()
    fw_sec._conn.close()
    for proc in (pd_proc, sec_proc):
        proc.terminate()
        proc.wait(timeout=5)
    rec.step("DRILL COMPLETE — all steps passed. Writing record.")

    os.makedirs(os.path.dirname(args.record), exist_ok=True)
    with open(args.record, "w", encoding="utf-8") as fh:
        fh.write("# D13 — First recorded standby drill (ADR-018)\n\n")
        fh.write("**Drill:** standby direct-to-PD + secondary-channel cutover "
                 "rehearsal\n")
        fh.write("**Runbook executed:** `docs/runbook-cutover.md`\n")
        fh.write("**Harness:** `ops/drills/d13_cutover_drill.py` "
                 "(local stack only)\n")
        fh.write("**Date:** 2026-10-05 (IST)\n\n")
        fh.write("## Verified vs stubbed\n\n")
        fh.write("- **Verified for real:** primary-drain failure mode "
                 "(connection-refused → retryable, row undelivered); "
                 "`send_direct` standby path (outbox bypass, 202/accepted "
                 "handling, emergency spill write + parse); standby "
                 "completion event-logged through the design's own "
                 "spill-replay path (`forward_confirmed` with "
                 "channel='direct-degraded', read back from the log); "
                 "secondary drill send + 10-min ack window mechanics; "
                 "`require_drilled_secondary` cutover gate (passes fresh, "
                 "refuses stale). The secondary drill completion is "
                 "recorded in the drill-tracker JSONL (the store the gate "
                 "reads); the event-log vocabulary is a closed Type-1 set "
                 "with no fitting drill event type — a dedicated type is "
                 "flagged as follow-up for the event-log lane.\n")
        fh.write("- **STUBBED (not claimed):** the PagerDuty endpoint "
                 "(`ops/drills/stub_pd.py` — a 202 "
                 '`{"status":"success"}` responder, not PagerDuty); the '
                 "secondary webhook (recording stub); the human ack "
                 "— **SIMULATED by the drill operator (agent)**. A real "
                 "human-acked drill is REQUIRED before the first prod "
                 "cutover (ADR-018 condition).\n\n")
        fh.write("## Step log\n\n")
        for line in rec.lines:
            fh.write(f"- {line}\n")
        fh.write("\n## Artifacts (ephemeral, drill-local)\n\n")
        fh.write(f"- state dir: `{tmp}` (temp; destroyed with the drill)\n")
        fh.write(f"- stub PD records: `{pd_stub_out}`\n")
        fh.write(f"- stub secondary records: `{sec_stub_out}`\n")
        fh.write(f"- drill tracker JSONL: `{drill_path}`\n")
    rec.step(f"record written: {args.record}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
