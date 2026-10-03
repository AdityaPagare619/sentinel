# Sentinel — R&D Charter (standing function)

**Status:** standing · **Owner:** Night R&D Coordinator → day-shift R&D lane · **Since:** 2026-10-02
**Parent order:** Aditya — R&D runs DAILY, per department, forever. Last night proved the
*opportunity* (breadth); this function proves the *craft* (depth). Brilliant architecture
comes from solving things fundamentally, not generically — and from understanding the
field more deeply than the vendors do.

## 1. Cadence

- **Daily:** each department owns exactly ONE open research question. The owner spends a
  bounded block (default 60–90 min), web-verified throughout, and lands a dated note in
  `research/<dept>/YYYY-MM-DD-<slug>.md` before the day ends. No note = the question
  rolls over, flagged red in the next brief.
- **Weekly synthesis:** every Sunday, the coordinator reads the week's notes and writes
  `research/synthesis-YYYY-MM-DD.md`: findings → proposed ADRs with evidence. Synthesis
  proposes; it never edits frozen specs.
- **Question backlog:** each department keeps an `agenda.md` (this dir) with standing
  questions ranked by leverage. New questions enter at the bottom; the coordinator
  promotes by expected impact on the platform or the Sunday goal.

## 2. Documentation standard (non-negotiable)

Every research note follows this shape:

1. **Question** — one sentence.
2. **Observed facts** — each with a source URL + access date. No naked claims.
3. **Inferences** — clearly labeled as such, with the facts they rest on.
4. **Honest limitations** — what could not be verified, what might be stale, where
   the source has an incentive to mislead (vendor docs sell; practitioner blogs
   generalize from n=1).
5. **Implications** — proposed ADR numbers, or "none — filed for later."

Facts and inferences are never mixed in one bullet. A note without sources is a
draft, not a finding.

## 3. Storage

- `research/` lives in git (`lane/research-*` branches, conventional commits),
  mirrored to GitHub with everything else. Research is a first-class artifact,
  not a chat transcript.
- Per-department subdirs: `research/sre-field/`, `research/jev-behavior/`,
  `research/competitive/`, `research/security-privacy/`, `research/ux-freedom/`.
- Agendas live at `research/<agenda>.md` (top level); notes live in the subdirs.

## 4. The iron rule: research never silently changes the spec

R&D findings enter the platform **only** via Forge-reviewed ADRs
(`ops/decision_log.md`, `ADR-###` numbered). A finding that challenges
`ARCHITECTURE.md` or `PLATFORM_ARCHITECTURE.md` is flagged in the synthesis as
"CHALLENGES FROZEN SPEC — ADR proposed," never applied quietly. The Claim Auditor
may veto any finding whose sources don't support its inferences.

## 5. Departments

| Dept | Owner | Dir | Mission |
|---|---|---|---|
| SRE field craft | Pager | `research/sre-field/` | How companies actually run alerting/on-call — the craft, not the marketing |
| Jev behavior | Oracle | `research/jev-behavior/` | Jev's real behavior + the fallback-engine survey (Jev first, not only) |
| Competitive | Prism + Forge | `research/competitive/` | What incumbents actually do, charge, and leave open — from live vendor docs |
| Security & privacy | Vault | `research/security-privacy/` | Auth patterns, audit integrity, residency, secret handling — precedents, not vibes |
| UX freedom | Prism | `research/ux-freedom/` | The control SREs lack today — the freedom we give them, sourced from practitioners |

## 6. Tonight (2026-10-02, 19:30–22:30)

First deep sprint, all five tracks, web-verified throughout. SRE craft is the
centerpiece — Aditya's explicit question was whether last night covered the craft
(it didn't; it covered the opportunity). Synthesis lands 22:30–23:15.
