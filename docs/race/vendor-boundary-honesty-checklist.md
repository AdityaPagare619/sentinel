# Vendor-Boundary Honesty Checklist — R-13 Coverage Gate (seed)

**Lane:** race-2 · **Branch:** `lane/12h-race-2-vendor` · **Checkpoint:** T+3 (seed list)
**Purpose:** enumerate PagerDuty Events API v2 *documented* behaviors and state,
for each, whether the FakePD/test battery covers it — per the Q6 split decision
(PagerDuty stays FakePD-only; no key exists), this list is how the FakePD battery
stays honest about what it does **not** cover. It is consumed by the quarterly
manual live-shape check and by the R-13 drift-harness design (race-1).

## Skill & Evidence (OPERATING-RULES §2.2)

- **principal-mindset proxy-trap audit (§3):** the proxy is "FakePD battery passes";
  the true goal is "the forwarder behaves correctly against real PagerDuty."
  A proxy that moves while the truth doesn't is tuning theater — every row below
  names whether the proxy can see that slice of truth at all.
- **principal-governance §1 (written decisions before code):** this checklist is
  the written record the R-13 drift design consumes; FakePD's modeled behaviors
  are an asserted subset of the docs, not the whole contract.
- **execution-doctrine (honesty before polish) + honesty law:** an uncovered
  behavior is a finding, not an omission. Nothing below is claimed as covered
  without a named test.

## Method (Aditya's web-research mandate)

Web is the core method: every behavior is cited to PagerDuty's official
developer docs, fetched 2026-10-05, before comparing against the repo.

- **D1:** PagerDuty developer docs, Events API v2 Overview
  (`docs/events-API-v2/01-Overview.md` —
  https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/01-Overview.md,
  fetched 2026-10-05) — response codes & retry table, rate limits, 512 KB size
  limit, PD-CEF field table, async-intake semantics.
- **D2:** PagerDuty developer docs, Send an Alert Event
  (`docs/events-API-v2/02-Trigger-Events.md` —
  https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/02-Trigger-Events.md,
  fetched 2026-10-05) — dedup_key (max 255), Alert De-Duplication section,
  Event Action Behavior table, summary max 1024, same-routing_key rule,
  auto-generated dedup_key echo in the response.

Coverage verdicts were checked read-only against the repo: `src/sentinel/pd_sender.py`,
`tests/test_durable_forwarder.py` (FakePD + all `test_*` methods), `tests/test_forwarder.py`,
`ops/drills/stub_pd.py`.

### Pre-mortem (principal-systems §2 — one year out, this checklist failed)

It failed because: (a) a PD doc change (retry table or a new documented limit)
shipped silently and the quarterly check read the checklist instead of re-fetching
the docs; (b) "uncovered" rows were treated as accepted gaps with no dated
re-examination; (c) FakePD was extended to model more behaviors and nobody
re-verified the extension against D1/D2. Mitigations: each row carries its
doc-citation hash-date (2026-10-05); T+6 re-fetches the docs; every FakePD
extension must cite the doc row it models (row references B-xx/U-xx).

---

## Covered behaviors (the gate's floor — documented here so the gate is complete)

