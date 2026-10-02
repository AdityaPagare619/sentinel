# Deployment Path — Earning the Right to Suppress

*Principal design paper. PAGER lane, 2026-10-03. Companion to*
*`02-field-archaeology.md` (the fences) — this paper is the adoption
strategy those fences demand.*

## Thesis

No company rips out its paging stack. PagerDuty/Opsgenie/Alertmanager are
load-bearing, audited, compliance-blessed, and wired into runbooks,
postmortems, and muscle memory. Any vendor that asks for rip-and-replace
loses the deal before the demo ends.

So Sentinel does not replace the paging stack. It earns a position *in
front of* it — and it earns that position the way every safety-critical
automation in history has earned authority: **advisory first, action later,
evidence always.** The product is a **proof engine first, a suppression
engine second**. The suppression is the feature; the proof is the product.

## The buyer's fear, named

The buyer — the VP Eng or SRE lead signing the PO — has exactly one fear,
and it is not "will it reduce noise." It is the Type 1 error: **Sentinel
suppresses a real SEV1, the page never fires, the outage burns for an extra
hour, and the postmortem names the new vendor.** Knight Capital lost $440M
in 45 minutes because no kill switch existed (02-field-archaeology.md,
Fence 7a). Target's team *had* the alerts and didn't act (Fence 1b).
Überlingen killed 71 people because the human overrode the automation that
was right (see Precedents). Every one of these is a story the buyer already
knows. No slide deck, no benchmark, no "99.2% precision" overcomes it.

Only one thing overcomes it: **evidence, generated on the buyer's own data,
under the buyer's own eyes, with the buyer's own incidents as the test
set.** That is what the three stages below produce. Each stage answers one
question, and the buyer advances only when the answer is in hand.

---

## Stage 0 — Shadow pipeline: "watch, don't touch"

### What runs where

Sentinel deploys as a **read-only tap** on the existing alert flow.
Concretely, one of:

- an **Alertmanager webhook receiver** added alongside (not instead of) the
  existing PagerDuty/Opsgenie receiver — every firing alert is POSTed to
  Sentinel in parallel; or
- a **PagerDuty webhook subscription (v3)** / **Opsgenie alert callback**
  on `incident.triggered` — Sentinel sees every incident the existing
  stack creates; or
- a **log-shipper sidecar** tailing the existing alert pipeline's audit
  log, for estates that won't add a receiver.

The tap carries **read-only credentials**. The shadow gate has no write
path to anything: no Events API key, no incident mutation, no silence
creation, no webhook that can page. This is enforced mechanically (no
credentials provisioned), not by policy. The existing stack pages exactly
as it did yesterday — byte-identical behavior, zero paging-path latency
added, zero blast radius.

Inside the shadow, Sentinel runs the full gate — receiver → correlator →
disposition — on every alert, and **writes down what it would have done**:
the disposition, the confidence, the evidence, the model vs. guardrail
agreement. Nothing leaves the shadow except the ledger.

### What it proves

The shadow answers the buyer's first question: *"On my data, in my
noisiest month, what would you have done differently — and were you
right?"* Specifically:

- **Agreement rate on pages**: of the N alerts the human stack paged,
  how many would Sentinel also have paged? (Target: ≥98%.)
- **Suppression opportunity**: of the N alerts paged, how many would
  Sentinel have suppressed — with the evidence for each, inspectable
  per alert? (This is the ROI number, but it is *not* the trust number.)
- **The divergence list**: every disagreement, both directions —
  "we would have suppressed what you paged" AND "we would have paged
  what you suppressed/didn't page." The second direction is the one
  that matters; see Stage 1.
- **Latency profile**: p50/p99 of gate decision time on real traffic —
  proving the Fence 4 latency budget holds before we ever touch the
  paging path.
- **Calibration**: are the confidence numbers honest? Of 100 alerts at
  0.9 confidence, were ~90 of the dispositions correct against the
  human outcome? A miscalibrated gate is a lying gate.

### What the buyer sees

A weekly **Shadow Report**, one page, same format every week:

