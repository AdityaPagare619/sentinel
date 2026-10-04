"""Seeded synthetic alert generator — ARCHITECTURE.md §6.

Generates labeled alert fixtures from a seeded mixture so the tuner and the
eval harness can run with zero external dependencies:

  * ``known_noise`` flaps — auto-clearing, historically never a SEV
    (the only alerts eligible for suppression; they form the allowlist)
  * deploy-adjacent spikes — gray zone, mostly p3, ~5% become real SEV2s
  * p3/p4 warnings — routine, never page at night
  * real SEV1/2s — ~5% of the mix, always became_sev12

``generate_alerts(n, seed)`` -> ``list[tuple[Alert, outcome_label]]``.
Deterministic: same ``(n, seed)`` -> byte-identical output.

``script_answers(alert, label, rng, label_noise)`` produces the Jev-style
answer triple the harness scripts into ``MockSystemOneClient``: calibrated-ish
Q1 probabilities (rounded to 0.01 like the real API), a Q2 team choice and a
Q3 disposition choice with confidence. ``label_noise`` is the probability that
the scripted argmax is deliberately wrong — it simulates Jev misreads.

CLI (emits tuner-ready labels.jsonl):
    PYTHONPATH=src python -m sentinel.synthetic --n 2000 --seed 7 -o labels.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import datetime, timedelta, timezone

from sentinel.models import Alert

SEVERITY_OPTIONS = ("p1_critical", "p2_high", "p3_medium", "p4_low",
                    "known_noise", "cannot_determine")

SERVICE_TEAMS = {
    "checkout-api": "product_backend",
    "payments-svc": "product_backend",
    "auth-gateway": "security",
    "search-api": "product_backend",
    "redis-cache": "data",
    "postgres-primary": "data",
    "kafka-ingest": "data",
    "cdn-edge": "network",
    "k8s-platform": "platform",
    "ci-runners": "platform",
}

_CHECKS = ("error_rate", "p99_latency", "cpu_util", "conn_pool",
           "queue_depth", "http_5xx", "disk_io", "tls_cert_expiry")
_REGIONS = ("us-east-1", "eu-west-1", "ap-south-1")
_BASE_TIME = datetime(2026, 9, 1, tzinfo=timezone.utc)

# Base Q1 probability templates, keyed by ground-truth severity.
# Chosen so the §5 policy behaves correctly on clean (label_noise=0) scripts:
#   noise  -> P(p1) < 0.002, suppressible via the triple lock
#   p4/p3  -> queued (P(p1)+P(p2) <= 0.30, P(p3)+P(p4) dominant)
#   p2/p1  -> page_now (P(p1)+P(p2) > 0.30)
_BASE_Q1_PROBS: dict[str, dict[str, float]] = {
    "p1_critical": {"p1_critical": 0.86, "p2_high": 0.09, "p3_medium": 0.03,
                    "p4_low": 0.01, "known_noise": 0.005, "cannot_determine": 0.005},
    "p2_high": {"p1_critical": 0.10, "p2_high": 0.82, "p3_medium": 0.05,
                "p4_low": 0.015, "known_noise": 0.01, "cannot_determine": 0.005},
    "p3_medium": {"p1_critical": 0.004, "p2_high": 0.08, "p3_medium": 0.80,
                  "p4_low": 0.07, "known_noise": 0.04, "cannot_determine": 0.006},
    "p4_low": {"p1_critical": 0.004, "p2_high": 0.01, "p3_medium": 0.08,
               "p4_low": 0.84, "known_noise": 0.05, "cannot_determine": 0.006},
    "known_noise": {"p1_critical": 0.0009, "p2_high": 0.004, "p3_medium": 0.02,
                    "p4_low": 0.04, "known_noise": 0.93, "cannot_determine": 0.0051},
}


def fingerprint_of(service: str, check: str, severity_in: str, region: str,
                   env: str = "prod", cluster: str | None = None) -> str:
    """Scheme-v2 fingerprint (ADR-017): env+cluster are in the hash input.

    Defaults mirror the synthetic estate (env=prod, cluster=<region>-a) so
    generated history accumulates per estate-qualified fingerprint.
    """
    if cluster is None:
        cluster = f"{region}-a"
    return hashlib.sha256(
        f"{service}|{check}|{severity_in}|{region}|{env}|{cluster}".encode("utf-8")
    ).hexdigest()[:16]


# Fixed fingerprint pools so history actually accumulates per fingerprint
# (this is what makes the tuner's per-fingerprint allowlist heuristic work).
_NOISE_TEMPLATES = [
    ("redis-cache", "cpu_util", "warning", "us-east-1"),
    ("redis-cache", "cpu_util", "warning", "eu-west-1"),
    ("kafka-ingest", "queue_depth", "warning", "us-east-1"),
    ("cdn-edge", "http_5xx", "warning", "us-east-1"),
    ("k8s-platform", "cpu_util", "warning", "ap-south-1"),
    ("ci-runners", "queue_depth", "info", "us-east-1"),
    ("postgres-primary", "disk_io", "warning", "eu-west-1"),
    ("search-api", "p99_latency", "warning", "us-east-1"),
    ("auth-gateway", "error_rate", "warning", "us-east-1"),
    ("checkout-api", "p99_latency", "warning", "eu-west-1"),
]

# The normal pool must never share a (service, check, region) triple with the
# noise pool: fingerprints are hash(service|check|severity_in|region|env|cluster), so a
# shared triple would let a p3/p4 alert land on a known-noise fingerprint,
# join the suppression allowlist, and get wrongly suppressed.
_NOISE_TRIPLES = {(s, c, r) for s, c, _, r in _NOISE_TEMPLATES}

_NORMAL_TEMPLATES = [
    (svc, chk, sev, reg)
    for svc in SERVICE_TEAMS
    for chk in _CHECKS
    for sev, reg in (("warning", "us-east-1"), ("critical", "eu-west-1"))
    if (svc, chk, reg) not in _NOISE_TRIPLES
][:40]


def _alert(alert_id: str, received_at: str, service: str, check: str,
           severity_in: str, title: str, region: str, metric_value: float,
           metric_threshold: float, breach_s: int, kind: str,
           extra_labels: dict | None = None,
           history_hint: dict | None = None) -> Alert:
    labels = {"env": "prod", "region": region, "cluster": f"{region}-a"}
    if extra_labels:
        labels.update(extra_labels)
    return Alert(
        alert_id=alert_id,
        received_at=received_at,
        fingerprint=fingerprint_of(service, check, severity_in, region),
        service=service,
        check=check,
        severity_in=severity_in,
        title=title,
        source="alertmanager",
        labels=labels,
        metric_value=metric_value,
        metric_threshold=metric_threshold,
        breach_duration_s=breach_s,
        raw={"generator": "synthetic", "kind": kind,
             "history_hint": history_hint or {}},
    )


def _label(severity_true: str, became_sev12: int, service: str,
           disposition_true: str, allowlist_candidate: bool,
           auto_cleared: int = 0) -> dict:
    return {
        "severity_true": severity_true,
        "became_sev12": became_sev12,
        "team_true": SERVICE_TEAMS[service],
        "disposition_true": disposition_true,
        "auto_cleared": auto_cleared,
        "allowlist_candidate": allowlist_candidate,
    }


def _gen_noise(i: int, seed: int, rng: random.Random) -> tuple[Alert, dict]:
    service, check, sev_in, region = rng.choice(_NOISE_TEMPLATES)
    breach = rng.randint(60, 600)
    threshold = 80.0
    alert = _alert(
        alert_id=f"syn-{seed:04d}-{i:06d}",
        received_at=(_BASE_TIME + timedelta(seconds=i * 53)).isoformat(),
        service=service, check=check, severity_in=sev_in,
        title=f"{check} spike on {service} — self-cleared after {breach // 60}m",
        region=region,
        metric_value=round(threshold * (1.3 + rng.random() * 1.2), 2),
        metric_threshold=threshold, breach_s=breach, kind="known_noise",
        history_hint={"n30d": rng.randint(40, 200), "paged30d": rng.randint(30, 150),
                      "sev12_30d": 0,
                      "median_autoclear_min": round(breach / 60 * (0.8 + rng.random() * 0.4), 1)},
    )
    return alert, _label("known_noise", 0, service, "suppress", True, auto_cleared=1)


def _gen_deploy(i: int, seed: int, rng: random.Random) -> tuple[Alert, dict]:
    # Never reuse a noise (service, check, region) triple: deploy-adjacent
    # alerts must not land on known-noise fingerprints (see _NOISE_TRIPLES).
    for _ in range(50):
        service = rng.choice(list(SERVICE_TEAMS))
        check = rng.choice(_CHECKS)
        region = rng.choice(_REGIONS)
        if (service, check, region) not in _NOISE_TRIPLES:
            break
    became = 1 if rng.random() < 0.05 else 0
    sev_true = "p2_high" if became else "p3_medium"
    disp = "page_now" if became else "page_business_hours"
    alert = _alert(
        alert_id=f"syn-{seed:04d}-{i:06d}",
        received_at=(_BASE_TIME + timedelta(seconds=i * 53)).isoformat(),
        service=service, check=check, severity_in="warning",
        title=f"{check} elevated on {service} within 30m of deploy",
        region=region,
        metric_value=round(80.0 * (1.2 + rng.random()), 2),
        metric_threshold=80.0, breach_s=rng.randint(120, 1800),
        kind="deploy_adjacent",
        extra_labels={"deploy_id": f"dpl-{rng.randint(10000, 99999)}"},
        history_hint={"n30d": rng.randint(2, 12), "paged30d": rng.randint(1, 8),
                      "sev12_30d": 1 if became else 0,
                      "median_autoclear_min": round(15 + rng.random() * 60, 1)},
    )
    return alert, _label(sev_true, became, service, disp, False)


def _gen_warn(i: int, seed: int, rng: random.Random) -> tuple[Alert, dict]:
    service, check, _, region = rng.choice(_NORMAL_TEMPLATES)
    sev_in = rng.choice(["warning", "info"])
    sev_true = "p3_medium" if rng.random() < 0.7 else "p4_low"
    alert = _alert(
        alert_id=f"syn-{seed:04d}-{i:06d}",
        received_at=(_BASE_TIME + timedelta(seconds=i * 53)).isoformat(),
        service=service, check=check, severity_in=sev_in,
        title=f"{check} {sev_in} on {service}",
        region=region,
        metric_value=round(80.0 * (1.05 + rng.random() * 0.4), 2),
        metric_threshold=80.0, breach_s=rng.randint(300, 3600),
        kind="p3p4_warning",
        history_hint={"n30d": rng.randint(5, 60), "paged30d": rng.randint(0, 20),
                      "sev12_30d": 0,
                      "median_autoclear_min": round(20 + rng.random() * 120, 1)},
    )
    # p4s are queued, never suppressed: only allowlisted known_noise suppresses.
    return alert, _label(sev_true, 0, service, "page_business_hours", False)


def _gen_sev(i: int, seed: int, rng: random.Random) -> tuple[Alert, dict]:
    service, check, _, region = rng.choice(_NORMAL_TEMPLATES)
    sev_true = "p1_critical" if rng.random() < 0.7 else "p2_high"
    alert = _alert(
        alert_id=f"syn-{seed:04d}-{i:06d}",
        received_at=(_BASE_TIME + timedelta(seconds=i * 53)).isoformat(),
        service=service, check=check, severity_in="critical",
        title=f"{check} CRITICAL on {service} — customer impact suspected",
        region=region,
        metric_value=round(80.0 * (2.0 + rng.random() * 2.0), 2),
        metric_threshold=80.0, breach_s=rng.randint(300, 3600),
        kind="real_sev",
        history_hint={"n30d": rng.randint(0, 3), "paged30d": rng.randint(0, 3),
                      "sev12_30d": rng.randint(1, 3),
                      "median_autoclear_min": None},
    )
    return alert, _label(sev_true, 1, service, "page_now", False)


def generate_alerts(n: int, seed: int) -> list[tuple[Alert, dict]]:
    """Generate ``n`` labeled alerts deterministically from ``seed``."""
    rng = random.Random(seed)
    out: list[tuple[Alert, dict]] = []
    for i in range(n):
        r = rng.random()
        if r < 0.55:
            pair = _gen_noise(i, seed, rng)
        elif r < 0.75:
            pair = _gen_deploy(i, seed, rng)
        elif r < 0.95:
            pair = _gen_warn(i, seed, rng)
        else:
            pair = _gen_sev(i, seed, rng)
        out.append(pair)
    return out


def _jitter_probs(base: dict[str, float], rng: random.Random) -> dict[str, float]:
    """Perturb a template ±10% relative, round to 0.01 (Jev wire behavior),
    renormalize so the map sums to 1.0."""
    perturbed = {k: v * (1.0 + rng.uniform(-0.10, 0.10)) for k, v in base.items()}
    rounded = {k: round(v, 2) for k, v in perturbed.items()}
    total = sum(rounded.values())
    # Fix rounding drift on the argmax key so the map sums to exactly 1.0.
    top = max(rounded, key=lambda k: rounded[k])
    rounded[top] = round(rounded[top] + (1.0 - total), 2)
    return rounded


def script_answers(alert: Alert, label: dict, rng: random.Random,
                   label_noise: float = 0.0) -> dict:
    """Script the Jev-style answer triple for one labeled alert.

    Returns ``{q1_probs, q1_choice, q1_conf, q2_choice, q2_probs,
    q3_choice, q3_conf}``. With ``label_noise=0`` the argmax always matches
    the ground truth; ``label_noise>0`` flips the argmax with that
    probability, simulating Jev misreads.
    """
    sev_true = label["severity_true"]
    q1_probs = _jitter_probs(_BASE_Q1_PROBS[sev_true], rng)
    q1_choice = max(q1_probs, key=lambda k: q1_probs[k])
    if rng.random() < label_noise:
        q1_choice = rng.choice([o for o in SEVERITY_OPTIONS if o != sev_true])

    team_true = label["team_true"]
    others = [t for t in sorted(set(SERVICE_TEAMS.values())) if t != team_true]
    t_base = {team_true: 0.85}
    share = 0.15 / len(others)
    for t in others:
        t_base[t] = share
    q2_probs = _jitter_probs(t_base, rng)
    q2_choice = max(q2_probs, key=lambda k: q2_probs[k])
    if rng.random() < label_noise:
        q2_choice = rng.choice(others)

    q3_choice = label["disposition_true"]
    if q3_choice == "suppress":
        q3_conf = 0.90 + rng.random() * 0.08   # clears the 0.90 triple-lock bar
    else:
        q3_conf = 0.80 + rng.random() * 0.18
    q3_conf = round(q3_conf, 2)

    return {
        "q1_probs": q1_probs,
        "q1_choice": q1_choice,
        "q1_conf": round(max(q1_probs.values()), 2),
        "q2_choice": q2_choice,
        "q2_probs": q2_probs,
        "q3_choice": q3_choice,
        "q3_conf": q3_conf,
    }


def labels_jsonl_row(alert: Alert, label: dict, answers: dict) -> dict:
    """One tuner-format JSONL row for an alert."""
    return {
        "fingerprint": alert.fingerprint,
        "q1_probs": answers["q1_probs"],
        "q3_confidence": answers["q3_conf"],
        "became_sev12": label["became_sev12"],
        "would_page_baseline": 1 if alert.severity_in in ("critical", "high", "warning") else 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate seeded synthetic alerts as tuner-ready labels.jsonl")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--label-noise", type=float, default=0.0)
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    rng = random.Random(args.seed + 1)
    with open(args.output, "w", encoding="utf-8") as fh:
        for alert, label in generate_alerts(args.n, args.seed):
            answers = script_answers(alert, label, rng, label_noise=args.label_noise)
            fh.write(json.dumps(labels_jsonl_row(alert, label, answers)) + "\n")
    print(f"wrote {args.n} rows -> {args.output} (seed={args.seed})")


if __name__ == "__main__":
    main()
