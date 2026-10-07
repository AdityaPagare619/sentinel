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


def tier_profiles(tier: str, seed: int) -> tuple[list[dict], float]:
    """Profiles compressed into the tier's virtual span.

    Every tier gets the full shape: baseline bulk + bad-deploy window +
    incident hour + storm burst. Rates are sized so the tier hits its
    target alert count; the MIX semantics stay identical across tiers.
    """
    if tier == "10k":
        span, tgt = 7200.0, 12_000
        return ([
            {"kind": "steady", "name": "baseline", "start_s": 0,
             "end_s": span, "rate_per_min": 70.0,
             "mix": {"noise": 0.55, "warning": 0.30,
                     "sev": 0.10, "deploy": 0.05}},
            {"kind": "steady", "name": "bad_deploy", "start_s": 3000,
             "end_s": 4800, "rate_per_min": 60.0,
             "mix": {"noise": 0.30, "warning": 0.30,
                     "sev": 0.25, "deploy": 0.15}},
            {"kind": "steady", "name": "incident", "start_s": 5400,
             "end_s": 6300, "rate_per_min": 80.0,
             "mix": {"noise": 0.20, "warning": 0.30,
                     "sev": 0.40, "deploy": 0.10}},
            {"kind": "storm_burst", "name": "storm",
             "start_s": 6600, "window_s": 300,
             "alerts": 600, "distinct_fingerprints": 400,
             "severity_in": ["warning", "critical"]},
        ], span)
    if tier == "100k":
        span = 21600.0
        return ([
            {"kind": "steady", "name": "baseline", "start_s": 0,
             "end_s": span, "rate_per_min": 280.0,
             "mix": {"noise": 0.55, "warning": 0.30,
                     "sev": 0.10, "deploy": 0.05}},
            {"kind": "steady", "name": "bad_deploy", "start_s": 9000,
             "end_s": 12600, "rate_per_min": 240.0,
             "mix": {"noise": 0.30, "warning": 0.30,
                     "sev": 0.25, "deploy": 0.15}},
            {"kind": "steady", "name": "incident", "start_s": 14400,
             "end_s": 16200, "rate_per_min": 320.0,
             "mix": {"noise": 0.20, "warning": 0.30,
                     "sev": 0.40, "deploy": 0.10}},
            {"kind": "storm_burst", "name": "storm",
             "start_s": 18000, "window_s": 600,
             "alerts": 6000, "distinct_fingerprints": 4000,
             "severity_in": ["warning", "critical"]},
        ], span)
    # 1m: full 24h shape at millions/day volume (~1M alerts).
    span = 86400.0
    return ([
        {"kind": "steady", "name": "baseline", "start_s": 0,
         "end_s": span, "rate_per_min": 640.0,
         "mix": {"noise": 0.55, "warning": 0.30,
                 "sev": 0.10, "deploy": 0.05}},
        {"kind": "steady", "name": "bad_deploy", "start_s": 36000,
         "end_s": 43200, "rate_per_min": 560.0,
         "mix": {"noise": 0.30, "warning": 0.30,
                 "sev": 0.25, "deploy": 0.15}},
        {"kind": "steady", "name": "incident", "start_s": 57600,
         "end_s": 61200, "rate_per_min": 750.0,
         "mix": {"noise": 0.20, "warning": 0.30,
                 "sev": 0.40, "deploy": 0.10}},
        {"kind": "storm_burst", "name": "storm",
         "start_s": 72000, "window_s": 900,
         "alerts": 60000, "distinct_fingerprints": 20000,
         "severity_in": ["warning", "critical"]},
    ], span)


