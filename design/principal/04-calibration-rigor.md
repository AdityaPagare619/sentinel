# 04 — Calibration Rigor: The Mathematics of Trust

*Oracle · principal-redesign wave · 2026-10-03.*
*Status: DESIGN. No code. The latency/flip measurement campaigns are HALTED —
their rigor is specified here as pre-registered protocols (§4, §5) to be executed
when the wave lifts.*

This document is the mathematical contract underneath every probability,
threshold, and metric Sentinel shows a human. It answers, from first principles:

1. What "calibrated" *means* for a page-or-suppress gate (§1) — not the slogan,
   the definition, the conditioning, and the reference class.
2. Exactly where deterministic code ends and Jev's judgment begins (§2) — the
   boundary, what breaks when it blurs, and why moving it is a Type 1 decision.
3. How much data a calibration claim needs before it is credible (§3).
4. How we will measure latency and flip-rate *when* measurement resumes (§4, §5).
5. What calibration means under Law 2 — corruption and distribution shift (§6).
6. The three rigor choices we make differently from the industry (§7).

It builds on: the Seven Laws (`00-laws.md`); Oracle's mandate (`chiefs/ORACLE.md`);
Ledger's calibration harness (`src/sentinel/evalharness.py`, 10-bin ECE);
the Jev behavior note (`research/jev-behavior/2026-10-02-latency-fallbacks.md`);
the constraint registry (`ops/constraint_registry.md`).

---

## §1. What "calibrated" means — from first principles

### 1.1 The textbook definition, then the peel

The standard definition (van Calster et al., classifier-calibration survey):
a probabilistic classifier $f: \mathcal{X} \to [0,1]$ is **calibrated** iff

$$P(Y = 1 \mid f(X) = s) = s \qquad \forall s \in [0,1].$$

Law 4 (five whys) demands we peel this before designing on it.

**"Calibration conditional on what?"** The conditioning event $\{f(X) = s\}$ is
*the forecaster's own output* — not the state of the world, not the alert's
features, not "alerts that look severe." Calibration is a **self-consistency**
claim: *among the times I said s, the world said yes s of the time.* This
self-reference is precisely what makes calibration empirically checkable at
all (Dawid 1982, "The well-calibrated Bayesian"): it can be verified from
(forecast, outcome) pairs alone, with no access to the true data-generating
process. The moment you condition on anything else — on features, on
"similar" alerts, on your beliefs about the world — you are no longer
measuring calibration; you are measuring something uncheckable.

**"Over which reference class?"** The empirical check needs a *reference
class* — the set of (forecast, outcome) pairs over which frequencies are
computed (the Reichenbach/von Mises reference-class problem). "Jev is right
90% of the time at 0.9" is meaningless until the class is named. For our gate,
every calibration claim names its class explicitly:

$$R(C, W, a) = \{\text{alerts from customer } C,\ \text{in window } W,\
\text{where the gate took action } a\}.$$

The suppression claim — the only one that touches money — is then:

$$P(\text{true SEV1} \mid \text{alert} \in R(C, W, \text{suppress}))
\;\approx\; \hat{r}_{\text{suppress}} \pm \text{CI},$$

where $\hat{r}$ is the observed false-suppress rate. Note what this is *not*:
it is not "Jev says 0.9 and is right 90% of the time." It is a claim about
**the action's error rate over a named population**.

### 1.2 Two levels: instrument calibration vs decision calibration

Sentinel has two distinct calibration objects, and conflating them is the
most common failure mode in this space:

- **Level 1 — the instrument.** Are Jev's reported $P(\text{SEV1} \mid
  \text{state})$ numbers calibrated on our alert distribution? Measured by
  the reliability of Q1 probabilities against adjudicated severity. This is
  a property of *Jev on our data*. We cannot fix it; we can only measure it
  (cf. §7, choice 2).
- **Level 2 — the decision.** Is the *gate's action* calibrated — does
  $P(\text{true SEV1} \mid \text{suppress})$ match the risk budget the
  threshold was derived from? This is a property of *our gate*, and it is
  the certification target.

