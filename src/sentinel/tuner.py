"""Threshold tuner CLI — ARCHITECTURE.md §5.

Reads labeled history (JSONL from shadow runs or the eval harness),
derives the expected-cost-optimal suppression threshold, grid-searches the
confidence bar, and prints a projection with real arithmetic.

Expected-cost math (suppress iff the expected cost of suppressing is below
the expected cost of paging):

    P(SEV1) * C_FN  <  (1 - P(SEV1)) * C_FP
    => P(SEV1)      <  C_FP / (C_FP + C_FN)  =: p*

The tuner NEVER recommends a suppress_p1_max below p* — the formula is the
floor. The confidence grid {0.80, 0.85, 0.90, 0.95} is reported as a tradeoff
table; the human decides which row to ship.

Usage:
    PYTHONPATH=src python -m sentinel.tuner --labels labels.jsonl \\
        [--c-fp 100] [--c-fn 50000] -o thresholds.json
"""

from __future__ import annotations

import argparse
import json

CONF_GRID = (0.80, 0.85, 0.90, 0.95)


# ---------------------------------------------------------------------------
# Core math (pure functions — unit-tested with hand-computed fixtures)
# ---------------------------------------------------------------------------

def expected_cost_threshold(c_fp: float, c_fn: float) -> float:
    """p* = C_FP / (C_FP + C_FN): suppress iff P(p1_critical) < p*."""
    return c_fp / (c_fp + c_fn)


def fingerprint_stats(rows: list[dict]) -> dict[str, dict]:
    """Per-fingerprint aggregates: sample count, SEV outcomes, mean probs."""
    stats: dict[str, dict] = {}
    for r in rows:
        fp = r["fingerprint"]
        s = stats.setdefault(fp, {"n": 0, "sev12": 0, "sum_noise_p": 0.0})
        s["n"] += 1
        s["sev12"] += int(r.get("became_sev12", 0))
        s["sum_noise_p"] += float((r.get("q1_probs") or {}).get("known_noise", 0.0))
    for s in stats.values():
        s["mean_noise_p"] = s["sum_noise_p"] / s["n"] if s["n"] else 0.0
    return stats


def candidate_allowlist(stats: dict[str, dict], min_samples: int = 5,
                        min_noise_prob: float = 0.6) -> set[str]:
    """Fingerprints that look like customer-verifiable known noise.

    Heuristic only — the customer still verifies before anything suppresses:
    enough samples, zero SEV1/2 outcomes, and a high mean P(known_noise).
    """
    return {
        fp for fp, s in stats.items()
        if s["n"] >= min_samples and s["sev12"] == 0
        and s["mean_noise_p"] >= min_noise_prob
    }


def classify(row: dict, p_star: float, conf_min: float, allowlist: set[str],
             page_p1p2_min: float = 0.30, queue_conf_min: float = 0.70) -> str:
    """Project one labeled alert to a disposition bucket (§5 policy)."""
    probs = row.get("q1_probs") or {}
    p1 = float(probs.get("p1_critical", 0.0))
    p12 = p1 + float(probs.get("p2_high", 0.0))
    p34 = float(probs.get("p3_medium", 0.0)) + float(probs.get("p4_low", 0.0))
    conf = float(row.get("q3_confidence", 0.0))
    if p1 < p_star and conf >= conf_min and row["fingerprint"] in allowlist:
        return "suppress"
    if p12 > page_p1p2_min:
        return "page_now"
    if p34 >= p12 and conf >= queue_conf_min:
        return "queue"
    return "baseline"  # unchanged from today's pipeline


def tune(rows: list[dict], c_fp: float = 100.0, c_fn: float = 50000.0,
         conf_grid: tuple[float, ...] = CONF_GRID,
         page_p1p2_min: float = 0.30, queue_conf_min: float = 0.70,
         min_samples: int = 5, min_noise_prob: float = 0.6) -> dict:
    """Grid-search suppress_conf_min; return the full projection.

    Recommendation rule: minimize expected error cost; tie-break by maximum
    avoided page cost, then by the most conservative (highest) confidence bar.
    The recommended conf_min is therefore always the expected-cost optimum on
    the grid — never below it.
    """
    p_star = expected_cost_threshold(c_fp, c_fn)
    stats = fingerprint_stats(rows)
    allowlist = candidate_allowlist(stats, min_samples, min_noise_prob)
    baseline_pages = sum(int(r.get("would_page_baseline", 0)) for r in rows)

    grid = []
    for conf_min in conf_grid:
        buckets = {"suppress": [], "page_now": [], "queue": [], "baseline": []}
        for r in rows:
            buckets[classify(r, p_star, conf_min, allowlist,
                             page_p1p2_min, queue_conf_min)].append(r)
        suppressed = buckets["suppress"]
        # Expected false suppresses = sum of P(p1_critical) over suppressed
        # alerts (linearity of expectation — no independence assumption needed).
        exp_false = sum(float((r.get("q1_probs") or {}).get("p1_critical", 0.0))
                        for r in suppressed)
        expected_cost = exp_false * c_fn
        avoided = len(suppressed) * c_fp
        grid.append({
            "conf_min": conf_min,
            "suppress": len(suppressed),
            "page_now": len(buckets["page_now"]),
            "queue": len(buckets["queue"]),
            "baseline": len(buckets["baseline"]),
            "exp_false_suppresses": exp_false,
            "expected_cost": expected_cost,
            "avoided_page_cost": avoided,
        })

    # Recommend the expected-cost optimum (ties -> max avoided -> max conf).
    best = min(grid, key=lambda g: (g["expected_cost"],
                                   -g["avoided_page_cost"],
                                   -g["conf_min"]))

    return {
        "c_fp": c_fp,
        "c_fn": c_fn,
        "suppress_p1_max": p_star,
        "page_p1p2_min": page_p1p2_min,
        "queue_conf_min": queue_conf_min,
        "uncertain_conf_max": 0.50,
        "alerts_evaluated": len(rows),
        "baseline_pages": baseline_pages,
        "allowlist_size": len(allowlist),
        "allowlist": sorted(allowlist),
        "grid": grid,
        "recommended_conf_min": best["conf_min"],
        "recommended": best,
    }


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

