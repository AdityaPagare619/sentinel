# Domain Review: Correlator (dedup / episodes / storm)

**Reviewer:** Phase-2 domain reviewer (fresh eyes — did not build this)
**Date:** 2026-10-05
**Scope:** `src/sentinel/correlator.py` + its tests + design docs, on branch `program/architecture-revision`
**Program phase:** Phase 2 — per-domain revision memos feeding the consolidated ADR-level architecture

---

## 1. WHAT EXISTS

The correlator is the deterministic pre-Jev triage stage: webhook receiver → **correlator** → gate (races Jev) → forwarder. Everything in `src/sentinel/correlator.py` (1,188 lines) is deterministic and runs before any Jev call, per the design principle stated in the module docstring: *"never pay Jev for deterministic work."* The whole `Correlator` class is in-memory, stdlib-only, guarded by a single global `threading.Lock`.

### Fingerprint identity — `fingerprint_for` / `fingerprint_of` (correlator.py:225–262)

- Scheme v2 (ADR-017): `sha256("service|check|severity_in|region|env|cluster")[:16]`. `env` and `cluster` are **required keyword-only** — no call site can silently mint an env-blind hash. This was a real fix: v1 was env-blind and a staging alert hashed identically to a prod alert (the documented staging→prod collision pre-mortem).
- An empty `env`/`cluster` is a real namespace ("unknown"), never a wildcard — deliberate and documented.
- `legacy_fingerprint_for` exists for a bounded allowlist migration window only.

### Dedup (correlator.py:587–611, 731–743)

- `_seen`: fingerprint → `{first_seen, last_seen, prior}`. Same fingerprint inside the dedup window (`window_s`, default **300s**) → kind `"duplicate"`, inherits the prior disposition via `note_disposition()` (the pipeline is required to call it after the gate runs), no Jev call.
- O(1)-amortized expiry via the insertion-ordered `_seen_order` deque (C1 hardened this after replay tests showed 20k ingests taking 27s with the old scan).
- On `resolve_episode()` and on flap-reopen, the stored `prior` is **discarded** so a stale decision can never resurrect across the resolve boundary. This is the honest mechanism against "stale-prior leak."

### Episode lifecycle (ADR-001; design/d10-correlator-craft.md)

- `Episode(state, flap_count, opened_ts, last_alert_ts, closed_ts, close_reason)`, keyed by fingerprint.
- **Freshness bound: 72h** (`EPISODE_FRESHNESS_S`). A fingerprint unseen >72h starts a FRESH episode (flap count reset), not a reopen. Records pruned at 72h — pruning expires *identity*, it never flips state to closed (the code comment is explicit: "Dropping a record is NOT closing an episode").
- **Only two legal closes** (`CLOSE_REASONS`): `operator_resolve` (explicit human action) and `verified_resolve` (an *authenticated* resolve signal — unsigned resolves never count). **Silence never closes.** This is the load-bearing anti-suppression rule, and it survived a pre-mortem challenge (a dead forwarder producing silence would auto-close every open episode — the exact outage Sentinel exists to catch).
- Flap semantics: reopen bumps `flap_count` (visible to the operator) but **never auto-promotes severity** — the fresh alert goes through the full gate evaluation. The panel rejected auto-promotion (no vendor precedent; escalates stale state).
- P1/P2 carve-out (DR-13): source labels `critical`/`high`/`p1`/`p2` (case-insensitive) **bypass change-window queuing entirely** — classification happens pre-Jev, deliberately coarse: a source-labeled `critical` pages now even if the model would have said P4. "Fail-loud beats fail-silent for P1."

### Classification pipeline — `_classify_locked` (correlator.py:587–731)

