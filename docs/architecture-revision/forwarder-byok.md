# Domain Review: Forwarder + BYOK Paging (Delivery Guarantees, Key Management)

**Reviewer:** Phase-2 domain reviewer (fresh eyes, did not build this)
**Branch:** `program/architecture-revision-dom-forwarder`
**Date:** 2026-10-05
**Scope:** `src/sentinel/forwarder.py` (both `DurableForwarder` and the legacy `Forwarder`), `src/sentinel/pd_sender.py`, `src/sentinel/integrations.py`, `src/sentinel/spill.py`, plus `tests/test_durable_forwarder.py`, `tests/test_forwarder.py`, `tests/test_integrations.py`, `tests/helpers.py`. Judged against `research/operating-without-vendor-apis.md` (faithful-mock checklist), `research/devops.md` (observability §3.3), `research/testing-at-scale.md`, and the four principal skills.

Reading posture, per `principal-mindset`: what is genuinely good here is named before what is broken. The honesty contract (at-least-once claimed, exactly-once explicitly disclaimed) is the strongest vendor-boundary discipline in the repo. The rest of this memo is brutal anyway.

---

## 1. WHAT EXISTS

### 1.1 Delivery flow (durable path — `DurableForwarder`)

One page, one content, one contract: every decided page enters the durable outbox (eventlog `outbox` table, I1) **before** the forwarder loop ever sees it. The scheduler thread claims due rows (`queued → in_flight` via a single `UPDATE ... WHERE status='queued'` — two forwarders cannot claim the same row), hands them to a worker pool, and every attempt ends in exactly one I2 receipt (`forward_confirmed` / `forward_failed`) written through the EventLog's single writer. The verdicts below are grounded in the code paths, not the comments:

