# Operating Without Live Vendor APIs — how elite teams do it

**Stream:** Phase-1 Stream 6 (Architecture Revision Program), Sentinel
**Author:** stream-6 research lane · **Date:** 2026-10-05
**Scope:** mechanism-level research with cited sources, not vibes. Every claim below carries a source.

> Aditya's directive, in brief: "mocking it in such perfect ways" is ONE subdomain
> among many. Get the *general pattern* right — mock servers, recorded replays,
> sandboxes, and the discipline that transfers across Stripe/AWS/Twilio-class APIs —
> then apply it to PagerDuty Events API v2, which Sentinel hits with
> bring-your-own-keys (the operator may have NO PagerDuty account at all).

## Prior-art check (no duplication)

- The narrow enterprise-testing lane (opened ~2026-10-05, branch
  `lane/enterprise-testing`, coordinator f3eba173) was **closed before committing
  any artifacts** — verified: its branch has zero commits beyond main. Nothing to
  build on; nothing to duplicate.
- `platform/contracts/mocks/` contains **Jev-behavior fixtures only**
  (calibration.json, decisions.json, etc.) — no PagerDuty mock exists.
- Current Sentinel vendor-mock practice (verified in-repo):
  - `src/sentinel/pd_sender.py` — a real PD client over stdlib `urllib`, with
    `PD_RETRY_TABLE` (202/400/429/5xx/network) verified against PD docs 2026-10-03,
    dedup_key ≤ 255 enforcement, 512 KB size constant, key-injection at send time,
    Retry-After parsing.
  - `tests/test_forwarder.py` — a `CaptureServer` fixture: a real local HTTP server
    that returns canned status codes (e.g. `status=500`) and records requests.
    Mocking happens **at the HTTP boundary**, which is the right layer
    (see §5). It is stateless: no dedup_key lifecycle, no 429 program, no
    routing-key validation.
  - Simulated paging mode short-circuits *before* key resolution and is labeled
    in-band — fine as a product feature, but it is **not** a mock of the vendor:
    it never exercises the wire path.

---

## 1. Mock servers: WireMock, Mountebank, Mockoon

### WireMock (stateful scenarios, request journal, fault injection)

WireMock runs as a standalone HTTP server (or embedded JVM) and matches incoming
requests against **stubs** — request matchers (method, URL pattern, headers,
JSONPath on the body) paired with canned responses. Three capabilities make it
the industry reference for vendor-API virtualization:

1. **Stateful scenarios — finite-state machines, not just stubs.** WireMock
   scenarios model multi-step workflows: e.g. `GET /order/1` first returns
   `PENDING` and transitions the scenario to `ORDER_PROCESSED`; the second call
   returns `COMPLETED`. State can be reset to the initial condition between
   tests via the admin API (`POST /__admin/scenarios/reset`) for deterministic
   CI. Guidance from practitioners: *design scenarios around business workflows,
   not just endpoints; simulate failure states intentionally; keep state
   transitions explicit and documented.*
   Source: https://medium.com/@divyabaskarsai/understanding-stateful-behaviour-in-wiremock-a-qas-guide-to-testing-complex-integrated-systems-e829cf59d507
   (Medium, ~Nov 2025; best-practice section on stateful patterns and scenario
   resets)

2. **Fault injection at the network layer.** Stubs can inject fixed or random
   delays (`fixedDelayMilliseconds`), socket connection resets
   (`CONNECTION_RESET_BY_PEER`), malformed response chunks, and HTTP error
   codes (429/502/504) — the exact vocabulary of vendor outages.
   Source: https://www.skakarh.com/blog/mocking-external-rest-apis-wiremock
   (SDET guide, crawled 2026-10-04)

3. **Request-journal verification.** WireMock keeps an in-memory request journal;
   the verification API (`POST /__admin/requests/count`) asserts that the
   application sent exactly the required requests with the expected payload —
   the mechanism that lets you test *what you sent the vendor*, not just that
   you handled its reply.
   Source: https://www.skakarh.com/blog/mocking-external-rest-apis-wiremock

   Caveats (so we pick with eyes open): WireMock is Java-first (JVM overhead for
   a Python shop), has a real learning curve, and is primarily HTTP-only.
   Source: https://www.saashub.com/compare-mockserver-vs-wiremock
   (comparison summary, crawled Sep 2026)

