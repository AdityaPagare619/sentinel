# RESEARCH REPORT — Observability Tools: Mechanisms Study (Datadog, Grafana, Sentry, Honeycomb)

*Research worker notes for the Sentinel SRE console design program. Mechanisms, never screenshots. Every claim sourced inline; unverified items marked explicitly.*

Method: official product docs, vendor engineering blogs, teardown articles, community docs — text only, no live-site clicking. Jargon translated to plain English on first use.

---

## TOOL 1 — DATADOG

### 1. Avoiding the infinite list

**The Monitor is the aggregation primitive, not the event.** Datadog separates *monitors* (rule definitions) from *monitor groups* (individual per-label evaluations — e.g., one "CPU > 90%" monitor evaluated per host yields hundreds of groups). A monitor query can be "grouped by" tags; each group gets its own state (OK / Alert / No Data / Warn), but the Monitors page shows one row per monitor with aggregated status — not one row per group. The Pulumi registry docs for `datadog.Monitor` describe `notify_by`, which controls what granularity a grouped monitor notifies on — e.g., notify only per `cluster` instead of per `pod` — or configure as "simple-alert" sending one notification no matter how many groups breach (https://www.pulumi.com/registry/packages/datadog/api-docs/monitor/). Plain English: you decide whether 400 pods breaching = 400 notifications or 1.

**Composite monitors** combine two or more monitors with boolean logic (AND/OR/NOT), so a "this AND that" failure produces one alert instead of two related ones (https://www.channele2e.com/news/datadog-watchdog-cloud-monitoring).

**Watchdog "stories" collapse related anomalies automatically.** Watchdog (Datadog's machine-learning anomaly engine) groups related APM anomalies *across different services* into a single "story" when it detects one underlying issue affecting multiple services, and the story includes a dependency map showing the originating service plus affected downstream services (https://datadoghq.com/blog/watchdog-enhanced-visibility). Instead of N service-level anomaly alerts, the human sees one narrative unit that already contains the blast radius.

**Log stream compaction via patterns.** In Log Management, Watchdog performs aggregations at intake on *detected log patterns* plus `environment`, `service`, `source`, `status` tags, and scans those aggregates — not raw lines — for anomalies like "sudden increase of warning/error logs" (https://docs.datadoghq.com/logs/explorer/insights/). Logs are compressed into *patterns* before a human ever scrolls them.

### 2. Compression into attention units

**The "story" is Datadog's invented attention unit for the unplanned.** Watchdog's feed of "stories" exists precisely for areas where the customer set *no* monitors: "what happens when latency starts to increase, or error rates spike, in areas of your application where you *haven't* set alerts?" (https://www.datadoghq.com/blog/watchdog/). Each story = a plain-language summary of what happened: which resource was affected, where, and for how long — with the timeframe of interest automatically highlighted on a timeseries graph.

**For planned coverage, the attention unit is the monitor group + priority.** Monitors carry an explicit priority (1 = high, 5 = low) setting alert severity (Pulumi registry docs, ibid).

**Severe-only promotion rule (verified, concrete).** In logs, an anomaly is only "severe" — and therefore promoted to the Watchdog alerts feed and made alertable — if it: contains error logs, lasts at least 10 minutes (to avoid transient errors), and shows a significant increase (to avoid small increases) (https://docs.datadoghq.com/logs/explorer/insights/). A documented, numeric fatigue filter: 10-minute minimum duration + magnitude gate.

**Service Page aggregates per-service attention.** The APM Service Page puts a "Service monitor" panel (active monitors/synthetics linked to the service), a Watchdog Insights carousel, and summary cards (deployments, new error-tracking issues, SLOs, ongoing incidents), all keyed off the `service` tag (https://docs.datadoghq.com/tracing/services/service_page/). Plain English: the service, not the alert, is the anchor for investigating one component.

### 3. Progressive disclosure

- **Layer 0 (feed):** Watchdog stories feed — one-liner + highlighted timeframe + plain-language summary (https://www.datadoghq.com/blog/watchdog/).
- **Layer 1 (detail page):** auto-aggregated performance statistics (throughput, errors, latency percentiles) for the specific service/resource; for the triggering indicator it shows *recent values vs. expected values from historical trends* so the engineer sees "how significant the anomaly is"; plus automatically surfaced "related behavior that might have the same underlying cause" (ibid.).
- **Layer 2 (raw):** for error events, Watchdog pulls in the common stack traces — "from not knowing about an issue to knowing exactly which line of code is causing the issue in one click" (ibid.). In logs: Watchdog Insights banner expands → side panel → per-insight timeseries, associated tags, full list of log patterns (https://docs.datadoghq.com/logs/explorer/insights/).

### 4. Trust in automation

- **Plain-language + timeframe + expected-vs-actual:** every Watchdog story states resource, location, duration, and shows the indicator's actual vs. historically-expected values. The mechanism that builds trust is *showing the comparison*, not asserting the conclusion.
- **Correlation without claiming causation:** the detail page shows "related behavior that *might* have the same underlying cause" — hedged language, letting the human judge. Watchdog's root-cause capability "pinpoints the precise service where an issue originated" via causal relationships across services, plus business impact via RUM (https://www.helpnetsecurity.com/2022/04/15/datadog-watchdog/).
- **Explicit severe-anomaly criteria** published (error logs + ≥10 min + significant increase) — automation decisions auditable against stated policy.
- **Config-from-story:** stories offer custom monitors — "if teams want to make sure they are notified should similar issues arise" (https://www.devclass.com/devops/2020/01/07/datadog-digs-deeper-into-infrastructure/1624366). The AI's finding becomes a durable, human-owned rule.
- **Audit controls on monitors:** `notify_audit` notifies tagged users when a *monitor definition itself* changes — trust includes watching the watchers (Pulumi registry docs, ibid.).
- **Bits AI SRE (2025):** a 24×7 on-call agent doing initial triage of alerts with preliminary findings *before* a human logs on — explicitly "human-in-the-middle workflows" (https://itbrief.com.au/story/datadog-launches-bits-ai-sre-to-automate-incident-resolution). The agent pre-investigates, assigns owners, drafts the post-mortem — the human remains the responder.

### 5. Realtime without disorientation

- **Live Tail for logs:** "See your ingested logs in real time" across environments (https://docs.datadoghq.com/logs/). In Containers Explorer: "Pausing the stream helps you read logs that are quickly being written; unpause to continue streaming." Explicitly: streaming logs are NOT persisted — entering a new search or refreshing clears the stream (https://docs.datadoghq.com/containers/monitoring/containers_explorer/). The live view is deliberately ephemeral; history comes from indexed logs.
- **Real-time collection has an off-switch:** container real-time (2s) data collection "is turned off after 30 minutes. To resume real-time collection, refresh the page" (ibid.) — a cost/attention guard against leaving a firehose open.

### 6. STEAL vs AVOID

**STEAL:** the *story* as attention unit (what/where/how long, bundling related anomalies across services); the severity gate in explicit numbers (error-logs + ≥10 min + significant increase); expected-vs-actual drawn on the detail graph; notify-on-subset-of-groupings (one knob turning 400 pod alerts into 1 cluster alert); stories → monitors (one-click conversion of an automated finding into a human-owned durable rule).
**AVOID:** the Watchdog anomaly/outlier/forecast models are proprietary ("the band can't be derived from the query" — http://dev.to/kaushik94/i-wrote-a-datadog-to-grafana-monitor-translator-250f). A 3-AM engineer cannot audit a black-box band; prefer explainable anomaly math. Also: live-tail (ephemeral, cleared on search) vs. indexed logs split loses stream context the moment the engineer refines a query — carry stream context across refinements.

---

## TOOL 2 — GRAFANA

### 1. Avoiding the infinite list

**Alertmanager-style notification grouping is the primary anti-list mechanism.** Grafana Alerting routes alert *instances* through a tree of notification policies that *group multiple alerts into a single notification* — explicitly because "our alerting setup can easily trigger hundreds or even thousands of alert instances" (https://grafana.com/docs/grafana/latest/alerting/fundamentals/notifications/). Grouping works on **labels** (e.g., group by `alertname`, or `alertname`+`service`) plus **timing options**: `group_wait` (default 30s — wait before first notification so stragglers join the group), `group_interval` (default 5m — wait before notifying about new alerts added to an already-notified group), `repeat_interval` (default 4h — re-notify about unchanged alerts) (https://community.grafana.com/t/grafana-default-notification-policy/90628). Plain English: 500 alert instances become one message per label-group, held 30s, re-sent at most every 4h unless something new appears.

**Data-layer aggregation by convention:** high-cardinality data (user IDs, unique URLs, timestamps) must NOT be labels; normalize endpoints (`/api/users/{id}`), pre-compute with Prometheus recording rules so dashboards query one pre-aggregated series; collapse dashboard rows so unexpanded panels are never queried (https://dev.to/cyberscoper/building-enterprise-level-monitoring-from-prometheus-to-grafana-dashboards-3pge; https://docs.aws.amazon.com/grafana/latest/userguide/v10-dash-troubleshoot.html).

**Silences and mute timings** pause notifications ad-hoc or on schedule *without stopping evaluation* — "pause or suppress notifications without interrupting alert evaluation" (Grafana docs, ibid.).

### 2. Compression into attention units

**The alert *rule* is the unit of attention; alert *instances* are the unit of evaluation.** The alert list shows rules with state, health, how long active, and *number of active instances* (verified via the Backstage Grafana plugin's live alerts table reading Grafana's rules API — https://github.com/marble-sh/backstage-plugins-grafana/blob/HEAD/plugins/grafana/README.md). One rule row with "42 active instances" is the compression.

**Annotations are the attention-compression overlay.** Event markers (deploys, alert state changes) drawn directly on timeseries graphs — "a spike aligned with a 'Deployment Started' annotation is expected" (https://medium.com/@sre999/grafana-advanced-automate-alert-and-secure-your-dashboards-like-a-pro-e01c96678ca6; AWS docs confirm alert-state-history annotations are built into every dashboard — https://docs.aws.amazon.com/grafana/latest/userguide/dashboard-annotations.html). Instead of correlating two lists (events + graphs), the graph *carries* the events.

**Stat panels as compression:** headline numbers ("Service Up", "Firing alerts", error rates) at the top of dashboards, coupled to the chosen time range (https://github.com/my-perfect-system/grafana-integrations/blob/HEAD/AGENTS.md).

### 3. Progressive disclosure

- **Layer 0:** alert list / dashboard stat row — rule name, state, active-since, instance count.
- **Layer 1:** the rule's panel — query graph with threshold line; alert-state annotations showing *when* the alert started/cleared on the same graph (https://github.com/krisiasty/arex/blob/HEAD/docs/grafana-dashboards.md).
- **Layer 2 (raw):** Loki log panel with live tail, or Explore view with the raw query; recording rules are the pre-computed aggregates underneath.
- **Correlational disclosure:** every error-rate panel should link to logs (Loki) and traces (Tempo) *filtered to the same time range and labels* (https://github.com/leoyeai/openclaw-master-skills/blob/HEAD/skills/grafana-panel-engineer/SKILL.md). Drill-down preserves context across data sources.

### 4. Trust in automation

- **The policy tree is inspectable:** explicit tree (root → nested routes) with matchers, timing knobs, contact points (https://grafana.com/docs/grafana/latest/alerting/fundamentals/notifications/).
- **Labels decide routing, visibly:** plain-text matchers like `severity=critical` in the policy (http://dev.to/sanket_patharkar/alerting-as-code-grafana-rules-contact-points-and-jenkins-dry-runs-2a1f). No hidden model.
- **State history is first-class:** Grafana writes alert state transitions and shows them as annotations — the "why did this fire" answer is a visible timeline.
- **Evaluation ≠ notification:** silences never stop evaluation, so "we didn't get paged" is distinguishable from "nothing was wrong."

### 5. Realtime without disorientation

- **Refresh is opt-in and interval-based:** "By default, Grafana does not automatically refresh the dashboard" — the user picks an interval (https://docs.aws.amazon.com/grafana/latest/userguide/v9-dash-using-dashboards.html). Convention: 30s for live ops dashboards, 5m otherwise, off for debug (https://github.com/leoyeai/openclaw-master-skills/blob/HEAD/skills/grafana-panel-engineer/SKILL.md).
- **Refresh cancels pending requests:** "Grafana cancels any pending requests when you trigger a refresh" (AWS docs, ibid.) — rapid refresh can't stack stale responses out of order.
- **The docs actively warn against fast refresh** ("puts unnecessary stress on the backend"; recommend 1m/10m/1h unless needed — https://docs.aws.amazon.com/grafana/latest/userguide/v10-dash-troubleshoot.html).

### 6. STEAL vs AVOID

**STEAL:** label-based notification grouping + the three timing knobs (group_wait/group_interval/repeat_interval) — the most battle-tested storm compressor in this set, every knob a tunable number; annotations drawn on graphs (alert transitions + deploys on the timeseries itself); cross-source drill-down links preserving time range + labels; refresh-cancels-pending rule.
**AVOID:** Grafana puts compression burden on the *operator* (grouping policy, labels, annotations, recording rules are DIY — out of the box it's a blank grid); for exhausted users, grouping must be default-on. Per-panel refresh means N independent queries at N cadences — at 3 AM disagreeing panels read as "the page is lying"; a console needs one coherent refresh heartbeat. And never show template source to on-call: notification templates can leak raw Go syntax (`{{ $values.B }}`) — verified complaint (https://github.com/marble-sh/backstage-plugins-grafana/blob/HEAD/plugins/grafana/README.md).

---

## TOOL 3 — SENTRY

### 1. Avoiding the infinite list

**Fingerprinting is the core mechanism.** "A fingerprint is a way to uniquely identify an event, and all events have one. Events with the same fingerprint are grouped together into an issue." Error events are fingerprinted from stacktrace → exception → message (in that priority order); transaction events by their spans (https://docs.sentry.io/concepts/data-management/event-grouping/). 50,000 identical crashes = 1 issue, with zero user configuration.

**Grouping is versioned and auditable.** Every change to the default grouping algorithm ships as a new *version*, applied only to new events; projects pin the grouping version. The Issue Details page has an "Event Grouping Information" section showing whether the issue was grouped by fingerprint, stack trace, exception, or message (ibid.).

**AI-assisted grouping (newer):** a transformer model embeds the error's message + in-app stack frames, compares against existing error embeddings per project, and merges semantically similar errors (e.g., same bug with slightly different stacks after a deploy) — but it *only merges new issues and never splits fingerprint-grouped issues*, and skips fully custom fingerprints. Merged issues are listed in the UI's "Merged Issues" section and can be unmerged (ibid.) — reversible automation.

**User-controlled grouping:** fingerprint rules (`error.type:DatabaseUnavailable -> system-down`), stack-trace rules, SDK-side fingerprints, manual merging; inbound filters drop unwanted events before they become issues (http://sentry.io/astro-assets/resources/resource-files/sentry-automate-group-alert-ebook.pdf).

**Spike protection as circuit breaker:** detects abnormal ingestion volume using hourly rates over the last 7 days, accounting for daily/weekly seasonality and historical variance, and drops excess events — with an in-app UI showing threshold, duration, and dropped counts, plus owner notification on activation (https://github.com/getsentry/sentry/discussions/44155). When a flood starts, Sentry chokes it at the intake and tells you what it discarded.

### 2. Compression into attention units

**The Issue is the unit of attention** — "Monitors detect; Issues are the unit you triage; Alerts respond" (https://github.com/danieljvdm/dev-kit/blob/HEAD/skills/sentry/references/concepts/monitors.md). An issue is a grouped, stateful object with status, priority, assignee, history. The Issues stream sorts by event counts, affected users, new vs. regressed.

**Prioritization signals:** the Issue Details header shows the error message, *how often seen, how many users affected*; below it an event graph shows the distribution of events/errors over time; the sidebar shows first/last seen, linked GitHub/Jira issues, activity — and a **facet map** of tag-value distributions across all events in the issue (https://docs.sentry.io/product/issues/issue-details/; https://github.com/erickzhao/sentry-docs/blob/HEAD/docs/product/issues/issue-details/index.mdx). One screen answers "how bad, since when, who is hit, which slice of users."

**Regression detection:** alerts can fire specifically when a *resolved* issue reappears — "we fixed this and it's back" is a first-class signal (Sentry ebook, ibid.).

### 3. Progressive disclosure

- **Layer 0 (stream):** Issues list — title, event count, user count, first/last seen.
- **Layer 1 (header):** error message + frequency + affected users; actions (assign, priority, resolve/archive, subscribe, share).
- **Layer 2 (aggregate):** event graph (filterable distribution), tags, facet map.
- **Layer 3 (single event):** stack trace, breadcrumbs (trail of events before the error, with a "View All" drawer supporting search/filter/sort — https://docs.sentry.io/product/issues/issue-details/breadcrumbs.md), tags, context, plus replays, attachments, user feedback.
- **Layer 4 (raw):** "View JSON" shows the raw payload including the fingerprint property (https://docs.sentry.io/concepts/data-management/event-grouping/).
- **Event navigator:** "Previous/Next Event" buttons move between events *within* the same issue — the rawest layer stays inside the grouped context (https://docs.sentry.io/product/issues/issue-details/performance-issues/).

### 4. Trust in automation

- **Grouping provenance shown:** "Event Grouping Information" names the mechanism (fingerprint / stack trace / exception / message / "Sentry Defined Fingerprint") — the single strongest trust mechanism in this set.
- **AI grouping is reversible and conservative:** only merges, never splits; skips custom fingerprints; unmerge is one click and the system "will avoid grouping them in the future."
- **Seer (AI debugger):** Autofix is a *four-step collaborative workflow* — Root Cause Analysis → Solution Identification → Code Generation → PR Iteration (https://docs.sentry.io/product/ai-in-sentry/seer/autofix/). Auto-trigger only for issues with ≥10 events; PR creation optional; "Nothing merges without a human signature" (https://medium.com/data-science-collective/how-sentry-built-seer-3c0456488822). Seer refuses metric-alert issues; its output is "a hypothesis to verify against the repo, not gospel" (https://github.com/w159/atlas/blob/HEAD/plugins/armada/skills/armada/departments/engineering/skills/sentry-seer-root-cause/SKILL.md).
- **Alert-rule anatomy explicit:** "When" trigger criteria, "If" condition filters (issue age, latest release), and an Action Interval rate-limiting notifications per issue; percent-based alerts self-adjust to usage (Sentry ebook, ibid.).
- **Cautionary evidence:** a Sept 2026 CERT/CC note (VU#212479) showed Seer's auto-remediation could be poisoned by attacker-controlled telemetry through the public DSN (https://automater.ai/intel/untrusted-telemetry-auto-remediation-register/). Automated reasoning over untrusted signal content is an attack surface, not just a UX question.

### 5. Realtime without disorientation

Sentry is fundamentally poll-and-refresh, not a live stream. The Issues stream updates as events arrive; dashboards auto-refresh on practitioner convention (~5 min — https://github.com/powerplatformtoolbox/desktop-app/blob/HEAD/docs/SENTRY_DASHBOARDS.md). The event graph is a historical distribution, not a ticking stream — *recency-anchored review* rather than live tailing. Metric alerts evaluate on event arrival but notifications are gated by the Action Interval (Sentry ebook, ibid.) — event-driven detection, rate-limited interruption. Spike protection prevents UI floods at the source.

### 6. STEAL vs AVOID

**STEAL:** fingerprint-first grouping with *shown provenance*; the Issue Details layering (header stats → event graph → facet map → one event's stack trace + breadcrumbs → raw JSON) — the cleanest summary→detail→raw ladder found; event navigator keeping raw events inside grouped context; regression as first-class signal; spike protection with visible "what we dropped and why"; Seer's ≥10-events gate before auto-investigation (a fatigue filter for the AI itself).
**AVOID:** Sentry's grouping is error-centric (stack traces, exceptions) — an infra/page-or-suppress console's signals have no stack trace; fingerprinting needs a different identity (service + signal type + affected scope). AI merging of "semantically similar" errors must stay exactly as visible as Sentry's "Merged Issues" section — anything less becomes mystery meat. And per the CERT/CC note: never let automated remediation consume untrusted signal content without provenance checks.

---

## TOOL 4 — HONEYCOMB

### 1. Avoiding the infinite list

**Wide events + query-time aggregation (the columnar bet).** Honeycomb stores *events, not pre-aggregated metrics*: each request/job emits one structured record with dozens–hundreds of fields. Adding `user.id` "doesn't create millions of time series — it's another column on each event, aggregated at query time" (https://github.com/honeycombio/agent-skill/blob/HEAD/honeycomb/skills/observability-fundamentals/SKILL.md). The columnar store reads only the columns a query touches (https://github.com/agentic15/knowledge-base/blob/HEAD/src/content/docs/lessons/monitoring-observability-saas/honeycomb-observability.md). Plain English: the list never exists because nothing is pre-bucketed; every view is a question answered on demand.

**Triggers replace monitors, and they're queries.** A Trigger = saved query + threshold + schedule: runs at a frequency, alerts when the threshold is met *x consecutive times* (default 1, max 5) — e.g., threshold met for 15 minutes = 3 cycles of 5 minutes before paging (https://docs.honeycomb.io/notify/triggers/create). No separate alert-rule language; the thing you investigated with is the thing that pages you.

**Sampling at the edge (Refinery):** the open-source Refinery proxy does tail-based sampling — waits for a *complete trace* before the keep/drop decision — so volume is controlled without losing interesting traces (knowledge-base teardown, ibid.).

**Dynamic baselines:** triggers compare against 1h/24h/7d/28d prior values with % or absolute change (Honeycomb docs, ibid.) — anomaly-shaped alerting without a black box.

### 2. Compression into attention units

**The heatmap + BubbleUp is the attention unit.** Canonical flow: a heatmap (duration distribution over time) shows an anomalous region; the engineer selects it; **BubbleUp** compares the statistical distribution of *every field value* in the selected region against the baseline window and ranks fields by statistical significance — e.g., "requests from us-east-1 using gRPC with payload > 50KB are 4× slower" without naming those dimensions (https://chatforest.com/reviews/honeycomb-mcp-server/; knowledge-base teardown, ibid.). The attention unit is *the anomalous subset plus its differentiating dimensions*.

**Verified correction:** BubbleUp answers *"what broke"* (the overrepresented dimension); a separate breakdown query answers *"who's affected"* (versions, user tiers). Conflating the two was a documented mistake their own curriculum had to fix (https://github.com/honeycombio/o11y-eng-masterclass/commit/970b7db256c02c97515d5746dfe4f6a84d5851b4). Keep "cause" and "blast radius" as separate computed answers.

**SLOs as the top-level attention filter.** SLOs on event data generate burn-alert dashboards; documented practice: 24h burn alert → Slack ("fix in the morning") vs. 4h burn alert → PagerDuty ("get cracking now") (https://DEV.to/honeycombio/honeycomb-slo-now-generally-available-success-defined-2hc3). The severity ladder is *time-to-exhaustion*, not raw severity — it directly answers "can this wait until morning."

**Query History as collective memory:** every team query is searchable (title, description, fields, author, time) — "replay the debugging steps from an incident" (https://docs.honeycomb.io/investigate/collaborate/explore-team-query-history).

### 3. Progressive disclosure

- **Layer 0:** Home view — recent traces + dataset summary (https://DEV.to/honeycombio/from-0-to-insight-with-opentelemetry-in-go-27hc); boards of saved queries.
- **Layer 1:** Query Builder result — SELECT/WHERE/GROUP BY/ORDER BY/LIMIT/HAVING producing line graphs, heatmaps, tables; the builder surfaces the team's "frequently queried values" in dropdowns (https://docs.honeycomb.io/reference/honeycomb-ui/query) — past attention shaping present UI.
- **Layer 2:** BubbleUp on a selected region — ranked differentiating dimensions.
- **Layer 3 (raw):** individual events/traces; the trace waterfall shows span hierarchy with field values inline.
- **Alert→investigation continuity:** "drop directly into the query that detected the issue and click into underlying trace data… all from within the same interface" (https://cdn.sanity.io/files/927dxf0h/production/535b8e7a0452880508d3e731d7c6587747253c1c.pdf). The trigger and the investigation are the same query — zero context loss.

### 4. Trust in automation

- **BubbleUp shows its work:** ranked field:value pairs with anomalous-vs-baseline proportions — a ranked list with numbers, not a verdict.
- **Triggers are legible queries:** because a trigger IS a query, "why did this alert fire" is answered by re-running it — the alert carries its own evidence (https://docs.honeycomb.io/notify/triggers/create).
- **Two alert modes, explicitly:** "Limited alerts" (one triggered + one resolved) vs. "Continuous alerts" (every run meeting threshold, no resolution) — the fatigue trade-off is a named, deliberate choice (ibid.).
- **Constraint honesty:** a trigger's query duration can't exceed 4× its frequency — a guardrail with teeth (masterclass commit, ibid.).

### 5. Realtime without disorientation

Honeycomb is query-driven, not stream-driven — no live-tail equivalent in the documented UI. "Now" is answered by re-running a query over a recent time range (the API's Query Result flow supports re-running a relative-time query "over and over… conceptually similar to clicking Run Query in the UI" — https://api-docs.honeycomb.io/api/query-data.md). Disorientation is avoided by *not having a live view at all*. Trigger evaluation cadence (1–15+ min) sets the effective "realtime" of alerting.

### 6. STEAL vs AVOID

**STEAL:** the heatmap→select→BubbleUp gesture — compare anomalous subset vs. baseline across all dimensions, rank by significance, show the proportions; triggers-as-queries (alert IS the investigation query; "open the alert's underlying question" in one click); the "what broke" vs. "who's affected" split; time-to-exhaustion (SLO burn) as the severity ladder; Limited vs. Continuous alert modes as an explicit fatigue trade-off.
**AVOID:** Honeycomb has essentially no native paging/on-call UX — it's an investigation tool wearing an alerting feature; a page-or-suppress console needs the inverse. Query-driven "now" means no true live view — fine for debugging, wrong for "is the site down right now." And per-event pricing ($3.00/million on new Pro plans, July 2026 — https://last9.io/blog/best-observability-tools/) creates "think before you query" friction — a 3-AM console must not inherit that hesitation.

---

## HONEST GAPS (could not verify)

1. **Datadog Monitors page exact UI** — verified the monitor/group data model, priority field, notify_by granularity, composite monitors from API/docs; not the exact current list-page layout/sorting or the monitor status page's threshold-graph rendering from text sources.
2. **Datadog event stream aggregation** — how the Events page compacts raw events at scale, not verified from docs text.
3. **Grafana native live-stream pause controls** — verified auto-refresh intervals, refresh-cancels-pending, Loki live-tail panels in practitioner dashboards; could not verify a unified native pause/stream control in Grafana's own UI from docs text.
4. **Sentry Issues stream live behavior** — could not verify any live-update or pause control; docs describe poll/refresh semantics.
5. **Honeycomb Boards auto-refresh** — could not verify from docs text.
6. **Datadog Watchdog false-positive handling** — verified the log severity gate (10 min / significance) but not a user-facing "dismiss as noise / train the model" feedback loop for Watchdog stories.
7. **Sentry metric-alert detail page** — verified the alert-rule anatomy (When/If/Action Interval) but not the exact "why did this fire" rendering.
8. All observations come from documentation/blog/teardown text, not hands-on use — interaction details (animation, exact click paths, latency feel) are unverifiable by this method.

---

**Caveats for the synthesis step:** this is mechanism research only, per the brief — no Sentinel design prescribed. The strongest cross-tool patterns for the synthesizer: (a) every tool invents an *attention unit* above raw signals (story / rule-with-instance-count / issue / anomalous-subset); (b) the best trust mechanisms are *shown provenance* (Sentry's grouping info, Grafana's policy tree, Honeycomb's trigger-as-query, Datadog's expected-vs-actual); (c) fatigue is handled with *explicit numbers* (group_wait 30s / repeat 4h; ≥10 min severity gate; ≥10 events before AI triage; Action Intervals); (d) the sharpest unresolved tension is live-stream vs. query-driven "now" — Datadog keeps both but quarantines the stream as ephemeral; Grafana polls; Honeycomb refuses the stream entirely.
