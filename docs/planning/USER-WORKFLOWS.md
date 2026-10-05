# SESSION B3 — USER WORKFLOWS: How Users Actually Use Sentinel

**Session:** B3 of the all-chiefs planning meeting · **Date:** 2026-10-05 IST
**Facilitator/scribe:** session subagent (this document)
**Chiefs present:** **Pager** (on-call veteran, principal for the human at 3 AM),
**Prism** (designer, owns the interface constitution), **Oracle** (decision
scientist, calibration and probability), **Ledger** (auditor, owns the evidence chain).
**Standards applied:** `principal-systems`, `principal-governance`,
`principal-mindset`, `execution-doctrine` (all four loaded and followed).
**Inputs:** `docs/architecture-revision/{prism-ui,forwarder-byok,deterministic-gate,event-log-audit}.md`,
`design/INTERFACE_PRINCIPLES.md` v1.1, `platform/server/app.py` (read endpoints),
web research on PagerDuty / Opsgenie / Grafana OnCall practice (sources at bottom).

**Session rule (Noise discipline):** the four chiefs recorded independent reads
*before* any discussion. The facilitator saw the spread, then ran the fights.
Forced probabilities on every verdict (`principal-mindset` §7). Disagree-and-commit
after each verdict. **Resulting ban applies:** judge these decisions on what was
known on 2026-10-05, never on outcomes.

**Standing context the session refused to forget:** Sentinel is a *page-or-suppress*
middleware — proof engine first, suppression engine second. It exists to reduce
on-call manual load and kill the frustration of millions of alerts. The user is a
sleep-deprived operator whose reward structure punishes a missed page and barely
notices a thousand correct suppressions. Design with that incentive, not against it
(`principal-systems`, product/design constitution).

## Session pre-mortem (one year out, the workflows failed)

*Assumed: it is October 2027 and the user workflows contributed to churn. What happened?*

1. **Onboarding lied.** An operator finished onboarding, the console said
   "key configured," and the first real 3 AM page dead-lettered — the UI key never
   reached the durable forwarder (the forwarder memo's §2.1 split-brain). The
   customer discovered it during a SEV1. *(Mitigation: workflow 1's key-resolution
   verification — "configured" fused to truth; test page with a real receipt.)*
2. **The storm drowned the triage.** During a vendor outage, the river showed
   40,000 dense rows; the operator missed the one critical row and PagerDuty
   escalated what Sentinel had already decided. *(Mitigation: workflow 2's
   storm mode as a designed state with its own 3 AM test, not an edge case.)*
3. **Two ack planes.** An operator acknowledged in the Sentinel console; PD kept
   escalating because ack lives in PD. The on-call lead now calls the console
   "the toy that can't stop the phone." *(Mitigation: workflow 3 — Sentinel never
   acks; the console is read-only for incident state.)*
4. **The 3 AM threshold edit.** An operator widened the suppress gate "temporarily"
   at 3 AM to stop the noise; it persisted six weeks and suppressed a real SEV1.
   *(Mitigation: workflow 4 — the attested ceremony; the 3 AM lever pages MORE,
   never suppresses more.)*
5. **The audit nobody could run.** A buyer asked for proof of a suppression; the
   "tamper-evident" panel showed a derived chain, the sealed log had no runtime
   endpoint, and the hourly checkpoint job had never been wired to run.
   *(Mitigation: workflow 5 — sealed chain on the read contract, wired controls,
   customer-held sink copies.)*

Every workflow below is designed against one of these futures.

**Target-state markers:** workflows describe the *revised* pipeline being planned.
`[REQUIRES X]` = depends on a revision proposal from the Phase-2 memos, not yet
built. `[GATED Qn]` = a Type-1 question gated on Aditya's word (planning meeting
record, 5 Oct ~20:10 IST).

---

## WORKFLOW 1 — Onboarding: connect alert source → PagerDuty key (BYOK) → test page → first live alert

**Metric:** time-to-first-page (minutes from landing to a page on the phone with a receipt).

### Deliberation record

- **Pager (independent read):** "Onboarding for a paging tool has exactly one
  job: prove a page will arrive. Everything else is decoration. The current BYOK
  surface can say 'configured: true' while the forwarder dead-letters every page
  (forwarder memo §2.1). That is not a gap, it is a lie the onboarding workflow
  must make impossible." — confidence 90.
- **Prism:** "The `start` tour surface exists; the honest move is to make
  onboarding a 3-screen funnel — mode → key → test page — and refuse the 14-step
  wizard. But the DATA_MODE shim is build-fixed: the funnel must branch at screen
  one, live vs simulated, and never let the two mix." — confidence 80.
- **Oracle:** "Time-to-first-page is the right metric, but it has a perverse
  twin: time-to-first-*false*-page destroys trust faster than slow onboarding
  builds it. The test page must go through the *durable* path (outbox → lease →
  receipt), not a shortcut, or the onboarding proof proves nothing." — 85.
- **Ledger:** "The test page must produce the same audit receipt as a real page
  (`forward_confirmed`, `wire_sha256`). An onboarding that leaves no evidence in
  the log is a workflow the auditor cannot reconstruct." — 90.

**The fight (user would abandon here):** Pager vs Oracle on the test page default.
Pager: *"If the default test page is simulated, the operator learns nothing about
whether the 3 AM page will actually arrive — the first real page becomes the test,
and it happens during an incident."* Oracle: *"If we force a real page for every
trial signup, we create alert fatigue with the tool that exists to reduce it —
and a trial user who gets paged at dinner churns."* Prism broke it: the default is
a **real** test page — one tap, receipt displayed, labeled as a test in the PD
incident — because a paging product whose setup cannot prove paging is a demo, not
a tool. Simulated is an explicit, labeled opt-in for evaluators who cannot page yet.

