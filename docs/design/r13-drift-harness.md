# R-13: Drift Harness + Shared FakePD + Jev Cassette Recorder — Design

**Lane:** race-1 (prep wave, pre-T+0) · **Branch:** `lane/prep-race-threat`
**Date:** 2026-10-05 IST · **Status:** design doc, PRE-T+0 PREP — no code changes.
**Decision type:** Type 2 (reversible: test-only harness; vendor stubs carry no production behavior).
Implements the design half of **R-13** (`docs/architecture-revision/PIPELINE-REVISION.md`,
R-13, "Vendor-boundary honesty: drift harness + shared FakePD + cassettes").
Source memos: `forwarder-byok.md` P4/P5; `testing-qa.md` P2-1/P2-2;
`research/operating-without-vendor-apis.md` §7.

---

## 0. ⚠️ GATED ON Q6 (Aditya decision) — read first

**UPDATE (2026-10-05 ~20:25 IST, recorded during prep — no scope change): T+0 FIRED
and Q6 was decided as a SPLIT.** Live-run posture is now: (a) **Jev live runs
AUTHORIZED** for the drift-harness recorder (§3) against the team-held Jev key —
drift-harness live runs only, hard cost cap, keys never logged; (b) **PagerDuty
stays FakePD-only — no PD key exists.** The PD half of §5's live harness and any
PD-side live re-recording therefore remain on the fallback (§0.3/§0.4 as they
stand: FakePD-only battery + quarterly manual live-shape check); the Jev side may
proceed to live recording and the scheduled shape-diff under the stated cap. The
rest of this section now reads as the pre-decision plan, kept for traceability —
the binding posture is this update box.

The **live** half of this design — recording cassettes against real PagerDuty/Jev and
running the scheduled drift harness — requires a **Sentinel-team-held PD test key +
Jev key** (never the operator's), with read-only probes plus one trigger/ack/resolve
cycle on a sacrificial service. That is **Type-1 question Q6**
(`PIPELINE-REVISION.md` §9.6): *"Provider-contract suite keys… Authorized to provision
and hold?"*

**Until Q6 is answered, and if Q6 is denied, the plan is:**

1. The stub-cassette infrastructure design (§3, §4) **proceeds** — record/replay
   machinery is built and shipped with hand-authored cassettes carrying the
   §4 caveat, verified only for code-behavior, never for vendor shape.
2. The coverage battery runs **FakePD-only** against the checkpointed PD-docs
   contract (`PD_RETRY_TABLE`, verified live 2026-10-03 — a *claim* with no
   mechanical refresh until the harness runs).
3. Fallback refresh (12H-PLAN §5): **quarterly manual live-shape check** — a human
   with a test key runs the recorded probes by hand and eyeballs the diff. Manual,
   unglamorous, scheduled: execution-doctrine's "manual first" is the discipline
   that keeps the stub from silently lying.
4. If Q6 is approved: §5's scheduled harness replaces the manual check, and
   stub cassettes are re-recorded against live vendors (caveat removed per-cassette
   on re-record).

Nothing in this doc provisions keys, requests credentials, or touches production
config. The harness refuses to run without an explicit key path (`SENTINEL_DRIFT_KEY_*`
envs absent ⇒ harness is a loud no-op, CI-marked "skipped: Q6 pending").

---

## 1. The problem, in one paragraph

Every hand-written stub is a hypothesis about reality. `FakePD` is a genuinely
stateful fake with real PagerDuty dedup semantics — but it lives inside one test
file (`tests/test_durable_forwarder.py:136`), its behavior layer omits the PD-docs
behaviors that pinning exists to manage (routing-key-scoped dedup, ack machine), and
its schema layer doesn't exist (no 400s on `dedup_key > 255`, `summary > 1024`,
512 KB bodies, missing/invalid `routing_key`). The Jev side is worse:
`LatencyStubClient` has a verified *latency* model and an **unverified *shape* model**
— nothing ever replays real Jev HTTP traffic. The `PD_RETRY_TABLE` row-for-row test
asserts *code == table*, not *table == reality*; the "verified live 2026-10-03"
comment has no mechanical refresh. `testing-qa.md` §2.5's audit question — *"what
mechanism re-validates this stub against the real dependency, and on what schedule?"*
— is today answered "none." R-13 is that mechanism.

## 2. FakePD promotion plan — what "promoted" means

"Promoted" is a checklist, not a file move. FakePD earns promotion from
`test_durable_forwarder.py` into a shared `tests/doubles/` module only when every
row below is green. Until then it stays where it is — the tests that depend on it
are honest about what they prove.

### 2.1 Promotion gate: the three fidelity layers

