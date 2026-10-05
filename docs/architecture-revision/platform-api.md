# Platform API — Phase-2 Domain Review

**Reviewer:** Phase-2 domain reviewer (fresh eyes; did not build this surface)
**Date:** 2026-10-05 IST · **Branch:** `program/architecture-revision-dom-api`
**Code read:** `platform/server/app.py` (674 lines, end-to-end), `platform/server/store.py`,
`platform/server/integrations.py`, `platform/server/shed.py`, `platform/server/__main__.py`,
`platform/server/tests/` (all 5 test files), `platform/contracts/openapi.yaml` (full),
`platform/contracts/README.md`, `platform/contracts/mocks/`, `platform/ui/assets/api.js`,
`platform/ui/assets/chain.js`, `platform/ui/tests/conformance.py`
**Research applied:** `research/architecture-patterns.md` (§3–§4: layering, BFF,
versioning/deprecation), `research/swe-discipline.md` (API governance, design-doc gates),
`research/testing-at-scale.md` (contract testing, consumer-driven contracts)
**Skills loaded:** principal-systems, principal-governance, principal-mindset,
execution-doctrine

Method: every claim below is grounded in a file + line-range in the code read above.
"It's fine" findings are not stated without the evidence that would falsify them.

---

## 1. WHAT EXISTS

### 1.1 Endpoint inventory (from `PlatformApp._api`, app.py:176–197)

Read surface — **all unversioned** under `/api/`:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/decisions` | filters: `limit` 1–500 (default 50), `since_id`, `fingerprint` (16-hex), `team`, `action`, `from`/`to` (ISO-8601). Cursor pagination via `meta.pagination.next_since_id` |
| GET | `/api/decision/<id>` | integer path param; full DecisionDetail incl. alert context + outcome join |
| GET | `/api/calibration[?team]` | reliability bins, ECE, coverage@τ; response hardcodes `data_source: "synthetic"` |
| POST | `/api/simulate` | pure recompute over pinned `dataset_version`; deterministic; full provenance block |
| GET | `/api/analytics/noise[?window]` | `window` = `\d+[smhd]`, default `24h` |
| GET | `/api/analytics/flips[?window]` | default `7d`; flip-audit records |
| GET | `/api/stream` | SSE (see §1.5) |

Write surface — **all versioned** under `/api/v1/integrations/` (app.py:183–197):

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/integrations/status` | configured/last4 per key — values never returned |
| POST | `/api/v1/integrations/keys` | saves `pagerduty_routing_key` and/or `jev_api_key`; validates-before-persist (atomic) |
| DELETE | `/api/v1/integrations/keys/<name>` | deletes one key |
| POST | `/api/v1/integrations/simulated` | `{enabled: bool}` toggle |
| POST | `/api/v1/integrations/test-page` | sends a **real** PagerDuty Events API v2 trigger (or absorbs it in simulated mode) |

Plus: static UI served from `/` when `ui_dir` exists (SPA fallback to `index.html`,
`Cache-Control: no-store`); OPTIONS preflight on `/api/*`.

### 1.2 Versioning state: mixed, and the worse half of "mixed"

- Reads and SSE live at **unversioned** `/api/*`. The write surface lives at
  **`/api/v1/*`**. The Phase-1 research names this exact shape and condemns it:
  *"Mixed versioning on one surface is worse than either convention — consumers
  cannot tell which paths are stable."* (`research/architecture-patterns.md` §4.3).
- `meta.contract_version: "1.0.0"` rides every response (app.py:34, `_meta`).
  `platform/contracts/README.md` documents a semver rule (1.x additive,
  2.0.0 breaking, migration note required). This is the **only** versioning
  mechanism — there is no `Accept`-version negotiation, no version in the read
  path, no `Deprecation` (RFC 9745) / `Sunset` (RFC 8594) headers, no published
  retirement timeline.
- `platform/contracts/openapi.yaml` v1.0.0 documents **only the read endpoints**.
  `grep -c integrations` on it returns **0** — the entire write surface is
  absent from the contract. The mocks in `platform/contracts/mocks/` likewise
  cover reads only.

### 1.3 The "implements exactly" claim is false in at least one place

