# ML / JEV DOMAIN RESEARCH — Machine Learning Advancements Applicable to Sentinel

**Lane:** domain-research-ml · **Date:** 2026-10-06 · **Mode:** research only, web-first, ours-vs-real-world
**Branch:** `lane/domain-research-ml`

## The control principle (read first)

Aditya's standing law: Jev is used very normally/casually — the **control sits across the entire architecture**.
Jev advises; the deterministic gate decides. Every recommendation in this document was checked against one
question: *does the deterministic gate still own the decision?* Nothing here proposes letting the AI decide more.

## What Sentinel does today (the baseline this research is measured against)

- **Pipeline:** INGEST (authenticated webhooks) → NORMALIZE (vendor payload → canonical `Alert`) →
  **L3 CORRELATOR** (deterministic pre-Jev triage: fingerprint, dedup, episodes, flap/storm handling;
  internal law: *"never pay Jev for deterministic work"*) → **L4 GATE** (deterministic policy kernel +
  instruction firewall + stepped fail-open ladder + the Jev race) → FORWARDER (PagerDuty delivery).
- **Jev:** an AI decision model (TypeSafe Jev, pinned `jev-1.13.0`) used as a judge — reads the alert, returns a
  judgment with a confidence score. Measured live on our path: p50 816 ms / p95 1312 ms / p99 1526 ms
  (`research/jev-behavior/latency-report-2026-10-03.md`); 1.3–2.2% observed output flips.
- **The race:** Jev races a 2700 ms bounded timer. A judgment that arrives in time is **advisory** to the gate.
  Timeout, error, or overload → the gate fails **open toward paging a human**. Late Jev answers become
  powerless shadow evidence only.
- **Fail direction:** uncertainty, staleness, overload, vendor errors → PAGE. Suppression must be earned:
  fast, confident, corroborated, fresh evidence, signed policy.
- **Boundary machinery:** Jev circuit breaker (opens on N consecutive budget-busts/latency drift; half-open
  probing), cost circuit breaker (warn/throttle/stop on spend), exact-cache on `input_sha256`.
- **Prior internal research:** `research/ai-ml-production.md` (Simplex boundaries, eval harnesses, drift discipline).
  This document builds on it; it does not repeat it.

## How to read this document

Six topics. Each: **(1)** state of the art, sourced — actual algorithms and mechanisms, not vendor poetry;
**(2)** what we do; **(3)** the gap; **(4)** principled recommendations that keep control in the architecture.
Vendor claims are verified or labeled as marketing. The document ends with 5 ML truths for Sentinel and the
honest-gaps section.

---

## Topic 1 — Alert correlation / dedup: what is beyond basic dedup?

### (1) State of the art, sourced

The industry stack is a layered pipeline, nearly identical everywhere: raw events → dedup (fingerprint collapse)
→ correlation/clustering into incidents → human or automation. The differentiator is the clustering layer, and
honestly, the deterministic layer is where most of the real value lives.

**Prometheus Alertmanager (open source — the most transparent reference implementation):** identity is an
**FNV-64a hash over the sorted label set** of an alert (Prometheus dev list, 2023). Grouping via configured
`group_by` label lists; a `groupKey` (hash of group_by values) identifies each aggregation group; timing
parameters `group_wait` / `group_interval` / `repeat_interval` bound notification cadence. **Inhibition rules**
suppress an alert when another (the "matcher") is firing — topology-free dependency suppression done entirely with
deterministic label matching. All configuration, fully auditable, zero ML.

**PagerDuty Event Intelligence:** per service, operators choose **Intelligent** grouping (ML on alert summary text
similarity, source overlap, timing, historical co-occurrence), **Content-based** (field matches), **Time-based**
(alerts within a duration), or off. The time window is the key knob: ~5 min for fast storms, ~30 min for slow
degradations. "Probable Origin" and "Past/Related Incidents" surface ML hints. PagerDuty claims ~87% incident
reduction for AIOps early-access customers (SiliconANGLE, Apr 2023) — **vendor-reported, no published methodology;
treat as marketing.** No engineering detail on the actual models has ever been published: no architecture, no
training data, no evaluation metrics. Telling: **Event Orchestration** sits *underneath* all of it as a deterministic
rule engine — the vendor's own answer to reliability is deterministic rules, not the ML.

**BigPanda:** documented pipeline is normalize → merge events into alerts → cluster into incidents using
(1) source system, (2) tags, (3) time window, (4) optional filter — deterministic pattern matching. Published
hard numbers: max 300 alerts per incident, 2000 tags per alert (docs.bigpanda.io, crawled Sep 2026). The "ML"
lives only in executive press quotes; the docs describe a deterministic pattern engine. Their one good doctrine:
"noise is telemetry without adequate metadata" — correlation quality is dominated by enrichment/tag quality,
not algorithm cleverness.

**Moogsoft / Dell APEX AIOps:** the most algorithmically honest vendor. On-prem v9.x uses **Sigalisers**
(clustering plugins): **Cookbook** is an explicitly **deterministic clustering algorithm** — alerts clustered into
Situations by time, topological proximity, class/type, description, server priority, geography, tuned via
*Recipes* (multiple recipes run concurrently); **Tempus** is time-based, clustering on **similarity of event
arrival patterns** (coincident arrival shapes, e.g., simultaneous availability failures); **Vertex Entropy** is
graph-theory node-importance over topology data. Even the community skill file warns: *"don't trust clustering as
proof of causation — it's a strong hint, not a verdict"* — exactly Sentinel's advisory-ML posture.

**Academic consensus:** three correlation families — **similarity-based** (attribute/Jaccard similarity;
e.g., arXiv 2312.01219, hierarchical similarity+graph model, 87% reduction on DARPA99), **graph/topology-based**
(dependency paths, blast radius), **temporal** (co-occurrence windows, burst patterns) — plus the finding that
similarity-at-ingest (correlating events *before* classification) beats correlating alerts after the fact.
Attribute-weight thresholds around **0.5–0.7 similarity** are the empirically stable band (Atlantis Press, 2017).
Transfer caution: these are IDS/network-security papers, not paging systems.

