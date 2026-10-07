# RFC — Reword the "paging on uncertainty" decision record (preview-v2)

**Date:** 2026-10-07 · **Lane:** `lane/faang-uiux-20261007` (Team 5, UI/UX) · **Owner:** Petu (UI/UX lane)
**Type:** Type-2 (one-string copy change on a frozen artifact). **Gate:** served-byte diff of `/preview-v2/`.

## Problem

`preview-v2/index.html:488` (frozen judging artifact, gh-pages) records the
doubt-band decision as:

> `Score inside the doubt band — paging on uncertainty`

The audit's P1 called it out: at SEV1 with a 0.44-confidence score this reads
as an excuse generator — "we paged because we weren't sure" — instead of
stating the actual policy. The decision record is the artifact an operator (or
a postmortem) reads to learn *why the machine acted*; "paging on uncertainty"
explains nothing about the rule it applied.

The rule (code truth): `if(s - band < SEM.suppressBar) → PAGE`. The machine
may only stay quiet when it is sure enough; anything inside the doubt band
goes to a human. This is the control principle's fail-safe direction — the
catastrophic automatic action is *suppression*, so doubt resolves to the
human.

## Decision

Replace the `plain` string (decision id `doubt` unchanged — the ledger's
stable rule vocabulary is not touched):

> `Judge score landed in the doubt band (±0.09) — too close to call, so a human decides. Doubt pages.`

What changed and why: it names the mechanism (doubt band), the measurement
(±0.09, rendered from the constant in the live file), and the policy in one
sentence ("too close to call, so a human decides"), and it closes with the
three-word policy ("Doubt pages.") that already anchors the sim console's WHY
`gate` popover. An operator reading this in the proof ledger learns the rule,
not an excuse.

The same string exists in the live v2 source (`platform/ui-v2/index.html:505`)
with identical semantics; it is reworded there identically for consistency —
staging and preview-v2 then *agree* on the doubt policy, which the
design-ethics sweep requires (one policy, one sentence, all surfaces).

## Alternatives considered

1. **Delete the record / say only "PAGE".** Rejected: the ledger's contract is
   that every decision carries a human-readable reason; "PAGE" alone is a
   verdict without a rationale.
2. **Keep "paging on uncertainty" and add a policy doc link.** Rejected: the
   plain-language string *is* the policy for most readers; pushing the
   justification behind a link repeats the audit's "phantom research docs"
   failure (C14).
3. **Justify with the full gate inequality.** Rejected: `s − 0.09 < 0.60`
   exposes the internal bar as a product promise (same rejection as the
   doubt-axis RFC). The ±0.09 band is already public copy; the bar stays
   internal.

## Lineage

- Working Backwards pressure test (principal-governance): the decision record
  is the launch narrative of a machine action — it must survive the hardest
  operator question ("why did you wake me?") in plain language, before any
  implementation detail.
- Design-ethics checklist: human-final authority — doubt resolving to the
  human is the mechanism; the copy now says so.

## Pre-mortem (assume this failed in 30 days)

1. **"Too close to call" reads as machine incompetence to an exec audience.**
   Mitigation: the sentence leads with the measurement (score, band) and ends
   with the policy; it is the same register as the rest of the ledger.
2. **preview-v2 edit violates the freeze.** Mitigation: the freeze is on the
   artifact's *design* (Petu's judging surface), not on a copy string the
   wave coordinator explicitly ordered fixed; the change is one string,
   recorded here and in the served-byte diff.
3. **The live file and preview-v2 diverge again later.** Mitigation: both
   strings are now identical; the sweep checklist records them as a pair.

## Rollout / verification

Edit the string in `preview-v2/index.html` on the gh-pages worktree and in
`platform/ui-v2/index.html`; rebuild; served-byte asserts: "paging on
uncertainty" = 0 on all surfaces; "Doubt pages." present in `/`, `/staging/`,
`/preview-v2/`. Rollback: one commit revert + rebuild (Type-2).
