# 2026-10-02 — What incumbents actually do, charge, and leave open

**Question:** What do PagerDuty AIOps, BigPanda, incident.io, and Grafana OnCall
actually offer today — features, pricing, integration surface — and where are
the concrete gaps our wedge slides into? All from live vendor docs.

## Observed facts

**PagerDuty AIOps:**
- Paid add-on starting at **$699/month** (betterstack.com comparison, crawled
  7h ago); spike.sh pricing breakdown (Sep 2026) lists **$799/month**, "not
  included on any plan" — the price moved or tiers differ; both agree it is
  never bundled.
- What it does: ML event correlation/grouping, noise suppression, probable
  origin / root-cause identification, past/related/outlier incident views,
  recent-changes correlation. Event Orchestration: DAG-evaluated rules that
  suppress, route, deduplicate, enrich events *before* incidents are created.
  Operations Console for triage. New: SRE Agent (Spring 2026) for AIOps +
  Advance customers — autonomous anomaly detection and diagnostics.
  (Support.pagerduty.com/main/docs/aiops, crawled 2 days ago; betterstack.com;
  accessed 2026-10-02)
- Pricing is event-consumption draw-down; every accepted event counts, including
  maintenance-window events. Events API: 120 events/min per key default, up to
  10,000 on request. (support.pagerduty.com; accessed 2026-10-02)
- Base plans are per-seat ($41–49/user/mo Business); Advance AI is another
  $415/mo after credits; runbook automation $59–125/user/mo. Reviewers'
  top complaint: pricing scales with users + integrations and strains small
  teams. (spike.sh; infotech.com reviews; accessed 2026-10-02)

**BigPanda:**
- Enterprise AIOps: AI event correlation, automated triage/routing, change
  intelligence (probable cause), customizable workflows. Custom pricing;
  practitioner-reported typical **~$200,000/year**. Enterprise-only motion.
  (signoz.io comparison; origin.peerspot.com reviews; accessed 2026-10-02)

**Grafana OnCall:**
- Open source, $0 starting, TrustRadius 8.3/10 — the free adjacent for
  Grafana-native shops. (trustradius.com; accessed 2026-10-02)

**The structural gap (documented, not asserted):**
- PagerDuty requires a paid seat for *every user who receives notifications*;
  Flashduty's comparison documents the alternative model: only active handlers
  pay (typically 10–20% of the team). Small teams pay for enterprise tiers
  (AIOps, stakeholder licenses) they never use; "there is no lighter version
  at a more affordable price point" for AIOps. Steep configuration learning
  curve (services, policies, schedules, event rules, workflows).
  (flashduty docs comparison; pagerly.io small-teams guide; accessed 2026-10-02)

## Inferences

1. Our wedge thesis (research §8.1) survives contact with live docs: the
   umbrella is real ($8.4k–9.6k/yr AIOps + per-seat), the "no affordable tier"
   gap is explicitly documented, and the per-seat notification tax is a
   named competitor grievance. Flat $199/mo unlimited seats is a legible
   attack on exactly this structure.
2. PagerDuty's Event Orchestration DAG is the closest existing thing to our
   gate — but it is rules the customer writes, not calibrated decisions with
   confidence. Our differentiation (auditable typed contract + calibration,
   not rules) is intact.
3. The SRE Agent (Spring 2026) shows PagerDuty moving toward autonomous
   response — our "only autonomous action is page a human" is the
   counter-position; worth naming explicitly in positioning.
4. Grafana OnCall at $0 means the Grafana-native segment needs a
   "works with your stack" story, not a rip-and-replace story — consistent
   with our 50-line middleware wedge.

## Honest limitations

- Pricing figures move quarterly; $699 vs $799 may be tier/region differences.
  AIOps event-volume pricing details are behind sales conversations.
- Review sites skew toward complaints; feature lists from vendor docs skew
  toward completeness.

## Implications

- Proposed ADR-009 (quarterly competitive price-tracking ritual — the umbrella
  moves, our anchors track it). Positioning note for Prism: name the
  counter-position vs autonomous agents explicitly.
