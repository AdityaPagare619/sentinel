# 07 — Data Pedigree: where every number comes from

**Owner:** LEDGER (data lead) · branch `lane/principal-ledger`
**Judged by:** Law 7 §4 — Data/AI constitution: *data pedigree — every number traces to its source; deterministic guardrails over probabilistic models; the model advises, it never controls.*
**Builds on:** `eval/dataset-spec.md` (D1/D2/D3, calibration frames, versioning), `eval/feedback-join-design.md` (the join protocol), ORACLE expected-cost theory, Law 2 (eternal friction), Law 6 (pre-mortem).
**Status:** DESIGN ONLY — no implementation. Aditya's halt order (principal-redesign wave, Sat 2026-10-03).

---

## 0. First principles (Law 4): why pedigree is the product

Peel it:

- *Why does Sentinel need data pedigree?* → Because we sell **defensibility**, not accuracy (JEV report §7.1 tempering case: "incumbents sell defensibility").
- *Why is defensibility the thing being sold?* → Because our customer is trusting us with the right to **not wake them up**. A missed SEV1 costs $300K–$5M/hr; the buyer's question in the 15-minute onboarding is never "is it smart" — it's "when this fails, can I defend having used it?"
- *What does defensibility require mechanically?* → Every number we show — confidence, ECE, coverage, suppression counts, savings projections — must resolve, on demand, to the raw events and assumptions that produced it. Not "the dashboard said 0.031". The raw decisions, the raw labels, who made them, when, under what schema.
- *The irreducible truth:* **a calibration claim is a promise about the future, collateralized by the past.** Pedigree is the collateral paperwork. Without it the number is marketing.

**The one-year test (Law 6, made concrete):** It is October 2027. A partner's database failover was suppressed by Sentinel and became a SEV1. Their lawyers subpoena the calibration evidence behind the "False-suppress on SEV1/2: 0 / 412" line from our shadow report. The partner's SRE lead asks in a deposition-adjacent meeting: "Show me the 412." Pedigree is the design that makes that sentence answerable — not with a deck, but with the actual 412 rows, the label cards, the labelers, the timestamps, the incident tickets, and the exact model + thresholds + questions that produced each decision. If the design can't survive that meeting, the design is incomplete.

**Scope of this document:** the full lineage map (§1), corruption handling per source under hostility (Law 2, §2), the feedback join's failure modes (labels are data too — §3), provenance as a first-class design element (§4), and three choices we make differently from the industry (§5).

---

## 1. The lineage map: every displayed number → its raw events

Sentinel displays exactly ten families of numbers. Each entry below names: the raw events, the transformations, the assumptions, and the adversary that can break it (Law 2 names one adversary per happy path).

**N1 — Per-decision confidence** (`q1_conf`, `q3_conf`, `p_p1` / `p_p2`, displayed in the decision river, audit explorer, per-alert tooltips).

- **Raw events:** the TypeSafe Jev API response triple for the canonical state JSON. Jev wire format: typed answers with calibrated probabilities, probabilities rounded to 0.01, noul clamped [0.01, 0.98], up to 255 choices, no seed/determinism parameter (ORACLE operating file; JEV report §5.2).
- **Transformations:** NONE. The numbers are stored verbatim from the wire response into the `decisions` row. This is deliberate: any re-normalization, rounding, or "smoothing" applied between the provider and the audit row is an unaccounted transformation — the first place pedigree dies. The gate's policy math (expected-cost optimum, triple lock) consumes the numbers; it does not alter them.
- **Assumptions:** (a) the provider's probabilities are what the API says they are (we do not re-derive); (b) rounding to 0.01 is the last significant digit of honest precision — we never display more digits than the wire carries; (c) the response we stored is the response the gate acted on (pinned by `input_sha256` + response digest).
- **Named adversary:** a gateway/fallback engine (Vercel AI Gateway, OpenRouter) silently rewrites or re-rounds the JSON between TypeSafe and us — the number on screen no longer matches the number the model emitted. **Design answer:** store the raw response body digest at ingress; the audit row joins `input_sha256` → `response_digest`; any mismatch between the gate's consumed values and the stored digest fails the row into quarantine, and the decision degrades to passthrough (page, don't suppress). The model's word and our word are independently verifiable.

**N2 — ECE / MCE** (calibration screen headline card; shadow reports; go-live bars).

