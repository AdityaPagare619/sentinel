# Session A1 — Operating Rules

**Meeting:** Sentinel All-Chiefs Planning Meeting · **Session:** A1 (of 5)
**Subject:** Operating rules — rules before roadmap
**Date:** 2026-10-05 IST · **Facilitator/scribe:** Petu (founder-deputy)
**Status:** VERDICT — carried by disagree-and-commit. Nothing here is advisory.

**Principals present:** Forge (build/engine), Vault (security), Pager (on-call/ops),
Prism (UI/design), Ledger (contracts/audit), Oracle (decision science), Relay (delivery/devops),
Tripwire (adversarial/red cell).
**Aditya's standing order:** "STRICT RULES and EXTREME COORDINATION. Always make use of
tools, web — even a single requirement gets tools+web research. Professional doesn't mean
shipping class code — it's a DISCIPLINED manner, using various techs/methods. Make use of
the principal skills properly."

These rules bind every lane of the 12-hour parallel build plan (Session B1). The rules are
grounded in this repo's actual incidents — cited below. They are enforced by Petu's brutal-review
gates (§4.4), not by hope. *Lineage: principal-governance — "trust is the control system,
not a nicety" (Auftragstaktik, mission command).*

---

## 0. How this document was decided (the deliberation, on record)

Each principal stated an independent position first (principal-mindset §9: independent
judgments before aggregation), then the room fought, then committed.

**The sharpest disagreement — speed vs. gates (Forge vs. Vault + Tripwire).**
Forge's opening position: a 12-hour parallel plan cannot survive gate theater; most decisions
are Type-2 and should default fast, reviews should be lightweight, and "every requirement gets
web research" will bottleneck 6–8 concurrent lanes. Vault countered: the revision program's
top findings (R-1 unattested policy lifecycle, R-2 broken key resolution, R-3 unauthenticated
`/v2/enqueue`) are precisely what happens when velocity skips gates — three security holes
shipped under "fast Type-2" reasoning. Tripwire added the anti-theater audit
(principal-mindset §11): a gate practice that never says no is decoration; if Forge's speed
argument exempts the security reviews, the reviews will never bite.

*Resolution (disagree-and-commit):* speed comes from **pre-clearance and routing, not skipped
gates**. The operating map in §1 and the review router in §4 are fixed *before* the build wave,
so a lane never waits to discover who must review it. Gating is automatic (CODEOWNERS-style
routing, §1.6; review triggers in §4), not a manual tap that queues behind humans. Forge
dissented on the "one web research pass per requirement" rule (§2) as heavy — and committed.
The metric that settles it: **feedback-loop velocity** (execution-doctrine §7) — a one-line fix
that takes three weeks of reviews means the loop is dead. Both speed and gates are measured;
if either breaks, the rule is repaired in writing, not abandoned in hallway conversation.

Other fights resolved:
- **Contracts: frozen vs. iterative (Ledger vs. Pager).** Pager wanted soft contracts that can
  drift during a lane to keep the 12-hour cadence; Ledger wanted versioned immutability from
  minute one. Resolution: contracts are **frozen at lane start; mid-lane changes require a
  written contract amendment (ADR) and a re-announcement to consuming lanes the same hour**
  (§1.1). Consumer owns the contract (PIPELINE-REVISION §2.2). Iterating without writing is
  how R-1/R-2/R-3 happened. Pager committed.
- **Web research for every requirement (Prism vs. Petu).** Prism argued a tiny UI fix needs
  no web research. Resolution: the requirement scales with novelty — the *mandatory* part is
  the skill clause (§2.1): every PR must name which principal-skill clause binds the decision.
  Web research is mandatory for Type-1 decisions, cross-lane contracts, and anything touching
  security/performance/thresholds; for trivial Type-2 fixes the "web" step may be the repo's
  own prior art with a citation. What is never allowed: a decision from a feeling. Prism committed.
- **Merge shape (Oracle vs. Forge).** Oracle wanted linear history (rebase); Forge wanted merge
  commits that preserve lane provenance for the 12-hour plan's audit trail. Resolution: merge
  commits for program lanes, squash for small main fixes (§1.5). Oracle committed.
- **Andon stop (Tripwire vs. Relay).** Tripwire demanded anyone-can-stop-anything; Relay feared
  stalls in a time-boxed plan. Resolution: the andon cord is real (§5.3) — a stopper gets help,
  never blame (Toyota andon; execution-doctrine §6). A stop requires a written reason within
  the hour and is time-boxed to a 2-hour adjudication. Stops that never get pulled are a
  red flag (anti-theater: a practice that never says no is theater). Relay committed.