app.py:3–14 docstring: *"Implements platform/contracts/openapi.yaml v1.0.0
exactly."* Counter-evidence, verified in code:

- `ACTIONS` (app.py:38–39) accepts **`folded`**, but the contract's
  `DispositionAction` enum (openapi.yaml) is exactly
  `[page_now, page_business_hours, suppress, passthrough]` — no `folded`.
  `GET /api/decisions?action=folded` returns 200 (passes `_BadParam` validation),
  while the published contract says it must be a 400. The code's own comment
  flags it ("D3 … flagged for awareness") — awareness, not conformance.
- Worse, it is **inconsistent internally**: the filter reads the raw engine
  field (`json_extract(body, '$.disposition')`, store.py:326–328) while
  `project()` *clamps* display disposition to the 4-option `DISP_OPTIONS`
  (store.py:46, 240–241). So `?action=folded` returns rows whose displayed
  `disposition` reads `"passthrough"`. Filter and display disagree.
- Nobody can catch this class of drift mechanically: there is **no test that
  validates responses against openapi.yaml** (the Phase-1 testing stream lists
  exactly this as missing: *"no OpenAPI snapshot test for the read-only API"*).

### 1.4 Auth story: there isn't one

- **Zero authentication and zero authorization on every endpoint**, including
  the secret-handling write surface. Anyone who can reach the port can read key
  status, overwrite or delete the PagerDuty routing key and the Jev API key,
  toggle simulated paging, and fire test pages to PagerDuty.
- CORS defaults to `*` (app.py:86–97; allowlist via `--cors-origins`, `""`
  disables). The preflight advertises `Authorization` in
  `Access-Control-Allow-Headers` — but **no code anywhere reads an
  Authorization header**. `grep` for bearer/token auth in `platform/server/`
  returns nothing.
- Key-source precedence is documented and honest (per-request `routing_key` →
  stored → `PD_ROUTING_KEY` env; `_int_test_page`, app.py:447–477). The
  failure paths are honest too (never echo the key; `501 persistence_unavailable`
  instead of pretending). The honesty is good; the **access control is absent**.
- Threat-model consequence: the BFF research (`architecture-patterns.md` §4.2)
  says the BFF exists to *"hold the secrets the browser must never see"*.
  Sentinel's platform tier does hold the secrets — with no gate in front of
  the hold.

### 1.5 SSE vs snapshot

- `GET /api/stream` (app.py:517–566): `retry: 3000`, resume via standard
  `Last-Event-ID` or `?since_id=`, one `event: gap` with
  `{resume_since_id, missed}` when the cursor predates retention
  (`MAX_REPLAY`), `: heartbeat` every 25s, then a 1s-poll live loop.
  Well-designed protocol; the gap/backfill contract is genuinely good
  (client backfills via `GET /api/decisions?since_id=`).
- **But the stream bypasses both protection mechanisms:**
  1. `__call__` calls `_stream`, which returns the **unconsumed generator**;
     the `finally: self.gate.release()` (app.py:171) runs when `__call__`
     returns — i.e., **before a single event is sent**. The admission slot is
     held for microseconds, not for the connection lifetime. Unlimited
     concurrent streams.
  2. `/api/stream` is **not in `EXPENSIVE_PATHS`** (shed.py:37–42), so load
     shedding never touches it.
  Each stream polls SQLite (`store.tail`, limit 500) **every second, forever**.
  A fleet of open browser tabs during an incident — exactly when the console
  is most used — is an unbounded query pump with no backpressure, no
  per-client cap, no auth.
- There is **no snapshot endpoint**: initial UI hydration is paged
  `/api/decisions` + open stream. No `ETag`/`Last-Modified`/`304` on any
  endpoint, though decision rows are immutable audit records — the ideal cache
  candidate.

### 1.6 Error shapes and envelopes

- Uniform envelope, consistently applied: `{data, meta{contract_version,
  data_source, generated_at}, error{code, message, retryable}}` (`_ok`/`_error`,
  app.py:629–660). Errors: 400 (bad params, machine codes like `bad_window`),
  404, 422 (simulate/integrations validation), 501 (`persistence_unavailable`),
  503 (`shed_admission`/`shed_hot`, `retryable: true`, `Retry-After`).
  This is genuinely good API craft — the error codes are machine-readable and
  `retryable` tells the client what to do.
