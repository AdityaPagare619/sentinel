# 01 — Race-to-Page: Implementation-Ready Design (ADR-010)

**Lane 1 (Forge) · principal fix-designs wave · 2026-10-03**
**Status: PROPOSED.** Implements ADR-010. The frozen spec is NOT unfrozen by
this document — application waits for Forge's review and Aditya's verdict
(synthesis §7).
**Judged against:** `design/principal/00-laws.md` (the seven laws).
**Builds on:** `design/principal/01-architecture-rederivation.md` §7 Move 1,
`design/principal/11-synthesis.md` §2 hole 1 / §3.1 / §4, ADR-013/015/019/022
as cited. No new fact-finding; no code.

---

## 1. First principles (Law 4)

Why does the gate call Jev at all? → Because suppression is an
expected-cost decision and the cost math needs P(SEV1) — a judgment only
the model can supply. Why was that call *blocking* the hot path? → v0.1
accident: one night, one thread, `answer = jev.ask(...)` inline. Why is
that wrong, at bedrock? → **Paging latency is our promise; inference
latency is the vendor's property. A promise bounded by someone else's
property is not a promise.** The measured 11.4s first call against a
70–500ms spec is not a tuning problem (Law 1: don't tune the timeout —
ask whether the call should block at all). The answer: it should not.
The page must be able to leave while inference is still thinking, and the
system must stay honest about which one happened.

The irreducible design: **the Jev call races a paging budget B. The
timer's default action is passthrough — a deterministic rule, not a model
output.** Suppression becomes something Jev must *earn* by being fast,
confident, and corroborated. Slow inference degrades to paging, never to
waiting.

---

## 2. The budget B — value, reasoning, tuning

### 2.1 Default: B = 1000 ms

The default is a single number with a written derivation (Law 3 demands
no magic constants on the paging path):

- **Floor constraint:** B must sit well above the vendor's *healthy*
  p99, or the timer wins during normal operation and suppression silently
  starves. Vendor spec: 70–500 ms. **B ≥ 2 × spec-max = 1000 ms** means
  the timer fires only when the vendor is operating at ≥2× outside its
  own spec — genuinely degraded, not jittery.
- **Ceiling constraint:** B must sit below any latency a postmortem could
  plausibly name as a contributing factor. Pre-mortem scenario C: ~10 s
  of added latency killed a renewal. Human paging response is measured in
  minutes; the mobile-push path itself jitters by seconds. **1000 ms of
  worst-case added latency is indefensible as a contributing factor** —
  it is less than the noise already in the pipeline.
- **Schelling point:** 1000 ms is 2× the spec max — memorable, explainable
  to a buyer in one sentence, and re-derivable by any engineer who reads
  the vendor spec. Constants that need a comment to survive are
  liabilities; this one carries its derivation in its value.

**Seeded, not frozen:** when Oracle's N≥100 latency campaign lands, the
shipped default is re-derived once as `max(1000 ms, 2 × measured healthy
p99)` ("healthy" = non-incident windows), then re-derived quarterly by
the tuner. The 1000 ms ships on day one because the campaign has not
landed and the race cannot wait for it.

### 2.2 Bounds and per-org tuning (Type 2)

- **Allowed range: [500 ms, 5000 ms].** The loader enforces it
  fail-closed on config (Law 7 / Forge A7: fail-CLOSED on config,
  fail-OPEN on runtime):
  - `B < 500 ms` → **refused at load** (below the vendor's own spec max
    the race is meaningless — the timer would win on healthy vendor
    behavior; fall back to compiled default 1000 ms, log loudly, metric).
  - `B = 0`, null, or "disabled" → **there is no way to disable the race
    via config.** Disabling the race is a Type-1 architecture change, not
    a config value. A zero/null value means "use default."
  - `B > 5000 ms` → refused (above this the "bounded latency" promise is
    hollow; the buyer is buying a 5-second paging delay).
  - `500 ms ≤ B < 1000 ms` → allowed with a loud warning (the org is
    trading suppression rate for latency; the tuner shows the priced
    tradeoff — see §8 creative application).
