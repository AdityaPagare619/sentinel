#!/usr/bin/env python3
"""A/B runner for Jev question variants — lane L6.

Dry-run (default, no credentials, no network):
    PYTHONPATH=src python3 research/jev-behavior/bin/ab_run.py --mode dry-run --n 50

Live (real Jev via the custom.typesafe surrogate — same credential path as
the N=100 latency campaign; raw key material never touches this process):
    PYTHONPATH=src python3 research/jev-behavior/bin/ab_run.py --mode live --n 200

Live rate is capped at --rate req/s (default 0.35, campaign-proven polite)
with jitter. Kill conditions K1/K2/K3/K5 (see sentinel.ab) abort the run;
partial results are still written.
"""

from __future__ import annotations

import argparse
import os
import random
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response

from sentinel.ab import (
    RunAborted,
    Variant,
    assert_variant_effective,
    build_evalset,
    cost_accounting,
    evalset_fingerprint,
    load_variant,
    paired_metrics,
    run_ab,
    variant_rng_seed,
    write_ab_report,
    write_manifest,
)
from sentinel.client import SystemOneClient, JevError, JevTimeout
from sentinel.evalharness import FlipMock, script_response, state_key

HOST = "api.typesafe.ai"
MODEL = "jev-1.13.0"  # pinned: the N=100 campaign's echoed version
USER_AGENT = "sentinel-ab/1.0"

# FlipMock-injected non-determinism for dry runs: the researched Jev flip
# band is 1.3–2.2%; 0.02 exercises the noise-floor machinery honestly.
DRY_RUN_FLIP_RATE = 0.02


class SurrogateSystemOneClient(SystemOneClient):
    """SystemOneClient whose transport attaches the custom.typesafe surrogate.

    The base class's api_key is never sent: _post_once is fully overridden so
    no Authorization header is ever built from it. Only hsurr:* values travel,
    and only to api.typesafe.ai. Error taxonomy (401/422/429/529/5xx) is
    inherited via _raise_for_status.
    """

    def __init__(self, **kwargs):
        super().__init__(api_key="surrogate-never-sent", **kwargs)

    def _post_once(self, url: str, data: bytes) -> dict:
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"User-Agent": USER_AGENT,
                     "Content-Type": "application/json"},
        )
        add_surrogate_to_request(req, "custom.typesafe",
                                 allowed_hosts=(HOST,))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return read_json_response(resp)
        except urllib.error.HTTPError as exc:
            self._raise_for_status(exc)
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise JevTimeout(
                    f"Jev request timed out after {self.timeout_s:.1f}s"
                ) from exc
            raise JevError(f"Jev connection failed: {reason}") from exc
        raise JevError("unreachable")  # pragma: no cover


class PacingClient:
    """Enforces a minimum interval between Jev calls (polite rate)."""

    def __init__(self, inner, min_interval_s: float, jitter_s: float = 0.4,
                 seed: int = 0):
        self.inner = inner
        self.min_interval_s = min_interval_s
        self.jitter_s = jitter_s
        self.rng = random.Random(seed)
        self._next_allowed = 0.0

    def decide(self, state, questions):
        wait = self._next_allowed - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            return self.inner.decide(state, questions)
        finally:
            self._next_allowed = (
                time.monotonic() + self.min_interval_s
                + self.rng.uniform(0.0, self.jitter_s))


