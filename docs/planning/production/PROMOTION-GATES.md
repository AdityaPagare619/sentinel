# PROMOTION GATES — from honor-system to checklist-enforced

**Owner:** Team 3 platform/infra · **Date:** 2026-10-07 ·
**Lane:** `lane/faang-platform-20261007`

> The audit: "Promotion gates are honor-system (`pre-pr-gate.sh` real
> but unenforced; entrypoint-import gate needs a box-side pre-merge
> hook, still TODO)." This doc is the enforcement mechanism. The gate
> is `scripts/ops/merge-checklist.sh`. A merge without its MERGE-RECORD
> is not a merge — it is a push with ambitions.

## 1. The mechanism (three layers)

**Layer 1 — the script gates the machine-checkable.** `merge-checklist.sh
<commit>` runs, in order: clean-tree check → `pre-pr-gate.sh` GREEN
evidence (reused via `--gate-log` or run fresh, never `--fast`) →
entrypoint-import gate (`py_compile` on every entrypoint + WSGI import
smoke of `app`, `safety_api`, `ops_health` via `_pkg`) → TEAM 6 gate
(§3) → secrets re-sweep. Any machine failure → exit 1, no merge.

**Layer 2 — the human checklist gates the judgment calls.** The script
prints the standing prohibitions and honesty items; the merger confirms
each with `--yes-human`. Without it → exit 3. The items: different-agent
review, no unmerged-lane evidence claims (X-C rule), the five standing
prohibitions (preview-v2 kept, no prod flip, no deployed-tier safety
changes, zero real-PD, key hygiene + ₹0), kill-switch copy honesty,
docs-changed-with-behavior.

**Layer 3 — branch protection + review routing (GitHub).** Standing
order is local gates only (no GitHub Actions CI on this repo), so
enforcement is procedural, not webhook-driven:

- `main` requires pull requests; direct pushes are reverted on sight.
- The PR body MUST contain the MERGE-RECORD block from the script.
  A PR without it is closed, not reviewed.
- Petu is a required reviewer on every PR to `main` (FAANG wave:
  nothing merges without his review).
- The wave coordinator sequences merges; lanes never self-merge to
  `main`.

This is weaker than a required-status check and the doc says so: the
backstop is Petu's review + the coordinator's sequencing, both human.
The script makes the evidence cheap to produce and expensive to fake;
the humans make faking it pointless.

## 2. What a human checks (the merger's reading list)

1. **The gate evidence names the commit.** A GREEN run on another
   commit is not evidence for this one.
2. **Different-agent review**: the builder never reviews their own lane.
   The reviewer re-runs `merge-checklist.sh --gate-log <dir>` — trust,
   then verify.
3. **X-C rule**: every number, artifact, or "proven" claim in the PR
   exists on the merged tree. Lane-only evidence names its branch or
   the claim is struck.
4. **Prohibitions** (from the FAANG wave order): `/preview-v2/` stays
   until Petu's flip order; production is not flipped live by a lane;
   safety endpoints on the deployed tier are not touched; zero real
   PagerDuty contact ever; the Jev key appears in no file, log, env
   dump, or browser JS; ₹0 — no paid service is introduced.
5. **Kill-switch honesty**: any copy touching the switch matches the
   decided semantics (HALT all paging, fail-closed) AND the tier's
   actual wiring. On the deployed tier that means the NO-OP LEVER
   marking stays until the kill-state RFC is implemented and re-drilled.
6. **Docs bookkeeping**: behavior changes ship with doc changes; no
   phantom topology, no specified-not-built, no stale contradiction
   (the docs-honesty contradiction matrix is the checklist's ancestor).

## 3. TEAM 6 contract — test-automation half

**Interface** (agreed in writing here; countersign pending with TEAM 6
and the wave coordinator — Team 3 does not duplicate their gate):

- Entrypoint: `scripts/ops/team6-gate.sh <commit-sha>`.
- Contract: exit 0 = their automation gate GREEN for that commit;
  non-zero = RED. Stdout ends with lines starting `PASS:`/`FAIL:`
  (same dialect as `pre-pr-gate.sh`).
- Their internals are theirs: which suites, what coverage bar, what
  fixtures. Our checklist invokes; it does not inspect.
- If `team6-gate.sh` is absent or not executable, the merge-checklist
  FAILS with "not present" — a missing gate is a red gate, not a
  skipped one. (A gate that can be absent without consequence is
  honor-system with extra steps.)

**Open coordination item** (for the wave coordinator): TEAM 6 to
countersign this interface or propose the amendment, then land
`team6-gate.sh` on their lane. Until then every merge-checklist run
records `team6-gate: MISSING` — visible, not silent.

## 4. The entrypoint-import gate (the former TODO)

Box-side pre-merge hook, now implemented as stage 3 of the checklist:

- `py_compile` on every entrypoint: `platform/server/__main__.py`,
  `deploy/vercel/api/index-prod.py`, `deploy/gh-pages/build-v2.py`,
  `deploy/gh-pages/build-static.py`, `src/sentinel/receiver.py`.
- Import smoke in a subprocess (60 s timeout): `_pkg.load("app")`
  exposes `PlatformApp`; `_pkg.load("safety_api")` exposes
  `handle_kill`; `_pkg.load("ops_health")` exposes `handle_health`.
  (Import, not boot: no ports bound, no state touched.)

Rationale: the deployed tier's only code path is
`index-prod.py` → `platform/server`; a merge that breaks that import
chain ships a 500 to production. Compile + import is the cheapest
possible catch and it was still TODO.

## 5. Anti-decay

Gates rot into theater (principal-mindset §11). Decay signals, checked
by the coordinator monthly:

- A merge landed without a MERGE-RECORD → the Layer-3 backstop failed;
  investigate, don't shrug.
- `team6-gate.sh` still absent after the wave → the contract failed;
  escalate, don't normalize.
- The human checklist confirmed in under 60 seconds repeatedly →
  it's being clicked through; rotate the reviewer pool.
- Any `--allow-dirty` bundle reaching a deploy → the provenance
  contract failed; the deploy is rolled back and the record names the
  operator.

---

*Lineage: DORA/Accelerate (change-failure rate is the gate's metric —
the checklist exists to move it down); Google SRE (error budgets need
an enforcement point — this is ours); aviation CRM (junior crew may
challenge the merge — the checklist is the ladder's bottom rung).*
