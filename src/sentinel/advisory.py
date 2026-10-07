"""T5 — Jev advisory directions: dispatcher, pool, pre-call gates, D1-D6.

THE IRON RULE (design doc docs/planning/JEV-DIRECTIONS.md, approved):
  Jev advises and enriches; it never decides, never pages, never suppresses.

Structural guarantees this module provides:
  * Every direction executes AFTER the disposition is final, consuming
    already-emitted payloads (read-only). There is no code path from here
    to the gate kernel before or during a decision.
  * Outputs are enrichment envelopes on the gate's emission channel
    (``advisory/*`` event types, the storm_root_cause_payload precedent).
    The gate kernel's read-set is unchanged — see tests/test_advisory.py::
    test_advisory_types_invisible_to_gate_read_set.
  * NO imports from the gate's decision path. This module imports nothing
    from sentinel.gate (the D5 offline replay lives in advisory_d5.py,
    which narrowly reuses Gate._resolve_suppress_path per approval).
  * No writes to the paging path, ever: no policy store, no forwarder, no
    audit decision fields, no operator token. The only network access is
    the injected ``decide_fn`` (Track 2's Jev entry; Track 5 never sees key
    material).
  * The AdvisoryPool is ISOLATED from the race's InferencePool — a storm
    of advisory work cannot delay a page by one scheduling quantum.
  * Never raises: every public entry point catches all exceptions. A
    failure drops an enrichment, never a page, never a suppression.

Labeling contract (§2.3, for Track 8): every envelope carries
``advisory/ai_generated/direction/fallback/jev_model/direction_version``.
``fallback="jev"`` means Jev answered; ``"deterministic"`` means the
template path; ``"absent"`` means honest absence. ``ai_generated`` is True
only when Jev actually contributed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

from .questions import build_questions
from .advisory_render import (assemble_evidence_bundle,
                              render_hypothesis,
                              render_storm_brief,
                              render_suppression_paragraph,
                              render_triage_suggestion)

log = logging.getLogger("sentinel.advisory")

# Price-list fact. Source of truth: JEV_USD_PER_M_INPUT in src/sentinel/ab.py
# (kept as a local literal so this module's import graph stays free of the
# gate decision flow that ab.py pulls in; the value is a config literal,
# not logic — there is nothing to drift).
JEV_USD_PER_M_INPUT = 0.042

DIRECTION_VERSION = 1

# ---------------------------------------------------------------------------
# Event types & fallback labels
# ---------------------------------------------------------------------------

ADVISORY_EVENT_TYPES = frozenset({
    "advisory/suppression_explainer",   # D1
    "advisory/storm_brief",             # D2 (merged storm call)
    "advisory/evidence_bundle",         # D3 (merged storm call / standalone)
    "advisory/rca_hypotheses",          # D4 (merged storm call / standalone)
    "advisory/policy_impact_preview",   # D5
    "advisory/triage_suggest",          # D6
})

# §5 timeout–fallback matrix: the honest label when Jev is unavailable.
FALLBACK_LABEL: dict[str, str] = {
    "suppression_explainer": "deterministic",
    "storm_brief": "deterministic",
    "evidence_bundle": "deterministic",
    "rca_hypotheses": "absent",
    "policy_impact_preview": "deterministic",
    "triage_suggest": "absent",
}

# ---------------------------------------------------------------------------
# Spend meter (Track 2 boundary — duck-type protocol)
# ---------------------------------------------------------------------------

@dataclass
class SpendMeter:
    """Track 2's spend surface: {session_usd, budget_usd, calls, blocked}.

    ROLE CONTRACT (RFC aiml-spend-reconciliation): this is the advisory
    PRE-AUTHORIZATION ENVELOPE, not the spend ledger. ``charge`` records the
    estimated pre-call cost (conservative: daily caps are enforced on the
    estimate, never on a post-hoc actual). The wire-truth ledger is
    ``sim_judge.JevSpendTracker`` (actual cost from response usage blocks;
    failed calls cost 0.0). For the same call stream this envelope's
    session_usd is >= the tracker's (estimates are conservative; failed calls
    are charged the estimate here but 0.0 there) — the divergence is bounded
    and one-directional, never an understatement. When a tracker is attached
    to the dispatcher, its ``blocked`` is consulted too (fail-closed on
    disagreement); the served spend surface (GET /api/v1/jev/spend) is the
    tracker only.
    """
    session_usd: float = 0.0
    budget_usd: float = 0.50
    calls: int = 0
    blocked: bool = False

    def charge(self, cost_usd: float) -> None:
        self.session_usd += max(0.0, cost_usd)
        self.calls += 1
        if self.session_usd >= self.budget_usd:
            self.blocked = True

    @classmethod
    def from_dict(cls, d: dict) -> "SpendMeter":
        return cls(session_usd=float(d.get("session_usd", 0.0)),
                   budget_usd=float(d.get("budget_usd", 0.50)),
                   calls=int(d.get("calls", 0)),
                   blocked=bool(d.get("blocked", False)))


@dataclass
class DirectionBudget:
    """Per-direction budget discipline (§4)."""
    enabled: bool = True
    timeout_ms: float = 8000.0
    cost_cap_usd: float = 0.0005
    daily_cap_usd: float | None = None


# §4 cost engineering table. storm_merged carries D2+D3+D4 in one call:
# timeout = max(D4 10s), daily cap = D3 0.15 + D4 0.15.
DEFAULT_DIRECTION_BUDGETS: dict[str, DirectionBudget] = {
    "suppression_explainer": DirectionBudget(
        enabled=True, timeout_ms=8000, cost_cap_usd=0.0005, daily_cap_usd=0.10),
    "storm_merged": DirectionBudget(
        enabled=True, timeout_ms=10000, cost_cap_usd=0.0005, daily_cap_usd=0.30),
    "evidence_bundler": DirectionBudget(
        enabled=True, timeout_ms=8000, cost_cap_usd=0.0005, daily_cap_usd=0.15),
    "rca_hypotheses": DirectionBudget(
        enabled=True, timeout_ms=10000, cost_cap_usd=0.0005, daily_cap_usd=0.15),
    "policy_impact_preview": DirectionBudget(
        enabled=True, timeout_ms=30000, cost_cap_usd=0.003, daily_cap_usd=0.02),
    "triage_suggest": DirectionBudget(
        enabled=True, timeout_ms=12000, cost_cap_usd=0.0005, daily_cap_usd=0.05),
}


# ---------------------------------------------------------------------------
# Token / cost estimation (deterministic, no Jev)
# ---------------------------------------------------------------------------

def estimate_input_tokens(state, questions) -> int:
    """Deterministic input-token estimate: bytes/4 over canonical JSON."""
    try:
        blob = json.dumps({"state": state, "questions": questions},
                          ensure_ascii=True, default=str, sort_keys=True)
    except Exception:
        blob = str(state) + str(questions)
    return max(1, len(blob) // 4)


def estimate_cost_usd(state, questions) -> float:
    return estimate_input_tokens(state, questions) / 1_000_000 * JEV_USD_PER_M_INPUT


# ---------------------------------------------------------------------------
# AdvisoryPool — isolated from the race's InferencePool
# ---------------------------------------------------------------------------

class AdvisoryPool:
    """Bounded pool for advisory Jev calls: 4 threads, queue 32, shed-first.

    A SEPARATE pool object from race.InferencePool by construction — advisory
    work can never borrow a race scheduling quantum. Queue-full => submit
    returns None => the caller takes the deterministic fallback (shed drops
    an enrichment, never a page).
    """

    def __init__(self, max_workers: int = 4, queue_size: int = 32) -> None:
        self._permits = threading.Semaphore(queue_size)
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="sentinel-advisory")
        self._lock = threading.Lock()
        self._shutdown = False

    def submit(self, fn):
        """Submit ``fn()``. Returns a Future, or None when shedding."""
        with self._lock:
            if self._shutdown:
                return None
        if not self._permits.acquire(blocking=False):
            return None
        try:
            return self._pool.submit(self._run_guarded, fn)
        except Exception:
            self._permits.release()
            raise

    def _run_guarded(self, fn):
        try:
            return fn()
        finally:
            self._permits.release()

    def close(self) -> None:
        with self._lock:
            self._shutdown = True
        self._pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# Labeling (§2.3)
# ---------------------------------------------------------------------------

def label_envelope(*, direction: str, fallback: str,
                   jev_model: str | None, body: dict,
                   fallback_reason: str | None = None,
                   alert_id: str | None = None,
                   fingerprint: str | None = None,
                   episode_id: str | None = None) -> dict:
    """Build the §2.3 enrichment envelope.

    ``fallback``: "jev" | "deterministic" | "absent". ``ai_generated`` is
    True ONLY when Jev actually answered (fallback == "jev").
    """
    if fallback not in ("jev", "deterministic", "absent"):
        fallback = "deterministic"
    return {
        "type": f"advisory/{direction}",
        "advisory": True,
        "ai_generated": fallback == "jev",
        "direction": direction,
        "fallback": fallback,
        "fallback_reason": fallback_reason,
        "jev_model": jev_model,
        "direction_version": DIRECTION_VERSION,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "alert_id": alert_id,
        "fingerprint": fingerprint,
        "episode_id": episode_id,
        "body": dict(body or {}),
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class AdvisoryDispatcher:
    """One submission path for all six advisory directions.

    ``decide_fn``: Track 2's Jev call entry — ``(state, questions) ->
    DecisionResponse``. Injected; never the raw key. ``spend_meter``:
    SpendMeter (or the C2 dict shape, auto-wrapped). ``budgets``:
    per-direction DirectionBudget overrides. ``tracker``: optional
    ``sim_judge.JevSpendTracker`` (the wire-truth ledger). When attached,
    the pre-call gates consult it too — a blocked tracker blocks advisory
    even if the estimate envelope hasn't latched (fail-closed on
    disagreement; RFC aiml-spend-reconciliation).
    """

    def __init__(self, decide_fn, spend_meter=None, budgets=None,
                 jev_model: str | None = None,
                 pool: AdvisoryPool | None = None,
                 tracker=None) -> None:
        self._decide_fn = decide_fn
        if isinstance(spend_meter, dict):
            spend_meter = SpendMeter.from_dict(spend_meter)
        self._meter: SpendMeter = spend_meter or SpendMeter()
        self._tracker = tracker  # JevSpendTracker | None — duck-typed
        merged = {k: DirectionBudget(**vars(v))
                  for k, v in DEFAULT_DIRECTION_BUDGETS.items()}
        for k, v in (budgets or {}).items():
            merged[k] = v if isinstance(v, DirectionBudget) else DirectionBudget(**v)
        self._budgets = merged
        self._jev_model = jev_model
        self._pool = pool or AdvisoryPool()
        self._spend_lock = threading.Lock()
        self._daily: dict[str, tuple[date, float]] = {}  # direction -> (day, usd)

    # -- properties (Track 7 test seam) -----------------------------------
    @property
    def meter(self) -> SpendMeter:
        return self._meter

    @property
    def pool(self) -> AdvisoryPool:
        return self._pool

    @property
    def jev_model(self) -> str | None:
        return self._jev_model

    # -- pre-call gates ----------------------------------------------------
    def _pre_call_gates(self, direction: str, state,
                        questions: dict) -> tuple[bool, str | None, float]:
        """All gates must pass: enabled -> not blocked -> daily cap ->
        cost cap. Returns (ok, reason, estimated_cost_usd)."""
        budget = self._budgets.get(direction)
        if budget is None or not budget.enabled:
            return False, "disabled", 0.0
        if self._meter.blocked or self._meter.session_usd >= self._meter.budget_usd:
            return False, "budget_blocked", 0.0
        if self._tracker is not None and self._tracker.blocked:
            # Fail-closed on disagreement: the wire-truth ledger says the
            # budget is spent, even if the estimate envelope hasn't latched.
            # (RFC aiml-spend-reconciliation.) The property read takes no
            # dispatcher locks — no lock-ordering risk.
            return False, "budget_blocked", 0.0
        est = estimate_cost_usd(state, questions)
        if est > budget.cost_cap_usd:
            return False, "cost_cap", est
        if budget.daily_cap_usd is not None:
            today = date.today()
            last_day, spent = self._daily.get(direction, (today, 0.0))
            if last_day != today:
                spent = 0.0
            if spent + est > budget.daily_cap_usd:
                return False, "daily_cap", est
        return True, None, est

    def _charge(self, direction: str, est: float) -> None:
        with self._spend_lock:
            today = date.today()
            last_day, spent = self._daily.get(direction, (today, 0.0))
            if last_day != today:
                spent = 0.0
            self._daily[direction] = (today, spent + est)
            self._meter.charge(est)

    # -- envelope helpers ---------------------------------------------------
    def _fallback_part(self, envelope_direction: str, reason: str,
                       on_fallback, alert_id, fingerprint,
                       episode_id) -> dict:
        label = FALLBACK_LABEL.get(envelope_direction, "deterministic")
        try:
            body = on_fallback(reason) or {}
        except Exception as exc:  # the fallback itself must never raise
            log.warning("[sentinel] advisory fallback builder raised: %r", exc)
            body = {"fallback_builder_failed": True}
        return label_envelope(direction=envelope_direction, fallback=label,
                              jev_model=self._jev_model, body=body,
                              fallback_reason=reason, alert_id=alert_id,
                              fingerprint=fingerprint, episode_id=episode_id)

    def _jev_part(self, envelope_direction: str, resp, on_jev, on_fallback,
                  alert_id, fingerprint, episode_id) -> dict:
        try:
            body = on_jev(resp) or {}
        except Exception as exc:
            log.warning("[sentinel] advisory on_jev raised: %r "
                        "(falling back)", exc)
            return self._fallback_part(envelope_direction, "answer_parse_error",
                                       on_fallback, alert_id, fingerprint,
                                       episode_id)
        return label_envelope(direction=envelope_direction, fallback="jev",
                              jev_model=(getattr(resp, "model", None)
                                         or self._jev_model),
                              body=body, alert_id=alert_id,
                              fingerprint=fingerprint, episode_id=episode_id)

    # -- public: blocking multi-part call (ONE decide call, N envelopes) ----
    def call_multi(self, direction: str, state, questions: dict,
                   parts: list[tuple], *, timeout_ms: float | None = None,
                   alert_id: str | None = None,
                   fingerprint: str | None = None,
                   episode_id: str | None = None) -> list[dict]:
        """Run ONE Jev call; build one envelope per part.

        ``parts``: list of ``(envelope_direction, on_jev, on_fallback)``.
        Never raises. On any failure every part gets its direction's
        deterministic/absent fallback, honestly labeled.
        """
        budget = self._budgets.get(direction) or DirectionBudget()
        try:
            ok, reason, est = self._pre_call_gates(direction, state, questions)
            if not ok:
                return [self._fallback_part(d, reason, fb, alert_id,
                                            fingerprint, episode_id)
                        for d, _oj, fb in parts]
            self._charge(direction, est)
            timeout_s = (timeout_ms if timeout_ms is not None
                         else budget.timeout_ms) / 1000.0
            outcome: dict = {}

            def _run():
                try:
                    outcome["resp"] = self._decide_fn(state, questions)
                except Exception as exc:  # noqa: BLE001
                    outcome["error"] = exc

            # Detached-wait: the worker is never cancelled — the socket
            # timeout inside decide_fn is the in-flight call's bound (the
            # race's "detaches — never cancels" discipline).
            t = threading.Thread(target=_run, daemon=True,
                                 name=f"sentinel-advisory-{direction}")
            t.start()
            t.join(timeout_s)
            if t.is_alive():
                log.warning("[sentinel] advisory %s timed out after %.1fs "
                            "(fallback, call detached)", direction, timeout_s)
                return [self._fallback_part(d, "timeout", fb, alert_id,
                                            fingerprint, episode_id)
                        for d, _oj, fb in parts]
            if "error" in outcome:
                exc = outcome["error"]
                log.warning("[sentinel] advisory %s Jev call failed: %r "
                            "(fallback)", direction, exc)
                return [self._fallback_part(
                    d, f"error:{type(exc).__name__}", fb,
                    alert_id, fingerprint, episode_id)
                    for d, _oj, fb in parts]
            resp = outcome.get("resp")
            return [self._jev_part(d, resp, oj, fb, alert_id, fingerprint,
                                   episode_id)
                    for d, oj, fb in parts]
        except Exception as exc:  # never raises — the ultimate backstop
            log.warning("[sentinel] advisory %s dispatcher backstop: %r",
                        direction, exc, exc_info=True)
            return [self._fallback_part(
                d, "dispatcher_error", (lambda _r: {}), alert_id,
                fingerprint, episode_id) for d, _oj, _fb in parts]

    def call_inline(self, direction: str, state, questions: dict,
                    on_jev, on_fallback, **kwargs) -> dict:
        """Single-envelope wrapper over call_multi."""
        parts = [(direction, on_jev, on_fallback)]
        return self.call_multi(direction, state, questions, parts,
                               **kwargs)[0]

    # -- public: detached submit -------------------------------------------
    def submit_multi(self, direction: str, state, questions: dict,
                     parts: list[tuple], on_done, *,
                     alert_id: str | None = None,
                     fingerprint: str | None = None,
                     episode_id: str | None = None) -> bool:
        """Detached advisory call. ``on_done(envelopes)`` fires exactly
        once — with jev envelopes, or honestly-labeled fallbacks on
        shed/failure. Returns True when submitted, False when the
        enrichment fell back synchronously (shed/gate-failed). Never
        raises."""
        try:
            ok, reason, est = self._pre_call_gates(direction, state, questions)
            if not ok:
                on_done([self._fallback_part(d, reason, fb, alert_id,
                                            fingerprint, episode_id)
                         for d, _oj, fb in parts])
                return False
            self._charge(direction, est)

            def _worker():
                try:
                    resp = self._decide_fn(state, questions)
                except Exception as exc:  # noqa: BLE001
                    log.warning("[sentinel] advisory %s detached call failed: "
                                "%r (fallback)", direction, exc)
                    resp = None
                if resp is None:
                    envs = [self._fallback_part(d, "detached_error", fb,
                                                alert_id, fingerprint,
                                                episode_id)
                            for d, _oj, fb in parts]
                else:
                    envs = [self._jev_part(d, resp, oj, fb, alert_id,
                                           fingerprint, episode_id)
                            for d, oj, fb in parts]
                try:
                    on_done(envs)
                except Exception:
                    log.warning("[sentinel] advisory on_done raised; "
                                "swallowed so the pool thread never dies loud",
                                exc_info=True)

            fut = self._pool.submit(_worker)
            if fut is None:
                log.warning("[sentinel] advisory %s shed: advisory pool full "
                            "(enrichment dropped, page/suppression "
                            "unaffected)", direction)
                on_done([self._fallback_part(d, "shed", fb, alert_id,
                                            fingerprint, episode_id)
                         for d, _oj, fb in parts])
                return False
            return True
        except Exception as exc:  # never raises
            log.warning("[sentinel] advisory %s submit backstop: %r",
                        direction, exc, exc_info=True)
            try:
                on_done([self._fallback_part(
                    d, "dispatcher_error", (lambda _r: {}), alert_id,
                    fingerprint, episode_id) for d, _oj, _fb in parts])
            except Exception:
                pass
            return False

    def submit(self, direction: str, state, questions: dict,
               on_jev, on_fallback, on_done, **kwargs) -> bool:
        """Single-envelope detached submit."""
        parts = [(direction, on_jev, on_fallback)]
        return self.submit_multi(direction, state, questions, parts,
                                 lambda envs: on_done(envs[0]), **kwargs)

    def close(self) -> None:
        self._pool.close()


# ---------------------------------------------------------------------------
# Question builders (typed: Choice/Score/Noul — Jev writes no text)
# ---------------------------------------------------------------------------

def _choice(qid: str, instructions: str,
            criteria: dict[str, str]) -> dict:
    crit = dict(criteria)
    crit.setdefault("cannot_determine",
                    "The context is insufficient or contradictory; do not guess.")
    return {"type": "choice", "instructions": instructions, "criteria": crit}


def _noul(qid: str, instructions: str, yes_means: str,
          no_means: str) -> dict:
    return {"type": "noul", "instructions": instructions,
            "criteria": {"yes": yes_means, "no": no_means}}


def _score(qid: str, instructions: str, low_means: str,
           high_means: str, levels: int = 9) -> dict:
    # Score: 2-10 ordered levels (levels=9 => levels 2..10). Ordinal only.
    return {"type": "score", "instructions": instructions,
            "levels": levels,
            "criteria": {"low": low_means, "high": high_means}}


# D1 leg sets per suppression reason (keys match advisory_render sentences).
_D1_LEG_SETS: dict[str, list[tuple[str, str]]] = {
    "allowlist": [
        ("allowlist_hit", "The alert's fingerprint matched an allowlist entry."),
        ("dual_attestation", "The allowlist entry carried two fresh human attestations."),
        ("corroboration_floor", "An independent corroborating signal confirmed the benign read."),
        ("freshness_ok", "Evidence freshness legs were green."),
        ("severity_confidence_low", "Jev's severity read was low-confidence."),
        ("known_noise_pattern", "The alert matched a recurring benign pattern."),
    ],
    "threshold": [
        ("severity_confidence_low", "Jev's severity read was below the suppress floor."),
        ("disposition_confidence", "The disposition answer cleared the suppress confidence floor."),
        ("corroboration_floor", "An independent corroborating signal confirmed the benign read."),
        ("freshness_ok", "Evidence freshness legs were green."),
        ("known_noise_pattern", "The alert matched a recurring benign pattern."),
    ],
}
_DEFAULT_D1_LEGS: list[tuple[str, str]] = [
    ("severity_confidence_low", "The severity evidence did not clear the page floor."),
    ("disposition_confidence", "The disposition answer cleared the suppress confidence floor."),
    ("corroboration_floor", "An independent corroborating signal confirmed the benign read."),
    ("freshness_ok", "Evidence freshness legs were green."),
    ("known_noise_pattern", "The alert matched a recurring benign pattern."),
]


def d1_leg_set(reason: str) -> list[tuple[str, str]]:
    return list(_D1_LEG_SETS.get(reason, _DEFAULT_D1_LEGS))


def d1_questions(reason: str) -> dict:
    """D1: Choice dominant_leg + Noul worth_second_look."""
    legs = d1_leg_set(reason)[:8]
    criteria = {name: desc for name, desc in legs}
    return {
        "dominant_leg": _choice(
            "dominant_leg",
            "Which recorded evidence leg would a human on-call find least "
            "obvious in this suppression? The suppression already happened; "
            "you are only choosing which leg to explain first.",
            criteria),
        "worth_second_look": _noul(
            "worth_second_look",
            "Does anything in the alert context look inconsistent with this "
            "suppression — a detail a human reviewer should double-check in "
            "business hours?",
            yes_means="Something looks inconsistent with the suppression.",
            no_means="Nothing looks inconsistent; the suppression reads clean."),
    }


def d1_should_fire(payload: dict) -> bool:
    """D1 volume guard: surprising suppressions (P1/P2 source severity) plus
    a stable 1% sample (fingerprint-hash mod 100) of the rest."""
    sev = str(payload.get("severity_in", "")).lower()
    if sev in ("critical", "high", "p1", "p2", "emergency", "fatal"):
        return True
    fp = str(payload.get("fingerprint", ""))
    return int(hashlib.sha256(fp.encode("utf-8")).hexdigest(), 16) % 100 == 0


def merged_storm_questions(*, team_options=None,
                           clusters: list[str],
                           incidents: list[dict],
                           runbooks: list[dict],
                           hypotheses: list[str]) -> dict:
    """The ONE merged storm call (D2+D3+D4): exactly 8 questions.

    Carries the existing D3 root-cause candidates (severity, owning_team —
    the disposition question is not re-asked: the aggregate pages by
    construction) plus the six advisory questions. One decide() call, not
    eight (approval condition 5).
    """
    base = build_questions(team_options)
    questions = {
        "severity": base["severity"],
        "owning_team": base["owning_team"],
        "brief_lead": _choice(
            "brief_lead",
            "Which member cluster should lead the one-paragraph storm brief "
            "— the cluster a human on-call most needs to see first?",
            {c: f"Member cluster {c}." for c in clusters[:12]}),
        "pattern_novelty": _score(
            "pattern_novelty",
            "How novel is this storm's pattern compared to recent storms?",
            low_means="Routine: matches recent storm patterns.",
            high_means="Novel: unlike anything seen recently."),
        "hypothesis_choice": _choice(
            "hypothesis_choice",
            "Which candidate root-cause hypothesis is the most plausible "
            "starting point for the on-call? Advisory only — unconfirmed.",
            {h: f"Candidate hypothesis: {h}." for h in hypotheses[:20]}),
        "hypothesis_plausibility": _score(
            "hypothesis_plausibility",
            "How plausible is the chosen hypothesis, as an ordinal level?",
            low_means="Weak: barely better than guessing.",
            high_means="Strong: the evidence points here."),
        "incident_rank": _choice(
            "incident_rank",
            "Which retrieved past incident is most relevant to this storm?",
            {i.get("incident_id", f"incident-{n}"):
             str(i.get("summary", "past incident"))[:200]
             for n, i in enumerate(incidents[:12])}),
        "runbook_rank": _choice(
            "runbook_rank",
            "Which retrieved runbook is most relevant to this storm?",
            {r.get("runbook_id", f"runbook-{n}"):
             str(r.get("title", "runbook"))[:200]
             for n, r in enumerate(runbooks[:8])}),
    }
    assert len(questions) == 8, f"merged storm call must carry 8 questions, got {len(questions)}"
    return questions


def d3_questions(incidents: list[dict], runbooks: list[dict]) -> dict:
    """D3 standalone (page_now path): rank retrieved candidates + stale flag."""
    return {
        "incident_rank": _choice(
            "incident_rank",
            "Which retrieved past incident is most relevant to this page?",
            {i.get("incident_id", f"incident-{n}"):
             str(i.get("summary", "past incident"))[:200]
             for n, i in enumerate(incidents[:12])}),
        "runbook_rank": _choice(
            "runbook_rank",
            "Which retrieved runbook is most relevant to this page?",
            {r.get("runbook_id", f"runbook-{n}"):
             str(r.get("title", "runbook"))[:200]
             for n, r in enumerate(runbooks[:8])}),
        "bundle_stale": _noul(
            "bundle_stale",
            "Is the retrieved material contradictory or stale relative to "
            "this alert?",
            yes_means="The retrieved material looks stale/contradictory.",
            no_means="The retrieved material looks consistent and fresh."),
    }


def d4_questions(hypotheses: list[str]) -> dict:
    """D4 standalone: Choice top_hypothesis + Score plausibility (ordinal)."""
    return {
        "hypothesis_choice": _choice(
            "hypothesis_choice",
            "Which candidate root-cause hypothesis is the most plausible "
            "starting point for the on-call? Advisory only — unconfirmed.",
            {h: f"Candidate hypothesis: {h}." for h in hypotheses[:20]}),
        "hypothesis_plausibility": _score(
            "hypothesis_plausibility",
            "How plausible is the chosen hypothesis, as an ordinal level?",
            low_means="Weak: barely better than guessing.",
            high_means="Strong: the evidence points here."),
    }


def d5_triage_questions(cases: list[dict]) -> dict:
    """D5 advisory triage: Choice riskiest_delta over code-proposed cases."""
    criteria = {}
    for n, c in enumerate(cases[:25]):
        key = c.get("case_id", f"case-{n}")
        criteria[key] = (
            f"Newly suppressed page {c.get('alert_id', '?')} "
            f"({c.get('severity_in', '?')}, {c.get('fingerprint', '?')[:12]}): "
            f"{c.get('recorded_action', '?')} -> {c.get('replay_action', '?')}"
        )[:240]
    return {
        "riskiest_delta": _choice(
            "riskiest_delta",
            "Which of these newly-suppressed pages would a human reviewer "
            "most want to inspect first? You are only ranking review order — "
            "never whether the policy change ships.",
            criteria),
    }


def d6_questions(team_options: list[tuple[str, str]] | None = None) -> dict:
    """D6: suggested owner + severity over a richer state (post-page)."""
    team_criteria = ({name: desc for name, desc in team_options}
                     if team_options else {
                         "platform": "Core infra, kubernetes, CI/CD runners.",
                         "network": "DNS, CDN, load balancers, VPC, transit.",
                         "data": "Databases, caches, queues, pipelines.",
                         "product_backend": "Application services and APIs.",
                         "security": "Auth, WAF, intrusion, certificates.",
                     })
    return {
        "suggested_owner": _choice(
            "suggested_owner",
            "Which team should own this paged alert? A suggestion for the "
            "on-call to confirm or override — the page already went out.",
            team_criteria),
        "suggested_severity": _choice(
            "suggested_severity",
            "What severity best describes this paged alert? A suggestion "
            "for the on-call to confirm or override.",
            {
                "p1_critical": "Customer-facing outage or data-loss risk in progress.",
                "p2_high": "Core function degraded or at imminent risk of full outage.",
                "p3_medium": "Non-critical degradation; safe for business hours.",
                "p4_low": "Informational; no action required unless it recurs.",
            }),
    }


def d6_should_fire(payload: dict) -> bool:
    """D6 trigger (minority path only): the race did not produce a confident
    Jev read — timer-win, or any race answer was cannot_determine, or no
    race answers were recorded. A clean race read => skip (no second call)."""
    if payload.get("race_source") == "timer":
        return True
    answers = payload.get("race_answers") or {}
    if not answers:
        return True
    return any(v == "cannot_determine" for v in answers.values())


# ---------------------------------------------------------------------------
# Direction entries — post-decision, emitted payloads in, envelopes out
# ---------------------------------------------------------------------------

def _payload_get(payload: dict, *keys, default=None):
    if not isinstance(payload, dict):
        return default
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    for key in keys:
        if key in payload:
            return payload[key]
        if key in body:
            return body[key]
    return default


def suppression_explainer(dispatcher: AdvisoryDispatcher, payload: dict,
                          emit_fn, *, recorded_ts: str | None = None) -> bool:
    """D1 — "why you weren't woken". Post-suppression, read-only.

    On worth_second_look=yes (ordinal high) a ``human_second_look`` tag is
    recorded on the suppression record — routed to the business-hours
    review digest. The digest surface itself is OUT of scope for Track 5
    (Track 8 / future): this track only attaches the tag.
    Never raises.
    """
    try:
        if not d1_should_fire(payload):
            return False
        reason = str(_payload_get(payload, "reason", default="unknown"))
        alert_id = _payload_get(payload, "alert_id")
        fingerprint = _payload_get(payload, "fingerprint")
        legs = list(_payload_get(payload, "evidence_legs", default=[]) or [])
        questions = d1_questions(reason)
        state = {"alert_id": alert_id, "fingerprint": fingerprint,
                 "reason": reason, "legs": legs,
                 "severity_in": _payload_get(payload, "severity_in")}

        def on_jev(resp):
            answers = resp.answers or {}
            dom = answers.get("dominant_leg")
            emphasis = getattr(dom, "choice", None)
            if emphasis == "cannot_determine":
                emphasis = None
            second = answers.get("worth_second_look")
            tag = (getattr(second, "noul", 0.0) or 0.0) >= 0.5
            paragraph = render_suppression_paragraph(
                reason=reason, legs=legs, emphasis_leg=emphasis,
                alert_id=str(alert_id), fingerprint=str(fingerprint),
                recorded_ts=recorded_ts or "unknown")
            body = {"paragraph": paragraph, "emphasis_leg": emphasis}
            if tag:
                # Approved: recorded on the suppression record; the digest
                # surface consuming it is Track 8 / future work.
                body["human_second_look"] = True
            return body

        def on_fallback(_reason):
            return {"paragraph": render_suppression_paragraph(
                reason=reason, legs=legs, emphasis_leg=None,
                alert_id=str(alert_id), fingerprint=str(fingerprint),
                recorded_ts=recorded_ts or "unknown"),
                "emphasis_leg": None}

        return dispatcher.submit(
            "suppression_explainer", state, questions, on_jev, on_fallback,
            lambda env: emit_fn(env),
            alert_id=alert_id, fingerprint=fingerprint)
    except Exception as exc:  # never raises
        log.warning("[sentinel] D1 suppression_explainer backstop: %r", exc)
        return False


def merged_storm_advisory(dispatcher: AdvisoryDispatcher, storm_ctx: dict,
                          emit_fn) -> bool:
    """D2+D3+D4 merged: ONE decide() call carrying 8 questions; three
    direction envelopes out (storm_brief, evidence_bundle, rca_hypotheses),
    each labeled per its own fallback. Never raises."""
    try:
        clusters = list(storm_ctx.get("clusters", []) or [])
        incidents = list(storm_ctx.get("incidents", []) or [])
        runbooks = list(storm_ctx.get("runbooks", []) or [])
        hypotheses = list(storm_ctx.get("hypotheses", []) or [])
        questions = merged_storm_questions(
            team_options=storm_ctx.get("team_options"),
            clusters=clusters, incidents=incidents, runbooks=runbooks,
            hypotheses=hypotheses)
        state = {"storm_size": storm_ctx.get("storm_size"),
                 "storm_counts": storm_ctx.get("storm_counts", {}),
                 "clusters": clusters[:12],
                 "incidents": incidents[:12], "runbooks": runbooks[:8],
                 "hypotheses": hypotheses[:20]}
        episode_id = storm_ctx.get("episode_id")
        alert_id = storm_ctx.get("alert_id")

        def _ans(resp, qid):
            return (resp.answers or {}).get(qid) if resp is not None else None

        def brief_jev(resp):
            lead = getattr(_ans(resp, "brief_lead"), "choice", None)
            nov = getattr(_ans(resp, "pattern_novelty"), "choice", None)
            try:
                nov = int(nov) if nov is not None else None
            except (TypeError, ValueError):
                nov = None
            return {"brief": render_storm_brief(
                storm_size=int(storm_ctx.get("storm_size") or 0),
                storm_counts=dict(storm_ctx.get("storm_counts") or {}),
                clusters=clusters, lead_pick=lead, novelty_level=nov)}

        def brief_fb(_reason):
            return {"brief": render_storm_brief(
                storm_size=int(storm_ctx.get("storm_size") or 0),
                storm_counts=dict(storm_ctx.get("storm_counts") or {}),
                clusters=clusters, lead_pick=None, novelty_level=None)}

        def bundle_jev(resp):
            inc_pick = getattr(_ans(resp, "incident_rank"), "choice", None)
            rb_pick = getattr(_ans(resp, "runbook_rank"), "choice", None)
            # NOTE: the merged call has no bundle_stale question; the
            # standalone D3 path carries it. Merged bundles are unflagged.
            return {"bundle": assemble_evidence_bundle(
                incidents=incidents, runbooks=runbooks,
                incident_pick=inc_pick, runbook_pick=rb_pick, stale=False)}

        def bundle_fb(_reason):
            return {"bundle": assemble_evidence_bundle(
                incidents=incidents, runbooks=runbooks,
                incident_pick=None, runbook_pick=None, stale=False)}

        def hyp_jev(resp):
            choice = getattr(_ans(resp, "hypothesis_choice"), "choice", None)
            plaus = getattr(_ans(resp, "hypothesis_plausibility"),
                            "choice", None)
            try:
                plaus = int(plaus) if plaus is not None else None
            except (TypeError, ValueError):
                plaus = None
            if not choice or choice == "cannot_determine":
                return {}
            return {"hypothesis": render_hypothesis(
                hypothesis=choice, plausibility_level=plaus)}

        def hyp_fb(_reason):
            return {}  # D4 fallback is honest absence

        parts = [
            ("storm_brief", brief_jev, brief_fb),
            ("evidence_bundle", bundle_jev, bundle_fb),
            ("rca_hypotheses", hyp_jev, hyp_fb),
        ]
        return dispatcher.submit_multi(
            "storm_merged", state, questions, parts,
            lambda envs: [emit_fn(e) for e in envs],
            alert_id=alert_id, episode_id=episode_id)
    except Exception as exc:  # never raises
        log.warning("[sentinel] merged storm advisory backstop: %r", exc)
        return False


def evidence_bundler(dispatcher: AdvisoryDispatcher, payload: dict,
                     emit_fn, *, incidents: list[dict],
                     runbooks: list[dict]) -> bool:
    """D3 standalone (page_now path): deterministic retrieval is done by the
    caller; Jev only ranks. Never raises."""
    try:
        alert_id = _payload_get(payload, "alert_id")
        fingerprint = _payload_get(payload, "fingerprint")
        questions = d3_questions(incidents, runbooks)
        state = {"alert_id": alert_id, "fingerprint": fingerprint,
                 "incidents": incidents[:12], "runbooks": runbooks[:8],
                 "check": _payload_get(payload, "check")}

        def on_jev(resp):
            answers = resp.answers or {}
            inc_pick = getattr(answers.get("incident_rank"), "choice", None)
            rb_pick = getattr(answers.get("runbook_rank"), "choice", None)
            stale_ans = answers.get("bundle_stale")
            stale = (getattr(stale_ans, "noul", 0.0) or 0.0) >= 0.5
            return {"bundle": assemble_evidence_bundle(
                incidents=incidents, runbooks=runbooks,
                incident_pick=inc_pick, runbook_pick=rb_pick, stale=stale)}

        def on_fallback(_reason):
            # Page ships with the raw retrieved bundle in recency order.
            return {"bundle": assemble_evidence_bundle(
                incidents=incidents, runbooks=runbooks,
                incident_pick=None, runbook_pick=None, stale=False)}

        return dispatcher.submit(
            "evidence_bundler", state, questions, on_jev, on_fallback,
            lambda env: emit_fn(env),
            alert_id=alert_id, fingerprint=fingerprint)
    except Exception as exc:  # never raises
        log.warning("[sentinel] D3 evidence_bundler backstop: %r", exc)
        return False


def rca_hypotheses(dispatcher: AdvisoryDispatcher, payload: dict,
                   emit_fn, *, hypotheses: list[str]) -> bool:
    """D4 standalone: advisory root-cause hypotheses, framed unconfirmed.
    Fallback is honest absence. Never raises."""
    try:
        alert_id = _payload_get(payload, "alert_id")
        fingerprint = _payload_get(payload, "fingerprint")
        questions = d4_questions(hypotheses)
        state = {"alert_id": alert_id, "fingerprint": fingerprint,
                 "hypotheses": hypotheses[:20]}

        def on_jev(resp):
            answers = resp.answers or {}
            choice = getattr(answers.get("hypothesis_choice"), "choice", None)
            plaus = getattr(answers.get("hypothesis_plausibility"),
                            "choice", None)
            try:
                plaus = int(plaus) if plaus is not None else None
            except (TypeError, ValueError):
                plaus = None
            if not choice or choice == "cannot_determine":
                return {}
            return {"hypothesis": render_hypothesis(
                hypothesis=choice, plausibility_level=plaus)}

        return dispatcher.submit(
            "rca_hypotheses", state, questions, on_jev, lambda _r: {},
            lambda env: emit_fn(env),
            alert_id=alert_id, fingerprint=fingerprint)
    except Exception as exc:  # never raises
        log.warning("[sentinel] D4 rca_hypotheses backstop: %r", exc)
        return False


def triage_suggest(dispatcher: AdvisoryDispatcher, payload: dict,
                   emit_fn, *, team_options=None) -> bool:
    """D6 — suggested owner/severity, human confirms. Fires ONLY on the
    minority path (timer-win / cannot_determine race). Never raises."""
    try:
        if not d6_should_fire(payload):
            return False
        alert_id = _payload_get(payload, "alert_id")
        fingerprint = _payload_get(payload, "fingerprint")
        questions = d6_questions(team_options)
        state = {"alert_id": alert_id, "fingerprint": fingerprint,
                 "check": _payload_get(payload, "check"),
                 "service": _payload_get(payload, "service"),
                 "race_source": payload.get("race_source"),
                 "sibling_alerts": payload.get("sibling_alerts", [])[:8]}

        def on_jev(resp):
            answers = resp.answers or {}
            owner = getattr(answers.get("suggested_owner"), "choice", None)
            sev = getattr(answers.get("suggested_severity"), "choice", None)
            if owner == "cannot_determine":
                owner = None
            if sev == "cannot_determine":
                sev = None
            return {"suggestion": render_triage_suggestion(
                owner=owner, severity=sev)}

        return dispatcher.submit(
            "triage_suggest", state, questions, on_jev, lambda _r: {},
            lambda env: emit_fn(env),
            alert_id=alert_id, fingerprint=fingerprint)
    except Exception as exc:  # never raises
        log.warning("[sentinel] D6 triage_suggest backstop: %r", exc)
        return False