### Mountebank (protocol imposter, predicates)

Mountebank defines **imposters**: a port plus a list of routes, each route a
predicate (request match) + response. Predicates compose (`and`/`or`,
`equals`, `deepEquals`, `matches`, `exists`), and imposters also cover
TCP/smtp. Typical setup: run `mb`, post the imposter definition to its admin
API, send traffic to the imposter port. It is the go-to for multi-protocol
virtualization and for teams that want JSON-driven, language-agnostic mocks.
Source: https://dev.to/jonishaso/how-to-set-up-backend-api-mocks-in-javascript-14of
(dev.to; predicate-based imposter example)

### Mockoon (GUI → headless CLI in CI)

Mockoon is the prototyping-first option: a desktop GUI where routes, response
bodies, delays and rules are defined visually; the environment exports to a
single `mockoon-env.json` that runs **headlessly in CI** with
`mockoon-cli start --data ./mockoon-env.json --port 3000`. The workflow that
transfers to us: design the mock interactively, then commit the exported file
and run it deterministically in CI — a human-friendly path to the same
discipline.
Source: https://www.skakarh.com/blog/mocking-external-rest-apis-wiremock
(Secret 7: Mockoon CLI for CI)

### What "perfect" mocking actually requires

Synthesizing the above, plus a service-virtualization primer published
2026-09 (dev.to/marxjenes), a faithful vendor mock must cover three layers:

1. **Schema fidelity** — exact request/response shapes, headers, status codes,
   required-field validation (the vendor's own 400s), documented limits (length,
   size). This is where most hand-built mocks stop, and it is not enough.
2. **Behavioral fidelity (stateful)** — the vendor's *state machine*: what does
   the second call with the same idempotency key do? What happens when you
   acknowledge a nonexistent alert? Hand-built mocks that answer
   "whatever the author believed on the day it was written" inherit every
   weakness of the author's mental model; deriving behavior from observed
   traffic (see §2) fixes fidelity and error coverage, including the odd cases
   nobody documented.
3. **Failure-mode fidelity** — throttling (429 with and without Retry-After),
   5xx, network errors, timeouts, malformed bodies, slow responses. This is the
   layer that tests your retry policy, backoff, and degraded-ladder behavior —
   i.e. the code that actually runs at 3 AM.

Two operating rules recur across sources: **mock only at the HTTP boundary**
(never mock what you own; use cassettes/mocks only for third-party services),
and **refresh on a schedule** — recorded or hand-built mocks drift as the
vendor's API evolves; expired/rotten mocks are a documented failure mode
(API Sunset/`Deprecated` headers, year-old stubs with dead fields).

Sources:
- https://gist.github.com/rasha-hantash/80ad4af6e80cf5a01bbbfaeb6c48602c
  ("Testing conventions: real deps, cassettes" — HTTP-boundary rule, cassettes-first,
  re-record triggers; crawled May 2026)
- https://dev.to/marxjenes/testing-against-a-dependency-you-cant-call-when-service-virtualization-is-the-only-option-3h8o
  (2026-09: "build it by hand, or derive it from real behavior"; secret-scrubbing
  of recorded traffic before it becomes a fixture)

**Relevance to Sentinel.** The `CaptureServer` fixture already mocks at the
right layer (HTTP) and verifies request bytes (authorization header absent —
a request-journal-lite assertion). What it lacks maps 1:1 onto the three
layers above: (a) no schema validation (does the mock 400 on `dedup_key` > 255
chars? on a missing `routing_key`? on > 512 KB bodies?), (b) no stateful
behavior (the PD dedup state machine — see §4), (c) no programmable failure
modes (a 429 with a `Retry-After` header; a 202 whose body is *not*
`{"status":"success"}` — the vendor-contract violation Sentinel's classifier
already detects but no test can produce today).

---

## 2. Recorded replays: VCR-style cassettes and GoReplay

### VCR cassettes (record once, replay forever)

