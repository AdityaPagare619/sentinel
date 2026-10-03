# Sentinel — CI Repair: `startup_failure` on every run (Tripwire investigation)

**Date:** 2026-10-03 ~00:30 IST, updated ~00:55 IST, **updated again ~13:45 IST
(coordinator).**
**Investigator:** Tripwire.
**Status:** ESCALATED — repo-level GitHub platform bug. The path-rename fix was
FALSIFIED by experiment (see "13:15–13:40 IST experiments" below). GitHub
Support escalation required (workflow id 373379524). Manual local CI is the
operative quality gate until GitHub resolves.

## Symptom

18/18 GitHub Actions runs `startup_failure` (2026-10-02 18:41:07 → 18:59:38) —
including a minimal probe workflow and, crucially, **two fresh runs from
`lane/tripwire-acceptance` at 18:59:30/18:59:38 UTC, six hours after the good
workflow record existed**. Every run: 0 jobs, 0s duration, empty run name.

## Evidence (all verified via `gh` CLI)

1. `gh run list -R AdityaPagare619/sentinel` → all runs `completed/startup_failure`.
2. `gh api repos/AdityaPagare619/sentinel/actions/workflows` → **two** workflow
   records:
   - `373379524` — name `''`, path **`BuildFailed`**, state `deleted`,
     created 2026-10-02T18:41:07Z
   - `373379827` — name `'Sentinel CI'`, path `.github/workflows/ci.yml`,
     state `active`, created 2026-10-02T18:41:30Z
3. **Every** run — push, pull_request, probe, and the two fresh Tripwire runs —
   carries `workflow_id: 373379524`. New events 6h later still bind to the dead
   record.
4. The workflow file bytes are **identical** on `base/setup-docs` (the 18:41:07
   push), `main` (f4aa005), and every lane branch — `diff` clean, parses with
   PyYAML, no BOM, no CRLF.
5. `gh api .../actions/permissions` → `{"enabled": true, "allowed_actions": "all"}`.
   Not permissions. Not YAML.

## Root cause (confirmed by experiment)

`path: BuildFailed` is GitHub's marker for a workflow file that failed
**server-side ingestion** on first registration. At 18:41:07 — the first-ever
push to this brand-new repo — GitHub's workflow compiler choked and registered
a dead `BuildFailed`/`deleted` record for `.github/workflows/ci.yml`. The
byte-identical file re-ingested cleanly 23 seconds later (record 373379827,
active).

The decisive experiment: the first hypothesis was "transient — the poisoned
binding will clear for new events." It did not: **fresh runs at 18:59 UTC, six
hours later, still bound to the dead record.** GitHub's event → workflow-record
resolution for this path is permanently glued to the first-created (dead)
record; the later "active" record is shadowed and never selected. Re-pushing
the same path cannot fix it — proven, not theorized.

## The fix (applied)

**Rename the workflow file to a fresh path.** The poison is attached to the
(repo, path) → record mapping; a new path gets a brand-new record with no
poisoned history:

```bash
git mv .github/workflows/ci.yml .github/workflows/sentinel-ci.yml
```

Cross-lane reference updates (flagged, not silent — METHODOLOGY §1):
`METHODOLOGY.md` §3 and `LOCAL-OPS.md` §4 now point at
`.github/workflows/sentinel-ci.yml`. (`ops/decision_log.md`'s 2026-10-02 entry
keeps the old name — it is a historical record of what base setup created.)

Zero content change to the workflow itself — the file was never the problem.

### If the renamed workflow STILL `startup_failure`s (final escalation)

Then the poison is repo-level, not path-level. Do not burn more runs —
escalate to GitHub Support with run ids (37051152907, 37049140301) and
workflow id 373379524, and tell Sunday's lanes CI is down: bar 10 falls back
to the manual equivalent (`python3 -m unittest discover tests` + the three
probe steps from the workflow, run locally on `main` HEAD, witnessed by
Tripwire + one other chief).

## 13:15–13:40 IST experiments (coordinator) — rename FALSIFIED, ESCALATED