Strict order: **(1)** change window match (non-P1/P2 only) → `"change_window"`; **(2)** duplicate inside dedup window of an open episode → `"duplicate"`; **(3)** active storm → `"storm"` fold (episode untouched, flap deferred until the storm clears); **(4)** episode outcome → `"new"` / `"reopened"`; **(5)** storm declaration check; **(6)** record observation. Change-window entries are `{service, start, end}` ISO-8601 dicts; unparseable entries **fail open** (never match).

### Storm detection — adaptive (C1; design/rfc-c1-stepped-failopen.md §2; PR #76)

This is the flagship fix for the C2 finding. The old fixed rule (`>20 distinct fingerprints / 60s`) permanently folded **~98% of all-distinct traffic** on a busy estate (research/load-c2-2026-10-04.md §5) — it could not distinguish "incident storm" from "busy estate."

- **Declaration rule: `W(t) >= max(F, k·B(t))`** where `W(t)` = distinct fingerprints first-seen in the trailing 60s window, `B(t)` = EWMA of that measure over the trailing 24h with a **6h half-life**, ticked every **60s** (`STORM_BASELINE_TICK_S`).
- `F` (absolute floor, default **20**): preserves small-estate semantics — the adaptive rule may only *widen* coverage on large estates, never narrow it on small ones.
- `k` (velocity-ratio multiplier, default **5.0**, clamped **[3, 12]**): per-tenant tuning rule documented as `k = P99.9(R) + 1.5` over 30 days of R samples. Safety-affecting (it decides what fraction of traffic reaches the proof engine): manual changes need two-person attestation (ADR-022/B3); `set_storm_k()` logs loudly and restarts a **30-day review clock** (`k_review_due()`).
- **I-1 (Type 1 invariant): B freezes while a storm is declared** — the detector may not learn from the thing it is detecting. Structural: during an active storm every ingest folds at step 3 and never reaches the tick; plus an explicit 300s post-declaration freeze (`STORM_BASELINE_FROZEN_S`).
- **I-2: declaration changes WHEN, never WHAT** — the fold path stays deterministic, pre-Jev, folded-not-suppressed (D3/ADR-016). One-call-per-storm (DR-14): the aggregate digest carries the page.
- **Cold-start bootstrap (R15):** fresh correlator has B=0 → threshold = F → a busy estate declares storm constantly (the code comment reproduces the C2 cliff: 98.1% fold). After a **30-min observation window** (`BOOTSTRAP_WINDOW_S`), if B is still ~0, B bootstraps from the true distinct velocity observed during the window, with busy-estate vs incident discrimination (velocity high through all thirds → busy, bootstrap to median; low-early/high-late → likely incident, **refuse** and require manual attestation). Loudly logged.
- **Anti-poisoning (Vault V3):** baseline growth beyond **3× the 30-day median** requires two-person attestation. Two legs, because the RFC's literal single-leg design was circular (I-1's freeze pins B forever under a sustained >k× shift): the *baseline leg* watches slow poisoning via non-declaring ticks, the *velocity leg* watches sustained tick-velocity vs the median. `rebase_baseline()` is the attestation's effect — operator-approved B reset that moves the guard's reference. This circularity trap was found while building, and the honest implementation note is in the code. Credit where due.
- **Per-fingerprint level-shift z-score** (`_fp_hourly`: 25 hourly buckets per fingerprint, 50k-fingerprint cap with oldest-eviction): this fingerprint's current-hour count vs its own trailing-24h baseline, clamped [-50, 50]. Exists because a step function has zero derivative after the step — the *level* is the anomaly. Used by the fail-open digest for triage ranking.

### D7 — history provenance (design/d7-history-provenance.md)

