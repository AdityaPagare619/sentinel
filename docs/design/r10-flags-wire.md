# R-10 wire-or-retract — design doc

**Lane:** dev-1 (devops/environments) · **Branch:** `lane/12h-dev-1-r10`
**Date:** 2026-10-05 IST · **Owner:** dev-1 (single-threaded)
**Type:** 2 — reversible. The flags *schema* is Type 1 (settled by
`ops/devops-foundation.md` §2.1, unchanged by this lane); this lane wires
the schema into the live gate. The wiring is fully retractable via the
pre-written retract ADR (`docs/decisions/2026-10-05-r10-retract-DRAFT.md`),
which is F1's binding kill path.
**Reviewers:** Relay (devops, OPERATING-RULES §1.6) + **Vault — sign-off
REQUIRED** (12H-PLAN §1; the wiring touches the decision path).

## Problem

`flags.json` is fictional: `flagctl.py` writes it, `env-bootstrap.sh`
creates it, `ops/devops-foundation.md` §2.1 specifies its contract — and
zero readers exist in `src/`. The revision program's R-10 finding, verbatim:
"the `flags.json` kill-switch is currently fictional (zero readers in
src/)". The operator's instant flag-off (`global_kill_switch`) is a
documented wish, not a code path. The devops pre-mortem names the failure
exactly: *"The kill switch rusted shut. Nobody drilled
`global_kill_switch` for six months; at 3 AM the operator discovers
flagctl writes a generation the old receiver build can't parse."*

## Goal

Wire `flags.json` into the live gate per `ops/devops-foundation.md` §2.1,
with the F1 kill condition: **the kill-switch drill must PASS on
staging-lab by T+9, or the retract ADR lands at T+9** — no third option.

## Design

### 1. Loader: flags join the 4-stage load (config.py)

`ConfigLoader._policy_paths()` gains `flags.json` as a third required
file. The same 4 stages (parse → schema → semantic → atomic swap) apply:

- **Schema** mirrors `flagctl.py`'s `FLAG_DEFS` exactly (the contract —
  flagctl is the sanctioned writer, the loader is the sanctioned reader;
  they must agree or the flip procedure lies): `version == 1`, `flags`
  object, all five flags present, no unknown flags, bool/list types,
  `{"value": …}` shape.
- **Semantic:** a non-empty `canary_severity_bands` or `canary_services`
  is **rejected** (`ConfigRejected`, live generation untouched). Reason:
  no dual-policy ("candidate policy") mechanism exists anywhere in the
  gate; arming a canary the kernel cannot honor would repeat the R-1
  finding (a governed object the kernel never reads). The rejection names
  the follow-up (R-15 dual-policy canary, Phase 4). This is fail-closed,
  and it is the only behavior that doesn't lie.
- `PolicyConfig` gains `flags: dict[str, Any]`; `to_dict()` persists it;
  `_newest_last_good()` restores it (old generations without flags get
  defaults). `source_sha256` covers the flags payload; `mtimes` cover the
  flags file — so `config_files_changed()` (the `config_current`
  predicate) trips on an un-reloaded flags edit automatically.
- **Missing `flags.json` ⇒ `ConfigMissing` ⇒ startup refusal**
  (fail-closed, like `thresholds.json`). A kill switch that silently
  doesn't exist is the rusted-shut failure mode; the config module's
  standing rule is "never supervise paging with an unvalidated (or
  defaulted) policy". `env-bootstrap.sh` creates the file in every tier.

### 2. Gate: three behavioral flags on the hot path (gate.py)

`Gate.__init__` gains keyword-only `flags: dict | None = None`
(defaults = current behavior: kill off, suppress on, shadow off,
canary empty — the previous behavior is the safe default).

- **`global_kill_switch=true` → S0 branch, first in `_decide`** (before
  S1, before the D6 firewall, before the fail-open ladder, before the S2
  race): returns `passthrough`, reason `"kill_switch"`, no Jev call, no
  race armed. Emits `decision_made` with
  `budget_outcome=structural_passthrough`; the audit row is still written
  (the audit invariant is unconditional). `digest_storm` is NOT covered:
  it cannot suppress by construction, and the kill switch defeats
  *suppression* — documented, not silent.
