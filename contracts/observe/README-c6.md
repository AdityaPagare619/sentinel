# C6 — L5 ACT → L6 OBSERVE: `ForwardReceipt` v0.1

**Contract file:** `forward-receipt.v0.1.json` (JSON Schema, draft-07 — the contract;
this README documents it, it is not the contract).
**Status:** DRAFT v0.1.
**Consumer-owner:** L6 OBSERVE (event-log/audit side). Consumer owns the contract
(OPERATING-RULES §1.1; principal-governance §2 unforgiving API design).
**Source:** PIPELINE-REVISION.md §2.2/C6; `forwarder-byok.md` §2.5 (the
simulated-spill audit lie) and P6; `event-log-audit.md` §1.1 (I2 invariant).

## 1. What crosses

`ForwardReceipt{contract, contract_version, decision_id, attempt_no, wire_sha256, outcome, simulated, channel?, error_class?}`

- `decision_id` — the event-log sequence number of the `decision_made`
  event being acted on, rendered as a decimal string (e.g. `"412"`).
  MINT — defined normatively here, the single contract that defines it
  (Tripwire B5): (a) outbox/secondary channels — L5 reads the outbox row's
  `decision_seq` column (INTEGER NOT NULL, eventlog.py:282) and renders it
  decimal; (b) direct-degraded channel — no outbox row exists, so L5 MUST
  capture the decision's event-log sequence into the spill record (spill
  field `decision_seq`) at send time, and the replay MUST copy it verbatim
  into the receipt. `"−1"` means NO joinable decision: control-plane pages
  (eventlog.py `_enqueue_control_plane_page`) and degraded sends whose
  decision never durably sequenced (e.g. the commit-watchdog path). L6
  joins receipt → `decision_made` on this key; `"-1"` receipts stand alone.
  Schema-enforced shape: `^-?[0-9]+$`.
- `attempt_no` — 1-based; from the outbox row's `attempt_count` at claim time
  (forwarder.py:444); shared with the retry-schedule index. On the
  direct-degraded channel `attempt_no` is ALWAYS 1 — the degraded path makes
  exactly one direct attempt (no retry ladder); duplicate suppression across
  crash-replays is `spill_id`'s job, not `attempt_no`'s.
- `spill_id` — REQUIRED when `channel` is `direct-degraded`, FORBIDDEN
  otherwise (Tripwire B3). sha256 (lowercase hex) of the spill file's exact
  bytes as read at replay. Stable across crash-replays of the same spill;
  unique across distinct spill writes. The replay-dedup key: L6 MUST treat
  same `(channel, spill_id)` as the same send replayed (keep first, drop
  the rest).
