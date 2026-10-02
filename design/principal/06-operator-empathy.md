# 06 — Operator Empathy: The 3 AM Human, From First Principles

*Principal-redesign study · PRISM (design) · 2026-10-03*
*Status: study/design only. Judged against Law 2 (eternal friction), Law 4
(five whys), Law 5 (Chesterton's fence), Law 7 constitution "Product".*
*Builds on `design/DESIGN_SYSTEM.md` and the screen specs in `design/screens/`.*

---

## 0. Why this study exists

Every other surface in Sentinel is designed from the data model outward:
decisions have confidence, confidence has a distribution, distributions get
charts. That is the wrong direction. Law 4 says peel every requirement to
bedrock. The bedrock here is not the decision — it is the human who must act
on it at 3 AM. This study derives the interface from that human.

---

## 1. Who is the 3 AM human?

Not a persona invented in a workshop. A composite drawn from on-call
reality, with the cognitive facts that constrain every pixel we ship.

### 1.1 The night

- **They were asleep.** The page woke them from slow-wave sleep. Sleep
  inertia — the groggy impairment after abrupt waking — measurably degrades
  decision quality for 30+ minutes. The first ten seconds of the page are the
  most cognitively impaired seconds of the whole incident.
- **They are tired already.** On-call rotations do not respect circadian
  rhythm. After 17–19 hours of wakefulness, cognitive and motor impairment
  equals a blood-alcohol concentration of ~0.05% — legally intoxicated in
  most jurisdictions (Williamson & Feyer, 2000). Reaction speed on vigilance
  tasks drops up to 57%; missed signals increase 40–187%.
- **They do not know they are impaired.** Subjective underestimation of
  impairment under sleep loss is one of the most replicated findings in the
  field (Van Dongen et al., 2003). The operator believes they are fine. The
  interface must compensate for a deficit its user denies having.
- **Working memory is gone.** "We can't keep as many things online at any
  one time when we're sleep deprived" — Drummond (UC San Diego). Attention,
  working memory, and decision-making are the three systems sleep loss hits
  first. The famous 7±2 items of working memory are a rested-lab number; at
  3 AM the practical budget is two or three. Any screen that requires holding
  a filter, a threshold, and a fingerprint in mind simultaneously is a screen
  designed for a person who does not exist.
- **Stress narrows the visual field.** Under high arousal, attention
  collapses onto central cues and discards peripheral ones — the Easterbrook
  hypothesis (Easterbrook, 1959), confirmed across decades of human-factors
  work. Corners, side rails, footnotes, and secondary tabs become literally
  invisible. Whatever the operator needs at 3 AM must be in the center of
  the screen, in the largest text, or it does not exist.
- **They are on a phone, in the dark, one-handed.** AMOLED at minimum
  brightness. The thumb owns the bottom third of the screen. Pinch-zoom is
  not happening. Neither is a 14-column table.

### 1.2 The history they carry into the page

- **They arrive pre-trained to distrust.** Industry benchmarks put
  95–98% of alerts at noncritical or false-positive (OX 2025 Application
  Security Benchmark, via The New Stack). The operator's prior, learned over
  years, is: *the page is probably wrong*. Alert fatigue is not tiredness —
  it is desensitization that actively trains on-call to distrust the
  channel, so the genuine alert arrives to an audience that stopped
  listening. A noisy alert is worse than no alert.
- **Their thinking is template-bound.** Under sustained cognitive load,
  "we become less creative, less exploratory… we exploit familiar templates
  and resort to easier solutions" (Moshe Bar, neuroscientist, Bar-Ilan
  University). At 3 AM the operator does not reason from first principles;
  they pattern-match to the last similar incident. The interface must serve
  the template ("what happened last time") before it serves the analysis.
