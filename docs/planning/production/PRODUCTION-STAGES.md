# PRODUCTION STAGES — full production plan

**Lane:** production-stages · **Branch:** `lane/production-stages` (from `program/full-build` @ `22cc12b`)
**Date:** 2026-10-06 · **Status:** PREPARED — NOT SHIFTED. The production shift
happens only on Aditya's explicit word, after Track 8 (console integration)
and his UI verdict.

## 0. What this lane found and fixed

**Found (blocking):** `platform/server/__main__.py` on `program/full-build`
shipped with an **unresolved merge conflict** (Track 1's `operator_token_store`
vs Track 3's `kill_switch`/`drill_dir`) — a `SyntaxError` on import. T7's
"all backend criteria green" did not catch it: the platform server entrypoint
has no import test. **Fixed on this branch** by keeping both sides
(`PlatformApp` accepts all three kwargs — verified). Lesson recorded in the
readiness checklist: every merge to the program branch gets a
`py_compile`-all + entrypoint-import gate.

**Built (this lane):**
- `FaithfulJev` (`src/sentinel/client.py`): scripted dispositions, REAL
  measured latencies (109-call empirical distribution), honest fault
  injection (520 @ measured 0.7%, opt-in 429/timeout/slow-tail), seeded.
- Faithful `FakePDSink` (`platform/server/sim/sim_runner.py`): ACK latency,
  RECEIVED→ACCEPTED→QUEUED→DELIVERED/FAILED state machine, PD dedup
  semantics, fault injection (429/500/delay/drop). Default stays instant
  for unit tests.
- `--judge faithful`: one flag = faithful judge + faithful delivery, zero
  spend, seeded, reproducible.
- `docs/planning/production/CALIBRATION.md`: what is measured vs modeled.

---

## 1. Simulated services — design

Aditya's law: fakes are simulated SERVICES, not value scripts. The three
judge modes:

| mode | dispositions | timing | faults | spend | use for |
|---|---|---|---|---|---|
| `fake` | scripted | zero (instant) | none | 0 | unit tests, CI determinism |
| `faithful` | scripted | **measured** (p50 816ms) | measured + opt-in | 0 | professional sim, race/timer drills |
| `real` | **live Jev** | live | live | metered, capped | judgment-semantics validation |

Honesty boundary (non-negotiable): `faithful` is faithful about TIMING and
FAULTS, never about judgment semantics. The 2026-10-06 live run proved
scripted dispositions diverge from the real judge (`noise_suppressed`
assertion failed against live Jev). Suppression-correctness claims require
`--judge real`.

## 2. Console parity — one console, two honest modes

The winning console v2 (Aditya judging our pass vs Sonnet round two) becomes
BOTH the simulated showcase and the production console. Design:

- **One codebase**, one `DataAdapter` interface (already the v2 contract:
  `getPulse/getQueue/getLedger/getSafety/...` reads; `ack/appeal/engageKill/...`
  writes).
- **Two adapters:** `local-sim` (seeded pipeline) and `production`
  (live backend over `/api/*` with the operator bearer token).
- **Environment is a first-class, honestly-labeled mode:** the mode banner
  is structural, not cosmetic — SIMULATED (violet, "nothing here pages
  anyone", seed shown) vs PRODUCTION (distinct, key-source shown, live
  paging armed). The mode is in the DOM, in the URL, and in every export.
- **What differs by mode:** data source, paging path (FakePD vs real PD),
  spend meter (sim) vs cost attribution (prod), drill affordances (sim).
  **What never differs:** layout, information architecture, safety
  surfaces, the trace-number system. A skill learned in sim transfers to
  prod 1:1.
- **No divergence rule:** any UI change lands in the shared shell; mode
  differences live ONLY in the adapter + the mode banner. CI asserts the
  banner contract (both modes render their banner; sim never renders the
  prod banner).

## 3. Merge plan: program/full-build → main

**Preconditions (all must hold):**
1. Track 8 merged: winning console v2 integrated via the adapter seam;
   UI falsifiers (AC-7) re-run and green, including a real-phone check.
2. This lane's `lane/production-stages` merged into `program/full-build`:
   conflict fix, faithful fakes, calibration docs.
3. Full suite green on the program branch + `py_compile`-all +
   entrypoint-import gate (the lesson from §0).
4. Aditya's UI verdict recorded; Aditya's production-shift word given.

**Sequencing:**
1. `lane/production-stages` → `program/full-build` (PR, review: Petu).
2. Aditya's verdict → Track 8 → `program/full-build` (PR, review: Petu + UI lane).
3. `program/full-build` → `main` (PR, review: Petu; Skill & Evidence;
   falsifiers re-run). Single merge commit, message enumerates the 8 tracks.
4. Tag `v1.0-prod-readiness` on main (readiness, NOT release — release is
   the shift).

**Conflict strategy:** program branch is the integration point; main has
only moved for UI fixes since the branch point (verify with
`git log main..program/full-build` before merging). Rebase-free: merge
commits only, never rewrite.

## 4. gh-pages rebuild

| path | content | source |
|---|---|---|
| `/` | production console (winning v2, production adapter) | built from main |
| `/staging/` | simulated showcase (winning v2, sim adapter, SIMULATED banner) | built from main |
| `/preview-v2/` | **removed** at shift (it was the judgment preview; keeping it would fork the UI) | — |

Build is the existing static pipeline; verify each path HTTP 200 + banner
contract after deploy. `/` must show zero fixtures and the production
mode banner; `/staging/` must show the SIMULATED banner with seed.

## 5. Backend production config

**Topology:** self-hosted platform server (`platform/server`) on the
operator's machine; static console on GitHub Pages calls it cross-origin
(CORS allowlist = Pages origin only).

**Provisioning (Aditya performs; Petu never handles raw values):**
1. `SENTINEL_OPERATOR_TOKEN`: generated at first boot, shown ONCE on
   stdout, stored 0600 at `<state-dir>/operator_token.json`. For
   hosted/rebuilt environments: set `SENTINEL_OPERATOR_TOKEN` (or
   `SENTINEL_OPERATOR_TOKEN_FILE`) in the environment before boot.
   Without it, every `/api/*` is 401 — fail-closed by default.
2. `TYPESAFE_API_KEY`: env var for the real Jev judge in prod.
   Cost-capped per the gate's per-call budget; spend metered.
3. **PagerDuty in prod = real, via the user's BYOK key.** The user enters
   their PD routing key in the console (stored per the BYOK resolution
   order: user store → env → unconfigured). FakePD is sim-only; the
   forwarder's `pd_endpoint` defaults to `https://events.pagerduty.com/v2/enqueue`
   in prod. The sim's loopback-only + never-pagerduty.com guards STAY in
   the sim path — prod and sim can never share a forwarder config.

**Startup assertions (fail-closed, loud):**
- operator token store present and 0600, else refuse `/api/*` (401).
- CORS origins != `*` unless explicitly passed (loud warning).
- Jev key present for prod; absent → judge-down mode (timer-wins,
  fail-open), banner shows judge-down.
- Kill-switch state file present and readable; drill record fresh
  (<30d) or warn.

## 6. Production kill-switch drill procedure

1. Engage via console (two-step) or API with operator bearer.
2. `scripts/kill_drill.py` measures flip→forwarder-halt from DECISION-LOG
   timestamps on the REAL path. Bar: <5000ms. Measured 5ms in T7.
3. Verify: pages flow during kill (fail-open), suppressions halt,
   re-arm requires separate deliberate action (sticky).
4. Drill record written to `ops/drills/`; console shows "measured, not
   asserted" with the record link.
5. Cadence: after every deploy, and monthly. A deploy without a green
   drill does not serve production traffic.

## 7. Production runbook

- **Deploy:** merge → tag → provision env → boot with startup assertions
  → kill drill green → smoke (one synthetic alert through the real path,
  FakePD-equivalent sink first, then live) → serve traffic.
- **401-by-default verification:** `curl` without bearer → 401 on every
  `/api/*`; bogus bearer → 401; cross-origin from non-allowlisted origin
  → blocked. (T7 AC-5 re-walked this; re-verify per deploy.)
- **Rotation ceremony:** dual-accept overlap (Track 4 `RotatingKeyStore`);
  new key accepted alongside old → traffic migrates → old revoked.
  Never a flag-day.
- **Break-glass:** dormant dual-controlled credentials; any use fires an
  alarm, forces rotation afterwards, and lands in the audit log.
  Break-glass bypasses the PATH, never the quorum.
- **Degraded:** judge-down → timer-wins → fail-open paging; banner shows
  judge-down; suppressions require fresh evidence or they don't happen.

## 8. Rollback plan

- **Console:** gh-pages is static — rollback = revert the gh-pages commit
  for the affected path (each path deploys independently).
- **Backend:** the platform server is self-hosted — rollback = stop, check
  out `v1.0-prod-readiness` (or prior tag), re-run startup assertions +
  kill drill, restart. State dir (`sentinel-state/`) is never rolled back
  (append-only audit history); only code + config.
- **Kill switch as rollback:** if a bad deploy pages wrongly, engage the
  kill switch FIRST (stops all suppression → everything pages), then roll
  back code. The switch is the fastest mitigation and it is measured.

## 9. Readiness checklist

| # | item | evidence | status |
|---|---|---|---|
| 1 | P0 auth closed | T7 AC-5 8/8; re-walk per deploy | ✅ (re-verify at shift) |
| 2 | Merge conflict in `__main__.py` resolved | this lane; `py_compile` OK | ✅ |
| 3 | Kill drill measured <5s on real path | T7 AC-1 4/4 (5ms) | ✅ (re-run at shift) |
| 4 | Race outcomes exercised | T7 AC-2 8/8 | ✅ |
| 5 | Shadow attribution intact | T7 AC-6 4/4 | ✅ |
| 6 | Faithful sim services built + calibrated | this lane; CALIBRATION.md | ✅ |
| 7 | UI falsifiers (AC-7) | parked → Track 8 | ⏳ needs Aditya's verdict |
| 8 | Live-Jev judgment semantics validated | 2026-10-06 run: 5/5 OK; noise_suppressed FAILED (known divergence) | ⚠️ partial — re-run at shift |
| 9 | Operator token provisioning | runbook §5; Aditya to set on hosted env | ⏳ |
| 10 | TYPESAFE_API_KEY provisioning | Aditya's action | ⏳ |
| 11 | Real PD BYOK in prod | forwarder default; user key flow | ⏳ needs live PD test (Aditya's key) |
| 12 | Rollback rehearsed | runbook §8 | ⏳ rehearse at shift |
| 13 | Phone falsifier | cannot verify without real device | ⚠️ needs Aditya's phone |
| 14 | Ops health endpoint (`GET /api/v1/ops/health`) | specified in CONSOLE-PARITY.md; backend aggregation | ⏳ not yet implemented |
| 15 | Jev circuit breaker (health `circuit` field) | **not implemented** — field must read `not_implemented`, never fake `closed` | ❌ gap — implement or drop the field |
| 16 | Judge observation log (`last_judgment`, connection) | gap — needs client call records | ⏳ |
| 17 | Per-stage pipeline instrumentation (correlator/gate) | receiver/race/forwarder have metrics; correlator/gate need it | ⏳ |
| 18 | Forwarder delivery confirmations in health | forwarder metrics exist; needs API exposure | ⏳ |

**Shift requires:** 7, 9, 10, 11 resolved + Aditya's explicit word.
Items 8 and 13 are the known weak points (see self-judgment).

## 10. Self-judgment — where the production path is weakest

1. **The merge-conflict escape (§0) is the sharpest signal.** T7 validated
   the engine but never imported the platform entrypoint. My fix is
   correct (both kwargs kept; `PlatformApp` accepts both; compiles), but
   the deeper weakness is systemic: **no entrypoint-import gate existed**.
   I add the gate to the checklist, but I did not wire it into CI (no
   GitHub Actions per standing law) — it needs a box-side pre-merge hook,
   which is still TODO.
2. **Live-Jev semantics are still unproven at scale.** One 5-call probe
   + one 858-problem run with a FAILED noise assertion. The faithful
   fake covers timing, not judgment. Production suppression correctness
   against the real judge is the least-measured safety property.
3. **The phone falsifier cannot be verified here.** Aditya's eyes on a
   real device are the only gate.
4. **FakePD timing is modeled, not measured.** If real PD is slower or
   flakier than modeled, forwarder retry behavior in prod will differ
   from sim. Marked honestly; needs real PD timing capture.
5. **The Operations Health surface is specified, not built.** The
   `GET /api/v1/ops/health` endpoint, the circuit breaker, the judge
   observation log, and correlator/gate instrumentation are all gaps —
   named honestly in CONSOLE-PARITY.md's source map. Aditya cannot
   validate the machine on the interface until these exist. This is now
   the critical path to his "inspect the pipeline myself" loop.
