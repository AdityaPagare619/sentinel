#!/usr/bin/env python3
"""Flip beat — LIVE-FIRST with an honestly-labeled recorded fallback.

Course refinement (fresh-minds pre-mortem, 2026-10-03): the flip beat is
live-first, not live-or-stop.

Contract:
  1. Attempt the real Jev re-ask LIVE (bounded timeout).
  2. Live success -> mode "live": both asks shown, flip/no-flip stated, LIVE badge.
  3. Live failure (529 / 402 / network / timeout -> JevError) -> the beat
     NEVER collapses:
       a. rehearsal recording exists -> mode "recorded" with the EXACT label
          "recorded real-Jev re-ask from rehearsal <ts> — live path down right now"
       b. no recording -> mode "limits_beat": the failure itself is rendered
          as a limits beat (the outage becomes the story).
  4. HARD RULES: no network failure may kill the beat/demo; no recording is
     ever shown without the label (the label always carries the rehearsal
     timestamp, so "recorded" can never be mistaken for "live").

Auth: the real System One API via the custom.typesafe surrogate (authd) —
the same convention as demo/storm-scenario/storm_runner.py. The raw key
never touches this process.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo" / "storm-scenario"))
sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")

from dynamic_credentials import add_surrogate_to_request  # noqa: E402

from sentinel.client import JevError, SystemOneClient  # noqa: E402
from sentinel.evalharness import build_eval_state  # noqa: E402
from sentinel.questions import build_questions  # noqa: E402
from sentinel.state import input_sha256  # noqa: E402
from sentinel.synthetic import generate_alerts  # noqa: E402

from storm_runner import SurrogateSystemOneClient  # noqa: E402

HOST = "api.typesafe.ai"

# The exact fallback label. Verbatim, always — tests assert on it.
RECORDED_LABEL_TEMPLATE = (
    "recorded real-Jev re-ask from rehearsal {ts} — live path down right now"
)

LIVE_TIMEOUT_S = 15.0
FLIP_SEED = 42  # same seed as storm_runner: the first known_noise alert


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- live ask

def _ask_once(client, state, questions) -> dict:
    """One real Jev ask. Returns a JSON-safe answer summary. Raises JevError."""
    t0 = time.monotonic()
    resp = client.decide(state, questions)
    dt_ms = (time.monotonic() - t0) * 1000.0
    sev = (resp.answers or {}).get("severity")
    disp = (resp.answers or {}).get("disposition")
    return {
        "model": resp.model,
        "latency_ms": round(dt_ms, 1),
        "severity_choice": getattr(sev, "choice", None),
        "severity_confidence": getattr(sev, "confidence", None),
        "severity_probabilities": dict(getattr(sev, "probabilities", None) or {}),
        "disposition_choice": getattr(disp, "choice", None),
        "disposition_confidence": getattr(disp, "confidence", None),
    }


def live_reask(client, state, questions, timeout_s: float = LIVE_TIMEOUT_S,
               n_asks: int = 2) -> dict:
    """Ask the real Jev n_asks times on the same state. Bounded by timeout_s
    per ask (thread join — a wedged socket cannot stall the demo past this).

    Returns {"asks": [...], "flipped": bool}. Raises JevError on any failure
    (529/402/network/timeout all surface as JevError subclasses).
    """
    asks = []
    for _ in range(n_asks):
        box: dict = {}
        def _run():
            try:
                box["ok"] = _ask_once(client, state, questions)
            except Exception as exc:  # noqa: BLE001 — re-raised below as JevError
                box["err"] = exc
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout_s)
        if t.is_alive():
            raise JevError(f"live re-ask exceeded the {timeout_s:.0f}s demo bound")
        if "err" in box:
            err = box["err"]
            raise err if isinstance(err, JevError) else JevError(str(err))
        asks.append(box["ok"])
    flipped = any(a["disposition_choice"] != asks[0]["disposition_choice"]
                  for a in asks[1:])
    return {"asks": asks, "flipped": flipped}


# ------------------------------------------------------- rehearsal record

def record_rehearsal(out_dir: str | Path) -> Path:
    """Perform the live re-ask NOW and save the recording artifact.

    The artifact is what the fallback shows when the live path is down —
    always WITH the honest label (see render_beat).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pairs = generate_alerts(40, FLIP_SEED)
    flip_idx = next(i for i, (_, label) in enumerate(pairs)
                     if label["severity_true"] == "known_noise")
    alert, _ = pairs[flip_idx]
    state = build_eval_state(alert)
    questions = build_questions()
    client = SurrogateSystemOneClient()
    result = live_reask(client, state, questions)
    ts = utcnow_iso()
    recording = {
        "kind": "flip-beat-rehearsal",
        "rehearsed_at": ts,
        "input_sha256": input_sha256(state),
        "alert_summary": alert.title,
        "source": "real Jev System One (custom.typesafe surrogate auth; no raw key in process)",
        "asks": result["asks"],
        "flipped": result["flipped"],
    }
    slug = ts.replace(":", "").replace("+", "p").replace("-", "")
    path = out_dir / f"flip-beat-recording-{slug}.json"
    path.write_text(json.dumps(recording, indent=2) + "\n")
    return path


