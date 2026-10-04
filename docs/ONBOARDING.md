# Sentinel — Onboarding: your first 30 minutes

You're a new agent (or member) joining the company. Do this in order; it takes
30 minutes and saves you from breaking the machine.

## 0:00–0:05 — Read the map
1. `REPO_MAP.md` — where everything lives and where new things go.
2. `CHARTER.md` — mission, design laws (non-negotiable), non-goals.
3. `ops/STANDING_ORDERS.md` — Aditya's permanent orders. They outrank convenience.

## 0:05–0:10 — Learn the constraints
4. `ops/constraint_registry.md` — hard constraints (Jev limits, architecture
   locks, the Sunday scope boundary). The Constraint Sentinel vetoes any plan
   that violates one. Read it before proposing anything.
5. `PLATFORM_ARCHITECTURE.md` — the frozen platform spec (changes via ADR only).
6. `WAVE_PLAN.md` — the current schedule and exit bars.

## 0:10–0:15 — Learn the machine
7. `METHODOLOGY.md` — branches, commits, CI, Definition of Done.
8. `ops/LANE_PLAYBOOK.md` — how to run a lane, start to finish.
9. `TEAMS.md` — who decides what; then `chiefs/<YOUR-CHIEF>.md` for your chief's
   full operating file.

## 0:15–0:20 — Check the state of the world
10. `ops/SENTINEL_LOG.md` — what's happened, with commit hashes.
11. `ops/lane_registry.md` — which lanes exist and their status.
12. `git log --oneline -10` and `git branch` — the ground truth, locally.
13. `ops/briefs/` — the latest brief; read the newest one first.

## Change windows: Sentinel-side only (ADR-001)
Sentinel's change windows are Sentinel-side config — PagerDuty-side
maintenance windows are **invisible to the receiver** and never defer
anything. P1/P2 (critical/high) alerts bypass change-window queuing
entirely (DR-13 reconciliation) — a P1 inside a window pages immediately.

## 0:20–0:30 — Do your first write-back
- If you were briefed a lane: create the branch, register it in
  `ops/lane_registry.md`, and make your first commit.
- If you were briefed research: read `research/CHARTER.md` + your department's
  agenda, then start the dated note.
- If you made a decision: log it in `ops/decision_log.md` **now**, not later.

## Never do these
- Never commit secrets/keys/`.env` — CI secrets-grep + Vault will catch it,
  and it's an incident.
- Never edit another lane's files silently — flag it in the PR; the owner edits.
- Never touch `src/`, `tests/`, `PROGRESS.md`, `ARCHITECTURE.md` unless you are
  the build coordinator or Forge flagged you in.
- Never merge your own PR — review must come from a different agent.
- Never present a number without a source — the Claim Auditor will kill it.
- Never let a decision live only in chat — `ops/decision_log.md` or it didn't
  happen.
