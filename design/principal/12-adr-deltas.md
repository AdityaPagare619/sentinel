# 12 — ADR Deltas: Proposed Changes vs the Frozen Spec

**Principal-redesign wave · 2026-10-03 · coordinator draft**
**Status: ALL PROPOSED. None applied. The frozen spec is NOT unfrozen by
this document.**

Each delta below is an Architecture Decision Record in proposal form: the
frozen-spec baseline, the change, the alternatives considered, and the
consequences. They are addressed to **Forge (review) and Aditya
(verdict)**. ADR-001/005/007 pre-date this wave and remain Aditya's
exclusively — they are restated in §2 for completeness, not re-argued.

Numbering continues the existing series (P-1–P-9 in `09-decision-register.md`
§2). "Frozen spec" = `ARCHITECTURE.md` + `PLATFORM_ARCHITECTURE.md` +
`CHARTER.md` as of 2026-10-02.

---

## §1 — New deltas from this wave (all PROPOSED)

### ADR-010 — Race-to-page: the Jev call leaves the blocking critical path

- **Baseline:** frozen spec has the gate call Jev inline, once per alert,
  on the paging path.
- **Change:** the Jev call races a paging budget B. Timer fires first ⇒
  immediate passthrough; the late answer becomes a shadow decision event.
  Suppression requires Jev fast AND confident AND corroborated.
- **Why:** the measured ~11.4s first-call latency (N=1) against the
  70–500ms spec means the inline call *is* paging-path latency. The
  do-no-harm law protected the path from the platform while leaving it
  exposed to the engine's own inference. (Forge 01, Move 1; precedent:
  hedged requests, The Tail at Scale.)
- **Alternatives:** (a) keep inline + timeout — rejected: a timeout that
  fires *is* the race, just without the shadow-event semantics; (b)
  async-everything — rejected: pages must not wait for inference at all.
- **Consequences:** paging latency bounded by our budget, never the
  vendor's tail; suppression rate drops under vendor slowness (honest
  degradation); the audit schema gains the late-answer event type.
- **Type:** 1 (paging-path latency semantics).

### ADR-011 — The audit log becomes an append-only event log

- **Baseline:** audit log records decision rows (`decision_requested` →
  row written → forward attempted).
- **Change:** events, not rows — `decision_requested` / `decision_made` /
  `forward_confirmed | forward_failed` / `flip_observed`. The river is a
  projection over events. (Forge 01, Move 2; precedent: Fowler event
  sourcing + transactional outbox.)
- **Why:** the dual-write problem — a crash between audit-write and
  forward lies (says paged, never paged) or gaps (paged, no row).
- **Alternatives:** (a) write-ahead-then-forward with reconciliation —
  rejected: reconciliation is the event log with extra steps; (b) keep
  rows + add a forward-receipt column — rejected: the receipt *is* the
  event; a nullable column reintroduces the ambiguity.
- **Consequences:** the durable-truth format changes; every downstream
  consumer (river, calibration, postmortems) reads events; migration note
  required for existing rows.
- **Type:** 1 (durable-truth format).

### ADR-012 — The forwarder becomes a durable last-mile

