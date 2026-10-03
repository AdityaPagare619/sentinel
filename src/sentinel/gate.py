"""Gate — Jev call races a paging budget + threshold policy + fail-open.

Policy table (frozen §4), evaluated in order:
  suppress            P(p1_critical) < 0.002 AND Q3 conf >= 0.90 AND fp in allowlist
  page_now            P(p1)+P(p2) > 0.30 OR Q3 conf < 0.50 (uncertainty pages)
  page_business_hours P(p3)+P(p4) dominant AND Q3 conf >= 0.70
  passthrough         client error/timeout, Q3 = cannot_determine, else uncertain

Race-to-page (ADR-010, design/fixes/01-race-to-page.md): the Jev call never
blocks the page. It races budget B on the inference pool; the timer's
default action is passthrough (deterministic, not model output); a late
answer becomes a ``shadow_decision`` payload via the late-answer hook —
it never pages, never suppresses. The ONLY Jev-call site is the pool
submission inside ``RaceRunner.run``.

Deterministic pre-Jev paths arrive via the optional `correlation` kwarg
(dup/change_window/storm-continuation from the Correlator) and never call
Jev — they emit ``structural_passthrough`` decision payloads.

The gate EMITS decision payloads (decision_made per decision,
shadow_decision for late answers); it does NOT write events — the
dispatcher/event-log lane owns persistence (see race_payloads.py's
EMISSION CONTRACT).

Invariants:
  * audit row is ALWAYS written, even on error / passthrough;
  * ANY exception from the client -> passthrough "error:<code>", NEVER raised;
  * shadow=True logs the would-be disposition (action recorded, reason
    "shadow") but always returns passthrough.
"""

from __future__ import annotations

import datetime as _dt
import re
import sys
import time

from . import race
from .client import JevError
from .models import Alert, DecisionRecord, Disposition, Thresholds
from .questions import build_questions
from .quantized import (AllowlistEntry, FitStore, leg1_prob_lock)
from .race_payloads import (decision_made_payload, empty_lock_evaluation,
                            lock_evaluation, shadow_decision_payload)
from .state import input_sha256

# Frozen contract: evaluate(self, alert, state, history, context).
# `correlation` is an optional extension (defaults None) carrying the
# Correlator's verdict so deterministic paths skip the Jev call.


def _error_code(exc: BaseException) -> str:
    name = type(exc).__name__
    if name.startswith("Jev") and len(name) > 3:
        name = name[3:]
    code = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return code or "unknown"


def _reported_p1(q_severity):
    """Reported P(p1) as received (for payloads; None when absent)."""
    if q_severity is None:
        return None
    probs = getattr(q_severity, "probabilities", None) or {}
    try:
        return float(probs.get("p1_critical", 0.0))
    except (TypeError, ValueError):
        return None


class PolicyVerdict:
    """Output of the pure policy kernel (frozen §4 table)."""

    __slots__ = ("action", "reason", "team", "confidence", "latency_ms",
                 "lock_evaluation", "jev_model", "q1_reported", "q2_team",
                 "q3_confidence", "q3_disposition")

    def __init__(self, action, reason, team, confidence, latency_ms,
                 lock_evaluation, jev_model=None, q1_reported=None,
                 q2_team=None, q3_confidence=None, q3_disposition=None):
        self.action = action
        self.reason = reason
        self.team = team
        self.confidence = confidence
        self.latency_ms = latency_ms
        self.lock_evaluation = lock_evaluation
        self.jev_model = jev_model
        self.q1_reported = q1_reported
        self.q2_team = q2_team
        self.q3_confidence = q3_confidence
        self.q3_disposition = q3_disposition


