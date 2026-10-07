# Decision: wire kill checks into the receiver's paging path (P0-1)
**Date:** 2026-10-07 · **Owner:** FIX CHIEF W1 (single-threaded) · **Type:** Type 2 (reversible internal wiring; no API/schema change)

## Problem
`receiver.py:1255` (`build_pipeline_from_env`) wires the legacy sync `Forwarder`, which has zero kill checks. Kill checks exist only in `DurableForwarder`. The 2.0 ms drill measured the DurableForwarder topology — the production receiver path was never drilled and had no kill wiring. Binding ruling (Petu, founder-deputy): **kill = HALT all paging (fail-closed)** — engaged = paging halted, nothing goes out; re-arm = resume.

## Chesterton's fence (why the receiver uses the legacy forwarder)
Commit `b170c6f` (design 03, #23) built `DurableForwarder` as an **async outbox topology**: it *requires* a file-backed EventLog (raises on `:memory:`), owns a scheduler thread + 4-worker pool lifecycle (`start()`/`stop()`), and has **no synchronous `forward(alert, disposition) -> ForwardResult` API** — the `Pipeline` consumes sync receipts via `note_forward`. The commit message states explicitly: *"Legacy v0.1 Forwarder preserved behavior-for-behavior for the receiver/gate lanes' existing call sites."* The two topologies were intentionally decoupled; the receiver was never cut over.

## Alternatives considered and rejected
1. **Migrate receiver → DurableForwarder.** Rejected: changes the receiver's timing model (sync receipt → enqueue-and-return), forces a file-backed EventLog + thread-lifecycle management into a stdlib `ThreadingHTTPServer` process, and is a far larger blast radius than the property at stake (kill halts sends). Architectural end-state maybe; a Type-1-scale change is not the right answer to a Type-2 kill-wiring gap. This lane fixes the kill wiring, not the outbox gap (that's arch P1-3, separate promotion).
2. **Kill check only at the Pipeline call sites (`_triage`/`handle_generic`).** Rejected: the send boundary is the right choke point (global over local). `Forwarder.forward()` and `forward_raw()` both funnel into `_post` — one check there covers the storm-aggregate path, the normal triage path, AND the fail-open `forward_raw` path, present and future callers included. Mirrors DurableForwarder's defense-in-depth (checks at scheduler head, worker head, `_attempt` first line) adapted to a single-send-method forwarder.

## Chosen
- `Forwarder.__init__` takes `kill_switch=None` (same pattern as `DurableForwarder`'s ctor).
- **The check is the first line of `_post`** — the single send boundary. Engaged → absorb: loud stderr line (precedent: `_simulated_send`), `metrics["killed"] += 1` (honest accounting, not folded into "errors"), `ForwardResult(forwarded=False, error="kill_switch_engaged", killed=True)`. **Never raises** (forward's contract: "Never raises"; `forward_raw` feeds `note_forward`).
- `build_pipeline_from_env` constructs `KillSwitch(log=audit.log)` (EventLog-backed, like the kill-drill harness), passes it to the `Forwarder`, and exposes `pipeline.kill_switch` (same attach pattern as `pipeline.jev_tracker`). The check is a thread-safe flag read — never Jev, never the race (control principle intact; `safety.py` imports unchanged → AST test unaffected).

## Pre-mortem (top 3 failure modes)
1. **A second paging path bypasses the forwarder** → mitigated: audited all `.forward(`/`_post` call sites on the receiver topology (3: storm forward, triage forward, fail-open forward_raw — all funnel through `_post`); `_DryRunForwarder` in health.py is a dry-run, never sends.
2. **Kill engaged but a queued/storm page slips during the flip** → mitigated: the check is a thread-safe property read at send time, not a cached decision; legacy `Forwarder` is synchronous so there is no queue to drain (no in-flight rows exist to requeue, unlike DurableForwarder).
3. **Operator can't engage on the receiver topology** → residual risk (accepted, documented): no engage/re-arm surface exists for the webhook-receiver process (the platform app's HTTP endpoint is serverless-only). The drill flips in-process. Follow-up: admin surface for the receiver topology.

## Verification
- New `tests/test_kill_topology.py`: forwarder-level absorb + `forward_raw` absorb + zero stub hits while engaged; wiring test (full receiver topology over loopback HTTP: engage → webhooks absorbed, re-arm → resumed).
- Re-drill `ops/drills/kill_drill_receiver_topology.py` → artifact `ops/drills/kill-drill-receiver-topology-20261007.json`, topology named in the artifact.
- `python3 -m compileall` clean; server entrypoint imports; `tests/test_safety.py` AST test green.
