"""Stage 1 divergence backtest — replay procedure support (design 06 §c).

The shadow proves the gate on *current* traffic. The backtest proves it on
the incidents the buyer's team still talks about — the 3 AMs in the
postmortem archive.

Procedure (design 06 §c.1):
  Step 0 — scope agreement (signed): services, window, and the buyer's
      severity taxonomy IN WRITING. If the estate has no formal taxonomy,
      the backtest cannot run.
  Step 1 — extract: read-only API keys; PD GET /incidents + log entries;
      Opsgenie alert API + action log; Alertmanager-native estates degrade
      per the documented order (the honest caveat).
  Step 2 — label (ground truth = the estate's own records), versioned
      labeling rules (LABELING_RULES_VERSION, pinned in the ledger
      header):
        real-SEV1/SEV2: estate priority/severity at final resolution OR
            postmortem tag (postmortem tags win ties; provenance recorded).
        real-SEV3+: paged and human-acknowledged with action taken.
        noise: silenced, auto-resolved with no human action, never
            acknowledged, or tagged "known flaky".
        ambiguous: everything else — EXCLUDED from both the
            false-suppress denominator and the noise-precision numerator
            (reported as a count, never silently assigned).
  Step 3 — replay: the historical alert stream through the gate OFFLINE,
      chronological, episode semantics intact, EXACT production config
      frozen at replay start (model pin, thresholds version, allowlist
      snapshot, calibration-fit version — all pinned in the ledger
      header). A backtest that "fixes" the gate mid-replay proves nothing.
  Step 4 — compare against OUTCOMES, not pages:
      PRIMARY: false-suppress count on postmortem-confirmed real
          SEV1/SEV2. The bar is ZERO. Not "low." Zero.
      Secondary: suppression precision on labeled noise (the ROI evidence).
      Secondary: page-vs-suppress on real incidents (usually good news).
      Reported: ambiguous count, window, label provenance, freeze manifest.
  Step 5 — the false-negative hunt: every miss root-caused to a mechanism
      and either fixed (version recorded) or explicitly accepted in
      writing; every miss becomes a regression case with a permanent ID.

This module implements steps 3–4 and the signed Backtest Ledger schema
(§f artifact 3). Extraction (step 1) and labeling (step 2) are the
buyer's estate work; they arrive here as labeled BacktestIncidents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

LABELING_RULES_VERSION = "labeling-rules v2"

LABEL_REAL_SEV12 = "real-SEV1/SEV2"
LABEL_REAL_SEV3P = "real-SEV3+"
LABEL_NOISE = "noise"
LABEL_AMBIGUOUS = "ambiguous"
VALID_LABELS = frozenset({LABEL_REAL_SEV12, LABEL_REAL_SEV3P, LABEL_NOISE,
                          LABEL_AMBIGUOUS})


# ---------------------------------------------------------------- inputs

@dataclass
class BacktestIncident:
    """One historical incident, labeled from the estate's own records."""
    incident_id: str
    date: str                          # ISO-8601
    estate_severity: str               # buyer's taxonomy (e.g. "SEV2")
    severity_provenance: str           # which field/system/timestamp
    postmortem_ref: str | None
    human_handling: list               # [(ts, action)] — paged/acked/resolved...
    alerts: list                       # chronological Alert objects (replay stream)
    label: str                         # one of VALID_LABELS
    label_provenance: str


@dataclass
class BacktestRow:
    """One ledger row per historical SEV1/SEV2 (§f artifact 3)."""
    incident_id: str
    date: str
    estate_severity: str
    severity_provenance: str
    postmortem_ref: str | None
    human_handling: list
    gate_disposition_on_replay: str
    gate_confidence: float | None
    agreement: str                     # "agreement" | "miss"
    miss_mechanism: str | None         # root-caused, named — or "pending-review"
    miss_disposition: str | None       # "fixed-in-gate-version-X" |
                                       # "buyer-accepted-residual-risk:<risk>"
    regression_case_id: str | None


# ---------------------------------------------------------------- replay

