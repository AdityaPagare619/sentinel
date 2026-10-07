# Sentinel — PLATFORM ARCHITECTURE (frozen)

**Status:** FROZEN 2026-10-02 evening · **Goal:** working interactive platform by **Sunday 9 PM IST**
**Parent specs:** `ARCHITECTURE.md` (the engine — v0.1 middleware, frozen), the JEV
research report §§5.2/7.1/8 (the problem, the economics, the scope).
**Chairman:** Aditya · **Commander:** Petu · **Architect:** Forge

---

## 1. What the platform is (one paragraph)

Sentinel the *engine* is a pre-page gate: alerts in, triaged decisions out, audit
rows written. Sentinel the *platform* is everything that makes the engine a
product a buyer can touch: a live decision river, calibration dashboards that
prove trustworthiness, a threshold simulator that turns "what if" into dollars,
an audit explorer that answers "prove it," noise analytics that show where the
pain lives, and a 15-minute onboarding that makes integration feel trivial. The
engine is the classifier; the platform is the **trust layer made interactive**.
That is what we sell — not "Jev working," but the confidence-gating, the tuning,
and the proof, all clickable.

## 2. Relationship to the engine (non-negotiable)

- The platform **builds ON `ARCHITECTURE.md`, not around it**. The engine's
  guarantees — fail-open always, uncertainty pages, triple-lock suppress,
  append-only audit — are inherited constraints, not suggestions.
- The engine's module contracts (`ARCHITECTURE.md` §3) are frozen. The platform
  adds a new tier; it does not modify the hot path.
- If a platform feature ever conflicts with an engine guarantee, the engine wins.
  No exceptions, no "just this once."

## 3. System diagram: two tiers, one bridge

```
  PAGING PATH (hot — sub-second budget)          PLATFORM TIER (interactive — no budget pressure)
  ┌──────────────────────────────────┐           ┌──────────────────────────────────┐
  │  receiver → correlator → gate    │           │  dashboard server (SEPARATE      │
  │      │         (Jev: ONE         │           │  OS process, stdlib http.server) │
  │      │         parallel call)   │           │                                  │
  │      ▼                          │           │  ┌────────────┐  ┌────────────┐  │
  │  forwarder → PagerDuty          │           │  │ read API   │  │ SSE stream │  │
  │      │                          │           │  │ (audit     │  │ (decision  │  │
  │      ▼                          │           │  │  reads     │  │  river)     │  │
  │  audit log (SQLite WAL) ────────┼───────────┼─▶│  only)     │  │            │  │
  └──────────────────────────────────┘           └──────────────────────────────────┘
         ▲ the ONLY bridge: engine writes, platform reads. Never the reverse.
```

**Why two processes:** a dashboard bug, a slow query, or a traffic spike on the UI
must be *incapable* of slowing a page. Process separation is the enforcement, not
a comment in the code. The platform tier holds a **read-only** SQLite connection;
the engine writes with WAL mode so readers never block the writer.

## 4. DO-NO-HARM LAWS (non-negotiable — violations stop the line)

**Law 1 — The platform never adds latency to the paging path.**
Beyond the single Jev call, the paging path gains zero milliseconds from the
platform's existence. Enforcement: (a) separate process — the dashboard cannot
contend for the engine's CPU/locks; (b) the audit log is the only bridge, and the
engine's write is a local WAL insert (milliseconds, already in the frozen design);
(c) the platform tier makes ZERO Jev calls on any read path — the simulator
recomputes from *stored* probabilities, never re-calls; (d) backpressure: on alert
spikes, storm-collapse fires one Jev call per storm (load-bearing at 40 req/s),
the receiver queue is bounded, and under load we shed *dashboard* traffic first —
never paging traffic. The receiver never returns 5xx for a triage failure.

**Law 2 — Fail-open always; uncertainty pages.**
Inherited from the engine and extended: if the *platform tier* dies, alerts still
flow — the tiers share nothing but the audit file. Any Jev error/timeout →
passthrough. Low confidence / `cannot_determine` / malformed input → the existing
pipeline, byte-identical. The kill-the-client test stays a release blocker.

**Law 3 — Zero *harmful* errors is the target; flips are audited, never hidden.**
Suppression keeps the triple lock (P(p1) < 0.002 AND conf ≥ 0.90 AND allowlisted
fingerprint). Every decision row carries `input_sha256` + `jev_model` + full
probability maps, so a flip is explainable. And we say the irreducible truth
out loud — on the dashboard itself: **Jev flips 1.3–2.2% of repeat calls (no seed
exists); this floor cannot be engineered away, only audited.** "Zero errors" as a
slogan would be a lie; zero *unexplained* errors is the engineering target, and
the audit explorer is how we prove it case by case.

