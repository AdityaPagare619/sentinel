# SWE Discipline at Elite Companies — Research Brief
**Phase-1, Stream 1 · Architecture Revision Program · Sentinel**
Date: 2026-10-05 IST · Author: Stream-1 researcher (subagent) · Branch: `swe-discipline`

> Scope: how elite orgs do RFC/design-doc culture, code review, requirements
> engineering, and testing/blameless-postmortem culture — the MECHANISMS, not the
> slogans — plus an anti-patterns list and concrete Relevance-to-Sentinel gaps.
> Prior related repo notes checked (no duplication): `research/sre-field.md`,
> `research/synthesis-2026-10-02.md`, `research/domain-validation-2026-10-04.md`,
> `docs/adr-decisions-2026-10-03.md`, `docs/rfc-retention-2026-10-05.md`,
> `ops/RUNBOOKS.md`, `ops/COMPLETENESS_AUDIT.md`, METHODOLOGY.md §8 (principal
> skills wired in). This brief builds on those, it does not repeat them.

---

## 1. RFC / design-doc culture: what makes a doc gate work vs theater

### 1a. Amazon: six-page narratives and the study hall

**Mechanism, not slogan.** In June 2004 Bezos banned PowerPoint from S-team
meetings: proposals arrive as six-page narrative memos in complete sentences,
and every meeting opens with a ~20–30 minute *silent "study hall"* read before
anyone speaks (Bezos, quoted in the amp.scroll.in piece; Banking Exchange's
2016-letter writeup). Three load-bearing elements make the memo do what a deck
can't (Mary Beth Hazeldine, "What Amazon's Six-Page Memo Gets Right That Slide
Decks Miss," Medium, 2026-09-16):

1. **Full sentences, not fragments.** The document must stand alone with no
   presenter narrating. "If a paragraph only makes sense once someone explains
   what they meant, it isn't finished."
2. **An explicit risk paragraph, written by the person making the
   recommendation** — "the strongest reason this could go wrong," in the
   writer's own words, *before* anyone else finds it. "Writing your own weakest
   point, before anyone else finds it, changes how carefully you think about
   the strength of everything else."
3. **A timed silent read, before anyone speaks.** "Reading replaces
   performance. A confident delivery can carry a weak argument past a room
   that's listening to a voice. It can't carry a weak argument past a room
   that's reading the words on their own."

The diagnostic test: after the read, if the first questions are specific —
pointing at a named number, a dated assumption, a line in the risk paragraph —
the document held up. If the first question is some version of "wait, what's
the actual ask here," the *argument* failed, and "the deck version would have
hidden that failure behind a confident presenter for another two weeks."

**Why narrative beats bullets, per Bezos:** "the narrative structure of a good
memo forces better thought and better understanding of what's more important
than what, and how things are related" — full sentences require a subject, a
verb, and a claim that connects to the sentence before it; a fragment can skip
the hardest part of an argument (same Medium piece; phrasing also echoed in
Working Backwards practitioners' dossiers, e.g. the decision-brief dossier at
github.com/product-on-purpose/thinking-framework-skills).

**The gate's kill function.** Working Backwards practitioners are explicit:
*most* PR/FAQs are (and should be) rejected — "that's the point" (marcos-sponton
working-backwards SKILL). AWS spent **2+ years in PR/FAQ before the 2006
S3/EC2 launch** — the document is a decision gate with a real rejection rate,
not a writing exercise (Bryar & Carr, *Working Backwards*, 2021, via the same
sources).

### 1b. Google: design docs as "code review before code"

**Mechanism.** Per *Software Engineering at Google*, ch. 10 (Documentation):
"Most teams at Google require an approved design document before starting work
on any major project." The canonical design-doc template **forces** engineers
to consider security, internationalization, storage, privacy, etc., and those
sections "are reviewed by experts in those domains" — i.e., the doc *routes*
cross-cutting concerns to named experts before code exists. A good doc covers
goals, implementation strategy, and **key design decisions with their
trade-offs, plus alternative designs with strong and weak points**
(github.com/dayuanjiang/software-engineering-at-google, ch. 10 —
community mirror of the published book).

