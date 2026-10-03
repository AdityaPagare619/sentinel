# ADR Adjudication — 2026-10-03

**Panel:** Forge (engineering) · Vault (security/trust) · Pager (SRE domain) · Tripwire (adversarial QA)
**Method:** disagree-and-commit. Each chief wrote an independent position memo citing principal law
(`principal-systems`, `execution-doctrine`, `principal-governance` skills), with code verified against
`origin/main @ 2afbf54` where the item touched implementation. The coordinator synthesized; no item was
rubber-stamped.
**Authority:** Aditya's 2026-10-03 20:08 IST order — pending ADRs decided tonight by combined group
discussion. Petu ratifies by PR review. Nothing here is merged until that review.

**Status vocabulary:** `RATIFIED` = decision adopted AND verified implemented on main.
`ADOPTED-DESIGN` = decision adopted, implementation absent or partial — explicitly not done.
`PARTIAL` = some clauses verified, others open. `OPEN` = not decided.

## Where Petu's priors lost (explicit)

1. **"Ratify deltas 010–024 as already implemented" — REJECTED 4–0, with code evidence.**
   Ratified as implemented: 010, 011, 012, 013. Conditionally ratified: 014, 016, 018.
   Adopted-as-design (not implemented): 015, 017, 019, 020, 021, 023. Partial: 022. Open: 024.
   Ratifying aspirational code as done is the honesty violation the standing order forbids —
   it stops the work while the gap stays open.
2. **ADR-005 IP-allowlist "opt-in only" — REJECTED 2–1–1 (Forge+Vault vs Pager vs prior).**
   The opt-in knob is either theater (fail-open) or a footgun (fail-closed on IP rotation).
   Verdict: no allowlist at all; strike the ARCHITECTURE.md §7 mention.
3. **ADR-001 unconditional adopt — CONDITIONED.** Flap-reopen bumps a visible flap/relapse
   count, not severity (deviation from every vendor precedent); P1/P2 window override covers
   Sentinel-side windows only; and Tripwire's DR-13 contradiction must be reconciled.

---

## ADR-001 — Correlator craft alignment

- **Decision:** ADOPT-WITH-CONDITIONS. **Status:** ADOPTED-DESIGN (not in code).
- **Type:** 1 — rewrites the frozen engine contract (ARCHITECTURE.md §3.5); correlation semantics
  decide which alerts the model ever sees.
- **Chief positions:** Forge ADOPT (Chesterton's fence — practitioner conventions encode incidents we
  haven't had). Vault ADOPT (wrong correlation is a trust incident; auto-close is silent suppress by
  another name). Pager ADOPT-WITH-CONDITIONS (flap-reopen must bump a visible flap count, never
  auto-promote severity — no vendor does; window override is Sentinel-side only, PD-side windows are
  invisible to the receiver). Tripwire ADOPT-WITH-CONDITIONS (all four behaviors falsifiably testable;
  **contradiction found:** behavior 4 conflicts with DECIDED DR-13 — `gate.py:293` routes change_window
  → `page_business_hours` unconditionally, so a P1 queued at 02:14 is a silenced P1 with extra steps).
- **Rationale:** The v0.1 correlator's missing craft is load-bearing — bad flap semantics multiply Jev
  calls and erode the one-call-per-storm guarantee (DR-14). Deterministic correlation is the global
  optimization: a better correlator buys more than model tuning.
- **Conditions:** (a) reconcile with DR-13 — P1/P2 carve out of change-window queuing (page now) or
  restate behavior 4 honestly; (b) reopen bumps flap/relapse count, not severity; (c) onboarding
  documents that PD-side maintenance windows are invisible to Sentinel.
- **What would change it:** a flap-storm CI fixture proving reopen/auto-promote creates more
  noise-miss incidents than it prevents; or a vendor source showing auto-promote-on-reopen precedent.

## ADR-005 + DR-27 — Webhook auth hardening

