"""Shadow Report generator — the week-one deliverable (design 06 §b).

One page. Same format every week. Consumes the ShadowStore's episodes +
observations and produces the report the buyer reads in five minutes:

  * header: the numbers (observed / human paged / would-page / agreement /
    noise-reduction OPPORTUNITY / divergences / ZERO SEV1/SEV2 divergences /
    gate p99 latency / calibration / tap health / credential inventory);
  * the numbered divergence list (both directions, SEV1/SEV2 as a separate
    highlighted block even when the count is zero — especially then);
  * the calibration summary (Oracle §7: decision calibration with Wilson
    95% bounds; N<30 bands print THIN — no claim; >5pp miscalibration in
    any band with N>=30 HOLDS the stage gate);
  * the zero-SEV1/SEV2 divergence bar: did the shadow ever suggest
    suppress on something the legacy stack paged AND a human confirmed
    real (estate priority field at final resolution)? The bar is ZERO.

Deliberately NOT in the report (design 06 §b.4): no model metrics
(accuracy/F1/ROC), no projected savings beyond the labeled opportunity
number, no remediation advice for the estate's alerting.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from .shadow import (SEV12, WOULD_PAGE, ShadowObservation, ShadowStore,
                     threshold_counterfactual)


# ---------------------------------------------------------------- constants

# Confidence bands for the calibration summary (design 06 §b.3).
CONFIDENCE_BANDS = (
    (0.95, 1.00),
    (0.90, 0.94),
    (0.85, 0.89),
    (0.70, 0.84),
    (0.50, 0.69),
)
MIN_BAND_N = 30            # below this: THIN — no claim
MISCALIBRATION_HOLD_PP = 5.0  # beyond this in any band with N>=30: stage hold

HUMAN_PAGED = "paged"


# ---------------------------------------------------------------- report

def generate_shadow_report(store: ShadowStore, *, org: str, week_label: str,
                           window_start: str, window_end: str,
                           credential_inventory: dict | None = None) -> dict:
    """Build the weekly Shadow Report dict. Pure function of the store."""
    episodes = list(store.episodes.values())
    # Latest gate-evaluated observation per episode (trigger / priority-updated).
    latest_eval: dict[str, ShadowObservation] = {}
    for obs in store.observations:
        if obs.gate_would in WOULD_PAGE | {"suppress"}:
            latest_eval[obs.episode_key] = obs

    evaluated = [(ep, latest_eval[ep.key]) for ep in episodes if ep.key in latest_eval]
    paged_eps = [p for p in evaluated if p[0].human_paged]
    unpaged_resolved = [p for p in evaluated
                        if not p[0].human_paged
                        and (p[0].human_resolved or p[0].human_acknowledged)]

    would_page_n = sum(1 for _, o in evaluated if o.gate_would in WOULD_PAGE)
    would_suppress_n = sum(1 for _, o in evaluated if o.gate_would == "suppress")

    agreements = sum(1 for ep, o in evaluated
                     if (ep.human_paged and o.gate_would in WOULD_PAGE)
                     or (not ep.human_paged and o.gate_would == "suppress"))
    denom = len(evaluated)
    agreement_pct = 100.0 * agreements / denom if denom else 100.0

    divergences = _build_divergences(evaluated)
    sev12 = [d for d in divergences if d["severity"] in SEV12]
    zero_bar = zero_sev12_divergence_bar(divergences)

    latencies = sorted(o.gate_latency_ms for _, o in evaluated
                       if o.gate_latency_ms is not None)
    p99 = latencies[max(0, math.ceil(0.99 * len(latencies)) - 1)] if latencies else None

    calibration = _calibration_table(evaluated)
    cal_hold = any(r["n"] >= MIN_BAND_N and r["delta_pp"] is not None
                   and r["delta_pp"] > MISCALIBRATION_HOLD_PP for r in calibration)

    health = store.tap_health()
    inventory = credential_inventory or {"write_credentials": []}

    return {
        "org": org,
        "week_label": week_label,
        "window": {"start": window_start, "end": window_end},
        "read_only": True,
        "header": {
            "alerts_observed": len(episodes),
            "episodes_evaluated": denom,
            "human_paged": len(paged_eps),
            "human_unpaged_resolved": len(unpaged_resolved),
            "sentinel_would_page": would_page_n,
            "sentinel_would_suppress": would_suppress_n,
            "agreement_on_pages_pct": round(agreement_pct, 1),
            "noise_reduction_opportunity_pct": round(
                100.0 * would_suppress_n / denom, 1) if denom else 0.0,
            "divergences_total": len(divergences),
            "divergences_suppress_vs_page": sum(
                1 for d in divergences if d["direction"] == "suppress-vs-page"),
            "divergences_page_vs_suppress": sum(
                1 for d in divergences if d["direction"] == "page-vs-suppress"),
            "zero_sev12_divergences": zero_bar["passed"],
            "zero_sev12_divergence_count": zero_bar["count"],
            "gate_p99_latency_ms": round(p99, 1) if p99 is not None else None,
            "calibration_hold": cal_hold,
        },
        "divergences": divergences,
        "sev12_divergences": sev12,   # separate highlighted block (§b.2)
        "calibration": calibration,
        "tap_health": health,
        "credential_inventory": inventory,
        "credential_inventory_hash": _inventory_hash(inventory),
        "zero_sev12_bar": zero_bar,
    }


def _build_divergences(evaluated) -> list[dict]:
    """Both directions, numbered, permanent within the report (§b.2)."""
    rows = []
    for ep, obs in sorted(evaluated, key=lambda p: p[1].received_at):
        human = HUMAN_PAGED if ep.human_paged else "unpaged"
        would = obs.gate_would
        direction = None
        if ep.human_paged and would == "suppress":
            direction = "suppress-vs-page"      # the ROI direction
        elif not ep.human_paged and would == "page_now":
            direction = "page-vs-suppress"      # the safety direction
        if direction is None:
            continue
        ev = obs.evidence or {}
        rows.append({
            "id": f"D-{len(rows) + 1:03d}",
            "direction": direction,
            "severity": ep.final_severity or ep.estate_severity or "unknown",
            "severity_at_occurrence": ep.estate_severity or "unknown",
            "service": ep.service,
            "alert_key": ep.key,
            "fingerprint": _fingerprint_of(obs),
            "human_did": human,
            "human_detail": _human_detail(ep),
            "gate_would": would,
            "gate_confidence": obs.gate_confidence,
            "gate_evidence": {
                "jev_model": ev.get("jev_model"),
                "severity_probs": ev.get("severity_probs", {}),
                "disposition_choice": ev.get("disposition_choice"),
                "allowlist_member": ev.get("allowlist_member"),
            },
            "evidence_refs": {
                "incident_url": ev.get("incident_url"),
                "observation_id": obs.observation_id,
            },
            "threshold_counterfactual": obs.threshold_counterfactual,
            "status": "OPEN",   # awaiting buyer review; ledger owns transitions
        })
    return rows


def _human_detail(ep) -> str:
    parts = []
    if ep.human_paged:
        parts.append("paged")
    if ep.human_acknowledged:
        parts.append("acknowledged")
    if ep.human_resolved:
        parts.append("resolved")
    return ", ".join(parts) or "no paging signal observed"


def _fingerprint_of(obs: ShadowObservation) -> str:
    return (obs.evidence or {}).get("fingerprint", "")


# ---------------------------------------------------------------- the bar

def zero_sev12_divergence_bar(divergences: list[dict]) -> dict:
    """The one non-negotiable number: did the shadow ever suggest SUPPRESS
    on something the legacy stack PAGED and a human confirmed REAL
    (estate SEV1/SEV2 at final resolution)? The bar is ZERO."""
    rows = [d for d in divergences
            if d["direction"] == "suppress-vs-page"
            and d["severity"] in SEV12]
    return {"passed": len(rows) == 0, "count": len(rows), "rows": rows,
            "definition": ("shadow suggested suppress on a human-paged "
                           "SEV1/SEV2 (estate severity at final resolution)")}


# ---------------------------------------------------------------- calibration

def _wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% interval for a binomial proportion (Oracle §7)."""
    if n == 0:
        return 0.0, 0.0
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _calibration_table(evaluated) -> list[dict]:
    """Decision calibration per confidence band (design 06 §b.3)."""
    rows = []
    for lo, hi in CONFIDENCE_BANDS:
        band = [(ep, o) for ep, o in evaluated
                if o.gate_confidence is not None and lo <= o.gate_confidence <= hi
                and o.gate_would in WOULD_PAGE | {"suppress"}]
        n = len(band)
        if n == 0:
            rows.append({"band": f"{lo:.2f}–{hi:.2f}", "n": 0, "thin": True,
                         "gate_p_page": None, "observed_p_human_paged": None,
                         "delta_pp": None, "wilson_lo": None, "wilson_hi": None})
            continue
        gate_p = (lo + hi) / 2
        observed = sum(1 for ep, _ in band if ep.human_paged) / n
        lo_w, hi_w = _wilson(observed, n)
        thin = n < MIN_BAND_N
        rows.append({
            "band": f"{lo:.2f}–{hi:.2f}",
            "n": n,
            "thin": thin,
            "gate_p_page": round(gate_p, 3),
            "observed_p_human_paged": round(observed, 3),
            "delta_pp": None if thin else round(abs(gate_p - observed) * 100, 1),
            "wilson_lo": round(lo_w, 3),
            "wilson_hi": round(hi_w, 3),
        })
    return rows


