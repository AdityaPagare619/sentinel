# Sentinel — Decision Log

Format: date — **decision** (rationale, who called it). Written at decision time.
Latest decisions on top.

## 2026-10-03 — Saturday wave (Tripwire)

- 2026-10-03 — **Sunday go/no-go checklist owned by Tripwire.**
  `ops/acceptance-sunday.md`: the 10 exit bars as executable rows (bar, exact
  verification step, command/click-path, pass criterion, owner); Phase A
  non-negotiables (bars 10/7/8/9) run first with automatic NO-GO on any red;
  two-key signatures per row (builder "built as specified" + Tripwire "verified
  broken-safe"). (Saturday Wave Coordinator brief; Tripwire.)
- 2026-10-03 — **The live-kill ritual.** Go/no-go requires re-running
  kill-the-client + platform-tier-death LIVE at the 20:30 table (demonstrated,
  not asserted), a named adversarial chair who must concede on the record, and
  the flip-ledger recital (Law 3: flips audited, never hidden).
  (`ops/fault-injection-plan.md`; Tripwire.)
- 2026-10-03 — **CI `startup_failure` root cause: dead workflow record, not the
  file.** GitHub registered the workflow as `BuildFailed` on first ingestion
  (2026-10-02 18:41:07, transient, repo-creation race); all 16 runs bound to
  the dead record 373379524; the file bytes were always valid (active record
  373379827 since 18:41:30). No file fix needed — a fresh run is the
  verification; escalation = forced re-ingest. (`ops/ci-repair.md`; Tripwire.)
- 2026-10-03 — **CI fix, second attempt: workflow renamed to
  `sentinel-ci.yml`.** The "transient" hypothesis was FALSIFIED — fresh runs at
  18:59 UTC still bound to the dead record, so the (repo, path) → record
  mapping is permanently poisoned and re-pushing the same path can never fix
  it. `git mv .github/workflows/ci.yml .github/workflows/sentinel-ci.yml`
  (zero content change); cross-lane path references in METHODOLOGY.md §3 and
  LOCAL-OPS.md §4 updated and flagged. Tripwire owns `ci.yml` test gates
  (TEAMS.md), so the rename is in-lane. (`ops/ci-repair.md`; Tripwire.)

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
