# Research agenda — SRE field craft

**Owner:** Pager (Chief SRE / Field Marshal) · **Dir:** `research/sre-field/`
**Mission:** the craft of running alerting and on-call inside real companies —
escalation policy design, rotation structures, alert taxonomy in the wild,
dedup/storm practices, incident-review culture, and the exact semantics of
PagerDuty vs Opsgenie vs Alertmanager. Last night covered the *opportunity*
(workflows, pricing, pain); this track covers the *craft* — how practitioners
actually do it, so Sentinel's policies, taxonomy, and UX match reality instead
of our imagination.

## Tonight's question (2026-10-02, centerpiece)

**Q1:** How do competent SRE orgs actually design escalation policies and
rotations, and where exactly does alert noise enter the system — i.e., what are
the concrete, practitioner-documented practices (dedup windows, storm handling,
severity definitions, rotation handoffs) that Sentinel's correlator thresholds
and disposition policy must mirror to be credible?

## Standing backlog (ranked by leverage)

1. **Alert taxonomy in the wild:** what do P1–P4 / SEV1–5 actually mean across
   orgs (Google SRE book, Atlassian incident handbook, PagerDuty's own taxonomy)?
   Where do orgs disagree, and what does that imply for our default severity
   options?
2. **Dedup and storm practices:** fingerprint windows, flap detection, and
   storm-collapse conventions practitioners actually use. What windows do they
   pick and why? (Feeds `correlator.py` tuning + the synthetic generator.)
3. **Incident-review culture:** blameless postmortem practice (Etsy, Google) —
   what data do reviewers wish they had? (Feeds the audit explorer's fields.)
4. **Vendor semantics, verified live:** PagerDuty Events API v2
   (`routing_key`, `dedup_key` lifecycle, `event_action`), Opsgenie alert API,
   Alertmanager routing trees + inhibition rules. One adapter per week, checked
   against live docs, logged in `ops/payload-verification.md`.
5. **The 3 AM test, sourced:** practitioner accounts of the worst on-call
   nights — what would they have paid to suppress, and what must never be
   suppressed? (Calibrates the triple-lock's allowlist philosophy.)
6. **Rotation structures:** weekly vs follow-the-sun vs "you build it, you run
   it" — who owns the page, and what does that imply for per-team threshold
   tuning UX?

## Sources to mine

- Google SRE workbook/books (sre.google), Atlassian incident management handbook,
  PagerDuty docs (docs.pagerduty.com), Opsgenie docs, Prometheus Alertmanager docs.
- Practitioner writing: SREcon talks, ;login: articles, Honeycomb/Charity Majors
  on on-call, Dan Luu on alerting, incident-review write-ups (e.g. public
  postmortems).
- Community: r/sre, Hacker News on-call threads (practitioner consensus, n>1).

## Output contract

Dated notes in `research/sre-field/` per the charter's documentation standard.
Every practice cited gets: who does it, where documented, URL + access date.
Pager's "3 AM test" applies to every candidate policy before it becomes an ADR.
