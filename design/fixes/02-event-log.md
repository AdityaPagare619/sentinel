# 02 — Append-Only Event Log: Implementation-Ready Design (ADR-011)

**Lane 1 (Forge) · principal fix-designs wave · 2026-10-03**
**Status: PROPOSED.** Implements ADR-011. The frozen spec is NOT unfrozen by
this document — application waits for Forge's review and Aditya's verdict
(synthesis §7).
**Judged against:** `design/principal/00-laws.md` (the seven laws).
**Builds on:** `design/principal/01-architecture-rederivation.md` §7 Move 2
(§9 there), `design/principal/11-synthesis.md` §2 hole 2 / §3.3 / §4,
ADR-010 (shadow event), ADR-012 (forward receipts), ADR-023
(counterfactual). No new fact-finding; no code.

---

## 1. First principles (Law 4)

Why does the audit log exist? → Because the product *is* the trust
layer — every platform surface (river, calibration, shadow reports,
postmortems) reads it. Why did the frozen schema record *decisions*? →
Because v0.1 was built in a night and a decision row was the obvious
shape. Why is a row wrong? → Because the system's promise is not "we
decided" but "the page left the building" — and a row records only the
first half of a two-half reality (local DB write + vendor network call)
that **cannot be atomic**. The dual-write problem is not a bug in our
code; it is a property of distributed systems (Richardson, transactional
outbox). The honest response is not to wish for atomicity but to make
the *absence* of the second half visible: record the lifecycle as
events, and let "decided but not delivered" be a first-class, rendered,
paged state instead of a silent lie.

The irreducible design: **the log is an append-only event stream; the
river is a projection; `forward_confirmed` is the only event that means
"paged."** Everything else is commentary.

---

## 2. Event taxonomy and field-level schemas

Seven event types. The first five are Forge's Move 2 plus the ADR-010
late-answer type; `checkpoint` is added here (the chain must cover the
checkpoints — no out-of-band truth). The taxonomy is **Type 1** — every
downstream consumer (river, calibration, postmortems, the verifier)
reads this vocabulary.

### 2.1 Envelope (all events — Type 1)

| Column | Type | Meaning |
|---|---|---|
| `seq` | INTEGER PK AUTOINCREMENT | **Authoritative order.** The log's truth is append order, never wall-clock. Survives NTP jumps by construction. |
| `event_id` | TEXT UNIQUE | uuid4 hex. Deduplication key for at-least-once writers. |
| `schema_v` | INTEGER | Envelope version, starts at 1. Bump ⇒ migration RFC. |
| `ts` | TEXT | UTC ISO-8601 with ms. **Wall-clock, for humans only.** Never used for ordering. |
| `actor` | TEXT | Who wrote it: `engine` \| `forwarder` \| `watchdog` \| `checkpoint` \| `calibration` \| `migration`. |
| `type` | TEXT | One of the seven below. |
| `alert_id` | TEXT | Receiver-assigned alert identity. |
| `fingerprint` | TEXT | 16-hex identity (includes env/cluster — ADR-017). |
| `episode_id` | TEXT | Correlator episode identity. |
| `outbox_id` | TEXT | Joins decision/forward events to the outbox row (file 03). NULL where inapplicable. |
| `body` | TEXT | JSON document — the per-type schema below. |
| `prev_hash` | TEXT | sha256 of the previous row's canonical form (§5). `"GENESIS"` for seq 1. |
| `row_hash` | TEXT | sha256(canonical(envelope-without-hashes + body) \|\| prev_hash). |

**Storage:** one SQLite table `events` in the engine's WAL-mode file —
the *same file* as the outbox (file 03). The shared file is load-bearing
(§3, alternatives): the decision_made+outbox atomicity proof needs one
transaction, and one transaction needs one database.

**Indexes** (Type 2 — query planning, not truth):
`(fingerprint, seq)`, `(alert_id, seq)`, `(type, seq)`.

### 2.2 `decision_requested` — the durability boundary

