#!/usr/bin/env python3
"""Analyze one C2 load-test run JSON -> summary dict printed as JSON."""

import json
import statistics
import sys


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, int(q * len(s)))
    return s[i]


def dist(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": round(statistics.mean(xs), 3),
        "p50": round(pct(xs, 0.50), 3),
        "p90": round(pct(xs, 0.90), 3),
        "p95": round(pct(xs, 0.95), 3),
        "p99": round(pct(xs, 0.99), 3),
        "max": round(max(xs), 3),
    }


def analyze(path):
    with open(path) as fh:
        r = json.load(fh)
    resps = r["responses"]
    ok = [x for x in resps if x["ok"]]
    e2e = [(x["t_recv"] - x["t_send"]) * 1000.0 for x in ok]

    # per-alert segment join
    tri, race, fwd = {}, {}, {}
    for rec in r.get("req_records", []):
        aid = rec.get("aid")
        if not aid:
            continue
        d = {"triage": tri, "race": race, "forward": fwd}[rec["kind"]]
        d[aid] = (rec["t0"], rec["t1"])
    engine_only, race_block, fwd_t = [], [], []
    joined = 0
    for aid, (t0, t1) in tri.items():
        rb = race.get(aid)
        rb_dur = (rb[1] - rb[0]) * 1000.0 if rb else 0.0
        race_block.append(rb_dur)
        engine_only.append((t1 - t0) * 1000.0 - rb_dur)
        if aid in fwd:
            fwd_t.append((fwd[aid][1] - fwd[aid][0]) * 1000.0)
        joined += 1

    vendor = [(e - s) * 1000.0 for s, e in r.get("vendor_calls", [])]

    disp_mix: dict[str, int] = {}
    for x in ok:
        d = x.get("disposition") or "?"
        disp_mix[d] = disp_mix.get(d, 0) + 1

    win_mix: dict[str, int] = {}
    out_mix: dict[str, int] = {}
    for w, o in r.get("race_outcomes", []):
        win_mix[w] = win_mix.get(w, 0) + 1
        out_mix[o] = out_mix.get(o, 0) + 1

    submit_lag = r.get("submit_lag_s", [])
    sched_lag_over_1s = sum(1 for x in submit_lag if x > 1.0)

    samples = r.get("samples", [])
    rss = [s["rss_mb"] for s in samples if s["rss_mb"] > 0]
    threads = [s["threads"] for s in samples]

    app = r.get("append_lat_ms", [])
    dur = r.get("duration_s", 1) or 1

    out = {
        "profile": r["profile"],
        "run": r["run_idx"],
        "seed": r["seed"],
        "offered": r["offered"],
        "responses_ok": len(ok),
        "responses_err": len(resps) - len(ok),
        "achieved_per_min": round(len(resps) / dur * 60.0, 1),
        "target_per_min": round(r["rate_per_s_target"] * 60.0, 1),
        "submit_lag_p99_s": round(pct(submit_lag, 0.99), 3) if submit_lag else None,
        "submit_lag_over_1s": sched_lag_over_1s,
        "e2e_ms": dist(e2e),
        "engine_only_ms": dist(engine_only),
        "race_block_ms": dist(race_block),
        "forward_ms": dist(fwd_t),
        "vendor_wait_ms": dist(vendor),
        "eventlog_append_ms": dist(app),
        "eventlog_appends": len(app),
        "eventlog_appends_per_s": round(len(app) / dur, 1),
        "disposition_mix": disp_mix,
        "race_winner_mix": win_mix,
        "race_outcome_mix": out_mix,
        "race_metrics_delta": r.get("race_metrics_delta", {}),
        "vendor_calls": len(vendor),
        "pd_deliveries": len(r.get("pd_arrivals", [])),
        "forwarder_metrics": r.get("forwarder_metrics", {}),
        "pipeline_metrics": r.get("pipeline_metrics", {}),
        "rss_start_mb": r.get("rss_start_mb"),
        "rss_end_mb": r.get("rss_end_mb"),
        "rss_max_sampled_mb": round(max(rss), 1) if rss else None,
        "threads_max": max(threads) if threads else None,
        "correlator_seen_start": r.get("correlator_seen_start"),
        "correlator_seen_end": r.get("correlator_seen_end"),
    }
    # timer-win rate among raced (non-structural) decisions
    raced = sum(win_mix.values())
    timer_wins = sum(v for k, v in win_mix.items() if "timer" in k.lower())
    out["timer_win_rate"] = round(timer_wins / raced, 4) if raced else None
    return out


if __name__ == "__main__":
    print(json.dumps(analyze(sys.argv[1]), indent=1))