- `data_source ∈ {synthetic, shadow, production}` on every response is an
  honest-labeling mechanism (contracts README §3). `_calibration` and
  `_simulate` hardcode `"synthetic"` — correct today, a landmine if labels ever
  go real without touching these two callsites.

### 1.7 Read-only contract and what the UI is allowed to assume

- Read-only is **enforced, not asserted**: SQLite opened `mode=ro`
  (test_guards.py `TestReadOnlyDb`), grep-guard tests forbid Jev-client and
  paging write-path imports (`TestNoJevClientImport`), and the one deliberate
  exception — `_int_test_page` firing a real page — is explicit and labeled.
  `POST /api/simulate` is side-effect-free (pure recompute).
- DB-schema leakage is **partial**: `project()` translates rows into contract
  shapes (good), but `store.decisions()` filters on engine-internal JSON paths
  (`$.disposition`, `$.q2_team`, store.py:324–328) — rename a key in the engine
  and the platform filter silently returns empty. Pagination cursors are raw
  SQLite rowids (`id`), coupling the public cursor to physical DB identity.
- What the UI assumes (measured, not guessed): `platform/ui` hardcodes
  unversioned paths (`/api/decisions` ×8, `/api/simulate` ×7, …). It already
  works around contract gaps client-side: `api.js:203` notes *"There is no
  /api/analytics/shadow in the frozen contract"*; `chain.js:4` notes no
  `GET /api/audit/chain`; `conformance.py` asserts these absences. The UI is
  the de-facto consumer, and its expectations live in its own test file — not
  in any shared contract the server verifies.

### 1.8 What is genuinely good (Chesterton's fence — do not "fix" these)

- The envelope + machine error codes + `retryable` + `Retry-After`.
- The SSE gap protocol (`event: gap` + backfill instruction).
- Honest `data_source` labeling as a structural field.
- Atomic key validation before persist; write-only key values with last4
  on read; honest 501s instead of pretend-persistence.
- Admission gate (fail-fast, no unbounded queue) + expensive-path shedding
  for the endpoints that have it.

---

## 2. WHAT'S MISSING

Measured against the Phase-1 enterprise standard. Each item cites the
research that defines the bar.

### M1. No versioning policy — only a version *string* (CRITICAL)

`research/architecture-patterns.md` §4.1 records the Google/Azure/Stripe
consensus: *"version only when you can't make the change backward-compatible"*
— additive inside a version, new version only for true breaks — and
*"Deprecation is a protocol, not a blog post"* (`Deprecation` RFC 9745 starts
the clock; `Sunset` RFC 8594 names the end). Sentinel has the semver string in
`meta` and a README paragraph, but: no version in the read path, no
deprecation headers, no sunset dates, no additive-change CI enforcement, no
transformation layer for old clients. The README's "Additive-only until
Sunday" rule is prose — the `folded` drift (§1.3) proves prose doesn't bind.
The next breaking read change has **no migration path at all**: the GitHub
Pages prod console (cross-origin, independently deployed) would simply break.

### M2. The write surface has no contract at all (CRITICAL)

principal-governance: *"internal and external interfaces are contracts…
version explicitly, deprecate on a published timeline"*; *"APIs as legal
contracts."* The five `/api/v1/integrations/*` endpoints — the only surface
that mutates secrets and fires real pages — are absent from openapi.yaml (0
mentions), absent from mocks, and untested for shape. A contract that doesn't
cover the write surface is a contract over the safe half.

### M3. No contract enforcement — drift is undetectable (HIGH)

`research/testing-at-scale.md` (per-layer checklist): *"no OpenAPI snapshot
test for the read-only API ❌ MISSING"*; contract/schema layer *"every event
validated against JSON Schema"* — absent here too. The `folded` enum drift is
the exhibit: the docstring claims exact implementation while code and contract
disagree, and no test can see it. principal-governance's governance checklist
asks *"API versioning with side-by-side support and a time-bound deprecation
roadmap?"* — the answer here is no on both halves.

### M4. No consumer-driven contracts (HIGH)

