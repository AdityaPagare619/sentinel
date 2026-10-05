# Sentinel Roadmap — Phased Build Plan (Planning Session A2)

**Status:** DRAFT — the program entry gate is Aditya's approval of
`docs/architecture-revision/PIPELINE-REVISION.md`. No phase starts before
that. No phase starts with the previous phase's verifications red.
**Branch:** `program/planning/roadmap` (from `origin/program/architecture-revision` @ e2a16f1)
**Date:** 2026-10-05 IST · **Session:** A2 ROADMAP SKELETON (facilitator/scribe)
**Primary input:** `docs/architecture-revision/PIPELINE-REVISION.md` §2–§9
**Parallel session:** A1 (OPERATING-RULES.md) — this roadmap assumes those
rules and is written to be compatible with strict coordination discipline
(see §8).

**Skills applied:** principal-systems, principal-governance,
principal-mindset, execution-doctrine.

**Reading convention.** Every phase has ENTRY and EXIT criteria. EXIT
criteria are *falsifiers* — a phase with no falsifier doesn't start
(principal-systems; PIPELINE-REVISION §6). "Entry gate" means a named,
checkable condition, not a feeling. Confidence on contested calls is
stated as a forced probability (principal-mindset §7).

---

## 0. Session record — deliberation, disagreements, disagree-and-commit

Four contested calls. Each recorded with the argument and the verdict.
After the verdict: total commitment, no re-litigation.

**D1. Emergency sequencing of R-3 (unauthenticated `/v2/enqueue`).**
The security chief argued R-3 is live-exploit shaped — anyone with a TCP
connection can inject arbitrary `critical` triggers — and belongs in
Phase 0, not Phase 1. The coordination chief countered: the sender
cutover cost is real; enforcing HMAC without the migration plan risks a
self-inflicted outage of legitimate senders, and without the post-merge
mechanical gate (R-11) the fix itself ships unverified. The chiefs also
noted the interim mitigations (bind default 127.0.0.1, onboarding-mode
loudness) are the compensating controls that buy the three weeks.
**Verdict:** R-3 stays in Phase 1, but Phase 0 must deliver the sender
inventory and the HMAC migration design doc (auth matrix target, cutover
plan, onboarding-mode window) so Phase 1 has no discovery work left.
Forced probability 85% the sequencing is right; the falsifier is
explicit (any legitimate sender broken by the cutover = the verdict was
wrong, and the migration doc is where that risk is named).

**D2. The post-merge runner (R-11) — Phase 0 or Phase 2?**
CI has been dead since Oct 2 (`startup_failure` streak); every PR since
merged on the honor system. The testing chief argued the mechanical gate
is honesty work and belongs in Phase 0. The devops chief countered:
Phase 0 is one week of claim-vs-reality cleanup; the runner needs a few
days of build plus 7 days of verdict history to mean anything.
**Verdict:** Phase 0 lands the honesty half — the PR template names the
GHA status honestly and requires a green full-suite artifact to merge —
and Phase 2 lands the runner. The runner's first job retro-verifies
every SHA merged since Oct 2; any red SHAs get incident entries, not
excuses.

**D3. R-9 disposition enums — now or late?**
The governance chief argued the vocabulary is Type 1 and everything
(C4, the event log, the UI) references it, so the enums should land in
Phase 1. The gate chief countered: the migration touches every layer's
literals (~150 bare strings); doing it before the contracts (C4) and
the harnesses exist is exactly how you break the vocabulary silently.
**Verdict (decision now, migration late):** Phase 1 ratifies the
Type-1 vocabulary *decision* — the enum definitions plus the versioned
wire-serialization spec, reviewed as a design doc — and the code
migration stays in Phase 5 where the harnesses catch it.

**D4. R-6 degraded-judge tier — build in Phase 3 or not at all?**
The principal-mindset chief argued a fallback built before the eval
harness (R-17, Phase 4) is fallback theater by definition: "test the
fallback with the same eval suite or you shipped theater."
The race chief countered with Manhattan discipline: build the bet cheap
in shadow, promote only on evidence — which is exactly what §8 says.
**Verdict:** R-6 is built in Phase 3 but runs in SHADOW ONLY; its
promotion is gated on R-17's golden set in Phase 4. Dated kill
condition: if R-17 is not green by Phase 4 exit, rung 1 stays shadow
and gets a kill review on a named date — a clean kill beats a slow
bleed.