def replay_backtest(incidents: list[BacktestIncident], decide_fn,
                    *, config_freeze: dict, window: dict,
                    extraction_ts: str | None = None,
                    caveats: tuple[str, ...] = ()) -> dict:
    """Run the Stage 1 backtest.

    decide_fn(alert) -> Disposition: the gate under the EXACT frozen
    production config (model pin, thresholds, allowlist, fit version).
    The replay never mutates the gate.

    Returns the signed Backtest Ledger dict: header + one row per
    historical SEV1/SEV2 + summary with the zero-miss bar.
    """
    for inc in incidents:
        if inc.label not in VALID_LABELS:
            raise ValueError(f"incident {inc.incident_id}: bad label {inc.label!r}")

    rows: list[BacktestRow] = []
    miss_seq = 0
    page_vs_suppress_on_real: list[dict] = []
    noise_suppressed = 0
    noise_total = 0
    ambiguous_count = 0

    for inc in sorted(incidents, key=lambda i: i.date):
        stream = sorted(inc.alerts, key=lambda a: a.received_at)
        dispositions = []
        for alert in stream:
            try:
                disp = decide_fn(alert)
            except Exception:
                disp = None
            dispositions.append(disp)
        # The paging decision is the opening alert's disposition: that is
        # what the estate's escalation engine acted on at alert time.
        first = dispositions[0] if dispositions else None
        gate_action = getattr(first, "action", "passthrough") or "passthrough"
        gate_conf = getattr(first, "confidence", None)

        human_paged = any(a == "paged" for _, a in inc.human_handling)
        agreement = "agreement"
        miss_mechanism = None
        miss_disposition = None
        regression_case_id = None

        # D3 note: "folded" (storm-continuation absorbed into the aggregate
        # page) is deliberately NOT "suppress" here — the aggregate pages,
        # so a folded continuation is not a missed page. But this replay is
        # per-incident over the raw alert stream: it cannot see the
        # synthetic aggregate digest page itself (the digest is emitted by
        # the live pipeline, not by decide_fn). Folded accounting is
        # correct; aggregate-page coverage is a known granularity gap.
        if inc.label == LABEL_AMBIGUOUS:
            ambiguous_count += 1
        elif inc.label == LABEL_REAL_SEV12:
            if gate_action == "suppress":
                agreement = "miss"
                miss_seq += 1
                miss_mechanism = "pending-review"  # Step 5: human root-cause
                regression_case_id = f"R-{miss_seq:03d}"
        elif inc.label == LABEL_NOISE:
            noise_total += 1
            if gate_action == "suppress":
                noise_suppressed += 1

        # Secondary, all real incidents: the gate would have paged what
        # humans didn't — usually good news (a missed page the estate
        # didn't know it missed). Only for non-misses.
        if (inc.label in (LABEL_REAL_SEV12, LABEL_REAL_SEV3P)
                and agreement == "agreement"
                and not human_paged and gate_action == "page_now"):
            page_vs_suppress_on_real.append({
                "incident_id": inc.incident_id,
                "estate_severity": inc.estate_severity,
                "gate": gate_action,
            })

        rows.append(BacktestRow(
            incident_id=inc.incident_id, date=inc.date,
            estate_severity=inc.estate_severity,
            severity_provenance=inc.severity_provenance,
            postmortem_ref=inc.postmortem_ref,
            human_handling=list(inc.human_handling),
            gate_disposition_on_replay=gate_action,
            gate_confidence=gate_conf,
            agreement=agreement, miss_mechanism=miss_mechanism,
            miss_disposition=miss_disposition,
            regression_case_id=regression_case_id))

    sev12_rows = [r for r in rows
                  if _incident_label(incidents, r.incident_id) == LABEL_REAL_SEV12]
    false_suppress = [r for r in sev12_rows if r.agreement == "miss"]
    zero_bar_passed = len(false_suppress) == 0

    return {
        "artifact": "signed-backtest-ledger",
        "header": {
            "window": window,
            "extraction_ts": extraction_ts or _utcnow_iso(),
            "labeling_rules_version": LABELING_RULES_VERSION,
            "config_freeze": config_freeze,
            "caveats": list(caveats),
        },
        "rows": [r.__dict__ for r in rows],
        "summary": {
            # PRIMARY — the one non-negotiable number (design 06 §c.1).
            "false_suppress_real_sev12": len(false_suppress),
            "zero_bar_passed": zero_bar_passed,
            "sev12_incidents": len(sev12_rows),
            # Secondary — the ROI evidence.
            "suppression_precision_on_noise": (
                noise_suppressed / noise_total if noise_total else None),
            "noise_suppressed": noise_suppressed,
            "noise_total": noise_total,
            # Secondary — the good-news list.
            "page_vs_suppress_on_real": page_vs_suppress_on_real,
            # Reported, not hidden.
            "ambiguous_count": ambiguous_count,
            "incident_count": len(incidents),
            "regression_corpus": [
                {"regression_case_id": r.regression_case_id,
                 "incident_id": r.incident_id,
                 "miss_mechanism": r.miss_mechanism}
                for r in false_suppress
            ],
        },
    }


def _incident_label(incidents: list[BacktestIncident], incident_id: str) -> str:
    for inc in incidents:
        if inc.incident_id == incident_id:
            return inc.label
    return LABEL_AMBIGUOUS


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
