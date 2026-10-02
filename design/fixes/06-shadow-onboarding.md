# 06 — Shadow Onboarding: The Enterprise Adoption Surface

**LANE 4 (Pager) · principal deep-dive wave · 2026-10-03**
**Status:** DESIGN ONLY. No code. No branch switches, no commits, no pushes.
**Judged against:** `design/principal/00-laws.md` (the seven laws).
**Builds on:** `design/principal/03-deployment-path.md` (the three-stage
strategy), `design/principal/11-synthesis.md` §4 (proof engine first) and §5
(the trust moat), `design/principal/12-adr-deltas.md` §2 (ADR-006:
shadow-pilot evidence standards — this document is its elaboration).
**This is the demo's centerpiece narrative:** the adoption surface is not an
onboarding flow; it is the product strategy made tangible.

---

## 0. First principles — why the adoption surface is the product

Peel "we need enterprise onboarding" to bedrock (Law 4):

- Why won't the buyer replace their paging stack? → Because it is
  load-bearing, audited, compliance-blessed, and wired into runbooks,
  postmortems, and muscle memory. (Law 5: the fence is real — SOC 2
  auditors ask for the escalation policy; the weird dedup rule exists
  because of a specific 3 AM in 2019.)
- Why won't the buyer trust a suppression engine on day one? → Because
  their one fear is the Type 1 error: a suppressed real SEV1 that burns
  an extra hour while the postmortem names the new vendor. No slide deck
  overcomes it.
- What overcomes it? → Evidence generated on the buyer's own data, under
  the buyer's own eyes, with the buyer's own incidents as the test set —
  before the vendor is ever *allowed* to act.
- So what is the product? → Not "fewer pages." The product is the
  evidence-generating machine. The suppression is what the evidence
  *permits*. **Sentinel sells evidence, and the suppression comes free
  with it.** (Synthesis §5.)

The adoption surface is therefore not a funnel toward a purchase. It is a
funnel toward *permission*: each stage buys the buyer one unit of
evidence, and the buyer advances only when the evidence is in hand. The
shadow report arrives in week one — the buyer gets value (their noise
named, with evidence) before the vendor has any power at all. That is
**time-to-value, not total replacement**: the first invoice-able value
lands while the existing stack is byte-identical.

Global-vs-local (Law 1) check: a conventional onboarding optimizes the
funnel — faster provisioning, slicker wizard. The global question is
whether onboarding should exist *as a distinct phase* at all. Here the
answer is yes, but redefined: onboarding is not setup, it is the first
product deliverable. The shadow *is* stage zero of the product, not a
prelude to it.

---

## (a) Stage 0 shadow — the read-only tap

### a.1 What the enterprise changes: one additive config, nothing removed

In every estate below, the existing paging behavior is unchanged because
the tap is *additive*: a new subscription/route/receiver that runs
alongside the existing ones. Nothing is repointed, removed, or
reordered. The estate's paging path never depends on Sentinel existing.

### a.2 The tap, per vendor — verified against live vendor docs

**PagerDuty — Generic Webhook (v3) subscription.**
(Verified: PagerDuty support docs "Webhooks", developer docs
"Verifying Signatures", community setup guides; accessed 2026-10-03.)

- Setup: Integrations → **Generic Webhooks (V3)** → New Webhook →
  Webhook URL = Sentinel's shadow receiver URL → **Scope Type**:
  Account (all incident events) or Service (scoped — via the service's
  Integrations tab; this is the mechanism behind service-by-service
  canary scoping in §(d)) → **Events to Send**: select
  `incident.triggered` at minimum (add `incident.acknowledged`,
  `incident.resolved`, `incident.escalated`, `incident.priority_updated`
  for the full lifecycle — the pre-mortem in §(h) exists because one
  of these is easy to forget) → Add Webhook → **Send Test Event** to
  verify.
