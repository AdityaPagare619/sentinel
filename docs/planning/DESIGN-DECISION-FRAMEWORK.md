# Design Decision Framework — How Principal-Disciplined Designers Think, Decide, and Categorize

**Status:** research lane deliverable (expert-decision-methodology). Aditya's order: understand how industry
experts *decide* — the process, not the portfolio. Feeds the complete redesign.
**Companion docs:** `PROFESSIONAL-TEARDOWN.md` (what the tools do), `USER-WORKFLOWS.md` (what on-call needs),
`design/INTERFACE_PRINCIPLES.md` (the constitution this framework enforces).

**Evidence honesty note:** every method below is sourced from public writing by the named practitioner.
Where I worked from search-result summaries rather than full primary texts, the source line says so.
Nothing here is quoted as verbatim unless it is.

---

## §1. How the experts decide — the actual methods

### M1. Conceptual model first, screens last (Don Norman)
**Source:** *The Design of Everyday Things* (revised ed., 2013), ch. 1 — the designer's model / user's model /
system image triangle. The designer expects the user's model to match the design model, but the designer never
talks to the user directly: *all communication happens through the system image*. If the system image is
incoherent, the user builds the wrong model and blames themselves.

**The method:** before any screen, write down (a) the designer's conceptual model — how the system actually
works, in plain sentences; (b) the model a tired 3 AM operator will *actually* form from what the screen shows;
(c) the deliberate bridges between them. Norman's operational rules: make system state visible, provide
immediate and interpretable feedback, use natural mappings (action → expected result), use constraints to
forbid wrong actions rather than documenting them away.

**Why it matters for Sentinel:** the current UI failed at exactly this layer. Nobody wrote down "the operator
must believe the kill switch propagates in under 5 seconds" as a *model* requirement, so the screen never
carried it — and the backend wiring turned out to be fiction (R-10). A screen is not a model. The model is
the thing you test.

### M2. Jobs to be done, stated without the product (Clayton Christensen)
**Source:** Christensen, *Competing Against Luck* (2016); HBR "Know Your Customers' Jobs to Be Done" (2016);
the milkshake study (Bob Moesta / Re-Wired Group fieldwork). A job is "the progress that a person is trying to
make in a particular circumstance." Customers *hire* products and *fire* them. The job statement format —
"When [circumstances], I want [progress], so I can [outcome]" — *never mentions the product*. Jobs are stable;
solutions change.

**The method:** every screen must name the job it is hired for, in circumstance language. The three dimensions
are mandatory, not decorative: **functional** (what they need to do), **emotional** (how they want to feel),
**social** (how they want to be perceived). Most teams design only for the functional job and lose. The forces
model (Moesta/Spiek): a switch happens only when Push + Pull > Habit + Anxiety — reducing *anxiety* (will this
suppression lose a page?) often beats increasing *pull* (a prettier dashboard).

**Why it matters for Sentinel:** our emotional job is *trust at 3 AM* and our anxiety is *the machine will
suppress something it shouldn't*. Every suppression surface must be designed against the anxiety, not the
functional job. The milkshake lesson: asking users "what do you want in the dashboard?" produces nothing;
watching what job the dashboard is hired to do (survive the night without missing a real page) changes the
design.

### M3. Friction-log the real thing (Stripe — Katie Dill)
**Source:** Katie Dill (Head of Design, Stripe; ex-Airbnb, ex-Lyft), via Lenny's Newsletter interview "Building
beautiful products" and the Creator Economy writeup of her talks. Stripe's practices: **MVQP over MVP**
(Minimum Viable *Quality* Product — solves the user problem completely, with the refinement that builds trust);
**Quality = Utility + Usability + Beauty**, and beauty measurably converts (Stripe's email typography/layout
work: +20% conversion); the **"15 Essential Journeys"** — the top 15 user flows, friction-logged every quarter
and scored on a 5-point red→green scale; **"Walk the Store"** — PMs, designers, and engineers *use the product*
and document every pain point; **"Minimize presentationing"** — review the product itself, never a deck about
the product.

