# ROAD TO LAUNCH — Master Plan

> **SUPERSEDED (2026-10-08).** This plan targeted Oct 4, 2026 (past). The
> current plan is `docs/planning/CURRENT_PLAN.md`. Kept for history only.

**Standing:** v0.1 engine built and tested (140/140 green); platform direction
frozen; company operating files in place.
**Target:** working interactive design-partner platform, **Sunday 4 Oct 2026,
21:00 IST.** Then: real-key shadow pilot → Preview-2.
This document is the operating system for getting there. Governing docs:
`PLATFORM_ARCHITECTURE.md` (frozen), `WAVE_PLAN.md` (the schedule),
`METHODOLOGY.md` (the git machine), `ops/RUNBOOKS.md` (RB-1/2/3).

## 1. Mission (one paragraph)

Build the drop-in pre-page gate that kills pager noise at the source — and the
trust layer around it that makes the decision *defensible*: expected-cost
threshold tuning, per-team calibration evidence, an immutable audit log, and a
shadow pilot that proves savings before a single page changes. The product we
sell is not the classifier. It is the trust layer, and on Sunday a design
partner can click through it, run it, and see it work.

## 2. Preview-1 exit bars (Sunday 21:00 IST is GO iff all green)

The full 10-bar table with evidence and owners is `WAVE_PLAN.md` §7. The shape:

| # | Bar | Target |
|---|---|---|
| 1–6 | Feature spine live | decision river, calibration dashboards, threshold simulator, audit explorer, noise analytics v1, 15-min onboarding |
| 7 | Do-no-harm | kill-the-client green mid-demo; platform-tier death doesn't affect paging |
| 8 | Hot-path latency | engine p95 unchanged with platform tier running vs not |
| 9 | Honest data | every view labels its data source; zero accuracy claims |
| 10 | `main` green | full suite + probes + secrets-grep green at 21:00 |

What can slip: P1s (Petu's call, logged). What can never slip: the four
do-no-harm laws, bars 7–10, and the scope boundary (no multi-tenancy, billing,
SSO, or production PagerDuty integration on Sunday).

## 3. Wave plan summary

- **Fri (done):** direction frozen — platform architecture, 8 chief operating
  files, ops deepened, night R&D sprint (charter + 5 agendas + 9 ADRs).
- **Sat Wave A — contracts + mocks:** Forge freezes the read API/SSE contracts;
  every lane builds against mocks after. No feature code before contracts.
  *Known conflict to reconcile: WAVE_PLAN.md says 09:00; working hours are
  10:00–19:00 IST — 10:00 is the implementation start, 09:30 is briefing time.*
- **Sat Wave B — six parallel lanes:** decision river, calibration dashboard,
  threshold simulator, audit explorer, noise analytics, onboarding/docs/security
  — all against frozen mocks, cross-agent review, no self-merge.
- **Sat Wave C — first integration + fault injection:** mocks → real platform
  tier (stdlib server + SQLite WAL + read-only conn); kill-the-client mid-run,
  529 storm, malformed payloads. Failures become Sun AM tasks.
- **Sun AM — hardening:** acceptance checks, Oracle's latency campaign results
  in, Vault secrets sweep, Pager disposition review, Prism onboarding timed,
  Forge hot-path separation audit.
- **Sun PM — rehearsal, go/no-go 20:30 (Petu), 21:00 delivery.** RB-3 demo-day
  checklist governs; no merges after 17:00 except stop-the-line fixes.

## 4. Top 5 risks (watched, not wished away)

| # | Risk | Owner | Mitigation |
|---|---|---|---|
| 1 | ~~**GitHub push still blocked** (token 403/404) — the visible-work machine stays local~~ — **KILLED 2026-10-03 ~00:25 IST**: all-access PAT installed; 7 branches pushed, PRs #1–#6 open, merges in flight | — | — |
| 2 | **Real-key latency 11.4s (N=1)** vs 70–500ms spec — the sub-second thesis is unearned | Oracle | latency campaign N≥100 (cold/warm p50/p95/p99); tight timeout + fail-open protects the path meanwhile; demo stays mock-backed |
| 3 | **TypeSafe rate limits disagree 6.25×** (live docs vs community) | Oracle + Forge | design to the tighter bound; storm-collapse (one call per storm) is load-bearing regardless |
| 4 | **Scope creep into Sunday** | Relay | kills with a citation to the scope boundary; "that's post-Sunday" is a complete sentence |
| 5 | **Lane collisions / demo depends on TypeSafe** | Forge + Tripwire | single-owner rule + contract checks; demo never depends on vendor availability — mock-backed with recorded fallbacks |

## 5. Decisions needed from Aditya

1. ~~**The token step** — add `AdityaPagare619/sentinel` (and `inboxpilot`) to the
   fine-grained PAT's selected-repository access. Unlocks: pushes, PRs, reviews,
   GitHub Actions, the entire visible-work machine. Nothing else unblocks this.~~
   — **DONE 2026-10-03 ~00:25 IST** (all-access PAT installed by Aditya; PAT path
   via `gh` CLI is now canonical for GitHub writes, App connector is backup only).
2. **ADR-001** — correlator craft alignment (flap-debounce, resolved/refired
   episodes, P1/P2 never silenced): accept for the platform wave, defer, or
   reject. Forge's call to recommend; Aditya's to decide.
3. **ADR-005** — webhook auth hardening scope for Sunday.
4. **ADR-007** — muted-not-dropped visibility state in the decision river.

## 6. After Sunday (sketched, not committed)

Real-Jev shadow pilot with one friendly org, calibration dashboard v1 on real
data, per-team threshold re-tuning, label-joiner automation, public repo +
pricing page. Preview-2 scope is decided after the Sunday debrief — not before.
