"""LOAD-TEST lane — tier runner CLI.

Tiers (alert counts; 1M alerts ~= one millions/day volume day):
  10k :  ~12K alerts, sanity + calibration
  100k: ~120K alerts, sustained behavior
  1m  :   ~1M alerts, the millions/day proof

Each tier: pick profiles for the target count, run chunked batches,
collect per-batch + total metrics, write a JSON report.

Usage:
  python3 run_tiers.py --tier 10k --seed 7 [--workers 16] [--sample-rate 0.02]
                       [--spend-cap 5.0] [--out report-10k.json]
                       [--pressure] [--burst-test]

  --pressure   : mid-run slow-tail pressure phase (judge latency attack).
                 Timer wins MUST rise; suppression rate must NOT rise
                 (fail-open law under load). Reported separately.
  --burst-test : deliberate 40 req/s × 10s real-Jev burst to observe 429
                 behavior at the vendor bound. Off by default.

Stdlib only. The real key is NEVER read here — MixedJevClient uses the
vault surrogate. FakePD sink only; real PagerDuty unreachable by construction.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generator import load_profiles  # noqa: E402
from harness import LoadHarness  # noqa: E402
from sentinel.client import FaultProfile  # noqa: E402

TIERS = {
    # tier: (target_alerts, virtual_span_s, workers, chunk_s)
    "10k": (12_000, 3600, 16, 60.0),
    "100k": (120_000, 21600, 32, 60.0),
    "1m": (1_000_000, 86400, 32, 60.0),
}


def scale_profiles(base: dict, factor: float) -> list[dict]:
    """Scale steady profile rates to hit the target alert count."""
    out = []
    for name, p in base.items():
        p = dict(p)
        if p["kind"] == "steady":
            p["rate_per_min"] = float(p["rate_per_min"]) * factor
        elif p["kind"] == "storm_burst":
            p["alerts"] = max(100, int(int(p["alerts"]) * factor))
        out.append(p)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sentinel load-test tiers")
    ap.add_argument("--tier", required=True, choices=sorted(TIERS))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--sample-rate", type=float, default=0.02)
    ap.add_argument("--spend-cap", type=float, default=5.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--pressure", action="store_true",
                    help="slow-tail pressure phase mid-run")
    ap.add_argument("--burst-test", action="store_true",
                    help="40 req/s x 10s real-Jev burst at vendor bound")
    args = ap.parse_args(argv)

    target, virtual_span, workers, chunk_s = TIERS[args.tier]
    workers = args.workers or workers
    profiles = load_profiles()
    # Estimate: baseline dominates. Scale its rate to hit target.
    # baseline 694/min over 86400s = ~1M. Compute factor for target.
    base_total = sum(
        float(p.get("rate_per_min", 0)) *
        (float(p.get("end_s", 0)) - float(p.get("start_s", 0))) / 60.0
        for p in profiles.values() if p["kind"] == "steady")
    factor = target / base_total if base_total else 1.0
    profiles = scale_profiles(profiles, factor)
    # Clip profile windows to this tier's virtual span.
    for p in profiles:
        if "end_s" in p:
            p["end_s"] = min(float(p["end_s"]), virtual_span)
        if "window_s" in p:
            p["start_s"] = min(float(p["start_s"]), virtual_span - 1)

    start_epoch = 1787952000.0  # fixed virtual epoch (deterministic)
    h = LoadHarness(seed=args.seed, start_epoch=start_epoch,
                    sample_rate=args.sample_rate,
                    spend_cap_usd=args.spend_cap, workers=workers)
    h.build()
    t_run0 = time.time()
    batches = []
    n_chunks = max(1, int(virtual_span // chunk_s))
    # Pressure window: middle third of chunks. Slow-tail faults attack the
    # judge; the fail-open law says timer-wins must rise and suppressions
    # must NOT rise. Batches are tagged for the comparison.
    p_start, p_end = n_chunks // 3, 2 * n_chunks // 3
    try:
        for ci in range(n_chunks):
            cs, ce = ci * chunk_s, min((ci + 1) * chunk_s, virtual_span)
            in_pressure = args.pressure and p_start <= ci < p_end
            h.pressure_faults = (FaultProfile(slow_tail_rate=0.15,
                                             slow_tail_ms=4500.0)
                                if in_pressure else None)
            b = h.run_batch(profiles, ci, cs, ce)
            b["pressure"] = bool(in_pressure)
            batches.append(b)
            if b["alerts"]:
                print(f"[tier] chunk {ci+1}/{n_chunks}: "
                      f"{b['alerts']} alerts, {b['wall_s']}s wall, "
                      f"{b['alerts_per_s']}/s"
                      + (" [PRESSURE]" if in_pressure else ""), flush=True)
    finally:
        h.pressure_faults = None
        totals = h.totals()
        h.close()

    wall_total = round(time.time() - t_run0, 1)
    total_alerts = sum(b["alerts"] for b in batches)
    report = {
        "tier": args.tier,
        "seed": args.seed,
        "workers": workers,
        "sample_rate": args.sample_rate,
        "spend_cap_usd": args.spend_cap,
        "target_alerts": target,
        "total_alerts": total_alerts,
        "wall_s": wall_total,
        "alerts_per_s": round(total_alerts / wall_total, 1) if wall_total else 0,
        "virtual_span_s": virtual_span,
        "batches": batches,
        "totals": totals,
        "simulated": True,
    }
    out = args.out or f"report-{args.tier}-seed{args.seed}.json"
    with open(out, "w") as fh:
        json.dump(report, fh, indent=1, sort_keys=True)
    print(f"[tier] done: {total_alerts} alerts in {wall_total}s "
          f"({report['alerts_per_s']}/s). report -> {out}")
    print(f"[tier] judge: {json.dumps(totals['judge']['routed'])} "
          f"spend=${totals['judge']['real_spend_usd']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