> **Week of Oct 6 — Shadow Report (read-only, zero stack changes)**
> Alerts observed: 4,212. Human stack paged: 1,365.
> Sentinel would have paged: 1,352 (agreement 99.0%).
> Would-have-suppressed: 2,847 (67.6% noise reduction *opportunity*).
> Divergences: 13 — 9 suppress-vs-page, 4 page-vs-suppress. All 13
> listed below with evidence links. **Zero divergences on SEV1/SEV2.**
> Gate p99 latency: 3.1s (budget 30s). Calibration: within 3pp at all
> confidence bands.

The buyer does not see a dashboard of model metrics. The buyer sees **a
list of 13 disagreements they can click through**, each with the evidence
the gate used. Trust is built one inspected disagreement at a time.

### Exit criteria (shadow → backtest)

- ≥30 days of shadow operation **or** ≥1,000 alert decisions, whichever
  is larger (small estates need the time; large estates need the volume).
- **Zero unexplained divergences on SEV1/SEV2.** Any SEV1/SEV2
  disagreement must have a written, buyer-accepted explanation (e.g.
  "human paged on a known-flaky check; gate correctly identified the
  flap pattern") or the stage does not advance.
- Calibration within 5 percentage points at every confidence band.
- Gate p99 latency within budget on the buyer's real traffic shape.

---

## Stage 1 — Divergence backtest: hunting false negatives with real data

### Methodology

The shadow proves the gate on *current* traffic. The backtest proves it on
*the incidents that hurt* — the ones in the postmortem archive. Method:

1. **Extract.** Pull 6–12 months of incident history from the buyer's
   PagerDuty (`GET /incidents` + `/incidents/{id}/alerts`) or Opsgenie
   alert API: every incident, its alerts, its severity, its timeline,
   its resolution notes, and — critically — the postmortem tag where one
   exists. This is the buyer's own ground truth: what the humans paged,
   what they suppressed (via silences), what turned out to be real.
2. **Replay.** Feed the historical alert stream through the gate,
   offline, in chronological order, with episode semantics intact
   (flaps, re-fires, resolves — Fence 5). The gate emits a disposition
   per alert-episode as if it were live.
3. **Compare against outcomes, not against pages.** The naive comparison
   — "did the gate agree with the human page?" — measures conformity,
   not correctness. The backtest's primary metric is **false-suppress
   rate against postmortem-confirmed real incidents**: of the incidents
   the buyer's own postmortems say were real (SEV1/SEV2 especially),
   how many would the gate have suppressed? The bar is **zero**.
   The secondary metric is precision on the noise: of the alerts the
   humans retrospectively labeled noise (silenced, auto-resolved,
   "known flaky" in postmortems), how many did the gate suppress?
4. **False-negative hunt protocol.** Every backtest false-suppress is
   investigated like an incident: root-caused to a mechanism (bad
   fingerprint? topology error? model overconfidence? guardrail gap?),
   fixed or explicitly accepted in writing by the buyer's incident
   review, and added to the regression corpus. The corpus grows with
   every backtest; the gate is never allowed to regress on it.

### Why this stage exists

The shadow can only evaluate what happens *now*. The incidents that
define a buyer's trust — the 3 AMs their team still talks about — are in
the archive. A gate that would have suppressed last year's SEV1 is a gate
the buyer must never deploy, and only the backtest can find that out
*before* it matters. This is also where the Law 6 pre-mortem gets its
empirical teeth: the named failure modes (fingerprint collision, stale
suppress leaking into a new episode) are hunted for explicitly in
historical data, not just reasoned about.

### What the buyer sees

The **Backtest Ledger**: every historical SEV1/SEV2, one row each —
what the humans did, what the gate would have done, agreement or a
named, root-caused, fixed-or-accepted explanation. The ledger is signed
off by the buyer's incident review board, not by the vendor. The buyer
also sees the regression corpus: "these are the 214 incidents the gate
is now contractually forbidden from getting wrong."

### Exit criteria (backtest → advisory)

- **False-suppress rate on postmortem-confirmed SEV1/SEV2: 0** across
  the full historical window. Not "low." Zero. This is the only
  non-negotiable number in the entire deployment path.
- False-suppress rate on SEV3+: buyer-defined tolerance (suggested
  ≤0.5%), each instance root-caused.
- Backtest Ledger signed by the buyer's incident review.
- Regression corpus committed; CI gate fails the build on any
  regression against it.

---

## Stage 2 — Gradual canary interception: from annotation to action

Only after shadow + backtest does Sentinel touch the paging path — and
then only in four sub-stages, each with its own rollback triggers. The
principle throughout: **the human stays in the loop until the evidence
says otherwise, and the loop can be re-entered with one flag.**

### 2a — Advisory annotation (the TA phase)

Sentinel attaches its disposition — verdict, confidence, evidence,
recommended action — to every incident as a **note/annotation** via the
existing stack's API (PagerDuty incident notes, Opsgenie alert notes).
No routing change. No suppression. The on-call sees, on every page: what
the gate thinks, why, and what it recommends.

*What it proves:* the dispositions are *useful*, not just correct. Do
engineers open the annotations? Do the recommended actions match what the
responder actually did? This is the adoption metric no offline evaluation
can measure.

*Advance when:* 30 days; annotation open-rate tracked; zero on-call
complaints about annotation quality; incident review agrees the
recommendations were "what a good senior would have said" in ≥90% of
sampled SEV1/SEV2s.

### 2b — Low-urgency interception (the first real suppression)

The gate moves into the paging path **for low-urgency traffic only**:
alerts the existing policy already routes to quiet channels (low urgency,
SEV3+, non-critical services). Suppressed low-urgency alerts go to the
muted-not-dropped state (ADR-007): visible on the dashboard, receipted,
auditable, never paged — with a **one-click "page me anyway" override**
on every suppressed item, and automatic re-escalation if the episode
worsens (severity upgrade ⇒ the suppression is voided and the page fires
immediately).

*What it proves:* suppression works mechanically — receipts, overrides,
re-escalation — on traffic where a mistake costs annoyance, not an
outage.

*Advance when:* 60 days; **zero** suppressed low-urgency alerts
re-escalated by humans as "should have paged"; override-button usage
<5% of suppressions (high override usage means the gate is wrong, not
that humans are cautious); on-call sentiment neutral-to-positive.

### 2c — Full pre-page gate (the RA phase)

The gate fronts the entire paging path: every alert passes through it;
page/suppress/mute decisions are executed. The human override and the
kill switch from 2b now cover everything. Escalation policies, urgency
model, rotations — all unchanged (the fences hold); only the *trigger*
got smarter.

*What it proves:* the complete thesis — fewer, better pages, with the
evidence trail to prove each suppression was correct.

*Advance when:* 90 days; sustained noise reduction ≥50% with zero
SEV1/SEV2 false-suppresses; MTTA/MTTR neutral or improved (the enriched
first page should *reduce* time-to-action — Fence 1); the buyer's
incident review board votes to continue. Any single confirmed
SEV1/SEV2 false-suppress **auto-reverts to 2b** (see Rollback).

### 2d — Autonomous operation (optional, may never be wanted)

Full gate *without* per-incident human override expectation — the gate's
decisions stand unless appealed. **Sentinel never pushes for this stage.**
Some buyers will want it for low-urgency tiers; most will keep the
override forever; both are correct. The product is complete at 2c. (Law 1:
the override is not a bottleneck to optimize away; it is the trust
mechanism itself.)

---

## Rollback triggers — the kill switch and its friends

Rollback is not a meeting; it is a mechanism. Every stage transition is
reversible, and reversal is designed to be *boring*:

**Automatic (no human judgment required):**
- Any **confirmed false-suppress of a SEV1/SEV2** ⇒ immediate auto-revert
  to the previous stage (from 2c → 2b, from 2b → 2a, from 2a → shadow),
  page the Sentinel platform admin, open a postmortem. The revert is a
  config-flag flip executed by the gate's own watchdog path — it does not
  depend on the gate being healthy (Fence 7).
- Page-divergence rate (gate vs. human-override-corrected outcome) above
  the buyer-set threshold over a rolling 7 days ⇒ revert one stage.
- Gate-added paging-path latency p99 above budget (Fence 4) for >15
  minutes ⇒ fail-open to page-everything (not a stage revert — a
  *bypass*; the gate removes itself from the path and pages).

**Manual (one action, no deploy):**
- The **kill switch**: a single flag — API call, CLI command, and a big
  red button in the admin console, all three — that bypasses the gate
  entirely. The paging path degrades to "exactly yesterday's stack."
  It must work when the estate is down (Fence 2: out-of-band), and it
  is tested in every game day.
- On-call sentiment: a one-tap "this page was wrong / this suppression
  was wrong" on every disposition. Sustained negative signal ⇒ human
  review, stage hold or revert at the buyer's discretion.
- Incident review board veto at any time, for any reason, no
  justification required. The board's authority over the gate is
  absolute — this is stated in the contract, because trust requires
  the buyer to hold the bigger stick.

**The Knight Capital rule** (Fence 7a): the kill switch is tested
quarterly under game-day conditions, and the test result is reported to
the buyer. A kill switch that has never been pulled is a rumor.

---

## Precedents — this path has been walked before

**Tesla's shadow mode.** Tesla runs new Autopilot/FSD builds in "shadow
mode" across the fleet: the software makes driving decisions in parallel
with the human driver and compares — *"compares our software algorithm to
real-world driver behavior across tens of millions of instances"* (Tesla
Q2 update letter, via teslamotorsclub.com; accessed 2026-10-03). Only
after shadow validation does a feature reach the driver. **Honest caveat:**
Tesla's shadow is campaign-sampled — community reporting notes it
*"ONLY reports specific things Tesla asks it to — and only from the
specific cars Tesla asks"* (teslamotorsclub.com; accessed 2026-10-03).
Sentinel's shadow is *more* thorough than the precedent: every alert, every
disposition, every divergence is logged, because our fleet is thousands of
alerts, not millions of cars — we can afford completeness where Tesla
must sample. We cite the precedent for the *pattern* (parallel,
non-interfering evaluation against human behavior) and exceed it on
coverage.

