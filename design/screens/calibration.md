# Screen: Calibration — "is the machine honest about what it knows?"

**Function code:** `CAL` · **Route:** `/calibration`
**API:** `GET /api/calibration?team=<team>` ·
`GET /api/analytics/flips?window=7d`

**Purpose:** The trust surface. A skeptical SRE — no ML background — lands
here and in ten seconds answers: *when Sentinel says 0.87, should I believe
0.87?* This screen is where Freedom 4 lives: *"Audit the machine's judgment,
not just its output"* — including where we're wrong.

---

## 1. The 10-second read (above the fold)

Three headline cards, left → right, each a complete sentence:

1. **Expected Calibration Error (ECE)** — `0.031`
   Sub-line, 11px mono `--tx-1`: `weighted over 10 bins · n=4,096 · shadow 7d`
   Plain-language gloss (13px): *"When Sentinel says 80% confident, the
   outcome matched about 77–83% of the time."* The gloss is generated from
   the actual ECE, not canned copy.
2. **Coverage at current threshold** — `94.2%`
   Sub-line: `threshold 0.70 · 3,858 of 4,096 decisions above gate`
   (Law L1 — the denominator is the card, not a footnote.)
3. **Flip rate (7d)** — `1.8%`
   Sub-line: `74 of 4,096 inputs re-asked → different answer`
   Gloss: *"Same input, asked twice, Jev changed its mind. Below 3% is our
   watch band; above it, we page the platform team, not you."*

Below the cards, one verdict line — the only editorial sentence on the
screen, derived from thresholds, not vibes:
*"Calibration is healthy for team=payments (ECE 0.031 < 0.05 gate). Two bins
are overconfident — see the diagram."*

## 2. The reliability diagram — spec

**Layout:** 480×360px canvas, dark-ops. X-axis: predicted confidence
(0.0–1.0, 10 bins). Y-axis: observed outcome rate. Diagonal: perfect
calibration (dashed `--line-1`). Bars: per-bin observed rate.

**Bar anatomy (per bin):**

- Bar height = observed rate; bar color = `--src-shadow` at 80% if within
  ±0.05 of diagonal (calibrated), `--sev-2` if overconfident
  (predicted > observed), `--sev-4` if underconfident.
- **Hover (the Bloomberg moment — Terminal Idea 3):** a tooltip reprices the
  bin like a quote:
  ```
  bin 0.80–0.90
  predicted 0.845 · observed 0.792
  n=412 · ECE contribution 0.011
  verdict: overconfident — treat 0.85s as ~0.79
  ```
  Every number carries its denominator. The "verdict" line translates the
  statistics into operator language.
- **Bins with n < 30** render hatched and hover reads `(n=17 — too thin to
  trust, excluded from ECE)` — honesty about thin evidence, drawn not hidden.

**The threshold line:** vertical dashed `--disp-page` at the team's gate
threshold, labeled `gate 0.70`. The SRE sees immediately which bins sit
above the line that wakes people up — and whether those bins are the
overconfident ones.

## 3. "How a non-ML SRE reads it" — the guided layer

A collapsible 3-step read, written for the on-call engineer, not the model
team. This is onboarding copy that lives permanently on the screen:

1. *"The diagonal is perfect honesty. Bars near it = the machine knows what
   it knows."*
2. *"Red-ish bars above your gate line = the dangerous kind of wrong:
   confident and incorrect. If you see those, tell us before you trust a
   suppression."*
3. *"The flip rate is the machine disagreeing with itself. Small is normal;
   growing is a platform problem, not your problem."*

No accuracy claims anywhere (Law L3). We report calibration, coverage, and
flips — the three quantities we can honestly measure.

## 4. Per-team tabs + comparison

Tabs across the top: `payments` `checkout` `infra` `+ all`. Selecting a team
re-queries `GET /api/calibration?team=<team>`; the URL deep-links
(`/calibration?team=checkout`). An `all` view overlays team curves in muted
tones with the selected team emphasized — miscalibration is often
team-shaped (one team's alert mix is noisier), and the overlay makes that
visible without a single misleading aggregate number.

## 5. Flip investigation entry

The flip-rate card links directly into the audit explorer prefiltered to
flipped decisions (`/audit?flipped=true&team=payments&last=7d`). The flip
timeline itself lives in the audit explorer spec — calibration surfaces the
*rate*, audit surfaces the *cases*.

## 6. States

- **Below n=100:** the whole screen renders in provisional mode — headline
  cards show values with `(provisional)` suffix and the diagram is
  hatched. Law L3: thin evidence confesses, never markets.
- **No shadow data for team:** empty state per design-system §5.1 — *"No
  shadow evaluations for team=checkout yet. Calibration needs the nightly
  shadow join — check back after 02:00 IST, or run a guided storm."*
- **Loading:** badge + dataset version first, then skeleton cards.

## 7. API mapping

| UI need | Endpoint |
|---|---|
| headline cards + diagram | `GET /api/calibration?team=<team>` (bins, counts, ECE, threshold) |
| flip rate card | `GET /api/analytics/flips?window=7d` |
| flip cases | deep link → audit explorer |

All read-only, data-source-labeled, zero Jev on reads.
