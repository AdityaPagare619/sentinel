# 04 — The Quantized Gate: ADR-013 Implementation Design

**Lane 2 (Oracle/Tripwire) · principal-fix-designs wave · 2026-10-03**
**Status:** DESIGN. No code. Implements ADR-013 (`design/principal/12-adr-deltas.md`),
resolving synthesis §3.1 (M-1 vs Oracle §1.5) and hole #4 (synthesis §2).
**Judged against:** `design/principal/00-laws.md` (the seven laws).
**Builds on:** `11-synthesis.md` §2 hole #4, §3.1; `12-adr-deltas.md` ADR-013;
`04-calibration-rigor.md` (esp. §1.2, §1.5, §2, §3.3, §7); `09-decision-register.md`
DR-4; `chiefs/ORACLE.md` (wire facts).

**One-sentence design:** the `P(p1) < 0.002` leg is re-derived in quantized
space — suppress requires reported `P(p1) = 0.00` **and** an empirical,
per-org upper bound `p̂_upper < 0.002` from the tuner (or, until the fit
exists, dual human attestation on the allowlist entry). The bar is never
silently approximated.

---

## §1. The units-error proof, worked numerically

### 1.1 The wire fact (Oracle §1.5)

Jev's wire format rounds probabilities to **0.01** (`chiefs/ORACLE.md`).
The triple-lock leg `P(p1) < 0.002` therefore cannot be evaluated as a
comparison on a continuous quantity: the instrument's resolution (0.01)
is **5× coarser** than the bar (0.002). The leg is satisfiable only by the
reported value `0.00` — a point condition. Any code that writes
`if reported_p1 < 0.002` is comparing a quantized report against a
continuous bar. That is the units error. (Law 4: peel to bedrock — the
bedrock is that the comparison's left operand does not mean what the code
thinks it means.)

### 1.2 The M-1 arithmetic, step by step

Take a concrete alert. Jev's true (unrounded) severity probability is

$$p_{\text{true}} = 0.0049.$$

**Step 1 — quantization.** 0.0049 rounds to the nearest hundredth:

$$r = \operatorname{round}_{0.01}(0.0049) = 0.00.$$

**Step 2 — the gate's comparison.** The frozen implementation evaluates

$$0.00 < 0.002 \;\Rightarrow\; \text{TRUE} \;\Rightarrow\; \text{leg 1 passes}.$$

**Step 3 — the bar.** DR-4 derives the Bayes-optimal suppress bar from the
cost model ($C_{FP} = \$100$, $C_{FN} = \$50{,}000$):

$$t^* = \frac{C_{FP}}{C_{FP} + C_{FN}} = \frac{100}{100 + 50{,}000}
      = \frac{100}{50{,}100} = 0.00199601\ldots \approx 0.001996.$$

**Step 4 — the comparison that matters.**

$$\frac{p_{\text{true}}}{t^*} = \frac{0.0049}{0.00199601} = 2.455.$$

The alert's true severity probability is **2.455× the bar** — it should
page — and the gate suppresses it, because the gate never saw 0.0049. It
saw 0.00.

### 1.3 The expected-cost arithmetic (DR-4's $250 vs $100)

Expected cost of suppressing an alert with true severity probability $p$:

$$E[C \mid \text{suppress}] = p \cdot C_{FN} = 0.0049 \times \$50{,}000 = \$245.$$

Expected cost of paging it:

$$E[C \mid \text{page}] = C_{FP} = \$100.$$

**Net expected loss per such mistaken suppression: $\$245 - \$100 = \$145$.**
(Synthesis §6's "$250" is \$245 rounded; the ratio 2.45× is exact to the
shown precision.) Every alert in the true-$p$ band $[0.002, 0.005)$ that
reports 0.00 is a negative-expected-value suppression the frozen gate
takes with a straight face. The band is not a corner case: it is the
*entire upper half* of the reported-0.00 mass under round-to-nearest.

### 1.4 Generalization: what reported $r$ implies about true $p$

Let $r_h = h/100$ for $h \in \{0, 1, \ldots, 100\}$ be the reported value.
The true-$p$ interval it implies depends on the vendor's rounding mode —
**which the vendor does not document**. The three plausible modes:

