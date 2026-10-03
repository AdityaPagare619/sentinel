#!/usr/bin/env python3
"""Live flip-beat rehearsal against the integrated stack.

Runs the flip beat (1a: designed B=500ms timer-win + 1b: honest 2x re-ask
at B=2700ms) through the REAL gate + REAL Jev, appending to the LIVE
event DB that the platform API serves. The demoist runs this exact
procedure on demo night; the beats are then read through the API/UI.

Usage:
    PYTHONPATH=src python3 demo/flip_beat_live.py \\
        --db demo/storm-scenario/storm.db --seed 42

Writes only real engine output. If the Jev credential is absent, exits 2
with the on-screen banner (never mocks).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "storm-scenario"))

import storm_runner as R
from sentinel.config import Thresholds
from sentinel import race as race_mod
from sentinel.gate import Gate
from sentinel.audit import AuditLog


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    R.check_auth()

    pairs = R.generate_alerts(40, args.seed)
    flip_idx = next(i for i, (_, label) in enumerate(pairs)
                    if label["severity_true"] == "known_noise")
    flip_alert, _ = pairs[flip_idx]
    flip_state = R.build_eval_state(flip_alert)
    flip_sha = R.input_sha256(flip_state)

    allowlist = R.attested_noise_allowlist()
    log = R.EventLog(db_path=args.db)
    # The gate's internal audit.record writes go to throwaway memory (as in
    # the runner and the test suite); the enriched decision_made events go
    # to the shared log via the sink. Single EventLog, chain never diverges.
    audit = AuditLog(":memory:")
    client = R.SurrogateSystemOneClient()

    print("=" * 78)
    print("BEAT 1a — designed flip, LIVE (B=500ms, real Jev)")
    print("=" * 78)
    flip_sink = R.sink_into(log)
    flip_gate = Gate(client, Thresholds(), allowlist, audit,
                     race_config=race_mod.RaceConfig(
                         budget_ms=R.FLIP_BUDGET_MS),
                     emit=flip_sink)
    disp1, rec1 = flip_gate.evaluate(flip_alert, flip_state,
                                    {}, {"org": "demo-org"})
    seq1 = R.append_decision(flip_sink, disp1, rec1)
    b1 = R.json.loads(log.get_event(seq1)["body"])
    print(R.river_row(flip_alert, disp1, b1["budget_outcome"],
                      disp1.latency_ms))
    print(f"  seq={seq1} input_sha256={flip_sha[:16]}…")
    deadline = time.monotonic() + R.LATE_ANSWER_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(0.5)
        cand = [r for r in log.events_of_type("shadow_decision", limit=50)
                if R.json.loads(r["body"]).get("input_sha256") == flip_sha
                and R.json.loads(r["body"]).get("budget_ms")
                == R.FLIP_BUDGET_MS]
        if cand:
            sb = R.json.loads(cand[-1]["body"])
            print(f"  ↳ late answer (seq {cand[-1]['seq']}): "
                  f"shadow_disposition={sb.get('shadow_disposition')} "
                  f"would_have_suppressed={sb.get('would_have_suppressed')} "
                  f"latency={sb.get('latency_ms', 0):.0f}ms")
            break
    else:
        print("  ↳ no late answer within 40s — noted honestly.")
    flip_gate.close()
    time.sleep(R.CALL_GAP_S)

    print()
    print("=" * 78)
    print("BEAT 1b — honest re-ask, LIVE-FIRST (2x, B=2700ms, real Jev)")
    print("=" * 78)
    main_sink = R.sink_into(log)
    main_gate = Gate(client, Thresholds(), allowlist, audit,
                     race_config=race_mod.RaceConfig(
                         budget_ms=R.MAIN_BUDGET_MS),
                     emit=main_sink)
    try:
        seqs = []
        for k in range(2):
            d, rec = main_gate.evaluate(
                flip_alert, R.build_eval_state(flip_alert),
                {}, {"org": "demo-org"})
            seqs.append(R.append_decision(main_sink, d, rec))
            print(f"  ask {k + 1}: seq={seqs[-1]} {d.action} ({d.reason}) "
                  f"conf={d.confidence} latency={d.latency_ms:.0f}ms "
                  f"[LIVE]")
            time.sleep(R.CALL_GAP_S)
        # flip verdict from the log (not from memory)
        import json as _json
        decs = [log.get_event(s) for s in seqs]
        disps = [_json.loads(e["body"])["disposition"] for e in decs]
        if disps[0] == disps[1]:
            print("  → same disposition twice — the modal outcome "
                  "(flip floor 1.3–2.2%)")
        else:
            print("  → DISPOSITIONS DIFFER — a real flip, in the open")
    except Exception as exc:  # noqa: BLE001 — the beat never dies
        print(f"  → live re-ask failed ({type(exc).__name__}): see "
              f"rehearsal/flip_beat.py::render_beat for the labeled "
              f"recorded fallback / limits beat. The demo continues.")
    finally:
        main_gate.close()
    print()
    print(f"done — new rows are live on the API (seqs {seq1}, {seqs}).")


if __name__ == "__main__":
    main()
