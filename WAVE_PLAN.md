# Sentinel — WAVE PLAN: Saturday build → Sunday 9 PM

**Frozen:** 2026-10-02 evening · **Goal:** working interactive platform, Sunday 9 PM IST
**Governing docs:** `PLATFORM_ARCHITECTURE.md` (frozen — changes via ADR only),
`METHODOLOGY.md` (git machine), `ops/RUNBOOKS.md` (RB-1 push/PR/CI, RB-3 demo-day).
**Doctrine:** waves, not idling. Contracts first, parallel lanes, independent verify.
Lanes never block on each other — mock the contract and move.

## Tonight (Fri): freeze the direction — DONE

- [x] `PLATFORM_ARCHITECTURE.md` frozen (6-feature spine, do-no-harm laws, scope boundary)
- [x] `chiefs/*.md` — the operating company (8 chiefs, rituals, artifacts, interfaces)
- [x] `ops/` deepened: decision log (living), constraint registry (platform laws), `RUNBOOKS.md`
- [x] `WAVE_PLAN.md` (this file)
- [ ] GitHub push unblock — **Aditya's 30-sec phone step** (add `sentinel` to the
  fine-grained token's repo access). Everything below assumes it lands tonight;
  if not, lanes work on local branches and the push wave runs first thing Sat.

## Sat Wave A — contracts + mocks (09:00–12:00 IST)

Forge publishes the frozen platform contracts; every lane builds against mocks after.
No feature code before contracts.

| # | Work | Owner | Exit |
|---|---|---|---|
| A1 | Platform read API spec final (`PLATFORM_ARCHITECTURE.md` §5) + mock server returning fixture JSON for all 7 endpoints | Forge + 1 contract-scribe | mock API live locally; lanes can `curl` it |
| A2 | Simulator compute contract: `POST /api/simulate` request/response schema; pure-function signature shared with `tuner.py` | Oracle + Forge | schema frozen; calib lane codes against it |
| A3 | Dashboard data-source labels spec (every view labels synthetic/shadow/prod) | Prism + Oracle | label component spec'd |
| A4 | Fixture freeze: versioned synthetic dataset for the demo (Ledger) | Ledger | `tests/fixtures/demo-v1/` frozen, seed recorded |

**Wave A exit bar:** all four done, or Wave B doesn't start. (Relay enforces.)

## Sat Wave B — parallel lane builds (12:00–19:00 IST)

Six lanes, one branch each (`lane/<lane>-<desc>`), all against Wave A mocks.
Reviews are cross-lane (a different agent reviews — no self-merge).

| Lane | Branch | Builds | Chief review |
|---|---|---|---|
| dashboard-river | `lane/ui-river` | decision river page + SSE client + detail view | Prism (UX), Tripwire (reconnect) |
| dashboard-explorer | `lane/ui-explorer` | audit explorer search + decision detail + flip-audit view | Prism, Vault (no sensitive material) |
| dashboard-sim | `lane/ui-simulator` | threshold sliders + live recompute UI | Prism, Oracle (math ≡ tuner) |
| calib-api | `lane/calib-api` | `POST /api/simulate` compute, calibration bins/ECE/coverage endpoints | Oracle, Forge (contract) |
| analytics | `lane/ui-analytics` | noise analytics v1 (top checks, team load, breakdown) | Pager (realism), Ledger (aggregates) |
| onboarding | `lane/docs-onboarding` | 15-min flow: snippet + BYOK UX + guided storm script | Prism, Vault (key handling) |

**Parallelization rules:** max 2 builders per lane; PRs <300 lines; review SLA minutes.
Security/audit lane runs Vault's per-PR pass on anything touching ingress/audit.
Red Team takes one adversarial pass over the simulator ("can the thresholds be
gamed into hiding suppressions?").

**Wave B exit bar:** all six lanes have working code against the mocks, CI green
on each branch.

## Sat Wave C — first integration (19:00–22:00 IST)

- Swap mocks → real platform tier (stdlib server + SQLite WAL + read-only conn).
- End-to-end: synthetic storm → engine (mock Jev) → audit → river/explorer/
  simulator/analytics all live.
- Tripwire: fault-injection pass #1 (kill-the-client mid-run, 529 storm,
  malformed payloads). Results logged; failures become Sun AM tasks.