- `read_history(fingerprint)` is the single provenance path; every ingest reads history **once, under one clock** (`history_as_of` = the knowledge cutoff stamped on the result and on `HistoryView`).
- **Label-pipeline freshness SLO: 900s.** A stale `LabelSnapshot` **pages** (a `Page` record to `page_sink`, kind `"label_stale"`) — rotten labels never get a silent vote. A *missing* snapshot is not stale (absence is visible as `labels_from="alert"`). Honest note in the code: the label pipeline doesn't exist in-repo yet, so the staleness machinery is built and tested but unreachable — the SLO path currently has no producer.
- **Novelty in shadow:** `novelty_mode="shadow"` (default) logs what the novelty rule *would* have done without affecting dispositions. `promote_novelty_to_live()` requires ≥100 shadow events (`NOVELTY_SHADOW_MIN_EVENTS`) and the parameter can only *raise* the bar, never lower it. The ≥95% spot-audit agreement and operator sign-off are explicitly process gates outside the code.
- Page emission has per-`(reason, service)` **cooldown of 300s** (anti-fatigue); the correlator owns emission discipline, the sink owns delivery dedup.

### State handling

- **Everything is in-memory and volatile.** `Correlator()` is constructed fresh at pipeline build (`src/sentinel/receiver.py:1151`, and again at `:1195` for the shadow pipeline). There is **no persistence, no snapshot, no restore** — checkpoint.py seals the *event log*, not correlator state. Every process restart replays the full R15 cold start.
- `detector_health()` is a read-only health snapshot (W, threshold, F, k, B, last declaration, frozen flag, growth-guard state) for the violet banner; called from the fail-open banner path (`src/sentinel/failopen.py:540, 695, 738, 942`), not per ingest.
- Tests: `tests/test_correlator.py` (fingerprint, dedup, storm, change windows), `tests/test_correlator_craft.py` (episodes, flaps, P1/P2 carve-out, freshness, close paths), plus C1 replay tests in `tests/test_c1_stepped_failopen.py`. The suite is example-based unit tests; there is no size classification, no soak test, no property-based coverage of the D10 state machine.

---

## 2. WHAT'S MISSING

Judged against the Phase-1 research (`research/architecture-patterns.md`, `research/testing-at-scale.md`, `research/ai-ml-production.md`) and the four principal skills. Every item below is a gap the current design does not close.

### M1. Restart amnesia — the adaptive detector is volatile process memory (ARCHITECTURAL)

The flagship C1 achievement — a *self-calibrating* storm threshold, the campaign law's headline virtue ("self-calibrating over month-tuned constants") — dies with the process. `Correlator()` is constructed fresh on every pipeline build; B(t), the 30-day daily ring, the tick-velocity ring, the attested baseline, the per-fingerprint hourly counts, and all episode/dedup state are gone on every restart. What returns is the **R15 30-minute blind window**: on a busy estate the detector falls back to the floor F=20 and folds ~98% of traffic — the *exact C2 cliff the PR was built to fix* — for up to 30 minutes after every deploy, crash, or config reload. If the bootstrap's discrimination then refuses (incident in progress at restart time), B stays 0 and the cliff persists until a human attests a rebase, with **no SLA on that human**.

Per principal-systems: *eternal friction* — the network drops, the process restarts, the maintainer deploys at 3am. Architecting for the demo world (warm, long-lived process) while the failure shape lives in the restart world is designing for the wrong adversary. The D10 done-checklist asks "dependency down 4h — what happens?" The correlator's own answer should be: "what happens when *I* go down for 4 minutes?" — currently, 30+ minutes of structurally blind storm detection. A baseline learned over 24h that can be lost in a SIGKILL is not a baseline; it's a cache with delusions of grandeur.

### M2. The tuned thresholds have no eval harness — RFC-reasoned, never measured (principal-mindset §2, ai-ml-production.md Q2)