**What genuinely works at scale vs marketing:** works = deterministic fingerprint dedup (FNV-64a over canonical
label set), `group_by`-style grouping, inhibition rules, time windows tuned per failure mode, topology proximity
*when you actually have topology data*. Marketing = any ML grouping model whose architecture, training data, and
evaluation are unpublished. Where ML legitimately helps: learning attribute weights/similarity thresholds from
responder feedback *offline* and deploying them as frozen deterministic recipes — Moogsoft's Cookbook recipe
model and PagerDuty's "historical co-occurrence" idea without the black box.

### (2) What we do

L3 correlator: deterministic fingerprint, dedup, episodes, flap/storm. "Never pay Jev for deterministic work."
Jev never touches grouping.

### (3) The gap

1. **Canonical fingerprint discipline:** Alertmanager excludes volatile fields (e.g., `startsAt`) from the
   fingerprint — a flap that resolves and refires keeps episode continuity. The *canonicalization* (which fields
   participate, sort order, normalization of volatile fields) is the entire game for dedup correctness.
2. **Inhibition rules:** we have no analog of "if the host-down alert is firing, fold the 47 service alerts behind
   it" — deterministic dependency suppression via label matching, no ML needed.
3. **Cookbook-style recipe framework:** Moogsoft runs multiple deterministic recipes concurrently
   (same-service+app, same-host+location, class+description) — a learnable, auditable ensemble. We have one
   correlator path.
4. **Tempus-style arrival-pattern clustering:** grouping by *similarity of arrival patterns* (coincident bursts
   from different sources) catches cross-stack availability failures that attribute matching misses.
