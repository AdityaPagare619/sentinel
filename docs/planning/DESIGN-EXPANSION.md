# Session B4 — Design Expansion

**Session:** Sentinel all-chiefs planning meeting, Session B4 (design expansion)
**Facilitator/scribe:** subagent, persistent session `77440fb4` (parent: coordinator `aa4f40c6`)
**Date:** 2026-10-05 IST · **Branch:** `program/planning/design-expansion`
**Mandate (Aditya):** the design team studies EXISTING software/UIs, expands creative areas, ships user-focused interface work — the system exists to REDUCE manual load and the frustration of seeing millions of alerts; the UI itself must not repeat that frustration.
**Constraints in force:**
- `design/INTERFACE_PRINCIPLES.md` v1.1 — the constitution. §3 (five surfaces), §4 (honesty), §8 (anti-slop) are hard gates on everything proposed here.
- `docs/architecture-revision/prism-ui.md` — the domain memo's findings: the shipped UI is vanilla JS, zero-build, **284 KB total**; Q1's direction is to bless the vanilla stack with written rationale rather than migrate to React/TS. **Every creative direction below must be buildable in vanilla JS, zero-build, inside that budget.** Directions that require a framework are dead on arrival.
- Session B3's `docs/planning/USER-WORKFLOWS.md` (in parallel draft) owns the user workflows; this document owns the *interface patterns and creative directions*. Where workflow assumptions are needed, they are stated as `WF-ASSUME` and flagged for B3 reconciliation, not duplicated.

**How this session ran** (per principal-governance): independent chief judgments were
recorded before discussion (Prism = interface principal, Pager = on-call operations
principal, Tripwire = QA/red-team principal). Tripwire's standing attack line for
every proposal: *"does this replicate the millions-of-alerts firehose? where,
exactly?"* Directions are proposed, red-teamed, then committed or killed. Killed
ideas stay on the record in §4 — dead ideas on record beat zombie ideas in hallway.
Verdicts carry forced probabilities (principal-mindset §7). Nothing here is a mood
board; everything is a production engineering decision with rationale.

---

## 1. Pattern library — what the industry's UIs teach us

Each pattern: the source, what to steal, and the anti-pattern to avoid (what
frustrates). Sources are the actual shipped interfaces, described in words.

### P-LIB-1. PagerDuty: the incident table with alert accordion
- **Source:** PagerDuty's Incidents view — a table of incidents with status badges
  (triggered/acknowledged/resolved), urgency indicator, title, service, assignee,
  created time; each incident row expands inline (accordion) to show its alerts.
  Priority P1–P5 color-codes rows and re-sorts the dashboard. Escalation is a
  policy ladder: notify on-call → wait 5 min → notify next → notify manager.
- **Steal:** the two-level hierarchy (incident rows, alerts one deliberate click
  away) — this is progressive disclosure done right: the primary list is the
  *decision* (incident), the *evidence* (alerts) is one interaction deeper. Steal
  the status vocabulary too — triggered/acknowledged/resolved is a closed,
  auditable lifecycle that survives a postmortem.
- **Avoid:** the row *is* the firehose. Every incident is a row; volume is the
  primary signal; urgency color-coding mitigates but does not fix a 500-row list.
  PagerDuty's own AIOps upsell (alert grouping that "reduces incident noise by up
  to 98%" — PagerDuty AIOps quickstart, support.pagerduty.com, crawled 2026-10-02)
  is an admission that the base UI is a volume display. Also avoid the modal-heavy
  response-play flows — context destroyers on a triage surface.

### P-LIB-2. PagerDuty AIOps: zero-config triage context
- **Source:** the AIOps triage panel on an incident — "Outlier Incident" (is this
  frequent, rare, or anomalous?), "Past Incidents" (has this happened before, with
  metadata on who responded and what remediation was used), "Related Incidents"
  (other active incidents that may share a cause). Triage and RCA features
  "require zero configuration."
- **Steal:** *novelty judgment is the highest-value context an operator can get* —
  "have we seen this before, and did we fix it" collapses minutes of archaeology
  into one glance. Sentinel's shadow report and calibration surfaces should
  surface the same three questions for every decision: outlier? precedent?
  related? This is directly the §3 S2/S5 contract.
