# Sentinel Interface Principles

**Version:** 1.0 — 2026-10-04
**Status:** LAW, not advice. Every interface lane builds against this file the way the engine lanes build against `ARCHITECTURE.md`.
**Authority:** Aditya's order 2026-10-03 — after the design showcase failed his visual judgment, this document replaces taste-based direction. Disagreements go through the RFC process in §10; until changed, this file wins arguments.
**Derived from:** `principal-systems` (the five laws, the constitutions), `principal-governance` (design systems, predictable hydration, parallel teams), `execution-doctrine` (pain first, manual first, validate → shadow → canary), `principal-mindset` (proxy traps, restatement test).

**How to read the labels:** every decision carries a **Type**. Type 1 = irreversible or expensive to reverse (stack choice, public contract, token architecture) — changing it needs an RFC with written alternatives. Type 2 = reversible — decide fast, measure, roll back if wrong. Unlabeled decisions default to Type 2 process, which is how Type 1 disasters happen — so everything structural is labeled.

**What this document is not:** it contains no theme recommendations, no "inspired by" references, no mood boards, no "make it feel premium." Taste is not a system. This file specifies contracts, budgets, states, and falsifiable gates. If a sentence in here cannot be checked by a test, a linter, or a timed operator exercise, it does not belong — and it isn't here.

---

## §0 — First principles: what the interface is for

**P0. The interface is a trust instrument, not a dashboard.**
Sentinel's product is not "alerts with a UI." The product is *a paging decision the operator can defend in a postmortem*. Every pixel either increases calibrated trust in a decision or decreases it. A screen that looks impressive but leaves the operator unsure why something paged (or didn't) is a failure, regardless of how it scores on any aesthetic rubric. *(principal-systems, product/design constitution: realized utility — a beautiful UI that adds operator friction is a failure.)*

**P1. The 3 AM test, operationalized.**
The design target is a sleep-deprived, cognitively impaired operator under time pressure. This is not a metaphor — it is a test protocol. Every primary screen must let that operator answer these five questions in under 30 seconds, timed, with no assistance:

1. What just paged, and how severe is it?
2. Why did it page — what evidence drove the decision?
3. What is the recommended action, and what happens if I do nothing?
4. Is the system itself healthy — is what I'm seeing live, cached, or degraded?
5. What did Sentinel suppress in the last hour, and can I see exactly why?

If a screen cannot pass this test, the screen is wrong — not the test. Run it quarterly with a real operator and a stopwatch. *(execution-doctrine: discover requirements by observing, not asking.)*

**P2. Show the work.**
No decision is ever rendered as a bare label. Every disposition shown in the interface carries five companions: the **evidence** (what the model saw), the **uncertainty** (quantized, never false precision), the **freshness** (as-of timestamp and live/cached/stale state), the **policy version** that produced it, and the **fallback reason** when the deterministic path decided instead of the model. A "suppress" row without these is not a minimal design — it is an unauditable one. *(principal-governance: zero data-model leakage does not mean zero evidence — the interface contract exposes decision provenance by design.)*

**P3. The honesty contract.**
The interface states its own condition at all times. Degraded, cached, stale, reconnecting, and uncertain states are rendered explicitly — never as blank screens, never as raw errors, never as silently invented data. The W1 incident (timer-win probability bars rendered as if measured) is codified here as law: **any derived, reconstructed, or simulated value is labeled in-band at the point of display**, not in a footnote, not in a tooltip. An interface that invents data to avoid an empty state is lying to an operator who makes safety decisions. *(principal-systems: eternal friction — the network will fail; design for that world.)*

**P4. Density is a feature.**
The operator is a professional doing triage, not a visitor being onboarded. Bloomberg-terminal-grade information density is the target: many meaningful values per viewport, scannable in seconds. Whitespace is not minimalism — whitespace that hides the fifth most important fact is a design defect. Marketing pages persuade; operator consoles inform. This console informs. *(principal-mindset: proxy-trap audit — "clean look" is a proxy; "time to correct action" is the objective.)*

**P5. The interface never pages, never suppresses, never asks Jev.**
Read paths make zero Jev calls — this is an engine law with interface consequences. The console renders **stored decisions** from the event log and platform store, with freshness metadata. It never triggers a new model evaluation to fill a screen. If the data isn't in the store, the screen says so (P3) instead of improvising. The UI sheds load before the paging path does, always. *(principal-systems, data/AI constitution: deterministic guardrails — no probabilistic system holds the critical path.)*

---

## §1 — Framework decisions: the production stack

These are decisions, not options. Each states the choice, the Type, the rationale citing the driving law, the rejected alternatives with reasons, and what would change it. The team does not re-litigate these in PRs — it files an RFC (§10).

