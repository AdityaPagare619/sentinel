# 2026-10-02 — Jev's real behavior: latency, limits, and the fallback map

**Question:** What does Jev actually do on our wire format — measured, not
marketed? Plus: the wire-compatible fallback-engine survey (Jev first, never only).

## Observed facts

**Vendor spec (corroborated across independent sources):**
- 70–500ms end-to-end latency, vendor-reported, measured from the US West Coast.
  Questions in one request are evaluated in parallel; adding questions adds
  little latency. (github.com/nedcut/gm-bench docs/typesafe_jev_lane.md;
  github.com/valentynkit/awesome-jev-typesafe; accessed 2026-10-02)
- Wire format confirmed independently: `POST https://api.typesafe.ai/v1/systemone`,
  `Authorization: Bearer`, body `{model, state, questions}`; response
  `{model, answers, usage:{input_tokens, output_tokens}}`.
  (gm-bench lane doc; pinggy-io blog; juan294/sutura research note; accessed 2026-10-02)
- Official SDKs: `typesafe-sdk` (Python), `@typesafe-ai/sdk` (JS) — defaults:
  10s timeout, 2 retries with 500ms initial backoff capped at 5s on 408/429/5xx,
  honors `Retry-After`, refuses browser without `dangerouslyAllowBrowser`.
  (juan294/sutura docs/research/2026-09-17-typesafe-jev-fit.md; accessed 2026-10-02)
- Served ALSO via Vercel AI Gateway, Cloudflare Workers AI, OpenRouter (beta),
  Netlify AI Gateway. (awesome-jev-typesafe; accessed 2026-10-02)

**Rate-limit discrepancy — flagged for verification:**
- Our research report (§3.2, live docs read 2026-10-02): **100k tokens/sec and
  40 req/sec**, described as *dynamic*, with dedicated 529 code.
- Community docs (awesome-jev-typesafe, crawled 3 days ago): **250,000
  tokens/sec and 1,200 requests per minute**.
- These disagree by 6.25x on request rate. Possible causes: tiered limits,
  docs changed between reads, or different limit dimensions. UNRESOLVED —
  design to the tighter bound until Oracle verifies against live docs.

**Our measurement:**
- 2026-10-02 ~13:28 IST: one real-key call from our India box — HTTP 200,
  correct shape, **11,429ms round trip** vs 70–500ms spec. Likely contributors:
  India→US round trip + first-call cold start. N=1 — not a finding, a trigger
  for the campaign.

**Fallback-engine map:**
- **Gateway fallbacks (same API, different pipe):** Vercel AI Gateway,
  Cloudflare Workers AI, OpenRouter, Netlify AI Gateway all serve Jev. If
  api.typesafe.ai 529s, the same request can route through a gateway.
  (awesome-jev-typesafe; accessed 2026-10-02)
- **Open-weights alternative:** Laya (Apache 2.0, ModernBERT-large 421M) —
  ~33–40ms/query self-hosted, multilingual router. BUT: base checkpoints ≈
  chance on typed-decisions zero-shot; needs domain fine-tuning; weaker beyond
  ~20–50 choice options without budget tuning. Author's house rule: Jev for
  managed API + high-cardinality Choice; Laya for open weights / offline VPC /
  $0 inference. (github.com/pongpong/token-savings-notes TYPESAFE-JEV.md;
  accessed 2026-10-02) — this is the concrete precedent for our
  fine-tuned-encoder exit.

## Inferences

1. The sub-second inline thesis is NOT yet earned. Vendor numbers are
   US-West-Coast; our infra is India. The latency campaign (N≥100, sizes,
   times of day, regions) is the highest-leverage measurement on the board —
   it gates Law 1's timeout policy and the whole "inline" claim.
2. The fallback story is stronger than last night knew: gateways give us
   same-day 529 resilience with zero model change; Laya gives us the
   open-weights exit with a real (if fine-tune-required) precedent.
3. Until the rate-limit discrepancy is resolved, backpressure design uses
   40 req/s as the ceiling.

## Honest limitations

- Community benchmarks are third-party, small-N, and US/EU-centric. None
  measured from India. Our N=1 is a smoke test, not data.
- Laya benchmark numbers are author-reported; treat as directional.

## Implications

- Proposed ADR-002 (latency measurement campaign — Oracle, verdict by Sun AM),
  ADR-003 (fallback-engine matrix: gateways now, Laya as encoder-exit
  precedent), ADR-004 (rate-limit verification vs live docs).
