# Sentinel Sunday Demo — REHEARSAL_LOGBOOK.md

**Lane 3.** Every rehearsal is logged here — honestly, including warts and
anything that smelled fake (killed on sight). No entry is edited after the
fact; corrections are new entries.

---

## Rehearsal 1 — dry run, engine-level — 2026-10-03 ~20:15 IST

**Command:** `storm_runner.py --n 6 --seed 42 --db /tmp/dry.db --out /tmp/dry-out`
**Stack:** real `Gate` + race-to-page, real Jev `jev-1.13.0` via
`custom.typesafe` surrogate auth (no raw key in process). Platform UI not
yet merged — terminal river + event log.

**What ran:** 8 real Jev calls (1 designed flip at B=500ms + 2 re-asks at
B=2700ms + 5 storm alerts at B=2700ms), ~2 s spacing, all HTTP 200.

**What the flip actually did:**
- seq 1: timer won at 500.26 ms → `passthrough` (the page went out; the
  budget held). `q1/q3/latency` null on the row, per schema.
- seq 2: the late REAL Jev answer landed 760 ms in as `shadow_decision`:
  `q1_reported=0.0`, `q3_confidence=0.67`,
  `q3_disposition=page_business_hours` → `shadow_disposition=passthrough`,
  `would_have_suppressed=false`. The machine's late answer: not
  suppressible. Recorded, never acting.
- `detect_flips()` on the log: 1 real non-determinism observation
  (q3 0.65 → 0.66 across the re-ask pair; same disposition).
- Re-ask pair (B=2700): both `passthrough`, conf 0.65/0.66 — same
  disposition twice, the modal outcome. Presented as such.

**Decisions:** `passthrough: 6, page_now: 2`. **Suppressions: 0.**
Budget outcomes: `timer_won: 1, answered_in_time: 7`.
Latencies (real): min 386 ms, p50 639 ms, max 783 ms — inside the Oracle
N=100 distribution, as expected.

**The zero-suppression finding (IMPORTANT, honest):** real Jev reported
`q1=0.01` on the smoke noise alert and `q1=0.0` with conf 0.67 on the
flip alert — the triple lock (p1 exactly 0.00 AND conf ≥ 0.90 AND
dual-attested allowlist) held on every alert. The engine suppressed
nothing, and the demo says so proudly (beat 2 honesty copy). The lock
holding IS the story; a manufactured suppression would be the fake.

**Warts:**
- The runner initially hardcoded `budget_outcome="answered_in_time"` in
  the storm print loop — caught and killed before the dry run: the row
  is now read off the emitted `decision_made` envelope. No assumed
  outcomes anywhere.
- `_post_once` had a garbage `raise` line from drafting — killed in
  review; `JevError` imported properly.
- `log.read_events()` did not exist on `EventLog` — replaced with the
  real read API (`events_of_type`, `get_event`, `head`).
- Unused `build_questions` import left in the runner — removed.

**Smelled fake?** Nothing in the demo path. The allowlist attestations
are labeled `demo-*` and documented as demo attestations in the runner —
they are the mechanism (dual attestation), not production evidence, and
the script says so.

**Verdict:** the machinery is REAL end to end. Proceeding to the full
40-alert storm.

---

## Rehearsal 2 — full storm — 2026-10-03 ~20:21–20:31 IST

**Command:** `storm_runner.py --n 40 --seed 42 --db demo/storm-scenario/storm.db --out demo/storm-scenario`
**Stack:** real `Gate` + race-to-page, real Jev `jev-1.13.0` via
`custom.typesafe` surrogate auth. Platform UI not yet merged — terminal
river + event log + storm-report.json.

**What ran:** 42 real Jev calls (1 designed flip at B=500ms + 2 re-asks at
B=2700ms + 39 storm alerts at B=2700ms), ~2 s spacing. All HTTP 200, zero
errors, zero retries exhausted.

