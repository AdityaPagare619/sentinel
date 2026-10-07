# RFC — Unify suppression vocabulary to "handled quietly"

**Date:** 2026-10-07 · **Lane:** `lane/faang-uiux-20261007` (Team 5, UI/UX) · **Owner:** Petu (UI/UX lane)
**Type:** Type-2 (copy change, reversible in one rebuild). **Gate:** served-byte diff on all four surfaces.

## Problem

The shipped v2 console uses four different terms for the same machine action
(suppression), depending on which card the operator reads — found by exact
served-byte grep on `platform/ui-v2/index.html` @ `ce6a709`:

| Term | Sites |
|---|---|
| "handled quietly" | headline (`#quietCount` label, aria-label, WHY popover) |
| "silenced" | `#quietCount` textContent (`d.quiet+' silenced'`), ledger title (`Silenced — <rule>`), empty state, storm-card contrast copy, code comments |
| "counted as problems never alerts" | open-problems clarifier (pulse header + WHY `open-n`) |
| "silences" (noun) | WHY `quiet-n` body ("Open-ended silences are forbidden") |

An operator reading "12 handled quietly" on the pulse, then "12 silenced" in the
ledger, then "counted as problems never alerts" on open problems, cannot tell
whether these are three different mechanisms. This is the term-drift P1 the
brutal audit left open (§3, design-ethics: honest labeling).

## Decision

**One term: "handled quietly."** It is the quiet-instrument idiom (the console's
own design language), already the headline and the aria-label that screen
readers announce. "Silenced" dies — it overclaims (suppressions expire and are
appealed; they are handled, not silenced) and it collides with the deliberate
contrast copy on the storm card ("grouped is not silenced — one human paged").

Concretely, in `platform/ui-v2/index.html`:

- `$('#quietCount').textContent = d.quiet+' silenced'` → `d.quiet+' handled quietly'`
- Ledger title `Silenced — ${name}` → `Handled quietly — ${name}`
- Empty state "Nothing silenced yet." → "Nothing handled quietly yet."
- Storm card "grouped is not silenced — one human paged, the rest held with
  proof" → "grouped is not handled quietly — one human paged, the rest held
  with proof." (The contrast is real and load-bearing: grouping still pages a
  human. It survives, in the canonical term.)
- WHY `quiet-n` body: "Open-ended silences are forbidden — each expires." →
  "Nothing stays quiet forever — each expires."
- Code comments mentioning "silence" updated to "handled quietly".
- "counted as problems never alerts" → "counted as problems, never as raw
  alerts." The problems-vs-raw-alerts distinction is a *different, legitimate
  axis* (attention units above raw events, design-ethics checklist); it is kept
  but punctuated so it cannot be misread as a third name for suppression.
  WHY `open-n` body updated identically.

Out of scope: preview-v2 (frozen judging artifact; only its explicitly-ordered
P1c copy changes).

## Alternatives considered

1. **"Suppressed" everywhere.** Rejected: it is engine jargon, not operator
   language; the console already committed to the quiet-instrument idiom in the
   winning v2 pass, and Petu's browser judging graded *that* idiom B+.
2. **"Silenced" everywhere.** Rejected: overclaims finality; suppressions are
   expirable and appealable. Also the aria-label already says "Handled
   quietly" — changing it would regress a screen-reader-visible fix.
3. **Leave the drift.** Rejected: the audit graded UI/UX 52/100 on exactly this
   class of drift; three names for one action is how operators stop trusting
   the ledger.

## Lineage

- Honest labeling (design-ethics checklist): one action, one name — the same
  reason incident-review templates standardize "mitigated" vs "resolved".
- Statuspage anchor ("the only honest status page is automated",
  dev.to/mthrn/our-status-page-lied-to-us): automation honesty extends to
  *vocabulary* honesty — a status surface that renames the same state across
  cards trains operators to distrust all of them.

## Pre-mortem (assume this failed in 30 days)

1. **A "silenced" string survives somewhere** (a toast, a title) and an
   operator asks support which count is real. Mitigation: post-build grep for
   `\bsilenc` across built bytes; the new banner-contract test asserts zero
   occurrences.
2. **"counted as problems, never as raw alerts" still confuses.** Mitigation:
   it is typographically fenced (em-dash, dim styling) and the WHY popover
   explains it in one sentence.
3. **Screen-reader regression.** Mitigation: the aria-label was already
   "Handled quietly"; we are converging the visible text *toward* it.

## Rollout / verification

Rebuild gh-pages with `build-v2.py`, then served-byte asserts:
`grep -c silenced` = 0 on `/` and `/staging/` (preview-v2 excluded as frozen);
`grep -c "handled quietly"` ≥ baseline on both; aria-labels unchanged.
Rollback: one commit revert + rebuild (Type-2).

## Residual

preview-v2 keeps old copy by explicit freeze order — flagged in the lane's
final report for Petu's production-shift call.
