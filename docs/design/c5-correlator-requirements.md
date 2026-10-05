# C5 Trace-ID Envelope — Correlator-Side Requirements + Per-Layer Propagation Notes

**Author:** corr-2 (CORRELATOR domain, Sentinel 12h wave) · **Checkpoint:** T+3 notes
**Dependency:** evlog-1's C5 envelope draft (`lane/prep-contracts-observe:contracts/observe/`)
— **not yet pushed as of this write** (verified: only `origin/lane/prep-contracts-ingest`
exists). This doc is written against PIPELINE-REVISION §2.2 (C5 text) and the actual
correlator/receiver/gate code at `3da2622`. Merge point: when evlog-1's draft lands,
these requirements fold into the C5 README as a "correlator-requirements" section;
this file remains the design rationale.

## Binding skill clause

**principal-governance §4, Unified telemetry:** *"one Trace-ID per user interaction,
injected from the browser through gateway, microservices, and database. A spinner on
screen must be diagnosable to the exact slow query in one lookup."* This doc is the
correlator's (L3) side of that binding: what it needs from the envelope, what it must
attach, and how its own latency is attributed. External grounding: W3C Trace Context —
`traceparent: {version}-{trace-id 32 hex}-{parent-span 16 hex}-{flags}`; the trace-id
identifies the *whole interaction*, span ids identify the *calling stage*. For
in-process propagation the propagator model is "a carrier is anything that holds
strings" — here the carrier is the `Alert` / `TriageResult` objects, not HTTP headers.
Sources: W3C Trace Context (https://www.w3.org/TR/trace-context/) via
https://openobserve.ai/blog/opentelemetry-context-propagation/ and
https://github.com/0xdarkmatter/axiom/blob/HEAD/.claude/skills/opentelemetry-expert/SKILL.md
(accessed 2026-10-05).

## 1. Where the correlator stands in C5 (code-grounded)

- **Layers:** correlator = **L3 DECIDE-1** (`src/sentinel/correlator.py`, 1188 lines).
  C5 crosses **L4 DECIDE → L6 OBSERVE** — the correlator is an *upstream contributor*,
  not the envelope owner. Owner: evlog-1.
- **Single entry point:** `Correlator.ingest(alert, label_snapshot=None)`
  (`correlator.py:479`). Called from exactly three sites:
  `receiver.py:436` (`Pipeline._triage`, the live path),
  `shadow.py:800` (shadow pipeline),
  `health.py:263` (health probe with a synthetic alert).
- **Today there is no trace identity anywhere:** `Alert` (`models.py`, frozen contract
  §3.2 of ARCHITECTURE.md) carries `alert_id, received_at, fingerprint, service, check,
  severity_in, title, source, labels, metric_value, metric_threshold, breach_duration_s, raw`
  — **no trace field**. `DecisionRecord` (gate-built, `gate.py:569`, `gate.py:627`) carries
  no trace_id and no stage latencies. `audit.record()` writes the event body with
  `latency_ms` (the gate's own `perf_counter` delta, `gate.py:546`) but no attribution
  across stages. L1 does **not** mint anything today (`handle_pd`/`handle_generic` go
  straight to `_triage`).
- **Stage boundaries inside the correlator** (`_classify_locked`, `correlator.py:598`):
  (1) change-window → (2) duplicate (inherits `prior` disposition, **no Jev call**) →
  (3) active-storm fold → (4) episode outcome (new/reopened/fresh) → (5) storm declaration
  (one aggregate, `storm_declared=True`, routed to the separate `gate.digest_storm`
  path, `receiver.py:442–457`) → (6) record observation. Post-gate, the pipeline calls
  `Correlator.note_disposition(fp, disp)` (`receiver.py:457,469`) so later duplicates
  inherit the prior disposition.

## 2. What L3 requires from the envelope (correlator-side)

**R1 — trace_id minted at L1, never at L3.** The trace_id (128-bit, 32 lowercase hex,
W3C-compatible) is born in L1 INGEST at the ingress handler and is **immutable for the
interaction's lifetime**. The correlator must *receive* it, never mint it. Why: minting at
L3 starts the trace mid-pipeline (L1/L2 latencies unattributable), and the `Correlator`
instance is shared across live, shadow, and health call sites — an L3 mint would mix
three pipelines' traces under one identity. Falsifier: any `uuid`/`secrets` call inside
`correlator.py` that generates a trace identity.

**R2 — trace_id travels on the object, not in `Alert.raw`.** Today the whole `Alert`
(including `raw`) flows L1→L3 because the receiver does everything in-process. Under the
revised layering, **C2 says `Alert.raw` never crosses L2→L3** (anti-corruption).
Requirement: trace_id is a **first-class field** (on `Alert`, and/or on an explicit
`TraceEnvelope` parameter to `ingest`) — placing it in `raw` is a silent-trace-loss bug
the moment the revised L2/L3 boundary is enforced. Same for span/parent links.

**R3 — `TriageResult` (C3) carries trace_id too.** `gate.evaluate(alert, state, history,
context, correlation=corr)` (`receiver.py:466`) receives *both* the Alert and the
correlation result. Requirement: the C3 crossing (`TriageResult{kind, fingerprint,
episode_ref, prior_disposition?}`) **additionally carries trace_id**; the gate
cross-checks `alert.trace_id == correlation.trace_id` at its boundary and fails LOUD on
mismatch (a forked trace is a contract violation, not a warning).

**R4 — envelope fields are inside the hash, and units are fixed.** C5's
`stage_latencies{normalize, correlate, gate_race, decide, forward}` must be **ms as
float, `perf_counter` deltas measured at stage boundaries by the stage itself**
(R4a below). Requirement: trace_id + stage_latencies live **inside the hashed event
body** (tamper-evidence: fields outside the chain are mutable without detection), and
`correlate` is wall-clock for the full `ingest()` call including lock wait (lock
contention is the correlator's real latency source — measuring only `_classify_locked`
would hide it).

**R5 — duplicates link, never fork silently.** A duplicate ingest (`kind="duplicate"`,
`correlator.py` steps 2) is a *new ingress* (new interaction) but the *same decision*.
Requirement: the duplicate's envelope gets its **own trace_id** (one trace per
interaction — principal-governance) **plus** `dedup_of_trace_id` pointing at the
original alert's trace. To supply this, the correlator must store trace_id alongside
`prior` in the `_seen[fp]` entry (`note_disposition` gains the trace to record).
Without the link, one episode = 1000 DecisionRecords with no join path.

**R6 — storm-aggregate trace semantics.** On `storm_declared=True`, `_triage` synthesizes
a new `Alert` (`_aggregate_alert`, `receiver.py:557`) and routes it to the *separate*
`gate.digest_storm` path (a second DecisionRecord). Requirement: the aggregate mints a
**fresh trace_id** (it is a new synthetic interaction, `alert_id=f"storm-{int(...)}"`)
with `trace_links: [triggering trace_id, …]` (cap the list, record the count) — W3C
"links" semantics. Folded alerts (`kind="storm"`, `storm_declared=False`) each keep
their own trace_id; they are absorbed, not suppressed, and their DecisionRecords must
still carry C5 fields.

**R7 — shadow and health pipelines are separate trace namespaces.** `shadow.py:800`
and `health.py:263` call the same `ingest()`. Requirement: shadow replays and health
probes **mint their own trace_ids and never reuse a live trace_id**; the envelope
carries `pipeline_mode: live | shadow | health` so a "diagnose in one lookup" query
cannot join a shadow DecisionRecord into a live incident. (Shadow reuse of a live
trace_id would be the C5 analogue of the simulated-spill audit lie.) This is an open
question for evlog-1 (see §5) only in *where* the mode field lives — the uniqueness
rule itself is an L3 requirement.

**R8 — L3 is read-only on the envelope.** The correlator attaches its own
`correlate_ms` and nothing else. It never rewrites trace_id, never backfills other
stages' latencies, never injects into the event log. (principal-governance SSOT: the
event log owns decisions; the correlator owns its measurement.)