**Committed (disagree-and-commit):** real test page is the default; receipt is the
proof; the funnel is 3 screens; "configured" is fused to key-resolution truth.
(Oracle dissented on the fatigue point, committed on the trust point.)

### The committed workflow

| # | Step | UI surface | The user's decision | What frustration it removes |
|---|------|-----------|---------------------|----------------------------|
| 1 | Land → choose **mode**: live (production backend) or simulated showcase. Build-fixed; the toggle cannot exist in a fixed build (DATA_MODE shim). | `start` tour / `setup` | "Am I evaluating or operating?" — the single most important click; everything downstream branches on it. | The phantom-interactivity class: a console that *looks* live while replaying fixtures. The mode is structural, announced in-band, and the setup screen renders instead of invented data when no backend is configured. |
| 2 | Connect alert source: point the alerting engine (Alertmanager / Datadog / Grafana) webhook at Sentinel's receiver. Receiver 202s only after `decision_requested` commits (I3) — the source gets a real acceptance signal. | `setup` (guided) | Which integration, which routing map entry. [GATED Q2: ingress auth mechanism — Aditya's word.] | "Did Sentinel even receive it?" — the 202 is bound to a committed log row, not a socket close. The reaper closes the crash window (orphaned `decision_requested` redriven as fail-open passthrough) — *a page lost between webhook and decision cannot happen silently.* |
| 3 | Enter PagerDuty key (BYOK): Settings → KEYS → POST `/api/v1/integrations/keys`. Write-only value, last4 display, format validation in operator language, never echoes. | `settings` (KEYS) | Which key (user store vs env fallback) — with the resolution order shown, not hidden. | Key handling is the #1 trust surface: secrets never touch disk in the frozen payload (key injected at send time), `sanitize_error` strips values from error strings. The operator never wonders "where did my key go." |
| 4 | **Key-resolution verification** [REQUIRES forwarder P1]: the console resolves the key through the *single* key contract and displays "key reaches the durable forwarder: verified HH:MM:SS" — or a loud failure. | `settings` (KEYS) | None — this is the system proving itself. The user only proceeds when it is green. | **The damning one, killed at the workflow level:** "configured: true" while pages dead-letter (forwarder memo §2.1). The Integrations status was a proxy for "pages will go out" that rank-ordered with nothing (`principal-mindset` proxy audit). The workflow fuses the proxy to truth: no verification, no onboarding completion. |
| 5 | **Test page** — real by default [REQUIRES forwarder P1 — revised path; CURRENT code is a direct single POST bypassing the outbox (`app.py:477` → `_pd_enqueue`, no claim/retry/ForwardReceipt — see diagrams `seq-byok-setup.mmd` D-4)]: POST `/api/v1/integrations/test-page` → durable outbox → claim → send → `forward_confirmed` receipt with `wire_sha256`. The PD incident is labeled a test. The operator sees the receipt in the console AND the page on their phone. | `settings` → `river` | "I have seen a page arrive on my phone" — explicit confirmation, the onboarding proof. | The "did it work?" ambiguity that kills every alerting-tool trial. The receipt is the same artifact as a real page (Ledger's requirement — which is exactly why the test page must move onto the outbox path) — onboarding leaves evidence, not vibes. Simulated test page is a labeled opt-in, absorbed loudly to stderr with `simulated` counted separately — never a silent fake. |
| 6 | First live alert: a real alert flows receiver → gate → decision → forwarder → PD. The river shows it with the five companions. | `river` | None — watch it work. | The weeks-long integration dance of legacy paging tools. Time-to-first-page is minutes; the number is on the wall. |

**What we refuse to build here:**
- We refuse to fetch keys *from* PagerDuty (OAuth import). Secrets flow one way — user → Sentinel — never the reverse. A tool that can pull your paging keys is a tool that owns your paging.
- We refuse auto-discovery of "your alerts" by scanning the vendor. That is surveillance dressed as onboarding.
- We refuse a 14-step wizard. Three screens. If onboarding needs a manual, the product is wrong.
- We refuse "magic connect" buttons that require permissions we don't hold. Every step names the credential it needs and why.
- We refuse to complete onboarding while key-resolution verification is red. A green setup screen over a broken paging path is the pre-mortem, not a feature.

---

## WORKFLOW 2 — Daily triage: the decision river

**Metric:** time-to-shift-confidence — the on-call engineer starts their shift and
within 2 minutes knows what paged overnight and what was suppressed, *and trusts it*.

### Deliberation record

- **Pager:** "I've started a hundred shifts the same way: open the alert inbox,
  scroll 400 items, build the mental model from scratch. The river must replace
  that ritual, not prettify it. The first screen answers: anything critical since
  I slept? What did the machine suppress while I was away? Is what I'm seeing
  live? If those three aren't above the fold in 30 seconds, I'm back in Gmail."
  — 90.
- **Prism:** "P4 density is the feature — ~15+ meaningful values per viewport —
  but Appendix B's pre-mortem #3 is real: density becomes noise at 3 AM in a
  storm. The answer is not less density, it is *structural* storm handling: storm
  continuation folds into the aggregate page by construction (`digest_storm` never
  calls the policy kernel, cannot suppress), and storm mode is a designed state
  with its own 3 AM test." — 85.
- **Oracle:** "Trust in suppression is the whole product. Every suppressed row
  must carry the five companions (P2): evidence, quantized uncertainty,
  freshness, policy version, fallback reason. A suppress row without these is not
  minimal — it is unauditable, and the operator will do what every operator does
  with an unauditable suppression: disable it." — 90.
- **Ledger:** "The shift handoff is an audit event. 'What did Sentinel suppress
  in the last hour, and can I see exactly why' is the 5th 3 AM question — the
  river must answer it from the log, not from a cache that can disagree with the
  log." — 80.

**The fight (user would abandon here):** Pager vs Prism on the storm.
Pager: *"Your density floor is going to kill someone at 3 AM. Forty thousand rows
is not 'information density,' it is the millions-of-alerts fatigue with better
typography."* Prism: *"The alternative — pagination, collapsing, 'smart' hiding —
is how the critical row gets hidden by the machine's judgment instead of shown
by the machine's evidence. Severity ordering is structural; the fold is in the
engine, not the UI."* Committed: storm mode is a **designed state** — the river
contracts to aggregate pages + the storm digest with its advisory root-cause
candidates, the fold is engine-side (digest path, never the suppress path), and
the 3 AM test runs the storm scenario quarterly. Density stays; the storm gets a
state, not a scrollbar.

**Committed (disagree-and-commit):** river-first landing; storm mode as designed
state; five companions on every disposition; the river is the handoff.

### The committed workflow

| # | Step | UI surface | The user's decision | What frustration it removes |
|---|------|-----------|---------------------|----------------------------|
| 1 | Shift starts → console lands on **S1 decision river**. First glance: freshness badge (live/cached/stale/degraded — text, never color-only), last-hour suppression summary, severity-ordered rows. | `river` | None — orientation is automatic. The 30-second read: "three criticals paged in the last 10 minutes, all with fresh evidence, system live." | The inbox-scroll ritual. The operator never reconstructs the night from 400 alert emails, Slack threads, and a dashboard that might be cached. Freshness is always stated — "is this live?" is answered before it is asked. |
| 2 | Scan the river: severity → action → evidence per row (the §5.1 hierarchy law). Each row: disposition + quantized confidence (never false precision), as-of + freshness, one-line evidence. Virtualized for 10k+ rows; 60fps scroll. | `river` | "Do I trust this suppression?" — the only triage decision, per row. Everything else is reading. | **Millions-of-alerts fatigue.** The operator never opens 400 alerts. Suppressed rows are *shown with their proof*, not hidden — the fatigue machine (an inbox that treats every alert as equal) is replaced by a river that treats every decision as evidenced. |
| 3 | Drill into any suppression → **S2 calibration & evidence**: the full evidence bundle, quantized probability with calibration context, freshness proofs, policy version, the **counterfactual receipt** ("at your 0.85 this stood down; at 0.70 it would have paged"), fallback reason when the deterministic path decided. | `calibration` (S2) | "Is this suppression defensible?" → if yes, move on; if no, dispute (appeal — wired, audit-logged [REQUIRES prism-ui P1; GATED Q8]). | "Why didn't this page?" — today a Slack archaeology expedition across three tools. Here it is one click: the exact evidence the gate saw, the exact policy version that decided, the exact counterfactual. Trust is built row by row, or the product dies — there is no third option. |
| 4 | Answer the 5th 3 AM question: "what did Sentinel suppress in the last hour, and can I see exactly why?" — filter `action=suppress`, each row carrying its five companions. | `river` (filtered) | None — this is the standing trust ritual, not a decision. | The unauditable-suppression death spiral: operators who cannot see *why* something stood down disable suppression entirely, and the fatigue returns. The ritual keeps the trust account funded. |
| 5 | Shift ends → **the river is the handoff.** Next operator starts at step 1. No verbal ritual, no "anything I should know" — the river plus the suppression summary *is* the state of the world. | `river` | None. | The 20-minute handoff call and the "oh I forgot to mention" page at 4 AM. State lives in the log and the river reads the log — the handoff cannot be forgotten because it was never a conversation. |

**What the operator never has to do again:**
- Scroll an unfiltered alert inbox to figure out what mattered.
- Ask "is this dashboard live?" — the freshness badge answers, always, in text.
- Tolerate a suppression they cannot defend — every suppress carries its proof, or it is a bug (and bugs against P2 are release-blocking).
- Rebuild context after a refresh — SSE with `Last-Event-ID` resume; reconnecting is brief and honest; deep links (`fpr=`, `sev=`, `in=`) survive.

**What we refuse to build here:**
- We refuse an alert inbox that lists everything unsorted. That is the fatigue machine we exist to replace — building it would be building our own competitor.
- We refuse "mark all as read" bulk-dismiss. Dismissal without evidence is a lie told to the next shift.
- We refuse user-reorderable severity. The severity taxonomy is a Type-1 decision (R16, Aditya's sign-off) — a shared language the whole org argues postmortems in. Custom orderings fork the language.
- We refuse auto-refresh that jumps scroll position. The operator's attention is a scarce resource; the interface never steals it (eternal friction includes the operator's own constraints).
- We refuse a second triage surface. The river is the triage. Five surfaces, five questions, closed by default (INTERFACE_PRINCIPLES §3) — a sixth surface is a maintenance liability and a trust fork.

---

## WORKFLOW 3 — Incident response: page arrives → acknowledge → investigate → resolve → postmortem seed

**The page must carry its proof.** Metric: time from page to "I understand what this
is" (target: under 60 seconds, phone in hand).

### Deliberation record

- **Pager:** "Real incident flow, from the research: the page arrives (PD Events
  API v2 trigger, dedup_key correlating into one alert), you acknowledge to stop
  escalation — ack is ownership, it halts the escalation chain, and the ack
  timeout re-triggers if you go quiet. Then you assess impact, pull in responders,
  mitigate first, and resolve only when monitoring confirms stability — not a
  momentary dip. The postmortem happens within 48 hours, blameless, with owned
  action items. Sentinel's job is to make steps 1 and 3 instant and step 5
  free." — 90.
