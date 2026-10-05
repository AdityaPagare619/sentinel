# Prism UI — Phase-2 Domain Review

**Reviewer:** Phase-2 domain reviewer, Prism UI (fresh eyes — did not build this)
**Date:** 2026-10-05 IST · Branch: `program/architecture-revision-dom-ui`
**Standards applied:** `design/INTERFACE_PRINCIPLES.md` v1.1 (the UI's own constitution),
Phase-1 `research/swe-discipline.md` + `research/architecture-patterns.md`,
workspace skills `principal-systems`, `principal-governance`, `principal-mindset`, `execution-doctrine`.
**Method:** read every asset (`platform/ui/assets/`, 4,144 lines), all 15 test files,
`design/DESIGN_SYSTEM.md`, the platform server's API surface, and ran the full UI
test suite (108 node tests + `conformance.py` + `antislop.py` — all green).

**One-sentence verdict:** the implemented Prism UI is a genuinely well-crafted vanilla-JS
static app whose *honesty engineering* is best-in-class, but it does not implement its
own constitution's framework stack (§1, Type-1 decisions F1–F10) or token taxonomy (§2.2) —
the constitution was written as greenfield law with no migration path from the shipped UI,
so "LAW, not advice" is currently advice. Everything below is cited to code, not impressions.

---

## 1. WHAT EXISTS

### 1.1 Architecture (as built, not as specified)

- **Vanilla JS, zero build step.** `platform/ui/index.html` + ES modules, no React, no
  TypeScript, no Vite, no bundler. Served as static files (`python3 -m http.server`
  per `README.md`); the platform API lane serves it at `/`. Total: ~284 KB
  uncompressed JS+CSS across 18 modules.
- **Hash router + per-view modules** (`assets/app.js`, 190 lines): eight screens —
  `start` (onboarding tour), `river`, `calibration`, `simulator`, `audit`, `shadow`,
  `settings` (KEYS/BYOK), `setup` (no-backend honest empty). Every state is a deep link
  (`team=`, `action=`, `reason=`, `sev=`, `fpr=`, `in=`, `q=`, `last=`); command palette
  (`Ctrl/⌘K`) with filter grammar.
- **Data layer** (`assets/api.js`, 435 lines): a hand-rolled `Data` facade —
  `getDecisions`, `getDecision`, `getCalibration`, `simulate`, `getNoise`, `getFlips`,
  `getShadow`, plus BYOK integrations endpoints. Module-level mutable state
  (`Data.mode`, `Data.lastMeta`, `Data.datasetVersion`). A hand-rolled `Stream` class:
  SSE with `?since_id=` resume, gap events, 30s-quiet heartbeat → polling fallback,
  per-mode stream behaviors (live SSE / static snapshot polling / mock replay).
- **Typed Contract River** (`assets/contract.js`, 110 lines, **generated** by
  `tools/gen_contract.py` from `platform/contracts/openapi.yaml` v1.0.0, sha-pinned):
  closed river-row vocabulary, closed filter grammar, enum membership checks, and
  runtime Postel validation (`validateDecisionSummary`/`validateDecisionDetail`).
  Rows the contract can't describe render as **drift rows**, never as decisions.
  The river may reference *only* enumerated fields.
- **Field Pins** (`assets/pins.js`, 235 lines): the composability mechanism — display-only
  bindings of `alert`-payload paths into river columns/evidence slots. The display-only
  invariant is load-bearing: pins can never enter the filter/sort grammar
  (`isQueryableField()` rejects them by construction), never drive color, one-keypress
  (`x`) escape hatch, in-band integration labels, unresolvable pins render "—" with the
  reason in-band. localStorage workspace store, diffable JSON export/import.
- **Components** (`assets/components.js`, 443 lines): hand-rolled atomic set —
  `decisionRow`, `drawerHtml` (the P2 companions), `sevChip` (signal bars, neutral ink),
  `dispChip` (drawn SVG glyphs per disposition + mandatory reason code),
  `FreshnessBadge`, `ReconstructionMark` (`derivedMark`), `confBar` (96×14 geometry,
  quartile ticks, threshold line, denominator with n<100 provisional hatching),
  reliability diagram, skeletons, error/empty blocks, gap markers, and a
  **five-state registry** (`COMPONENT_STATE_COVERAGE`) naming the renderer for every
  component × state (loading/ready/stale-degraded/error/empty).
- **Freshness contract as code** (`assets/freshness.js`, 92 lines): the closed
  four-state vocabulary (live/cached/stale/degraded), per-surface budgets
  (river 30s; calibration/simulator/shadow 26h; audit null = chain-verified),
  text-labeled badges (never color-only), reserved `--state-*` hue channel.
- **Honest hostile-content payload renderer** (`assets/payload.js`, 338 lines):
  renders *structure, never meaning*; depth/width/item/byte budgets with stated
  truncation; secret-shaped keys → "▪▪▪ redacted"; bidi/control chars → visible
  `[U+XXXX]` markers; never infers semantics. Fuzz-tested (`payload.test.mjs`, 14 tests).
- **Client-derived audit chain** (`assets/chain.js`): `link = sha256(prev ‖ input_sha256 ‖ created_at)`,
  verified client-side — with an honest header comment admitting it is **not** the
  platform's sealed log ("it proves the decisions the console holds are internally
  consistent… it is NOT the platform's sealed log and cannot attest to what the
  platform recorded").
- **Simulator labeling** (`views-sim.js`): undismissable in-band `SIMULATION` banner,
  every card carries the simulated mark, policy-diff exports watermarked
  (`# SIMULATED PROJECTION`), ack-gate before export, mock-mode recompute labeled
  "NOT tuner math" in provenance. The W1 incident is genuinely encoded as mechanism.
- **Shadow report** (`views-shadow.js` + `shadow.js`): page-precision and
  suppression-regret over labeled outcomes only (exclusions stated); a hardcoded ban on
  vanity metrics (`BANNED_VANITY_PATTERNS`, self-audit hook); honest absence when the
  agreement dimension is unavailable.
- **The DATA_MODE shim** (`api.js` + `datamode.test.mjs`, 11 tests): build-time
  `window.SENTINEL_DATA_MODE` — `static` (staging: pre-rendered JSON, forced simulated
  paging, in-memory demo KEYS that never persist, "SIMULATED SHOWCASE" banner, toggle
  *removed* as phantom interactivity), `live` (production: real backend, no fixtures
  anywhere, setup screen instead of invented data when no backend is configured),
  unset (dev: legacy `?mock=1`/localStorage switch). Build-fixed modes cannot be
  flipped — `setMode` is a tested no-op. Mock mode renders the `◈ MOCK DATA` banner
  with a "go live" escape. **This is the single best-executed honesty mechanism in
  the UI**: the build's condition is structural, not cosmetic.

### 1.2 What the UI assumes about the platform API (verified against `platform/server/app.py`)

The UI assumes: `GET /api/decisions` (+`limit/since_id/fingerprint/team/action/from/to`),
`GET /api/decision/<id>`, `GET /api/calibration[?team]`, `POST /api/simulate`,
`GET /api/analytics/noise|flips`, SSE `GET /api/stream`, and
`/api/v1/integrations/{status,keys,simulated,test-page}` — all present on the server
(app.py:163–199, 495–540). It additionally assumes three things the frozen contract
**does not provide**, each handled with honest-absence rendering rather than invention:
`meta.data_source` on envelopes (falls back to `unknown`, badge-tested in
`honesty.test.mjs` INV-6), `policy_version` / `freshness_state` on `DecisionSummary`
(`contract.js` header: "the console renders that absence explicitly rather than
inventing values"), and the shadow join + sealed audit chain (client-side substitutes:
derived shadow metrics, derived hash chain — both labeled). In live mode,
`getShadow` performs up to **200 sequential `getDecision` calls** (N+1) to build the
report, and the agreement dimension — S5's core question — is unavailable.

### 1.3 Test coverage (108 tests, all passing — verified by running them)

- 15 `node:test` suites: contract (6), components (3), lib (14), payload (14), pins (7),
  freshness (13), shadow (6), honesty (18), datamode (11), integrations (10),
  chain (4), start-live/start-static (1+1).
- `tests/conformance.py`: endpoint coverage, mock-shape conformance, enum mappings,
  envelope labels. `tests/antislop.py`: mechanical §8 checks (emoji, type discipline,
  color literals, gradients, phantom interactivity) — passes.
- **The §9.2 honesty invariants as automated gates (18 tests) are the standout.**
  INV-1 (freshness on stale/degraded, wired in every view — asserted by static scan),
  INV-2 (derived/simulated values labeled in-band — behavioral + static),
  INV-3 (five P2 companions incl. honest absence of policy version),
  INV-4 (five-state registry completeness, every named renderer exists),
  INV-5 (zero Jev/model-evaluation calls on read paths — static scan of 15 files),
  INV-6 (source badges derive from envelope evidence, never hardcoded — including
  regression tests for two previously-shipped honesty bugs: the shadow hardcode and
  the unevidenced confidence denominator). This is blind-analysis-grade discipline
  applied to UI honesty, and it is real: the tests fail if the laws regress.

---

## 2. WHAT'S MISSING

Two different gaps, kept separate: **(A)** the UI vs its own constitution
(`INTERFACE_PRINCIPLES.md` v1.1) — the more serious; **(B)** the UI vs the Phase-1
enterprise standard.

### A. The UI does not meet its own constitution

**A1. §1 framework decisions (F1–F10): essentially none implemented.**
F1 mandates **React 18+ with TypeScript strict as a Type-1 decision** ("the console
must be unable to misread [the contract]… contract drift [becomes] a compile error").
The UI is vanilla JS: there is no compile step, no generated TS types
(`gen_contract.py` emits a JS module with enum lists + runtime validators — the
*spirit* of F8's contract-authority exists, the *letter* of F1's compile-time gate
does not). F2 Vite, F3 TanStack Query/Zustand (the "two substances never mix" rule has
no enforcement — `Data` is one mutable module mixing server state, UI mode, and
listener sets), F5 SSR shell (static shell, no hydration contract; skeletons exist
but CLS has no budget or measurement), F6 headless primitives (hand-rolled components;
a11y is hand-added per component, not inherited), F9 Vitest/Testing Library/
**Playwright/visual-regression** (node:test only — the §9.1 critical flows, §9.3
screenshot diffing, and §9.4 quarterly 3 AM test do not exist; no timed 3 AM result
file exists in the repo despite R15/R22 requiring one), F10 component catalog
(Storybook — the "catalog" is `components.js` + prose). F4 SSE is implemented but
deviates: resume is `?since_id=`, not `Last-Event-ID`; the "single reconnect wrapper"
is per-view `new Stream()` instances, not one. **No RFC was ever filed to reconcile
this** — the constitution's §10 requires a Type-1 change to go through RFC with
written alternatives, yet the constitution itself was written *over* a shipped vanilla
UI without one. The file's header still says "Version: 1.0 — 2026-10-04" while its own
changelog records v1.1 (§11) — even the version label wasn't maintained.

**A2. §2.2 token taxonomy not implemented; two competing design systems.**
`tokens.css` implements `design/DESIGN_SYSTEM.md` (the pre-constitution doc): ordinal,
abbreviated names (`--bg-0`, `--tx-1`, `--sev-1`, `--disp-page`). §2.2 requires
semantic-only tokens (`--surface-raised`, `--text-primary`, `--severity-critical-bg`,
`--space-4` on an 8pt grid, `--motion-*`, `--elevation-*`, `--density-*`) — none exist.
`--sev-1` is the ordinal equivalent of the `--blue-500` the constitution bans. The F7
lint rule ("raw color literals outside token files = CI failure") exists only in the
weaker mechanical form (`antislop.py` scans components/views for hex literals).
Token versioning, changelog, and the deprecation discipline (§2.1, §2.3) do not exist.

**A3. §3: eight screens vs a closed set of five.** The constitution closes the surface
set ("new surfaces require an RFC"). The UI ships river, calibration, simulator,
audit, shadow, **KEYS/settings, start tour, setup** — no RFC record for the additions.
(KEYS is arguably required by R8; the start tour is onboarding — but the constitution
says the set is closed *by default*, and there is no paper trail.)

**A4. Honesty gaps inside the strongest area (§4/P3).** Three concrete, checkable
violations of the laws the UI otherwise enforces well:
1. **Mock replay presents as live.** `Stream._startMock()` sets stream state `'live'`
   while replaying canned `stream-events.jsonl` recordings; `streamFreshness` then
   computes the tape badge as `live` with a fresh as-of (`api.js`, `freshness.js`,
   `views-river.js paintTapeFresh`). The static showcase correctly demotes snapshots to
   `cached — not live`; the mock path is strictly less honest using the same vocabulary.
   The page-level `◈ MOCK DATA` banner exists, but §4.4's own text says screen-level
   labeling does not satisfy point-of-display law — and here the freshness badge
   (the B1 health channel) actively contradicts the banner.
2. **Mock-derived vendor payloads render unmarked.** In mock mode `getDecision` (non-1042
   rows) fabricates `alert` client-side via a title regex (`severity_in: 'unknown'`,
   nulls — `api.js`). The drawer's "Unmapped bag — vendor payload" section renders it
   through `renderPayload` with **no reconstruction mark**, and its note claims "the
   raw payload is untouched above the viewer" — false in mock mode. This is the exact
   W1 shape (derived data rendered as if measured) surviving inside the screen that
   has the heaviest §4.4 machinery everywhere else.
3. **The "page me anyway" appeal button is phantom interactivity that can never act.**
   It renders on every decision drawer; in *all* modes (including production `live`)
   the click handler replaces it with "demo build: no page sent" (`app.js` `openDrawer`,
   `components.js:362-365`). §8.9: "if it's rendered, it works; if it doesn't work,
   it isn't rendered." In the production build the "Demo build" note is additionally
   false. For a *paging product*, a safety control that cannot act — that an operator
   at 3 AM may believe acted — is the most operationally dangerous finding in this
   review. (The palette's data-source toggle correctly hides itself when build-fixed
   — the team knows the rule; the appeal button is the exception.)

**A5. §5/§6/§7 gates missing as mechanisms:** no `prefers-reduced-motion` handling
anywhere (§5.4 says "respected unconditionally"); no performance budgets measured or
enforced (§6 — no TTFMP, no 50ms interaction, no 60fps, no bundle gate; worse,
`app.js` statically imports all eight views so the §6 rule "S4's heavy explorer code
never loads on the river" is **violated by construction**); SSE first retry is 5s,
not the §6-budgeted <1s; no contrast CI (§7 — token pairings are not checked);
no automated keyboard-navigation tests (§7); no CSP on the console (R23 names it —
`index.html` has no `Content-Security-Policy`, on a surface that handles API keys);
R25 trace-ID absent (no trace plumbing in `api.js`); R7 load-shedding signals absent
(the UI cannot shed simulator/shadow before river — it has no signal to obey).

**A6. §11 inbound requirements the UI needs but the platform doesn't supply**
(the constitution anticipated these; they remain open and they cap what the UI can
honestly claim): R1 freshness fields on the contract, R6 policy version +
counterfactual receipt schema (P2's five companions cannot be fully met — the drawer
meets 4.5 of 5), the sealed audit-chain endpoint (S4's "sealed and exportable"
30-second read is undeliverable — the derived chain is labeled but cannot attest),
the shadow join (S5's agreement card is honestly absent in live mode — the surface
"shown to buyers" cannot answer its own question against production data).

### B. The UI vs the Phase-1 enterprise standard

- **Design systems, not screens** (`principal-governance` §3; `swe-discipline` §1):
  elite practice is a versioned token/component library with generated API types and
  contract-tested boundaries. The UI has the *instincts* (generated `contract.js`,
  conformance tests, drift rows) but not the *system*: no TS types generated (F8's
  letter), no component catalog, two competing token docs, no visual-regression
  enforcement of the severity/freshness visual contracts (which the constitution
  itself calls "safety-critical").
- **No written decision precedes the code** (`swe-discipline` §1, `principal-governance` §1):
  the UI's major Type-1-shaped choices (vanilla JS over the constitution's React/TS;
  the DATA_MODE shim; client-derived shadow/chain substitutes) have no RFC with
  rejected alternatives. The constitution demands this ritual of others while its
  own adoption skipped it.
- **Reviewer-side bar absent** (`swe-discipline` §2): no CODEOWNERS, no published
  reviewer checklist wired to CI (the §8 checklist is prose; only its mechanical
  subset is enforced), no small-PR norm — the UI landed in multi-thousand-line PRs
  (#54, #68, #77).
- **Requirements traceability** (`swe-discipline` §3): no REQ-IDs, no traceability
  matrix REQ → component → test. The §9.2 invariants are the closest thing — excellent
  but unmapped to the R1–R25 supply chain, so a missing R-item (e.g. R6) has no
  mechanical tripwire.
- **Operational layering** (`architecture-patterns` §3): the UI is the accidental BFF
  the research names — UI-shaped endpoints aggregated without a declared BFF contract.
  Client-side derivation (shadow join, audit chain, mock simulate) is business logic
  that leaked past the API boundary because the contract is incomplete; the honest
  labels treat the symptom, not the cause.
- **Unified telemetry** (`principal-governance` §3, `architecture-patterns` §3.2):
  no trace/correlation ID from console interaction through platform to store — the
  "spinner diagnosable in one lookup" test fails by construction.

---

## 3. CONCRETE REVISION PROPOSALS

Ranked by value (global over local — each unlocks or gates the ones below it).
Every proposal states what would verify it; nothing here is "consider" or "explore."

### P0. Reconcile-or-migrate RFC: decide what the constitution actually is (Type 1)
**Rationale:** Every other proposal's acceptance criteria depend on which standard is
law. Today the constitution mandates a React/TS stack the shipped UI never adopted,
and two token documents disagree — so reviewers cannot tell whether a vanilla-JS PR
is compliant or a violation. A law nobody follows is worse than no law: it teaches
the org that laws are theater (the anti-theater audit, `principal-mindset` §11).
The two honest options: (a) migrate Prism to the F1–F10 stack, or (b) amend the
constitution to bless the vanilla stack with written rationale (zero-build
deployability, 284 KB total, no toolchain to rot — a real global-optimization
argument exists: the console's complexity may not justify React). Either is
defensible; the current middle state is not.
**Verify:** a dated RFC with written alternatives and a principal sign-off from
outside the UI lane; the constitution's header version bumped and §1/§2 amended to
match the decision; `DESIGN_SYSTEM.md` either merged or explicitly superseded.
**Why first:** `principal-governance` operating rule — no major component starts from
a feeling; the constitution's own §10 demands this of Type-1 changes, and adopting
the constitution *was* one.

### P1. Kill or wire the phantom appeal control
**Rationale:** "Page me anyway" renders on every drawer and can never page — in the
production build. §8.9 phantom interactivity on a *safety control* is the sharpest
P3 violation found: an operator may believe an appeal acted when nothing happened.
The honest options: wire it to a real appeal endpoint (POST appeal → audit-logged
override → real paging path, with the P5 read-path law preserved by making it an
explicit write action), or remove the button and keep the "dispute this suppression"
audit link. The "Demo build" note must go in the prod build either way.
**Verify:** in the `live` build, clicking appeal either pages through the configured
paging path with an audit-logged override event (asserted in CI against a mock
pager), or the control does not render — asserted by an extension of `antislop.py`'s
phantom-interactivity scan to cover rendered-but-inert controls.

### P2. Complete the platform read contract (R1/R6/sealed-chain/shadow-join)
**Rationale:** The UI's best honesty work is currently spent *labeling the absence*
of `policy_version`, `freshness_state`, the sealed chain, and the shadow join.
Labels treat the symptom; the cause is an incomplete contract. P2's five companions,
S4's "sealed and exportable," and S5's agreement metric are undeliverable in live
mode until the platform exposes: freshness/policy fields on `DecisionSummary`,
`GET /api/audit/chain` (platform-sealed), and the shadow join. This is also the
fix for the N+1 `getShadow` path (up to 200 sequential fetches) — a BFF-shaped
endpoint replaces client-side assembly, per `architecture-patterns` §4.2.
**Verify:** live-mode drawer shows policy version from contract (INV-3 updated to
assert presence, not honest absence); live S5 renders the agreement card from
platform data; audit explorer verifies platform seals; `getShadow` issues O(1)
requests. Until then, the derived substitutes stay — but marked as tech debt with
an owner and date, not as architecture.

### P3. Repair mock-mode honesty (replay ≠ live; derived payloads marked)
**Rationale:** Two W1-shaped survivors inside the best-labeled UI in the repo:
(a) mock replay sets stream state `'live'` — the freshness badge reads live during
canned playback; (b) mock-fabricated `alert` payloads render in the evidence drawer
unmarked, under a note claiming the raw payload is untouched. Both are small,
mechanical, and high-leverage — the honesty invariants exist precisely to catch
these, and they currently don't.
**Verify:** new invariants in `honesty.test.mjs`: (i) no stream state in mock mode
maps to freshness `live` (replay → `cached` with "recorded replay — not live",
mirroring the static snapshot treatment); (ii) any client-fabricated payload section
carries `derivedMark('reconstructed')` at the point of display — asserted
behaviorally on `getDecision` mock output through `drawerHtml`.

### P4. Implement the §9 testing pyramid gaps (Playwright flows, visual regression, contrast CI)
**Rationale:** 108 unit tests + conformance + antislop are real, but the pyramid's
top is missing: the five §9.1 critical flows (river→evidence companions,
keyboard-only acknowledge path, degraded-platform honesty, simulator labeling,
shadow bilateral evidence), §9.3 visual regression on the five surfaces × four
freshness states (the severity/freshness languages are safety-critical visual
contracts — a restyle regression is a safety regression), and §7 contrast CI on
token pairings. The 3 AM test (R15/R22) has no timed result file — the constitution's
own release gate has never been run.
**Verify:** CI runs the Playwright suite on every PR; visual baselines exist with
human-reviewed diffs; contrast check gates token changes; one dated
`design/3am-test-YYYY-QN.md` with stopwatch results committed per quarter.

### P5. Reconcile the token system and enforce it
**Rationale:** Two documents, two taxonomies, ordinal names violating the
semantic-only rule. Pick one taxonomy (recommend §2.2's, since the constitution is
the newer law — or formally keep `DESIGN_SYSTEM.md`'s and amend §2.2), migrate
`tokens.css`, and extend the F7 lint from "no hex in components" to "no token
outside the taxonomy" — a token allow-list generated from the single source.
**Verify:** `tokens.css` diffed against the single taxonomy file in CI; a component
consuming an unlisted token fails the build; `DESIGN_SYSTEM.md` marked superseded
or merged.

### P6. Performance budgets as measured gates + code-split the views
**Rationale:** §6 budgets are currently prose. `app.js` statically imports all eight
views, violating the §6 rule that S4's explorer code never loads on the river.
Measure first (TTFMP, interaction latency, bundle), then enforce: dynamic `import()`
per route, a CI bundle budget, and the §6 load-shedding order implemented against
real platform signals (R7) rather than only the SSE→poll fallback.
**Verify:** route-level code splitting (river initial JS measurably smaller);
CI budget check on total and per-route bytes; SSE first-retry <1s per the §6 budget
(currently 5s); a drill demonstrating shed order river-last.

### P7. Console security hardening (R23) + trace-ID (R25)
**Rationale:** A console that handles PagerDuty routing keys and Jev API keys ships
with no Content-Security-Policy and no secret-in-bundle grep in CI (the demo-mode
key ephemerality is good; the prod surface's hardening is unproven). Separately,
unified telemetry's "spinner to slow query in one lookup" is unimplementable without
a trace ID carried console→platform→store.
**Verify:** CSP header/meta present and pen-test clean before design-partner
exposure; CI secrets-grep over the served assets; every `Data` request carries a
trace ID visible in the UI's error states and joinable to platform logs.

---

## Appendix — reviewer notes (not findings)

- **What genuinely exceeds the standard:** the §9.2 honesty invariants as automated
  gates, the DATA_MODE build-fixed shim, the five-state registry, field pins'
  display-only invariant, the hostile-content payload renderer, and the
  regression tests for previously-shipped honesty bugs (INV-6). Whoever built these
  understood that honesty is a testable property, not a tone. The revision should
  preserve and extend this machinery, not rewrite it.
- **Pre-mortem (one year out, the console contributed to an incident):** the appeal
  button (P1) is the most likely vector — an operator believing an appeal acted.
  Second: mock-replay-as-live (P3a) during a game-day confusing "live" with
  "recorded." Third: S5 agreement shown to a buyer from mock fixtures mistaken for
  production evidence — the `◈ MOCK DATA` banner mitigates, but buyer decks
  screenshot past banners.
- **What I did not verify:** visual design quality against Aditya's bar (no
  screenshots taken — needs human eyes or the §9.3 pipeline); real-backend behavior
  of the SSE/gap paths (no live platform exercised); the `setup`/`settings` screens'
  full flows. The code claims are cited; the runtime claims are not.
- **Confidence:** 85% that the A-section findings are accurate as stated (all rest
  on read code with line citations); 60% that P0's option (b) — bless the vanilla
  stack — is the right call vs migration, pending the RFC's written alternatives.
  The ranking above is a judgment call under `principal-mindset`'s forced-probability
  rule, not a measurement.