**Law 4 — Honest scope, stated on the tin.**
Sunday 9 PM = a working interactive platform a design partner can click through
and run — explicitly NOT multi-tenancy, billing, SSO, or production PagerDuty
integration (§8). Every dashboard view labels its data source (synthetic / shadow /
production). Demo data presented as production data is a trust incident.

## 5. Platform read API (Forge's Sat Wave A contract — frozen)

All read-only. All served by the platform tier from SQLite. All responses labeled
with their data source. Lanes build against these signatures; mocks provided.

```
GET  /api/decisions?limit=50&since_id=<id>          # decision river feed
GET  /api/decisions?fingerprint=&team=&action=&from=&to=   # audit explorer search
GET  /api/decision/<id>                             # full row: probs, input_sha256, model
GET  /api/calibration?team=<team>                   # ordinal rank-fidelity (AUC over p1 ranks), deciles, coverage@τ
POST /api/simulate  {thresholds, dataset_version}    # → {projection} (pure recompute)
GET  /api/analytics/noise?window=24h                # top checks, team load, breakdown
GET  /api/analytics/flips?window=7d                # flip-audit records
SSE  /api/stream                                    # new decisions as they're written
```

No endpoint writes. No endpoint calls Jev. No endpoint touches the hot path.

## 6. Feature spine — the six surfaces

### (a) Live decision river — P0 for Sunday

- **User flow:** open the platform → the river streams every triaged decision as
  it lands: time, alert title, service, severity chip, team chip, disposition chip,
  and a **confidence bar** (the Q3 probability distribution rendered as a bar).
  Click a row → full decision detail (probabilities per question, input hash,
  model version, latency).
- **Data source:** `decisions` table, `ORDER BY id DESC` + SSE tail. Zero hot-path
  coupling — the river is a view over the audit log.
- **Build lane:** dashboard lane (UI + SSE) against Forge's read API; Tripwire
  gates the SSE reconnect behavior.
- **Why it matters:** this is the "it's alive" moment. A buyer watches noise die
  in real time.

### (b) Per-team calibration dashboards — P0 (v1) for Sunday

