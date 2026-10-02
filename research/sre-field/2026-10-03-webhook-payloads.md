# 2026-10-03 — Vendor webhook payload shapes: PagerDuty v3, Opsgenie, Alertmanager

**Question:** What exactly do PagerDuty (outbound webhooks v3), Opsgenie (alert
callbacks), and Alertmanager (webhook receiver) POST to a receiver? Field by
field: what identifies the incident, severity, urgency, the dedup key, and the
links — and which fields are reliable vs optional?

**Method:** Live vendor docs fetched 2026-10-03. Every claim cites URL + access
date. This is the *vendor → Sentinel* direction (these vendors emit; our
receiver parses). For PagerDuty, the reverse direction (Events API v2 —
what our forwarder would emit) is documented separately at the end of §1
because the `dedup_key` semantics live there.

---

## 1. PagerDuty — Webhooks v3 (outbound)

Primary source: `github.com/pagerduty/developer-docs` —
`docs/webhooks/01-Overview.md`
(https://github.com/pagerduty/developer-docs/blob/HEAD/docs/webhooks/01-Overview.md,
accessed 2026-10-03). Event catalogue cross-checked against
`support.pagerduty.com/docs/webhooks` (https://support.pagerduty.com/docs/webhooks,
accessed 2026-10-03).

### 1.1 Transport

- HTTP POST, JSON body. One payload = **one event** (`{"event": {...}}`).
- Identifying headers: `User-Agent: PagerDuty-Webhook/V3.0`
  (V2 sent `PagerDuty-Webhook/V2.0`).
- V3-only headers: `x-webhook-subscription` (the subscription id),
  `x-pagerduty-signature` (HMAC-SHA256 of the body keyed by the subscription
  secret). **This is the only vendor of the three with a native signing
  scheme** — our receiver MUST verify it.
- Delivery: subscription-scoped by service/team/account filter; the subscriber
  chooses the event-type list (e.g. `incident.triggered`,
  `incident.acknowledged`, `incident.resolved`, `incident.reopened`,
  `incident.escalated`, `incident.priority_updated`, `incident.reassigned`,
  `incident.unacknowledged`).

### 1.2 Envelope (`event.*`) — identical for every v3 event type

| Field | Type | Reliability | Meaning |
|---|---|---|---|
| `event.id` | string (UUID) | always | Unique id of *this event*. Idempotency key for redelivery dedup. |
| `event.event_type` | string | always | e.g. `incident.triggered`. V3 names are past-tense (`triggered`); V1/V2 used `trigger`. |
| `event.resource_type` | string | always | `incident` or `service` (the root resource). |
| `event.occurred_at` | ISO 8601 datetime | always | When the event happened (server-side). |
| `event.agent` | resource reference or `null` | always present, often `null` | Who/what initiated it. `null` = automation, not a person. |
| `event.client` | object or `null` | optional | Where the event was triggered (`{"name": "PagerDuty"}`). |
| `event.data` | object | always | The resource payload; shape keyed by `event_type` (see 1.3). |

### 1.3 Incident payload (`event.data` when `resource_type == "incident"`)

This is the shape our receiver must parse for the pre-page gate. Fields from
the vendor's own `incident` example in the docs:

