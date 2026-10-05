# R-18: Event-Log Lifecycle Threat Model — Skeleton (Type-1 requirement doc)

**Lane:** evlog-2 (prep wave, pre-T+0) · **Branch:** `lane/prep-race-threat`
**Date:** 2026-10-05 IST · **Status:** skeleton, PRE-T+0 PREP — requirement doc,
not a build. The Phase 5 build (genesis unification, drain-before-roll, verifier
convergence) is **blocked on this doc's approval** — written first, built second.
**Decision type: TYPE 1** — per `PIPELINE-REVISION.md` §3.4 and R-18 ("P1 is TYPE 1").
Source memos: `event-log-audit.md` P1/P3/P4 (M1/M3/M4); `PIPELINE-REVISION.md` §3.4.

---

## 0. What this document is, and the one row that must not be skipped

"Tamper-evident durable truth" today means **external-attacker tamper-evidence**.
Against the writer — the party the customer buys trust from — the guarantee is one
hourly HMAC'd anchor plus customer discipline no artifact asks for; writer forgery
and writer silence are unaddressed; the 2-hour sink-failure bound is in-memory state
a restart resets. This skeleton names the adversaries, what each can do, how each is
detected, the evidence the customer holds, the time bound, and what breaks the
guarantee — per §3.4's requirement.

**The brutal rule for this doc** (OPERATING-RULES §3.4 honesty law): an adversary
you cannot detect is a **finding**, not an omission. §7 lists the undetectable
rows explicitly. The insider-writer row carries a mandatory disagree-and-commit:
**accept the limit in writing, or fund the external-anchoring work that narrows it**
(§3.4). Vault reviews this doc; Tripwire spot-reviews the build when it lands.

Scope of the model: the event log + audit chain lifecycle — DB file, hash chain,
genesis rule, anchors, checkpoint job, the two verifiers, outbox rows, spill files.
Not the gate kernel, not the forwarder wire (those have their own threat surfaces).

## 1. Method

- **Shostack's four questions** drive the structure: *What are we building? What
  can go wrong? What are we going to do about it? Did we do a good enough job?*
  (source [1]). Each §3 row answers all four in order; a row missing an answer is
  an open finding, not an editing gap.
- **STRIDE as the lens, before code** (source [3]): Spoofing / Tampering /
  Repudiation / Information disclosure / Denial of service / Elevation of privilege
  is the coverage discipline — §4 maps the boundary's threats through it. STRIDE is
  a *design-stage* process (source [3]); this doc is exactly that stage.
- **Derived, not workshopped** (source [2]): every threat traces to a line of
  evidence that already exists in the estate (code path, memo finding, prior
  incident). The useful artifact is the **diff between runs** — this skeleton is
  version 0 of a living file, re-opened on every architecture change.
- **Threat modeling is continuous, not a launch gate** (source [1]): this doc
  gets a review trigger on: any change to the chain format, genesis rule, anchor
  cadence, checkpoint job, or roll procedure. No trigger ⇒ the model silently
  rots; rotten models are how M1 stayed unaddressed.

## 2. Assets and trust boundaries (brief — the skeleton's minimum)

**Assets:** (a) the hash-chained event log (the durable truth); (b) hourly HMAC
anchors; (c) the genesis block/prev-hash rule; (d) queued outbox rows (I1/I2
delivery guarantees); (e) the checkpoint job's sink writes; (f) spill files
(`direct-degraded` emergency records).