Two further mechanisms from the same chapter: (1) the approved doc becomes
**a historical record and a yardstick** — teams review it before launch to
check "that the stated goals when the design document was written remain the
stated goals at launch (and if they do not, either the document or the product
can be adjusted accordingly)"; (2) docs live in Google Docs with real-time
collaboration — the review happens as comment threads, not a meeting
presentation.

**The key insight (Hacker News thread on "Design Docs at Google"):** treat
design docs "not [as] an artifact produced by a project but as a **communication
mechanism of a team**." The primary value isn't issue-discovery per se — it's
that review happens *relatively early in the development lifecycle when it is
still relatively cheap to make changes*, and that cross-cutting concerns
(observability, security, privacy) get incorporated by the organization's
combined experience (news.ycombinator.com/item?id=23915521). The socialization
is the mechanism: share with 1–2 reviewers, then the team; formal review
meetings exist but engineers are warned not to block progress on wide review —
get the crucial feedback directly.

### 1c. Stripe: writing quality is a career gate

**Mechanism.** Reported Stripe norms (Alexandre Zajac's "Stripe's 10
Non-Negotiable Engineering Rules," LinkedIn — secondary, treat as reported
practice): **20-page design docs are standard; doc quality affects promotions**;
Stripe built its own internal doc framework; and — the enforcement teeth — a
**dedicated cross-functional governance team reviews every public API change**
for consistency, downstream impact, and backward compatibility (not a normal
code review). "Never deprecate a public API" without CEO approval. Corroborated
on the API-design side: Stripe maintains a ~20-page internal API design
document every new endpoint must follow (dev.to/preecha, 2026-10). On the
writing-as-thinking side: a former Stripe engineer's HN account notes projects
in the Atlas team were "considered done when a Shipped email was sent" — and
**the Shipped email was drafted (not sent) at project start**, "serving as a
north star for understanding the scope and goals" (news.ycombinator.com,
item 34968371).

**Synthesis — what makes a design doc gate REAL (not theater):**
- A **template that forces** cross-cutting sections (security/privacy/storage/i18n)
  routed to **named domain experts** (Google).
- A **silent, unmediated read** so no presenter can smooth over gaps (Amazon).
- An **author-written risk paragraph** (Amazon) or explicit **rejected
  alternatives with trade-offs** (Google).
- A **measurable kill rate** — most docs should die at this stage (Amazon PR/FAQ).
- **Career consequences** tied to doc quality, and **API-governance teams** for
  contract surfaces (Stripe).
- A **goal-fidelity check at launch** — compare launch against the doc's stated
  goals (Google).

**Sources §1:** Medium silent-read piece —
https://medium.com/@marybeth.hazeldine/what-amazons-six-page-memo-gets-right-that-slide-decks-miss-adaf141eb1b7
(2026-09-16); Amazon meeting culture —
https://amp.scroll.in/article/877760/watch-why-ceo-jeff-bezos-doesn-t-allow-powerpoint-presentations-at-amazon-meetings;
Bezos 2016-letter handstand memo —
https://www.bankingexchange.com/sections/reporter-s-notebook/item/7527-handstands-ans-six-page-memos-from-amazon?Itemid=730;
SWE Book ch.10 —
https://github.com/dayuanjiang/software-engineering-at-google/blob/HEAD/en/Chapter-10_Documentation/Chapter-10_Documentation.md;
HN design-docs thread — https://news.ycombinator.com/item?id=23915521;
Amazon mechanisms ("Good intentions don't work. Mechanisms do") —
https://github.com/marcos-sponton/frameworks-as-skills/blob/HEAD/skills/working-backwards/SKILL.md;
PR/FAQ mechanics —
https://github.com/zahaale20/elite-ball-knowledge/blob/HEAD/companies/08-amazon-mechanisms-customer-obsession.md;
Stripe engineering rules —
https://www.linkedin.com/posts/alexandre-zajac_stripes-10-non-negotiable-engineering-rules-activity-7455278265713315840-RpZm
(secondary); Stripe API design doc —
https://dev.to/preecha/why-stripes-api-is-the-gold-standard-design-patterns-that-every-api-builder-should-steal-ddi
(2026-10); Stripe Shipped-email-first — https://news.ycombinator.com/item?id=34968371.