5. **Topology proximity:** the single largest structural gap vs. vendors — defensible (we don't ingest topology),
   but the gap is real.
6. **Multi-window grouping:** PagerDuty's 5-min vs 30-min split (storm vs. slow degradation) — one window cannot
   serve both failure modes; a single episode window is a design smell.

### (4) Recommendations (gate owns every decision)

1. **Canonical fingerprint spec:** FNV-64a (or truncated SHA-256) over the sorted, normalized canonical Alert
   fields, explicitly excluding volatile fields (timestamps, counts, last-seen). Deterministic, auditable,
   testable. (Lifts Alertmanager's proven scheme.)
2. **Cookbook-style deterministic recipe framework in L3:** a small set of named, human-authored recipes
   (exact-fingerprint dedup → same-source+time-window → inhibition rules → description-similarity threshold)
   evaluated in order, each emitting its own audit trail. Recipes are config, not code. ML may sit *behind* the
   gate only as an offline recipe proposer — suggest weights from responder feedback; never promote without a
   red-team LGTM and a shadow period.
3. **Inhibition-rule support in the L4 policy kernel:** deterministic dependency suppression —
   `if alert A (host/node down) firing and in-episode, fold alerts B..n matching labels X into the same
   incident.` The cheapest approximation of Moogsoft's Vertex Entropy without a CMDB.
4. **Multi-window episode model:** short window (~5 min, storm cohesion) + long window (~30 min, slow
   degradation), both deterministic. Tempus-style arrival-pattern similarity (normalized inter-arrival vectors)
   is the legitimate home for one *frozen* similarity function — self-calibrated per tenant from their own
   history, never month-tuned constants.

---

## Topic 2 — Flap / storm detection in realtime streams

### (1) State of the art, sourced

**Flap detection — Nagios/Icinga (the canonical open implementation):** keeps the last 21 check results; a state
change is recorded when a result differs from its chronological predecessor; **percent state change is computed
with recency weighting — the newest change carries 50% more weight than the oldest**; hysteresis thresholds:
enter-flap at 20.0%, exit-flap at 5.0% (Icinga docs, crawled Oct 2026). While flapping, notifications are
suppressed and a flapping flag is attached. Principled ideas: **hysteresis** (enter at 20, exit at 5 — prevents
threshold chattering) and **recency weighting** (a flap *right now* matters more than one 20 checks ago).
The 5/20 numbers are round-number vibes; the *structure* is principled.

**Dampening — exponential penalty decay (BGP Route Flap Dampening, RFC 2439 heritage):** penalty +1024 per flap,
exponential decay by half-life, suppress threshold 2500, reuse threshold, **max-suppress-time** (hard ceiling on
suppression duration). Dell OS10 instance: suppress=2500, half-life default 5s. Three guarantees in one mechanism:
suppression proportional to intensity, automatic recovery, nothing suppressed forever. Caution from history: BGP
operators disabled flap dampening by default (RIPE-378, 2006) because wrong parameters amplified convergence
problems — dampening with wrong parameters is worse than none.

**Burst detection — Kleinberg (KDD 2002):** infinite-state HMM; inter-event gaps drawn from exponentials whose
expected value scales as s^-i in state i; parameters s (magnification, typically 2) and gamma (transition cost —
higher = bursts must be sustained longer). A burst is defined as deviation from the stream's *own baseline rate*,
not as exceeding a magic number. Two-state (normal/burst) is the pragmatic realtime form.

**The one vendor threshold derived from actual customer data — xMatters:** Alert Rate Filter default suppresses
alerts from the *same integration* targeting the *same recipients* above **four alerts per minute**. The docs
state explicitly: *"This default rule is based on our analysis of actual customer alert flood data"* and "in all
customer cases that we reviewed, a recipient receiving more than four alerts per minute about an incident is able
to gain better situational awareness by going to the source system" (help.xmatters.com, crawled Oct 2026).
Suppressed alerts don't count against licensed rate; other sources keep flowing — *per-source-keyed* flood
control, not global.

**Also in the record:** PagerDuty Events API rate limits (~120 events/min per key; AIOps up to 10,000/min) —
infrastructure self-protection our forwarder must respect; Micro Focus OMi storm suppression (>1000 events from
the same node in 5 min to enter, <100 to exit — hysteresis again, per-node keying again); Opsgenie tightening
escalation limits *because floods broke reliability* — an existence proof the fix is bounding the response
machinery, not smarter thresholds; Prometheus `for:` clause — the cheapest flap filter in existence.

**Principled vs vibes:** principled = hysteresis entry/exit thresholds, recency-weighted volatility,
penalty-decay-reuse dampening with max-suppress-time, baseline-relative burst detection (Kleinberg),
per-source-keyed thresholds derived from own traffic. Vibes = round-number counts per window with no exit
condition, global thresholds across heterogeneous sources, fixed windows that can't distinguish storm from slow
degradation.

### (2) What we do

L3 correlator handles flap/storm detection (implementation detail not re-verified in this lane); the fail
direction is page; suppression must be earned.

### (3) The gap

1. **Hysteresis:** every mature implementation uses separate enter/exit thresholds. A single count-per-window
   threshold oscillates at the boundary.
2. **Recency-weighted volatility:** Icinga's 1.5× newest weighting is simple, deterministic, defensible.
3. **Penalty-decay dampening with reuse + max-suppress-time:** the max-suppress-time is the piece most designs
   forget — it's what preserves "suppression must be earned" under sustained flap.
4. **Self-calibrated baselines:** xMatters' 4/min came from *their* customer data; Kleinberg's bursts are relative
   to the stream's own rate. Any constant we ship is a guess.
5. **Per-source-keyed flood control:** floods are local phenomena — a global count lets one noisy source DoS the
   paging path for everyone else.

### (4) Recommendations (uncertainty and suppression both route to PAGE; Jev never gates this layer)

1. **Recency-weighted percent-state-change flap scoring (Icinga's algorithm, deterministic):** last N state
   transitions per fingerprint; newest weighted 1.5× oldest; enter-flap at high threshold, exit-flap at low
   (hysteresis). While flapping, the episode's pages collapse into **one** notification — flap handling reduces
   page *count*, never page *existence*. Flap state is an annotation the correlator attaches, not a suppression
   decision.
2. **Exponential penalty-decay dampening for repeat storm sources (RFC 2439 structure):** penalty per flap
   event, exponential decay with half-life from the tenant's own check cadence, suppress threshold → per-source
   episode batching, reuse threshold → restore, **max-suppress-time** hard ceiling. All four parameters estimable
   from the target's own traffic — self-calibrating per the generalization law, not month-tuned constants.
3. **Storm detector keyed per fingerprint/source with self-calibrated threshold:** count-per-window with
   hysteresis; entry threshold = max(floor, k × baseline p99 rate) from the tenant's own distribution; exit at a
   fraction of entry. On storm entry the gate emits **one** incident-page ("storm in progress, N alerts/min from
   source S") instead of N pages — the fail-open-preserving equivalent of xMatters' flood control: the human is
   still woken, once, with scope attached. Storm mode **compresses; it never silences.**
4. **Kleinberg-style burst level as shadow-only triage signal:** burst level (normal/burst, high gamma so only
   sustained bursts register) computed per fingerprint in L3, attached as advisory metadata for Jev and the
   responder ("this episode is still rising / cooling"). It never gates a page.

---
## Topic 3 — Confidence calibration for page-or-suppress

### (1) State of the art, sourced

**What calibration means and how it's measured.** A classifier is calibrated if P(Y=+ | f(X)=s) = s — when it
says "80%," the event happens ~80% of the time (Springer survey, 2023). Three standard instruments:
- **Reliability diagrams** — bin predicted confidences, plot mean confidence vs. observed accuracy; a calibrated
  model traces the diagonal. Best practice: binomial error bars per bin plus a count histogram, else
  small-sample bins read as miscalibration.
- **ECE** = Σ (|B_m|/N)·|acc(B_m) − conf(B_m)|. Warning: a constant base-rate predictor has near-zero ECE and
  zero resolution — ECE alone is not a criterion. Compare against the constant predictor *and* against the model
  recalibrated (isotonic); if recalibration improves Brier, "the probabilities come ranked, not calibrated."
- **Brier score** = mean (f_i − o_i)², a proper scoring rule (truth-telling is the unique optimum).
  Real practitioner numbers (Vigil ML validation audit, LightGBM, N=56,514): raw ECE 8.91% → isotonic 5.32%,
  Brier 0.1926 → 0.1824 — ~40% calibration-error reduction from post-hoc recalibration on a well-behaved model.

**Thresholds are set by cost, not vibes.** The Bayes-optimal rule (Elkan 2001): for calibrated probability p,
page/suppress at **t\* = C_FP / (C_FP + C_FN)** — cost of a false page over total misclassification cost. You only
need the *ratio*, and it's invariant to scaling all costs. Teams that tune by gut oscillate between "too noisy"
and "we missed something" (Sherlocks.ai, Apr 2026).

**The bad news about LLM/judge confidence scores.** The published consensus is that aligned-LLM confidence is
**systematically overconfident, prompt-fragile, and at best ordinal — "ranked, not calibrated":**
- Tian et al. 2023 ("Just Ask for Calibration"): *verbalized* probabilities from RLHF models have ~50% lower ECE
  than token log-probs — but still imperfect.
- "Closing the Confidence-Faithfulness Gap" (Mar 2026): LLMs "tend to verbalize confidence scores that are largely
  detached from their actual accuracy," clustering near the top of the range; instruction tuning and RLHF
  *exacerbate* it.
- "Wired for Overconfidence" (Apr 2026): identifiable internal circuits that inflate verbalized confidence.
- A Dec-2024/May-2026 survey concludes reliability of verbalized confidence is "contested and poorly understood,"
  prompt-method dependent.
- Kadavath et al. (Anthropic, 2022): models are well calibrated on multiple-choice/True-False in the right format,
  but replacing an option with "none of the above" *reduces accuracy and calibration significantly* — models are
  strongly biased against using abstention options. Calibration breaks on OOD questions and long-form generation.

**The closest published work to our exact problem.** "Automated Alert Classification and Triage (AACT)"
(arXiv 2505.09843, 2025, Sophos MDR): gradient boosting on features encoding *analyst triage actions*, trained to
predict whether an alert enters an investigation. Deployed 6 months in a live managed SOC: ~3.1M alerts, **61%
auto-closed**. Two things right: (a) they stated the cost asymmetry explicitly — "missing true critical alerts has
a larger negative impact than incorrectly marking alerts as critical" — and chose a recall-favoring threshold;
(b) thresholds chosen off precision–recall/ROC sweeps on time-series cross-validation, not fixed. Scope note: this
is *security* triage (investigate vs. ignore), not SRE paging (wake vs. suppress) — but it's the nearest real
deployment with asymmetric costs + principled threshold selection.

**The formal machinery for "gate consumes an uncalibrated score": selective classification.**
Geifman & El-Yaniv (NeurIPS 2017; SelectiveNet, ICML 2019): a selective classifier is (f, g) — predictor f plus
gating function g: X→{0,1}; the system answers f(x) when g=1 and *abstains* otherwise. Two metrics: **coverage**
(how often it commits) and **selective risk** (error rate *among committed decisions*). The SGR algorithm
binary-searches a confidence threshold to *guarantee* a target selective risk with confidence 1−δ. The crucial
result: **the confidence score only needs to rank correctly (be monotone in true loss), not be a calibrated
probability.** A crudely ordered score plus the right abstention threshold still buys a bounded-risk contract.

**Cost context (real numbers, no universal ratio exists):** $5,600 average cost per minute of enterprise downtime
(incident.io 2025 survey); orgs spend $1.9M/yr on incident responders, ~$700K of it manual toil (PagerDuty 2024
whitepaper); 67–83% of engineers admit ignoring/dismissing alerts (incident.io survey; Apr 2026 study); 9.6M
observability events/year per enterprise, <18% ever acted upon (BigPanda 2025 report). Google SRE doctrine:
"Every page should be actionable" — target max ~2 pages per 12-hour shift.

### (2) What we do

Jev's judgment is advisory; a "confidence above bar" is required for suppression. Measured 1.3–2.2% flips —
hard proof the point estimates are unstable — but no reliability diagram, no ECE/Brier, no stated derivation of
the bar, no ambiguity-band discipline, no calibration drift monitoring.

### (3) The gap

1. **No evidence Jev's confidence is calibrated — assume it isn't.** If the gate reads it as a probability, the
   suppression bar is built on a miscalibrated number.
2. **No cost-ratio derivation for the bar.** The principled answer is t\* = C_FP/(C_FP+C_FN) with a risk–coverage
   curve; we have a bar with no published derivation.
3. **No abstention discipline.** With 1.3–2.2% flips, scores within a few points of the bar are coin flips — the
   contract should *abstain* (fail toward page), not decide.
4. **No calibration drift discipline** — no scheduled recalibration, no ECE monitoring, no automatic fallback.

### (4) Recommendations (gate owns abstention; Jev's raw score has no direct authority)

1. **Treat Jev confidence as ordinal rank, never probability.** Fit a post-hoc calibration mapping (isotonic
   regression) of Jev score → empirical P(correct) on labeled historical page/suppress outcomes; the gate consumes
   the *mapped value with its binomial confidence interval*, and anything within ±δ of the bar (δ sized to the
   measured flip rate) is untrusted → default PAGE. The mapping table, δ, and fail direction are deterministic
   gate config.
2. **Derive the suppression bar from a stated, reviewed cost ratio.** Publish C_FP and C_FN as *business
   constants*, set the bar at t\* = C_FP/(C_FP+C_FN), validate against a risk–coverage curve on historical
   labeled alerts. Frame it as a selective-risk contract: "we tolerate ≤0.1% wrongful-suppression rate and pay
   for it in suppression coverage" (Geifman & El-Yaniv SGR framing). The ratio is a business invariant, not a
   month-tuned constant.
3. **Two-key suppression rule (asymmetric quorum).** Jev confidence may only *enable* suppression when
   deterministic evidence is already suppression-consistent (freshness, signed policy, no overload); Jev can never
   suppress against deterministic evidence, and deterministic evidence alone can never suppress without its own
   signed-policy path. Plus hysteresis: scores that flip across the bar within N re-queries pin the episode to
   PAGE.
4. **Scheduled calibration monitoring with automatic fallback.** Weekly: recompute ECE/Brier of the calibration
   mapping on resolved alerts (ground truth arrives at incident resolution — "was this page real?" is knowable).
   If ECE drifts or suppression precision drops, the gate falls back to a conservative fixed bar and raises a
   maintenance signal — the confidence channel gets its Neural-Simplex-style reverse switch.

---

## Topic 4 — Anomaly detection as signal source (before the correlator vs inside the judge)

### (1) State of the art, sourced

**How Datadog Watchdog actually works — the verifiable parts.** Launched July 2018 as "zero-config" ML monitoring
(TechCrunch). The engineering-verifiable internals: three exposed anomaly algorithms — **basic** (lagging rolling
quantile, no seasonality), **agile** (seasonal decomposition, fast level-shift adaptation), **robust** (seasonal
decomposition, stable against long outages); seasonality at hourly/daily/weekly; tuning collapses to `bounds`
(stddev multiplier, recommended 2–3), directionality, seasonality, alert/recovery windows. **Outliers** (peer
comparison): DBSCAN or MAD. Watchdog's product surface: auto-generated "stories" in a dedicated inbox —
**findings, not pages**; turning a story into monitoring is an explicit user action. Beyond those monitor
algorithms, Watchdog's "ML" (Log Anomaly Detection, RCA, added ~2022) has **no published internals** — product
descriptions only. Datadog's honest open signal: **Toto**, a 151M-parameter decoder-only time-series foundation
model with the **BOOM** benchmark (350M observations, 2,807 real series), Apache 2.0 (arXiv 2505.14766, May 2025;
Toto 2.0 family 4M–2.5B announced ~May 2026) — but Datadog has not disclosed which production features (if any)
run on Toto. Honest read: today's Watchdog detection path is the classical decomposition/quantile family; the
transformer is research with declared integration intent.

**The industry-wide pattern: seasonal decomposition dominates; detection is decoupled from the paging decision.**
Across Datadog, New Relic (auto baseline, stddev-based), Grafana (Prophet-like additive), Dynatrace Davis
(auto-adaptive 7-day band), Elastic (decomposition + Bayesian modeling) — the shared default is **STL/Prophet/
Holt-Winters-style: trend + daily + weekly + residual, then band the residual**, streaming, on pre-aggregated
metrics. User-facing tuning everywhere collapses to one knob: a stddev multiplier on the residual band.
Log-pattern anomaly detection is a second-class subsystem everywhere — metrics first, logs second.

**Where the literature puts AD: signal source BEFORE correlation, never inside the judge.** The most consistent
architectural fact in the space: (1) detectors *produce events* — Watchdog stories, Davis "problems" precursors,
New Relic anomaly conditions; (2) a correlation/explanation layer *groups and decides* — Dynatrace Smartscape
topology collapsing anomalies into one "problem," PagerDuty Event Intelligence grouping. Cross-system finding:
"**Detection is decoupled from explanation.** Every system has two layers — 'here is an anomaly' and separately
'here is why / what else correlates.' Nobody has solved one-shot detect-and-explain." Production AIOps correlation
metrics: alert-volume-reduction ratio (healthy 70–90%), alert-to-incident ratio, correlation precision/recall,
operator split/merge feedback rate — with the warning that "raw alert reduction alone is a dangerous vanity
metric." **Nobody — no vendor, no paper found — puts the anomaly model inside the final wake-the-human decision;
the page/no-page authority always sits in the correlation + policy layer downstream.**

**Failure modes of ML anomaly detectors, published:** concept drift (TSAD challenge #1); deployments routinely
producing 20+ false positives for ~2h after deploy; Monday-morning FP bursts; week-one alerts unreliable (7-day
minimum learning; weekly models need 2–3 weeks training); **the detector can become the storm source** (missing-data
alerts must be disabled for sparse streams "or they alert-storm"); precision collapse under class imbalance
(ROC-AUC looks strong while precision collapses — the metric trap); anomaly/drift conflation; training-data
contamination.

### (2) What we do

No anomaly detection. Sentinel is purely reactive: it decides only on alerts vendors already fired. The
correlator's storm logic has no seasonal awareness.

### (3) The gap

1. **No proactive signal source.** A metric degrading 10 minutes before the vendor alert fires is invisible to us.
2. **Storm handling is seasonally blind.** Known-good patterns (Monday-morning ramp, deploy windows) can't be
   distinguished from genuine storms deterministically.
3. **No placement doctrine** for any future ML AD — and the real-world answer is unambiguous.

### (4) Recommendations (AD is a signal producer; the gate never consults it)

1. **AD belongs before the correlator, as one more alert producer — never inside the judge.** Any detector emits
   *candidate alerts* (score + expected band + model version) into INGEST/NORMALIZE; L3 treats them exactly like a
   vendor alert: fingerprint, dedup, episode-merge, flap/storm-handle. The gate never sees "the detector thinks
   page" — and all AD-originated alerts carry provenance. Control check: the gate's decision logic is unchanged;
   AD only widens the input aperture.
2. **Quota and corroboration for the untrusted source.** Hard cap on AD-originated pages per window (deterministic
   storm-breaker kills AD-derived alerts first); an AD alert contributes to a page only with independent
   corroboration (vendor alert or deterministic threshold breach, same service/window); **AD alerts can never earn
   suppression** — they only add evidence toward paging, matching our fail-open direction. The detector gets a
   deterministic kill switch: precision below bar or storm behavior → source disabled, pipeline continues on
   vendor signals alone.
3. **Seasonal baselines for the correlator's own storm logic — the cheapest deterministic win.** Apply STL-style
   seasonal decomposition (hourly/daily/weekly) to alert/page volume for *dynamic* storm thresholds. The
   industry's most proven technique, in deterministic code, no model in the loop — and it directly attacks the
   "detector causes the storm" failure mode by making our own storm logic season-aware.
4. **Track the detector's triage-outcome rate:** (confirmed incidents + new regression checks) / total flags,
   reviewed weekly; <20–30% → recalibrate, >60–70% → too conservative. Log every AD alert with model version +
   band parameters for post-incident replay. If we later evaluate Toto-class foundation models, this is the only
   honest scoreboard.

---
## Topic 5 — AI-judge-in-the-loop WITHOUT surrendering control

### (1) State of the art, sourced

**(a) Bounded-time judging.** Honest finding first: **nobody publishes a "bounded-time model-as-judge" pattern as
such** — what exists is the general LLM-resilience stack, and Sentinel's temporal race is *ahead* of published
practice, not behind. The established pieces: timeouts at every level with a total deadline around the invoke
(timeout composition order: attempt → timeout → retry → error handler); circuit breakers closed/open/half-open per
provider with auto-recovery via single in-flight half-open probe; hedged requests (duplicate after short delay,
first response wins) for tail-latency-bound idempotent calls; fallback models pre-tested against the real
workload, switchable by config; versioning — pin model + prompt versions, track per-version scores for compare
and rollback.

**(b) Abstention — when the AI says "I don't know."** The principled mechanisms:
- **Selective classification / risk-coverage curves** (Geifman & El-Yaniv lineage): plot risk vs coverage sweeping
  the confidence threshold, pick the operating point on the calibration split only. A production-grade demo:
  conformal abstention at τ\*=0.559 automated 67.5% of workload while halving error rate (20.38% → 9.98%).
  A three-signal gate (similarity ≥0.90, extraction confidence ≥0.70, then margin between top-2) beats
  single-signal designs. The **margin signal** (raw gap between top predictions) ranks ambiguity nearly as well as
  complex uncertainty methods.
- **Critical caveat (Kadavath et al., Anthropic, 2022):** models are well calibrated on multiple-choice/True-False
  in the right format — but replacing an option with "none of the above" *reduces accuracy and calibration
  significantly*; models are strongly biased against using abstention options. Calibration breaks on OOD
  questions, claims needing external verification, long-form generation.
- **Incident-domain calibration:** PACE-LM — "Prompting and Augmentation for Calibrated Confidence Estimation
  with GPT-4 in Cloud Incident Root Cause Analysis," FSE (Industry) '24 — the only incident-RCA calibration
  paper found. Real venue, Microsoft-adjacent.
- **Conformal limitations:** vanilla conformal gives a *marginal* guarantee, not per-group — the dominant failure
  mode is "confident hallucination on unanswerable questions."

**(c) Judge prompt discipline, 2024–2026.** Structured outputs with out-of-band schema validation; retry-on-invalid
(JSONSchemaBench, StructEval — 2025 benchmarks). Few-shot 2–5 diverse examples *including edge cases* beats clever
instructions; 5–10 expert examples at calibration, representative subset in production (Patronus AI). Binary
outputs outperform Likert scales for objective tasks (Yan et al., 2024); ask reasoning before the score;
temperature 0; pin judge model and prompt versions; use a **different model family** to avoid self-preference;
order-swap pairwise judgments — position bias can favor the first answer up to ~3/4 of the time; a real bench
measured 76.7% position bias and 75% length bias on a naive judge. Constraint discipline: prompts drop constraints
as generation lengthens; mechanical constraints (ranges, completeness, arithmetic) belong in code, qualitative
ones in the prompt — the confidence bar and output schema are deterministic-code jobs, not prompt text.

**(d) Eval harnesses for the judge itself.** Human agreement bar: **κ ≥ 0.6 ("substantial")** on the golden set as
the minimum before trusting judge scores; κ < 0.4 = fix the rubric or judge. Real deployments recompute κ on a
monthly re-sample to catch judge drift. Objective tasks: LLM judges reach κ 0.6–0.8; subjective: κ 0.4–0.6. A
perioperative clinical judge hit κ 0.852 vs physicians on a tightly bounded rubric. Ground truth must be
human-labelled; discard items annotators disagree on (the rubric is wrong, not the annotator); a judge from the
same family as the system under test shares blind spots. Judges are overconfident and poorly calibrated; scores
cluster at 4–5/5; malformed structured-output rates range 0.58%–45.5% depending on model, usually silently dropped
from metrics.

**(e) Production AI-triage case studies — the honest ledger:**
- **DeepTriage** (Microsoft/Azure, arXiv:2012.03665): *deployed*, thousands of teams daily; F1 0.829 overall,
  0.763–0.913 on high-impact incidents; ensemble of classical ML + deep models. **Real — but classical ML, not
  LLM, and it *recommends* teams; it doesn't page or suppress.**
- **RCACopilot** (Microsoft, EuroSys'24): 0.766 RCA accuracy on a year of real incidents; the diagnostic-information
  *collection* component live in production 4+ years — **real**, but the LLM prediction component's
  live-deployment status is not established by the paper.
- **CERT-EU intelligent ticket routing** (production, 6 months, 1900 tickets): 86.3% weighted F1, 5.5× routing
  speedup, 22× on high-confidence auto-routed. **Real** — ticketing, not paging.
- **Ramp OCA** (on-call assistant): 575 merged fixes across 1,220 incident PRs in 4 weeks, 37% fewer engineers —
  **self-reported company blog; no independent verification; PRs still need human review.**
- Vendor land: PagerDuty SRE Agent (GA Oct 2025, "up to 50% faster" — vendor claim, unverified); Datadog Bits AI
  SRE; Splunk ITSI Episode Summarization; AWS DevOps Agent (GA Apr 2026); incident.io AI Investigations;
  FireHydrant explicitly *not* building its own AI SRE — its CEO publicly says "I have yet to hear of a single
  company using it successfully" (2025).
- **Nobody found publishes an AI judge that decides page-or-suppress at the paging boundary with evaluated
  outcomes.** Every production case is advisory upstream of a human.

### (2) What we do

Jev advisory behind a 2700 ms race, timer-default fail-open to page; Jev + cost circuit breakers; exact cache on
`input_sha256`; suppression needs fast + confident + corroborated + fresh + signed policy. The deterministic
instruction firewall (ADR-020) and freshness proofs are code, not a second model — a genuine strength.

### (3) The gap

1. **Abstention is a gate, not a prompt request.** Kadavath's warning: models are *biased against* using abstention
   options, and verbalized confidence is the weakest signal. We currently read Jev's confidence as an emitted
   signal; the field's answer is thresholding cheap deterministic signals (margin p₁−p₂, confidence) with an
   operating point from a risk-coverage curve on labeled calibration data.
2. **No judge self-evaluation discipline.** Industry standard: golden set, human labels, κ ≥ 0.6 bar, monthly
   resample, three-way split. We have flip-rate measurements but no published κ for the Jev judge against
   human-labeled page/suppress decisions, no stated resample cadence.
3. **No recovery-of-the-advanced-layer discipline** — already flagged internally (Neural Simplex reverse
   switching); the 2026 resilience literature converges on the same point: half-open single-probe recovery,
   fallback models pre-tested against the workload.
4. **Conformal's trap:** marginal guarantees under-cover the subgroup that matters. A threshold picked on
   aggregate statistics can silently fail on the subgroup where wrong-suppress = missed SEV-1.

### (4) Recommendations (all keep the deterministic gate in ownership)

1. **Deterministic abstention gate, computed not asked.** Do NOT prompt Jev with an "abstain if unsure" option
   (models are biased against using it). Instead: log all four signals per judgment (max-probability, margin
   p₁−p₂, Jev `confidence`, deterministically-computed P(fit) boolean from the instruction firewall), fit a
   risk-coverage curve on human-labeled calibration data, pick τ\* on the calibration split only, enforce the
   threshold in deterministic code at L4. A Jev judgment below τ\* is treated identically to a race timeout:
   shadow evidence, fail-open to page. (This unifies with Topic 3's calibration-mapping recommendation — one
   gate, two readings: mapped probability for the bar, margin/threshold for abstention.)
2. **Judge κ harness with a monthly drift gate.** Golden set of ≥100–200 real alert→page/suppress decisions with
   human labels (on-call veterans); measure Jev-judge vs human Cohen's κ; ship only if κ ≥ 0.6. Re-sample monthly;
   κ < 0.6 or a ≥0.15 κ drop → automatic incident: gate ignores Jev advisory (treat all as timeouts) until
   re-calibrated. Version-pin jev + prompt + τ\* together; per-version scoreboard with rollback.
3. **Graded Jev re-entry after circuit-breaker trips** (the missing Neural Simplex reverse switch). Half-open
   probing with a single in-flight probe, then graded re-entry: probed answers enter shadow-only until K
   consecutive in-budget, in-agreement judgments, then advisory again. Log Jev availability (fraction of races
   won/advisory) as a first-class ops signal.
4. **Subgroup-conditional thresholds, not one global τ.** Stratify the calibration set (alert class, vendor,
   severity, novel-vs-known fingerprint); require τ\* to hold within each subgroup, or set per-subgroup bars.
   Suppress-side subgroups must never inherit a threshold calibrated mostly on page-side cases.

---

## Topic 6 — ML advancements on the horizon (2024–2026) for incident management

### (1) State of the art — honest maturity ratings

| Candidate | Evidence | Verdict |
|---|---|---|
| **LLM-based root-cause analysis** | RCACopilot (Microsoft, EuroSys'24): 0.766 on a year of real incidents; collection module live 4+ years. PACE-LM (FSE Industry'24) for calibration. RCAgent, VOCE (>80% originating-alert accuracy) — offline evaluations. | **REAL, but advisory and offline-evaluated.** The only deployed-at-scale piece is diagnostic *collection*, not the LLM's prediction. No published case of an LLM's RCA output driving an automated paging decision. |
| **Agentic incident responders** | PagerDuty SRE Agent (GA Oct 2025, "up to 50% faster" — vendor claim); Datadog Bits AI SRE; AWS DevOps Agent (GA Apr 2026); HolmesGPT (CNCF Sandbox, *read-only by design*, Operator mode opens fix PRs); K8sGPT (CNCF Sandbox, rule-based scanner + LLM explanations; named production users: Kubermatic, SpectroCloud); Aurora (Apache-2.0 multi-cloud agent); incident.io Investigations. Gartner projects 70% of enterprises deploy agentic AI for IT ops by 2029 (projection, not fact). FireHydrant CEO: no successful production use heard of (2025). JetBrains AI Pulse (Apr 2026): 78.2% of respondents don't use AI in CI/CD at all. | **DEMOS + EARLY-ADOPTER PILOTS, mostly.** Real code exists (HolmesGPT/K8sGPT inspectable), but autonomous *remediation* in production has no independently verified deployment. The pattern that ships: investigate → draft → human approves. |
| **Multimodal alert understanding** | DiagFusion (arXiv'23); Nezha (FSE'23); IJRAI survey notes multimodal text+image enriching routing accuracy. | **RESEARCH STAGE.** Papers, no deployment evidence at alert-ingress scale. |
| **Learned alert routing** | DeepTriage (Azure, *deployed*, thousands of teams daily, F1 0.829); DeepCT (Microsoft, 14 large systems); CERT-EU (production 6 months, 86.3% F1, 5.5× faster); incident.io AI triage (ownership + schedules + classification). | **REAL AND DEPLOYED** — the most mature ML application in incident management. But all route to *teams/humans*; none decides page-vs-suppress. |
| **Predictive paging** | Nothing found beyond vendor phrases. Closest real work: VOCE and classic AIOps anomaly detection. | **PRESS RELEASE, not product.** Ignore as a category; track as research. |

Notable: the open-source project that thinks most like Sentinel is **maxuver/sentinelops** (Sep 2026) — reflex
pipeline plus a *bounded* on-demand agent (tool calls, time, budget caps, read-only, no shell), publishing
measured accuracy *including where it fails*. The honest sibling to study.

### (2) What we do

Deterministic L3 correlator + L4 gate; Jev only advises within a 2700 ms race, fail-open to page. No learned
routing, no RCA model, no agent with tools.

### (3) The gap

The field's genuinely deployed wins are **learned routing and RCA-as-evidence-gathering**, both sitting *upstream*
of the page decision and feeding humans — exactly the position Jev already occupies. What we don't use: (a) cheap,
safe multi-signal context assembly (deploy correlation, past-incident retrieval, runbook surfacing) as
*deterministic or retrieval-based* enrichment of the gate's evidence bundle; (b) a measured judge-agreement
discipline; (c) the corollary that the two categories with real deployment evidence (learned routing, diagnostic
collection) are the only ones worth piloting — agentic remediation is demo-ware.

### (4) Recommendations (control stays deterministic)

1. **Track, don't build: agentic remediation; LLM RCA as autonomous decider.** Maturity is pilots-and-claims.
   The control principle forbids an agent with write access anywhere near the paging path anyway. Revisit only
   when an independently verified production deployment (not a vendor case study) shows evaluated outcomes.
2. **Worth tracking, possibly piloting in shadow: learned routing signals as gate evidence.** DeepTriage/DeepCT/
   CERT-EU prove routing models work at scale. A routing model's output ("owning team X, confidence 0.92") is
   *evidence* the L4 gate consumes deterministically (e.g., cross-check against the service-ownership table) —
   never a suppress decider. Shadow-mode only, human agreement measured.
3. **Worth building: retrieval-enriched evidence bundle.** Multi-signal correlation (metrics+logs+traces+deploys),
   retrieval of similar past incidents with *outcomes*, confidence scoring — as enrichment feeding the
   deterministic gate's freshness/confidence checks, not as a judge. This is RCACopilot's *deployed* collection
   module shape, and it's what practitioners report actually cutting time-to-action.
4. **Defensive track: judge-drift and concept-drift for any learned component.** Concept drift, false
   correlations, stale topology, black-box mistrust — any learned routing/RCA pilot ships with the same monthly
   κ/accuracy resample gate as the judge (Topic 5 Rec 2), or it doesn't ship.

---
## The 5 ML truths for Sentinel

Distilled from all six topics. These are the claims this lane will defend.

1. **The industry's best correlation is more deterministic machinery, better keyed — and our
   "never pay Jev for deterministic work" law is already ahead of the median.** Across PagerDuty, BigPanda, and
   Moogsoft, the ML in alert grouping is either unpublished (label it marketing) or confined to advisory roles
   (probable origin, recipe suggestions). What demonstrably works at scale — Alertmanager's FNV-64a fingerprint
   over sorted canonical labels, `group_by` grouping, inhibition rules, Icinga's recency-weighted flap scoring,
   RFC-2439 penalty-decay dampening with max-suppress-time, xMatters' per-source flood filter derived from real
   customer data — is all deterministic. Our genuine gaps are mechanical, not model-shaped: a canonical
   fingerprint spec, inhibition rules, hysteresis, recency weighting, penalty-decay dampening with a hard
   max-suppress ceiling, and self-calibrated per-source storm thresholds.

2. **Jev's confidence is ordinal, not calibrated — and the gate must own that fact.** The published consensus
   (Mar 2026–May 2026) is that aligned-LLM confidence is systematically overconfident, prompt-fragile, and at
   best rank-ordered. Our 1.3–2.2% measured flips are hard proof the point estimates are unstable. The gate must
   consume an isotonic-mapped score with binomial intervals, treat the ±δ ambiguity band as untrusted (default
   PAGE), derive the bar from a stated cost ratio t\* = C_FP/(C_FP+C_FN) validated on a risk–coverage curve, and
   never ask the model to abstain in prose (Kadavath: models are biased against using abstention options) —
   abstention is a deterministic computation over margin, mapped score, and the instruction firewall's P(fit).

3. **Detection is decoupled from the paging decision — everywhere, without exception.** Every serious vendor
   runs anomaly detection as an event *producer* upstream of correlation; the page/no-page authority always sits
   in the correlation + policy layer downstream. No vendor, no paper found, puts the anomaly model inside the
   final wake-the-human decision. If we ever add anomaly detection, it enters at INGEST as one more alert
   producer (fingerprinted, deduped, episode-merged like any vendor alert), with quotas, corroboration
   requirements, a deterministic kill switch — and it can only ever add evidence *toward* paging, never earn
   suppression. The cheapest win needs no model at all: STL-style seasonal baselines for the correlator's own
   storm thresholds.

4. **The page-or-suppress boundary is human-or-deterministic-owned across the entire industry — and our
   temporal race may be a genuine novelty.** The complete public record: DeepTriage (deployed, classical ML)
   *recommends* teams; RCACopilot's deployed piece is diagnostic *collection*; CERT-EU routes *tickets*; every
   "AI SRE agent" ships investigate → draft → human-approves, with zero independently verified autonomous
   remediation deployments. Nobody publishes an AI judge that decides page-or-suppress with evaluated outcomes.
   If a competitor runs one, they aren't publishing it — which is itself data: the industry's revealed preference
   is that this boundary is not model-owned. Our race-to-page (temporal switch, fail-open to page, late answers
   as powerless shadow evidence) has no published peer; that should be documented as an ADR, not assumed to be
   standard practice.

5. **Judge trust is measured, not asserted — κ, drift, graded re-entry, subgroups.** The published minimum bar
   for a production judge: golden set of human-labeled decisions, Cohen's κ ≥ 0.6 before shipping, monthly
   re-sample for drift, three-way split discipline. We are below it until built. The missing Neural-Simplex
   reverse switch applies to the whole Jev layer: graded re-entry after circuit-breaker trips (shadow-only until
   K consecutive in-budget in-agreement judgments), Jev availability as a first-class ops signal, weekly
   ECE/Brier recomputation with automatic fallback to a conservative bar. And conformal's trap: thresholds must
   hold *within* subgroups (alert class, vendor, severity, novel-vs-known) — a marginal guarantee silently
   under-covers exactly the subgroup where wrong-suppress = missed SEV-1.

---

## Honest gaps — what this lane could not verify

1. **PagerDuty Intelligent Grouping internals** — no published model architecture, training data, or evaluation;
   "87% incident reduction" is vendor-reported with no methodology. Marketing until proven otherwise.
2. **BigPanda's clustering ML** — docs describe a deterministic pattern engine; the "ML" lives only in executive
   quotes. Unverifiable.
3. **Moogsoft Tempus / Cookbook internals** — algorithm-family level only (deterministic attribute clustering;
   arrival-pattern similarity); exact similarity functions, thresholds, and feedback loops unpublished.
4. **Watchdog's story-generation / ranking / Log Anomaly Detection / RCA internals** — product descriptions only;
   no honest third-party teardown of the production stack found. Datadog's Toto foundation model is open, but the
   company has not disclosed which production features (if any) run on it.
5. **No published work on calibrating page-or-suppress judgments specifically for SRE paging.** Closest: AACT
   (SOC triage, 2025), Elkan cost-thresholding theory, selective classification. The paging-specific calibration
   literature appears not to exist publicly.
6. **No published hard C_FP/C_FN ratio.** Industry gives proxies ($5,600/min downtime, $700K/yr toil, 67–83%
   ignore rates, 9.6M events/yr with <18% actioned) — but the ratio must be priced per-org. Any recommendation
   quoting a ratio without our own numbers is theater.
7. **A data-derived flap-volatility threshold** — Nagios 5/20 are round numbers (structurally principled via
   hysteresis); xMatters' 4 alerts/min is the only flood threshold explicitly derived from real customer flood
   data. No vendor publishes a flap volatility threshold with data behind it.
8. **Sentinel's internal L3 correlator implementation** — this lane did not read `correlator.py`; statements about
   what the correlator "presumably" lacks are inferred from the pipeline context, not verified against code. The
   parent agent should verify fingerprint canonicalization, hysteresis presence, and per-source keying against the
   actual implementation before adopting the Topic 1/2 recommendations.
9. **Sentinel's numeric confidence bar and Jev score type** (verbalized vs logit-derived) — recommendations assume
   only "a bar exists" and degrade gracefully either way. If no bar exists yet, the Topic 3 derivation *is* the
   bar's derivation.
10. **Cross-month stability of any vendor's ML grouping** — no vendor publishes per-month or drift data; the
    generalization-law concerns apply to all of them unverifiably.
11. **Academic transfer risk** — the similarity/graph correlation literature is predominantly IDS/network-security
    (DARPA datasets), not on-call paging. Reported reductions do not transfer to paging semantics where false
    suppression is a SEV-1 silence.
12. **PACE-LM details** (FSE Industry '24) — cited via bibliography; the exact calibration technique and numbers
    were not read from the full paper.
13. **Vendor ecosystem claims labeled as marketing in this document:** Datadog Bits AI SRE "2,000+ customer
    environments," PagerDuty "up to 50% faster," Ramp OCA's 575 merged fixes (single-company self-report, human
    review still in the loop).

---

## What this lane did not do

No code was written, no contracts changed, no Jev calls made. This is a research artifact: the state of the art,
sourced; our position; the gaps; and recommendations that keep the deterministic gate in ownership of every
decision. The next step — if Aditya approves — is per-recommendation feasibility against the actual L3/L4
implementation, then requirements (REQ-xxx) before any lane builds.

*Control-principle check on every recommendation in this document: Jev and any learned component remain strictly
advisory or shadow; the deterministic L4 gate owns abstention, thresholds, recovery, subgroup bars, and the
page/suppress decision. Nothing here asks the AI to decide more.*
