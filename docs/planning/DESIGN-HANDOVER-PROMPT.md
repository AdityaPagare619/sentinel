# THE SENTINEL CONSOLE — DESIGN HANDOVER PROMPT

**For:** the designer (Claude Sonnet 5.5)
**From:** the Sentinel backend teams (via Aditya)
**What this is:** everything you need to design the Sentinel console — and nothing you don't. Requirements, user flows, the plain-English truth about the system, how to think, how to research, how to stay disciplined, and what "done" means.
**What this is NOT:** a design. There is no visual direction in this document — no colors, no themes, no fonts, no layouts. That is your job, and it is deliberately yours: you will derive the entire visual system from your own research. If you find a sentence here telling you what something should look like, it is a mistake — ignore it and tell Aditya.

---

## THE KARAPAHI CONTRACT (read this first)

This document follows one rule: **you will never be asked to understand the backend.**

You do not need to know how the code works, what the database looks like, or what any API returns. Every technical term in this document is translated into plain English on first use. Where the backend matters to your design, it is translated into a *human* fact: a constraint ("the screen can never do X"), a need ("the operator must see Y"), or a story ("at 3 AM, this happens").

What you get instead:
1. **Requirements** — complete, numbered, testable. If you satisfy all of them, the design is correct. If you violate one, it is wrong no matter how good it looks.
2. **Useful details** — the surprising facts, hard constraints, and "why behind the rule" that unlock good decisions and good research.
3. **How to think, how to research, how to stay disciplined** — the method. Not taste. Method.

One more thing: the people handing you this document do not trust their own design taste, and they said so out loud. That is a gift. It means no one will fight you on visual grounds. They will fight you — hard — on whether the design *works*: whether an exhausted engineer understands it in ten seconds, whether it survives ten thousand open problems, whether every number is honest. Win those fights and the visuals are yours.

---

## 1. WHAT THIS SYSTEM IS

Sentinel sits between a company's monitoring tools and its on-call engineers, and it answers one question: **should a human be woken up right now, or not?**

Here is the world it lives in. Modern companies run hundreds of software services. Monitoring tools watch those services and fire off *alerts* — automated messages that say "something might be wrong." A large company can generate thousands of alerts a day. The brutal truth, measured across the industry: **more than half of those alerts require no action.** Engineers get woken at 3 AM for things that fix themselves, for duplicates of things they already know about, for noise.

This has a name: **alert fatigue.** And it kills. Not metaphorically — 83% of on-call engineers admit they now ignore or dismiss alerts, because most alerts have been noise. The pager that cries wolf eventually gets silenced, and then the real emergency gets missed. That is the problem Sentinel exists to solve.

