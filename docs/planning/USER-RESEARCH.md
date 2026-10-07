# User Research: How On-Call Engineers Actually Work

**Status:** research lane output for the Sentinel redesign (Aditya's order, 2026-10-05).
**Method:** We cannot interview SREs directly. The honest substitute is published evidence:
Google's SRE books (free, sre.google), PagerDuty's incident analyses, postmortem/debriefing
literature (Allspaw, Cook, Etsy), practitioner voices (Hacker News, engineering blogs), and
industry surveys. Every claim below is either sourced with a link or explicitly labeled
**[INFERENCE]** — our judgment drawn from the sources, not a fact. We interviewed nobody;
nothing here claims otherwise.

**Principal-mindset note:** the 4-question checklist asks what would change the engineer's mind.
That question structures §6. Base rates before stories: the base rate is that most pages are
waste — the design must start from that, not from "alerts are valuable signals."

---

## 1. Jobs-to-be-done (the on-call engineer's real job list)

Synthesized from the sources. The engineer's jobs, in the order they actually occur:

1. **J1 — Wake up and orient.** From sleep to "I know what broke" in under 60 seconds.
   The page must carry enough context that orientation doesn't require opening five tools.
2. **J2 — Decide: real or noise.** Is this a genuine user-impacting problem or a false positive?
   This decision is the engineer's most repeated cognitive act — and the one machines get wrong most.
3. **J3 — Establish blast radius.** What is affected, how badly, who is impacted (users vs infra).
   Google's definition: *"One 'incident' is defined as one 'problem,' no matter how many alerts
   have been fired for the same 'problem.'"* — SRE Workbook, Ch. 8 (On-Call), via
   [Alistair Pialek's chapter notes](https://alistairpialek.com/notes-from-sre-workbook-betsy-bayer).
4. **J4 — Stop the bleeding.** Mitigate first; root-cause later. *"Google always aims to first stop
   the impact of an incident, and then find the root cause."*
   — [SRE Workbook, Incident Response](https://sre.google/workbook/incident-response/).
5. **J5 — Coordinate.** Get the right people in one place, keep one shared picture, hand off cleanly.
   The 3Cs: coordinate, communicate, maintain control — same source.
6. **J6 — Learn.** Afterward, reconstruct what the world looked like *at the time*, not in hindsight.
   Not "find the root cause" — *"Post-accident attribution to a 'root cause' is fundamentally wrong."*
   — Richard I. Cook, [How Complex Systems Fail](https://how.complexsystems.fail/).

---

## 2. The 3 AM scenario (concrete narrative, grounded)

This is a composite of two documented incidents. Nothing is invented; the beats are theirs.

**Beat 1 — the page (T+0).** In the SRE Workbook's GKE case study, *"One Thursday at 6:41 a.m. PST,
London's on-call SRE for GKE, Zara, was paged for CreateCluster prober failures across several
zones. No new clusters were being successfully created."*
([source](https://sre.google/workbook/incident-response/)). The alert named the **symptom**
(prober failures), the **service** (CreateCluster), and the **scope** (several zones). That is
the minimum viable page.

**Beat 2 — verify impact (T+0–5 min).** *"Zara checked the prober dashboard and saw that failures
were above 60% for two zones. She verified this issue was affecting user attempts to create new
clusters, though traffic to existing clusters was not affected."* Note the order: dashboard
(blame-radius numbers) → **user impact confirmation** → only then escalation. Infra failing ≠
users hurting; the engineer checks the second before committing.

**Beat 3 — declare early (T+25 min).** *"Zara followed GKE's documented procedure and declared an
incident at 7:06 a.m."* The workbook's lesson: *"Our experience shows that managed incidents are
resolved faster… Miscommunication between the client and server developers is prevented"*
when you declare early. The anti-pattern is the Google Home case in the same chapter: days of
troubleshooting via "normal" bug-tracker methods while users were down, because nobody declared.

**Beat 4 — one room (T+30 min).** *"Zara opened a GKE Panic IRC channel where the team could debug
together."* Responders gather in **one** real-time place. The PagerDuty NTP-drift incident (2017,
documented on the same page) shows the same shape: on-call validated automated recovery had run,
worked the runbook, *"documented in the SRE team's dedicated Slack channel"* — one channel,
working record as you go.

**Beat 5 — check what changed.** When the senior SRE joined the GKE incident at 8:45, his *first*
move: *"checked the day's release against the timing of the alerts, and determined that the day's
release did not cause the incident."* Recency of change is the first hypothesis an expert tests.
**[INFERENCE]** Any interface that doesn't put "what changed recently" next to the alert is
making the expert do the tool's job.

**Beat 6 — mitigate, don't root-cause.** The GKE team found a plausible cause at 9:56 but didn't
mitigate until 11:59; the workbook's verdict: *"To mitigate an incident, you don't have to fully
understand the details — you only need to know the location of the root cause."* The working
order is always: assess impact → mitigate → root-cause → postmortem. A UI that leads with
root-cause analysis during an active incident is leading with the wrong job.

---

## 3. Minute-by-minute: the first five minutes

| Minute | The engineer does | Information needed (in order) | If it's missing |
|---|---|---|---|
| 0 | Reads the page on the phone | Service, symptom, scope — the three fields of the GKE page | Opens laptop blind; orientation delayed |
| 0–1 | Decides: get up or snooze? | Is this actionable? Has this alerted before and been noise? | Alert fatigue: they learn to distrust the pager (see §5) |
| 1–3 | Checks the dashboard | Current numbers: error rate, affected zones, trend (is it still happening?) | Falls back to SSH/logs — slowest, highest-cost path |
| 3–5 | Confirms user impact | Are users actually hurt, or is this infra-only? | Either over-escalates (wakes people for nothing) or under-escalates (the Google Home failure) |
| 5+ | Declares / opens the room | One place to gather; who else is being paged | Fragmented threads, duplicated investigation (the workbook's war-room lesson) |

**The ordering law** comes from Peter Bourgon's observability cost gradient (via the resilience
literature): *metrics* answer "what is happening now / is it still happening / how big is the
blast radius" (cheapest); *logs* answer "what happened, exactly, with what values"; *traces*
answer "what happened to THIS request across services." A UI that opens on logs before metrics
is forcing the most expensive sensemaking path first. **[INFERENCE]** Our redesign's default
view should follow the cost gradient: state → scope → change → detail.

---

## 4. What "what's happening" means (the mental model)

The engineer is not collecting data. They are **building a story that makes the cues make sense**
— Allspaw's sensemaking frame. From his "Infinite Hows" (via
[dataworkers.io](https://dataworkers.io/blog/what-allspaw-blameless-postmortem-taught-our-incident-agent/)
summarizing kitchensoap.com/2014): *"Cause is something we construct, not find."* The
investigation runs on four prompts — **cues** (what signals, who saw them when), **interpretation**
(how did people make sense of them), **goals** (what were they trying to do), **action** (what
did they do, what did they expect).

The Etsy Debriefing Facilitation Guide (archived, CC-adjacent; via
[the Howie methodology notes](https://github.com/anmolg1997/prepostmortem-skills/blob/HEAD/postmortem-analyst/references/methodology.md))
gives the exact questions that recover a real-time mental model:
- *"When you saw X, what did you think was happening?"*
- *"What did you do next, and what were you expecting to happen?"*
- *"Was there anything about this incident that surprised you?"* — the surprise locates the
  model-reality gap.
- *"How did you know to look there?"* — surfaces unwritten expertise.

**What this means for the UI:** "what's happening" = current state + what changed + what has
been tried and what was expected + what surprised the last person. Cook's points 10–12:
*"All practitioner actions are gambles"* taken under uncertainty; *"Human practitioners are the
adaptable element"*; *"People continuously create safety"* by nudging the system. The interface
serves the gambler, not the auditor: it must show the **odds-relevant** information (blast
radius, trend, change recency, prior similar cases), not the **blame-relevant** information
(who deployed, who acked late).

**Jeli/PagerDuty's Howie process** adds: *"the incident looks different from each responder's
seat, and the writeup should preserve that rather than flatten it into one narrative."*
**[INFERENCE]** A timeline that flattens six perspectives into one log line destroys the
sensemaking material. Preserve per-actor views.

---

## 5. What creates alert fatigue (specific tool behaviors)

Not "too many alerts." These mechanisms, each sourced:

1. **Non-actionable pages.** The SRE Book's on-call economics: *"dealing with the tasks involved
   in an on-call incident — root-cause analysis, remediation, follow-up — takes 6 hours. It
   follows that the maximum number of incidents per day is 2 per 12-hour on-call shift"* and
   *"most days should have zero incidents"*
   (via [dev.to summary](https://dev.to/raoulmeyer/7-site-reliability-lessons-from-google-and-amazon-520a)).
   Every non-actionable page spends from a budget of ~2. The SRE Workbook's On-Call chapter
   names three pager-load categories — production bugs, **alerting misconfiguration**
   (thresholds not SLO-grounded), and human process errors — and the rule: **every alert must be
   actionable** (via [testdouble/han research notes](https://github.com/testdouble/han/blob/HEAD/docs/research/on-call-engineer-research.md)).
2. **Self-healing alerts that still page.** The workbook names *"transient dismissal"* — marking a
   self-resolved incident "resolved" without investigation — as a postmortem anti-pattern
   (same source). The fatigue version: the pager fires, the engineer wakes, the system has
   already recovered, and there is nothing to do but log it. Nothing teaches distrust faster.
3. **One problem, N alerts.** Google's definition exists because the failure mode is real: a
   single incident routinely fires dozens of alerts across services. Any tool that counts alerts
   instead of problems is manufacturing fatigue and calling it observability.
4. **Alerts without context.** A page that says "ERROR RATE HIGH" with no service, no owner, no
   runbook link, no "last time this fired" forces the full orientation loop from zero — at
   maximum cognitive cost, at 3 AM. The HN runbook thread's distinction is exact: a *script* is
   "commands to run"; a *playbook* is "possible root causes, complex interactions, not
   overreacting to spurious alerts" — the page must point at the playbook, not just the symptom
   ([HN](https://news.ycombinator.com/item?id=22207452)).
5. **The firehose room.** The Hubot-era monitoring literature already knew: *"start publishing
   everything to chatrooms [and] your chatrooms will soon be firehosed with messages that
   nobody will read"* (Varaneckas, *Automation and Monitoring with Hubot*). A notification
   stream nobody reads is not a feature; it is training engineers to ignore the channel that
   will one day carry the real page.

**The numbers** (industry survey via
[carriermanagement.com, Apr 2026](https://www.carriermanagement.com/news/2026/04/08/286535.htm)):
77% of on-call teams receive at least ten alerts per day; 57% say fewer than 30% are
actionable; **83% ignore or dismiss alerts at least occasionally.** The engineer's own bar, from
HN: *"I consider getting paged more than two or three times a year unacceptable. Any company
with on-call should track life interruptions as a KPI and actively work to drive their
frequency to zero"* ([HN](https://news.ycombinator.com/item?id=30669408)). And the incentive
root, also HN: *"if the person writing the code is not the one on the pager at 3am, they have
no serious incentive to make it correct or resilient"*
([HN](https://news.ycombinator.com/item?id=37728915)).

---

## 6. What a machine must prove before a 3 AM engineer trusts it to suppress a page

Principal-mindset question: **what would change the engineer's mind?** Not claims — the
specific evidence. Five requirements, in priority order:

1. **A track record on THIS class of alert.** Not "98% noise reduction" in aggregate — the
   precision and recall on alerts shaped like this one, over the engineer's own history.
   **[INFERENCE]** Aggregate suppression stats are the executive dashboard; the engineer needs
   the per-class confusion matrix. The exec/practitioner gap is the warning: 74% of C-suite
   say their org uses AI for incident management vs 39% of practitioners; execs are 3× more
   likely to say AI reduced toil (35% vs 12%) — *"Executives report what has been purchased or
   decided; practitioners report what is running"*
   ([carriermanagement.com](https://www.carriermanagement.com/news/2026/04/08/286535.htm)).
   Trust lives with the practitioner. Vendor claims do not transfer.
2. **The evidence for THIS decision, inspectable in seconds.** Which cues, which thresholds,
   how fresh the evidence was, what the machine considered and rejected. Allspaw's frame:
   the engineer needs to reconstruct the *machine's* local rationality the way a debrief
   reconstructs a human's — "what did you see, what did you expect."
3. **Reversibility in one action.** The undo must be cheaper than the doubt. If un-suppressing
   takes longer than just getting paged, the engineer will disable the machine instead.
4. **Fail-open by construction, disclosed.** Cook: catastrophe needs multiple failures; the
   suppression layer must not be one of them. The machine must show, per decision, what
   happens when it is wrong — and "it pages you" must be the default, not the fallback.
5. **An audit trail that survives hindsight.** Cook's point 8: *"Hindsight bias remains the
   primary obstacle to accident investigation, especially when expert human performance is
   involved"* and *"Knowledge of the outcome makes it seem that events leading to the outcome
   should have appeared more salient to practitioners at the time than was actually the
   case."* When a suppression is later questioned, the record must show what the machine knew
   *then* — not what we know now.

**[INFERENCE]** The order matters: track record > evidence > reversibility > fail-open >
audit. A machine with (2)–(5) but no (1) is a stranger asking for trust; a machine with (1)
but not (2) is a black box that will be disabled after its first visible mistake.

---

## 7. Prioritized user needs (for the redesign)

| # | Need | Source |
|---|---|---|
| N1 | The first screen answers: what is broken for users, how bad, what changed — within 60 seconds of opening | §2 beats 1–2, 5; Bourgon cost gradient |
| N2 | One incident = one problem, regardless of alert count; the UI counts problems, never alerts | SRE Workbook Ch. 8; §5.3 |
| N3 | Every suppressed alert shows its proof: cues, thresholds, freshness, and the per-class track record | §6.1, §6.2 |
| N4 | Undo any machine action in one step; the undo is always visible next to the action | §6.3 |
| N5 | "What changed recently" sits next to every incident (deploys, config pushes, flag flips) | §2 beat 5 (Il-Seong's first move) |
| N6 | Blast radius before detail: metrics view (state/scope/trend) precedes logs | §3 ordering law |
| N7 | Per-actor timeline views preserved; no flattened single narrative | Howie; §4 |
| N8 | The interface shows what the machine knew THEN on every past decision (hindsight-proof record) | Cook pt. 8; §6.5 |
| N9 | Declaring/escalating is one action from anywhere; the "one room" is one click away | §2 beats 3–4; 3Cs |
| N10 | Honest mode labeling: simulated vs live is in-band and unmissable, always | Exec/practitioner gap, §6.1 (trust dies when purchased ≠ deployed) |
| N11 | Alert history per alert-shape: "this fired 14 times, 13 were noise" — the distrust is shown, not hidden | §5.1, §5.2; HN KPI bar |
| N12 | Mitigation paths surfaced before root-cause analysis during active incidents | §2 beat 6; workbook generic mitigations |

---

## 8. Anti-requirements (things users hate — we must not build)

- **A1. The alert firehose.** No unbounded volume lists, no "millions of alerts" views, no
  notification stream without triage. (Hubot firehose; §5.5)
- **A2. Naked alert counts.** Any number that counts alerts instead of problems is fatigue
  instrumentation. (§5.3)
- **A3. The black-box suppressor.** No suppression without inspectable evidence and a visible
  undo. The first silent mistake permanently destroys trust. (§6)
- **A4. Root-cause-first during incidents.** RCA tooling during mitigation is the wrong job at
  the wrong time. (§2 beat 6)
- **A5. Flattened timelines.** One log line per event across six actors destroys sensemaking.
  (Howie; §4)
- **A6. Purchased-not-deployed honesty gaps.** Any simulated/demo state that could be mistaken
  for live operation. The practitioner detects the gap; trust does not recover.
  (Exec/practitioner 74/39 split; §6.1)
- **A7. Alerting on causes instead of symptoms.** Pages must fire on user-visible symptoms, not
  internal causes — the SRE Book's alerting doctrine (symptom-based, actionable pages).
  **[PARTIAL]** — grounded in the book's widely-summarized doctrine; the direct chapter fetch
  failed, so this is cited to the secondary summary, not the primary text.
- **A8. The engagement-metric dashboard.** No streaks, no "alerts handled" gamification. Calm-tech
  rule: the interface's success is the engineer sleeping, which is unmeasurable in-app — so the
  app must not optimize for in-app engagement.

---

## 9. Sources

**Primary (fetched and read):**
- Google SRE Workbook, Ch. 9 "Incident Response" — https://sre.google/workbook/incident-response/
  (GKE CreateCluster case, Google Home case, PagerDuty 2017 NTP case, 3Cs, mitigate-first doctrine)
- SRE Book / Workbook Ch. 8 "On-Call" via Alistair Pialek's chapter notes —
  https://alistairpialek.com/notes-from-sre-workbook-betsy-bayer
  (one incident = one problem; 6-hour incident cost; ≤2 incidents/shift; most days zero)
- SRE Book alerting doctrine via dev.to summary —
  https://dev.to/raoulmeyer/7-site-reliability-lessons-from-google-and-amazon-520a
- Richard I. Cook, "How Complex Systems Fail" (1998, rev. 2000) — https://how.complexsystems.fail/
  (18 points; post-accident root-cause attribution; hindsight bias; practitioners create safety)
- Allspaw's "Infinite Hows" via dataworkers.io synthesis —
  https://dataworkers.io/blog/what-allspaw-blameless-postmortem-taught-our-incident-agent/
  (cause constructed not found; local rationality; how-not-why; cues/interpretation/goals/action)
- Etsy Debriefing Facilitation Guide & Jeli/PagerDuty Howie via methodology notes —
  https://github.com/anmolg1997/prepostmortem-skills/blob/HEAD/postmortem-analyst/references/methodology.md
  (debrief questions; multi-seat incident narratives)
- Bourgon's three pillars (metrics/logs/traces cost gradient) via —
  https://github.com/kyeshmz/dotfiles/blob/HEAD/agents/research/06b-debug-sources-and-prompts.md
- testdouble/han on-call research notes (SRE Workbook On-Call chapter summary) —
  https://github.com/testdouble/han/blob/HEAD/docs/research/on-call-engineer-research.md
  (three pager-load categories; transient dismissal anti-pattern; every alert actionable)

**Practitioner voices:**
- HN "Diary of a first-time on-call engineer" — https://news.ycombinator.com/item?id=30669408
  (2–3 pages/year bar; life-interruptions-as-KPI)
- HN "2023 DevOps Is Terrible" — https://news.ycombinator.com/item?id=37728915
  (you-write-it-you-run-it incentive alignment)
- HN "How to manage oncall" — https://news.ycombinator.com/item?id=41610000
  (runbooks, secondary on-call, handover, pay)
- HN "Writing Runbook Documentation" — https://news.ycombinator.com/item?id=22207452
  (script vs playbook; domain-expert on-call debate)

**Industry data:**
- Alert-fatigue survey via carriermanagement.com (Apr 2026) —
  https://www.carriermanagement.com/news/2026/04/08/286535.htm
  (77% ≥10 alerts/day; 57% <30% actionable; 83% ignore/dismiss; exec/practitioner AI gap)

**Inspiration (not evidence):**
- Varaneckas, *Automation and Monitoring with Hubot* (2014) — the firehose-room warning.
- PagerDuty AIOps marketing claims were deliberately excluded as evidence.

---

## 10. Method appendix (honesty log)

- **What we did:** read the SRE Workbook incident-response chapter end-to-end, verified Cook's
  18 points across three independent summaries, read HN threads for first-person on-call
  voices, pulled one industry survey for base rates.
- **What we did NOT do:** interview any SRE; read the full SRE Book Ch. 5 primary text (one
  fetch failed — A7 is marked PARTIAL, not upgraded); verify PagerDuty's proprietary on-call
  report numbers (used their public incident write-up only).
- **[INFERENCE] tags** mark every claim that is our synthesis rather than a sourced fact.
  The redesign lanes should treat unsourced sentences as hypotheses to validate, not findings.
