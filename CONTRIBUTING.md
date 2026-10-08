# Contributing to Sentinel

Sentinel is open source (MIT). Contributions welcome — but this is a paging
product, so the bar is higher than usual. The rules below are enforced by
gates, not goodwill.

## Prereading

- `README.md` — what Sentinel is and its honest limits.
- `docs/planning/CURRENT_PLAN.md` — where the project stands right now.
- `METHODOLOGY.md` §8 — the principal-engineering disciplines every lane follows.

## Build

```bash
python3 -m compileall -q src platform/server scripts
```

Consoles: `platform/ui-v2/index.html` is the single console source.
`python3 deploy/gh-pages/build-v2.py --out /tmp/site --backend <url>`
builds production (`/`), staging (`/staging/`), and reserves `/loadtest/`.

## Test gates (must be green before any push)

```bash
bash scripts/ops/pre-pr-gate.sh   # 8 stages: secrets, entrypoints, banner contract,
                                   # kill-the-client, full suite, boot smoke, config schemas
```

Plus, for console changes: `python3 deploy/gh-pages/build-v2.py` must pass its
ordinality gate (no calibrated-probability language in served bytes).

## PR discipline

1. **RFC before code** for anything architectural (see `docs/planning/rfc/`).
   Copy/docs fixes don't need an RFC, but they still need green gates.
2. **Alternatives and tradeoffs** in the RFC; **pre-mortem** for risky changes;
   **rollback plan** for anything touching the paging path.
3. **Failing-before / passing-after evidence**: show the gate or test failing
   on the old code and passing on the new.
4. **Verify, don't trust**: every "pushed" claim is checked with
   `git ls-remote origin <branch>`. No silent pushes.
5. **No `git stash`** in the shared clone — use a throwaway worktree.

## Hard rules (non-negotiable)

- **Zero real PagerDuty contact** in dev, sim, or tests. Simulation and load
  tests structurally use FakePD. Production pages only via explicit customer
  BYOK plus explicit operator action.
- **Ordinality law**: Jev confidence is ordinal, never a calibrated
  probability. Banned in code, copy, and docs: "Expected Calibration Error",
  reliability diagrams, `P(p1)=` notation, "% confident" claims.
- **Deterministic gate owns every page/suppress decision.** Jev advises only.
- **Kill = halt all paging** (fail-closed). Never invert the semantics.
- **No secrets** in chat, files, logs, committed envs, browser JS, or process
  args. The Jev key lives in env/vault only.

## Diagrams

All architecture diagrams, flowcharts, and pipeline drawings use the
diagram-design skill (`~/workspace/skills/diagram-design/`): read SKILL.md,
load the matching type reference, run the §9 taste gate. No ASCII boxes, no
Mermaid. Output: single self-contained HTML in `docs/planning/diagrams/`.
