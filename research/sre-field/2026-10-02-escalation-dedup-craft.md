# 2026-10-02 — Escalation, dedup & storm craft: how practitioners actually do it

**Question:** How do competent SRE orgs actually design escalation policies and
rotations, and where exactly does alert noise enter the system — what concrete,
practitioner-documented practices must Sentinel's correlator thresholds and
disposition policy mirror to be credible?

## Observed facts

**Escalation policy shape (convergent across sources):**
- The standard structure is 3 tiers: L1 primary on-call (acknowledge in
  5–15 min) → L2 secondary/team lead (15 min) → L3 manager (30 min), with the
  policy re-cycling 2–3 times if nobody acknowledges.
  (github.com/trevoredris/fellowship-of-the-workflows SKILL.md; github.com/abdulmalikalayande/sorokeep docs/examples/pagerduty-escalation.md; accessed 2026-10-02)
- Practitioner rule: always ≥2 escalation levels — a single-level policy has no
  fallback; stagger primary and secondary schedules so the same person is never
  on both; anchor the end of the policy on a manager.
  (github.com/dheerajreddy01/claude-skills pagerduty SKILL.md; pagerduty.com/blog — "On-Call Scheduling Best Practices"; accessed 2026-10-02)
- PagerDuty urgency model: high/low urgency per incident determines push/SMS/phone
  vs quiet channel; urgency can change by time-of-day. A user with only email
  notification rules is effectively never paged.
  (github.com/dheerajreddy01/claude-skills pagerduty SKILL.md; accessed 2026-10-02)
- "Manage as code": escalation policies and schedules kept in Terraform /
  version control so changes are reviewable and diffable; re-validate after ANY
  roster change, not just at setup — a correct policy silently develops gaps.
  (github.com/selvarajmurugesan90/ops-engineering-skills pagerduty-and-opsgenie SKILL.md; accessed 2026-10-02)

**Dedup / storm / flap craft (the layered doctrine):**
- Storm control is layered because every layer can be bypassed: per-source rate
  limit → redelivery idempotency → fingerprint dedup → flap debounce → grouping
  → digest throttle → **never auto-close**. Removing any layer turns a flapping
  monitor into thousands of notifications.
  (github.com/firefightlabs/firefight docs/alerts.md; accessed 2026-10-02)
- Fingerprint convention: `service + alert_name + labels` (or
  `{service}:{alert_rule}:{env}` for dedup keys); dedup window ~5 minutes;
  merged notification carries the trigger count ("Triggered 12 times in last 5
  minutes"); window resets on resolve.
  (github.com/asiaostrich/universal-dev-standards core/alerting-standards.md; accessed 2026-10-02)
- Alertmanager primitives: `group_by`, `group_wait: 30s`, `group_interval: 5m`;
  inhibition rules and silences for dependency-aware suppression (when the DB is
  down, suppress downstream "can't reach DB" pages — page the root cause once).
  (prometheus.io/docs/alerting/latest/alertmanager/ via datatalksclub research note; accessed 2026-10-02)
- Flap dampening: `for:` duration (fire only if true for N minutes), hysteresis
  (fire at 90%, resolve at 80%), percentage-based thresholds for multi-instance
  services. Flap debounce: re-fire inside the flap window *reopens the same
  row*; a resolved-then-refired alert is a **fresh episode, not a reopen**.
  (asiaostrich/universal-dev-standards; firefightlabs/firefight; accessed 2026-10-02)
- Maintenance windows: scoped to the affected service, time-bound, reason
  attached — never mute a whole team's paging; **P1/P2 are never silenced**
  (safety override); windows logged for audit.
  (asiaostrich/universal-dev-standards; accessed 2026-10-02)
- Noise-bench decision rules (EdgeDelta): genuinely ambiguous → SAFE default =
  page; deploy-correlated + self-healing → suppress (expected rollout churn);
  deploy-correlated + climbing + not self-healing → page even mid-deploy-storm.
  (github.com/edgedelta/noise-bench datasets/noisebench/deploy-storm/instruction.md; accessed 2026-10-02)
- Muted-not-dropped precedent: chronic-noise alert classes rendered *muted*
  (visible for eyeballing, never paging) rather than dropped — a middle ground
  between suppress and page, with per-class kill-switches.
  (github.com/froggychips/sre-ai-copilot README.md; accessed 2026-10-02)

**Rotation math:**
- Google SRE Book floor: 8 engineers single-site (primary+secondary), 5–6 per
  site follow-the-sun; ≤25% of time on-call; ≤2 pages per shift; hand-off
  meeting at shift change.
  (medium.com/@martin_16641 solo-founder playbook citing SRE Book ch.11; ritesh-18/revision-material SRE handbook; accessed 2026-10-02)
- Below the floors, alert quality dominates headcount: a 4-person team with
  clean alerts sustains indefinitely; a 6-person team with noisy alerting burns
  out regardless. "You build it, you run it" — owners carry the pager.
  (levelup.gitconnected.com in-house-vs-outsourced-sre; sameeralam3127/compute-central-docs; accessed 2026-10-02)

**Postmortem culture (feeds the audit explorer):**
- Blameless postmortem: Etsy/Allspaw 2012, Google SRE Book ch.15. Core
  mechanics: timeline first (pure observation before interpretation), Second
  Story over First Story (human error as *effect* of system design, not cause),
  hindsight-bias correction (Cook), action items tracked to completion,
  postmortems written even for non-paging incidents ("even more valuable").
  "Blameless" ≠ "sanctionless" — people can tell the difference (STELLA).
  (github.com/raphaelthomas/tech-operator-crm-cards; github.com/andrefigueira/engineering-codex; github.com/jeffreytse/grimoire-core; accessed 2026-10-02)

## Inferences

1. Our correlator's 300s window and fingerprint scheme match practitioner
   convention — good. The gaps vs the craft: we lack flap-debounce *reopen*
   semantics, resolved-then-refired episode handling, and the
   never-auto-close rule. Our synthetic generator should produce flaps that
   test exactly these.
2. The "ambiguous → page" rule from noise-bench is already our Law 2
   (uncertainty pages) — independently convergent, which raises confidence.
3. The muted-not-dropped pattern is a genuine product idea our disposition
   set lacks: a fourth visibility state for chronic noise the buyer wants to
   *see* but never be paged for. Dashboard-only, zero paging-path impact.
4. "Policies as code" is a strong post-Sunday direction: per-team threshold
   configs versioned in git matches how practitioners already manage
   escalation policies.

## Honest limitations

- Sources are practitioner-consensus docs and engineering blogs (n>1, but not a
  controlled study). Rotation math comes from Google-scale guidance; small-team
  translation is inferred, flagged as such.
- Vendor-adjacent sources (PagerDuty blog) have incentive to normalize their
  own primitives; cross-checked against vendor-neutral sources where possible.

## Implications

- Proposed ADR-001 (correlator craft alignment), ADR-006 (postmortem-grade
  audit explorer), ADR-007 (muted-not-dropped visibility state), ADR-008
  (reliability-as-code direction, post-Sunday).
