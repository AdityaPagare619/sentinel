# RFC: Forwarder/pipeline metrics lock — the 3fac416 bug class (2026-10-07)

Owner: FAANG engine team (single-threaded). Branch: `lane/faang-engine-20261007`.
Type: **Type 2** (internal locking; no behavior change except counter accuracy).

## Problem
Audit engine P3: forwarder `metrics` unlocked `+=` — the same bug class as the fail-open ladder race `3fac416` fixed (unsynchronized shared state under the threaded receiver). Sweep results (`metrics[...] +=` with no lock, all reachable from `ThreadingHTTPServer` handler threads sharing one `Pipeline`):

- `src/sentinel/forwarder.py` — **DurableForwarder**: lines 341, 365, 488, 642, 667, 704, 768, 847 (8 sites; worker pool + scheduler + scan threads). **Legacy Forwarder**: lines 1097, 1162, 1211, 1226, 1240 (5 sites; shared across webhook handler threads).
- `src/sentinel/receiver.py` — `Pipeline.metrics`: lines 304, 326, 357, 365, 373, 378, 405, 411, 474, 481, 524, 538, 540, 543, 742, 905 (16 sites; same threads).
- `src/sentinel/eventlog.py` — `EventLog.metrics`: 8 internal sites + 2 cross-module (`checkpoint.py` evidence-loss counter, reaper `reaper_redrives`) — forwarder worker pool, checkpoint thread, reaper thread. Fixed via `bump_metric()` under the existing `_lock` (RLock — safe reentrantly).
- Clean: `failopen.py` (fixed by `3fac416` — locks + snapshot iteration; verified by `test_failopen_hammer.py`), `gate.py` (stderr only), `secondary.py`/`watchdog.py`/`spill.py` (no shared counters on hot paths).

In CPython, `d[k] += 1` is a read-modify-write sequence: two threads can read the same value and one increment is lost. Consequence today: undercounted metrics (the `killed` counter — the exact counter an operator reads during a kill event — can undercount). Consequence if ignored: metrics the kill drill asserts on (`killed_metric_eq_5`) become flaky under load.

## Decision
One leaf `_metrics_lock = threading.RLock()` per class + `_metric_inc(key, n=1)` helper; replace every `self.metrics[k] += 1` site. RLock (not Lock): `_kill_absorb` bumps `killed` while callers may hold `_halt_lock` — a non-reentrant lock risks self-deadlock on future refactors; leaf-lock discipline documented (the metrics lock is never held while acquiring any other lock).

Readers (`ops_health`, drill assertions) read `dict(metrics)` under the same lock via a `_metrics_snapshot()` helper — no torn reads.

## Alternatives considered and rejected
- **Per-counter `itertools.count` / atomics:** Python has no atomic int increment for dict values; a lock is the idiomatic fix.
- **Lock-free sharded counters:** rejected — complexity for counters read by operators, not the hot path; the lock is uncontended (nanosecond-scale critical section).
- **Leaving it ("metrics are advisory"):** rejected — the kill drill asserts exact metric values; advisory-only metrics that lie during the incident they instrument are worse than none.

## Pre-mortem ("this failed in production — why?")
1. **Deadlock via lock ordering:** mitigated by leaf-lock discipline (metrics lock last, never held across other locks) + RLock reentrancy.
2. **A new `metrics[...] += 1` site bypasses the helper:** mitigated by the hammer test + a source-scan test asserting no raw `metrics[` `+=` remains in the three files (the same "prove the class is gone" pattern as the AST purity test).
3. **Performance:** the critical section is a dict increment; measured overhead is noise next to a PD POST.

## Verification
- `tests/test_metrics_lock.py`: N-thread hammer (32 threads × 5k increments across all three classes) asserting exact final counts — failing-before (run against the pre-fix code to prove it catches the race), passing-after.
- Source-scan test: `grep`-equivalent asserting zero remaining `self.metrics[...] +=` in `forwarder.py`/`receiver.py`.
- Full suite re-run (at least `test_forwarder`, `test_durable_forwarder`, `test_kill_topology`, `test_receiver`).

## Rollback
Revert the helper commit; metrics return to racy-but-present. No data migration, no API change.