Written by the **receiver**, committed **before** the 202 response to
the sender (§3, Pair A).

| Body field | Type | Meaning |
|---|---|---|
| `input_sha256` | string | Hash of the normalized inbound payload. Idempotency/correlator key. |
| `source_integration` | string | Which receiver integration (PD-mirror route, webhook name). |
| `severity_in` | string | Severity as received. |
| `received_ts` | string | Receiver's wall-clock receipt time. |
| `raw_payload_bytes` | int | Size of the stored raw payload (the payload itself lives in a `raw_payloads` side table keyed by `input_sha256` — keeps the event row small; Type 2). |

### 2.3 `decision_made` — the disposition

Written by the **gate** (dispatcher loop), in the **same transaction**
as the outbox row for page dispositions (§3, Pair C).

| Body field | Type | Meaning |
|---|---|---|
| `input_sha256`, `fingerprint`, `episode_id` | | Joins to the request. |
| `jev_model` | string \| null | Exact pinned version that answered; null when no Jev call ran (structural-bar passthrough, reaper redrive). |
| `q1_reported` | number \| null | Reported P(p1), quantized as received. |
| `q2_team` | string \| null | Routing answer. |
| `q3_confidence` | number \| null | Reported confidence. |
| `q3_disposition` | string \| null | The model's disposition answer. |
| `disposition` | string | `passthrough` \| `page_now` \| `page_business_hours` \| `suppress`. |
| `budget_outcome` | string | `answered_in_time` \| `timer_won` \| `timer_won_shed` \| `error_passthrough` \| `reaper_redrive` \| `structural_passthrough`. The race's vocabulary — the tuner and watchdog read this, never the raw latency alone. |
| `latency_ms` | number \| null | Jev call latency; null unless `answered_in_time`. |
| `timer_fired_at_ms` | number \| null | Monotonic ms elapsed when the timer won. |
| `lock_evaluation` | object | Per-lock verdicts: `prob: {verdict, detail}`, `conf: {verdict, detail}`, `allowlist: {verdict, detail}` — each `pass`/`fail`/`stale` with the evidence (fit id + p̂_upper, attestation ids, TTL remaining). |
| `freshness` | object | `fit_as_of`, `threshold_attested_as_of`, `allowlist_attested_as_of` — the proofs, dated (ADR-014). |
| `threshold_counterfactual` | object | ADR-023: disposition the gate would have taken at each configured threshold preset, evaluated deterministically at event-write time. |
| `outbox_id` | string \| null | Set for page dispositions; null for suppress. |
| `links.decision_requested_seq` | int | The request this answers. |

### 2.4 `shadow_decision` — the late answer (ADR-010; canonical schema)

Written by the **detached inference worker** (§6 of file 01). Never
pages, never suppresses. Schema is defined canonically in file
`01-race-to-page.md` §4.2 and repeated here verbatim for the log RFC:

Envelope per §2.1, `actor = "engine"`. Body: `jev_model`,
`q1_reported`, `q2_team`, `q3_confidence`, `q3_disposition`,
`latency_ms` (**full** call latency — the vendor-tail sample),
`budget_ms`, `timer_fired_at_ms`, `decided_disposition`
(`"passthrough"`), `shadow_disposition` (`suppress`|`passthrough` —
evaluated by the shared policy kernel, not a copy),
`would_have_suppressed` (bool), `lock_evaluation` (per-lock
pass/fail/stale at answer time), `links.decision_made_seq` (the
timer-win decision this shadows). On late error: q-fields null,
`error_class` set.

### 2.5 `forward_confirmed` / `forward_failed` — the delivery receipts

Written by the **forwarder loop** (file 03), always in the **same
transaction** as the outbox scheduler-column update (§3, Pair D). The
`channel` field (not new event types) distinguishes paths:

**`forward_confirmed`** body: `outbox_id`, `channel`
(`primary`|`secondary`), `attempt_no`, `vendor_status` (202),
`vendor_message`, `vendor_dedup_key_echo`, `latency_ms`,
`drill` (bool, default false — the monthly secondary-channel drill
writes a confirmed event with `drill=true`; no new type needed).

