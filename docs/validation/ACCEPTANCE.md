# TRACK 7 — ACCEPTANCE CRITERIA

**Owner:** Track 7 (Validation + Full-Scale Testing)
**Branch:** `lane/build-t7-validation` → merges to `program/full-build`
**Date:** 2026-10-06
**Law:** Nothing merges to `main` without Track 7's sign-off on `program/full-build`.
Production shift happens only after Track 7 is green.

## How this document is used

Every track (T1–T6, T8) builds against the inter-track contracts in
`docs/planning/PROGRAM-CONTRACTS.md` (C1–C7). This document is the
falsifiable bar those contracts must clear. Every criterion is
**measured, not asserted**: the harness in `tests/validation/`
re-derives every number from artifacts (decision log, event log,
server responses, UI bytes). A criterion that cannot be executed is
**BLOCKED** (named owner, not a pass). A criterion that fails is **FAIL**
— never dressed up.

### Verdict vocabulary

| Verdict | Meaning |
|---|---|
| **PASS** | All sub-checks executed, all thresholds met, evidence on disk. |
| **FAIL** | At least one sub-check executed and missed its threshold. |
| **BLOCKED** | A dependency (named track/merge) is not yet on the branch. Red until green. |
| **CANNOT-VERIFY** | Cannot be executed in this environment (stated, with the exact limitation). Red until resolved by an eligible parent. |

"Baseline exists", "measured before", "was green on another branch" are
not PASS. PASS is always on `program/full-build @ <sha>` with fresh evidence.

---

## AC-1 — Kill-switch drill: measured, sticky, attributed

**Contract:** C3. **Depends on:** Track 3 (kill endpoint, `mode` field, drill artifact).

**Procedure** (run against the real gate + real event log — a drill on any
other path is theater, per the engine domain research):

1. Warm-up: feed a suppressible stream (alerts the gate genuinely
   suppresses — triple-lock holding, corroboration passing, policy fresh).
   Confirm ≥ 10 consecutive `suppress` decisions in the log.
2. Flip: `POST /api/v1/safety/kill` (C1-authed). Record `t_flip` from the
   flip's audit-log timestamp (actor + timestamp required on the row).
3. Continue feeding the same suppressible stream.
4. Find the first `decision_made` AFTER `t_flip` with a live-mode record
   whose disposition is NOT `suppress`. `t_halt` = its event timestamp
   (event-log time, never wall-clock arithmetic on the tester).
5. **Measured propagation latency = `t_halt − t_flip`.**

**PASS requires ALL of:**