- **Avoid:** the model is opaque — the ML grouping works but the operator cannot
  interrogate *why* two alerts grouped. A published caution (dev.to alert-noise
  piece, crawled 2026-09) notes overly aggressive grouping masks distinct issues
  ("database connection lost" folded into "cache miss spike" delays the real
  diagnosis). Sentinel's grouping must always show its grouping key and let the
  operator split a cluster in one action — group is a hypothesis, not a verdict.

### P-LIB-3. Opsgenie: the alert lifecycle + one-screen on-call
- **Source:** Opsgenie's Alerts page — a dense alert list with priority, owner,
  and status; the alert detail carries the full activity timeline (every action
  tracked through the lifecycle). The Teams page unifies routing rules,
  escalation policies, and on-call schedules in one interface.
- **Steal:** the lifecycle activity log as *the* trust artifact — "who did what,
  when" rendered inline on the item, not buried in an audit tab. And the
  one-screen accountability view: who is on-call, what escalates next, and when.
- **Avoid:** alert-first framing everywhere. Opsgenie's fundamental unit is the
  alert, so the operator's attention scales with alert volume — the exact
  frustration Aditya named. Their fix is analytics *about* the alerts
  (MTTA/R dashboards) rather than fewer alerts in front of the operator. Sentinel
  must not ship "analytics about the firehose" and call it design expansion.

### P-LIB-4. Grafana OnCall: alert groups + the visible escalation ladder
- **Source:** OnCall's Alert Groups list — many alerts collapse into one group by
  a configurable Grouping ID template (`{{ payload.labels.alertname }}-{{
  payload.labels.instance }}`); opening a group shows its timeline (alerts,
  notifications, acknowledgments in sequence). Escalation chains are rendered as
  an explicit step ladder (notify → wait 5 → notify → wait 10 → webhook), and the
  routing-rule preview lets an author test a Jinja rule against a sample payload
  *before* going live.
- **Steal:** two things. (1) The grouping-ID template is the cleanest
  articulation of "cluster heads with declared keys" in the industry — Sentinel's
  storm-collapse should steal the *declared, inspectable grouping key*. (2) The
  routing-preview pattern ("test the rule against a sample before it touches
  production") generalizes: any Sentinel rule change (threshold, mute, grouping)
  should preview against recent decisions before committing — this is the
  threshold-simulator pattern applied to all rules, and it belongs in the UI.
- **Avoid:** config-as-interface. Routes, chains, schedules, Jinja templates —
  the operator needs a working knowledge of a DSL to change routing. Every
  "flexible" knob is a page in someone else's runbook. Sentinel keeps the power
  (rules, grouping) but renders them as *plain-language receipts*, not templates.

### P-LIB-5. Datadog: one case view + enriched context
- **Source:** Datadog Event Management — "one case view" that aggregates alerts
  and change events into a single case, enriches events with business context
  (CMDB data, normalized tags), correlates intelligently, and triggers automated
  triage workflows. The Incident Management product adds declaration workflows
  with structured intake (severity criteria, role assignments embedded).
- **Steal:** the *case* as the unit of work, not the alert; and enrichment at
  ingestion (attach service/team/severity/dependency context once, so every
  surface downstream inherits it). Sentinel's evidence drawer should read as a
  case file: decision, evidence, context, history, one page.
- **Avoid:** dashboard sprawl. Datadog's "single pane of glass" is, in practice,
  a pane per product — the attention is fragmented across a thousand integrations
  and out-of-box dashboards. A Sentinel console that grows a dashboard builder
  recreates this. The constitution's closed five-surface set (§3) is the defense;
  §4 kills the builder proposal explicitly.

### P-LIB-6. incident.io: Slack-native response + auto-timeline + newcomer summary
- **Source:** incident.io — an incident declared (e.g. `/incident`) auto-creates a
  dedicated Slack channel; *all* response work happens in the channel (role
  assignment, status updates, action tracking as buttons/messages); the timeline
  is captured automatically from conversation; when a new person joins, they get
  an auto-summary instead of having to ask "what's happening?". Their AI triage
  framing is explicit: "the layer that turns a bare page into an
  already-investigated incident: classified, deduplicated, routed, and backed by
  evidence *before a human even opens Slack*" (incident.io, "What is AI incident
  triage?", updated ~Sep 2026).
- **Steal:** the *newcomer summary* pattern — an auto-composed "what's happening
  and what do I do" brief, refreshed as the incident evolves. This is the
  concrete form of the constitution's §5.1 hierarchy law (severity → action →
  evidence): the first thing rendered is the verdict and the recommended action.
  Steal the auto-timeline too — Sentinel's audit chain (S4) should assemble
  itself; the operator never hand-writes incident narrative.