- **Baseline:** forwarder failure mode is "log + metric."
- **Change:** decided pages enter a local outbox; retry with stable
  `dedup_key` (PD's own idempotency primitive); undelivered past X ⇒
  customer-configured secondary channel. No single notification path.
  (Forge 01, Move 3.)
- **Why:** fail-open is vacuous when the thing that failed *is* the paging
  path. "Alert the operator" has no channel when the alerter is broken —
  the one failure mode that degrades to silence.
- **Alternatives:** (a) hot-standby forwarder process — rejected: same
  fate-sharing class without the outbox; keep as complement, not
  substitute; (b) page-on-forward-failure via the same path — rejected:
  circular.
- **Consequences:** at-least-once delivery contract; dedup_key stability
  becomes a correctness property; secondary-channel config is new
  customer surface.
- **Type:** 1 (delivery contract).

### ADR-013 — The probability lock is evaluated in quantized space

- **Baseline:** `P(p1) < 0.002` evaluated as a comparison on the reported
  probability.
- **Change:** suppress requires reported P(p1) = 0.00 AND (per-org
  calibration fit's p̂_upper < 0.002 OR, until the fit exists, dual human
  attestation on the allowlist entry). The bar is never silently
  approximated. (Tripwire T1-01; synthesis §3.1.)
- **Why:** 0.01 quantization makes 0.002 unimplementable as a raw
  comparison — a true 0.0049 reports as 0.00 and passes. Units error.
- **Alternatives:** (a) raise the bar to 0.01 — rejected: destroys the
  expected-cost optimum; (b) ignore rounding — rejected: the pre-mortem
  proves it.
- **Consequences:** suppress fires less often until an org has a
  calibration fit; honesty over suppression rate; the tuner gains the
  fit as a first-class output.
- **Type:** 1 (paging-path safety policy).

### ADR-014 — Freshness proofs on all three locks; stale ⇒ page

- **Baseline:** the triple lock's legs are evaluated on current values
  with no liveness.
- **Change:** each lock carries a proof of freshness (calibration-fit
  date, threshold attestation, allowlist attestation + drift check). Any
  stale proof ⇒ the lock fails ⇒ page. A (fresh|stale)³ rot-matrix CI
  fixture tests every combination. (Tripwire T1-14 / C-1; synthesis §3.5.)
- **Why:** locks that rot together are one lock — the compound scenario
  defeats all three through independent silent aging.
- **Alternatives:** (a) periodic manual review of locks — rejected:
  silent rot is silent; (b) single global freshness flag — rejected:
  coarse flags hide per-lock rot.
- **Consequences:** freshness checks live off the hot path (cached
  attestations); config-load and periodic revalidation own them.
- **Type:** 1 (core safety invariant).

### ADR-015 — Exact model-version pinning + 7-day re-validation

- **Baseline:** client sends `model: "jev-latest"`.
- **Change:** pin the exact version in config; assert
  `response.model == pinned_version`; mismatch ⇒ passthrough + drift
  page. New versions require the 7-day re-validation protocol (30-day
  decision replay, drift report, re-fit) before the pin moves.
  (Tripwire T1-02 / M-2.)
- **Why:** a vendor "improved calibration" release changes the probability
  mapping under fixed thresholds — silent, within-spec, and invisible
  unless the version is an input to the safety case.
- **Alternatives:** float `jev-latest` with monitoring — rejected: silent
  mapping shifts don't trigger monitors tuned for outages.
- **Consequences:** version adoption becomes a deliberate, evidenced
  act; the pin is versioned alongside thresholds.json.
- **Type:** 1 (provider contract).

### ADR-016 — Storm aggregates can never suppress

- **Baseline:** the storm path's suppress-vs-page authority is ambiguous
  (§3.5 vs §3.6).
- **Change:** storm aggregates cannot suppress — separate code path, not
  a flag. Degraded mode falls back to a deterministic storm digest, never
  raw passthrough. (Tripwire T1-04, T1-10 / M-4, I-3.)
- **Why:** the aggregate path concentrates the most alerts behind the
  least evidence; a suppress-capable aggregate is the highest-blast-radius
  single decision in the system.
- **Alternatives:** suppress-on-aggregate with high confidence — rejected:
  confidence on aggregates is uncalibrated by construction.
- **Consequences:** storm handling degrades to deterministic triage
  (counts by service, digest) rather than probabilistic suppression.
- **Type:** 1 (paging-path architecture).

### ADR-017 — Fingerprint includes env+cluster; allowlist attestation tuples

- **Baseline:** fingerprint definition excludes env/cluster; allowlist
  entries are "customer-verified" (unstandardized).
- **Change:** fingerprint includes env+cluster; allowlist namespaced by
  env; entries carry attestation tuples (who/when/on-what-evidence/TTL);
  security-category fingerprints banned in code. (Tripwire T1-05, T1-13 /
  D-1, A-1; Vault Choice 1.)
- **Why:** staging→prod fingerprint collision is a concrete missed-SEV1
  pre-mortem; "verified" without a tuple is theater (meta-lesson 6).
- **Alternatives:** global allowlist with env as a label — rejected:
  labels are advisory; namespaces are structural.
- **Consequences:** allowlist admission becomes a defined standard (30d /
  K-occurrences / zero-incident-linkage / attested / TTL); migration of
  existing entries required.
- **Type:** 1 (core schema).

### ADR-018 — Standby direct-to-PD fallback + schema-validated config

- **Baseline:** fail-open covers triage failures; config is parsed at
  startup without schema validation.
- **Change:** config schema validation (invalid ⇒ last-good + page, never
  crash-loop); a standby direct-to-PD integration with an external health
  watcher and a monthly-drilled fallback runbook. (Tripwire T1-09 / I-2;
  synthesis §3.6.)
- **Why:** fail-open assumes liveness. A dead receiver under hard cutover
  is connection-refused for every alert — the SEV1 never enters the
  system, and no triage-time promise covers it.
- **Alternatives:** (a) process supervisor restarts — rejected: restarts
  don't help a poisoned config; (b) active-active receivers — rejected:
  cost and split-brain outweigh the benefit at this stage; keep as
  future option.
- **Consequences:** the deployment architecture gains a liveness story;
  the fallback drill is a recurring operational cost, reported to the
  buyer (the Knight Capital rule).
- **Type:** 1 (deployment architecture).

### ADR-019 — Asymmetric trust: suppression requires corroboration

- **Baseline:** page and suppress are symmetric outputs of one gate.
- **Change:** suppression above the silence floor requires corroboration —
  a lone model judgment can never suppress a critical alert. Corroboration
  shapes: (a) allowlist attestation tuple (ADR-017), (b) matching signed
  `resolve` from the same source for the same alert_key. The silence floor
  is versioned, audited, two-person-changed. (Vault Choice 1; synthesis
  §3.2.)
- **Why:** the failure costs aren't symmetric — a false page costs trust,
  a false silence costs the company. "Silence takes two."
- **Alternatives:** symmetric gate with higher thresholds — rejected:
  thresholds don't distinguish *kinds* of evidence.
- **Consequences:** suppression rate is lower than a symmetric gate's;
  every suppression is defensible in a postmortem.
- **Type:** 1 (trust model).

### ADR-020 — Instruction firewall with adversarial CI corpus

- **Baseline:** alert text flows to the model as context.
- **Change:** alert text is hostile input by architecture. A deterministic
  screen strips/flags instruction-shaped content in untrusted fields;
  flagged payloads fail closed to page. Effectiveness is measured: a
  GhostJacking-shaped adversarial corpus runs in CI with
  attack-success-rate as the gate metric. (Vault Choice 2.)
- **Why:** indirect prompt injection via alert fields (poisoned
  User-Agent → fake resolution → silence) needs no stolen secret. The
  industry is discovering this in production; we build the firewall
  before the incident.
- **Alternatives:** (a) prompt-hardening only — rejected: unmeasured;
  (b) drop flagged alerts — rejected: dropping is silence; page instead.
- **Consequences:** the firewall is a pipeline stage with its own tests;
  the adversarial corpus is a living artifact.
- **Type:** 1 (safety invariant).

### ADR-021 — History provenance + staleness policy

- **Baseline:** the correlator consumes history with no provenance.
- **Change:** `history_as_of` provenance on all history inputs; history
  older than 72h is treated as empty (⇒ novelty rule ⇒ page); the
  label pipeline gets a staleness SLO. (Tripwire T1-06 / D-2.)
- **Why:** a frozen outcomes pipeline silently defeats the confidence
  lock — the gate trusts history that stopped updating.
- **Alternatives:** (a) best-effort freshness logging — rejected:
  unenforced; (b) shorter staleness bound — Type 2, tunable per org.
- **Consequences:** the 72h bound is a default; per-org tuning is Type 2.
- **Type:** 1 (data-retention policy).

### ADR-022 — Threshold-change governance + suppression SLO

- **Baseline:** thresholds are config; changes are ungoverned.
- **Change:** threshold changes require cost floors, two-person signed
  ack, and a 5%/7-day canary. A suppression-rate SLO with a watchdog;
  a 30-day re-validation clock with automatic revert; conf floor 0.85.
  (Tripwire T1-11, T1-12 / H-1, H-2.)
- **Why:** the fatigue ratchet — a human lowering the bar alert by alert
  until the gate suppresses everything — is a system failure mode, and
  "the human decides" is not a mitigation for it.
- **Alternatives:** (a) freeze thresholds post-onboarding — rejected:
  orgs legitimately evolve; (b) single-operator changes with audit —
  rejected: the ratchet is gradual and audit-visible but unstopped.
- **Consequences:** threshold changes become slow, evidenced acts; the
  watchdog is a new control-plane component.
- **Type:** 1 (safety-policy governance).

### ADR-023 — Counterfactual receipt on every suppression row

- **Baseline:** the decision event carries the disposition and confidence.
- **Change:** the event gains `threshold_counterfactual` — the disposition
  the gate would have taken at each configured threshold preset,
  evaluated deterministically at event-write time. (Prism §5; synthesis
  §3.4.)
- **Why:** the 3 AM operator trusts contrast against their own experience
  ("at 0.70 this paged; at your 0.85 it stood down"); a bare 0.87 does
  not survive impaired cognition.
- **Alternatives:** (a) compute on read in the platform tier — rejected:
  the receipt must be durable in the event, not derived later under a
  possibly-changed policy; (b) omit — rejected: the operator study marks
  a suppression row without a counterfactual as a rendering defect.
- **Consequences:** one field added to the decision event schema (folds
  into ADR-011's event-log RFC); zero hot-path cost.
- **Type:** 1 (contract addition to the event schema).

### ADR-024 — Audit data retention policy — OPEN

- **Baseline:** append-only forever; no retention policy.
- **Change:** unknown — needs an RFC. Type 1 in both directions.
- **Why:** flagged independently by Forge and Relay (O-1). Deleting is
  irreversible; keeping forever is a cost and liability.
- **Status:** OPEN. Required before the first design partner.
- **Type:** 1 (data-retention policy).

---

## §2 — Pre-existing ADRs (restated, not re-argued)

From `research/synthesis-2026-10-02.md`, via `09-decision-register.md` §2.
All PROPOSED. **ADR-001, ADR-005, and ADR-007 are Aditya's decisions** —
this wave takes no position on their verdicts, only records the evidence.

- **ADR-001 — Correlator craft alignment** (flap-debounce reopens the same
  row; resolved-then-refired is a fresh episode; never auto-close; P1/P2
  never silenced in maintenance windows). **Aditya decides.**
- **ADR-002 — Rate-limit design to the tighter bound** (design to 40
  req/s until Oracle's campaign settles the 6.25× dispute).
- **ADR-003 — Gateway redundancy** (four Jev gateways; Laya encoder exit).
- **ADR-004 — Open-weights encoder precedent** (evaluation harness
  precedent, not a product dependency).
- **ADR-005 — Webhook verification posture** (precedent judges IP
  allowlisting brittle without benefit — in tension with ARCHITECTURE.md
  §7's "optional IP allowlist config"; reconciliation belongs in the
  verdict). **Aditya decides.**
- **ADR-006 — Shadow-pilot evidence standards** (what the shadow must
  prove before cutover — now elaborated by Pager's 03-deployment-path).
- **ADR-007 — Muted-not-dropped** (suppressions are quarantined, visible,
  appealable — the Gmail precedent). **Aditya decides.**
- **ADR-008 — Vendor wedge** ("no affordable AIOps tier" positioning).
- **ADR-009 — Pricing/packaging direction.**

---

## §3 — What this wave does NOT change

For the record, so the deltas above aren't misread as a rewrite:

- The two-tier architecture, the control-plane/data-plane split, and the
  file-as-bridge mechanism stand.
- Shadow-first deployment stands (and is now the product strategy).
- Fail-open always stands (extended to liveness by ADR-018).
- The triple lock's three-legged shape stands (re-derived, not replaced).
- No accuracy claims; calibration evidence only — stands, deepened.
- The Sunday 2026-10-04 21:00 IST design-partner platform scope is
  unchanged by this wave. These deltas are post-Sunday architecture;
  none of them move the demo.

---

*End of ADR deltas. Application of any delta waits for Forge's review and
Aditya's verdicts. The frozen spec remains frozen.*
