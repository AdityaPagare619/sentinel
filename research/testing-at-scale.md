# Testing at Scale — Phase-1 Stream 5 Research

**Program:** Sentinel Architecture Revision — Phase 1, Stream 5 (testing-at-scale)
**Date:** 2026-10-05
**Question:** How do elite companies actually practice testing at scale — and what is Sentinel missing per layer?

**What this stream read first:** `research/load-c2-2026-10-04.md` (one-off C2 measurement: 1,000/min sustained held, 10k/min burst 96% served + 4% loud resets, storm-detector threshold saturates at C2 volumes, `gate.emitted` memory leak found by hand) — this doc does NOT duplicate it; it covers what *surrounds* a load measurement. Also consulted the four principal skills (principal-systems, principal-governance, principal-mindset, execution-doctrine) for judgment framing. Every claim below carries a cited source with URL and date; sources are web research from Oct 2026 unless noted.

---

## Q1. Mock vs stub vs fake vs record/replay — when each, and the failure modes of over-mocking

### The mechanism

The five doubles form a precision hierarchy, and the words are not interchangeable. The load-bearing distinction is **stub vs mock**:

- **Dummy** — fills a parameter slot, never used. Use when the signature demands a value the path never touches.
- **Stub** — returns canned answers to calls; no interaction verification. Supports *state verification* (assert on the result). Use when the code under test needs a dependency to supply a specific input. Reach for a stub by default.
- **Spy** — a stub that also records how it was called. Use when you need the input *and* the call is worth asserting (e.g., "did we call the logger with this level?").
- **Mock** — pre-programmed with expectations; *fails if they are not met*. Performs *behavior verification* (assert that a particular call happened). Use only when the interaction itself is the required behavior: an event published, a payment charged, an expensive boundary deliberately not hit. "Over-mocking produces tests that break on every refactor without catching real bugs" — a mock asserting an internal call sequence the user cannot observe breaks on any harmless refactor; state verification survives restructuring.
- **Fake** — a real working implementation with a shortcut (in-memory DB, in-memory queue). Use when the real dependency is too slow or external but its *behavior* matters across calls (state survives across calls). Prefer fakes over mocks for stateful dependencies: "If you have a `PaymentGateway` interface and a `StripePaymentGateway` adapter, your unit tests fake `PaymentGateway`, and you have one integration test against real Stripe that catches upstream breaks."

The rule that recurs across sources: **default to fakes and stubs; reach for mocks only when "did we call X with Y in this order" is the actual requirement.** "Mock only what you don't own" — mock external dependencies, never internal logic.

Sources:
- https://github.com/rundesk-ai/rundesk-team-development/blob/HEAD/skills/testing-code/references/boundaries-and-doubles.md (updated ~Sep 2026): stub-vs-mock table, "stub by default" rule, bad/good examples.
- https://github.com/devjarus/coding-agent/blob/HEAD/skills/practices/test-doubles-strategy/SKILL.md (updated ~Jul 2026): fake-first strategy, over-mocking breaks on refactors.
- https://github.com/hkarpinen/agent-skills/blob/HEAD/testing/references/TEST-DOUBLES.md: definitions and the "Use stubs by default. Use mocks only when the call itself is what you're testing" rule.
- https://dev.to/wittedtech-by-harshit/why-mocking-is-your-testing-superpower-2301 (~May 2025): over-mocking symptoms (tests fail despite correct functionality; excessive `when` statements), "mock only what you don't own" rule.

### Record/replay (VCR cassettes) — a sixth double

VCR.py/Betamax sits between stub and integration test: on first run it calls the real API and records the HTTP request/response to a YAML cassette; afterwards it replays from disk, with zero network. The record modes are *workflow controls*, not convenience flags:

| Mode | Behavior | Good use |
|---|---|---|
| `none` | Replay only; fail on any new HTTP | **CI** — proves the test can run offline; guarantees no network |
| `once` | Record if cassette absent, then replay; fail on unexpected new requests | First recording of a new test |
| `new_episodes` | Replay known, append new unmatched | Expanding a workflow cassette (can hide accidental extra calls) |
| `all` | Always call network, overwrite | Scheduled re-record |

The recommended day-to-day loop: record with `once`, then verify with `none` — "AI coding agents should usually run the second command while editing application code, because it prevents the agent from changing test behavior by repeatedly touching the real API." Re-recording discipline matters: cassettes go stale silently, so teams schedule refresh runs (`all` mode) or review shape changes explicitly. Recorded cassettes must be scrubbed of secrets before committing.

Sources:
- https://codecut.ai/vcrpy-repeatable-public-api-tests-python/ (Sep 2026): `once` semantics, cassette YAML format.
- https://qaskills.sh/blog/vcrpy-pytest-recording-http-replay-guide (Sep 2026): record modes as release controls, the record-then-verify loop, secret filtering.
- https://github.com/thomasmonahan/tidesurgedata/blob/HEAD/docs/dev/recording-cassettes.md (Sep 2026): "Unit tests never touch the network (ADR 0005). Adapter tests replay HTTP responses recorded with pytest-recording" — worked production example of the discipline.