### Relevance to Sentinel — design-doc gaps
- Sentinel HAS decision-recording (ADRs, `ops/decision_log.md` written at
  decision time, one panel-reviewed RFC — `docs/rfc-retention-2026-10-05.md` —
  with position memos, Type-1 classification, disagree-and-commit). These are
  excellent but mostly **post-decision records**; the retention RFC is the only
  pre-build gating doc with a panel.
- MISSING: (a) a **design-doc template** that forces cross-cutting sections and
  routes them to named domain experts (the repo's principals act as reviewers
  informally, but no doc lists *who reviews what* — the Linux-style ownership
  map the principal-governance skill calls for); (b) a **rule that no lane
  starts building without an approved doc** (wave lanes build from briefs);
  (c) a **goal-fidelity check at lane close** — compare what shipped against the
  doc's stated goals; (d) a **recorded kill rate** for proposed docs.
- Evidence the gate is absent: PR #65 merged a `cryptography` import that
  violated the frozen stdlib-only decision — caught post-merge by Petu, not by
  review (see AGENTS.md lesson "Verify the artifact"). A Google-style
  design-doc gate with a canonical security section reviewed by the security
  principal *before* the lane built would have forced the decision to be amended
  or the code rejected.

---

## 2. Code review bars: what elite review actually catches

### 2a. Google's published bar (eng-practices)

**The standard:** "Favor approving a CL once it is in a state where it
**definitely improves the overall code health of the system**, even if the CL
isn't perfect" — "there is no such thing as 'perfect' code — there is only
better code." Reviewers who demand perfection teach authors to batch changes
into unreviewable lumps; reviewers who rubber-stamp let health decay one
approval at a time. Personal preference without technical basis defers to the
author. (https://google.github.io/eng-practices/review/reviewer/standard.html —
URLs as cited verbatim across multiple practitioner mirrors, e.g.
github.com/bigmoon-dev/dianoia-ai corpus and github.com/jackreichert/mithril
theme notes.)