---

## 1. Phase 0 — Honesty triage (week 1)

**Goal:** close the claim-vs-reality gaps before any new capability.
(principal-systems law 5: global first — the claims are the global
system; the theater compounds.)

**Work items:**
- **R-10** — flags wire-or-retract: either wire `flags.json` into the
  live gate (schema + loader, per `ops/devops-foundation.md` §2.1) or
  retract the `<5s` claim, the kill-switch drill, and the "no emergency
  code rollbacks" claim from the docs. No third option.
- **§4.2#5** — rewrite `docs/deploy-production.md` §8 to mandate
  `deploy.sh` (pinned SHA → health-gated swap; `git pull` on the prod
  box forbidden by policy).
- **R-11 (honesty half)** — PR template names the GHA `startup_failure`
  status honestly; "full suite green, artifact attached" becomes a
  written merge requirement (the mechanical half arrives in Phase 2).
- **§4.2#11** — create `ops/incidents/` + land the blameless postmortem
  template (Summary → Impact → Root Causes → Trigger → Resolution →
  Detection → Action Items → Lessons incl. "where we got lucky" →
  Timeline).
- **§4.2#9** — publish the page-vs-ticket classification table with
  runbook links (every page actionable).
- **Receiver R-8 (exception-path fix)** — malformed JSON increments
  `unparseable`, not `handler_panics`.
- **Forwarder P6** — simulated-spill audit fix: `simulated: true`
  propagates; the log never records a non-failure as `forward_failed`.
- **D1 fallout** — sender inventory + HMAC migration design doc for
  R-3 (auth matrix target, cutover plan, onboarding-mode window);
  interim mitigations documented.

**ENTRY:** Aditya approves `docs/architecture-revision/PIPELINE-REVISION.md`
(program gate). Definition of Ready per Session A1 (design doc, owner,
reviewer routing, risk paragraph, rollback plan) holds for each lane.

**EXIT (falsifiers):** grep-verified zero doc claims of unwired
capabilities; `ops/incidents/` exists with the template; malformed JSON
increments `unparseable`; simulated spill ⇒ `simulated: true` with zero
`forward_failed`; the R-3 migration design doc is reviewed and approved
on its own.

**Dependencies:** none (this is the foundation). Unblocks everything:
no phase starts until this exit is green.

**Owner domains:** devops-environments, testing-qa (R-11 honesty half),
receiver, forwarder-byok.

---

## 2. Phase 1 — Trust boundaries (weeks 2–3)

**Goal:** every asymmetric trust posture is fixed or explicitly gated.
(principal-systems: eternal friction — trust postures are where the
adversary lives.)

**Work items:**
- **Contracts C1–C7 first** — the versioned artifacts (ingress JSON
  Schemas, canonical `Alert` anti-corruption, typed `TriageResult`,
  `Disposition{action, reason}` vocabulary, trace-ID envelope,
  `ForwardReceipt` atomicity, `/api/v1` read contract + `openapi.yaml`)
  are written and reviewed *before* implementation code
  (principal-governance: contract first, implementation parallel).
- **R-2** — one key-resolution contract: documented order
  (IntegrationStore → `secret:`-mapped env → `PD_ROUTING_KEY` env →
  `SecretMissing`); every forward path uses the single resolver; loud
  warning when the UI reports configured but the durable path cannot
  resolve. TYPE 1 contract, Type 2 code.
- **R-3** — `/v2/enqueue` HMAC (ADR-005 reuse): **BUILD GATED on Q2.**
  Proceeds without Aditya: sender migration design (from Phase 0),
  onboarding-mode cutover plan, the contract doc stating the ingress
  auth scheme per route. Waits: HMAC enforcement on the route.
  Fallback if declined: an honest ADR recording the accepted risk with
  the compensating controls (bind posture, onboarding loudness) named
  — no silent exposure.
- **R-16** — platform write-surface auth (bearer token on the five write
  endpoints) + versioning unification: everything canonical under
  `/api/v1`; old paths as `Deprecation` (RFC 9745) + `Sunset`
  (RFC 8594) aliases; deprecation policy in
  `platform/contracts/README.md`. TYPE 1.
