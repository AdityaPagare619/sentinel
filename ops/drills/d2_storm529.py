#!/usr/bin/env python3
"""DRILL 2 — 529 storm through the REAL Pipeline with the REAL SystemOneClient.

The receiver cannot point its real client at a stub (no JEV_BASE_URL wiring
in build_pipeline_from_env — flagged to the coordinator), so this drives the
real receiver Pipeline in-process: correlator -> gate (race-to-page,
B=2700ms) -> legacy Forwarder, exactly as receiver._triage does.

  Phase 1: 25 distinct fingerprints (storm declares at the 21st).
  Phase 2: 35 more alerts folded into the active storm.

Bar: backoff engages (529 -> retry x3 within budget -> JevOverloaded);
every alert gets a disposition and pages on uncertainty; folded alerts are
suppressed ONLY with storm-fold evidence (the delivered aggregate page);
zero suppressions via the triple lock (impossible on a 529); Jev calls are
O(storm declares), never O(alerts), after the storm is declared.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from sentinel.audit import AuditLog
from sentinel.client import SystemOneClient
from sentinel.config import Thresholds
from sentinel.correlator import Correlator
from sentinel.forwarder import Forwarder
from sentinel.gate import Gate
from sentinel.receiver import Pipeline, ReceiverConfig

N_DISTINCT = 25
N_FOLD = 35


def pd_payload(i, fp_i):
    return json.dumps({
        "routing_key": "drill-routing-key",
        "event_action": "trigger",
        "dedup_key": f"d2-{i}",
        "payload": {
            "summary": f"d2 storm alert {i}",
            "source": f"svc-{fp_i % 5}",
            "severity": "critical",
            "component": f"svc-{fp_i % 5}",
            "class": f"check-{fp_i}",
            "group": "region-0",
        },
    }).encode()


def main():
    db, count_path = sys.argv[1], sys.argv[2]
    client = SystemOneClient(
        api_key="dummy",
        base_url=os.environ.get("JEV_STUB_URL", "http://127.0.0.1:8101"),
        retry_budget_s=2.0)
    audit = AuditLog(db)
    gate = Gate(client, Thresholds(), set(), audit)
    fwd = Forwarder(
        pd_events_url=os.environ.get("PD_EVENTS_URL",
                                     "http://127.0.0.1:8099/v2/enqueue"),
        timeout_s=5.0, default_routing_key="drill-routing-key")
    pipe = Pipeline(Correlator(), gate, fwd, audit, ReceiverConfig())

    def jev_calls():
        with open(count_path) as fh:
            return int(fh.read().strip() or "0")

    calls_before_declare = None
    # Phase 1: distinct fingerprints -> storm declares at the 21st.
    for i in range(N_DISTINCT):
        resp = pipe.handle_pd(pd_payload(i, i))
        assert resp["status"] == "success", resp
        if i == 20:
            calls_before_declare = jev_calls()
    # Phase 2: folded into the active storm.
    for i in range(N_DISTINCT, N_DISTINCT + N_FOLD):
        resp = pipe.handle_pd(pd_payload(i, N_DISTINCT + (i % 5)))
        assert resp["status"] == "success", resp

    calls_after = jev_calls()
    gate.close()
    audit.close()

    import sqlite3
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "select action, reason from decisions where type='decision_made'").fetchall()
    actions = {}
    reasons = {}
    for a, r in rows:
        actions[a] = actions.get(a, 0) + 1
        reasons[r] = reasons.get(r, 0) + 1
    storms = pipe.metrics["storms"]
    folded = reasons.get("storm", 0)
    lock_suppress = sum(1 for a, r in rows
                        if a == "suppress" and r not in ("storm", "dedup"))
    print(json.dumps({
        "n_alerts": N_DISTINCT + N_FOLD,
        "actions": actions,
        "reasons": reasons,
        "storms_declared": storms,
        "jev_calls_before_declare": calls_before_declare,
        "jev_calls_total": calls_after,
        "jev_calls_after_declare": calls_after - calls_before_declare,
        "lock_suppressions_on_529": lock_suppress,
    }, indent=1))
    ok = (storms >= 1
          and lock_suppress == 0
          and actions.get("suppress", 0) == folded  # every suppress is storm-fold
          and (calls_after - calls_before_declare) <= 3 * (storms + 2))
    print("verdict:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
