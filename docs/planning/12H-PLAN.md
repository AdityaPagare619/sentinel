# Session B1 — The 12-Hour Plan

**Meeting:** Sentinel All-Chiefs Planning Meeting · **Session:** B1 (of 5)
**Subject:** the 12-hour parallel execution wave — every domain team works in
parallel, dropping cumulative work at checkpoints
**Date:** 2026-10-05 IST · **Facilitator/scribe:** Petu (founder-deputy)
**Status:** VERDICT — carried by disagree-and-commit. Nothing here is advisory.

**Anchors (read before executing):**
- `docs/planning/OPERATING-RULES.md` — coordination discipline; this plan obeys it.
- `docs/planning/ROADMAP.md` — Phase 0 honesty triage is the 12-hour candidate;
  its work items, entry/exit falsifiers, and owner domains are the plan's scope.
- `docs/architecture-revision/PIPELINE-REVISION.md` §6 (ranked changes) §8 (sequenced plan).

**Skills bound:** principal-systems, principal-governance, principal-mindset,
execution-doctrine. The binding clause is named per contested call (§0).

**T+0 definition:** Aditya's approval of `docs/architecture-revision/PIPELINE-REVISION.md`
(the program entry gate). All times relative. If approval hasn't landed, T+0 waits —
§4 names the prep work that proceeds without it.

---

## 0. Session record — deliberation, disagreements, disagree-and-commit

Each chief stated an independent position first (principal-mindset §9), the room
fought, verdicts below. After the verdict: total commitment, no re-litigation.
Forced probabilities are stated on contested calls (principal-mindset §7).

**F1. R-10 wire-in-12h vs. retract (Relay vs. Forge).** Relay: wiring `flags.json`
into the live gate inside 12 hours is reckless — the loader touches the decision
path, gate semantics need a drill, and a failed drill is worse than an honest
retract. Forge: retracting is doc deletion dressed as a decision; the roadmap
"strongly prefers" wire and the wiring is mechanical per `ops/devops-foundation.md`
§2.1. *Verdict (disagree-and-commit):* attempt the wire with a dated kill
condition — the kill-switch drill must pass on `staging-lab` by T+9. If it fails,
the retract ADR lands at T+9 and a follow-up lane is registered the same hour.
Forced probability 70% the wire lands. Relay dissented, committed.
*(principal-mindset §6: monkey-first — attack the drill, the hardest part, first.)*

**F2. All seven contracts vs. the evaluable subset (Ledger vs. Oracle).** Ledger:
contract-first is the coordination mechanism; all C1–C7 drafted in the 12h or
Phase 1 lanes start without contracts. Oracle: without the eval harness (R-14)
the correlator-side contracts (C2/C3) will freeze the wrong vocabulary — we'll
re-version within weeks, which is versioning theater. *Verdict:* draft all seven,
but C2/C3 carry an explicit `stability: draft` marker and stay v0.x (the
versioning rule — additive changes inside a version, breaks only in new versions
— permits draft churn). Oracle dissented, committed.
*(principal-governance §1: contract first, implementation parallel.)*

**F3. Sender-inventory ownership (Vault vs. receiver).** Vault: the sender
inventory is an auth-posture artifact; Vault must own it. Receiver (Forge):
they own the ingress routes and the logs — inventory without route knowledge is
fiction. *Verdict:* recv-2 owns the inventory; Vault reviews at T+6 with veto
authority over the auth-matrix accuracy. Vault committed.
*(principal-governance: single-threaded ownership, reviewer routing.)*

**F4. Q8/R-20 scope (Prism vs. Petu).** Prism: R-20 is Phase 5 work; the 12h
should only take the both-options design doc. Petu: the staging demo keeps using
mocks — the mock-honesty invariants (`mock` never maps to freshness `live`) must
be written now or the demo lies in-band. *Verdict:* both-options doc (ui-1) +
mock-honesty invariant checklist (ui-2) by T+9. Prism committed.
*(execution-doctrine: honesty before polish; no fake demos.)*

