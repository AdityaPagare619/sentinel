# 2026-10-04 — Fresh-Minds Review: Does the Proof-Engine Thesis Still Hold?

**LANE 4 (fresh-minds research) · Sunday wave · 2026-10-04**
**Question:** Re-derive Sentinel's thesis with fresh eyes after the implementation
wave. Does "proof engine first, suppression engine second" still hold? What is
the strongest case against it? What did the principal waves never question?
**Status:** research note. Proposes no ADRs directly; flags candidates.
**Method:** four principal skills applied as lenses
(`principal-systems`: five whys, Chesterton's fence, pre-mortem;
`execution-doctrine`: the YC hierarchy pain > distribution > idea, applied
ruthlessly; `principal-governance`: contract/API discipline;
`principal-mindset`: proxy traps, restatement test, monkey-first).
All web-fetched facts carry source + access date per `research/CHARTER.md`;
repo-internal facts carry path + branch/commit + date.

---

## 1. OBSERVED FACTS

**F1.** The thesis under review, verbatim: "Sentinel is a proof engine first and a
suppression engine second" (`design/principal/11-synthesis.md` §4, branch
`lane/principal-fix-designs`, 2026-10-03; restated as the demo's thesis in
`design/fixes/08-proof-demo-narrative.md`: "the demo is a designed narrative of
trust formation").

**F2.** The implementation wave merged 8/8 fix lanes on `origin/main`
(through `2afbf54`, 2026-10-03 ~20:00 IST): append-only event log
(`decision_requested`/`decision_made`/`forward_confirmed|forward_failed`/
`flip_observed`), race-to-page with budget **B=2700ms**, quantized gate
(suppress requires reported P(p1)=0.00 AND per-org calibration fit's
p̂_upper < 0.002, else dual human attestation), freshness proofs, durable
forwarder, liveness receiver contract, shadow-tap route with fail-closed OG
auth. 473/473 tests green on PR heads; main verified 426/426.
(Source: `ops/SENTINEL_LOG.md`, 2026-10-03 entries; `git log origin/main`.)

**F3.** The Oracle N=100 latency campaign (real TypeSafe key, 2026-10-03,
`research/jev-behavior/latency-report-2026-10-03.md`): p50 **816ms**, p99
**1526ms**, min 494ms. The vendor's 70–500ms claim is **not supported** on the
measured path. The single prior 11.4s data point did **not** reproduce
(slowest observed: 1678ms). B=2700ms was re-derived as 2× measured healthy
p99 — the seeded B=1000ms was proven too tight by measurement, and the
re-derivation protocol worked as designed.

**F4.** The demo narrative (`08-proof-demo-narrative.md`) opens with an
unskippable contract screen admitting three limits — synthetic data, recorded
storm, mock engine, nothing pages anyone — and shows the machine being wrong
first (a designed flip at minute 6). It names its own adversary explicitly:
"the demo's own persuasiveness."

**F5.** Market base rate (`~/workspace/jev-product-research/JEV_RESEARCH_REPORT.md`,
§4.2, research window 2026-10-02): **nobody has paying users** for a Jev-native
product. jevmod (177 commits, multi-tenant moderation API, Prometheus metrics,
fail-open) and Sutura (placebo-controlled CI benchmark, 18/19 traps caught)
have **1 star each**. Inbox Zero (12.3k stars, real SaaS) merged Jev routing
but **kept multi-rule selection on the LLM path** because "expanded evals
showed JEV could not reliably separate overlapping secondary rules from
explicit negative instructions" — the single most honest production data
point in the ecosystem.

**F6.** The quantified pain the thesis rests on (JEV report §5.2, S4 field
study): 95–98% of alerts noncritical; each false page costs ~45 min of sleep
+ 2–3h daytime debugging; SRE toil back to ~30% of working time; practitioner
account: 18 nightly pages → 3 after AI triage; 62% of "high-severity" pages
were mid-tier Redis spikes clearing in <30s. Pricing umbrella: PagerDuty
AIOps $699–799/mo add-on, never bundled; BigPanda ~$200k/yr typical
(`research/competitive/2026-10-02-incumbent-gaps.md`, accessed 2026-10-02).

**F7.** The engine follows the JEV report's builder rules where checkable:
`src/sentinel/questions.py` includes a `cannot_determine` option (the report's
"always include an explicit cannot-determine option" rule, §3.4); the math-in-code
boundary is registered Type 1 (deterministic code does math/counting/thresholds;
Jev answers contextual judgment only — synthesis §1).

