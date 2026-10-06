"""Gate — Jev call races a paging budget + threshold policy + fail-open.

Policy table (frozen §4), evaluated in order:
  suppress            P(p1_critical) < 0.002 AND Q3 conf >= 0.90 AND fp in allowlist
                      AND every freshness proof fresh (ADR-014/D1 — stale ⇒ page_now)
                      AND the corroboration leg passes (ADR-019/D5 — a suppress
                      must ALSO be corroborated by an independent signal, not
                      the same model, not the same data path; un-corroborated
                      ⇒ page_now, reason "uncorroborated" — never silent)
  page_now            P(p1)+P(p2) > 0.30 OR Q3 conf < 0.50 (uncertainty pages)
  page_business_hours P(p3)+P(p4) dominant AND Q3 conf >= 0.70
  passthrough         client error/timeout, Q3 = cannot_determine, else uncertain

ADR-014 freshness (D1): the suppress conjunction carries freshness legs
evaluated from the cached FreshnessReport (two-point discipline — V1 boot
+ V2 heartbeat; the gate reads O(1) cached booleans, never validates on
the hot path). Stale evidence vetoes suppression: the alert pages NOW
with reason ``freshness:<stale legs>``. Without a wired monitor the legs
fail closed — suppress is unreachable. Never silently approximated.

Race-to-page (ADR-010, design/fixes/01-race-to-page.md): the Jev call never
blocks the page. It races budget B on the inference pool; the timer's
default action is passthrough (deterministic, not model output); a late
answer becomes a ``shadow_decision`` payload via the late-answer hook —
it never pages, never suppresses. The ONLY Jev-call sites are the pool
submissions inside ``RaceRunner.run`` (the race — gates the disposition)
and ``RaceRunner.submit_advisory`` (detached advisory for the storm
digest — never gates anything).

Deterministic pre-Jev paths arrive via the optional `correlation` kwarg
(dup/change_window/storm-continuation from the Correlator) and never call
Jev — they emit ``structural_passthrough`` decision payloads.

Storm aggregates take ``digest_storm`` (D3, ADR-016) — a separate code
path, not a flag: it never calls the policy kernel, never arms a race,
and cannot suppress by construction (see sentinel.storm_digest).

C1 (design/rfc-c1-stepped-failopen.md) — stepped fail-open: when the
vendor path degrades (timer-win rate over the trailing window, or
absolute page-rate pressure), the gate steps down an explicit ladder
instead of falling off a cliff: step 1 = last-known-good compiled policy
(deterministic, no Jev call; freshness-gated with auto-fall to step 2),
step 2 = static severity floor (source-critical only, deduped; the rest
held for the digest), step 3 = rate-capped paging + scannable digest
(critical-first, level-shift ordered). Each step is a named,
event-logged transition (``failopen_step_entered`` {cause, at}); each
step surfaces the violet banner (cause + start time + detector-health
line). The stepped path never consults the suppress conjunction and
cannot suppress by construction — see sentinel.failopen.

The gate EMITS decision payloads (decision_made per decision,
shadow_decision for late answers); it does NOT write events — the
dispatcher/event-log lane owns persistence (see race_payloads.py's
EMISSION CONTRACT).

Invariants:
  * audit row is ALWAYS written, even on error / passthrough;
  * ANY exception from the client -> passthrough "error:<code>", NEVER raised;
  * shadow=True logs the would-be disposition (action recorded, causal reason
    carried through untouched, mode="shadow") but always returns passthrough.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
import sys
import threading
import time

from . import firewall
from . import corroboration
from . import race
from . import sim_judge
from .client import JevError
from .correlator import legacy_fingerprint_of
from .counterfactual import (CounterfactualInputs,
                             build_counterfactual_receipt)
from .failopen import (FailopenConfig, FailopenController, FailoverPolicy)
from .freshness import (LOCK1_CALIBRATION, lock1_fallback_offered,
                        suppress_precondition)
from .models import Alert, DecisionRecord, Disposition, Thresholds
from .questions import build_questions
from .quantized import (AllowlistEntry, FitStore, leg1_prob_lock)
from .race_payloads import (decision_made_payload, drift_lock_evaluation,
                            empty_lock_evaluation, lock_evaluation,
                            model_drift_payload, shadow_decision_payload)
from .state import input_sha256
from .storm_digest import (storm_digest_disposition, storm_root_cause_payload,
                           storm_root_cause_questions)

logger = logging.getLogger("sentinel.gate")

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


def _mark_legacy_resolution(verdict) -> None:
    """Name a legacy v1 fingerprint resolution in the audit trail.

    The disposition ``reason`` taxonomy is deliberately unchanged
    (``"allowlist"`` — asserted by tests); the truth lives in the lock
    detail and in the LOUD ``LEGACY-FINGERPRINT-RESOLVED`` warning log.
    """
    try:
        leg = verdict.lock_evaluation.get("allowlist")
    except AttributeError:
        return
    if isinstance(leg, dict):
        leg["detail"] = (str(leg.get("detail", ""))
                         + " [LEGACY v1 fingerprint resolved during the "
                           "ADR-017 migration window — re-attest]")


def evaluate_policy(alert, *, jev_model, q_severity, q_team, q_disposition,
                    thresholds: Thresholds, allowlist: set,
                    latency_ms: float, prob_lock_pass: bool = False,
                    freshness_report=None,
                    lock1_dual_attested: bool = False,
                    suppress_leg_enabled: bool = True,
                    suppress_conf_min_override: float | None = None
                    ) -> PolicyVerdict:
    """The pure policy kernel — the frozen §4 table, no I/O, no clocks.

    Shared by the live gate, the race's late-answer path, the shadow tap,
    and the D9 counterfactual receipt — one kernel, not a copy (DR-26;
    ADR-010 §4.2: ``shadow_disposition`` is what the gate *would* have
    decided had the answer arrived in time).

    D9/ADR-023 counterfactual axes (parameters of the kernel itself, not a
    wrapper — the receipt re-runs THIS function with different flags, so
    there is no second implementation to drift):
      suppress_leg_enabled: False removes the suppress branch — the
        definitional ``no_suppress_leg`` counterfactual (what disposition
        would this alert have received without the suppress leg?).
      suppress_conf_min_override: override the suppress confidence floor
        for this evaluation (the "what if the floor were higher?" preset).

    ADR-014: the freshness legs are part of the suppress conjunction.
    ``freshness_report`` is the cached FreshnessReport (two-point
    discipline — O(1) read, no validation on the hot path). A stale proof
    vetoes suppression: the severity/confidence evidence is distrusted
    wholesale, so the alert pages NOW (``page_now``, reason
    ``freshness:<stale legs>``) rather than queuing on distrusted
    evidence — uncertainty pages (Law 7). No report ⇒ the legs fail
    closed: suppress is unreachable without proven freshness, never
    silently approximated.

    ``lock1_dual_attested``: the lock-1 interim path (synthesis §3.1) — a
    stale calibration leg may be satisfied by dual human attestation.
    Locks 2 and 3 offer no interim path. The flag is explicit operator
    evidence carried on the decision context, never a default.

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
    # D9 counterfactual axis: the confidence floor under evaluation.
    scm = (t.suppress_conf_min if suppress_conf_min_override is None
           else suppress_conf_min_override)

    # Per-lock verdicts, evaluated at answer time. The detail strings say
    # exactly what was checked.
    # ADR-013: quantized lock replaces the naive p1 threshold.
    prob_pass = prob_lock_pass
    conf_pass = conf3 is not None and conf3 >= scm
    allow_pass = alert.fingerprint in allowlist
    locks = lock_evaluation(
        prob_pass,
        f"p1_critical={p1:.4f} {'<' if prob_pass else '>='} "
        f"suppress_p1_max={t.suppress_p1_max}",
        conf_pass,
        f"q3_confidence={conf3} "
        f"{'>=' if conf_pass else '<'} suppress_conf_min={scm}",
        allow_pass,
        ("fingerprint in allowlist" if allow_pass
         else "fingerprint NOT in allowlist"),
    )

    # ADR-014: freshness legs of the suppress conjunction. Every stale leg
    # is named in the veto reason (rot-matrix row 5: the operator sees the
    # full rot, not the first failure).
    fresh_ok, fresh_reasons = _freshness_legs(
        freshness_report, alert.fingerprint, lock1_dual_attested)

    if q3.choice == "cannot_determine":
        action, reason = "passthrough", "uncertain"
    elif suppress_leg_enabled and prob_pass and conf_pass and allow_pass:
        if fresh_ok:
            action, reason = "suppress", "allowlist"          # triple lock
        else:
            action, reason = ("page_now",
                              "freshness:" + "|".join(fresh_reasons))
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


