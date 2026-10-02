"""The three Jev questions — frozen contract §3.3 of ARCHITECTURE.md.

Design law (from the architecture doc, non-negotiable):
  * every Choice option carries a one-line description — bare labels are banned;
  * `cannot_determine` is mandatory on all three questions (Jev docs rule).

Descriptions below are copied verbatim from §3.3.
"""

from __future__ import annotations

_Q1_INSTRUCTIONS = "Classify the severity of this production alert."
_Q2_INSTRUCTIONS = "Identify the team that owns this alert."
_Q3_INSTRUCTIONS = (
    "Decide how this alert should be handled: page now, queue for "
    "business hours, suppress, or pass through."
)

# Q1 severity (Choice) — the disposition anchor.
_SEVERITY_CRITERIA: dict[str, str] = {
    "p1_critical": (
        "Customer-facing outage or data-loss risk in progress; revenue/SLA "
        "actively burning; needs a human in under 5 minutes."
    ),
    "p2_high": (
        "Core function degraded or at imminent risk of full outage; needs a "
        "human within about 30 minutes."
    ),
    "p3_medium": (
        "Non-critical degradation or early warning; safe to handle in "
        "business hours; no page needed."
    ),
    "p4_low": (
        "Informational; no action required unless it recurs; never pages."
    ),
    "known_noise": (
        "Matches a recurring benign pattern (flap, self-clearing spike, "
        "planned-work artifact); historically never became an incident."
    ),
    "cannot_determine": (
        "The alert context is insufficient or contradictory to assign a "
        "severity; do not guess."
    ),
}

# Q2 owning_team (Choice) — defaults; customers override at onboarding.
_DEFAULT_TEAM_CRITERIA: dict[str, str] = {
    "platform": "Core infra, kubernetes, CI/CD runners, deploy pipeline.",
    "network": "DNS, CDN, load balancers, VPC, transit — connectivity and edge.",
    "data": "Databases, caches, queues, pipelines — persistence and streaming.",
    "product_backend": "Application services and APIs owned by product engineering.",
    "security": "Auth, WAF, intrusion, certificate expiry — the security on-call.",
    "cannot_determine": (
        "No clear owner from the alert context; route to the default "
        "escalation policy."
    ),
}

# Q3 disposition (Choice) — the money question; descriptions embed the
# conservative policy.
_DISPOSITION_CRITERIA: dict[str, str] = {
    "page_now": (
        "A human must be woken or paged immediately; this is or may be a "
        "real customer-impacting event."
    ),
    "page_business_hours": (
        "Route to the queue for next-business-hours handling; do not wake "
        "anyone."
    ),
    "suppress": (
        "Safe to drop: confirmed known noise with a clean historical record; "
        "no human needs to see it. Only choose with very high certainty."
    ),
    "cannot_determine": (
        "Not enough evidence to act; default to the existing pipeline — "
        "page as before, drop nothing."
    ),
}

_MAX_TEAM_OPTIONS = 8  # customer-supplied options (excl. cannot_determine)


def build_questions(
    team_options: list[tuple[str, str]] | None = None,
) -> dict:
    """Return the three question dicts, wire-format ready.

    `team_options`: ≤8 customer (option, description) pairs replacing the
    six defaults. `cannot_determine` is appended automatically and is
    mandatory — it cannot be removed by an override.
    """
    if team_options is None:
        team_criteria = dict(_DEFAULT_TEAM_CRITERIA)
    else:
        if len(team_options) > _MAX_TEAM_OPTIONS:
            raise ValueError(
                f"At most {_MAX_TEAM_OPTIONS} custom team options are supported "
                f"(got {len(team_options)})."
            )
        team_criteria = {name: desc for name, desc in team_options}
        team_criteria["cannot_determine"] = _DEFAULT_TEAM_CRITERIA["cannot_determine"]
        for name, desc in team_criteria.items():
            if not desc or not desc.strip():
                raise ValueError(
                    f"Team option '{name}' has an empty description — "
                    "bare labels are banned by design."
                )

    return {
        "severity": {
            "type": "choice",
            "instructions": _Q1_INSTRUCTIONS,
            "criteria": dict(_SEVERITY_CRITERIA),
        },
        "owning_team": {
            "type": "choice",
            "instructions": _Q2_INSTRUCTIONS,
            "criteria": team_criteria,
        },
        "disposition": {
            "type": "choice",
            "instructions": _Q3_INSTRUCTIONS,
            "criteria": dict(_DISPOSITION_CRITERIA),
        },
    }
