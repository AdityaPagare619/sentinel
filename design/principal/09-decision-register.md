# Decision Register — Principal Redesign

**Law:** Law 3 (Type 1 vs Type 2). **Location mandated:** `design/principal/09-decision-register.md`.
**Status:** seeded 2026-10-03 by TRIPWIRE from `08-pre-mortem.md`. Other chiefs
append their Type 1 decisions under their own sections; the coordinator merges.

**Convention:** each entry names the alternatives considered, the tradeoff, and
any dissent (Law 3: "RFC-grade rigor"). Type 2 decisions are *not* tracked here —
they ship fast and iterate.

---

## TRIPWIRE-seeded (from the 2026-10-03 pre-mortem)

### T1-01 — Suppress bar in quantized probability space (M-1)
- **Decision:** the `P(p1) < 0.002` lock is evaluated against interval upper
  bounds (`reported + 0.005`) and a per-org calibration fit's p̂_upper, never
  against raw rounded reports.
- **Alternatives:** (a) raise the bar to 0.01 — rejected: destroys the
  expected-cost optimum, suppresses the product's value; (b) ignore rounding —
  rejected: the pre-mortem proves it's a units error.
- **Tradeoff:** suppress fires less often until an org has a calibration fit;
  honesty over suppression rate.
- **Dissent:** none recorded.

### T1-02 — Exact model-version pinning (M-2)
- **Decision:** `model` is a pinned exact version, never `jev-latest`; mismatch
  ⇒ passthrough + version-drift page; new versions require the 7-day
  re-validation protocol (30-day decision replay, drift report) before the pin moves.
- **Alternatives:** floating `jev-latest` with monitoring — rejected: silent
  calibration shifts move fitted thresholds without any alarm.
- **Tradeoff:** operational toil per vendor release; buys threshold validity.

### T1-03 — Novelty rule: novel fingerprints always page (M-3)
- **Decision:** fingerprint with <5 occurrences in 30d is novel: forced
  `page_now` (reason `novel_fingerprint`), never queue/suppress. Deterministic,
  computed from the audit log, overriding the model.
- **Alternatives:** (a) low-confidence ⇒ page (status quo) — rejected: the
  model is *confident* about novel noise; (b) novelty ⇒ passthrough —
  rejected: passthrough pages too, but without the first-seen banner the human
  can't distinguish novel from routine.
- **Tradeoff:** more pages during onboarding/new-service rollout; buys the
  cold-start SEV1.

### T1-04 — Storm aggregates can never suppress (M-4)
- **Decision:** the storm path bypasses the suppress branch by construction
  (separate code path); disposition ∈ {`page_now`, `passthrough`} via
  deterministic rules.
- **Alternatives:** two-call confirmation for storm suppress — rejected: doubles
  Jev load exactly when the rate limit binds, and two flipped calls are still
  correlated through the shared aggregate state.
- **Tradeoff:** storms always disturb a human; buys the correlated-error blast radius.

### T1-05 — Fingerprint includes env+cluster; allowlist namespaced (D-1)
- **Decision:** fingerprint =
  `sha256(service|check|severity_in|region|env|cluster)`; allowlist entries
  namespaced by env with attestation tuple (verifier, env, date, evidence).
  Cross-env rollup uses a separate marked key.
- **Alternatives:** keep env out, namespace allowlist only — rejected: dedup
  would still merge prod/staging incidents into one (wrong incident grouping is
  its own failure mode).
- **Tradeoff:** migration cost (dual-write + re-attestation); buys blast-domain
  separation. Chesterton's fence honored: the deploy-rollup use case keeps its
  own key.

### T1-06 — History provenance + staleness policy (D-2)
- **Decision:** every history number carries `history_as_of`/`history_source`/
  `history_coverage`; history older than 72h is treated as empty (⇒ novelty ⇒
  page); label-pipeline staleness is a paged SLO.
- **Alternatives:** (a) staleness ⇒ passthrough — rejected: passthrough pages
  but hides the cause; novelty+page carries the reason; (b) no staleness bound —
  rejected: the pre-mortem's D-2.
- **Tradeoff:** label-pipeline outages convert to pages; buys calibration honesty.

