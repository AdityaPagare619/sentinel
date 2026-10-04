# RFC: Sentinel Architecture Advancement — v0.1 → Enterprise-Grade

**Status:** PROPOSED (design doc, not code; nothing here is built until a lane builds it)
**Lane:** L2 — Architecture Advancement · **Date:** 2026-10-04
**Parent:** `ARCHITECTURE.md` (v0.1, frozen 2026-10-02) — this RFC is what v0.1 grows into
**Process:** principal-systems output contract. Every decision carries a **Type 1 / Type 2**
label with justification, alternatives considered AND rejected with reasons, the constitution
clauses that bind, and a written pre-mortem (§10). Disagree-and-commit applies after Petu's
ratification; until then everything here is PROPOSED.

**Note on the v0.1 diagram:** ARCHITECTURE.md §2 still shows a "SQLite audit" box. ADR-011
(ratified) replaced it with the append-only hash-chained event log with hourly
customer-sealed HMAC checkpoints. This RFC treats the event log as the durable-truth
primitive and never as "SQLite".

**Decision-type convention used throughout:** *Type 1* = irreversible or very costly to
reverse (data layout, public contracts, tenancy boundaries, durability promises) — demands
RFC rigor and slow commitment. *Type 2* = reversible (thresholds, replica counts, syntax
choices) — decide fast, roll back if wrong. A Type label without justification is a Type-2
default waiting to become a Type-1 disaster.

---

## 0. What this RFC is and is not

**Is:** the enterprise-architecture design layer above the v0.1 module layout — tenancy,
topology, partitioning, extensions, config/secrets, DR, observability, API evolution, and
fate domains. Concrete enough to build from: contracts, schemas, budgets, and protocols are
specified; code is not written.

**Is not:** a v0.1 rewrite. The v0.1 kernel (receiver → correlator → gate → forwarder, the
fail-open guarantee, the triple lock, the event log, the outbox) is the load-bearing core
and stays. This RFC adds the *boundaries around* that core that make it deployable as a
real product. §12 maps every v0.1 section to "stays" or "changes" explicitly.

**Honesty boundary** (extends DR-7 / the 2026-10-03 panel note): nothing in this RFC may be
described — in docs, demos, or buyer conversations — as a property of the system until a
lane implements it and the ratification checklist in §13 closes. An adopted design is not a
shipped property.

---

## 1. Tenancy model

### 1.1 The decision

**Adopt the bridge model as the standing tenancy architecture:**

- **Control plane (shared, pooled):** tenant lifecycle, config bundles, threshold
  governance (ADR-022), shadow/simulation, platform read API, billing. One deployment,
  multi-tenant, hard per-tenant quotas.
- **Data plane (tiered):** the paging path (ingress → decide → relay) runs **pooled by
  default** with logical isolation, and **siloed for enterprise/regulated tenants** as a
  paid tier — including full single-tenant stacks for air-gap buyers.
- **Self-hosted distribution** is a first-class offering, not a fork: the same deployable
  images/binaries, single-tenant by construction, with the control plane optionally
  connected (hybrid) or absent (offline).

