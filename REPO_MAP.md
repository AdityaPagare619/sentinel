# Sentinel — REPO MAP

**Read this first.** Everything in this repo, what lives where, and where NEW
things go. If you can't find a home for something, it goes in `ops/` and gets
logged — nothing lives only in chat.

## Root — the constitution (read these before touching anything)

| File | What it is | Who owns it |
|---|---|---|
| `CHARTER.md` | Mission, why-we-win, design laws, non-goals, operating principles | Petu |
| `METHODOLOGY.md` | The git machine: branches, commits, CI, Definition of Done, ledgers | Petu |
| `TEAMS.md` | Named roster: 9 chiefs + 4 wardens + lane role templates + staffing | Petu |
| `ROADMAP.md` | Preview-1 scope, milestones M1–M6, exit bars, lane assignments | Relay |
| `ROAD_TO_LAUNCH.md` | Master plan: mission, waves, top-5 risks, decisions needed | Petu |
| `LOCAL-OPS.md` | How agents work in this room: branch-per-lane, file ownership, push/PR flow | Petu |
| `ARCHITECTURE.md` | v0.1 engine design — **frozen**; changes via Forge + logged decision | Build coordinator / Forge |
| `PLATFORM_ARCHITECTURE.md` | Platform spec — **frozen**; changes via ADR only | Forge |
| `WAVE_PLAN.md` | Sat/Sun schedule, lanes, exit bars | Relay |
| `PROGRESS.md` | Build coordinator's build order + milestones — coordinator-owned | Build coordinator |
| `README.md` | Zero-to-first-alert quickstart — the public face | Prism |

## `chiefs/` — the operating company

One file per chief: `ARCHITECT`, `PAGER`, `ORACLE`, `VAULT`, `LEDGER`, `PRISM`,
`TRIPWIRE`, `RELAY`. Each carries mandate, skills, daily/weekly rituals,
artifacts with paths, interfaces to other chiefs, staffing/lane plan,
definition of done, and prohibited actions. **Start here to understand who
decides what.**

## `ops/` — the ledgers and the machine's memory

| File | What it is |
|---|---|
| `ops/decision_log.md` | Every consequential decision, dated, with rationale and who called it |
| `ops/constraint_registry.md` | Hard constraints; the Constraint Sentinel vetoes violations |
| `ops/SENTINEL_LOG.md` | The dated living log: milestones, commits, numbers, verdicts |
| `ops/lane_registry.md` | Every lane: branch, owner, status, outcome, commits |
| `ops/LANE_PLAYBOOK.md` | How to run a lane: lifecycle, naming, commits, PR rules, done-criteria |
| `ops/RUNBOOKS.md` | RB-1 push/PR/CI · RB-2 false-suppress SEV1 drill · RB-3 demo-day checklist |
| `ops/STANDING_ORDERS.md` | Aditya's permanent orders — they outrank convenience |
| `ops/COMPLETENESS_AUDIT.md` | Repo completeness vs the petu-labs ledger set + remaining gaps |
| `ops/briefs/` | Archived briefs: `2026-10-02-morning.md`, `2026-10-02-evening.md`, then daily |

## `research/` — the daily R&D function

| Path | What it is |
|---|---|
| `research/CHARTER.md` | The R&D machine: cadence, citation standard, ADR-only spec changes |
| `research/<dept>.md` | Per-department agenda: tonight's question + ranked backlog + sources |
| `research/<dept>/YYYY-MM-DD-*.md` | Dated sprint notes — every claim cited, facts separated from inferences |
| `research/synthesis-YYYY-MM-DD.md` | Nightly synthesis → proposed ADRs (evidence-backed, never applied silently) |

## `src/` and `tests/` — the engine (single-owner: build coordinator)

`src/sentinel/` — receiver, correlator, gate, client (+mock), forwarder, audit,
tuner, synthetic generator, eval harness. `tests/` — 140 tests, stdlib unittest.
**Do not touch unless you are the build coordinator or Forge flagged you in.**

## `.github/` — CI and PR machinery

`workflows/ci.yml` (full suite + repeatability probes + kill-the-client +
secrets-grep), `pull_request_template.md`.

## `docs/` — member-facing docs

`docs/ONBOARDING.md` — a new agent's first 30 minutes.

## Where NEW things go

| New thing | Home |
|---|---|
| A decision | `ops/decision_log.md` (at decision time) |
| A hard constraint | `ops/constraint_registry.md` |
| A lane | new `lane/*` branch + a row in `ops/lane_registry.md` |
| A research finding | `research/<dept>/YYYY-MM-DD-*.md` + synthesis |
| A spec change proposal | ADR in the research synthesis → Forge review → frozen doc |
| A brief | `ops/briefs/YYYY-MM-DD-morning.md` / `-evening.md` |
| An incident | `ops/incidents/YYYY-MM-DD-*.md` (per RB-2) |
| A milestone/blocker | `ops/milestones.md` / `ops/blockers.md` (Relay's — to be created) |
| A Sunday acceptance check | `ops/acceptance-sunday.md` (Tripwire's — to be created) |