### T1-07 — Duplicate-inheritance requires field similarity (D-3)
- **Decision:** correlator `duplicate` inheritance requires title/metric
  similarity ≥ bound; mismatch ⇒ full triage as new. Plus onboarding
  fingerprint-quality audit with quarantine.
- **Alternatives:** trust customer integrations — rejected: the most dangerous
  inputs are well-formed.
- **Tradeoff:** slightly more Jev calls on sloppy integrations; buys the
  merged-incident miss.

### T1-08 — Audit WAL off the hot path (I-1)
- **Decision:** audit writes go to a local append-only WAL (no fsync on hot
  path), background flusher to SQLite; gate never blocks >50ms on audit;
  evidence loss is itself a paged event.
- **Alternatives:** (a) keep synchronous SQLite — rejected: latency is
  correctness on the paging path; (b) drop audit on pressure — rejected:
  evidence loss must be loud, never silent.
- **Tradeoff:** crash-recovery complexity (WAL replay); buys paging latency and
  the audit trail under disk pressure.

### T1-09 — Config validation + standby fallback (I-2)
- **Decision:** config loads are schema-validated; invalid ⇒ keep last-good +
  page operator (never crash-loop). Deployment keeps a standby direct-to-PD
  integration with an external health watcher and a monthly-tested fallback
  runbook; "Sentinel down" is a SEV1 on a path that doesn't traverse Sentinel.
- **Alternatives:** single-path cutover with fast restart — rejected: restart
  doesn't help a crash-loop, and every minute down is every alert lost.
- **Tradeoff:** customer setup complexity (standby integration); buys the
  dead-receiver case that fail-open cannot cover.

### T1-10 — Degraded-mode deterministic storm digest (I-3)
- **Decision:** when provider error rate crosses threshold, page ONE digest
  incident (deterministic ranking, top-5 root-cause candidates); queue the rest
  with a degraded banner. Degrade to deterministic triage, never raw passthrough.
- **Alternatives:** raw passthrough (status quo) — rejected: manufactures the
  page cannon that inverts the human.
- **Tradeoff:** digest ranking can miss the true root cause's position;
  mitigated by the top-5 being human-readable and the queue being inspectable.

### T1-11 — Threshold-change governance (H-1)
- **Decision:** cost floors (C_FN floor from industry range; override requires
  explicit flag + rationale); two-person signed ack for threshold changes
  (gate refuses unsigned); 5%/7-day canary; shadow report always shows
  default-cost column alongside custom.
- **Alternatives:** human decides, ungated (status quo) — rejected: the human
  is the single point of failure on the most leveraged number.
- **Tradeoff:** change velocity; buys the assumption-laundering miss.

### T1-12 — Suppression governance: SLO, clock, floor (H-2)
- **Decision:** suppression-rate SLO with watchdog paging; 30-day
  re-validation clock (expiry ⇒ revert to last-validated + alarm);
  `suppress_conf_min` floor 0.85 without principal exception; shadow report is
  a signed weekly check with a named owner.
- **Alternatives:** passive shadow report (status quo) — rejected: the ratchet
  only moves toward more suppression.
- **Tradeoff:** toil (re-validations, acks); buys the fatigue-driven drift.

### T1-13 — Allowlist admission standard + security category ban (A-1)
- **Decision:** admission requires ≥30-day observation, ≥K occurrences, zero
  incident linkage, named attestation with TTL; security/compliance check-name
  patterns are never allowlist-eligible (enforced in code); >10× rate spike on
  an allowlisted fingerprint ⇒ auto-evict + page + alarm.
- **Alternatives:** "customer-verified" boolean (status quo) — rejected:
  verification without a standard is theater, and it's poisonable.
- **Tradeoff:** slower allowlist growth; buys the poisoning attack.

### T1-14 — Freshness proofs on all three locks (C-1)
- **Decision:** suppress requires three *live* proofs (calibration fit ≤30d;
  threshold attestation ≤30d; allowlist attestation ≤90d + drift-check); any
  stale proof ⇒ lock fails ⇒ page. Rot-matrix (fresh|stale)³ as a permanent CI
  fixture.
- **Alternatives:** three values without freshness (status quo) — rejected: the
  pre-mortem proves common-mode rot defeats all three.
- **Tradeoff:** proof-maintenance toil; buys the compound 02:14.

---

*End of TRIPWIRE seed. Next: coordinator merge + other chiefs' Type 1s.*
