# RFC: Operator engage/re-arm surface for the receiver topology (2026-10-07)

Owner: FAANG engine team (single-threaded). Branch: `lane/faang-engine-20261007`.
Type: **Type 2** (additive CLI + one state-file default; reversible). Binding: control principle — the kill path never calls Jev, never waits on the race (AST test enforces); the operator surface is a flag-flip + audit event, nothing more.

## Problem
The receiver topology's kill switch (`build_pipeline_from_env`, receiver.py:1273) is an in-process `KillSwitch` with no operator surface: drills flip `pipeline.kill_switch` in-process, but an operator responding to a real incident has no way to engage it. The audit's fix-wave residual: "no operator engage/re-arm surface for the receiver topology." The platform tier's safety API is a different process (and a documented no-op lever there) — it cannot reach the receiver.

## Options considered

**A. State-file CLI (chosen).** `KillSwitch` already supports atomic cross-process state files (`_write_state_file`/`_read_state_file`, "cross-process visibility"). Wire `state_path=<state_dir>/kill-switch.json` into the receiver's switch; ship `scripts/ops/sentinel-kill` (stdlib-only) with `engage|disengage|status`, flipping via the same `KillSwitch` methods (engage idempotent; disengage requires `confirm=True` — the sticky re-entry rule, mapped to a `--confirm` flag). Audit event appended to the receiver's sqlite audit log (`SENTINEL_DB`, `BEGIN IMMEDIATE` — concurrent-safe). Default state dir is `~/.sentinel/state` (outside the repo, per the P0-5 lesson).
- Pros: no new attack surface (no network listener); reuses the audited atomic-file mechanism; engaged persists across receiver restarts (fail-closed — the audit flagged cold-start *disengage* as the dangerous direction); the AST purity test covers the CLI (stdlib-only, no Jev import possible).
- Cons: requires shell access (acceptable — the operator already needs it to run the receiver); one receiver per state dir (documented).

**B. HTTP admin endpoint on the receiver.** Rejected: the receiver is a webhook-ingest server; a new privileged endpoint needs auth, rate-limiting, and audit of its own — a larger attack surface for a flag-flip. Violates "flag-flip + audit event, nothing more."

**C. Unix signals (SIGUSR1/USR2).** Rejected: a signal cannot carry the `confirm=True` re-arm semantics (sticky re-entry is a safety property, not ceremony); no actor identity for the audit event.

**D. Reuse the platform safety API.** Rejected: different tier, different process; the deployed tier's kill is a per-instance no-op lever — wiring the receiver to it would be theater.

## Required hardening (part of this RFC)
`KillSwitch.engage()/disengage()/status()` currently consult in-memory `_engaged`, but `engaged` reads the state file when `state_path` is set. With two writers (receiver + CLI), the methods must consult the file-aware state or they double-audit and no-op incorrectly (fail-unsafe direction for disengage). Fix: internal `_is_engaged_locked()` uses `_read_state_file()` when a state path is set. Behavior without a state path is unchanged.

## Pre-mortem ("this failed in production — why?")
1. **Stale engaged file after the incident:** operator forgets to re-arm → paging stays halted. Mitigated: `status` shows `engaged_at`; the halt is LOUD (stderr per absorb + `killed` metric + audit); re-arm is one command. Fail-closed is the chosen direction — a forgotten halt pages nobody; a forgotten re-arm pages everybody.
2. **CLI and receiver disagree:** fixed by the file-aware hardening above; the file is the single source of truth when a state path is set.
3. **Audit log locked/busy:** `engage()` never raises for audit failure (existing rule — the flip is safety-critical, the audit write is loud-on-stderr). The sqlite `BEGIN IMMEDIATE` serializes concurrent appends.
4. **Operator engages the wrong receiver:** one state dir per receiver, documented; `status` prints the state dir it read.

## Rollback
Stop passing `state_path` (one-line revert in `build_pipeline_from_env`) → switch returns to in-process-only; delete the state file. The CLI refuses to run against a receiver without a state file (loud error, no silent no-op).

## Verification
- `tests/test_kill_operator_surface.py`: (a) two `KillSwitch` instances sharing a state file — engage via one, the other sees engaged (the CLI simulation); (b) disengage without confirm raises `RearmRefused`; with confirm flips; (c) engage is idempotent — no duplicate audit event; (d) restart persistence: new instance with the same state path reads engaged.
- AST purity test extended: `scripts/ops/sentinel-kill` must import no Jev (the existing `test_safety.py` AST test pattern).
- Manual: `sentinel-kill status` against a live receiver shows the drill's flips.

## Lineage
Knight Capital: "every system that sends anything needs an off switch… knowing how to halt it should not require understanding it in the middle of an incident" (dev.to/axrisi); Toyota andon — the cord must bring help, and pulling it must be a one-motion act. The dangerous direction is the switch that silently disengages (audit P0-4 on the deployed tier); this design persists engaged across restarts.
