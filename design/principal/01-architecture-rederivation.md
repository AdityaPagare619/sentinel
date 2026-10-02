# Architecture Re-derivation — Sentinel, from first principles

**Author:** FORGE (principal architect) · **Date:** 2026-10-03
**Under review:** `ARCHITECTURE.md` (v0.1 engine, frozen 2026-10-02),
`PLATFORM_ARCHITECTURE.md` (frozen 2026-10-02 evening)
**Judged against:** `design/principal/00-laws.md` (the 7 laws)
**Method:** five whys per component (Law 4) → deletion pass (Law 1) →
adversarial pass (Law 2) → reversibility marking (Law 3) → legacy
archaeology (Law 5) → pre-mortem (Law 6) → four-constitutions review
(Law 7). No code was written or changed for this document.

---

## 0. Bottom line up front

**The two-tier architecture (hot paging path + platform tier, audit log as
the only bridge) is RIGHT and survives the re-derivation.** It is the
control-plane/data-plane split, and the file-as-bridge is the correct
mechanism. What does *not* survive:

1. **The Jev call blocks the paging path.** With a measured ~11.4s first-call
   latency against a 70–500ms spec, "the single Jev call" is not free — the
   do-no-harm law as written protects the paging path from the *platform*
   while leaving it exposed to the *engine's own* inference call. **Move 1:
   race-to-page.**
2. **The audit log records decisions but not forward receipts.** A crash
   between the audit write and the forward creates a lying river (says
   paged, never paged) or a silent gap (paged, no row). This is the classic
   dual-write problem, and the current schema has it. **Move 2: the audit
   log becomes an append-only event log.**
3. **The forwarder's failure mode is "log + metric."** Fail-open is vacuous
   when the thing that failed *is* the paging path — there is nothing to
   fail open *to*, and "alert the operator" has no channel because the
   alerter is what's broken. This is the one failure mode that degrades to
   silence, which the product forbids. **Move 3: the forwarder becomes a
   durable last-mile with no single notification path.**

Everything else in the frozen architecture survives — including several
choices this review tried hard to kill. The kills that failed are recorded
in §2 (the deletion ledger) because a survived attack is evidence, not
luck.

All three moves are **Type 1** (Law 3): they change the paging path's
latency semantics, the durable-truth format, and the delivery contract.
Each ships with its precedent, its honest tradeoff, and its migration
note. Nothing here is a vibe.

---

## 1. Five whys — every major component, peeled to bedrock

### 1.1 The receiver (HTTP ingress mirroring PagerDuty Events API v2)

1. Why does Sentinel need its own receiver? → Because customers point
   their *existing* alerting integrations at Sentinel instead of PagerDuty
   directly.
2. Why not a library/sidecar inside the customer's pipeline? → Because
   the wedge is "15-minute integration, zero code changes." Touching every
   integration is rip-and-replace — the exact failure mode the YouTube
   pivot record names as fatal for adoption.
3. Why mirror the PD Events API v2 shape exactly (same routes, same
   response envelope)? → Because hundreds of existing integrations speak
   it. The Events API is the narrow waist of the alerting ecosystem; a
   stable contract outlives any single vendor choice.
4. Why HTTP push at all, rather than consuming from the customer's queue?
   → Because alerting sources are push-based. We don't get to choose
   their emission model; interposition requires protocol compatibility
   with what already exists.
5. **Bedrock:** Sentinel is an interposition point, and interposition is
   priced in protocol compatibility. The receiver is the price of the wedge.

**Judgment: KEEP.** Type 1 — the public contract. (Law 5 detail in §5.)

### 1.2 The correlator (dedup / storm-collapse / change-windows, as a separate stage)

1. Why a separate stage before the gate? → Design principle 3: *never pay
   Jev for deterministic work.* Dedup, storm-collapse, and change-window
   suppression are pure functions of history and config.
2. Why does that deterministic work exist at all? → Because flaps, storms,
   and deploys are where the noise lives, and the SRE field research
   (2026-10-02, escalation-dedup-craft) shows storm control is *layered
   because every layer can be bypassed* — per-source rate limit →
   idempotency → fingerprint dedup → flap debounce → grouping → digest
   throttle → never auto-close. PagerDuty, Alertmanager, and Opsgenie each
   built dedup primitives natively (see §5). The industry converged; we
   don't get to skip it.
3. Why in Sentinel rather than relying on PagerDuty's native dedup? →
   Because Sentinel sits *before* PagerDuty. If we forward every flap-fire,
   we haven't reduced noise — and we've paid a Jev call per fire. Our
   dedup is the **Jev-budget guard**; PD's dedup is the incident-hygiene
   guard. Different reasons, both needed. (This is also why the receiver
   must still forward the vendor `dedup_key` when it has one — §5.)
4. Why a separate *module* rather than folded into the receiver or the
   gate? → Law 1 asks the sharper question (could it be deleted?), but on
   placement: folding it into the receiver makes the receiver stateful
   (episodes), violating its job as a stateless protocol adapter; folding
   it into the gate puts the "should we even call Jev?" decision behind
   the Jev-call machinery and blurs the economic boundary. The stage
   exists to make the Jev call *conditional*.
