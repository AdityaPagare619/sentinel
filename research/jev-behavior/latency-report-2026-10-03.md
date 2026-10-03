# Oracle Latency Campaign — Report 2026-10-03

**Mission:** measure the real-key Jev (`api.typesafe.ai`) latency distribution to
re-derive the race-to-page budget **B**. Measurement only; no product code touched.

## Method

- Script: `research/jev-behavior/bin/latency_campaign.py` (committed on
  `lane/oracle-latency`; adapted once to record the echoed model version).
- Credential: `custom.typesafe` connector via surrogate helpers. **No raw key
  material in any file, log, or this report** (secrets-grep clean).
- Phases:
  - **Cold ×10** — ≥60 s idle before each call, fresh connection each.
  - **Warm-fresh ×60** — ~3 s spacing, fresh connection each (urllib).
  - **Warm-reused ×40** — ~3 s spacing, one keep-alive `HTTPSConnection`.
  - **Supplement ×40** — warm-fresh, same method, different seed (see §4).
- Rate: ≤0.35 req/s with jitter — far under the 40 req/s live-docs bound.
- Payloads: small/medium/large alert-state bodies (396–~8k tokens in, ~45 out),
  same vocabulary as the Sentinel synthetic generator.
- Window: 2026-10-03 07:46–08:07 UTC (13:16–13:37 IST).

## Results — latency distribution (HTTP 200 only, n=109 of 150 calls)

| phase | n | min | p50 | mean | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| cold (≥60 s idle, fresh conn) | 10 | 615 ms | 843 ms | 938 ms | 1458 ms | 1524 ms | 1540 ms |
| warm (fresh conn) | 99 | 494 ms | 815 ms | 841 ms | 1286 ms | 1339 ms | 1678 ms |
| **all successful** | **109** | **494 ms** | **816 ms** | **850 ms** | **1312 ms** | **1526 ms** | **1678 ms** |

- Echoed model on every success: `jev-1.13.0` (matches the requested model).
- Dispositions returned normally (suppress 71 / page 38) — calls were
  semantically healthy, not just fast.
- **The single prior 11.429 s data point did NOT reproduce.** Slowest observed
  in this campaign: 1678 ms. The 11.4 s was an outlier (likely first-ever-call
  origin spin-up or a transient), not the steady-state behavior.
- Cold vs warm: no meaningful cold penalty (cold p50 843 ms vs warm p50
  815 ms). The "first call after idle is slow" effect did not materialize.

## Error accounting

| outcome | count | notes |
|---|---|---|
| HTTP 200 | 109 | |
| HTTP 520 | 1 | one transient Cloudflare "origin unknown error" in warm-fresh (~0.7%) |
| `KeepAlive:SSLError` | 40 | **all 40 warm-reused calls** — diagnosed below, environment artifact |
| HTTP 429 / 529 | **0** | no rate limiting hit at our polite rate |

### Warm-reused failure diagnosis (not an API finding)

All 40 keep-alive attempts failed in <25 ms with
`ssl.SSLError: [SSL: WRONG_VERSION_NUMBER]`. Root cause: this sandbox egresses
through an HTTP proxy (`hatch-egress-proxy:3128`); `api.typesafe.ai` resolves
to the intercept address `198.18.26.111`. `urllib` honors `HTTPS_PROXY`
(CONNECT tunnel → real TLS works); raw `http.client.HTTPSConnection` and raw
sockets bypass the proxy and hit the intercept, which speaks plain HTTP on
443. The campaign's own TCP/TLS decomposition probe failed for the same reason
(all 3 samples). **Connection-reuse latency of the real API is therefore
unmeasured** — the urllib fresh-connection numbers above are the valid
end-to-end measurements.

## Region / path notes

- Response headers: `server: cloudflare`; `cf-ray: …-ATL` — the request was
  served via the **Atlanta (ATL)** Cloudflare edge.
- Caveat: the sandbox path is India → egress proxy → Cloudflare ATL → origin.
  Proxy overhead is included in every number above; a direct India→API route
  could differ. These are the latencies *our* infrastructure would actually see.

## Verdict on the 70–500 ms vendor claim

**Not supported on the measured path.** Minimum observed was 494 ms (right at
the vendor's ceiling); p50 was 816 ms (1.6× the vendor max); p95 1312 ms;
p99 1526 ms. Honest caveats: (a) proxy overhead is baked into our numbers;
(b) traffic routed via ATL edge, not a regional edge; (c) one historical
11.4 s outlier never reproduced. The claim may hold for a well-placed client
on a warm path — it does not hold for what Sentinel would see from this
environment. Treat 70–500 ms as best-case marketing, not a planning number.

## B re-derivation

Design rule: `B = max(1000 ms, 2 × measured healthy p99)`.

- Healthy p99 (warm steady-state, n=99): **1339 ms** → 2× = 2678 ms.
- (All-successful p99 = 1526 ms → 2× = 3052 ms, for reference.)
- **B = max(1000, 2678) = 2678 ms → recommend B = 2700 ms.**

The seeded B=1000 ms in the race-to-page fix design is **too tight** against
measured reality: the median healthy call alone (816 ms) nearly exhausts it,
and p99 (1339 ms) exceeds it. With B=2700 ms, ~99% of healthy calls complete
inside the budget on the measured path.

## Files

- `research/jev-behavior/latency-n100.json.jsonl` — 150 rows, one JSON object
  per call (raw data; 40 keep-alive error rows retained and labeled).
- `research/jev-behavior/bin/latency_campaign.py` — the campaign script.

## Limitations

- n=10 cold is thin for cold-tail claims; the no-cold-penalty finding is
  directional, not definitive.
- Keep-alive (connection reuse) unmeasured — sandbox proxy artifact.
- Single 20-minute window; diurnal/origin-load variance not captured.
- One transient HTTP 520 observed — origin-side flakiness exists (~1%).
