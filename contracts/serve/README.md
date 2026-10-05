# C7 — L6 OBSERVE → L7 SERVE (read contract)

**Contract:** `api-v1.v0.1.yaml` (OpenAPI 3.0.3 seed) · **Version:** 0.1 ·
**Status:** DRAFT (prep, not frozen)
**Consumer-owner:** L7 SERVE (Prism UI + platform API consumers; Prism+Forge per OPERATING-RULES §1.6)
**Producer:** L6 OBSERVE (platform read tier over the event log)
**12H-PLAN task:** plat-1 · **Reviewers:** Prism+Forge (platform/UI), Ledger consulted on the audit-chain shape

## What crosses

The versioned read contract — every read path under `/api/v1`:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/decisions` | River feed / audit search; adds `trace_id` filter (the C5 one-lookup pivot) and opaque `cursor` pagination |
| GET | `/api/v1/decision/{id}` | Full decision record (`DecisionDetail` gains `audit.trace_id` + `audit.stage_latencies`) |
| GET | `/api/v1/calibration` | Reliability bins, ECE, coverage, flip rate |
| POST | `/api/v1/simulate` | Threshold projection — pure recompute, never writes/pages/calls Jev |
| GET | `/api/v1/analytics/noise` | Top checks, team load, suppression breakdown |
| GET | `/api/v1/analytics/flips` | Flip-audit records |
| SSE | `/api/v1/stream` | New decisions as written (DecisionSummary v1 shape incl. trace_id) |
| GET | `/api/v1/audit/chain` | **NEW** — platform-sealed chain: events with `row_hash`/`prev_hash` + checkpoint HMACs (event-log-audit.md P5). The console verifies the platform's seals instead of deriving links; the panel label flips "derived" → "platform-sealed" |
| POST | `/api/v1/integrations/test-page` | Write surface, documented here for the first time; requires `Idempotency-Key` (exactly-once retries) |

**`DecisionSummary` gains the freshness/policy fields** the UI's honesty
machinery currently labels as absent (prism-ui.md P2/A6): `trace_id`,
`freshness_state` / `freshness_reason` / `freshness_as_of` (R1),
`policy_version` (R6; INV-3 flips from "honest absence" to presence
assertion once served).

**Deprecated aliases:** the seven old unversioned `/api/*` paths stay live
as aliases with **byte-identical v1.0.0 payloads** and every response
carrying:

```
Deprecation: @<unix-seconds>        # RFC 9745 — structured-field date
Sunset: <HTTP-date>                 # RFC 8594 — never earlier than Deprecation
Link: </api/v1/...>; rel="successor-version", <migration-doc>; rel="deprecation"
```

## What NEVER crosses

- **Writes disguised as reads.** No read endpoint mutates, fires a page, or
  calls Jev (the contract's standing guarantee, preserved from v1.0.0).
  The only write in this file is the explicitly-tagged `write` surface.
- **SQLite rowids.** Cursors are opaque (zero data-model leakage —
  PIPELINE-REVISION §2.2 cross-cutting). Filters never reach into engine
  JSON paths (platform-api.md P2-6/M7).
- **Another layer's database.** The platform tier stays read-only
  (`mode=ro`); the event log owns decisions (SSOT).
- **Unversioned new surface.** New endpoints land under `/api/v1` only;
  the deprecated aliases are frozen at the v1.0.0 shape forever.

## Version

`info.version: 1.0.0` — this seed targets the v1 line. Versioning policy
(additive inside a version; new version only for true breaks) is recorded
in `platform/contracts/README.md` ("Versioning policy (C7, v0.1)") and
summarized here:

- **Safe inside v1:** new optional response fields, new endpoints, wider
  enums, looser validation. Server-first deploys; old clients ignore
  unknown fields (the README's forward-compatibility rule).
- **True breaks (new major version, side-by-side deploy):** rename, removal,
  retype, new required parameter. Google Cloud Endpoints guidance: deploy
  both versions from one backend.
- **Deprecation mechanics:** RFC 9745 `Deprecation` + RFC 8594 `Sunset` on
  every alias response; `Sunset` ≥ `Deprecation`; retirement announced in a
  published timeline (principal-governance: unforgiving API design).
- **Calendar dates:** `x-deprecation-date` / `x-sunset-date` are `TBD` in
  this seed — set at freeze, not invented in prep. An example value is shown
  in the YAML; the real dates are an open item below.

## Machine-checkable

```bash
python3 contracts/serve/validate.py   # stdlib only
```

Checks: both fixtures against the DecisionSummary/ChainResponse shape
(embedded subset — kept in sync with the YAML; qa-2's `tests/contracts/`
skeleton graduates this to full OpenAPI validation), all 9 v1 paths
present, all 7 deprecated aliases marked `deprecated: true` with
Deprecation/Sunset/Link documented, and no stray POST outside the v1
write surface.

Fixtures: `fixtures/decision-summary-v1.json` (page_now with trace_id,
freshness, policy_version), `fixtures/audit-chain-v1.json` (sealed window:
one decision_made event with C5 body fields, one checkpoint seal with
`prev_checkpoint_hmac: null` until P6 lands).

## Skill clauses that bind

- **principal-governance §2 — Unforgiving API design:** "Changing a consumed
  endpoint is massively disruptive — version explicitly, deprecate on a
  published timeline." The v1 move + RFC-header deprecation timeline is this
  clause executed.
- **principal-governance §1 — Written decisions before code:** the contract
  is the written decision; Prism builds against it from hour one with mocks
  shaped exactly like it (§3: "design never waits for backend").
- **principal-governance §4 — Unified telemetry:** `trace_id` on
  DecisionSummary is the telemetry clause reaching the operator's screen —
  the spinner-to-slow-stage lookup in one hop.
- **principal-mindset §2 (anti-self-deception):** the sealed chain endpoint
  exists because the console's derived chain attested to nothing about the
  platform — P5 fixes the audit claim with a verifiable artifact, not a
  label.
- **OPERATING-RULES §1.1:** the contract is a machine-checkable artifact;
  this README documents the contract, it is not the contract.

## Alternatives considered and rejected

- **Header-based versioning** (`Accept: application/vnd.sentinel.v1+json`).
  Rejected: path versioning is the Google AIP-185 consensus, CDN-friendly,
  and greppable; the dev.to Google/Azure/Stripe comparison notes all three
  vendors agree on "version only when you can't stay compatible" — the
  mechanism differs, and path is the most operable for a one-box tier.
- **Keeping unversioned paths as primary, versioning only on break.**
  Rejected: PIPELINE-REVISION C7 mandates the move; versioning from day one
  is cheaper than retrofitting it at the first break.
- **Serving the sealed chain from the existing `/api/decisions` shape.**
  Rejected: the chain needs `row_hash`/`prev_hash`/checkpoint HMACs the
  river shape deliberately omits; a dedicated endpoint keeps the river
  contract stable (additive-inside-v1, not a mutation).

## Web research

- RFC 9745 — The Deprecation HTTP Response Header Field (IETF, 2025):
  `Deprecation: @<unix-seconds>`, RFC 9651 structured-field Date
  (https://www.rfc-editor.org/rfc/rfc9745, accessed 2026-10-05). Shaped the
  alias header format.
- RFC 8594 — The Sunset HTTP Header Field: HTTP-date; Sunset must not be
  earlier than Deprecation; pair with `Link` rel values
  (https://www.rfc-editor.org/rfc/rfc8594, accessed 2026-10-05). Shaped the
  date-ordering rule and the successor-version link.
- Google Cloud Endpoints — API lifecycle management: backwards-compatible
  changes keep the version constant / increment minor; breaking changes get
  a new major version (`/v2`) deployed side-by-side from one backend
  (https://docs.cloud.google.com/endpoints/docs/openapi/lifecycle-management,
  accessed 2026-10-05). Shaped the additive-inside-v1 policy.
- "Google, Azure and Stripe version APIs three different ways. Here's what
  they agree on." (DEV, 2026): all three agree — version only when you
  can't make the change backward-compatible; adding optional fields and
  endpoints is safe, removing/renaming/retyping is breaking
  (https://dev.to/freelance_inspector/google-azure-and-stripe-version-apis-three-different-ways-heres-what-they-agree-on-3mbg,
  accessed 2026-10-05). Shaped the break taxonomy.
- W3C Trace Context (for the C5 trace_id this contract serves):
  https://www.w3.org/TR/trace-context/ (accessed 2026-10-05).

## Open items (T+0 / freeze)

1. **Deprecation/sunset calendar.** `x-deprecation-date` / `x-sunset-date`
   are TBD — set at freeze with the migration window (recommendation: ≥90
   days of alias service after v1 ships; the date is a Type-2 calendar
   decision recorded in the freeze ADR).
2. **Migration doc URL.** The `rel="deprecation"` link target is TBD —
   one canonical migration guide (dev.to consensus: "document the switch
   path in one canonical migration guide").
3. **`prev_checkpoint_hmac` (P6).** Carried as nullable/additive in
   `CheckpointSeal`; becomes required when checkpoint-to-checkpoint
   chaining lands — a v1-minor addition, not a break.
4. **`/api/v1/integrations/*` beyond test-page.** Only `test-page` is
   sketched here; the full write surface is the platform-api lane's
   follow-on (Vault sign-off required per OPERATING-RULES §4.3 — write
   surface).
