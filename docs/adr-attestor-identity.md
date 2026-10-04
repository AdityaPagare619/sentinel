# ADR: Attestor Identity — the unnamed trusted party (ADR-013/014/022)

**Status:** DECIDED by governance panel, 2026-10-04.
**Type:** 1 — identity is the trust root. Changing the scheme later invalidates every
historical attestation; getting it wrong silently voids the two-person rule.
**Panel:** Forge (engineering), Vault (security/trust), Pager (SRE domain), Tripwire
(adversarial QA). Cross-item note 2 of the 2026-10-03 adjudication: *"Attestor identity
is now load-bearing (ADR-013 dual attestation, ADR-014 proofs, ADR-022 two-person rule).
Attestor onboarding/offboarding/compromise response is an unnamed trusted party across
all three."*
**Process:** disagree-and-commit. Real disagreement recorded below; all four commit.

## The problem (stated plainly)

`PolicyAttestation.attestor_ids` is a tuple of **bare strings**. Anyone with write access
to the policy-state JSON file can mint an attestation as anyone — the two-person rule
(ADR-022), the dual-attestation gate (ADR-013), and the proof chain (ADR-014) all rest on
string equality. There is no onboarding, no offboarding, no revocation, and no compromise
response. The emperor has no keypair.

## 1. Position memos

### Forge (engineering)

- Identity verification must be **cryptographic, not string comparison**. Attestations are
  Ed25519 signatures over canonical bytes; the registry holds public keys; verification
  is signature-check. A JSON registry file (`attestors.json`) is enough — no new service:
  `{id, public_key_hex, algorithm, status, added_at, added_by, revoked_at, revoke_reason}`.
- Execution doctrine: **manual first**. The registry is edited via a CLI ceremony with
  two-person co-signing, not a web UI nobody asked for. The 10-line ceremony beats the
  identity microservice.
- Eternal friction: keys get lost, people leave, laptops die. Onboarding MUST include a
  key-rotation path or the first lost laptop becomes a permanent incident.
- The dual-attestation validation path is `PolicyStore.transition()` + `unfreeze()`. The
  registry check goes there — one place, not five.

### Vault (security/trust)

- Threat model: a compromised attestor key + one colluding attestor = malicious policy
  (e.g. suppress-everything) promoted with a straight face. Therefore compromise response
  must be: **(1)** revoke in the registry — a SINGLE-operator action, no two-person
  ceremony for revoke (revocation is fail-safe; it must be fast); **(2)** quarantine every
  live version the revoked attestor helped promote; **(3)** page the owner.
- Revocation is a **one-way latch**: revoked → active is forbidden. Re-onboarding is a NEW
  key id. (Un-revoke is how incidents recur.)
- **Disagreement (recorded):** Vault wanted *retroactive invalidation* — versions promoted
  by a revoked attestor become void ab initio. Forge refused: rewriting history breaks the
  audit trail, and the audit trail is the product. **Resolution (Pager adjudicating):**
  quarantine via the existing watchdog **freeze** mechanism — the version is frozen, new
  dispositions fall through to page, history stays intact. Fail toward paging WITHOUT
  rewriting the past. Vault commits to freeze-quarantine; Forge commits to building the
  quarantine sweep (not just the transition-time check).

### Pager (SRE domain)

- True objective: "a bad actor cannot silently buy suppression." The ceremony that matters
  is the one that happens at 3 AM during a compromise — so revoke must be one command,
  one operator, immediate effect.
- Onboarding is a two-person ceremony (2 active attestors co-sign the new key), recorded
  in an append-only ceremony log chained like the event log. Bootstrap: the founder
  ceremony seeds the initial quorum (recorded, audited, never repeated silently).
- Propagation time: the registry is read **fresh on every enforcement decision** — the
  same pattern the PolicyGate already uses for the policy-state file (never a boot cache).
  Revocation is effective on the next decision. A cached registry is a revocation-delay
  vulnerability; that would be a Type-1 failure wearing a Type-2 implementation.
- Rotation: quarterly key-rotation drill, same ritual family as the ADR-018 drill.

### Tripwire (adversarial QA)

- **Gap found (real):** attestations bind `policy_id + version + from/to` but NOT the
  policy `content_hash`. An attestation for v3's benign content replays cleanly onto v3's
  malicious content after a draft swap. The signature MUST cover `content_hash`.
  (Monkey-first: this was the hardest part of the design — the replay binding.)
- Adversarial cases: (1) attacker adds their own key → onboarding needs 2 co-signers;
  (2) attestation replayed for a different transition → signature covers
  `(policy_id, version, content_hash, from_state, to_state, decided_at)`;
  (3) `decided_at` clock games → freshness window vs the transition call's `now`
  (7-day max age, 5-min future tolerance); (4) registry file tampered → the registry is
  HMAC-sealed with a root key; seal failure = registry unusable = **fail toward paging**;
  (5) the ceremony log forked → hash-chained, verified on load.
