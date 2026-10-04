#!/usr/bin/env python3
"""DRILL 1B — kill the Jev client mid-storm: race-to-page must page via timer.

Real engine path: Gate + RaceRunner (the same race the receiver uses),
legacy Forwarder wired exactly like receiver._triage (passthrough ->
forward to the stub PD), AuditLog like the receiver.

  Phase 1 (alerts 0-9):  client WEDGED (hangs in decide)  -> timer must win
  KILL at alert 10:      client DIES (raises JevTimeout)   -> fail-open
  Phase 2 (alerts 10-29):                                    -> error_passthrough

Bar: 30/30 passthrough, zero hangs (each evaluate < budget + slack),
30/30 receipts at the stub PD, 30/30 audit rows with honest reasons.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from sentinel.audit import AuditLog
from sentinel.client import JevTimeout, SystemOneClient
from sentinel.config import Thresholds
from sentinel.forwarder import Forwarder
from sentinel.gate import Gate
from sentinel.models import Alert
from sentinel import race as race_mod

BUDGET_MS = 400
SLACK_MS = 1500
N = 30
KILL_AT = 10


class WedgedThenDeadClient(SystemOneClient):
    """Hangs forever until kill() is called, then raises JevTimeout."""

    def __init__(self):
        # ADR-015: drills never float silently — explicit, loudly-warned
        # opt-out (drill only; the client wedges before any answer).
        super().__init__(api_key="drill-key", base_url="http://drill.invalid",
                         allow_floating_model=True)
        self.dead = False

    def kill(self):
        self.dead = True

    def decide(self, state, questions):
        if self.dead:
            raise JevTimeout("client killed mid-drill")
        time.sleep(60)  # wedged: blackholed connection
        raise AssertionError("wedged client should never return")


def main():
    db = sys.argv[1]
    audit = AuditLog(db)
    client = WedgedThenDeadClient()
    gate = Gate(client, Thresholds(), set(), audit,
                race_config=race_mod.RaceConfig(budget_ms=BUDGET_MS))
    fwd = Forwarder(
        pd_events_url=os.environ.get("PD_EVENTS_URL",
                                     "http://127.0.0.1:8099/v2/enqueue"),
        timeout_s=5.0,
        default_routing_key="drill-routing-key")

    results = []
    for i in range(N):
        if i == KILL_AT:
            client.kill()
            print(f"--- CLIENT KILLED at alert {i} ---")
        alert = Alert(
            alert_id=f"d1b-{i}", received_at="2026-10-04T20:30:00+00:00",
            fingerprint=f"d1b-fp-{i:02d}", service="api", check="cpu_high",
            severity_in="critical", title=f"drill 1b alert {i}",
            source="pagerduty")
        t0 = time.monotonic()
        disp, _rec = gate.evaluate(alert, {}, {}, {})
        wall_ms = (time.monotonic() - t0) * 1000.0
        if disp.action != "suppress":
            fr = fwd.forward(alert, disp)
            forwarded = fr.forwarded
        else:
            forwarded = False
        results.append({"i": i, "action": disp.action, "reason": disp.reason,
                        "wall_ms": round(wall_ms, 1), "forwarded": forwarded})
        print(f"alert {i:2d}: {disp.action:10s} {disp.reason:16s} "
              f"wall={wall_ms:7.1f}ms fwd={forwarded}")

    gate.close()
    audit.close()

    by_reason = {}
    for r in results:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
    ok = (
        all(r["action"] == "passthrough" for r in results)
        and all(r["forwarded"] for r in results)
        and all(r["wall_ms"] < BUDGET_MS + SLACK_MS for r in results)
        and by_reason.get("timer_won", 0) == KILL_AT
        and sum(1 for r in results if r["reason"].startswith("error:")) == N - KILL_AT
    )
    print(json.dumps({"n": N, "by_reason": by_reason,
                      "max_wall_ms": max(r["wall_ms"] for r in results),
                      "verdict": "PASS" if ok else "FAIL"}, indent=1))
    # audit evidence
    import sqlite3
    n = sqlite3.connect(db).execute(
        "select count(*) from decisions where type='decision_made'").fetchone()[0]
    print(f"audit decision_made rows: {n}")
    return 0 if (ok and n == N) else 1


if __name__ == "__main__":
    sys.exit(main())
