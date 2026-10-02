# 08 — Proof-Demo Narrative: The Interface That Sells Evidence

**LANE 6 (Prism) · principal fix-designs wave · 2026-10-03**
**Status:** DESIGN ONLY. No code, no schema changes, no commits.
**Branch:** `lane/principal-fix-designs` (do not switch, do not push).
**Judged against:** `design/principal/00-laws.md` (the seven laws).
**Builds on:** `11-synthesis.md` §4 (platform tier), §3.4 (counterfactual
receipt), `06-operator-empathy.md` (the 3 AM human), `12-adr-deltas.md`
ADR-023 (receipt contract addition), `03-deployment-path.md` (stages,
divergence ledger, backtest bar).
**Designed AGAINST:** `PLATFORM_ARCHITECTURE.md` §§5–6 (frozen read
API/SSE contracts — this document proposes no new endpoints and no new
tables; where the presentation needs fields the frozen contract does not
carry, the need is flagged to Forge's event-log RFC, not smuggled in).

**Thesis.** Sentinel is a proof engine first and a suppression engine
second (synthesis §4). The demo is therefore not a tour of features; it is
a **designed narrative of trust formation**: the SRE arrives a skeptic,
watches the machine fail first, sees its disagreements itemized with
evidence, hunts its false negatives personally, checks the signed ledger,
touches the canary controls, and reads one suppression receipt — in that
order. The order is the design. Every number on every screen traces to the
append-only event log; every screen opens with a Standing Verdict Strip;
every surface is specified from its dead state upward.

---

## §0 — The demo contract (beat 0: before anything is shown)

Law 2 names the adversary the demo itself is most vulnerable to: **the
demo's own persuasiveness**. A slick demo teaches the SRE to trust the
presentation, not the evidence. So the demo opens with a contract screen,
not a product screen. It is static (reads no endpoint), unskippable, and
exactly this copy:

> **This is a demonstration, not your system.**
> Everything you are about to see runs on **synthetic data** and a
> **recorded storm** (recorded 2026-10-01, mock engine). The *evidence
> patterns* — divergences, receipts, the backtest bar — are real shapes;
> the *data* is not yours and proves nothing about your fleet.
> Nothing in this demo can page anyone. The first thing we will show you
> is the machine being wrong.

**Strip for beat 0:** *"Demo data only — recorded storm, mock engine.
Nothing here pages anyone."*

**Acceptance:** the SRE can recite, unprompted, the demo's three limits
(synthetic data, recorded storm, no paging) before beat 1 begins. If they
cannot, the demo failed at beat 0 and no later beat counts.

**Why this is the opening:** 06 §2.5 — the trust prior is set in the first
minutes and the 3 AM self spends it down. A demo that opens with
capability teaches "this machine is impressive." A demo that opens with
its own limits teaches "this machine shows me its boundaries" — which is
the belief the 3 AM suppression needs underneath it. (PLATFORM_ARCHITECTURE
Law 4: demo data presented as production data is a trust incident. This
screen is the enforcement.)

---

## §1 — The demo arc (the SRE's journey, screen by screen)

Each beat states: what the SRE does, the exact screen state, the strip,
the frozen endpoint(s) read, the exit condition (what lets them move on —
usually *closing the tab*).

### Beat 1 — Connect: the shadow tap (START screen)

**What the SRE does:** pastes the 50-line webhook snippet (point the PD
webhook at Sentinel *alongside* the existing stack), enters the BYOK key
once, fires the guided synthetic storm.

**Screen state (exact):** `design/screens/onboarding.md` flow, steps
1–2–3, with the shadow-mode banner pinned above the fold:

> **SHADOW MODE — read-only tap.** Sentinel watches a copy of your alert
> stream and logs what it *would* have done. Your paging path is
> byte-identical: nothing here can suppress, delay, or alter a page.
> The storm below is synthetic.

The deliberate-wrongness beat fires here (06 §2.5): the synthetic storm
contains one **designed flip** — an alert the gate first suppresses and
then, on re-ask, pages (flip_observed). The river shows it before any
capability is claimed. The SRE watches the machine change its mind about
itself, in the open, at minute 6 of a 15-minute onboarding.

**Reads:** `GET /api/decisions?limit=50` + `SSE /api/stream` (the storm
lands in the river live).

**Strip:** *"Shadow mode — read-only tap. Your paging is unchanged.
Nothing here can suppress a page."*

**Exit condition:** the SRE has seen one flip and can say what it means.
("The machine showed me a failure before asking for trust.")

### Beat 2 — The first weekly Shadow Report

**What the SRE does:** opens the report — a generated weekly artifact, a
projection over the event log (shadow-mode `decision_made` events joined
with the recorded production dispositions from the read-only tap).

**Screen state (exact):** report header, then the numbered divergence
summary, then calibration and latency cards:

> **WEEK 1 SHADOW — team: payments**
> 1,412 alerts observed · **96.3% agreement** (shadow disposition =
> production action) · **13 divergences — all listed below** ·
> **0 unexplained divergences on SEV1/SEV2** · report generated
> 2026-10-03 09:00 IST · data: shadow week 2026-09-26 → 2026-10-03

The divergence summary is not a count; it is the first 13 rows of the
numbered list (§3), SEV1/SEV2 pinned first. Each row carries evidence
links (fingerprint → audit drawer). The calibration card carries its
denominator on the card (`n=1,412 · shadow 7d`), per 06 §2.2.

**Reads:** `GET /api/decisions?team=payments&from=…&to=…` (river feed as
report input), `GET /api/calibration?team=payments`. The report itself is
a generated artifact (batch projection); the platform serves the
rendered page, not a new endpoint. If a dedicated report endpoint is
wanted, that is a Forge contract addition — flagged, not assumed.

**Strip:** *"Week 1: 1,412 alerts, 96.3% agreement, 13 divergences all
listed below. Zero unexplained on SEV1/SEV2."*

**Exit condition:** the SRE scrolls past the 96.3% to the 13. (The
agreement number is the machine's grade; the divergences are the SRE's
work. The screen's layout teaches that ordering.)

### Beat 3 — The divergence moment (the centerpiece)

**What the SRE does:** clicks divergence **D-007** — the one SEV2 where
shadow and production *disagreed*.

**Screen state (exact):** the divergence renders not as a row but as an
**argument transcript** — two positions, each with its cited evidence,
the SRE as judge. Three columns:

```
┌─ PRODUCTION (what happened) ─┬─ SHADOW (what it would have done) ─┐
│ DISPOSITION: PAGED            │ DISPOSITION: SUPPRESS                │
│ 03:12:04 · SEV2 · payments   │ flap-debounce · P(p1)=0.00 ·        │
│ Acked 4m · resolved 38m       │ conf 0.93 · attested allowlist       │
│ Evidence:                     │ Evidence:                          │
│ · PD incident #INC-8841       │ · flap timeline: 3 flaps / 4 min    │
│ · postmortem: deploy-churn    │ · change-window marker (deploy      │
│   cascade, self-cleared       │   #d-5918, same window)             │
│ · 14 sibling alerts, same     │ · 2 siblings suppressed this week, │
│   window, all paged           │   none became SEV1/2                │
└───────────────────────────────┴────────────────────────────────────┘
STATUS: UNREVIEWED — needs your eyes.
[ Machine was right ] [ Human was right ] [ mark reviewed ]
```

The evidence for each side is cited *like a court record*: every claim
has a deep link (incident, timeline, allowlist attestation tuple with
who/when/TTL per synthesis §3.2). The SRE does not read a summary; they
read two argued positions and render a verdict. The one-click review
buttons resolve the divergence into the ledger (MACHINE-RIGHT /
HUMAN-RIGHT) — this is how the Divergence Ledger gets written, one human
judgment at a time.

**Reads:** `GET /api/decision/<id>` (shadow decision row, full probs +
`threshold_counterfactual` per ADR-023), `GET /api/decisions?fingerprint=…`
(the alert's history — "2 siblings suppressed this week"), and the
recorded production disposition from the shadow tap's production-disposition
record (presentation need → Forge event-log RFC, ADR-011; flagged in §9
open questions).

**Strip:** *"D-007: production paged, shadow would have suppressed.
SEV2, deploy-churn cascade. Unreviewed — the evidence is below; you are
the judge."*

**Exit condition:** the SRE renders a verdict on D-007 (either button).
The demo does not proceed until they have judged one divergence —
because the product's trust model *is* the human judging divergences.

### Beat 4 — The backtest ledger

**What the SRE does:** opens the signed ledger — 6–12 months of the
buyer's own incident history, replayed through the gate offline.

**Screen state (exact):** an immutable table. Every postmortem SEV1/SEV2
is a row: incident id, date, postmortem severity, gate disposition on
replay, match/mismatch. Above the table, the living counter (§3):

> **0** false-suppresses · **214** SEV1/SEV2 · 11 months replayed ·
> **signed 2026-10-03** · attestation `sha256:9f2c…a41d` · [verify ↗]
> *(customer-controlled sink — the customer holds the seal, per
> synthesis §4 security)*

A mismatch row — if one ever exists — renders lit-amber, never hidden,
with the full evidence drawer one click away. The ledger is signed and
immutable: the demo's copy is labeled synthetic; the design partner's
copy is signed over *their* history.

**Reads:** the ledger is a generated signed artifact; underlying reads
are `GET /api/decisions` (replay decisions) joined with `outcomes`
(Ledger's join). Served as an artifact page.

**Strip:** *"Backtest: 0 false-suppresses on 214 SEV1/SEV2 across 11
months. Signed — verify the signature, not our word."*

**Exit condition:** the SRE clicks [verify]. (Trust the artifact, not
the vendor: the signature is the sentence made checkable.)

### Beat 5 — Canary controls

**What the SRE does:** opens the stage panel — the deployment state
machine 0 → 1 → 2a → 2b → 2c → 2d (03-deployment-path), current stage
highlighted.

**Screen state (exact):** stage 2b active:

```
STAGE 2b — low-urgency suppression with receipts
Suppressing: SEV3+ only · 0 overrides this week · 41 receipts issued
Kill switch: tested 12d ago (quarterly drill due in 78d) [test now]
Auto-revert: ANY confirmed SEV1/SEV2 false-suppress ⇒ revert to 2a,
              page the platform operator. (armed)
Stage change: two-person signed request → audit-logged. [request change]
```

Every control is shown with its guard: suppression-rate SLO + watchdog,
30-day re-validation clock, conf floor 0.85 (ADR-022). The kill-switch
button is present and testable — a kill switch that has never been pulled
is a rumor (synthesis §5.5). **No control on this screen writes
directly**: stage/threshold changes are two-person signed *requests*
(ADR-022) producing audit-logged change artifacts. The frozen read API
has no writes; the canary screen honors that — the `[request change]`
affordance opens the signed-request flow, it does not mutate config.
(The request flow's shape is a Forge + Vault decision — flagged in open
questions.)

**Reads:** `GET /api/analytics/noise?window=24h` (suppression counts,
override counts), `GET /api/decisions?action=suppress&…` (receipts).
Stage state is a versioned config artifact read by the platform tier.

**Strip:** *"Canary 2b: suppressing low-urgency only. 0 overrides this
week. Kill switch tested 12 days ago."*

**Exit condition:** the SRE runs the kill-switch test (in the demo: a dry
run against the mock engine) and watches the strip change to "tested
just now."

### Beat 6 — The first suppression receipt

**What the SRE does:** back on the river, clicks one dim SUPPRESS row —
a suppression they slept through.

**Screen state (exact):** the drawer opens in the locked reading order
(06 §4.3): verdict → reason → counterfactual → confidence-vs-typical →
raw. The counterfactual receipt (§5) is the drawer's third section, on
the row's second line even before the drawer opens. At the drawer's foot:
`[page me anyway]` (one-click appeal — pages through the normal path,
audit-logged as an override) and `[dispute this suppression]` (opens the
divergence-review affordance from beat 3).

**Reads:** `GET /api/decision/<id>`.

**Strip (drawer):** *"Suppressed 03:12 — flap-debounce. At 0.70 this
paged; at your 0.85 it stood down."*

**Exit condition:** the SRE reads the counterfactual and either closes
the drawer (trust) or taps `[page me anyway]` (distrust, exercised).
Both are successes. The demo's closing strip:

> *"You have seen the machine fail first, its disagreements itemized,
> its ledger signed, its kill switch tested. The close-the-tab test:
> from here on, if you stopped watching, the strip on every screen
> would still tell you what you need."*

---

## §2 — Divergence evidence: the numbered list as an interface

The Divergence Ledger (synthesis §5.1) is the trust moat made visible.
Its interface is a numbered, permanent, evidence-linked list. Design:

### 2.1 Row anatomy

Every divergence row is one fixed line + an expandable evidence block:

```
D-007 · 03:12:04 · fpr:9f2c·a41d · payments-api · SEV2
  PROD→PAGE  vs  SHADOW→SUPPRESS · flap-debounce
  status: UNREVIEWED · evidence: 4 links
```

- **D-###**: permanent, monotonic within the org. Numbers are never
  reused, never reordered. The number is the handle the SRE cites in the
  postmortem ("D-007").
- **Direction glyph**: always `X→Y vs X→Y`, both dispositions named.
  Never "shadow disagreed" — the *direction* is the information
  (suppress-vs-page is the dangerous direction; page-vs-suppress is the
  expensive direction; 03-deployment-path §: 9 suppress-vs-page, 4
  page-vs-suppress).
- **Reason code** is a filter, not a label (06 §4.2): clicking
  `flap-debounce` filters the list to that reason.
- **The whole row is the button** (06 §4.2, R3): the transcript view
  (§1 beat 3) opens from anywhere on the row.
- **Status chip**: `UNREVIEWED` (lit, demands eyes) / `MACHINE-RIGHT`
  (dim) / `HUMAN-RIGHT` (dim). Resolved rows dim; unreviewed rows stay
  lit. The list's visual field is dominated by what still needs a human.

### 2.2 The pinned SEV1/SEV2 section (not a sort key)

The list has two sections. The top section is **pinned, not sorted**:

> **SEV1/SEV2 DIVERGENCES — always on top, never sortable away.**
> D-007 · D-003 · (both unreviewed)

The pinned section is a structural guarantee, not a default sort the SRE
can accidentally change: it is rendered as a separate block above the
chronological list, and no filter or sort control applies to it. (Why
this matters is the pre-mortem in §10: a sortable-away SEV strip is how
an interface hides a real divergence.)

Below it, the full chronological list with the false-negative hunt
affordance:

> **FALSE-NEGATIVE HUNT** — the funnel, stated as three numbers:
> `13 divergences → 2 on SEV1/SEV2 → 1 unreviewed`
> [filter: unreviewed only] [filter: suppress-vs-page only]

The hunt is a *view*, not a feature: it is the list filtered to
(direction = suppress-vs-page) with the SEV1/SEV2 section pinned above it.
Visually, hunting false negatives looks like *descending a funnel whose
walls are severity and review-state*, not like reading a log.

### 2.3 The zero-SEV1/SEV2-false-suppress bar as a living counter

The bar (03-deployment-path: "the only non-negotiable number") is not a
claim on a slide; it is a **living counter** with rules:

**Render (exact):**

> **0** false-suppresses · **214** SEV1/SEV2 joined · **3** divergences
> unreviewed · outcomes joined through 2026-10-02 23:59 IST · [ledger]

**The counter's laws:**

1. **Monotonic within a signed epoch.** The count only ever changes on a
   signed ledger event (a reviewed divergence marked HUMAN-RIGHT on a
   SEV1/SEV2 suppress-vs-page, or a backtest mismatch). Between
   signatures, the number cannot move — which means it cannot be quietly
   edited, and cannot be quietly *fixed* either. Corrections arrive as
   superseding signed attestations, visibly.
2. **The denominator rides with the number** (06 R2, Law L1's human
   reason). `0` never appears alone. `0 of 214 joined` is a claim; `0` is
   a rumor. The join-freshness timestamp rides with it too — the counter
   is always *0 as of a named join*, never *0, timelessly*.
3. **The unreviewed count rides with it.** This is the pre-mortem's
   lesson (§10): `0 false-suppresses · 3 unreviewed` cannot be misread as
   "nothing to worry about." The unreviewed count is the counter's
   shadow — the number of divergences that *could* move it.
4. **Zero is quiet; nonzero is amber, never red.** A zero counter renders
   dim — quiet is the signal that the bar holds. A nonzero counter
   renders lit-amber with the auto-revert reference inline: *"1 confirmed
   SEV1 false-suppress — auto-revert to stage 2a armed, platform operator
   paged."* Red is reserved for pages (§8); a nonzero counter is an
   abnormality demanding attention, not a page demanding action — so it
   gets light, not red.
5. **Any SEV1/SEV2 divergence marked HUMAN-RIGHT moves the counter and
   arms the revert.** The counter card links the auto-revert rule and the
   kill-switch test on the same card. Cause, effect, and escape are one
   glance apart.

---

## §3 — The decision river as a projection over the event log

The river is not a table of rows; it is a **projection over the
append-only event log** (`decision_requested` / `decision_made` /
`forward_confirmed` | `forward_failed` / `flip_observed` — synthesis
§3.3). Each row on the tape is the folded state of one alert's event
sequence. The normal folds are boring by design: SUPPRESS rows dim,
PAGE rows lit. The interesting folds are the **anomalies** — event
sequences that do not complete — and each has a designed rendering,
because at 3 AM an unexplained row shape is indistinguishable from a
broken machine.

**Reads:** `GET /api/decisions?limit=50&since_id=<id>` + `SSE /api/stream`.

### 3.1 `decision_made` (PAGE) without `forward_confirmed` — the paged anomaly

**The rule is disposition-conditional** (stated explicitly because the
naive implementation is a bug): a SUPPRESS decision never expects a
forward — no `forward_confirmed` is missing, the event sequence is
complete at `decision_made`. Only a PAGE disposition opens a forward
obligation. If `forward_confirmed` *or* `forward_failed` has not arrived
within the forward timeout (config, default 30s), the row enters the
**DECIDED–UNCONFIRMED** state:

```
▮ 03:14:22 · db-failover · payments · DISPOSITION: PAGE — UNCONFIRMED
  ⚠ forward unconfirmed 47s — the forwarder's watchdog has been paged.
  [open incident] [check forwarder health]
```

- The row renders lit-amber with a watchdog badge. It does not dim into
  the tape — an unconfirmed page is the loudest non-page on the screen.
- The river's strip changes to name it: *"1 page decided but
  unconfirmed (db-failover, 03:14). The forwarder's watchdog is paged —
  check the forwarder, not the gate."*
- Per synthesis §3.3, this *is itself a paged event*: the forwarder's
  watchdog pages the platform operator. The river's rendering is the
  visible half of that page.
- Resolution: when `forward_confirmed` lands, the row folds to the
  normal lit PAGE row with a small `confirmed 03:14:31` marker. When
  `forward_failed` lands, see §3.2.

### 3.2 `forward_failed` — the red row that is not a page

A failed forward means a page the system intended did not go out. The
row renders with the **red PAGE disposition chip** (red follows the
disposition — the SRE must act as if paged: the page is owed) plus an
amber delivery badge:

```
▮ 03:14:22 · db-failover · payments · PAGE — delivery failed
  ⚠ primary forward failed 03:14:29 — secondary channel engaged.
  [open incident] [escalation policy]
```

The rule (§8): **red renders on the disposition PAGE, present or
intended-but-failed, never on health states.** The amber badge carries
the health information. A tired brain pattern-matches red to "I act" —
and a failed page is exactly an "I act."

### 3.3 `flip_observed` — the machine changing its mind, in the open

A flip folds as: the original disposition renders **dimmed but visible**
(history is not rewritten), with a flip badge carrying the new
disposition and the delta:

```
▮ 03:12:04 · cache-flap · SUPPRESS →(flip 03:12:41)→ PAGE
  the machine changed its mind: conf 0.93 → 0.41 on re-ask.
  [flip timeline]
```

The flip timeline expands inline: `decision_made(03:12:04, conf 0.93,
model jev-2.5, input sha256:…) → flip_observed(03:12:41, conf 0.41) →
decision_made(03:12:41, PAGE)`. Both `decision_made` events remain in the
event log; the river shows the *fold*, the audit drawer shows the
*sequence*. (06 §2.4: the flip timeline is first-class — "the machine
changed its mind" is the most trust-relevant fact the surface can show,
and it is shown *by the machine about itself*.)

### 3.4 Gap markers — holes are rendered, never hidden

SSE gap > 30s: the head row becomes a gap marker (06 §3.1):

```
— 4m 12s gap in the tape (SSE down, retrying) —
  newest row: 03:12:04 · 6m ago · [refresh]
```

A hole in the tape that the interface hides is a hole in the operator's
mental model (06 §2.1). The gap marker is mandatory rendering, not a
decorative state.

---

## §4 — The counterfactual receipt: exact row anatomy

Every suppression row carries its counterfactual receipt (ADR-023:
`threshold_counterfactual` on the decision event, evaluated
deterministically at event-write time). The receipt is not in the
drawer — it is **on the row's second line**, because the operator who
never opens the drawer must still meet the counterfactual. The drawer
then expands it in the locked reading order (06 §4.3).

**Row (exact):**

```
▮ SUPPRESS · flap-debounce · 03:12:04 · cache-flap · payments
  at 0.70 this paged · at your 0.85 it stood down — 3 similar stood down this week
```

**Drawer — the receipt block (exact anatomy, in order):**

1. **Verdict.** `SUPPRESS · flap-debounce · 03:12:04 IST · decision
   #48,201`
2. **Reason (human-arguable).** `flap-debounce — 3 flaps in 4 min,
   self-cleared 03:11:58. Correlator rule, deterministic. [timeline]`
3. **Counterfactual (the receipt proper).**
   - `at 0.70 (last month's policy) → PAGE`
   - `at 0.85 (your policy) → STAND DOWN`
   - `nearest boundary: 0.78 — a 0.07 tightening pages this class`
   - `3 similar suppressions this week, 0 became SEV1/2`
   The `threshold_counterfactual` field carries the per-preset
   dispositions; the "nearest boundary" is derived by the platform tier
   from the stored probability (pure arithmetic, no Jev call — frozen
   law).
4. **Confidence vs typical.** `conf 0.93 · your team's Q3 is 0.88 —
   this was an easy call *for you*` (06 §5.3: not "how confident" but
   "how confident relative to our bar"; the marker-vs-Q3 rendering from
   DESIGN_SYSTEM §3.6).
5. **The three locks, with proof-of-freshness ages.**
   `P(p1)=0.00 reported · fit p̂_upper<0.002 (fit 6d old, fresh) ·
   conf 0.93 ≥ 0.90 · allowlist attested by <name>, 11d ago (TTL 30d,
   fresh)`. A stale proof would have paged (synthesis §3.5) — so its
   presence here, fresh, is part of the receipt.
6. **What would have un-suppressed it.** Named, singular, checkable:
   `any one of: conf < 0.90 · P(p1) reported > 0.00 · allowlist
   attestation expired · severity SEV1/2`. This is the receipt's
   falsifiability clause — the operator can name the exact condition
   that would have changed the outcome.
7. **Input hash + model + deep link.** `input sha256:9f2c… ·
   jev-2.5-pinned · event #48,201 · [copy deep link]`
8. **Appeal.** `[page me anyway]` (one-click, pages through the normal
   path, audit-logged as override) · `[dispute]` (opens divergence
   review, beat 3).

**Reads:** `GET /api/decision/<id>` — the full row: probs, input_sha256,
model, `threshold_counterfactual`, latency.

**The rendering-defect rule** (06 Choice 3, brutal version): a
suppression row without a counterfactual second line is a rendering
defect, same class as a confidence bar without a denominator. The river
must not render the row until the receipt fields are present; if the
event predates ADR-023 (no `threshold_counterfactual`), the row renders
the honest fallback: *"receipt unavailable — decided before
counterfactual logging (event #<n>)."* Old rows do not get fake
receipts.

---

## §5 — The Standing Verdict Strip: one sentence per screen

The strip is **machine-generated from screen state with a fixed grammar**
(06 open question #1 — position taken: generated, never hand-written, so
it cannot go stale; the grammar is Type 2 copy with Type 1 seriousness).
Every screen opens with it. The acceptance test, per screen: **the
operator who reads only the strip acts correctly.**

| Screen | Strip grammar (exact templates) | Strip-only correct action |
|---|---|---|
| RIVER | Quiet: *"Nothing on fire. {n} suppressions in the last hour, all reason-coded. Gate armed."* · Page: *"{n} page(s) in the last hour ({service} {sev}, {time}). Details below."* · Anomaly: *"1 page decided but unconfirmed ({service}, {time}). Check the forwarder, not the gate."* | Quiet → close the tab. Page → open PD / acknowledge (service+sev+time route them). Anomaly → forwarder health, not the river. |
| SHADOW REPORT | *"Week {n}: {alerts} alerts, {agree}% agreement, {d} divergences all listed below. Zero unexplained on SEV1/SEV2."* | Scroll to the divergences; do not stop at the agreement number. |
| DIVERGENCE LIST | *"{u} divergences need your eyes — {s} on SEV1/SEV2 unreviewed. Start at the top."* · All reviewed: *"All {d} divergences reviewed. {m} machine-right, {h} human-right."* | Unreviewed → open the top pinned item. All reviewed → close the tab. |
| BACKTEST LEDGER | *"Backtest: {c} false-suppresses on {n} SEV1/SEV2 across {mo} months. Signed {date} — verify the signature, not our word."* | Click [verify]; then close the tab. |
| CANARY | *"Canary {stage}: {scope}. {o} overrides this week. Kill switch tested {age}."* | None — unless kill-switch age > 90d, then [test now]. |
| SIM | *"At these thresholds you would have been woken {p} times last week."* · Refusal: *"Cannot project — {n} cases is below 100. This refusal is the feature, not a bug."* | Carry {p} to the 10 AM review. Refusal → run a guided storm or wait for the nightly join. |
| CAL | Healthy: *"Calibration healthy as of {age}: ECE {ece} (n={n})."* · Stale: *"Calibration as of {age}: cannot refresh — treat tonight's confidences as uncalibrated until this updates."* · Thin: *"n={n} — calibration provisional."* | Healthy → trust tonight's confidences. Stale → be skeptical, retry later. Thin → do not tune on this. |
| AUDIT | *"fpr:{fp} — suppressed {n} times in 7d, always {reason}. Never paged."* (or the paged variant) | Nothing to defend → close the tab. Surprising → open the receipt. |
| START | *"Shadow mode — read-only tap. Your paging is unchanged."* · Degraded: *"This is a recording — the pipeline is unreachable right now."* | Continue the tour; or [retry live]. |

**Strip degradation rule** (the meta-rule from 06 §3, applied to strips):
every strip degrades to lead with what is still true. API down on the
river does not produce *"Error loading decisions"* — it produces:

> *"Decisions API unreachable (GET /api/decisions → 503, 2m ago).
> Showing the tape as of 03:10:22. **The gate is unaffected — paging
> behavior does not depend on this screen.** [Retry] [Open PagerDuty
> incidents ↗]"*

"The gate is unaffected" comes first because at 3 AM the operator reads
the first clause and acts on it — the order of sentences is a safety
property.

---

## §6 — Degraded-first composition: the dead copy, written first

Per 06 Choice 2, every surface below is specified from its dead state
upward. The API-down copy is written *before* the happy path in each
screen spec; the happy path is the enhancement. The QA gate (proposed to
the coordinator): **unplug the API and complete the operator's task** —
read the tape, check calibration, reprice a policy, pull a receipt. If
the task cannot be completed, the screen fails review.

### 6.1 The surfaces, dead first

**RIVER.** Dead states per 06 §3.1 (SSE <30s: `◌ reconnecting…
(attempt N)`, no modal; SSE dead >30s: gap-marker head + poll fallback
with `newest: 03:12:04 · 6m ago` always visible; API fully down:
last-known snapshot from cache + the age banner + the gate-unaffected
sentence + PagerDuty escape hatch; data stale: `● live — no decisions
in 47m (gate armed, team=payments)` — "gate armed" is the sentence that
turns quiet from frightening into informative). **The river is a static
tape that happens to go live** — cache + poll are the base, SSE is the
enhancement.

**SHADOW REPORT.** The report is a generated artifact, so its dead state
is graceful by construction: API down ⇒ serve the **last generated
report** with the age banner: *"Showing the report for week ending
2026-09-26 — 7 days old. This week's report cannot generate (API down).
The divergences below are last week's; treat them as stale."* A stale
report stated declaratively would be a lie the operator acts on (06
§3.2); the conditional tense is load-bearing.

**DIVERGENCE LIST.** API down ⇒ the list renders from the session cache
(every divergence drawer opened this session is cached with full
evidence) with the honest scope line: *"Showing divergences you have
opened this session ({n}). Full ledger unreachable (503). Unreviewed
counts may be stale — do not treat this as the complete hunt. [Retry]"*
The pinned SEV1/SEV2 section renders from cache too, labeled as cached.
The one thing the dead list must never do is present a partial list as
the whole hunt.

**BACKTEST LEDGER.** The ledger is signed and immutable — its dead state
is its normal state: *"Signed 2026-10-03 · attestation sha256:9f2c…a41d ·
verify against your sink."* If the sink is unreachable, the copy says
so: *"Cannot reach your verification sink — the signature above is
unverifiable right now. The ledger's contents are unchanged; only
verification is unavailable."* Immutability degrades to
unverifiable-but-unchanged, never to editable.

**CANARY.** Degraded ⇒ the panel renders **read-only with the reason
inline**: stage state, override counts, kill-switch age from the last
known config snapshot (age-labeled); the `[request change]` and `[test
now]` affordances disable with the reason *on the button*: *"Change
requests unavailable — API down. Stage changes are two-person signed;
use the out-of-band runbook."* A disabled control without a reason at
3 AM reads as "the machine won't let me think" (06 §3.3); the reason
restores agency.

**CAL.** Per 06 §3.2: frozen card, conditional tense, denominator on the
card, provisional styling below n=100.

**AUDIT.** Per 06 §3.4: session-local index fallback with exact scope
("Searching your recent history only — 214 decisions cached"); the
loneliest moment handled ("No decisions match fpr:… — either the
fingerprint is wrong, or this alert never reached the gate — check
receiver health").

### 6.2 The simulator refuses to project on thin data

The refusal is a designed state, not an error state (06 §3.2). Below
n=100 labeled cases, `POST /api/simulate` returns the refusal and the
screen renders it as the *primary* content:

> **Cannot project.**
> Not enough shadow data — {n} cases. Projections on this little data
> would be numerology, not math.
> [Run a guided storm] [Wait for the nightly join]
>
> Strip: *"Cannot project — {n} cases is below 100. This refusal is the
> feature, not a bug."*

The slider stays live (a frozen slider reads as "the machine won't let
me think") but the projection pane shows the refusal, and `[export]`
disables with the reason inline: *"Export disabled — projections on
thin data are not shippable to a 10 AM review."* The demo deliberately
triggers this state once (filter to n=87) so the SRE watches the machine
refuse to manufacture confidence — beat 0's promise, kept in public.

### 6.3 Onboarding refuses to fake liveness

Per 06 §3.5: if the storm cannot run against the real pipeline, the
flow offers a **recorded storm** — a real recording of a real synthetic
run, labeled unmissably:

> **Recorded walkthrough** (pipeline unreachable).
> This is a recording of a real storm from 2026-10-01 — the suppressions
> are real, they just aren't yours. [Retry live]

A fake-live storm would poison the trust prior the whole product depends
on. The demo's own beat 0 is the same principle at the narrative level:
the demo *is* a recorded walkthrough, and says so before anything else.

---

## §7 — Dark-cockpit discipline: quiet is a signal, red is reserved

(06 §4.1; synthesis §4 platform tier. This section is the enforceable
spec, not the philosophy.)

### 7.1 The color budget

| Color / treatment | Meaning | May appear on |
|---|---|---|
| **Dim, low-contrast rows** | Normal operation: suppressions, reviewed divergences, confirmed forwards | River SUPPRESS rows, resolved divergence rows, ledger match rows |
| **Lit, neutral** | Abnormal-but-not-urgent: unreviewed divergences, unconfirmed forwards, nonzero amber states | Unreviewed divergence rows, DECIDED–UNCONFIRMED rows, nonzero counter |
| **Amber** | Abnormality demanding attention | Forward-failed badge, nonzero false-suppress counter, stale-proof warnings |
| **Red (`--sev-1` / `--disp-page`)** | **A page. Only a page.** | PAGE disposition chips — present, or intended-but-failed (§3.2) |
| **Nothing** | No lights: everything normal | The quiet river, the zero counter, the `● live — gate armed` indicator in its off state |

**The rules:**

1. **Red follows the disposition, never the health.** Red appears on
   `PAGE` — delivered, or decided-but-undelivered. It never appears on
   confidence bars, thresholds, calibration cards, or error banners.
   The day red appears somewhere it shouldn't, the operator's learned
   association ("red = I act") degrades — and that association is
   load-bearing at 3 AM.
2. **The confidence ramp is deliberately not red/green** (06 §4.1). A
   confidence value is information about the machine's belief, not a
   verdict. A tired brain pattern-matches red/green to bad/good in
   milliseconds; we refuse to let "0.42 confident" read as "bad" when
   0.42 below threshold is a *correct suppression*.
3. **Quiet is rendered, not empty.** The quiet river is dim rows + the
   `● live — gate armed` indicator + `newest: 03:12:04 · 6m ago` in mono,
   always. An empty screen is ambiguous (dead? quiet? broken?); a quiet
   screen with the armed indicator and a fresh timestamp is a sentence:
   "I am working and there is nothing for you."
4. **One animation per screen, and it means "new."** The 120ms row
   highlight on SSE arrival is the only motion in the product. Motion is
   the scarcest attentional currency; it is spent exactly once, on
   exactly one meaning: *this arrived while you were looking.*
5. **Staleness is ambient, not textual.** The age of the newest row sits
   in the top bar in mono, always — absorbed peripherally like airspeed,
   not read like a sentence.
6. **Gap markers are mandatory rendering** (§3.4). A hidden hole is a
   lie about continuity.

### 7.2 The reading order is a safety property (locked)

For the tunneling mind (Easterbrook), information arrives in the order
the operator would ask for it — verdict → reason → counterfactual →
confidence-vs-typical → raw (06 §4.3). This order is locked across the
river row, the receipt drawer, and the divergence transcript. The
operator who learns it once can read every surface at 3 AM without
re-learning.

---

## §8 — CREATIVE APPLICATION: three choices we make differently

These are not in 06's three (strip, degraded-first, counterfactual
receipt — taken, and built on above). Each is Type 2-reversible with
Type 1 seriousness about the human.

### Choice 1 — The anti-demo opening: show the failure before the capability

No observability vendor opens a demo with the product being wrong. We
open beat 0 with the honesty contract and beat 1 with a *designed
flip* — the machine changing its mind about itself at minute 6, before
any capability is claimed. The derivation: 06 §2.5 — the trust prior is
set in the first minutes, and the prior we need at 3 AM is not "this
machine is impressive" but "this machine shows me its failures." A
vendor demo optimizes for the 2 PM buyer who wants to *see capability*;
we optimize for the 3 AM user who needs to *stop watching*. The flip is
not a bug we apologize for — it is the load-bearing trust event of the
whole narrative, and the demo would be dishonest without it. (It also
inoculates: when the SRE later meets a real flip on the river, the
mental model already contains the failure mode — Lee & See's
appropriate reliance, manufactured on purpose.)

### Choice 2 — The divergence as a transcript, not a row

Divergences are not log entries to be read; they are **arguments to be
judged**. The transcript view (§1 beat 3) renders production and shadow
as two argued positions with cited evidence, and the SRE renders a
verdict with one click. Nobody does this because vendors show *their*
analysis; we show *both sides'* evidence and hand the gavel to the
operator. The derivation is 06 §5.3's evidence order + R5 (template-bound
thinking): the operator pattern-matches to the last similar incident,
so the transcript's evidence columns are organized as "what happened
last time like this" (sibling alerts, same-window history) before any
model internals. The behavioral test: the SRE who judges three
divergences has built the mental model that lets them close the tab at
3 AM — "I know how this machine argues, and I know how to overrule it."

### Choice 3 — The monotonic counter and the close-the-tab metric

The SEV1/SEV2 false-suppress counter is **monotonic within a signed
epoch** (§2.3): it moves only on signed ledger events, and corrections
arrive as superseding attestations, visibly. Nobody does this because
dashboards show live numbers; we show a number that *cannot move
without a signature*, because the number's job is not to be fresh — its
job is to be *believable at 3 AM by someone who distrusts the channel*.
Paired with it: every screen's success metric is the operator leaving.
The demo's closing strip says it out loud, and the strip acceptance
tests (§5) are all stated as "the operator reads only the strip and
acts correctly" — where, for six of nine screens, the correct action is
*close the tab*. "You can stop looking" is the product's true success
metric (06 §5.3), and the demo is the first place we prove we mean it:
the better the demo goes, the less the SRE needs to watch.

---

## §9 — PRE-MORTEM: the demo convinced them, and the interface later hid a real divergence

*It is one year from now. A design partner's SEV1 was suppressed. The
demo had convinced them. The interface hid the divergence that would
have caught it. What exactly failed?*

**The failure, in concrete mechanical detail:**

1. **The pinned SEV1/SEV2 section was a sort, not a structure.** Six
   months after the demo, a dashboard refactor (Type 2 change, no brutal
   review) replaced the pinned section with a default sort on
   `severity DESC` — visually identical in every test. During a
   flap-debounce storm week, the divergence list held 400+ noise
   divergences. The SRE, hunting, clicked "sort by time" to find the
   newest — and the SEV1 divergence, which the pinned section would have
   held at the top, slid to row 311. The interface did not hide it; it
   *allowed it to be sorted away*. The demo had shown the pinned
   section; the shipped product had a sort that looked like it.

2. **The severity that mattered never matched the filter.** The
   suppressed alert came through the legacy PD integration, which emits
   severity as `P1` (PagerDuty's taxonomy), not `SEV1`. The canonical
   severity mapping at ingest had a gap: `P1 → SEV1` was mapped for the
   *alert* pipeline but not for the *divergence* pipeline (two code
   paths, one mapping table, the second path added later). The
   divergence record carried `severity: "P1"`. The pinned-section query
   filtered `severity IN ('SEV1','SEV2')` — string equality. The
   divergence was in the chronological list, correctly rendered, 311
   rows down, wearing a severity chip no filter recognized. Chesterton's
   fence, unlearned: the old system had a human triage step that
   defaulted unknown-severity alerts to the top of the queue. We removed
   the human and the default with one refactor.

3. **The counter said `0`, truthfully, about the wrong set.** The living
   counter read `0 false-suppresses · 214 SEV1/SEV2 joined`. The join
   was 19 days stale — the customer's label export (Ledger's outcomes
   join) had silently stopped, and the counter's freshness timestamp
   (`outcomes joined through …`) rendered in small type beneath the big
   `0`. The operator read the big number, not the small timestamp. The
   divergence that would have moved the counter sat **unreviewed** —
   and the unreviewed count, which §2.3 requires to ride *with* the
   counter, had been moved to a tooltip in the same refactor ("cleaner
   card"). `0` with no visible unreviewed count reads as "nothing to
   worry about." It was `0 of a stale 214, with 1 unreviewed that
   mattered`.

Three failures, each "behaving as designed," composing into a hidden
divergence: a sort that wasn't a structure, a taxonomy gap between two
pipelines, a counter whose denominator and shadow were demoted to small
type. The demo convinced them because the demo showed the *designed*
states; the product failed in the *refactored* states.

**The design changes folded back in (from this pre-mortem):**

- **Pinned is structural, tested as structural.** The SEV1/SEV2 section
  is a separate rendered block, not a sort default — and a CI fixture
  asserts it: with 400 noise divergences and a sort-by-time click, the
  SEV block still renders first. (Proposed: Tripwire-style permanent
  fixture, same class as the (fresh|stale)³ rot-matrix.)
- **Canonical severity at every ingest edge, one mapping, one test.**
  The severity taxonomy is canonicalized once, at the event-log write
  boundary — alerts, divergences, and backtest rows share the mapping
  and the test corpus (`P1→SEV1`, `critical→SEV1`, unknown→`SEV-?`
  which pins *up*, never down). An unrecognized severity pins to the
  top section, never to the chronological list: **unknown severity is
  treated as SEV1 until proven otherwise.** This is the fence rebuilt:
  the old human triage default, encoded as a rule.
- **The counter's denominator, join-freshness, and unreviewed count are
  structural, not styling.** §2.3's laws 2 and 3 are rendering
  *invariants*: the strip grammar emits them as mandatory slots —
  `"{c} false-suppresses · {n} joined through {ts} · {u} unreviewed"` —
  and a strip missing a slot fails rendering (loudly, not silently). A
  refactor cannot demote them to a tooltip because they are not
  styling; they are the sentence.
- **Join-freshness has its own watchdog.** If the outcomes join goes
  stale past the expected cadence, the counter card degrades per the
  degraded-first rule: *"Outcomes join 19d stale — the 0 below is 0 of
  a stale set. Do not tune on this."* Stale claims are marked STALE,
  never silently trusted (synthesis §4 data).

---

## §10 — Open questions for the coordinator (not decisions)

1. **Divergence-record shape → Forge's event-log RFC (ADR-011).** This
   narrative needs, per divergence: shadow disposition, production
   disposition (recorded from the shadow tap), direction, review status,
   evidence links. I have designed the *presentation contract* (what
   the screen needs); the *event schema* is Forge's. Flagged, not
   assumed.
2. **Shadow Report generator ownership.** The report is a batch
   projection over the event log. Engine or platform tier? (Position:
   platform tier — it reads, never writes the hot path — but the
   scheduling and signing need an owner.)
3. **Canary change-request flow.** The frozen read API has no writes;
   this narrative designs `[request change]` / `[test now]` as
   signed-request affordances, never direct mutations. The request
   flow's shape (who signs, where the request lands, how the audit log
   records it) needs Forge + Vault sign-off.
4. **Strip copy owner** (echoes 06's open question, with position
   taken): strips are machine-generated with fixed grammar (§5). The
   grammar file needs an owner and a review cadence — recommend Prism
   owns the grammar, coordinator reviews quarterly.
5. **Degraded-first QA gate** (echoes 06's open question #2): "unplug
   the API and complete the operator's task" as a named P0 exit bar —
   needs coordinator sign-off to add.

---

*End of LANE 6 design. No code written, no contracts changed, no branch
switched, nothing committed, nothing pushed. The file is
`design/fixes/08-proof-demo-narrative.md` on `lane/principal-fix-designs`.*
