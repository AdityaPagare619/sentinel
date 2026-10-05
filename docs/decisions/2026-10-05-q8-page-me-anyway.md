# Decision: Q8 — "page me anyway" → WIRE IT as audit-logged override

**Date:** 2026-10-05 IST · **Decided by:** Aditya (founder; T+0 full-authority release)
**Type:** 1 — irreversible product-surface commitment (a new write action
on the platform API; an explicit human override of the decision engine).
Labeled explicitly.

## Decision
Wire the appeal control as an audit-logged override → real paging path
(12H-PLAN R-20 option (a)). The control becomes a first-class write
action on the platform API: the operator's explicit "page me anyway"
creates an audit-logged override record and routes to the real paging
path, with the override visible in the decision's evidence trail. An
explicit human override of the engine is honest; a control that pretends
to act but doesn't would be the fake-demo class of lie.

## Alternatives considered and rejected
- **Option (b): removal from the live build:** was the documented
  honesty fallback (12H-PLAN §5: "fallback: REMOVE the control from the
  live build"). Rejected because the override is genuinely useful — an
  operator who has context the engine lacks must have a real escape
  hatch, and an audit-logged one is strictly more honest than a missing
  one. Removal stays as the fallback only if wiring proves unsafe.
- **Wire without audit logging:** rejected — an unlogged override of a
  safety engine is unaccountable power; the audit record is the point.
- **Wire to a simulated path:** rejected — that would be the
  simulated-spill lie (forwarder-byok.md §2.5) in a new costume.

## Dissent on record
Abuse potential was raised: an override that pages unconditionally can
become the "just page me for everything" button, re-creating the
alert-fatigue problem Sentinel exists to kill. Answered by the audit
trail (every override is attributable and reviewable) and by scoping
the control to per-decision override, not a global "page everything"
mode. If audit review shows the control becoming a fatigue vector, that
is a reversal trigger (below).

## Single-threaded owner
ui-1 (R-20 both-options design, per 12H-PLAN §1), with plat-1 for the
platform API write-action surface. Reviewers: Prism + Vault (write
surface, per §1.6 — Vault sign-off required on the write surface).

## Reversal conditions
Reopen if: (a) audit review shows the override becoming a systematic
fatigue vector (operators paging everything) — then re-open as
wire-vs-remove with the abuse data; (b) the audit logging proves
unreliable (overrides without records) — then the control is removed
until logging is trustworthy, per the honesty law. The fallback remains
removal, never silent degradation.

## Traceability
PIPELINE-REVISION.md §9 Q8 · R-20 · prism-ui.md P1 ·
12H-PLAN §1 (ui-1), §5 (R-20 wiring → Phase 5).
