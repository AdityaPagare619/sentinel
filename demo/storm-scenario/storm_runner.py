#!/usr/bin/env python3
"""Sentinel Sunday demo — synthetic storm driving the REAL engine.

NO-FAKE CONTRACT (Aditya's law, enforced by this script):
  * Synthetic ALERT TRAFFIC only (a demo is not production data).
  * Every DECISION is computed live by the real Gate + real race-to-page,
    answered by the REAL Jev System One API (custom.typesafe surrogate auth —
    the same convention as research/jev-behavior/bin/latency_campaign.py).
    The raw key never touches this process.
  * If auth is unavailable the script STOPS and says so on screen — it NEVER
    silently falls back to the mock client. (grep: MockSystemOneClient is not
    imported anywhere in this file.)
  * Every number in the report is a projection over the append-only event log
    written by the real engine. detect_flips() re-derives flips from the log.

Usage:
    PYTHONPATH=src python3 demo/storm-scenario/storm_runner.py \\
        --n 40 --seed 42 --db demo/storm-scenario/storm.db \\
        --out demo/storm-scenario

The designed flip (beat 1): the first known_noise alert is run through a gate
with B=500ms — below Jev's measured p50 — so the timer wins on the merits and
the late real Jev answer lands as a shadow_decision. Then the same alert is run
twice through the B=2700ms gate; both dispositions are presented honestly
(the 1.3–2.2% flip floor is the story, not a bug to hide).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")

from dynamic_credentials import (
    DynamicCredentialError,
    add_surrogate_to_request,
    dynamic_credential_entry,
    read_json_response,
)

from sentinel.audit import AuditLog
from sentinel.client import JevError, SystemOneClient
from sentinel.config import Thresholds
from sentinel.eventlog import EventLog, detect_flips
from sentinel.evalharness import build_eval_state
from sentinel.gate import Gate
from sentinel.quantized import AllowlistEntry, Attestation
from sentinel.state import input_sha256
from sentinel import race as race_mod
from sentinel.synthetic import generate_alerts, _NOISE_TEMPLATES

HOST = "api.typesafe.ai"
FLIP_BUDGET_MS = 500          # designed-flip gate: below Jev's measured p50
MAIN_BUDGET_MS = 2700         # Oracle N=100 re-derivation (PR #19)
CALL_GAP_S = 2.0              # polite spacing, far under the 40 req/s bound
LATE_ANSWER_WAIT_S = 40.0     # socket timeout bounds the detached thread

NO_KEY_BANNER = """
╔══════════════════════════════════════════════════════════════════════╗
║  SENTINEL DEMO — STOPPED: no TypeSafe credential available             ║
║                                                                       ║
║  The demo refuses to run on a mock engine. The custom.typesafe        ║
║  connector returned no surrogate, so there is no real Jev to call.    ║
║  Nothing was decided; nothing was recorded.                           ║
║                                                                       ║
║  To continue: reconnect the custom.typesafe credential, then re-run.  ║
╚══════════════════════════════════════════════════════════════════════╝
"""


class SurrogateSystemOneClient(SystemOneClient):
    """The real System One client, real wire — auth via authd surrogate only.

    Same convention as research/jev-behavior/bin/latency_campaign.py:
    only hsurr:* values are sent, and only to api.typesafe.ai.
    The constructor's api_key arg is a non-empty placeholder the transport
    never uses; _post_once is overridden wholesale.
    """

    def __init__(self, **kwargs):
        super().__init__(api_key="surrogate-auth-via-authd", **kwargs)

    def _post_once(self, url, data):
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "User-Agent": "sentinel-sunday-demo/1.0",
                "Content-Type": "application/json",
            },
        )
        add_surrogate_to_request(req, "custom.typesafe", allowed_hosts=[HOST])
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return read_json_response(resp)
        except urllib.error.HTTPError as exc:
            self._raise_for_status(exc)
        except urllib.error.URLError as exc:
            raise JevError(f"Jev connection failed: {exc.reason}") from exc


def check_auth() -> None:
    """Fail LOUD when the real credential is absent. Never mock."""
    try:
        dynamic_credential_entry("custom.typesafe")
    except DynamicCredentialError as exc:
        print(NO_KEY_BANNER)
        print(f"(authd detail: {exc})")
        sys.exit(2)


def attested_noise_allowlist() -> list[AllowlistEntry]:
    """Dual-attested allowlist entries for the 10 known-noise fingerprints.

    Distinct attestors, both distinct from the entry author, fresh TTL,
    run-id-shaped evidence refs — the honest interim of quantized.py §2.3.
    These are DEMO attestations, labeled as such; in production the
    attestation tuples come from the linkage-query pipeline.
    """
    now = datetime.now(timezone.utc)
    entries = []
    for i, (svc, chk, sev, reg) in enumerate(_NOISE_TEMPLATES):
        import hashlib
        fp = hashlib.sha256(
            f"{svc}|{chk}|{sev}|{reg}".encode()).hexdigest()[:16]
        entries.append(AllowlistEntry(
            fingerprint=fp,
            author=f"demo-onboarding-{i:02d}",
            attestations=[
                Attestation(
                    attestor_id=f"demo-sre-alpha-{i:02d}",
                    attested_at=now - timedelta(days=3),
                    evidence_ref=f"linkageq.demo.2026-10-{1 + (i % 2):02d}.{1000 + i}",
                    ttl_days=30,
                ),
                Attestation(
                    attestor_id=f"demo-sre-beta-{i:02d}",
                    attested_at=now - timedelta(days=2),
                    evidence_ref=f"linkageq.demo.2026-10-{1 + (i % 2):02d}.{2000 + i}",
                    ttl_days=30,
                ),
            ],
        ))
    return entries


def sink_into(log: EventLog):
    """emit= callback: gate payloads -> append-only event log.

    Returns the sink function; the function's ``payloads`` attribute holds
    every emitted envelope in order (for reading the real budget_outcome).
    """
    payloads: list[dict] = []

    def _emit(payload: dict) -> None:
        payloads.append(payload)
        log.append_event(
            payload["type"],
            actor=payload.get("actor", "engine"),
            alert_id=payload["alert_id"],
            fingerprint=payload["fingerprint"],
            episode_id=payload.get("episode_id") or "",
            outbox_id=payload.get("outbox_id"),
            body=payload["body"],
            ts=payload.get("ts"),
        )
    _emit.payloads = payloads
    return _emit


def river_row(alert, disp, budget_outcome: str, latency_ms) -> str:
    kind = (alert.raw or {}).get("generator", "")
    chip = {"page_now": "PAGE", "suppress": "SUPPRESS",
            "page_business_hours": "QUEUE",
            "passthrough": "PASS-THRU"}.get(disp.action, disp.action)
    lat = f"{latency_ms:7.0f}ms" if latency_ms else "   timer"
    return (f"▮ {alert.received_at[11:19]} · {alert.service:16s} · "
            f"{chip:9s} · {disp.reason:14s} · {budget_outcome:18s} · {lat} · {kind}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--db", required=True, help="EventLog sqlite path")
    ap.add_argument("--out", required=True, help="Report/output directory")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    check_auth()

    pairs = generate_alerts(args.n, args.seed)
    # The designed flip alert: the first known_noise alert in the storm.
    flip_idx = next(i for i, (_, label) in enumerate(pairs)
                    if label["severity_true"] == "known_noise")

    allowlist = attested_noise_allowlist()
    log = EventLog(db_path=args.db)
    audit = AuditLog(":memory:")
    client = SurrogateSystemOneClient()

    # -- Beat 1a: the designed flip — B=500ms gate on the flip alert --------
    print("=" * 78)
    print("BEAT 1a — the designed flip (real engine, real Jev, B=500ms budget)")
    print("=" * 78)
    flip_alert, _flip_label = pairs[flip_idx]
    flip_state = build_eval_state(flip_alert)
    flip_sha = input_sha256(flip_state)
    flip_sink = sink_into(log)
    flip_gate = Gate(client, Thresholds(), allowlist, audit,
                     race_config=race_mod.RaceConfig(budget_ms=FLIP_BUDGET_MS),
                     emit=flip_sink)
    t0 = time.monotonic()
    disp1, _ = flip_gate.evaluate(flip_alert, flip_state,
                                  {}, {"org": "demo-org"})
    row1 = next(p for p in reversed(flip_sink.payloads)
                if p["type"] == "decision_made"
                and p["alert_id"] == flip_alert.alert_id)
    print(river_row(flip_alert, disp1, row1["body"]["budget_outcome"],
                    disp1.latency_ms))
    # Wait for the detached late answer (real Jev, real shadow decision).
    deadline = time.monotonic() + LATE_ANSWER_WAIT_S
    shadow = None
    while time.monotonic() < deadline:
        time.sleep(0.5)
        # shadow decisions arrive via the same emit sink; re-read the log.
        cand = [r for r in log.events_of_type("shadow_decision", limit=100)
                if json.loads(r["body"]).get("input_sha256") == flip_sha]
        if cand:
            shadow = cand
            break
    if shadow:
        body = json.loads(shadow[-1]["body"])
        print(f"  ↳ late answer landed: shadow_disposition="
              f"{body.get('shadow_disposition')} "
              f"would_have_suppressed={body.get('would_have_suppressed')} "
              f"(full vendor latency {body.get('latency_ms', 0):.0f}ms — the tail, sampled)")
    else:
        print("  ↳ no late answer within 40s — noted honestly; the tail won that round too.")
    flip_gate.close()
    time.sleep(CALL_GAP_S)

    # -- Beat 1b: the honest re-ask — same alert, B=2700, twice -------------
    print()
    print("=" * 78)
    print("BEAT 1b — the honest re-ask (same alert, real gate, B=2700ms, twice)")
    print("=" * 78)
    main_sink = sink_into(log)
    main_gate = Gate(client, Thresholds(), allowlist, audit,
                     race_config=race_mod.RaceConfig(budget_ms=MAIN_BUDGET_MS),
                     emit=main_sink)
    reask = []
    for k in range(2):
        d, _ = main_gate.evaluate(flip_alert, build_eval_state(flip_alert),
                                  {}, {"org": "demo-org"})
        reask.append(d)
        print(f"  ask {k + 1}: {d.action} ({d.reason}) "
              f"conf={d.confidence} latency={d.latency_ms:.0f}ms")
        time.sleep(CALL_GAP_S)
    same = reask[0].action == reask[1].action
    print(f"  → {'same disposition twice — the modal outcome' if same else 'DISPOSITIONS DIFFER — a real flip, in the open'} "
          f"(measured flip floor 1.3–2.2%)")

    # -- The storm: every remaining alert through the real gate -------------
    print()
    print("=" * 78)
    print(f"THE STORM — {args.n - 1} alerts, real engine, real Jev (seed={args.seed})")
    print("=" * 78)
    storm = [(a, l) for i, (a, l) in enumerate(pairs) if i != flip_idx]
    for i, (alert, label) in enumerate(storm):
        disp, _ = main_gate.evaluate(alert, build_eval_state(alert),
                                     {}, {"org": "demo-org"})
        # The REAL budget outcome, off the emitted decision_made envelope —
        # never assumed.
        row = next(p for p in reversed(main_sink.payloads)
                   if p["type"] == "decision_made"
                   and p["alert_id"] == alert.alert_id)
        print(river_row(alert, disp, row["body"]["budget_outcome"],
                        disp.latency_ms))
        time.sleep(CALL_GAP_S)
    main_gate.close()

    # -- The receipts: fold the real event log ------------------------------
    head_seq, head_hash = log.head()
    events = [log.get_event(s) for s in range(1, head_seq + 1)]
    by_type: dict[str, int] = {}
    disps: dict[str, int] = {}
    budgets: dict[str, int] = {}
    suppress_rows = []
    latencies = []
    models = set()
    for e in events:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        if e["type"] == "decision_made":
            b = json.loads(e["body"])
            disps[b["disposition"]] = disps.get(b["disposition"], 0) + 1
            budgets[b.get("budget_outcome", "?")] = budgets.get(b.get("budget_outcome", "?"), 0) + 1
            if b.get("latency_ms"):
                latencies.append(b["latency_ms"])
            if b.get("jev_model"):
                models.add(b["jev_model"])
            if b["disposition"] == "suppress":
                suppress_rows.append((e, b))

    flips = detect_flips(events)
    disp_flips = [f for f in flips if f["differing_field"] == "disposition"]
    q_wobbles = [f for f in flips if f["differing_field"] != "disposition"]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "real Gate + race-to-page, B=2700ms (flip beat B=500ms)",
        "jev": {"model": sorted(models), "auth": "custom.typesafe surrogate (no raw key in process)"},
        "traffic": {"synthetic": True, "n": args.n, "seed": args.seed,
                    "note": "synthetic data, REAL engine — every number traces to the event log"},
        "decisions": disps,
        "budget_outcomes": budgets,
        "latency_ms": {"n": len(latencies),
                       "min": min(latencies) if latencies else None,
                       "p50": sorted(latencies)[len(latencies)//2] if latencies else None,
                       "max": max(latencies) if latencies else None},
        "suppressions": len(suppress_rows),
        "flips_detected": flips,
        "disposition_flips": len(disp_flips),
        "confidence_wobbles": len(q_wobbles),
        "event_types": by_type,
        "head_hash": head_hash,
        "no_key_fallback": "none — the script exits 2 if auth is absent (no mock path)",
    }
    (out / "storm-report.json").write_text(json.dumps(report, indent=2))
    (out / "event-log.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n")

    print()
    print("=" * 78)
    print("RECEIPTS — every number traces to the append-only event log")
    print("=" * 78)
    print(json.dumps(report, indent=2))
    print(f"\nwrote: {out/'storm-report.json'}  {out/'event-log.jsonl'}  db: {args.db}")


if __name__ == "__main__":
    main()