`research/testing-at-scale.md` Q2 (Pact flow): the *consumer* owns expectations;
the provider proves it meets them; `can-i-deploy` turns "will this break the
other team" into a CI check. Sentinel's consumer is Prism (`platform/ui`), but
the contract is provider-written (Forge's lane), and the UI's expectations
live in `conformance.py` — which the server never runs. "Design never waits
for backend… mocks match it exactly" (principal-governance §3): the mocks/
directory exists, but nothing binds server output to them.

### M5. No authN/authZ, no rate limit per client, CORS `*` by default (CRITICAL)

The governance checklist's security section and the BFF research (§4.2:
secrets held server-side, browser never sees them) both assume an access
boundary. Sentinel has the secret-holding with no boundary. Additionally:
no per-client rate limiting (only a global 32-inflight admission gate that SSE
bypasses — §1.5), and `test-page` has no idempotency — every POST mints a fresh
`dedup_key` (`os.urandom(8).hex()`, app.py:480), so a retried request
**double-pages PagerDuty**. principal-governance: *"Idempotency by default:
every critical write takes an idempotency key."* Violated on the one write
with a real-world side effect.

### M6. No request identity / unified telemetry (MEDIUM)

principal-governance §4: *"one Trace-ID per user interaction, injected from the
browser through gateway, microservices, and database."* `research/architecture-
patterns.md` checklist #8: *"One trace/correlation ID across every hop… with
per-stage latency attribution"* — scored **missing** for Sentinel. The platform
API accepts no `X-Request-ID`, emits none, logs none. A slow console spinner
is not diagnosable to a slow query.

### M7. Pagination and caching coupled to internals (MEDIUM)

Cursors are raw SQLite rowids (zero data-model leakage — principal-governance —
requires the backend be *"rewritable without breaking upstream"*; a DB
vacuum/rebuild that renumbers rows breaks every outstanding cursor). No
`ETag`/`304` despite immutable rows. Filters reach into engine JSON paths
(`$.q2_team`, `$.disposition`) — the platform tier's query semantics depend on
the engine's internal document shape (SSOT/anti-corruption violation at the
query layer).

### M8. SSE has no operational contract (MEDIUM)

Beyond §1.5's bypass: no documented max-connections policy, no backpressure
signal to the client, no auth, no `Last-Event-ID` validation (non-integer is
silently treated as 0 — arguably Postel-correct, but undocumented), and the
stream is invisible to the shed policy. The 25s heartbeat and gap protocol are
good; the capacity story is absent.

### M9. Error taxonomy is ad-hoc (LOW)

Error codes are per-callsite strings (`bad_window`, `shed_hot`,
`persistence_unavailable`…); no documented catalog; `501` is used for
"ephemeral persistence unavailable," which is a deployment state, not "not
implemented." Fine internally, but a third-party consumer (the stated
pro-user direction) cannot program against an uncatalogued code space.

---

## 3. CONCRETE REVISION PROPOSALS

Type labels per principal-systems (Type 1 = irreversible public-contract
decision, RFC-grade; Type 2 = reversible). Ranked by value = risk removed ×
blast radius, heaviest first. Each states what would verify it — no proposal
is done until its verification exists.

### P0-1. Authenticate the write surface — bearer token, localhost-default deny

**Rationale.** §1.4: the secret-handling surface (key save/delete, simulated
toggle, real test pages) is reachable by anyone with network access. This is
the single highest-severity finding: it is a Type 1 trust decision (who may
hold the PagerDuty keys) currently answered as "everyone." The BFF research
says the tier exists to hold secrets the browser must never see — a hold with
no lock is not a hold. principal-systems eternal friction: the network is
hostile; "hope the port isn't scanned" is not a strategy.
**Proposal.** Require `Authorization: Bearer <token>` on all five
`/api/v1/integrations/*` endpoints; token from `SENTINEL_PLATFORM_TOKEN` env
(constant-time compare); reads keep working without it. Additionally default
`--cors-origins` to the console origin instead of `*` in the self-host guide,
and document the threat model (who is the API's principal?).
**Alternatives considered and rejected:** full OAuth/OIDC — overkill for a
single-operator self-hosted tier (rejected: complexity without a second
principal); mTLS — right for service-to-service, wrong for a browser console
(rejected: UX). Bearer token is the minimal Type-1-adequate mechanism.
**Verify.** Integration test: all five write endpoints return 401 without the
token, 200 with it; wrong token → 401; timing-safe compare covered by unit
test; conformance test asserts `Authorization` is *not* accepted on read
endpoints (no accidental lockout of the public console).

### P0-2. Unify versioning: serve everything under `/api/v1`, keep old read paths as deprecated aliases

**Rationale.** §1.2 + research verdict: mixed versioning is worse than either
convention. The GitHub Pages console is independently deployed — the next
breaking change needs a migration path, and today there is none. This is a
Type 1 decision (public path structure) and must be made once, deliberately.
**Proposal.** Canonical paths become `/api/v1/decisions`, `/api/v1/decision/
<id>`, `/api/v1/calibration`, `/api/v1/simulate`, `/api/v1/analytics/*`,
`/api/v1/stream`; the old unversioned paths keep working but emit
`Deprecation: true` and a `Sunset` date (RFC 9745/8594 — *"deprecation is a
protocol, not a blog post"*). Write the deprecation policy into
`platform/contracts/README.md` (notice period, additive-only rule, who
approves a 2.0.0).
**Alternatives considered and rejected:** Azure-style `?api-version=` query
param (rejected: worse cacheability, uglier for SSE/EventSource); Stripe-style
date-pinned transformation layer (rejected: correct at Stripe's scale,
machinery theater for one self-hosted tier — revisit if a second external
consumer appears).
**Verify.** Conformance test hits every endpoint under both path families;
old paths assert `Deprecation` + `Sunset` headers; a CI check fails any new
endpoint added without a version prefix.

### P1-3. Put the write surface in the contract and enforce the whole contract in CI

**Rationale.** M2 + M3: the contract doesn't cover writes, and nothing
validates reads against the contract — the `folded` drift is the proof that
prose doesn't bind. principal-governance: *"APIs as legal contracts"*;
testing-at-scale: contract layer missing.
**Proposal.** (a) Document all five `/api/v1/integrations/*` endpoints in
openapi.yaml (request/response schemas, error codes, auth requirement from
P0-1). (b) Resolve the `folded` drift explicitly: either add it to the
`DispositionAction` enum with semantics or reject it at the API boundary —
and fix the filter/display inconsistency (filter must use the same clamped
vocabulary the display uses). (c) Add a contract-conformance test that
validates live responses against openapi.yaml (stdlib `jsonschema`-shaped
check or a small validator in tests/): unknown enum values, undeclared
fields, and missing required fields fail the build.
**Verify.** The `folded` case becomes a test: contract and code agree in both
directions. CI runs the conformance suite; the suite currently *fails* on
`folded` (red first, then green).

### P1-4. Give SSE an operational contract: cap, shed-class, backpressure

**Rationale.** §1.5: streams bypass admission control (slot released before
first event) and the shed list; each polls SQLite 1/s forever. Eternal
friction says the incident-time tab fleet *will* happen. This is Type 2
(capacity policy, tunable at runtime).
**Proposal.** Count open streams; cap concurrent streams (env-tunable,
default e.g. 64); beyond cap return 503 `stream_at_capacity` with
`Retry-After` (consistent with the existing 503 vocabulary); classify
`/api/stream` under the expensive-path shed policy so hot-box shedding sheds
streams before reads; document the policy in the contract (`/api/stream`
description).
**Verify.** Load test: 200 concurrent streams against a seeded DB — p95 of
`GET /api/decisions` stays within its C2 baseline; stream #65 gets 503 +
`Retry-After`; existing gap/resume semantics unchanged (the three SSE tests
in test_server.py still pass).

### P1-5. Idempotency-Key on `POST /api/v1/integrations/test-page`

**Rationale.** M5: retried POSTs double-page PagerDuty today (fresh
`dedup_key` per call). principal-governance: idempotency by default on
critical writes. Type 2 (additive header).
**Proposal.** Accept `Idempotency-Key`; store key→result for a TTL (in the
integrations store); same key within TTL replays the recorded result without
re-sending. Document in the contract (P1-3).
**Verify.** Against the mock PagerDuty server (request journal): two POSTs
with the same key → one journaled event; different keys → two events.

### P2-6. Opaque cursors + cache semantics on immutable reads

**Rationale.** M7: rowid cursors couple the public contract to SQLite's
physical identity; immutable audit rows are the ideal ETag candidate
(global optimization — bandwidth and UI snappiness — over local tuning).
**Proposal.** Cursor becomes an opaque token (e.g. base64 of
`v1:<id>:<checksum>`); server rejects foreign/ancient tokens with a
machine-readable `bad_cursor` 400. Add `ETag` (hash of payload) +
`If-None-Match` → 304 on `/api/decisions`, `/api/decision/<id>`,
`/api/calibration`. Keep `since_id` accepted as a deprecated alias during the
P0-2 sunset window.
**Verify.** Cursor survives a DB dump/restore cycle (test rebuilds the DB,
old token still pages correctly); 304 test asserts byte-identical savings.

### P2-7. Request identity: accept/emit `X-Request-ID`, log it

**Rationale.** M6: unified telemetry (principal-governance §4) — one ID per
interaction, browser to DB. Type 2.
**Proposal.** If the client sends `X-Request-ID`, echo it back and include it
in server logs for that request; if absent, mint one and return it. Include
the ID in the 503/422 error envelope's `meta` so a console error is
diagnosable server-side.
**Verify.** Test: request with header → response echoes it and the log line
contains it; slow-query attribution drill (the "spinner to slow query in one
lookup" exercise) done once by hand, recorded.

### P2-8. Catalog the error codes

**Rationale.** M9: third parties (the pro-user direction) program against
error codes; an uncatalogued code space is not a contract.
**Proposal.** `platform/contracts/errors.md`: every `error.code`, its HTTP
status, `retryable` semantics, and client action. Conformance test asserts
every code the server can emit appears in the catalog.
**Verify.** The catalog test fails if a new code is added without a catalog
entry.

### P3-9. Consumer-driven contract: UI-owned expectations, provider-verified in CI

**Rationale.** M4: `conformance.py` is the seed of consumer ownership, but the
server never runs it. testing-at-scale Q2's Pact flow, right-sized: no broker,
just checked-in expectation files.
**Proposal.** Promote `platform/ui/tests/conformance.py` into a shared
`platform/contracts/consumer/` directory: the UI records the request/response
shapes it depends on (flexible matchers, not hardcoded values); the server's
CI replays them against the real app (provider verification). This is the
mechanism that would have caught `folded` from the consumer side.
**Verify.** A deliberately introduced drift (e.g. renaming a field) fails
provider CI before merge; `can-i-deploy` equivalent is a green conformance
job.

---

### Pre-mortem: how this surface fails in one year (top 3)

1. **Unauthenticated key rotation redirects pages.** No auth on
   `/api/v1/integrations/keys` (P0-1 unaddressed) → an attacker on the network
   replaces the PagerDuty routing key; pages go nowhere during a real SEV.
   Mitigation is P0-1.
2. **A breaking read change ships with no migration path.** Unversioned paths
   (P0-2 unaddressed) → the independently-deployed Pages console breaks on a
   field rename; rollback requires redeploying the console, which Aditya does
   by hand from his phone. Mitigation is P0-2 + P1-3.
3. **SSE tab fleet DoSes the platform tier mid-incident.** Unbounded streams
   (P1-4 unaddressed) → during the worst SEV of the year, every responder's
   open console polls SQLite 1/s; the decisions endpoint degrades exactly when
   humans need it. Mitigation is P1-4.

### What this review deliberately does not propose

- No Stripe-style date-pinned transformation layer (machinery theater at this
  scale), no OAuth/OIDC (no second principal exists), no GraphQL/BFF split
  (the accidental-BFF question from the research is real, but renaming the
  tier is not the fix — the contract and auth are).
- No changes to the read-only enforcement, the envelope shape, the SSE gap
  protocol, or the honest `data_source` labeling — verified good (§1.8);
  Chesterton's fence holds.

### Confidence

85% that P0-1 and P0-2 are the correct top two (unauthenticated secret
surface + unmigratable versioning are the two Type-1 defects; everything else
is Type 2). 70% that the `folded` drift generalizes — i.e., that a contract
conformance test will find at least one more code/contract disagreement on
its first red run.
