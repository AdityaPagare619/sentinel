# Decision: Q3 — policy unification → option (a), canonical `PolicyVersion.content`

**Date:** 2026-10-05 IST · **Decided by:** Aditya (founder; T+0 full-authority release)
**Type:** 1 — irreversible trust-root change (what the kernel accepts as policy;
governs every suppression decision). Labeled explicitly.

## Decision
`PolicyVersion.content` becomes the canonical thresholds.json. The kernel
refuses unattested generations — an unattested policy change cannot reach
the suppression path, full stop. The D8 fail-closed flip is included: any
policy-state ambiguity resolves to fail-closed (page), never to a permissive
default. This closes R-1: the attested lifecycle governs the policy object
the kernel actually reads, ending the governance theater where B3/Ed25519
ceremony guarded an object nothing enforced.

## Alternatives considered and rejected
- **Option (b): 2-attestor PR rule with B3 expiry/freeze on thresholds.json:**
  cheaper to build, and was the documented deputy-authority floor. Rejected
  because it leaves the core theater standing — the kernel would still read
  a file whose attestation is a process convention, not an enforced
  property. A PR rule is bypassable in exactly the way the current
  thresholds.json edit is.
- **Status quo (unattested thresholds.json):** the R-1 finding — rejected
  outright.
- **Defer to Phase 2+:** rejected — R-1 is the highest-ranked change in
  PIPELINE-REVISION §6; every suppression decision until it lands runs on
  unattested policy.

## Dissent on record
The cost side was argued honestly: option (a) requires kernel-level
refusal logic, content-hash binding verification on the hot path, and a
migration for the current thresholds.json — materially more build than
(b). The verdict accepts the cost because the threat model (R-18) names
unattested policy mutation as the highest-stakes integrity failure.
No dissent against (a) on correctness grounds.

## Single-threaded owner
gate-1 (R-1 option designs, per 12H-PLAN §1). Reviewers: Oracle + Forge
(gate kernel, per OPERATING-RULES §1.6); Tripwire on every policy-change
PR (standing). Phase 1 build owner to be named at T+12 handoff.

## Reversal conditions
Reopen if: (a) canonical-content enforcement proves unimplementable
without breaking the policy lifecycle's operability (e.g., no safe
migration exists for the live thresholds.json) — then the decision
re-opens as (a)-vs-(b) with the blocking mechanism named; (b) the
attestor quorum itself is compromised — then the trust root, not this
decision, is re-examined. Cost overruns alone do not reverse a Type-1
trust decision.

## Traceability
PIPELINE-REVISION.md §9 Q3 · R-1 · deterministic-gate.md P1/P2 ·
12H-PLAN §1 (gate-1), §5 (R-1 build → Phase 1, D8 included).