| Rounding mode | Reported $r_h$ implies true $p \in$ |
|---|---|
| Round-half-up (nearest) | $[\max(0,\frac{h-0.5}{100}),\; \min(1,\frac{h+0.5}{100})]$ |
| Truncation (floor) | $[\frac{h}{100},\; \min(1,\frac{h+1}{100})]$ |
| Round-half-even | same as half-up except at exact $.x005$ boundaries |

Worked rows under round-half-up (the rows that matter for the gate):

| Reported $r$ | True $p$ interval | Contains the 0.002 bar? |
|---|---|---|
| **0.00** | $[0,\; 0.005)$ | **Yes — the whole interval straddles it** |
| 0.01 | $[0.005,\; 0.015)$ | entirely above the bar |
| 0.02 | $[0.015,\; 0.025)$ | entirely above the bar |
| 0.99 | $[0.985,\; 0.995)$ | — |
| 1.00 | $[0.995,\; 1.000]$ | — |

General formula (round-half-up), edge-clipped:
$$r_h \;\Rightarrow\; p_{\text{true}} \in
\left[\max\!\left(0,\tfrac{h-0.5}{100}\right),\; \min\!\left(1,\tfrac{h+0.5}{100}\right)\right].$$

**The design's working assumption (Type 1 — see §5):** because the rounding
mode is undocumented **and unidentifiable from reports alone** (no
known-$p$ calibration states exist on the wire; repeated queries of one
state reveal straddle, not mode — the mode is confounded with the unknown
true $p$), the design does *not* attempt to infer it. Pre-fit, the
reported-0.00 class is treated under the most conservative plausible mode,
truncation:

$$r = 0.00 \;\Rightarrow\; p_{\text{true}} \in [0,\; 0.01).$$

