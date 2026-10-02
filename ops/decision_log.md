# Sentinel — Decision Log

Format: date — **decision** (rationale, who called it). Written at decision time.
Latest decisions on top.

## 2026-10-02 — base setup

- 2026-10-02 — **Repo is the source of truth, the room is the workbench.**
  `AdityaPagare619/sentinel` (private → public at Preview-1) holds code + docs;
  agents operate in `~/workspace/jev-builds/sentinel/`; the two mirror each other.
  (Base setup, Aditya's order.)
- 2026-10-02 — **Base-setup file ownership.** Base setup creates ONLY: CHARTER.md,
  METHODOLOGY.md, TEAMS.md, ROADMAP.md, LOCAL-OPS.md, `ops/decision_log.md`,
  `ops/constraint_registry.md`, `.github/workflows/ci.yml`,
  `.github/pull_request_template.md`, `.gitignore`. The build coordinator owns
  `src/`, `tests/`, `PROGRESS.md`, `ARCHITECTURE.md`, `_shims.py` — single-owner
  rule while helpers are mid-flight. (Base setup, Aditya's order.)
- 2026-10-02 — **`main` protected; PR-only; no self-merge.** Review must come from a
  DIFFERENT agent; green CI required; squash merges; `main` stays green — red main is
  stop-the-line. (METHODOLOGY.md §1.)
- 2026-10-02 — **False-suppress incident rule.** Any unexplained false suppress on a
  SEV1-class alert is treated as a SEV1: blameless postmortem, report derived from
  the audit log. (CHARTER.md design laws.)
- 2026-10-02 — **Preview-1 target: EOD Oct 4, 2026 IST.** Working demo (synthetic
  storm → triage → audit log → calibration report), not slides. Scope = M3–M6.
  Exit bars: ≥60% suppression, ZERO false-suppress on synthetic SEV1s, p95 <1s on
  mock, every decision audited. (ROADMAP.md; Aditya's order.)

## Seeded from PROGRESS.md (build coordinator's decisions, recorded verbatim)

- 2026-10-02 — **Python stdlib only (no pip deps).** `http.server` for the receiver,
  `urllib` for the Jev client (custom User-Agent per the documented 403 quirk),
  `unittest` for tests. Rationale: ₹0 ops, runs anywhere, zero supply-chain surface;
  FastAPI/gunicorn swap documented as the production upgrade path.
- 2026-10-02 — **v0.1 is Jev-only (no LLM adjudicator).** Per the architecture doc —
  hybrid deferred to a measured premium tier.
- 2026-10-02 — **SQLite for the v0.1 audit store** (single-file, zero-ops); schema
  designed Postgres-compatible for the SaaS upgrade.
- 2026-10-02 — **Design frozen for v0.1** (`ARCHITECTURE.md`): receiver → correlator
  → gate → forwarder + audit; tuner CLI; eval harness; System-One client + mock.
  Dashboard UI, label joiner automation, SSO, self-hosted relay, LLM adjudicator,
  fine-tuned-encoder exit all deferred (designed, not built).
- 2026-10-02 — **Fail-open architecture.** Every exception path → pass-through;
  unparseable receiver input is forwarded unchanged, never dropped; kill-the-client
  test is a release blocker.
- 2026-10-02 — **Suppress triple lock.** P(p1_critical) < 0.002 AND Q3 confidence
  ≥ 0.90 AND fingerprint ∈ customer-verified allowlist — from expected-cost math
  with C_FP=$100, C_FN=$50,000 (founder-grade estimates; tuner + shadow pilots
  replace them with measurements).
- 2026-10-02 — **Shadow-first deployment.** Weeks 1–2 of any deployment run
  shadow-only (log dispositions, change nothing); the weekly shadow report is the
  sales collateral.
- 2026-10-02 — **Honest numbers only.** No accuracy claims in marketing or output;
  only calibration curves on the customer's own labels. Synthetic eval proves
  plumbing, never production accuracy.

## 2026-10-02 — evening: the platform pivot (Aditya's correction)

- 2026-10-02 ~18:45 — **Aditya's verdict: engine before platform was the wrong order;
  roster before company was the wrong depth.** The v0.1 middleware is a strong engine
  (fail-open, triple-lock, audit-first, 140/140), but the team built it before freezing
  the PLATFORM architecture, and TEAMS.md was a roster, not an operating company.
  Correction accepted in full. Tonight's work: per-chief operating files
  (`chiefs/*.md`), the frozen `PLATFORM_ARCHITECTURE.md`, and `WAVE_PLAN.md` — no code
  until the direction is frozen. (Aditya's order; this decision.)
- 2026-10-02 ~18:45 — **Sunday 9 PM goal: the entire interactive platform, working.**
  Not a wrapper, not slides — a design partner can click through it and run it:
  live decision river, calibration dashboards, threshold simulator, audit explorer,
  noise analytics, 15-minute onboarding. Error rate extremely low; Jev's speed must
  not be degraded by our architecture; the software must be *interactive* and
  feature-rich *besides* Jev. (Aditya's order; scope boundary in
  PLATFORM_ARCHITECTURE.md §8.)
- 2026-10-02 ~18:45 — **Do-no-harm laws adopted as non-negotiable** (from Aditya's
  "things that shouldn't happen"): (i) the platform never adds latency to the paging
  path beyond the Jev call itself; (ii) fail-open always, uncertainty pages;
  (iii) zero *harmful* errors as the target — triple-lock suppress, flips audited
  never hidden (Jev's 1.3–2.2% flip floor is irreducible and is said plainly);
  (iv) honest scope — Sunday is a clickable working platform, NOT multi-tenancy,
  billing, SSO, or production PagerDuty integration. (PLATFORM_ARCHITECTURE.md §4.)
- 2026-10-02 ~18:45 — **Platform/hot-path separation is architectural, not aspirational.**
  The dashboard tier runs as a SEPARATE process, reads ONLY from the audit log
  (SQLite WAL, read-only connection), and never imports hot-path modules in a way
  that couples deploys. UI traffic can never contend with the audit writer or slow
  a page. (PLATFORM_ARCHITECTURE.md §3; Forge enforces.)
- 2026-10-02 ~13:28 — **Real Jev key connected + verified.** Aditya submitted via
  secure card; connector `custom.typesafe` live. Petu's smoke test: HTTP 200,
  `answers.disposition` = choice "page", confidence 0.9, probabilities
  {page: 0.94, cannot_determine: 0.05, suppress: 0.01} — shape matches the client.
  **Open measurement question:** first-call latency was **~11.4s** vs the 70–500ms
  spec. Could be cold start; Oracle owns the latency measurement campaign
  (p50/p95/p99, cold vs warm) before anyone quotes latency numbers. Until measured,
  the timeout policy (tight timeout, fail open fast) is what protects the paging
  path — not hope. (Petu; skill at `~/workspace/skills/typesafe/`.)
- 2026-10-02 ~13:40 — **Vercel connector available; deploy only when needed.**
  v0.1 is a stdlib middleware — nothing to deploy yet. Vercel is the target for the
  dashboard/marketing surface when it exists. No premature deploys. (Aditya's order.)
- 2026-10-02 ~13:40 — **Reporting = PETU-LABS rhythm.** Detailed morning brief
  09:30 IST, evening debrief at end of working hours, no ad-hoc daytime dumps.
  Working hours 10:00–19:00 IST at full intensity. (Aditya's order.)
- 2026-10-02 ~13:40 — **The bar: harder and broader than the other teams.**
  Sentinel teams out-work the TFB and studio teams — deeper research, more code,
  wider scope. Written as the standard, measured in the briefs. (Aditya's order.)
- 2026-10-02 ~18:45 — **15 minutes of deep reading before direction.** Aditya gave
  Petu 15 minutes to go through everything in depth and come back with the right
  path — including correcting Aditya where he's wrong. The architect's verdict
  (delivered to Petu): (1) the platform-before-engine correction is right and is
  now executed; (2) "zero errors" needs the honest form — zero *unexplained* errors;
  the flip floor is irreducible; (3) Sunday 9 PM is achievable for a clickable
  design-partner platform, NOT for production (the scope boundary protects the goal);
  (4) the 11.4s real-key latency vs "sub-second inline" is the biggest technical
  risk to the thesis — handled by timeout policy + honest measurement, not assumed
  away. (This decision.)
