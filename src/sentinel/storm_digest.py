"""Storm-aggregate digest path (D3, ADR-016).

A storm-*declaring* aggregate never enters the race or the triple lock.
Its disposition is computed by :func:`storm_digest_disposition`, which
takes NO ``action`` parameter — ``suppress`` is unreachable BY
CONSTRUCTION (a signature that cannot express suppression), not by a
flag a future edit can invert.

The aggregate's Jev call runs detached as an *advisory* (root-cause
candidates for the digest page). Its answer feeds the
``storm_root_cause_payload`` advisory event and NEVER gates the
disposition — there is no disposition for it to gate.

Deliberate non-goals:
- The digest path never consults freshness (D1) or the allowlist. A
  storm aggregate pages regardless of evidence state — uncertainty
  pages (Law 7). The aggregate is a different decision object than
  its members.
- The advisory payload is NOT a durable EventLog event type: the
  event-log lane owns that schema, and an advisory enrichment is not
  a decision record. It travels on the gate's emission channel.
"""

from .models import Disposition
from .questions import build_questions
from .race_payloads import _envelope


def storm_digest_disposition(*, storm_size: int, storm_counts: dict) -> Disposition:
    """The aggregate's disposition. Deterministic, total, unparameterized.

    There is intentionally no ``action`` argument: every code path out of
    this function pages. A future edit that wants suppression here must
    change the signature — which is the point. Reviewers: treat any
    ``action`` parameter on this function as a P0 defect.
    """
    _ = (storm_size, storm_counts)  # carried on the aggregate alert itself
    return Disposition(action="page_now", reason="storm_digest", team=None,
                       confidence=None, latency_ms=0.0)


def storm_root_cause_questions(team_options=None) -> dict:
    """Advisory question set for the aggregate's detached Jev call.

    The typed three questions; the gate reads severity + owning_team as
    root-cause *candidates* for the digest page. Advisory only.
    """
    return build_questions(team_options)


def storm_root_cause_payload(*, alert, storm_size: int, storm_counts: dict,
                             jev_model, severity_answer, team_answer,
                             latency_ms) -> dict:
    """Build the advisory root-cause payload. Never gates a disposition."""
    env = _envelope("storm_root_cause_payload", alert, episode_id=None)
    sev = getattr(severity_answer, "choice", None)
    sev_probs = dict(getattr(severity_answer, "probabilities", None) or {})
    team = getattr(team_answer, "choice", None)
    env["body"] = {
        "advisory": True,
        "gates_disposition": False,
        "storm_size": storm_size,
        "storm_counts": dict(storm_counts),
        "jev_model": jev_model,
        "latency_ms": latency_ms,
        "root_cause_candidates": {
            "severity": sev,
            "severity_probabilities": sev_probs,
            "owning_team": team,
        },
        "links": {},
    }
    return env