# ---------------------------------------------------------------- inventory

def _inventory_hash(inventory: dict) -> str:
    canonical = json.dumps(inventory, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- rendering

def render_report_markdown(report: dict, race_budget_ms: int = 2700) -> str:
    """Render the report in the exact §b format: one page, same every week.

    race_budget_ms: the race-to-page budget B whose p99 gate latency the
    report compares against (2700ms default per the race-to-page lane,
    PR #19 — the Oracle N=100 re-derivation; the old 1000ms seed is
    retired). Pass B explicitly when rendering against a different
    budget so the number never silently drifts.
    """
    h = report["header"]
    th = report["tap_health"]
    inv = report["credential_inventory"]
    L = []
    A = L.append
    A(f"# Shadow Report — week of {report['week_label']} "
      f"(read-only, zero stack changes)")
    A(f"Org: {report['org']} · window {report['window']['start']} → "
      f"{report['window']['end']}")
    A("")
    A(f"Alerts observed: **{h['alerts_observed']}** · "
      f"Human stack paged: **{h['human_paged']}** · "
      f"Human stack unpaged/resolved: **{h['human_unpaged_resolved']}**")
    A("")
    A(f"Sentinel would have paged: **{h['sentinel_would_page']}** · "
      f"Agreement on pages: **{h['agreement_on_pages_pct']}%**")
    A("")
    A(f"Would-have-suppressed: **{h['sentinel_would_suppress']}** — "
      f"{h['noise_reduction_opportunity_pct']}% noise-reduction *OPPORTUNITY*. "
      f"(Labeled OPPORTUNITY, forever. It is not a promise; it is the number "
      f"the evidence would have to earn.)")
    A("")
    A(f"**Divergences: {h['divergences_total']}** — "
      f"{h['divergences_suppress_vs_page']} suppress-vs-page, "
      f"{h['divergences_page_vs_suppress']} page-vs-suppress. "
      f"All listed below with evidence links. "
      f"**{'Zero' if h['zero_sev12_divergences'] else str(h['zero_sev12_divergence_count'])} "
      f"divergences on SEV1/SEV2.**")
    A("")
    if h["gate_p99_latency_ms"] is not None:
        A(f"Gate p99 decision latency: **{h['gate_p99_latency_ms']}ms** "
          f"(race-to-page budget {race_budget_ms}ms — measured on your traffic).")
        A("")
    A("Calibration: " + ("**HOLD — miscalibration beyond 5pp in a band with "
                         "N≥30: shadow continues, backtest is blocked until "
                         "the tuner re-fits.**" if h["calibration_hold"]
                         else "within 5pp at all bands with N≥30 "
                                "(Oracle §7 — decision calibration, Wilson "
                                "bounds; bands and N printed below)."))
    A("")
    d = th["dropped_by_reason"]
    A(f"Tap health: {th['ingested']}/{th['ingested'] + th['dropped']} events "
      f"ingested ({100.0 * th['ingested'] / max(1, th['ingested'] + th['dropped']):.1f}%); "
      f"{th['dropped']} dropped (bad-signature: {d['bad_signature']}; "
      f"unauthenticated: {d['unauthenticated']}; "
      f"unparseable: {d['unparseable']}; "
      f"duplicate-collapsed: {th['duplicate_collapsed']}); "
      f"payload completeness {th['payload_completeness_pct']:.1f}%.")
    A("")
    A(f"Credential inventory: write credentials provisioned: "
      f"**{inv.get('write_credentials') or 'none'}** "
      f"(hash {report['credential_inventory_hash']}…).")
    A("")
    A("## SEV1/SEV2 divergences")
    if report["sev12_divergences"]:
        for div in report["sev12_divergences"]:
            A(_render_divergence(div))
    else:
        A("**Zero divergences on SEV1/SEV2.** Printed in bold every week it "
          "is true — because the week it stops being true is the week "
          "everything stops.")
    A("")
    A("## Divergence list")
    if report["divergences"]:
        for div in report["divergences"]:
            A(_render_divergence(div))
    else:
        A("No divergences this week.")
    A("")
    A("## Calibration summary")
    A("")
    A("confidence band | N | gate said P(page) | observed P(human paged) | "
      "|Δ| | Wilson 95%")
    A("---|---|---|---|---|---")
    for r in report["calibration"]:
        if r["n"] == 0:
            A(f"{r['band']} | 0 | — | — | — | **THIN — no claim**")
        elif r["thin"]:
            A(f"{r['band']} | {r['n']} | {r['gate_p_page']} | "
              f"{r['observed_p_human_paged']} | — | "
              f"[{r['wilson_lo']}, {r['wilson_hi']}] **THIN — no claim**")
        else:
            A(f"{r['band']} | {r['n']} | {r['gate_p_page']} | "
              f"{r['observed_p_human_paged']} | {r['delta_pp']}pp | "
              f"[{r['wilson_lo']}, {r['wilson_hi']}]")
    A("")
    A("_Not in this report, by design: no model metrics (accuracy/F1/ROC), "
      "no projected savings beyond the labeled opportunity number, no "
      "remediation advice for your alerting. The divergence list implies "
      "them; you draw the conclusions._")
    return "\n".join(L) + "\n"


def _render_divergence(div: dict) -> str:
    ev = div["gate_evidence"]
    probs = ev.get("severity_probs") or {}
    cf = div["threshold_counterfactual"] or {}
    cf_str = "; ".join(f"at {k} → {v}" for k, v in cf.items())
    refs = div["evidence_refs"]
    lines = [
        f"{div['id']}  direction: {div['direction']}   "
        f"severity: {div['severity']}   service: {div['service']}",
        f"  alert_key:    {div['alert_key']} · fingerprint {div['fingerprint']}",
        f"  human did:    {div['human_did']} ({div['human_detail']})",
        f"  gate would:   {div['gate_would']} "
        f"(confidence {div['gate_confidence']})",
        f"                severity probs: "
        + ", ".join(f"{k}={v:.3f}" for k, v in sorted(probs.items())),
        f"                allowlist member: {ev.get('allowlist_member')}",
        f"  evidence:     incident: {refs.get('incident_url') or 'n/a'} · "
        f"observation: {refs.get('observation_id')}",
        f"  threshold counterfactual: {cf_str or 'n/a'}",
        f"  status: {div['status']} — awaiting buyer review.",
        "",
    ]
    return "\n".join(lines)
