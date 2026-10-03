# Interface R&D — Lane L1b (research half, independent from L1a)

**Date:** 2026-10-04
**Lane:** L1b — disagree-then-commit pair with L1a (worked independently, originality intentional)
**Status:** research deliverable; no code, no UI built
**Method:** real web research (browser.search + browser.open, all sources cited with URLs); mandatory skills applied — principal-systems, execution-doctrine, principal-governance, principal-mindset; design/INTERFACE_PRINCIPLES.md treated as law (contracts/budgets/states, not taste)

## Executive answer to Aditya's challenge

*"Users' use cases might be the same but the content data could be INFINITELY anything — you can't code fixed logic for that."*

Correct — so the research question is restated: **which logic must be fixed is not the data, it is the decision grammar.** Every real product in this space converged on the same three-layer architecture, independently:

1. **A fixed, typed decision envelope** (severity/disposition/confidence/freshness/policy) — owned by the platform, never by the customer payload. This is where Sentinel's paging decision, evidence companions (P2), and honesty language (§4) live.
2. **A total, safe renderer over arbitrary input** — a function that cannot crash, lie, or require foreknowledge of any key. It handles infinitely-anything by being *structurally* complete (atoms, tables, truncation, redaction), not by knowing the domain.
3. **A user-composable projection layer** — the enterprise shapes the console to its workflow by *projecting* its own fields into the fixed grammar (columns, views, filters, highlight rules), never by forking the product.

Nobody in the research codes fixed logic for infinite content. They fix the *shape of the answer* and make the *renderer* total. That is the whole answer, and everything below is the evidence.

---

## Per-source findings (all cited)

### 1. PagerDuty Events API v2 — the canonical fixed-vs-arbitrary boundary

PagerDuty's intake is the cleanest worked example of the boundary the task asks for. The trigger payload has a **fixed typed core** (`summary`, `severity` ∈ {info, warning, error, critical}, `source`, `component`, `group`, `class`) plus **`custom_details` — a free-form dict-or-string that PagerDuty renders as a table** without knowing its schema in advance (Airflow provider docs note: "If a dictionary is passed it will show up in PagerDuty as a table"). v2 also added links, images, and client info — typed attachments alongside the arbitrary blob. The console never promises to understand `custom_details`; it promises to *display it structurally*.

**Boundary drawn:** fixed = routing/decision vocabulary (severity, dedup key, source/component/group/class). Arbitrary = `custom_details`, rendered by a generic table renderer. The product never blurs the two — the incident dashboard sorts/filters on the typed fields only.

Sources: https://dev.to/zuplo/pagerduty-api-essentials-a-guide-19ha · https://airflow.apache.org/docs/apache-airflow-providers-pagerduty/stable/_api/airflow/providers/pagerduty/hooks/pagerduty_events/index.html

### 2. Grafana OnCall — Jinja templates as the composability layer

OnCall (archived OSS 2026-03-24, continued as Grafana Cloud IRM) solved "infinitely anything" with **user-authored Jinja2 templates per integration**: the input payload is raw arbitrary JSON; the templates project it onto a **fixed output grammar** — `Title`, `Message`, `Image URL`, `Grouping Id`, `Resolve/Acknowledge signals`, `Source link` — rendered per surface (web, Slack, SMS, phone, email). The template editor is three columns: *example alert payload → template → rendered result*, editable against a real recent payload ("Choose a Recent Alert group to see its latest alert payload"). Defaults ship per integration so it works out of the box.

**Takeaways:** (a) the projection layer is user-owned and *previewable against real data* — composability without forking; (b) behavioral templates (grouping/resolve) are separate from appearance templates — the same fixed-vs-flexible split, applied to logic as well as rendering; (c) Grafana's best-practice guidance is to append *Playbooks / Useful links / Checklists* to the alert message — i.e., the console's job is to render the customer's own runbook links, not to invent them.

Sources: https://grafana.com/docs/oncall/latest/jinja2-templating/ · https://github.com/grafana/oncall/blob/HEAD/docs/sources/configure/integrations/_index.md

### 3. BigPanda — normalization into a canonical tag taxonomy

BigPanda's core pipeline for 300+ monitoring integrations: **ingest → normalize → enrich → correlate**, where normalization translates disparate event formats into "one consistent taxonomy, represented using general-purpose key-value pairs called tags," performed in realtime with out-of-the-box and custom normalization methods. Crucially, their "Open Box ML" exposes the correlation logic *in plain English, editable, with preview-before-deploy* — the opposite of a black box. Incident 360 console filters by severity on the normalized form; business context (affected services, customer impact) is rendered from enrichment, not from raw payloads.

