"""D5 — policy_impact_preview: offline replay of archived decision inputs.

"This rule change would have suppressed X of last week's pages."

Two stages (§3 D5):
  1. Deterministic replay (no Jev): archived inputs are re-evaluated
     through ``Gate._resolve_suppress_path`` — the same one-kernel DR-26
     composition the live decision and the D9 receipt use (reuse, not a
     mirror) — with the proposed policy applied. Jev cannot do arithmetic;
     the counting is code.
  2. Advisory triage (Jev, typed): Choice riskiest_delta over the changed
     cases (code proposes; deterministic risk proxy pre-ranks; top-25 go to
     Jev). Output: top-5 flagged for human review.

CONTROL — read-only over archives, never touches live state:
  * The ONLY gate import is ``Gate`` (for ``_resolve_suppress_path``) —
    enforced by tests/test_advisory.py::test_d5_narrow_gate_import.
  * The replay runs on an OFFLINE kernel: an uninitialized Gate
    (``object.__new__(Gate)``) whose every attribute the composition reads
    (thresholds, allowlist, policy_gate=None, corroboration inputs) is set
    explicitly from the PROPOSED policy + the archived record. No live
    policy store, no forwarder, no audit writer, no event-log writer is
    imported or touched — the module has no write-capable imports at all.
  * Corroboration replays the RECORDED evidence triple when the archive
    carries it; when it doesn't (the live leg never ran), the leg fails
    closed by its own contract (page_now / "uncorroborated") — reported
    separately, never silently counted as suppressed.
  * Archive window: default 7 days, VERIFIED against the retention config
    (RetentionConfig.hot_floor_days) — never silently extended.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .advisory import d5_triage_questions, label_envelope
from .counterfactual import CounterfactualInputs
from .models import Alert, Thresholds
from .retention import RetentionConfig

# The narrow gate import (approval condition 1): Gate is imported ONLY so
# the replay can reuse Gate._resolve_suppress_path. No other gate member
# is touched — the AST test enforces this.
from sentinel.gate import Gate

log = logging.getLogger("sentinel.advisory_d5")

DEFAULT_ARCHIVE_WINDOW_DAYS = 7
TRIAGE_TOP_N = 25
TRIAGE_FLAG_N = 5
RATE_LIMIT_PER_DRAFT_S = 3600.0  # 1 preview per policy draft per hour


class ArchiveWindowError(ValueError):
    """Requested archive window exceeds the retention floor."""


class RateLimitedError(RuntimeError):
    """A preview was refused by the per-draft rate limiter."""


def verify_archive_window(window_days: int) -> int:
    """Approval condition 4: the default 7-day window holds ONLY if
    retention covers it — verified against the live retention config.
    Shrink the window when it doesn't; never silently extend retention."""
    floor = RetentionConfig().hot_floor_days
    if window_days > floor:
        raise ArchiveWindowError(
            f"archive window {window_days}d exceeds retention hot floor "
            f"{floor}d — shrink the window, never extend retention")
    return window_days


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass
class ProposedPolicy:
    """An operator-proposed rule change, as data (never applied live)."""
    name: str = "proposed"
    thresholds_override: dict = field(default_factory=dict)
    allowlist_add: list[str] = field(default_factory=list)
    allowlist_remove: list[str] = field(default_factory=list)
    suppress_conf_min_override: float | None = None


@dataclass
class ArchiveDecisionInput:
    """One archived decision input: the alert + the live decision's
    recorded inputs (CounterfactualInputs shape, the D9 precedent) +
    what the live decision actually did."""
    alert: Alert
    inputs: CounterfactualInputs
    recorded_action: str
    recorded_reason: str = ""
    ts: str = ""  # ISO-8601 archive timestamp


# ---------------------------------------------------------------------------
# Offline kernel — the DR-26 composition, none of the live machinery
# ---------------------------------------------------------------------------

