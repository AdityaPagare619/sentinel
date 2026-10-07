# RFC — Design-system backstage: delete dead gh-pages assets, test the banner contract, cover build-v2.py

**Date:** 2026-10-07 · **Lane:** `lane/faang-uiux-20261007` (Team 5, UI/UX) · **Owner:** Petu (UI/UX lane)
**Type:** Type-2 (gh-pages tree change + new tests; main-branch source untouched
except tests). **Gate:** served-byte diff + `pytest`.

## Problem (three backstage findings from the audit's design-ethics delta)

1. **`tokens.css` referenced ZERO times by the shipped console.** Verified
   2026-10-07: `git grep "tokens.css" origin/gh-pages -- '*.html'` → zero
   hits; served bytes of all four surfaces contain zero `assets/` references.
   The token file describes a different (violet) theme and was never wired.
2. **Dead v1 assets are still publicly reachable on gh-pages** (`assets/`,
   `staging/assets/`, `staging/api/decision/*.json` — 99 of 103 branch files).
   They reference only each other; no shipped page loads them. Worse, they
   carry the Jev-audit X1 ordinality violation (`assets/view-cal.js`:
   "Expected Calibration Error", "Reliability diagram", `P(p1)=` language)
   on a public URL — dead-but-public is public.
3. **The banner contract is a substring assert inside the builder**, and
   `build-v2.py` has zero test coverage. The audit: "can't fail a dishonest
   build." (The builder has since been strengthened to parse the actual
   `<html>` element, but it still runs untested.)

## Decision

1. **Delete, don't wire.** The shipped console is a single-file bundle whose
   token system is inline CSS custom properties (`var(--quiet)`,
   `var(--ink-3)`, …). Wiring `tokens.css` (a different theme) would be a
   visual redesign — explicitly forbidden (Aditya's law: engineering
   decisions, not taste). Chesterton's fence checked: the files' only
   consumer is the retired v1 console; nothing references them. So: remove
   `assets/`, `staging/assets/`, and the unreferenced `staging/api/`
   fixtures from the gh-pages publish tree. `preview-v2/index.html` is
   self-contained (zero external refs — verified) and survives intact.
2. **Promote the banner contract to a real test.**
   New `tests/test_gh_pages_banner.py`: builds via `build-v2.py` into a
   temp dir and asserts on the *output bytes* —
   - prod `<html>` carries `data-mode="production"` + the exact
     `data-backend`; staging `<html>` carries no `data-mode`;
   - prod bytes contain `aria-label="Production console` and zero
     `aria-label="Simulated mode"`;
   - staging bytes are byte-identical to the v2 source (verbatim ship);
   - the `±doubtBand` label renders from the constant (no hardcoded twin).
3. **Cover the builder.** The same test module covers `build-v2.py`'s
   behaviors: backend injection, sim-band swap, loadtest-dashboard handoff,
   missing-source failure. This closes "zero test coverage for the builder
   you depend on."

## Alternatives considered

1. **Wire tokens.css into the console.** Rejected: different theme, visual
   redesign, forbidden by standing law; and the console already has an
   inline token layer — two token systems is worse than one.
2. **Keep the dead assets "for history."** Rejected: git history is the
   archive; a public URL is not an archive. The ordinality-violating
   calibration assets actively contradict shipped law (AC-8c).
3. **Document "why not a real test" instead of writing one.** Rejected: the
   test is cheap (one build into tmpfs, ~10 asserts) and the banner contract
   is exactly the class of invariant that must fail a dishonest build.

## Lineage

- Atomic design systems (principal-governance §3): tokens live in one
  versioned place — here, the single-file bundle's `:root`. A second,
  unreferenced token file is not a design system, it is drift.
- "Code is a liability" (principal-systems software constitution): the best
  PR deletes code — 99 dead files deleted.
- Verify-before-trust (wave law): the contract test reads output bytes, not
  builder exit codes.

## Pre-mortem (assume this failed in 30 days)

1. **Something still referenced the deleted assets** (e.g. a doc deep-link).
   Mitigation: repo-wide grep for `assets/` and `staging/api/decision` before
   deletion; GitHub Pages 404s are loud, not silent — and nothing in the four
   served surfaces references them today.
2. **The new test is flaky** (builds real files). Mitigation: builds into
   `tmp_path`, asserts bytes not timing; no network.
3. **preview-v2 breaks.** Mitigation: it is copied verbatim (untouched by the
   builder) and its self-containment is asserted pre-deletion; post-publish
   curl verifies 200 + unchanged size.

## Rollout / verification

- Repo: add `tests/test_gh_pages_banner.py`; run `pytest` (new + existing).
- gh-pages: rebuild with `build-v2.py --backend <stable alias>`
  `--loadtest-dashboard <current dashboard>`; delete dead trees in the publish
  worktree; keep `preview-v2/`; push; verify served bytes (200s, sizes,
  banner asserts, zero `assets/` refs).
- Rollback: revert the gh-pages commit (branch is append-only history);
  builder/tests are Type-2 additive.