Mechanism: the test runs once with a recorder hooked into the HTTP client; the
full request/response exchange is serialized to a file ("cassette",
e.g. `testdata/cassettes/stripe_create_charge.yaml`); the cassette is committed
alongside the test; all later runs replay from disk — no network, fast,
deterministic. Re-recording is triggered by API changes, new scenarios, or a
scheduled expiry (expiring cassettes throw warnings so stale recordings don't
silently rot — year-old stubs with dead fields are a documented failure mode).
Per-language: Python `vcrpy`, Go `go-vcr`, JS `nock` with `.nockBack()`,
Ruby `VCR` + webmock, .NET `Vcr.HttpRecorder` auto-interception.

**When recording beats hand-built mocks:** the canonical case is schema/shape
drift — "if Stripe nests `payment_method` inside a new `payment_details`
object, a hand-written stub still passes; a re-recorded cassette surfaces the
break." Cassettes capture what the dependency *actually did*, including odd
cases nobody documented. Fall back to hand-written stubs only for: error
scenarios (401/429/500/timeouts — hard to trigger on demand), trivial APIs,
and retry/backoff logic where you need precise control.

Sources:
- https://gist.github.com/rasha-hantash/80ad4af6e80cf5a01bbbfaeb6c48602c
  (cassettes-first doctrine, when-to-re-record, hand-stub exceptions)
- https://dev.to/georgopoulosgiannis/level-up-your-integration-tests-in-net-record-replay-relax-2m5c
  ("run the test once, records the actual request/response… next runs replay";
  crawled Sep 2026)
- https://dev.to/arjunrajkumar/fast-er-tests-with-stunt-doubles-and-vcr-recordings-486h
  (Shopify wrapper case: VCR over webmock for fidelity to API changes)
- https://apisyouwonthate.com/blog/testing-api-interactions/
  (expiring recordings + Sunset/Deprecated headers)

### GoReplay (traffic-level shadowing)

Different mechanism, different job. GoReplay captures **live HTTP traffic**
(`gor --input-raw :8080 --output-file=api.gor`) and replays it — against
staging, a canary, or amplified 2x/10x for load testing with real production
traffic. It is not a mock and not a test framework; it answers "does this
build survive *our* mix?" Middleware rewrites tokens/masks PII before anything
is stored. Complementary pairing that recurs: **replay for realism, k6/JMeter
for a known-bad endpoint you want to hammer**.

Sources:
- https://goreplay.org/blog/simplifying-rest-api-testing-with-goreplay/
  (record/replay workflow, what-it-is-and-is-not)
- https://goreplay.org/blog/replay-http-traffic/
  (scripts vs replay comparison table; filter/mask before leaving production)
- https://goreplay.org/blog/load-testing-tools-open-source/
  (replay + k6 coexistence: same pipeline, different jobs)

### Secret-scrubbing rule

Recorded traffic can contain tokens, personal data, and account identifiers
that must not land in a repository. The scrubbing step — tokens mapped to
placeholders, bodies redacted before the cassette is committed — "deserves real
attention." This is a standing constraint, not optional hygiene.
Source: https://dev.to/marxjenes/testing-against-a-dependency-you-cant-call-when-service-virtualization-is-the-only-option-3h8o

**Relevance to Sentinel.** Sentinel's PD calls are outbound events, not inbound
requests, so GoReplay's capture-and-replay does not map directly. VCR cassettes
*do* map — to a niche, valuable one: **provider contract tests** (see §5).
Recording a real PD `202` response body once (`{"status":"success",
"dedup_key":"...","message":"..."}`) gives a regression anchor for the
`vendor_status == "success"` classifier and for field-evolution detection, at
near-zero ongoing cost. The hard constraint: cassettes require a PD account to
record against, and Sentinel's operator may have none (BYOK). So: cassettes
are a *build-time* artifact maintained by Sentinel's own team (who hold a test
key), never by the operator. And note the documented exception that fits us
exactly — **hand-built stubs remain the right tool for error scenarios and
retry/backoff**, which is precisely Sentinel's most critical wire logic.

---

## 3. Sandbox environments in CI — and what teams do when none exists

### How sandboxes get used (Stripe discipline)

The Stripe discipline is the reference: every account has a test mode
(`sk_test_*` keys) that mirrors the live API without moving money; tests run
against the sandbox with secrets injected from CI secrets at job runtime (never
persisted); suites explicitly refuse live keys; a **provider contract test** —
a small, labeled, deliberate suite that hits the *real* sandbox — is kept
separate from the mocked unit suite, proving the integration against live shape
while everything else stays fast and deterministic. One practitioner doc puts
the boundary exactly right: the sandbox test "is a provider contract test, not
proof that the company website integration is complete" — it tests the vendor
boundary, not the product. Stripe even lowered the sandbox barrier: `stripe
sandbox create` via the CLI generates test API keys with no registration.