| # | Check | Threshold |
|---|---|---|
| 1a | Measured flip→halt latency | **< 5000 ms** (FAIL ≥ 5000 ms) |
| 1b | Drill artifact | `scripts/kill_drill.py` (Track 3's) runs and prints the measured ms; artifact copy in `docs/drills/` |
| 1c | Post-flip dispositions | Zero `suppress` rows after `t_halt`; every post-flip row names the kill causally in `reason` (e.g. `kill_switch`…) and carries `mode: "live"` |
| 1d | Stickiness | ≥ 20 further suppressible alerts after the flip: **zero** suppressions. A second `POST /api/v1/safety/kill` does NOT disarm (no self-revive) |
| 1e | Separate re-arm | `POST /api/v1/safety/rearm` + explicit confirmation restores suppression; re-arm is a SEPARATE deliberate action, audit-logged with actor + timestamp |
| 1f | Attribution survives shadow | Where a `shadow_decision` exists near the kill, the live row's `reason` still reads `kill_switch` — shadow never rewrites it (see AC-6) |
| 1g | Control principle | No Jev/advisory signal can flip, delay, or reinterpret the kill (code-path inspection: the kill check sits above the race, before any Jev call) |

**FAIL** if any of 1a–1g fails. **BLOCKED** until Track 3's kill endpoint
lands on `program/full-build`.

---

## AC-2 — Race outcomes: all six exercised, correct disposition + correct `source`

**Contract:** C2. **Depends on:** Track 2 (judge adapter, `JudgeResult`,
spend endpoint). Race mechanics (`src/sentinel/race.py`) exist on-branch.

Each case drives the real race (`RaceRunner.run`) and asserts the
disposition AND the `source` field. `source` vocabulary (C2):
`"jev" | "timer" | "deterministic"`.

| # | Case | Setup | Expected disposition | Expected `source` |
|---|---|---|---|---|
| 2a | **judge-wins** | FakeJev answers in 10 ms, `suppress` confidently, triple-lock + corroboration holding | `suppress` (or policy's true call) | `jev` (budget_outcome `answered_in_time`) |
| 2b | **timer-wins** | Forced short timeout: budget = 500 ms (config floor), FakeJev latency 1500 ms | `passthrough` | `timer` (budget_outcome `timer_won`; `timer_fired_at_ms` ≈ 500 ms) |
| 2c | **Jev-error** | Client raises `JevTimeout`/`JevOverloaded` on every call | `passthrough`, `reason: error:<code>` | `deterministic` (`error_passthrough`) |
| 2d | **Jev-overload** | Inference pool queue full (submit returns None) | `passthrough` | `timer` (`timer_won_shed`, `shed: true`) |
| 2e | **key-absent** | No Jev key → FakeJev (Track 2) | policy's true call | `jev` ACCEPTED **iff** the fake is honestly labeled (`model_version` identifies the fake, e.g. `fakejev-*`); `deterministic` also accepted with the same labeling. **FAIL if the fake is ever presented as a real model or accrues spend** |
| 2f | **budget-exhausted** | `--jev-budget-usd` spent to 0; `GET /api/v1/jev/spend` → `blocked: true` | fail-open disposition (page/passthrough — never suppress on the degraded path) | `deterministic`; zero Jev calls attempted after block |

**PASS requires ALL of:**

- Every case above executes and matches its expected (disposition,
  budget_outcome, source) triple. Any mismatch = FAIL.
- Timer-win honesty: on 2b the decision record's `timer_fired_at_ms` is
  measured (≈ budget), and any user-facing copy says the page happened
  BECAUSE the judge was too slow (C2 race honesty).
- Late answers (2b's detached 1500 ms call): become `shadow_decision`
  ONLY — they never page, never suppress, never re-open the decision
  (checked in AC-6).
- The gate's own backstop (B + ε): killing the scheduler thread must not
  hang a decision past B + ε + 500 ms slack (triple-death shape).

**BLOCKED** until Track 2's judge adapter + spend endpoint land (2e, 2f
and the `source` field require it); 2a–2d, backstop are executable now
against `race.py` + gate wiring.

---

## AC-3 — Suppression correctness: independently recomputed

**Contract:** gate records (C3), ADR-019/D5, ADR-023/D9. **Depends on:**
nothing — executable on current code.

Track 7 is the auditor: for each sampled suppression, recompute the
decision from the record's own evidence. The UI never recomputes — Track 7
does.

**Sample:** `N = min(50, all suppressions)` from the event log, newest
first, across at least 3 distinct fingerprints where available.

**For EACH sampled row, PASS requires ALL of:**

| # | Check | Rule |
|---|---|---|
| 3a | Conjunction holds | `verdict.action == "suppress"` from the policy kernel AND D8 allowed AND `corroboration.passed == true` AND no `freshness:<…>` veto in `reason` — recomputed from the row's stored `lock_evaluation`, `corroboration`, `freshness` bodies |
| 3b | Corroboration named | `corroboration.kind` is a named witness (not null, not "none"); fail-closed path (any leg error → page) verified on one synthetic error-injected case |
| 3c | Counterfactual receipt | `threshold_counterfactual` present and non-null (ADR-023: live suppress path only); recomputing the receipt from the row's recorded inputs reproduces the stored receipt |
| 3d | Causal reason | `reason` is causal (`allowlist`, `dedup`, `flap_debounce`, `storm`, `kill_switch`, `duplicate`, …) — never `"shadow"` (see AC-6) |
| 3e | Confidence ordinal | `q3_confidence` meets `suppress_conf_min` (0.90 default) per the active thresholds; confidence is never presented as a probability anywhere downstream (see AC-8) |
| 3f | Hash well-formed | `input_sha256` is 64 hex chars; on harness-driven (live) rows, recomputing `input_sha256(state)` matches the row exactly |

**FAIL** if ANY sampled row misses any check. **Additionally FAIL the
whole criterion** if the harness cannot drive at least 10 real
suppressions through the gate (coverage floor) — a thin sample is not an
audit.

---

## AC-4 — Scale test: ≥ 2,000 problems through the real pipeline

**Contract:** C6 (Track 6 scenarios drive the REAL pipeline:
receiver → correlator → race → gate → forwarder; pages sink to FakePD).
**Depends on:** Track 6 (scenario manifests under
`platform/server/scenarios/`, referenced by name).

**Load:** ≥ 2,000 problems, mixing Track 6's named profiles
(`normal-day`, `bad-deploy`, `infra-incident`, `storm-surge` — at least
one storm profile at storm rate). FakeJev answers in 1–5 ms (scale
measures PIPELINE throughput, not vendor latency; vendor tails are AC-2's
job).

**Latency budgets** (measured on local CPU, per-stage, p99 unless noted):

| Stage | What is timed | PASS (p99) | FAIL |
|---|---|---|---|
| receiver | ingest → validated alert | ≤ 100 ms | ≥ 500 ms |
| correlator | fingerprint + dedup/window | ≤ 200 ms | ≥ 1000 ms |
| race | race arm → claimed disposition | ≤ B + ε + 500 ms (bounded by construction; B ∈ [500, 5000], ε = 500) | any decision past B + ε + 500 ms |
| gate kernel | post-race decide (excludes race) | ≤ 500 ms | ≥ 2000 ms |
| forwarder | outbox claim → FakePD 202 | ≤ 1000 ms | ≥ 5000 ms |
| end-to-end | receiver → `decision_made` (answered path) | ≤ B + 3000 ms | ≥ B + 10000 ms |

**PASS requires ALL of:**

| # | Check | Threshold |
|---|---|---|
| 4a | Volume | ≥ 2,000 problems ingested and decided |
| 4b | No silent loss | Every ingested alert has a `decision_made`; event-log `seq` contiguous (no lost writes); sheds appear ONLY as `timer_won_shed` |
| 4c | Stage budgets | All six budgets above met (measured, from stage-boundary timestamps) |
| 4d | Storm profile | `storm-surge` (or Track 6's named storm) keeps up: forwarder never more than 60 s behind the receiver at storm rate |
| 4e | UI no-collapse | With the storm volume in the log, the shipped console (Track 8) renders: queue ≤ 80 riskiest + explicit risk-line summary; ledger ≤ 100 least-sure + "N more below the line"; no pagination-as-navigation; no unbounded DOM growth (verified via console's headless harness or measured DOM counts) |

**FAIL** on any missed budget or any silent drop. **BLOCKED** until Track
6's scenario manifests land (4d, and the by-name scenario references);
4a–4c, 4e-mechanism are drivable with harness-generated profiles meanwhile.

---

## AC-5 — P0 re-walk: Track 1's attack chain against final code

**Contract:** C1. **Depends on:** Track 1 (operator auth on `/api/*`,
CORS allowlist default).

Re-execute the exact attack chain from the infra domain research against
the FINAL merged code:

| # | Attack | Expected (fail-closed) |
|---|---|---|
| 5a | `POST /api/v1/integrations/keys` — no `Authorization` header | **401** + body `{"error": "unauthorized"}`; key store unchanged |
| 5b | Same, with a bogus bearer token | **401**; key store unchanged |
| 5c | Same, `Origin: https://evil.example`, with a VALID token | No `Access-Control-Allow-Origin` granting the evil origin (blocked) |
| 5d | `DELETE /api/v1/integrations/keys/pagerduty_routing_key` — no auth | **401**; key store unchanged |
| 5e | `POST /api/simulate`, `GET /api/v1/integrations/status`, `GET /api/stream` — no auth | **401** (none are exempt) |
| 5f | `GET /api/v1/health/live`, `GET /api/v1/health/ready` — no auth | **200** (the ONLY exemptions, per C1) |
| 5g | Authenticated `POST /api/v1/integrations/keys` with valid operator token | **200**, key saved (functionality intact) |

**PASS:** all of 5a–5g. **FAIL:** any deviation (a 200 on 5a–5e is a P0
regression — automatic sign-off block). **BLOCKED** until Track 1 merges.

---

## AC-6 — Shadow audit: the shadow never touches the decision record

**Contract:** C3. **Depends on:** Track 3 (`mode` field, write-once `reason`).

**Sample:** `min(50, all)` `shadow_decision` events, newest first.

**For EACH sampled row, PASS requires ALL of:**

| # | Check | Rule |
|---|---|---|
| 6a | Mode correct | `body.mode == "shadow"` on the shadow row; the corresponding live `decision_made` for the same episode has `mode == "live"` |
| 6b | Reason never rewritten | shadow row's `reason` is the CAUSAL reason (`kill_switch`, `duplicate`, `flap_debounce`, `timer_won_late`, …) — **zero** rows with `reason == "shadow"` or any mode-derived rewrite |
| 6c | Shadow is powerless | shadow rows carry no `outbox_id`; no `forward_confirmed` exists for a shadow episode; the late answer never paged, never suppressed, never re-opened the decision |
| 6d | Kill attribution intact | on any episode touching a kill flip, the live row's `reason` still names `kill_switch` (shadow proximity must not dilute it) |

**FAIL** on ANY violation — a single rewritten reason is an architecture
violation (separate write paths, per the engine research), not a
statistical miss. **BLOCKED** until Track 3's `mode` field lands.

---

## AC-7 — The 9 falsifiers, re-run against the shipped console

**Applies to:** Track 8's final console artifact on `program/full-build`
(whichever pass Aditya judges in). **Depends on:** Track 8.

Scored against the falsifiers as stated in the handover + round-2 brief
(`docs/planning/research/UI-V2-NOTES.md` §6 — the definitions below are
verbatim from that judgment):

| # | Falsifier | PASS bar |
|---|---|---|
| 7.1 | **3 AM test** (10-second comprehension) | The state line answers "what needs me" in one sentence; SEV1 rows are bigger, bolder, first — verified against the rendered artifact |
| 7.2 | **Scale test** (hundreds of open problems, no infinite list) | Queue renders the top-N riskiest + explicit risk-line summary; ledger capped + "N more below the risk line"; no pagination-as-navigation (driven at ≥ 400 problems headless) |
| 7.3 | **Honesty test** | SIMULATED band in-band on every surface showing sim data; every header number traceable; seed stated; confidence shown as ordinal score ± band with a "ranks decisions, not a probability" caption; no fabricated drills ("no flip recorded this session" until a real flip); every control live or absent |
| 7.4 | **AI-generic recognition** | No gradients, no glassmorphism, no purple/blue glow, no emoji iconography, no hero layout, no dark-mode-everything (static scan of shipped CSS/HTML + rendered spot-check) |
| 7.5 | **Hierarchy without color** | Severity = position + size + weight + edge bar; in grayscale (computed, via luminance collapse) SEV1 rows are still unmistakably first |
| 7.6 | **No process theater** | No falsifier buttons, no research claims, no compliance-display widgets in the operator UI |
| 7.7 | **Dynamics, not coverage** | Nothing hardcoded to a fixed problem count; storm builds; timer wins occur; suppressions expire; queue reorders (driven headless over ≥ 400 ticks) |
| 7.8 | **Appeal/audit integrity** | Appeal marks the ledger row and reopens the problem; the row is never filtered out, never rewritten; the "frozen" decision snapshot is never mutated (post-decision events live in a separate log) |
| 7.9 | **Phone test** (3 AM, on a phone, 60 seconds) | Single-column ≤ 640 px layout, queue first, detail as full-screen drawer, tap targets ≥ 32 px — **rendered on a real phone viewport**. Reasoned-not-rendered is CANNOT-VERIFY, not PASS |

**PASS:** all nine PASS. **FAIL:** any FAIL. **CANNOT-VERIFY** stays red:
7.9 cannot be verified in this environment (no live browser / no real
phone) — it requires an eligible parent to render the artifact on a real
device or a real phone viewport. Track 7 will not upgrade the earlier
"weak pass" on reasoning alone.

---

## AC-8 — Honesty audit: no fake-real numbers, no unmeasured claims

**Applies to:** all user-facing copy — console strings, API messages,
evaluator/harness labels, docs shown to operators. **Depends on:** Track 8
(console copy); API copy checkable now.

**PASS requires ALL of:**

| # | Check | Rule |
|---|---|---|
| 8a | SIMULATED labels | Every surface showing simulated/synthetic data carries an in-band SIMULATED label (not a footnote, not a tooltip) |
| 8b | No fake-real numbers | No fabricated measurements in copy (e.g. no "halted in X ms" without a drill artifact in `docs/drills/`; no invented latency figures) |
| 8c | Confidence is ordinal | Confidence is never presented as a probability (no "%", no "P="); wherever shown as a score it carries the "ranks decisions, not a probability" caption or equivalent |
| 8d | No unmeasured claims | Every latency/scale claim in user-facing copy traces to a measured artifact (drill log, scale report, race log) committed in the repo |
| 8e | Key honesty | The Jev key is never in logs, responses, SSE, or the browser (grep-verified); spend meter visible where Jev is used; budget-exhausted state honestly labeled ("deterministic mode") |

**FAIL** on any violation. 8e's key-leak grep runs on every sign-off
attempt.

---

## Sign-off

Track 7's sign-off is a sworn statement, issued only when every criterion
is green on the merged program branch:

> **Track 7 sign-off: <date> — all 8 criteria PASS on program/full-build @
> <sha>. Evidence: <paths>.**

Rules:

1. The statement names the exact `program/full-build` SHA the evidence
   was produced against. A merge after the run voids the sign-off.
2. Evidence paths point at the harness's sign-off report
   (`docs/validation/evidence/<date>-<sha>/`) — per-criterion verdict,
   measured numbers, and the artifacts they were read from.
3. If ANY criterion is FAIL, BLOCKED, or CANNOT-VERIFY, Track 7 does not
   sign off. The report says so plainly, names the red criterion, the
   owning track, and what would turn it green. A red criterion is never
   dressed up.
4. Partial runs are reported as partial: "AC-3, AC-4 (partial), AC-8
   executed; AC-1, AC-5, AC-6 BLOCKED on T3/T1; AC-2, AC-4, AC-7 pending
   T2/T6/T8." Never "mostly green".
5. The harness is deterministic and re-runnable: `python3 -m unittest
   discover -s tests/validation` + `python3 tests/validation/run.py
   --report` reproduces the sign-off report locally, stdlib only.

## Current status (2026-10-06, program/full-build @ aee5f33)

- AC-1: BLOCKED (Track 3)
- AC-2: PARTIAL — 2a–2d + backstop executable against `race.py`; 2e, 2f, `source` field BLOCKED (Track 2)
- AC-3: EXECUTABLE (baseline run in progress)
- AC-4: PARTIAL — drivable with harness profiles; by-name Track 6 scenarios BLOCKED (Track 6); 4e needs Track 8
- AC-5: BLOCKED (Track 1)
- AC-6: BLOCKED (Track 3)
- AC-7: DEFERRED (Track 8)
- AC-8: EXECUTABLE (API copy now; console copy when Track 8 lands)
