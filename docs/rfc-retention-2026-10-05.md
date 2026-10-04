# RFC: Audit Data Retention Policy (ADR-024 / O-1)

**Status:** DECIDED by governance panel, 2026-10-04 (Vault-led RFC per ADR-024).
**Type:** 1 in both directions — deleting is irreversible; keeping forever is unbounded liability.
**Panel:** Forge (engineering), Vault (security/trust), Pager (SRE domain), Tripwire (adversarial QA).
**Process:** disagree-and-commit. Real disagreement is recorded below; all four commit.

---

## 1. Position memos

### Forge (engineering)

- **Chesterton's fence:** the hash-chained event log is the product. Never `DELETE` rows
  from a chained log — time-partition into segments and chain the segments
  (`genesis_prev_hash = previous segment head hash`). The fence exists because a broken
  chain voids every audit the product ever produced.
- **Global over local:** do not solve disk-full by buying a bigger disk. Bound growth with
  self-calibrating tiers: the retention job measures its own bytes/day and derives the hot
  window from the disk budget. A bigger disk then *automatically* lengthens the window —
  no retuning, no config change.
- **Execution doctrine:** the retention job must be a dumb, idempotent, dry-runnable job —
  the 10-line cron that works for two years beats the distributed lifecycle manager.
  Shadow it (dry-run) before it ever deletes anything.
- **Pre-mortem top 3:** (1) job prunes before the archive verifies → data loss → hard
  ordering: export → verify chain → seal manifest → checkpoint → prune, in that order,
  never reordered; (2) the job runs while the disk is full → the job itself needs disk to
  work → below 5% free the job goes read-only and pages instead of writing; (3) backdated
  events corrupt window math → tier boundaries on `seq`, never on wall-clock `ts`.
- **Position:** hot 180d full-fidelity, volume-clamped by measured growth; warm 365d;
  cold 7y summaries.

### Vault (security/trust)

- **Non-negotiable floors:** ≥180 days full-fidelity for **suppress** dispositions (covers
  the 30-day replay, 90-day attestations, 180-day TTLs); ≥60 days full-fidelity for **all**
  decisions. Deletion is a logged event, never a silent prune.
- **GDPR vs immutability:** pseudonymization-on-delete. On an erasure request the
  warm/cold export pipeline rewrites PII-bearing fields to salted-hash tombstones
  (`redacted:<sha256(salt|value)>`); the event row is never deleted and the chain stays
  continuous; the redaction itself is a logged event (checkpoint → redaction record).
  Hot-window erasure is covered by the legal-obligation basis (SOC 2 evidence) and
  tombstoned at export.
- **Evidence — one real enterprise retention clause:** PagerDuty documents **12 months of
  audit record retention** for its Audit Trail feature (enterprise plan). Source:
  https://github.com/ethanolivertroy/grclanker/blob/HEAD/specs/pagerduty-sec-inspector.spec.md
  (Control 12: "PagerDuty documents 12 months of audit record retention"). SOC 2 Type II
  auditors expect evidence retained for the audit period — in practice 6–12 months, usually
  1 year. Sources: https://scopeforged.com/blog/compliance-as-code ("Retained for the audit
  period (usually 1 year for SOC 2)"), https://www.trycomp.ai/hub/soc-2-checklist-for-saas-startups
  ("logs should be kept for at least 6-12 months").
- **Priced WORM/archive option:** S3 Object Lock (WORM, compliance mode) + Glacier Deep
  Archive ≈ **$0.00099/GB-month** (us-east-1, 2026 pricing), 180-day minimum storage
  duration. Sources: https://dev.to/medampudi/the-s3-cost-occupation-playbook-a48
  (playbook table, accessed 2026-06-18), https://awsglossary.org/terms/s3-glacier
  (four-dimensional pricing model; Deep Archive ~96% cheaper than S3 Standard).
  Worked example: 10k decisions/min ≈ 21.6 GB/day (measured §3); one year of full-fidelity
  warm ≈ 7.9 TB ≈ **$7.80/month** in Deep Archive. Retention is not a cost problem; it is
  a discipline problem.
- **EU residency (O-3, answered jointly):** all three tiers stay in the customer's region.
  EU customers get EU-region WORM buckets; the job fail-closes on region mismatch (refuses
  a cross-region sink rather than writing to it).