Sources:
- https://github.com/mikker/dotfiles/blob/HEAD/agents.symlink/skills/stripe-best-practices/SKILL.md
  (`stripe sandbox create`, restricted keys `rk_` over `sk_`)
- https://github.com/insightitsgit/prismagenticpay/blob/HEAD/docs/COMPANY_STRIPE_HANDOFF.md
  (test refuses live keys; sandbox = provider contract test; "if no sandbox
  credentials are available… return that blocker without claiming a successful
  provider test")
- https://github.com/justinv200/onthefly/blob/HEAD/backend/app/services/transactions/stripe/NOTES.md
  (HTTP-boundary mocks in tests covering rate limits + live-key rejection; real
  sandbox runs kept separate)

### What teams do when no sandbox exists

1. **Stateful fake servers as first-class citizens.** `stripe-mock` (Stripe's
   own, from the OpenAPI spec) runs as a Docker service container in GitHub
   Actions (`stripe/stripe-mock`, port 12111) — zero credentials, schema-exact
   shapes, no network. `localstripe` is the stateful variant (created customers
   survive across requests; supports webhooks). The pattern: the *vendor itself*
   ships the mock, generated from the spec, so schema drift is the vendor's
   problem.
   Sources: https://dev.to/wceolin/how-to-use-stripe-mock-with-github-actions-325;
   https://libraries.io/pypi/stripe_mock_server (localstripe description)
2. **Third-party multi-vendor mocks.** `integration-mock` (Node, MIT) answers
   as Slack/Stripe/HubSpot "would", needs no API key, and fails loudly on
   unknown routes instead of returning empty data — the fail-loud property that
   keeps mocks honest.
   Source: https://github.com/ludwiggerdes/integration-mock/blob/HEAD/README.md
3. **Probing + recording discipline.** One drift-detection outfit runs the
   customer's own suite behind a recording proxy with injected credentials in a
   fresh CI job, capturing request/response shapes — and classifies POST
   endpoints as non-idempotent, probing only GET by default unless the vendor's
   test mode is explicitly safe to write to. The principle transfers: **never
   probe a vendor's mutating endpoints casually; test mode is the only place
   writes are legal.**
   Source: https://github.com/nerdev-co/driftlock/blob/HEAD/docs/product-engineering.md
4. **When there is truly nothing:** mock at the HTTP boundary (always), keep
   the vendor contract as checked-in data (schema + retry table), and gate
   every contract claim on re-verification — exactly what Sentinel's
   `PD_RETRY_TABLE` already does with its "verified live 2026-10-03" comment
   and row-for-row test assertion.

**Relevance to Sentinel.** PagerDuty has no Events-API sandbox: the only way to
"really" page is a real service with a real routing key — and Sentinel's
operator may not have one. So Sentinel sits permanently in case 4 above, with
two consequences: (a) the stdlib-only **fake-PD mock server** is not a nice-to-have,
it is the *only* full-fidelity test target the operator will ever have —
it must be good enough to stand in for a vendor that offers no sandbox; (b)
Sentinel's own team (not the operator) should hold one real routing key and run
a small, labeled provider-contract suite against live PD on a schedule — the
recorded 202/400 responses become the cassette anchors in §2 and the drift
detector in §5. This is the missing half of the current story: the mock is
authoritative about behavior, the contract suite is authoritative about
drift, and they are *different jobs*.

---

## 4. The PagerDuty Events API v2 case — exact contract

All values below are from PagerDuty's own developer docs (GitHub
`pagerduty/developer-docs`, verified current: pages last updated 19 days before
this research, i.e. mid-September 2026) and practitioner write-ups. Sentinel's
`pd_sender.py` already encodes most of this; this section is the citable
reference.

### Endpoint and auth

- `POST https://events.pagerduty.com/v2/enqueue` (global) or
  `https://events.eu.pagerduty.com/v2/enqueue` (EU region). Auth is the
  **`routing_key` in the request body** (32-char integration key) — there is no
  auth header on the Events API v2. Content-Type `application/json`.