That is a **5× widening** of the bar's uncertainty band (0.01 vs 0.002):
under truncation, a reported 0.00 can hide a true $p$ up to just under
0.01 — expected suppression cost up to $0.01 \times \$50{,}000 = \$500$
against a \$100 page. This is exactly why the pre-fit path requires *two
humans*, not one (§2.3): one human is not enough corroboration for a 5×
uncertainty band (Law 2 — design for the hostile case, which here is the
vendor's undocumented rounding).

The post-fit path does not need the mode at all: the calibration fit is
*empirical* over the reported-0.00 class (§2.2), so whatever the vendor's
rounding does, the observed SEV1 rate among reported-0.00 alerts absorbs
it. The fit is mode-agnostic by construction. **Uncertainty we cannot
resolve, we bound; we never average over it.** (Creative application §6,
choice 3.)

---

## §2. The re-derived decision procedure

### 2.1 Leg-1 predicate, mechanical steps

The gate computes leg 1 (probability lock) as follows. Every step is
deterministic code (04 §2.1 — threshold arithmetic is code's monopoly).

```
INPUT:  reported_p1      # the wire value, string or Decimal, e.g. "0.00"
        fingerprint      # alert fingerprint (env+cluster namespaced, ADR-017)
        org, now         # customer id, current time
        fit_store        # versioned tuner artifacts (off hot path)
        PINNED_MODEL     # exact model version pin (ADR-015)

STEP 1 — parse to integer hundredths (never float):
    hundredths = parse_hundredths(reported_p1)   # "0.00" -> 0, "0.01" -> 1, ...
    # Rationale: 0.01 is not exactly representable in binary float; the
    # comparison must be on the wire's own quantized domain. (Type 2.)

STEP 2 — the point condition:
    if hundredths != 0:
        leg1 = FALSE; goto DONE        # any reported value >= 0.01 fails the leg

STEP 3 — try the fit path:
    fit = fit_store.get(org, class="R(C,W,r=0.00)")
    if fit is not None
       and fit.reference_class == "R(C,W,r=0.00)"   # schema assertion, not trust
       and fit.model_pin == PINNED_MODEL
       and fit.valid_until > now
       and fit.shift_status == "OK"
       and fit.p_hat_upper < 0.002:                  # one-sided 95% Wilson upper
        leg1 = TRUE; goto DONE

STEP 4 — the honest interim (never an approximation):
    entry = allowlist.lookup(fingerprint)            # env-namespaced (ADR-017)
    if entry is not None
       and dual_attestation_valid(entry, now):      # §2.3, two distinct humans
        leg1 = TRUE; goto DONE

STEP 5 — default:
    leg1 = FALSE

DONE: leg1
```

Notes the builder must not innovate on:

- **Step 2 is exact equality on the quantized domain**, not `< 0.002` on a
  float. `hundredths != 0` *is* the implementation of the bar in quantized
  space; the continuous bar lives only in the cost model (§1.2) and the
  fit's bound (§2.2). Confusing the two is the original bug wearing a new
  coat.
- **Step 3's `< 0.002` compares the fit's *upper confidence bound*, not a
  point estimate.** Comparing `fit.p_hat < 0.002` (point estimate) would
  reintroduce the units error one level up: the point estimate has no
  business near a 0.002 bar without its error bar. (Law 7, data/AI:
  deterministic guardrails over probabilistic claims.)
- **Step 4 replaces the lock; it does not approximate it** (Tripwire
  T1-01, synthesis §3.1). There is no `elif hundredths == 0 and
  org.trust_tier == "high"` branch. The interim is binary: fit or two
  humans, nothing in between.

### 2.2 The calibration fit, specified exactly

**What the tuner fits.** For each org $C$, the tuner maintains one scalar
per gate-relevant quantized class. For leg 1 the only class is the
reported-0.00 mass:

$$\text{fit}(C) :\; R(C, W, r{=}0.00) \;\longmapsto\; \hat{p}_{\text{upper}},$$

where $\hat{p}_{\text{upper}}$ is the **one-sided 95% upper confidence
bound** (Wilson score) on the true SEV1 rate within that class. The tuner
fits nothing else for this leg — no curve, no regression, no smoothing.
One class, one bound. (Law 7, software engineering: the smallest
sufficient system.)

**On what labels.** Adjudicated severity labels from the org's *own*
incident history — never Jev's labels, never synthetic labels:

- **Label 1 (true SEV1):** the alert is linked to a confirmed SEV1 incident
  in the org's incident system within the correlation window (the window
  is org-configured, default 24h; the linkage query is the same one
  ADR-017's attestation evidence uses).
- **Label 0 (not SEV1):** the alert was reviewed (on-call adjudication or
  postmortem) with an explicit recorded "not SEV1" verdict.
- **Unlabeled after the label SLA (72h):** **excluded** from both $n$ and
  $k$. Unlabeled-as-negative is the classic denominator lie (04 §3.3,
  Oracle mandate 1); the tuner reports the unlabeled fraction $m$ alongside
  $n, k$, and a fit with $m/(n+m) > 0.20$ is flagged (not failed — flagged;
  the failure mode is silent label rot, ADR-021's D-2).

Label sources in order of availability: (1) shadow-mode adjudications
(weeks 1–2: suppressions don't execute, so every shadow-suppressed alert
gets a free human verdict — the interim that pays for itself); (2)
postmortem linkage; (3) on-call "was this a real SEV1" prompts on
executed suppressions.

**The named reference classes.** Every fit names its class explicitly
(04 §1.1):

$$R(C, W, r{=}0.00) = \{\text{alerts from customer } C,\ \text{in trailing
window } W,\ \text{with reported } P(p1) = 0.00\}.$$

$W$ = trailing 90 days (Type 1 — it is part of the certification's meaning;
see §5). The tuner *may* compute sub-class bounds (by env, by service
tier) as diagnostics; the gate uses only the top-level class bound. No
sub-class bound may relax the gate — diagnostics never steer the safety
case (Law 3: the steering wheel is Type 1).

**Wilson bound construction.** One-sided 95% ($z = 1.6449$), Wilson score
upper — the same construction as 04 §1.3's decision-calibration
certificate, so the fit and the certificate are directly comparable:

$$\hat{p}_{\text{upper}}(k, n) =
\frac{\hat{p} + \frac{z^2}{2n} + z\sqrt{\frac{\hat{p}(1-\hat{p})}{n} +
\frac{z^2}{4n^2}}}{1 + \frac{z^2}{n}}, \qquad \hat{p} = k/n.$$

The 0.002 bar bites hard here, and the numbers must be faced, not wished
away. Minimum labeled $n$ for $\hat{p}_{\text{upper}} < 0.002$ (one-sided
95% Wilson), by observed SEV1 count $k$:

| $k$ (SEV1s among reported-0.00) | min $n$ for bound < 0.002 |
|---|---|
| 0 | **1,351** (bound 0.0019986; at $n{=}1350$ the bound is 0.0020001 — fails) |
| 1 | 2,239 |
| 2 | 3,019 |
| 3 | 3,751 |

Verification of the $k{=}0$ boundary: with $k{=}0$ the Wilson upper reduces
to $z^2/(n + z^2) = 2.7055/(n + 2.7055)$; solving $< 0.002$ gives
$n > 1350.0$; $n{=}1351$ is the first integer that clears. (Checked by
direct computation, §1's arithmetic.)

**The honest consequence:** most orgs will not have 1,351 labeled
reported-0.00 alerts for months. That is fine — it is the *point*. Until
then the gate runs the dual-attestation interim, and shadow mode (where
suppressions don't execute anyway) makes the interim costless. A design
that needed the fit on day one would be a design that fakes the fit on
day one. (Law 6: the pre-mortem where someone lowers $n_{\min}$ to
"unblock the demo" is PM-4 in §7.)

**Validity windows.** A fit is *valid* iff all hold:

1. `now < valid_until`, where `valid_until = computed_at + 30 days`
   (the 30-day re-validation clock, ADR-022 — the fit is a threshold
   input, so it inherits the clock);
2. `fit.model_pin == PINNED_MODEL` (ADR-015: a new pin invalidates every
   fit; the mapping is version-specific);
3. `fit.shift_status == "OK"` — the shift monitor (§2.4) has not tripped
   for the $r{=}0.00$ class;
4. `fit.window_end >= now - 7 days` — the trailing window must be fresh;
   a fit whose window ended three weeks ago is a stale claim wearing a
   fresh timestamp.

**What "stale fit" means and what happens then.** A fit failing any of
(1)–(4) is *stale*. A stale fit is **not** a fit: the gate treats it
exactly as `fit is None` and falls to Step 4 (dual attestation). Stale
never degrades to the point estimate, never degrades to last-good, never
logs-and-continues. Stale ⇒ the probability lock's empirical leg is gone
⇒ suppress additionally requires two humans. This is ADR-014's
"stale ⇒ page" applied to the fit: the lock fails, and the only remaining
suppression path is the corroborated human one. The staleness transition
itself emits a control-plane event (unsuppressible — the guard's alarm
bypasses the guard, synthesis §4).

### 2.3 The interim: dual human attestation (until the fit exists)

When no valid fit exists, leg 1 is *replaced* by dual human attestation on
the allowlist entry — the same entry leg 3 (ADR-017/019) already requires.
"Dual" is specified, not gestured at (synthesis §3.2: verification is a
tuple or it is theater):

- The allowlist entry carries **two** attestation tuples
  `(attestor_id, attested_at, evidence_ref, ttl_days)`, with:
  - `attestor_id` distinct between the two tuples **and** distinct from
    the entry's author (enforced in code, not policy);
  - `evidence_ref` = the run id of the incident-linkage query showing
    zero SEV1 linkage for this fingerprint over the trailing 30 days
    (a verifiable artifact, not a Slack thread);
  - `attested_at` within `ttl_days` (TTL ≤ 30 days; the attestation is a
    freshness proof per ADR-014);
  - fingerprint namespaced by env+cluster (ADR-017) — the attestation
    covers *this env's* fingerprint, not the check name globally.
- Post-fit, the allowlist leg reverts to its standard single-attestor
  tuple (ADR-017) — but leg 1 then requires the fit's bound. The two
  regimes never mix: it is never "one human + a stale fit."

Why two humans and not one: §1.4's truncation-union gives the pre-fit
reported-0.00 class a true-$p$ interval of $[0, 0.01)$ — a 5× uncertainty
band around the bar. One human attestation is corroboration of the
*fingerprint's history* (it hasn't paged wrongly before); the second human
is corroboration of the *corroboration* (the first attestor actually ran
the linkage query). Asymmetric trust (ADR-019): silence takes two.

### 2.4 Fit artifact schema (versioned; the gate validates, never trusts)

```
FitArtifact v1:
  fit_id:            uuid
  org:               customer id
  reference_class:   "R(C,W,r=0.00)"          # gate asserts exact match
  window:            [start, end]              # trailing 90d, end within 7d of now
  n:                 labeled reported-0.00 alerts in window
  k:                 ... adjudicated true-SEV1 among them
  m:                 unlabeled-excluded count (reported, never folded into n)
  p_hat_upper:       one-sided 95% Wilson upper(k, n)
  z:                 1.6449                    # pinned in the artifact
  model_pin:         exact Jev version string
  gate_formula_version: versioned aggregation formula id (04 §2.2.1 —
                     changing the formula invalidates the fit)
  computed_at / valid_until: timestamps       # valid_until = computed_at + 30d
  shift_status:      "OK" | "TRIPPED"
  tuner_version:     tuner code version
```

The shift monitor feeding `shift_status` watches the $r{=}0.00$ class
*conditionally*, not pooled: weekly sequential check on the class's label
rate plus a PSI distance on the class's alert-feature distribution vs the
fit window. Thresholds pre-registered before the first design partner
(04 §6.2). Any single confirmed SEV1 among reported-0.00 alerts trips the
monitor immediately (not at the weekly cadence) — a confirmed false
member of the "safe" class is the strongest possible shift signal, and it
also triggers the deployment path's auto-revert (synthesis §4).

---

## §3. The decision-calibration certificate

### 3.1 What it measures, and on what

The certificate is **decision calibration** (04 §1.3), not instrument
calibration: it measures the *gate's action*, on the *actual gate
including this implementation* (synthesis §3.1 — the empirical claim stays
honest regardless of the quantization, because the measurement is over
decisions, not probabilities):

$$P(\text{true SEV1} \mid \text{alert} \in R(C, W, \text{suppress})),$$

$$R(C, W, \text{suppress}) = \{\text{alerts from customer } C,\ \text{in
window } W,\ \text{where the full triple lock (as implemented in §2, with
legs 2–3 per ADR-022/017/019) took action suppress}\}.$$

**Denominator discipline** (Oracle mandate 1; 04 §3.3):

- $n$ = number of suppress decisions in $R(C, W, \text{suppress})$.
  Decision units, not alert units — storm-collapsed alerts count once
  (the decision is the unit).
- $k$ = number of those decisions later adjudicated true-SEV1 (label 1
  per §2.2's label rules).
- $m$ = suppressions still unlabeled at certificate time — **excluded**
  from $n$ and $k$, reported separately. The certificate states the
  unlabeled fraction; a certificate with $m/(n+m) > 0.20$ is marked
  `LABEL-ROT-RISK`, not silently issued.
- Shadow-mode and executed suppressions **both** enter $R$, tagged by
  mode; the certificate reports the pooled bound and the per-mode bounds
  (a pooled-vs-mode divergence is a shift signal).
- Minimum-N floors (04 §3.3): no tail claim is issued with
  $n_{\text{suppress}} < 300$ **or** $n_{\text{confirmed SEV1}} < 25$.
  Below the floors the certificate reads `INSUFFICIENT-N` — the gate may
  still operate (the cost model + interim path justify operation), but no
  certificate is claimed. Operation without a certificate is labeled as
  such (04 §3.3).

### 3.2 The exact computation (what the builder implements)

One versioned pure function. No branches, no modes, no "pooled" flag —
the reference class is an input, the mode tag is a field on each decision
event, and the function is called once per (class, mode-slice):

```
certificate(R, W, decisions):
    # decisions: list of (adjudicated_label | UNLABELED, mode) for the class
    labeled = [d for d in decisions if d.label != UNLABELED]
    n = len(labeled)
    k = sum(1 for d in labeled if d.label == SEV1)
    m = len(decisions) - n
    if n < 300 or confirmed_sev1_in_window(W) < 25:
        return Certificate(verdict="INSUFFICIENT-N", n=n, k=k, m=m, ...)
    p_hat = k / n
    u = wilson_upper_onesided(k, n, z=1.6449)   # §2.2's construction
    verdict = "PASS" if (u <= 0.005 and k == 0) else \
              "FAIL" if (u > 0.01 or k >= 1) else "FLAG"
    # k >= 1 additionally triggers the deployment auto-revert (synthesis §4),
    # independent of the bound.
    return Certificate(
        statement=(f"P(true SEV1 | suppress) <= {u:.4f} at 95% one-sided Wilson, "
                   f"n={n}, k={k}, unlabeled-excluded m={m}"),
        reference_class=R, window=W, model_pin=PINNED_MODEL,
        gate_formula_version=GATE_FORMULA_VERSION,
        shift_status=shift_status(R, W), valid_until=now + 30d,
        verdict=verdict)
```

**Acceptance bands (Type 1 — the certification policy):**

| Band | Condition | Meaning |
|---|---|---|
| PASS | $u \le 0.005$ **and** $k = 0$ | the gate's empirical miss rate is consistent with the 0.002 design bar at 2.5× margin |
| FLAG | $0.005 < u \le 0.01$, $k = 0$ | re-fit + threshold review; suppress path continues under heightened monitoring |
| FAIL | $u > 0.01$ **or** $k \ge 1$ | suppress path suspended to dual-attestation-only; auto-revert per deployment path |

The 0.005 PASS bar is 2.5× the 0.002 design bar — margin for the
quantization residue (§1.4: the fit's class is reported-0.00, whose true
mass extends to 0.005 under round-half-up). The bands are the certificate's
teeth: a measurement without acceptance criteria is a dashboard, not a
certificate.

### 3.3 Worked example (the "show me the 412")

Org $C$, trailing 90-day window, full gate as implemented:

- Suppressions in $R(C, W, \text{suppress})$: 412 decisions.
- Adjudicated: $k = 0$ true-SEV1, $n = 380$ labeled, $m = 32$ unlabeled
  (unlabeled fraction $32/412 = 7.8\%$ — below the 20% flag).
- $n = 380 \ge 300$ ✓; confirmed SEV1s in window (all severities):
  41 ≥ 25 ✓ — floors cleared.
- Point estimate $\hat{p} = 0/380 = 0$.
- Wilson one-sided 95% upper: $u = 2.7055/(380 + 2.7055) = 0.007070$.
- $u = 0.0071 > 0.005$ ⇒ verdict **FLAG** (not PASS): with only 380
  labeled suppressions and zero misses, the 95% upper bound is 0.71% —
  honest, and honestly above the 0.5% PASS bar. The certificate says so.
  To reach PASS at $k{=}0$ needs $n \ge 541$ ($2.7055/(541+2.7055) =
  0.004976 \le 0.005$). The numbers tell the org exactly how much more
  evidence the PASS verdict costs: 161 more labeled suppressions.

That is the certificate doing its job: it does not flatter the gate, it
prices the next claim.

---

## §4. Alternatives considered

**A1 — Raise the bar to 0.01. REJECTED (Type 1).** Two independent kills:

1. *It destroys the expected-cost optimum.* $t^* = 0.001996$ is not a
   preference; it is the Bayes rule under the cost model (DR-4). A
   hypothetical true-$p$ bar at 0.01 suppresses the marginal band
   $p \in [0.002, 0.01)$ where every suppression has negative expected
   value: at uniform $p$ on the band, average excess cost per marginal
   suppression is $0.006 \times \$50{,}000 - \$100 = \$200$. The bar would
   be a round number chosen for instrument convenience, not the cost
   model — the tail wagging the dog.
2. *It doesn't even fix the units problem.* A bar at 0.01 implemented on
   quantized reports ("suppress iff reported $r \le 0.01$") admits true
   $p$ up to 0.015 (round-half-up) or 0.02 (truncation) — expected
   suppression cost up to $\$750$–$\$1{,}000$ vs the \$100 page, i.e. up
   to $7.5$–$10\times$ the bar it replaced. Moving the bar to the
   instrument's resolution doesn't resolve the mismatch; it re-bases the
   error at a larger multiple.

**A2 — Ignore the rounding (keep `reported < 0.002`). REJECTED (Type 1).**
The pre-mortem proves it (§1.2–§1.3): a 0.0049 true-$p$ alert — e.g. a
degraded-but-recovering replica the model judges 99.5% likely to
self-heal — reports 0.00, passes the raw comparison, suppresses; the 0.5%
tail hits; SEV1; no page. Postmortem finds the gate compared `0.00 <
0.002` and called it safe. Expected loss \$145 per such decision, forever,
compounding silently because the gate's own logs record a "correct"
sub-bar comparison. This is the incident this design exists to prevent.

**A3 — Midpoint imputation (treat reported 0.00 as $p = 0.0025$).
REJECTED (Type 1).** Imputation is a point fiction laid over an interval
fact — the B3 blur (04 §2.2: code re-interprets Jev's numbers). Worse, it
is dishonest in the other direction: $0.0025 > 0.002$ means the leg could
*never* fire, converting the probability lock into a permanent page
smuggled in as a calibration trick. If the org wants "never suppress on
probability," that is an ADR-013 verdict for Aditya — stated openly, not
laundered through arithmetic.

**A4 — Ask the vendor for finer low-tail quantization. NOTED, not
depended on (Type 2).** A finer wire format would shrink the uncertainty
band, but Law 2 forbids designing on the vendor fixing it. Filed as an
operational ask; the design is complete without it.

---

## §5. Type 1 / Type 2 register for this design

Every choice in §§1–4, marked per Law 3. (Type 1 = RFC-grade, reversible
only by ADR; Type 2 = reversible, builder's discretion within the spec.)

| # | Choice | Type | What would change it |
|---|---|---|---|
| D1 | The bar's *justification* stays at $t^* \approx 0.002$ from the cost model; its *implementation* is the point condition + fit bound | 1 | A measured $C_{FP}/C_{FN}$ from shadow pilots moves the constants (DR-4 already says so); the two-level structure (justify continuous, implement quantized) stands |
| D2 | Leg 1 = `hundredths == 0 AND (fit.p̂_upper < 0.002 OR dual attestation)`; the bar is never silently approximated | 1 | ADR only — this is the paging-path safety policy |
| D3 | Pre-fit working assumption: reported 0.00 ⇒ true $p \in [0, 0.01)$ (truncation-union); the rounding mode is *not* inferred | 1 | Vendor documents the rounding mode *and* the mode is verified by a wire-format probe; then the assumption narrows to the verified mode |
| D4 | The fit is one scalar per class: one-sided 95% Wilson upper over $R(C, W, r{=}0.00)$; no curve, no smoothing | 1 | A second gate-relevant quantized class is identified (none exists today — the 0.002 bar touches only the 0.00 mass) |
| D5 | Labels are org-adjudicated SEV1 linkage; unlabeled excluded from $n, k$, reported as $m$; $m/(n+m) > 0.20$ flags the fit | 1 | The label SLA or the flag threshold — the exclusion rule itself is the denominator discipline and stands |
| D6 | Fit validity: 30-day clock + model-pin match + shift OK + window fresh ≤7d; stale ⇒ treated as no fit (Step 4), never last-good | 1 | ADR-014/015/022 evolve the clocks; the "stale = absent" direction is constitutional |
| D7 | Interim = dual attestation tuples (two humans ≠ each other ≠ author; evidence_ref = linkage-query run id; TTL ≤ 30d) on the allowlist entry | 1 | The fit exists (the interim retires); the tuple *schema* is ADR-017's |
| D8 | Certificate bands: PASS $u \le 0.005 \land k{=}0$; FLAG $(0.005, 0.01]$; FAIL $> 0.01 \lor k \ge 1$; floors $n \ge 300$, confirmed SEV1 $\ge 25$ | 1 | Measured cost ratios or a larger evidence base move the band edges by ADR; the band *structure* (measurement with teeth) stands |
| D9 | Parse wire value to integer hundredths; exact equality in Step 2; bound comparison on `p̂_upper` not `p̂` in Step 3 | 2 | A cleaner wire type from the vendor; the semantics (D2) don't move |
| D10 | $W$ = trailing 90 days for the fit window | 1 | Per-org evidence: high-volume orgs may justify shorter windows by ADR; 90d is the default the certificate's meaning rests on |
| D11 | Shift monitor watches the $r{=}0.00$ class conditionally; any confirmed SEV1 in the class trips it immediately | 1 | The monitor's statistic may improve (Type 2); class-conditional monitoring and the immediate trip are the design |

---

## §6. Creative application — the three choices we make differently

**Choice 1 — We let the threshold's justification and its implementation
live at different resolutions.** The industry response to "the bar is
finer than the instrument" is either to coarsen the bar (A1 — destroys the
optimum) or to pretend the comparison works (A2 — the pre-mortem). We do
neither: the cost model keeps its continuous 0.002 optimum *as the reason
the bar exists*, and the implementation moves the comparison into
quantized space with an empirical bridge (the fit). Justification and
implementation are allowed to disagree about resolution, because they
answer different questions — "why 0.002 and not 0.5" vs "what does the
gate compare."

**Choice 2 — The honest interim is a first-class path, not a TODO.**
"Until the fit exists" is where designs go to be quietly dishonest — a
`# TODO: replace with real calibration` above a hardcoded 0.001. Our
interim is specified to the same rigor as the steady state (tuple schema,
two-human rule, TTL, evidence_ref), and it composes with the deployment
path: shadow mode's weeks 1–2 don't execute suppressions anyway, so the
interim costs nothing exactly when fits don't exist, and the shadow labels
are what *build* the fit. The deployment path and the calibration rigor
compose — synthesis §3.1's point, made mechanical.

**Choice 3 — We refuse to infer the rounding mode, and we say so.**
A less honest design would assume round-half-up (the convenient mode),
compute pretty intervals, and ship. We name the mode *unidentifiable from
reports alone*, design the pre-fit path against the most conservative
plausible mode, and make the post-fit path mode-agnostic by construction
(the empirical bound absorbs whatever the vendor does). Uncertainty we
cannot resolve, we bound; we never average over it. A Google principal
reviewing this should find the unknown labeled, bounded, and retired by
the fit — not hidden.

---

## §7. Pre-mortem of this design (Law 6)

*It is one year from now. The quantized gate caused a missed SEV1 that
cost a customer millions. What exactly failed?*

**PM-1 — Composition shift inside the 0.00 class.** The org onboarded a
new service whose alerts flood the reported-0.00 mass with a higher true
SEV1 rate. The trailing-90d fit lags; the shift monitor watches pooled
alert features and misses the composition change *within* the class; the
gate suppresses under a stale-but-technically-valid fit for up to 30 days.
A real SEV1 in the new service's 0.00 mass is suppressed. **Mitigations
already in the design:** the shift monitor is class-conditional (§2.4,
D11); any single confirmed SEV1 among reported-0.00 alerts trips it
immediately and triggers auto-revert; the fit's window-freshness rule
(≤7d) bounds the lag. **Test:** CI fixture injects a composition shift
into the 0.00 class and asserts the monitor trips within one cycle.

**PM-2 — Attestation theater.** The dual-attestation interim degrades:
the same two on-call engineers countersign each other's entries during a
busy quarter; `evidence_ref` points at a Slack thread; the 30-day
zero-incident-linkage query is run once and copy-pasted across entries.
**Mitigations already in the design:** `evidence_ref` must be a
linkage-query *run id* (verifiable artifact); attestors distinct from each
other and the author, enforced in code; TTL ≤ 30d forces re-attestation;
attestation freshness is part of the lock-3 freshness proof (ADR-014).
**Test:** the gate rejects a fixture entry whose two attestors match, or
whose evidence_ref is not a query run id.

**PM-3 — The Wilson misimplementation.** A builder implements the
*two-sided* 95% interval and takes its upper end (wrong error budget), or
folds unlabeled $m$ into $n$ as negatives (denominator lie), or computes
the bound over the pooled all-alert class instead of $R(C, W, r{=}0.00)$
(wrong reference class — 04 §1.1). Each silently corrupts the bound.
**Mitigations already in the design:** one versioned pure function for the
bound, one for fit validity, one for the certificate (§2.4, §3.2), with
property tests: `(k=0, n=1351) → valid`, `(k=0, n=1350) → invalid`,
`(k=1, n=1351) → invalid`, unlabeled inputs rejected as a separate
parameter, wrong `reference_class` string rejected by the gate's schema
assertion. The $n{=}1350/1351$ boundary is the tripwire: any
reimplementation that gets it wrong fails CI loudly.

**PM-4 — The $n_{\min}$ ratchet.** Someone lowers the fit's minimum-$n$
(or the 0.002 bar, or the 30-day clock) in config "to unblock
suppressions for the demo" — the H-2 fatigue ratchet wearing a
calibration costume. **Mitigations already in the design:** $n_{\min}$,
the bar, and the clock live in the versioned gate contract, not in tunable
config (D2, D6, D8 are Type 1); changing them requires an ADR; and the
Sunday 2026-10-04 demo scope explicitly excludes live suppression
(12-adr-deltas §3), so no demo pressure can justify it.

**PM-5 — The label pipeline freezes (D-2).** The adjudication feed stops
updating; $k$ stays 0 because no labels arrive, not because no SEV1s
occur; the fit looks pristine while the world burns. **Mitigations
already in the design:** unlabeled-excluded accounting (§2.2, §3.1) means
a frozen pipeline shrinks $n$, it doesn't inflate confidence; the
$m/(n+m) > 0.20$ flag fires; ADR-021's label-pipeline staleness SLO pages
the platform team. A fit with no fresh labels cannot stay valid — the
window-freshness rule (§2.2.4) kills it within 7 days.

---

*End of 04-quantized-gate. ADR-013 is now implementation-ready: the
predicate (§2.1), the fit (§2.2), the interim (§2.3), the certificate
(§3), and the failure modes (§7) are specified to the level a builder
implements without inventing safety policy. Application waits for Forge's
review and Aditya's verdict per 12-adr-deltas §3.*