- **Raw events:** `decisions` rows (predicted probabilities) LEFT JOIN `outcomes` rows (`became_sev12`, matured labels only) — the feedback join (§3, and `eval/feedback-join-design.md`).
- **Transformations:** Guo et al. 2017 binning (10 equal-width bins, frame A on `q1_conf`, frame B on `p_p1`); per bin `conf = mean predicted`, `acc = observed positive fraction`; `ECE = Σ (|B_m|/n)·|acc−conf|`; `MCE = max |acc−conf|`. Null-outcome rows excluded from the denominator, counted separately. Bins with n < 30 grayed and excluded from the ECE sum; the suppression-region bins (P(p1) < 0.002 and adjacent) require n ≥ 100 or render "insufficient evidence" (dataset-spec §3.2).
- **Assumptions:** (a) labels are right (this is §3's entire subject); (b) the binning convention is fixed and stated (M=10 always shown); (c) the matured-label filter (72 h watch window + 24 h grace) does not bias the sample — i.e., late labels are missing-at-random. This assumption is load-bearing and auditable: §3.4 tracks the maturity-lag distribution and flags windows where late-maturing outcomes differ from early ones.
- **Named adversary:** systematic label error (§3.2) — ECE computed over confidently-wrong labels converges to a beautiful number that measures nothing.

**N3 — Coverage@τ** (calibration screen card; simulator projection cards).

- **Raw events:** `decisions` rows: the fraction of rows with `q3_conf ≥ τ` for τ ∈ {0.7, 0.8, 0.9}.
- **Transformations:** a count and a division. The only transformation is τ itself — which is why the screen shows the threshold value next to the number ("threshold 0.70 · 3,858 of 4,096 decisions above gate"). The denominator is the card, not a footnote (design spec, calibration screen §1, "Law L1").
- **Assumptions:** τ is the *current, versioned* gate threshold for that team, not a number someone typed into a slide. The calibration API resolves τ from the threshold registry at query time and stamps `threshold_version` on the response.
- **Named adversary:** a stale dashboard cache shows coverage computed under last month's τ next to this month's suppression counts — the two numbers describe different policies. **Design answer:** every aggregate response carries `{dataset_version, threshold_version, jev_model, computed_at}`; the UI refuses to render two cards from mismatched versions side by side (they render with a "versions differ — re-run" banner instead of silently blending).

**N4 — Suppression counts / page-reduction** (simulator cards; shadow reports; the "≥60% suppression" go-live bar).

- **Raw events:** `decisions` rows, `action` column (`suppress` / `page_now` / `page_business_hours` / `passthrough`).
- **Transformations:** counts, deltas between current and simulated thresholds (simulator replays the tuner binary on the shadow dataset — "same math as the tuner", design simulator §4). Deduped/storm-folded rows (`reason="dedup"`/`"storm"`) are excluded: one incident, one vote (feedback-join-design §5).
- **Assumptions:** (a) the baseline is "what the partner's paging did without us" — `would_page_baseline`, recorded per row at decision time from the receiver's view of the alert before Sentinel acted; (b) the shadow dataset is representative of production mix (base-rate monitor watches this, §2-S6).
- **Named adversary:** the correlator's storm-folding changes between versions — v0.3 folds 40 alerts into 1 row, v0.2 folded them into 3 — and the "suppressions went up 12%" line is really a correlator artifact, not a gate improvement. **Design answer:** suppression metrics are always reported as a *pair*: alert-level and incident-level (post-fold). The version that changed the folding rule gets a major dataset-version bump, and the report draws the fold-rule change as a vertical line on the trend. Folding-rule changes are Type 1 decisions (Law 3): RFC, named dissent, decision-register entry.

**N5 — False-suppress on confirmed SEV1/2: "k / N"** (the trust metric; calibration screen card #1; shadow report line #1).

- **Raw events:** `decisions` rows with `action=suppress` ⋈ `outcomes` rows with `became_sev12=1` (matured). The join is on `alert_id`; the outcome side is labeler-verified with the 72 h watch window.
- **Transformations:** a count, a division, and — critically — the N. The number is never displayed without its denominator ("0 / 412", never "0%").
- **Assumptions:** every suppressed alert actually got its 72 h watch window; the mandatory 10% suppress-review sampling ran; no suppressed alert was lost between the gate and the label queue (see §2-S7, the loss-detector).
- **Named adversary:** §3.3 — suppressed alerts that became SEV1s but were never labeled because the incident was filed under a different service or the labeler never got the card. k is right; N is wrong; the rate is understated. **Design answer:** N is computed as *suppressed decisions with matured outcomes*, and the card's sub-line names the immature count: "0 / 412 · 23 suppressions still in watch window". The immature set is separately listed in the audit explorer with their watch-window deadlines. A trust metric that hides its pending tail is a lie with good typography.

**N6 — Savings projection** (tuner CLI output; shadow savings report; the GTM top-of-funnel artifact).

- **Raw events:** labeled decisions (D3 rows) + the cost table: `C_FP` (cost of a false page — engineer interruption) and `C_FN` (cost of a missed SEV — downtime).
- **Transformations:** expected-cost arithmetic. Optimal threshold `p* = C_FP/(C_FP+C_FN)` (ORACLE). Projected savings = Σ over shadow rows of [cost of baseline action − cost of Sentinel action] at the tuned thresholds. The tuner emits the tradeoff table (suppression rate × false-suppress count) and a cost-sensitivity sweep (what if C_FN is $500k, not $50k — ORACLE mandate #3).
- **Assumptions:** (a) C_FP and C_FN are *estimates* — README honest limitation #7: defaults ($100 / $50k) are replaced by partner measurements; the projection is labeled with the cost-table version and its source ("C_FN from partner's 2026 incident cost ledger" vs "C_FN default estimate"); (b) the shadow mix predicts the production mix (base-rate assumption again); (c) suppressed noise has zero residual cost (no "suppressed but slowly degrading" state — §2-S4 names this adversary).
- **Named adversary:** the savings number is computed with the partner's most expensive historical SEV1 as C_FN — a $5M outlier — and the projection is 10× what any realistic year delivers. The buyer signs on a fantasy. **Design answer:** the savings report always shows three columns — *measured* (C_FP/C_FN from the partner's own incident cost records), *conservative* (10th-percentile incident cost), and *default-estimate* (flagged as such). The tuner refuses to emit a single headline savings number without the cost-table version and source line. A projection without a stated cost source is a rumor with a dollar sign (ORACLE mandate #1, applied to money).

**N7 — Flip rate** (calibration card; `/api/analytics/flips`).

- **Raw events:** the probe set — 200 fixed alert inputs re-asked to Jev 100 times each (v0.1: 200×100; the shared probe set per the JEV report moat) — plus production flip observations (same `input_sha256`, different response digest).
- **Transformations:** flip rate = flipped responses / total re-asks; reported with the 2%/3% watch bands (design calibration §1).
- **Assumptions:** the probe set is fixed and versioned (the "shared probe set" is sales collateral — it must not drift); production flips are observable because `input_sha256` is stable across retries.
- **Named adversary:** the provider silently changes the model behind the same model name ("jev" today is not "jev" last week) — the flip rate jumps and we misread a model change as stochasticity. **Design answer:** every Jev response carries the provider's model identifier verbatim; the flip monitor groups by (`model_id`, `input_sha256`); a model-id change is a first-class event that restarts the flip baseline and fires the 7-day re-validation clock (dataset-spec §5 / report §8). The flip-rate card's sub-line names the model id it was measured against.

**N8 — Per-fingerprint outcome rates** (the allowlist heuristic's input; tuner per-fingerprint tables).

- **Raw events:** `outcomes` aggregated by `fingerprint` (the alert *class*: `sha256(service|check|severity_in|region)[:16]`).
- **Transformations:** per-fingerprint `n30d`, `paged30d`, `sev12_30d`, `median_autoclear_min` (the `history_hint` in D1's `raw` sidecar; the real pipeline computes these from the audit log).
- **Assumptions:** the fingerprint is stable and collision-safe — see §2-S1 for the fingerprint-collision adversary (the Law 6 pre-mortem's allowlist collision is named in the laws themselves: two services, same check name, one hash).
- **Named adversary:** fingerprint collision across services (the pre-mortem). **Design answer:** the fingerprint is a 16-hex-char truncation — 64 bits — and the allowlist records the *full* `(service, check, severity_in, region)` tuple alongside the hash; the gate's allowlist lookup is on the full tuple, the hash is only the index. A collision produces an allowlist-entry conflict that fails closed (requires two-SRE sign-off to resolve, feedback-join-design §2.4) rather than a silent merge.

**N9 — Latency p50/p95/p99** (ORACLE's measurement campaign; the timeout-policy evidence; "inline" claim).

- **Raw events:** `decisions.latency_ms` — measured gate-side, from receiver-ingress to decision-write (not provider-reported).
- **Transformations:** percentiles over a versioned measurement window; cold-vs-warm split (first-call 11.4s measured 2026-10-02 is named and kept separate from warm-call statistics).
- **Assumptions:** the clock is monotonic (see §2-S7: clock skew on a multi-box deployment).
- **Named adversary:** the 11.4s first-call figure quietly gets averaged into the warm-call p99 and the timeout policy is set from a blended number that represents neither. **Design answer:** cold-start latency is a separate reported series, always, with its own N. The timeout policy cites the series it was set from. Blended latency is banned from policy documents.

**N10 — Drift-monitor statistics** (weekly rolling ECE, base-rate monitor, confidence-histogram monitor; report §8).

- **Raw events:** the week's `decisions` ⋈ matured `outcomes` (same join as N2, sliding 7-day window), plus the raw confidence histogram.
- **Transformations:** rolling ECE vs the shadow-window ECE (ΔECE with bootstrap CI); base-rate `P(became_sev12)` vs the 2σ band from the shadow window; PSI-style histogram shift.
- **Assumptions:** the shadow window remains the reference population (it is frozen as a dataset version, not a live query — §4.2).
- **Named adversary:** the reference window itself is quietly re-cut ("we refreshed the baseline") and the drift alarm that should have fired is normalized away. **Design answer:** reference windows are immutable dataset versions; re-cutting is a versioned decision-log event that restarts the monitoring baseline with a named reason. Drift is measured against the *declared* reference, and the declaration is auditable.

### The lineage summary (one table to rule the audit)

Every number above resolves through this chain:

```
displayed number
  → API response {value, n, error bar, dataset_version, threshold_version, jev_model, computed_at}
    → decisions ⋈ outcomes rows (or decisions rows, or probe rows)
      → alert_id → input_sha256 → archived canonical state JSON (the exact Jev input)
      → jev_model + response digest (the exact Jev output)
      → outcome row {became_sev12, labeler identity, labeled_at, label schema version, incident ticket link}
```

If any link in the chain is missing, the number is not displayed — it is replaced by its absence, labeled ("insufficient evidence", "provisional", "versions differ"). **Law 7 product constitution: every failure mode degrades to something useful, never to silence — and never to a confident-looking number.**

---

## 2. Law 2: every data source's corruption story

For each source: (a) how it corrupts, (b) how we detect it, (c) what the system does — the principled response, not "validate input".

### S1 — The partner's alert stream (webhooks: PagerDuty / Opsgenie / Alertmanager)

**How it corrupts:** (i) provider-side schema drift — a field renamed or a new severity enum appears without notice (Law 2's "APIs change unannounced"); (ii) duplicate delivery — at-least-once webhook semantics replay the same alert (research/webhook-audit-precedents: Stripe/Slack replay tolerance); (iii) clock skew and out-of-order arrival — an alert's `created_at` predates the previous one from the same source; (iv) payload truncation — a proxy or WAF clips a large payload mid-JSON; (v) a misconfigured integration sends test alerts into production (the classic).

**Detection:** (i) schema-version pinning — the receiver accepts only declared webhook schema versions; unknown fields are preserved (forwarded into `raw`) but never consumed by policy; a renamed-field alert fails schema check and is quarantined, not coerced; (ii) idempotency on the provider's event id — the receiver keeps a seen-id window (72 h, matching the watch window) and drops exact duplicates with a counter, not a second decision row; (iii) per-source sequence/arrival-time anomaly — an alert arriving with `created_at` older than the newest seen from that source is flagged `late_arrival` and excluded from storm-folding (it must not rewrite history); (iv) JSON parse failure or truncated-signature ⇒ quarantine; (v) test-alert detection: alerts matching the provider's test-event signature (known test check names, the partner's declared test service list) are routed to a `test` lane, never to the gate.

**Principled response:** the receiver is a *translator with a quarantine*, not a parser. Three lanes: `admit` (schema-valid, idempotent-new), `quarantine` (schema-unknown, truncated, unparseable — held for human triage, counted on a receiver-health dashboard, and *defaulted to page-through*: a quarantined alert pages as today, because the one thing we never do is drop an alert we couldn't understand), `test` (routed away from all metrics). **The product constitution's "never to silence" is implemented here:** quarantine ⇒ page-through ⇒ the human still gets woken; the machine just declines to opine.

### S2 — Jev model answers (TypeSafe API + gateway fallbacks)

**How it corrupts:** (i) the measured one: non-determinism, 1.3–2.2% flips on identical inputs (report §5.2) — this is not a bug, it is the physics of the source; (ii) dynamic rate limits that "disagree 6.25×" (night R&D sprint finding) — 429s under load, exactly when alerts storm; (iii) silent model change behind a stable name (§1-N7); (iv) fallback-engine divergence — a gateway serves a *different* model or a stale cache under the same API shape (the wire-compatible fallback survey's sharp edge); (v) latency blowup — the 11.4s first-call measurement vs the 70–500ms spec (ORACLE's open measurement question).

**Detection:** (i) the flip probe set — 200 fixed inputs × 100 re-asks, run nightly, grouped by model id; (ii) 429/5xx/timeout counters per provider with the 10s-timeout/2-retry policy's own telemetry (research/jev-behavior); (iii) model-id verbatim comparison on every response (§1-N7); (iv) the fallback matrix (ADR-003) is *measured*, not assumed — each fallback engine gets the probe set before it is allowed in the matrix, and its calibration delta is recorded; a fallback that answers differently is labeled, not silently substituted; (v) gate-side latency percentiles, cold vs warm split (§1-N9).

**Principled response:** the gate is *fail-open by construction* (README honest limitation #4; ARCHITECTURE): any provider error — timeout, 429, 5xx, schema-invalid response, model-id change — degrades the decision to `passthrough` (page as today), never to suppress. **The Data/AI constitution's "the model advises, it never controls" is implemented here:** the gate's suppression path requires the triple lock (P(p1) < 0.002 AND q3_conf ≥ 0.90 AND fingerprint ∈ allowlist), and *any* corruption signal on the advisory input dissolves the lock. Uncertainty pages. Corruption pages. Silence pages. The only thing that suppresses is a clean, versioned, triple-locked advisory.

### S3 — The incident system (PagerDuty/Opsgenie incident tickets — the triangulation oracle)

**How it corrupts:** (i) incident taxonomy drift — the partner redefines SEV levels and old tickets no longer mean what they meant; (ii) incident tickets filed late, or never — the on-call fixed it in Slack and the ticket is a stub; (iii) duplicate incidents for one root cause (the dedup-craft research: Opsgenie duplicate-alert questions are a known field reality); (iv) tickets linked to the wrong alert (the labeler's `became_sev12=1` points at an incident whose root cause is a different service).

**Detection:** (i) taxonomy mapping is versioned at onboarding (§1-N5 table); a remap is a decision-log event, and labels are re-interpreted under the mapping version they were made under — never re-read under a new taxonomy; (ii) the triangulation rule (§3.3) works both directions: a `became_sev12=1` label *without* a linked incident ticket is flagged `unanchored` and sampled at 100% (not 10%) — the label exists, but it carries less weight until anchored; (iii) incident-dedup: incidents sharing a root-cause link within the same episode collapse to one outcome vote (the correlator's episode linkage: a `reopened` event after `resolved` is episode linkage, not a duplicate — research/webhook-payloads).

**Principled response:** the incident ticket is the *higher-authority* source (§3.3), but it is not infallible — it is *externally anchored* (created by the partner's business process, not by our protocol). Disagreements between the label card and the ticket system resolve in favor of the ticket, and the disagreement itself is recorded as a label-quality event. This is the asymmetry that makes the whole join defensible: our labels are self-reported; their tickets are process-generated. Process beats self-report.

### S4 — The labeler (the partner's on-call; the label card; double-labels)

**How it corrupts:** (i) rubber-stamping — "no incident" on everything to keep the curve pretty or to end the card queue faster (the named adversary in feedback-join-design §5); (ii) anchoring — seeing the model's prediction before labeling (addressed by blinding, §2.3 of the join design); (iii) fatigue-order effects — the 40th card of a shift is labeled worse than the 1st; (iv) systematic misunderstanding — the on-call's mental model of "SEV" differs from the protocol's §2.2 definition (taxonomy drift at the human layer); (v) gaming — labels skewed to push thresholds in a preferred direction (more suppression ⇒ fewer pages for *their* team).

**Detection:** (i) κ on the 5% double-labeled sample, trended weekly — a falling κ is the smoke alarm (§3.2); (ii) the 10% mandatory suppress-review sample, compared against the labeler's own full-population labels — a labeler whose suppress-sample labels disagree with their routine labels at a higher rate than the double-label baseline is flagged; (iii) card-order analysis — label distributions by queue position, a standard survey-methodology check, run monthly; (iv) the unanchored-label rate (labels without linked tickets) per labeler; (v) cross-labeler comparison — labelers are never identified to each other, but their aggregate statistics are comparable, and an outlier is reviewed.

**Principled response:** labels are **evidence events with a quality budget, never ground truth** (§5, choice 1). A labeler's labels enter the calibration set weighted by their current quality score; when the score drops below the review threshold, their recent labels are quarantined (excluded from ECE, counted separately) and the labeling protocol — not the model — goes under review (the join design already says this for κ < 0.7; the redesign generalizes it to the full quality battery). The calibration screen's sub-line carries the label-quality budget: "κ 0.81 · triangulation 94% · 10% suppress-sampled". The buyer sees the uncertainty *of the evidence*, not just of the model.

### S5 — The cost table (C_FP, C_FN — the tuner's money inputs)

**How it corrupts:** (i) defaults treated as measurements — the $100/$50k estimates (README #7) calcify into "the numbers" because nobody replaced them; (ii) outlier anchoring — C_FN set from the single worst incident in company history (§1-N6); (iii) cost drift — the incident cost ledger is from 2024 and the architecture changed; (iv) asymmetric knowledge — the partner knows their costs; we don't; the tuner runs on our guesses.

**Detection:** the cost table is versioned with a *source field* per entry (`measured:<ledger-ref>`, `estimated:<who,when>`, `default`). The tuner prints the source line on every run. A cost entry older than 12 months or still `default` after the first shadow pilot triggers a "replace with measurements" nudge in the shadow report. The sensitivity sweep (C_FN at 0.1×/1×/10×) is mandatory output — if the optimal threshold doesn't move across the sweep, the costs don't matter and we say so; if it moves violently, the costs are the decision and we say that too.

**Principled response:** the savings report's three columns (measured / conservative / default-estimate, §1-N6). The tuner never emits a single headline savings number. Money is the most corruptible number in the building — it gets the most redundant reporting.

### S6 — The audit store (SQLite → Postgres; the decisions/outcomes tables)

**How it corrupts:** (i) the v0.1 single-box reality — SQLite on one box: disk-full, process-kill mid-write, no replication; (ii) schema migration that rewrites history (a migration backfills a column with a *new* default and the old rows silently change meaning); (iii) clock skew across boxes in the SaaS future — `created_at` ordering lies; (iv) retention deletion — the partner's data-retention policy deletes raw rows that a year-later audit needs.

**Detection:** (i) write-ahead discipline — the decision row is written *before* the action is taken (the audit row is the precondition of the page/suppress, not a log of it); a decision without an audit row is a bug, and the forwarder refuses to act without the row id; (ii) migrations are append-only in effect — new columns default NULL for old rows, and any backfill is a versioned migration with its own decision-log entry; the schema version is stamped on every row; (iii) monotonic logical clock (Lamport-style sequence per deployment) alongside wall-clock — ordering disputes resolve to the sequence, not the timestamp; (iv) the retention contract (§4.3) is signed at onboarding: what is kept, for how long, in what form — and the "one-year test" (§0) is the acceptance criterion.

**Principled response:** the audit log is **append-only and content-addressed** (§4.1). Rows are never updated in place: a relabel appends an outcome event; a threshold change appends a threshold version; a migration appends a schema version. The store is a ledger in the accounting sense — corrections are new entries, never erasures. (The name is the design.)

### S7 — The pipeline's own telemetry (loss, lag, and the watch window)

**How it corrupts:** (i) decision-to-label-queue loss — a suppressed alert's row never reaches the label card queue (a crash, a dropped message), so its watch window never opens and N is understated (§1-N5's adversary); (ii) maturity-lag bias — late-maturing outcomes (the 72 h tail) differ systematically from early ones, and the ECE computed on early labels is optimistic; (iii) the nightly shadow join itself fails silently and the dashboard keeps showing yesterday's numbers as today's.

**Detection:** (i) the loss-detector: every `action=suppress` row must have a corresponding label-queue entry within 5 minutes; the reconciliation job counts orphans hourly and pages the *platform* team (not the partner) on any nonzero count — the trust metric's denominator is guarded by an alarm; (ii) the maturity-lag distribution is tracked: for each closed window, time-to-label is recorded, and the join design's "stale_pending" line (§3 of the join design) is trended — a growing stale tail is a data-quality incident; (iii) every dashboard aggregate carries `computed_at` and the join-run id; a stale `computed_at` renders as a banner, never as fresh numbers ("evaluated 02:00 IST nightly shadow join" — the simulator's provenance block already does this; the redesign extends it to every panel).

**Principled response:** the pipeline watches itself with the same seriousness it watches the model. The loss-detector and the staleness banner are not monitoring nice-to-haves — they are *correctness* mechanisms for N5 and N2. A calibration number computed over a lossy join is wrong in the one place (the denominator) that the buyer cannot see. So the denominator gets its own alarm.

---

## 3. The feedback join under hostility

The join design (`eval/feedback-join-design.md`) specifies the protocol. This section specifies what happens when the protocol's inputs are adversarial — because Law 2 says the labeler is not a trusted oracle, the labeler is a data source, and data sources corrupt.

### 3.1 The threat model for labels

| Adversary | Mechanism | What it breaks |
|---|---|---|
| A1 — random label noise | 1–5% of labels flipped (mis-clicks, ambiguous cases) | ECE biased; the bias is bounded and estimable |
| A2 — systematic always-0 | labeler (or team culture) defaults to "no incident" | suppression looks perfect; frame-B curve over-optimistic exactly where it matters |
| A3 — systematic always-1 / alarmist | every ambiguous case labeled SEV | thresholds pushed conservative; the product looks useless |
| A4 — anchored labels | labeler sees the prediction first (blinding fails) | labels correlate with predictions ⇒ ECE underestimated (the evidence agrees with the model because it *copied* the model) |
| A5 — gaming | labels skewed to move thresholds (fewer pages for my team) | the tuner optimizes against fabricated costs |
| A6 — taxonomy drift | "SEV" means something different to this on-call | label semantics shift mid-window; the curve mixes two definitions |

A1 is statistics; A2–A6 are protocol failures. The design treats them differently: A1 gets error bars, A2–A6 get alarms and quarantine.

### 3.2 What each adversary does to the numbers (the math, briefly)

**A1 (random noise, rate ε):** observed positive rate `acc_obs = acc_true·(1−ε) + (1−acc_true)·ε`. At the suppression-region base rate (~5% SEV), ε=0.05 moves a true 0.05 to 0.0925 — nearly double. Random label noise *inflates* low base rates and *deflates* high ones; it biases ECE upward everywhere and, worse, it biases the false-suppress rate (N5) *downward* (a true SEV labeled 0 hides a false suppress). This is why N5's sub-line carries the suppress-sample rate: the 10% mandatory review exists to bound ε exactly where the bias is most dangerous.

**A2 (always-0 bias, fraction β of true positives labeled 0):** frame B's low-P bins — the suppression region — show `acc_obs = acc_true·(1−β)`. The reliability diagram looks *better* than reality precisely in the region where suppression decisions live. This is the most dangerous adversary in the table because its signature is "everything looks great." Detection cannot come from the labels themselves (they are the corrupted layer) — it must come from **outside** the labels: §3.3.

**A4 (anchoring):** if the label copies the prediction with probability γ, the measured ECE is `(1−γ)` times the true ECE plus noise — the calibration curve is flattered in direct proportion to the blinding failure. This is why blinding (§2.3 of the join design) is not a nice-to-have: it is the *identifying assumption* of the entire calibration enterprise. A blinding failure doesn't add noise; it invalidates the measurement.

**A6 (taxonomy drift):** mixes two label semantics in one window. The signature is a *bimodal* disagreement pattern in the double-label sample: pairs of labelers from different sub-teams disagree systematically while within-team pairs agree. The κ trend alone won't catch it (κ can stay high within a shifted team) — the double-label sample must be stratified across teams, and the protocol review (§2.4 of the join design) must ask "do we mean the same thing by SEV?" before it asks "is the model wrong?"

### 3.3 Triangulation: the higher authority

The labeling protocol's failure modes are all *self-report* failures — the labeler telling us what happened. The design's answer is an independent, process-generated oracle: **the partner's own incident system.**

The triangulation rule:

1. **Ticket-anchored truth:** any alert linked to a SEV1/SEV2 incident ticket in the partner's incident system (PagerDuty/Opsgenie) is `became_sev12=1`, regardless of what the label card says. The ticket was created by the partner's incident process — it is externally anchored (§2-S3). A label card that says 0 against an anchored ticket is not a disagreement; it is a *label-quality event* against the labeler.
2. **Unanchored labels are second-class:** a `became_sev12=1` label with no linked ticket is admitted but flagged `unanchored` and sampled at 100%. A `became_sev12=0` label on an alert that *paged and produced no ticket* is normal (most pages produce no incident) — the asymmetry is deliberate: positive claims need anchors; negative claims need the watch window.
3. **The triangulation coverage metric:** the fraction of positive labels that are ticket-anchored, reported on the calibration screen next to κ ("κ 0.81 · triangulation 94%"). If triangulation coverage falls, the labels are drifting away from the incident process — which means either the process changed (taxonomy drift, A6 — re-map) or the labelers did (A2/A5 — review).
4. **Auto-clear as a negative anchor:** an alert that auto-cleared with no human action and no customer-impact signal is a process-generated negative — it anchors `became_sev12=0` without needing a label card at all. This is the one label the machine generates itself, and it is the safest one in the building because no human's incentives touch it.

Why this is the load-bearing design choice: it breaks the circularity. Without triangulation, the calibration evidence is "our model, judged by labels our protocol collected" — a closed loop. With triangulation, it is "our model, judged against the partner's own incident process" — the loop is opened at exactly the point where the buyer's trust lives. The partner already believes their incident tickets; we borrow that belief.

### 3.4 Detecting miscalibration *caused by* bad labels

The subtle failure: the model is fine, the labels are wrong, and the ECE says the model is miscalibrated (or calibrated). How do we tell "the model drifted" from "the labels rotted"? Four detectors, run weekly:

1. **The κ-trend alarm:** κ on the stratified double-label sample, trended. A falling κ with a stable model version and stable alert mix ⇒ the labels are rotting, not the model. Action: quarantine the window's labels (exclude from ECE, count separately), review the protocol with the partner.
2. **The suppress-sample divergence check:** the 10% mandatory suppress-review sample is labeled under *heightened* scrutiny (the labeler knows it's audited). Compare the reviewed sample's positive rate against the routine labels' positive rate for suppressed alerts. A gap larger than the double-label disagreement baseline ⇒ routine suppress labels are systematically optimistic (A2's signature). Action: re-weight suppress labels, expand the sample to 25% until the gap closes.
3. **The ticket-reconciliation check:** join labels against incident tickets (§3.3). Count label=0 rows with anchored tickets (should be ~0; each one is a label-quality event) and label=1 rows with no ticket (the unanchored rate). A rising unanchored-positive rate with stable incident volume ⇒ A3/A5.
4. **The maturity-lag asymmetry check:** late-maturing labels (the 72 h tail) vs early labels. If the tail's positive rate differs from the early window's by more than the bootstrap CI, the ECE-on-early-labels is biased — and the bias direction tells you which adversary: tail-heavier-than-early ⇒ A2 (suppressed SEVs surface late, exactly the failure the product exists to measure).

**The decision rule:** when any detector fires, the calibration screen does not show a "worse ECE" — it shows the ECE *with the label-quality budget widened* and a banner naming the detector ("κ fell to 0.62 this week — labels under review; curve provisional"). The number degrades to honesty, never to a confident wrongness. (Law 7 product constitution, again.)

### 3.5 What we never do

- **Never "correct" labels with the model.** Using Jev's answers to fix labeler disagreements is the snake eating its tail — it manufactures agreement and calls it calibration. Disagreements resolve via the ticket system (§3.3) or a third human label, never via the model.
- **Never silently drop inconvenient labels.** Quarantined labels are counted and shown, not deleted. Deletion is indistinguishable from fraud in the one-year test (§0).
- **Never let one org's labels tune another's thresholds** without explicit consent (dataset-spec §4.3). Labels are per-org evidence; the shared artifact across orgs is the *protocol and the probe set*, not the judgments.

---

## 4. Provenance as a first-class design element

### 4.1 What is recorded (the provenance record)

For every decision, the audit store holds a **provenance record** — not just the decision, but everything needed to re-derive every number that decision ever contributed to:

| Element | Content | Why |
|---|---|---|
| `input_sha256` | hash of the canonical state JSON the Jev call saw | pins the exact model input; the state JSON is archived in cold storage, retrievable by hash |
| `response_digest` | hash of the raw provider response body | pins the exact model output; detects gateway rewriting (§2-S2) |
| `jev_model` | provider's model identifier, verbatim | model-change detection; flip grouping (§1-N7) |
| `questions_version` | version of the 3 Jev questions asked | a question rewording is a different measurement; calibration never mixes question versions |
| `thresholds_version` | the gate thresholds (per team) in force at decision time | coverage and suppression counts are policy-relative (§1-N3) |
| `correlator_version` + `fold_rule` | the dedup/storm-folding code version and rule id | suppression metrics are fold-relative (§1-N4) |
| `dataset_version` (shadow) | the shadow window's frozen version | every aggregate names its population (§1-N10) |
| `label_schema_version` | the label-card schema + `became_sev12` definition version | labels are uninterpretable without the definition they were made under (§2-S3) |
| outcome event chain | append-only: `(alert_id, labeled_at, labeler_id, became_sev12, ticket_link, reason)` | relabels are new rows; the full history of what was believed when |
| `cost_table_version` | for tuner outputs | savings projections name their money assumptions (§1-N6) |

**The invariant:** any number displayed anywhere in the product can be re-derived by a stranger with (a) the archived inputs addressed by these hashes, (b) the code at the recorded versions, and (c) the label events. If a number can't be re-derived, it isn't shown. This is the acceptance test for every new metric: *"write the re-derivation recipe before you ship the card."*

### 4.2 The one-year audit (how the §0 meeting goes)

The partner's SRE lead says "show me the 412." The audit explorer answers:

1. The shadow report's N5 line links to the exact `dataset_version` (frozen D3 window) and the exact query (the join SQL, versioned with the report).
2. The 412 rows resolve: each `alert_id` → its decision row → `input_sha256` → the archived state JSON (what Jev saw) → `response_digest` (what Jev said) → `thresholds_version` (why it suppressed) → the outcome event chain (who labeled it, when, under which label schema, linked to which incident ticket).
3. The label-quality battery for that window: κ, triangulation coverage, suppress-sample rate, the detector states (§3.4) — so the 412 carries its evidence-quality certificate, not just its count.
4. The code: `git` refs for the gate, tuner, and join job at the recorded versions. Re-running the join reproduces the 412 exactly (deterministic replay — the join is a pure function of the archived inputs).

**Retention contract (signed at onboarding):** decision rows + state JSON + response bodies: **minimum 1 year, append-only, content-addressed** (S3/GCS object store, hash as key — deduplication is free, tampering is detectable). Label events: 1 year minimum. Aggregates (ECE reports, shadow reports): 2 years (they're small). The partner's data-deletion requests are honored *above* this floor only by mutual agreement, and any deletion is itself a logged event naming what was deleted and when — because a gap in the ledger is also evidence, and it must be a *declared* gap.

### 4.3 What provenance costs, and why we pay it

Storing every state JSON and response body is the most expensive line in the infra budget after the Jev calls themselves. The Law 1 question: *what if this storage didn't exist at all?* Then the one-year test (§0) is unanswerable, the calibration claim is unsecured, and the product is selling the one thing (defensibility) it cannot deliver. The storage is not overhead — **it is the collateral for every promise the product makes.** The Law 7 infra constitution's blast-radius thinking applies in reverse: the blast radius of *losing* provenance is the entire trust story. So provenance gets the strongest durability guarantee in the system, stronger than the dashboard, stronger than the tuner, second only to the paging path itself.

---

## 5. CREATIVE APPLICATION: the 3 data-pedigree choices we make differently

These are not industry best practices. They are bets — each one costs us something, each one buys something the incumbents don't have.

### Choice 1 — Labels are evidence events with a quality budget, never ground truth

**What the industry does:** labels go into the training/eval set as ground truth; label noise is a footnote (or a "data cleaning" step nobody documents). The calibration curve is presented as a property of the model.

**What we do:** every label carries `{labeler_id, labeled_at, label_schema_version, ticket_link, quality_weight}`, and every aggregate number carries the label-quality budget that produced it: κ, triangulation coverage, suppress-sample rate, detector states. The ECE on the calibration screen is displayed as `0.031 ± 0.012 (measurement) ± 0.008 (label-noise bound)` — the second error bar is the A1-bias bound from §3.2, computed from the double-label disagreement rate. When the quality budget is spent (κ < 0.7, triangulation falling), the number doesn't get worse — it gets *provisional*, with the reason named.

**What it costs us:** our numbers look less certain than a competitor's single clean ECE. In a bake-off, the vendor with the prettier curve wins the first meeting.

**What it buys us:** the second meeting — the one with the skeptic, the postmortem, the lawyer. When their curve is interrogated and ours is *already interrogated* (the interrogation is printed on the screen), we are the only vendor whose numbers survive contact with the buyer's own doubt. Defensibility is the product; this is the product's spec.

### Choice 2 — The incident system is the higher authority, not the label card

**What the industry does:** human labeling is the gold standard; the label UI is the source of truth; disagreements are resolved by more humans (a third annotator, an adjudicator).

**What we do:** the partner's own incident tickets outrank our label cards (§3.3). A ticket-anchored positive is truth even against a labeler's 0; an unanchored positive is admitted but flagged and fully sampled. The label card is the *fallback* for the unattributed remainder, not the primary instrument. And the auto-clear signal — process-generated, incentive-free — anchors negatives without any human in the loop.

**What it costs us:** we inherit the partner's incident-process messiness (taxonomy drift, late tickets, stub tickets — §2-S3). Our "ground truth" is only as clean as their incident hygiene, and we have to build the taxonomy-mapping and reconciliation machinery to live with that.

**What it buys us:** the calibration evidence is no longer a closed loop ("our model, judged by labels our protocol collected"). It is open at exactly the point the buyer already trusts: their own incident process. And it gives us the one detector (ticket reconciliation, §3.4-3) that catches the most dangerous adversary in the table (A2, systematic always-0) — the one whose signature is "everything looks great." Nobody else can catch A2 from inside their own labels. We catch it from outside them.

### Choice 3 — Every dashboard number is a reproducible, versioned query — production data gets dataset versions too

**What the industry does:** dataset versioning (if it exists) covers training/eval fixtures. Production dashboards are live queries over the current warehouse state — the number on screen today cannot be reproduced tomorrow because the underlying data moved.

**What we do:** the `dataset_version` discipline (dataset-spec §4) extends to *live* aggregates. Every panel's API response carries `{dataset_version, threshold_version, jev_model, computed_at, query_version}`; the shadow windows are frozen artifacts; the UI refuses to blend cards from mismatched versions (§1-N3); the simulator's "same math" provenance block (design simulator §4) is the pattern for every panel, not just the simulator. Re-running any number with its recorded versions reproduces it exactly — the join is a pure function of archived inputs (§4.2-4).

**What it costs us:** engineering discipline at every layer — frozen windows instead of rolling queries, version-stamped APIs, the UI complexity of version-mismatch states, storage for the frozen artifacts. It is slower to build than a live query.

**What it buys us:** the one-year test (§0) becomes a *routine operation* instead of a forensic expedition. "Show me the 412" is a link, not a project. And strategically: the frozen per-org D3 windows are the fine-tuned-encoder training corpus (the report's escape hatch from the Jev layer) — versioned, consented, reproducible. The thing that makes our numbers auditable today is the same thing that makes our model exit possible tomorrow. Pedigree is the moat's paperwork.

---

## 6. Open questions (for the coordinator's cross-domain challenge)

1. **The label-noise error bar (§5-1):** the ±0.008 bound assumes the double-label disagreement rate estimates the true error rate — but double-labeled cards get *more* attention than routine cards (Hawthorne effect on the audit sample). Is our noise bound optimistic? Should the suppress-review sample (which is also heightened-scrutiny) be corrected, and by what factor?
2. **Triangulation coverage as a KPI:** if a partner's incident hygiene is poor (50% of real SEVs never get tickets), triangulation coverage is low through no fault of our protocol — and Choice 2's authority weakens exactly where we need it most. What is the minimum viable incident hygiene for a design partner, and do we *require* it at onboarding?
3. **The 72 h watch window vs. the tail:** §3.4-4 notes suppressed SEVs surface late. Is 72 h enough? The cost of a longer window is staler calibration; the cost of a shorter one is missing the exact failure the product exists to measure. This needs incident-data, not opinion — ORACLE should measure time-to-surface distributions from the partner backfill before we freeze the window.
4. **Provenance storage vs. partner deletion rights:** §4.2's 1-year floor conflicts with aggressive data-deletion clauses some enterprises require. Which yields — and what does the calibration story look like for a partner who deletes raw rows after 30 days? (Candidate answer: aggregates and hashes survive; re-derivation degrades to "reproducible from hashes + code, raw inputs deleted per <clause>" — declared, not silent.)

---

*Ledger's sign-off: this document is the design. The numbers it governs are named in §1, their adversaries in §2–§3, their afterlife in §4, and our three bets in §5. A principal who reads it should be able to run the one-year test (§0) in their head and find no missing link. If they find one, that's §6's job — and the design isn't done until §6 is empty.*
