"""LOAD-TEST lane — chunked alert-stream generator.

Reuses sim_runner's arrival emitters and mix semantics (same _EMITTERS,
same per-kind event shapes), but generates in time-chunks so a millions
run never materializes the whole stream at once.

Determinism: chunk i derives its RNG from (master_seed, chunk_idx); the
same seed always yields the same stream for a given profile set. (The
stream intentionally differs from sim_runner's unchunked generate_events
— chunking changes RNG consumption order. The load test defines its own
canonical stream; what matters is seed -> stream reproducibility.)

Profiles mirror the sim scenarios' mix semantics:
  baseline : steady, noise-heavy background (like normal-day)
  bad_deploy: deploy churn + elevated sev (like bad-deploy)
  incident : cascade + flap (like infra-incident)
  storm    : storm_burst (like storm-surge)
"""
from __future__ import annotations

import os
import random
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO_ROOT, "platform", "server", "sim"))

import sim_runner as sim  # noqa: E402


def _clip_profile(profile: dict, start_s: float, end_s: float) -> dict | None:
    ps = float(profile.get("start_s", 0))
    # Some emitters (storm_burst) define their window via window_s instead
    # of end_s — derive the effective end accordingly.
    pe = float(profile.get("end_s",
                           ps + float(profile.get("window_s", 0))))
    cs, ce = max(ps, start_s), min(pe, end_s)
    if ce <= cs:
        return None
    p = dict(profile)
    p["start_s"], p["end_s"] = cs, ce
    # For window-based emitters, shrink the window and scale the alert
    # count proportionally so a clipped chunk emits the right share.
    if "window_s" in p and "alerts" in p:
        full_window = float(profile.get("window_s", 0))
        if full_window > 0:
            frac = (ce - cs) / (pe - ps) if (pe - ps) > 0 else 1.0
            p["window_s"] = ce - cs
            p["alerts"] = max(1, int(int(profile["alerts"]) * frac))
    return p


def generate_chunk(profiles: list[dict], chunk_idx: int, master_seed: int,
                   chunk_start_s: float, chunk_end_s: float,
                   seq_offset: int = 0):
    """Generate one time-chunk of events. Returns (events, next_seq)."""
    rng = random.Random(f"{master_seed}:chunk:{chunk_idx}")
    events: list[dict] = []
    seq = seq_offset
    for profile in profiles:
        clipped = _clip_profile(profile, chunk_start_s, chunk_end_s)
        if clipped is None:
            continue
        evs, seq = sim._EMITTERS[clipped["kind"]](rng, clipped, seq)
        events.extend(evs)
    events.sort(key=lambda e: (e["offset_s"], e["seq"]))
    return events, seq


def build_chunk_manifest(name: str, seed: int, start_epoch: float,
                         profiles: list[dict], version: int = 1) -> dict:
    return {"name": name, "seed": seed, "_start_epoch": start_epoch,
            "version": version, "profiles": profiles}


def build_alerts_for_chunk(events, manifest) -> list:
    # sim.build_alerts derives alert_ids from enumerate() — for chunked runs
    # we need globally unique ids, so we rewrite them after building.
    return sim.build_alerts(events, manifest)


def load_profiles() -> dict:
    """Load-test profile library (mirrors sim scenario mix semantics)."""
    return {
        # Steady background: the millions/day bulk.
        "baseline": {"kind": "steady", "name": "baseline",
                     "start_s": 0, "end_s": 86400,
                     "rate_per_min": 694.0,
                     "mix": {"noise": 0.55, "warning": 0.30,
                             "sev": 0.10, "deploy": 0.05}},
        # Bad deploy window: elevated sev + deploy mix for 2h (steady emitter
        # with shifted mix — the volume semantics that matter at scale).
        "bad_deploy": {"kind": "steady", "name": "bad_deploy",
                       "start_s": 36000, "end_s": 43200,
                       "rate_per_min": 900.0,
                       "mix": {"noise": 0.30, "warning": 0.30,
                               "sev": 0.25, "deploy": 0.15}},
        # Infra incident: sev-heavy hour.
        "incident": {"kind": "steady", "name": "incident",
                     "start_s": 57600, "end_s": 61200,
                     "rate_per_min": 1500.0,
                     "mix": {"noise": 0.20, "warning": 0.30,
                             "sev": 0.40, "deploy": 0.10}},
        # Storm surge: 15-minute burst at extreme rate.
        "storm": {"kind": "storm_burst", "name": "storm",
                  "start_s": 72000, "window_s": 900,
                  "alerts": 60000, "distinct_fingerprints": 20000,
                  "severity_in": ["warning", "critical"]},
    }
