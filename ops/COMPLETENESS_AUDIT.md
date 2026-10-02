# Sentinel — Completeness Audit (2026-10-02)

Repo completeness vs the petu-labs ledger set. Written honestly: what matches,
what's intentionally different, and what remains open. No padding.

## 1. What now matches the petu-labs ledger set

| petu-labs | Sentinel | Status |
|---|---|---|
| `ops/CAMPAIGN_LOG.md` (dated living log) | `ops/SENTINEL_LOG.md` | ✅ backfilled 2026-10-02 with commit hashes |
| `ops/ROAD_TO_1.md` (master plan) | `ROAD_TO_LAUNCH.md` | ✅ mission, exit bars, waves, top-5 risks, decisions needed |
| `ops/decision_log.md` | `ops/decision_log.md` | ✅ living, through the 18:45 platform pivot |
| `ops/constraint_registry.md` | `ops/constraint_registry.md` | ✅ + platform-tier laws |
| `ops/experiment_registry.csv` | `ops/lane_registry.md` | ✅ adapted: lanes, not experiments |
| `ops/KAGGLE_RUNBOOK.md` | `ops/RUNBOOKS.md` | ✅ RB-1 push/PR/CI, RB-2 SEV1 drill, RB-3 demo-day |
| `intel/` (per-topic notes) | `research/` | ✅ charter + 5 agendas + dated notes + synthesis |
| `ops/last_debrief_date.txt` | `ops/briefs/` | ✅ superseded by the full brief archive |

Plus Sentinel-native depth petu-labs doesn't have: `chiefs/` (8 operating
files), frozen `PLATFORM_ARCHITECTURE.md`, `WAVE_PLAN.md`, `ops/LANE_PLAYBOOK.md`,
`REPO_MAP.md`, `docs/ONBOARDING.md`, `TEAMS.md` operating roster.

## 2. What's intentionally different (product company, not Kaggle campaign)

- **No experiment registry CSV.** Sentinel runs *lanes* (bounded missions with
  branches), not experiments — `ops/lane_registry.md` is the equivalent.
- **No kernel workflow / pipeline_state / splits.** No Kaggle compute or data
  pipeline here; the audit DB is the state, `src/` + `tests/` is the pipeline.
- **No public leaderboard.** Preview-1's scoreboard is the 10 exit bars in
  `WAVE_PLAN.md` §7, verified by Tripwire's acceptance checks.
- **Frozen specs + ADRs.** A product company needs spec stability: platform
  changes go through Forge-reviewed ADRs; research never edits frozen docs
  silently. (`research/CHARTER.md` iron rule.)
- **`intel/PROGRESSION_LEDGER.md` has no separate twin.** Its function —
  tracking progression with evidence — is folded into `ops/SENTINEL_LOG.md`
  (dated record) + `ops/lane_registry.md` (lane outcomes). A per-component
  progression ledger would be duplication, not depth.

## 3. Remaining gaps (open, owned, dated)

| # | Gap | Owner | Due |
|---|---|---|---|
| 1 | `ops/milestones.md` + `ops/blockers.md` — promised by `chiefs/RELAY.md`, not yet created | Relay | Sat 10:00 kickoff |
| 2 | `ops/incidents/` dir — RB-2's postmortem home; doesn't exist yet | Tripwire | Sat |
| 3 | `ops/acceptance-sunday.md` — WAVE_PLAN Sun AM references it; Tripwire to encode | Tripwire | Sun AM |
| 4 | `WAVE_PLAN.md` says Wave A starts 09:00; working hours are 10:00–19:00 IST — reconcile to 10:00 start / 09:30 briefing | Relay + Forge | Sat 09:30 |
| 5 | ADR-001 / ADR-005 / ADR-007 decisions pending | Forge recommends; Aditya decides | before Sunday |
| 6 | No remote `main`, no PRs, no GitHub Actions until the token step | Aditya (30-sec phone step) | ASAP |
| 7 | Brief cadence: only 2026-10-02 archived — the daily 09:30/EOD discipline must hold through the weekend | Relay | daily |

Nothing above is hidden. When a gap closes, it leaves this list with a commit
hash — the audit is re-run, not edited in place.
