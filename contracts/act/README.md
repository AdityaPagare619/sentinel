# C4 — L4 DECIDE-2 → L5 ACT: `Disposition` v0.1

**Contract file:** `disposition.v0.1.json` (JSON Schema, draft-07 — the contract;
this README documents it, it is not the contract).
**Status:** DRAFT v0.1 · vocabulary pinned **`pending-R9-ratification`** (see §1).
**Consumer-owner:** L5 ACT (forwarder side; fwd-2 lane). Consumer owns the contract
(OPERATING-RULES §1.1; principal-governance §2 unforgiving API design).
**Source:** PIPELINE-REVISION.md §2.2/C4; `deterministic-gate.md` P2; R-9.

## 1. The pending-R9 pin (read first)

The `action` / `reason_code` enums below are a **draft vocabulary**, pinned
`pending-R9-ratification` per 12H-PLAN §1 fwd-2 / §2 T+3 (fwd-2 waits on R-9
ratification, Phase 1 D3). R-9 is the TYPE-1 decision on the machine-checked
disposition vocabulary; this draft is the working shape so lanes can build
against it in parallel. **Do not treat this enum as settled:** R-9 may add,
merge, or rename reason codes; that lands as a versioned amendment
(`contract_version` bump for true breaks; additive within v0.1 otherwise —
C7 compat rule). Dated kill condition on the pin: if R-9 is not ratified by
T+9 of the 12-hour wave, fwd-2 escalates to Ledger the same hour
(principal-mindset §6: dated kill conditions, no drift).

## 2. What crosses

`Disposition{contract, contract_version, action, reason_code, reason_detail?, detail?, evidence_refs}`

- `action` — closed enum: `page_now | page_business_hours | suppress | passthrough | folded`.
  `folded` (D3): storm-continuation absorbed into the aggregate page —
  intentionally not forwarded, **not** suppression.