def _freshness_legs(report, fingerprint: str | None,
                    lock1_dual_attested: bool) -> tuple[bool, list[str]]:
    """Freshness legs of the suppress conjunction (ADR-014, D1).

    Returns (ok, reasons). No report ⇒ not ok: suppress requires PROVEN
    freshness — a missing report must never silently approximate fresh
    (fail closed). Lock 1's stale leg may be satisfied through the
    dual-attestation interim path (synthesis §3.1); locks 2 and 3 have no
    interim path.
    """
    if report is None:
        return False, ["no freshness evidence: suppress requires a "
                       "FreshnessReport (ADR-014) — without one the proofs "
                       "cannot be verified"]
    ok, reasons = suppress_precondition(report, fingerprint)
    if ok:
        return True, []
    l1 = report.locks.get(LOCK1_CALIBRATION)
    if (l1 is not None and not l1.fresh and lock1_dual_attested
            and lock1_fallback_offered(report)):
        # The interim path satisfies the probability leg: drop lock 1's
        # stale reason. Any other stale leg still vetoes.
        reasons = [r for r in reasons if r != l1.reason]
    return (len(reasons) == 0), reasons


# ADR-015 (D2): the no-pin boot warning fires once per process — the gate
# is constructed once in production, but tests build many; loudness is
# preserved where it matters (the single production boot).
_PIN_WARNED = False


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
                 policy_id: str = "suppression",
                 freshness_monitor=None,
                 legacy_allowlist: dict[str, str] | None = None,
                 legacy_window_ends_at: str | None = None,
                 corroborator=None,
                 silence_floor=None,
                 attestor_registry=None,
                 # C1 (RFC design/rfc-c1-stepped-failopen.md): the stepped
                 # fail-open ladder. failopen_config: FailopenConfig (Type-2
                 # knobs; enabled by default). correlator: the Correlator
                 # instance feeding the violet banner's detector-health line
                 # (Pager P1) and the digest's level-shift z-score (M1) —
                 # None leaves the banner's detector line honestly marked
                 # unavailable. failover_policy: the last-known-good signed
                 # policy for step 1 (C5 freshness-gated); None uses the
                 # compiled-in default severity routing.
                 failopen_config: FailopenConfig | None = None,
                 correlator=None,
                 failover_policy: FailoverPolicy | None = None,
                 # Track 2 (C2): the sim Jev spend tracker. None outside
                 # sim mode (SENTINEL_SIM=1) — when None, no JudgeResult is
                 # attached to decision payloads and nothing about the
                 # decision path changes.
                 jev_tracker=None):
        """policy_gate: optional ADR-022/D8 enforcement hook with
        ``can_suppress(policy_id) -> (bool, reason)``. When present and the
        verdict is suppress, a False answer downgrades to passthrough — the
        watchdog freeze and the B3 expired state are enforced here, on the
        hot path, not in the UI. Never raises (fail toward the human).

        D5 (ADR-019) corroboration leg:
          corroborator: optional evidence provider — a callable
            ``(alert, floor, now) -> list[CorroborationEvidence]`` or an
            object with ``evidence_for(alert, floor, now)``. The engine lane
            wires this (e.g. signed resolves from the event log). Evidence
            is VERIFIED IN THE LEG, never trusted on arrival.
          silence_floor: a SilenceFloor or SilenceFloorStore (the versioned
            silence floor). A store is fresh-read on every suppress decision
            — no boot cache. None ⇒ the default floor (the leg still fails
            closed without evidence).
          attestor_registry: the sealed AttestorRegistry used to strictly
            verify ``signed_resolve`` evidence. None ⇒ signed resolves
            cannot verify ⇒ they do not corroborate (fail closed)."""
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
        # ADR-015 (D2): the hot-path pin — asserted against every Jev
        # response on the answered path. None ⇒ drift detection INACTIVE:
        # the gate cannot tell "vendor moved" from "no pin wired", so it
        # says so loudly at boot instead of pretending. Wire
        # pinning.json's pinned_model_version via build_pipeline_from_env.
        self.pinned_model = pinned_model
        if pinned_model is None:
            global _PIN_WARNED
            if not _PIN_WARNED:
                _PIN_WARNED = True
                sys.stderr.write(
                    "[sentinel] WARNING: no pinned_model wired to the gate — "
                    "model_drift detection is INACTIVE; the client-level "
                    "floating refusal still applies, but a response from an "
                    "unexpected model will not fire a model_drift event. Wire "
                    "pinning.json's pinned_model_version (ADR-015).\n")
        self.org = org
        self.clock = clock  # () -> aware datetime; tests inject a fixed now
        self.policy_gate = policy_gate
        self.policy_id = policy_id
        # ADR-017/D4 — dual-write migration window. legacy_allowlist maps
        # scheme-v1 fingerprints -> their scheme-v2 successors (written by
        # scripts/migrate_fingerprints_v1_v2.py). While the window is open,
        # an alert whose v2 fingerprint misses the allowlist may still
        # resolve via its v1 fingerprint — loudly logged. Absent/expired =>
        # no legacy resolution (fail closed). Never merged into
        # self.allowlist: the two schemes stay distinguishable so the loud
        # log and the window bound mean something.
        self._legacy_map = dict(legacy_allowlist or {})
        self._legacy_window_ends_at = legacy_window_ends_at
        self.legacy_resolutions = 0  # loud-log counter, ops-visible
        # ADR-014 (D1): the V1/V2 FreshnessMonitor whose cached report the
        # kernel reads per-alert (two-point discipline — O(1), no
        # validation on the hot path). None ⇒ the freshness legs fail
        # closed and suppress is unreachable.
        self.freshness_monitor = freshness_monitor
        # ADR-019 (D5): the corroboration leg — the final witness before
        # silence. All three are optional; the leg FAILS CLOSED when
        # evidence is absent (uncorroborated suppress ⇒ page_now).
        self.corroborator = corroborator
        self.silence_floor = silence_floor
        self.attestor_registry = attestor_registry
        # C1 — the stepped fail-open ladder (design/rfc-c1-stepped-failopen.md).
        # The controller observes every decision (vendor outcome + paging
        # action) and steps the gate down/up the ladder; the stepped branch
        # in _decide replaces the S2 race while degraded.
        self._failopen = FailopenController(
            failopen_config,
            clock=self._epoch_now,
            detector_health_fn=(correlator.detector_health
                                if correlator is not None else None),
            zscore_fn=(correlator.fingerprint_level_shift_zscore
                       if correlator is not None else None),
            failover_policy=failover_policy)
        # Track 2 (C2): sim spend accounting. The per-decision cost delta
        # is best-effort under concurrency (a shared session meter); the
        # meter itself (GET /api/v1/jev/spend) is exact.
        self._jev_tracker = jev_tracker
        self._jev_spend_lock = threading.Lock()
        self._jev_spend_mark = 0.0

    # --------------------------------- ADR-017/D4 legacy-fingerprint window

    def _legacy_window_open(self, now: _dt.datetime) -> bool:
        """True iff a legacy map is configured and its window has not lapsed.

        Unparseable window end => fail closed (no legacy resolution).
        """
        if not self._legacy_map or not self._legacy_window_ends_at:
            return False
        try:
            ends_at = _dt.datetime.fromisoformat(
                str(self._legacy_window_ends_at).replace("Z", "+00:00"))
        except ValueError:
            return False
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=_dt.timezone.utc)
        return now < ends_at

    def _gate_now(self) -> _dt.datetime:
        if self.clock:
            return self.clock()
        return _dt.datetime.now(_dt.timezone.utc)

    def _epoch_now(self) -> float:
        """Epoch seconds for the fail-open ladder. Tolerates both clock
        shapes the gate accepts: aware datetimes (production/tests) and
        raw epoch floats (helpers.FakeClock)."""
        now = self._gate_now()
        ts = getattr(now, "timestamp", None)
        return ts() if callable(ts) else float(now)

    def _note(self, vendor_outcome: str, action: str,
              now: float | None = None) -> None:
        """Feed one decision observation to the C1 fail-open ladder.

        vendor_outcome: "timer_win" | "answered" | "unhealthy_error" |
        "structural" | "failopen". Transition payloads (step_entered /
        recovered / banner / digest) are emitted on the gate's emission
        channel. Never raises: the ladder must never sink the alert."""
        try:
            t = self._epoch_now() if now is None else now
            for payload in self._failopen.observe(
                    vendor_outcome=vendor_outcome, action=action, now=t):
                self._emit(payload)
        except Exception as exc:
            print(f"[sentinel] failopen observe failed ({exc})",
                  file=sys.stderr)

    def _effective_allowlist(self, alert: Alert) -> tuple[set[str], bool]:
        """The fingerprint set for the policy kernel + whether legacy fired.

        Normal path: the v2 allowlist, unchanged. Migration-window path: the
        alert's v1 fingerprint hits the legacy map AND the alert's v2 equals
        the map's attested successor => the successor v2 is unioned into the
        kernel's set (so the pure kernel sees one set, as before) and a LOUD
        warning is logged. A different env's alert collides on the env-blind
        v1 but its v2 won't match the successor — it is NOT unioned and
        pages. The caller post-fixes the lock detail so the audit trail names
        the legacy resolution; the disposition ``reason`` taxonomy is unchanged.
        """
        if (alert.fingerprint not in self.allowlist and self._legacy_map
                and self._legacy_window_open(self._gate_now())):
            v1 = legacy_fingerprint_of(alert)
            v2 = self._legacy_map.get(v1)
            # The alert must BE the attested successor: its v2 must equal the
            # map's v2. A different env's alert collides on the env-blind v1,
            # but its v2 won't match the attested successor — it pages. This
            # is the staging→prod collision ADR-017 was built to kill
            # (Tripwire-2: unioning the alert's own v2 re-opened it).
            if v2 is not None and alert.fingerprint == v2:
                self.legacy_resolutions += 1
                logger.warning(
                    "LEGACY-FINGERPRINT-RESOLVED scheme=v1 fingerprint=%s "
                    "successor=%s alert=%s service=%s check=%s "
                    "window_ends_at=%s "
                    "(ADR-017 migration window: re-attest this entry; "
                    "legacy resolution stops at window end)",
                    v1, v2, alert.alert_id, alert.service, alert.check,
                    self._legacy_window_ends_at)
                return set(self.allowlist) | {v2}, True
        return self.allowlist, False

    def _legacy_entry_for(self, alert: Alert):
        """The AllowlistEntry backing a legacy-resolved alert (for the
        quantized leg-1 dual-attestation interim path), else None."""
        if not self._legacy_map or not self._legacy_window_open(self._gate_now()):
            return None
        v2 = self._legacy_map.get(legacy_fingerprint_of(alert))
        if v2 is None:
            return None
        return self.allowlist_entries.get(v2)

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
            self._note("unhealthy_error", "passthrough")

        if self.shadow:
            # Contract C3: the shadow path NEVER rewrites reason. The causal
            # reason from the decisioning path rides through untouched; the
            # only thing the shadow path writes is mode="shadow"
            # (Disposition.as_shadow / as_shadow_shell — any direct
            # `reason=` assignment here would raise ReasonRewriteError).
            would_be = disp
            audit_disp = would_be.as_shadow()
            disp = would_be.as_shadow_shell()
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

    def digest_storm(self, agg: Alert, agg_state: dict, *, storm_size: int,
                     storm_counts: dict) -> tuple:
        """Deterministic digest for a storm-declared aggregate (D3).

        A SEPARATE CODE PATH from evaluate(), not a flag: this method
        never calls evaluate_policy, never arms a race, never consults
        freshness or the allowlist. ``suppress`` is unreachable BY
        CONSTRUCTION — storm_digest_disposition takes no action parameter.

        The aggregate's Jev call runs detached as an advisory (root-cause
        candidates for the digest page); its answer feeds the
        ``storm_root_cause_payload`` advisory event and never gates the
        disposition. Never raises: the company-ending bug is dropping the
        storm page.
        """
        t0 = time.perf_counter()
        try:
            disp = storm_digest_disposition(storm_size=storm_size,
                                            storm_counts=storm_counts)
            self._note("structural", "page_now")
            self._emit(decision_made_payload(
                alert=agg, input_sha256=input_sha256(agg_state),
                disposition=disp.action,  # always "page_now"
                budget_outcome=race.STRUCTURAL_PASSTHROUGH,
                budget_ms=self._runner.config.budget_ms,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                lock_evaluation=empty_lock_evaluation()))
            # Advisory root-cause call: detached, best-effort, never gates.
            submitted = self._runner.submit_advisory(
                state=agg_state,
                questions=storm_root_cause_questions(),
                on_answer=lambda resp: self._on_storm_root_cause(
                    agg, storm_size, storm_counts, resp))
            if not submitted:
                print("[sentinel] storm advisory shed (pool full); "
                      "digest page unaffected", file=sys.stderr)
        except Exception as exc:  # fail open — never drop the storm page
            code = _error_code(exc)
            latency = (time.perf_counter() - t0) * 1000.0
            disp = Disposition(action="passthrough", reason=f"error:{code}",
                               team=None, confidence=None, latency_ms=latency)

        rec = DecisionRecord(
            alert=agg,
            input_sha256=input_sha256(agg_state),
            jev_model=None,
            q_severity=None,
            q_team=None,
            q_disposition=None,
            disposition=disp,
        )
        try:
            self.audit.record(rec)
        except Exception as exc:  # audit must never sink the alert
            print(f"[sentinel] audit write failed for {agg.alert_id}: {exc}",
                  file=sys.stderr)
        return disp, rec

    def _on_storm_root_cause(self, agg, storm_size, storm_counts, resp):
        """Advisory answer handler: emit enrichment, touch no disposition."""
        answers = (resp.answers or {}) if resp is not None else {}
        self._emit(storm_root_cause_payload(
            alert=agg, storm_size=storm_size, storm_counts=storm_counts,
            jev_model=getattr(resp, "model", None),
            severity_answer=answers.get("severity"),
            team_answer=answers.get("owning_team"),
            latency_ms=getattr(resp, "latency_ms", None)))

    # ------------------------------------------------- ADR-015 model drift

    def _model_drift(self, resp) -> "tuple[str, object] | None":
        """Check the hot-path pin against a Jev response (ADR-015, D2).

        Returns (expected, observed) on mismatch, None when the response
        matches the pin — or when no pin is wired (drift detection is
        inactive; the boot warning said so). A response that declares NO
        model (observed None) is a mismatch: undeclared evidence is
        untrusted evidence — uncertainty pages (Law 7).
        """
        if self.pinned_model is None:
            return None
        observed = getattr(resp, "model", None)
        if observed == self.pinned_model:
            return None
        return (self.pinned_model, observed)

    def _on_model_drift(self, alert, in_sha, outcome, budget_ms, res, resp,
                        drift: "tuple[str, object]"):
        """ADR-015: the vendor answered from a model that is not the pin.

        Fail toward the human: the answer is untrusted evidence (the
        probability mapping may have changed under fixed thresholds), so
        the verdict is passthrough — the alert PAGES, drift never
        suppresses — and the named model_drift event fires so the
        postmortem reads "vendor moved", not "lock-1 staleness". The
        decision record still carries the observed model for forensics.
        Never raises: the company-ending bug is dropping the page.
        """
        expected, observed = drift
        latency = res.latency_ms if res is not None else 0.0
        self._emit(model_drift_payload(
            alert=alert, input_sha256=in_sha,
            expected_model=expected, observed_model=observed,
            decision_phase="gate", latency_ms=latency))
        disp = Disposition(action="passthrough", reason="model_drift",
                           team=None, confidence=None, latency_ms=latency)
        self._emit(decision_made_payload(
            alert=alert, input_sha256=in_sha,
            disposition=disp.action,
            budget_outcome=race.ANSWERED_IN_TIME, budget_ms=budget_ms,
            jev_model=observed,
            latency_ms=latency,
            lock_evaluation=drift_lock_evaluation(expected, observed)))
        return disp, {"jev_model": observed, "q_severity": None,
                      "q_team": None, "q_disposition": None}

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
            # Folded into the aggregate page: no individual forward. This is
            # NOT suppression — action "folded" (D3), so a 3 AM operator never
            # reads it as model-driven suppression.
            return (Disposition(action="folded", reason="storm", team=None,
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
        if entry is None:
            # ADR-017/D4: during the migration window a legacy-resolved
            # alert's quantized leg reads the mapped v2 entry's
            # dual-attestation evidence.
            entry = self._legacy_entry_for(alert)
        try:
            ok, _detail = leg1_prob_lock(
                reported_p1, org=org, now=now,
                fit_store=self.fit_store, pinned_model=self.pinned_model,
                entry=entry)
        except Exception:
            return False
        return ok

    def _freshness_report(self):
        """Per-alert read of the cached FreshnessReport (two-point
        discipline: a pure attribute read — no validation, no clock, no
        I/O; this is the whole point).

        Returns None when no monitor is wired OR the monitor cannot serve
        (never booted). The kernel fails the freshness legs closed on None,
        so suppress is unreachable — the failure mode is noisy paging,
        never silence, never a crash.
        """
        mon = self.freshness_monitor
        if mon is None:
            return None
        try:
            return mon.current_report()
        except Exception as exc:
            print(f"[sentinel] freshness monitor unreadable ({exc}) — "
                  f"suppress unreachable until it serves", file=sys.stderr)
            return None

    # ------------------------------------------------- ADR-019/D5 corroboration

    def _corroboration_floor(self) -> "corroboration.SilenceFloor":
        """The silence floor for this decision. A store is fresh-read on
        every call (Type-1 fresh-read discipline — a floor change is
        effective on the next decision); a SilenceFloor is used as-is; None
        ⇒ the default floor. The floor can never *grant* silence — only
        evidence can."""
        sf = self.silence_floor
        if sf is None:
            return corroboration.SilenceFloor.default()
        if isinstance(sf, corroboration.SilenceFloorStore):
            return sf.current()
        return sf

    def _provider_evidence(self, alert, floor, now) -> list:
        """Evidence from the engine-wired corroborator. Never raises: a
        broken provider yields no evidence — the leg fails closed, loudly."""
        prov = self.corroborator
        if prov is None:
            return []
        try:
            if hasattr(prov, "evidence_for"):
                return list(prov.evidence_for(alert, floor, now) or [])
            return list(prov(alert, floor, now) or [])
        except Exception as exc:
            print(f"[sentinel] corroborator failed ({exc}) — evidence "
                  f"ignored; the leg fails closed", file=sys.stderr)
            return []

    def _policy_allows_suppress(self) -> tuple[bool, str]:
        """ADR-022/D8 enforcement: a frozen or expired suppression policy
        cannot suppress, no matter what the triple lock says. Fail toward
        the human. Never raises — an unreadable policy state is itself a
        reason to page, not to suppress."""
        if self.policy_gate is None:
            return True, "ok"
        try:
            return self.policy_gate.can_suppress(self.policy_id)
        except Exception:  # noqa: BLE001
            return False, "policy_state_unreadable"

    def _corroboration_leg(self, alert, jev_model=None, *,
                           drop_evidence: bool = False,
                           corro_inputs=None):
        """D5 (ADR-019): the final leg of the suppress conjunction — a
        suppress must ALSO be corroborated by an independent signal.

        Never raises: any failure fails the leg CLOSED (no corroboration),
        and the caller pages on the failure. The verdict is VISIBLE in the
        decision record (which corroboration fired, or that none did).

        Returns ``(verdict, (floor, evidences, now))`` — the resolved
        inputs are returned so the D9 counterfactual receipt can
        re-evaluate presets against byte-identical inputs without
        re-calling the evidence provider (perishable inputs: the floor is
        fresh-read per decision, evidence ages out). ``drop_evidence``
        evaluates the leg with no evidence (fails closed) — the D9
        "what if the corroborating evidence were absent?" preset.
        ``corro_inputs`` reuses a previously resolved triple instead of
        fresh-reading (no provider I/O).
        """
        try:
            if corro_inputs is not None:
                floor, evidences, now = corro_inputs
            else:
                floor = self._corroboration_floor()
                now = self._gate_now()
                evidences = self._provider_evidence(alert, floor, now)
            if drop_evidence:
                evidences = []
            verdict = corroboration.evaluate_corroboration(
                alert, evidences, floor, now=now,
                registry=self.attestor_registry,
                # drop_evidence: "what if the corroborating evidence were
                # absent?" means ALL of it — the built-in
                # allowlist_attestation derivation is evidence too. With
                # no entry the leg sees nothing and fails closed.
                allowlist_entries=({} if drop_evidence
                                    else self.allowlist_entries),
                jev_model=jev_model)
            return verdict, (floor, evidences, now)
        except Exception as exc:  # fail closed — the leg never sinks the page
            return corroboration.CorroborationVerdict(
                passed=False, kind=None, floor_version=0,
                detail=f"corroboration leg error (fail closed): {exc}"), None

    def _resolve_suppress_path(self, alert, *, jev_model, q_sev, q_team,
                               q_disp, latency_ms,
                               lock1_dual_attested: bool = False,
                               freshness_report=None,
                               prob_lock_pass: bool = False,
                               allowlist_for_kernel=None, via_legacy=False,
                               suppress_leg_enabled: bool = True,
                               suppress_conf_min_override=None,
                               drop_evidence: bool = False,
                               corro_inputs=None):
        """DR-26: the kernel, the D8 policy gate, and the D5 corroboration
        leg composed ONCE.

        The live answered path, the late-answer path, and the D9
        counterfactual receipt ALL call this — one composition, not three.
        The next policy-table change edits ``evaluate_policy``; every
        consumer moves together.

        Returns ``(action, reason, verdict, corro_verdict, corro_inputs)``.
        ``corro_verdict``/``corro_inputs`` are None unless the kernel said
        suppress AND the D8 gate allowed it (the leg never runs otherwise);
        the leg's verdict is merged into ``verdict.lock_evaluation`` so it
        stays VISIBLE in the decision record.

        The kernel may raise JevError on malformed answers (callers
        translate that to the uncertainty-pages path, as before); the D8
        hook and the leg never raise by construction.
        """
        verdict = evaluate_policy(
            alert, jev_model=jev_model,
            q_severity=q_sev, q_team=q_team, q_disposition=q_disp,
            thresholds=self.thresholds,
            allowlist=(self.allowlist if allowlist_for_kernel is None
                       else allowlist_for_kernel),
            latency_ms=latency_ms, prob_lock_pass=prob_lock_pass,
            freshness_report=freshness_report,
            lock1_dual_attested=lock1_dual_attested,
            suppress_leg_enabled=suppress_leg_enabled,
            suppress_conf_min_override=suppress_conf_min_override)
        if via_legacy:
            _mark_legacy_resolution(verdict)
        action, reason = verdict.action, verdict.reason
        corro = None
        used_inputs = None
        if action == "suppress":
            # ADR-022/D8 enforcement: a frozen or expired suppression
            # policy cannot suppress, no matter what the triple lock says.
            allowed, why = self._policy_allows_suppress()
            if not allowed:
                action, reason = "passthrough", f"policy_blocked:{why}"
            else:
                corro, used_inputs = self._corroboration_leg(
                    alert, jev_model=jev_model, drop_evidence=drop_evidence,
                    corro_inputs=corro_inputs)
                verdict.lock_evaluation["corroboration"] = \
                    corroboration.corroboration_leg_evaluation(corro)[
                        "corroboration"]
                if not corro.passed:
                    # ADR-019/D5 — un-corroborated ⇒ page_now with the
                    # explicit "uncorroborated" reason (Law 7: uncertainty
                    # pages) — never silent suppress.
                    action, reason = "page_now", "uncorroborated"
        return action, reason, verdict, corro, used_inputs

    # --------------------------------- Track 2 (C2): JudgeResult records

    def _judge_spend_delta(self) -> float:
        """USD billed since the last judge record (best-effort under
        concurrency — the shared session meter, not this delta, is the
        exact accounting). 0.0 when no tracker is wired."""
        t = self._jev_tracker
        if t is None:
            return 0.0
        with self._jev_spend_lock:
            now = t.session_usd
            delta = max(now - self._jev_spend_mark, 0.0)
            self._jev_spend_mark = now
        return round(delta, 6)

    def _client_model_label(self) -> str:
        """Honest model label for the judge record: the real pinned id,
        the FakeJev id, or the mock id — never invented."""
        return (getattr(self.client, "model", None)
                or getattr(self.client, "judge_label", None)
                or "unknown")

    def _judge_record(self, *, judgment: str, confidence: float,
                      latency_ms: float, source: str,
                      model_version: str | None = None,
                      cost_usd: float = 0.0) -> dict | None:
        """Build the contract ``JudgeResult`` for a decision payload body.

        Returns None outside sim mode (no tracker wired) — the decision
        path is then byte-identical to before. The record is
        observability, never load-bearing: a build failure logs loudly
        and yields None rather than sinking the decision.
        """
        if self._jev_tracker is None:
            return None
        try:
            return sim_judge.judge_result(
                judgment=judgment, confidence=confidence,
                latency_ms=latency_ms, source=source,
                model_version=(model_version if model_version is not None
                               else self._client_model_label()),
                cost_usd=cost_usd)
        except Exception:
            logger.warning("[sentinel] judge record build failed",
                           exc_info=True)
            return None

    def _attach_judge(self, payload: dict, record: dict | None) -> None:
        """Attach the JudgeResult to a decision payload body (additive —
        extra body keys are permitted by the event-log validator)."""
        if record is not None:
            payload["body"]["judge"] = record

    def _decide(self, alert, state, history, context, correlation):
        in_sha = input_sha256(state)
        budget_ms = self._runner.config.budget_ms

        # S1 — structural bars: deterministic, pre-race, never pay Jev for
        # a decision already made.
        structural = self._structural(alert, state, correlation)
        if structural is not None:
            disp, answers = structural
            self._note("structural", disp.action)
            self._emit(decision_made_payload(
                alert=alert, input_sha256=in_sha,
                disposition=disp.action,
                budget_outcome=race.STRUCTURAL_PASSTHROUGH,
                budget_ms=budget_ms, latency_ms=disp.latency_ms,
                lock_evaluation=empty_lock_evaluation()))
            return disp, answers

        # D6 (ADR-020) — deterministic instruction-firewall screen, phase 2.
        # Runs AFTER the S1 structural bars (the correlator's dedup /
        # storm-collapse has already folded duplicates — Pager's condition:
        # an injection storm pages once per fingerprint, not once per
        # injected alert) and BEFORE the S2 Jev race (a flagged alert never
        # arms the race: no vendor call, no Jev spend, and no model ever
        # sees the hostile text). A hit is always page_now (fail-closed);
        # the firewall never suppresses.
        hit = firewall.apply_firewall(alert)
        if hit is not None:
            disp, _ = hit.as_gate_tuple()
            payload = decision_made_payload(
                alert=alert, input_sha256=in_sha,
                disposition=disp.action,
                budget_outcome=race.STRUCTURAL_PASSTHROUGH,
                budget_ms=budget_ms, latency_ms=disp.latency_ms,
                lock_evaluation=empty_lock_evaluation())
            # Merge the firewall body keys (firewall_flagged + detectors +
            # evidence + version) into the decision_made body — the
            # event-log validator permits extra keys, so the field survives
            # to the shadow report's ASR metric.
            payload["body"].update(hit.body_extra)
            self._emit(payload)
            self._note("structural", disp.action)
            # as_gate_tuple's answers slot is None by design (no Jev
            # answers exist on this path); normalize so evaluate() always
            # gets the (Disposition, dict) shape every other path returns.
            return disp, _empty_answers()

        # C1 — stepped fail-open: while the ladder is stepped down, the
        # deterministic degraded path replaces the S2 race (no Jev call —
        # the vendor path is the thing that's degraded). S1 structural bars
        # and the D6 firewall above still run first. The suppress
        # conjunction (policy kernel → D8 → D5) is never consulted on the
        # stepped path — the step decide() methods only emit page_now /
        # page_business_hours / passthrough / folded, and FailoverPolicy
        # refuses suppress-capable severity maps.
        #
        # R15 F4 (honest): S1 dedup above CAN emit suppress while degraded
        # — inheriting a legitimate pre-degradation prior (same fingerprint,
        # same evidence, decided by the full conjunction at step 0). That
        # is not a new suppression hole: the suppress was earned before
        # the ladder stepped down, and the row carries reason="dedup" (not
        # a step mode). The "cannot suppress" guarantee is about the
        # stepped path itself, which never consults the suppress
        # conjunction and never emits suppress.
        step = self._failopen.current_step
        if step > 0:
            return self._on_failopen_step(alert, in_sha, state, step,
                                          budget_ms)

        # S2 — FAST: win the race. The pool submission inside runner.run()
        # is the only Jev-call site on the hot path.
        questions = build_questions((context or {}).get("team_options"))
        outcome = self._runner.run(alert=alert, state=state,
                                   questions=questions, input_sha256=in_sha)

        if outcome.winner == race.CLAIMED_BY_INFERENCE:
            return self._on_answered(alert, in_sha, outcome, budget_ms,
                                     context)
        return self._on_timer_won(alert, in_sha, outcome, budget_ms)

    def _on_failopen_step(self, alert, in_sha, state, step, budget_ms):
        """C1: the deterministic degraded disposition (RFC §3.2).

        No Jev call, no race, no model output — the vendor path is the
        thing that's degraded. Every row carries its step mode in-band
        (``mode: failopen_stepN``) for the console's violet banner. Never
        raises: the company-ending bug is dropping the page.

        R15: canary probes — every canary_every_n-th alert while degraded
        also runs a lightweight vendor probe. The probe's outcome feeds
        the health monitor ONLY (the stepped disposition below stands);
        it is the recovery signal that makes step-1 exit possible.
        """
        now = self._epoch_now()
        if step == 1 and not self._failopen.policy.is_fresh(now):
            # C5 auto-fall at decision time: a policy past valid_until
            # fails step 1 into step 2 + alarm — stale fallback is worse
            # than no fallback.
            for payload in self._failopen.request_step(
                    2, f"step-1 policy stale at decision time "
                       f"(valid_until past); auto-fall to severity floor "
                       f"(C5)", now):
                self._emit(payload)
            step = 2
        # Canary before the stepped disposition: the probe's latency is
        # bounded by canary_timeout_ms and it never affects the outcome.
        if self._failopen.should_canary():
            self._canary_probe(alert, state, now)
        disp = self._failopen.decide(alert, step, now)
        payload = decision_made_payload(
            alert=alert, input_sha256=in_sha,
            disposition=disp.action,
            budget_outcome="failopen_stepped",
            budget_ms=budget_ms, latency_ms=disp.latency_ms,
            lock_evaluation=empty_lock_evaluation())
        payload["body"]["mode"] = f"failopen_step{step}"
        # Track 2 (C2): no Jev call, no race — the deterministic degraded
        # disposition owns this page.
        self._attach_judge(payload, self._judge_record(
            judgment="page", confidence=1.0,
            latency_ms=disp.latency_ms or 0.0, source="deterministic",
            cost_usd=0.0))
        self._emit(payload)
        self._note("failopen", disp.action, now)
        return disp, _empty_answers()

    def _canary_probe(self, alert, state, now):
        """R15: lightweight vendor health probe while degraded.

        Runs ONE Jev call (no race, no timer competition); the outcome
        feeds the health monitor ONLY via _note — the stepped disposition
        is unaffected. Never raises: a failed probe is itself a health
        signal (unhealthy_error), not a gate error.
        """
        try:
            questions = build_questions(None)
            outcome = self._runner.probe_vendor(
                state, questions,
                timeout_ms=self._failopen.config.canary_timeout_ms)
            # "canary" is not a page action — _pages ignores it; only the
            # vendor health monitor records the sample.
            self._note(outcome, "canary", now)
        except Exception as exc:
            print(f"[sentinel] canary probe failed ({exc})",
                  file=sys.stderr)

    def _on_answered(self, alert, in_sha, outcome, budget_ms, context):
        """Inference won the race: run S3–S6 through the shared kernel."""
        res = outcome.result
        if res is None or not res.ok:
            err = (res.error if res is not None
                   else RuntimeError("race: inference won with no result"))
            latency = res.latency_ms if res is not None else 0.0
            disp = Disposition(action="passthrough",
                               reason=f"error:{_error_code(err)}",
                               team=None, confidence=None, latency_ms=latency)
            self._note("unhealthy_error", "passthrough")
            payload = decision_made_payload(
                alert=alert, input_sha256=in_sha,
                disposition=disp.action,
                budget_outcome=race.ERROR_PASSTHROUGH, budget_ms=budget_ms,
                latency_ms=latency,
                lock_evaluation=empty_lock_evaluation())
            # Track 2 (C2): the judge failed — the DETERMINISTIC fail-open
            # owns this page (source "deterministic", confidence 1.0: the
            # rule fired, no model uncertainty). A JevBudgetExhausted here
            # surfaces as reason error:budget_exhausted ("Jev budget
            # exhausted — deterministic mode").
            self._attach_judge(payload, self._judge_record(
                judgment="page", confidence=1.0, latency_ms=latency,
                source="deterministic", cost_usd=0.0))
            self._emit(payload)
            return disp, _empty_answers()

        resp = res.response
        # ADR-015 (D2): hot-path pin assertion — the FIRST thing checked
        # on every answered response. A drifted answer is untrusted
        # evidence and never reaches the policy kernel.
        drift = self._model_drift(resp)
        if drift is not None:
            return self._on_model_drift(alert, in_sha, outcome, budget_ms,
                                        res, resp, drift)
        answers = resp.answers or {}
        q_sev = answers.get("severity")
        q_team = answers.get("owning_team")
        q_disp = answers.get("disposition")
        try:
            # ADR-013: compute quantized lock before policy evaluation.
            # ADR-014 (D1): the cached freshness report gates the suppress
            # conjunction; the lock-1 interim flag is explicit operator
            # evidence carried on the decision context.
            _probs = (q_sev.probabilities or {}) if q_sev else {}
            _lock = self._leg1_prob_lock(_probs.get("p1_critical"), alert, {})
            # ADR-017/D4: the kernel always sees one set; legacy v1
            # resolution (migration window) is folded in here, loudly logged.
            allowlist_for_kernel, via_legacy = self._effective_allowlist(alert)
            freshness_report = self._freshness_report()
            lock1_dual = bool(
                (context or {}).get("lock1_dual_attested", False))
            jev_model = getattr(resp, "model", None)
            # DR-26: the kernel + the D8 policy gate + the D5 corroboration
            # leg composed once — the same composition the late-answer path
            # and the D9 counterfactual receipt call.
            (action, reason, verdict, corro, corro_inputs
             ) = self._resolve_suppress_path(
                alert, jev_model=jev_model,
                q_sev=q_sev, q_team=q_team, q_disp=q_disp,
                latency_ms=res.latency_ms, lock1_dual_attested=lock1_dual,
                freshness_report=freshness_report, prob_lock_pass=_lock,
                allowlist_for_kernel=allowlist_for_kernel,
                via_legacy=via_legacy)
        except JevError as exc:
            # S3 — malformed/untrustworthy answer: uncertainty pages.
            disp = Disposition(action="passthrough",
                               reason=f"error:{_error_code(exc)}",
                               team=None, confidence=None,
                               latency_ms=res.latency_ms)
            payload = decision_made_payload(
                alert=alert, input_sha256=in_sha,
                disposition=disp.action,
                budget_outcome=race.ERROR_PASSTHROUGH, budget_ms=budget_ms,
                jev_model=getattr(resp, "model", None),
                latency_ms=res.latency_ms,
                lock_evaluation=empty_lock_evaluation())
            # Track 2 (C2): untrustworthy answer — deterministic fail-open.
            self._attach_judge(payload, self._judge_record(
                judgment="page", confidence=1.0,
                latency_ms=res.latency_ms, source="deterministic",
                model_version=getattr(resp, "model", None),
                cost_usd=0.0))
            self._emit(payload)
            return disp, _empty_answers()

        disp = Disposition(action=action, reason=reason,
                           team=verdict.team, confidence=verdict.confidence,
                           latency_ms=verdict.latency_ms)
        # ADR-019/D5 — the leg's verdict is merged into the lock evaluation
        # AND the payload body (by _resolve_suppress_path): which
        # corroboration fired, or that none did, is VISIBLE in the
        # decision record.
        corro_body = corro.to_dict() if corro is not None else None
        # ADR-023/D9 — the counterfactual receipt: computed at event-write
        # time, on the live suppress path only, BEFORE the decision_made
        # payload is emitted. The disposition above is already final; the
        # receipt is pure and can never alter it (the builder is fully
        # wrapped — any exception becomes an error receipt, never a
        # changed disposition). Non-suppress decisions carry null — the
        # field stays null rather than pretending every decision needs
        # contrast (zero hot-path cost on page/passthrough).
        counterfactual = None
        if disp.action == "suppress":
            try:
                counterfactual = build_counterfactual_receipt(
                    self, alert,
                    CounterfactualInputs(
                        jev_model=verdict.jev_model,
                        q_sev=q_sev, q_team=q_team, q_disp=q_disp,
                        latency_ms=res.latency_ms,
                        prob_lock_pass=_lock,
                        freshness_report=freshness_report,
                        allowlist_for_kernel=allowlist_for_kernel,
                        via_legacy=via_legacy,
                        lock1_dual_attested=lock1_dual,
                        corro_floor=(corro_inputs[0]
                                     if corro_inputs is not None else None),
                        corro_evidences=(corro_inputs[1]
                                         if corro_inputs is not None else None),
                        corro_now=(corro_inputs[2]
                                   if corro_inputs is not None else None)),
                    getattr(self.thresholds, "counterfactual_presets", None)
                    or ())
            except Exception:
                # Belt and suspenders over the builder's own never-raises
                # contract: the receipt must never be able to sink, delay,
                # or alter a suppression. Loud, then null — the disposition
                # above is already final.
                logger.exception(
                    "counterfactual receipt failed for alert %s; emitting "
                    "null receipt", alert.alert_id)
                counterfactual = None
        payload = decision_made_payload(
            alert=alert, input_sha256=in_sha,
            disposition=disp.action,
            budget_outcome=race.ANSWERED_IN_TIME, budget_ms=budget_ms,
            jev_model=verdict.jev_model, q1_reported=verdict.q1_reported,
            q2_team=verdict.q2_team, q3_confidence=verdict.q3_confidence,
            q3_disposition=verdict.q3_disposition,
            latency_ms=res.latency_ms,
            lock_evaluation=verdict.lock_evaluation,
            corroboration=corro_body,
            threshold_counterfactual=counterfactual)
        # Track 2 (C2): who decided, at what cost. confidence is the
        # kernel's ordinal q3 confidence; 0.0 = "no confidence signal
        # reported" (never an invented probability). source is "jev" ONLY
        # for a real vendor client — a FakeJev "win" is deterministic
        # (never fake-real).
        real_vendor = sim_judge.is_real_vendor_client(self.client)
        self._attach_judge(payload, self._judge_record(
            judgment="suppress" if disp.action == "suppress" else "page",
            confidence=(disp.confidence
                        if disp.confidence is not None else 0.0),
            latency_ms=res.latency_ms,
            source="jev" if real_vendor else "deterministic",
            model_version=getattr(resp, "model", None),
            cost_usd=(self._judge_spend_delta()
                      if real_vendor else 0.0)))
        self._emit(payload)
        self._note("answered", disp.action)
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
        self._note("timer_win", "passthrough")
        payload = decision_made_payload(
            alert=alert, input_sha256=in_sha,
            disposition=disp.action,
            budget_outcome=outcome.budget_outcome, budget_ms=budget_ms,
            latency_ms=None,  # null unless answered_in_time (schema §2.3)
            timer_fired_at_ms=outcome.timer_fired_at_ms,
            backstop_claimed=outcome.backstop_claimed,
            lock_evaluation=empty_lock_evaluation())
        # Track 2 (C2): the timer won — the page happened BECAUSE the judge
        # was too slow (source "timer"). The late Jev answer is powerless:
        # it becomes shadow evidence inside the race, never an override.
        self._attach_judge(payload, self._judge_record(
            judgment="page", confidence=1.0, latency_ms=latency,
            source="timer", cost_usd=0.0))
        self._emit(payload)
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
            # ADR-015 (D2): drift is checked on the late path too — the
            # decision already went to passthrough (the timer won), so a
            # drifted late answer cannot re-open it, but it IS vendor
            # evidence and the named event must still fire: the postmortem
            # needs "vendor moved" distinguished from "we forgot to
            # re-fit" on every path, not just the answered one.
            drift = self._model_drift(resp)
            if drift is not None:
                expected, observed = drift
                self._emit(model_drift_payload(
                    alert=alert, input_sha256=late.input_sha256,
                    expected_model=expected, observed_model=observed,
                    decision_phase="late_answer",
                    latency_ms=late.latency_ms))
                self._emit(shadow_decision_payload(
                    alert=alert, input_sha256=late.input_sha256,
                    episode_id=late.episode_id, jev_model=observed,
                    latency_ms=late.latency_ms,
                    budget_ms=late.budget_ms,
                    timer_fired_at_ms=late.timer_fired_at_ms,
                    shadow_disposition="passthrough",
                    would_have_suppressed=False,
                    lock_evaluation=drift_lock_evaluation(expected, observed),
                    error_class="model_drift"))
                return
            jev_model = getattr(resp, "model", None)
            answers = resp.answers or {}
            q_sev = answers.get("severity")
            q_team = answers.get("owning_team")
            q_disp = answers.get("disposition")
            try:
                # DR-26: the SAME composition the live gate uses — the
                # kernel + the D8 policy gate + the D5 corroboration leg,
                # composed once in _resolve_suppress_path (not a copy, and
                # not the old hand re-composition that skipped the D8
                # policy gate: under a frozen/expired policy the live gate
                # pages while a gate-less shadow would have said "would
                # have suppressed" — a lying mirror in the over-trust
                # direction). ADR-013: quantized lock for shadow too.
                # ADR-014 (D1): the same cached freshness report, so the
                # counterfactual answers what the gate *would* have decided
                # with the proofs live at decision time. The lock-1 interim
                # cannot apply here: the live decision already went to
                # passthrough (the timer won), so there is no interim
                # attestation in play — passing False is the honest input.
                _probs2 = (q_sev.probabilities or {}) if q_sev else {}
                _lock2 = self._leg1_prob_lock(_probs2.get("p1_critical"), alert, {})
                # Same effective allowlist as the live path: the
                # counterfactual answers what the gate *would* have decided.
                allowlist_for_kernel2, via_legacy2 = self._effective_allowlist(alert)
                (action2, _reason2, verdict, _corro2, _inputs2
                 ) = self._resolve_suppress_path(
                    alert, jev_model=jev_model,
                    q_sev=q_sev, q_team=q_team, q_disp=q_disp,
                    latency_ms=late.latency_ms, lock1_dual_attested=False,
                    freshness_report=self._freshness_report(),
                    prob_lock_pass=_lock2,
                    allowlist_for_kernel=allowlist_for_kernel2,
                    via_legacy=via_legacy2)
            except JevError:
                # Malformed late answer: still vendor evidence — q-fields
                # null, error_class set; never acts.
                q_sev = q_team = q_disp = None
                jev_model = None
                error_class = "malformed"
            else:
                # The composed action mapped to the shadow vocabulary:
                # suppress stays suppress, anything else becomes
                # passthrough — preserving the shadow_decision payload
                # contract. The corroboration leg's verdict rides in the
                # lock evaluation (merged by _resolve_suppress_path) so
                # the shadow report shows which corroboration fired (or
                # none).
                locks = verdict.lock_evaluation
                shadow_disposition = ("suppress" if action2 == "suppress"
                                      else "passthrough")
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