- Change events exist as a separate type (no notifications); alert events are
  the paging path. Ruleset integration keys (begin with `R`) cannot receive
  change events.
- Success response: `202` with body `{"status": "success", "dedup_key": "...",
  "message": "..."}`.
  Sources: developer docs overview (below); https://github.com/axonops/go-audit/pull/...
  (PR body documenting endpoint/auth/response/rate limits — via search result,
  axonops/go-audit PD output backend)

### Request schema (trigger)

| Field | Required | Notes |
|---|---|---|
| `routing_key` | yes | 32-char integration key |
| `event_action` | yes | `trigger` / `acknowledge` / `resolve` |
| `dedup_key` | no | ≤ **255 chars**. If omitted, PD generates a unique UUID and returns it in the response |
| `payload.summary` | yes | ≤ **1024 chars** |
| `payload.source` | yes | hostname/FQDN preferred |
| `payload.severity` | yes | `critical` / `error` / `warning` / `info` |
| `payload.timestamp` | no | ISO 8601; auto-generated if absent |
| `payload.component` / `.group` / `.class` | no | e.g. `mysql`, `app-stack`, `ping failure` |
| `payload.custom_details` | no | free-form object |
| `images[]` / `links[]` | no | `images[].src` required, HTTPS-only |
| `client`, `client_url` | no | top-level context |

Source: https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/02-Trigger-Events.md
(trigger parameters + images/links context properties)

Acknowledge/resolve are minimal: `{routing_key, dedup_key, event_action}`.

### Dedup semantics (the state machine the mock must implement)

Verbatim contract from the docs:

- Every alert event has a `dedup_key`. If omitted on first trigger, PD generates
  one and returns it in the response.
- Subsequent events with the same `dedup_key` apply to the **open alert**
  matching that key. Once the alert is **resolved**, further events with the
  same key create a **new** alert (for `trigger`) or are **dropped** (for
  `acknowledge`/`resolve`).
- **Only `trigger` creates alerts.** Acknowledge/resolve with no currently open
  alert do nothing — they do not create one.
- Correlation is scoped by **routing key**: subsequent events must arrive via
  the *same* `routing_key` as the original trigger; ack/resolve via a different
  key are **dropped**.
- A trigger **without** a `dedup_key` always creates a new alert (generated key
  is a unique UUID).
- Behavior table: `trigger` → opens new alert or adds a trigger log entry to
  the existing open alert; `acknowledge` → alert enters acknowledged state
  (**no further notifications, even on new triggers** — the responder is
  working it); `resolve` → alert enters resolved state, no further
  notifications; new triggers after a resolve create a new incident.

Source: https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/02-Trigger-Events.md
("Alert De-Duplication" and "Event Action Behavior" sections)

### Response codes and retry logic (PD's own table)

| Code | Meaning | Retry? |
|---|---|---|
| 202 | Accepted — event accepted by PagerDuty | No |
| 400 | Bad Request — check the JSON | No |
| 429 | Too many API calls at a time | Yes — after some time |
| 500 / other 5xx | Internal Server Error | Yes — after some time |
| Network error | communication failure | Yes — after some time |

Source: https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/01-Overview.md
("Response Codes & Retry Logic")

### Limits

- **Rate:** ~**120 calls/minute per integration key**, calculated over a 60-second
  sliding window; dynamically throttled based on traffic behavior; account-level
  limits also apply. **No `Retry-After` header is sent** on 429 — PD's guidance
  is "retry 3 times, 30 seconds apart" / "with a backoff of a few minutes".
  (AIOps accounts: up to 10,000/min on request.)
- **Size:** event payloads limited to **512 KB**.
- Practitioner detail (Cisco integration guide): 202/400/429/5xx/network-error
  handling with the same retry semantics.

Sources:
- https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/05-Rate-Limits.md
  (120/min, 60s window, 429 without Retry-After, retry-3-times/30s guidance,
  512 KB in the overview page)
- https://www.Cisco.com/c/en/us/td/docs/cloud-systems-management/network-automation-and-management/Catalyst-Center-Platform-Documentation/2-3-7/itsm-ig/b-cisco-catalyst-center-itsm-ig-2-3-7/m_appendix_pagerduty.pdf
  (PD response codes for Catalyst Center integration)

