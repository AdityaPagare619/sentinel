# RFC — Executable gates contract for the promotion checklist (TEAM 6 ↔ TEAM 3)

**Date:** 2026-10-07 · **Lane:** `lane/faang-quality-20261007` · **Author:** TEAM 6 (quality/test)
**Decision type:** Type 2 (scripts are reversible; the interface is the contract)
**Status:** PROPOSED — needs TEAM 3 (platform/infra) countersign on the interface table (§4).

## 1. Problem

The promotion checklist is prose; the gates it invokes are honor-system
(audit P1-24: "Promotion gates are honor-system (entrypoint-import gate has
no enforcement)"). TEAM 3 owns the checklist document; TEAM 6 owns the
executable gates. This RFC is the interface between them: script paths, exit
codes, and what "green" means — so the checklist can invoke gates by path and
the gates can evolve without rewriting the checklist.

## 2. Governing law

Repo policy (stated in `pre-pr-gate.sh`): *no GitHub Actions workflow files
on main — the script IS the CI.* Gates therefore live as executable scripts
under `scripts/ops/` plus unittest modules under `tests/`, and the aggregate
is `scripts/ops/pre-pr-gate.sh`.

## 3. The gates

| Gate | Script / test | Exit code contract | "Green" means |
|---|---|---|---|
| secrets-grep | `scripts/ops/secrets-grep.sh` | 0 = clean; 1 = hits (printed `file:line`) | No secret-shaped values outside the documented allowlist |
| entrypoint-import | `scripts/ops/gate-entrypoints.sh` (NEW) | 0 = all compile + import; 1 = any failure (printed per module) | `python3 -m compileall` clean over the repo tree AND every entrypoint module imports AND every CLI entrypoint answers `--help` (where it has one) without tracebacks |
| banner-contract | `tests/test_banner_contract.py` (NEW, structural) | unittest semantics (0 = pass) | Visible banner text, `aria-label`, and `data-mode` are mutually consistent per surface; every JS function that rewrites banner *text* also rewrites the accessible name in the same function |
| kill-the-client | `tests.test_gate` (existing, named in pre-pr-gate) | unittest semantics | Fail-open invariant holds |
| full-suite | `python3 -m unittest discover tests` (existing) | unittest summary line | `^OK$` AND test count ≥ 496 baseline (the count guard: tests must not silently disappear) |
| boot-smoke | `scripts/ops/receiver-smoke.sh` (existing) | 0/1 | The receiver process actually starts |
| config-schemas | `flagctl.py validate` (existing) | 0/1 | Example configs validate |
| kill-topology pin | `tests/test_kill_topology.py` (existing + new topology-pin test) | unittest semantics | Receiver topology is the legacy `Forwarder` (exact class) with the C3 kill wiring; kill→halt→re-arm→recover |

All gates are stdlib-only (`python3`, `bash`, `grep`); no network, no
credentials, no real PagerDuty contact (loopback `CaptureServer` only).
Suite budget: the full gate must stay fast enough to actually run — a gate
nobody runs is a wish.

## 4. Interface table (TEAM 3: countersign or amend)

The checklist invokes gates *only* by the paths in §3. TEAM 6 guarantees:

1. **Paths are stable.** A gate moves only with a checklist-visible rename
   note and a same-day checklist update (no silent moves).
2. **Exit codes are binary and honest.** 0 = green, non-zero = red. No
   "green with warnings" — a warning is either a failure or it is deleted.
3. **Failure evidence survives.** `pre-pr-gate.sh` writes per-stage logs to
   `/tmp/sentinel-gate-<ts>/`; the report tail shows the last lines.
4. **Green is re-derivable.** Any engineer can re-run the gate on the pinned
   SHA and get the same verdict (no hidden state, no network).
5. **Every gate has a failure-proof.** For each new gate, the lane records a
   deliberately-broken input that the gate catches (see §6) — a gate that
   has never been seen to fail is theater and is reported as unproven, not
   done.

TEAM 3 guarantees: the checklist names gates by path + expected-green
semantics from this table, and does not re-implement gate logic in prose.

## 5. Integration

New stages are added to `scripts/ops/pre-pr-gate.sh` in dependency order:
`secrets-grep` → `entrypoints` → `full-suite` (which includes the banner and
kill-topology tests) → the rest. `pre-pr-gate.sh --fast` still skips the
full suite only; it never skips secrets-grep or entrypoints.

## 6. Failure-proofs (each gate demonstrated to fail)

- **entrypoints:** a scratch copy of an entrypoint with a syntax error →
  gate red, naming the module.
- **banner-contract:** a fixture HTML where a JS function rewrites banner
  text without touching `aria-label` → test red (this is the exact
  `aria-label="Simulated mode"` bug class).
- **secrets-grep:** a fixture `api_key = "sk-live-…"` → red. (Already
  proven in production: the gate caught the new drill script's
  `webhook_secret=<redacted>` literal on 2026-10-07.)
- **kill-topology pin:** a factory variant wiring `DurableForwarder` →
  pin test red (the wrong-topology mistake, caught in code, not in a
  headline).

## 7. Pre-mortem (it is Oct 2027; the gate system failed us)

1. *The gate went red on main and everyone started ignoring it.* Mitigation:
   §4.2 — no "green with warnings"; a persistently red gate is either fixed
   or formally quarantined with a dated ticket + owner (see workstream item
   3), never normalized. (Vaughan: normalization of deviance is the killer.)
2. *A gate passed on broken code.* Mitigation: §4.5 failure-proofs —
   every gate has a recorded broken input it catches.
3. *The suite got slow and `--fast` became the default.* Mitigation: the
   suite budget is a release criterion; slow tests get fixed or quarantined,
   and `--fast` is documented as invalid for PRs (already in the script
   header).
4. *TEAM 3's checklist and TEAM 6's gates drifted.* Mitigation: the
   interface table — paths stable, renames checklist-visible.

## 8. Lineage

DORA/Accelerate — the four delivery metrics as a *balanced set*: a gate
suite is itself a measurement system, and optimizing one stage (speed) while
the others rot is the proxy trap. Google SRE error budgets — the gate is a
release policy: red means halt, no exceptions, because "no merge, no
deploy, no exceptions" is the only budget rule that survives contact with
schedule pressure.
