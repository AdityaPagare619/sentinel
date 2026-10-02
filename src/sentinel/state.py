"""State shaping for the Jev call — frozen contract §3.4 of ARCHITECTURE.md.

build_state() returns the JSON `state` object with a ~850-token hard cap
(token ≈ chars/4). Fields are listed in priority order; when the budget is
exceeded the lowest-priority fields are truncated first (string fields are
shortened in place, non-string fields are dropped), and the top-priority
fields (title/source/check) are never removed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from .models import Alert

STATE_TOKEN_BUDGET = 850

# Top-priority fields are never removed, even when the budget is exceeded.
_PROTECTED_KEYS = {"title", "source", "check"}

_LABEL_KEYS = ("env", "region", "cluster")


def estimate_tokens(text: str) -> int:
    """Token estimate: chars // 4."""
    return len(text) // 4


def _field_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _field_tokens(value) -> int:
    return estimate_tokens(_field_json(value))


def input_sha256(state: dict | str) -> str:
    """Canonical sha256 of the state (for the audit row / mock fingerprints)."""
    if isinstance(state, str):
        canonical = state
    else:
        canonical = _field_json(state)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _time_block(alert: Alert, context: dict) -> dict:
    block = dict(context.get("time") or {})
    block.setdefault("iso", alert.received_at)
    if "dow" not in block:
        try:
            block["dow"] = datetime.fromisoformat(
                alert.received_at.replace("Z", "+00:00")
            ).strftime("%A")
        except (ValueError, TypeError):
            pass
    block.setdefault("holiday", False)
    return block


def _priority_fields(alert: Alert, history: dict, context: dict) -> list[tuple[str, object]]:
    """The 10 priority fields in order (highest value first)."""
    labels = {k: alert.labels[k] for k in _LABEL_KEYS if k in alert.labels}
    metric = {
        "value": alert.metric_value,
        "threshold": alert.metric_threshold,
        "breach_duration_s": alert.breach_duration_s,
    }
    outcome_history = {
        "alerts_30d": history.get("alerts_30d", 0),
        "paged": history.get("paged", 0),
        "became_sev12": history.get("became_sev12", 0),
        "median_auto_clear_min": history.get("median_auto_clear_min"),
    }
    return [
        # 1. title, source, check (~40 tok)
        ("title", alert.title),
        ("source", alert.source),
        ("check", alert.check),
        # 2. service, labels env/region/cluster (~60 tok)
        ("service", alert.service),
        ("labels", labels),
        # 3. metric: value vs threshold, breach duration (~50 tok)
        ("metric", metric),
        # 4. outcome_history: highest-value field (~120 tok)
        ("outcome_history", outcome_history),
        # 5. recent_deploys: last 6h touching this service, top 3 (~80 tok)
        ("recent_deploys", list(context.get("recent_deploys", []))[:3]),
        # 6. sibling_alerts: last 1h on sibling services, top 3 (~80 tok)
        ("sibling_alerts", list(context.get("sibling_alerts", []))[:3]),
        # 7. oncall: team -> current primary, tz (~60 tok)
        ("oncall", context.get("oncall", {})),
        # 8. time: ISO time + dow + holiday flag (~20 tok)
        ("time", _time_block(alert, context)),
        # 9. runbook_title (~30 tok)
        ("runbook_title", context.get("runbook_title", "")),
        # 10. annotations: customer free text (~310 tok)
        ("annotations", str(context.get("annotations", ""))),
    ]


def _whole_state_tokens(fields: list[tuple[str, object]]) -> int:
    """Token estimate of the fully serialized state dict.

    This is the measure the budget is enforced against — it includes key
    names and JSON structure overhead, not just the values.
    """
    return estimate_tokens(_field_json(dict(fields)))


def _truncate_field_to_fit(
    fields: list[tuple[str, object]], idx: int, budget: int
) -> str | None:
    """Largest leading prefix of fields[idx]'s value fitting the budget.

    Binary-searches against the whole-state token measure, so the result is
    exact by construction. Returns None when not even the "…" marker fits
    (the field must be dropped instead).
    """
    key, value = fields[idx]
    lo, hi = 0, len(value)
    best: str | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = value[:mid].rstrip()
        if mid < len(value):
            candidate += "\u2026"  # mark the truncation explicitly
        trial = list(fields)
        trial[idx] = (key, candidate)
        if _whole_state_tokens(trial) <= budget:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        marker_trial = list(fields)
        marker_trial[idx] = (key, "\u2026")
        if _whole_state_tokens(marker_trial) <= budget:
            return "\u2026"
    return best


def _total_tokens(fields: list[tuple[str, object]]) -> int:
    return _whole_state_tokens(fields)


def build_state(alert: Alert, history: dict | None = None,
                context: dict | None = None) -> dict:
    """Build the Jev `state` dict within the ~850-token hard cap.

    Budget policy (§3.4): walk fields lowest-priority-first. The lowest
    non-empty string field is truncated in place when that alone absorbs
    the overage; otherwise the field is dropped and the walk continues up.
    `title`/`source`/`check` are never dropped; they are truncated only as
    a last resort to hold the hard cap.
    """
    history = dict(history or {})
    context = dict(context or {})

    fields = _priority_fields(alert, history, context)

    i = len(fields) - 1
    while _whole_state_tokens(fields) > STATE_TOKEN_BUDGET and i >= 0:
        key, value = fields[i]
        if key in _PROTECTED_KEYS:
            i -= 1
            continue
        if isinstance(value, str) and value:
            truncated = _truncate_field_to_fit(fields, i, STATE_TOKEN_BUDGET)
            if truncated is not None:
                fields[i] = (key, truncated)
                break
        # Truncation can't absorb it (or it's not a string): drop the field.
        del fields[i]
        i -= 1

    # Last resort: only protected fields remain and we're still over.
    # No single field can absorb it (the others are still huge), so shrink
    # the protected string fields iteratively — halving one at a time and
    # re-measuring — until the hard cap holds. They are truncated, never
    # dropped.
    while _whole_state_tokens(fields) > STATE_TOKEN_BUDGET:
        shrunk = False
        for j, (k, value) in enumerate(fields):
            if k in _PROTECTED_KEYS and isinstance(value, str) and len(value) > 1:
                raw = value[:-1] if value.endswith("\u2026") else value
                fields[j] = (k, raw[: max(len(raw) // 2, 0)].rstrip() + "\u2026")
                shrunk = True
                break  # re-measure after each shrink
        if not shrunk:
            break  # irreducible (all markers); best effort reached

    return {key: value for key, value in fields}