**`forward_failed`** body: `outbox_id`, `channel`, `attempt_no`,
`error_class` (`retryable`|`terminal`|`vendor_down`), `http_status`
(nullable — null for network errors), `error_detail`,
`next_attempt_at` (the scheduler's decision, recorded as an event so a
postmortem can see *why* the retry is at 03:12 and not 03:02).

### 2.6 `flip_observed` — the non-determinism record

Written by the **calibration pipeline** (off the hot path,
`actor = "calibration"`) when two answers for the same `input_sha256`
differ. Body: `input_sha256`, `first_seq`, `second_seq`,
`differing_field` (`q1`|`q3`|`disposition`), `first_value`,
`second_value`, `jev_model` (both — a flip *across* model versions is a
drift signal, not a flip; the schema distinguishes).

### 2.7 `checkpoint` — the seal, in-band

Written by the **checkpoint job** (`actor = "checkpoint"`), hourly
(Type-2 cadence). Body: `head_seq`, `head_hash`, `event_count`,
`window_start_ts`, `window_end_ts`, `hmac_hex` (§5), `sink_uri`,
`sink_push_ok` (bool). The chain covers the checkpoints — a verifier
never trusts an out-of-band manifest.

---

## 3. The write-ordering proof — "says paged, never paged" is impossible *as a silent state*

**THE LAW.** `forward_confirmed` is the ONLY event that means "the page
left the building." `decision_made(disposition=page_*)` means "we
decided to page." The river MUST render these differently, and the
following invariant is enforced by transaction boundaries, not by code
paths:

> **Invariant I1:** `decision_made` with a page disposition and its
> outbox row commit in ONE transaction. (Decision ⇒ deliverable.)
> **Invariant I2:** `forward_confirmed`/`forward_failed` and the outbox
> scheduler-column update commit in ONE transaction. (Receipt ⇒ queue
> agrees.)
> **Invariant I3:** The receiver 202s ONLY after `decision_requested`
> commits. (Promise ⇒ durable.)

