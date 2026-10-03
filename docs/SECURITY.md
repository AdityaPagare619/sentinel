# Sentinel — Security Design

**Lane:** VAULT (security lead) · Saturday wave · 2026-10-03
**Branch:** `lane/vault-hardening`
**Status:** DESIGN — feeds ADR-005 (proposed, **NOT decided** — Aditya decides)
**Builds on:** `research/security-privacy/2026-10-02-webhook-audit-precedents.md` (2026-10-02 precedent sweep)

Sentinel sits in the paging path. A forged alert pages a human at 3am; a
suppressed alert silences one. This doc is the security lawbook: the threat
model, the webhook-verification design, the secrets rules, and the audit-log
integrity design. Every rule here is fail-closed — when in doubt, Sentinel
pages, never swallows.

---

## 1. Threat model (lite)

Three scenarios. Each names what breaks, who can do it, and what the blast
radius is.

### T1 — The receiver is fooled

**Attack:** An attacker sends a crafted alert to our receiver endpoint.
Two directions of harm:

- **False page (nuisance → outage):** forged `severity=critical` alerts flood
  the on-call rotation. Cost: engineer trust destroyed, pager fatigue in a
  night, and our flagship promise ("kills pager noise") dead on arrival.
- **False silence (dangerous):** forged `alert.resolved` / cancel events
  silence a real incident. A genuine SEV1 never pages. This is the worst case
  in the model — a paging gate that can be *silenced remotely* is worse than
  no gate at all.

**Attacker capability assumed:** can reach the endpoint over the internet,
can replay observed deliveries, can attempt timing attacks against naive
string comparison. **Not assumed:** ability to break TLS or SHA-256.

**Design consequence:** signature verification is **mandatory and fail-closed**
on every inbound delivery (§2). No unsigned path exists — not for health
checks, not for local dev, not for "just this once" debugging.

### T2 — The audit log is tampered

**Attack:** Someone with access to the machine (a disgruntled insider, a
compromised deploy script, a bug) edits or deletes rows in the decision audit
log — the record of every page/suppress decision.