- Relay: wave report — landed vs plan, delta explained, Sun tasks assigned.

## Sun AM — hardening (09:00–14:00 IST)

- Tripwire: fault-injection pass #2 + acceptance checks encoded (`ops/acceptance-sunday.md`).
- Oracle: latency measurement campaign results in (p50/p95/p99, cold vs warm) —
  dashboard shows measured numbers; if the campaign is still running, the view
  shows the honest caveat.
- Vault: secrets sweep (`git log -p` grep) + audit-row sensitivity verification.
- Pager: disposition defensibility review on the demo script's suppressions.
- Prism: onboarding timed with a stopwatch (<15 min or fix task).
- Forge: hot-path separation audit — prove the platform tier cannot slow paging
  (separate process, read-only conn, zero shared locks).

## Sun PM — demo + goal (14:00–21:00 IST)

- 14:00–17:00: RB-3 T-4h checklist (full rehearsal, timed; recorded fallbacks ready;
  fixtures frozen; no merges after 17:00 except stop-the-line).
- 17:00–20:00: polish buffer. Only stop-the-line fixes merge.
- 20:30: go/no-go (Petu) — exit bars checked with evidence, or explicitly waived
  with a logged reason. No silent waivers.
- **21:00: Sunday goal.** Live demo for Aditya.

## Exit bars — Sunday 9 PM is GO iff all green

| # | Bar | Target | Evidence | Owner |
|---|---|---|---|---|
| 1 | Decision river live | streams decisions w/ confidence bars, click-through detail | live run | dashboard lane |
| 2 | Calibration dashboards | reliability diagram + ECE + coverage@τ per team, denominators on every chart | live run | Oracle |
| 3 | Threshold simulator | sliders recompute projections live; math ≡ tuner | live run + Oracle sign-off | calib lane |
| 4 | Audit explorer | search + full decision records + flip-audit view | live run | dashboard + Vault |
| 5 | Noise analytics v1 | top checks, team load, suppression breakdown | live run | analytics lane |
| 6 | Onboarding | zero → synthetic storm → river in <15 min, stopwatch | timed run | Prism |
| 7 | Do-no-harm | kill-the-client green mid-demo; platform tier death doesn't affect paging | fault-injection log | Tripwire |
| 8 | Hot-path latency | engine p95 unchanged with platform tier running vs not | benchmark | Forge + Tripwire |
| 9 | Honest data | every view labels its data source; zero accuracy claims | Prism + Oracle review | Prism |
| 10 | `main` green | full suite + probes + secrets-grep green at 21:00 | CI | Tripwire |

**What can slip:** P1s (noise timeline depth, extra analytics) — Petu's call, logged.
**What can never slip:** the four do-no-harm laws, bars 7–10, and the scope boundary.

## Top risks (watched, not wished away)

1. **GitHub push still blocked** → the visible-work machine Aditya demanded stays
   local. Mitigation: local branches + the same PR discipline; push wave runs the
   instant the token step lands. **This is the #1 external risk.**
2. **Real-key latency (11.4s measured once)** → if Oracle's campaign confirms slow
   Jev calls, the demo stays mock-backed (already the plan) and the timeout policy
   is the story, not the spec. Never let the demo depend on TypeSafe.
3. **Scope creep into Sunday** → Relay kills it with a citation to §8 of the
   platform spec. "That's post-Sunday" is a complete sentence.
4. **Lane collisions** → single-owner rule (LOCAL-OPS.md §2) + Forge's daily
   contract check. Crossing lanes = flagged PR, never silent edit.