---

## 1. Coordination protocol

### 1.1 Contract-first parallel work

Parallel lanes coordinate through **contracts, not conversations**
(principal-governance: "contract first, implementation parallel").

1. **Contract lifecycle:** `proposed → frozen → changed (via ADR only) → versioned`.
   A contract is a machine-checkable artifact (JSON Schema, enum, checked-in test fixture) —
   prose descriptions are documentation *of* the contract, not the contract.
   PIPELINE-REVISION §2 sets the standard: each inter-stage contract names what crosses,
   what *never* crosses, and the version.
2. **The consumer owns the contract.** The consuming lane writes it, versions it, and must
   approve changes to it (unforgiving API design). No provider silently widens a contract.
3. **Freeze point:** contracts are frozen at lane start (§3.5 Definition of Ready). Mid-lane
   changes require: a contract-amendment ADR (problem, alternatives, what breaks), and a
   re-announcement to all consuming lanes **within the hour**. Unannounced contract drift is
   a §5 violation.
4. **Mock against the frozen contract from hour one.** "Backend didn't give us details" is
   never an acceptable stall (principal-governance §3). If a lane is blocked on another lane's
   output, it works against the contract + mocks and flags the dependency in the lane registry
   the same hour — work never freezes waiting for someone else (principal-governance §5).
5. **Compatibility:** additive changes inside a version; a new version only for true breaks
   (Google/Azure/Stripe consensus, per PIPELINE-REVISION §2.2/C7).