| ID | Documented behavior | Citation | Coverage |
|---|---|---|---|
| B-01 | Retry table: 202→no retry; 400→no retry ("check the JSON"); 429→retry "after some time"; 500/other 5XX→retry "after some time"; network error→retry. | D1 §Response Codes & Retry Logic | **TESTED** — `test_classifier_matches_documented_table_row_for_row` asserts `PD_RETRY_TABLE` row-for-row against `classify()`. A PD doc change surfaces as a build break by design. |
| B-02 | A 202 whose body does not echo `status == "success"` is not acceptance. | D1 (202 = "Accepted — the event has been accepted by PagerDuty"); sender design §4.2 | **TESTED** — `test_202_without_success_is_vendor_contract_violation`: retryable + loud, never "accepted". |
| B-03 | Trigger on an open alert with the same `dedup_key` → "a trigger log entry is created on an existing alert" — no new incident, no fresh escalation. | D2 §Alert De-Duplication + §Event Action Behavior (trigger row) | **TESTED** — FakePD `_default_response` implements it; `test_retry_then_success_reuses_dedup_key` (Case 1) asserts appended-to-open-alert. |
| B-04 | Once resolved, a trigger with the same `dedup_key` creates a **new** alert; resolve without an open alert creates nothing ("dropped"). | D2 §Alert De-Duplication: "any further events with the same `dedup_key` will create a new alert (for `trigger` events) or be dropped (for `acknowledge` and `resolve` events)" | **TESTED** (trigger path) — `test_crash_retry_after_resolve_is_one_loud_new_alert` (Case 2). Ack/resolve drop: not modeled (see U-09 — product never sends them). |
| B-05 | 429 responses carry the guidance "be sure to retry on a 429 response code"; honoring `Retry-After`. | D1 §Rate Limits; general HTTP convention for `Retry-After` | **TESTED** — `test_429_honors_retry_after_and_floor`: Retry-After honored; 60 s floor without it. (Doc's "few minutes" recommendation vs the 60 s floor: see U-05.) |
| B-06 | 400 = our bug, no retry. Terminal handling + secondary + control-plane page. | D1 ("Bad Request — check the JSON", Retry? No) | **TESTED** — `test_400_is_terminal_secondary_fires_control_plane_pages`. (Which 400s PD actually emits: see U-07.) |

---

## Uncovered behaviors (the findings — this is the gate)

Each entry: what PD documents, the citation, what the battery does/does not do,
and why it matters for the drift harness.

### U-01 — 512 KB payload size limit: documented, never enforced, never tested

- **Documented:** "Events API payloads are limited to **512 KB**" (D1 §Size Limits).
- **Repo state:** `PD_MAX_PAYLOAD_BYTES = 512 * 1024` exists in `pd_sender.py:46`
  but has **zero references anywhere else** — no enforcement in `build_wire_event`,
  no test. An oversized payload reaches the wire and (per B-01) would come back
  400 → terminal → dead letter + secondary + control-plane page, i.e. a loud
  incident for a fully predictable client-side violation.
- **Why it matters:** the drift harness replays realistic payloads; a real
  PD-shaped 512 KB rejection is a behavior the harness claims to emulate but
  cannot produce — FakePD never validates size. The quarterly manual check
  should confirm the current limit and its rejection code; the battery should
  either enforce the cap client-side (fail before the wire) or FakePD-test the
  400 path with a size-shaped fixture.
- **Severity:** high — this is a documented hard limit with a dead constant.

### U-02 — `dedup_key` 255-char limit: guard exists, no test exercises it

- **Documented:** "The maximum permitted length of this property is 255
  characters." (D2 §Parameters).
- **Repo state:** `DedupKeyInvalid` is raised in `build_wire_event` when the key
  is missing or >255 chars — but **no test in `test_durable_forwarder.py` or
  `test_forwarder.py` exercises it** (grep: zero hits for `DedupKeyInvalid`,
  `PD_MAX_DEDUP_KEY_LEN` outside its definition).
- **Why it matters:** the guard is the only client-side protection against a
  guaranteed-400; an untested guard is a wish. The drift battery's dedup-key
  generator must prove it stays within the bound, or a long incident key
  (fingerprint + org + episode concatenation) dies terminal at 3 AM.
- **Severity:** medium — guard exists, evidence missing.

### U-03 — Same `routing_key` required for subsequent events: FakePD ignores `routing_key`

- **Documented:** "Subsequent events for the same `dedup_key` will only apply
  to the open alert if the events are sent via the same `routing_key` as the
  original trigger event. Subsequent acknowledge or resolve events sent via a
  different `routing_key` from the original will be dropped." (D2 §Alert
  De-Duplication).
- **Repo state:** FakePD's `_default_response` keys incidents **only on
  `dedup_key`** — it never reads `routing_key` (the parsed body carries it;
  the handler ignores it). `test_routing_key_pinned_across_rotation` proves the
  *sender* pins the key across a rotation mid-flight, but nothing tests what PD
  does when a rotated key crosses an open incident — FakePD cannot express the
  vendor-side drop at all.