**The method:** the unit of design criticism is a *journey walked with a friction log*, not a screen reviewed
in a meeting. The redesign's first artifact must be a friction log of the current UI (the 15 essential
journeys of an on-call: paged → acknowledge → triage → resolve → audit → configure), scored honestly.

**Why it matters for Sentinel:** Aditya's verdict ("worst I have ever seen") is a friction log of one. The
professional response is not defensiveness — it is fifteen journeys, scored red-to-green, in writing, before a
single new screen is drawn.

### M4. The 80% case and the 30-second test (Julie Zhuo)
**Source:** Julie Zhuo (VP Product Design, Facebook 2006–2020; *The Making of a Manager*, 2019; juliezhuo.com
essays). Her decision questions: *What is the job to be done? What does success look like? What is the 80%
case — are we optimizing for that? Can a new user figure this out in 30 seconds? What is the simplest version
that solves the problem?* Her red flags: designer-centered design (made for designers, not users),
over-complexity (solving edge cases first), delight-before-utility (pretty but not useful).

**The method:** for every screen, write the 80% case in one sentence and the 30-second test (what a new
on-call understands in 30 seconds without explanation). If the screen optimizes an edge case, it is wrong.
Delight is a layer applied *after* utility works — never a substitute.

### M5. Feature first, grayscale first (Refactoring UI — Adam Wathan & Steve Schoger)
**Source:** *Refactoring UI* (2018). "Start with a feature, not a layout": don't design the app shell (nav,
sidebar, logo) first — an app is a collection of features, and you don't have enough information to design
navigation until you've designed features. "Detail comes later": design in grayscale first, so hierarchy must
be carried by spacing, size, and contrast — if the grayscale version is confusing about what matters most,
color on top is decoration standing in for an unsolved hierarchy problem. "Most design problems are hierarchy
problems": everything looks equally important, so nothing feels important. Systems, not ad-hoc values:
constrained spacing/type/color scales. "Be a pessimist": design the smallest useful version first.

**The method:** the redesign draws the *page moment* (a page arrives — what does the operator see?) before the
navigation, and every screen passes the grayscale test: cover the color, and the eye must still land on the
most important thing first.

### M6. Strip to the core, then restore with care (Apple — HIG)
**Source:** Apple Human Interface Guidelines, "Designing for iOS 7" (the themes of **Deference** — the UI never
competes with content; **Clarity** — every element serves a purpose; **Depth** — layers communicate hierarchy):
Apple's stated method — *"First, strip away the UI to expose the app's core functionality and reaffirm its
relevance. Next, use the themes to inform the design. Restore details and embellishments with care and never
gratuitously. Throughout, be prepared to defy precedent, question assumptions, and let a focus on content and
functionality motivate every design decision."*

