# Screen: Decision River — the live stream of decisions

**Function code:** `RIVER` · **Route:** `/river`
**API:** `GET /api/decisions?limit=50&since_id=<id>` (initial + backfill) ·
`GET /api/decisions?fingerprint=&team=&action=&from=&to=` (filters) ·
`SSE /api/stream` (live prepend)

**Purpose:** One continuous, queryable tape of every gate decision — pages
*and* suppressions — newest first. This is the screen a design partner leaves
open. It is also where Freedom 2 lands: *"See why this paged you — and why
the others didn't."*

---

## 1. Interaction model — a tape, not a feed

Bloomberg-terminal semantics (Terminal Idea 1):

- **SSE prepends to the head.** New decisions arrive at the top with the
  120ms row highlight. The tape never re-sorts under you.
- **Pinned when scrolled.** The moment the user scrolls down from the head,
  the tape pins their position and a floating pill appears at the top of the
  viewport: `▲ 12 new — jump to live`. Clicking (or `Shift+G`) releases the
  pin and returns to the head. You never lose your place because a storm
  arrived.
- **Gap markers.** If SSE drops > 30s and reconnects, a divider row marks the
  hole: `— 4m 12s gap in the tape (reconnecting, attempt 2) —`. Missing time
  is visible, never silently skipped (Law L3 — honest scope).
- **Density toggle** (`1`/`2`/`3`): comfortable / compact / tape-mode
  (tape-mode = 28px rows, 200+ visible decisions, no prose).

## 2. Layout

Three columns, full viewport height:

| Column | Width | Contents |
|---|---|---|
| Filter rail (left) | 240px | team selector, severity toggles, disposition toggles, reason-code chips, time window presets, "saved views" |
| The tape (center) | fluid | row list, gap markers, pinned pill, `● live` indicator |
| Detail drawer (right) | 420px | opens on row click — see §5 |

Top bar carries the shared chrome: function code `RIVER`, the data-source
badge (Law L2), dataset version `ds:shadow-2026-10-02`, SSE state.

## 3. Row anatomy

One row, left → right, 13px mono throughout (compact mode):

```
02:14:07  [SEV2]  ▮ SUPPRESS · flap-debounce   0.87 (n=4,096)   payments/checkout   fpr:9f2c·a41d   ◈ shadow
```

| Segment | Component | Notes |
|---|---|---|
| Timestamp | `02:14:07` — decision time, `--tx-1` | hover reveals full ISO + age ("3m ago") |
| Severity | severity chip (`DESIGN_SYSTEM.md` §3.1) | |
| Disposition | disposition chip (`DESIGN_SYSTEM.md` §3.2) — **reason code is mandatory** | the fastest scan cue on the tape |
| Confidence | `0.87` + denominator `(n=4,096)` in `--tx-2` | full bar (with histogram + Q3) appears in the drawer, not the row |
| Team/service | `payments/checkout`, `--tx-1` | click filters to team |
| Fingerprint | fingerprint link (`fpr:9f2c·a41d`) | click opens audit explorer |
| Source | data-source badge | always last, always present |

**Color discipline:** a scrolled tape of 200 rows must read as mostly quiet
gray-blue with occasional red left-bars (pages). If the tape looks loud, the
design has failed — the machine's job is to make paging rare.

## 4. Filters — the query syntax (Terminal Idea 2)

The filter rail and the command palette share one grammar; every state is a
deep link (`/river?team=payments&action=suppress&reason=flap-debounce&last=7d`):

- `team=<name>` — team scope (multi-select in rail, comma-joined in URL)
- `action=page|suppress|escalate|defer`
- `reason=<reason-code>` — click any reason tag anywhere to apply
- `sev=1|2|3|4`
- `fingerprint=<fpr>` — jumps straight to that decision
- `from=<iso>&to=<iso>` or presets `last=1h|24h|7d|30d`
- Free text matches service name, fingerprint prefix, reason code

Filter application is instant (client-side on the loaded window; server-side
for `from`/`to` outside the window). The active filter set renders as
removable tokens above the tape — every token shows what it filters, and
clearing is one click.

## 5. Click-to-drill-down — the detail drawer

Clicking a row opens the 420px drawer (row stays highlighted; `Esc` closes).
This is Freedom 2's home: *"Show me why this paged me"* — the timeline the
scribe would have written, before interpretation.

Drawer sections, top → bottom:

1. **Verdict.** Big disposition chip + severity + timestamp. One sentence in
   plain language: *"Suppressed: disk-pressure alert on payments/checkout
   self-cleared 3 of the last 4 times within 5 minutes."* — generated from
   the reason code and evidence, never free-form LLM prose.
2. **Evidence.** The confidence bar (full geometry, §3.5 of the design
   system): histogram + Q1/Q2/Q3 + marker + threshold line + denominator.
   Below it, the Jev typed answer: `Choice: suppress · conf 0.87` and the
   contributing signals (top 5, with weights).
3. **Timeline.** Decision-time vertical timeline: alert fired → correlator
   episode formed (flap-debounce window) → gate evaluated → disposition.
   Each event timestamped; the episode shows sibling alerts merged into it
   ("1 page, 14 suppressed siblings" — one page per incident).
4. **Provenance.** Input hash (`sha256:7d3a…`), dataset version, gate policy
   version, tuner version. "Reproduce this decision" button → deep-links the
   audit explorer with the input hash prefilled.
5. **Raw.** Collapsible `GET /api/decision/<id>` JSON. Always available —
   the drawer never hides data the API has.

## 6. Keyboard map

`j`/`k` move selection · `Enter` opens drawer · `Esc` closes drawer ·
`/` focuses filter text · `Shift+G` jump to live · `p`/`s`/`e`/`d` toggle
disposition filters · `1`/`2`/`3` density · `?` shows this map. The river is
fully operable without a mouse — it is an on-call instrument.

## 7. States

- **Empty:** per design-system §5.1, names the filter set and suggests
  widening.
- **Loading:** badge + dataset version first, then skeleton rows.
- **SSE degraded:** `◌ reconnecting` in top bar + gap markers in the tape.
  The tape degrades to a polling refresh (30s) with a visible `◌ polling`
  state rather than pretending to be live.

## 8. API mapping

| UI need | Endpoint |
|---|---|
| initial 50 + backfill scroll | `GET /api/decisions?limit=50&since_id=<id>` |
| filter application | `GET /api/decisions?fingerprint=&team=&action=&from=&to=` |
| drawer evidence | `GET /api/decision/<id>` |
| live prepend | `SSE /api/stream` (event: `decision`) |

Zero Jev on reads (platform law). The river never triggers evaluation.
