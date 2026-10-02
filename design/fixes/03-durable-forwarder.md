# 03 — Durable Forwarder: Implementation-Ready Design (ADR-012)

**Lane 1 (Forge) · principal fix-designs wave · 2026-10-03**
**Status: PROPOSED.** Implements ADR-012. The frozen spec is NOT unfrozen by
this document — application waits for Forge's review and Aditya's verdict
(synthesis §7).
**Judged against:** `design/principal/00-laws.md` (the seven laws).
**Builds on:** `design/principal/01-architecture-rederivation.md` §7 Move 3
(§9 there) and §5 (Chesterton's fence), `design/principal/11-synthesis.md`
§2 hole 3 / §3.6 / §4, ADR-018 (standby/liveness). PagerDuty wire semantics
**verified live against PD's official docs on 2026-10-03** (§4). No other
new fact-finding; no code.

---

## 1. First principles (Law 4)

Why does the forwarder exist? → Because a decided page is a promise to a
human, and promises need durability. Why was "log + metric" the frozen
failure mode? → Because v0.1 treated forwarding as a side effect of
deciding. Why is that wrong at bedrock? → **Deciding and delivering are
different failure domains** (Forge §1.5: Jev can be up while PagerDuty is
down and vice versa). A module boundary drawn where failures decouple is
not overhead — it is the only way the audit log can tell "we decided
wrong" apart from "the vendor was down," which is the difference between
a useful postmortem and a misleading one. And: fail-open is vacuous when
the thing that failed *is* the paging path — "alert the operator" has no
channel when the alerter is broken. The irreducible truth: **the page
must survive the death of any single notification path, including the
primary's, and the system must be able to prove it.**

The irreducible design: **decided pages enter a durable local outbox;
a forwarder loop delivers at-least-once with the vendor's own idempotency
primitive; undelivered past X, an independent secondary channel fires.
No single notification path, ever.**

---

## 2. The outbox — schema and lifecycle

### 2.1 Table (same SQLite/WAL file as the event log — file 02 §2.1)

The shared file is load-bearing: invariants I1/I2 (file 02 §3) need the
decision_made+outbox insert and the receipt+scheduler update each in one
transaction, and one transaction needs one database.

| Column | Type | Meaning |
|---|---|---|
| `outbox_id` | TEXT PK | uuid4 hex. |
| `alert_id` / `fingerprint` / `episode_id` | TEXT | Joins to the decision. |
| `decision_seq` | INTEGER | `events.seq` of the `decision_made` this row delivers. |
| `dedup_key` | TEXT NOT NULL | **Stable, pinned** (§3). The idempotency property. |
| `routing_key_ref` | TEXT NOT NULL | Reference to the secrets entry — **never the raw key**. Pinned at first attempt (§4.3). |
| `payload_frozen` | TEXT NOT NULL | The exact PD-CEF JSON bytes sent on attempt 1. Immutable. |
| `payload_sha256` | TEXT NOT NULL | Integrity of the frozen payload. |
| `status` | TEXT NOT NULL | `queued` \| `in_flight` \| `delivered` \| `dead_letter`. **There is no terminal "secondary fired" status** — the secondary firing never ends primary retries (§5.2); the model makes the forbidden optimization unrepresentable. |
| `priority` | INTEGER | 0 = normal page; 1 = control-plane page (watchdog, evidence-loss, reaper anomalies). Same mechanism, ordered first — one pager, prioritized, not two pagers. |
| `attempt_count` | INTEGER | Incremented per attempt, both channels. |
| `next_attempt_at` | TEXT | Scheduler's decision, wall-clock ISO-8601. Re-read every loop; NTP steps only shift retry timing (safe directions — §6). |
| `lease_at` | TEXT | `in_flight` lease. Startup scan re-queues `in_flight` rows with stale leases (>5 min) — a crashed worker can never strand a page. |
| `secondary_fired_at` | TEXT | Timestamp, not a status. Set when the secondary fires; primary retries continue. |
| `created_at` / `delivered_at` | TEXT | — |
| `max_age_at` | TEXT | `created_at + max_age` (§5.1). Past this ⇒ `dead_letter`. |
| `last_error` | TEXT | Last failure, for the river and the postmortem. |

**UNIQUE(episode_id) WHERE status IN ('queued','in_flight')** — a
duplicate page decision for an already-queued episode **coalesces**:
the duplicate's `decision_made.outbox_id` points at the *existing* row
("this duplicate rides the already-queued page"). No second row, no
second PD alert, no schema change — the join does the work.

### 2.2 The forwarder loop

- **Scheduler:** one thread. Startup scan re-queues stale `in_flight`
  rows, then loops: pick due rows (`next_attempt_at <= now`, priority
  first), hand to the worker pool.
- **Workers:** 4 threads (Type 2), each attempt = one HTTP POST to
  `https://events.pagerduty.com/v2/enqueue` + the I2 transaction
  (receipt event + scheduler-column update, one txn).
- **Single-writer discipline** is preserved: workers serialize their I2
  transactions through the one engine writer (SQLite handles the
  queueing; `SQLITE_BUSY` ⇒ backoff-and-retry inside the worker, never
  a lost receipt).
- Throughput: pages are rare by design (suppression is the product); 4
  workers × ~200 ms per 202 is two orders of magnitude above any
  realistic page rate, and the correlator's storm-collapse sits upstream
  anyway.

### 2.3 What enters the outbox (and what does not)

- Every `decision_made` with disposition `page_now` or
  `page_business_hours` inserts an outbox row **in the same transaction**
  (I1). `suppress` inserts nothing. The timer-win passthrough (file 01
  §4.1) inserts here — the race's output is a promise, and promises go
  in the outbox.
- **Control-plane pages** (watchdog, evidence-loss, reaper anomalies,
  drift pages) enter the same outbox with `priority = 1`. "The guard's
  alarm bypasses the guard" does not mean a second alerter — it means
  the alarm jumps the queue of the one alerter.
- **Enqueue-time validation** (fail-closed on our bugs, fail-open on the
  page): payload size ≤ 400 KB headroom under PD's 512 KB limit
  (verified §4) — oversize ⇒ deterministically trim `custom_details`
  (largest fields first, documented order), record what was trimmed in
  the row; the full-fidelity payload stays in the event log's
  `raw_payloads`. `dedup_key` length ≤ 255 (verified §4) — asserted;
  fallback to the hashed form (§3), still deterministic. If validation
  itself fails impossibly ⇒ the page goes to the secondary channel with
  a `payload_formation_error` annotation. The page still goes out.

---

## 3. The stable `dedup_key` — derivation and pinning

**Derivation** (deterministic, human-debuggable):
`sentinel/{env}/{fingerprint}/{episode_seq}`
e.g. `sentinel/prod/a3f9c1d2e4b5a678/0042`. If the composed form would
exceed 255 chars (pathological env names), fall back to
`sentinel/{sha256(env|fingerprint|episode_seq)[:32]}` — fixed 41-char
form, still deterministic. The derivation is a pure function of the
episode identity: the same episode always yields the same key, across
retries **and** across process restarts (the row stores it, but the
function means a fresh derivation agrees — defense in depth against
row corruption).

**Why this shape:** it is namespaced (`sentinel/` — never collides with
a customer's own integrations' keys), env-qualified (ADR-017's
staging→prod lesson, applied to the vendor key too), and episode-bound
(ADR-001's episode semantics decide continue-vs-renew: reopen-inside-
window reuses the episode ⇒ same key ⇒ appends to the open PD alert;
resolved-then-refired is a fresh episode ⇒ new key ⇒ new PD alert —
which is PD's own documented behavior for post-resolve triggers,
verified §4).

**`dedup_key` stability is a correctness property**, not a
convenience: a test asserts byte-identical keys across 100 simulated
retries and across a process restart (row re-read, compared). If the
key ever varied, retries would create duplicate PD alerts — the exact
failure the outbox exists to prevent.

---

## 4. At-least-once with the vendor's own idempotency — verified semantics and the proof

### 4.1 What PagerDuty actually offers (verified live 2026-10-03)

Source: PagerDuty official developer docs, Events API v2
([Trigger Events](https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/02-Trigger-Events.md),
[Overview](https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/01-Overview.md)):

- **Endpoint:** `https://events.pagerduty.com/v2/enqueue`. The API is
  explicitly **asynchronous**: events are "accepted" and "ultimately
  routed to a service and processed."
- **Response codes and PD's own retry table:**

  | Code | Meaning | PD says retry? |
  |---|---|---|
  | 202 | Accepted — the event has been accepted by PagerDuty | **No** |
  | 400 | Bad Request — check the JSON | **No** |
  | 429 | Too many API calls at a time | **Yes — after some time** (docs: "preferably with a backoff of a few minutes") |
  | 500/5xx | Internal Server Error | **Yes — after some time** |
  | Network error | — | **Yes — after some time** |

- **`dedup_key` semantics:** same key ⇒ applied to the **open** alert
  matching that key ("a trigger log entry is created on an existing
  alert"). Once resolved: a `trigger` with the same key **creates a new
  alert**; `acknowledge`/`resolve` with the same key are **dropped**.
  **Dedup applies only within the same `routing_key` as the original
  trigger.** Max `dedup_key` length: **255 chars.** A trigger *without*
  a key always creates a new alert (auto-UUID).
- **Payload size limit:** 512 KB.

Our retry classifier is a pure function of this table — and the table
is copied into a test fixture, so a PD docs change surfaces as a
**failing test**, not a 3 AM discovery.

### 4.2 `forward_confirmed` — what it means, honestly

`forward_confirmed` is written on **202 with `status == "success"`**
(the 202 body echoes `status`/`message`/`dedup_key`; we assert
`status == "success"` — a 202 with a non-success status is a vendor
contract violation, treated as retryable + loud log). It means:
**"accepted by the vendor's durable intake."** It does NOT mean "the
human's phone rang" — that is PD's internal delivery, past the boundary
every PD customer already lives with. Chesterton's fence (§5 of the
rederivation): we do not re-verify the vendor's internals; we verify
acceptance, and we keep the **independent secondary path** for when the
intake itself is down. The contract is stated at exactly this boundary
— no more, no less.

### 4.3 The idempotency proof

**Claim:** at-least-once delivery with no duplicate *pages*. (Duplicate
trigger *log entries* on the same open alert are possible and harmless
— they are PD's documented dedup behavior, not pages.)

- **Case 1 — retry after a lost response, alert open.** Same
  `dedup_key`, same pinned `routing_key` ⇒ PD applies the retry to the
  open alert as a trigger log entry. No new alert, no new incident, no
  fresh escalation. The human was paged exactly once. ∎
- **Case 2 — retry after a lost response, alert resolved between
  attempts.** Trigger with the same key ⇒ **new alert** ⇒ new page
  (PD's documented post-resolve behavior). This is the *bounded*
  duplicate class: at most one per crash-restart per alert, only when a
  human resolved inside the crash window. It is **loud-safe** (a
  duplicate page, never a missed one), and the retry carries
  `custom_details.sentinel_retry = true` + `attempt_no`, so the new
  alert is self-describing; the event log shows
  crash → retry → `forward_confirmed`, making the postmortem trivial.
  Accepted by design, bounded by construction.
- **Case 3 — routing-key rotation.** Dedup works only within the
  original `routing_key` (verified §4.1) — so the outbox row **pins**
  `routing_key_ref` at first attempt and retries **always** use the
  pinned key, even if config changed mid-flight. The rotation runbook
  requires draining the outbox first (config loader warns on live rows)
  — because a new key with an old `dedup_key` would *not* match the
  open alert and would create a duplicate. Named hazard, procedural
  mitigation, tested in the rotation drill.
- **What is NOT claimed:** exactly-once. PD offers at-least-once +
  dedup, not exactly-once; the design claims exactly what the vendor
  offers. Any builder who writes "exactly-once" in a comment fails
  review.

### 4.4 The 400 case — our bug, loud

A 400 means **our** payload is malformed — retrying is futile. The row
goes `dead_letter` **and** the page fires via the secondary channel
with a `payload_formation_error` annotation, **and** a control-plane
page goes to the engineering on-call (a malformed page is a code bug in
production — it pages the people who can fix it, not just the people
on call for the customer's incident). Terminal for the primary, loud
everywhere else.

---

## 5. Retry policy and the secondary channel

### 5.1 Retry schedule (Type 2 defaults)

Attempt 1 at enqueue (immediate). On retryable failure, delays:
**5 s → 15 s → 45 s → 2 m → 5 m → 15 m → 30 m → 30 m (capped)**,
each ±20 % decorrelated jitter. On **429**: delay =
`max(scheduled, 60 s)`, honoring any `Retry-After` header (PD's docs ask
for "a backoff of a few minutes" — the 60 s floor respects that without
freezing the page for the full backoff on a transient throttle).
**Max age: 24 h** (Type 2) ⇒ `dead_letter`: beyond 24 h the alert is
archaeology, the secondary fired at X, and unbounded retention in the
retry set is unbounded liability. `dead_letter` is **loud**
(control-plane page + checkpoint-visible) — it is a morgue with an
alarm, not a trash can.

### 5.2 The secondary channel — "no single notification path, ever"

**Fires when:** an outbox row is undelivered past **X = 15 min**
(default; Type 2, loader range [5 min, 60 min]). The reasoning, written
once: below ~15 min of total vendor silence, P(transient blip)
dominates and the secondary would buy mostly duplicates; past 15 min,
P(vendor incident) dominates and the independent path is warranted.
The duplicate-on-recovery risk is accepted, annotated
(`custom_details.sentinel_secondary = true`), and bounded — a
duplicate page, never a missed one.

**Structural rule:** `secondary_fired_at` is a timestamp, **not** a
terminal status (§2.1). Primary retries **continue** after the
secondary fires (the dedup_key keeps the PD side clean on recovery).
Any implementation that stops primary retries on secondary-fire is a
P0 defect — §8's pre-mortem is the reason, and the status model makes
the defect unrepresentable.

**Config surface** (per-org; the *interface* is Type 1, the *choice* is
Type 2):

```yaml
secondary_channel:
  type: sms | webhook | email        # exactly one, for now
  provider: <name>                   # MUST be vendor-independent of the primary
  destination: <phone | url | address>
  credential_ref: <secrets ref>      # never inline
  wakefulness: <REQUIRED free-text>  # what this channel guarantees about reaching a human
  test_cadence_days: 30
```

- **The `wakefulness` attestation is required config** — the on-call
  writes, in their own words, what the channel does and does *not*
  guarantee ("SMS via Twilio — does not bypass DND; not equivalent to a
  PD voice call"). A secondary without this sentence is unconfigured.
  This field exists because of §8's pre-mortem: the miss happened where
  nobody wrote down that SMS ≠ voice.
- **Cutover rule (Type 1):** in Stage 2b+ (any suppression authority)
  and Stage 2c (cutover), the engine **refuses to start the forwarder**
  without a configured secondary whose last successful drill is within
  30 days. In shadow mode the secondary is optional (nothing pages).
  Fail-closed on config, as always.
- **The drill is a human test, not a delivery test.** Monthly; writes
  `forward_confirmed(channel=secondary, drill=true)` — but the drill
  only counts as **passed** if the on-call **acks within 10 min**. A
  drill that delivers to a DND phone and calls it success is theater.
  A missed/failed drill is a **paged SLO breach** (control-plane page).
- **Secondary send** renders from the *same frozen payload*
  (summary/severity/source/component) — one page, one content, two
  paths. Provider-positive confirmation ⇒
  `forward_confirmed(channel=secondary)`; failure ⇒
  `forward_failed(channel=secondary)` + bounded secondary retries (3),
  then the watchdog owns it (primary retries continue regardless).

### 5.3 Composition with ADR-018 (not duplication)

ADR-018's **standby direct-to-PD integration** and this file's
**secondary channel** are different mechanisms for different failures —
builders must not conflate them:

| | Standby direct-to-PD (ADR-018) | Secondary channel (this file) |
|---|---|---|
| Failure covered | **Sentinel is dead** (engine crash-loop, host down) | **PagerDuty is down** (vendor incident) |
| Trigger | External health watcher; monthly-drilled runbook | Outbox row undelivered past X |
| Path | Alerts bypass Sentinel entirely, go straight to PD | Page bypasses PD entirely, goes via SMS/webhook/email |
| Liveness assumption | PD is up, we are down | We are up, PD is down |

Both must exist for "no single notification path" to be true in both
directions. If both fail simultaneously, §6's ladder applies.

---

## 6. Failure modes — walked, not listed

**F1. Outbox disk full.** The I1 transaction (decision_made + outbox
insert) fails → file 02's 50 ms commit watchdog fires → the **degraded
ladder**, in order: (1) direct inline PD send, bypassing the outbox,
with an emergency spillover record (stderr + a pre-allocated spill
file — the record the next startup replays into the log); (2) if the
direct send fails, a bounded in-memory ring (1000 entries, Type 2);
(3) if the ring fills, the process **cannot make progress** — it says
so loudly (stderr/syslog) and ADR-018's external health watcher
detects the unhealthy process and triggers the standby direct-to-PD
fallback. The liveness story is the backstop for total local failure —
named, layered, no circularity (the page about the broken pager does
not travel on the broken pager).

**F2. PD API down for hours.** Retries continue on the capped schedule
(§5.1); the secondary fires at X=15 min; the outbox retains everything.
On recovery, the first successful retry **creates** the alert if none
exists (correct — it was never delivered) or appends via dedup_key if a
partial-outage alert exists (correct — no duplicate page). The storm of
recovery-retries is bounded by the per-row schedule + jitter — no
thundering herd (the jitter is load-bearing here, not cosmetic).

**F3. Duplicate delivery on retry.** The proof is §4.3. The residual
(Case 2: retry-after-resolve) is bounded (one per crash-restart per
alert), loud-safe, self-describing (`sentinel_retry`), and
postmortem-trivial. The design accepts it explicitly rather than
claiming it away.

**F4. Payload exceeds PD's 512 KB.** Caught at enqueue (§2.3):
deterministic trim of `custom_details` with the trim recorded; the
full-fidelity payload stays in the event log. Never a 400, never a
surprise.

**F5. `dedup_key` would exceed 255 chars.** The hashed fallback (§3)
keeps it at 41 chars, deterministic. Asserted at enqueue; the assertion
is the test.

**F6. Secondary provider down too.** Bounded secondary retries (3),
then: primary retries continue (the vendor may recover), and a
control-plane page fires about the secondary outage. Both-down is the
**dead_letter-with-screaming** state: the page is durably recorded as
undeliverable, the checkpoint surfaces it, and the design states
plainly that there is no fourth path — instead of inventing one.

**F7. A builder "optimizes" the retry into the hot path.** The outbox
row is written on the hot path (one txn, sub-ms); the *retry loop* is
never on it. Any retry logic found in the gate/dispatcher path is a P0
defect — the file-as-bridge (Forge §1.7) is what keeps the platform
(and the retry loop) incapable of slowing a page.

**F8. NTP steps vs `next_attempt_at`.** The scheduler re-reads the
clock each loop and compares — a backward step delays retries (safe
direction), a forward step fires them early (harmless — it is only a
retry). No deadline arithmetic that can break; the race's monotonic
discipline (§3.5 of file 01) is not needed here because a retry is not
a promise.

---

## 7. Alternatives considered — and why rejected

| Alternative | Why rejected |
|---|---|
| Hot-standby forwarder process instead of the outbox | Same fate-sharing class (same disk, same config, same binary) without durability. A standby that shares the primary's fate is redundancy theater. Keep as a *future complement*, not a substitute. (ADR-012 alt (a)) |
| Page-on-forward-failure via the same path | Circular: "alert the operator" on the broken alerter is silence with extra steps. (ADR-012 alt (b)) |
| Claim exactly-once delivery | PD offers at-least-once + dedup, not exactly-once (§4.1). Claiming more than the vendor offers is a lie the first incident would expose. |
| Synchronous forward with retries on the hot path | The frozen flaw: retries hold the page hostage (pre-mortem scenario C). The outbox moves the risky network call out of the request path — Richardson's transactional outbox, applied literally. |
| Rely on PD's own redundancy instead of a secondary | Fate-sharing: when PD is down, PD's redundancy is what we are waiting on. Chesterton's fence (rederivation §5): the incumbents built multi-channel notification *because the primary fails at the worst moment*. The secondary must be vendor-independent. |
| Stop primary retries once the secondary fires ("avoid duplicates") | **The pre-mortem's bug** (§8). Duplicates on recovery are bounded and loud-safe; a stopped primary is a removed redundancy. The status model (§2.1) makes this unrepresentable. |
| A second, separate pager for control-plane alerts | Two alerters = two failure domains to operate at 3 AM. One outbox, prioritized (Law 7 SE: smallest sufficient system). |

---

## 8. Type 1 / Type 2 register (this file's choices)

**Type 1** (irreversible — the delivery contract; RFC-grade):

| Choice | Why Type 1 |
|---|---|
| The outbox as the delivery contract (decided pages enter it; nothing pages around it except the degraded ladder) | The promise-keeping contract with the customer |
| At-least-once + stable `dedup_key` (never exactly-once) | Claims exactly what the vendor offers; the idempotency property |
| `dedup_key` derivation formula + pinning + the 255-char assertion | Correctness property; a varying key is duplicate pages |
| `routing_key_ref` pinned at first attempt | PD dedups only within the original routing key — unpinned retries break idempotency |
| 202+`status=="success"` ⇒ `forward_confirmed`; the PD retry table as the classifier | The vendor contract, executable |
| Secondary channel interface + "cutover requires a drilled secondary" | No single notification path, ever — enforced, not advised |
| Secondary fire never stops primary retries (timestamp, not terminal status) | The redundancy the design promises; §8's pre-mortem |
| Payload frozen at enqueue; enqueue-time validation | What was sent is immutable and auditable |
| The `wakefulness` attestation as required config | The urgency semantics the pre-mortem proved must be written down |

**Type 2** (reversible — values, choices, operations):

| Choice | Why Type 2 |
|---|---|
| Backoff schedule, 429 floor (60 s), max age (24 h), X (15 min, range [5, 60]) | Tunable policy numbers on a fixed contract |
| Worker pool size (4), scheduler cadence, ring size (1000), lease (5 min) | Capacity numbers, not semantics |
| Secondary channel type/provider per org | Customer choice on a fixed interface |
| Drill cadence (30 d), drill ack window (10 min) | Operational SLOs |
| Payload trim order, 400 KB headroom | Implementation details under the 512 KB vendor limit |

---

## 9. CREATIVE APPLICATION — what a builder does differently

1. **The builder treats the outbox as the only exit.** Every page the
   system ever sends goes through an outbox row — no inline sends, no
   "just this once" direct calls, except the degraded ladder, which is
   itself a reviewed, fault-injection-tested code path (§6 F1), not a
   hack. The day a builder adds a second exit, the delivery contract
   has two truths.
2. **The builder copies PD's retry table into a test fixture.**
   `test_retry_classifier` asserts our mapping against the documented
   table row-for-row (202→no, 400→no, 429→yes, 5xx→yes, network→yes).
   When PD changes their docs, the test fails in CI — the vendor's
   contract drift becomes a build break, not a 3 AM discovery. This is
   what "live sources cited" means in practice: the citation is
   executable.
3. **The builder writes the dedup_key stability test first.** One
   hundred simulated retries, one process restart, byte-identical keys
   — before the retry loop itself is written. The idempotency property
   is the foundation; the loop is the decoration.
4. **The builder onboards the secondary channel by interviewing the
   on-call**, not by filling a form. The `wakefulness` sentence is
   elicited ("what happens on your phone at 3 AM when this fires?"),
   and the drill's 10-minute ack window is what makes the answer
   checkable. The config file ends up containing a sentence a human
   wrote about their own sleep — which is exactly the right level of
   honesty for a paging system.
5. **The builder never writes the "stop primary on secondary" line.**
   The status model has no place to put it (§2.1) — and when the next
   engineer asks why, the answer is §8's pre-mortem, linked in the
   schema comment. The pre-mortem is documentation that prevents its
   own recurrence.

---

## 10. PRE-MORTEM — "it is one year from now and this mechanism caused a missed SEV1"

*October 2027. A customer's SEV1 — a cascading payments outage at
01:20 — never woke anyone. The postmortem finds the `decision_made`
at 01:20:04, the outbox row queued at 01:20:04, and PagerDuty in a
regional incident from 01:12 to 01:34 (22 minutes). The secondary SMS
fired at 01:35. The on-call's phone was on Do-Not-Disturb for SMS.
What failed, mechanically?*

Three links — two of them violations of this design, one of them a gap
the design now closes:

1. **A builder stopped the primary retries.** Nine months earlier,
   "to avoid duplicate pages on recovery," a well-meaning engineer
   added: `if secondary_fired: cancel_primary_retries()`. The status
   model in this design (§2.1) has no terminal "secondary fired"
   state precisely to make this line unwritable — but the builder
   implemented it as a scheduler filter ("skip rows with
   `secondary_fired_at` set"), bypassing the model. PD recovered at
   01:34. No retry ever ran. **No PD incident was ever created** for a
   SEV1 the system had correctly decided to page.
2. **The SMS went to a phone that sleeps through SMS.** The
   `wakefulness` attestation (§5.2) was filled with the default
   placeholder ("SMS delivery") during a rushed onboarding and never
   corrected. The monthly drill *passed* every month — because the
   drill only checked delivery receipts, not human wakefulness. The
   design's 10-minute-ack rule existed in this document but was
   implemented as "ack *or* delivery receipt," and nobody noticed the
   weakening.
3. **The composition was never tested.** No game-day ever ran "PD down
   22 minutes during a SEV1" — the drill tested the secondary in
   isolation, on a quiet Tuesday, and the recovery path (PD comes back,
   retries resume, dedup keeps it clean) was exercised by no test at
   all.

*What the design already says about each link:* the status model makes
"stop on secondary" unrepresentable (§2.1 — and the schema comment
links this pre-mortem); the `wakefulness` attestation is **required**,
human-written config, and the drill passes only on **human ack within
10 minutes** (§5.2 — a delivery receipt is not an ack); the recovery
path gets a chaos test (kill PD for 25 min mid-storm, assert the
secondary fires at X, assert retries resume on recovery, assert exactly
one PD alert per dedup_key at the end). **The pre-mortem's lesson: the
forwarder's failure mode is never the vendor — it is always us,
weakening our own redundancy one reasonable-sounding line at a time.
The design's job is to make the weakening unrepresentable, untestable-
to-skip, and loud when attempted.**

---

## Sources (live-verified 2026-10-03)

- PagerDuty developer docs, Events API v2 — **Trigger Events**
  (dedup_key semantics, 255-char limit, routing_key scoping, post-resolve
  behavior) —
  https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/02-Trigger-Events.md
- PagerDuty developer docs, Events API v2 — **Overview** (response
  codes & retry table, async-acceptance model, 512 KB limit, dynamic
  throttling, 429 backoff guidance) —
  https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/01-Overview.md
- C. Richardson, "Transactional Outbox" —
  https://microservices.io/patterns/data/transactional-outbox.html
  (the outbox pattern; the forwarder loop is the relay)
- Debezium, "Outbox Event Router" —
  https://debezium.io/documentation/reference/stable/transformations/outbox-event-router.html
  (outbox relay precedent)
- Internal: `design/principal/01-architecture-rederivation.md` §7 Move 3
  (§9 there) and §5 (Chesterton's fence — PD's multi-channel
  notification design), `design/principal/11-synthesis.md` §2.3/§3.6,
  `design/principal/12-adr-deltas.md` ADR-012/018; files
  `01-race-to-page.md` (§4.1 — the timer-win path inserts here) and
  `02-event-log.md` (§3 invariants I1/I2, §4 — the commit watchdog that
  engages the degraded ladder)