**THE RIVER RULE.** A `decision_made(page_*)` with no matching
`forward_confirmed` (same `outbox_id`) older than **T_sla = 60 s**
(Type 2) renders as a **PAGED ANOMALY** — red, pinned to the top of the
river — and fires the forwarder's watchdog: an **unsuppressible
control-plane page** via the secondary channel ("the guard's alarm
bypasses the guard" — Vault). The transient state (crash windows below)
can exist; the *silent* state cannot.

### Crash walk — every pair

**Pair A — receiver commit → 202.**
Crash *after* commit, *before* 202: the sender retries (timeout or
explicit retry). The receiver writes a *second* `decision_requested`
for the same `input_sha256` — an honest duplicate, not a corruption.
The correlator collapses it via fingerprint dedup; the projection shows
one episode; the log shows both receipts. No silence: the first commit
was durable.
Crash *before* commit: the sender got connection-refused/timeout and
retries. Nothing was promised; nothing is owed.

**Pair B — `decision_requested` → `decision_made` (dispatcher/gate).**
Crash between: orphan `requested`. The **reaper** closes it: a loop
sweeps `decision_requested` older than **R = 30 s** (Type 2) with no
`decision_made`, and redrives each through the gate as **passthrough**
(fail-open) with `budget_outcome = "reaper_redrive"`. The sweep runs at
engine startup before the dispatcher resumes, then periodically.
Safety of the reaper: the gate's worst-case processing time is
B+ε+commit ≈ 2 s ≪ 30 s (the race's *bounded latency* is what makes the
reaper's age test sound — a composition worth naming: without the race,
"older than R" could not distinguish orphan from slow).
Result: the alert pages. The crash window is 30 s of delay, not silence.

**Pair C — `decision_made` + outbox insert → forwarder pickup.**
Crash *before* commit: nothing durable beyond `requested` → Pair B's
reaper redrives → pages.
Crash *after* commit, *before* pickup: the outbox row exists. The
forwarder's **startup scan** picks up every non-delivered row. The river
shows "decided, delivery unconfirmed" — visible, never "paged."
*This is the pair that killed the frozen schema* (pre-mortem scenario
B): the old row said `page_now` with no delivery record and the river
lied for 40 minutes. Under I1 the lie is structurally impossible — the
schema has no "paged" state except `forward_confirmed`.

**Pair D — forward attempt (HTTP) → receipt write.**
Crash *after* PD 202'd, *before* we write `forward_confirmed`: restart
→ startup scan → retry with the **same `dedup_key`, same `routing_key`**
→ PD finds the open alert → "trigger log entry on the existing alert,"
no new page (vendor semantics — file 03 §4, verified against PD docs)
→ 202 → `forward_confirmed`. The human was paged exactly once.
Crash *before* any response: identical retry path; PD saw it zero or one
times — the dedup_key makes both cases safe.
Crash *between* `forward_failed` and the scheduler update: impossible —
I2 puts them in one transaction. The event log and the work queue can
never disagree about what happened; that agreement is a database
property, not a code convention.

**Pair E — `forward_confirmed` → anything.** Terminal. Nothing follows
except projections. A crash here loses nothing — the receipt is durable
and the outbox row is `delivered` in the same transaction.

**Pair F — `shadow_decision` → anything.** Informational. Loss is
bounded by the evidence-loss rule (§4): a dropped shadow event is
counted, and sustained loss pages. Calibration degrades gracefully; the
paging path never depends on it.

**Pair G — `flip_observed`.** Written off the hot path by calibration.
A crash loses at most one derived observation; the raw answers it was
derived from are already durable. Re-derivable by replay — the flip
pipeline is a pure function of the log, which is why it may live off
the hot path at all.

---

## 4. WAL discipline — "off the hot path" made precise

The synthesis says "WAL off the hot path (never block >50 ms)." A
builder will read that as "do event writes asynchronously" — which
**breaks I1** (the decision_made+outbox atomicity needs one synchronous
transaction). The precise reading, stated once so the RFC never has to
be re-argued:

- **ON the hot path:** the transactional SQLite INSERT (events +
  outbox scheduler updates, one txn). Typical cost: sub-millisecond
  local WAL append. WAL mode is what makes this safe — readers never
  block the writer (sqlite.org). The hash chain is computed here too:
  sha256 over a small canonical JSON is microseconds (CI asserts p99
  event-write under 5 ms — measured, not assumed).
- **OFF the hot path:** checkpointing (background thread:
  `PRAGMA wal_checkpoint(TRUNCATE)` on schedule + WAL-size tripwire),
  fsync pacing beyond the commit itself, sink pushes (hourly checkpoint
  thread), chain *verification* (a read path — any builder who puts
  verification in the write path fails review; see §9).
- **The 50 ms rule:** the hot-path commit is wrapped in a watchdog. If
  COMMIT exceeds **50 ms** (~50× normal — only a sick disk does this),
  the hot path **abandons the wait** and takes the degraded path
  (direct inline send + emergency spillover record — file 03 §6), and
  the evidence-loss machinery fires.
- **Evidence loss is a paged event.** Any dropped event (a full handoff
  queue in the checkpoint/sink threads), any commit-watchdog trip, any
  checkpoint-push failure for >2 consecutive hours → an **unsuppressible
  control-plane page** via the outbox priority lane: "audit degraded:
  N events unguaranteed in window W." A blind trust engine is worse
  than a loud one. If the outbox itself is unwritable (disk full), file
  03's degraded ladder applies and ADR-018's external health watcher is
  the backstop — layered, named, no circularity (the page about the
  broken pager does not travel on the broken pager).
- **The A4 adversary** (stuck platform reader grows the WAL until the
  disk fills — Forge §3): platform reads use **short-lived connections**
  — never hold a read transaction across an SSE heartbeat. Enforced by
  an integration test that fails the build if any platform read path
  holds a transaction >5 s. Plus the WAL-size tripwire metric → pages
  the operator *before* the disk does. The checkpoint thread uses a
  separate connection; on `SQLITE_BUSY` it skips the round and retries
  — it never blocks the writer.

---

## 5. Hash chaining and signed checkpoints — the customer holds the seal

**Chain.** Canonical form: JSON with sorted keys, no whitespace, UTF-8,
of the envelope-minus-hashes + body. `row_hash =
sha256(canonical || prev_hash)`; genesis `prev_hash = "GENESIS"`. The
engine's writer is the single writer (SQLite single-writer is a feature
here — no merge conflicts, ever). Verification replays from genesis or
from the last customer-verified checkpoint; the first broken `seq` is
reported. Corruption and tamper present identically — the design
reports, it does not distinguish.

