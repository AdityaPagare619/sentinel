# RFC: Countersign — W3 sim-kill fail-closed change (2026-10-07)

Owner: FAANG engine team (countersigning FIX CHIEF W3's change). Type: **Type 2**.
Change under review: commit `7d1f86d` (merged to main via `02a520d`), `platform/ui-v2/index.html` — the console SimAdapter's kill semantics flipped from fail-open to fail-closed.

## What changed
Before: `if(S.kill.engaged) return decide(p,'PAGE','kill-switch','Kill switch engaged — all suppression paused')` — kill → everything pages.
After: `if(S.kill.engaged) return decide(p,'HALT','kill-switch','Kill switch engaged — paging halted, nothing goes out')`; racing problems hold while engaged (`ageWorld` skips resolution); on re-arm, halted problems resume the race (`p.state='racing'`) — nothing was paged while engaged.

## Countersign: APPROVED — correct against the control principle

1. **Semantics now agree with the binding ruling.** Kill = HALT all paging (fail-closed), the only semantic. The old sim adapter was the last place in any surface where kill meant fail-open; it taught operators the opposite of what the engine does. A simulator that trains the wrong mental model of the safety control is worse than no simulator.
2. **Consistent with the engine's absorb-and-resume mechanics.** Engine: forwarder absorbs while engaged (`_kill_absorb`), in-flight rows requeue, re-arm resumes (`_requeue_on_kill`). Sim: decisions HALT while engaged, halted problems resume the race on re-arm. End-observable contract matches on both: engaged → nothing pages; re-arm → work resumes. The sim holds the *race* while the engine holds the *send* — different layers, identical contract (the sim's in-file gate+race is a teaching model, not the engine).
3. **Control principle respected.** The sim adapter never pages real humans (client-side JS, FakePD/sim surfaces only); the change alters no wire behavior. The kill path in the sim adapter is a flag read, no Jev, no race wait — structurally incapable of violating the purity rule.

## Pre-mortem (known imperfections — not revert reasons)
1. **Stale `_resolveAt` latency:** a problem halted past its `_resolveAt` resolves immediately on re-arm with `latency` measured from `createdAt`, including the hold duration — the sim overstates judge latency after a hold. Display-fidelity nit in a teaching sim; the safety contract (nothing paged while engaged) is unaffected. Noted, not fixed here.
2. **No JS test harness:** the Python suite cannot execute the sim adapter; verification is by code review of the merged state (done — the `HALT` branch, the `!S.kill.engaged` race guard, and the re-arm resume loop are all present on main). Residual: a future JS regression has no automated catch. Accepted: the sim adapter is a showcase surface, and the engine truth is covered by `test_kill_topology.py`.

## Risk assessment
Sim-only, client-side, zero real paging: **low risk confirmed.** The dangerous direction would have been keeping fail-open copy/behavior in the surface operators train on.

## Verification performed
- Read the merged state on main (`platform/ui-v2/index.html` @ `ce6a709`): `HALT` branch, race-hold guard, re-arm resume, and `kill switch ENGAGED — paging halted, nothing goes out` log line all present; the old `'PAGE','kill-switch'` fail-open line is gone (grep confirms no `all suppression paused` remains).
- Decision doc `docs/planning/fix-w3-decision.md` D1 records the rejected alternative (keep sim fail-open with matching copy) — correctly rejected as contradicting the binding ruling.

**Signed: engine team, 2026-10-07. The change stands.**
