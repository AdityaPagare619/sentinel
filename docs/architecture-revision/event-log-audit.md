# Event Log / Audit (Tamper-Evidence) — Phase-2 Domain Review

**Reviewer:** Phase-2 domain reviewer (fresh eyes; did not build this)
**Date:** 2026-10-05 · Branch: `program/architecture-revision-dom-eventlog`
**Code read at:** `program/architecture-revision` (worktree `sentinel-revdom-eventlog`)
**Skills loaded:** principal-systems, principal-governance, principal-mindset, execution-doctrine
**Phase-1 research read:** `research/architecture-patterns.md`, `research/swe-discipline.md`

**Scope:** `src/sentinel/eventlog.py`, `audit.py`, `checkpoint.py`,
`retention.py`, `verify.py`, `migrate.py`, the platform read model
(`platform/server/store.py`, `mute.py`), the audit explorer surface
(`platform/ui/assets/views-audit.js`, `chain.js`), and `tests/test_eventlog.py`
/ `test_audit.py` / `test_retention.py` / `test_diskguard.py`.

**Standing note on evidence:** every claim below is grounded in code I read
line-by-line in this worktree. Where I ran something, I say so. The known
pre-existing `test_dual_write_round_trip` failure is in
`tests/test_fingerprint_env.py` — it concerns the *fingerprint-scheme*
migration, not the event log; it is unrelated to this domain and I did not
chase it.

---

## 1. WHAT EXISTS

### 1.1 Chain construction

`src/sentinel/eventlog.py`:

- One SQLite table, `events`, in WAL mode, co-located with the `outbox`
  table in the **same file** — load-bearing, because the I1/I2 transaction
  invariants need one database (module docstring).
- Hash chain: `row_hash = sha256(canonical(envelope-without-hashes + body) || prev_hash)`
  (`_row_hash`). Canonical form is sorted-keys, no-whitespace JSON.
  Genesis seed is the constant `"GENESIS"`; since the D14 retention work
  (today), a rolled segment may instead seed from the previous segment's
  head hash via the `genesis_prev_hash` constructor parameter
  (chain-of-segments).
- Single-writer: one connection, one `threading.RLock`. The head cache
  (`_head_seq`, `_head_hash`) is snapshot-and-restored around commit
  failures so a rolled-back write can never leave the cache chained from a
  phantom hash (the "Blocker 1" fix — real engineering).
- **Type-1 vocabulary is frozen in code:** 17 event types
  (`decision_requested`, `decision_made`, `shadow_decision`, `model_drift`,
  `forward_confirmed`, `forward_failed`, `flip_observed`, `checkpoint`,
  `mute_*` ×4, `episode_resolved`, `failopen_step_entered`,
  `failopen_recovered`), the `ACTORS` set, the `DISPOSITIONS` set, and
  per-type required body keys — all enforced in `_validate` at write time.
  Notable: the no-auto-mute invariant (ADR-007) is enforced **in the log
  layer itself** (attestor blocklist in `_validate`), so no caller can
  bypass it by appending directly. This is the right place for the check.
- **Write-ordering invariants I1/I2/I3** (module docstring, enforced by the
  transaction structure, not by convention):
  - I1: `decision_made` + outbox row commit in one transaction
    (`record_decision_and_enqueue`), with per-episode coalescing
    (`UNIQUE(episode_id) WHERE status IN ('queued','in_flight')`).
  - I2: `forward_confirmed`/`forward_failed` + outbox scheduler update in
    one transaction (`record_receipt_and_update`) — "the event log and the
    work queue can never disagree about what happened — a database
    property, not a code convention."
  - I3: the receiver 202s only after `decision_requested` commits
    (`record_request`).
- **Commit watchdog:** every hot-path commit is timed against a 50 ms
  budget (`COMMIT_WATCHDOG_MS`); a trip engages a degraded path —
  emergency spillover record, degraded direct send, and an unsuppressible
  control-plane page via the priority-1 outbox lane — with a re-entrancy
  guard so the degraded path's own commits can't re-trip. CI
  fault-injects the trip (`_test_commit_delay_s`) and asserts p99
  event-write < 5 ms (`P99_WRITE_BUDGET_MS`). The degraded-path design is
  careful and honest about its limits (the docstring admits the commit
  already happened — "abandoning the wait is aspirational on one thread").
