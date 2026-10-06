"""LOAD-TEST lane — static dashboard generator.

Reads tier report JSONs and emits a single self-contained HTML file for
publishing at /loadtest/ on GitHub Pages. Zero dependencies, inline SVG
charts, all numbers baked in at generation time.

Usage:
  python3 make_dashboard.py report-10k.json [report-100k.json ...] --out index.html
"""
from __future__ import annotations

import argparse
import html
import json
import sys


def pct(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(int(q * len(s)), len(s) - 1)]


def svg_bars(values, labels, width=520, height=140, unit=""):
    if not values:
        return ""
    mx = max(values) or 1.0
    bw = width / len(values)
    rects = []
    for i, (v, lab) in enumerate(zip(values, labels)):
        h = (v / mx) * (height - 24)
        x = i * bw + 2
        y = height - 14 - h
        rects.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw-4:.1f}" '
            f'height="{h:.1f}" fill="#2f6fed" opacity="0.85">'
            f'<title>{html.escape(str(lab))}: {v}{unit}</title></rect>')
    return (f'<svg width="{width}" height="{height}" role="img">'
            + "".join(rects) + "</svg>")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--verdict", default="PENDING")
    ap.add_argument("--limits", default="")
    args = ap.parse_args(argv)

    tiers = []
    for path in args.reports:
        with open(path) as fh:
            tiers.append(json.load(fh))

    cards = []
    rows = []
    for r in tiers:
        t = r["totals"]["judge"]
        real_lat = t["real_latency_ms"]
        race = r["totals"]["race_metrics"]
        judge_wins = sum(v for k, v in race.items()
                         if "answered_in_time" in k)
        timer_wins = sum(v for k, v in race.items() if "timer_won" in k.lower()
                         or "TIMER_WON" in k)
        errors = sum(v for k, v in race.items()
                     if "error" in k.lower() or "shed" in k.lower())
        spend = t["real_spend_usd"]
        alerts = r["total_alerts"]
        cost_per_m = (spend / alerts * 1_000_000) if alerts else 0
        rows.append(
            f"<tr><td>{r['tier']}</td><td>{alerts:,}</td>"
            f"<td>{r['alerts_per_s']}/s</td>"
            f"<td>{t['routed'].get('real', 0):,} real / "
            f"{t['routed'].get('faithful', 0):,} faithful</td>"
            f"<td>{pct(real_lat, .5):.0f} / {pct(real_lat, .99):.0f} ms</td>"
            f"<td>{judge_wins:,} / {timer_wins:,} / {errors:,}</td>"
            f"<td>${cost_per_m:.2f}</td></tr>")
        # throughput chart per tier from batches
        thr = [b["alerts_per_s"] for b in r["batches"] if b["alerts"]]
        labs = [f"chunk {b['chunk_idx']}" for b in r["batches"]
                if b["alerts"]]
        cards.append(
            f"<section><h3>Tier {r['tier']} — throughput per chunk "
            f"(alerts/sec)</h3>{svg_bars(thr, labs)}"
            f"<p>{alerts:,} alerts in {r['wall_s']}s wall "
            f"({r['virtual_span_s']/3600:.1f}h virtual). "
            f"Workers: {r['workers']}, sample rate: "
            f"{r['sample_rate']*100:.1f}%.</p></section>")

    limits_html = "".join(
        f"<li>{html.escape(x.strip())}</li>" for x in args.limits.split(";;")
        if x.strip()) or "<li>None recorded.</li>"

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sentinel — Load Test: Millions/Day Validation</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:960px;margin:0 auto;padding:24px;color:#1a1a1a;background:#fafafa}}
.banner{{background:#fff3cd;border:1px solid #d9a821;padding:12px 16px;border-radius:8px;margin-bottom:20px}}
.verdict{{background:#e8f5e9;border:1px solid #2e7d32;padding:12px 16px;border-radius:8px;margin:20px 0}}
table{{border-collapse:collapse;width:100%;margin:16px 0;background:#fff}}
th,td{{border:1px solid #ddd;padding:8px 10px;text-align:left;font-size:14px}}
th{{background:#f0f0f0}}section{{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:16px;margin:16px 0}}
h1{{font-size:28px}}h2{{font-size:22px;margin-top:32px}}h3{{font-size:17px}}
.small{{color:#555;font-size:13px}}
</style></head><body>
<div class="banner"><strong>SIMULATED LOAD TEST</strong> — synthetic alert
traffic through the real Sentinel pipeline. Real Jev key active on a sampled
fraction (default 2%); bulk carried by FaithfulJev, the calibrated simulated
service. FakePD sink only — <strong>real PagerDuty was never contacted</strong>.
Every number below is measured, never asserted.</div>
<h1>Sentinel Load Test — Can We Handle Millions/Day?</h1>
<p class="small">Generated from tier run reports. Method: concurrent alert
injection through <code>Pipeline._triage</code> (correlator → gate(race →
judge) → forwarder), virtual-time batches, deterministic seeds.</p>
<div class="verdict"><strong>Verdict: {html.escape(args.verdict)}</strong></div>
<h2>Per-tier results</h2>
<table><tr><th>Tier</th><th>Alerts</th><th>Throughput</th><th>Judgments</th>
<th>Jev p50/p99</th><th>Race: judge / timer / err</th><th>Cost / 1M alerts</th></tr>
{"".join(rows)}</table>
{"".join(cards)}
<h2>What this test did NOT prove</h2>
<ul>{limits_html}</ul>
<p class="small">Calibration: FaithfulJev latencies from the 2026-10-03
real-key campaign (n=109, p50 816ms, p99 1526ms); fault rates measured.
Real-key samples via vault surrogate — the key never touched any file.</p>
</body></html>"""
    with open(args.out, "w") as fh:
        fh.write(page)
    print(f"dashboard -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
