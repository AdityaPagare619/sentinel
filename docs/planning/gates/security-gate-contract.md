# Security gate contract — secrets-grep as a merge gate

**Owner:** TEAM 4 (Security) — owns the script, its patterns, and its allowlist.
**Consumer:** TEAM 6 (Quality/Test) — owns the promotion checklist; REFERENCES
this contract, never forks or reimplements the script.
**Status:** 2026-10-07 · lane/faang-security-20261007

## 1. The gate

| | |
|---|---|
| Command | `./scripts/ops/secrets-grep.sh` (from the repo root) |
| Stage | `pre-pr-gate.sh` stage 1 — fast fail before the test suite |
| Pass | exit `0`, stdout ends with `ok: secrets-grep clean` |
| Fail | exit `1`, `SECRET-HIT:` lines on stdout, `FAIL:` on stderr |
| Machine contract | exit code is the verdict; the `ok:`/`FAIL:` trailer lines are the greppable summary. `ALLOWLISTED:` lines go to **stderr** so the report shows what was excused and why. |

The script scans the working tree for secret VALUES (not env-var names):
assignment-shaped long opaque values, `Bearer <token>` literals, vendor
prefixes (`sk-live/test-`, `ghp_`, `gho_`, `xox*`), and 32+ hex routing /
webhook secrets. Generated outputs (`deploy/dist*`, `__pycache__`, `*.db`)
are pruned — they are copies of scanned sources, and scanning them would
double-report.

## 2. The incident that motivates this contract

2026-10-07: the fix wave left `secrets-grep.sh` green on its branch; commit
`3031963` ("fix(kill): wire C3 kill switch into the receiver's legacy
Forwarder") then introduced a new secret-shaped fixture
(`ops/drills/kill_drill_receiver_topology.py:79`) and merged to `main`
with the gate red. The gate ran on the lane branch, not on the merge
result — so the regression was invisible until this lane re-ran it.

**Rule (binding): the gate runs on the merge-result tip.** The lane branch
being green is necessary, not sufficient. The coordinator re-runs
`pre-pr-gate.sh` on the exact SHA about to land on `main`; red = no merge,
no exceptions (LANE_PLAYBOOK step 7 already says this — this contract makes
"the gate" unambiguous: it means the merge-result tip, full gate, never
`--fast`).

## 3. Allowlist change protocol

The script carries an explicit allowlist for fixture placeholders that LOOK
secret-shaped but are provably fake. Rules:

1. A real secret is NEVER "fixed" by adding it to the allowlist. A hit that
   is a real credential = P0: rotate the credential, purge it from history
   if committed, fix same-day.
2. Allowlist entries name the exact fixture (`file:literal-or-regex`) with a
   one-line justification of WHY it is provably fake (self-contained drill,
   named test fixture, documented surrogate).
3. Prefer renaming the fixture to a self-evident fake
   (e.g. `drill-fixture-loopback-only`) over a loose regex — the code then
   carries its own excuse.
4. Allowlist edits are security-team reviewed. TEAM 6 may propose, TEAM 4
   disposes.

## 4. What TEAM 6's checklist references (and does not duplicate)

TEAM 6's promotion checklist cites this contract by path. It does NOT embed
a copy of the patterns, the allowlist, or the invocation — one owner, one
implementation (Linux MAINTAINERS model: patches route to the subsystem
owner). If TEAM 6 needs a new pattern (new secret shape observed in the
wild), they file it to TEAM 4; TEAM 4 ships the pattern + regression test.

## 5. Second layer (recommended, Aditya's dashboard action)

GitHub **secret scanning + push protection** is free on public repos
(`AdityaPagare619/sentinel` is public). Push protection blocks the push
that contains a secret *before it lands* — it catches what a local gate
misses when someone pushes without running the gate. Enable at:
repo → Settings → Code security → Secret Protection. This does not replace
the local gate (defense in depth); it backstops the human who skips it.

## 6. Verification receipts (this lane)

- `secrets-grep.sh` green on `lane/faang-security-20261007` (exit 0).
- Full `pre-pr-gate.sh` green before the branch is offered for merge.
- The 2026-10-07 regression (drill fixture) fixed by rename + allowlist
  entry, not by weakening a pattern.
