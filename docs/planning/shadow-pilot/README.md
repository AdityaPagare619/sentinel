# Shadow Pilot Harness

Everything needed to start the 2-week shadow pilot the moment real traffic
exists. The pilot is the go-live evidence: **the false-suppress rate on real
SEV1s**, measured on the customer's own alert traffic, with Sentinel in
read-only shadow mode (nothing pages, nothing is suppressed — the gate
records what it *would have* done).

## What's here

| File | Purpose |
|---|---|
| `run_shadow_pilot.py` | Runnable harness. Builds a `ShadowPipeline` (gate in
  `shadow=True`, dry-run — no paging path exists in the object graph),
  ingests PagerDuty v3 webhooks, logs every decision, generates the
  suppression report. Proven runnable: 200/200 synthetic deliveries
  accepted, report generated. |
| `RUNBOOK.md` | The 2-week runbook: day 0 setup, daily checks, day 14 report. |
| `LABELING_PROTOCOL.md` | How to label real SEV1s vs noise in the traffic. |
| `REPORT_TEMPLATE.md` | The suppression report template (what the buyer reads). |

## Where real traffic plugs in

Today `run_shadow_pilot.py` synthesizes PD v3 `incident.triggered`
webhooks locally (`synthetic_deliveries()`). For the real pilot:

1. Run an HTTP server exposing `/shadow/pagerduty` (or `/shadow/opsgenie`,
   `/shadow/alertmanager`) backed by `ShadowPipeline.handle()`.
2. Point the vendor's webhook at it (PD: Events API v2 → webhook
   subscription; the tap verifies HMAC via `pd_secret`).
3. Replace `synthetic_deliveries()` with the live feed. Nothing else changes
   — same verification, same parsing, same store, same report.

## Safety invariants (enforced, not promised)

- `ShadowConfig.write_credentials` must be empty — a non-empty list
  **refuses to boot** (`SystemExit`).
- The `ShadowPipeline` object graph contains **no reference** to any paging
  or vendor-write path — enforced by the static import-graph guard in
  `tests/test_shadow.py::TestReadOnlyProof`.
- Handler exceptions return 500 (vendor retries; ingest is idempotent) and
  **never** fall through to the receiver's fail-open-to-page path.

## Run it

```bash
python3 docs/planning/shadow-pilot/run_shadow_pilot.py --n 200 --seed 7
```
