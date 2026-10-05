# Architecture Patterns in Production Systems
**Stream 2 — Architecture Revision Program, Sentinel · 2026-10-05**

Method: public-web research only (browser search + page fetch, first-page results
plus follow-on queries). Every substantive claim carries a source with URL and
access date. "Relevance to Sentinel" notes are grounded in code read at
`program/architecture-revision @ f1e9563` (branch `program-arch-rev-architecture-patterns`).

Prior notes consulted (not duplicated): `ARCHITECTURE.md` (frozen engine
contracts), `docs/adr-decisions-2026-10-03.md` (ADR adjudication),
`docs/adr-attestor-identity.md`, `PLATFORM_ARCHITECTURE.md`. None of them
treat *layering as an architecture discipline* — that is the gap this stream fills.

---

## 1. Event-driven systems at scale: what works, what becomes operational hell

### 1.1 The four patterns are not one pattern (mechanism)

Martin Fowler's Thoughtworks-summit summary distinguishes four things people
conflate under "event-driven", and the conflation itself is the primary failure
mode:

- **Event notification** — a system notifies others of a change; the source
  doesn't care about the response. Low coupling, simple. Trap: when a real
  logical flow runs *across* several notifications, the flow is invisible in
  program text and can only be discovered by monitoring a live system — hard to
  debug and modify years later. The acute form is the "passive-aggressive
  command": a message styled as an event that the source actually expects the
  recipient to act on. *(Fowler, "What do you mean by Event-Driven?",
  https://martinfowler.com/articles/201701-event-driven.html, Jan 2017, updated
  2017-02-08; fetched 2026-10-05)*
- **Event-carried state transfer** — events carry the data that changed, so
  recipients never query the source. Gains resilience and latency; costs
  duplicated data and receiver-side state maintenance.
- **Event sourcing** — every state change is appended to an immutable log; the
  log is the source of truth and current state is derived by replay. The working
  copy and the event log should be *separate components*; only the parts that
  truly need history touch the log. Problems: replay breaks when results depend
  on interactions with outside systems; event schema evolution over time; many
  find the event processing adds large complexity (Fowler suspects poor
  separation between working-copy derivation and domain processing is a major
  cause). *(same source)*
- **CQRS** — separate read and write data structures, justified only when a
  single model handling both gets too complicated (e.g., very asymmetric access
  patterns). Fowler: "many of my colleagues are deeply wary of using CQRS,
  finding it often misused" — and on one project "event sourcing had been a
  disaster", where in that single sentence he could detect a confusion between
  event-sourcing and CQRS, meaning the team couldn't even identify the culprit.
  *(same source)*

### 1.2 What becomes operational hell (mechanisms with failure evidence)

