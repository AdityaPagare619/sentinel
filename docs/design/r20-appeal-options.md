# R-20 / Q8: The appeal control's future — both-options design doc

**Status:** DRAFT · **Gate:** ✅ **Q8 RESOLVED 2026-10-05 ~20:25 IST —
Aditya released full authority; the verdict is WIRE the appeal as an
audit-logged override.** Recorded as: option (a) is the decided direction;
the REMOVE fallback no longer applies. No wiring work starts until the
12h wave's lanes own it (this doc is the build-ready input).
**Date:** 2026-10-05 IST · **Lane:** prep-ui-qa (doc only, pre-T+0 prep)
**Type:** supports a **Type-1** decision — a new write action on the platform API
(public-API schema + security posture change) is irreversible; removal is Type-2.
**Binding skills:** principal-systems (Type 1 vs Type 2, Op-rule 3; five-whys);
principal-governance (unforgiving API design, §2); execution-doctrine (honesty
before polish — no fake demos); principal-mindset §11 (anti-theater: a practice
that never says no is theater).
**Reviewers:** Prism + Vault (12H-PLAN §1, ui-1 row; Vault sign-off mandatory on
any write-surface change per OPERATING-RULES §1.6).
**Dependency:** option (a)'s endpoint shape waits on plat-1; both options are
drafted here against the contract stub (12H-PLAN §2, ui-1 row).

---

## The finding (restated from `prism-ui.md` A4/P1)

"Page me anyway" renders on every decision drawer. In **all** DATA_MODEs —
including production `live` — the click handler replaces the button with the
text "demo build: no page sent" (`app.js` `openDrawer`, `components.js:362-365`).
The constitution's §8.9 is unambiguous: *"if it's rendered, it works; if it
doesn't work, it isn't rendered."* An operator at 3 AM may believe an appeal
acted when nothing happened. The pre-mortem in `prism-ui.md` names this the
most likely vector by which the console contributes to an incident. The
"Demo build" note is additionally **false** in the production build.

**Why this is Q8:** wiring the control creates a new write action on the
platform API — an audit-logged override that reaches the real paging path.
That is a public-API-schema change with a security posture (a new authenticated
write surface on the paging product). Per principal-systems Op-rule 3, a Type-1
decision of this shape belongs to Aditya. The design below makes both options
build-ready so the verdict converts directly into a lane spec.

---

## Option (a) — wire the appeal as an audit-logged override

**Shape.** A new write action on the platform API:

- `POST /api/v1/appeals` — body: `{ decision_id, team, operator, idempotency_key,
  reason? }`. Auth: the plat-2 bearer-token design (R-16); Vault must sign off
  the auth matrix for this route (auth change ⇒ Vault trigger, OPERATING-RULES
  §4.3).
