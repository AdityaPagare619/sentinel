# Production notes — learned from the 2026-10-06 load test

Full evidence: `docs/planning/loadtest/LOADTEST-REPORT.md` (1.23M alerts,
three tiers + burst + breaking-point probes, zero real PagerDuty contact).
This file is the production-relevant extract — the things a future
engineer must know before deploying.

## 1. The audit DB belongs on fast storage (btrfs lesson)

The production `EventLog` fsyncs every commit (correct for production).
On this dev box's **btrfs**, a commit costs **~60ms** vs 0.2ms on tmpfs.
Under the 1M-alert load test, 32 triage workers serialized on the audit
lock → throughput capped at 55/s. A filesystem artifact, not an engine
limit — but a REAL deployment constraint: **put the audit DB on fast
storage (or use batched commits)**. The 5-chunk verification after
switching the *test harness* to WAL+NORMAL hit 335/s (was 54/s);
production defaults (fsync-per-commit) were deliberately left untouched
(commit `581054d`).

## 2. The three bugs the load test caught (all fixed on main)

1. **Fail-open ladder `deque mutated during iteration`** under 16-thread
   load — `VendorHealthMonitor`'s deque was appended by `record()` while
   `observe()` iterated it. Fixed: locks + snapshot iteration in
   `src/sentinel/failopen.py` (commits `3fac416` + `dcf0955`). Regression
   guard: `tests/test_failopen_hammer.py` (N-thread hammer, in the gate).
2. **Unbounded audit RAM** (~43MB/min → 7GB OOM at 1M alerts) — the audit
   trail was an in-memory list. Fixed: file-backed DB + bounded
   1000-sample trace (commit `9dfeb96`).
3. **btrfs fsync serialization** — see §1 (commit `581054d`).

## 3. What the load test did NOT prove (do not cite beyond these)

- Judgment semantics at volume are *modeled* (FaithfulJev scripted
  dispositions); only the 2% real sample (n=444) carries true Jev
  judgments. Timing, faults, race dynamics, dedup, storm behavior, and
  cost are measured.
- Virtual time, not real time. Single machine — no network partitions,
  no multi-instance dedup races, no cross-AZ latency.
- The 100K tier's 55/s was environment-depressed (box under memory
  pressure); the 1M tier's 187/s on a clean box is the authoritative
  number.