- **Reaper** (Pair-B crash-window closer): sweeps `decision_requested`
  older than 30 s with no answering `decision_made` (joined via
  `links.decision_requested_seq`) and redrives each as fail-open
  passthrough in the I1 transaction. Available as `startup_sweep()` and
  `run_periodic()`.
- **The river is a fold, not a SELECT** (`render_alert`/`render_episode`):
  the alert's honest state (`requested | decided_unconfirmed | paged |
  suppressed | passthrough_* | folded_into_aggregate | PAGED_ANOMALY |
  unknown`) is derived by folding the event stream. THE RIVER RULE: a
  `decision_made(page_*)` with no `forward_confirmed` older than 60 s
  renders `PAGED_ANOMALY` — red, pinned. "The transient state can exist;
  the SILENT state cannot." `forward_confirmed` is the **only** event that
  means "paged."
- **Append-only is test-enforced:** `tests/test_eventlog.py` carries a
  grep guard — any `UPDATE` against `events` in `src/` fails the suite —
  plus tamper tests (rewrite a body, rewrite a `prev_hash`, delete a row)
  that assert detection.
- `raw_payloads` side table: inbound payloads stored content-addressed by
  `input_sha256`; event rows carry only `raw_payload_bytes` so the chain
  stays small. (Tampering with a payload breaks its hash link — the
  content-addressing is the integrity story here.)
- `detect_flips`: pure function finding non-determinism across
  `decision_made`/`shadow_decision` pairs by `input_sha256`.

### 1.2 Sealed checkpoints

`src/sentinel/checkpoint.py`:

- Hourly `CheckpointJob`: signs `(head_seq, head_hash, event_count,
  window_start_ts, window_end_ts)` with **HMAC-SHA256** and pushes the
  document to a customer-controlled sink (`LocalDirSink` — atomic
  tmp+rename; `WebhookSink` — HTTPS POST).
- The checkpoint is **written back into the log itself** as a `checkpoint`
  event carrying `sink_push_ok` — the push happens first and its outcome
  is part of the sealed record. Windows chain (`_last_window_end` reads
  the latest checkpoint; an old ASC-ordering bug that froze windows is
  documented as fixed).
- Sink outage pages after >2 consecutive failed hours
  (`SINK_FAILURE_PAGE_AFTER_H = 2`) via the control-plane outbox lane.
- The module docstring states the signature primitive **honestly**: the
  HMAC key is generated on the customer's side at onboarding and provided
  to Sentinel; the HMAC buys transit/at-rest integrity of the sink copy
  and non-equivocation *to third parties*; it does **not** buy protection
  against a fully compromised engine — "the signature was never the
  defense against that." Ed25519 is flagged as the future path.

### 1.3 Retention tiers (D14 — landed today)

`src/sentinel/retention.py` (RFC `docs/rfc-retention-2026-10-05.md`):

- Hot (self-calibrating window, 60-day floor) → warm (365 d
  full-fidelity) → cold (7 y summaries). The hot window is derived from
  the deployment's **own measured bytes/day** against its disk budget —
  no month-tuned constants — and the job **pages instead of silently
  shortening** below the 60-day floor (under-provisioned is a paging
  incident). Cold expires only with explicit customer liability
  acceptance (`allow_cold_expiry`).
- Chesterton's fence honored: no `DELETE` from a chained log — segments
  are time-partitioned and chained (`seal_and_roll`: WAL checkpoint,
  close, rename to `segments/`, new `EventLog` seeded from the sealed
  head hash).
- Export order is fixed and never reordered: verify chain → write
  `events.jsonl` → seal HMAC'd manifest → checkpoint the archive linkage
  into the live log → prune. A broken chain refuses to prune
  (`ChainBroken` pages).
- Deletions are ledger-logged **and** checkpointed into the live log —
  the two records corroborate each other. `SegmentLedger` saves
  atomically (tmp+replace). Dry-run mode is the shadow mode (full plan,
  zero deletes, zero checkpoints). Low-disk (<5% free) makes the job
  read-only and pages.
- GDPR: salted-hash tombstones (`pseudonymize_value`/`pseudonymize_record`);
  per-customer tier overrides (`RetentionConfig.for_customer`); EU region
  pinning in config.

### 1.4 Migration and dual-read surface

- `src/sentinel/migrate.py`: one-time v0.1 → event-log backfill. Type-1
  rule: **never synthesizes `forward_confirmed`** — "synthesizing a
  receipt for history we did not witness would be the exact lie this
  design exists to kill." Idempotent resume after crash (backfilled rows
  form a marked prefix). Old table renamed to `decisions_v0_1`.
- `src/sentinel/audit.py`: v0.1-compat `AuditLog` facade writing
  `decision_made` events with explicit "pre-lock era" notes instead of
  inventing lock/freshness evidence it never had. Honest shimming.
- A disposable `decisions` SQL VIEW over `events` serves pre-migration
  readers; "projections are never the truth."

### 1.5 Independent verification

- `src/sentinel/verify.py`: one-command chain verifier
  (`python -m sentinel.verify sentinel.db`), with **anchored
  verification** from a customer-verified checkpoint (`--from-seq` +
  `--hmac-key-file`): the checkpoint's HMAC is verified first, its own
  link checked, then the chain replays forward. Exit 0 intact / 1
  broken. "Corruption and tamper present identically — the verifier
  reports, it does not distinguish."

### 1.6 Audit explorer surface

- The console's Audit view (`platform/ui/assets/views-audit.js`) searches
  `GET /api/decisions` (+ filters, flips, noise analytics) and renders a
  chain panel. `chain.js` **derives a tamper-evident chain locally** from
  stored decisions (`sha256(prev ‖ input_sha256 ‖ created_at)`) and
  verifies it client-side — explicitly labeled DERIVED: "it is NOT the
  platform's sealed log and cannot attest to what the platform recorded.
  The frozen read contract (v1.0.0) does NOT expose the platform's sealed
  event log — there is no GET /api/audit/chain."
- The platform read model (`platform/server/store.py`) is read-only by
  construction: every query opens the DB `mode=ro`; a fresh connection
  per query; zero Jev imports (grep-guarded); honest reconstruction
  labels where the engine doesn't persist something (Q2/Q3 residual
  mass, alert context). Good hexagonal-adjacent hygiene for the read
  side.
- Mute governance (`platform/server/mute.py`) folds `mute_*` events from
  the log — the mute lifecycle is fully event-sourced, and
  `quarantined_suppressions` cross-checks suppressions against mutes.

**Bottom line of §1:** the mechanism layer is genuinely good — frozen
vocabulary, transaction-bound invariants, test-enforced append-only,
honest checkpoint primitive, a retention design with self-calibration
and fail-toward-the-human semantics, and labeled honesty where the
system knows its limits. This is better than most "audit log" features
I have reviewed.

---

## 2. WHAT'S MISSING

Measured against the Phase-1 enterprise standard. Citations inline.

### M1. The tamper-evidence guarantee is narrower than the claim — the threat model is unwritten

The Phase-1 architecture research establishes the bar: audit/replay is
the fit condition for event sourcing, and the log's value is as
*independently verifiable* truth (architecture-patterns §1.3: "Sentinel's
hash-chained event log is event sourcing done right… audit capability
without the replay liabilities"). But auditability as a **requirement**
needs IEEE-830-style verifiability — "for each requirement there is a
finite cost-effective process by which a person or machine can check
that the product meets it" (swe-discipline §3a). No document states the
adversary, the check, and the evidence for this log.

What the mechanism actually guarantees, stated precisely:

- **Held:** post-hoc modification of *stored artifacts* by anyone who
  does not hold the HMAC key is detectable (hash chain + checkpoint
  HMACs). Internal consistency of one chain is machine-checkable.
- **NOT held — writer forgery:** the engine holds the signing key and
  the pen. A compromised writer can produce a *perfectly valid* hash
  chain plus *valid* HMACs for any fabricated history. The checkpoint.py
  docstring admits this. Note the principal-mindset proxy-trap audit
  here: "chain verifies" is a proxy for "history is true"; the proxy
  fails exactly at the insider threat. Ed25519 would not fix it — the
  key still lives in the writer.
- **NOT held — writer silence:** stopping logging is bounded only by the
  hourly checkpoint cadence and a *self-reported* sink-failure page (the
  writer attests to its own sink health). The two-hour page bound
  (`SINK_FAILURE_PAGE_AFTER_H`) is also in-memory state — a process
  restart resets `consecutive_failures`, so the bound is weaker than
  documented.
- **NOT held — independent verification in practice:** the customer
  verifies with the writer's CLI against the writer's database file.
  There is no documented customer verification workflow (what to check,
  how often, what the 2-hour bound means for them), and the sealed log
  is not exposed at runtime at all (see M5).

"Tamper-evident durable truth" as a product claim currently means
*external-attacker* tamper-evidence. Against the writer — the party the
customer is actually buying trust *from* — the guarantee is one hourly
HMAC'd anchor plus the customer's discipline in archiving checkpoint
docs, a discipline no artifact asks for.

### M2. Three audit-critical components exist with no production wiring

Code without a caller is a design doc, not a control. Grep-verified in
this worktree — zero production callers (tests only):

- **`CheckpointJob`** (the hourly seal). Nothing schedules it. The
  entire sealed-checkpoint story — the *only* external anchor of the
  chain — never runs in production.
- **`Reaper.startup_sweep()` / `run_periodic()`** (the crash-window
  closer). Nothing calls it. Orphaned `decision_requested` rows are
  never redriven in production; the Pair-B guarantee exists in prose
  and tests only.
- **`seal_and_roll` / `RetentionJob`** (the tier state machine). Landed
  today in code; nothing schedules it, no coordinator owns it, no
  runbook arms it. Consequence: the D14 "retention policy" is currently
  the 100 MiB disk-guard watermark alarm — an early-warning page, not a
  policy.

Per execution-doctrine, the validate→shadow→canary pipeline starts with
the thing actually running. The retention RFC being pre-build is the
swe-discipline model (the one panel-reviewed RFC with position memos);
the checkpoint job and reaper never even got that.

### M3. The verifier and the retention model disagree on the genesis rule — reproduced

`sentinel/verify.py` (the customer-facing one-command verifier) anchors
an un-anchored verification at `prev_hash = GENESIS` for seq 1.
`retention.verify_chain` (the prune-gate verifier) instead takes the
first row's own `prev_hash` as the segment anchor. After one
`seal_and_roll`, the live DB's seq 1 carries `prev_hash = <old head>`
— and the customer verifier reports a **healthy log as BROKEN**:

```
customer verifier FAILED: chain broken at seq 1: prev_hash c662700a…
  != expected GENESIS... (previous row rewritten or a row deleted)