- **Per-org tuning is Type 2** (reversible, re-tunable, no RFC): the
  tuning *surface* (the knob, its range, its loader guards) is the
  Type-2 decision; the *race mechanism itself* is Type 1.
- **Drift guard:** the tuner reports suppression-rate-as-a-function-of-B
  per org, and a **timer-win-rate watchdog** pages the operator if the
  rolling 1-hour timer-win rate exceeds 5% (Type-2 threshold): sustained
  timer wins mean the vendor is degraded or B is misconfigured — both are
  operator-actionable, neither may be silent. (A B set too small does not
  cause missed pages — it causes suppression-rate collapse, a *business*
  failure with a *safety-shaped* silence. The watchdog exists because the
  failure direction is invisible otherwise.)

### 2.3 What B is NOT

- B is not a timeout on the Jev call. The inference call has its **own**
  socket-level timeout (default 30 s, Type 2 — §5.3) that bounds the
  detached thread's lifetime. B bounds the *page*, not the *call*.
- B is not per-question or per-model. One B per gate process; per-org
  override via config. (Per-alert B would make latency promises
  alert-dependent — un-auditable.)

---

## 3. The race mechanism — exact semantics

### 3.1 The three parties

The race has **three** participants, not two. This is load-bearing (the
pre-mortem in §9 shows why two is insufficient):

1. **The inference worker** — runs the Jev call on a bounded thread pool
   (see §3.3), then attempts to claim the race.
2. **The timer** — a single scheduler thread holding a heap of deadlines
   (see §3.4). On deadline expiry it attempts to claim the race.
3. **The gate waiter** — the gate's own thread, which waits on the claim
   with its own deadline of **B + ε (ε = 500 ms)**. If neither party has
   claimed by B+ε, the gate claims passthrough itself. The waiter is the
   backstop against a dead timer *and* a hung inference call composing.

**The claim** is a single atomic transition guarded by one lock:

- State per in-flight alert: `UNCLAIMED → CLAIMED_BY_INFERENCE |
  CLAIMED_BY_TIMER | CLAIMED_BY_GATE`.
- The transition is check-and-set under the lock. Whoever transitions
  first owns the disposition. Same-instant completion is impossible by
  construction — the lock serializes it, and a 1 µs loss is still a
  loss: **if the timer's claim wins by a microsecond, the page has
  already left; the late confident answer becomes a shadow event.** That
  is what a race *means*; a builder who asks "but what if the answer
  arrived 1 ms after the timer?" gets this paragraph, not a tie-breaker.

### 3.2 Arming order (exact)

1. `deadline = monotonic() + B/1000` — computed FIRST, before the
   inference thread exists. The budget belongs to the page, not the
   inference: thread-start scheduling latency eats into the *inference's*
   share, never the page's.
2. Submit the Jev call to the inference pool.
3. Arm the timer: push `(deadline, alert_id)` onto the scheduler heap.
4. The gate thread waits on the claim event with timeout `(B+ε)/1000`.

On inference-win: cancel the timer entry best-effort (lazy deletion from
the heap — mark invalid; the scheduler thread skips invalid entries; no
heap surgery under lock). A fired-but-unclaimed timer is harmless: it
checks the claim flag, finds it taken, and does nothing.

On timer-win: the gate proceeds immediately to §4 (passthrough). The
inference worker is **detached, not cancelled** (§5.3).

### 3.3 The inference pool (bounded, fail-open under load)

- Dedicated `ThreadPoolExecutor`, size 32, bounded queue 256 (both
  Type-2 values; sized for network-bound work at 40 req/s with headroom).
- **Queue-full → immediate passthrough.** If the pool cannot accept the
  call, the alert takes the timer-win path without waiting: fail-open
  under load. This is the correct backpressure direction (shed inference,
  never shed pages) and it makes pool saturation *visible* as timer wins
  in the watchdog (§2.2) rather than as latency.
- Pool threads are daemons; each carries the socket timeout from §5.3 so
  no thread lives past it. The pool is per-gate-process (never shared
  with the platform tier — process separation is the do-no-harm
  enforcement).

### 3.4 The timer scheduler (one thread, not one thread per alert)

