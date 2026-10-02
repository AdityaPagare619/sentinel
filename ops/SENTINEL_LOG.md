# Sentinel — Living Log

The single complete dated record of the build: every milestone, commit, decision
pointer, number, verdict, and lesson. Failures are documented as failures.
Format: date/time (IST) — event — evidence. Latest entries on top.

## 2026-10-02

### 19:00 — Night R&D sprint complete (`8eda683`, `lane/research-night-1`)
- Standing R&D function chartered: `research/CHARTER.md` (daily cadence,
  per-department questions, citation standard, ADR-only spec changes).
- 5 department agendas (`research/sre-field.md`, `jev-behavior.md`,
  `competitive.md`, `security-privacy.md`, `ux-freedom.md`) + 5 web-verified
  sprint notes + `research/synthesis-2026-10-02.md` with 9 proposed ADRs.
- Top findings: correlator missing industry craft (→ ADR-001, Forge review);
  TypeSafe rate limits disagree 6.25× (design to tighter bound); 4 gateways
  serve Jev today + open-weights encoder-exit precedent; sub-second thesis
  unearned — 11.4s India N=1 vs 70–500ms US spec, latency campaign N≥100 is
  top-leverage; wedge validated by vendors' own pages ("no affordable AIOps tier").
- Aditya's read confirmed: last night's research covered the opportunity, not
  the craft. Tonight covered the craft.

### 18:50 — Platform architecture wave complete (3 lanes, unmerged)
- `lane/company-v2` → `afd16dc`: 8 per-chief operating files in `chiefs/`
  (ARCHITECT, PAGER, ORACLE, VAULT, LEDGER, PRISM, TRIPWIRE, RELAY) — mandate,
  skills, rituals, artifacts, interfaces, staffing, DoD, prohibited actions.
- `lane/platform-arch` → `6ed1138`: `PLATFORM_ARCHITECTURE.md` **frozen**
  (6-feature spine, do-no-harm laws, hot-path/platform separation, read API +
  SSE, scope boundary), `WAVE_PLAN.md` (Sat A/B/C, Sun AM/PM, 10 exit bars).
- `lane/ops-deepening` → `5020e5a`: `ops/RUNBOOKS.md` (RB-1/2/3),
  `ops/STANDING_ORDERS.md`, decision-log + constraint-registry deepening.
- Trigger: Aditya's 18:45 correction — engine-before-platform was the wrong
  order; roster-not-company was the wrong depth. Accepted in full.

### 18:20 — Evening brief delivered
- Day-one complete: v0.1 engine, 10 base docs (3 commits, 2 branches), Jev key
  live + real-call verified (latency flag open), standing orders law.
- Single key blocker: GitHub push 403/404 — token can't see the new repos.
  Aditya's 30-sec phone step (add repos to the fine-grained token's
  selected-repository access) still pending.

### 13:57 — Sentinel v0.1 MVP complete (`a8a7cf4`, `lane/code-mvp-v0.1`)
- Build coordinator delivered all 6 build-order items + M1–M6: System-One
  client + mock, webhook receiver, deterministic correlator, gate + conservative
  threshold policy, PagerDuty-style forwarder, SQLite audit log, threshold tuner
  CLI, synthetic data generator, eval/calibration harness, README, demo
  walkthrough. Petu independently ran the suite: **140/140 tests green.**
- Synthetic smoke: 2,000 alerts, conf 0.90 → 1,123 suppressions (67.3% of
  baseline pages), zero synthetic SEV1 false suppressions.
- Follow-up fixes: `5c4bcb1` (mock dev mode, dead-code removal, doc corrections).

### 13:28 — Real TypeSafe Jev key connected + verified
- Aditya submitted via secure card; connector `custom.typesafe` live;
  skill at `~/workspace/skills/typesafe/`.
- Smoke call: HTTP 200, `answers.disposition` = choice "page", confidence 0.9,
  probabilities {page: 0.94, cannot_determine: 0.05, suppress: 0.01} — shape
  matches the client.
- **Open measurement:** first-call latency **~11.4s** vs 70–500ms spec.
  Oracle owns the latency campaign (N≥100, cold/warm p50/p95/p99) before any
  latency number is quoted. Never request the raw key again.

### 13:20 — Standing orders issued + v0.1 build ordered
- Aditya's permanent orders: design first-class, depth bar = the JEV report,
  coordinate via contracts + always web-verify, key-ask pre-authorized but
  never stall. → `ops/STANDING_ORDERS.md`.
- v0.1 build ordered to the build coordinator (single-owner: `src/`, `tests/`,
  `PROGRESS.md`, `ARCHITECTURE.md`).

### 13:10 — Base setup committed (`f4aa005`, `base/setup-docs`)
- CHARTER.md, METHODOLOGY.md, TEAMS.md, ROADMAP.md, LOCAL-OPS.md,
  `ops/decision_log.md`, `ops/constraint_registry.md`, `.github/workflows/ci.yml`,
  `.github/pull_request_template.md`, `.gitignore`.

### 09:10 — JEV research report delivered (888 lines) — Sentinel picked
- `~/workspace/jev-product-research/JEV_RESEARCH_REPORT.md`: 9 sections,
  10 field studies, 360-idea scored funnel, 3 production architectures.
- #1 recommendation: page-or-suppress SRE triage middleware. Aditya read it in
  full and ordered the build phase: two parallel builds (Sentinel + InboxPilot),
  separate rooms and coordinators.
