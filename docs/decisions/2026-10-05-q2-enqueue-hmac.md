# Decision: Q2 — /v2/enqueue authentication → HMAC (ADR-005 scheme)

**Date:** 2026-10-05 IST · **Decided by:** Aditya (founder; T+0 full-authority release)
**Type:** 1 — irreversible contract (public ingress auth scheme; sender cutover cost).
Unlabeled decisions default to Type 2; this one is labeled explicitly.

## Decision
`/v2/enqueue` is authenticated with HMAC, reusing the tested ADR-005 scheme
already gating `/webhook/generic`. Enforcement build proceeds per the R-3
migration design (auth matrix, onboarding-mode window, cutover plan). This
closes R-3: the highest-value ingress route no longer accepts unauthenticated
`critical` injection from any TCP connection.

## Alternatives considered and rejected
- **mTLS for senders:** stronger identity, but requires sender certificate
  issuance, rotation, and revocation infrastructure we do not have; cutover
  cost an order of magnitude above HMAC for zero additional threat coverage
  on this route. Rejected on cost/complexity.
- **Bearer/API-key auth:** weaker than HMAC for webhook-shaped ingress
  (key in header, no body integrity); PagerDuty/Stripe webhook practice
  converges on HMAC signatures for this shape. Rejected on industry-practice
  grounds.
- **Accept the risk with network policy:** leaves arbitrary `critical`
  injection open to anyone reaching the port; the revision program named
  this a governance failure, not a risk to accept. Rejected.
- **Do nothing (stay unauthenticated):** the R-3 finding stands — rejected.

## Dissent on record
None recorded against HMAC specifically. The §9 framing noted sender cutover
cost as the real price; the onboarding-mode window in the migration design
exists to pay it without a flag day. If a named sender class proves
HMAC-undeployable inside the window, that is a reversal trigger (below),
not a veto.

## Single-threaded owner
recv-2 (R-3 migration design + sender inventory, per 12H-PLAN F3).
Reviewer with veto: Vault (auth-matrix accuracy). Phase 1 enforcement build
owner to be named at T+12 handoff.

## Reversal conditions
Reopen if: (a) a named sender class cannot deploy HMAC inside the
onboarding-mode window despite good-faith effort — then the decision
re-opens as HMAC-vs-mTLS-vs-accept-risk with that sender class named;
(b) ADR-005's scheme is found broken — then the scheme, not the
authenticate-the-route decision, is replaced. "Senders complain" without
a named undeployable class is not a reversal trigger.

## Traceability
PIPELINE-REVISION.md §9 Q2 · R-3 · ADR-005 · 12H-PLAN §1 (recv-2), §5 (R-3 build → Phase 1).
