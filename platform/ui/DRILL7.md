# DRILL 7 — "kill the Jev path 5 minutes before showtime"
## Prism (UI) lane evidence

**Drill intent (fresh-minds pre-mortem):** with the model client dead/unreachable,
the demo's critical path continues without a skipped beat.

**UI lane scope:** the UI never calls Jev (Law L5 — zero Jev on read paths), so
river / calibration / simulator / audit / drawer / receipts are structurally
immune: they read the event log via the frozen read API. The only surface that
touches the Jev path is the START flow's key-verification step. The drill hook
is deterministic: `#/start?jev=dead`.

### What was built (folded into the lane, not a restart)

`assets/lib.js` — `startPlan(jevState)` returns the step plan for `live` /
`dead` / `unknown`. The `dead` plan is the DESIGNED recorded-walkthrough
fallback (narrative §6.3), with honest copy at every step:

| Step | Degraded behavior |
|---|---|
| key | "Key verification needs the Jev path, which is down. Your key is NOT stored and NOT verified — the tour continues on the recording." |
| storm | RECORDED STORM card: "The suppressions are real, they just are not yours. Suppression counts here prove the pipeline works, not that the model is good." |
| flip beat | `◌ RECORDED FLIP — the live flip feed is down.` + the flip timeline from the recording |
| why / tune / source | Unaffected — the tour continues; nothing is auto-applied, ever |

A persistent amber banner names the state for the whole flow:
"Jev path unreachable — this is a recorded walkthrough of a real storm from
2026-10-01… Nothing here pages anyone."

### Verification

- `node --test tests/lib.test.mjs` — 12/12 pass, including 3 DRILL-7 tests:
  `jevStateFromParams` maps `?jev=dead|live`; the degraded plan contains the
  recorded-walkthrough copy and the NOT-stored/NOT-verified refusal; the live
  plan contains no degraded copy.
- Manual rehearsal path: open `#/start?jev=dead&mock=1`, walk key → storm →
  flip beat; confirm every degraded label renders and the tour completes.
  Then open `#/river?mock=1` and confirm the tape, drawer, and receipts are
  byte-identical to the live-path rendering.

### PASS/FAIL bar

- PASS: every Jev-touching step degrades to a labeled state; no step claims
  liveness it doesn't have; the tour completes; river/drawer/receipts render
  unchanged.
- FAIL: any copy implying a live Jev call succeeded while `jev=dead`, or any
  screen that blanks/errors instead of degrading.

**Threat to Sun 21:00 IST:** none from this lane — the hook is implemented,
tested, and rehearsal-ready. The live Jev-path health signal (real
reachability, not the `?jev=dead` override) is engine-lane owned.