retention.verify_chain: (1, '4dc86e…', 1)   # passes
```

(Reproduced 2026-10-05 in `/tmp/evlog-repro`: wrote 2 events, rolled,
wrote 1 event, ran both verifiers.) Two truth-rules for one chain. The
decay mode is the one the principal-mindset anti-theater audit names:
a verifier that cries wolf on healthy logs trains everyone to ignore
it — and it will cry wolf on *every* deployment the day after the
first roll.

### M4. The roll entombs live outbox rows — I1/I2 break at the segment boundary

`seal_and_roll` renames the live DB file without checking for live
outbox rows (`queued`/`in_flight`). The fresh `EventLog` starts empty.
Everything that monitors decided-but-unconfirmed work reads **only the
live log**: the forwarder's due-row query, the Pair-C startup scan
(`undelivered_outbox_rows`), the Reaper, `render_alert`'s RIVER RULE,
the ADR-022 watchdog (`decision_dispositions_since`). So a page queued
at roll time is sealed into yesterday's segment — never redriven, never
rendered as `PAGED_ANOMALY`, never reported. The module docstring's
proudest sentence — "the event log and the work queue can never
disagree about what happened — a database property, not a code
convention" — holds only *within* a segment. Across the roll boundary,
the chain is continuous but the operational guarantees it exists to
support are silently dropped. This is Chesterton's fence in reverse:
the roll respects the hash chain while breaking the invariants the
chain was built to protect. (Also: `seq` restarts at 1 in every
segment — cross-segment tooling keyed on `seq` is ambiguous, and
`checkpoint` docs carry no `segment_id`, so a customer holding sink
copies cannot attribute a checkpoint to its segment.)

### M5. The audit explorer verifies a derived chain, not the sealed log

`chain.js` is honest about it — the panel is labeled DERIVED — but the
architecture fact stands: the most prominent customer-facing
"tamper-evident" affordance proves only that the decisions the *console*
holds are internally consistent. It cannot attest to what the platform
recorded. The frozen v1.0.0 read contract exposes no sealed chain
("there is no GET /api/audit/chain"), and `chain.js` itself records
that shipping it is an unfiled RFC. Combined with M1: the customer has
no runtime path to the actual evidence, only to a re-derivation of a
projection of it.

### M6. Checkpoint docs don't chain to each other

A checkpoint document commits to `(head_seq, head_hash, event_count,
window)` — but not to the *previous* checkpoint. Between two hourly
checkpoints, a writer-side history rewrite produces two
individually-valid HMAC'd docs; detection requires the customer to have
archived every doc and to check `head_seq`/window continuity manually.
No workflow asks them to. (Related hygiene: in-band checkpoint events
use `alert_id="checkpoint"` / `fingerprint="checkpoint"`, polluting
the alert/fingerprint query surface — `events_for_alert("checkpoint")`
is a real query someone will run by accident.)

### M7. Key lifecycle is unspecified; the GDPR story is half-wired

The HMAC key is "generated on the customer's side at onboarding" —
then nothing. No rotation protocol, no dual-key transition, no
compromise response runbook. The same key seals retention manifests
(`_manifest_hmac`), so the gap covers the archive too. And the GDPR
tombstone the module advertises is **dead code on the only path that
matters**: `_promote_to_warm` calls `export_segment(...)` without
`salt` or `pseudonymize_fields` — the `pii_fields` config and the
region pinning are plumbed into `RetentionConfig` but never into the
promotion path. The privacy story is documented, tested in isolation,
and not invoked. ("WORM" is likewise a deployment property, not an
enforced one: `export_segment` does a plain `open()`; "WORM-shaped
storage" is doing heavy lifting in the prose.)

### M8. The archive drops evidence the chain references

`export_segment` exports **only** the `events` table. After hot→warm,
the sealed segment file is deleted — and with it:

- `raw_payloads`: every `decision_requested` references its payload by
  `input_sha256`; the hash link stays verifiable but the payload itself
  is gone. "What did the vendor actually send?" becomes unanswerable
  for archived windows — an audit-completeness regression the design
  never names.
- `outbox` lifecycle rows (attempts, dead letters, `secondary_fired_at`):
  the warm summary folds only counts by type/disposition. The
  forward-retry evidence for old segments exists only in deleted files.

### M9. The "disposable" decisions VIEW has no disposal date

"Disposable by definition" — but `audit.py`, the platform read paths,
and the v0.1-compat projections still read through it, and there is no
named owner, no migration checklist for remaining readers, no dated
kill condition. Principal-mindset rule: a practice without a date is
permanent. The grep guard enforces "no UPDATE on events"; nothing
enforces "no new reads of the disposable VIEW."

### M10. Scale question is unasked (principal-systems done-checklist)

The single-writer RLock serializes all hot-path writes — fine at
design-partner volumes, and the p99<5 ms CI bound is real. But no
artifact asks the 10× question: at what event rate does the single
writer become the pipeline's bottleneck, and what is the plan then
(batch appends? writer sharding breaks the single chain — the
alternative space is unexamined). This is Type-2-reversible today;
naming the ceiling now is cheaper than discovering it during an
incident.

---

## 3. CONCRETE REVISION PROPOSALS

Ranked by value = (closes a guarantee-gap) × (falsifiability) − (build
cost). Each names what would verify it — per the swe-discipline rule,
a requirement without a named check is a wish.

### P1. Publish the tamper-evidence threat model as a Type-1 requirement doc (highest value)

**Rationale.** Aditya's multi-layer bar starts at requirements, and the
swe-discipline research names this the biggest gap class: no artifact
says what the customer must observe, how well, and how it is verified —
written before building. Every other proposal below is unanchored
without it: you cannot judge whether M1–M9 matter until the guarantee is
falsifiable. The doc names the adversaries (post-hoc file editor;
compromised writer; silent writer; curious-but-honest operator), and for
each: the detection mechanism, the detection time bound, the evidence
the customer holds, and what breaks the guarantee. It must state the
insider-writer limit plainly (M1) — the checkpoint.py docstring already
does; the product claim should match the mechanism.

**Verifies by:** the doc exists, is reviewed by the security principal,
and each adversary row has a named check (e.g., "post-hoc editor →
`python -m sentinel.verify` fails at seq N", "silent writer →
customer-visible checkpoint gap > 2 h"). Disagree-and-commit on the
insider-writer row: either accept the limit in writing or fund the
external-anchoring work (P6) that narrows it.

### P2. Wire the three orphaned controls into the runtime (near-zero cost, tested code)

**Rationale.** `CheckpointJob.run_periodic`, `Reaper.startup_sweep` +
`run_periodic`, and `RetentionJob.apply` (dry-run first) exist, are
unit-tested, and are never invoked. This is the highest
value-per-line-change in the domain: the Pair-B crash guarantee, the
hourly seal, and the retention policy go from prose to production.
Single-threaded owner per control (principal-governance); the
coordinator owns the thread lifecycle as the docstrings already assume.

**Verifies by:** an integration test per control — boot the engine,
kill it mid-decision, reboot, assert the reaper redrove the orphan
(fail-open page exists); run the checkpoint job against a failing sink,
assert the page after the bound; run the retention job in dry-run,
assert the plan without deletes. Plus runbook entries and a drill
(ops/drills) exercising each. Kill condition if the wiring proves the
controls were never needed: say so in writing, don't leave them
half-alive.

### P3. Unify the genesis rule — one chain-verification implementation

**Rationale.** M3 is a verifier that will cry wolf on every deployment
the day after the first roll — the exact decay the anti-theater audit
names. `retention.verify_chain` (linkage-based) is the correct
semantic; `sentinel/verify.py` should delegate to it (or share the
implementation), reading a rolled segment's first `prev_hash` as the
segment anchor and checking continuity against the ledger's recorded
predecessor head. While there: carry `segment_id` so verification
output names what it verified.

**Verifies by:** the `/tmp/evlog-repro` reproduction becomes a
regression test — roll, then `verify()` passes; then tamper with one
row, `verify()` fails naming the seq. Both verifiers agree on healthy
and on broken.

### P4. Drain-before-roll: the roll must not entomb live work

**Rationale.** M4 silently breaks I1/I2 — the domain's proudest
invariant — at every segment boundary. Options, in preference order:
(a) `seal_and_roll` refuses (pages) when live outbox rows exist in
`queued`/`in_flight`, and the roll is retried after drain; (b)
migrate live outbox rows into the new DB inside the roll transaction.
Additionally: the new segment's first checkpoint carries
`open_outbox_count`, and the river fold gains a documented
cross-segment mode (or an explicit, loudly-labeled limitation).
`seq`-per-segment ambiguity is fixed by namespacing tooling on
`(segment_id, seq)`.

**Verifies by:** a test that queues a page, rolls, and asserts the page
is still delivered/visible post-roll (or the roll refused and paged).
The ADR-022 watchdog and the river rule get cross-segment coverage or
an explicit limitation statement.

### P5. Expose the sealed chain on the platform read contract

**Rationale.** M5: the customer's only runtime "tamper-evident" view
is a derived chain that attests to nothing about the platform. Ship
the RFC `chain.js` anticipates: `GET /api/audit/chain` (versioned,
read-only, `mode=ro` like the rest of store.py) serving events with
their `row_hash`/`prev_hash` plus the checkpoint HMACs; the console
verifies the platform's signatures instead of deriving links, and the
panel label flips from "derived" to "platform-sealed."

**Verifies by:** the console chain panel renders the platform's seal
state; a tampered platform copy breaks the panel in-band (the
`verifyChain` seam already exists client-side — it just needs real
links). Contract-versioned per the API-layering research (additive
inside v1; the endpoint is new surface).

### P6. Chain checkpoints to each other; publish the customer verification workflow

**Rationale.** M6: add `prev_checkpoint_hmac` (and `segment_id`, M4) to
the checkpoint document and its HMAC input. A between-checkpoint
rewrite then breaks the sink-copy chain without any customer-side
bookkeeping beyond archiving the docs. Pair with the missing
operational artifact: a one-page customer runbook — what to archive,
what to check, how often, what a >2 h checkpoint gap means. Move the
sink-failure counter from memory into the log (read recent
`sink_push_ok=false` checkpoints at startup) so the 2-hour bound
survives restarts.

**Verifies by:** forge a history between two checkpoints with valid
per-doc HMACs; the sink-copy verification fails on the
`prev_checkpoint_hmac` link. The runbook is drill-tested (hand a new
operator the sink dir and the runbook; they detect the forgery).

### P7. Key lifecycle + actually invoke the GDPR path

**Rationale.** M7 has two halves. (a) Rotation: dual-key overlap
(verify with old-or-new during the window, seal with new), manifest
re-seal, and a compromise-response runbook — a Type-1 decision with a
written RFC, since it touches the trust root. (b) Wire
`salt`+`pii_fields` into `_promote_to_warm` (or delete the GDPR claim
from the module and the RFC — honest deletion beats decorative code).
Same for the "WORM" claim: either enforce it (immutable flag /
object-lock as a deployment requirement, checked at job start) or
downgrade the prose to "WORM-shaped."

**Verifies by:** a rotation drill (rotate, verify old manifests still
check, new exports seal with the new key); a warm export with
`pii_fields` set contains zero raw `alert_id`/`fingerprint` values
(test asserts this on the exported JSONL).

### P8. Archive completeness: export what the chain references

**Rationale.** M8: `export_segment` should include `raw_payloads` (keyed
by `input_sha256`) and the `outbox` lifecycle rows for the segment, or
the RFC must state explicitly what is dropped and why (the cold
summary's body-drop is already an explicit, defensible choice — extend
that honesty to the warm tier). Auditability that degrades silently
across tiers is the normalization-of-deviance pattern.

**Verifies by:** a warm-export round-trip test —
`decision_requested` in the archive resolves its raw payload; an old
segment's dead-letter outbox row is inspectable post-promotion.

### P9. Put a dated kill on the disposable decisions VIEW

**Rationale.** M9: name the owner, list the remaining readers, migrate
them, and set the removal date (suggest 90 days). Evolve the grep
guard from "no UPDATE on events" to "no new reads of the disposable
VIEW in production paths." A shim without a date is a second schema.

**Verifies by:** the VIEW is gone on the date or the slip is
disagree-and-committed in writing with a new date. The suite's grep
guard is the enforcement.

---

## Appendix: what I deliberately did not propose

- **Ed25519 for checkpoints.** The checkpoint.py docstring already
  records the honest analysis: asymmetric signatures don't fix the
  writer-holds-the-key problem. Proposing it would be refinement theater
  (principal-mindset restatement test: it changes the number — nicer
  crypto — not the belief). The belief-changing work is P1/P6.
- **Kafka-style infrastructure between stages.** The event log is
  audit-only by design (architecture-patterns §1.3 affirms this shape);
  nothing in this review suggests the log should become a state store
  or message bus.
- **Rewriting the chain construction.** `_row_hash` is sound for its
  stated purpose; the gaps are all in *wiring, lifecycle, and the
  guarantee's scope*, not in the hash function.

## Confidence

Forced probabilities (principal-mindset §7), on what was known when
(code read end-to-end, one empirical repro, zero production traffic
observed):

- M2 (orphaned controls): 95 — grep is decisive; the only uncertainty
  is whether a wiring path exists outside `src/`, `platform/`, `ops/`
  (I checked all three).
- M3 (genesis-rule disagreement): 98 — reproduced empirically.
- M4 (roll entombs live outbox rows): 90 — certain from code reading
  (`seal_and_roll` has no outbox check; all monitors read the live log
  only); the remaining 10 is whether some forwarder path I didn't read
  scans segment files (I read the forwarder's query surface via
  `due_outbox_rows`/`undelivered_outbox_rows`, both live-log-only).
- M1 (guarantee narrower than claim): 85 — the mechanism analysis is
  certain; the "claim" side is my reading of product prose, which the
  P1 doc would settle authoritatively.
- M5/M6/M7/M8/M9: 80–90 — code-grounded; M7's warm-path dead code is
  certain (read `_promote_to_warm`).

*Reviewer stance: the mechanism layer is strong and honestly
documented; the domain's failures are all in the layers Aditya named —
requirements (no threat-model requirement), architecture wiring
(controls without callers), and operations (no customer verification
workflow). The log keeps perfect evidence of a history nobody is
checking.*