- **Avoid:** the dependency it creates. incident.io's core loop requires Slack;
  the incident is hostage to a third-party client. Sentinel's console must keep
  its core triage loop dependency-free — the alert-to-decision-to-action path
  works with zero integrations. (This is why the appeal-button finding in
  prism-ui.md A4.3 matters: a control that only works when something else is
  configured is phantom interactivity.)

### P-LIB-7. Linear: triage queue + keyboard-first + command palette
- **Source:** Linear — incoming work lands in a **Triage queue** with
  prioritization rules, owners, and SLAs rather than scattered DMs; the whole
  app is keyboard-first (single-key actions: `T` status, `A` assign, `L` label,
  `V` view); `Cmd+K` opens a universal command palette with natural-language
  commands ("assign to Alice", "filter: my issues, urgent, not done"); cycles
  roll over automatically.
- **Steal:** the triage queue as an *attention contract* — the queue says "these
  items need a human decision; everything else is handled," which is precisely
  Sentinel's suppression story: the river of *decisions needing review*, not the
  river of *everything that fired*. Steal keyboard-first operation for the triage
  loop (constitution §5.5 already requires it; Linear shows the bar) and the
  command palette (Sentinel already ships `Ctrl/⌘K` — extend its grammar to
  actions, not just filters).
- **Avoid:** Linear's model assumes a *finite backlog* — issues are worked to
  zero. Alert decisions are an *infinite stream*; "inbox zero" is unreachable and
  promising it is dishonest. The Sentinel triage queue must be framed as
  "everything important is handled," never "queue is empty."

### P-LIB-8. Superhuman / Gmail triage: auto-advance, split inboxes, undo
- **Source:** Superhuman — keyboard-everything email triage (`e` archive, `b`
  snooze, `u` return), **auto-advance** (after acting on a message, the next one
  is already in front of you), split inboxes (Focused/Other), and global undo
  (`z`). Gmail's inbox-zero workflow codifies the same: one decision per item,
  keyboard-first, undo as the safety net. A community seed document
  (kl3init/zero-mail SEED-004, GitHub) enumerates the durable primitives:
  bundles (bulk-actionable groups), snooze, delivery schedules (hold non-urgent
  until chosen times), and granular notifications (only alert for high-priority
  splits).
- **Steal:** auto-advance + undo as a pair. Auto-advance makes triage *fast*;
  undo makes fast triage *safe*. Sentinel's river: act on a decision (acknowledge,
  appeal, mute-with-TTL), the next decision is already selected; any action
  reverses cleanly. Steal "bundles" too — suppressed decisions are natural
  bundles: bulk-actionable by reason. Steal granular notification as a principle:
  only critical severity may demand center attention (§4 kills the notification
  center; this is its positive form).
- **Avoid:** the snooze model. Email snooze works because the item waits for
  *you*. A suppressed paging decision snoozed by an operator is a paging-path
  decision made in the console — that crosses the constitution's P5 read-path law
  unless the appeal path is a real, audit-logged write action (prism-ui.md P1:
  kill-or-wire the appeal control). Snooze-analogues in Sentinel must be explicit
  audited mutes with TTL, never a UI-only hide.

### P-LIB-9. Calm technology: center vs. periphery, the smoke-alarm test
- **Source:** Weiser & Brown, "The Coming Age of Calm Technology" (Xerox PARC,
  1995/1996); Amber Case's principles (2015): technology should require the
  smallest possible amount of attention; inform and create calm; make use of the
  periphery; amplify the best of technology and humanity; communicate without
  needing to speak; work even when it fails; minimum technology to solve the
  problem; respect social norms. The operative image: the **smoke alarm** — silent
  for years, then demanding total attention the moment it matters — and the
  inverse failure: apps that scream constantly until the alarm means nothing
  (hedgehoglab calm-technology summary; Adobe blog on Case's principles,
  2016-10-12; NewScientist, "The rebirth of calm," ~Jul 2026).
- **Steal:** the center/periphery split as an *architecture* for the console.
  The river is the center; everything else (suppression counts, system health,
  calibration drift) lives in the periphery — a quiet ambient strip that changes
  state without demanding attention. The smoke-alarm test becomes a UI gate:
  only critical severity may seize the center uninvited. Alert fatigue is
  precisely what happens when *everything* demands the center.