**Takeaways:** (a) normalization is a *declared mapping*, not magic — customers can see and edit it; (b) the "preview capability makes it easy to build and test new filter patterns based on alert metadata" — test-before-deploy is part of the composability contract; (c) enrichment (CMDB/topology/runbook links) is how they render "anything" — by attaching the customer's own context graph, rendered through fixed incident views.

Sources: https://www.aithority.com/machine-learning/bigpanda-launches-new-data-engine-capabilities/ · https://www.bigpanda.io/wp-content/uploads/2024/11/sb-event-management-bigpanda.pdf · https://appdevelopermagazine.com/unified-analytics-launches-from-bigpanda/

### 4. Moogsoft — Situation Room: fixed workflow stations, arbitrary content

Moogsoft's Situation Room is a fixed IA (Timeline / Topology / Collaboration / Integrations tabs) into which arbitrary alert content flows; incidents "automatically aggregate and deduplicate all the tags from alerts, including any custom tags created along the way." The workflow engine modules (Ingest → Enrich → Automate → Ticket → Collaborate) are fixed stations the customer configures, not code they fork. Their Alert Analyzer gives a visual UI to "visually configure, identify anomalies within, and fine tune the platform's alert processing."

**Takeaway:** even the most "AI" product here keeps a *fixed station-based IA* (cf. our §3 five surfaces) and puts variability into tags + workflows. Timeline-first ordering is the generic renderer for arbitrary alert sequences — order by time, mark activity; no domain knowledge required.

Sources: https://www.helpnetsecurity.com/2021/05/14/moogsoft-product-features/ · https://www.amasol.de/fileadmin/moogsoft-ebook-getting-acquainted-with-aiops-platform.pdf

### 5. Datadog — reserved attributes vs facets; declared vs observed

Datadog's log/incident query language hard-splits **reserved attributes** (`service`, `env`, `version`, `host`, `status` — no prefix, globally understood) from **facets/custom attributes** (`@`-prefixed, customer-defined). The unified service tagging convention (`service/env/version`) is a social contract that makes the fixed layer useful across teams. Their IDP work explicitly contrasts *declared* system state with *observed* state — "not only developer intention but also what is actually in production... without stale metadata" — i.e., the console must label which layer a fact came from.

**Takeaways:** (a) the two-namespace trick (reserved vs `@`-prefixed) is the lightest possible implementation of fixed-vs-flexible; (b) labeling the *provenance* of a value (declared vs observed) is a real product's answer to our reconstruction law (§4.4).

Sources: https://dev.to/carolemlago/a-natural-language-interface-for-datadog-log-search-4occ · https://www.computerweekly.com/blog/CW-Developer-Network/Platform-Engineering-Datadog-defines-components-of-Internal-Developer-Portal

### 6. Opsgenie — dedup with notes; heartbeats as the freshness signal

Two small, sharp mechanisms: (a) deduplication can be configured to *append notes* on each dedup hit — updates without new pages, "Silent Updates: Notes don't trigger notifications." This is exactly our suppressed-decision evidence trail in miniature. (b) **Heartbeat monitoring** — the absence of an expected signal is itself a typed, renderable state. Freshness is not inferred from timestamps alone; it is a first-class declared signal.

**Takeaway for §4:** the "degraded" treatment should distinguish *declared-dead* (heartbeat lost → render as dead with reason) from *merely old* (stale with as-of). Both render honestly, but they are different states with different operator actions.

Sources: https://community.atlassian.com/forums/Opsgenie-articles/Managing-Alert-Deduplication-and-Notes-in-Opsgenie/ba-p/2865144 · https://wac-cdn.atlassian.com/de/dam/jcr:42d114c0-ec52-4572-b60f-7d961eeaf4d8/Opsgenie%20Product%20Spec%20Sheet.pdf?cdnVersion=3140

### 7. Linear — opinionated density, keyboard-first, views as composition

From the Linear method reporting and community analysis: (a) **opinionated software with deliberate feature removals** — Linear wins by refusing configurability that would dilute the workflow (the failure mode: teams that need full flexibility spin up a second tool). (b) **Custom views**: saved filter + column + sort bundles, shareable; the composition unit is the *view*, not a plugin API. (c) **Keyboard-first with <50ms interaction** — speed is the density enabler; a dense screen that lags is clutter, a dense screen that responds instantly is a workbench. (d) "Calm density": one concept per view, consistent 4pt spacing, progressive disclosure done right (Linear's settings and Vercel's deploy panel as references). (e) Restraint as identity: "Software made by people who deploy software. The interface is so dense with affordances because the audience reads code, not marketing copy."

