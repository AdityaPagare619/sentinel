# Sentinel feedback join design

**Owner:** LEDGER (data lead) · branch `lane/ledger-datasets`
**Status:** v1 — implements the D2→D3 transition from `eval/dataset-spec.md`.
**Read with:** the dataset spec (§3 calibration frames) and `src/sentinel/audit.py`
  on lane `lane/code-mvp-v0.1` (the `decisions` / `outcomes` schema this extends).

> The audit log records what the system *believed*. The feedback join records
> what *happened*. Without it there is no calibration evidence — only marketing
> with a confidence interval. This document is the protocol that turns alert
> outcomes into the reliability diagram a buyer trusts.

---

## 1. What gets joined to what

**Decision side (already exists, v0.1 `decisions` table).** One row per gate
decision: `alert_id`, `fingerprint`, `input_sha256`, `jev_model`, `q1_*` (severity
choice/probs/conf), `q2_*` (team), `q3_*` (disposition choice/conf), `action`
(page_now / page_business_hours / suppress / passthrough), `reason`, `latency_ms`,
`created_at`.

**Outcome side (`outcomes` table, v0.1 schema).** Keyed on `alert_id` (PRIMARY KEY):
`fingerprint`, `became_sev12` (0/1), `auto_cleared` (0/1), `mttr_min`, `labeled_at`.

**The join is a LEFT JOIN, decisions → outcomes, on `alert_id`.** Unmatched rows
(labels not yet mature) are *excluded* from calibration denominators and counted
separately — a growing shadow never dilutes the curve with unknowns.

### 1.1 Join keys — three of them, three different jobs

