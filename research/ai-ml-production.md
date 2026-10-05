# AI/ML in production — Phase-1 Stream 4 research

**Stream:** AI/ML in production · **Date:** 2026-10-05 · **Author:** Phase-1 stream-4 worker
**Branch:** `program/architecture-revision-aiml` · **Report:** `research/ai-ml-production.md`

**Scope.** How elite companies bound a non-deterministic model behind deterministic
guardrails, evaluate it honestly, degrade when it fails, control its cost, and
watch it in production — and where Sentinel's race-to-page boundary stands against
that practice. Every external claim carries a source (URL + date). Internal
Sentinel facts cite repo paths, not memory.

**Sentinel context this stream builds on.** Jev (`jev-1.13.0`, pinned, per
`src/sentinel/revalidation.py`) measured live: p50 816 ms / p95 1312 ms / p99 1526 ms
on our path (n=109, 2026-10-03; `research/jev-behavior/latency-report-2026-10-03.md`),
1.3–2.2% observed flips. Race budget B = 2700 ms = `max(1000, 2*1339)` re-derived in
PR #19 (`src/sentinel/race.py:89`). The AI *never* blocks the page: the timer's
default action is passthrough (a deterministic rule), and suppression must be
earned by being fast, confident, and corroborated.

---

## Q1. Model-serving boundaries — the "untrusted model" pattern

### The mechanism

