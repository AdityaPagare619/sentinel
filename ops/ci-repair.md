# Sentinel — CI Repair: `startup_failure` on every run (Tripwire investigation)

**Date:** 2026-10-03 ~00:30 IST. **Investigator:** Tripwire.
**Status:** ROOT CAUSE FOUND — no workflow-file fix needed. Live verification
pending (this PR's CI run is the test).

## Symptom

16/16 GitHub Actions runs `startup_failure`, 2026-10-02 18:41:07 → 18:50:31 —
including a minimal probe workflow on `probe/ci-test`. Every run: 0 jobs, 0s
duration, empty run name. Looks repo-level, not file-level. It is — but not in
the way it looks.

## Evidence (all verified via `gh` CLI, 2026-10-03)

1. `gh run list -R AdityaPagare619/sentinel` → all runs `completed/startup_failure`.
2. `gh api repos/AdityaPagare619/sentinel/actions/workflows` → **two** workflow
   records:
   - `373379524` — name `''`, path **`BuildFailed`**, state `deleted`,
     created 2026-10-02T18:41:07Z
   - `373379827` — name `'Sentinel CI'`, path `.github/workflows/ci.yml`,
     state `active`, created 2026-10-02T18:41:30Z
3. Every run (push + pull_request + the probe) carries
   `workflow_id: 373379524` — **all runs bound to the dead record**,
   including the probe run 9 minutes after the good record existed.
4. The workflow file bytes are **identical** on `base/setup-docs` (the 18:41:07
   push), `main` (f4aa005), and every lane branch — `diff` clean, parses with
   PyYAML, no BOM, no CRLF.
5. `gh api .../actions/permissions` → `{"enabled": true, "allowed_actions": "all"}`.
   Not a permissions problem. Not a YAML problem.

## Root cause

`path: BuildFailed` is GitHub's marker for a workflow file that failed
**server-side ingestion** when first registered. At 18:41:07 — the first-ever
push to this brand-new repo — GitHub's workflow compiler choked on the initial
ingest and registered a dead `BuildFailed`/`deleted` record. The byte-identical
file re-ingested cleanly 23 seconds later (record 373379827, active).

Every run in the push wave then bound to the dead record (GitHub resolves the
run → workflow-record mapping at event time and the poisoned mapping stuck for
the whole wave, including the later probe). A run bound to a `BuildFailed`
record can never start a job: hence `startup_failure`, empty name, 0 jobs.

**This was a transient GitHub ingestion failure on repo creation, NOT a bug in
`.github/workflows/ci.yml`.** No file change is required.

## The fix (no code change)

1. Trigger a fresh run — any `pull_request` → `main` or push to `main` does it.
   **This PR (`lane/tripwire-acceptance`) is the live test.**
2. Verify the new run binds to workflow `373379827` and goes green:
   `gh run view <id> --json workflowDatabaseId,conclusion,jobs`.
3. If green → incident retired. Sunday's lanes can trust CI.

### If a fresh run STILL `startup_failure`s (escalation path)

Then the dead-record binding is sticky and we force re-ingestion:

```bash
# trivial byte change to force GitHub to compile a NEW workflow record
printf '\n# tripwire: force workflow re-ingest 2026-10-03\n' >> .github/workflows/ci.yml
git commit -m "chore(ci): force workflow re-ingest after BuildFailed poisoning" \
  -m "Decision: ops/ci-repair.md" && git push origin lane/tripwire-acceptance
# then confirm via API the new record is state=active (never BuildFailed/deleted):
gh api repos/AdityaPagare619/sentinel/actions/workflows \
  --jq '.workflows[] | "\(.id) \(.name) \(.state)"'
```

If even that fails → GitHub Support with run ids
(37050156129, 37049140301) and workflow id 373379524. Do NOT keep pushing
blind probe branches — each one burns a run on the dead record.

## Prevention ritual (standing)

After adding or changing ANY workflow file, before announcing "CI is live":

```bash
gh api repos/AdityaPagare619/sentinel/actions/workflows \
  --jq '.workflows[] | "\(.id) | \(.name) | \(.path) | \(.state)"'
# require: state=active. If you ever see path=BuildFailed / state=deleted,
# the file did not ingest — fix by re-push, never by re-running.
```

…and watch the FIRST run to green. A workflow file on disk is not a working CI.

## Known limitations (not blockers)

- This PAT gets `403 Resource not accessible` on the check-runs API (needs
  `Checks: read` on the fine-grained token). Use `gh run view <id> --log` or
  the Actions web UI for step logs.
- `ci.yml` runs `python3 -m unittest discover tests` — `tests/` does not exist
  on `main` yet (it arrives with the engine PR merge). Until that merge, expect
  the suite step to fail at *runtime* (`ImportError: Start directory is not
  importable`) — that is a missing-code condition, not a CI-infra problem.
  Bar 10 ("main green") can only go green after the engine merge lands.

## Live verification

- [ ] PR opened: <URL>
- [ ] CI run id: <id> — bound to workflow 373379827: <yes/no>
- [ ] Conclusion: <success/startup_failure> at <timestamp>
- [ ] Verdict: <CI HEALTHY — incident retired | STILL BROKEN — escalation path taken>