def build_offline_kernel(proposed: ProposedPolicy,
                         base_thresholds: Thresholds | None = None,
                         base_allowlist: set[str] | None = None,
                         clock=None) -> Gate:
    """Build the offline replay kernel.

    An uninitialized Gate whose ONLY exercised method is
    ``_resolve_suppress_path``. Every attribute that method (and the D8 /
    D5 legs it composes) reads is set explicitly from the proposed policy:
    thresholds (+ overrides), the proposed allowlist, policy_gate=None
    (offline: the D8 hook cannot consult a live policy state, so the
    replay evaluates the kernel purely), no corroborator (recorded
    evidence only), no attestor registry. No RaceRunner, no watchdog, no
    audit, no emit function — none of the live machinery exists here.
    """
    base = base_thresholds or Thresholds()
    merged = {**dataclasses.asdict(base), **proposed.thresholds_override}
    # Drop non-init fields defensively (Thresholds may grow).
    import inspect as _inspect
    params = set(_inspect.signature(Thresholds).parameters)
    thresholds = Thresholds(**{k: v for k, v in merged.items()
                               if k in params})
    allowlist = ((set(base_allowlist or ())) | set(proposed.allowlist_add)
                 ) - set(proposed.allowlist_remove)

    kernel = object.__new__(Gate)  # offline: no __init__, no live machinery
    kernel.thresholds = thresholds
    kernel.allowlist = set(allowlist)
    kernel.allowlist_entries = {fp: None for fp in allowlist}
    kernel.policy_gate = None
    kernel.policy_id = "suppression"
    kernel.silence_floor = None
    kernel.corroborator = None
    kernel.attestor_registry = None
    kernel.clock = clock
    kernel._legacy_map = {}
    kernel._legacy_window_ends_at = None
    return kernel


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

