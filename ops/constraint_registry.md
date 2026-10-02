# Sentinel — Constraint Registry

Hard constraints. Owned by the Constraint Sentinel. Any plan violating one is stopped
and replanned. Verified 2026-10-02 against `ARCHITECTURE.md` + the research report.

## Jev / TypeSafe (the load-bearing limits)

- **255-choice ceiling** on any Choice question (API hard limit). Hierarchical design:
  Jev picks category/team ≤255; deterministic retrieval picks the runbook. Never
  design a question with more than 255 options.
- **Non-deterministic: 1.3–2.2% flip rate, no seed.** Repeatability probes
  (flip <2%, option-shuffle <3%) must stay green in CI. Every decision row stores
  `input_sha256` + `jev_model` so flips are auditable, never mysterious.
- **Probabilities rounded to 0.01; noul clamped to [0.01, 0.98].** Threshold math
  must not assume finer precision.
- **70–500ms inline latency is the product spec.** Any design moving triage off the
  hot path (e.g. a 2–3s LLM call during storms) is wrong.
- **~40 req/s dynamic rate limit.** Storm-collapse (one call per storm, not per alert)
  is load-bearing at scale, not an optimization.
- **TypeSafe is a single point of failure:** no SLA, no SOC 2, US-only hosting,
  ~$66 liability cap (vs a missed page costing $300K–$5M/hr), MCA forbids
  distilling outputs. Mitigations: fail-open everywhere, BYOK blast isolation,
  customer-VPC relay (designed, not v0.1), fine-tuned-encoder exit (200–500 outcome
  labels banked from shadow pilots).
- **EU data residency:** unresolved at v0.1 — no EU customer data through the US-only
  API. Enterprise answer is the self-hosted relay (designed, not built).

## Product / architecture locks

- **The ONLY autonomous action is "page a human."** Never auto-remediate, never
  auto-dismiss security/compliance alerts, never build the "Jev incident commander".
- **Suppression requires the triple lock** (P(p1) < 0.002, conf ≥ 0.90, allowlisted
  fingerprint). Suppress is never the default; uncertainty always pages.
- **Append-only audit log.** No UPDATE/DELETE paths in `AuditLog`. Every alert gets
  a row — including passthroughs and suppressions.
- **Stdlib only (v0.1).** No pip deps. Production swap path (FastAPI/gunicorn,
  Postgres, Redis, KMS envelope) is documented, not built.
- **No accuracy claims** in marketing, output, or docs. Only calibration metrics on
  the customer's own labels.

## Schedule / process

- **Preview-1: EOD Oct 4, 2026 IST.** Working demo, not slides (see ROADMAP.md).
- **Definition of Done** (METHODOLOGY.md §4): tests green + docs updated + PROGRESS.md
  entry + demo-able + no secrets + Claim-Auditor pass.
- **File ownership:** base setup never touches `src/`, `tests/`, `PROGRESS.md`,
  `ARCHITECTURE.md`, `_shims.py` while the build coordinator is mid-flight.
- **No secrets in git.** `.gitignore` + CI secrets-grep. `TYPESAFE_API_KEY` via env
  only; never in code, logs, audit rows, or error messages.
