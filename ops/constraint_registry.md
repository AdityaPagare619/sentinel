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

## Platform tier (added 2026-10-02 evening — the platform pivot)

- **Hot-path/process separation.** The dashboard/platform tier runs as a SEPARATE
  OS process from the paging path. It reads ONLY from the audit log. It never
  imports hot-path modules in a way that couples deploys. UI load can never slow
  a page — this is enforced by Forge at review, not hoped for.
- **Audit log is the ONLY bridge.** Engine writes; platform reads. SQLite WAL mode
  + a read-only connection for the platform tier so dashboard queries never
  contend with the audit writer. (Postgres read replica is the SaaS upgrade.)
- **Jev call budget: ONE parallel call per alert.** The three questions (severity,
  team, disposition) go in a single parallel call — never sequential, never
  chained. The platform tier makes ZERO Jev calls on the read path; the simulator
  recomputes from stored probabilities, never re-calls.
- **Timeout policy protects the paging path, not hope.** The 11.4s first-real-call
  measurement (2026-10-02) vs the 70–500ms spec is an open question until Oracle's
  latency campaign (p50/p95/p99, cold vs warm) reports. Until then: tight client
  timeout, fail open fast. A slow Jev call delays NOTHING — it becomes a passthrough.
- **Backpressure, never drops.** On alert spikes: storm-collapse before the Jev
  call (one call per storm — load-bearing at 40 req/s, not an optimization);
  bounded receiver queue; shed *dashboard/API* traffic first under load, never
  paging-path traffic. The receiver never returns 5xx for a triage failure.
- **No new dependencies for the platform tier without Forge + a logged decision.**
  Default: stdlib HTTP server + vanilla JS + SSE. ₹0, zero supply-chain surface,
  same as the engine.
- **Sunday scope boundary is a constraint, not a suggestion.** Sunday 9 PM =
  working interactive platform a design partner can click through and run.
  Explicitly OUT: multi-tenancy, billing, SSO, production PagerDuty integration,
  the fine-tuned-encoder exit. Post-Sunday list lives in PLATFORM_ARCHITECTURE.md §8.
- **Every dashboard number has a source label.** Synthetic vs shadow vs production
  data is labeled ON THE VIEW. Prism + Oracle co-enforce. Presenting demo data as
  production data is a trust incident.