The canonical pattern is **Simplex architecture**: run the high-performance,
hard-to-verify controller *alongside* a verified-safe baseline controller, with
a **decision module** that switches control to the baseline when a safety property
is violated (Sha, IEEE Software 18(4), 2001). The Boeing 777 is the textbook
instantiation — it flies a complex optimized controller plus a secondary
controller based on the older 747's control laws, keeping the aircraft inside the
earlier design's envelope. The designer identifies a safe region, a smaller
recovery region from which the baseline can always regain control, and the
**boundary of the recovery region is the switching condition** — the boundary
contract, in one sentence.
(Sources: Simplex overview via certified-control survey,
http://arXiv.org/pdf/2104.06178, crawled 2026; Heimel VALO whitepaper relating
runtime-assurance architectures,
https://github.com/heimel-open/heimel/blob/HEAD/runtime/v5-core/whitepaper.md, updated Sep 2026)

Two later results strengthen the pattern:
- **Neural Simplex** (Phan et al., ISoLA 2020; https://arxiv.org/pdf/1908.00528)
  adds reverse switching and online retraining of the advanced controller — the
  baseline is *permanent*, but the advanced layer can earn its way back.
- **Black-Box Simplex** (Bak, Smolka, Stoller, 2024;
  https://par.nsf.gov/biblio/10508985-black-box-simplex-architecture-runtime-assurance-multi-agent-cps)
  proves that **runtime checks can replace static baseline verification** —
  demonstrated with neural controllers that occasionally output unsafe commands
  (F-16 formation flight) while the system stays safe. This is the result that
  matters for Sentinel: you do not have to *verify* Jev; you have to bound it.

The 2026-vintage LLM-engineering consensus has converged on the same skeleton
from below, as a three-plane middleware:
1. **Input rails** — before the model: block/modify/strip (PII, injection,
   jailbreak shapes) — deterministic where the check is pattern-matchable.
2. **Flow routing** — canonical flows with mandatory acknowledgment steps.
3. **Output rails** — after the model: schema/structure validation,
   blocklists, mandatory tokens.
   (Source: NeMo Guardrails production playbook,
   https://github.com/explorepraburamaraj/production-ai-engineering-playbook/blob/HEAD/week-02/W2D7_deterministic-guardrails-nemo/docs/technical-document.md,
   Aug 2026)

The rule that falls out across every serious write-up: **cheap deterministic
detectors handle everything expressible as a pattern; the model is reserved for
judgments that genuinely require understanding** ("Use cheap deterministic
detectors for everything you can express as a pattern. Reserve an LLM for the
judgments that genuinely require understanding." —
https://github.com/trishambp/trishambp.github.io/blob/HEAD/_implementations/guardrails-and-safety-for-production-llm-agents.md,
Sep 2026; same ordering argued in
https://github.com/ethemkd/tealtiger/blob/HEAD/blog-publish/haystack-tealtiger/_posts/2026-08-11-llm-judge-vs-deterministic-guardrails.md, Aug 2026).

The sharpest critique sharpens the contract's content: most production
"guardrails" are a **second model judging the first one's output — correlated
risk wearing a safety costume**, same architecture, same training distribution,
same adversarial surface. Real safety engineering treats ML guardrails as
*advisory signals* and puts the actual enforcement in deterministic code:
schemas, allowlists, circuit breakers, sandboxed side effects
(https://dev.to/aiexplore369zoho/llm-guardrails-are-just-another-model-not-a-safety-boundary-56o8,
Oct 2026). The extreme form: StarkGate, a **fully deterministic decision
firewall** between agent and world — zero LLM in the loop, microseconds, and
**fail-closed by default** on network drops, key rotation, malformed payloads
(http://dev.to/starkgate/why-llm-guardrails-are-failing-ai-agents-and-how-we-built-a-deterministic-firewall-instead-3jj9, Sep 2026).

**What a production boundary contract contains** (synthesizing the above):
(1) the identity of the verified baseline and the decision module that owns the
switch; (2) the switching condition stated as a predicate on observable state,
not on model internals; (3) the fail direction of the switch (fail-closed vs
fail-open — a Type-1 choice); (4) proof the baseline's safe region covers the
switching transient (Black-Box Simplex's runtime-check theorem is the model);
(5) no-model-on-the-boundary — nothing on the decision path may itself be
non-deterministic.

### Relevance to Sentinel

Sentinel is a recognizable Simplex instance: Jev is the advanced controller, the
paging path (timer-default passthrough + the deterministic triple-lock) is the
baseline, and the race is the decision module — with the unusual property that
the "switch" is *temporal* (budget B) rather than state-predicate. Two
differences cut against Sentinel. First, industry practice converges on
**fail-closed** switches for safety-critical paths; Sentinel's switch is
deliberately **fail-open to page** — the honest, correct direction for a
page-or-suppress system (a missed page is a SEV-1 silence; a wrong page is
fatigue), and it maps to the principal-systems Data/AI clause ("no
probabilistic system holds absolute veto over a critical path — hardcoded
fallback"). But it should be written down as the Type-1 decision it is, with
the SEV-1-vs-fatigue asymmetry priced. Second — and this is the gap —
Sentinel's boundary is *one-sided*: it has the baseline and the switch, but
**no recovery-of-the-advanced-layer discipline** (Neural Simplex's reverse
switching, StarkGate's auto-recovery). Jev comes back only by implication; there
is no measured "healthy again" transition, no graded re-entry, no logging of
the advanced layer's availability as a first-class signal. The dev.to piece's
correlated-guardrail critique does *not* bite Sentinel — the timer, the
deterministic instruction firewall (ADR-020), and the freshness proofs are
deterministic code, not a second model. That is a genuine, citable strength of
the current design.

---

## Q2. Eval harnesses — offline, shadow, and the line between trustworthy and theater

### The mechanism

**Golden sets.** Start with 50–200 *real* tasks, not synthetic trivia; label
expected *behavior* (schema, refusal, tool sequence), not exact strings; freeze
the set — "if you keep editing labels to match the new model, you are cheating."
Run tiers: smoke (20, every PR), regression (full, on prompt/model change),
hard (known failures you are never allowed to forget)
(https://github.com/kayra-ml/rovecode_plugins/blob/HEAD/plugins/ai-engineering/skills/llm-evaluation.md,
Sep 2026). Anthropic's three design principles, verbatim: **task-specific**,
**automate when possible**, and **prioritize volume over quality** — and start
small, ~20 queries of real usage patterns
(https://platform.claude.com/docs/en/test-and-evaluate/develop-tests, via
https://github.com/lokumai/ai-engineering-bazaar/blob/HEAD/mini-courses/scratchpad/research/13_loop_engineering.md,
Sep 2026). Named edge cases to include: irrelevant/nonexistent input, overly
long input, harmful input, ambiguous cases where even humans disagree.

**Three-way split discipline.** Development set the author may look at while
editing prompts; held-out set that only the CI gate sees; and the *judge itself*
needs its own held-out validation — Hamel Husain's standing result: a judge is
just another model, so measure its agreement with expert labels
(precision/recall/Cohen's κ) on a held-out split before the judge earns gate
duty, and re-calibrate on a schedule; Shreya Shankar's "Who Validates the
Validators?" sets the floor at ~100+ labeled examples with weekly re-calibration
(https://hamel.dev/blog/posts/llm-judge/, via
https://github.com/rczamor/rz-agent-team/blob/HEAD/corpus/qa-eng/hamel-husain-shreya-shankar.md,
notes on hamel.dev / parlance-labs material, 2026). Datadog's validation
protocol is the operational form: build a human-labeled golden set spanning
clear passes, clear failures, and the ambiguous middle (where judges and humans
diverge most); measure judge–human agreement corrected for chance; inspect the
disagreements — systematic disagreement is a rubric defect, not a model limit;
re-validate on every judge-model, rubric, prompt, or traffic-distribution
change; and track judge cost/latency as first-class metrics in the same view
as application spend (https://www.datadoghq.com/knowledge-center/llm-as-a-judge/,
Sep 2026). The field's calibration bars: κ ≥ 0.75 judge–human agreement on the
FDE pre-flight checklist
(https://github.com/adilshamim8/fde-field-guide/blob/HEAD/ai/03-evaluation-and-testing.md,
Sep 2026); >90% judge–expert agreement within 3 judge-prompt iterations in the
Honeycomb case study (https://hamel.dev/blog/posts/llm-judge/, via
https://github.com/evanoman/reader-triage/blob/HEAD/docs/research/llm-scoring-methods/09%20-%20Production%20LLM%20Scoring%20Systems.md,
Aug 2026). Judge biases to design against: position bias (favors the first
option shown) and self-preference bias (favors its own model family) —
randomize candidate order and treat judge scores as directional signal, never
ground truth
(https://github.com/niravrvaghasiya/ml-ai-skills/blob/HEAD/llm-evaluation/SKILL.md,
Sep 2026). The sharpest operational discipline: a new judge version runs in
**shadow** against the current one on the golden set, you compare their
human-agreement, and only then promote it to gating duty — shadow-deploy your
evaluator before trusting it
(https://github.com/s-samarth/datasciencepreparation/blob/HEAD/RawNotes/MLCaseStudies/06-llm-evaluation-monitoring-platform.md,
Sep 2026). Error analysis stays the foundation: read traces first, build a
failure-mode taxonomy, and target evals at the dominant modes — Hamel Husain's
teams spend 60–80% of dev time on error analysis and evals; the classic
anti-pattern is a single "rate quality 1–5" judge with no error analysis, no
failure-mode decomposition, no calibration — uncalibrated, too coarse to drive
a fix, blind to process failures
(https://github.com/landedjobs/ai-product-engineer-roadmap/blob/HEAD/roadmap/5-evals-and-reliability.md,
Sep 2026; https://www.antoinebuteau.com/lessons-from-hamel-husain/, Sep 2026).

**Shadow eval.** Run the candidate in parallel on a slice of live traffic; the
caller still gets the real response and the shadow response is never served —
a blind LLM judge compares the two arms and reports a win rate, overall and
per complexity tier, answering "should this key adopt the change?" (forward)
or "is the current setup still worth it?" (reverse)
(https://docs.litellm.ai/blog/auto-router-shadow-evaluations, Aug 2026). The
mature rollout sequence is shadow → canary → A/B: shadow carries zero user
risk and validates latency, stability, and prediction sanity; canary takes a
small live slice and watches health metrics on a progressive ramp; A/B is the
statistically designed traffic split on business metrics — the only proof that
offline gains move product outcomes, and the slowest because it needs volume
(https://github.com/pranesh-2005/ai-ml-dl-courses/blob/HEAD/V1/08_MLOps_LLMOps.md,
Jul 2026). LLM-specific wrinkle for A/B: outputs are non-deterministic even
for identical version and identical input, so you need a genuinely larger
sample than intuition suggests — sampling temperature is a variance source in
the power calculation, not something you can ignore because "the code didn't
change"
(https://github.com/ml-cab/ml-for-java-minds/blob/HEAD/part4/33-model-and-prompt-versioning.md,
Sep 2026).

**Rotation rules.** Two distinct enemies: *contamination* (memorizing answers)
and the subtler *overfitting-to-the-benchmark* (Goodhart's law — iterate
against one fixed set long enough and you tune the system, prompts, and rubric
to that set's quirks; the score climbs while real capability stalls, no leak
required). Symptoms of the second: the gate score rises release over release
but user reports and spot checks don't move; gains concentrate on the items
you looked at most and vanish on new items; a newly drawn set from the same
distribution scores materially lower than the standing set. Defenses: reserve
a rarely-touched held-out set opened only at release — "the set you read every
iteration is the set you overfit; frequency of contact is the risk, not just
leakage"; rotate and refresh items on a schedule and report the fresh slice
separately, watching the standing-vs-fresh gap; cap look frequency (track how
many times each set has been evaluated — a set looked at hundreds of times is
a training set in disguise); keep an ungameable qualitative human pass that
no optimization loop can see; judge a basket, not one number; and when the
metric and reality disagree, believe reality and suspect the benchmark
(https://github.com/alexgreensh/eval-genius/blob/HEAD/skills/eval-genius/references/05-dataset-construction.md,
Sep 2026). Anti-contamination hygiene: never paste benchmark items into
prompts, docs, or issue trackers that get crawled; at release time run
exact-match hashing, near-dup similarity, and n-gram overlap between splits;
version datasets semantically and never mutate examples — add or deprecate —
with provenance (who, when, which script, source licenses) in a dataset card
(https://medium.com/@connect.hashblock/dataset-versioning-habits-that-make-llm-eval-trustworthy-46d201178e83,
Jan 2026). If eval prompts or close paraphrases leaked into fine-tuning or
few-shot examples, scores are inflated and don't predict production behavior —
keep the eval set held out and refresh it on a cadence
(https://github.com/niravrvaghasiya/ml-ai-skills/blob/HEAD/llm-evaluation/SKILL.md,
Sep 2026).

**What makes eval theater vs trustworthy.** Theater signals: labels edited to
match the new model; a judge that was never calibrated against human labels;
dev set and gate set being the same items; a single generic "rate 1–5" judge
with no error analysis; evals that never block a merge; cost and latency
absent from the gate (a technically accurate but slow or expensive change is
still a regression —
https://hackernoon.com/a-quality-engineering-framework-for-testing-ai-ml-and-llm-systems,
Sep 2026). Trustworthy: frozen golden set drawn from real traffic; held-out
split opened rarely; calibrated judge with a κ bar; shadow-before-promotion
for judge changes; rotation schedule with standing-vs-fresh gap monitoring;
layered metrics cheapest-first (deterministic code scorers → semantic match →
judge as last resort for fuzzy quality —
https://github.com/sir-chawakorn/sanook-cli/blob/HEAD/skills/llm-eval-harness/SKILL.md,
Jun 2026); evals wired into CI with pass thresholds that automatically block
regressions; model versions pinned with every run. The FDE pre-flight
checklist is the audit to copy: golden set assembled (50–200 real cases),
≥10% adversarial edge cases, held-out split, per-class precision/recall/F1,
deterministic schema invariants validated before any LLM evaluation, latency
percentiles under load, automated CI/CD harness blocking merges
(https://github.com/adilshamim8/fde-field-guide/blob/HEAD/ai/03-evaluation-and-testing.md,
Sep 2026).

### Relevance to Sentinel

Sentinel's eval gap is concrete. The deterministic triple-lock is the natural
code-scorer layer; Jev-as-judge needs its own calibration — agreement between
Jev's suppress/page verdicts and the human-oncall's eventual judgment, measured
on a held-out trace set, with the FDE bar (κ ≥ 0.75) as the floor. The Oct-4
A/B gives a ready-made regression seed (20–24% of calls exceeded the 2700 ms
budget during the vendor slowdown; ~89% of flip signal was latency artifact).
Any change to the judge path, the race budget, or a fallback rung must run
shadow against the current configuration on the golden set before promotion —
including the judge itself. Production failures feed the golden set (the
LLMOps loop: production data → eval set → prompts → production —
http://dev.to/prakruti_biswas/from-prototype-to-production-an-llmops-guide-for-gen-ai-apps-2h7o,
Sep 2026), with rotation discipline so the set doesn't become a training set
in disguise.

---

## Q3. Fallback & degradation — ladders, second providers, same-contract rules

### The mechanism

**Degradation ladders.** Design the ladder before the outage, one rung per
capability cut:
rung 0 — primary model, full toolset (full experience);
rung 1 — fallback model (different provider), same tools;
rung 2 — same provider, smaller/faster model (slightly worse answers);
rung 3 — no tools, retrieval-only mode (answers from pre-indexed docs);
rung 4 — static/canned responses + escalation ("a human will reply shortly").
Each rung needs a trigger (error-rate threshold, provider health check), a
specific capability trade, a user-visible honesty clause — rung 3 must say
"live data unavailable, showing cached knowledge", because users forgive
slower but not silent degradation — plus flag propagation (`degraded`,
`degradation_reason` reach the final answer), a rung-specific lifeboat prompt,
a tighter budget than the normal path, and auto-recovery back up the ladder
(breaker re-close, primary health probe) — manual un-degrading is forgotten
within a week
(https://github.com/shanmukhanssm/langgraph/blob/HEAD/skills/agent-reliability-hardener/SKILL.md,
Sep 2026). The framing that lands: decide in advance what "degraded" looks
like and make it a designed state, not an accident — a user-visible "we're
operating in reduced mode" beats a timeout, and both beat a cascade
(https://github.com/shafaypro/crackingmachinelearninginterview/blob/HEAD/system_design/intro_backend_ai_system_design.md,
Sep 2026).

**Model-down / slow / wrong map to different triggers.** Hard failures —
connection errors, 5xx, auth errors — fall back immediately. Rate limits:
retry once with backoff, then fall back (a queue of retries during an outage
is a slow outage). Timeouts before the first token: fall back; after the
first token, finish or fail cleanly rather than restarting with a different
voice mid-answer. Content refusals are signals, not errors — never fall back
on them automatically
(https://dev.to/pranjulrathour/multi-provider-llm-fallback-staying-up-when-one-api-goes-down-40dp,
Sep 2026). Reliability mechanics underneath: a hard timeout on every call —
without it, one slow upstream hangs the request indefinitely; bounded retries
with exponential backoff + full jitter (LiteLLM defaults: backoff from 0.2 s
up to a 10 s cap; never `while True`); only retry idempotent steps; circuit-
break and degrade gracefully — return a partial result, a cached result, or a
cheaper-model result, never hang
(https://github.com/damiangilgonzalez1995/rsc-harness/blob/HEAD/skills/llm-pipeline/SKILL.md,
Sep 2026). "Wrong" (confidently incorrect, not erroring) does *not* trip
HTTP-level breakers — it is caught by quality monitoring, confidence
thresholds, and sampled judging (Q5), not by the ladder's triggers.

**Second-provider / fallback paths — the same-contract requirement.** Fallback
and routing are orthogonal: routing decides the *desired* model (quality/cost,
planner-invoked); fallback decides who *actually serves* when the desired one
can't (availability, harness-invoked on classified errors); the circuit
breaker feeds both
(https://github.com/bagofwords1/bagofwords/blob/HEAD/docs/design/llm-fallback.md,
Sep 2026). A production router needs four things: ordered providers,
per-provider timeouts, a circuit breaker, and structured logging — skip, don't
retry, a tripped provider (a breaker is cheaper than a timeout); fail fast,
fall down, each provider on its own timeout so a hung request can't block the
cascade; log the whole chain so the final error tells you *why each one*
failed; keep the order configurable, not hardcoded
(https://dev.to/ancucorp/designing-a-multi-model-ai-router-that-doesnt-fall-over-cascade-circuit-breaker-119o,
Sep 2026). The abstraction that makes multi-provider fallback possible: one
interface — `generate(messages, options)` → tokens + usage — with prompts kept
in a neutral format and translated per provider inside the adapter; the moment
a prompt depends on one vendor's special syntax, fallback silently degrades
quality. Order providers by quality *on your evaluation set*, then by cost;
keep a cheap, fast model as the last resort so the product degrades to
"shorter, plainer answers" rather than "no answers"; record which provider
answered each request or quality complaints become undebuggable
(https://dev.to/pranjulrathour/multi-provider-llm-fallback-staying-up-when-one-api-goes-down-40dp,
Sep 2026). The hard rule: **test the fallback with the same eval suite or you
shipped fallback-theater** — a fallback that was never evaluated is a hope,
not a rung
(https://github.com/shanmukhanssm/langgraph/blob/HEAD/skills/agent-reliability-hardener/SKILL.md,
Sep 2026). A second provider is one of the cheapest reliability wins available
— single-provider systems lose 4–40+ hours/year to the provider alone. And the
fallback model is uptime engineering for model-disappearance events no retry
policy fixes (a frontier model vanished from the market for twenty days on a
government decision): a second provider or smaller model, pre-tested against
your workload, behind the same interface, so the switch is a config change,
not an engineering sprint that starts the morning of the outage
(http://dev.to/draganristicrsjpg/retry-backoff-and-circuit-breakers-for-llm-api-calls-h3k,
Sep 2026). Layer bulkheads so one slow provider doesn't exhaust the shared
connection pool, and load-shedding that rejects low-priority traffic early to
protect the SLO for the rest
(https://github.com/shafaypro/crackingmachinelearninginterview/blob/HEAD/system_design/intro_backend_ai_system_design.md,
Sep 2026). Checklist form: 429/5xx retried with exp backoff + jitter,
Retry-After honored over the local formula, total retry time capped by a
per-request deadline, 400s/auth failures/refusals never retried, circuit
breaker per provider with state visible on the dashboard, persistent queue
draining after outages, fallback model tested against the real workload and
switchable by config
(http://dev.to/draganristicrsjpg/retry-backoff-and-circuit-breakers-for-llm-api-calls-h3k,
Sep 2026).

### Relevance to Sentinel

Sentinel's ladder today is two rungs: rung 0 (Jev wins — suppress) and the
fail-open baseline (page human). The missing piece is **rung 1 between "Jev
wins" and "page human"**: an exact-cache on alert fingerprints (repeat alerts
with identical fingerprints need no model call at all) and a second-provider
path behind the same contract — same request shape, same eval suite, and a
tighter time budget that fits *inside the remaining* 2700 ms, not a fresh
budget. Triggers: hard Jev failures and timeouts map cleanly onto
immediate-fallback; the 20–24% budget-breach class from the Oct-4 A/B is the
slow case that today burns the full budget before paging — a rung 1 with a
shorter deadline would convert much of that class into resolved decisions.
"Wrong" is the hard case: Jev confidently wrong doesn't trip the ladder,
which is why Q5's sampled-judge monitoring must exist. The `degraded` flag
must propagate to the event log on every degraded decision — silent
degradation is the one thing neither users nor oncall forgive.

---

## Q4. Cost control — caching, batching, routing, circuit breakers

### The mechanism

**Instrument first.** Log every API call with timestamp, model name, input
tokens, output tokens, latency, computed cost, user ID, cache hit/miss, and
request category — this data reveals which features are expensive, which users
are heavy consumers, and where caching has the most impact
(https://github.com/menokoog/learning-app/blob/HEAD/content/vol4-llms/64-caching-rate-limiting-cost-optimization.md,
Sep 2026). Cost attribution by team/feature/application is the prerequisite:
you cannot cut what you cannot see; without it, LLM spend grows unchecked
until it shows up as a surprise on the cloud bill — per a16z, AI inference is
the single largest line item in the AI application stack for most companies
scaling beyond prototype
(https://neuraltrust.ai/blog/ai-gateway-llm-cost-optimization, Sep 2026).

**The optimization stack, in effort-to-payoff order — cache, route,
compress.** Provider prompt caching (KV-cache reuse on repeated prefixes):
up to ~90% on cached input tokens (Anthropic), low effort, needs a stable
prefix. **Exact caching**: hash of the canonicalized request → stored
response; low effort (hash + dict), typical 10–20% savings — identical calls
never hit the model twice. Semantic caching (embedding similarity, cosine
> 0.95 as the safe-serve threshold; tools: GPTCache, Redis + embedding
lookup): 15–30% savings at medium effort, trading false-positive cache hits
at too-loose thresholds — a threshold set too loosely serves confidently
wrong answers to subtly different questions, and a cache that ignores the
requesting user is a data-leak vector, so every cache needs an invalidation
story, a TTL, and per-user scoping when responses are personalized
(https://github.com/menokoog/learning-app/blob/HEAD/content/vol4-llms/64-caching-rate-limiting-cost-optimization.md,
Sep 2026;
https://github.com/youlianvr/oper-share/blob/HEAD/skills/llm-cost-optimizer/SKILL.md,
Sep 2026;
https://github.com/shafaypro/crackingmachinelearninginterview/blob/HEAD/system_design/intro_backend_ai_system_design.md,
Sep 2026). Model routing / cascades (RouteLLM and FrugalGPT report 85–98%;
LiteLLM shadow evals show auto-routing saving 51% in production, 69% stacked
on prompt caching): a cheap model attempts first and escalates only the
fraction that genuinely needs the expensive model — sequence by effort, and
never call the large model by default
(https://bigdataboutique.com/blog/llm-cost-optimization-techniques, Sep 2026;
https://docs.litellm.ai/blog/auto-router-shadow-evaluations, Aug 2026).
Batching: the Batch API processes asynchronously at a flat 50% discount
(OpenAI; Anthropic similar) for work that can wait up to 24 h — nightly
processing, bulk classification, evaluation runs, data enrichment — never for
real-time user-facing queries
(https://github.com/menokoog/learning-app/blob/HEAD/content/vol4-llms/64-caching-rate-limiting-cost-optimization.md,
Sep 2026). Output-token control (`max_tokens` bounds per endpoint — output
tokens are the higher-priced side) and prompt compression round out the cheap
layer. The three genuinely different engineering responses run simultaneously:
**avoid** a call entirely (caching), **cheapen** a call that must happen
(routing, batching), and **measure** whether the first two worked — an SLO
stated as a percentile and an error budget that turns "how unreliable may
this be" into an exhaustible number
(https://github.com/agenticforze/ai-regenesis-learn/blob/HEAD/docs/10-agentic-architectures/10-9-cost-latency-and-reliability-engineering-for-agents.mdx,
Sep 2026). Verify every optimization against a quality threshold per task — a
cheaper wrong answer costs more than an expensive right one
(https://bigdataboutique.com/blog/llm-cost-optimization-techniques, Sep 2026).

**Cost circuit breakers and budget discipline.** A circuit breaker stops
spending when you hit a limit — without one, a bug or abuse can burn through
a monthly budget in hours. Three thresholds: warning at 70% (alert), throttle
at 85% (cheaper models only), stop at 95% (reject new requests, serve cached
responses only)
(https://github.com/menokoog/learning-app/blob/HEAD/content/vol4-llms/64-caching-rate-limiting-cost-optimization.md,
Sep 2026). Budget envelopes per feature, per user tier, per day, with soft
alerts at 80% of limit; per-key daily request-count and USD-cost quotas
enforced with HTTP 429; tier model access by user tier at design time —
model monoculture (all requests hitting one model) is the #1 overspend
pattern, and no cost alerts means spikes go undetected for days
(https://github.com/youlianvr/oper-share/blob/HEAD/skills/llm-cost-optimizer/SKILL.md,
Sep 2026; https://github.com/tridpt/llm-gateway, Aug 2026). The architectural
point: LLM cost is unusual in that it varies per request rather than being a
fixed serving cost — so cost belongs in the same category as latency and
availability: SLOs, monitoring, alerting, not a monthly invoice surprise. When
the budget is exceeded the ladder is: switch to a smaller model → serve cached
response → queue for async processing
(https://github.com/shafaypro/crackingmachinelearninginterview/blob/HEAD/system_design/intro_backend_ai_system_design.md,
Sep 2026). The gateway pattern centralizes all of this (routing, caching,
token/rate limits, attribution, budgets) so application code stays simple and
there is one place to observe and control spend — and the discipline applies
to evaluation spend too: judge and shadow-eval calls belong in the same cost
view as application calls, or coverage gets quietly cut for budget reasons
(https://neuraltrust.ai/blog/ai-gateway-llm-cost-optimization, Sep 2026;
https://www.datadoghq.com/knowledge-center/llm-as-a-judge/, Sep 2026).

### Relevance to Sentinel

Jev is per-call-priced (~$0.042/M input), so every race entry has a price tag
— cost-per-decision should be logged alongside latency in the event log
before any other control. The missing controls are exactly the program's known
findings: **no exact-cache on alert fingerprints** (the cheapest rung — repeat
alerts with identical fingerprints should never reach the model), **no cost
circuit breaker** (warn/throttle/stop thresholds on daily/weekly Jev spend,
wired into the degradation ladder: throttle → cheaper path → cached only),
and **no same-eval-suite requirement for fallback paths** (any second-provider
or cached path must clear the golden set before it can serve a rung-1
decision). A shadow eval of a model router is also the honest way to test
whether a cheaper judge could handle the easy fraction of alerts — LiteLLM's
51% production saving is the reference number to beat or dismiss with data.

---

## Q5. Monitoring model behavior — drift, quality sampling, dashboards

### The mechanism

**The thing most teams skip is quality monitoring.** Cost and latency come
from API response metadata for free; quality requires actually looking at what
the model said and whether it was any good. Build a pipeline that samples
1–5% of production traffic, runs it through LLM-as-judge evaluation, and
surfaces quality trend data weekly
(https://github.com/parthivpandya/enterprise-ai-architecture-library/blob/HEAD/ai-ea-library/03-EA-Practice/05-LLMOps.md,
Sep 2026). What to monitor, in full: latency (p50/p95/p99 per endpoint);
token cost (input/output/total per request, attributed to business unit,
feature, user); error rate (API failures, timeout rates, guardrail trigger
rates); hallucination rate (automated factuality check against the knowledge
base); output quality drift (judge scoring on sampled traffic); prompt
injection attempts (flagged by the input layer); user feedback signals
(thumbs up/down, correction rates, escalation rates)
(https://github.com/parthivpandya/enterprise-ai-architecture-library/blob/HEAD/ai-ea-library/03-EA-Practice/05-LLMOps.md,
Sep 2026). Start with a small set of SLOs tied to user experience — maximum
cost per resolved request, minimum groundedness, acceptable tool success
rates, p95 latency target — because hundreds of metrics without ownership or
thresholds produce dashboards nobody uses; separate debugging data (detailed
traces on a sampled subset) from analytics data (aggregate trends), and use
adaptive sampling to retain failures, high-cost requests, unusual paths, and
low eval scores while reducing volume for routine interactions
(https://www.theprotec.com/blog/ai-observability-tools-monitor-llm-apps/, Sep
2026).

**Drift detection.** Detect input drift from inputs alone (PSI/KS vs the
training/eval reference — a pre-flight checklist sets PSI ≥ 0.25 on input
length distributions and embedding clusters as the alert line); output drift
needs labels or strong proxies: prediction-distribution and score-calibration
shifts, business KPIs correlated with model quality (chargeback disputes,
manual-review overturn rate), segment-level anomalies
(https://github.com/pranesh-2005/ai-ml-dl-courses/blob/HEAD/V1/08_MLOps_LLMOps.md,
Jul 2026;
https://github.com/adilshamim8/fde-field-guide/blob/HEAD/ai/04-monitoring-and-reliability.md,
Sep 2026). When true labels arrive weeks late, layer proxies now and schedule
backtests as labels mature — comparing realized metrics to training-time eval
and alerting on gaps — and design label capture into the product with
prediction timestamps so late labels join correctly (point-in-time)
(https://github.com/pranesh-2005/ai-ml-dl-courses/blob/HEAD/V1/08_MLOps_LLMOps.md,
Jul 2026).

**Real-time vs batch.** Real-time monitoring is threshold alerting for acute
issues — error rates over baseline, latency beyond the acceptable line, token
costs spiking, guardrail triggers multiplying. Batch analysis is the periodic
quality assessment that catches gradual degradation no real-time alert fires
on: automated evaluators against sampled sessions, measuring the quality
dimensions too expensive to assess live
(http://dev.to/kuldeep_paul/llm-monitoring-for-reliable-agents-18ne, Oct
2025). Two more production disciplines: log enough to replay failures
(inputs, outputs, context, prompt and model versions, latency, feedback —
with privacy controls, since logs contain sensitive material), and read fifty
real conversations a week — it teaches more than most dashboards. The loop
this closes is the heart of LLMOps: production data improves the eval set,
which improves prompts and retrieval, which improves production — teams that
build it early improve steadily, teams without it guess
(http://dev.to/prakruti_biswas/from-prototype-to-production-an-llmops-guide-for-gen-ai-apps-2h7o,
Sep 2026). Comparative monitoring across versions (control vs treatment with
significance testing) doubles as rollout validation. Pin model versions and
test new provider versions deliberately instead of letting a provider update
change behavior unexpectedly — silent provider drift is the failure mode
nobody instruments for, which is why the FDE checklist mandates scheduled
golden benchmark runs (weekly cron of the golden eval set against production
config)
(https://github.com/adilshamim8/fde-field-guide/blob/HEAD/ai/04-monitoring-and-reliability.md,
Sep 2026). Paging discipline: symptom-based rules (p95 over the line, refusal
rate tripling) that fire on user-impacting symptoms, not transient
single-request exceptions; every model-backed feature independently killable
via feature flag; every alert linked to an explicit mitigation runbook
(https://github.com/adilshamim8/fde-field-guide/blob/HEAD/ai/04-monitoring-and-reliability.md,
Sep 2026). Reference tooling: Langfuse (open-source trace logging),
OpenTelemetry spans per model call, Helicone/Datadog for cost + quality,
Prometheus/Grafana dashboards
(https://www.theprotec.com/blog/ai-observability-tools-monitor-llm-apps/, Sep
2026; https://medium.com/@khushijigenshah/llm-observability-monitoring-large-language-models-da5a9bf9febb,
Mar 2026).

### Relevance to Sentinel

The Oct-4 A/B is a monitoring case study: 20–24% of Jev calls exceeded the
2700 ms budget during the vendor slowdown, and ~89% of the flip signal was
latency artifact — which means **flip rate and latency must be monitored
disentangled**, or every future A/B reads vendor weather as model behavior.
The metrics Sentinel needs, in order: budget-breach rate (p-breach of the
2700 ms budget — the rung-0/rung-n switch's own health), Jev flip rate tracked
against its 1.3–2.2% baseline, suppression-vs-page decision quality sampled
weekly with an LLM judge against the human-oncall's eventual resolution as
the delayed label, guardrail trigger rates (ADR-020 firewall, freshness
proofs), and a weekly golden-benchmark cron against production config to catch
silent provider drift behind the pinned `jev-1.13.0` string. Delayed labels
need point-in-time capture: every decision logged with model version,
fingerprint, latency, and outcome so late labels join correctly.

---

## AI-boundary checklist — 10 requirements for a production-grade deterministic/non-deterministic boundary

Synthesized from Q1–Q5 plus the deterministic-controls literature (Salesforce
engineering's Agent Graph pattern: the LLM may signal intent through a state
variable, but deterministic guards decide routing, identity, and what the
client will accept — the model never controls opaque identifiers, and if JSON
parsing fails the system returns an error instead of guessing —
https://engineering.salesforce.com/how-deterministic-controls-turn-ai-output-into-reliable-prompt-templates/,
Oct 2026; the zero-trust agent guardrail rules: dual-LLM/zero-trust boundary —
the model that reads data is not the model that acts; gates are deny-only,
never let an LLM-driven module override security checks; budget guards live
outside the LLM runtime at the OS layer; demand deterministic evidence
(checksums, diffs, AST facts) before state transitions —
http://dev.to/jackymencz/autodoc-sentinel-building-a-deterministic-zero-trust-guardrail-for-autonomous-ai-agents-aa4,
Sep 2026; production guardrail layering: control lives outside the LLM,
intercept before execution, hard transaction limits, isolated tool scope,
state rollbacks, log every input/tool choice/policy check —
https://dev.to/rcortez056/why-prompts-fail-as-ai-agent-guardrails-and-how-to-fix-it-47j5,
Sep 2026).

1. **Named baseline and decision module.** The verified deterministic fallback
   and the module that owns the switch are named in the architecture doc; the
   switch's identity is not "whatever happens when the model is slow."
2. **Fail direction written as a Type-1 decision.** Fail-open vs fail-closed
   is decided in writing with the loss asymmetry priced (for Sentinel: missed
   page = SEV-1 silence vs wrong page = fatigue), and mapped to the
   principal-systems Data/AI clause — no probabilistic system holds absolute
   veto over a critical path.
3. **Switching condition is a predicate on observable state.** Budget, health
   probe, confidence threshold — never model internals. The Black-Box Simplex
   result (runtime checks can replace static baseline verification) is the
   theoretical warrant.
4. **No model on the boundary.** Nothing on the decision path is itself
   non-deterministic. Second-model judges are advisory signals; enforcement
   lives in deterministic code — schemas, allowlists, circuit breakers,
   sandboxed side effects. (The correlated-guardrail critique: a second model
   judging the first is correlated risk in a safety costume —
   https://dev.to/aiexplore369zoho/llm-guardrails-are-just-another-model-not-a-safety-boundary-56o8,
   Oct 2026.)
5. **Deny-only gates.** The model can *earn* an action (fast + confident +
   corroborated); it can never override a deterministic safety check, invent
   permissions, or control routing/identity/output format. Parse failure →
   error, never guess.
6. **Hard deadline on every model call.** No naked calls. The timeout budget
   covers the machine path only — human waits (escalation, approval) are a
   deliberate exclusion from the machine-controlled latency budget, not a
   rounding error inside it
   (https://github.com/agenticforze/ai-regenesis-learn/blob/HEAD/docs/10-agentic-architectures/10-9-cost-latency-and-reliability-engineering-for-agents.mdx,
   Sep 2026).
7. **Designed degradation ladder.** Rungs with triggers, capability trades,
   honesty flags, tighter budgets per rung, and auto-recovery; the fallback
   clears the same eval suite as the primary (no fallback theater).
8. **Cost is an SLO.** Per-call spend attributed; warn/throttle/stop circuit
   breaker on budget; judge and shadow-eval spend in the same cost view as
   application spend.
9. **Eval harness before promotion.** Frozen golden set from real traffic,
   held-out split, calibrated judge (κ ≥ 0.75 bar), shadow-vs-current
   comparison for judge/model changes, rotation schedule with
   standing-vs-fresh gap monitoring; metric-reality divergence is an eval bug,
   not a product bug.
10. **Production monitoring with teeth.** Dual-plane dashboards (infra
    metrics + quality proxies), 1–5% quality sampling with weekly judge
    scoring, weekly golden re-runs against silent provider drift,
    symptom-based paging, per-feature kill switches, runbook per alert.

---

## Relevance to Sentinel — gaps

Sentinel's current design already gets the hardest part right: it is a
recognizable **Simplex instance** (Q1), and the correlated-guardrail critique
does not bite — the timer, the ADR-020 instruction firewall, and the
freshness proofs are deterministic code, not a second model. That is a citable,
defensible strength. The gaps are in what *surrounds* the boundary.

**G1. The Simplex warrant is unwritten.** Black-Box Simplex (Bak, Smolka,
Stoller, 2024) is the theoretical warrant for "you do not have to verify Jev;
you have to bound it" — it exists nowhere in the repo. Write the boundary
contract: named baseline (paging path, timer-default passthrough), decision
module (the race), switching predicate (2700 ms budget), and the fail-open
direction recorded as the Type-1 decision it is, with the SEV-1-silence vs
page-fatigue asymmetry priced.

**G2. No recovery discipline for the advanced layer.** Neural Simplex's
reverse switching is absent: Jev "comes back" by implication only. There is
no measured "healthy again" transition, no graded re-entry after a provider
slowdown, and no logging of Jev availability as a first-class signal. The
Oct-4 A/B showed 20–24% of calls exceeding the 2700 ms budget during a vendor
slowdown — the system needs a defined path back to rung 0 with health probes,
not just absence of failure.

**G3. No rung 1 between "Jev wins" and "page human" — the biggest gap.** No
second-provider path behind the same contract, no exact-cache on alert
fingerprints, no cost circuit breaker, and no same-eval-suite requirement for
fallbacks. The ladder to design: rung 0 (Jev within budget) → rung 1
(exact-cache / second-provider with a *tighter* budget inside the remaining
2700 ms) → baseline page. Each rung clears the golden eval suite before it
can serve.

**G4. Flip signal is latency-confounded.** ~89% of the Oct-4 flip signal was
latency artifact, and flip rate (1.3–2.2% baseline) is not monitored
disentangled from budget-breach rate. Fix: track budget-breach rate and flip
rate as separate first-class metrics; sample decision quality weekly with a
judge against the human-oncall's eventual resolution as the delayed label
(point-in-time capture: every decision logged with model version,
fingerprint, latency, outcome).

**G5. Eval harness is pre-production folklore, not CI.** No frozen golden set
of alert traces, no held-out split, no calibration of Jev-as-judge against
human-oncall judgment (κ bar), no shadow eval before judge/budget/fallback
changes, no rotation discipline. The Oct-4 A/B traces are the seed corpus;
the FDE pre-flight checklist (Q2) is the audit to run before any judge-path
change ships.

**G6. No cost SLO and no silent-drift watch.** Per-call Jev spend is
attributable but unattributed; there is no warn/throttle/stop circuit breaker
on spend, and no weekly golden-benchmark cron against production config to
catch silent provider drift behind the pinned `jev-1.13.0` string. Judge and
shadow-eval spend must live in the same cost view as application spend.

**Priorities, in order:** G3 (the missing rung is the availability story),
G5 (without the harness, rung 1 is fallback theater), G1 (write the warrant
the design already leans on), G4 (disentangle the metric the A/Bs are judged
on), G2/G6 (recovery discipline and cost/drift watches). None of this changes
the fail-open direction or puts a model on the boundary — the boundary itself
is sound; it is the surrounding discipline that is missing.
