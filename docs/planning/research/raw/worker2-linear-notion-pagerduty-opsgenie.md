# Interface Research: Linear, Notion, PagerDuty, Opsgenie
**Research worker report — Sentinel SRE console program, 2026-10-06**
Mechanisms only, no aesthetics. Every claim carries its source inline. Plain-English translations of jargon on first use. No Sentinel design prescription — raw mechanism research.

---

# PART 1 — PAGERDUTY

## 1. Avoiding the infinite list

PagerDuty's core anti-list mechanism is **alert grouping at the service level**: incoming alerts are folded into incidents instead of each spawning its own incident. A service can be configured with one of four grouping methods (official docs, [Configurable Service Settings](https://Support.pagerduty.com/main/docs/configurable-service-settings)): **Intelligent** (machine learning, textual similarity across alert fields), **Alert Content** (exact matching on specified fields), **Time only** (everything inside a fixed window goes together), and **Global** (grouping across multiple services).

The grouping rules are concrete and auditable ([Intelligent Alert Grouping](https://Support.pagerduty.com/main/docs/intelligent-alert-grouping)):
- An alert joins an existing incident only if the most recent grouped alert was created **within a rolling grouping time window** (timestamp of the incoming alert is compared against the most recently grouped alert, on a rolling basis).
- The incident must be **less than 24 hours old**.
- The model must judge the alerts similar (default analysis field: the alert **Summary**; up to five fields configurable, 1,000-character total cap).
- Alerts that fail the criteria trigger a **new** incident.
- There is a hard cap of **1,000 alerts per incident**; after that, a new incident is created and subsequent alerts group into it.

The time window is self-calibrating in one respect: the "Recommended" window is computed from the **average time between alerts using the service's own historical data** — the parameter is estimated from the target data, not a magic constant. PagerDuty's own DataOps team used grouping + suppression to get a **37% reduction in incidents** (cited in the same doc).

Two pre-incident mechanisms sit upstream of grouping:
- **Event Orchestration**: rules that route, suppress, or enrich incoming events *before* they become incidents (described in the [chrishuffman5 PagerDuty skill](https://github.com/chrishuffman5/domain-expert/blob/HEAD/./plugins/monitoring/skills/pagerduty/SKILL.md) — "Event Orchestration: rules that route, suppress, or enrich incoming events before they become (or don't become) an incident").
- **Auto-pause incident notifications for transient alerts**: the service waits to see if a flapping alert (alert that fires, then clears quickly, then fires again) resolves on its own before notifying anyone ([Configurable Service Settings](https://Support.pagerduty.com/main/docs/configurable-service-settings)).

Deduplication (collapsing exact repeats) is handled with a **dedup key** — a fingerprint field on the event; a consistent dedup key prevents the same source from creating duplicate incidents (operational rule #5 in the [chrishuffman5 skill](https://github.com/chrishuffman5/domain-expert/blob/HEAD/./plugins/monitoring/skills/pagerduty/SKILL.md)).

Incident lifecycle states are a small fixed enum: **triggered → acknowledged → resolved** ([Escalation Policy Basics](https://support.PagerDuty.com/main/docs/escalation-policies)). Acknowledging stops escalation and pauses notifications but does *not* resolve; an acknowledged incident that re-triggers resumes the escalation policy. An incident also resolves automatically when all its alerts resolve, and resolving an incident auto-resolves its triggered alerts.

Urgency is the attention throttle: each incident has **high urgency** (phone call + SMS) or **low urgency** (email + push notification). Urgency can be dynamic — e.g., low urgency outside support hours — and PagerDuty supports "raise urgency of unacknowledged incidents to high when support hours start" so overnight items surface in the morning instead of being silently buried ([dev.to — Better Sleep with PagerDuty](https://dev.to/pdcommunity/better-sleep-with-pagerduty-dynamic-notifications-and-support-hours-4jkp)).

## 2. Compression into attention units

The **incident** is the unit of attention — a container that aggregates alerts, responders, notes, and a conference bridge. On the incident's detail page, the **Alerts tab** shows how many alerts are grouped and their statuses (e.g., two alerts: one triggered, one resolved), with a "Grouping Now" label when grouping is active ([Intelligent Alert Grouping](https://Support.pagerduty.com/main/docs/intelligent-alert-grouping)).

What the human sees first is governed by **priority**: assigning a priority defines impact and gives "common language"; priorities color-code incidents and **move the most critical ones to the top of the incident dashboard view** ([community.pagerduty.com — Responding to Incidents](https://community.pagerduty.com/pagerduty-user-onboarding-13/responding-to-incidents-in-pagerduty-372)). Routing is by service ownership: one service per team-owned component, each service wired to an escalation policy (skill rule #1: avoid monolithic "all alerts" services).

The escalation policy itself is the attention machine: an **ordered list of who gets notified and after how long** if the previous level doesn't acknowledge ([Escalation Policy Basics](https://support.PagerDuty.com/main/docs/escalation-policies)). Each rule has an "escalates after ___ min" timeout — the time a responder has to act before the next rule fires. Key behaviors: the first rule's person is **always notified the moment an incident triggers** (no delay setting exists for the first notification); if nobody acknowledges, the incident walks down the rules automatically; a policy with only one level and no fallback means an unavailable on-call = **silently dropped incident** ([chrishuffman5 skill](https://github.com/chrishuffman5/domain-expert/blob/HEAD/./plugins/monitoring/skills/pagerduty/SKILL.md)). PagerDuty's own ops team recommends a **5-minute escalation timeout** and always having a backup schedule ([pagerduty/incident-response-docs](https://github.com/pagerduty/incident-response-docs/blob/HEAD/docs/oncall/being_oncall.md)).

## 3. Progressive disclosure

- **Top layer — incident dashboard**: the incidents list (status badge, urgency, title, service, assignee, created time per the third-party dashboard PRD at [github.com/nyg/pagerduty](https://github.com/nyg/pagerduty/issues/1) — could not verify exact columns from official docs; the official doc confirms priority color-coding and critical-first ordering).
- **Detail layer — incident page**: Alerts tab (grouped alert count + statuses), responders, response plays (auto-attached bridge, added responders, status-update template), notes. The grouping is exposed, not hidden: **"Alert grouping details"** shows which grouping method is in effect, when grouping started, and the conditions under which grouping stops ([Intelligent Alert Grouping](https://Support.pagerduty.com/main/docs/intelligent-alert-grouping)).
- **Raw layer — alert payloads**: individual alerts with their Common Event Format (PD-CEF) fields and custom details — the fields the grouping model analyzes.
- Monitoring tools integrate via the **Events API v2** (trigger/acknowledge/resolve events — [dev.to PagerDuty API guide](https://dev.to/zuplo/pagerduty-api-essentials-a-guide-19ha)), which is the rawest machine layer.

## 4. Trust in automation

- **Provenance of grouping decisions**: the "Alert grouping details" panel exposes the method, start time, and stop conditions of the grouping on that incident — the automation's reasoning is inspectable per-incident ([Intelligent Alert Grouping](https://Support.pagerduty.com/main/docs/intelligent-alert-grouping)).
- **Human feedback trains the model**: "The best way for the algorithm to learn and adapt is to manually merge related incidents and to manually move alerts to a different incident" — but *only* manual merges/unmerges in the web app influence the learner; API merges do not ([Intelligent Alert Grouping](https://Support.pagerduty.com/main/docs/intelligent-alert-grouping)). Corrections are first-class inputs, not hidden overrides.
- **Undo/reversibility**: acknowledge → snooze (hides notifications for a chosen period) → unacknowledge returns the incident to triggered state and restarts the notification process; resolving an incident can be undone only by the monitoring source re-triggering ([community.pagerduty.com](https://community.pagerduty.com/pagerduty-user-onboarding-13/responding-to-incidents-in-pagerduty-372)).
- **Audit trail**: per-incident notes create a running timeline ("every significant finding, every action taken, every hypothesis tested should go into the incident notes") used both for live coordination and post-incident review ([Medium — PagerDuty Deep Dive](https://medium.com/@code.chandrashekhar/pagerduty-deep-dive-complete-guide-with-real-time-practical-approach-81010d0b579f)). For governance, **Insights** reports (Incident Activity, Service Performance, Responder, Team, Escalation Policy, Business Impact) track MTTA/MTTR — though analytics data is available "typically within 24 hours," i.e., not realtime ([Insights](https://support.pagerduty.com/main/docs/insights)).
- Caution the docs themselves carry: over-aggressive grouping can mask distinct issues (e.g., "database connection lost" lumped with "cache miss spike"), and the grouping algorithm "takes several factors into consideration, which makes tracing specific grouping decisions difficult" ([Intelligent Alert Grouping](https://Support.pagerduty.com/main/docs/intelligent-alert-grouping); also [dev.to intelligent incident management](https://dev.to/igarakh/intelligent-incident-management-how-pagerduty-aiops-incidentio-ai-and-mabl-are-revolutionising-nel)).

## 5. Realtime without disorientation

PagerDuty's realtime discipline is **notification-shaped, not list-shaped**: the web list behavior (whether rows jump on live updates) could not be verified from official docs — see Honest Gaps. What is documented is a carefully engineered notification ladder:
- Users define **notification rules per urgency**: an ordered sequence of contact methods each with a delay — e.g., push immediately, phone after 1 min, Slack after 2 min, email after 3 min, SMS after 4 min. PagerDuty recommends a short delay between rules so you are not hit on all channels at once ([Notification Rules](https://support.pagerduty.com/main/docs/notification-rules)).
- Push notifications are called out as **the highest-reliability delivery method** (survives a third-party SMS/voice carrier outage).
- PagerDuty's own ops team practice: push + email first, then phone/SMS each minute until escalation; "if you don't pick up by the 3rd time, it's unlikely you are able to respond, and the incident will get escalated away from you" ([pagerduty/incident-response-docs](https://github.com/pagerduty/incident-response-docs/blob/HEAD/docs/oncall/being_oncall.md)).
- Community practice: separate notification rules for state changes (acknowledged/resolved/escalated) and for going on-call (1-day and 1-hour advance reminders); getting notified *on resolution* is recommended so the engineer knows "action on my part is no longer needed" ([community.pagerduty.com — pager notification opinions](https://community.pagerduty.com/pagerduty-user-onboarding-13/some-opinions-on-how-should-i-set-up-my-pager-notifications-398)).
- The system also produces **On-Call Readiness Reports** that audit whether every team member has working contact methods configured ([pagerduty/goingoncall-docs](https://github.com/pagerduty/goingoncall-docs/blob/HEAD/docs/next_steps.md)) — readiness itself is monitored.

## 6. STEAL vs AVOID

**STEAL:**
- *Rolling-window grouping with per-incident provenance*: folding thousands of alerts into incidents with a visible "why grouped" panel is the single best-studied mechanism for list-collapse. The recommended window computed from the service's own alert cadence is a self-calibrating parameter.
- *Explicit escalation ladder with a no-silent-drop rule*: every attention unit must have a fallback chain; a single-level policy is a documented failure mode. The 5-minute acknowledge timeout is an empirical norm from PagerDuty's own ops team.
- *Urgency as a two-tier interrupt system*: high urgency = phone/SMS wake-the-human; low urgency = email/push. The 3-AM lesson: the system decides the interrupt level per incident, and it can raise urgency automatically at support-hours start so nothing rots overnight.
- *Human corrections as model training data*: merge/unmerge in the UI retrains the grouper. Corrections improve the automation instead of just fighting it.
- *Notify on resolution*: the engineer who was paged gets told "stand down." At 3 AM, knowing you are no longer needed is as valuable as the page itself.
- *Transient-alert auto-pause*: flapping signals get held briefly before anyone is woken — the cheapest possible fatigue filter.

**AVOID:**
- *Black-box grouping without per-incident explanation*: PagerDuty itself admits tracing specific grouping decisions is difficult. At 3 AM, an engineer who cannot see *why* 400 alerts became one incident will either over-trust it (miss the masked distinct issue) or ignore it entirely.
- *Insights latency for operational decisions*: the governance dashboards refresh on ~24h pipeline cycles. Anything the console shows for live triage must be genuinely realtime — analytics pipelines are for mornings, not midnights.
- *Single-level escalation*: never let an attention unit have exactly one human with no fallback. This is a documented silent-drop mode.

---
# PART 2 — OPSGENIE

> Context: Atlassian ended new Opsgenie sales on **June 4, 2025** and will shut the service down completely on **April 5, 2027** (login, REST APIs, integrations, mobile app all stop; unmigrated data deleted), folding alerting into Jira Service Management ([JAMS Scheduler](https://jamsscheduler.com/resources/blog/opsgenie-migration-test-before-you-cut-over); [Eficode](https://www.eficode.com/insights/blog/opsgenies-transition-into-jira-service-management-and-compass)). The mechanisms below remain the industry's longest-running worked example of alerting ergonomics — study the mechanisms, not the product's future.

## 1. Avoiding the infinite list

Opsgenie's central mechanism is **alert de-duplication by alias**: the **alias** is a user-defined unique identifier for open alerts, and **there can be at most one open alert with the same alias at any time**. If a source tries to create a new alert while an open alert with the same alias exists, the existing alert's **Count** value is incremented instead of creating another alert ([Atlassian Support — What is alert de-duplication?](http://support.atlassian.com/opsgenie/docs/what-is-alert-de-duplication/)). De-duplication works across integrations — the alias only needs to match. The Count property and per-event logging stop after 100 occurrences, but de-duplication itself continues indefinitely while the alert stays open. So a chatty monitor firing 500 times for one outage produces one alert row with Count climbing, not 500 rows.

Two policy layers sit around dedup ([Atlassian Support — Create and manage team policies](http://support.atlassian.com/opsgenie/docs/create-and-manage-team-alert-policies/); [docs.opsgenie.com — alert policies](https://docs.opsgenie.com/docs/alert-policies)):
- **Alert policies** transform alerts on creation: set priority, add responders/teams, add/remove tags, append a runbook URL to the description, or **delay/suppress**. Conditions match on tags, integration, message regex, teams, priority, custom properties (per the [opsgenie-policies skill](https://github.com/ice-962464/codex-skill-library/blob/HEAD/opsgenie-policies/SKILL.md)).
- **Notification policies** control the *notification flow* without touching the alert: **delay** notifications (e.g., "delay unless the de-duplication count reaches 5" or "delay unless the alert occurred N times in a time interval" — useful for bursty alerts that self-resolve), **suppress** notifications for known-noisy classes, **auto-close** alerts a specified time after their last occurrence (dedup postpones the auto-close clock), and **auto-restart** notifications — re-fire the notification flow if the alert is still open after N minutes, up to 20 repeats per alert ([docs.opsgenie.com](https://docs.opsgenie.com/docs/alert-policies)).
- **Maintenance policies** can enable/disable integrations or policies inside a time window (e.g., suppress all non-critical notifications during a planned deploy window) ([Atlassian Community](https://community.atlassian.com/forums/Opsgenie-articles/How-to-suppress-Alert-Creation-and-or-Alert-Notifications-in/ba-p/2389528)).

Routing is split from escalation: **routing rules** decide which schedule/escalation policy an alert lands on (including "route to no one"), while **escalation policies** define the if-not-acknowledged ladder — rules execute in order, each with a condition (`if-not-acked`), notify type, and delay; a repeat block can re-cycle the whole ladder with a wait interval ([Atlassian Community — routing rules deep dive](https://community.atlassian.com/forums/Opsgenie-articles/A-deep-dive-on-Routing-Rules/ba-p/1330474); [devops-study-hub escalation example](https://github.com/igalhub/devops-study-hub/blob/HEAD/content/opsgenie/incident-workflow.md)).

## 2. Compression into attention units

The **alert** is the unit of attention. Compression levers:
- **Alias design is a first-class discipline**: the operational guidance is to build the alias as a stable fingerprint (`service + check + resource`), with enough uniqueness to avoid collapsing distinct failures ([opsgenie-policies skill](https://github.com/ice-962464/codex-skill-library/blob/HEAD/opsgenie-policies/SKILL.md)). Alias quality *is* the dedup quality — a design decision, not a tuning knob.
- **Priority P1–P5 maps to notification behavior**: P1 = immediate primary; P2 = short delay OK; P3 = business hours or low-urgency; P4–P5 = notify lightly or ticket only (skill normalization table).
- **Escalation ladders auto-widen the audience**: e.g., notify on-call now → if not acked in 5 min notify team lead → if not acked in 10 min notify the whole team → repeat; the repeat block means an unacknowledged alert never silently dies, it cycles ([devops-study-hub](https://github.com/igalhub/devops-study-hub/blob/HEAD/content/opsgenie/incident-workflow.md)).

## 3. Progressive disclosure

- **Top layer — alert list**: open alerts, each showing message, priority, count, responders/teams, age.
- **Detail layer — alert detail**: message, description, tags, responders, the **activity log** (every create/dedup/ack/close/escalation/policy action is logged), and the dedup **Count**.
- **Silent-detail layer — notes**: when alerts are deduplicated, Opsgenie can be configured to **append a note with the new message/description** on each dedup. Critically, **notes don't trigger notifications** — new information accumulates on the alert without re-paging anyone ([Atlassian Community — Managing Alert Deduplication and Notes](https://community.atlassian.com/forums/Opsgenie-articles/Managing-Alert-Deduplication-and-Notes-in-Opsgenie/ba-p/2865144)). This is progressive disclosure of *information freshness*: the alert row stays quiet, but opening it reveals everything that arrived since.
- **Raw layer**: integration payloads and the ordered action list per integration (create/close/ack actions evaluated in order against each incoming payload — [Atlassian Community — Auto Close Dynatrace alert](https://community.atlassian.com/forums/Opsgenie-questions/Auto-Close-Dynatrace-alert/qaq-p/1957126)).

## 4. Trust in automation

- **Every dedup event is written to the alert's activity log** — the automation's work is visible as a first-class log entry, not a hidden counter ([Atlassian Support — de-duplication](http://support.atlassian.com/opsgenie/docs/what-is-alert-de-duplication/)).
- **Alias is user-controlled and deterministic**: unlike ML grouping, an Opsgenie user can read the alias template on the integration and predict exactly what will dedup with what. The failure mode is also legible: two teams' alerts can silently dedup against each other *across team boundaries* because dedup ignores teams — a known sharp edge that forces deliberate alias-prefix design ([prometheus/alertmanager#3639](https://github.com/prometheus/alertmanager/issues/3639)).
- **Alert policies are declarative and ordered**: conditions + actions evaluated top-to-bottom; the docs show an example policy's coverage "within the alert activity log" ([docs.opsgenie.com](https://docs.opsgenie.com/docs/alert-policies)). Operators can read the policy list and simulate outcomes.
- **Undo/reversibility**: acknowledge → unacknowledge restarts the notification flow; alerts can be manually closed/reopened; auto-restart behaves exactly like an executed "unacknowledge" ([docs.opsgenie.com](https://docs.opsgenie.com/docs/alert-policies)).
- **Action policies** run remediation (restart an instance, bump capacity) without waiting for a human — automation that *acts*, with the action logged ([Atlassian Support — team policies](http://support.atlassian.com/opsgenie/docs/create-and-manage-team-alert-policies/)).

## 5. Realtime without disorientation

Opsgenie's realtime discipline is **notification-shaped** around one philosophy, stated plainly in a community support thread: "OpsGenie is all about expecting someone to respond to an alert when it happens. Once a person is notified, if that person doesn't respond, the OG follows the escalation rules" ([Atlassian Community — Deduping Notification Policy](https://community.atlassian.com/forums/Opsgenie-questions/Deduping-Notification-Policy/qaq-p/1457065)). Concretely:
- A **delayed first notification** is a supported pattern: notify on the first occurrence, then suppress subsequent ones unless a threshold (dedup count / occurrence rate) is breached — the alert stays open and accumulating, the human gets one tap and then silence unless things get worse.
- **Silent updates**: dedup-driven notes never trigger notifications — the list and the pager both stay calm while information accrues ([Atlassian Community — Managing Alert Deduplication and Notes](https://community.atlassian.com/forums/Opsgenie-articles/Managing-Alert-Deduplication-and-Notes-in-Opsgenie/ba-p/2865144)).
- **Auto-restart as anti-rot**: if an alert is still open after N minutes (even acknowledged), the notification flow restarts — the system re-asserts attention on stale-but-open items instead of letting them scroll away ([docs.opsgenie.com](https://docs.opsgenie.com/docs/alert-policies)). This is the mechanism that prevents "open but forgotten."
- I could not verify mobile-app-specific behaviors (quiet hours, critical-override sound discipline) from the fetched sources — see Honest Gaps.

## 6. STEAL vs AVOID

**STEAL:**
- *Deterministic dedup by user-designed fingerprint*: the alias is the single most legible compression mechanism in this study — exact, predictable, debuggable. The operational discipline of designing the alias as `service + check + resource` generalizes to any "storm aggregator."
- *Silent note-appending on dedup*: new info lands on the record without a new interrupt. This is the correct answer to "how do I keep the human informed without re-paging them" — a pattern worth stealing wholesale.
- *Delay-unless-threshold notification policies*: one notification for the first occurrence, silence unless it gets worse. This is precisely calibrated for the 3-AM scenario — the first page earns the wake-up; repeats don't.
- *Auto-restart / anti-rot re-notification*: an acknowledged-but-unresolved alert re-surfaces after N minutes. Acknowledgment must never equal disappearance.
- *Separation of alert policy (transform the record) from notification policy (transform the interrupt)*: two independent levers, independently auditable.

**AVOID:**
- *Cross-boundary silent dedup*: Opsgenie's dedup ignores team boundaries — two teams can silently swallow each other's alerts. Any shared-fingerprint scheme needs explicit scoping, or one tenant's storm blinds another tenant's on-call.
- *Count/logging cutoff at 100*: the count stops incrementing and event logging stops after 100 dedups while dedup continues. At 3 AM, "Count: 100+" with no further per-event log is exactly when you most want to know the arrival *rate* — cap the counter display, never the evidence.
- *Building on a sunsetting platform*: Opsgenie's mechanisms are proven, but the product dies April 2027. Steal the patterns; don't copy integration code or depend on its APIs.

---
# PART 3 — LINEAR

## 1. Avoiding the infinite list

Linear's central mechanism is **Triage as a first-class status, not a view**: "Triage is a special inbox for your team. When an issue is created by integration or by a workspace member not belonging to your specific Linear team, it will appear here… an opportunity to review, update, and prioritize issues before they are added to your team's workflow" ([Linear Docs — Triage](https://linear.app/docs/triage)). The key structural move: **issues in Triage do not show up in the backlog or active issue lists** ([Linear Docs — How to manage unplanned work](https://linear.app/docs/triage-manage-unplanned-work)). Unreviewed work is quarantined in exactly one place ("exactly one front door… nothing that enters the system is silently lost" — [foolscap linear-review](https://github.com/davidcpage/foolscap/blob/HEAD/docs/linear-review.md)), so the backlog stays a trusted, groomed list and the incoming firehose never pollutes it.

Triage decisions are keyboard-speed micro-actions: **`1` accept** (into the workflow), **`3` decline** (cancel with optional reason), **`2`/`MM` merge duplicate** (folds into a canonical issue, moving attachments/customer requests over), **`H` snooze** (hide until a chosen time or until new activity) ([foolscap linear-review](https://github.com/davidcpage/foolscap/blob/HEAD/docs/linear-review.md); triage actions also in [markdown-matters research](https://github.com/srobinson/markdown-matters/blob/HEAD/research/task-management-2026/linear/01-core-features-workflow.md)). The whole "decide what to do with this" loop is a few keystrokes per item — throughput, not just organization.

Two automation layers pre-filter the queue ([Linear Docs — Large & scaling companies](https://linear.app/docs/how-to-use-linear-large-scaling-companies); [tvararu/peon research](https://github.com/tvararu/peon/blob/HEAD/docs/archive/2026-09-25-dev-factory-research.md)):
- **Triage rules** (deterministic): filterable conditions that auto-set team, status, assignee, label, project, priority for incoming issues — predictable intake is routed automatically and only ambiguous items need a human.
- **Triage Intelligence** (LLM, Business+): analyzes incoming issues against the backlog and suggests team routing, project, assignee, labels, **duplicate detection**, and related-issue linking. Users can **accept/decline/inspect each suggestion with reasoning shown**; auto-apply mode exists for high-confidence suggestions (latency 1–4 min).

Beyond Triage, the list never grows unbounded because **views are saved filters, not folders**: "Views in Linear are saved filters" — personal dashboards ("All my open bugs"), role-based lists ("Open QA issues"), team boards ("Bugs reported this week"), each a filter+group+sort+display bundle that can be shared and pinned to the sidebar ([morgen.so Linear Guide](https://www.morgen.so/blog-posts/linear-project-management)). Advanced filters support nested AND/OR groups, natural-language filter phrases, and the filtered state is persisted in the URL so it is shareable ([modbot Linear UI findings](https://github.com/modbot/modbot/blob/HEAD/.agent/research/2026-09-16-linear-ui-findings.md)). **Cycles** (time-boxed sprints with automatic rollover of unfinished work) bound the *active* list temporally — what isn't done rolls forward automatically rather than accumulating as an ever-longer "current" list.

## 2. Compression into attention units

The **issue** is the atomic unit of attention; everything flows through it — inbox, board, cycle, project view, search — without being re-entered ("one object flows through every surface… every view is a lens on the same object" — [puntakit product research](https://github.com/suriyong1993/puntakit-kalasin/blob/HEAD/docs/PUNTAKIT_PRODUCT_RESEARCH.md)).

What the human sees first is driven by:
- **"My Issues"**: tasks assigned to you, grouped by status or cycle — the personal attention queue ([morgen.so guide](https://www.morgen.so/blog-posts/linear-project-management)).
- **Priority** (Urgent/High/Medium/Low) as a first-class field, plus labels and cycles.
- **Triage responsibility**: rotations (linkable to on-call schedules) assigning a rotating owner to monitor the triage queue, with **SLAs** on time-sensitive issues and SLA notifications 24h before breach and at breach ([Linear Docs — Large & scaling](https://linear.app/docs/how-to-use-linear-large-scaling-companies); [polaris inbox research](https://github.com/mcpeixoto/polaris/blob/HEAD/docs/01-features/10-inbox-notifications-my-issues.md)).

## 3. Progressive disclosure

- **Top layer — list rows**: dense issue rows showing key properties (status, priority, labels, assignee, cycle); board/list layouts switchable per view.
- **Detail layer — issue page**: full description (markdown), comments, sub-issues, relations (blocks/blocked-by, duplicates), and the **activity history** — the changelog notes Linear surfaces "the previous priority in a tooltip over issue priority changes," i.e., per-field history ([Linear changelog 2024-08-23](https://linear.app/changelog/2024-08-23-slack-channel-notifications-for-custom-views)).
- **Raw layer**: every property change, comment edit, and relation change is recorded on the issue; integrations (GitHub PRs auto-updating issue status) write into the same record ([morgen.so guide](https://www.morgen.so/blog-posts/linear-project-management)).
- **Command bar (`Cmd+K`)** is the universal escape hatch — "do any action by name, from anywhere, with fuzzy search" — so disclosure is also *actionable*: any layer can jump to any other layer or command without navigating the hierarchy ([foolscap linear-review](https://github.com/davidcpage/foolscap/blob/HEAD/docs/linear-review.md)).

## 4. Trust in automation

- **Triage Intelligence shows its reasoning**: each suggestion can be inspected before accepting; duplicate detection surfaces the *candidate* duplicates for human confirmation rather than auto-merging (per [tvararu/peon](https://github.com/tvararu/peon/blob/HEAD/docs/archive/2026-09-25-dev-factory-research.md), suggestions "can be auto-applied per property" — auto-apply is opt-in per property, defaulting to human confirmation).
- **Duplicate merge is lossless and explicit**: attachments, customer requests, and linked items transfer to the canonical issue; the duplicate is marked Canceled — the merge is an auditable action, and the canonical issue accumulates the evidence ([markdown-matters research](https://github.com/srobinson/markdown-matters/blob/HEAD/research/task-management-2026/linear/01-core-features-workflow.md)).
- **Issue history**: every change to the issue is a history entry with actor and timestamp (the [linear-cli](https://github.com/nesszer/linear-cli) exposes `issues get --history` as an "activity timeline"; changelog confirms per-field history tooltips).
- **Undo/reversibility**: statuses move freely backward and forward; snoozed issues return on new activity; declined (canceled) issues are restorable. The sync engine's optimistic updates are applied with rollback semantics on failure (see §5).

## 5. Realtime without disorientation

Linear's realtime story is the **sync engine**: the client treats the browser's **IndexedDB** (the browser's built-in local database) as a real database. "Every change happens locally first, then in the background uses GraphQL (a way for the app to request exactly the data it needs) for mutations and Websockets (a persistent two-way connection) for sync" ([bytemash — Linear rabbit hole](https://bytemash.net/posts/i-went-down-the-linear-rabbit-hole/); same in [jyecusch's notes](https://github.com/jyecusch/blog/blob/HEAD/src/data/blog/i-went-down-the-linear-rabbit-hole.md) citing a CTO-endorsed reverse-engineering of the sync protocol at [marknotfound.com](https://marknotfound.com/posts/reverse-engineering-linears-sync-magic/)). Consequences:
- **Optimistic updates**: the UI updates instantly from the local store; the server syncs in the background and rolls back on conflict. The network is never on the critical path of the user's action.
- **No list-jump by design**: because every client holds a local replica and merges deltas, remote changes arrive as data updates to the local store rather than full list re-fetches — rows update in place instead of the list re-rendering and losing scroll position (general sync-engine property described at [dev.to — Are Sync Engines the Future](https://dev.to/isaachagoel/are-sync-engines-the-future-of-web-applications-1bbi) and [0xgosu — Why Linear Feels Fast](https://0xgosu.dev/blog/linear-local-first-speed/): "keep re-render scope proportional to the user's actual change").
- **Speed as a correctness property**: "A slow issue tracker changes how teams behave: they batch work, avoid cleanup, postpone triage, and leave context stale because touching the system costs too much" ([0xgosu](https://0xgosu.dev/blog/linear-local-first-speed/)).

Notification discipline ([polaris inbox/notifications research](https://github.com/mcpeixoto/polaris/blob/HEAD/docs/01-features/10-inbox-notifications-my-issues.md), third-party but detailed):
- The **Inbox is the single sink**: "You cannot choose what enters the Inbox — everything lands there, and other channels link back to the Inbox item." Desktop, mobile, and Slack notifications are realtime; **email has immediate or digest modes**, and digests are "delayed based on urgency/issue status, and are only sent if you haven't already read the Inbox notification" — the email is a backstop, not a duplicate.
- Notification *types* are **grouped** (e.g., "status changes" bundles completions, cancellations, urgent-priority changes, blocking-relationship changes) — you cannot subscribe to a single sub-event, which deliberately prevents notification-spam configuration.
- **Keyboard-first everything** (`G T` go-to-triage, `F` filters, `/` search, `C` create from anywhere) keeps triage throughput high without pointer-chasing.

## 6. STEAL vs AVOID

**STEAL:**
- *Triage as a quarantine status, not a filter*: unreviewed items live in exactly one inbox and are **excluded from all working lists** until a human (or deterministic rule) moves them. This is the mechanism that lets the main list stay trustworthy at scale. The one-keystroke accept/decline/merge/snooze loop is the throughput engine.
- *Snooze-until-activity*: hiding an item until a chosen time *or until new activity* — the item resurfaces precisely when there's new information. This beats fixed-time snooze at 3 AM.
- *One object, many lenses*: the issue is never re-entered across surfaces; every view (board, cycle, search, project) is a projection of the same record. Consistency without duplication.
- *Grouped notification types + digest-as-backstop*: notification categories are bundled so users can't build themselves a spam cannon; email digests only fire if the inbox item is still unread. The interrupt hierarchy (realtime → digest → nothing) is worth copying.
- *Optimistic local-first updates*: at 3 AM on a bad connection, the console must respond instantly to the exhausted engineer's actions and sync later — never block interaction on the network.

**AVOID:**
- *Keyboard-only throughput assumptions*: Linear's triage speed comes from keyboard-first power users. A 3-AM incident console will be used by exhausted people who may be on a phone, on a tablet in a datacenter, or half-asleep — every keyboard shortcut needs an equally fast pointer/touch path.
- *AI triage suggestions without visible reasoning*: Linear gets this right (reasoning shown, accept/decline per suggestion), but the pattern is easy to get wrong. Never auto-apply AI routing silently — the 1–4 minute latency of Triage Intelligence also means suggestions can arrive after the human already decided.
- *The "one front door" becoming a dumping ground*: Triage only works because teams actually work the queue (rotations, SLAs). A quarantine inbox with no ownership SLA just moves the infinite list one screen to the left.

---
# PART 4 — NOTION

## 1. Avoiding the infinite list

Notion's mechanism is **views as lenses over one database**: "A view helps you hide the different pieces of information you need by specifying the: properties to show or hide, the order that properties are listed, entries to show using filters, the way to sort those entries… Even though a view is not showing all your data, nothing is ever lost in your database. A view is merely hiding stuff that you don't want to see of that database at that time" ([Medium — Notion Databases: 10 Things I Needed To Learn](https://medium.com/@VaughanVanDyk/notion-databases-10-things-i-needed-to-learn-52873eb2618b)). One master database feeds many **linked views** (filtered per team/role) — "you keep a single source of truth, but each group sees only what they need" ([connex.digital](https://connex.digital/blog/how-to-build-a-master-task-database-in-notion-with-linked-views/)). Each database supports table, board (kanban grouped by select/status), timeline, calendar, list, and gallery layouts ([makeuseof guide](https://www.makeuseof.com/beginners-guide-using-databases-notion/)).

The practiced pattern is **time-scoped default views**: a "TODAY" view (Due Date = today), "THIS WEEK," and "OVERDUE" — three views that replace the whole daily workflow; "open TODAY in the morning… never feel overwhelmed by seeing everything at once" ([Medium — The One Notion Feature](https://medium.com/@SuzaanSayed/the-one-notion-feature-nobody-talks-about-changed-how-everything-works-a0b737808a05)). The principle: *the system brings relevant information forward automatically instead of waiting to be searched.*

Scale guardrails are real and documented: there is "a load limit placed in terms of the number of entries Notion loads at a time" per page view — large databases must use views to reduce how much loads at once ([Medium — 10 Things](https://medium.com/@VaughanVanDyk/notion-databases-10-things-i-needed-to-learn-52873eb2618b)). The API reveals the same philosophy at a different layer: queries paginate and are **capped at 10,000 results per query**, with an explicit `incomplete` status flag when the cap hits — the system tells you the result was cut off rather than silently truncating ([Notion Developers — Query a data source](https://developers.notion.com/reference/query-a-data-source)).

## 2. Compression into attention units

Notion's unit of attention is the **page** (a database row is also a page — structured metadata plus unstructured block content; "a database item was not reduced to a row: it remained a page that could hold open-ended block content" — [notion-product-design research](https://github.com/oreo992/skills/blob/HEAD/skills/notion-product-design/references/product-evolution.md)).

Attention routing mechanisms:
- **Inbox** (sidebar): "See all notifications, mentions, and work assignments in one place" ([morgen.so Notion guide](https://www.morgen.so/blog-posts/notion-tips-and-tricks)).
- **Home**: "View your most important pages and action items."
- **Favorites** (starred pages) and **@-mentions** in comments as the "this needs your eyes" signal.
- **Smart-filter views** (TODAY/OVERDUE) as role-agnostic attention queues — the database equivalent of a triage inbox, except the "routing" is just filter logic the user owns.

## 3. Progressive disclosure

- **Top layer — database row**: only the properties the view chooses to show; list view is the most condensed.
- **Detail layer — open row as page**: the full page with all blocks, comments, and properties — the record "remains a page," so drill-down is depth, not a different object ([notion-product-design research](https://github.com/oreo992/skills/blob/HEAD/skills/notion-product-design/references/product-evolution.md)).
- **Raw layer — version history**: every page keeps edit history (Free: 7 days; Plus: 30; Business/Enterprise: 90), accessible via `•••` → Page history; a version snapshot is captured roughly every ten minutes while editing and two minutes after stopping; **restoring creates a new version — it is non-destructive** ([cloudless.gr notion-wikis skill](https://github.com/heianxing/cloudless.gr/blob/HEAD/.claude/skills/notion-wikis/SKILL.md); [clonepartner](https://clonepartner.com/blog/how-to-preserve-version-history-in-knowledge-base-migrations)). Deleted pages sit in **Trash for 30 days** before permanent deletion, restorable by workspace owners.
- Notably, the rawest layers are *not* fully open: the public API exposes only the current state of pages (no revision-history endpoint), and version history "shows the main highlights" and "won't capture every single change" ([clonepartner](https://clonepartner.com/blog/how-to-preserve-version-history-in-knowledge-base-migrations)). Disclosure has a floor.

## 4. Trust in automation

- **Non-destructive everything**: restore creates a new version rather than overwriting; trash is a 30-day soft delete. The user can always walk backward — the system's reversibility is structural, not a feature toggle.
- **Version history with contributor info**: who changed what and when is visible per page; Enterprise adds a workspace-level **Audit Log API** ("User A edited Page B"), though it does not return the edit's content ([clonepartner](https://clonepartner.com/blog/how-to-preserve-version-history-in-knowledge-base-migrations)).
- **Optimistic editing with server validation**: your edit applies locally first, then the server "validates it against the 'before' and 'after' state for permissions and data correctness, and only then commits it" — the local experience is instant, but the commit is gated on a permission+correctness check ([insidethestack — How Notion Works](https://github.com/udaykumar-dhokia/insidethestack/blob/HEAD/content/articles/how-notion-works.mdx), summarizing Notion's official [data-model blog post](https://www.notion.com/blog/data-model-behind-notion)).
- **AI posture**: "The AI suggests, but the human decides, edits, and refines" — Notion's documented stance keeps the human as the commit authority on AI-generated content (reported in [Medium — Notion product strategy](https://medium.com/@takafumi.endo/notion-navigating-simplicity-scale-and-ai-in-modern-product-strategy-fb8cbb4834bf)). This is a trust contract: automation proposes, humans dispose.

## 5. Realtime without disorientation

Notion's realtime pipeline, per the [insidethestack teardown](https://github.com/udaykumar-dhokia/insidethestack/blob/HEAD/content/articles/how-notion-works.mdx) of Notion's engineering writing:
1. You type → applied **locally first, optimistically** (no spinner, no lag, survives Wi-Fi hiccups).
2. Sent to server → validated (before/after state, permissions) → committed.
3. Commit triggers background work: version-history snapshot scheduling, search indexing, and notification of **MessageStore** — "Notion's real-time updates service" — which pushes the change to other viewers of the page.

Conflict handling moved from last-write-wins to **CRDTs** (Conflict-free Replicated Data Types — data structures designed so two independently edited copies merge deterministically without losing data), specifically a Replicated Growable Array for text, extended with "text slices" and "search labels" to track moving content across block records ([YouTube — "One of the largest CRDT deployments at Notion"](https://www.youtube.com/watch?v=zdwyR6vbXWM)). This is the foundation of their offline editing story: edits queue locally and merge deterministically on reconnect — nobody's keystrokes are lost and nobody has to resolve a conflict dialog.

Scale context: blocks grew from 20B+ (2021) to 200B+ (2024), stored in sharded Postgres (480 logical shards, workspace_id as shard key) with a separate data lake for analytics — the realtime path and the analytics path are deliberately different systems ([plim research index](https://github.com/darylcecile/plim/blob/HEAD/research/notion-editor-architecture/00-source-index.md) summarizing Notion's [data lake blog](https://www.notion.com/blog/building-and-scaling-notions-data-lake)). I could not verify presence-UI specifics (colored cursors, avatar stacks) from fetched sources — see Honest Gaps.

## 6. STEAL vs AVOID

**STEAL:**
- *Views as shareable, ownable lenses*: the filter+sort+layout bundle that different roles reuse over one dataset. The "one master database, many linked views" pattern is the cleanest separation of *data* from *attention* in this study.
- *Non-destructive reversibility as a structural property*: restore-creates-a-version, 30-day trash. At 3 AM, the engineer's biggest fear is making things worse; a console where every action is walk-backable changes behavior — people act instead of freezing.
- *Apply-locally-first, validate-before-commit*: the interaction never waits on the network, but the commit is permission-checked against before/after state. Instant UI + gated truth.
- *Explicit truncation signaling*: the API's `incomplete` flag on capped queries. If the console ever caps a list, it must say so — silent truncation is how you miss the one alert that mattered.
- *Automation proposes, human disposes*: the AI-suggests/human-decides contract, stated as product policy rather than buried in UX.

**AVOID:**
- *Unbounded flexibility as a tax*: Notion's power is also its failure mode — infinite views, infinite nesting, "two views useful, fifteen a filing problem" ([notion-suite research](https://github.com/navysum/notion-suite)). A 3-AM console must be opinionated: a small set of curated lenses, not a view-builder.
- *Analytics and realtime on different clocks*: Notion deliberately split its data lake (minutes-to-hours latency) from its realtime path. Don't present slow aggregates as live truth — label the latency of every number.
- *The disclosure floor*: Notion's own history "won't capture every single change." Know and document what your audit trail *doesn't* capture — an incident console's audit gaps are a liability, not a footnote.

---

# HONEST GAPS — what I could not verify

1. **PagerDuty web-app list behavior on live updates** (does the incident list jump/reorder when new incidents arrive? are there pause/"N new" controls?): not described in the official docs I fetched. A third-party dashboard PRD ([github.com/nyg/pagerduty](https://github.com/nyg/pagerduty/issues/1)) describes polling with a countdown timer, but that is someone's clone, not PagerDuty's product. **Unverified.**
2. **PagerDuty in-incident live timeline tab**: the official docs confirm per-incident notes and grouping-details panels, and a third-party skill references an "IncidentEvent (timeline)" API object ([servosity skill](https://github.com/servosity/msp-skills/blob/HEAD/skills/pagerduty/guide.md)), but I did not fetch an official doc describing a realtime activity/timeline tab on the incident page. **Partially verified.**
3. **Opsgenie mobile-app sound/vibration/quiet-hours discipline**: notification policies and escalation are well documented; device-level interruption behavior (critical overrides, quiet hours) was not covered in fetched sources. **Unverified.**
4. **Linear presence / multi-user realtime cursors**: Linear's sync engine (local-first, optimistic, websocket sync) is well documented via CTO-endorsed teardowns ([marknotfound.com](https://marknotfound.com/posts/reverse-engineering-linears-sync-magic/); [bytemash](https://bytemash.net/posts/i-went-down-the-linear-rabbit-hole/)), but I did not verify specifics of how simultaneous editors are visualized. **Unverified.**
5. **Linear notification Inbox internals**: the polaris research doc ([10-inbox-notifications-my-issues.md](https://github.com/mcpeixoto/polaris/blob/HEAD/docs/01-features/10-inbox-notifications-my-issues.md)) is detailed but third-party; Linear's official docs confirm the Inbox exists and triage basics, but digest-timing rules and grouped-type behavior come from the third-party source. **Partially verified — treat digest specifics as third-party.**
6. **Notion presence UI** (live cursors, avatar stacks, "who's viewing"): the CRDT/optimistic/message-store pipeline is documented via engineering teardowns; the visual presence layer was not verified from fetched sources. **Unverified.**
7. **Notion "load limit" exact numbers**: the per-page load limit is confirmed as a concept ([Medium — 10 Things](https://medium.com/@VaughanVanDyk/notion-databases-10-things-i-needed-to-learn-52873eb2618b)); exact row counts were not stated in any fetched source. **Unverified.**
8. **Opsgenie heartbeat monitoring**: Opsgenie historically offered heartbeat monitoring (dead-man's-switch for cron/monitoring sources), but I did not fetch a source confirming its current behavior. **Unverified — omitted from the body rather than asserted.**
9. **Linear Triage Intelligence latency/accuracy specifics** beyond "1–4 min" and plan gating: from a third-party research repo ([tvararu/peon](https://github.com/tvararu/peon/blob/HEAD/docs/archive/2026-09-25-dev-factory-research.md)). Official docs confirm the feature and the accept/decline/inspect model. **Partially verified.**

**Source-quality note:** where official docs existed (PagerDuty support site, Atlassian Support, linear.app/docs, Notion engineering blog, Notion developers), I used them. Where only third-party teardowns/research repos existed (sync-engine internals, notification internals), I used them and marked the confidence above. No claim in this report is drawn from the pasted "principal framework" — all observations come from the tools studied.