**Trust boundaries crossed:** (1) writer process → log file at rest (the writer
is trusted by the filesystem, untrusted by this model — §3's core move);
(2) roll/segment boundary (chain-of-segments, D14 retention RFC 2026-10-05);
(3) the two verifiers (customer-side and internal — they *disagree* on genesis,
R-18 defect (b)); (4) anchor sink (wherever the hourly HMAC lands); (5) process
restart (volatile state, including the 2-hour sink-failure bound, dies here).

**What "tamper-evident" currently covers** (honest baseline): detection of
*post-hoc* modification of a sealed log by someone who does not control the
writer — via hash-chain breakage and anchor mismatch. **What it does not cover:**
anything in §7.

## 3. Adversary × detection × time-bound × evidence

Reading key: *Can do* = the adversary's capability inside the boundary.
*Detection* = the mechanism that catches it, with its owner. *Time bound* =
worst-case detection latency, stated honestly (a bound we can't state is marked
`unstated — finding`). *Evidence* = what the customer independently holds.
*Guarantee-breaker* = what makes even the stated detection fail.

### A1. Post-hoc file editor
*The classic external attacker: edits the sqlite/DB file at rest, offline, without
the writer's cooperation.*

| | |
|---|---|
| **Can do** | Rewrite, delete, or reorder sealed rows; truncate the tail. |
| **Detection** | Hash-chain verification breaks at the first edited row (chain is content-linked); hourly HMAC anchor mismatch if the edit postdates the last anchor. Owner: the verifier run (customer-side + internal). |
| **Time bound** | ≤ anchor cadence (1 h) for edits that change anchored content; ≤ next verifier run for unanchored tail edits. Bound is *stated and real*. |
| **Evidence** | Customer holds the anchor HMACs (out-of-band) and the verifier binary/report. |
| **Guarantee-breaker** | Attacker edits rows *and* recomputes the chain *and* compromises the anchor store — then the chain is self-consistent and the anchor lies. Anchors are only as trustworthy as their sink. |
| **Mitigation status** | Covered today. This is the one adversary "tamper-evident" was built for. |

### A2. Compromised writer (privilege-level attacker)
*Attacker holds the writer process's privileges (RCE, stolen deploy credentials,
malicious insider with shell). Writes through the legitimate writer.*

| | |
|---|---|
| **Can do** | Emit well-formed but false events (phantom `forward_confirmed` for pages never sent; suppressed `forward_failed`); forge a self-consistent chain forward from the compromise point — the chain cannot distinguish "true" from "well-formed." |
| **Detection** | Chain verification does **not** detect this (the chain is intact — the lies are grammatical). Detection is behavioral only: cross-check against independent signals (vendor-side PD incident list vs `forward_confirmed`; outbox I2 receipts vs forwarder receipts; customer-side anomaly review of event *content*). |
| **Time bound** | `Unstated — finding.` Bounded only by the cadence of independent cross-checks, which do not exist as a scheduled practice today. |
| **Evidence** | The customer holds: the anchor stream (proves *continuity*, not *truth*), the vendor's own incident records (PD-side), their monitoring. |
| **Guarantee-breaker** | N/A — the guarantee ("the log is truthful") does not exist for this adversary. §7 finding #1. |
| **Mitigation status** | **Not covered.** This is the "tamper-evident is external-attacker-only" defect (§3.4). Mitigations are Phase 5 options, not facts: dual-control event emission for high-stakes dispositions, vendor-side reconciliation job (PD incidents vs log), external anchoring (see A7). |

### A3. Silent writer (omission attacker)
*Attacker — or a failing component — stops the writer emitting events: gaps with no
alarm, or events emitted to a sink nobody reads.*