def burst_test(model: str = "jev-1.13.0", rate: float = 40.0,
             seconds: float = 10.0) -> dict:
    """Deliberate vendor-bound probe: 40 req/s x 10s of real Jev calls.

    Observes 429/timeout behavior AT the bound. Polite otherwise — this is
    the only phase that intentionally approaches the limit, and it says so.
    Spend: ~400 calls x ~0.5K tokens ~= $0.01. Well under any cap.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from mixed_jev import SurrogateJevClient, RatePacer

    client = SurrogateJevClient(model=model)
    pacer = RatePacer(rate)
    n = int(rate * seconds)
    results: list[dict] = []
    rlock = threading.Lock()

    # Representative alert state (mirrors the live smoke shape).
    state = {"alert": "p99_latency breach on checkout-api (burst probe)",
             "service": "checkout-api", "seed": "burst"}
    questions = {"disposition": {
        "type": "choice",
        "instructions": "Should this alert page a human now, or be "
                        "suppressed as routine noise?",
        "criteria": {"page": "genuine incident needing human attention",
                     "suppress": "routine noise",
                     "cannot_determine": "not enough signal"}}}

    def one(i):
        pacer.acquire()
        t0 = time.time()
        try:
            resp = client.decide(state, questions)
            out = {"ok": True,
                   "latency_ms": round((time.time() - t0) * 1000, 1)}
        except Exception as exc:  # noqa: BLE001 — this IS the measurement
            out = {"ok": False, "error": type(exc).__name__,
                   "latency_ms": round((time.time() - t0) * 1000, 1)}
        with rlock:
            results.append(out)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=48) as pool:
        list(pool.map(one, range(n)))
    wall = time.time() - t0
    oks = [r for r in results if r["ok"]]
    errs: dict[str, int] = {}
    for r in results:
        if not r["ok"]:
            errs[r["error"]] = errs.get(r["error"], 0) + 1
    lat = sorted(r["latency_ms"] for r in oks)
    summary = {
        "calls": n, "ok": len(oks), "errors": errs,
        "wall_s": round(wall, 1),
        "achieved_per_s": round(n / wall, 1) if wall else 0,
        "latency_ms": {"p50": lat[len(lat) // 2] if lat else 0,
                       "p99": lat[int(len(lat) * 0.99)] if lat else 0,
                       "max": lat[-1] if lat else 0},
        "spend_usd": round(client.spend_usd(), 4),
    }
    print(f"[burst] {n} calls at {rate}/s: {summary['ok']} ok, "
          f"errors={errs}, p99={summary['latency_ms']['p99']}ms, "
          f"spend=${summary['spend_usd']}")
    return summary


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

    burst = None
    if args.burst_test:
        print("[tier] running vendor-bound burst probe first (40 req/s x 10s)")
        burst = burst_test()

    target, virtual_span, workers, chunk_s = TIERS[args.tier]
    workers = args.workers or workers
    profiles, virtual_span = tier_profiles(args.tier, args.seed)

    start_epoch = 1787952000.0  # fixed virtual epoch (deterministic)
    # File-backed audit DB next to the report: :memory: OOMs at 1M scale
    # (~7KB/decision retained). On the big disk, not /tmp (tmpfs).
    out = args.out or f"report-{args.tier}-seed{args.seed}.json"
    audit_db = os.path.join(os.path.dirname(os.path.abspath(out)),
                            f"audit-{args.tier}-seed{args.seed}.sqlite3")
    h = LoadHarness(seed=args.seed, start_epoch=start_epoch,
                    sample_rate=args.sample_rate,
                    spend_cap_usd=args.spend_cap, workers=workers,
                    audit_db_path=audit_db)
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
        "audit_db": audit_db,
    }
    if burst is not None:
        report["burst_probe"] = burst
    with open(out, "w") as fh:
        json.dump(report, fh, indent=1, sort_keys=True)
    print(f"[tier] done: {total_alerts} alerts in {wall_total}s "
          f"({report['alerts_per_s']}/s). report -> {out}")
    print(f"[tier] judge: {json.dumps(totals['judge']['routed'])} "
          f"spend=${totals['judge']['real_spend_usd']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
