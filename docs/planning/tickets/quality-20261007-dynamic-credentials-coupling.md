# TICKET — four dev/research modules fail the entrypoint hermeticity gate

**Filed:** 2026-10-07 · **Owner:** TEAM 6 (quality/test) · **Status:** OPEN (quarantined)
**Quarantine ref:** `scripts/ops/gate-entrypoints.quarantine`

## Symptom

These modules fail the `gate-entrypoints.sh` hermeticity check (no absolute
out-of-repo `sys.path` entries):

| Module | Violation |
|---|---|
| `demo/storm-scenario/storm_runner.py` | `sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")` (line 38) |
| `rehearsal/flip_beat.py` | `sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")` (line 36) |
| `research/jev-behavior/bin/ab_run.py` | `sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")` (line 28) |
| `research/jev-behavior/bin/latency_campaign.py` | `sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")` (line 36) |

All four dynamically couple to the host's skill-creator tooling directory —
an absolute path that exists only on this build box. (The repo-relative
`src/`/`demo/` inserts in the same files are the dynamic part of the same
coupling.)

## Why quarantined, not fixed now

- These are demo/rehearsal/research scripts, not product code: nothing in
  the shipped pipeline imports them.
- The coupling is environmental (this box's `/opt/hatch`), not a product
  defect. Fixing it means either vendoring the skill-creator dependency or
  removing the scripts' dependence on it — each script owner's judgment,
  not the quality lane's unilateral call.
- Filed during the FAANG principal wave merge sequence: the quarantine
  lines landed without this ticket, which the entrypoint gate correctly
  flagged as a dangling reference (a gate doing its job).

## Options for the owner

1. Remove the `/opt/hatch/skills/skill-creator/bin` inserts (vendor or drop
   the dependency) and delete the quarantine lines.
2. Retire the scripts if the demo/rehearsal/research workflows are done
   ("code is a liability" — principal-systems).
3. Keep quarantined only while the scripts are actively used.

## Gate behavior until closed

The entrypoint gate reports these modules as QUARANTINED (not PASS) and exits
0 only while this ticket file exists. If a module stops failing hermeticity,
remove its quarantine line. If this ticket is deleted while any module still
fails, the gate goes RED.