- **Avoid:** the failure mode Case names — calm technology conflicts with
  engagement metrics. A console that optimizes for "time in app" or "screens
  viewed" will drift toward interruption. Sentinel's success metric is the
  opposite: *time from page to correct action, trending down* — measure the
  operator's time saved, never the operator's attention captured.

### P-LIB-10. The anti-library: what the firehose looks like from inside
- **Source:** the aggregate of the above vendors' *base* (non-AIOps) UIs and the
  industry's alert-fatigue literature — raw event tables, badge-count notification
  centers, ever-growing dashboard grids, modal storms, and escalation ladders
  that page the same human five ways.
- **Steal:** nothing. This is the shape of the enemy, kept on the wall so the
  red team can point at it: any Sentinel proposal that renders *raw volume as the
  primary signal*, that interrupts for non-critical states, or that grows a new
  surface per concern is this, wearing our tokens. §3 of this document converts
  it into testable invariants.

---

## 2. Creative expansion areas — six committed directions

Each direction states: the frustration it kills, the pattern lineage (§1),
**exactly what the operator sees and does** (no mood, no adjectives), and why it
fits vanilla JS + zero-build + the 284 KB budget.

### D1. The Verdict River — decisions first, alerts never
- **Frustration killed:** "millions of alerts." The operator never sees a raw
  alert stream. The primary list is *paging decisions*: each row is a verdict
  (PAGED / SUPPRESSED) with severity, service, quantized confidence, as-of +
  freshness, and a one-line evidence summary. Alerts are evidence inside a
  decision's drawer — they never surface as rows.
- **Lineage:** P-LIB-1 (incident rows with alert accordion — the two-level
  hierarchy), P-LIB-6 (already-investigated incident before the human arrives),
  constitution §3 S1 and §5.1 (severity → action → evidence).
- **Interaction, concretely:** The landing surface renders rows like —
  `SUPPRESSED · api-gw 5xx spike · 14 alerts folded · conf 0.87 · as-of 03:12:04
  live · policy v14 · "matches known deploy-canary pattern"`. One click (or `x`)
  expands the drawer: the full evidence bundle (P2 companions), the folded alerts
  listed as a count with their shared fingerprint, "why suppressed" in
  operator language. Suppressed rows are first-class, always visible, never
  filterable-away-by-default — hiding them would recreate the distrust that
  kills automation adoption. A keyboard loop: `j/k` move, `x` expand,
  `a` appeal, `e` acknowledge, `u` undo, `?` cheat sheet.
- **Why vanilla fits:** the shipped UI already renders `decisionRow` and
  `drawerHtml` as string-built components; the change is data-shaping (decisions
  over alerts) plus keyboard bindings on the existing hash router. No new
  dependencies; the virtualization stays a scroll listener. Estimated delta:
  view-layer only.

### D2. Storm collapse — cluster heads with declared keys
- **Frustration killed:** the 3 AM storm — 400 rows in 10 minutes, one of which
  is the real outage, the rest its echo. Scrolling a storm is the firehose made
  personal.
- **Lineage:** P-LIB-2 (PagerDuty alert grouping — steal the UX, avoid opaque
  grouping), P-LIB-4 (Grafana's declared Grouping ID template), the
  constitution's own §11 pre-mortem #3 ("Alert fatigue via density" — storm mode
  is the named mitigation).
- **Interaction, concretely:** when the river's per-window decision count crosses
  the storm threshold (a stated, tunable budget in the surface contract), the
  river folds: rows sharing a grouping key collapse into a cluster head —
  `17 decisions · payment-db · connection-refused fingerprint · 1 CRITICAL
  representative shown`. The head always names the **grouping key** (visible,
  inspectable) and the count of distinct fingerprints inside. One click or `x`
  expands the cluster inline; a `split` action (one keypress) detaches a
  decision from the cluster when the operator disagrees with the grouping — the
  grouping is a hypothesis, the operator is the judge. Storm mode is a *designed
  state* of the river with its own 3 AM test (constitution pre-mortem), not an
  edge case: the badge reads `STORM — collapsed by fingerprint`, never silently.
