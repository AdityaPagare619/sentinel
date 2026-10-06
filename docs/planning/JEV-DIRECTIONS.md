# T5 — Jev Multi-Direction Expansion: Design Doc

**Track:** 5 (Jev advisory directions) · **Contract:** C5 · **Phase:** 1 — design only, no code
**Author lane:** `lane/build-t5-jev-multi` · **Status:** awaiting coordinator review (code blocked until approved)

## 0. The iron rule (non-negotiable, per direction)

> **Jev advises and enriches; it never decides, never pages, never suppresses.**

Every direction in this doc satisfies all of the following:

1. It executes **after the disposition is final** (at/after event-write time, the same
   temporal boundary the D9 counterfactual receipt uses). There is no code path by which
   a direction's output can reach the gate kernel before or during a decision.
2. Its outputs are **enrichment attached to records** — advisory payloads on the gate's
   emission channel (the `storm_root_cause_payload` precedent), never fields the gate
   reads. The gate kernel's read-set is unchanged by this track.
3. It has **no write access to the paging path, ever**: no imports from the gate's
   decision path, no writes to the policy store, forwarder, audit decision fields, or
   operator token. Its only network access is the Jev API via Track 2's client.
4. Every output carries honest labeling (`ai_generated`, `direction`, `fallback`) for
   Track 8's UI contract, and every direction has a deterministic fallback that fires
   on timeout/shed/budget-exhaustion (Track 7's falsifier surface).

The control question per direction — *"does the deterministic gate still own the
decision?"* — is answered **yes, structurally**: the advisory dispatcher consumes
*emitted* payloads (read-only) and can neither observe nor mutate in-flight decisions.

## 1. Foundational constraint: Jev is typed, not generative

Jev is a System One *decision* model (`POST api.typesafe.ai/v1/systemone`): one call
takes `state` + typed questions — **Choice** (pick from ≤255 options), **Score** (2–10
ordered levels), **Noul** (yes/no as probability) — and returns typed answers with
probabilities. **It writes no text**, does no math, holds no memory across calls
(JEV research report; vendor docs).

Consequences for the six candidate directions:

- "One paragraph" (directions 1, 2) **cannot be Jev-generated prose**. Redesign:
  Jev answers *typed* questions (which evidence leg to emphasize; which cluster leads
  the storm); **deterministic templates render the paragraph** from Jev's typed
  answers + the already-recorded evidence. The AI contribution is selection/ranking;
  the words are code. This is *more* honest than prose: templates cannot hallucinate
  facts, and every sentence is traceable to a recorded field.
- Counting/arithmetic (direction 5's "X of last week's pages") is **deterministic** —
  Jev cannot do math. Jev's role there is ranking *which* changed cases a human
  should review first (Choice over a code-proposed candidate set: "code proposes,
  Jev disposes").
- Confidence is **ordinal, never probability**: Score answers are presented as
  "plausibility level L/10 (ordinal)"; Choice confidences are never shown as
  percentages to operators (Jev flips 1.3–2.2%; confidence is rank signal only).

## 2. Shared infrastructure (all directions)

### 2.1 AdvisoryDispatcher — one submission path

New module `src/sentinel/advisory.py` (Phase 2). All six directions submit through it;
no direction calls the Jev client directly.

```python
AdvisoryDispatcher(decide_fn, spend_meter, config)
# decide_fn   — Track 2's Jev call entry (injected; never the raw key)
# spend_meter — Track 2's spend surface: {session_usd, budget_usd, calls, blocked}
# config      — per-direction {enabled, timeout_ms, cost_cap_usd, daily_cap_usd}
```

- **Pool isolation (control-relevant):** the dispatcher owns a *separate* bounded
  `AdvisoryPool` (4 threads, queue 32, shed-first). Advisory calls **never** borrow
  the race's `InferencePool` — a storm of advisory work cannot delay a page by even
  one scheduling quantum. Shed ⇒ deterministic fallback, logged at warning.
- **Never raises.** Every direction entry point catches all exceptions; a failure
  drops an enrichment, never a page, never a suppression (the `submit_advisory`
  precedent in `race.py`).
- **Pre-call gates (all must pass):** direction enabled in config → `spend_meter.blocked`
  is false → direction daily cap not hit → estimated input tokens ≤ direction cost cap
  (state serialization is capped *before* the call is built; oversize ⇒ fallback).
  Any gate fails ⇒ deterministic fallback, honestly labeled.
- **Test seam:** injecting `decide_fn`/`spend_meter` lets Track 7 script FakeJev,
  forced timeouts, and exhausted budgets without touching Track 2.

### 2.2 Track 2 integration (consume, don't duplicate)

- Key resolution: Track 2's `resolve_jev_key()` (user store → `TYPESAFE_API_KEY`).
  The key **never leaves the server process** — never in logs, responses, SSE, or
  the browser (C2). Track 5 never sees key material; it receives `decide_fn`.
- Spend: every advisory call is charged to Track 2's meter (`session_usd`, `calls`).
  `blocked=true` ⇒ **all six directions degrade to deterministic fallbacks
  immediately** (no Jev calls). Absent key ⇒ FakeJev, honestly labeled —
  which doubles as Track 7's fallback-test harness.
- Pinned model: advisory calls use the same pinned versioned model id as the race
  (ADR-015/D2); the model id is recorded on every enrichment (`jev_model`).

### 2.3 Labeling contract (for Track 8)

Every enrichment envelope carries:

```json
{
  "type": "advisory/<direction>",
  "advisory": true,
  "ai_generated": true,
  "direction": "suppression_explainer",
  "fallback": "jev" | "deterministic" | "absent",
  "jev_model": "jev-1.13.0",
  "direction_version": 1
}
```

`fallback: "deterministic"` means the content was template-rendered without Jev;
`"absent"` means nothing was attached (honest absence). Track 8 renders the
AI marker from these fields; **no advisory content may be displayed as fact or as
a decision**.

### 2.4 Attachment points

Advisory payloads travel on the gate's **emission channel** as advisory event types
(the `storm_root_cause_payload` precedent in `storm_digest.py`), attached to the
already-emitted `decision_made` / storm aggregate payloads by `alert_id` /
`episode_id`. They are **not** durable EventLog event types (the event-log lane owns
that schema) and they are never written into audit decision fields.

## 3. The six directions

Each spec: `{name, input, output, control_analysis, cost_per_call_usd, timeout_ms, fallback}`.

### D1 — suppression_explainer ("why you weren't woken")

- **input:** the finalized suppression record: `Disposition(action="suppress",
  reason, …)` + the recorded evidence legs (threshold values, allowlist hit,
  freshness report, corroboration floor) + alert fields. Read-only, post-decision.
- **Jev call (typed):** `Choice dominant_leg` over the reason-specific leg set
  (≤8 options, e.g. `allowlist_hit`, `known_noise_pattern`, `corroboration_floor`,
  `freshness_veto_absent`…; each option carries a one-line description; mandatory
  `cannot_determine`) — *"which recorded evidence leg would a human on-call find
  least obvious?"* — plus `Noul worth_second_look` — *"does anything in the alert
  context look inconsistent with this suppression?"*
- **output:** one paragraph, **deterministic-template-rendered** from
  (reason, legs, Jev's chosen emphasis leg, alert fields), attached to the
  suppression record. If `worth_second_look=yes` (ordinal high), a
  `human_second_look` tag is attached — routed to the **business-hours review
  digest**, never to the paging path.
- **control_analysis:** Computed at event-write time, after the disposition is
  final (D9 pattern). The gate never reads the paragraph, the emphasis choice, or
  the tag. It can NEVER: unmake or alter a suppression, page anyone, delay a page,
  or feed a future decision (outputs are not policy inputs; there is no learning
  loop from advisory outputs back into thresholds). The `human_second_look` tag is
  consumed only by the digest surface, which has no path to the forwarder.
- **cost_per_call_usd:** ~$0.00008 (state ≈ 1.5–2.5K input tokens @ $0.042/M, output
  free). Hard per-call cap $0.0005 (state serialization capped at 12K tokens).
- **timeout_ms:** 8000 (socket-bounded on the advisory pool; independent of the
  race budget).
- **fallback:** deterministic template with neutral emphasis + no second-look tag;
  labeled `"deterministic"`. The suppression stands exactly as decided.
- **volume guard:** fires only for suppressions where `severity_in` suggests
  P1/P2 (the surprising ones) plus a stable 1% sample (fingerprint-hash mod 100)
  of the rest. Per-direction daily cap $0.10 → deterministic fallback past it.

### D2 — storm_briefer (one-paragraph storm brief)

- **input:** the storm aggregate record (`storm_size`, `storm_counts`, member
  cluster rollup) + the existing D3 advisory's root-cause candidates.
- **Jev call (typed):** extends the **existing** detached D3 storm advisory call —
  no new call site. Adds `Choice brief_lead` over the top member clusters
  (code-proposed, ≤12) and `Score pattern_novelty` (2–10 ordinal vs. recent
  storms). The paragraph is deterministic-template-rendered from (counts, clusters,
  Jev's lead pick, novelty level) and attached to the storm digest page payload.
- **output:** brief attached to the storm digest page (what the on-call reads first).
- **control_analysis:** Storm aggregates page **by construction** (D3/ADR-016 —
  `storm_digest_disposition` has no `action` parameter; suppression is
  inexpressible). The brief attaches after the page decision to a record that can
  only ever be a page. It can NEVER: suppress, un-page, reroute, or de-duplicate.
  The D3 advisory call it extends is already detached and non-gating
  (`submit_advisory`); this direction adds questions to that call, not a new path.
- **cost_per_call_usd:** ~$0.00002 marginal (piggybacks the existing D3 call;
  +~0.5K tokens). See §4 for the merged storm-call costing.
- **timeout_ms:** the D3 advisory socket bound (unchanged; detached thread).
- **fallback:** page ships with raw counts only (today's behavior); labeled
  `"deterministic"`. The page is unaffected in all cases.

### D3 — evidence_bundler (retrieval-enriched page payloads)

- **input:** a paged alert (`page_now` / storm aggregate; `page_business_hours`
  optional per config). Post-decision.
- **behavior:** **deterministic retrieval first (code, no Jev):** related past
  incidents (same fingerprint family / service+check, from the event log,
  read-only) + matching runbook snippets (operator-configured runbook index,
  keyword match on check name). Candidates capped *before* serialization: ≤12
  incidents × ~150 tokens, ≤8 runbooks × ~200 tokens, hard state cap 8K tokens
  (truncate by recency).
- **Jev call (typed):** `Choice most_relevant_incident` + `Choice
  most_relevant_runbook` over the retrieved candidate sets ("code proposes, Jev
  disposes") + `Noul bundle_stale` (*"is the retrieved material contradictory or
  stale relative to this alert?"*).
- **output:** evidence bundle attached to the page payload — ranked incidents,
  ranked runbooks, stale-flag. Deterministic assembly; Jev only ranks.
- **control_analysis:** Built at page-payload build time, **after the page is
  emitted**. It can NEVER: suppress, delay, or reroute the page — the page has
  already left; the bundle is payload enrichment. Retrieval is read-only over the
  event log and never writes to the decision path. If `bundle_stale=yes`, the
  bundle is labeled "retrieved material may be stale" — the flag downgrades trust
  in the bundle, never the page.
- **cost_per_call_usd:** ~$0.00015 (~3.5K input tokens). Hard per-call cap $0.0005.
- **timeout_ms:** 8000.
- **fallback:** page ships with the raw retrieved bundle in recency order
  (unranked), labeled `"deterministic"`.

### D4 — rca_hypotheses (advisory root-cause hypotheses)

- **input:** storm aggregate or `page_now` alert. Post-decision.
- **Jev call (typed):** `Choice top_hypothesis` over a **deterministic candidate
  set** — code proposes from member checks/services + deploy-event correlation
  (e.g. "bad deploy of X at T−12m", "AZ network blip", "DB failover"),
  ≤20 options + mandatory `cannot_determine` ("do not guess") — plus `Score
  hypothesis_plausibility` (2–10 **ordinal**, presented as "plausibility level
  L/10", never a probability).
- **output:** hypotheses attached to the record as **"AI-generated hypothesis —
  unconfirmed"**: starting points for the on-call, never conclusions. The D3
  storm advisory's existing severity/team candidate answers are folded into this
  call (see §4: one merged storm call).
- **control_analysis:** Hypotheses are generated after disposition; the gate never
  reads them; they are never presented as facts and never trigger any automated
  action (no auto-remediation, no re-page, no severity change — those would be
  paging-path writes and are explicitly out of scope). It can NEVER: page,
  suppress, change severity/routing, or auto-remediate.
- **cost_per_call_usd:** ~$0.00013 (~3K input tokens). Hard per-call cap $0.0005.
- **timeout_ms:** 10000.
- **fallback:** `"absent"` — no hypotheses attached; the record honestly shows
  "no hypotheses available". Absence is preferable to a stale guess.

### D5 — policy_impact_preview ("this rule change would have suppressed X of last week's pages")

- **input:** an operator-proposed rule change (e.g. "add fingerprint F to
  allowlist", "raise `suppress_conf_min` 0.90→0.95") + a window of **archived**
  decision inputs (default: last 7 days of stored alert inputs + resolved
  evidence). Operator-initiated from the console; rate-limited (1 preview per
  policy draft per hour).
- **behavior (two stages):**
  1. **Deterministic replay (no Jev):** archived inputs are re-evaluated through
     `Gate._resolve_suppress_path` — the same one-kernel DR-26 composition the
     live decision and the D9 receipt use (no mirror implementation) — with the
     proposed policy applied. Output: exact counts — "would have suppressed X of
     Y pages (Δ), Z business-hours pages moved". Jev cannot do arithmetic; the
     counting is code.
  2. **Advisory triage (Jev, typed):** `Choice riskiest_delta` over the changed
     cases (code proposes; deterministic risk proxy pre-ranks by severity; top-25
     go to Jev) — *"which of these newly-suppressed pages would a human reviewer
     most want to inspect?"* Output: top-5 flagged for human review in the report.
- **output:** impact report (deterministic delta table + Jev-ranked review list).
  The report is **read by the operator**; shipping the change goes through the
  normal policy-change path (touchpoint: `src/sentinel/policy_lifecycle.py`;
  note the R-1 finding that the policy-change path needs governance hardening —
  this report is evidence *for* that path, not a bypass of it).
- **control_analysis:** Fully offline batch job; never on the hot path; read-only
  over archives. It CANNOT touch live policy, live alerts, or the gate. It can
  NEVER page, suppress, or ship a policy change — it produces a report a human
  reads. The Jev ranking advises *which deltas to review*, never *whether to ship*.
- **cost_per_call_usd:** ≤$0.002/preview (≤25 triage calls × ~1.5K tokens ≈
  $0.00006 each). Hard preview cap $0.003; operator-initiated only.
- **timeout_ms:** 30000 per preview (batch; operator waits with progress).
- **fallback:** report ships with the deterministic delta table only, labeled
  `"deterministic"` ("delta ranking unavailable").

### D6 — triage_suggest (suggested owner/severity, human confirms)

- **input:** a paged alert (`page_now` / `page_business_hours`). Post-page.
- **trigger (minority path only):** fires **only when the race did not produce a
  confident Jev read** — `source="timer"` (timer-win: paged because the judge was
  too slow) or the race answers were `cannot_determine`. When the race already
  answered cleanly, D6 is skipped (the gate's routing stands; no second call).
- **Jev call (typed):** `Choice suggested_owner` (team set, same criteria as Q2)
  + `Choice suggested_severity` (P1–P4 criteria set) over a **richer state**
  (full raw payload + recent sibling alerts) than the race's budget-constrained
  state. Framed as a suggestion, not a classification.
- **output:** attached to the page payload as **"triage suggestion (AI,
  unconfirmed) — confirm or override"**. The on-call human's confirmation is a
  **UI action**, not a gate input: it updates the incident's displayed
  owner/severity in the console; it cannot re-route an already-emitted page or
  trigger new paging.
- **control_analysis:** The page was already emitted with the gate's deterministic
  default routing. The suggestion is enrichment; the gate never reads it; it can
  NEVER re-route, re-page, escalate, or de-escalate automatically. Human
  confirmation changes console display state only. This direction is deliberately
  scoped to the timer-win/`cannot_determine` path so it can never shadow or
  second-guess a clean race decision.
- **cost_per_call_usd:** ~$0.00017 (~4K input tokens). Hard per-call cap $0.0005.
- **timeout_ms:** 12000 (post-page; latency is non-critical, accuracy matters).
- **fallback:** `"absent"` — no suggestion; the page carries the deterministic
  default routing, labeled accordingly.

## 4. Cost engineering (shared budget discipline)

Price list: **$0.042 / 1M input tokens, output free** (`JEV_USD_PER_M_INPUT` in
`src/sentinel/ab.py`). Track 2's default session budget: **$0.50**.

| Direction | Est. $/call | Cap $/call | Fires when | Daily cap |
|---|---|---|---|---|
| D1 explainer | 0.00008 | 0.0005 | surprising suppressions + 1% sample | $0.10 |
| D2 briefer | 0.00002 marginal | (shared) | every storm aggregate | — (in storm call) |
| D3 bundler | 0.00015 | 0.0005 | `page_now` + storm | $0.15 |
| D4 hypotheses | 0.00013 | 0.0005 | `page_now` + storm | $0.15 |
| D5 preview | ≤0.002/preview | 0.003/preview | operator-initiated, ≤1/hr/draft | $0.02 |
| D6 triage | 0.00017 | 0.0005 | race source ≠ jev only | $0.05 |

**Merged storm call (D2+D3+D4):** one detached advisory call per storm aggregate
carrying (severity_choice, team_choice [existing D3], brief_lead, pattern_novelty,
hypothesis_choice, hypothesis_plausibility, incident_rank, runbook_rank) —
~4.5K input tokens ≈ **$0.00019/storm**, replacing three separate calls. The merge
is specified here so Phase 2 implements one call, not three.

**Budget backstop:** Track 2's `blocked=true` degrades *all* directions to
deterministic fallbacks instantly. Per-direction daily caps degrade that direction
only. Worst-case daily advisory spend is bounded by construction (sum of daily
caps + merged-call volume), independent of alert storms — advisory cost cannot
spike with incident volume because D1 is sampled/capped and D2–D4 ride one call
per storm/page.

## 5. Timeout–fallback matrix (Track 7's falsifier surface)

| Direction | Timeout | Fallback behavior | Label |
|---|---|---|---|
| D1 explainer | 8 s | deterministic template, neutral emphasis, no second-look tag | `deterministic` |
| D2 briefer | D3 socket bound | raw counts only (today's behavior) | `deterministic` |
| D3 bundler | 8 s | recency-ordered raw bundle, unranked | `deterministic` |
| D4 hypotheses | 10 s | nothing attached (honest absence) | `absent` |
| D5 preview | 30 s/preview | delta table only, no ranked review list | `deterministic` |
| D6 triage | 12 s | nothing attached; default routing stands | `absent` |

Track 7 tests: force timeout per direction → assert the exact fallback + label;
exhaust budget (`blocked=true`) → assert zero Jev calls and all-`deterministic`
labels; FakeJev (absent key) → assert the pipeline completes with honest labels.
Shed (full advisory pool) → assert dropped enrichment, page/suppression
unaffected — the load test must show **zero** advisory-induced delay on the race
pool.

## 6. Rejected directions (control analysis with teeth)

These were considered and **rejected** — recorded so Phase 2 doesn't reinvent them:

1. **Jev-generated free text** (paragraphs/summaries written by the model).
   Rejected: Jev cannot generate text (typed answers only); even if it could,
   model-written prose is un-auditable and hallucinates facts. Templates render;
   Jev selects.
2. **Jev re-scores the disposition** ("second opinion on page/suppress").
   Rejected: a second judge on the decision path violates the iron rule — it is a
   shadow decision-maker, and any consumer *will* eventually read it as one.
3. **Auto-escalation / auto-remediation from hypotheses** (D4 → action).
   Rejected: paging-path write access. Hypotheses are read by humans, full stop.
4. **Suppression-confidence Noul gating the suppress leg** ("Jev, are you sure?").
   Rejected: the gate's suppress conjunction is deterministic and frozen; no
   advisory input may enter it, however "advisory" the framing.
5. **Learning thresholds from advisory outputs** (closing the loop).
   Rejected: advisory outputs feeding policy inputs would make Jev a slow,
   un-auditable decision-maker. No learning loop from advisory → policy, ever.

## 7. Phase 2 implementation sketch (for coordinator approval)

- `src/sentinel/advisory.py`: `AdvisoryDispatcher` (isolated `AdvisoryPool`,
  pre-call gates, per-direction entry points D1–D6, labeling). **No imports from
  `gate.py`'s decision path** — it consumes emitted payloads only. Dependency
  injection of `decide_fn` + `spend_meter` (Track 2 boundary; Track 7 test seam).
- Deterministic renderers (D1 paragraph, D2 brief) as pure functions over
  (typed answers + recorded evidence) — unit-testable with zero Jev.
- D3 retrieval: read-only helpers over the event log + operator runbook index
  (new, operator-configured; empty index ⇒ bundle is incidents-only, labeled).
- D5 replay: offline CLI (`scripts/policy_impact_preview.py`) reusing
  `Gate._resolve_suppress_path` on archived inputs; Jev triage via the dispatcher.
- Tests (local CPU): per-direction timeout→fallback, shed→fallback,
  blocked→all-deterministic, FakeJev labeling, template renderer goldens,
  merged-storm-call question-set test, cost-cap enforcement (oversize state ⇒
  no call). Full suite green before PR.

## 8. Cross-track touchpoints & open questions for the coordinator

- **Track 2:** I build against `resolve_jev_key()` + the spend meter + `blocked`
  degradation (C2). Question: does Track 2's `decide_fn` accept an arbitrary
  multi-question dict in one call (needed for the merged storm call)? The current
  `SystemOneClient.decide(state, questions)` does — confirm Track 2 preserves this.
- **Track 3:** advisory payloads ride the emission channel as advisory event types
  (not audit decision fields) — confirm the event-log lane will carry them
  opaquely or whether they need a registered envelope type.
- **Track 8:** labeling contract §2.3 is the UI surface — every advisory element
  renders with its AI marker + fallback state; nothing advisory is shown as fact.
- **Track 7:** falsifier matrix §5 is the acceptance surface; the advisory pool's
  isolation from the race pool must be load-tested (advisory storm ⇒ race latency
  unchanged).
- **Open:** D5's archive window default (7 days — confirm retention covers it);
  D1's 1% sampling rate; whether `page_business_hours` is in scope for D3/D6
  (proposed: no — cost discipline; revisit post-launch).

---
*Phase 1 ends here. No implementation code has been written. Phase 2 begins only
on the coordinator's written approval of this doc.*