**Takeaway:** composability via *saved views* (filters/columns/sorts as data, shareable, versionable) beats composability via *custom code*. And density is a chosen number repeated everywhere — decide the row height up front, hold it.

Sources: https://www.usetranscribe.io/yt/4muxFVZ4XfM/transcript.pdf · https://www.morgen.so/blog-posts/linear-project-management · https://github.com/yunusemrejr/yunuspi/blob/HEAD/agent/skills/ui-ux-principles/SKILL.md · https://news.ycombinator.com/item?id=33199304

### 8. Bloomberg Terminal — concrete density techniques (not vibes)

From the Terminal's documented conventions: (a) **color is a closed semantic code**: yellow keys = market sectors, green = actions (GO), red = cancel/stop. Color never decorates; it *classifies* (cf. our §8.7 semantic-color-only). (b) **Mnemonic function codes** (`SRP AU Equity HP GO`) — the power user addresses the system by a typed command language, not by hunting menus; (c) **multi-panel layout** (PANEL key rotates windows) — density through simultaneous contexts, not through cramming; (d) Launchpad's **user-authored headline color-highlighting rules** (condition → text/background color on news headlines) — the reference implementation of safe, user-composable conditional rendering over a stream; (e) clone terminals adopt: tabular numerals, monospace, flat dark canvas, unknown/missing renders as "—" with an in-band caveat ("NIFTY TAPE OFF — BETA SHOWS GAPS") — a real product's reconstruction-law equivalent.

**Takeaway:** Bloomberg's composability is *rules over streams* (highlighting rules, custom tabs, saved functions), never *forks of the terminal*. Its density works because of extreme consistency: one row rhythm, tabular figures, semantic color, everything addressable by keyboard.

Sources: http://en.wikipedia.org/wiki/Bloomberg_Terminal · https://www.scribd.com/document/424015927/Bloombeg-Launchpad-English-Manual-YuErHa-pdf · https://github.com/keenpromax-art/bullion-board/blob/HEAD/AGENTS.md

### 9. Density, made concrete (synthesis of craft references)

Convergent techniques across the craft sources, all falsifiable in review:

- **Hierarchy through size/weight/space/position; color is the 4th lever** (breaks for color-blind users). The figure leads through size+weight+one accent; labels are demoted.
- **Tabular numerals everywhere numbers sit near numbers** (§5.3 already law — confirmed by every source).
- **Uneven spatial rhythm**: dense control zones give way to open content; group tightly-related things, then put real air between groups. Monotone layouts = no one deciding.
- **~60/30/10 surface distribution; one accent, used with intention.** Count saturated colors per view: more than two (excluding data-viz) = decoration.
- **Hierarchy through space and weight, not lines.** Whitespace and tonal shift before borders/dividers.
- **Clarity is orchestration, not subtraction** (Davis, Jan 2026): "over-minimalism often reduces clarity... Clarity comes from meaningful structure." And: "clarity scales with literacy" — design for the expert operator, with progressive disclosure for the rest, not a novice-default UI.
- **Density is a declared per-surface value** (comfortable/compact), never mixed within one visual region.

Sources: https://webdesignerdepot.com/density-vs-clarity-the-core-tension-in-modern-ui-design/ · https://github.com/mendestrading21/vertex-/blob/HEAD/.claude/design-skills/interface-design/SKILL.md · https://github.com/ajinkyabhanudas/youk/blob/HEAD/skills/ux-designer/references/craft.md

### 10. Failure-state design — what the best consoles actually do

- **Never render failure as valid-empty**: a GitHub issue on a real runner dashboard documents the exact bug — `RUNNERS 0 / 0` displayed when the API 504'd. Fix: track fetch-error state separately and render degraded/stale explicitly, never `0/0` as a healthy count. (This is §4.3 in the wild.)
- **Stale-while-revalidate as default**: keep last successful data on screen, show honest empty-live state before first sync, explicit transport status (`Live (WebSocket) / Live (SSE) / Polling / Degraded / Offline`).
- **Honest verdicts**: an "all clear" may render only when all feeds are fresh, known, and empty; a failed/stale feed reads as "reconnecting" / "status unavailable" — never a false green.
- **Partial failure is a design decision, not a monitoring decision** (hackernoon): decide per surface what happens when one input dies — "Can unaffected information continue to be served? How old can the last known information become before it should no longer be used?" Our §6 load-shedding order is this principle operationalized.
- **Degraded mode**: last-known-good snapshot stays visible, marked stale via banner + "last updated" timing, polling continues at controlled interval, manual reconnect without page remount.

