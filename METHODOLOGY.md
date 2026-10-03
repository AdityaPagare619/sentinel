# Sentinel — Methodology: the git-powered engineering machine

**Repo:** `https://github.com/AdityaPagare619/sentinel` — private → public at Preview-1.
**Local working dir:** `~/workspace/jev-builds/sentinel/` — same machine; see `LOCAL-OPS.md`.

## 1. Branch discipline

- `main` is **protected**: PR-only, no direct pushes. **Local test suite green
  required** (`python3 -m unittest discover tests` run locally on the PR head —
  GitHub Actions is not the gate; see ops/ci-repair.md for the platform-bug
  history). Review required from a **DIFFERENT agent** — no self-merge, ever.
- Work happens on lane branches: `lane/<lane>-<short-desc>` (e.g. `lane/engine-gate-policy`,
  `lane/adapters-opsgenie`, `lane/docs-preview1-demo`). One branch = one reviewable unit.
- Short-lived: branch → PR → squash-merge → delete. `main` history is a clean, readable
  sequence of squashed PRs.
- File ownership is per-branch too: a lane owns its files; crossing into another lane's
  files requires a heads-up in the PR description, never a silent edit. (This is why the
  build coordinator owns `src/`, `tests/`, `PROGRESS.md` and the base setup owns docs —
  see `LOCAL-OPS.md`.)

## 2. Conventional commits

`feat|fix|docs|test|chore(scope): <short imperative summary>` — e.g.
`feat(gate): expected-cost threshold policy`, `test(receiver): loopback HTTP suite`.

Every commit: a body line saying *why* (the decision), and a footer line pointing at the
decision-log entry when one exists (`Decision: ops/decision_log.md#2026-10-02-gate-policy`).

## 3. CI — `.github/workflows/sentinel-ci.yml`

Runs on every PR and every push to `main`. **stdlib only** (frozen decision — no pip
deps, no network installs):

1. **Full test suite:** `python3 -m unittest discover tests` — all green, zero tolerance.
2. **Repeatability probes:** option-order shuffle (bar <3% disposition change) and
   repeat-call flip check (bar <2%) on a 200-alert sample — Jev non-determinism (1.3–2.2%,
   no seed) must stay audited, never surprising.
3. **Fail-open kill-the-client test:** mock client raising `JevOverloaded`/`JevTimeout` on
   every call → assert every alert returns `passthrough` and the forwarder relays the
   original payload. This test failing is a **release blocker**.
4. **Secrets grep:** fail on anything looking like a key/token in the diff —
   `TYPESAFE_API_KEY` values, `sk-`/`key-`/`token` patterns, `.env` files, `*secret*`,
   `*.pem`. Nothing secret is ever committed, ever.

## 4. Definition of Done (a PR is mergeable iff)

- [ ] Tests green (full suite + new tests for the new behavior).
- [ ] Docs updated: README and/or the relevant doc touched by the change.
- [ ] PROGRESS.md entry by the build coordinator (status is honest: DONE / FAILED / KILLED).
- [ ] Demo-able: the change can be shown in the Preview-1 demo transcript.
- [ ] No secrets in the diff (CI enforces; humans double-check).
- [ ] Claim-Auditor pass on every number the PR description asserts (source or `UNVERIFIED`).

## 5. Ledgers (the PETU-LABS pattern, adapted)

- **`ops/decision_log.md`** — every consequential decision: date, decision, rationale,
  who called it. Written at decision time, never reconstructed from memory.
- **`ops/constraint_registry.md`** — hard constraints (Jev limits, architecture locks,
  deadlines). The Constraint Sentinel checks every plan against it before execution;
  violations = hard stop + replan, not patch.
- **`PROGRESS.md`** (build coordinator's) — build order, milestones M1–M6, integration
  notes, integration notes, decisions-needed.

## 6. The false-suppress incident rule

**Any unexplained false suppress on a SEV1-class alert is treated as a SEV1.**
Blameless postmortem within 24h, the report derived from the audit log (input hash,
model version, probabilities, thresholds, allowlist membership). If the audit log
cannot fully explain the decision, that is itself a finding — against the audit log.

## 7. 100x-speed operating principles (why agents beat human velocity)

- **Parallel lanes, sequential main.** Lane branches are independent by construction;
  interfaces are contracts (see `ARCHITECTURE.md` §3). Never block a lane on another
  lane — mock the interface contract and move.
- **PRs under ~300 lines.** Small PRs review in minutes, not days. Review SLA:
  minutes, not days.
- **Waves, not idling:** SCOUT (parallel variations) → JUDGE (Red Team + auditors score
  in parallel) → BUILD (parallel lanes) → VERIFY (independent gate). Dozens of agents
  at peak, each with a bounded mission and a write-back — that beats an idle standing
  army every time.
- **Write-back or it didn't happen.** Code → repo branch, decisions → log,
  intel → docs. An agent that finishes without writing back gets its work redone.
- **Kill fast, report honestly.** A falsified hypothesis is a success — it is a negative
  result logged in `PROGRESS.md` with evidence, not a silent pivot. Never dress up a
  failure.

## 8. Principal operating system (standing — Aditya's order 2026-10-03)

Every agent on every lane operates as a **principal**, never a ticket-taker. These
skills are mandatory before starting lane work — read the SKILL.md, then apply it:

- `~/workspace/skills/principal-systems/` — universal laws (global over local
  optimization, eternal friction, Type 1 vs Type 2 decisions), research method
  (five whys, Chesterton's fence, pre-mortems), constitutions for software,
  infrastructure, data/AI, and product/design.
- `~/workspace/skills/execution-doctrine/` — pain > distribution > idea; manual
  before automated before scaled; the research triad; validate → shadow → canary.
- `~/workspace/skills/principal-governance/` — API contracts, design systems, RFC
  culture, unified telemetry, feature flags, the parallel-teams rule.
- `~/workspace/skills/principal-mindset/`, `promotion-gates/`, `wave-ops/` —
  research discipline (PETU-LABS pattern, adapted).

**Parallel-teams rule:** no lane ever stalls waiting on another lane. Work against
the frozen contract + faithful mocks; flag dependencies in the lane registry the
same hour. "Backend didn't give us details" is never an acceptable stall — the
contract is the coordination mechanism: discussed, planned, versioned.

**Decision authority:** Petu holds founder-deputy authority. All decisions are
taken without Aditya via combined group discussion (disagree-and-commit) and
recorded in `ops/decision_log.md` with Type 1/2 labels. No permission asks —
standing order.
