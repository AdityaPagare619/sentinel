# RFC: Delivery-confirmation honesty boundary for the legacy forwarder (2026-10-07)

Owner: FAANG engine team (single-threaded). Branch: `lane/faang-engine-20261007`.
Type: **Type 2** (documentation + contract downgrade; no behavior change). Explicitly: no gold-plating.

## Problem
Audit arch P1-3: legacy `Forwarder.forward` counts any 2xx as `forwarded=True` — no outbox, no retry. A crash between the gate decision and the PD POST loses the page, contradicting the module's "neither lose nor double-page" promise. The industry answer is the transactional outbox (Debezium / Chris Richardson). The design knows it; the receiver's hot path doesn't use it.

## Decision
1. **Document the honesty boundary in code** (the fix): `forward()`'s contract becomes explicit — `forwarded=True` means *the PD Events API accepted the POST (2xx)*, matching PagerDuty's own 202 semantics (already documented this way for `DurableForwarder._confirmed`). It does NOT mean the human's phone rang. A crash before the POST loses the page; the *decision* record always survives in the audit log (sqlite, committed before forward), so a lost page is detectable by reconciling `decision_made(page_now)` against `forward_confirmed` — the reconciliation is noted as a future option, not built here.
2. **Downgrade the module promise**: the legacy forwarder is an at-most-once relay with durable decision records — the "neither lose nor double-page" language is removed from its contract where it overclaims.
3. **Defer the outbox migration (no gold-plating).** The transactional outbox already exists in this codebase (`DurableForwarder`: outbox table, worker, retry, dedup — the Chris Richardson pattern). Migrating the receiver's synchronous hot path to it is a **Type 1** decision (new failure modes: outbox DB on the ingest path, worker lifecycle, replay semantics) that the current evidence does not demand. The outbox "does not promise exactly-once delivery… it promises no lost intent" — and for the legacy relay, intent is already durably recorded in the audit log; what the outbox would add is automatic *recovery* of the send, at the cost of a stateful hot path.

**Re-entry conditions** (when this decision is revisited — written now so it isn't revisited on vibes):
- a production incident where a page was decided but never sent and the loss was *not* caught by reconcile/alerting; or
- the receiver's reliability target is raised to at-least-once delivery as a committed product requirement (not an audit aspiration).
Either triggers a new RFC to migrate the receiver to `DurableForwarder`, which already implements the pattern.

## Alternatives considered and rejected
- **Transactional outbox for the legacy path now:** rejected — Type 1 machinery for a loss window bounded by process lifetime, with the decision record already durable; the codebase already owns the pattern where it's needed. This is the "when NOT to use" case: the overhead is not justified by the reliability requirement *as committed*.
- **Synchronous retry in `forward()`:** rejected — retries on the webhook handler thread couple ingest latency to PD availability and still don't survive a crash; strictly worse than the outbox on both axes.
- **Claiming at-least-once without the machinery:** rejected — the audit's core complaint; the honesty law forbids it.

## Pre-mortem ("this failed in production — why?")
1. **A page is lost to a crash and nobody notices:** mitigated by durable decision records + the documented reconcile query (`decision_made` with action `page_now` and no later `forward_confirmed`); the watchdog lane owns detection.
2. **A reader misreads `forwarded=True` as "human paged":** mitigated by the docstring boundary, stated in the same words as the durable forwarder's `_confirmed` ("does NOT mean the human's phone rang").

## Verification
- Code review: docstrings on `forward()`, `forward_raw()`, and the module header state the boundary; no "neither lose nor double-page" overclaim remains in the legacy forwarder's contract.
- No behavior change: existing `test_forwarder.py` passes unmodified (the contract change is words, not wire).

## Rollback
Words-only change — revert the docstring commit.

## Lineage
Chris Richardson, *Microservices Patterns* (2018): "The Transactional Outbox pattern uses the database as a temporary message queue" — atomic business-write + outbox row, separate publisher. Practitioner consensus: the outbox "does not promise exactly-once delivery… it promises no lost intent" (technori.com/news/outbox-pattern/); "when NOT to use: … event loss is acceptable … and the complexity of the outbox pattern is not justified" (Caldis/frameworks outbox-pattern reference). PagerDuty Events API v2: 202 = accepted into the intake, not delivered to a human — our `forward_confirmed` already honors this.
