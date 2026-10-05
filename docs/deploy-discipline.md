# Sentinel production deploy discipline

**Status:** standing rule · **Owner:** Relay (devops review owner per
OPERATING-RULES §1.6) · **Date:** 2026-10-05
**Decision record:** `docs/decisions/2026-10-05-deploy-discipline.md` (Type 1)

The only supported way to change what runs in production is the
pinned-SHA → health-gated swap implemented by `scripts/ops/deploy.sh`.
Everything else — above all `git pull` on the production box — is forbidden,
not deprecated.

## 1. The deploy lifecycle (what `deploy.sh` does)

```
pin (full 40-hex SHA) → clone fresh → checkout SHA → verify rev-parse
  → run the gate on the pinned artifact → swap `current` symlink
  → restart ($SENTINEL_RESTART_CMD) → health-gate /healthz (ok:true, ≤2 min)
  → GREEN: log to deploy.log   |   FAIL: auto-rollback to previous artifact
```

- **Pin:** `--sha` must be a full 40-hex SHA; branch names and tags are
  rejected. A branch is a moving target — a deploy record that names a
  branch cannot be audited, reproduced, or rolled back to.
- **Fresh clone:** the artifact dir is a fresh clone checked out at exactly
  the SHA, and `git rev-parse HEAD` is verified to equal it. The artifact
  dir is built, never mutated.
- **Gate first:** `./scripts/ops/pre-pr-gate.sh --fast` runs on the pinned
  artifact *before* it can become live. Gate red = deploy aborted, artifact
  kept for forensics.
- **Swap:** `ln -sfn` of the `current` symlink. The swap is atomic; there is
  no moment when `current` points at a half-written tree.
- **Health gate:** up to 24 × 5 s polls of `/healthz` requiring HTTP 200
  *and* top-level `ok:true` (a 503 with failed predicates is unhealthy).
  Failure rolls back the symlink to the previous artifact and restarts.
- **Record:** every green deploy appends `<timestamp> <sha> <prev-sha>` to
  `$ROOT/deploy.log` — the audit trail of what ran, when.

**Rollback** (`scripts/ops/rollback.sh [--to DIR]`): swaps `current` back,
restarts, and health-gates — and refuses to declare success unless
`/healthz` reports `ok:true`. The 3 AM script; no archaeology required.

## 2. The rule

> **`git pull` on production is forbidden. No exceptions, no "just this once."**

Also forbidden on the box: editing the artifact dir by hand, restarting the
services against a mutated tree, and running upgrades from any path other
than `deploy.sh`. State lives in `$SENTINEL_STATE_DIR` / `/etc/sentinel`
(state + secrets) — never in the repo tree.

## 3. The reason (why the rule exists)

1. **The artifact you tested is not the artifact you run.** `deploy.sh`
   builds a fresh dir, checks out exactly the SHA, and gates it. `git pull`
   mutates the *live* tree in place while processes are running — a restart
   mid-pull starts from a half-merged tree, and in-flight processes run
   mixed versions nobody can name.
2. **No health gate.** A `git pull` + restart that produces a broken tree
   fails only when a human notices. `deploy.sh` refuses to declare the
   deploy live until `/healthz` says `ok:true` and auto-rolls back when it
   doesn't.
3. **No rollback point.** `deploy.sh` keeps the previous artifact dir and
   the previous `current` target; rollback is one command. After a `git
   pull`, rollback is log archaeology (`git log`, guess the merge, hope the
   tree was clean).
4. **Unverifiable state.** After a pull, `git rev-parse` names a SHA — but
   the tree may carry local drift, stashed-and-popped leftovers, or a
   half-finished merge. The whole point of the immutable law is that the
   running bits are *provably* the audited bits.
5. **Version skew across processes.** The receiver and the platform restart
   from the same mutable tree at different times → the paging path and the
   read path can run different code for an unbounded window. Artifact
   dirs make every process's version a fact, not an assumption.

*Lineage:* principal-systems infrastructure constitution — "If it isn't
automated, it doesn't exist"; immutable IaC — never SSH-fix production,
destroy and recreate. A `git pull` on the box is SSH-fix-production under
a friendly name. (The D6 lane's `git stash`/`pop` collision and the 003M
re-dispatch corruption are this repo's own proof that shared mutable trees
corrupt work — OPERATING-RULES §5.2.)

## 4. Upgrades (operator path)