Four further interventions were tried; **all failed**. The ghost record
373379524 (state=deleted, path=BuildFailed) is pinned at **repo-ID level** in
GitHub's run router — it survives path changes, permission toggles, and even
a repo rename.

1. **Workflow rename** (`ci.yml` → `sentinel-ci.yml`, pushed to main as
   d373428): new active record 373764245 registered cleanly. Fresh
   pull_request runs at 07:42–07:43 UTC (ids 37107244899, 37107246586,
   37107251546, 37107253552, 37107267154, 37107270292) — on branches whose
   trees contain ONLY `sentinel-ci.yml` — **still bind to 373379524**.
   The (repo, path) → record poison theory is FALSIFIED.
2. **Actions disable/re-enable** (API permissions toggle): no effect; new runs
   still bind to 373379524.
3. **Repo rename** (`sentinel` → `sentinel-tmp` → back): no effect; run
   37108030561 still binds to 373379524. The pin follows the repo ID, not the
   name.
4. **Minimal probe workflow** (workflow_dispatch, separate file): GitHub did
   not even register it (`gh workflow run Probe` → "could not find any
   workflows"); its push-triggered run (37107593678) also bound to 373379524.

**Verdict: ESCALATED.** This is a GitHub platform bug — a deleted ghost
workflow record permanently shadows the active record in the event→workflow
router. It cannot be fixed from the repo side. Escalate to GitHub Support
with: repo `AdityaPagare619/sentinel`, ghost workflow id 373379524
(state=deleted, path=BuildFailed, created 2026-10-02T18:41:07Z), active
workflow id 373764245, sample run ids above.

**Operative gate until resolved:** run the workflow's four steps locally
(`python3 -m unittest discover tests`; `python3 -m unittest tests.test_gate -v`;
`PYTHONPATH=src python3 -m sentinel.evalharness --n 200 --seed 7`;
secrets-grep on the diff). Local green + independent review = mergeable.
This is documented, not a waiver — the steps are identical, the witness is
the coordinator.

## Prevention ritual (standing)

After adding or changing ANY workflow file, before announcing "CI is live":

```bash
gh api repos/AdityaPagare619/sentinel/actions/workflows \
  --jq '.workflows[] | "\(.id) | \(.name) | \(.path) | \(.state)"'
# require: exactly one record per path, state=active. If you ever see
# path=BuildFailed / state=deleted, the path is poisoned — rename the file,
# do not re-push the same path.
```

…and watch the FIRST run to green. A workflow file on disk is not a working CI.

## Known limitations (not blockers)

- This PAT gets `403 Resource not accessible` on the check-runs API (needs
  `Checks: read` on the fine-grained token). Use `gh run view <id> --log` or
  the Actions web UI for step logs.
- The workflow runs `python3 -m unittest discover tests` — `tests/` does not
  exist on `main` yet (it arrives with the engine PR merge). Until that merge,
  expect the suite step to fail at *runtime*
  (`ImportError: Start directory is not importable`) — a missing-code
  condition, not a CI-infra problem. Bar 10 ("main green") can only go green
  after the engine merge lands.

## Live verification

- [x] PR opened: https://github.com/AdityaPagare619/sentinel/pull/13
- [x] First hypothesis FALSIFIED: fresh runs 18:59:30/18:59:38 UTC
      (ids 37051138569, 37051152907) bound to dead record 373379524 —
      binding is sticky, not transient.
- [x] Fix applied: workflow renamed to `.github/workflows/sentinel-ci.yml`
      (this push).
- [x] Second hypothesis FALSIFIED (13:15–13:40 IST): runs 37107244899,
      37107246586, 37107251546, 37107253552, 37107267154, 37107270292
      (07:42–07:43 UTC, post-rename) still bind to 373379524. Actions
      toggle + repo rename also fail. Poison is repo-ID-level.
- [x] Conclusion: still failing → final escalation (see above).
- [x] Verdict: **ESCALATED** — GitHub Support required. Local CI is the
      operative gate.