- **Decision:** ADOPT-WITH-CONDITIONS. **Status:** PARTIAL (HMAC/compare_digest/raw-body verified;
  empty-secret-refuses and 5-min timestamp tolerance NOT in code — `receiver.py` `_signature_ok`
  fails open on empty secret and absent signature, contradicting DR-27's text).
- **Type:** 1 — receiver auth is a trust-boundary contract; silent divergence between §7 and DR-27
  is a Type-1 lie in a doc.
- **Chief positions:** Forge ADOPT-WITH-CONDITIONS, REJECT allowlist entirely (eternal friction —
  egress IPs rotate; an opt-in knob failing closed on rotation turns every PD IP change into a
  dropped-alert incident; code-is-liability — an unmaintained security knob is theater that breaks
  loudly). Vault ADOPT-WITH-CONDITIONS, explicitly against the opt-in compromise (opt-in becomes
  de-facto required the first time an anxious buyer asks; buys nothing HMAC doesn't already buy;
  plus: unsigned-accepted ingest cannot mint trustworthy resolve evidence for ADR-019's shape (b)).
  Pager ADOPT (agree with prior, strengthened: opt-in must fail open — warn, never reject a
  validly-HMAC'd webhook). Tripwire ADOPT-WITH-CONDITIONS (5-min window without nonces is a 5-minute
  replay window; "no allowlist" is posture, falsifiable only by an allowlist-preventable incident).
- **Rationale:** HMAC is the real authentication; IP pinning is a brittle second opinion that fails
  in the exact direction we cannot afford (silence, not noise). The current code's fail-open
  contradicts the decision text — pick one, they can't both be true.
- **Conditions:** (a) adopt all five hardening clauses; (b) fix code to refuse on empty secret in
  production mode (explicit flagged onboarding mode may fail open with a boot-time loud warning);
  (c) add 5-min timestamp tolerance + replay-window analysis; (d) REJECT the IP allowlist entirely
  and strike the ARCHITECTURE.md §7 "optional IP allowlist config" mention in the same verdict.
- **What would change it:** a design partner whose compliance regime mandates source-IP pinning with
  a funded rotation feed and written risk acceptance — then it's their funded liability, not our
  default surface.

## ADR-007 — Muted-not-dropped

- **Decision:** ADOPT-WITH-CONDITIONS. **Status:** ADOPTED-DESIGN (zero "mute" in `src/`).
- **Type:** 1 — extends the DR-21 disposition set; implemented as a platform-tier rendering label
  (Type 2 mechanics) over suppress-with-reason, never a fifth engine disposition.
- **Chief positions:** Forge ADOPT-WITH-CONDITIONS (platform label only — widening the gate's enum
  leaks the data model across the DR-9 process boundary). Vault ADOPT-WITH-CONDITIONS (mute is an
  event-logged transition with reason + attestor + TTL; no auto-mute ever; mute rates in the weekly
  report; muted suppressions still carry the ADR-023 counterfactual). Pager ADOPT-WITH-CONDITIONS
  (incentive realignment — without a named owner and a weekly "what we muted" review ritual, mute is
  /dev/null with better lighting and the fatigue ratchet moves toward it). Tripwire
  ADOPT-WITH-CONDITIONS (CI invariant: every suppression-class disposition retrievable from
  quarantine within bounded staleness; appeal writes a `mute_appealed` event; muted rows visible by
  default, never filter-excluded).
- **Rationale:** Buyer resonance is real and paging-path impact is zero by construction. But mute is
  where the fatigue ratchet goes to retire — governance is the decision, not the label.
- **Conditions:** (a) engine disposition enum stays four-valued; (b) mute transitions are
  event-logged (reason, attestor, TTL); (c) no auto-mute, ever; (d) weekly mute review in the shadow
  report with a named owner; (e) the Tripwire quarantine invariant in CI before the design partner.
- **What would change it:** a design-partner pilot showing operators use mute as documented triage
  rather than a fatigue shortcut — then the attestor requirement relaxes.

## ADR-010 — Race-to-page

- **Decision:** ADOPT. **Status:** RATIFIED (implemented #16; B=2700ms via #25, verified
  `race.py:89` with the re-derivation formula inline).
- **Type:** 1 — paging-path latency semantics; B is a safety budget.
- **Chief positions:** unanimous ADOPT. Forge (global over local — the vendor's 11.4s tail is removed
  from the path, not tuned around). Vault (late-answer `shadow_decision` events are structurally
  incapable of paging/suppressing — `decided_disposition` always passthrough; residual risk is Prism
  rendering, which must show them as counterfactuals). Pager (at 3 AM nobody feels 2.7s vs 0.3s;
  boundedness is what matters; honest degradation is the brand). Tripwire ADOPT-WITH-CONDITIONS
  (n=100 is thin for a safety-critical budget — p99 from 100 draws has a wide CI; the timer-win
  watchdog is the live falsifier).
- **Rationale:** The cleanest failure semantic in the system: slow vendor ⇒ passthrough, late answer
  ⇒ shadow event. The re-derivation protocol (seeded 1000ms was wrong; measurement corrected it) is
  exactly how budgets should work.
- **Conditions:** B stays provisional — re-derive at N≥1000 with a distributional argument; timer-win
  watchdog trips are B-falsification events; B remains a measured per-region quantity, never a
  magic constant.
- **What would change it:** an N≥1000 campaign with error bars on the tail, or three months of
  watchdog-quiet production at B=2700.

## ADR-011 — Append-only event log

- **Decision:** ADOPT. **Status:** RATIFIED (implemented #24; hash chain + hourly customer-sealed
  HMAC checkpoints verified).
- **Type:** 1 — durable-truth format.
- **Chief positions:** unanimous ADOPT. Forge (Five Whys — reconciliation was the event log with
  extra steps; condition: consumer migration checklist closed). Vault (tamper-evidence is a safety
  property under DR-6; honest residual: engine holds the signing key — Ed25519 is the documented
  future path; key-rotation ceremony unwritten). Pager (the 3 AM test is passed by the explorer, not
  the format — ADR-006's timeline-first audit explorer ships as the log's other half). Tripwire
  (chain proves ordering and non-deletion, explicitly not payload integrity — stated, not hidden;
  crash-window tests exist).
- **Rationale:** The dual-write lie (says paged, never paged) is buried. The honest boundary —
  ordering/non-deletion proven, payload integrity out of scope — is the brand.
- **Conditions:** consumer migration checklist; timeline explorer (ADR-006) as the log's other half.
- **What would change it:** a demonstrated crash window where a committed event breaks chain
  continuity and `verify()` stays silent.

## ADR-012 — Durable forwarder + outbox

- **Decision:** ADOPT. **Status:** RATIFIED (implemented #23; outbox relay, stable `dedup_key`,
  degraded `send_direct` standby, spill replay, `require_drilled_secondary` verified).
- **Type:** 1 — the delivery contract (at-least-once) is a public promise.
- **Chief positions:** unanimous ADOPT. Forge (`dedup_key` stability needs a property test in CI;
  secondary-channel config must be documented before the design partner). Vault (at-least-once +
  stable dedup is the only honest contract; residual: crash between direct-send and spill-write is
  the named gap). Pager ADOPT-WITH-CONDITIONS (the secondary channel must be a **cutover gate** —
  no secondary, no cutover out of shadow; provisioned inside the 15-minute onboarding; a different
  fate domain — SMS/voice/second vendor — not a second API key on the same vendor). Tripwire
  (dedup stability proven by test; the chain rests on ADR-011's durability).
- **Rationale:** "Alert the operator" has no channel when the alerter is broken — the one failure
  mode that degrades to silence gets the outbox + secondary channel. Fail-open theater is over.
- **Conditions:** dedup_key property tests; secondary as cutover gate with fate-domain separation;
  runbook names the direct-send/spill-write residual.
- **What would change it:** production duplicate pages traceable to dedup_key instability; or evidence
  the secondary channel's failure modes outweigh its saves (then opt-in, standby-PD default).

## ADR-013 — Quantized probability lock

- **Decision:** ADOPT. **Status:** RATIFIED (implemented #15; Tripwire hand-verified the math in
  code — `MIN_N_K0 = 1351` exact).
- **Type:** 1 — paging-path safety policy; the bar defines what silence is allowed.
- **Chief positions:** unanimous ADOPT. Forge (the fix went to the unit, not the constant; time-box
  the dual-attestation interim). Vault (the bar can never be silently approximated — without a fit,
  suppression gets harder, not easier; the failure mode is conservative, which is the correct
  asymmetry). Pager (dual attestation for a 3-person team is the whole company signing — that's the
  point; mint attestations as a side-effect of shadow sign-off inside the 15-minute onboarding).
  Tripwire (code matches the verified spec; worth a fuzzer on `parse_hundredths` hostile inputs).
- **Rationale:** The M-1 units error was real; the fix is honest — suppress less until the fit
  exists. "0.002" survives as the expected-cost optimum while evaluation lives in the space the
  vendor actually reports.
- **Conditions:** calibration fit becomes mandatory by the clock (interim must not rot into a quiet
  failure mode); fuzzer on hostile probability inputs.
- **What would change it:** a vendor response format reporting thousandths — the construction gets
  re-derived, not patched; or a calibration study showing systematic over-blocking past buyer
  tolerance (then better fits, not a looser bar).

## ADR-014 — Freshness proofs; stale ⇒ page

- **Decision:** ADOPT. **Status:** PARTIAL — module, proofs, and the (fresh|stale)³ rot-matrix fixture
  verified; **ratification withheld**: Tripwire found `gate.py` states "attestation freshness is NOT
  enforced yet" — the live gate never calls `suppress_precondition`; the rot matrix tests
  `decide_like_gate`, a test-side model of the conjunction, not the kernel.
- **Type:** 1 — core safety invariant; compound rot is the documented killer.
- **Chief positions:** Forge ADOPT/ratify (attestation cache TTL ≤ strictest proof TTL; the fixture
  never gets deleted in a "cleanup"). Vault ADOPT/ratify (forgery mitigations layered; residual: the
  attestor roster is an unnamed trusted party — recommend a follow-up ADR). Pager ADOPT (stale ⇒
  page is the right failure mode; condition: 7-day and 1-day pre-expiry nudges before the spike
  bites). Tripwire ADOPT-decision, REJECT-ratification (a fixture testing a model of the gate is
  not a test of the gate).
- **Rationale:** Locks that rot together are one lock; only a proof beats silent aging. But an
  unenforced invariant is a comment with a test suite — the wiring is the decision's missing half.
- **Conditions for ratification:** wire `suppress_precondition` into the live gate kernel; collapse
  `decide_like_gate` into the kernel per DR-26 (same code path, not a copy); pre-expiry nudges.
- **What would change it:** a demonstrated common-mode rot the matrix misses (matrix grows,
  invariant doesn't shrink); or proof-maintenance toil causing rubber-stamp attestations (then
  automate proof generation from pipeline runs, not longer TTLs).

## ADR-015 — Exact model-version pinning + 7-day re-validation

- **Decision:** ADOPT the design. **Status:** ADOPTED-DESIGN — NOT ratified. Verified: pin binds the
  fit (`quantized.py`) and the freshness proof (mismatch ⇒ stale ⇒ page — a good fail-closed path).
  **Not found:** no `response.model == pinned` assertion at the Jev call site, no named drift-page
  event, no shipped `pinning.json`, no 7-day re-validation protocol.
- **Type:** 1 — the provider contract; a silent vendor remap under fixed thresholds is a
  supply-chain attack on the safety case.
- **Chief positions:** unanimous ADOPT-WITH-CONDITIONS, all four explicitly refusing ratification.
  Forge (a recorded invariant the code doesn't enforce is worse than an open gap — the team stops
  looking). Vault (today drift surfaces only as lock-1 staleness, indistinguishable in the log from
  an expired fit — a postmortem can't tell "vendor moved" from "we forgot to re-fit"). Pager (no team
  runs a 7-day human protocol per vendor release — automate it as a job; the human signs the
  result). Tripwire (the hot-path assertion + a test proving mismatch ⇒ passthrough + drift page;
  the ceremony needs a named owner).
- **Rationale:** Eternal friction in its purest form — downstream APIs change without warning, and a
  vendor "improved calibration" release changes the probability mapping under fixed thresholds.
- **Conditions for ratification:** (a) hot-path `response.model == pinned` assertion producing a
  named `model_drift` event (passthrough + drift page); (b) client stops floating `jev-latest` by
  default; (c) the 7-day re-validation protocol written as a Type 1 procedure with a named owner
  (replay machinery exists in `backtest.py` — wire it).
- **What would change it:** a vendor contract with machine-readable model-version immutability
  guarantees — then the assertion is belt-and-braces, but the belt stays.

## ADR-016 — Storm aggregates can never suppress

- **Decision:** ADOPT. **Status:** PARTIAL — the deterministic separate path for storm-folded
  members verified (`gate.py` `_structural`, pre-Jev, reason `"storm"`); **ratification withheld**
  on the aggregate itself: Tripwire and Pager verified the storm-*declaring* aggregate falls through
  the race to the full triple lock and **can suppress with reason="allowlist"** — the delta's
  "separate code path, not a flag" is not what ships for the aggregate.
- **Type:** 1 — paging-path architecture; the highest-blast-radius single decision in the system.
- **Chief positions:** Forge ADOPT-WITH-CONDITIONS (the code contradicts the delta's letter —
  `Disposition(action="suppress", reason="storm")` reads exactly like model-driven suppression to the
  3 AM operator; rename to `folded`). Vault ADOPT with a naming note (confidence on aggregates is
  uncalibrated by construction). Pager ADOPT-WITH-CONDITIONS, refusing ratification (a flag-guard in
  `receiver.py` is exactly what a future edit inverts; the commander wants a deterministic digest —
  what's broken, counts by service, queued raw alerts). Tripwire REJECT-ratification (the aggregate
  is one confident Jev answer away from the suppress branch).
- **Rationale:** The aggregate concentrates the most alerts behind the least evidence; it gets the
  strongest structural guarantee, not the cleverest flag.
- **Conditions for ratification:** (a) storm aggregates route to a structurally separate digest path
  (suppress unreachable by construction); the aggregate's Jev call may run for root-cause candidates
  but its disposition never gates suppression; (b) regression test: storm-declared aggregate with all
  locks green still pages; (c) rename the folded disposition (`folded`, not `suppress`).
- **What would change it:** a calibrated storm-aggregate confidence study on real incident data with
  a defensible false-silence bound — that evidence doesn't exist today.

## ADR-017 — Fingerprint env+cluster; attestation tuples; security-category ban

- **Decision:** ADOPT the design. **Status:** PARTIAL — attestation tuples verified
  (`dual_attestation_valid`: distinct attestors, distinct-from-author, run-id evidence refs,
  TTL ≤ 30d; TTL expiry fails safe → page); env-namespaced allowlist verified; **fingerprint change
  NOT done** (`fingerprint_for` still `sha256(service|check|severity_in|region)`); security ban is a
  self-asserted boolean, not derived.
- **Type:** 1 — core schema; fingerprints are the dedup identity of the whole system.
- **Chief positions:** unanimous ADOPT-WITH-CONDITIONS. Forge (approve the design now so the lane can
  build; the delta's marked-key deploy-rollup solution honors Chesterton's fence). Vault (the hash-
  level collision still exists — any future consumer matching the raw fingerprint reopens the hole
  silently; the ban must be *derived at admission* from check-name/team patterns, not self-asserted).
  Pager (tuples operable only if minted as side-effects of existing workflows — shadow sign-off,
  deploy pipeline — never a separate ceremony; ship the migration with the schema change or every
  existing entry silently breaks). Tripwire (either change `fingerprint_for` with migration, or
  revise the ADR on the record defeating the staging→prod collision pre-mortem).
- **Rationale:** The staging→prod collision is a concrete missed-SEV1 pre-mortem; "verified" without
  a tuple is theater; env-awareness downstream (`derive_dedup_key` already takes env) proves the
  direction but doesn't fix the hash.
- **Conditions for ratification:** (a) `fingerprint_for` includes env+cluster with the dual-write +
  re-attestation migration; (b) security-category ban derived at admission from taxonomy patterns.
- **What would change it:** a migration analysis showing the fingerprint change breaks incident-
  grouping UX buyers depend on (then namespace harder, but the raw hash still changes); or
  production evidence of a collision that didn't matter.

## ADR-018 — Standby direct-to-PD + schema-validated config

- **Decision:** ADOPT. **Status:** RATIFIED with conditions (config half: 4-stage load, checksummed
  last-good chain, 0.85 conf floor in schema; standby half: `send_direct` with routing-key pinning,
  spill replay, `DrillTracker` + `require_drilled_secondary` enforced at startup — all verified).
- **Type:** 1 — deployment architecture; fail-open assumed liveness, and this is the correction.
- **Chief positions:** unanimous ADOPT. Forge ("last-good" and "standby" are the two cheapest words
  in incident response; condition: monthly drill gets a named owner and a first drill date before
  the design partner). Vault (fallback-path auth sound — same secret-mapping, distinct control
  routing key; drill completions should be event-logged). Pager ADOPT-WITH-CONDITIONS (monthly
  drills survive only as gated drills, never calendar drills; keep the drill a <5-minute one-tap
  exercise; **name who runs the external health watcher** — "Sentinel down is a SEV1 on a path that
  doesn't traverse Sentinel" is a sentence, not a system, until the watcher has an owner; the
  cutover runbook needs writing). Tripwire ADOPT-WITH-CONDITIONS ("drilled" is verifiable iff drill
  records with human acks exist within 30 days; cutover must refuse a stale-drilled secondary).
- **Rationale:** The one failure mode that degrades to silence — a dead receiver under hard cutover
  is connection-refused for every alert — gets a standby and a drill. A standby with an undrilled
  runbook doesn't exist (infrastructure constitution).
- **Conditions:** first recorded drill with human ack before the design partner; named watcher owner;
  cutover runbook written; drill completions event-logged.
- **What would change it:** a chaos exercise showing the standby shares an unseen fate (same
  process, egress, credential store) — then it's complement, not substitute.

## ADR-019 — Asymmetric trust: suppression requires corroboration

- **Decision:** ADOPT the design. **Status:** ADOPTED-DESIGN — NOT ratified. All four chiefs verified:
  "corroboration"/"silence takes two" appear only in comments (`freshness.py`, `race.py`) and a
  test-fixture premise; **no corroboration leg exists in the gate**. Vault: blessing this as done
  would be the single most dishonest ratification in the wave.
- **Type:** 1 — the trust model; "silence takes two" is a brand-level promise.
- **Chief positions:** unanimous ADOPT-design, refuse-ratification. Forge (honest interim framing:
  dual human attestation *is* a form of corroboration — say that, don't pretend the code does
  more). Vault (shape (b) interacts with ADR-005: the receiver accepts unsigned traffic fail-open,
  so unsigned "resolves" must never count as corroboration; corroboration evidence itself needs
  freshness). Pager (yes, it reduces the suppression rate — say so plainly; the product is "suppress
  pages you can defend in a postmortem," and marketing must not promise rates the trust model can't
  deliver). Tripwire (the ratification bar: a permanent CI fixture proving the model alone,
  uncorroborated, cannot produce a suppress disposition on a critical alert).
- **Rationale:** A false page costs trust; a false silence costs the company. Thresholds don't
  distinguish *kinds* of evidence — corroboration does. A comment reading "AND corroboration" while
  the gate evaluates no such leg is tuning theater applied to the trust model.
- **Conditions for ratification:** (a) explicit corroboration leg in the live suppress conjunction,
  evaluated from evidence; (b) the silence floor versioned, audited, two-person-changed;
  (c) resolve-as-corroboration requires strict signature verification (the one path where fail-open
  ingest is unacceptable); (d) corroboration evidence carries freshness.
- **What would change it:** shadow-pilot data showing lone-model suppressions with a false-suppress
  rate at or below the corroborated rate — i.e., the second witness adds no information.

## ADR-020 — Instruction firewall + adversarial CI corpus

- **Decision:** ADOPT the design. **Status:** ADOPTED-DESIGN — NOT ratified. Verified: no firewall
  stage in `src/`, no adversarial corpus in CI; only comment premises ("NOT firewall-flagged") —
  assumed armor.
- **Type:** 1 — safety invariant.
- **Chief positions:** unanimous ADOPT-design, refuse-ratification. Forge (deterministic screen only —
  no model-based screening on the hot path; if the corpus won't be maintained, delete the comment
  premises and carry the risk openly). Vault (alert text is hostile input by architecture; the
  firewall must be deterministic — a model judging whether the model's input attacks the model is
  circular; publish the ASR metric in the shadow report). Pager ADOPT-WITH-CONDITIONS (flagged ⇒
  page must route through dedup/storm-collapse so an injection storm pages *once per fingerprint* —
  an attacker-induced page flood is the attacker succeeding; metric + alarm on the flag rate).
  Tripwire (a corpus written by the team that wrote the firewall is a mirror, not an adversary —
  name the corpus owner (Vault) and a red-team for the corpus itself (Tripwire, quarterly)).
- **Rationale:** Indirect prompt injection via alert fields (poisoned User-Agent → fake resolution →
  silence) needs no stolen secret, and the industry is discovering it in production right now. The
  pre-mortem writes itself: the GhostJacking incident is discovered in production, not in CI.
- **Conditions for ratification:** (a) deterministic screen as a pipeline stage before the Jev call,
  with its own tests; (b) versioned adversarial corpus in CI with attack-success-rate as the gate
  metric; (c) flagged ⇒ fail-closed-to-page, flag as an event-logged field; (d) named corpus owner
  and quarterly red-team.
- **What would change it:** measured evidence that the Jev API's own input handling neutralizes
  indirect injection on alert-shaped payloads (screen stays as defense in depth); or a documented
  industry incident where this deterministic shape failed open (then the design re-opens).

## ADR-021 — History provenance + 72h staleness

- **Decision:** ADOPT the design. **Status:** PARTIAL — fit training pedigree (`history_as_of` on
  `trained_on`) verified; **correlator runtime history provenance, the 72h bound, and the novelty
  rule all absent** (no `history_as_of` on history inputs, no `novel_fingerprint` in `src/`).
- **Type:** 1 — data-freshness policy (the bound's *existence* is Type 1; the 72h *value* is
  Type 2, tunable per org).
- **Chief positions:** unanimous ADOPT-WITH-CONDITIONS, refusing ratification. Forge (adopt the
  staleness bound now; sequence the novelty rule behind shadow evidence — don't ship a page cannon
  on a hunch; manual first, measured second). Vault (a frozen outcomes pipeline silently defeats the
  confidence lock; label-pipeline staleness must be a paged SLO before the first production
  cutover). Pager (a 72h history outage does *not* page everything — the novelty rule fires only on
  <5-occurrences/30d fingerprints, so the outage pages the unusual, which is exactly right; the SLO
  must page the platform owner, not arrive as noise to the on-call human). Tripwire (the bound must
  be computed at ingest from the receiver's single clock — a skewed producer clock defeats the bound
  silently).
- **Rationale:** Data pedigree first: a history input with no provenance is an unpedigreed input to
  a safety lock. Stale history doesn't error — it quietly makes the confidence lock confident about
  a world that no longer exists.
- **Conditions for ratification:** (a) 72h bound on correlator history inputs, treat-as-empty ⇒
  novelty ⇒ page with reason `history_stale`; (b) single-clock rule written into the code;
  (c) label-pipeline staleness as a paged SLO; (d) novelty rule runs in shadow first, promotes with
  evidence.
- **What would change it:** an estate where >50% of alert volume is novel fingerprints (page-storm
  risk — rethink per org); or contractual upstream freshness SLOs making our bound a backstop.

## ADR-022 — Threshold-change governance + suppression SLO

- **Decision:** ADOPT. **Status:** PARTIAL — two-person `ThresholdAttestation`, 30-day re-validation
  clock, and the 0.85 conf floor verified in schema; **canary, suppression-rate SLO watchdog, and
  automatic revert all absent** (`false_suppress_rate` measures; nothing watches).
- **Type:** 1 — safety-policy governance; the fatigue ratchet is a human-factors failure mode, and
  "the human decides" was already rejected as its mitigation.
- **Chief positions:** unanimous ADOPT-WITH-CONDITIONS. Forge (the watchdog is a new control-plane
  component — run the signed weekly shadow check manually first; automate when skipped twice or
  toil is measured). Vault (static governance is necessary but the ratchet moves *within* the rules
  — only live machinery catches it; clock expiry must revert to last-validated + alarm).
  Pager (canary = **shadow-diff**, not live 5% splitting — run the proposed threshold in shadow 7
  days, diff dispositions, sign the diff; cheaper, safer, same evidence). Tripwire (the regress
  terminates at a paged human: the watchdog emits a heartbeat, and a *missing* heartbeat pages via
  a path that doesn't traverse the watchdog).
- **Rationale:** Each individual threshold change looks reasonable; only the trend is the signal.
  Audit-visible-but-unstopped is not a mitigation.
- **Conditions for ratification:** (a) canary-as-shadow-diff gates threshold changes; (b)
  suppression-rate SLO watchdog with dead-man's-switch heartbeat; (c) 30-day clock expiry reverts
  to last-validated + alarms — all before the first design partner can change thresholds in
  production (the manual weekly signed check is the interim).
- **What would change it:** a year of shadow data showing threshold changes are rare, always upward,
  always attested (then the canary simplifies); or evidence the two-person ack gets rubber-stamped
  (then the control needs rotation/expiry).

## ADR-023 — Counterfactual receipt on every suppression row

- **Decision:** ADOPT. **Status:** PARTIAL — fully implemented in the *shadow* pipeline (policy
  mirror + drift guard, genuinely good); **the live path emits `None`** (`race_payloads.py` leaves
  it null, delegating to "the event-log lane," which never fills it).
- **Type:** 1 — contract addition to the event schema.
- **Chief positions:** unanimous ADOPT-WITH-CONDITIONS. Forge (wire it into the real suppression
  emit; presets from the thresholds config — shadow's hardcoded (0.85, 0.90, 0.95, 0.99) drifting
  from configured presets is a quiet lie in the making). Vault (a null field is not evidence — it
  *looks* implemented; audit the receipt against the simulator, same code path not a copy, per
  DR-26). Pager (not noise: "at your 0.85 this stood down; at 0.70 it would have paged" is how a
  human calibrates trust one contrast at a time — one line in the UI, not a matrix). Tripwire (the
  counterfactual is currently a *mirror* of the gate — exactly the drift DR-26 forbids; pin the
  policy version on the receipt or it's not reproducible under policy change).
- **Rationale:** Empathy for the operator: a bare 0.87 does not survive impaired cognition; contrast
  against the operator's own experience does. Zero hot-path cost, durable in the event.
- **Conditions for ratification:** (a) populate at event-write time on the live suppress path;
  (b) pin the policy version on the receipt; (c) collapse the mirror into the gate kernel per
  DR-26; (d) presets from config.
- **What would change it:** operator-study evidence the counterfactual line is never read (then it
  stays as cheap schema weight, but we stop claiming the trust benefit); or a platform-tier proof
  of byte-identical derivation from immutable event fields (then derivation-at-read is acceptable).

## ADR-024 / O-1 — Audit data retention policy (Vault-led RFC)

- **Decision:** RFC REQUIRED BEFORE THE FIRST DESIGN PARTNER. **Status:** OPEN — deliberately not
  decided by vote; decided by evidence.
- **Type:** 1 in both directions — deleting is irreversible; keeping forever is unbounded liability
  (PM-3: disk-full at 03:12, a week of silent audit gap).
- **Chief positions:** unanimous. Forge (agree Vault leads; add a Type-2 interim disk guard *before*
  the design partner — WAL-size watermark ⇒ page + spillover, using the existing spill machinery —
  because an RFC that takes six weeks while the WAL fills is a decision made by neglect). Vault
  (non-negotiable floors: **≥180 days full-fidelity for suppress dispositions** — covers the 30-day
  replay, 90-day attestations, 180-day TTLs — ≥60 days for all decisions; deletion is a logged
  event, never a silent prune; proposed shape: tiered hot → hash-chained summaries + customer-sealed
  checkpoints to WORM archive, per-customer tiers as the enterprise surface; answer EU
  retention+residency (O-3) jointly). Pager (SOC 2 Type II auditors expect ≥1 year of audit logs;
  regulated buyers 3–7; the GDPR-vs-immutability collision needs pseudonymization-on-delete, not
  row deletion; disk-full protection as a first-class requirement). Tripwire (the evidence that
  settles it: measured log growth per decision → a disk-full date (arithmetic, computable now); one
  enterprise retention clause; a priced WORM option; standing bias: TTL ≥ the postmortem window,
  never hard delete — the company's product IS the audit trail).
- **Rationale:** Retention is the rare decision that is Type 1 whichever way you cut it, and
  "decide later" already has a priced pre-mortem. The RFC is the right process and Vault is the
  right lead — but the disk fills on its own schedule, not the RFC's.
- **Conditions:** (a) Vault-led RFC lands before the first design partner; (b) Type-2 interim disk
  guard lands before the design partner regardless; (c) the RFC answers tiers, GDPR collision,
  disk-full bound, and EU retention+residency jointly.
- **What would change it:** the growth arithmetic + one enterprise retention clause + a priced WORM
  option — then it's a decision, not a debate; or a signed contract with shorter retention and
  explicit customer liability acceptance.

---

## Implementation debt register (lanes implied by tonight's verdicts)

These are not decided tonight — they are the build consequences. The Sunday wave coordinator owns
scheduling; Petu owns priority.

| # | Debt | Source |
|---|---|---|
| D1 | Wire `suppress_precondition` into the live gate kernel; collapse `decide_like_gate` (DR-26) | ADR-014 |
| D2 | Hot-path `response.model == pinned` assertion + named `model_drift` event; stop floating `jev-latest`; 7-day re-validation as an automated job with owner | ADR-015 |
| D3 | Storm aggregate: structural digest path (suppress unreachable); regression test (all-locks-green aggregate still pages); rename folded disposition | ADR-016 |
| D4 | `fingerprint_for` env+cluster with dual-write migration; security ban derived at admission | ADR-017 |
| D5 | Corroboration leg in the live suppress conjunction; versioned/audited/two-person silence floor; strict-signature resolve path; freshness on corroboration evidence | ADR-019 |
| D6 | Deterministic instruction-firewall stage; versioned adversarial corpus in CI with ASR gate; Vault owns corpus, Tripwire red-teams quarterly | ADR-020 |
| D7 | 72h history bound + `history_as_of` on correlator inputs (single-clock rule); label-pipeline staleness SLO; novelty rule in shadow first | ADR-021 |
| D8 | Threshold canary-as-shadow-diff; suppression-SLO watchdog with dead-man's-switch heartbeat; 30-day clock auto-revert | ADR-022 |
| D9 | Counterfactual receipt on the live suppress path; policy version pinned; presets from config; mirror collapsed into kernel | ADR-023 |
| D10 | ADR-001 correlator craft (flap-reopen, fresh episodes, never-auto-close, P1/P2 carve-out) + DR-13 reconciliation | ADR-001 |
| D11 | ADR-005 code fixes: empty-secret-refuses (prod), 5-min timestamp tolerance, replay analysis; strike §7 allowlist mention | ADR-005 |
| D12 | ADR-007 platform mute label + governance (event-logged transitions, no auto-mute, weekly review ritual, quarantine invariant in CI) | ADR-007 |
| D13 | ADR-018: first recorded drill with human ack; named external-watcher owner; cutover runbook | ADR-018 |
| D14 | Vault-led retention RFC (O-1/ADR-024) + interim Type-2 disk guard | ADR-024 |

## Cross-item notes

1. **ADR-005 × ADR-019 (unsigned ingest vs corroboration evidence):** the receiver's fail-open ingest
   is correct for *paging* (DR-2/DR-11) but must never mint *corroboration evidence*. When D5 is
   built, the signed-resolve path requires strict auth — carved out explicitly. Unsigned "resolves"
   never count.
2. **Attestor identity is now load-bearing** (ADR-013 dual attestation, ADR-014 proofs, ADR-022
   two-person rule). Attestor onboarding/offboarding/compromise response is an unnamed trusted party
   across all three — recommend a follow-up ADR.
3. **B is provisional** (ADR-010): the N≥1000 re-derivation and the timer-win watchdog as
   falsifier are part of the decision, not footnotes.
4. **No accuracy claims; no safety claims either** (extends DR-7): an adopted-but-unimplemented
   design must never be described as a property of the system — in docs, demos, or buyer
   conversations. The demo narrative shows what is *built*, not what is *decided*.

---

*Panel: Forge · Vault · Pager · Tripwire. Coordinator synthesis 2026-10-03 ~20:50 IST.
Decisions take effect on Petu's PR review (founder-deputy ratification). Until then: PROPOSED.*
