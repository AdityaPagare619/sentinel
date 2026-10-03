"""Threshold simulator math (Forge's lane).

Oracle's rule: simulator math IDENTICALLY EQUALS tuner math. This module
does not reimplement classification — it calls sentinel.tuner's
fingerprint_stats / candidate_allowlist / classify / tune directly. The
contract's ThresholdSet maps 1:1 onto tuner.classify's parameters.

One deliberate, documented choice: the v0.1 tuner folds Q3-uncertain
alerts into the "baseline" bucket (the gate applies the
uncertain_conf_max page rule at runtime). uncertain_conf_max is
accepted, validated, and echoed, but the projection buckets follow the
tuner exactly — simulator math == tuner math, no second policy.
"""

from __future__ import annotations

from sentinel import quantized
from sentinel.tuner import (candidate_allowlist, classify, fingerprint_stats,
                            tune)

CONF_GRID_NOTE = ("expected-cost optimum on the confidence grid "
                  "{0.80, 0.85, 0.90, 0.95}; ties broken by max avoided "
                  "page cost, then most conservative bar")

# Sane bounds for a caller-supplied threshold set (-> 422 past these).
_BOUNDS = {
    "suppress_p1_max": (0.0, 0.10),     # exclusive lo
    "suppress_conf_min": (0.0, 1.0),    # exclusive lo
    "page_p1p2_min": (0.0, 1.0),
    "uncertain_conf_max": (0.0, 1.0),
    "queue_conf_min": (0.0, 1.0),
}


def validate_thresholds(t: dict) -> dict:
    """Type/bounds check a ThresholdSet. Raises ValueError -> HTTP 422."""
    if not isinstance(t, dict):
        raise ValueError("thresholds must be an object")
    out = {}
    for key, (lo, hi) in _BOUNDS.items():
        if key not in t:
            raise ValueError(f"thresholds.{key} is required")
        try:
            v = float(t[key])
        except (TypeError, ValueError):
            raise ValueError(f"thresholds.{key} must be a number")
        if not (lo < v <= hi):
            raise ValueError(
                f"thresholds.{key}={v} out of bounds ({lo}, {hi}]")
        out[key] = v
    return out


def validate_cost_model(c: dict | None) -> tuple[float, float]:
    if c is None:
        return 100.0, 50000.0
    try:
        c_fp, c_fn = float(c["c_fp"]), float(c["c_fn"])
    except (TypeError, KeyError, ValueError):
        raise ValueError("cost_model needs numeric c_fp and c_fn")
    if c_fp <= 0 or c_fn <= 0:
        raise ValueError("cost_model costs must be positive")
    return c_fp, c_fn


def project(rows: list[dict], thresholds: dict, c_fp: float, c_fn: float,
            window_days: int = 30) -> dict:
    """Pure recompute over stored probabilities. Deterministic."""
    stats = fingerprint_stats(rows)
    allowlist = candidate_allowlist(stats)
    buckets: dict[str, list[dict]] = {"suppress": [], "page_now": [],
                                      "queue": [], "baseline": []}
    for r in rows:
        buckets[classify(r, thresholds["suppress_p1_max"],
                         thresholds["suppress_conf_min"], allowlist,
                         thresholds["page_p1p2_min"],
                         thresholds["queue_conf_min"])].append(r)
    suppressed = buckets["suppress"]
    # Expected false suppresses = sum of P(p1_critical) over suppressed
    # (linearity of expectation — no independence assumption; tuner.py).
    exp_false = sum(float((r.get("q1_probs") or {}).get("p1_critical", 0.0))
                    for r in suppressed)
    n = len(rows)
    projection = {
        "n_alerts": n,
        "suppress": len(suppressed),
        "page_now": len(buckets["page_now"]),
        "queue": len(buckets["queue"]),
        "baseline": len(buckets["baseline"]),
        "exp_false_suppresses": round(exp_false, 4),
        "expected_cost": round(exp_false * c_fn, 2),
        "avoided_page_cost": round(len(suppressed) * c_fp, 2),
        "suppress_rate": round(len(suppressed) / n, 4) if n else 0.0,
        "pages_per_night_avoided": round(len(suppressed) / window_days, 1)
                                   if window_days else 0.0,
    }
    # The tuner's own recommendation (expected-cost optimum on its grid).
    tuned = tune(rows, c_fp=c_fp, c_fn=c_fn)
    projection["recommended"] = {
        "conf_min": tuned["recommended_conf_min"],
        "note": CONF_GRID_NOTE,
    }
    return projection


def policy_version() -> str:
    """Real version constant from the engine's gate formula."""
    return quantized.GATE_FORMULA_VERSION
