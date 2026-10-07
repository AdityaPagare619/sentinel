# PRODUCTION STAGES — full production plan

**Lane:** production-stages · **Branch:** `lane/production-stages` (from `program/full-build` @ `22cc12b`)
**Date:** 2026-10-06 · **Status:** PREPARED — NOT SHIFTED. The production shift
happens only on Aditya's explicit word, after Track 8 (console integration)
and his UI verdict.

> **Honesty correction (2026-10-07, docs W7).** This doc lives on `main`
> but describes two things at once: (a) the design the lane *proposed*,
> and (b) reality. The lane's engine content — `FaithfulJev`, the faithful
> `FakePDSink`, `--judge faithful`, `CALIBRATION.md` — exists **only on the
> open `lane/production-stages` branch** (`d6273d4`), which is NOT merged
> into `main`. §0 below now says so explicitly; item 6 in the readiness
> checklist is marked lane-only. The backend sections (§§5/8) were
> rewritten to Vercel serverless reality on 2026-10-07.

## 0. What this lane found and fixed

**Found (blocking):** `platform/server/__main__.py` on `program/full-build`
shipped with an **unresolved merge conflict** (Track 1's `operator_token_store`
vs Track 3's `kill_switch`/`drill_dir`) — a `SyntaxError` on import. T7's
"all backend criteria green" did not catch it: the platform server entrypoint
has no import test. **Fixed on this branch** by keeping both sides
(`PlatformApp` accepts all three kwargs — verified). Lesson recorded in the
readiness checklist: every merge to the program branch gets a
`py_compile`-all + entrypoint-import gate. **Enforcement status
(2026-10-07, docs W7): NOT automated** — `scripts/ops/pre-pr-gate.sh`
covers ops-scripts `py_compile` but has no entrypoint-import stage, and
the "program branch" no longer exists. This gate is currently
honor-system; wiring it is a code change (flagged, not made in this docs
lane). Every doc that lists it as an enforced gate must say so.

**Built (this lane — on `lane/production-stages`, NOT on `main`):**
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
- Until the lane merges (or is formally re-planned), every claim that
  depends on these is a claim about the lane, not about `main`.

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
  differences live ONLY in the adapter + the mode banner. The banner
  contract is asserted at **build time** by `deploy/gh-pages/build-v2.py`
  (it injects `data-mode="production"` + PRODUCTION chrome into `/`,
  ships `/staging/` verbatim from the sim-marked source, and fails the
  build if either `<html>` element carries the wrong mode — substring +
  tag checks, not behavior tests; see the script's "banner contract
  verification" section). There is no GitHub Actions CI on this repo —
  the local gates are `scripts/ops/pre-pr-gate.sh` and a different-agent
  review (Aditya's standing order, `ops/decision_log.md` 2026-10-03).

## 3. Merge plan: program/full-build → main

**Preconditions (all must hold):**
1. Track 8 merged: winning console v2 integrated via the adapter seam;
   UI falsifiers (AC-7) re-run and green, including a real-phone check.
2. This lane's `lane/production-stages` merged into `program/full-build`:
   conflict fix, faithful fakes, calibration docs.
3. Full suite green + `pre-pr-gate.sh` GREEN on the merge head (the local
   gate, not CI — see the X-G correction); entrypoint-import remains
   honor-system until wired (the lesson from §0).
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
| `/preview-v2/` | **kept until Petu's flip order** (Petu live-browser verdict 2026-10-07: preview-v2 = B; it is a judgment artifact, not a fork to kill unilaterally) | — |
| `/loadtest/` | the load-test lane's dashboard (placeholder until `lane/loadtest-env` merges; until then the path says so) | lane merge → coordinator's call on timing |

> Load-test headline rule (docs W7, until the lanes merge): every
> load-test number repeated anywhere (16.2M/day, 187.4/s, the three bug
> fixes) describes **`lane/loadtest-env` code, not `main`** — every
> headline must name its code revision until the merge lands. The
> report and dashboard live on that lane; `main` carries no load-test
> numbers today.

Build is the existing static pipeline; verify each path HTTP 200 + banner
contract after deploy. `/` must show zero fixtures and the production
mode banner; `/staging/` must show the SIMULATED banner with seed.

## 5. Backend production config

> Rewritten 2026-10-07 (docs W7) to Vercel serverless reality. The
> self-hosted topology below was never the shipped one; do not boot a
> self-hosted box expecting this tier.

**Topology:** project **`sentinel-platform`** on Vercel (Hobby) —
`https://sentinel-platform-adityapagare619s-projects.vercel.app`,
header `X-Sentinel-Deployment: production-api`. The exact `PlatformApp`
from `platform/server` runs as **one serverless function**
(`deploy/vercel/api/index-prod.py`); `vercel.prod.json` routes every
`/api/*` to it. Static console on GitHub Pages calls it cross-origin
(CORS allowlist = Pages origin only). Bundle built by
`deploy/vercel/build-bundle-prod.sh` → `deploy/dist-prod/vercel`
(gitignored, generated).

This tier is read-path + safety-state: C1 auth, `/api/v1/ops/health`,
kill-switch endpoints. **Not on this tier:** the paging receiver, the
gate, the forwarder — there is no paging path here and no engine DB
(`engine_db.available=false`, reported honestly).

**Provisioning (Aditya performs via Vercel dashboard → project →
Settings → Environment Variables; Petu never handles raw values):**
1. `SENTINEL_OPERATOR_TOKEN`: the C1 operator bearer. Serverless has no
   writable state dir — without this env var the token store is ephemeral
   (every cold start mints a token nobody holds; the function logs a loud
   warning) and the console cannot authenticate: fail-closed by default,
   every `/api/*` is 401.
2. `TYPESAFE_API_KEY`: env var for the real Jev judge in prod.
   Cost-capped per the gate's per-call budget; spend metered. This is the
   shipped engine's key path — compliant with the standing law (never
   committed, never logged, `sanitize_error` strips it). The
   `custom.typesafe` vault surrogate is the **agent/research tooling**
   path (`research/jev-behavior/bin/`), not a production path.
3. **PagerDuty in prod = real, via the user's BYOK key ONLY.**
   Production MAY page real PD only via explicit customer BYOK +
   explicit operator action (the test-page precedent); sim/loadtest NEVER
   page real PD — that is structural, Stripe-`sk_test`-grade, not a
   toggle (X-B ruling, 2026-10-07; overrides this doc's earlier
   absolute "NEVER real PagerDuty" phrasing, which would outlaw the
   product's purpose).

**Startup assertions (fail-closed, loud):**
- `SENTINEL_OPERATOR_TOKEN` set, else `/api/*` 401 + the ephemeral-store
  warning in function logs.
- CORS origins != `*` (loud warning if wildcarded).
- Jev key present → real judge; absent → judge-down mode (timer-wins,
  fail-open); the console must show judge-down (wiring gap: currently
  `getJudgeDown(){ return false; }` hardcodes false — Track 8).
- Kill-switch state: per-instance `/tmp` (`kill_state_scope=
  "per_instance_tmp"` visible in status payloads). Cold start silently
  disengages — the dangerous direction; no multi-instance safety until
  external flag state exists (P1 gap).

**Known honest gaps on this tier:** ~30-min Hobby log retention, no
uptime monitor, no bundle-vs-source drift check, no engine DB wired.
None are hidden; none are claimed.

## 6. Production kill-switch drill procedure

> Topology qualifier (engine ruling 2026-10-07 — X-A): the measured drill
> below ran against the **`DurableForwarder` topology** (2.0ms, single
> local sample, ±1ms instrument). The production receiver wires the
> legacy sync `Forwarder`, which has **zero kill checks** — the drill has
> NOT been measured on the receiver topology (P0-1 open). The Vercel
> tier's kill is a no-op lever (flag flips, nothing reads it) until the
> fail-open vs fail-closed semantics are decided and wired. Every
> repetition of the drill headline must carry this qualifier.

1. Engage via console (two-step) or API with operator bearer.
2. `scripts/kill_drill.py` measures flip→forwarder-halt from DECISION-LOG
   timestamps on the REAL path. Bar: <5000ms. Measured 2.0ms in T7
   (DurableForwarder topology only — see qualifier above).
3. Verify: pages flow during kill (fail-open), suppressions halt,
   re-arm requires separate deliberate action (sticky).
4. Drill record written to `ops/drills/`; console shows "measured, not
   asserted" with the record link.
5. Cadence: after every deploy, and monthly. A deploy without a green
   drill does not serve production traffic.

## 7. Production runbook

- **Deploy:** merge → tag → build bundle
  (`deploy/vercel/build-bundle-prod.sh`, clean tree; record the commit)
  → deploy via Vercel CLI to project `sentinel-platform` → provision env
  vars in the dashboard (operator token, Jev key) → smoke (unauth 401s,
  authed ops/health 200) → kill drill green → serve traffic. Re-deploy
  after every main change that touches `platform/server` — the bundle is
  a snapshot, not a live checkout.
- **401-by-default verification:** `curl` without bearer → 401 on every
  `/api/*`; bogus bearer → 401; cross-origin from non-allowlisted origin
  → blocked. (T7 AC-5 re-walked this; re-verify per deploy.)
- **Rotation reality:** the dual-accept ceremony (Track 4
  `RotatingKeyStore`) is the engine's design (`docs/rotation/ROTATION.md`).
  On the Vercel tier, where every secret arrives via dashboard env vars,
  rotation is currently **flag-day**: swapping the env var cuts over with
  no overlap (`{"primary": provisioned, "secondary": None}`). Dual-accept
  overlap on serverless is unwired — the rotation doc now states this
  explicitly. Never rotate blind: stage the new value, verify auth against
  the tier, then swap and confirm.
- **Break-glass:** dormant dual-controlled credentials; any use fires an
  alarm, forces rotation afterwards, and lands in the audit log.
  Break-glass bypasses the PATH, never the quorum.
- **Degraded:** judge-down → timer-wins → fail-open paging; the banner
  MUST show judge-down — currently it cannot (`getJudgeDown()` is
  hardcoded `false`; Track 8 wiring gap, UI P0). Do not treat this line
  as built; it is a requirement.

## 8. Rollback plan

> Rewritten 2026-10-07 (docs W7) to Vercel reality. The self-hosted
> "stop, check out, restart" path below was never the shipped topology.

- **Backend (the actual rollback path):** Vercel dashboard → project
  `sentinel-platform` → Production Deployment tile → **Instant
  Rollback**. Hobby plan: rollback is offered to the **previous
  production deployment only** (Pro/Enterprise: any eligible deployment).
  What rollback does and does NOT do (per
  https://vercel.com/docs/instant-rollback, verified 2026-10-07):
  - Re-points production to the chosen previous deployment. No rebuild.
  - **Environment variables are NOT rolled back** — they remain exactly
    as configured in project settings. A rollback after an env-var change
    does not restore the old vars; set them deliberately, before or
    after, with eyes open.
  - Cron jobs revert to the rolled-back deployment's state.
  - After a rollback, Vercel turns off auto-assignment of production
    domains: subsequent pushes build but do NOT go live until you
    choose **Undo Rollback** (dashboard) or `vercel promote
    <deployment>`.
  - Older than the previous deployment: revert in Git and redeploy —
    there is no other path on Hobby.
- **Console:** gh-pages is static — rollback = revert the gh-pages commit
  for the affected path (`/`, `/staging/`, `/loadtest/` deploy
  independently). The `/preview-v2/` path is kept until Petu's flip
  order.
- **Kill switch as rollback — NOT wired (P0-1).** The old text said
  "engage the kill switch FIRST (stops all suppression → everything
  pages)". That is false on the shipped topologies: the deployed tier's
  kill is a no-op lever (engine ruling 2026-10-07), the receiver path has
  zero kill checks, and the fail-open vs fail-closed semantics are still
  undecided. **Do not list the kill switch as a rollback mitigation
  until the semantics are decided, wired on every topology, and
  re-drilled.** Until then the fastest mitigation is Instant Rollback.
- State-dir caveat (`sentinel-state/` never rolled back) applies to the
  self-hosted engine design only; the serverless tier has no persistent
  state dir.

## 9. Readiness checklist

| # | item | evidence | status |
|---|---|---|---|
| 1 | P0 auth closed | T7 AC-5 8/8; re-walk per deploy | ✅ (re-verify at shift) |
| 2 | Merge conflict in `__main__.py` resolved | this lane; `py_compile` OK | ✅ |
| 3 | Kill drill measured <5s on real path | T7 (2.0ms, single local sample ±1ms — **DurableForwarder topology only**; receiver topology NOT drilled, P0-1) | ⚠️ topology-qualified (re-drill receiver topology at shift) |
| 4 | Race outcomes exercised | T7 AC-2 8/8 | ✅ |
| 5 | Shadow attribution intact | T7 AC-6 4/4 | ✅ |
| 6 | Faithful sim services built + calibrated | **`lane/production-stages` ONLY** — FaithfulJev / FakePDSink / `CALIBRATION.md` are NOT on `main` (lane unmerged; merge or re-plan) | ⏳ lane-bound |
| 7 | UI falsifiers (AC-7) | parked → Track 8 | ⏳ needs Aditya's verdict |
| 8 | Live-Jev judgment semantics validated | 2026-10-06 run: 5/5 OK; noise_suppressed FAILED (known divergence) | ⚠️ partial — re-run at shift |
| 9 | Operator token provisioning | `SENTINEL_OPERATOR_TOKEN` in the Vercel dashboard (env var; serverless has no state dir) | ⏳ Aditya's action |
| 10 | TYPESAFE_API_KEY provisioning | env var in the Vercel dashboard — the shipped engine's key path (compliant: never committed/logged). The `custom.typesafe` vault surrogate is the agent/research tooling path, not a production path (X-E). | ⏳ Aditya's action |
| 11 | Real PD BYOK in prod | only via explicit customer BYOK + explicit operator action (X-B); sim/loadtest structurally never real PD | ⏳ needs live PD test (Aditya's key) |
| 12 | Rollback rehearsed | Vercel dashboard Instant Rollback (Hobby: previous deployment only; env vars NOT rolled back) | ⏳ rehearse at shift |
| 13 | Phone falsifier | cannot verify without real device | ⚠️ needs Aditya's phone |
| 14 | Ops health endpoint (`GET /api/v1/ops/health`) | ✅ **implemented on `main`** — `platform/server/app.py` routes to `ops_health.handle_health`; stages honestly report `not_instrumented`, the circuit reads `not_implemented` | ✅ built |
| 15 | Jev circuit breaker (health `circuit` field) | **not implemented** — the endpoint reports `circuit: "not_implemented"` literally, never fake `closed` | ❌ gap — implement or drop the field |
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
5. **The Operations Health surface is built but thin.** The
   `GET /api/v1/ops/health` aggregate exists on `main` (2026-10-07),
   but its stage fields honestly read `not_instrumented` (correlator/gate
   metrics), the circuit reads `not_implemented`, and the judge
   observation log is a gap. Aditya's "inspect the pipeline myself" loop
   is gated on real instrumentation behind those fields — the honest
   `not_instrumented` labels are the contract until they land. The
   all-or-nothing console `sync()` that blanks the drawer on any single
   endpoint failure remains the top UI P1.
