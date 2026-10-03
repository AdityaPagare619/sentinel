# Prism Design System — Sentinel platform v0.1

**Status:** spec, not implementation. This document is the visual and interaction
contract every screen spec under `design/screens/` builds on.

**Design philosophy:** Sentinel is a 3 AM instrument, not a dashboard.
The person looking at this screen has been woken up, is holding a phone,
and has ninety seconds to decide whether the machine made the right call.
Every pixel choice below serves that person.

---

## 1. UX laws (non-negotiable)

These are laws, not guidelines. A screen that violates one fails review.

| # | Law | Testable acceptance criterion |
|---|-----|-------------------------------|
| L1 | **Provenance on every automated claim.** | No confidence bar renders without its denominator (n = evaluated cases). No `SUPPRESS` chip renders without its reason code. If the evidence is missing, the component renders the error state, never a naked number. |
| L2 | **Every view labels its data source.** | Each screen and each projection carries a source badge: `synthetic`, `shadow`, or `production`. No badge may be ambiguous or absent — including during loading (badge shows first, skeleton after). |
| L3 | **Honest scope — no accuracy marketing.** | We never claim accuracy, precision, or recall on production data. We report calibration (ECE), coverage-at-threshold with denominators, and suppression rates on labeled shadow/synthetic sets. The word "accurate" does not appear in the UI. |
| L4 | **Blameless by construction.** | The interface attributes behavior to *signals, policies, and decisions* — never to people. No "caused by" attribution to a person, no deployer names next to suppressions. Deploy hashes are facts about the system; blame is not a feature. |
| L5 | **Reads before writes.** | All platform paths are read-only. The UI can never change paging behavior directly — threshold changes are exported as policy diffs for review. Zero Jev calls on read paths; every screen states the dataset version it read. |

---

## 2. Design tokens

### 2.1 Color — dark-ops palette

Backgrounds are near-black with a cold blue bias (reduces eye strain at 3 AM
on AMOLED; no pure white anywhere).

| Token | Value | Use |
|---|---|---|
| `--bg-0` | `#06090F` | app chrome, river background |
| `--bg-1` | `#0B111A` | cards, drawers, panels |
| `--bg-2` | `#111826` | hover / raised surfaces |
| `--bg-3` | `#1A2436` | pressed / selected surfaces |
| `--line-0` | `#1E2A3E` | hairline dividers |
| `--line-1` | `#2C3B55` | emphasized borders |
| `--tx-0` | `#E8EEF7` | primary text |
| `--tx-1` | `#9AA9C4` | secondary text |
| `--tx-2` | `#5D6E8C` | tertiary / metadata |
| `--tx-dim` | `#3A4A63` | disabled / placeholder |

**Severity scale** (chips, row gutters — never large filled areas):

| Token | Value | Meaning |
|---|---|---|
| `--sev-1` | `#FF5470` | SEV1 — page-worthy now |
| `--sev-2` | `#FF9F43` | SEV2 — urgent, degrades soon |
| `--sev-3` | `#FFD166` | SEV3 — needs attention today |
| `--sev-4` | `#7BDFF2` | SEV4 — informational |

**Disposition scale:**

| Token | Value | Meaning |
|---|---|---|
| `--disp-page` | `#FF5470` | PAGE — a human was woken |
| `--disp-suppress` | `#8A99B5` | SUPPRESS — machine stood down (reason code required) |
| `--disp-escalate` | `#FF9F43` | ESCALATE — routed up a tier |
| `--disp-defer` | `#5D6E8C` | DEFER — held for correlation window |

**Data-source badges** (Law L2 — these colors are reserved, never reused for
severity or disposition):

| Token | Value | Source |
|---|---|---|
| `--src-synthetic` | `#B28DFF` | synthetic — generated storm data |
| `--src-shadow` | `#FFB86B` | shadow — real alerts, evaluated but not enacted |
| `--src-production` | `#5EEAD4` | production — enacted decisions |

**Confidence scale** (the confidence bar gradient, low → high):

`#3A4A63 → #7BDFF2 → #5EEAD4` — cool monochrome ramp. Deliberately *not*
red/green: a confidence value is information about the machine's belief, not
a verdict. Verdicts are dispositions; colors stay in their lanes.

### 2.2 Typography

