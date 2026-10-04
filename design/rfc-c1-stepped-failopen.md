# RFC — C1 Stepped Fail-Open + Adaptive Storm Detector (one architectural change)

**Status:** PROPOSED (design only — no code; implementation is sequenced later through the gate/correlator lane registries)
**Type:** 1 — failure semantics are the product's moral core (PR #51 §C1.1); adopted as contract, activated on evidence.
**Date:** 2026-10-04
**Parent docs:** `research/load-c2-2026-10-04.md` (C2 measurement, PR #58 — MEASUREMENT, not merged),
`design/rfc-followups-2026-10-04.md` §C1 (ADOPT-WITH-CONDITIONS + 5 evidence conditions),
`docs/adr-decisions-2026-10-03.md` (ADR-016 storm-aggregate digest path; ADR-022 threshold governance).
**Panel:** Forge · Vault · Pager · Tripwire — position memos in §1, disagreement recorded in §1.5.

## Why these two are one change (C2 §9.3)

The C2 load test proved the two are inseparable:

1. The correlator's storm detector is a fixed absolute — `>20 distinct fingerprints / 60s`
   (`correlator.py`, `storm_fingerprints=20`). Under all-distinct traffic at 1,000/min, the
   window always holds ~1,000 distinct (50× the threshold): the detector fires, folds everything
   for 60s, the window drains (folded alerts don't append), traffic resumes ~2s, it re-fires.
   Measured duty cycle: **~2% of alerts reach the Jev race; 98% are structurally folded**
   (sustained_uniform: 97% folded, 2.1% raced; burst_uniform: 99% folded, 0.2% raced).
   The threshold cannot distinguish **"incident storm"** from **"busy estate"** — a
   1,000/min diverse enterprise would live in permanent storm-fold, and the proof engine would
   see almost none of its traffic. The detector — not the race, not the vendor — decides what
   fraction of traffic gets a model-backed decision. This is architectural, not a tuning knob.
2. Fixing the detector *without* stepped fail-open just moves the failure: at 167 events/s
   with a 1.1s median vendor, ~180 concurrent vendor slots are needed vs the race pool's
   32 workers — the TIMER_WON_SHED path would fire constantly (C2 §6.6).
3. Adding the steps *without* fixing the detector is theater: the steps respond to the vendor
   signal while 98% of traffic still folds before the gate.

**Therefore:** the adaptive storm detector and the stepped fail-open ship as one RFC, one
review, one drill matrix. The RFC's activation is still gated on the 5 evidence conditions
(§6) — design-adopted now, behavior-activated on measurement.

---

## 1. Chief position memos

### 1.1 Forge (build craft) — ADOPT-WITH-CONDITIONS

The C2 finding is a five-whys terminus: *why* does coverage collapse? Because the
declaration rule is an absolute count in a world where the denominator (estate alert rate)
spans 55,000× (PR #51 §C2.1: 18/min average enterprise vs 1M/min design point). An absolute
threshold on a 55,000×-spanning denominator is not a parameter to tune — it is a category
error. The fix must make the threshold *relative to the estate*, estimated from the
estate's own statistics (campaign law: self-calibrating methods over month-tuned constants).

Conditions:
- **F1:** The baseline B must freeze while a storm is declared — the detector may not learn
  from the thing it is detecting. (Eternal friction: the feedback loop is the failure.)
- **F2:** C2-replay acceptance gate (the seeded raw JSONs in `research/load-c2/`) before any
  production canary: the detector must declare the embedded storm episodes in the primary-mix
  runs and must NOT declare permanent storm on the uniform-busy runs. Measurement, not faith.
- **F3:** The absolute floor F=20 preserves today's small-estate declaration semantics
  exactly. Chesterton's fence: on small estates the current behavior is the predictable one;
  the adaptive rule may only *widen* coverage on large estates, never *narrow* it on small ones.

### 1.2 Vault (safety) — ADOPT-WITH-CONDITIONS

The adaptive parameters (k, F, the baseline B) are safety-affecting: they decide what
fraction of traffic reaches the proof engine. Per ADR-022/B3, they inherit the policy
lifecycle — manual changes to k or F require two-person attestation; k carries the 30-day
review clock. An adaptive safety parameter with no governance is the fatigue ratchet wearing
a new hat.

Conditions:
- **V1:** Declaration changes *when* the detector fires, never *what* firing does.
  The fold path stays deterministic, pre-Jev, and non-suppressing (D3's structural digest
  path, ADR-016). Regression test (extends ADR-016's condition (b)): an
  adaptive-declared aggregate with all locks green still pages.
- **V2:** D3's rename (`folded`, not `suppress`) is a hard dependency of activation. At
  98%+ fold volumes the word "suppress" on a folded row is a lie the 3 AM operator will
  believe — and with the adaptive detector *unfolding* busy-estate traffic, the fold counts
  will swing by orders of magnitude. The disposition vocabulary must be honest before the
  volumes move.
- **V3:** Baseline growth beyond 3× the 30-day median within 7 days requires two-person
  attestation (the anti-poisoning guard; see pre-mortem §4). Attestation must show the
  growth reason (change-window/deploys correlation); auto-attest is forbidden.

### 1.3 Pager (on-call) — ADOPT-WITH-CONDITIONS

The operator's contract is legibility, not cleverness. PR #51's Chesterton point stands:
"dead vendor ⇒ page the human" fits on a sticker. The steps are adopted only if they are at
least as predictable — and that means the operator must see the *detector's judgment*,
not just the steps' actions. A banner that says "step 3" while hiding that the detector
never declared the storm is a new kind of silent.

Conditions:
- **P1:** The violet banner carries a detector-health line at all steps:
  `Storm detector: W(t)=… distinct/60s, threshold=max(F, k·B)=…, last declared <t> (UTC+local)`.
- **P2:** Step 3's banner always shows "N held in digest, **of which M are source-critical**"
  — a critical-heavy digest is a different emergency than a noise-heavy one.
- **P3:** The K6 trigger-tunability bar (see §5): C2 showed the watchdog tripping 1–2×/run
  under *normal* sustained load with a merely slow vendor. If the sustained-degradation
  signal cannot be tuned to separate from normal slow-vendor noise, the steps never engage.
  Pager refuses a step-1 that fires on a normal Tuesday.

### 1.4 Tripwire (adversarial) — REJECT, then disagree-and-commit

*My best shot at killing this, in writing:*

1. **Baseline poisoning is the steady state, not a corner case.** Every estate trends
   noisier. Your V3 attestation guard is ADR-022's two-person rule applied to a number that
   moves every week — the rubber-stamp ratchet the ADR itself warns about. You have built a
   control that requires humans to notice slow drift, which is the one thing humans never do.
2. **The one-change doctrine buys coherence and sells testability.** The replay gate tests
   the detector alone; the fault-injection drills test the steps alone. But the coupling —
   detector-poisoned ⇒ steps misbehave — is exercisable only in production. You cannot drill
   the interaction that the pre-mortem says kills people.
3. **The digest ordering is a guess.** The pre-mortem (§4) shows "novelty" defined as
   rate-of-change misses the level-shift signature of a real SEV. Ship chronological first
   (PR #51's K2 fallback), earn the ordering with drill data — don't ship the guess and hope
   K5 catches it before a customer does.

*Tripwire's position after the panel:* the kill shot (2) is unanswerable in principle and
is carried as **open dissent** (§1.5). On (1): accept V3 with the operationalized friction
(attestation requires viewing the growth chart — no auto-attest). On (3): dissent recorded,
panel majority rules — the z-score fix + K5 kill condition is the initial bet, and **the
drill decides, not the debate**.

### 1.5 Synthesis and recorded disagreement

**Adopted:** the adaptive detector (§2) and the stepped ladder (§3) as one Type-1 design,
activated only when the 5 evidence conditions (§6) hold.

**Recorded disagreements (disagree-and-commit):**
- **D-1 (Tripwire, open):** digest triage ordering. Tripwire: ship chronological-only until a
  drill proves level-shift+z-score ranking; the panel majority (Forge/Vault/Pager): ship the
  z-score ordering with K5 as the falsifier. Resolution mechanism: K5's drill, not the debate.
  If the drill shows a level-shift SEV signature ranking below position 5, ordering reverts to
  chronological pending redesign.
- **D-2 (Forge vs Tripwire, resolved):** whether step 3's volume trigger may depend on storm
  declaration. Forge: the step-3 budget trigger already fires on page-rate pressure, so an
  independent trigger is redundant. Tripwire: redundancy is the point — a poisoned baseline
  must not be able to pin the system in step 2 while the digest fills. **Tripwire's version
  adopted:** step escalation on absolute page-rate pressure fires regardless of detector
  storm state (belt and suspenders; the detector and the steps are decoupled in the trigger
  path even though they ship as one change).
- **D-3 (Pager, resolved):** the step-1 entry threshold must be strictly stronger than the
  existing watchdog trip (C2: 1–2 trips/run at normal load). Adopted as K6.

---

## 2. The adaptive storm threshold — exact design

### 2.1 The signal

**Storm = fingerprint-velocity spike against the estate's own baseline.** The quantity that
distinguishes "incident storm" from "busy estate" is not the absolute distinct count — it
is the *ratio* of current distinct-fingerprint velocity to the estate's trailing baseline
velocity. A busy estate is high-velocity but *stationary* (R≈1); an incident storm is a
*spike* (R≫1).

Definitions (all computed in the correlator, deterministic, pre-Jev):
- **W(t)** — distinct fingerprints *first-seen* in the trailing 60s window. Only arrivals
  that pass dedup count; folded members do not append (preserves the C2-measured drain
  behavior, §5). Same measure as today; the rule around it changes.
- **B(t)** — EWMA of the 60s-distinct measure over the trailing 24h, half-life 6h, ticked
  every 60s. Self-calibrating per campaign law: estimated from the target estate's own
  statistics, never a month-tuned constant.
- **Declaration rule:** storm declares iff `W(t) ≥ max(F, k·B(t))`.

### 2.2 The falsifiable tuning rule

- **k̂ (default 5, Type 2)** is set per tenant from the trailing 30 days of R samples:
  `k̂ = P99.9(R) + 1.5`, clamped to [3, 12]. Declaration happens only above the tenant's
  own 30-day extremes.
- **F (default 20, Type 2)** is the absolute floor — today's constant. On small estates
  (B tiny), declaration semantics are byte-identical to current behavior (Forge F3).
- k and F are safety-affecting policies: manual changes need two-person attestation and
  k carries ADR-022's 30-day review clock (Vault V1/V3, B3 lifecycle).

### 2.3 What would prove it wrong (falsifiers — kill the signal, keep the fixed floor)

1. **Precision:** over any rolling 30-day window with ≥5 declarations, if fewer than 50%
   coincide with an operator-confirmed incident or deploy-adjacent window within ±30 min
   → the velocity ratio declares on busy-estate noise. k rises or the signal dies.
2. **Recall:** any real incident — paged volume ≥3× page budget sustained ≥10 min — that
   the detector failed to declare → missed storm → signal dies, fall back to the fixed
   floor + the absolute volume trigger (D-2).
3. **C2 replay (acceptance gate, before any production canary):** run the detector over
   the seeded raw JSONs in `research/load-c2/` (F1). It must declare the embedded storm
   episodes (5× for 25s) in the three primary-mix sustained runs, and must NOT declare
   permanent storm on `sustained_uniform` / `burst_uniform` (steady all-distinct traffic —
   the current design folds 97–99% of it; the adaptive design must unfold it). Failure on
   either → kill the velocity signal.

### 2.4 Invariants (Type 1 — not tunable)

- **I-1:** B freezes while a storm is declared (no learning from the detection target).
- **I-2:** Declaration changes *when*, never *what*: the fold path stays deterministic,
  pre-Jev, non-suppressing (storm_digest construction; ADR-016). Storm members fold with
  disposition `folded` (D3 rename), never `suppress`.
- **I-3:** The absolute volume trigger (D-2) is independent of the detector: if
  page-eligible volume exceeds 3× the page budget for >5 min, step 3 engages whether or
  not a storm is declared.

---

## 3. The stepped degradation ladder

### 3.1 Trigger hierarchy (refined from PR #51 §C1.3 with C2 evidence)

C2 measured two facts that sharpen the trigger (C2 §9.1–9.2): the watchdog's trip signal
is live (1–2 trips/run at normal sustained load), and "vendor slow" is common (timer-win
6–14% of raced calls). The ladder therefore distinguishes three regimes:

- **Single slow call (timer-win) → naive passthrough. UNCHANGED.** This is today's behavior
  for the common case (Chesterton's fence: the sticker sentence survives).
- **Watchdog sustained-degradation signal → enter step 1.** Enter when the timer-win rate
  exceeds X over the trailing 15 min (X Type 2, default 25% — strictly stronger than the
  existing trip per K6/P3). Exit/hysteresis: signal below X/2 for ≥10 min; minimum 10 min
  dwell per step; every transition event-logged (`failopen_step{n}_entered {cause, at}`).
- **Escalation 1→2→3 on page-rate pressure** (independent of storm state per D-2/I-3):
  - Step 1 → 2: no fresh signed policy available (compiled policy past `valid_until` ⇒
    auto-fall to step 2 + alarm, C5), OR step-1 disposition page rate > page budget.
  - Step 2 → 3: step-2 page rate (critical-only, deduped) still > page budget for >5 min.
- **De-escalation** in reverse on the same hysteresis. Full recovery emits
  `failopen_recovered` with the step history.

### 3.2 Step semantics

- **Step 1 — last-known-good compiled policy.** The gate runs the last signed policy
  version's *deterministic* rules (threshold table, allowlist, guardrails) without the Jev
  call. Freshness-gated: policy past its `valid_until` fails step 1 into step 2
  automatically (C5; stale fallback is worse than no fallback). Dispositions carry
  `mode: failopen_step1` in-band on every row.
- **Step 2 — static severity floor.** Page only alerts whose *source-declared* severity is
  critical (never model-derived — the model is the thing that's down), deduped on
  `dedup_key`. Everything else held for digest. The estate's severity mapping
  (name + version) is shown in the banner.
- **Step 3 — rate-capped paging + scannable digest.** Critical-first ordering,
  **novelty-within-severity** (per-fingerprint z-score on count vs its own 24h baseline —
  catches level-shifts, not just ramps; §4), deduped. Remainder to the digest. Cap defaults
  to the tenant's agreed page budget (Type 2). The digest is triage, not a parking lot.

### 3.3 Violet-banner UI contract (per step — never silent)

Every step raises the violet system-channel banner. The banner ALWAYS carries: step name,
cause (with the watchdog's numbers), **start time (UTC+local)**, step history for the
incident ("step 1 03:12–03:31, step 2 03:31–"), one-line semantics, runbook link, and the
detector-health line: `Storm detector: W(t)=… distinct/60s, threshold=max(F, k·B)=…,
last declared <t>`.

Per-step additions:
- **Step 1:** "Deterministic fallback — model unavailable." Policy id, signed-at,
  valid-until. Per-row `mode: failopen_step1`.
- **Step 2:** "Severity floor — paging CRITICAL only." Severity mapping name/version.
  "N held for digest." Per-row `mode: failopen_step2`.
- **Step 3:** "Rate-capped — M paged, N held in digest (**of which M_c are
  source-critical**, P2)." Digest top-3 by level-shift z-score, one click from the banner.
  Per-row `mode: failopen_step3`.

(Data/AI constitution: steps 1–2 are deterministic by construction — no model output
anywhere in the degraded path. Product constitution: the degraded path is the *most*
legible path, because it is the path the operator walks during an incident.)

---

## 4. Pre-mortem — "The Baseline That Ate the Storm"

*Assumed: it is April 2027. Stepped fail-open has been in production for 6 months. It has
caused a real incident. This is exactly how.*

**The setup.** A design partner ran a three-week noisy migration in February. W(t) sat at
4× normal for weeks — deploy noise, backfill alerts, a misconfigured canary spamming a new
check name. The baseline B adapted upward, as designed. The V3 attestation guard existed —
but it was operationalized as a weekly review email, and the on-call lead auto-attested it
twice ("looks like the migration, fine"). ADR-022's rubber-stamp ratchet, wearing the new
hat Tripwire predicted. B ended 12× above the estate's true normal.

**The incident.** 02:10, a config push takes down a region. Fingerprints spike 8× over the
*original* baseline — but only 1.5× over the poisoned B. **The storm never declares.**
40,000 distinct fingerprints hit the steps individually. The vendor degrades simultaneously
(their incident — vendor slowest when traffic is highest, the five-whys correlation from
PR #51 §C1.2). Watchdog enters step 1. Nothing folds (no storm declared), so step-1
disposition volume explodes past the page budget within minutes → step 2 (severity floor,
critical only). The incident's most important alert is source-severity **"major"** — the
estate's severity mapping was never tuned for this new service; the failure is novel.
Critical-only pages miss the actual failure signal. Step 3 engages on rate pressure.
The banner reads correctly: "Step 3 — rate-capped, 12,400 held in digest." The on-call
opens the digest's top-novel list — sorted by **rate-of-change**. The incident's root-cause
fingerprint spiked *early* then plateaued (one alert per host, 400 hosts, then flat);
a noisy retry-storm fingerprint grew monotonically and topped the list. **The on-call
chases retry noise for 40 minutes.** Region down 67 minutes.

**The review's finding.** The steps worked as designed. Two design assumptions failed
together — the coupling Tripwire warned about:
1. The adaptive baseline was poisoned by chronic noise through a rubber-stamped guard.
2. "Novelty" was defined as the wrong derivative: rate-of-change misses the level-shift
   signature of a real SEV (a step function has zero derivative after the step).

### 4.1 Mitigations designed into this RFC

- **M1 (level-shift triage):** digest triage ranks by BOTH level-shift (per-fingerprint
  count z-score vs its own 24h baseline) and ramp; the banner's top-3 lists biggest
  level-shifts first. Per-fingerprint hourly counts reuse the correlator's existing
  per-fingerprint state (bounded LRU) — no new state system.
- **M2 (critical visibility):** step-3 banner always shows held source-critical count
  separately (P2) — a critical-heavy digest is visibly a different emergency.
- **M3 (honest friction on the guard):** the V3 attestation UI cannot sign without the
  reviewer viewing the baseline-growth chart (dwell-gated); auto-attest is forbidden.
  The 30-day k review clock (B3 lifecycle) is automatic — no human may postpone it.
- **M4 (K5):** if any production drill shows a level-shift SEV signature ranking below
  position 5 in the digest triage, step-3 ordering reverts to pure chronological pending
  redesign — the drill decides (settles D-1).
- **M5 (I-3/D-2):** step escalation on absolute page-rate pressure is independent of storm
  declaration — a poisoned baseline cannot pin the system in step 2 while the digest fills.

---

## 5. Type labels, alternatives, kill conditions

### 5.1 Type 1 (contract — RFC-grade, slow to change)

The step contract (§3.2 semantics, §3.1 transition rules, §3.3 banner contract); the
deterministic-only degraded path (no model output in steps 1–2); never-fail-silent
(C rejected — silence is a failure mode); the velocity-ratio declaration semantics (§2.1)
and invariants I-1/I-2/I-3; the B3-lifecycle governance of k/F/B-growth (Vault);
the C2-replay acceptance gate (F1) as a condition of first canary.

### 5.2 Type 2 (tunable from production data — decide fast, roll back)

k (default 5, from the P99.9+1.5 rule), F (default 20), B windows (24h baseline, 6h
half-life, 60s tick), watchdog enter/exit X and X/2 (default 25%), min dwell 10 min,
escalation windows (5 min), rate-cap number (tenant page budget), digest triage
weights, attestation dwell friction.

### 5.3 Alternatives considered and rejected

- **A. Keep naive page-everything as the permanent semantic.** REJECTED for the degraded
  regime: C2 measured the storm case the steps exist for (§5 saturation; §9.5 — at high
  volume page-everything fail-open IS the incident). KEPT as the sub-trigger default:
  below the watchdog's sustained signal, behavior is exactly today's fail-open (no snap
  calls; Tripwire's kill shot in PR #51 is answered by evidence, not prose).
- **B. Adaptive detector WITHOUT the steps.** REJECTED — C2 §9.3: moves the failure to
  the race pool (~180 concurrent vendor slots needed vs 32 workers → mass timer-wins).
- **C. Steps WITHOUT the adaptive detector.** REJECTED — the detector decides coverage;
  the uncovered regime is busy-estate + degraded vendor, which needs both halves.
- **D. Single rate cap, no steps.** REJECTED (PR #51): re-derives the steps with the
  transitions unspecified — unpredictable beats complex.
- **E. Fail-open → digest-only.** REJECTED HARD: silence is a failure mode.
- **F. Per-tenant selectable fail-open policy.** PARKED (PR #51): revisit as Type-2
  configuration once production step data exists to show tenants.

### 5.4 Kill conditions

- **K1:** C2-replay acceptance fails (declares on steady busy-estate OR misses embedded
  storms) → kill the velocity signal, keep the fixed floor.
- **K2:** production declaration precision < 0.5 over any 30-day window → kill the signal.
- **K3:** any missed real storm (recall failure, §2.3) → kill the signal.
- **K4:** flap rate > 1 transition/hour in sustained marginal-degradation drills → kill
  the steps, keep naive fail-open (PR #51 K1).
- **K5:** level-shift SEV signature ranks below position 5 in digest-triage drill →
  revert step-3 ordering to chronological (settles D-1).
- **K6:** the watchdog's sustained-degradation signal cannot be tuned to separate from
  normal slow-vendor noise in drills → the steps never engage; keep naive fail-open
  (Pager P3).

---

## 6. Evidence table — C2 measurements vs the 5 activation conditions

| # | Condition (PR #51 §C1.9) | C2 evidence | Status |
|---|---|---|---|
| C1 | ADR-022 suppression-rate watchdog with dead-man's-switch heartbeat exists and is drill-proven — the trigger signal is real before the steps respond to it | Watchdog exists on main (PR #57 merged: `SuppressionWatchdog` + `HeartbeatEmitter` + `WatchdogRunner`, D8 closed). C2 measured the signal live: 1–2 watchdog trips per run at *normal* sustained load with a merely slow vendor (p95 3.4s). | **PARTIALLY SATISFIED** — exists and fires. **OUTSTANDING:** drill-proven as the *sustained-degradation step trigger* (marginal-degradation hysteresis behavior, dead-man's-heartbeat path test). The 7/7 fault-injection drills cover the simple fail-open, not watchdog→step transitions. |
| C2 | Load test demonstrates the storm case: sustained vendor degradation at ≥10× the day-one tier produces a page rate the on-call cannot survive under naive fail-open | C2 measured the saturation *mechanism*: fixed 20/60s → 97–99% fold under uniform-distinct traffic at 1k/min (race sees 0.2–2.1%); the counterfactual arithmetic — no calibration fit means raced traffic pages at ~39% (35% page_business_hours + 4% page_now over 3 sustained runs), and the race pool would need ~180 concurrent vendor slots vs 32 workers → mass timer-wins. Burst at 10k/min (10× the 1k/min sustained tier): 96% served, 4% loud resets. | **PARTIALLY SATISFIED** — the mechanism and the arithmetic are measured. **OUTSTANDING:** the controlled naive-fail-open page rate under *sustained vendor degradation with the adaptive detector* — the true counterfactual cannot be measured until the detector is adaptive. The drill plan specifies this measurement against the C2 raw JSONs (`research/load-c2/`) before activation. |
| C3 | Each step has its own fault-injection drill, including a multi-hour step-2 run and a flap test for the hysteresis | No step drills exist — the steps are not implemented (this RFC is design-only; implementation is a separate lane). C2 is measurement, not drills. | **OUTSTANDING** — implementation lane's drill matrix. |
| C4 | The digest-triage requirement (§C1.5 PM-2 mitigation) is a designed Prism screen — the banner names held counts, top novel fingerprints, and the digest is one click from the banner | Requirement specified (§3.3 banner contract, §4.1 M1/M2). No Prism screen designed. | **OUTSTANDING** — Prism's domain. |
| C5 | Step 1's freshness gate (`valid_until` ⇒ auto-fall to step 2) is implemented and tested, not documented | Config-bundle `valid_until` exists in the B3 design; enforcement (fail-to-step-2 on stale policy + alarm) is not built. | **OUTSTANDING** — implementation lane. |

**Reading the table:** the design is adopted; activation is gated. C1 and C2 are partially
satisfied by C2's measurement (the trigger signal is live; the storm-case mechanism is
measured); C3–C5 are outstanding by construction — they belong to the implementation and
Prism lanes. No step activates before its row turns green.

---

## 7. Done-checklist (principal-systems), answered in writing

1. **10× scale without architectural rewrite?** The steps are volume-responsive by design —
   step 3 exists for the 10× case — and the detector is now estate-relative, so it scales
   with the denominator instead of fighting it. At 10× the validated tier the steps need
   re-examination (step 2's "page critical only" at 100k criticals/min is still a storm).
   The ladder buys one order of magnitude, not infinite. Stated, not hidden.
2. **Third-party dependency down 4 hours?** That IS the design case: vendor down 4h ⇒
   step 1 (fresh deterministic policy) for as long as the policy is fresh, else step 2
   (severity floor), step 3 if page-rate pressure demands it. PagerDuty down 4h is unchanged
   (outbox + secondary, ADR-012). Vendor down 4h AND no fresh policy ⇒ step 2 for 4h —
   the severity-floor path must be drill-proven for multi-hour runs (C3).
3. **Junior deploys a stale config?** Caught: step 1 refuses a policy past `valid_until`
   and falls to step 2 + alarm (C5). The failure mode is "degrades one more step and tells
   someone," never "pages on a 6-month-old severity mapping silently."
4. **Local optimization at global cost?** The honest risk: three steps + hysteresis +
   per-row labels + adaptive detector is real complexity on the safety path — the path
   where complexity kills. The global counter-argument: the complexity is bounded and
   legible (three named steps, one ratio, each with one job), versus the unbounded
   illegibility of "page everything and hope the human survives" and the silent coverage
   collapse of the fixed threshold. Tripwire's dissent (D-1, §1.4.2) is carried, not buried.
5. **Truck-factor docs?** The step contract + event-logged transitions + detector-health
   banner line + the runbook ("what each banner means") are the docs. Gap named honestly:
   the digest-triage UX (C4) is a Prism design problem this RFC specifies but does not solve.

---

*Panel: Forge · Vault · Pager · Tripwire. Disagreements D-1 (open, settled by K5's drill),
D-2 (resolved — Tripwire's independent volume trigger adopted), D-3 (resolved — K6).
Decisions take effect on Petu's PR review (founder-deputy ratification). Until then: PROPOSED.*