- `wire_sha256` — sha256 of the EXACT bytes presented to the transport
  (pd_sender.py:338 hashes `wire`). For SIMULATED absorption: sha256 of the
  CANONICAL KEYLESS serialization — the wire event as constructed on the
  simulated path (which never resolves a routing key; the key value is
  deliberately never materialized), serialized as JSON with keys sorted,
  separators `(',',':')`, UTF-8 encoded (Tripwire B4 — the previous "exact
  bytes that WOULD have been sent" had no canonical construction). The
  producer MUST construct these exact bytes on the simulated path even
  though nothing is transmitted. Never a re-serialization of pretty-printed
  JSON, never a hash of an arbitrary placeholder.
- `outcome` — `accepted | retryable | terminal`, pd_sender's own taxonomy
  (pd_sender.py:91). `accepted` covers real wire acceptance AND simulated
  absorption (the `simulated` flag distinguishes them).
- `simulated` — **true iff the page was absorbed by simulated mode and
  nothing went on the wire** (forwarder.py:766, `pd_outcome: "simulated"`).
- `channel` — `outbox | direct-degraded | secondary`.
- `error_class` — REQUIRED for `retryable`/`terminal`, FORBIDDEN for
  `accepted`. This is what makes "failure" machine-detectable.

## 2. The two normative behavioral rules (not just data)

1. **Atomicity — OUTBOX-BACKED CHANNELS (outbox, secondary).** The receipt
   MUST be written in the SAME database transaction as the outbox scheduler
   update it describes (claim / confirm / retry / dead-letter). A receipt
   without its scheduler row, or a scheduler row without its receipt, is a
   contract violation. *Why:* the failure mode is a crash between the two
   writes — eternal friction (principal-systems law 2). I2, today's proudest
   invariant (`event-log-audit.md` §1.1): "the event log and the work queue
   can never disagree — a database property, not a code convention."
   *Mechanism:* one transaction; plus a reconciliation test — every
   scheduler transition to a terminal state MUST have a matching receipt —
   owned by the L5 lane, plugged into the qa-2 `tests/contracts/` gate.
   **DEGRADED CHANNEL (direct-degraded): there is NO scheduler row by design
   — the atomicity rule does not apply** (Tripwire B3: the old text
   criminalized the contract's own `direct-degraded` fixture). The honest
   guarantee is AT-LEAST-ONCE REPLAY: every spill file is replayed into the
   event log until its file is archived; a crash between the log append and
   the file archive MAY emit a duplicate receipt for one send (mechanically
   demonstrated by crash injection against `spill.py`). L6 MUST deduplicate
   on `(channel, spill_id)`.
2. **Simulated honesty.** `simulated: true` MUST propagate end-to-end
   (spill → replay → event body) and MUST NEVER be recorded as a failure.
   `simulated: true` + `outcome: accepted` is a CONFIRMATION of absorption,
   not a failure. **This contract is where the simulated-spill audit lie dies**
   (`forwarder-byok.md` §2.5: simulated degraded pages replaying as
   `forward_failed` with `error_class: "degraded_send_failed"`). *Mechanism:*
   the schema makes `simulated` a first-class required boolean AND makes the
   lie shape unrepresentable: `simulated: true` ⇒ `outcome` MUST be
   `"accepted"`, `error_class` MUST be absent (schema `allOf` branch 3;
   a failure for a page never attempted on the wire cannot be expressed).
   Standing falsifier from P6 survives into the build: *any code path that
   emits `forward_failed` for a page never attempted on the wire falsifies
   the contract.* Negative fixture
   `fixtures/negative_simulated_as_failure.json` MUST fail validation.

## 3. What NEVER crosses (and the mechanism for each "never")

1. **A receipt without its scheduler row (or vice versa) — on outbox-backed
   channels.** Mechanism: one transaction (§2.1) + the reconciliation test.
   On the degraded channel there is no row by design; the "never" there is
   **an undeduplicable duplicate**: mechanism is `spill_id` (required) +
   L6's MUST-deduplicate rule — a degraded receipt without `spill_id` is a
   contract violation (schema-enforced).
2. **A simulated absorption recorded as failure.** Mechanism: §2.2 —
   schema, negative fixture, P6 falsifier.
3. **error_class on an accepted receipt.** Mechanism: schema `not required`
   rule — an accepted receipt carrying an error is a lie in the other
   direction (poisoning "accepted" for SLO math).
4. **Re-serialized wire bytes hashed as wire_sha256.** Mechanism: producer
   MUST hash the transport bytes; a test asserting
   `receipt.wire_sha256 == sha256(bytes_actually_sent)` on the FakePD path.
   On the simulated path the producer MUST hash the canonical keyless
   serialization (defined in §1); a test asserting equality against
   independently-constructed keyless bytes. A hash of an arbitrary
   placeholder is a contract violation.
5. **A new outcome value invented by a producer.** Mechanism: closed enum;
   the consumer (L6) owns the enum and must approve additions
   (principal-governance: no provider silently widens a contract).
6. **Raw secret material** (Vault never-crosses rule, attested 2026-10-05 —
   see `docs/decisions/2026-10-05-nevercrosses-c4-c6.md` for the
   field-by-field audit). No ForwardReceipt field carries key material:
   - The receipt carries NO key field at all. The only key-adjacent value
     is `wire_sha256` — a one-way hash of the POST bytes (preimage
     resistance; it proves what went on the wire, it cannot recover the
     key). `spill_id` is a hash of spill-file bytes that themselves carry
     only `routing_key_ref` (a vault-path reference, `secret:pd/...`,
     resolved env-side at send time; forwarder.py:806-823).
   - The simulated path constructs the CANONICAL KEYLESS serialization and
     never resolves a routing key — it reports the key SOURCE only
     (`paging_key_source`, integrations.py:219).
   - `payload_frozen` (what gets hashed/sent) must be keyless:
     `PayloadNotKeyless` refuses key-bearing frozen payloads at send time
     (pd_sender.py:173-186) — secrets never freeze to disk, so they can
     never ride a hash or a receipt.
   - Failure-channel strings (`error`, `error_class`, scheduler
     `last_error`) run through `sanitize_error` (integrations.py:259),
     which strips configured key VALUES before emission.
   *Mechanism:* schema shape (no key-bearing field exists to fill) +
   mechanically enforced by `tests/test_nevercrosses_key_hygiene.py` —
   resolve_paging_key rigged to raise on both simulated paths, a canary key
   asserted absent from stderr/spill/returned structures, and a negative
   structural guard that fails the build if any C4/C6 fixture grows a bare
   `routing_key` field.

## 4. Version / compatibility

Additive inside v0.1 (e.g. new optional fields); new version only for true
breaks (C7 compat rule, OPERATING-RULES §1.1). Whether L6 materializes
simulated receipts as `forward_confirmed`+`simulated:true` or as a distinct
`forward_simulated` event is the CONSUMER's Type-2 decision (L6 owns the
event taxonomy) — the contract only guarantees the boolean reaches L6.

## 5. Pre-mortem (top 3 — principal-systems §2)

1. *The receipt is written by a separate async writer "for performance."*
   The dual-write crash window returns. Killed by §2.1: one transaction is
   normative, not advisory; the reconciliation test fails the build.
2. *A metrics consumer maps `simulated: true` → failure burn.*
   The audit lie returns wearing a metrics costume. Killed by §2.2 + the
   negative fixture + P6's falsifier; reviewers: any dashboard touching
   receipts routes through Ledger.
3. *`wire_sha256` gets computed over pretty-printed JSON instead of the
   POST bytes.* The hash proves nothing. Killed by the FakePD byte-equality
   test (§3.4).

## 6. Skill & Evidence (OPERATING-RULES §2.2)

Binding clauses, each with how it binds:
- **principal-systems law 2 (eternal friction):** the crash between "vendor
  accepted" and "receipt written" is designed for, not hoped away — the
  atomicity rule exists because the crash WILL happen (forwarder.py's
  crash-window analysis is the repo's own prior art).
- **principal-systems software constitution ("explicit versioned boundaries")
  + `event-log-audit.md` §1.1:** the I2 invariant is a database property,
  not a code convention — the contract states it as normative behavior, not
  prose advice.
- **principal-governance §2 (unforgiving API design; consumer owns):**
  closed `outcome` enum owned by L6; additive-inside-version compatibility.
- **principal-systems "Chesterton's fence":** `outcome` reuses pd_sender's
  `accepted|retryable|terminal` (pd_sender.py:91) instead of inventing a new
  taxonomy — the existing one carried embedded knowledge (terminal vs
  retryable drives the retry ladder).
- **OPERATING-RULES §3.4 honesty law ("no fake demos"):** the simulated
  flag is the in-band honesty label; the contract is the enforcement point
  of the P6 fix.
- **External source — transactional outbox pattern**
  (https://github.com/dcsg/archway/blob/HEAD/website/src/content/docs/glossary/outbox-pattern.mdx,
  accessed 2026-10-05): "the transactional outbox pattern guarantees reliable
  event publishing… by writing events to a database table in the same
  transaction as the business operation, then relaying them asynchronously"
  — the dual-write problem is exactly the receipt/scheduler-write pair.
  This is what killed alternative (a) below: atomicity as a database
  property, relay (the event log) as the async consumer.

Alternatives considered and rejected:
- (a) receipt written by a separate async writer after the scheduler commit.
  Rejected: the dual-write crash window is the failure mode being fixed;
  a database property, not a code convention.
- (b) fold `simulated` into the outcome enum only (no boolean), or record
  simulated pages as failures. Rejected: (b1) loses the honesty signal —
  audit can't distinguish simulated absorption from real wire success;
  (b2) IS the audit lie (`forwarder-byok.md` §2.5).
- (c) distinct `forward_simulated` event type instead of the flag.
  Deferred to the consumer (L6): the contract guarantees the boolean reaches
  L6; L6 chooses the event shape (Type-2, consumer-owned).

Tool evidence: `contracts/validate_fixtures.py` (stdlib `json` only) —
3 validating fixtures + 1 negative fixture; run output in the PR body.
Inventory commands: `grep -n "attempt_no\|wire_sha256\|pd_outcome" src/sentinel/{forwarder,pd_sender,spill}.py`
(all read, none modified).

Consumer-owner: L6 OBSERVE (event-log side). Expected reviewers: Tripwire
(full adversarial, per 12H-PLAN F6). Vault ack on the wire-hash handling
surface (audit-tamper model).