def evaluate_policy(alert, *, jev_model, q_severity, q_team, q_disposition,
                    thresholds: Thresholds, allowlist: set,
                    latency_ms: float, prob_lock_pass: bool = False) -> PolicyVerdict:
    """The pure policy kernel — the frozen §4 table, no I/O, no clocks.

    Shared by the live gate AND the race's late-answer path (one kernel,
    not a copy — ADR-010 §4.2: ``shadow_disposition`` is what the gate
    *would* have decided had the answer arrived in time).

    Raises JevError on malformed answers (uncertainty pages — Law 7).
    """
    q1, q2, q3 = q_severity, q_team, q_disposition
    if q1 is None or q3 is None:
        raise JevError("Jev response missing severity/disposition answers")

    probs = q1.probabilities or {}
    p1 = float(probs.get("p1_critical", 0.0))
    p2 = float(probs.get("p2_high", 0.0))
    p3 = float(probs.get("p3_medium", 0.0))
    p4 = float(probs.get("p4_low", 0.0))
    conf3 = q3.confidence  # may be None -> uncertainty pages
    team = getattr(q2, "choice", None) if q2 is not None else None
    t = thresholds

    # Per-lock verdicts, evaluated at answer time. The detail strings say
    # exactly what was checked: attestation freshness is NOT enforced yet
    # (ADR-014 — the freshness lane owns it), so the allowlist leg never
    # silently approximates it.
    # ADR-013: quantized lock replaces the naive p1 threshold.
    prob_pass = prob_lock_pass
    conf_pass = conf3 is not None and conf3 >= t.suppress_conf_min
    allow_pass = alert.fingerprint in allowlist
    locks = lock_evaluation(
        prob_pass,
        f"p1_critical={p1:.4f} {'<' if prob_pass else '>='} "
        f"suppress_p1_max={t.suppress_p1_max}",
        conf_pass,
        f"q3_confidence={conf3} "
        f"{'>=' if conf_pass else '<'} suppress_conf_min={t.suppress_conf_min}",
        allow_pass,
        ("fingerprint in allowlist" if allow_pass
         else "fingerprint NOT in allowlist")
        + "; attestation freshness not yet enforced (ADR-014/freshness lane)",
    )

    if q3.choice == "cannot_determine":
        action, reason = "passthrough", "uncertain"
    elif prob_pass and conf_pass and allow_pass:
        action, reason = "suppress", "allowlist"          # triple lock
    elif (p1 + p2 > t.page_p1p2_min
          or conf3 is None or conf3 < t.uncertain_conf_max):
        action, reason = "page_now", "threshold"          # uncertainty pages
    elif (p3 + p4) >= 0.50 and conf3 >= t.queue_conf_min:
        action, reason = "page_business_hours", "threshold"
    else:
        action, reason = "passthrough", "uncertain"

    return PolicyVerdict(
        action, reason, team, conf3, latency_ms, locks,
        jev_model=jev_model,
        q1_reported=p1,
        q2_team=team,
        q3_confidence=conf3,
        q3_disposition=q3.choice,
    )


def _default_watchdog_page(rate: float, n: int) -> None:
    """Placeholder operator page: loud stderr until the engine owns an
    operator-paging channel (platform tier). Never silent."""
    print(f"[sentinel] WATCHDOG PAGE: timer-win rate {rate:.1%} over the "
          f"last {n} decisions (1h rolling window) — vendor degraded or B "
          f"misconfigured; operator action required", file=sys.stderr)


