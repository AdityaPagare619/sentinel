# Pull Request checklist

## What
- Branch: `lane/<lane>-<short-desc>` ← `main`
- Summary (1–3 lines):

## Definition of Done (all must be checked)
- [ ] Full test suite green: `python3 -m unittest discover tests`
- [ ] Kill-the-client fail-open test green (or untouched and still green)
- [ ] Docs updated (README and/or the doc this change touches)
- [ ] PROGRESS.md entry (build coordinator) — status honest: DONE / FAILED / KILLED
- [ ] Demo-able: the change can be shown in the Preview-1 demo transcript
- [ ] No secrets in the diff (`.env`, keys, tokens, `*.pem`, routing keys) — the gate's secrets-grep stage re-checks
- [ ] **Claim-Auditor pass:** every number asserted in this description has a source
      (file, line, URL, or run id) — or is marked `UNVERIFIED`

## Claim audit
| Number claimed | Source |
|---|---|
| _e.g. 67.3% suppression_ | _tuner smoke run, PROGRESS.md M4_ |

## Lane-boundary crossings
- [ ] This PR touches only my lane's files, OR the crossed files are listed below
      with the owning lane's acknowledgment:
