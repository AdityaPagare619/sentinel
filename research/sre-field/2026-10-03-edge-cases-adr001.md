# 2026-10-03 — Edge-case evidence base for ADR-001 (correlator craft alignment)

**Status of ADR-001: PROPOSED, NOT DECIDED.** Aditya decides. This document is
evidence + proposed receiver/correlator behaviors as input to that decision.

**Question:** For the four correlator edge cases named in ADR-001 — (1) flap
debounce with re-fire-reopens-same-row, (2) resolved-then-refired as a fresh
episode, (3) never auto-close incidents, (4) P1/P2 never silenced in
maintenance windows — what do vendors and practitioners actually do, exactly,
and why does each matter for a pre-page gate like Sentinel?

Builds on `2026-10-02-escalation-dedup-craft.md` (doctrine summary); this file
adds per-case vendor/practitioner evidence with exact behaviors and URLs,
all sources accessed 2026-10-03 unless noted.

---

## Case 1 — Flap debounce: re-fire inside the flap window reopens the same row

### What it is
A monitor oscillating across its threshold produces fire → resolve → fire →
resolve. Without debounce, each cycle is a new incident row, a new page, and
a new escalation — a flapping monitor becomes thousands of notifications for
one fuzzy underlying condition.

### Evidence

**E1 — firefightlabs/firefight `docs/alerts.md` (practitioner implementation,
the strongest single source).** Their layered storm-control table names the
mechanism exactly:
- *"Flap debounce | re-fire inside `flap_window` reopens the same row"*
  (implementation: `#recently_resolved` / `#reopen`).
- *"A monitor that fires 10,000 times in five minutes produces one alert row,
  one incident, and one Slack message reading `fired 10000x`."*
- Source: https://github.com/firefightlabs/firefight/blob/HEAD/docs/alerts.md
  (accessed 2026-10-03; the same doc was cited in the 2026-10-02 research).

**E2 — fclairamb/solidping (independent implementation, same shape).**
`createOrReopenIncident` tries `tryReopenIncident` first: reopen window =
`check.Period × ReopenCooldownMultiplier` (default 5), clamped to
**[2 min, 30 min]**. Reopen is refused when the incident was manually acked
or the check definition changed since resolution — otherwise a new incident
is created. Reopening flips state back to active, clears resolved/ack fields,
bumps `RelapseCount` and `FailureCount`, and emits an explicit
`IncidentReopened` event.
- Source: https://github.com/fclairamb/solidping/blob/HEAD/wiki/features/notifications-and-escalation.md
  (accessed 2026-10-03).

**E3 — IBM Cloud Pak for AIOps, "Detecting flapping events" (vendor).**
Built-in *Global flapping detection* policy marks events that clear and
reopen **4+ times in an hour** as flapping; they stop being flapping after
30 minutes of stability. *"When an incident contains flapping events, it
cannot be resolved automatically until the events stop flapping… If a user
tries to manually set an incident with flapping events to resolved, they are
warned that flapping events might cause the incident to reopen."*
- Source: https://www.ibm.com/docs/en/cloud-paks/cp-management/2.3.0?topic=policies-example-detecting-flapping-events
  (accessed 2026-10-03).

**E4 — nitishmane/sre-oncall, alert-quality runbook (practitioner).**
Flapping pathology write-up: a crashlooping deployment with backoff dips
under threshold and the alert "resolves while nothing is fixed" — *"an
incident was resolved and a postmortem written while all three replicas were
still in CrashLoopBackOff."* Distinguishes `for` (how fast it fires) from
`keep_firing_for` (how sure it must be that the condition is over):
*"Flapping on recovery is a `keep_firing_for` problem, not a threshold
problem."*
- Source: https://github.com/nitishmane/sre-oncall/blob/HEAD/skills/sre-runbooks/runbooks/alert-quality.md
  (accessed 2026-10-03).

### Why it matters for Sentinel (pre-page gate)
The gate sits *before* the human. If we create a new incident row per flap
cycle, we forward N pages for 1 problem — exactly the noise Sentinel exists
to kill. The flap window is the difference between "debounced" and "new
incident": re-fire inside the window reopens the same row (and must bump a
visible fire/relapse count so the human sees it's flapping, not fresh).

### Proposed behavior (input to ADR-001, not a decision)
- Correlator keeps one row per `(vendor, dedup_key)` episode; a
  `flap_window` (proposed default **30m**, inside the solidping clamp range
  2–30m and matching IBM's 30m flap-clear) after resolve: any re-fire with
  the same dedup key **reopens the same row**, bumps `fire_count` /
  `relapse_count`, emits an explicit `reopened` audit event, and re-enters
  the gate with the *escalated* disposition (a flapping P3 is behaving like
  a P2 — propose: bump internal severity one notch on reopen, cap at P1).
- Reopen is refused (new row instead) if the previous row was closed by an
  explicit human close, or if the alert identity changed (new fingerprint /
  new alias / new incident_key ⇒ different question, per solidping's
  check-changed rule).

---

## Case 2 — Resolved-then-refired is a fresh episode, not a reopen

### What it is
Once the flap window has elapsed (or the resolve was a genuine all-clear), a
new fire is a *new episode*: new row, new gate evaluation, linked (not
merged) to the prior episode. Conflating it with the old row corrupts MTTR,
hides recurrence, and lets a recurring fault masquerade as one long incident.

### Evidence

**E1 — firefightlabs/firefight (same doc):** *"a resolved-then-refired alert
is a fresh episode, not a reopened one."*
(https://github.com/firefightlabs/firefight/blob/HEAD/docs/alerts.md)

**E2 — PagerDuty dedup_key semantics (vendor):** *"If there's no open (i.e.
unresolved) incident with this key, a new one will be created. If there's
already an open incident with a matching key, this event will be appended to
that incident's log."* — i.e. **resolve the incident and the next trigger
with the same key opens a NEW incident**; the vendor never resurrects the
old one. Acknowledge/resolve events referencing resolved incidents are
discarded.
(https://www.rubydoc.info/gems/ansible-ruby/1.0.18/Ansible/Ruby/Modules/Pagerduty_alert,
quoting PagerDuty API behavior; accessed 2026-10-03.)

**E3 — Opsgenie alias dedup (vendor):** *"There can be at most one open alert
with the same alias at any time"*; dedup (count++) applies to **open**
alerts only. A closed alert + a new create with the same alias ⇒ **a new
alert** (confirmed in Atlassian community practice:
https://community.atlassian.com/forums/Opsgenie-questions/How-to-manage-duplicate-alerts-against-jira-incident/qaq-p/1946586).
(https://support.atlassian.com/opsgenie/docs/what-is-alert-de-duplication/,
accessed 2026-10-03.)

**E4 — Alertmanager (vendor):** resolved-then-refired keeps the same
`fingerprint` (label hash) but gets a **new `startsAt`** and a fresh
`endsAt` zero-sentinel. The vendor hands the correlator the episode boundary
explicitly — the episode is defined by (`fingerprint`, `startsAt`), not by
`fingerprint` alone.
(Webhook payload schema, prometheus.io docs via
https://github.com/mr-karan/calert/issues/60; see companion payloads doc.)

### Why it matters for Sentinel
This is the *complement* of Case 1 and the pair must be implemented together:
inside the flap window → reopen same row; outside it (or after genuine
resolve) → new episode. Getting the boundary wrong in either direction is a
failure mode: too eager to merge hides recurrence and corrupts MTTR; too
eager to split re-creates the flap storm. The vendors agree on the rule
(open-ness is the criterion, not key equality), which gives our correlator a
vendor-consistent definition to implement.

### Proposed behavior (input to ADR-001, not a decision)
- Episode key = (`vendor`, `dedup_key`, `episode_seq`). Resolve starts the
  flap window; re-fire inside ⇒ reopen (Case 1); re-fire after the window ⇒
  `episode_seq + 1`, new row, `previous_episode_id` link, fresh gate
  evaluation (a recurring fault re-earns its page — it is not grandfathered
  by the old episode's suppression).
- For Alertmanager, episode identity is (`fingerprint`, `startsAt`) straight
  from the vendor payload; for PagerDuty, (`incident_key` → new `data.id`
  after resolve); for Opsgenie, (alias → new `alertId` after close).
- Audit explorer shows episodes as a chain: `EP-3 → reopened from EP-2
  (flap, 12m after resolve)`, never a silent merge.

---

## Case 3 — Never auto-close incidents

### What it is
A "resolved" signal from monitoring marks the *alert* resolved. It must never
close the *incident*. Closing is a human decision: the monitor saying "green"
is not evidence the underlying condition is fixed (the crashloop postmortem
written while replicas were still crashlooping, E4 in Case 1).

### Evidence

**E1 — firefightlabs/firefight:** *"Never auto-close | a resolved alert
never closes its incident"* and, verbatim: *"**Incidents are never
auto-closed from a resolved alert.** A resolve marks the alert and updates
the digest, nothing else. Closing is a human decision."*
(https://github.com/firefightlabs/firefight/blob/HEAD/docs/alerts.md)

**E2 — projectertzu/kindergarten123 (practitioner, production paging doc):**
Only `trigger` is implemented; `acknowledge`/`resolve` are *deliberately
absent* because "ERTZU has no trustworthy internal recovery signal… 
*Auto-resolving would silently close incidents that still hold real money.
**The human resolves the incident in PagerDuty once the underlying
condition is actually repaired**."*
(https://github.com/projectertzu/kindergarten123/blob/HEAD/docs/production-paging-pagerduty.md,
accessed 2026-10-03.)

**E3 — IBM (vendor):** an incident containing flapping events *cannot be
resolved automatically* until flapping stops — the vendor actively blocks
auto-resolve in the exact situation where it's most tempting.
(https://www.ibm.com/docs/en/cloud-paks/cp-management/2.3.0?topic=policies-example-detecting-flapping-events)

**E4 — Opsgenie auto-close (vendor, the exception that proves the rule):**
Opsgenie *does* offer auto-close, but strictly as an **opt-in Auto-Close
Policy** with conditions and time restrictions — and even then, if a
matching alert gets de-duplicated, *"auto-closing the alert will be
postponed for the specified time of the policy."* The default is never
auto-close; auto-close is a configured policy, and dedup defeats it.
(https://support.atlassian.com/opsgenie/docs/what-is-alert-de-duplication/)

**E5 — PagerDuty (vendor):** no auto-resolve exists in the Events API
contract; `resolve` is an explicit `event_action` a human or automation must
send. (Events API v2 reference, 2026-10-03.)

### Why it matters for Sentinel
Sentinel is a *pre-page* gate, which makes this rule load-bearing in both
directions: (a) our correlator must never mark an incident closed on a
vendor `resolved`/`Close` webhook — that webhook retires the *alert row's*
firing state and starts the flap window, nothing more; (b) the gate's
disposition history must survive the resolve, because a re-fire inside the
flap window re-enters the gate with memory (Case 1), and a re-fire outside
it starts a new episode that references, not inherits, the old one (Case 2).

### Proposed behavior (input to ADR-001, not a decision)
- Vendor resolve/close events transition the alert row `firing → resolved`,
  update the digest/counts, and start the flap window. They **never** set
  incident state to closed.
- Incident closure happens only via explicit human action in Sentinel (or an
  explicit `close` API call from an authorized operator) — recorded with
  actor + reason in the audit log.
- Reopen-after-human-close is refused (Case 1 guard): a human close means
  "I looked at this"; a subsequent fire is a new episode, never a silent
  resurrection.

---

## Case 4 — P1/P2 are never silenced in maintenance windows

### What it is
Maintenance windows suppress *expected* noise for the services under
maintenance. The practitioner safety override: **P1/P2 always page through**,
because a window that can silence a SEV-1 is a window that can hide a real
outage behind a deploy.

### Evidence

**E1 — asiaostrich/universal-dev-standards `core/alerting-standards.md`
(practitioner standard):** *"Silence alerts during known maintenance:
Define maintenance windows with start/end time and affected services. All
P3/P4 alerts for affected services are silenced. **P1/P2 alerts are never
silenced (safety override).** Maintenance windows are logged for audit."*
(https://github.com/asiaostrich/universal-dev-standards/blob/HEAD/core/alerting-standards.md,
accessed 2026-10-03; also cited in the 2026-10-02 research.)

**E2 — giantswarm/rfc "silence-based maintenance windows" (practitioner
design doc):** they replaced per-installation alert routing with *temporary
Alertmanager silences + an Alerts Timeline dashboard for situational
awareness* — the engineer creating the silence **monitors a dashboard during
the window** precisely because silenced alerts are invisible otherwise. The
doc's premise is that silences are dangerous enough to need a compensating
watch.
(https://github.com/giantswarm/rfc/blob/HEAD/silence-based-maintenance-windows/README.md,
accessed 2026-10-03.)

**E3 — Vendor behavior (why the override must be OURS):** PagerDuty
maintenance windows suppress incident creation for *all* events on the
windowed services — there is no native per-priority carve-out in the
documented behavior ("prevent any Incidents from being created during the
period of the maintenance window"). Alertmanager silences match on label
matchers with no severity floor. **Neither vendor implements the P1/P2
override; it is a practitioner policy the gate must enforce itself.**

### Why it matters for Sentinel
Sentinel's gate is the last place the override can live: vendor-side
suppression (PD maintenance windows, AM silences, Opsgenie policies) happens
*before* the webhook reaches us and is invisible to the receiver. Whatever
reaches our gate with P1/P2 severity during a maintenance window must page —
the gate is the safety net under the vendors' all-or-nothing suppression.
Without the override, a maintenance window becomes a plausible deniability
machine for a SEV-1.

### Proposed behavior (input to ADR-001, not a decision)
- Maintenance windows in Sentinel: **service-scoped** (never whole-team),
  **time-bounded** with automatic expiry (proposed cap: 24h, no indefinite
  windows), **reason required** (free text, min length), **logged to the
  audit trail** (who, what scope, why, when).
- **P1/P2 safety override:** the suppressing policy applies only to P3 and
  below. Any alert normalizing to P1/P2 bypasses window suppression and
  pages. The bypass itself is audit-logged ("paged despite window W-118:
  severity P1").
- Windows are visible in the dashboard (muted-not-dropped visibility state
  from the 2026-10-02 research applies here: suppressed alerts remain
  eyeball-able, never paging).
- A window cannot be created retroactively and cannot cover incidents
  already open before the window started (no back-dated silence).

---

## Creative application — concrete receiver/correlator behaviors proposed

These are **proposals as input to ADR-001**, not decisions. Each is tagged
with the evidence above.

1. **Per-vendor dedup-key extraction** (payloads doc §4): PagerDuty →
   `data.incident_key`, fallback `data.id`; Opsgenie → `alert.alias`,
   fallback `alertId` (empty alias ⇒ dedup off, say so in the row);
   Alertmanager → `fingerprint`, fallback `groupKey`+`startsAt`. Correlator
   identity = (`vendor`, `dedup_key`).
2. **Severity normalization map** (proposed): P1 ⇐ {PD priority P1/P2 or
   urgency high + priority P1/P2; Opsgenie P1; AM severity critical},
   P2 ⇐ {PD P3/high; Opsgenie P2; AM severity high/error}, P3 ⇐ {PD P4/low;
   Opsgenie P3 (default); AM warning}, P4/P5 ⇐ {rest / AM info}. **Missing
   or unrecognized severity ⇒ treat as P2 and page (Law 2: ambiguity
   pages).** Priority `summary` strings are account-configurable — match on
   the stable `id` where available, fall back to normalized summary text.
3. **Episode state machine** (Cases 1+2): `firing → resolved → (flap window
   30m) → reopened | new episode`; `resolved → human-closed` is terminal
   (re-fire ⇒ new episode). All transitions emit audit events with actor
   (vendor webhook vs human).
4. **Redelivery idempotency** (payloads doc §4): PD `event.id`; Opsgenie
   (`alertId`, `action`); AM (`fingerprint`, `startsAt`). Unique index on
   the provider event id before any gate evaluation.
5. **Alertmanager group discipline**: iterate `alerts[]`, branch on per-alert
   `status`; never treat top-level `status` as resolve-all;
   `truncatedAlerts > 0` ⇒ mark the group row `visibility: degraded` and
   note it in the digest (we may be paging on a partial picture).
6. **Link preservation**: store `html_url` (PD), `generatorURL`/`externalURL`
   (AM) on the incident row — the audit explorer links back to the vendor,
   no re-fetch needed.
7. **Opsgenie optional-field tolerance**: receiver schema marks
   `description`/`details` optional (they're checkbox-gated and 1000-char
   truncated); the gate never depends on them. `updatedAt` magnitude-sniff
   (ms vs ns).
8. **Signature verification**: verify `x-pagerduty-signature` (HMAC-SHA256)
   on the PD path; treat the Opsgenie/AM callback URLs as bearer secrets
   (unpredictable path + header token, rotated, never logged).
9. **Synthetic acceptance scenarios** (for the Tripwire lane): (a) 10k
   fires/5min on one key ⇒ 1 row, 1 page, `fired 10000x` digest; (b)
   resolve→re-fire at 12m ⇒ reopen, no new page storm, severity bumped;
   (c) resolve→re-fire at 45m ⇒ new episode, fresh gate; (d) human
   close→re-fire ⇒ new episode, never silent resurrection; (e) vendor
   resolve webhook ⇒ alert resolved, incident stays open; (f) P1 during a
   maintenance window ⇒ pages, bypass logged.

## Evidence-strength self-grade

- **Strong (vendor docs + ≥2 independent implementations):** flap-debounce
  reopen semantics; fresh-episode-after-resolve (PD + Opsgenie + AM all
  agree); never-auto-close (vendor default + practitioner doctrine).
- **Strong practitioner consensus, vendor-neutral by necessity:** P1/P2
  never silenced — the vendors *don't* implement it, which is exactly why
  the evidence is practitioner-side and the behavior must be ours.
- **Weaker / flagged:** exact flap-window default (30m proposed from
  solidping's clamp and IBM's 30m; tune against our own data); severity
  string matching across account-configured PD priorities (needs a
  per-account mapping table, not a global constant).
