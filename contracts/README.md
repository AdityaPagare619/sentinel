# `contracts/` — versioned inter-stage contracts (PIPELINE-REVISION §2.2)

Machine-checkable artifacts, not prose: each contract is a JSON Schema file
(draft 2020-12, `$id`-versioned) plus a README stating what crosses, what
*never* crosses, the version, the stability, and the consumer-owner.
Prose describes the contract; the schema *is* the contract
(OPERATING-RULES §1.1).

## File map

| Contract | Boundary | Files |
|---|---|---|
| C1 | L1 INGEST → L2 NORMALIZE | `ingress/raw-inbound.v0.1.json`, `ingress/pd-trigger.v0.1.json`, `ingress/generic-webhook.v0.1.json`, `ingress/README.md` |
| C2 | L2 NORMALIZE → L3 DECIDE-1 | `decide/alert.v0.1.json`, `decide/README.md` |
| C3 | L3 DECIDE-1 → L4 DECIDE-2 | `decide/triage-result.v0.1.json`, `decide/README.md` |

Fixtures live under `<dir>/fixtures/<schema-name>/*.json` (≥2 per schema).
`validate_contracts.py` (stdlib only — no pip deps) validates every fixture
against its schema and sanity-checks schema metadata:

```
python3 contracts/validate_contracts.py contracts
```

## Status

All three contracts are `v0.1`, `stability: draft` (12H-PLAN F2 — the F2
verdict keeps C2/C3 draft until the eval harness R-14 exists; C1 is draft
until the pre-T+0 prep wave's T+12 freeze review). Additive changes inside
a version; a new schema-file version only for true breaks (field
renames/removals, meaning changes). A field never changes meaning under
the same name. Consumer owns the contract (see each README's ownership
line); contracts freeze at lane start — mid-lane changes need a written
contract-amendment ADR + same-hour re-announcement to consuming lanes
(OPERATING-RULES §1.1).

## What this lane did NOT do

- No `src/` changes, no test edits, no behavior changes (pre-T+0 rule).
- C4–C7 are other prep lanes' drafts; this lane defines only the
  C3→C4 and C2→C5/C1→C5 open interfaces (documented in the READMEs).
- `prior_disposition`'s vocabulary is C4's (pending-R9-ratification) —
  carried opaquely here, never pre-empted.