# ------------------------------------------------------------- the beat

def _failure_class(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = str(exc)
    if "529" in msg or "Overloaded" in name:
        return "529 (model overloaded)"
    if "402" in msg or "payment" in msg.lower():
        return "402 (billing/quota)"
    if "401" in msg or "Auth" in name:
        return "401 (auth)"
    if "timed out" in msg.lower() or "timeout" in msg.lower() or "exceeded" in msg:
        return "timeout"
    return f"{name} (network)"


def render_beat(state, questions, client=None,
                recording_path: str | Path | None = None,
                live_timeout_s: float = LIVE_TIMEOUT_S) -> dict:
    """Render the flip beat. LIVE-FIRST, fallback-honest. NEVER raises.

    Modes:
      live        — the live re-ask worked; show both asks + flip verdict.
      recorded    — live failed; show the rehearsal recording WITH the label.
      limits_beat — live failed and no recording exists; show the failure
                    itself as a limits beat.
    """
    try:
        if client is None:
            client = SurrogateSystemOneClient()
        result = live_reask(client, state, questions,
                            timeout_s=live_timeout_s)
        return {
            "mode": "live",
            "badge": "LIVE",
            "asks": result["asks"],
            "flipped": result["flipped"],
            "copy": ("same disposition twice — the modal outcome "
                     "(flip floor 1.3–2.2%)" if not result["flipped"] else
                     "DISPOSITIONS DIFFER — a real flip, in the open "
                     "(flip floor 1.3–2.2%)"),
        }
    except Exception as exc:  # noqa: BLE001 — the beat never dies
        failure = _failure_class(exc)
        if recording_path is not None:
            try:
                rec = json.loads(Path(recording_path).read_text())
                ts = rec["rehearsed_at"]
            except Exception:
                rec, ts = None, "unknown"
            if rec is not None:
                return {
                    "mode": "recorded",
                    "badge": "RECORDED",
                    "label": RECORDED_LABEL_TEMPLATE.format(ts=ts),
                    "rehearsed_at": ts,
                    "asks": rec["asks"],
                    "flipped": rec["flipped"],
                    "live_failure": failure,
                    "copy": ("The live re-ask failed just now "
                             f"({failure}) — so you're seeing a real Jev "
                             "re-ask captured in rehearsal, labeled as "
                             "such. The demo continues; nothing here "
                             "pretends to be live."),
                }
        # No recording (or unreadable): the failure IS the beat.
        return {
            "mode": "limits_beat",
            "badge": "LIMITS",
            "live_failure": failure,
            "copy": ("Jev is unreachable right now "
                     f"({failure}) — and that's the backup plan working: "
                     "when the model path is down, Sentinel pages on "
                     "uncertainty instead of going blind. The paging path "
                     "never depended on this call. No recording was "
                     "available, so nothing recorded is shown."),
        }


def assert_honest(render: dict) -> None:
    """The honesty gate: a recorded render without the exact label fails."""
    if render.get("mode") == "recorded":
        label = render.get("label", "")
        expected = RECORDED_LABEL_TEMPLATE.format(
            ts=render.get("rehearsed_at", ""))
        assert label == expected, f"recorded beat mislabeled: {label!r}"
        assert render.get("rehearsed_at"), "recorded beat missing rehearsed_at"
        assert render.get("asks"), "recorded beat has no asks to show"
    # No render may ever claim LIVE unless the asks just happened.
    if render.get("mode") == "live":
        assert render.get("badge") == "LIVE"
    # A recorded render must never wear the LIVE badge.
    assert not (render.get("mode") == "recorded"
                and render.get("badge") == "LIVE"), "recorded beat wears LIVE badge"


# ------------------------------------------------------- rehearsal driver

class DeadEndpointClient(SurrogateSystemOneClient):
    """Simulates a dead live path (connection refused -> JevError)."""


def _dead_client():
    c = DeadEndpointClient()
    c.base_url = "http://127.0.0.1:9"
    return c


def cmd_record(out_dir: str) -> int:
    print("flip-beat rehearsal: attempting LIVE re-ask (2 real Jev calls)…")
    try:
        path = record_rehearsal(out_dir)
    except Exception as exc:
        print(f"  LIVE RECORDING FAILED: {_failure_class(exc)}")
        print("  (the fallback path is exactly for this — run rehearse-fallback)")
        return 1
    rec = json.loads(path.read_text())
    print(f"  recorded -> {path}")
    print(f"  rehearsed_at={rec['rehearsed_at']} flipped={rec['flipped']}")
    for i, a in enumerate(rec["asks"]):
        print(f"  ask {i + 1}: disposition={a['disposition_choice']} "
              f"conf={a['disposition_confidence']} "
              f"sev={a['severity_choice']} ({a['latency_ms']:.0f}ms)")
    return 0


def cmd_rehearse_fallback(recording: str) -> int:
    """Rehearse the fallback path: dead live endpoint, recording present,
    then no recording. Asserts the honesty contract on every render."""
    pairs = generate_alerts(40, FLIP_SEED)
    flip_idx = next(i for i, (_, label) in enumerate(pairs)
                     if label["severity_true"] == "known_noise")
    alert, _ = pairs[flip_idx]
    state = build_eval_state(alert)
    questions = build_questions()
    failures = []

    # 1. Dead live path + recording present -> recorded mode, exact label.
    r1 = render_beat(state, questions, client=_dead_client(),
                     recording_path=recording)
    try:
        assert r1["mode"] == "recorded", f"expected recorded, got {r1['mode']}"
        assert_honest(r1)
        rec_ts = json.loads(Path(recording).read_text())["rehearsed_at"]
        assert rec_ts in r1["label"], "label missing rehearsal timestamp"
        assert "live path down right now" in r1["label"]
        print(f"  [PASS] dead-live + recording -> recorded mode")
        print(f"         label: {r1['label']}")
    except AssertionError as e:
        failures.append(f"recorded-mode: {e}")
        print(f"  [FAIL] recorded-mode: {e}")

    # 2. Dead live path + NO recording -> limits beat, failure shown.
    r2 = render_beat(state, questions, client=_dead_client(),
                     recording_path=None)
    try:
        assert r2["mode"] == "limits_beat", f"expected limits_beat, got {r2['mode']}"
        assert_honest(r2)
        assert r2["live_failure"], "limits beat must name the failure"
        assert "recorded" not in json.dumps(r2["asks"]) if "asks" in r2 else True
        print(f"  [PASS] dead-live, no recording -> limits_beat mode")
        print(f"         failure shown as: {r2['live_failure']}")
    except AssertionError as e:
        failures.append(f"limits-beat: {e}")
        print(f"  [FAIL] limits-beat: {e}")

    # 3. Corrupt recording path -> must degrade to limits_beat, never crash.
    r3 = render_beat(state, questions, client=_dead_client(),
                     recording_path="/nonexistent/recording.json")
    try:
        assert r3["mode"] == "limits_beat", f"expected limits_beat, got {r3['mode']}"
        assert_honest(r3)
        print(f"  [PASS] corrupt recording path -> limits_beat (no crash)")
    except AssertionError as e:
        failures.append(f"corrupt-recording: {e}")
        print(f"  [FAIL] corrupt-recording: {e}")

    # 4. Unlabeled-recording scan: no render may contain asks without a label.
    for name, r in (("r1", r1), ("r2", r2), ("r3", r3)):
        if r.get("mode") == "recorded":
            assert "label" in r and r["label"], f"{name}: asks without label"
    print(f"  [PASS] unlabeled-recording scan clean")

    if failures:
        print(f"\nREHEARSAL FAILED: {len(failures)} failure(s)")
        return 1
    print("\nREHEARSAL PASS: fallback path honest under all three failure shapes")
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Flip-beat rehearsal harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_rec = sub.add_parser("record", help="live re-ask now; save the recording")
    p_rec.add_argument("--out", default="rehearsal",
                       help="directory for the recording artifact")
    p_fb = sub.add_parser("rehearse-fallback",
                          help="rehearse the fallback path (dead live endpoint)")
    p_fb.add_argument("--recording", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "record":
        return cmd_record(args.out)
    return cmd_rehearse_fallback(args.recording)


if __name__ == "__main__":
    sys.exit(main())