- **Prism:** "I proposed an in-console acknowledge — one less context switch at
  3 AM. I was wrong and the session talked me out of it (see the fight). The
  console's contribution is the evidence bundle behind the deep link, reachable
  from the phone in one tap, with the five companions rendered for a
  cognitively-impaired operator: severity → action → evidence, no hunting." — 80.
- **Oracle:** "The page should carry a calibrated prior: 'this paged with 0.87
  (policy v14), freshness fresh as of 03:12:04, counterfactual: at 0.90 it would
  have stood down.' That is the proof. A page that says 'CRITICAL: api latency'
  is a page that starts a 20-minute context-assembly tax." — 85.
- **Ledger:** "Every step of the response must be recoverable from the log
  afterward: the `decision_made`, the `forward_confirmed` (the ONLY event that
  means 'paged' — the river rule), the retry annotations, the episode resolution.
  The postmortem timeline is an export, not a reconstruction." — 90.

**The fight (user would abandon here — the sharpest of the session):** Prism's
in-console acknowledge vs Pager's veto. Pager: *"If the console has an ack button
and PD has an ack button, there are two acknowledgment planes. The day PD
escalates an incident the operator 'acknowledged' in our console — because ack
lives in PD's escalation chain, not ours — the on-call lead deletes our bookmark
and tells the team the console is a toy. Split-brain ack fails 100% eventually:
PD's ack-timeout semantics guarantee it."* Ledger: *"Two ack planes is two audit
trails that can disagree. Un auditable."* Oracle: *"The expected value of saving
one context switch is dwarfed by the cost of one escalation-during-acknowledged
incident. This is not close."* Prism disagreed, then committed: **Sentinel never
acks, never resolves, never pages from the console.** The read-path law (P5) is
extended: the console is read-only for incident state, full stop. (The appeal
control is the deliberate, narrow, audited exception — a *governance* action, not
an incident action — and it must be wired to a real endpoint or removed:
phantom interactivity on a safety control is release-blocking, prism-ui memo A4.3.)

