# W3 Fix Decision — UI kill-copy + banner honesty (2026-10-07)

Owner: FIX CHIEF W3 (single-threaded). Branch: `lane/fix-ui-copy` off main @09236e4.
Scope: `platform/ui-v2/index.html`, `deploy/gh-pages/build-v2.py`, `platform/server/ops_health.py`.
NOT in scope: kill wiring into the receiver path (P0-1, engine lane), Ops Health drawer emptiness (P1-11),
load-test charts, /preview-v2/ removal. No merge to main.

## Rebase onto bf0508f (2026-10-07, parent order)
main moved to bf0508f (lane/fix-deployed-kill merged): honest no-op-lever marking on the
kill button + killed fabricated toasts, PROD_MODE-gated. Rebased locally (no force-push).
Layering, per the parent's order — W3's X-D copy rewrite sits ON TOP of the honest marking:
- ProductionAdapter engageKill/disengageKill: kept the deployed-kill chief's honest echo
  ("flag only, nothing halted") — supersedes W3's earlier "Backend confirmed: kill=engaged".
- renderHeader: PROD_MODE keeps the DISABLED button + honest copy; sim branch carries the
  fail-closed rewrite ("Paging halted"/"Paging live").
- renderDegraded: PROD_MODE keeps "Kill flag engaged (this instance)… nothing was halted";
  sim branch carries "paging halted, nothing goes out".
- renderSafety: PROD_MODE keeps the "NO-OP LEVER (this tier)" srow (disabled button,
  per-instance visibility); sim srow carries the fail-closed contract.
- prodSyncStore: kept both — instanceId/scope (theirs) + key_configured judge wiring (W3).

## Decisions

**D1. Kill semantics: fail-closed (owner ruling carried out, not re-litigated).**
Petu's binding ruling: kill = HALT all paging; engaged = "paging halted, nothing goes out";
re-arm = "resume". All copy rewritten to this. The sim adapter's in-file gate (fail-open:
kill → everything pages) is the last place fail-open lives in this surface — changed to HALT
(`decide(p,'HALT',...)`, racing problems held while engaged, resume on re-arm) so copy and
behavior agree on every surface. Residual: the *production receiver's* legacy Forwarder still
has no kill checks (P0-1, engine lane) — the console is honest about this tier's wiring gap
in the Safety drawer rather than claiming a halt it cannot execute.

**D2. aria-label lie fixed in static bytes (X-H).**
Recommendation (implemented): **static prod chrome in the production HTML** over amending the
matrix. Reasons: (a) first paint on the prod URL must never say SIMULATED or carry a
"Simulated mode" accessible name — not for one frame, not with JS disabled, not before the
token gate; (b) the current design couples the mode label to JS+token success, so a JS/fetch
failure mislabels an armed console as simulated — the dangerous direction; (c) static bytes
are byte-inspectable (curl, auditors, screen readers); the banner contract becomes a real
build assert. build-v2.py now swaps the sim-band div for a prod band div at build time;
prodApplyChrome only fills dynamic, health-attested state.

**D3. Judge-down wired from the health payload.**
`getJudgeDown(){ return false; }` (hardcoded) is deleted. The health payload gains
`jev.key_configured` (presence-only boolean, same pattern as forwarder identity; secret never
read). ProductionAdapter derives `S.judgeDown = !key_configured` in prodSyncStore; the amber
degraded banner and prod band text render from it. When the key is set, live judge state is
still `not_instrumented` on this read tier — the banner says so literally instead of claiming
health. "Every action is real" is deleted from the prod band; forwarder identity (fakepd vs
pagerduty) and judge state are rendered from the health payload.

**D4. "Undoable for 60 seconds" deleted** — no undo implementation exists. Ack confirm and
toast rewritten without the claim.

**D5. CSS + handler guards.** `pillwarn` (amber) / `pillmute` (muted) defined; Shift+E handler
guards the removed evalbar (null-check).

**D6. Production toast echoes the response.** Per the engine ruling, the fabricated
"Suppression paused — backend confirmed" becomes "Backend confirmed: kill=engaged (flag
recorded on this tier)" — echoing `{"engaged": true}`, plus the drawer's wiring caveat.
The kill button stays enabled (hand-test path intact; Aditya already warned to HOLD) but the
Safety drawer states: flag is per-instance only, no paging path on this tier reads it,
wiring P0-1 open — do not test as a safety control.

## Alternatives considered and rejected
- Amend the environment matrix to bless dual-mode bytes (JS flips sim→prod post-token): rejected,
  blesses P0-7 instead of fixing it.
- Disable the prod kill button entirely: rejected — removes the hand-test path and the sim
  surface needs the same control; the wiring caveat in the drawer is the honest middle.
- Keep sim gate fail-open with matching fail-open sim copy: rejected — contradicts the binding
  owner ruling; all surfaces carry one semantics.