**F5. Contract-test skeleton before contracts (Forge vs. testing-qa).** Forge:
machinery before the contracts it tests is theater. Testing-qa: without one
skeleton, seven domains invent seven validators and the §3.3 contract-test gate
dies on arrival. *Verdict:* qa-2 ships a minimal stdlib JSON-Schema-validation
skeleton (`tests/contracts/`) at T+3; domains plug fixtures in by T+6. Forced
probability 60% the skeleton survives first contact. Forge committed.
*(principal-systems software constitution: explicit versioned boundaries.)*

**F6. Tripwire review bandwidth (Tripwire vs. the room).** Tripwire: adversarial
review on all seven contract drafts. The room: 12 hours cannot afford it, and
perfunctory review is theater. *Verdict:* Tripwire reviews C4 (disposition
vocabulary — highest stakes) and C6 (the `simulated: true` propagation — the
named honesty incident) in full, plus random-spots 2 of the remaining 5 at T+12
findings feed the anti-theater audit. Tripwire committed.
*(principal-mindset §11: a practice that never says no is theater.)*

**F7. Checkpoint granularity (Pager vs. Petu).** Pager: 3-hour checkpoints are
coarse for the two-line fixes (R-8, P6) — they can merge at T+3. Petu: the plan's
auditability needs uniform checkpoints; finer only where the critical path
demands it. *Verdict:* 3h checkpoints stand; R-8/P6 carry a T+3 *target* merge
inside the T+6 review gate (merge per §1.5 rules, never rushed). Pager committed.

---

## 1. Agent roster — every domain, named agents, deliverables, checkpoint times

Worktrees: one durable worktree per lane under `~/workspace/jev-builds/sentinel-<lane>/`
(OPERATING-RULES §1.4). Lane branches: `lane/<slug>`; this plan's artifacts never
commit to `main` or `program/architecture-revision` (§1.5). No agent idle: every
agent drops a named artifact at every checkpoint in §2. Agent names are
domain-coded; chief review routing follows OPERATING-RULES §1.6.

