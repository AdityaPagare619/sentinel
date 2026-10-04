# Prism Rebuild — build record (2026-10-04)

**Lane:** PRISM REBUILD (`lane/prism-rebuild`), stacked on PR #54 (`lane/interface-rnd-impl`).
**Law:** `design/INTERFACE_PRINCIPLES.md` v1.0 (PR #46; v1.1 named in the task is not on origin/main yet — the v1.0 §0–§11 text is the ratified law; §11 R1–R25 tracked below).
**Direction:** Typed Contract River + Field Pins (synthesis verdict, committed). This lane extends PR #54's core rendering (contract.js/payload.js/pins.js) into the five surfaces — never forks it.

---

## 1. What was built, per surface

**S1 Decision river** (`views-river.js`)
- Virtualized (windowed) rendering: only visible rows + 12 overscan exist in the DOM; fixed row heights per density (compact 30px / tape 24px / comfortable 46px). §6's 60fps-at-10k-rows budget is structural, not aspirational.
- Stable rows: keyed by decision id; selection and focus survive re-renders; pin-on-scroll retained (the tape never re-sorts under you); jump-to-live pill retained.
- Live-tail state machine kept (`tailState`): pauses above 20/s with a visible reason, hysteresis release below 10/s, memory bounded while paused.
- Tape-level `<FreshnessBadge>` driven by the SSE/stream state (live/cached/stale/degraded); rows carry in-band dual-zone times (`02:14:07 UTC · 07:44 IST`, A5) and as-of ages.
- `x` = the one-keypress pins escape hatch (in addition to the rail toggle).

**S2 Calibration & evidence** (`views-cal.js`, `drawerHtml`)
- The drawer is now the per-decision evidence surface with the P2 five companions rendered as a labeled strip: evidence (triples + confidence bar), uncertainty (quantized + calibration context), freshness badge, policy version, fallback reason. Companions 4–5 render their honest absence ("not exposed by the read API") — never invented (P3, synthesis §7 gap).
- A1 data architecture rendered: envelope (verdict + companions) / facets (platform-promoted; honest-absence state with the pins→facets promotion path stated) / unmapped bag (generic payload viewer, "Vendor payload" renamed).
- CAL surface gains a join-bound freshness badge (honest: join timestamp not exposed).

**S3 Threshold simulator** (`views-sim.js`)
- Full-surface simulation treatment (W1 law with full force): an undismissable in-band SIMULATION banner; every projection card carries `SIMULATED` in-band; the tradeoff curve and the export diff are watermarked ("SIMULATED PROJECTION — not a historical measurement").
- Projection-dataset freshness badge (dataset-bound, §4.1).

**S4 Audit explorer** (`views-audit.js`, `chain.js`)
- Event-log chain view (Appendix A `<TimelineExplorer>`): hash-linked events derived locally from stored decisions, re-verified client-side; a break renders its location in-band. Labeled `derived` — the platform's sealed log is not in the read contract (RFC below); the view states what the derived chain can and cannot attest to.
- Export now ships decisions + the derived chain (re-verifiable by the recipient).
- Audit-window freshness line; flips render dual-zone times.

**S5 Shadow report** (`views-shadow.js`, `shadow.js`, `data/shadow.json`)
- New surface: page-precision (acked-and-acted pages ÷ labeled pages sent) and suppression-regret (shadow-suppressed events later joined to an incident or manually unsuppressed ÷ labeled shadow-suppressions) — computed client-side from the read contract's decision records + outcomes, every figure labeled `derived`; unlabeled outcomes excluded from denominators and the exclusion stated.
- Agreement/disagreement with bilateral evidence links per row; review notes in-band. **No vanity metrics** — "noise removed %" and kin are banned by `tests/shadow.test.mjs`.
- Live mode computes precision/regret and renders the honest absence of the agreement dimension (shadow join not in the read contract); the mock fixture shows the full S5 shape.

**Cross-cutting**
- `freshness.js`: §4.1 as code — closed four-state vocabulary, per-surface budgets (§11 R3), `<FreshnessBadge>` with text label (never color-only, §7).
- `components.js`: `<ReconstructionMark>` (§4.4), decision glyphs (color+shape per disposition — B1/R12 icon discipline), drawn close glyph (§8.3), §4.3 five-state registry (`COMPONENT_STATE_COVERAGE`, gated by tests).
- `tokens.css`: `--state-*` roles given values (B1 third channel, reserved; evaluated against the §2.2 taxonomy — see §4 below on the hex question).
- Anti-slop mechanical gates (`tests/antislop.py`): no emoji, two font families, no raw color literals outside tokens, no gradients, no phantom interactivity, tabular numerals. Fixed two pre-existing violations found by the gate (raw hex in `.mock-banner`/`.deg-banner`, `rgba()` scrims → `color-mix()` over tokens; ✕ text glyphs → drawn close glyph).

## 2. Reviewer R1 findings — rulings (recorded in the PR body)

**R1.1 — Q1 vs "pins as filter operand": ruled — Q1 stands, pins are render-only.**
The L1b Q1 ("fixed envelope fields are the ONLY things any surface may sort/filter/color by") is correct and stands as the Type 1 query-surface closure. Direction 1's "first-class column, filter operand" language is **superseded** by the synthesis verdict ("pins are display bindings, never query operands") — which is the committed direction this lane implements. Reasoning: making pins filter operands would create a second query grammar over uncontracted vendor fields, violating the query-surface closure and the behavior firewall (projections never drive behavior). A pin that needs to be filterable goes through promotion-by-RFC into the contract — A's mechanism, kept. Enforced as a gate: `tests/honesty.test.mjs` asserts `isQueryableField()` rejects every pin path and that the river's filter-param builder never references pins.

**R1.2 — Direction 2 headline-rule colors vs §8.7: one-line constraint, in code.**
D2 (headline rules) is banked, not built — but the constraint is recorded now so a future revival can't smuggle in decorative color. In `assets/components.js`: *"RULE: any rule-driven highlight color must resolve to a token in the §2.2 taxonomy — literal colors are banned here."* Enforced by `tests/antislop.py` (no hex/rgb literals in components or views).

**R1.3 — Kill conditions for the rebuild direction (falsifiable, stated here and in the PR):**
- **KC1 (honesty):** any drill operator mistakes a simulated, reconstructed, or derived value for a measured fact, or the §9.2 gates catch a derived value without its in-band label → the reconstruction-law implementation failed; the surface blocks release until fixed.
- **KC2 (river at scale):** the virtualized river drops below 60fps median in a 10k-row drill, or the live-tail state machine flaps (≥2 pause↔resume cycles in 60s at a steady rate) → the tail/river architecture is wrong for storms; redesign the tail, don't tune the cap.
- **KC3 (envelope thinness):** in the timed 3 AM exercise, >20% of "why did this page/suppress" answers require raw-payload spelunking that field pins cannot surface → the envelope is too thin (mirrors synthesis K1); action: RFC fields into the contract, or promote to lenses.
- **KC4 (pin safety):** any drill where an operator acts on a pinned value whose meaning differed across vendors despite the in-band integration label (synthesis K3) → pins restricted to single-integration workspaces or killed.
- **KC5 (shadow trust):** suppression-regret recomputed against an independent incident-join audit on the same window disagrees with the console's figure → the S5 definitions are wrong; restate the metric, don't decorate it.

## 3. Type labels for this lane's decisions

- **Type 1:** the query-surface closure (unchanged, enforced); envelope/facets/unmapped-bag as the interface data architecture (A1 adopted); the five-state registry; the honesty invariants as gates; the three-channel separation with the reserved `--state-*` hue set.
- **Type 2:** virtualization row heights and overscan; live-tail thresholds (kept at 20/10); freshness budgets (kept per §11 R3, tunable with measurement); the kill conditions above (re-measured, not re-decided); pin editor UX; shadow metric definitions.

## 4. Notes on contested points

- **Token hex values:** the synthesis lane was told "do not invent hex values in this lane." This lane is the design lane, and R9 names token values as its deliverable — `--state-*` values are set here with the reservation documented (health channel reserved, text labels mandatory, the one sanctioned overlap is error-red = page-red, documented as shared urgency semantics). Contrast CI (§7) is a follow-up for the design-system lane.
- **Chain and shadow endpoints:** neither `/api/audit/chain` nor `/api/analytics/shadow` is in the frozen contract v1.0.0 — both surfaces are client-derived and labeled. RFC candidates: (a) `freshness{state, as_of, budget}` + `policy_version` on the decision read contract (R1/R6, unblocks full P2 companions); (b) sealed event-log chain endpoint; (c) shadow join for the agreement dimension.
- **Cut list:** none triggered. The scope cuts (simulator advanced features → audit export formats → shadow extra charts) were not needed; all five surfaces shipped with their honest cores. The five states, honesty invariants, and anti-slop checklist were never at risk.

## 5. Test record

- `node --test tests/*.test.mjs`: **78 pass, 0 fail** (lib 14 · contract 6 · components 3 · payload 14 · pins 7 · freshness 13 · shadow 6 · honesty 15).
- `python3 tests/conformance.py`: all pass (incl. new §6 shadow-derivation checks).
- `python3 tests/antislop.py`: all pass (fixed 2 pre-existing violations + the ✕ glyphs).
- Secrets grep over new/changed files: clean (no keys, tokens, or credentials introduced; pins use localStorage only).

## 6. What's next (not this lane)

1. Timed 3 AM operator exercise to measure KC1–KC4 (the evidence that promotes to lenses or kills pins).
2. RFCs: contract freshness/policy_version (R1/R6); sealed chain endpoint; shadow join.
3. Saved views (Linear model) — agreed, Type 2.
4. Contrast CI for the `--state-*` pairings (§7).
5. Visual regression baselines for the five surfaces (§9.3) and the Playwright critical flows (§9.1) — Tripwire's R19–R21.
6. Pin store migration to the platform workspace store when it ships.