- **Disagreement (recorded):** Vault wanted 7-year *full-fidelity* for suppress dispositions
  (regulated buyers: 3–7 years). Commits to summaries-by-default for cold, with
  full-fidelity cold as a per-customer enterprise tier carrying explicit liability
  acceptance — because a 7-year full-fidelity body store is also a 7-year PII liability
  under GDPR's storage-limitation principle.

### Pager (SRE domain)

- **True objective:** "an auditor can replay any suppress decision from the last N days
  without the company going down." Proxy trap: "disk is 60% full" is not the objective —
  the objective is replayability under bounded disk.
- **The 10k/min arithmetic is the finding** (§3): 21.6 GB/day ⇒ 180-day hot = 3.9 TB.
  Even at 1k/min, 180-day hot = 389 GB — neither fits a 100 GB VM disk. Therefore the hot
  window MUST be self-calibrating:

  `hot_window = clamp(target=180d, floor=60d, disk_budget_bytes / measured_daily_bytes)`

  measured from the deployment's own statistics (self-calibration, not month-tuning).
  If `disk_budget / daily < 60d` the deployment is **under-provisioned: page the operator
  (provisioning incident), keep serving, never silently shorten below the floor.**
- **The retention job gets its own SLO:** no successful run in 26h ⇒ page. A retention
  job that silently stops is PM-3 (the disk-full-at-03:12 pre-mortem) on a timer.
- **Ordering law:** archive-verify-before-prune. The prune step is last, and it is the only
  step that deletes.

### Tripwire (adversarial QA)

- **Measured (this panel, live code):** 755.7 bytes/event steady-state on SQLite
  (envelope + hash chain + indexes). A suppress decision = 2 events ≈ **1.51 KB**; a paged
  decision = 3 events ≈ **2.27 KB**. Basis: 2,000 representative events written through
  the real `EventLog` write path.
