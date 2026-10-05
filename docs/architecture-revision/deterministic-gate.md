# Deterministic Gate — Phase-2 Domain Review

*Reviewer: Phase-2 domain reviewer (fresh eyes — did not build this). Branch
`program/architecture-revision-dom-gate`. Date: 2026-10-05 IST.*
*Method: read `src/sentinel/gate.py` (1,342 lines), `policy_lifecycle.py`
(570), `attestor.py` (429) end-to-end; `models.py` (Disposition/Thresholds);
`receiver.py` wiring; `config.py` threshold loading; `race_payloads.py`
EMISSION CONTRACT; `tests/test_gate.py` (450 lines), `tests/test_attestor.py`
(368 lines); `docs/adr-attestor-identity.md`; `ARCHITECTURE.md` §4–§8.
Measured against `research/swe-discipline.md` (Stream 1) and
`research/architecture-patterns.md` (Stream 2).*
*Verdict posture: "it's fine" is not a finding. Two findings below are
defects, not nits — one is a trust-root hole.*

---

## 1. WHAT EXISTS

### 1.1 Decision flow (`gate.py`)

`Gate.evaluate(alert, state, history, context, correlation)` returns
`(Disposition, DecisionRecord)` and **never raises** — the outer `except`
converts any exception to `passthrough` with `reason="error:<code>"`, and an
audit row is written even on error. The internal `_decide` pipeline, in order:

1. **S1 — structural bars (deterministic, pre-Jev):** via the optional
   `correlation` kwarg from the Correlator: `duplicate` inherits the prior
   disposition (`reason="dedup"`), `change_window` → `page_business_hours`,
   undeclared storm continuation → `folded` (D3 — absorbed into the aggregate
   page, explicitly *not* suppression). No Jev call, no race armed.
2. **D6 — instruction-firewall screen** (`firewall.apply_firewall`): a hit is
   always `page_now` (fail-closed); the firewall never suppresses; flagged
   alerts never arm the race, so no model ever sees the hostile text.
3. **C1 — stepped fail-open ladder** (`failopen.py`): while stepped down
   (step > 0), a deterministic degraded path *replaces* the race — no Jev
   call. Step 1 = last-known-good signed policy (freshness-gated, C5
   auto-falls to step 2 if the policy is stale *at decision time*); step 2 =
   static severity floor; step 3 = rate-capped paging + digest. Each
   transition is a named, event-logged `failopen_step_entered` event with a
   violet-banner payload. The stepped path never consults the suppress
   conjunction. Documented honest exception (R15 F4): S1 dedup above *can*
   emit `suppress` while degraded — inheriting a pre-degradation prior earned
   by the full conjunction, reason stays `"dedup"`.
4. **S2 — race-to-page** (`race.py`): the Jev call races a budget timer on the
   inference pool. Timer's default action is deterministic `passthrough`. A
   late answer becomes a `shadow_decision` payload via the late-answer hook —
   it never pages, never suppresses, never re-opens the decision.