| Domain team | Agent | Deliverable(s) | Checkpoint drops |
|---|---|---|---|
| **receiver** | recv-1 | R-8 exception-path fix (malformed JSON ⇒ `unparseable`, not `handler_panics`); C1 ingress JSON Schemas draft | T+3 branch+test · T+6 merged · T+9 schema reviewed · T+12 frozen |
| | recv-2 | Sender inventory (every `/v2/enqueue` sender: source, auth today, cutover risk); R-3/Q2 HMAC migration design doc (auth matrix target, onboarding-mode window, cutover plan) | T+3 inventory v0 · T+6 inventory final, design v0 · T+9 design reviewed (Vault) · T+12 approved |
| **correlator** | corr-1 | C2 canonical `Alert` contract draft (consumer-owned); review of C1 from the consumer side | T+3 v0.1 · T+6 review comments addressed · T+9 frozen · T+12 merged review |
| | corr-2 | C5 trace-ID envelope: correlator-side requirements + per-layer propagation notes | T+3 notes · T+6 draft section · T+9 frozen · T+12 merged review |
| **race/jev-boundary** | race-1 | R-13/Q6 design doc: FakePD promotion plan + stdlib Jev cassette recorder + stub-cassette caveat text ("shape unverified against live vendor") | T+3 skeleton · T+6 full draft · T+9 reviewed · T+12 approved |
| | race-2 | Vendor-boundary honesty checklist: seed list of PD-docs behaviors with no FakePD test (the R-13 coverage gate, started) | T+3 seed list · T+6 expanded · T+9 reviewed · T+12 handed to Phase 4 |
| **gate** | gate-1 | R-1/Q3 design docs for BOTH options: (a) canonical `PolicyVersion.content` vs (b) 2-attestor PR rule + operator CLI ceremony prototype | T+3 skeletons · T+6 full drafts · T+9 reviewed (Oracle+Forge) · T+12 approved |
| | gate-2 | C3 typed `TriageResult` draft (consumer-owned); D8 fail-closed flip design (part of R-1) | T+3 v0.1 · T+6 reviewed · T+9 frozen · T+12 merged review |
| **forwarder-byok** | fwd-1 | P6 simulated-spill fix (`simulated: true` propagates; log never records non-failure as `forward_failed`); C6 `ForwardReceipt` draft (atomicity + `simulated` propagation) | T+3 branch+test · T+6 merged · T+9 C6 reviewed (Tripwire) · T+12 frozen |
| | fwd-2 | C4 `Disposition{action,reason}` contract draft (consumer-owned; vocabulary pinned `pending-R9-ratification`) | T+3 v0.1 · T+6 review comments · T+9 reviewed (Tripwire) · T+12 frozen |
| **event-log-audit** | evlog-1 | C5 trace-ID envelope draft (owned; co-consumer platform-api); checkpoint-HMAC discipline note reused by R-5 | T+3 v0.1 · T+6 draft · T+9 frozen · T+12 merged review |
| | evlog-2 | R-18 threat-model skeleton (adversaries × detection × time-bound × evidence — the Type-1 requirement doc per PIPELINE-REVISION §3.4, written before any Phase 5 build) | T+3 skeleton · T+6 full draft · T+9 Vault review · T+12 approved |
| **platform-api** | plat-1 | C7 `/api/v1` read contract draft + `openapi.yaml` seed | T+3 v0.1 · T+6 draft · T+9 frozen · T+12 merged review |
| | plat-2 | R-16 design doc: bearer token on the five write endpoints + versioning unification (canonical `/api/v1`, `Deprecation`+`Sunset` aliases, policy in `platform/contracts/README.md`) | T+3 skeleton · T+6 full draft · T+9 reviewed · T+12 approved |
| **prism-ui** | ui-1 | R-20/Q8 both-options design doc: (a) appeal wired as audit-logged override → real paging path, vs (b) removal from the live build | T+3 skeleton · T+6 full draft · T+9 reviewed (Prism+Vault) · T+12 approved |
| | ui-2 | Mock-honesty invariant checklist (mock replay never maps to freshness `live`; fabricated payloads carry the reconstruction mark at point of display) + `antislop.py` phantom-scan extension seed | T+3 checklist v0 · T+6 reviewed · T+9 frozen · T+12 handed to Phase 5 |
| **devops-environments** | dev-1 | R-10 wire-or-retract: `flags.json` schema + loader wiring into the live gate; kill-switch drill design; F1 kill-condition owns the T+9 verdict | T+3 wiring on staging-lab · T+6 drill rehearsal · T+9 drill (or retract ADR) · T+12 record/ADR landed |
| | dev-2 | §4.2#5 `deploy.sh` docs rewrite (pinned SHA → health-gated swap; `git pull` forbidden); §4.2#11 `ops/incidents/` + postmortem template; §4.2#9 page-vs-ticket classification table with runbook links | T+3 all three drafts · T+6 reviewed · T+9 merged · T+12 exit-verified |
| **testing-qa** | qa-1 | R-11 honesty half: PR template names GHA `startup_failure` honestly; "full suite green, artifact attached" becomes a written merge requirement | T+3 template draft · T+6 reviewed · T+9 merged (template live) · T+12 in use by all lane PRs |
| | qa-2 | `tests/contracts/` skeleton: stdlib JSON-Schema validation + fixture convention that C1–C7 plug into (§3.3 contract-test gate) | T+3 skeleton · T+6 two contracts plugged (C1, C6) · T+9 all seven plugged · T+12 CI wired |

**Review routing per drop** (OPERATING-RULES §1.6): gate/policy → Oracle+Forge,
Tripwire on every policy PR, Vault on policy-change path · receiver/auth →
Forge+Vault · forwarder/keys → Relay+Vault · event log → Ledger+Vault ·
platform/UI → Prism+Forge · devops/config → Relay. Vault sign-off required on
the R-10 wiring, R-3 auth matrix, R-16 bearer design, C6 key-handling surface.
Author ≠ reviewer, always.

---

## 2. Checkpoint schedule — who drops what, cumulative; who reviews; dependency edges