### F1. Web console: React 18+ with TypeScript in strict mode
- **Type:** 1 — the stack choice shapes hiring, contracts, and five years of maintenance.
- **Rationale:** The frozen platform API is a typed contract; the console must be unable to misread it. Strict TypeScript with `noImplicitAny`, `strictNullChecks`, and **no `any` at any API boundary** makes contract drift a compile error instead of a 3 AM mystery. React's ecosystem carries the headless-component and data-fetching libraries this stack depends on (F3, F6).
- **Rejected:** Angular (heavier framework coupling, smaller hiring pool for this team shape); Vue (fine framework, wrong ecosystem for the libraries below); vanilla JS/HTMX (correct for marketing pages, insufficient for a stateful realtime console — global optimization: the console's complexity is real, the tool must match it).
- **What would change it:** a second client surface (e.g., a native incident-commander app) with genuinely different constraints — then the console stays React and the new surface gets its own RFC.

### F2. Build: Vite
- **Type:** 2 — build tooling is replaceable; the output contract (static assets + SSR shell, F5) is what matters.
- **Rationale:** Fast cold starts keep feedback-loop velocity high (execution-doctrine §6); standard enough that any engineer is productive in an hour.

### F3. State architecture: server state and client state are different substances
- **Decision:** All server state (decisions, events, calibration, config) lives in **TanStack Query**. All ephemeral UI state (selected row, panel open/closed, simulator inputs) lives in **Zustand**. The two never mix: no server payload is duplicated into Zustand, no UI flag is stuffed into the query cache.
- **Type:** 1 — state architecture is the hardest thing to migrate later.
- **Rationale:** Sentinel's product *is* freshness reasoning — stale-while-revalidate, background refetch, retry with backoff, and explicit staleness metadata are the domain, not plumbing. TanStack Query makes "this data is 40 seconds old and refetching" a first-class renderable state instead of a hand-rolled `useEffect` swamp. Zustand holds UI state because it is small, explicit, and doesn't pretend to be a server cache.
- **Rejected:** Redux Toolkit for server state (a client-state tool forced into server-state shape — the exact local optimization principal-systems warns against); hand-rolled fetch + useEffect (every team that does this rebuilds TanStack Query badly, then maintains it forever — code is a liability); stuffing everything into one store (mixing substances is how "the UI showed yesterday's decision" incidents happen).
- **What would change it:** nothing short of the state architecture proving unmaintainable in a postmortem — this is a five-year decision.

### F4. Realtime: Server-Sent Events, not WebSockets
- **Decision:** The decision river and live surfaces consume **SSE (`EventSource`)** through a single reconnect wrapper with exponential backoff, `Last-Event-ID` resume, and explicit connection-state rendering (connected / reconnecting / showing-cached).
- **Type:** 1 — the realtime protocol shapes the platform API contract.
- **Rationale:** The data flow is one-directional (platform → console). WebSockets buy bidirectional capability the console never uses, at the cost of connection management, proxy traversal pain, and a second failure mode. SSE reconnects natively, replays from `Last-Event-ID`, and degrades to plain HTTP semantics that every load balancer understands. Eternal friction: pick the protocol with fewer ways to fail.
- **Rejected:** WebSockets (unneeded bidirectionality); polling (latency and load for a paging-adjacent surface); gRPC-web (heavier contract machinery for a JSON-shaped domain).
- **What would change it:** a genuine bidirectional requirement (e.g., operator acknowledgments that must be exactly-once over the same channel) — then the RFC re-opens F4 with the new constraint stated.

### F5. Rendering: SSR shell, client hydration for live surfaces, zero layout shift
- **Decision:** The app shell and the audit explorer server-render for first paint; the decision river and live surfaces hydrate on the client. **Every async region allocates its pixel bounding box before content loads** — cumulative layout shift on a primary screen is a release-blocking defect, not a polish item.
- **Type:** 1 — rendering strategy determines the hosting contract and the hydration discipline.
- **Rationale:** At 3 AM, time-to-first-meaningful-paint is a safety property: the operator must see *something oriented* immediately. But the live surfaces are inherently client-stateful (F3/F4) — SSR-ing them would be theater. Predictable hydration (principal-governance) means the server HTML and the first client render agree exactly; skeletons mirror real layouts so hydration never visibly reflows.
- **What would change it:** measurement showing the SSR shell costs more (complexity, hosting) than it buys in observed time-to-orientation — then the shell goes static with the same CLS budget enforced.

### F6. Components: headless primitives + token styling, composed — never configured
- **Decision:** Interactive primitives come from a **headless component library** (Radix-style: behavior and accessibility without opinions about appearance); all visual identity comes from design tokens (§2). Components are **composed** from small parts, never configured through 40-prop APIs.
- **Type:** 2 for the library choice, Type 1 for the atomic structure (tokens → primitives → components → patterns → screens — §2).
- **Rationale:** Accessibility is then inherited, not re-implemented per component (keyboard handling, focus management, ARIA — §7). Styling stays in one system (tokens), so a rebrand or a contrast fix is a token change, not a 50-file edit. Composition over configuration keeps the component API surface small — code is a liability, and a 40-prop component is 40 liabilities.
- **Rejected:** a batteries-included UI kit with built-in styling (Material, Ant-style) — importing someone else's visual identity is how products end up looking like every other dashboard; the team would then fight the kit's opinions instead of expressing its own tokens.
- **What would change it:** the headless library going unmaintained — the primitives are thin and replaceable, which is the point.

### F7. Styling: tokens compiled to CSS custom properties at build time — no runtime CSS-in-JS
- **Type:** 1 — the styling architecture determines performance characteristics and the token contract.
- **Rationale:** Runtime CSS-in-JS evaluates styles in the browser on every render — a tax paid on the hottest path (the decision river) for zero product benefit. Tokens compile to CSS variables at build; the browser does what it's good at. Theming, contrast modes, and density modes become variable swaps, not re-renders.
- **Enforcement:** a lint rule bans raw color/space literals outside the token files (§2). `color: #3b82f6` in a component is a CI failure, not a style preference.
- **Rejected:** runtime CSS-in-JS (performance tax, debugging indirection); utility-class-only styling (Tailwind-style utilities are fine as an authoring aid, but every utility must resolve to a token — `p-4` is acceptable only if 4 maps to `--space-4`; arbitrary values `p-[13px]` are banned by the same lint rule).
- **What would change it:** nothing — this is a constraint, not a preference.

### F8. The API boundary is generated, validated, and contract-tested
- **Decision:** TypeScript types are **generated from the platform OpenAPI contract** — never hand-written at the boundary. Every API payload is validated at runtime with a schema validator (zod-style) before it enters the query cache. **Contract tests run in CI**: if the platform API changes shape, the console build fails loudly at generation time, not silently at 3 AM.
- **Type:** 1 — the boundary contract is what lets interface and platform teams work in parallel without integration surprises.
- **Rationale:** principal-governance: the contract is the coordination mechanism. Generated types make the console *unable* to drift from the platform; runtime validation makes the console resilient to the drift that happens anyway (Postel's law — conservative in what you send, liberal in what you accept, strict on emit). This is also what makes "design never waits for backend" safe: mocks are generated from the same contract, so a mock-shaped-like-contract is guaranteed, not hoped.
- **What would change it:** the platform API versioning scheme changing — then F8 follows the new scheme, the principle (generated + validated + tested) doesn't move.

### F9. Testing: the pyramid with honesty invariants at the top
- **Decision:** Vitest + Testing Library for unit/interaction tests; Playwright for the critical user flows (§9 lists them); **visual regression in CI** (screenshot diffing on the five primary screens); and a set of **honesty invariants as automated tests** — e.g., "no data component renders without a freshness indicator when its payload is older than N seconds," "every reconstructed value carries its label," "no screen renders a bare disposition without its evidence companions" (P2).
- **Type:** 2 for tooling, Type 1 for the invariant list — the invariants in §9 are release gates.
- **Rationale:** Testing Library tests user-visible behavior, not implementation — the tests survive refactors. Playwright covers the flows where failure costs sleep (acknowledge, investigate, appeal a suppression). Visual regression is the anti-slop enforcement arm: an accidental restyle of the severity language fails the build. The honesty invariants turn P2/P3 from prose into gates.
- **What would change it:** tooling churn — the gates are the constant.

### F10. Component catalog with contract docs
- **Decision:** Every shared component ships in a **catalog** (Storybook or equivalent) with its contract documented: props, the five states (§4), accessibility notes, and do/don't examples drawn from real Sentinel screens — not abstract placeholders.
- **Type:** 2.
- **Rationale:** The catalog is where "design never waits for backend" becomes concrete: interface lanes build screens from catalog components against contract-shaped mocks. It is also the truck-factor document for the UI — a new engineer learns the system from the catalog, not from tribal knowledge. *(principal-governance checklist: truck-factor docs.)*

---

## §2 — The design system as code

### 2.1 Tokens are the system; everything else is derived
A design token is a named, versioned decision about a visual value. Tokens live in **one repository location**, versioned like an API — because they *are* an API: every component consumes them, and changing one changes the product. Token changes follow the same discipline as API changes: explicit version, changelog entry, and a visual-regression run (§9) before merge. Rebranding, contrast fixes, and density modes are token changes in one place — never 50-file edits. *(principal-governance: atomic design systems; principal-systems Type 1 — the token architecture is expensive to reverse.)*

### 2.2 The token taxonomy
Tokens are **semantic**, never literal. No component ever consumes `--blue-500`; it consumes `--severity-critical-bg`, which *resolves to* a scale value. The literal scales exist underneath for the token authors; the semantic layer is the contract.

| Category | Token pattern | Examples | Notes |
|---|---|---|---|
| Color — surfaces | `--surface-{base,raised,overlay,sunken}` | `--surface-base`, `--surface-raised` | 4 surfaces max. More surfaces = visual noise, not hierarchy. |
| Color — text | `--text-{primary,secondary,muted,disabled,inverse}` | `--text-primary` | Contrast ratios against their surfaces are CI-checked (§7). |
| Color — severity | `--severity-{critical,major,minor,info}-{bg,border,text}` | `--severity-critical-bg` | The ONLY hues with intrinsic meaning. See §8: color is semantic-only. |
| Color — state | `--state-{live,stale,degraded,error}-{...}` | `--state-stale-text` | Freshness language (§4) has reserved color roles. |
| Color — interactive | `--action-{primary,hover,active,disabled,danger}` | `--action-primary` | One primary action color. One. |
| Type | `--font-{text,mono}` + `--text-{xs…2xl}` scale | `--font-mono`, `--text-sm` | Two families, fixed scale — §5. |
| Space | `--space-{1…16}` on an 8pt grid | `--space-4` (=16px) | No off-grid values. Ever. |
| Motion | `--motion-{instant,fast,base,slow}` + easings | `--motion-fast` | Durations only; §5 restricts what may move. |
| Elevation | `--elevation-{0…4}` | `--elevation-2` | Shadows are functional (layering), not decorative. |
| Radius | `--radius-{sm,md,lg,full}` | `--radius-sm` | Small set; `full` reserved for pills/badges. |
| Density | `--density-{comfortable,compact}` | spacing scale multiplier | Compact is the operator default (P4); comfortable exists for accessibility. |

**Rules:** (1) No raw literals outside token files — lint-enforced (F7). (2) No new token without a semantic name and a documented consumer. (3) Token deprecation follows the API deprecation discipline: mark, migrate, remove — never silent deletion.

### 2.3 Atomic structure — and its enforcement
Tokens → **primitives** (headless behavior, F6) → **components** (DecisionRow, FreshnessBadge — App. A) → **patterns** (evidence panel layout, investigation flow) → **screens** (§3). Each layer may only consume the layer below it. A screen reaching past components directly to tokens for a one-off style is a **one-off component**, and one-off components are banned: if the need is real, it becomes a component with a contract and five states; if it isn't, it doesn't ship. The ban is enforced by code review with §8's checklist, not by hope.

### 2.4 Theming without overrides
Dark mode, high-contrast mode, and density modes are **token value swaps**, never component overrides. A component that needs a special case for dark mode is a component with a missing token. This is what makes the system maintainable at 5× its current size: the number of theming code paths is exactly one.

---

## §3 — Information architecture: five surfaces, five questions

The console has exactly five primary surfaces. Each answers **one** question — stated below — and is judged by whether the 3 AM operator gets that answer in under 30 seconds. Anything that doesn't serve its surface's question doesn't belong on it. New surfaces require an RFC; the set is closed by default because every new surface is a new maintenance liability. *(principal-systems, software constitution: the best PR deletes code — the best IA deletes surfaces.)*

**S1. Decision river** — *What is happening right now?*
The live stream of paging decisions: severity, service, disposition, confidence, freshness, one-line evidence. Virtualized for 10k+ rows (§6). This is the landing surface. Its 30-second read: "three criticals paged in the last 10 minutes, all with fresh evidence, system live."

**S2. Calibration & evidence** — *Why should I believe a decision?*
Per-decision deep view: the full evidence bundle, the quantized probability with its calibration context, the freshness proofs, the policy version, the counterfactual receipt ("at your 0.85 this stood down; at 0.70 it would have paged"). This is where P2 (show the work) lives. Its 30-second read: "I see exactly what the model saw and why 0.87 cleared the bar."

**S3. Threshold simulator** — *What would change if I moved the bar?*
What-if analysis against historical decisions: move a threshold, see which past decisions flip. Every simulated value is labeled as simulation (P3 — the W1 law applies with full force here: the simulator is the one surface where *everything* is derived, so its labeling must be unmistakable). Its 30-second read: "raising to 0.90 would have suppressed 12 more pages last week, including 2 I wouldn't have wanted suppressed."

**S4. Audit explorer** — *Prove what happened.*
The event log made navigable: hash-chained, filterable, exportable. Timeline-first. This surface serves the postmortem and the auditor, not the triage moment — so it optimizes for completeness and verifiability over speed. Its 30-second read: "here is the unbroken chain for incident #4821, sealed and exportable."

**S5. Shadow report** — *Is Sentinel earning the cutover?*
Divergence between Sentinel's shadow decisions and what actually paged: agreement rates, disagreements with evidence on both sides, calibration drift, mute review ritual (ADR-007). This is the surface that earns production trust — and the surface shown to buyers. Its 30-second read: "last 7 days: 94% agreement, 6 disagreements, all 6 reviewed, 2 led to threshold changes."

**Navigation follows the incident workflow**, not a marketing sitemap: river → evidence → (simulate | audit) → shadow. Cross-links are contextual (a river row deep-links to its evidence bundle and its audit chain), never a generic nav bar of equals. The information architecture is a workflow with five stations, not a dashboard with five tabs.

---

## §4 — The honesty UI: rendering uncertainty, staleness, and degradation

### 4.1 The freshness contract (interface side)
Every component that renders data carries three inseparable companions: the **value**, the **as-of timestamp**, and the **freshness state**. The four freshness states form a closed vocabulary used identically across all five surfaces:

- **live** — data is current within the surface's freshness budget; rendered normally.
- **cached** — served from cache while refetching; the as-of time is shown, never hidden.
- **stale** — exceeded the budget; the component renders with the stale treatment (reserved `--state-stale-*` roles) and states what it's waiting on.
- **degraded** — the source is unreachable; the component shows the last known value *labeled as last-known* plus the explicit reason, or an honest empty state (§4.3) — never a spinner that implies progress, never a blank.

The freshness budget is per-surface and stated in the surface's contract (S1: seconds; S4: the audit log is immutable — freshness means chain-verified). "Unknown freshness" is not a fifth state — it is a bug.

### 4.2 Uncertainty rendering: quantized, never false precision
Probabilities render at the quantization the engine actually produces — `0.87`, never `0.87134`. Rendering more digits than the engine's quantization is false precision: it implies a measurement resolution that doesn't exist and invites operators to over-interpret noise. Confidence displays pair the number with its calibration context (S2) — a bare 0.87 is P2-violating. Where the engine reports a range or a lock state instead of a point estimate, the UI renders the lock state ("awaiting calibration fit — suppression held conservative"), not a faked number.

### 4.3 The five states of every component
Every data-bearing component is designed in five states, each with an explicit design — **blank is never one of them**:

1. **loading** — skeleton mirroring the real layout, bounding box pre-allocated (F5, zero CLS).
2. **ready** — the P2 companions present (evidence, uncertainty, freshness, policy version).
3. **stale/degraded** — §4.1 treatments; the operator always knows the data's condition.
4. **error** — what failed, in operator language; what to do next; what is unaffected. Never a raw stack trace, never "Something went wrong."
5. **empty** — "no suppressions in the last hour" with the query scope stated, not an empty table that could mean either "none" or "broken."

A component that reaches code review with only state 2 designed is sent back. This is the single highest-leverage interface rule in this file: most "bad UI" in production is not bad aesthetics — it is unhandled states.

### 4.4 The reconstruction law
Any value that is derived, reconstructed, simulated, or backfilled is **labeled in-band at the point of display** — a visible marker with the same visual weight as the value itself, stating what the value is ("reconstructed," "simulated," "backfilled from X"). Footnotes, tooltips, and documentation links do not satisfy this law. The simulator (S3) labels its entire surface. Timer-win fallbacks, interpolated series, and estimated aggregates all carry the mark. Violation is a release-blocking defect — it was a real incident (W1), and incidents become laws.

### 4.5 The interface reads; it never evaluates
Restating P5 as a component rule: no component triggers model evaluation to render itself. If the store lacks the data, the component renders state 5 (empty, scope stated) or state 4 (error, reason stated). A component that "helpfully" fetches a fresh evaluation to fill a gap has violated the engine's read-path law and created an unlogged, uncalibrated decision surface. The platform API the console consumes is read-only by contract (F8); the console treats it as such.

---

## §5 — Cognitive load engineering

### 5.1 The hierarchy law
On every screen: **severity → action → evidence**, in that visual order. The recommended action is always one glance away from the severity — the operator never hunts for "what do I do." Detail is progressive: the 30-second read is visible without interaction; the 30-minute investigation is one deliberate drill-down away (S1 → S2), not scattered across tabs.

### 5.2 Interaction prohibitions
These are banned, without exception, on the five primary surfaces:
- **Modals for critical information.** A modal is a context destroyer; critical information lives in the flow, not in a box that must be dismissed.
- **Hover-only information.** Anything that matters must be visible without hovering — touch devices, keyboard users, and stressed operators don't hover reliably.
- **Critical information in tooltips.** Tooltips are for supplementary detail. If removing the tooltip removes the meaning, the design is wrong.
- **Confirm dialogs on read actions; missing confirms on destructive ones.** Reads never confirm. Destructive actions (mute with TTL, threshold changes) confirm with the consequences stated in the dialog — and even then, prefer undo over confirm where the domain allows.

### 5.3 Typography as the interface
Two families, no exceptions: `--font-text` for prose and labels, `--font-mono` for identifiers, timestamps, probabilities, and anything the operator might copy. Numerals are **tabular** everywhere a number appears near another number (timestamps, confidence, counts) — proportional numerals in a data table make values unrhythmable at a glance, which is a readability defect with safety consequences. The type scale (§2.2) is fixed; a screen that needs a seventh size has an information-architecture problem, not a typography problem.

### 5.4 Motion: functional only
Motion acknowledges state changes (a row entering the river, a freshness badge flipping live→stale). It never decorates, never entertains, never draws attention to itself. Durations come from `--motion-*` tokens; `prefers-reduced-motion` is respected unconditionally — and the interface must remain fully usable with all motion disabled, because for some operators it will be. A transition that exists to "feel premium" is removed in review.

### 5.5 Keyboard-first operation
The full critical path — navigate the river, open evidence, acknowledge, jump to audit — is operable by keyboard, with visible focus at all times. The operator's hands are on the keyboard at 3 AM; forcing a mouse round-trip for a frequent action is a latency tax on incident response. Keyboard shortcuts are documented in a discoverable cheat sheet, not tribal knowledge. Focus is never lost into the void: every dialog, panel, and navigation moves focus deliberately and returns it.

---

## §6 — Performance budgets

Budgets are Type 2 (tunable with measurement), but they are budgets — exceeding one is a defect with an owner, not an observation.

| Budget | Value | Rationale |
|---|---|---|
| Time to first meaningful paint (shell + river skeleton) | < 1.5s on broadband, < 3s on throttled 4G | P1: orientation speed is a safety property |
| Interaction latency (row select, panel open) | < 50ms | Above ~100ms the interface feels broken to a fast operator; 50ms leaves headroom |
| Decision river scroll | 60fps at 10,000+ rows | Virtualized/windowed rendering is mandatory, not an optimization — the river is unbounded by nature |
| SSE reconnect | first retry < 1s, backoff capped at 30s, `Last-Event-ID` resume | P3: the reconnecting state must be brief and honest; resume avoids gaps the operator would otherwise miss |
| Bundle (initial, gzipped) | < 250KB | Code-split by surface; S4's heavy explorer code never loads on the river |
| Background refetch | never blocks interaction | Stale-while-revalidate (F3): the old value stays rendered with its freshness badge while the new one loads |

**Load-shedding order** (P5, made concrete): when the platform is under stress, the console degrades in this order — (1) pause background refetch, lengthening freshness badges honestly; (2) drop to cached snapshots with explicit labeling; (3) shed the simulator and shadow surfaces before the river and evidence. The river and evidence are the last things to degrade. The paging path is never affected by console load — different fate domain, enforced by the platform, assumed by the UI.

---

## §7 — Accessibility as CI gates, not charity

WCAG 2.2 AA is the floor. The following are automated gates, not aspirations:

- **Contrast:** every `--text-*` on every `--surface-*` pairing is contrast-checked in CI. A token change that breaks a pairing fails the build.
- **No color-only semantics.** Severity, freshness, and disposition are never conveyed by color alone — each has a text label, an icon shape, or both. (This also serves the 3 AM operator on a dimmed screen.)
- **Keyboard map:** the critical path (§5.5) has automated keyboard-navigation tests in Playwright.
- **Screen reader:** dispositions, confidence values, and freshness states have text equivalents — a screen-reader user gets the same five answers as the 3 AM test (P1).
- **Density mode** (§2.2) exists for low-vision operators; `prefers-reduced-motion` (§5.4) is honored; text scales to 200% without horizontal scrolling on primary surfaces.

Accessibility bugs are filed at the same severity as functional bugs — because for the operators who need them, they are functional bugs. *(principal-systems: eternal friction includes the operator's own constraints.)*

---

## §8 — The anti-slop specification

"AI-generated-looking" is not a vibe judgment — it is a checklist. A screen fails design review if any item below is true. Slop is sent back like a failing test (principal-governance operating rule 3).

1. **The logo-removal test.** Remove the logo and product name. If the screen could be any SaaS product, it has no identity — it is a template with data. Identity comes from domain-specific information architecture (§3), opinionated density (P4), and the honesty language (§4) — not from decoration.
2. **No gradient-as-identity.** Decorative gradients — especially dark purple/blue — are the single strongest marker of generated-looking design. Surfaces are flat token colors (§2.2). If a gradient appears, it must encode data (e.g., a calibrated heat scale) or it is removed.
3. **No emoji as iconography.** Severity and state icons are drawn shapes with consistent stroke and geometry, from one icon set. Emoji render inconsistently across platforms and read as informal in a safety surface.
4. **No decorative glow, orbs, sparkles, or "AI" ornament.** Every visual element must be removable without losing meaning. If removing it loses nothing, it was decoration — remove it in review, not in production.
5. **Density floor.** A primary surface showing fewer than ~15 meaningful values per viewport on desktop is hiding information behind interaction. (Marketing pages are exempt; operator surfaces are not.)
6. **Typography discipline.** Two families (§5.3), the fixed scale, tabular numerals. A screen with four font sizes doing the job of two has no hierarchy.
7. **Semantic color only.** Every color on screen means something from the token taxonomy (§2.2). Decorative color — color chosen to "add interest" — is banned. Muted grays are not "boring"; they are the background against which the semantic colors can speak.
8. **Microcopy in the operator's language.** "Suppress with 0.87 confidence (policy v14, evidence fresh as of 03:12:04)" — not "AI Insights suggest this alert may be safely ignored." No marketing verbs (unlock, supercharge, elevate), no anthropomorphizing the model, no hedging that obscures the decision. The copywriter's test: would an SRE say this sentence in a postmortem? If not, rewrite it.
9. **No phantom interactivity.** Buttons that don't do anything yet, tabs with "coming soon," charts that can't be interrogated — if it's rendered, it works; if it doesn't work, it isn't rendered. A disabled control states why it's disabled.
10. **The screenshot test.** Screenshot the screen at 50% size. If the severity ordering, the actions, and the freshness states are still legible, the hierarchy works. If it dissolves into gray boxes, the design was decoration-dependent.

**Design review is a gate equal to code review.** A PR that touches the console gets both. The reviewer uses this checklist literally — "fails item 7, the amber here is decorative" is a complete review comment.

---

## §9 — Interface testing strategy

### 9.1 The critical flows (Playwright, run on every PR)
1. River → open evidence → verify the five companions (P2) are present.
2. Acknowledge/escalate path completes and is keyboard-operable end to end.
3. Freshness degradation: with the platform stubbed slow/unreachable, every surface shows its honest degraded state — no blank screens, no spinners-forever.
4. Simulator: move a threshold, verify every derived value carries the simulation label (§4.4).
5. Shadow report: disagreement rows link to evidence on both sides.

### 9.2 The honesty invariants (automated gates — Type 1)
- No data component renders without a freshness indicator when its payload exceeds the surface's freshness budget.
- No reconstructed/simulated/derived value renders without its in-band label.
- No disposition renders without its evidence companions (P2).
- No screen renders a raw error or blank state for a handled failure mode (the five states, §4.3).
- Console network traffic contains zero model-evaluation calls on read paths (P5) — asserted against a request log in CI.

### 9.3 Visual regression
Screenshot diffing on the five primary surfaces, in all four freshness states and the five component states where applicable. Diffs are reviewed by a human — the gate catches accidental restyles of the severity and freshness languages, which are safety-critical visual contracts.

### 9.4 The 3 AM test, quarterly
A real operator, a stopwatch, the five questions (P1), timed. Results go in the repo next to the test run. If the 30-second bar slips, it slips in writing — and the next interface lane's first job is winning it back. *(execution-doctrine: feedback-loop velocity — the operator's finding becomes a scheduled fix, not a backlog ghost.)*

---

## §10 — How teams work with this file

**Changing a Type 1 decision** requires an RFC: the problem, the alternatives explored and rejected with reasons, the migration/rollback plan, and sign-off from a principal reviewer outside the proposing lane. Type 1 changes are rare by design — if they're frequent, the decisions were wrong, and that's useful information too.

**Changing a Type 2 decision** requires a note in the PR and a measurement plan. Decide fast, measure honestly, roll back without shame.

**The parallel-teams rule** (principal-governance): interface lanes build against the frozen platform contract (F8) with contract-shaped mocks from hour one. "The platform team hasn't finished the endpoint" is never a stall — the contract is the coordination mechanism. If the contract itself is wrong, that's an RFC, filed the same hour, not a blocker carried for a week.

**Design review gates PRs** that touch the console, using §8's checklist. **The 3 AM test** (§9.4) gates releases. **This file's own changelog** lives at the bottom — every amendment dated, with its RFC or PR reference. A principles file that never changes is a file nobody reads; a principles file that changes without a record is tribal knowledge with extra steps.

---

## §11 — What each team owes: inbound requirements

The principles above are laws for the interface lanes. Laws need supplies. This section lists the actual requirements each team must provide — without these, the interface team cannot execute, and "waiting on backend" becomes a real dependency instead of an excuse. Each item has an owner, a consumer, and an acceptance check. A missing item is logged in the lane registry the same hour (parallel-teams rule, §10) — flagged, never silently waited on.

### 11.1 Platform team → interface team

| ID | Requirement | Serves | Acceptance |
|---|---|---|---|
| R1 | Frozen, versioned OpenAPI contract for the read API + SSE event schemas, with freshness fields (`as_of`, `freshness_state`) on every data payload | F8, §4.1 | Console generates types from it in CI; contract drift fails the build |
| R2 | Contract-shaped mocks: a mock server or fixture set **generated from the same contract**, never hand-written | F8, §10 | Interface lanes build screens from hour one; mock-vs-contract conformance test green |
| R3 | Freshness budgets per surface (S1–S5): the number each FreshnessBadge renders against | §4.1 | Stated in each surface contract; badge behavior tested against it |
| R4 | Error catalog: every handled failure mode with operator-language message, what-to-do-next, and what's unaffected | §4.3 (state 4) | For each catalog entry a designed error state exists; an unlisted failure is a platform-side bug |
| R5 | Disposition + severity vocabularies: exact enums, labels, ordering | §3, §8 | The console renders them; the console never defines new ones |
| R6 | Policy version identifiers + counterfactual receipt schema | P2; S2/S3 | Every rendered decision carries its policy version |
| R7 | Load-shedding signals: how the platform tells the console to degrade, in the §6 order | §6 | Under stubbed stress the console degrades river-last (drill-verified) |
| R8 | Console authN/Z contract: identity and roles — who may attest a mute, change a threshold, appeal | ADR-007/022 governance | Destructive actions gate on role; unauthenticated console exposes nothing sensitive |

### 11.2 Design team → the build

| ID | Requirement | Serves | Acceptance |
|---|---|---|---|
| R9 | Token values: the complete token file with real values for every token in §2.2, contrast pairings verified | §2 | One `tokens.json` in one repo location; contrast CI green |
| R10 | Component catalog (F10): every shared component with contract docs and all five states designed | §4.3, F10 | Each catalog entry passes the §8 checklist in review |
| R11 | The five screens, built against R1/R2 from hour one — before the platform endpoints exist | §3, §10 | Screens render against mocks; zero "waiting on backend" stalls |
| R12 | Icon set: drawn shapes, one set, consistent stroke and geometry — severity and freshness icons included | §8 item 3 | No emoji anywhere in the console (lint-enforced) |
| R13 | Microcopy in operator language, reviewed by someone who understands the domain | §8 item 8 | The postmortem test: an SRE would actually say these sentences |
| R14 | Design review as a gate: a named reviewer on every console PR | §8, §10 | No console PR merges without the checklist pass |

### 11.3 Product (Aditya) → the teams

| ID | Requirement | Serves | Acceptance |
|---|---|---|---|
| R15 | A real operator, quarterly, for the 3 AM test | §9.4 | A timed run with written results exists in the repo |
| R16 | Severity taxonomy sign-off: what critical/major/minor/info mean for the design partners | §3 | Documented; the R5 enums trace to it |
| R17 | Type 1 decisions on RFCs inside an agreed window — or explicit delegation of the call | §10 | No RFC waits past the window without a named reason |
| R18 | Validation of the five 3 AM questions (P1): are these the right five for the buyers? | P1 | Signed off or amended in writing |

### 11.4 QA (Tripwire) → the gates

| ID | Requirement | Serves | Acceptance |
|---|---|---|---|
| R19 | The honesty invariants implemented as automated CI gates | §9.2 | All five invariants run on every PR; violation blocks merge |
| R20 | Visual regression baselines: five surfaces × freshness states × component states | §9.3 | Baseline set exists; diffs get human review |
| R21 | Playwright critical flows | §9.1 | All five flows green on every PR |
| R22 | Quarterly 3 AM test execution, with written results committed to the repo | §9.4 | One dated result file per quarter |

### 11.5 Security (Vault) → the console

| ID | Requirement | Serves | Acceptance |
|---|---|---|---|
| R23 | AuthN/Z implementation, content-security policy, and a guarantee of no secrets in the frontend bundle | F8 boundary | Secrets-grep in CI; console auth pen-tested before design-partner exposure |
| R24 | Audit schema for console actions (mute, threshold change, appeal): what gets event-logged and where | ADR-007/022 | Every destructive console action writes its event; asserted in CI |

### 11.6 Telemetry → debuggability

| ID | Requirement | Serves | Acceptance |
|---|---|---|---|
| R25 | Trace-ID from console interaction through platform to store | principal-governance §3 | The actual test: a spinner on screen is diagnosable to the slow query in one lookup |

**The rule for missing requirements:** any R-item not provided when a lane needs it is (a) logged in the lane registry the same hour, (b) worked around against the contract + mocks — and (c) never a license to violate P2/P3. "We didn't have R4, so we rendered a raw error" fails review. The interface team owns the honesty contract even when the platform team is late; the platform team owns the unblocking, on the record.

---

## Appendix A — Component contracts (initial set)

Each component documents: props, the five states (§4.3), accessibility notes. New shared components get a contract entry before code.

- **`<DecisionRow>`** — one river row: severity tag, service/check, disposition + confidence (quantized), as-of + freshness badge, one-line evidence. States: all five; empty n/a (river shows the honest empty, §4.3-5).
- **`<FreshnessBadge>`** — the closed four-state vocabulary (§4.1) with text label, never color-only (§7).
- **`<ConfidenceMeter>`** — quantized value + calibration context link; never renders digits beyond engine quantization (§4.2).
- **`<DispositionTag>`** — the four engine dispositions; muted (ADR-007) renders as a platform-tier label over suppress, visually distinct from engine dispositions.
- **`<EvidencePanel>`** — the P2 companions: evidence bundle, uncertainty, freshness, policy version, fallback reason. The S2 building block.
- **`<ReconstructionMark>`** — the in-band label (§4.4); mandatory wherever derived values render.
- **`<TimelineExplorer>`** — S4's chain view: hash-linked events, sealed checkpoints, export.
- **`<ThresholdSimulator>`** — S3's what-if surface; the entire surface carries the simulation treatment.
- **`<ShadowDiffTable>`** — S5's agreement/disagreement rows with bilateral evidence links.
- **`<DegradedBanner>`** — the honest degraded state: what failed, what's shown instead, what to do.

## Appendix B — Pre-mortem: the interface failed in production

*Assumed: one year from now, the console contributed to a real incident. What happened?*

1. **Stale-as-live.** A freshness badge regressed to color-only in a restyle; an operator acted on a 40-minute-old decision during a fast-moving incident. *Mitigation:* the honesty invariants (§9.2) — freshness text is asserted, not just rendered; visual regression covers the badge in all four states.
2. **Simulator confusion.** An operator mistook simulated outcomes for historical fact and argued a threshold change in a review using simulated numbers as evidence. *Mitigation:* the reconstruction law (§4.4) with full-surface simulation treatment; simulator exports watermark every value.
3. **Alert fatigue via density.** The river's density (P4) became noise at 3 AM during a storm — the operator missed the one critical row among hundreds. *Mitigation:* severity ordering is structural (storm-collapse, §3 S1 contract), not cosmetic; the river's storm mode is a designed state with its own 3 AM test, not an edge case.

## Changelog

- **2026-10-04 v1.0** — Initial ratification. Written after the design showcase failed Aditya's visual judgment; replaces taste-based direction with contracts, budgets, and gates.
- **2026-10-04 v1.1** — Added §11: inbound requirements each team owes (R1–R25) — the supplies without which the laws can't execute.