**The method:** a mandatory *strip pass*: remove every element from the current UI and ask "is the core
functionality still exposed and relevant?" Anything restored must be justified in writing. "Defy precedent" is
permission to not copy PagerDuty's layout when our job (prove the machine's decisions) differs from theirs
(route the human's attention).

### M7. Appetite before scope (Basecamp — Ryan Singer, *Shape Up*)
**Source:** *Shape Up* (Basecamp, 2019; free at basecamp.com/shapeup). The central inversion: don't ask "how
long will this take?" — ask **"how much time is this worth?"** (appetite). Shaping (senior, separate track)
turns raw ideas into pitches that are *rough, solved, bounded*: rough enough for team creativity, solved enough
that no rabbit holes are handed to the team. The unifying principle: **target the risk of not shipping** —
shaping de-risks open questions, betting caps commitment, building integrates early.

**The method:** the redesign is scoped by appetite, not by feature list. Each beat gets a fixed budget and a
shaped pitch (problem, appetite, sketch, risks); the team owns the rest. This is how the redesign ships instead
of becoming a second backlog.

### M8. Opinionated beats flexible (Linear — Nan Yu)
**Source:** Nan Yu (Head of Product, Linear), Lenny's Podcast (Jan 2025) and the Behind-the-Craft transcript:
speed and quality are not at odds — *speed is a result of competence*; strict anti-bloat (refuse customization
features that worsen the core workflow); **focus on the "main thing"** and over-provision it; **"better to be
decisively wrong and pivot than cautiously mediocre"**; minimize meta-work so users focus on their jobs; ditch
the "As a user, I want X" formalism — it obscures the actual need; his "double triangle" framework for product
work.

**The method:** the redesign takes positions in writing, with the courage to be wrong. Every major screen
carries a one-line point of view ("the river is a proof ledger, not an alert feed"). Cautious mediocrity —
the dashboard that shows a bit of everything — is the named enemy.

### M9. Measure only to decide (John Cutler)
**Source:** John Cutler ("The Beautiful Mess" newsletter; "12 Signs You're Working in a Feature Factory").
Three load-bearing ideas: **deconstruct "product sense"** into tangible competencies (systems thinking,
facilitation, uncertainty modeling) — vague talent-talk is gatekeeping; **measurement is uncertainty
reduction** (after Douglas Hubbard): "you only need to reduce the uncertainty to an acceptable amount to make
the *next* decision"; **outcomes over outputs**, and never copy-paste ("a blog post is not a blueprint" — adapt
to your context); beware the **alignment trap**, where fragile consensus is valued over a true solution.

**The method:** research stops when the next decision is unblocked — not when the researcher feels thorough.
Every research artifact ends with "the decisions this unblocks."

### M10. Explore the maze before converging (Figma — Dylan Field)
**Source:** Dylan Field (CEO, Figma), Lenny's Podcast and Stratechery interviews (2025–2026): design is
**craft + attention to detail + point of view**; AI makes breadth cheap — the failure mode is *tunnel vision*,
getting attached to one generated direction; **"going fast in the wrong direction is not progress"**; great
design needs breadth, exploration, and a mental model of the user; his learning stance — *"assume you're
missing something"* when you don't understand.

**The method:** divergence is a scheduled phase with a quota (N distinct directions explored before any
convergence), and convergence is a decision with written reasoning. Speed in the wrong direction is measured
as waste, not velocity.

---

## §2. How they categorize — the dimensions experts sort design problems along

| # | Dimension | Source | The sort | Sentinel application |
|---|-----------|--------|----------|---------------------|
| D1 | Job dimensions | Christensen (JTBD) | Functional / Emotional / Social — every job has all three; omit one and the design fails | Every screen names all three; the emotional job is *trust*, the anxiety is *wrongful suppression* |
| D2 | Model triangle | Norman | Design model / User's model / System image — the UI is only the image; the models are the design | Write the two models before the image, for every critical surface |
| D3 | Hierarchy tiers | Refactoring UI | Primary / Secondary / Tertiary — color, weight, size assigned by tier; color always carries meaning | Grayscale test per screen; color reserved for state (page/suppressed/degraded) |
| D4 | Risk type | Shape Up | Shipping risk vs understanding risk — shape de-risks the open questions *before* the bet | The redesign's open questions (what does the operator believe?) are research-shaped first |
| D5 | Case frequency | Zhuo | The 80% case vs edge cases — optimize the former, don't lead with the latter | The 3 AM page is the 80% case; the policy-change ceremony is not the home screen |
| D6 | Outcome vs output | Cutler | Outcomes (trust, fewer missed pages) vs outputs (screens shipped) | The redesign is judged on the friction-log delta, not screen count |
| D7 | Uncertainty type | Cutler/Hubbard | Reducible-by-research vs irreducible — research only the former, decide through the latter | Stop researching when the next decision unblocks (M9) |
| D8 | Deference test | Apple HIG | Does the UI compete with the content? Strip pass (M6) | The content is the *decision and its proof*; chrome that competes is cut |
| D9 | Big hire vs little hire | JTBD (Moesta) | Signup (once) vs actual use (repeated) — retention failures are little-hire failures | Onboarding is the big hire; the first real 3 AM page is the little hire — design for the second |
| D10 | Opinionated vs flexible | Linear | A strong point of view vs configurable mediocrity | One page-first opinion, written down; no "customize your dashboard" escape hatch |

