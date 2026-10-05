# Testing/QA — Phase-2 Domain Review

**Reviewer:** Phase-2 domain reviewer, testing/QA (fresh eyes — did not build this)
**Date:** 2026-10-05 · **Branch:** `program/architecture-revision-dom-testing`
**Standard judged against:** Phase-1 research — `research/testing-at-scale.md` (primary),
`research/operating-without-vendor-apis.md`, `research/swe-discipline.md`; the four
principal skills (principal-systems, principal-governance, principal-mindset,
execution-doctrine).

**Method:** read the Phase-1 research first, then inventoried `tests/` (49 files, 960 test
methods, 17,830 lines, stdlib `unittest` only), sampled one unit test
(`test_correlator.py`), one integration test (`test_firewall_integration.py`), the
fault-drill corpus (`ops/drills/`, `docs/drills/`, `ops/fault-injection-plan.md`), the
load research (`research/load-c2-2026-10-04.md`), and checked the live CI state via the
`gh` CLI. "It's fine" was not an option: every claim below cites a file, a line, or a
tool result.

**Bottom line up front:** Sentinel's testing culture is unusually honest for a
week-old codebase — tests document their own gaps, drills carry written honesty
contracts, and one battery ships a genuinely stateful vendor fake. But the strongest
asset (the 960-test suite) is currently **unenforced**: GitHub Actions has been dead
with `startup_failure` on every run since 2026-10-02, and the replacement is an honor
system. A suite that nothing gates is a ritual, not a control. Fix the gate first;
everything else ranks behind it.

---

## 1. WHAT EXISTS

Grounded in the repo, layer by layer. For each: what it is, what it proves.

### 1.1 Unit layer — strong, mostly hermetic, well-instrumented

- **Inventory:** the bulk of the 960 methods. `test_correlator.py` (fingerprint
  determinism, ADR-017 env/cluster collision — hex-format + sha256 re-derived in the
  test, not copied), `test_gate.py`, `test_race.py` (36), `test_watchdog.py` (36),
  `test_firewall.py` (47), `test_quantized_gate.py` (65), `test_freshness.py` (54),
  `test_eventlog.py` (57), `test_tuner.py`, `test_state.py`, `test_diskguard.py`,
  `test_attestor.py`, `test_signature_auth.py`, `test_model_pinning.py`,
  `test_mute_governance.py`, `test_retention.py`, `test_audit.py`, `test_ab.py`,
  `test_counterfactual.py`, `test_backtest.py`, `test_evalharness.py`.
- **What it proves:** pure-function correctness with real mathematical assertions
  (hashes recomputed, not asserted against magic strings). Good fixture discipline:
  `tests/helpers.py` provides `make_alert` and `FakeClock` (injectable clock — the
  freshness suite pins time via `BASE_EPOCH`, and `freshness_fixtures.py` states its
  design intent explicitly: "the rot matrix must test the mechanics, not the calendar").
- **Contract/schema layer (a genuine strength):** the freshness system is the model to
  copy elsewhere — `test_freshness.py` + `test_freshness_rot_matrix.py` ((fresh|stale)³
  rot matrix), canonical-JSON pinning, manifest verification, two-point validation
  discipline. `test_durable_forwarder.py` embeds `PD_RETRY_TABLE` as an executable
  fixture: "a PD docs change becomes a build break, not a 3 AM discovery" (row-for-row
  classifier assertion). This is Phase-1's "vendor contract is data, versioned in the
  repo" — and Sentinel already does it in these two places.

### 1.2 Integration layer — real stacks, partial mock fidelity

- **`test_firewall_integration.py`** (123 lines): flagged alert → fail-closed page through
  the **real** `EventLog` (validation + storage + read-back on tempdir sqlite). Honest
  docstring: documents "what phase 2 must additionally guarantee (documented, not
  tested here)" — hook ordering, dedup/storm-collapse flow. The test says what it does
  not prove.
- **`test_durable_forwarder.py`** (990 lines): the battery that disproves part of the
  Phase-1 claim. It ships a **stateful FakePD** (`ScriptedHTTP` + `FakePD`): scripted
  (status, body, headers) failure injection consumed first, request journal on every
  POST, and a *real PagerDuty dedup state machine* in the default path (re-trigger on
  open alert appends a trigger log entry; trigger after resolve creates a new incident;
  incidents stored as a list because one key can own two). Crash-safety batteries:
  kill -9 between "page sent" and "outbox marked delivered" neither loses the page nor
  double-pages; `run_once_sync()` determinism except where threading itself is under
  test. **Correction to Phase-1:** the vendor-mock research said "the mock PagerDuty
  server never landed" — true as a *shared ops artifact*, but a faithful stateful fake
  **did** land inside this battery. Credit it.
