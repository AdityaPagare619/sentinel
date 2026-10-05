# UI Redesign — Information Architecture & Core Flows (Beat 1)

**Lane:** ui-redesign (Prism) · **Date:** 2026-10-05 · **Status:** Beat 1 decision record. Beat 2 builds from this.
**Inputs:** `design/INTERFACE_PRINCIPLES.md` v1.2 (§12 lineage, §13 critical surfaces, §14 anti-fatigue are law),
`design/screens/*.md` (10 specs), `docs/planning/USER-WORKFLOWS.md`,
`docs/planning/PROFESSIONAL-TEARDOWN.md` (R-UI-1..25), `docs/planning/USER-RESEARCH.md` (N1..N12, A1..A8),
`docs/planning/DESIGN-DECISION-FRAMEWORK.md` (M1..M10, D1..D10, T1..T8, prescription phases, F1..F8),
`docs/architecture-revision/PIPELINE-REVISION.md` (R-findings).

---

## Phase 0 — The main thing (M8)

**At 3 AM, the operator knows exactly what the machine decided, why, and what to do next — in under 30 seconds.**

Ratified as stated in the framework. Everything below is judged against this sentence.

## Phase 1 — Job statements (M2, all three dimensions)

**J-paged (the 80% case, D5).** "When my phone goes off at 3 AM, I want to know what's broken, how bad it is,
and what to do first, so I can stop the bleeding without waking up fully."
Functional: orient → decide real-or-noise → mitigate. Emotional: feel in control, not ambushed; the machine is
a colleague that shows its work, not a stranger that hides things. Social: be the responder who had the full
picture in 30 seconds. Anxiety (the design target, per the milkshake lesson): *the machine suppressed something
it shouldn't have, and I won't know.*

**J-storm.** "When fifty things fire at once, I want to see the one problem — not fifty alerts — and know the
machine is absorbing the noise with proof I can inspect later, so I can work the fire instead of the feed."
Emotional: calm inside the flood; the flood is the machine's job, the fire is mine.

**J-review.** "When I review what the machine suppressed overnight, I want each decision's evidence and the
per-class track record, so I can trust it again tonight." Emotional: trust rebuilt daily, not assumed.
This is the job no vendor's UI serves (teardown §4.1) — it is Sentinel's invention space and the redesign's
differentiator.

## Phase 2 — Conceptual models (M1) + thermostat tests (T6)

**Design model (how it actually works, plain sentences):** Alerts enter the receiver. The correlator groups them
into problems (one problem, many alerts). Each problem races: Jev judges against a bounded timer; the timer
winning means the deterministic gate decides alone. The gate dispositions page-or-suppress with a reason code
and proof (cues, thresholds, evidence freshness). The forwarder pages humans through real channels. Every
suppression is an auditable, reversible, time-bounded decision. The kill switch stops paging. When the machine
is uncertain, stale, or degraded, it pages — never suppresses.

**Predicted user model (what the screen must build):** "Sentinel watches my alerts and wakes me only for real
problems. Everything it suppresses, I can inspect — the proof is attached. If I stop trusting it, the kill
switch is right there. It would rather page me than be wrong quietly."

**Bridges:** the NOW screen's pipeline strip makes the design model visible as a living thing; the PROOFS ledger
makes every suppression inspectable; the kill-switch state is ambient chrome, never a settings page.