`threading.Timer` per alert is rejected: at 40 alerts/s it churns 40
threads/s — thread-creation latency alone would jitter the budget, and a
per-alert thread is a per-alert failure surface. Instead:

- **One scheduler thread** for the process, sleeping on a heapq of
  `(deadline, seq, alert_id)` keyed by `time.monotonic()`.
- The thread's wakeup loop does exactly one thing per expired entry:
  acquire the alert's claim lock; if unclaimed, transition to
  CLAIMED_BY_TIMER and set the gate's claim event. **Five lines. No I/O.
  No logging. No allocation beyond the tuple.** Wrapped in a
  try/except that on *any* exception falls through to the gate's own
  B+ε deadline (§3.1, party 3) — the timer thread must be nearly
  unkillable, and the design does not assume it is immortal.
- The scheduler thread is a daemon with the highest practical priority
  hint the platform allows; its only job is deadlines.

### 3.5 Clock source: monotonic, and why wall-clock is forbidden

- **All race arithmetic uses `time.monotonic()`.** CPython guarantees a
  monotonic clock "cannot go backwards"; on Linux it is CLOCK_MONOTONIC —
  immune to NTP step corrections, manual `date` changes, and leap-second
  smears.
- **Wall-clock (`time.time()`) is forbidden** for the race by
  architecture, not by comment: an NTP step *backward* during a race
  makes a wall-clock deadline comparison fire late or never (the page
  waits past B — the exact promise broken); a step *forward* fires it
  early (suppression starves for the step duration). Both directions are
  wrong, and both have happened in production fleets.
- **Event timestamps are still wall-clock** (`ts` in the event envelope,
  UTC ISO-8601, for humans and postmortems). The log's *authoritative
  order* is the append sequence number, never `ts` — because wall-clock
  can jump and the truth must not depend on NTP. The race and the log
  use different clocks on purpose, each for the job it can do.
- A CI test asserts the race module references no wall-clock source
  (grep-grade test: `time.time`, `datetime.now` absent from the race
  module). The tired maintainer (Forge A2-adversary) cannot
  "simplify" the clock without breaking the build.

---

## 4. Timer-wins behavior — immediate passthrough, late answer as shadow

### 4.1 The timer-win path (exact steps)

When the timer (or the gate backstop) claims the race:

1. **Disposition = passthrough.** No lock evaluation, no waiting, no
   second-guessing. The deterministic safe default.
2. **One transaction:** `INSERT decision_made` with
   `budget_outcome = "timer_won"`, `latency_ms = NULL`,
   `timer_fired_at_ms = <monotonic ms elapsed>` **plus**
   `INSERT outbox_row` (the durable forwarder's row — file
   `03-durable-forwarder.md`). COMMIT. The page is now durable; the
   forwarder loop owns delivery from here. (The atomicity proof is in
   file `02-event-log.md` §3.)
3. The gate thread returns to the dispatcher loop. **Total added latency
   on this path: ≤ B + ε, bounded by our budget, never by the vendor.**
4. The inference worker, when it eventually completes, finds the claim
   taken and proceeds to §4.2. It never writes `decision_made`.

### 4.2 The late answer → `shadow_decision` event (exact schema)

The late answer **never pages, never suppresses, never re-opens the
decision.** It is written as a `shadow_decision` event — the audit
schema's late-answer type (ADR-010 consequence). Exact schema
(canonical; repeated verbatim in `02-event-log.md` §2):

**Envelope** (shared with all events): `event_id` (uuid4 hex),
`seq` (append order, authoritative), `schema_v = 1`,
`ts` (UTC ISO-8601, wall-clock, human use only), `actor = "engine"`,
`alert_id`, `fingerprint`, `episode_id`, `input_sha256`.

**Body:**

