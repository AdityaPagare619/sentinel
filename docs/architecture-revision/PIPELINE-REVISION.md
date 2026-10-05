# Sentinel Pipeline Revision — Architecture Decision Record

**Program:** Sentinel Architecture Revision · **Phase:** 3 (synthesis)
**Branch:** `program/architecture-revision-phase3` (from `program/architecture-revision` @ 2deb22f)
**Date:** 2026-10-05 IST · **Author:** Phase-3 synthesizer
**Status:** DRAFT — no building until Aditya approves (per his 2026-10-05 ~17:00 IST directive)

**Inputs synthesized:** Phase-1 research (6 streams: `research/swe-discipline.md`,
`research/architecture-patterns.md`, `research/devops.md`, `research/ai-ml-production.md`
— repair landed 2deb22f, now 702 lines, complete Q1–Q5 + 10-requirement AI-boundary
checklist + G1–G6 gaps), `research/testing-at-scale.md`,
`research/operating-without-vendor-apis.md`) and Phase-2 domain memos
(`docs/architecture-revision/{receiver,correlator,race-jev-boundary,deterministic-gate,forwarder-byok,event-log-audit,platform-api,prism-ui,devops-environments,testing-qa}.md`).
Every structural claim below cites the memo or research file it rests on.
Skills applied: principal-systems, principal-governance, principal-mindset,
execution-doctrine.

**Reading note on provenance.** Two Phase-1/Phase-2 conflicts were adjudicated
by grounding in the code (details in §6, "Adjudications"); the race memo's
meta-finding that `research/ai-ml-production.md` was truncated is **resolved** —
the repair (2deb22f) re-authored Q2–Q5 and stands as the AI-boundary standard.
One caveat from the repair agent is preserved: the single sentence completing
the "(Ham" fragment (the Hamel Husain judge-validation result in Q2) was
completed from fresh research, not recovered verbatim — treat that one
sentence as fresh-research-cited, not original-lane text. It carries its own
citations and is used accordingly.

---

## 1. Mandate & scope

### 1.1 The directive

On 2026-10-05 ~17:00 IST, Aditya issued a STOP on the narrow enterprise-testing
lane and ordered: **"don't hurry; revise the pipeline architecture."** His
critique, in his words: professional software engineering has multi-layers —
requirements, architectures, DevOps — that are missing; "without all these no
one would use our software and it's not even software now." He demanded each
principal revise their domain with fresh mindsets, deep web research first on
how FAANG/elite companies actually build (SWE discipline, architectures,
DevOps, AI/ML in production, testing at scale, operating without live
third-party APIs — mocking PagerDuty being one subdomain among many), and that
**no building resume until he approves the revision**. This document is the
approval gate's deliverable: Phase 3 of the Architecture Revision Program
(coordinator 8f55297a), synthesizing Phase-1 research and Phase-2 per-domain
memos into ADR-level decisions.

### 1.2 What this revises

- The **pipeline architecture**: layer boundaries, the contracts between them,
  what crosses and what never does, and how the current
  receiver → correlator → gate → forwarder → platform API (+ Prism UI) maps
  onto explicit layers (§2).
- The **requirements layer**: the missing REQ discipline — what a release
  requirements doc contains, REQ → architecture → test traceability, and the
  Definition of Ready for lanes (§3). Phase-1 names this the biggest gap
  (`research/swe-discipline.md` §3: "no single document says, per
  component/release: what the customer must observe, how well, and how we
  will verify it — written before the lane builds").
- The **DevOps layer**: the honest ₹0 machinery — post-merge verification,
  metrics, GitOps-for-one-box config, flag wiring, deploy/rollback,
  staging/prod story (§4).
- The **testing strategy per layer**: what each layer gets, the nightly drift
  harness, threshold-gated load, test-size classification (§5).
- The **ranked change list** with Type-1/Type-2 labels, rationale, and
  verification per change (§6); what stays (§7); the sequenced build plan
  with entry/exit criteria and a deliberate not-built list (§8); and the
  Type-1 open questions only Aditya can decide (§9).

### 1.3 What this does NOT revise (out of scope, by design)

- **No code.** Phase-3 is thinking and writing. Every proposal names its
  verification; none is implemented here.
- **The fail-open direction.** Fail-open-to-page (missed page = SEV-1
  silence vs wrong page = fatigue) is a settled Type-1 decision, priced in
  the race docstring and warranted by the principal-systems Data/AI clause
  ("no probabilistic system holds absolute veto over a critical path").
  The repaired AI/ML research confirms the choice and asks only that it be
  written as the Type-1 decision it is (`research/ai-ml-production.md` G1).
- **Campaigns and chats outside Sentinel.** TFB/petu-labs, Petu Studio,
  InboxPilot, the Foundry lab, Kaggle work — untouched. Chat boundaries
  hold.
- **Standing orders.** The no-GitHub-Actions order, ₹0 budget, and
  stdlib-only constraint (with the single ratified `cryptography==44.0.3`
  exception) are treated as fixed constraints, not variables. Where a
  proposal touches a standing order (the dead GHA workflows), it is flagged
  as an Aditya decision (§9), not assumed.
- **The UI stack decision.** Reconcile-or-migrate for Prism is a Type-1
  question for Aditya (§9); this document records both honest options, it
  does not pick one.

---

## 2. Revised pipeline architecture

### 2.1 The layers

Production practice stages a request's journey as
ingest → normalize → decide → act → observe (SEDA: Welsh, Culler, Brewer,
SOSP 2001; `research/architecture-patterns.md` §3.2), with a serve tier on
top. Sentinel's revised layering:

| # | Layer | Job (one sentence) | Current code home |
|---|---|---|---|
| L1 | **INGEST** | Accept vendor webhooks; authenticate; admission-control; assign the trace ID | `src/sentinel/receiver.py` (routes, auth, semaphore) |
| L2 | **NORMALIZE** | Anti-corruption: vendor payload → canonical `Alert`; raw bytes and vendor identity stop here | *New as an explicit stage* — today folded into receiver.py's `_normalize_*` |
| L3 | **DECIDE-1 (correlator)** | Deterministic pre-Jev triage: fingerprint, dedup, episodes, storm — "never pay Jev for deterministic work" | `src/sentinel/correlator.py` |
| L4 | **DECIDE-2 (gate)** | Policy kernel + instruction firewall + stepped fail-open ladder + the Jev race (decision module) | `src/sentinel/gate.py`, `race.py`, `failopen.py`, `firewall.py` |
| L5 | **ACT** | Durable delivery: outbox → PagerDuty via BYOK keys, retries, secondary channel | `src/sentinel/forwarder.py`, `pd_sender.py`, `spill.py` |
| L6 | **OBSERVE** | Hash-chained event log, metrics, traces, shadow pipeline — a stage, not an afterthought | `src/sentinel/eventlog.py`, `checkpoint.py`, (new) metrics |
| L7 | **SERVE** | Read-only platform API + Prism UI console (+ BYOK integrations surface) | `platform/server/`, `platform/ui/` |

**Mapping honesty.** The Phase-1 layering review scores Sentinel today as
"stages but not layers": receiver → correlator → gate → forwarder are named
pipeline *steps* executed as synchronous in-process calls with lateral
imports, no declared inter-stage contracts, no queues, no per-dependency
isolation (`research/architecture-patterns.md` §6, finding 1). The receiver
mixes three layers' jobs — it imports correlator, gate, forwarder, shadow,
policy_lifecycle, health (receiver.py:63–74) — the "gateway got too smart"
shape, in-process (`research/architecture-patterns.md` §2.4; `receiver.md`
§2). The revision does not re-plumb the process topology (no Kafka between
stages — the event-log memo explicitly rejects it, and the ripped-out-EDA
postmortem in `research/architecture-patterns.md` §1.2 is the standing
warning); it makes the **contracts** between stages explicit, versioned, and
machine-checked, which is the portable 80% of the layering discipline.

### 2.2 Contracts between each pair

Each contract is a **versioned artifact owned by the consuming side**
(principal-governance: unforgiving API design), not prose. The one existing
model is `models.py`'s frozen contract §3.2 of ARCHITECTURE.md — everything
below is measured against that bar.

**C1 — L1 INGEST → L2 NORMALIZE.**
*Crosses:* `RawInbound{route, headers, body_bytes, received_at, request_id,
auth_outcome}`. The ingress shapes are checked-in JSON Schemas, versioned
(`contracts/ingress/pd-trigger.v1.json`, `contracts/ingress/generic-webhook.v1.json`;
`receiver.md` R-6). Auth outcome (ADR-005 pass/fail + reason code) is attached
at this boundary — post-revision, `/v2/enqueue` carries the same HMAC scheme
as `/webhook/generic` (`receiver.md` R-1).
*Never crosses:* unauthenticated bytes reaching triage; bodies over the
route's cap (512 KiB on the PD-mirror route — `research/operating-without-vendor-apis.md` §4).

**C2 — L2 NORMALIZE → L3 DECIDE-1.**
*Crosses:* canonical `Alert` — and only the canonical shape. **`Alert.raw`
(the full vendor payload) and `Alert.source` (vendor identity) do not cross
this boundary.** The anti-corruption layer translates at the edge; the
decision core never sees foreign shapes (hexagonal rule;
`research/architecture-patterns.md` §3.4.2; `deterministic-gate.md` P4;
`correlator.md` P8's label contract governs the fingerprint inputs).
*Never crosses:* vendor DTOs, infrastructure types, unvalidated label
fields (the ADR-017 staging→prod collision, resurrected by label drift, is
the named failure).

**C3 — L3 DECIDE-1 → L4 DECIDE-2.**
*Crosses:* `TriageResult{kind, fingerprint, episode_ref, prior_disposition?}`
where `kind` is a **typed enum** (`new | duplicate | change_window |
storm_fold | reopened`), not a string. The correlator's structural paths
(S1) short-circuit the gate today via an untyped kwarg; the revision types
it (`deterministic-gate.md` P4).

**C4 — L4 DECIDE-2 → L5 ACT.**
*Crosses:* `Disposition{action: Action, reason: ReasonCode, evidence_refs}`
— the stringly-typed vocabulary (`~150` bare literals; `models.py` documents
8 of 20+ reasons) becomes a machine-checkable enum with structured variants
(error code, stale legs, policy-block reason as *fields*, not string
interpolation), with explicit versioned wire serialization — the event log
is the audit trail and history is never rewritten
(`deterministic-gate.md` P2, TYPE 1 for the vocabulary).
*Never crosses:* policy re-interpretation — ACT executes the disposition,
it does not re-decide it.

**C5 — L4 DECIDE → L6 OBSERVE.**
*Crosses:* `DecisionRecord{..., trace_id, stage_latencies{normalize,
correlate, gate_race, decide, forward}}`. One trace ID born at L1 ingress,
carried through every hop, on every log line and in the hash-chained event
body (principal-governance unified telemetry; `deterministic-gate.md` P6;
`devops-environments.md` P4; `receiver.md` R-3). Per-stage latency
attribution answers "which stage ate the budget" in one lookup.

**C6 — L5 ACT → L6 OBSERVE.**
*Crosses:* `ForwardReceipt{decision_id, attempt_no, wire_sha256, outcome}`
written **in the same transaction** as the outbox scheduler update (I2 —
today's proudest invariant: "the event log and the work queue can never
disagree — a database property, not a code convention";
`event-log-audit.md` §1.1). The simulated-spill audit lie
(`forwarder-byok.md` §2.5 — simulated pages replaying as `forward_failed`)
is fixed at this contract: `simulated: true` propagates, never recorded as
failure.

**C7 — L6 OBSERVE → L7 SERVE.**
*Crosses:* the versioned read contract. All read paths move under `/api/v1`
with the old unversioned paths as deprecated aliases emitting `Deprecation`
(RFC 9745) + `Sunset` (RFC 8594) headers; the write surface
(`/api/v1/integrations/*`) is documented in `openapi.yaml` for the first
time; new `GET /api/v1/audit/chain` serves the platform-sealed chain
(`event-log-audit.md` P5); `DecisionSummary` gains the freshness/policy
fields the UI's honesty machinery currently labels as absent
(`prism-ui.md` P2). Additive inside a version; new version only for true
breaks (Google/Azure/Stripe consensus; `research/architecture-patterns.md` §4.1).

**Cross-cutting rules (apply at every boundary):**
- **Idempotency keys on mutating writes** (principal-governance): PD
  `dedup_key` pinned per outbox row, never re-derived (`forwarder-byok.md`
  §1.1); `Idempotency-Key` on `POST /api/v1/integrations/test-page`
  (`platform-api.md` P1-5).
- **Zero data-model leakage:** opaque cursors replace SQLite rowids;
  filters stop reaching into engine JSON paths
  (`platform-api.md` P2-6, M7).
- **SSOT per entity** (principal-governance): the event log owns
  decisions; the platform tier stays read-only (`mode=ro`, grep-guarded —
  keep); no layer reads another's database.
- **Postel at the edge:** strict canonical form on emit, resilient parsing
  on ingest — with the liberality *tested* (unknown fields, Unicode,
  extra keys), not merely claimed (`receiver.md` R-6).

### 2.3 Ports, not imports, at the decision seams

`gate.py` imports ~14 sibling modules directly; `receiver.py` is
simultaneously composition root and business orchestrator
(`research/architecture-patterns.md` §3.4.4). The revision declares the
gate's seams as protocols owned by the gate (`ports.py`): `Corroborator`,
`FreshnessMonitor`, `PolicyGate`, `JevClient` — constructor-injected, with
today's constructors as default adapters (`deterministic-gate.md` P4,
Type 2). The hexagonal test — "run core logic in tests with zero
infrastructure" — passes for `evaluate_policy` today; the revision extends
it to `Gate`. The anti-corruption half (C2) rides the same change.

### 2.4 Failure isolation between layers (Envoy transfer)

Sentinel's fail-open *is* Envoy's panic mode — but Envoy quantifies the
path to panic and Sentinel does not (`research/architecture-patterns.md`
§6, finding 3). The revision adds the quantified path, configured above
the data path (config, not hard-coded):
- **Jev circuit breaker** between L4 and the judge: opens on N consecutive
  budget-busts or latency/error drift (outlier detection over a trailing
  window); while open, fail-open with **zero vendor calls** (stops the
  polite-retry burn); half-open probing before close, feeding the C1
  ladder's recovery signal (`race-jev-boundary.md` P3;
  `deterministic-gate.md` P7). This subsumes the 6–24% partial-degradation
  gap (page-and-hope today) and the 429-retry-inside-budget burn.