def variant_paths(variants_dir: str, stems: list[str]) -> list[str]:
    paths = []
    for stem in stems:
        p = os.path.join(variants_dir, stem + ".json")
        if not os.path.isfile(p):
            raise SystemExit(f"variant spec not found: {p}")
        paths.append(p)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Jev question-variant A/B runner")
    ap.add_argument("--variants-dir",
                    default="research/jev-behavior/variants")
    ap.add_argument("--arms", default="control-v1,control-v1,q123-shuffle-v1",
                    help="comma-separated variant file stems; first two "
                         "should be control for the A1/A2 noise floor")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mode", choices=["dry-run", "live"], default="dry-run")
    ap.add_argument("--rate", type=float, default=0.35,
                    help="max live req/s (polite ceiling)")
    ap.add_argument("--report",
                    default="research/jev-behavior/ab-2026-10-04.md")
    ap.add_argument("--manifest-dir", default=None)
    args = ap.parse_args()

    stems = [s.strip() for s in args.arms.split(",") if s.strip()]
    if len(stems) < 3:
        raise SystemExit("--arms needs at least 3 entries (A1,A2,B)")
    arms = [load_variant(p) for p in variant_paths(args.variants_dir, stems)]
    print("arms:", " | ".join(f"{v.ref} sha={v.sha256[:12]}" for v in arms),
          flush=True)

    # K4 pre-live gate: B must be wire-distinct from A before spending calls.
    k4 = assert_variant_effective(arms[0], arms[2])
    print(f"K4 passed: A questions sha {k4['a_questions_sha']}, "
          f"B questions sha {k4['b_questions_sha']}", flush=True)

    items = build_evalset(args.n, args.seed)
    print(f"eval set: n={len(items)} seed={args.seed} "
          f"sha={evalset_fingerprint(items)[:16]}", flush=True)

    t_start = datetime.now(timezone.utc)
    aborted_note: str | None = None

    if args.mode == "dry-run":
        script = {}
        rng = random.Random(args.seed + 1)
        for it in items:
            script[state_key(it.state)] = script_response(
                it.alert, it.label, rng, label_noise=0.0)

        def client_factory(variant: Variant, arm_name: str):  # noqa: ANN001, ANN202
            vrng = random.Random(
                variant_rng_seed(args.seed, arm_name + "|" + variant.ref))
            return FlipMock(script=script, flip_rate=DRY_RUN_FLIP_RATE,
                            rng=vrng)

        conditions_text = (
            "DRY-RUN: scripted FlipMock answers (flip_rate=0.02 injected, "
            "seeded per arm); variant transforms are structurally applied "
            "but the mock keys answers off state fingerprints, so variant "
            "effects are zero by construction. Plumbing + statistics only.")
        jev_model_note = "jev-mock-1.0"
    else:
        min_interval = 1.0 / args.rate if args.rate > 0 else 0.0

        def client_factory(variant: Variant, arm_name: str):  # noqa: ANN001, ANN202
            inner = SurrogateSystemOneClient(model=MODEL, timeout_s=30.0,
                                             max_retries=3,
                                             retry_budget_s=2.0)
            return PacingClient(inner, min_interval_s=min_interval,
                                seed=args.seed)

        conditions_text = (
            f"LIVE: https://{HOST}/v1/systemone model={MODEL} via "
            f"custom.typesafe surrogate; fresh urllib connections; "
            f"rate <= {args.rate} req/s + jitter; per-call timeout 30 s; "
            f"retry x3 within 2 s budget on 529/5xx/timeout (no retry on "
            f"401/422; 429 aborts per K3).")
        jev_model_note = MODEL

    def progress(done: int, total: int) -> None:
        if done % 25 == 0 or done == total:
            print(f"  ... {done}/{total} alerts", flush=True)

    try:
        results, meta = run_ab(items, arms, client_factory, seed=args.seed,
                               progress=progress)
    except RunAborted as exc:
        aborted_note = str(exc)
        results = exc.partial
        print(f"RUN ABORTED: {aborted_note}", flush=True)
        meta = {"n_alerts": len(items),
                "evalset_sha": evalset_fingerprint(items),
                "arms": [{"ref": v.ref, "sha": v.sha256,
                          "description": v.description} for v in arms],
                "aborted": aborted_note}

    t_end = datetime.now(timezone.utc)
    meta["n_alerts_requested"] = args.n
    meta["seed"] = args.seed
    metrics = paired_metrics(results)  # A1/A2/B
    toks = metrics["input_tokens_per_arm"]["A1"]
    cost = cost_accounting(toks["mean"])

    conditions = {
        "date": t_start.strftime("%Y-%m-%d"),
        "mode": args.mode,
        "conditions_text": conditions_text,
        "window_utc": (f"{t_start.strftime('%H:%M:%S')}–"
                       f"{t_end.strftime('%H:%M:%S')}"),
        "rate_req_s": args.rate if args.mode == "live" else None,
        "jev_model": jev_model_note,
    }
    write_ab_report(metrics, meta, cost, args.report, conditions)

    mdir = args.manifest_dir or os.path.join(
        "research/jev-behavior/ab-runs",
        f"{t_start.strftime('%Y-%m-%dT%H%M%SZ')}-{args.mode}")
    os.makedirs(mdir, exist_ok=True)
    write_manifest(results, meta, conditions,
                   os.path.join(mdir, "manifest.json"))
    print(f"n_paired={metrics['n_paired']} "
          f"noise_flip={metrics['noise_floor']['disposition_flip_rate']:.4f} "
          f"variant_flip={metrics['variant']['disposition_flip_rate']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
