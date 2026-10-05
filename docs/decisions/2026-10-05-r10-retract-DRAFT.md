# ADR-DRAFT: R-10 retract — `flags.json` stays unwired (DRAFT — lands ONLY if the T+9 drill fails)

**Status: DRAFT — pre-written per 12H-PLAN T+6 edge ("the fallback is
pre-written, not discovered"). This ADR lands if and only if the F1 kill
condition fires: the kill-switch drill FAILS on staging-lab by T+9.
If the drill passes, this file is deleted, not landed.**

**Date:** 2026-10-05 IST · **Owner:** dev-1 · **Type:** 2 — reversible.
**Decided by:** F1 verdict, Session B1 (disagree-and-commit) — the kill
condition, not a new debate.

## The kill condition (binding)

> "The kill-switch drill must pass on staging-lab by T+9. If it fails,
> the retract ADR lands at T+9 and a follow-up lane is registered the
> same hour. No third option, no silent slip." (12H-PLAN §0 F1, §2 T+9)

## Drill outcome (fill at T+9)

- [ ] Drill record: `ops/drills/2026-10-05-r10-kill-switch-drill.md`
- [ ] Verdict: PASS / **FAIL**
- [ ] Failure mechanism (honesty law — a failed drill is reported as a
>   failed drill with its mechanism, never dressed up):


## Decision (on drill failure)

`flags.json` is NOT wired into the live gate in the 12-hour wave. The
`lane/12h-dev-1-r10` branch is NOT merged. Concretely:

1. `flagctl.py` remains the writer of flag *intent* on disk; the operator
   applies kill-switch semantics through the existing knobs
   (`SENTINEL_SHADOW`, config reload, allowlist edits) — the interim
   posture `ops/devops-foundation.md` §2.1 already documents honestly
   ("Receiver integration status (honest)").
2. The `flags.json` contract (schema, `flagctl`, flip procedure, kill-
   switch asymmetry) stands unchanged — the contract was never the
   problem; the wiring was.
3. A follow-up lane is registered the same hour, owned by Relay
   (devops), scoped to the drill's failure mechanism above — not a
   re-attempt of the same wire, a re-design against the mechanism.

## What the drill proved (on failure)

The failure mechanism above is the finding. It is recorded in
`ops/decision_log.md` and the lane registry row for `lane/12h-dev-1-r10`
is marked `killed` with this ADR as the reason (history is not
rewritten — OPERATING-RULES §5.4).

## Alternatives considered and rejected

- **Extend the deadline past T+9.** Rejected: the kill condition is
  dated; "no third option, no silent slip" (F1). A slip without a
  re-authorized date is drift (principal-mindset §6).
- **Merge the wiring without a passing drill.** Rejected: an undrilled
  kill switch is the rusted-shut failure mode with a merge commit on
  top. The honesty law forbids it.
- **Partial wire (kill switch only, drill the rest later).** Rejected at
  T+9: if the drill failed, the failure mechanism is not understood well
  enough to scope a partial landing. The follow-up lane may propose it
  with a new drill plan.

## Reversal conditions

Reopen when the follow-up lane's drill plan passes review AND the drill
passes on staging-lab. The wire is re-attempted, not resurrected — new
branch, new design doc against the failure mechanism.

## Traceability

F1 verdict (12H-PLAN §0) · 12H-PLAN §2 T+9 checkpoint falsifier ("no
drill record AND no retract ADR") · `docs/design/r10-flags-wire.md`
(superseded on failure) · R-10.