class Gate:
    def __init__(self, client, thresholds: Thresholds,
                 allowlist, audit, shadow: bool = False,
                 race_config=None, emit=None, race_metrics=None,
                 on_watchdog_page=None, on_scheduler_stall=None,
                 *, fit_store: FitStore | None = None,
                 pinned_model: str | None = None,
                 org: str | None = None,
                 clock=None,
                 policy_gate=None,
                 policy_id: str = "suppression"):
        """policy_gate: optional ADR-022/D8 enforcement hook with
        ``can_suppress(policy_id) -> (bool, reason)``. When present and the
        verdict is suppress, a False answer downgrades to passthrough — the
        watchdog freeze and the B3 expired state are enforced here, on the
        hot path, not in the UI. Never raises (fail toward the human)."""
        self.client = client
        self.thresholds = thresholds
        # Allowlist entries: ADR-017/019. Accepts a plain set of fingerprints
        # (legacy shape: leg-3 membership only, no attestation evidence) or
        # AllowlistEntry objects carrying the dual-attestation tuples the
        # quantized leg 1 needs pre-fit (design §2.3).
        self.allowlist_entries: dict[str, AllowlistEntry | None] = {}
        if isinstance(allowlist, dict):
            items = allowlist.items()
        else:
            items = [(a, a) if isinstance(a, str) else (a.fingerprint, a)
                     for a in (allowlist or [])]
        for fp, entry in items:
            if isinstance(entry, str):
                self.allowlist_entries[fp] = None
            else:
                self.allowlist_entries[entry.fingerprint] = entry
        # Backward compat for the pure policy kernel (needs fingerprint set).
        self.allowlist = set(self.allowlist_entries.keys())
        self.audit = audit
        self.shadow = shadow
        # Race integration (ADR-010): bounded pool, timer scheduler, watchdog.
        self._emit_fn = emit
        self.emitted: list[dict] = []
        self._race_metrics = race_metrics or race.Metrics()
        cfg = (race_config if isinstance(race_config, race.RaceConfig)
               else race.RaceConfig.from_raw(race_config,
                                             metrics=self._race_metrics))
        self._runner = race.RaceRunner(
            client, cfg, metrics=self._race_metrics,
            late_answer_hook=self._on_late_answer,
            on_scheduler_stall=on_scheduler_stall)
        self._runner.watchdog.on_trip = (
            on_watchdog_page if on_watchdog_page is not None
            else _default_watchdog_page)
        # ADR-013 quantized prob lock (design/fixes/04-quantized-gate.md).
        self.fit_store = fit_store
        self.pinned_model = pinned_model
        self.org = org
        self.clock = clock  # () -> aware datetime; tests inject a fixed now
        self.policy_gate = policy_gate
        self.policy_id = policy_id

    def close(self) -> None:
        """Shut down the race scheduler/pool/watchdog threads."""
        self._runner.close()

    # ------------------------------------------------------------------ API

    def evaluate(self, alert: Alert, state: dict, history: dict,
                 context: dict, correlation=None) -> tuple:
        """Return (Disposition, DecisionRecord). Never raises."""
        t0 = time.perf_counter()
        try:
            disp, answers = self._decide(alert, state, history, context,
                                         correlation)
        except Exception as exc:  # fail open — the company-ending bug is dropping pages
            code = _error_code(exc)
            latency = (time.perf_counter() - t0) * 1000.0
            disp = Disposition(action="passthrough", reason=f"error:{code}",
                               team=None, confidence=None, latency_ms=latency)
            answers = {"jev_model": None, "q_severity": None,
                       "q_team": None, "q_disposition": None}

        if self.shadow:
            would_be = disp
            audit_disp = Disposition(
                action=would_be.action, reason="shadow", team=would_be.team,
                confidence=would_be.confidence, latency_ms=would_be.latency_ms)
            disp = Disposition(
                action="passthrough", reason="shadow", team=would_be.team,
                confidence=would_be.confidence, latency_ms=would_be.latency_ms)
        else:
            audit_disp = disp

        rec = DecisionRecord(
            alert=alert,
            input_sha256=input_sha256(state),
            jev_model=answers["jev_model"],
            q_severity=answers["q_severity"],
            q_team=answers["q_team"],
            q_disposition=answers["q_disposition"],
            disposition=audit_disp,
        )
        try:
            self.audit.record(rec)
        except Exception as exc:  # audit must never sink the alert
            print(f"[sentinel] audit write failed for {alert.alert_id}: {exc}",
                  file=sys.stderr)
        return disp, rec

    # -------------------------------------------------------------- internals

    def _emit(self, payload: dict) -> None:
        """Emit a decision payload. Must never sink the alert."""
        try:
            if self._emit_fn is not None:
                self._emit_fn(payload)
            else:
                self.emitted.append(payload)
        except Exception as exc:
            print(f"[sentinel] emit failed: {exc}", file=sys.stderr)

    def _structural(self, alert, state, correlation):
        """Deterministic pre-Jev paths (S1) — no client call, no race armed.

        Returns (Disposition, answers) or None when the race must run."""
        if correlation is None:
            return None
        kind = getattr(correlation, "kind", None)
        if kind == "duplicate" and correlation.prior is not None:
            prior = correlation.prior
            return (Disposition(action=prior.action, reason="dedup",
                                team=prior.team, confidence=prior.confidence,
                                latency_ms=0.0),
                    _empty_answers())
        if kind == "change_window":
            return (Disposition(action="page_business_hours",
                                reason="change_window", team=None,
                                confidence=None, latency_ms=0.0),
                    _empty_answers())
        if kind == "storm" and not getattr(correlation, "storm_declared", False):
            # Folded into the aggregate page: no individual forward.
            return (Disposition(action="suppress", reason="storm", team=None,
                                confidence=None, latency_ms=0.0),
                    _empty_answers())
        return None

    def _leg1_prob_lock(self, reported_p1, alert, context) -> bool:
        """ADR-013 leg 1 — the quantized probability lock (§2.1).

        Never raises: any failure fails the leg closed (no suppression on
        this path); the gate's outer fail-open still pages on true errors.
        """
        now = self.clock() if self.clock else _dt.datetime.now(_dt.timezone.utc)
        org = (context or {}).get("org") or self.org
        entry = self.allowlist_entries.get(alert.fingerprint)
        try:
            ok, _detail = leg1_prob_lock(
                reported_p1, org=org, now=now,
                fit_store=self.fit_store, pinned_model=self.pinned_model,
                entry=entry)
        except Exception:
            return False
        return ok

    def _decide(self, alert, state, history, context, correlation):
        in_sha = input_sha256(state)
        budget_ms = self._runner.config.budget_ms

        # S1 — structural bars: deterministic, pre-race, never pay Jev for
        # a decision already made.
        structural = self._structural(alert, state, correlation)
        if structural is not None:
            disp, answers = structural
            self._emit(decision_made_payload(
                alert=alert, input_sha256=in_sha,
                disposition=disp.action,
                budget_outcome=race.STRUCTURAL_PASSTHROUGH,
                budget_ms=budget_ms, latency_ms=disp.latency_ms,
                lock_evaluation=empty_lock_evaluation()))
            return disp, answers

        # S2 — FAST: win the race. The pool submission inside runner.run()
        # is the only Jev-call site on the hot path.
        questions = build_questions((context or {}).get("team_options"))
        outcome = self._runner.run(alert=alert, state=state,
                                   questions=questions, input_sha256=in_sha)

        if outcome.winner == race.CLAIMED_BY_INFERENCE:
            return self._on_answered(alert, in_sha, outcome, budget_ms)
        return self._on_timer_won(alert, in_sha, outcome, budget_ms)

    def _on_answered(self, alert, in_sha, outcome, budget_ms):
        """Inference won the race: run S3–S6 through the shared kernel."""
        res = outcome.result
        if res is None or not res.ok:
            err = (res.error if res is not None
                   else RuntimeError("race: inference won with no result"))
            latency = res.latency_ms if res is not None else 0.0
            disp = Disposition(action="passthrough",
                               reason=f"error:{_error_code(err)}",
                               team=None, confidence=None, latency_ms=latency)
            self._emit(decision_made_payload(
                alert=alert, input_sha256=in_sha,
                disposition=disp.action,
                budget_outcome=race.ERROR_PASSTHROUGH, budget_ms=budget_ms,
                latency_ms=latency,
                lock_evaluation=empty_lock_evaluation()))
            return disp, _empty_answers()

        resp = res.response
        answers = resp.answers or {}
        q_sev = answers.get("severity")
        q_team = answers.get("owning_team")
        q_disp = answers.get("disposition")
        try:
            # ADR-013: compute quantized lock before policy evaluation.
            _probs = (q_sev.probabilities or {}) if q_sev else {}
            _lock = self._leg1_prob_lock(_probs.get("p1_critical"), alert, {})
            verdict = evaluate_policy(
                alert, jev_model=getattr(resp, "model", None),
                q_severity=q_sev, q_team=q_team, q_disposition=q_disp,
                thresholds=self.thresholds, allowlist=self.allowlist,
                latency_ms=res.latency_ms, prob_lock_pass=_lock)
        except JevError as exc:
            # S3 — malformed/untrustworthy answer: uncertainty pages.
            disp = Disposition(action="passthrough",
                               reason=f"error:{_error_code(exc)}",
                               team=None, confidence=None,
                               latency_ms=res.latency_ms)
            self._emit(decision_made_payload(
                alert=alert, input_sha256=in_sha,
                disposition=disp.action,
                budget_outcome=race.ERROR_PASSTHROUGH, budget_ms=budget_ms,
                jev_model=getattr(resp, "model", None),
                latency_ms=res.latency_ms,
                lock_evaluation=empty_lock_evaluation()))
            return disp, _empty_answers()

        disp = Disposition(action=verdict.action, reason=verdict.reason,
                           team=verdict.team, confidence=verdict.confidence,
                           latency_ms=verdict.latency_ms)
        # ADR-022/D8 enforcement: a frozen or expired suppression policy
        # cannot suppress, no matter what the triple lock says. Fail toward
        # the human. Never raises — an unreadable policy state is itself a
        # reason to page, not to suppress.
        if disp.action == "suppress" and self.policy_gate is not None:
            try:
                allowed, why = self.policy_gate.can_suppress(self.policy_id)
            except Exception:  # noqa: BLE001
                allowed, why = False, "policy_state_unreadable"
            if not allowed:
                disp = Disposition(action="passthrough",
                                   reason=f"policy_blocked:{why}",
                                   team=disp.team, confidence=disp.confidence,
                                   latency_ms=disp.latency_ms)
        self._emit(decision_made_payload(
            alert=alert, input_sha256=in_sha,
            disposition=disp.action,
            budget_outcome=race.ANSWERED_IN_TIME, budget_ms=budget_ms,
            jev_model=verdict.jev_model, q1_reported=verdict.q1_reported,
            q2_team=verdict.q2_team, q3_confidence=verdict.q3_confidence,
            q3_disposition=verdict.q3_disposition,
            latency_ms=res.latency_ms,
            lock_evaluation=verdict.lock_evaluation))
        return disp, {"jev_model": verdict.jev_model,
                      "q_severity": q_sev,
                      "q_team": q_team,
                      "q_disposition": q_disp}

    def _on_timer_won(self, alert, in_sha, outcome, budget_ms):
        """Timer (or the gate backstop) won: immediate passthrough.

        No lock evaluation, no waiting, no second-guessing — the
        deterministic safe default (design §4.1). The detached inference
        worker will emit the shadow_decision when it completes."""
        reason = "timer_won_shed" if outcome.shed else "timer_won"
        latency = (outcome.timer_fired_at_ms
                   if outcome.timer_fired_at_ms is not None else 0.0)
        disp = Disposition(action="passthrough", reason=reason,
                           team=None, confidence=None, latency_ms=latency)
        self._emit(decision_made_payload(
            alert=alert, input_sha256=in_sha,
            disposition=disp.action,
            budget_outcome=outcome.budget_outcome, budget_ms=budget_ms,
            latency_ms=None,  # null unless answered_in_time (schema §2.3)
            timer_fired_at_ms=outcome.timer_fired_at_ms,
            backstop_claimed=outcome.backstop_claimed,
            lock_evaluation=empty_lock_evaluation()))
        return disp, _empty_answers()

    def _on_late_answer(self, late: race.LateAnswer) -> None:
        """Detached worker's late answer → shadow_decision payload.

        Never pages, never suppresses, never re-opens the decision. Runs
        on the detached inference thread — pure + emit only, no shared
        state touched."""
        alert = late.alert
        q_sev = q_team = q_disp = None
        jev_model = None
        error_class = late.error_class
        shadow_disposition = "passthrough"
        locks = None
        if late.response is not None:
            resp = late.response
            jev_model = getattr(resp, "model", None)
            answers = resp.answers or {}
            q_sev = answers.get("severity")
            q_team = answers.get("owning_team")
            q_disp = answers.get("disposition")
            try:
                # The SAME pure policy kernel the live gate uses — not a
                # copy (design §4.2). ADR-013: quantized lock for shadow too.
                _probs2 = (q_sev.probabilities or {}) if q_sev else {}
                _lock2 = self._leg1_prob_lock(_probs2.get("p1_critical"), alert, {})
                verdict = evaluate_policy(
                    alert, jev_model=jev_model,
                    q_severity=q_sev, q_team=q_team, q_disposition=q_disp,
                    thresholds=self.thresholds, allowlist=self.allowlist,
                    latency_ms=late.latency_ms, prob_lock_pass=_lock2)
            except JevError:
                # Malformed late answer: still vendor evidence — q-fields
                # null, error_class set; never acts.
                q_sev = q_team = q_disp = None
                jev_model = None
                error_class = "malformed"
            else:
                shadow_disposition = ("suppress" if verdict.action == "suppress"
                                      else "passthrough")
                locks = verdict.lock_evaluation
        self._emit(shadow_decision_payload(
            alert=alert, input_sha256=late.input_sha256,
            episode_id=late.episode_id, jev_model=jev_model,
            q1_reported=_reported_p1(q_sev),
            q2_team=(getattr(q_team, "choice", None)
                     if q_team is not None else None),
            q3_confidence=(getattr(q_disp, "confidence", None)
                           if q_disp is not None else None),
            q3_disposition=(getattr(q_disp, "choice", None)
                            if q_disp is not None else None),
            latency_ms=late.latency_ms,  # FULL latency — vendor-tail sample
            budget_ms=late.budget_ms,
            timer_fired_at_ms=late.timer_fired_at_ms,
            shadow_disposition=shadow_disposition,
            would_have_suppressed=(shadow_disposition == "suppress"),
            lock_evaluation=locks,
            error_class=error_class))


def _empty_answers() -> dict:
    return {"jev_model": None, "q_severity": None,
            "q_team": None, "q_disposition": None}
