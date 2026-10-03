# Sentinel Sunday Demo — Script

**Demo:** Sun 2026-10-04, 21:00 IST · **Audience:** Aditya (design partner)
**Narrative copy:** demo-narrative lane (this file's beat *structure* is normative;
the *wording* of non-flip beats is theirs to refine).
**Flip-beat spec:** normative per the fresh-minds pre-mortem refinement (2026-10-03).
**Rehearsal status:** flip-beat fallback rehearsed 2026-10-03 — see
`rehearsal/flip_beat.py` + `rehearsal/flip-beat-recording-*.json`.

---

## 0. The contract (read aloud or on screen, 30 seconds)

> Everything you see deciding is the real Sentinel engine — the real Gate,
> the real race-to-page, answered by the real Jev System One API. The alert
> *traffic* is synthetic (a demo is not production data); every *decision*
> is computed live. If the model path is down, the demo says so out loud —
> nothing recorded ever pretends to be live.

This is the no-fake law. It is also why the flip beat has a fallback instead
of a prayer (see Beat 1b).

---

## Beat 1 — the flip (Jev is non-deterministic; we audit it, we don't hide it)

**Point:** Jev flips 1.3–2.2% of repeat calls (no seed exists). Sentinel's
answer is audit (`input_sha256` + `jev_model` on every row), never denial.

### 1a — the designed flip (timer wins on the merits)

- First `known_noise` alert through the B=500ms gate (below Jev's measured
  p50 ≈ 816ms) → the timer wins → `passthrough`, reason `timer_won`.
- The late real-Jev answer lands as `shadow_decision` (up to 40s wait).
- On screen: the river row + the late answer's `shadow_disposition` /
  `would_have_suppressed` + full vendor latency ("the tail, sampled").
- If no late answer lands in 40s: say so honestly
  ("the tail won that round too") — the beat does not need the late answer.

### 1b — the honest re-ask: LIVE-FIRST, never live-or-stop ⭐

**This is the refined beat.** Procedure:

1. **Attempt the real Jev re-ask live** — same alert, same state, twice
   through the B=2700ms gate (bounded: 15s per ask; a wedged socket cannot
   stall the demo).
2. **Live works** → show both asks side by side + the verdict:
   - same disposition twice → "the modal outcome (flip floor 1.3–2.2%)"
   - dispositions differ → "a real flip, in the open"
   - Badge: **LIVE**.
3. **Live fails** (529 / 402 / network / timeout) → **the demo does NOT
   collapse.** Two honest options, in this order:
   - **(a) Recorded fallback** (preferred when a rehearsal recording exists):
     show the recorded real-Jev re-ask with the EXACT label, verbatim:

     > recorded real-Jev re-ask from rehearsal \<timestamp\> — live path down right now

     The label always carries the rehearsal timestamp. No timestamp, no
     showing. The presenter adds one line: "The live re-ask failed just now
     — so you're seeing a real re-ask from rehearsal, labeled as such."
   - **(b) Limits beat** (no recording available): show the failure itself
     as a limits beat — "Jev is unreachable right now (\<failure class\>) —
     and that's the backup plan working: when the model path is down,
     Sentinel pages on uncertainty instead of going blind. The paging path
     never depended on this call."

**Hard rules (no exceptions):**
- No network failure may kill the demo. `render_beat()` never raises.
- No unlabeled recordings, ever. A recorded render without the verbatim
  label is a rehearsal failure, not a demo option.
- A recorded beat never wears the LIVE badge.

**Rehearsal (done 2026-10-03, `rehearsal/flip_beat.py`):**
- `record`: 2 real Jev calls → `flip-beat-recording-20261003T144811…json`
  (rehearsed_at `2026-10-03T14:48:11Z`; both asks `page_business_hours`,
  conf 0.64/0.63 — the modal outcome, no flip).
- `rehearse-fallback`: dead live endpoint → recorded mode with the verbatim
  label; no recording → limits beat naming the failure; corrupt recording
  path → limits beat, no crash; unlabeled-recording scan clean.
- **Re-run `record` on demo day** (a fresh timestamp keeps the label honest;
  keep the 2026-10-03 file as the backup).

---

## Beat 2 — the storm (the engine at work)

Synthetic storm (40 alerts, seed 42) through the real Gate + race-to-page +
real Jev. River rows live: PAGE / SUPPRESS / QUEUE / PASS-THRU with reason
codes and budget outcomes. (`demo/storm-scenario/storm_runner.py`;
`--n 40 --seed 42`.)

*Narrative lane: the storm's story arc — what the audience should feel at
alert 10 vs alert 40.*

## Beat 3 — the river (Freedom 2: see why this paged you)

`/river`: the decision tape, filter rail, detail drawer (verdict → evidence
→ timeline → provenance → raw). Click a SUPPRESS row → the reason code and
the confidence bar. Click a PAGE row → the timeline.

*Narrative lane: the two rows to click and the one-line plain-language
verdict for each.*

## Beat 4 — Shadow Report + calibration (the trust layer)

Flip-audit view (`/api/analytics/flips`), calibration panel, the 1.3–2.2%
floor disclosed on screen (Law 3). The simulator: drag a threshold, watch
last week's noise re-price.

*Narrative lane: which threshold to drag and the sentence that lands it.*

## Beat 5 — receipts (every number traces to the log)

Fold the append-only event log: decision counts, budget outcomes, latency
p50, suppression count with reasons, `detect_flips()` output, head hash.
`storm-report.json` is the leave-behind.

---

## Failure choreography (the demo's immune system)

| Failure | Response | Shown as |
|---|---|---|
| Jev 529/402/network/timeout during flip beat | recorded fallback (labeled) or limits beat | RECORDED / LIMITS badge |
| No `custom.typesafe` credential at startup | loud stop, no mock (`storm_runner.py` exits 2) | banner (never a silent mock) |
| SSE drop during river | gap marker + `◌ reconnecting`, 30s polling fallback | visible, never silent |
| Any beat raises | the beat degrades to its honest fallback; the demo continues | the failure, labeled |

Nothing in this table is a surprise on demo night — every row is rehearsed.

---

## Rehearsal log

| Date | What | Result |
|---|---|---|
| 2026-10-03 | Flip-beat fallback (`rehearsal/flip_beat.py rehearse-fallback`) | PASS — 4/4 checks (recorded label verbatim, limits beat, corrupt-path, unlabeled scan) |
| 2026-10-03 | Flip-beat live recording (`record`) | OK — 2 real Jev calls, modal outcome, artifact saved |
| — | Full dry run (all 5 beats, timed) | narrative lane |
| Sun AM | Fresh `record` (fresh timestamp for the label) | TODO |
