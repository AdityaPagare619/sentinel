# Decision: Q6 — provider test keys → SPLIT DECISION

**Date:** 2026-10-05 IST · **Decided by:** Aditya (founder; T+0 full-authority release)
**Type:** 1 — irreversible secret-handling commitment (a live vendor key
enters the team's custody; PagerDuty coverage posture is declared).
Labeled explicitly.

## Decision
Split by vendor, on the evidence:

**Jev: AUTHORIZED for drift-harness live runs only.** Petu holds a real
Jev key; its use is authorized strictly for the R-13 drift harness's
Jev-side live runs, under three non-negotiable constraints designed into
R-13: (1) a hard cost cap — every live call is budgeted before it runs,
and the harness refuses to exceed it; (2) every live call is logged
(count, timestamp, purpose) with the spend reconcilable; (3) the key
value never appears in logs, commits, cassettes, or error strings.
Principle: never mock what you can run live — the Jev side gets real
coverage because the key exists.

**PagerDuty: NO key exists — FakePD-only battery stands.** Aditya has no
PagerDuty account and cannot create one (work email required). No live
PD coverage is claimed, ever. The drift harness runs the FakePD-only
battery plus the documented quarterly manual live-shape check against
PagerDuty's published Events API v2 docs. Principle: never claim live
coverage on the PD side.

## Alternatives considered and rejected
- **No live keys at all (mock everything):** rejected for Jev — mocking
  a vendor you can actually call is the precise theater the honesty law
  forbids; the key exists, the cost is budgetable, use it.
- **Provision a PagerDuty test key:** impossible — no account exists and
  none can be created. Not a choice; a fact. Rejected as unavailable.
- **Use the operator's (end-user's) PD key for harness runs:** rejected —
  operator keys are per-user secrets; the harness must never touch them.
  The decision explicitly forbids this.
- **Uncapped Jev live runs:** rejected — unbounded vendor spend on an
  automated harness is how budgets die quietly. The cap is part of the
  authorization, not a suggestion.

## Dissent on record
Cost anxiety was raised and answered by the cap design: the authorization
is void the moment spend cannot be reconciled. No dissent on the split
itself — the asymmetry (key exists vs. key doesn't exist) decides it.

## Single-threaded owner
race-1 (R-13 drift-harness design, per 12H-PLAN §1). The cost-cap
mechanism is part of the R-13 design doc acceptance criteria.
Reviewer: Vault (key-handling surface — §1.6 triggers on anything
touching keys/secrets).

## Reversal conditions
Reopen if: (a) Jev spend exceeds the cap or cannot be reconciled — the
authorization suspends immediately pending review (this is a kill
condition, not a discussion); (b) the key value appears in any log,
commit, or cassette — same immediate suspension plus incident review;
(c) a PagerDuty key becomes available — then the PD half re-opens as
"provision sacrificial-service key under the same three constraints."
The FakePD-only posture for PD is not revisited without (c).

## Traceability
PIPELINE-REVISION.md §9 Q6 · R-13 · forwarder-byok.md P5 ·
testing-qa.md P2-2 · 12H-PLAN §1 (race-1), §5 (R-13 live runs → Phase 4).
