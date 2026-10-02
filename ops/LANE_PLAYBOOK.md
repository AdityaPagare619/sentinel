# Sentinel — Lane Playbook

How work gets done: the lane lifecycle, branch naming, commit rules, PR rules,
and what "done" means per lane type. This is the working structure every lane
follows — no improvisation, improve by proposal + logged decision.

## 1. Lane lifecycle

1. **Brief.** Petu spawns the lane with a bounded brief: mission, files owned,
   interface contract (or the mock to build against), done-criteria, write-back
   requirement. No lane starts without a brief.
2. **Register.** The lane lead adds a row to `ops/lane_registry.md` (branch,
   owner, status `in flight`). A lane that isn't registered doesn't exist.
3. **Branch.** `git checkout -b lane/<lane>-<short-desc>` from the agreed base
   (usually the latest reviewed tip — ask Relay if unsure). One branch = one
   reviewable unit.
4. **Work in small commits.** Commit per meaningful unit; push the branch when
   the remote is reachable. A day's work never lives only on one agent's scratch.
5. **PR.** Against `main` (once the remote is live), using
   `.github/pull_request_template.md`: tests evidence, docs touched,
   Claim-Auditor pass on numbers, lane-crossing flags.
6. **Cross-agent review.** A DIFFERENT agent reviews — no self-merge, ever.
   Checklist: contracts conformed? secrets absent? numbers sourced? hot-path
   separation intact? Vault pass if ingress/egress/audit touched.
7. **CI green.** Full suite + repeatability probes (flip <2%, shuffle <3%) +
   kill-the-client + secrets-grep. Red = no merge, no exceptions.
8. **Merge + registry entry.** Squash-merge, delete the branch, flip the
   registry row to `done` with the outcome. Write-back or it didn't happen:
   code → branch, decisions → `ops/decision_log.md`, findings → `research/`.

## 2. Branch naming

`lane/<lane>-<short-desc>` — e.g. `lane/ui-river`, `lane/calib-api`,
`lane/docs-onboarding`, `lane/research-night-1`. Base-setup's branch was
`base/setup-docs` (one-off). Keep names short, kebab-case, no dates.

## 3. Conventional commits

`feat|fix|docs|test|chore(scope): <short imperative summary>` — e.g.
`feat(gate): expected-cost threshold policy`, `docs(brief): 2026-10-03 morning`.

- Imperative mood, <72 chars, scope in parens.
- Body: *why* (the decision), one or two lines.
- Footer when a decision was logged: `Decision: ops/decision_log.md#<date>-<slug>`.
- Scopes: the module (`gate`, `receiver`, `audit`), the surface (`river`,
  `simulator`, `explorer`), or the function (`research`, `brief`, `ops`, `ci`).

## 4. PR rules

- PRs under ~300 lines. Small PRs review in minutes, not days.
- No self-merge. Review SLA: minutes, not days.
- Green CI required — full suite, probes, kill-the-client, secrets-grep.
- Squash-merge → delete the branch. `main` history is a clean sequence.
- Crossing into another lane's files → flagged in the PR description, never
  silent. The file owner makes the edit.

## 5. What "done" means per lane type

| Lane type | Done iff |
|---|---|
| **Code** (engine/platform) | tests green (incl. new behavior) + kill-the-client green + docs updated + PROGRESS.md entry + demo-able + no secrets + Claim-Auditor pass on numbers |
| **Research** | dated note in `research/<dept>/` with every claim cited (URL + access date), facts separated from inferences, honest-limitations section; implications as proposed ADRs in the synthesis — never applied to frozen specs silently |
| **Docs/UX** | the artifact exists and is reviewable; onboarding timed where applicable; Prism's anti-slop bar (no lorem, no fake data presented as real, every dashboard number source-labeled) |

## 6. Single-owner rule (hard)

`src/`, `tests/`, `PROGRESS.md`, `ARCHITECTURE.md` belong to the build
coordinator while mid-flight — nobody else creates or edits those paths.
Each lane owns its files; crossing lanes requires a flagged PR, never a
silent edit. (LOCAL-OPS.md §2.)
