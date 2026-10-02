# 07 — Freshness Proofs: C-1 Implementation Design

**Lane 5 (Vault) · principal fix-designs wave · 2026-10-03**
**Status:** DESIGN. No code. Proposes mechanics for ADR-014 (PROPOSED —
needs Forge review + Aditya's verdict).
**Builds on:** synthesis §3.5 (C-1), ADR-014, ADR-017, ADR-022, `09-decision-register.md` DR-27,
`05-security-constitution.md` §7 Choice 1.

---

## 0. The thesis, in one paragraph

Tripwire's compound scenario proved the triple lock's independence
assumption false: quantization rot defeats lock 1, a frozen outcomes
pipeline plus a fatigue-ratcheted confidence bar defeats lock 2, and a
staging→prod fingerprint collision defeats lock 3 — three separate silent
rots, every component behaving "as designed." The resolution (synthesis
§3.5, ADR-014, **Type 1**): each lock carries a *proof of freshness*, and
**any stale proof ⇒ the lock fails ⇒ page**. This document specifies the
exact proof shapes, where they live, the validation points, the
stale⇒page mechanics, and the (fresh|stale)³ rot-matrix CI fixture.
The lock becomes three locks *with liveness* — which is what
"independent" was supposed to mean.

Judged against `00-laws.md`: Law 3 (this is a core safety invariant —
Type 1, RFC-grade); Law 6 (each lock gets its pre-mortem below);
Law 2 (rot is the named adversary — time is hostile); Law 7 data/AI
(deterministic checks on cached attestations; the model never evaluates
its own freshness).

---

## Part A — The proof shapes, validation points, and stale⇒page mechanics

### A1. Proof shapes per lock

**Lock 1 — probability lock: `CalibrationFitProof`**

The quantized-space implementation (synthesis §3.1, ADR-013): suppress
requires reported P(p1) = 0.00 AND (per-org calibration fit's p̂_upper <
0.002 OR, until the fit exists, dual human attestation on the allowlist
entry). The fit is the thing that rots — the mapping from quantized
report to upper confidence bound decays as the org's incident
distribution drifts.

| Field | Type | Meaning |
|---|---|---|
| `fit_id` | string | content hash of the fit artifact (binds proof to a specific fit) |
| `fit_trained_at` | RFC3339 UTC | when the fit was trained |
| `trained_on` | struct | `{ n: int, reference_class: string, label_pipeline_version: string, history_as_of: RFC3339 }` — the fit's pedigree (Ledger discipline; ADR-021's `history_as_of` feeds this) |
| `p_upper_measured` | float | the p̂_upper value at fit time (for audit, not for the gate — the gate reads the fit artifact, not the proof) |
| `validity_window_days` | int | org-configured TTL, default **90 days** (Type 2 — tunable; the *existence* of the window is Type 1) |
| `issued_by` | string | tuner pipeline run id + code version (never "a human" — the fit is machine-generated; humans don't hand-compute Wilson bounds) |
| `model_pin` | string | exact Jev model version the fit was trained against (ADR-015 — a vendor model move invalidates the fit mapping) |

**Freshness predicate:** `now - fit_trained_at ≤ validity_window_days`
AND `model_pin == config.pinned_model_version` AND
`label_pipeline_version` matches the currently-deployed label pipeline
(because a frozen outcomes pipeline D-2 poisons the labels the fit
*trains on* — the fit rots through its training data even if its date is
fresh).

**Lock 2 — confidence lock: `ThresholdAttestation`**

Lock 2 is the human-governed leg (ADR-022): the confidence bar ≥ 0.90,
the fatigue ratchet (H-2), the 30-day re-validation clock, the conf floor
0.85. Its rot vector is governance decay — a bar ratcheted down alert by
alert, or a re-validation clock that nobody answers.

| Field | Type | Meaning |
|---|---|---|
| `threshold` | float | the attested confidence bar (must equal the live config value — drift ⇒ stale) |
| `attested_by` | string (×2) | two distinct operator identities (ADR-022: two-person signed ack; the fatigue ratchet is a single-operator failure mode) |
| `attested_at` | RFC3339 UTC | when the two-person ack was signed |
| `on_evidence` | struct | `{ backtest_id: string, false_suppress_count: int, n: int, shadow_report_id: string }` — the evidence the attestation claims to rest on; a re-attestation without a fresh backtest is a rubber stamp |
| `revalidation_due_at` | RFC3339 UTC | 30-day clock from `attested_at` (ADR-022); passing due ⇒ stale |
| `config_hash` | string | sha256 of the exact thresholds.json the attestation covers; a hand-edited threshold (Tripwire I-2's trailing-comma world) invalidates every attestation on contact |

**Freshness predicate:** `now ≤ revalidation_due_at` AND
`threshold == config.confidence_bar` AND `config_hash == sha256(live
thresholds.json)` AND `threshold ≥ 0.85` (the conf floor — an
attestation *for* 0.82 is not a stale proof, it is an invalid one, and
invalid ⇒ page too).

**Lock 3 — allowlist lock: `AllowlistAttestation` + drift check**

Per ADR-017: allowlist entries are namespaced by env (fingerprint
includes env+cluster), entries carry attestation tuples
(who/when/on-what-evidence/TTL), security-category fingerprints are
banned in code. Its rot vectors are D-1 (staging→prod collision — now
structural, namespaced away) and silent staleness: a fingerprint attested
two years ago for a service that has since been rewritten.

| Field | Type | Meaning |
|---|---|---|
| `fingerprint` | string | `env:cluster:check_name:signature` (ADR-017 namespace; never env-blind) |
| `attested_by` | string (×2) | two distinct identities (dual attestation until the calibration fit exists — synthesis §3.1 — and as the corroboration standard for the allowlist leg permanently; "customer-verified" without a tuple is theater, meta-lesson 6) |
| `attested_at` | RFC3339 UTC | |
| `on_evidence` | struct | `{ occurrences: int, incident_linkage: string /* "zero" or incident id */, observed_since: RFC3339, note: string }` — the admission standard (30d / K-occurrences / zero-incident-linkage / attested / TTL) |
| `ttl_days` | int | default **180 days** (Type 2 — tunable; existence of TTL is Type 1) |
| `env` | string | the namespace; must match the alert's env — a cross-env match is impossible by construction, not by hope |
| `security_category_ban` | bool | must be `false`; any entry resolving to a security-category fingerprint is rejected *at admission* (in code, per ADR-017 — the ban is not a proof field, it is a structural guard; listed here so the validator can assert it) |

**Drift check (the second half of lock 3's freshness):** attestation
freshness is necessary but not sufficient. On each revalidation cycle,
the validator recomputes: does this fingerprint still meet the admission
standard? `occurrences` since attestation, any incident linkage (a
fingerprint that has since appeared in a real SEV1/2 is *poisoned* —
attestation void immediately, entry quarantined, page on next match),
service rewrite signals (deploy of a new major version of the owning
service resets `observed_since` — the fingerprint is a claim about a
specific service's noise, and the service is now a different service).

**Freshness predicate:** `now - attested_at ≤ ttl_days` AND drift check
passes (no incident linkage, admission standard still met, no owning-service
major-version rewrite since `observed_since`) AND `env` matches.

### A2. Where proofs live

**Decision (Type 1, structural): config-embedded, versioned, signed —
never sidecar files.**

The proofs live *inside* the versioned config bundle that ADR-018's
schema validation already gates:

```
config/
  thresholds.json          # lock 2's live values; config_hash binds here
  allowlist.json           # per-env namespaces; entries carry attestation tuples
  calibration.json         # the fit artifact + CalibrationFitProof
  proofs/                  # the canonical proof bundle (machine-checked)
    calibration_proof.json
    threshold_attestation.json
    allowlist_attestations.json
  manifest.json            # sha256 of every file above + schema version;
                           # the boot self-test verifies the manifest first
```

Reasons, stated (Law 4 — why not a sidecar DB?):

1. **The frozen spec's schema-validated config already owns "invalid ⇒
   last-good + page"** (ADR-018). Proofs riding the config bundle inherit
   the crash-loop-proofing, the last-good rollback, and the boot
   self-test for free. A separate proof store is a second config system
   with its own failure modes — Law 7 SWE: code is liability, and so are
   config systems.
2. **Atomicity.** The manifest binds all four locks' proofs to one
   content hash. A partial update (new thresholds, old attestation) is
   detectable *structurally* — the validator compares `config_hash` in
   the attestation against the manifest, and a mismatch ⇒ stale ⇒ page.
   Two stores cannot give you this without a transaction protocol.
3. **Audit.** The manifest hash lands in every decision event
   (`config_manifest_sha256`), so any postmortem can replay exactly which
   proofs were live at decision time (Ledger pedigree: the decision is
   bound to the proofs it was evaluated under).
4. **Rejected alternative — proofs in the audit DB:** the audit log is
   the *output* of decisions (ADR-011's event log), not an input to the
   gate. Reading proofs from the DB puts the gate downstream of the DB's
   liveness — a DB stall then fails the freshness check, which fails the
   locks, which pages everything: a correctness-preserving but
   availability-destroying coupling. Config-embedded proofs are readable
   from a local file with no network and no DB.

**Who writes them (Law 7 data/AI — the model never attests to itself):**

- `CalibrationFitProof` — written by the tuner pipeline run that
  produces the fit. Machine-generated, machine-signed (pipeline run id +
  code version). A human does not hand-edit it; the validator rejects any
  proof whose `issued_by` is not a tuner run.
- `ThresholdAttestation` — written by the two-person governance flow
  (ADR-022): two operators sign via the platform tier; the platform
  writes the JSON. The *gate* only reads.
- `AllowlistAttestation` — written by the admission flow (dual
  attestation), also platform-tier. The drift check is computed by the
  validator at revalidation time, not stored — stored drift verdicts
  would themselves rot (turtles).

### A3. Validation points — NEVER per-alert

The race-to-page budget is untouched (synthesis §3.5, Forge's Move 1).
Freshness is evaluated at exactly two points, both off the hot path:

**V1 — config load (boot + every config reload).** The validator runs as
a stage of the boot self-test (constitution A12's "prove the
verification contract on boot" pattern, applied to proofs): parse the
manifest, verify all content hashes, evaluate all three freshness
predicates against `now`, and produce a `FreshnessReport`:

```json
{
  "evaluated_at": "2026-10-03T01:30:00Z",
  "config_manifest_sha256": "…",
  "locks": {
    "lock1_calibration": { "verdict": "fresh|stale", "reason": "…", "proof_id": "…" },
    "lock2_threshold":   { "verdict": "fresh|stale", "reason": "…", "proof_id": "…" },
    "lock3_allowlist":   { "verdict": "fresh|stale", "reason": "…", "proof_id": "…" }
  }
}
```

The gate consumes the `FreshnessReport`, not the proofs. At triage time
the gate reads three booleans from memory — O(1), no parsing, no clock
arithmetic, no I/O. A config reload that produces a stale verdict for
any lock takes effect *immediately* (the gate's next decision sees it);
a reload that fails schema validation ⇒ last-good + page (ADR-018) —
the freshness validator never gets to run on a malformed bundle.

**V2 — periodic revalidation (the heartbeat).** A control-plane timer
re-runs the validator every **15 minutes** (Type 2 — the cadence is
tunable; the *existence* of periodic revalidation is Type 1, because
boot-only validation reintroduces the rot window between reloads).
Revalidation catches: clocks crossing `revalidation_due_at` (lock 2's
30-day clock), TTL expiry (lock 3), incident-linkage drift (lock 3's
drift check — a fingerprint attested this morning can be poisoned by
this afternoon's SEV1), and model-pin drift (lock 1's `model_pin` vs the
live pin — a vendor model move at 14:00 must not wait for the next
deploy to invalidate the fit).

On any transition fresh→stale: the gate's in-memory verdict flips, an
`integrity_break`-class control-plane alert fires (**unsuppressible** —
constitution §7 Choice 3: the guard's alarm bypasses the guard), and an
audit event is appended (`freshness_transition{lock, reason,
previous_proof_id}`). The stale lock fails; the lock fails ⇒ page.
No human is in the loop between detection and the page — Law 2 (the
operator is tired) and Law 6 (the pre-mortem where the 3 AM human
"reviews" the staleness and waves it through).

**Explicitly forbidden: per-alert validation.** Rationale, for the
record a Google principal will ask for (Law 1 — don't tune the
bottleneck, ask whether it should exist): per-alert proof evaluation
would put clock arithmetic, JSON parsing, and drift-check queries on
the race-to-page critical path — the exact budget Forge's Move 1
defends. Worse, it would make the gate's decision depend on the
*validator's* liveness: a slow drift-check query becomes paging
latency. The two-point design converts freshness from a per-decision
computation into a *state* the gate reads — the only shape that
composes with a hard paging budget.

### A4. The exact stale⇒page mechanics per lock

The gate's suppress path is a conjunction. ADR-014's rule is a
*precondition* on that conjunction: a lock whose proof is stale is a
lock that *fails*, and a failed lock ⇒ page. Mechanics:

**Lock 1 stale (fit expired, model-pin mismatch, or label-pipeline
version drift):**
- Suppress additionally requires the interim path from synthesis §3.1:
  **dual human attestation on the allowlist entry** (Tripwire T1-01 —
  "replaced, never approximated"). Until the fit is re-trained, the
  probability lock is evaluated *only* through the attestation fallback.
- The re-fit is a control-plane work item with an SLA (default: fit
  refreshed within 7 days of staleness; Type 2), tracked as an
  unsuppressible alert until closed. Staleness is not a state the
  system rests in — it is a state the system pages out of and then
  repairs.
- Pre-mortem for this lock (§C1 below): the quantization scenario —
  true 0.0049 reporting as 0.00 — is *exactly* what a stale fit
  re-introduces, because the fit is the only thing mapping the
  quantized report to the cost-optimal bar. A stale fit is not "slightly
  old math"; it is the M-1 bug returning through the calendar.

**Lock 2 stale (re-validation clock lapsed, config hash mismatch, or
bar below the 0.85 floor):**
- The confidence leg fails. Suppress is impossible while lock 2 is
  stale — there is no fallback, because the bar *is* the human's
  judgment, and a stale bar is judgment that nobody currently stands
  behind. (Contrast lock 1, which has a machine-fit fallback, and
  lock 3, which has the resolve-corroboration shape.)
- `config_hash` mismatch (hand-edited thresholds.json, the I-2 world)
  is the fastest path to staleness here: any edit to the live
  thresholds without a matching two-person re-attestation ⇒ lock 2
  fails ⇒ page. This is deliberate — it makes the fatigue ratchet
  (H-2) *mechanically impossible* to execute silently: the ratchet
  requires editing the bar, and editing the bar without attestation
  pages everything. The governance is in the physics, not the policy.
- Re-attestation requires a fresh backtest (`on_evidence.backtest_id`
  newer than the previous attestation) — a re-attestation citing the
  old backtest is a rubber stamp and the validator rejects it as
  invalid (invalid ⇒ page, same as stale).

**Lock 3 stale (attestation TTL expired, drift check failed, or
incident linkage detected):**
- The allowlist leg fails for the affected entries. Staleness is
  **per-entry**, not per-lock: one poisoned fingerprint does not fail
  the whole allowlist (Law 7 infra: blast radius thinking — the
  alternative is a single stale entry paging the entire org's noise,
  which trains the operator to bypass the freshness system entirely).
- Incident linkage is the emergency path: a fingerprint that appears
  in a confirmed SEV1/2 has its attestation **voided immediately** on
  the next revalidation heartbeat (≤15 min), the entry is quarantined,
  and the quarantine itself is an unsuppressible control-plane alert.
  The next matching alert pages — the system has learned, in the only
  direction safety allows.
- Drift-check failure (owning service major-version rewrite,
  admission-standard decay) ⇒ entry pages on next match until
  re-attested under the new evidence. The re-attestation must cite the
  *new* evidence (post-rewrite occurrences), not the original.

**Composition — the conjunction with liveness:**

```
can_suppress =
  lock1_fresh AND reported_p1 == 0.00 AND (fit.p_upper < 0.002 OR dual_attested)
  AND lock2_fresh AND confidence ≥ bar
  AND lock3_fresh(entry) AND fingerprint ∈ allowlist[env]
  AND corroboration_present          # ADR-019, above the silence floor
  AND NOT flagged_by_firewall        # ADR-020
  AND NOT storm_aggregate            # ADR-016
```

Any `lockN_fresh == false` short-circuits to page. The page carries the
reason (`page_reason: "lock2_stale: revalidation_due 2026-09-03 <
now"`) — the operator at 3 AM sees *why* the system is paging more, and
the reason names the repair (re-attest, re-fit, re-admit), not just the
symptom. (Prism: the Standing Verdict Strip extends to freshness pages —
"paging because the confidence bar's attestation lapsed 12 days ago;
re-attestation is a two-person action in the platform.")

### A5. The (fresh|stale)³ rot-matrix CI fixture — all 8 combinations

A permanent fixture (ADR-014). It lives in the gate's test suite, runs
on every commit, and constructs a synthetic config bundle per
combination with controlled proof ages. Each row names the combination,
the expected disposition for a would-be-suppressed alert (one that
passes all three locks' *value* checks), and what the test asserts
beyond the disposition.

| # | L1 (fit) | L2 (threshold) | L3 (allowlist entry) | Expected | Asserts |
|---|---|---|---|---|---|
| 1 | fresh | fresh | fresh | **suppress** (given all value checks pass) | The only row that may suppress. Asserts the decision event carries all three `proof_id`s + `config_manifest_sha256` (pedigree binding). |
| 2 | **stale** | fresh | fresh | **page** | `page_reason` names lock 1 + the staleness cause (expired TTL vs pin mismatch vs label-pipeline drift — three sub-cases). Asserts the dual-attestation fallback path is *offered* (lock 1's interim mechanics) and that without it the page stands. |
| 3 | fresh | **stale** | fresh | **page** | `page_reason` names lock 2. Asserts NO fallback exists (lock 2 has no interim path — the bar is the human's judgment). Asserts a `freshness_transition` audit event was appended at the V2 heartbeat that detected it. |
| 4 | fresh | fresh | **stale** | **page** | `page_reason` names lock 3 + the entry fingerprint. Asserts staleness is per-entry: a *second* fixture entry with a fresh attestation in the same run still suppresses (blast-radius containment). |
| 5 | **stale** | **stale** | fresh | **page** | The C-1 compound scenario's shape (M-1 + H-2 together). Asserts `page_reason` names **both** locks (the operator must see the full rot, not the first failure — short-circuit evaluation must still record all stale locks for the reason string). |
| 6 | **stale** | fresh | **stale** | **page** | Asserts both named; asserts the unsuppressible control-plane alert fired once per lock (not once per alert — alert-fatigue discipline on the guard's own alarm, constitution fence #11). |
| 7 | fresh | **stale** | **stale** | **page** | Asserts the incident-linkage sub-case: fixture entry with `incident_linkage` set ⇒ attestation void *even though* `attested_at` is within TTL (TTL is necessary, not sufficient — the drift check dominates). |
| 8 | **stale** | **stale** | **stale** | **page** | Total rot. Asserts page; asserts the system degrades to "everything pages" rather than "gate refuses to decide" (Law 7 product: graceful degradation — the failure mode is noisy paging, never silence, never a crash). Asserts the V1 boot path with an all-stale bundle still starts serving (fail-open at the config layer: a validator that refuses to boot on stale proofs would convert calendar rot into an outage — ADR-018's liveness lesson applied to freshness). |

**Fixture construction rules (so the matrix tests the mechanics, not the
calendar):**

- The fixture synthesizes proofs with *controlled ages* (e.g.
  `attested_at = now - 200d` against a 180d TTL) — never wall-clock
  dependent in a way that makes the test pass today and fail in a year.
  The validator takes an injectable clock; the fixture pins it.
- Each stale sub-case (TTL expiry vs pin mismatch vs drift) is a
  separate parameterized case under its row — the matrix is 8 rows, the
  suite is ~20 cases.
- The fixture also covers the **V1/V2 wiring**, not just the gate
  predicate: a test boots the validator on the row-8 bundle and asserts
  the `FreshnessReport` verdicts; a test advances the injected clock past
  a TTL boundary and asserts the V2 heartbeat flips the verdict and
  emits the control-plane alert. The matrix tests the *system* (proof →
  validation → gate → page), not just the conjunction.
- **Mutation discipline:** the fixture's alert is one that *would*
  suppress under row 1 (passes all value checks, has corroboration, is
  not firewall-flagged, is not a storm aggregate). If the fixture alert
  could never suppress, every row would trivially page and the matrix
  would prove nothing. The fixture's setup asserts the row-1 suppress
  first — the test fails loudly if the "would-suppress" premise breaks.

**Type marking:** the matrix itself is Type 1 (it encodes the safety
invariant in executable form — changing what row 1 asserts is changing
the product's safety case). The TTL defaults (90d fit, 180d allowlist,
15-min heartbeat, 30-day re-validation clock) are Type 2 (tunable per
org, versioned in config, audited on change).

---

## Part B — DR-27 / ADR-005 reconciliation brief: the IP-allowlist tension

**Status note:** ADR-005 is Aditya's decision exclusively (ADR deltas
§2). This brief takes no position on the verdict. It states both
positions fairly with evidence, proposes a resolution shape, and names
exactly what Aditya must decide and what follows from each verdict.
Prepared by Lane 5 (Vault) because the tension is a security-architecture
question.

### B1. Position 1 — ARCHITECTURE.md §7: "optional IP allowlist config" for the PD path

**What it says.** ARCHITECTURE.md §7 (Security): "PD path relies on the
customer's routing key + optional IP allowlist config." The generic
webhook path gets HMAC-SHA256 (`X-Sentinel-Signature`); the PagerDuty
path — where PagerDuty webhooks to *us* carry no customer-controlled
HMAC (PagerDuty signs with its own `X-PagerDuty-Signature` scheme, which
is PagerDuty's secret, not the customer's) — gets the routing key (a
bearer token in the URL path) plus an *optional* restriction that the
deliveries must originate from PagerDuty's published egress IP ranges.

**Why it was written (the honest archaeology — Law 5).** The routing key
is a bearer credential embedded in a URL. URLs leak: they land in proxy
logs, browser histories, error trackers, and the partner's Terraform
state (constitution §2.1's pre-mortem is *exactly* this shape — a secret
in a contractor's public fork). The IP allowlist was defense-in-depth
against the leaked-routing-key scenario: even with the key, the attacker
must also send from PagerDuty's network. For a v0.1 built in one night,
"restrict to the vendor's published IPs" was the cheapest available
second factor on a path that has no HMAC.

**The evidence for keeping it:**
- PagerDuty *does* publish its webhook egress IPs, and several
  practitioners do filter on them. It is not a fantasy control.
- The PD path is the highest-value path (it is the paging path for the
  design partners). Defense-in-depth on the highest-value path is the
  conservative instinct, and conservatism is warranted where the failure
  mode is a 3 AM forged page (constitution §2.1).
- "Optional" matters: orgs whose egress is stable can turn it on; orgs
  behind NAT churn leave it off. Optionality was the hedge against the
  brittleness argument.

### B2. Position 2 — ADR-005: IP allowlisting is brittle without benefit

**What it says.** DR-27 records the proposed ADR-005 posture: "NO IP
allowlisting." The precedent judgment (research/security-privacy note):
IP allowlisting for webhook verification is *brittleness without
benefit*.

**Why (the evidence, stated fairly):**
1. **Vendor IP ranges change.** PagerDuty, like every SaaS vendor,
   rotates egress infrastructure. An allowlist that is not updated in
   lockstep with the vendor's published ranges fails *closed on
   legitimate traffic* — real alerts rejected at 3 AM because PagerDuty
   added a netblock last Tuesday. The failure mode of a stale IP
   allowlist is *dropped pages*, which is the one failure mode this
   product forbids. A control whose rot mode is silence is
   architecturally opposed to Sentinel's safety case.
2. **It authenticates the network, not the sender.** Behind NAT,
   egress proxies, and shared cloud infrastructure, source IP proves
   almost nothing about *who* sent the bytes — and proves nothing at
   all about whether the bytes are *true* (constitution §0: provenance
   ≠ truth; IP filtering is the weakest form of provenance).
   Against the actual adversaries in the constitution's pre-mortems —
   a stolen routing key (attacker sends from anywhere; the allowlist
   only helps if the attacker can't reach PagerDuty's network, which
   any cloud VM can approximate via the vendor's own infrastructure
   or an egress proxy), or a poisoned payload over a legitimate
   channel (§2.2, where the bytes come from PagerDuty's real IPs
   anyway) — the allowlist is either bypassable or irrelevant.
3. **Operational cost with no measurable security gain.** Somebody must
   watch the vendor's IP announcements, update the config, and handle
   the 3 AM "PagerDuty rotated IPs and we're rejecting webhooks"
   incident. That somebody is the tired operator (Law 2). The
   security-privacy research note's industry judgment: mature
   receivers (GitHub, Stripe, Shopify, PagerDuty itself, Slack) all
   converged on HMAC-with-secret as the verification primitive, and
   none of them recommend IP allowlisting as the auth layer. The
   industry's revealed preference is evidence.
4. **It composes badly with the freshness regime.** An IP allowlist is
   a *fourth* lock with no proof-of-freshness story in ADR-014's
   design — vendor IP ranges rot on the vendor's schedule, not ours,
   and there is no attestation tuple for "PagerDuty's netblocks."
   Keeping it would either exempt it from C-1 (a lock that rots
   without a freshness proof — the exact thing C-1 forbids) or force a
   vendor-IP freshness protocol nobody has designed.

**The steelman of the anti-allowlist case in one line:** the control's
failure mode (rejecting real pages when the vendor's IPs change) is
*worse* than the attack it mitigates (which requires a leaked routing
key *and* is better mitigated by routing-key rotation hygiene and the
first-seen-critical annotation from constitution §2.1 R1).

### B3. Proposed resolution

**Recommendation (for Aditya's verdict, not a decision): adopt ADR-005
as written — no IP allowlisting — and repair the gap the allowlist was
covering with controls that don't rot into silence.**

The allowlist was covering the leaked-routing-key scenario. Replace it
with:

1. **Routing-key hygiene as a product feature** (constitution §3.4 —
   the secure path is the easy path): rotation is one command with a
   dual-valid window (the same pattern as DR-27's HMAC rotation sets);
   provisioning docs state plainly "this string pages your people;
   treat it like a password" (constitution §2.1 R3); a leaked-key
   rotation runbook exists before the first design partner.
2. **First-seen-critical annotation** (constitution §2.1 R1): a
   validly-signed, never-before-seen `alert_key` paging at critical
   severity raises an unsuppressible "unprecedented alert" annotation.
   This converts the leaked-key forgery from "indistinguishable page"
   to "page marked as unprecedented" — detection instead of the
   allowlist's brittle prevention.
3. **Rejection-burst telemetry** (constitution §7 Choice 3): the
   guard's alarm bypasses the guard — anomalous delivery patterns are
   unsuppressible control-plane alerts regardless of source IP.
4. **Remove the §7 mention or mark it superseded.** If Aditya adopts
   ADR-005, ARCHITECTURE.md §7's "optional IP allowlist config" must be
   struck or annotated as superseded-by-ADR-005 in the same commit —
   a frozen spec that contradicts the decided ADR is a trap for the
   next builder (Law 5: the fence's reason must be addressed, and the
   address must be *written where the fence was*).

**Why this resolution rather than "keep it optional":** "optional"
preserves the worst of both — the code path exists (attack surface,
test burden, config surface), the rot mode exists for anyone who
enables it (dropped pages on vendor IP rotation), and the security
benefit accrues only to orgs that maintain it perfectly, which by Law
2 is nobody at 3 AM. Optionality is not a compromise here; it is the
brittleness with a settings page.

### B4. What Aditya must decide

The verdict is a single binary with a documentation tail:

1. **Adopt ADR-005 (no IP allowlisting)?** Yes / No.
2. **If yes:** authorize striking the §7 "optional IP allowlist
   config" mention (superseded-by-ADR-005 annotation), and confirm
   the four replacement controls above as the gap-repair (they are
   currently derived requirements R1/R3 + Choice 3, not yet
   committed work).
3. **If no (keep the allowlist):** decide its exact semantics, because
   "optional IP allowlist config" as written is under-specified for
   implementation —
   - Is it *auth* (reject non-matching) or *telemetry* (annotate
     non-matching but accept)? (Telemetry-only is the only form that
     doesn't rot into dropped pages; auth-form needs a vendor-IP
     freshness protocol that doesn't exist yet.)
   - Who owns updating it when PagerDuty publishes new ranges, and
     what pages the operator when the update is missed?
   - How does it satisfy C-1 — what is its proof of freshness?
   A "keep" verdict without these answers re-creates the hole the
   wave just closed.

### B5. What happens under each verdict

**If ADR-005 is adopted (recommended):**
- ARCHITECTURE.md §7 is amended (superseded annotation); DR-27's
  "Known tension" line is closed with the verdict date.
- The PD path's verification story is: routing key (bearer) +
  first-seen-critical annotation + rejection-burst telemetry +
  one-command rotation. No IP logic in the receiver; the receiver's
  verification contract stays minimal and auditable (constitution
  §3.1 — the verifier is the code there is least of).
- The four gap-repair controls enter the build backlog as derived
  requirements with tests (constitution §2.1 R1/R3).

**If the allowlist is kept:**
- Forge must review an IP-allowlist freshness protocol before
  implementation (C-1 applies to all locks; a lock without a
  freshness proof is a finding, not a feature).
- The receiver gains an IP-evaluation stage with its own tests, its
  own config surface, and its own 3 AM failure mode (vendor IP
  rotation ⇒ rejected real pages). The pre-mortem for *that* must be
  written (Law 6) before the code: "it is one year from now;
  PagerDuty rotated egress IPs on a Tuesday; Sentinel rejected real
  SEV1 webhooks for six hours. What exactly failed?" — the answer
  had better not be "nobody updated the allowlist."
- The "optional" semantics must be specified per B4.3, or the next
  builder will implement auth-form and inherit the rot mode.

**Type marking:** the verdict itself is **Type 1** (verification
primitive for the highest-value ingress path — irreversible in
practice once design partners integrate against it). The four
gap-repair controls are Type 1 as safety requirements, Type 2 in
their exact UX/cadence.

---

## C. Pre-mortems for the freshness regime (Law 6)

### C1. "The fit rotted and the M-1 bug came back through the calendar"

It is March 2027. Org "fintech-partner" has been live for five months.
Their calibration fit was trained in October 2026 on 412 labeled
outcomes. In January, they migrated half their fleet to a new instance
family; the alert distribution shifted; the true P(true SEV1 |
reported-0.00) crept from 0.0011 to 0.0049 — 2.45× the 0.002 bar. The
fit's `p̂_upper` still reads 0.0016 because the fit is a photograph of
October. Reported 0.00s keep passing lock 1. On March 14, a real SEV1
reports 0.00, suppresses, and the partner finds out from a customer
tweet.

**What the freshness regime does:** the fit's 90-day TTL expired in
January. The V2 heartbeat flipped lock 1 to stale; the gate fell back
to the dual-attestation interim path (synthesis §3.1); suppressions
requiring the fit stopped; an unsuppressible "calibration fit stale —
re-fit SLA 7 days" alert fired. The March 14 alert *paged*, because
lock 1 was stale and stale ⇒ page. The incident in this pre-mortem is
"the partner got paged more for six weeks while the fit was re-trained"
— annoying, trust-taxing, and the correct failure mode. The design
choice that matters: the TTL default (90d) is Type 2, but the *fallback
to attestation rather than to the stale fit* is Type 1 — a tuner
pipeline that "extends" an expired fit by re-stamping the date without
re-training is fraud, and the validator's `issued_by`-must-be-a-tuner-run
rule plus the `trained_on.n` pedigree make the fraud detectable.

### C2. "The operator re-attested the ratcheted bar at 3 AM"

It is June 2027. An operator, drowning in pages, has been lowering the
confidence bar 0.01 at a time for three weeks (the H-2 fatigue ratchet).
Each edit broke the `config_hash` binding, so lock 2 went stale and
everything paged — which is what drove the operator to the
re-attestation flow at 3 AM, intending to bless the 0.81 bar and get
some sleep.

**What the regime does:** the re-attestation requires (a) two distinct
identities — the 3 AM operator cannot self-attest; (b) a fresh backtest
cited in `on_evidence` — the rubber-stamp rejection rule voids any
attestation citing the old backtest; (c) the 0.85 conf floor — an
attestation *for* 0.81 is invalid, not stale, and invalid ⇒ page. The
ratchet cannot be laundered through the attestation flow because the
flow's inputs (second human, fresh evidence, floor) are exactly the
things the ratchet lacks. The pre-mortem's lesson: freshness proofs
don't just detect rot — the *attestation requirements* are the
anti-ratchet physics. ADR-022's governance and ADR-014's freshness are
the same mechanism viewed from two sides.

### C3. "The allowlist entry outlived the service it described"

It is September 2027. A fingerprint for `cache-cluster` eviction noise
was attested in January with a 180-day TTL. In May, the team replaced
the cache cluster with a managed service; the check name survived the
migration (same Terraform module, new backend). The fingerprint still
matches — same check name, same env — but the noise profile is entirely
different: the managed service's eviction alerts now correlate with
real latency incidents. In July the TTL expired; nobody re-attested;
the entry went stale and matching alerts paged all summer. In August,
annoyed, an engineer re-attested the entry citing *the January
evidence* ("it's been fine for months" — it had been paging, which is
why it seemed fine).

**What the regime does, and where it strains:** the drift check is the
hero and the weak point. TTL expiry did its job (summer of paging, not
summer of silence). The strain is the August re-attestation: the
validator's drift check should have caught the owning-service
major-version rewrite (May) and demanded post-rewrite evidence — but
the "service rewrite" signal depends on the org wiring deploy metadata
into the validator, which is the least-mature integration in this
design. **Honest flag:** the drift check's rewrite-detection is only
as good as the deploy-metadata feed. Until that feed exists, the
defense rests on TTL expiry + the re-attestation's `on_evidence`
requiring *post-expiry* occurrences (the January evidence is
pre-expiry and the validator rejects it — occurrences must be newer
than the previous attestation). That backstop holds even without the
deploy feed. The deploy-metadata integration is recorded as required
follow-up work, not assumed.

---

## CREATIVE APPLICATION — what this design does that the industry doesn't

**1. Freshness as a first-class safety primitive, not a cron job.**
Every alerting system has stale thresholds; none that we know of
treat *proof-of-freshness per lock leg* as a load-bearing part of the
safety case with a CI matrix enforcing it. The industry's pattern is
"review thresholds quarterly" (a calendar reminder). Ours is "the
lock fails closed on stale proof, the rot matrix proves it on every
commit, and the page reason names the repair." The moat version:
*"Our suppression system cannot silently age — every leg of every
suppress decision carries a proof it is current, and the proof is
checked by machines, not remembered by humans."*

**2. The ratchet is defeated by physics, not policy.** The fatigue
ratchet (H-2) is the failure mode every "human in the loop" system
claims to handle with "governance." Our answer: editing the bar
without re-attestation doesn't weaken the gate — it *pages everything*,
because the `config_hash` binding breaks and lock 2 fails. The
governance is in the mechanism: the cheapest action under fatigue
(edit the file) produces the most paging, which is the opposite of
what the fatigued operator wants. Systems that make the wrong action
easy and the right action hard lose; we inverted the gradient.

**3. The rot matrix as a trust artifact.** The (fresh|stale)³ fixture
is not just a test — it is the executable version of the C-1 finding,
and it ships to the buyer. In a security review, "show me that stale
calibration can't suppress" is answered by running row 2, not by a
slide. This is the trust-moat pattern from synthesis §5 (evidence
over claims) applied to the safety case itself: *the proof that our
locks can't rot is itself a program you can run.*

---

## Type register for this document

| Decision | Type | Rationale |
|---|---|---|
| Each lock carries a proof of freshness; stale ⇒ page | 1 | Core safety invariant (ADR-014) |
| Proofs live config-embedded (manifest-bound), not sidecar | 1 | Structural; changes the config contract |
| Validation at boot/reload + 15-min heartbeat; never per-alert | 1 (existence) | Paging-path architecture |
| Heartbeat cadence (15 min), TTL defaults (90d/180d), re-fit SLA (7d) | 2 | Tunable per org; versioned, audited |
| 30-day re-validation clock; two-person attestation; 0.85 floor | 1 | Safety-policy governance (ADR-022) |
| Rot matrix: 8 rows, ~20 cases, permanent CI fixture | 1 | Encodes the safety invariant executably |
| ADR-005 verdict (IP allowlisting) | 1 | Verification primitive; Aditya decides |
| Gap-repair controls if ADR-005 adopted | 1 (requirements) / 2 (UX, cadence) | Safety requirements vs implementation detail |

---

*End of 07-freshness-proofs. Next: Forge reviews the mechanics; Aditya
verdicts ADR-014 and ADR-005. Nothing here is implemented until both.*