| Field | Type | Meaning |
|---|---|---|
| `jev_model` | string | Exact model version that answered (pin asserted; mismatch ⇒ the answer is discarded as untrusted — ADR-015) |
| `q1_reported` | number | Reported P(p1), quantized 0.01 as received |
| `q2_team` | string | Routing answer |
| `q3_confidence` | number | Reported confidence |
| `q3_disposition` | string | The model's disposition answer |
| `latency_ms` | number | **Full** Jev call latency — this is the vendor-tail measurement; feeds Oracle's latency campaign and the tuner |
| `budget_ms` | number | B in force at race time |
| `timer_fired_at_ms` | number | Monotonic ms elapsed when the timer won |
| `decided_disposition` | string | `"passthrough"` — what actually went out |
| `shadow_disposition` | string | What the gate *would* have decided had the answer arrived in time (`suppress` \| `passthrough`), evaluated by the **same pure policy kernel** the live gate uses (Forge §2 deletion ledger: one kernel, not a copy) |
| `would_have_suppressed` | bool | `shadow_disposition == "suppress"` |
| `lock_evaluation` | object | Per-lock `pass`/`fail`/`stale` as evaluated at answer time (prob / conf / allowlist + freshness proofs) |
| `links.decision_made_seq` | int | `seq` of the timer-win `decision_made` this event shadows |

**Why the shadow event earns its keep** (Law 1 — it must justify its
existence): (i) it is the vendor-latency evidence (every late answer is
a dated tail sample with the model version attached); (ii) it feeds
calibration honestly — the tuner learns from what *would* have happened
without ever letting a late answer act; (iii) `would_have_suppressed`
is the priced tradeoff made visible: "the race cost you N suppressions
this week; raise B to 1500 ms to recover them at +500 ms worst-case
latency" (§8). Shadow stops being a deployment *mode* and becomes the
system's normal way of handling slow inference (Forge §2).

### 4.3 River rendering

The river (a projection over events — synthesis §3.3) renders a
timer-win as **one row**: `decision_made(passthrough, timer_won)` with
the linked `shadow_decision` inline-expandable, showing
`would_have_suppressed` and the lock evaluation. The 3 AM operator sees
"paged because Jev was slow (1.8 s vs 1000 ms budget); the late answer
would have suppressed" — contrast against their experience, per Prism's
counterfactual-receipt principle. A timer-win is never rendered as a
model decision; it is rendered as a *budget* decision.

---

## 5. "Fast AND confident AND corroborated" — the mechanical procedure

Suppression is the conjunction of six ordered steps. **Order matters:**
deterministic bars first (free, no Jev spend), then the race, then the
locks. Pseudocode-free, builder-executable:

**S1. STRUCTURAL BARS (deterministic, pre-race — from the correlator/gate config).**
Evaluate before the race is even armed; any bar hit ⇒ passthrough
**without starting the Jev call** (never pay Jev for a decision already
made — the correlator is the economic boundary, Forge §1.2):
- (a) Storm aggregate ⇒ passthrough on the **separate storm code path**,
  not a flag (ADR-016). The aggregate path cannot call the suppress
  branch — structural, not conditional.
- (b) Novel fingerprint (no history) ⇒ page (novelty rule).
- (c) Security-category fingerprint ⇒ page (banned from the allowlist
  in code — ADR-017).
- (d) History staleness: `history_as_of` older than 72 h ⇒ treated as
  empty ⇒ novelty rule ⇒ page (ADR-021).

**S2. FAST — win the race.** Arm per §3.2. If the timer (or gate
backstop) claims first ⇒ passthrough per §4, stop. Only a
`CLAIMED_BY_INFERENCE` proceeds. (Also: pool queue-full ⇒ passthrough
per §3.3, which is the timer-win path with
`budget_outcome = "timer_won_shed"` — distinguished in the event for
the watchdog.)

**S3. VALID — the answer must be trustworthy.** All must hold, else
passthrough:
- (a) Not an error (wire errors → `JevError` → passthrough; 401/422
  never retried — Forge A9).
- (b) `response.model == pinned_version` exactly (ADR-015); mismatch ⇒
  passthrough **plus a drift page** (control-plane alert,
  unsuppressible — the vendor changed the mapping under our thresholds).
- (c) All three questions well-formed (option sets per the frozen
  contract; malformed ⇒ passthrough — uncertainty pages, Law 7).

