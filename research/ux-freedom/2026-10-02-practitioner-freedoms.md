# 2026-10-02 — The freedoms practitioners wish they had

**Question:** What do on-call practitioners say they wish their alerting tools
let them do — in their own words? Distill the recurring "freedoms" into
interface requirements.

## Observed facts

**Recurring practitioner wishes (distilled from practitioner sources):**
- **"Let me simulate a policy change before it pages someone at 3 AM."**
  The shift-left reliability movement names the missing layer explicitly:
  "version control for reliability" — declare intent, validate against
  reality, generate the artifacts (dashboards, SLOs, alerts, escalation
  policies); check in real time whether a service is in breach.
  (dev.to/rsionnach/shift-left-reliability; accessed 2026-10-02)
- **"Show me why this paged me."** Postmortem practice demands timeline-first
  review: impact start, detection signal, who was notified, first action —
  pure observation before interpretation. Practitioners reconstruct this by
  hand today. (Dan Slimmon's scribe method — blog.danslimmon.com; postmortem
  best-practices synthesis, sparkfabrik; accessed 2026-10-02)
- **"Let me tune my own thresholds without filing a ticket."** Alert-rule
  authoring today means writing PromQL / event rules by hand and deploying
  through CI; per-team tuning is toil. Practitioner consensus: alert changes
  should be code-reviewed and CI-tested — but the *tuning* interaction is
  still expert-only. (asiaostrich/universal-dev-standards "alerts as code";
  accessed 2026-10-02)
- **"Stop waking me for things that always resolve themselves."** The entire
  noise-bench ruleset (EdgeDelta) is practitioners formalizing exactly this:
  flapping/fast-self-clearing/deploy-churn should never page; the craft is in
  the evidence thresholds, which today live in tribal knowledge.
  (edgedelta/noise-bench; accessed 2026-10-02)
- **"One page per incident, pointing at the cause."** Cascading failures
  should produce one actionable page (dependency-aware suppression), not a
  wall of correlated noise to sift through under pressure.
  (codebybilal18/system-design monitoring-and-alerting; accessed 2026-10-02)

**Anti-patterns (what erodes trust):**
- Dashboards nobody looks at; "AI insights" panels with no provenance that
  teach users to ignore the tool. Every automated claim needs its evidence
  attached or it becomes noise itself.
- Blame-adjacent automation: any system that names *who* caused an alert
  instead of *what* the system did — the blameless-culture literature is
  unanimous that this drives information underground.

## Inferences (→ interface requirements)

1. **Threshold simulator** (already P0): the #1 validated freedom — "what-if"
   before 3 AM. Practitioner evidence says this exact interaction is the
   missing layer. Keep it the demo centerpiece.
2. **Decision detail view** ("why did this page me"): every disposition shows
   its evidence — probabilities, input hash, reason code, the timeline the
   scribe would have written. This is the audit explorer's product shape.
3. **Per-team tuning without tickets**: threshold controls must be
   self-service per team, with the simulator as the safety rail (see the
   tradeoff *before* committing). Post-Sunday: versioned in git.
4. **Provenance on every automated claim**: no confidence bar without its
   denominator; no suppression without its reason code. The anti-pattern
   literature says unprovenanced automation is worse than none.

## Honest limitations

- "Practitioner wishes" are distilled from consensus docs and movement
  writing, not from interviews we conducted. Direct practitioner interviews
  (design partners) remain the gold standard — queued.
- The shift-left piece is partly aspirational writing; adoption evidence is thin.

## Implications

- Validates the P0 feature spine (simulator, decision river detail, audit
  explorer) from practitioner evidence rather than our assumptions.
- Proposed: Prism adds "provenance on every claim" as a dashboard-wide UX law.