5. **Answered path** (`_on_answered`): hot-path model pin asserted FIRST
   (ADR-015 — a drifted model ⇒ `passthrough`, reason `"model_drift"`, plus a
   named `model_drift` event; the answer is untrusted evidence and never
   reaches the kernel); then the quantized leg-1 prob lock, the effective
   allowlist (incl. the ADR-017 legacy v1→v2 migration window, loudly logged,
   never merged into the kernel's set), the cached freshness report, and the
   shared composition `_resolve_suppress_path` = **policy kernel → D8 policy
   gate → D5 corroboration leg** (DR-26: the live path, the late-answer path,
   and the D9 counterfactual receipt all call this one composition — no
   second implementation to drift).
6. **Timer-won path** (`_on_timer_won`): immediate `passthrough`
   (`"timer_won"` / `"timer_won_shed"`), no lock evaluation, no waiting.

`digest_storm` is a **separate code path, not a flag**: it never calls
`evaluate_policy`, never arms a race, and cannot suppress by construction
(`storm_digest_disposition` takes no action parameter). The aggregate's Jev
call runs detached as an advisory for root-cause candidates only.

### 1.2 The policy kernel (`evaluate_policy`) — the frozen §4 table

A pure function (no I/O, no clocks), shared by the live gate, the late-answer
path, the shadow tap, and the D9 counterfactual receipt. Branch order,
verified against `ARCHITECTURE.md` §4:

| Branch | Condition |
|---|---|
| `passthrough` / `"uncertain"` | Q3 choice `cannot_determine` |
| `suppress` / `"allowlist"` | `prob_lock_pass` AND `q3_conf ≥ suppress_conf_min` AND fingerprint ∈ allowlist AND every freshness leg fresh |
| `page_now` / `"freshness:<legs>"` | suppress triple-lock held but a freshness leg stale — pages NOW, names every stale leg |
| `page_now` / `"threshold"` | P(p1)+P(p2) > `page_p1p2_min` (0.30) OR Q3 conf < `uncertain_conf_max` (0.50) OR conf None — uncertainty pages |
| `page_business_hours` / `"threshold"` | P(p3)+P(p4) ≥ 0.50 AND conf ≥ `queue_conf_min` (0.70) |
| `passthrough` / `"uncertain"` | else |

The numbers live in the `Thresholds` dataclass (`models.py:52-58`;
defaults 0.002 / 0.90 / 0.30 / 0.50 / 0.70), loaded from `thresholds.json` via
`ConfigLoader` (schema validation + `_semantic_check`; refuses to run on
missing/unparseable files; last-good generation fallback). The 0.002 is
derived in `ARCHITECTURE.md` §4 from expected-cost math
(C_FP=$100, C_FN=$50,000 → p* ≈ 0.002), both constants tagged `[ASSUMED]`;
`tuner.py` grid-searches `suppress_conf_min` against labeled history and
prints the tradeoff table but never decides.

### 1.3 Suppression locks — the conjunction that must ALL hold

Suppress requires, in series: (a) **quantized leg 1** (`quantized.py` —
per-org calibration fit, or dual-attestation interim pre-fit; any failure
fails the leg closed); (b) **confidence floor**; (c) **allowlist membership**
(+ legacy-window resolution); (d) **ADR-014 freshness legs** — the cached
`FreshnessReport` (V1 boot + V2 heartbeat, O(1) read, never validated on the
hot path); stale ⇒ veto, and *no monitor wired ⇒ suppress unreachable*
(fail closed); a lock-1 interim dual-attestation flag is explicit operator
evidence, never a default; (e) **ADR-022/D8 policy gate** — `PolicyGate`
fresh-reads the policy-state JSON file on *every* suppress verdict (never a
boot cache); frozen/expired/unreadable ⇒ downgrade to `passthrough`
(`"policy_blocked:<why>"` / `"policy_state_unreadable"`); (f) **ADR-019/D5
corroboration leg** — suppress must ALSO be corroborated by an independent
signal, evidence *verified in the leg* (signed resolves need the attestor
registry; absent registry ⇒ they don't corroborate); uncorroborated ⇒
`page_now`, reason `"uncorroborated"` — never silent. The leg's verdict is
merged into the decision record's lock evaluation, so it is visible.

### 1.4 Policy lifecycle + attestation (`policy_lifecycle.py`, `attestor.py`)

The B3 six-state machine (`draft → shadow → canary → live → review-due →
expired`), automatic clock transitions via `tick()` (live→review-due,
review-due→expired, emergency-override auto-revert), a sign-off matrix
(2 distinct attestors + preview hash for shadow→canary and canary→live;
attestors distinct from the author), content freeze on live, no
expired→live, watchdog freeze/unfreeze (unfreeze needs 2 attestors). The
attestor ADR (decided 2026-10-04 by a four-principal panel with recorded
disagreement) names the trust root: Ed25519 keypairs in an HMAC-sealed
registry (`SENTINEL_ATTESTOR_ROOT_KEY`), hash-chained ceremony log, 2-person
onboarding ceremony, single-operator one-way-latch revocation (fail-safe
direction), `quarantine_revoked` freeze-sweep over live versions a revoked
attestor touched, attestation signatures binding
`(policy_id, version, content_hash, from_state, to_state, decided_at)` with a
7-day max age / 5-minute future tolerance — the content-hash binding closes
the replay gap Tripwire found. Registry-less string-ID mode is deprecated
with a loud warning. `tests/test_attestor.py` (16 tests) covers the
fail-toward-paging chain: revoked ⇒ transition raises ⇒ version never live ⇒
`can_suppress` False ⇒ gate pages.

### 1.5 What the code does well (credit where due)

- The fail-open direction is *relentless and consistent*: every error,
  timeout, malformed answer, missing monitor, stale proof, drifted model,
  unreadable policy file falls toward the human. This is the single most
  important property of a paging middleware, and it is implemented, not
  asserted.
- The D9 counterfactual design (re-running the *same* kernel with axis
  flags rather than a second implementation) and DR-26 (one composition for
  live/late-answer/shadow) are exactly the anti-drift discipline the
  enterprise research prescribes.
- The attestor ADR is genuine Type-1 governance work: panel process,
  recorded disagreement (Vault vs Forge on retroactive invalidation, resolved
  by Pager to freeze-quarantine), rejected alternatives, "what would change
  it" — this is the Amazon/Google design-doc shape, done once, for the
  trust root.
- `tests/test_gate.py` pins each policy-table branch, the fail-open paths,
  shadow semantics, and audit-row-always-written.

---

## 2. WHAT'S MISSING

Measured against the Phase-1 enterprise standard (Stream 1: design-doc
culture, reviewer bars, IEEE-830 requirements; Stream 2: explicit layers,
contracts, anti-corruption, failure isolation). The policy table and the
disposition vocabulary are **Type 1** (principal-systems: irreversible —
they decide, for every customer, who gets paged at 3 AM; the reason strings
are pinned by tests, i.e. a de-facto frozen API). They are currently governed
as Type 2.

### 2.1 THE DAMNING FINDING: the attested lifecycle governs nothing the kernel reads

There are **two parallel policy-change paths**, and the governed one is not
the one that decides suppression:

- **Path A (governed):** the B3 lifecycle + Ed25519 dual attestation, with
  state machine, freeze, expiry, quarantine. Impressive — and **unwired**:
  `PolicyStore(` is never instantiated outside tests and the module itself;
  `create_draft`/`transition`/`unfreeze` have no non-test callers in `src/`
  or `scripts/`; no CLI, script, or operator runbook drives a policy through
  draft→shadow→canary→live. The only production touchpoint is the D8
  `PolicyGate` *read* in `receiver.py` (fresh-read of the policy-state JSON
  file per suppress verdict) — a file whose provenance no shipped code
  establishes through the attested lifecycle.
- **Path B (actually used):** `thresholds.json` → `ConfigLoader` → the
  kernel's `Thresholds`. Schema validation and `_semantic_check` only — **no
  attestation, no state machine, no expiry, no freeze, no two-person rule**.
  An operator edits `suppress_p1_max` from `0.002` to `0.9` in a JSON file
  and the kernel obeys on the next load. `PolicyVersion.content_hash` binds
  *policy content*, but nothing requires that content to be (or match) the
  thresholds the kernel evaluates — the attestation's content-hash binding,
  the mechanism Tripwire identified as the replay-gap closure, binds an
  object the decision path never consults.

The concrete incident this enables (pre-mortem, in past tense): *during a
noisy on-call night, an operator "temporarily" widened the suppress gate in
`thresholds.json` to stop pages. There was no attestation, no expiry, no
review-due clock. The change persisted silently for six weeks; a true SEV1
matching the widened gate was suppressed. The postmortem found the B3
lifecycle had never been invoked once in production — the governance was
theater over an empty stage.*

Compounding it: the D8 missing-file semantics are **fail-OPEN**
(`PolicyGate.can_suppress` returns `(True, "policy_gate_not_configured")`
when the policy-state file doesn't exist — one stderr warning, then the
triple lock stands alone and suppression is allowed). Compare
principal-systems' eternal friction: the "watchdog never set up" state is
precisely the state a rushed first deployment ships in, and it is the state
in which suppression is *least* supervised. Envoy's panic mode is an explicit
quantified degradation; this is an unquantified permanent one.

### 2.2 No written requirements per suppression rule (Stream 1, §3 — the biggest repo-wide gap, acute here)

- **Zero `REQ-` identifiers anywhere in the repo** (verified by grep). The
  suppress/page rules are derived in prose (`ARCHITECTURE.md` §4,
  expected-cost math with `[ASSUMED]` constants) — derivation is not a
  requirement. IEEE 830's gates fail: no requirement is uniquely
  identifiable, ranked (essential vs desirable), verifiable (no
  per-requirement check method named *before* implementation), or traceable
  (no requirement → design → test linkage).
- There is **no traceability matrix**: `evaluate_policy`'s branches are
  tested individually in `test_gate.py`, but nothing machine-checks that the
  doc's frozen §4 table *is* the kernel — prose and code can drift silently,
  and a new branch can land without any written requirement existing.
- No PR/FAQ or customer-voice acceptance bar for the policy table; no
  Definition of Ready for policy changes ("no lane starts without named
  acceptance criteria" — Stream 1). The gate IS the product's policy, and it
  has no requirements document.

### 2.3 No design-doc gate for policy changes (Stream 1, §1)

- The retention RFC is the *only* pre-build gating doc with a panel; the
  attestor ADR was decided pre-build (good). But **kernel changes** — a new
  branch in `evaluate_policy`, a new `Thresholds` default, a new suppress
  leg — go through `thresholds.json`/PR review with no required design doc,
  no author-written risk paragraph (Amazon), no rejected-alternatives
  section (Google), no named domain-expert routing.
- **No ownership map** (Stream 1 §1d/§2d; principal-governance): no
  CODEOWNERS; nothing names who must review `gate.py` /
  `policy_lifecycle.py` / `attestor.py`. The PR #65 exhibit (a `cryptography`
  import violating the frozen stdlib-only decision, merged and caught
  post-merge by Petu) proves the review checks *what the code does*, not
  *what constraints it breaks* — Bacchelli & Bird's lesson: review's real
  job here is the evolvability/contract check.
- No published reviewer bar (Google's design→functionality→complexity→tests
  order); the PR template is author-side DoD only.

### 2.4 Stringly-typed disposition vocabulary, ~150 literals, no machine contract (Stream 2, §3.4.5)

- `Disposition.action` is a bare `str` (5 values); `Disposition.reason` is a
  bare `str` with **templated values** (`"error:<code>"`,
  `"freshness:<leg>|<leg>"`, `"policy_blocked:<why>"`,
  `"failopen_step{step}_error"`, `"timer_won_shed"`, `"model_drift"`,
  `"uncorroborated"`, `"folded"`, `"dedup"`, `"change_window"`, `"storm"`,
  `"storm_digest"`, `"commit_watchdog_trip"`, `"label_pipeline_stale"`,
  `"operator_resolve"`, `"verified_resolve"`, `"shadow"`…). The comment on
  `models.py:32-34` documents **8** reasons; the codebase emits **20+**.
- `_mark_legacy_resolution` carries the comment *"the disposition `reason`
  taxonomy is deliberately unchanged (`"allowlist"` — asserted by tests)"* —
  i.e. the vocabulary is a **de-facto frozen API pinned by tests, with no
  version, no deprecation protocol, no additive-change discipline**
  (contrast Stream 2 §4.1: Google/Azure/Stripe versioning consensus; RFC
  9745/8594 deprecation protocol). The attestor ADR taught this organization
  the bare-strings-as-trust-roots lesson; the same reasoning applies to the
  vocabulary that decides whether a human is paged — with higher stakes.
- `gate.py` imports **14 sibling modules directly** (firewall,
  corroboration, race, client, correlator, counterfactual, failopen,
  freshness, models, questions, quantized, race_payloads, state,
  storm_digest); `receiver.py` imports gate, policy_lifecycle, correlator,
  forwarder, audit, shadow, health. No ports, no dependency inversion —
  Stream 2's hexagonal test ("run core logic in tests with zero
  infrastructure") passes for `evaluate_policy` alone but fails for `Gate`.
  The "AI judge supplies judgment only; the gate owns policy" boundary is
  asserted in prose, not enforced by a seam.

### 2.5 Missing anti-corruption at the gate boundary (Stream 2, §3.4.2–4)

- `Alert.raw` (the full vendor payload) and `Alert.source` (vendor identity)
  flow through the correlator, the gate, and into AI state-shaping. The
  hexagonal rule says the edge translates and the core never sees foreign
  shapes; PagerDuty's `dedup_key` shows the alternative (declared identity
  over derived fingerprint). Vendor bytes crossing the decision core is a
  trust issue, not a style issue — the D6 firewall screens *some* hostile
  text, but the raw payload's presence in the core is the attack surface the
  screen exists to mitigate.

### 2.6 No per-alert trace ID / per-stage latency attribution (Stream 2, §3.2; principal-governance)

- The event log and shadow pipeline exist, but no correlation ID is carried
  receiver→correlator→gate→forwarder→platform API, and there is no
  per-stage latency attribution on the hot path. When a decision is slow,
  no single lookup answers "which stage ate the budget" — the Envoy/gateway
  practice of one ID through every hop is not implemented. (The race emits
  `latency_ms` per decision; attribution across *stages* is what's missing.)

### 2.7 No circuit breaker on the Jev dependency (Stream 2, §2.1/§6.3)

- Sentinel's fail-open *is* Envoy's panic mode — but Envoy quantifies the
  path to panic (breaker thresholds, outlier ejection, panic threshold,
  re-admission probing). Sentinel has only the per-call budget race: every
  Jev call is treated as independent; sustained latency drift or error-rate
  elevation trips no breaker. The C1 ladder watches timer-win rate, and
  canary probes run only *while* degraded — there is no outlier detection
  feeding the ladder, no half-open probing on recovery. The transfer from
  the research is direct and mechanical.

---

## 3. CONCRETE REVISION PROPOSALS

Ranked by value = (trust impact) × (probability the gap bites before the
design partner). Each names what would verify it. Type labels per
principal-systems (Type 1 = RFC-grade rigor; Type 2 = decide fast).

### P1 — Unify the policy-change path: bind the kernel's numbers to the attested lifecycle (TYPE 1)

**Rationale.** §2.1: the dual-attestation regime currently governs an object
the decision path never consults, while the numbers the kernel evaluates
change via an unattested JSON edit. This is governance theater over the
highest-stakes surface in the system — worse than no governance, because the
ADR's existence will be cited as the control. Chesterton's fence does not
apply: the fence was built but never connected to the field.

**Proposal.** One of: (a) `PolicyVersion.content` becomes the canonical
`thresholds.json` (+ allowlist generation); `ConfigLoader` refuses any
generation whose content-hash is not covered by a LIVE/REVIEW_DUE attested
version — mismatch fails toward paging, never suppress; or (b) the lighter
honest alternative: an ADR stating that `thresholds.json` changes require
the same 2-attestor sign-off via PR (reviewer-side bar, §P5), with the B3
lifecycle's expiry/freeze applying. Either way, ship the missing operator
entry point: a CLI driving `create_draft → … → live` (execution doctrine:
manual first — a 10-line ceremony beats the unbuilt one), and flip D8's
missing-file semantics from fail-open to fail-closed (`(False,
"policy_gate_not_configured")`) once the store is initializable — the
"watchdog never set up" state must page, not suppress unsupervised.

**Verifies by.** A test mutating `thresholds.json` without attestation ⇒
suppress unreachable (gate pages); a test with an attested LIVE version
covering the thresholds ⇒ suppress reachable; the operator runbook actually
executed once end-to-end (draft→live via the CLI) before the design partner;
`PolicyStore(` instantiated outside tests.

### P2 — Machine-check the disposition vocabulary (TYPE 1 for the vocabulary; Type 2 for rollout)

**Rationale.** §2.4: ~150 bare-string literals decide paging; the taxonomy is
pinned by tests but versioned nowhere; `models.py` documents 8 of 20+
reasons. This is the exact bare-strings lesson the attestor ADR already
taught, re-appearing where the stakes are highest.

**Proposal.** Replace `action: str` / `reason: str` with enums (`Action`,
`ReasonCode`); the templated reasons become structured variants (error code,
stale legs, policy-block reason as *fields*, not string interpolation).
Keep wire compatibility via explicit versioned serialization (the event log
is the audit trail — history is never rewritten, per the attestor ADR's own
Type-1 ruling). Add an exhaustiveness test: every reason the kernel, the
fail-open ladder, and the structural paths can emit appears in the enum —
the compiler, not a comment, owns the taxonomy.

**Verifies by.** `mypy --strict` (or pyright) on the gate module with zero
`str`-typed disposition values; the exhaustiveness test green; a follow-up
grep showing zero bare `"suppress"`/`"page_now"` literals outside the enum
definition and its serializer.

### P3 — Write the gate's requirements: identified, ranked, verifiable, traceable (TYPE 2 to write; Type 1 to change)

**Rationale.** §2.2: "frozen §4" is prose. Amazon's rule (Stream 1) —
"mechanisms do" — and IEEE 830: a requirement needs an ID, a rank, a
verification method, and a trace. The gate is where the missing
requirements layer bites hardest because every suppression rule is a promise
to a customer about when they *won't* be woken.

**Proposal.** A `docs/requirements/gate-requirements.md` (or
`requirements/` tree): `REQ-GATE-xxx` per suppression rule and per
fail-open guarantee, each with rank (essential/desirable), verification
method (named test), source (ARCHITECTURE.md §4 line / ADR), and the
`[ASSUMED]` constants explicitly marked as assumptions with owners and
revisit dates. Plus a traceability matrix REQ → `evaluate_policy` branch →
test name, checked in CI (a test that fails if a kernel branch has no REQ,
or a docs lint). New branches require a REQ first — the Definition of Ready
for policy work.

**Verifies by.** Every kernel branch maps to ≥1 REQ and ≥1 test (matrix
complete); the next policy-table PR references a REQ ID and updates the
matrix; the `[ASSUMED]` constants each name an owner and a revisit date.

### P4 — Ports for the gate's seams (Type 2)

**Rationale.** §2.4: 14 lateral imports; the "judge advises, gate decides"
boundary is prose. Hexagonal ports (Stream 2 §3.1) make the boundary real:
the core declares `Corroborator`, `FreshnessMonitor`, `PolicyGate`,
`JevClient` as protocols; adapters implement them; `Gate` takes them by
constructor injection. The pure kernel already passes the hexagonal test —
extend the property to the class.

**Proposal.** Define the four protocols in a `ports.py` (owned by the gate,
not a shared dump — Stream 2: ports live in the domain that owns them);
`Gate.__init__` accepts them (keeping today's constructors as the default
adapters for backward compatibility); move the 14 direct sibling imports
behind the ports. The anti-corruption half (§2.5): the receiver translates
vendor payloads to canonical `Alert`s at ingress and drops `raw`/`source`
before the decision core (audit keeps what it needs via the audit adapter).

**Verifies by.** `Gate` instantiable in tests with fake ports and zero
infrastructure imports; an import-graph test asserting `gate.py` imports
only `models`, `ports`, and stdlib; `Alert` no longer carries `raw` past
the receiver.

### P5 — Design-doc gate + ownership map for policy changes (Type 2)

**Rationale.** §2.3: the PR #65 post-merge catch proves review currently
checks function, not constraints. Google's design-doc mechanism (Stream 1
§1b) routes cross-cutting concerns to named experts *before* code; Stripe
ties doc quality to the career ladder; the repo's own attestor ADR shows
the panel format works here.

**Proposal.** Any change to `evaluate_policy` branches, `Thresholds`
defaults, the suppress conjunction, or the disposition vocabulary requires
a short design doc *before* the PR: problem, rejected alternatives,
author-written risk paragraph ("the strongest reason this could go wrong"),
rollout/rollback plan. Establish CODEOWNERS for `gate.py`,
`policy_lifecycle.py`, `attestor.py`, `quantized.py`, `freshness.py`
(single-threaded owners, principal-governance); the PR template gains a
"policy-change design doc" checkbox and a reviewer-side bar (Google's
order: design → functionality → complexity → tests). Record a kill rate —
most policy-change docs should die at the doc stage; a doc process that
never says no is theater (principal-mindset anti-theater audit).

**Verifies by.** CODEOWNERS file exists and is enforced; the next three
policy PRs each link a pre-PR design doc with a risk paragraph; at least
one policy proposal killed at the doc stage within a quarter, on record.

### P6 — Per-alert trace ID + per-stage latency attribution (Type 2)

**Rationale.** §2.6: principal-governance unified telemetry — one Trace-ID
per unit of work, injected through every hop. Today a slow decision cannot
be attributed to its stage in one lookup.

**Proposal.** Generate `trace_id` at receiver ingress; carry it through
correlator → gate → forwarder → `decision_made` payloads and the platform
API; add per-stage latencies (`stage_latencies: {normalize, correlate,
gate_race, decide, forward}`) to the decision payload body. Observe becomes
a stage (Stream 2 §3.2), not an afterthought.

**Verifies by.** One lookup from any `decision_made` row to the full
stage-attributed trace; a slow-decision drill where the trace names the
stage without log-diving.

### P7 — Circuit breaker for the Jev dependency (Type 2)

**Rationale.** §2.7: the direct Envoy transfer (Stream 2 §6.3). The budget
race is per-call independence; the C1 ladder is the panic mode. What's
missing is the quantified path between them.

**Proposal.** A breaker between gate and Jev, configured above the data path
(config, not hard-coded): opens after N consecutive budget-busts or a
latency/error-rate drift breach (outlier detection over a trailing window);
while open, fail-open (the panic behavior, now named and measured);
half-open probing before full close, feeding the C1 ladder's recovery
signal (the existing canary probe becomes the half-open probe). Thresholds
are Type-2 tunables; the breaker *existing* is the architectural decision.

**Verifies by.** Fault-injection test: Jev latency storm ⇒ breaker opens ⇒
stepped path engaged; recovery ⇒ half-open probe ⇒ close; breaker state
transitions event-logged like the C1 steps.

---

*Pre-mortem note (principal-systems): the failure this revision prevents is
not a code bug — it is a 3 AM operator widening `thresholds.json` with no
attestation, no expiry, and no review, under a governance regime everyone
believes is protecting them. P1 exists so that story cannot be told
truthfully again.*