| Layer | Required behaviors (each a test, not a comment) | Ground |
|---|---|---|
| **Behavior** | re-trigger on open incident appends (existing); **trigger after resolve creates a NEW incident**; **ack on open alert suppresses notifications; ack with no open alert ⇒ dropped, still 202**; **incidents keyed by `(routing_key, dedup_key)` — events via a *different* key than the original trigger are dropped per PD docs** | forwarder-byok.md P4(a)(b); PD docs via search result [2] |
| **Schema** | 400 on `dedup_key > 255`; 400 on missing/invalid `routing_key`; 400 on `payload.summary > 1024`; 400 on bodies > 512 KB; 202 without `"status":"success"` is a vendor-contract violation ⇒ retryable + loud (existing `PD_RETRY_TABLE` classifier assertion is the seed, extended to wire-level responses) | forwarder-byok.md P4(c); testing-qa.md P2-1(b)(c) |
| **Failure** | scripted toxics: 429 **without** `Retry-After` (matches PD docs — today the mock always scripts one); connection reset; malformed chunk; programmable latency; fail-loud (500 + "unknown route") on unregistered paths | forwarder-byok.md P4(d); research vendor-APIs §6 (WireMock fault vocabulary); PD docs [2]: "Dynamic throttling, 429 on exceed, no `Retry-After` header" |

**Anti-theater rule for the schema layer** (principal-mindset §2, anti-self-deception):
the 400 rows must be asserted against the *wire* (full request bytes into FakePD,
full response status parsed by the classifier), not against the classifier function
in isolation. A test that asserts the classifier matches a table is code==table;
promotion requires wire==table too.

### 2.2 The R-13 coverage gate: the checklist itself

R-13's verify line names the gate: *"a PD-docs behavior with no corresponding FakePD
test is the coverage gate (the checklist itself)."*

**Seed list** (started in this doc per 12H-PLAN race-2; Phase 4 expands it into
`tests/doubles/pd_docs_checklist.md`):

1. Dedup scoped by routing key, not dedup_key alone. *(PD behavior; forwarder
   pinning's entire reason to exist — forwarder-byok.md §2.3)*
2. Ack on open alert suppresses; ack on nothing ⇒ dropped, still 202.
3. Trigger after resolve ⇒ NEW incident (no zombie append).
4. `dedup_key` > 255 chars ⇒ 400. *(Already terminal in the forwarder; the mock
   must agree.)*
5. Missing/invalid `routing_key` ⇒ 400, no incident created.
6. `payload.summary` > 1024 chars ⇒ 400.
7. Request body > 512 KB ⇒ 400.
8. 429 carries **no** `Retry-After` header (dynamic throttling).
9. 202 without `{"status":"success"}` body ⇒ contract violation (retryable + loud).
10. `event_action: acknowledge`/`resolve` require `dedup_key`; `trigger` without
    `dedup_key` is accepted (PD generates one) — behavior recorded, not assumed.

The checklist lives as a machine-readable file (YAML: behavior → required
`dedup_key`-table test → status), and CI fails on an unchecked row only after the
row's reference behavior is pinned — never on a guess. **What we know:** this list
is seeded from the forwarder-byok.md review + one external verification pass
(source [2] below); any row not verifiable against PD docs gets marked
`unverified — source needed`, never silently dropped.

### 2.3 Migration shape (design, not build)

- `tests/doubles/__init__.py`, `fake_pd.py`, `scripted_http.py` — moved, not copied;
  the old import sites re-export until Phase 4 updates them (one PR, mechanical).
- `test_forwarder.py`'s stateless `CaptureServer` stays for its one job (always-202
  HTTP-boundary tests); anything asserting dedup/ack semantics migrates to FakePD.
- Drill stubs (`ops/drills/stub_pd.py`, `stub_jev529.py`) stay as scripted acceptance
  rigs; they are not promoted — their honesty contract (stubbed endpoint, stated in
  `2026-10-04-fault-drills.md`) is the correct scope for one-off drills.

## 3. Stdlib Jev cassette recorder design

**Constraint (frozen):** stdlib-only. `vcrpy` is off-limits by the shipped-code hygiene
layer — so the recorder is reimplemented in the *test* tree (~150 lines, pure stdlib:
`http.client` interception is unnecessary; the client under test is ours, so the
recorder wraps at the transport call site the tests already patch).

### 3.1 Record/replay shape

```
tests/cassettes/
  jev/<test-name>.cassette.json          # the recorded interaction
  jev/<test-name>.cassette.json.sha256   # integrity pin, checked in
```

Each cassette file:

