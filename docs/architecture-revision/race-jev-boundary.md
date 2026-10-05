# Phase-2 Domain Review — RACE-TO-PAGE + JEV BOUNDARY
## Timeouts, fallbacks, eval, cost

**Reviewer:** Phase-2 domain reviewer (fresh eyes; did not build this)
**Date:** 2026-10-05 · **Branch:** `program/architecture-revision-dom-race`
**Scope:** `src/sentinel/race.py`, `src/sentinel/client.py`, the fallback ladder
(`failopen.py`), judge eval (`evalharness.py`, `research/jev-behavior/ab-2026-10-04.md`),
cost, and the measured Jev data (`research/jev-behavior/`).

**Phase-1 source-of-truth note (meta-finding, verify against git):**
`research/ai-ml-production.md` is **incomplete as committed** — it contains a
complete Q1 (Simplex / Black-Box Simplex framing) and a Q2 (golden sets,
three-way splits) that **ends mid-citation**: the file's final line is
"the *judge itself* needs its own held-out validation (Ham" followed by a
pasted `[truncated 18155 chars]` artifact. The degradation ladders,
second-provider paths, exact-cache, cost circuit breakers, and eval-suites-for-fallbacks
material the brief asked me to cite from that file **do not exist in it**.
I cite what is actually there (Q1's Simplex framing), plus
`research/architecture-patterns.md` (circuit breakers, panic mode, §5–6),
`research/testing-at-scale.md` (golden sets, shadow/canary, chaos), and
`research/jev-behavior/` (fallback-engine map, live A/B). **Recommendation to
the coordinator:** the Phase-1 Q2/Q3+ sections need to be re-authored before
this review's citations can be treated as standing on that document.

---

## 1. WHAT EXISTS

Grounded in the code as it stands on `program/architecture-revision`.

### 1.1 The race (src/sentinel/race.py)

A three-party race per alert: (1) the inference worker on a bounded pool
(32 workers / 256-queue), (2) one daemon timer-scheduler thread holding a
heap of deadlines (lazy cancellation — no heap surgery), (3) the gate waiter's
own `B + ε` deadline (ε = 500 ms) as the backstop against a dead timer AND a
hung inference call composing. The claim is a single atomic transition under
one lock: `UNCLAIMED → CLAIMED_BY_{INFERENCE, TIMER, GATE}` — a 1 µs loss is
still a loss. Detach-not-cancel: CPython has no safe thread-kill, so the
socket timeout (not the budget) bounds the detached thread's lifetime.

**Budget derivation (the honest chain):** `DEFAULT_BUDGET_MS = 2700`
(race.py:89) = `max(1000 ms, 2 × measured healthy p99 1339 ms) = 2678 → 2700`,
re-derived from Oracle's N≥100 latency campaign (PR #19), superseding the
seeded `2 × vendor spec max (70–500 ms)` default. Config loader
`RaceConfig.from_raw` enforces `[500, 5000]` ms fail-closed: out-of-range
values fall back to the compiled default, and the race **cannot be disabled
via config** — `0`/`"off"`/`"disabled"` mean "use default" (a deliberate
Type-1 choice, logged).

**Timeout paths (what happens, exactly):**

| Condition | Outcome | Disposition | Evidence |
|---|---|---|---|
| Jev answers before B | `answered_in_time` | Jev's answer flows to the policy kernel | — |
| Timer fires first (slow) | `timer_won` | **passthrough** (deterministic rule, never a model output) | `LateAnswer` → `late_answer_hook` → `shadow_decision` event; never pages, never suppresses, never re-opens |
| Pool queue full (32/256) | `timer_won_shed` | **immediate passthrough**, timer stamped at 0.0 ms | budget_outcome distinguishes shed from slow |
| Inference won but the call raised | `error_passthrough` | **passthrough** (`reason=error:<class>`) | unhealthy sample to the health monitor |
| Timer dead AND inference hung | `CLAIMED_BY_GATE` at B+ε | passthrough; control-plane page for the stalled scheduler | scheduler heartbeat (F3) watched every 5 s |

**Clock rule:** `time.monotonic()` only — a CI test asserts no wall-clock
reference in the module. Event timestamps are built in `race_payloads.py`
on purpose (humans only).

### 1.2 The Jev client boundary (src/sentinel/client.py)

- **Model pinning (ADR-015, D2):** the client *requires* a pinned versioned id
  (e.g. `jev-1.13.0`); `None`/empty is refused fail-closed, and the floating
  `jev-latest` alias is refused unless `allow_floating_model=True`, which
  fires a CRITICAL boot banner. The gate asserts the pin against every
  answered response (`_model_drift`) — a drifted answer never reaches the
  policy kernel. The mock defaults to a fake pinned id (`jev-mock-0.0.0`) so
  drift detection stays meaningful in tests.
- **Timeouts:** `timeout_s` default 30 s, asserted `> 0` **twice** — at client
  construction and at `RaceRunner` construction (fail-closed; duck-typed test
  doubles are skipped). Unbounded inference calls are refused, which matters
  because of detach-not-cancel.
- **Retry policy:** 401 → no retry; 422 → no retry; 429 → honors `Retry-After`
  within a 2.0 s retry budget; 529/5xx/timeout → 3 retries with exponential
  backoff + jitter, all inside the retry budget. Wire quirks handled:
  urllib's default User-Agent gets HTTP 403, so a custom UA is sent.
- **Identity:** the state is canonical-JSON'd and sha256'd (`input_sha256`)
  as the fingerprint carried through race, payloads, and late answers —
  identity exists, but (see §2.4) nothing caches on it.

### 1.3 What happens on slow / down / wrong — the measured facts

- **Slow (vendor degradation):** the Oct-4 live A/B
  (`research/jev-behavior/ab-2026-10-04.md`) ran straight into a vendor
  slowdown: **20–24% of calls exceeded the 2700 ms budget and timer-won to
  passthrough**. Post-hoc decomposition showed **~89% of the "flip signal"
  was a latency artifact** — arms that timer-won recorded no answers, counted
  as "different". On the 66 alerts where all three arms actually got answers,
  per-question choice-flip rates were identical between noise and variant.
  Lesson already internalized by the campaign: flip telemetry is confounded
  by latency unless timer-wins are decomposed out.
- **Sustained slow:** two overlapping tripwires. The race's `TimerWinWatchdog`
  (1 h rolling window, >5% rate, min 10 samples, hysteresis re-arm) pages an
  operator — the docstring names the failure direction honestly: "a B set too
  small causes suppression-rate collapse — a *business* failure with a
  *safety-shaped* silence". The stepped fail-open ladder C1 (`failopen.py`)
  enters **step 1 at 25% timer-win rate over a trailing 900 s window**
  (n ≥ 30, 600 s dwell, K6: deliberately strictly stronger than the watchdog
  trip): step 1 = last-known-good signed policy, deterministic severity
  routing, **no Jev call**; step 2 = severity floor (source-declared CRITICAL
  only, deduped, rest held for the digest); step 3 = rate-capped paging +
  ranked digest. Steps 1–3 are deterministic — "suppress" is unreachable by
  construction. Recovery is real: canary probes (R15) feed the vendor health
  monitor while degraded (the sensor was previously downstream of the
  actuator), and exit needs the timer-win rate below half the entry rate,
  held 600 s. Every transition emits `failopen_step_entered` / the violet
  banner / `failopen_recovered` events.
- **The 6–24% gap:** sustained timer-win rates *between* the 5% watchdog page
  and the 25% ladder entry page an operator but never step the ladder — the
  gate keeps racing a degraded vendor indefinitely. This is deliberate
  (RFC §5.3 rejects alternatives B/C as theater), but it means a long-lived
  partial degradation is a page-and-hope, not a controlled degradation.
- **Wrong:** answered answers pass deterministic rails (pin assertion,
  freshness monitor, quantized-probability lock, dual-attested allowlist)
  before the policy kernel. Measured non-determinism is 1.3–2.2% flips.
- **All hot-path Jev calls are inside `RaceRunner`** (verified: the only
  `.decide()` call sites are `_inference_call`, `submit_advisory` (detached,
  never gates), and `probe_vendor` (canary, feeds health only); the weekly
  revalidation job is out-of-band). No inline `client.decide` on the hot path
  — that is documented as a P0 defect.

### 1.4 Simplex framing (per ai-ml-production.md Q1)

Sentinel *is* a recognizable Simplex instance: Jev = advanced controller, the
paging path (timer-default passthrough + deterministic triple-lock) = verified
baseline, the race = decision module — with the switch being *temporal*
(budget B) rather than a state predicate. The correlated-guardrail critique
(a second model judging the first) does NOT bite: the timer, the
instruction firewall (ADR-020), and freshness proofs are deterministic code.
The fail-direction is deliberately fail-open-to-page (a missed page is a
SEV-1 silence; a wrong page is fatigue) — the correct Type-1 choice for a
page-or-suppress system, honestly written in the race docstring as "Suppression
is something Jev must EARN by being fast, confident, and corroborated."

**Where the research is now stale:** ai-ml-production.md Q1 says Sentinel has
"no recovery-of-the-advanced-layer discipline… no measured 'healthy again'
transition, no graded re-entry". The C1/R15 ladder **does** now have that —
canary probes, held-rate exit, step history, `failopen_recovered` events. What
is *still* one-sided, and the research's deeper point, is per-§2 below.

---

## 2. WHAT'S MISSING

vs the Phase-1 enterprise standard. Each item is grounded in the code above;
each citation is a document that actually exists in this repo.

### 2.1 No second engine for the judge — "Jev first" is also "Jev only"

`research/jev-behavior/2026-10-02-latency-fallbacks.md` mapped the escape
hatches three days into the build: Jev is served **via multiple pipes** with
the same wire shape (Vercel AI Gateway, Cloudflare Workers AI, OpenRouter,
Netlify AI Gateway) — a same-day 529-resilience route with zero model change —
and **Laya** (Apache 2.0, ModernBERT-large 421M, ~33–40 ms/query self-hosted)
as the open-weights exit with a real fine-tune precedent. The research agenda
explicitly carries Aditya's thesis: *"Jev is the FIRST engine, never the ONLY
engine."*

**None of it is wired.** There is no alternate-base-url path, no gateway
routing, no Laya adapter, no `secondary` provider concept for the judge
(`secondary.py` is the *notification* secondary — a different failure). When
TypeSafe is down, the architecture's own ladder (steps 1–3) **never
suppresses by construction** — so the entire suppression value proposition
vanishes at exactly the moment noise is highest (vendor degradation and
alert storms are correlated). The degradation ladder handles the *paging*
path; the *judge* path has no degraded alternative at all. Per the Simplex
framing in ai-ml-production.md Q1: there is a baseline and a switch, but the
advanced layer has no recovery discipline *of itself* — no second provider,
no degraded-judge tier between "full Jev" and "no Jev".

### 2.2 No cost circuit breaker — the boundary spends money it cannot see

`DecisionResponse.input_tokens` is parsed from every response (client.py:354)
and then **aggregated nowhere in production**. Cost accounting exists exactly
once: `ab.py:633 cost_accounting()` for the A/B *report*. There is no
per-org spend ledger, no burn-rate alert, no cost-based throttle, no cost term
in the error budget. Jev is $0.042/M input — cheap per call, unbounded in
aggregate: a storm × retry policy (§2.3) is a blank check the operator only
sees on the vendor's invoice. The principal-governance checklist asks for
error budgets that govern launch/release risk; the campaign's promotion gates
price score but not spend. A cost term belongs in the watchdog vocabulary
(§1.3's business-failure-with-safety-shaped-silence cuts both ways: cost
collapse is also silent).

### 2.3 The retry policy burns guaranteed losers against a degraded vendor

Retries happen **inside the race budget** (2.0 s retry budget < 2.7 s B), and
each retry is a new billable POST with **no idempotency key**. A call that
hits 429 honors `Retry-After` by *sleeping inside the race* — holding a pool
thread for a response that mathematically cannot win (the timer fires at B
regardless). The polite per-call behavior (honor Retry-After, back off,
retry ×3) aggregates into the wrong global behavior: calling a vendor that is
telling you to stop, with extra billable calls, each one a guaranteed
timer-win. `research/architecture-patterns.md` §5–6 names this shape exactly:
"Sentinel has the budget race (2700 ms) but no equivalent of **outlier
detection for the Jev dependency** — … the budget race treats every call as
independent. A circuit-breaker between gate and Jev (open the breaker after
N consecutive budget-busts, fail-open while open, half-open probe) is the
direct Envoy-mechanism transfer." Fail-open here is Envoy's panic mode —
but Envoy quantifies the path to panic (thresholds, ejection, re-admission);
Sentinel has only the per-call budget.

### 2.4 No exact-cache — the same question is re-bought at storm rates

`input_sha256` exists as identity (canonical state → sha256, carried through
race payloads and late answers) but **nothing caches a Jev answer on it**.
Identical states re-enter the race and re-bill the vendor at full latency.
Principal-systems law 1 (global over local): before tuning the budget, ask
whether the bottleneck — and the spend — should exist at all. Deterministic
states (same alert shape, same questions, same pinned model, fresh policy)
are pure functions of their inputs; the race re-evaluates them as if they
weren't. The correlator dedups alerts upstream, but anything reaching the
race with a repeated fingerprint pays full price.

### 2.5 No eval suite for the fallbacks or for the judge itself — only for the gate

- The eval harness (`evalharness.py`) evaluates the **gate** against
  **scripted mock answers keyed off state fingerprints** — its own report's
  Honest Limitation 1: "**Synthetic data proves plumbing, not production
  accuracy**… it says nothing about how the real Jev model will score on
  customer alerts." The flip probe injects flips into the mock; the shuffle
  probe is **admitted vacuous** ("the mock keys answers off state, not option
  order… Re-run it against live Jev before claiming order-robustness").
- The one live-judge eval (the Oct-4 A/B, `ab-2026-10-04.md`) was confounded
  by the vendor slowdown it was trying to be robust to (~89% of its flip
  signal was latency artifacts; only 66 of 200 alerts got answers on all
  arms). There is **no frozen golden set of real alert shapes with
  behavior-labeled expectations run against live Jev** — the exact artifact
  ai-ml-production.md Q2 prescribes (50–200 real tasks, frozen, tiers:
  smoke/regression/hard).
- The fallback ladder has **no quality eval**: do step-1/2/3 decisions agree
  with healthy-mode decisions on paging coverage (the trust metric is
  false-suppress on SEV1s — measured only on mocks)? Does the severity floor
  preserve recall? Nobody has run the ladder's degraded tiers against the
  same golden set and priced the degradation.
- The shadow evidence (`shadow_decision` from late answers) is persisted but
  has no consumer evaluated against it: the `detached_in_flight` gauge is
  exposed as a property and read by **nobody** (grep: only race.py references
  it); the late-answer vendor-tail latency samples feed the river but the
  revalidation cadence for B against those samples is absent (see §2.6).

### 2.6 B is derived once, never re-derived — the tuner doesn't read the river

`race.py`'s module docstring claims: "the `budget_outcome` vocabulary
(Type 1 — durable truth; **the river, tuner and watchdog read this**, never
raw latency alone)." The watchdog reads it (`TimerWinWatchdog.record`).
**`tuner.py` does not** — zero references to the budget vocabulary, to
`timer_won`, or to `budget_ms`. The gate logs "the tuner prices this tradeoff
per org" when B is set below recommended (race.py:187) — but no tuner code
prices it. B is `max(1000, 2×1339)` from the Oct-3 N=100 campaign, frozen.
This violates the campaign's own unseen-month law: prefer self-calibrating
methods (parameters estimated from the target month's own statistics) over
once-tuned constants. A healthy p99 that drifts — the Oct-4 slowdown pushed
20–24% of calls past B — silently changes the race's meaning (B no longer
means "2× healthy p99") with no recomputation and no alarm on the drift of
the *derivation*, only on the symptom.

### 2.7 Minor inconsistencies (honesty-grade, small blast radius)

- `RaceConfig.from_raw`'s refused() log comment says "using compiled default
  (1000 ms)" but passes `cls()` whose default is 2700 ms — stale comment,
  confusing in a postmortem.
- During the Oct-4 slowdown the measured data (p50 816 / p99 1339) came from
  the Oct-3 campaign; the A/B report's conditions text understated the global
  rate (0.35 → true 0.60 req/s). Small, but the pattern — reports that
  misstate their own conditions — is what the harness's "no cheating on
  frozen sets" rule exists to prevent.

---

## 3. CONCRETE REVISION PROPOSALS

Ranked by value (safety impact × blast radius × falsifiability). Each names
its verification. No code — this is the revision memo; MATH_DESIGN++ precedes
any implementation per lab law.

### P1 — A degraded-judge tier: wire the second provider as step 0.5, not a wish

**What:** give the judge its own fallback before the "no Jev" ladder.
Two lanes, in order of build cost:
(a) **Alternate pipe for the same model** — the race client gains a
`secondary_base_url` (gateway: Vercel AI Gateway / Cloudflare Workers AI /
OpenRouter, same `/v1/systemone` wire shape, same pinned model id, drift
assertion unchanged). On `JevOverloaded`/sustained timer-wins, new races route
to the secondary pipe; health on the secondary is tracked independently.
(b) **Degraded engine** — Laya-class open-weights encoder behind the same
`decide()` interface for steps-1-equivalent dispositions, clearly labeled
`engine=degraded` in the audit trail, **never allowed to suppress alone**
(it can corroborate a deterministic suppress rule, never originate one —
the Data/AI constitution clause).

**Rationale:** §2.1 — the ladder degrades *paging* but the product's value
(suppression) goes to zero the moment TypeSafe is sick, exactly when storms
make suppression most valuable. This is also the direct answer to Aditya's
"Jev first, never only" thesis, which the current architecture contradicts.
Simplex framing (ai-ml-production.md Q1): a real advanced-layer recovery
discipline, not just a switch away.

**Verify:** chaos test — inject 100% 529s on the primary for 30 min against a
replay of the Oct-4 slowdown trace; require (i) suppression rate ≥ 70% of
healthy-mode on the golden set (§P5), (ii) zero suppress dispositions with
`engine=degraded` uncorroborated, (iii) failover event-logged with per-pipe
health, (iv) cost per decision within the P2 budget.

### P2 — Cost as a first-class signal: spend ledger + cost circuit breaker

**What:** aggregate `input_tokens` from every `DecisionResponse` into a
per-org spend ledger (the field is already parsed — client.py:354); derive
$/decision, $/min burn, and a **cost budget per org** wired into the
watchdog vocabulary alongside timer-win rate: sustained burn above budget →
alarm; burn above 3× budget → cost-throttle (shed advisory calls first, then
degrade the race to timer-favoring B' — never to suppress-skipping). Cost
events join the event log so the invoice is reconcilable.

**Rationale:** §2.2 — unbounded spend during retry storms is the one failure
mode with no sensor at all. "Hope is not a strategy" applies to invoices too.
Converts the blank check into a priced error budget (principal-governance).

**Verify:** replay a synthetic 529-storm with the §2.3 retry pattern; require
the ledger to trip the throttle before spend exceeds 2× the healthy-hour
baseline; reconcile ledger vs vendor invoice within 1% on a live week.

### P3 — Circuit breaker on the Jev dependency (Envoy transfer)

**What:** per `research/architecture-patterns.md` §5–6 finding 3 — a
breaker between gate and Jev: **open** after N consecutive budget-busts or a
sustained error-rate elevation (outlier detection, not just the per-call
race); while open, fail-open with **no vendor calls** (stop the polite retry
burn, §2.3); **half-open** with a cheap probe (the existing `probe_vendor`
shape, but a minimal pin-probe question — revalidation.py's `pin_probe` is
the template — not a full decide). Close on sustained healthy probes. The
6–24% gap (§1.3) closes: the breaker, not a page-and-hope, owns partial
degradation.

**Rationale:** §2.3 + the architecture-patterns review's sharpest finding —
the budget race treats every call as independent; Envoy's panic mode is
quantified, Sentinel's is not. Also fixes the 429-retry-inside-budget
burn: a breaker that is open sheds at the pool edge (`timer_won_shed`
path already exists).

**Verify:** fault-injection — ramp vendor p99 from 800 ms to 4000 ms over
10 min; require the breaker to open before pool-thread occupancy exceeds
80%, billable calls to stop within one detection window of opening, and
half-open recovery to close within 5 min of vendor recovery. Assert the
failopen ladder entry criteria are re-expressed in breaker states (no two
independent degradation state machines disagreeing).

### P4 — Deadline-aware retries + idempotency; exact-cache on input_sha256

**What:** (a) the client stops retrying calls that cannot win: if
`elapsed + expected_attempt_cost > remaining budget`, shed instead of burn
(no guaranteed-loser POSTs); retries carry an idempotency key
(`input_sha256` + attempt nonce) so a retried call is not double-counted.
(b) Exact-cache: `input_sha256 → (DecisionResponse, pinned_model,
policy_version, answered_at)` with a short TTL (minutes, not hours — the
judge's inputs are alert-shaped and policy-gated); cache hits skip the race
entirely and are labeled `budget_outcome=answered_in_time, cached=true`.
Invalidate on policy-version change and model-drift events.

**Rationale:** §2.3 (guaranteed-loser burn) and §2.4 (global optimization —
should this spend exist at all?). The 429-honoring sleep inside a race that
it cannot win is the single most wasteful mechanism in the boundary; the
cache is the cheapest global win in the whole domain.

**Verify:** (a) unit test with a scripted 429-then-slow vendor: assert ≤1
billable POST per race and no pool thread held past B. (b) replay a storm
trace with repeated fingerprints: assert ≥ X% cache-hit rate and zero
cache-served answers across a policy-version bump.

### P5 — Eval suites for the judge and the fallbacks (the missing Q2)

**What:** (a) a **frozen golden set**: 50–200 real alert shapes (from the
Oct-4 A/B trace + production shadow captures, not synthetic fixtures) with
behavior-labeled expectations (disposition, never exact strings), frozen per
ai-ml-production.md Q2's rule ("if you keep editing labels to match the new
model, you are cheating"), tiered smoke/regression/hard, run against **live
Jev** on a schedule; (b) **ladder-quality eval**: run failopen steps 1–3
against the same golden set and measure paging-coverage and suppression
agreement vs healthy mode — price the degradation; (c) **timer-win
decomposition as a standing metric** in every judge eval (the Oct-4 lesson:
flip telemetry must separate latency artifacts from judgment flips, or the
eval measures the vendor's health, not the judge).

**Rationale:** §2.5 — the current eval proves the gate's plumbing with
scripted answers; the judge, the degradation ladder, and the shadow evidence
have no honest measurement at all. Per principal-mindset: name the proxy
(harness pass rate), name the true goal (live judge quality under
degradation), prove they rank-order the same — today they don't.

**Verify:** the golden set is committed, frozen (hash-pinned), and CI-gated
on prompt/model/pin changes; the ladder eval produces a degradation-price
table (coverage @ step 0/1/2/3) in the standing calibration report; a
red-team run edits labels to match a new model and the CI gate rejects it.

### P6 — Close the B loop: re-derive B from the river, or admit it's manual

**What:** either (a) implement the tuner-side reader the race docstring
promises — rolling healthy-p99 from `answered_in_time` vendor-tail samples
in the event log, re-deriving B = max(1000, 2×p99) per org on a weekly
cadence with the derivation event-logged (self-calibrating per the
unseen-month law), with a guardrail that B moves at most ±20% per cycle and
never outside [500, 5000] without operator ack; or (b) delete the "tuner
reads this" claim and document B as a manual Type-2 value with a runbook
re-derivation step. No third option — a documented-as-automatic loop that
doesn't exist is worse than an honest manual.

**Rationale:** §2.6 — the docstring's claim is false (verified: tuner.py has
zero references to the budget vocabulary), and the Oct-4 slowdown showed a
drifting p99 silently redefining what B means. The fix is small either way;
the dishonesty is the risk.

**Verify:** (a) backtest on the Oct-3→Oct-4 latency traces: the re-derivation
would have moved B before the 20–24% timer-win episode, and the move is
event-logged with before/after; or (b) the docstring claim is removed and
the runbook step exists.

---

## Appendix — pre-mortem (top 3, per principal-systems)

Assume it is Oct 2027 and the race-to-page boundary has failed catastrophically:

1. **The vendor had a bad quarter, and we paid for it twice.** Sustained
   p99 drift (not an outage — never enough for the 25% ladder entry) kept
   8–15% of calls timer-winning for months. Suppression rate decayed
   silently (the watchdog paged; pages were acked as "vendor issue"); each
   timer-win still burned a billable call plus retries; no cost ledger
   existed to notice the spend doubling. *Mitigation: P2 + P3 + P6.*
2. **A 529 storm during a real incident.** The incident storm hit the pool
   limit; `timer_won_shed` fired — correct — but every shed alert's
   detached retry kept hammering TypeSafe, the gateways were never wired
   (P1), steps 1–3 never suppressed, and the on-call faced the full
   unfiltered storm with a violet banner as their only tool.
   *Mitigation: P1 + P3.*
3. **A silent model remap under the pin.** TypeSafe shipped `jev-1.14.0`
   remapping choice probabilities; the pin assertion fired `model_drift` —
   but drift detection was the *only* consumer, and the golden set (P5)
   didn't exist, so nobody could say whether the new mapping was better
   or worse; the team rolled back to the pin on principle and lost a
   genuine improvement. *Mitigation: P5.*

---

*Review posture: the race itself is the best-engineered piece of this
boundary — the three-party design, the monotonic clock discipline, the
detach-not-cancel reasoning, and the honest watchdog docstring are
genuinely good. The failures are all one layer out: no second engine, no
cost sensor, no breaker above the per-call race, no honest eval of the
judge or the fallbacks, and a self-calibration loop that is documented
but not built.*