**Committed (disagree-and-commit):** one ack plane (PD); the page carries the
evidence handle; investigation is one tap to S2; the postmortem timeline is a log
export.

### The committed workflow

| # | Step | UI surface | The user's decision | What frustration it removes |
|---|------|-----------|---------------------|----------------------------|
| 1 | **Page arrives** on the phone via PagerDuty (trigger event, dedup_key-correlated into one alert). The page's custom details carry the proof handle: `sentinel_decision_url` (deep link: `fpr=` + decision id), severity, quantized confidence, policy version, and on retries the `sentinel_retry`/`attempt_no` annotations. | Phone (PD) → deep link → `calibration` (S2) | "Is this real?" — answered from the page itself: the evidence summary is on the page, the full bundle is one tap away. | **The bare page.** Today's page says "CRITICAL: api latency" and the operator spends 20 minutes assembling context from five tools at 3 AM. The Sentinel page carries its proof — investigation starts where context-assembly used to be. |
| 2 | **Acknowledge — in PagerDuty, never in Sentinel.** Ack = "I own this" → escalation halts (PD semantics). Reassign if it belongs to another team/escalation policy. The ack-timeout re-triggers if the operator goes quiet — the safety net stays armed. | PagerDuty (app/web) | Take ownership, or reassign. Snooze only with a stated reason. | The split-brain ack that kills trust in every middleware that tries it. There is exactly one ack plane, and it is the vendor's — "I acked it but it kept escalating" is structurally impossible. |
| 3 | **Investigate** — follow the deep link to S2: the evidence bundle (what the model saw), quantized probability + calibration context, freshness proofs, policy version, the counterfactual receipt, the fallback reason if the deterministic path decided. Cross-link to S4 for the event timeline. Severity → action → evidence, keyboard-operable, no modals, no hover-only information. | `calibration` (S2) → `audit` (S4) | Mitigate first, investigate second (response playbook): rollback / scale / isolate — then root-cause. Add responders as needed. | The 3 AM context hunt. The five companions (P2) mean the operator sees *why the machine paged* in under 60 seconds — the decision is defensible in the postmortem because it was legible in the moment. |
| 4 | **Resolve — in PagerDuty** (or auto-resolve via the PD integration when the monitor clears). Resolve only after monitoring confirms *sustained* stability — not a momentary dip (research: validate through the recovery window). Resolution notes go to PD. Sentinel records `episode_resolved`; the river fold renders the honest state. | PagerDuty | "Is it actually fixed?" — the decision the machine never makes for you. | Premature resolution and the re-page. The workflow refuses to auto-resolve on a dip; the human decides, with the evidence in front of them. |
| 5 | **Postmortem seed** — S4 audit explorer: pull the unbroken hash-chained timeline for the incident, export it (events.jsonl + seals). The export seeds the postmortem template (Google SRE: Summary → Impact → Root Causes → Trigger → Resolution → Detection → Action Items → Lessons → Timeline). The timeline is the export; the judgment is human. | `audit` (S4) | Action items with owners and due dates — the only part of the postmortem that compounds. | The postmortem archaeology: today the timeline is rebuilt from Slack scrollback and memory, within 48 hours or not at all. Here it is an export — Timeline is free, so the session spends its time on Root Causes and Action Items, which is where the leverage is. |