- **Data/mono:** `JetBrains Mono`, `IBM Plex Mono`, fallback `ui-monospace`.
  All numbers, timestamps, hashes, reason codes, thresholds, URLs.
  Tabular numerals always (`font-variant-numeric: tabular-nums`).
- **Prose/UI:** `Inter`, fallback system sans. Labels, explanations, onboarding
  copy. Never used for a number that participates in a decision.
- **Scale:** 11px metadata · 13px body · 15px section heads · 20px screen
  titles. No display typography anywhere — this is an instrument.

### 2.3 Spacing, shape, motion

- 8px base grid; radii 4px (chips/badges), 8px (cards/drawers), 0px (river
  rows — the tape is flush).
- Motion budget: **one** animation per screen. The river's new-row highlight
  (120ms, `--tx-0` at 8% opacity fading out). Everything else is instant.
  No spinners that spin — see §5 for loading states.

---

## 3. Components

### 3.1 Severity chip

`[SEV1]` — 4px radius, 11px mono uppercase, colored text + 1px colored border
on `--bg-1` fill. Never filled solid (solid fills are reserved for
dispositions at a glance).

### 3.2 Disposition chip

The verdict of a decision. Anatomy: `▮ SUPPRESS · flap-debounce`.

- `▮` — 3px left bar in the disposition color, the fastest scan cue.
- `SUPPRESS` — 11px mono uppercase in disposition color.
- `· flap-debounce` — 11px mono in `--tx-2`, the **reason code** (Law L1).
  A SUPPRESS chip without a reason code is a rendering bug.

### 3.3 Data-source badge

`◈ shadow` — 10px mono, source color, 1px dashed border. The dash pattern is
deliberate: it reads as "provisional" even in grayscale. Shown on every
screen header, every projection panel, every export.

### 3.4 Reason-code tag

Inline tag for correlator/gate reason codes: `flap-debounce`,
`self-clear<5m`, `deploy-churn`, `cascade-child`, `low-confidence-hold`.
10px mono, `--bg-2` fill, `--tx-1` text. Clicking one filters the river to
that reason code (deep link: `?reason=flap-debounce`).

### 3.5 Confidence bar — **geometry spec**

**Definition.** The confidence bar answers two questions at once:
*"How sure was the machine?"* (the marker) and *"Is that sure-by-our-standards?"*
(the distribution). The distribution is the team's confidence values from the
current labeled evaluation set (shadow, 7d window); the spec marks the
**third quartile (Q3)** boundary explicitly, because "0.87 confident" is
meaningless without knowing whether 0.87 is typical or exceptional for this team.

**Anatomy (left → right):**

```
┌────────────────────────────────────────────────────────────┐
│  96px × 14px bar                                           │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ▓▓▓▓░░░░░░▓▓▓▓▓▓▓▓░░░░░░▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░  │  │  ← 10-bin histogram of team confidences
│  │         │        │        │             ▲              │  │  ← Q1/Q2/Q3 quartile ticks · this decision's marker
│  └──────────────────────────────────────────────────────┘  │
│                          0.87                               │  ← value label, 13px mono
│                    (n=4,096 · bin 0.85–0.90)                │  ← denominator, 10px mono --tx-2  (Law L1)
└────────────────────────────────────────────────────────────┘
```

**Exact geometry:**

| Element | Spec |
|---|---|
| Bar frame | 96px wide × 14px tall, 4px radius, `--bg-2` fill, no border |
| Histogram | 10 equal bins (width 9.6px each), bin height normalized to max bin, drawn bottom-aligned in `--line-1` at 60% opacity; bins with zero count render as 1px baseline ticks in `--line-0` |
| Quartile ticks | 1px vertical lines at Q1/Q2/Q3 positions in `--tx-2` at 80% opacity, full bar height, **with a 2px gap at the histogram's own pixels** (tick floats above the bars, z-layer 2) |
| Q3 emphasis | the Q3 tick is `--tx-1` (brighter than Q1/Q2) — it is the "typical high-water mark" for this team |
| Decision marker | 5px-wide downward triangle (`▼`) in `--tx-0`, positioned with its tip at the exact x for the decision's confidence, overlapping the bar's top edge by 2px |
| Threshold line | 1px dashed vertical line at the team's current gate threshold in `--disp-page` at 70% opacity — the only red element in the component |
| Value label | right of the bar, 13px mono `--tx-0`, e.g. `0.87` |
| Denominator | below the label, 10px mono `--tx-2`: `(n=4,096 · bin 0.85–0.90)` — the evaluation-set size and the reliability-diagram bin this decision fell in |