**S4. CONFIDENT — lock 2.** `q3_confidence ≥ 0.90` (shipped default).
- The threshold value is Type 2; the loader enforces the **hard floor
  0.85** (ADR-022): a configured value below 0.85 is refused
  (fail-closed on config — below the cost-optimum floor the bar is not
  a tuning choice, it is a safety violation). Refusal ⇒ compiled
  default 0.90, loud log, metric.
- The fatigue-ratchet guard (suppression-rate SLO + watchdog, 30-day
  re-validation clock) lives in the tuner/watchdog **off the hot path**
  (ADR-022); the gate reads the current attested value at config load.
  Freshness proof: the threshold attestation must be within its
  30-day validity — **stale attestation ⇒ lock fails ⇒ page**
  (ADR-014). The freshness check is a cached comparison, never
  per-alert I/O (synthesis §3.5 — the race budget is untouched).

**S5. CORROBORATED — locks 1 and 3, each with proof of freshness.**
- **Lock 1 (probability, quantized — ADR-013):** `q1_reported == 0.00`
  AND ( (a calibration fit exists for this org/fingerprint-class AND
  `fit.p̂_upper < 0.002` AND the fit is within its validity window) OR
  (no fit yet AND the allowlist entry carries **dual human
  attestation**, unexpired) ). The bar is never silently approximated:
  no fit and no dual attestation ⇒ lock fails ⇒ page.
