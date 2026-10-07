# RFC: Engine kill coverage — every paging path kill-wired (2026-10-07)

Owner: FAANG engine team (single-threaded). Branch: `lane/faang-engine-20261007`.
Type: **Type 2** (reversible wiring; no API/data-model change). Binding: Petu's ruling — kill = HALT all paging (fail-closed), the only semantic.

## Problem
The audit's engine standards now apply to the whole pipeline. The fix wave wired kill into the receiver's legacy Forwarder (3031963, re-drilled 9.75ms, artifact `ops/drills/kill-drill-receiver-topology-20261007.json`) — verified holding (10/10 `tests/test_kill_topology.py`). Remaining paging paths were never proven per-path.

## Inventory (every path that can put a page on the wire)

| # | Path | Kill treatment | Proof |
|---|---|---|---|
| 1 | Receiver → legacy `Forwarder.forward/forward_raw/_post` | `_kill_absorb` at all 3 boundaries (fix wave) | existing tests + drill artifact — verified holding |
| 2 | `DurableForwarder._attempt` | check at worker head, raises `KillEngaged` → requeue | existing durable-forwarder kill tests |
| 3 | Degraded ladder `_degraded_send` → `send_direct` | absorb on kill (engine change) | **new test** (was assertion-only) |
| 4 | `DurableForwarder._secondary_scan` / `_terminal` secondary fire | **GAP: no kill check** — secondary (webhook/SMS/email) pages past an engaged kill | **wire + test** |
| 5 | `run_secondary_drill` | **GAP**: operator drill fires secondary while killed | **refuse while killed + test** |
| 6 | `send_direct` (public standby direct-to-PD) | **GAP**: direct callers bypass `_degraded_send`'s check | **kill check inside + test** (defense in depth) |
| 7 | sim_runner `build_sim_pipeline` forwarder | **GAP**: constructed with no `KillSwitch` | **wire + test** (topological fidelity: sim exercises the exact kill-wired path production uses) |
| 8 | Shadow pipeline | read-only by construction (no forwarder) | existing `TestReadOnlyProof` (static + dynamic) |
| 9 | `_int_test_page` (platform tier) | **documented exception** (below) | code review + X-B precedent |
| 10 | Watchdog `_page_human` | injected callable; zero callers in tree | contract documented: implementations MUST route through a kill-wired forwarder |
| 11 | Control-plane pages (`enqueue_control_plane_page`) | via EventLog outbox → worker `_attempt` | transitively wired; **new test** |
| 12 | Fail-open ladder (failopen.py) | pages on timer-win → absorbed at forwarder boundary | funnel property: all automated paging funnels through `_post`/`_attempt` |

**Funnel property (the design invariant):** every *automated* page funnels through exactly one send boundary per topology — legacy `_post`, durable `_attempt` — plus the secondary channel (path 4–6, separately wired). The kill check sits AT the boundary, never upstream in the gate: the gate still triages while killed (drill artifact notes this), the forwarder absorbs. This is why kill needs no Jev and no race wait — a thread-safe flag read at the wire.

**Documented exception — `_int_test_page`:** the single sanctioned bypass, per ruling X-B ("explicit customer BYOK + explicit operator action"). Reasons: (a) it IS an operator safety action (the same class as engaging the switch) — kill halts *automated* paging, not the operator's own deliberate, labeled, single-page verification; (b) blocking it would prevent verifying the re-arm path, which the hand-test procedure requires; (c) it lives on the platform tier whose kill lever is a documented no-op — wiring it to that lever would be theater, not safety. Residual (stolen operator token → test pages) is the audit's arch P3, mitigated by auth + per-call audit + one labeled page per call. Revisit if the platform tier ever hosts a real forwarder.

## Alternatives considered and rejected
- **Kill check in the gate instead of the forwarder:** rejected — the gate emits dispositions, not pages; suppressing at the gate would also halt *suppression accounting* and entangle the kill path with the race/Jev (violates the control principle; the AST purity test would fail the concept).
- **One global kill registry all forwarders poll:** rejected — implicit global state, untestable in isolation; explicit `kill_switch` constructor injection (current design) keeps the dependency visible and the funnel property provable per path.
- **Leaving the secondary unwired ("it's vendor-independent"):** rejected — a future SMS provider pages real humans; the binding ruling says HALT *all* paging, and the degraded ladder was already held to the same standard.

## Pre-mortem ("this failed in production — why?")
1. A new paging path is added without a kill check → mitigated by this RFC's per-path test file (`tests/test_kill_coverage.py`): every path in the inventory has a named test; a new path with no test is a review-time catch, and the funnel property gives reviewers the one question to ask ("where is its send boundary?").
2. Kill engaged but a queued outbox row's secondary fires during the flip window → `_secondary_scan` checks kill at scan head; in-flight secondary sends are not recalled (documented: halt is at *initiation*, matching the primary's requeue-not-recall semantics).
3. Operator confuses "absorbed" with "lost" → every absorb path is loud on stderr AND recorded (`killed` metric, `forward_failed/kill_switch_halt` receipts, audit events).

## Verification
`tests/test_kill_coverage.py` (new): one test per inventory row, failing-before/passing-after for rows 4–7. Existing suites re-run: `test_kill_topology`, `test_durable_forwarder`, `test_safety`, `test_shadow`.

## Rollback
Revert the wiring commits; kill checks are additive flag-reads — removal restores prior behavior with no data migration. The `_int_test_page` exception and shadow proof are unaffected.

## Lineage
Knight Capital: "every system that sends anything needs an off switch" (dev.to/axrisi/knight-capital-how-a-reused-feature-flag-lost-460-million-133j); the 45-minute, ~$440M loss with no kill switch (SEC 2013). Google SRE deadline/inhibitor pattern: the check that logs but doesn't act is a report, not a control.