**Reading contract (what the SRE learns in one glance):**
marker left of Q3 = below-typical confidence (treat with skepticism);
marker right of threshold line = the machine's belief cleared the gate;
denominator tells you how much evidence backs the calibration claim.

**Edge rules:** if the evaluation set has < 100 cases, the histogram renders
flat-hatched and the denominator reads `(n=87 · below 100 — calibration
provisional)`; the bar is never hidden, but it always confesses thin evidence.

### 3.6 Threshold slider

Horizontal, 100% width, 0.00–1.00. The track is the team's confidence
histogram (same 10 bins as §3.5, miniaturized); the handle is a 14px circle
with the current value in mono beneath it. Dragging is live — every connected
projection reprices on `input`, not on release. Keyboard: `←`/`→` step 0.01,
`Shift` steps 0.05.

### 3.7 Fingerprint link

`fpr:9f2c·a41d` — 11px mono, `--tx-1`, underline on hover. Copies the full
fingerprint on click; links to the audit explorer.

---

## 4. Screen chrome (shared)

- **Top bar (48px):** product mark `SENTINEL` (13px mono, letter-spaced) ·
  screen function code (`RIVER`, `CAL`, `SIM`, `AUDIT`, `START`) · data-source
  badge (Law L2) · dataset version (`ds:shadow-2026-10-02`) · connection state
  for SSE (`● live` / `○ paused` / `◌ reconnecting`).
- **Command palette (`⌘K` / `Ctrl+K`):** every screen reachable by function
  code; filters expressed as tokens (`team=payments`, `fpr=…`,
  `reason=flap-debounce`, `last=7d`). Every state is a deep link.
- **Detail drawer (right, 420px):** click-to-drill-down from river and audit.
  Sections: verdict · evidence · timeline · raw (JSON).

---

## 5. States

### 5.1 Empty

Never a blank page. Empty states name the cause and the next action.

- River, no decisions in window: *"No decisions in the last 24h for
  team=payments. The gate is armed and watching — widen the window or check
  another team."* + shortcut hint (`last=7d`).
- Audit, no fingerprint match: *"No decisions match fpr:9f2c·a41d. Check the
  fingerprint — or this alert never reached the gate (see receiver health)."*
- Simulator, no shadow data: *"No shadow evaluations for team=checkout yet.
  Run a guided storm (15 min) or wait for the nightly shadow join."*

### 5.2 Loading

No spinners. Skeleton rows in `--bg-1` with a 1.2s shimmer; the data-source
badge and dataset version render **before** any skeleton (Law L2 — you know
what you're waiting for before you wait). SSE reconnect shows `◌
reconnecting… (attempt 2)` in the top bar, never a modal.

### 5.3 Error

Errors are evidence, not dead ends. Every error block: what happened (plain
language), what it affects, the dataset version attempted, and the retry
action.

- API failure: *"Couldn't reach the decisions API (GET /api/decisions →
  503). The gate itself is unaffected — paging behavior doesn't depend on
  this screen. [Retry]"*
- SSE drop > 30s: river shows a gap marker row — `— 4m 12s gap in the tape
  (reconnecting) —` — the missing time is visible, never silently skipped.
- Calibration below n=100: provisional styling per §3.5, not a block.

---

## 6. The freedoms this system grants (design intent)

These are named in the screen specs where they land, and summarized here as
the bar every design review holds:

1. **Ask "what would this policy have done last week?" before it pages anyone
   at 3 AM.** (Simulator — draggable thresholds, same math as the tuner,
   projection with denominators.)
2. **See why this paged you — and why the others didn't.** (River detail
   drawer — evidence attached to every disposition, page and suppression alike.)
3. **Tune your own team's thresholds without filing a ticket.** (Per-team
   controls, simulator as safety rail, export-as-policy-diff for review.)
4. **Audit the machine's judgment, not just its output.** (Audit explorer —
   flip timelines, input hashes, calibration honesty about where we're wrong.)

PagerDuty and Opsgenie never granted these because their interface is a
configuration surface for rules. Ours is an instrument for decisions.