| Key | Scope | Job in the join |
| --- | --- | --- |
| `alert_id` | One alert occurrence (`syn-…` in D1; the receiver's id in D2) | **The join itself.** Exact match; stable from receiver → gate → labeler |
| `fingerprint` | `sha256(service\|check\|severity_in\|region)[:16]` — the alert *class* | **Aggregation.** History accumulates per fingerprint (the allowlist is per fingerprint, not per alert); per-fingerprint outcome rates power the tuner's allowlist heuristic |
| `input_sha256` | `sha256` of the canonical state JSON the Jev call saw (`sentinel.state.input_sha256`) | **The certificate.** Pins the exact state, questions, model name, and thresholds at decision time. A relabeled or re-run decision reproduces the same input hash or is flagged as a *different* decision |

Why three: `alert_id` is precise but singular; `fingerprint` is where statistical
power lives (the noise pool has 10 templates with `n30d` 40–200 occurrences each);
`input_sha256` is the auditability story — when a partner asks "what exactly did
the model see for this suppress?", the hash resolves to the archived state JSON.

---

## 2. The labeling protocol

### 2.1 Who labels

**The partner's own on-call** — the human who triaged the page or the incident —
labels outcomes during the shadow period and ongoing operation. Not us. They own
the ground truth; we own the protocol, the tooling, and the statistical guardrails.

- **Onboarding bootstrap (D3 backfill):** the org's last 30–90 days of
  PagerDuty/Incident history is backfilled heuristically — an alert that spawned
  a linked SEV1/SEV2 incident ticket ⇒ `became_sev12=1`; alerts in
  auto-resolve/no-action bins ⇒ `became_sev12=0, auto_cleared=1` — then a random
  5% sample is human-verified before thresholds are fit.
- **Ongoing:** after incident resolution (or auto-clear), the audit explorer
  presents a 30-second label card: "Did this alert correspond to a real
  SEV1/SEV2?" The labeler's identity and timestamp are recorded.

### 2.2 What each label means (load-bearing definitions)

| Label | Value | Meaning |
| --- | --- | --- |
| `became_sev12` | 1 | **Within 72 h of the alert, a SEV1/SEV2 incident was opened whose root cause is this alert's (service, check, region).** The labeler links the incident ticket id. |
| `became_sev12` | 0 | The 72 h watch window closed with no linked SEV1/SEV2, OR the alert auto-cleared with no human action and no customer impact. |
| `became_sev12` | NULL | **Watch window still open, or genuinely unresolved.** NULL is a first-class state, not "no". |
| `auto_cleared` | 1 | Alert stopped firing with no human intervention. |
| `mttr_min` | float | Minutes from first page to incident resolved (populated only when `became_sev12=1`; NULL otherwise). |

These definitions are shown verbatim on the label card. A label means exactly
what the table says — the calibration curve inherits no ambiguity beyond it.

### 2.3 Blinding and anti-gaming

- The labeler is **not shown the model's prediction or the action taken** before
  labeling (blinded) — this prevents anchoring ("the model said suppress, so it
  must have been noise") and protects the calibration evidence from the
  evidence-gathering process.
- Labeling is append-only: a relabel writes a new row with a new `labeled_at`
  and a reason; the previous label is retained. (v0.2: `outcome_events` table
  keyed `(alert_id, labeled_at)`; v0.1's `outcomes` table records latest with
  `labeled_at` bump + reason.)
- Suppressions get **mandatory** review sampling: 10% of `action=suppress` rows
  are sampled for outcome verification even when they auto-cleared — the
  trust metric (false-suppress on SEV1/2) must be measured, not assumed.

### 2.4 Disagreement handling

- During shadow, a 5% random sample is double-labeled by a second on-call.
- **Cohen's κ is computed and reported on the dashboard.** κ ≥ 0.7 ⇒ labels
  accepted as ground truth for that window. κ < 0.7 ⇒ the labeling protocol
  (not the model) is reviewed with the partner: definitions sharpened, label
  card rewritten, the window re-labeled.
- `became_sev12` is the *only* label that gates go-live; `auto_cleared` and
  `mttr_min` disagreements are recorded but never block.
- Customer-verified allowlist additions (the third lock of the triple lock)
  require **two SRE sign-offs** and are recorded in the decision log.

---

## 3. Latency: decision → label

| Outcome path | When the label matures |
| --- | --- |
| `auto_cleared=1` | Within hours — the alert stops firing on its own |
| Paged and resolved | At incident resolution + link, typically same shift |
| `became_sev12=1` (the tail risk) | **Up to the 72 h watch window** — a suppressed alert that becomes a SEV two days later is the failure the whole product exists to measure, so the window must be long enough to catch it and short enough to keep the curve fresh |
| Unresolved / no outcome | Ages out: after 72 h + 24 h grace with no incident link and no auto-clear signal, the row is marked `stale_pending` — excluded from ECE, counted in a separate "unresolved" line on the dashboard |

The **2-week shadow protocol** (report §8) exists to let the 72 h windows mature
over a meaningful stream: decisions in week 1 get labels, the calibration curve
is drawn at the end of week 2, and go-live bars are evaluated against *matured*
labels only.

---

## 4. From join to the reliability diagram the buyer sees

```
decisions (per alert: p_p1, q1_conf, action, input_sha256, jev_model)
   LEFT JOIN outcomes ON alert_id
   WHERE became_sev12 IS NOT NULL          -- matured labels only
   → bin (frame B: P(p1) bins; frame A: q1_conf bins)
   → per bin: n, mean_conf, observed_rate
   → reliability diagram + ECE/MCE + coverage@τ + false-suppress/N
```

**What the partner sees, in order — and why:**

1. **The safety number, first:** "False-suppress on confirmed SEV1/2: **0 / N**"
   (v0.1 anchor: 0/120). Always with N. This is the one number that could end
   the conversation — leading with it says we know what we're being trusted with.
2. **The reliability diagram (frame B):** "when we said P(SEV) was in this band,
   it was a SEV this often" — bin counts drawn under the curve, thin bins
   grayed per the §3.2 minimum-N rule. This answers "confident means right?"
   visually, in ten seconds.
3. **Coverage@τ ladder:** τ ∈ {0.7, 0.8, 0.9} — "this fraction of your alerts we
   decide confidently; the rest page as today." Maps directly to toil reduction.
4. **The ECE table (for the data scientist in the room):** frame-A ECE/MCE with
   M stated, per-bin n/acc/conf — the standard Guo et al. 2017 summary, so the
   numbers are checkable against the literature, not just our slides.
5. **The drift guards (for the 6-month skeptic):** weekly rolling ECE, base-rate
   monitor, confidence-histogram monitor, monthly per-org re-fit schedule,
   model-version pinning with 7-day re-validation (all per report §8). The
   calibration claim is *maintained*, not just made.

**Why this order convinces in a 15-minute onboarding:** each layer answers the
objection the previous layer creates. The safety number kills the extinction
fear; the diagram proves the probabilities mean something; coverage maps to
their toil; the ECE table satisfies the skeptic with credentials; the drift
guards answer "what about month six?". And the simulator is always one click
away: the threshold simulator lets the partner drag the suppression bar and
watch expected-cost math (C_FP vs C_FN) move *on their own labels* — the moment
calibration stops being our claim and becomes their measurement.

### 4.1 The shadow track record as sales collateral

Per the JEV report: the moat is "per-org outcome labels, per-team calibration
data, the shared probe set + shadow track record as sales collateral". The
feedback join is what manufactures that collateral: every shadow run produces a
matured D3 dataset, a calibration report, and a decision-log entry — a
verifiable artifact the *next* partner can inspect. D3 datasets never cross orgs
without consent, but the *existence* of the protocol and its artifacts is
itself the pitch.

---

## 5. Failure modes the protocol defends against

| Failure | Defense |
| --- | --- |
| Labeler rubber-stamps "no incident" to keep the curve pretty | Blinding (§2.3) + mandatory 10% suppress-review sampling + κ audit |
| Partner's incident taxonomy differs from ours (SEV definitions drift) | `became_sev12` is defined in §2.2 by *our* protocol, mapped once at onboarding; remapping is a versioned decision-log event |
| Alert storms create thousands of near-duplicate rows, overweighting one incident | Correlator dedups/storm-folds *before* the gate (gate.py `reason="dedup"`/`"storm"` rows carry no Jev answers and are excluded from calibration rows — one incident, one vote) |
| Model version changes mid-shadow | `jev_model` is a join column: calibration is computed *per model version*; a version change restarts the maturity clock with 7-day re-validation (report §8) |
| Base-rate shift (quiet week → incident week) | Base-rate monitor alerts when `P(became_sev12)` moves >2σ from the shadow window; thresholds re-fit, not re-assumed |

---

## 6. References

- `eval/dataset-spec.md` §3 (calibration frames, bin rules), §4 (D2/D3 versioning).
- JEV product research report §5.2 (calibration evidence), §7.1 (SRE field),
  §8 (MVP: shadow protocol, drift monitors, per-org re-fits, go-live bars).
- v0.1 `src/sentinel/audit.py` — `decisions`/`outcomes` schema this design
  extends (lane `lane/code-mvp-v0.1`).
- Calibration methodology: Guo et al. 2017 §2 (bins, ECE, MCE; "state M");
  reliability-diagram reading conventions (diagonal = calibrated; below =
  over-confident; always show bin counts) — public web sources, accessed 2026-10-03.
