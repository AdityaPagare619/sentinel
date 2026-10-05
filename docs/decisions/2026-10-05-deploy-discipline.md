# 2026-10-05: Production deploy discipline — pinned-SHA health-gated swaps; `git pull` forbidden

**Date:** 2026-10-05 IST · **Type: 1** (production-safety standing rule;
reversal requires re-running the reasoning below, not a lane-local call)
**Owner (single-threaded):** Relay (devops review owner, OPERATING-RULES
§1.6) · **Lane:** dev-2, 12-hour execution wave (12H-PLAN §1)

## Decision

1. The only supported production upgrade path is `scripts/ops/deploy.sh`:
   pinned 40-hex SHA → fresh artifact dir → gate on the pinned SHA →
   atomic `current`-symlink swap → `/healthz` health gate (200 + `ok:true`)
   → auto-rollback to the previous artifact on failure.
2. `git pull` (any flags, including `--ff-only`) on the production box is
   **forbidden** — as are hand-editing artifact dirs and upgrading by any
   path other than `deploy.sh`. Docs must not prescribe it;
   `docs/deploy-production.md` §8 was struck and rewritten in this lane.

## Why (the five reasons)

1. The artifact you tested is not the artifact you run — `git pull`
   mutates the live tree while processes run; restarts can start from a
   half-merged tree with mixed in-flight versions.
2. No health gate — a broken tree fails only when a human notices.
3. No rollback point — rollback becomes log archaeology instead of one
   command (`rollback.sh`).
4. Unverifiable state — local drift hides behind a clean `rev-parse`.
5. Version skew — receiver and platform restart from the same mutable tree
   at different times.

Full treatment: `docs/deploy-discipline.md` (§3 for the reasons, §6 for
rejected alternatives).

## Alternatives considered and rejected

- `git pull --ff-only` (the pre-existing §8 text) — rejected: still
  mutates the live tree; see reasons 1–5.
- `rsync` of a CI-built working tree — rejected: no SHA provenance chain
  for audit/forensics.
- Docker on the box — rejected: the `current` symlink already gives atomic
  swap + kept prior artifact; Docker adds a daemon to a ₹0 minimal-ops
  posture for no new atomicity.
- Manual hotpatching — rejected: unverifiable state by definition.

## Dissent

None on record — the rule codifies what `deploy.sh` and `rollback.sh`
already implement; the lane's work is docs, not new mechanism. The only
open item: the systemd units in the install guide still point at the
mutable repo checkout; Relay/dev-1 own re-pointing them at the `current`
symlink (flagged in `docs/deploy-discipline.md` §7, not silently assumed).

## Reversal conditions

This rule is reopened only if: (a) the units' WorkingDirectory/PYTHONPATH
migration (§7) is verified complete and (b) a concrete failure of the
artifact-swap model on this box is demonstrated in writing with a
replacement that preserves pinning, the health gate, and one-command
rollback. "It was faster to pull" is not a reversal condition.

## Skill clauses that bind

- principal-systems · infrastructure constitution (immutable IaC; never
  SSH-fix production).
- principal-governance · decoupled deployment (deploy ≠ release;
  health-gated swap as the single-box canary).
- execution-doctrine §6 (validate → shadow → canary; andon authority).
