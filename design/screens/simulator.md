# Screen: Threshold Simulator — "what would this policy have done last week?"

**Function code:** `SIM` · **Route:** `/simulator`
**API:** `POST /api/simulate {thresholds, dataset_version} → {projection}`

**Purpose:** The #1 validated practitioner freedom (research/ux-freedom):
*"Let me simulate a policy change before it pages someone at 3 AM."* Drag a
threshold, watch last week's noise get re-priced in real time, and commit
only when the tradeoff is one you'd defend in a postmortem.

---

## 1. Layout

Two panels, full viewport height:

| Panel | Width | Contents |
|---|---|---|
| Controls (left) | 380px | team selector · per-severity threshold sliders · policy-diff preview · "export policy diff" |
| Projection (right) | fluid | repriced outcome cards · the tradeoff curve · the "same math" provenance block |

The data-source badge here reads `◈ shadow` — the simulator never touches
production data and says so in the header: *"Projected on shadow evaluations
· ds:shadow-2026-10-02 · no live alerts affected."*

## 2. The controls — draggable thresholds

One slider per severity tier (`DESIGN_SYSTEM.md` §3.6), each initialized to
the team's current gate thresholds:

```
SEV1 gate   ━━━━━●━━━━━━━ 0.55   (slider)
SEV2 gate   ━━━━━━━●━━━━━ 0.70
SEV3 gate   ━━━━━━━━━●━━━ 0.85
```

- The slider **track is the team's confidence histogram** (same 10 bins as
  the confidence bar) — you drag *across the actual distribution*, seeing
  exactly which mass of decisions each notch captures or releases.
- Dragging is live: every `input` event fires `POST /api/simulate` (debounced
  150ms) and the projection panel reprices. Keyboard: arrows step 0.01.
- **Per-team, self-service** (Freedom 3): the team selector switches the
  whole control set; changing `payments` thresholds never touches
  `checkout`. No tickets, no PromQL, no CI deploy — the simulator *is* the
  safety rail.

## 3. The projection — repriced outcomes

Three outcome cards, repriced on every drag:

1. **Pages** — `1,204 → 1,031 (−173)` — alerts that would still have paged.
2. **Suppressions** — `2,892 → 3,065 (+173)` — with the top-3 reason codes
   that absorb the delta: `flap-debounce +96 · self-clear<5m +51 ·
   deploy-churn +26`.
3. **False-suppress watch** — `3 → 3 (+0)` — shadow-labeled cases the new
   thresholds would have suppressed but a human actually needed. Sub-line
   with the denominator: `(3 of 412 labeled SEV1s · unchanged)`. **This card
   is the conscience of the screen**: if the delta here moves, the card
   turns `--sev-1` and the export button locks until the operator
   acknowledges each case by opening it in the audit explorer.

Below the cards, **the tradeoff curve**: x = suppression rate, y =
false-suppress count, the team's operating point as a dot, the dragged point
as a second dot connected by an arrow. Dragging the slider walks the dot
along the curve — the exact mental model of repricing a position.

## 4. "Same math as the tuner" — the guarantee, made visible

A provenance block pinned to the bottom of the projection panel, always
visible, never collapsible:

```
projection math
  dataset      ds:shadow-2026-10-02 (4,096 decisions · 7d window)
  tuner        tuner v0.3.1 · commit 8f2ac41 · same binary the CLI runs
  evaluated    02:00 IST nightly shadow join — replayed with your thresholds
  reproduce    POST /api/simulate with this payload → identical projection
  [copy payload]
```

This is the anti-"AI insights panel" (research anti-patterns): the simulator
doesn't ask for trust, it shows the exact inputs and offers the payload to
re-run. The `[copy payload]` button copies the `{thresholds,
dataset_version}` JSON so any engineer can reproduce the number from a
terminal.

## 5. Commit flow — simulate → review → export

The simulator **never writes policy** (Law L5 — reads before writes).
When the operator likes a projection:

1. `Review & export` opens a **policy diff** — old thresholds vs new, per
   severity, per team, in the exact YAML the gate consumes.
2. The diff carries the projection summary and provenance block as comments.
3. Export produces a file + a PR-ready branch name suggestion
   (`policy/payments-thresholds-2026-10-03`). Applying it is a human's
   code-review decision, in git, with the simulator's projection as the
   evidence attached.

Post-Sunday, per the research implications: threshold changes versioned in
git, simulator output attached as the review artifact.

## 6. Guardrails (honest scope)

- The simulator projects on **shadow + labeled** data only. It cannot
  simulate on production (no labels) and says so — no fake certainty.
- Projections are labeled *projections*, never predictions. Copy uses
  "would have", never "will".
- Below n=100 labeled cases for a team: sliders still drag, but the
  projection panel shows provisional styling and the export button requires
  an explicit "I understand the evidence is thin" checkbox.

## 7. States

- **Empty:** no shadow data → *"No shadow evaluations for team=checkout yet.
  Run a guided storm (15 min) or wait for the nightly shadow join."*
- **Loading:** badge + dataset version first; the last-known projection
  stays visible (dimmed, labeled `stale`) while the new one computes — the
  screen never blanks on a drag.
- **API error:** *"Simulation failed (POST /api/simulate → 500). Your
  thresholds are unchanged — nothing was projected. [Retry]"* — and the
  sliders keep their positions.

## 8. API mapping

| UI need | Endpoint |
|---|---|
| reprice on drag | `POST /api/simulate {thresholds, dataset_version} → {projection}` |
| current thresholds | `GET /api/calibration?team=<team>` (threshold field) |
| false-suppress cases | deep link → audit explorer with `labeled=sev1&action=suppress` |