| Field | Reliability | Meaning / notes |
|---|---|---|
| `data.id` | always | Canonical incident id, e.g. `PGR0VU2`. **Identity #1.** |
| `data.type` | always | `"incident"`. |
| `data.self` | always | API URL of the incident (`https://api.pagerduty.com/incidents/PGR0VU2`). |
| `data.html_url` | always | Human link (`https://acme.pagerduty.com/incidents/PGR0VU2`). **Link #1 — keep for audit explorer.** |
| `data.number` | always | Account-scoped incident number (int). Human-friendly, not globally unique. |
| `data.status` | always | `triggered` / `acknowledged` / `resolved`. **Lifecycle state.** |
| `data.incident_key` | usually | **The dedup key.** Carried over from the Events API trigger that created the incident (e.g. `d3640fbd41094207a1c11e58e46b1662`). Same key + open incident ⇒ new alerts append to the incident log instead of creating a new incident (see 1.5). May be absent for manually created incidents. |
| `data.title` | always | Incident title. |
| `data.created_at` | always | ISO 8601. |
| `data.reopened_at` | situational | Set only when the incident was reopened. Presence ⇒ this incident has a prior episode — **our correlator must treat a `reopened` event after a `resolved` as episode linkage, not a duplicate.** |
| `data.incident_type` | optional | `{"name": "major"}` — account-configured incident types. |
| `data.service` | always | Service reference: `id`, `self`, `html_url`, `summary`, `type: "service_reference"`. **Identity #2 (scope).** |
| `data.assignees` | usually | Array of user references. Empty at trigger time is possible. |
| `data.escalation_policy` | always | Policy reference (`id`, `summary`, `html_url`). |
| `data.teams` | always | Array of team references. May be empty. |
| `data.priority` | usually | Priority reference: `id`, `summary` (e.g. `"P1"`), `self`. **Severity #1.** Account-configurable names (P1–P5 typical, not guaranteed); `summary` is the human string, `id` (e.g. `PSO75BM`) is the stable key. May be absent if the account doesn't use priorities. |
| `data.urgency` | always | `"high"` or `"low"`. **Severity #2 / routing.** High ⇒ push/SMS/phone; low ⇒ quiet channel. A user with only email rules is effectively never paged (practitioner note, prior research). |
| `data.conference_bridge` | optional | `{conference_number, conference_url}`. Often absent. |
| `data.resolve_reason` | situational | `null` unless `status == "resolved"`. |

What is NOT in the webhook incident payload: the underlying *alerts* list
(alerts are a separate REST resource, `GET /incidents/{id}/alerts`). The
webhook tells you the incident changed; the alert detail needs an API call.
**Implication: our receiver cannot do alert-level dedup from the PD webhook
alone — `incident_key` + `data.id` are the grouping primitives.**

### 1.4 Version differences (v1 / v2 / v3)

- V1: end-of-life October 2022. V2: end-of-support October 31, 2022 (existing
  integrations keep working; no new features, no bug fixes). V3 is current.
  (developer-docs 01-Overview.md, "Deprecated Versions".)
- Event naming: `trigger`/`acknowledge`/`resolve`/`escalate` (v1/v2) vs
  `triggered`/`acknowledged`/`resolved`/`escalated` (v3).
  (support.pagerduty.com/docs/webhooks.)
- Headers: `User-Agent: PagerDuty-Webhook/V2.0` vs `/V3.0`;
  `x-webhook-subscription` and `x-pagerduty-signature` are **v3-only**.
- Event catalogue: v3 adds `priority_updated`, `reassigned`, `reopened`,
  `responder.added`, `responder.replied`, `status_update_published`,
  `conference_bridge.updated`, `custom_field_values.updated`,
  `workflow.started/completed`, `incident_type.changed`, `service_updated`.
  (Both vendor sources agree on the table.)
- Subscription model (v3): explicit event list + service/team/account filter
  via the Webhook Subscriptions API. Our receiver will see only what the
  subscription selects — **a missing event type in our receiver is a config
  gap, not a vendor gap.**

### 1.5 The dedup_key (Events API v2 — the inbound direction, for the forwarder lane)

Sources: PagerDuty Events API v2 reference as summarized in
`github.com/anthropics/claude-tag-plugins` pagerduty-api references
(https://github.com/anthropics/claude-tag-plugins/blob/HEAD/pagerduty/skills/pagerduty-api/references/api.md,
accessed 2026-10-03); behavior quote from the Ansible PagerDuty module docs
(https://www.rubydoc.info/gems/ansible-ruby/1.0.18/Ansible/Ruby/Modules/Pagerduty_alert,
accessed 2026-10-03).

`POST https://events.pagerduty.com/v2/enqueue`:
`{routing_key, event_action: trigger|acknowledge|resolve, dedup_key, payload:
{summary (≤1024 chars), severity: critical|error|warning|info, source,
timestamp, component, group, class, custom_details}, images[], links[],
client, client_url}`.

Dedup semantics (vendor behavior, quoted):
*"If there's no open (i.e. unresolved) incident with this key, a new one will
be created. If there's already an open incident with a matching key, this
event will be appended to that incident's log."* Acknowledge/resolve events
referencing resolved or nonexistent incidents are **discarded**.
**This is the vendor's own never-auto-close + fresh-episode-after-resolve
doctrine: resolve the incident and the next trigger with the same key opens a
new incident — it does not resurrect the old one.**

---

## 2. Opsgenie — Alert callbacks (outbound)

