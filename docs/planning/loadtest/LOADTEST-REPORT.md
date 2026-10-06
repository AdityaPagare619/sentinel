# Sentinel Load Test — Millions/Day Validation

**Lane:** `lane/loadtest-env` · **Date:** 2026-10-06 · **Order:** Aditya — prove
how Jev + our engine handle millions of alerts/day, with the REAL Jev key.

## Method (principal approach)

Measure the real thing at statistical significance; simulate the rest
faithfully. The harness drives the REAL pipeline (`Pipeline._triage`:
correlator → gate(race → judge) → forwarder) with concurrent alert
injection, exactly like a real deployment's receiver.

- **Judge:** MixedJevClient — sampled REAL Jev via vault surrogate
  (default 2%, seeded) + FaithfulJev bulk (real-measured latencies
  p50 816ms/p99 1526ms, honest fault injection). HARD $5 spend cap.
- **Fakes:** FakePD sink, loopback-only. Real PagerDuty never addressable.
- **Vendor bound:** 40 req/s paced; deliberate burst probe at the bound.
- **Tiers:** 10K → 100K → 1M alerts, same mix semantics, deterministic seeds.
- **Breaking point:** worker ramp 8→64 on fixed workload.
- **Pressure phase:** slow-tail judge attack mid-run; timer-wins MUST rise,
  suppression rate MUST NOT (fail-open law under load).

## Results

_(filled per tier as runs complete)_

### Tier 10K ✅ (2026-10-06 ~13:50 IST, seed 7, 16 workers)

- **11,899 alerts in 296.1s wall → 40.2 alerts/s** (2.0h virtual span)
- Problems triaged 11,869 · deduped 7,235 (**61.0%**) · storms declared 30
- Race: judge-wins 4,294 · **timer-wins 0** · errors 2
  (budget B=2700ms ≫ judge p99 — timer never legitimately wins at baseline;
  the fail-open path is exercised by the pressure phase, not the baseline)
- Actions: suppress 5,161 (43.4%) · page_now 2,089 · page_business_hours 3,883 ·
  folded 680 · passthrough 86
- Judge: real 97 / faithful 4,199 (2.26% sampled, seeded) · cap not tripped
- Real Jev latency: n=97 **p50 527ms / p99 1,085ms**
- Spend **$0.0045 → $0.38/M alerts** (at 2% sampling)
- FakePD pages 6,058 · real PagerDuty contacted: **false** (verified)
- Capacity at measured rate: **3.47M alerts/day** (16 workers, single box)

**Load-test catch #1 (real bug, fixed same day):** the 10K run surfaced
`[sentinel] failopen observe failed (deque mutated during iteration)` —
`VendorHealthMonitor`'s deque was appended by `record()` while
`timer_win_rate()` iterated it under 16-thread load. The gate swallowed it
in a stderr print, so the fail-open ladder was silently going blind under
pressure. Fixed in `src/sentinel/failopen.py` (commit `3fac416`): locks +
snapshot iteration on both monitors, whole-`observe()` RLock on the
controller. 39/39 C1 unit tests pass; 32k-observation 16-thread hammer clean.

### Tier 100K (running — with pressure phase)

### Tier 1M

### Breaking point ✅ (2026-10-06 ~14:00 IST, 2K-alert steady workload, all-faithful)

Worker ramp on the fixed workload — where throughput plateaus and HOW it
degrades:

| workers | alerts/s | timer-wins | errors | suppression rate |
|--------:|---------:|-----------:|-------:|-----------------:|
| 8 | 133.1 | 0 | 0 | 0.459 |
| 16 | 175.3 | 0 | 0 | 0.459 |
| **32** | **211.1** | 0 | 0 | 0.459 |
| 64 | 160.5 | 0 | 0 | 0.459 |

- **Peak at 32 workers: 211 alerts/s** (matches the race pool size of 32 —
  the judge-race pool is the binding constraint, as designed).
- At 64 (2× oversubscription): throughput drops 24% but **zero errors, zero
  timer-wins, suppression rate identical to 4 decimals** — degradation is
  graceful slowdown, never wrongness. The control principle holds under
  saturation: no silent suppression creep, no collapse.
- **The breaking point is a soft knee, not a cliff.** Past 32 workers you
  pay queueing delay, not correctness.

### Burst probe (40 req/s vendor bound)

## Verdict: can we handle millions/day?

_(data-driven)_

## What this test did NOT prove

1. **Judgment semantics at volume are modeled, timing is measured.**
   FaithfulJev dispositions follow the scripted `answers_for` semantics;
   only the 2% real sample carries true Jev judgments. Timing, faults,
   race dynamics, and cost are measured.
2. **Virtual time, not real time.** Correlator windows see virtual density;
   sub-minute burst shapes are approximated by batch concurrency.
3. **Single machine.** No distributed-systems effects: no network partitions,
   no multi-instance dedup races, no cross-AZ latency.
4. **FakePD timing is modeled.** No PagerDuty delivery data exists; the
   sink models ACK latency and the event state machine.
5. **Drift detection inactive** for load runs (mixed judge, by loud design);
   the pinned path was validated separately by the live real-key run.
6. **Capacity claims are rate-based** (measured alerts/sec × 86400) unless
   a full 24h-equivalent volume completed; stated per tier.

## Calibration sources

- Jev latencies: 2026-10-03 real-key campaign (n=109) + 2026-10-06 live
  probe (n=5, 282–717ms) + sampled real calls in these runs.
- Fault rates: measured 0.7% transient 520; zero 429s at polite rates.
- Key: vault surrogate only; never in files, env, or logs.