Cumulative rule: each drop builds on the last; a checkpoint drop that doesn't
reference the previous drop's review comments is returned. Dependency edges name
**who waits on whom and what the waiter does meanwhile** — per OPERATING-RULES
§1.1: work against contracts + mocks, never freeze; every dependency is flagged
in the lane registry the same hour.

### T+0 — kickoff (program entry gate fires)

**Drops:** 10 lane registries exist (one per lane: owner, REQs served,
contract versions pinned, reviewer routing, Definition of Ready signed —
acceptance criteria, short design doc with rejected alternatives +
author-written risk paragraph, rollback plan). Aditya's approval recorded.
**Reviewed by:** Petu (brutal-review gate: design docs before lanes open).
**Falsifier:** any lane without a registry = the plan has not started.
**Edges:** none — all lanes start against the frozen contract drafts once
they exist; nothing waits.

### T+3 — drafts dropped (cumulative: registry → first artifacts)

| Who drops | What (cumulative) | Reviewed by | Dependency edge / waiter meanwhile |
|---|---|---|---|
| recv-1 | R-8 branch + test (malformed JSON ⇒ `unparseable`); C1 schemas v0.1 | Forge | none |
| recv-2 | sender inventory v0 (named senders, auth-today column) | — (self-check) | waits on nothing; route logs are local |
| corr-1 | C2 v0.1 | Oracle | consumes C1 — C1 is v0.1, so drafts against it and logs 2 open questions |
| corr-2 | C5 correlator requirements notes | Oracle | waits on evlog-1's envelope draft — meanwhile writes propagation notes from the correlator's side |
| race-1 | R-13/Q6 design skeleton | Forge | waits on Q6 provisioning for nothing (design proceeds); meanwhile seeds cassette caveat text |
| race-2 | vendor-behavior checklist seed | — | independent |
| gate-1 | R-1 options (a)/(b) skeletons | Oracle+Forge | none (design proceeds without Q3) |
| gate-2 | C3 v0.1; D8 flip design | Oracle | waits on corr-1's TriageResult producer review — meanwhile drafts C3 from the gate's consumer side per contract ownership |
| fwd-1 | P6 branch + test; C6 v0.1 | Relay | none |
| fwd-2 | C4 v0.1 (vocab pinned `pending-R9-ratification`) | Ledger | waits on R-9 ratification (Phase 1) — meanwhile drafts with the pin; flags the dependency in the lane registry the same hour |
| evlog-1 | C5 envelope v0.1 | Ledger | none |
| evlog-2 | threat-model skeleton | Vault (ack) | none |
| plat-1 | C7 v0.1 + openapi seed | Prism+Forge | none |
| plat-2 | R-16 design skeleton | Vault (ack) | none |
| ui-1 | Q8 options skeleton | Prism | waits on plat-1's appeal-endpoint shape for option (a) — meanwhile drafts both options against the contract stub |
| ui-2 | mock-honesty checklist v0 | Prism | independent |
| dev-1 | R-10 wiring on staging-lab (branch) | Relay | waits on staging-lab access — Relay provisions at T+0; meanwhile finalizes the drill plan |
| dev-2 | deploy.sh docs, postmortem template, page-vs-ticket table — all v0 | Relay | independent |
| qa-1 | PR-template draft | Relay | none |
| qa-2 | `tests/contracts/` skeleton | Forge | waits on nothing — the skeleton defines the fixture convention the contracts plug into |

**Checkpoint falsifier:** any contract draft still prose-only (no
machine-checkable artifact) is a red item; any missing drop without a
written reason + re-plan is a §5 violation.

### T+6 — reviewed + revised (cumulative: drafts → addressed reviews)

