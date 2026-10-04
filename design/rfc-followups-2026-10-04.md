# RFC follow-ups — external proposal adjudication

**Status:** PROPOSED (design doc, not code; nothing here is built until a lane builds it)
**Lane:** L2 follow-up — architecture RFCs from the external audit · **Date:** 2026-10-04
**Parent docs:**
`research/external/page-or-suppress-redesign-proposal-2026-10-04.md` (the proposal),
`design/architecture-advancement-rfc.md` (L2's RFC, PROPOSED),
`docs/adr-decisions-2026-10-03.md` (ADR-007, ADR-022, ADR-024),
`INTERFACE_PRINCIPLES.md` §2–§4 (S3 threshold simulator, honesty UI, freshness contract —
context only; the interface is Prism's domain).
**Binding decisions this implements:** B3 ADAPT (policy lifecycle), B4 ADAPT (hold
terminology, no code), C1 INVESTIGATE (stepped fail-open, Type 1, no snap calls),
C2 INVESTIGATE (scale assumption validation). Per Petu's adjudication: ADOPT =
envelope/facets/unmapped-bag + render-by-shape + suppression-regret/page-precision
metrics + per-source heartbeat + stable rows/live-tail + UTC+local; those are not
re-litigated here.

**Type convention (principal-systems):** *Type 1* = irreversible or very costly to
reverse — demands RFC rigor, slow commitment. *Type 2* = reversible — decide fast,
roll back if wrong. Every item labels its pieces; a label without justification is
a Type-2 default waiting to become a Type-1 disaster.

---

## B3 — Policy lifecycle (ADAPT)

### B3.0 Decision

**Extend ADR-022's threshold-governance shape (two-person rule, 30-day clock,
shadow-diff canary, suppression-rate SLO watchdog, dead-man's heartbeat) into a
full policy lifecycle for ALL safety-affecting policies — not just numeric
thresholds.** "Policy" here means anything that can change a disposition:
threshold sets, suppression/allowlist predicates, guardrail definitions, and
config-bundle policy sections. The lifecycle is `draft → shadow → canary → live →
review-due → expired`, with a server-side blast-radius preview gating every
forward transition.

**Why this strengthens ADR-022 rather than duplicating it:** ADR-022's ratification
conditions are PARTIAL — canary-as-shadow-diff, the watchdog, and auto-revert are
all *absent* (`docs/adr-decisions-2026-10-03.md`, ADR-022 status). The lifecycle is
the machinery those conditions ride on: the shadow-diff *is* the
shadow→canary evidence, the watchdog *is* the review-due→expired enforcer, and
auto-revert *is* the expired-state semantics. Without the lifecycle, ADR-022's
conditions are three disconnected features; with it, they're one auditable
pipeline.

**Type:** Type 1 — the lifecycle, the state machine, the preview semantics, and the
sign-off matrix are safety-policy governance. Extends ADR-022's Type-1 decision.
Day counts and window sizes are Type 2 (labeled in §B3.6).

### B3.1 The state machine

```
draft ──(edit)──▶ draft
  │
  │ submit for shadow (author)
  ▼
shadow ──(shadow-diff SIGNED by 2 attestors)──▶ canary
  │                                              │
  │ abort (author)                               │ abort (attestor)
  ▼                                              ▼
draft                                         draft
                                                 │
              canary: bounded-scope evaluation   │ promote (2 attestors +
              on production traffic              │ watchdog baseline signed)
                                                 ▼
                                               live ──(edit)──▶ draft (new version; live version frozen)
                                                 │
                          clock expiry (automatic)│
                                                 ▼
                                           review-due ──(re-review: 2 attestors + fresh evidence)──▶ live
                                                 │
                              grace end (automatic)│ or early-expire (attestor)
                                                 ▼
                                              expired ──(re-draft: author)──▶ draft (new version)
```

Definitions (what each state MEANS, not just what it's called):

- **draft** — content being written. Touches nothing. Zero preview required to
  enter; a preview may be computed for author feedback but it is advisory.
- **shadow** — the policy runs against live traffic in parallel; dispositions are
  logged as `shadow_decision` events; it cannot page, cannot suppress, cannot
  touch the paging path (the shadow pipeline's read-only invariant from
  `src/sentinel/shadow.py` extends here: the ShadowPipeline holds no reference to
  any vendor-write code path, enforced by the static import-graph guard).
- **canary** — the shadow-diff evaluation state. Per ADR-022's Pager position,
  canary is **shadow-diff, not live 5% splitting**: the proposed policy runs in
  shadow for a bounded scope and bounded duration on production traffic, the
  disposition diff is computed, signed, and reviewed. "Scope" is bounded by
  named services or a percentage of matching traffic in shadow — never live
  traffic splitting. A bounded *live* canary exists only under the emergency
  override path (§B3.4).
- **live** — fully effective. Content frozen: any edit creates a new version at
  draft; the live version is immutable (auditability, DR-26's mirror-drift
  prohibition applied to policies).
- **review-due** — still effective, but under enforcement (§B3.5). Entered
  automatically at the policy's review date (the ADR-022 30-day clock, generalized:
  every policy carries `review_at`; thresholds default 30 days, suppression
  policies default 14 days — defaults are Type 2).
- **expired** — inactive. Flag flip, reversible only via re-draft (never
  expired→live). Dispositions fall through to the next policy / fail-open
  semantics — expired never silently drops: a tenant with zero live policies gets
  the fail-open guarantee, loudly (violet system-channel banner naming the gap).

### B3.2 The blast-radius preview (server-side, before suppression-granting transitions)

**Scope (reconciled):** the preview is REQUIRED before **shadow→canary** and
**canary→live** — the two transitions that grant suppression power.
**draft→shadow requires no preview** (nothing is suppressed in shadow; the
preview may still be computed as author advisory). The section title's
"every forward transition" is corrected by this scope note.

Computed by the platform tier (never the console — per INTERFACE_PRINCIPLES §4.5,
the interface reads and never evaluates; the preview is an engine-side
computation exposed as a read). The preview is a **contracted computation**, not a
UI nicety: its definition is Type 1 because operators will rely on the numbers
in incident reviews. Exact window W is Type 2 (default 7 days of decision-event
history).

For a proposed policy version V′ against window W, the preview MUST compute:

1. **Match volume:** events in W that V′ would match, as count + share of total
   event volume in W. (Answers: "how much of my estate does this touch?")
2. **Disposition flips:** for each ordered pair (old, new) ∈ {page_now,
   page_business_hours, suppress, passthrough}², the count of events whose
   disposition changes, with the (suppress→page) and (page_now→suppress) cells
   called out as the danger cells. Reported as counts AND per-hour rates.
3. **Page-rate delta:** projected pages/hour gained or lost. This is the
   proposal's central arithmetic made concrete: at the validated tier (see §C2),
   even a 0.1% flip on 1,000 events/min is 1 page/min — the preview must show
   the number in pages-per-hour, not just percent, because humans budget sleep
   in pages, not percents.
4. **Guardrail-class touch:** any event matched that falls in a guardrail-
   protected class (ADR: guardrails precede policies). A nonzero count here is a
   **hard block** — the transition cannot proceed past shadow without the
   emergency override path (§B3.4). Not a warning. A block.
5. **Regret estimate:** for suppressions V′ would add, the suppression-regret of
   historically similar suppressions (join rate of past suppressions to later
   incidents, per the proposal's §8.5 metric — ADOPTED by Petu's adjudication).
   Labeled as an estimate with its window and sample size (honesty UI §4.1:
   every number says what it is).
6. **Budget impact:** projected consumption of the tenant's page budget
   (proposal §8.5) as headroom-remaining, not just delta.

The preview output is **signed and stored** as a `policy_preview` event in the
event log (hash-chained, per ADR-011), so a postmortem can ask "what did the
preview say at promotion time" and get an answer, not a shrug.

### B3.3 Sign-off matrix (ties to the ADR-022 two-person rule)

The primitive is ADR-022's `ThresholdAttestation`, generalized to
`PolicyAttestation {policy_id, version, from_state, to_state, preview_hash,
attestor_ids[2], decided_at}`. Same attestor identity machinery as ADR-022
(the attestor on/offboarding question in L2 RFC §14.1 is now load-bearing for
this section too — flagged, not solved here).

| Transition | Required signatures | Type |
|---|---|---|
| draft → shadow | author only | 2 — reversible, no traffic effect |
| shadow → canary | **2 attestors** + signed shadow-diff referencing the preview hash | 1 — the fatigue ratchet's first gate |
| canary → live | **2 attestors** + preview hash re-verified (the shadow-diff's preview; recomputed if the version changed since) + suppression-rate watchdog baseline signed (the baseline the watchdog will alarm against) | 1 — this is the moment a suppression policy starts hiding alerts |
| live → review-due | none — automatic at `review_at` | 1 (the automaticity is the design; no human may postpone it) |
| review-due → live (re-review) | **2 attestors** + fresh preview over the trailing window | 1 |
| review-due → expired | automatic at grace end; attestor may expire early | 2 for early-expire (it's a safe direction) |
| expired → draft | author only (creates a NEW version; the expired version is immutable) | 2 |
| draft/shadow/canary → draft (abort) | author or attestor | 2 — abandoning a non-live version is always the safe direction |
| live → expired (abort = early-expire) | attestor | 2 — aborting a live policy RETIRES it via early-expire; it never forks back to draft (the live version is immutable, so "abort to draft" would be a lie — there is no live→expired early-kill missing: this row IS it) |

Rules: the two attestors must be distinct from the author and from each other
(ADR-022's "what would change it" anticipates rubber-stamping: if >90% of
attestations are same-hour approvals by the same pair, the control rotates to
mandatory cooling-off — named here as the anti-rubber-stamp tripwire).
Emergency override (§B3.4) is the only path that shortens this matrix.

### B3.4 Emergency override (the escape hatch, and its price)

A named approver may move draft → live directly (skipping shadow/canary) when
paging-path safety demands it (e.g., a suppression rule that is actively hiding
a SEV pattern must be killed NOW). The price, enforced not suggested:

1. Time-boxed: the override expires in ≤24h (Type 2 number; the time-box itself
   is Type 1), auto-reverting to the previous version.
2. Event-logged with the approver's identity, the reason, and the preview
   computed anyway (the preview runs even when nobody waits for it — the
   postmortem needs it).
3. A post-override review is mandatory within the time-box: 2 attestors
   retro-sign or the policy goes to expired. An override without retro-sign
   pages the platform owner (the regress terminates at a paged human, ADR-022
   Tripwire position).

### B3.5 What review-due and expired ENFORCE (not just display)

This is the section the proposal's §8.3 leaves as prose. Every bullet below is a
machine behavior:

**review-due enforces:**
- (a) **Content freeze.** The policy version is immutable; any edit creates a new
  version at draft. This kills the failure mode "quietly widening the suppression
  rule while nobody's looking" — the exact ratchet the proposal's evidence
  (§1.1: 44% of orgs with suppression-caused outages) warns about.
- (b) **Watchdog tightening.** The suppression-rate SLO watchdog drops from
  SLO-band to alarm-on-any-drift for this policy. A review-due suppression
  policy that changes its suppression rate by >2σ from baseline pages the owner.
- (c) **No extensions without re-review.** `review_at` cannot be bumped; the only
  forward transition is re-review (2 attestors) or expiry. "Snoozing the review"
  is not a state in the machine.
- (d) **Ritual visibility.** The policy is named in the weekly shadow report /
  mute-review ritual (ADR-007's ritual, extended) with owner, days-overdue, and
  current suppression volume.
- (e) **Demotion from recommendations.** The shadow report and policy editor may
  not recommend this policy as a template or a "draft from this evidence"
  source until re-reviewed.

**expired enforces:**
- (a) **Flag-flip to inactive** — a single audited `policy_expired` event; the
  revert is instant, no deploy (principal-governance: rollback measured in
  seconds).
- (b) **Fail-loud, never fail-silent.** If expiry leaves a tenant with no live
  policy covering a traffic class, dispositions fall through to the fail-open
  default AND the console raises a violet system-channel banner ("no live
  policy for class X since T — dispositions are fail-open passthrough"). The
  banner is per INTERFACE_PRINCIPLES §4.1 degraded-state treatment.
- (c) **No expired→live transition exists.** Re-activation requires re-draft as
  a new version with a fresh preview. The shortcut "just turn it back on" is
  the failure mode being removed.

### B3.6 Type labels and kill conditions

- **Type 1:** the six-state machine, the preview computation contract (§B3.2
  items 1–6), the sign-off matrix (§B3.3), the automaticity of live→review-due
  and review-due→expired, the content-freeze, the no-expired→live rule, the
  emergency-override price. Rationale: these are the promises operators and
  auditors will rely on; changing them after the first design partner is a
  contract change.
- **Type 2:** review periods (30d thresholds / 14d suppressions defaults),
  grace length (default 14d), shadow window (default 7d), preview window W
  (default 7d), the 2σ watchdog-tightening number, the ≤24h override box.
  Rationale: all tunable from production data; none changes the safety shape.

**Kill conditions (monkey-first — the hardest part is that the lifecycle adds
process latency; if the process is theater, kill it):**
- K1: if a design-partner pilot shows the full lifecycle adds >1 business day
  to routine policy changes while postmortems show the preview was never
  consulted before promotion, collapse to four states (draft → shadow → live →
  expired) and keep only the preview + two-person gate. A lifecycle nobody
  reads is process theater (execution-doctrine: proxy that moves while the
  true objective doesn't).
- K2: if attestation telemetry shows >90% same-hour rubber-stamp approvals,
  the two-person control is not a control — rotate to mandatory cooling-off
  (attestations ≥4h apart) before adding more states.
- K3: if the shadow-diff computation cannot be made reproducible (same traffic
  + same policy version ⇒ same diff) within one quarter, the shadow→canary
  gate is not evidence-based — hold all promotions at shadow until it is.

---

## C1 — Stepped fail-open RFC (INVESTIGATE, Type 1)

### C1.0 The question

Our cleanest safety semantic is ADR-010's: **dead/slow vendor ⇒ passthrough —
page the human.** The proposal (§8.4) accepts the direction but rejects the
naivety: at high alert volume, page-everything fail-open IS a page storm, and
fail-open becomes the incident. It proposes stepped degradation:

1. Last-known-good compiled policy,
2. Static severity floor (page critical only) with dedupe,
3. Rate-capped paging + digest for the remainder.

This RFC investigates whether we adopt the steps. It does not pre-decide: the
sections below are the decision mechanism.

### C1.1 Type justification — Type 1

Failure semantics are the product's moral core (ARCHITECTURE.md §1 principles:
"fail-open", "uncertainty pages"). The moment a customer signs a contract, "what
happens when the vendor is down" is a promise, not an implementation detail.
Changing it later is a contract renegotiation, and getting it wrong at 3 AM is
irreversible for that incident. The *step definitions* (what each step does,
what triggers transitions, what the UI shows) are Type 1. The *trigger
thresholds* (how slow is "degraded", what is the rate cap number) are Type 2 —
tunable from the timer-win watchdog's production data.

### C1.2 Global-vs-local analysis

**The local optimization trap:** tuning the rate cap in step 3, or arguing about
whether the severity floor should be "critical only" vs "critical+major". Those
are Type-2 numbers that a principal should not spend Type-1 energy on.

**The global question (five whys):** *Why* does fail-open page-storm? Because
passthrough preserves the *alert* rate into the *page* rate, and the alert rate
during a vendor outage is the storm rate (storms and vendor degradation
correlate — the vendor is slowest exactly when traffic is highest). The step
proposal's real insight is not "three steps" but this: **during failure, the
paging path must shed *fidelity* before it sheds *safety*** — the human needs
(1) the criticals that need action now, and (2) everything else as a digest
they can scan, in that order. The steps are a graduated-fidelity ladder.

**Chesterton's fence:** the current simple fail-open exists for a reason — it
is the one behavior an operator can predict at 3 AM ("the vendor died, so
everything pages — I know what that means"). Any replacement must be at least
as *predictable* as what it replaces, or it trades a loud failure for a
confusing one. Predictability is the fence; the steps must not tear it down.

### C1.3 The proposed step semantics (precise enough to drill against)

**Trigger:** the timer-win watchdog (ADR-010's live falsifier) declares the
vendor degraded — not a single slow call (that's just a timer-win), but the
watchdog's sustained-degradation signal. The trigger is the same signal that
already exists; the steps are new responses to it. **Each step transition is an
event-logged event** (`failopen_step{n}_entered {cause, at}`) and raises the
violet system-channel banner naming the step, the cause, and the start time
(proposal §8.4's requirement, adopted).

- **Step 1 — last-known-good compiled policy.** The gate runs the last signed
  policy version's *deterministic* rules (threshold table, allowlist,
  guardrails) without the Jev call. Dispositions from step 1 carry
  `mode: failopen_step1` and a reconstruction-style label (INTERFACE_PRINCIPLES
  §4.4: derived values labeled in-band) — the operator sees "decided by
  deterministic fallback, model unavailable" on every affected row.
- **Step 2 — static severity floor.** If step 1 cannot run (no signed policy
  cached, config bundle expired per §5.1's `valid_until`), the gate pages only
  alerts whose *source-declared* severity is critical (never model-derived —
  the model is the thing that's down), with dedupe on `dedup_key`. Everything
  else is held for digest. Severity mapping is the estate's configured mapping,
  shown in the banner.
- **Step 3 — rate-capped paging + digest.** If the step-2 page rate still
  exceeds the tenant's page budget, pages are rate-capped (critical-first
  ordering, deduped) and the remainder goes to a digest the on-call can scan.
  The cap number defaults to the tenant's agreed page budget (proposal §8.5);
  the digest is never silent — the banner shows "N held in digest, M paged".

**Step-down and recovery:** steps are entered in order 1→2→3 as degradation
deepens and exited in reverse as the watchdog clears, with hysteresis (enter at
X, exit at X/2 — no flapping; flapping between steps is pre-mortem item PM-3
below). Full recovery emits `failopen_recovered` with the step history.

### C1.4 Alternatives considered AND rejected

- **A. Keep naive fail-open (status quo).** The honest baseline. At the C2-
  validated day-one tier (≤1,000 events/min sustained), a full passthrough is
  survivable — the on-call gets a loud night, not an impossible one. *Not
  rejected outright:* this RFC's recommendation (§C1.9) keeps A as the initial
  behavior until the load-test trigger proves the storm case. Rejecting A now,
  before measurement, would be building the 1M/min answer on faith.
- **B. Single rate cap, no steps.** Simpler than stepped. *Rejected:* the moment
  you specify "which alerts survive the cap," you re-derive the steps —
  critical-first ordering IS step 2, and "what runs when the cap isn't the
  binding constraint" IS step 1. B is not simpler; it's the steps with the
  transitions unspecified, which is worse (unpredictable beats complex).
- **C. Fail-open → digest-only (no paging during vendor outage).** *Rejected
  hard:* silence is a failure mode. The proposal's own evidence (78% of orgs had
  an incident where no alert fired) kills this. Fail-open must never be
  fail-silent. This is the Chesterton's fence around "page the human" — the
  direction is sacred even if the volume is managed.
- **D. Stepped degradation (this proposal).** The candidate.
- **E. Per-tenant selectable fail-open policy ("page me everything" vs
  "stepped").** *Considered, parked:* good execution-doctrine instinct (let the
  design partner choose), but a tenant choosing "page everything" learns it's
  a storm at 3 AM — the choice is not informed until the first storm. Park
  until we have production step data to show tenants; revisit as a Type-2
  configuration once D is proven.

### C1.5 Pre-mortem — top 3 failure modes

*Assumed: it is October 2027. Stepped fail-open is in production. It has failed
catastrophically. What killed it?*

**PM-1: The step-1 "compiled policy" was stale in exactly the wrong way.**
The last-known-good policy was signed 40 days ago; the estate's severity
mapping changed since. Step 1 pages on a severity mapping that no longer
matches reality — criticals are missed because the mapping drifted, and the
operator trusted the fallback because it wore the "deterministic" halo.
*Mitigation:* step 1 requires a *freshness proof* — the compiled policy
carries the config bundle's `valid_until` (§B3, L2 RFC §5.1); a policy older
than its validity window fails step 1 into step 2 automatically. Stale
fallback is worse than no fallback; the freshness check is the difference.

**PM-2: The rate cap in step 3 ate a SEV1 during the outage that mattered.**
Vendor degraded during a real incident; step 3's critical-first ordering
worked, but the cap was set to the tenant's *average* page budget, and the
SEV1's duplicates were deduped while a novel critical paged 40 times through
different dedup keys. The digest held the one alert that explained the
incident, and nobody scanned the digest at 3 AM.
*Mitigation:* the digest is not a passive list — the banner shows the top
digest fingerprints by rate-of-change ("3 novel critical fingerprints in
digest, none paged in 10m"), and the cap orders by novelty-within-severity,
not just severity. The digest must be *scannable triage*, not a parking lot.

**PM-3: Step transitions flapped during a marginal vendor degradation.**
Vendor latency oscillated around the watchdog's enter/exit boundary; the
engine entered step 2, recovered, entered step 1, degraded again — four
transitions in 20 minutes. Pages went out under three different semantics;
the incident review concluded "we don't know what the tool was doing during
the outage." Trust collapsed not from wrongness but from *illegibility*.
*Mitigation:* hysteresis on all transitions (enter at threshold X, exit at
X/2, minimum 10 minutes per step — numbers are Type 2), every transition
event-logged with cause, and the banner shows the full step history for the
incident ("step 2 03:12–03:31, step 1 03:31–"). Illegibility is a failure
mode; the audit trail is its mitigation.

### C1.6 Constitution clauses that bind, and how

- **Software — "explicit versioned boundaries":** the step semantics are a
  versioned contract with the tenant (what each step does, in writing, before
  the first outage). The steps may not be "improved" silently.
- **Infrastructure — "blast-radius compartmentalization":** the steps ARE the
  compartmentalization of vendor failure — each step bounds the blast radius
  of the failure mode above it. And: the watchdog that triggers the steps
  must itself be failure-isolated (a watchdog that dies silently is PM-0;
  ADR-022's dead-man's-switch heartbeat covers it).
- **Data/AI — "deterministic guardrails always":** step 1 and step 2 are
  deterministic by construction — no model output anywhere in the degraded
  path. This is the clause that makes the steps trustworthy: when the chaotic
  component fails, only deterministic components remain.
- **Product — "empathy for the operator" + "graceful degradation":** the
  violet banner, the per-row `mode: failopen_step{n}` label, the scannable
  digest — the degraded path must be the *most* legible path, not the least,
  because it's the path the operator walks during an incident.

### C1.7 Done-checklist, answered in writing

1. **10× scale without architectural rewrite?** The steps are volume-
   *responsive* by design — step 3 exists precisely for the 10× case. But: at
   10× the validated tier (the C2 redesign trigger), the steps themselves need
   re-examination — step 2's "page critical only" at 100k criticals/min is
   still a storm. The steps buy one order of magnitude, not infinite. Stated,
   not hidden.
2. **Third-party dependency down 4 hours — what happens?** That IS the design
   case: vendor down 4h ⇒ step 1 (deterministic policy) for as long as the
   policy is fresh, else step 2 (severity floor), step 3 if the page rate
   demands it. PagerDuty down 4h is unchanged (outbox + secondary, ADR-012).
   Vendor down 4h AND no fresh policy ⇒ step 2 for 4h — the severity-floor
   path must be drill-proven for multi-hour runs, not just transitions.
3. **Junior deploys a stale config — caught automatically or crash?** Caught:
   step 1 refuses a policy past its `valid_until` and falls to step 2 + alarm
   (PM-1's mitigation). The failure mode is "degrades one more step and tells
   someone," never "pages on a 6-month-old severity mapping silently."
4. **Did we optimize locally at the cost of global complexity?** The honest
   risk of this RFC. Three steps + hysteresis + per-row labels + digest
   triage is real complexity on the safety path — the path where complexity
   kills. The global counter-argument: the complexity is *bounded and
   legible* (three named steps, each with one job), versus the unbounded
   illegibility of "page everything and hope the human survives." The
   adversarial section (§C1.8) prosecutes this risk properly.
5. **If I leave tomorrow, can the team run this from logs + architecture docs
   alone?** The step contract + the event-logged transitions + the runbook
   ("what each banner means" — the proposal's §10.2 item 6, adopted) are the
   truck-factor docs. Gap named honestly: the digest-triage UX (PM-2's
   scannable digest) is a Prism design problem this RFC does not solve — it
   specifies the requirement, not the screen.

### C1.8 ADVERSARIAL — red-teaming this proposal (Tripwire voice)

*I am Tripwire. My job is to kill this proposal. Here is my best shot:*

1. **You are replacing the one sentence every operator understands with a
   state machine nobody will read.** "Dead vendor ⇒ page the human" fits on a
   sticker. Your three steps, hysteresis, per-row mode labels, and digest
   triage fit in a wiki page nobody opens at 3 AM. When the outage comes, the
   operator will not think "we're in step 2 with hysteresis" — they will think
   "the tool is doing something weird." You have traded a loud, predictable
   failure for a quiet, clever one, and clever is what kills people at 3 AM.

2. **Step 1 is stale-config laundering.** You admit the policy can be 40 days
   old and call it "last-known-good." Known good *when*? The estate changed;
   the policy didn't. ADR-014 exists because proofs rot — your step 1 is a
   proof-shaped object with the rot check bolted on as an afterthought
   (`valid_until` fails it into step 2, which is just admitting step 1
   doesn't work when it matters). The deterministic fallback is only as good
   as its freshness, and freshness is exactly what degrades during the long
   outages where you need it.

3. **Step 3 is suppression wearing a fail-open costume.** You built an entire
   company on "suppression is the dangerous half" and "silence is a failure
   mode," and now your step 3 *holds criticals in a digest* during a vendor
   outage — the exact scenario where a missed critical kills. Your PM-2
   mitigation ("scannable digest," "novelty ordering") is UI hope, not a
   safety property. A rate cap that drops a SEV1 into a digest is a
   suppression decision made by an arithmetic formula, unattested, during an
   incident. ADR-022 would like a word.

4. **You cannot drill what you cannot trigger.** Three steps × enter/exit ×
   multi-hour runs = a fault-injection matrix nobody will maintain. The 7/7
   drills that pass today test the *simple* fail-open. Your steps will rot
   untested, and an untested safety path is a liability, not an asset —
   Chesterton's fence again: the current fail-open is *proven* by drills;
   your steps are *promised* by prose.

5. **The trigger is the weakest link and you waved at it.** "The watchdog's
   sustained-degradation signal" — the same watchdog whose heartbeat ADR-022
   hasn't built yet (status: PARTIAL, watchdog absent). You are designing
   three responses to a signal that does not exist. Build the signal first.

*Tripwire's kill shot, in one line:* this proposal adds complexity to the
safety path before the trigger signal exists, before the digest UX exists,
and before any measurement proves the storm case — it is architecture for a
volume tier we have not validated (see §C2).

### C1.9 RECOMMENDATION — adopt with conditions (and what would change it)

**Recommendation: ADOPT-WITH-CONDITIONS — the step *contract* is adopted as
the design; activation of the steps is gated on measured evidence.**

The adversarial case is strong but not fatal, because its strongest points
are sequencing objections, not structural ones: the trigger signal doesn't
exist yet (true — build it first), the storm case isn't measured yet (true —
§C2's load-test target is the measurement), the digest UX isn't designed yet
(true — Prism's problem, specified here as a requirement). None of these says
the steps are *wrong*; they say the steps are *early*. The structural core —
shed fidelity before safety, deterministic-only degraded path, never
fail-silent — survives Tripwire's attack. Step 3's "suppression in a fail-open
costume" charge is the one that needs a structural answer, and the answer is:
step 3 pages critical-first with the digest as *scannable triage*, the banner
names the held count continuously, and the cap defaults to the tenant's own
agreed page budget — it is rate management with the human in the loop, not
suppression behind their back. If that distinction can't be held in the
implementation, Tripwire wins and step 3 dies.

**Conditions for activation (all must hold; until then, behavior stays naive
fail-open per alternative A):**
- C1: the ADR-022 suppression-rate watchdog with dead-man's-switch heartbeat
  exists and is drill-proven — the trigger signal is real before the steps
  respond to it. (Answers Tripwire point 5.)
- C2: the load-test target (§C2) demonstrates the storm case: sustained
  vendor degradation at ≥10× the day-one tier produces a page rate the
  on-call cannot survive under naive fail-open. If the test shows naive
  fail-open survives, the steps stay parked — measurement, not faith.
  (Answers Tripwire's "architecture for an unvalidated tier.")
- C3: each step has its own fault-injection drill, including a multi-hour
  step-2 run and a flap test for the hysteresis. Undrilled steps don't
  activate. (Answers Tripwire point 4.)
- C4: the digest-triage requirement (§C1.5 PM-2 mitigation) is a designed
  Prism screen, not a hope — the banner names held counts, top novel
  fingerprints, and the digest is one click from the banner. (Answers PM-2
  and Tripwire point 3's "UI hope" charge.)
- C5: step 1's freshness gate (`valid_until` ⇒ auto-fall to step 2) is
  implemented and tested, not documented. (Answers Tripwire point 2.)

**What would change this recommendation:**
- *Toward full adopt:* C1–C5 all green in production drills, plus one real
  vendor-degradation incident where the steps behaved as designed — then the
  conditions lift and stepped fail-open becomes the standing semantic.
- *Toward reject:* the load test (C2) shows naive fail-open survives the
  storm tier (then the steps are unnecessary complexity — kill per KISS and
  Chesterton); OR the drill program shows step transitions confusing
  operators in practice (then Tripwire point 1 was right — predictability
  beats cleverness, and we keep the sticker sentence).

**Kill conditions for the C1 design itself:**
- K1: if the watchdog trigger cannot be made hysteresis-clean in drills
  (flap rate >1 transition/hour in sustained marginal degradation), the steps
  are illegible in practice — kill the steps, keep naive fail-open.
- K2: if implementing step 3's digest-triage requires the console to make
  paging-affecting choices (which digest items to surface first becomes a
  de-facto suppression decision), step 3 is re-architected as pure
  chronological digest with no ordering intelligence — or killed.

---

## C2 — Scale assumption validation (INVESTIGATE)

### C2.0 The question

The proposal (§2) assumes 1M events/min as the design point and notes its own
industry benchmark says the average enterprise is ≈18/min — a 55,000× gap.
Which number do we actually design for first? This section validates with real
sources, gives the tiered answer, and checks L2's partitioning/fate-domain
decisions against the validated tier.

### C2.1 What the sources actually say

| Source | Figure | What it measures | Caveat |
|---|---|---|---|
| BigPanda 2025 benchmark (130 enterprises) | **9.6M events/year ≈ 18/min average** | Observability events ingested per enterprise | Vendor report; customers are BigPanda buyers (selection bias toward noisy estates) |
| BigPanda customer table (largest cited) | **22.8M/year ≈ 43/min average** (Customer A); typical mid: 48K–670K/year (≈0.1–1.3/min) | Same | Same; the 22.8M customer is the top end of the published table |
| PagerDuty Events API v2 rate limits (official developer docs) | **~120 events/min per integration key**; AIOps tier **up to 10,000/min per key** on request | What the incumbent pager accepts per key | A vendor's *limit*, not a customer's *volume* — but it bounds what customers actually send through PD |
| Opsgenie API | ~3,000 requests/min per account (third-party docs) | API budget, not alert volume | Third-party source; treat as indicative |
| Cleric AI SRE case study (BlaBlaCar-scale SaaS) | **10,000–100,000 alerts/month per cluster** (≈0.2–2.3/min average per cluster) | Alert volume at a mid-sized SaaS | Vendor case study |
| Riskified engineering blog | **~300–1,000 alerts/day** org-wide after noise reduction (≈0.2–0.7/min) | Alert volume, post-triage | Single company; post-optimization |
| Google SRE book | Median **~0 pages/day** per on-call shift is the quality bar | *Page* volume, not event volume | The human-sized number the whole product serves |

Three facts fall out:

1. **The proposal's "18/min average" checks out** — BigPanda's 9.6M/year ÷
   525,600 min/year = 18.3/min. Independent vendor, same number. The benchmark
   is real.
2. **1M/min is 55,000× the average and ~100× PagerDuty's own AIOps ceiling.**
   No design partner will arrive with 1M/min of *alert-granularity* events —
   PagerDuty's API would 429 them at 120/min per key long before that. The
   1M/min figure is raw-observability-event scale (metrics/logs/traces), not
   alert-decision scale.
3. **The alert/event distinction is load-bearing for our architecture.**
   BigPanda's 9.6M/year is *observability events into the correlator*; the
   alert stream that reaches a decision layer is the post-correlation subset —
   typically 10–100× smaller (BigPanda's own pitch is 97–99.9% noise
   reduction). Our receiver sits at alert granularity (PD v3 webhooks,
   Opsgenie actions, Alertmanager batches) — the Jev gate never sees the raw
   firehose. Designing the *gate* for 1M/min is designing for a number that
   cannot physically arrive at the gate.

Storm factor: alert storms multiply the average by 10–100× (flap storms,
misconfigured monitors — the failure mode ADR-001's storm handling exists
for). A 43/min-average enterprise storms at ~4,300/min. PagerDuty's own 429
guidance ("fan out across integration keys") implies customers routinely hit
120/min per key and split — bursts in the low thousands per minute per tenant
are the realistic worst case, not millions.

### C2.2 The tiered answer

| Tier | Volume (per tenant) | Meaning |
|---|---|---|
| **Design for day one (SLO)** | **1,000 events/min sustained; 10,000/min burst (10×)** | ~55× the average enterprise (18/min); ~8× PD's standard per-key limit (120/min); burst covers the AIOps ceiling (10k/min). Generous headroom, honest measurement. |
| **Redesign review trigger** | **Sustained >10,000/min, or any single-tenant burst >100,000/min** | Past PagerDuty's own AIOps ceiling — we're now in territory the incumbent doesn't serve per-key. The correlation and queue architecture gets re-examined here (see §C2.3). |
| **Load-test target** | **10,000/min sustained + 50,000/min 10-min burst**, single tenant | Measures: queue depth, dedupe correctness, Jev-call fan-out under the B=2700ms race, outbox depth, correlator state growth. This is the C1-C2 evidence gate. |

**Why not test at 1M/min:** testing at 100× the redesign trigger is tuning
theater (principal-mindset: proxy-trap audit). A 1M/min test would validate
an architecture we'd throw away at the trigger anyway — the trigger exists
precisely because the architecture changes there. The honest test proves the
day-one tier with margin and locates the actual knee; it does not perform
confidence at a scale no customer can send us.

**Why not design day-one for 18/min:** execution-doctrine says don't
overbuild — but 18/min is the *average*, and averages don't page anyone.
Storms are the product's reason to exist (ADR-001), and the storm factor is
10–100×. 1,000/min sustained covers a 43/min-average enterprise's 20× storm
with room; the per-tenant quotas in L2 §1.2 are set against this tier.

### C2.3 Do L2's partitioning and fate-domain decisions hold at the validated tier?

**Yes — with the trigger named as the re-examination point.**

- **`(tenant_id, month)` partitioning (L2 §3):** at 1,000/min sustained, a
  tenant writes 1.44M decision-events/day; a 7-day hot tier is ~10M rows per
  tenant-month-partition — comfortable for partitioned Postgres. Even at the
  10,000/min burst tier (14.4M/day), partitions stay operable; TTL-by-detach
  keeps the hot tier bounded. The disk-guard arithmetic (L2 §3.1) is computed
  against the *measured* `mean_event_bytes` — the tier feeds the arithmetic,
  not the other way around.
- **Fate domains (L2 §9):** at the validated tier, queue depths between
  ingress→decide→relay stay bounded under the Redis Streams design; the
  correlator's state fits in Redis with headroom; the outbox's at-least-once
  contract holds. PM-1's reconciler (spill_offsets vs decide_acks) is
  feasible because the offset space is small enough to reconcile continuously.
- **What breaks at the trigger (>10k/min sustained):** L2 §2.3's
  central-correlation invariant gets stress-tested first — the correlator
  becomes the bottleneck, and the rejected alternatives (decide-at-edge,
  CRDT correlation) get re-opened *as the designed response to the trigger*,
  not as speculation now. Second: the event-log hash chain's write throughput
  (append-only + hash per event at 100k/min = 1,667 hashes/sec — fine for
  one node, but cross-region checkpoint replication at that rate needs
  re-examination). Third: per-tenant Jev-budget partitioning (L2 PM-2) —
  at 10k/min the Jev call fan-out, not the queue, is the binding constraint
  (B=2700ms race × 10k calls/min needs ~450 concurrent Jev calls — the vendor
  budget, not our architecture, is the wall).
- **The bridge tenancy model (L2 §1)** is unaffected by the tier choice: pool
  default with per-tenant quotas is *more* defensible at the validated tier
  (the noisy-neighbor math in PM-2 is computed at realistic volumes).

**Bottom line for L2:** the RFC's decisions hold at the validated day-one
tier. The redesign trigger is defined as the point where §2.3 and §3 get
re-opened — the trigger is part of the architecture's contract with the
future, not an admission of weakness.

### C2.4 Kill conditions

- K1: if the design partner's measured estate volume (shadow-tap data)
  exceeds 10,000/min sustained for >1 week, the day-one tier was wrong —
  compress the timeline: the redesign review happens now, not at a future
  trigger.
- K2: if the load test shows the knee (queue-depth unbounded growth, dedupe
  incorrectness) *below* 10,000/min sustained, the architecture doesn't meet
  its own day-one tier — the bottleneck gets a dedicated RFC before any
  feature work.
- K3: if a credible source (not a vendor pitch deck) shows alert-granularity
  volumes ≥100,000/min sustained at a plausible design partner, the
  1M/min question re-opens — until then it stays closed.

---

## B4 — "Hold" terminology alignment (ADAPT, no code)

### B4.0 Decision

**Terminology alignment ONLY. The engine disposition enum is five-valued
post-D3 — `page_now | page_business_hours | suppress | passthrough | folded`
(`src/sentinel/models.py:29`; `folded` added by D3/ADR-016 for storm-folded
members that page via the aggregate digest — folded pages, it is never a
suppression). ADR-007's decision (no *sixth* value for mute-as-label) stands;
this section does not touch the enum.**

### B4.1 The alignment

The proposal uses a page / hold / suppress split (§1.1: "the middle tier
(hold, meaning batch into a digest)") and encodes it as the decision channel's
three glyphs (§5.2: Page = solid triangle, Hold = half-filled circle, Suppress
= hollow circle).

Our engine's middle tier is `page_business_hours`, whose documented criterion
is: *"Route to the queue for next-business-hours handling; do not wake
anyone."* (`src/sentinel/questions.py:66`) — reached in the gate when lower-
urgency probabilities dominate with sufficient confidence
(`src/sentinel/gate.py:6`). Functionally, that IS the proposal's hold: a
deferred, batched, non-waking notification tier. The concept adds nothing we
don't have; the *word* adds clarity we do want.

**Adopted mapping (UI/docs language → engine value):**

| Operator-facing term | Engine disposition | Renders as |
|---|---|---|
| **Page** | `page_now` | Solid triangle (proposal §5.2) |
| **Hold** | `page_business_hours` | Half-filled circle (proposal §5.2) |
| **Suppress** | `suppress` | Hollow circle (proposal §5.2) |
| **Pass-through** (no decision taken) | `passthrough` | Neutral glyph, distinct from all three — never collapsed into Page |
| **Folded** (storm member — paged via the aggregate digest, never suppressed) | `folded` | Distinct glyph, never the suppress hollow circle — folded pages; rendering it as suppressed would be the exact lie D3 was built to kill |

Rules for the alignment (binding on Prism lanes):
1. "Hold" is a *rendering label* for `page_business_hours`, never a stored
   value. The event log, the API contract, and the gate store the five-valued
   enum verbatim. A UI string search for "hold" must find zero hits in
   `src/sentinel/` (same enforcement shape as ADR-007's "zero mute in src/").
2. Every "Hold" rendering carries its evidence companions (decision reason,
   confidence, policy version) per the honesty invariants — a Hold row without
   "why" is the same failure as a bare disposition (INTERFACE_PRINCIPLES §4,
   P2).
3. `passthrough` gets its own neutral glyph and its own plain-language
   explanation ("no decision taken — passed through", with the reason:
   timer-win, uncertain, cannot_determine). It must never render as a Page —
   collapsing fail-open passthrough into "Page" would misreport the safety
   semantic (ADR-010) and inflate page-precision metrics.
4. Docs (operator runbook, policy editor) use Page / Hold / Suppress /
   Pass-through consistently; the mapping table above is reproduced in the
   runbook's glossary.

### B4.2 Why not a fifth engine value

ADR-007 already litigated this exact shape for "mute" and decided:
widening the gate's enum leaks the data model across the process boundary
(Forge's position, adopted). "Hold" as an engine value would be the same
mistake with a different name — and worse, it would fork the middle tier
into two overlapping concepts (`page_business_hours` vs `hold`) with no
behavioral difference. The engine's five values describe *what the system
does*; the UI's labels describe *what the operator understands*
(Page/Hold/Suppress/Pass-through/Folded). The mapping is the alignment;
there is nothing else to build.

### B4.3 Kill conditions

- K1: if operator testing (design partner or Prism usability pass) shows
  "Hold" misleads — e.g., operators read it as "the alert is being held and
  nothing will happen" when the engine actually posted a downgraded warning
  trigger — drop the term and render the literal disposition
  (`page_business_hours`) with a one-line explanation. Clarity of the safety
  semantic outranks elegance of the vocabulary.
- K2: if a future policy type needs a genuinely distinct middle-tier
  *behavior* (not just a label), that re-opens the enum as a Type-1 RFC —
  this alignment does not pre-authorize it.

---

## Cross-item notes

1. **B3 → C1 dependency:** C1's condition C1 (watchdog with heartbeat) and
   B3's review-due enforcement both ride the ADR-022 watchdog. It is the
   single most load-bearing unbuilt component across both items — the night
   shift's D-lanes should treat it as the critical path.
2. **C2 → C1 gating:** C1's activation condition C2 is the C2 load-test
   target. The measurement is the decision mechanism; neither item proceeds
   on faith.
3. **C2 → L2:** the redesign trigger (>10k/min sustained) is the defined
   re-entry point for L2 §2.3's rejected alternatives. L2's RFC should
   reference this trigger explicitly at ratification.
4. **Attestor identity (L2 §14.1)** is now load-bearing for B3's sign-off
   matrix as well as ADR-013/014/022 — still needs its own ADR; flagged for
   the Vault-led follow-up, not solved here.
5. **Honesty boundary** (L2 §0, extended): nothing in this doc may be
   described as a property of the system — in docs, demos, or buyer
   conversations — until a lane implements it and the ratification checklist
   closes. An adopted design is not a shipped property.

---

*End of RFC follow-ups. Status: PROPOSED. Next step: panel review
(disagree-and-commit), then lane sequencing by the wave coordinator.*