**Thermostat tests (wrong models the tired operator will form, and the screen's correction):**
- Kill switch → "flipping it pages everyone instantly." Correction: the KILL screen states the engagement
  contract in plain words (paging stops; in-flight races resolve as pages; the river annotates the window).
- Suppression → "the machine hides things." Correction: PROOFS ledger — every suppression browsable,
  recoverable, with undo next to it.
- Simulated → "this is live data." Correction: in-band SIMULATED labeling on every surface + the generator
  itself inspectable (seed, event log, "why this number" on every count).
- Grouped → "grouped means silenced." Correction (R-UI-15): grouped rows render notification state separately
  from group state, always.
- Green dashboard → "all quiet means all well." Correction: the pipeline-health strip shows degraded/stale as
  first-class states; "nothing on fire" is never the headline when the pipeline itself is sick (R-UI-24).

## Phase 3 — Friction log of the current UI (M3; code-derived, honest method note)

*Method honesty: as a subagent I cannot walk the live staging site in a browser. This log is derived from
reading the shipped code (platform/ui) plus Aditya's verdict. Scored against the 30-second test.*

| # | Journey | Friction | Score |
|---|---------|----------|-------|
| 1 | Open the console fresh (staging). What do I see? | START/onboarding tour, not the system state. The first screen teaches the product instead of showing what's happening. A 3 AM operator lands in a tutorial. | 🔴 |
| 2 | "What's happening right now?" | No surface answers this. RIVER shows the decision tape (volume-ordered), but there is no pipeline view, no open-pages view, no health strip. The operator must assemble "happening" from a tape. | 🔴 |
| 3 | A page arrives. What do I see first? | The river row: timestamp, severity, disposition, confidence, team. No cost-of-inaction, no pre-attached blast radius, no "what changed recently." Evidence requires opening the drawer and navigating. (R-UI-2/5/6 violated.) | 🔴 |
| 4 | What did the machine suppress, and why? | Suppressions appear as river rows with reason codes — browsable only by filtering the tape. No proof ledger, no per-class track record, no unified "suppressed in the last hour and why" surface. (R-UI-7/8/9 violated.) | 🔴 |
| 5 | Where is the kill switch? | No screen. The F1 kill condition — the single most critical safety control — has no UI surface at all. | 🔴 |
| 6 | Is the pipeline itself healthy? | SSE badge shows stream state; no pipeline-lag, no correlator/race/gate/forwarder stage visibility, no degraded-mode surface. A sick pipeline looks identical to a quiet one. (R-UI-24 violated.) | 🔴 |
| 7 | Simulated mode: can I tell what's real? | SIMULATED banner exists in-band (good), but the staging console is dead: pre-rendered static snapshots, no live pipeline feel. Nothing moves; "what's happening" is unanswerable because nothing is happening. Aditya's verdict lands here. | 🔴 |
| 8 | Storm: fifty alerts fire. | The river shows fifty rows (or grouped rows). No storm-mode designed state, no flood banner with live counts and rule conditions. (R-UI-16 violated.) | 🔴 |
| 9 | Undo a suppression. | No undo surface. The machine's action is not reversible in one step next to the action. (N4 violated.) | 🔴 |
| 10 | Policy: what policy is live, is it attested? | No surface. R-1's governance theater finding is invisible in the UI. | 🔴 |

**Baseline: 10 journeys, 0 green.** The redesign must move every one.

## Phase 5 — Information architecture (feature-first, M5)

Designed feature-first: the page moment, the suppression-proof moment, and the kill-switch moment were drawn
before the navigation. Navigation follows.

**Primary nav (opinionated, D10 — no customizable dashboard escape hatch):**

| Route | Code | Job | Source |
|---|---|---|---|
| `/now` | NOW | J-paged, J-storm: what's happening right now | new — answers the missing journey #2 |
| `/pages` | PAGES | J-paged: open pages needing human action | R-UI-1..6 |
| `/proofs` | PROOFS | J-review: every suppression with its proof | R-UI-7..13, N3 |
| `/river` | RIVER | the decision tape (kept: SSE prepend, pinning, gap markers — convergent pattern, teardown §3.3) | existing spec |
| `/safety` | SAFETY | kill switch · policy · auth · race · degraded (section, five sub-screens) | C1–C8, 5 critical specs |
| `/audit` | AUDIT | the unified timeline incl. considered-and-rejected | R-UI-20..23 |
| `/lab` | LAB | simulator · calibration · shadow (section) | existing specs |
| `/keys` | KEYS | integrations / BYOK | existing |
| `/start` | START | onboarding (kept, re-pointed at the new IA) | existing spec |

**The NOW screen (the answer to "what's happening"):** order is the design (R-UI-2, Bourgon cost gradient —
state → scope → change → detail):
1. Mode strip: LIVE or SIMULATED — in-band, unmissable (N10, A6). In simulated mode: the generator seed and
   a "why this number" affordance on every count.
2. Pipeline strip: RECEIVER → CORRELATOR → RACE → GATE → FORWARDER — live counts and rates per stage,
   pipeline health + lag (R-UI-24). The design model made visible.
3. Kill-switch state chip: ambient, always visible (C1).
4. Open pages: problems, severity-ordered, each row in R-UI-2 order (title+severity+service+owner → primary
   action → evidence tier), with cost-of-inaction (R-UI-5) and pre-attached context (R-UI-6).
5. Active suppressions: count + top-3 reason codes + "view all in PROOFS" (R-UI-9 summary; never the full list
   — no unbounded lists, A1).
6. "What changed recently" strip: deploys/config/flag flips near open incidents (N5).

**SAFETY section (five sub-screens, first-class — never settings pages):**
- `/safety/kill` — KILL console per kill-switch-console.md: state word, propagation proof as headline claim,
  flip ledger, drill records, engagement contract. The thermostat correction lives here.
- `/safety/policy` — POLICY per policy-governance.md: live PolicyVersion card (version + content hash +
  attestation + expiry + kernel-binding heartbeat), change ledger, B3 lifecycle lane.
- `/safety/auth` — AUTH per auth-status.md: per-route auth scheme table, sender inventory (fingerprints, never
  keys), HMAC migration tracker, rejection log. An unauthenticated ingress route renders in critical register.
- `/safety/race` — RACE per race-monitor.md: timer-won rate, latencies vs budget, late-answer assertion
  (0 reached the kernel), drift-harness status with cost-cap usage.
- `/safety/degraded` — DEGRADED per degraded-mode.md: the fail-open ladder as named visible steps, the 3 AM
  levers (all page *more*, never suppress more), step history.

**PROOFS ledger:** every suppression as a card: the exact rule/policy, who configured it, matched condition
with values, evidence freshness, expiry, per-class track record ("this rule suppressed 412 like this; 3 needed
a human"), one-tap override/undo (N4), and the considered-and-rejected entries (R-UI-21). Recoverable and
browsable (R-UI-8). This is the surface no vendor has built (teardown §4.1) — the redesign's differentiator.

**Simulated mode (the honest simulation):** a seeded in-browser pipeline generator (`synth.js`) produces alerts
and runs them through real pipeline logic (grouping, dispositions with reason codes, proofs). Every number is
traceable to the generator (seed shown; "why this number" reveals the generating events). Labeled SIMULATED on
every surface. The pipeline FEELS alive because it IS running — synthetic inputs, real logic, honest labels.
Never fake-real: no synthetic data presented as live, ever (A6).

## Phase 6 — Strip pass (M6) + grayscale rule (M5)

Removed from the new UI vs the old: the START tour as the landing surface (moved to /start, linked once);
the raw decision tape as the home view (moved to /river); the KEYS screen from primary nav (kept, de-emphasized
— keys are setup, not operations); the shadow report from primary nav (moved under /lab — it is analysis, not
operations); any "total alerts" counter anywhere (A2 — counts are problems or they don't render).

Grayscale rule: hierarchy is carried by spacing, size, and weight. Color is reserved for state and state only:
page (red), suppressed (neutral/muted), degraded (amber), healthy (green), simulated (violet — the honest
color, distinct from live). If the grayscale screenshot doesn't communicate hierarchy, the screen is wrong (F2).

Dark-first (3 AM is dark), light theme available. System type stack only: monospace for data, sans for prose.
No webfonts — the console must load instantly on a bad hotel connection at 3 AM.

## Phase 7 — Opinionated decisions (M8; dissent welcome in review)

1. **Problems, not alerts, are the unit of the UI.** Alert counts never render. (N2, A2)
2. **The NOW screen is the home screen.** The river is a tool, not the home. (J-paged)
3. **Safety surfaces are first-class screens.** Kill switch, policy, auth, race, degraded — never settings.
4. **Suppression proof is the product's trust surface.** PROOFS is a primary nav item, not a filter on the river.
5. **Simulated mode is a live simulation, honestly labeled** — not a dead snapshot gallery.
6. **No customization escape hatches.** One opinionated layout; hierarchy disputes are resolved by the 30-second
   test, not by "let the user configure it." (F5)
7. **The undo is always next to the action.** Every machine action renders its reversal. (N4)

## What the research changed (M9 — decisions unblocked)

- **Teardown →** the PROOFS ledger exists because xMatters' suppression report is the closest any vendor gets
  and it's still post-hoc; BigPanda's invisible 95% is the named anti-pattern (R-UI-8). The pipeline-health
  strip exists because PagerDuty's 14-minute ingestion delay had no responder-surface (R-UI-24).
- **User research →** the NOW screen leads with problems (N1/N2) not volume — this reverses the old UI's
  river-first IA. Cost-of-inaction on every page (R-UI-5/N1). "What changed recently" strip (N5). Mitigation
  before root-cause ordering on the page view (N12).
- **Decision framework →** this document's structure IS the framework's prescription (phases 0–8 followed;
  falsifiers F1–F8 self-applied below).

## Self-applied falsifiers

- F1: every nav item above names its job statement in Phase 1. ✓
- F2: grayscale test will be run on the Beat 2 build (screenshot, color removed, eye-landing check). ⏳
- F3: friction log written before any new pixel. ✓
- F4: thermostat tests written per critical control. ✓
- F5: no customization answers anywhere in the decisions. ✓
- F6: the review artifact is the working prototype on staging, not this doc. (Beat 2) ⏳
- F7: research stopped — the next decisions (IA, flows) are unblocked; remaining research folds into Beat 2. ✓
- F8: to be measured — the friction log re-scored after Beat 2. ⏳

---

## SELF-JUDGMENT — Beat 1

**Principal skills honored:** principal-systems (five whys on the current UI's failures — each friction traced
to whose workflow the screen served; Chesterton's fence — kept the river's SSE prepend/pinning/gap markers
because they're the convergent industry pattern, not because they're ours; Type-1 vs Type-2 — the nav
restructure is Type-1, reversible styling is Type-2). principal-governance (decisions in writing before code;
the opinionated log). principal-mindset (the 30-second test as the bar; proxy-trap audit — screen count is
not the measure, friction-log delta is).

**Principal skills violated / weak:** the framework's Phase 4 (appetite-shaped beats) — I did not set an
explicit appetite for Beat 2; "complete redesign + working prototype" is unshaped scope and risks the
second-backlog failure the framework warns about. Mitigation: Beat 2 is time-boxed by the lane's nature and
I'll cut scope (fewer LAB screens rebuilt) before cutting the NOW/PAGES/PROOFS/SAFETY core.

**Past learnings:** honesty law — the friction log's method note admits it's code-derived, not walked (M3's
"walk the store" properly done would need the live browser, which I don't have as a subagent; flagged, not
hidden). No theater — every IA element traces to a research requirement or a named job; nothing is here
because it looks good. Verify-artifact — Beat 2's claims will be node --check + served-and-curled, not asserted.

**Did research change decisions?** Yes, three reversals: (1) river-first → NOW-first (user research N1/N2 vs
old IA); (2) suppression as river filter → PROOFS as primary nav (teardown's whitespace §4.1 — no vendor owns
this surface, so it must be first-class, not a filter); (3) dead simulated snapshots → live in-browser
simulation (Aditya's "feels dead" + A6 honesty requirement forced the synth.js design: synthetic inputs, real
logic, honest labels).

**Least-proud thing:** the friction log scores without walking the store — T7 violated in the same document
that cites T7. The honest mitigation is stated, but it's still the weakest link.

**What I'd do differently:** set the Beat 2 appetite in writing (scope: NOW, PAGES, PROOFS, RIVER, SAFETY,
AUDIT rebuilt; LAB/KEYS/START adapted from existing views) — actually, let me do that now: **Beat 2 appetite:
rebuild NOW, PAGES, PROOFS, SAFETY fully; adapt RIVER and AUDIT; carry LAB/KEYS/START forward with nav +
labeling changes only.** Anything beyond that is cut.

**Self-rating: 4/5** — the decisions are sourced and the reversals are real, but the unwalked friction log and
the initially unshaped Beat 2 scope are genuine process debts, both now named with mitigations.

---

## BEAT 2 — SELF-JUDGMENT (5 Oct 2026, ~23:40 IST)

**What was built:** `platform/ui/` fully rebuilt. New: `synth.js` (seeded honest synthetic pipeline: receiver→correlator→race→gate→forwarder, dispositions with reason codes + proofs, fail-open, time-bounded suppressions, undo, `why()` traceability), `store.js` (unified read layer: synth in static / real API+SSE in live), `tokens.css` (dark-first design tokens, color reserved for state), `index.html` (mode bar, pipeline strip, drawer, palette), `app.css` (full visual system), `app.js` (router, mode bar, pipeline strip, kill/health chips, drawer, palette, theme), `view-now.js`, `view-pages.js`, `view-proofs.js`, `view-safety.js` (kill/policy/auth/race/degraded), `view-river.js`, `view-audit.js`, `tests/synth.test.mjs`. Carried forward: `api.js`, `lib.js`, `components.js`, `freshness.js`, `chain.js`, `contract.js`, `payload.js`, `pins.js`, `shadow.js`, `views-sim.js`, `views-cal.js`, `views-shadow.js`, `views-settings.js`, `views-start.js`, `views-setup.js` (restored — builder contract).

**Principal skills — honored:** Professional lineage (L0–L10): PagerDuty's acknowledge/override model in PAGES, Grafana's explore-every-number in `why()` links, calm-tech dark-first. Critical surfaces (C1–C8): kill switch is a first-class SAFETY screen with engage/disengage + fail-open behavior verified in-DOM; policy governance shows attestation state; auth truth; race monitor (Jev vs timer wins); proof ledger; degraded mode. Anti-fatigue (A1–A12): pages-first (PAGES is the storm surface), every suppression shows its proof + undo, no naked alert counts (every number has a `why` link), no unbounded lists (problems capped, river paginated).

**Principal skills — violated:** The grayscale falsifier (F2) was done by construction (spacing/size/weight carry hierarchy) but never verified with an actual screenshot — no browser available. Named, not hidden.

**Past learnings:** Honesty law — the simulated mode bar says "SIMULATED SHOWCASE — snapshot — not live" in-band on every surface; the synth generator is seeded and every number traces to `synth.why()`. Verify-artifact — `node --check` on `.js` files was caught passing everything (theater); all syntax verification redone via `.mjs` copies; the jsdom boot-smoke (13 routes, zero errors, kill-switch end-to-end, live-mode setup routing) is the real evidence. No theater — the pipeline strip shows live counts that actually tick; the kill switch actually changes disposition behavior (verified: engage → all decisions page as `kill-switch`). Fail-before/pass-after — the antislop gates failed 4× (emoji dingbats, font token names, raw hex, tabular numerals) and were fixed to green; the builder contract (views-setup.js, STAGING_REQUIRED strings) was caught by reading the builder, not by failing CI.

**Did research change decisions?** Yes. (1) The user-research contradiction (lead with PROBLEMS not alert volume) is why NOW leads with problem cards and PAGES is the storm surface — the old UI led with counts. (2) The expert-decision framework's "write the mental model first" is why every screen answers one question (NOW: what's happening; PAGES: what needs me; PROOFS: why did it decide; SAFETY: can I stop it). (3) Aditya's "feels dead" verdict is why simulated mode runs a live synthetic pipeline in-browser instead of pre-rendered snapshots — honest but alive.

**Least-proud thing:** I deleted `views-setup.js` without checking the builder contract, and only caught it by reading `build-static.py` after the fact. That's exactly the "verify the artifact" failure mode — I got lucky the builder was readable. The process debt: I should have read the deployment contract before touching the file list.

**What I'd do differently:** Read `deploy/gh-pages/build-static.py` FIRST, before any file deletions. Also: the `.js` vs `.mjs` `node --check` trap cost a full debugging cycle — from now on, syntax-check via import or `.mjs` copy only.

**Self-rating: 4/5** — the redesign is complete, honest, and mechanically verified (118 tests, 13 routes, kill-switch E2E, builder contract). The point off is for the views-setup.js deletion (caught by luck, not process) and the unverified grayscale falsifier.