**What we refuse to build here:**
- We refuse in-console acknowledge/resolve. One ack plane. (Settled above — the session's sharpest fight.)
- We refuse a ChatOps bot that acks for you. Ack is ownership; ownership is a deliberate human act. Automated acks are how incidents get "owned" by nobody.
- We refuse auto-resolve on a momentary quiet. Sustained recovery window or the incident stays open.
- We refuse a postmortem *narrator* — an LLM that writes "what happened." The log supplies the timeline; the human supplies the judgment. A generated narrative is W1-shaped: derived content rendered as fact.
- We refuse to page from the console, ever — including the appeal path's audit trail (the appeal pages through the *paging path*, attested and logged; the console itself holds no paging capability).

---

## WORKFLOW 4 — Policy management: change a suppression threshold with attestation

**The R-1 revised ceremony.** Metric: time from "we need to move the bar" to an
attested, live, expiring policy change — with zero unattested generations ever
reaching the kernel.

### Deliberation record

- **Pager:** "The 3 AM operator path is the whole workflow. At 3 AM, woken for
  the fourth time by a noisy gate, the operator wants ONE thing: make it stop.
  Every paging tool in history has given them a config file for that. The
  pre-mortem is written in past tense for a reason: the 'temporary' widening
  that persisted six weeks and suppressed a real SEV1. The ceremony must make
  the 3 AM edit *impossible*, and give the operator a different lever that
  actually helps at 3 AM." — 95.
- **Prism:** "The UI's role here is S3 (simulator) and S5 (shadow): show the
  operator what moving the bar *would have done* — every value labeled
  SIMULATED, exports watermarked. The UI never edits policy. A threshold input
  in the console that writes to the kernel is the phantom-interactivity class
  wearing a governance costume." — 85.
- **Oracle:** "The suppress threshold (0.002, from the expected-cost math
  C_FP=$100 / C_FN=$50,000) is the highest-leverage number in the system and the
  least-observed. The simulator must show the tradeoff table the tuner prints —
  'raising to 0.90 would have suppressed 12 more pages last week, including 2 I
  would not have wanted suppressed' — before anyone touches the ceremony. And
  dual attestation: two humans, async, independent (Noise), never the author." — 85.
- **Ledger:** "The attestation binds (policy_id, version, content_hash,
  from_state, to_state, decided_at) with 7-day max age — and the binding is
  worthless unless the kernel reads *only* attested generations (gate memo P1:
  ConfigLoader refuses generations not covered by a LIVE/REVIEW_DUE version;
  mismatch fails toward paging). The ceremony's output is an event in the log,
  or the ceremony didn't happen." — 90.

**The fight (user would abandon here):** Oracle vs Pager on the 2-attestor rule
at a small team. Oracle: *"At a five-person startup, requiring two attestors
means a policy change takes three days. The operator will route around the
ceremony — edit the JSON, restart, done — and then your governance is theater
over an empty stage, exactly the anti-theater audit's decay mode."* Pager:
*"The routed-around control is worse than the slow control, because the slow
control is at least honest about being slow. And the 3 AM case isn't solved by
making attestation faster — it's solved by making the 3 AM lever something
other than the threshold."* Committed: the ceremony's **mechanics** are <5
minutes via the operator CLI (manual first — a 10-line ceremony beats the
unbuilt one); the wait is only for the second human, and the second human
reviews *with* the shadow divergence data, not vibes. And the 3 AM lever is
explicit: the fail-open ladder (pages MORE, never suppresses more), storm
digest, secondary channel — the emergency path degrades toward the human, never
away. Oracle dissented on the latency, committed on the structure (forced
probability 70% the dual-attestation survives contact with a real 3 AM operator
— the alternative is the pre-mortem, verbatim).

**Committed (disagree-and-commit):** the B3 lifecycle is the ONLY policy-change
path (no side doors); the kernel reads only attested generations; the 3 AM
operator cannot widen suppression — by design, not by policy doc.

### The committed workflow