**Checkpoints.** Hourly (Type-2 cadence), the checkpoint job signs
`(head_seq, head_hash, event_count, window)` and pushes to the
**customer-controlled sink**: an S3/GCS bucket, an HTTPS webhook to the
customer's SIEM, or a local directory the customer's own shipper reads
(Type 2 — the org's choice; the *contract* — signed, hourly, pushed —
is Type 1). A `checkpoint` event is written into the log itself (§2.7).

**The signature primitive — stated honestly.** stdlib-only gives us
**HMAC-SHA256** (no Ed25519 in the stdlib). The HMAC key is generated
**on the customer's side at onboarding** and provided to Sentinel; the
customer holds the only authoritative copy. What the HMAC buys:
(i) integrity of the sink copy in transit/at rest — a third party or a
compromised platform tier cannot forge checkpoints; (ii)
non-equivocation — we cannot show two different heads for the same seq
without the customer detecting it. What it does NOT buy: protection
against a fully compromised engine (which holds the key to sign — but a
fully compromised engine could simply stop logging; the signature was
never the defense against that). An Ed25519 upgrade is the documented
future path *when the stdlib-only discipline is relaxed* — flagged, not
silently substituted. A principal reviews the claim, not the
algorithm's reputation: we claim exactly (i) and (ii), nothing more.

**Sink failure** for >2 consecutive hours → control-plane page (the
seal must be verifiable; a broken sink is a trust outage, and trust
outages page).

---

## 6. Migration — v0.1 rows become events without lying

v0.1's `decisions` table has rows: decision recorded, forward never
recorded. The migration (one-time, at upgrade):

1. Create `events` (+ envelope, §2.1).
2. For each v0.1 row, backfill **one** `decision_made` event:
   `body.migrated_from = "v0.1-decisions"`,
   `budget_outcome = "pre_race_unknown"`, `actor = "migration"`,
   and **no `forward_confirmed`** — we never observed the forward, and
   synthesizing a receipt for history we did not witness would be the
   exact lie this design exists to kill. (Type 1: the migration never
   synthesizes receipts.)