**Drops:** every T+3 draft carries reviewer comments addressed or rebutted
in writing; R-8 and P6 **merged** (PR per §1.5: owner + triggered sign-offs,
PR body with Skill & Evidence, CI-honor per qa-1's template); sender
inventory final (every sender named — "unknown sender" is a red item);
R-10 wiring rehearsed on staging-lab; C1 and C6 fixtures plugged into
qa-2's skeleton.
**Reviewed by:** owners per §1.6 map; Vault vetoes the sender-inventory
auth matrix (F3); Tripwire begins C4/C6 adversarial review (F6).
**Checkpoint falsifier:** R-8/P6 unmerged without a written reason;
inventory with an unnamed sender; zero contracts validated against the
skeleton.
**Edges:** C-draft authors wait on reviewer comments — meanwhile they
write the amendment-ready change log (proposed → frozen lifecycle,
§1.1). dev-1 waits on the T+9 drill — meanwhile documents the retract
fallback ADR skeleton so F1's kill condition can fire without discovery
work (monkey-first: the fallback is pre-written).

### T+9 — freeze + drill (cumulative: revised → frozen; the hard verdicts)

**Drops:** C1–C7 **frozen** (version pins, consumer sign-offs recorded —
a contract frozen without its consumer's sign-off is not frozen);
R-10 kill-switch drill executed on staging-lab with its record in
`ops/drills/` **or** the retract ADR landed (F1 kill condition — no third
option); R-3 migration design reviewed by Vault; R-1 (a)/(b) designs
reviewed by Oracle+Forge; Q8 options reviewed by Prism+Vault; R-13/Q6
design reviewed; all seven contracts plugged into `tests/contracts/`.
**Reviewed by:** Petu (phase-gate brutal review) + the §1.6 owners;
Tripwire spot-reviews C4/C6.
**Checkpoint falsifier:** a contract still mutable after T+9; no drill
record AND no retract ADR; any design doc without principal sign-off.
**Edges:** Phase 1 lane planning waits on the frozen contracts —
meanwhile lanes pre-write their Definition-of-Ready stubs against the
frozen pins. The R-3 build (Phase 1) waits on Q2 — meanwhile the
migration doc is the complete handoff (D1 verdict: no discovery work
left). The R-1 build waits on Q3 — meanwhile option (b)'s 2-attestor
rule is documented as the deputy-authority floor.

### T+12 — merged reviews + handoff (cumulative: frozen → evidenced)

**Drops:** every lane opens its PR against the planning integration branch
(`program/planning/12h-output` — NOT `program/architecture-revision`;
nothing merges there until Aditya approves the program) with the full
§3.2 PR body (Skill & Evidence block mandatory); lane registries complete
with verification evidence (commit hashes, test outputs, `git ls-remote`
for any push claim — the verify-artifact rule, §5.1); the 12h exit
checklist (§3) run and signed by Petu.
**Reviewed by:** owners + triggered Vault/Tripwire sign-offs; Petu's
artifact-verification spot-checks (principal-on-metal: raw logs, the
running binary, `git ls-remote`).
**Checkpoint falsifier:** any deliverable claimed without artifact
evidence; any PR missing the Skill & Evidence block (returned without
review, §2.3); any Type-1 decision without a decision-log entry in
`docs/decisions/`.
**Edges:** the planning integration branch waits on Aditya's program
approval — meanwhile the PRs are the reviewable, merge-ready handoff
(work never freezes; the artifacts are real whether or not the gate
has fired).

---

## 3. Dependency map — what unblocks what; the critical path

```
T+0 registries + Definition of Ready
  │
  ├─ R-8 / P6 fixes (recv-1, fwd-1) ──► T+6 MERGED ──► metric honesty
  │     (malformed JSON ⇒ unparseable; simulated:true)   for everything downstream
  │
  ├─ C1–C7 drafts (all domains) ──► T+6 reviews addressed ──► T+9 FROZEN
  │     │                                                    (consumer sign-off)
  │     └─► qa-2 skeleton (T+3) ◄── C1, C6 fixtures (T+6) ◄── all seven (T+9)
  │                                                              │
  │                                                              ▼
  │                                              Phase 1 lanes build against
  │                                              frozen pins (no discovery)
  │
  ├─ sender inventory (recv-2) ──► T+6 Vault veto cleared ──► R-3 migration
  │     design (T+9 reviewed) ──► Phase 1 R-3 build (waits Q2 only)
  │
  ├─ R-10 wiring (dev-1) ──► T+6 rehearsal ──► T+9 DRILL or RETRACT (F1)
  │     (kill condition dated; fallback ADR pre-written at T+6)
  │
  ├─ R-1 (a)/(b) designs (gate-1) ──► T+9 Oracle+Forge review ──► Q3 build
  │     waits; option (b) floor documented under deputy authority
  │
  ├─ R-13/Q6 design (race-1) ──► T+9 reviewed ──► stub infra proceeds;
  │     live runs wait Q6; fallback = FakePD-only + quarterly manual check
  │
  ├─ R-16 design (plat-2) ──► T+9 reviewed ──► Phase 1 build (deputy authority)
  │
  ├─ Q8 options (ui-1) + mock-honesty (ui-2) ──► T+9 reviewed ──► wiring
  │     waits Q8; fallback = REMOVE from live build (honesty default)
  │
  └─ threat-model skeleton (evlog-2) ──► T+9 Vault review ──► Phase 5
        R-18 builds on it (written first, built second)
```

**The critical path through the 12 hours** (the chain with zero slack):
T+0 registries → T+3 contract drafts → T+6 review comments addressed →
T+9 contracts frozen with consumer sign-off → T+12 PRs with Skill &
Evidence. **If the T+9 freeze slips, Phase 1 lanes start without
contracts — the slip must be named in writing with a re-sequenced plan,
never absorbed silently** (ROADMAP §9: R-12 is the load-bearing spine of
the program; here the frozen contracts are the load-bearing spine of
the wave).

**The second-hardest chain:** R-10 wiring → T+6 rehearsal → T+9 drill
(F1 kill condition). A failed drill at T+9 fires the retract ADR the
same hour — the fallback is pre-written, not discovered.

**Definition of done per checkpoint** (the falsifier — a checkpoint with
no checkable outcome doesn't exist):
- **T+0:** 10 lane registries exist, each with owner + REQs + contract
  pins + reviewer routing + signed Definition of Ready.
- **T+3:** every contract draft is a machine-checkable artifact
  (schema/enum/fixture), not prose; R-8/P6 branches exist with tests.
- **T+6:** R-8/P6 merged with sign-offs; sender inventory names every
  sender; reviewer comments addressed in writing.
- **T+9:** contracts frozen with consumer sign-offs; drill record in
  `ops/drills/` OR retract ADR landed; design docs carry principal
  sign-off.
- **T+12:** lane PRs open with Skill & Evidence + verification evidence;
  exit checklist signed; nothing claims what the artifacts don't prove.

---

## 4. Pre-T+0 prep lane (proceeds even if approval hasn't landed)

Per the program gate rule, src/ changes wait for T+0. The following
proceed without Aditya's approval — they are designs and drafts, and
they are the reason T+0 starts hot instead of cold:

- Contract drafts C1–C7 (v0.1, machine-checkable where possible).
- R-3/Q2: sender inventory + HMAC migration design doc.
- R-1/Q3: options (a)/(b) design docs + CLI ceremony prototype notes.
- R-13/Q6: drift-harness design + stub-cassette caveat text.
- R-20/Q8: both-options design doc + mock-honesty checklist.
- Threat-model skeleton (R-18), R-16 auth design doc, PR-template draft.
- The qa-2 `tests/contracts/` skeleton.

Anything touching `src/` or the live gate waits for T+0. Doc retractions
(R-10 option (b), `deploy.sh` rewrites) may be drafted as prep but land
only after T+0.

---

## 5. What does NOT fit in 12 hours — named explicitly, sequenced after

Nothing below is dropped; each is sequenced to its phase with the reason
it can't compress into the wave. Revisit only with a written reason and
the evidence that changed (ROADMAP §8).

**Designs proceed, builds wait (the four Type-1 gates):**
- **R-1 build** — kernel refusing unattested generations; D8 fail-closed
  flip. Waits Q3 (Aditya). → Phase 1. Deputy-authority floor (option b,
  2-attestor rule) documented in the 12h.
- **R-3 HMAC enforcement build** — `/v2/enqueue` enforcement. Waits Q2.
  → Phase 1. Migration design is the 12h deliverable (D1).
- **R-13 live drift runs + key provisioning** — team-held PD/Jev test
  keys. Waits Q6. → Phase 4. Fallback: FakePD-only battery + quarterly
  manual live-shape check.
- **R-20 appeal wiring build** — waits Q8. → Phase 5. Fallback: REMOVE
  the control from the live build (honesty default).

**Phase 1 work that is not Phase 0 (needs the 12h's contracts first):**
- **R-2** key-resolution contract build (single resolver, UI loud
  warning) — the contract shape is drafted in C6's orbit, the build is
  Phase 1.
- **R-9 vocabulary ratification** — the enum definitions + wire-spec
  decision. C4 is drafted with the vocabulary pinned
  `pending-R9-ratification`; ratification itself is Phase 1 (D3).
- **R-16 bearer-token build** — design doc in the 12h, build in Phase 1.
- **R-19 full ingress hardening** — slowloris timeouts, 512 KiB cap,
  replay cache. Only the R-8 exception-path fix (two lines) fits the
  12h; the rest is Phase 1.

**Later phases (need instruments the 12h doesn't build):**
- **R-12** `/metrics` + trace ID + first SLO — Phase 2. C5 is drafted
  in the 12h; the shared metrics module is not.
- **R-11 mechanical runner** — Phase 2. The 12h lands the honesty half
  only (PR template, written merge requirement).
- **R-5/R-7/R-8** resilience builds — Phase 3 (need R-12 metrics to
  measure anything; building them blind is theater).
- **R-6 rung 1** — Phase 3 build, shadow only; promotion gated on R-17
  (D4). Not touched in the 12h.
- **R-14/R-17 eval harnesses** — Phase 4. The 12h deliberately drafts
  C2/C3 as `stability: draft` (F2) rather than pretending the
  vocabulary is settled.
- **R-15 load-harness promotion** — Phase 4. C2 numbers stay a report
  until then.
- **R-18 event-log lifecycle** — Phase 5. Threat-model skeleton in the
  12h; chaining, drain-before-roll, genesis unification after.
- **R-4 retention arming** — Phase 5, after dry-run review.
- **Contract conformance tests** (openapi validation, consumer-driven
  expectations) — Phase 4. The 12h wires the skeleton and fixtures.
- **Fault-proxy chaos catalogue** — Phase 4 (Q7 conditional); never
  production, never live PagerDuty without a fresh Aditya decision.
- **Q4 per-service routing map** — NOT built, ever (ROADMAP §8); the
  single-key boundary is declared honestly instead.
- **The §8 deliberately-not-built list** — Kubernetes/Argo, LaunchDarkly,
  k6/Locust adoption, full Pact broker, Stripe-style date-pinned API
  layer, OAuth/OIDC, mTLS for senders, Ed25519 checkpoints, Kafka
  between stages, GraphQL/BFF, production chaos on live PD, preview
  envs per PR, blue-green receiver. None enter the 12h; none enter the
  program without a written reason.

---

## 6. Standing watch items for the wave

- **Andon cord is live** (OPERATING-RULES §0, §5.3): any agent stops any
  lane with a written reason within the hour; 2-hour adjudication; the
  stopper is thanked, never blamed. Stops that never get pulled are a
  red flag.
- **Lane registry discipline:** every dependency flagged the same hour
  it is discovered (§1.1 #4). A lane waiting silently is a §5 violation.
- **Honesty law:** a failed drill is reported as a failed drill with
  its mechanism (F1's retract path exists precisely so failure has
  somewhere honest to go). Simulated results are labeled `simulated:
  true` in-band. Numbers travel with sources or they don't travel.
- **Verify the artifact, never the report** (§5.1): T+12 completion is
  evidenced by commit hashes, test outputs, and `git ls-remote` —
  completion reports are never evidence.
- **Disagree-and-commit binds:** F1–F7 are final for this wave.
  Re-litigation happens in the retrospective, not in hallway
  conversations.

---

*Session B1 adjourned. Disagree-and-commit recorded on F1–F7.
Next: Session B2 — diagrams, user workflows, design expansion.*
