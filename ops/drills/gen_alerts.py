#!/usr/bin/env python3
"""Generate PD Events API v2-shaped alert payloads as JSONL (Tripwire harness).

--sev critical  : synthetic SEV1 storm (drill 6)
--sev mix       : mixed severities (drill 1/5 storms)
--fp-mod N      : distinct fingerprints cycle over N (storm shape for drill 2)
"""
import argparse
import json
import random

SERVICES = ["api", "payments", "db-primary", "cdn-edge", "worker-queue"]
CHECKS = ["cpu_high", "error_rate_spike", "disk_full", "p99_latency",
          "conn_pool_exhausted", "tls_cert_expiring", "queue_depth"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--sev", choices=["critical", "mix"], default="mix")
    ap.add_argument("--fp-mod", type=int, default=0,
                    help="distinct fingerprints cycle (0 = all unique)")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    with open(args.output, "w", encoding="utf-8") as fh:
        for i in range(args.n):
            fp_i = (i % args.fp_mod) if args.fp_mod else i
            # Fingerprint = sha256(service|check|severity|region): key ALL
            # of them off fp_i so --fp-mod genuinely cycles fingerprints.
            sev = "critical" if args.sev == "critical" else random.Random(
                args.seed * 1000 + fp_i).choice(
                ["critical", "critical", "error", "warning"])
            svc = SERVICES[fp_i % len(SERVICES)]
            chk = CHECKS[fp_i % len(CHECKS)]
            region = fp_i % 3
            payload = {
                "routing_key": "drill-routing-key",
                "event_action": "trigger",
                "dedup_key": f"drill-{args.seed}-{i}",
                "payload": {
                    "summary": f"[SEV1 drill] {svc}:{chk} breach #{fp_i}",
                    "source": svc,
                    "severity": sev,
                    "component": svc,
                    "class": chk,
                    "group": f"region-{region}",
                    "region": f"region-{region}",
                    "custom_details": {
                        "fingerprint_seed": fp_i,
                        "breach_pct": 95 + (i % 5),
                    },
                },
            }
            fh.write(json.dumps(payload) + "\n")
    print(f"wrote {args.n} payloads -> {args.output} (seed={args.seed})")


if __name__ == "__main__":
    main()
