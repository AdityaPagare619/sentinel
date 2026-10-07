# CALIBRATION — faithful simulated services

**Lane:** production-stages · **Date:** 2026-10-06
**Applies to:** `FaithfulJev` (`src/sentinel/client.py`), faithful `FakePDSink`
(`platform/server/sim/sim_runner.py`), `--judge faithful`.

## What "calibrated" means here

A faithful fake is calibrated when its BEHAVIORAL parameters (timing,
fault rates) come from measurements of the real service — not from guesses.
Judgment SEMANTICS (which alerts get paged) remain scripted by design:
the fake is faithful about *how the service behaves*, not *what it decides*.

## Jev latency distribution — MEASURED

- **Source:** `research/jev-behavior/latency-report-2026-10-03.md`
  (raw: `research/jev-behavior/latency-n100.json.jsonl`, status==200 rows)
- **Measured:** 2026-10-03 07:46–08:07 UTC (13:16–13:37 IST)
- **Method:** real-key calls to `POST api.typesafe.ai/v1/systemone` via the
  stored credential; cold×10, warm-fresh×60 (+40 supplement), warm-reused×40
  (all 40 failed on a sandbox proxy artifact — excluded, documented).
- **n = 109** successful calls. Embedded verbatim in
  `src/sentinel/client.py::_JEV_LATENCY_CAMPAIGN_MS`.
- **Distribution:** min 494ms · p50 816ms · mean 850ms · p95 1312ms ·
  p99 1526ms · max 1678ms.
- **Faults measured in the same campaign (150 calls):** one transient
  HTTP 520 (~0.7%); zero 429/529 at ≤0.35 req/s.

### Supplementary live data — 2026-10-06

- **Source:** first real-key sim run, `normal-day` scenario through the real
  race→gate→forwarder (parent lane, vault-surrogate credential).
- **5/5 live calls OK**, latencies **282–717ms** — faster than the 10-03
  campaign (different path conditions; smaller n).
- **Key finding:** the `noise_suppressed` sim assertion FAILED — the real
  judge did not suppress noise the way scripted FakeJev did. This is the
  empirical proof that scripted dispositions diverge from reality, and why
  `FaithfulJev` is explicit: faithful TIMING + FAULTS, scripted SEMANTICS.
- **Adoption rule:** the 10-03 campaign (n=109) remains the primary latency
  calibration (larger sample, wider conditions). Re-run the latency
  campaign quarterly or after any vendor model change, and re-embed.

## FakePD timing — MODELED, NOT MEASURED (honest gap)

- **ACK latency 80–400ms, delivery delay 200–1500ms:** assumptions, not
  measurements. No PagerDuty Events API timing data was available.
- **What IS faithful:** the delivery state machine
  (RECEIVED→ACCEPTED→QUEUED→DELIVERED/FAILED), dedup_key idempotency
  (append-to-open-incident), and the fault classes (429/500/delay/drop).
- **To calibrate:** capture real PD Events API v2 timings (accept latency,
  Retry-After behavior under 429) and replace the modeled ranges.

## Re-calibration triggers

1. Vendor model change (new `jev-*` version) — re-run latency campaign.
2. Quarterly cadence, even without changes (drift check).
3. Any production incident where sim timing diverged from observed reality.
4. FakePD: first real PD timing capture.

## What the fakes are NOT

- `FaithfulJev` does not reproduce judgment semantics — dispositions are
  scripted. Do not use it to validate suppression correctness; use
  `--judge real` (live runbook) for that.
- Neither fake models vendor outages longer than the injected profiles;
  chaos drills beyond the profiles need explicit scenario design.
