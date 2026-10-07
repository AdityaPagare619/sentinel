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

### Tier 100K ✅ (2026-10-06 ~14:20-15:00 IST, seed 7, 32 workers, WITH pressure)

**Load-test catch #2 (memory, fixed before it bit):** the first 100K
attempt showed ~43MB/min unbounded RSS growth — the sim pipeline's
`AuditLog(":memory:")` retains every `decision_made` event (~7KB/decision).
At 1M alerts that's ~7GB on a 7GB box: a guaranteed OOM. Fixed in commit
`9dfeb96`: `build_sim_pipeline` takes an `audit_db_path` (file-backed DB
next to the report, provenance-recorded in the report JSON); the mixed
judge's per-call trace list became route counters + a bounded 1000-sample.
Restarted 100K with the fixes.

- **130,812 alerts in 2,380s wall (39.7 min) → 55.0 alerts/s** (6.0h virtual)
- Problems triaged 130,776 · deduped 100,903 (**77.2%**) · storms 36
- Race: judge-wins 7,082 · **timer-wins 51** · errors 2
- Actions: folded 80,274 · page_now 22,135 · page_business_hours 9,287 ·
  suppress 18,316 (14.0%) · passthrough 800
- Judge: real 177 / faithful 7,970 (2.17%) · cap not tripped
- Real Jev: n=177 **p50 503ms / p99 932ms** · Spend **$0.0083 → $0.06/M**
- FakePD pages 32,222 · real PagerDuty contacted: **false**

**Pressure phase (the fail-open proof):** slow-tail attack (15% of judgments
at 4.5s > 2.7s budget) across the middle third of chunks:
- **Timer-wins: 50 under pressure vs 1 at baseline** — the race is REAL, not
  theater. When the judge is slow, the timer wins.
- **Suppression rate: 0.009 under pressure vs 0.217 baseline** — NO creep;
  it FELL. A slow judge pages (timer-win → fail-open), never silences.
  The control principle holds under load. **PASS.**

**Throughput caveat (honest):** the 55/s was measured while the box was
under memory pressure (0 free RAM — the killed first attempt hadn't
released). A clean 60-chunk re-run on identical chunks/settings did
**157/s**. The engine's true rate is ~3x the pressured number; the 1M tier
(running now, clean box) establishes the real figure.

### Tier 1M ✅ (2026-10-06 ~15:40-17:20 IST, seed 7, 32 workers, clean baseline)