- **Disk-full projections (100 GB disk):**

  | rate | GB/day | days to full | interim 100 MiB guard fires |
  |---|---|---|---|
  | 1k decisions/min | 2.16 | ~46 | ~1.1 h |
  | 10k decisions/min | 21.6 | ~4.6 | ~7 min |

  The interim Type-2 guard (already on main, #37) is loud and early — correct as an
  interim. It is not the answer; the tiers are.
- **Adversarial cases:** (1) log-flood to force early prune → floors are floors: the job
  never prunes below the 60-day floor under pressure; (2) clock skew / backdated events →
  seq-based boundaries; (3) broken chain on export → refuse to prune + page (fail toward
  the human, never toward silent data loss); (4) attacker deletes the warm archive →
  the live chain's checkpoint events name every archived segment's head hash and HMAC —
  deletion is detectable, and re-export from hot is possible inside the hot window.
- **Kill condition for this RFC:** if production payloads measure >2× the 756 B/event
  figure, recompute every tier — the arithmetic is the decision.

---

## 2. The decision

### Tiers

| Tier | Contents | Duration | Store |
|---|---|---|---|
| **Hot** | full-fidelity event log, queryable | `clamp(180d, floor 60d, disk_budget/measured_daily)` | local SQLite segments |
| **Warm** | full-fidelity, hash-chained JSONL export + HMAC manifest | 180d → 365d | WORM object store (S3 Object Lock / GCS bucket lock / Azure immutable blob) |
| **Cold** | hash-chained **summaries only** (daily disposition digests + segment head hashes; never raw bodies) | 1y → 7y | WORM archive (Glacier Deep Archive class) |

- Per-customer tier overrides are the enterprise surface (e.g. full-fidelity cold to 7y
  with explicit liability acceptance). Defaults above are the floor, not the ceiling.
- The existing `checkpoint` event vocabulary carries the archive linkage
  (`head_hash`, `event_count`, `sink_uri`, `hmac_hex`, `sink_push_ok`) — **no new event
  types**; the Type-1 event vocabulary stays frozen.
- Segments chain: each new segment's `genesis_prev_hash` = previous segment's head hash.
  The whole history is verifiable from any segment via the ledger.

### Disk-full bound

`hot_bytes ≤ disk_budget_bytes + 1 day of volume` (segment roll is daily; overshoot is
bounded by one segment). The interim 100 MiB watermark guard stays as the Type-2 backstop.
The bound is *computed*, not hoped: the job derives `measured_daily_bytes` from the DB's
own stats every run.

### GDPR vs immutability

Pseudonymization-on-delete, never silent row deletion:
- Erasure requests tombstone PII-bearing fields (deployment-configured; default
  `alert_id`, `fingerprint`) in warm/cold exports: `redacted:<sha256(salt|value)>`.
- Archive manifests record `pseudonymized_fields`; per-event hashes are recomputed over
  the tombstoned form; segment head-hash continuity is preserved.
- The redaction is itself a logged event (checkpoint → redaction record).
- Hot-window data is retained under the legal-obligation basis (SOC 2 evidence) and
  tombstoned at export.

### EU residency

All tiers in-region. EU customers ⇒ EU-region WORM buckets; the job fail-closes on region
mismatch. No cross-border replication of event bodies.

---

## 3. Type labels

| Item | Type | Why |
|---|---|---|
| Tier floors (180d suppress / 60d all / 365d warm / 7y cold-summary) | **1** | auditor-facing promise; shortening retroactively breaks the evidence contract |
| Self-calibration formula + 60d floor | **1** | the formula IS the promise; silent re-tuning is how evidence dies |
| Pseudonymization-on-delete rule | **1** | irreversible once applied; chain-continuity law |
| EU in-region rule + fail-closed region check | **1** | contractual (DPA); a cross-region write is a breach |
| Storage backend (S3 vs GCS vs local dir) | 2 | sink interface; swappable without breaking the chain |
| Retention job cadence | 2 | operational |
| Interim 100 MiB disk guard | 2 | already shipped (#37); reversible |
| Cold-summary aggregation granularity (daily) | 2 | summaries are derivative; re-aggregatable from warm inside 365d |

---

## 4. Alternatives rejected

1. **Forever-retention in SQLite** — unbounded liability; PM-3 (disk-full at 03:12, a week
   of silent audit gap) already priced this. Rejected.
2. **Silent TTL prune (`DELETE WHERE ts < …`)** — breaks the hash chain; destroys the
   product (the audit trail IS the product). Rejected — "never hard delete" stands.
3. **30-day hot to fit small disks** — fails the 30-day replay / 90-day attestation /
   180-day TTL evidence windows Vault requires. Rejected.
4. **Vendor log sink as primary (Datadog/Splunk)** — ₹0 budget; and the chain must be
   verifiable by the *customer*, not just the vendor. Rejected as primary; acceptable as
   an additional customer-managed sink.

---

## 5. What would change it

- Measured growth differs >2× from 756 B/event on production payloads (recompute).
- A signed enterprise contract with shorter retention **and explicit customer liability
  acceptance** (the ADR-024 escape hatch).
- A regulator ruling that summaries do not satisfy the evidence requirement (then cold
  becomes full-fidelity by default).
- Evidence the checkpoint-linked archive is never actually replayed in audits (then warm
  export format simplifies, but tiers stand).

---

## 6. Implementation (this lane)

`src/sentinel/retention.py` + `tests/test_retention.py`:

- `RetentionConfig`: `hot_days=180`, `hot_floor_days=60`, `warm_days=365`,
  `cold_years=7`, `disk_budget_bytes` (default 50 GiB), per-customer overrides,
  `pii_fields`, `region`.
- `measure_daily_bytes(db_path)`: self-calibration from the DB's own stats
  (size ÷ days spanned by seq range; falls back to bytes/event × recent event rate).
- `hot_window_days(config, daily_bytes)`: the clamp; returns
  `(window_days, under_provisioned_bool)`.
- `verify_chain(db_path)`: recompute the hash chain read-only; the job refuses to prune
  on failure.
- `export_segment(...)`: chain-verified export to JSONL + HMAC-SHA256 manifest
  (`manifest.json`: segment id, seq range, head hash, event count, hmac, sink uri,
  pseudonymized fields).
- `pseudonymize_record(...)`: salted-hash tombstones for configured PII fields.
- `SegmentLedger`: JSON ledger of sealed segments (id, seq range, head hash, state
  hot/warm/cold, archived_at, sink_uri, checkpoint_seq).
- `RetentionJob.apply(dry_run=True)`: the tier state machine — hot→warm (export, verify,
  checkpoint into live log, mark), warm→cold (compact to daily summaries, delete warm
  file, ledger-log the deletion), cold expiry (only with explicit acceptance flag).
  Dry-run is the shadow mode: full plan, zero deletes.
- `EventLog`: `genesis_prev_hash` ctor param (default `GENESIS`, backward compatible) +
  `head()` accessor, enabling `seal_and_roll()` (daily segment rotation).
- Deletion is always ledger-logged + checkpointed. Never silent.

Built in this lane; reviewed under the 72-hour wave; PR targets `main`, **not merged**
per lane rules.