## 3. How the correlator's own latency is attributed (R4a)

- **Clock owner:** L3 measures its own ingest. `correlate_ms =
  (time.perf_counter() - t0) * 1000.0` wrapped around the **entire** `ingest()` body —
  including `_prune`, the D7 single-clock `read_history`, label-freshness check, and
  lock acquisition. Precedent: `gate.evaluate` already measures its own `t0` the same
  way (`gate.py:546`). The stage owns its measurement (state isolation); the composer
  (`Pipeline._triage`) does not thread clocks through.
- **Carrier:** returned on `CorrelationResult` as a new field `correlate_ms` (additive;
  `CorrelationResult` is correlator-owned, so this needs no other consumer's sign-off).
  The composer copies it into the envelope's `stage_latencies["correlate"]`.
- **No per-sub-stage spans.** The six `_classify_locked` sub-stages are *not*
  individually instrumented — per-sub-stage timing inside one lock-holding call adds
  overhead for no decision value. Instead, **latency-by-kind is derivable in
  aggregate**: the event log joins `correlate_ms` with `CorrelationResult.kind`
  (new / duplicate / storm / change_window / reopened / label_stale). If a kind shows a
  fat tail, *then* instrument that sub-stage (Type 2, behind a flag). Subtract before
  adding — principal-mindset §4.
- **Aggregate attribution:** the aggregate DecisionRecord's `correlate` latency is the
  *declaring* alert's ingest latency, not the sum of folded alerts. Folded alerts keep
  their own tiny per-ingest `correlate_ms` on their own DecisionRecords (§2 R6).
- **Log lines:** correlator `logger` calls gain a structured `trace_id=` field so the
  existing text logs join the trace without a second lookup (C5's "on every log line").

## 4. Per-layer propagation notes — the correlator's view

| Layer | What must happen (correlator's perspective) |
|---|---|
| **L1 INGEST** (`receiver.py` handlers) | Mint the 32-hex trace_id at the ingress handler (`handle_pd`,
...[truncated 6365 chars]