### A practitioner-side confirmation

A PD deep-dive (Medium, Apr 2026) restates the operational semantics from the
responder's side: trigger with the same `dedup_key` updates the same open alert
instead of creating a new one; resolve closes the alert (and the incident if it
was the last open alert); dedup_key design is "the foundation of alert
deduplication."
Source: https://medium.com/@code.chandrashekhar/pagerduty-deep-dive-complete-guide-with-real-time-practical-approach-81010d0b579f

**Relevance to Sentinel.** This is the spec sheet for the faithful mock. Every
row of the dedup state machine is a test Sentinel cannot run today: re-trigger
with the same `dedup_key` (dedup, no new alert), ack-then-trigger (suppressed
notifications — does the forwarder's audit record reflect *our* side of that
correctly?), resolve-then-trigger (new alert, not re-open), ack with no open
alert (dropped, still 202?), trigger from a *different* routing key (dropped).
The mock must also reproduce the 429-without-Retry-After behavior (Sentinel
already parses Retry-After defensively — good, the mock proves the fallback
path) and the 202-without-`"status":"success"` contract violation that
`classify()` treats as retryable-plus-loud.

---

## 5. The general pattern: developing against Stripe/AWS/Twilio-class APIs without live accounts

Across the sources, elite teams converge on the same five-part discipline —
independent of vendor:

1. **The vendor contract is data, versioned in the repo.** Schema, retry
   tables, limits, dedup semantics — checked in, with a test that fails when
   the code drifts from the contract. (This is the strongest thing Sentinel
   already does: `PD_RETRY_TABLE` + "test asserts the classifier matches this
   table row-for-row, so a PD docs change surfaces as a failing test.")
2. **Mock at the HTTP boundary, never above it.** Unit tests swap the transport
   (local server, recorded cassette), never the client internals. "Never mock
   what you own; mock only third-party services at the HTTP boundary." Sentinel's
   `CaptureServer` is already at this layer.
3. **Hand-built mocks for behavior and failure; cassettes for shape.**
   Programmatic failure modes (429, 500, timeouts, contract violations) get
   hand-written stubs because you need precise control; response *shapes* get
   cassettes because hand-written stubs hide vendor drift. The split is explicit
   and documented, not accidental.
4. **A small, labeled provider-contract suite against the real vendor, run on a
   schedule.** Sandbox keys in CI secrets; read-only probes by default; mutating
   endpoints only where test mode makes them safe; and the suite is named for
   what it is — a boundary test, not product proof. Recorded responses from this
   suite become the cassette anchors and the drift detector.
5. **Fail-loud mocks and expiring fixtures.** Unknown routes error instead of
   returning empty data (integration-mock); recordings expire or warn so a
   year-old cassette can't silently certify against a dead API (VCR expiry +
   Sunset headers). A mock that always passes is a mock that lies.

The through-line (principal-governance language): **the contract is the
coordination mechanism.** The mock matches the contract exactly; the client is
written against the contract; drift is detected mechanically, not discovered
at 3 AM.

**Relevance to Sentinel.** Items 1 and 2 are substantially in place. Items 3–5
are the gaps (see next section).

---

## 6. Faithful-mock checklist (schema / behavior / failure modes)

A PagerDuty Events API v2 mock that deserves to be called faithful:

### Schema layer
- [ ] Accepts exactly the trigger/acknowledge/resolve shapes (§4 table); rejects
      unknown `event_action` with 400
- [ ] Enforces `dedup_key` ≤ 255 chars (400 when exceeded)
- [ ] Enforces `payload.summary` ≤ 1024 chars
- [ ] Enforces 512 KB total payload limit
- [ ] Requires `routing_key` (400 when missing/invalid); distinguishes
      *unknown* key (400) from *different* key (dedup scoping — see behavior)
- [ ] Success body is byte-shape-faithful: `{"status":"success",
      "dedup_key":"...","message":"..."}`; echoes or generates dedup_key per §4
- [ ] EU/global endpoint switching (two base URLs, same behavior)

### Behavior layer (stateful — the WireMock-scenario analog)
- [ ] Trigger without dedup_key → new alert, generated UUID returned
- [ ] Re-trigger same dedup_key while open → single alert, trigger log entry
      appended (no new alert)
