#!/usr/bin/env python3
"""D5 — policy impact preview (offline CLI).

"This rule change would have suppressed X of last week's pages."

Reads a window of ARCHIVED decision inputs, replays them through the
proposed policy via sentinel.advisory_d5 (which reuses
Gate._resolve_suppress_path — the live DR-26 composition, not a mirror),
and prints the deterministic delta table plus the Jev-ranked review list.

CONTROL (read-only, offline):
  * Never touches live policy, live alerts, or the gate. No network except
    the optional Jev triage call (absent TYPESAFE_API_KEY => deterministic
    triage ranking, honestly labeled).
  * Archive window defaults to 7 days and is VERIFIED against the
    retention config — the CLI refuses a window retention doesn't cover.
  * Rate-limited: 1 preview per policy draft per hour (--ledger).

Archive JSON schema (list of):
  {"alert": {"alert_id", "received_at", "fingerprint", "service", "check",
             "severity_in", "title", "source", ...},
   "inputs": {"jev_model": str,
              "q_sev": {"choice": str, "probabilities": {opt: p},
                        "confidence": float|null},   # same for q_team/q_disp
              "latency_ms": float, "prob_lock_pass": bool, ...},
   "recorded_action": "page_now"|"suppress"|...,
   "recorded_reason": str,
   "ts": "ISO-8601 archive timestamp"}

Producing these archives from the live event log is Track 3's lane (they
own the schema); this CLI consumes the documented shape above.

Policy JSON schema:
  {"name": str, "thresholds_override": {field: value},
   "allowlist_add": [fingerprint...], "allowlist_remove": [...],
   "suppress_conf_min_override": float|null}

Exit codes: 0 ok, 1 usage/config error, 2 replay aborted (nothing applied —
nothing is ever applied; the CLI only reads and reports).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

from sentinel.advisory import AdvisoryDispatcher, SpendMeter  # noqa: E402
from sentinel.advisory_d5 import (ArchiveDecisionInput, ArchiveWindowError,  # noqa: E402
                                  ProposedPolicy, RateLimitedError, d5_preview)
from sentinel.client import Answer, JevError, SystemOneClient  # noqa: E402
from sentinel.counterfactual import CounterfactualInputs  # noqa: E402
from sentinel.models import Alert, Thresholds  # noqa: E402


def _answer(raw) -> Answer | None:
    if not raw:
        return None
    return Answer(qid=str(raw.get("qid", "?")),
                  qtype=str(raw.get("qtype", "choice")),
                  choice=raw.get("choice"),
                  noul=raw.get("noul"),
                  probabilities=dict(raw.get("probabilities") or {}),
                  confidence=raw.get("confidence"))


def load_archive(path: str) -> list[ArchiveDecisionInput]:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    items = []
    for entry in raw:
        a = entry.get("alert", {})
        alert = Alert(
            alert_id=str(a.get("alert_id", "?")),
            received_at=str(a.get("received_at", "")),
            fingerprint=str(a.get("fingerprint", "")),
            service=str(a.get("service", "")),
            check=str(a.get("check", "")),
            severity_in=str(a.get("severity_in", "")),
            title=str(a.get("title", "")),
            source=str(a.get("source", "generic")),
            labels=dict(a.get("labels") or {}),
            raw=dict(a.get("raw") or {}),
        )
        i = entry.get("inputs", {})
        inputs = CounterfactualInputs(
            jev_model=i.get("jev_model"),
            q_sev=_answer(i.get("q_sev")),
            q_team=_answer(i.get("q_team")),
            q_disp=_answer(i.get("q_disp")),
            latency_ms=float(i.get("latency_ms", 0.0)),
            prob_lock_pass=bool(i.get("prob_lock_pass", False)),
            freshness_report=i.get("freshness_report"),
            allowlist_for_kernel=i.get("allowlist_for_kernel"),
            via_legacy=bool(i.get("via_legacy", False)),
            lock1_dual_attested=bool(i.get("lock1_dual_attested", False)),
        )
        items.append(ArchiveDecisionInput(
            alert=alert, inputs=inputs,
            recorded_action=str(entry.get("recorded_action", "unknown")),
            recorded_reason=str(entry.get("recorded_reason", "")),
            ts=str(entry.get("ts", ""))))
    return items


def load_policy(path: str) -> ProposedPolicy:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return ProposedPolicy(
        name=str(raw.get("name", "proposed")),
        thresholds_override=dict(raw.get("thresholds_override") or {}),
        allowlist_add=list(raw.get("allowlist_add") or []),
        allowlist_remove=list(raw.get("allowlist_remove") or []),
        suppress_conf_min_override=raw.get("suppress_conf_min_override"))


def build_dispatcher(with_triage: bool) -> AdvisoryDispatcher | None:
    if not with_triage:
        return None
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if api_key:
        client = SystemOneClient(api_key=api_key)
        decide_fn = client.decide
        model = client.model
    else:
        # Absent key: no Jev — the triage degrades to the deterministic
        # proxy ranking, honestly labeled (never fake-real).
        def decide_fn(state, questions):  # noqa: ANN001, ANN202
            raise JevError("no TYPESAFE_API_KEY — triage deterministic")
        model = None
        print("[sentinel] no TYPESAFE_API_KEY: Jev triage unavailable; "
              "deterministic ranking (honestly labeled).", file=sys.stderr)
    return AdvisoryDispatcher(decide_fn, SpendMeter(budget_usd=0.50),
                              jev_model=model)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="D5 offline policy-impact preview (read-only).")
    ap.add_argument("--archive", required=True,
                    help="JSON archive of decision inputs (schema in docstring)")
    ap.add_argument("--policy", required=True,
                    help="JSON proposed policy (schema in docstring)")
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--draft-id", default="default")
    ap.add_argument("--ledger",
                    default=os.path.expanduser(
                        "~/.cache/sentinel/d5_rate_ledger.json"),
                    help="rate-limit ledger path")
    ap.add_argument("--no-triage", action="store_true",
                    help="skip the Jev triage ranking (delta table only)")
    ap.add_argument("--json", action="store_true",
                    help="print the full report envelope as JSON")
    args = ap.parse_args(argv)

    try:
        archive = load_archive(args.archive)
        proposed = load_policy(args.policy)
    except (OSError, ValueError, KeyError) as exc:
        print(f"[sentinel] config error: {exc}", file=sys.stderr)
        return 1

    ledger = {}
    if os.path.exists(args.ledger):
        try:
            with open(args.ledger, "r", encoding="utf-8") as fh:
                ledger = json.load(fh) or {}
        except (OSError, ValueError):
            ledger = {}

    dispatcher = build_dispatcher(not args.no_triage)
    try:
        envelope = d5_preview(
            archive=archive, proposed=proposed, dispatcher=dispatcher,
            window_days=args.window_days, draft_id=args.draft_id,
            rate_ledger=ledger, with_triage=not args.no_triage)
    except ArchiveWindowError as exc:
        print(f"[sentinel] preview refused: {exc}", file=sys.stderr)
        return 2
    except RateLimitedError as exc:
        print(f"[sentinel] preview refused: {exc}", file=sys.stderr)
        return 2
    finally:
        if dispatcher is not None:
            dispatcher.close()

    try:
        os.makedirs(os.path.dirname(args.ledger), exist_ok=True)
        with open(args.ledger, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh)
    except OSError as exc:
        print(f"[sentinel] warning: could not persist ledger: {exc}",
              file=sys.stderr)

    body = envelope.get("body", {})
    if args.json:
        print(json.dumps(envelope, indent=2, default=str))
        return 0

    print(f"policy:   {body.get('policy')}")
    print(f"window:   last {body.get('window_days')}d "
          f"(evaluated {body.get('inputs_evaluated')}, "
          f"unresolvable {body.get('inputs_unresolvable')}, "
          f"out-of-window {body.get('inputs_out_of_window')})")
    print(f"headline: would have suppressed "
          f"{body.get('would_suppress_of_pages')} of "
          f"{body.get('recorded_pages')} recorded pages "
          f"(net delta {body.get('delta_suppressed'):+d})")
    print(f"  kernel newly suppress: {body.get('kernel_newly_suppress')}, "
          f"newly uncorroborated: {body.get('newly_uncorroborated')}, "
          f"newly paged: {body.get('newly_paged')}")
    print(f"  transitions: {json.dumps(body.get('by_transition') or {})}")
    print(f"review list ({body.get('review_ranked_by')}):")
    for case in body.get("review_list") or []:
        print(f"  #{case.get('review_priority')} {case.get('alert_id')} "
              f"{case.get('recorded_action')}->{case.get('replay_action')} "
              f"[{case.get('corroboration')}]")
    print(f"label: fallback={envelope.get('fallback')} "
          f"ai_generated={envelope.get('ai_generated')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
