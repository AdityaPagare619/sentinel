"""D3 — evidence_bundler retrieval helpers: deterministic, read-only, no Jev.

Retrieval runs BEFORE any Jev call ("code proposes, Jev disposes"): related
past incidents from the event log (read-only) + matching runbook snippets
from the operator-configured runbook index. Candidates are capped BEFORE
serialization (<=12 incidents x ~150 tokens, <=8 runbooks x ~200 tokens,
hard state cap 8K tokens, truncate by recency).

Read-only contract (enforced by tests):
  * The event-log access is an injected ``read_log`` callable — this module
    never imports the event-log WRITER (or any writer). The callable is the
    test seam and the Track 3 boundary.
  * The runbook index is a plain operator-configured data structure
    (list of dicts); an empty index => incidents-only bundle, labeled.
  * No imports from sentinel.gate, no network, no clocks.
"""

from __future__ import annotations

from .advisory import estimate_input_tokens

MAX_INCIDENTS = 12
MAX_RUNBOOKS = 8
STATE_TOKEN_CAP = 8000


def retrieve_related_incidents(read_log, *, fingerprint: str,
                               service: str | None = None,
                               check: str | None = None,
                               limit: int = MAX_INCIDENTS) -> list[dict]:
    """Read-only lookup of related past incidents.

    ``read_log``: callable ``(fingerprint=..., service=..., check=...,
    limit=...) -> list[dict]`` — the read side of the event log only.
    Each incident dict carries at least ``incident_id`` + ``summary``;
    recency order is preserved (newest first).
    """
    try:
        rows = read_log(fingerprint=fingerprint, service=service,
                        check=check, limit=limit) or []
    except Exception:
        return []
    incidents = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        incidents.append({
            "incident_id": str(row.get("incident_id") or row.get("alert_id")
                               or "unknown"),
            "summary": str(row.get("summary") or row.get("title") or "")[:300],
            "ts": row.get("ts"),
            "disposition": row.get("disposition"),
        })
    return incidents


def match_runbooks(runbook_index: list[dict] | None,
                   check_name: str | None,
                   limit: int = MAX_RUNBOOKS) -> list[dict]:
    """Keyword match over the operator-configured runbook index.

    Empty/missing index => [] (the bundle is incidents-only, labeled by the
    caller). Matching is a case-insensitive substring over the runbook's
    keywords + title + check names — deterministic, no ranking model.
    """
    if not runbook_index or not check_name:
        return []
    needle = str(check_name).lower()
    matched = []
    for rb in runbook_index:
        if not isinstance(rb, dict):
            continue
        haystack = " ".join([
            str(rb.get("title", "")),
            str(rb.get("check", "")),
            " ".join(str(k) for k in (rb.get("keywords") or [])),
        ]).lower()
        if needle and needle in haystack:
            matched.append({
                "runbook_id": str(rb.get("runbook_id") or rb.get("id")
                                  or "unknown"),
                "title": str(rb.get("title", ""))[:200],
                "snippet": str(rb.get("snippet", ""))[:400],
            })
        if len(matched) >= limit:
            break
    return matched


def cap_state_tokens(state: dict, max_tokens: int = STATE_TOKEN_CAP) -> dict:
    """Enforce the hard state cap BEFORE serialization.

    Truncates by recency: drops the oldest incidents first, then the oldest
    runbooks, until the estimate fits. Returns the (possibly trimmed) state
    plus a ``truncated`` flag so the bundle can say so honestly.
    """
    state = dict(state)
    truncated = False
    while estimate_input_tokens(state, {}) > max_tokens:
        incidents = state.get("incidents") or []
        runbooks = state.get("runbooks") or []
        if len(incidents) > 1:
            state["incidents"] = incidents[:-1]
            truncated = True
        elif len(runbooks) > 1:
            state["runbooks"] = runbooks[:-1]
            truncated = True
        else:
            break
    state["truncated"] = truncated
    return state