- **`suppress_enabled=false` → every suppress becomes `passthrough`**,
  reason `"suppress_disabled"` (never queued — the doc's exact words):
  in `_on_answered`'s `if action == "suppress":` block (mirroring the D8
  `policy_blocked` downgrade pattern, checked first), AND in
  `_structural`'s duplicate branch (a prior suppress inherited via dedup
  is still a suppress disposition the contract covers).
- **`shadow_mode=true`** → `Gate(shadow=True)` semantics: `evaluate()`
  uses `effective_shadow = self.shadow or flags["shadow_mode"]`; the
  would-be disposition is audited, `passthrough` is returned.

The frozen §4 policy kernel (`evaluate_policy`) is untouched — the flags
are a wrapper-level release axis, exactly like the D8 enforcement hook.

### 3. Receiver + health (receiver.py, health.py, Pipeline.apply_policy)

- `build_pipeline_from_env` passes `flags=policy.flags` to the live
  `Gate`. (The Stage-0 shadow-tap gate keeps `shadow=True`; flags are not
  threaded there — the tap never pages.)
- `Pipeline.apply_policy` sets `self.gate.flags = dict(policy.flags)`
  alongside the thresholds/allowlist swap — the flip is atomic with the
  generation, so a confirmed generation bump means the new flags are
  live (this closes the "rusted shut" pre-mortem mode 3).
- `/healthz` deep-check body gains a `flags` summary (all five effective
  values) so the drill's confirmation step is checkable, not believed.

### 4. Contract artifacts

- `ops/flags.schema.json` — the machine-checkable JSON Schema of the
  `flags.json` contract (for qa-2's `tests/contracts/` skeleton to plug
  in at T+9).
- The bootstrap's example `flags.json` is the canonical fixture.

### 5. Tests

- Loader: valid load; missing file refuses startup; unknown flag
  rejected; wrong type rejected; version mismatch rejected; invalid
  flags ⇒ `config_rejected`, live generation untouched (reload-rejection
  drill, unit form); non-empty canary list rejected with the R-15
  pointer; flags round-trip through the last-good chain.
- Gate: kill switch ⇒ passthrough, reason `kill_switch`, no Jev call
  (client call counter stays zero), audit row still written; suppress
  leg ⇒ passthrough `suppress_disabled` when disabled (kernel + dedup
  paths); `shadow_mode` flag ⇒ shadow semantics; both flag states tested
  (flag-review checklist); `digest_storm` unaffected.
- Receiver: `/-/reload` swaps flags; `/healthz` shows them.
- Existing `ConfigLoader` test helpers updated to write `flags.json`
  (the honest migration — the file is now required).

## Alternatives considered and rejected

- **Wire only `global_kill_switch`.** Rejected: leaves `suppress_enabled`
  / `shadow_mode` as the same fiction R-10 calls out. The wiring is
  mechanical for all three; partial wiring is how fictions survive.
- **Dual-policy canary now.** Rejected: no candidate-policy mechanism
  exists; threading a second policy through the race, corroboration leg,
  and fail-open ladder is a Type-1-scale change on the wave's
  second-hardest chain with a dated T+9 kill condition. Fail-closed
  rejection of non-empty canary lists is the honest interim; R-15 owns
  the real build (Phase 4).
- **Canary flags validate-and-ignore (with a `canary_cohort` marker).**
  Rejected: repeats the R-1 finding — a governed object the kernel never
  reads. A marker without behavior is theater with better logging.
- **`flags.json` optional (missing ⇒ defaults).** Rejected: a missing
  flags file silently disarms the operator's kill switch — the exact
  rusted-shut failure. Fail-closed, like `thresholds.json`.
- **Enforce `suppress_enabled` only in the kernel path (not dedup).**
  Rejected: the contract says "suppress dispositions become
  passthrough"; a dedup-inherited suppress is a suppress disposition.
  The drill would pass while a replay path still suppressed.

## Pre-mortem (principal-systems §2)

Assumed: it is T+12 and the R-10 wire failed catastrophically.

