# `tests/contracts/` — fixture convention (qa-2 skeleton)

**Status:** skeleton (pre-T+0 prep) · **Date:** 2026-10-05 IST · **Lane:** prep-ui-qa
**Binding skills:** principal-systems software constitution ("explicit versioned
boundaries" — a contract is a machine-checkable artifact, OPERATING-RULES
§1.1); principal-governance §2 (unforgiving API design — contracts are
versioned, never silently widened); OPERATING-RULES §3.3 (contract/schema
tests for every inter-stage contract — checked-in JSON Schemas, fixtures
validated against them, CI fails when code and schema disagree).
**Why a skeleton first (F5 verdict):** without one convention, seven domains
invent seven validators and the §3.3 contract-test gate dies on arrival.
Forced probability 60% the skeleton survives first contact (12H-PLAN F5).

## Layout

```
tests/contracts/
├── validate.py          # stdlib-only minimal JSON-Schema validator (no pip deps)
├── README.md            # this file: the convention
├── schemas/
│   └── <contract-id>.contract.schema.json
└── fixtures/
    ├── <contract-id>.valid.json              # MUST validate (happy path)
    └── <contract-id>.invalid-<why>.json      # MUST fail (one per failure mode)
```

## Naming rules

- `<contract-id>` is the 12H-PLAN contract id: `c1` … `c7`
  (C1 ingress, C2 Alert, C3 TriageResult, C4 Disposition, C5 trace envelope,
  C6 ForwardReceipt, C7 platform read contract). Files are
  `<id>.contract.schema.json` / `<id>.valid.json` /
  `<id>.invalid-<why>.json`.
- Schema files are Draft 2020-12-shaped JSON and must pass
  `validate.py --check` (i.e. `check_schema`): only the supported keyword
  subset. Anything outside it is a **SchemaError at check time**, never a
  silent gap.
- Every contract ships **≥1 valid + ≥1 invalid fixture per failure mode the
  contract exists to prevent**. The invalid fixtures are the interesting half:
  they encode the failure the schema was written to catch (cf. C6's
  `simulated: true` propagation — the fixture that forgets the flag must fail).

## How C1–C7 plug in (T+6 per 12H-PLAN)

1. The contract owner (consumer-owned per OPERATING-RULES §1.1) drops
   `schemas/<id>.contract.schema.json` plus fixtures under `fixtures/`.
2. The owner runs the check locally:
   `python3 tests/contracts/validate.py schemas/<id>.contract.schema.json
   fixtures/<id>.valid.json fixtures/<id>.invalid-*.json`
   — the valid fixture must print VALID, every invalid fixture INVALID, and
   the schema itself must pass `check_schema` (no unsupported keywords).
3. CI (wired by T+12) runs the same over every schema × its fixtures and
   fails when (a) a schema uses an unsupported keyword, (b) a `.valid`
   fixture fails, or (c) an `.invalid-*` fixture passes — the receiver R-6
   pattern (CI fails when code and schema disagree).

## Versioning

Additive changes inside a version (new optional properties); a new version
only for true breaks (OPERATING-RULES §1.1 — Google/Azure/Stripe consensus).
Schema files carry their version in the filename or a top-level
`"$comment"`. The consumer owns the contract and must approve changes —
no provider silently widens a contract.

## Example pair (proves the convention works)

`schemas/example.contract.schema.json` + `fixtures/example.valid.json` +
`fixtures/example.invalid-*.json` exercise the whole loop: valid instance,
an enum violation, and a forbidden additional property. Run:

```
python3 tests/contracts/validate.py \
  tests/contracts/schemas/example.contract.schema.json \
  tests/contracts/fixtures/example.valid.json \
  tests/contracts/fixtures/example.invalid-action.json \
  tests/contracts/fixtures/example.invalid-extra-field.json
```

Expected: one VALID, two INVALID with paths and reasons.
