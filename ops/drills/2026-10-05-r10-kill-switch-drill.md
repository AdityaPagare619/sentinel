# R-10 kill-switch drill record

**Date:** 2026-10-05T15:46:39.623172+00:00 · **Ran by:** dev-1 (two-person OFF: op-a,op-b)
**Lab:** /tmp/drill-verify (built via `scripts/ops/env-bootstrap.sh --tier staging-lab`); receiver in SENTINEL_MOCK=1, no PD_ROUTING_KEY (never-pages rule), forwarder at the in-process 202 blackhole (pages nothing).
**Verdict:** PASS

## Criteria

- [x] **C1 baseline alert round-trips (pre-flip)** — disposition=passthrough reason=error:error gen=2
- [x] **C2 kill-switch flip ON via flagctl (one human)** — next: POST /-/reload (or SIGHUP), then confirm the new config_generation on /healthz
- [x] **C3 reload applies: generation bumps, /healthz shows the flag** — generation=3 flags.global_kill_switch=true
- [x] **C4 alert round-trips as passthrough/reason=kill_switch** — disposition=passthrough reason=kill_switch
- [x] **C5 kill-switch flip OFF needs the two-person ceremony** — single-human OFF refused; two-person=(op-a,op-b) accepted
- [x] **C6 behavior restored after OFF (no kill_switch reason)** — disposition=passthrough reason=error:error
- [x] **C7 reload-rejection: invalid flags.json -> 422, live untouched** — 422 as expected; live gen still 4

## F1 kill-condition linkage

PASS: the wire stands; `lane/12h-dev-1-r10` proceeds to PR (Relay + Vault review).