```json
{
  "cassette_format": "sentinel-cassette/1",
  "recorded_at": "2026-10-XXTXX:XX:XXZ",
  "recorded_against": "live-vendor | stub-authored",
  "vendor": "typesafe-jev",
  "pinned_model": "jev-1.13.0",
  "scrubbed": true,
  "interactions": [
    {
      "request": {
        "method": "POST", "path": "/v1/systemone",
        "match_key": {"body_sha256": "<hex>", "model": "jev-1.13.0"}
      },
      "response": {
        "status": 200,
        "headers": {"content-type": "application/json"},
        "body": "<base64 of scrubbed JSON>",
        "latency_ms": 812
      }
    }
  ]
}
```

**Design decisions and why:**

- **JSON, not YAML** (VCR.py uses YAML; our recorder uses JSON): the repo's contract
  fixtures are JSON (`PD_RETRY_TABLE`, canonical-JSON pinning in `test_freshness.py`
  — the freshness system is the named model to copy). One serialization dialect for
  machine-checkable artifacts.
- **`match_on` = (`path`, `body_sha256`, `pinned_model`)**, not raw body: PD/Jev
  request bodies carry volatile fields (timestamps, attempt nonces). VCR.py's
  `match_on: [uri, method, body]` practice maps here to a semantic match key —
  match on what the *test* varies, ignore what the world varies. Body bytes are
  pinned by hash for integrity, not compared byte-wise at match time.
- **Secrets scrubbed at record time, fail-closed:** the recorder's allowlist of
  scrubbed paths (`authorization`, `routing_key` in PD cassettes, any `*key*`
  header/body field) is checked *before* write; an interaction with an unrecognized
  credential-looking field raises instead of writing. A cassette committed with a
  secret is a §5 violation (safety breach → andon). VCR.py's `filter_headers`
  precedent is the model (source [1]).
- **Record modes map to VCR.py's vocabulary** (source [1]): `once` (record if
  missing, else replay — default), `new_episodes` (replay known, record unknown),
  `none` (replay only, **fail if cassette missing** — CI's mode), `all` (re-record,
  requires an explicit `--re-record` flag + a live key; never in CI).
- **Replay is timing-free:** `latency_ms` is metadata for the budget vocabulary,
  not a sleep — replay never sleeps (tests that need timing use `FakeClock`,
  per the existing fixture discipline).
- **Non-determinism is recorded honestly:** Jev flips 1.3–2.2%. A cassette records
  *one* draw; tests asserting on choices must run multi-draw suites, never assert
  a single draw's exact choice. The cassette's job is shape replay, not choice
  oracle — this is the exact line §4's caveat draws.

### 3.2 What replay proves — and what it doesn't (the honesty contract)

Replay proves: the client's retry classifier, the race's timeout paths, the payload
builders, and the drift-detection assertions behave correctly against the *recorded
shape*. It does not prove the shape is current — that is the drift harness's job
(§5) or the §4 caveat's admission.

## 4. The stub-cassette caveat text (mandatory)

Every hand-authored (never-recorded-against-live) cassette **must** carry this text
verbatim in a `CAVEAT` field of the cassette file, and the test loading it must
surface the caveat in the failure message of any shape-sensitive assertion:

> **STUB CASSETTE — shape unverified against live vendor.** This cassette was
> authored by hand (from vendor documentation / prior observations), not recorded
> against the live vendor. It verifies that Sentinel's code handles *this recorded
> shape*; it proves nothing about what the vendor sends today. A passing test
> against this cassette is a behavior test against a hypothesis, not a contract
> test against reality. Recorded-against-live refresh: `<none — gated on Q6 keys /
> scheduled: <cadence> / last manual check: <date>>`. Do not cite this cassette's
> passing as evidence of vendor compatibility in any report, brief, or audit.

**Why verbatim** (OPERATING-RULES §3.4 honesty law): the BYOK lane's "simulated-spill
audit lie" and the kubaik phantom-citation incident are the precedents — a caveat
that can be silently reworded is a caveat that will be. The `recorded_against:
"stub-authored"` field is machine-grepable; any CI job asserting "vendor
compatibility" must exclude stub-authored cassettes, or fail.

## 5. Scheduled drift harness (design; live half gated on Q6)

**Purpose:** close the loop `testing-qa.md` P2-2 and `forwarder-byok.md` P5 name:
the provider half of consumer-driven contracts is missing *by construction* for
third-party vendors, so Sentinel owns a scheduled probe that diffs live response
*shapes* against the checked-in contracts.

**Design:**

1. **Schedule:** nightly (CI replacement gate or box cron — same runner as the
   interim mechanical gate per P0-1; the harness and the gate share infrastructure,
   not code).