**The log:** 43 events, hash-chained, head
`7b23a0d4451ff73ce3f11fa169631311f65af92e1650322b3456193920e3b106`
(recorded in `demo/storm-scenario/storm-report.json`).

**Results (all from the event log, nothing assumed):**
- decisions: `passthrough: 30, page_now: 12`
- budget outcomes: `timer_won: 1, answered_in_time: 41`
- real Jev latencies (n=41): min 316 ms, p50 587 ms, max 924 ms —
  inside the Oracle N=100 distribution
- **suppressions: 0** — the triple lock held on every alert, again.
  Beat 2's honesty copy stands: the lock holding is the story.
- flips: 1 total — a q3 confidence wobble (0.67 → 0.64) across the
  re-ask pair; **0 disposition flips**. The re-ask presented both
  dispositions honestly: identical twice, the modal outcome.

**The designed flip, full-storm edition:** timer won at 500.14 ms →
passthrough; the late real Jev answer (844 ms, q1=0.00, conf=0.61,
disposition=page_business_hours) landed as `shadow_decision` with
`would_have_suppressed=false`. The machine's late answer: not
suppressible. Both positions in the log, citable.

**Warts:**
- The `disposition_flips` / `confidence_wobbles` summary keys were added
  to the runner mid-run (module already loaded), so this report carries
  only `flips_detected` — computed by hand above. Cosmetic; the data is
  complete. Next run picks up the keys.
- The full-storm `event-log.jsonl`/`storm.db` are real artifacts of THIS
  run (new chain, new head hash) — they supersede the dry-run artifacts,
  not supplement them. The dry-run artifacts live in /tmp, not in demo/.

**Smelled fake?** Nothing. The zero-suppression outcome is the engine's
real answer, presented as such.

**Verdict:** the demo is REAL end to end and rehearsed twice. Ready for
the Sun 21:00 IST delivery, pending the UI lanes for the screen-driven
beats (terminal river is the rehearsed fallback).

---

## Rehearsal 3 — flip-beat fallback + no-key stop — 2026-10-03 ~20:45 IST