- **`test_forwarder.py`:** older `CaptureServer` (from `tests/helpers.py`) — stateless,
  always answers 202 (or one canned `status=500`). Request-journal-lite assertions
  (e.g., no Authorization header). Right layer (HTTP boundary), no state.
- **`test_shadow.py` / `test_shadow_report.py`** (800 lines): Stage-0 shadow tap against
  real loopback `ThreadingHTTPServer`; additive routes; threshold counterfactuals and
  subscription coverage. Shadow *mode* exists; there is **no** production-traffic replay
  loop and no canary promotion machinery — execution-doctrine's validate→shadow→canary
  is doctrine, not machinery.
- **`test_integrations.py`, `test_receiver.py`, `test_liveness.py`, `test_resolve_wiring.py`,
  `test_freshness_wiring.py`, `test_firewall_wiring.py`:** wiring/integration coverage
  of the ingest→decide→act path.

### 1.3 Load — one excellent measurement, zero harness

- `research/load-c2-2026-10-04.md` + `research/bin/load_c2_harness.py` (in-tree) +
  raw run JSONs in `research/load-c2/`. Seeded, reproducible, 3 runs per profile:
  **1,000/min sustained held** (99.7% HTTP 200, e2e p50 20ms / p99 1.28s), **10k/min
  burst 96% served with 4% loud resets** (never silent). Found by hand: the
  `gate.emitted` unbounded memory leak and event-log lock contention (p99 253ms, one
  run 585ms) — exactly the class soak tests exist to catch. The harness PR was **opened,
  NOT merged** (report's own words), and the harness lives in `research/bin/`, not in
  `tests/` — nothing runs it, nothing gates on it.

### 1.4 Fault drills — honest acceptance drills, not chaos experiments

- **7 fault drills** (`ops/drills/2026-10-04-fault-drills.md`, LANE 5 Tripwire, operator
  Petu, 7/7 PASS on `origin/main` @ `2afbf54`): kill-Jev-client mid-storm, 529 storm,
  forwarder crash/restart, showtime scenario. Real rig: `stub_pd.py` (recording 202
  responder), `stub_jev529.py` (always-529 with call counter), `fire.py` (burst sender
  with latency histogram), `hammer_livez.py` (read-path saturator). Each drill states
  its **bar** ("no hung alerts, no silent drops; every page reaches the vendor").
  `ops/fault-injection-plan.md` names the four platform laws under test and the rule
  "a failed drill becomes a Sun AM task, not a debate."
- **D13 cutover drill** (`ops/drills/d13_cutover_drill.py` → `docs/drills/drill-2026-10-05.md`):
  end-to-end runbook rehearsal with an explicit **HONESTY CONTRACT** — PD endpoint
  stubbed, human ack simulated by an agent, "a real human-acked drill is REQUIRED before
  the first prod cutover — this drill does not satisfy that." The honesty is exemplary;
  the mechanism is a one-off scripted run, not a scheduled experiment.
- What the drills are **not** (per Phase-1 Q4): no written steady-state hypothesis per
  experiment, no fault-injection *proxy* with programmable toxics (latency/jitter/reset/
  429/5xx) sitting between forwarder↔PD or gate↔Jev, no blast-radius/error-rate budget,
  no schedule. They are scripted acceptance drills with pass criteria — valuable, but
  one notch below chaos engineering.

### 1.5 Corpus / adversarial — present

- `tests/corpus/adversarial_corpus.json` + `manifest.json`, `test_firewall_corpus_gate.py`
  (164 lines), `test_correlator_craft.py`, `test_synthetic.py`. The injection-firewall
  path is tested against a committed adversarial corpus — good.

### 1.6 What the tests are honest about

Several test docstrings state their own limits in writing (the firewall integration
"phase 2" list; the durable forwarder's "Timing-sensitive tests use tiny Type-2
values"). This is rare and good. It is also a signpost: the authors already know the
gaps in §2 — they just have no mechanism that forces the gaps closed.

---

## 2. WHAT'S MISSING

Judged against the Phase-1 enterprise standard. Ordered by severity.

### 2.1 The CI gate is dead — nothing enforces anything (P0)

- `gh run list -R AdityaPagare619/sentinel` (checked 2026-10-05 17:0x IST): the last
  10 runs are all `completed/startup_failure`, 0s duration, back through
  2026-10-03T08:05:11Z. Per `ops/ci-repair.md`, this began 2026-10-02 18:41 UTC — an
  18/18 `startup_failure` streak escalated as a repo-level GitHub platform bug, with
  "manual local CI is the operative quality gate until GitHub resolves."
- Consequence: **every PR since Oct 2 has merged with no mechanical verification.**
  The PR template's "full suite green" is an honor-system checkbox. All of §1's
  strengths are real code, but the control that makes a suite a gate — the
  non-zero-exit-code block on merge — does not exist. (Phase-1 Q3: "k6 thresholds
  turn a test into a pass or fail gate for CI — the mechanism is thresholds producing
  a non-zero exit code that blocks the merge." The threshold part is missing; the CI
  part is *broken*.)
- Principal-systems verdict: a test suite with no binding gate is **locally optimized,
  globally inert** — the effort is real, the control is not.

### 2.2 No test-size classification, no hermeticity enforcement (P1)

- Phase-1 Q5 (Google Ch. 11): the classification is by *what the test may touch*
  (small/medium/large), not "unit vs integration" — because the former is
  machine-checkable. Sentinel has **zero markers**: no `@pytest.mark.small`,
  no conftest, no audit. A "unit" test that secretly opens a socket, sleeps, or
  writes outside tempdir is undetectable today.
- At 960 tests and growing, with 5 test files containing `time.sleep`, this is where
  trust goes to die — Phase-1 names it verbatim ("the first network-touching 'unit'
  test or the first quarantinable flake is a matter of time, not luck").

### 2.3 No threshold-gated load harness, no soak (P1)

- C2 is a measurement, not a practice: a report in `research/`, a harness in
  `research/bin/` whose PR never merged, raw JSONs aging in a folder. Nothing fails a
  PR if p99 regresses, if the admission bound trips early, or if `gate.emitted`
  leaks faster.
- **No soak test at any cadence.** The two defects C2 found by hand — the
  `gate.emitted` unbounded-growth leak and event-log lock contention — are precisely
  what soak tests catch mechanically. "Evidence that isn't a gate decays" (Phase-1 Q3);
  the C2 numbers are already 1 day old and aging.
- Missing tiers per Phase-1 Q3: PR-gated smoke (small VU count, percentile assertions,
  non-zero exit), nightly sustained + spike, weekly soak with leak/drift detection.

### 2.4 No flaky-quarantine discipline (P1)

- No quarantine policy, no rerun-to-detect, no owner/SLA on flakes, no hermeticity
  enforcement to prevent them. The durable-forwarder battery itself admits timing
  sensitivity ("tiny Type-2 values"). Five files use `time.sleep`. Google/Microsoft/
  Facebook practice (Phase-1 Q5): identify via rerun, quarantine (not skip-and-forget)
  with owner + SLA, fix or delete; retries capped at 1–2, never on the same test for
  more than a week. Sentinel has none of this.

### 2.5 Vendor stubs have no honesty mechanism (P2)

- Per Phase-1 Q1/Q2: every hand-written stub is a hypothesis about reality, and "the
  audit question for each: what mechanism re-validates this stub against the real
  dependency, and on what schedule? Today the answer is 'none.'"
- Concrete gaps:
  - `LatencyStubClient` has a *verified latency model* but an **unverified shape
    model** — nothing replays real Jev HTTP traffic or re-records it. stdlib-only
    blocks `vcrpy`; the mechanism is reimplementable in-test as a tiny cassette
    recorder (record-once, replay with `record_mode="none"` semantics).
  - `CaptureServer` (test_forwarder) is stateless: no PD dedup state machine, no 429
    program, no schema validation.
  - The stateful `FakePD` exists but lives **inside one test file** — not a shared
    `tests/doubles/` module, not reused by `test_forwarder.py`, not extended to the
    schema-validation layer of the Phase-1 faithful-mock checklist (dedup_key ≤ 255
    → 400, summary ≤ 1024, 512 KB limit, unknown routing key → 400, 429 **without**
    Retry-After per PD docs, 202-without-`"status":"success"` contract violation).
  - **No nightly drift harness**: neither PagerDuty nor Jev will verify Sentinel's
    contracts (third-party caveat, Phase-1 Q2), so Sentinel must own a scheduled
    read-only probe that diffs live response shapes against checked-in contracts.
    Today: zero. The `PD_RETRY_TABLE` "verified live 2026-10-03" comment has no
    mechanical refresh — it is a claim, not a control.

### 2.6 No contract/schema tests for receiver inputs or the read-only API (P2)

- Phase-1 Q5 checklist: contract/schema tests for every event (JSON Schema on webhook
  payloads), OpenAPI conformance snapshot for the read-only API. Sentinel validates
  freshness manifests and PD payload shapes, but there is **no checked-in schema for
  inbound webhook payloads** and **no API conformance test** for the read-only
  surface. A malformed-but-plausible vendor payload is tested only by hand-written
  cases.

### 2.7 No data-quality / invariant reconciliation layer (P3)

- Phase-1 Q5: the pipeline pyramid's data-quality tier (schema, business rules,
  reconciliation). The event log's hash chain is *verifiable* — but there is no
  standing CI check that "events in = decisions + folds + errors, accounted" (no
  silent drop reconciliation) on every build. `test_audit.py` covers audit mechanics;
  the invariant layer is missing.

### 2.8 Suite runtime and parallelism are unmanaged (P3)

- Evidence: a plain `python3 -m unittest discover -s tests` on this branch did not
  finish within ~2.5 minutes (backgrounded at 15s, still running; killed). Some
  batteries do real sleeping/waiting (race budgets, watchdog timers). There is **no
  per-test time budget** (pytest-test-categories enforces 1s/5min/15min by size),
  **no parallel runner**, and no measured full-suite runtime published anywhere.
  A suite whose runtime nobody measures will grow until nobody runs it.

### 2.9 No requirement→test traceability (P3)

- Per `research/swe-discipline.md` §3: no REQ-xxx numbering, no traceability matrix.
  The testing-angle consequence: the question "which customer-visible behavior does
  this test verify, and which behaviors have no test?" is unanswerable without reading
  17,830 lines. (This is the testing half of the SWE discipline's biggest gap.)

---

## 3. CONCRETE REVISION PROPOSALS

Ranked by value = (risk reduced) × (enforceability) ÷ (cost). Each states the
rationale, the concrete build, and what would verify it.

### P0-1. Establish a mechanical merge gate — the gate that gates the gates

- **Rationale:** everything in §1 is advisory until a machine blocks a merge on it.
  CI has been dead since Oct 2; the longer the honor system runs, the more
  untested-by-machine code accumulates and the harder the real gate is to turn on.
  (principal-governance: "no major component starts from a feeling"; execution-doctrine
  §7: feedback-loop velocity is the health metric — right now the loop's verify step
  is manual.)
- **Build:** (a) pursue the GitHub `startup_failure` escalation to resolution; (b)
  in parallel, stand up the interim mechanical gate the same week: a scheduled
  box-side runner (cron on the 2-CPU box) that, on every push to a PR branch, runs
  the full suite + the load smoke + the hermeticity check and posts a pass/fail
  artifact; make "green artifact attached" a written merge requirement in the PR
  template. Either path is acceptable; "no gate" is not.
- **Verifies:** `gh run list` (or the interim runner's log) shows zero
  `startup_failure`/skipped gates over 7 consecutive days; a scratch PR with a
  deliberately failing test is blocked, and the block is visible in the record.
  **Anti-theater check:** if the interim runner ever goes red and a merge happens
  anyway, the gate failed — log the violation, don't excuse it.

### P1-1. Threshold-gated load harness: PR smoke + nightly sustained + weekly soak

- **Rationale:** C2 found a memory leak and lock contention *by hand*. Hand-found
  evidence decays; gated evidence compounds. The C2 numbers become the first
  thresholds, not a report. (Phase-1 Q3 three-tier practice; principal-mindset §2:
  freeze the protocol before the decisive measurement — thresholds are the frozen
  protocol.)
- **Build:** promote `research/bin/load_c2_harness.py` into `tests/load/` (merged,
  maintained — close the open PR or re-land it); add threshold assertions as exit
  codes: PR smoke (1,000/min × 2 min, seeded) asserting e2e p99 ≤ 1.5s, 0 silent
  drops, admission-bound behavior; nightly sustained (1,000/min × 10 min) + spike
  (10k/min burst); weekly soak (hours) asserting `gate.emitted` growth ≈ 0 and
  event-log p99 within budget. stdlib runner is fine — the non-negotiable part is
  **thresholds-as-gates**, not the tool.
- **Verifies:** on a scratch branch, reintroduce the `gate.emitted` leak (one-line
  revert of the fix when it lands); the weekly soak must go red. If the harness
  cannot catch the one leak we *know* existed, it is theater.

### P1-2. Test-size classification + hermeticity enforcement for the 960

- **Rationale:** Google's move from "unit vs integration" to "what may this test
  touch" is the difference between the suite as an asset and as a liability. With
  960 tests and 5 files sleeping, the first silent network dependency is a matter of
  time. (Phase-1 Q5; pytest-test-categories precedent: block network/FS/DB/`sleep`
  in small tests, enforce time budgets.)
- **Build:** classify every test file small/medium/large by the Ch. 11 table
  (small = in-process, no network/FS/`sleep`; medium = localhost loopback + tempdir;
  large = everything else). Add a ~30-line unittest runner wrapper using
  `sys.audit` / socket-patch to **fail any small test that opens a socket or
  sleeps**, and enforce per-size time budgets (fail small > 5s, medium > 5min).
  Publish the distribution; target ~80/15/5 by count.
- **Verifies:** deliberately add a socket-opening "unit" test on a scratch branch —
  the hermeticity gate must fail it. Also verifies: the full-suite runtime is
  measured and published for the first time.

### P1-3. Flaky-quarantine policy with owner + SLA

- **Rationale:** flakes are the leading cause of suite distrust; distrust is the
  leading cause of ignored gates. (Phase-1 Q5: quarantine, not skip-and-forget.)
- **Build:** rerun-failures detection in the runner (a test that fails then passes
  on retry is flagged flaky); flagged tests move to a `tests/quarantine/` manifest
  with a named owner and a 7-day SLA; max 1 retry on the blocking gate; a quarantined
  test that can't be stabilized in 7 days is fixed or deleted. Audit the 5
  `time.sleep` files first — replace sleeps with condition-polling.
- **Verifies:** a weekly quarantine report exists (even if empty); no flake older
  than its SLA remains in the blocking suite.

### P2-1. Promote the vendor doubles: shared stateful FakePD + cassette recorder

- **Rationale:** the faithful mock exists in one battery; the Phase-1 faithful-mock
  checklist (§6 of the vendor-APIs research) says schema + behavior + failure-mode
  fidelity is what makes a mock deserve the name. Today the schema layer is missing
  everywhere and the behavior layer lives in one file.
- **Build:** (a) promote `FakePD`/`ScriptedHTTP` from `test_durable_forwarder.py`
  into a shared `tests/doubles/` module used by all forwarder tests; (b) add the
  schema-validation layer (dedup_key ≤ 255 → 400, summary ≤ 1024, 512 KB limit,
  unknown routing key → 400); (c) add scripted toxics: 429 **without** Retry-After
  (matches PD docs), 202-without-`"status":"success"`, connection reset, malformed
  chunk; (d) build the stdlib cassette recorder for Jev (record-once against real
  Jev with the team's key, replay with `record_mode="none"` semantics, secrets
  scrubbed) so the stub's *shape* model is re-validated, not just its latency model.
- **Verifies:** a forwarder test matrix asserting exact wire bytes + PD incident-store
  state for the full dedup table (re-trigger/ack-then-trigger/resolve-then-trigger/
  different-routing-key); a scratch-branch drift in the Jev response shape fails the
  cassette replay. **Also verifies the doubles aren't lying:** the drift harness in
  P2-2 is the check on the check.

### P2-2. Nightly vendor drift harness (the missing half of contract testing)

- **Rationale:** PagerDuty and Jev will never verify Sentinel's contracts — the
  provider half of consumer-driven contracts is missing by construction for
  third-party vendors. The industry-standard replacement is a self-owned scheduled
  probe (Phase-1 Q2; the smsgatewaycenter analysis). The `PD_RETRY_TABLE`
  "verified live 2026-10-03" comment is currently a claim with no refresh.
- **Build:** scheduled job (Sentinel team's own PD test key + Jev key — never the
  operator's) hitting read-only/sandbox paths; diffs live response *shapes* against
  the checked-in contracts; opens an issue / pages on shape change. Read-only probes
  by default; mutating endpoints only in test mode. Recorded responses become the
  cassette anchors for P2-1.
- **Verifies:** introduce a synthetic contract change in scratch (e.g., PD returns an
  extra field / renames one) — the harness must raise within one schedule interval.
  False-positive rate tracked; a harness that cries wolf weekly gets tuned, not
  ignored.

### P2-3. Inbound schema + read-only API conformance tests

- **Rationale:** the pipeline pyramid's contract tier (Phase-1 Q5). Sentinel
  schema-validates its own manifests but not the vendor-shaped payloads arriving at
  its front door.
- **Build:** checked-in JSON Schemas for every receiver input shape; a test that
  every inbound fixture in `tests/` validates; an OpenAPI snapshot test for the
  read-only API (response shapes pinned, diff fails the build).
- **Verifies:** a scratch-branch change to an API response shape (field rename)
  fails the conformance test.

### P3-1. Invariant reconciliation in CI (the data-quality tier)

- **Rationale:** the event log's hash chain is verifiable but not verified per
  build; "no silent drop" is Sentinel's core promise.
- **Build:** a CI check over a seeded end-to-end run: events in = decisions + folds
  + errors, hash chain continuous, dedup window invariants hold. Reuse the drill
  generators (`ops/drills/gen_alerts.py`) as the seed.
- **Verifies:** a scratch branch that silently drops 1% of events (injected in the
  correlator) fails the reconciliation check.

### P3-2. Lightweight REQ→test traceability

- **Rationale:** closes the loop with the SWE-discipline gap: without traceability,
  "which behaviors have no test" is unanswerable.
- **Build:** a `tests/REQUIREMENTS.md` table mapping each test file to the
  requirement(s)/ADR(s)/design-law(s) it verifies; new test files must add their
  row (PR-template checklist item). Keep it a table, not a framework — the 10-line
  cron beats the platform here.
- **Verifies:** for any of the four platform laws, the table names the tests that
  prove it; an auditor can find an untested law in under five minutes.

---

## Appendix: what was deliberately NOT proposed

- **k6/Locust/JMeter adoption:** the stdlib-only constraint is frozen for shipped
  code; the harness runner is test-only, and Phase-1 Q3 explicitly allows the
  harness to stay in Python. Tool choice is not the gap — gating is.
- **Full Pact broker / can-i-deploy:** Pact proper is for internal consumer/provider
  pairs with separately deployed services. Sentinel's read-only API + UI are not
  there yet; OpenAPI conformance + the drift harness is the right-sized mechanism
  (Phase-1 Q2's own conclusion).
- **Production chaos / GameDay against live PagerDuty:** PagerDuty offers no sandbox
  and the operator may have no account — production fault injection against a
  paging vendor is a Type-1 decision with irreversible blast radius. The fault-proxy
  + scripted toxics in CI/nightly (P2-1) is the correct scope until the harness
  earns trust.

## Appendix: verification of this review's own claims

- CI state: `gh run list -R AdityaPagare619/sentinel --limit 10` on 2026-10-05 —
  all `startup_failure`, 0s, back to 2026-10-03T08:05:11Z. (The `actions/workflows`
  API query returned empty in this session; the run list is the evidence.)
- Test counts: `grep -c "    def test" tests/test_*.py` → 960 methods; `wc -l` →
  17,830 lines across 49 files.
- Suite runtime: `python3 -m unittest discover -s tests` did not complete in
  ~2.5 min on the 2-CPU box (killed); `tests.test_correlator + tests.test_forwarder`
  (30 tests) green in 4.8s — the suite is not broken, it is slow and unmeasured.
- Samples read in full: `tests/test_correlator.py` (head), `tests/test_firewall_integration.py`
  (head + docstring), `tests/test_durable_forwarder.py` (docstring + doubles section),
  `tests/test_shadow.py` (imports + docstring), `tests/helpers.py`, `tests/test_freshness.py`
  (head), `tests/freshness_fixtures.py` (head), `ops/drills/2026-10-04-fault-drills.md`
  (head), `ops/fault-injection-plan.md` (head), `ops/drills/d13_cutover_drill.py`
  (head + honesty contract), `research/load-c2-2026-10-04.md` (verdict + method).
- Phase-1 research read: `research/testing-at-scale.md` (full),
  `research/operating-without-vendor-apis.md` (full), `research/swe-discipline.md`
  (full, §3 truncated by length — the requirements-gap conclusion is intact).