- **Why vanilla fits:** folding is a render transform over the existing decision
  array; the grouping key is a string on the contract (R2 mock-shaped); the
  expand/split actions are existing drawer primitives. No layout engine needed.
- **Dispute, resolved on record:** Prism argued storm-collapse should be default-
  on; Pager argued default-folding risks hiding a distinct incident inside a
  cluster (the dev.to caution in P-LIB-2). Verdict (forced probability 75% that
  the compromise holds in field testing): collapse defaults on, but every
  cluster head shows the distinct-fingerprint count, expansion is one action,
  and the kill condition is explicit — **if operator split-rate on clusters
  exceeds 15% over a week, the grouping key is wrong and the direction goes back
  to design** (kill condition with a date: first storm-mode review 30 days after
  ship). Disagree-and-commit recorded.

### D3. The 30-second evidence sheet — verdict before evidence
- **Frustration killed:** the hunt. Today the operator opens a drawer and must
  assemble the story themselves from evidence fragments. At 3 AM, "what do I do"
  must not be a scavenger hunt.
- **Lineage:** P-LIB-6 (the newcomer auto-summary), constitution §5.1
  (severity → action → evidence, in that visual order), P-LIB-9 (the summary
  moves the operator's task to the periphery — they read, not assemble).
- **Interaction, concretely:** every decision drawer opens with the **verdict
  block** pinned at top: severity, disposition, quantized confidence with
  calibration context, recommended action ("acknowledge and watch deploy
  pipeline" / "no action — matches known benign pattern"), and "what happens if
  you do nothing" in one sentence. Below it, the evidence unfolds in fixed
  sections: what the model saw, the uncertainty, the freshness proofs, the
  policy version, the fallback reason (constitution P2 — the five companions,
  now with a fixed reading order). Nothing the operator needs is behind a tab.
  The recommended action is always adjacent to the severity — one glance, no
  scroll. Every recommended action is either a read (no confirm) or a destructive
  write (confirm with consequences stated, or undo — constitution §5.2).
- **Why vanilla fits:** this is a template reorder of the existing drawer plus
  the recommended-action line computed from the disposition enum + confidence
  band (a lookup, not a model call — P5 preserved: the UI never evaluates).

### D4. The quiet console — periphery-mode ambient strip
- **Frustration killed:** ambient anxiety. Today's console, like every monitoring
  UI, radiates urgency even when nothing is wrong — red everywhere, badges
  everywhere, the operator's attention taxed by the healthy state.
- **Lineage:** P-LIB-9 (calm technology — center vs. periphery, the smoke-alarm
  test), P-LIB-8 (granular notification: only the highest split may interrupt).
- **Interaction, concretely:** a single ambient strip, always visible, above the
  river: `● 2 critical open · 41 suppressed (last hour, all with proof) ·
  system LIVE · river as-of 4s`. When nothing is critical it renders in muted
  tones and does not move — no pulsing, no badges, no sound. It changes state
  only on genuine state change, and *only critical severity may promote the
  strip to the center* (expanded, high-contrast, unmissable). `prefers-reduced-
  motion` disables all of its transitions; with motion off the strip is still
  fully informative (constitution §5.4). The strip is also the degradation
  honesty channel: `system DEGRADED — showing cached decisions, last write
  03:14:22` renders in-band, in the same place the operator already looks.
- **Why vanilla fits:** one DOM node, updated by the existing SSE stream handler;
  state changes are class swaps on CSS tokens. This is the cheapest direction
  in the set and the highest calm-per-byte.

### D5. The triage keyboard loop — act, advance, undo
- **Frustration killed:** the mouse round-trip at 3 AM. Every frequent action
  currently costs a pointer journey: find the row, click, find the button,
  click, navigate back. In an incident, that latency is measured in mistakes.
- **Lineage:** P-LIB-7 (Linear's keyboard-first + command palette), P-LIB-8
  (Superhuman's auto-advance + global undo), constitution §5.5 (keyboard-first
  operation is law).
- **Interaction, concretely:** the river is a triage queue with a fixed keymap:
  `j/k` move, `Enter`/`x` open evidence, `a` acknowledge, `e` appeal (wired —
  see §4 K6 for the phantom-button kill; the key does not exist until the action
  is real), `m` mute-with-TTL (confirm dialog states the consequence: "mute
  payment-db alerts 30 min — 3 open decisions will stop paging"), `u` undo the
  last action, `?` the cheat sheet, `/` filter, `g s` jump to shadow report.
  **Auto-advance:** after any action, selection moves to the next undecided
  decision — the operator processes the queue without navigating back.
  **Undo is load-bearing:** every destructive action is reversible within its
  window (mute TTL cancellable, appeal retractable), so speed does not trade
  against safety. The command palette (`Ctrl/⌘K`, already shipped) gains action
  verbs, not just filters: "mute payment-db 30m", "appeal decision 1042".
- **Why vanilla fits:** a `keydown` handler on the river view + selection state
  in the existing app module; undo is a stack of inverse actions against the
  audit-logged API. No framework needed — this is the pattern vanilla does
  best, and it is why Linear-speed is achievable at 284 KB.

### D6. The suppression proof ledger — trust you can audit in one click
- **Frustration killed:** distrust of the machine. "Sentinel suppressed it" is
  exactly as reassuring as "trust me" — until the operator can see the proof
  instantly. Adoption of suppression lives or dies on this surface.
- **Lineage:** P-LIB-3 (Opsgenie's lifecycle activity log as the trust artifact),
  P-LIB-6 (auto-timeline), constitution P1 question 5 ("What did Sentinel
  suppress in the last hour, and can I see exactly why?" — the 30-second test).
- **Interaction, concretely:** a ledger view (a mode of the river, not a sixth
  surface — the constitution's surface set stays closed): every suppressed
  decision in the window, each row carrying its proof inline — confidence,
  policy version, the matching known-pattern name, and the one-line reason.
  One click expands the full evidence bundle. A "pattern" row aggregates:
  `deploy-canary pattern · 23 suppressed this week · 0 appealed · 0 regretted`
  — the track record of each suppression reason, computed from the audit log.
  This is the surface shown to the skeptic in the buying meeting and to the
  operator at 3 AM. Empty state is honest: "no suppressions in the last hour
  (window: 02:14–03:14)" — never an empty table.
- **Why vanilla fits:** a filtered, grouped render of the existing decision
  store; the pattern track-record is an aggregation the shadow lane can compute
  from the event log. View-layer work on the existing components.

---

## 3. Anti-frustration invariants — hard rules, testable gates

These are proposed as additions to the constitution's §9.2 honesty invariants
(RFC to amend INTERFACE_PRINCIPLES.md — Type 1 for the list, Type 2 for each
threshold). Each states the rule, the user outcome it protects, and the test.

- **INV-U1 — No unbounded volume lists.** *Rule:* any decision list that exceeds
  the storm threshold in its time window renders as cluster heads (D2), never as
  raw rows. *Protects:* the operator from scrolling the firehose. *Test:* feed
  the river N > threshold decisions in T minutes; assert no more than K rows
  render uncollapsed (K = cluster budget, stated in the surface contract).
- **INV-U2 — Proof in one click.** *Rule:* every suppressed decision's evidence
  bundle is reachable in ≤ 1 interaction from any surface that names the
  decision. *Protects:* trust in automation (D6). *Test:* static scan — every
  render of a suppressed disposition carries a deep link to its evidence; click-
  depth asserted in the Playwright critical flow.
- **INV-U3 — Volume is never the primary signal.** *Rule:* the river's default
  ordering is decision severity, then recency; arrival order is never the top-
  level sort, and no surface renders a raw count ("1,204 alerts") as its
  headline figure. *Protects:* the millions-of-alerts framing from re-entering
  through a KPI widget. *Test:* snapshot assertion on the river's sort
  comparator; antislop-style scan for unqualified alert-count headlines.
- **INV-U4 — Actions are reversible or explicitly consequential.** *Rule:* every
  operator action from the console is undoable within its window, or it confirms
  with the consequences stated in operator language; a control that cannot act
  does not render (the prism-ui.md A4.3 appeal-button law, generalized).
  *Protects:* 3 AM mistakes; phantom-interactivity betrayal. *Test:* extend
  `antislop.py`'s phantom scan to rendered-but-inert controls; every destructive
  action has an inverse-action test.
- **INV-U5 — The center is earned, never assumed.** *Rule:* only critical
  severity may seize center attention uninvited (modal-equivalent, sound,
  badge). All other state changes stay in the periphery (D4 strip). No badge
  counts, no notification center, no auto-playing motion. *Protects:* ambient
  anxiety; the smoke-alarm test (P-LIB-9). *Test:* a "center-seizure audit" —
  enumerate every UI element that can demand attention; assert each is gated on
  critical severity or explicit operator request.
- **INV-U6 — Replay never presents as live.** *Rule:* mock replay, recorded
  replays, and simulated content render as `cached`/`simulated` with in-band
  labels at the point of display — never as `live` (generalizes prism-ui.md
  P3's two mock-honesty findings). *Protects:* the operator and the buyer from
  mistaking demonstration for reality. *Test:* the two new `honesty.test.mjs`
  invariants proposed in prism-ui.md P3.
- **INV-U7 — Freshness is always visible.** *Rule:* already constitution §9.2
  INV-1; restated here because it is the anti-frustration twin — a silent stale
  river is how the operator learns to distrust the console. *Protects:* the
  honesty contract under load. *Test:* existing INV-1 suite.
- **INV-U8 — The triage loop is keyboard-complete.** *Rule:* navigate, open,
  act (acknowledge/appeal/mute), advance, and undo are all keyboard-operable
  with visible focus; a frequent action that requires the mouse is a defect.
  *Protects:* the 3 AM operator's hands. *Test:* the Playwright critical flow
  "keyboard-only acknowledge path" (constitution §9.1 #2), extended to cover
  the full loop.

---

## 4. What the red team killed — dead ideas on record

Each kill states the proposal, who killed it, the mechanism of the kill, and
what (if anything) survives as a fragment. A killed lane that would have worked
is not an error (resulting ban, principal-mindset §8) — these kills were
well-founded at decision time, on the record.

### K1. AI copilot chat panel — KILLED (Tripwire, Prism concurring, 90%)
*Proposal:* a "Ask Sentinel" chat sidebar on the evidence surface — natural-
language questions about the incident, answered from the evidence bundle.
*Kill mechanism:* three independent violations. (a) It re-creates the problem
the engine solved: an LLM answering at 3 AM is the firehose with a typeface —
unbounded, uncalibrated prose where the constitution demands quantized,
auditable verdicts. (b) It violates P5 by construction: a chat that "looks
things up" will, under pressure, trigger evaluation to fill a gap. (c) It is a
latency and attention tax on the critical path with no measured benefit —
execution-doctrine: validate the pain manually first; nobody has shown an
operator asking a question the evidence sheet (D3) doesn't already answer.
*Survives as:* nothing. If a future wave demonstrates a measured 3 AM question
the sheet can't answer, the proposal may return with a measurement plan — as a
new RFC, not a resurrection.

### K2. Infinite-scroll live ticker — KILLED (Tripwire, unanimous, 95%)
*Proposal:* a Twitter-style live ticker of alerts with infinite scroll and a
"live" pulse, as the river's ambient mode.
*Kill mechanism:* this is the millions-of-alerts frustration with better CSS.
Prism conceded it "feels alive"; Pager named the mechanism precisely — a ticker
optimizes for *the operator watching the system* instead of *the system
protecting the operator's attention*. It fails INV-U1, INV-U3, and INV-U5
simultaneously. The SSE stream already exists for data; the display of it as a
ticker is the failure. *Survives as:* the D4 ambient strip — the one-line
quiet summary is the ticker's honest form.

### K3. On-call response-time leaderboard — KILLED (Pager, Tripwire concurring, 85%)
*Proposal:* a gamified scoreboard ranking responders by acknowledge/resolve
times, "to drive accountability."
*Kill mechanism:* incentive realignment, product/design constitution — if the
tool fights how people are rewarded, they sabotage it. A leaderboard measures
the operator instead of reducing their load; it converts triage into
performance theater and punishes the careful responder who takes 40 seconds to
read the evidence. Opsgenie's analytics (P-LIB-3) measure the *system's*
responsiveness, never the human's rank. Aditya's bar is the operator's
frustration going down, not the operator being watched. *Survives as:* nothing
in the console; aggregate system-level responsiveness may live in the shadow
report (S5) as a system metric, never a personal rank.

### K4. Custom dashboard builder — KILLED (Prism, Pager concurring, 80%)
*Proposal:* a drag-and-drop widget builder so teams can compose their own
monitoring views.
*Kill mechanism:* Datadog-sprawl in miniature (P-LIB-5 anti-pattern). The
constitution closes the surface set at five for a reason — every new surface
is a maintenance liability, and a builder outsources information architecture
to every team, guaranteeing fragmentation. The builder also has no answer to
the honesty invariants — who labels the derived values on a user-built widget?
*Survives as:* Field Pins (already shipped — display-only bindings of payload
paths into river columns) remain the sanctioned customization mechanism:
bounded, display-only, contract-checked.

### K5. Notification center with badges — KILLED (Tripwire, unanimous, 92%)
*Proposal:* a bell icon with unread badge counts and a notification drawer.
*Kill mechanism:* badge counts are the canonical anti-calm pattern (P-LIB-9) —
they manufacture ambient anxiety and train the operator to clear badges
instead of resolving incidents. The console's notification model is
pull-based triage (D1/D5) plus the periphery strip (D4); a push-based center
with counts is the firehose wearing a bell. *Survives as:* the D4 strip's
critical-only promotion — the one interrupt the system is allowed.

### K6. Inline auto-remediation buttons — KILLED as proposed (Pager, Prism concurring, 70%)
*Proposal:* "Restart service" / "Fail over" buttons inline on river rows, for
one-click remediation.
*Kill mechanism:* a Type-1 action (irreversible, production-mutating) launched
from a Type-2 triage list with no mitigation context — the operator's click
carries the blast radius of the whole service with the evidence of one row.
It also crosses the console's read-path law (P5): remediation is a write path
needing its own audited, confirmed, platform-side control plane — not a button
in a list. The related wound is real (prism-ui.md A4.3: the phantom "page me
anyway" button) — the lesson generalizes: *a safety control that cannot act, or
that acts without the full control plane behind it, is the most dangerous UI
element in a paging product.*
*Survives as:* a gated future — remediation actions may return only behind an
RFC that specifies the platform control plane, the confirmation contract, the
audit events, and the undo semantics. The appeal control specifically: wire it
to a real appeal endpoint (POST → audit-logged override → real paging path)
or remove it; the "demo build" note dies in the prod build either way
(prism-ui.md P1).

---

## 5. Session record — verdicts, probabilities, open items

**Committed directions:** D1–D6, each with the interaction specified in §2 and
the vanilla/zero-build/284 KB fit argued. Forced-probability assessment of the
set: **80%** that D1–D6 ship inside the 12-hour plan's interface allocation
without framework migration; the risk is D2's grouping-key design (needs the
platform contract to carry the key — an R-item for the platform lane, flagged
the same hour per the parallel-teams rule).

**Disagree-and-commit items:** the D2 default-collapse compromise (Prism
disagreed on default-on, committed to the 15%-split-rate kill condition);
K6's 70% kill (Pager's dissent: a fully-wired remediation plane is the real
product moat — recorded as a future RFC, not a present direction).

**Coordination with Session B3 (USER-WORKFLOWS.md):** this document assumes —
`WF-ASSUME-1`: the primary operator workflow is river → evidence → act
(acknowledge/appeal/mute) → audit, matching constitution §3's navigation;
`WF-ASSUME-2`: the buyer workflow needs the suppression ledger (D6) and shadow
report as the trust surfaces. If B3's workflows contradict these, B3's
workflows win and §2 is amended — flagged for reconciliation, not duplicated.

**RFCs this session spawns (not decisions — filings):**
1. RFC: amend `INTERFACE_PRINCIPLES.md` §9.2 to adopt INV-U1–U8 (Type 1 list).
2. RFC: storm-mode grouping key — contract field + platform supply (R-item).
3. RFC (from K6): remediation control plane — platform-side, future.
4. Note (Type 2): D4 strip budgets (what counts as "critical may promote") —
   decide fast, measure, roll back without shame.

**Kill conditions with dates (principal-mindset §6):** D2 split-rate review 30
days after storm-mode ships (>15% weekly split rate ⇒ grouping key wrong ⇒
back to design). D5: if the keyboard loop's Playwright flow is not green
before the interface lane's first demo, the keymap ships disabled rather than
half-working (phantom interactivity law, INV-U4). Anti-theater check: any
INV-U rule that has never fired in CI after one quarter is audited for
toothlessness, not celebrated.

**What would change these verdicts:** a timed 3 AM test (constitution §9.4)
showing a killed direction outperforming a committed one on time-to-correct-
action — evidence beats the red team, always. The test does not exist yet
(prism-ui.md A1 notes no timed result file in the repo); running it is R15/R22,
and this session's directions are designed to be measured by it.