**F8.** The per-org calibration fit — the mechanism that makes the quantized
gate's probability leg implementable — **does not exist for any real org**.
Zero org-weeks of labels. The JEV report's standing requirement: "you must fit
thresholds on 50–300 of your own labels per deployment" (§1); "fine-tuned
encoders beat Jev everywhere once you have 200–500 labels" (§3.1).

**F9.** No repo artifact names design partner #1 (by name), the outreach
channel, the outreach owner, or the demo's invited audience. `design/screens/
onboarding.md` assumes arrival ("A design partner goes from 'I've heard of
Sentinel' to … in fifteen minutes"). `ops/constraint_registry.md` defines the
Sunday platform as "a working interactive platform a design partner can click
through and run" — the partner is an abstract noun throughout.
(Grep over `research/`, `design/`, `ops/`, 2026-10-04.)

**F10.** TypeSafe AI counterparty facts (JEV report §3.2/§3.7, verified
2026-10-02): 11 days post-GA at research time; no verifiable SOC 2; no SLA;
dynamic rate limits with a 529 shed-load code; HTTP 402 outage on 2026-09-19
(jev-search built Vercel/Cloudflare fallback by hand); MCA forbids
distillation; liability capped at max(12 months' fees, $50). Within 14 days of
launch: Ollama 0.35 shipped Jev-compatible models; OpenAI previewed a
Decisions API.

**F11.** The shadow-tap failure semantics were deliberately separated from the
paging path (PR #26): own `/shadow/` route, fail-closed OG auth. Shadow mode
is read-only by construction — "your paging path is byte-identical."

**F12.** Sunday wave scope (per coordinator brief, 2026-10-03 20:15 IST):
working demo platform for Sun 2026-10-04 21:00 IST — real engine, real Jev
path, no fakes; Prism UI parallel from hour one against the frozen API
contract; fault-injection drills; demo narrative + rehearsal.

---

## 2. VERDICT 1 — DOES PROOF-ENGINE-FIRST STILL HOLD AFTER THE BUILD?

**Yes — and the build strengthened it. But the thesis now has a sharper,
narrower commercial form than the slogan, and the slogan is hiding it.**

**What the build proved (for):**
- The wave did not make suppression easier; it made suppression *harder to
  earn*. The quantized gate (F2) replaced a broken point-condition bar with a
  calibration-fit requirement plus dual human attestation until the fit
  exists. A team optimizing for "suppress more pages" would have loosened the
  gate; the principal wave tightened it and spent its rigor on the *evidence
  machinery* (event log, receipts, freshness proofs). That is proof-first
  revealed by resource allocation, not rhetoric.
- The latency campaign (F3) is the thesis working as an institution: a seeded
  constant (B=1000ms) was killed by measurement and re-derived (B=2700ms)
  without drama. The race-to-page architecture — fail-open preserved around an
  816ms-median call — is the proof thesis operationalized: the engine must
  never be the reason a page is lost, and the budget is a measured quantity,
  not a hope.
- The shadow tap's separated failure semantics (F11) make the deployment path
  and the proof thesis the same object: the product's go-to-motion *is* an
  evidence-gathering motion. This is the synthesis's real insight, and the
  build implemented it rather than just writing it.

**What weakens it (against) — stated plainly:**
- **W1. The proof engine's proof is currently a demo of proof.** Every number
  in Sunday's demo traces to the event log (good), but the event log will
  contain synthetic storms and mock-engine decisions. The demo narrative's
  contract screen (F4) is honest about this — but honesty about circularity
  does not break the circularity. The calibration fit (F8), which is the load-
  bearing mechanism of the entire gate, has zero real labels behind it.
  *Inference:* until one real org completes a shadow week, "proof engine" is
  an architecture with no product — the way a beautifully designed wind tunnel
  is not aerodynamics.
- **W2. The thesis assumes a user who may not exist.** Everything downstream —
  divergence transcripts, counterfactual receipts, the SRE "hunting false
  negatives personally" — assumes judge-mode engagement. The field evidence
  (F6) says teams investigate <40% of alerts and over-alert "to be safe";
  their revealed preference is *fewer decisions*, not *more transparent
  decisions*. The demo's beat 3 (D-007 as argument transcript) is designed for
  a skeptic persona. *Inference:* the skeptic persona audits; it is not clear
  he buys. The buyer persona (burned-out on-call lead) may prefer "it stopped
  40% of my 3 AM pages" to "here is a receipt for each one." This is
  untested — see U1.
- **W3. The interim makes suppression effectively unavailable for weeks.**
  Dual attestation + nonexistent fits means: for every new org, weeks 1–2 of
  shadow produce *no live action at all*. The product's only commercial
  artifact during the entire proof phase is the weekly Shadow Report.
  *Inference:* the honest restatement of the thesis is not "proof first,
  suppression second" but **"the Shadow Report is the product; suppression is
  a feature the report unlocks."** If the Shadow Report isn't worth keeping
  the tap open for, the thesis dies at the top of the funnel — not at the
  gate. The slogan hides this; the architecture knows it (the demo narrative
  gives the report its own beat for exactly this reason).

**Net:** the thesis holds as *architecture* and as *deployment strategy*. It
does not yet hold as a *commercial claim* — that requires one real shadow
week. The gap between the two is now precisely nameable: it is the Shadow
Report's insight density (see §5, why #5).

---

## 3. VERDICT 2 — THE COUNTER-THESIS, STEELMANNED, THEN JUDGED

**The counter-thesis:** *Proof-engine-first mistakes what SREs buy. SREs don't
buy proof; they buy fewer pages and liability cover. The demo arc — contract
screen, designed flip, divergence theater — is epistemically admirable and
commercially inert: it sells to the skeptic, and the skeptic doesn't buy, he
audits. The proof requirements (dual attestation, per-org fits, Wilson
bounds) convert the 50-line middleware wedge into a multi-week calibration
project, destroying the 15-minute promise in practice. And the foundation is
weaker than it looks: 96.3% agreement on a synthetic storm is a theater
number; real evidence only arrives after adoption, which only arrives after
evidence — the proof engine has a cold-start problem the thesis redescribed
as "shadow mode" rather than solving. Therefore: flip the priority. Ship the
suppression engine on the best available priors, sell the outcome, and let
proof accumulate as a byproduct — not a prerequisite.*

**Why it stings (the valid hits):**
- The cold-start point is real and unanswered (F8, W1). "Shadow mode" is a
  deployment shape, not a solution to "who labels the first 50–300."
- The "buyers don't read receipts" point is real and untested (W2). PagerDuty
  AIOps and BigPanda sell suppression/correlation *today* with ML black boxes
  and no attestation tuples — that is the revealed market (F6). Our
  differentiation (calibrated, auditable) is on a dimension buyers have not
  demonstrated they pay for.
- The base rate is brutal (F5): the trust-wrapper-first camp has zero
  commercial validation. Both camps are at zero revenue; "proof sells" is
  untested, and untested is untested.

**Why it fails (the load-bearing wall):**
- **One false suppression that becomes a missed SEV1 is an extinction-level
  product event.** Downtime runs $300K–$5M/hr (JEV report §5.2); the SRE who
  installed the thing gets fired; the category "AI that eats pages" gets
  poisoned for everyone. The JEV report asked exactly this: *"Who gets paged
  on a Jev false negative?"* Without the proof infrastructure you cannot even
  answer the post-incident question — and the July 2025 Replit precedent (an
  agent deleting a prod database during a code freeze, then fabricating
  records) is why the market distrusts acting machines. The counter-thesis's
  "sell the suppression number" is precisely the product that dies at its
  first miss. Proof is not a philosophy here; it is the *insurance policy*
  that makes the action sellable at all.
- **It misreads the shadow phase as a delay tax.** The shadow tap (F11) is not
  deferred gratification — it is the distribution mechanism. "Point your
  webhook here, change nothing, risk nothing" is the lowest-friction
  enterprise ask imaginable, and it is only credible *because* the proof
  thesis constrains it (read-only by construction, byte-identical paging
  path). Strip the proof discipline and the shadow tap becomes "let our
  black box watch your alerts" — a harder sale, not an easier one.
- **The "theater number" charge cuts both ways.** The counter-thesis would
  have us sell suppression on priors — but our priors are *also* synthetic.
  Selling outcomes on synthetic priors with no receipts is exactly the
  vendor behavior (F10: "70–500ms" unsupported by measurement) that the whole
  campaign was built to not be.

**Judgment:** counter-thesis **rejected** as a strategy, **accepted** as a
warning label. Its two valid hits become owned risks: (a) the cold-start
label problem must be solved as a *distribution* problem, not restated as
"shadow mode" (see §4); (b) the Shadow Report must be sold and measured as
the product, with suppression as the unlock — the demo's center of gravity
should move from the suppression receipt to the report (concrete proposal in
§6, K5 mitigation).

---

## 4. WHAT WE MISSED — THE DISTRIBUTION AUTOPSY (YC HIERARCHY, RUTHLESS)

Score the hierarchy honestly (execution-doctrine §1):

- **Pain (YC #1): strong.** F6 is quantified, brutal, and multi-sourced. The
  pain thesis is the best-evidenced part of the campaign. No dispute.
- **Distribution + execution (YC #2): the weakest pillar, by far.** F9 is the
  finding: no named partner, no channel, no owner, no invited audience. The
  Sunday wave builds a demo *for* an abstract noun. Per the doctrine: "A
  brilliant product with no distribution is a ghost town." We are building the
  ghost town's most beautiful building.
- **Idea/tech (YC #3): strong but third.** The engine is real, the math is
  honest, the fail-open design is principled. It does not matter yet.

**The three brutal questions, answered in writing (execution-doctrine §6):**

1. *Who is the exact human using this every day, and what are they forced to
   use right now?* — The on-call SRE at a 20–200-person SaaS company, forced
   to use PagerDuty/Opsgenie with hand-written event rules (or nothing).
   **We have not named one.** Not a persona — a name, a company, a phone
   number. Until that exists, "design partner" is fiction.
2. *If the core idea is proven wrong next week, what discovered problem space
   keeps us working?* — The decision-trust layer: evals, threshold tuning,
   audit trails, failover (JEV report §4.5 gaps #1–3). Notably, gap #1 —
   **decision-eval & threshold-tuning as a service** ("every serious builder
   hand-rolls this; nobody sells it") — is arguably the better *distribution*
   wedge: sell evals to the ecosystem of Jev builders first, and
   Sentinel-the-product rides the relationships. The principal waves never
   considered leading with the pickaxe. *Inference:* our second product is
   already designed; it is sitting in the research report's gap list.
3. *Are we optimizing for 1,000,000 users before convincing 5 to use it
   end-to-end?* — The Sunday platform (multi-surface: river, calibration,
   tuner, audit explorer, onboarding) is built for the 1,000,000-user story.
   The 5-user story needs exactly one thing: a shadow tap and a weekly
   report someone reads. The wave should be able to say which surfaces serve
   the 5 and cut the rest from the demo (see §6, K5).

**The "why now" gap (from the five whys, §5):** there is no forcing function
in the plan — no compliance deadline, no trigger event. The real trigger is
emotional and temporal: **catch a team the week after a bad alert storm**,
when the 3 AM anger is fresh. That is a targeting insight the outreach plan
(when it exists) should encode: target post-incident teams, not happy teams.

**The unit-economics-of-onboarding gap:** every org needs 50–300 labels (F8)
plus attestation tuples with TTLs. Who does that work? At what cost in our
hours per org? Unmodeled. The YC answer ("do things that don't scale") says
hand-holding the first 10 is *correct* — but it must be the named plan, with
an owner, not a surprise discovered in week 2 of the first shadow.

**The liability gap:** TypeSafe caps at max(12mo fees, $50) (F10). Our cap
for a missed SEV1: undesigned. Shadow mode is read-only so exposure is low —
good — but the canary/suppression stage cannot begin without a commercial
liability posture. Flagged for the ADR backlog, pre-first-live-action.

---

## 5. FIVE WHYS — "WHY WILL A DESIGN PARTNER SAY YES TO THE SHADOW TAP?"

- **Why 1 — why say yes at all?** Because the ask is ~zero-risk: 50 lines,
  webhook pointed *alongside* the existing stack, paging path byte-identical,
  nothing can suppress, delay, or alter a page (F11). The cost of trying is
  one engineer-hour and no blast radius.
- **Why 2 — why is zero-risk sufficient?** Because the pain is priced and
  present (F6): 95–98% noise, ~45 min of sleep per false page, toil at ~30%
  of working time. The status quo is expensive and every SRE knows the number
  from their own body. Zero-risk trial + priced pain = the yes is cheap.
- **Why 3 — why us instead of PagerDuty AIOps?** The umbrella is documented
  ($8.4k–9.6k/yr + per-seat notification tax, no affordable tier — F6); our
  wedge is flat pricing + the auditable typed contract (calibration evidence,
  not ML black box) + the 50-line wedge (no rip-and-replace, no 12-week
  services engagement) + the counter-position (our only autonomous action is
  page a human; we do not do autonomous remediation — the Replit-shaped fear,
  named).
- **Why 4 — why now, this week, not "interesting, Q1"?** *Thin.* There is no
  structural forcing function. The honest answer is emotional timing: the
  week after a bad storm, when the team is angry at their paging. *Inference:*
  outreach targets post-incident teams; the "why now" is manufactured by
  timing, not by the product. This is the distribution plan's missing page.
- **Why 5 — why KEEP the tap open past week 1?** Because the weekly Shadow
  Report shows them something their own dashboards don't: the numbered
  divergences, the flap patterns, the calibration card with its denominator
  on it. The retention mechanic is **the report's insight density** — week 1
  must contain at least one "holy shit" divergence (the D-007 shape: "this
  SEV2 paged you at 3:12 AM and self-cleared in 38 minutes; we would have
  stood it down, here's the evidence"). If week 1's report is a dashboard of
  agreement percentages, the tap dies.

**Root cause (monkey-first for distribution):** the hardest part of this
company is not the engine — it is making the first Shadow Report undeniable.
Everything downstream (fits, suppression, $199/mo) depends on report quality
in week 1. The Sunday demo should be judged on one question: *does the
Shadow Report beat make the audience want that report about their own
fleet?* If yes, distribution has a chance. If the demo's emotional peak is
the suppression receipt instead, we optimized the wrong beat.

---

## 6. PRE-MORTEM — SUNDAY 21:00, THE DEMO FLOPPED

Assumed: it is 21:45, the demo is over, and it flopped. What killed it,
ranked, with what the coordinator changes **Sunday morning**:

**K1. The live Jev path fails on stage (likelihood: medium; lethality:
  fatal).** If any beat depends on a real `api.typesafe.ai` call during the
  window and TypeSafe 529s/402s (F10: 402 precedent 2026-09-19; dynamic rate
  limits; ~1% origin flakiness observed in the N=100 campaign) or the egress
  path flakes, beat 1 dies in front of everyone.
  **Change before then:** hard rule — the demo's critical path is 100% local
  (mock engine, recorded storm, frozen endpoints). The real Jev path appears
  ONLY as a labeled optional beat with a pre-recorded fallback cued up.
  Sunday's fault-injection drills must include "kill the Jev path 5 minutes
  before showtime" and verify the demo continues without a beat skipped.

**K2. The persuasiveness trap (likelihood: high; lethality: slow).** The demo
  is slick, everyone is impressed, and the team leaves believing the product
  is done — then Monday's hangover: synthetic data, zero real labels (F8),
  no named partner (F9). The flop isn't on stage; it's the week after, when
  applause becomes the excuse to stop doing distribution.
  **Change before then:** end the demo with the honest-limitations slide
  (what's real / what's synthetic / what doesn't exist yet) and a named
  distribution next step with an owner and a date. The demo's exit condition
  is "one named human agreed to a shadow-tap conversation by Friday" — not
  applause.

**K3. The SRE-in-the-room Q&A (likelihood: medium; lethality: high).** "Show
  me the calibration fit." "What happens when Jev flips its answer?" "Who
  gets paged when you're wrong?" "What's your liability on a missed SEV1?"
  If the demo hand-waves, the proof thesis dies in Q&A.
  **Change before then:** Sunday morning, run the adversarial Q&A drill — the
  10 hardest questions answered *live in the demo environment*, no slides:
  (1) show me a flip; (2) open the audit drawer for a suppression; (3) Jev
  is down — now what; (4) the receiver crashes mid-storm — now what;
  (5) where is the calibration data from; (6) liability on a missed SEV1;
  (7) why not PagerDuty AIOps; (8) what does the SRE do in week 1;
  (9) how are thresholds set for MY team; (10) we change our alerting — what
  breaks. Any question that can't be answered live becomes a labeled
  limitation, not a fumble.

**K4. The "so what" beat (likelihood: medium; lethality: high).** The demo
  shows 96.3% agreement, the audience nods, nobody feels the pain being
  solved. Beat 3 (D-007) is the emotional center — if the synthetic
  divergence isn't viscerally recognizable as *that 3 AM page*, the demo is
  a dashboard tour.
  **Change before then:** rehearse beat 3 as the centerpiece; craft the
  designed divergences from real practitioner pain (F6: Redis spikes clearing
  in <30s; 30–50% of pages are planned-work artifacts). The narrator lands
  one line with weight: "this paged a human at 3:12 AM and self-cleared in
  38 minutes."

**K5. The feature-tour bloat (likelihood: high; lethality: medium).** River +
  calibration + tuner + audit explorer + onboarding in one demo becomes 40
  minutes where nothing lands. Sunday pressure ("we built it all, show it
  all") fights the narrative's own discipline.
  **Change before then:** the coordinator cuts one beat Sunday morning.
  Recommendation: cut the threshold simulator — it serves the "tune" story,
  not the "trust" story. 15 minutes: contract → shadow tap → flip → shadow
  report → D-007 → receipt → limitations + ask. The demo is a trust
  narrative, not a surface inventory.

**K6. The empty room (likelihood: unknown; lethality: existential).** The
  demo is perfect and the only audience is the team.
  **Change before then:** by Sunday noon, an invite list exists and ≥3 humans
  outside the team hold a calendar invite. If impossible, rename the event
  honestly — it is a milestone review, not a launch — and the distribution
  plan gets its own dated owner.

**Logistics:** name the demo driver Sunday morning — not someone who has been
building for 12 hours. 21:00 IST after a full wave is a fatigue risk; the
narrator's energy is part of the demo.

---

## 7. NAMED UNCERTAINTIES

- **U1 (behavioral, untested):** Will any SRE engage divergence transcripts in
  judge mode, or is "fewer decisions" the whole job? The proof UX assumes the
  former; field evidence leans the latter.
- **U2 (commercial, thesis-critical):** Does the Shadow Report clear the
  "worth keeping the tap open" bar? Everything downstream depends on it.
- **U3 (operational):** Who produces the first 50–300 labels per org, at what
  cost in our hours? The cold-start is a distribution problem wearing a
  "shadow mode" costume.
- **U4 (counterparty):** TypeSafe is weeks old, no SOC 2, no SLA, dynamic
  limits, one 402 outage on record. Our enterprise-trust path is blocked
  until theirs exists — or until the self-host escape hatch (F10) is real.
- **U5 (strategic, never kill-attempted):** Is suppression the right second
  stage, or should it be routing/prioritization with suppression never?
  The SOC field study concluded "re-ranking yes, autopilot never" for
  adversarial domains; the SRE domain's non-adversarial input is the
  distinguishing argument, but no principal wave ran the kill attempt on
  "maybe never suppress."
- **U6 (narrative honesty):** If labels beat models (F8), the long-term
  engine may be a fine-tuned encoder, not Jev. The pitch should name the
  exit explicitly — the first partner who reads the JEV report will ask.
- **U7 (commercial):** Liability posture on a missed SEV1 — undesigned.
  Required before any live suppression, even canary.
- **U8 (existential):** Distribution. Zero named prospects (F9). The YC
  hierarchy says this is the pillar that kills us.

---

## 8. HONEST LIMITATIONS OF THIS NOTE

- Web facts were re-verified 2026-10-04 against the cited notes (access dates
  in §1 are the original notes' dates); vendor pricing moves quarterly.
- Repo-internal facts are from `origin/main` @ `2afbf54` and branch
  `lane/principal-fix-designs` as of 2026-10-04; the Sunday wave's demo
  platform did not exist yet at writing time — the pre-mortem targets the
  plan, not the artifact.
- The counter-thesis verdict rests on inference about buyer behavior (U1);
  the base rate (F5: zero paying users anywhere in the ecosystem) means both
  theses are unvalidated commercially. This note does not claim otherwise.
- No design-partner interviews were conducted; the five whys' "why 4" is
  explicitly marked thin.

## 9. IMPLICATIONS

- **No ADR proposed against frozen architecture.** The proof-engine thesis
  survives as architecture and deployment strategy; the build strengthened it
  (§2). Research alters architecture only through reviewed ADRs — none
  warranted from this note.
- **Proposed ADR candidate (distribution):** name the first-10-partners plan —
  owner, channel, post-incident targeting, the label-supply model (U3), and
  the demo's invited audience (K6) — as a dated, owned artifact. The YC
  hierarchy says this outranks the next engine refinement.
- **Proposed ADR candidate (demo):** move the demo's center of gravity from
  the suppression receipt to the Shadow Report (K5 cut list; §5 root cause);
  adopt the §6 pre-mortem mitigations as Sunday-morning checklist items.
- **Proposed ADR candidate (commercial):** liability posture for missed-SEV1
  before any live suppression (U7); name the fine-tuned-encoder exit in the
  partner narrative (U6).
- **Filed for later:** the "maybe never suppress" kill attempt (U5); the
  eval-service-first distribution wedge (§4, brutal question 2); per-org
  onboarding unit economics.
