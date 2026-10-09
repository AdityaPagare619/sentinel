# Sentinel — PROGRESS (synced to shipped state)

**Project:** Sentinel — SRE page-or-suppress triage middleware. Jev (TypeSafe System One) advises; the deterministic gate decides. **Open source (MIT), no pricing, no billing — ever.**
**Repo:** `AdityaPagare619/sentinel` · **Env:** `~/workspace/jev-builds/sentinel/`
**Started:** 2026-10-02. This file is synced to git + gh-pages, not to reports.

## Current shipped state (verified against git/gh-pages, 2026-10-09)

| Surface | State | Evidence |
|---|---|---|
| `main` | @7ab7133 — FAANG wave merged (6 lanes, gates 8/8 green) + committee trust fixes + release files + diagrams | `git ls-remote origin main` |
| `gh-pages` | @02a298e — consoles rebuilt with honest labels (staging "Paging simulation", prod pre-auth banner "Production — operator access required") | `git ls-remote origin gh-pages` + served-byte checks |
| Release | **v0.1.0** tagged @6013c03, GitHub release live | https://github.com/AdityaPagare619/sentinel/releases/tag/v0.1.0 |
| Consoles | production / staging / loadtest / preview-v2 all HTTP 200 | https://adityapagare619.github.io/sentinel/ (and subpaths) |
| Backend | Vercel `dpl_7erEzK2CrSxtp3K1bQhWpBxWW2JP` (built from 9f51807 code; docs-only diff since), stable alias serves it; live 200, CORS preflight OK, 401s fast + `WWW-Authenticate` | direct probes |
| License | MIT (`LICENSE`, `CHANGELOG.md`, `CONTRIBUTING.md` at repo root) | v0.1.0 tag |

## What this is / is not (honest scope)

- **Is:** a self-hostable page-or-suppress engine (receiver → correlator → race → gate → forwarder), four honest console surfaces, a structural simulation tier (FakePD only — zero real PagerDuty contact, ever), a millions-scale load-test proof, and a runnable shadow-pilot harness awaiting real traffic.
- **Is not (yet):** a production paging deployment. No engine is deployed anywhere receiving real webhooks; no real page has ever been sent or suppressed; the deployed kill flag guards no paging path on the serverless tier (the real halt proof is the receiver-topology drill, 9.75ms).

## Milestone timeline (artifact-backed)

- **2026-10-02** — MVP v0.1 build: client, pipeline, tuner, eval harness, README (full suite green).
- **2026-10-05** — Repo public; GitHub Pages production + staging live.
- **2026-10-06** — Load test: 1,092,876 alerts @ 187.4/s (virtual time, one box), $0.02 Jev spend, zero real PD contact; 3 real bugs found/fixed (race, 7GB OOM, fsync cap).
- **2026-10-07** — Brutal audit (12 workers) → P0 fix wave → FAANG principal wave (6 teams, all merged, pre-pr-gate 8/8). Kill switch re-wired on the true receiver topology (re-drilled 9.75ms). Backend redeployed + verified. FAANG-standard diagrams committed (`docs/planning/diagrams/` — architecture, alert sequence, deployment topology; mandatory for all design docs since Aditya's diagram-law order).
- **2026-10-08** — Ship-readiness verdict waves (internal 4 teams + external 5-judge committee): **NOT production-ready**, unanimous. The gate: a real shadow-pilot report. Committee items 2–4 done (trust-copy fixes, v0.1.0 cut, stale plans superseded → `docs/planning/CURRENT_PLAN.md`), vendor posture doc written (`docs/planning/VENDOR_POSTURE.md`), shadow-pilot harness built + proven runnable (`docs/planning/shadow-pilot/`). Aditya ruled: open source, no pricing/billing — commercial blockers struck; self-hosting must be trivial.

## Open items — whose side

**Aditya's side (nothing here blocks Petu's work):**
- [ ] Kill-switch hand-test on the production console (engage → "Paging halted" → re-arm → recover + confirm Operations Health populates). Note: proves API/UI wiring on the serverless tier; the paging-halt proof lives on the receiver topology.
- [ ] `TYPESAFE_API_KEY` in Vercel env (project `sentinel-platform`). Until set, the console truthfully shows judge-down. Also needed: `key_configured` must read env as well as the integrations store (code fix, Petu's side).
- [ ] Real traffic source for the shadow pilot (2 weeks, real labeled traffic → published suppression report). Harness, runbook, and labeling protocol are ready; this is the single gate to production-ready per all five judges.
- [ ] Credential rotation in GitHub settings (exposed via chat on 2026-10-03).
- [ ] Optional: GitHub push protection on; UptimeRobot monitors (₹0).

**Petu's side:**
- [ ] Production-flip call — gated on Aditya's hand-test + a real receiver deployment; the flip is a decision, not a flag flip.
- [ ] `key_configured` env-vs-store fix (named by the integration verdict team).
- [ ] Shadow-pilot verdict once the pilot runs.
- [ ] AC8 / CONSOLE_BANNED substring gates made negation/comment-aware (stopgap rewords hold).
- [ ] Label or delist `/preview-v2/` — still carries the pre-fix kill copy publicly (Petu's call, pending the flip decision).

## Standing laws (unchanged)

Deterministic gate owns every decision (Jev advises only, ordinal never calibrated) · fail-open: silence when we should page is the worst failure · kill = HALT all paging, fail-closed · sim/loadtest structurally FakePD, real PD only via customer BYOK + explicit operator action · ₹0 ops · verify the artifact, never the report (`git ls-remote` before every "pushed" claim).
