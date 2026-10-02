# 11 — Synthesis: The Re-designed Sentinel

**Principal-redesign wave · coordinator synthesis · 2026-10-03**
**Status:** DESIGN. No code. All eight chief studies gated and merged.
**Judged against:** `design/principal/00-laws.md` (the seven laws).

This document is the re-designed Sentinel: what survived the re-derivation,
what changed, and — the wave's actual product — the cross-domain resolutions
where the chiefs' findings collided. Every collision below was real; every
resolution is a design decision, marked Type 1 or Type 2 per Law 3.

---

## 1. What survived (the re-derivation confirmed the core)

The wave tried hard to kill the frozen architecture. Most of it survived,
and the kills that failed are evidence, not luck:

- **The two-tier architecture** (hot paging path + platform tier, audit log
  as the only bridge) is right. It is the control-plane/data-plane split;
  the file-as-bridge is the correct mechanism. (Forge §0)
- **Shadow-first deployment** survives and is now the product strategy, not
  just a rollout step: the product is a **proof engine first, suppression
  engine second**. (Pager 03)
- **Fail-open always** survives — and the wave extended it: fail-open
  covered triage failures; it did not cover process death. See §3.6.
- **The triple lock's shape** survives — three independent legs — but its
  *implementation* is re-derived. See §3.1.
- **The math-in-code boundary** was implicit in the frozen spec; the wave
  made it explicit law and registered it Type 1: deterministic code does
  math, counting, and thresholds; Jev answers contextual judgment questions
  only. (Oracle §2)
- **No accuracy claims; calibration evidence only** — confirmed and
  deepened into decision calibration with Wilson bounds over named
  reference classes. (Oracle §7, Ledger §4)

---

## 2. What the frozen spec got wrong (the honest ledger)

The frozen spec was a v0.1 built in one night. The wave found five real
holes in it. Naming them is the point of the exercise:

1. **The do-no-harm law protected the paging path from the platform but
   not from the engine's own inference call.** The ~11.4s Jev call blocked
   the hot path. (Forge Move 1)
2. **The audit log had the dual-write problem.** A crash between the audit
   write and the forward produced a lying river (says paged, never paged)
   or a silent gap. (Forge Move 2)
3. **Fail-open was vacuous when the thing that failed was the paging path
   itself.** "Log + metric" as the forwarder's failure mode degrades to
   silence — the one failure mode the product forbids. (Forge Move 3)
4. **The 0.002 suppress bar was a units error.** Jev probabilities are
   quantized to 0.01; a true 0.0049 (2.45× the bar, $250 expected cost vs
   $100 page) reports as 0.00 and passes. Nobody propagated the
   quantization into the gate. (Tripwire M-1)
5. **Fail-open assumed liveness.** Every "never drops a page" promise was
   conditioned on a running process. A crash-looped receiver under hard
   cutover means connection-refused for every alert — the SEV1 never
   enters the system. (Tripwire I-2)

---

## 3. Cross-domain challenge — the collisions and their resolutions

### 3.1 M-1 vs Oracle §1.5: the threshold is finer than the instrument

**The collision.** Oracle found the quantization fact and concluded the
`P(p1) < 0.002` leg is a *point condition* (satisfiable only by reported
0.00), with decision-relevant calibration living at the reported-0.00 mass
and the confidence leg doing the continuous work. Tripwire went further:
the expected-cost *justification* (DR-4, "suppress iff p < t*") is
**unimplementable from raw 0.01-rounded reports** — the bar cannot be
evaluated as a comparison on a continuous quantity. Both are right; they
are the same wire fact viewed at different levels.

**Resolution (Type 1).** The t* math justifies the bar's *existence* (why
0.002 and not 0.5 — the cost model). The bar's *implementation* is
re-derived in quantized space:

- Suppress requires **reported P(p1) = 0.00 AND a per-org calibration
  fit's p̂_upper < 0.002** (the fit maps the quantized report to an upper
  confidence bound, trained on the org's own labels by the tuner).
- **Until the fit exists, the probability lock is replaced, never
  approximated:** suppress additionally requires dual human attestation on
  the allowlist entry. (Tripwire T1-01)
- Oracle's decision-calibration certificate — P(true SEV1 | suppress) with
  Wilson bounds over the named reference class — is measured on the
  *actual gate including this implementation*, so the empirical claim stays
  honest regardless of the quantization.