- On receipt the platform writes an **override event to the sealed audit chain**
  (Ledger's chain — the same tamper-evidence machinery Vault reviews), then
  enqueues the page through the **real paging path** (forwarder, not a bypass).
- Response returns the audit anchor (chain index / hash), so the UI can render
  proof the appeal acted — the 3 AM operator sees *where* the page went, not
  just that the button was clicked.
- **Idempotency by default** (principal-governance §2): the idempotency key makes
  a dropped-packet retry page exactly once. A double-tap on a touch screen must
  not double-page the on-call.
- **Rate limits per operator + team** (fail-open ladder context): an appeal is
  an override of the suppression policy — abuse shape is an operator (or a
  compromised session) paging the team repeatedly. The platform enforces the
  limit; the UI renders the limit state in-band (honest absence of capability
  must be explicit, per the P2 companion discipline).
- The UI renders the control **only** when the endpoint binding exists in the
  build (contract-driven visibility — `contract.js` style): in builds/modes
  without the binding, the control does not render. This is the §8.9 fix as
  mechanism, asserted in CI by the extended `antislop.py` phantom scan.

**P5 read-path law preserved.** INV-5 (zero Jev/model-evaluation calls on read
paths) stays intact because the appeal is a declared **write** action, not a
read-path side effect. No GET ever triggers a page.

**Verify (when built post-T+0):** in the `live` build against a mock pager,
clicking appeal produces (1) a page on the configured paging path and (2) an
override event on the sealed audit chain — asserted in CI (R-20's verifier in
PIPELINE-REVISION §6).

**Risk paragraph.** A new authenticated write surface is a new attack surface:
the auth design must be right (R-16) and the rate-limit numbers must be tuned
against the forwarder's real capacity, or a misconfigured limit either
strangles a genuine 3 AM appeal or turns the control into a paging-amplifier.
The override writes to the sealed chain, which makes appeal metadata (who
overrode, when, why) part of the tamper-evident record — that is intended, but
it means the threat model for the chain (R-18) must explicitly cover appeal
events. If plat-1's C7 shape or plat-2's auth design slips, option (a) cannot
land cleanly in Phase 5 — it must not be built on an unshaped contract.
**Author-written risk:** 60% confidence the Phase-5 wiring is routine *given*
C7 and R-16 land first; 30% that Vault finds an auth edge the design misses;
the remaining risk is operational — rate-limit tuning without production data.

---

## Option (b) — remove the control from the live build

**Shape.** The button is removed from the drawer in the `live` build (and in
any mode whose backend lacks the appeal binding). The drawer's "dispute this
suppression" **audit link stays** — the operator's recourse becomes the audit
trail, not a phantom action. `antislop.py`'s phantom scan asserts the control
does not render when the binding is absent.

**Why it is honest.** §8.9's fix has two compliant states: working or absent.
Removal is the state that requires no new API surface, no Vault sign-off, no
threat-model amendment, and no rate-limit tuning. The honesty law
(OPERATING-RULES §3.4) prefers removing a capability over shipping a simulated
one — the honesty default named in 12H-PLAN §5.

**Verify:** in the `live` build, the drawer contains no appeal control
(phantom-scan assertion); the dispute/audit link renders and resolves to the
decision's chain position.

**Risk paragraph.** Removal closes an operator recourse path: at 3 AM an
operator who believes a suppression is wrong has no one-click path to force a
page — they must go through the normal paging path manually (phone, PagerDuty
console), which is slower and loses the audit linkage to the suppression
decision. The fail-open direction (§7 of PIPELINE-REVISION) mitigates the worst
case — genuine unknowns page rather than suppress — but the appeal existed for
the operator's judgment, and removing it removes that judgment's fastest
instrument. If Q8 lands as "remove," the Phase-5 lane must also write the
runbook sentence: *"there is no in-console appeal; to override a suppression,
page via [path] and the audit trail links your manual page to the decision."*
Honest absence is a feature; silent absence is a new gap.

---

## Rejected alternatives (with reasons)

1. **Keep the control as-is with the "demo build" note.** Rejected — the note
   is false in production; the control is phantom interactivity on a safety
   control. Banned by the honesty law (OPERATING-RULES §3.4: no fake demos) and
   by §8.9. This is not an option on the table; it is the bug.
2. **Wire the appeal to page without the audit-logged override.** Rejected —
   an appeal is an override of a policy decision. R-1's finding is that the
   policy lifecycle is already unattested; a new unattested override path
   would repeat the finding in the name of fixing it. If it pages, it is
   audited — no exceptions.
3. **Confirm-only modal that never reaches the platform.** Rejected — theater.
   A confirmation dialog that terminates client-side is option (a)'s UI with
   option (b)'s honesty subtracted. principal-mindset §11: a control that
   never acts is decoration; an acknowledged decoration is worse than removal.
4. **Hide the button behind a feature flag with no wiring plan.** Rejected —
   flags are for dark launches (principal-governance §4), not for parking
   phantom controls. A flag without a wiring decision is option 1 with an
   extra step.

---

## Decision record (deputy authority — Q8 verdict 2026-10-05 ~20:25 IST)

**VERDICT: WIRE (option a).** Aditya's full-authority release decided Q8 as:
page-me-anyway wired as an audit-logged override → real paging path. This doc
is now the build-ready input for the Phase-5/12h-wave wiring lane; option (b)
(removal) is superseded as a direction but kept in-record as the rejected
alternative it formally is. What remains unacceptable, per the honesty law:
any state where the control renders and cannot act.

**Rationale for the verdict (on record):** wire preserves operator agency
with the audit trail intact, and the P5 read-path law survives by
construction (appeal is a declared write action, not a read-path side
effect). Removal stays honest and cheap as a contingency if wiring hits
auth/threat-model blockers — it is not abandoned, just demoted to fallback.

**Pre-mortem (one year out, this decision contributed to an incident):**
option (a) built with a wrong rate limit lets a fat-fingered operator page the
whole team every 10 seconds for an hour — the paging equivalent of a retry
storm; mitigation is the per-team limit with a loud UI state. Option (b)
chosen, then a genuine suppression miss at 3 AM, and the postmortem asks why
the fastest override path was deleted — mitigation is the written runbook
sentence and the linked audit trail.
