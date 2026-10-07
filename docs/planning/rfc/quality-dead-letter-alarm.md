# RFC — Dead-letter alarm on the `_retryable` max-age path (engine P1)

**Date:** 2026-10-07 · **Lane:** `lane/faang-quality-20261007` · **Author:** TEAM 6 (quality/test)
**Decision type:** Type 2 (reversible, within-module, precedent-bound)
**Status:** ACCEPTED — implement, then prove with tests.

## 1. Problem

Audit engine P1: `_retryable`'s schedule-time max-age branch dead-letters with
no control-plane page, violating the module's "morgue with an alarm"
invariant (`src/sentinel/forwarder.py:705`).

Two code paths dead-letter with `error_class="max_age_exceeded"`:

| Path | Location | Alarm |
|---|---|---|
| Schedule-time (`_retryable`): next retry would exceed `max_age_at` | forwarder.py:653–656 | **none** — stderr print only |
| Background sweep (`_max_age_sweep`): row aged past `max_age_at` | forwarder.py:797–807 | `enqueue_control_plane_page(kind="forwarder_dead_letter")` |

Same event class, same morgue record, different alarms. The schedule-time
path is arguably the *more* common trigger (a single row whose backoff would
miss its deadline is caught here first; the sweep only sees rows that fell
through). The inconsistency means the alarm's coverage depends on which path
noticed first — an accident of timing, not a design decision.

## 2. Why the invariant stands (Chesterton's fence)

The alarm exists because silent drops are this product's catastrophic failure
mode: a paging intent that dies without a human ever knowing is the
quant_platform-class failure ("dashboard showed active while the engine kept
placing orders"). The control-plane page channel is already the module's
chosen alarm: a Priority-1 immortal outbox row (`EventLog._enqueue_control_plane_page`,
eventlog.py:639 — immortal because `max_age_at == created_at`, per the
module's immortal-row rule), routed to the control routing key, delivered by
the forwarder. The `_terminal` path (400 / our bug) uses the same channel.
The sweep path uses it for the *identical* error class. The invariant is
deliberate, thrice-precedented, and the schedule-time path is the outlier.

## 3. Decision

Add the same `enqueue_control_plane_page(kind="forwarder_dead_letter", …)`
call to the `_retryable` max-age branch, wrapped in the same try/except guard
as the sweep (a sick loud path must not resurrect the row or abort the
worker). The sweep's summary/detail shape is reused verbatim so the two paths
are indistinguishable downstream — one alarm class, one morgue.

## 4. Alternatives considered and rejected

- **Remove the sweep's page for "symmetry."** Rejected — symmetry at the
  lower alarm level deletes the invariant the module was designed around.
- **Honest log-only alternative (document that max-age expiry is
  by-design and needs no page).** Rejected — stderr-only is precisely the
  blind spot the invariant was written against; and "expiry is by design"
  confuses the *alert's* deadline with the *pipeline's* duty to report that
  the deadline was missed unserved.
- **Coalesced/rate-limited alarm (one page per window + counter).** Deferred,
  not rejected. Real concern (a vendor outage ages every row → N pages), but
  the sweep path already has the identical property, so this change introduces
  no new failure mode. Coalescing is a separate RFC with its own measurement.
- **Check the kill switch before paging.** Rejected — the sweep doesn't, and
  kill semantics are enforced at *send* time (`_degraded_send` absorbs when
  engaged, loudly). The row is immortal; it replays after re-arm. Enqueue is
  a DB write, not a page.

## 5. Pre-mortem (it is Oct 2027; this change caused an incident)

1. *The enqueue deadlocked the forwarder worker.* It can't — it is the same
   call, from the same worker thread, that `_max_age_sweep` already makes.
   `EventLog._enqueue_control_plane_page` takes `self._lock` briefly; the
   worker never holds that lock while calling `_retryable`.
2. *Double-page: a row dead-lettered by `_retryable`, then paged again by the
   sweep.* Can't happen — `_retryable`'s branch returns immediately after
   `_dead_letter`; the row's status is `dead_letter`, and the sweep only
   scans undelivered (`queued`/`in_flight`) rows. Terminal state is terminal.
3. *The loud path raised and the row was lost.* The try/except prints to
   stderr and the row is *already* dead-lettered before the page is
   attempted — same ordering as the sweep. A sick loud path never
   resurrects.

## 6. Tests (verify-before-trust)

- `tests/test_durable_forwarder.py`: new test drives `_retryable` into the
  max-age branch and asserts a `forwarder_dead_letter` control-plane row
  exists in the outbox (priority 1, immortal). Existing sweep tests already
  assert the sweep path's page — both paths now covered.
- Failure-proof of the gate: the test fails on the pre-fix code
  (demonstrated by stashing the one-line change — never `git stash` in the
  shared clone; use a temp worktree/diff instead).

## 7. Lineage

Google SRE *Monitoring Distributed Systems* — alerting on symptoms, and the
rule that a dropped paging intent is itself a page-worthy symptom; the
"dropped alert" is the highest-severity monitoring failure because it
silences everything downstream. Feynman 1974 — report what the instrument
says, including when it says nothing.
