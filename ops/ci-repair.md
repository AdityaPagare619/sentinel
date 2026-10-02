# Sentinel — CI Repair: `startup_failure` on every run (Tripwire investigation)

**Date:** 2026-10-03 ~00:30 IST, updated ~00:55 IST. **Investigator:** Tripwire.
**Status:** ROOT CAUSE CONFIRMED BY EXPERIMENT — fix applied (workflow file
renamed). Awaiting green confirmation run.

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
- [ ] New CI run binds to a fresh active record and starts jobs: <run id>
- [ ] Conclusion: <success | still failing → final escalation>
- [ ] Verdict: <CI HEALTHY — incident retired | ESCALATED>
