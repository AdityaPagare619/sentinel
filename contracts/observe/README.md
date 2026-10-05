# C5 — L4 DECIDE → L6 OBSERVE (trace-ID envelope)

**Contract:** `decision-record.v0.1.json` · **Version:** 0.1 · **Status:** DRAFT (prep, not frozen)
**Consumer-owner:** L6 OBSERVE (Ledger lane — the event log owns the record)
**Producer:** L4 DECIDE (gate)
**12H-PLAN task:** evlog-1 · **Reviewers:** Ledger (event-log side) per OPERATING-RULES §1.6

## What crosses

`DecisionRecord{contract, envelope, body}` — one per `decision_made` write:

- **envelope**: the hash-chained event envelope (`event_id`, `schema_v`, `ts`,
  `actor=engine`, `type=decision_made`, `alert_id`, `fingerprint`,
  `episode_id`, `outbox_id`) **plus `trace_id`** (W3C Trace Context
  trace-id: 16 random bytes, 32 lowercase hex, all-zeros forbidden).
  The envelope is part of the hashed material
  (`row_hash = sha256(canonical(envelope-without-hashes + body) || prev_hash)`),
  so trace-ID tampering breaks the chain.
- **body**: the decision (`disposition`, `budget_outcome`, `lock_evaluation`,
  `freshness`, `threshold_counterfactual`, `links` — the frozen vocabulary)
  **plus two C5 additions**: `policy_version` (the policy that decided; fills the
  gap the Prism UI currently labels as honestly absent — prism-ui.md P2/A6,
  INV-3) and `stage_latencies{normalize_ms, correlate_ms, gate_race_ms,
  decide_ms}` (monotonic-clock per-stage legs; answers "which stage ate the
  budget" in one lookup — receiver.md R-3 lineage).

## What NEVER crosses

- **A second copy of trace_id in the body.** trace_id lives in the envelope
  once (SSOT — principal-governance state isolation). A body duplicate is a
  divergence incident waiting to happen.
- **Raw vendor bytes.** They stop at L2 (C2 anti-corruption). The record
  carries `input_sha256` as the join key; raw payloads are keyed by it
  (event-log-audit P8).
- **Vendor identity beyond the fingerprint.** The decision core never sees
  foreign shapes (C2).
- **Secrets, keys, PII.** BYOK key material, attestor identities beyond the
  governance-required fields, and PII fields never enter the record
  (event-log-audit P7).
- **Policy content.** Only the `policy_version` label crosses. The content is
  governance-controlled (R-1 attested lifecycle); the kernel's canonical
  `PolicyVersion.content` is Aditya's Type-1 Q3 — this contract carries the
  label, never the content.
- **forward latency.** `forward_ms` arrives later via C6's `ForwardReceipt`;
  L6 reconciles it onto the read view. The sealed `decision_made` row is
  never rewritten (event-log appendix: the log is audit-only by design).

## Version

`contract.version: "0.1"` — additive inside a version; a new version only for
true breaks (rename/remove/retype, new required key) — Google/Azure/Stripe
consensus (see `contracts/serve/README.md` versioning policy).

## Per-layer propagation notes (what each layer must attach)

One trace ID is born at L1 ingress and carried through every hop. Propagation
is by **explicit parameter threading** (a context object / function argument),
never thread-local or ambient context — the hexagonal test
("run core logic in tests with zero infrastructure") must keep passing, and
ambient context is the failure mode eternal friction warns about.

| Layer | Must attach |
|---|---|
| **L1 INGEST** | **Mint-or-continue.** Accept inbound W3C `traceparent`; if well-formed (`00-<32hex>-<16hex>-<flags>`), continue it (keep trace-id, mint a fresh parent-id). If absent or malformed, mint 32 random lowercase hex. Malformed headers never reject the request (Postel at the edge — validated in the C1 ingress-schema tests). Record the monotonic-clock start as the latency baseline. Emit the first structured log line with `trace_id`. |
| **L2 NORMALIZE** | Propagate trace_id into the pipeline context (never into vendor-shaped fields). Measure `normalize_ms` at the L2 boundary. |
| **L3 DECIDE-1** | Propagate; measure `correlate_ms`. The `TriageResult` may reference the trace_id in episode links but never re-mints it. |
| **L4 DECIDE-2** | Measure `gate_race_ms` (the Jev race budget leg) and `decide_ms` (total decide leg; `decide_ms ≥ gate_race_ms`). Attach `policy_version`. Write the DecisionRecord via `record_decision_and_enqueue` with `envelope.trace_id`. Every structured log line on the decide path carries `trace_id`. |
| **L5 ACT** | Propagate trace_id into the outbox row and onto the outbound PagerDuty event (vendor contract permitting — custom detail, never the dedup_key, which is pinned per C4/C6 idempotency). Attempt latencies ride C6's `ForwardReceipt`, not this contract. |
| **L6 OBSERVE** | Persist the envelope+body; every log line carries `trace_id`. Reconcile `forward_ms` from C6 receipts onto the served view (join on decision seq) — never into the sealed row. Chain verification covers the envelope including trace_id. |
| **L7 SERVE** | Expose `trace_id` on `DecisionSummary` (C7) so an operator pivots from a slow decision to the exact slow stage in one lookup. The console's R25 trace plumbing consumes it (prism-ui.md A6 — currently absent; this contract is the supply). |

## Machine-checkable

```bash
python3 contracts/observe/validate.py   # stdlib only; validates fixtures/
```

Fixtures: `fixtures/decision-page-now.json` (answered_in_time page path,
outbox row present), `fixtures/decision-suppress.json` (timer_won suppress
path, `outbox_id: null`, `threshold_counterfactual` populated, `segment_id`
present to exercise chain-of-segments). Cross-fixture invariant: trace_id
differs per request.

## Skill clauses that bind

- **principal-governance §4 — Unified telemetry:** "one Trace-ID per user
  interaction, injected from the browser through gateway, microservices, and
  database." This contract is that clause made machine-checkable for the
  decide path.
- **principal-systems Infrastructure constitution — observability over
  monitoring:** "trace a request through every layer, don't just alert on
  CPU." stage_latencies is the per-layer trace made queryable.
- **principal-governance — State isolation (SSOT):** trace_id lives in the
  envelope once; no body duplicate.
- **principal-governance — Contract first:** the contract is a JSON Schema,
  not prose — prose here documents the contract (OPERATING-RULES §1.1).

## Alternatives considered and rejected

- **Reuse `event_id` (per-row uuid4) as the correlation ID.** Rejected: one
  inbound request fans out to `decision_requested`, `decision_made`,
  `forward_*` rows — per-row ids cannot correlate. A separate request-scoped
  ID is required.
- **Ambient propagation (contextvar/thread-local).** Rejected: fails the
  hexagonal test under async, and is invisible to the tired maintainer
  (eternal friction). Explicit threading is boring and testable.
- **64-bit integer trace IDs.** Rejected: collision risk across restarts and
  chain segments; the 128-bit W3C form is the vendor-neutral standard and
  matches the 16-hex fingerprint vocabulary already in the codebase.

## Web research

- W3C Trace Context, `traceparent` format — trace-id = 32 lowercase hex,
  16 bytes, all-zeros forbidden; malformed headers MUST be ignored, never
  reject traffic (https://www.w3.org/TR/trace-context/, accessed 2026-10-05).
  Shaped the mint-or-continue rule and the all-zeros rejection.
- RFC 9745 / RFC 8594 deprecation practice (for C7, see
  `contracts/serve/README.md`) — https://www.rfc-editor.org/rfc/rfc9745,
  https://www.rfc-editor.org/rfc/rfc8594, accessed 2026-10-05.
- Google Cloud Endpoints API lifecycle guidance — backwards-compatible
  changes keep the version constant / bump minor; breaking changes get a new
  major version deployed side-by-side
  (https://docs.cloud.google.com/endpoints/docs/openapi/lifecycle-management,
  accessed 2026-10-05); Google/Azure/Stripe consensus summary
  (https://dev.to/freelance_inspector/google-azure-and-stripe-version-apis-three-different-ways-heres-what-they-agree-on-3mbg,
  accessed 2026-10-05). Shaped the C7 versioning policy this contract defers to.

## Open items (T+0 / amendment discipline)

1. **Vocabulary amendment ADR.** `trace_id` (envelope), `policy_version` and
   `stage_latencies` (body) widen the Type-1-frozen event vocabulary
   (`eventlog.py` `_BODY_REQUIRED` is frozen). Adoption ships with the
   contract-amendment ADR per OPERATING-RULES §1.1 — no silent widening.
2. **trace_id on the other event types.** v0.1 pins the `decision_made`
   DecisionRecord. Extending `envelope.trace_id` to `decision_requested`,
   `forward_*`, `checkpoint` rows is the v0.2 extension (same ADR or a
   follow-on; the per-layer notes above already specify the behavior).
3. **`policy_version` format.** Carried as an opaque label here. If Aditya's
   Type-1 Q3 (canonical `PolicyVersion.content`) lands, the label format is
   pinned in the amendment; the contract shape does not change.