- **1,092,876 alerts in 5,830s wall (97 min) → 187.4 alerts/s** (24.0h virtual)
- Problems triaged 1,092,830 · deduped 902,560 (**82.6%**) · storms 46
- Race: judge-wins 924 · **timer-wins 0** · errors 4
  (only 0.08% of alerts reach the race — dedup+fold absorb the rest;
  judge p99 1.1s < 2.7s budget, so timer-wins 0 is correct, not theater —
  the race's reality was proven by the 100K pressure phase)
- Actions: folded 691,342 · page_now 388,552 · page_business_hours 1,631 ·
  suppress 11,080 (1.0%) · passthrough 271
- Judge: real 170 / faithful 7,544 (2.2% seeded sample) · cap not tripped
- Real Jev: n=170 **p50 560ms / p99 1,122ms**
- Faithful: p50 769ms / p99 1,302ms · faults: 4 transient_520 (0.05%)
- Spend **$0.0079 → $0.01/M alerts** (at 2% sampling)
- FakePD pages 390,421 · real PagerDuty contacted: **false** (verified)
- **Capacity at measured rate: 16,191,360 alerts/day**

### Burst probe (40 req/s vendor bound) ✅

Deliberate 400-call probe at the vendor bound (40 req/s × 10s):
- **400/400 ok, zero 429s, zero errors** — the vendor does NOT reject at the bound.
- **BUT: p50 latency 5,152ms / p99 9,071ms** — 10x degradation vs the ~500ms
  at polite rates. The vendor throttles by SLOWING, not by 429ing.
- Achieved 9.0/s actual (calls take 5-9s each, so 40/s offered → 9/s completed).
- **Production implication:** at the bound, every real call exceeds the 2.7s
  race budget → 100% timer-wins → fail-open pages everything. The design
  degrades gracefully (pages, never silences), but the PRACTICAL real-Jev
  rate for good latency is far below 40/s. At our 2% sampling (≈0.2 calls/s
  at 187/s engine rate), we live in the good-latency zone. Spend $0.0063.

## Verdict: can we handle millions/day?

**YES — with large headroom, on a single box.**

| Measure | Result |
|---|---|
| Sustained engine throughput | **187 alerts/s** (1M tier, 32 workers) |
| Daily capacity | **16.2M alerts/day** (16x the "millions" bar) |
| Breaking point | Soft knee at 32 workers (211/s peak); 64 workers = -24% speed, zero errors, suppression unchanged |
| Real Jev @ 2% sample (n=444 total) | p50 ~530ms, p99 ~1.1s — inside the 2.7s race budget |
| Cost | **$0.01 per million alerts** (2% real sampling); $5 cap never touched ($0.02 total across all tiers) |
| Fail-open under judge pressure | **PASS** — timer-wins 50 vs 1; suppression 0.009 vs 0.217 (fell, never rose) |
| Real PagerDuty contact | **Zero** across 1.23M alerts (FakePD only, verified) |
| Vendor bound | Respected (pacer); burst probe: 0 errors at 40/s, 10x latency |

**The three load-test catches** (bugs this env found that unit tests didn't):
1. Fail-open ladder `deque mutated during iteration` under 16-thread load → serialized (commit `3fac416` + `dcf0955`).
2. Unbounded audit RAM (~43MB/min → 7GB OOM at 1M) → file-backed DB + bounded trace (commit `9dfeb96`).
3. btrfs 60ms sqlite fsync serializing 32 workers → WAL+NORMAL for the test harness; production note: audit DB belongs on fast storage (commit `581054d`).

## What this test did NOT prove

1. **Judgment semantics at volume are modeled, timing is measured.**
   FaithfulJev dispositions follow scripted `answers_for` semantics; only
   the 2% real sample (n=444) carries true Jev judgments. Timing, faults,
   race dynamics, dedup, storm behavior, and cost are measured.
2. **The 100K tier's 55/s was environment-depressed** (box under memory
   pressure during that run). The 1M's 187/s on a clean box is the
   authoritative number; a clean 60-chunk re-run did 157/s, consistent.
3. **Virtual time, not real time.** Correlator windows see virtual density;
   sub-minute burst shapes are approximated by batch concurrency.
4. **Single machine.** No distributed-systems effects: no network partitions,
   no multi-instance dedup races, no cross-AZ latency.
5. **The burst probe's 10x latency degradation** means the real-Jev sampling
   rate cannot scale linearly — at 100% real, the race would timer-win
   constantly (fail-open, but noisy). The 2% sampling strategy is load-bearing.
6. **Audit durability vs speed:** the test used WAL+NORMAL; production uses
   fsync-per-commit. On slow filesystems the audit is the throughput cap
   (measured 60ms/commit on btrfs).

**Load-test catch #3 (the 55/s mystery, solved):** the 100K tier's 55/s was
NOT the engine's limit. Thread-stack dumps showed 65/68 threads parked on
the audit lock. Root cause: the production `EventLog` fsyncs every commit
(correct for production); on this box's **btrfs** a commit costs **~60ms**
vs 0.2ms on tmpfs. 32 triage workers serialized on the audit lock → 55/s
cap. A filesystem artifact, not an engine limit — but a REAL production
deployment note: the audit DB belongs on fast storage (or batched commits).
Fixed in commit `581054d`: the harness sets `WAL + synchronous=NORMAL` on
the load-test audit connection (production defaults untouched). 5-chunk
verification: **335/s** (was 54/s). The 1M tier measures the engine, not the
filesystem.

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