**What ran:**
1. `rehearsal/flip_beat.py rehearse-fallback --recording rehearsal/flip-beat-recording-20261003T144811.672709p0000.json`
   (sibling lane's tooling, executed by lane 3 in this worktree).
2. Monkeypatched `check_auth()` in `demo/storm-scenario/storm_runner.py`
   with a dead credential to exercise the no-key stop path.

**Results — fallback rehearsal 4/4 PASS:**
- dead live + recording → `recorded` mode, verbatim label:
  "recorded real-Jev re-ask from rehearsal
  2026-10-03T14:48:11.672709+00:00 — live path down right now"
- dead live, no recording → `limits_beat` mode, failure named
  (JevError/network), no crash
- corrupt recording path → limits beat, no crash
- unlabeled-recording scan: clean

**Results — no-key stop:** banner printed verbatim on screen
("The demo refuses to run on a mock engine…"), `SystemExit(2)`,
no mock engaged, nothing decided, nothing recorded. PASS.

**Honesty notes:**
- The rehearsal recording was inspected: real Jev answers, timestamped,
  source-labeled (`real Jev System One (custom.typesafe surrogate
  auth)`). A labeled recording of the real engine is not a mock —
  but it NEVER wears the LIVE badge, and without the verbatim
  timestamped label it is not shown at all.
- `render_beat()` never raises (verified across all three failure
  shapes). No network failure can kill the demo.
- DEMO_SCRIPT.md beat 1b updated to the live-first procedure with the
  exact fallback label and the hard rules.

**Verdict:** the flip beat is live-first and failure-proof. The demo
survives Jev being down, the credential being gone, and the recording
being corrupt — each with its honest on-screen state.

---

## Rehearsal 4 — integrated-stack rehearsal (shim + merged UI) — 2026-10-03 ~21:05 IST

**Context:** the UI lanes merged on main (PR #31 Prism foundation, PR #33
calibration + simulator views) but NO API server lane has merged — the
stack is not integrated. Per the wave brief ("re-check main periodically,
then rehearse"), lane 3 built the rehearsal rig instead of waiting.

**What ran:**
1. `demo/storm-scenario/storm_runner.py` re-run (42 real Jev calls) with
   answer persistence (`answers.jsonl`: the engine's own DecisionRecords —
   full Jev answers, evidence preserved, never fabricated).
2. `demo/rehearsal-shim.py` (new): serves the frozen read API v1.0.0 from
   the REAL storm artifacts. Every envelope carries the rig as its
   `data_source` — the UI's source badge shows it; nothing pretends to be
   the production platform tier.

**Verified live (curl, all 200 except noted):**
- `GET /api/decisions?limit=50` — 42 rows, newest first; every row carries
  ALL required contract fields (0 missing-field violations). The single
  timer-win row (seq 1) carries honest nulls (severity/team/confidence/
  model/latency) — no Jev answer exists on that path; the contract marks
  model/latency nullable (severity/team/confidence nullability flagged
  to the Forge lane as a contract note).
- `GET /api/decision/1` — the flip beat: passthrough, reason `timer_won`,
  budget `timer_won`, nulls honest. `GET /api/decision/3` — the re-ask:
  real answers (severity `known_noise`, conf 0.62, `jev-1.13.0`), 1 flip
  on the input hash.
- `GET /api/analytics/flips` — 1 record (q3 0.62→0.63 wobble, 0
  disposition flips). `GET /api/analytics/noise` — real breakdown
  (passthrough 28, page_now 14; reasons timer_won 1, uncertain 27,
  threshold 14).
- `GET /api/calibration` — provisional, `n_labeled: 0`, with the honest
  note (no outcome labels in a synthetic storm). The denominator rides
  on the card, per the contract.
- `POST /api/simulate` — **422 honest refusal** (`simulator_not_wired`):
  this rig does not implement the tuner path, and the simulator must run
  the live kernel (UI lane's build — copy-audit Finding 3). A wrong curve
  is worse than no curve.
- `GET /api/stream` — SSE replay of all 42 real decisions with
  `id:`/`event: decision` framing per the contract, then the heartbeat.

**Warts (all caught, all fixed, all logged):**
- The first answers.jsonl keyed answers by alert_id — the flip alert's
  three decisions collapsed onto the last re-ask's answers (seq 1 showed
  reason `uncertain` instead of `timer_won`). Killed: answers are now
  keyed by decision seq; existing artifacts migrated with per-row
  alert_id verification against the log.
- Port 8080 was held by the drills lane's receiver — the shim moved to
  18081 without touching their process.
- A `pkill -f` matched my own shell (footgun); narrowed the pattern.

**Smelled fake?** Nothing. The one thing the rig cannot do honestly is
also the thing it refuses to do (simulate).

**Limitations (stated plainly):** no live-browser verification of the
rendered screens — subagents cannot operate the live browser. The data
layer contract conformance is verified; the visual pass needs a
browser-capable agent (parent to arrange). The production API server
itself is still the API lane's build — this shim is the rehearsal
stand-in, labeled as such in every envelope.

**Verdict:** the demo's data path is rehearsed end to end — real engine
→ real event log → frozen contract → (UI when a browser looks at it).
The Sun 21:00 checkpoint shows this REAL state, warts included.

## Rehearsal 5 — INTEGRATED STACK, live flip beat (2026-10-03 ~20:50 IST)

**The gating dependency resolved:** platform API merged (PR #44, commit
f69e116). Server: `python3 platform/server/__main__.py --port 18082 --db
demo/storm-scenario/storm.db --context-json demo/storm-scenario/alert-context.jsonl`.
The stack is now engine → event log → platform API → Prism UI, all real.

**Platform-fidelity work done before the rehearsal (honest, documented):**
1. My runner's emit sink was writing the gate's lean payloads (no
   v01_compat) while the gate's internal audit.record went to :memory:.
   The platform projects reason/Q1 from v01_compat — so the UI showed
   `cannot_determine` severity on EVERY row and `unknown` reasons.
   Fixed: runner now enriches decision_made bodies with v01_compat
   derived from the REAL DecisionRecord via sentinel.audit's own
   helpers (same derivation the engine's canonical audit path uses).
   The gate's TRUE budget_outcome (timer_won) and REAL lock_evaluation
   are kept — the audit path mislabels timer_won as structural_passthrough
   (verified) and writes only a lock note.
2. One-time verified migration of the existing 43 events
   (`migrate_v01compat.py`): 42 enriched, chain recomputed, backup kept
   as storm.db.pre-v01compat. Asserted: seq↔alert_id match, 43/43 events
   migrated.
3. Outcomes: loaded 40 synthetic reference labels into the outcomes
   table (`load_outcomes.py`) — the platform's own labels-v3 precedent
   ("seeded synthetic labels; data_source=synthetic, loudly"). labeled_at
   is the rehearsal timestamp; never presented as real incidents.

**Live flip beat** (`demo/flip_beat_live.py`, real Jev, real gate):
- 1a: B=500ms → timer won → passthrough, seq 44. No late answer within
  40s (noted honestly on screen) — the shadow_decision landed just after
  the window as seq 45: real Jev, 849ms, q1=0.0, conf=0.61,
  shadow_disposition=passthrough, would_have_suppressed=false.
- 1b: two live re-asks at B=2700ms → seqs 46, 47, both passthrough
  (uncertain), conf 0.66/0.61, latencies 651/501ms — modal outcome,
  [LIVE] badges, no fallback needed.
- Flip-audit via the API: the alert now has 6 repeats (seqs 1,3,4,44,46,47),
  `flipped: true`, full decision list including both timer-win rows.

**Verified through the REAL platform API (all against storm.db):**
- /api/decisions — 45 decisions, real reasons (uncertain/threshold/timer_won),
  real severities (known_noise), real teams, titles joined from context.
- /api/decision/1 and /44 — flip rows read `reason: timer_won`.
- /api/decision/5 — REAL Q1 prob_map (known_noise 0.57 / p3_medium 0.28 /
  p2_high 0.15), not the uniform fabrication.
- /api/analytics/flips — 6-repeat audit with flipped=true.
- /api/analytics/noise — real breakdown (uncertain 64.4%, threshold 31.1%,
  timer_won 4.4%), 25 top checks.
- /api/calibration — n_decisions=45, n_labeled=45, ece=0.05, bin n=43 with
  Wilson CI [0.0, 0.082], data_source=synthetic. Denominators on every card.
- /api/stream (SSE) — replays the log live.
- / serves the Prism UI (200).

**Warts, logged honestly:**
- W1 (OPEN, platform lane): timer-win rows still get fabricated UNIFORM
  prob_maps (0.2/0.2/…), unlabeled in the response. jev_model=null and
  latency_ms=null are the honest signals; the uniform bars are not. The
  demoist does NOT open the prob drawer on the timer-win row, or names
  the reconstruction if asked. Recommended fix: when jev_model is null,
  the store should return prob_map null (or a marked reconstruction).
- W2 (OPEN, contract): budget_outcome has no first-class contract field;
  the flip story rides on the reason chip (now truthful: timer_won).
- W3 (resolved): the 40s late-answer window missed the shadow by seconds;
  the event still landed (seq 45) and is visible — the beat's honesty copy
  already covers this ("the machine's late answer is recorded, never acting").
- W4 (simulator): platform simulate.py == tuner.classify (pre-ADR-013
  continuous gate), not the live kernel — Finding 3 stands, UI lane's call.

**What smelled fake and was killed:** the lean emit bodies (severity
cannot_determine everywhere, unknown reasons) — killed via the v01_compat
enrichment. The uniform prob_maps on answered rows — killed (now real).
No mocks, no recordings, no key in the repo. The one recording on disk
(flip-beat-recording-20261003T144811) remains the LABELED fallback only.
