# Sentinel — Runbooks

Operational procedures. Follow exactly; improve by proposal + logged decision,
never by improvisation.

## RB-1: Push / PR / CI flow (once the token sees the repo)

**Precondition:** `gh api repos/AdityaPagare619/sentinel` returns 200 (not 404).
Until then, branches live locally — this runbook waits.

1. **Push the branch:**
   ```bash
   cd ~/workspace/jev-builds/sentinel
   git push -u origin <branch>   # e.g. lane/code-mvp-v0.1, base/setup-docs
   ```
2. **Open the PR** (never push to `main` directly):
   ```bash
   gh pr create --base main --head <branch> \
     --title "feat(scope): <imperative summary>" \
     --body "$(cat .github/pull_request_template.md)"
   ```
   Fill the template: tests evidence, docs touched, Claim-Auditor numbers,
   lane-crossing flags.
3. **Review:** a DIFFERENT agent reviews. Checklist: contracts conformed?
   secrets absent? numbers sourced? hot-path separation intact? Vault pass if
   ingress/egress/audit touched.
4. **The local gate must be GREEN** — `bash scripts/ops/pre-pr-gate.sh`
   (secrets-grep, full suite, kill-the-client invariant, boot smoke,
   ops-scripts, config schemas). There is no GitHub Actions on this repo;
   a remote CI signal does not exist. Red = no merge, no exceptions.
5. **Squash-merge, delete the branch:**
   ```bash
   gh pr merge <number> --squash --delete-branch
   ```
6. **Verify:** `main` green after merge. If red → stop-the-line (RB-1 §7):
   all lanes pause merges until green; breaker owns the fix.

## RB-2: False-suppress incident drill (SEV1 handling)

**Trigger:** any *unexplained* false suppress on a SEV1-class alert — in eval,
demo, shadow, or production. Per CHARTER design law #4, this is a SEV1.

1. **Detect → pause.** The discovering agent stops its thread and posts to the
   room: `SEV1: false suppress suspected — <alert_id>/<fingerprint>`. No further
   suppress-policy changes until the drill completes.
2. **Pull the audit row.** Everything needed is in `decisions`:
   ```sql
   SELECT alert_id, fingerprint, input_sha256, jev_model,
          q1_severity, q1_probs, q1_conf, q3_disposition, q3_probs, q3_conf,
          action, reason, latency_ms, received_at
   FROM decisions WHERE alert_id = '<id>';
   ```
3. **Answer, in order:**
   a. Was the triple lock actually satisfied? (P(p1) < 0.002 AND conf ≥ 0.90
      AND fingerprint ∈ allowlist — check each against the row + thresholds.json.)
   b. Was it a Jev flip? (Same `input_sha256` decided differently on repeat —
      check flip-audit records.)
   c. Was the allowlist wrong? (Fingerprint verified by whom, when?)
   d. Was the input malformed? (State shaping bug — `input_sha256` lets you
      rebuild the exact state.)
4. **If the audit log cannot fully explain the decision, that is itself a
   finding — against the audit log.** Log it as such.
5. **Blameless postmortem within 24h** → `ops/incidents/<date>-false-suppress.md`:
   timeline, root cause, which lock failed, fix (threshold change? allowlist
   removal? state bug?), regression test added. Vault adds the security section;
   Oracle re-verifies the math.
6. **Resume** only after the postmortem's action items have owners and dates.

## RB-3: Demo-day checklist (Sunday 9 PM)

**T-24h (Sat 21:00):**
- [ ] Tripwire: full fault-injection pass green (kill-the-client mid-demo-run,
  529 storm, malformed payloads). Results logged.
- [ ] Oracle: calibration sign-off — every number in the demo re-verified from
  artifacts. Flip/shuffle probes green.
- [ ] Pager: disposition review — every suppression in the script has its
  defensibility note.

**T-4h (Sun 17:00):**
- [ ] Full demo script run-through, timed (Prism). Every live segment has a
  recorded fallback; network-dependent segments default to the recording.
- [ ] Fixtures frozen and versioned (Ledger). Demo data source labels on every view.
- [ ] `main` green; demo branch cut. No merges after T-4h except stop-the-line fixes.

**T-1h (Sun 20:00):**
- [ ] Kill-the-client segment rehearsed (not improvised).
- [ ] Latency numbers on screen are the measured ones (Oracle's campaign), with
  the honest caveat if the campaign is still running.
- [ ] Rollback plan: the demo is mock-backed; worst case is the recorded
  transcript. There is no live dependency that can fail the demo.

**Go/no-go (Sun 20:30, Petu):** all exit bars in `WAVE_PLAN.md` §7 checked with
evidence, or the bar is explicitly waived with a logged reason. No silent waivers.
