# Decision: Vault never-crosses secrets rule attested for C4/C6

**Date:** 2026-10-05 IST · **Type:** Type-2 (attestation + mechanical guards; additive, no schema break)
**Lane:** follow-up to #87 contract surgery (which flagged this as out of scope)
**Reviewers:** Vault (security), Ledger (contracts/audit), Tripwire (adversarial)
**Status:** ATTESTED — rule is written into both contracts' "What NEVER crosses" sections, not assumed.

## Context

Vault's rule: secrets must NEVER cross contract boundaries in plaintext —
key references, not key material; hashes, not raw identifiers. The #87
surgery left this unattested for C4 (Disposition) and C6 (ForwardReceipt).
This record closes it: a field-by-field audit of both schemas against every
producer that populates them, with file:line evidence.

## Audit verdict: CLEAN on the schemas; ONE producer/doc gap found and fixed

No C4/C6 field carries, or can be made to carry, raw secret material.
The only key-adjacent shapes on either contract are:

- `routing_key_ref` — a vault-path **reference** (`secret:pd/routing_key`,
  `secret:pd/control_routing_key`), resolved env-side at send time. A
  reference is not material; it cannot be spent, replayed, or decrypted.
  (It crosses the outbox row and the spill record, not C4/C6 themselves —
  neither schema has a key-bearing field.)
- `wire_sha256` (C6) — one-way hash of the exact POST bytes (which do carry
  the key by PagerDuty's own Events API v2 design: auth IS the key in the
  body). sha256 preimage resistance means the hash cannot recover the key;
  it exists to prove what went on the wire, not to transport it.
- `spill_id` (C6) — one-way hash of spill-file bytes that themselves carry
  only the ref, hashes, and metadata (forwarder.py:806-823).

## Field-by-field evidence

### C4 Disposition (`contracts/act/disposition.v0.1.json`)

| Field | Verdict | Evidence |
|---|---|---|
| `contract`, `contract_version` | clean (constants) | schema |
| `action` | clean (closed enum) | schema |
| `reason_code` | clean (closed enum + `^[a-z][a-z0-9_]*$`; no interpolated data) | schema; README §3.2 |
| `reason_detail.error_code` | clean (snake_case pattern) | schema |
| `reason_detail.stale_legs[]` | clean shape; producer rule only | free-form leg names; fixture shows `["lock1_calibration: 26h old (ttl 24h)", …]` |
| `reason_detail.detectors[]` | clean (fixed catalog) | firewall.py:383-408 detector names |
| `reason_detail.evidence[]` | clean — cannot carry the key | firewall.py `_evidence` truncates to 80 chars (firewall.py:76) of *screened alert fields* (title/service/check/labels, firewall.py:68); alerts never contain the routing key — ingress strips it at the edge (receiver.py:485: key read for presence/auth, never copied into `Alert`) |
| `reason_detail.step`, `.digest` | clean (int/bool) | schema |
| `reason_detail.policy_reason` | clean (snake_case pattern) | schema |
| `detail` | clean shape (human text ≤2000); producer rule only | no producer exists yet (draft contract); failure channels run through `sanitize_error` (integrations.py:259) |
| `evidence_refs` | clean (hashes/URIs) | no producer yet; rule: never a credential-bearing URI |

### C6 ForwardReceipt (`contracts/observe/forward-receipt.v0.1.json`)

| Field | Verdict | Evidence |
|---|---|---|
| `decision_id`, `attempt_no`, `outcome`, `simulated`, `channel`, `error_class` | clean | schema; none can hold a key |
| `wire_sha256` | clean (one-way commitment) | hashed at pd_sender.py:338 from the exact POST bytes; `_post` strips Authorization headers (pd_sender.py:324-328) and never logs bodies; the 64-hex value is asserted in `test_nevercrosses_key_hygiene.py::test_wire_sha256_never_embeds_key` |
| `spill_id` | clean (one-way hash of keyless bytes) | spill records carry `routing_key_ref` (ref), `payload_sha256`, metadata only (forwarder.py:806-823); simulated-path spill carries `simulated:true` + refs (forwarder.py:771-778) |