The threshold derivation that connects them: suppress (predict negative)
iff the expected cost of suppressing is below the expected cost of paging.
With $C_{FP}$ the cost of a false page and $C_{FN}$ the cost of a missed
SEV1, the Bayes-optimal rule suppresses iff

$$P(Y=1 \mid x) < \frac{C_{FP}}{C_{FP} + C_{FN}} \;\equiv\; t^*.$$

(Oracle's mandate: "$p^* = C_{FP}/(C_{FP}+C_{FN})$ is the anchor.";
van Calster et al.: with calibrated probabilities, decision thresholds
adjust "in a straightforward way to account for different class priors or
misclassification costs.") With the founder-grade estimates
$C_{FP} = \$100$, $C_{FN} = \$50{,}000$:

$$t^* = \frac{100}{100 + 50{,}000} \approx 0.001996 \approx 0.002.$$

**The triple lock's $P(p_1) < 0.002$ leg is not a chosen number. It is the
Bayes-optimal decision threshold under the cost model — and it is valid
only if the probabilities fed into it are calibrated.** An uncalibrated
0.002 means nothing; a calibrated 0.002 makes the expected-cost arithmetic
sound. *Calibration is what licenses the threshold math.* That sentence is
the bridge between this section and §2.

### 1.3 Decision calibration: the right target (Zhao et al. 2021)

Full **distribution calibration** — "among inputs receiving predicted class
probabilities $q$, the actual distribution over classes is $q$" — requires
sample complexity exponential in the number of classes $C$, and is
infeasible for us (Zhao, Kim, Sahoo, Ma & Ermon, *Calibrating Predictions
to Decisions*, NeurIPS 2021). Their result: a predictor is distribution
calibrated **iff** it is *decision calibrated* with respect to **all**
decision-makers — but restricted to decision-makers with a **bounded number
of actions** (polynomial in $C$), decision calibration becomes feasible via
a recalibration algorithm with polynomial sample complexity.

Our gate is a decision-maker with **2 actions** (page/suppress) and a fixed
loss matrix. We therefore do not need — and do not claim — distribution
calibration of Jev's full Choice distribution. We need **decision
calibration of the suppression action with respect to our loss**: no
downstream use of the gate's decision can distinguish Jev's predicted risk
from the true risk *through the costs we actually pay*. Concretely: the
certification metric is the false-suppress rate with a Wilson interval over
$R(C, W, \text{suppress})$, not global ECE. The harness's Q1/Q3 ECE
(Ledger's 10-bin) remains a *diagnostic* of the instrument; the
*certificate* is the decision-region rate.

### 1.4 Proper scoring rules: how we compare engines honestly

When we compare probability quality across engines — Jev direct vs
gateway-routed Jev vs the Laya encoder exit — the comparison metric must be
a **strictly proper scoring rule** (Gneiting & Raftery 2007, JASA):
$S(Q,Q) \ge S(P,Q)$ for all $P, Q$, with equality iff $P = Q$. The Brier
(quadratic) and log scores are strictly proper; ECE is not (it is a
diagnostic of the reliability term, cf. Nixon et al. 2019 on ECE's
limitations). Properness means the honest engine wins — no hedging strategy
scores better than truthful reporting. We therefore:

- evaluate engine probability quality with **Brier score** (Murphy
  decomposition: Brier = reliability − resolution + uncertainty; we track
  the reliability term as the calibration signal and resolution as the
  sharpness signal — "the sharper the better, subject to calibration");
- use ECE/reliability diagrams only as *diagnostics*, never as the
  engine-selection criterion.

### 1.5 A quantization consequence (wire fact → math fact)

Jev's wire format rounds probabilities to **0.01** (`chiefs/ORACLE.md`).
Therefore the triple-lock leg $P(p_1) < 0.002$ is satisfiable only by a
reported value of **0.00** — it is a *point condition*, not an interval.
There is no data between 0.00 and 0.01 on which to calibrate "the threshold
0.002 itself." Consequence: the decision-relevant calibration claim lives
entirely at the reported-0.00 mass intersected with the other two lock legs
(conf ≥ 0.90, allowlisted fingerprint). The conf leg does the continuous
work; the probability leg is a binary gate. Any calibration analysis that
treats $P(p_1)$ as a continuous input to the suppression decision is
measuring a fiction — the instrument quantizes it away.

---

## §2. The math-in-code boundary (Type 1 — Law 3)

### 2.1 The boundary, exactly

Deterministic code does **math, counting, and thresholds**. Jev answers
**contextual judgment questions**. Nothing else crosses in either
direction.

| Lives in CODE (deterministic, exactly reproducible, auditable) | Lives in JEV (contextual judgment only) |
|---|---|
| Threshold arithmetic: the triple-lock comparisons ($P(p_1)<0.002$, conf ≥ 0.90, allowlist match); the expected-cost optimum $t^*$; cost-ratio sensitivity tables ($C_{FN}$ = $50k vs $500k — the tuner shows it, the human decides) | Q1: severity judgment from the alert state (text, labels, topology context) |
| Probability aggregation: the fixed, versioned formula combining Q1/Q3 answers into the disposition probability | Q3: disposition judgment (page/suppress) with probabilities |
| All calibration statistics: binning, ECE, Brier decomposition, Wilson/Clopper-Pearson intervals, flip-rate estimates, latency percentiles | Q2: team-routing judgment |
| Counting: audit-log suppression rates, coverage, false-suppress counts, denominator discipline (Oracle mandate 1: no number without a denominator, an N, and an error bar) | — |
| Timeouts, retries, backoff, rate-limit token buckets; the fail-open rule (Jev timeout/529/corrupt input → **page**, never suppress, never wait) | — |
| Fingerprint hashing for the allowlist; dedup/correlation keys; one-parallel-call-per-alert fan-out | — |
| Prompt *templates* (versioned artifacts) | Prompt *judgment* (the answers; sampled, not derived) |

Three clarifications that prevent the usual blur:

1. **The aggregation formula is code, and it is versioned.** If we change
   how Q1 and Q3 combine into the disposition probability, that is a
   schema-grade change to what "the decision" means — it invalidates every
   prior calibration certificate, because the certificates were measured on
   decisions produced by the old formula.
2. **Jev never computes a threshold, never aggregates across alerts, never
   counts.** "Is $P < 0.002$?" is a comparison — code. "What is the
   expected cost?" is arithmetic — code. "How many alerts did we suppress
   today?" is counting — code.
3. **Code never renders judgment on alert content.** No regex suppresses an
   alert; no heuristic re-scores severity; no "this customer is noisy, bump
   the confidence." Content judgment is Jev's monopoly. Code's monopoly is
   everything that must be exactly replayable.

### 2.2 What breaks when the boundary blurs

**B1 — Jev does math.** Ask Jev to compute the threshold comparison or the
expected cost, and the 1.3–2.2% flip floor now infects *exact* arithmetic: a
flipped comparison bit changes the action, and the audit log records a
sampled token where a derivation should be. Two violations: Law 7's data
pedigree ("every number traces to its source" — a Jev-computed threshold
traces to a sample, not a derivation) and the award's reproducibility gate
(rerun must match the selected submission within 1% — sampling noise in
the arithmetic path breaks exact replay).

**B2 — Code does judgment.** Hard-coded suppression rules ("alerts matching
pattern R are noise") encode a stale context as law. Under shift (Law 2)
the rule silently rots *while looking deterministic and therefore
trustworthy* — the most dangerous failure mode, because determinism
masquerades as rigor. The Law-6 pre-mortem exhibit: an allowlist
fingerprint hashed on check-name alone collides across two services, and
the 02:14 database-failover alert matches a known-noise pattern from the
cache cluster. That is code doing identity judgment badly. Judgment rots;
math doesn't — which is why each lives where it does.

**B3 — Code re-interprets Jev's numbers.** "Jev said 0.88; this customer is
noisy, record 0.95." The calibration instrument is now broken: every
reliability diagram downstream measures the *edited* numbers, the
calibration certificate is void, and no audit can detect it — the raw
answer is gone. This is why §7 choice 2 bans all post-hoc rescaling of
Jev's outputs (temperature, Platt, isotonic): the audit log records what
Jev actually said; all adjustment lives in the versioned gate formula,
where it is visible, diffable, and re-certifiable.

### 2.3 Why this boundary is a Type 1 decision (Law 3)

Moving the boundary changes three irreversible things:

1. **The audit-log schema** — what counts as "the decision": raw Jev
   answers + deterministic derivation (current), vs. a blended blob
   (blurred). Schema changes are not migratable in an append-only log.
2. **The reproducibility contract** — exact replay of (code version +
   recorded Jev answers) must reproduce the submission within 1%. The
   boundary defines what "replay" means.
3. **The paging-path architecture** — Jev is never on the counting path;
   timeout → deterministic fail-open. The platform's do-no-harm law
   ("platform never adds paging-path latency, fail-open") is downstream of
   this boundary.

A Type 1 decided like a Type 2 is a future incident (Law 3). This boundary
is therefore registered as **Type 1** in
`design/principal/09-decision-register.md`, with the alternatives
considered (Jev-side thresholding; code-side content heuristics) and the
dissent (the latency temptation: "just let Jev decide inline to save a
round trip") recorded and rejected — the round trip is not the bottleneck
Law 1 questions; the bottleneck is whether the decision is *certifiable*.

---

## §3. Minimum-N rules for credible calibration

### 3.1 Ledger's baseline (cited, not re-derived)

Ledger's harness (`src/sentinel/evalharness.py`) implements the Guo et al.
(2017) §3.1 convention: **10 equal-width-bin ECE** on Q1 severity and Q3
disposition, with per-bin $(n, \text{acc}, \text{mean\_conf})$ tables. Its
honest-limitations section states the operative rule of the campaign so
far: **"Real calibration needs 50–300 customer labels."** This section
derives *why* those numbers are what they are, and sharpens them into
decision-unit rules.

### 3.2 Per-bin credibility (the diagnostic level)

A bin's accuracy is a binomial proportion with standard error
$\mathrm{SE} = \sqrt{p(1-p)/n_b} \le 1/(2\sqrt{n_b})$. Reporting discipline:

| Bin count $n_b$ | Status |
|---|---|
| $n_b \ge 100$ | Reportable with error bar (SE ≤ 0.05 worst case) |
| $30 \le n_b < 100$ | Shown greyed, Wilson interval mandatory, never a point claim |
| $n_b < 30$ | Not a claim. Shown as "insufficient N." No point estimate on any dashboard. |

The information-theoretic floor for the ECE estimator is $n \ge 2B$ just
to be *defined* (arXiv:2405.15709) — necessary and laughably insufficient;
it is cited here only to forbid the "but the estimator ran" fallacy.

**Quantile bins over equal-width bins.** Dimitriadis, Gneiting & Jordan
(2021, *Stable reliability diagrams for probabilistic classifiers*, PNAS):
classical fixed-bin reliability diagrams are asymptotically **biased and
inconsistent** for the true calibration curve; with $m(n)$ bins on
empirical-quantile boundaries, each bin holds $\approx n/m(n)$ points,
estimation variance decays like $m(n)/n$ and squared bias like
$m(n)^{-2}$, with optimal $m \sim n^{1/3}$ (MSE $\sim n^{-2/3}$); the CORP
(isotonic) reliability diagram is preferable in finite samples. Practical
consequence for us: alert confidences are heavy-tailed (mass at the
extremes — the triple lock lives at 0.00 and ≥0.90), so fixed equal-width
bins starve the middle and over-aggregate the tails. **The harness keeps
10-bin ECE as the regression metric (continuity with Ledger's work);
dashboards and certification use quantile bins with consistency bars**
(Broecker & Smith 2007), which guarantee every displayed bin is populated.

### 3.3 The suppression tail (the certification level) — where the 300 comes from

The certification claim is an **upper bound** on $P(\text{true SEV1} \mid
\text{suppress})$. With **0 observed false-suppresses in $n_s$ suppression
decisions**, the 95% rule of three gives upper bound $\approx 3/n_s$. To
certify the tail below **1%** at 95% confidence with zero failures observed:

$$n_s \ge 300.$$

**Ledger's "300" is not a folk number — it is the rule of three at the 1%
tail.** With $k > 0$ observed false-suppresses, the Wilson score interval
replaces the rule of three. Minimum-N rules, stated in **decision units,
not alert units** (raw alert counts are vanity; the denominators that
matter are decisions and confirmed positives):

- **No suppression-tail calibration claim** with $n_{\text{suppress}} < 300$
  **or** $n_{\text{confirmed SEV1}} < 25$, whichever binds later. (The 25:
  the miss-rate side $P(\text{suppress} \mid \text{true SEV1})$ needs
  confirmed positives as its denominator; at $n=25$ the worst-case SE is
  $1/(2\sqrt{25}) = 0.10$ — the coarsest honest statement of "we rarely
  miss.")
- Below these floors, the gate may still *operate* (the triple lock is a
  sound decision rule under the cost model), but no calibration *certificate*
  is issued. Operation without a certificate is labeled as such — Law 7,
  product constitution: graceful degradation, never silent confidence.

### 3.4 What "50" means

The lower end of Ledger's "50–300" is the *instrument* floor: with ~50
labeled alerts you can detect gross miscalibration of Jev (Level 1) — a
reliability diagram that is visibly off-diagonal, a Brier score far above
the base-rate benchmark. You cannot certify the suppression tail with 50.
The two numbers measure different things; this document keeps them
separate so the wave-6 reader doesn't mistake a diagnostic for a
certificate.

---

## §4. Latency measurement protocol (pre-registered; campaign HALTED)

*Context: the campaign is halted by the principal-redesign wave — the rigor
of measurement is the task, not the running. What follows is the protocol
to be executed when the wave lifts, pre-registered so no analysis choice
can be made after seeing the data.*

### 4.1 The decision this gates

The timeout policy, and behind it the architectural question: is "inline
Jev" viable at all? Vendor spec claims 70–500ms end-to-end (US West Coast);
our N=1 real-key call measured **11,429ms** from India, likely cold start +
India→US round trip
(`research/jev-behavior/2026-10-02-latency-fallbacks.md`). Law 1's question
is not "how do we make the call faster" but "what if the wait didn't exist"
— the do-no-harm law already answers: the paging path never waits on Jev;
timeout → page. This protocol determines the timeout's *value* and whether
the inline assumption survives contact with our geography.

### 4.2 Estimands

1. Round-trip latency distribution: p50 / p95 / p99, **stratified** (§4.3) —
   never pooled across strata.
2. Cold-start distribution separately: first call after ≥30 min idle or
   fresh process.
3. Timeout-rate at candidate timeouts $\{2\text{s}, 5\text{s}, 10\text{s}\}$
   per stratum (Wilson interval) — the timeout policy is set from this, not
   from the mean.
4. 429/529 rate per route (design ceiling: **40 req/s** tight bound until
   the rate-limit discrepancy is resolved — the protocol must not itself
   trip limits: inter-call spacing ≥ 1/40 s with jitter, per-key budget
   logged).

### 4.3 Pre-registered strata

| Stratum | Levels |
|---|---|
| Thermal state | **cold** (first call after ≥30 min idle / fresh process) vs **warm** (keep-alive, ≥5 prior calls in session) |
| Time of day | IST working hours (10–19) / IST night / US Pacific 9–17 |
| Payload size | input-token quartiles (question count held fixed — vendor evaluates questions in parallel) |
| Route | direct `api.typesafe.ai` vs Vercel / Cloudflare / OpenRouter / Netlify gateways |
| Region | India box; US box if available |

**Confounds controlled and logged per call:** connection reuse (fresh vs
keep-alive, separately from thermal state); DNS/TLS handshake vs TTFB where
measurable; box 1-min loadavg; Jev-side backoff events — logged as their
own stratum, **never pooled** into latency percentiles (a 429-backoff
second is not a latency observation); clock discipline: round-trip only, no
one-way claims.

### 4.4 Pre-registration (binding)

- **N:** ≥100 warm calls per route for the headline percentiles (the
  halted campaign's N≥100); ≥30 cold starts; timeout-rate strata sized for
  Wilson half-width ≤ 0.03 at the observed rate.
- **Stopping rule:** fixed N per stratum. No peeking, no "collect until it
  looks good."
- **Outlier rule (pre-registered):** >60 s = infra incident: excluded from
  percentiles, counted in a separate incident rate. No post-hoc trimming.
- **Analysis plan:** stratified percentiles + Wilson timeout-rates +
  cold/warm mixture decomposition (what fraction of p99 is cold starts?).
  The *deliverable* is a timeout recommendation with the timeout-rate table,
  not a single "Jev latency" number — there is no such number; there is
  only latency *conditional on the stratum* (§1.1 again: name the reference
  class).

---

## §5. Flip-rate protocol (pre-registered; campaign HALTED)

### 5.1 Estimand

Per-answer **choice-flip probability** under identical (model, state,
questions): $P(\text{choice}_2 \ne \text{choice}_1 \mid \text{same input})$.
Prior: the halted campaign's measured **1.3–2.2%** non-determinism. Wire
facts shaping the design: **no seed/determinism parameter** (irreducible —
`chiefs/ORACLE.md`); probabilities rounded to 0.01, so flip *detection* is
on **choice**, never on probability equality.

### 5.2 Design

- **$M$ distinct alert states × $K$ repeats:** $M \ge 20$ states stratified
  {clear-SEV1, clear-noise, boundary} × $K \ge 50$ repeats. The *pooled*
  rate ($n = M \cdot K \ge 1000$) gives SE ≈ 0.0044 at 2% — the headline.
  Per-state rates are not individually credible at $K=50$ and are used only
  for the heterogeneity test below.
- **Randomized order, spacing ≥60 s** between repeats of the same state
  (session/statefulness confound); wall-clock logged.
- **Heterogeneity test:** $\chi^2$ across states — are flips i.i.d. per
  call or **state-dependent**? If state-dependent (some states flip-prone),
  the "second call as tiebreaker" math changes: a second call on a
  flip-prone state carries less information, and redundancy must be
  allocated accordingly.
- **Decision-flip rate**, not just answer-flip rate: $P(\text{gate action
  flips})$ — the deterministic threshold layer (§2) converts probability
  jitters into decision stability, and it is the *decision* flip that
  threatens the award's 1%-rerun reproducibility gate. Report both; certify
  on the decision.

### 5.3 Decision use

1. **Reproducibility bound:** the flip floor is irreducible and is said
   plainly everywhere (Oracle mandate 2). The mitigation is architectural,
   not statistical: deterministic thresholds + triple lock mean small
   probability jitters don't change the action — decision calibration (§1.3)
   absorbs what distribution calibration cannot.
2. **Value of redundancy:** expected value of a second Jev call =
   $P(\text{first wrong} \land \text{second right} \land \text{disagree})$ —
   computable only from the *joint* flip structure the heterogeneity test
   reveals. No second call is budgeted until this is measured (constraint:
   one parallel call per alert).

---

## §6. Law 2 — calibration under corruption and distribution shift

*Eternal friction: the network drops, data corrupts, APIs change
unannounced. A calibration certificate that assumes a clean, stationary
world is a demo artifact, not a measurement.*

### 6.1 What the literature says

Ovadia et al. (2019, *Can You Trust Your Model's Uncertainty?*,
NeurIPS): "calibrating on the validation set leads to well-calibrated
predictions on the test set, [but] **does not guarantee calibration on
shifted data**." Worse: under shift, "nearly all other methods (except
vanilla) perform better than the state-of-the-art post-hoc calibration
(Temperature scaling) in terms of Brier score." Translation for us: our
calibration certificate is **conditional on the reference distribution**
(§1.1); it does not survive the shift — and the fancier the post-hoc fix,
the less it survives. (Related: Wald et al. 2021 on calibration and
out-of-domain generalization; Yu et al. 2022 on multi-domain temperature
scaling as a research direction, not our solution.)

### 6.2 Design consequences (named adversaries)

1. **Every calibration report names $R(C, W)$**: customer, window, alert
   mix, base rate. A number without its reference class is a rumor with
   formatting (Oracle mandate 1). The report also carries a **validity
   window** — the certificate expires; expiry is a field, not an
   afterthought.
2. **Shift monitor, pre-registered:** confidence-histogram drift +
   base-rate monitor (already in Oracle's weekly ritual) + a PSI-style
   distributional distance on alert features. Thresholds set before
   deployment, not after the first incident.
3. **Degradation policy:** shift beyond threshold → calibration claims
   marked **STALE** (never silently trusted); the gate **widens toward
   page** — uncertainty pages is the constitutional default, and under
   detected shift the allowlist leg (deterministic, human-audited,
   shift-resistant) carries more of the suppression burden while the
   probability leg is treated as suspect.
4. **The tail problem:** $P(\text{true SEV1} \mid \text{suppress})$ is a
   rare-event claim, and shift hits tails first. This is the deepest reason
   the triple lock has a deterministic leg at all: the allowlist fingerprint
   is **not learned**, so it does not silently rot under shift — Law 7's
   "deterministic guardrails over probabilistic models," instantiated.
5. **Corruption (the other half of Law 2):** malformed payloads, truncated
   state, Jev 529/timeout — the fail-open rule (**page**) is the
   deterministic answer. Calibration math never runs on corrupted inputs:
   garbage in → page, not a wider interval. A confidence interval around a
   corrupted observation is numerology.

---

## §7. Creative application — the three rigor choices we make differently

**Choice 1 — We calibrate the decision, not the distribution.**
The industry reports global ECE and stops. We certify
$P(\text{true SEV1} \mid \text{suppress})$ over a named reference class
with Wilson bounds (Zhao et al. 2021: with bounded actions, decision
calibration is feasible where distribution calibration is not). It is the
only calibration claim that touches money, and the only one whose sample
complexity we can afford.

**Choice 2 — We never rescale Jev's probabilities; we calibrate the gate.**
No temperature scaling, no Platt, no isotonic on Jev's outputs — ever.
Three reasons: (a) post-hoc calibration doesn't survive shift (Ovadia et
al. 2019 — the fancier the fix, the worse the transfer); (b) Law 7 data
pedigree — the audit log must record what Jev *actually said*, or the
instrument contract (§2, B3) is void; (c) rescaling breaks the traceability
the award's reproducibility gate requires. All adjustment lives in the
versioned deterministic gate formula — the tuner moves *thresholds*, never
probabilities. (This codifies existing practice as law — Law 5: name the
fence before you claim it.)

**Choice 3 — Every measurement is pre-registered, and every calibration
number expires.** Latency and flip campaigns ship as protocols — N,
strata, stopping rule, exclusion rule, the decision each gates — *before*
data collection resumes. Every calibration report carries its reference
distribution, its N, its error bars, its shift-check status, and its
validity window; stale claims are marked STALE, never silently trusted.
And on dashboards we use quantile bins with consistency bars (Dimitriadis
et al. 2021; Broecker & Smith 2007) — keeping Ledger's 10-bin ECE only as
the regression metric — because fixed equal-width bins are asymptotically
biased and starve exactly the tail we certify.

---

## Sources

- Gneiting, T. & Raftery, A. E. (2007). Strictly proper scoring rules,
  prediction, and estimation. *JASA* 102(477), 359–378.
  DOI: 10.1198/016214506000001437.
  https://stat.uw.edu/raftery/Research/PDF/Gneiting2007jasa.pdf
- Guo, C., Pleiss, G., Sun, Y. & Weinberger, K. Q. (2017). On calibration
  of modern neural networks. *ICML*.
  https://ctsilva.github.io/2026-VisML-CDS/refs/Guo_Pleiss_Sun_Weinberger_2017_Calibration_Modern_Neural_Networks.pdf
- Naeini, M. M., Cooper, G. & Hauskrecht, M. (2015). Obtaining well
  calibrated probabilities using Bayesian binning into quantiles. *AAAI*.
  (ECE metric.)
- Ovadia, Y. et al. (2019). Can you trust your model's uncertainty?
  Evaluating predictive uncertainty under dataset shift. *NeurIPS 2019*.
  http://arxiv.org/pdf/1906.02530
- Zhao, S., Kim, M. P., Sahoo, R., Ma, T. & Ermon, S. (2021). Calibrating
  predictions to decisions: A novel approach to multi-class calibration.
  *NeurIPS 2021*.
  https://proceedings.neurips.cc/paper/2021/hash/bbc92a647199b832ec90d7cf57074e9e-Abstract.html
- Dimitriadis, T., Gneiting, T. & Jordan, A. (2021). Stable reliability
  diagrams for probabilistic classifiers. *PNAS* 118(8):e2016191118.
  https://www.pnas.org/content/118/8/e2016191118
- Broecker, J. & Smith, L. A. (2007). Increasing the reliability of
  reliability diagrams. *Wea. Forecasting* 22, 651–661.
  doi:10.1175/WAF993.1 (consistency bars.)
- Nixon, J. et al. (2019). Measuring calibration in deep learning. *CVPR*.
  (ECE limitations.)
- van Calster, B. et al. Classifier calibration: a survey on how to assess
  and improve predicted class probabilities. *Machine Learning* (Springer).
  https://link.springer.com/article/10.1007/s10994-023-06336-7
  (formal binary-calibration definition; cost-adjusted thresholds.)
- Dawid, A. P. (1982). The well-calibrated Bayesian. *JASA* 77(379),
  605–610. (self-referential checkability of calibration.)
- Wald, Y., Feder, A., Greenfeld, D. & Shalit, U. (2021). On calibration
  and out-of-domain generalization. *NeurIPS 2021*.
- Yu, Y., Bates, S., Ma, Y. & Jordan, M. I. (2022). Robust calibration
  with multi-domain temperature scaling. arXiv:2206.02757.
- ECE estimator sample floor ($n_e \ge 2B$): arXiv:2405.15709.
  https://arxiv.org/pdf/2405.15709

### Internal

- `design/principal/00-laws.md` — the Seven Laws (Law 2 eternal friction;
  Law 3 Type 1 vs Type 2; Law 4 five whys; Law 5 Chesterton's fence;
  Law 6 pre-mortem; Law 7 four constitutions).
- `research/jev-behavior/2026-10-02-latency-fallbacks.md` — N=1 11,429ms;
  70–500ms vendor spec; 40 req/s tight bound; gateway fallbacks; Laya exit.
- `src/sentinel/evalharness.py` — Ledger's 10-bin ECE harness; honest
  limitations ("50–300 customer labels"; flip injection as crude stand-in).
- `ops/constraint_registry.md` — triple lock; one parallel Jev call per
  alert; fail-open; no accuracy claims, only calibration metrics on the
  customer's own labels.
- `chiefs/ORACLE.md` — Oracle mandate (denominator/N/error bar; flip floor
  irreducible; $p^* = C_{FP}/(C_{FP}+C_{FN})$ anchor); wire facts
  (255-choice ceiling, 0.01 rounding, noul clamp, no seed).
