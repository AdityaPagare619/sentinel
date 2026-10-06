"""LOAD-TEST lane — tier report analyzer.

Turns a tier report JSON into the key findings: throughput, race outcomes,
control-principle check (suppression creep under pressure), cost, and the
breaking-point read.

Usage: python3 analyze.py report-10k.json
"""
from __future__ import annotations

import json
import sys


def pct(vals, q):
    s = sorted(vals)
    return s[min(int(q * len(s)), len(s) - 1)] if s else 0.0


def main(path: str) -> None:
    r = json.load(open(path))
    t = r["totals"]
    judge = t["judge"]
    race = t["race_metrics"]
    pm = t["pipeline_metrics"]

    judge_wins = sum(v for k, v in race.items() if "answered_in_time" in k)
    timer_wins = sum(v for k, v in race.items()
                     if "TIMER_WON" in k or "timer_won" in k.lower())
    errors = sum(v for k, v in race.items()
                 if "error" in k.lower() or "shed" in k.lower())

    actions: dict[str, int] = {}
    for b in r["batches"]:
        for k, v in b["actions"].items():
            actions[k] = actions.get(k, 0) + v
    total_act = sum(actions.values())
    suppress_rate = actions.get("suppress", 0) / total_act if total_act else 0

    # Pressure vs baseline comparison (control principle under load).
    pb, bb = [], []
    for b in r["batches"]:
        (pb if b.get("pressure") else bb).append(b)
    def sr(bs):
        s = sum(x["actions"].get("suppress", 0) for x in bs)
        n = sum(sum(x["actions"].values()) for x in bs)
        return s / n if n else 0
    def tw(bs):
        return sum(x["race_delta"].get(k, 0) for x in bs
                   for k in x["race_delta"]
                   if "TIMER_WON" in k or "timer_won" in k.lower())

    real_lat = judge["real_latency_ms"]
    print(f"=== {r['tier']} (seed {r['seed']}) ===")
    print(f"alerts: {r['total_alerts']:,} in {r['wall_s']}s wall "
          f"({r['virtual_span_s']/3600:.1f}h virtual) -> "
          f"{r['alerts_per_s']}/s")
    print(f"problems triaged: {pm.get('triaged', 0):,}, "
          f"deduped: {pm.get('deduped', 0):,} "
          f"({pm.get('deduped',0)/max(pm.get('triaged',1),1)*100:.1f}%), "
          f"storms: {pm.get('storms', 0)}")
    print(f"race: judge-wins={judge_wins:,} timer-wins={timer_wins:,} "
          f"errors={errors:,}")
    print(f"actions: {json.dumps(actions)}")
    print(f"suppression rate: {suppress_rate:.3f}")
    print(f"judge routed: real={judge['routed'].get('real', 0):,} "
          f"faithful={judge['routed'].get('faithful', 0):,} "
          f"cap_tripped={judge['cap_tripped']}")
    print(f"real Jev latency: n={len(real_lat)} "
          f"p50={pct(real_lat,.5):.0f}ms p99={pct(real_lat,.99):.0f}ms")
    print(f"faithful latency: p50={judge['faithful_latency_p50_ms']}ms "
          f"p99={judge['faithful_latency_p99_ms']}ms "
          f"faults={judge['faithful_faults']}")
    print(f"spend: ${judge['real_spend_usd']} "
          f"(${judge['real_spend_usd']/max(r['total_alerts'],1)*1e6:.2f}/M alerts)")
    print(f"FakePD pages: {t['fakepd_pages']:,} "
          f"(real PD contacted: {t['real_pagerduty_contacted']})")
    if pb:
        print(f"PRESSURE: timer-wins pressure={tw(pb)} vs baseline={tw(bb)}; "
              f"suppress rate pressure={sr(pb):.3f} vs baseline={sr(bb):.3f}")
        if sr(pb) > sr(bb) + 0.02:
            print("  FAIL: suppression creep under judge pressure!")
        else:
            print("  OK: no suppression creep (fail-open holds).")
    # Capacity extrapolation.
    per_day = r["alerts_per_s"] * 86400
    print(f"capacity at measured rate: {per_day:,.0f} alerts/day")


if __name__ == "__main__":
    main(sys.argv[1])
