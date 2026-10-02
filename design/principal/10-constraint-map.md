# 10 — Constraint Map

Every constraint Sentinel operates under, mapped: where it binds, what breaks
if violated, which decisions it forces. Companion to `09-decision-register.md`
(DR-/P-/O- references point there).

**How to read an entry:**
- **Binding:** the hard limit, with the number.
- **Where it binds:** the component, path, or surface the limit actually touches.
- **What breaks:** the concrete failure if violated — mechanical, not vibes.
- **Decisions it forces:** the register entries that exist *because of* this constraint.

Categories: **T** technical (paging path & platform) · **V** vendor (TypeSafe/Jev) ·
**R** regulatory/compliance · **H** human/operator · **B** business/commercial ·
**P** process/schedule (the machine). §7 covers collisions between constraints.

---

## §1 — Technical: the paging path & the platform

**T-1 — Sub-second inline budget.**
Binding: vendor spec 70–500ms end-to-end (US West Coast); our one real-key call
from India measured **11,429ms** (N=1, 2026-10-02 ~13:28 IST). The spec is
*unearned* until Oracle's N≥100 campaign reports.
Where it binds: the gate, the timeout policy, the "inline" positioning, the demo.
What breaks: if Jev p95 from India is seconds, the inline wedge is false and
Sentinel becomes async triage — the whole positioning (CHARTER.md "Latency is
the product") must be rewritten.
Decisions it forces: DR-10 (one parallel call), P-2 (the latency campaign),
DR-32 (mock-backed demo), and the tight-timeout / fail-open-fast posture
(constraint registry; PLATFORM_ARCHITECTURE.md §4 Law 1).

**T-2 — ONE parallel Jev call per alert.**
Binding: the three questions (severity, team, disposition) go in a single
request, evaluated in parallel; never sequential, never chained.
Where it binds: `client.py` + `gate.py`.
What breaks: sequential calls triple the latency budget and triple the rate-limit
burn — during a storm, that's a 429 cascade.
Decisions it forces: DR-10.

**T-3 — ~40 req/s dynamic rate ceiling (disputed 6.25×).**
Binding: live docs say 100k tok/s + **40 req/s** dynamic; community docs say
250k tok/s + 1,200 req/min. Unresolved — design to the tighter bound.
Where it binds: receiver backpressure, correlator storm-collapse.
What breaks: a storm above the ceiling without collapse → 429/529 cascade →
everything fails open (pages still flow, but the suppression story dies and the
demo inverts).
Decisions it forces: DR-14 (storm-collapse is load-bearing, not an optimization),
P-4 (verification vs live docs), DR-15 (shed order).

**T-4 — 255-choice ceiling per Choice question (API hard limit).**
Binding: Q2 `owning_team` (default 6 options; customers may supply ≤8 — far under).
Where it binds: `questions.py`.
What breaks: a 300-team org can't list teams directly — needs hierarchical
design (Jev picks category ≤255; deterministic retrieval picks the runbook).
Decisions it forces: the registry note — never design a question with >255 options.

**T-5 — Probabilities rounded to 0.01; noul clamped to [0.01, 0.98].**
Binding: threshold math and the simulator.
Where it binds: `gate.py`, `tuner.py`, `POST /api/simulate`.
What breaks: thresholds finer than 0.01 are fiction; the simulator would show
precision the model can't deliver.
Decisions it forces: DR-26 (simulator ≡ tuner), the Type 2 threshold constants.

**T-6 — SQLite WAL; platform holds a read-only connection.**
Binding: the audit log is the ONLY bridge between tiers (DR-9).
Where it binds: `audit.py`, the platform tier.
What breaks: a dashboard query blocking the audit writer adds milliseconds to
the paging path — violating do-no-harm Law 1.
Decisions it forces: DR-9, DR-34; the SaaS upgrade is a Postgres read replica.

**T-7 — Stdlib only: no pip dependencies, engine AND platform tier.**
Binding: ₹0 ops, zero supply-chain surface, runs anywhere.
Where it binds: everything (`http.server`, `urllib`, `unittest`, vanilla JS + SSE).
What breaks: one pip dep = supply-chain surface + the "runs anywhere" claim dies
+ CI needs network installs.
Decisions it forces: DR-33; CI runs stdlib-only.

**T-8 — The receiver never returns 5xx for a triage failure; providers expect
fast ACK (~5s).**
Binding: ingress.
Where it binds: `receiver.py` (`POST /v2/enqueue`, `POST /webhook/generic`).
What breaks: slow/erroring ingress → provider retries and duplicate deliveries;
a 5xx on triage failure is a dropped-alert risk.
Decisions it forces: DR-11; the idempotent-ingest posture (dedupe on delivery ID).

**T-9 — ~850-token state budget (chars/4; truncate lowest-priority first).**
Binding: the Jev `state` object.
Where it binds: `state.py`.
What breaks: unbounded state → token cost + latency per call, compounding T-1
and T-3.
Decisions it forces: the field-priority order — `outcome_history` (30d counts
per fingerprint) is the highest-value field; annotations truncate first.

**T-10 — Bounded receiver queue; shed dashboard/API traffic first, never
paging-path traffic.**
Binding: backpressure under alert spikes.
Where it binds: receiver, platform tier.
What breaks: unbounded queue = OOM under storm; shedding paging traffic =
delayed pages.
Decisions it forces: DR-15.

**T-11 — Bounded client timeout (8.0s) + retry budget (2.0s); 401/422 never retried.**
Binding: the Jev client.
Where it binds: `client.py` (429 honors Retry-After; 529/5xx/timeout →
`JevOverloaded`/`JevTimeout` after budget).
What breaks: unbounded retries = the paging path waits on a dead vendor.
Decisions it forces: DR-2's executable form; the kill-the-client test (DR-43).

**T-12 — Synthetic data proves plumbing, never production accuracy.**
Binding: every output surface.
Where it binds: eval harness, dashboards, demo, marketing.
What breaks: presenting synthetic calibration as production accuracy is a trust
incident — the design partner spots it once, and the relationship is over.
Decisions it forces: DR-7, DR-36, the HONEST LIMITATIONS sections.

---

## §2 — Vendor: TypeSafe / Jev (the load-bearing limits)

**V-1 — Non-deterministic: 1.3–2.2% flip rate, no seed exists.**
Binding: every Jev call can flip on repeat; the floor is irreducible.
Where it binds: gate policy, CI probes, audit schema, the dashboard's honesty copy.
What breaks: a flip on a SEV1-class alert → false suppress; unexplained flips
destroy the calibration story.
Decisions it forces: DR-3 (the triple lock exists *because* of this), DR-20
(`input_sha256` + `jev_model` per row — flips are auditable, never mysterious),
DR-44 (repeatability probes), DR-6, do-no-harm Law 3 ("flips are audited, never
hidden" — said on the dashboard itself).

**V-2 — No SLA, no SOC 2, US-only hosting.**
Binding: the vendor dependency.
Where it binds: deployment architecture, enterprise readiness.
What breaks: a TypeSafe outage is our outage unless fail-open holds; enterprise
security review stalls without SOC 2.
Decisions it forces: DR-2, DR-48 (SPOF accepted with mitigations), P-3
(gateway fallbacks), the designed-but-unbuilt customer-VPC relay.

**V-3 — Liability capped at ~max(12mo fees, $50) vs a missed page costing
$300K–$5M/hr.**
Binding: business risk.
Where it binds: the trust model.
What breaks: one missed SEV1 = company-ending, with no vendor recourse.
Decisions it forces: DR-3, DR-5, DR-6 — the entire trust architecture is priced
against this asymmetry.

**V-4 — MCA forbids distilling Jev outputs.**
Binding: the fine-tuned-encoder exit strategy.
Where it binds: the post-Sunday roadmap.
What breaks: training the exit *on Jev outputs* breaches the contract.
Decisions it forces: DR-48's exit design — bank 200–500 *outcome labels* from
shadow pilots (ground truth), not Jev outputs.

**V-5 — Dedicated 529 code; dynamic rate limits; 429 honors Retry-After.**
Binding: client resilience.
Where it binds: `client.py` error taxonomy (`JevAuthError`/`JevRateLimited`/
`JevOverloaded`/`JevTimeout`).
What breaks: treating 529 like 500 (retry storm) vs like 429 (back off).
Decisions it forces: the error taxonomy, P-3 (gateway fallbacks for 529s).

**V-6 — Fallbacks exist: 4 gateways serve Jev today (same API, different pipe);
Laya (Apache 2.0, ModernBERT-large) is the open-weights precedent —
~33–40ms/query self-hosted, but base checkpoints ≈ chance zero-shot, needs
domain fine-tuning, weaker beyond ~20–50 choices.**
Binding: resilience strategy and the encoder exit.
Where it binds: ops runbooks, post-Sunday roadmap.
What breaks: nothing if designed; the 529-storm scenario (Sept 2026 precedent)
has a same-day answer.
Decisions it forces: P-3; the "Jev first, never only" posture.

**V-7 — Inference economics: ~$66/yr per org; the real cost edge is ~25–40×,
not the vendor's 40–400×.**
Binding: pricing narrative honesty.
Where it binds: charter economics, sales copy.
What breaks: claiming 400× = Claim-Auditor kill; the number doesn't survive
contact with a buyer's spreadsheet.
Decisions it forces: DR-7; "price against avoided toil, never against COGS."

---

## §3 — Regulatory / compliance

**R-1 — EU data residency: unresolved at v0.1.**
Binding: no EU customer data through the US-only TypeSafe API.
Where it binds: customer targeting, wire-format minimization (alert metadata
only — never more customer data than triage needs).
What breaks: regulatory exposure in the first EU design-partner conversation.
Decisions it forces: DR-17; O-3 (structural answer: self-hosted relay vs
regional inference vs no-EU).

**R-2 — PHI: nothing until TypeSafe signs a BAA (12+ months).**
Binding: customer targeting.
Where it binds: the non-goals list (CHARTER.md).
What breaks: HIPAA exposure.
Decisions it forces: the explicit non-goal — decided, not deferred vaguely.

**R-3 — Security/compliance alerts are adversarial; never auto-dismissed,
never auto-dispositioned.**
Binding: disposition policy.
Where it binds: the gate (security-team routing exists; suppression of
security alerts does not).
What breaks: an attacker silencing their own trail; tail liability.
Decisions it forces: DR-16.

**R-4 — No audit retention policy decided.**
Binding: the append-only log (DR-18) grows forever.
Where it binds: `audit.py`, disk, the platform tier.
What breaks: unbounded WAL growth → disk-full → audit writes fail → the
platform serves stale dashboards while the engine pages on — a silent audit
gap (pre-mortem PM-3 in the register).
Decisions it forces: O-1 — the most urgent OPEN Type 1.

**R-5 — TypeSafe DPA re-read scheduled (follow-up from the security-privacy
sprint note).**
Binding: enterprise readiness.
Where it binds: legal posture.
What breaks: unsigned data-processing terms block enterprise pilots.
Decisions it forces: queued follow-up — named, not forgotten.

---

## §4 — Human / operator (the 3 AM human)

**H-1 — 3 AM cognitive load: one page per incident, pointing at the cause.**
Binding: disposition UX and alert content.
Where it binds: correlator (dedup/storm), the decision river, forwarder payloads.
What breaks: a wall of correlated noise under pressure = the product failed at
its only job.
Decisions it forces: DR-14 (page once per storm), DR-21 (queue vs page vs
passthrough are distinct states), P-6 (timeline-first audit explorer).

**H-2 — Uncertainty pages; suppress is never the default.**
Binding: the gate.
Where it binds: every threshold, every question design.
What breaks: the operator learns the tool drops things it doesn't understand —
trust evaporates permanently.
Decisions it forces: DR-2, DR-3, DR-12.

**H-3 — "Show me why this paged me": provenance on every automated claim.**
Binding: every dashboard surface.
Where it binds: decision river detail view, audit explorer, calibration charts.
What breaks: unprovenanced automation is worse than none — practitioners learn
to ignore the tool (ux-freedom anti-pattern literature).
Decisions it forces: DR-36, P-6, Prism's UX law.

**H-4 — 15-minute onboarding, stopwatch-verified.**
Binding: the integration funnel.
Where it binds: the 50-line snippet, BYOK UX, guided storm.
What breaks: a 2-hour integration = the wedge (B-5) dies at the door.
Decisions it forces: DR-35; the onboarding is timed weekly, regressions get fix
tasks.

**H-5 — Blameless postmortem culture; a false suppress is a SEV1.**
Binding: ops culture.
Where it binds: the incident process (METHODOLOGY.md §6).
What breaks: blame-adjacent automation drives information underground (the
blameless literature is unanimous).
Decisions it forces: DR-6; Second-Story fields in P-6.

**H-6 — Rotation math (Google SRE Book floors: 8 engineers single-site,
5–6/site follow-the-sun, ≤25% time on-call, ≤2 pages/shift).**
Binding: the noise-analytics narrative.
Where it binds: analytics v1 (pages/night before vs after Sentinel).
What breaks: below the floors, alert *quality* dominates headcount — a 4-person
team with clean alerts sustains; a 6-person team with noisy alerting burns out
regardless. This is the buyer's pain, quantified.
Decisions it forces: the analytics surface exists to show *their* pain dropping.

**H-7 — Escalation craft (3 tiers L1→L2→L3; always ≥2 levels; stagger
schedules; anchor on a manager; urgency models; policies as code).**
Binding: per-team tuning and realism review.
Where it binds: Pager's disposition-defensibility review; ADR-008 direction.
What breaks: thresholds tuned without understanding escalation reality page the
wrong human at 3 AM.
Decisions it forces: P-1 (P1/P2 never silenced), P-8 (reliability-as-code).

---

## §5 — Business / commercial

**B-1 — ₹0 ops budget. Hard.**
Binding: everything — tools, infra, services.
Where it binds: stdlib-only (T-7), single-box deploys, Vercel only when needed,
no paid anything.
What breaks: any fee, subscription, or paid tier.
Decisions it forces: DR-33, DR-7 (stdlib CI), "no premature deploys."

**B-2 — Sunday 2026-10-04 21:00 IST hard deadline + the scope boundary.**
Binding: what ships this weekend.
Where it binds: the wave plan (Sat A/B/C, Sun AM/PM), the P0/P1 split.
What breaks: slipping scope = missed demo; the boundary is what makes the date
credible.
Decisions it forces: DR-30, DR-31, DR-32; no merges after 17:00 Sunday except
stop-the-line fixes; "that's post-Sunday" is a complete sentence.

**B-3 — Design-partner trust IS the product.**
Binding: every honest constraint (T-12, H-3, V-1's dashboard copy).
Where it binds: dashboards, demo script, sales motion.
What breaks: one trust incident (demo data presented as production, a hidden
flip) = a lost partner and a dead reference.
Decisions it forces: DR-7, DR-36, DR-6, DR-5.

**B-4 — Pricing wedge: flat $199–499/mo vs the $8.4–9.6k/yr PagerDuty AIOps
umbrella + per-seat notification tax ($41–49/user/mo; every notified user needs
a paid seat).**
Binding: positioning and packaging.
Where it binds: charter economics, the simulator's "avoided cost" framing.
What breaks: competing on COGS ("cheap AI") — nobody buys price-per-token;
buyers buy defensibility and reclaimed hours.
Decisions it forces: "price against avoided toil, never against COGS"; O-2
(final pricing undecided); P-9 (track the umbrella — it already moved
$699→$799 across sources).

**B-5 — 95–98% of alerts are noncritical noise; the wedge is integration
(50-line snippet), not rip-and-replace.**
Binding: product shape.
Where it binds: the receiver (PD Events API v2 shape — existing integrations
don't break), onboarding.
What breaks: rip-and-replace = lost to incumbents (nobody leaves PagerDuty);
Grafana OnCall at $0 means the Grafana-native segment needs "works with your
stack."
Decisions it forces: DR-29, DR-35, the PD-v2-compatible ingress.

**B-6 — Shadow pilots (2 weeks) precede any page change; the weekly shadow
report is the sales collateral.**
Binding: deployment and GTM.
Where it binds: `Gate(shadow=True)`, the label pipeline.
What breaks: changing pages without proof = trust incident; the report is the
top of funnel.
Decisions it forces: DR-5; O-6 (label-joiner automation is the open piece).

**B-7 — Repo goes public at Preview-1.**
Binding: visibility and hygiene.
Where it binds: secrets handling, docs quality.
What breaks: a leaked key or a sloppy README in public.
Decisions it forces: DR-47; CI secrets-grep; "never request the raw key again."

**B-8 — No "cheap AI" positioning, anywhere.**
Binding: messaging.
Where it binds: charter, demo narrative, pricing page (post-Sunday).
What breaks: the trust sale — buyers buy defensibility, avoided cost with
proof, or reclaimed hours.
Decisions it forces: DR-7; the calibration-report headline.

---

## §6 — Process / schedule (the machine)

**P-1 — Working hours 10:00–19:00 IST at full intensity; briefs 09:30/morning
and end-of-day; no ad-hoc daytime dumps.**
Binding: agent scheduling.
Where it binds: the wave plan (Sat Wave A reconciled to 10:00 start, 09:30
briefing).
What breaks: coordination overhead eats the build window.
Decisions it forces: the wave structure itself.

**P-2 — Red main is stop-the-line.**
Binding: merges.
Where it binds: every lane.
What breaks: building on a broken base.
Decisions it forces: DR-40.

**P-3 — Review must come from a DIFFERENT agent; `gh pr review --approve`
rejects as self-approval because GitHub identity is Aditya's for all agents —
review *content* is the control.**
Binding: the PR flow.
Where it binds: all merges (PRs #1–#6 merged on content review).
What breaks: rubber-stamp merges.
Decisions it forces: DR-40's no-self-merge rule, adapted honestly.

**P-4 — PRs <300 lines; max 2 builders per lane; review SLA minutes.**
Binding: wave velocity.
Where it binds: Wave B's six lanes.
What breaks: giant PRs = review latency = the weekend dies in review.
Decisions it forces: the lane/branch-per-unit structure.

**P-5 — Single-owner rule.**
Binding: file writes.
Where it binds: every lane (base setup owns docs; build coordinator owns
`src/`/`tests/`/`PROGRESS.md`/`ARCHITECTURE.md`).
What breaks: two writers racing on one artifact path corrupt results (learned
in the TFB campaign).
Decisions it forces: DR-41; LOCAL-OPS.md §2.

**P-6 — Wave A exit bar gates Wave B (Forge's contracts + mocks, or Wave B
doesn't start).**
Binding: the schedule.
Where it binds: Saturday 10:00–12:00 → 12:00–19:00.
What breaks: building features against uncontracted interfaces = integration
weekend.
Decisions it forces: Relay enforces the gate.

**P-7 — GitHub Actions `startup_failure` is repo-level (proven by a minimal
probe workflow); Tripwire is investigating; docs PRs merge on review meanwhile.**
Binding: CI trust.
Where it binds: merges to main.
What breaks: treating CI-green as the gate when CI itself is broken = false
confidence; blocking everything on it = the weekend stalls.
Decisions it forces: the logged exception — merges on review content while the
repo-level failure is repaired. Not normalized; tracked.

**P-8 — Principal-redesign wave: no code until Sat 11:00 IST; research, study,
design only (Aditya's halt).**
Binding: this wave.
Where it binds: all chiefs.
What breaks: implementation before the re-derivation = the old mistakes,
faster.
Decisions it forces: this register and this map.

**P-9 — Claim-Auditor pass on every number a PR asserts (source or UNVERIFIED).**
Binding: PRs, dashboards, briefs.
Where it binds: the Definition of Done.
What breaks: an unverified number in the demo = a trust incident (B-3).
Decisions it forces: DR-42.

---

## §7 — Constraint collisions (where constraints fight each other)

**T-1 vs B-2.** The latency campaign may not report by Sunday; the demo must
not depend on it. Resolution in force: DR-32 (mock-backed demo) + the honest
caveat on the dashboard. The campaign gates the *claim*, not the *weekend*.

**V-1 vs DR-3.** The flip floor is irreducible; the triple lock is the price of
using Jev at all. There is no engineering that removes this tension — only
auditing (DR-20) and the allowlist (human-verified ground truth).

**B-1 vs quality.** ₹0 + stdlib is a constraint, not a quality excuse. The
standing bar ("harder and broader than the other teams") is how the tension
resolves: fewer tools, more rigor.

**R-4 vs DR-18.** Append-only (decided) without a retention policy (open) is a
known debt with a named owner and trigger (O-1). The register does not hide it.

**P-7 vs P-2.** CI is broken at the repo level while "red main stops the line."
Resolution: the exception is logged and scoped (docs PRs on review content),
not normalized — Tripwire owns the repair.

**H-1 vs T-3.** "One page per incident" wants per-alert fidelity; the rate limit
wants collapse. Resolution: storm-collapse pages once *with counts by service* —
fidelity is aggregated, not lost.

---

## Appendix — the three tightest constraints

**1. T-1 — the sub-second inline thesis is unearned.** 11.4s measured (N=1,
India) vs 70–500ms spec (US West Coast). Everything downstream — the timeout
policy, the "latency is the product" positioning, the inline architecture —
rests on Oracle's N≥100 campaign. It is the highest-leverage measurement on
the board; until it reports, the product's core claim is an open question
carried by the fail-open design, not by evidence.

**2. V-1 + V-2 + V-3 — the vendor is untrustworthy by construction.**
1.3–2.2% irreducible flips, no seed, no SLA, no SOC 2, US-only, ~$66 liability
cap against $300K–$5M/hr of missed-page exposure. The *entire trust
architecture* — triple lock, fail-open, append-only audit with input hashes,
shadow-first, repeatability probes, the SEV1 false-suppress rule — exists
because the load-bearing dependency cannot be trusted. This is the constraint
that shapes the most decisions.

**3. B-2 — Sunday 2026-10-04 21:00 IST with a hard scope boundary.** The date
forces the mock-backed demo, the P0/P1 split, the no-new-tables freeze, and
the 17:00 merge cutoff. It is the constraint that turns every "should we…"
into "that's post-Sunday" — the most operationally binding of the three.

*Runner-up:* **T-3** — the 40 req/s ceiling (disputed 6.25×) makes
storm-collapse load-bearing at scale; a misconfigured storm shape is the most
likely mechanical failure of the paging path (register PM-4).

---

## Changelog

- 2026-10-03 ~01:30 IST — Map created by Relay (principal-redesign wave).
  Seeded from `ops/constraint_registry.md`, the five research sprint notes,
  `research/synthesis-2026-10-02.md`, and the frozen specs. 12 technical,
  7 vendor, 5 regulatory, 7 human, 8 business, 9 process constraints;
  6 collisions; 3 tightest named.
