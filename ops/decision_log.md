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
