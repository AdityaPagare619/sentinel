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

## Rehearsal 3 — PENDING

Reserved for: the integrated stack rehearsal (API + UI lanes merged on
main). Lane 3 re-checks main periodically; when the platform tier serves
the frozen read API from a real projection, this logbook gets the
UI-driven rehearsal entry.