The detector's soul is a stack of tuned constants: `k=5`, `F=20`, 6h half-life, 60s tick, 300s freeze, 30-min bootstrap, 72h freshness, 300s dedup window, 300s page cooldown, 100-shadow-event promotion floor, 3× growth guard, [3,12] k-clamp. Each has an RFC argument. **None has a measurement.** The Phase-1 ai-ml-production research states the eval discipline exactly: golden sets of 50–200 *real* tasks, labels frozen ("if you keep editing labels to match the new model, you are cheating"), smoke/regression/hard tiers, three-way split discipline. There is no correlator eval harness — no frozen estate traces (busy-estate + real-incident) against which declare precision/recall is scored, no regression gate that fails when someone retunes `k` and fold-behavior shifts. Per principal-mindset's proxy-trap audit: the *true objective* is "declare on real incident storms, never on busy-estate velocity"; the proxy is "the RFC's reasoning sounds right." A proxy that moves (k=5→k=7 by vibes) while the truth is unmeasured is tuning theater. The 30-day k-review clock is a calendar reminder, not evidence — it fires whether or not anyone measured anything.

### M3. No stage isolation — one global lock, and readers can stall writers (architecture-patterns.md; principal-systems done-checklist)

The entire correlator — ingest, prune, baseline tick, and every read — serializes on **one `threading.Lock`**. There are no stage boundaries (SEDA discipline: queues between stages so one stage's stall never propagates). Two concrete consequences:

- **The hot path is O(window) per alert.** The storm check rebuilds the distinct set over the trailing 60s window on *every ingest*: `distinct = {f for _, f in self._recent}`. C1's quadratic-avoidance work covered `_seen`/`_observed` expiry but left the storm scan. At C2 burst rates (10k/min) the 60s window holds ~10k entries — that's a ~10k-element set build per alert, ~1.7M element-visits/second at 167 alerts/s, under the global lock. The D10 checklist's "O(1) amortized plus the storm-deque scan" is honest but the scan is the binding term at burst volume.
- **`detector_health()` scans the same window under the same lock**, and it's called by banner renders (`failopen.py:942` — "the console polls this"). A 1Hz console poller during a burst contends with every ingest for the lock while doing its own O(window) scan. A read-only observability path must never be able to slow the write path. Today it can.

### M4. A new leak-class bug: `_last_page` is unbounded (testing-at-scale.md Q3 — soak discipline)

The C2 report found by hand that `gate.emitted` grows unboundedly — the exact class of bug soak tests exist to catch mechanically. The correlator has its own instance: **`_last_page: dict[tuple, float]`** (correlator.py:429) has **no bound and no expiry**. It's keyed by `(reason, service)` where `service` arrives from alert labels — caller-controlled strings. An estate with many distinct service labels, or a misbehaving integration emitting novel service names, grows this map forever. Every other map in the class got a bound (50k fingerprint cap on `_fp_hourly`, 72h prune on episodes, ring sizes on the V3 guards); this one didn't. And there is still **no soak test** — C2 remains a one-off report, and the testing research's verdict stands: *"Evidence that isn't a gate decays."*

### M5. The `_fp_hourly` cap degrades silently under the load it exists for (eternal friction)

`FP_HOURLY_FP_CAP = 50_000` evicts the **oldest-inserted** fingerprint when full. During exactly the event this machinery serves — a storm with >50k distinct fingerprints — the level-shift statistics for the *most important* fingerprints get evicted by insertion order, and the digest's triage ranking quietly goes blind. Eviction by importance (e.g., protect high-current-hour counts, or the fingerprints in the active storm's `storm_counts`) is the honest policy; insertion-order eviction is the easy one.

### M6. Change-window config fails open *silently* — no load-time validation (principal-systems done-checklist)

Unparseable change-window entries never match (fail open: no suppression on bad config). That's the correct *runtime* direction, but there is **no validation at config load**: a typo'd ISO timestamp silently disables a window, and nobody learns until an incident pages during what was supposed to be a quiet window — or worse, until a post-incident review. The done-checklist asks: "Junior deploys a stale config — caught automatically or crash?" For change windows the answer is: silently ignored. Fail-open at runtime should be paired with fail-loud at config time.

### M7. Shadow → canary is doctrine, not machinery (execution-doctrine §6; principal-governance §4)