- **User flow:** pick a team → reliability diagram (predicted probability vs
  observed outcome rate per bin), ECE number with N and error bar, coverage@τ
  table (τ ∈ {0.7, 0.8, 0.9}), flip-rate panel. Every chart carries its
  denominator and data-source label (Oracle's law: no chart without a denominator).
- **Data source:** `decisions ⨝ outcomes` (Ledger's join; Sunday runs on versioned
  synthetic labels — labeled as such, loudly).
- **Build lane:** calib/eval lane (compute: bins, ECE, coverage) + dashboard lane
  (rendering). Oracle owns the math; Prism owns the presentation.
- **Why it matters:** this is the trust sale. Not "we're accurate" — "here is our
  calibration curve on data, with error bars."

### (c) Threshold simulator — P0 for Sunday (the demo centerpiece)

- **User flow:** draggable sliders for `suppress_conf_min`, `suppress_p1_max`,
  `page_p1p2_min` (and C_FP/C_FN cost inputs) → the projected outcome recomputes
  **live**: suppressions, pages, queue, expected false suppresses, expected
  cost/yr, avoided page cost/yr — with the tradeoff table the tuner prints.
  Drag the confidence bar down → watch projected savings rise and expected false
  suppresses rise with it. The tradeoff is *visible*, not argued.
- **Data source:** the tuner's projection logic exposed as a pure function over
  the labeled dataset (same code path as `tuner.py`, not a copy — Oracle's rule).
  Recompute is local arithmetic over stored probabilities. **No Jev calls.**
- **Build lane:** calib/eval lane (compute API `POST /api/simulate`) + dashboard
  lane (sliders + live recompute). Oracle signs off that simulator math ≡ tuner math.
- **Why it matters:** this is the "shut up and take my money" interaction. The
  buyer prices their own risk tolerance.

### (d) Audit explorer — P0 for Sunday

- **User flow:** search decisions by fingerprint, alert_id, team, action, time
  range → full decision record: all three questions' probability maps, input
  SHA-256, model version, latency, reason code. Flip-audit view: same
  `input_sha256` decided differently across repeats — shown, not hidden.
- **Data source:** `decisions` table (+ flip-audit records from repeatability probes).
- **Build lane:** dashboard lane + security/audit lane (Vault: verify zero sensitive
  material in any returned row).
- **Why it matters:** "prove it" is the whole sale. The explorer is the proof,
  queryable.

### (e) Alert-noise analytics — P1 for Sunday (v1: top checks + team load)

- **User flow:** "where is my noise?" — top flapping checks (by volume and by
  suppressed-volume), per-team page load (pages/night before vs after Sentinel),
  suppression breakdown by reason (allowlist / storm / change-window / dedup),
  storm timeline.
- **Data source:** aggregations over `decisions` (Ledger provides the aggregate
  queries; Sunday v1 = top checks + team load + breakdown; timeline if time).
- **Build lane:** dashboard lane + Ledger (aggregates). Pager reviews: "would an
  SRE recognize their pain in these charts?"
- **Why it matters:** turns the buyer's own noise into the sales deck.

### (f) 15-minute onboarding — P0 for Sunday

- **User flow:** (1) paste the 50-line integration snippet (point your PD webhook
  at Sentinel); (2) BYOK setup — key entered once, never shown again (Vault-approved
  handling under Prism's UX); (3) guided first storm — fire the synthetic storm,
  watch the river, open the simulator. Stopwatch-verified <15 minutes.
- **Data source:** docs + `synthetic.py` + the river. No new backend.
- **Build lane:** docs/devrel lane + Prism. Tripwire: the onboarding is timed
  weekly; regressions get fix tasks.
- **Why it matters:** the wedge is integration, not rip-and-replace. Fifteen
  minutes to "holy shit, it works" is the funnel.

**P0/P1 note:** P0 = Sunday 9 PM or the goal fails. P1 (noise timeline, extra
analytics depth) ships if green early, else post-Sunday — decided by Petu, never
by silent slipping.

## 7. Data model (platform additions: none required)

The audit schema (`ARCHITECTURE.md` §3.8: `decisions` + `outcomes`) is already the
platform's API. **Decision: no new tables for the Sunday platform.** The simulator
reads the labeled dataset (Ledger's versioned fixtures); the river/explorer/
analytics read `decisions`; calibration reads `decisions ⨝ outcomes`. If a lane
wants a new table, that's a contract change → Forge + ADR, not a quiet migration.

## 8. Tech decisions (frozen)

- **Platform tier: stdlib `http.server` (separate process) + vanilla JS + SSE.**
  No framework, no pip deps — the engine's ₹0/zero-deps discipline extends to the
  platform. (FastAPI/gunicorn remains the documented production swap, post-Sunday.)
- **SQLite in WAL mode; platform holds a read-only connection.** Readers never
  block the audit writer. (Postgres read replica is the SaaS upgrade.)
- **Vercel is the deploy target for the marketing/docs surface when it exists**
  (Aditya's order — only when needed). The Sunday demo runs local; nothing about
  Sunday depends on any external service — not TypeSafe, not Vercel, not GitHub.
- **The demo is mock-backed with recorded fallbacks** (ROADMAP.md risk #2, now
  extended to the platform): the river/SSE demo runs against a live local engine
  in mock mode; any segment that could depend on the network has a recording.

## 9. Honest scope boundary

**Sunday 9 PM ships:** the six surfaces above, clickable, running against
versioned synthetic data (labeled as such), single-tenant, local SQLite,
mock-backed engine, demo script with recorded fallbacks.

**Explicitly post-Sunday (designed, not built):** multi-tenancy, billing, SSO,
production PagerDuty/Opsgenie integration, Postgres + Redis, KMS envelope /
per-tenant BYOK, self-hosted customer-VPC relay, label-join automation, the
fine-tuned-encoder exit, design-partner outreach, public repo + pricing page.

## 10. Honest limitations (Sunday platform)

1. Synthetic data proves plumbing and interactivity, not production accuracy —
   every view says so on the tin.
2. The 11.4s first-real-call latency measurement stands unresolved until Oracle's
   campaign reports; the dashboard will show *measured* latency, and the timeout
   policy (not hope) protects paging meanwhile.
3. Single box, single tenant, SQLite — the SaaS hardening is drawn, not built.
4. The simulator's projections are only as good as the labels underneath —
   versioned synthetic labels for Sunday, customer shadow labels after.
