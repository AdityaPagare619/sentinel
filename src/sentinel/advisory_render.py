"""Deterministic advisory renderers — pure functions, zero Jev.

Jev is a typed decision model: it answers Choice/Score/Noul questions, it
writes no prose and does no math. Every paragraph/brief/bundle in the T5
advisory directions is therefore RENDERED HERE, from (typed Jev answers +
already-recorded evidence). Jev's contribution is selection/ranking only;
the words are code, so no sentence can hallucinate a fact it wasn't given.

Purity contract (enforced by tests):
  * stdlib only — this module imports nothing from sentinel, nothing
    networked, nothing clocked. Same inputs => byte-identical outputs.
  * Every renderer takes the Jev answers as plain values (choice strings,
    ordinal levels), never Answer/DecisionResponse objects.

Track 8 consumes these renderers' outputs through the labeling contract
(src/sentinel/advisory.py::label_envelope): nothing rendered here is ever
shown as fact or as a decision.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# D1 — suppression_explainer paragraph
# ---------------------------------------------------------------------------

# One explanatory sentence per evidence leg. The Jev Choice (dominant_leg)
# picks WHICH of these the human on-call would find least obvious; the
# sentences themselves are fixed. Keys must match the leg names offered in
# advisory.d1_questions().
_LEG_SENTENCES: dict[str, str] = {
    "allowlist_hit": (
        "the alert's fingerprint matched an allowlist entry, which may not "
        "have been obvious as covering this check"
    ),
    "dual_attestation": (
        "the allowlist entry carried two fresh human attestations, the "
        "independent witness the suppress leg requires"
    ),
    "known_noise_pattern": (
        "the alert matched a recurring benign pattern with a clean "
        "historical record"
    ),
    "corroboration_floor": (
        "an independent corroborating signal confirmed the benign read"
    ),
    "freshness_ok": (
        "the evidence freshness legs were green, so the suppress "
        "conjunction was allowed to evaluate"
    ),
    "severity_confidence_low": (
        "Jev's own severity read was low-confidence, below the suppress "
        "floor the policy table requires"
    ),
    "disposition_confidence": (
        "the disposition answer cleared the policy table's suppress "
        "confidence floor"
    ),
}

_NEUTRAL_EMPHASIS = "no single evidence leg stood out"


def render_suppression_paragraph(*, reason: str, legs: list[str],
                                 emphasis_leg: str | None,
                                 alert_id: str, fingerprint: str,
                                 recorded_ts: str) -> str:
    """Render the D1 "why you weren't woken" paragraph.

    ``legs``: the recorded evidence-leg names (in record order).
    ``emphasis_leg``: Jev's dominant_leg Choice answer, or None for the
    deterministic fallback (neutral emphasis, no second-look tag — the
    caller handles the tag).
    """
    leg_list = ", ".join(legs) if legs else "no recorded evidence legs"
    if emphasis_leg and emphasis_leg in _LEG_SENTENCES:
        emphasis = (f"the least obvious part of this decision is that "
                    f"{_LEG_SENTENCES[emphasis_leg]}")
    else:
        emphasis = _NEUTRAL_EMPHASIS
    return (
        f"Alert {alert_id} was suppressed (reason: {reason}); no page was "
        f"sent. From the recorded evidence, {emphasis}. "
        f"Evidence on record: {leg_list}. "
        f"Suppression recorded at {recorded_ts} for fingerprint {fingerprint}; "
        f"the suppression stands exactly as decided."
    )


# ---------------------------------------------------------------------------
# D2 — storm_briefer brief
# ---------------------------------------------------------------------------

def render_storm_brief(*, storm_size: int, storm_counts: dict,
                       clusters: list[str], lead_pick: str | None,
                       novelty_level: int | None) -> str:
    """Render the D2 one-paragraph storm brief.

    ``lead_pick``: Jev's brief_lead Choice (top member cluster), or None.
    ``novelty_level``: Jev's pattern_novelty Score (2-10 ordinal), or None.
    """
    n_clusters = len(clusters)
    cluster_str = ", ".join(clusters[:5])
    if n_clusters > 5:
        cluster_str += f", +{n_clusters - 5} more"
    lead = (f"Leading cluster: {lead_pick}. "
            if lead_pick and lead_pick != "cannot_determine" else "")
    novelty = (f"Pattern novelty {novelty_level}/10 (ordinal). "
               if novelty_level is not None else "")
    counts = ", ".join(f"{k}={v}" for k, v in sorted(storm_counts.items()))
    return (
        f"Storm of {storm_size} alerts ({counts}). {lead}"
        f"{novelty}"
        f"{n_clusters} member clusters: {cluster_str}."
    )


# ---------------------------------------------------------------------------
# D3 — evidence bundle assembly (deterministic; Jev only ranked)
# ---------------------------------------------------------------------------

def assemble_evidence_bundle(*, incidents: list[dict], runbooks: list[dict],
                             incident_pick: str | None,
                             runbook_pick: str | None,
                             stale: bool) -> dict:
    """Assemble the D3 evidence bundle from ranked retrieval results.

    Ranking is applied by moving Jev's picks to the front; every candidate
    remains present (Jev ranks, code assembles — nothing is dropped by the
    model). ``stale`` downgrades trust in the bundle, never the page.
    """
    def _rank_first(items: list[dict], pick: str | None,
                    key: str) -> list[dict]:
        if not pick or pick == "cannot_determine":
            return list(items)
        picked = [i for i in items if i.get(key) == pick]
        rest = [i for i in items if i.get(key) != pick]
        return picked + rest

    bundle = {
        "incidents": _rank_first(incidents, incident_pick, "incident_id"),
        "runbooks": _rank_first(runbooks, runbook_pick, "runbook_id"),
        "ranked_by": "jev" if (incident_pick or runbook_pick) else "recency",
        "stale_warning": bool(stale),
    }
    if stale:
        bundle["stale_note"] = ("retrieved material may be stale or "
                                "contradictory — treat the bundle as "
                                "untrusted context, not guidance")
    return bundle


# ---------------------------------------------------------------------------
# D4 — RCA hypothesis framing
# ---------------------------------------------------------------------------

def ordinal_level(level: int) -> str:
    """Present a Score answer as an ordinal level — never a probability."""
    return f"plausibility level {int(level)}/10 (ordinal)"


def render_hypothesis(*, hypothesis: str,
                      plausibility_level: int | None) -> dict:
    """Frame a D4 advisory root-cause hypothesis.

    Hypotheses are starting points for the on-call, never conclusions:
    the "unconfirmed" framing is structural, not a label the caller can
    forget.
    """
    level = (ordinal_level(plausibility_level)
             if plausibility_level is not None else "unrated")
    return {
        "hypothesis": hypothesis,
        "plausibility": level,
        "status": "AI-generated hypothesis — unconfirmed",
        "not_a_conclusion": True,
    }


# ---------------------------------------------------------------------------
# D6 — triage suggestion framing
# ---------------------------------------------------------------------------

def render_triage_suggestion(*, owner: str | None,
                             severity: str | None) -> dict:
    """Frame a D6 triage suggestion.

    The suggestion is enrichment on an already-emitted page: the on-call
    human's confirmation is a UI action, never a gate input. The framing
    says so structurally.
    """
    return {
        "suggested_owner": owner,
        "suggested_severity": severity,
        "status": "triage suggestion (AI, unconfirmed) — confirm or override",
        "changes_routing": False,
    }