- **Claim + lease:** claims increment `attempt_count` in the claim `UPDATE`; startup scan replays spills first, then re-queues `in_flight` rows whose lease (300 s default) is stale, recording the crash-window attempt honestly as `crash_window_requeue`. A kill -9 between "vendor accepted" and "receipt written" can neither lose the page (the row is durable) nor double-page (the retry reuses the row's pinned `dedup_key`; PD appends to the open alert). `tests/test_durable_forwarder.py` proves both residual classes (Case 1: retry onto an open alert = one incident; Case 2: retry after human resolve = one loud self-describing new alert with `sentinel_retry` annotations). `os._exit(1)` crash-injection hook exists for exactly this test.
- **Retry schedule:** 5 s → 15 s → 45 s → 2 m → 5 m → 15 m → 30 m → 30 m capped, each ±20 % decorrelated jitter. 429 ⇒ `max(scheduled, 60 s)`, honoring `Retry-After` when present — which matches the vendor's actual behavior: **PD sends no `Retry-After` header** (research §4, PD rate-limits doc), so the 60 s floor is the operative path, and there is a test asserting both branches. 5xx and network errors retry; any other 4xx is terminal (retrying identical bytes is futile); a 202 whose body is not `{"status":"success"}` is a vendor-contract violation ⇒ retryable + loud.
- **The vendor contract is data.** `PD_RETRY_TABLE` in `pd_sender.py` encodes 202/400/429/5xx/network, verified live against PD docs 2026-10-03, and a test asserts the classifier matches the table row-for-row — a PD docs change becomes a build break, not a 3 AM discovery. This is the single strongest enterprise practice in the domain (research §5 item 1: "the contract is the coordination mechanism").
- **Idempotency property:** `dedup_key` comes from the ROW, re-derived never; > 255 chars is terminal (PD's documented limit, enforced); PD's own dedup is the idempotency, and the module explicitly disclaims exactly-once. Attempt annotations (`sentinel_retry`/`attempt_no`) on retry > 1 make the bounded duplicate class self-describing in the human's alert.
- **Payload hygiene:** `payload_frozen` is stored keyless on disk (the routing key lives in the PD-CEF *body*, so freezing the literal bytes would put the secret on disk); the sender injects the resolved key at send time and records `wire_sha256` on the receipt; a frozen payload containing `routing_key` raises `PayloadNotKeyless` ⇒ terminal. sha256 integrity check refuses to send tampered pages.
- **Secondary channel:** rows undelivered past 15 min fire a secondary webhook (bounded: 3 tries); **primary retries never stop** — `secondary_fired_at` is a timestamp, not a status, and no code path cancels primary retries (design §8, enforced in `_secondary_scan`). Terminal primary failures (400 / our bug) dead-letter *and* fire the secondary with an annotation *and* enqueue a control-plane page to the engineering on-call. The secondary is drill-gated: `require_drilled_secondary` refuses to start in staged modes without a human-acked drill inside 10 min.
- **Max-age sweep:** rows past `max_age_at` ⇒ `dead_letter`, loudly (stderr + control-plane page). Rows with `max_age_at <= created_at` are treated as IMMORTAL (protects control-plane pages) — a Chesterton's-fence-aware read of the event-log lane's enqueue behavior, flagged to the coordinator.
- **Standby (degraded ladder):** `send_direct` bypasses the outbox when it is unavailable, posts direct-to-PD, and writes an emergency spill record (stderr first, write-then-rename JSON file second); the next startup replays spills into the event log as `forward_confirmed`/`forward_failed` with `channel="direct-degraded"`. This is the fail-open backstop that keeps the promise to the human when the primary durable path is dead.

### 1.2 Key management (BYOK)

Two separate mechanisms, and they do not agree (details in §2.1):

- **`IntegrationStore`** (`integrations.py`): 600-perm JSON under `$SENTINEL_STATE_DIR` (gitignored), write-only values (`status()` reports configured/last4 only), ephemeral-store loud-fail (writes raise 501 instead of silently pretending to persist), atomic `set_many` (validate-all-then-persist, from Vault PR #77), key-format validation with operator-language errors that never echo the value. Surfaced via the platform API (`platform/server/app.py`) as the Integrations settings UI. Resolution order documented as **user store → env (`PD_ROUTING_KEY`) → unconfigured**, and simulated paging short-circuits before key resolution, labeled in-band.
- **`pd_sender.resolve_routing_key`** (env-only): `secret:<name>` refs map to `SENTINEL_SECRET_<NAME>` env vars; bare refs map to their env name. Raises `SecretMissing` (never carries the value).
- **Error-path hygiene:** `sanitize_error` is the one wired emission boundary — the forwarder knows the configured key values and strips them from error strings on every forward failure path (the PR #77 review deleted the advertised-but-uncalled `redact()`/`scrub_record()` helpers, which is honest subtraction).
- **Rotation:** pinning is in-memory only (`_key_pins` dict, retries reuse the first-attempt key); the runbook is a comment ("rotation runbook: drain first"). There is no rotation API, no key versioning, no overlap window, no expiry, no audit of key changes.

### 1.3 Simulated vs real paths

- Legacy `Forwarder.forward` and `DurableForwarder.send_direct` check `simulated_paging()` *before* key resolution: the send is absorbed, logged loudly to stderr (`SIMULATED PAGE ... nothing was sent to PagerDuty`), and counted separately (`simulated` metric). A simulated page needs no key — correct, and honestly labeled.
- Simulated mode is **not** a vendor mock: it never exercises the wire path (research §7 item 4 — this module already honors that distinction; the mock gap is in §2.3 below).

### 1.4 What is genuinely good (do not redesign this)

`principal-systems` operating rule 2 (disagree-and-commit) applies: dissent is recorded above; the following survives it. The retry-table-as-data, the no-exactly-once honesty contract, the lease-based crash recovery, the secondary-never-cancels-primary invariant, the keyless-on-disk payload decision, and the single wired `sanitize_error` boundary are all enterprise-grade. The revision proposals in §3 *add to* this, they do not restate it.

---

## 2. WHAT'S MISSING

Measured against the Phase-1 enterprise standard. Each gap cites the research.

### 2.1 THE DAMNING ONE: key-resolution split-brain between the BYOK surface and the production path

`DurableForwarder._pinned_key` resolves `routing_key_ref` through **`pd_sender.resolve_routing_key` — env vars only** (`SENTINEL_SECRET_PD_ROUTING_KEY` for user pages, `SENTINEL_SECRET_PD_CONTROL_ROUTING_KEY` for control-plane pages). It never consults `IntegrationStore`. Meanwhile `resolve_paging_key` (user store → env → unconfigured) is consulted by the legacy `Forwarder._resolve_key`, by the degraded `send_direct` path (which prefers the user's key, then the control-plane ref), and by simulated-mode logging — everything *except* the durable production path.

Consequences, all verified in code:

1. An operator who configures their PagerDuty key **only** through the Integrations UI — the designed BYOK surface — gets `SENTINEL_SECRET_PD_ROUTING_KEY` unset, so every production page raises `SecretMissing` inside `_pinned_key` ⇒ `_retryable("secret_missing", backoff)` ⇒ retries on the backoff schedule until max age ⇒ **`dead_letter`**. The UI reports `configured: true` (last4) while the forwarder dead-letters every page. Two code paths in the same file disagree about where keys live (`_attempt` vs `send_direct`).
2. Nothing in the repo bridges `IntegrationStore` into env vars — grep for the two `SENTINEL_SECRET_PD_*` names finds them in tests and dynamic-mapping code only; no deployment doc on this branch wires the store into the process environment (the task's "per-decision BYOK key resolution (user key → env → unconfigured)" describes the legacy/degraded paths, not the durable one).
3. The legacy `Forwarder` still exists for receiver/gate call sites and resolves keys the *other* way — so two forwarders in one process family have opposite key semantics. When the legacy path is eventually retired, the UI-surface keys silently stop working everywhere.

`principal-mindset` proxy check: the Integrations UI's "configured" status is a proxy for "pages will go out"; it rank-orders with nothing. This is the one finding that makes the BYOK feature fail closed against its own operator.

### 2.2 Delivery observability: the forwarder's metrics die in-process

`DurableForwarder.metrics` (`claimed`, `confirmed`, `retryable`, `dead_letter`, `secondary_fired`, `spills_replayed`, `requeued`, `control_plane_pages`) is **write-only**: the only consumers in the repo are the tests. There is no `/metrics` endpoint, no Prometheus text export, no delivery SLO, no burn-rate alerting, no correlation ID across the webhook→decision→forward chain. Research `devops.md` §3.3 names exactly this stack of gaps and calls the missing metrics endpoint "the single highest-leverage observability gap": ~50 lines of stdlib unlocks burn-rate math, canary auto-gates, and dashboards. For a paging pipeline, *delivery health you cannot see* is the enterprise bar failing: the 3 AM operator's only signals today are stderr lines (`FORWARD FAILED`, `dead_letter`) and journald scraping — logs, not metrics (metrics *detect*, logs *explain*; §3.3's correlation triangle). There is also no written SLO/error budget for the paging pipeline, so there is no release-policy governor and no objective paging policy for the external watcher.

### 2.3 The faithful-mock checklist: behavior layer is half-built, failure layer is scripted-not-faulted

`tests/test_durable_forwarder.py`'s `FakePD` is a genuine **fake** (research `testing-at-scale.md` Q1: "prefer fakes over mocks for stateful dependencies") with real trigger-dedup + resolve semantics — the vendor-mock research's claim of "no stateful PD mock" was written against the older `CaptureServer` and is now half-outdated. What the faithful-mock checklist (`operating-without-vendor-apis.md` §6) still does not have:

- **Behavior:** incidents are keyed by `dedup_key` only, not `(routing_key, dedup_key)` — PD scopes dedup by routing key, and events via a *different* key than the original trigger are **dropped** (§4). Ack semantics are absent entirely (ack on open alert ⇒ suppressed notifications; ack with no open alert ⇒ dropped, still 202). The forwarder's routing-key pinning and its cross-restart behavior therefore run against a mock that cannot reproduce the vendor behavior pinning exists to manage.
- **Schema:** the mock never 400s on `dedup_key` > 255, missing/invalid `routing_key`, `payload.summary` > 1024, or > 512 KB bodies — the 400 rows of the retry table are asserted against the *classifier*, never against *vendor-shaped rejection*.
- **Failure:** `ScriptedHTTP` scripts status codes and headers (429 with/without `Retry-After` is covered — good), but there is no connection-reset / timeout / malformed-chunk fault injection for the degraded ladder (WireMock's fault vocabulary, §6), and no programmable latency for timeout tuning.
- **Provider-verification half is missing** (`testing-at-scale.md` Q2 caveat): the "verified live 2026-10-03" comment has no mechanical refresh. The row-for-row test asserts *code == table*, not *table == reality*. Per the research, neither PagerDuty nor Jev will verify Sentinel's contracts, so Sentinel needs its own scheduled drift harness (read-only probes + recorded cassettes as anchors) — nothing runs one today.

### 2.4 Key rotation, multi-key isolation, lifecycle

- **No rotation story.** Pins are in-memory (`_key_pins`), so a restart drops them and in-flight rows re-resolve — potentially under a *different* key. Combined with PD's routing-key-scoped dedup (§2.3), a post-restart retry under a rotated key may be **dropped by PD** (or create a new alert) — the one failure mode the pinning design exists to prevent, untested across the restart boundary. The "drain first" runbook is a comment, not a mechanism.
- **No multi-key isolation.** `routing_key_ref` is hardcoded (`"secret:pd/routing_key"`, `"secret:pd/control_routing_key"`); there is no per-service/per-team routing map, no tenant scoping. Whether that is product scope or a gap is Aditya's call — but the enterprise standard (and the `principal-governance` state-isolation clause) says delivery routing is a first-class routing decision, not a constant.
- **No key-change audit.** `IntegrationStore.set`/`delete` write no audit event; a key changed at 2 AM is invisible in the event log.

### 2.5 Audit-honesty defect: simulated degraded pages replay as `forward_failed`

In `DurableForwarder.send_direct`'s simulated branch, a spill is written with `pd_outcome: "simulated"` and `simulated: True`. At the next startup, `spill.replay_spills` computes `accepted = (pd_outcome == "accepted")` ⇒ `False` ⇒ emits a **`forward_failed`** event with `error_class: "degraded_send_failed"` — and the `"simulated"` flag is not even propagated into the event body. A page that was honestly absorbed by simulated mode (nothing failed) lands in the tamper-evident audit log as a delivery failure. The audit trail is the one artifact that must never lie.

### 2.6 Cross-lane coupling via a private method

`enqueue_control_plane_page` calls the event-log lane's **private** `_enqueue_control_plane_page` (pinned at lane commit `b2e0005`), failing loudly if it disappears. Loud failure is better than silent rot, but a private-method call across lanes is not a contract — it is a handshake with one person's naming choice. `principal-governance` §2 (APIs as legal contracts) applies even in-monorepo: the control-plane page API should be public, typed, and interface-pinned.

---

## 3. CONCRETE REVISION PROPOSALS

Ranked by value = (user-visible risk removed) × (enterprise-bar movement). Each states what would *verify* it — no proposal without a falsifier.

### P1 — One key-resolution contract for every path (fixes §2.1)

**Rationale.** Two resolvers with different precedence orders feeding two forwarders is a correctness bug, not a style issue: the UI-configured key never reaches the production path, and the UI lies about it. `principal-governance`: unforgiving API design, single source of truth — one resolver, one documented order, every path uses it.

**Proposal.** Replace both with a single `resolve_routing_key_for(ref)` used by `DurableForwarder._pinned_key`, the legacy `Forwarder`, and `send_direct`, with the order: IntegrationStore user key → `secret:`-mapped env → `PD_ROUTING_KEY` env → `SecretMissing`. Keep the "secrets never touch disk" invariant: the store is read at send time and injected into wire bytes, same as env today. Add a loud startup/poll warning when the UI reports a key configured but the durable path cannot resolve one (the proxy must be fused to the truth).

**Verifies by:** an end-to-end test that sets a key *only* via `IntegrationStore` (env scrubbed), runs `run_once_sync` through the durable path, and asserts `forward_confirmed` — plus a regression test that legacy and durable paths resolve identically for the same row. Falsifier: any path resolving a key the others cannot.

### P2 — Delivery observability: `/metrics` + first SLO + burn-rate alerting (fixes §2.2)

**Rationale.** The highest-leverage enterprise gap per Phase-1 research (`devops.md` §3.3): ~50 lines of stdlib Prometheus text format from the already-collected `metrics` dict (forward success/failure rate, retry counts, PD latency, secondary fires, dead-letters, spill replays) unlocks burn-rate math, canary auto-gates, and dashboards. Without it, delivery health is journald-grep — the 3 AM operator is flying blind on the metric that matters.

**Proposal.** (a) A `/metrics` route emitting the forwarder (and receiver) counters as Prometheus text, scraped by the existing external watcher. (b) Write the first paging-pipeline SLO — e.g. "99.9% of real pages `forward_confirmed` (primary or secondary) within 30 days" — as the release-policy governor (budget burned ⇒ feature freeze, per Google SRE lineage). (c) A shared correlation ID from webhook ingress through the forward receipt, on every log line and in the event body, closing the governance checklist's trace-ID demand.

**Verifies by:** scrape `/metrics` while injecting failures into FakePD — counters move; the burn-rate alert fires on a scripted 15-minute outage; a single page is traceable ingress→receipt by one ID. Falsifier: an incident class that changes no exported series.

### P3 — Key rotation protocol (fixes §2.4, first half)

**Rationale.** "Drain first" as a comment is not a rotation story. In-memory pins + no overlap window + no audit means rotation is a human procedure with a crash window (restart mid-rotation ⇒ re-resolve under a new key ⇒ PD's routing-key-scoped dedup may drop the retry — §2.3).

**Proposal.** (a) `IntegrationStore` gains versioned keys (`current` + `next` with an overlap window) and every set/delete appends an audit event (actor, key name, last4, never the value). (b) Pinning becomes durable: persist the pinned ref+key-fingerprint per outbox row (not the value) so a restart re-pins instead of re-resolving — or deliberately re-pin with an audit annotation. (c) A mechanized `sentinel rotate-keys` flow: install `next`, overlap, drain in-flight, promote, retire — the runbook as code, not prose.

**Verifies by:** rotate keys while rows are in flight against FakePD extended with routing-key-scoped dedup (§3.4) — zero `SecretMissing`, zero dropped retries, zero extra incidents. Falsifier: any in-flight row changing keys without an audit annotation.

### P4 — Complete the faithful mock: stateful + schema + fault layers (fixes §2.3)

**Rationale.** The tests prove the forwarder's logic against the author's mental model of PD; the research's faithful-mock checklist (`operating-without-vendor-apis.md` §6) is the standard for proving it against PD's *actual* model. The two gaps that bite: routing-key-scoped dedup (the pinning design's entire reason to exist) and ack semantics.

**Proposal.** Extend `FakePD`: (a) incidents keyed by `(routing_key, dedup_key)`; different-key events dropped per PD docs; (b) ack/resolve state machine (ack ⇒ suppressed notifications; ack-with-no-open-alert ⇒ dropped, still 202; resolve ⇒ later trigger creates a NEW incident); (c) schema 400s (dedup_key > 255, missing/invalid routing key, summary > 1024, > 512 KB); (d) fault injection (connection reset, timeout, malformed chunk, programmable latency) for the degraded ladder; (e) fail-loud on unknown routes. Keep stdlib-only (`http.server` + threads), per the hygiene layer.

**Verifies by:** one test per checklist row — a matrix that stays green only if both forwarder and mock agree with the PD docs. Falsifier: a PD-docs behavior with no corresponding test (the checklist itself is the coverage gate).

### P5 — Scheduled provider-contract suite with recorded cassettes (fixes §2.3, drift half)

**Rationale.** The row-for-row retry-table test is code==table, not table==reality. Per `testing-at-scale.md` Q2's third-party caveat, the missing half must be owned by Sentinel: a scheduled job against live PD with the team's own test key (read-only probes + one trigger/ack/resolve cycle on a sacrificial service), diffing response shapes against checked-in contracts, with recorded responses committed as scrubbed cassettes — drift surfaces as a failing test, not a 3 AM discovery. Secrets stay in CI secrets; the operator never needs a PD account (BYOK reality, per research §3).

**Verifies by:** run it once manually, commit the cassettes; a deliberate response-shape change in the mock suite trips the diff. Falsifier: six months pass with no refresh run (the suite must page/issue on schedule miss, not just on diff).

### P6 — Simulated-spill audit honesty (fixes §2.5)

**Rationale.** The audit log must never record a non-failure as a failure. Small, surgical, high-integrity.

**Proposal.** `replay_spills` propagates `simulated: True` into the event body and replays simulated records as `forward_confirmed` with `channel="direct-degraded"` + `simulated: true` (or a distinct `forward_simulated` event) — never `forward_failed`/`degraded_send_failed`.

**Verifies by:** spill→replay roundtrip test: simulated spill ⇒ audit event carrying `simulated: true`, zero `forward_failed` emissions. Falsifier: any code path that can emit `forward_failed` for a page that was never attempted on the wire.

### P7 — Per-service routing map (fixes §2.4, second half)

**Rationale.** A single hardcoded `routing_key_ref` makes multi-team paging impossible without forking config; routing is a first-class delivery decision.

**Proposal.** The enqueue side selects `routing_key_ref` from a versioned service→ref map (config, not code), defaulting to the current constant. The forwarder already pins refs per row, so the machinery generalizes unchanged.

**Verifies by:** two services, two keys, FakePD with key-scoped dedup (§3.4) — events land in the correct vendor-side buckets, retries never cross keys. Falsifier: a row whose ref cannot be traced to the map. *(Lower priority: product-scope decision for Aditya — build P1–P6 first.)*

### P8 — Formalize the control-plane page API (fixes §2.6)

**Rationale.** Cross-lane private-method calls are handshake governance; the control-plane page path is too critical (it carries dead-letter and drill-breach alarms) for naming-choice coupling.

**Proposal.** Promote `_enqueue_control_plane_page` to a public, typed `EventLog` API with an interface-pinning test; the forwarder's loud-failure becomes a contract-version check.

**Verifies by:** renaming the method breaks a compile-time/interface test, not a runtime path. Falsifier: any forwarder call site reaching into a private eventlog member.

---

## Reviewer's verdict (forced probability, `principal-mindset` §7)

- **75%** that P1 (key split-brain) is a real production failure for any operator who follows the documented BYOK path (UI only, no env vars) — grounded in two resolvers and zero bridging code.
- **60%** that the missing `/metrics` signal (P2) is the first thing an enterprise buyer or SRE audit flags — the research names it the single highest-leverage gap, and the metrics dict is demonstrably write-only.
- **40%** that PD's routing-key-scoped dedup (P4) has already bitten or will bite the pinning design across a restart/rotation — untested boundary, documented vendor behavior.

*What was known when:* all findings are grounded in the code read on `program/architecture-revision` (worktree `sentinel-revdom-forwarder`) and the Phase-1 research cited above; no live PD calls were made and no behavior was assumed beyond the cited docs. `resulting ban` applies: if a killed or banked proposal here later proves wrong, judge the evidence available on 2026-10-05, not the outcome.
