# UI v2 — research notes, adapter contract, falsifier self-judgment

**Lane:** UI build (parallel design pass) · **Date:** 2026-10-06
**Artifact:** `platform/ui-v2/index.html` — single file, zero dependencies, ~1,150 lines
**Branch:** `lane/ui-redesign-v2`

---

## 1. What I studied (my own research, not inherited)

**Mechanism research (from the repo's research lanes, verified by reading):**
- `SONNET-INSPIRATIONS-TEARDOWN.md` — the 12 goods / 12 bads. The verdict I internalized: round one rendered nouns, skipped verbs. My build is verbs-first: arrivals, reordering, storms, expiries, appeals all happen live.
- `REAL-SOFTWARE-MECHANISMS.md` — the 10 patterns / 5 anti-patterns. The ones that shaped concrete decisions:
  - **P5 fixed-position geometry** → keyed queue rows, in-place updates, hover-to-hold (P7). Position is the navigation.
  - **P6 append-only timelines** → the ledger never paginates-as-navigation; expiry re-evaluates visibly; appeal annotates.
  - **P8 trust = provenance + reversibility + human-final authority** → trace numbers, "considered, not done", one-step undo next to every action, appeal-as-override.
  - **P9 explicit numbers** → storm card shows rate/trend/threshold; queue footer states the risk-line cut explicitly.
  - **M2 storm collapse** → the storm card: one count, one trend, grouped-by provenance, one mitigation, "grouped is not silenced".
  - **A2 wall-shrunk-to-handset** → the phone layout is a single column with the queue first, not a squeezed grid.

**Visual research (my own, via web):** Linear's design-system writeups (restrained monochrome + single accent; hierarchy through spacing not color; "every element earns its place"; density without clutter; keyboard-first), plus the de-slop consensus docs (warm-neutral light base `#f7f7f5`, one interaction blue, semantic colors only for status, system fonts, tight hierarchy). What I took: **the discipline, not the palette** — color spent only where attention is spent; weight/size/position carry hierarchy; tabular numerals for every number.

## 2. The visual system — "the quiet instrument"

Derived, not prescribed. Three registers, nothing else:
1. **NEEDS YOU** — the only place saturated color lives (signal red `#BE2C1D`, edge bars, SEV1 rows larger/bolder/first).
2. **MACHINE-HANDLED** — recessed slate, aggregated counts, quiet bars.
3. **PROOF ON DEMAND** — dotted-underline trace affordances in interaction blue; click → in-context "why this number".

Base: warm paper `#F6F6F3`, surfaces white, ink `#1C1B17`, hairline rules. System sans; tabular numerals everywhere; mono only for IDs/rule codes. Sentence-case micro-labels (the old console's uppercase-everything was a tell). No gradients, no glass, no shadows except the drawer, 6–7px radii, 150–250ms motion, `prefers-reduced-motion` respected.

**Deliberate breaks from both predecessors:** not dark-terminal (Aditya: "humans hate such type of colors"), not File A's serif-paper, not File B's blue-gray tabs. No nav tabs at all for the core loop — one living surface + drawers.

## 3. What I stole (behaviors, with the reason)

| # | Behavior | Source | Why |
|---|----------|--------|-----|
| 1 | Trace numbers (dotted affordance → in-context popover) | G-1, both files | The skeptic's layout as behavior; honesty REQ-34 at the point of reading |
| 2 | Grouped silence, raw list never first | G-2, P1 | Scale solved as behavior; unit of silence is the group |
| 3 | "Considered, not done" on every decision | G-4, REQ-23 | Trust is built by showing the ruled-out scary alternative |
| 4 | Kill switch: two-step + plain-language contract + measured ms | G-5 | Fat-finger-proof at 3 AM; "measured, not asserted" |
| 5 | Degraded banner says what STOPPED | G-7 | Fail-open as a visible property, not a backend secret |
| 6 | Least-sure-first ledger default | G-8 | Triage by risk, not recency |
| 7 | Appeal annotates, never rewrites | G-9, B-3 | Audit integrity: the anti-pattern is named and avoided |
| 8 | Expiry as a first-class dimension | G-11, REQ-12 | Suppressions are temporary acts; expiry → visible re-evaluation |
| 9 | Storm as a compressed state | G-3, M2, REQ-18 | One count, one trend, one mitigation; "grouped is not silenced" |
| 10 | Evaluator harness separated from the operator's header | G-10, B-5 | Scenario/load controls behind Shift+E, dashed-border bar, never in the rail |

## 4. Dynamics — what actually moves

- Problems arrive on a seeded stream (default target ~40 open); the queue re-sorts by risk with keyed rows and in-place updates.
- The race is real inside the sim: judge latency lognormal-ish (p50 ~520ms, ~5% tail loses to the 3s timer); timer-wins page with the reason "judge too slow — the late answer is powerless".
- Storms trigger on arrival rate (≥10/min) and end when it subsides; the compression card shows live count, sparkline trend, grouped-by provenance.
- Suppressions expire (15–60 min) and visibly re-evaluate as pages — never silently dropped.
- Problems self-clear; flap-debounce folds refires; duplicates fold into open problems (fingerprint = service + incident).
- Kill switch flips all new dispositions to PAGE with measured-ms proof; judge-down scenario pauses suppression with the honest banner.
- Load crank (40/150/400) in the evaluator; queue renders the 80 riskiest + an explicit risk-line summary (aggregation, not pagination).

## 5. Adapter interface — contracts, not coupling

`DataAdapter` (documented in code, Part 2) is the seam. Today `local-sim` implements it with the seeded pipeline. The parallel tracks slot in without UI changes:

- **Reads:** `getPulse, getStateLine, getQueue, getQuietGroups, getProblem, getLedger, getSafety, getStorm, getJudgeDown` — every number the UI shows flows through these. Summaries are computed from the same rows as detail (P10).
- **Writes:** `ack, takeOwnership, appeal, engageKill, disengageKill` — operator intent; the adapter owns side effects.
- **Future `real-jev-sim`:** `judge(problem)` → POST `/api/v1/sim/judge` with the operator bearer token; returns `{score, latencyMs}` (real Jev judgment, cost-capped server-side). Spend meter: `GET /api/v1/sim/spend` → `{usedUsd, capUsd}`; the UI treats cap-hit as judge-down (timer-wins, fail-open) — never silently. PagerDuty stays FakePD in sim: the forwarder mock is unchanged.
- **Future auth track:** kill/policy endpoints take the per-install operator bearer token; the UI already isolates those calls in `engageKill/disengageKill`.

## 6. Falsifier self-judgment — brutal and specific

Scored against the falsifiers as stated in the handover + round-2 brief.

1. **3 AM test (10-second comprehension)** — PASS with a caveat. The state line ("3 need you · 41 handled quietly") answers the only question in one sentence; SEV1 rows are bigger, bolder, first. Caveat: the storm card is dark-on-light inverted — at 3 AM the inversion reads as "different mode", which is intended, but I could not test it on a real exhausted human.
2. **Scale test (hundreds of open problems, no infinite list)** — PASS. Load crank to 400 verified headless: queue renders 80 riskiest + explicit risk-line summary; ledger renders 100 least-sure + "N more below the risk line — use the jumps". No pagination-as-navigation anywhere. Not tested: 10,000 (the sim targets hundreds; the mechanism — aggregation + risk-line — is scale-free, but I did not run 10k rows).
3. **Honesty test** — PASS. SIMULATED band in-band on every surface; every header number traceable via popover; seed stated; confidence shown as ordinal score ± band with the caption "ranks decisions, not a probability"; no fabricated drills ("no flip recorded this session" until a real flip); unwired controls don't exist (every button works or is absent).
4. **AI-generic recognition** — PASS. No gradients, no glassmorphism, no purple/blue glow, no emoji iconography, no hero layout, no dark-mode-everything. The one risk: the storm card's dark inversion could read as "drama styling" — it is functional (mode change must be unmissable), and it carries information (count, trend, provenance), not vibe.
5. **Hierarchy without color** — PASS (by construction). Severity = position + size + weight + edge bar; color reinforces. In grayscale the SEV1 rows are still unmistakably first.
6. **No process theater** — PASS. No falsifier buttons, no Notes tab, no research claims in the UI. The evaluator harness is a tool, not a compliance display — and it's hidden by default.
7. **Dynamics, not coverage** — PASS. Nothing is hardcoded to 3 problems; the storm builds; the timer wins; suppressions expire. The sharpest proof: the smoke test drove 400 ticks and watched the queue reorder, the storm declare and end, and 46 suppressions expire into re-evaluated pages.
8. **Appeal/audit integrity** — PASS. Verified in code and at runtime: appeal sets `appealed` on the ledger row and reopens the problem; the row is never filtered out, never rewritten. "Frozen" section is genuinely immutable (decision-time snapshot object, never mutated; post-decision events live in the separate live log).
9. **The phone test (3 AM, on a phone, 60 seconds)** — WEAK PASS. Single-column layout, queue first, detail as full-screen drawer, tap targets ≥32px. But: I could not render it on a real phone (no live browser in this environment) — the 640px breakpoint is reasoned, not seen. This is the falsifier I'm least sure about.

**Two more I set for myself:**
10. **No silent disagreement (P10)** — PASS. State line, queue footer, quiet groups, and ledger all compute from `S.problems`/`S.ledger` — one store, no divergent counts (verified: counts reconcile in the smoke test).
11. **Every control live or absent (B-11)** — PASS. No dead buttons; the evaluator's scenario buttons all execute.

## 7. Requirements coverage (42/42)

- REQ-1..8 (arrival, problem-as-unit, ack vs own, actionable page, page anatomy order, cost of inaction, pre-attached context, machine's call): all in the queue + detail drawer. REQ-4 (actionable from the page channel): the detail drawer is the data-driven phone surface — ack/own/escalate act on any problem (B-6 fixed: not a prop).
- REQ-9..16 (suppression proof, first-class, unified, time-bounded, all channels, never silently un-created, one-step undo, grouped ≠ silenced): ledger drawer + expiry + re-evaluation + storm card's "grouped is not silenced". REQ-13 (all outbound channels): stated in the kill contract; the sim has one forwarder mock — noted as a sim boundary.
- REQ-17..19 (no unbounded lists, storm as state, problems-not-alerts + why): risk-line aggregation, storm card, trace popovers.
- REQ-20..23 (provenance, rule preview, veto-not-authoring, considered-rejected): rule IDs on every decision, "considered, not done", appeal-as-veto. REQ-21 (preview-before-approve): machine-proposed rules are not in this sim's scope — no fake preview UI shipped (B-11: absent, not faked).
- REQ-24..27 (unified timeline, what-the-machine-knew-then, export, no silent override): frozen vs live sections, clipboard export, re-opens as new attributed events.
- REQ-28..32 (kill switch, policy, auth, race, degraded): safety drawer, all first-class, all live.
- REQ-33..36 (simulated labeled, traceable, no fake-real, pipeline health + data age): sim band, trace popovers, seed, pulse + data age.
- REQ-37..42 (never-builds): no unbounded lists, no suppression without evidence+undo, no root-cause tooling leading, no flattened narrative (actor-attributed timeline), no gamification, no dead controls.

## 8. Known gaps (honest)

1. Phone breakpoint reasoned, not rendered (no live browser here).
2. Load tested to 400 open problems headless, not 10,000.
3. `performance.now()` resolution for the kill-switch ms is device-dependent; the harness measures honestly whatever the device gives.
4. The sim's gate bar (0.60 ± 0.09) is an internal sim constant, never displayed — per the corrected-constants rule there is no universal threshold in the UI.
5. REQ-13's "all outbound channels" and REQ-21's rule preview are sim boundaries: absent, not faked.