- Authentication/integrity: PagerDuty generates the **signing secret at
  creation and shows it exactly once** (regenerate in PD if lost).
  Every delivery carries `x-pagerduty-signature` (HMAC-SHA256 over the
  body) and `x-webhook-subscription`. Sentinel verifies the signature
  on receipt; unsigned or bad-signature deliveries are dropped and
  counted (the drop counter is itself reported — Law 7 infra: silence
  about the tap's health is forbidden).
- Passive by construction: a webhook subscription is an additional
  subscriber on PD's incident bus. Existing service integrations
  (Events API v2 routing keys, escalation policies, notification
  rules) are untouched. Adding a subscription cannot change who gets
  paged, because paging is decided by PD's escalation engine, not by
  its subscribers.
- Delivery semantics (Law 2 — the network drops): PagerDuty retries
  failed webhook deliveries. The shadow ingest is therefore
  **idempotent on `(subscription_id, event_id)`** — `event_id` is in
  the v3 payload — so retried deliveries collapse to one decision
  event. A slow or dead shadow receiver changes nothing on PD's side.

**Opsgenie — outgoing Webhook integration.**
(Verified: Opsgenie/Atlassian support docs on integration types and
actions; third-party integration guides for the Webhook integration's
outgoing path; accessed 2026-10-03.)

- Setup: Settings → **Integrations** → Add Integration → **Webhook**
  (team-level or global — plan-dependent; the onboarding checklist
  verifies the estate's plan supports outgoing webhooks before promising
  them) → Webhook URL = Sentinel's shadow receiver → optional custom
  headers for a bearer token → tick **Add alert description/details to
  payload** → under **Alert actions**, select which events forward:
  **Create** (required — this is the "alert fired" signal),
  Acknowledge and Close (recommended — they close the episode and feed
  the label pipeline) → optional alert **filters** (conditions) to scope
  which alerts forward — the service-by-service scoping mechanism →
  **Save**, then verify in the integration's **Delivery Log**.
- Passive by construction: the webhook is an *outgoing* integration;
  Opsgenie's notification rules, escalation policies, and existing
  integrations are untouched. The alert still flows to its responders
  exactly as before; Sentinel merely receives a copy.
- Delivery semantics: Opsgenie retries webhook deliveries on failure;
  ingest idempotency key is the alert's `alertId` + action type +
  action timestamp.
- Honest note: the outgoing payload's completeness depends on the two
  checkboxes ("add description/details"). The onboarding runbook sets
  them; the shadow's weekly report includes a **payload-completeness
  line** ("3.1% of events arrived without details — integration config
  drift suspected") because Law 2 assumes the estate will drift.

**Alertmanager — additive `webhook_configs` receiver with `continue: true`.**
(Verified: Alertmanager configuration semantics via the promview and
Robusta integration patterns; accessed 2026-10-03.)

- Setup: add one receiver and one child route to the existing
  `alertmanager.yml`; nothing else changes:

  ```yaml
  routes:
    # ADD THIS as the FIRST child route of the root route:
    - receiver: 'sentinel-shadow'
      # catch-all matcher so every alert is copied to the shadow
      continue: true          # <-- the load-bearing flag: routing continues
                              #     to all existing receivers unchanged
  receivers:
    - name: 'sentinel-shadow'
      webhook_configs:
        - url: 'https://shadow.<buyer>.sentinel.example/am/v1/ingest'
          send_resolved: true  # required: resolve events close episodes
          http_config:
            authorization:
              credentials_file: /etc/alertmanager/secrets/sentinel-shadow-token
  ```

  The `continue: true` flag is the entire safety argument for this
  estate: the shadow route matches everything, copies it, and routing
  proceeds to the existing receivers exactly as before. The paging
  receiver still fires. (This is the WAF monitor-before-block pattern
  from 03-deployment-path.md §Precedents, applied verbatim.)
- Authentication: Alertmanager webhooks carry **no native signature**.
  Authenticate with a bearer token (via `credentials_file`, never inline
  in the config — the token must not live in the estate's git repo) over
  TLS; mTLS is a per-estate option. The shadow receiver rejects
  unauthenticated POSTs and counts them.
- Payload: grouped batches (`receiver`, `status`, `alerts[]` with
  labels/annotations/`fingerprint`/`startsAt`/`endsAt`,
  `commonLabels`). The shadow runs its own correlator over these, with
  the same grouping semantics, so the "would-have" dispositions are
  computed on the same alert groups the estate's paging saw.
- Delivery semantics: a failing webhook receiver does **not** block
  other receivers in Alertmanager — the paging path is fate-decoupled
  from the shadow by the vendor's own architecture. Retry backoff is
  Alertmanager's; ingest idempotency key is the alert `fingerprint` +
  batch `status` + `startsAt`.

**Common across vendors — the enrichment read key.** Where the estate
allows it, the shadow is provisioned a **read-only API key**
(PagerDuty read-only API key; Opsgenie read API key) for two purposes:
backfill (incident history for Stage 1) and enrichment (incident notes,
postmortem links). Read-only is verified, not asserted: the onboarding
runbook includes a negative test — attempt a write-scoped call with the
provisioned key and confirm it is denied — and the denial is logged as
the first entry in the Divergence Ledger. (The first evidence artifact
the buyer sees is us failing to write. That is deliberate.)

### a.3 What flows where

```
 EXISTING STACK (untouched)                          SHADOW (Sentinel)
 ┌────────────────────────┐
 │ PagerDuty / Opsgenie / │───copy of every alert/incident event───►┌──────────────┐
 │ Alertmanager           │   (webhook subscription / outgoing     │ shadow       │
 │                        │    integration / continue:true route)   │ receiver     │
 │ pages exactly as       │                                          │  (verify    │
 │ yesterday              │                                          │   sig/token) │
 └────────────────────────┘                                          └──────┬───────┘
        ▲ no arrow back. Ever.                                                       │
        │                                                                          ▼
        │                                                              ┌──────────────┐
        │                                                              │ shadow gate  │
        │                                                              │ (full gate:  │
        │                                                              │ receiver→    │
        │                                                              │ correlator→  │
        │                                                              │ disposition) │
        │                                                              └──────┬───────┘
        │ no write path                                                        │ writes only
        │                                                                      ▼
        │                                                              ┌──────────────┐
        │                                                              │ event log →  │
        │                                                              │ weekly       │
        │                                                              │ Shadow       │
        │                                                              │ Report       │
        └──────────────────────────────────────────────────────────────┘
```

Flows, enumerated:
1. **Vendor → shadow receiver:** alert/incident event copies (inbound
   webhooks only).
2. **Shadow receiver → shadow gate:** verified events.
3. **Shadow gate → event log:** `would_page` / `would_suppress`
   dispositions with confidence, evidence, and threshold
   counterfactuals — recorded, never executed.
4. **Event log → Shadow Report:** the weekly projection (§(b)).
5. **Sentinel → vendor:** nothing. There is no fifth flow in Stage 0.

### a.4 What can NEVER happen in shadow — proved, not promised

"Policy says we won't write" is not a safety property. Five mechanical
guarantees, each independently checkable by the buyer:

1. **No write credentials exist.** The shadow deployment's secret store
   contains exactly: (a) the inbound webhook *verification* secret
   (HMAC key — verify-only, useless for making calls), (b) optionally
   a read-only API key (write-denial tested at onboarding, §a.2). There
   is no Events API v2 routing key, no REST key with write scope, no
   silence-creation token. The shadow config schema has a
   `write_credentials: []` field validated at startup — a non-empty
   list **refuses to boot the shadow profile** (fail-closed on its own
   safety invariant; the mirror image of ADR-018's config validation).
2. **The shadow binary cannot call the vendor.** The shadow is a
   separate build profile: the disposition-execution arm (forwarder,
   vendor API clients) is compiled out. The binary has no
   PagerDuty/Opsgenie write client code at all. CI enforces this with a
   static import-graph assertion: the shadow artifact may not import any
   vendor-write client package. "We forgot to call it" is weak; "the
   code to call it does not exist in this binary" is a proof.
3. **No network path to vendor write endpoints.** The shadow profile's
   egress policy allows exactly: the ledger/event-log sink, the Jev
   inference API (the model's only external dependency), and DNS/NTP.
   There is no route to `events.pagerduty.com`, `api.pagerduty.com`,
   or `api.opsgenie.com` from the shadow network — even a
   compromised/misconfigured gate process cannot reach a write
   endpoint. The egress rules are part of the signed deployment
   manifest the buyer reviews.
4. **Dispositions are recorded, not executed — by type.** The shadow
   gate's output type is `WouldHaveDisposition`, a distinct type from
   the live gate's `ExecutedDisposition`. There is no conversion
   function between them; the compiler rejects any code path that
   treats a would-have as an executable. (Type 1 decision: the schema
   boundary between "evidence" and "action" is enforced in the type
   system, not in a comment.)
5. **The buyer holds the leash.** The shadow runs in the *buyer's*
   account/VPC wherever possible, with keys the buyer generated. The
   weekly Shadow Report's footer prints the credential inventory hash
   ("write credentials provisioned: none — hash 9f3a…"). The buyer can
   revoke the tap in their own console at any time with zero
   coordination. Trust does not require trusting us.

Consequence, stated plainly (and this is synthesis §3.6's point): in
shadow mode the I-2 liveness hole **does not exist**. A dead shadow
receiver, a crash-looped shadow gate, a poisoned shadow config — none
of it changes a single page, because the existing stack never knew the
shadow was there. The deployment path's ordering is load-bearing for
safety, not just for sales.

---

## (b) The weekly Shadow Report — exact contents

One page. Same format every week. The buyer reads it in five minutes;
the SRE team reads the divergence list the way pilots read incident
reports — because it's about *them*.

### b.1 The header (the numbers)

> **Week of Oct 6 — Shadow Report (read-only, zero stack changes)**
>
> Alerts observed: **4,212** · Human stack paged: **1,365** · Human
> stack suppressed/silenced: **2,847** (via the estate's own silences —
> we count them, we don't touch them)
>
> Sentinel would have paged: **1,352** · Agreement on pages: **99.0%**
>
> Would-have-suppressed: **2,847** — 67.6% noise-reduction *opportunity*.
> (Labeled OPPORTUNITY in every report, forever. It is not a promise; it
> is the number the evidence would have to earn.)
>
> **Divergences: 13** — 9 suppress-vs-page, 4 page-vs-suppress. All 13
> listed below with evidence links. **Zero divergences on SEV1/SEV2.**
>
> Gate p99 decision latency: **3.1s** (budget 30s — the race-to-page
> budget from ADR-010, measured on your traffic).
>
> Calibration: **within 3pp at all confidence bands** (Oracle §7 method —
> decision calibration with Wilson bounds; bands and N printed below).
>
> Tap health: 4,212/4,219 events ingested (99.8%); 7 dropped
> (bad-signature: 0; duplicate-collapsed: 7); payload completeness 99.9%.
>
> Credential inventory: write credentials provisioned: **none** (hash …).

### b.2 The numbered divergence list (the heart of the report)

Every disagreement between the gate and the human stack, both
directions. The second direction is the one that matters — it is the
false-negative hunt, running continuously from week one.

Entry format (each entry is one row; each row links into the evidence):

```
D-014  direction: page-vs-suppress   severity: SEV3   service: payments-api
  alert_key:    pd:inc/P8X2KQ · fingerprint fp:8f2c… (env+cluster namespaced, ADR-017)
  human did:    paged (escalation policy L1, 02:14 IST, ack'd in 6 min)
  gate would:   paged (confidence 0.93) — agreement on verdict, divergence on EVIDENCE:
                gate's top evidence: error-rate spike + deploy correlation (deploy d-4482,
                02:09 IST); human's ack note: "known noisy after deploys — watching"
  gate evidence bundle: [link]  raw alert payload: [link]
  threshold counterfactual: at conf ≥ 0.85 → page; at conf ≥ 0.95 → page (no counterfactual
                disagreement — the locks agree at every preset; ADR-023)
  status: EXPLAINED — buyer-accepted ("deploy-correlated self-healing; correct to page
                under current policy; candidate for allowlist entry with 30d TTL — see
                attestation proposal A-014")
```

```
D-015  direction: suppress-vs-page   severity: SEV3   service: cache-eu
  alert_key:    og:alert/9d1f… · fingerprint fp:11ab…
  human did:    paged (02:41 IST, auto-resolved 02:52, no human action — resolved itself)
  gate would:   SUPPRESSED (confidence 0.97; probability lock: reported 0.00 + fit p̂_upper
                0.0011 < 0.002 [fit v3, 2026-09-28]; confidence lock 0.97 ≥ 0.90, fresh;
                allowlist: attested tuple T-118, TTL to 2026-10-28, env=prod)
  gate evidence bundle: [link]  raw alert payload: [link]
  threshold counterfactual: at conf ≥ 0.90 → suppress; at conf ≥ 0.99 → page
                (the counterfactual is why the confidence bar is 0.90 and not 0.99 —
                the operator sees exactly how close this call was)
  status: OPEN — awaiting buyer review. If accepted: becomes regression case R-118
                ("cache-eu flap, self-resolving") and a candidate allowlist renewal.
```

Divergence-list rules (Type 1 — the list is the trust instrument, its
shape is a commitment):
- **Numbered, permanent, never renumbered.** D-015 is D-015 forever;
  the ledger is append-only (ADR-011). A divergence that disappears
  from the report is a lie; resolved divergences move to
  EXPLAINED/REGRESSION status, they are not deleted.
- **Both directions, always.** Suppress-vs-page (the ROI direction) and
  page-vs-suppress (the safety direction) are printed side by side.
  A report that only shows the money direction is marketing.
- **SEV1/SEV2 divergences are a separate, highlighted block**, even
  when the count is zero — especially when the count is zero. "Zero
  divergences on SEV1/SEV2" is printed in bold every week it is true,
  because the week it stops being true is the week everything stops.
- **Per-item evidence links are mandatory.** An entry without a working
  link to the gate's evidence bundle and the raw alert payload is a
  rendering defect (Prism: the operator study). Links resolve into the
  buyer's own estate (their PD/Opsgenie incident, their alert payload)
  — the buyer inspects *their* data, not our screenshots.

### b.3 The calibration summary

The calibration line from the header expands to a small table, one row
per confidence band, each with N:

```
confidence band   N      gate said P(page)   observed P(human paged)   |Δ|
0.90 – 0.94     412        0.92                  0.905                 1.5pp
0.95 – 0.99     238        0.97                  0.975                 0.5pp
…
```

Method: Oracle §7 — decision calibration with Wilson 95% bounds over
named reference classes (the reference class is printed: "all shadow
decisions, org=buyer, 2026-10-01→2026-10-07, N=4,212"). A band with
N < 30 is printed as **THIN — no claim** (the simulator rule from the
synthesis: refuse to project on thin data). Miscalibration beyond 5pp
in any band **holds the stage gate** (exit criteria, §(d)) — the report
says so explicitly: "calibration breach in band 0.90–0.94 (Δ=6.2pp):
shadow continues, backtest is blocked until the tuner re-fits."

### b.4 What is deliberately NOT in the report

- No model metrics (no "accuracy", no "F1", no ROC). The buyer is not
  buying a classifier; showing classifier metrics would train the
  buyer to evaluate the wrong thing. (Synthesis: no accuracy claims;
  calibration evidence only.)
- No "projected savings" beyond the labeled opportunity number. The
  report never says "you would have saved $X."
- No recommendations to change the estate's alerting ("you should fix
  your noisy check") — the divergence list *implies* them, and the
  buyer will draw the conclusions themselves. Unsolicited remediation
  advice from a week-old vendor is presumptuous; the evidence is
  respectful.

---

## (c) Stage 1 — divergence backtest: replaying the incidents that hurt

The shadow proves the gate on *current* traffic. The backtest proves it
on the incidents the buyer's team still talks about — the 3 AMs in the
postmortem archive. This is ADR-006's core evidence standard, made
procedural.

### c.1 The exact procedure

**Step 0 — scope agreement (signed).** Before extraction: the services
in scope, the historical window (6–12 months; longer is better, shorter
is documented), and — critically — the buyer's **severity taxonomy in
writing**. SEV1/SEV2 are the *buyer's* definitions (their priority
field, their postmortem tags), not ours. If the estate has no formal
severity taxonomy, the backtest cannot run — taxonomy definition
becomes a paid discovery engagement first. A backtest against an
undefined "real incident" is theater.

**Step 1 — extract.** Read-only API keys (the same ones from §a.2):
- PagerDuty: `GET /incidents` (paginated, `since`/`until`, all
  statuses) → per incident `GET /incidents/{id}/log_entries` and the
  alert table → fields: incident id, created/acknowledged/resolved
  timestamps, urgency, priority, service, escalation policy, assignees,
  every alert (alert_key, fingerprint inputs, timestamps), every note
  (acknowledgment notes carry the human's reasoning — they are the
  richest label source), every status change.
- Opsgenie: Alert API list with time-range query → alert fields plus
  the action log (Create/Acknowledge/Close/Add note with actors and
  timestamps).
- Alertmanager-native estates — **honest caveat**: Alertmanager has no
  long-term incident store. The procedure degrades, in order: (1) if
  Alertmanager feeds PD/Opsgenie (the common enterprise shape),
  extract from there; (2) otherwise, from the longest available
  window of the estate's existing incident system of record, or a
  Prometheus/Thanos remote-read export of the `ALERTS` series plus
  the alertmanager notification log; (3) whatever window results is
  documented in the ledger header with the missing-data caveat
  ("4.5 months available; 6-month minimum not met — window accepted
  in writing by buyer incident review, 2026-10-20"). The ledger never
  pretends a longer window than it has.

**Step 2 — label (ground truth = the estate's own records).**
Labeling rules, versioned (`labeling-rules v2`, pinned in the ledger
header):
- `real-SEV1/SEV2`: the estate's priority/severity field at final
  resolution **or** a postmortem tag. Postmortem tags win ties. Each
  label records its provenance: which field, which system, what
  timestamp (Law 7 data pedigree — a label without provenance is an
  opinion).
- `real-SEV3+`: paged and human-acknowledged with action taken (note
  or timeline evidence).
- `noise`: silenced via the estate's own silences, auto-resolved with
  no human action, never acknowledged, or tagged "known flaky" in a
  postmortem.
- `ambiguous`: everything else — paged but auto-resolved, acknowledged
  with no note, single-ack-and-close. **Ambiguous labels are excluded
  from both the false-suppress denominator and the noise-precision
  numerator** (they are reported as a count, not silently assigned).
  Law 6 discipline: the metric that matters must not be gameable by
  label choice.
- Inter-rater: a 5% sample is double-labeled by the buyer's incident
  review; disagreement rate is printed in the ledger. If the estate
  can't label consistently, the backtest says so.

**Step 3 — replay.** The historical alert stream is fed through the
gate **offline, in chronological order, with episode semantics intact**:
dedup, flap-debounce (re-fire inside the flap window reopens the same
episode; resolved-then-refired is a *fresh* episode — ADR-001), resolve
events, silences the humans applied (the gate sees what the humans
saw). The gate runs with the **exact production configuration frozen**
at replay start — model version pin, thresholds.json version,
allowlist snapshot, calibration-fit version — all pinned in the ledger
header. (A backtest that "fixes" the gate mid-replay proves nothing.)
Late-arriving Jev answers in the replay become shadow decision events
(ADR-010) exactly as in production.

**Step 4 — compare against outcomes, not against pages.** The naive
comparison ("did the gate agree with the human page?") measures
conformity, not correctness. The backtest's metrics:
- **Primary: false-suppress count on postmortem-confirmed real
  SEV1/SEV2. The bar is ZERO.** Not "low." Zero. This is the one
  non-negotiable number in the entire deployment path (03 §Stage 1).
- Secondary: suppression precision on labeled noise (of the alerts
  the humans retrospectively called noise, how many did the gate
  suppress?) — the ROI evidence.
- Secondary: page-vs-suppress divergences on real incidents — the
  "gate would have paged what humans didn't" list (these are usually
  good news: missed pages the estate didn't know it missed).
- Reported, not hidden: ambiguous-count, extraction window, label
  provenance summary, config freeze manifest.

**Step 5 — the false-negative hunt.** Every backtest miss is
investigated like an incident: root-caused to a *mechanism*
(fingerprint collision across services? topology error? model
overconfidence on a novel pattern? guardrail gap? stale allowlist
attestation? — the named failure modes from the Law 6 pre-mortem get
hunted explicitly, not just reasoned about), then either fixed in the
gate (with the fix version recorded) or **explicitly accepted in
writing by the buyer's incident review** with a named residual risk.
Every miss becomes a regression case with a permanent ID; the
regression corpus is committed and CI fails the build on any
regression against it. The corpus grows with every backtest at every
buyer — this is the compounding moat: no competitor inherits the
corpus.

### c.2 What the buyer sees

The **signed Backtest Ledger** (§(f), artifact 3): every historical
SEV1/SEV2, one row each — what the humans did, what the gate would
have done, agreement or a named, root-caused, fixed-or-accepted
explanation. Plus the regression corpus manifest: "these are the 214
incidents the gate is now contractually forbidden from getting wrong."
The ledger is signed by the **buyer's incident review board**, not by
the vendor — the attestation is theirs.

### c.3 Stage 0 → Stage 1 transition (exact)

Advance when ALL hold:
1. ≥30 days of shadow **or** ≥1,000 alert decisions, whichever is
   larger.
2. **Zero unexplained divergences on SEV1/SEV2.** Any SEV1/SEV2
   divergence has a written, buyer-accepted explanation, or the stage
   does not advance. (One unexplained SEV1 divergence holds the gate
   indefinitely — this is stated in the contract.)
3. Calibration within 5pp at every confidence band with N ≥ 30.
4. Gate p99 decision latency within the race-to-page budget on the
   buyer's real traffic shape.

---

## (d) Stage 2 — canary: the state machine

Only after shadow + backtest does Sentinel touch the paging path — in
four sub-stages. The principle throughout: **the human stays in the
loop until the evidence says otherwise, and the loop can be re-entered
with one flag.** (Law 1: the override is not a bottleneck to optimize
away; it is the trust mechanism itself.)

### d.1 States and exact transition conditions

```
SHADOW(0) ──T01──► BACKTEST(1) ──T12──► ADVISORY(2a) ──T2ab──► LOW-SUPPRESS(2b)
                                                                    │
                                              ┌─────────────────────┘
                                              ▼
                                        FULL-GATE(2c) ──T2cd──► AUTONOMOUS(2d, optional)
```

**T01 (shadow → backtest):** the four exit criteria of §c.3. Evidence:
the Shadow Report archive (≥4 weekly reports) + the calibration table.
Signed by: Sentinel (evidence complete) — no buyer signature required
to *look at history*.

**T12 (backtest → advisory):** ALL hold —
1. False-suppress count on postmortem-confirmed SEV1/SEV2 = **0**
   across the full window.
2. False-suppress on SEV3+: within buyer-defined tolerance (suggested
   ≤0.5%), each instance root-caused with a mechanism.
3. **Backtest Ledger dual-signed** (Sentinel ran it; buyer's incident
   review board accepts the zero-miss attestation) — §(f) artifact 3.
4. Regression corpus committed; CI gate green on it.
Evidence: the signed ledger + corpus manifest hash. This is a **Type 1
transition** (Law 3): it authorizes the first write credential
(annotation API key, §d.2). Type 1 rigor: written decision record,
named dissent captured, both signatures.

**T2ab (advisory → low-urgency suppression):** ALL hold —
1. ≥30 days of advisory operation.
2. Annotation open-rate tracked and reported (the 03 bar: zero on-call
   complaints about annotation quality; ≥90% "what a good senior would
   have said" in sampled SEV1/SEV2s, judged by the buyer's incident
   review).
3. **The suppressible scope is contractually enumerated and signed:**
   the (services × urgency ceiling × time windows) matrix, each cell
   explicitly in or out — §d.2. No scope, no suppression.
4. Calibration certificate current (validity window unexpired);
   regression corpus still green.
Evidence: advisory effectiveness report + signed scope matrix. Type 1.

**T2bc (low-suppress → full gate):** ALL hold —
1. ≥60 days of low-urgency suppression.
2. **Zero** suppressed alerts retrospectively judged "should have
   paged" (the false-suppress definition of §d.3 — any one holds the
   transition and triggers auto-revert instead).
3. One-click override usage <5% of suppressions (high override usage
   means the gate is wrong, not that humans are cautious).
4. On-call sentiment neutral-to-positive (the one-tap signal, §d.3).
5. Sustained noise reduction reported; MTTA/MTTR neutral or improved.
6. **The buyer's incident review board votes to continue** — explicit
   vote, minuted. The board's authority is absolute and contractual.
Evidence: suppression report + board vote record. Type 1.

**T2cd (full gate → autonomous):** the buyer's explicit, signed risk
acceptance — **and Sentinel never proposes it.** The product is
complete at 2c. Most buyers keep the override forever; both are
correct. (If a buyer asks, the answer is a fresh Type 1 decision
record, not a toggle.)

**Rollback transitions (automatic, no human judgment required):**
- **Any confirmed SEV1/SEV2 false-suppress** ⇒ immediate auto-revert
  one stage (2c→2b, 2b→2a, 2a→1). The revert is a config-flag flip
  executed by the gate's own watchdog path — it does not depend on the
  gate being healthy (Fence 7). It pages the Sentinel platform admin
  and opens a postmortem. From 2b→2a, "revert" means: suppression
  stops *now*, annotations continue. The paging path is never left in
  an undefined state — every stage's revert target is a previously
  evidenced stage.
- Page-divergence rate (gate disposition vs human-override-corrected
  outcome) above the buyer-set threshold over a rolling 7 days ⇒
  revert one stage.
- Gate-added paging-path latency p99 above budget for >15 minutes ⇒
  **bypass, not stage revert**: the gate removes itself from the path
  and the estate pages everything (fail-open). The bypass is
  mechanical (the race-to-page timer's big brother).
- Any single confirmed SEV1/SEV2 false-suppress at 2c **auto-reverts
  to 2b and re-opens the Stage 1 backtest** on the incident — the
  regression corpus gains a case, and T2bc must be re-earned from
  zero. There is no "we fixed it, carry on."

**Manual (one action, no deploy):**
- **The kill switch**: one flag — API call, CLI command, and a big red
  button in the admin console, all three — that bypasses the gate
  entirely. The paging path degrades to **exactly yesterday's stack**
  (the escalation policies, urgency model, rotations — all unchanged;
  only the trigger got dumber again). It works **out-of-band**
  (Law 7 infra: the estate being down cannot take the kill switch
  with it — the switch lives on a path that does not depend on the
  estate's network, and on the gate host as a local flag file as the
  last resort), and it is **tested quarterly under game-day
  conditions with the buyer witnessing**, the result reported to the
  buyer (§(f) artifact 5). A kill switch that has never been pulled is
  a rumor.
- One-tap "this page was wrong / this suppression was wrong" on every
  disposition. Sustained negative signal ⇒ human review; stage hold
  or revert at the buyer's discretion.
- Incident review board veto at any time, for any reason, no
  justification required. Stated in the contract. The buyer holds the
  bigger stick — that is the design, not a concession.

### d.2 The sub-stages in operation

**2a — Advisory annotation.** Sentinel attaches its disposition —
verdict, confidence, evidence, recommended action — to every incident
as a **note/annotation** via the existing stack's API (PagerDuty
incident notes; Opsgenie alert notes; Alertmanager has no annotation
primitive — for AM estates the annotation lands in the estate's
chatops/incident channel via the existing bot, or is skipped with the
gap documented). This is the **first write credential ever
provisioned**: a write-scoped API key, and its provisioning is the
T12 Type 1 decision. No routing change. No suppression. The on-call
sees on every page: what the gate thinks, why, what it recommends —
with the counterfactual ("at your 0.85 this stood down; at 0.70 it
paged") because a bare 0.87 does not survive 3 AM cognition (Prism §5).

**2b — Low-urgency suppression with receipts.** The gate moves into
the paging path **for the signed scope only**. Suppressed alerts enter
the **muted-not-dropped** state (ADR-007): visible on the dashboard,
receipted (§(f) artifact 2), auditable, never paged — with a
**one-click "page me anyway" override** on every suppressed item, and
**automatic re-escalation**: if the episode worsens — severity upgrade,
new correlated critical, or the same fingerprint firing outside the
scope — the suppression is voided and the page fires immediately.
(Episode-worsening detection runs on the full event stream, including
events outside the canary scope — the correlator sees everything even
when the gate may only act inside the scope. The pre-mortem exists
because this sentence is easy to get wrong.)

**Scoping controls (the SRE holds these, §(e)):** service-by-service
(service checkboxes — backed by PD service-scoped webhooks / Opsgenie
team integrations / AM route matchers), urgency ceiling (nothing above
the signed urgency ever suppresses — SEV1/SEV2 are out of scope at 2b
*by construction*, not by confidence), and **daylight-only windows**
(suppression active only inside the org's defined business hours in
their primary timezone; outside the window everything pages — fail-open
by clock). Each scope is a versioned, signed config; changing it at 2b+
is Type 1 (two-person, audit-logged).

**2c — Full pre-page gate.** The gate fronts the entire paging path.
Human override and kill switch cover everything. Escalation policies,
urgency model, rotations — unchanged (the fences hold); only the
*trigger* got smarter. The invariants that never relax: storm
aggregates can never suppress (ADR-016); security-category fingerprints
are banned in code (ADR-017); the silence floor is versioned, audited,
two-person-changed (ADR-019); every lock's freshness proof is checked
(ADR-014) — stale ⇒ page.

**2d — Autonomous (optional, never pushed).** The gate's decisions
stand unless appealed. Documented here so the state machine is
complete; not sold, not demoed, not defaulted.

### d.3 How a false-suppress is detected (the definition matters)

A suppressed episode is a **confirmed false-suppress** if ANY hold:
1. A human paged on the same alert_key/fingerprint within 24h of the
   suppression via the one-click override or the normal paging path
   (the override *is* the detection instrument).
2. A postmortem tagged the incident SEV1/SEV2 with an onset inside the
   suppression window (retrospective detection — the label pipeline
   replays postmortem tags against the suppression log weekly).
3. The episode auto-escalated (severity upgrade, correlated critical)
   while suppressed and the void fired — counted as a *near-miss*
   (the void worked) unless a human judges it should have paged
   earlier, in which case it promotes to (1).
"Suspected" false-suppresses (on-call grumbling without a page) go to
the sentiment signal, not the auto-revert trigger — the revert trigger
must be unambiguous, or it will fire on noise and teach the org to
ignore it (Law 2: alarms that cry wolf get disabled).

---

## (e) The SRE's view at each stage

### What they see

| Stage | The SRE sees | The control they hold |
|---|---|---|
| **0 shadow** | The weekly Shadow Report in their inbox (and in the platform UI). The divergence list, with links into *their* PD/Opsgenie. Nothing in their stack changed; the report footer proves it (credential inventory: none). | Revoke the tap in their own console, any time, zero coordination. Ask for the payload-completeness line to be explained. Nothing else is asked of them — **week one asks for fifteen minutes of setup and zero behavior change.** |
| **1 backtest** | The Backtest Ledger: every historical SEV1/SEV2 as one row — "here is the outage your team still talks about; here is what the gate would have done." The regression corpus manifest. | They sign the ledger (their incident review board). They define the severity taxonomy. They accept-or-reject each miss's root cause. **The attestation is theirs; the vendor cannot self-certify.** |
| **2a advisory** | On every page: the gate's verdict, confidence, evidence, recommended action, and the counterfactual — as a note on the incident they already opened. | The one-tap "wrong" signal on every annotation. The scope matrix is not yet live (nothing suppresses), but they see it in draft. |
| **2b canary** | Suppressed low-urgency items in the muted-not-dropped view — visible, receipted, each with one-click "page me anyway." The override-rate trend, week over week. | **Service-by-service scoping** (checkboxes), **urgency ceiling**, **daylight-only windows**, one-click override per item, the kill switch, board veto. The scope matrix is versioned and signed — they see exactly what the gate may touch. |
| **2c full gate** | Fewer, better pages. Every suppression carries its receipt; the river shows the full event projection. MTTA/MTTR on the dashboard. | Everything from 2b, now covering all traffic. The kill switch (quarterly-drilled, witnessed). The board's absolute veto. |
| **2d** | (Their choice, if ever.) | Unchanged — the override never disappears unless they sign it away. |

### The UI copy — time-to-value, not total replacement

The standing copy rule: **every screen answers "what changed about my
stack?" first, and the answer is usually "nothing."** Sample copy,
verbatim for the design:

- Shadow week 1, empty state: *"Nothing changed. Nothing can change.
  Connect the read-only tap (15 minutes, one additive config) and your
  first Shadow Report arrives in 7 days — with your numbers, your
  divergences, your evidence. You will know what we would have done
  differently before we are allowed to do anything."*
- Divergence list header: *"13 times we disagreed with your team this
  week. Click any of them. The evidence we used is on the right; your
  incident is on the left. Tell us which of us was right — your call
  becomes a regression case."*
- 2b scope screen: *"The gate may suppress inside this box and nowhere
  else. Draw the box: services, urgency ceiling, hours. Outside the
  box, everything pages — including at 3 AM, including on holidays,
  including when we're down. The box is versioned; changing it takes
  two of your people, not one of ours."*
- Kill switch screen: *"One action. No deploy. When pulled, your stack
  becomes exactly yesterday's stack — the gate removes itself from the
  path. Last drilled 2026-10-01 with your team watching: 41 seconds to
  bypass. Next drill: 2027-01-01."*
- The Standing Verdict Strip (Prism, every screen): one plain-language
  sentence, e.g. *"Shadow mode: watching 4,212 alerts/week, changing
  nothing. 13 disagreements need your review."* The accept test: the
  operator who reads only the strip acts correctly.

**What "time-to-value not total replacement" means, concretely:**
value lands in week 1 (the noise named, the divergences inspectable,
the calibration printed) while the stack is byte-identical. The buyer
never signs a rip-and-replace. They sign a *reading* of their own
alerting — and the reading is so useful that the suppression, when it
comes, feels like the obvious next step rather than a leap. The demo
(Sunday 2026-10-04) shows exactly this: a tap connected live, a Shadow
Report generated from real traffic, the divergence list clicked
through. The demo's climax is not a suppression — it is the thirteenth
divergence, explained.

---

## (f) The five trust-moat artifacts — designed data artifacts

Each artifact: schema in prose, who writes, who signs, where it lives.
(Synthesis §5 names the five; here they become buildable.)

### 1. The Divergence Ledger

- **Schema (prose):** append-only, hash-chained entries. Each entry:
  `ledger_seq` (monotonic), `event_ref` (alert_key + env+cluster-
  namespaced fingerprint, ADR-017), `occurred_at`, `gate_disposition`
  (verdict, confidence, per-lock leg results with freshness proofs,
  `threshold_counterfactual` per ADR-023), `human_disposition` (what
  the stack actually did — paged / silenced / ignored — with the
  estate's own event id), `divergence_type` (suppress-vs-page |
  page-vs-suppress | evidence-disagreement), `evidence_refs` (links:
  gate evidence bundle, raw alert payload, estate incident),
  `severity_at_occurrence`, `status` (open | explained-buyer-accepted
  | root-caused-fixed | regression-case), `status_history`
  (append-only transitions with actor + timestamp). Hourly checkpoints
  hashed to the customer-controlled sink (synthesis §4 audit design).
- **Who writes:** the shadow gate (machine). **Who signs what:** the
  buyer accepts explanations — each `explained` status carries the
  accepting reviewer's identity and the written explanation. The
  vendor cannot mark its own homework.
- **Lives:** the event log (ADR-011); the weekly Shadow Report is a
  projection over it. Retention per the O-1 RFC (open).

### 2. Suppression Receipts

- **Schema (prose):** emitted once per suppression, durable, immutable.
  Fields: `receipt_id`, `alert_key`/fingerprint, `suppressed_at`,
  `gate_version` + `config_version` (thresholds, allowlist snapshot,
  calibration-fit version, model pin — the full freeze manifest, so a
  receipt is interpretable years later), the three lock legs with
  their proofs (probability: reported 0.00 + fit p̂_upper + fit date;
  confidence: value + fatigue-ratchet state; allowlist: attestation
  tuple who/when/evidence/TTL), `threshold_counterfactual` (the
  disposition at each configured preset), `what_would_un_suppress`
  (enumerated: severity upgrade, correlated critical, human override,
  kill switch, scope exit, daylight-window end), `appeal` (the
  one-click override link, bound to the operator's identity when
  used), `links` (alert payload, incident, the backtest rows for this
  fingerprint, the divergence-ledger entries that admitted it to the
  allowlist — the full pedigree chain).
- **Who writes/signs:** the gate signs at emission (machine signature
  over the receipt hash). A used override is countersigned by the
  operator. In a postmortem, "why didn't we get paged?" is answered by
  producing the receipt — seconds, not a war-room debate.
- **Lives:** the event log; surfaced in the muted-not-dropped view
  and attached to the incident record.

### 3. The signed Backtest Ledger

- **Schema (prose):** header (window, extraction timestamp,
  `labeling-rules` version, config-freeze manifest: model pin,
  thresholds version, allowlist snapshot, fit version; missing-data
  caveats verbatim) + one row per historical SEV1/SEV2:
  `incident_id`, `date`, `estate_severity` (+ provenance: which field),
  `postmortem_ref`, `human_handling` (paged/acked/resolved timeline),
  `gate_disposition_on_replay`, `agreement` | `miss`, and for misses:
  `miss_mechanism` (root-caused, named), `miss_disposition`
  (fixed-in-gate-version-X | buyer-accepted-residual-risk with the
  named risk), `regression_case_id`. Plus the corpus manifest
  (all regression cases, permanent IDs) and the inter-rater
  disagreement rate.
- **Who writes:** Sentinel runs the replay. **Who signs:** dual —
  Sentinel attests the replay was run under the frozen config;
  **the buyer's incident review board signs the zero-miss
  attestation.** Both signatures are required for T12. The hash of
  the signed ledger is pinned in the decision register (Law 3).
- **Lives:** with the buyer (their copy is the attested one) and in
  the platform; the auditors, the insurers, and the board get this
  document after the first suppressed-then-real incident that
  *doesn't* happen.

### 4. Published calibration

- **Schema (prose):** per named reference class, a certificate:
  `class` (e.g. "shadow decisions, org=buyer, prod, SEV3+"),
  `window`, `N`, observed `P(true SEV1 | suppress)` — decision
  calibration, not distribution calibration (Oracle §7) — Wilson 95%
  bounds, the reference distribution description, `fit_version`,
  `validity_window`, `stale_check_date`. Certificates past their
  validity window are marked **STALE** automatically — never silently
  trusted (synthesis §4 data discipline).
- **Who writes:** the tuner/calibration pipeline (deterministic code —
  math-in-code boundary, synthesis §4). **Who signs:** the pipeline
  signs the certificate; the buyer's review acknowledges receipt
  weekly (in the Shadow Report). The one-year test: "show me the 412"
  — the buyer can demand the underlying 412 labeled decisions behind
  any number, and the pedigree chain (raw events → transforms →
  assumptions → named adversary, Ledger N1) produces them.
- **Lives:** the Shadow Report (weekly), the platform calibration
  dashboard, and pinned to each suppression receipt's config manifest.

### 5. The Kill-Switch Runbook

- **Schema (prose):** `trigger_paths` (API call, CLI command, console
  button, local flag file on the gate host — each with its auth and
  its expected latency), `out_of_band_channel` (the path that works
  when the estate is down — described, tested), `expected_behavior`
  ("the paging path degrades to exactly yesterday's stack" — with the
  verification procedure: after pulling, fire a test alert and confirm
  it pages through the *original* path), `drill_schedule` (quarterly
  game-day, buyer-witnessed), `drill_log` (date, who pulled, seconds
  to bypass, observed paging behavior, witness signature — append-only),
  and the **fallback runbook** for the I-2 case (standby direct-to-PD
  integration + external health watcher + monthly drill, ADR-018).
- **Who writes:** Sentinel ops. **Who signs:** Sentinel ops attest
  each drill; the **buyer witness countersigns**. The buyer holds a
  copy of the runbook inside their own incident docs — the runbook is
  a product feature, not an ops afterthought, and the buyer's copy
  means the switch survives *our* disappearance too (Law 2: the vendor
  is also a failure mode).
- **Lives:** the platform admin console (the big red button), the
  buyer's incident-docs repo, and the quarterly report to the buyer.

---

## Law-by-law review-bar mapping

- **Law 1 (global vs local):** the adoption surface is not an optimized
  funnel; it is the product strategy (proof engine first) made
  tangible. The global move is making *evidence generation* the
  deliverable, not faster provisioning.
- **Law 2 (eternal friction):** the tap assumes retries, duplicates,
  drifted integration configs, unsigned Alertmanager webhooks, and a
  dead shadow — each with a named handling (idempotent ingest,
  payload-completeness line, bearer tokens, "a dead shadow changes
  nothing"). The kill switch assumes the estate is down. The runbook
  assumes the vendor disappears.
- **Law 3 (Type 1 vs Type 2):** T12/T2ab/T2bc are Type 1 (written
  record, dual signatures, named dissent); the weekly report's *format*
  is Type 2 (it can iterate); the divergence list's *append-only-ness*
  is Type 1. The decision register pins the backtest hash.
- **Law 4 (five whys):** §0 — the buyer's fear is the Type 1 error, so
  the product is evidence, and suppression is what evidence permits.
- **Law 5 (Chesterton's fence):** we never ask the estate to repoint,
  remove, or reorder. The fence — audited escalation policies,
  compliance-blessed paging, runbook muscle memory — is addressed by
  not touching it until the evidence earns each step.
- **Law 6 (pre-mortem):** §(h) below, in concrete mechanical detail.
- **Law 7 (four constitutions):** SE — the shadow/live type boundary
  and the import-graph CI assertion; Infra — blast-radius-zero shadow,
  out-of-band kill switch; Data/AI — deterministic calibration,
  pedigree on every label, the model advises (2a) before it ever acts
  (2b); Product — the SRE's view (§(e)), the Standing Verdict Strip,
  copy that answers "what changed?" first.

---

## CREATIVE APPLICATION — the adoption surface as a growth engine

The shadow is not just a safe rollout. It is a machine that manufactures
the moat while the buyer watches. Five applications beyond the obvious:

1. **The noise autopsy as a free service.** The Shadow Report names
   which alerts are noise and why — with evidence — before Sentinel is
   allowed to suppress anything. Many buyers will fix their own
   alerting rules from the report alone. That is value delivered even
   if they never buy suppression — and it is the cheapest possible
   proof that the gate understands their estate. Paradox as strategy:
   the proof engine's best demo is making the buyer's alerting better
   for free.

2. **The divergence list as the SRE team's favorite reading.** Thirteen
   disagreements a week, each clickable, each about *their* alerts.
   The gate's mistakes are inspectable; the gate's wins are
   instructive. Handled right, the weekly report becomes a ritual —
   the team argues with the gate the way they argue in postmortems,
   and every argument they win becomes a regression case that makes
   the gate smarter. The buyer is training our moat and enjoying it.

3. **The override flywheel.** In 2b, every one-click "page me anyway"
   is labeled data: it re-fits the calibration, tightens the Wilson
   bounds, and shrinks the suppressible set exactly where the humans
   disagree. The buyer watches the override rate fall week over week —
   4.1%, 2.8%, 1.3% — and that falling curve is the most persuasive
   sales artifact in the company because *they* generated it. Trust as
   a measurable, decreasing function.

4. **The kill-switch drill as a trust ritual.** Invite the buyer's SRE
   to pull the switch during game day — to break us on purpose and
   watch the stack degrade to yesterday in 41 seconds. No slide deck
   has ever produced the feeling of pulling the red button yourself
   and watching the pages flow through the old path untouched. The
   drill is the demo that the demo cannot fake.

5. **The ledger as the unreplicable asset.** Every shadow week, every
   backtest, every override at every buyer grows two corpora — the
   Divergence Ledger and the regression corpus — that no competitor
   can buy, scrape, or synthesize. They are earned alert by alert, on
   real estates, with buyer signatures. The model is a commodity; the
   ledger is the moat. And the moat compounds: each new buyer's
   backtest runs against the corpus all previous buyers paid for in
   divergences.

Boundary, stated once: **a buyer's ledger never trains another
buyer's gate.** The corpus carries mechanisms and regression cases
(anonymized, structural); raw alert payloads, service names, and
incident details never cross the org boundary. The moat compounds on
patterns, not on data. This is contractual, not just architectural.

---

## PRE-MORTEM — "the shadow report convinced them and the first canary week suppressed a real SEV2 — what exactly failed?"

It is October 2027. The shadow ran clean for five weeks: 99.1%
page-agreement, zero SEV1/SEV2 divergences, calibration within 2pp.
The backtest replayed eleven months: zero false-suppress on 63
postmortem-confirmed SEV1/SEV2s, ledger dual-signed. Advisory ran
thirty days; the on-call called the annotations "what a good senior
would have said." The scope matrix was signed: 2b covers low-urgency
alerts on `payments-api` and `cache-eu`, daylight hours IST, nothing
above urgency low, SEV1/SEV2 out of scope *by construction*.

On Thursday of the first canary week, at 14:02 IST, the gate suppressed
an alert on `payments-api`: `HighErrorRate`, urgency **low** in
PagerDuty, confidence 0.97, probability lock clean (reported 0.00, fit
p̂_upper 0.0011, fit fresh), allowlist attestation T-118 valid. The
receipt was perfect. At 14:47 the buyer's incident review declared a
SEV2: the error spike was a partial payments outage. The page that
should have fired at 14:02 fired at 14:47, when a human noticed the
dashboard. Forty-five minutes of burn. The postmortem named Sentinel.

**What exactly failed — the mechanism, not "the model was wrong":**

The alert was *correctly* low-urgency at 14:02 — by the estate's own
urgency field. At 14:19, the on-call engineer, watching the same
dashboard, raised the PagerDuty incident's priority from P4 to P2 —
a **`incident.priority_updated` webhook event**. Sentinel's canary tap
was subscribed to `incident.triggered`, `incident.acknowledged`, and
`incident.resolved` — the three events the shadow needed, carried over
unchanged into 2b. **`priority_updated` was never subscribed.** The
severity upgrade entered PagerDuty and never entered Sentinel. The
suppression, valid at 14:02 against a low-urgency alert, stood
unchallenged at 14:19 against what was now a SEV2 — because the gate
never saw the world change.

Two compounding failures made it fatal rather than embarrassing:

1. **The scope-exit check read the wrong stream.** The 2b design said
   "severity upgrade ⇒ suppression voided." The implementation checked
   severity upgrades *on events Sentinel ingested*. The estate's source
   of truth for severity (the PD priority field) moved on an event
   type outside the subscription. The void logic was correct; its input
   contract was incomplete. The triple lock never failed — the lock
   was guarding a door while the wall moved.

2. **The shadow→canary parity check didn't exist.** Nothing in T2ab
   verified that the canary's event subscription was a superset of the
   event types the estate's stack actually emits. The shadow only
   needed three event types to *observe*; the canary needed the full
   lifecycle to *act safely*. The stage transition certified the
   evidence (calibration, agreement) but not the *input contract*.

**The fixes (designed against the answers, per Law 6):**

- **F1 — subscription parity as a stage gate.** T2ab gains a mechanical
  check: enumerate every event type the estate's stack emitted in the
  shadow window (from the Divergence Ledger's `human_disposition`
  event ids — the ledger already contains the ground truth of what the
  stack does); the canary subscription must cover all of them, or the
  transition is blocked with the missing types named. For PagerDuty
  estates the mandatory set is
  `triggered + acknowledged + resolved + escalated + priority_updated`;
  for Opsgenie, Create + Acknowledge + Close + Priority-update-equivalent
  custom actions; for Alertmanager, firing + resolved (the AM payload
  has no priority primitive — the gap is documented per estate, and
  for AM estates the void also fires on any *new* alert with a
  correlated fingerprint within the episode window).
- **F2 — the void reads the estate's source of truth, not the tap.**
  In 2b+, a periodic reconciler (every 60s, off the hot path) re-reads
  the current severity/urgency of every *currently suppressed* episode
  via the read-only API and voids any suppression whose estate-side
  severity has risen above the scope ceiling. The webhook is the fast
  path; the reconciler is the backstop. Belt and suspenders, because
  the fast path just proved it can miss.
- **F3 — scope is evaluated continuously, not at suppression time.**
  A suppression is a *standing* decision re-validated against the
  current scope on every correlated event and every reconciler tick —
  not a one-time verdict. The receipt gains a `scope_valid_until`
  field; expiry without re-validation pages.
- **F4 — the game-day drill for this.** The quarterly drill gains a
  chaos case: mid-suppression priority escalation on a canary-scope
  alert, verifying the void fires and the page goes out through the
  original path. The drill log records seconds-to-void.

**The honest footnote the pre-mortem demands:** the backtest could not
have caught this. The historical replay fed the gate the alert stream
*as recorded* — and the recording contained the priority change (it was
in the PD log entries). But the replay compared *dispositions at alert
time*, and at alert time the disposition was correct. The failure was
not in the gate's judgment but in the *live input contract* — which is
exactly the class of failure a replay cannot see, because the replay
uses the complete record while production uses the subscribed subset.
This is why F1 enumerates from the ledger's ground truth, not from the
gate's inputs. The pre-mortem's real lesson: **evidence about the
gate's judgment does not transfer to the gate's plumbing** — and the
stage gates must certify the plumbing separately.

---

## Open items

- **O-1 (retention):** the Divergence Ledger and receipt store inherit
  the audit-log retention question — flagged in synthesis §3.7, needs
  the RFC before the first design partner. The Shadow Report archive
  (weekly projections) is the minimum viable retention even under the
  strictest policy.
- **Opsgenie plan-gating:** outgoing webhook integrations are
  plan-dependent; the onboarding checklist verifies the estate's plan
  before promising the tap. (JSM Operations estates need Premium or
  above for outgoing webhooks — verified 2026-10-03.)
- **Alertmanager annotation gap:** AM has no incident-note primitive;
  the 2a annotation path for AM-native estates (chatops bot vs skip
  with documented gap) is a per-estate decision, recorded in the scope
  matrix.
- **ADR-006 status:** this document elaborates ADR-006's evidence
  standards; the ADR itself remains PROPOSED until Forge's review and
  Aditya's verdict. Nothing here unfreezes the frozen spec.

---

*LANE 4 (Pager) — design complete. No code written, no branches switched,
no commits, no pushes. Reviewed against the seven laws; the pre-mortem's
four fixes (F1–F4) are folded into the design above as stage-gate and
canary requirements.*
