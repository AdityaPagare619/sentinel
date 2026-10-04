"""Weekly model re-validation (ADR-015, D2) — Type 1 procedure machinery.

The vendor remap failure mode is not a one-time fix: "improved
calibration" releases arrive on the vendor's schedule, not ours. The
ratification condition (Pager): no team runs a 7-day human protocol per
vendor release — automate it as a job; the human signs the result.

What this job does, every 7 days (see docs/MODEL_REVALIDATION.md):

  1. PIN PROBE — one live Jev call through the production client path;
     asserts ``response.model == pinned_model``. A mismatch is drift,
     and drift is a page-the-human event, not a backtest input.
  2. REPLAY — the buyer's labeled incident corpus through
     ``backtest.replay_backtest`` under the EXACT frozen production config
     (model pin, thresholds version, allowlist snapshot, fit version —
     all pinned in the ledger header). The bar is the ledger's zero-bar:
     ZERO false suppressions on postmortem-confirmed real SEV1/SEV2.
  3. REPORT — a signed report dict (written as JSON). The
     ``human_signoff`` field is None until a human signs it — an UNSIGNED
     revalidation is a visibly open loop, not a quiet green checkmark.

Named owner (ADR-015 ratification condition): Pager — Sentinel SRE chief.
The job automates the evidence; the human (designated on-call SRE, per
the schedule doc) signs the result within 24 h. A missed signature is
itself paged.

Zero-evidence is a refusal, not a pass: if the corpus is empty or
unreadable, the job raises RevalidationRefused and exits loud (exit 2) —
a revalidation that ran on nothing and reported green is the theater the
honesty order forbids.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .backtest import (VALID_LABELS, BacktestIncident, replay_backtest)

#: ADR-015 ratification condition (c): the 7-day re-validation protocol is
#: a Type 1 procedure with a NAMED owner. The job automates the evidence;
#: the owner signs the result.
JOB_NAME = "weekly-model-revalidation"
JOB_OWNER = "Pager — Sentinel SRE chief"
REVALIDATION_INTERVAL_DAYS = 7

#: Env override for the labeled incident corpus (JSONL, one incident per
#: line — schema in load_corpus). Defaults to the deploy bundle's corpus.
CORPUS_ENV = "SENTINEL_REVALIDATION_CORPUS"
DEFAULT_CORPUS_PATH = "revalidation/corpus.jsonl"


class RevalidationRefused(Exception):
    """The job refuses to run or to sign: zero evidence, unreadable corpus,
    or an incident row that fails schema validation. Refusal is loud —
    the caller exits non-zero and pages the owner."""


# ------------------------------------------------------------------ corpus

def load_corpus(path: str):
    """Load the labeled incident corpus from JSONL.

    Returns (incidents, replay_map) where replay_map maps alert_id ->
    {"state", "history", "context"} — the replay payloads replay_backtest
    needs alongside each Alert. Every row is schema-validated; a bad row
    raises RevalidationRefused — the corpus is never silently filtered.
    """
    incidents: list[BacktestIncident] = []
    replay_map: dict[str, dict] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
    except OSError as exc:
        raise RevalidationRefused(
            f"corpus unreadable at {path!r}: {exc}") from exc
    if not lines:
        raise RevalidationRefused(
            f"corpus at {path!r} is empty: zero-evidence revalidation is "
            "refused — a green report on no incidents is theater")
    for lineno, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise RevalidationRefused(
                f"corpus {path}:{lineno}: invalid JSON ({exc})") from exc
        incidents.append(_incident_from_row(row, lineno, path, replay_map))
    return incidents, replay_map


def _incident_from_row(row: dict, lineno: int, path: str,
                       replay_map: dict) -> BacktestIncident:
    from .models import Alert  # local: keep import graph shallow

    def fail(why: str) -> RevalidationRefused:
        return RevalidationRefused(f"corpus {path}:{lineno}: {why}")

    if not isinstance(row, dict):
        raise fail("row is not a JSON object")
    label = row.get("label")
    if label not in VALID_LABELS:
        raise fail(f"bad label {label!r} (expected one of "
                   f"{sorted(VALID_LABELS)})")
    raw_alerts = row.get("alerts")
    if not isinstance(raw_alerts, list) or not raw_alerts:
        raise fail("incident has no alerts")
    alerts = []
    for i, entry in enumerate(raw_alerts):
        if not isinstance(entry, dict):
            raise fail(f"alerts[{i}] is not an object")
        alert_row = entry.get("alert")
        if not isinstance(alert_row, dict):
            raise fail(f"alerts[{i}]: missing 'alert' object")
        try:
            alert = Alert(**{k: v for k, v in alert_row.items()
                             if k in Alert.__dataclass_fields__})
        except TypeError as exc:
            raise fail(f"alerts[{i}]: {exc}") from exc
        alerts.append(alert)
        replay_map[alert.alert_id] = {
            "state": entry.get("state", {}),
            "history": entry.get("history", {}),
            "context": entry.get("context", {}),
        }
    try:
        return BacktestIncident(
            incident_id=str(row["incident_id"]),
            date=str(row["date"]),
            estate_severity=str(row.get("estate_severity", "")),
            severity_provenance=str(row.get("severity_provenance", "")),
            postmortem_ref=row.get("postmortem_ref"),
            human_handling=list(row.get("human_handling", [])),
            alerts=alerts,
            label=label,
            label_provenance=str(row.get("label_provenance", "")),
        )
    except KeyError as exc:
        raise fail(f"missing required field {exc}") from exc


# ------------------------------------------------------------- the job

def pin_probe(client, pinned_model: str) -> dict:
    """One live Jev call through the client; assert the pin (ADR-015).

    Returns {"pinned", "observed", "match"}. The probe is the cheapest
    falsifier in the system: a vendor remap shows up here before any
    backtest could measure it. Never swallows: a wire failure propagates
    — an unreadable probe is a job failure, not a "pass".
    """
    resp = client.decide(
        {"probe": "model-pin", "instructions": "Reply with one choice."},
        {"probe": {"type": "choice",
                   "instructions": "Choose 'ok'.",
                   "criteria": {"ok": "the probe succeeded"}}})
    observed = getattr(resp, "model", None)
    return {"pinned": pinned_model, "observed": observed,
            "match": observed == pinned_model}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_weekly_revalidation(*, client, pinned_model: str,
                            incidents: list, replay_map: dict,
                            thresholds, allowlist=(),
                            fit_store=None, freshness_monitor=None,
                            config_freeze: dict | None = None,
                            window: dict | None = None,
                            report_path: str | None = None) -> dict:
    """Run the 7-day re-validation: probe, replay, report.

    ``client`` is the production Jev client (pinned model — or the
    explicit floating opt-out, in which case the pin is still the asserted
    pin and any mismatch is drift). ``incidents``/``replay_map`` come from
    ``load_corpus``. ``fit_store``/``freshness_monitor`` wire the live
    kernel's full suppress conjunction (without them suppress is
    unreachable and the zero-bar is vacuous — pass what production runs).
    Returns the signed report dict; writes it to ``report_path`` when
    given. Raises RevalidationRefused on zero evidence — never signs an
    empty run.

    The verdict is FAIL when: the pin probe drifts, or the replay ledger
    breaks the zero-bar. A FAIL is a page-the-owner event (the caller
    exits non-zero); the report itself stays honest — it never flips a
    FAIL to "pass with caveats".
    """
    from .gate import Gate  # local: keep module import graph shallow
    from .audit import AuditLog

    if not incidents:
        raise RevalidationRefused(
            "zero incidents: zero-evidence revalidation is refused — a "
            "green report on nothing is theater (ADR-015)")
    if not pinned_model:
        raise RevalidationRefused(
            "no pinned model: re-validation without a pin is undefined — "
            "the pin probe and the replay freeze both need "
            "pinned_model_version from pinning.json")

    probe = pin_probe(client, pinned_model)
    drift_detected = not probe["match"]

    # The replay runs the LIVE gate kernel (not a copy — DR-26) under the
    # frozen config, one gate for the whole chronological replay. The
    # fit store and freshness monitor are production's: without them
    # suppress is unreachable and the zero-bar would be vacuous.
    gate = Gate(client, thresholds, allowlist or [],
                AuditLog(":memory:"), pinned_model=pinned_model,
                fit_store=fit_store, freshness_monitor=freshness_monitor)

    def decide_fn(alert):
        payload = replay_map.get(alert.alert_id, {})
        disp, _rec = gate.evaluate(
            alert,
            payload.get("state", {}),
            payload.get("history", {}),
            payload.get("context", {}))
        return disp

    ledger = replay_backtest(
        incidents, decide_fn,
        config_freeze={**(config_freeze or {}),
                       "model_pin": pinned_model,
                       "labeling_rules_check": "backtest.LABELING_RULES_VERSION"},
        window=window or {"scope": "7-day rolling re-validation"})
    gate.close()

    summary = ledger["summary"]
    zero_bar_passed = bool(summary["zero_bar_passed"])
    verdict = ("PASS" if (not drift_detected and zero_bar_passed)
               else "FAIL")

    report = {
        "job": JOB_NAME,
        "owner": JOB_OWNER,   # ADR-015 ratification condition (c)
        "ran_at": _utcnow_iso(),
        "interval_days": REVALIDATION_INTERVAL_DAYS,
        "pinned_model": pinned_model,
        "pin_probe": probe,
        "drift_detected": drift_detected,
        "ledger_summary": summary,
        "zero_bar_passed": zero_bar_passed,
        "incident_count": summary.get("incident_count", 0),
        "verdict": verdict,
        # The human signature closes the loop. None ⇒ the revalidation is
        # OPEN — an unsigned PASS is evidence awaiting sign-off, not a
        # completed procedure. The schedule doc (docs/MODEL_REVALIDATION.md)
        # owns the sign-off ritual.
        "human_signoff": None,
    }

    if report_path:
        os.makedirs(os.path.dirname(os.path.abspath(report_path)),
                    exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
            fh.write("\n")

    banner = (f"[sentinel] {JOB_NAME}: verdict={verdict} "
              f"(owner: {JOB_OWNER}) — pin={pinned_model}, "
              f"drift={'YES' if drift_detected else 'no'}, "
              f"zero_bar={'pass' if zero_bar_passed else 'BROKEN'}, "
              f"incidents={report['incident_count']}")
    sys.stderr.write(banner + "\n")
    if verdict == "FAIL":
        sys.stderr.write(
            f"[sentinel] CRITICAL: re-validation FAILED — page {JOB_OWNER}; "
            "the pin or the gate's safety case no longer holds. See "
            "docs/MODEL_REVALIDATION.md §failure.\n")
    return report