```bash
# 1. Pick the SHA you want — a green CI SHA, never a branch.
SHA=<full-40-hex>
# 2. Deploy it. The script refuses unpinned refs, refuses a red gate,
#    and auto-rolls back if the health gate fails.
SENTINEL_RESTART_CMD="sudo systemctl restart sentinel-receiver sentinel-platform" \
SENTINEL_HEALTH_TOKEN=<token> \
SENTINEL_PORT=8080 \
  sudo -E /opt/sentinel/repo/scripts/ops/deploy.sh --sha "$SHA"
```

That is the entire upgrade path. The old §8 `git pull --ff-only` block in
`docs/deploy-production.md` has been struck and replaced with this section —
it was the one place the docs contradicted the script, and docs that
contradict the script are how bans rot.

## 5. Deploy failure → ticket, not page

`deploy.sh` fails *safe*: a red gate aborts before the swap; an unhealthy
new artifact auto-rolls back to the previous one and restarts. So a deploy
failure is **not** a page — the box healed itself to the last-known-good
artifact. It **is** a ticket within 30 minutes: attach the kept-forensics
artifact dir path, the gate output (gate-red case), or the healthz body
(unhealthy case), and fix forward with a new SHA. See
`ops/page-vs-ticket.md`.

## 6. Rejected alternatives

- **`git pull --ff-only` in docs** (the pre-existing §8 text): rejected for
  the five reasons in §3. Fast-forward-only still mutates the live tree.
- **`rsync` of a CI-built working tree**: rejected — no provenance chain.
  You cannot map the tree back to a SHA for audit, forensics, or the
  deploy log. A git clone at a verified SHA is the provenance.
- **Docker on the box**: rejected — the product ships as a stdlib-only
  Python tree (single pinned `cryptography` exception); Docker buys nothing
  the `current` symlink doesn't already give (atomic swap, kept prior
  artifact) and adds a daemon + image store to a ₹0, minimal-ops posture.
- **Manual hotpatching on the box**: rejected — repair source, rebuild
  (new SHA), redeploy. Hotpatches are exactly the unverifiable state §3.4
  bans.

## 7. Migration note (open, owned)

`docs/deploy-production.md` installs systemd units with
`WorkingDirectory=/opt/sentinel/repo` — the mutable checkout. For this
discipline to hold, the units' `WorkingDirectory` and `PYTHONPATH` must be
re-pointed at the `current` symlink
(`/opt/sentinel/current`, `PYTHONPATH=/opt/sentinel/current/src`), and
`SENTINEL_RESTART_CMD` set accordingly. **Flagged dependency:** Relay /
dev-1 owns the unit migration before the first `deploy.sh` production run;
until then the docs rule is the contract and the units are the known gap.
(Stated rather than hidden — execution-doctrine: honesty about what's real.)

## Skill & Evidence

- **principal-systems · infrastructure constitution** ("If it isn't
  automated, it doesn't exist"; immutable IaC — never SSH-fix production):
  the ban on `git pull` is this clause written as a rule. The swap script
  is the automation; everything else is the grind it replaces.
- **principal-governance · decoupled deployment**: deploy ≠ release. The
  health-gated swap is the single-box analog of the canary —
  the new artifact proves itself *in the live environment* before it owns
  the `current` pointer, and the previous artifact stays alive on disk as
  the instant rollback.
- **execution-doctrine §6** (validate → shadow → canary; andon authority):
  a red health gate auto-halts the line the way the andon cord does — the
  stopper is the script, and the stop is the designed behavior, not an
  incident.
- **Web:** "Health checks are the only truth... Never run `git pull` on a
  production server... Automate the rollback, not just the deploy"
  (zero-downtime deploy patterns,
  https://github.com/claude-dev-suite/knowledge_base/blob/HEAD/knowledge/deployment/zero-downtime-patterns.md,
  accessed 2026-10-05);
  "IMMUTABLE INFRASTRUCTURE: Never SSH into a server to `git pull`.
  Replace the runtime with the verified artifact"
  (https://github.com/lyther/agent-surface/blob/HEAD/commands/ship-deploy.md,
  accessed 2026-10-05).
- **Repo evidence:** `scripts/ops/deploy.sh` (pinned SHA regex, gate,
  symlink swap, health-gate loop, auto-rollback) and
  `scripts/ops/rollback.sh` (health-gated rollback) read in the worktree —
  the docs describe the script that exists, not an aspiration.
