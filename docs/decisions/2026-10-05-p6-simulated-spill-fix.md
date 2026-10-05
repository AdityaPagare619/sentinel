# Decision: P6 — simulated-spill replay honesty (forward_failed lie fix)

**Date:** 2026-10-05 IST · **Lane:** fwd-1 · **Decided by:** fwd-1 (lane owner)
**Type:** 2 — reversible (one-function change in `spill.py`, test-covered,
no event-vocabulary or API change). Labeled explicitly.

## Decision
`replay_spills()` (src/sentinel/spill.py) maps a simulated absorption
(`simulated: true` / `pd_outcome == "simulated"`) to **`forward_confirmed`
with `simulated: true` in the body and no `error_class`** — never
`forward_failed`. This is the P6 fix for the named honesty incident
(forwarder-byok.md §2.5: simulated degraded pages replaying as
`forward_failed`). It implements the C6 ForwardReceipt contract §2.2
(consumer-owned, lane/prep-contracts-act): `simulated:true` ⇒ outcome
"accepted", `error_class` forbidden; any path emitting `forward_failed`
for a page never attempted on the wire falsifies the contract.

The fix also carries a structural guarantee: a defensive assertion fires if
a simulated record is ever mapped to `forward_failed`, however the branch
code evolves.

## What was found in the code (tools-first)
- `send_direct()` (forwarder.py:758) absorbs simulated degraded pages and
  writes the spill with `simulated: True`, `pd_outcome: "simulated"` —
  the write side already labeled honestly.
- `replay_spills()` (spill.py) computed `accepted = rec.get("pd_outcome")
  == "accepted"` and replayed everything else as `forward_failed` —
  dropping the `simulated` flag and lying in the durable audit trail.
  **This was the misrecording path.**
- All other `forward_failed` sites (forwarder.py `_retryable`,
  `_dead_letter`, `_secondary_receipt`, `_safe_requeue`, crash-window
  requeue) record genuine wire attempts with honest error classes —
  audited, left untouched.

## Alternatives considered and rejected
- **New event type `forward_simulated`:** widens the Type-1 event
  vocabulary (eventlog.py, evlog lane's file) for a case the C6
  consumer-owned contract already settles as `outcome: accepted` +
  `simulated` flag. Rejected — the contract is the coordination mechanism
  (OPERATING-RULES §1.1); a lane does not widen another lane's vocabulary
  unilaterally.
- **Fix at the display/UI layer only:** the lie would persist in the
  durable log and in every `forward_failed` metric. Rejected — the
  honesty law (OPERATING-RULES §3.4) demands the record be true at the
  source, not relabeled downstream.
- **Drop simulated spills without replaying:** silence about a degraded
  send is a different honesty violation (the spill is the only audit
  evidence). Rejected.

## Dissent on record
None (single-agent lane; C6 consumer owns the contract mapping).

## Single-threaded owner
fwd-1.

## Reversal conditions
Reopen if the evlog lane / C6 consumer re-ratifies a dedicated
`forward_simulated` event type (then remap the replay to it), or if
`append_event` validation ever rejects `simulated: true` bodies (then the
mapping must move with the vocabulary, via ADR).

## Observed edge (out of P6 scope, flagged to coordinator)
`write_spill` names files `spill-{ms}-{pid}.json` — two spills in the same
millisecond from one process collide and the second overwrites the first
(observed while testing). Pre-existing, not the P6 incident; recommend a
lane owns it (monotonic counter or uuid suffix) — it can lose a degraded
audit record under burst conditions.

## Traceability
P6 (12H-PLAN §1 fwd-1) · C6 ForwardReceipt v0.1 §2.2 ·
OPERATING-RULES §3.4 (honesty law) · tests/test_durable_forwarder.py
TestSimulatedSpillHonesty.
