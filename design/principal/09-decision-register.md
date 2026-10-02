# 09 — Decision Register

**Law 3 made concrete.** Every significant decision Sentinel has taken, plus every
decision the principal re-design proposes, marked Type 1 (irreversible — core
schema, public API, paging-path architecture, data retention, trust model) or
Type 2 (reversible — gets speed, not an RFC).

**Conventions:**
- **Type 1** entries carry: decision, date, decided-by, alternatives considered,
  the tradeoff, and what would change it. A Type 1 decided like a Type 2 is a
  future incident.
- **Status:** `DECIDED` (in force) · `PROPOSED` (RFC open, not applied) ·
  `OPEN` (not yet decided — a named gap, not an oversight).
- **Who decides:** Aditya owns the big Type 1s (direction, scope, pricing,
  vendor posture, ADR verdicts). Forge recommends; Petu enforces. Chiefs decide
  their lane's Type 1s under standing orders; all are logged here.
- Sources: `ops/decision_log.md`, `research/synthesis-2026-10-02.md` (9 ADRs),
  `ARCHITECTURE.md` + `PLATFORM_ARCHITECTURE.md` (frozen), `CHARTER.md`,
  `METHODOLOGY.md`, `ROAD_TO_LAUNCH.md`, `WAVE_PLAN.md`, `ops/constraint_registry.md`.

---

## §1 — Type 1 decisions, DECIDED

### Trust model & paging-path architecture

