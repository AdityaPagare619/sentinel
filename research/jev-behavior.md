# Research agenda — Jev behavior

**Owner:** Oracle (Chief Scientist, Calibration) · **Dir:** `research/jev-behavior/`
**Mission:** Jev's *real* behavior under measurement, not the vendor's spec sheet —
and the fallback-engine survey. Aditya's thesis: Jev alone might not be enough;
the moat is the architecture. So: Jev is the FIRST engine, never the ONLY engine.
The wire format has commoditized (Ollama, OpenAI Decisions API, Ollaya per the
research report) — that is our escape hatch and our negotiating position, and it
must be mapped precisely.

## Tonight's question (2026-10-02)

**Q1:** What does Jev actually do on our wire format — measured, not marketed?
Design the latency measurement campaign for the 11.4s first-call finding
(cold start vs steady state, time-of-day variance, payload-size scaling), and
survey the wire-compatible fallback engines (what exists today that speaks
`/v1/systemone`-shaped calls, what each costs, where each is weaker/stronger).

## Standing backlog (ranked by leverage)

1. **Latency campaign:** N≥100 calls across sizes/times; report p50/p95/p99 with
   error bars; separate first-call cold start from steady state. The sub-second
   inline thesis lives or dies here — no assumptions survive the night.
2. **Flip-rate characterization:** repeat-call flips on alert-shaped states
   (not toy states); does flip rate vary with confidence level, option count,
   state size? (Feeds the triple-lock's confidence bar and the dashboard's
   honest flip panel.)
3. **Calibration on real vs synthetic:** how far do our synthetic-state
   probabilities deviate from real-key behavior? (Bounds what Sunday's demo
   numbers can honestly claim.)
4. **Fallback-engine matrix:** for each wire-compatible engine — auth, pricing,
   latency, choice/noul/score support, probability quality, rate limits. Ranked
   by "could carry the paging path if TypeSafe 529s for an hour."
5. **The 529 regime:** TypeSafe's dynamic limits under load — documented behavior,
   practitioner reports, our own measured backoff behavior. (Feeds the
   backpressure design and the priority-queue spec.)
6. **Version discipline:** `jev-latest` vs pinned version IDs — what changes
   between versions, how to detect drift, the 7-day re-validation protocol.

## Sources to mine

- TypeSafe live docs (typesafe.ai/docs) — re-read on a schedule; the vendor is
  3 weeks old and the docs move.
- Our own measurements (the only source that counts for latency/flips).
- OpenAI docs (structured outputs / decisions), Ollama docs, community
  benchmarks of decision-model APIs.

## Output contract

Dated notes in `research/jev-behavior/` per the charter. Every number gets N,
error bars, and the exact call parameters. No vendor claim is repeated without
a measurement or a "UNVERIFIED — vendor claim" tag.
