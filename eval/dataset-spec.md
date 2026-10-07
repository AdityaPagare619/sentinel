# Sentinel eval dataset spec

**Owner:** LEDGER (data lead) · branch `lane/ledger-datasets`
**Status:** v1 — covers the v0.1 synthetic generator and the calibration frame it feeds.
**Companion:** `eval/feedback-join-design.md` (how real outcomes join back to these rows).

> Every number in Sentinel's calibration story traces to a dataset. This document
> defines the three datasets the campaign runs on (D1/D2/D3 from the JEV product
> report §8), how each is generated or gathered, and the versioning rule that
> keeps a stranger from mixing them up. Synthetic data proves plumbing, never
> production accuracy — that line is load-bearing.

---

## 1. Dataset families

| Family | Name | Source | Purpose | Caliber |
| --- | --- | --- | --- | --- |
| D1 | STORM (synthetic storm) | `sentinel.synthetic.generate_alerts` | Regression anchor; local-gate regression; harness plumbing | Proves the pipeline, never the model |
| D2 | SHADOW (shadow-mode decisions) | Partner's real alert stream in shadow mode | First real calibration curve | Proves the curve on *their* stream |
| D3 | PARTNER (outcome-labeled) | D2 decisions + feedback join | Threshold tuning; go-live bars | The evidence a buyer signs |

D2 is produced by the 2-week shadow protocol (report §8: explicit go-live bars).
D3 is D2 joined to labeled outcomes via `eval/feedback-join-design.md`.

---

## 2. D1 — the synthetic storm dataset

### 2.1 Generator identity and reproducibility

- **Module:** `src/sentinel/synthetic.py` (lane `lane/code-mvp-v0.1`), entry point
  `generate_alerts(n, seed) -> list[tuple[Alert, dict]]`.
- **Deterministic:** same `(n, seed)` → byte-identical output. Seeded
  `random.Random(seed)`; no wall-clock, no network, no threads.
- **Canonical anchors:**
  - `n=2000, seed=7` — the v0.1 regression anchor. This exact run produced the
    v0.1 calibration report (severity_acc=0.9765, team_acc=0.9785, disp_acc=1.0000,
    ECE_q1=0.0956, ECE_q3=0.0819, false_suppress=0.0000, flip=PASS, shuffle=PASS).
  - `n=10000, seed=7` — the full storm; used when bin counts in the suppression
    region must be dense (see §3.2).
- **CLI:** `PYTHONPATH=src python -m sentinel.synthetic --n 2000 --seed 7
  --label-noise 0.0 -o labels.jsonl` emits tuner-ready rows.
- **Reproduce-from-scratch command** (no checkout needed beyond the pinned lane):
  ```
  git archive <lane> src/sentinel | tar -x
  PYTHONPATH=src python -m sentinel.evalharness --n 2000 --seed 7 -o calibration-report.md
  ```
  (The harness replays the synthetic alerts through the Gate with a mock Jev
  client scripted from the labels — see §2.4.)

### 2.2 Row schema

Each row is one `(Alert, label)` pair.

**Alert fields** (`src/sentinel/models.py` contract):