1. **The drill failed because a suppress path bypassed the flag checks.**
   The S0 branch was placed after S1/the firewall, or a new suppress
   emitter was added that the wrapper doesn't cover. *Mitigation:* S0 is
   the FIRST branch in `_decide`; suppress checks cover kernel + dedup;
   the drill asserts behavior (disposition + reason + no Jev call), not
   just the generation bump.
2. **The flip confirmed but the behavior didn't change.** `apply_policy`
   swapped thresholds/allowlist but not flags — generation bumped,
   `/healthz` green, kill switch dead. The rusted-shut failure with a
   fresh coat of paint. *Mitigation:* `apply_policy` sets `gate.flags`
   atomically; the drill's pass criterion includes the alert round-trip
   AND the `/healthz` flags summary; a unit test asserts
   `apply_policy` propagates flags.
3. **The full suite broke** (496 tests): required `flags.json` broke
   `ConfigLoader` test helpers; the `Gate` signature change broke
   evalharness/ab call sites. *Mitigation:* run the FULL local gate
   (`pre-pr-gate.sh`) before any PR; the `flags` kwarg defaults to
   current behavior so non-loader call sites are unaffected.
4. **The drill passed on the local lab but the lab wasn't the real
   staging-lab.** Relay provisions a shared lab at T+8 with a different
   bootstrap; my local lab certified a fiction. *Mitigation:* the local
   lab is built ONLY with the sanctioned `env-bootstrap.sh
   --tier staging-lab` (no hand-rolled config); the drill record names
   the lab's provenance; re-run on the shared lab if one appears.

## Binding skill clauses

- **principal-systems, software constitution** ("reliability is policy,
  not hope"): the kill switch must be a tested code path with a dated
  drill, not a documented wish. **Type-1/Type-2 labeling** (operating
  rule 3): schema Type 1 (settled), wiring Type 2 (retractable).
- **principal-systems §2, pre-mortem:** written above, before code.
- **principal-governance §1** (written decision before code — this doc)
  and **§4 decoupled deployment**: code deploy ≠ feature release; the
  kill switch is the instant flag-off, measured in seconds
  (`flagctl set` + `/-/reload`).
- **principal-mindset §6** (monkey-first + dated kill): the drill is the
  monkey; F1's T+9 kill condition is the date; the retract ADR is
  pre-written, not discovered.
- **execution-doctrine §6** (andon): the kill switch IS the andon cord
  for the suppression feature — one human pulls it toward fail-open;
  turning it off needs two.

## Web source (Aditya's tools+web mandate)

Kill-switch discipline per industry practice: every high-risk feature
ships behind a kill switch; the kill-switch path is tested in staging
before it is needed; during an incident the flag is disabled *before*
root-causing; unit tests cover both flag states; the safe default is the
previous behavior.
([Feature-Flagging-Best-Practices](https://github.com/sujaydutta/best-practices/blob/HEAD/Feature-Flagging-Best-Practices.md),
§10.4 incident response + §11.2 review checklist, accessed 2026-10-05.)
What it changed in this design: S0 placement *before* the risky path
(check-then-boring-safe-path, per the kill-switch pattern); both-states
test coverage as a requirement; defaults = previous behavior (so the
loader change alone cannot alter gate behavior).

## Dissent on record

None inside the lane — the F1 verdict (Relay vs. Forge) was adjudicated
at the all-chiefs session: attempt the wire with the dated kill
condition; Relay dissented on the wire attempt itself and committed.
The canary-rejection semantic is flagged for Vault review in the PR
(it makes a contract value unloadable — deliberately, loudly).

## Reversal conditions

Reopen if: the T+9 drill fails (the retract ADR fires instead — this doc
is superseded, not patched); Vault rejects the S0-before-firewall
ordering (then S0 moves after the D6 screen with the reason taxonomy
updated); a Phase-4 R-15 design needs the canary semantic changed
(the rejection is the placeholder it replaces).

## Traceability

R-10 · `ops/devops-foundation.md` §2.1, §2.2, §2.4, §2.5 ·
`PIPELINE-REVISION.md` R-10 · 12H-PLAN §1 (dev-1), §2 (T+3/T+6/T+9),
F1 verdict · OPERATING-RULES §1.6 (Relay + Vault routing).