**What Sentinel does:** every alert passes through Sentinel before it can reach a human. Sentinel decides — using an AI judge plus a set of strict deterministic rules — whether the alert is worth waking someone for. If yes, the human gets paged (through PagerDuty, the industry-standard paging service — Sentinel does not page directly; it hands the page to PagerDuty, which calls the engineer's phone). If no, the alert is **suppressed** — logged, explained, and filed where it can be inspected later, but no one is woken.

**The product promise, in one sentence:** fewer 3 AM wake-ups, and every single suppression carries its proof — the evidence for why the machine decided you could sleep.

**Who uses it:**
- **The on-call engineer** (primary user). Woken at 3 AM, groggy, possibly on a phone. Needs to know in under 60 seconds: what broke, how bad, what changed, what to do. This is the person the entire design serves.
- **The engineering manager / SRE lead.** Reviews the week: how many pages, how many suppressions, were any suppressions wrong. Needs the audit trail.
- **The platform operator.** Configures integrations (plugs in the company's PagerDuty key — Sentinel never holds it), watches system health, manages the safety controls.

**Why it exists:** because the current state of the art is either "page on everything" (fatigue, ignored pages, missed real incidents) or "trust the automation blindly" (silent failures, no proof). Sentinel's bet: a machine can make the page-or-suppress decision *if* every decision is explainable, reversible, and auditable — and if the human can always see what the machine is doing and stop it.

---

## 2. THE PIPELINE IN PLAIN LANGUAGE

An alert enters Sentinel. Here is what happens to it, stage by stage. You do not need the internals — you need to know what each stage *means for the human looking at the screen.*

**Stage 1 — Receiving.** Alerts arrive from monitoring tools (Datadog, Prometheus, Grafana, anything that can send a webhook — a webhook is just an automated HTTP message one system sends another). Sentinel checks: is this message authentic (did it really come from our monitoring, or is someone injecting fake alerts?), and is it well-formed? *What comes out:* a validated alert, or a rejection. *Why it exists:* an unauthenticated alert pipeline is an attack surface — anyone who can inject a fake "everything is down" alert can wake the whole company. The operator must be able to see that the front door is locked.

**Stage 2 — Grouping (the correlator).** Five hundred alerts that all say "database slow" are one problem, not five hundred. This stage groups related alerts into *problems* (also called incidents or episodes): it deduplicates repeats, folds flapping alerts (flapping = an alert that fires, clears, fires, clears, like a flickering light) into one, and declares a *storm* when thousands arrive at once. *What comes out:* problems, each containing many alerts. *Why it exists:* the unit of human attention is the problem, never the alert. Every interface in the world that shows humans raw alert lists creates fatigue. This stage is the reason Sentinel can promise "we count problems, not alerts."

**Stage 3 — The race.** For each problem, two things happen at once: an AI judge (called Jev — think of it as a specialist doctor giving a second opinion) reads the alert and returns its judgment with a confidence score; and a countdown timer runs (a few seconds). If the AI answers before the timer ends, its judgment is used. If the timer wins — the AI was too slow, errored, or is down — the system does NOT wait: the problem goes to a human. *What comes out:* either an AI judgment, or a timeout. *Why it exists:* speed with a safety net. The AI makes the system smart; the timer makes it safe. A late AI answer is never allowed to override the decision to page — being slow is treated the same as being wrong.

**Stage 4 — The deterministic gate (the decider).** This is the strict rule-checker that makes the final call: page or suppress. It takes the AI's judgment as *advice*, then applies hard rules: Is the evidence fresh? (Stale information can never justify staying silent.) Is the confidence above the bar? Is the policy — the company's suppression rules — current and properly signed off? If anything is uncertain, stale, broken, or expired, the gate **pages the human.** Suppressing requires everything to check out; paging requires only doubt. *What comes out:* a decision — PAGE or SUPPRESS — with a reason code and the evidence attached. *Why it exists:* this is the product. The gate is deliberately biased: it would rather wake you unnecessarily than stay silent wrongly. Every safety property in this document flows from that bias.

**Stage 5 — Forwarding.** Paged problems go to PagerDuty (which calls the human). Suppressed problems go to the proof ledger (explained below). Every delivery is recorded, retried if it fails, and never lost. *Why it exists:* a page that doesn't arrive is the same as no page. This stage is the system's promise that its decisions actually reach the world.

**Two more concepts you must understand:**

**The proof ledger.** Every suppression — every time the machine decides *not* to wake someone — is recorded with its full reasoning: what rule matched, what the evidence was, how fresh it was, when it expires. This is the product's soul. An engineer who was not woken must be able to ask "why didn't you page me?" and get a complete, inspectable answer. No monitoring tool on earth does this well today. It is Sentinel's core invention and your core design surface.

**The safety machinery.** Four controls the operator must always be able to see and reach:
- **The kill switch.** One control that instantly turns off ALL suppression — every alert pages a human until it is turned back off. The emergency brake. It must provably work in under 5 seconds, and the screen must show that proof.
- **Policy governance.** The suppression rules (the policy) can only be changed through a formal sign-off: two humans approve, the change expires automatically, and the system refuses to run on unsigned rules. The screen shows which policy is live, whether it is properly signed, and when it expires.
- **Auth status.** Which alert sources are authenticated, which keys are healthy, whether any door is unlocked.
- **Degraded mode.** When parts of the system are sick (the AI is down, data is stale), the system degrades *toward* the human — it pages more, never less. The screen must show the degraded state honestly, never rendering stale data as if it were live.

## 3. THE SCALE PROBLEM (the core design challenge — read this twice)

This is the problem your entire design must solve. Everything else is detail.

Sentinel's customers generate **thousands to millions of signals.** A signal is one alert from one monitor: "CPU high on server 47," "checkout latency p99 above threshold," "certificate expires in 6 hours." During normal operation: thousands a day. During a bad deploy or an infrastructure incident: thousands *per minute.*

**Here is why this breaks every obvious design:**

The obvious design is a list. A scrolling feed of cards, one per alert, newest first. Every monitoring tool starts here, and every one of them fails here. Why:

1. **Human attention does not scale.** An engineer can meaningfully consider maybe five to ten items. A list of ten thousand items is not ten thousand times more useful than a list of ten — it is *less* useful, because the engineer cannot find the five that matter. The list's length becomes the problem.
2. **Scrolling is linear; emergencies are not.** In a scrolling list, the item at position 9,999 might be the one that matters. The engineer scrolls past 9,998 things to find it, or — more likely — never scrolls at all and misses it. Position in a list is an accident of timing, not a measure of importance.
3. **Every card shouts equally.** A list gives every item the same visual weight by default. But a SEV1 ("users cannot check out") and a SEV4 ("certificate expires in 6 hours") do not deserve equal weight. When everything shouts, the engineer learns to hear nothing — this is the mechanical cause of alert fatigue.
4. **The list is always behind.** During a storm, new items arrive faster than any human can read. The list grows while you scroll. You are always reading the past, never the present. A UI that is always behind reality trains the operator not to trust it.
5. **The machine already did the reading.** This is the key: Sentinel's pipeline *already* grouped those 500 alerts into one problem, *already* decided 480 of them didn't need a human, *already* ranked what remains. A UI that shows the human the raw 500 is undoing the machine's work and handing the fatigue back.

**So the central design question is not "how do I display a million things." It is: "how does a human stay in command of a system processing a million things, without ever looking at a million things?"**

The human's job is not to *see* the signals. The human's job is to *decide* — and to *trust* the machine's decisions, and to *intervene* when the machine is wrong. Your design must serve those three verbs: decide, trust, intervene. Everything the human sees should be in service of one of them.

Some directions the problem points toward (these are *problems to solve*, not solutions to implement — you will find your own answers in research):
- **Aggregation as the primary object.** If the machine groups 500 alerts into 1 problem, the human's world contains 1 thing, not 500. But the 500 must remain *reachable* — "show me everything inside this problem" must always be possible, or the grouping is a black box.
- **The triage queue, not the feed.** A feed is ordered by time. A triage queue is ordered by *need*: what needs a human most urgently, first. What determines "need"? That is a design decision with real consequences — severity, confidence, cost of waiting, age. Choose deliberately; every ordering is an argument about what matters.
- **Progressive disclosure.** The human sees the smallest true summary first, and every summary can be opened into the next level of detail, all the way down to the raw signal. Nothing is hidden (hidden = untrustworthy), but nothing is forced (forced = fatigue).
- **The storm as a first-class state.** Thousands firing at once is not an edge case — it is the moment the product matters most. The UI must have a *designed* storm state: what the human sees, what the machine is doing about it, and what the human can do. A storm must never look like "a very long list."
- **Counts that mean something.** "10,241 alerts" is a number that helps no one. "3 problems need you; 214 handled quietly; 1 storm folding" is the same reality, expressed in units of human attention. Every number on screen should answer "so what do I do?"

**Useful details (facts that should shape your thinking):**
- Measured across the industry: 77% of on-call engineers get 10+ alerts per day; 57% say fewer than 30% are actionable; 83% admit to ignoring or dismissing alerts. Your user has been trained by bad tools to distrust tools. You are designing for a skeptic.
- One vendor's grouping log *stops recording* after 100 duplicates of the same alert — the evidence trail dies exactly when the storm is longest. Your design must never have a point where the record quietly stops.
- Another vendor deletes ~95% of alerts as "noise" with no record of what disappeared. The first time your system suppresses something wrongly and can't show what it did, trust is gone permanently. *Recoverability of every automated decision is non-negotiable.*
- Google's SRE doctrine, the industry's bible: **every page should be actionable.** If waking a human doesn't require a human decision, it shouldn't be a page. Your design should make unactionable pages *visible as failures* — the system should be embarrassed by them, not proud of its volume.

---

## 4. JEV SORTS, SO THE UI SORTS

Here is the key product insight, and it should shape your entire information architecture:

**The pipeline is already sorting.** Every stage of Sentinel's pipeline sorts, filters, ranks, or decides along specific dimensions. The AI judge scores every problem. The gate applies thresholds. The correlator groups by similarity. The dimensions the *machine* uses are exactly the dimensions the *human* needs to explore. Your interface must let the human sort, filter, and drill along those same dimensions — because the human's questions are the machine's dimensions, asked from the other side.

**The dimensions (what they are, in plain English, and why a human cares):**

- **Severity — how bad is this for users?** Four levels, SEV1 (users are hurt right now) to SEV4 (informational). *Why it matters:* this is the primary triage axis. A human's first question is always "how bad," and the answer determines everything else. But severity is *declared* (by the alert source or the policy), not *proven* — a good design lets the human question it.
- **Confidence — how sure is the machine?** A number from 0 to 1 on every AI judgment. 0.96 means "very sure"; 0.58 means "barely." *Why it matters:* confidence is the machine's honesty about its own uncertainty. A suppression at 0.96 confidence is a different object than a suppression at 0.58 — the human needs to feel that difference. Low confidence should *look* uncertain, whatever "look uncertain" means in your visual language.
- **The decision (disposition) — what did the machine decide, and why?** Every problem ends in one of a small set of outcomes: paged as genuine, suppressed as duplicate, suppressed as flapping noise, suppressed as known noise, paged because the evidence was stale (fail-open), paged because the timer beat the AI. Each outcome has a *reason code* — a short machine-readable label like "duplicate" or "flap." *Why it matters:* this is the vocabulary of the proof ledger. The human must be able to ask "show me everything suppressed as duplicates in the last hour" and get an answer. If your design can't filter by decision, it can't build trust.
- **Team / service / owner — whose problem is this?** Every alert belongs to a service, every service has an owner (a team or a person). *Why it matters:* at 3 AM, "is this mine?" is the second question after "how bad." Ownership is also how organizations divide attention — a team lead needs "everything for my team," not "everything."
- **Time — when did it start, how long has it been open, is it getting worse?** Age, trend (improving/stable/worsening), and the timeline of what happened. *Why it matters:* a problem open for 4 hours is different from one that started 40 seconds ago. A worsening trend is different from a stable one. Time is how the human judges urgency.
- **Alert history — has this shape fired before, and was it noise?** "This exact pattern fired 14 times this month; 13 were noise." *Why it matters:* this is the machine's track record, per problem-shape. It is the single most trust-building fact you can show: not "we suppress 98% of noise" (an aggregate claim no one believes), but "for alerts *like this one*, here's what happened last time." Trust is per-class, not aggregate.
- **Freshness — how recent is the evidence behind this decision?** *Why it matters:* a suppression based on 30-second-old evidence is different from one based on 30-minute-old evidence. Stale evidence can never justify silence — the system knows this, and the human must be able to see it.

**What "the UI sorts" means in practice:** the human must be able to ask the system's own questions back at it. "Show me low-confidence suppressions from the last hour." "Show me everything for the payments team, sorted by severity." "Show me what changed in the last day." These are not advanced features — they are the *basic* interaction model. If the machine can decide along a dimension, the human can interrogate along that dimension. A design where the human can only *view* what the machine *decided* is a dashboard; a design where the human can *question* it is an instrument.

---

## 5. WHY ANYONE WOULD USE THIS (the value story — feel this before you design)

**The pain.** It is 3:12 AM. Your phone screams. You fumble for it, heart hammering — the physiological stress response is real and immediate, and it does not care that this is the fourth time this week. The screen says: "CRITICAL: api latency p99." That's it. That's all you get.

Now the context hunt begins. Laptop open, VPN, dashboards: is it still happening? Which service? Are users actually affected or is this a monitoring blip? You check the deploy log — did someone ship something at midnight? You check the error tracker, the status page, Slack. Twenty minutes later you know what the page should have told you in twenty seconds: a deploy at 2:40 AM doubled checkout latency, users are affected, roll it back. You roll it back. You lie awake until 4:30 wondering if it'll hold. You have standup at 9:30.

Multiply by every on-call engineer, every week. The industry numbers: an engineer loses roughly 6 hours of life per incident (the page, the context hunt, the adrenaline, the lost sleep). Most pages didn't need a human at all — a flapping alert, a duplicate, a self-healing blip. But the pager can't tell the difference, so it wakes you for all of them, and you learn — rationally, inevitably — to distrust it. Then one night the page is real and you sleep through it, because the last forty weren't.

**The promise.** Now imagine the same night with Sentinel. At 2:40 AM the deploy doubles checkout latency. Alerts fire — dozens of them. Sentinel groups them into one problem in seconds. The AI judge reads it: genuine, confidence 0.91, users affected. The gate checks: evidence fresh, policy signed, confidence above the bar. Decision: PAGE. Your phone rings at 2:41 — one page, not forty. And it doesn't just say "CRITICAL: api latency." It says: checkout latency doubled at 2:40, right after deploy #4821; 12% of checkouts failing; blast radius is the checkout service; the last three times this pattern fired, it was the deploy. You roll back by 2:50. Back asleep by 3:10.

And the forty other alerts that night — the flapping disk warnings, the duplicate CPU spikes, the self-healing blips? Suppressed. Each one with its proof: *why* it was suppressed, *what rule* matched, *how fresh* the evidence was, *when* the suppression expires, and a one-tap way to undo it if you disagree. In the morning you can review every single one. Nothing vanished. Nothing hid.

**What "faster, crystal clear" means.** The product makes two things faster: *the machine's* decision (seconds, not the 20-minute human context hunt) and *the human's* decision (every fact needed, in order, no tab-hunting). And it makes one thing crystal clear: *why.* Why did I get woken? Why didn't I? What did the machine see? What would change its mind? A design that delivers "faster" without "crystal clear" is just a faster black box — and black boxes get disabled after their first visible mistake.

**The emotional truth your design must carry:** the user is someone who has been woken, unnecessarily, hundreds of times, by tools that didn't respect their sleep. They are skeptical, tired, and technically expert. They will not be impressed by novelty. They will be impressed by *correctness*: the right thing surfaced, the proof attached, the control where they expect it, nothing hidden, nothing shouting. Design for the skeptic. Earn the trust one suppression at a time.

## 6. DETAILED USER FLOWS

These are human stories, not screen specs. Each flow: **who** the human is, **what triggers** it, **what they need to know** (in the order they need it), **what they do**, **what success looks like**, **what failure looks like**. Design so that the success column happens and the failure column is structurally impossible — not just unlikely.

---

### Flow 1 — The 3 AM page (the first 60 seconds)

**Who:** Maya, backend on-call for the payments team. Asleep. Phone on loud because it has to be.

**Trigger:** Her phone rings at 3:12 AM. PagerDuty is calling — not a push notification she can sleep through, a call. Something needs her.

**What she needs to know, in order:**
1. *What broke, how bad, whose is it?* — "Checkout latency doubled" / SEV1 / payments team. If it's not hers, she needs to know in five seconds so she can route it, not investigate it.
2. *Is this real and actionable, or noise?* — Has this pattern fired before? Was it noise last time? The single most important trust question, answered from the page itself.
3. *Are users actually hurt?* — Infra failing is not the same as users hurting. "12% of checkouts failing" vs "one internal dashboard slow" are different universes.
4. *What changed recently?* — The expert's first hypothesis is always recency: deploys, config pushes, flag flips in the last hours. This must be *attached*, not hunted.
5. *What should I do first?* — Not root cause. Mitigation: roll back, scale up, fail over. The recommended first action, with the evidence for why.

**What she does:** She acknowledges the page *from the phone* — one action, no laptop, no login. Acknowledging means "I own this" and stops the escalation (the system paging her manager next). Then she opens the laptop, follows the deep link from the page, sees the full picture in the order above, and mitigates: rolls back the 2:40 AM deploy. 3:04 AM: latency recovering. She watches for ten minutes, confirms stability, goes back to sleep.

**What success looks like:** From ring to "I know what to do" in under 60 seconds. Every fact above present, in order, no tab-hunting. She never wonders "is this real?" because the page carried its proof.

**What failure looks like:** The page says "CRITICAL: api latency" and nothing else. Twenty minutes of dashboard-hunting. She acks from the console because the phone can't — but the ack doesn't stop the escalation, so her manager gets woken too. Or: she decides it's probably noise (the last nine were) and goes back to sleep — and this one was real. *Your design must make the failure column impossible: the page carries proof, ack works from the page channel, and "probably noise" is answered by the track record, not by her gut at 3 AM.*

---

### Flow 2 — Morning triage (the night's decisions, reviewed)

**Who:** Daniel, the SRE lead. 9:00 AM, coffee, reviewing the night before his team's standup.

**Trigger:** Start of the workday. He wasn't paged — which means either the night was quiet, or the machine handled things quietly. He needs to know which.

**What he needs to know, in order:**
1. *Was I supposed to be woken?* — The complete list of what the machine decided overnight: what paged (and to whom), what was suppressed.
2. *For each suppression: was it right?* — The proof for each: rule matched, evidence, freshness, confidence. He's spot-checking the machine's judgment, the way you'd review a junior engineer's calls.
3. *Anything need follow-up?* — Suppressions that were borderline (low confidence, unusual pattern) deserve a human look, even if the decision was probably right.
4. *How's the machine's track record?* — Per problem-shape: "alerts like this fired 40 times this month; 39 suppressions were correct, 1 was wrong." Trends, not aggregates.

**What he does:** He scans the night's suppressions — most are obviously right (duplicates, flaps, self-healing blips with high confidence). Two are borderline; he opens their proofs, agrees with one, and for the other he adjusts nothing but notes the pattern for the team to watch. He checks the track record for the noisiest alert shape and sees it's been 100% correct for three weeks. Standup: "quiet night, machine handled 214, two worth a look, both fine."

**What success looks like:** Reviewing a full night of automated decisions takes ten minutes and *increases* his trust. The suppressions are browsable, each with proof. He can answer "was the machine right?" with evidence, not faith.

**What failure looks like:** The suppressions are invisible — "214 suppressed" as a number with no way to inspect them. Or each suppression requires five clicks to understand. He stops reviewing (too expensive), trust decays silently, and the first wrong suppression becomes a scandal instead of a correction. *Your design must make the night's decisions as reviewable as a code diff: every decision, its reasoning, its outcome, skimmable in minutes.*

---

### Flow 3 — The storm (thousands firing at once)

**Who:** Priya, on-call for platform. 2:15 PM on a Tuesday. A bad config push just went to the entire fleet.

**Trigger:** Her phone starts buzzing — then keeps buzzing. Not one page: the start of many. Something big is happening.

**What she needs to know, in order:**
1. *Is this one problem or fifty?* — The single most important question in a storm. If it's one bad config push, there is one fix. If it's fifty independent failures, it's a very different day.
2. *What's the blast radius, right now, and is it growing?* — How many users, which services, trend direction. Updated live — a storm is a moving object.
3. *What is the machine doing about the flood?* — Is it grouping? What's the grouping rule? How many signals have folded into how many problems? She must never wonder whether the machine is keeping up.
4. *What needs me, specifically?* — Out of the chaos: the one or two decisions only a human can make. Everything else should be handled or queued.
5. *What changed?* — In a storm, "what changed" is usually the answer. The config push at 2:14 PM, right there next to the problem.

**What she does:** She sees: one problem ("config push fleet-wide"), 4,200 signals folded into it, blast radius growing, the machine has paged once (to her) and suppressed the 4,199 duplicates with proof. She acknowledges, rolls back the config push, watches the blast radius shrink. The storm folds itself back down. Total pages to humans: one.

**What success looks like:** One problem, one page, one fix. The storm is a *designed state* — the interface shows the flood, the grouping, the rule, the trend — not "a very long list." She is in command throughout, never scrolling.

**What failure looks like:** Her phone buzzes 400 times and becomes unusable. The interface shows an endless scrolling list of alerts, each one demanding attention, newest first, the important one buried at position 9,999. She silences her phone to make it stop — and misses the one page that mattered. Or: the machine groups everything silently and she can't tell whether it's keeping up, so she doesn't trust the single page and investigates all 4,200 anyway. *Your design must make the storm legible: one problem, visible grouping, live blast radius, and the human's job reduced to the one decision only they can make.*

---

### Flow 4 — "Why was this suppressed?" (the trust-building flow)

**Who:** Jonas, a backend engineer. He was NOT woken last night. Over coffee, he idly wonders: "the deploys were flaky yesterday — did the machine suppress something it shouldn't have?"

**Trigger:** Curiosity, or suspicion, or a teammate asking "hey, did you see anything about the queue workers last night?" This flow also fires during formal reviews: the weekly "was the machine right?" check.

**What he needs to know, in order:**
1. *What was suppressed, when?* — The complete, browsable record. Not a count — the items.
2. *For this one: why?* — The full reasoning, in plain language: what rule matched, what the evidence was, how fresh the evidence was, what the confidence was, when the suppression expires.
3. *What's the track record for this kind of alert?* — "This pattern fired 14 times this month; 13 suppressions were correct." The per-class history that makes this decision trustworthy (or not).
4. *What did the machine consider and reject?* — Did it consider paging and decide against it? What was the near-miss? A system that only logs what it *did* can't be audited for what it *chose not to do.*

**What he does:** He finds the queue-worker suppression from 2:30 AM. The proof: duplicate of an already-paged problem, confidence 0.97, evidence 40 seconds old, expires in 15 minutes. Track record: 14/14 correct this month. He nods and moves on. *Or:* he finds one at 0.58 confidence with a vague reason — he hits the one-tap override ("page me about this"), the page goes out, it's logged. The machine was wrong; the human caught it; the system recorded both.

**What success looks like:** Every suppression is inspectable in seconds, with proof, track record, and a visible undo. Trust is built one verified decision at a time. A wrong suppression is *findable* — which is what makes the system safe to trust.

**What failure looks like:** "214 suppressed" with no way to see them. Or each suppression shows "suppressed by rule X" with no evidence, no freshness, no expiry, no undo. Jonas can't verify, so he concludes the machine is a black box — and the next time he's on-call, he disables it. *The first silent mistake permanently destroys trust. Your design must make silence impossible: every suppression carries its proof, its expiry, and its undo, always.*

---

### Flow 5 — The safety check (can I trust the guardrails?)

**Who:** Aisha, the new platform engineer. Week two. She's been told "Sentinel decides what pages us," and her honest reaction is: "and what if it's wrong?"

**Trigger:** Onboarding curiosity, or a real incident where someone asks "is the kill switch actually live?", or the quarterly safety review.

**What she needs to know, in order:**
1. *The kill switch: is it live, and would it work?* — Not "does the button exist" but "is the wire real": when was it last tested, how fast does it act, who can flip it, what exactly happens when it's flipped (everything pages; in-flight decisions resolve as pages; nothing is suppressed).
2. *The policy: what rules are live, are they properly signed off, when do they expire?* — The suppression rules are the highest-stakes configuration in the system. She needs to see: which version is live, who approved it, when it expires, and — critically — that the system *refuses* to run on unsigned rules.
3. *Auth: are the doors locked?* — Which alert sources are authenticated, which keys are healthy, is anything accepting unauthenticated input.
4. *Degraded state: what's sick right now?* — Is the AI judge healthy? Is the data fresh? If something's degraded, the system must say so plainly — never rendering stale data as if it were live.

**What she does:** She checks each control. Kill switch: last drill passed, acts in under 5 seconds, she can see *which code* reads it (not a marketing claim — the actual wiring). Policy: version 14, signed by two humans, expires in 6 days. Auth: all sources authenticated. Health: all green. She still doesn't *trust* it — trust takes months — but she can *verify* it, which is the precondition for trust.

**What success looks like:** Every safety control is visible, inspectable, and first-class — never buried in settings. Every claim carries its proof ("last tested," "measured," not "trust us"). A new engineer can verify the guardrails in ten minutes.

**What failure looks like:** The kill switch is a button with no proof behind it — and it turns out nothing reads it (this actually happened in our history: the "under 5 seconds" claim was fiction because no code read the flag). The policy page shows "configured" while the production path can't find the key. The health indicator is green because nobody checks. *Your design must make safety theater structurally impossible: every safety claim displays its evidence, its last verification, and its failure mode. A control without proof is a lie the interface must not tell.*

---

### Flow 6 — The appeal ("page me anyway")

**Who:** Ravi, a senior engineer. He's looking at a suppressed item and it bothers him. The machine says "duplicate, confidence 0.96." His gut says the duplicate might be a *different* failure with the same shape.

**Trigger:** Disagreement with a machine decision. This is the most important interaction in the product: the moment human judgment overrides machine judgment.

**What he needs to know, in order:**
1. *What exactly am I overriding?* — The suppression's proof, so his override is informed, not reflexive.
2. *What will happen when I appeal?* — A page goes out through the normal paging path, right now. Not "a ticket." Not "a note." A page.
3. *Will anyone know I did this?* — Yes: it's audit-logged, attributed to him, with his reason. Overrides are first-class events, not shameful exceptions. The system *wants* to know when it's wrong — that's how the track record improves.

**What he does:** One action: appeal. The page goes out. It's logged: who, when, why, what the machine had decided. Later, the review shows: he was right (it *was* a different failure) or the machine was right (it was a duplicate). Either way, the record improves.

**What success looks like:** Overriding the machine is *easier* than doubting it — one action, immediate, transparent. The audit trail records the disagreement and its outcome. The machine's track record gets better because humans correct it.

**What failure looks like:** The appeal button exists but does nothing (this actually happened in our history: "page me anyway" rendered in the production build and could never page — an operator at 3 AM could believe an appeal acted when nothing happened). Or the appeal requires five steps, so Ravi doesn't bother and just lives with the doubt. Or overrides aren't logged, so the machine never learns. *Your design must make the appeal real, immediate, and recorded. A button that promises action and delivers nothing is the most dangerous object in this interface.*

## 7. REQUIREMENTS

Numbered, testable, written as capabilities — what the operator *can do* and what the system *must guarantee*. Nothing here prescribes layout, position, or navigation; those are yours. Each requirement has a **why** (the human reason) and a **check** (how you'd verify it — by exercise, inspection, or test). If you satisfy all of them, the design is correct. If you violate one, it is wrong no matter how good it looks.

### A. Arrival and attention

**REQ-1 — When the operator arrives, the first thing presented is problems needing human action — never the raw alert stream, never the full decision log.**
*Why:* attention is the scarcest resource. Leading with volume trains fatigue; leading with need trains trust.
*Check:* a fresh arrival reaches an actionable problem in under 10 seconds; no raw-alert rows on the arrival surface.

**REQ-2 — The unit of the interface is the problem (one real-world thing going wrong), never the alert. Counts, queues, and lists count problems.**
*Why:* 500 alerts are 1 problem. Showing 500 is the machine un-doing its own grouping work and handing fatigue back.
*Check:* spot-audit every count in the interface; any number that counts alerts where problems are meant is a defect.

**REQ-3 — "Acknowledge" and "take ownership" are distinct actions. Ack means "I own this, stop escalating." Ownership transfers from the wrong person without friction, recorded for both parties.**
*Why:* at 3 AM the wrong person often acks first; a claim by the wrong person must not block the right one.
*Check:* transfer ownership from an acked-but-wrong owner; both parties recorded; escalation behaves correctly.

**REQ-4 — The page notification itself is actionable: acknowledge, escalate, and take ownership work from the page channel (phone/push) without opening the console.**
*Why:* the 30-second response starts before any screen loads. A page that requires a login to ack has failed.
*Check:* ack from the notification alone; the escalation stops; the console reflects it.

### B. What a page contains

**REQ-5 — Every page presents, in this order: what broke + how severe + whose it is → the primary action → the evidence.**
*Why:* every serious tool converges on action-before-evidence. Inverting the order slows the 30-second scan when seconds matter.
*Check:* timed exercise — "what paged, how severe, who owns it" answered in under 30 seconds; the action precedes the evidence.

**REQ-6 — Every page carries an explicit cost-of-inaction statement: what happens if nobody acts, in concrete terms.**
*Why:* this is the question the 3 AM brain is actually asking ("can this wait till morning?"), and no competitor answers it. Uncontested design space.
*Check:* every rendered page includes it; a page without one is a defect.

**REQ-7 — Every page arrives with context pre-attached: service ownership, blast radius, what changed recently. Never as links to go find.**
*Why:* "open six tabs" is the failure mode the industry evolved away from. The expert's first hypothesis is always recency — the interface must do that work.
*Check:* "why did it page" answered from the page alone, zero navigation, under 30 seconds.

**REQ-8 — The machine's call is on the page: the decision (page or suppress), the reason in plain language, and the confidence.**
*Why:* the operator must see what the machine *thought*, not just what it *did*. A decision without its reasoning is a black box.
*Check:* every page shows decision + plain-language reason + confidence.

### C. Suppression trust (the surface no vendor has built — your core invention space)

**REQ-9 — Every suppressed item carries its proof: the exact rule that matched, who configured it, the matched condition with values, how fresh the evidence was, and when the suppression expires.**
*Why:* this is the product's trust surface. A suppression without proof asks for faith; faith runs out at the first mistake.
*Check:* audit — every suppressed item expands to its full proof; one without is a defect.

**REQ-10 — Suppressed items are first-class: browsable, filterable, recoverable. The removed noise is inspectable, never invisible.**
*Why:* the first wrong suppression destroys trust permanently if it can't be found and examined.
*Check:* list, filter, and open any suppressed item from the last N hours; the visible count matches the decided count.

**REQ-11 — One unified place answers "everything currently suppressed and why" — never scattered per-rule drill-downs.**
*Why:* suppression posture scattered across rules is invisible posture. The operator must see the whole silence at once.
*Check:* "what was suppressed in the last hour and why" answered from one place in under 30 seconds.

**REQ-12 — Every suppression is time-bounded with a visible expiry and automatic re-evaluation. Open-ended silences are forbidden.**
*Why:* temporary suppression is legible and self-healing; a silence that never ends is how real pages die quietly.
*Check:* no suppression record exists without an expiry; expiry triggers re-evaluation and a record of it.

**REQ-13 — Suppression covers all outbound channels. A suppression that stops the page but not the chat flood is broken.**
*Why:* fatigue leaks through the side channel. The operator suppressed *the interruption*, not one of its routes.
*Check:* suppress a decision; assert zero notifications fire on every configured channel.

**REQ-14 — Nothing is ever silently un-created. Every suppression is a recorded event, and the record never quietly stops.**
*Why:* for a proof engine the audit trail IS the product. One vendor's log stops recording after 100 duplicates — the evidence dies exactly when the storm is longest. Yours must not.
*Check:* chaos test — a 200-occurrence storm retains all 200 records; no silent cutoff anywhere.

**REQ-15 — Undo any machine action in one step; the undo is always visible next to the action.**
*Why:* the undo must be cheaper than the doubt. If un-suppressing takes longer than just getting paged, the engineer disables the machine instead.
*Check:* every automated action has a one-step reversal, visible alongside it; timed.

**REQ-16 — Grouping and silencing are separate, explicitly-linked concepts. "Grouped" is never presented as "suppressed."**
*Why:* operators who believe the consolidated view ended the noise get paged anyway — and learn the interface lies.
*Check:* every grouped item shows its notification state independently of its group state.

### D. Scale (the core challenge, as requirements)

**REQ-17 — The interface never presents an unbounded scrolling list as the primary view. There is always an aggregated, triaged, or summarized form first, with drill-down available to the raw items.**
*Why:* unbounded lists fail at thousands of items — attention doesn't scale, position is an accident of timing, and the list is always behind during a storm.
*Check:* load 10,000 synthetic problems; the primary view stays usable and the most urgent is findable in under 10 seconds.

**REQ-18 — The storm is a designed state, not a long list. During mass events the interface shows: that a storm is happening, its size and trend, the grouping rule in effect, and what needs the human.**
*Why:* the storm is when the product matters most. "A very long list" is not a storm design — it's surrender.
*Check:* synthetic storm — the storm state is unmistakable; the human's job reduces to the decisions only they can make.

**REQ-19 — Every number counts problems, never alerts — and every number can answer "why this number?"**
*Why:* naked counts are fatigue instrumentation; traceable counts are trust instrumentation.
*Check:* spot-audit every number in the interface; each traces to its source.

### E. Rules and automation transparency

**REQ-20 — Every automated decision renders its provenance: which rule matched, in what evaluation order, and a path to the rule itself.**
*Why:* invisible automation reasoning is the trust killer. "The computer said so" is not a reason.
*Check:* every automated decision links to its rule and shows match order; one without provenance is a defect.

**REQ-21 — Rule changes are previewed against real recent data before they take effect. Nothing touches production unpreviewed.**
*Why:* the industry converged on preview-before-commit as the safety pattern for automation config — it catches the misconfiguration before the 3 AM page it would cause.
*Check:* the commit action stays disabled until a preview against real recent data has run.

**REQ-22 — The operator's role in automation is veto and edit — never writing grouping or filter rules from scratch. The machine proposes; the human approves, rejects, or edits.**
*Why:* tired humans maintaining filters is how noise returns at scale. The machine does the authoring; the human does the judgment.
*Check:* no workflow requires authoring a rule to achieve quiet; every machine-proposed grouping carries approve/reject/edit.

**REQ-23 — The record includes "considered and rejected": what the machine evaluated but didn't do.**
*Why:* a wrong suppression is unreviewable if only actions are logged. The near-miss is where the next bug hides.
*Check:* a suppression rule that evaluates but doesn't fire produces a record; "show me everything considered for this problem" works.

### F. Timeline and audit

**REQ-24 — One unified timeline per problem: human actions, machine decisions, suppressions, near-misses — earliest-first, every entry attributed (human, machine, which rule).**
*Why:* the timeline is where trust lives, and the earliest events are closest to root cause. No tool puts the machine's decisions in it — yours must.
*Check:* every entry carries an attribution tag; automated entries exist for every automated decision affecting the problem.

**REQ-25 — The record shows what the machine knew THEN, not what we know now.**
*Why:* post-accident review is corrupted by hindsight bias ("it should have been obvious"). The record must freeze the machine's knowledge at decision time.
*Check:* historical decisions render with their original evidence and confidence, not current data.

**REQ-26 — The full timeline exports in one action for postmortems, including machine decisions.**
*Why:* rebuilding timelines from chat scrollback is where postmortems die. The timeline is free; the judgment is human.
*Check:* export matches the on-screen timeline completely, machine entries included.

**REQ-27 — A human's terminal action (resolve, close) is never silently overridden by the machine. Automated re-opens arrive as new, attributed events.**
*Why:* silent overrides train engineers to distrust the automation — existential for a suppression engine.
*Check:* human resolves, machine re-triggers → a new attributed event, never a silent status flip.

### G. Safety machinery (must exist and be first-class — where and how is your decision)

**REQ-28 — The kill switch exists, is reachable within seconds from anywhere, and displays its proof: last tested, how fast it acts, who can flip it, and exactly what happens when flipped. A control without proof is not a control.**
*Why:* the emergency brake must work. Our history includes a kill switch whose "under 5 seconds" claim was fiction because no code read it. The interface must make that class of lie structurally impossible.
*Check:* the proof (last drill, measured time) sits next to the control; flipping it stops all suppression; the claim is measured, not asserted.

**REQ-29 — Policy governance is visible: which rule-set version is live, whether it is properly signed off by the required humans, when it expires. The system refuses to run on unsigned rules, and that refusal renders honestly — never as a silent gap.**
*Why:* the suppression rules are the highest-stakes configuration in the system. Theater here is worse than absence, because a badge that says "governed" while nothing is governed teaches contempt for all badges.
*Check:* live version + sign-off state + expiry always visible; the unsigned state renders as an explicit refusal.

**REQ-30 — Auth status is visible: which alert sources are authenticated, key health, and whether anything accepts unauthenticated input — with problems rendered as problems, not fine print.**
*Why:* an unlocked door must scream. An unauthenticated alert source is an invitation to inject fake pages.
*Check:* introduce an unauthenticated source; its presence is unmissable.

**REQ-31 — The AI-vs-timer race is visible: how often the AI answers in time, how often the timer wins, and what happens on timeout (the human gets paged — always).**
*Why:* the race is the safety net. Its health is a safety fact, and a degrading AI must be visible before it becomes a failed one.
*Check:* race outcomes visible over time; timeouts visibly resolve to pages.

**REQ-32 — Degraded mode is a designed, honest state. When the AI is down, data is stale, or anything is sick, the interface says so plainly and never renders stale data as if it were live. Degradation always moves toward the human: it pages more, never less.**
*Why:* a silent stale console is how operators learn to distrust the system. Honest degradation is a feature; hidden degradation is a lie.
*Check:* chaos test — degrade the pipeline; the degraded state is unmistakable within seconds; nothing stale renders as live.

### H. Honesty (non-negotiable — these are the product's identity)

**REQ-33 — Simulated vs live is labeled in-band, unmissable, on every surface, always. Nothing simulated can be mistaken for live operation.**
*Why:* practitioners detect the gap between purchased and deployed; trust does not recover. Our own history: a simulated mode that felt dead was judged the worst design we'd shipped.
*Check:* every surface carries its mode; a simulated number never appears without its label.

**REQ-34 — Every number is traceable to its source. "Why this number?" must always have an answer.**
*Why:* untraceable numbers are decoration; traceable numbers are instruments. An operator who can't interrogate a number can't trust it.
*Check:* spot-audit any number in the interface; the trace exists.

**REQ-35 — No fake-real data, ever. Synthetic data is labeled synthetic; demo states never impersonate production.**
*Why:* the moment a demo number is mistaken for a real one, every number becomes suspect.
*Check:* audit all synthetic and demo surfaces for labeling; no exceptions.

**REQ-36 — The interface always shows the pipeline's own health and the age of the data on screen.**
*Why:* the pager can fail too. A console that can't say "I'm sick" or "this is 4 minutes old" teaches distrust with every silent stale render.
*Check:* degrade the pipeline; health state and data age are visible within seconds.

### I. Never build

**REQ-37 — No unbounded alert lists, no "millions of alerts" views, no notification stream without triage.** The firehose is not a feature.

**REQ-38 — No suppression without inspectable evidence and a visible undo.** The black box is not a product.

**REQ-39 — No root-cause tooling leading during active incidents.** Mitigation paths first, analysis after. The wrong job at the wrong time kills people (metaphorically) and sleep (literally).

**REQ-40 — No flattened single narrative across actors.** Per-actor perspectives preserved; sensemaking needs viewpoints, and one log line per event across six actors destroys it.

**REQ-41 — No gamification, streaks, or "alerts handled" counters.** The interface's success is the engineer sleeping — unmeasurable in-app — so the app must never optimize for engagement.

**REQ-42 — No control that promises action and delivers nothing.** Any control labeled as acting (page, suppress, undo, appeal) must act through the real path or not exist. Our history includes a "page me anyway" button that could never page — an operator at 3 AM could believe help was coming when nothing happened. This must be structurally impossible, not just avoided.

## 8. HOW TO THINK (direct instructions — this is the method, not taste)

Do these in order. Skipping a step is how the last design failed.

**1. Write the operator's mental model before anything else.**
Before any structure, any screen, any element: write down, in plain sentences, what the operator *believes* the system is. Example: "The operator believes the kill switch stops all suppression within seconds." Then write what is *actually true*. Where the two can diverge, that divergence is your most important design problem — the interface exists to close it. Our history proves this: nobody wrote down "the operator must believe the kill switch propagates in under 5 seconds" as a model requirement, so no screen ever carried it, and the backend wiring turned out to be fiction. Screens are not models. The model is the thing you test.

**2. State the jobs without mentioning the product.**
For each of the three on-call moments — paged at 3 AM / triaging a storm / calm review — write what the human is *trying to accomplish*, in circumstance language, without naming any feature. Example: "When I'm woken at 3 AM, I want to know whether this needs me or can wait, so I can decide whether to get up." If a job statement mentions the product ("I want to check the dashboard"), rewrite it — you're describing the solution, not the job. No structure gets built until its job statement exists and survives being challenged.

**3. Invert: ask what would make this unusable at 3 AM.**
Before asking what makes a design good, ask what makes it fail. List the failure modes: the exhausted engineer misreads it, the storm makes it unusable, the silent state gets mistaken for the healthy state, the control invites the wrong mental model. Design *against* the list. (Example of the wrong-mental-model test: a tired operator will believe "flipping the kill switch pages everyone instantly" — the design must correct that belief itself, not in documentation.)

**4. Constraint-first: design within the hard constraints, and let them sharpen you.**
Your constraints: scale is the constraint (thousands to millions of signals — REQ-17/18 are non-negotiable); the interface makes zero AI calls on read paths (it reads stored decisions, never asks the AI live); every suppression shows its proof (REQ-9); simulated is always labeled (REQ-33). Constraints are not the enemy of creativity — they are the instrument. The best designs in this space came from someone refusing to cheat the constraint.

**5. Subtract before you add.**
The first design move is removal. Start from the smallest true thing and add only what a job statement demands. Every element you restore must carry a written justification: which job needs it, what happens without it. If you can't write the justification, it doesn't ship.

**6. Ask what each surface teaches.**
Every surface has a curriculum — it teaches the operator what to *expect* from the system. A suppression shown without proof teaches "the machine hides things." A storm shown as a long list teaches "the machine is overwhelmed." Ask of every element: what belief will a tired operator form from this? Then make sure the belief it forms is the true one.

**7. Walk the store.**
Before defending any design decision, *use* it as the 3 AM operator. Walk the 3 AM page flow (Flow 1) through your own design, groggy and impatient, on a phone, with twenty seconds. Where you stumble, the design is wrong — not the user. The designer who won't walk their own store doesn't get a vote.

**8. Assume you're missing something.**
When the person who commissioned this says "this is the worst I've seen," the principal move is not defense — it is the assumption that they are seeing something you are blind to, followed by the work that finds it. Your research (section 9) is that work. Your falsifiers (section 10) are the proof you did it.

---

## 9. HOW TO RESEARCH (do this FIRST, before designing anything)

**You do your own web research. Not ours — yours.** The requirements above came from our research; your job is to challenge, deepen, and extend them with your own. Research ends when the next design decision unblocks — not before (designing on vibes), not after (research as procrastination).

**Where to look:**
- **Linear** — the densest, most opinionated product interface in the industry. Study: how they handle triage queues, how they keep density without chaos, how keyboard-first flows work, how they say no to features.
- **Notion** — study: progressive disclosure at scale, how millions of documents stay navigable, the balance of power and simplicity.
- **Datadog, PagerDuty, Grafana, incident.io** — the incident-management incumbents. Study them as *cautionary* research as much as inspiration: where does each one create fatigue? Where does each one hide its automation's reasoning? What does each one do at 3 AM that works?
- **Any software that monitors millions of realtime events** — stock trading terminals, network operations centers, air-traffic-adjacent tools, large-scale logging (Splunk, Elasticsearch), telecom dashboards. The domain doesn't matter; the *scale problem* does. Find whoever solved "human in command of a firehose" and study them.

**What to look for (your research questions):**
1. How do they handle scale *without* infinite lists? (Aggregation, triage queues, summarization — the actual mechanisms, not the marketing.)
2. How do they keep density without chaos? (What earns space? What gets removed? How is hierarchy signaled?)
3. How do they handle realtime updates without distraction? (Live data that doesn't yank the operator's attention every second — the calm-vs-live tension.)
4. How do they build trust in automated decisions? (What does the machine show about its own reasoning? Where's the undo? What happens when it's wrong?)
5. What do their users complain about? (Support forums, Hacker News threads, reviews — the complaints are more valuable than the feature lists. Every complaint is a requirement someone else learned the hard way.)

**What a good research note looks like.** For each observation, write three lines:
- **Observation:** what the tool does (concrete, specific — "Linear's triage queue shows X," not "Linear is clean").
- **Why it works:** the mechanism, not the aesthetic — *why* does this work for a tired human at scale?
- **Steal / avoid:** what you'd take for Sentinel and what you'd refuse — with the reason.

**Deliverable of this phase (for yourself, not for us):** a research log with at least 20 such notes, and a one-page "decisions this unblocks" summary. If your research doesn't change at least three of your initial instincts, you didn't research — you confirmed.

---

## 10. HOW TO STAY DISCIPLINED (the falsifiers)

These are tripwires. If you catch yourself doing any of these, stop — you're doing theater, not design. Check yourself against this list at every review point.

**The AI-generic recognition test.** AI-generated design has tells, and they are *thinking* tells, not color rules: the same hero layout on every landing page; glassmorphism applied to everything because it looks "premium"; purple-blue gradients as a substitute for hierarchy; emojis used as iconography; lorem-ipsum density (lots of elements, no information); cards that all look equally important. The discipline: for every element, ask "does this carry information, or does it carry *vibe*?" If vibe, delete it. Generic design is what happens when no decision was made — your job is the decisions.

**The 3 AM test.** Would an exhausted, groggy engineer understand this in 10 seconds? Not "is it pretty" — *can they act on it while half-asleep?* If a surface requires careful reading, comparison, or interpretation to use safely at 3 AM, it fails. Simplify until the 10-second version is the whole version.

**The scale test.** Does this survive 10,000 open problems? Mentally load your design with ten thousand items. What breaks? (The list, the counts, the filters, the human.) Whatever breaks was never the design — it was a demo. Fix it now, not later.

**The honesty test.** Is every number traceable? Is simulated labeled on every surface? Could any element be mistaken for live production data when it isn't? Could any control promise action it doesn't deliver? One "yes" to the wrong question fails the whole design — honesty is binary.

**One question per surface.** Every surface answers exactly one operator question ("what needs me?", "why was this suppressed?", "are the guardrails live?"). If you can't state a surface's one question in a sentence, the surface doesn't know what it is yet. Split it or kill it.

**One job statement per surface.** From section 8: no surface gets built without its job statement surviving a challenge. A surface without a job statement is decoration.

**The hierarchy-without-color test.** Remove color entirely: does the most important thing still stand out first? Hierarchy must be carried by structure and order, never by hue alone. (This is a thinking tool, not an aesthetic rule — it's about whether your information architecture works, not about palettes.)

**The "who is this for" test.** Point at any element and ask: which of the three users (3 AM engineer, SRE lead, platform operator) needs this, in which flow? If the answer is "everyone, generally" — it serves no one specifically. Cut it or place it where its user lives.

**The subtraction audit.** At every milestone, remove 20% and see what breaks. What breaks was load-bearing; what doesn't was decoration. Our best designs will be the ones that survived this repeatedly.

---

## 11. THE BUILD CONTRACT

**What you deliver:** a single HTML file containing the complete redesigned Sentinel console. Zero limits on code length — take the space you need. It must run by opening the file (no build step, no server required for the simulated experience).

**Nothing in this document prescribes layout, position, visual hierarchy, or navigation structure. Those are yours to derive from your research.** If any sentence above seemed to tell you where something goes or what it looks like, it was a mistake — the requirements constrain *what must be true*, never *where it lives or how it appears*.

**What "done" means (all must hold):**
- [ ] Every requirement REQ-1 through REQ-42 is satisfied. For each, you can point at the design and show how.
- [ ] All six user flows (section 6) are walkable end-to-end in the file. The 3 AM flow works on a small viewport.
- [ ] The simulated experience is complete and honest: labeled in-band on every surface (REQ-33), every number traceable (REQ-34), zero fake-real data (REQ-35).
- [ ] The safety machinery exists and is first-class: kill switch with proof (REQ-28), policy governance (REQ-29), auth status (REQ-30), race visibility (REQ-31), degraded mode (REQ-32).
- [ ] The scale problem is solved, not deferred: the design has a stated answer for 10,000 open problems and a designed storm state (REQ-17, REQ-18).
- [ ] Your research log exists (20+ notes in the observation → why → steal/avoid form) and your "decisions this unblocked" page names at least three instincts the research changed.
- [ ] Every surface has its one-question sentence and its job statement written down (they can live in comments or an accompanying notes section — Aditya will read them).
- [ ] The falsifiers (section 10) have been run at least twice during the build, with the results noted.

**How it will be judged:** Aditya's eyes, against the falsifiers. He is the skeptic described in section 5 — tired of bad tools, expert, unimpressed by novelty. He will check: does it answer "what's happening" in ten seconds? Does it survive the scale test? Is every number honest? Can he find the kill switch's proof? He will not judge your colors — he will judge whether the design *works*. The highest praise available is: "I trust this at 3 AM."

**One last useful detail, from the team that failed before you:** the last redesign was judged "the worst I've seen" not because it was ugly, but because it answered the wrong question — it showed *artifacts* (decisions made, logs, settings) when the operator needed *the live system* (what's happening right now). Whatever else you do: answer "what's happening right now" first. Everything else is second.

Good luck. Design like someone's sleep depends on it — because it does.