| Field | Value in STORM |
| --- | --- |
| `alert_id` | `syn-{seed:04d}-{i:06d}` (e.g. `syn-0007-000000`) |
| `received_at` | ISO-8601 UTC, base 2026-09-01T00:00:00Z, +53 s per alert |
| `fingerprint` | `sha256(service\|check\|severity_in\|region)[:16]` — fixed 10-template noise pools so history accumulates per fingerprint (this is what makes the tuner's per-fingerprint allowlist heuristic work) |
| `service` / `check` / `severity_in` / `title` / `region` | 10 services × 8 checks × 3 regions; `severity_in` ∈ {warning, info, critical} |
| `source` | `"alertmanager"` |
| `labels` | `{env: prod, region, cluster: <region>-a}` (+ `deploy_id` on deploy-adjacent rows) |
| `metric_value` / `metric_threshold` / `breach_duration_s` | thresholds 80.0; values breach ×1.05–×4.0 depending on kind |
| `raw` | `{generator: "synthetic", kind: <one of four>, history_hint: {n30d, paged30d, sev12_30d, median_autoclear_min}}` |

**Label sidecar** (the ground truth the harness scripts from):

| Field | Meaning |
| --- | --- |
| `severity_true` | `known_noise` \| `p4_low` \| `p3_medium` \| `p2_high` \| `p1_critical` |
| `became_sev12` | 1 if a SEV1/SEV2 incident followed, else 0 — **the outcome variable for the suppression-safety frame** |
| `team_true` | owning team from the service map (product_backend / security / data / network / platform) |
| `disposition_true` | `suppress` \| `page_now` \| `page_business_hours` |
| `auto_cleared` | 1 if the alert self-cleared without human action (noise rows) |
| `allowlist_candidate` | 1 iff the row is eligible for the suppression allowlist (`known_noise` only — never a SEV) |

**Design invariants (baked into the generator, enforced by tests):**

1. The noise fingerprint pool and the normal pool share **no** `(service, check, region)`
   triple — a p3/p4 alert can never land on a known-noise fingerprint and be
   wrongly suppressed.
2. Only `known_noise` rows are `allowlist_candidate=1` — suppression is reserved
   for alerts that have *historically never* been a SEV.

### 2.3 Volumes and mixture (measured, not nominal)

Generator mixture probabilities: 55% `known_noise` / 20% `deploy_adjacent` /
20% `p3p4_warning` / 5% `real_sev`. **Measured** counts (deterministic at seed 7):

| n | known_noise | deploy_adjacent | p3p4_warning | real_sev | became_sev12=1 |
| --- | --- | --- | --- | --- | --- |
| 2000 | 1123 | 367 | 406 | 104 | 120 (6.0%) |
| 10000 | 5597 | 1960 | 1954 | 489 | 581 (5.8%) |

Severity mix (n=2000/seed 7): `known_noise` 1123 · `p3_medium` 643 · `p4_low` 114 ·
`p1_critical` 73 · `p2_high` 47. Disposition mix: `suppress` 1123 ·
`page_business_hours` 757 · `page_now` 120.

**Noise/critical ratios.** At the canonical anchor, 1880:120 → **15.7:1** noise-to-SEV12
(6.0% criticals). Real on-call streams run 95–98% noncritical noise (report §7.1).
STORM is deliberately *near-field but slightly richer in criticals* — dense enough
to populate the high-P(sev) bins of the reliability diagram (§3.2), honest enough
to be labeled as optimistic on base rate.

### 2.4 How the v0.1 generator maps to the eval rows

The eval harness (`src/sentinel/evalharness.py`) never touches Jev. It replays each
synthetic row with a mock Jev client scripted **from the label**, so the pipeline
under test is receiver → correlator → gate → audit; the model is held constant:

1. `script_answers(alert, label, rng, label_noise)` builds the Jev-style answer
   triple: Q1 severity probabilities jittered ±10% relative around
   ground-truth-conditional templates, **rounded to 0.01 exactly like the Jev
   wire format**, renormalized to sum to 1.0; Q2 team choice; Q3 disposition
   choice with confidence 0.90–0.98 for `suppress` (clears the triple-lock bar)
   and 0.80–0.98 otherwise.
2. `label_noise` (default 0.0) flips the scripted argmax with that probability —
   a crude stand-in for Jev's measured 1.3–2.2% answer flips (report §5.2).
3. The mock returns these as a `DecisionResponse(model="jev-mock-1.0", ...)`; the
   harness records the gate's real `Disposition` and audit row per alert.
4. `labels_jsonl_row` emits the tuner format: `{fingerprint, q1_probs,
   q3_confidence, became_sev12, would_page_baseline}`.

**What this mapping proves:** the gate's expected-cost threshold math (triple lock:
`P(p1) < 0.002 AND q3_conf ≥ 0.90 AND fingerprint ∈ allowlist`), the ECE/bin
machinery, the coverage@τ ladder, the flip/shuffle probes, and the audit join
keys — end to end. **What it cannot prove:** anything about the real Jev model's
calibration on a partner's stream. The v0.1 report's HONEST LIMITATIONS section
says this outright, and every dataset version carries the same warning.

### 2.5 Provenance table (reproducible by a stranger)

