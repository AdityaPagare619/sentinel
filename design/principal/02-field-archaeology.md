# Field Archaeology — Why the Paging Stack Looks the Way It Does

*Law 5 (Chesterton's fence) working paper. PAGER lane, 2026-10-03.*
*Builds on `research/sre-field/2026-10-02-escalation-dedup-craft.md` and*
*`research/sre-field/2026-10-03-webhook-payloads.md` — practitioner mechanics*
*are not re-collected here; this paper asks WHY they exist.*

## Doctrine

Every "ugly" thing in the paging stack — the escalation ladder, the dedup
window, the weekly rotation, the 30-second group wait — is a scar. Each was
cut by a specific outage, a specific customer scream, a specific 3 AM. Law 5
forbids us from proposing changes to any of them until the scar's story is
named and its reason addressed.

Format per fence: **the ugly thing** → **the 3 AM that built it** (named,
sourced, access-dated) → **the irreducible reason** → **Sentinel's answer**,
with the reason addressed rather than ignored.

---

## Fence 1 — The escalation ladder (one human at a time, ack windows, manager anchor)

**The ugly thing.** PagerDuty/Opsgenie page exactly one person at a time. If
L1 doesn't acknowledge in 5–15 minutes, the incident escalates to L2, then L3
(usually a manager), then re-cycles. No group blasts. No "page the channel."

**The 3 AM.** Two, layered:

(a) *The broadcast era.* PagerDuty was founded in 2009 by three ex-Amazon
engineers — Andrew Miklas, Alex Solomon, Baskar Puvanathasan — who carried
pagers at Amazon and built the company to fix what Amazon's alerting did to
them (Y Combinator S10; SiliconANGLE, 2012-02-07; ycombinator.com/people/andrew-miklas;
accessed 2026-10-03). Their founding design decision, stated explicitly in
2012: *"PagerDuty doesn't do group alerts, that way a person receiving a
particular alert knows that it is their responsibility to either act or not
act on a particular alert."* Group paging had produced the bystander effect
at scale — an alert to everyone is an alert to no one, because each recipient
assumes someone else is handling it. The one-at-a-time ladder is not a
notification preference; it is an **anti-bystander protocol**.

(b) *Target, November–December 2013.* Target's $1.6M FireEye deployment
worked. The Bangalore SOC saw "malware.binary" alarms at the top of FireEye's
criticality scale on **November 30, 2013**, escalated them to the Minneapolis
security team per protocol — and Minneapolis did nothing. More alerts on
December 2. Nothing. The U.S. Department of Justice contacted Target on
December 12. Result: 40 million card numbers and 70 million customer records
exfiltrated (Infosecurity Magazine, reporting Bloomberg Businessweek's
account; thefreelibrary.com teaching case; arxiv.org/pdf/1701.04940; AP via
phys.org; all accessed 2026-10-03).

**The irreducible reason.** The ladder is a *retry protocol over unreliable
humans* — phones die, people sleep, people misjudge. But Target proves the
deeper fence: **escalation is a routing mechanism, not an action mechanism.**
The page reached the correct human through the correct ladder, and the
incident still burned for two weeks. Delivery ≠ action. The ladder guarantees
*someone was told*; nothing in it guarantees *someone understood*.

**Sentinel's answer (reason addressed).** We keep the ladder — the human-retry
protocol is sound and the bystander lesson is permanent. What changes is the
*first rung's payload*: a Sentinel disposition is never just a verdict
("suppress"/"page"). It is verdict + evidence + recommended action, attached
to the incident before the ladder starts climbing. The fence's reason —
"someone must be unambiguously responsible" — is preserved; the failure mode
Target exposed — "the responsible person didn't understand what they were
looking at" — is what the proof engine attacks. We do not make the ladder
smarter about *who*; we make the first step undeniable about *what*.

---

## Fence 2 — The urgency model and the phone call (why PagerDuty still calls you in 2026)

**The ugly thing.** High/low urgency; phone calls and SMS for SEV1; push for
the rest; the practitioner rule that "a user with only email notification
rules is effectively never paged" (prior research, 2026-10-02). In an era of
Slack, the phone call persists.

**The 3 AM.** *Meta, October 4, 2021.* A routine backbone-capacity audit
command — missed by its own safety-check tool — severed every link between
Meta's data centers. The DNS fail-safe then withdrew Meta's own BGP routes,
deleting facebook.com, Instagram, and WhatsApp from the internet for ~6
hours. The same outage took down Meta's *internal* tools, Workplace
(internal comms), and — per the New York Times — the **electronic badge
system**, so engineers couldn't physically enter buildings. Recovery required
dispatching a team to the Santa Clara data center for a manual reset
(Facebook VP Infrastructure statement via appleinsider.com, 2021-10-04/05;
techtarget.com; Cloudflare's analysis via rappler.com/dev.to; accessed
2026-10-03).

**The irreducible reason.** **The escalation path must not share fate with
the thing that is broken.** Every in-band notification channel — Slack, push
via the corporate MDM, email on the corporate domain — rides on
infrastructure that can be part of the incident. The phone call survives
because the PSTN/cellular network is the one channel the data-center outage
doesn't take down. Urgency tiers are really *channel-diversity tiers*.

**Sentinel's answer (reason addressed).** Two consequences:

1. Sentinel's dispositions ride the *existing* urgency model — we never invent
   a new severity taxonomy the org doesn't already use. The gate outputs a
   disposition; the existing policy maps it to channels. The channel-diversity
   fence is untouched.
2. Sentinel's own infrastructure must be **out-of-band by design**: the
   shadow pipeline, the gate, and above all the kill switch must not depend
   on the monitored estate's network, DNS, or identity systems. A gate whose
   bypass requires the dashboard that is down is a gate that fails closed at
   the worst moment. (Law 7, infra constitution: blast-radius thinking.)

---

## Fence 3 — Dedup keys and incident merging (the flood wall)

**The ugly thing.** PagerDuty's `dedup_key`: same key + open incident ⇒ the
new trigger is *appended to the incident log*, not paged again. Resolve the
incident and the next trigger with the same key opens a **new** incident —
it never resurrects the old one. Acknowledge/resolve events against resolved
or nonexistent incidents are discarded (vendor behavior, documented in
`research/sre-field/2026-10-03-webhook-payloads.md`, §1.5; accessed
2026-10-03). The 2012 founding-era description: *"The software de-duplicates
alerts, so if a problem is affecting 200 servers, only one alert is sent to
the person on-call"* (SiliconANGLE, 2012-02-07; accessed 2026-10-03).

**The 3 AM.** The NOC flood: during a genuine large outage, every monitoring
integration fires independently and the on-call receives hundreds of pages
for one root cause. The canonical modern instance is the **AWS us-east-1
EBS incident, April 21–24, 2011** — a cascading control-plane failure in
which thousands of volumes stuck, customers' monitoring fired everywhere,
and responders drowned in symptom alerts while the root cause (a network
configuration change during a routine upgrade) went unaddressed for hours.
Dedup exists because without it, the biggest incidents — the ones where
paging matters most — are precisely the ones where paging becomes useless.

**The irreducible reason.** **One root cause must produce one unit of human
attention.** Dedup is not a convenience; it is the mechanism that keeps the
paging channel's signal-to-noise ratio from collapsing exactly when the
signal matters most. The "fresh episode after resolve" rule is its corollary:
resurrecting a resolved incident would re-attach stale context (old timeline,
old responders, old hypotheses) to a new failure — the team would fight the
last war.

**Sentinel's answer (reason addressed).** We keep the flood wall — no design
removes dedup. We strengthen its two weak joints:

1. *Fingerprint collisions.* The Law 6 pre-mortem already names it: an
   allowlist fingerprint that collides across services merges two different
   failures into one incident and the second one is never paged. Sentinel's
   gate must **audit its own fingerprints** — collision detection on the
   dedup key space, with a loud alert when two distinct alert classes hash
   together. The flood wall must never become a trapdoor.
2. *Semantic grouping over string matching.* The fence's reason is "one root
   cause, one page" — the `dedup_key` string is just today's approximation
   of "same root cause." Sentinel's correlator may group by dependency
   topology (Alertmanager-style inhibition: the DB is down, so suppress the
   fifty "can't reach DB" pages), but **only as a refinement of the dedup
   contract, never as a replacement for it**. If the topology is wrong, the
   string-level dedup still holds the wall.

---

## Fence 4 — Grouping and inhibition (group_wait, group_interval, inhibit_rules)

**The ugly thing.** Alertmanager's `group_by` / `group_wait: 30s` /
`group_interval: 5m` / `repeat_interval`, plus `inhibit_rules` ("if the DB
is down, suppress downstream 'can't reach DB' pages — page the root cause
once"). Practitioner consensus calls the grouping/inhibition config "the
single highest-leverage piece of Alertmanager" (prior research, 2026-10-02).

**The 3 AM.** The Prometheus/Alertmanager stack was built at **SoundCloud
(2012)** by engineers living through cascading-failure pager storms: one
root cause, hundreds of symptom alerts, each producing its own page. The
`group_wait` exists because the first alert of a cascade arrives *before*
its siblings — notify instantly and you page N times; wait 30 seconds and
you page once with N lines. `repeat_interval` exists because the opposite
failure also happened: without it, an unacknowledged firing alert re-paged
every evaluation cycle until someone's phone melted. Inhibition rules exist
because someone once got paged 200 times for one dead database and wrote the
rule so nobody ever would again.

**The irreducible reason.** **Time is the cheapest correlator.** The 30s
wait and 5m interval are not magic numbers — they are admissions that
causally-related alerts arrive *clustered in time*, and that a small,
bounded delay buys an enormous reduction in pages. The fence is: never trade
a bounded delay for an unbounded page storm.

**Sentinel's answer (reason addressed).** This fence *constrains our gate
latency budget*. Sentinel's pre-page gate sits in the paging path; whatever
time it takes is added to every page. The fence says: a bounded delay (tens
of seconds) in exchange for fewer, better pages is a trade the industry has
already accepted — twice (group_wait, and the flap `for:` clause in Fence 5).
So the gate gets an explicit latency budget (p99 ≤ 30s, target ≤ 5s), and
— critically — **the budget is spent on evidence assembly, not model
inference**. The Jev call is the uncertain-latency component (measured
~11.4s vs 70–500ms spec; see memory); the deterministic guardrails
(fingerprint match, topology inhibition, urgency mapping) run first and
fast. If the model hasn't answered within budget, the gate **fails open to
page** (Law 2: uncertainty pages). The fence's reason — bounded delay,
unbounded storms avoided — is honored by construction.

---

## Fence 5 — Flap dampening and never-auto-close (Nagios's 21 checks, Prometheus's `for:`)

**The ugly thing.** Nagios: 21-check history, high/low flap thresholds
(defaults 20.0/5.0), "flapping start/stop" notifications, all other
notifications *suppressed while flapping*. Prometheus: the `for:` clause
(fire only if true for N minutes). PagerDuty Events API: resolve +
re-trigger = **new incident**, never a resurrection. And the standing rule
from practitioner craft: **never auto-close** (prior research, 2026-10-02).

**The 3 AM.** Two:

(a) *The flap storm.* Nagios's flap detection was written by Ethan Galstad
because flapping checks produced "a storm of problem and recovery
notifications" — and his own documentation contains the most honest
archaeological admission in this paper: *"flapping detection has been a
little difficult to implement. How exactly does one determine what 'too
frequently' means in regards to state changes for a particular host or
service? When I first started looking into flap detection I tried to find
some information on how flapping could/should be detected. After I couldn't
find any, I decided to settle with what seemed to be a reasonable
solution."* (assets.nagios.com, Nagios Core docs, "Detection and Handling
of State Flapping"; accessed 2026-10-03). **The 21-check window and the
thresholds are one engineer's reasonable guess, fossilized into two decades
of infrastructure.** The fence here is meta: some fences are load-bearing
(the storm is real), and their *parameters* are arbitrary (the numbers are
not). A principal must know which is which.

(b) *The resurrection bug.* The never-auto-close / fresh-episode rule exists
because resurrected incidents killed people — operationally. An incident
that auto-closes when its alert clears, then reopens on the next flap,
carries forward a stale timeline: responders join mid-thread, assume the
old diagnosis holds, and miss that the *cause changed between episodes*.
PagerDuty's API enshrines the lesson mechanically: resolve is terminal; the
next trigger is a new incident with a clean timeline.

**The irreducible reason.** **Episodes are the unit of understanding, not
alerts.** A flapping check is not N incidents; it is one unstable episode.
A resolved-then-refired alert is not a continuation; it is a new episode.
The machinery (dampening, fresh incidents, human-declared resolve) exists to
keep the *narrative* of the incident aligned with reality.

**Sentinel's answer (reason addressed).** Three design consequences,
already partially captured in ADR-001:

1. The gate's dispositions are **episode-scoped, not alert-scoped**. A
   suppress decision on a flapping alert covers the episode; when the
   episode ends (clean resolve + quiet period) the disposition expires. A
   re-fire is re-evaluated fresh — we never let a stale "suppress" leak
   into a new episode.
2. **Never auto-close, never auto-resolve.** Sentinel may recommend
   resolution with evidence; only a human (or the upstream system that owns
   the incident) declares it. The gate is a pre-page filter, not an
   incident lifecycle manager. (Law 1: don't let the gate become a second
   incident system.)
3. The flap parameters (windows, thresholds) are Type 2 decisions (Law 3):
   tunable per team, versioned in config, explicitly *not* sacred. The
   sacred part — the load-bearing fence — is that flapping is named,
   bounded, and visible, never silently swallowed.

---

## Fence 6 — Rotation math (the 8-person floor, follow-the-sun, handoff meetings)

**The ugly thing.** Google SRE Book, chapter 11: minimum 8 engineers for a
single-site rotation (primary + secondary), 5–6 per site for follow-the-sun;
≤25% of time on-call; ≤2 pages per shift; a handoff meeting at every shift
change. Weekly (not daily) rotations as the norm. Below the floors, the book
is blunt: the rotation is unsustainable regardless of tooling (prior
research, 2026-10-02, citing SRE Book ch. 11 and practitioner sources).

**The 3 AM.** Google's own operational history: the numbers are not theory,
they are the fossil record of burnout studies — teams that carried pagers
above 25% time or with fewer than 8 people produced worse incident outcomes
*and* attrition, and the "≤2 events per shift" cap exists because the third
page of a night is answered by a cognitively impaired human. The handoff
meeting exists because of the incident class where the failure started
during shift change and neither the outgoing nor incoming engineer had the
full picture. (The extreme historical exhibit for lost situational
awareness across a shift boundary is the **2003 Northeast blackout**:
FirstEnergy's alarm system had been silently dead for 88 minutes — see
Fence 7 — and operators dismissed a warning call from American Electric
Power about a tripping 345 kV line, partly because no one in the room had a
coherent picture of system state. U.S.-Canada Task Force final report, April
2004; en.wikipedia.org/wiki/Northeast_blackout_of_2003; accessed
2026-10-03.)

**The irreducible reason.** **Humans are the constraint, and the constraint
is biological.** No model, no automation, no tooling changes how much
sleep-deprived interruption a person can absorb. Rotation math is not an
engineering choice; it is occupational health with a pager attached.

**Sentinel's answer (reason addressed).** Law 1, global optimization:
*don't question the rotation; question whether the page had to happen.*
Sentinel cannot change the 8-person floor, the 25% cap, or the biology.
Its only lever — and it is a large one — is **cost per page**: every
suppressed non-actionable page is on-call time returned to the humans, and
every enriched page (evidence + action attached, Fence 1) is faster
resolution, which is fewer follow-up pages. For the 4-person team that can
never reach Google's floor, page quality is not an optimization — it is the
*only* available lever, which is exactly why the practitioner note in our
prior research holds: "a 4-person team with clean alerts sustains
indefinitely; a 6-person team with noisy alerting burns out regardless."
Sentinel's product thesis, reduced to one line: **make the small team
sustainable by making every page worth waking for.**

---

## Fence 7 — Silences, maintenance windows, and the watchdog (who watches the monitor)

**The ugly thing.** Silences/maintenance windows are always **scoped** (to
the affected service, never the whole team's paging), **time-bound** (they
expire), **reason-attached**, and logged for audit. P1/P2 are never
silenced — the safety override. (Practitioner consensus, prior research
2026-10-02.)

**The 3 AM.** Two again, because this fence has two posts:

(a) *The silence left on.* Every SRE org has one: a maintenance window
created for a deploy, scoped too broadly or left to run long, during which
a real SEV1 fired into the void. The scoping/time-bound/reason-attached
doctrine is the scar tissue. The named exhibit for automation left
disarmed: at Target (2013), the FireEye tool *could have been configured to
delete the detected malware automatically* — and that auto-remediation was
**deactivated** by Target's own security team (Bloomberg Businessweek,
via itnews.com.au and darkreading.com; accessed 2026-10-03). The industry's
standing position ever since: automatic action stays gated behind human
judgment, because the one time the automation is wrong about *what it is
looking at*, nobody is watching.

(b) *The monitor that died silently.* **August 14, 2003, 14:14 EDT**: a race
condition in GE Energy's XA/21 energy management system stalled
FirstEnergy's control-room alarm system in Akron, Ohio. Operators were
**unaware of the malfunction** — screens kept showing live grid data while
the warning layer sat frozen. For 88 minutes the Harding-Chamberlin 345 kV
line tripped with no announcement. The backup EMS server then failed under
the queued unprocessed events; screen refresh degraded from 1–3 seconds to
59 seconds. ~50 million people across eight U.S. states and Ontario lost
power; 265+ plants shut down (U.S.-Canada Power System Outage Task Force
final report, April 2004; en.wikipedia.org; accessed 2026-10-03). The
blackout's deepest lesson for our purposes is not the trees or the race
condition — it is that **the alerting system failed silently and no one
knew**, and everything downstream (the dismissed AEP call, the missed
cascade) followed from that silence.

**The irreducible reason.** **Suppression machinery must be louder about its
own health than about anything else.** Every silence, every maintenance
window, every auto-suppression is a small, deliberate blindness — and
deliberate blindness must be scoped, expiring, logged, and overridable, or
it becomes accidental blindness. And the suppression system itself must be
watched harder than the systems it watches.

**Sentinel's answer (reason addressed).** This is the fence that most
directly shapes Sentinel's architecture:

1. **Fail-open, always.** If the gate cannot reach its model, its
   fingerprint store, or its topology data — or if it cannot decide within
   its latency budget (Fence 4) — it pages. Uncertainty pages (Law 2). A
   gate that fails closed is a FirstEnergy alarm system with better
   marketing.
2. **The watchdog.** Sentinel's gate emits a heartbeat, and a separate,
   minimal dead-man's switch watches it. Gate silent for >N seconds ⇒ page
   the platform team through a path that does not depend on the gate
   (Fence 2: out-of-band). The gate's own health is the highest-urgency
   alert in the system — louder than any customer SEV1 — because a dead
   gate is *every* alert silenced at once.
3. **Suppression receipts.** Every suppressed alert produces a durable,
   auditable receipt (what, why, confidence, what would have paged it) —
   the "muted-not-dropped" visibility state from ADR-007. Nothing is ever
   silently dropped; the difference between "suppressed" and "lost" is the
   receipt. Silences in the old world were scoped, expiring, and logged;
   Sentinel's suppressions inherit all three properties mechanically.

---

## CREATIVE APPLICATION — what the archaeology changes about Sentinel's design

Seven fences, distilled into design law. Each is a constraint the principal
redesign must satisfy, not a suggestion:

1. **The disposition is evidence + action, not a verdict.** (Fence 1,
   Target.) The gate's output schema must carry: the disposition, the
   evidence that earned it, and the recommended human action. A page that
   says "disk 92% full, suppress" is a verdict; a page that says "disk 92%,
   growing 1%/hr for 6h, runbook RB-14 step 3, on-call ack'd similar 3
   times this month" is undeniable. Target's Minneapolis team had a
   verdict; they lacked understanding. We sell understanding.

2. **The gate is out-of-band or it is a liability.** (Fence 2, Meta 2021.)
   Separate network path, separate credentials, separate identity from the
   monitored estate. The kill switch must work when everything else is
   down. This is a Type 1 decision (Law 3): the paging-path architecture
   gets RFC-grade rigor.

3. **Dedup is sacred; fingerprints are audited.** (Fence 3.) Keep the
   flood wall exactly as the industry built it. Add mechanical collision
   auditing on the fingerprint space — the Law 6 pre-mortem made concrete.
   Topology-aware grouping is a refinement of dedup, never a replacement.

4. **The gate has a latency budget, and the budget is spent on evidence.**
   (Fence 4.) p99 ≤ 30s, target ≤ 5s. Deterministic guardrails first and
   fast; the model call is the budgeted, uncertain component; budget
   exhausted ⇒ page. The industry already accepted bounded delay for fewer
   pages — twice. We accept it a third time, explicitly.

5. **Dispositions are episode-scoped; resolve is human.** (Fence 5.)
   Suppress-the-episode, re-evaluate the re-fire, never auto-close, never
   resurrect. Flap parameters are Type 2 (tunable, versioned, not sacred);
   the episode as the unit of understanding is Type 1 (sacred).

6. **The product thesis in one line.** (Fence 6.) *Make the small team
   sustainable by making every page worth waking for.* We do not touch
   rotation math — it is biology. We attack cost-per-page, the only lever
   available to teams below the 8-person floor.

7. **The watchdog is the highest-urgency alert in the system.**
   (Fence 7, FirstEnergy.) Fail-open on any gate fault. Heartbeat +
   dead-man's switch on a path independent of the gate. Every suppression
   emits a receipt: scoped, expiring, logged — the old silence doctrine,
   mechanized. A dead gate must be louder than any customer SEV1, because
   a dead gate is every alert silenced at once.

**The meta-finding** (from Fence 5a, Galstad's confession): some fences are
load-bearing and their parameters are arbitrary. The principal's job is to
know which is which — and to mark every arbitrary parameter as a Type 2
decision with its arbitrariness stated in the open, so the next maintainer
(the tired one, at 3 AM, per Law 2) can change the number without fearing
they're tearing down the wall.

---

*Sources accessed 2026-10-03 unless noted. Prior practitioner mechanics:*
*`research/sre-field/2026-10-02-escalation-dedup-craft.md` (accessed*
*2026-10-02), `research/sre-field/2026-10-03-webhook-payloads.md` (accessed*
*2026-10-03). Vendor docs: PagerDuty developer-docs (webhooks v3),*
*support.pagerduty.com/docs/webhooks.*
