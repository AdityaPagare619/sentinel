# Interface R&D Synthesis — disagree-then-commit verdict (2026-10-04)

**Lane:** L1 implementer. **Inputs:** L1a (`interface-rnd-2026-10-04.md`, PR #48) and
L1b (`interface-rnd-b-2026-10-04.md`, PR #47), worked independently and read in full.
**Law:** `design/INTERFACE_PRINCIPLES.md` (read from `origin/lane/interface-principles`;
§2 tokens, §3 surfaces, §4 honesty UI, §5 cognitive load, §8 anti-slop).
**Skills applied:** principal-systems (Type 1/2, global-over-local, pre-mortem),
principal-governance (contract-first, design never waits for backend),
execution-doctrine (pain first, proxy traps, build things that don't scale first),
principal-mindset (restatement test, subtract-before-adding, monkey-first + kill conditions).

**Status of this document:** a decision record, not a proposal. The direction below is
committed; the build in this lane implements it. Disagreement with this verdict goes
through the RFC process (§10), not through re-litigating in PRs.

---

## 1. Where the two halves converged (taken as settled)

Both halves, independently, derived the same three-layer architecture from the
primary sources (PagerDuty Events API v2, Grafana OnCall, BigPanda, Moogsoft,
Datadog, Opsgenie, Linear, Bloomberg). Convergence from independent derivation
is evidence, not coordination:

1. **A fixed, typed decision envelope** owned by the platform — severity,
   disposition, confidence (quantized), freshness, policy version. The console's
   chrome, lists, filters, and sorts touch *only* this envelope, never the raw
   payload. (L1a Direction A §3; L1b Q1 — "the shape of the answer is fixed".)
2. **A total, safe generic renderer** for the arbitrary remainder — renders
   *structure*, never *meaning*; cannot crash, lie, or require foreknowledge of
   any key. (L1a `<PayloadViewer>`; L1b's atomic grammar
   scalar|timestamp|link|flat-table|nested-collapse|binary/redacted.)
3. **Composability as data, never code or forks** — saved views / pins / lenses /
   rules are declared configuration, versioned, previewable, reversible.
4. **The behavior firewall** — projections never drive behavior. Lenses/templates/
   pins/rules may *display*; the engine owns dispositions, full stop. (L1a-B's
   item 4 — a point of genuine agreement both directions must hold.)
5. **Saved views (Linear model)** — named typed filters over contract fields,
   personal/team-shared, favorited, subscribable, URL-shareable — as the
   filter/subscription mechanism. Agreed under both L1a directions; compatible
   with both L1b directions.

None of the above is re-decided below.

## 2. The live disagreement, stated honestly

The disagreement is the **operator model and the composability mechanism** —
where the operator's power lives, and what the store must carry:

| | L1a-A "Typed Contract River" | L1a-B "Declarative Lenses" | L1b-D1 "Envelope & Inspector" (pins) | L1b-D2 "Headline Rules" |
|---|---|---|---|---|
| Queryable surface | Contract fields only | Contract + lens-declared fields | Contract fields only | Contract fields (+ pins as rule inputs) |
| Per-integration rendering | None — one generic viewer | Declarative lens documents, versioned, pinned to decisions | Field pins: path→slot bindings, workspace config | Fixed row grammar + WHEN/THEN rules |
| Operator cost | Zero config; promotion-by-RFC for new fields | Must author lenses (15-min kill condition) | Author pins (minutes); progressive enhancement | Author rules; rules panel with 24h match counts |
| Failure mode | Contract thinness → RFC backlog | Lens sprawl → config zoo | Pin mislabeling across vendors | Rule sprawl → confetti ("rules off" escape hatch) |
| Trust story | "The console shows only what the engine guarantees" | "The console shows what you declared, pinned and reproducible" | "The console shows the engine's fields plus your pinned labels" | "The console highlights what your rules say to watch" |

L1a's committed position going in: **A first, B as escape hatch.** L1b's handoff
note: pins and headline rules compose cleanly (pins feed rules) — a point in
favor of both, not a decision between them.

## 3. The verdict

### COMMITTED: "Typed Contract River + Field Pins"

**One line:** the river is the engine's (closed typed envelope, contract fields
only for filters/sorts/color); the operator's composability is **field pins** —
display-only, workspace-level bindings of payload paths into river columns and
evidence slots, which can never enter the query grammar, never drive behavior,
and switch off with one keypress.

**Exactly which parts of which (this is a hybrid with a decision, not "best of
both"):**
- **From L1a-A:** the operator model and the Type 1 closure — the queryable
  surface is the frozen contract's fields and *only* those fields; vendor fields
  reach filterability solely through promotion-by-RFC into the contract; the
  river never shows payload fields; no template language in the console.
- **From L1b-D1:** the composability mechanism — field pins (not A's
  nothing-at-all). A pin is `{path, slot, integration-label}`; pins render as
  extra river columns and S2 "Pinned fields" sections, previewed against a live
  payload before saving, versioned JSON, exportable/diffable.
- **Rejected now, with promotion gates (not rejected forever):** L1a-B's lens
  documents (per-integration appearance sections, platform-stored, lens_id +
  lens_version pinned to decisions) and L1b-D2's headline rules (a second
  highlighting authority over the river). §5 states the gates.

### Why this, and not the alternatives — the genuine disagreement

**Against pure A (no pins, RFC-only):** A's own open question 3 is the killing
argument — "does the promotion-by-RFC path survive a 40-integration estate, or
does the RFC backlog become the bottleneck A claims to avoid?" The RFC path is
a *process* answer to a *seconds-scale* debugging need: at 3 AM the operator
needs `payload.labels.cluster` visible *now*, not after a contract review.
Pins are the manual-before-automated step (execution-doctrine): the unscaled,
reversible version of "let me see my field." Pure A optimizes the view system's
maintainability at the cost of the operator's 3 AM debugging loop — a
local optimization (principal-systems law 1). The pain is real and named:
operators live in vendor fields (`cluster`, `runbook_url`,
`error_budget_burn`); L1b's research and L1a's own Q2 synthesis both document it.

**Against B (declarative lenses):** subtract-before-adding (principal-mindset).
A lens = pins + appearance sections per surface + per-integration versioned
store + lens-version pinning on decisions + a three-column editor. Ask the
ablation question: *does the marginal machinery change any 3 AM answer?* No.
The five 3 AM questions (P1) are answerable from the envelope + the generic
viewer + pins. The marginal machinery buys per-integration blast-radius
bounding and named panel sections — real benefits, but benefits for a
300-integration estate we do not have. At our scale it is a Type 1 liability
(a second contract the platform must version forever) bought before the
evidence exists. L1a-B already rejected the Turing-complete template language
as a Type 1 liability; what remains of B after removing
conditionals/loops/scripting is *declarative path→slot bindings* — which is
exactly what a pin is, plus integration-scoped documents. The integration
scoping is the one genuine B-advantage (bounded blast radius on vendor schema
change); pins recover most of it by carrying the integration name in-band and
warning when a pinned column's value distribution changes shape. **B is banked,
not killed:** it is the promotion target if the committed direction's kill
condition fires (§5).

**Against D2 (headline rules):** two highlighting authorities is the problem,
not the solution. Bloomberg needed Launchpad's condition→color rules because
Launchpad is a *news tape with no engine*; Sentinel has an engine whose
severity/disposition coloring is already the headline signal. Adding operator
rules on top creates two sources of truth for "what deserves my eye" — every
researched console keeps highlighting authority in the typed layer. The
legitimate need D2 serves ("highlight rows matching X") is served by saved
views (agreed by both halves) with zero new machinery: a saved view *is* a
named highlighted subset. D2's own pre-mortem names rule sprawl and the
"rules off" escape hatch as its failure mode — the escape hatch is an admission
of the failure mode, not a mitigation of it.

**The line that makes this a decision:** **pins are display bindings, never
query operands.** This is falsifiable and shapes the store: pins live in
workspace config (localStorage now, platform workspace store later — Type 2),
the filter/sort grammar never sees them, they cannot drive severity color or
dispositions, they carry integration labels in-band, and they switch off with
one keypress. If a pin's field needs to be *filterable*, the path is
promotion-by-RFC into the contract — A's mechanism, kept.

### Type labels

- **Type 1:** the query-surface closure (contract fields only — shapes the
  store's query grammar and five years of operator mental model); the fixed
  decision envelope itself (frozen platform contract, RFC to change); the
  behavior firewall (projections never drive behavior); the generic renderer's
  atomic grammar contract (what "safe" means is a public promise).
- **Type 2:** pins (workspace config, reversible, one-keypress off); saved
  views; pin editor UX; density of pin columns; the promotion gates in §5
  (re-measured, not re-decided).

## 4. What each rejected direction's kill condition is (pre-registered)

- **B (lenses):** KILLED if median lens-authorship time exceeds 15 minutes in a
  timed exercise, or if a lens-version drift produces a wrong rendering in a
  drill. Also killed by the ablation: if pins deliver an equal 3 AM pass rate
  with less machinery, B stays banked. **Promotion gate (banked → built):**
  §5.2 fires.
- **D2 (headline rules):** KILLED if any drill ever reaches for the "rules off"
  toggle (confetti admission). **Revival gate:** operators consistently build
  saved views whose real predicate is "highlight X in the river" AND X cannot be
  expressed over contract fields AND the need recurs across teams — then rules
  return as *display-only annotations*, never behavior.
- **Pure A (no pins):** KILLED by this verdict's own rationale — the RFC backlog
  argument stands. Revisit only if pins demonstrably cause the mislabeling
  failure (§5.1) at a rate exceeding the RFC path's cost.

## 5. The committed direction's kill conditions + what would change my pick

**5.1 Kill conditions (hitting one is a successful outcome — clean kill, per
principal-mindset):**
- **K1 — contract thinness:** in a timed operator exercise (the 3 AM test
  protocol, P1), if >20% of "why did this page/suppress" answers require
  raw-payload spelunking that pins could not surface, the envelope is too thin.
  Action: promote fields by RFC into the contract, or promote to lenses.
- **K2 — pin duplication:** if the same 3+ fields are pinned across ≥3
  integrations for >2 weeks, pins have revealed a systematic need. Action:
  promote those fields into the contract (RFC) or into lenses — pins were the
  discovery mechanism, not the destination.
- **K3 — pin mislabeling:** if a drill shows an operator acting on a pinned
  value whose meaning differed across vendors (L1b-D1's pre-mortem), and the
  in-band integration label did not prevent it, pins-as-display is unsafe.
  Action: restrict pins to single-integration workspaces or kill pins.

**5.2 Evidence that would change my pick (stated in advance):**
1. A timed operator exercise with a heterogeneous vendor estate where pin
   paths are systematically unstable across vendors (the same logical field at
   different paths) → **promotes B**: per-integration lenses bound the
   blast radius that workspace-level pins cannot.
2. Sustained K2 evidence (same pins everywhere) → the fields belong in the
   contract or in lenses; pins become the on-ramp, not the system.
3. Operators demanding in-river highlighting authority with predicates
   unexpressible over contract fields, recurring across teams → **reopens D2**
   as display-only annotations.

## 6. Pre-mortem (committed direction, top 3)

1. **Pin-induced misreading during a storm.** An operator pins
   `payload.status` and sorts/eyeballs a storm by it; "firing" vs "ok" means
   different things per vendor. *Mitigation (built):* pins are never sortable,
   never filterable, carry the integration name in-band, cannot touch severity
   color; a "pins off" toggle is one keypress.
2. **The generic viewer becomes a wall of text** on 200-key vendor payloads.
   *Mitigation (built):* depth cap 6 with collapse, 64KB stated byte budget,
   flat-table fast path for scalar maps, item caps with "+N more" rows; the
   engine-authored evidence stays the primary read, the viewer is the drill-down.
3. **Pins rot silently** when a vendor renames a field; the column shows `—`
   and nobody notices. *Mitigation (built):* unresolvable pins render `—` with
   the reason in-band (never blank); pin validation warns at save time when a
   path resolves on zero recent decisions; dead-pin review is a standing item.

## 7. Build record (this lane)

Implements the committed direction against the frozen platform contract
(`platform/contracts/openapi.yaml` v1.0.0), extending the Prism screens
(#31/#33/#35), never forking:

1. **`platform/ui/tools/gen-contract.mjs`** — generates
   `platform/ui/assets/contract.js` from the frozen OpenAPI. F8 made concrete:
   the console's typed boundary is generated, never hand-written.
2. **`platform/ui/assets/contract.js`** (generated) — the fixed typed
   decision-contract renderer core: contract enums, required-field sets, the
   closed river-row vocabulary, runtime Postel validation at the API boundary
   (`validateDecisionSummary/Detail`), quantization rule. Rows failing
   validation render an honest contract-drift state, never silently.
3. **`platform/ui/assets/payload.js`** — the total, safe generic
   arbitrary-payload renderer (atoms, flat tables, depth/width/item caps,
   secret-shaped key redaction, `—` for unknown with in-band reason). Pure
   functions, fuzz-tested with hostile payloads. Renders structure, never
   meaning; used in S2 evidence for `decision.alert`.
4. **`platform/ui/assets/pins.js`** — field pins: safe path resolution,
   workspace-config persistence, JSON export/import (diffable in PRs),
   save-time validation with live preview, display-only rendering with
   in-band integration labels, one-keypress pins-off.
5. **Wiring** — river gains pin columns (display-only, outside the filter
   grammar) + a pins rail section with editor; the evidence drawer gains a
   "Vendor payload" section (generic viewer) and a "Pinned fields" section.
   Every rendered decision keeps its P2 companions; reconstructed values carry
   in-band labels (§4.4); the five component states are designed for the new
   components.
6. **Tests** — `payload.test.mjs` (hostile fuzz corpus),
   `pins.test.mjs` (resolution, validation, display-only invariant,
   export/import round-trip), `contract.test.mjs` (generated code matches the
   frozen contract; mocks validate; drift is detected); `conformance.py`
   extended (generator freshness). Full suite green before PR.

**Honest gap noted during the build:** the frozen contract (v1.0.0) does not
carry `freshness_state` or `policy_version` on DecisionSummary — both demanded
by §4/P2. The build renders the honest absence explicitly ("not exposed by the
read API") rather than inventing values (P3). **RFC candidate:** add
`freshness{state, as_of, budget}` and `policy_version` to the read contract
(R1/R6). Filed as a follow-up, not a blocker — the interface owns the honesty
contract even when the platform is late (§11 rule for missing requirements).

## 8. What's next (crisp plan, not built in this lane)

1. **RFC:** `freshness_state` + `policy_version` on the decision read contract
   (R1/R6) — unblocks full P2 companions on every row.
2. **Saved views (Linear model):** named typed filters over contract fields +
   subscriptions ("notify me when a decision enters this view") + the
   Suppression Inbox (Linear Triage analog) as a first-class surface. Agreed by
   both halves; Type 2.
3. **Command palette** per the setproduct 8-state spec (L1a §6): actions-only
   scope; pins-off and pin-editor as palette commands.
4. **Single-key daily-loop bindings** on the river (j/k/Enter/a/e/s//, stable,
   printed on rows — L1a §6), plus the bulk-triage bar on multi-select.
5. **Timed operator exercise** to measure K1/K2/K3 (the 3 AM test protocol) —
   the evidence that promotes to lenses or kills pins.
6. **Pin store migration:** localStorage → platform workspace store when the
   platform team ships it (Type 2; the pin JSON schema is already stable).
7. **Facet promotion path (A1 binding):** platform RFC for operator-driven facet
   promotion with cost display (cardinality, coverage, index weight); target
   envelope v1.1 = + `policy_version`, `freshness{state, as_of, budget}`,
   `trace_id` (Type 1 — the console renders their honest absence until then).
8. **Field Catalog (§6.2, platform-side):** engine profiles fields per source;
   the pin editor reads from it when available (coverage-aware field picker).
9. **Shadow Report metrics (A2):** page-precision + suppression-regret as the S5
   headline metrics (definitions in §9).
10. **Fleet overview + grouped river (B2):** follow-up builds against
    contract-shaped mocks; the altitude seam (shared route state + A4 rule) is
    already designed.
11. **Icon set (R12):** drawn decision glyphs (triangle/half-circle/hollow-circle
    discipline per B1) + system-health token reservation with contrast CI.

---
*Synthesis recorded 2026-10-04 by the L1 implementer lane. Disagreement goes
through RFC (§10). The build below commits to this verdict.*

## 9. Binding input reconciliation — external redesign proposal (2026-10-04 ~00:45 IST)

Petu's binding decisions on `research/external/page-or-suppress-redesign-proposal-2026-10-04.md`
(Aditya's order: consider in research, NOT counter-audit). Each item is recorded
with its Type label. Where the proposal contradicts the §3 verdict, the proposal
wins — reconciled explicitly below. Rejected: the "Vigil" name, stack
suggestions (TypeScript/React — our no-build-step Prism stands), vendor survey
figures cited as claims.

### ADOPT

**A1 — Envelope / Facets / Unmapped-bag + render-by-shape (§6) as the interface
data architecture. Type 1 for the three-layer architecture; Type 2 for shape-renderer details.**
Reconciliation with the §3 verdict ("Typed Contract River + Field Pins"):
- **Envelope (fixed):** identical to the verdict — `contract.js` generated from the
  frozen platform contract is the envelope. The proposal's envelope fields
  (disposition ✓, quantized confidence ✓ via `CONFIDENCE_DP=2`, policy_version ✗,
  freshness ✗, trace_id ✗) set the **target envelope v1.1**: the three missing
  fields are now committed RFC items (Type 1), not just candidates. The console
  keeps rendering their honest absence (P3) until the contract carries them.
- **Facets vs pins — the genuine tension, resolved:** the proposal's facets are
  *platform-level, indexed, costed* (cardinality/coverage/index weight shown at
  promotion) and become columns, filters, group-by keys, and policy variables.
  Pins are *console-level, display-only, unindexed*. These are different layers,
  not competitors: **pins are the discovery mechanism, facets are the promotion
  target.** The verdict's display-only invariant stands (a pin NEVER becomes
  queryable by being pinned); the verdict's K2 kill condition (same 3+ fields
  pinned across ≥3 integrations) now maps to a concrete promotion path — facet
  promotion with the proposal's cost display (cardinality, coverage, index
  weight). "Facets as policy variables" is engine-side; the console never
  authors policy-affecting bindings (behavior firewall, §3 — unchanged).
- **Unmapped bag:** identical to the verdict — `decision.alert` rendered by the
  total safe generic renderer. The proposal's "flattened to dotted paths, plus a
  pointer to the untouched raw payload" is adopted as the viewer's contract.
- **Render-by-shape (§6.4), adapted:** single-value shapes implemented in the
  generic renderer now — URL → safe link; strict-ISO-8601 timestamp → UTC +
  local in-band (A5, see below); long text → width-capped; object/blob → collapsed
  depth-limited tree; unrecognized → `—` with in-band reason (never an error).
  Shapes needing corpus statistics (enum-like chips with counts, top values)
  belong to the **Field Catalog** (§6.2, platform-side) — the pin editor will
  read from it when it exists (follow-up). Duration/unit humanization is
  rejected in the generic renderer (a bare `420` is not knowably seconds —
  rendering "7m" would be inventing meaning); unit-aware display only where the
  contract types the field.
- **Hostile-content contract (§6.5):** adopted with one correction to this
  lane's first implementation — bidi/zero-width/C0 controls render as **visible
  `[U+XXXX]` markers**, never silently stripped (stripping can hide a spoof).
  Fuzz corpus in `payload.test.mjs` extended accordingly.

**A2 — suppression-regret + page-precision become Shadow Report metrics.
Type 2 (metric definitions, tunable).** Audit result: Prism carries **no**
"noise removed %" headline or equivalent vanity metric — the simulator pairs
every suppression count with its cost (False-suppress watch: Σ P(p1), expected
cost, export lock). Definitions recorded for the S5 build (follow-up):
- *Page precision* = acknowledged-and-acted-on pages ÷ pages sent.
- *Suppression regret* = suppressed events later joined to an incident or
  manually unsuppressed ÷ suppressions.

**A4 — stable rows, live-tail rule, no-insert-under-cursor. Type 2 (thresholds tunable).**
Already-law parts confirmed (pin-on-scroll = no-insert-under-cursor, F5/§7).
Adopted new: the **live-tail rule** — above `LIVE_TAIL_MAX_PER_SEC = 20`
(starting point, tunable with measurement) the tail pauses itself **with a
visible reason** ("Too fast to read: ~N/sec match this filter — live tail
paused. Narrow the filter to regain the tail."), rows are bounded in memory
while paused, and release uses hysteresis (resume below 10/s — no flapping).
Implemented as a pure state machine (`tailState` in lib.js, unit-tested); the
grouped-view altitude it switches *to* is the B2 follow-up.

**A5 — UTC + local timestamps on ALL event displays. No exceptions. Type 2.**
Implemented: `fmtTimeBoth` (lib.js) and `dualZone` (payload.js) — strict
ISO-8601 only, never guessed. River row titles, drawer timeline, flip timeline,
and timestamp-shaped payload strings all render both zones in-band (hover-only
would violate §5.2).

### ADAPT (principle yes, our implementation)

**B1 — three-channel visual separation. Type 2 for the encoding, Type 1 for the
token reservation.** Adopted the SEPARATION, not their hex values, not the name:
- *Decision channel* (what did we do): color + shape — our `dispChip` keeps
  `--disp-*` colors; decision glyphs get distinct drawn shapes in the icon-set
  follow-up (R12).
- *Severity channel* (how bad did the source say it was): **signal bars in
  neutral ink** — `sevChip` rebuilt (bars + text label, `var(--tx-1)`/`--line-1`
  only). A critical that was suppressed now looks different from a critical
  that paged — the proposal's key insight, and our old colored severity chips
  violated it.
- *System-health channel*: reserved hue. Evaluation against our token taxonomy
  (§2.2): `--state-*` roles exist in the taxonomy but have no values in
  `tokens.css`, and `--src-synthetic` already spends a violet-ish hue on data
  source — a collision the proposal's reservation would forbid. Follow-up: add
  a dedicated system-health token with contrast-CI verification (§7); do not
  invent hex values in this lane.

**B2 — three altitudes (fleet → groups → events). Type 2; seam designed now,
no 1M/min overbuild (execution-doctrine).** The seam: altitudes share one route
state (filter + window + altitude param in the hash grammar — `routeHref`
already carries filters; altitude joins it), and the A4 live-tail rule is the
automatic groups↔events transition. Default working view stays the river at our
real scale; Fleet overview and the grouped river are follow-up builds against
contract-shaped mocks. Explicitly NOT built: virtualization/Web-Worker
overbuild for event rates we do not have.
