"""Gate — Jev call + threshold policy + fail-open (§3.6, §4).

Policy table (§4), evaluated in order:
  suppress            P(p1_critical) < 0.002 AND Q3 conf >= 0.90 AND fp in allowlist
  page_now            P(p1)+P(p2) > 0.30 OR Q3 conf < 0.50 (uncertainty pages)
  page_business_hours P(p3)+P(p4) dominant AND Q3 conf >= 0.70
  passthrough         client error/timeout, Q3 = cannot_determine, else uncertain

Deterministic pre-Jev paths arrive via the optional `correlation` kwarg
(dup/change_window/storm-continuation from the Correlator) and never call Jev.

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

from .client import JevError
from .models import Alert, DecisionRecord, Disposition, Thresholds
from .questions import build_questions
from .quantized import (AllowlistEntry, FitStore, leg1_prob_lock)
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


class Gate:
    def __init__(self, client, thresholds: Thresholds,
                 allowlist, audit, shadow: bool = False,
                 *, fit_store: FitStore | None = None,
                 pinned_model: str | None = None,
                 org: str | None = None,
                 clock=None):
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
        self.audit = audit
        self.shadow = shadow
        # ADR-013 quantized prob lock (design/fixes/04-quantized-gate.md).
        self.fit_store = fit_store
        self.pinned_model = pinned_model
        self.org = org
        self.clock = clock  # () -> aware datetime; tests inject a fixed now

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
        # Deterministic pre-Jev paths — no client call.
        if correlation is not None:
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

        # The Jev path.
        t0 = time.perf_counter()
        questions = build_questions((context or {}).get("team_options"))
        resp = self.client.decide(state, questions)
        latency = (time.perf_counter() - t0) * 1000.0

        answers = resp.answers or {}
        q1 = answers.get("severity")
        q2 = answers.get("owning_team")
        q3 = answers.get("disposition")
        if q1 is None or q3 is None:
            raise JevError("Jev response missing severity/disposition answers")

        probs = q1.probabilities or {}
        p1 = float(probs.get("p1_critical", 0.0))
        p2 = float(probs.get("p2_high", 0.0))
        p3 = float(probs.get("p3_medium", 0.0))
        p4 = float(probs.get("p4_low", 0.0))
        conf3 = q3.confidence  # may be None -> uncertainty pages
        team = getattr(q2, "choice", None) if q2 is not None else None
        t = self.thresholds

        def disp(action, reason):
            return Disposition(action=action, reason=reason, team=team,
                               confidence=conf3, latency_ms=latency)

        if q3.choice == "cannot_determine":
            action = disp("passthrough", "uncertain")
        elif (conf3 is not None and conf3 >= t.suppress_conf_min
              and alert.fingerprint in self.allowlist_entries
              and self._leg1_prob_lock(probs.get("p1_critical"), alert,
                                       context)):
            # ADR-013 quantized triple lock: leg 1 is the re-derived
            # probability lock (integer-hundredths point condition AND
            # (valid fit bound OR dual attestation)); leg 2 is Q3
            # confidence; leg 3 is allowlist membership (ADR-017/019).
            action = disp("suppress", "allowlist")
        elif (p1 + p2 > t.page_p1p2_min
              or conf3 is None or conf3 < t.uncertain_conf_max):
            action = disp("page_now", "threshold")          # uncertainty pages
        elif (p3 + p4) >= 0.50 and conf3 >= t.queue_conf_min:
            action = disp("page_business_hours", "threshold")
        else:
            action = disp("passthrough", "uncertain")

        return action, {"jev_model": getattr(resp, "model", None),
                        "q_severity": q1, "q_team": q2, "q_disposition": q3}


def _empty_answers() -> dict:
    return {"jev_model": None, "q_severity": None,
            "q_team": None, "q_disposition": None}