Primary sources:
- "Integrate Opsgenie with Webhook"
  (https://support.atlassian.com/opsgenie/docs/integrate-opsgenie-with-webhook,
  accessed 2026-10-03)
- "Use alert callbacks"
  (https://support.atlassian.com/opsgenie/docs/use-alert-callbacks/,
  accessed 2026-10-03)
- "What is alert de-duplication?"
  (https://support.atlassian.com/opsgenie/docs/what-is-alert-de-duplication/,
  accessed 2026-10-03)
- Opsgenie Edge Connector alert action data (sample payloads per action)
  (https://support.atlassian.com/opsgenie/docs/opsgenie-edge-connector-alert-action-data/,
  accessed 2026-10-03)

### 2.1 Transport

- HTTP POST of a JSON body to a configured URL. **No native signing scheme**
  — auth is whatever you put in the URL or custom headers. Our receiver must
  treat the callback URL as a bearer secret (unpredictable path / header
  token) and never log it.
- Emission is **mapping-driven**: in the Webhook integration you configure
  "Post to Webhook URL for [action]" mappings (e.g. "if alert is created in
  Opsgenie → post to URL"). Our receiver sees only mapped actions — same
  config-gap warning as PagerDuty applies.
- One payload = one alert action on one alert (no batching).

### 2.2 Payload shape

```json
{
  "action": "Create",
  "alert": {
    "alertId": "44f717bf-44bd-11c9-44c7-2d0cf1d07b23",
    "message": "testing webhooks",
    "alias": "webhooktest",
    "tinyId": "454",
    "entity": "",
    "username": "fili@opsgenie.com",
    "userId": "4caaaa77-9222-4322-8622-d3522fbd7dda",
    "priority": "P1",
    "tags": ["Critical"],
    "source": "user@opsgenie.com",
    "createdAt": 1512047424512,
    "updatedAt": 1512559548447000000,
    "insertedAt": 1512047424512000000,
    "description": "...",
    "details": {"key1": "value1"}
  },
  "source": {"name": "mytool", "type": "api"},
  "integrationId": "868be72a-8015-432e-8b23-c1f7f4374baa",
  "integrationName": "Webhook_Test",
  "integrationType": "Webhook"
}
```

(Field names/values above are the vendor's own samples from the two Atlassian
support pages, merged with the Edge Connector action-data page.)

| Field | Reliability | Meaning / notes |
|---|---|---|
| `action` | always | `Create`, `Acknowledge`, `AddNote`, `AssignOwnership`, `Close`, `Delete`, or a **custom action name**. Custom actions are free-form strings — our receiver must not assume a closed enum. |
| `alert.alertId` | always | UUID. **Canonical identity #1.** Use the Alert API (`GET /v2/alerts/{alertId}`) to fetch full fields. |
| `alert.message` | always | Title. Community-reported 130-char cap (treat as display truncation, not a parse limit). |
| `alert.alias` | usually | **The dedup key** (client-defined, ≤512 chars per community/Opsgenie-API evidence). "User-defined unique identifier for *open* alerts." Often empty if the sender didn't set one — **then dedup is effectively off.** |
| `alert.tinyId` | always | Short numeric id for humans. |
| `alert.entity` | usually | Affected system/host. Often `""`. |
| `alert.username` / `alert.userId` | usually | The actor for the action (who acked/closed). |
| `alert.priority` | usually | `P1`–`P5`, default `P3`. **Severity.** |
| `alert.tags` | usually | String array. |
| `alert.source` | usually | Who created the alert. |
| `alert.createdAt` | always | Epoch **milliseconds**. |
| `alert.updatedAt` / `alert.insertedAt` | usually | Vendor samples show **nanosecond** epoch here (16 digits). Parse defensively: magnitude-sniff ms vs ns. |
| `alert.description` / `alert.details` | **optional, gated** | Sent **only** for `Create` and custom actions, **only** if the "send description/details" checkboxes are ticked in the integration. **Truncated to 1000 chars.** Our receiver MUST NOT require these fields. |
| `source.{name,type}` | always | Origin of the action (`type`: `api`/`web`/…). |
| `integrationId` / `integrationName` / `integrationType` | always | Which integration emitted this. Useful for multi-tenant routing. |
| Action-specific extras | situational | e.g. `UpdatePriority` carries `oldPriority`; `Escalate` carries `escalationNotify{name,id,type,entity}`, `escalationName`, `escalationTime`, `repeatCount`. Parse `action` first, then branch. |

### 2.3 The `alias` dedup contract (Alert API direction — vendor doctrine)

From "What is alert de-duplication?" (Atlassian support, verbatim rules):

1. **At most one open alert with the same alias at any time.**
2. A create with an alias matching an **open** alert does **not** create a new
   alert — it **increments the open alert's Count** and appends a
   de-duplication entry to its activity log.
3. Count/log bookkeeping stops after 100 occurrences, but **de-duplication
   continues** as long as the alert is open.
4. If a de-duplicated alert matched an Auto-Close Policy, auto-closing is
   **postponed** by the policy's time.
5. (Community-confirmed operational rule, Atlassian forum:
   https://community.atlassian.com/forums/Opsgenie-questions/How-to-manage-duplicate-alerts-against-jira-incident/qaq-p/1946586 —
   "the alias is the unique identifier for open alerts"; a *closed* alert +
   same alias ⇒ a **new** alert is created. Dedup applies to open alerts only.)

**This is the vendor's fresh-episode-after-close doctrine, matching
PagerDuty's incident_key behavior.**

### 2.4 Version notes

Opsgenie alert callbacks have no v1/v2/v3 versioning story in the vendor docs
— the shape above is the current documented one. The Alert API is v2
(`https://api.opsgenie.com/v2/alerts`, `Authorization: GenieKey <key>`,
`202 Accepted` — async processing). No migration hazard found 2026-10-03.

---

## 3. Alertmanager — webhook receiver (outbound)

Primary source: the official webhook payload schema in the Prometheus docs
(`prometheus.io/docs/alerting/latest/configuration/#webhook_config`), quoted
verbatim in `github.com/mr-karan/calert` issue #60
(https://github.com/mr-karan/calert/issues/60, accessed 2026-10-03), and
corroborated by two independent receiver implementations:
`github.com/iabdukhoshimov/monitoring-event-service`
(https://github.com/iabdukhoshimov/monitoring-event-service, accessed
2026-10-03) and `github.com/vnmoorthy/canary-refinery` obs/README.md
(https://github.com/vnmoorthy/canary-refinery/blob/HEAD/obs/README.md,
accessed 2026-10-03).

### 3.1 Transport

- HTTP POST, JSON body, to the URL in the `webhook_config`. No signing, no
  versioning headers — same bearer-secret treatment as Opsgenie.
- **One POST per group per notification cycle** (not one per alert).

### 3.2 Payload shape

| Field | Reliability | Meaning / notes |
|---|---|---|
| `version` | always | `"4"` — the payload schema version, **not** the Alertmanager version. |
| `groupKey` | always | String key identifying the group, e.g. `"{}:{alertname=\"HighCPUUsage\"}"`. Group identity. |
| `truncatedAlerts` | always | Int. How many alerts were dropped from `alerts[]` due to `max_alerts`. **> 0 ⇒ our receiver saw a degraded picture — flag it, don't silently page on a partial group.** |
| `status` | always | `"firing"` or `"resolved"`. **This is the GROUP rollup, not per-alert.** A `firing` group payload routinely contains resolved alerts. **Never treat top-level `resolved` as resolve-all without iterating `alerts[]`.** |
| `receiver` | always | Receiver name from the config. |
| `groupLabels` | always | The labels the group was formed on (the `group_by` set). |
| `commonLabels` | always | Labels common to all alerts in this notification. |
| `commonAnnotations` | always | Annotations common to all alerts. |
| `externalURL` | always | Backlink to the Alertmanager. **Link — keep for audit.** |
| `alerts` | always | Array of the alerts in this group **for this notification cycle** — firing AND newly-resolved. |

Per alert in `alerts[]`:

| Field | Reliability | Meaning / notes |
|---|---|---|
| `status` | always | `"firing"` or `"resolved"` **per alert — the field our receiver must branch on.** |
| `labels` | always | Full label set, incl. `alertname` and (by convention, unenforced) `severity`. **Identity #1.** |
| `annotations` | always | `summary`, `description`, runbook links. Free-form per rule. |
| `startsAt` | always | RFC3339. **Episode start.** A re-fire after resolve gets a NEW `startsAt`. |
| `endsAt` | always | RFC3339. **`0001-01-01T00:00:00Z` while firing** — the zero-time sentinel. Real timestamp when resolved. |
| `generatorURL` | usually | Prometheus graph URL for the expression — identifies the causing entity. **Link — keep.** |
| `fingerprint` | always | Hash of the alert's label set. **Stable dedup identity across episodes** (same labels ⇒ same fingerprint; the episode boundary is `startsAt`). |

### 3.3 Group notification semantics (why this shape differs)

This is the critical vendor semantic the prior research flagged and the docs
confirm:

- Alertmanager **batches by group**: `group_wait` (default 30s) delays the
  first notification for a group; `group_interval` (default 5m) sets the
  cadence of follow-ups; `repeat_interval` re-sends unchanged groups.
- Each POST carries **the whole current group state**: every firing alert plus
  alerts that resolved since the last notification. A resolved alert is
  delivered exactly once with `status: "resolved"` and a real `endsAt` — then
  it leaves the group.
- **Resolved-then-refired is a new episode at the vendor level**: same
  `fingerprint` (label hash unchanged), new `startsAt`, fresh `endsAt` zero
  sentinel. The vendor gives us the episode boundary for free — our
  correlator must not invent its own.
- `severity` is a **label convention, not a schema field** (`critical` /
  `warning` / `info` by community convention). Absent or exotic values are
  normal — **missing severity ⇒ page (Law 2), never assume low.**
- Inhibition rules and silences are server-side; the webhook receiver never
  sees what was inhibited — only what survived. A silence during maintenance
  is invisible to us. (This is why the P1/P2 safety override must live in OUR
  gate, §4 of the edge-cases doc.)

### 3.4 Version notes

Payload `version: "4"` is the schema version and has been stable across
Alertmanager releases; there is no v1/v2/v3 webhook versioning to migrate
between. `truncatedAlerts`/`max_alerts` and the group-batch model are the
durable contract.

---

## 4. Cross-vendor comparison (what the receiver normalizes)

| Concern | PagerDuty v3 | Opsgenie | Alertmanager |
|---|---|---|---|
| Identity of the incident | `event.data.id` (+ `number`) | `alert.alertId` (+ `tinyId`) | `fingerprint` (per alert) / `groupKey` (per group) |
| Dedup key (stable across fires) | `data.incident_key` (fallback: `data.id`) | `alert.alias` (fallback: `alertId`; empty alias ⇒ no dedup) | `fingerprint` (fallback: `groupKey` + `startsAt`) |
| Severity | `data.priority.summary` (P1–P5-ish) + `data.urgency` (high/low) | `alert.priority` (P1–P5, default P3) | `labels.severity` (convention only) |
| Lifecycle events | `incident.triggered/.acknowledged/.resolved/.reopened/.escalated/.priority_updated` | `action`: Create/Acknowledge/Close/Delete/custom | per-alert `status`: firing/resolved in a group batch |
| Episode boundary after resolve | new incident for same `incident_key` | new alert for same `alias` | new `startsAt`, same `fingerprint` |
| Links | `data.html_url`, `data.self` | (fetch via Alert API) | `generatorURL`, `externalURL` |
| Auth on the wire | `x-pagerduty-signature` (HMAC-SHA256) | none (bearer URL/headers) | none (bearer URL) |
| Event idempotency key | `event.id` | (`alertId`, `action`) | (`fingerprint`, `startsAt`) |
| Batching | one event per POST | one action per POST | whole group per POST |
| Server-side suppression visible? | maintenance windows suppress before webhook | policies route before callback | silences/inhibition invisible to receiver |

## 5. Honest limitations

- PagerDuty fields marked "usually" (priority, assignees, incident_key) depend
  on account configuration; the vendor docs show them populated in examples
  but do not publish an exhaustive nullability table. Our receiver treats
  everything except the "always" column as optional.
- Opsgenie's 130-char message cap and 512-char alias cap are community/
  API-doc evidence, not stated on the two callback pages — flagged as such.
- The Opsgenie `updatedAt` nanosecond-epoch observation comes from the
  vendor's own sample (16-digit value); magnitude-sniffing is the defensive
  parse, not a documented contract.
- Alertmanager semantics confirmed against the official schema text and two
  independent receiver implementations; `group_wait`/`group_interval`
  defaults (30s/5m) are from the Alertmanager configuration docs via the
  prior research, not re-fetched today.
