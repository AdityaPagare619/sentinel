# Research agenda — UX freedom

**Owner:** Prism (Chief Product/Design) · **Dir:** `research/ux-freedom/`
**Mission:** the control SREs lack today — the "freedom they didn't have" that
Sentinel's interface gives them. Aditya's thesis: the moat is partly the UX that
hands users power incumbents never did. This track sources that power from
practitioner writing, talks, and postmortems — never from our imagination of
what SREs want.

## Tonight's question (2026-10-02)

**Q1:** What do on-call practitioners say they wish their alerting tools let
them do — in their own words (blogs, talks, postmortems, threads)? Distill the
recurring "freedoms" (e.g., "let me tune my own thresholds without a ticket,"
"show me why this paged me," "let me simulate a policy change before it pages
someone at 3 AM") into interface requirements for the platform.

## Standing backlog (ranked by leverage)

1. **The simulator's cousins:** who already does "what-if" policy simulation
   well (inside or outside alerting) — what interaction patterns work?
2. **Calibration UX:** how do non-ML practitioners read reliability diagrams,
   confidence bars, ECE? What visual language earns trust vs confuses?
   (Practitioner-tested, not designer-assumed.)
3. **Onboarding friction:** documented 15-minute-integration precedents — what
   makes a developer integration feel trivial vs feel like a project?
4. **Alert-rule authoring pain:** how SREs write/tune alert rules today
   (Prometheus, PagerDuty event rules) — the toil our threshold UX must absorb.
5. **The demo moment:** practitioner accounts of tools that won them over in
   one session — what was the interaction? (Design the Sunday demo's "holy
   shit" beat from evidence.)
6. **Anti-patterns:** dashboards nobody looks at, "AI insights" panels that
   eroded trust — what to never build, with sources.

## Sources to mine

- Practitioner blogs (Honeycomb, Grafana Labs blog, PagerDuty blog engineering
  posts), SREcon talks, public postmortems with "what we'd change about our
  alerting" sections, r/sre and HN threads on alerting tools.
- UX research on trust in automation (appropriate, sourced — no pop psych).

## Output contract

Dated notes in `research/ux-freedom/` per the charter. Practitioner quotes are
quoted with attribution + URL. Every "freedom" maps to a concrete interface
requirement or is filed as inspiration, labeled as such. Prism's anti-slop veto
applies to our own notes: no generic personas, no invented users.
