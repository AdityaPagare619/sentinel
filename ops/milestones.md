# Sentinel — Saturday Wave Milestones

**Window:** Sat 2026-10-03 00:30 IST → Sun 2026-10-04 21:00 IST.
**Grounded in:** `WAVE_PLAN.md` (frozen 2026-10-02 evening) and `ROAD_TO_LAUNCH.md` §3.
**Scope law:** no multi-tenancy, billing, SSO, or production PagerDuty
integration on Sunday — Relay kills scope creep with a citation.
"That's post-Sunday" is a complete sentence.

**Time reconciliation (ROAD_TO_LAUNCH.md §3):** WAVE_PLAN says Wave A
starts 09:00; working hours are 10:00–19:00 IST — so **09:30 is briefing
time, 10:00 is the implementation start.** The table below uses that
reconciliation everywhere.

## Milestone table

| # | Milestone | When (IST) | Owner chief | Exit criteria |
|---|---|---|---|---|
| M0 | Relay ops scaffold lands | Sat 00:30 | Relay | `ops/milestones.md`, `ops/blockers.md` written; 8 lane rows registered `in flight` in `ops/lane_registry.md`; branch pushed; PR open |
| M1 | Wave A kickoff brief | Sat 09:30 | Relay | all lanes acknowledge the frozen contracts + mocks plan; briefs landed |
| M2 | **Wave A exit — contracts + mocks frozen** | Sat 10:00–12:00 | Forge | A1 mock API live locally (lanes can `curl` it) · A2 `POST /api/simulate` schema frozen · A3 data-source label spec done · A4 fixtures `tests/fixtures/demo-v1/` frozen with seed recorded. **Wave B does not start until this is green — Relay enforces.** No feature code before contracts. |
| M3 | **Wave B exit — six parallel feature lanes** | Sat 12:00–19:00 | Relay (coord) | all six lanes (river, explorer, simulator, calib-api, analytics, onboarding) working against Wave A mocks; CI green per branch; cross-agent reviews done (no self-merge, PRs <300 lines); Vault per-PR pass on ingress/audit-touching code; Red Team adversarial pass on the simulator |
| M4 | **Wave C exit — first integration + fault injection** | Sat 19:00–22:00 | Tripwire (faults) + Relay (report) | mocks → real platform tier (stdlib server + SQLite WAL + read-only conn); synthetic storm → engine (mock Jev) → audit → river/explorer/simulator/analytics all live; fault-injection pass #1 (kill-the-client mid-run, 529 storm, malformed payloads) logged; failures assigned as Sun AM tasks; Relay's wave report published (landed vs plan, delta explained) |
| M5 | Oracle latency results in | Sun AM, by 12:00 | Oracle | N≥100 campaign: p50/p95/p99 cold vs warm + flip protocol; **timeout policy is set from measured data, not the spec**; dashboard shows measured numbers — or the honest caveat if the campaign is still running. (Sub-second thesis unearned until this lands.) |
| M6 | Secrets sweep + audit integrity | Sun AM, by 13:00 | Vault | `git log -p` secrets grep clean; audit-row sensitivity verified (no sensitive material in explorable records); webhook verification design done (gated by ADR-005) |
| M7 | Hot-path separation audit | Sun AM, by 14:00 | Forge (+ Tripwire) | proven: platform tier cannot slow paging (separate process, read-only conn, zero shared locks); engine p95 unchanged with platform tier running vs not — bar 8 evidence assembled |
| M8 | Demo rehearsal T-4h (RB-3) | Sun 14:00–17:00 | Relay | full timed rehearsal; recorded fallbacks ready; fixtures frozen; onboarding stopwatch-timed <15 min; **no merges after 17:00 except stop-the-line fixes** |
| M9 | **Go / no-go** | Sun 20:30 | Petu | the 10 exit bars (`WAVE_PLAN.md` §7 / `ROAD_TO_LAUNCH.md` §2) checked against evidence — or explicitly waived with a logged reason. **No silent waivers.** |
| M10 | **Sunday delivery — live demo** | Sun 21:00 | Relay + Petu | live demo for Aditya. GO iff all 10 exit bars green. |

## Exit bars recap (M9 checks these)

1. Decision river live · 2. Calibration dashboards (ECE + coverage, denominators
on every chart) · 3. Threshold simulator (math ≡ tuner, Oracle sign-off) ·
4. Audit explorer (search + flip-audit) · 5. Noise analytics v1 ·
6. Onboarding <15 min stopwatch · 7. Do-no-harm (kill-the-client green mid-demo) ·
8. Hot-path latency unchanged · 9. Honest data (every view source-labeled) ·
10. `main` green at 21:00 (suite + probes + secrets-grep).

## Decision checkpoints (not milestones — blockers)

- **Aditya:** ADR-001 (correlator craft), ADR-005 (webhook auth scope),
  ADR-007 (muted-not-dropped visibility) — accept/defer/reject.
  Tracked in `ops/blockers.md`; lanes code to the proposed ADR text and log
  assumptions until the decision lands.
- **GitHub push unblock:** landed Sat ~00:25 IST (all-access PAT, gh path
  canonical) — the visible-work machine is live; `ops/blockers.md` records it
  as killed, not forgotten.