- `reason_code` — closed enum of 18 values: 13 simple
  (`threshold allowlist uncertain shadow dedup change_window storm storm_digest
  model_drift verified_resolve operator_resolve label_pipeline_stale
  commit_watchdog_trip`) + 5 structured variants
  (`error freshness_veto firewall_flagged failopen policy_block`).
  **reason_code NEVER carries interpolated data.** No `error:TimeoutError`,
  no `freshness:leg1|leg2` — those shapes are rejected by the enum (and by the
  schema's `pattern`, and by `validate_fixtures.py`).
- `reason_detail` — the structured payload, selected by `reason_code`:
  - `error` → `error_code` (snake_case, was `f"error:{code}"`; gate.py `_error_code`)
  - `freshness_veto` → `stale_legs[]` (every stale leg named, ADR-014; was
    `"freshness:" + "|".join(fresh_reasons)`)
  - `firewall_flagged` → `detectors[]` (+ optional `evidence[]`; was
    `firewall_flagged:<detectors>`)
  - `failopen` → `step` (1–3), `digest` (bool), optional `error_code`
    (collapses `failopen_step{1,2,3}{,_error,_digest}`)
  - `policy_block` → `policy_reason` (open string; `policy_gate_not_configured`
    today; full vocabulary pending Q3 — Type-1, Aditya's item)
  - simple reasons → `reason_detail` MUST be absent or empty.
- `detail` — optional human occurrence text (RFC 9457 shape: stable
  `reason_code` ≈ `type`/`title`, occurrence-specific `detail`).
- `evidence_refs` — pointers (hashes/URIs) to Jev answers, firewall evidence,
  freshness reports, attestation refs. Pointers, never raw vendor payloads.

Every instance self-identifies on the wire: `"contract": "disposition"`,
`"contract_version": "v0.1"` — explicit versioned wire serialization; the
event log is the audit trail and history is never rewritten (attestor ADR
Type-1 ruling).

## 3. What NEVER crosses (and the mechanism for each "never")

1. **Policy re-interpretation** (PIPELINE-REVISION §2.2/C4). ACT executes the
   disposition; it does not re-decide it. *Mechanism:* `reason_code`/`detail`
   are advisory for routing and audit only; the `action` field is the command.
   Postel's law inverted for an audit boundary: L4 is strict on emit (schema),
   L5 is strict on ingest (validates before acting) — resilient ingest would
   silently admit invented vocabulary.
2. **String-interpolated reasons.** *Mechanism:* closed enum + `pattern`
   `^[a-z][a-z0-9_]*$` (no `:`, no `|`) + negative fixture
   `fixtures/negative_stringly_typed_reason.json` that MUST fail validation.
3. **Machine logic parsing `detail`.** *Mechanism:* `detail` is optional and
   absent from every enum/allOf rule; the qa-2 `tests/contracts/` gate owns a
   test asserting no consumer matches on `detail` (this contract plugs into it).
4. **Vendor payloads / vendor identity.** Raw bytes and source stop at L2
   (C2 anti-corruption); nothing here resurrects them — `evidence_refs` are
   hashes/URIs, never payloads.
5. **Unknown reason codes on the wire.** *Mechanism:* schema rejects them;
   L5 must fail the receipt to dead-letter with `error_class:
   "unknown_reason_code"` rather than guess (fail-open ladder decides the
   paging posture, not silent acceptance).

## 4. Version / compatibility

- Additive inside v0.1 (new reason codes that are simple; new optional fields
  in `reason_detail`); a new version only for true breaks (Google/Azure/Stripe
  consensus, OPERATING-RULES §1.1).
- The vocabulary pin (`pending-R9-ratification`) is itself versioned metadata:
  ratification lands as a schema amendment ADR + `contract_version` note, and
  the pin text is removed — never silently.

## 5. Pre-mortem (top 3 — principal-systems §2)

1. *A lane ships `reason_code: "error_rate_limited"` inline to ship faster.*
   Caught by: schema reject at L5 ingest + the exhaustiveness test (P2) at
   ratification — the compiler/schema, not a comment, owns the taxonomy.
2. *An on-call dashboard starts routing on `detail` text.*
   Caught by: README §3 rule 3 + the qa-2 contract test grepping consumers.
   Fix on detection: andon (OPERATING-RULES §5.3) — parsing human text for
   machine logic is a §5 violation.
3. *R-9 never ratifies and the pin rots into permanence.*
   Caught by: the dated kill condition in §1 (T+9 escalation to Ledger).

## 6. Skill & Evidence (OPERATING-RULES §2.2)

Binding clauses, each with how it binds:
- **principal-governance §2 (unforgiving API design; consumer owns the
  contract):** the enum is closed and versioned; the consumer (L5/fwd-2)
  writes and approves changes — no provider silently widens it.
- **principal-systems software constitution ("explicit versioned boundaries;
  never leak internals"):** the schema is the boundary; internals
  (gate branch names, `_error_code` internals) never cross — only the
  published `reason_code` vocabulary.
- **principal-systems operating rule 3 (label the Type):** the vocabulary is
  Type-1 (R-9, Aditya's Phase-1 item) — hence the explicit
  `pending-R9-ratification` pin rather than an unlabeled draft that would
  default to Type-2 process.
- **principal-mindset §6 (dated kill conditions):** the pin carries a date
  and an escalation path (§1).
- **External source — RFC 9457, Problem Details for HTTP APIs**
  (https://www.rfc-editor.org/rfc/rfc9457, published July 2023; accessed
  2026-10-05): shaped the wire design — stable problem `type`/`title` that
  never changes between occurrences, occurrence-specific `detail`, and
  extension members carrying machine-actionable data. C4 adopts the *shape*
  (stable `reason_code` + occurrence `detail` + structured `reason_detail`
  fields) without the URI machinery, which an internal boundary doesn't need.
  This is what killed alternative (c) below.
- **Repo prior art — `deterministic-gate.md` P2 exhaustiveness rule:**
  every reason the kernel, fail-open ladder, and structural paths can emit is
  enumerated here (inventory taken from gate.py, failopen.py, firewall.py,
  storm_digest.py, correlator.py, policy_lifecycle.py — see Verification).

Alternatives considered and rejected:
- (a) `oneOf` discriminated union, one schema per reason (17 branches).
  Rejected: equivalent expressiveness, hostile to stdlib-only validation,
  harder for the event-log consumer to read.
- (b) free-string `reason` + regex lint. Rejected: unenforceable
  cross-process; R-9 demands the taxonomy be owned by the schema, not a lint.
- (c) RFC 9457 verbatim (`type` as URI + `title`). Rejected: internal
  boundary, no resolver; keep the shape, drop the URIs.

Tool evidence: `contracts/validate_fixtures.py` (stdlib `json` only) —
3 validating fixtures + 1 negative fixture; run output in the PR body.
Inventory commands: `grep -rn 'reason=' src/sentinel/{gate,failopen,firewall,correlator,storm_digest}.py`
+ `policy_lifecycle.py` policy_gate reasons (all read, none modified).

Consumer-owner: L5 ACT (fwd-2). Expected reviewers: Ledger (C4 per 12H-PLAN)
+ Tripwire (full adversarial, per 12H-PLAN F6).