| # | Step | UI surface | The user's decision | What frustration it removes |
|---|------|-----------|---------------------|----------------------------|
| 1 | Need identified: S5 shadow report shows disagreements, or S3 simulator shows a better tradeoff. The evidence for change exists *before* the ceremony starts. | `shadow` (S5) / `simulator` (S3) | "Is the current bar wrong?" — decided from disagreement evidence, not from being woken up. | Change-from-pain instead of change-from-data. Today thresholds get edited at 3 AM because someone is suffering; here the suffering is routed to the 3 AM lever (step 7) and the threshold change starts from measured divergence. |
| 2 | Simulate: move the threshold in S3 against historical decisions; see which past decisions flip. Every value labeled SIMULATED (full-surface treatment); exports watermarked `# SIMULATED PROJECTION`; ack-gate before export. | `simulator` (S3) | "What would this have done last week?" — including the 2 suppressions you would NOT have wanted. | The blind threshold edit. The operator sees the tradeoff table *before* the ceremony — the simulator is where bad ideas die cheaply (most policy proposals should die here; a simulator that never says no is theater). |
| 3 | Draft via the operator CLI: `create_draft` with the new thresholds; preview hash bound at draft; content freeze semantics. The B3 state machine: draft → shadow → canary → live → review-due → expired. | CLI (operator runbook) | The author writes the change and the reason. The author cannot attest their own change. | The unattested JSON edit — the exact pre-mortem. There is now one door, it has a lock, and the lock's key is two humans. |
| 4 | Shadow: the new policy runs against live traffic without deciding. Divergence measured on S5: agreement rate, disagreements with bilateral evidence. | `shadow` (S5) | "Does the new policy agree with reality?" — the shadow report is the evidence the attestors review. | Shipping a threshold on vibes. Shadow is the validate→shadow→canary pipeline made concrete: the policy proves itself against live traffic before it touches a single real decision. |
| 5 | Canary → live: **2 distinct attestors** (≠ author) sign; signatures bind (policy_id, version, content_hash, from_state, to_state, decided_at); 7-day max age. Each attestor judges independently and asynchronously before any discussion (Noise discipline). | CLI + ceremony log (hash-chained, in the event log) | Each attestor: "do I sign?" — with the shadow data in front of them. | The single-human 3 AM widening. Two humans, neither the author, with data — the control is proportionate to the stakes (this number decides who gets woken). |
| 6 | Live: content freeze; **the kernel reads only attested generations** [REQUIRES gate P1] — ConfigLoader refuses any generation whose content-hash is not covered by a LIVE/REVIEW_DUE attested version; mismatch fails toward paging, never suppress. Every decision renders its policy version (P2). The review-due → expired clocks run automatically via `tick()`. | `river` / `calibration` (policy version on every row) | None — the machine enforces. Expired policy ⇒ suppress unreachable ⇒ pages. | **The six-week silent widening.** Changes expire. They are witnessed. They are content-bound to what the kernel evaluates. An expired or unattested generation cannot suppress — the failure direction is always toward the human. |
| 7 | **The 3 AM path** (the reason this workflow exists): the operator at 3 AM does NOT touch thresholds. The emergency levers are: the C1 stepped fail-open ladder (step 1: last-known-good signed policy; step 2: static severity floor; step 3: rate-capped paging + digest — each step a named, event-logged transition), storm digest mode, the secondary channel. All degrade toward paging MORE, never suppressing more. Emergency override exists with **auto-revert**. | `river` (degraded banners, honest and explicit) | "Which fail-open step are we on, and is the human loop intact?" — never "how do I make it stop." | The 3 AM threshold edit and everything downstream of it. The operator gets levers that *help* at 3 AM (fewer decisions to make, more paging, digest instead of storm) instead of the one lever that *kills* (silent suppression widening). This is designing with the operator's incentives: at 3 AM they want relief; give them relief that cannot suppress a SEV1. |

**What we refuse to build here:**
- We refuse threshold editing in the UI. The console reads; it never evaluates, never pages, never suppresses, never changes policy (P5, extended). A threshold input in the console is governance theater with a text box.
- We refuse "temporary overrides" that don't auto-revert. A temporary that persists is permanent with extra steps — the pre-mortem, verbatim.
- We refuse policy-as-code PRs that bypass the state machine. The B3 lifecycle IS the change path. No side doors, no "emergency" JSON edits, no exceptions for founders.
- We refuse per-alert threshold exceptions set by hand. Exceptions are mutes with TTL — attested, event-logged, expiring — not invisible tweaks.
- We refuse to let D8's missing-file state fail open [REQUIRES gate P1]: "policy gate not configured" must page, not suppress unsupervised. The watchdog-never-set-up state is the state a rushed first deployment ships in, and it is the state where suppression is least supervised.

---

## WORKFLOW 5 — Audit/compliance: prove what happened

**The auditor's path through the event log, sealed checkpoints, and evidence
exports.** Metric: time from "prove incident #4821" to a verifiable evidence
bundle in the auditor's hands (target: under 30 minutes, no Sentinel engineer involved).

### Deliberation record

- **Ledger (independent read):** "The mechanism layer is genuinely good — frozen
  vocabulary, I1/I2/I3 transaction invariants, test-enforced append-only, honest
  checkpoint primitive. The failures are all in the layers Aditya named:
  requirements (no threat model), wiring (CheckpointJob, Reaper, RetentionJob
  have no callers — code without a caller is a design doc, not a control),
  operations (no customer verification workflow). The auditor's workflow must
  assume the wiring gets built [REQUIRES event-log P2] and the threat model gets
  published [REQUIRES event-log P1] — and until then, the console labels the
  derived chain as derived, honestly." — 90.
- **Pager:** "The auditor is not the 3 AM operator, but they ask the 3 AM
  operator's questions after the fact: what paged, why, what was suppressed and
  why, who changed the policy. The audit explorer must answer those from the
  log, timeline-first — S4 optimizes for completeness and verifiability over
  speed." — 80.
- **Prism:** "S4's 30-second read is 'here is the unbroken chain for incident
  #4821, sealed and exportable.' The chain panel today verifies a *derived*
  chain — labeled, honest, but attesting to nothing about the platform. The
  workflow's end state needs GET /api/audit/chain [REQUIRES event-log P5]: the
  console verifies the platform's seals instead of deriving links." — 85.
- **Oracle:** "The threat model must state the insider-writer limit plainly —
  the checkpoint docstring already does; the product claim should match the
  mechanism. 'Chain verifies' is a proxy for 'history is true' and the proxy
  fails exactly at the insider threat (proxy-trap audit). The honest guarantee:
  post-hoc modification by anyone without the HMAC key is detectable; the writer
  is bounded by the hourly checkpoint the *customer* holds." — 85.