- **Why it matters:** the 12h wave ships the R-3 HMAC migration design and the
  R-2 key-resolution build — key rotation is a live operational event. If a
  rotation lands between trigger and retry, real PD may treat the retry as a
  foreign event while FakePD cheerfully appends it. The drift harness cannot
  see this failure mode; the quarterly check must re-confirm the documented
  drop rule and whether it applies to `trigger` events as well (the doc text
  names acknowledge/resolve explicitly; trigger-on-open-alert under a new key
  is the case the battery needs).
- **Severity:** high for the drift battery — a rotation-adjacent blind spot.

### U-04 — Trigger without `dedup_key`: PD auto-generates a UUID and returns it; FakePD returns literal "auto-uuid"

- **Documented:** "If omitted, it will be generated automatically by PagerDuty
  and returned in the Events API v2 response… A trigger event sent without a
  `dedup_key` will always generate a new alert because the automatically
  generated `dedup_key` will be a unique UUID." (D2 §Alert De-Duplication).
- **Repo state:** Sentinel always pins a `dedup_key` (`build_wire_event` refuses
  an empty one via `DedupKeyInvalid`), so the sender never exercises this path —
  fine. But FakePD's fallback for a missing key is the literal string
  `"auto-uuid"`, and `pd_sender` parses only the `status` field from the 202
  body, never the echoed `dedup_key` (see U-11). If a future lane ever omits
  the key (or PD changes the echo contract), FakePD's response is fiction:
  real PD returns a *unique* UUID per event; FakePD returns a constant.
- **Why it matters:** the R-13 drift harness replays vendor-shaped responses;
  a battery fixture that treats the dedup_key echo as a constant teaches the
  harness the wrong response grammar. Low blast radius today (key always
  pinned), but the quarterly check should verify the echo contract is stable.
- **Severity:** low today; guard-rail for future lanes.

### U-05 — 429 backoff guidance "preferably a backoff of a few minutes" vs the implemented 60 s floor

- **Documented:** "be sure to retry on a 429 response code, preferably with a
  backoff of a few minutes." (D1 §Rate Limits).
- **Repo state:** the mechanism is tested (U: `test_429_honors_retry_after_and_floor`)
  — Retry-After honored verbatim, 60 s floor otherwise. The doc's *recommendation*
  (a few minutes) is stricter than the implemented floor (60 s). This is not a
  contract violation (PD accepts any "after some time"), but the battery cannot
  tell the operator whether a 60 s floor re-triggers throttling under sustained
  429s — the exact storm condition the forwarder exists for.
- **Why it matters:** the drift harness's storm scenarios (d2_storm529,
  FakePD-scripted 429 sequences) should parameterize the floor against the doc
  recommendation, not just assert the mechanism works. The quarterly check
  should re-read this sentence — if PD tightens the language, the floor moves.
- **Severity:** medium — guidance drift, not contract drift.

### U-06 — Throttle limits are dynamically adjusted; the battery is static

- **Documented:** "There is a limit on the number of events that a service can
  accept at any given time. Depending on the behaviour of the incoming traffic
  and how many incidents are being created at once, we dynamically adjust our
  throttle limits." (D1 §Rate Limits). AIOps accounts: up to 10,000 events/min
  per integration key, upon request.
- **Repo state:** FakePD/ScriptedHTTP serves a fixed script; the forwarder's
  429 handling is stateless w.r.t. limit *changes*. Nothing models a limit that
  tightens mid-storm — the exact condition PD documents.
- **Why it matters:** the drift harness is supposed to catch vendor-side shape
  changes; dynamic throttling is the one documented behavior that is *defined*
  to change at runtime. A battery that only ever sees static 429s cannot
  distinguish "our backoff works" from "PD moved the limit and our backoff
  no longer converges." The quarterly manual check must ask: has the dynamic
  adjustment behavior or the AIOps ceiling changed?
- **Severity:** medium — structural blind spot, hard to fake without live traffic.

### U-07 — What actually produces a 400: field validation is untested