| | |
|---|---|
| **Can do** | Suppress `forward_failed` (hide delivery failures); suppress anchor writes (hide the gap's extent); let the checkpoint job write to a dead sink while reporting success. |
| **Detection** | Anchor *absence*: a missing hourly anchor is itself the signal — but only if someone watches for absence. Today: the 2-hour sink-failure bound exists **in memory** (event-log-audit.md M1) and a restart resets it (§3.4) — so the absence detector has no durable memory. Sequence-gap detection on the chain helps only if the writer was *supposed* to emit (heartbeats make silence visible; without a heartbeat contract, silence is indistinguishable from quiet). |
| **Time bound** | Nominally 2 h (sink-failure bound) — **restartable to ∞ by A4**. Honest statement: the bound is a hope with a counter, not a guarantee. |
| **Evidence** | Customer holds: the anchor cadence record (absence is visible *if* they keep their own cadence log); nothing else. |
| **Guarantee-breaker** | A4 (restart resets the bound); a writer that emits plausible-but-empty heartbeats (silence disguised as quiet). |
| **Mitigation status** | **Partially covered; the coverage is restartable.** Phase 5 must make the sink-failure bound durable (persist the failure state, not the timer) and define the heartbeat contract that makes silence an event. |

### A4. Bound-resetter (restarter)
*A named variant of A3 (§3.4 calls it out explicitly): anyone who can restart the
writer process — operator, deploy system, crash — resets the 2-hour sink-failure
bound.*

| | |
|---|---|
| **Can do** | Wipe all in-memory failure state: sink-failure timers, watchdog arming, half-open breaker state. Each restart buys a fresh 2 hours of un-alarmed sink failure. |
| **Detection** | Restart is itself logged (process start event) — detectable *if* restart frequency is monitored. It is not, today. |
| **Time bound** | Unstated — finding. A slow drip of restarts (one per 119 minutes) defeats the bound indefinitely with no alarm. |
| **Evidence** | Customer holds the process-start events in the log (the log records its own restarts — self-referential, but the *count* is checkable). |
| **Guarantee-breaker** | Restarts that don't emit start events (kill -9 during the start path; log rotation swallowing the start line). |
| **Mitigation status** | **Not covered.** This is why §3.4 calls the bound "restartable memory." Phase 5: durable failure state (the bound lives in the log, not the heap). |

### A5. Malicious-or-buggy deploy (segment-roll adversary)
*The roll procedure / a bad deploy interacts with the chain-of-segments lifecycle.
Not necessarily adversarial intent — a buggy roll is the more likely realization —
but the threat model treats them identically.*

| | |
|---|---|
| **Can do** | (a) Roll that entombs live outbox rows — the chain stays continuous while I1/I2 delivery guarantees are silently dropped (R-18 defect (c), "Chesterton's fence in reverse"). (b) Genesis-rule change that makes the two verifiers disagree — the verifiers cry wolf on every deployment the day after the first roll (R-18 defect (b)), training everyone to ignore them (the anti-theater decay). |
| **Detection** | (a) Outbox-row survival check across the roll (queued page survives, or the roll refuses and pages — R-18's stated verify). (b) The `/tmp/evlog-repro` reproduction becomes a regression test both verifiers pass (R-18's stated verify). |
| **Time bound** | ≤ one roll cycle for (a) if the survival check gates the roll; immediate (CI) for (b) once the repro is a test. Both bounds are *post-Phase-5*; today the time bound is "when a human notices." |
| **Evidence** | Customer holds: verifier reports across deploys (the cry-wolf pattern is visible in the report history). |
| **Guarantee-breaker** | A roll that passes its own checks (checks written by the roll's author — the fox guards the henhouse). Mitigation: the survival check is owned by Ledger (event-log lane), not the deploy lane — separation of check authorship. |
| **Mitigation status** | **Known defects, fix designed (R-18 P3/P4), not yet built.** Genesis unification + drain-before-roll are the Phase 5 build items this doc gates. |

### A6. Curious-but-honest operator
*Reads the log for secrets/PII out of curiosity or during debugging — no intent to
alter.*

| | |
|---|---|
| **Can do** | Read everything the log stores: alert payloads, routing metadata, error strings. |
| **Detection** | Read-access auditing on the log store (filesystem/OS-level), if enabled. Not a log-chain property. |
| **Time bound** | Unstated — depends on access-audit retention, outside this model's scope. |
| **Evidence** | Customer holds their own access logs. |
| **Guarantee-breaker** | N/A — this adversary doesn't attack integrity. |
| **Mitigation status** | **Covered by design:** payloads are stored keyless on disk (the forwarder memo's `payload_frozen` decision — routing keys live in wire bytes, never in the log); `sanitize_error` is the one wired emission boundary stripping key values from error strings. The log is *designed* to be safe to read. Residual: custom_details may carry customer PII — a data-classification question for the customer, recorded as accepted. |

### A7. The insider-writer with legitimate access — THE FINDING
*The §3.4 disagree-and-commit row. A party with legitimate writer access (a
compromised-but-authorized service account, a disgruntled operator with deploy
rights, a supply-chain backdoor in the writer binary) emits false-but-well-formed
events and valid anchors over them.*

| | |
|---|---|
| **Can do** | Everything A2 can do, plus valid anchors — the anchor stream, the customer's independent evidence, attests to a lie. |
| **Detection** | **None within the current architecture.** The chain verifies; the anchors verify; the verifiers agree; the log is a perfectly attested falsehood. Detection requires *external* anchoring (anchor hashes committed to a system the writer cannot rewrite — e.g., an append-only external log, a transparency-log-style Merkle tree with independent witnesses, or customer-held anchor receipts countersigned at write time) — none of which exists. |
| **Time bound** | `Unstated — finding. There is no bound; there is no detector.` |
| **Evidence** | The customer holds anchors to a lie — which is worse than holding nothing, because it manufactures false confidence. **This must be stated to the customer, not discovered by them.** |
| **Guarantee-breaker** | The guarantee itself: "tamper-evident" as marketed does not survive this row. |
| **Mitigation status** | **Not covered. This is the Type-1 decision:** (a) accept the limit *in writing* — the threat-model doc records that tamper-evidence is external-attacker-only and the customer is told exactly that; or (b) fund the external-anchoring work that narrows it (append-only external anchor sink, witness cosigning). Vault owns the recommendation; Aditya owns the call (Type 1 — trust root). |

## 4. STRIDE mapping of the boundary

The coverage lens (source [3]). Each category's instantiation on the event-log
lifecycle; rows marked **FINDING** have no adequate mitigation today.

| STRIDE | Instantiation | Detection / bound | Status |
|---|---|---|---|
| **S**poofing | A process impersonating the legitimate writer (stolen socket creds, confused-deputy via the forwarder's private control-plane call — forwarder-byok.md §2.6) | Writer identity is ambient (same user, same box) — no authentication of *which* component wrote a row | **FINDING** — row authorship is unattributed; a forged event is indistinguishable from a real one at the row level |
| **T**ampering | Post-hoc file edit (A1); writer forgery (A2/A7); roll entombment (A5a) | Chain+anchor for A1 (≤1 h); none for A2/A7 | A1 covered; A2/A7 **FINDING** |
| **R**epudiation | "That `forward_confirmed` was never emitted by us" / "the anchor was never received" | The log is the non-repudiation record — but only against A1-class attackers; against the writer itself the log is the *repudiator's* instrument | **FINDING** for writer-originated repudiation |
| **I**nformation disclosure | Operator reads secrets from the log (A6) | Keyless-on-disk design + `sanitize_error` boundary | Covered by design (residual: customer PII in custom_details — accepted, recorded) |
| **D**enial of service | Silent writer (A3); log-fill / disk exhaustion as an integrity attack (a full disk stops the writer — silence by resource); verifier cry-wolf (A5b) training ignore-the-alarm | 2 h sink bound (restartable — A4); diskguard exists (test_diskguard.py) but its *alarm* path vs *silent* path needs the heartbeat contract | Partial; A4 **FINDING** |
| **E**levation of privilege | A component with read access gaining writer access (the forwarder's cross-lane private-method call is ambient trust — forwarder-byok.md §2.6); checkpoint job's sink credentials | No privilege separation between writer-adjacent components today | **FINDING** — the writer's trust domain includes everything on the box |

## 5. What this skeleton deliberately does NOT do (scope honesty)

- **No DFD-level per-element STRIDE walk.** A full data-flow-diagram threat model
  (every flow, every store, every trust boundary with per-element threats) is the
  Phase 5 deliverable *after* the lifecycle redesign — drawing it against the
  pre-revision architecture would model a system we're about to change. The
  skeleton names the adversaries and the contract; the detailed model follows the
  build, and the diff between this skeleton and that model is itself a review
  artifact (source [2]: the useful artifact is the diff).
- **No CVSS-style scoring.** Severity theater on a skeleton produces false
  precision; the Type-1 decision (A7) is binary and doesn't need a number.
  Forced probabilities arrive with the Phase 5 review (principal-mindset §7).
- **No mitigations specified for FINDING rows.** Specifying mitigations is the
  Phase 5 design's job, gated on this doc. Prescribing them here would pre-decide
  the build the doc is supposed to gate.

## 6. Rejected alternatives

| Alternative | Rejected because |
|---|---|
| Model only the external attacker (the current implicit model) | That is the defect §3.4 names. A threat model that excludes the writer is a marketing document, not a security document. The A2/A7 rows are the reason this doc exists. |
| Trust the hourly HMAC anchors as sufficient | Anchors attest to *continuity*, not *truth* — a compromised writer anchors its own lies (A7). Conflating the two is the precise error the customer must never be led into. |
| Handle insider-writer via "we'll notice anomalies" | Unscheduled anomaly-noticing is not detection; it has no time bound, no owner, no evidence. "Someone would notice" is hope, not a strategy (principal-systems: eternal friction). |
| Defer the threat model until after the Phase 5 build | Inverts the Type-1 order: §3.4 requires the doc *before* the build because the build's shape (genesis unification choices, drain-before-roll semantics, whether external anchoring is funded) depends on which rows we accept vs mitigate. Building first would concrete the wrong trust assumptions. |
| A workshop-only model (slides, no versioned file) | Source [2]: the derived, versioned model is cheap to regenerate and the diff is the artifact; source [1]: models must be continuous with the SDLC. A slide deck from one meeting is how M1 stayed unaddressed — no trigger, no owner, no diff. |

## 7. Findings register (the undetectable adversaries, on record)

These are not gaps in this document — they are gaps in the architecture that this
document exists to record. Each is a Phase 5 input and a customer-communication
obligation.

1. **F-1 (A2): Compromised writer forgery is undetectable by the chain.**
   Time bound: unstated. Customer evidence: anchors-to-continuity + vendor-side
   records only. Phase 5 input: vendor-reconciliation job; dual-control emission.
2. **F-2 (A4): The 2-hour sink-failure bound is restartable to infinity.**
   Time bound: unstated (nominally 2 h, actually unbounded). Phase 5 input:
   durable failure state in the log, not the heap.
3. **F-3 (A7): The legitimate insider-writer anchors its own lies.**
   Time bound: none. This is the Type-1 disagree-and-commit row: accept in
   writing or fund external anchoring. Vault recommends; Aditya decides.
4. **F-4 (S-row): Row authorship is unattributed.** No component-level writer
   identity; a forged row is indistinguishable from a real one. Phase 5 input:
   per-component row attribution (signed or MAC'd row origin).
5. **F-5 (E-row): No privilege separation around the writer.** Everything on the
   box is in the writer's trust domain. Phase 5 input: threat-model the deploy
   and operations plane as part of the lifecycle work, not as a separate "ops"
   concern.

**Customer-communication rule** (honesty law): until F-1/F-3 are mitigated or
formally accepted, every customer-facing statement of the tamper-evidence
property must carry the qualifier **"against post-hoc modification by parties
without writer access"** — the precise scope, not the slogan.

## 8. Review and lifecycle requirements

- **Reviewers:** Vault (security principal) — required sign-off; Tripwire —
  adversarial spot on the Phase 5 build against this doc (any build claim of a
  security property without a §3 row is rejected per OPERATING-RULES §4.2).
- **Reopen triggers:** any change to chain format, genesis rule, anchor cadence
  or sink, checkpoint job, roll procedure, or writer trust domain. The skeleton's
  version increments; the diff is reviewed.
- **Anti-theater check** (principal-mindset §11): if the Phase 5 build closes
  zero FINDING rows, this doc failed — the review must say so on record, and the
  build does not proceed on "we'll address it later."
- **Pre-mortem (top 3), assumed Oct 2027:**
  1. *The verifiers cried wolf.* Genesis unification shipped subtly wrong; both
     verifiers disagreed after the second roll; on-call muted the verifier
     channel; six months later a real A1-class edit went unnoticed for 41 days.
     Mitigation: the `/tmp/evlog-repro` regression test gates every roll; the
     cry-wolf pattern is itself an alarm (verifier disagreement ⇒ page, never mute).
  2. *The anchor sink was the attacker.* A3-class adversary compromised the anchor
     sink (not the log); anchors verified against attacker-held values; the
     "tamper-evident" property attested to edited history for a quarter.
     Mitigation: anchor-sink integrity is in the Phase 5 scope — the sink is a
     trust boundary (§2), not an assumption.
  3. *F-3 was accepted silently.* The disagree-and-commit happened in a hallway;
     the customer was never told the scope qualifier; a disputed incident's audit
     trail was challenged and the company could not defend the "tamper-evident"
     claim. Mitigation: the acceptance is *in this file*, versioned, and the
     customer-facing qualifier (§7) is a docs/contract change, not a verbal
     understanding.

## 9. Traceability

**External sources (web research, accessed 2026-10-05):**

1. Shostack's four threat-modeling questions + continuous-over-launch-gate
   practice + "start practicing now" discipline:
   https://github.com/binaryphile/binaryphile.github.io/blob/HEAD/_posts/2026-05-10-shostack-threat-modeling-guide.md
   (threat-modeling guide post, updated 10 days before access; also
   https://thinkcloudly.com/blog/threat-modeling-methodology/ for the OWASP
   continuous-activity framing). Changed this design: §1's four-question
   structure and §8's reopen triggers come from here — the skeleton is built
   to be re-run, not filed.
2. Derived threat models — every threat traces to existing evidence; the
   versioned data file, not slides; the diff between runs is the artifact:
   https://github.com/spbreed/cyber-commons/blob/HEAD/skills/appsec/threat-model-stride/SKILL.md
   (STRIDE skill, crawled ~17 days before access). Changed this design: §5's
   scope honesty (skeleton now, DFD-level model after the build, diff as
   review artifact) and the evidence-citation discipline in §3.
3. STRIDE as a *design-stage* process vs OWASP Top 10 as a *built-system* list
   (use STRIDE before code; they answer different questions):
   https://dumpsgate.com/stride-threat-model/ (updated ~89 days before access).
   Changed this design: §4's STRIDE table is explicitly a coverage lens for the
   requirement doc, not a code-review checklist — the OWASP-shaped verification
   belongs to the Phase 5 build review.

**Skill clauses that bind (OPERATING-RULES §2.1):**

- *principal-systems, Type 1 vs Type 2 (operating rule 3):* the A7
  insider-writer row is irreversible (trust root, customer-facing guarantee) —
  it gets RFC-grade rigor, a named decider (Aditya), and a written
  disagree-and-commit. Unlabeled it would default to Type-2 process — exactly
  how Type-1 disasters happen.
- *principal-governance, written decisions before code:* this doc *is* the
  written decision preceding the Phase 5 build; the build is blocked on its
  approval. "No major component starts from a feeling" — the threat model is
  the feeling-killer.
- *principal-governance, unforgiving API design / state isolation:* the F-4/F-5
  findings (unattributed rows, no privilege separation) are API-contract
  problems — row authorship and writer trust domain are contracts, and today
  they are handshake governance (cf. forwarder-byok.md §2.6).
- *principal-mindset §2 (anti-self-deception, move 1 — don't see the answer):*
  the blind-analysis analogue here is modeling the writer as untrusted *before*
  designing the mitigations — the current architecture was designed with the
  writer trusted, which is why §3.4's defects exist.
- *principal-mindset §11 (anti-theater audit):* the cry-wolf verifier (R-18
  defect (b)) is the worked example of a security practice that stopped
  biting; §8's anti-theater check (a Phase 5 build closing zero FINDING rows
  is a failed review) is the audit biting back.
- *principal-systems, Chesterton's fence (in reverse):* R-18 defect (c) — the
  roll entombs live outbox rows while the chain stays continuous. The fence
  here is the I1/I2 guarantee the chain exists to support; the roll procedure
  was "improved" without understanding what it was protecting.

**Grounding tools run:** `git ls-remote origin program/architecture-revision`
(→ `f5469f2`); code checks — `GENESIS_PREV_HASH` constructor param at
`src/sentinel/eventlog.py:382` (the genesis rule both verifiers must agree on);
`FakePD` confined to `tests/test_durable_forwarder.py:136` (forwarder-side
context for the A2 vendor-reconciliation note).

---
*Pre-T+0 prep artifact. Nothing here touches `src/`, tests, or the live gate.
Type-1 requirement doc — Phase 5 build is blocked on its approval.
Expected reviewers: Vault (threat model — required sign-off), Forge (lifecycle
mechanics of the Phase 5 build this doc gates).*