Sources: https://github.com/d-sorganization/runner_dashboard/issues/765 · https://github.com/christopherstalker/distributed-job-platform · https://github.com/zoolok17/agenttalk/releases/tag/v0.76.0 · https://hackernoon.com/silent-data-failures-how-to-detect-when-real-time-applications-look-healthy-but-arent · https://github.com/zecurb/real-time-system-monitoring/blob/HEAD/docs/dashboard/incident-console.md

---

## The four research questions — answered

**Q1. Fixed vs flexible: where is the boundary?**
Fixed (platform-owned, typed, in the OpenAPI contract): severity, disposition, confidence (quantized), freshness state + as-of, policy version, dedup/grouping identity, source link. These are the *only* things any surface may sort, filter, or color by. Flexible (customer-owned, rendered generically): the entire alert payload — rendered by a total function with a fixed atomic grammar (§ below), pinned into the fixed layer only through user-declared projection rules. PagerDuty's typed-core + `custom_details`-table and Datadog's reserved-vs-`@` namespaces are the same boundary, drawn twice.

**Q2. Composability without forking?**
The convergent mechanism is **views/rules as data**: Linear's saved views (filter+columns+sort, shareable), Bloomberg Launchpad's headline highlighting rules (condition → color), Grafana OnCall's per-integration Jinja templates with live preview against real payloads, BigPanda's editable-in-plain-English correlation with preview-before-deploy. All are *declared in config, versioned, previewable, reversible* — never code, never forks. The console stays one product; enterprises differ by their projection rules.

**Q3. Density done right — concrete techniques.**
Closed semantic color code (≤2 saturated hues per view); tabular numerals; one row rhythm per surface decided up front; hierarchy via size/weight/space/position (color 4th); uneven spatial rhythm (tight groups, real air between); 60/30/10 surfaces; structure without lines; keyboard-first (<50ms); density declared per surface. Bloomberg works because every one of these is held constant across thousands of functions.

**Q4. Failure states.**
Distinguish real-empty from fetch-failed (never `0/0`); stale-while-revalidate with explicit transport state; "all clear" only when all feeds fresh+known+empty; heartbeat-declared-dead ≠ merely-stale (Opsgenie); degraded banner + last-updated timing + manual reconnect. All already law in §4/§6 — the research confirms the law is what the best products do.

---

## My two owned design directions

Both directions accept the three-layer answer above and §1–§11 as law. They differ in *where the operator's power lives*.

### Direction 1 — "Envelope & Inspector" (instrument-first)

**Thesis:** the console is two instruments that never mix: a *typed decision instrument* (the envelope) and a *generic payload inspector*. The operator trusts the first; the second never asks for trust.

- **Fixed typed contract:** `DecisionEnvelope { severity, disposition, confidence_q, freshness{state, as_of, budget}, policy_version, fallback_reason?, evidence_refs[], source_identity }`. Every surface renders this and only this for sorting, coloring, and the 30-second read. Generated from the platform OpenAPI (F8); the console cannot add fields.
- **Generic arbitrary-payload renderer:** a total, sandboxed recursive renderer with a fixed atomic grammar — `scalar | timestamp | link | flat-table | nested-collapse | binary/redacted`. Rules: depth-capped (e.g. 6), width-capped per cell, secret-shaped values (key matches *token/secret/key/password*) render as `▪▪▪ redacted` (never the value, never blank), unknown/missing renders as `—` with the reason in-band. It cannot throw on any JSON input — fuzz-tested in CI. It renders *structure*, never *meaning*: it will never label a field "root cause."
- **User-composable layer:** **field pins** — the operator pins any payload path (`payload.labels.region`, `payload.custom_details.az`) into a first-class column, filter operand, or river cell. Pins are workspace config (versioned, shareable, previewed against a live payload before saving — the OnCall three-column editor pattern). No pins → the river still works; pins are progressive enhancement, never required for the 3 AM test.
- **What I would NOT build:** alert templating in the console (templates belong to the integration, per OnCall); a plugin/extension framework (pins-as-data cover 95% of needs — Chesterton: the fork-mechanism is a maintenance cliff); custom dashboard builders (closed five-surface IA, §3); any renderer that infers semantics from key names (the "root-cause guessing" trap).