**The fight (user would abandon here):** Ledger vs Prism on what ships first.
Ledger: *"The sealed-chain endpoint and the wired checkpoint job are the audit
story. Without them, S4 is a pretty timeline over a derived chain — the buyer
who asks 'prove it' gets a shrug."* Prism: *"Then we say so, in-band, until it
ships. The derived label is not a placeholder for the real thing — it is the
honest statement of what the console can attest today. What we must not do is
soften the label to close the deal."* Committed: the workflow is specified
against the end state (sealed chain on the read contract, wired controls,
chained checkpoints, customer verification runbook), every `[REQUIRES]` marked,
and the current console keeps the DERIVED label with zero softening. A buyer
deck that screenshots past the label is a sales problem, not an honesty problem —
and the session minutes say so.

**Committed (disagree-and-commit):** publish the threat model as a Type-1
requirement; wire the three orphaned controls; expose the sealed chain;
chain checkpoints to each other; hand the customer a verification runbook.

### The committed workflow

| # | Step | UI surface | The user's decision | What frustration it removes |
|---|------|-----------|---------------------|----------------------------|
| 1 | Auditor opens **S4 audit explorer**, searches by fingerprint / incident / time window → timeline-first view of hash-linked events: `decision_requested` → `decision_made` → `forward_confirmed` (the only event that means "paged") → `episode_resolved`, with policy transitions and mute events interleaved. | `audit` (S4) | Which incident, which window. The explorer answers "what happened" from the log, not from anyone's memory. | The post-incident "what actually happened" meeting where three people remember three timelines. The log is the timeline; the explorer is its reading room. |
| 2 | Verify the chain — one command: `python -m sentinel.verify sentinel.db --from-seq <n> --hmac-key-file <customer-key>`. Anchored verification: the checkpoint's HMAC is verified first, its own link checked, then the chain replays forward. Exit 0 intact / 1 broken. [REQUIRES event-log P3: one genesis rule — the verifier that cries wolf on healthy logs trains everyone to ignore it.] | CLI (+ S4 panel showing seal state [REQUIRES event-log P5]) | None — the machine answers. "Corruption and tamper present identically — the verifier reports, it does not distinguish." | "We have logs but can't prove anything." The verification is mechanical, customer-runnable, and named: post-hoc file edits are detected at the sequence number where they happened. |
| 3 | Check the seals: hourly HMAC-signed checkpoint docs `(head_seq, head_hash, event_count, window, prev_checkpoint_hmac, segment_id)` pushed to the **customer-controlled sink** — the customer holds the sink copy. Checkpoints chain to each other [REQUIRES event-log P6], so a between-checkpoint rewrite breaks the sink-copy chain. The sink-failure page bound (>2h) survives restarts (counter in the log, not memory). | Customer sink dir + verification runbook | Which checkpoint to anchor from (the customer's copy, not the writer's). | The writer-holds-the-pen problem, bounded honestly: a compromised writer can forge *within* a checkpoint window, but cannot rewrite history across checkpoints the customer holds without breaking the chain the customer verifies. The limit is stated in the threat model (REQUIRES event-log P1) — the product claim matches the mechanism. |
| 4 | Export evidence — fixed order, never reordered: verify chain → write `events.jsonl` → seal HMAC'd manifest → checkpoint the archive linkage into the live log → prune. A broken chain refuses to prune (`ChainBroken` pages). Retention tiers: hot (self-calibrating 60-day floor — the job pages instead of silently shortening) → warm (365d full-fidelity, `raw_payloads` and outbox lifecycle rows included [REQUIRES event-log P8]) → cold (7y summaries, expiry only with explicit customer liability acceptance). | S4 export / CLI | Whether to accept cold expiry (explicit liability acceptance — a decision, not a default). | The retention-policy-that-isn't (today: a disk watermark alarm, not a policy) and the archive that silently drops the evidence the chain references. After this workflow, "what did the vendor actually send six months ago" is answerable — the payload is content-addressed in the archive, not deleted with the segment. |
| 5 | Hand over the bundle: `events.jsonl` + HMAC manifest + checkpoint docs + segment ledger. The auditor/regulator verifies with the *customer's* key against the *customer's* sink copy — without trusting Sentinel, without a Sentinel engineer in the room. GDPR: salted-hash tombstones actually invoked on the warm path [REQUIRES event-log P7] — the privacy story is code that runs, not prose that reads. | Out-of-band (the bundle) | None — the bundle is self-verifying. | The audit that requires the vendor's cooperation to complete. The customer holds everything needed to verify: the sink copies, the key, the runbook, the one-page verification workflow (what to archive, what to check, how often, what a >2h checkpoint gap means). |

**What we refuse to build here:**
- We refuse Ed25519-for-checkpoints as the trust fix. The checkpoint docstring already records the honest analysis: asymmetric signatures don't fix the writer-holds-the-key problem (restatement test — it changes the number, not the belief). The belief-changing work is the threat model, chained checkpoints, and the customer-held sink.
- We refuse a "Verify" button that checks the writer's DB with the writer's CLI and calls it independent verification. The customer verifies with the customer's key against the customer's sink copy, or it is theater.
- We refuse log *search* as the audit story. Search is for triage; proof is the chain + seals + exports. A search box over a tamperable index proves nothing.
- We refuse silent tier degradation. If the warm tier drops evidence the chain references, the RFC states it explicitly with the reason — auditability that degrades silently across tiers is normalization of deviance.
- We refuse the disposable decisions VIEW without a dated kill [REQUIRES event-log P9]. A shim without a date is a second schema; the grep guard evolves from "no UPDATE on events" to "no new reads of the disposable VIEW."

---

## Cross-workflow refusals (what Sentinel refuses to be)

These were committed once, at session level, and bind all five workflows:

1. **The console reads; it never evaluates, never pages, never suppresses, never
   asks Jev on read paths** (P5, extended by this session to incident state:
   never acks, never resolves). The narrow, audited exceptions are governance
   actions (attested mutes, the attested policy ceremony, the wired appeal) and
   the BYOK key surface — each with its own audit event (R24).
2. **One control plane per function.** Ack lives in PagerDuty. Paging lives in
   the forwarder. Policy lives in the attested lifecycle. The console duplicates
   none of them. Two planes that can disagree are worse than one plane that is
   slow.
3. **No temporary anything that persists.** Overrides auto-revert. Policies
   expire. Mutes carry TTLs. Keys rotate with overlap windows. A "temporary"
   without a clock is permanent with extra steps.
4. **Freshness is always stated, never inferred.** Every data surface carries
   value + as-of + freshness state, in text. "Unknown freshness" is a bug, not
   a fifth state.
5. **Derived values are labeled at the point of display, never in a footnote.**
   The reconstruction law (W1) binds every workflow, including this document's
   `[REQUIRES]` markers — the plan labels what is not yet built.

## Sharpest fights log (session record)

| # | Fight | Sides | Verdict | Sharpest "abandon here" line |
|---|-------|-------|---------|------------------------------|
| 1 | In-console acknowledge | Prism proposed, Pager vetoed, Ledger/Oracle backed veto | **Sentinel never acks.** One ack plane (PD). | Pager: *"The day PD escalates an incident the operator 'acknowledged' in our console, the on-call lead deletes our bookmark and calls the console a toy."* |
| 2 | Storm-mode density | Pager vs Prism | **Storm mode is a designed state** with its own 3 AM test; density stays. | Pager: *"Forty thousand rows is not information density, it is the millions-of-alerts fatigue with better typography."* |
| 3 | Test page default (real vs simulated) | Pager vs Oracle | **Real test page default**; receipt is the proof; simulated is labeled opt-in. | Pager: *"If the default test page is simulated, the first real page becomes the test — and it happens during an incident."* |
| 4 | 2-attestor rule at small-team speed | Oracle vs Pager | **Ceremony mechanics <5 min via CLI**; the human gate stays; 3 AM lever is fail-open, not thresholds. | Oracle: *"The operator will route around the ceremony, and then your governance is theater over an empty stage."* |
| 5 | Derived chain label vs sealed-chain endpoint | Ledger vs Prism | **End-state specified, REQUIRES marked, DERIVED label unsoftened** until the endpoint ships. | Ledger: *"The buyer who asks 'prove it' gets a shrug."* / Prism: *"What we must not do is soften the label to close the deal."* |

## Sources (web research grounding)

- PagerDuty incident response: acknowledge stops escalation; ack-timeout
  re-triggers; reassign/restakeholder/response plays; resolution notes; mobile
  ack/resolve. <https://community.pagerduty.com/pagerduty-user-onboarding-13/responding-to-incidents-389>
  and <https://community.pagerduty.com/pagerduty-user-onboarding-13/responding-to-incidents-in-pagerduty-372>
- PagerDuty Events API v2: trigger/acknowledge/resolve actions; `dedup_key`
  correlation; integration-key + test alert flow.
  <https://medium.com/@code.chandrashekhar/pagerduty-deep-dive-complete-guide-with-real-time-practical-approach-81010d0b579f>
- Opsgenie: acknowledge-as-ownership; two-way ServiceNow sync (ack reflected);
  auto-generated postmortems; teams with on-call schedules and custom roles;
  time-to-acknowledge / time-to-resolve reporting.
  <https://community.atlassian.com/forums/Opsgenie-articles/Opsgenie-in-a-nutshell/ba-p/1599023>
- Grafana OnCall: alert-group feed (Mine/All tabs); acknowledge/resolve/silence
  with TTL; bulk acknowledge with `--force` guard; idempotent actions;
  escalation chain on the group view.
  <https://grafana.com/docs/oncall/latest/manage/notify/mobile-app/alert-groups-feed/?src=feedly&tech=target&pg=docs-grafana-cloud-send-data-traces-set-up&plcmt=sidebar-banner>
- Real on-call runbook anatomy (alert naming; "if it is just one error,
  acknowledge and go back to sleep; if there are a lot, keep investigating";
  Grafana → logs investigation order). <https://hackmd.io/@YgTM6m7hQiGwjyMT797PCw/rJXwws-ri>
- Incident lifecycle: mitigate-first ordering; sustained-recovery verification
  (not a momentary dip); blameless postmortem within 24–48h with owned action
  items; the pipeline architecture (observability → alerting engine → on-call
  notification → coordination → diagnosis → mitigation → recovery verification
  → postmortem).
  <https://medium.com/@akshay_6555/incident-management-in-devops-the-complete-enterprise-guide-to-detecting-responding-and-learning-8408d4a13213>

## Session close

Five workflows committed, each naming the frustration it kills and the scope it
refuses. The session's through-line, stated by Pager in close: *"Every workflow
above is a promise about what the operator never has to do again. The day any of
them becomes a prettier version of the old ritual — an inbox, a second ack
button, a threshold text box, a verify button that checks our own homework — we
will have rebuilt the fatigue machine with better typography. The refusals are
the product."*

**Open items carried forward:** `[GATED Q2]` ingress auth mechanism, `[GATED Q8]`
appeal-button wiring — both await Aditya's word (planning record 5 Oct ~20:10
IST). All `[REQUIRES]` markers trace to Phase-2 revision proposals with named
verifiers; none are "consider" or "explore."