- **R-1** — policy-path unification: **BUILD GATED on Q3.**
  Proceeds without Aditya: design docs for both options
  ((a) `PolicyVersion.content` canonical thresholds vs
  (b) 2-attestor PR rule with B3 expiry), the operator CLI ceremony
  prototype. Waits: kernel refusing unattested generations, the D8
  missing-file flip to fail-closed (presumes the unified store).
  Fallback if declined: option (b) is the floor — strictly cheaper,
  closes the theater, under deputy authority; record the disagreement.
- **R-19** — ingress hardening: socket/read timeouts (slowloris),
  512 KiB cap on the PD route, replay cache inside the 5-min window,
  (exception path already fixed in Phase 0).
- **R-9 (decision, not migration)** — ratify the Type-1 vocabulary:
  enum definitions + versioned wire-serialization spec as a reviewed
  design doc (D3 verdict); code migration stays Phase 5.

**ENTRY:** Phase 0 exit green. Each lane meets Definition of Ready.

**EXIT (falsifiers):** UI-only key ⇒ `forward_confirmed` through the
durable path (R-2 E2E); 401 matrix green on all five write endpoints;
old paths emit `Deprecation`+`Sunset`; 600 KiB PD body ⇒ 413;
sign-and-double-POST ⇒ second refused with a distinct reason code;
the R-9 enum spec is ratified with principal sign-off. Where Q2/Q3 are
still gated, the exit is instead the reviewed design docs + named
fallbacks — a gate marker is not a red phase.

**Dependencies:** Phase 0 (honesty first); C1–C7 written docs precede
their implementations *within* the phase. Unblocks: Phase 2 (platform
contributes read-tier series to the shared metrics module), Phase 4
(contract-conformance tests test against these artifacts), Phase 5
(R-9 migration, R-18 audit chain serving).

**Owner domains:** receiver, forwarder-byok, deterministic-gate,
platform-api, prism-ui (contract review).

---

## 3. Phase 2 — Observability backbone (weeks 3–4)

**Goal:** make the system legible — to itself first, then to operators.
(principal-governance: unified telemetry; you cannot govern what you
cannot attribute.)

**Work items:**
- **R-12** — one shared stdlib metrics module (A2: single owner, not
  four implementations): `/metrics` in Prometheus exposition on the
  receiver (hot path), platform read-tier series, one UUID born at L1
  ingress on every log line and event-log row, first availability SLO
  (e.g. 99.9% real pages `forward_confirmed` within 30 days; p99 gate
  latency ≤ 2700 ms) + error-budget arithmetic as a decision doc.
  The decision to have an SLO is Type-1-ish; the values are tunable.
- **R-11 (mechanical half)** — box-side post-merge runner on a clean
  worktree (cron/systemd timer): full gate per new SHA, per-commit
  verdicts. **Q5: blessed permanent** — the runner is the standing
  gate regardless of the GHA escalation (which remains Aditya's call
  but gates nothing).
- First-run retro-verification: the runner verifies every SHA merged
  since Oct 2; red SHAs become incident entries (D2 verdict).
- **§4.2#10** — burn-rate alerts on the watcher (14.4× page / 6× page
  / 1× ticket, multi-window) replacing raw threshold polling.
  Depends on R-12 metrics + SLO; sequenced last deliberately.
- **§4.2#14** — wire `record_request()` at receiver ingest (unlocks
  per-source heartbeat subjects, dormant by the docs' own honest
  flag).
- **§4.2#12** — parity/drift audit script (prod box vs repo: pinned
  SHA, deps, Caddyfile, config schema) — "continuously reconciled"
  without Argo CD.

**ENTRY:** Phase 1 exit green (at least the ungated parts; gated items
have their named fallbacks).

**EXIT (falsifiers):** `curl :8080/metrics` returns valid Prometheus
exposition; one synthetic alert ⇒ `grep <uuid>` returns the complete
ingest→decision→forward chain; 7 consecutive days of per-SHA verdicts
with zero skipped gates (a scratch PR with a deliberately failing test
is blocked, visibly, on the record); the SLO doc governs the cutover
checklist.

**Dependencies:** Phase 1. R-12's metrics module is the load-bearing
dependency of Phase 3's experiments (breaker/cost/baseline verifications
all read it) and Phase 4's threshold gates (R-15). R-11 gates all later
merges.

**Owner domains:** devops-environments, receiver, platform-api,
testing-qa.

---

## 4. Phase 3 — Resilience mechanisms (weeks 4–6)