- **Per-sender bulkheads at L1:** token-bucket per source IP / routing key
  ahead of the 64-slot semaphore — the 503 admission control is defeated
  by slowness alone today (`receiver.md` R-2/R-4; M-2 slowloris).
- **Cost circuit breaker** on the judge path: warn/throttle/stop on
  daily/weekly Jev spend (throttle → cheaper path → cached only), the
  spend ledger aggregated from the already-parsed `input_tokens`
  (`race-jev-boundary.md` P2; `research/ai-ml-production.md` Q4/G6).
- The degraded-judge **rung 1** (second pipe / exact-cache on
  `input_sha256`, tighter budget inside the remaining 2700 ms, never
  allowed to suppress uncorroborated) gives the judge its own fallback
  before the no-Jev ladder (`race-jev-boundary.md` P1;
  `research/ai-ml-production.md` G3 — "the biggest gap").

### 2.5 What the layering deliberately does NOT add

No message bus between stages (the forwarder's queue+spill is the one
durable seam, and it stays the only one — any future broker must pass the
ripped-out-EDA test: name the flow needing time-decoupling, show the
outbox/idempotency story). No CQRS/event-sourcing beyond the audit log
(the log is audit-only by design; `event-log-audit.md` appendix). No
Kubernetes/Argo (mechanisms port, machinery doesn't).

---

## 3. Requirements layer

### 3.1 The gap, stated once

Phase-1's verdict is unambiguous: "the requirements gap" is the biggest
class in the repo (`research/swe-discipline.md` §3). Sentinel has a
CHARTER, five design laws, a constraint registry, and ADRs — parts of a
requirements doc — but **no single document says, per component/release:
what the customer must observe, how well, and how we will verify it,
written before the lane builds.** IEEE 830's gates fail on all eight
counts for everything in the repo: no requirement is uniquely
identifiable (no REQ-xxx numbering), ranked, verifiable (no per-requirement
check method named before implementation), or traceable. The constraint
registry covers *constraints on implementation*, not *customer-visible
behaviors with acceptance checks*. Wave lanes build from briefs — work
plans, not requirements.

### 3.2 What a release requirements doc contains

Every release ships `docs/requirements/<release>.md` with this shape
(IEEE 830-1998 structure, Amazon PR/FAQ front half):

- **REQ-ID** — unique, stable, e.g. `REQ-GATE-014`. Zero REQ identifiers
  exist in the repo today (verified by grep; `deterministic-gate.md` §2.2).
- **Statement** — the customer-visible behavior, in customer voice where
  it touches the operator ("the on-call is not woken for…"), never
  implementation ("the correlator will…").
- **Rank** — essential / desirable (IEEE 830's "ranked for importance").
  Essential = release-blocking; desirable = tracked, not blocking.
- **Verification** — the finite, cost-effective check named *before*
  implementation (IEEE 830's "verifiable"): the test file, the drill, the
  metric threshold. A requirement without a named check is a wish
  (`research/swe-discipline.md` §3a).
- **Source** — the ADR, ARCHITECTURE.md section, or customer commitment
  it traces to (IEEE 830's "traceable").
- **`[ASSUMED]` constants** (the gate's C_FP=$100 / C_FN=$50,000) are
  marked as assumptions with a named owner and a revisit date — they are
  requirements *about our uncertainty*, and they expire
  (`deterministic-gate.md` P3).

The front half is a one-page PR/FAQ in the customer's words (headline,
problem, solution, hardest questions answered honestly) — Amazon's forcing
function: "if you cannot write a compelling headline, the product is
probably not worth building" (`research/swe-discipline.md` §3b).

### 3.3 REQ → architecture → test traceability

A checked-in matrix (a table, not a framework — the 10-line cron beats
the platform here):

```
REQ-ID → ARCHITECTURE.md § / ADR → kernel branch / module → test file(s)
```

Enforced in CI: a kernel branch with no REQ fails the build; a new
policy-table branch requires a REQ first (`deterministic-gate.md` P3).
The testing half: `tests/REQUIREMENTS.md` maps each test file to the
requirement(s)/ADR(s)/design-law(s) it verifies, so "which behaviors
have no test?" is answerable in under five minutes
(`testing-qa.md` P3-2). The gate is where this bites hardest — every
suppression rule is a promise to a customer about when they *won't* be
woken — so `docs/requirements/gate-requirements.md` is the first
requirements doc written (`deterministic-gate.md` P3; Type 2 to write,
Type 1 to change).

### 3.4 The tamper-evidence threat model as a Type-1 requirement

"Tamper-evident durable truth" currently means *external-attacker*
tamper-evidence. Against the writer — the party the customer buys trust
from — the guarantee is one hourly HMAC'd anchor plus customer
discipline no artifact asks for; writer forgery and writer silence are
unaddressed; the 2-hour sink-failure bound is in-memory state that a
restart resets (`event-log-audit.md` M1). The P1 proposal: a Type-1
requirement doc naming the adversaries (post-hoc file editor;
compromised writer; silent writer; curious-but-honest operator) and,
for each, the detection mechanism, the detection time bound, the
evidence the customer holds, and what breaks the guarantee — reviewed
by the security principal, with disagree-and-commit on the
insider-writer row: accept the limit in writing, or fund the
external-anchoring work that narrows it.

### 3.5 Definition of Ready for lanes

No lane starts building without (principal-governance: no major
component starts from a feeling):
1. Named acceptance criteria (the REQs it serves, or a written reason
   there are none yet).
2. A short design doc *before* the PR: problem, rejected alternatives,
   author-written risk paragraph ("the strongest reason this could go
   wrong"), rollout/rollback plan — the Amazon/Google mechanism
   (`research/swe-discipline.md` §1; `deterministic-gate.md` P5 for
   policy changes).
3. A named single-threaded owner and the reviewer/owner routing
   (CODEOWNERS — the Linux-style ownership map; `research/swe-discipline.md` §1d).
4. A kill rate that is actually measured: most policy-change docs should
   die at the doc stage; a doc process that never says no is theater
   (principal-mindset anti-theater audit; `deterministic-gate.md` P5).
5. The goal-fidelity check at lane close: compare what shipped against
   the doc's stated goals (Google's yardstick;
   `research/swe-discipline.md` §1b).

---

## 4. DevOps layer

### 4.1 The honest audit first

`ops/devops-foundation.md` is an unusually good ops constitution — but
several of its load-bearing mechanisms are not wired into the running
system (`devops-environments.md` §1.1). The two claim-vs-reality gaps
that matter, both verified by grep on `program/architecture-revision`:

1. **The flag story is fictional at runtime.** `flags.json` /
   `global_kill_switch` have **zero readers in `src/`** (verified).
   The foundation doc's entire release-safety architecture — flag-off
   rollback `<5s`, kill-switch drill, canary-by-severity-band, "no
   emergency code rollbacks" — rests on a mechanism that doesn't
   execute. The monthly kill-switch drill, run today, would flip flags
   on disk that the receiver ignores: a drill of a rumor
   (`devops-environments.md` §2.1). Phase-1's devops research *assumed*
   the flags contract was live; Phase-2 corrected it by reading the
   code. The correction stands.
2. **The operator guide teaches the anti-pattern the scripts reject.**
   `docs/deploy-production.md` §8 (Upgrading):
   `git pull --ff-only && sudo systemctl restart`. The ops doctrine
   (`ops/devops-foundation.md` §4.2) and `scripts/ops/deploy.sh` call
   pull-and-restart "SSH-and-pray" and mandate pinned-SHA immutable
   artifacts with health-gated swap. The doc every new operator follows
   routes around the doctrine (`devops-environments.md` §2.2).

Under the standing honesty order, closing these outranks pure feature
value. The revision's DevOps layer is therefore sequenced
**honesty-first**: retract-or-wire before new machinery.

### 4.2 The ₹0 machinery (ranked by value-per-effort)

All items are ₹0 and compliant with the no-GitHub-Actions standing
order (`research/devops.md` §6):

1. **`/metrics` endpoint** (Prometheus text format, stdlib, ~50 lines) —
   the single highest-leverage observability gap (`research/devops.md`
   §6 item #1). Counters (webhooks in, decisions by disposition,
   forwards ok/failed, Jev timeouts/fallbacks), histograms (gate
   latency vs the 2700 ms budget, forwarder latency), gauges
   (`tap_lag_s`, outbox depth, config generation). Unlocks burn-rate
   math, canary auto-gates, dashboards. **Adjudication:** four memos
   propose this (`receiver.md` R-3, `forwarder-byok.md` P2,
   `devops-environments.md` P2, `platform-api.md` implied) — it is **one
   shared stdlib metrics module**, emitted on the receiver's `/metrics`
   (hot path) with the platform tier contributing its read-tier series;
   single owner; not four divergent implementations.
2. **Correlation ID** — one UUID born at L1 ingress, propagated
   correlator → gate → forwarder → event-log row, on every log line
   (principal-governance unified telemetry; `research/devops.md` §3.3).
   **Adjudication:** five memos propose this — it is one
   implementation, owned at the receiver (the one place an ID can be
   born), referenced by the others.
3. **Post-merge runner on a clean worktree** (cron/systemd timer) —
   pulls `main` into a throwaway worktree, runs the full gate per new
   SHA, posts per-commit verdicts. Replicates CI's three detection
   classes (post-merge combination, clean environment, blame
   attribution) with zero GitHub Actions (`research/devops.md` §1.3;
   `devops-environments.md` P5). This is also the interim mechanical
   merge gate while CI is dead (§5.4).
4. **Wire `flags.json` into the live gate — or retract the `<5s`
   claim.** Two acceptable outcomes; wiring is strongly preferred
   (decoupled deployment is the right architecture). Verify on
   `staging-lab`: `flagctl set global_kill_switch true` → synthetic
   alert round-trips as `passthrough` within seconds
   (`devops-environments.md` P1).
5. **Fix `docs/deploy-production.md` §8** to mandate `deploy.sh`
   (pinned SHA → health-gated swap; `rollback.sh` for reversal; `git
   pull` on the prod box forbidden by policy) —
   `devops-environments.md` P3, a 10-line doc edit that kills the
   restart-blind-window-as-default.
6. **Config-as-commits (GitOps-for-one-box):** `/var/lib/sentinel/config`
   (+ non-secret `/etc/sentinel`) versioned in Git; `deploy.sh`
   snapshots config alongside the artifact; `rollback.sh` restores both.
   Every production config change becomes a commit — audit trail plus
   `git revert` rollback (`research/devops.md` §2.1, principle 2;
   `devops-environments.md` P6).
7. **First SLO + error budget** (a decision doc, not code): e.g. "99.9%
   of real pages `forward_confirmed` (primary or secondary) within 30
   days; p99 gate latency ≤ 2700 ms." The budget converts reliability
   into release policy — launches proceed while budget remains, halt
   when exhausted (Google SRE; `research/devops.md` §3.2;
   `devops-environments.md` P7).
8. **Shadow-diff auto-gate:** a script computing decision counts,
   suppression rate, latency p99, forward failures from the event log
   against thresholds → PASS/FAIL. ADR-022's two-human sign-off stays,
   but humans sign *numbers* (`research/devops.md` §2.3;
   `devops-environments.md` P8).
9. **Page-vs-ticket classification** for Sentinel's own signals
   (`BROKEN: checkpoint` = page; `FORWARD FAILED` = page;
   `webhook_auth_fail_open=true` = ticket; disk watermark =
   ticket-escalating-to-page; dead-man trip = page) — every page
   actionable, every alert with a runbook link
   (`research/devops.md` §5.2; `devops-environments.md` P9).
10. **Burn-rate alerts on the watcher** (14.4× page / 6× page / 1×
    ticket, multi-window) replacing raw threshold polling — after
    #1 and #7 (`research/devops.md` §3.2).
11. **Postmortem template in `ops/` + create `ops/incidents/`** —
    three runbooks already reference the missing directory
    (principal-systems operating rule 5: blameless postmortem within
    72h, with "where we got lucky").
12. **Parity/drift audit script** — diffs the prod box against the repo
    (pinned SHA? deps match? Caddyfile matches template? config schema
    valid?) — "continuously reconciled" without Argo CD
    (`research/devops.md` §2.1).
13. **Pre-push hook** (fast subset: lint/format + changed-area tests +
    secrets grep) — complements #3 (`devops-environments.md` P13).
14. **Wire `record_request()` at receiver ingest** — unlocks the
    per-source heartbeat subjects (L3-A3-1, currently dormant by the
    doc's own honest flag).
15. **Quarterly toil audit + secrets-rotation calendar** as repo
    artifacts (the toil cap is enforced by measurement;
    `research/devops.md` §5.1).
16. **Blue-green receiver on one box** — explicitly **deferred,
    conditional**: two systemd units + Caddy port flip only if restarts
    measurably lose pages after #4/#5 land. Building it before the
    flag wiring would be local optimization (principal-systems law 1).

### 4.3 Staging / prod story (kept, tightened)

The four tiers (`local` / `staging-shadow` / `staging-lab` / `prod`)
with the **never-pages rule** (Type-1) stay — the
"nothing real lives in a place with pretend rules" classification is
ahead of many small teams (`research/devops.md` §4.3). Tightening:
`staging-shadow`'s startup refusal of prod routing keys moves from
build request (L3-FLAGS-3) to code; the shadow tap's `tap_lag_s`
freshness metric gets its written SLO threshold; the static console's
GitHub Pages story stays (staging simulated showcase vs prod real
surface, already shipped in PR #79). Preview environments for PRs stay
deprioritized — real value only when contributor load grows
(`research/devops.md` §6 item #17).

### 4.4 What NOT to do (negative checklist)

- Don't adopt Kubernetes/Argo CD for a two-process stdlib system — the
  mechanisms port (#6, #12), the machinery doesn't.
- Don't buy LaunchDarkly — `flags.json` + the lifecycle rule is the
  80%, once #4 wires it.
- Don't chase DORA deploy frequency as a vanity metric — for a paging
  pipeline, change failure rate and recovery time are the metrics that
  matter; frequency is the proxy that decouples
  (`research/devops.md` §1.1; principal-mindset proxy-trap audit).
- Don't build #10/#16 before #1–#5 — sequencing is the proposal.

---

## 5. Testing strategy per layer

### 5.1 The per-layer contract

Each layer gets the test kinds its failure modes demand — not a flat
"more tests." The pipeline pyramid for ingest→decide→act
(`research/testing-at-scale.md` Q5): many fast unit tests on pure
functions at the bottom; contract/schema above them; data-quality and
integration in the middle; chaos and performance at the top, nightly,
non-blocking until trusted.

**L1 INGEST (receiver).**
- Contract/schema: checked-in JSON Schemas for both ingress shapes;
  every inbound fixture validates; a CI job fails when `_normalize_*`
  code and schema disagree (`receiver.md` R-6;
  `research/testing-at-scale.md` Q5 — currently MISSING).
- Auth matrix (exists — the best-tested part of the receiver), extended
  to `/v2/enqueue` post-R-1.
- Postel liberality suite: unknown top-level fields ignored, extra
  `custom_details` pass through, Unicode summaries accepted
  (`receiver.md` R-6).
- Adversarial: slow-drip test (64 connections trickling bytes —
  legitimate traffic still 200s), 600 KiB body ⇒ 413 on the PD route,
  per-sender token-bucket flood test (`receiver.md` R-2/R-4).

**L2 NORMALIZE.**
- Anti-corruption tests: an import-graph test asserting the decision
  core never imports vendor shapes; `Alert` carries no `raw`/`source`
  past the receiver (`deterministic-gate.md` P4).
- Label-contract tests: fixture sources dropping `cluster` or
  case-shifting `env` must fork dedup identity visibly, and the drift
  job must flag it (`correlator.md` P8).

**L3 DECIDE-1 (correlator).**
- **Frozen eval harness**: versioned corpus of estate traces —
  busy-estate velocity (labeled "never declare") and real incident
  storms (labeled "declare within N seconds of onset") — scoring
  declare precision/recall and time-to-declare; tiers
  smoke/regression/hard (the C2 uniform-distinct trace and the R15
  cold-start trace are the hard tier — known failures never forgotten).
  Freeze the corpus; tuning `k` against it while editing labels is
  cheating, enforced by git versioning (`correlator.md` P3).
- Perf: O(1) incremental storm-window distinct count with a
  micro-benchmark gate (≥5× at full windows); lock-free health reads
  with a contention test (`correlator.md` P2).
- Soak: weekly multi-hour run asserting bounded growth of every
  correlator map — it must currently FAIL on `_last_page` under a
  many-service-label workload, proving it catches the class
  (`correlator.md` P4; `research/testing-at-scale.md` Q3).
- Invariant reconciliation: per-window `ingested = new + duplicate +
  storm_folded + change_windowed + label_stale + errors`, asserted in CI
  on harness traces (`correlator.md` P6).

**L4 DECIDE-2 (gate + Jev boundary).**
- Kernel: REQ ↔ branch ↔ test matrix complete; every `evaluate_policy`
  branch maps to ≥1 REQ and ≥1 test (`deterministic-gate.md` P3).
- Vocabulary: enum exhaustiveness test — every reason the kernel, the
  ladder, and the structural paths can emit appears in the enum
  (`deterministic-gate.md` P2).
- Policy-change design-doc gate: the next three policy PRs each link a
  pre-PR doc with a risk paragraph; at least one killed at the doc
  stage per quarter, on record (`deterministic-gate.md` P5).
- **Judge eval (the missing Q2):** frozen golden set of 50–200 real
  alert shapes (Oct-4 A/B traces + production shadow captures, not
  synthetic fixtures) with behavior-labeled expectations, hash-pinned;
  Jev-as-judge calibrated against human-oncall judgment with the κ ≥
  0.75 bar (Hamel Husain/Shankar; `research/ai-ml-production.md` Q2 —
  noting the repair caveat on the one fresh-research sentence);
  **timer-win decomposition as a standing metric** in every judge eval
  (the Oct-4 lesson: ~89% of flip signal was latency artifact — flip
  telemetry must separate latency from judgment, or the eval measures
  vendor weather); ladder-quality eval pricing degradation at each rung
  (paging coverage, suppression agreement vs healthy mode)
  (`race-jev-boundary.md` P5; `research/ai-ml-production.md` G4/G5).
- Shadow-before-promotion for any judge/budget/fallback change
  (`research/ai-ml-production.md` Q2/Q5).
- Chaos: stdlib fault-proxy with latency/reset/timeout/HTTP-error
  toxics between gate↔Jev and forwarder↔PD; experiment catalogue (Jev
  timeout storm; PD 429/5xx; event-log disk-full; correlator
  thread-starvation) each with steady-state metric + hypothesis + abort
  threshold; GameDay cadence with scorecard — CI/nightly, never
  production until the harness earns trust
  (`research/testing-at-scale.md` Q4).

**L5 ACT (forwarder).**
- Promote `FakePD`/`ScriptedHTTP` from `test_durable_forwarder.py` into
  shared `tests/doubles/`; add the schema layer (dedup_key > 255 ⇒ 400,
  summary > 1024, 512 KB limit, unknown routing key ⇒ 400), routing-key-
  scoped dedup incidents, the ack/resolve state machine, and scripted
  toxics: 429 **without** Retry-After (matches PD docs), 202-without-
  `"status":"success"`, connection reset, malformed chunk
  (`forwarder-byok.md` P4; `testing-qa.md` P2-1;
  `research/operating-without-vendor-apis.md` §6).
- Key-resolution E2E: key via `IntegrationStore` only (env scrubbed) ⇒
  `forward_confirmed` through the durable path; legacy/durable parity
  regression test (`forwarder-byok.md` P1).
- Simulated-spill roundtrip: simulated spill ⇒ audit event carrying
  `simulated: true`, zero `forward_failed` (`forwarder-byok.md` P6).
- Rotation drill: rotate keys with rows in flight against key-scoped
  FakePD — zero `SecretMissing`, zero dropped retries, zero extra
  incidents (`forwarder-byok.md` P3).

**L6 OBSERVE (event log).**
- The `/tmp/evlog-repro` genesis disagreement becomes a regression
  test: roll, then `verify()` passes; tamper one row, `verify()` fails
  naming the seq — both verifiers agreeing (`event-log-audit.md` P3).
- Drain-before-roll: queue a page, roll, assert the page is still
  delivered/visible post-roll (or the roll refused and paged)
  (`event-log-audit.md` P4).
- Orphaned controls wired: kill-mid-decision ⇒ reaper redrives;
  failing sink ⇒ page after the bound; retention dry-run ⇒ plan
  without deletes (`event-log-audit.md` P2).
- Warm-export round-trip: archived `decision_requested` resolves its
  raw payload; old dead-letter outbox rows inspectable post-promotion
  (`event-log-audit.md` P8).
- Chain continuity + no-silent-drop reconciliation in CI on seeded
  end-to-end runs (`testing-qa.md` P3-1).

**L7 SERVE (platform API + Prism UI).**
- Contract conformance: live responses validated against `openapi.yaml`
  (writes included post-`platform-api.md` P1-3); the `folded` drift
  becomes a test — contract and code agreeing in both directions.
- Consumer-driven: promote `platform/ui/tests/conformance.py` into
  shared `platform/contracts/consumer/`; server CI replays UI-owned
  expectations (flexible matchers) — the right-sized Pact
  (`platform-api.md` P3-9; `research/testing-at-scale.md` Q2).
- SSE operational contract: 200 concurrent streams — p95 of
  `GET /api/decisions` within C2 baseline; stream #65 ⇒ 503 +
  `Retry-After`; gap/resume semantics unchanged
  (`platform-api.md` P1-4).
- Idempotency: two POSTs, same `Idempotency-Key` ⇒ one journaled PD
  event (`platform-api.md` P1-5).
- Opaque cursors survive a DB dump/restore cycle; 304 on immutable
  reads (`platform-api.md` P2-6).
- UI: the §9.2 honesty invariants stay and grow — new invariants that
  mock replay never maps to freshness `live` and that
  client-fabricated payloads carry `derivedMark` at the point of
  display (`prism-ui.md` P3); `antislop.py`'s phantom-interactivity
  scan extended to rendered-but-inert controls (the appeal button,
  `prism-ui.md` P1); Playwright critical flows + visual regression on
  the five surfaces × four freshness states + contrast CI
  (`prism-ui.md` P4).

### 5.2 The nightly drift harness (the missing half of contract testing)

Neither PagerDuty nor Jev will verify Sentinel's contracts — the
provider half of consumer-driven contracts is missing by construction
for third-party vendors (`research/testing-at-scale.md` Q2). The
industry-standard replacement, owned by Sentinel: a scheduled job with
the **team's own** PD test key + Jev key (never the operator's —
BYOK reality), hitting read-only/sandbox paths, diffing live response
*shapes* against the checked-in contracts, paging/opening an issue on
shape change within one schedule interval. Recorded responses become
the cassette anchors (secrets scrubbed); false-positive rate tracked —
a harness that cries wolf weekly gets tuned, not ignored
(`testing-qa.md` P2-2; `forwarder-byok.md` P5;
`research/operating-without-vendor-apis.md` §7). This is the only
mechanism that catches the exact failure the hand-written stubs encode
as truth — including the PD `429`-without-`Retry-After` behavior and
the Jev response-shape drift behind the pinned `jev-1.13.0` string.

### 5.3 Threshold-gated load (C2 becomes a practice, not a report)

Three tiers (`research/testing-at-scale.md` Q3; `testing-qa.md` P1-1):
**PR smoke** (1,000/min × 2 min, seeded: e2e p99 ≤ 1.5 s, zero silent
drops, admission-bound behavior) — non-zero exit blocks merge;
**nightly** sustained (1,000/min × 10 min) + spike (10k/min burst);
**weekly soak** (hours: `gate.emitted` growth ≈ 0, event-log p99 within
budget). The C2 numbers (2026-10-04) are the first baseline thresholds.
The anti-theater check: reintroduce the `gate.emitted` leak on a
scratch branch — the soak must go red, or the harness is decoration.
stdlib runner is fine; **thresholds-as-gates** is the non-negotiable
part, not the tool.

### 5.4 The gate that gates the gates

GitHub Actions has been dead with `startup_failure` on every run since
2026-10-02 (verified `gh run list`, 2026-10-05; `testing-qa.md` §2.1):
**every PR since Oct 2 merged with no mechanical verification.** The
suite's 960 tests are real code; the control that makes a suite a gate
— the non-zero-exit block on merge — does not exist. Two tracks:
(a) the **interim mechanical gate this week** — the §4.2#3 box-side
post-merge runner (cron on the 2-CPU box, order-compliant), with "green
artifact attached" as a written merge requirement in the PR template;
(b) the GHA `startup_failure` escalation — which touches the standing
ban and is Aditya's call (§9). Anti-theater: if the runner goes red
and a merge happens anyway, log the violation — don't excuse it.

### 5.5 Suite hygiene (the trust infrastructure)

- **Test-size classification + hermeticity**: classify all 960 by what
  they may touch (small/medium/large, Google Ch. 11); a ~30-line runner
  wrapper fails any *small* test that opens a socket or sleeps;
  per-size time budgets; publish the distribution, target ~80/15/5
  (`testing-qa.md` P1-2). A suite whose runtime nobody measures grows
  until nobody runs it — measure and publish full-suite runtime now
  (the reviewer's run didn't finish in ~2.5 min on the 2-CPU box).
- **Flaky quarantine**: rerun-to-detect; flagged tests move to a
  quarantine manifest with named owner + 7-day SLA; max 1 retry on the
  blocking gate; fix or delete past SLA (`testing-qa.md` P1-3).
- **No escape hatches as the norm**: audit the 5 `time.sleep` files
  first — condition-polling over sleeps.

---

## 6. What changes vs current — ranked

Ranked by (safety impact × irreversibility of getting it wrong) ÷ cost,
per `research/devops.md` §6. Type labels per principal-systems: **Type 1**
= irreversible (core contracts, trust roots, public API shape) — RFC-grade
rigor, months of care; **Type 2** = reversible — decide fast, measure,
roll back if wrong. Each change names what verifies it: a change without
a falsifier is a wish.

### Adjudications (conflicts resolved by grounding in the code)

Before the ranking, the disagreements — stated, not smoothed:

- **A1. The "mock PD server never landed" claim.** Phase-1
  (`research/testing-at-scale.md` Q1/Q2) says the mock PagerDuty server
  never landed. Phase-2 (`testing-qa.md` §1.2) corrects it: a **stateful
  FakePD did land inside `test_durable_forwarder.py`** (990-line battery:
  scripted failure injection, request journal, a real PD dedup state
  machine). `forwarder-byok.md` §2.3 agrees the Phase-1 claim is
  half-outdated. **Adjudication: Phase-2 wins on facts.** The residual
  gap both memos agree on: the fake lives in one test file — promote it
  to shared `tests/doubles/` and add the schema + fault layers
  (R-13). The Phase-1 sentence should be read as "no *shared* mock
  artifact," not "no mock."
- **A2. `/metrics` in four memos.** Receiver R-3, forwarder P2, devops P2,
  platform-api (implied) each propose it. **Adjudication: one shared
  stdlib metrics module**, emitted on the receiver's `/metrics` (hot
  path) with the platform tier contributing read-tier series — single
  owner, consistent label taxonomy. Four divergent implementations
  would be the opposite of observability (R-12).
- **A3. Correlation ID in five memos.** **Adjudication: one ID, born at
  L1 ingress** (the receiver owns it — the one place it can originate);
  correlator, gate, forwarder, platform, UI propagate and reference it
  (R-12).
- **A4. Jev circuit breaker in three places**
  (`research/architecture-patterns.md` §6, `deterministic-gate.md` P7,
  `race-jev-boundary.md` P3). **Adjudication: one breaker**, owned by
  the race/gate boundary, configured above the data path. It subsumes
  the 6–24% partial-degradation gap and the 429-retry-inside-budget
  burn (R-7).
- **A5. `flags.json`: assumed wired (Phase-1) vs unwired (Phase-2).**
  Phase-1's devops research assumed the flags contract was live.
  Phase-2 (`devops-environments.md` §2.1) grep-verified **zero readers
  in `src/`** — re-verified by this synthesis (2026-10-05).
  **Adjudication: Phase-2 wins.** The revision treats this as the
  largest claim-vs-reality gap in the ops story: wire or retract (R-10).
- **A6. CI: repair GHA vs box-side runner.** `testing-qa.md` P0-1
  proposes pursuing the GHA `startup_failure` escalation *and* an
  interim box-side runner. The standing order bans GitHub Actions.
  **Adjudication: the box-side post-merge runner (§4.2#3) is the
  order-compliant mechanism and ships regardless; the GHA repair is
  Aditya's call** (§9) since it revisits a standing order.
- **A7. "No recovery discipline" (repaired `research/ai-ml-production.md`
  G2) vs the C1/R15 ladder.** G2 says Jev "comes back" by implication
  only, with no measured healthy-again transition. The race memo
  (§1.4) shows the *paging* ladder now has exactly that — canary
  probes, held-rate exit, `failopen_recovered` events. **Adjudication:
  both true, different layers.** Paging-ladder recovery: landed. Judge-
  path recovery (graded re-entry of Jev-as-judge, per-provider health
  as a first-class signal, a second provider): missing. The revision
  keeps the distinction sharp (R-6, R-7).
- **A8. The `(Ham` sentence.** Per the repair agent's caveat, the one
  sentence completing the truncation fragment (Hamel Husain's
  judge-validation result) is fresh-research-cited, not original-lane
  text. It carries its own citations (hamel.dev, Datadog, FDE
  checklist) and is used with that provenance noted. The race memo's
  meta-finding recommending re-authoring is **closed** by 2deb22f.

### The ranked list

**R-1. Unify the policy-change path: bind the kernel's numbers to the
attested lifecycle — TYPE 1**
*Source: `deterministic-gate.md` P1 (§2.1: "the damning finding").*
The B3 lifecycle + Ed25519 dual attestation governs a policy object the
decision path never consults — `PolicyStore(` is instantiated only
inside `policy_lifecycle.py` itself and in tests (verified 2026-10-05)
— while `thresholds.json` changes the kernel's actual numbers via an
unattested JSON edit. An operator widening `suppress_p1_max` at 3 AM
faces no attestation, no expiry, no review-due clock. This is
governance theater over the highest-stakes surface in the system, and
theater is worse than absence because the ADR will be cited as the
control. Options: (a) `PolicyVersion.content` becomes the canonical
thresholds (+ allowlist); `ConfigLoader` refuses generations not covered
by a LIVE/REVIEW_DUE attested version — mismatch fails toward paging;
or (b) the honest lighter alternative: an ADR requiring 2-attestor
sign-off on `thresholds.json` via PR with B3 expiry/freeze applying.
Either way: ship the missing operator entry point (a CLI driving
draft→live — manual first, per execution-doctrine), and flip D8's
missing-file semantics from fail-open (`(True,
"policy_gate_not_configured")`) to fail-closed once the store is
initializable — the "watchdog never set up" state must page, not
suppress unsupervised.
*Verifies:* unattested `thresholds.json` mutation ⇒ suppress unreachable
(gate pages); attested LIVE version covering the thresholds ⇒ suppress
reachable; the CLI ceremony executed end-to-end once before the design
partner.

**R-2. One key-resolution contract for every forward path — TYPE 1
(contract), Type 2 (code)**
*Source: `forwarder-byok.md` P1 (§2.1: "the damning one").*
`DurableForwarder._pinned_key` resolves through `pd_sender`
(env-only); `IntegrationStore` (the designed BYOK surface) is consulted
by everything *except* the durable production path. An operator who
configures their key only via the Integrations UI gets `SecretMissing`
⇒ retries ⇒ `dead_letter` — while the UI reports `configured: true`.
The UI's "configured" status is a proxy that rank-orders with nothing
(principal-mindset proxy-trap audit); forced probability 75% this is a
real production failure for any UI-only operator. Single resolver,
documented order (IntegrationStore → `secret:`-mapped env →
`PD_ROUTING_KEY` env → `SecretMissing`), every path uses it; loud
warning when the UI reports configured but the durable path cannot
resolve.
*Verifies:* key set via `IntegrationStore` only (env scrubbed) ⇒
`forward_confirmed` through the durable path; legacy/durable parity
regression test.

**R-3. Authenticate `/v2/enqueue` with the ADR-005 HMAC — TYPE 1**
*Source: `receiver.md` R-1 (M-1: "the single most damning finding").*
The highest-value ingress route is unauthenticated while its sibling
route is HMAC-gated — asymmetric trust postures with no written
rationale. Any host with a TCP connection can inject arbitrary
`critical` triggers or manipulate correlator state via crafted
dedup_keys; the only defense is the default `--bind 127.0.0.1`, a
network-layer hope. Mechanical fix reusing the existing pure-function
check; migration via `SENTINEL_WEBHOOK_ONBOARDING=1` loud-accept during
sender cutover.
*Verifies:* `/v2/enqueue` auth matrix mirroring the generic matrix in
`test_signature_auth.py`; contract doc stating the ingress auth scheme
per route.

**R-4. Wire the three orphaned audit controls — Type 2**
*Source: `event-log-audit.md` P2 (M2: 95% confidence by grep).*
`CheckpointJob` (the hourly seal — the *only* external anchor of the
chain), `Reaper.startup_sweep`/`run_periodic` (the Pair-B crash-window
closer), and `RetentionJob` (the tier state machine) exist, are
unit-tested, and are **never invoked in production**. The Pair-B
guarantee, the hourly seal, and the retention policy are prose.
Highest value-per-line-change in the domain.
*Verifies:* kill-mid-decision ⇒ reaper redrives the orphan as fail-open
page; failing sink ⇒ page after the bound; retention dry-run ⇒ plan
with zero deletes; runbook entries + a drill per control.

**R-5. Durable correlator baseline with staleness-aware restore —
Type-1-ish**
*Source: `correlator.md` P1 (M1: "restart amnesia").*
The flagship C1 achievement — the self-calibrating storm threshold —
is volatile process memory. Every restart replays the R15 30-minute
blind window, during which a busy estate falls back to floor F=20 and
folds ~98% of traffic: the exact C2 cliff the PR was built to fix. A
baseline learned over 24h that dies on SIGKILL is a cache with
delusions of grandeur. Persist `(B, rings, attested baseline, storm_k)`
on a slow cadence + clean shutdown, signed with the checkpoint HMAC
discipline; restore with a staleness policy (accept < 24h, decay toward
floor beyond, always re-enter the R15 discrimination from a warm start).
*Verifies:* kill -9 mid-burst on the seeded C2 harness ⇒ declaration
behavior matches pre-kill within 5 min, first-10-min fold rate within
2× of pre-kill (not ~98%); tampered snapshot ⇒ loud reject +
R15-from-zero.

**R-6. Degraded-judge tier (rung 1) — TYPE 1**
*Source: `race-jev-boundary.md` P1; `research/ai-ml-production.md` G3
("the biggest gap").*
The ladder degrades *paging* but the product's value (suppression)
goes to zero the moment TypeSafe is sick — exactly when storms make
suppression most valuable. Aditya's thesis ("Jev is the FIRST engine,
never the ONLY engine") is contradicted by the architecture. Two
lanes in build order: (a) alternate pipe for the same pinned model
(Vercel AI Gateway / Cloudflare Workers AI / OpenRouter — same wire
shape, drift assertion unchanged), routed on `JevOverloaded`/sustained
timer-wins; (b) degraded engine (Laya-class open-weights) behind the
same `decide()` interface, labeled `engine=degraded`, **never allowed
to suppress alone** (corroborate a deterministic rule only —
ai-ml checklist #4/#5). Plus the exact-cache on `input_sha256` (the
cheapest rung — identical states never re-bill).
*Verifies:* 100% 529s on primary for 30 min against the Oct-4 replay ⇒
suppression ≥ 70% of healthy-mode on the golden set; zero suppress
dispositions with `engine=degraded` uncorroborated; failover
event-logged with per-pipe health. Promotion gated on R-17's golden
set ("test the fallback with the same eval suite or you shipped
fallback-theater" — `research/ai-ml-production.md` Q3).

**R-7. Circuit breaker on the Jev dependency — Type 2**
*Source: `race-jev-boundary.md` P3; `deterministic-gate.md` P7;
`research/architecture-patterns.md` §6 (finding 3). Adjudication A4.*
The budget race treats every call as independent — no outlier
detection, no breaker, no half-open probing. Sustained latency drift
trips nothing; the 6–24% timer-win band is page-and-hope; honoring
`Retry-After` by sleeping inside a race that cannot win burns
guaranteed-loser billable calls. The breaker: opens on N consecutive
budget-busts or drift breach; while open, fail-open with zero vendor
calls; half-open via the existing `probe_vendor` shape; close on
sustained healthy probes; states event-logged like the C1 steps; the
C1 ladder's entry criteria re-expressed in breaker states (no two
independent degradation state machines disagreeing).
*Verifies:* fault-injection latency ramp ⇒ breaker opens before pool
occupancy 80%; billable calls stop within one detection window;
half-open recovery closes within 5 min of vendor recovery.

**R-8. Cost ledger + cost circuit breaker — Type 2**
*Source: `race-jev-boundary.md` P2; `research/ai-ml-production.md`
Q4/G6.*
`input_tokens` is parsed from every response and aggregated nowhere in
production. A storm × retry policy is a blank check the operator sees
only on the vendor invoice — the one failure mode with no sensor.
Aggregate per-org spend (`$/decision`, `$/min` burn) into the watchdog
vocabulary: warn/throttle/stop thresholds (throttle → cheaper path →
cached only); cost events join the event log so the invoice is
reconcilable. LLM cost is per-request variable — it belongs with
latency and availability as an SLO, not as a monthly surprise
(`research/ai-ml-production.md` Q4).
*Verifies:* synthetic 529-storm replay ⇒ throttle trips before spend
exceeds 2× the healthy-hour baseline; ledger reconciles to a live
week's vendor invoice within 1%.

**R-9. Machine-check the disposition vocabulary — TYPE 1 (vocabulary)**
*Source: `deterministic-gate.md` P2 (§2.4).*
`Disposition.action`/`reason` are bare strings — ~150 literals decide
whether a human is paged; `models.py` documents 8 of 20+ reasons; the
taxonomy is pinned by tests but versioned nowhere (contrast the
Google/Azure/Stripe versioning consensus and RFC 9745/8594). The
attestor ADR already taught this organization the bare-strings lesson;
it re-appears where the stakes are highest. Enums (`Action`,
`ReasonCode`) with structured variants; explicit versioned wire
serialization (history never rewritten); exhaustiveness test owned by
the compiler, not a comment.
*Verifies:* `mypy --strict` on the gate module with zero `str`-typed
disposition values; exhaustiveness test green; grep shows zero bare
`"suppress"`/`"page_now"` literals outside the enum + serializer.

**R-10. Flags: wire into the live gate or retract the `<5s` claim —
TYPE 1 (the honesty decision)**
*Source: `devops-environments.md` P1 (§2.1). Adjudication A5.*
Every release-safety claim (flag-off, kill-switch drill,
canary-by-flag, "no emergency code rollbacks") is load-bearing on a
mechanism with zero readers in `src/`. Two acceptable outcomes —
(a) wire it (schema + loader + gate semantics are settled in
`ops/devops-foundation.md` §2.1; the wiring is mechanical), or
(b) retract the `<5s` row, the drill, and the "no emergency code
rollbacks" claim until it is real. (a) strongly preferred.
*Verifies:* on `staging-lab`, `flagctl set global_kill_switch true` ⇒
synthetic alert round-trips as `passthrough` within seconds; monthly
drill runs against this real path with its record in `ops/drills/`.

**R-11. Post-merge mechanical gate — Type 2**
*Source: `testing-qa.md` P0-1 (§2.1); `devops-environments.md` P5.*
CI dead since Oct 2 (`startup_failure` streak); every PR since merged
on the honor system; the PR template's "full suite green" is a
checkbox. The §4.2#3 box-side runner (cron on clean worktree,
per-commit verdicts) is the order-compliant interim gate —
Adjudication A6.
*Verifies:* 7 consecutive days of per-SHA verdicts with zero
skipped/failed gates; a scratch PR with a deliberately failing test is
blocked, visibly, on the record.

**R-12. Observe as a stage: `/metrics` + trace ID + first SLO —
Type 2 (SLO values tunable; the decision to have one is Type-1-ish)**
*Source: `devops-environments.md` P2/P4/P7; `receiver.md` R-3;
`forwarder-byok.md` P2. Adjudications A2/A3.*
The research's highest-leverage gap; three unlinkable log streams for
a missed page; no release-policy governor. One shared metrics module,
one ingress-born UUID, one availability SLO with error-budget
arithmetic (43.2 min/30d at 99.9%).
*Verifies:* `curl :8080/metrics` returns valid Prometheus exposition;
one synthetic alert ⇒ `grep <uuid>` returns the complete
ingest→decision→forward chain; the SLO doc exists and the cutover
checklist references it.

**R-13. Vendor-boundary honesty: drift harness + shared FakePD +
cassettes — Type 2**
*Source: `testing-qa.md` P2-1/P2-2; `forwarder-byok.md` P4/P5;
`research/operating-without-vendor-apis.md` §7. Adjudication A1.*
Every hand-written stub is a hypothesis about reality; today the
re-validation answer is "none." Promote `FakePD` to `tests/doubles/`
(+ schema/fault layers + routing-key-scoped dedup + ack machine);
build the stdlib Jev cassette recorder (record-once, replay with
`none` semantics, secrets scrubbed); run the scheduled drift harness
against the team's own keys (Aditya decision, §9).
*Verifies:* synthetic contract change trips the harness within one
interval; a scratch-branch Jev shape drift fails cassette replay; a
PD-docs behavior with no corresponding FakePD test is the coverage
gate (the checklist itself).

**R-14. Correlator eval harness (frozen estate traces) — Type-1-ish**
*Source: `correlator.md` P3 (M2: "RFC-reasoned, never measured").*
The detector's soul is a stack of tuned constants (`k=5`, `F=20`, 6h
half-life, 60s tick, 300s freeze, 30-min bootstrap, 72h freshness…)
each with an RFC argument and **none with a measurement**. The true
objective ("declare on real incident storms, never on busy-estate
velocity") vs the proxy ("the reasoning sounds right") — a proxy that
moves while the truth is unmeasured is tuning theater
(principal-mindset). Frozen corpus, declare precision/recall +
time-to-declare, tiers smoke/regression/hard; the harness also
produces the 30 days of R samples the `k = P99.9(R)+1.5` rule needs.
*Verifies:* the harness scores the *current* constants first
(publishing the baseline — if k=5 loses to k=4, the RFC was wrong and
the number changes with evidence); k-retune PRs show harness deltas;
hard-tier recall regression blocks merge.

**R-15. Threshold-gated load harness — Type 2**
*Source: `testing-qa.md` P1-1 (§2.3); `research/testing-at-scale.md`
Q3.*
C2 was a measurement, not a practice — and the two defects it found by
hand (the `gate.emitted` leak, event-log lock contention) are exactly
what soak tests catch mechanically. Promote
`research/bin/load_c2_harness.py` into `tests/load/`; PR smoke +
nightly sustained/spike + weekly soak, all threshold-gated (non-zero
exit). C2 numbers become the first thresholds, not a report.
*Verifies:* reintroduce the `gate.emitted` leak on a scratch branch ⇒
the weekly soak goes red. A gate that can't catch the one leak we
*know* existed is theater.

**R-16. Platform API: authenticate the write surface + unify
versioning — TYPE 1**
*Source: `platform-api.md` P0-1/P0-2 (M1/M4/M5: CRITICAL).*
Zero auth on the secret-handling surface (key save/delete, simulated
toggle, real test pages) — anyone with network access can rotate the
PagerDuty key and pages go nowhere during a SEV. Mixed versioning
(unversioned reads + `/api/v1` writes) with no migration path for the
independently-deployed Pages console. Bearer token on the five write
endpoints (minimal Type-1-adequate mechanism; OAuth/OIDC rejected as
overkill for a single-operator tier); everything canonical under
`/api/v1` with old paths as `Deprecation`+`Sunset` aliases; the
deprecation policy written into `platform/contracts/README.md`.
*Verifies:* 401 matrix on all five write endpoints; old paths assert
`Deprecation`+`Sunset` headers; CI fails any new endpoint without a
version prefix.

**R-17. Judge eval: golden set + κ calibration + disentangled
flip/latency — Type 2**
*Source: `race-jev-boundary.md` P5; `research/ai-ml-production.md`
Q2/G4/G5.*
The current eval proves the gate's plumbing with scripted answers;
the judge, the degradation ladder, and the shadow evidence have no
honest measurement. Frozen hash-pinned golden set (Oct-4 A/B traces +
production shadow captures); Jev-as-judge calibrated to human-oncall
judgment at κ ≥ 0.75; timer-win decomposition standing in every eval;
ladder degradation priced (coverage @ step 0/1/2/3 in the calibration
report); shadow-before-promotion for judge/budget/fallback changes;
rotation discipline so the set doesn't become a training set in
disguise.
*Verifies:* golden set committed and CI-gated on prompt/model/pin
changes; a red-team label-edit run is rejected by the gate; the
degradation-price table exists in the standing report.

**R-18. Event-log lifecycle: threat-model doc + genesis unification +
drain-before-roll — P1 is TYPE 1**
*Source: `event-log-audit.md` P1/P3/P4 (M1/M3/M4).*
Three defects: (a) "tamper-evident" is external-attacker-only —
writer forgery/silence unaddressed, the 2-hour bound is restartable
memory (Type-1 requirement doc per §3.4); (b) the two verifiers
disagree on the genesis rule — reproduced, and it *will* cry wolf on
every deployment the day after the first roll (the anti-theater decay:
a verifier that cries wolf trains everyone to ignore it); (c) the roll
entombs live outbox rows, silently breaking I1/I2 at every segment
boundary — the chain stays continuous while the guarantees it exists
to support are dropped (Chesterton's fence in reverse).
*Verifies:* the `/tmp/evlog-repro` reproduction becomes a regression
test both verifiers pass; queued page survives the roll (or the roll
refuses and pages); the adversary×detection×time-bound table is
reviewed by the security principal.

**R-19. Ingress hardening: slowloris + caps + replay cache +
exception-path fix — Type 2**
*Source: `receiver.md` R-2/R-5/R-8 (M-2/M-3/M-7/M-9).*
The 64-slot semaphore is defeated by slowness (no socket/read
timeout — a blocking `rfile.read` up to 8 MiB with no deadline);
the PD-mirror route accepts 16× the vendor's own 512 KiB cap; the
5-minute replay window is acknowledged and unmitigated; malformed JSON
takes the panic path so the metrics lie about what happened
(`handler_panics` vs `unparseable`).
*Verifies:* slow-drip test (64 trickling connections — legit traffic
still 200s); 600 KiB PD body ⇒ 413; sign-and-double-POST ⇒ second
refused with a distinct reason code; malformed JSON ⇒ `unparseable`
increments, `handler_panics` does not.

**R-20. UI: appeal control + constitution RFC + mock honesty —
P0 is TYPE 1 (Aditya, §9)**
*Source: `prism-ui.md` P0/P1/P3 (A4: "the most operationally dangerous
finding in this review").*
"Page me anyway" renders on every drawer and can never page — in the
production build. An operator at 3 AM may believe an appeal acted when
nothing happened. Wire it to a real appeal endpoint (audit-logged
override → real paging path) or remove it; the "Demo build" note goes
either way. And the constitution mandates a React/TS stack the shipped
vanilla UI never adopted, with two competing token taxonomies — a law
nobody follows teaches that laws are theater. Mock replay must never
map to freshness `live`; mock-fabricated payloads must carry the
reconstruction mark at the point of display.
*Verifies:* in the `live` build, appeal either pages through the
audited path (asserted in CI against a mock pager) or doesn't render
(`antislop.py` phantom scan extended); the reconcile-or-migrate RFC
lands with written alternatives and a principal sign-off.

---

## 7. What stays — genuine strengths to preserve

The revision is not a rewrite. These survive disagree-and-commit, with
the reason each is load-bearing:

1. **The fail-open direction.** Every error, timeout, malformed answer,
   missing monitor, stale proof, drifted model, and unreadable policy
   file falls toward the human (`deterministic-gate.md` §1.5). It is
   Envoy's panic mode, named and deliberate — and the one property of a
   paging middleware that matters more than all others. The revision
   quantifies the path to panic (R-7); it never reverses the direction.
2. **The Simplex boundary shape.** Jev as the advanced controller, the
   paging path as the verified baseline, the race as the decision
   module with a *temporal* switch — and the correlated-guardrail
   critique does not bite: the timer, the ADR-020 instruction firewall,
   and the freshness proofs are deterministic code, not a second model
   (`research/ai-ml-production.md` Q1/G1; `race-jev-boundary.md` §1.4).
   A citable, defensible strength. The revision writes the warrant
   down (R-18's threat-model doc covers the boundary contract); it
   doesn't redesign the switch.
3. **The honesty machinery.** The never-5xx contract with last-resort
   raw forwarding; onboarding mode's loudness (metric + health flag +
   CRITICAL warning, not a quiet env var); the resolve direction's
   fail-closed design; honest `/healthz` predicates that state what is
   *not* measured; the DATA_MODE build-fixed shim; the §9.2 honesty
   invariants as automated gates; `migrate.py`'s refusal to synthesize
   `forward_confirmed` ("synthesizing a receipt for history we did not
   witness would be the exact lie this design exists to kill").
   The revision extends this machinery (R-6's simulated-spill fix,
   R-20's mock-honesty invariants); it never weakens it.
4. **Checked-in vendor contracts.** `PD_RETRY_TABLE` with its
   row-for-row classifier test ("a PD docs change becomes a build
   break, not a 3 AM discovery"); the freshness manifest's two-point
   validation discipline; `PD_RETRY_TABLE`'s "verified live" comment.
   The revision gives these a refresh mechanism (R-13's drift harness);
   it doesn't replace the discipline.
5. **The attestor ADR's intent.** Dual attestation, content-hash
   binding, the 2-person ceremony, quarantine-on-revoke, the panel
   process with recorded disagreement — genuine Type-1 governance work
   (`deterministic-gate.md` §1.4). R-1 exists precisely because the
   intent is right and the wiring is missing: the fix connects the
   governance to the field, it doesn't relitigate the ADR.
6. **Event sourcing done right.** The hash-chained log is audit-only:
   never the source of rebuildable runtime state, no replay-on-boot,
   no projections — "audit capability without the replay liabilities"
   (`research/architecture-patterns.md` §1.3). I1/I2/I3 as database
   properties, the single writer, the commit watchdog, the test-enforced
   append-only grep guard. The revision wires the orphaned controls
   and fixes the lifecycle (R-4, R-18); the chain construction itself
   is sound and untouched.
7. **The staging classification and shadow-first evidence.**
   Four tiers with the never-pages rule as Type-1 law; the shadow tap's
   full-fidelity copy; ADR-022's canary-as-shadow-diff (the canary
   *mechanism* — real-traffic evidence before promotion — without
   traffic-splitting machinery). The revision automates the sign-off
   computation (§4.2#8); the doctrine stands.
8. **The receiver's auth-code standard.** The pure-function signature
   check shared by two call sites, constant-time compare, terse reason
   codes, no secrets in logs (`receiver.md` appendix). R-1/R-3 raise the
   *rest* of the receiver to the standard its auth code already sets.
9. **The no-exactly-once honesty contract.** The forwarder claims
   at-least-once and explicitly disclaims exactly-once; the lease-based
   crash recovery; the secondary-never-cancels-primary invariant; the
   keyless-on-disk payload decision; the single wired `sanitize_error`
   boundary (`forwarder-byok.md` §1.4). Enterprise-grade. The revision
   adds to it (R-2, R-13); it doesn't restate it.
10. **The correlator's honesty density.** The I-1 circularity trap, the
    R15 refusal case, the "unverified external assumption" labels, the
    admission that D7's staleness machinery is built-but-unwired — all
    written *in the code* (`correlator.md` reviewer's notes). The
    findings against the correlator are architecture and verification
    gaps, not craft gaps. Preserve the pre-mortem discipline that
    produced this code.

---

## 8. Sequenced build plan

Dependencies are real: no phase starts until the previous phase's
verifications are green. A phase whose core verification cannot be
built is re-scoped in writing, never stretched (principal-mindset:
monkey-first + dated kill conditions). Entry criterion for the whole
program: **this document approved by Aditya.**

### Phase 0 — Honesty triage (week 1)

*Closes the claim-vs-reality gaps before any new capability.*
- Retract-or-wire decisions executed: flags `<5s` row (R-10 —
  either wire or retract in the doc, no third option);
  `docs/deploy-production.md` §8 rewritten to mandate `deploy.sh`
  (§4.2#5); GHA status noted honestly in the PR template (R-11).
- `ops/incidents/` created + postmortem template landed (§4.2#11).
- Page-vs-ticket classification table published (§4.2#9).
- Receiver R-8 (exception-path fix) — two lines, metric honesty.
- Simulated-spill audit fix (`forwarder-byok.md` P6) — the log must
  never record a non-failure as a failure.
- *Exit:* grep-verified zero doc claims of unwired capabilities;
  `ops/incidents/` exists; malformed JSON increments `unparseable`.

### Phase 1 — Trust boundaries (weeks 2–3)

- R-2 (one key-resolution contract) — E2E BYOK test green.
- R-3 (`/v2/enqueue` HMAC) — auth matrix green; onboarding cutover
  documented. **Type-1: sender migration cost is real; Aditya's §9
  call if the cutover needs a longer window.**
- R-16 (platform write-surface auth + versioning unification).
- R-1 (policy-path unification) — the CLI ceremony executed once
  end-to-end; D8 missing-file semantics flipped to fail-closed.
- R-19 (ingress hardening: timeouts, 512 KiB cap, replay cache).
- *Exit:* UI-only key ⇒ `forward_confirmed`; `/v2/enqueue`
  unauthenticated ⇒ 403; write surface 401 matrix green; unattested
  thresholds mutation ⇒ suppress unreachable.

### Phase 2 — Observability backbone (weeks 3–4)

- R-12 (`/metrics`, trace ID, first SLO + error budget).
- R-11 (post-merge runner — the interim mechanical gate).
- Burn-rate alerts on the watcher (§4.2#10); `record_request()` wired
  (§4.2#14); parity/drift audit script (§4.2#12).
- *Exit:* `/metrics` scraped; one UUID greps the full chain; 7 days
  of per-SHA verdicts; the SLO doc governs the cutover checklist.

### Phase 3 — Resilience mechanisms (weeks 4–6)

- R-5 (durable correlator baseline) — kill -9 experiment green.
- R-7 (Jev circuit breaker) — fault-injection experiment green.
- R-8 (cost ledger + breaker) — storm-replay experiment green.
- R-6 (rung 1) **built** here, **promotion-gated** on Phase 4:
  the degraded tier runs in shadow on the golden set until R-17's
  harness exists to verify it ("no fallback theater").
- B-loop: implement the tuner-side re-derivation or delete the
  docstring claim — no third option (`race-jev-boundary.md` P6).
- *Exit:* breaker/cost/baseline experiments green; rung 1 shadow
  evidence accumulating; B is self-calibrating or honestly manual.

### Phase 4 — Verification machinery (weeks 6–8)

- R-14 (correlator eval harness) — scores current constants first.
- R-17 (judge golden set + κ calibration + ladder pricing).
- R-13 (drift harness + shared FakePD + cassettes).
- R-15 (threshold-gated load: PR smoke + nightly + weekly soak).
- Contract conformance (platform read+write; receiver ingress
  schemas); test-size classification + hermeticity; flaky quarantine
  policy live.
- R-6 promotion: rung 1 clears the golden set ⇒ serves.
- *Exit:* every R-14–R-17 verification green; the soak catches the
  reintroduced leak; a synthetic vendor drift trips the harness.

### Phase 5 — Lifecycle & completion (weeks 8–10)

- R-18 (threat-model doc, genesis unification, drain-before-roll,
  checkpoint chaining, `GET /api/v1/audit/chain`, archive
  completeness, key lifecycle + GDPR path wired or honestly deleted).
- R-4's remaining wiring (retention job armed after dry-run review).
- Key rotation protocol (`forwarder-byok.md` P3); per-service routing
  map only if Aditya approves the scope (§9).
- UI: reconcile-or-migrate RFC first (§9), then appeal control,
  mock-honesty invariants, token reconciliation, Playwright/visual/
  contrast gates, CSP + trace-ID.
- R-9 (disposition enums) — sequenced late deliberately: it touches
  every layer's vocabulary and is safest after the contracts (C4) and
  harnesses exist to catch the migration.
- *Exit:* the explicit-layers checklist
  (`research/architecture-patterns.md` §5, 12 items) re-scored —
  target: no item still "missing"; verifier agreement, sealed-chain
  serving, and retention all demonstrated in drills.

### Deliberately NOT built

- Kubernetes / Argo CD / Flux (mechanisms ported, machinery doesn't).
- LaunchDarkly or any paid flag service.
- k6 / Locust / JMeter adoption (stdlib harness; thresholds-as-gates
  is the requirement, not the tool).
- Full Pact broker / `can-i-deploy` (right-sized: OpenAPI conformance
  + consumer-owned expectations + drift harness).
- Stripe-style date-pinned API transformation layer (machinery theater
  at this scale; revisit with a second external consumer).
- OAuth/OIDC for the platform tier; mTLS for senders (revisit if the
  threat model grows to mutual distrust).
- Ed25519 for checkpoints (doesn't fix writer-holds-the-key;
  `event-log-audit.md` appendix — refinement theater).
- Kafka / message bus between pipeline stages (the ripped-out-EDA
  test stands).
- GraphQL / BFF split (the contract and auth are the fix, not the
  rename).
- Production chaos against live PagerDuty (Type-1 blast radius;
  Aditya's call, §9).
- Preview environments per PR (deprioritized until contributor load).
- Blue-green receiver (deferred conditional, §4.2#16).

---

## 9. Open questions for Aditya — Type-1 decisions only

These cannot be decided by grounding in the code or research; each is
irreversible enough (contract, trust root, public surface, standing
order) to need his word. Everything else in this document the program
can decide and build under founder-deputy authority.

1. **UI stack: reconcile or migrate?** (`prism-ui.md` P0) The
   constitution mandates React 18 + TypeScript strict (F1–F10) and a
   semantic token taxonomy; the shipped UI is vanilla JS with a
   competing token doc, and no RFC ever reconciled them. (a) Amend
   the constitution to bless the vanilla stack with written rationale
   (zero-build deployability, 284 KB total, no toolchain to rot), or
   (b) migrate Prism to the F1–F10 stack. Either is defensible; the
   middle state teaches that laws are theater.
2. **`/v2/enqueue` auth migration.** (`receiver.md` R-1) HMAC
   (recommended — reuses tested ADR-005) vs mTLS vs accepting the risk
   with network policy. The sender cutover cost is real either way;
   if senders need a longer window than the onboarding-mode cutover,
   that's his call.
3. **Policy-path unification approach.** (`deterministic-gate.md` P1)
   (a) `PolicyVersion.content` becomes the canonical thresholds.json
   (kernel refuses unattested generations) vs (b) 2-attestor PR rule
   with B3 expiry/freeze applied to thresholds.json. Both close the
   theater; (a) is stronger, (b) is cheaper.
4. **Per-service routing map: product scope or gap?**
   (`forwarder-byok.md` P7) A single hardcoded `routing_key_ref`
   makes multi-team paging impossible without forking config. Build
   the versioned service→ref map, or declare single-key a product
   boundary?
5. **GitHub Actions: repair or retire?** (`testing-qa.md` P0-1) The
   workflows are dead (`startup_failure` since Oct 2); the standing
   order bans GHA. Repair the escalation (revisiting the ban) or bless
   the box-side post-merge runner as the permanent gate?
6. **Provider-contract suite keys.** (`forwarder-byok.md` P5,
   `testing-qa.md` P2-2) The drift harness needs a real PD test key +
   Jev key held by the Sentinel team (never the operator's), with
   read-only probes + one trigger/resolve cycle on a sacrificial
   service. Authorized to provision and hold?
7. **Production chaos against live PagerDuty: ever?**
   (`testing-qa.md` appendix) Explicitly not now (no sandbox exists;
   irreversible blast radius). Is it "never," or "after the
   fault-proxy harness earns trust"?
8. **The appeal control's future.** (`prism-ui.md` P1) If "page me
   anyway" is wired rather than removed, it becomes a new write action
   on the platform API (audit-logged override → real paging path).
   Approve the new surface, or prefer removal?

---

## Sources & traceability

Every section above cites the memo or research file behind each claim;
the citation is the trace. Provenance notes:

- Phase-1 research: `research/swe-discipline.md` (Stream 1),
  `research/architecture-patterns.md` (Stream 2),
  `research/devops.md` (Stream 3), `research/ai-ml-production.md`
  (Stream 4 — repair 2deb22f; the single Hamel Husain sentence per the
  repair caveat is fresh-research-cited),
  `research/testing-at-scale.md` (Stream 5),
  `research/operating-without-vendor-apis.md` (Stream 6).
- Phase-2 memos: `docs/architecture-revision/{receiver,correlator,
  race-jev-boundary,deterministic-gate,forwarder-byok,event-log-audit,
  platform-api,prism-ui,devops-environments,testing-qa}.md`.
- Skills: `~/workspace/skills/{principal-systems,principal-governance,
  principal-mindset,execution-doctrine}/SKILL.md`.
- Code verifications performed by this synthesis (2026-10-05, branch
  `program/architecture-revision` @ 2deb22f): `flags.json` /
  `global_kill_switch` — zero hits in `src/`; `/metrics` route —
  absent; `trace_id`/`correlation_id`/`request_id` — absent in `src/`
  and `platform/server`; `PolicyStore(` — instantiated only inside
  `policy_lifecycle.py` itself and in tests; `record_request(` — zero
  call sites outside its definition. These confirm the devops and gate
  memos' grep claims.

*Pre-mortem for this document (principal-systems): it is Oct 2026 and
this revision failed. The three most likely causes: (1) the ranked list
was treated as a backlog and built top-to-bottom without the phase
gates — sequencing is the proposal, and Phase 0's honesty triage was
skipped as "not real work"; (2) Aditya's §9 questions went unanswered
while building continued, so Type-1 decisions were made by default
instead of by choice; (3) the eval harnesses (R-14, R-17) were
deprioritized as "later," and the resilience mechanisms shipped
without the measurement that makes them more than theater. The
mitigation is written into §8: no phase starts with the previous
phase's verifications red.*
