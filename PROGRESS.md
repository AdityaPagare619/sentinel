# Sentinel — Build PROGRESS

**Project:** Sentinel — SRE page-or-suppress triage middleware (Jev System-One decision model)
**Coordinator:** Petu's build coordinator (reports to Petu; Aditya sees via briefs)
**Env:** `~/workspace/jev-builds/sentinel/` — all work lives here
**Started:** 2026-10-02 ~13:00 IST
**Spec foundation:** `~/workspace/jev-product-research/JEV_RESEARCH_REPORT.md` §§1,7,8; `notes/phase5-arch1-paging.md`; `notes/phase1-jev-deepdive.md` §§2.1–2.7

## Build order (MVP v0.1)

- [x] 1. ARCHITECTURE.md — design doc (frozen 2026-10-02)
- [x] 2. System-One wire-format client (`src/sentinel/client.py`) — DONE 2026-10-02 (Helper A: client+models+questions+state, 41/41 tests pass, coordinator-verified)
- [x] 3. Webhook receiver + triage pipeline — DONE 2026-10-02 (Helper B: correlator/audit/gate/forwarder/receiver; kill-the-client release-blocker tests pass; coordinator-verified)
- [x] 4. Threshold tuner CLI (`tuner.py`) — DONE 2026-10-02 (Helper C, tests pass, coordinator-verified)
- [x] 5. Eval harness (`evalharness.py` + `synthetic.py`) — DONE 2026-10-02 (Helper C, 24/24 tests pass, coordinator-verified)
- [x] 6. README.md — DONE 2026-10-02 (coordinator; quickstart, BYOK, env table, honest limitations)

## Milestones

- [x] M1: ARCHITECTURE.md complete (design frozen for v0.1)
- [x] M2: client + mock + unit tests green (41/41, coordinator-verified 2026-10-02)
- [x] M3: receiver → correlator → gate → forwarder pipeline end-to-end (mock Jev) — verified 2026-10-02 via live mock-mode smoke test (HTTP 200s, audit rows written, fail-open on mock error) + loopback receiver tests incl. dead-Jev byte-identical relay
- [x] M4: tuner CLI produces thresholds + savings projection from labeled data (smoke: 2,000 synthetic alerts → conf 0.90, 1,123 suppressions / 67.3% of baseline pages)
- [x] M5: eval harness runs; calibration report generated; probes green; FULL SUITE 140/140 OK (2026-10-02)
- [x] M6: MVP complete — README done, full suite green, demo walkthrough below

## Demo walkthrough (Preview-1 ready)

```bash
cd ~/workspace/jev-builds/sentinel && export PYTHONPATH=src
# 1. synthetic storm → tune → evaluate
python3 -m sentinel.synthetic --n 2000 --seed 7 -o /tmp/labels.jsonl
python3 -m sentinel.tuner --labels /tmp/labels.jsonl -o /tmp/thresholds.json
python3 -m sentinel.evalharness --n 2000 --seed 7 -o /tmp/judgment-fidelity-report.md
# 2. live receiver in mock mode (no key needed)
SENTINEL_MOCK=1 SENTINEL_DB=/tmp/demo.db python3 -m sentinel.receiver --port 8080 &
curl -s localhost:8080/v2/enqueue -H 'Content-Type: application/json' -d \
 '{"routing_key":"demo","event_action":"trigger","payload":{"summary":"CPU > 95% for 10m","source":"prometheus","severity":"critical","component":"api-web"}}'
# 3. audit trail
python3 -c "import sqlite3; [print(r) for r in sqlite3.connect('/tmp/demo.db').execute('select alert_id,action,reason from decisions')]"
```

## Integration notes
- Box has no `python`, only `python3` — README uses `python3` throughout.
- `_shims.py` REMOVED 2026-10-02 (dead code: evalharness prefers real `gate.py` via try/except; eval tests re-verified green after removal).
- `SENTINEL_MOCK=1` added to receiver (coordinator): mock client, full pipeline runs, every decision fails open to passthrough, zero network calls.
- Code committed on `lane/code-mvp-v0.1` (a8a7cf4, conventional commit). Push to GitHub blocked on Aditya's 30-sec phone step (repo access for the PAT) — Petu's domain.
- Helper A spec resolutions accepted: Q2/Q3 instruction one-liners; state cap enforced on whole-state JSON tokens; truncate-lowest-priority-first semantics; mock-local canonical-JSON helper (avoids import cycle).
- ARCHITECTURE.md §5 example corrected 2026-10-02 ($20,000 not $20.0 — caught by Helper C).
- Smoke-test forwarder behavior confirmed as designed: PD relay timeout → logged + metric, receiver still 200; missing routing key → loud logged failure, never raise.

## Decisions log

- 2026-10-02: Python stdlib only (no pip deps) — `http.server` for the receiver, `urllib` for the Jev client (custom User-Agent per the documented 403 quirk), `unittest` (stdlib) for tests. Rationale: ₹0 ops, runs anywhere, zero supply-chain surface; FastAPI/gunicorn swap documented as the production upgrade path.
- 2026-10-02: v0.1 is Jev-only (no LLM adjudicator) per the architecture doc — hybrid deferred to a measured premium tier.
- 2026-10-02: SQLite for the v0.1 audit store (single-file, zero-ops); schema designed Postgres-compatible for the SaaS upgrade.

## Blockers / decisions needed

(none — MVP complete. Awaiting GitHub push unblock for PR flow; next build decisions (shadow pilot, real-key Jev validation) are Petu/Aditya calls.)