The honest interim matters: in shadow mode (weeks 1–2 for every org),
suppressions don't execute anyway — so the attestation requirement costs
nothing during the period when fits don't exist yet. The deployment path
and the calibration rigor compose.

### 3.2 Vault's "silence takes two" vs the triple lock's allowlist leg

**The collision.** Vault Choice 1: suppression above the silence floor
requires corroboration — a lone model judgment can *never* suppress.
Oracle's triple lock already has the allowlist as its third leg. Is the
allowlist corroboration, or is Vault demanding something more?

**Resolution (Type 1).** The allowlist **is** a corroboration shape —
human-verified ground truth — but Tripwire's meta-lesson 6 applies:
"customer-verified" is not a verification standard. Verification is a
tuple (who / when / on-what-evidence / TTL) or it is theater. So:

- Allowlist entries carry **attestation tuples with TTL**, namespaced by
  env (D-1: the fingerprint includes env+cluster — a staging→prod
  fingerprint collision is a concrete missed-SEV1 pre-mortem).
- Vault's runtime corroboration shape — a matching signed `resolve` from
  the *same source* for the *same alert_key* — is the second corroboration
  form, covering resolved-then-refired episodes.
- The silence floor (which severities need corroboration) is a versioned,
  audited policy; changing it is a two-person, audit-logged action.

Corroboration is now a defined standard with two shapes, not a word.

### 3.3 Forge's event log vs Ledger's pedigree vs Prism's river

**The collision.** Three chiefs converged on the audit log from different
directions: Forge (Move 2: the dual-write problem demands an append-only
event log), Ledger (N1: raw events are the foundation of data pedigree),
Prism (the river renders decisions — but from what source of truth?).

**Resolution (Type 1).** They are the same object:

- The audit log becomes an **append-only event log**:
  `decision_requested` / `decision_made` /
  `forward_confirmed | forward_failed` / `flip_observed`.
