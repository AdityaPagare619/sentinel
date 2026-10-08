# Vendor Posture — Sentinel Dependencies (2026-10-08)

Procurement-ready summary of every third party in Sentinel's paging path.
Honest, no softening. Where a claim is "as understood" rather than verified
against a signed agreement, it says so.

## 1. TypeSafe Jev (decision model)

**Role:** advisory judge in the alert race. Jev advises; the deterministic
gate owns every page/suppress decision. If Jev is slow, down, or errors, the
bounded timer wins and the system fails open (pages).

| Item | Posture |
|---|---|
| SLA | **None.** No uptime commitment, no latency commitment. |
| SOC 2 / ISO 27001 | **None.** No audit reports available. |
| Data residency | **US-only hosting** (as understood from vendor docs). Customer alert
  text leaves the customer's region when sent for classification. |
| Data sent | Alert text (title, service, severity, signals) for classification.
  No customer PII is sent by design — the receiver strips fields outside
  the classification schema before the call. |
| Data retention | As understood: per TypeSafe's terms; not independently verified.
  Assume retention for abuse/quality purposes until a DPA says otherwise. |
| Training on customer data | **Prohibited** — the Master Customer Agreement forbids distillation
  / training on customer inputs (as understood). |
| Liability cap | **max(12 months fees, $50)** (as understood from MCA). Effectively
  zero recourse for a missed page. |
| Determinism | **Non-deterministic**: 1.3–2.2% decision flips measured on identical
  inputs (no seed). This is why Jev is advisory-only and why suppression
  additionally requires the triple lock. |

**Architectural consequence:** the fail-open race, the deterministic gate,
and the triple lock exist precisely because this vendor cannot be trusted
with a page. Any deployment that lets Jev suppress unilaterally is
misconfigured — the gates enforce this structurally.

**What a customer should do:** run the shadow pilot on their own traffic
before allowing any suppression; pin the model version (ADR-015); keep the
timer budget tight enough that a Jev outage degrades to paging, never to
silence.

## 2. PagerDuty (paging sink, BYOK only)

**Role:** the thing Sentinel pages through. Customer brings their own
PagerDuty account and API key; Sentinel never holds a shared key.

| Item | Posture |
|---|---|
| Key custody | Customer key, customer vault. Sentinel reads it from the operator's
  environment at the receiver; it is never logged, never committed,
  never sent anywhere except `events.pagerduty.com`. |
| Sentinel's PD contact | **Zero.** No Sentinel-operated PagerDuty account exists; dev, sim,
  and load tests structurally use FakePD and cannot contact the real API. |
| Blast radius of a key leak | Limited to paging (events API). The receiver needs no read/admin
  PagerDuty permissions; use an Events API v2 integration key. |

## 3. Vercel (platform API hosting)

**Role:** hosts the stateless platform API (auth, health, ops reads).
No paging path runs here.

| Item | Posture |
|---|---|
| Tier | Free tier ($0 ops budget). Per-instance ephemeral state; no
  cross-instance guarantees. |
| Consequence | The kill flag on this tier is per-instance and guards no paging
  path — the console disables the control and says so. Do not mistake
  this tier for the engine. |

## 4. GitHub Pages (console hosting)

**Role:** static console hosting only. No secrets, no backend logic.

## Open items

- [ ] **Credential rotation** — a past exposure is documented in
  `docs/SECURITY.md`; rotation is Aditya's action, still open.
- [ ] DPA / data-processing terms with TypeSafe — not yet executed.
- [ ] Independent verification of TypeSafe retention and no-training claims
  against the signed MCA.
