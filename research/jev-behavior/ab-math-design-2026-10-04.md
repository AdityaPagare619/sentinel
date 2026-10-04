# A/B Harness for Jev Question Variants — Math & Design (2026-10-04)

Lane L6. Status: design pre-registration. No code written yet.

## 1. Problem restatement

The CI repeatability bars (option-order shuffle <3%, repeat-call flips <2%)
are measured against `FlipMock`/`OrderShuffleClient` — and `evalharness.py`
admits in its own HONEST LIMITATIONS that the shuffle probe "is vacuous
against the scripted mock (the mock keys answers off state, not option
order)". So the bars may be tuning theater: they cannot detect a live
order-sensitivity. The A/B harness measures the REAL thing: the same
alerts, the same production Gate path, variant questions, live Jev.

This changes what we BELIEVE (are the CI bars real?), not a number.

## 2. Proxy-trap audit

| Proxy | True objective | Gap |
|---|---|---|
| variant-vs-control disposition flip rate | disposition CORRECTNESS on real alerts | flip direction unknown without labels — a flip can be toward correct or away. Report magnitude vs noise floor; never claim better/worse |
| latency shift per variant | race budget B=2700ms headroom | longer questions cost tokens AND time; measure both |
| confidence shift | calibration / coverage@tau gating | a variant that inflates confidence without accuracy change is dangerous; report coverage@tau per arm |
| $/decision at observed tokens | unit economics | vendor $0.042/M is a price-list fact; our TOKEN count is the measured variable |

## 3. Irreducible noise (measured, not assumed)

- Researched Jev non-determinism: 1.3–2.2% answer flips on identical repeats.
- Therefore: every variant effect is judged against the WITHIN-variant
  repeat disagreement (arm A1 vs arm A2 on the same alerts), never against zero.
- Pre-registered interpretation rule: if the variant-flip 95% CI overlaps the
  noise-floor 95% CI substantially → "no evidence the variant exceeds
  irreducible noise". The deliverable is estimates + CIs, not significance.

## 4. Design: paired, fixed-set, randomized-order

- **Fixed eval set**: n alerts from `generate_alerts(n, seed)`, states built
  with the production `build_state(alert, {}, {})` (same call as
  receiver.py:208 — realistic token sizes). Set frozen by (n, seed, sha).
- **Paired**: every alert runs under every arm (A1, A2, B). Each alert is its
  own control — halves variance vs unpaired, kills alert-mix confounding.
- **Order randomization**: per alert, arm execution order shuffled with a
  seeded RNG → time drift (vendor slowdown mid-run) cannot masquerade as a
  variant effect.
- **Variant injection without touching production**: `VariantClient` wraps
  any client; `decide(state, questions)` applies the versioned transform to
  the questions dict, times the inner call, records (variant_id, sha,
  latency_ms, error). Gate, questions.py, race.py untouched. The full
  production decision path (race → policy → prob lock) runs unmodified.
- **Variant = versioned artifact**: JSON spec
  `{id, version, description, transforms[], created}`; sha256 of canonical
  JSON stamped on every result row. Transforms: `shuffle_options` (seeded),
  `reword_instructions`, `reword_option`. Control = identity transform.

## 5. Statistics (frozen before the run)

- Flip rates: Wilson score 95% CI. n, conditions always reported.
- Noise vs variant: McNemar on paired binary outcomes per alert —
  noise_flip=(A1≠A2), variant_flip=(A1≠B); discordant cells b=(0,1), c=(1,0);
  χ²=(|b−c|−1)²/(b+c), p from χ²(1). If b+c<10: "underpowered — descriptive
  only", no p-value claim.
- Power honesty: n=200, noise≈2% → detecting variant=5% is underpowered
  (~30%). We report the estimate and CI; we do NOT claim "no effect" from
  a null — we claim "no evidence of effect above noise at this n".
- Calibration-relevant (no labels needed): per-arm confidence mean,
  coverage@tau for tau∈{0.7,0.8,0.9}, mean |Δconfidence| A1→B.
- Latency: n, min, p50, mean, p95, p99, max per arm + window/rate/model-echo.

## 6. Cost accounting (our arithmetic)

- $/decision = mean_input_tokens × $0.042 / 10⁶ (price-list fact × OUR measured tokens).
- $/1M decisions = mean_input_tokens × $0.042.
- Ratio vs named reference: GPT-6 Sol classify+JSON $2,878/1M decisions
  (independent AI/ML API study, via our JEV research report §3.5 correction).
  Caveat printed next to the ratio: different task, different token profile —
  the ratio is illustrative, not a claim about our workload.
- Also reported: where our $/1M lands vs the report's own $23–84/1M band,
  and the DeepSeek-V4-Flash $77/1M vs Jev $98/1M routing data point (report:
  Jev can be MORE expensive than cheap LLMs — our long option descriptions
  bill every call).

## 7. Kill conditions (monkey-first)

- K1: live error rate >5% over any rolling 50-call window → stop live, deliver harness + dry-run.
- K2: two consecutive non-retryable hard failures → stop.
- K3: 429/529 storm → hard stop per policy; report partial n honestly.
- K4 (pre-live gate): B-arm wire payload identical to A-arm on ANY alert of
  the dry-run → transform is a silent no-op; fix before spending live calls.
- K5: vendor median latency 3× the N=100 p50 (>2.4s) → the path is degraded;
  stop, report, do not burn budget proving the vendor is down.

## 8. Alternatives considered and rejected

- **Unpaired A/B (separate alert sets per arm)**: rejected — doubles variance,
  needs 2× calls for the same power, confounds alert mix.
- **Bypassing the Gate, calling evaluate_policy directly**: rejected —
  loses the ADR-013 prob lock and race behavior; the VariantClient wrapper
  keeps the exact production path for ~15 lines.
- **New parallel eval system**: rejected — task says extend evalharness;
  ab.py imports its primitives (generate_alerts, script_response, FlipMock,
  COVERAGE_TAUS); evalharness main gains an `--ab` passthrough.
- **Wording variant as experiment 1**: deferred to experiment 2 IF budget
  allows. Order-shuffle is experiment 1 because it directly adjudicates the
  existing CI bar's honesty — highest information value.

## 9. Experiment 1 (pre-registered)

- Arms: A1=control@v1 (frozen §3.3), A2=control@v1 repeat (noise floor),
  B=q123-shuffle@v1 (seeded permutation of all three questions' criteria).
- n=200 alerts, seed=7 (same seed family as evalharness default).
- Metrics: disposition flip A1→B vs A1→A2 (Wilson CIs + McNemar),
  per-question choice flips, latency distributions, tokens, $/decision,
  coverage@tau per arm, error accounting.
- Rate ≤0.35 req/s with jitter (campaign-proven polite rate); per-call
  timeout 30s; one retry on 529/520 only, recorded.

## 10. Done-checklist answers

- Vendor down 4h → harness still delivers: dry-run mode is the contracted fallback.
- Junior runs it → one CLI, `--dry-run` default; live requires explicit `--live`.
- No secrets in repo: surrogate helpers only; secrets-grep clean enforced.
- Results reproducible: fixed eval set (n, seed, sha), variant shas, run manifest
  JSON with every row.