- [ ] Acknowledge on open alert → acknowledged; further triggers suppressed
      (no notifications)
- [ ] Acknowledge with no open alert → dropped, still 202
- [ ] Resolve → alert resolved; later trigger with same key → NEW alert
      (never re-opens)
- [ ] Events via a different routing_key than the original trigger → dropped
- [ ] Invalid routing key → 400 (never 401 — Events API v2 has no auth header)
- [ ] Reset-to-initial-state endpoint for deterministic test runs
      (WireMock's `/__admin/scenarios/reset` pattern)

### Failure-mode layer (the 3-AM vocabulary)
- [ ] 429 program: programmable throttle (~120/min/key semantics or scripted),
      **no Retry-After header** (matches PD), exercising Sentinel's backoff
- [ ] 202 with body missing `"status":"success"` (vendor contract violation —
      Sentinel treats as retryable + loud; no test covers this today)
- [ ] 400 for malformed JSON / missing required fields
- [ ] 5xx series (500/502/503/504) per the retry table
- [ ] Connection reset / timeout / malformed response chunk (WireMock fault
      vocabulary) — for the degraded ladder in design §6
- [ ] Programmable latency (fixed + jitter) — for timeout tuning
- [ ] Request journal: every received event recorded and queryable, so tests can
      assert exact wire bytes (routing key injected, retry annotations on
      attempt > 1, no Authorization header) — `CaptureServer.requests` is the
      seed of this

### Hygiene layer
- [ ] stdlib-only implementation (frozen constraint) — `http.server` +
      threads, like the existing `CaptureServer`; no WireMock JVM, no new dep
- [ ] Routing keys are test fixtures, never real; any accidental real key in a
      cassette/fixture is a security incident (scrub before commit — §2 rule)
- [ ] Cassettes (recorded real responses) live next to the mock, versioned,
      with expiry/refresh policy and owner — per §2

---

## 7. Relevance-to-Sentinel gaps (what to build, in priority order)

1. **No stateful PD mock exists; `CaptureServer` is stateless.** The dedup
   state machine in §4 is the core of Sentinel's paging semantics (page /
   suppress / re-page), and today *zero* tests exercise it. This is the single
   biggest vendor-mocking gap. A stdlib `http.server`-based fake-PD with an
   in-memory alert store (keyed by `(routing_key, dedup_key)`) implementing the
   behavior layer of §6 closes it. Priority 1.
2. **No programmable failure modes.** `CaptureServer(status=500)` covers one
   row of the retry table. Missing: 429 without Retry-After, 202-without-success,
   malformed JSON 400, dedup_key-too-long 400, unknown routing key 400,
   connection reset, scripted latency. These are Sentinel's most
   safety-critical paths (retry policy, backoff, degraded ladder).
3. **No provider-contract suite against live PD.** Nobody re-verifies
   `PD_RETRY_TABLE` against the real API; the "verified live 2026-10-03"
   comment has no mechanical refresh. A small scheduled suite (Sentinel team's
   own test key, read-only-ish probes + one trigger/resolve cycle against a
   sacrificial service) with recorded cassettes as drift anchors is the
   industry-standard answer (§3, §5.4).
4. **Simulated paging mode is not a mock and should stop being described as
   one.** It short-circuits before key resolution and never touches the wire
   path — valuable as a UX feature, useless as vendor verification. The staging
   story should be: simulated mode for demos, **fake-PD mock for staging**,
   provider-contract suite for drift. Three different jobs, three different
   tools.
5. **Key-hygiene asymmetry.** `pd_sender.py` correctly keeps secrets off disk
   and out of logs — but any future recorded cassettes or request-journal dumps
   will contain *fixture* routing keys, which must be scrubbed/labeled as fake
   before commit (§2 rule). Worth one explicit policy line now, before the
   first cassette exists.

---

## Source index (all claims above cite one of these)