- `set_storm_k()` and `rebase_baseline()` flip the detector's behavior for the **entire estate atomically** — no flags, no canary, no dark launch. The single most safety-affecting knob in the system (it decides what fraction of traffic reaches the proof engine) has a louder log line but the same blast radius as before.
- Novelty's promotion path is half-built: the in-code gate (≥100 shadow events) exists, but the real gates — the 95% spot-audit and operator sign-off — are "process gates recorded in the decision log," i.e., they don't exist yet as mechanisms. The shadow log itself is 72h-pruned, so the evidence window is short and there is no export path for the audit.
- There is **no production-traffic replay loop**: the C1 replay tests run in CI on fixtures, but nothing replays captured production traffic against a candidate detector tuning before it ships (testing-at-scale.md: shadow deployments consuming the same events, compared before users are affected).

### M8. No feedback loop on declare quality — the detector never learns whether it was right (principal-governance §4, unified telemetry)

`detector_health()` exposes the detector's *inputs* (W, threshold, B) for the banner, but nothing records declare *outcomes*: was this storm a real incident or busy-estate velocity? The event log records the declaration, but there is no reconciliation layer ("events in = decisions + folds + errors, accounted" — the data-quality/invariant layer the testing research marks MISSING) and no weekly declare-precision metric. Per the DORA/SPACE lineage in principal-systems: measure across dimensions, never one. Today the detector's only production feedback is the attestation process — a human noticing. A detector that cannot be scored cannot be improved; k's "P99.9(R) + 1.5" tuning rule requires 30 days of R samples that nothing in the system is currently collecting into a queryable store.

### M9. Fingerprint identity has no schema contract at ingest (testing-at-scale.md Q2 — contract layer)

Dedup identity is derived from alert labels (`env`, `cluster`, `region`). There is **no schema validation** that these fields are present, well-formed, or stable: a source that starts emitting `env="Prod"` vs `env="prod"`, or drops the `cluster` label during an upgrade, silently *splits* one incident into two fingerprints (dedup breaks, episodes fork, flap counts reset) or *merges* two estates' alerts (the staging→prod collision ADR-017 killed, resurrected by a label regression). The contract-testing research is exact here: the boundary between the correlator and its label sources is untested, and label-shape drift is the silent upstream change hand-written assumptions encode as truth. A checked-in label contract (required fields, normalization rules, a nightly drift check against real sources) is the missing half.

### M10. Attestation is a human process with no SLA and no degraded-mode clock

The V3 growth guard and the R15 bootstrap refusal both terminate in "requires two-person attestation" — a human process the code cannot see. During the gap (busy estate post-shift, or restart-during-incident), the detector sits in permanent storm-fold and the proof engine is starved, with no escalation timer, no page-to-a-human, and no documented maximum blind duration. Per principal-systems: failure is *budgeted*, not wished away — the blind window should have an error budget (max acceptable fold-starved minutes per quarter) with the attestation SLA sized to fit it, not an open-ended "someone will notice."

---

## 3. CONCRETE REVISION PROPOSALS

Ranked by value = (severity of the failure it prevents) × (how much of the system it hardens) ÷ (implementation cost). Each names what would verify it — a proposal without a verification is a wish.

### P1. Durable baseline snapshot with staleness-aware restore — kills M1 (the restart cliff)

