# RFC — Label the doubt-band score axis (quiet groups + ledger + detail)

**Date:** 2026-10-07 · **Lane:** `lane/faang-uiux-20261007` (Team 5, UI/UX) · **Owner:** Petu (UI/UX lane)
**Type:** Type-2 (copy change, reversible in one rebuild). **Gate:** served-byte diff.

## Problem

The v2 console renders judge scores as bare ordinals — `least sure: 0.56` on
quiet-group cards, `0.66 — ordinal, not a probability` in the proof ledger,
`0.77 ±0.09 — ordinal: ranks decisions, not a probability` in the problem
detail drawer. The audit's staging pass found these "undecodable at a glance":
the number carries no axis. An operator cannot tell whether 0.56 is high or
low, what the scale bounds are, or what moves a decision across the page line.

What the number *is* (code truth, `gate()` @ `platform/ui-v2/index.html:503`):
the judge's reported score, an **ordinal** (ranks decisions; never a calibrated
probability, per the ordinality law). The gate pages when
`score − doubtBand < suppressBar`, i.e. scores near/below the band mean the
machine is unsure and a human decides — "doubt pages". So: **low = unsure
(page), high = sure (stay quiet)**.

The "not a probability" half is already labeled; the axis half is missing.

## Decision

Label the axis wherever a score is rendered, in plain words, without exposing
`SEM.suppressBar` (deliberately an internal gate bar — "never displayed as a
product constant"):

- Quiet card (`renderQuiet`): extend the existing note —
  "Least-sure decisions first inside each group." gains
  "Scores run 0 (unsure) → 1 (sure); the doubt band (±0.09) around a score
  pages a human." Same quiet-instrument voice, no new widget.
- Proof ledger (`renderLedger`, "Judge score" row): `0.66` →
  `0.66` + "ordinal, not a probability — 0 unsure, 1 sure".
- Problem detail (`renderDetail`, "Judge score" row): append
  "· 0 unsure, 1 sure" to the existing "±0.09 — ordinal: ranks decisions,
  not a probability".

No visual redesign (Aditya's law): typographic labels only, no new sliders,
no chart widgets. The axis is text because the scores are text.

## Alternatives considered

1. **Visual axis widget (gradient bar with ticks).** Rejected: taste-level
   redesign risk on the winning v2 pass; the scores render as text rows, and a
   text axis keeps the quiet-instrument idiom (dense where the operator needs
   density).
2. **Expose the internal page line (0.60) as the axis anchor.** Rejected: the
   constant is internal by design (round-1 drift note); anchoring the axis to
   it would turn an internal tuning bar into a product promise. The ±0.09 doubt
   band is already public copy; the axis anchors to 0/1, which are scale facts.
3. **Remove the numbers entirely ("just say unsure/sure").** Rejected: the
   whole console's contract is "every number answers why this number" (trace
   popovers). Hiding the score would violate it.

## Lineage

- Design-ethics checklist: honest labeling — a number without an axis is a
  decoration, not information. Same class as the audit's C8 (`dataAgeSec`
  computed, never rendered): computed-but-unexplained is theater-adjacent.
- LLM-as-judge overconfidence literature (ECE up to 74 — cited in the Jev
  audit §6): treating model scores as calibrated probabilities is
  industry-known-bad, which is why the ordinal framing and the axis both
  exist.

## Pre-mortem (assume this failed in 30 days)

1. **Operators read "0 unsure, 1 sure" as a probability anyway.** Mitigation:
   "not a probability" stays adjacent to every score; the WHY `gate` popover
   ("Doubt pages") is one click away.
2. **The ±0.09 label drifts from the constant.** Mitigation: the label is
   generated from `SEM.doubtBand` at render time (`±${SEM.doubtBand}`), not
   hardcoded — it cannot drift.
3. **Clutter.** Mitigation: the axis text reuses the existing `.dim` style and
   adds no new DOM depth.

## Rollout / verification

Rebuild; served-byte asserts: "0 unsure, 1 sure" (or the axis sentence)
present in `/` and `/staging/` at the three render sites; `±0.09` still
rendered from the constant (no hardcoded duplicate).
Rollback: one commit revert + rebuild (Type-2).