- **Documented:** required PD-CEF fields for trigger — `payload.summary`,
  `payload.severity` (enum `info|warning|error|critical`), `payload.source`
  (D1 §PD-CEF table; D2 §Parameters: routing_key 32 chars, required).
- **Repo state:** the *response* to a 400 is tested (B-06) via scripted 400s.
  But FakePD never validates fields — it 202s anything. There is no test
  proving the sender's wire bytes satisfy PD's field rules: `build_wire_event`
  truncates `summary` to 1024 **only on the control-plane wrap path**; frozen
  PD-CEF payloads pass `summary` through unvalidated; `severity` is never
  checked against the enum; `routing_key` length is never checked (see U-10).
  A malformed frozen payload's first 400 would arrive from real PD, not the
  battery.
- **Why it matters:** B-06's "terminal = our bug" classification assumes our
  bugs are the only 400 source — but the battery never proves our wire bytes
  are 400-proof. A FakePD "strict mode" (validate PD-CEF fields, emit 400s
  like the docs describe) is the honest fix; until then the quarterly check
  must re-confirm the required-field list and the severity enum.
- **Severity:** medium-high — the 400 path is tested, the 400 *causes* are not.

### U-08 — `payload.summary` max 1024: truncation only on one path

- **Documented:** "The maximum permitted length of this property is 1024
  characters." (D2 §Parameters).
- **Repo state:** `str(summary)[:1024]` exists only in the control-plane wrap
  branch of `build_wire_event` (`pd_sender.py:203`). Frozen PD-CEF payloads
  carry their summary straight to the wire. If PD truncates or 400s overlong
  summaries (the docs state the limit, not the enforcement), the battery can't
  say which.
- **Why it matters:** same class as U-01/U-07 — a documented numeric limit the
  sender treats as advisory. The drift harness should include an overlong-
  summary probe against the quarterly check's confirmed enforcement behavior.
- **Severity:** medium.

### U-09 — Acknowledge/resolve event actions: documented, never sent, never modeled

- **Documented:** acknowledge → "the incident referenced with the `dedup_key`
  will enter the acknowledged state. While an incident is acknowledged, it won't
  generate any additional notifications, even if it receives new trigger
  events." Resolve → resolved state; "new trigger events with the same
  `dedup_key`… won't re-open the incident. Instead, a new incident will be
  created." (D2 §Event Action Behavior).
- **Repo state:** Sentinel sends **only `trigger`** events. FakePD has no
  acknowledged state and never receives ack/resolve. The trigger-after-resolve
  → new-incident half is tested (B-04); the acknowledge-suppression half is
  unreachable by construction.
- **Why it matters:** if a future lane ever sends acknowledge (e.g. the Q8
  appeal-override path or an ops "we're on it" signal), the entire behavior —
  including "no notifications while acknowledged" — is untested against
  anything. The checklist records this now so the capability isn't added later
  under the illusion that the battery covers the vendor contract.
- **Severity:** low today (unused), flagged for future lanes.

### U-10 — `routing_key` is a 32-character integration key: never validated

- **Documented:** "This is the 32 character Integration Key for an integration
  on a service or on a global ruleset." (D2 §Parameters; required).
- **Repo state:** `resolve_routing_key` maps refs to env values with no length
  or charset check; FakePD accepts anything. A misconfigured key (wrong env
  var, truncated secret) produces a real-PD 400/invalid-key rejection the
  battery can never emit.
- **Why it matters:** key misconfiguration is the most common integration
  failure in the field and the battery is blind to it. Cheap fix: validate at
  resolution time (fail fast, loud) or a FakePD strict-mode 400. The quarterly
  check confirms the 32-char contract still holds.
- **Severity:** medium — operational, not storm-related.

### U-11 — The 202 body echoes `dedup_key`: FakePD echoes it, nothing consumes it

- **Documented:** the auto-generated dedup_key is "returned in the Events API
  v2 response" (D2); the success envelope shape
  `{"status":"success","message":"Event processed","dedup_key":"..."}` is the
  widely documented contract.