2. **Keys:** Sentinel-team test keys from a secrets path the harness reads at run
   time (never committed, never the operator's). **Absent keys ⇒ loud skip**
   ("skipped: Q6 pending"), not a fake pass. A harness that passes without probing
   is theater (principal-mindset §11 anti-theater audit).
3. **Probes (read-only by default):** PD — `GET /v2/enqueue` is trigger-only, so the
   PD half uses *response-shape validation of the retry-table rows*: one
   trigger + ack + resolve cycle on a **sacrificial service** (delete after), plus
   a malformed-trigger probe to confirm the 400 row. *(PD side overridden to
   FakePD-only by the §0 update box — Q6 SPLIT: no live PD probes run; this item
   now describes the pre-decision plan only.)* Jev — `pin_probe` question
   shape (the template from `revalidation.py`), checking `DecisionResponse` field
   set and types, not choices.
4. **Diff:** live response JSON-Schema (shape only — keys dropped, values
   type-erased) compared against the checked-in contract; diff ⇒ issue opened +
   control-plane page. The recorded responses are committed as new cassette
   anchors for §3 (`recorded_against: "live-vendor"`, caveat removed on re-record).
5. **Schedule-miss is itself an alarm:** the suite pages on six months without a
   refresh run — *"a deliberate response-shape change in the mock suite trips the
   diff"* is the falsifier; the harness missing its own schedule must also trip
   (forwarder-byok.md P5's stated falsifier).
6. **False-positive budget:** tracked per run; a harness crying wolf weekly gets
   tuned, not ignored — a noisy alarm trains the ignore reflex (R-18's anti-theater
   decay: the genesis cry-wolf finding).

### 5.1 Hard cost cap — the mechanism (binding, added post-Q6-SPLIT review)

The §0 update box says "hard cost cap" — this section is the mechanism that makes
it a cap instead of a label. **Max live Jev calls per scheduled run: 50.** The
probe set in §5.3 is bounded by construction (one `pin_probe` shape check per
pinned Jev model + one malformed-shape probe = ≤2 live calls), so 50 is generous
headroom for retries, not a tuning dial. **Enforcement point:** a single in-process
counter lives in the harness runner at the *only* live-transport choke point — the
wrapper around the Jev client's request call that fires real HTTP (the same call
site the cassette recorder intercepts in §3.1). The counter increments **before**
each live request is sent; if the next increment would exceed 50, the runner
aborts the whole run immediately with a hard failure (CI red, not "skipped", not a
silent pass — an overrun means the probe set escaped its bound, which is a bug).
A second, independent guard: CI asserts at plan time that the enumerated live-probe
count ≤ 50 before the runner starts. **Cap changes:** raising or lowering the cap
requires a signed entry in `ops/decision_log.md` approved by Petu (senior-most
principal); any *increase* above 50 additionally requires Aditya's explicit
approval (founder-deputy authority — the Jev key's spend is his). The counter is
per-run (reset on each scheduled invocation); CI's plan-time assertion makes the
cap visible before money is spent.

**Verify (post-build, Phase 4):** introduce a synthetic contract change in scratch
(PD returns a renamed field; Jev drops a field) — the harness must raise within
one schedule interval. And the reverse: a scratch-branch drift in the Jev shape
fails cassette replay.

## 6. Rejected alternatives

| Alternative | Rejected because |
|---|---|
| Adopt Pact / a contract broker | Pact is for internal consumer/provider pairs with separately deployed services. Sentinel's vendors are third parties who will never verify our contracts (testing-qa.md Q2 caveat — the provider half is missing *by construction*). The self-owned drift harness is the right-sized mechanism; Pact would be theater with a dashboard. |
| Vendor SDKs or `vcrpy` | Frozen stdlib-only hygiene layer for shipped code; `vcrpy` is off-limits. The recorder is test-tree-only, ~150 lines. Adopting a recording framework to avoid writing one is Type-2 energy on a Type-1-clean design. |
| Validate stubs by reading vendor docs again (manual refresh) | Docs lag reality and have no machine check; `PD_RETRY_TABLE`'s "verified live 2026-10-03" comment is the worked example — a claim with no refresh. Manual reading is the *fallback* (§0.3), not the plan. |
| Reuse drill stubs (`stub_pd.py`, `stub_jev529.py`) as the fake | Scripted acceptance rigs with a stated one-off honesty contract; promoting them confuses drill fidelity with contract fidelity. The stateful FakePD already exists — Chesterton's fence: the drills' stubs are *for drills*. |
| Run live drift probes with operator-held keys | Explicitly rejected by R-13's own terms ("never the operator's"). Operator BYOK keys are per-deployment secrets; the Sentinel team's test keys are the only legitimate probe identity. Doing otherwise is a secrets-handling violation and a Q6 gate. |
| Skip the caveat; stub cassettes are "obviously" stubs | The kubaik incident: numbers without a verifiable source traveled into briefs. Anything not labeled will be cited as verified within a quarter. The caveat is load-bearing honesty infrastructure. |

## 7. Risk paragraph (pre-mortem: why this design could fail)

The likeliest failure is not technical: the drift harness is the first nightly job
whose whole job is to *potentially* cry vendor-changed, and an org that just spent
a quarter building faithfully-mocked batteries will feel each diff as a false
accusation — the tuning instinct ("relax the schema, it's probably fine") will
quietly hollow the diff until the harness passes against everything, i.e., against
nothing. The defenses are structural, not motivational: the §4 caveat is verbatim
and machine-grepable; the harness's schedule-miss and false-positive budget are
alarmed, not reviewed; the coverage-gate checklist fails on *unchecked* rows, never
on checked-and-red ones (red is information, unchecked is theater). The second
risk is Q6 denial by default — the fallback degrading into "quarterly manual check"
degrading into "nobody does it": the manual check is a named calendar item owned by
a named owner (Relay per the ownership map, devops), not a vibe. The third risk is
cassette rot on the Jev side: recorded answers freeze one nondeterministic draw and
a future engineer will assert on it; the §3.1 rule (cassettes are shape replay, not
choice oracles) must be enforced in review, because no test can.

## 8. Traceability

**External sources (web research, accessed 2026-10-05):**

1. VCR.py record-mode semantics (`once` / `new_episodes` / `none` / `all`),
   secret filtering (`filter_headers`), commit-cassettes + re-record-periodically
   practice: https://github.com/religa/multi_mcp/blob/HEAD/tests/cassettes/README.md
   (repo guide, updated 5 days before access). Changed this design: record-mode
   vocabulary adopted verbatim; JSON chosen over YAML for repo-consistency;
   `filter_headers` became the fail-closed scrub allowlist (§3.1).
2. PagerDuty Events API v2 contract (endpoint `POST /v2/enqueue`,
   `routing_key` in body, 202 `{"status":"success","dedup_key":...}`,
   dynamic throttling → 429 with **no** `Retry-After` header, trigger/ack/resolve
   semantics): https://github.com/axonops/go-audit/issues/210 (integration write-up,
   crawled ~49 days before access). Changed this design: seed-list rows 2, 8, 9
   (§2.2) are now anchored to this source rather than memory of PD docs.

**Skill clauses that bind (OPERATING-RULES §2.1):**

- *principal-governance, unforgiving API design + contract-first:* the vendor
  contract is data (`PD_RETRY_TABLE` as executable fixture, schema diff as the
  harness output); promotion is a machine-checkable gate, not a feeling.
- *principal-mindset §2 (anti-self-deception, move 2 — freeze the protocol):*
  the promotion checklist and coverage gate are written *before* the build; the
  §4 caveat exists because every field that can fool itself converges on blind
  analysis — a stub tested as-if-live is cargo-cult science.
- *principal-mindset §3 (proxy-trap audit):* the harness pass rate is the proxy;
  the true goal is "stubs match the vendor." The proxy is distrusted by design —
  the drift harness is the check on the check (testing-qa.md P2-1's "verifies the
  doubles aren't lying").
- *principal-systems, eternal friction:* vendors change response shapes without
  warning; the design assumes PD and Jev *will* drift and prices the detection,
  not the hope.
- *principal-systems, software constitution (fail on timeline, not technical
  ambition):* the Q6-gated fallback (quarterly manual check) ships on schedule
  even if the live harness waits on Aditya — a thinner true mechanism beats a
  delayed perfect one.
- *execution-doctrine §3 (Learn→Measure→Build) + §6 (validate→shadow→canary):*
  the drift harness is the *measure* step for the fake batteries; manual-first
  governs the fallback.

**Grounding tools run:** `git ls-remote origin program/architecture-revision`
(→ `f5469f2`); worktree file inventory — `tests/doubles/` absent, `FakePD` at
`tests/test_durable_forwarder.py:136`, `PD_RETRY_TABLE` at
`src/sentinel/pd_sender.py:52`, drill stubs at `ops/drills/stub_pd.py`,
`ops/drills/stub_jev529.py`.

---
*Pre-T+0 prep artifact. Nothing here touches `src/`, tests, or the live gate.
Expected reviewers: Forge (drift design), Vault (key-handling surface of §5).*