5. **Bedrock:** the correlator is the economic boundary of the system. It
   decides whether an alert is worth a paid, slow, non-deterministic
   inference call. Everything downstream assumes that question was
   already answered.

**Judgment: KEEP as a stage.** But two findings: the fingerprint
definition excludes `env`/`cluster` — a concrete missed-page pre-mortem
(§6, scenario A) — and the in-memory episode state dies on restart,
which is a Law-2 crash vulnerability (§3). ADR-001's episode craft
(reopen-inside-flap-window, fresh-episode-after, vendor-consistent) is
correct and should be promoted from ADR-input into the architecture
proper.

### 1.3 The gate (Jev call + threshold policy)

1. Why does the gate exist? → To convert Jev's probabilistic answers into
   actions under an expected-cost policy (suppress iff
   P(SEV1)·C_FN < (1−P)·C_FP).
2. Why not wire Jev straight to the pager? → Law 7 (data/AI): *the model
   advises, it never controls.* Jev is provably non-deterministic
   (1.3–2.2% flips, no seed exists). A direct Jev→pager wire makes paging
   a dice roll. The gate is where the deterministic guardrails live:
   fail-open, uncertainty-pages, triple-lock suppress, shadow mode,
   audit-write-always — none of which can live inside the model call.
3. Why does the policy live in the gate rather than the forwarder? →
   Because the decision must be auditable *before* the action, and the
   kill-the-client test needs a boundary: client dies → gate returns
   `passthrough`, forwarder relays the original payload. If policy lived
   in the forwarder, a dead client would leave the forwarder with no
   disposition — the boundary would leak, and the test would be
   unwriteable.
4. Why three questions and not one or five? → §1.4.
5. **Bedrock:** the gate is the system's conscience — the single place
   where probabilistic advice is converted into irreversible action under
   deterministic rules. Every trust claim Sentinel makes ("we never drop
   a page on error") is enforced here, in code, in one module.

**Judgment: KEEP.** But the gate's Jev call is *blocking* on the hot
path, and that is the architecture's deepest flaw — §7, Move 1.

### 1.4 The three questions (why three, not one or five)

- **Why not one question** ("what should I do with this alert?")? →
  Because the policy needs the severity *probability distribution*, not
  the argmax. Suppress is rational iff P(p1_critical) < 0.002 — an
  expected-cost computation that is meaningless on a collapsed single
  choice. Calibration (ECE on Q1/Q3) likewise needs the distribution.
  Team routing is a separate decision with separate consumers (routing,
  analytics, onboarding); entangling it with disposition means one flip
  corrupts two outputs, and you can no longer audit "we paged the wrong
  team but the right severity."
- **Why not five** (add urgency? auto-resolve likelihood? runbook
  needed?)? → Each question costs input tokens (descriptions are billed
  as input) and adds a failure surface. A question earns its keep only
  with a distinct policy consumer: Q1→cost math, Q2→routing, Q3→action +
  confidence. No fourth consumer exists in the design. Law 7 (software
  engineering): the smallest sufficient system wins; every line (and
  every billed token) is liability.
- **Why these three?** → They factor the decision into orthogonal axes:
  *how bad* (severity), *who* (team), *what to do* (disposition). The
  factorization is what makes the triple lock expressible at all —
  P(p1) comes from Q1, confidence from Q3, the allowlist from history.
  Jev's typed-question design rewards decomposing one entangled judgment
  into independent judgments; correlated questions would double-count
  the same evidence.

**Judgment: KEEP the three.** The option sets are Type 1 — changing an
option breaks calibration continuity and allowlist semantics, so option
changes get RFC rigor, not drive-by edits.

### 1.5 The forwarder

1. Why a separate module? → Because forwarding is a *different failure
   domain* than deciding: Jev can be up while PagerDuty is down and vice
   versa. Law 7 (infra): blast-radius thinking — the module boundary is
   drawn where the failures decouple.
2. Why relay to PagerDuty/Opsgenie rather than becoming the pager? →
   Law 5: PD's escalation policies (L1→L2→L3, manager anchor), urgency
   model, and multi-channel notifications (SMS/voice/push/email) exist
   because of a decade of 3 AM outages. Rebuilding that is not our
   product; our product is the triage decision.
3. Why downgrade severity to `warning` for the business-hours queue? →
   Because PD's own severity→urgency mapping (critical/error→high
   urgency, warning/info→low) is the vendor-blessed primitive for "don't
   wake anyone." We use their primitive instead of inventing one.
4. Why not fold it into the gate? → Because the gate's job ends at the
   decision; the forwarder's job is the durable last-mile. Conflating
   them makes a PagerDuty outage look like a decision error in the audit
   log — which is exactly the kind of misattribution that poisons a
   postmortem.
5. **Bedrock:** the forwarder is the system's promise-keeper. A decided
   page is a promise to a human, and promises need durability, retry,
   and idempotency.

