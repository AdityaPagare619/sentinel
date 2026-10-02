# Research agenda — Competitive

**Owners:** Prism + Forge · **Dir:** `research/competitive/`
**Mission:** what the incumbents actually do, charge, and leave open — from live
vendor docs, never from memory. PagerDuty AIOps, BigPanda, incident.io, Grafana
OnCall, and the adjacent automation layer. We win on price + auditable typed
contract + vendor-neutrality (research §8.1) — this track keeps that claim
honest and finds the gaps we exploit next.

## Tonight's question (2026-10-02)

**Q1:** What do PagerDuty AIOps, BigPanda, incident.io, and Grafana OnCall
actually offer today — features, pricing, integration surface — and where are
the concrete gaps (price, auditability, vendor lock-in, integration friction)
that Sentinel's wedge slides into? All from live vendor docs, accessed tonight.

## Standing backlog (ranked by leverage)

1. **PagerDuty AIOps, precisely:** event correlation, probable-cause, automation
   actions — what each does, what it costs ($699/mo + usage), what data it needs.
   Where does it require rip-and-replace vs sit alongside?
2. **BigPanda:** correlation engine claims, pricing model, who buys it and why.
   What does "noisy" cost there?
3. **incident.io:** the modern incident lifecycle — what they own (declarations,
   follow-ups, AI summaries) and what they deliberately don't (triage gating?).
4. **Grafana OnCall / Alertmanager ecosystem:** the open-source adjacent — what
   a Grafana-native shop does about noise today, and whether our 50-line
   middleware fits their stack.
5. **The "Switzerland" gap:** which tools are vendor-neutral vs
   ecosystem-captured — documented evidence for our positioning, not slogans.
6. **Pricing-page archaeology:** quarterly re-check of all four vendors' pricing;
   the umbrella we price against moves, and our $199/$799 anchors must track it.

## Sources to mine

- Live vendor docs and pricing pages (accessed tonight, re-checked quarterly).
- G2/Capterra reviews (practitioner complaints = gap list).
- Vendor changelogs (what they're building tells you what they fear).

## Output contract

Dated notes in `research/competitive/` per the charter. Feature claims cite the
doc URL + access date. Pricing numbers are screenshots-in-words (plan name,
figure, billing unit). No "everyone knows" — if it's not on a live page, it's
UNVERIFIED.
