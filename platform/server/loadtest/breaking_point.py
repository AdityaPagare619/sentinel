"""LOAD-TEST lane — breaking-point micro-benchmark.

Same workload (2K alerts, mixed), worker ramp 8 -> 16 -> 32 -> 64.
Finds where throughput plateaus or errors rise: THE BREAKING POINT.

For each level: alerts/sec, judge latency p50/p99, race outcomes
(judge-wins vs timer-wins vs errors), suppression rate (control check:
must not rise as the system saturates).

Usage:
  python3 breaking_point.py --seed 7 --out bp.json
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import LoadHarness  # noqa: E402

WORKLOAD = [
    {"kind": "steady", "name": "bp", "start_s": 0, "end_s": 600,
     "rate_per_min": 200.0,
     "mix": {"noise": 0.50, "warning": 0.30, "sev": 0.15, "deploy": 0.05}},
]
CHUNK_S = 60.0


def run_level(workers: int, seed: int) -> dict:
    h = LoadHarness(seed=seed, start_epoch=1787952000.0,
                    sample_rate=0.0, spend_cap_usd=5.0, workers=workers)
    h.build()
    t0 = time.time()
    actions: dict[str, int] = {}
    race_delta_total: dict[str, int] = {}
    n_alerts = 0
    try:
        n_chunks = 10
        for ci in range(n_chunks):
            b = h.run_batch(WORKLOAD, ci, ci * CHUNK_S, (ci + 1) * CHUNK_S)
            n_alerts += b["alerts"]
            for k, v in b["actions"].items():
                actions[k] = actions.get(k, 0) + v
            for k, v in b["race_delta"].items():
                race_delta_total[k] = race_delta_total.get(k, 0) + v
    finally:
        totals = h.totals()
        h.close()
    wall = time.time() - t0
    judge_wins = sum(v for k, v in race_delta_total.items()
                     if "answered_in_time" in k)
    timer_wins = sum(v for k, v in race_delta_total.items()
                     if "TIMER_WON" in k or "timer_won" in k.lower())
    errors = sum(v for k, v in race_delta_total.items()
                 if "error" in k.lower() or "shed" in k.lower())
    suppress = actions.get("suppress", 0)
    return {
        "workers": workers,
        "alerts": n_alerts,
        "wall_s": round(wall, 1),
        "alerts_per_s": round(n_alerts / wall, 1) if wall else 0,
        "judge_wins": judge_wins,
        "timer_wins": timer_wins,
        "errors": errors,
        "suppression_rate": round(suppress / n_alerts, 4) if n_alerts else 0,
        "faithful_p50_ms": totals["judge"]["faithful_latency_p50_ms"],
        "faithful_p99_ms": totals["judge"]["faithful_latency_p99_ms"],
        "actions": actions,
    }


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    levels = []
    for w in (8, 16, 32, 64):
        print(f"[bp] workers={w} ...", flush=True)
        r = run_level(w, args.seed)
        levels.append(r)
        print(f"[bp] workers={w}: {r['alerts_per_s']}/s, "
              f"timer_wins={r['timer_wins']}, errors={r['errors']}, "
              f"suppress_rate={r['suppression_rate']}", flush=True)
    out = args.out or "breaking-point.json"
    with open(out, "w") as fh:
        json.dump({"levels": levels, "simulated": True}, fh, indent=1)
    print(f"[bp] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