**Fire-and-forget publication loses facts silently.** A first-person postmortem
of a team that ripped out its event-driven architecture after two years
describes the canonical failure: one `OrderPlacedEvent` failed to publish
during a brief Kafka broker outage; the order itself succeeded (the code "fired
and forgot" the event); inventory drifted by 1,200 units across 80 SKUs over
weeks before detection; root-causing took six weeks and ~600 engineer-hours
(manual reconciliation, customer refunds, one lost $40K B2B contract); the fix
took two days. Their honest audit: of 17 event-driven flows, maybe three needed
to be event-driven. The lesson is not "events are bad" — it is that
**at-least-once publication requires a transactional outbox or the consumer
must be able to detect a missing event**, and most flows don't need events at
all. *(Medium @code_pulse_devrim, "Why We Ripped Out Our Event-Driven
Architecture After Two Years", May 2026;
https://medium.com/@code_pulse_devrim/why-we-ripped-out-our-event-driven-architecture-after-two-years-and-built-boring-synchronous-apis-b4ac7b9d1abb;
fetched 2026-10-05)*

**Replay is the marketed superpower and the operational nightmare.**
Kafka's replayability is genuinely transformative for fixing bad analytics —
reset offsets and reprocess *(Medium @sriraghukatragadda, "Why Event-Driven
Architecture and Kafka Actually Exist", fetched 2026-10-05;
https://medium.com/@sriraghukatragadda/why-event-driven-architecture-and-kafka-actually-exist-a-real-production-walkthrough-5a0dd5288ed2)*.
But in full event-sourced systems, practitioners report: log compaction is "an
evil problem" (unbounded logs consume terabytes even of test data); replays
after schema changes can take a day-plus even at 100× normal throughput; when
several instances go down, simultaneous replay causes a **thundering herd that
takes the system down regardless of capacity**; one corrupt event anywhere in
an "immutable" log forces manual deletion, voiding the immutability story; and
projecting everything everywhere costs more memory/performance than a database
holding commit log + state together. *(Hacker News comment thread on Kafka,
news.ycombinator.com/item?id=23206566; fetched 2026-10-05)*

**Scale brings its own failure taxonomy:** consumer lag (producers at
1M events/min vs consumers at 700k/min — "real-time" systems become delayed
systems), hot partitions (a `country` partition key sends 80% of traffic to
one broker), rebalance storms that pause consumption and duplicate processing,
and — most insidious — **recovery causing the second outage**: after Kafka
returns, consumers drain millions of backlogged messages and spike PostgreSQL
and external APIs; the guidance is controlled catch-up rates, backpressure,
and treating recovery as part of incident design, not the epilogue.
*(sriraghukatragadda, same article as above)*

**Schema evolution is a semantic problem, not a syntax problem.**
Compatibility registries handle add/remove/rename/retype; the dangerous change
is one that is *interpreted successfully with the wrong meaning*. Senior
practice separates syntactic from semantic compatibility.
*(sriraghukatragadda, same article)*

**When it works — the fit conditions.** Practitioners converge on a short list:
use EDA when multiple independent consumers need the same event, producer and
consumer can be time-decoupled, fan-out is needed, or replay/audit is a real
requirement; avoid when immediate consistency is needed, the system is simple,
or strict global ordering is required. Events must model **business facts**
(`OrderPlaced`), not database operations (`OrderUpdated`); anti-pattern list:
Kafka replacing every REST call, event storms of tiny events, no replay
strategy, no observability, no idempotency, events hiding slow services.
*(foojay.io, "When NOT TO USE Event-Driven Architecture (EDA)", fetched
2026-10-05, http://foojay.io/today/when-not-to-use-event-driven-architecture-eda/ ;
prokstudio event-driven-architecture skill, github.com/prokstudio/skills, fetched
2026-10-05 — anti-pattern guards: idempotent handlers, schema registry,
acknowledgment-based delivery, never auto-ack)*

**The one clean win documented:** a team rebuilt a Kafka pipeline with CQRS +
Redis (commands through Kafka for durability, queries from Redis), getting
2.5× throughput and 89% reduction in query latency — the win came from
separating read/write models under asymmetric access patterns, exactly the
CQRS justification Fowler names. *(Medium @harishsingh8529, Jul 21 2025;
https://medium.com/@harishsingh8529/we-rebuilt-our-kafka-pipeline-using-cqrs-and-redis-heres-what-broke-6cb100321214;
fetched 2026-10-05)*

### 1.3 Relevance to Sentinel

- **Sentinel's hash-chained event log is event sourcing done right.**
  `src/sentinel/eventlog.py` appends decisions immutably for audit/attestation;
  it is *never* the source of rebuildable runtime state (no replay-on-boot,
  no projections), which is precisely how Fowler says to use the pattern —
  audit capability without the replay/thundering-herd/compaction liabilities.
  The attestor work (ADR-013/014/022, `docs/adr-attestor-identity.md`) strengthens
  this log's trust properties. Keep this shape; do not let the event log become
  a state store.
- **The "3 of 17 flows" audit applies to Sentinel's own queue ambitions.**
  Today there is exactly one durable queue (inside the forwarder, `queue` +
  `spill.py`). Any proposal to put Kafka-style infrastructure *between*
  pipeline stages must pass the ripped-out-EDA test: name which flow genuinely
  needs time-decoupling, and show the outbox/idempotency story, or it is
  complexity theater.
- **The receiver→gate path is where the fire-and-forget lesson bites.**
  If a future design ever decouples receiver from gate via an internal broker,
  the OrderPlacedEvent postmortem is the mandatory reading: publication must be
  transactional with the receiver's accept-record, or the consumer must detect
  gaps. Today the handoff is synchronous (receiver.py:436–474), so this is a
  *future-change* guardrail, not a current bug.

---

## 2. Middleware design: what makes good middleware

### 2.1 Envoy — the reference implementation of middleware layering (mechanism)

Envoy (born at Lyft, now the standard service-mesh data plane) is architected
as **layered filter chains inside an out-of-process sidecar**:

- **Out-of-process, not a library**: runs as a sidecar next to each app
  instance; language-agnostic; upgraded independently of application code. This
  is the decisive layering choice — the middleware's lifecycle is decoupled
  from the application's.
- **L3/L4 filter chain** (listener filters → network filters: raw TCP/UDP,
  TLS, Redis/Mongo/Postgres sniffing) with an **L7 HTTP filter layer on top**
  (buffering, rate limiting, routing, header manipulation; the HTTP router
  filter performs the actual forwarding). Each filter is a discrete stage with
  a narrow contract on the request/response stream.
- **Control plane vs data plane split (xDS)**: configuration (listeners,
  clusters, routes, certs) arrives dynamically from a central control plane
  (LDS/CDS/EDS/RDS); the data plane only executes. A proxy can modify how a
  request is handled but *cannot modify what the routing namespace means* —
  governance lives above execution. *(qu3ry.net, "Envoy Proxy Made Service
  Mesh Data Planes Programmable. The Control Plane Still Governs.", fetched
  2026-10-05, https://qu3ry.net/articles/adaptive-indexing-envoy-proxy.pdf)*
- **Failure isolation between the middleware and its upstreams is explicit
  and quantified**: circuit breakers (max connections, pending requests,
  requests, retries) short-circuit with `upstream_rq_pending_overflow`;
  outlier detection ejects unhealthy hosts (consecutive 5xx, success-rate
  triggers), with `base_ejection_time` (30s) before re-admission and probing;
  and **panic mode** — when >50% of hosts are ejected (configurable
  `panic_threshold`), Envoy *ignores* outlier detection and load-balances
  across all hosts including unhealthy ones, because returning zero results is
  worse than trying flapping nodes. *(Red Hat, "Microservices Patterns with
  Envoy Sidecar Proxy, Part I: Circuit Breaking", www.redhat.com/de/blog,
  fetched 2026-10-05; calvin-puram/envoy-web3-rpc-labs circuit-breaking
  README, github.com/calvin-puram/envoy-web3-rpc-labs, fetched 2026-10-05;
  Envoy overview research doc fetched from envoyproxy.io docs 2026-09-13,
  via https://github.com/vitou-vitou/laravel13.x/blob/HEAD/docs/research/envoy-proxy-overview.md)*

The mechanism to copy: **the middleware never trusts its own dependencies**.
Every upstream gets a bulkhead (circuit breaker), a health model (outlier
detection), and a defined behavior when *everything* is down (panic mode) —
and these are configured from the control plane, not hard-coded in the data
path.

### 2.2 API gateways — aggregation without business logic (mechanism)

The practiced gateway pattern: a gateway makes **parallel** backend requests
and aggregates into a single response (`GET /dashboard` → fan-out →
aggregate). Cross-cutting concerns live there: TLS, auth, rate limiting,
circuit breaking, retries with exponential backoff, timeout enforcement,
request deduplication, observability with per-request IDs and distributed
tracing across gateway→services. The enumerated pitfalls are a design law for
any middleware:
1. **Making the gateway too smart** — business logic in the gateway; the fix
   is cross-cutting concerns only.
2. Treating the gateway as the cache (cache at multiple levels instead).
3. Tight coupling to specific services (use discovery, not hard-coded routes).
4. Sequential instead of parallel backend calls.
*(dev.to @said_olano, "API Gateway: The Essential Microservices Component for
Production Systems", fetched 2026-10-05,
https://dev.to/said_olano/api-gateway-the-essential-microservices-component-for-production-systems-emn)*

### 2.3 SRE triage layers — the domain-native middleware (mechanism)

PagerDuty's own alert pipeline is the closest production analogue to Sentinel:
events arrive from many integrations → **normalization** (the PD Events API v2
schema: `routing_key`, `event_action` = trigger/acknowledge/resolve,
`dedup_key`, structured `payload` with summary/severity/source/component) →
**Event Orchestration decision engine** (nested event rules that enrich,
modify, and control routing) → intelligent grouping/dedup → routing to the
right on-call. Their published numbers: 58% average noise reduction, up to 86%
in individual services (IG Group case). *(PagerDuty Event Intelligence
datasheet, www3.pagerduty.com/assets/event-intelligence-datasheet.pdf, fetched
2026-10-05; martechseries.com PagerDuty capabilities piece, fetched
2026-10-05; dev.to @zuplo "PagerDuty API Essentials", fetched 2026-10-05)*

The architectural facts that matter: (a) **dedup is keyed on a declared
identity** (`dedup_key`), not on heuristics — the contract between sender and
pipeline is explicit; (b) **decision is a separate stage** from ingest and
from delivery, with its own rule language; (c) the pipeline's *job* is defined
as noise reduction with measured percentages, i.e., the middleware's value
proposition is quantified, not asserted.

### 2.4 Relevance to Sentinel

- **Sentinel IS this middleware, and its fail-open design is Envoy's panic
  mode.** When the "upstream" (Jev) is down or over budget, the gate routes
  everything to pass-through — degrading to the pre-middleware behavior rather
  than dropping pages. This is the correct instinct and should be named as
  such. The gap: Envoy quantifies and *configures* each isolation mechanism
  per upstream (circuit-breaker thresholds, ejection criteria, panic
  threshold). Sentinel has the budget race (2700ms) but no equivalent of
  **outlier detection for the Jev dependency** — e.g., sustained latency drift
  or error-rate elevation does not trip any breaker; the budget race treats
  every call as independent. A circuit-breaker between gate and Jev (open the
  breaker after N consecutive budget-busts, fail-open while open, half-open
  probe) is the direct Envoy-mechanism transfer.
- **Gateway pitfall #1 ("too smart") is Sentinel's correlator/gate boundary
  question.** Business logic (storm collapse, flap semantics, change windows)
  currently lives in the correlator — which is correct *only if* the
  correlator is defined as the domain-decision stage, not as plumbing.
  PagerDuty's split is the model: normalization (mechanical) vs decision
  engine (rules) vs delivery (mechanical). Sentinel's `receiver` mixes
  normalization with orchestration (it imports correlator, gate, forwarder,
  shadow, policy_lifecycle, health — receiver.py:63–74), which is the
  "gateway got too smart" shape, in-process.
- **`dedup_key` as explicit contract is the mechanism Sentinel's fingerprint
  approximates.** Sentinel computes fingerprints from normalized fields
  (models.py: `scheme-v2 hash(service, check, severity_in, region, env,
  cluster)`). The PagerDuty lesson is to let the *contract* carry identity
  when possible (declared key beats derived hash), because derived hashes
  silently change semantics when normalization changes.

---

## 3. What "layers" actually means in production

### 3.1 Hexagonal / clean architecture — the contract mechanism

Two vocabularies, one mechanism (Cockburn 2005 "ports and adapters"; Uncle Bob's
clean architecture synthesis):

- **The Dependency Rule**: source-code dependencies point inward only.
  Inner circles (entities, use cases) know nothing of outer circles
  (controllers, DB drivers, UI). Crossing a boundary inward is done by
  **dependency inversion**: the inner layer declares an interface (a port);
  the outer layer implements it (an adapter). *(generalistprogrammer.com,
  "Clean Architecture Guide", fetched 2026-10-05,
  http://generalistprogrammer.com/tutorials/clean-architecture-complete-guide)*
- **What crosses a boundary**: "data that crosses a boundary is always in the
  form that is most convenient for the inner circle" (Martin) — simple
  structs/DTOs/plain arguments. Never ORM entities, database rows, or
  framework-specific types inward; that makes the inner circle depend on the
  outer one. *(bakaskillsyan hexagonal-architecture SKILL.md, fetched
  2026-10-05, https://github.com/bakajieyan/bakaskillsyan/blob/HEAD/hexagonal-architecture/SKILL.md)*
- **The anti-corruption layer is a driven adapter that translates a foreign
  model.** External DTOs never cross into the application core; the adapter at
  the edge translates vendor shapes into core shapes. Ports live in the domain
  that owns them, not in a shared `common/ports/` dump. And a restraint rule
  practitioners use: don't create ports for everything (logging, time, config
  don't need them) — ports mark real seams. *(aurelian1974 clean-architecture
  SKILL.md, fetched 2026-10-05,
  https://github.com/aurelian1974/testrepo/blob/HEAD/.claude/skills/clean-architecture/SKILL.md)*
- **The test**: "if you can run core logic from tests with no infrastructure,
  boundaries are correct." *(bakaskillsyan, same source)*

### 3.2 Operational layering — ingest / normalize / decide / act / observe

Hexagonal is the *code-shape* answer. Production adds an *operational* answer:
a request's journey is staged as ingest → normalize → decide → act → observe,
and each stage has different failure, scaling, and change properties:

- **SEDA (Welsh, Culler, Brewer, SOSP 2001)** is the formal version: build the
  server as event-driven **stages separated by explicit queues**. The queues
  are the mechanism — they allow inspection of the request stream,
  prioritization or filtering under heavy load, and per-stage dynamic resource
  control, so a service degrades gracefully instead of overcommitting
  resources when demand exceeds capacity. Stages give modularity, code reuse,
  and debuggability. *(Ph.D. qual proposal and SOSP'01 talk materials,
  https://github.com/mdwelsh/mdwelsh.github.io/raw/b88b7205879ac649c7397e66612c6d0db2564745/astro%2Fpublic%2Fpapers%2Fquals-seda.pdf
  and https://phroxy.z3bra.org/gopher.petergarner.net:70/9/The_TPN_Papers/SEDA_-_An_Architecture_for_Well-Conditioned,_Scalable_Internet_Services_-_Deck_(seda-sosp01-talk).pdf;
  fetched 2026-10-05)*
- **Observe is a stage, not an afterthought.** Envoy emits stats, access logs,
  and distributed-tracing spans per request as a first-class output; gateways
  are expected to carry a request ID through every hop so a slow UI spinner
  resolves to the exact slow query. *(Envoy overview doc, same as §2.1;
  dev.to gateway piece, same as §2.2)* The principal-governance law we already
  carry: "one Trace-ID per user interaction, injected from the browser through
  gateway, microservices, and database" *(~/workspace/skills/principal-governance/SKILL.md)*.

### 3.3 Where contracts live, how they are versioned, what crosses and what never does

- **Contracts live in exactly one place per boundary**, owned by the inner
  (consuming) side, and are versioned artifacts — not comments. The
  principal-governance standing rules: unforgiving API design (changing a
  consumed endpoint is massively disruptive — version explicitly, deprecate on
  a published timeline); zero data-model leakage (never expose the DB schema
  through the API; the backend must be rewritable without breaking upstream);
  state isolation (every entity owned by exactly one service); idempotency by
  default. *(~/workspace/skills/principal-governance/SKILL.md)*
- **What never crosses**: infrastructure types inward (hexagonal rule);
  raw vendor payloads into the decision core (anti-corruption rule);
  another service's database reads (SSOT rule); unversioned breaking changes
  (API contract rule).
- **Postel's law at the edge**: conservative in what you send, liberal in
  what you accept — strict canonical form on emit, resilient parsing on
  ingest. *(principal-governance skill)*

### 3.4 Relevance to Sentinel — the implicit-contracts inventory

Checked against `src/sentinel/` at f1e9563:

1. **One explicit contract exists and is good: `models.py`.**
   `Alert`, `Disposition`, `DecisionRecord`, `Thresholds` are declared the
   "frozen contract §3.2 of ARCHITECTURE.md" in the module docstring. This is
   the single place where a cross-stage contract is named, versioned in prose,
   and shared. Everything below is measured against this bar.
2. **The anti-corruption layer is missing at ingress.** `Alert.raw: dict`
   retains the original vendor payload and the whole `Alert` — `source`
   field included (`"pagerduty" | "alertmanager" | "generic"`) — flows through
   correlator, gate, state-shaping, and audit. Vendor identity and vendor bytes
   cross the decision core. The hexagonal rule says: translate at the edge
   (receiver/integrations), hand the core only canonical shapes, and *drop*
   the raw payload at the boundary except where the audit log explicitly needs
   it (and then it belongs to the audit adapter, not to `Alert`).
3. **Stages exist but queues don't (except one).** receiver → correlator →
   gate → forwarder are synchronous in-process calls (receiver.py:436–474;
   gate→forwarder at :456, :474). Only the forwarder has an explicit durable
   queue (`queue` module + `spill.py`). Per SEDA, the receiver↔gate path has
   no load-conditioning seam: receiver threads (ThreadingHTTPServer,
   receiver.py:876) are held for the full Jev race; the 2700ms budget bounds
   the *call*, not the *thread pool*. Under a Jev latency storm, the receiver
   exhausts threads while the forwarder idles — the failure mode SEDA's
   per-stage queues exist to prevent.
4. **No ports; lateral imports instead of adapters.** `gate.py` imports ~10
   sibling modules directly (`firewall`, `corroboration`, `race`,
   `counterfactual`, `freshness`, `quantized`, `storm_digest` — gate.py:71–88);
   `receiver.py` is simultaneously composition root and business orchestrator
   (imports correlator, gate, forwarder, audit, shadow, policy_lifecycle,
   health — receiver.py:63–74). There is no seam where the Jev client could
   be swapped without touching the gate, and no place where a layer's
   contract is declared except in prose. The hexagonal test — "run core logic
   in tests with no infrastructure" — fails for the gate as currently wired.
5. **`Disposition.action`/`reason` are stringly-typed.** Bare strings
   (`"page_now"`, `"suppress"`, `"storm"`, `"error:<code>"`) cross every layer
   with no machine-checkable contract. The attestor ADR
   (`docs/adr-attestor-identity.md`) already established this organization's
   lesson about bare strings as trust roots; the same reasoning applies to the
   action vocabulary that decides whether a human gets paged.
6. **Observe is present but not a stage.** The event log and shadow pipeline
   exist, but there is no per-alert trace ID carried receiver→correlator→gate→
   forwarder→platform API, and no per-stage latency attribution in the hot
   path — the gateway/Envoy practice of one ID through every hop is not
   implemented.

---

## 4. API layering: BFF, gateway aggregation, versioning/deprecation

### 4.1 Versioning — three practiced strategies, one agreement

Google, Azure, and Stripe version differently and all three are defensible:
- **Google (AIP-185):** `v1` in the path; never `v1.1`.
- **Azure:** required `?api-version=2026-01-01` query parameter; no version in
  the path.
- **Stripe:** rolling date-named versions; **each account is pinned to the
  version it first used**; a transformation layer converts responses for older
  pinned versions so business logic never carries version-conditional
  branches.
- **The agreement: version only when you can't make the change
  backward-compatible.** Safe: adding optional fields, new endpoints, new
  optional params. Breaking: removing/renaming/retyping a field, making an
  optional param required.
*(dev.to @freelance_inspector, "Google, Azure and Stripe version APIs three
different ways", Sept 2026, fetched 2026-10-05,
https://dev.to/freelance_inspector/google-azure-and-stripe-version-apis-three-different-ways-heres-what-they-agree-on-3mbg ;
kariemseiam playbooks rest-api-design.md, fetched 2026-10-05,
https://github.com/kariemseiam/playbooks/blob/HEAD/software-engineering/rest-api-design.md)*

**Deprecation is a protocol, not a blog post.** `Deprecation` (RFC 9745) starts
the clock; `Sunset` (RFC 8594) names when the old version stops working; the
sunset date can never precede the deprecation date. *(same dev.to source)*
One practitioner's warning worth keeping: versioning is a necessary evil —
each new version multiplies the maintained surface (30 endpoints × N versions),
so prefer additive evolution inside a version and reserve new versions for
true breaks. *(sgoedecke, "Good API Design",
https://github.com/sgoedecke/gatsby-blog/blob/master/content/blog/good-api-design/index.md,
fetched 2026-10-05)*

### 4.2 BFF — the per-consumer aggregation layer (mechanism)

The Backend-for-Frontend pattern (popularized by SoundCloud): a dedicated
backend per frontend application that aggregates multiple service calls into
the shapes that UI needs, holds the secrets the browser must never see
(API keys in server-side env, never in the frontend bundle), and filters
formats before responding. The Prism UI ↔ platform API relationship is
exactly this shape when done deliberately rather than accidentally.
*(securityboulevard.com, "Stop Leaking API Keys: The Backend for Frontend
(BFF) Pattern Explained", Jan 2026, fetched 2026-10-05,
https://securityboulevard.com/2026/01/stop-leaking-api-keys-the-backend-for-frontend-bff-pattern-explained/ ;
developers.dev, "The API Gateway Bottleneck" recovery playbook — BFF as the
decomposition strategy when a central gateway gets bloated, fetched
2026-10-05,
https://www.developers.dev/tech-talk/the-api-gateway-bottleneck-a-performance-and-architectural-recovery-playbook.html)*

The caution from the same playbook: BFF decomposition is high-effort,
high-overhead — justified when distinct consumers have genuinely different
data needs, not as a default.

### 4.3 Relevance to Sentinel

- **The platform API is half-versioned.** `platform/server/app.py` serves
  `/api/decisions`, `/api/calibration`, `/api/simulate`,
  `/api/analytics/noise`, `/api/analytics/flips` with **no version**, next to
  `/api/v1/integrations/status`, `/api/v1/integrations/keys`,
  `/api/v1/integrations/simulated` (app.py:176–197). Mixed versioning on one
  surface is worse than either convention — consumers cannot tell which paths
  are stable. And while `platform/contracts/openapi.yaml` exists at v1.0.0
  and the server docstring claims to implement it "exactly" (app.py:3), there
  is **no deprecation policy**: no `Deprecation`/`Sunset` headers, no
  published retirement timeline, no additive-change discipline documented.
  The Google/Azure/Stripe agreement gives the rule to adopt: additive inside
  a version; a new version (or a date-pinned transformation layer, Stripe
  style) only for true breaks.
- **Prism is an accidental BFF.** The UI (`platform/ui`) talks to the
  platform server, which aggregates engine state (decisions, calibration,
  analytics) into UI-shaped responses — but this is not declared as a BFF
  with a contract; it is just the server growing UI-shaped endpoints
  (`/api/analytics/noise`, `/api/analytics/flips`). The BFF lesson: either
  name it a BFF (with its own versioned contract, owned by the UI's needs)
  or keep the platform API as a pure engine surface and aggregate in the UI.
  The current middle state is where gateway-bloat begins.
- **The receiver's external contract is the PD Events API v2 shape.**
  Sentinel deliberately mirrors PagerDuty's Events API (`POST /v2/enqueue`,
  receiver.py:238) as its ingress contract — a sound Postel's-law move
  (liberal ingest of a known shape). The versioning question applies here
  too: the day Sentinel's ingress needs a breaking change, the Stripe
  transformation-layer pattern (serve old shape, translate internally) is the
  fit, because customers have this URL hard-coded in their alerting
  integrations and cannot migrate on Sentinel's schedule.

---

## 5. Explicit-layers checklist

What a well-layered system has that an implicit one lacks. Each item is
checkable against the codebase:

1. **Named layers with one job each** — e.g. ingest / normalize / decide /
   act / observe (operational) mapped onto code units; no module does two
   layers' jobs. *(SEDA; PagerDuty pipeline)*
2. **A declared contract per boundary**, owned by the consuming side,
   versioned as an artifact — not prose in a docstring. *(hexagonal ports;
   principal-governance: unforgiving API design)*
3. **Anti-corruption at every foreign edge**: vendor payloads translated to
   canonical shapes at ingress; raw bytes never cross the decision core.
   *(hexagonal driven adapters)*
4. **Only inner-circle-convenient shapes cross inward**: plain data, never
   infrastructure/ORM/framework types, never vendor DTOs. *(Martin via
   bakaskillsyan)*
5. **Ports, not imports, between core and infrastructure**: the core declares
   interfaces; adapters implement them; the core's tests run with zero
   infrastructure. *(hexagonal test)*
6. **Explicit queues (or declared equivalents) between stages**, each with
   backpressure, overflow, and observability semantics — the load-conditioning
   seam. *(SEDA)*
7. **Failure isolation between layers**: circuit breaker + health model +
   defined total-failure behavior (panic mode) per dependency, configured
   from above the data path. *(Envoy)*
8. **One trace/correlation ID across every hop** of a unit of work, with
   per-stage latency attribution. *(Envoy/gateway observability;
   principal-governance unified telemetry)*
9. **Idempotency keys on critical writes** so retries across layer boundaries
   are safe. *(principal-governance)*
10. **Versioned public contracts with a deprecation protocol**
    (Deprecation/Sunset headers, published timelines); additive change inside
    a version, new version only for true breaks. *(Google/Azure/Stripe
    consensus; RFC 9745; RFC 8594)*
11. **Zero data-model leakage**: internal schemas never visible through a
    boundary; each side rewritable without breaking the other.
    *(principal-governance)*
12. **SSOT per entity**: every record owned by exactly one layer/service;
    others query or subscribe. *(principal-governance)*

Scorecard against Sentinel today (honest): #1 partial (stages named, but
receiver mixes layers); #2 partial (`models.py` only); #3 missing; #4 partial
(`Alert.raw` violates it); #5 missing; #6 partial (forwarder only); #7 partial
(budget race, no breaker/outlier-detection); #8 missing; #9 present in
forwarder paths (verify per-path before claiming); #10 partial (openapi.yaml
v1.0.0 exists, mixed path versioning, no deprecation protocol); #11 partial;
#12 present for decisions (event log) — untested elsewhere.

---

## 6. The three sharpest findings

1. **Sentinel has stages but not layers.** receiver→correlator→gate→forwarder
   are named pipeline *steps* executed as synchronous in-process calls with
   lateral imports and no declared inter-stage contracts, no queues, and no
   per-dependency isolation between them. SEDA's whole point is that the
   *queue between stages* is the architecture — it is what gives you load
   conditioning, per-stage backpressure, and independent failure behavior.
   Sentinel has exactly one such seam (the forwarder's queue+spill) and needs
   the concept applied deliberately at the receiver↔gate boundary, where
   receiver threads are currently held hostage to the Jev latency
   distribution. *(Welsh/Culler/Brewer SOSP 2001; receiver.py:436–474, 876)*

2. **The missing anti-corruption layer is a trust issue, not a style issue.**
   `Alert.raw` (the full vendor payload) and `Alert.source` (vendor identity)
   flow through the decision core and into AI state-shaping. Hexagonal practice
   says the edge translates and the core never sees foreign shapes; the
   PagerDuty analogue shows the alternative (`dedup_key` as declared identity
   rather than derived fingerprint). Combined with stringly-typed
   `Disposition.action`/`reason` crossing every layer, the pipeline's most
   safety-critical vocabulary — the words that decide whether a human is
   paged — has no machine-checkable contract. The attestor ADR already taught
   this organization the bare-strings lesson; it applies here with higher
   stakes. *(aurelian1974 SKILL.md; models.py; docs/adr-attestor-identity.md)*

3. **Fail-open is Envoy's panic mode — name it, then finish it.**
   Sentinel's best architectural instinct (any error → pass-through to the
   existing pipeline) is exactly Envoy's panic mode: when all upstreams are
   ejected, ignore the health model and route everywhere rather than return
   nothing. But Envoy *quantifies* the path to panic (circuit-breaker
   thresholds, outlier ejection criteria, panic threshold, re-admission
   probing), while Sentinel has only the per-call 2700ms budget race. There
   is no circuit breaker between gate and Jev, no outlier detection on Jev
   latency/error drift, no half-open probing. The transfer is direct and
   mechanical. *(Red Hat Envoy circuit-breaking piece; envoy-web3-rpc-labs
   panic-mode experiments; gate.py race logic)*

---

## 7. Sources

All fetched 2026-10-05 unless noted.

**Event-driven / Kafka / ES / CQRS**
- Fowler, M. "What do you mean by Event-Driven?" (Jan 2017, upd. 2017-02-08).
  https://martinfowler.com/articles/201701-event-driven.html
- HN comment thread, "What every software engineer should know about Apache
  Kafka". https://news.ycombinator.com/item?id=23206566
- @code_pulse_devrim. "Why We Ripped Out Our Event-Driven Architecture After
  Two Years" (May 2026).
  https://medium.com/@code_pulse_devrim/why-we-ripped-out-our-event-driven-architecture-after-two-years-and-built-boring-synchronous-apis-b4ac7b9d1abb
- @sriraghukatragadda. "Why Event-Driven Architecture and Kafka Actually
  Exist — A Real Production Walkthrough".
  https://medium.com/@sriraghukatragadda/why-event-driven-architecture-and-kafka-actually-exist-a-real-production-walkthrough-5a0dd5288ed2
- @harishsingh8529. "We Rebuilt Our Kafka Pipeline Using CQRS and Redis"
  (Jul 21, 2025).
  https://medium.com/@harishsingh8529/we-rebuilt-our-kafka-pipeline-using-cqrs-and-redis-heres-what-broke-6cb100321214
- foojay.io. "When NOT TO USE Event-Driven Architecture (EDA)".
  http://foojay.io/today/when-not-to-use-event-driven-architecture-eda/
- prokstudio/skills, event-driven-architecture SKILL.md.
  https://github.com/prokstudio/skills/blob/HEAD/skills/architecture/event-driven-architecture/SKILL.md
- osvaldojramos/dotnet-senior-study-guide, event-sourcing.md.
  https://github.com/osvaldojramos/dotnet-senior-study-guide/blob/HEAD/08-architecture-and-patterns/15-event-sourcing.md

**Middleware (Envoy, gateways, SRE triage)**
- Envoy overview (official docs fetched 2026-09-13, via research mirror).
  https://github.com/vitou-vitou/laravel13.x/blob/HEAD/docs/research/envoy-proxy-overview.md
  (primary: https://www.envoyproxy.io/docs/envoy/latest/intro/what_is_envoy)
- Bomberbot. "A Deep Dive into Envoy Proxy: The Universal Data Plane".
  https://www.bomberbot.com/proxy/a-deep-dive-into-envoy-proxy-the-universal-data-plane/
- Red Hat. "Microservices Patterns with Envoy Sidecar Proxy, Part I: Circuit
  Breaking". https://www.redhat.com/de/blog/microservices-patterns-envoy-part-i
- calvin-puram/envoy-web3-rpc-labs, circuit-breaking README (panic-mode
  experiments). https://github.com/calvin-puram/envoy-web3-rpc-labs/blob/HEAD/circuit-breaking/README.md
- qu3ry.net. "Envoy Proxy Made Service Mesh Data Planes Programmable. The
  Control Plane Still Governs." https://qu3ry.net/articles/adaptive-indexing-envoy-proxy.pdf
- @said_olano. "API Gateway: The Essential Microservices Component for
  Production Systems". https://dev.to/said_olano/api-gateway-the-essential-microservices-component-for-production-systems-emn
- PagerDuty Event Intelligence datasheet.
  https://www3.pagerduty.com/assets/event-intelligence-datasheet.pdf
- martechseries.com. "New PagerDuty Capabilities…" (Event Orchestration).
  https://martechseries.com/sales-marketing/customer-experience-management/new-pagerduty-capabilities-empower-businesses-to-deliver-exceptional-customer-experiences-with-automated-incident-response/
- @zuplo. "PagerDuty API Essentials: A Guide". https://dev.to/zuplo/pagerduty-api-essentials-a-guide-19ha

**Layers (hexagonal/clean, SEDA, operational)**
- generalistprogrammer.com. "Clean Architecture Guide: Layers, Dependency
  Rule & vs Onion". http://generalistprogrammer.com/tutorials/clean-architecture-complete-guide
- bakajieyan/bakaskillsyan, hexagonal-architecture SKILL.md.
  https://github.com/bakajieyan/bakaskillsyan/blob/HEAD/hexagonal-architecture/SKILL.md
- aurelian1974/testrepo, clean-architecture SKILL.md.
  https://github.com/aurelian1974/testrepo/blob/HEAD/.claude/skills/clean-architecture/SKILL.md
- Welsh, M., Culler, D., Brewer, E. SEDA qual proposal.
  https://github.com/mdwelsh/mdwelsh.github.io/raw/b88b7205879ac649c7397e66612c6d0db2564745/astro%2Fpublic%2Fpapers%2Fquals-seda.pdf
- Welsh, M. SEDA SOSP'01 talk deck.
  https://phroxy.z3bra.org/gopher.petergarner.net:70/9/The_TPN_Papers/SEDA_-_An_Architecture_for_Well-Conditioned,_Scalable_Internet_Services_-_Deck_(seda-sosp01-talk).pdf
- Workspace skills (standing law, not web): principal-systems,
  principal-governance (unforgiving APIs, Postel's law, SSOT, idempotency,
  unified telemetry).

**API layering (BFF, versioning)**
- @freelance_inspector. "Google, Azure and Stripe version APIs three different
  ways" (Sep 2026).
  https://dev.to/freelance_inspector/google-azure-and-stripe-version-apis-three-different-ways-heres-what-they-agree-on-3mbg
- kariemseiam/playbooks, rest-api-design.md.
  https://github.com/kariemseiam/playbooks/blob/HEAD/software-engineering/rest-api-design.md
- sgoedecke. "Good API Design".
  https://github.com/sgoedecke/gatsby-blog/blob/master/content/blog/good-api-design/index.md
- developers.dev. "The API Gateway Bottleneck: A Performance and
  Architectural Recovery Playbook".
  https://www.developers.dev/tech-talk/the-api-gateway-bottleneck-a-performance-and-architectural-recovery-playbook.html
- securityboulevard.com. "Stop Leaking API Keys: The Backend for Frontend
  (BFF) Pattern Explained" (Jan 2026).
  https://securityboulevard.com/2026/01/stop-leaking-api-keys-the-backend-for-frontend-bff-pattern-explained/

**Sentinel-internal evidence** (code read, not web): `ARCHITECTURE.md`,
`src/sentinel/models.py`, `receiver.py` (imports :63–74, pipeline :436–474,
server :876), `gate.py` (imports :71–88, race/budget), `forwarder.py`
(queue, spill), `platform/server/app.py` (:3, :176–197), `platform/contracts/
openapi.yaml` (v1.0.0), `docs/adr-attestor-identity.md`,
`docs/adr-decisions-2026-10-03.md`.