---

## §3. How they think — the mental moves

- **T1. Inversion (the premortem move).** Ask what would make this screen *fail* before asking what makes it
  good. Zhuo's red-flag list is an inversion checklist: is this designer-centered? delight-before-utility?
  edge-case-first? (Lineage: Klein's premortem; principal-mindset §6.)
- **T2. Constraint-first.** Shape Up's appetite; Refactoring UI's constrained scales. Constraints are not the
  enemy of creativity — they are the instrument. The redesign's constraints: zero Jev calls on read paths,
  pages-first, proof-always-attached.
- **T3. Subtraction.** Apple's strip pass; Linear's anti-bloat. The first redesign move is *removal*, and every
  restored element carries a written justification.
- **T4. Prototype-to-think.** Field's option-space exploration. Sketches are thinking tools, not deliverables —
  "design in grayscale first" is a thinking constraint, not a style.
- **T5. Second-order effects.** Stripe's "think rigorously": what does this screen teach the operator to
  *expect*? A suppression shown without proof teaches "the machine hides things." Every surface has a
  curriculum.
- **T6. The thermostat test.** Norman's canonical failure: users crank the thermostat to 90° believing higher =
  faster. For every control, ask: *what wrong mental model will a tired operator form?* The kill switch's wrong
  model ("flipping it pages everyone instantly") must be corrected by the screen itself.
- **T7. Walk the store.** Katie Dill's method as a mental habit: before defending a screen, *use* it as the
  3 AM operator. The designer who won't walk their own store doesn't get a vote.
- **T8. Assume you're missing something.** Field's learning stance. When Aditya says "worst I've ever seen,"
  the principal move is not defense — it is the assumption that he is seeing something the team is blind to,
  followed by the friction log that finds it.

---

## §4. PRESCRIPTION — the decision process Sentinel's interface team will follow

This is the process. Skipping a step is a process violation, not a shortcut.

### Phase 0 — Name the main thing (Linear, M8)
Write one sentence: the single thing the Sentinel console must be great at. Proposed: *"At 3 AM, the operator
knows exactly what the machine decided, why, and what to do next — in under 30 seconds."* Disagree in writing
if you disagree; do not proceed on vibes.

### Phase 1 — Job statements (Christensen, M2)
For the three on-call moments (paged / triaging a storm / calm review), write JTBD statements in circumstance
language, all three dimensions each. No screen may be drawn until its job statement exists and survives a
challenge round.

### Phase 2 — Conceptual models (Norman, M1)
For each critical surface (kill switch, suppression proof, policy governance, race monitor, degraded mode):
the design model (how it actually works, plain sentences) and the predicted user model (what the operator will
believe from the screen). Where they can diverge, name the bridge. The thermostat test (T6) per control.

### Phase 3 — Friction log of the current UI (Stripe, M3)
The 15 essential journeys, walked on the live staging site, scored red→green, in writing. This is the baseline
the redesign must beat. No new pixels until the log exists.

### Phase 4 — Appetite and shaped bets (Shape Up, M7)
The redesign is split into beats by appetite (fixed time, variable scope). Each beat: problem, appetite,
rough sketch, open risks. Open questions are shaped (researched) *before* the beat starts, not during.

### Phase 5 — Feature-first, grayscale flows (Refactoring UI, M5)
Draw the page moment before the navigation. Every screen passes the grayscale test: with color removed, the
eye lands on the most important thing first. Hierarchy tiers (D3) assigned before any color is chosen.

### Phase 6 — Strip pass (Apple, M6)
Remove every element; restore only with written justification. "Defy precedent" — do not copy PagerDuty's
layout where our job differs.

### Phase 7 — Opinionated decision log (Linear, M8)
Every major decision recorded with the position taken, the alternatives rejected, and the dissent. "Decisively
wrong and pivot" is permitted; "cautiously mediocre" is not.

### Phase 8 — Measure to decide (Cutler, M9)
Research ends when the next decision unblocks. Every research artifact ends with "the decisions this unblocks."

### Falsifiers — "if we did X, we'd be doing theater"
- **F1.** If a screen exists without a job statement → theater (M2 violated).
- **F2.** If the grayscale version doesn't communicate hierarchy → theater (M5 violated).
- **F3.** If the friction log was skipped and we went straight to mockups → theater (M3 violated).
- **F4.** If we can't name the wrong mental model a control invites → theater (T6 violated).
- **F5.** If "the user can customize it" is the answer to a hierarchy dispute → theater (M8 violated — Linear's
  anti-bloat).
- **F6.** If a design review critiques a deck instead of the working prototype → theater (M3 "minimize
  presentationing" violated).
- **F7.** If research continues past the point where the next decision is unblocked → theater (M9 violated).
- **F8.** If the redesign ships more screens but the friction-log score doesn't move → failure by our own
  measure (D6 violated).

---

## Sources (as consulted)

- Norman, D. — *The Design of Everyday Things*, rev. ed. (2013), ch. 1. Via search summaries + the
  designinshort.com explainer: https://designinshort.com/sources/the-design-of-everyday-things/on-the-communication-of-conceptual-models-through-design
- Christensen, C. — *Competing Against Luck* (2016); HBR (2016). Via HBS Online explainer:
  https://online.hbs.edu/blog/post/jobs-to-be-done-examples — and glasp.co summary: https://glasp.co/articles/jobs-to-be-done
- Dill, K. (Stripe) — via Lenny's Newsletter "Building beautiful products" and the Creator Economy writeup
  (search summaries; primary interview not read end-to-end).
- Zhuo, J. — *The Making of a Manager* (2019); juliezhuo.com essays. Via persona summary:
  https://github.com/beam-gtm/beam-next-personas/blob/HEAD/personas/julie-zhuo.md (secondary).
- Cutler, J. — "The Beautiful Mess" newsletter; "12 Signs You're Working in a Feature Factory." Via:
  https://insightdigest.net/john-cutler-high-performing-product-teams/ and https://www.mindtheproduct.com/the-beautiful-mess-by-john-cutler/
- Wathan, A. & Schoger, S. — *Refactoring UI* (2018). Via distilled references:
  https://github.com/exodes/skills-workspace/blob/HEAD/refactoring-ui/references/design-process.md
- Apple — Human Interface Guidelines, "Designing for iOS 7" (deference/clarity/depth). Via:
  https://forums.macrumors.com/threads/apple-publishes-its-ios-human-interface-guidelines-on-ibooks.1734394/
- Singer, R. — *Shape Up* (Basecamp, 2019). Via: https://github.com/gethamster/skills/blob/HEAD/methods/shape-up/METHOD.md
- Yu, N. (Linear) — Lenny's Podcast (Jan 2025). Via: https://www.antoinebuteau.com/lessons-from-nan-yu-head-of-product-at-linear/
- Field, D. (Figma) — Lenny's Podcast; Stratechery (2025–2026). Via:
  https://github.com/hczhu/stock-research/blob/HEAD/memos/2026-06-25-figma-dylan-field-stratechery-design-ai.md
- PagerDuty operational culture — via: https://medium.com/@code.chandrashekhar/pagerduty-deep-dive-complete-guide-with-real-time-practical-approach-81010d0b579f
  (secondary; no first-party design blog found — noted as a gap).