- **Repo state:** FakePD echoes `dedup_key` in its 202 body (good fidelity),
  but `pd_sender._post` parses only `status` — the echoed key is discarded.
  Correct today (we always pin), but the response-grammar contract is
  unasserted: if PD ever changes the envelope, `test_classifier_matches_documented_table_row_for_row`
  still passes while the intake receipt silently changes shape.
- **Why it matters:** the drift harness's job is detecting vendor-shape drift;
  the envelope is part of the shape. A one-line assertion on the echoed key in
  the happy-path test closes this for free.
- **Severity:** low.

### U-12 — Everything past the 202 boundary: alert → incident → notification

- **Documented:** "Alert events create incidents on a service in PagerDuty.
  The incident will be assigned to the person on-call. This will generate a
  notification (phone call, SMS, email, or mobile push notification depending
  on the on-call responder's preferences)." (D1 §How Events Are Used). The
  Events API is explicitly **asynchronous**.
- **Repo state:** the repo's HONESTY CONTRACT (`pd_sender.py` module docstring)
  states it correctly: `forward_confirmed` = "accepted by the vendor's durable
  intake", NOT "the human's phone rang". FakePD — and any fake — stops at 202.
- **Why it matters:** this is the ultimate proxy-trap boundary. No FakePD
  extension can ever cover "did the human get paged" without a live vendor;
  per the Q6 decision there is no live vendor. The drift harness must never
  claim coverage past 202, and the quarterly manual check is the only
  instrument that can even look at this layer (via the docs, not via calls).
  Any future claim of end-to-end paging proof from the battery is a §5.3
  honesty violation by definition.
- **Severity:** structural — the permanent, declared limit of the battery.

### U-13 — Change events: separate event type, no notifications (out of scope, recorded)

- **Documented:** Change events are a distinct Events API v2 type (D1 §Event
  Types) that provide responder context with **no notifications**.
- **Repo state:** Sentinel never sends change events (different endpoint,
  `/v2/change/enqueue`).
- **Why it matters:** recorded so the quarterly check explicitly confirms
  "still out of scope" rather than discovering later that an incident-context
  lane assumed trigger semantics for change events.
- **Severity:** informational.

---

## Assumptions the code makes that the docs do not state (for the quarterly check to confirm or kill)

- `Retry-After` is parsed **only on 429** (`pd_sender.py`: `parse_retry_after`
  gated by `status == 429`). D1 says 5xx means "retry after some time" but does
  not promise a `Retry-After` header there. Undocumented either way — the
  quarterly check should note whether PD emits `Retry-After` on 503s.
- Any non-202/400/429/5xx 4xx → terminal ("retrying the identical bytes is
  futile"). D1's table doesn't enumerate other 4xx codes; the code's reading
  is reasonable but asserted, not cited.
- Scripted FakePD entries **bypass** incident tracking ("use them for failure
  injection — 500/400/429 create nothing, as in reality"). Real PD: 4xx/5xx
  create nothing — true per the docs' async model — but the *scripted bypass*
  is a test-double choice, not a doc fact; a scripted 202 *does* bypass dedup
  tracking while a real 202 would not. Noted for FakePD-extension authors.

## T+6 plan (expansion)

1. Re-fetch D1/D2; diff against the 2026-10-05 snapshots for doc drift.
2. Add the PD support knowledge-base article on alert/incident interaction
   (D2 links it) and the severity→urgency mapping to the citation set.
3. Draft the FakePD "strict mode" proposal (field validation → doc-shaped 400s)
   as the fix candidate for U-01/U-07/U-08/U-10 — design doc, not code.
4. Feed U-03/U-12 into race-1's R-13 drift-harness design as explicit
   non-goals with the quarterly-check procedure attached.

## T+9 plan (review) / T+12 plan (handoff)

- T+9: Forge review of the expanded list; every U-row carries a dated
  re-examination note (accept / FakePD-extend / quarterly-check).
- T+12: hand the frozen checklist to the Phase 4 R-13 lane as the coverage
  gate's input; the quarterly manual live-shape check procedure (steps, owner,
  evidence format) is attached as an appendix.

---
*Seed list closed 2026-10-05 ~20:35 IST · race-2 · honesty law: uncovered is a finding.*
