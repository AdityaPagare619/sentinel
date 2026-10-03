# Interface R&D — L1a (2026-10-04)

**Lane:** L1a — interface research half of a disagree-then-commit pair (independent of L1b; not coordinated).
**Law:** `design/INTERFACE_PRINCIPLES.md` (v1.1) is the contract this research serves — especially P0–P5, §3
(five surfaces), §4 (honesty UI), §8 (anti-slop), §11 (inbound requirements R1–R25).
**Skills applied:** principal-systems (Type 1/2 labels, global-over-local, pre-mortem), principal-governance
(design never waits for backend; contract-first), execution-doctrine (pain first; monkey-first + kill
conditions), principal-mindset (proxy traps; restatement over refinement).

**Method:** real reading of primary sources below (official docs, vendor engineering writing, one
production-grade interaction teardown). Snippets were not skimmed — each source was opened and read.

---

## 1. Per-source findings (with citations)

### 1.1 PagerDuty — fixed envelope + free-form details table

PagerDuty's Events API v2 defines a **fixed typed payload** (`summary`, `severity`, `source`, `component`,
`group`, `class`, `dedup_key`) plus one free-form field: `custom_details`. The documented rendering rule is
explicit: *"Free-form details from the event. Can be a dictionary or a string. **If a dictionary is passed it
will show up in PagerDuty as a table.**"*
— [PagerDuty events hook docs (airflow provider API ref)](https://airflow.apache.org/docs/apache-airflow-providers-pagerduty/stable/_api/airflow/providers/pagerduty/hooks/pagerduty_events/index.html)

That is the whole infinite-data strategy of the market leader: **the list/incident chrome is always typed and
fixed; the vendor's arbitrary JSON is rendered by one deterministic generic renderer (key→value table).** No
per-integration rendering code is ever written; the product never hardcodes a vendor's schema.

The same philosophy shows up in how PagerDuty lets customers shape views over identical features: **Slack
Card Builder** templates per channel type — admins show/hide default system fields (status, priority,
assignee) and drop in Custom Fields configured per incident type, and "the template applies to every incident
card posted in that channel type going forward — no per-incident setup."
— [PagerDuty blog: Automate Incident Management with PagerDuty Slack](https://www.pagerduty.com/blog/chatops/automate-incident-management-with-pagerduty-slack/)
Pattern: **fixed vocabulary of fields (some typed, some customer-defined), assembled into templates by the
admin — the template is data, not code.**

### 1.2 Opsgenie — saved-search queries as the composable unit; activity log as provenance

Opsgenie's Alerts page: a query bar with a **search language** (`status:open and acknowledged:false`), custom
**saved searches** (name + query + owner + team sharing) that appear in the left sidebar, click-on-any-tag /
team / owner to apply that filter, and **bulk actions** (select N → ack/close; "Ack all" / "Close all" via
sidebar). Alert filtering is async ("available to list page at most within 2 seconds" — a freshness budget
stated in the docs).
— [Opsgenie docs: Navigate the alerts list](http://support.atlassian.com/opsgenie/docs/navigate-the-alerts-list/)

Two details matter for our questions:
- **Density is user-chosen, not designed once:** the incidents list ships **comfort / cozy / lite** views
  (density modes), selectable from the list header settings.
  — [Opsgenie docs: Navigate the incidents list](http://support.atlassian.com/opsgenie/docs/navigate-the-incidents-list/)
- **Provenance is a first-class tab:** the alert details window has an **Activity Log** tab recording "which
  user or integration created the alert, the notifications that were sent, updates to the alert, **which
  policies were run in the creation of the alert**, and possible errors encountered in the alert life cycle,"
  plus a **Responder states** timeline (Pending → Sent → Received → Seen → Action). This is decision
  provenance rendered as an event ledger — the exact pattern our S2/S4 surfaces need. The alert itself is a
  fixed-typed record (`message`, `alias`, `description`, `responders`, `actions`, `tags`, `entity`, `source`,
  `priority`) with one arbitrary slot: `details`, a **map of key-value pairs** ("Map of key-value pairs to use
  as custom properties of the alert").
  — [Opsgenie docs: Navigate the alerts list](http://support.atlassian.com/opsgenie/docs/navigate-the-alerts-list/);
  [opsgenie-python-sdk CreateAlertPayload](https://github.com/opsgenie/opsgenie-python-sdk/blob/HEAD/docs/CreateAlertPayload.md)

### 1.3 Grafana OnCall — the operator writes the rendering function (Jinja2 templates)

Grafana OnCall's answer to infinite data is the most explicit in the industry: **"Integration templates" are
Jinja2 templates applied to each alert to define its rendering and behaviour.** Two distinct template
families:
- **Appearance templates** — `Title`, `Message`, `Image URL` per surface: Web, Slack, MS Teams, Telegram,
  SMS, Phone, Email, push. Infinite payload → finite rendered projection, per surface, declared by the
  operator.
- **Behavioral templates** — `Grouping Id` (alerts with the same rendered id are grouped), `Acknowledge
  Condition`, `Resolve Condition`, `Source Link`, `Routing Template` (route to escalation chains on
  `{{ payload.labels.severity == "critical" }}`-style conditions).

The template editor is a three-column live instrument: **example alert payload | template | rendered
result**, with "Use custom payload" to test against arbitrary JSON and a cheatsheet. Rendering is thus
**data authored against real payloads with instant feedback**, never code shipped by the vendor.
— [Grafana OnCall: Jinja2 templating](https://grafana.com/docs/oncall/v1.2.x/jinja2-templating/);
[Advanced template configuration](https://github.com/grafana/oncall/blob/HEAD/docs/sources/configure/jinja2-templating/advanced-templates/index.md)

**Lesson for Sentinel:** the industry's most successful infinite-data product delegates rendering to
*declarative per-integration configuration with live preview*, and strictly separates **appearance**
(rendering) from **behavior** (grouping/routing). The separation is what keeps it maintainable.

### 1.4 BigPanda — provenance as UI: matched correlation patterns + change-suspect reasoning

BigPanda's Incident Console is the richest decision-provenance UI I found:
- **Matched Correlation Patterns** (lightning-bolt icon in the details pane): "click the lightning bolt icon
  to see a list of matched correlation patterns for the incident" — the operator sees *which rules* assembled
  these alerts into this incident. This is literally "why did this happen" as a clickable UI element.
- **Change suspects with stated reasoning:** the engine marks changes as **Suspect** (never "Match" — "to give
  users the final say"), adds a **comment to the Change Details panel explaining why the change was marked**,
  and shows a **change suspect score**. Uncertainty is quantified and the human keeps the verdict.
- **Titles from configured properties:** the incident title is built from the customer-configured **primary
  property**, the subtitle from the **secondary property** — a declarative projection of unbounded alert data
  into a fixed readable slot (the same idea as OnCall's appearance templates, config-side rather than
  template-side).
- **Configurable alert columns:** the Alerts tab renders "data for each related alert in a table. The column
  headers show the tag names and the rows show the tag values… The details that appear can be configured by
  your BigPanda administrator" (**Manage Alert Views**). Columns are added/removed dynamically as space
  allows — **the table schema is operator configuration, not vendor code**.
- **Environments pane** groups/filters incidents by properties (source, priority) — saved, named filter
  contexts that also drive dashboards and sharing rules.
- **Audit Logs** for correlation configuration changes — provenance of the provenance rules.
- Bulk triage primitives: **Select All + bulk actions**, **Split/Merge** alerts directly from the table.
— [BigPanda docs: The Incident Console](https://docs.bigpanda.io/en/the-incident-console);
[Remediate Incidents (change suspects)](https://docs.bigpanda.io/en/remediate-incidents)

### 1.5 Moogsoft — open-box ML: radar charts of *why* the machine grouped these alerts

Moogsoft's Situation Room ships **Visualize**, "providing an 'open-box' machine learning experience":
"Similarity clusters are presented as **radar charts for each Situation**… a window into how the system's
automated decision making works. Users can understand at a glance the **matching criteria for those events
that have been correlated together**." Operators can also fine-tune the correlation configuration from the
same surface.
— [Help Net Security: Moogsoft AIOps 7.2](https://www.helpnetsecurity.com/2019/05/30/moogsoft-aiops-7-2/)

Combined with **Probable Root Cause** (most-likely-causal alerts, supervised + unsupervised) and a
**Timeline** view ("breakdown of the Situation's associated alerts in the order they occurred alongside key
activity markers"), the Situation Room is the clearest precedent for our S2 (Calibration & evidence): **show
the match dimensions, not just the match.**
— [Moogsoft Enterprise UI Reference](https://docs.moogsoft.com/Enterprise.8.0.0/en/moogsoft-enterprise-ui-reference.html)

### 1.6 Datadog — one search syntax everywhere; saved views; facets

Datadog's incident list "uses the same event-based **search syntax as Logs and Event Management**"
(`severity:(SEV-1 OR SEV-2) state:active`, `services:checkout AND -state:resolved`), with a left **facet
panel** (Status, Severity, TTR, other configured properties), **Export**, and **Save views** for frequently
used queries. Admins "configure additional fields that appear for all incidents in Incident Settings."
— [Datadog docs: Incident Management](https://docs.datadoghq.com/service_management/incident_management/)

Pattern: **one query language shared across the whole platform** (incidents, logs, events), so a skill learned
in one surface transfers to all others; facets are configuration, not code.

### 1.7 Linear — views are saved filters; keyboard is a first-class citizen; opinionated beats configurable

From Linear's official docs:
- **Custom Views = saved, shareable, typed filters** over issues/projects/initiatives (`IssueFilter` JSON):
  personal or shared (workspace/team/project scope), favorited/pinned to the sidebar, **subscribable**
  (notify me / post to Slack when an item *enters* a view), ownable (named owner), URL-shareable (links can
  carry temporary filters without granting access). Any filtered board/list is savable with `Option+V`.
  **Triage** is a team inbox for un-admitted work, **excluded from ordinary views by default** — the exact
  model for Sentinel's suppressions (work that exists but is deliberately kept out of the paging surface).
  — [Linear docs: Custom Views](https://linear.app/docs/custom-views)
- Speed discipline (Karri Saarinen's operating principles, third-party synthesis of his writing/talks):
  **"Opinionated beats configurable.** Linear ships one good workflow instead of a settings page of
  workflows… Every preference you add is a decision you refused to make." **"Speed is a design feature, and
  the spec is measurable.** Interactions should complete in under 100ms perceived" via optimistic UI,
  local-first data, preloading, skeleton-free rendering. **"Keyboard is a first-class citizen, not an
  accelerator layer.** Every action reachable by keyboard; the command menu (Cmd+K) as the universal verb
  surface; single-key shortcuts for the daily loop. Power users are made, not found: the UI teaches
  shortcuts contextually."
  — [Karri Saarinen principles (community synthesis)](https://github.com/samuraizac/web-ui-mastery/blob/HEAD/skills/web-ui-mastery/references/designers/karri-saarinen.md)

### 1.8 Bloomberg Terminal — density as doctrine; mnemonic command vocabulary

- Screens are "dense with text, much of it in tabular form… The default is still amber characters on a black
  background" — "investment pros… like to gorge on data." Density is the product, not a flaw.
  — [Fast Company: How the Bloomberg Terminal Made History](https://www.fastcompany.com/3051883/the-bloomberg-terminal)
- The keyboard was **designed for traders with no prior computer experience**: function keys renamed to
  memorable, color-coded names (F10 → yellow `Index`, Esc → red `Cancel`, Enter → green `GO`); commands are
  **mnemonic codes in curly brackets** (`{VOD LN Equity GO}`); a dedicated **Menu** key returns to the
  previous function; **History** repopulates the command line. Interaction model: *type a stable mnemonic,
  hit GO* — muscle memory over discoverability, zero animations, updates synchronous with data ticks.
  — [Wikipedia: Bloomberg Terminal (Keyboard section)](http://en.wikipedia.org/wiki/Bloomberg_Terminal)

**Lesson for Sentinel:** a professional console earns trust through **stable, terse, keyboard-driven command
vocabulary and maximal information per viewport** — the polar opposite of discoverability-first marketing
design. Our §5.5 keyboard-first rule and P4 density rule are the same instinct, with one difference: Bloomberg
can rely on trained users; we must also pass the 3 AM test for a sleep-deprived operator, so density must
pair with the §5.1 severity→action→evidence hierarchy.

### 1.9 Command palette interaction model (setproduct teardown, Sep 2026)

The sharpest interaction reference found: a production teardown of 10 palettes (Linear, Raycast, Vercel,
Notion, Figma, GitHub, Slack, Superhuman, Arc, Cal) plus **8 states most teams never design**:
default-with-recents, typing-with-live-results, no-results (recovery path, not dead end), loading skeletons
at final row height, error/offline (**local commands keep working** — maps to our §6 load-shedding order),
nested-page-with-breadcrumb, multi-select/batch (footer switches to selection summary — batch triage),
disabled-command-with-reason.
Interaction rules: **input never loses focus**; Enter goes deeper, Backspace-on-empty goes back, Esc closes —
"back is never the same key as close"; **frecency ranking** (frequency × recency, decayed, per user);
**fuzzy matching with visible highlight** on matched characters (without the highlight the order looks
arbitrary); mode prefixes (`>` commands, `@` people, `#` tags); spatial memory — **never reorder groups
between keystrokes**; print the real shortcut on every row (Figma's move — the palette *teaches* the
keyboard).
Linear's signature move: "nested sub-pages feel like one list… Backspace walks back one level. The input
never loses focus."
— [setproduct: Command Palette UI Design, 8 States and 10 Teardowns](https://www.setproduct.com/blog/command-palette-ui-design-guide)

---

## 2. Synthesis — answers to the four research questions

**Q1. How do consoles render unbounded alert payloads without hardcoding fields?**
The industry converged on three mechanisms, always in combination:
(a) **A fixed typed envelope** for what the *product* needs (PagerDuty's payload schema; Opsgenie's
fixed alert fields; OnCall's mapped fields) — the console's chrome, lists, filters, and sorts touch
*only* this envelope, never the raw payload.
(b) **A deterministic generic renderer** for the arbitrary remainder (PagerDuty: dict → table; Opsgenie's
`details` map; BigPanda's Alerts-tab tag table). It never pretends to understand the data — it renders
structure faithfully.
(c) **Operator-authored projections** declared as configuration with live preview (OnCall's Jinja2
appearance templates: `Title`/`Message`/`Image URL` per surface; BigPanda's primary/secondary properties
and Manage Alert Views; Datadog's configurable incident fields). No vendor engineer hardcodes a schema;
the customer declares what matters, per integration, against real payloads.
Nobody we read attempts free-text querying of raw payloads as the primary interface, and nobody lets the
raw payload leak into list chrome.

**Q2. How do customers shape different views/workflows over the same features?**
The composable unit is always **a saved named filter over typed fields**, never a dashboard builder:
Linear's Custom Views (typed filter JSON + scope + favorites + subscriptions + owners), Opsgenie's saved
searches (query + sharing), Datadog's saved views, BigPanda's Environments, PagerDuty's per-channel-type
Card Builder templates. Two cross-cutting lessons: (i) **views are not just filters — they are
notification/subscription endpoints** (Linear: notify when an item *enters* a view; Opsgenie: alert
filtering; Datadog: saved views in the workflow); (ii) **density is a mode, not a redesign**
(Opsgenie comfort/cozy/lite; our §2.2 density tokens). And Linear's Triage model is directly portable:
a first-class inbox for work the system deliberately keeps out of the main surface — our suppressions
need exactly this.

**Q3. How do they render decision provenance?**
Four patterns, in increasing strength: (1) **event ledger** — Opsgenie's Activity Log (which policies ran,
who did what, in order) and Datadog's incident timeline; (2) **matched-rule display** — BigPanda's
lightning-bolt "Matched Correlation Patterns" (which rules assembled this incident) — the single best UI
metaphor found for "why did this happen"; (3) **match-dimension visualization** — Moogsoft's radar charts
showing *the matching criteria* for correlated events (open-box ML); (4) **suspect-with-reasoning** —
BigPanda marks changes *Suspect, never Match*, with a written reason and a score, explicitly leaving the
verdict to the human — the honest rendering of model uncertainty our §4.2 demands. None of them render a
bare label; every automated verdict carries its evidence trail.

**Q4. What interaction patterns make them fast?**
- **Keyboard as first-class citizen, palette as universal verb surface** (Linear: Cmd+K with nested
  sub-pages, input never loses focus, frecency; every action keyboard-reachable; single-key daily-loop
  keys). The setproduct 8-states spec is the implementation checklist, including multi-select → footer
  becomes a batch bar (our bulk-triage pattern).
- **Bulk triage primitives everywhere:** Opsgenie (select N → ack/close; Ack-all/Close-all), BigPanda
  (Select All, Split/Merge from the table), PagerDuty (ManageIncidents bulk endpoint).
- **Muscle-memory command vocabulary** (Bloomberg mnemonics + color-coded GO/Cancel; stable single keys in
  Linear) — speed comes from *stable* bindings the operator can internalize, not from clever ones.
- **Optimistic, measurable speed** (Linear: <100ms perceived interactions, optimistic UI, preloading,
  skeleton-free first render — the same instinct as our §6 budgets and §5.4 zero-CLS rule).

---

## 3. Direction A — "The Typed Contract River" (opinionated, closed)

**Thesis.** Sentinel's console renders exactly one thing it understands: **its own decisions.** The vendor
payload is *evidence*, never *chrome*. We choose the smallest possible typed contract and enforce it
ruthlessly — the Linear lesson ("opinionated beats configurable") applied to an SRE console.

**The FIXED typed decision contract** (what the engine guarantees on every decision record — R1/R5/R6 in
§11; this is a **Type 1** decision, frozen by RFC):
`decision_id, alert_id, disposition ∈ {page, suppress, mute, escalate}, severity ∈ {critical, major,
minor, info}, service, check, confidence (quantized to engine precision), calibration_ref, policy_version,
evidence_excerpt (≤ 280 chars, engine-authored one-line summary), as_of, freshness_state,
fallback_reason?`. Nothing else is queryable, sortable, or filterable. The river (S1), the views, the
simulator axes, the shadow tables — **all of them read only these fields**. This is the enforcement arm of
P2: a decision that can't show its companions can't exist in the UI, because the contract makes the
companions mandatory at the type level (F8 generated types).

**The GENERIC renderer** (Type 2): one component, `<PayloadViewer>`, with a deterministic rendering law —
scalar → value row; map → two-column key/value table (the PagerDuty `custom_details` rule); array →
numbered list; nested depth > 3 → collapsed by default; payload > 64 KB → truncated with a stated byte
budget and a download link. It is deliberately *dumb*: it never guesses semantics, never promotes a vendor
key into chrome. Keyboard-navigable, not hover-dependent (§5.2). It appears only in S2 (evidence) — **the
river never shows payload fields**, full stop.

**The USER-COMPOSABLE layer** (Type 2): **Saved views = named typed filters over contract fields only** —
the Linear model, exactly: personal or team-shared, favorited/pinned to the nav, subscribable ("notify me
when a decision *enters* this view" — e.g. a "suppressed criticals" view that pings the on-call for
review), URL-shareable with temporary filters. View definition is `DecisionFilter` JSON, generated from the
same contract (F8) — a view can never reference a field that doesn't exist, so views cannot rot when
vendors change payloads. **No template language, no per-integration rendering config.** If operators need a
vendor field to be filterable, the fix is to **promote it into the contract via RFC** (engine extracts it
at ingest), not to bolt on ad-hoc querying. Triage-model import: a first-class **Suppression Inbox**
(Linear Triage analog) — suppressions live there, reviewable and appealable, excluded from the river by
default.

**Monkey-first + kill conditions.** Hardest part: *contract sufficiency* — can the 3 AM operator answer
P1's five questions from contract fields + the generic viewer alone, without vendor-specific rendering?
Kill condition: in a timed operator exercise, if >20% of "why did this page/suppress" answers require
opening raw payload to resolve, the contract is too thin → Direction A is killed (or a promotion round
runs first; the kill is on the *direction*, not the goal).

**Pre-mortem (top 3).** (1) Vendor with 200-key payloads: operators complain the generic viewer is a wall
of text — mitigation: evidence_excerpt is engine-authored, and promotion-by-RFC exists; (2) Power users
demand payload-field filtering we refused — mitigation: the promotion path is fast (Type 2 field additions
to the contract are cheap; the *closedness* is what's Type 1); (3) The contract freezes and rots —
mitigation: contract review is a standing quarterly item next to the 3 AM test.

### What Direction A explicitly will NOT build (and why)

1. **No free-text/JSONPath querying of raw payloads.** Unbounded cardinality, unshareable views, untestable
   honesty states. Global-over-local: it optimizes one debugging session at the cost of the whole
   view system's maintainability. (Proxy trap: "powerful search" is a proxy; "time to correct action" is
   the objective.)
2. **No per-integration rendering code, ever.** The vendor set is unbounded; per-integration UI is a
   maintenance cliff and a Chesterton's-fence violation — we'd be re-learning why PagerDuty chose the
   generic table, the hard way.
3. **No NL-generated views or AI-summarized payloads.** Non-deterministic rendering on a safety surface
   violates P3/P4.4; every derived string must be labeled, and an NL-generated view cannot carry a stable
   audit trail.
4. **No hover-only payload drill-down** (§5.2 prohibition, stated as law already).

---

## 4. Direction B — "Declarative Lenses" (open, operator-authored projections)

**Thesis.** The fixed contract (A's envelope) is necessary but not sufficient: real operators live in
vendor-specific fields (`cluster`, `runbook_url`, `error_budget_burn`), and the industry's best answer
(OnCall's templates, BigPanda's primary/secondary properties + Manage Alert Views) is to let the operator
**declare the projection as data** — with live preview, versioning, and validation — rather than hardcode
it in the console.

**The FIXED typed decision contract** (same as A — shared Type 1 foundation; this is deliberate: the
disagreement between A and B is about *payload projection*, not about Sentinel's own decision record).
Sentinel's decision fields are identical and identically closed. The difference is entirely in how vendor
payload becomes *presentable and queryable*.

**The GENERIC renderer** (same deterministic `<PayloadViewer>` as A — the fallback when no lens matches;
honesty law: an unlensed payload renders structurally, never semantically).

**The USER-COMPOSABLE layer** — **lenses**, declarative per-integration documents (JSON, versioned, stored
in the platform store, never code):
- `projection`: ordered column/field bindings — e.g. for integration `prometheus`: columns
  `alertname → title slot`, `severity → severity override? NO — display only`, `runbook_url → action link`,
  `cluster, namespace → facet columns`. This is BigPanda's Manage Alert Views + primary/secondary property
  as data.
- `render`: per-surface appearance declarations — river row subtitle template, S2 evidence panel sections
  (named groups of payload keys: "Impact", "Links", "Raw"), shadow-table columns. Declarative string
  interpolation over payload paths only — **no conditionals, no loops, no scripting** (deliberate
  rejection of OnCall's full Jinja: a Turing-complete template language executing against the console is a
  Type 1 security/reliability liability we refuse).
- `provenance`: named display for the *match dimensions* — which evidence keys fed the decision (Moogsoft's
  radar-chart instinct, rendered as a labeled key-contribution list, not a chart by default).
- Lenses are **validated at save time** against the contract (F8): a lens may only bind display slots the
  console defines; it can never invent chrome. Lens versions are pinned to decisions (the decision record
  stores `lens_id + lens_version`), so a decision's rendering is reproducible forever — provenance of the
  rendering itself. **The lens editor is the three-column live instrument** (example payload | lens | rendered
  result) stolen directly from OnCall's template editor — design never waits for backend: it works against
  contract-shaped fixtures (R2).
- Saved views (same Linear model as A) can additionally filter/group on **lens-projected fields** — but only
  fields the lens declares; undeclared payload remains browsable, not queryable.

**Monkey-first + kill conditions.** Hardest part: *lens authorship UX* — can an operator who has never read
docs author a correct lens for a new integration in a timed exercise? Kill condition: if median authorship
time exceeds 15 minutes, or if a lens-version drift produces wrong columns in a drill (rendering
irreproducibility), the machinery cost exceeds the value → Direction B is killed. The fallback is A.

**Pre-mortem (top 3).** (1) Lens sprawl: 40 integrations × 3 lens versions = unmaintainable config zoo —
mitigation: lenses are owned (named owner, like Linear view owners), unused lenses expire with a warning;
(2) Operators treat lens-projected fields as engine truth (confusing display binding with decision input) —
mitigation: projected fields render with a distinct "vendor field" treatment, never in the severity/confidence
slots; (3) The lens store becomes a second contract the platform team must version — mitigation: lenses are
validated data, versioned immutably; old versions never mutate.

### What Direction B explicitly will NOT build (and why)

1. **No Turing-complete template language in the console** (no Jinja-in-browser). OnCall gets away with it
   server-side; we render client-side, and arbitrary template execution is a security boundary we will not
   defend. Declarative bindings or nothing. (Type 1 reasoning: this is nearly irreversible once operators
   depend on it.)
2. **No automatic lens inference ("AI generates your lens from payload").** Non-deterministic authoring of a
   safety surface's rendering; violates the honesty contract at the meta level (P3 applies to the renderer,
   not just the rendered).
3. **No cross-integration lenses** (one lens spanning vendors). Lenses are per-integration precisely so a
   vendor's schema change has a bounded blast radius. Global-over-local: the "one view to rule them all"
   optimizes a demo at the cost of every future vendor change.
4. **No lens-conditioned *behavior*** (grouping, routing, suppression on lens fields). OnCall merges
   appearance and behavior templates; we keep them surgically separate — lenses may only *display*.
   Behavior stays in the engine behind the typed contract. This is the line that keeps Direction B from
   becoming a shadow rule-engine.

---

## 5. The A-vs-B disagreement, stated for the commit step

| | **A: Typed Contract River** | **B: Declarative Lenses** |
|---|---|---|
| Queryable surface | Contract fields only | Contract fields + lens-declared fields |
| Per-integration rendering | None — one generic viewer | Declarative lenses, validated, versioned |
| Operator learning curve | Zero config; promotion-by-RFC for new fields | Must author lenses (15-min kill condition) |
| Failure mode | Contract thinness → RFC backlog | Lens sprawl → config zoo |
| Trust story | "The console only shows what the engine guarantees" | "The console shows what you declared, pinned and reproducible" |
| Best when | Vendor set is stable; team owns the contract | Vendor set is heterogeneous; operators are power users |

Shared (non-negotiable, from INTERFACE_PRINCIPLES.md): the fixed decision contract, the honesty UI (§4),
the five surfaces (§3), keyboard-first (§5.5), density (P4), the anti-slop checklist (§8), the generic
renderer as fallback, and "lenses/templates never drive behavior" (B's item 4 — actually a point of
*agreement* both directions must hold).

**Type labels:** the A-vs-B choice is **Type 1** (shapes the store — do lenses live in it? — the query
surface, and five years of operator mental model). The generic renderer is Type 2. The Saved Views model
(named typed filters + subscriptions + Triage-style suppression inbox) is agreed under both directions and
is Type 2 to start.

**My committed position going into disagree-then-commit:** **A as the default, B as the escape hatch.**
Ship A first (it passes the 3 AM test with the least machinery, and its kill condition is directly
measurable); promote to B only when the contract-thinness kill condition fires in a real operator
exercise — not before. Rationale (global-over-local): B's machinery is real and permanent; A's
limitation is curable by RFC. But B's lens editor and lens versioning are worth *designing now* (contract
first, implementation parallel) so the promotion path exists.

---

## 6. Cross-cutting interaction commitments (both directions)

Distilled from §§1.7–1.9, to be specified in the build lanes:
- **Command palette** (Cmd+K) implementing the setproduct 8-state spec: recents-first empty state,
  frecency-ranked fuzzy results with match highlighting, no-results recovery, skeleton rows at final
  height, offline-tolerant local commands, nested sub-pages with breadcrumb (Backspace ≠ Esc), multi-select
  → batch footer bar, disabled-with-reason rows. Palette actions: navigate, act on selection (ack,
  escalate, appeal suppression), open views, change thresholds (gated by role).
- **Single-key daily-loop bindings** on the river (Linear/Bloomberg instinct): j/k or arrows navigate,
  Enter opens evidence, a/e ack/escalate, s suppress-appeal, / focuses filter. Stable, printed on rows
  (Figma's teaching move), documented in a discoverable cheat sheet (§5.5).
- **Bulk triage bar** on multi-select (Opsgenie/BigPanda pattern): acknowledge N, escalate N, appeal N —
  with the §5.2 destructive-action confirm (consequences stated) and undo where the domain allows.
- **Suppression Inbox** as a first-class surface (Linear Triage analog): suppressions are reviewable work,
  not a hidden log — excluded from the river by default, subscribable ("notify me when a critical enters
  the suppression inbox").

---

## 7. Open questions for L1b disagree-then-commit

1. Is the 15-minute lens-authorship kill condition the right bar, or does it undervalue B for
   heterogeneous vendor estates?
2. Should lens-projected fields be allowed in S3 (threshold simulator) axes, or does that violate
   "lenses never drive behavior"?
3. Does Direction A's promotion-by-RFC path survive contact with a 40-integration estate, or does the RFC
   backlog become the very bottleneck A claims to avoid? (This is the strongest argument for B-first.)
4. Command palette scope: actions-only (setproduct's definition — "do it, not where is it") or
   palette-as-universal-search? I argue actions-only; search belongs in the river's filter bar.

---

*Research completed 2026-10-04 ~01:30 IST by lane L1a. All sources read in full; URLs above are verbatim
from tool results. No vendor UI was operated live (subagent browser constraint); all claims rest on the
cited documentation as written.*
