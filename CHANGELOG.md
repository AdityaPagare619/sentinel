# Changelog

All notable changes to Sentinel. Dates in IST.

## [0.1.0] — 2026-10-08

First release. What v0.1.0 **is**: the deterministic page-or-suppress engine
(receiver → correlator → race → gate → forwarder), three consoles (production,
staging simulation, load-test dashboard), the simulation harness with
cost-capped real-Jev sampling, and the full gate/test suite. What it **is not**:
a production paging deployment — no engine is deployed on the production tier,
no real page has ever traversed the stack, and the shadow-pilot report (the
go-live evidence) does not exist yet. See `docs/planning/CURRENT_PLAN.md`.

### Added
- MIT license (repo was previously all-rights-reserved by default).
- `CONTRIBUTING.md`: build, gates, PR discipline.
- Kill-switch re-wire on the true receiver topology (legacy `Forwarder`),
  drilled at 9.75ms engage latency
  (`ops/drills/kill-drill-receiver-topology-20261007.json`).
- `scripts/ops/sentinel-kill`: stdlib operator CLI for the receiver kill switch.
- Executable promotion gates (`scripts/ops/pre-pr-gate.sh`, 8 stages) with the
  Team 6 ↔ Team 3 gate-interface contract.
- Structural banner contract test (`tests/test_banner_contract.py`).
- Three architecture diagrams (`docs/planning/diagrams/`) under the
  diagram-design standard.
- Shadow-pilot harness (`docs/planning/shadow-pilot/`): dry-run runner,
  labeling protocol, suppression report template, 2-week runbook.
- Vendor posture document (`docs/planning/VENDOR_POSTURE.md`).
- External committee launch-readiness review
  (`committee/EXTERNAL_COMMITTEE_2026-10-08.md`).

### Fixed
- Brutal audit (2026-10-07) P0s: kill wiring on the true topology, fail-open
  ladder race, sim safe-default/structural FakePD, production banner
  accessibility, kill-switch copy semantics ("Paging halted", fail-closed),
  Ops Health CORS root cause, dead calibration assets, loadtest chart/headline
  honesty.
- FAANG principal wave (6 teams): ordinality sweep (Jev confidence is ordinal,
  never calibrated probability), dual-accept token rotation, repeatable deploy
  docs, unified "handled quietly" terminology, dead-letter alarm repair.
- Trust presentation (committee item 2): load-test headline carries its
  virtual-time qualification inline; staging "Paging live" → "Paging
  simulation"; SEV1 fixture language softened with "scripted replay" marker;
  production banner no longer claims "Live" before verification.

### Known limitations (honest)
- No engine on the deployed production tier; the kill flag there guards no
  paging path (per-instance, honestly disabled in the console).
- Legacy `Forwarder` ships in the paging path (any-2xx acceptance, no
  outbox/retry); the honest `DurableForwarder` is not yet wired in.
- Missing `TYPESAFE_API_KEY` is a boot failure on the engine path, not a
  degraded judge-down mode; the console's `key_configured` health read checks
  the ephemeral store, not the environment.
- Zero real PagerDuty pages ever sent; BYOK onboarding path not yet built.
- `preview-v2/` preserved with pre-shift copy (superseded, kept for judging).

## History (pre-release)

- **2026-10-07** — FAANG principal wave: six teams (engine, AI/ML, platform,
  security, UI/UX, quality) applied the audit methodology codebase-wide;
  merged to main as six gated merges.
- **2026-10-07** — P0 fix wave: ten teams closed the brutal-audit findings;
  true-topology kill drill passed (9.75ms).
- **2026-10-07** — Brutal audit: 12 auditors, department by department;
  verdict "not production-ready", deterministic core real.
- **2026-10-06** — Load test: 1,092,876 alerts at 187.4/s, 16.2M/day
  virtual-time projection on one box, $0.02 Jev spend, zero real PagerDuty
  contact; caught 3 real bugs (fail-open race, audit RAM, fsync cap).
- **2026-10-05** — Repo made public; GitHub Pages live (prod + staging).
- **2026-10-02..04** — Core engine, Jev integration, console v2
  ("quiet instrument" design).