**DR-1 — The ONLY autonomous action is "page a human."**
Decided 2026-10-02 (research pick → CHARTER.md design law 1; Aditya).
Alternatives considered: autonomous remediation ("incident commander"); routing
+ auto-resolve of low-severity. Tradeoff: we surrender the autonomous-response
narrative (PagerDuty's SRE Agent, Spring 2026, is moving exactly there) in
exchange for a legible counter-position and zero tail-liability from machine
actions. What would change it: a customer-funded, liability-bounded pilot with
Aditya's explicit sign-off — not a technical argument.

**DR-2 — Fail-open always; uncertainty pages.**
Decided 2026-10-02 (CHARTER.md laws 2–3; PLATFORM_ARCHITECTURE.md Law 2).
Alternatives: fail-closed with retry (dropped pages are company-ending — a gate
that drops a page on error is worse than no gate); queue-on-error (adds latency
to the path that must never wait). Tradeoff: we accept noisy pages during vendor
outages instead of silent ones. What would change it: nothing — this is the brand.

**DR-3 — Suppression requires the triple lock.**
`P(p1_critical) < 0.002 AND Q3 confidence ≥ 0.90 AND fingerprint ∈
customer-verified allowlist`. Decided 2026-10-02 (ARCHITECTURE.md §4).
Alternatives: model-only threshold (rejected — raw week-1 probabilities are
uncalibrated); human-in-the-loop per suppress (rejected — doesn't scale);
two-lock without allowlist (rejected — the 1.3–2.2% flip floor makes raw
confidence unsafe to act on alone). Tradeoff: suppression rate is lower than
competitors claim, but every suppression is defensible in a postmortem.
What would change it: the *constants* move when shadow pilots measure C_FP/C_FN
(Type 2); removing a *lock* needs Aditya + postmortem-grade evidence.

**DR-4 — Expected-cost threshold math: suppress iff p* < C_FP/(C_FP+C_FN).**
Decided 2026-10-02 (ARCHITECTURE.md §4; defaults C_FP=$100, C_FN=$50,000 →
p* ≈ 0.002). Alternatives: fixed 0.5 confidence gates (ignores asymmetric cost —
a $100 false page vs a $50,000 suppressed SEV1 are not symmetric); per-team
manual tuning only (uncalibrated week-1). Tradeoff: founder-grade cost estimates
until measured; the math is principled, the inputs are provisional.
What would change it: tuner + shadow pilots replace the estimates with
measurements; the math itself stands.

**DR-5 — Shadow-first deployment: weeks 1–2 shadow-only for every org.**
Decided 2026-10-02 (decision log; ARCHITECTURE.md §4). Alternatives: direct
cutover (trust incident waiting to happen); canary pages (still changes paging).
Tradeoff: slower time-to-value for faster time-to-trust — the weekly shadow
report ("we would have suppressed X, gotten Y wrong") is the sales collateral.
What would change it: shadow data could shorten the window for low-risk orgs —
Aditya's call.

**DR-6 — Any unexplained false suppress on a SEV1-class alert is a SEV1.**
Decided 2026-10-02 (CHARTER.md; METHODOLOGY.md §6): blameless postmortem ≤24h,
report derived from the audit log; if the log can't explain it, that's a finding
against the log. Alternatives: log-and-move-on (erodes the trust sale).
Tradeoff: heavyweight process for a startup — deliberately so; the rule is the
proof we take suppression seriously. What would change it: none planned.

**DR-7 — No accuracy claims anywhere; calibration curves only.**
Decided 2026-10-02 (CHARTER.md law 5; constraint registry). Alternatives:
headline accuracy numbers (dishonest — synthetic data + flips + uncalibrated
week-1). Tradeoff: weaker marketing copy for an unbreakable honesty position;
the JEV research report's honesty order is the brand. What would change it: none.

**DR-8 — Deterministic work happens BEFORE the Jev call; never pay Jev for it.**
Decided 2026-10-02 (ARCHITECTURE.md principle 3): dedup, storm-collapse,
change-window suppression in plain code, pre-call. Alternatives: Jev-first
pipeline (cost + latency + non-determinism spent on trivially deterministic
work). Tradeoff: the correlator must be genuinely good (ADR-001 exists because
v0.1's is missing practitioner craft). What would change it: a 10× drop in Jev
latency/cost could reopen the ordering for correlation quality — unlikely.

**DR-9 — Platform/hot-path separation is architectural, not aspirational.**
Decided 2026-10-02 ~18:45 (Aditya's platform pivot; PLATFORM_ARCHITECTURE.md
§3): separate OS process; the audit log is the ONLY bridge (engine writes,
platform reads); read-only SQLite connection for the platform tier; UI traffic
can never contend with the audit writer or slow a page. Alternatives: in-process
dashboard (a slow query could delay a page — violates do-no-harm Law 1).
Tradeoff: two deployables, SSE plumbing, duplicated fixture handling — the price
of the guarantee. What would change it: nothing; Forge audits it at review.

**DR-10 — ONE parallel Jev call per alert; ZERO Jev calls on platform read paths.**
Decided 2026-10-02 evening (constraint registry; PLATFORM_ARCHITECTURE.md §4
Law 1): the three questions go in a single parallel call — never sequential,
never chained; the simulator recomputes from *stored* probabilities, never
re-calls. Alternatives: per-question calls (3× latency, 3× rate burn);
simulator re-calls (cost + non-determinism breaks reproducibility).
Tradeoff: the client must get parallel-question semantics right once.
What would change it: nothing while T-1/T-3 bind.

**DR-11 — The receiver never returns 5xx for a triage failure; unparseable input
is forwarded byte-identical.** Decided 2026-10-02 (ARCHITECTURE.md §3.9).
Alternatives: reject malformed with 4xx (a dropped alert is worse than a noisy
one). Tradeoff: garbage in → noisy page out, but never a silent drop.
What would change it: nothing.

**DR-12 — `cannot_determine` always resolves to passthrough.**
Decided 2026-10-02 (CHARTER.md law 2; ARCHITECTURE.md §4). Alternatives: treat
as suppress (suppressing the unknown is the tail-risk event — rejected outright).
What would change it: nothing.

**DR-13 — Change-window alerts → `page_business_hours` deterministically, no Jev call.**
Decided 2026-10-02 (ARCHITECTURE.md §3.5). Alternatives: triage through Jev
anyway (deploy-churn is known in advance; practitioner rule is suppress/queue it).
Tradeoff: a genuinely novel SEV1 inside a change window gets queued — accepted
because the window is customer-declared and auditable. What would change it:
per-customer override configs post-Sunday (ADR-008 direction).

**DR-14 — Storm-collapse: one Jev call per storm, page once.**
Decided 2026-10-02 (ARCHITECTURE.md §3.5; constraint registry — load-bearing at
40 req/s, not an optimization). Alternatives: per-alert calls (a 1,000-alert
storm 529s the path — rejected by T-3). Tradeoff: per-alert fidelity is lost
inside a storm; the aggregate disposition carries counts by service.
What would change it: nothing while rate limits bind.

**DR-15 — Under load, shed dashboard/API traffic first; never paging-path traffic.**
Decided 2026-10-02 evening (constraint registry; PLATFORM_ARCHITECTURE.md §4
Law 1). Alternatives: fair-share shedding (a dashboard is never worth a delayed
page). What would change it: nothing.

**DR-16 — Never auto-dismiss security/compliance alerts; no "Jev incident commander."**
Decided 2026-10-02 (CHARTER.md non-goals; research §8.5). Alternatives: full
autonomous response (adversarial input + tail liability — rejected; this is the
named counter-position to PagerDuty's SRE Agent). What would change it: none —
also a regulatory posture (R-3).

**DR-17 — EU data residency: no EU customer data through the US-only TypeSafe API at v0.1.**
Decided 2026-10-02 (constraint registry; research note). Alternatives: route
anyway with disclosure (regulatory exposure — rejected). Tradeoff: EU design
partners wait for the structural answer. What would change it: self-hosted
customer-VPC relay (designed) or regional inference — Aditya decides (O-3).

### Core schema, audit & public API

**DR-18 — Append-only audit log; no UPDATE/DELETE paths in `AuditLog`.**
Decided 2026-10-02 (ARCHITECTURE.md §3.8, §7; CHARTER.md law 6). Alternatives:
mutable log with corrections (corrections destroy the trust story; flips must be
visible, not overwritten). Tradeoff: storage grows forever until O-1 (retention)
is decided — the register names its own debt. What would change it: hash-chaining
is additive hardening, not a reversal; retention policy is the open question.

**DR-19 — Audit schema: `decisions` + `outcomes` tables, Postgres-compatible types.**
Decided 2026-10-02 (ARCHITECTURE.md §3.8). Alternatives: schemaless JSON store
(calibration joins need queryable structure — rejected). Tradeoff: schema
discipline under deadline. What would change it: new tables need Forge + ADR
(DR-25); the SaaS move is Postgres, types already compatible.

**DR-20 — Every decision row carries `input_sha256` + `jev_model` + full probability maps.**
Decided 2026-10-02 (ARCHITECTURE.md §4 honesty note; constraint registry).
Alternatives: store disposition only (flip-audits and postmortems become
impossible — rejected). Tradeoff: wider rows for complete explainability.
What would change it: additive fields only, never fewer.

**DR-21 — Disposition set: `page_now` | `page_business_hours` | `suppress` | `passthrough`,
with reason codes.** Decided 2026-10-02 (ARCHITECTURE.md §3.2).
Alternatives: binary page/drop (queue and passthrough are distinct trust states —
rejected). Tradeoff: downstream integrations must handle four states.
What would change it: ADR-007 proposes a fifth visibility state
(`muted-not-dropped`, dashboard-only) — Aditya decides.

**DR-22 — Severity taxonomy Q1 (`p1_critical`/`p2_high`/`p3_medium`/`p4_low`/
`known_noise`/`cannot_determine`); `cannot_determine` mandatory on all three questions.**
Decided 2026-10-02 (ARCHITECTURE.md §3.3). Alternatives: free-text severity
(uncalibratable); vendor severity passthrough (taxonomies are inconsistent).
Tradeoff: our taxonomy must be mapped at every ingress. What would change it:
the mandatory `cannot_determine` is a Jev docs rule — non-negotiable.

**DR-23 — Fingerprint = sha256(`service|check|severity_in|region`), first 16 chars.**
Decided 2026-10-02 (ARCHITECTURE.md §3.5). Alternatives: include metric value
(flapping values would never dedup); PD `dedup_key` passthrough (inconsistent
across sources). Tradeoff: 16 hex chars is enough for dedup, short enough for
humans. What would change it: collision evidence in the wild → widen.

**DR-24 — Platform read API: 6 endpoints + SSE, all read-only, every response
labeled with its data source.** Decided 2026-10-02 evening, frozen
(PLATFORM_ARCHITECTURE.md §5): `GET /api/decisions`, `GET /api/decision/<id>`,
`GET /api/calibration`, `POST /api/simulate`, `GET /api/analytics/noise`,
`GET /api/analytics/flips`, `SSE /api/stream`. Alternatives: GraphQL (surface
area + stdlib-only — rejected); mutable endpoints (the audit bridge is
write-one-way — rejected). Tradeoff: clients get exactly the trust views, nothing
else. What would change it: additive endpoints via Forge contract review.

**DR-25 — No new tables for the Sunday platform.**
Decided 2026-10-02 evening (PLATFORM_ARCHITECTURE.md §7). Alternatives:
per-feature tables (schema sprawl under deadline — rejected). Tradeoff: Ledger's
aggregates must be queries, not tables. What would change it: post-Sunday via
Forge + ADR — never a quiet migration.

**DR-26 — Simulator math ≡ tuner math: same code path, not a copy.**
Decided 2026-10-02 evening (PLATFORM_ARCHITECTURE.md §5c; Oracle's rule).
Alternatives: separate JS reimplementation (drift between "what we demo" and
"what we ship" — rejected). Tradeoff: the compute API must expose the tuner's
pure function. What would change it: nothing; Oracle signs off equivalence.

**DR-27 — Webhook auth = HMAC-SHA256 (`X-Sentinel-Signature`); hardening:
raw-body-before-parse, constant-time compare, empty-secret-refuses, ~5-min
timestamp tolerance; NO IP allowlisting.** Base scheme decided 2026-10-02
(ARCHITECTURE.md §3.9, §7); hardening PROPOSED via ADR-005 (research note —
industry standard: GitHub/Stripe/Shopify/PagerDuty/Slack). Known tension:
ARCHITECTURE.md §7 mentions "optional IP allowlist config" — ADR-005 says
precedent judges it brittleness without benefit. The reconciliation is part of
the ADR-005 verdict. What would change it: Aditya's ADR-005 decision.

**DR-28 — Forwarder semantics: `page_now` → PD trigger with original routing key;
`page_business_hours` → severity downgraded to `warning` + `sentinel_queue`
marker; `suppress` → not forwarded; `passthrough` → original payload.**
Decided 2026-10-02 (ARCHITECTURE.md §3.7). Alternatives: uniform forwarding
(queue vs page must be distinguishable downstream — rejected). Tradeoff: the
queue state lives in PagerDuty's severity field — pragmatic, visible.
What would change it: production PD/Opsgenie integration work post-Sunday.

### Product scope & positioning

**DR-29 — The founding pick: page-or-suppress pre-page middleware.**
Decided 2026-10-02 ~09:10 (Aditya, from the 888-line JEV research report:
#1 of the scored funnel; backups were customs doc-completeness and
claim-denial scoring). Alternatives: the backups (kept as ranked options).
Tradeoff: the entire company is a bet on the wedge thesis (B-5).
What would change it: only Aditya, only on falsifying evidence from pilots.

**DR-30 — Sunday 2026-10-04 21:00 IST: working interactive design-partner
platform; the scope boundary is hard** (explicitly OUT: multi-tenancy, billing,
SSO, production PagerDuty integration, fine-tuned-encoder exit).
Decided 2026-10-02 ~18:45 (Aditya's platform-pivot correction;
PLATFORM_ARCHITECTURE.md §9). Alternatives: engine-only demo (rejected —
engine-before-platform was the wrong order); full production (impossible +
dishonest). Tradeoff: everything post-Sunday is designed, not built.
What would change it: Aditya only.

**DR-31 — The six-surface feature spine** (decision river, per-team calibration
dashboards, threshold simulator, audit explorer, noise analytics v1, 15-minute
onboarding). Decided 2026-10-02 evening, frozen (PLATFORM_ARCHITECTURE.md §6).
Alternatives: narrower scope (rejected — the trust layer IS the product).
Tradeoff: P0/P1 split — P1s (noise timeline depth) can slip on Petu's logged
call; P0s and the do-no-harm laws cannot. What would change it: Aditya.

**DR-32 — The demo is mock-backed with recorded fallbacks; it never depends on
TypeSafe's availability.** Decided 2026-10-02 (ROADMAP.md risk #2;
PLATFORM_ARCHITECTURE.md §8). Alternatives: live-Jev demo (vendor 529s, rate
limits, and the 11.4s latency are vendor-controlled — rejected). Tradeoff: the
demo proves plumbing and interactivity, never production accuracy — said on the
tin. What would change it: when the latency campaign earns the inline claim.

**DR-33 — Stdlib-only: no pip dependencies, engine AND platform tier.**
Decided 2026-10-02 (decision log; constraint registry; PLATFORM_ARCHITECTURE.md
§8). Alternatives: FastAPI/gunicorn now (rejected — ₹0 ops, zero supply-chain
surface, runs anywhere). Tradeoff: hand-rolled HTTP/SSE instead of frameworks.
What would change it: the production swap path is pre-documented
(FastAPI/gunicorn, Postgres, Redis, KMS) — post-Sunday, not a reversal.

**DR-34 — SQLite audit store (WAL mode), Postgres-compatible schema.**
Decided 2026-10-02 (ARCHITECTURE.md §3.8). Alternatives: Postgres now (ops
weight for a design-partner demo — rejected). Tradeoff: single-box ceiling.
What would change it: SaaS upgrade = Postgres (+ read replica for the platform
tier) — pre-planned.

**DR-35 — 15-minute onboarding is a product promise, stopwatch-verified.**
Decided 2026-10-02 evening (PLATFORM_ARCHITECTURE.md §6f; WAVE_PLAN Sun AM).
Alternatives: docs-only onboarding (the wedge is integration — rejected).
Tradeoff: the 50-line snippet + BYOK UX + guided storm must actually fit in
15 minutes, timed weekly. What would change it: evidence from real onboardings.

**DR-36 — Every dashboard view labels its data source (synthetic/shadow/
production); no chart without a denominator; provenance on every automated claim.**
Decided 2026-10-02 evening (PLATFORM_ARCHITECTURE.md Law 4; Oracle's law;
Prism's UX law from the ux-freedom synthesis). Alternatives: clean-looking demo
data (presenting demo data as production data is a trust incident — rejected).
Tradeoff: every view carries its caveats — deliberately unsexy.
What would change it: nothing.

**DR-37 — v0.1 is Jev-only; the LLM adjudicator is deferred to a measured premium tier.**
Decided 2026-10-02 (decision log; ARCHITECTURE.md §10.6). Alternatives: hybrid
now (cost/accuracy must be measured on gray-zone labels first — rejected).
Tradeoff: gray-zone accuracy is Jev-bounded until measured. What would change it:
measured evidence + Aditya.

**DR-38 — The seven principal laws adopted as the review gate.**
Decided 2026-10-03 ~01:10 IST (Aditya's direct order;
`design/principal/00-laws.md`). What would change it: Aditya.

**DR-39 — The four do-no-harm laws** (platform never adds paging-path latency;
fail-open always; zero *harmful* errors with flips audited never hidden; honest
scope). Decided 2026-10-02 ~18:45 (Aditya; PLATFORM_ARCHITECTURE.md §4).
Note the honest form: "zero errors" was corrected to "zero *unexplained*
errors" — the 1.3–2.2% flip floor is irreducible and said plainly.
What would change it: Aditya.

### Process & doctrine

**DR-40 — `main` protected: PR-only, no self-merge (different agent reviews),
squash merges, green CI required; red main is stop-the-line.**
Decided 2026-10-02 (METHODOLOGY.md §1; LOCAL-OPS.md §6). Alternatives: trunk
commits (agent velocity needs the gate — rejected). Tradeoff: review latency vs
safety; review SLA is minutes. Note: `gh pr review --approve` rejects as
self-approval because GitHub identity is Aditya's for all agents — review
*content* is the control. What would change it: Aditya.

**DR-41 — Single-owner rule: one lane owns each file set; crossing lanes is a
flagged PR request, never a silent edit.** Decided 2026-10-02 (LOCAL-OPS.md §2;
learned the hard way in the TFB campaign — two writers racing on one artifact
path corrupted results). What would change it: nothing.

**DR-42 — Definition of Done: tests green + docs updated + PROGRESS.md entry +
demo-able + no secrets + Claim-Auditor pass.** Decided 2026-10-02
(METHODOLOGY.md §4). What would change it: Petu.

**DR-43 — The kill-the-client test is a release blocker.**
Decided 2026-10-02 (ARCHITECTURE.md §9; METHODOLOGY.md §3). What would change
it: nothing — it is the executable form of DR-2.

**DR-44 — Repeatability probes run in CI: flip <2%, option-shuffle <3% on a
200-alert sample.** Decided 2026-10-02 (METHODOLOGY.md §3; eval harness).
The *existence* of the probes is Type 1; the bar *values* are Type 2.
Alternatives: no probes (Jev's non-determinism would go unaudited — rejected).
What would change it: tightening needs evidence; removing needs Aditya.

**DR-45 — Frozen specs change via ADR only; never silent edits.**
Decided 2026-10-02 (research CHARTER.md; synthesis). Alternatives: direct edits
(frozen means frozen — rejected). What would change it: nothing.

**DR-46 — Standing R&D function: daily per-department questions, web-verified,
citation standard, ADR-only spec changes.** Decided 2026-10-02
(research/CHARTER.md; synthesis). What would change it: Aditya.

**DR-47 — Repo is the source of truth; the room is the workbench; the two mirror.**
Decided 2026-10-02 (decision log; Aditya's order). Private → public at Preview-1.
What would change it: Aditya.

---

## §2 — Type 1 decisions, PROPOSED (the 9 ADRs — none applied)

All from `research/synthesis-2026-10-02.md`. ADR-001/005/007 are the three worth
deciding before Sunday (synthesis implication note).

**P-1 — ADR-001: Correlator craft alignment.** Flap-debounce *reopens the same
row* on re-fire; resolved-then-refired is a *fresh episode, not a reopen*;
incidents are *never auto-closed* from a resolved alert; P1/P2 are *never
silenced* in maintenance windows. Evidence: sre-field note (firefightlabs,
asiaostrich standards). **This is the only ADR touching a frozen spec**
(ARCHITECTURE.md §3.5). Status: PROPOSED → Forge review → **Aditya decides**.
Alternatives: keep v0.1 correlator semantics (rejected by practitioner
consensus — our synthetic generator should produce flaps testing exactly
these). Tradeoff: touches the frozen engine contract mid-weekend; the demo's
storm story gets more credible.

**P-2 — ADR-002: Latency measurement campaign (N≥100; p50/p95/p99; cold vs warm;
verdict Sun AM).** Evidence: jev-behavior note (11.4s N=1 India vs 70–500ms US
spec). Status: PROPOSED/IN-FLIGHT (Oracle). Gates the timeout policy (tight
timeout + fail-open-fast, per the constraint registry) and the entire "inline" claim. Alternatives: trust the vendor spec (rejected —
N=1 already contradicts it from India).

**P-3 — ADR-003: Fallback-engine matrix.** Gateway fallbacks now (Vercel AI
Gateway, Cloudflare Workers AI, OpenRouter, Netlify AI Gateway — same API,
different pipe: same-day 529 resilience, zero model change); Laya
(Apache 2.0, ModernBERT-large) as the concrete open-weights precedent for the
fine-tuned-encoder exit (~33–40ms/query self-hosted; needs domain fine-tuning).
Status: PROPOSED. Alternatives: TypeSafe-only (single point of failure with no
mitigation — rejected).

**P-4 — ADR-004: Rate-limit verification vs live docs; design to 40 req/s meanwhile.**
The 6.25× disagreement (live docs: 100k tok/s + 40 req/s dynamic; community:
250k tok/s + 1,200 req/min) is unresolved. Status: PROPOSED (Oracle + Forge).
Alternatives: trust community docs (designing to the wrong ceiling 529s the
storm path — rejected). This ADR is the formal version of DR-49's posture.

**P-5 — ADR-005: Webhook auth hardening** (raw-body-before-parse, constant-time
compare, empty-secret-refuses, 5-min timestamp tolerance; no IP allowlisting).
Evidence: security-privacy note (industry standard). Status: PROPOSED →
Vault + Forge → **Aditya decides**. Tightens the receiver spec; must reconcile
the "optional IP allowlist" mention in ARCHITECTURE.md §7 (see DR-27).

**P-6 — ADR-006: Postmortem-grade audit explorer** (timeline-first,
Second-Story fields, action-item shape). Evidence: sre-field + ux-freedom notes.
Status: PROPOSED (Prism). Platform feature detail — additive, no frozen-spec
impact. Alternatives: table-of-rows explorer (practitioners reconstruct
timelines by hand today — the timeline-first shape is the product).

**P-7 — ADR-007: Muted-not-dropped visibility state** (dashboard-only, never
paging). Evidence: sre-field note (precedent: sre-ai-copilot). Status:
PROPOSED → Prism + Forge → **Aditya decides**. A fifth disposition visibility
state for chronic noise the buyer wants to *see* but never be paged for —
extends DR-21. Tradeoff: small dashboard addition, outsized buyer resonance;
zero paging-path impact by construction.

**P-8 — ADR-008: Reliability-as-code direction** (versioned per-team threshold
configs in git; "policies as code" per practitioner consensus). Status:
PROPOSED, **post-Sunday**. Alternatives: org-level `thresholds.json` forever
(rejected — practitioners already manage escalation policies as code).

**P-9 — ADR-009: Quarterly competitive price-tracking ritual** (the umbrella
moves — PD AIOps $699 vs $799 across sources already; our anchors track it).
Status: PROPOSED (Relay owns the ritual). Ops ritual, not a spec change.

---

## §3 — Type 1 decisions, OPEN (named gaps)

**O-1 — Audit data retention policy.** Append-only forever (DR-18) is not a
policy. Options: time-based TTL, per-customer retention tiers, WORM archive.
Decider: Aditya (+ legal when it exists). Trigger: the first enterprise design
partner asks — or the first disk-full pre-mortem (§5.3) comes true.

**O-2 — Final pricing.** The band $199–499/mo is proposed (CHARTER.md); flat
$199/mo unlimited seats is named in research as the legible attack on the
per-seat notification tax. Nothing is decided. Decider: Aditya.

**O-3 — EU residency structural answer.** Self-hosted customer-VPC relay
(designed, not built) vs regional inference vs no-EU posture. Decider: Aditya.

**O-4 — Audit integrity next rung: hash-chained log records / WORM.**
Staged as post-Sunday hardening (security-privacy note). Not decided.

**O-5 — Per-team threshold configs** (vs org-level `thresholds.json`).
ADR-008 direction; the config schema is undecided. Decider: Forge
recommends, Aditya decides, post-Sunday.

**O-6 — Label-joiner automation design.** Deferred in v0.1 (ARCHITECTURE.md);
the shadow-report pipeline needs it. Undecided.

---

## §4 — Type 2 register (reversible — speed, not RFC)

These are decided but cheaply reversible. Logged for completeness; they do not
need the §1 treatment.

| Decision | Value | Owner |
|---|---|---|
| Threshold constants | suppress_p1_max 0.002, suppress_conf_min 0.90, page_p1p2_min 0.30, uncertain_conf_max 0.50, queue_conf_min 0.70 | tuner/Forge |
| Cost defaults | C_FP=$100, C_FN=$50,000 (founder-grade; measured later) | Oracle |
| Correlator windows | dedup 300s; storm 20 fingerprints / 60s | Forge |
| Client timeouts | timeout 8.0s; max_retries 3; retry_budget 2.0s | Forge |
| State budget | ~850 tokens; field priority (outcome_history highest value) | Forge |
| Default team options | 6 defaults; customer may supply ≤8 | Prism |
| Tuner grid | suppress_conf_min ∈ {0.80, 0.85, 0.90, 0.95} | Oracle |
| Eval bars | ECE 10-bin; coverage@τ τ∈{0.7,0.8,0.9}; 2,000-alert smoke | Oracle |
| Deployment shape | single box, `python -m sentinel.receiver --port 8080`, env vars | Relay |
| Demo script order | 7-step transcript (ROADMAP.md) | Prism |
| Wave schedule | Sat A/B/C, Sun AM/PM; go/no-go 20:30; no merges after 17:00 Sun | Relay/Petu |
| Lane mechanics | max 2 builders/lane; PRs <300 lines; review SLA minutes | Relay |
| GitHub tooling | PAT path via `gh` CLI canonical; App connector backup | Petu |
| Dashboard copy/UX | all Prism-owned surfaces | Prism |
| Fixture versions | `tests/fixtures/demo-v1/`, seed recorded | Ledger |
| Analytics windows | noise 24h, flips 7d (defaults) | Ledger |
| Runbook content | RB-1/2/3 | Relay |
| This wave's rule | no code until Sat 11:00 IST; research/study/design only (Aditya) | Coordinator |

---

## §5 — Pre-mortem cross-check (Law 6) on the riskiest Type 1s

*It is one year from now. Sentinel caused a missed SEV1. What exactly failed —
in mechanical detail?*

**PM-1 vs DR-2/DR-11 (fail-open / never-5xx).** The forwarder's PagerDuty
endpoint changed its error contract; our passthrough POSTs started 400ing;
alerts neither paged nor errored visibly — the "never drops" path had a hole
*after* the decision. Mitigation in force: forwarder errors → log + metric +
operator alert. Residual: the forwarder's downstream contract needs a
compatibility probe in CI — not currently present. *Flagged, not yet an ADR.*

**PM-2 vs DR-3 (triple lock).** An allowlist entry for `disk-full` on the cache
cluster was verified against last quarter's data; a service rename merged two
check namespaces; a SEV1 `disk-full` on the *database* cluster matched the
stale allowlisted fingerprint with P(p1)=0.0018, conf 0.93 → suppressed.
Mitigation in force: audit explorer surfaces reason codes; DR-6 triggers the
SEV1 process. Residual: allowlist entries are not namespaced/expiry-bound —
*this is the strongest argument for ADR-008 (versioned, reviewable configs).*

**PM-3 vs DR-18 (append-only).** The SQLite WAL grew unbounded (O-1 never
decided); disk filled at 03:12; audit writes failed; the platform tier kept
serving stale dashboards while the engine kept paging — nobody noticed the
audit gap for a week. Mitigation: none in force. *This is exactly why O-1 is
the most urgent OPEN Type 1.*

**PM-4 vs DR-10/DR-14 (one call per alert / storm-collapse).** A 5,000-alert
storm with all-distinct fingerprints sat *below* the storm threshold
configuration for its shape; 5,000 Jev calls fired; 429s cascaded; timeouts
failed open — pages flowed, but the storm story died and the trust demo
inverted. Mitigation in force: bounded queue, shed-dashboard-first (DR-15).
Residual: storm detection is threshold-shape-dependent — *Oracle owns the
sensitivity analysis; not yet scheduled.*

**PM-5 vs DR-5 (shadow-first).** Shadow mode's "would-have" dispositions never
got labels joined (O-6 open); the weekly shadow report shipped with synthetic
labels; a design partner spotted it. Mitigation: DR-36 (source labels) is the
last line of defense. Residual: *O-6 is the open Type 1 that protects the
sales motion.*

---

## Changelog

- 2026-10-03 ~01:30 IST — Register created by Relay (principal-redesign wave).
  Seeded from `ops/decision_log.md`, the 9 proposed ADRs
  (`research/synthesis-2026-10-02.md`), and the frozen specs. 47 DECIDED,
  9 PROPOSED, 6 OPEN Type 1s; Type 2 table; 5 pre-mortems.
- Status fields (`DECIDED`/`PROPOSED`/`OPEN`) are the live state — update the
  entry, never rewrite history; append to this changelog.
