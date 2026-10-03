# Research agenda — Security & privacy

**Owner:** Vault (Chief Security) · **Dir:** `research/security-privacy/`
**Mission:** the security and privacy precedents Sentinel must match or exceed —
webhook authentication patterns in the wild, audit-log integrity approaches,
data-residency options, and secret-handling precedents. Aditya's order: security
and privacy posture is *shaped by R&D findings*, not asserted. Everything here is
precedent-based: who does it, how, and what broke when someone didn't.

## Tonight's question (2026-10-02)

**Q1:** How do production webhook receivers authenticate senders (HMAC patterns —
GitHub, Stripe, PagerDuty webhook signing), how do audit logs achieve
tamper-evidence (hash chaining, WORM, signed checkpoints), and what are the
realistic data-residency answers for a US-hosted inference dependency?

## Standing backlog (ranked by leverage)

1. **Webhook auth matrix:** HMAC-SHA256 patterns across GitHub/Stripe/PagerDuty/
   Opsgenie — header names, payload canonicalization, timestamp tolerance,
   rotation. (Feeds the receiver's auth design and `X-Sentinel-Signature` spec.)
2. **Audit integrity:** hash-chained logs, WORM storage options, signed
   checkpoints, what "immutable" actually costs operationally. Our audit log is
   the product's proof — its integrity story must be airtight and explainable.
3. **Secret handling precedents:** how BYOK products handle customer keys
   (envelope encryption, in-memory-only decrypt, redaction) — who does it well,
   who got breached doing it badly.
4. **Data residency vs US-only inference:** the honest architecture for
   EU-conscious buyers while TypeSafe is US-only — relay options, minimization
   (what never leaves the customer), contractual mitigations (DPA).
5. **Threat model inputs:** documented attacks on alerting pipelines
   (webhook spoofing, alert injection, audit tampering) — each with a source,
   each mapped to a mitigation or an accepted risk with a name on it.
6. **Compliance shape:** what SOC 2 Type II actually requires of a company our
   size, and the honest timeline — no "we're secure" without the audit.

## Sources to mine

- Vendor webhook-signing docs (GitHub, Stripe, PagerDuty), NIST SP 800-series
  (where relevant), Trail of Bits / Latacora engineering writing, breach
  postmortems involving key mishandling.
- TypeSafe DPA + sub-processor list (re-read on schedule).

## Output contract

Dated notes in `research/security-privacy/` per the charter. Every pattern gets
a precedent (who, URL, date). Every accepted risk gets a named owner and a
review date. Vault's rule: a security claim without a precedent is a wish.