**TCAS: TA then RA, and the cost of premature authority.** Aviation's
collision-avoidance system spent years as a two-tier design: the Traffic
Advisory (awareness only — "look here") came first; the Resolution
Advisory (command — "climb NOW, overriding ATC") earned its authority
through investigation and training, not through marketing. When the
authority question was tested — **Überlingen, July 1, 2002**: the
Bashkirian crew followed ATC instead of their RA, the DHL crew followed
theirs, 71 people died — the global conclusion was unambiguous: the
automation's RA was right, the training was wrong, and RA compliance
became mandatory and overriding (German BFU investigation; ICAO guidance;
radiohangar.com; en.wikipedia.org/wiki/Traffic_collision_avoidance_system;
accessed 2026-10-03). Our stages mirror this exactly: **2a is the TA
(advisory, awareness, no authority); 2c is the RA (action, with the
override hierarchy explicitly defined and trained).** And Überlingen is
why the override hierarchy is *written down and drilled* rather than
assumed: in a conflict between the gate and the human, who wins, under
what conditions, must be unambiguous before the first suppression —
because the one time it matters, there will be no time to decide.

**Gmail's spam filter: never delete, always appealable.** From its
earliest days Gmail filtered suspected spam into the Spam folder —
quarantined, user-visible, recoverable — and built the "report spam" /
"not spam" buttons as the human feedback loop that trained the system
(ZDNet, 2015, on Google's ML spam filtering; accessed 2026-10-03).
Twenty years later, Gmail still does not delete suspected spam
unilaterally. This is the direct ancestor of our **muted-not-dropped**
state (ADR-007) and the one-tap override: the filter earns trust by being
*reversible and inspectable*, and the user's corrections are the training
signal. Sentinel's suppression receipt is Gmail's Spam folder,
industrialized: every suppressed alert visible, appealable, and feeding
the calibration loop.

**WAF monitor-before-block.** Every enterprise WAF deployment (Cloudflare,
Akamai, and the OWASP CRS tradition) runs new rules in log/monitor mode
first — recording what *would* have been blocked — and only promotes to
block mode after the false-positive rate is proven on the customer's own
traffic. The industry learned long ago that blocking on day one is how
you take down the checkout page. Our shadow → backtest → canary sequence
is this pattern applied to paging: **log mode, then block mode, with the
customer's traffic as the judge.**

---

## CREATIVE APPLICATION — the proof engine as the product

The deployment path above is not just a sales motion. It *is* the product
strategy, and it generates artifacts that become Sentinel's moat:

1. **The Divergence Ledger** (shadow, ongoing). Every disagreement
   between the gate and the human stack, with evidence, permanently
   recorded. This ledger is the single most valuable artifact Sentinel
   produces: it is simultaneously the trust instrument (the buyer
   inspects it weekly), the training signal (disagreements become
   regression cases), and the sales collateral (the next buyer's
   objection — "prove it on *our* data" — is answered by showing the
   last buyer's ledger, anonymized). No competitor can replicate a
   ledger they didn't earn alert by alert.

2. **The Suppression Receipt** (2b onward). Every suppressed alert emits
   a durable receipt: what was suppressed, why (evidence + confidence),
   what would have un-suppressed it, one-click appeal. The receipt is
   the mechanical implementation of Fence 7's silence doctrine (scoped,
   expiring, logged) and Gmail's never-delete precedent. In a
   postmortem, the receipt answers "why didn't we get paged?" in
   seconds — which is the difference between a vendor being blamed and
   a vendor being exonerated.

3. **The Backtest Ledger** (Stage 1). The buyer's own incident history,
   replayed, with a signed zero-false-suppress attestation on SEV1/SEV2.
   This document is what the buyer's incident review board signs — and
   what their auditors, their insurers, and their board will ask for
   after the first suppressed-then-real incident that *doesn't* happen.
   The ledger turns "trust us" into "here is the signed evidence."

4. **The Kill-Switch Runbook** (all stages). Quarterly-tested,
   buyer-witnessed, one-flag bypass. The runbook is a product feature,
   not an ops afterthought: it is the answer to the buyer's one fear,
   printed, tested, and dated. Knight Capital's epitaph was "no kill
   switch." Ours is the opposite.

5. **Calibration as a public metric.** The shadow report's calibration
   line — "within 3pp at all confidence bands" — is published to the
   buyer weekly. Most ML vendors hide calibration; we print it, because
   a suppression engine whose confidence numbers are honest is
   qualitatively different from one whose numbers are marketing. When
   the gate says 0.97, the buyer should be able to check that 0.97 meant
   something — and then trust the next 0.97 at 3 AM.

**The one-line product strategy:** *Sentinel sells evidence, and the
suppression comes free with it.* The buyer never buys "fewer pages."
The buyer buys the ledger, the receipts, the backtest attestation, and
the kill switch — and the fewer pages are what those artifacts permit.
Any competitor can build a classifier. Nobody else will have *your*
divergence ledger — and that is why the proof engine is the moat, not
the model.

---

## Summary — the three stages on one page

| Stage | The gate does | The stack does | Buyer sees | Advances when |
|---|---|---|---|---|
| **0 — Shadow** | Watches, logs would-be dispositions; zero write path | Unchanged, byte-identical | Weekly Shadow Report: agreement %, 13 divergences with evidence, calibration, latency | ≥30d/1k decisions; zero unexplained SEV1/2 divergences |
| **1 — Backtest** | Replays 6–12mo of buyer incidents offline; false-negative hunt | Unchanged | Signed Backtest Ledger: every SEV1/2 row, zero false-suppress attestation | 0 false-suppress on postmortem SEV1/2; ledger signed |
| **2a — Advisory** | Annotates incidents with disposition + evidence + action | Unchanged routing | Annotations on every page | 30d; annotations useful (≥90% "senior-grade") |
| **2b — Low-urgency** | Suppresses low-urgency only; receipts; override; auto re-escalate | Pages everything else as before | Suppression receipts; override stats | 60d; zero "should have paged"; overrides <5% |
| **2c — Full gate** | Pre-page gate on all traffic; human override + kill switch | Escalation/urgency/rotations unchanged | Fewer, better pages; MTTA/MTTR | 90d; ≥50% noise cut; zero SEV1/2 false-suppress; board vote |
| **2d — Autonomous** | *(Optional; never pushed)* | Unchanged | — | Buyer's explicit risk acceptance, if ever |

Rollback at every stage: automatic on any confirmed SEV1/SEV2
false-suppress or latency breach; manual via one-flag kill switch,
tested quarterly, working out-of-band.

---

*Precedent sources accessed 2026-10-03. Archaeological sources:*
*`design/principal/02-field-archaeology.md`. Practitioner mechanics:*
*`research/sre-field/2026-10-02-escalation-dedup-craft.md`,
`research/sre-field/2026-10-03-webhook-payloads.md`. Design constraints:*
*`design/principal/00-laws.md` (Laws 1, 2, 3, 7); ADR-001/007.*