## Producer evidence (what populates the fields)

- `resolve_routing_key` (pd_sender.py:130-155): env-only resolution;
  `SecretMissing` never carries the value (docstring + constructor).
- `build_wire_event` (pd_sender.py:173-186): raises `PayloadNotKeyless` if
  the frozen payload contains `routing_key` — the keyless-disk rule is
  mechanically enforced at send time, so a key can never be frozen to disk
  and later ride a hash/spill/receipt.
- `_pinned_key` (forwarder.py:726-735): pins the *resolved* key in a
  process-lifetime in-memory dict (outbox_id → key). Memory-only; never
  written to receipts, bodies, logs, or disk. (Noted, not changed: lifetime
  matches the env vars it mirrors, and clearing it would break the
  retry-across-rotation semantics in design §4.3.)
- `send_direct` degraded path: real sends resolve the key and POST it (PD
  design); the spill record carries only the ref + hashes
  (forwarder.py:806-823). Simulated sends never resolve (see fix below).
- Ingress edge: `routing_key` is extracted from the inbound PD event body
  (receiver.py:485) for presence/auth and is never copied into the
  normalized `Alert` — so no downstream field (firewall evidence, detail,
  labels) can smuggle it.
- Failure channels: `sanitize_error` (integrations.py:259) strips the exact
  configured key VALUES from error strings on every forward failure path —
  covering `error`/`error_class`/`last_error` crossing C6.

## The one gap found (fixed, not waived)

The C6 contract claimed the simulated path "never resolves a routing key;
the key value is deliberately never materialized" — but both simulated
code paths resolved-then-discarded it:

- `forwarder.py` (was) `_k, _src = resolve_paging_key()` in the degraded
  simulated branch — `_k` bound then unused;
- legacy `_simulated_send` (was) `_key, key_source = self._resolve_key()`.

No value crossed any boundary (locals were discarded; log lines printed
only the source), but the code contradicted the contract. Fixed:

- New `paging_key_source()` (integrations.py:219) — reports user/env/
  unconfigured without returning the value; simulated paths use it
  (forwarder.py:765, forwarder.py:1048).
- Mechanical proof: `tests/test_nevercrosses_key_hygiene.py` rigs
  `resolve_paging_key` to raise and runs both simulated paths; they pass
  and a canary key is asserted absent from stderr, the spill file, and all
  returned structures. 9 tests, all passing.

## What changed in this lane

1. `src/sentinel/integrations.py` — `paging_key_source()` helper.
2. `src/sentinel/forwarder.py` — both simulated paths report source only.
3. `tests/test_nevercrosses_key_hygiene.py` — 9-test battery (canary-based
   never-resolves proofs, PayloadNotKeyless guard, sanitize_error,
   wire-hash non-embedding, C4/C6 fixture structural guard).
4. `contracts/act/README.md` §3 + `contracts/observe/README-c6.md` §3 —
   rule 6 in each "What NEVER crosses" section: the secrets rule, with its
   mechanism (schema shape + mechanical tests).
5. This decision record.

## Skill & Evidence

- **principal-systems software constitution** ("explicit versioned
  boundaries; never leak internals"): the secrets rule is now stated
  normatively on the contract boundary, with mechanical enforcement — not
  prose in a module docstring.
- **principal-systems law 2 (eternal friction)**: the
  resolve-then-discard discrepancy was fixed at the producer, because a
  tired future maintainer reading the doc claim would believe something
  the code didn't do.
- **principal-mindset §6 (dated kill conditions)**: the fixture
  structural guard fails the build on regression — the rule is a test,
  not a hope.
- Prior art: pd_sender.py module docstring (Vault mandate: "secrets never
  touch disk, logs, audit rows, or error messages"); PR #77 review
  (deleted zero-caller redact helpers, replaced with the wired-in
  `sanitize_error`).
- Tool evidence: `python3 -m unittest tests.test_nevercrosses_key_hygiene`
  → 9 OK; `tests.test_integrations tests.test_forwarder` → 49 OK;
  `tests.test_durable_forwarder` → 33 OK.
