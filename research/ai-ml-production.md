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
needs its own held-out validation (Ham
...[truncated 18155 chars]