| # | Source | Accessed |
|---|---|---|
| S1 | WireMock stateful scenarios & QA best practices — medium.com/@divyabaskarsai/…-e829cf59d507 | 2026-10-05 (article ~Nov 2025) |
| S2 | WireMock/Mockoon secrets incl. fault injection, request journal, Mockoon CLI — skakarh.com/blog/mocking-external-rest-apis-wiremock | 2026-10-05 (crawled 2026-10-04) |
| S3 | WireMock vs MockServer comparison — saashub.com/compare-mockserver-vs-wiremock | 2026-10-05 (crawled Sep 2026) |
| S4 | Mountebank imposters/predicates — dev.to/jonishaso/how-to-set-up-backend-api-mocks-in-javascript-14of | 2026-10-05 |
| S5 | Testing conventions: cassettes-first, HTTP-boundary rule — gist.github.com/rasha-hantash/80ad4af6e80cf5a01bbbfaeb6c48602c | 2026-10-05 (crawled May 2026) |
| S6 | VCR/HttpRecorder record-replay — dev.to/georgopoulosgiannis/…-2m5c | 2026-10-05 (crawled Sep 2026) |
| S7 | VCR vs webmock (Shopify) — dev.to/arjunrajkumar/fast-er-tests-with-stunt-doubles-and-vcr-recordings-486h | 2026-10-05 |
| S8 | Record/replay, expiring cassettes, Sunset headers — apisyouwonthate.com/blog/testing-api-interactions/ | 2026-10-05 (crawled 2026-10-03) |
| S9 | Service virtualization: hand-built vs derived, secret scrubbing — dev.to/marxjenes/testing-against-a-dependency-you-cant-call-…-3h8o | 2026-10-05 (2026-09) |
| S10 | GoReplay record/replay — goreplay.org/blog/simplifying-rest-api-testing-with-goreplay/ | 2026-10-05 |
| S11 | GoReplay traffic replay vs scripts — goreplay.org/blog/replay-http-traffic/ | 2026-10-05 |
| S12 | Replay + k6 coexistence — goreplay.org/blog/load-testing-tools-open-source/ | 2026-10-05 |
| S13 | Stripe sandbox via CLI, restricted keys — github.com/mikker/dotfiles …/stripe-best-practices/SKILL.md | 2026-10-05 |
| S14 | Sandbox test discipline, live-key refusal — github.com/insightitsgit/prismagenticpay …/COMPANY_STRIPE_HANDOFF.md | 2026-10-05 |
| S15 | stripe-mock in GitHub Actions — dev.to/wceolin/how-to-use-stripe-mock-with-github-actions-325 | 2026-10-05 |
| S16 | localstripe stateful fake Stripe — libraries.io/pypi/stripe_mock_server | 2026-10-05 |
| S17 | integration-mock fail-loud multi-vendor — github.com/ludwiggerdes/integration-mock | 2026-10-05 |
| S18 | Sandbox probing policy (read-only default) — github.com/nerdev-co/driftlock …/product-engineering.md | 2026-10-05 |
| S19 | PD Events API v2 trigger schema + dedup semantics — github.com/pagerduty/developer-docs …/docs/events-API-v2/02-Trigger-Events.md | 2026-10-05 (page updated Sep 2026) |
| S20 | PD Events API v2 overview: response codes/retry, PD-CEF — github.com/pagerduty/developer-docs …/docs/events-API-v2/01-Overview.md | 2026-10-05 (page updated Sep 2026) |
| S21 | PD Events API rate limits (~120/min/key, 429 w/o Retry-After) — github.com/pagerduty/developer-docs …/docs/events-API-v2/05-Rate-Limits.md | 2026-10-05 (page updated Sep 2026) |
| S22 | PD response codes, practitioner table — cisco.com Catalyst Center ITSM integration guide (PDF) | 2026-10-05 |
| S23 | PD dedup practitioner deep-dive — medium.com/@code.chandrashekhar/…-81010d0b579f | 2026-10-05 (Apr 2026) |
| S24 | PD Events API v2 integration example (endpoint/auth/202 body) — github.com/axonops/go-audit PD output backend PR (via web search) | 2026-10-05 |
| S25 | E2E mock discipline (mock ≠ testing the vendor) — contextqa.com/blog/mocking-third-party-apis-e2e-testing/ | 2026-10-05 |
| S26 | Sentinel in-repo: `src/sentinel/pd_sender.py` (retry table, key hygiene, classifier) | 2026-10-05 |
| S27 | Sentinel in-repo: `tests/test_forwarder.py` `CaptureServer` fixture (HTTP-boundary mocking) | 2026-10-05 |