def _money(x: float) -> str:
    return f"${x:,.1f}"


def format_projection(res: dict) -> str:
    p_star = res["suppress_p1_max"]
    c_fp, c_fn = res["c_fp"], res["c_fn"]
    rec = res["recommended"]
    lines = [
        "Sentinel threshold tuner — expected-cost policy (§5)",
        "=" * 60,
        f"suppress_p1_max   = {p_star:.4f}   "
        f"(from C_FP/(C_FP+C_FN) = {c_fp:g}/{c_fp + c_fn:g})",
        f"suppress_conf_min = {res['recommended_conf_min']:.2f}   (recommended: expected-cost optimum on grid)",
        f"page_p1p2_min     = {res['page_p1p2_min']:.2f}",
        "",
        f"Alerts evaluated: {res['alerts_evaluated']:,} | "
        f"baseline pages: {res['baseline_pages']:,}",
        f"Candidate allowlist fingerprints: {res['allowlist_size']}",
        "",
        "Projected (recommended thresholds):",
    ]
    base = res["baseline_pages"] or 1
    lines.append(
        f"  suppress {rec['suppress']:,} "
        f"({100.0 * rec['suppress'] / base:.1f}% of baseline pages), "
        f"page_now {rec['page_now']:,}, queue {rec['queue']:,}, "
        f"unchanged {rec['baseline']:,}"
    )
    lines += [
        f"  Expected false suppresses: {rec['exp_false_suppresses']:.2f}  |  "
        f"Expected error cost: {_money(rec['expected_cost'])}  |  "
        f"Avoided page cost: {_money(rec['avoided_page_cost'])}",
        "",
        "Tradeoff table — suppress_conf_min grid (human decides):",
        f"  {'conf_min':>8} {'suppress':>9} {'exp_false':>10} "
        f"{'exp_cost':>12} {'avoided':>12}",
    ]
    for g in res["grid"]:
        marker = "  <-- recommended" if g["conf_min"] == res["recommended_conf_min"] else ""
        lines.append(
            f"  {g['conf_min']:>8.2f} {g['suppress']:>9,} {g['exp_false_suppresses']:>10.2f} "
            f"{_money(g['expected_cost']):>12} {_money(g['avoided_page_cost']):>12}{marker}"
        )
    lines += [
        "",
        "Note: the tuner reports; it never auto-ships. suppress_p1_max is",
        "floored at the expected-cost optimum — it is never recommended lower.",
    ]
    return "\n".join(lines)


def thresholds_json(res: dict) -> dict:
    return {
        "suppress_p1_max": res["suppress_p1_max"],
        "suppress_conf_min": res["recommended_conf_min"],
        "page_p1p2_min": res["page_p1p2_min"],
        "uncertain_conf_max": res["uncertain_conf_max"],
        "queue_conf_min": res["queue_conf_min"],
        "tuner_meta": {
            "c_fp": res["c_fp"],
            "c_fn": res["c_fn"],
            "alerts_evaluated": res["alerts_evaluated"],
            "baseline_pages": res["baseline_pages"],
            "allowlist_size": res["allowlist_size"],
            "grid": res["grid"],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Tune Sentinel suppression thresholds")
    ap.add_argument("--labels", required=True, help="JSONL labeled history")
    ap.add_argument("--c-fp", type=float, default=100.0)
    ap.add_argument("--c-fn", type=float, default=50000.0)
    ap.add_argument("-o", "--output", required=True, help="thresholds.json path")
    args = ap.parse_args()

    rows = []
    with open(args.labels, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        raise SystemExit("no label rows found")

    res = tune(rows, c_fp=args.c_fp, c_fn=args.c_fn)
    print(format_projection(res))
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(thresholds_json(res), fh, indent=2)
    print(f"\nwrote thresholds -> {args.output}")


if __name__ == "__main__":
    main()
