"""Event payload builders for the race lane (ADR-010 / ADR-011).

EMISSION CONTRACT — for the gate/dispatcher layer and the event-log lane:

    The gate calls ``emit(payload)`` with plain dicts shaped like below.
    The event log assigns ``event_id`` / ``seq`` / ``prev_hash`` /
    ``row_hash`` at write time (``seq`` is the authoritative order — never
    the wall-clock ``ts``). The dispatcher fills:

    * ``links.decision_requested_seq`` on decision_made,
    * ``links.decision_made_seq`` on shadow_decision,
    * ``outbox_id`` for page dispositions,
    * ``freshness`` proofs (ADR-014 — the freshness lane owns them),
    * ``threshold_counterfactual`` (ADR-023 — evaluated at event-write time).

    Required body keys (per the event-log lane's validation) are always
    present: decision_made carries disposition, budget_outcome,
    lock_evaluation, freshness, threshold_counterfactual, links;
    shadow_decision carries links.

Wall-clock ``ts`` is used here ONLY for the human-readable envelope
timestamp (design §3.5: event timestamps are wall-clock, for humans and
postmortems). The race itself never touches wall-clock — see race.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

SCHEMA_V = 1

DECIDED_DISPOSITION_TIMER_WIN = "passthrough"


def utcnow_iso() -> str:
    """UTC ISO-8601 with millisecond precision (wall-clock, humans only)."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def empty_lock_evaluation() -> dict:
    """Lock verdicts when no Jev answer was evaluated (structural bars,
    errors, shed). Shape matches the live evaluation."""
    return {
        "prob": {"verdict": "fail",
                 "detail": "no Jev answer evaluated on this path"},
        "conf": {"verdict": "fail",
                 "detail": "no Jev answer evaluated on this path"},
        "allowlist": {"verdict": "fail",
                      "detail": "no Jev answer evaluated on this path"},
    }


def lock_evaluation(prob_pass: bool, prob_detail: str,
                    conf_pass: bool, conf_detail: str,
                    allow_pass: bool, allow_detail: str) -> dict:
    """Per-lock verdicts: prob / conf / allowlist, each pass/fail/stale.

    These are the VALUE legs. The freshness legs (ADR-014, D1) are
    evaluated by the gate kernel from the cached FreshnessReport and
    surface as the ``freshness:<stale legs>`` veto on the disposition
    reason — a stale proof vetoes suppression even when all three value
    legs pass, so a ``pass`` here is never a silent approximation of
    freshness."""
    def verdict(ok: bool) -> str:
        return "pass" if ok else "fail"

    return {
        "prob": {"verdict": verdict(prob_pass), "detail": prob_detail},
        "conf": {"verdict": verdict(conf_pass), "detail": conf_detail},
        "allowlist": {"verdict": verdict(allow_pass), "detail": allow_detail},
    }


def _envelope(event_type: str, alert, episode_id: str | None) -> dict:
    return {
        "type": event_type,
        "actor": "engine",
        "schema_v": SCHEMA_V,
        "ts": utcnow_iso(),
        "event_id": None,   # assigned by the event log
        "seq": None,        # assigned by the event log (authoritative order)
        "alert_id": alert.alert_id,
        "fingerprint": alert.fingerprint,
        "episode_id": episode_id,
        "outbox_id": None,  # dispatcher fills for page dispositions
        "body": {},
    }


def decision_made_payload(*, alert, input_sha256: str,
                          episode_id: str | None = None,
                          disposition: str, budget_outcome: str,
                          budget_ms: int,
                          jev_model: str | None = None,
                          q1_reported: float | None = None,
                          q2_team: str | None = None,
                          q3_confidence: float | None = None,
                          q3_disposition: str | None = None,
                          latency_ms: float | None = None,
                          timer_fired_at_ms: float | None = None,
                          lock_evaluation: dict | None = None,
                          backstop_claimed: bool = False) -> dict:
    """Build the ``decision_made`` payload the gate emits per decision.

    ``latency_ms`` is the Jev call latency — set ONLY for
    ``answered_in_time`` (null otherwise, per the schema).
    ``timer_fired_at_ms`` is the monotonic ms elapsed when the timer won.
    """
    env = _envelope("decision_made", alert, episode_id)
    env["body"] = {
        "input_sha256": input_sha256,
        "fingerprint": alert.fingerprint,
        "episode_id": episode_id,
        "jev_model": jev_model,
        "q1_reported": q1_reported,
        "q2_team": q2_team,
        "q3_confidence": q3_confidence,
        "q3_disposition": q3_disposition,
        "disposition": disposition,
        "budget_outcome": budget_outcome,
        "latency_ms": latency_ms,
        "timer_fired_at_ms": timer_fired_at_ms,
        "budget_ms": budget_ms,
        "backstop_claimed": backstop_claimed,
        "lock_evaluation": (lock_evaluation if lock_evaluation is not None
                            else empty_lock_evaluation()),
        # ADR-014 freshness proofs — owned by the freshness lane; null
        # until that lane's cache exists. Never silently approximated.
        "freshness": {
            "fit_as_of": None,
            "threshold_attested_as_of": None,
            "allowlist_attested_as_of": None,
        },
        # ADR-023 — evaluated deterministically at event-write time by the
        # event-log lane; the gate carries zero hot-path cost here.
        "threshold_counterfactual": None,
        "outbox_id": None,  # dispatcher fills for page dispositions
        "links": {
            # dispatcher fills once decision_requested is sequenced
            "decision_requested_seq": None,
        },
    }
    return env


def shadow_decision_payload(*, alert, input_sha256: str,
                            episode_id: str | None = None,
                            jev_model: str | None = None,
                            q1_reported: float | None = None,
                            q2_team: str | None = None,
                            q3_confidence: float | None = None,
                            q3_disposition: str | None = None,
                            latency_ms: float,
                            budget_ms: int,
                            timer_fired_at_ms: float | None,
                            shadow_disposition: str,
                            would_have_suppressed: bool,
                            lock_evaluation: dict | None = None,
                            error_class: str | None = None) -> dict:
    """Build the ``shadow_decision`` payload for a late Jev answer.

    Canonical schema (design §4.2): the late answer never pages, never
    suppresses — ``decided_disposition`` is always ``"passthrough"``.
    ``latency_ms`` is the FULL call latency — the vendor-tail sample that
    feeds Oracle's latency campaign and the tuner. On a late *error* the
    q-fields are null and ``error_class`` is set (still vendor evidence).
    """
    env = _envelope("shadow_decision", alert, episode_id)
    env["body"] = {
        "jev_model": jev_model,
        "q1_reported": q1_reported,
        "q2_team": q2_team,
        "q3_confidence": q3_confidence,
        "q3_disposition": q3_disposition,
        "latency_ms": latency_ms,
        "budget_ms": budget_ms,
        "timer_fired_at_ms": timer_fired_at_ms,
        "decided_disposition": DECIDED_DISPOSITION_TIMER_WIN,
        "shadow_disposition": shadow_disposition,
        "would_have_suppressed": would_have_suppressed,
        "lock_evaluation": (lock_evaluation if lock_evaluation is not None
                            else empty_lock_evaluation()),
        "error_class": error_class,
        "links": {
            # dispatcher fills with the seq of the timer-win decision_made
            # this event shadows, once seqs are assigned
            "decision_made_seq": None,
        },
    }
    return env