This follows the AWS SaaS Factory taxonomy (silo / pool / bridge) and its standing
guidance: **default pool → bridge for noisy/compliance tenants → silo only for
premium/regulated tiers** ([source](https://github.com/rodrigo-mendes/agents-factory/blob/HEAD/StoryBeat/.claude/skills/architecting-aws-saas-multitenant/SKILL.md),
[AWS guidance](https://aws.amazon.com/solutions/guidance/multi-tenant-architectures-on-aws/)).

**Type 1.** Once two tenants' alert data shares a store, un-mixing it is a migration, not a
config change; once an enterprise buyer's auditors see row-level isolation, upgrading them
to physical isolation without a re-architecture is a re-sale. Tenancy boundaries are the
textbook irreversible decision.

### 1.2 What each deployment model demands of the architecture

**SaaS, pooled tier (default):**

- *Identity:* every request carries `tenant_id` from the first byte. The receiver resolves
  tenant from the webhook secret / routing-key mapping — never from a body field the
  sender controls (spoofing the tenant boundary is a cross-tenant leak).
- *Data isolation:* shared Postgres with **`tenant_id` on every row + Postgres Row-Level
  Security policies as the enforcement floor**, app-side `tenant_id` filters as belt-and-
  braces. (RLS is the floor because a single missed `WHERE` clause is the entire
  vulnerability; belt-and-braces because RLS misconfiguration is silent.)
- *Compute isolation (noisy neighbor):* per-tenant Jev concurrency quota, per-tenant race
  budget slice, per-tenant ingress rate limit. A tenant's storm may degrade *its own*
  dispositions to passthrough (fail-open) — it must never consume another tenant's Jev
  budget or race time. This is the pooled model's honest price: degraded-to-safe, never
  degraded-to-silent.
- *Crypto isolation:* per-tenant envelope encryption (see §5) — so "we share a database"
  is a true statement and "tenant A can read tenant B's rows" is a false one even if both
  the app filter and RLS fail.

**SaaS, siloed tier (enterprise / regulated):**

- Dedicated data plane: separate decide+relay deployables, separate database, separate
  Jev quota, separate KMS key. The control plane stays shared (config, platform API,
  billing) but is *read-scoped*: it can never write to a siloed tenant's paging path.
- Migration path pool→silo must be a **supported operation from day one**: export
  tenant's event-log partition → import into silo store → flip DNS/routing-key mapping →
  verify chain continuity (`verify()` passes across the boundary). Designed now, built
  when the first enterprise deal demands it. (Designing the migration later, after data
  has mixed, is the Type-1 disaster this section exists to prevent.)

**Self-hosted (single tenant):**

- Same binaries, `TENANT_ID=default`, SQLite/Postgres local, no control-plane dependency
  for the paging path. The paging path must run **indefinitely with zero egress** except
  the Jev API call (BYOK). This is a hard requirement, not a preference: the air-gap
  buyer's threat model is "your cloud is down and we still page."
- What self-hosted gives up explicitly: platform-hosted shadow analytics, cross-tenant
  benchmark calibration, managed config rollout. Documented at onboarding, not discovered
  in an incident.

**Hybrid:**

- Data plane in the customer's VPC (or on-prem); control plane hosted by us. The paging
  path never depends on the control plane at runtime: config bundles are pulled and
  cached with the last-good chain (ADR-018's 4-stage load), the event log ships
  checkpoints outbound (customer-sealed HMAC per ADR-011 — we hold summaries, never raw
  payloads, unless the contract says otherwise).
- **The control-plane connection is allowed to die without paging-path impact.** If a
  design ever makes the gate call home before deciding, the hybrid model is broken.

### 1.3 Control plane vs data plane split — the actual line

| | Control plane | Data plane |
|---|---|---|
| Components | tenant mgmt, config service, shadow/sim, platform API, billing | ingress, decide (correlator+gate), relay/outbox |
| May it page? | **Never.** It holds no paging credentials. | Yes — its only autonomous action |
| May it suppress? | Never. | Only under the triple lock |
| Failure mode | Degraded console/analytics; paging continues | Fail-open passthrough |
| Data it holds | Config, aggregates, sealed checkpoints | Raw alerts, decisions, outbox |

The Cloudflare Workers-for-Platforms pattern is the reference shape here: a dispatcher
resolves tenant → dispatches into isolated per-tenant workers, while the control plane
"projects defaults and shared secrets into tenant databases so the isolated worker serves
without reading the control plane on the hot path"
([source](https://github.com/markusahlstrand/authhero/blob/HEAD/apps/docs/architecture/multi-tenancy.md)).
Our version: the receiver is the dispatcher; the decide deployable serves from cached
config; the control plane is never on the hot path.

### 1.4 Alternatives considered and rejected

- **Pure pool for everything.** Rejected: the alert pipeline's noisy-neighbor failure mode
  is not "slow dashboard" but "your storm ate my Jev budget and my SEV1 went passthrough."
  Row-level isolation is also the hardest story to sell to a regulated buyer (SOC 2 Type II
  auditors expect ≥1 year of audit logs per ADR-024 discussion; regulated buyers 3–7
  years — a pooled-only vendor has to prove a negative on every audit).
- **Pure silo (stack per tenant).** Rejected: operational multiplier on a ₹0 ops budget.
  One schema migration × N tenants, one drill × N tenants, one config rollout × N
  tenants. The bridge model keeps the pooled default cheap and pays the silo cost only
  where a contract funds it.
- **Account-per-tenant (AWS-native silo).** Rejected: same cost objection as silo, plus
  cloud-vendor lock-in baked into the tenancy model — a Type-1 coupling to one provider's
  account machinery for a product that must also ship self-hosted.
- **Tenant isolation by schema-per-tenant in one DB.** Rejected as the *default*:
  schema migrations across hundreds of schemas are the known painful middle
  ([source](https://altersquare.io/blog/multi-tenant-saas-architecture-shared-vs-isolated));
  keep it as a possible bridge-tier implementation detail, not the architecture.

---

## 2. Deployment topologies

### 2.1 The decision

**Split the v0.1 single binary into three data-plane deployables + a control plane, with
durable queues between them. Keep the single binary as the self-hosted distribution.**

```
                        ┌─────────────────────────────────────────────────┐
                        │                  CONTROL PLANE                   │
                        │  config service · shadow/sim · platform read API │
                        │  tenant mgmt · billing · (Prism console talks    │
                        │  to platform API only)                           │
                        └──────────────┬──────────────────────────────────┘
                                       │ config bundles (pull, cached,
                                       │ last-good chain); sealed checkpoints
                                       │ (outbound only); NEVER on hot path
                                       ▼
  alerts ──▶ ┌──────────────┐  queue  ┌──────────────┐  queue  ┌──────────────┐ ──▶ PagerDuty/
 (PD/AM/     │   ingress    │ ──────▶ │    decide    │ ──────▶ │    relay     │     Opsgenie
  generic)   │ receiver +   │  (durable)│ correlator + │ (durable)│ forwarder +  │
             │ auth +       │         │ gate + Jev   │         │ outbox +     │
             │ normalize    │         │ client       │         │ spill +      │
             │ (stateless)  │         │ (+ Redis)    │         │ secondary    │
             └──────────────┘         └──────────────┘         └──────────────┘
                    │                       │                        │
              trace minted             trace continued          trace continued
```

- **`sentinel-ingress`** (stateless, horizontally scalable): HTTP ingress, webhook auth
  (HMAC, ADR-005), tenant resolution, normalization to `Alert`, fingerprinting, trace-ID
  minting. Writes the normalized alert to the durable queue; acks the sender only after
  the queue ack (or the local spill, see §2.3). Owns *nothing* durable beyond the spill
  buffer.
- **`sentinel-decide`** (stateful via Redis, not via local disk): correlator (dedup,
  storm, change windows, flap craft per ADR-001), the Jev gate kernel (triple lock,
  race-to-page per ADR-010, freshness proofs per ADR-014, instruction firewall per
  ADR-020), plugin stage (§4). Reads config bundles from local cache. Emits decision
  events to the event log and dispositions to the relay queue.
- **`sentinel-relay`** (owns durability): forwarder + outbox (ADR-012), stable
  `dedup_key`, spill replay, standby direct-to-PD + secondary channel. The outbox is the
  DR primitive (§6). Owns the at-least-once delivery contract.
- **Control plane** (`sentinel-platform` + console): read-only API over the event log
  (already the frozen contract), config service, shadow/simulation, label joiner,
  tuner. The console (Prism) talks to the platform API only — never to the data plane.

**Type 1.** Deployable boundaries are process and fate boundaries: what shares a process
shares a crash, a deploy, and a config rollout. The queue contracts between deployables
(idempotency keys, dedup_key stability, at-least-once semantics) are public promises once
customers integrate. Splitting later, after customers have coupled to a monolith's
failure modes, is a rewrite.

### 2.2 Topology options

| Topology | Shape | When |
|---|---|---|
| **T1 single-binary** | v0.1 as-is (all modules, one process) | Self-hosted SMB; dev; the demo. Stays forever as the offline-capable distribution. |
| **T2 three-deployable** | ingress / decide / relay as separate deployables, one region | SaaS default. Independent scaling, independent deploys, independent fate. |
| **T3 edge-ingress + central-decide** | ingress PoPs near customer regions; decide+relay central | Enterprise tier when ingest latency or data-residency demands it. |

**Type 2** which topology a tenant runs on (it's a deployment choice, reversible via the
migration path in §1.2). **Type 1** that the *boundaries* are drawn the same in all three
— T1 is literally the three deployables in one process with in-memory queues, so a
tenant can move T1→T2 without relearning failure modes.

### 2.3 Edge vs central — the actual tradeoff

The temptation: push `decide` to the edge for latency. The math says no:

- The paging-path latency budget B=2700ms (ADR-010) is dominated by the Jev call
  (p50 816ms, p99 1526ms measured). Edge placement saves ~50–200ms of network against a
  ~1.5s model tail. **Local optimization; the bottleneck is the vendor.**
- Correlation is a *global* property: a storm is only visible if all of a tenant's
  alerts are seen by one correlator. Edge-decide means either cross-PoP correlator
  state (a distributed-consensus problem smuggled into an alerting product) or
  split-brain storms (each PoP pages its own aggregate — the exact failure ADR-016
  exists to prevent).
- **Decision: edge does auth + normalize + local spill; decide stays central.**
  The edge ingress keeps a bounded local spill buffer so a central outage degrades to
  "alerts buffered at edge, paged via edge-direct passthrough" rather than
  connection-refused (the ADR-018 silence mode). The edge never suppresses — it lacks
  the correlation state to earn that right.

**Type 1** (the edge-never-suppresses rule and the central-correlation invariant).
**Type 2** (how many edge PoPs, which regions).

### 2.4 Alternatives considered and rejected

- **Keep the single binary for SaaS.** Rejected: one process = one fate domain (§9).
  A console bug (Prism rendering) must never be able to take down paging; in a single
  binary they share a process. The ADR panel already moved the platform to a read-only
  server on a frozen contract — the deployable split is that decision's structural
  conclusion.
- **Microservices per v0.1 module (13 deployables).** Rejected: code-is-liability.
  Thirteen deployables is thirteen deploys, thirteen dashboards, thirteen on-call
  surfaces for a team that fits in one room. Three deployables map to three *failure
  semantics* (accept / decide / deliver) — the split follows fate, not the module list.
- **Service mesh with sidecars for the queue.** Rejected for now (Type 2, revisit at
  scale): Redis Streams + explicit consumer-group code is debuggable by a junior with
  `redis-cli`; a mesh is debuggable by nobody at 3 AM without the mesh's own
  expertise. Per the done-checklist: "if I leave tomorrow, can the team run this from
  logs + architecture docs alone?" — plain queues pass, mesh does not, at our scale.
- **Decide-at-edge with CRDT correlation.** Rejected: genuinely interesting, genuinely
  a research project. The restatement test (principal-mindset) asks what we *believe*
  differently — and we believe storms are global. A CRDT correlator is a bet for a
  v2 architecture review, not this RFC.

---

## 3. Data partitioning & retention lifecycle

### 3.1 The decision

**Three tiers, partitioned by `(tenant_id, month)`, with the ADR-024 (O-1) Vault-led RFC
as the authority on exact durations. This section specifies the mechanism; the RFC sets
the policy numbers.**

| Tier | Contents | Store | Serves |
|---|---|---|---|
| **Hot** | Full-fidelity decision events, last N days (N≈7 default; ≥60d floor for all decisions per ADR-024) | Postgres, partitioned by `(tenant_id, month)` | Console river, correlator history, tuner, shadow diff |
| **Warm** | Full-fidelity events, hot < age ≤ policy max (≥180d for suppress dispositions per ADR-024 floor) | Object storage (Parquet, per-tenant prefix) or Postgres detached partitions | Postmortems, replays, audits, attestation checks |
| **Cold** | Hash-chained summaries + customer-sealed checkpoints (ADR-011) | WORM / immutable object storage | Tamper-evidence, compliance, "prove what happened in 2026" |

**Partition key: `(tenant_id, month)`** — tenant first, so per-tenant export/drop/retention
is a partition operation, not a query; month second, so TTL is detach-a-partition, never
`DELETE WHERE`. Deletion is a logged event (ADR-024), never a silent prune; the
GDPR-vs-immutability collision is resolved by **pseudonymization-on-delete** (rotate the
tenant's field-level encryption key for PII columns; rows stay, names become
ciphertext), not row deletion — per the Pager chief's position in ADR-024.

**The disk-full bound (Type-2 interim guard, required before the design partner per
ADR-024):** the bound is arithmetic, not a hope:

```
bytes_per_decision ≈ measured_mean_event_bytes × 1.3 (JSON overhead + index)
disk_full_date = today + (free_bytes − reserve_bytes) / (alerts_per_day × bytes_per_decision)
```

- The receiver measures `mean_event_bytes` continuously (it already hashes inputs).
- **Watermark at 70%:** page the platform owner + spill warm-tier writes to the
  existing spill machinery. **Watermark at 85%:** stop accepting *warm* writes, keep
  hot; page again. **At 95%:** the paging path is unaffected (decisions are small and
  hot is bounded) — the console degrades honestly. Disk-full must *never* be the thing
  that stops a page; it degrades the audit trail's completeness, loudly.
- This guard is Type 2 (thresholds tunable); the *existence* of the guard before the
  design partner is a panel condition, not optional.

**Type 1:** the `(tenant_id, month)` partition scheme and the three-tier shape — they
determine what "delete my data" and "prove it" mean forever. **Type 2:** the exact day
counts (policy, set by the ADR-024 RFC within the Vault floors).

### 3.2 What changes from v0.1

- `audit.py`'s single `decisions` table → partitioned `decision_events` (one row per
  event-log event, not per alert — suppressions, pages, timer-wins, drift pages are all
  events). The v0.1 schema's spirit survives; the grain changes from "decision row" to
  "event".
- `outcomes` table → the label pipeline writes to hot with `history_as_of` provenance
  (ADR-021); the tuner reads from hot/warm transparently.
- The correlator's history inputs get `history_as_of` and the 72h staleness bound
  (ADR-021, D7) — the retention tiers are what make "stale" computable.

### 3.3 Alternatives considered and rejected

- **Single table, `DELETE WHERE age > N`.** Rejected: silent prunes violate ADR-024's
  "deletion is a logged event"; a 10M-row delete locks the table the console reads; and
  per-tenant retention becomes a query, not an operation.
- **Keep everything hot forever.** Rejected: PM-3 in ADR-024 priced this — disk-full at
  03:12 with a week of silent audit gap. "Keep forever" is also unbounded liability
  (the other Type-1 direction the panel named).
- **Tenant data in per-tenant databases from day one (pool tier).** Rejected: the silo
  cost objection from §1.4, applied to storage. Partition-per-tenant *within* shared
  tables gives the operational shape (export/drop per tenant) at pool cost.

---

## 4. Plugin / extension architecture

### 4.1 The decision

**The extension boundary is declarative-first, sandboxed-second, out-of-process third.
Customer code never runs in the decide process.**

Three layers, in order of preference:

1. **Declarative rules (the 90% case).** JSON/YAML in the config bundle: label-pattern
   routing, custom team taxonomies (Q2 options), per-service storm parameters, change
   windows, business-hours definitions, custom fingerprint salt per tenant. Versioned,
   diffable, reviewable in a PR. No code, no sandbox needed — the attack surface is a
   schema validator.
2. **Sandboxed predicate language (the 9% case).** A tiny deterministic expression
   language for custom correlation/suppression predicates — pure functions over the
   normalized `Alert` + bounded history window. Constraints, enforced by construction:
   no I/O, no network, no clocks (time comes in as a parameter), instruction-count cap,
   wall-clock cap (e.g. 5ms), total ordering deterministic. The model is Vector's VRL:
   a purpose-built, compiled, type-checked remap language where "user logic" cannot
   escape the transform because the language has no escape to begin with
   ([source](https://dev.to/vst/vectorized-data-pipelines-2ip1)).
   Predicates are **advisory by default**: they may add evidence fields and may *page*,
   but a predicate can never suppress on its own — suppression still requires the triple
   lock. A predicate that wants suppress-power goes through the attestation path
   (ADR-013/017) like any other lock.
3. **Out-of-process policy webhook (the 1% case).** For logic that genuinely needs the
   customer's systems: decide POSTs a signed `decision-request` to a customer endpoint
   with a hard timeout (≤500ms, counted inside the race budget B). Timeout or error →
   passthrough (fail-open preserved). The plugin's answer is *one input* to the gate,
   never the decision. Strict HMAC both directions; the plugin endpoint is registered
   in config with its own secret (rotation per §5).

**The sandboxing story, stated plainly:** layers 1 and 2 are not sandboxed code — they
are *data* (config) and a *language without side effects*. There is nothing to escape
from. Layer 3 is sandboxed by the network boundary: it runs on the customer's
infrastructure, talks over HTTPS with timeouts, and its worst case is fail-open. This
is defense by *removing* the dangerous thing, not by containing it — the principal
move (global optimization: the sandbox doesn't need to exist).

**Extension contract (Type 1):** what a plugin can observe (normalized alert fields,
bounded history aggregates, decision-so-far) and what it can influence (evidence
fields, page-now votes, never suppress votes). The *contract* is versioned with the API
(§8); the *language syntax* is Type 2.

### 4.2 Alternatives considered and rejected

- **Embedded Lua/WASM plugins in the gate process.** Rejected: in-process user code on
  the paging path is the highest-blast-radius extensibility choice available. It
  reintroduces the exact supply-chain and injection surface ADR-020 (instruction
  firewall) exists to close, and a misbehaving plugin (infinite loop, memory blowup)
  shares the gate's fate domain. The 5ms wall-clock cap is a mitigation; not running
  the code is the solution.
- **Config-only, no predicate language ("customers who need more can fork").**
  Rejected: forks are how you get N divergent cores and zero upgrade path — the
  opposite of enterprise. The predicate language is the pressure valve that keeps
  weird customer logic *inside* the supported product.
- **Model-based (LLM) custom logic.** Rejected on the hot path: non-deterministic,
  un-auditable, billed per call, and it would need its own instruction firewall
  (ADR-020 applies recursively). Offline/simulation use is fine; paging-path use is not.

---

## 5. Config & secret management

### 5.1 Versioned config

**Decision:** all behavior-affecting configuration ships as a **signed config bundle**
(version, checksum, schema version, author, attestation refs), loaded through the
already-ratified 4-stage chain with checksummed last-good fallback (ADR-018 config
half). This RFC adds the enterprise layer:

- **Schema-versioned:** every bundle declares `config_schema_version`; the loader
  rejects unknown major versions (fail to last-good, alarm). Minor additions are
  additive-only.
- **Diff-preview before apply:** the platform API renders bundle N→N+1 as a human diff
  (thresholds, rules, predicates, plugins); threshold changes additionally require the
  shadow-diff canary (ADR-022, D8) — *proposed threshold changes run 7 days in shadow,
  the disposition diff is signed, then applied.*
- **Change-as-event:** every applied bundle writes a `config_applied` event to the
  event log (who, what hash, previous hash). Rollback = apply previous bundle, also
  logged. The 30-day re-validation clock and two-person rule (ADR-022) ride on this
  event stream.
- **Stale-config safety:** bundles carry `valid_until`; an expired bundle fails the
  loader to last-good + pages (a config that silently ages is ADR-014's rot problem in
  another costume).

**Type 1:** the bundle format, the last-good chain semantics, and "config change is an
audited event." **Type 2:** the rollout cadence, canary percentages.

### 5.2 Secrets

**Decision: envelope encryption per tenant, dual-secret rotation, nothing secret in the
repo — ever.**

- **Key hierarchy** (the industry-canonical pattern,
  [source](https://github.com/hung-phan/system-skills/blob/HEAD/skills/system-review/references/security/encryption-at-rest/SKILL.md)):
  `Root/KEK (KMS or HSM-backed)` → `Tenant Master Key (per tenant_id)` → `Scope DEK
  (per purpose: webhook-signing, plugin-auth, platform-tokens)` → data. Every stored
  secret is `{v, ciphertext, wrapped_dek, nonce, key_id}`. Rotation of the KEK rewraps
  DEKs — no bulk re-encryption. Per-tenant keys mean tenant A's compromise never
  touches tenant B, and per-tenant deletion is key destruction (this is also the
  GDPR-erasure mechanism for the warm tier, §3.1).
- **BYOK for the Jev key:** the Typesafe API key is the customer's (BYOK, v0.1 §7
  stands). Platform-managed keys are the enterprise option, wrapped in the tenant's
  envelope. Either way the key reaches the decide process via env/secret-store mount,
  never via config bundle, never via the event log, never in an error message.
- **Webhook secret rotation without downtime:** dual-secret acceptance — the receiver
  accepts signatures from `secret_current` and `secret_next` during a bounded rotation
  window (e.g. 24h), then `secret_next` becomes current and the old is destroyed. The
  rotation is a config-bundle change (audited) with a named owner (Vault). No flag day,
  no dropped webhooks.
- **What never lives in the repo:** API keys, webhook secrets, KMS keys, customer
  routing keys, real alert payloads, attestation private keys, TLS private keys. The
  `secrets-grep` CI check (key-shaped strings, `BEGIN PRIVATE KEY`, `sk-`, `xoxb-`
  analogues, `TYPESAFE_API_KEY=` assignments) runs on every PR. A committed secret is
  treated as compromised: rotate first, purge history second, postmortem third.

**Type 1:** the envelope hierarchy and the dual-secret rotation protocol — both are
promises to auditors and both are miserable to retrofit. **Type 2:** KMS provider
choice (AWS KMS / GCP / self-hosted HSM), rotation window lengths.

### 5.3 Alternatives considered and rejected

- **Single app-wide encryption key.** Rejected: one compromise = all tenants; key
  rotation = re-encrypt everything; per-tenant erasure impossible. The hierarchy costs
  one KMS call per DEK unwrap (cached in memory) — the cheapest real isolation money
  buys.
- **Secrets in the config bundle ("it's signed").** Rejected: signed is not secret.
  Bundles are diffed, previewed, and shown to humans — exactly the places secrets must
  not appear. Config and secrets travel on separate paths with separate access
  controls, always.
- **Vault/secret-manager as a runtime dependency of the paging path.** Rejected for the
  hot path: the gate must decide with secrets mounted at boot (tmpfs), not fetched per
  request. A secret-store outage must not become a paging outage — eternal friction
  says the store *will* be down at 3 AM. (The control plane may use the secret manager
  live; the data plane mounts at boot and reloads on SIGHUP.)

---

## 6. Disaster recovery

### 6.1 The decision

**The outbox is the DR primitive. Every component's RPO/RTO is stated against it.**

| Component | RPO | RTO | How |
|---|---|---|---|
| `ingress` | 0 (stateless) | minutes (new pods) | No local state beyond bounded spill; spill drains on restart |
| `decide` (correlator state) | ≤60s | minutes | Correlator state in Redis with AOF; rebuildable from hot event log if Redis is lost (replay last N minutes — dedup may double-fire Jev calls, never double-page: dispositions are idempotent on `dedup_key`) |
| `relay` / outbox | **0** | minutes | Outbox rows are the durability contract (ADR-012). Undelivered = still in outbox. Crash between direct-send and spill-write is the named residual — the runbook owns it |
| Event log | 0 in-region; ≤1h cross-region | minutes (read), hours (full rebuild) | Hash-chained append-only (ADR-011); hourly customer-sealed checkpoints replicated cross-region |
| Platform API / console | ≤5 min (derived data) | minutes | Read-only over the log; rebuild from event log + warm tier |
| Config service | 0 (bundles versioned, last-good cached on every decide node) | minutes | Decide nodes run indefinitely on cached last-good |

**Region story:** active + warm standby. The event-log checkpoints (already
customer-sealed per ADR-011) replicate to the standby region; DNS/routing-key mapping
flips on declared disaster. Honest residual, stated not hidden: **the TypeSafe API is
US-only with no SLA** (v0.1 §10.4) — a region loss that takes our primary *and* the
vendor is the standby-direct-to-PD path (ADR-018), which is vendor-independent by
design. The DR plan does not pretend the Jev dependency is multi-region; it plans
around it.

**Type 1:** the RPO/RTO table (these are contractual promises once an enterprise deal
signs them) and "outbox as source of truth." **Type 2:** the specific regions, the
checkpoint replication cadence within the ≤1h bound.

### 6.2 Alternatives considered and rejected

- **Active-active multi-region decide.** Rejected: correlation is global (§2.3); two
  active deciders need a consensus protocol for storm state. Warm standby with a
  declared, drilled cutover (ADR-018's drill discipline, extended to region cutover)
  is honest; active-active is a distributed-systems paper wearing a product costume.
- **Backup = nightly DB dump.** Rejected for the paging path: a dump restores *state*,
  not *in-flight intent*. The outbox restores intent (what was decided but not yet
  delivered). Dumps are fine for the platform tier; the data plane's DR primitive is
  the outbox + the event log, full stop.
- **"The cloud provider handles DR."** Rejected: provider-region failure is exactly
  the event being planned for. Multi-AZ is the reliability baseline, not the DR plan
  (the SaaS skill's critical-limits table makes this exact distinction: Multi-AZ ≈ 2×
  cost for HA, *not* a cross-region DR substitute).

---

## 7. Observability

### 7.1 The decision

**One trace ID per alert, minted at ingress, carried through every stage to the
console. Emit everything needed to debug the paging path; emit nothing that leaks
customer data.**

- **Trace envelope:** `sentinel_trace_id` (ULID, time-sortable) minted by ingress,
  propagated via HTTP header (`X-Sentinel-Trace`) between deployables and as a field
  on every event-log event, every Jev call record (with `jev_model` version), every
  outbox row, and every platform API response. The console's decision-river links
  trace → events → Jev answers → PD incident in one lookup. This is the
  principal-governance "unified telemetry" pillar: a spinner on screen must be
  diagnosable to the exact slow stage in one lookup.
- **What we emit (per tenant, per stage):** ingest rate; auth rejections by cause;
  stage latency histograms (p50/p99 per stage — the race budget B is *measured* here,
  per ADR-010's re-derivation protocol); disposition counters by `(action, reason)`;
  Jev call rate/latency/error-code by model version; timer-win watchdog trips
  (B-falsification events); outbox depth + age of oldest undelivered; spill rate;
  suppression-rate SLO gauge + watchdog heartbeat (ADR-022, dead-man's-switch);
  config bundle version in effect; predicate/plugin evaluation latency + timeout rate.
- **What we never emit:** raw alert payloads in logs (log fingerprints, redacted
  summaries, and field-presence bits — never titles, label values, or metric values
  that could carry PII); API keys, webhook secrets, routing keys (redacted at the
  HTTP layer, v0.1 §7 stands); customer hostnames in metric labels (cardinality bomb
  *and* a leak — aggregate by tenant, not by host).
- **Sampling:** traces for all suppress/page decisions retained in full (they're the
  trust surface); passthrough traces sampled (e.g. 1%) with counters exact. Sampling
  decisions are themselves logged — "we sampled this" must never look like "nothing
  happened."

**Type 1:** the trace-ID envelope and the never-emit list (a logging choice becomes a
privacy incident the first time a log ships to a vendor). **Type 2:** metric names,
dashboard layouts, sample rates.

### 7.2 Alternatives considered and rejected

- **Trace only the Jev call (vendor-centric observability).** Rejected: the paging path
  has four stages and three queues; Jev is one stage. When a page is late, "the model
  was slow" is one hypothesis of five — the trace must cover all five.
- **Full-payload debug logging behind a flag.** Rejected: the flag *will* be left on
  (eternal friction: the tired maintainer), and the log aggregator becomes a second
  copy of every customer's alerts with none of the event log's integrity properties.
  Field-presence bits + fingerprints give 90% of the debuggability at 0% of the leak
  surface.
- **Vendor APM agent on the data plane.** Rejected for now (Type 2, revisit):
  stdlib-only is a v0.1 constraint with real value (dependency-free deploys,
  air-gap friendliness). Structured JSON logs + the trace envelope get us to "one
  lookup" without an agent; add APM when the log volume, not the fashion, demands it.

---

## 8. API versioning & evolution

### 8.1 The decision

**Two contracts, two versioning schemes — because one is ours to change and one is not.**

**A. Ingest contracts (what customers send us):**

- The **PagerDuty Events API v2 shape is frozen by compatibility** — we mirror PD so
  existing integrations don't break (v0.1 §3.9). We never "version" it; if PD changes
  v2, we track PD.
- The **generic webhook is ours**: version in the envelope
  (`{"sentinel_webhook_version": "2026-10-04", ...}`) *and* negotiable via
  `Sentinel-Webhook-Version` header. Additive changes (new optional fields, new event
  types) are the default and need no version bump — consumers ignore what they don't
  recognize. Breaking changes get a new version with **dual-accept for ≥6 months** and
  a `Sunset` header on responses to old versions.

**B. Platform read API (what the console and customer automation read):**

- URL-versioned (`/api/v1/...`), plus **per-tenant API version pinning à la Stripe**:
  a tenant is pinned to the API version in effect at onboarding; breaking changes never
  affect a pinned tenant unless they upgrade; `Sentinel-Version` header overrides
  per-request for testing. Stripe's model — "each account has an API version, and the
  version in effect when the event occurs determines the structure"
  ([source](https://medium.com/@instawebhook.com/building-a-custom-webhook-provider-api-design-lessons-from-stripe-and-github-10967a4d1bf1)) —
  is the gold standard because it decouples *our* ship cadence from *their* upgrade
  cadence. Events already emitted are immutable under later version upgrades (same
  source).
- **Deprecation discipline (published, not improvised):** every deprecation ships with
  (1) a changelog entry, (2) a sunset date ≥6 months out, (3) `Deprecation` + `Sunset`
  response headers, (4) a migration guide, (5) dual-serve old+new for the whole window.
  A version is removed only after the sunset date *and* zero pinned tenants remain on
  it — the platform API reports pinned-version distribution as a metric (so "zero
  remain" is measured, not assumed).
- **Zero data-model leakage** (principal-governance): the API never exposes storage
  internals (`tenant_id` stays internal; the API speaks `customer_id`; no
  `decisions_v2` table names in field names). The backend must be rewritable without
  breaking the console or customer scripts.

**Type 1:** the versioning scheme + pinning semantics + deprecation discipline — all
three are public promises the moment the first customer automates against the API.
**Type 2:** which endpoint gets v2 first, the exact sunset lengths beyond the 6-month
floor.

### 8.2 Alternatives considered and rejected

- **SemVer-style `/v1/`, `/v2/` with breaking bumps whenever convenient.** Rejected:
  it couples our release train to every customer's upgrade project. Stripe's
  date-pinned model exists precisely because "v2" is a flag day; date pins are a
  per-tenant choice.
- **"We version nothing until we need to."** Rejected: the first breaking change then
  becomes the versioning design, done under pressure, without pinning — which is how
  you get the "upgrade broke our on-call automation at 2 AM" incident. Versioning is
  Type 1 *because* it's cheap to design now and expensive to retrofit.
- **GraphQL to "avoid versioning."** Rejected: it moves the versioning problem into
  field-deprecation bookkeeping (which still needs the discipline above) while adding
  a query-planning attack surface on a read API that sits next to the paging path.
  REST + pins is boring, auditable, and cacheable.

---

## 9. Blast-radius compartmentalization

### 9.1 The decision

**Four fate domains. A failure in one may degrade the others, but may never silently
take down paging.**

| Domain | Contains | May fail into | Must never cause |
|---|---|---|---|
| **Paging path** | ingress → decide → relay | — (it *is* the promise) | Silent drop. Every failure mode is passthrough or buffered-and-paged |
| **Shadow / simulation** | shadow gate, backtest, tuner, threshold canary | Stale recommendations | Any write to the paging path. Reads the event log only; its outputs are *proposals* until a human signs (ADR-022) |
| **Platform API** | read API over event log, config service reads | Console degradation | Paging impact. Already read-only on a frozen contract; extend the rule: the platform holds no paging credentials, opens no connections to decide/relay |
| **Console (Prism)** | UI | Blank screen with honest message | Confusion mistaken for system state. Backend down → cached data + honest "stale" banner (product constitution: graceful degradation), never a raw 500 or a silently empty river |

**Structural rules (Type 1):**

1. **No shared process** between the paging path and anything else (the §2 split is
   this rule's implementation).
2. **No shared mutable state** between domains: the event log is the *only* cross-
   domain channel, and it's append-only — domains communicate by publishing facts,
   never by taking locks.
3. **Separate budgets:** Jev quota, connection pools, and rate limits are per-domain.
   The shadow pipeline's backtest may not consume the paging path's Jev budget
   (a shadow experiment that starves production of model calls is a self-inflicted
   vendor outage).
4. **Circuit breakers at every domain boundary**, failing toward the safe direction:
   platform→decide has no path (rule: it doesn't exist); decide→Jev breaks to
   passthrough (built); relay→PD breaks to outbox+secondary (built, ADR-012);
   ingress→decide breaks to edge spill + edge-direct passthrough (§2.3).
5. **Degraded-mode semantics are specified per dependency loss**, not improvised:
   Redis down → correlator runs stateless (no dedup, no storm-collapse: every alert
   gets its own Jev call; dispositions still gated by the triple lock; Jev budget
   alarm fires). Config service down → decide runs on cached last-good indefinitely
   (ADR-018). Event-log writer down → decisions continue (paging is the promise;
   the audit gap is a loud, paged incident — the log's absence must never block a
   page).

**Type 2:** the exact breaker thresholds, pool sizes, and alarm routing.

### 9.2 Alternatives considered and rejected

- **"The platform API needs a write path for mute/acknowledge."** Rejected as
  specified: ADR-007 already decided mute is a platform-tier *label* over
  suppress-with-reason, event-logged, never a fifth engine disposition. The
  compartmentalization holds because the write goes to the *log*, not to the
  paging path. Any future "platform writes to decide" proposal re-opens this
  section as a Type-1 RFC.
- **Shared Redis for correlator state and platform caching.** Rejected: a cache
  stampede from the console (someone loads a year of decision river) must not evict
  correlator state. Separate instances (or at minimum separate logical DBs with
  separate memory quotas) — shared infrastructure is a shared fate.
- **Single on-call rotation for all domains.** Rejected operationally (Type 2 but
  load-bearing): the paging path's on-call is woken for paging-path symptoms; a
  console CSS bug pages nobody. Incentive realignment (product constitution): if the
  same human is woken for both, the urgent-but-unimportant trains them to ignore the
  important.

---

## 10. Pre-mortem — top 3 failure modes of the proposed topology

*Assumed: it is October 2027. The three-deployable bridge architecture is in production
with 40 pooled tenants and 3 siloed enterprise tenants. It has failed catastrophically.
What killed it?*

### PM-1: The queue between ingress and decide became the new dual-write lie

**The failure:** ingress acks the sender after the queue-ack, but a Redis Streams
failover drops the un-replicated tail. 400 alerts were "accepted" and never decided —
not paged, not logged, not spilled. The dual-write lie ADR-011 buried (says paged,
never paged) returns as "says accepted, never triaged."

**Why it's the top risk:** every queue added between deployables is a new place where
"accepted" and "processed" can diverge, and this queue sits *before* the event log —
there is no record of what was lost.

**Mitigations (designed now, built with the split):**
- Receiver spill-first: the bounded local spill is written *before* the queue write;
  the queue write carries the spill offset; decide acks with the offset; un-acked
  spill drains on a timer, not on hope.
- Idempotent decide on `(tenant_id, dedup_key, trace_id)` — redelivery is safe, so
  the queue can be configured for at-least-once without fear.
- A continuous reconciler (control plane, Type 2 cadence): `spill_offsets` vs
  `decide_acks` — any gap older than N minutes pages the platform owner. The absence
  of loss is *measured*, not assumed.

### PM-2: Tenant A's storm eats the pooled decide tier

**The failure:** a tenant's misconfigured monitor emits 50k alerts/hour. Per-tenant
quotas were set but the *race budget* wasn't partitioned — tenant A's Jev calls
saturate the shared decide workers' latency, tenant B's alerts start timer-winning
to passthrough. Tenant B's on-call gets 300 passthrough pages for a flap storm and
concludes Sentinel is "a very expensive way to do nothing." Two churns, one
case-study-shaped hole.

**Why it's structural:** the pooled model's honest price (§1.2) is "degraded-to-safe
*for the noisy tenant*" — but latency is a shared resource that quotas on *call count*
don't fully partition.

**Mitigations:**
- Partition the race budget: per-tenant Jev concurrency *and* per-tenant latency
  accounting; a tenant exceeding its slice gets fail-open passthrough *for its own
  alerts* while others are unaffected (bulkhead, not just quota).
- Automatic bridge-tier escalation: sustained quota breach (>X% of slice for >Y
  minutes) triggers a commercial + technical workflow to move the tenant to a
  dedicated decide shard. The noisy neighbor gets isolated by the architecture, not
  by a support ticket.
- The suppression-rate watchdog (ADR-022) watches *per tenant* — a tenant whose
  passthrough rate spikes is a tenant whose isolation is failing, and the alarm
  names them.

### PM-3: A bad config bundle rolls to all pooled tenants at once

**The failure:** a well-formed but wrong predicate (suppression rule with an inverted
condition — the classic) ships in bundle v214. The diff preview showed it; the
reviewer misread it; the shadow-diff canary ran but the canary tenant's traffic
didn't include the affected alert shape. 40 tenants suppress a real SEV pattern for
47 minutes until the per-tenant suppression-rate watchdog fires. The control plane —
which was never supposed to be able to cause a paging incident — just caused the
worst one in company history.

**Why it's the control plane's original sin:** §1.3 says the control plane "may never
suppress." True at runtime — and irrelevant at config time. Configuration *is* a
write to the paging path, just a slow one.

**Mitigations:**
- Config rollout is a *deployment*, with the deployment discipline (§8's
  principal-governance pillar): canary 1 tenant → 5% → 100%, with automatic halt on
  suppression-rate deviation per tenant (the watchdog is the rollback trigger, not a
  human reading a dashboard).
- Predicates that can influence suppression require the attestation path (ADR-013/017)
  *and* a production-burn-in: N days in shadow with the disposition diff signed
  before the predicate is eligible for the live bundle.
- The kill-switch: a single audited action reverts all tenants to the last-good
  bundle in <60s, and the revert itself is a `config_applied` event. Drilled
  quarterly (ADR-018's drill discipline covers config rollback too).

---

## 11. Principal-systems output contract — the done-checklist, answered in writing

1. **10× scale without architectural rewrite?** Yes, within the pooled tier: ingress
   is stateless (add pods), decide scales by tenant-shard (the bridge model *is* the
   scaling plan — hot tenants get dedicated shards), relay scales on outbox depth.
   The 10× that breaks us is 10× *tenants*, not 10× alerts — tenant onboarding must
   stay a control-plane API call, never a provisioning project. (If onboarding needs
   a human, we fail this at 50 tenants, not 500.)
2. **Third-party dependency down 4 hours — what happens?** Jev down → passthrough
   everywhere, outbox unaffected, event log records `error:jev_overloaded` with the
   trace. PagerDuty down → outbox accumulates, secondary channel carries pages
   (ADR-012/018). KMS down → boot-mounted secrets keep the data plane alive;
   new-tenant onboarding pauses (accepted, documented). Cloud region down → warm
   standby cutover (§6), paging continues via edge spill + direct passthrough during
   the cutover window.
3. **Junior deploys a stale config — caught automatically or crash?** Caught:
   bundles carry `valid_until` + schema version + checksum; the 4-stage loader
   rejects stale/unknown bundles to last-good and alarms (§5.1). The failure mode is
   "runs on last-good and pages someone," never "runs on a 6-month-old threshold set
   silently."
4. **Did we optimize locally at the cost of global complexity?** The deliberate
   non-optimizations: no decide-at-edge (§2.3 — the bottleneck is the vendor tail),
   no service mesh (§2.4 — debuggability beats elegance at our scale), no
   active-active (§6.2 — consensus is not a product feature). The complexity we *do*
   buy (three deployables, envelope encryption, per-tenant quotas) is all on the
   global path: isolation, durability, and auditability — the things buyers pay for.
5. **If I leave tomorrow, can the team run this from logs + architecture docs alone?**
   This RFC + the runbooks it implies (cutover, config rollback, drill records per
   ADR-018 D13) are the truck-factor docs. Gaps named honestly: the attestor
   onboarding/offboarding story is still an unnamed trusted party (ADR panel
   cross-item note 2 — needs its own ADR); the 7-day model re-validation protocol
   needs a named owner (D2).

**Constitution clauses that bind, and how:**
- *Software — "explicit versioned boundaries":* the deployable contracts (§2), the
  plugin contract (§4), the API versioning scheme (§8). No cross-domain DB reads,
  ever (platform reads the event log; nothing reads another domain's store).
- *Infrastructure — "blast-radius compartmentalization":* §9 in full; "observability
  over monitoring": the trace envelope (§7), not just CPU alerts.
- *Data/AI — "data is deterministic, models are chaotic":* the deterministic
  correlator and firewall sit *before* the Jev call (never pay Jev for deterministic
  work — v0.1 principle 3, preserved); "deterministic guardrails always": the triple
  lock, the race budget, the freshness proofs are hardcoded fallbacks no model
  output can veto.
- *Product — "empathy for the operator":* the counterfactual receipt (ADR-023),
  honest degradation (console shows stale-with-banner, never blank), "no accuracy
  claims" extended to "no safety claims" for unbuilt designs.

---

## 12. What changes vs what stays — section-by-section against ARCHITECTURE.md v0.1

| v0.1 section | Stays | Changes (this RFC) |
|---|---|---|
| §1 principles (fail-open, uncertainty pages, never pay Jev for deterministic work, descriptions, cannot_determine, honest numbers) | **All stay.** They are the product's moral core. | *Added:* fate-domain isolation (§9), extension-boundary principle (§4), "an adopted design is not a shipped property" (§0). |
| §2 component diagram | The four-stage pipeline shape (receive → correlate → decide → relay) | SQLite audit → event log (already decided, ADR-011 — the diagram is stale and gets redrawn); single box → three deployables + control plane (§2); queues drawn as durable, not arrows. |
| §3 module layout + contracts | Module boundaries and signatures stay — they become the *kernel* inside `decide`. | *Added:* plugin stage interface (§4), trace propagation fields on every record (§7), config-bundle loader replacing ad-hoc env reads (§5). |
| §4 confidence policy / §5 tuner | Math and CLI stay. | Tuner runs **per tenant** (thresholds are tenant-scoped config); shadow-diff canary gates threshold changes (ADR-022). |
| §6 eval harness | Stays. | Adversarial corpus (ADR-020) and flap-storm fixtures (ADR-001) extend it; ASR becomes a gate metric. |
| §7 security | BYOK, HMAC webhook auth, Authorization redaction stay. | "Per-tenant KMS envelope is the SaaS upgrade (documented, not built)" → **designed here** (§5.2); dual-secret rotation added; secrets-grep CI added. |
| §8 deployment | Single-box stays as the self-hosted distribution (T1). | SaaS gets T2/T3 topologies (§2); "production upgrade path (documented)" becomes the deployable contracts. |
| §9 testing | The kill-the-client test stays and stays a release blocker. | *Added:* ratification checklists per ADR (D1–D14) as CI gates where falsifiable; dedup_key property tests (ADR-012 condition); storm-aggregate regression (D3). |
| §10 honest limitations | The honesty *practice* stays and deepens. | Limitations 3 (single-box/SQLite) and parts of 4 graduate into designed sections; limitation 2 (Jev non-determinism) is now load-bearing in the trace + audit design rather than a caveat. |

**What is deliberately not in this RFC:** the Jev-question design, the threshold math,
the UI/UX (Prism's domain, governed by the Interface Principles doc), the label
pipeline internals, pricing/packaging. This RFC is the *architecture* the product
runs on — it ends where the product decisions begin.

---

## 13. Sequencing — build order (Type 1 first, manual first)

Per execution-doctrine: manual first, automated second, scaled third. Per
principal-systems: Type 1 decisions get RFC rigor before code; Type 2 gets decided fast.

**Phase A — Type-1 foundations (before the first design partner):**
1. Redraw the §2 diagram (event log, three deployables, queues) — the stale SQLite box
   is a Type-1 lie in a doc (ADR-005's lesson applied to diagrams).
2. Freeze the deployable contracts: queue message schema, `dedup_key` stability,
   idempotency keys, trace envelope. (T1 single-binary keeps working — the contracts
   are honored in-process first.)
3. `(tenant_id, month)` partitioning on the event store + the interim disk-guard
   watermarks (ADR-024 condition b — *before* the design partner, regardless of the
   RFC's timeline).
4. Envelope-encryption hierarchy for the first SaaS tenant (even if tenant #1 is us).
5. Config bundle format + last-good chain + `config_applied` events (extends the
   ratified ADR-018 config half).
6. API versioning scheme + tenant pinning (cheap now, miserable later — §8.2).

**Phase B — with the first design partner (manual, measured):**
7. Per-tenant quotas + the suppression-rate watchdog with dead-man's-switch heartbeat
   (ADR-022) — run the weekly shadow check manually first; automate when skipped
   twice (Forge's position, adopted).
8. Declarative rules layer (§4.1) driven by the partner's actual needs — build the
   pressure valve *from* real pressure, not speculation.
9. First recorded drill with human ack + named watcher owner + cutover runbook
   (D13) — a standby that hasn't been drilled doesn't exist.

**Phase C — scale-gated (only when the trigger fires):**
10. Split T1→T2 deployables (trigger: one deployable's scaling needs diverge, or the
    first fate-domain incident).
11. Predicate language (§4.2) (trigger: two customers need logic declarative rules
    can't express).
12. Warm standby region (trigger: first enterprise contract with a region clause).
13. Policy-webhook layer (§4.3) (trigger: a customer asks, with funding).

**Kill conditions (monkey-first):** if Phase A item 2 (deployable contracts) can't be
written without the modules leaking internals across the boundary, the three-deployable
split is wrong and we stay single-binary longer — the contracts are the bet, not the
process count. If the disk-guard arithmetic (item 3) shows the design partner's
volume fills the disk before the ADR-024 RFC lands, the RFC timeline compresses —
the disk fills on its own schedule.

---

## 14. Open questions for the panel

1. **Attestor identity** (ADR panel cross-item note 2): dual attestation, freshness
   proofs, and the two-person rule all rest on "who is an attestor." Onboarding,
   offboarding, and compromise response are undesigned. Vault to lead a follow-up ADR?
2. **The pooled tier's Jev-budget partition** (§1.2, PM-2): per-tenant concurrency
   quotas are proposed; should the *race budget B* also be per-tenant, or is a global
   B with per-tenant accounting sufficient? Pager's call.
3. **Predicate language scope** (§4.2): advisory-only (may page, never suppress) is
   proposed. Is there a customer shape where a predicate *should* be able to suppress
   under attestation, or does that re-open ADR-019's "silence takes two" through a
   side door? Tripwire + Vault.
4. **EU retention + residency** (ADR-024 condition c): this RFC answers the mechanism
   (partitions, WORM, pseudonymization-on-delete) but not the jurisdiction mapping.
   Jointly with the Vault-led retention RFC — which tiers may physically live where?
5. **The 7-day model re-validation owner** (D2): the protocol exists as a Type-1
   procedure in ADR-015; nobody owns the pager. Name the owner before the first
   vendor remap, not after.

---

## Appendix A — sources

Research conducted 2026-10-04 for this RFC. Vendor engineering material is cited as
precedent for *patterns*, not as instruction — per the standing FYI boundary, external
material is seasoning; our validation numbers steer.

- AWS SaaS Factory taxonomy (silo / pool / bridge) and the "default pool → bridge →
  silo" guidance:
  https://github.com/rodrigo-mendes/agents-factory/blob/HEAD/StoryBeat/.claude/skills/architecting-aws-saas-multitenant/SKILL.md
- AWS Guidance for Multi-Tenant Architectures (silo/bridge/pool at the data layer):
  https://aws.amazon.com/solutions/guidance/multi-tenant-architectures-on-aws/
- Pool vs silo tradeoffs (cost, blast radius, compliance, migration pain):
  https://altersquare.io/blog/multi-tenant-saas-architecture-shared-vs-isolated
- Control-plane/data-plane split, per-tenant isolation, dispatcher pattern
  (Cloudflare Workers for Platforms reference shape):
  https://github.com/markusahlstrand/authhero/blob/HEAD/apps/docs/architecture/multi-tenancy.md
- Stripe API versioning: per-account version pins, immutable events, date-based
  versions, per-request override:
  https://medium.com/@instawebhook.com/building-a-custom-webhook-provider-api-design-lessons-from-stripe-and-github-10967a4d1bf1
  and https://dev.to/preecha/why-stripes-api-is-the-gold-standard-design-patterns-that-every-api-builder-should-steal-ddi
- Vector VRL as the model for a sandboxed, side-effect-free user logic language:
  https://dev.to/vst/vectorized-data-pipelines-2ip1
- Secure polyglot code execution (defense-in-depth layers; why removing the dangerous
  thing beats containing it):
  https://dev.to/totylabs/secure-polyglot-code-execution-how-to-run-untrusted-code-safely-4n8a
- Envelope encryption canonical pattern (KEK → DEK, rotation without re-encryption):
  https://github.com/hung-phan/system-skills/blob/HEAD/skills/system-review/references/security/encryption-at-rest/SKILL.md
- Per-tenant key hierarchy with HKDF-derived tenant master keys (miniKMS):
  https://github.com/shitcodebykaushik/minikms
- Tenant secrets key-custody + rotation posture (molecule-core):
  https://github.com/molecule-ai/molecule-core/blob/HEAD/docs/architecture/secrets-key-custody.md

---

*End of RFC. Status: PROPOSED. Next step: panel review (disagree-and-commit), then
Phase A sequencing by the Sunday wave coordinator.*
