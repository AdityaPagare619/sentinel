# RFC: Spend-meter reconciliation — `advisory.SpendMeter` (estimate) vs `sim_judge.JevSpendTracker` (actual)

**Status:** PROPOSED by FAANG principal wave, Team 2 (AI/ML), 2026-10-07.
**Type:** 2 — reversible; docstring contracts + one fail-closed gate line + test.
**Law:** FinOps hard-limit discipline — the industry default is alert-not-enforce;
our budget is a hard gate (`try_begin_call` before network I/O, permanent latch).
Two ledgers for the same dollars must not silently disagree.

---

## 1. Problem (audit P2/C3)

Two spend meters exist for Jev dollars:

| | `sim_judge.JevSpendTracker` (C2) | `advisory.SpendMeter` (Track 2 seam) |
|---|---|---|
| Semantics | **wire truth** — actual cost from response `input_tokens × $0.042/1M`; failed calls record 0.0 ("inventing usage would be dishonest") | **pre-authorization envelope** — `charge(estimate)` BEFORE the call; conservative by construction |
| Thread safety | lock-protected | dataclass, no lock (`AdvisoryDispatcher._charge` serializes via `_spend_lock`, but `_pre_call_gates` reads `blocked`/`session_usd` unlocked) |
| Latch | permanent; 0.0/negative/garbage budget → fail-closed to 0.0 | latches at `session_usd >= budget_usd`; no fail-closed constructor |
| Backs | `GET /api/v1/jev/spend` | nothing served yet — advisory directions are UNWIRED in production |

**Latent disagreement:** when advisory wires in, `decide_fn` =
`SimJudge.decide_advisory` → `SpendCappedJevClient.decide` charges the C2
tracker with **actuals**, while the dispatcher charges its own meter with
**estimates**. Same dollars, two ledgers, different latch points. The console
could show one while the gate enforces the other.

## 2. Anchors (web-verified 2026-10-07)

- **FinOps hard-limit vs alert-default:** the industry default is alert, not
  enforce — GCP: "reaching the budget or threshold does not affect the resource
  limitation, as they continue to operate normally"; AWS Budgets send
  notifications. Hard enforcement (quotas, policy-deny, billing-disconnect) is
  the stronger posture practitioners must build themselves. Our design is
  deliberately on the hard-limit side: the gate runs *before* network I/O and
  the latch is permanent within the process. The reconciliation must not weaken
  this — any ambiguity fails CLOSED (blocks), never open.
- **Estimate-vs-actual:** `estimate_cost_usd` prices the *estimated* input
  tokens; actuals come from the response usage block. Estimates are conservative
  (over-admission impossible from the estimate side), so the divergence is
  bounded and one-directional: `SpendMeter.session_usd ≥ tracker.session_usd`
  for the same call stream, modulo failed calls (estimate charged, actual 0.0).

## 3. Alternatives considered

- **A. Merge into one meter.** Rejected: the two answer different questions.
  The dispatcher's estimate envelope exists to enforce *per-direction daily caps
  and cost caps before the call* — policy. The tracker records *what the wire
  did* — ledger. Merging destroys the policy/ledger separation.
- **B. Post-call settlement (adjust estimate → actual).** Rejected for now:
  requires the dispatcher to observe actuals (it doesn't — `decide_fn` returns
  the response but the settlement path would couple Track 2 to C2's cost
  function); complexity without a wired consumer. Noted as the design to revisit
  IF advisory wires into a shared-budget deployment.
- **C (chosen). Explicit contract + fail-closed cross-check, no behavior change
  to the money path:**
  1. Docstring contracts on both classes naming their role (envelope vs ledger)
     and the divergence bound (`SpendMeter` may overstate; never understates).
  2. `AdvisoryDispatcher.__init__` gains an optional `tracker`
     (`JevSpendTracker | None`, default None — advisory stays standalone until
     wired); `_pre_call_gates` blocks when the tracker's `blocked` is set even
     if the estimate meter is not (fail-closed on disagreement). One gate line;
     the read takes no locks on the dispatcher side.
  3. Regression test: drive both meters with the same call script (including a
     failed call at 0.0 actual); assert `spend_meter.session_usd >=
     tracker.session_usd` (one-directional bound) and that a blocked tracker
     blocks the dispatcher even when the estimate meter is not blocked.

## 4. Decision

Implement C. Type 2: docstrings + one gate line + test. The money path
(`SpendCappedJevClient.decide` → tracker) is untouched.

## 5. Pre-mortem (it is 2027-10-07 and this failed)

1. **Advisory wires in and someone reads `SpendMeter` as the bill** →
   mitigated: docstring says ENVELOPE in the first line; `GET /api/v1/jev/spend`
   (the served surface) is the tracker only.
2. **The cross-check line introduces a lock-ordering deadlock** → mitigated: the
   check reads `tracker.blocked` (a lock-protected property that never calls
   back into the dispatcher); no lock is held across it.
3. **Estimates drift far above actuals and the envelope blocks early** →
   accepted and documented: conservative direction is the safe one; the bound
   is per-call and small (estimate vs actual input tokens).

## 6. Verification

- New test `test_spend_meters_reconcile` (failing-before: without the gate line,
  a blocked tracker does not block the dispatcher → test fails; passing-after:
  with it, blocks).
- Existing suites green: `tests/test_advisory.py`, `tests/test_sim_judge.py`.
- `GET /api/v1/jev/spend` shape unchanged (tracker only).