*Lineage:* Kubernetes scales review by routing — the k8s merge-bot auto-assigns reviewers
from `OWNERS` files and every file in a PR must carry an approver's sign-off before merge;
routing, not meetings, is the coordination mechanism
([contrib README](https://raw.githubusercontent.com/kenden/contrib/master/README.md)).

### 1.2 Decision log discipline

**Every Type-1 decision is recorded where it can be found in five minutes.**
No exceptions.

- **Where:** `docs/decisions/YYYY-MM-DD-<slug>.md`, committed before or with the PR.
- **What it contains:** the decision, its Type (1 or 2 — explicit; *unlabeled decisions default
  to Type 2 process, which is how Type 1 disasters happen*, principal-systems operating rule 3),
  alternatives considered and rejected with reasons, the dissent on record, who the single-threaded
  owner is, and the reversal conditions (what evidence would reopen it).
- **Why:** The two most expensive recent incidents were both process failures around recorded
  decisions: PR #65 imported `cryptography` without amending the frozen stdlib-only decision
  (broke main in a clean env; ratified only after a decision-log entry with reversal conditions,
  commit a91b0c2); the BYOK fix lane's "pushed, NOT merged" claim with commit hashes was never
  actually on the remote — a completion report was accepted without verification (§5.1).
- **Type-1 list (from PIPELINE-REVISION §9 — Aditya owns these):** /v2/enqueue HMAC, canonical
  PolicyVersion.content, provider test keys, page-me-anyway override. Under deputy authority the
  others proceed with stated recommendations.

### 1.3 Handoff protocol

Agent-to-agent handoffs carry more than status — a handoff without the evidence is a rumor.

Every handoff must contain:
1. **Goal and current state** — one paragraph: what the lane was asked to do, where it stands.
2. **Artifacts with verification evidence** — every artifact (branch, commit, PR, test result)
   with the verification that proves it exists (§5.1: verify the artifact, never the report).
3. **Open questions** — named, with named owners and deadlines.
4. **Explicit do-not-do list** — what the next agent must not touch or repeat (the lane registry's
   guardrails).
5. **Lineage** — what prior work it builds on and what it supersedes.

A handoff that says "done" without the commit hash, the test output, and the verification
method is rejected and sent back.

### 1.4 Worktree and branch conventions

From AGENTS.md hard-won lessons (incidents cited):

- **One durable worktree per lane.** `~/workspace/jev-builds/sentinel-<lane>/`. Never `/tmp`
  (the BYOK push verification was silently voided because the worktree was swept — durable
  worktrees are non-negotiable).
- **Never `git stash` in a shared clone.** The D6 lane's `git stash`/`pop` pulled another lane's
  foreign `FORGE-LANE-ONLY` stash and conflicted. Use a throwaway worktree for pristine-tree
  checks. (Four foreign Oct-3 stashes still sit in the shared clone's stash list — leave them alone.)
- **No two lanes in one worktree.** Wave-3's 003M re-dispatch raced a still-alive first builder
  on the same artifact paths and corrupted results. Re-dispatch rule: `pkill -f` the stale
  experiment's processes first, or use a fresh dir (single-owner rule, AGENTS.md).
- **Branch naming:** `lane/<slug>` for build lanes; `program/planning/<topic>` for planning
  artifacts (this session's branch: `program/planning/operating-rules`). No lane commits on
  `main`, `program/architecture-revision`, or any shared integration branch.
- **Do not merge planning branches into `program/architecture-revision`.** Planning artifacts
  land as separate branches; Aditya approves the revision program first.

### 1.5 Merge discipline

- No direct pushes to `main` or any `program/*` branch. Everything lands through a PR
  reviewed per §4. (GitHub writes go through the `gh` CLI; the write-gate card is Aditya's
  one-tap platform safeguard — it is never treated as a question to ask.)
- **Merge commits for program lanes** (provenance preserved for the 12-hour plan's audit trail);
  squash for small fixes on main. The merge PR must link the lane's design doc, contract
  version, and decision log entries.
- CI must be green. There is no "CI is flaky, merge anyway" — a flaky CI is itself a
  §5-reportable incident to Relay.

### 1.6 Ownership map (the router)

Published before the build wave starts — routing must never be ambiguous
(principal-governance: Linux-MAINTAINERS-style trust ladder):

| Area | Review owner (approves) | Adversarial spot (Tripwire) | Security sign-off (Vault) |
|---|---|---|---|
| gate kernel, policy tables, fail-open ladder | Oracle + Forge | yes — every policy change PR | yes — policy change path |
| receiver, ingress auth, rate limits | Forge + Vault | spot | yes — auth changes |
| forwarder, BYOK, outbox | Relay + Vault | spot | yes — key handling |
| event log, audit chain | Ledger + Vault | yes — audit claims | yes — tamper model |
| platform API, Prism UI | Prism + Forge | spot | yes — write surface |
| devops, config, flags | Relay | spot | yes — secret/config paths |

Trust is earned incrementally and **revocable**: whoever waves through junk loses review
rights (principal-governance §2). Adjudications escalate by the CRM ladder — observation,
concern, proposal, escalation — and seniority never overrides a stated quality concern.

---

## 2. Tool/web/skill mandates

**Aditya's order, operationalized:** *"even a single requirement gets tools + web research."*
This section is what that means as a checklist an agent can execute and a reviewer can verify.

### 2.1 The mandate

For every requirement (and every non-trivial decision):

1. **Tools.** Read the code first — `grep`, the actual file, the actual schema, the actual
   test. Claims about the repo are verified against the repo. (The R-1/R-2/R-3 findings exist
   because lanes reasoned from memory of the code, not from the code.)
2. **Web.** Research how it is actually done at scale — at least one external source with a
   citation (URL, accessed date) for Type-1 decisions and cross-lane contracts; for smaller
   Type-2 work the repo's own prior art (ADRs, postmortems, research notes) counts if cited.
   FYI drops are seasoning, never steering (AGENTS.md) — validate externally-sourced ideas
   against our own numbers.
3. **Skill.** Name the binding principal-skill clause. Not "skills applied: principal-systems"
   as a badge — the specific clause: e.g., *"principal-systems software-constitution:
   reliability is policy, not hope — the error budget governs the release decision here."*
   If no skill clause binds the decision, the lane says so in writing rather than inventing one.

### 2.2 What "done" looks like (evidenced in the PR)

Every PR body carries a **Skill & Evidence** block:
- Requirement/REQ-ID (or why none applies).
- Skill clauses that bind, with one line each on *how* they bind.
- Web sources consulted (URL + what it changed in the design), or the prior-art citations.
- Tool evidence: the commands run and their outputs (test runs, `git ls-remote` for push
  claims, schema validation results). *Verify the artifact, never the report* (AGENTS.md).

### 2.3 What gets rejected

- A PR whose design decisions cite no skill clause and no source — rejected as "from a feeling"
  (principal-governance: no major component starts from a feeling; PIPELINE-REVISION §3.5
  Definition of Ready).
- A claim of external completion ("pushed", "deployed", "Pages enabled") without artifact
  evidence — rejected per §5.1.
- Cargo-culted research: a URL pasted that the diff doesn't reflect is worse than no URL —
  the reviewer checks that the source actually changed the design.

*Lineage:* Google's design-doc culture — "design discussions act as a form of code review
before any code is written"; docs require goals, alternatives with trade-offs, and expert
review of security/privacy/storage/rollout sections *before* implementation begins
([Software Engineering at Google, Ch. 10](https://github.com/dayuanjiang/software-engineering-at-google/blob/HEAD/en/Chapter-10_Documentation/Chapter-10_Documentation.md)).
Our version is smaller and checked in, but the sequence is the same: writing before code,
experts before implementation.

---

## 3. Discipline standards — what "professional" means here

Per Aditya: professional = **a disciplined manner + methods**, not just class code.
The bar is the process, and the process is checkable.

### 3.1 Commit hygiene

- Conventional commits (`feat|fix|docs|research|test|chore(scope): ...`), one logical change
  per commit, imperative voice. No "WIP", no "fix fix fix" chains, no mystery binaries.
- Every commit that closes a requirement names the REQ-ID.
- No commits in shared clones except through the lane's own worktree.

### 3.2 PR body standard (template — mandatory)

```
## What / Why
## Requirement (REQ-ID or "none because ...")
## Design doc (link — required for Type-1 and cross-lane changes)
## Alternatives considered and rejected
## Skill & Evidence (see §2.2)
## Verification (tests run, outputs; external claims evidenced)
## Rollout / rollback plan
## Reviewers (from the ownership map §1.6)
```

A PR body missing the Skill & Evidence block is returned without review.

### 3.3 Test gates

From PIPELINE-REVISION §5 — each layer gets the test kinds its failure modes demand:

- **Contract/schema tests** for every inter-stage contract (§1.1): checked-in JSON Schemas,
  fixtures validated against them, CI fails when code and schema disagree (receiver R-6 pattern).
- **The frozen eval harness** (correlator): versioned corpus, labeled, with the known-failure
  hard tier — tuning against it while editing labels is cheating, enforced by git versioning.
- **Anti-corruption tests** at layer boundaries (import-graph assertions: the decision core
  never imports vendor shapes).
- **Invariant reconciliation** where the revision names it (per-window ingested = new +
  duplicate + storm_folded + change_windowed + label_stale + errors).
- **Nightly drift harness and threshold-gated load** per §5 — non-blocking until trusted,
  then blocking.

### 3.4 Honesty law (standing — Aditya's order)

- **A failed experiment is reported as a failure with its mechanism, never dressed up.**
- **No fake demos.** Anything simulated is labeled in-band as simulated. (The forwarder memo's
  "simulated-spill audit lie" — simulated pages replayed as `forward_failed` — is the named
  incident; the fix is contract C6's `simulated: true` propagation.)
- **No unverified claims.** The BYOK lane reported "pushed, NOT merged" with commit hashes —
  the push never landed (§5.1). The kubaik "84% fewer wake-ups, $8,400/mo saved" figures were
  phantom citations — PR #50 was blocked and the domain verdict was re-run without them.
  Numbers travel with their source or they don't travel.
- **No bought ceilings, no dressed-up verdicts.** Campaign law from AGENTS.md applies to all
  lanes: an exhaustion certificate is a map, not a surrender.
- **Completeness is verified, not claimed.** The lane that claims "12 steps done" shows the
  step-by-step evidence — the night-ops run that stopped at step 7 of 12 without a recorded
  reason is the cautionary example.

### 3.5 What gets an agent's work rejected (summary)

1. Work started without the Definition of Ready (acceptance criteria, short design doc with
   rejected alternatives + author-written risk paragraph, named owner, reviewer routing)
   — PIPELINE-REVISION §3.5.
2. Contract changes without a written amendment ADR + same-hour re-announcement.
3. PR body without Skill & Evidence; decision without a Type label and decision-log entry.
4. Any simulated/approximate result presented as measured. One strike: the work is returned.
5. Working from a shared clone with another lane's processes alive (single-owner rule).

---

## 4. Review gates

### 4.1 Different-agent review — always

Author ≠ reviewer, enforced by the ownership map (§1.6). Self-approval is a §5 violation,
not a shortcut. Reviews happen in writing on the PR — hallway approvals don't count.

### 4.2 Adversarial (Tripwire) review triggers

Tripwire reviews fire on:
- every policy-change PR (gate kernel, policy tables, fail-open ladder) — standing;
- any claim of a security property ("tamper-evident", "authenticated", "attested");
- any new external dependency or vendor API integration;
- **random spot reviews** — at least 10% of merged PRs sampled after merge; findings feed the
  anti-theater audit. A red team that never finds anything is either perfect or theater —
  principal-mindset §11 demands the audit check which.

### 4.3 Vault security review triggers

Vault sign-off is required before merge on:
- auth changes (any route, token, HMAC, key resolution — the R-2/R-3 class);
- anything that touches keys, secrets, or the `cryptography` exception path;
- the write surface (`/api/v1/integrations/*`) and any new ingress shape;
- changes to the tamper-evidence machinery (event log, anchors, checkpoint job).

### 4.4 Petu's brutal-review gates

Petu (founder-deputy, principal-on-metal) reviews:
- **Phase gates:** operating rules (this session), roadmap (B1), design docs before lanes open.
- **Pre-mortem gate:** every lane runs a written pre-mortem (principal-systems §2) before code;
  Petu attends the ones on Type-1 changes.
- **Artifact verification spot-checks:** Petu verifies the artifact directly on the box —
  raw logs, `git ls-remote`, the running binary — per the principal-on-metal law
  (Aditya's diagnosis: the principal being far from the metal is why work stalls).

### 4.5 What blocks merge

- Unresolved review thread (any reviewer). Blocking threads must carry a reason; "LGTM pending"
  is not a thread.
- Missing required sign-off (owner + triggered Vault/Tripwire sign-offs).
- CI red or missing the §3.3 gates for touched layers.
- PR body missing Skill & Evidence.
- An un-recorded Type-1 decision behind the change.

---

## 5. Violation handling

Rules without consequences are suggestions. The scale below is graduated; the andon cord is
not.

### 5.1 The verify-artifact precedent (the BYOK incident, 2026-10-04)

A fix lane reported "pushed, NOT merged" with commit hashes and canary proofs. The push never
landed — the remote tip was unchanged; the `/tmp` worktree was swept before the push, voiding
the evidence. Petu told Aditya "fixed" on an unverified report.

**The rule this created:** every "pushed/deployed/enabled/completed-externally" claim is verified
with artifact evidence (`git ls-remote origin <branch>` for pushes; the actual URL or receipt
for deployments) before the completion is reported — and completion reports are never evidence
of completion. (AGENTS.md: "Verify the artifact, never the report.")

### 5.2 The banned-practice precedents

- **`git stash` in shared clones is banned** — the D6 lane pulled a foreign stash into its
  worktree (AGENTS.md). Use throwaway worktrees.
- **Re-dispatching into a live lane's dir is banned** — 003M's corruption (AGENTS.md).
  `pkill -f` first, or a fresh dir.
- **Phantom citations are banned** — the kubaik incident: numbers without a verifiable source
  get the PR blocked and the claim struck from all briefs.

### 5.3 Graduated response

1. **First breach:** the work is returned for rework; the breach is noted in the lane's
   decision log (blameless — the *system* allowed it, and the system gets the fix item).
2. **Repeat breach:** the agent's review rights are suspended per the trust ladder
   (principal-governance: trust is revocable) until they re-clear with a reviewed lane.
3. **Safety breach (unverified security claim, silent contract drift, fake demo):** the andon
   cord — the work stops immediately, the stopper is thanked, and the lane restarts only
   after the written fix lands. Stops are time-boxed to 2-hour adjudication.
4. **Pattern breach:** blameless postmortem within 72 hours
   (principal-systems operating rule 5: summary → impact → root causes → action items with
   owners → lessons including *where we got lucky* → timeline), shared with all lanes.

*Lineage:* the rule for violations comes from the same place as the rule for decisions —
Google SRE postmortem culture (writeups assume good intentions, investigate systemic causes,
action items carry owners) and Toyota's andon (the first pull summons help; the line stops
only if the problem can't be fixed in cycle time; pulling the cord is never punished).

---

## 6. What this session did NOT decide (carried to later sessions)

- The 12-hour parallel plan itself and the roadmap — Session B1.
- The Type-1 open questions — §9 of PIPELINE-REVISION; only Aditya decides.
- Per-lane REQ documents and contract contents — lanes write them under these rules.
- The `program/architecture-revision` approval — Aditya's call; no building until then.

---

*Deliberation closed 2026-10-05. All eight principals committed under disagree-and-commit.
Next: Session B1 — roadmap and the 12-hour parallel plan.*