- **Their trust budget is nearly spent, and it is ours to lose.** The
  strongest predictor of acknowledgment speed is whether the engineer
  believes the page is real: trusted pages get answered in seconds;
  distrusted pages rot in the queue. When teams rebuild alert trust, "the
  first 5 minutes of an incident" come back (mopra/exit1.dev). Sentinel's
  entire product value is denominated in those five minutes.

### 1.3 The constraints, stated as requirements

From the human above, five non-negotiable interface requirements fall out.
These are not UX preferences; they are prosthetics for documented cognitive
deficits.

| # | Cognitive fact | Interface requirement |
|---|---|---|
| R1 | Attention narrows under stress (Easterbrook) | The verdict — the one thing that matters — is centered, largest, first. No scrolling, no tabs, no "see details" to reach it. |
| R2 | Working memory collapses (Drummond) | Every screen answers its question in one self-contained view. Nothing required to be held across screens. Denominators travel with their numbers (this is Law L1's human reason). |
| R3 | Impairment is underestimated (Van Dongen) | The interface must be correct when used badly: big targets, forgiving defaults, no destructive action reachable by a mis-tap, reads before writes (Law L5's human reason). |
| R4 | Prior is distrust (alert fatigue) | Trust is not claimed, it is evidenced — and the evidence is ordered for a skeptic (see §5). The word "accurate" never appears (Law L3's human reason). |
| R5 | Thinking is template-bound (Bar) | The first thing shown is always *what kind of situation this is*, in the operator's own vocabulary (reason codes, fingerprints), not the machine's (embeddings, logits). |

---

## 2. Five whys on every screen (Law 4)

Each surface below is re-derived from the human, not from the API. The
"bedrock answer" is the irreducible truth the screen must serve at 3 AM.

### 2.1 RIVER (`design/screens/decision-river.md`)

- Why does the operator open the river? → To see what the gate decided.
- Why? → To check whether anything needs them right now.
- Why? → Because a machine is making paging decisions on their behalf and
  they are accountable for the outcome.
- Why does *that* matter at 3 AM? → Because "the machine stood down 14
  alerts while I slept" is a sentence they must be able to defend at 10 AM.
- **Bedrock:** the operator needs, in under five seconds, the answer to
  *"is anything on fire, and did the machine do something I need to know
  about?"*

**Derived requirements:**
- The tape's head carries a standing verdict (see Choice 1, §6). The
  operator who reads one sentence and closes the tab leaves correctly.
- Suppressions render dim; pages render lit. This is the dark-cockpit
  principle (§4): the visual field at 3 AM should contain *only the
  abnormal*. A screen full of equally-weighted rows is a screen that has
  already failed R1.
- Gap markers are mandatory, not decorative. A hole in the tape that the
  interface hides is a hole in the operator's mental model — and at 3 AM
  the mental model is all there is.

### 2.2 CAL (`design/screens/calibration.md`)

- Why open calibration? → To answer "when Sentinel says 0.87, should I
  believe 0.87?"
- Why? → Because tonight the operator must decide whether to trust a
  suppression.
- Why does the number matter? → It doesn't. What matters is whether the
  machine's confidence *usually means what it says*.
- **Bedrock:** a tired human needs one sentence — "the machine is honest
  about what it knows" or "it is not, here is where" — before any diagram.

**Derived requirements:**
- The verdict line is the screen; the reliability diagram is the appendix.
  At 3 AM nobody reads a reliability diagram. The diagram exists for the
  10 AM postmortem, and the verdict line tells the operator whether the
  postmortem will be about this screen.
- The denominator is never more than one glance away (R2): `n=4,096 ·
  shadow 7d` rides on the card, not behind a tooltip. A confidence claim
  without a visible denominator is a claim the tired brain will inflate.
- Provisional evidence confesses itself (n<100 styling in DESIGN_SYSTEM
  §3.5). A tired operator cannot be expected to notice thin evidence; the
  interface must make thinness *feel* thin.

### 2.3 SIM (`design/screens/simulator.md`)

- Why open the simulator? → To ask "what would this policy have done last
  week?"
- Why? → Because the operator is afraid of being paged.
- Why is that the emotion? → Every tuning decision is a bet with 3 AM
  consequences, and the operator has lost that bet before.
- **Bedrock:** fear of the page. The simulator's job is to convert fear
  into an informed, defensible bet.

**Derived requirements:**
- Projections are always stated in the operator's native unit: **pages**.
  "At 0.75 you would have been woken 3 times last week; at 0.85, once —
  and the once was the deploy-churn burst." Not ECE deltas, not coverage
  percentages. Pages. The unit of the fear is the unit of the answer.
- The simulator must never touch production data, and must *say so in the
  header* — because the operator's nightmare is that the tuning tool pages
  someone. The fear is irrational; the reassurance must be explicit anyway.
- Export is a diff, not a change (Law L5). At 3 AM there is no "apply";
  there is only "prepare the argument for tomorrow." The interface respects
  the impairment it cannot see.

### 2.4 AUDIT (`design/screens/audit-explorer.md`)

- Why open audit? → To find what the machine decided about one specific
  alert.
- Why? → Because something is being questioned — by the operator, their
  manager, or the postmortem.
- Why does it matter? → A suppression without a receipt is indistinguishable
  from a dropped page. The operator's professional safety depends on being
  able to say "here is why it stood down."
- **Bedrock:** the audit explorer is not a search tool; it is a
  **receipt printer**. Every suppression must produce a receipt a tired
  human can hand to a skeptical manager at 10 AM.

**Derived requirements:**
- The receipt is one deep-linkable view: verdict, reason code,
  counterfactual ("at last month's policy this paged"), confidence vs the
  team's own typical, input hash. In that order (§5). The drawer already
  has these sections; this study fixes their *order* as a trust requirement,
  not a layout preference.
- Flip timelines are first-class: "the machine changed its mind" is the
  single most trust-relevant fact the audit surface can show, and it must
  be shown *by the machine about itself* (onboarding already does this;
  audit must too).

### 2.5 START (`design/screens/onboarding.md`)

- Why does onboarding exist? → To get a skeptic from zero to first
  suppression in 15 minutes.
- Why 15 minutes? → Because that is the length of a coffee, and skepticism
  has a short half-life.
- Why does the skeptic matter? → The 3 AM operator *is* this person, two
  weeks later, woken by a system they met at 2 PM. First impressions set the
  trust prior that the 3 AM self will spend down.
- **Bedrock:** the first impression must be honesty, not capability. The
  operator must leave onboarding believing "this machine shows me its
  failures" — because at 3 AM, that belief is the only thing standing
  between a suppression and a panic.

**Derived requirements:**
- The deliberate wrongness beat (the designed flip, the "suppression counts
  here prove the pipeline works, not that the model is good") is not a nice
  touch; it is the load-bearing trust event of the whole product. It must
  never be A/B-tested away.
- Skip paths everywhere: a senior SRE's time is the scarcest resource in
  the funnel. Respecting it is itself a trust signal.

---

## 3. Graceful degradation — every surface, every failure (Law 2, Law 7)

The product constitution says: *every failure mode degrades to something
useful, never to silence.* "Never a spinner, never silence" is the test.
A spinner is a lie about how long you will wait; silence is the machine
abandoning the operator at the exact moment they reached for it.

The adversary set (Law 2 — name them): **API down**, **SSE disconnected**,
**data stale**, **Jev slow** (our own measurement: ~11s first call vs
70–500ms spec — the platform cannot assume the model is fast), **thin
evaluation data** (n<100).

The paging path itself never depends on any screen — that invariant is
stated on every degraded surface, because at 3 AM the operator's first
fear when a screen fails is "is the gate down too?"

### 3.1 RIVER

| Failure | Degraded state (concrete) |
|---|---|
| SSE disconnected < 30s | Top-bar `◌ reconnecting… (attempt N)`; tape continues to render; no modal, no toast. The operator's reading is never interrupted for a transient blip. |
| SSE dead > 30s | Gap marker row renders at the head: `— 4m 12s gap in the tape (SSE down, retrying) —`. The tape degrades to a **poll-based static tape**: `Refresh` button + auto-poll every 60s with the age of the newest row always visible (`newest: 03:12:04 · 6m ago`). The operator can still read history, filter, and open drawers — the river is a tape that *happens to go live*, not a live view that dies when the socket does. |
| API fully down | Last-known snapshot renders from cache with an unmissable age banner: *"Decisions API unreachable (GET /api/decisions → 503, 2m ago). Showing the tape as of 03:10:22. **The gate is unaffected — paging behavior does not depend on this screen.** [Retry] [Open PagerDuty incidents ↗]"*. The PagerDuty fallback link is the escape hatch: when we cannot show decisions, we hand the operator to the system that can. |
| Data stale (no new decisions, API fine) | Not an error — but indistinguishable from one at 3 AM. The head shows `● live — no decisions in 47m (gate armed, team=payments)`. "Gate armed" is doing the work: it tells the operator the quiet is *the machine working*, not the machine dead. |

### 3.2 CAL

| Failure | Degraded state (concrete) |
|---|---|
| API down | Frozen card with timestamp and conditional tense: *"Calibration as of 26h ago: ECE 0.031 (healthy). Cannot refresh — treat tonight's confidences as uncalibrated until this updates. [Retry]"*. The verdict line switches from declarative to conditional. A stale "healthy" stated declaratively would be a lie the operator acts on. |
| Thin data (n<100) | Per DESIGN_SYSTEM §3.5: flat-hatched histogram, `(n=87 · below 100 — calibration provisional)`. Extended here: the simulator *refuses to project* below n=100 and says why: *"Not enough shadow data to project — 87 cases. Projections on this little data would be numerology, not math. Run a guided storm or wait for the nightly join."* Refusing to compute is a feature when the computation would manufacture false confidence. |
| Jev slow | Irrelevant to CAL (no live Jev calls on read paths — Law L5). The screen states this in its footer: *"No model calls were made to render this screen."* The operator learns, over time, which surfaces can be slow (none of them). |

### 3.3 SIM

| Failure | Degraded state (concrete) |
|---|---|
| `/api/simulate` slow/down | Projection falls back to **cached shadow evaluation + local repricing**: the tradeoff curve recomputes from the last dataset version in the browser, with the provenance block downgraded honestly: *"Projected on cached shadow data (ds:shadow-2026-10-02, 26h old) — recompute when the API returns. [Retry]"*. The math is the same math (tuner parity); only the freshness is provisional. The slider never freezes: a frozen slider at 3 AM reads as "the machine won't let me think." |
| Dataset version mismatch | If the cached data predates the team's current thresholds, the projection renders with a warning band across the curve and the export button is disabled with the reason inline: *"Export disabled — thresholds changed since this data. Recompute first."* A policy diff computed on stale math is worse than no diff. |

### 3.4 AUDIT

| Failure | Degraded state (concrete) |
|---|---|
| API down | Search degrades to the **session's local index**: every decision whose drawer the operator has opened this session is cached with full evidence. The search bar says: *"Searching your recent history only — full archive unreachable (503). 214 decisions cached this session. [Retry]"*. At 3 AM the operator is almost always investigating something they have already looked at; the local index covers the common case, and it says exactly what it covers. |
| Fingerprint with no match (API fine) | Not a failure, but the loneliest moment in the product: *"No decisions match fpr:9f2c·a41d. Either the fingerprint is wrong, or this alert never reached the gate — check receiver health."* The second sentence is the point: it names the next place to look instead of leaving the operator holding an empty result. |

### 3.5 START (onboarding)

| Failure | Degraded state (concrete) |
|---|---|
| API down mid-flow | Onboarding **refuses to fake liveness**. If the storm cannot run against the real pipeline, the flow offers a *recorded* storm — a real recording of a real synthetic run, labeled unmissably: *"Recorded walkthrough (pipeline unreachable). This is a recording of a real storm from 2026-10-01 — the suppressions are real, they just aren't yours. [Retry live]"*. A fake-live storm would poison the trust prior the whole product depends on (§2.5). A labeled recording preserves it. |
| Key verification slow (Jev latency) | The BYOK step shows the raw latency as it happens — *"…11.2s (our measured p50 is ~11s on first call; the vendor spec says 70–500ms — we're measuring, not marketing)"* — turning our own honest flag into the onboarding's first trust event. The operator watches us tell the truth about our dependency's weakness before we've asked them to trust anything. |

### The degradation meta-rule

Every degraded state above follows one composition rule: **lead with what is
still true, name what is missing, give the next action.** "The gate is
unaffected" comes before "the API is down," because at 3 AM the operator
reads the first clause and acts on it. The order of sentences is a safety
property.

---

## 4. Law 2 — what the interface communicates without words

The operator is tired and will not read docs. So the interface must speak
in the channels that survive exhaustion: position, color, density, and
motion — the things the visual system processes before the prefrontal cortex
is consulted.

### 4.1 The dark-cockpit contract

Aviation learned this decades ago. The dark-cockpit philosophy (Boeing;
FAA Human Factors guidance): *during normal operations the panel is
visually quiet — no lights. An abnormality illuminates, immediately
directing the pilot to the affected system* (Lean Enterprise Institute
interview with a 737 pilot; FAA HFCC design guidance). "If no lights are
on, everything is operating normally."

Our translation, already in DESIGN_SYSTEM and hardened here:

- **Quiet is a signal, not an absence.** A dim river of SUPPRESS rows is the
  machine saying "I am working and there is nothing for you." The
  `● live — gate armed` indicator is the master-caution light *in its
  off state* — its presence, unlit, is the reassurance.
- **Light is reserved for the abnormal.** `--sev-1`/`--disp-page` red
  appears only on pages. It never appears on confidence bars, never on
  thresholds, never decoratively. The day red appears somewhere it
  shouldn't, the operator's learned association ("red = I act") degrades —
  and that association is load-bearing at 3 AM.
- **The confidence ramp is deliberately not red/green** (DESIGN_SYSTEM
  §2.2). A confidence value is information about the machine's belief, not
  a verdict. Verdicts are dispositions; colors stay in their lanes. A tired
  brain pattern-matches red/green to bad/good in milliseconds; we refuse to
  let "0.42 confident" read as "bad" when 0.42 below a 0.40 threshold is a
  *correct suppression*.

### 4.2 Affordances for a tunneling mind

- **The whole river row is the button.** At 3 AM there is no precision
  tap; the drawer opens from anywhere on the row. Fitts's law is not a
  guideline here, it is R3.
- **Reason codes are filters, not labels.** Clicking `flap-debounce`
  filters the river to that reason (`?reason=flap-debounce`). The tired
  operator thinks "show me more like this one" — the interface makes the
  label *be* that thought. No separate filter UI to discover.
- **The slider drags across the data.** The threshold slider's track *is*
  the team's confidence histogram (DESIGN_SYSTEM §3.6). The operator does
  not set a number; they drag a line across a visible distribution and
  watch which mass of decisions crosses it. The data is the affordance.
  Numbers are for the 10 AM review; shapes are for 3 AM.
- **One animation per screen, and it means "new".** The 120ms row
  highlight is the only motion in the product. Motion is the scarcest
  attentional currency; we spend it exactly once, on exactly one meaning:
  *this arrived while you were looking.*
- **Staleness is ambient, not textual.** The age of the newest row
  (`newest: 03:12:04 · 6m ago`) sits in the top bar in mono, always. The
  operator should absorb "how fresh is this" the way a pilot absorbs
  airspeed — peripherally, continuously, without reading a sentence.

### 4.3 The reading order is a safety property

For the tunneling mind (Easterbrook), information must arrive in the order
the operator would ask for it, because they will not scroll to find the
second thing:

1. **Verdict** — what happened (SUPPRESS · flap-debounce).
2. **Reason** — the human-arguable rule behind it.
3. **Counterfactual** — what would have happened under a stricter policy.
4. **Confidence vs typical** — was this an easy call or a hard one.
5. **Raw** — the JSON, for the 10 AM self.

This is the inverted pyramid of journalism, built for scanning tired
readers. The drawer already has these sections; this study locks their
order.

---

## 5. What it feels like to trust a machine that silenced a page

### 5.1 The phenomenology

Nobody experiences "trust in automation" as a number. What the operator
experiences, at 3 AM, looking at a suppression they slept through, is a
sequence:

1. **"What did it not wake me for?"** — the first emotion is not
   gratitude, it is *vulnerability*. Lee & See's (2004) definition of trust
   is exact here: "the attitude that an agent will help achieve an
   individual's goals in a situation characterized by **uncertainty and
   vulnerability**." The operator is vulnerable — they delegated their
   sleep — and uncertain. Any interface that opens with "the machine was
   87% confident" has answered a question the operator is not asking.
2. **"Was that the right call?"** — asked not as statistics but as
   *accountability*. They must defend this at 10 AM. What they need is not
   proof the machine is smart; it is a story they can retell: "it stood
   down because the alert flapped three times in four minutes and
   self-cleared — here's the timeline."
3. **"Will it fail in a way I can survive?"** — the deepest question, and
   the one most interfaces never address. The operator does not need the
   machine to be perfect; they need its failures to be *legible and
   bounded*. A machine that is wrong 2% of the time in ways I can see is
   trustworthy; a machine that claims 100% is a machine I must watch
   forever — which defeats the product.

### 5.2 Calibration, not maximization

The literature is unambiguous: the goal is **appropriate reliance**, not
maximal trust (Lee & See, 2004). Overtrust (misuse) and undertrust
(disuse) both degrade outcomes; teams with calibrated trust outperform both
extremes — one study found 47% better performance for appropriately
reliant human-AI teams (Wong et al., 2024, via the trust-calibration
literature). Repeated reliable performance builds trust; clear
communication of limits preserves it (Zhao et al., 2020; Verhagen et al.,
2021).

For Sentinel this means the product must be *as good at producing
skepticism as it is at producing confidence*:

- The calibration screen's flip-rate card ("1.8% of inputs changed the
  machine's mind when re-asked") exists to manufacture *warranted*
  skepticism. An operator who knows the machine flips is an operator who
  will check the flip timeline on a surprising suppression — which is
  exactly the behavior that catches the real misses.
- The onboarding's deliberate wrongness (§2.5) sets the prior: "this
  machine fails, and shows you." That prior is what makes the 3 AM
  suppression survivable — the operator's mental model already contains
  the failure mode.

### 5.3 The evidence order at 3 AM

Trust is built by evidence in a specific order — each step answering the
next question the skeptic asks. This is the drawer's locked section order
(§4.3), justified:

| Order | Evidence | The question it answers | Why it comes here |
|---|---|---|---|
| 1 | Verdict + reason code | "What did it do and why, in words I use?" | A named rule (`flap-debounce`) is *arguable* — the operator can disagree with a rule, not with a logit. Arguability is the on-ramp to trust. |
| 2 | Counterfactual | "Would last month's policy have paged me?" | Connects the machine's decision to the operator's own history. "It stood down what you would have been woken for" is visceral in a way 0.87 never is. |
| 3 | Confidence vs the team's typical (marker vs Q3) | "Was this an easy call or a hard one?" | Not "how confident" — *how confident relative to our own bar*. A 0.62 that sits right of Q3 reads as "confident *for us*", which is the honest statement. |
| 4 | Input hash + timeline | "Can I prove this at 10 AM?" | The receipt. Last, because at 3 AM it is the least urgent — but its presence, visible, is what lets the operator close the tab. |
| 5 | Raw JSON | — | For the 10 AM self. Its existence matters more than its contents at 3 AM. |

The test of this ordering is behavioral: **does the operator close the
tab?** "You can stop looking" is the product's true success metric. Every
screen should be designed toward the close — the moment the tired human
decides the machine has earned the rest of the night.

---

## 6. CREATIVE APPLICATION — three operator-experience choices we make differently

These are not in PagerDuty, Opsgenie, or any AI-ops dashboard we studied.
Each is derived above; each is a Type 2-reversible UI choice with Type 1
seriousness about the human.

### Choice 1 — The Standing Verdict Strip

**Every screen opens with one plain-language sentence that is the whole
screen for a reader who reads nothing else.**

- RIVER: *"Nothing on fire. 14 suppressions in the last hour, all
  reason-coded. Gate armed."* — or — *"1 page in the last hour
  (payments-api SEV2, 03:12). Details below."*
- CAL: the existing verdict line, promoted to the strip.
- SIM: *"At these thresholds you would have been woken 3 times last week."*
- AUDIT: *"fpr:9f2c·a41d — suppressed 6 times in 7d, always flap-debounce.
  Never paged."*
- START: *"This is a recording — the pipeline is unreachable right now."*
  (the degraded honest state, §3.5)

**Why nobody does this:** dashboards open with charts because charts are
what the data model has. Vendors optimize for the demo — the 2 PM buyer
who wants to *see capability*. We optimize for the 3 AM user who needs to
*stop looking*. The strip serves R1 (tunneling attention lands on the
first sentence), R2 (no memory required), and R4 (the sentence is a claim
the rest of the screen must then evidence — it puts the machine's neck on
the line first).

**The brutal version:** if the operator reads only the strip and acts
correctly, the screen succeeded even if every chart below failed to load.
That is the acceptance test, and it is why the strip is composed first in
every screen spec — before the layout, before the API.

### Choice 2 — Degraded-first composition

**We spec every surface from its dead state upward: the "API down" copy is
written before the happy path.**

Concretely: the river is designed as a *static tape that happens to go
live* (cached snapshot + poll fallback are the base, SSE is the
enhancement). Calibration is a *frozen card that happens to refresh*
(the conditional-tense stale state is the base, live refresh the
enhancement). The simulator *refuses to project* on thin data rather than
manufacturing confidence. Onboarding *refuses to fake liveness* rather
than running a theater storm.

**Why nobody does this:** degraded states are designed last, by whoever
has time left — which is why the industry standard is a spinner and a
toast. Spinners are designed for the demo's Wi-Fi. But Law 2 says the
network drops, and the product constitution says a degraded surface must
still be useful. At 3 AM, a spinner is not a loading state; it is the
machine saying "wait" to a person who cannot afford to — and silence is
the machine abandoning them at the exact moment they reached for it.

**The brutal version:** our QA for every screen includes "unplug the API
and complete the operator's task." If the task cannot be completed — read
the tape, check calibration, reprice a policy, pull a receipt — the screen
fails review regardless of how beautiful the happy path is.

### Choice 3 — The Counterfactual Receipt

**Every suppression carries "what would have paged me" — the
at-threshold, at-last-month's-policy counterfactual, on the row, not in
the drawer.**

`▮ SUPPRESS · flap-debounce` becomes, on hover or in the row's second
line: *"at 0.70 this paged; at your 0.85 it stood down — 3 similar stood
down this week."* The audit receipt (§2.4) leads with it. The simulator's
native unit is pages (§2.3).

**Why nobody does this:** vendors show confidence because confidence is
what models output. But a number like 0.87 does not survive 3 AM cognition
— it is abstract, unarguable, and inflated by the tired brain (R2, R3).
What survives is *contrast against the operator's own experience*: "the
old policy would have woken you for this; the new one didn't, and here are
the three it stood down this week." That is a claim the operator can
check, argue with, and — over weeks of correct calls — convert into
calibrated trust. Trust in automation is calibration (Lee & See), and
calibration needs a number you can disagree with. The counterfactual is
that number.

**The brutal version:** a suppression row without a counterfactual is a
naked claim — the same class of bug as a confidence bar without a
denominator (Law L1). We treat it as a rendering defect.

---

## 7. Sources

- Williamson, A. M. & Feyer, A.-M. (2000). Moderate sleep deprivation
  produces impairments in cognitive and motor performance equivalent to
  legally prescribed levels of alcohol intoxication. *Occupational and
  Environmental Medicine*, 57(10), 649–655.
  http://esperex.bg/wp-content/uploads/2014/04/sleep_deprivation.pdf
- Drummond, S. (UC San Diego / VA San Diego) — sleep deprivation impairs
  attention, working memory, decision-making: "We can't keep as many things
  online at any one time." http://www.sciencedaily.com/releases/2006/08/060807121151.htm
- Van Dongen, H. et al. (2003) — subjective underestimation of impairment
  under chronic sleep restriction (cited via the sleep-deprivation
  literature above).
- Lee, J. D. & See, K. A. (2004). Trust in automation: Designing for
  appropriate reliance. *Human Factors*, 46(1), 50–80.
  https://doi.org/10.1518/hfes.46.1.50.30392
- Parasuraman, R. & Riley, V. (1997). Humans and automation: Use, misuse,
  disuse, abuse. (Overtrust/misuse framing, via the trust literature.)
- Easterbrook, J. A. (1959). The effect of emotion on cue utilization and
  the organization of behavior. *Psychological Review*, 66(3). (Attentional
  narrowing under arousal.)
- Boeing / FAA dark-cockpit philosophy: quiet panel in normal ops, lights
  only on abnormality. https://www.lean.org/the-lean-post/articles/the-dark-cockpit-what-aviation-can-teach-lean-leaders/ ;
  FAA HFCC Ch. 7 https://hfcc.dot.gov/publications/docs/GeneralGuidance/zz_FAA_GeneralGuidanceDoc_Chapter_07_Section_00.pdf
- Google SRE Workbook — Ch. 8 On-Call, Ch. 9 Incident Response, Ch. 11
  Managing Load (pager-pain; on-call load as a design input).
- Alert fatigue: desensitization mechanics and the "every page must be
  actionable" litmus test.
  https://github.com/tapankumarbarik/backend-roadmap/blob/HEAD/backend/08-observability-and-operational-readiness/08-alerting-without-fatigue/README.md
- OX 2025 benchmark via The New Stack: 95–98% of alerts noncritical/false
  positives; cognitive-load effects (Moshe Bar, Bar-Ilan).
  https://thenewstack.io/how-ai-can-help-it-teams-find-the-signals-in-alert-noise/
- Alert-trust compounding: acknowledgment speed, the "first 5 minutes,"
  burnout.
  https://github.com/mopra/exit1.dev.website/blob/HEAD/src/content/posts/incident-management/alert-fatigue-starts-with-maintenance-process.md
- Tunnel vision under pressure (Easterbrook applied).
  https://www.psychologytoday.com/us/blog/the-forensic-view/202202/how-we-process-under-pressure-tunnel-vision

---

## 8. Open questions for the coordinator (not decisions)

1. The Standing Verdict Strip needs a copy owner and a generation rule —
   is the strip machine-generated from state (preferred: never stale) or
   template-composed? Recommend machine-generated with a fixed grammar.
2. Degraded-first QA ("unplug the API") should be a named gate in the
   P0 exit bars — needs coordinator sign-off to add.
3. The counterfactual receipt needs the tuner contract to expose
   at-threshold evaluation per decision — flag to the Saturday wave's
   contracts lane if not already covered.

*End of study. No code was written or changed. Next: coordinator review
against the brutal gate.*