**Rationale.** The entire adaptive detector is currently a per-process cache. Persist `(B, _baseline_daily, _tick_w tail, _attested_baseline, storm_k, k_set_at, saved_at)` to durable storage on a slow cadence (e.g., every baseline tick that moves B materially + on clean shutdown) and restore on startup with a staleness policy: accept the snapshot if `now - saved_at < 24h`, decay B toward the floor proportionally to staleness beyond that, and always re-enter the R15 discrimination logic (a restored B is a prior, not a truth — the bootstrap's busy-vs-incident test still runs, just from a warm start instead of zero). Storage options in-repo: a small signed state file next to the event log (reuse the checkpoint HMAC discipline — checkpoint.py already signs `(head_seq, head_hash, ...)`; sign the baseline snapshot the same way so a tampered restore is rejected, not trusted), or an event-log-sealed `baseline_snapshot` event type read back at startup. Seal the write in the hash chain — the restoration of a safety-affecting parameter must be auditable.

**Verifies by:** a chaos experiment — kill -9 the pipeline mid-burst on the seeded C2 harness, restart, and measure (a) time until the declaration rule matches pre-kill behavior (< 5 min, vs the current 30-min blind window) and (b) fold-rate in the first 10 minutes post-restart stays within 2× of the pre-kill fold rate instead of collapsing to ~98%. Gate it in CI: the experiment fails the build if the post-restart cliff reappears. Also verify the negative: corrupt the snapshot file → startup must reject it loudly and fall back to R15-from-zero (fail-loud, never fail-silent on tampered state).

### P2. O(1) incremental storm-window distinct count + lock-free health reads — kills M3's hot path

**Rationale.** Replace the per-ingest `_recent` set rebuild with a maintained counter: a `collections.Counter` (or dict of fp→count) over the window, incremented on append and decremented on popleft-expiry, with `len(counter)` as the O(1) W(t). This is the same quadratic-avoidance discipline C1 already applied to `_seen`/`_observed` — finish the job on the hottest path. Separately, make `detector_health()` read a **snapshot** (updated under the lock at most once per tick) instead of scanning `_recent` under the global lock, so banner polls can never contend with ingestion. Stage-isolation principle: the observability stage must not share a lock with the write stage.

**Verifies by:** micro-benchmark in CI — 10k distinct fingerprints in the 60s window, 20k ingests: ingest p99 must not regress vs the current implementation at small windows and must improve ≥5× at full windows; plus a contention test (banner poll at 10Hz during burst ingest) showing ingest p99 unaffected. Threshold-gated, not eyeballed.

### P3. A frozen correlator eval harness — the tuned thresholds get the ai-ml-production Q2 discipline (kills M2)

**Rationale.** Build the eval harness the thresholds have never had: a frozen, versioned corpus of estate traces — busy-estate velocity traces (labeled "never declare") and real incident-storm traces (labeled "declare within N seconds of onset") — with declare precision/recall and time-to-declare as the scored metrics. Tiers per the research: smoke (small, every PR), regression (full corpus, on any change to storm parameters or classification order), hard (the C2 uniform-distinct trace and the R15 cold-start trace — known failures never forgotten). Freeze the corpus; tuning `k` against the harness and editing labels to match is cheating, enforced by corpus versioning in git. The harness also produces the 30 days of R samples the `k = P99.9(R)+1.5` rule needs — collected from production declares, stored queryably, instead of existing only as an RFC sentence.

**Verifies by:** the harness itself is the verification — its first act is to score the *current* constants and publish the baseline (if k=5 scores worse than k=4 on the frozen corpus, the RFC reasoning was wrong and the number changes with evidence). Promotion gate: any PR touching storm parameters must show the harness delta; a recall regression on the hard tier blocks merge.

### P4. Bound `_last_page`, add the weekly soak, gate the leak class (kills M4)

**Rationale.** Cap `_last_page` with the same discipline as the other maps: TTL-expiry (entries older than the page cooldown × N, or 24h) plus a hard cap with loud logging on eviction. Then stand up the soak the testing research demands: a weekly multi-hour run at moderate load with assertions on RSS growth and on the sizes of every correlator map (`_seen`, `_episodes`, `_observed`, `_last_page`, `_fp_hourly`, `_shadow_log`), failing on unbounded growth. This converts "C2 found a leak by hand" into a mechanical practice.

**Verifies by:** the soak itself — it must currently FAIL on `_last_page` under a synthetic many-service-label workload (proving it catches the class), then pass after the bound lands. A gate that can't demonstrate catching the bug is theater.

### P5. Flag + canary for detector retunes; finish the novelty promotion machinery (kills M7)

**Rationale.** Per principal-governance, deploying code ≠ releasing: `set_storm_k` and `rebase_baseline` need a staged rollout — e.g., a detector-config version that can run the new (k, B) *in shadow* on live traffic (declare decisions logged, fold path unchanged) for N hours before the flip, with automatic rollback on fold-rate deviation beyond a bound. This reuses the existing shadow-mode machinery (D7) rather than inventing new infra. For novelty: build the missing half — an exportable shadow-evidence report (the 100-event gate is currently 72h-pruned in-memory with no audit trail) and the spot-audit workflow as a real checklist with sign-off records, not a decision-log mention.

**Verifies by:** a canary drill — retune k on the seeded harness with the canary path, show the shadow log diverging/not-diverging from live, and demonstrate the auto-rollback firing on an intentionally bad k. If the rollback can't be demonstrated, the flag is decoration.

### P6. Declare-outcome journal + invariant reconciliation (kills M8)

**Rationale.** Emit a `storm_declared`/`storm_cleared` event pair (already partially in the event log) plus a post-hoc outcome label path: when an operator resolves the incident behind a storm (or marks "not an incident"), the declaration gets its outcome. A weekly job computes declare precision and time-to-declare distribution. Alongside, add the accounting invariant the testing research names: per-window `ingested = new + duplicate + storm_folded + change_windowed + label_stale + errors` — asserted in CI on harness traces and monitored in production. A correlator whose books don't balance is lying about something; today nothing checks.

**Verifies by:** the reconciliation assertion running green on the frozen corpus (P3) and a weekly declare-precision number published to the decision log — the first number the k-review clock can actually use.

### P7. Fail-loud config validation for change windows (kills M6)

**Rationale.** Validate change-window entries at load time: ISO-8601 parse, `start < end`, known service names (warn on unknown), and refuse-or-loudly-warn on garbage — while keeping the *runtime* fail-open behavior for entries that become invalid later. Small, cheap, and it answers the done-checklist question ("junior deploys a stale config") with "caught automatically."

**Verifies by:** a unit test feeding the historical typo shapes (the ones operators actually make) and asserting loud rejection at load; plus a startup log line inventorying active windows so "the window was silently dead" becomes impossible.

### P8. Label contract for fingerprint inputs (kills M9)

**Rationale.** Check in the label contract the fingerprint depends on: required fields (`env`, `cluster`, `region`), normalization rules (case-folding? trim?), and the dedup-identity consequences of each field's absence. Add a nightly drift check comparing real source label shapes against the contract — the self-owned provider-verification harness the testing research prescribes for third-party boundaries, applied to alert sources. Label-shape drift is the quiet way the ADR-017 fix gets undone.

**Verifies by:** a contract test that fails when a fixture source drops `cluster` (showing the dedup-identity fork), and the drift job alerting on the first real-world label change after it ships.

---

## Reviewer's notes (out of scope of the three sections, for the program)

- **What the builders got right:** the honesty density of this code is unusually high — the I-1 circularity trap, the R15 refusal case, the "unverified external assumption" label on the 900s SLO, the admission that D7's staleness machinery is built-but-unwired are all written down *in the code*. The pre-mortem discipline (D10 §6) visibly shaped the design. My findings are gaps in *architecture and verification*, not in craft or candor.
- **Type assessment (principal-systems):** P1 (durable baseline) and P3 (eval harness) are Type-1-ish — they change what the detector's calibration *means* across restarts and what "correct" means for thresholds. P2, P4, P7 are Type 2 — decide fast, measure, roll back if wrong.
- **What I did not verify:** I did not run the test suite or the C2 harness (docs-only mandate); all behavioral claims are grounded in code read at `program/architecture-revision-dom-correlator` (== `program/architecture-revision` @ b831d3c). Line numbers cited are from that tree.
