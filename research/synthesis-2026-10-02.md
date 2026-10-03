# Synthesis — 2026-10-02 (night R&D sprint 1)

**Sprint:** 19:30–22:30 IST · **Tracks:** 5/5 complete · **Coordinator:** Night R&D
**Verdict on Aditya's question first:** he stands correct. Last night covered
the *opportunity* (workflows, pricing, pain, the pick). It did not cover the
*craft* — and tonight's sprint confirms the craft is where the moat details
live. Daily per-department R&D is the right call; one-time research would have
left all of the below on the table.

## Tonight's top 5 findings (sourced)

1. **Our correlator is missing three practitioner-standard behaviors.**
   Industry practice: flap-debounce *reopens the same row* on re-fire;
   resolved-then-refired is a *fresh episode, not a reopen*; incidents are
   *never auto-closed* from a resolved alert; P1/P2 are *never silenced* in
   maintenance windows. Our v0.1 has none of the first three codified.
   (firefightlabs/firefight docs/alerts.md; asiaostrich/universal-dev-standards;
   accessed 2026-10-02) → **ADR-001** (extends frozen ARCHITECTURE.md §3.5 —
   flagged for Forge review, NOT applied).
2. **TypeSafe rate limits disagree by 6.25x between sources.** Our report
   (live docs, Oct 2): 100k tok/s + 40 req/s dynamic. Community docs: 250k
   tok/s + 1,200 req/min. Cause unresolved. → **ADR-004**: verify against live
   docs; design backpressure to the tighter bound (40 req/s) meanwhile.
3. **The fallback map is stronger than last night knew.** Vercel AI Gateway,
   Cloudflare Workers AI, OpenRouter, and Netlify AI Gateway all serve Jev
   *today* — same API, different pipe: same-day 529 resilience with zero model
   change. And Laya (Apache 2.0, ModernBERT-large) is the concrete open-weights
   precedent for the fine-tuned-encoder exit (~33–40ms/query self-hosted;
   needs domain fine-tuning — base checkpoints ≈ chance zero-shot).
   (awesome-jev-typesafe; pongpong/token-savings-notes; accessed 2026-10-02)
   → **ADR-003**.
4. **The sub-second thesis is not yet earned.** Vendor 70–500ms is US West
   Coast; our one real-key call from India measured 11,429ms (N=1 — a trigger,
   not a finding). The latency campaign (N≥100, sizes, times, regions,
   p50/p95/p99 with error bars) is the highest-leverage measurement on the
   board; it gates the timeout policy and the whole "inline" claim. → **ADR-002**
   (Oracle, verdict by Sunday AM).
5. **The wedge is validated by the vendors' own pages.** "No lighter/affordable
   AIOps tier" is explicitly documented; per-seat notification tax (every
   notified user needs a paid seat) is a named practitioner grievance; small
   teams pay for enterprise features they never use. Flat $199/mo unlimited
   seats attacks exactly this structure. PagerDuty's new SRE Agent (Spring
   2026, autonomous response) makes our "only autonomous action is page a
   human" the legible counter-position — name it. → **ADR-009** (quarterly
   price-tracking ritual).

## Proposed ADRs (for Forge review — none applied)

| ADR | Title | Evidence | Spec impact |
|---|---|---|---|
| ADR-001 | Correlator craft alignment (flap reopen, fresh episodes, never-auto-close, P1/P2 never silenced) | sre-field note §Observed | EXTENDS ARCHITECTURE.md §3.5 — Forge review required |
| ADR-002 | Latency measurement campaign (N≥100, verdict Sun AM) | jev-behavior note | None yet — gates Law 1 timeout policy |
| ADR-003 | Fallback-engine matrix (gateways now; Laya as encoder-exit precedent) | jev-behavior note | None — operational readiness |
| ADR-004 | Rate-limit verification vs live docs; design to 40 req/s meanwhile | jev-behavior note | None — backpressure assumption |
| ADR-005 | Webhook auth hardening (raw-body-before-parse, constant-time compare, empty-secret-refuses, 5-min timestamp tolerance; no IP allowlisting) | security-privacy note | Tightens receiver spec — Vault + Forge |
| ADR-006 | Postmortem-grade audit explorer (timeline-first, Second-Story fields, action-item shape) | sre-field + ux-freedom notes | Platform feature detail — Prism |
| ADR-007 | Muted-not-dropped visibility state (dashboard-only, never paging) | sre-field note (precedent: sre-ai-copilot) | Platform feature addition — Prism + Forge |
| ADR-008 | Reliability-as-code direction (versioned per-team threshold configs; post-Sunday) | sre-field + ux-freedom notes | Post-Sunday roadmap item |
| ADR-009 | Quarterly competitive price-tracking ritual | competitive note | Ops ritual — Relay |

**Frozen-spec flags:** ADR-001 is the only proposal touching a frozen spec
(ARCHITECTURE.md §3.5). It is PROPOSED, not applied — Forge reviews, Aditya
decides. Everything else is additive.

## Implications for the Sunday build

- Nothing tonight *breaks* the frozen platform spec. ADR-001/005/007 are the
  three worth deciding before Sunday: correlator semantics affect the demo's
  storm story; webhook hardening is cheap to add now; muted-not-dropped is a
  small dashboard addition with outsized buyer resonance.
- The simulator stays the demo centerpiece — practitioner evidence (shift-left
  "version control for reliability") validates the exact interaction.
- Prism's new UX law from tonight: **provenance on every automated claim** —
  no confidence bar without a denominator, no suppression without a reason
  code. (Anti-pattern literature: unprovenanced automation is worse than none.)

## Tomorrow's R&D questions (2026-10-03)

- **SRE:** Alertmanager inhibition rules + routing trees (live docs);
  severity taxonomy compared (Google vs Atlassian vs PagerDuty) — feeds the
  default severity options.
- **Jev:** EXECUTE the latency campaign (key is live); flip-rate
  characterization on alert-shaped states.
- **Competitive:** incident.io feature/pricing pass; Grafana OnCall
  integration surface (the $0 adjacent).
- **Security:** TypeSafe DPA re-read (scheduled); envelope-encryption
  precedents for the BYOK path.
- **UX:** calibration-visualization UX research (how non-ML practitioners
  read reliability diagrams); 15-minute-onboarding precedents.