**Type:** 1 for the envelope schema and the atomic-grammar contract (public contract, expensive to reverse); 2 for pin UX details.

**Pre-mortem (top failure):** an operator pins a payload field that changes meaning across vendors (`payload.status` = "firing" in one, "ok" in another) and sorts a storm by it. *Mitigation:* pins carry the integration name in-band, cannot drive severity color, and the river warns when a pinned column's value distribution changes shape week-over-week.

### Direction 2 — "Headline Rules Console" (Bloomberg-descended)

**Thesis:** treat the decision river like a Bloomberg news tape: a fixed row grammar, dense and uniform, with **user-authored headline rules** providing all enterprise-specific shaping. Power lives in *declared conditional rules over the typed envelope + pinned fields*, evaluated client-side, versioned, and always visible as rules — never hidden logic.

- **Fixed typed contract:** same envelope as Direction 1 (the two directions agree on the contract — disagree-then-commit is about the operator model, not the platform contract). Row grammar is fixed: `[severity][disposition][confidence][freshness][one-line evidence][pinned cells…]`. One row height, tabular numerals, closed color code. No per-row customization of the grammar itself.
- **Generic arbitrary-payload renderer:** same total renderer as Direction 1 for the drill-down (S2 evidence panel), but the *river* never shows raw payload — the river shows only envelope + rule outputs. Raw payload lives one keypress away (S1→S2), not in the stream.
- **User-composable layer:** **headline rules** — `WHEN <condition over envelope + pins> THEN <highlight/tag/route-to-view>` (Bloomberg Launchpad's headline color rules, generalized). Rules are listed in a rules panel, each showing match counts over the last 24h (so a dead rule is visible, not silent). Plus **saved views** (Linear-style: filter + pinned columns + sort + density mode, shareable). Rules and views are config-as-data, exported/imported as JSON, diffable in PRs.
- **What I would NOT build:** per-row freeform widgets (the row grammar is closed — Bloomberg's discipline); server-side rule evaluation on read paths (P5 — rules evaluate against stored decisions client-side); rule actions that page or suppress (rules are *rendering* only — the engine owns dispositions, full stop); a visual rule-builder with drag-drop (text rules with live preview, OnCall-style, are more auditable and diffable).

**Type:** 1 for the closed row grammar and the rules-evaluate-client-side-only law; 2 for rule syntax.

**Pre-mortem (top failure):** rule sprawl — 200 headline rules, nobody knows which fire, the river becomes unreadable confetti. *Mitigation:* rules panel shows per-rule 24h match counts and last-modified; rules with zero matches for 30 days are auto-flagged for review (never auto-deleted — Chesterton); a "rules off" toggle is one keypress (the 3 AM escape hatch).

---

## Restatement test & principal notes

- **Restatement (not refinement):** the shift is from "how do we render infinite content?" to "the content was never the console's to understand — the console's job is a *total renderer + a fixed decision grammar + user-owned projections*." If we believe this, several planned features (payload-aware sorting, semantic field inference) die — correctly.
- **Proxy-trap audit:** "looks like Bloomberg" is a proxy; the true objective is P1's 30-second 3 AM test. Density that slows the five answers is clutter, whatever its pedigree.
- **Monkey-first:** the hardest part of both directions is the *total renderer* — a function that cannot lie, crash, or leak on arbitrary JSON. Kill condition: if a fuzzer can produce an input that renders a bare disposition without its P2 companions, the direction is dead.
- **What the research did NOT find:** no major product renders model-confidence UI the way Sentinel must (P2's five companions are our invention — the honesty language is the differentiator, not the density); no product in the survey solves the suppressed-decision audit trail well (Opsgenie's dedup-notes is the closest ancestor). Both are our moat — do not dilute them chasing parity features.
- **Disagreement with L1a (expected):** L1a and I worked independently; where we agree (likely: the typed envelope) that is convergent evidence, not coordination. Where we differ (operator model: instrument-vs-tape), synthesis picks by the 3 AM test, not by taste.

## Handoff notes

- No code written; full test suite untouched (baseline 496 — nothing to regress).
- This file is the deliverable; branch `lane/interface-rnd-b` tracks `origin/main`.
- Open question for synthesis: whether Direction 1's pins and Direction 2's headline rules are one mechanism (pins feed rules) — they compose cleanly, which is a point in favor of both.