- Kill condition: if per-call registry reads ever breach the hot-path latency budget,
  measure first — the read is a few-KB JSON parse against an 800 ms Jev call; the
  bulkhead rationale (paging path independent of event-log availability) already
  justifies it.

## 2. The decision

- **Identity = Ed25519 public key in a sealed registry.** `attestors.json` (format v1):
  attestor records + HMAC-SHA256 seal (root key from `SENTINEL_ATTESTOR_ROOT_KEY`).
  Seal failure ⇒ registry unusable ⇒ transitions raise ⇒ gate fails toward paging.
- **Who mints / how verified:** attestors sign canonical bytes
  `(policy_id, version, content_hash, from_state, to_state, decided_at)` with their
  private key. `transition()` and `unfreeze()` verify: signature valid against the
  registry pubkey; attestor status `active`; `content_hash` equals the version's
  content hash (NEW binding — closes the replay gap); `decided_at` within the freshness
  window; attestors distinct and distinct from the author (existing checks kept).
- **Onboarding:** 2 active attestors co-sign `(new_id, public_key, added_at)`; recorded
  in the chained ceremony log. Founder bootstrap seeds the initial quorum (recorded as such).
- **Offboarding / compromise:** single-operator `revoke(id, reason)` — immediate,
  one-way latch, recorded in the ceremony log. Quarterly rotation drill.
- **Revocation propagation:** registry loaded fresh per enforcement read; effective on
  the next decision (< 1 decision latency).
- **In-flight suppressions:** history is never rewritten. `quarantine_revoked(store, id)`
  freezes every LIVE/REVIEW_DUE version whose promotion attestations include the revoked
  attestor and emits page events — dispositions fall through to page via the existing
  `can_suppress` → gate hook. New transitions citing the revoked key are rejected.
- **Fail-toward-paging chain (tested):** revoked/unknown attestor ⇒ `transition()` raises
  ⇒ version never reaches LIVE ⇒ `PolicyGate.can_suppress` → `(False, …)` ⇒ the gate
  converts suppress → passthrough ⇒ the human gets paged.

## 3. Type labels

| Item | Type | Why |
|---|---|---|
| Signature scheme (Ed25519) + registry format v1 + one-way revocation latch + content-hash binding | **1** | the trust root; rotation changes history's meaning |
| Fresh-read-per-decision propagation (no registry cache) | **1** | a cache is a revocation-delay vulnerability |
| Freeze-quarantine (not history rewrite) on compromise | **1** | the audit trail is the product |
| Registry-less (string-ID) mode deprecated | **1** | it does not satisfy the two-person rule's trust basis |
| Key algorithm migration path (registry carries `algorithm`) | 2 | named migration, not a rewrite |
| Quarterly rotation drill cadence; 7-day attestation freshness | 2 | operational tunables |
| Ceremony transport (CLI now, IdP-backed later) | 2 | the signatures are the trust, not the tool |

## 4. Alternatives rejected

1. **Keep string IDs + file permissions** — the registry file lives on the same host as
   the engine; permissions are not identity. Rejected.
2. **External IdP (OIDC) on the hot path** — third-party dependency on the suppression
   path (done-checklist: IdP down 4 hours — what happens?). Rejected for the hot path;
   acceptable later as the onboarding ceremony's human auth.
3. **Retroactive version invalidation** — rewrites history; breaks the audit trail.
   Rejected in favor of freeze-quarantine (the disagreement above).
4. **HMAC-shared-secret attestations** — symmetric: any attestor could forge another's.
   Non-repudiation between attestors requires asymmetric. Rejected.

## 5. What would change it

- A customer requirement for HSM-backed keys → the registry holds key *references*
  (`algorithm: hsm-ref`), format already carries the field.
- Measured evidence that per-call registry reads breach the latency budget (measure
  first; the budget is 800 ms and the read is sub-ms).
- An auditor requiring the onboarding ceremony in the event-log chain itself → an 8th
  event type via full ADR (the Type-1 vocabulary is frozen; this would be a real one).

## 6. Implementation (this lane)

- `src/sentinel/attestor.py`: `AttestorRegistry` (sealed JSON, ceremony log,
  onboard/revoke with one-way latch), `generate_keypair`, `sign_attestation`,
  `verify_attestation`, `quarantine_revoked`.
- `src/sentinel/policy_lifecycle.py`: `PolicyAttestation` gains `signatures`
  (attestor_id → hex, backward-compatible); `PolicyStore` accepts
  `attestor_registry` (path or object); `transition()`/`unfreeze()` verify
  signatures + active status + content-hash binding + freshness when a registry is
  configured. Registry-less mode keeps the legacy structural checks with a loud
  once-per-process deprecation warning.
- `tests/test_attestor.py`: 16 tests incl. revoked ⇒ transition raises ⇒
  `can_suppress` False ⇒ gate pages; replay-across-content rejected; re-onboard of a
  revoked id forbidden; tampered seal ⇒ fail toward paging; onboarding needs 2
  co-signers.

Built in this lane; PR targets `main`, **not merged** per lane rules.