- **Lock 3 (allowlist — ADR-017/019):** the fingerprint matches an
  allowlist entry whose attestation tuple
  (who / when / on-what-evidence / TTL) is complete and unexpired, the
  entry is namespaced to this env, and the drift check is fresh.
  **Corroboration shapes** (synthesis §3.2): the allowlist-attestation
  shape or the signed-`resolve`-from-same-source-for-same-`alert_key`
  shape. **Silence-floor rule** (mechanical reading of ADR-019, open to
  Forge's review): for severities at/above the org's silence floor, lock
  3 MUST be satisfied by the allowlist-attestation shape (human-verified
  ground truth); the signed-resolve shape is admissible only below the
  floor. Rationale: the two shapes are not equally strong, and the floor
  is where "silence takes two" bites hardest.
- **Freshness (ADR-014):** any stale proof ⇒ that lock fails ⇒ page.
  Freshness is checked against cached attestations at decision time
  (microseconds); the *revalidation* that refreshes the cache runs off
  the hot path.

**S6. SUPPRESS — only if S1–S5 all pass.** Write `decision_made`
with `disposition = "suppress"`, `budget_outcome = "answered_in_time"`,
the full lock evaluation, and the `threshold_counterfactual` field
(ADR-023 — evaluated deterministically at event-write time, zero
hot-path cost). No outbox row (nothing to deliver). The suppression
receipt (muted-not-dropped per ADR-007's pending verdict) is the
platform's projection job, not the gate's.

**What the procedure guarantees:** suppression requires the model to be
fast (S2), confident (S4), and corroborated by non-model evidence (S5)
— and every leg carries liveness (freshness), so the triple lock cannot
rot into a single lock (synthesis §3.5, Tripwire C-1).

---

## 6. In-flight inference on timer-win — detach, never cancel

When the timer wins, the inference thread is **detached**:

- **No thread cancellation.** CPython has no safe thread-kill; a
  "cancel" would either leak the socket or corrupt the HTTP client's
  connection pool. Detach is the honest primitive.
- The detached thread **touches no shared state** except: (i) reading
  the claim flag (already taken — it proceeds to §4.2), (ii) writing the
  `shadow_decision` event through the normal event-write path. It never
  writes `decision_made`, never touches the outbox, never signals the
  gate.
- **Bounded lifetime:** the Jev client is constructed with an explicit
  socket timeout (default 30 s, Type 2) asserted `> 0` at construction —
  `None`/infinite is refused (fail-closed; §9 pre-mortem). A best-effort
  abort of the HTTP request is attempted on detach *only if* the client
  library supports it without pool corruption; correctness never depends
  on it.
- If the detached call errors (wire error, 429/529, malformed), it still
  writes `shadow_decision` with `q*_fields = null` and an `error_class`
  — a failed late answer is still vendor evidence (and still never
  pages).
- **Thread accounting:** a gauge tracks detached-in-flight count; a
  sustained high count is the vendor-degradation signal for the
  timer-win watchdog (§2.2). Threads always terminate by the socket
  timeout — no leak by construction, verified by a soak test (CI:
  10k races with a black-hole Jev stub ⇒ thread count returns to
  baseline).

---

## 7. Failure modes — walked, not listed

**F1. Timer armed after inference starts (deadline skew).** Prevented by
§3.2's arming order (deadline computed before pool submission). A
debug-assertion in tests: `deadline_monotonic ≤ submit_monotonic` is
impossible — the deadline is always computed first. If a builder
reorders it, the race still favors the page (the timer gets *more*
time, not less — the skew direction is safe, but the test forbids it
anyway: safe-by-accident is not a design).

**F2. Timer entry leaked (never cancelled on inference-win).** Lazy
deletion: the heap entry is marked invalid; the scheduler thread skips
invalid entries on pop. Worst case is a few dead heap entries per
second — O(1) amortized, no correctness impact. A metric counts
skipped-invalid entries; a spike means the cancel path regressed.

**F3. The scheduler thread dies.** Its callback is five lines wrapped in
try/except (§3.4); but "nearly unkillable" is not "immortal." The
backstop is party 3: the gate's own `B+ε` wait. If the scheduler is dead
*and* inference hangs, the gate still pages at B+ε. Additionally, a
watchdog thread heartbeats the scheduler (heap size + last-wake
timestamp in shared memory); a stale heartbeat is a control-plane page.
Three layers: the timer, the gate's deadline, the watchdog. (This
composition is the direct answer to §9's pre-mortem.)

**F4. B misconfigured to 0 / ∞.** Impossible via config: the loader's
[500, 5000] ms range with fail-closed refusal (§2.2). There is no
"disable" value. A hand-edited config with `B: 0` loads as 1000 ms with
a loud log — the A7 tired-maintainer adversary is answered by refusal,
not by documentation.

**F5. Clock jumps.** Monotonic is jump-immune by construction (§3.5).
The residual risk is a *platform* that does not provide a true monotonic
clock (exotic embedded); the supported-platforms note names Linux
CLOCK_MONOTONIC, and the CI clock test (§3.5) pins the stdlib call. If
`time.monotonic` ever went backward, every deadline in the process
would be suspect — this is a "the OS is broken" event, out of scope by
explicit statement, not by silence.

**F6. Pool exhaustion under storm.** §3.3: queue-full ⇒ immediate
passthrough (`timer_won_shed`). The correlator's storm-collapse should
make this rare; when it happens the watchdog sees it as timer wins and
the operator sees *why* in the event (`budget_outcome` distinguishes
shed from slow-vendor). Overload sheds inference, never pages.

**F7. Same-instant finish.** Serialized by the claim lock (§3.1). The
loser — even 1 µs late — takes the loser's path. No tie-breakers, no
"close enough": the race's semantics are total and simple, which is what
makes them auditable.

**F8. Vendor answers *after* the page left, and the answer is
confident-suppress.** The page stands — un-sending a page is impossible,
and the design never pretends otherwise. The `shadow_decision` records
`would_have_suppressed = true`; the river shows it inline (§4.3); the
tuner prices it (§8). The operator's mental model ("the page went out
because we didn't wait") matches the mechanism exactly.

**F9. Inference wins at B−1 ms, then lock evaluation is slow.**
Impossible by construction: lock evaluation is local, deterministic,
microsecond-scale (cached attestations, no I/O — ADR-014's freshness
checks are explicitly off the hot path). The claim is the commitment
point; everything after it is bounded deterministic work. A CI
performance test asserts p99 claim-to-decision_made-commit under 10 ms.

**F10. The gate thread itself is stalled (GIL, CPU saturation) past
B+ε.** Then the page is late by the stall duration — the one case where
added latency is *ours*, not the vendor's. Mitigation is structural, not
in this file: the engine process is dedicated to the paging path
(two-process topology — Forge §1.7), and the platform tier can never
steal its CPU. Named here so the builder knows the boundary of this
design's guarantee: the race bounds *waiting on the vendor*; it does
not bound *our own scheduling*. The deployment runbook (not this file)
owns CPU isolation.

---

## 8. Alternatives considered — and why rejected

| Alternative | Why rejected |
|---|---|
| Keep inline Jev call + a plain timeout | A timeout that fires *is* the race — minus the shadow-event semantics, minus the priced tradeoff, minus the watchdog. It concedes the mechanism while discarding the evidence. (ADR-010 alt (a)) |
| Async-everything (decide later, page when ready) | Pages must not wait for inference *at all*. Deferring the decision defers the page — the same flaw with better throughput. (ADR-010 alt (b)) |
| Hedged duplicate Jev call (The Tail at Scale, literally) | There is one Jev endpoint; a second call doubles billed cost for zero new information. The correct "hedge" against a single slow oracle is the deterministic default action, not a replica. |
| Circuit breaker on the Jev client instead of a race | A breaker reacts to *history* — the first slow page of an incident still waits. The race bounds *every* page's latency including the first. Breaker and race compose (breaker ⇒ shed faster); the breaker does not replace the race. |
| B = 0 (never wait; always passthrough) | Suppression rate → 0 and the product dies commercially. The race exists to *buy* suppression with bounded latency, not to eliminate waiting entirely. |
| Wait indefinitely for confident answers | Pre-mortem scenario C, verbatim. The renewal that died is the reason this row exists. |
| `threading.Timer` per alert | 40 threads/s churn at storm rates; thread-creation latency jitters the budget; per-alert thread = per-alert failure surface. One scheduler thread (§3.4). |
| Cancelling the inference thread on timer-win | No safe primitive in CPython; "cancel" would corrupt the HTTP pool or leak the socket. Detach + bounded socket timeout (§6) is the honest mechanism. |
| Wall-clock deadline | §3.5 — NTP steps break it in both directions. Forbidden by architecture, pinned by CI test. |

---

## 9. Type 1 / Type 2 register (this file's choices)

**Type 1** (irreversible — paging-path latency semantics; RFC-grade):

| Choice | Why Type 1 |
|---|---|
| The race itself (Jev call never blocks the page) | Changes the latency semantics of every page the system will ever send |
| Timer-wins ⇒ immediate passthrough (deterministic default) | The fail-open direction is the product's core promise |
| Late answer ⇒ `shadow_decision` event, never acts | The safety invariant that makes the race honest |
| Monotonic clock for the race; wall-clock forbidden | A clock change is a silent semantic change to every future race |
| The three-party race (gate waiter's B+ε backstop) | The liveness guarantee against dead-timer + hung-inference composition |
| Detach-not-cancel for in-flight inference | Thread-safety invariant; a "cancel" would be a latent corruption bug |
| `budget_outcome` taxonomy on `decision_made` | Durable-truth vocabulary; the river, tuner, and watchdog read it |
| Suppression procedure S1–S6 (fast ∧ confident ∧ corroborated) | The safety policy's mechanical form |
| No config value disables the race | Disabling is an architecture change, not a tuning choice |

**Type 2** (reversible — values and surfaces):

| Choice | Why Type 2 |
|---|---|
| B default 1000 ms; range [500, 5000] ms; per-org tuning | Tunable constant; the tuner prices the tradeoff per org |
| ε = 500 ms (gate backstop slack) | Backstop margin; tunable if the scheduler's wakeup jitter is ever measured |
| Inference socket timeout 30 s | Bounds the detached thread; vendor behavior may justify retuning |
| Pool size 32 / queue 256 | Capacity planning numbers, not semantics |
| Timer-win watchdog: 5% / 1 h | Observability threshold; org-tunable |
| Re-derivation cadence for B (quarterly via tuner) | Process, not promise |

---

## 10. CREATIVE APPLICATION — what a builder does differently

1. **The builder never writes `result = jev.ask(...)` on the hot path.**
   The only Jev-call site in the gate is the pool submission in §3.2.
   Any inline call found in review is a P0 defect — the race is not an
   optimization, it is the paging path's shape.
2. **The builder implements the claim as a real state machine**
   (UNCLAIMED → one of three CLAIMED states), not as two booleans and a
   hope. The pre-mortem (§11) is the reason: the failure mode is in the
   transition, and transitions need a lock and a test — including a
   stress test that fires timer and inference completion from two
   threads 100k times and asserts exactly one winner per race.
3. **The builder writes the `shadow_disposition` evaluation as a call
   into the shared policy kernel**, not as gate logic copied into the
   shadow path. The deletion ledger killed the second implementation;
   the shadow path is its first consumer.
4. **The builder treats `budget_outcome` as a first-class dimension in
   every query they write** — the tuner's "suppression rate" is always
   qualified by it (`answered_in_time` vs `timer_won` vs
   `timer_won_shed`), because an unqualified suppression rate after the
   race is a lie about what the model did.
5. **The builder prices B for the buyer.** The tuner screen for B shows
   two curves from the org's own history: suppression recovered vs
   worst-case added latency as B moves 500→5000 ms. The buyer moves a
   slider; the design already defined what the slider means. This is the
   moment the race stops being infrastructure and becomes the product:
   *latency with a price tag, chosen by the customer, evidenced by
   their own data.*

---

## 11. PRE-MORTEM — "it is one year from now and this mechanism caused a missed SEV1"

*October 2027. A customer's SEV1 — a payments database failover at
02:14 — never paged. The postmortem finds the alert entered the gate at
02:14:03. What failed, mechanically?*

Three small bugs composed — none of them "the model was wrong":

1. **The timer scheduler thread was dead.** Six months earlier, a builder
   added a debug log line *inside* the timer callback ("for
   observability"). Under a log-disk-full event the logging call raised
   inside the callback; the try/except the design required had been
   "simplified" in a refactor that the clock-pinning CI test did not
   cover (it pinned the clock source, not the callback's purity). The
   scheduler thread died silently at 01:58. The heartbeat watchdog
   (§7 F3) had been wired to the *platform* tier's health page, which
   nobody watches at 2 AM — the control-plane page went to a dashboard,
   not a human.
2. **The inference call hung.** A Jev client-library upgrade reset the
   socket timeout default to `None`; the construction-time assertion
   (`timeout > 0`, §6) was in the *old* client's factory, and the new
   client's factory was written without it. The 02:14 call hung on a
   stalled vendor connection — forever.
3. **The gate waited forever.** The gate used `future.result()` with no
   timeout — the B+ε backstop wait (§3.1, party 3) was specified in this
   design but implemented as "wait on the claim event" *without* the
   timeout argument, because "the timer always fires." The timer was
   dead. The claim event never set. The gate thread sat for six hours
   until the process was restarted at 08:20. The page never left.

*What the design already says about each link:* the callback is five
lines with no I/O (§3.4) — the log line violated it; the socket timeout
is asserted `> 0` at construction, fail-closed (§6) — the new factory
violated it; the gate's wait carries its own B+ε deadline as the third
party of the race (§3.1) — the implementation dropped it. **Every link
in this pre-mortem is a named requirement in this document, which is
exactly why each gets a test:** a callback-purity test (the callback
module imports nothing that can do I/O), a construction test (every
client factory in the tree asserts timeout > 0), and a triple-death
test (kill the scheduler thread + black-hole the vendor ⇒ the page
leaves by B+ε or the test fails). The pre-mortem is not a prediction; it
is the test plan.

---

## Sources

- Dean & Barroso, "The Tail at Scale," CACM 2013 —
  https://www.barroso.org/publications/TheTailAtScale.pdf (hedged
  requests; the race's precedent — our "hedge" is the deterministic
  default, §8)
- CPython docs, `time.monotonic` — monotonic clock guarantee (§3.5)
- PagerDuty Events API v2 docs — response semantics referenced for the
  outbox contract live in file 03; the race itself is vendor-agnostic
- Internal: `design/principal/01-architecture-rederivation.md` §7 Move 1
  (§9 there), `design/principal/11-synthesis.md` §2.1/§4,
  `design/principal/12-adr-deltas.md` ADR-010/013/014/015/019/022