**Judgment: KEEP — but it is under-designed.** "Log + metric" on forward
error is the one place fail-open is vacuous (§3, adversary 3; §6,
scenario D; §7, Move 3). The forwarder must also send a *stable*
`dedup_key` on every attempt so retries are idempotent at the vendor
(PD's own docs give us the primitive — §5).

### 1.6 The audit log (SQLite, WAL, the only bridge)

1. Why does the audit log exist? → Because the product *is* the trust
   layer. The JEV research report's honest headline: "money is in selling
   avoided cost + the trust wrapper (calibration evidence, audit trails,
   failover), not Jev's cheapness." Every platform surface — river,
   calibration, simulator, explorer, analytics — reads this log. No log,
   no product.
2. Why SQLite? → Stdlib, single-file, crash-safe; v0.1 is single-box.
   Postgres is a second process to operate at 3 AM (Law 2: another thing
   that can be down). The schema is kept Postgres-compatible so the
   upgrade is a Type-2 swap that preserves the Type-1 contract.
3. Why WAL mode? → sqlite.org: *"WAL provides more concurrency as readers
   do not block writers and a writer does not block readers."*
   (https://www.sqlite.org/wal.html). The platform tier's reads must be
   *incapable* of blocking the audit writer — WAL is the mechanism, not
   a comment.
4. Why is the audit log the *only* bridge between engine and platform?
   → Because do-no-harm requires the platform to be incapable of slowing
   a page. A file is the simplest durable bridge with crash recovery; a
   socket or queue adds a network failure mode to the bridge *and* loses
   durability when the engine crashes. One mechanism, two jobs
   (durability + decoupling) — Law 7 (SE): smallest sufficient system.
5. **Bedrock:** the audit log is the system's memory — the only thing
   that survives a crash, the only evidence in a postmortem, the only
   input to calibration. Everything else is ephemeral; this is the
   durable truth.

**Judgment: KEEP SQLite/WAL as the bridge mechanism — but the
GRANULARITY is wrong.** A `decisions` row records the decision, not the
forward receipt: the dual-write gap (§3, adversary 5; §7, Move 2). And
the WAL has a named adversary the frozen design doesn't name: a stuck
platform reader prevents checkpoint reclamation, the WAL grows, the disk
fills, the audit insert fails (§3, adversary 4).

### 1.7 The platform tier as a separate OS process

1. Why a separate process, not a thread in the engine? → Threads share
   fate: a GIL stall, an uncaught exception, or a memory spike in a
   dashboard thread can delay the paging path. Process separation is the
   *enforcement* of do-no-harm, not a comment in the code.
2. Why not a separate host/service? → v0.1 is single-box and the bridge
   is a file; the failure domain we care about is the paging path, and
   same-host process isolation is sufficient. (sqlite.org constraint is
   noted: WAL requires all processes on the same host —
   https://www.sqlite.org/wal.html — which the single-box design
   satisfies by construction.)
3. Why does the platform make zero Jev calls on any read path? → Every
   Jev call is paid, slow, and non-deterministic. The simulator
   recomputes from *stored* probabilities — the same pure math, no
   inference. This is also what makes the simulator honest: it shows
   what the recorded evidence implies, not what a fresh dice roll says.
4. **Bedrock:** this is the control-plane/data-plane split. Kubernetes
   keeps serving pods when the API server dies; Envoy keeps routing
   when the control plane is gone. The paging path is the data plane;
   the platform is the control plane. A control-plane outage must never
   break the data plane.

**Judgment: KEEP. The two-tier architecture is RIGHT.** What must change
is what flows across the bridge (events, not rows — Move 2) and what
happens on the hot path (race, not block — Move 1).

---

## 2. Law 1 — the deletion ledger

Every row answers: *"What if this didn't exist at all?"* A survived
attack is evidence.

| Candidate for deletion | What if it didn't exist? | Verdict |
|---|---|---|
| Correlator as a separate stage (fold into receiver) | Receiver becomes stateful (episodes); the "should we call Jev?" economic boundary blurs into protocol handling. The stage exists to make the Jev call conditional — deleting the *stage* doesn't delete the *work*. | **KEEP** |
| Generic webhook path (PD Events API only) | Kills the Alertmanager wedge — the actual market. The receiver's job is protocol compatibility (§1.1). | **KEEP** |
| `page_business_hours` disposition (fold into passthrough) | Loses the "don't wake anyone but don't drop" middle that PD's urgency model supports natively. Field research found buyers want *more* middle states (muted-not-dropped), not fewer. | **KEEP** |
| Tuner CLI *and* `/api/simulate` as separate implementations | Two implementations of one pure function (threshold math + projection). This one the review **kills**: extract a pure `policy` kernel; the CLI and the HTTP endpoint become thin shells over it. Oracle's "same code path, not a copy" rule made structural. | **DELETE one (keep the kernel)** — Type 2 |
| In-memory correlator state (stateless correlator) | Every flap-fire pays a Jev call; the unit economics die at the first flapping check. The state must exist — but it must survive a restart (§3, adversary: restart mid-storm → 40 Jev calls again). | **KEEP, make durable** |
| The allowlist (third lock of the triple lock) | Week-1 probabilities are uncalibrated (ARCHITECTURE.md §4, honest note). Confidence alone cannot carry suppression until calibration is earned on the customer's own labels. Law 7 (data/AI): deterministic guardrails over probabilistic models. | **KEEP** |
| The `outcomes` table / label-join pipeline | Calibration needs labels from *somewhere*; shadow runs produce them. Deleting it deletes the trust sale. | **KEEP** |
| Forwarder severity-downgrade for queue | Alternative: a separate PD service with a low-urgency policy — heavier customer config for the same outcome. The downgrade uses the vendor's own primitive. | **KEEP** |
| The entire platform tier (engine alone pages correctly) | The engine is correct but *unsellable*: the research report's headline is that the money is in the trust wrapper, not the inference. Deleting the platform deletes the business. This is also why the bridge must be right — the thing we sell is only as trustworthy as the log it's built on. | **KEEP** |
| Duplicate-inherits-disposition (no Jev call for duplicates) | Alternative: cheap re-check per duplicate. But a duplicate is definitionally the same question; re-asking a non-deterministic oracle N times and taking the latest answer *increases* flip exposure rather than reducing it. Inheritance is correct — *provided* the fingerprint is correct (§6, scenario A) and reopen escalates per ADR-001. | **KEEP** |
| Shadow mode as a flag | With Move 1 (race-to-page), late Jev answers become shadow decision events *structurally* — shadow stops being a mode and becomes the system's normal way of handling slow inference. The flag can eventually be deleted. | **DELETE the flag, keep the semantics** (post-Move-1) |

---

## 3. Law 2 — eternal friction: named adversaries

The network drops. Data corrupts. The next maintainer is tired and will
not read the docs. Each adversary below is named, with its current
mitigation status. **Unmitigated** items drive the three moves in §7.

**A1. The 7-second Jev (slow-but-not-dead).** Timeout is 8s with a 2s
retry budget; the first real call measured **~11.4s** against a 70–500ms
spec. A degraded-but-not-dead vendor evening means *every page waits up
to ~10s* — and the retry budget stacks onto page latency during exactly
the incident when latency matters most. The do-no-harm law protects the
paging path from the platform while leaving it exposed to the engine's
own inference call. **UNMITIGATED → Move 1.**

**A2. Jev 429/529 storm during a customer alert storm.** Retry budget
exhausts → passthrough (correct direction), but the storm still pages N
times unless collapsed first. Mitigation holds: the correlator's
storm-collapse fires one Jev call per storm (load-bearing at 40 req/s).
**MITIGATED** — provided the correlator's episode state survives restart
(see A6).

**A3. PagerDuty/Opsgenie down.** The forwarder's documented behavior is
"log + metric; the page was already decided, alert the operator." But
*the alerter is what's broken* — there is no channel left for "alert the
operator," and fail-open is vacuous when the failure *is* the paging
path. A decided page that never arrives degrades to **silence**, the one
thing the product forbids. **UNMITIGATED → Move 3.**

**A4. Disk full via WAL growth from a stuck platform reader.** SQLite
checkpoints cannot reclaim WAL frames that a reader snapshot is still
using; a stuck SSE/platform read transaction grows the `-wal` file until
the disk fills, and then the engine's audit insert fails. The platform
tier — which must be *incapable* of harming the paging path — can fill
the disk out from under it. **PARTIALLY MITIGATED → named fix:** platform
reads use short-lived connections (never hold a read transaction across
an SSE heartbeat); the engine runs periodic
`PRAGMA wal_checkpoint(TRUNCATE)`; a WAL-size tripwire metric pages the
operator before the disk does. (sqlite.org documents the 1000-page
auto-checkpoint threshold and the same-host WAL constraint —
https://www.sqlite.org/wal.html.)

**A5. Engine crash between audit-write and forward.** Two directions,
both bad: audit row says `page_now` but the page never left the box
(**the lying river**); or the forward succeeded but the crash came
before the audit write (**the silent gap**). The current schema records
the decision, not the delivery — the textbook dual-write problem
(Richardson, https://microservices.io/patterns/data/transactional-outbox.html).
**UNMITIGATED → Move 2.**

**A6. Restart mid-storm.** Correlator episode state is in-memory; a
restart during a 40-req/s storm re-fires a Jev call per alert until the
windows refill. The economics and the latency budget both break for the
duration. **UNMITIGATED → durable episode state** (SQLite table in the
same file; the bridge file becomes the engine's durable state, not just
the platform's view).

**A7. The tired maintainer edits `thresholds.json` by hand.**
`suppress_conf_min: 0.5` at 2 AM. The tuner *reports* tradeoffs, but the
loader must *refuse*. **Principle established: fail-CLOSED on config,
fail-OPEN on runtime.** The loader validates floors derived from the
expected-cost inequality (never suppress below the cost optimum); on
violation it falls back to compiled defaults, logs loudly, and emits a
metric. A config error must never silently widen suppression.

**A8. Fingerprint semantic miss (not collision).** The fingerprint is
`sha256(service|check|severity_in|region)[:16]` — 64-bit truncation is
fine at our volumes (birthday bound is not the threat). The threat is
*semantic*: `env` and `cluster` are excluded. Cluster A flaps (suppressed
via allowlist); cluster B fires a real SEV1 within the 300s window → the
duplicate path *inherits the suppression* → **missed page**. The
`Alert.labels` dict already carries env/cluster; the hash ignores them.
**UNMITIGATED → Type-1 fix:** the fingerprint includes env/cluster, and
the exclusion policy (which labels are in/out of identity) is documented
explicitly, not implied by a format string.

**A9. TypeSafe changes the wire format unannounced** (no SLA; dynamic
rate limits). Mitigation holds structurally: per-call model-version
resolution recorded in every row (`jev_model`), wire errors →
`JevError` → passthrough, 401/422 never retried. The recorded model
version is what makes a vendor-side change *auditable* rather than
mysterious. **MITIGATED.**

**A10. The next maintainer deletes the kill-the-client test** ("it's
slow/flaky"). The test is the executable form of the fail-open
invariant. **Fix:** keep it in CI under a name that explains why it
exists, and mark it release-blocking in the test itself, not just in a
doc.

---

## 4. Law 3 — Type 1 vs Type 2 register

(Seed for `design/principal/09-decision-register.md`. A Type 1 decided
like a Type 2 is a future incident.)

**Type 1 — irreversible, RFC-grade rigor required:**

| Decision | Why irreversible |
|---|---|
| Paging-path stage order (receiver→correlator→gate→forwarder) | Reordering changes failure semantics; every guarantee is stated against this order |
| The fail-open invariant + kill-the-client test | Removing it is a company-ending change; it is the product's core promise |
| The three questions' option sets | Changing an option breaks calibration continuity and allowlist semantics |
| Two-process topology (engine vs platform) | Merging later destroys the isolation guarantee; it cannot be un-merged without a rewrite |
| The audit schema as the platform's API | Every platform surface is a projection of it; the bridge contract is the product contract |
| **Move 1:** the Jev-call race (paging latency bounded by our budget) | Changes the latency semantics of every page the system will ever send |
| **Move 2:** the event-log schema (decision/forward/flip events) | The durable-truth format; everything downstream (river, calibration, postmortems) reads it |
| **Move 3:** the forwarder's delivery contract (at-least-once, dedup_key idempotency, secondary channel) | The promise-keeping contract with the customer |
| Fingerprint definition (identity semantics) | Changing it orphans historical episodes and allowlist entries |
| Public webhook contract (`/v2/enqueue` mirror) | Customer integrations are written against it |
| Data retention policy | **OPEN — no RFC exists yet.** Retention is irreversible once data is gone (or once a regulator asks). Flagged for the coordinator. |

**Type 2 — reversible, speed is fine:**

| Decision | Why reversible |
|---|---|
| Timeout / retry-budget *values* | Tunable constants; Oracle's latency campaign sets them empirically |
| Threshold *values* in `thresholds.json` | Tuned per org; the loader floors guard the safe direction |
| In-memory vs Redis correlator | Swappable behind the module boundary (though §3/A6 forces durability either way) |
| SQLite → Postgres | Schema kept Postgres-compatible — the Type-1-preserving move; the swap changes ops, not contracts |
| stdlib `http.server` → FastAPI/gunicorn | Documented production swap; no contract change |
| SSE vs polling, dashboard copy, tuner CLI flags, synthetic generator | Presentation and tooling |
| Window constants (300s dedup, 30m flap, storm thresholds) | Tunable with documented rationale; ADR-001 owns the values |

---

## 5. Law 5 — Chesterton's fence: why the incumbents look the way they do

Before proposing changes to how paging works, name the outage each
incumbent feature was built for.

**PagerDuty's `dedup_key`.** The official Events API v2 docs state the
semantics precisely: events with the same `dedup_key` apply to the
*open* alert; *"once the alert is resolved, any further events with the
same dedup_key will create a new alert (for trigger events) or be dropped
(for acknowledge and resolve events)"*
(https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/02-Trigger-Events.md).
The fence: customers were burned twice — once by duplicate incidents
from retried triggers (hence key-based dedup), once by *resurrected*
incidents when a re-fire appended to a resolved alert (hence the
open-ness rule). **Our correlator honors this fence** via ADR-001's
episode semantics (reopen inside the flap window; fresh episode after —
vendor-consistent), and **Move 3 uses the vendor's own primitive**:
PD built idempotent retry into the API, so our durable outbox retries
with a stable `dedup_key` instead of inventing its own idempotency.

**Alertmanager's grouping, inhibition, and silences.** `group_by` /
`group_wait` / `group_interval` exist because operators drowned in
one-notification-per-firing-alert storms; inhibition rules ("DB is down
→ suppress downstream can't-reach-DB pages") exist because cascading
failures page the wrong humans first; silences exist because planned
work is not an incident. Each was learned from a specific 3 AM. **Our
storm-collapse mirrors grouping; our change-windows mirror silences.**
We reimplement rather than reuse because we sit *before* the vendor —
but we keep the semantics recognizable so an SRE's existing mental
model transfers.

**PagerDuty's escalation policies and multi-channel notifications.**
L1→L2→L3 with a manager anchor, and SMS *and* voice *and* push *and*
email, exist because *a single notification path fails* — people sleep
through push, carriers drop SMS, apps get muted. **This is the fence our
forwarder currently ignores.** "Log + metric" on forward failure assumes
the primary path works; the incumbents' entire notification design says
it won't, at the worst moment. Move 3 is Chesterton-compliant: no
single notification path, ever.

**The Events API v2 contract itself.** It is the narrow waist: every
monitoring tool emits to it, every runbook assumes it. Our receiver
mirrors it byte-for-byte in the response envelope because replacing the
contract means re-wiring the customer's estate — the adoption-killing
move.

**Google SRE's alerting philosophy (Ch. 6, Ewaschuk).** "Alert on
symptoms, not causes… Every page must be actionable, urgent, and about
real user impact"
(https://sre.google/sre-book/monitoring-distributed-systems/). The
fence: alert fatigue is itself an outage risk — the page that mattered
is the one that got dismissed. **The gate is this philosophy made
mechanical.** Q1's option descriptions encode it verbatim
("Customer-facing outage or data-loss risk in progress; revenue/SLA
actively burning"). Where SRE culture enforces paging discipline
socially, Sentinel enforces it in code, before the human is involved.

---

## 6. Law 6 — pre-mortem

*It is one year from now. Sentinel caused a missed SEV1 that cost a
customer millions. What exactly failed — in concrete, mechanical
detail?*

**Scenario A — the fingerprint miss.** 02:14 IST. The `cache` cluster's
`redis-memory` check flapped all evening on cluster A (same service,
check, severity, region as cluster B) and was allowlist-suppressed. At
02:14, cluster B's Redis began a genuine failover — same
service/check/severity/region, different cluster. The correlator hashed
`service|check|severity_in|region`, matched cluster A's fingerprint
inside the 300s window, and the duplicate path *inherited the
suppression*. No Jev call. No page. The postmortem found the two alerts
side by side in the river with identical fingerprints and different
`labels.cluster` values that the hash had ignored. → **Fix (§3/A8):
fingerprint includes env/cluster; exclusion policy explicit.**

**Scenario B — the lying river.** During a deploy storm the engine
process was OOM-killed between the audit write and the forward: the
`decisions` row said `page_now` for the SEV1, but the page never left
the box. The postmortem team trusted the river for 40 minutes before
someone thought to check PagerDuty directly. The gap was discoverable
only by the *absence* of something the schema never recorded. →
**Fix (Move 2): forward receipts as events; a decision without a
receipt is visible as unconfirmed, not as paged.**

**Scenario C — the slow Jev.** TypeSafe had a degraded-but-not-dead
evening: p99 9s for three hours. Every page waited out the 8s timeout
plus retry budget. The customer's SEV1 page arrived ~10s late into a
4-minute outage; their postmortem named Sentinel's added latency as a
contributing factor, and the renewal died. The cruelest detail: our
retries during *their* incident added load to an already struggling
vendor. → **Fix (Move 1): paging latency bounded by our race budget;
the page never waits on inference.**

**Scenario D — the vendor outage.** PagerDuty had a 22-minute regional
incident. The forwarder got 500s; three decided pages never arrived;
"alert the operator" was a log line in a file nobody reads during an
incident. → **Fix (Move 3): durable outbox with idempotent retry, and a
secondary channel that does not depend on the primary.**

Every mitigation above gets a test — that is the law. (A8: a
cross-cluster duplicate-inheritance test with a real SEV1 on cluster B.
B: a kill-between-audit-and-forward test asserting the river shows
*unconfirmed*. C: a slow-client test asserting the page leaves within
the race budget. D: a dead-vendor test asserting outbox retention +
secondary-channel fire.)

---

## 7. Law 7 — four constitutions

- **Software engineering.** Boundaries are explicit and drawn where
  failures decouple (§1) — good. Violation found: the tuner CLI and
  `/api/simulate` are two implementations of one pure function; Law 1
  kills one (extract the `policy` kernel, §2). Code-is-liability is
  respected by the stdlib-only discipline; the moves add no
  dependencies.
- **Infra/DevOps.** Blast-radius thinking holds for the platform tier
  (it cannot take down paging) but fails twice on the hot path itself:
  the Jev call can *delay* every page (Move 1), and the forwarder is a
  single point of trust for decided pages (Move 3). Ephemeral-infra
  thinking is fine for v0.1 single-box; the WAL same-host constraint is
  satisfied by construction.
- **Data/AI.** Deterministic guardrails over probabilistic models — the
  gate does this well (triple lock, fail-open, uncertainty pages, the
  allowlist as the uncalibrated-early-days anchor). Data pedigree —
  `input_sha256` + `jev_model` per row means a flip is auditable, never
  mysterious. The model advises, never controls — and Move 1 *preserves*
  this: the race timer's default action is passthrough, a deterministic
  rule, not a model output.
- **Product.** Empathy for the 3 AM human — the river shows what the
  system believed and why, with denominators on every chart. Graceful
  degradation — every failure mode degrades to passthrough *except*
  forwarder-down (degrades to log-and-metric: silence) and audit-down
  (currently implicit: must be explicit — audit failure degrades to
  metric + continue, never blocks the forward). Move 3 fixes the first;
  the audit-failure rule is a one-line addition to the gate contract.

---

## 8. The center question — answered

**Is the two-tier architecture right? Yes.** It is the
control-plane/data-plane split with precedent (Kubernetes, Envoy), and
the file-as-bridge is the right mechanism (durability + crash recovery
beat a socket; WAL gives readers-don't-block-writer by documentation,
not by hope).

**Is the audit log the right bridge? Right mechanism, wrong
granularity.** A `decisions` row is a *state snapshot*; what crosses the
bridge must be *events* — because the platform's job is to show what
happened, and "what happened" includes the forward, the flip, and the
late answer, not just the decision.

**What would I change? Three moves, no more.** Everything else in the
frozen architecture survived a genuine attempt to kill it.

---

## 9. CREATIVE APPLICATION — the 3 architectural moves we make differently

### MOVE 1 — Race-to-page: the Jev call leaves the blocking critical path

**The change (Type 1).** The gate starts the Jev call and a timer for
budget **B** (a Type-2 *value*, seeded by Oracle's latency campaign —
e.g. the measured p50 with headroom; the *race* is the Type-1 part). If
Jev answers within B, the disposition applies normally. If the timer
fires first, the alert forwards as **passthrough immediately** — the
deterministic safe default — and when the late Jev answer arrives, it is
written as a **shadow decision event**: it feeds calibration, the tuner,
and flip analysis, but pages nothing. Suppression now requires Jev to be
*both fast and confident* — conservative by construction. Retries
continue in the background for the shadow record; the page never waits
on them.

**Why (not a vibe).** The do-no-harm law as frozen protects the paging
path from the *platform* while the *engine's own* inference call sits
on the critical path with a measured ~11.4s tail against a 70–500ms
spec (pre-mortem scenario C). Precedent: Dean & Barroso, "The Tail at
Scale" (CACM 2013) — a hedged request raced against the 95th-percentile
expected latency cut p99.9 from 1800ms to 74ms at ~2% extra load
(https://www.barroso.org/publications/TheTailAtScale.pdf,
https://research.google/pubs/pub40801/). Our variant races inference
against a paging budget where the "hedge" is the deterministic default
action rather than a second replica — appropriate because there is only
one Jev endpoint, and the safe fallback is *not calling at all*.

**Honest tradeoff.** Slow-Jev periods suppress less — savings dip when
the vendor struggles. That tradeoff is priced, not hidden: the tuner
projects suppression rate *as a function of B*, so the buyer sees the
dollars move with the budget slider. We stop needing Jev to be fast and
start guaranteeing *we* are fast. Side effect, and it's a good one:
shadow mode stops being a flag and becomes the system's normal way of
handling slow inference (§2, deletion ledger).

**Amendment to the frozen laws.** Do-no-harm Law 1 ("the platform never
adds latency to the paging path") is extended: *the paging path's added
latency is bounded by the race budget B.* The kill-the-client test still
passes unchanged (a dead client is just a race the timer always wins).

### MOVE 2 — The audit log becomes an append-only event log

**The change (Type 1).** Replace the single `decisions` row with an
append-only event stream in the same SQLite/WAL file:

- `decision_requested` — input hash, fingerprint, episode identity
- `decision_made` — the three answers, disposition, `jev_model`,
  latency, and the budget outcome (`answered_in_time` | `late_shadow` |
  `error_passthrough`)
- `forward_attempted` / `forward_confirmed` / `forward_failed` — Move 3's
  receipts, closing the loop
- `flip_observed` — same `input_sha256`, different answer across repeats

The platform river becomes a **projection over events**, not a
`SELECT` over rows. A decision with no forward receipt renders as
*decided, delivery unconfirmed* — visible, not lying (pre-mortem
scenario B). Crash-consistency falls out of the design instead of being
wished for.

**Why (not a vibe).** The current schema has the textbook dual-write
problem: the decision (local DB write) and the page (network call to
PD) cannot be atomic, and the schema records only the first half
(Richardson, transactional outbox,
https://microservices.io/patterns/data/transactional-outbox.html;
Debezium's outbox event router). Our engine's local SQLite *is* already
an outbox — it just forgot the second half. Precedent for the shape:
Fowler's event sourcing — *"capture all changes to an application state
as a sequence of events"*; the log is the system of record and the
current state is a derivation
(https://martinfowler.com/eaaDev/EventSourcing.html). We adopt the
append-only discipline we already have and extend it to the full
lifecycle. The audit explorer's flip-audit view and the "prove it" sale
both get strictly stronger: the log now shows not only what we
decided, but what actually happened.

**Migration.** Additive: keep the `decisions` projection as a
materialized view for backward compatibility; the event log is the
source of truth. No platform surface breaks — they read projections,
and projections are disposable by definition.

### MOVE 3 — The forwarder becomes a durable last-mile with no single notification path

**The change (Type 1).** Decided pages enter a **durable outbox table**
(same SQLite file, same WAL) instead of being fired at PagerDuty
inline. A forwarder loop delivers with retry + backoff; every attempt
carries a **stable `dedup_key` derived from our episode identity**, so
retries are idempotent at the vendor — PagerDuty's own docs give us the
primitive (same key appends to the open alert; §5). Delivery is
at-least-once with vendor-side dedup. If an outbox entry ages past **X**
without delivery (the vendor is down, not slow), the forwarder fires
the **secondary channel** — customer-configured, independent of the
primary (e.g. direct SMS via a second provider, or a webhook to the
customer's own fallback) — because "alert the operator" cannot travel
on the broken primary (pre-mortem scenario D).

**Why (not a vibe).** This is the one failure mode in the frozen design
that degrades to *silence*, and silence is the one thing the product
forbids (Law 7, product constitution). "Log + metric" on forward failure
is fail-open theater: there is nothing to fail open *to*. Chesterton's
fence (§5): PagerDuty's entire notification design — escalation chains,
multi-channel delivery — exists because a single notification path
fails at the worst moment. We honor the fence by refusing to have one.
The transactional-outbox relay (Richardson; Debezium) is the mechanism:
the risky network call moves out of the request path into a retryable
loop, and the durable promise ("this page *will* be delivered or
screamed about") lives in the same local transaction as the decision.

**Scope honesty.** Durable outbox + idempotent retry is v0.1-shippable
(it's a table and a loop, stdlib-only). The secondary channel is a
Type-1 *contract* now (outbox schema + secondary-channel interface),
built next — designed, not deferred vaguely. The `forward_confirmed`
event from Move 2 is what lets the river, the analytics, and the
postmortem distinguish "decided" from "delivered" forever after.

---

## 10. What this review deliberately did not change

- The two-tier topology, the SQLite/WAL bridge mechanism, the
  receiver's PD-compatible contract, the correlator stage, the gate's
  policy heart, the three questions and their option sets, the triple
  lock, fail-open, uncertainty-pages, the allowlist, shadow semantics,
  the zero-Jev-calls platform rule, the stdlib-only discipline. Each
  was attacked; each held. That is the point of the exercise.
- Open Type-1 item flagged for the coordinator: **data retention
  policy** — no RFC exists, and retention is irreversible in both
  directions (data deleted too early kills postmortems; data kept too
  long invites regulators). This needs an RFC before the first design
  partner, not after.

---

## Sources (architectural precedents cited)

- Dean & Barroso, "The Tail at Scale," CACM 2013 —
  https://www.barroso.org/publications/TheTailAtScale.pdf and
  https://research.google/pubs/pub40801/ (hedged requests; Move 1)
- PagerDuty developer docs, Events API v2, "Alert De-Duplication" —
  https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/02-Trigger-Events.md
  (dedup_key semantics; Move 3 idempotency; §5)
- C. Richardson, "Transactional Outbox" —
  https://microservices.io/patterns/data/transactional-outbox.html
  (dual-write problem; Moves 2 & 3)
- Debezium, "Outbox Event Router" —
  https://debezium.io/documentation/reference/stable/transformations/outbox-event-router.html
  (outbox relay; Moves 2 & 3)
- M. Fowler, "Event Sourcing" —
  https://martinfowler.com/eaaDev/EventSourcing.html (Move 2)
- M. Fowler, "Audit Log" —
  https://martinfowler.com/eaaDev/AuditLog.html (append-only audit
  discipline; Move 2)
- SQLite, "Write-Ahead Logging" — https://www.sqlite.org/wal.html
  ("readers do not block writers and a writer does not block readers";
  1000-page auto-checkpoint; same-host constraint; §1.6, §3/A4)
- Google SRE Book, Ch. 6, "Monitoring Distributed Systems" —
  https://sre.google/sre-book/monitoring-distributed-systems/ (alert on
  symptoms; every page actionable; §5)
- Internal ground truth: JEV research report §§5.2/7.1/8; ARCHITECTURE.md
  §§1–10; PLATFORM_ARCHITECTURE.md §§1–10;
  `research/sre-field/2026-10-02-escalation-dedup-craft.md` (layered
  storm doctrine, ADR-001/006/007/008 inputs);
  `research/sre-field/2026-10-03-edge-cases-adr001.md` (episode
  semantics, PD/Opsgenie/Alertmanager dedup behavior);
  `research/security-privacy/2026-10-02-webhook-audit-precedents.md`
  (HMAC standard, fast-ACK + idempotent ingest, hash-chained audit as
  staged hardening).

---

*Delivered for the principal-redesign brutal gate. The three moves are
Type 1: each needs its RFC (written alternatives, explicit tradeoffs,
named dissent) before implementation resumes. Aditya's halt on
implementation is respected — this document is design only, no code.*
