# Sentinel — Build PROGRESS

**Project:** Sentinel — SRE page-or-suppress triage middleware (Jev System-One decision model)
**Coordinator:** Petu's build coordinator (reports to Petu; Aditya sees via briefs)
**Env:** `~/workspace/jev-builds/sentinel/` — all work lives here
**Started:** 2026-10-02 ~13:00 IST
**Spec foundation:** `~/workspace/jev-product-research/JEV_RESEARCH_REPORT.md` §§1,7,8; `notes/phase5-arch1-paging.md`; `notes/phase1-jev-deepdive.md` §§2.1–2.7

## Build order (MVP v0.1)

- [x] 1. ARCHITECTURE.md — design doc (frozen 2026-10-02)
- [x] 2. System-One wire-format client (`src/sentinel/client.py`) — DONE 2026-10-02 (Helper A: client+models+questions+state, 41/41 tests pass, coordinator-verified)
- [ ] 3. Webhook receiver + triage pipeline — Helper B running (mid-flight; full-suite shows its tests still being written)
- [x] 4. Threshold tuner CLI (`tuner.py`) — DONE 2026-10-02 (Helper C, tests pass, coordinator-verified)
- [x] 5. Eval harness (`evalharness.py` + `synthetic.py`) — DONE 2026-10-02 (Helper C, 24/24 tests pass, coordinator-verified)

## Integration notes (for coordinator review when all land)
- Box has no `python`, only `python3` — README must use `python3` in all commands.
- `src/sentinel/_shims.py` appeared (not Helper A's) — review at integration; remove if redundant.
- Helper A spec resolutions accepted: Q2/Q3 instruction one-liners; state cap enforced on whole-state JSON tokens; truncate-lowest-priority-first semantics; mock-local canonical-JSON helper (avoids import cycle).
- [ ] 6. README.md — after code lands (coordinator)

## Milestones

- [x] M1: ARCHITECTURE.md complete (design frozen for v0.1)
- [x] M2: client + mock + unit tests green (41/41, coordinator-verified 2026-10-02)
- [ ] M3: receiver → correlator → gate → forwarder pipeline working end-to-end on synthetic alerts (mock Jev)
- [x] M4: tuner CLI produces thresholds + savings projection from labeled data (smoke: 2,000 synthetic alerts → conf 0.90, 1,123 suppressions / 67.3% of baseline pages)
- [ ] M5: eval harness runs; calibration report generated; repeatable probes green (harness DONE — severity acc 0.9765, ECE Q1 0.0956/Q3 0.0819, false-suppress 0.0000, flip+shuffle PASS; full-suite green pending Helper B)
- [ ] M6: MVP complete — README done, full test suite green, demo walkthrough recorded in PROGRESS

## Decisions log

- 2026-10-02: Python stdlib only (no pip deps) — `http.server` for the receiver, `urllib` for the Jev client (custom User-Agent per the documented 403 quirk), `unittest` (stdlib) for tests. Rationale: ₹0 ops, runs anywhere, zero supply-chain surface; FastAPI/gunicorn swap documented as the production upgrade path.
- 2026-10-02: v0.1 is Jev-only (no LLM adjudicator) per the architecture doc — hybrid deferred to a measured premium tier.
- 2026-10-02: SQLite for the v0.1 audit store (single-file, zero-ops); schema designed Postgres-compatible for the SaaS upgrade.

## Blockers / decisions needed

(none yet)