**Goal:** quantified degradation paths — the panic mode gets a measured
road into it and a measured road out (Envoy transfer).

**Work items:**
- **R-5** — durable correlator baseline: persist `(B, rings, attested
  baseline, storm_k)` on a slow cadence + clean shutdown, signed with
  checkpoint HMAC discipline; restore with staleness policy (accept
  < 24h, decay toward floor beyond, always re-enter R15 discrimination
  from a warm start).
- **R-7** — Jev circuit breaker (A4: one breaker, owned at the
  race/gate boundary, configured above the data path): opens on N
  consecutive budget-busts or drift breach; while open, fail-open with
  zero vendor calls; half-open via the existing `probe_vendor` shape;
  states event-logged like the C1 steps; the C1 ladder's entry
  criteria re-expressed in breaker states (no two independent
  degradation state machines disagreeing).
- **R-8** — cost ledger + cost circuit breaker: aggregate per-org
  spend (`$/decision`, `$/min` burn) into the watchdog vocabulary;
  warn/throttle/stop thresholds (throttle → cheaper path → cached
  only); cost events join the event log for invoice reconciliation.
- **R-6 (build, shadow only)** — rung 1: (a) exact-cache on
  `input_sha256` (cheapest rung), (b) alternate pipe for the pinned
  model (same wire shape, routed on `JevOverloaded`/sustained
  timer-wins), (c) degraded engine (open-weights) behind the same
  `decide()` interface, labeled `engine=degraded`, **never allowed to
  suppress uncorroborated**. Runs in shadow on the golden set only.
  **Promotion gated on R-17 (Phase 4).** Dated kill condition if R-17
  stays red past Phase 4 exit (D4 verdict).
- **B-loop** — implement the tuner-side re-derivation or delete the
  docstring claim. No third option.

**ENTRY:** Phase 2 exit green — the experiments need the metrics
module to measure anything.

**EXIT (falsifiers):** kill -9 mid-burst on the seeded C2 harness ⇒
declaration behavior matches pre-kill within 5 min, first-10-min fold
rate within 2× of pre-kill (not ~98%); tampered snapshot ⇒ loud
reject + R15-from-zero; fault-injection latency ramp ⇒ breaker opens
before pool occupancy 80%, billable calls stop within one detection
window, half-open recovery closes within 5 min of vendor recovery;
synthetic 529-storm replay ⇒ throttle trips before spend exceeds 2×
the healthy-hour baseline and the ledger reconciles to a live week's
vendor invoice within 1%; rung 1 shadow evidence accumulating;
B is self-calibrating or honestly manual.

**Dependencies:** Phase 2 (metrics). R-6's promotion depends on Phase 4
(R-17). Unblocks: Phase 5's key-lifecycle and threat-model work (the
checkpoint HMAC discipline R-5 uses is exercised by R-18's chaining).

**Owner domains:** correlator, race/jev-boundary, deterministic-gate,
event-log-audit (HMAC discipline), forwarder-byok (cost ledger input).

---

## 5. Phase 4 — Verification machinery (weeks 6–8)

**Goal:** the measurement instruments exist and bite — every remaining
ranked change is verifiable, and the verifications are wired into the
merge path. (Anti-theater: a gate that can't catch the one leak we
*know* existed is decoration.)

