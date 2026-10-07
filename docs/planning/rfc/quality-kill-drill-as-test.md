# RFC — Promote the receiver-topology kill drill to a runnable test

**Date:** 2026-10-07 · **Lane:** `lane/faang-quality-20261007` · **Author:** TEAM 6 (quality/test)
**Decision type:** Type 2 (test-only change; the drill script is untouched)
**Status:** ACCEPTED — implement.

## 1. Problem

Audit X-A: the "2.0ms drill headline" was true of the *DurableForwarder*
topology while the production receiver pages through the legacy sync
`Forwarder`. The property was proven of a forwarder the pipeline doesn't
use. The fix-wave re-drilled on the true receiver topology
(`ops/drills/kill-drill-receiver-topology-20261007.json`, 9.75ms PASS) and
`tests/test_kill_topology.py` already proves kill→halt→re-arm→recover
in-process on that topology.

What is still missing — the wrong-topology mistake can recur *silently*:

1. No test pins the topology itself: if `build_pipeline_from_env` ever
   switches the receiver's forwarder to `DurableForwarder` (or any new
   class), the kill tests still pass (they construct `Forwarder` directly
   or test whatever the factory returns) while the drill numbers become
   meaningless.
2. The committed drill *artifact* is prose-adjacent JSON: its `topology`
   string is hand-written in the script. Nothing ties the artifact's claim
   to the code that produced it.

## 2. Decision

Two test-only additions, no drill-script changes:

**(a) Topology pin.** In `TestReceiverTopologyKillWiring`, assert the
factory-built pipeline's forwarder is *exactly* `sentinel.forwarder.Forwarder`
(the legacy sync class carrying the C3 kill checks in `_post`) — `assertIs(type(f), Forwarder)`,
not `isinstance` (a subclass could drop the kill wiring). Same pin on the
manually-built topology. If the factory ever changes the paging forwarder,
this test fails loudly and the drill numbers are invalidated by name.

**(b) Artifact schema contract.** New test loads the committed artifact
`ops/drills/kill-drill-receiver-topology-20261007.json` and asserts:
`passed is True`, `threshold_ms` present, `measured_ms <= threshold_ms`,
and — critically — `artifact["topology"]` equals the `TOPOLOGY` constant
imported from the drill script (not a string duplicated in the test). The
topology claim is therefore code-derived: the artifact and the script share
one source of truth.

## 3. Alternatives considered and rejected

- **Re-run the drill script inside the test and compare.** Rejected —
  the drill script does real wall-clock HTTP + timing; in-process tests
  already cover the property faster and deterministically. The artifact
  test validates the *record*, not the *run*.
- **Delete the script now that tests exist.** Rejected — the script is the
  operator-runnable drill (Aditya's hand-test lineage); tests and drills
  serve different readers. Chesterton's fence.
- **`isinstance` instead of exact-class.** Rejected — see §2(a). The
  property under test is "this exact class with its `_post` kill check",
  not "something forwarder-shaped".

## 4. Pre-mortem (it is Oct 2027; the topology pin betrayed us)

1. *The factory legitimately moved to DurableForwarder and the pin blocked
   it.* That is the pin *working*: the drill numbers were measured on the
   legacy topology. The mover updates the pin, re-drills, and the artifact
   carries the new topology string — the failure is loud and the
   re-measurement is forced, which is the entire point.
2. *The artifact test rots when the next drill overwrites the JSON.* The
   test reads the `kill-drill-receiver-topology-*.json` by glob and takes
   the latest; a new drill with a new topology string must update the
   script's TOPOLOGY constant, which the test imports — still one source
   of truth.
3. *The pin passes but the kill wiring moved out of `_post`.* Defense in
   depth, not this RFC: `test_kill_topology.py` already asserts the
   *behavior* (zero sends under kill). The pin asserts the *identity*.
   Both must hold.

## 5. Failure-proof

Swap the factory's forwarder to `DurableForwarder` in a scratch worktree →
pin test red, naming the class mismatch. (The pre-fix state — no pin at
all — is the "green on wrong topology" control.)

## 6. Lineage

CERN blind-analysis discipline — the measurement's *conditions* are part of
the result; a number without its topology is not a result, it's a rumor.
The 2015 diphoton excess lesson: ~3.9σ local meant nothing without the
global (look-elsewhere) context; our drill number means nothing without
its topology context, and the test suite is where that context now lives.