def _parse_ts(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def filter_archive_window(archive: list[ArchiveDecisionInput],
                          window_days: int,
                          now: datetime | None = None
                          ) -> tuple[list[ArchiveDecisionInput], int]:
    """Keep inputs inside the window. Inputs with missing/unparseable
    timestamps are EXCLUDED and counted — never silently included."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    kept, excluded = [], 0
    for item in archive:
        ts = _parse_ts(item.ts)
        if ts is not None and ts >= cutoff:
            kept.append(item)
        else:
            excluded += 1
    return kept, excluded


def replay_one(kernel: Gate, archived: ArchiveDecisionInput,
               proposed: ProposedPolicy) -> dict:
    """Re-run one archived input through the proposed policy. Never raises:
    malformed recorded answers become ``unresolvable`` (the D9 receipt's
    honesty discipline), never a crash, never a live write."""
    inp = archived.inputs
    triple = None
    if inp.corro_floor is not None and inp.corro_now is not None:
        triple = (inp.corro_floor, inp.corro_evidences or [], inp.corro_now)
    try:
        action, reason, verdict, corro, _used = kernel._resolve_suppress_path(
            archived.alert,
            jev_model=inp.jev_model,
            q_sev=inp.q_sev, q_team=inp.q_team, q_disp=inp.q_disp,
            latency_ms=inp.latency_ms,
            lock1_dual_attested=inp.lock1_dual_attested,
            freshness_report=inp.freshness_report,
            prob_lock_pass=inp.prob_lock_pass,
            via_legacy=inp.via_legacy,
            suppress_conf_min_override=proposed.suppress_conf_min_override,
            corro_inputs=triple)
    except Exception as exc:  # noqa: BLE001 — replay never raises
        return {
            "alert_id": archived.alert.alert_id,
            "fingerprint": archived.alert.fingerprint,
            "recorded_action": archived.recorded_action,
            "recorded_reason": archived.recorded_reason,
            "kernel_action": None, "replay_action": None,
            "replay_reason": None, "corroboration": "unresolvable",
            "unresolvable": f"{type(exc).__name__}: {exc}",
        }
    kernel_action = verdict.action
    if kernel_action != "suppress":
        corro_status = "not_run"
    elif triple is None:
        # No recorded evidence: the leg cannot corroborate offline. The
        # composition already failed it closed (page_now/uncorroborated);
        # report the gap honestly instead of counting a suppression.
        corro_status = "unresolvable"
    elif corro is not None and corro.passed:
        corro_status = "passed"
    else:
        corro_status = "failed"
    return {
        "alert_id": archived.alert.alert_id,
        "fingerprint": archived.alert.fingerprint,
        "recorded_action": archived.recorded_action,
        "recorded_reason": archived.recorded_reason,
        "kernel_action": kernel_action,
        "replay_action": action,
        "replay_reason": reason,
        "corroboration": corro_status,
        "unresolvable": None,
    }


def replay_archive(kernel: Gate, archive: list[ArchiveDecisionInput],
                   proposed: ProposedPolicy) -> list[dict]:
    return [replay_one(kernel, item, proposed) for item in archive]


def build_impact_report(results: list[dict], *, window_days: int,
                        policy_name: str,
                        out_of_window: int = 0) -> dict:
    """Deterministic delta table. The headline counts EFFECTIVE
    suppressions (kernel suppress AND corroborated by recorded evidence);
    the kernel-only count and the uncorroborated gap are shown alongside —
    Jev cannot do arithmetic, so every number here is code."""
    evaluated = [r for r in results if r["unresolvable"] is None]
    unresolvable = len(results) - len(evaluated)
    recorded_pages = [r for r in evaluated
                      if r["recorded_action"] == "page_now"]
    kernel_newly_suppress = [r for r in evaluated
                             if r["recorded_action"] != "suppress"
                             and r["kernel_action"] == "suppress"]
    effective_newly_suppressed = [r for r in evaluated
                                  if r["recorded_action"] != "suppress"
                                  and r["replay_action"] == "suppress"]
    newly_uncorroborated = [r for r in kernel_newly_suppress
                            if r["replay_action"] != "suppress"]
    newly_paged = [r for r in evaluated
                   if r["recorded_action"] == "suppress"
                   and r["replay_action"] != "suppress"]
    by_reason: dict[str, int] = {}
    for r in evaluated:
        if r["recorded_action"] != r["replay_action"]:
            key = f"{r['recorded_action']}->{r['replay_action']}"
            by_reason[key] = by_reason.get(key, 0) + 1
    return {
        "policy": policy_name,
        "window_days": window_days,
        "inputs_evaluated": len(evaluated),
        "inputs_unresolvable": unresolvable,
        "inputs_out_of_window": out_of_window,
        "recorded_pages": len(recorded_pages),
        # Headline: "would have suppressed X of Y pages (Δ)".
        "would_suppress_of_pages": len(effective_newly_suppressed),
        "delta_suppressed": (len(effective_newly_suppressed)
                             - len(newly_paged)),
        # Honesty companions: kernel-only count + the corroboration gap.
        "kernel_newly_suppress": len(kernel_newly_suppress),
        "newly_uncorroborated": len(newly_uncorroborated),
        "newly_paged": len(newly_paged),
        "by_transition": by_reason,
        "changed_cases": [
            {"alert_id": r["alert_id"], "fingerprint": r["fingerprint"],
             "recorded_action": r["recorded_action"],
             "replay_action": r["replay_action"],
             "replay_reason": r["replay_reason"],
             "corroboration": r["corroboration"]}
            for r in evaluated
            if r["recorded_action"] != r["replay_action"]
        ],
    }


# ---------------------------------------------------------------------------
# Advisory triage (Jev, typed) — "code proposes, Jev disposes"
# ---------------------------------------------------------------------------

_SEV_RANK = {"critical": 4, "emergency": 4, "fatal": 4, "p1": 4,
             "high": 3, "p2": 3, "medium": 2, "warning": 2, "p3": 2,
             "low": 1, "info": 1, "p4": 1}


def risk_proxy(case: dict) -> tuple:
    """Deterministic risk proxy: severity x change-direction weight.

    page_now -> suppress is the risky direction (a page that would go
    silent); suppress -> page is fail-safe and ranks lower.
    """
    sev = _SEV_RANK.get(str(case.get("severity_in", "")).lower(), 0)
    weight = (2 if case.get("recorded_action") == "page_now"
              and case.get("replay_action") == "suppress" else 1)
    return (sev * weight, sev)


def triage_with_jev(dispatcher, changed_cases: list[dict],
                    *, top_n: int = TRIAGE_TOP_N,
                    flag_n: int = TRIAGE_FLAG_N) -> tuple[list[dict], str]:
    """Rank the changed cases for human review.

    Returns (flagged, fallback_label): ``flagged`` is the top-N case dicts
    (each with ``review_priority``), ``fallback_label`` is "jev" when Jev
    ranked them, "deterministic" when the proxy ranking was used.
    """
    if not changed_cases:
        return [], "deterministic"
    ranked = sorted(changed_cases,
                    key=lambda c: (risk_proxy(c),
                                   str(c.get("alert_id", ""))),
                    reverse=True)
    candidates = ranked[:top_n]
    for i, c in enumerate(candidates):
        c["case_id"] = c.get("case_id") or f"case-{i}"

    questions = d5_triage_questions(candidates)
    state = {"changed_cases": [
        {k: c.get(k) for k in ("case_id", "alert_id", "fingerprint",
                               "severity_in", "recorded_action",
                               "replay_action", "replay_reason",
                               "corroboration")}
        for c in candidates]}

    def on_jev(resp):
        answers = resp.answers or {}
        ans = answers.get("riskiest_delta")
        probs = dict(getattr(ans, "probabilities", None) or {})
        order = sorted(candidates,
                       key=lambda c: probs.get(c["case_id"], 0.0),
                       reverse=True)
        # Jev's pick leads even if probabilities are thin.
        pick = getattr(ans, "choice", None)
        if pick and pick != "cannot_determine":
            order = sorted(order,
                           key=lambda c: 0 if c["case_id"] == pick else 1)
        return {"order": [c["case_id"] for c in order]}

    def on_fallback(_reason):
        return {"order": [c["case_id"] for c in candidates]}

    env = dispatcher.call_inline(
        "policy_impact_preview", state, questions, on_jev, on_fallback)
    order = (env.get("body") or {}).get("order") or []
    by_id = {c["case_id"]: c for c in candidates}
    flagged = [dict(by_id[cid], review_priority=i + 1)
               for i, cid in enumerate(order[:flag_n]) if cid in by_id]
    return flagged, env.get("fallback", "deterministic")


# ---------------------------------------------------------------------------
# Rate limiting (operator-initiated only)
# ---------------------------------------------------------------------------

def check_rate_limit(ledger: dict, draft_id: str,
                     now_s: float | None = None) -> tuple[bool, float]:
    """1 preview per policy draft per hour. Returns (ok, wait_s)."""
    now_s = now_s if now_s is not None else time.time()
    last = ledger.get(draft_id)
    if last is None:
        # Never previewed: allowed.
        ledger[draft_id] = now_s
        return True, 0.0
    wait = (float(last) + RATE_LIMIT_PER_DRAFT_S) - now_s
    if wait > 0:
        return False, wait
    ledger[draft_id] = now_s
    return True, 0.0


# ---------------------------------------------------------------------------
# Full preview
# ---------------------------------------------------------------------------

def d5_preview(*, archive: list[ArchiveDecisionInput],
               proposed: ProposedPolicy,
               dispatcher=None,
               window_days: int = DEFAULT_ARCHIVE_WINDOW_DAYS,
               base_thresholds: Thresholds | None = None,
               base_allowlist: set[str] | None = None,
               with_triage: bool = True,
               rate_ledger: dict | None = None,
               draft_id: str = "default",
               clock=None,
               now: datetime | None = None) -> dict:
    """Run the full D5 preview. Returns the labeled report envelope.

    Read-only: ``archive`` is never mutated; the kernel is offline; the
    only network touch is the optional Jev triage via ``dispatcher``.
    """
    verify_archive_window(window_days)
    if rate_ledger is not None:
        ok, wait_s = check_rate_limit(rate_ledger, draft_id)
        if not ok:
            raise RateLimitedError(
                f"rate-limited: draft {draft_id!r} previewed recently; "
                f"retry in {wait_s:.0f}s")
    in_window, excluded = filter_archive_window(archive, window_days,
                                                now=now)
    kernel = build_offline_kernel(proposed, base_thresholds,
                                  base_allowlist, clock=clock)
    results = replay_archive(kernel, in_window, proposed)
    report = build_impact_report(results, window_days=window_days,
                                 policy_name=proposed.name,
                                 out_of_window=excluded)
    triage_label = "deterministic"
    if with_triage and dispatcher is not None:
        flagged, triage_label = triage_with_jev(
            dispatcher, report["changed_cases"])
        report["review_list"] = flagged
        report["review_ranked_by"] = triage_label
    else:
        report["review_list"] = []
        report["review_ranked_by"] = "deterministic"
    return label_envelope(
        direction="policy_impact_preview",
        fallback="jev" if triage_label == "jev" else "deterministic",
        jev_model=dispatcher.jev_model if dispatcher is not None else None,
        body=report,
        fallback_reason=None if triage_label == "jev"
        else "triage_unavailable_deterministic_table_only")