**Work items:**
- **R-14** — correlator eval harness: frozen versioned estate-trace
  corpus (busy-estate "never declare" + real incident storms "declare
  within N seconds"); tiers smoke/regression/hard; declare
  precision/recall + time-to-declare; scores the *current* constants
  first (publishing the baseline — if k=5 loses to k=4, the RFC was
  wrong and the number changes with evidence); the harness also
  produces the 30 days of R samples the `k = P99.9(R)+1.5` rule needs.
- **R-17** — judge eval: frozen hash-pinned golden set (Oct-4 A/B
  traces + production shadow captures, not synthetic fixtures);
  Jev-as-judge calibrated to human-oncall judgment at κ ≥ 0.75;
  **timer-win decomposition standing in every eval** (the Oct-4 lesson:
  ~89% of flip signal was latency artifact); ladder-quality eval
  pricing degradation at each rung; shadow-before-promotion for
  judge/budget/fallback changes; rotation discipline so the set
  doesn't become a training set in disguise.
- **R-13** — vendor-boundary honesty: promote `FakePD` to shared
  `tests/doubles/` (+ schema/fault layers, routing-key-scoped dedup,
  ack machine); stdlib Jev cassette recorder (record-once, replay with
  `none` semantics, secrets scrubbed); scheduled drift harness.
  **Key-provisioning GATED on Q6.** Proceeds without Aditya: harness
  design, shared doubles, cassette infra, stub-recorded cassettes
  marked "shape unverified against live vendor." Waits: team-held
  PD/Jev test keys and the live drift runs. Fallback if declined:
  FakePD-only battery + documented stubs with the stated caveat, and a
  named quarterly manual live-shape check.
- **R-15** — threshold-gated load: promote the C2 harness into
  `tests/load/`; PR smoke (1,000/min × 2 min: e2e p99 ≤ 1.5 s, zero
  silent drops, admission-bound behavior, non-zero exit blocks merge)
  + nightly sustained/spike + weekly soak (`gate.emitted` growth ≈ 0,
  event-log p99 within budget). **Anti-theater check:** reintroduce
  the `gate.emitted` leak on a scratch branch — the soak must go red.
- **Fault-proxy chaos harness (Q7, conditional proceed):** stdlib
  fault proxy with latency/reset/timeout/HTTP-error toxics between
  gate↔Jev and forwarder↔PD; experiment catalogue (Jev timeout storm;
  PD 429/5xx; event-log disk-full; correlator thread-starvation), each
  with steady-state metric + hypothesis + abort threshold; GameDay
  cadence with scorecard — CI/nightly only, **never production, never
  live PagerDuty** until the harness earns trust, and then only with a
  fresh Aditya decision.
- Contract conformance: live platform responses validated against
  `openapi.yaml`; receiver ingress fixtures against the checked-in
  schemas; consumer-driven expectations promoted into
  `platform/contracts/consumer/` (the right-sized Pact).
- Suite hygiene: test-size classification + hermeticity (publish the
  distribution, target ~80/15/5), flaky quarantine with named owners
  and 7-day SLA, no-escape-hatches audit of the sleeping files.
- **R-6 promotion decision:** rung 1 clears the golden set ⇒ serves;
  otherwise stays shadow and hits the D4 kill review.

**ENTRY:** Phase 3 exit green. R-12 metrics available for the load
gates. R-17's human-labeled expectations need human oncall time —
scheduled before the phase starts, not discovered mid-phase.

**EXIT (falsifiers):** every R-14–R-17 verification green; the soak
catches the reintroduced leak; a synthetic vendor drift trips the
harness within one interval (or, if Q6 is gated, the stub-cassette
path is honest about its caveat); R-6 promoted or killed-by-evidence;
no `time.sleep`-as-synchronization in the blocking path.

**Dependencies:** Phase 3. R-17 gates R-6's promotion (D4). R-13's
harness (post-Q6) continuously verifies Phase 1's vendor-facing
schemas. Unblocks: Phase 5's audit-chain serving tests and the final
explicit-layers re-score.

**Owner domains:** correlator, race/jev-boundary, forwarder-byok,
testing-qa, platform-api, devops-environments.

---

## 6. Phase 5 — Lifecycle & completion (weeks 8–10)

**Goal:** the system's full lifecycle is closed — birth to archive —
and the honest-build state is demonstrated in drills, not claimed in
docs.

**Work items:**
- **R-18** — event-log lifecycle: (a) the Type-1 threat-model
  requirement doc per §3.4 (adversaries × detection × time bound ×
  evidence, reviewed by the security principal; disagree-and-commit on
  the insider-writer row — accept the limit in writing or fund
  external anchoring); (b) genesis-rule unification between the two
  verifiers (the `/tmp/evlog-repro` reproduction becomes a regression
  test both pass); (c) drain-before-roll (queued page survives the
  roll, or the roll refuses and pages); (d) checkpoint chaining;
  (e) `GET /api/v1/audit/chain`; (f) archive completeness +
  warm-export round-trip; (g) key lifecycle + GDPR path — wired or
  honestly deleted.
- **R-4** — arm the retention job after dry-run review (the remaining
  orphaned wiring).
- **Key rotation protocol** (`forwarder-byok.md` P3): rotate keys with
  rows in flight against key-scoped FakePD — zero `SecretMissing`,
  zero dropped retries, zero extra incidents.
- **Q4: declare the single-key product boundary** — the versioned
  service→ref map is NOT built; the boundary is documented honestly.
- **R-20 (UI):** first the reconcile-or-migrate RFC with written
  alternatives and principal sign-off — **Q1 PROCEED: bless the
  vanilla stack** (write the constitution amendment with the rationale:
  zero-build deployability, 284 KB total, no toolchain to rot); then
  the appeal control — **GATED on Q8:** plan both options
  (wire-as-audit-logged-override vs remove); fallback if declined is
  **removal from the live build** (a non-functional control must not
  render); mock-honesty invariants (mock replay never maps to
  freshness `live`; fabricated payloads carry `derivedMark` at the
  point of display); token reconciliation; Playwright critical flows +
  visual regression + contrast CI; CSP + trace-ID.
- **R-9 (migration)** — disposition enums across every layer,
  sequenced late deliberately: the C4 contract and the Phase-4
  harnesses exist to catch it now.

**ENTRY:** Phase 4 exit green. The Q8 decision (if it comes) routes
R-20's appeal work down the wired or removed path.

**EXIT (falsifiers):** the explicit-layers checklist
(`research/architecture-patterns.md` §5, 12 items) re-scored — target:
no item still "missing"; verifier agreement + sealed-chain serving +
retention demonstrated in drills; in the `live` build, the appeal
control either pages through the audited path (asserted in CI against
a mock pager) or doesn't render; `mypy --strict` on the gate module
with zero `str`-typed disposition values and the exhaustiveness test
green.

**Dependencies:** Phase 4. R-9 migration depends on Phase 1's ratified
spec. R-18's threat-model doc may constrain the retention and key
work — written first, built second (principal-governance).

**Owner domains:** event-log-audit, platform-api, prism-ui,
forwarder-byok, deterministic-gate, devops-environments.

---

## 7. Type-1 decision gates — Q1–Q8

Status per Petu's gate summary. Each row states: what proceeds without
Aditya, what waits for his word, and the fallback if he declines or
stays silent (silence is not approval — a gate marker is held, and
the fallback is named in advance).

| Q | Status | Proceeds (no Aditya) | Waits for his word | Fallback if declined |
|---|---|---|---|---|
| Q1 UI stack | **PROCEED** | Constitution amendment blessing vanilla JS + written rationale (zero-build deployability, 284 KB, no toolchain to rot); UI work continues on vanilla | — | n/a — deputy authority |
| Q2 `/v2/enqueue` HMAC | **GATED** | Sender inventory + migration design + onboarding-mode cutover plan + contract doc stating auth per route | HMAC enforcement build | Honest ADR recording accepted risk with compensating controls (bind posture, onboarding loudness) named; revisit if exposure changes |
| Q3 policy unification | **GATED** | Design docs for both options ((a) canonical `PolicyVersion.content` vs (b) 2-attestor PR rule) + operator CLI ceremony prototype | Kernel refusing unattested generations; D8 flip to fail-closed | Option (b) — strictly cheaper, closes the theater, under deputy authority; disagreement recorded |
| Q4 per-service routing | **PROCEED** | Declare the single-key boundary honestly in docs; no map built | — | n/a — boundary declared, not built |
| Q5 CI | **PROCEED** | Box-side post-merge runner as the permanent mechanical gate (Q5 verdict) | — | n/a — the GHA escalation is Aditya's call but gates nothing; the runner is permanent regardless |
| Q6 provider test keys | **GATED** | Drift-harness design, shared FakePD doubles, cassette infra, stub-recorded cassettes marked "shape unverified against live vendor" | Team-held PD/Jev test keys + live drift runs | FakePD-only battery + documented stubs with the stated caveat; a named quarterly manual live-shape check on the calendar |
| Q7 chaos vs live PD | **CONDITIONAL** | Stdlib fault proxy + experiment catalogue + GameDay in CI/nightly; never production, never live PD | Live-PagerDuty chaos (fresh decision, only after the proxy earns trust) | Stays NOT-built; the fault proxy is the standing chaos story |
| Q8 appeal control | **GATED** | Both-options design (audit-logged override wiring vs removal); mock-honesty invariants regardless | Wiring "page me anyway" to a real audited paging path | **Remove the control from the live build** — a non-functional control must not render (the honesty default) |

**Gate hygiene (principal-mindset anti-theater):** each gate has a named
single-threaded owner and a date at which it is re-asked, on record.
A gate that is never answered is a decision by default — §8's
pre-mortem failure mode #2 — and the roadmap treats it as such: the
fallback column is the default-by-choice, not by drift.

---

## 8. Deliberately NOT built — the roadmap says no

One-line rationale each, from PIPELINE-REVISION §8. Revisit only with
a written reason and the evidence that changed.

- **Kubernetes / Argo CD / Flux** — mechanisms ported (#6 config-as-
  commits, #12 parity audit), the machinery doesn't scale to a
  two-process stdlib system.
- **LaunchDarkly or any paid flag service** — `flags.json` + the
  lifecycle rule is the 80%, once R-10 wires it; ₹0 is a hard
  constraint.
- **k6 / Locust / JMeter adoption** — stdlib runner is fine;
  thresholds-as-gates is the requirement, not the tool.
- **Full Pact broker / `can-i-deploy`** — right-sized: OpenAPI
  conformance + consumer-owned expectations + drift harness.
- **Stripe-style date-pinned API transformation layer** — machinery
  theater at this scale; revisit with a second external consumer.
- **OAuth/OIDC for the platform tier** — bearer token is the minimal
  Type-1-adequate mechanism for a single-operator tier.
- **mTLS for senders** — revisit if the threat model grows to mutual
  distrust.
- **Ed25519 for checkpoints** — doesn't fix writer-holds-the-key;
  refinement theater.
- **Kafka / message bus between pipeline stages** — the ripped-out-EDA
  test stands (name the flow needing time-decoupling first).
- **GraphQL / BFF split** — the contract and auth are the fix, not the
  rename.
- **Production chaos against live PagerDuty** — Type-1 blast radius;
  Aditya's call (Q7), and only after the fault proxy earns trust.
- **Preview environments per PR** — real value only when contributor
  load grows; deprioritized.
- **Blue-green receiver** — deferred conditional: only if restarts
  measurably lose pages after R-10/#4/#5 land; building it earlier is
  local optimization.

---

## 9. Cross-phase dependency graph (text)

```
Aditya approves PIPELINE-REVISION.md  (program entry gate)
        │
        ▼
PHASE 0 honesty triage
  ├─ R-10 wire-or-retract ──────────────► unblocks flag-dependent work
  │                                       (kill-switch drills, canary-by-flag)
  ├─ PR-template honesty (R-11 half) ────► unblocks honest merging until
  │                                       the runner lands
  └─ R-3 migration design (D1) ──────────► PHASE 1 R-3 build has no
                                          discovery work left
        │ EXIT green
        ▼
PHASE 1 trust boundaries
  ├─ C1–C7 contracts written ────────────► reviewed artifacts that PHASE 4
  │                                       contract-conformance tests run
  │                                       against; PHASE 5 R-9 migration
  │                                       honors C4
  ├─ R-2 key contract ──────────────────► PHASE 5 key rotation + R-18 key
  │                                       lifecycle build on the contract
  ├─ R-16 write-surface auth ────────────► PHASE 5 /api/v1/audit/chain is
  │                                       born authenticated; no unversioned
  │                                       paths for R-18 to remediate
  ├─ R-9 enum spec (D3: decision now) ───► PHASE 5 R-9 migration (code late)
  └─ Q2/Q3 gates ───────────────────────► R-3 / R-1 builds wait; fallbacks
                                          in §7 are the default-by-choice
        │ EXIT green (ungated parts; gates named)
        ▼
PHASE 2 observability backbone
  ├─ R-12 /metrics + trace ID ───────────► PHASE 3 experiments read it
  │  (shared module, single owner)        (breaker pool-occupancy, cost burn,
  │                                       baseline fold rate); PHASE 4 R-15
  │                                       thresholds are gates on it; PHASE 2
  │                                       §4.2#10 burn-rate alerts need it
  └─ R-11 runner ────────────────────────► gates every later merge; first run
                                          retro-verifies SHAs since Oct 2 (D2)
        │ EXIT green (7 days of verdicts)
        ▼
PHASE 3 resilience mechanisms
  ├─ R-5/R-7/R-8 experiments ────────────► all falsifiers read R-12 metrics
  ├─ R-6 rung 1 (shadow only) ───────────► promotion decision lives in
  │                                       PHASE 4, gated on R-17 (D4)
  └─ checkpoint HMAC discipline (R-5) ──► exercised by PHASE 5 R-18 chaining
        │ EXIT green
        ▼
PHASE 4 verification machinery
  ├─ R-17 golden set ───────────────────► gates R-6 promotion (D4);
  │                                       dated kill if R-17 stays red
  ├─ R-13 drift harness (Q6) ────────────► continuously verifies PHASE 1
  │                                       vendor-facing schemas (FakePD,
  │                                       cassettes); stub-cassette caveat
  │                                       honest until keys provisioned
  ├─ R-15 load gates ───────────────────► read R-12 thresholds; the
  │                                       anti-theater leak reintroduction
  │                                       proves the soak is not decoration
  └─ fault-proxy chaos (Q7) ─────────────► trust earned here is the
                                          precondition for ever revisiting
                                          live-PD chaos (fresh Aditya call)
        │ EXIT green
        ▼
PHASE 5 lifecycle & completion
  ├─ R-18 threat-model doc (Type-1) ─────► written FIRST; may constrain
  │                                       retention + key-lifecycle builds
  ├─ R-4 retention armed ───────────────► after dry-run review only
  ├─ Q4 single-key boundary declared ───► map stays NOT-built (§8)
  ├─ R-20 UI (Q1 proceeds, Q8 gated) ───► appeal wired OR removed; never
  │                                       both, never neither
  └─ R-9 enum migration ────────────────► caught by C4 contracts + PHASE 4
                                          harnesses if it regresses
        │ EXIT green
        ▼
  explicit-layers checklist re-scored (target: no "missing");
  roadmap complete
```

**The two dependencies that bite hardest** (named for the chiefs):
1. **R-12 is the load-bearing spine.** Three phases' falsifiers read
   it. If `/metrics` slips, Phase 3's experiments and Phase 4's gates
   have no instruments — the slip must be named in writing with a
   re-sequenced plan, never absorbed silently.
2. **Q2/Q3/Q6/Q8 are the human gates.** Building must never route
   around them quietly. §7's fallback column is the complete list of
   what proceeds; anything else waits.

---

## 10. Operating contract with Session A1

Session A1 (OPERATING-RULES.md) owns the coordination discipline; this
roadmap is written to be compatible with it and assumes:

- **Definition of Ready** per lane (from PIPELINE-REVISION §3.5):
  named acceptance criteria (the REQs served or a written reason for
  none), a short pre-PR design doc (problem, rejected alternatives,
  author-written risk paragraph, rollout/rollback), a named
  single-threaded owner + reviewer routing (CODEOWNERS), a measured
  kill rate at the doc stage, and a goal-fidelity check at lane close.
  No lane in any phase starts without it.
- **Single-threaded ownership** per work item (principal-governance):
  exactly one owner accountable for results on each R-number;
  part-time owners on critical paths are a known failure mode.
- **The contract is the coordination mechanism:** lanes run in
  parallel against the C1–C7 contracts + mocks from hour one;
  a lane blocked on another lane's output works against the contract
  and flags the dependency in the lane registry the same hour —
  work never freezes waiting (principal-governance §5).
- **Disagree-and-commit binds bosses too:** §0's four verdicts are
  final; re-litigation happens in the retrospective, not in hallway
  conversations.

If A1 lands stricter rules, the stricter rules win — this roadmap's
phase gates are floors, not ceilings.

---

## 11. Pre-mortem for this roadmap (principal-systems)

It is December 2026 and this roadmap failed. The three most likely
causes, and the mitigation already written into the plan:

1. **The ranked list was treated as a backlog and built top-to-bottom
   without the phase gates** — Phase 0's honesty triage skipped as
   "not real work," Phase 4's harnesses deferred as "later."
   *Mitigation:* no phase starts with the previous phase's exit red;
   the gates are named, checkable, and owned.
2. **The §9/§7 questions went unanswered while building continued** —
   Type-1 decisions made by default instead of by choice.
   *Mitigation:* gate markers with named fallbacks; a gate never
   answered triggers its fallback on the recorded date, on record.
3. **The resilience mechanisms shipped without the measurement that
   makes them more than theater** — rung 1 promoted on vibes, the
   breaker tuned by feel.
   *Mitigation:* R-6 promotion requires R-17 evidence (D4); the
   anti-theater checks (leak reintroduction, tampered snapshot,
   red-team label edit) are written into the exits.

---

*Session A2 adjourned. Disagree-and-commit recorded. Next: Aditya's
approval of the revision + the §7/§9 answers route the gated work
down its branches.*