**Priority order (explicit):** Design → Functionality → Complexity → Tests →
Naming → Comments → Style → Consistency → Documentation — then every line,
surrounding context, and positive feedback. Design is "the most important
thing to cover in a review": does the change belong here, does it integrate
well? Functionality: "does this CL do what the developer intended? Is what
the developer intended good for the users?" Complexity: **vigilance against
over-engineering** — solve the problem known now, not speculative future
problems. Tests: "unit, integration, or end-to-end tests as appropriate…
**with tests generally added in the same CL as the production code**." Style is
delegated to linters — humans never argue about tabs.
(https://google.github.io/eng-practices/review/reviewer/looking-for.html)

**Process mechanics** (Google's policy, per UMich lecture notes on Google's
process): **all CLs must be reviewed, period**; every directory has owners and
**at least one reviewer or the author must be an owner**; authors preview
static-analyzer results *before* requesting review; **unresolved comments must
be addressed** (enforced by hooks); one business day is the max acceptable
first-response time; reviewers should ask authors to **split oversized CLs**
rather than reviewing them whole; there is a harassing-reminder process for
slow reviewers
(https://web.eecs.umich.edu/~weimerw/2022-481F/lectures/se-07-codereview.pdf).

**Scale numbers** (Sadowski, Söderberg, Church, Sipko, Bacchelli, "Modern Code
Review: A Case Study at Google," ICSE-SEIP 2018): ~9M reviewed changes,
25,000+ authors/reviewers (Jan 2014–Jul 2016) — **median 24 lines changed**,
~90% of changes touch <10 files, median reviewer count **1**, median
submission→acceptance **<4 hours**, median 4 reviews/developer/week, ~3
hours/week spent reviewing. Small changes are the mechanism that makes review
fast and real — the median CL is a 24-line one-reviewer affair, not a
multi-day tribunal (via slides summarizing the paper:
https://mcoblenz.github.io/CSE210/assets/slides/12-code-review.pdf; median-time
figure also cited in Springer Empirical SE, 2025:
https://link.springer.com/article/10.1007/s10664-025-10791-2).

### 2b. Readability: Google's certified second gate

**Mechanism** (Michael Lynch-style account of a Google readability mentor,
2023-07-03, https://www.moderndescartes.com/essays/readability/): every change
needs an approval from a maintainer **of that corner of the codebase** AND an
approval from somebody who "has readability" in the language — certification
that you know "the language's ins and outs, design patterns, ecosystem of
libraries, and idiomatic usage" well enough to catch language-usage issues.
Satisfied if author *or* any reviewer has it. To earn it you submit your own
code to a **randomly drawn** readability mentor for fine-tooth review; after
enough "good" code you're certified. ~⅓–½ of Googlers hold it in their primary
language. The defining (and hard) feature is **programmatic enforcement** —
GitHub-style protected branches can't express "Python readability."

Decay modes the author names honestly: mentors who act as "human code
linters," teams grinding to a crawl when readability-holders are on vacation.
His verdict: replicate the **mentorship program** (consensus bar + mentoring +
non-blocking encouragement), not the hard gate — unless you're safety-critical.

### 2c. What reviews actually catch (research, not vibes)

The honest numbers cut against the folk theory that review = bug hunting:

- **Bacchelli & Bird (2013), Microsoft:** observed 17 developers, interviewed
  each, surveyed 873 programmers + 165 managers. "Finding defects" was the #1
  *stated* motivation (44% of programmers). But classifying 570 review
  comments: **only 14% were defects**; 29% were code improvement, ~22% were
  understanding questions/doubts, ~15% social communication. Reviews are
  primarily a **design-discussion and knowledge-transfer** instrument that
  happens to catch bugs (summarized in
  https://blog.codeminer42.com/six-weeks-of-ai-code-review-what-it-caught-and-what-it-let-through/ —
  2026, citing the paper).
- **Mäntylä & Lassenius (2009):** **75% of defects found in review do not
  visibly affect software functionality** — they are *evolvability* defects
  (organization, naming, structure) that matter for long-lifecycle code (same
  summary source).
- Dorner et al. (2025): information spreads to up to **85% of participating
  developers after an average of only 3 code reviews** — review is the org's
  fastest knowledge-distribution channel (cited in
  https://link.springer.com/article/10.1007/s10664-025-10791-2).

**Synthesis — what elite review is FOR:** (1) a design sanity check *late
enough to be concrete, early enough to be cheap*; (2) evolvability/maintainability
defects — the 75% that keep a codebase alive for a decade; (3) knowledge
transfer to reviewers; (4) a small-CL, fast-response, owner-approved loop
(<4h, 1 reviewer, 24 lines at Google). Bug-finding is a minority output; the
process is justified by the other three.

**Sources §2:** Google reviewer guide —
https://google.github.io/eng-practices/review/reviewer/looking-for.html and
https://google.github.io/eng-practices/review/reviewer/standard.html (via
mirrors at github.com/bigmoon-dev/dianoia-ai and github.com/jackreichert/mithril);
Google process lecture —
https://web.eecs.umich.edu/~weimerw/2022-481F/lectures/se-07-codereview.pdf;
Readability —
https://www.moderndescartes.com/essays/readability/ (2023-07-03); review-outcome
research —
https://blog.codeminer42.com/six-weeks-of-ai-code-review-what-it-caught-and-what-it-let-through/
(2026, summarizing Bacchelli & Bird 2013); Sadowski et al. ICSE-SEIP 2018 via
https://mcoblenz.github.io/CSE210/assets/slides/12-code-review.pdf and
https://link.springer.com/article/10.1007/s10664-025-10791-2 (2025).

### Relevance to Sentinel — review-bar gaps
- Sentinel's PR template (`.github/pull_request_template.md`) is a strong
  **author-side** Definition of Done (full suite green, kill-the-client test,
  docs updated, PROGRESS.md entry, demo-able, no secrets, Claim-Auditor pass).
- MISSING is everything **reviewer-side**: (a) no published reviewer bar
  (Google's priority order: design → functionality → complexity → tests →
  naming → comments → style; "approve when it definitely improves code
  health"; defer to author on taste); (b) no **per-directory owners**
  (OWNERS/CODEOWNERS) — nothing that makes "at least one reviewer or the
  author must be an owner" enforceable; (c) no **readability-equivalent bar**
  (who is certified to judge Python-idiom/security-sensitive code?); (d) no
  **small-CL norm** or first-response SLA — lanes land large PRs reviewed
  hours later by whoever is free; (e) no "call out good things" norm (Google
  explicitly instructs it).
- The PR #65 case is the exhibit: a stdlib-only violation merged because the
  review checked *what the code did*, not *what constraints the code broke*.
  Bacchelli & Bird's lesson applies: review's real job here would have been
  the *evolvability/contract* check (does this change violate a frozen
  architectural decision?), not the functional check.

---

## 3. Requirements engineering: written BEFORE code

### 3a. IEEE 830 lineage — what "requirements" concretely means

IEEE Std 830-1998 (Recommended Practice for Software Requirements
Specifications; superseded by ISO/IEC/IEEE 29148:2011) defines the SRS as a
specification that is **jointly prepared by supplier and customer reps**
(subclause 4.4 recommends both) and answers: (a) **Functionality** — what is
the software supposed to do? (b) **External interfaces** — how it interacts
with people, hardware, other software; (c) **Performance** — speed,
availability, response time, recovery time; (d) **Attributes** — portability,
correctness, maintainability, security; (e) **Design constraints** — standards,
languages, resource limits, operating environments (via
http://www.sweetstudy.com/files/ieee830recommendedpracticeforsrs1-pdf —
copy of the standard text).

The canonical structure (§3): 1. Introduction → 2. Overall Description →
**3. Specific Requirements** (3.1 external interfaces, 3.2 functions, 3.3
performance, 3.4 logical DB, 3.5 design constraints, 3.6 quality attributes) →
4. Appendices. Eight quality gates for a *good* SRS: **Correct, Unambiguous,
Complete, Consistent, Ranked for importance, Verifiable, Modifiable,
Traceable** — where **verifiable** means "for each requirement there is a
finite cost-effective process by which a person or machine can check that the
product meets it," and **traceable** means the origin of each requirement is
clear and it is uniquely identifiable + cross-referenced to its source (via
http://www.slideserve.com/kamuzu/software-requirements-specification-srs —
slide summary of the standard).

Two structural rules that matter: (1) the SRS must state **every input, every
output, and all functions performed in response to an input or in support of
an output**; (2) the SRS is simultaneously **"an input to the design
specification" and "a product validation check"** — i.e., requirements →
architecture is a designed handoff, and requirements → acceptance testing is
the other designed handoff (same source). Modern restatement (Avenga, 2026):
"An SRS is not only for waterfall. Agile teams maintain requirement
specifications alongside user stories, acceptance criteria, prototypes, and
backlog items" — and requirements "should describe the need, not prematurely
dictate the design" (https://www.avenga.com/magazine/software-requirement-specification/ —
2026).

### 3b. Amazon Working Backwards PR/FAQ — the customer-first requirements doc

**Mechanism** (collected from Bryar & Carr-derived sources, because Amazon's
internal template isn't public): the author writes a **one-page mock press
release as if the product already shipped** — headline (benefit in the
customer's words), sub-headline (who it's for), problem paragraph, solution
paragraph, leader quote, customer quote, call to action — then a **2–5 page
FAQ**: customer FAQs (price, migration, failure behavior, differentiation) and
**internal FAQs answered honestly as a thinking tool** (technical feasibility,
cost, top-3 risks + mitigations, dependencies, "plan if this doesn't get
traction in 90 days")
(https://github.com/namht1st/prepkit-product/blob/HEAD/skills/product-validation/references/working-backwards-prfaq-template.md).

The forcing function: "If you cannot write a compelling headline, the product
is probably not worth building. The FAQ then forces you to confront the
assumptions you would otherwise discover six months and a million dollars
later" (https://github.com/zahaale20/elite-ball-knowledge/blob/HEAD/companies/08-amazon-mechanisms-customer-obsession.md).
Bezos's meta-principle, quoted by Carr repeatedly: **"Good intentions don't
work. Mechanisms do."**
(https://github.com/marcos-sponton/frameworks-as-skills/blob/HEAD/skills/working-backwards/SKILL.md).
The PR/FAQ is only the front half; behind it sit the **single-threaded leader**
(one leader, one initiative, zero competing responsibilities) and **input
metrics** (controllable inputs reviewed weekly, not outputs) — the execution
mechanisms that keep the requirements honest after writing (same source).

### 3c. User stories at scale — the agile form of requirements

**Mechanism** (Scrum.org, "Are User Stories Requirements?",
http://www.scrum.org/resources/blog/are-user-stories-requirements): a user
story is three things, not one — the **3 Cs**: **Card** ("As a [role], I want
[goal], so that [benefit]"), **Conversation** (the dialogue that builds shared
understanding — "Which credit cards? Do we store them? Buy-now-pay-later?"),
and **Confirmation** — the acceptance criteria that define when the story is
complete and **drive the creation of acceptance tests**. Quality bar: Bill
Wake's **INVEST** — Independent, Negotiable, Valuable, Estimable, Small,
Testable — plus a **Definition of Ready** (clear why, Given/When/Then
acceptance criteria, known dependencies, no estimation-blocking open
questions) gating sprint entry and a **Definition of Done** gating acceptance
(same source; practitioner synthesis at
https://github.com/omar-kabeer/business-analysis-pro/blob/HEAD/plugin/skills/product-owner/SKILL.md).

**How requirements flow into architecture (the answer to "concretely"):** in
the classical chain, requirements are uniquely identified, ranked, and traced
to sources (IEEE 830), then handed to designers as the input spec and to
testers as the validation check; in the Amazon chain, the PR/FAQ's internal
FAQs become the design doc's constraints (feasibility, risks, dependencies,
economics), owned by a single-threaded leader against input metrics; in the
agile chain, epics → INVEST stories → Given/When/Then acceptance criteria →
acceptance tests, each link preserving the trace back to the outcome the story
serves. Every form shares one property: **the requirement names a checkable
customer-visible behavior BEFORE the design says how.**

**Sources §3:** IEEE 830 content —
http://www.sweetstudy.com/files/ieee830recommendedpracticeforsrs1-pdf (standard
text copy); SRS characteristics —
http://www.slideserve.com/kamuzu/software-requirements-specification-srs;
modern SRS practice — https://www.avenga.com/magazine/software-requirement-specification/
(2026); PR/FAQ template —
https://github.com/namht1st/prepkit-product/blob/HEAD/skills/product-validation/references/working-backwards-prfaq-template.md;
PR/FAQ mechanics —
https://github.com/zahaale20/elite-ball-knowledge/blob/HEAD/companies/08-amazon-mechanisms-customer-obsession.md;
Working Backwards mechanisms (STL, input metrics, "Mechanisms do") —
https://github.com/marcos-sponton/frameworks-as-skills/blob/HEAD/skills/working-backwards/SKILL.md;
3 Cs / INVEST — http://www.scrum.org/resources/blog/are-user-stories-requirements.

### Relevance to Sentinel — the requirements gap (BIGGEST)
- Sentinel has: a CHARTER (pain quantified: 95–98% noncritical noise,
  $8,388/yr PagerDuty AIOps, 45-min-per-false-page cost), five non-negotiable
  design laws, a constraint registry (hard, verified constraints), and ADRs.
  These are *parts* of a requirements doc — but **no single document says, per
  component/release: what the customer must observe, how well, and how we will
  verify it — written before the lane builds.**
- IEEE 830's test fails on all eight gates for anything in the repo: no
  requirement is **uniquely identifiable** (REQ-xxx numbering doesn't exist),
  **ranked** (no essential-vs-desirable), **verifiable** (no per-requirement
  check method named before implementation), or **traceable** (no
  requirement → design → test linkage). The constraint registry is the
  closest artifact, and it covers *constraints on implementation*, not
  *customer-visible behaviors with acceptance checks*.
- There is no PR/FAQ: no one-page launch narrative in the customer's words
  (headline, problem, solution, "how it works" from the customer's
  perspective). The CHARTER's mission paragraph is founder-voice, not
  customer-voice; "demo-able" in the PR DoD is a demo script, not a customer
  acceptance bar.
- There are no INVEST stories with Given/When/Then acceptance criteria feeding
  acceptance tests; lanes receive wave briefs, which are work plans, not
  requirements.
- Concrete missing artifacts: (1) a **release requirements doc** (PR/FAQ
  shape for the product, IEEE-830-shaped per-component requirements:
  REQ-ID, statement, rank, verification method, source); (2) a **traceability
  matrix** REQ → ARCHITECTURE.md section → test file; (3) a **Definition of
  Ready** for lanes ("no lane starts without named acceptance criteria") —
  which is also the mechanism that would have caught the
...[truncated 11872 chars]