**Why it matters:** the audit log is the product. The calibration dashboards,
the expected-cost reports, the 2-week shadow-pilot savings proof — all of it
is downstream of "what did we decide and why." If rows can be rewritten
after the fact, every report is deniable and every savings claim is
unverifiable. Worse: an attacker who can edit the log can *hide* a false
silence (T1's worst case) — suppress a real SEV1, then erase the suppress
row.

**Design consequence:** hash-chained rows (§4), DB-level mutation triggers,
periodic out-of-band checkpoints. Tampering must be *detectable and
pinpointable to the exact row*, even by direct SQL edits bypassing the app.

### T3 — A key leaks

**Attack surface:** three secret classes live around Sentinel:

1. **Receiver signing secrets** (per alert source) — leak lets an attacker
   forge alerts (→ T1).
2. **TypeSafe/Jev API key** — leak lets an attacker burn inference budget
   and, worse, interrogate our triage model for free. (The key also crossed
   chat once — see honest flag in the campaign log; rotation hygiene applies.)
3. **Paging-provider API key** (PagerDuty/Opsgenie) — leak lets an attacker
   page or acknowledge incidents directly, bypassing the gate entirely.

**Design consequence:** secrets live in env/secret-store only, never in
repo, logs, PRs, or chat (§3). Per-source secrets so a leak has minimum blast
radius. Rotation is a documented procedure, not an emergency rewrite.

### Explicitly out of scope (honest)

- We do not defend against a **full-machine compromise** where the attacker
  can rewrite the entire audit chain *and* intercept the out-of-band
  checkpoints — at that point the endpoint itself is untrusted and the answer
  is incident response, not cryptography. Our chain defeats the realistic
  cases: casual DB edits, partial rewrites, row deletion, tail truncation.
- We do not defend against **TLS interception** — that's TLS's job; we
  require HTTPS in production and refuse to serve webhooks over HTTP.
- NIST guidance was not consulted in the precedent sweep (queued follow-up).

---

## 2. Webhook verification design — evidence for ADR-005

> ADR-005 is **proposed, not decided**. This section is the design evidence
> Aditya evaluates. No implementation may claim ADR-005 compliance until
> Aditya approves it.

### 2.1 The contract

Every inbound delivery to the receiver must carry a signature. Sentinel
speaks **HMAC-SHA256**, the convergent industry standard:

| Direction | Sender | Header | What is signed | Source |
|---|---|---|---|---|
| Outbound (their → our adapter) | PagerDuty v3 webhooks | `X-PagerDuty-Signature` = `v1=<hex>` (may be comma-separated during rotation; accept if ANY matches) | raw request body, per-source signing secret | [qhook webhook-verification guide](https://github.com/totte-dev/qhook/blob/HEAD/docs/guides/webhook-verification.md); [callhook PagerDuty adapter](https://github.com/isundram/callhook/blob/HEAD/integrations/pagerduty/README.md); [research brief 2026-06-13](https://github.com/bicameralai/bicameral-integrations/blob/HEAD/docs/research-brief-gitlab-sentry-pagerduty-2026-06-13.md) — all accessed 2026-10-03 |
| Inbound (their platform → our receiver) | Sentinel's own scheme | `X-Sentinel-Signature` = `sha256=<hex>` | raw request body, per-source secret we provision | Our contract (this doc) |
| Timestamped variants | Slack/Datadog-style | signed string `timestamp:raw_body`, header carries `t=<ts>` or separate timestamp header | raw body + timestamp, 5-min replay window | [incident-copilot PagerDuty docs](https://github.com/jianxux/incident-copilot/blob/HEAD/docs/user-guide/integrations/pagerduty.md) (HMAC-SHA256, invalid → 401), [opnform webhook-security](https://github.com/opnform/opnform/blob/HEAD/docs/api-reference/integrations/webhook-security.mdx) — accessed 2026-10-03 |

**Opsgenie note (honest):** this pass did not surface a public Opsgenie
HMAC webhook-signing scheme comparable to PagerDuty's. For Opsgenie-class
sources (API-key-over-TLS only), the design treats them as *weaker auth* and
requires one of: (a) the source routed through our signing relay that re-signs
deliveries under `X-Sentinel-Signature`, or (b) explicit risk acceptance logged
in `ops/decision_log.md`. We never pretend a bearer header over TLS is
equivalent to HMAC — a leaked header value allows arbitrary spoofing
([fauward audit precedent](https://github.com/isaacpyo/fauward/blob/HEAD/docs/CONSOLE_REMEDIATION_TRACKER.md), accessed 2026-10-03).

### 2.2 The five hardening rules (non-negotiable)

1. **Raw body before parse.** The signature is computed over the *exact
   bytes received*, not over re-serialized JSON. Parsed-then-resigned bodies
   drift (key order, whitespace) and break or weaken verification — the
   practitioner consensus is explicit about reading raw bytes first.
   Pipeline order per request: `read raw bytes → verify signature → then parse`.
2. **Constant-time signature comparison.** Use `hmac.compare_digest`
   (Python) or equivalent — never `==` — so a remote attacker cannot
   brute-force the MAC byte-by-byte via timing.
3. **Empty secret refuses startup.** If a source has no signing secret
   configured, the receiver **refuses to start that source's route** (fail
   closed at boot), and any request to a secretless route is rejected.
   "No secret, no check" turns a deployment mistake into an open endpoint —
   we turn it into a loud, immediate failure instead.
4. **5-minute timestamp tolerance on timestamped schemes.** For schemes that
   sign `timestamp:body` (Slack/Datadog-style), reject any delivery whose
   timestamp is more than 300s from server time. This bounds replay attacks.
   Timestamp-less schemes (PagerDuty-style `v1=<hex>` over body) are accepted
   as the provider's documented contract; replay safety there comes from
   idempotent ingestion (§2.3).
5. **Per-source secret rotation, zero downtime.** The verifier accepts a
   *set* of currently-valid secrets per source and passes if ANY matches
   (PagerDuty itself does exactly this during rotation). Rotation procedure:
   add new secret to the set → roll out to the sender → remove old secret.
   No flag day, no dropped deliveries.

### 2.3 Ingestion robustness (security-adjacent, required)

- **Fast ACK:** return 200/202 *before* heavy triage work (providers expect
  ACK within ~5s). Verification happens *before* the ACK, not after — a
  forged body must never be ACKed as accepted.
- **Idempotent ingest:** dedupe on the provider's delivery/event ID. A
  replayed-but-valid delivery returns 200 with the existing decision — it
  cannot double-page or double-suppress.
- **Structured failure logging:** log `webhook_signature_invalid` events with
  delivery ID, source, and reason — never the secret, never the full
  signature value.

### 2.4 What "verified" means, precisely

A delivery is accepted **iff** all of: (a) the expected signature header is
present; (b) the HMAC over the raw body with a currently-valid per-source
secret matches under constant-time comparison; (c) for timestamped schemes,
`|now - ts| ≤ 300s`; (d) the route's secret set is non-empty. Otherwise: HTTP
401 (invalid) or 403 (missing), **no payload parsed, no decision recorded,
no page sent**. The rejection itself is an audit event (`webhook.rejected`)
so a signature flood shows up in the log as what it is.

---

## 3. Secrets hygiene

### 3.1 What lives where

| Secret | Lives in | Never in |
|---|---|---|
| Per-source receiver signing secrets | env var `SENTINEL_WEBHOOK_SECRET_<SOURCE>` (or the design partner's secret store; see BYOK below) | repo, logs, PRs, chat, error messages |
| TypeSafe/Jev API key | env var, injected at process start; held in memory only | repo, logs, PRs, chat — *ever again* |
| PagerDuty/Opsgenie API key (forwarding) | env var or secret store | same as above |
| Audit chain checkpoints (§4.4) | public by design (a hash commits to history, it doesn't reveal it) | — |

Rules:

- **Env or secret store, never repo.** No secret value is committed, printed,
  or pasted. A grep for secret-shaped values (`whsec_`, `sk-`, 32+ hex
  chars in config) is a CI gate candidate (post-Sunday).
- **Log the key ID, never the key.** Structured logs reference
  `secret_id` / `key_fingerprint` (first 8 hex of SHA-256 of the value) so
  rotation events are traceable without exposure.
- **Minimum lifetime in memory.** Secrets are read at startup into the
  verifier; no secret is re-read from disk per request, and none is written
  to swap-friendly debug dumps.
- **Rotation on suspicion, not on schedule.** Any suspected leak → rotate
  immediately via the dual-secret window (§2.2.5). The honest flag stands:
  the TypeSafe key traveled through chat once — Aditya rotates it when
  convenient, and we treat the pre-rotation key as burnable, never
  load-bearing.

### 3.2 The BYOK path for design partners

Design partners bring their own keys; we never custody what we don't need.

- **Their TypeSafe/Jev key:** stays in *their* environment/secret store. Our
  self-hosted receiver reads it from *their* env. We never see it, never log
  it, never transit it through our systems. Our hosted-preview path (if any)
  runs against *our* key against *synthetic* alerts only — never partner data.
- **Their PagerDuty/Opsgenie API key:** same rule. The forwarder uses the key
  the partner injected; the minimum scope is paging-only.
- **Receiver signing secrets:** we generate one per source
  (`whsec_<32 random bytes>`), hand it to the partner once over a secure
  channel, and store only the fingerprint. The partner configures their
  alerting platform (or our relay) to sign with it.
- **What this buys the buyer:** Sentinel can't leak what it never holds.
  The BYOK story is the answer to "why should we trust your startup with our
  pager keys" — because we architecturally *can't* lose them.

### 3.3 Incident response (key leak)

1. Rotate via the dual-secret set — old and new both valid during the
   window, zero downtime.
2. Emit an audit row (`secret.rotated`, with key fingerprint, actor, reason).
3. Invalidate the old secret everywhere (sender config, env, stores).
4. Post-mortem: how did it leak, what accepted it while leaked.

---

## 4. Audit-log integrity

The decision audit log is append-only by design. Append-only is necessary
but not sufficient: anyone with DB access can `UPDATE` or `DELETE` rows in a
plain table without leaving a trace. This design makes tampering
**detectable, pinpointable, and attributable** — using hash chaining with
practitioner-proven precedents.

### 4.1 The concrete mechanism

Each audit row commits to the row before it:

```
row_hash = SHA-256( prev_hash || canonical(row_without_hashes) )
prev_hash = row_hash of the immediately preceding row (by id)
genesis: prev_hash = 64× "0" for the first row
```

Convergent precedent (all accessed 2026-10-03):

- [ember issue #8](https://github.com/jusso-dev/ember/issues/8) — `row_hash =
  sha256(prev_hash || canonical_json(row))`, genesis `0x00…`, nightly
  verifier walk emitting control-plane events on mismatch.
- [promptzero #139](https://github.com/xunholy/promptzero/commit/f073c51ef123ceb98e861a980968243e2980c1f3) —
  **length-prefixed field encoding** so no value can forge a field boundary;
  chain head held in memory + write mutex serializing read-head → compute →
  insert; honest scope: *tamper-evidence against casual DB edits, NOT defense
  against full-chain rewrite or tail truncation without an out-of-band
  anchor*.
- [lawyer-assistant](https://github.com/asalamat/lawyer-assistant/commit/22d128cc811c7caf935f27c1d00978ad33c78207) —
  direct-SQL tamper detected and **pinpointed to the exact row**; verified
  live against a 76-row history.
- [fhir-sqlite CHANGELOG](https://github.com/fhir-rust/fhir-rust/blob/HEAD/fhir-sqlite/CHANGELOG.md) —
  DB-level `BEFORE UPDATE OR DELETE` trigger refusing mutations ("rewriting
  history now requires deliberately disabling a trigger") + a
  `verify-audit` CLI exiting nonzero on a break.

Our design adopts these lessons (four verified precedents; a fifth,
mcp-coordinator, was cited in error with a wrong URL and has been removed —
see review note on PR #8):

1. **Canonical encoding with length prefixes** (promptzero's lesson): each
   field encoded as `len || bytes` before hashing — no field-boundary
   forgery, no JSON key-order games.
2. **Hash the timestamp too** (our own addition — timestamps are part of the
   threat model):
   `created_at` is part of the hashed payload so timestamps can't be
   silently rewritten either.
3. **Atomic tip read + insert**: the chain tip is read and the row inserted
   in one transaction (or under one write mutex for single-process SQLite)
   so concurrent writers can't fork the chain.
4. **DB-level mutation triggers**: `BEFORE UPDATE` / `BEFORE DELETE`
   triggers `RAISE(ABORT, 'audit log is append-only')` — the app has no
   UPDATE/DELETE path *and* the database refuses them. Rewriting history
   requires deliberately disabling a trigger, which is itself an event.
5. **Out-of-band checkpoint anchor** (§4.4): the chain head is periodically
   signed and published to a separate sink, defeating tail-truncation
   (deleting the newest rows), which no in-band chain can catch alone.

### 4.2 Schema (proposed)

```sql
CREATE TABLE decision_audit (
  id            INTEGER PRIMARY KEY,
  ts_utc        TEXT    NOT NULL,   -- ISO-8601, part of the hash
  source        TEXT    NOT NULL,   -- which alert source
  delivery_id   TEXT    NOT NULL,   -- provider delivery/event id (idempotency key)
  alert_key     TEXT    NOT NULL,   -- dedupe key
  disposition   TEXT    NOT NULL,   -- page | suppress | escalate
  confidence    REAL    NOT NULL,   -- Jev calibrated confidence
  reason_json   TEXT    NOT NULL,   -- canonical JSON, sorted keys, explicit nulls
  prev_hash     TEXT    NOT NULL,   -- hex SHA-256 of previous row's row_hash
  row_hash      TEXT    NOT NULL    -- hex SHA-256(prev_hash || canonical(fields))
);
CREATE TRIGGER decision_audit_no_update BEFORE UPDATE ON decision_audit
  BEGIN SELECT RAISE(ABORT, 'decision_audit is append-only — UPDATE not permitted'); END;
CREATE TRIGGER decision_audit_no_delete BEFORE DELETE ON decision_audit
  BEGIN SELECT RAISE(ABORT, 'decision_audit is append-only — DELETE not permitted'); END;
```

Genesis row: `prev_hash = '0' * 64`. Pre-chain rows (if any exist at
migration time) are backfilled in id order from the genesis hash and marked
`legacy` in the verifier — forward-evidentiary, honestly labeled.

### 4.3 What we verify, and when

| When | What | On failure |
|---|---|---|
| **On every write** | tip check: the row we chain against is still the chain tip (guards concurrent-writer forks) | abort the insert, retry once, then audit-event + alert |
| **Nightly (cron)** | full chain walk: recompute every `row_hash` from genesis, confirm `prev_hash` links | control-plane alert + `audit.integrity_break` row (chained *after* the break point is recorded, so the alarm itself is sealed) |
| **On demand** | admin-only `verify` (CLI now, endpoint later): returns `{verified, total_rows, first_bad_id, head_hash}` | surfaced to operator; non-admins get 403 |
| **At every checkpoint** | recompute head hash before signing it (§4.4) | checkpoint refused; integrity incident declared |

The verifier **pinpoints the first broken row** — not just "something is
wrong." That row ID is the start of the forensic window.

### 4.4 Checkpoints: defeating tail-truncation

An in-band chain cannot detect deletion of the *newest* rows (the remaining
chain still verifies). The answer is an out-of-band anchor:

- Every N decisions (and at least hourly), the chain head
  `(head_id, head_hash, ts)` is signed with a checkpoint key and published
  to a **separate sink** the DB writer cannot silently rewrite: the
  control-plane log today, an append-only cloud object / email to the
  operator tomorrow.
- A verifier compares the DB's current head against the latest checkpoint.
  If rows are missing from the tail, the head won't match the checkpoint —
  deletion is detected even though the remaining chain is internally
  consistent.
- Checkpoint publication is itself an audit event (`audit.checkpoint`).

### 4.5 Honest limits (stated plainly, as the precedents do)

- The chain proves **tamper-evidence against direct DB edits** (UPDATE,
  DELETE, field rewrite, reorder, forged insert) — pinpointed to the row.
- It does **not** prove against an attacker who rewrites the *entire* chain
  in place *and* controls the checkpoint sink — that's a full-machine
  compromise (§1, out of scope).
- Timestamps are integrity-protected by inclusion in the hash, but *clock*
  trust is the host's job (NTP); we log clock-sync state at startup.
- Pre-chain legacy rows are forward-evidentiary only; the verifier says so
  explicitly.

---

## 5. Creative application — why OUR audit log beats a plain append-only file

**The concrete mechanism.** A plain append-only file (or table) proves
nothing: anyone with file access edits it silently. Our log seals every
page/suppress decision into a SHA-256 chain where each row cryptographically
commits to the row before it, encoded with length prefixes so no field can be
forged, guarded by database triggers that *refuse* UPDATE/DELETE, and
anchored by signed hourly checkpoints published outside the database so even
deleting the newest rows is caught. One command verifies the whole history
and points at the exact row where tampering starts.

**The one-line buyer story:**

> "Every page-or-suppress decision is sealed into a tamper-evident chain —
> edit any entry and the seal breaks at that exact row; delete the newest
> rows and the signed checkpoint catches it. One click verifies the whole
> history."

Why this wins deals: the shadow pilot's savings report, the calibration
dashboards, the compliance answers (SOC 2 CC7.2 / ISO 27001 A.12.4 evidence)
all rest on this log. A buyer doesn't have to trust our math — they can
*verify our history*. The trust layer is not a claim; it's a checkable
artifact.

---

## 6. Sources

Precedent base: `research/security-privacy/2026-10-02-webhook-audit-precedents.md`
(sweep of 2026-10-02). Live sources re-verified / added 2026-10-03:

- PagerDuty v3 `X-PagerDuty-Signature` (`v1=<hex>` HMAC-SHA256 over raw body,
  multi-signature rotation): qhook guide, callhook PagerDuty adapter,
  bicameral research brief (2026-06-13).
- HMAC-SHA256 verification patterns, 401-on-invalid, raw-body-first:
  incident-copilot PagerDuty integration docs; opnform `webhook-security.mdx`;
  GitHub official webhook validation docs (test vector `secret: It's a Secret
  to Everybody`, `payload: Hello, World!` → `757107ea…`).
- Shared-secret-vs-HMAC audit precedent (PagerDuty/Persona/DocuSign P0 fix,
  "reject on missing/invalid signature; reject on timestamp skew > 5
  minutes"): fauward console remediation tracker.
- Hash-chained audit logs: ember #8, promptzero #139,
  lawyer-assistant (live-verified tamper detection), fhir-sqlite CHANGELOG
  (DB triggers + `verify-audit`).
- Open question queued: Opsgenie public HMAC scheme (not found this pass —
  treated as weaker auth, §2.1); NIST webhook guidance (not consulted).

## 7. Decision log linkage

- Feeds **ADR-005** (webhook auth hardening checklist → receiver spec):
  PROPOSED. Aditya decides.
- Feeds audit-log implementation lane: schema §4.2 + verifier §4.3 are the
  build spec once ADR-005 (and the audit ADR, if any) are approved.
- BYOK posture (§3.2) is a standing product promise — no ADR needed, but
  any deviation (us ever custodied partner keys) requires a new ADR.