### Failure modes of over-mocking (tests pass, reality fails)

1. **Drift:** "Mocks drift from reality when APIs evolve, DB schemas change, or third parties update. Perfect mocks don't exist. They're a 'happy path' at best. Real users, async flows, and third parties get ignored." (https://www.linkedin.com/posts/alexandre-zajac_your-test-mocks-are-lying-to-you-your-activity-7502025771301883904-9Aox, Jan 2026)
2. **Fidelity gap:** "Complex behaviors of the real world — such as dynamic dependency chains and nuanced API interactions — are often impossible to simulate with sufficient fidelity... Mocks alone can't capture real-world interactions, adapt dynamically as APIs evolve, or surface integration issues early." (https://DEV.to/signadot/why-mocks-fail-real-environment-testing-for-microservices-h3a, ~Mar 2025)
3. **Refactor brittleness:** mocks asserting internal call sequences break on harmless refactors while catching no real bugs — the "double hit" of mock maintenance cost plus debugging integration failures in staging.
4. **WireMock pitfall catalogue** (directly transferable): treating stubs as contracts — "stubs don't bind the real provider. If the real provider changes, your stubs lie quietly"; recording without reviewing (secrets, timestamps leak in); not resetting state between tests. (https://github.com/aks-builds/quality-skills/blob/HEAD/skills/wiremock/SKILL.md, Aug 2026)

### Relevance to Sentinel

- Sentinel's Jev boundary is a hand-written `LatencyStubClient` with a *verified latency model* (C2 report: "the model is verified, not assumed") — good discipline, but it is still a hand-maintained stub against a vendor API that evolves. There is **no VCR cassette or re-record loop**: nothing replays real Jev HTTP traffic offline, and nothing detects when the hand-written stub drifts from the real Jev response shape. stdlib-only blocks `vcrpy` (external dep); the mechanism is reimplementable in-test via a tiny cassette recorder (record once against real Jev, replay with `record_mode="none"` semantics in CI, secrets scrubbed).
- The forwarder → PagerDuty path is the same shape: the closed enterprise-testing lane planned a mock PagerDuty Events API v2 server with programmable behaviors (per MEMORY.md, 5 Oct) — it **never landed** (`lane/enterprise-testing` branch is clean at base commit `f1e9563`; verified 2026-10-05). A stateful mock server (WireMock semantics: stubbing + request journal + fault/delay injection + stateful scenarios) is the correct double here — not bare stubs — because the forwarder's requirement is literally interaction-shaped ("delivered exactly once, payload contains X, retried with backoff"). A WireMock-style stdlib server should record the *request journal* and let tests assert on what was sent, per the WireMock Request Journal pattern (https://www.skakarh.com/blog/mocking-external-rest-apis-wiremock, Oct 2026).
- Judgment (principal-mindset §2, anti-self-deception): every hand-written stub in `tests/` is a hypothesis about reality, not a fact. The audit question for each: *what mechanism re-validates this stub against the real dependency, and on what schedule?* Today the answer is "none."

---

## Q2. Contract testing — Pact and consumer-driven contracts: how teams keep mocks honest

### The mechanism

Contract testing fills the exact hole Q1 names: "unit tests with mocks verify a consumer's logic in isolation but can't catch a real API mismatch with the provider; full e2e tests catch mismatches but are slow, flaky, and need every service actually running. Contract testing verifies the *interface* between services without either service needing the other running." (https://github.com/vidhya101/k8s-ai-operator/blob/HEAD/.claude/skills/contract-testing/SKILL.md, Sep 2026)

The Pact flow, step by step (consumer-driven — the *consumer* owns the expectations):

1. Consumer writes a test against a Pact mock provider, stating "I expect this request → this response" (using flexible matchers — `Like`, `Term`/regex — not hardcoded values).
2. Running the test generates a **Pact file** (JSON): the exact requests the consumer makes and the responses it expects. Only the parts of the API the consumer actually uses are covered — "unused provider behavior can change without breaking tests."
3. The pact is published to a **Pact Broker** (shared registry).
4. The provider's CI fetches the consumer's pact, replays the recorded requests against its **real implementation** (with provider-state handlers seeding test data), and verifies actual responses match.
5. Verification results publish back to the Broker; the `can-i-deploy` gate turns "will this deployment break the other team's service" into an automated CI check instead of a Slack message and hope.

The asymmetry is the point: the consumer owns expectations; the provider proves it still meets them. (Sources: https://www.augmentcode.com/guides/api-contract-testing-agent-authored-specs, Jul 2026; https://github.com/nguyen-mau-anh/springboot-kb/blob/HEAD/07.testing/08-consumer-driven-contracts.md, Jun 2026; https://medium.com/@tcankaya99/pact-contract-testing-in-real-world-java-preventing-the-02-27-am-outage-c505879e0bd8, Jan 2026.)

### When it earns its keep — and the third-party caveat

The honest signal: "are you experiencing 'the other team broke our integration' incidents? CDC pays for itself starting at the first such incident. Without those incidents, the discipline is overhead." It is essential for multi-team microservices, polyglot stacks, event-driven (message) contracts; overkill for a single-team monolith or throwaway prototypes. (springboot-kb source above.)

**Critical caveat for Sentinel:** the second half of the pattern — provider verification — only works if the *provider runs it*. A third-party vendor "will not replay your pact before each release, and you would not want it to gate its releases on your file anyway. So the half of the pattern that catches provider drift is missing." The recommended replacement for third-party boundaries: **you own the provider-verification harness** — run on your schedule against the vendor's production API, comparing today's live response *shapes* against yesterday's (a nightly drift harness that never sends a real message). It costs a dozen free read calls per run and catches "any shape change, including on paths you do not read yet," at the cost of detecting drift after it ships (within one schedule interval) rather than before. (https://www.smsgatewaycenter.com/blog/contract-testing-harness-messaging-api/, Sep 2026.)

### Relevance to Sentinel

Sentinel has **two third-party boundaries and zero contract tests**:

- **Consumer side (owned, cheap):** the forwarder's expectations of PagerDuty Events API v2 and the judge-client's expectations of the Jev API should be captured as versioned contract files (request/response shapes Sentinel depends on). These double as executable documentation that doesn't rot.
- **Provider side (the missing half):** neither PagerDuty nor Jev will verify Sentinel's pacts. Sentinel therefore needs its own **nightly drift harness**: a scheduled job that hits the real Jev and PagerDuty APIs with read-only/sandbox calls, diffs response shapes against the checked-in contracts, and pages (or opens an issue) on shape change. This is the *only* mechanism that catches the exact failure Aditya's FYI-boundary work fears: a silent upstream change that hand-written stubs then encode as truth.
- Pact proper (broker, can-i-deploy) is for *internal* consumer/provider pairs — Sentinel should adopt it if/when the read-only API and UI become separately deployed services; today OpenAPI/schema validation + the drift harness is the right-sized mechanism.

---

## Q3. Load testing practice — k6/Locust/JMeter as actually used

### What "good" looks like: the four tests, each catching a different class of failure

| Test | Shape | What it catches |
|---|---|---|
| **Load** | Ramp to expected peak, hold 15–30 min | Baseline SLO behavior: p95/p99, error rate under normal max |
| **Stress** | Keep adding load until something gives | The level where latency climbs, where errors begin, what failure *looks like* — turns capacity planning into arithmetic |
| **Spike** | Normal → extreme almost instantly | Systems that pass gradual stress tests fail here — autoscaling takes minutes, spikes take seconds |
| **Soak** | Moderate load for hours/days | Slow failures short tests cannot see: **memory leaks, connection pools running dry, logs filling disks, scheduled jobs colliding with traffic at 3 a.m.** |

"How errors appear as load rises matters too: clean 503 responses mean the system is shedding load on purpose, while timeouts and connection resets mean it is drowning." (https://dev.to/paulcrinigan/load-stress-spike-and-soak-four-performance-tests-and-what-each-one-catches-4g6l, Oct 2026). Gatling's pattern catalogue adds: capacity tests (maximum sustainable traffic, hours until failure), breakpoint (at which traffic level do we break), ramp-hold (do SLOs hold at peak). (https://gatling.io/types-load-testing, crawled Oct 2026.)

### What gets measured — and how results gate releases

Measured: response time distributions (p50/p95/p99 — never just averages), throughput, error rate, and resource consumption (CPU/memory). In k6 these map to `Trend` (latency), `Rate` (success ratios), `Counter` (errors), `Gauge` (memory, pool depth). Example production thresholds: `p(99)<1200`, `rate>0.999`. (https://www.skakarh.com/blog/defining-slos-in-k6-thresholds, Oct 2026.)

**The gating practice — this is the part Sentinel lacks:** "Continuous Performance Governance": (1) lightweight ~10-VU smoke SLO tests on **every PR** with non-zero exit code gates before merge; (2) **nightly** multi-stage stress tests evaluating long-term resource endurance (connection leaks); (3) cross-service SLI standardization. k6 thresholds "turn a test into a pass or fail gate for CI" — the mechanism is `thresholds` in the test definition producing a non-zero exit code that blocks the merge. Tool fit: k6 is the modern default for dev teams (tests as code, fast Go binary, CI gates); Locust fits Python shops (each virtual user is a Python class); JMeter has the broadest protocol support but heavy JVM + XML plans awkward in version control; Gatling squeezes the most concurrency per machine. (https://dev.to/paulcrinigan/... Oct 2026; https://www.skakarh.com/blog/defining-slos-in-k6-thresholds, Oct 2026.)

Practical loop, per Gatling's myth-busting guide: "Start with three steps: (1) pick your most critical user flow and write a test for it in the language your team already uses; (2) **add it to CI with percentile-based assertions, so a regression fails the build automatically**; (3) expand deliberately: spike tests before campaigns, soak tests before major releases, stress tests to find real limits." Teams that avoid production failures "don't necessarily run the biggest tests. They run the right tests, often, and act on what they find." (http://dev.to/gatling/12-performance-testing-myths-that-lead-to-production-failures-334m, Sep 2026.)

Two k6 sharp edges worth recording for whoever builds the harness: tag cardinality — unique IDs in metric tags create millions of series and crash k6 (tag only by static dimensions: endpoint, tier, region); and `delayAbortEval` grace periods so warm-up doesn't trip thresholds. (skakarh SLO guide, Oct 2026.)

### Relevance to Sentinel

- The C2 measurement (2026-10-04) is a **measurement, not a harness**: one-off, un-gated, un-repeatable in CI. Nothing fails a PR if p99 regresses or `gate.emitted` leaks faster. The soak test is the conspicuous hole: the C2 report *found by hand* a slow memory leak (`gate.emitted` grows unboundedly) and lock contention in the event log — exactly the class of failure soak tests exist to catch mechanically.
- Sentinel should stand up the three-tier practice: **PR-gated smoke** (small VU count, percentile assertions, non-zero exit code blocks merge); **nightly sustained + spike** against the seeded harness; **weekly soak** (hours, leak/drift detection). The C2 numbers become the first baseline thresholds, not a report in a folder.
- stdlib-only means k6 itself is an external binary — acceptable as a *test runner* (not shipped code), or the harness can stay in Python. Either way, the non-negotiable part is **thresholds-as-gates**, not the tool.

---

## Q4. Chaos engineering — lineage, reality vs hype, and what a non-Kubernetes Python service should take

### The real lineage

- **Early 2000s — Amazon GameDay:** Jesse Robbins, drawing on volunteer-firefighter experience, introduced GameDay at Amazon to simulate major failures and improve resilience. (https://en.wikipedia.org/wiki/Chaos_engineering, crawled Oct 2026; https://dev.to/techielass/what-is-chaos-engineering-254o)
- **2006 — Google DiRT:** Kripa Krishnan built Disaster Recovery Testing at Google; Jason Cahoon (SRE) documented it in the *Chaos Engineering* book. Google still runs large-scale production failure injection routinely. (Wikipedia, above.)
- **2010–2011 — Netflix Chaos Monkey:** randomly terminates instances in production "during usual hours of activity" so teams build redundancy as an obligation, not an option. Evolved: Chaos Kong (region failure, war-room monitored), and in July 2017 **ChAP (Chaos Automation Platform)** — "interrogates the deployment pipeline for a user-specified service, launches experiment and control clusters, routes a small amount of traffic to each" — making chaos a first-class gate in the CI/CD pipeline: "deployments that couldn't survive random instance termination simply didn't ship." A dedicated chaos team formed 2015 under Bruce Wong. (https://www.infoworld.com/article/2257835/what-is-chaos-monkey-chaos-engineering-explained.html, ~2019; https://medium.com/@ghosalarjun/beyond-failure-tracing-the-revolutionary-history-of-chaos-engineering-a703b3c59387, Dec 2025)
- **2017 — The Principles:** a cross-industry working group (Netflix, Gremlin, Google, CNCF) formalized the scientific method: (1) define **steady state** (measurable: e.g., 99.9% HTTP 200, median latency < 200ms); (2) form a **hypothesis** ("if we kill a pod, steady state holds because the LB reroutes"); (3) **introduce variables** reflecting real events (crash, disk failure, severed network); (4) **disprove the hypothesis** by comparing control vs experimental groups. (Infoworld, above; https://conduktor.io/glossary/chaos-engineering-for-streaming-systems, Sep 2026)
- Netflix's cultural guardrail: freedom within boundaries — "do not exceed 5% error rate"; chaos is fun when it happens under your control. (medium history, Dec 2025)

### Reality vs hype

What enterprises actually do: **hypothesis-driven, blast-radius-limited experiments, increasingly as deployment gates** (ChAP blocking deploys; AWS zone-failure GameDays; Google DiRT). What the hype sells: randomly breaking production for its own sake. The discipline's own literature is explicit: "Chaos Engineering isn't anarchy. It follows a rigorous, scientific method... The experiment is a success whether your hypothesis was proven right or wrong." (https://dev.to/satyam_gupta_0d1ff2152dcc/chaos-engineering-in-devops-2025-a-guide-to-building-unbreakable-systems-4abk, Oct 2025). For streaming/pipeline systems specifically, chaos belongs *in the CI/CD pipeline for critical applications, with automated tests that inject failures and verify recovery within acceptable time bounds* — broker death mid-peak, rebalance during backlog — because "failures can cascade quickly through distributed pipelines." (https://conduktor.io/glossary/testing-strategies-for-streaming-applications, Sep 2026.)

### What a non-Kubernetes Python service should take from it

No K8s needed. The transferable machinery:

1. **Steady-state + hypothesis per experiment, written first.** Example hypotheses for Sentinel: "If the PagerDuty endpoint returns 429s for 60s, the forwarder backs off and no page is lost (steady state: 100% page delivery, p99 forward latency < X)"; "If the Jev client times out on 100% of calls for 5 minutes, the deterministic gate's timer-win path holds steady state (page on uncertain, never silent drop)."
2. **A fault-injection proxy in front of dependencies** — the Python-native equivalent of Toxiproxy: an `asyncio` TCP/HTTP proxy (stdlib-buildable) between Sentinel and the Jev client / PagerDuty endpoint applying programmable toxics: latency (+jitter), connection reset, timeout, HTTP 429/500/502/503 overrides, payload corruption. Precedent tooling: Toxiproxy (latency/jitter/timeout/bandwidth toxics via config), `tc netem` (`tc qdisc add dev eth0 root netem delay 100ms 20ms loss 5%`), and faultbox-style Python proxies with pytest plugins. (https://github.com/alexandrmotologa/faultbox, crawled Oct 2026; https://github.com/doanchienthangdev/omgkit/blob/HEAD/docs/skills/chaos-engineering.mdx, Jan 2026)
3. **Application-level chaos hooks** — a `@inject_chaos(failure_rate, latency_ms)` decorator / WSGI middleware for dev/staging only, toggling failure modes on the Jev client and forwarder paths. (omgkit skill, above.)
4. **GameDay ritual** — scheduled, facilitated failure drills with a scorecard (the dannyhmyers chaos-engineering-toolkit ships a FacilitatorGuide + GameDayScorecard; Sentinel's existing fault drills are the seed, but they need hypotheses and pass/fail criteria to be chaos engineering rather than theater). (https://github.com/dannyhmyers/chaos-engineering-toolkit, crawled Oct 2026)
5. **Blast-radius discipline**: start on staging/CI, error-rate budgets (Netflix's 5% rule), andon-style stop authority — anyone halts the experiment if steady state breaks beyond budget.

### Relevance to Sentinel

- Sentinel **has fault drills but no chaos experiments**: drills exist (per task brief) but there is no steady-state definition, no written hypothesis, no fault-injection proxy between the forwarder and PagerDuty or between the gate and the Jev client. The C1 stepped fail-open RFC (test_c1_stepped_failopen.py exists) is *exactly* the kind of behavior that should be proven under injected faults — "what happens when PagerDuty is down for 4 hours" (principal-systems done-checklist) currently has no automated answer.
- Concrete build list: (a) stdlib fault-proxy with latency/reset/timeout/HTTP-error toxics, usable from unittest; (b) a chaos experiment catalogue (Jev timeout storm; PagerDuty 429/5xx; event-log disk-full; correlator thread-starvation) each with steady-state metric + hypothesis + abort threshold; (c) GameDay cadence with scorecard. Start in CI/nightly, never production, until the harness earns trust.

---

## Q5. Test strategy per layer — the pyramid for ingest→decide→act pipelines

### Google's doctrine (the reference implementation)

Google's *Software Engineering at Google* (Ch. 11) reframes the pyramid by **test size — what the test is allowed to touch — not by "unit vs integration"**:

| Size | Allowed | Not allowed |
|---|---|---|
| **Small** | In-process, your code only | Network, DB, filesystem, `sleep()` |
| **Medium** | Same machine; local DB/Redis in containers, localhost | Internet, external APIs |
| **Large** | Anything, incl. internet/external services | — |

Distribution target: **~80% narrow-scoped unit tests, ~15% medium integration, ~5% E2E** (by test count). Small tests are hermetic and deterministic; flakiness lives in external calls, and the machine can *check* whether a test opened a socket. Google's Test Certified program (5 levels; L1 = continuous build + coverage tracking + classify all tests by size + identify flaky tests; L5 = no nondeterministic tests, everything automated) lifted 1,500+ projects; its 2015 successor **Project Health (pH)** auto-scores every project 1–5 on test quality/speed/reliability continuously. (https://github.com/dayuanjiang/software-engineering-at-google/blob/HEAD/en/Chapter-11_Testing_Overview/Chapter-11_Testing_Overview.md, crawled Oct 2026; https://medium.com/startlovingyourself/quality-at-scale-how-google-keeps-bugs-in-check-across-billions-of-lines-7dfdcddff978, ~Jun 2025)

The pytest-native enforcement exists: `pytest-test-categories` (`@pytest.mark.small/medium/large`, blocks network/FS/DB/subprocess/sleep in small tests, enforces time budgets — 1s small / 5min medium / 15min large — and validates the 80/15/5 distribution). "If your test needs network access, it should be marked `@pytest.mark.medium` or larger... Escape hatches become the norm" — hence no escape hatches. (https://github.com/mikelane/pytest-test-categories, Sep 2026; design philosophy: https://github.com/mikelane/pytest-test-categories/blob/HEAD/docs/architecture/design-philosophy.md.)

### The pipeline pyramid (ingest → decide → act)

Data/event pipelines invert the naive pyramid at their peril: "a handful of slow end-to-end checks and nothing underneath, so a broken fare calculation is only discovered after a full pipeline run. Unit tests on pure functions catch that in milliseconds." The worked pipeline pyramid (rideflow example, real project doc):

```
Performance (few, slow) — throughput · latency · backfill
Chaos / Resilience — kill consumer · replay · malformed flood
Integration — end-to-end through real infra
Data quality — schema · business rules · reconciliation
Contract / schema — every event validated against JSON Schema
Unit (many, fast) — pure functions: dedup · state machines · scoring
```

with counts like ~120 unit / ~40 contract / ~90 data-quality / ~15 integration / ~8 chaos / ~5 perf, unit+contract+data-quality PR-gating, chaos/perf nightly non-blocking. (https://github.com/kevin1skyrj/rideflow-data-engineering-pipeline/blob/HEAD/docs/testing_strategy.md, Sep 2026)

Two permanent tiers for integration (nova-project ADR-033): **fake-backed integration** (fast, no Docker, exercises real routing + domain logic end-to-end, PR-gating) AND **real-infrastructure integration** (real Postgres/Redis/bus via testcontainers — exercises real constraints, transaction isolation, driver behavior; opt-in, not PR-gating). "Neither tier retires the other." Enforced minimums: 85% line coverage on the domain/cognitive-logic package, checked in CI — `api/`/repository layers covered by integration tests, not chased for unit coverage. (https://github.com/tudor191/nova-project-bible/blob/HEAD/docs/architecture/16-testing-strategy.md, Sep 2026)

Also standard: contract layer via OpenAPI + event-schema snapshots (`test_openapi.py`, `test_event_schemas.py`); table-driven tests; **banning SQL-mock libraries** ("mocking SQL drivers is banned as it obfuscates real database behavior and leads to fake confidence" — use throwaway real databases). (https://github.com/sanjay10tech/purpllehackathon-smart-ai-store-surveillance/blob/HEAD/docs/architecture/testing-strategy.md, Jun 2026; https://github.com/vctplatform/vct-platform/blob/HEAD/docs/architecture/qa-testing-architecture.md, Apr 2026)

### Distribution models — pick by risk profile, not by default

The classic 70/20/10 pyramid suits backend services with clear domain logic (that's Sentinel). Alternatives exist for different shapes: Testing Trophy (integration-heavy, for UIs/APIs where HTTP+DB correctness dominates), Testing Honeycomb/Spotify (thin services, collaboration is the value), Contract-First (alongside any model when shared APIs are consumed), and Risk-Based (test depth ∝ probability × impact × change frequency). (https://github.com/eagleeyevisionlabz/wayland-m3ta-0s/blob/HEAD/src/process/resources/skills-library/bodies/skills/testing-quality/test-strategy-design/SKILL.md, Jul 2026)

### Flaky-test discipline at scale

Google/Microsoft/Facebook practice: identify (rerun failures to detect inconsistency), **quarantine** (move to a non-blocking suite — not skip-and-forget — with an owner and SLA), fix or delete (delete tests that can't be stabilized and lack value). Prevention: hermetic tests, unique data per test, explicit waits over `sleep`, deterministic clocks/RNG, idempotent cleanup. Retries are a bandage: cap at 1–2, never on the same test for more than a week; new/fixed tests must prove stability (e.g., 50+ consecutive passes) before rejoining the blocking gate. (https://github.com/mejbaurbahar/software-tester-skills/blob/HEAD/skills/flaky-test-management/SKILL.md, Sep 2026; http://dev.to/beefedai/diagnosing-and-fixing-flaky-microservice-tests-4iag, Sep 2026; https://thenewstack.io/?p=15398482.)

### Beyond the pyramid: shadow and canary (testing in production, safely)

When pre-production can't catch it, elite teams test *with* production traffic: **shadow deployments** run the new version in parallel consuming the same events but writing to separate outputs, comparing results before users are affected; **canary deployments** roll out to small traffic percentages with metric monitoring and automated rollback. The mature pipeline: build → evaluate → deploy dark → shadow production traffic → canary 1% → evaluate quality/safety/reliability/economics → expand → monitor → auto-reduce/rollback on degradation. CI integration pattern: build image → deploy shadow (receives no user traffic) → replay captured+masked production traffic for a budgeted window (15–60 min or ~50k requests) → automated analysis → promote to real canary or auto-rollback with artifacts attached to the PR. (https://www.conduktor.io/glossary/testing-strategies-for-streaming-applications, Sep 2026; https://debugg.ai/resources/from-staging-to-shadow-traffic-production-replay-patterns-2025, ~Sep 2025; https://www.linkedin.com/pulse/progressive-rollouts-ai-native-sdlc-core-capability-arup-das-xuhmf, Sep 2026.)

### Relevance to Sentinel — the per-layer checklist

Mapping Sentinel's pipeline (webhook receiver → correlator → deterministic gate racing Jev judge → durable PagerDuty forwarder → hash-chained event log → read-only API + UI):

- [ ] **Unit (small/hermetic, ~80%):** ✅ mostly present (~960 tests; correlator, gate, firewall, tuner). GAPS: no size classification — tests are not marked small/medium/large and nothing enforces hermeticity (a "unit" test that secretly hits the FS/network is undetectable); no enforced time budget per test.
- [ ] **Contract/schema:** ❌ MISSING. No validation of webhook payload shapes against a checked-in schema; no consumer-side contracts for Jev or PagerDuty APIs; no OpenAPI snapshot test for the read-only API. Add: event-schema tests (every receiver input validated against JSON Schema), OpenAPI conformance test.
- [ ] **Data-quality / invariant:** ❌ MISSING. No dbt-style invariant layer: hash-chain continuity checks, dedup-window invariants, "no silent drop" reconciliation (events in = decisions + folds + errors, accounted). The event log's hash chain is *verifiable* — but is it *verified in CI on every build*?
- [ ] **Integration, fake-backed (medium, PR-gating):** ⚠️ PARTIAL. `test_durable_forwarder.py`, `test_firewall_integration.py`, `test_shadow.py` exist — but the fake/mock PagerDuty server never landed, so forwarder tests can't assert request journals against programmable behaviors.
- [ ] **Integration, real-infrastructure (nightly):** ❌ MISSING. No real-PagerDuty sandbox path, no real-Jev verification path (the nightly drift harness from Q2 covers the contract half of this).
- [ ] **Chaos / resilience (nightly):** ❌ MISSING. No fault-injection proxy, no steady-state hypotheses (see Q4).
- [ ] **Performance (PR smoke + nightly + weekly soak):** ❌ MISSING as a gate. C2 exists as a report; nothing gates.
- [ ] **Shadow / canary:** ⚠️ PARTIAL. `test_shadow.py` / `test_shadow_report.py` exist (shadow *mode*), but there is no production-traffic replay loop and no canary promotion machinery — the execution-doctrine pipeline (validate → shadow → canary) is doctrine, not machinery.
- [ ] **Flaky discipline:** ❌ MISSING. No quarantine policy, no rerun-to-detect, no hermeticity enforcement — as the suite grows past 960 tests, this is where trust goes to die.

---

## The three sharpest findings

1. **Sentinel's stubs have no honesty mechanism — and for third-party vendors, Pact's provider half doesn't exist, so Sentinel must build its own nightly drift harness.** The `LatencyStubClient` is a verified latency model but an *unverified shape model*; nothing re-validates it against the real Jev API. The smsgatewaycenter analysis (Sep 2026) is exact: with a third-party provider, the protective half of consumer-driven contracts is missing by construction — the replacement is a self-owned harness that diffs live response shapes against checked-in contracts on a schedule. This is the highest-leverage gap because every other test layer *assumes* the stub is truth.
2. **C2 was a measurement, not a practice — and the soak layer is where Sentinel's known bugs live.** Elite practice gates releases on percentile thresholds (k6 `thresholds` → non-zero exit → PR blocked), runs nightly stress, and soaks weekly; Sentinel has a report. The two defects the C2 report found by hand — the `gate.emitted` memory leak and event-log lock contention — are precisely what soak tests catch mechanically. Evidence that isn't a gate decays: every week the C2 numbers age without thresholds, they become archaeology.
3. **No size classification, no hermeticity enforcement, no flaky quarantine — the suite is one silent dependency away from distrust.** Google's move from "unit vs integration" (unenforceable) to "what may this test touch" (machine-checkable), plus quarantine-not-skip discipline, is the difference between 960 tests as an asset and 960 tests as a liability. At 960 tests and growing, the first network-touching "unit" test or the first quarantinable flake is a matter of time, not luck.

## The single biggest testing gap

**There is no continuous, release-gating verification of the two external boundaries (Jev judge, PagerDuty forwarder) — Sentinel's promise ("page when it matters, suppress when it doesn't") is tested only against hand-maintained stubs that nothing keeps honest, and performance is measured once, never gated.** Concretely: stand up (a) versioned consumer contracts for both vendor APIs, (b) a nightly drift harness diffing live vendor response shapes against those contracts, (c) a stateful programmable mock PagerDuty server (the closed lane's unlanded artifact — rebuild it), and (d) a threshold-gated load harness (PR smoke + nightly + weekly soak) seeded from the C2 baselines. Until (a)–(d) exist, the 960-test suite proves the engine agrees with itself — not that it agrees with reality.

---

## Sources index

All URLs verified from search results on 2026-10-05; dates are page "last updated" as reported:

**Test doubles & over-mocking**
- https://github.com/rundesk-ai/rundesk-team-development/blob/HEAD/skills/testing-code/references/boundaries-and-doubles.md (~Sep 2026)
- https://github.com/devjarus/coding-agent/blob/HEAD/skills/practices/test-doubles-strategy/SKILL.md (~Jul 2026)
- https://github.com/hkarpinen/agent-skills/blob/HEAD/testing/references/TEST-DOUBLES.md
- https://dev.to/wittedtech-by-harshit/why-mocking-is-your-testing-superpower-2301 (~May 2025)
- https://www.linkedin.com/posts/alexandre-zajac_your-test-mocks-are-lying-to-you-your-activity-7502025771301883904-9Aox (Jan 2026)
- https://DEV.to/signadot/why-mocks-fail-real-environment-testing-for-microservices-h3a (~Mar 2025)

**Record/replay & mock servers**
- https://codecut.ai/vcrpy-repeatable-public-api-tests-python/ (Sep 2026)
- https://qaskills.sh/blog/vcrpy-pytest-recording-http-replay-guide (Sep 2026)
- https://github.com/thomasmonahan/tidesurgedata/blob/HEAD/docs/dev/recording-cassettes.md (Sep 2026)
- https://github.com/aks-builds/quality-skills/blob/HEAD/skills/wiremock/SKILL.md (Aug 2026)
- https://www.skakarh.com/blog/mocking-external-rest-apis-wiremock (Oct 2026)
- https://github.com/bmsuseluda/techradar/blob/HEAD/radar/2017-03-01/wiremock.md

**Contract testing**
- https://www.augmentcode.com/guides/api-contract-testing-agent-authored-specs (Jul 2026)
- https://github.com/vidhya101/k8s-ai-operator/blob/HEAD/.claude/skills/contract-testing/SKILL.md (Sep 2026)
- https://github.com/nguyen-mau-anh/springboot-kb/blob/HEAD/07.testing/08-consumer-driven-contracts.md (Jun 2026)
- https://www.smsgatewaycenter.com/blog/contract-testing-harness-messaging-api/ (Sep 2026)
- https://medium.com/@tcankaya99/pact-contract-testing-in-real-world-java-preventing-the-02-27-am-outage-c505879e0bd8 (Jan 2026)

**Load testing**
- https://dev.to/paulcrinigan/load-stress-spike-and-soak-four-performance-tests-and-what-each-one-catches-4g6l (Oct 2026)
- https://gatling.io/types-load-testing (crawled Oct 2026)
- https://www.skakarh.com/blog/defining-slos-in-k6-thresholds (Oct 2026)
- http://dev.to/gatling/12-performance-testing-myths-that-lead-to-production-failures-334m (Sep 2026)
- https://dev.to/keploy/a-comprehensive-guide-to-load-testing-2fmb (~Oct 2024)

**Chaos engineering**
- https://en.wikipedia.org/wiki/Chaos_engineering (crawled Oct 2026)
- https://www.infoworld.com/article/2257835/what-is-chaos-monkey-chaos-engineering-explained.html (~2019)
- https://medium.com/@ghosalarjun/beyond-failure-tracing-the-revolutionary-history-of-chaos-engineering-a703b3c59387 (Dec 2025)
- https://conduktor.io/glossary/chaos-engineering-for-streaming-systems (Sep 2026)
- https://dev.to/satyam_gupta_0d1ff2152dcc/chaos-engineering-in-devops-2025-a-guide-to-building-unbreakable-systems-4abk (Oct 2025)
- https://github.com/alexandrmotologa/faultbox (crawled Oct 2026)
- https://github.com/doanchienthangdev/omgkit/blob/HEAD/docs/skills/chaos-engineering.mdx (Jan 2026)
- https://github.com/dannyhmyers/chaos-engineering-toolkit (crawled Oct 2026)

**Test pyramid / strategy / flakiness / shadow**
- https://github.com/dayuanjiang/software-engineering-at-google/blob/HEAD/en/Chapter-11_Testing_Overview/Chapter-11_Testing_Overview.md (crawled Oct 2026; book: Winters/Manshreck/Wright, O'Reilly 2020)
- https://github.com/mikelane/pytest-test-categories (Sep 2026) + https://github.com/mikelane/pytest-test-categories/blob/HEAD/docs/architecture/design-philosophy.md
- https://github.com/kevin1skyrj/rideflow-data-engineering-pipeline/blob/HEAD/docs/testing_strategy.md (Sep 2026)
- https://github.com/tudor191/nova-project-bible/blob/HEAD/docs/architecture/16-testing-strategy.md (Sep 2026)
- https://github.com/sanjay10tech/purpllehackathon-smart-ai-store-surveillance/blob/HEAD/docs/architecture/testing-strategy.md (Jun 2026)
- https://github.com/vctplatform/vct-platform/blob/HEAD/docs/architecture/qa-testing-architecture.md (Apr 2026)
- https://github.com/eagleeyevisionlabz/wayland-m3ta-0s/blob/HEAD/src/process/resources/skills-library/bodies/skills/testing-quality/test-strategy-design/SKILL.md (Jul 2026)
- https://github.com/mejbaurbahar/software-tester-skills/blob/HEAD/skills/flaky-test-management/SKILL.md (Sep 2026)
- http://dev.to/beefedai/diagnosing-and-fixing-flaky-microservice-tests-4iag (Sep 2026)
- https://medium.com/startlovingyourself/quality-at-scale-how-google-keeps-bugs-in-check-across-billions-of-lines-7dfdcddff978 (~Jun 2025)
- https://www.conduktor.io/glossary/testing-strategies-for-streaming-applications (Sep 2026)
- https://debugg.ai/resources/from-staging-to-shadow-traffic-production-replay-patterns-2025 (~Sep 2025)
- https://www.linkedin.com/pulse/progressive-rollouts-ai-native-sdlc-core-capability-arup-das-xuhmf (Sep 2026)