- The decision river is a **projection over events**, not a table of rows.
  A crash between decision and forward can no longer lie: the river shows
  `decision_made` without `forward_confirmed`, which is itself a paged
  event (the forwarder's watchdog).
- Ledger's pedigree layers map onto it directly: the event log is the
  raw-events layer (transformations: NONE); calibration statistics,
  dashboards, and shadow reports are transformations with named
  assumptions and adversaries.

One durable-truth format; everything downstream reads it.

### 3.4 Prism's counterfactual receipt vs Forge's frozen contracts

**The collision.** Prism's operator study demands the counterfactual
receipt — every suppression row carries "what would have paged me"
("at 0.70 this paged; at your 0.85 it stood down"). This needs the tuner
contract to expose at-threshold evaluation per decision. Forge's platform
contracts are frozen.

**Resolution (Type 1 — contract addition).** The counterfactual receipt is
not decoration; it is the mechanism by which a 3 AM operator trusts a
suppression they slept through (Prism §5: contrast against the operator's
own experience survives impaired cognition; a bare 0.87 does not). The
frozen contract gains one field on the decision event:
`threshold_counterfactual` — the disposition the gate *would* have taken
at each of the org's configured threshold presets. This is deterministic
(post-hoc evaluation of a pure function), costs nothing on the hot path,
and is computed at event-write time. Forge's RFC owns the exact schema.

### 3.5 C-1: locks that rot together are one lock

**The finding.** Tripwire's compound scenario: quantization (M-1) defeats
lock 1, a frozen outcomes pipeline (D-2) plus a fatigue-ratcheted
confidence bar (H-2) defeats lock 2, a staging→prod fingerprint collision
(D-1) defeats lock 3. Three locks, three separate silent rots, every
component behaving "as designed." The triple lock's independence
assumption is false.

**Resolution (Type 1).** Each lock carries a **proof of freshness**:
calibration-fit date (lock 1), threshold attestation (lock 2), allowlist
attestation + drift check (lock 3). **Any stale proof ⇒ the lock fails ⇒
page.** Freshness checks are off the hot path (cached attestations,
validated at config load and periodic revalidation — never per-alert, so
Forge's race-to-page budget is untouched). A permanent (fresh|stale)³
rot-matrix CI fixture tests every combination. The triple lock is now
three locks *with liveness*, which is what "independent" was supposed to
mean.

### 3.6 I-2: fail-open assumes liveness

**The finding.** A hand-edited `thresholds.json` with a trailing comma
crash-loops the receiver; under hard cutover ("point integrations at
Sentinel *instead of* PagerDuty"), that's connection-refused for every
alert. The SEV1 never enters the system.

**Resolution (Type 1 — deployment architecture).** Two mitigations:
schema-validated config (invalid ⇒ last-good + page, never crash-loop),
and a **standby direct-to-PD integration with an external health watcher
and a monthly-drilled fallback runbook**. Note the composition with
Pager's deployment path: in shadow mode the hole doesn't exist (read-only
tap alongside the existing stack — a dead Sentinel changes nothing). The
liveness requirement is specific to cutover, which is why cutover is
Stage 2c, not Stage 0. The deployment path's ordering is load-bearing for
safety, not just sales.

### 3.7 The open items (named, not hidden)

- **O-1 — audit data retention policy.** Append-only forever with no
  retention policy is acknowledged debt (Relay, Forge). It is Type 1 in
  both directions (deleting is irreversible; keeping forever is a
  liability). Needs an RFC before the first design partner. **OPEN.**
- **DR-27/ADR-005 tension.** ARCHITECTURE.md §7 mentions "optional IP
  allowlist config" for the PD path; ADR-005 judges IP allowlisting
  brittle without benefit. The reconciliation belongs in **Aditya's
  ADR-005 verdict**. **OPEN.**
- **docs/SECURITY.md** exists only on the deleted `lane/vault-hardening`
  branch. The redesign references it; it must be re-introduced onto the
  redesign branch before implementation resumes. **OPEN (mechanical).**

---

## 4. The re-designed Sentinel — the whole, stated once

**Hot path** (paging-plane): receiver (PD Events API v2-compatible,
signature-verified, schema-validated config that can never crash-loop) →
correlator (deterministic: dedup, storm-collapse, flap-debounce,
change-windows — never pays Jev for deterministic work) → gate →
durable forwarder (local outbox; at-least-once with stable `dedup_key`
idempotency; undelivered past X ⇒ customer-configured secondary channel;
no single notification path, ever).

**The gate** (race-to-page): the Jev call races a paging budget B.
Timer fires first ⇒ immediate passthrough (the page goes out; the late
answer becomes a shadow decision event). Suppression requires Jev fast
*and* confident *and* corroborated. Paging latency is bounded by our
budget, never by the vendor's tail. Shadow stops being a mode and becomes
the system's normal way of handling slow inference.

**The suppress decision** (triple lock, re-derived): (1) probability lock
in quantized space — reported 0.00 AND (calibration-fit p̂_upper < 0.002
OR dual human attestation until the fit exists); (2) confidence lock ≥
0.90 with the fatigue-ratchet guard (suppression-rate SLO + watchdog,
30-day re-validation clock, conf floor 0.85); (3) allowlist lock —
attestation tuples (who/when/evidence/TTL), namespaced by env, with a
security-category ban in code. Each lock carries a proof of freshness;
stale ⇒ page. Storm aggregates can never suppress (separate code path,
not a flag). Novel fingerprints always page.

**Math-in-code boundary** (Type 1): code does threshold arithmetic, the
versioned Q1/Q3 aggregation formula, all calibration statistics, counting,
timeouts/retries/fail-open, fingerprint hashing. Jev answers Q1/Q3/Q2
judgment questions only — never computes thresholds, never aggregates,
never counts. Exact model-version pinning; mismatch ⇒ passthrough + drift
page; new versions require the 7-day re-validation protocol.

**Audit**: append-only event log (`decision_requested`/`decision_made`/
`forward_confirmed|failed`/`flip_observed`), hash-chained, WAL off the
hot path (never block >50ms; evidence loss is a paged event), signed
hourly checkpoints to a customer-controlled sink. The river, the
calibration dashboards, and the shadow reports are projections.

**Deployment** (proof engine first): Stage 0 shadow (read-only tap, zero
write path, weekly Shadow Report with the numbered divergence list) →
Stage 1 divergence backtest (6–12 months of incident history; the bar is
**zero false-suppress on SEV1/SEV2** — the only non-negotiable number) →
Stage 2 canary (2a advisory annotation → 2b low-urgency suppression with
receipts + one-click override → 2c full pre-page gate → 2d autonomous,
optional, never pushed). Rollback is a mechanism: auto-revert on any
confirmed SEV1/SEV2 false-suppress; the kill switch is out-of-band,
one action, tested quarterly under game-day conditions.

**Platform tier** (read-only, zero Jev calls on read paths): dark-cockpit
UI — quiet is a signal, red is reserved for pages. Every screen opens
with a Standing Verdict Strip (one plain-language sentence; the accept
test is the operator who reads only the strip and acts correctly).
Degraded-first composition: the API-down copy is written before the happy
path; the simulator refuses to project on thin data; onboarding refuses
to fake liveness. Every suppression row carries its counterfactual
receipt. The gate is unaffected by any screen — paging never depends on
a dashboard.

**Security** (adversarial): asymmetric trust (silence takes two);
instruction firewall — alert text is hostile input by architecture,
flagged payloads fail closed to page, effectiveness measured by a
GhostJacking-shaped adversarial corpus in CI; the customer holds the seal
(checkpoints to a customer-controlled sink, one-command verifier); the
guard's alarm bypasses the guard (rejection bursts and integrity breaks
are unsuppressible control-plane alerts). Honest scope: 12 explicit
non-defenses, stated plainly.

**Data**: pedigree discipline on every number (raw events →
transformations → assumptions → named adversary). Decision calibration,
not distribution calibration: P(true SEV1 | suppress) with Wilson bounds
over named reference classes. Never rescale Jev's probabilities; the
tuner moves thresholds, never probabilities. Every measurement
pre-registered; every calibration number carries its reference
distribution, N, error bars, shift-check, and validity window — stale
claims are marked STALE, never silently trusted.

---

## 5. The trust moat (why this is the product)

The eight studies converge on one strategy, stated most sharply by Pager:
*Sentinel sells evidence, and the suppression comes free with it.* The
moat is not the model — it is the artifacts the design generates and no
competitor can replicate without earning them alert by alert:

1. **The Divergence Ledger** — every gate-vs-human disagreement, with
   evidence, permanently recorded. Trust instrument, training signal,
   and sales collateral in one artifact.
2. **Suppression Receipts** — every suppressed alert emits a durable
   receipt: what, why, what would have un-suppressed it, one-click
   appeal. In a postmortem, "why didn't we get paged?" is answered in
   seconds.
3. **The signed Backtest Ledger** — the buyer's own incident history,
   replayed, with zero false-suppress on SEV1/SEV2 as the bar. The one
   fear (a suppressed real SEV1) is answered only by evidence on the
   buyer's own data.
4. **Published calibration** — decision-calibration certificates with
   denominators, error bars, and expiry dates. The one-year test: "show
   me the 412."
5. **The Kill-Switch Runbook** — tested quarterly, reported to the buyer.
   A kill switch that has never been pulled is a rumor.

---

## 6. Brutal-gate record

All eight chief studies were read in full by the coordinator; key math
was hand-verified (t* = 100/50,100 ≈ 0.001996; the M-1 expected-cost
arithmetic: 0.005 × $50,000 = $250 vs $100 page); historical claims were
spot-checked against the record (Target 2013, Meta 2021-10-04, FirstEnergy
2003-08-14, Überlingen 2002-07-01, Knight Capital).

- **Rejected:** none. **Sent back for rework:** none.
- The gate's work was verification and cross-domain challenge, not
  rejection. The sharpest challenge — M-1 vs Oracle §1.5 — is resolved in
  §3.1 above; it changed the design (the probability lock's implementation)
  rather than any study.
- Pre-existing note: the studies were produced under the 11:00 IST
  deadline at high intensity; the RFC-grade rigor Law 3 demands for each
  Type 1 move happens *before implementation resumes*, not in this wave.
  This document records the decisions; the RFCs prove them.

---

## 7. What the coordinator is NOT deciding

Per standing orders: **ADR-001/005/007 are Aditya's.** The deltas in
`12-adr-deltas.md` are proposals. The frozen spec (`ARCHITECTURE.md`,
`PLATFORM_ARCHITECTURE.md`, `CHARTER.md`) is **not unfrozen** by this
wave — the redesign is documented alongside it, and application of any
delta waits for Forge's review and Aditya's verdicts.