| Artifact | Produced by | When | Seed / config | Generator ref |
| --- | --- | --- | --- | --- |
| Canonical labels.jsonl (n=2000) | `python -m sentinel.synthetic` | any time | `--n 2000 --seed 7 --label-noise 0.0` | `synthetic.py::generate_alerts` @ lane `lane/code-mvp-v0.1` |
| Full-storm labels.jsonl (n=10000) | same | any time | `--n 10000 --seed 7` | same |
| v0.1 calibration-report.md | `python -m sentinel.evalharness` | 2026-10-02 | `--n 2000 --seed 7` | `evalharness.py` @ same lane |
| This spec's measured mixture tables | LEDGER (Aditya's SRE wave) | 2026-10-03 | n=2000/10000, seed 7 | generator + harness, byte-verified in `/tmp/syncheck` |

Rule: anyone regenerating a D1 artifact uses **the same seed and the generator
code at the commit recorded in `dataset_version`** — and must get byte-identical
JSONL (the generator asserts no wall-clock input; if it doesn't reproduce, the
version string was wrong, not the data).

---

## 3. The calibration dataset shape

### 3.1 The two calibration frames

Sentinel reports calibration in two frames because two different questions are
being bought:

| Frame | Predicted quantity | Outcome variable | Bin axis | Answers |
| --- | --- | --- | --- | --- |
| A — top-1 calibration (standard) | `q1_conf` (confidence of the severity argmax) | 1 iff `q1_choice == severity_true` | 10 equal-width confidence bins | "Is the model's confidence what it says it is?" |
| B — suppression safety (the buyer's frame) | `P(p1_critical)` from `q1_probs` (+ `P(p1)+P(p2)`) | `became_sev12` | P(p1) bins | "When you suppressed, how often was it actually a SEV1/2?" |

Frame A is the v0.1 harness's ECE-10 on Q1 severity and Q3 disposition
(0.0956 / 0.0819 at the canonical anchor). Frame B is what the reliability
diagram on the design-partner dashboard shows — and what the false-suppress-on-SEV1
rate (0/120 = 0.0000 at the anchor) summarizes.

### 3.2 Bins and the minimum-N rule (credible ECE)

Methodology follows Guo et al. 2017 (accessed 2026-10-03): predictions binned
into M equal-width bins (M=10 conventional); per bin,
`conf(B_m) = mean predicted probability`, `acc(B_m) = observed positive fraction`;
plot acc vs conf — a perfectly calibrated model lies on the diagonal; points
**below** the diagonal are over-confident. Scalar summary:
`ECE = Σ_m (|B_m|/n)·|acc(B_m) − conf(B_m)|`; report the worst bin as
**MCE = max_m |acc(B_m) − conf(B_m)|** alongside. Always state M, and always draw
the bin counts: *a bin with six items tells you nothing* — sparse high-confidence
bins on rare-positive data are routinely near-empty.

Minimum N per bin (Sentinel rule, enforced by the harness/dashboard):

| Bin class | Minimum N | What we show if N is smaller |
| --- | --- | --- |
| Standard ECE bins (frame A) | **30 decisions** | Bin shown grayed with Wilson 95% CI, excluded from the ECE sum |
| Decision-critical bins (frame B suppression region, P(p1) < 0.002 and the adjacent bin) | **100 decisions** | Bin labeled "insufficient evidence" — never presented as calibrated |

Rationale: at N=30 the Wilson interval on a 5% observed rate is ~±8pp — the
coarsest resolution at which "0.9 means ~90%" is a falsifiable claim. The
suppression region is where a false negative costs $300K–$5M/hr of downtime, so
it gets the stricter bar. At the canonical anchor the P(p1) < 0.002 bin holds
~1,895 rows (P(p1) histogram x0.1: 1895 / 32 / … / 73) and the triple-lock
eligible set holds 1,123 rows — the strict bar is comfortably met on D1. At a
partner's scale (4,330 alerts/day, report §7.1), the suppression bin refills in
**hours**; the two-week shadow protocol exists precisely to accumulate the
SEV-positive tail.

The v0.1 anchor itself demonstrates why the rule matters: 7 of 10 frame-A bins
are empty (all scripted confidences ≥ 0.7) — ECE 0.0956 is computed over 3
populated bins, and the report prints that, not a ten-bin illusion.

### 3.3 What one calibration row contains

For every decision (D1 replay or D2 shadow), the calibration table joins:

```
{ alert_id, fingerprint, input_sha256, jev_model, q1_choice, q1_conf,
  p_p1, p_p2, action, reason,            # from the decision row
  became_sev12, auto_cleared, labeled_at, # from the feedback join (D3)
  bin_a, bin_b }                          # frame-A / frame-B bin indices
```

Null-outcome rows (labels not yet mature — see the join design) are **excluded**
from the ECE denominator and counted separately, so a growing shadow never
dilutes the curve with unknowns.

### 3.4 Companion metrics (always shown with ECE)

Per the campaign's standing metrics set (report §8): severity / team-routing /
disposition accuracy; **ECE + MCE** (M stated); **coverage@τ for τ ∈ {0.7, 0.8, 0.9}**
(anchor: Q3 coverage@0.9 = 0.7705 — the fraction of decisions the system was
confident enough to own); flip rate < 2% (200 alerts × 100 repeats); option-shuffle
< 3%; and the trust metric — **false-suppress rate on confirmed SEV1s**
(0/120 at the anchor; reported as a rate **with N**, never as a bare "0%").

---

## 4. Dataset versioning rule

Every dataset artifact carries a `dataset_version` string, and the simulator /
harness **accepts only versions it understands** — a version mismatch fails
loudly, never silently.

### 4.1 Format

```
D{1|2|3}.{FAMILY}-v{major}.{minor}[+{org}][.{yyyymmdd}]
```

Examples:
- `D1.STORM-v1.0` — canonical synthetic storm, generator @ lane `lane/code-mvp-v0.1`
- `D1.STORM-v1.1` — same schema/mixture, new seeds or additional fixtures only
- `D2.SHADOW-acme.20261010` — acme's shadow decisions, two-week window starting 2026-10-10
- `D3.PARTNER-acme.20261024` — D2 + matured outcome labels (see the join design)

**Bump rules:** `major` bumps when anything a stranger would call "a different
dataset" changes — generator code, mixture probabilities, schema, label
semantics. `minor` bumps for seed additions, fixture count changes, or
re-exports. D2/D3 are dated because the data is the partner's stream at a
time; a re-cut of the same window is a new version, full stop.

### 4.2 What the version pins

The version string is written into `labels.jsonl` (one `dataset_version` header
row) and into every calibration report. It resolves to an immutable triple:
**(generator code ref, seed/config, mixture+schema hash)**. The harness startup
check compares the artifact's version against its accepted list; on mismatch it
exits non-zero with "dataset X not accepted by simulator Y — regenerate or pin".

### 4.3 Retention

D1 artifacts are regenerable and disposable (keep the last three minors).
D2/D3 are partner data: retained per the partner's agreement, never mixed
across orgs, never used to tune another org's thresholds without explicit
consent. D3 label sets that cross 200–500 labels become the fine-tuned-encoder
training corpus (the report's standing escape hatch) — that transition is itself
a versioned event in the decision log.

---

## 5. References

- JEV product research report, `~/workspace/jev-product-research/JEV_RESEARCH_REPORT.md`,
  §5.2 (calibration evidence: ECE 0.032–0.284 by domain; 50–300 own labels to fit;
  Jev non-determinism 1.3–2.2%), §7.1 (SRE field: 95–98% noise; 4,330 alerts/day),
  §8 (MVP: D1/D2/D3 datasets; metrics; 2-week shadow protocol; weekly rolling ECE,
  base-rate + confidence-histogram drift monitors; monthly re-fits; model pinning).
- Calibration methodology: Guo et al., "On Calibration of Modern Neural Networks"
  (2017), §2 — 10 equal-width bins, ECE/MCE definitions; Niculescu-Mizil &
  Caruana binning convention; Roelofs et al. 2021 (binary-ECE as an *estimate*).
  All accessed via public web sources on 2026-10-03.
- v0.1 code: `src/sentinel/{synthetic,evalharness,gate,audit,models}.py` on lane
  `lane/code-mvp-v0.1`; v0.1 calibration report numbers reproduced by LEDGER
  2026-10-03 in an isolated archive (`/tmp/syncheck`).
