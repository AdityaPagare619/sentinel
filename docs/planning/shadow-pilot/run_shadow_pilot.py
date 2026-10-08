#!/usr/bin/env python3
"""Shadow-pilot harness runner — proves the pilot loop is runnable end to end.

What it does:
  1. Builds a ShadowPipeline (gate in shadow=True, dry-run: NOTHING pages).
  2. Feeds it synthetic PagerDuty-format webhooks (sim fixtures stand in for
     real traffic until the pilot starts — see WHERE_REAL_TRAFFIC_PLUGS_IN).
  3. Logs every shadow decision to the ShadowStore.
  4. Generates the suppression report via shadow_report.py.

WHERE REAL TRAFFIC PLUGS IN:
  Today this script synthesizes PD v3 `incident.triggered` webhooks locally.
  For the real pilot, replace `synthetic_deliveries()` with an HTTP server
  exposing `/shadow/pagerduty` (or `/shadow/opsgenie`, `/shadow/alertmanager`)
  and point the vendor's webhook at it. The ShadowPipeline.handle() contract
  is unchanged — same verification, same parsing, same store. Nothing else
  in this harness needs to change.

Safety: ShadowConfig.write_credentials MUST be empty (the config refuses to
boot otherwise). The pipeline holds no reference to any paging path —
enforced by tests/test_shadow.py::TestReadOnlyProof.

Usage:
  python3 docs/planning/shadow-pilot/run_shadow_pilot.py [--n 200] [--seed 7]
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "src"))

from sentinel.shadow import (  # noqa: E402
    ShadowConfig,
    ShadowPipeline,
    ShadowStore,
)
from sentinel.shadow_report import generate_shadow_report  # noqa: E402

# The gate/correlator/thresholds live in the engine. Import lazily so this
# file documents the wiring without hard-coding engine internals.
from sentinel.gate import Gate  # noqa: E402
from sentinel.models import Thresholds  # noqa: E402
from sentinel.correlator import Correlator  # noqa: E402
from sentinel.audit import AuditLog  # noqa: E402
from sentinel.sim_judge import build_sim_judge  # noqa: E402


# Verification secret for the synthetic smoke run. Real pilots set
# SHADOW_PILOT_PD_SECRET in the environment (the vendor's webhook secret).
# Never hardcode even a dummy secret — the secrets gate forbids it.
PD_SECRET = os.environ.get("SHADOW_PILOT_PD_SECRET") or secrets.token_hex(16)


def _pd_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def synthetic_deliveries(n: int, seed: int):
    """Yield (body, headers) pairs shaped like PD v3 incident.triggered.

    Stand-in for real traffic. Deterministic from seed.
    """
    import random
    rng = random.Random(seed)
    services = ["checkout", "payments", "db-primary", "edge-cache", "billing"]
    titles = [
        "Error rate spike on checkout",
        "Latency p99 above SLO",
        "DB failover drill event",
        "CPU saturation on worker pool",
        "Queue backlog growing",
        "Certificate expiring soon",
    ]
    for i in range(n):
        # ~5% of fixtures are "real SEV1s" (would-page ground truth);
        # the rest are noise. The labeling protocol (LABELING_PROTOCOL.md)
        # replaces this heuristic with human labels on real traffic.
        is_real = rng.random() < 0.05
        incident_id = f"INC{i:05d}"
        # PD v3 webhook shape: event.{id, event_type, data{...}}.
        payload = {"event": {
            "id": f"evt-{incident_id}",
            "event_type": "incident.triggered",
            "occurred_at": "2026-10-08T00:00:00Z",
            "data": {
                "id": incident_id,
                "title": rng.choice(titles),
                "urgency": "high" if is_real else rng.choice(["high", "low"]),
                "priority": {"summary": "P1" if is_real else rng.choice(["P1", "P2", "P3"])},
                "service": {"summary": rng.choice(services)},
                "_ground_truth_real_sev1": is_real,
            },
        }}
        body = json.dumps(payload).encode()
        headers = {
            "x-webhook-subscription": "pilot-harness",
            "x-pagerduty-signature": "v1=" + _pd_signature(PD_SECRET, body),
        }
        yield body, headers


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200, help="synthetic deliveries")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    config = ShadowConfig(enabled=True, pd_secret=PD_SECRET)
    config.validate()  # fail-closed on write_credentials

    jev_client, _, _ = build_sim_judge(budget_usd=1.0)
    tmpdir = tempfile.mkdtemp(prefix="shadow-pilot-")
    gate = Gate(jev_client, Thresholds(), [],
                AuditLog(db_path=os.path.join(tmpdir, "audit.db")),
                shadow=True)
    store = ShadowStore()
    pipeline = ShadowPipeline(gate=gate, correlator=Correlator(),
                              store=store, config=config)

    accepted = rejected = 0
    t0 = time.time()
    for body, headers in synthetic_deliveries(args.n, args.seed):
        status, _ = pipeline.handle("pagerduty", body, headers)
        if status in (200, 202):
            accepted += 1
        else:
            rejected += 1
    dt = time.time() - t0

    report = generate_shadow_report(
        store, org="pilot-harness", week_label="harness-smoke",
        window_start="2026-10-08T00:00:00Z", window_end="2026-10-08T00:00:00Z")
    print(f"[pilot] ingested={args.n} accepted={accepted} rejected={rejected} "
          f"({dt:.1f}s)")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
