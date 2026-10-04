#!/usr/bin/env python3
"""DRILL 7 — kill the Jev path 5 minutes before showtime.

Scenario: the model vendor is unreachable (connection refused). The demo's
critical path must continue without a skipped beat:
  1. race-to-page still pages (fail-open: error_passthrough), every alert
     disposed, every page forwarded, no hangs;
  2. the flip beat degrades to its honestly-labeled recorded fallback
     (or an honest limits beat when no recording exists);
  3. river/report/receipts keep working off the local event log
     (reads, hash-chain, flip detection, disposition fold).

Bar: all three phases pass, zero exceptions, zero silent drops.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
# The flip-beat artifact under test lives in the demo worktree (read-only
# reuse — the drill validates the real artifact, not a copy).
sys.path.insert(0, "/home/hatch/workspace/jev-builds/sentinel-sun-demo/rehearsal")

from sentinel.audit import AuditLog
from sentinel.client import SystemOneClient
from sentinel.config import Thresholds
from sentinel.correlator import Correlator
from sentinel.eventlog import EventLog, detect_flips
from sentinel.forwarder import Forwarder
from sentinel.gate import Gate
from sentinel.receiver import Pipeline, ReceiverConfig

from flip_beat import (  # noqa: E402  (demo-lane artifact, validated here)
    RECORDED_LABEL_TEMPLATE,
    assert_honest,
    render_beat,
)

N = 15
BUDGET_S = 30  # per-alert wall ceiling: the race budget bounds everything


def pd_payload(i):
    return json.dumps({
        "routing_key": "drill-routing-key",
        "event_action": "trigger",
        "dedup_key": f"d7-{i}",
        "payload": {
            "summary": f"d7 showtime alert {i}",
            "source": f"svc-{i % 3}",
            "severity": "critical",
            "component": f"svc-{i % 3}",
            "class": f"check-{i}",
            "group": "region-0",
            "region": "region-0",
        },
    }).encode()


def phase1_pipeline(db, fwd_log):
    """Jev unreachable -> every alert still pages. Returns (ok, detail)."""
    client = SystemOneClient(api_key="dummy",
                             base_url="http://127.0.0.1:9",  # dead
                             # ADR-015: Jev-unreachable drill — the client
                             # never gets an answer, so the pin is moot;
                             # explicit, loudly-warned opt-out (drill only).
                             allow_floating_model=True)
    audit = AuditLog(db)
    gate = Gate(client, Thresholds(), set(), audit)
    fwd = Forwarder(
        pd_events_url=os.environ.get("PD_EVENTS_URL",
                                     "http://127.0.0.1:8099/v2/enqueue"),
        timeout_s=5.0, default_routing_key="drill-routing-key")
    pipe = Pipeline(Correlator(), gate, fwd, audit, ReceiverConfig())
    walls = []
    for i in range(N):
        t0 = time.monotonic()
        resp = pipe.handle_pd(pd_payload(i))
        walls.append((time.monotonic() - t0))
        assert resp["status"] == "success", resp
    gate.close()
    audit.close()
    import sqlite3
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "select body from decisions where type='decision_made'").fetchall()
    acts = {}
    for (body,) in rows:
        b = json.loads(body)
        acts[b["disposition"]] = acts.get(b["disposition"], 0) + 1
    keys = [json.loads(l).get("dedup_key") for l in open(fwd_log)]
    d7keys = [k for k in keys if str(k).startswith("d7-")]
    detail = {"n": N, "actions": acts, "max_wall_s": round(max(walls), 2),
              "stub_receipts": len(d7keys)}
    ok = (acts.get("passthrough", 0) == N
          and len(d7keys) == N
          and max(walls) < BUDGET_S)
    return ok, detail


def phase2_flipbeat():
    """Flip beat with a dead live path -> labeled fallback / limits beat."""
    from sentinel.evalharness import build_eval_state
    from sentinel.questions import build_questions
    from sentinel.synthetic import generate_alerts
    pairs = generate_alerts(40, 42)
    flip_idx = next(i for i, (_, lab) in enumerate(pairs)
                    if lab["severity_true"] == "known_noise")
    alert, _ = pairs[flip_idx]
    state = build_eval_state(alert)
    questions = build_questions()

    class DeadClient(SystemOneClient):
        def __init__(self):
            # ADR-015: dead-client drill — explicit, loudly-warned
            # opt-out (drill only; the client never answers).
            super().__init__(api_key="x", base_url="http://127.0.0.1:9",
                             allow_floating_model=True)

    import glob
    recs = sorted(glob.glob(
        "/home/hatch/workspace/jev-builds/sentinel-sun-demo/rehearsal/"
        "flip-beat-recording-*.json"))
    checks = {}
    if recs:
        r = render_beat(state, questions, client=DeadClient(),
                        recording_path=recs[-1])
        assert_honest(r)
        checks["with_recording"] = r["mode"]
        checks["label_verbatim"] = (
            r["label"] == RECORDED_LABEL_TEMPLATE.format(
                ts=r["rehearsed_at"]))
    r2 = render_beat(state, questions, client=DeadClient(),
                     recording_path=None)
    assert_honest(r2)
    checks["without_recording"] = r2["mode"]
    ok = (checks.get("with_recording") == "recorded"
          and checks.get("label_verbatim") is True
          and checks["without_recording"] == "limits_beat")
    return ok, checks


def phase3_receipts(db):
    """The local event log serves river/report/receipts with Jev down."""
    log = EventLog(db)
    head_seq, head_hash = log.head()
    events = [log.get_event(s) for s in range(1, head_seq + 1)]
    # hash chain intact
    chained = all(events[i]["prev_hash"] == events[i - 1]["row_hash"]
                  for i in range(1, len(events)))
    first_ok = events[0]["prev_hash"] == "GENESIS"
    # disposition fold (what the receipts beat renders)
    fold = {}
    for e in events:
        if e["type"] == "decision_made":
            d = json.loads(e["body"])["disposition"]
            fold[d] = fold.get(d, 0) + 1
    flips = detect_flips(events)  # must not raise
    detail = {"events": len(events), "head_hash": head_hash[:12],
              "chain_intact": chained and first_ok,
              "disposition_fold": fold,
              "flips_detected": flips}
    ok = chained and first_ok and fold.get("passthrough", 0) == N
    return ok, detail


def main():
    db, fwd_log = sys.argv[1], sys.argv[2]
    ok1, d1 = phase1_pipeline(db, fwd_log)
    print(f"phase 1 (paging with Jev dead): {'PASS' if ok1 else 'FAIL'} "
          f"{json.dumps(d1)}")
    ok2, d2 = phase2_flipbeat()
    print(f"phase 2 (flip-beat fallback):  {'PASS' if ok2 else 'FAIL'} "
          f"{json.dumps(d2)}")
    ok3, d3 = phase3_receipts(db)
    print(f"phase 3 (log-backed receipts): {'PASS' if ok3 else 'FAIL'} "
          f"{json.dumps(d3)}")
    ok = ok1 and ok2 and ok3
    print("DRILL 7 verdict:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