3. Rename the old table to `decisions_v0_1`, read-only, kept for one
   retention window (O-1 will decide the window; the rename, not the
   policy, is this file's business).
4. Provide a `decisions` **SQL VIEW** over `events` for platform queries
   not yet migrated — disposable by definition; projections are never
   the truth. (Type 2: the view's shape.)
5. The river renders migrated rows as **"decided (pre-event-log era) —
   delivery unconfirmed"** — honest, grey, never green.

---

## 7. Alternatives considered — and why rejected

| Alternative | Why rejected |
|---|---|
| Write-ahead-then-forward with a reconciliation job | Reconciliation *is* the event log with extra steps: a second code path that re-derives what the log would have said. One mechanism, not two. (ADR-011 alt (a)) |
| Keep rows + add a nullable `forward_receipt` column | The receipt *is* the event. A nullable column reintroduces the ambiguity the design kills: does NULL mean "not yet" or "never"? The ambiguity is the bug. (ADR-011 alt (b)) |
| Two-phase commit with PagerDuty | No XA over a vendor HTTP API — absurd on its face. The vendor gives us 202-accepted + dedup_key; the design is built from what the vendor actually offers (file 03 §4). |
| Outbox in a separate DB/file from the event log | Loses the single-transaction atomicity that invariants I1/I2 depend on. The shared SQLite file is load-bearing, not incidental. |
| Fully async event writes on the hot path (fire-and-forget queue) | Breaks I1: decision_made and the outbox row must commit atomically, and atomicity needs the synchronous transaction. "WAL off the hot path" is satisfied by the 50 ms watchdog + background checkpointing (§4), not by abandoning atomicity. A builder WILL propose the queue; this row is the pre-emptive answer. |
| Blockchain / ledger database | The threat model is postmortem-verifiability and customer audit, not Byzantine consensus. A hash chain in SQLite gives the tamper-evidence without the ops burden. Law 7 (SE): the smallest sufficient system wins. |
| Chain verification in the write path ("for integrity") | Verification is O(chain) — putting it per-write is a latency bomb (see §9). Integrity is *produced* by the write path and *checked* by read paths. The separation is structural. |

---

## 8. Type 1 / Type 2 register (this file's choices)

**Type 1** (irreversible — the durable-truth format; RFC-grade):

| Choice | Why Type 1 |
|---|---|
| The seven event types + envelope schema | Every downstream consumer reads this vocabulary; changing it orphans history |
| Invariants I1/I2/I3 (transactional write ordering) | The proof that "says paged, never paged" is impossible as a silent state |
| `forward_confirmed` as the only "paged" truth; the river rule + watchdog | The visible invariant; the anomaly that pages |
| Reaper redrive as passthrough (fail-open) with `reaper_redrive` outcome | The crash-window closer; its direction is the safety policy |
| Hash-chain columns (`prev_hash`, `row_hash`) + in-band `checkpoint` events | Tamper-evidence is structural, not a feature flag |
| Signed hourly checkpoints to a customer-controlled sink (the contract) | The trust seal; the customer holds it |
| Migration never synthesizes `forward_confirmed` | Synthesizing receipts would be the exact lie the design kills |
| 50 ms commit watchdog + evidence-loss-pages rule | The bound that keeps the WAL honest without breaking atomicity |

**Type 2** (reversible — values, surfaces, operations):

| Choice | Why Type 2 |
|---|---|
| Reaper age R = 30 s; sweep cadence | Timing bound derived from the race's latency guarantee; tunable |
| River anomaly SLA T_sla = 60 s | Rendering threshold, not truth |
| Checkpoint cadence (hourly); sink type per org | Operational choices on a fixed contract |
| HMAC-SHA256 now; Ed25519 later | Primitive swap that preserves the claimed properties (i)+(ii) |
| Index set; `raw_payloads` side table; the `decisions` VIEW | Query planning and compat shims |
| p99 event-write CI bound (5 ms) | Performance budget, re-measurable |

---

## 9. CREATIVE APPLICATION — what a builder does differently

1. **The builder stops thinking in rows and starts thinking in
   appends.** There is no UPDATE to an event, ever — not for
   corrections, not for backfills, not for "oops." A correction is a new
   event that references the old `seq`. The day a builder reaches for
   UPDATE on `events`, the design has failed to transfer; the code
   review checklist names it explicitly.
2. **The builder writes the river as a fold, not a SELECT.** The
   projection function `render(alert_id) -> row` reduces the event
   stream; it is a pure function, tested against scripted event
   sequences (including every crash pair in §3 — each pair gets a
   fixture: "crash here, assert the river shows *unconfirmed*, never
   *paged*"). The fixtures are the executable form of the ordering
   proof.
3. **The builder treats the 50 ms watchdog as a load-bearing test
   target**, not a comment: CI runs the hot path against a
   fault-injecting filesystem (slow commits) and asserts the degraded
   path engages and the evidence-loss page fires. The watchdog is only
   real if it has been seen firing.
4. **The builder never adds an eighth event type casually.** New types
   are Type-1 vocabulary changes — they need the RFC, because every
   projection, the verifier, and the customer's SIEM parsing must learn
   the new word. The `drill` flag on `forward_confirmed` (§2.5) is the
   worked example of the preferred alternative: a field, not a type.
5. **The builder gives the customer the verifier on day one.** The
   one-command chain verifier ships with onboarding, against the
   customer's own sink — because the seal the customer cannot check is
   theater, and the design's whole point is checkable truth.

---

## 10. PRE-MORTEM — "it is one year from now and this mechanism caused a missed SEV1"

*October 2027. A customer's SEV1 — a region-wide API outage at 03:40 —
never paged. The postmortem finds the alert reached the gate at 03:40:11
and a `decision_made(page_now)` was committed at 03:40:12. The page
never left. What failed, mechanically?*

Four links composed — each a named mitigation in this design, each
quietly undone over the year:

1. **The platform team reverted short-lived reads.** Eight months in, a
   platform builder "optimized" the river's SSE stream to hold one
   long-lived read transaction ("fewer round trips, smoother stream").
   The integration test that forbade >5 s transactions (§4) was marked
   `skip` during a flaky-CI week and never re-enabled. The WAL grew
   unchecked for weeks — 40 GB by October.
2. **The disk filled at 03:37.** The WAL-size tripwire metric existed
   but its alert route had been repointed during an on-call rotation
   change to a mailing list nobody reads. The hot-path commit started
   hitting the 50 ms watchdog — which *did* fire, engaging the degraded
   path (direct inline send). So far the design held.
3. **The degraded path had never been load-tested.** The direct-send
   code leaked one socket per alert under the fault-injecting CI that
   §9 demands — but that CI job was the same skipped one from link 1.
   At 03:40, mid-storm, the process hit fd exhaustion and died mid-send.
   The `decision_made` was durable; the page was not.
4. **The external health watcher never drilled.** ADR-018's monthly
   fallback drill was skipped four times ("we'll do it after the
   release"). The standby direct-to-PD integration's routing key had
   expired silently nine months earlier. When the engine died, nothing
   watched it, and the bypass it would have triggered pointed at a dead
   key. The SEV1 never entered any system.

*What the design already says about each link:* short-lived platform
reads are a **tested invariant**, not a guideline (§4 — the test is
release-blocking, not skippable); the WAL tripwire pages a *human*,
never a mailing list (§4); the degraded path is exercised by
fault-injection CI as a condition of its existence (§9); the fallback
drill is a **paged SLO** — a missed drill pages the operator the way a
missed backup pages an SRE team. **The pre-mortem's lesson is not a new
mechanism; it is that every mitigation in this file degrades from
"tested invariant" to "documentation" the moment its test is allowed to
skip — and the design marks which tests are load-bearing so the year
from now has a checklist, not a hope.**

---

## Sources

- C. Richardson, "Transactional Outbox" —
  https://microservices.io/patterns/data/transactional-outbox.html
  (dual-write problem; invariants I1/I2 are the outbox pattern with the
  event log as the outbox)
- M. Fowler, "Event Sourcing" —
  https://martinfowler.com/eaaDev/EventSourcing.html (the log as system
  of record; projections as derivations)
- M. Fowler, "Audit Log" — https://martinfowler.com/eaaDev/AuditLog.html
  (append-only audit discipline)
- SQLite, "Write-Ahead Logging" — https://www.sqlite.org/wal.html
  (readers don't block writers; 1000-page auto-checkpoint; same-host
  constraint — §4's WAL discipline)
- PagerDuty Events API v2 docs (response codes; dedup_key semantics) —
  verified live 2026-10-03; full citation in file 03 §4
- Internal: `design/principal/01-architecture-rederivation.md` §7 Move 2
  (§9 there), `design/principal/11-synthesis.md` §2.2/§3.3,
  `design/principal/12-adr-deltas.md` ADR-011/023
