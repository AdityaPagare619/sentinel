# Session B2 — Diagrams: index, scope, review status

**Session:** Sentinel all-chiefs planning meeting, Session B2 (DIAGRAMS).
**Date:** 2026-10-05.
**Facilitator/scribe:** Petu (founder-deputy).
**Skills loaded and followed:** `principal-systems`, `principal-governance`,
`principal-mindset`, `execution-doctrine`.

**Session rule (from the brief):** every diagram reviewed for logical
correctness by a DIFFERENT chief than its author. A wrong diagram is worse
than none. Each reviewer cited the file/line or doc section grounding every
arrow; disagreements were recorded and resolved by **disagree-and-commit**
(principal-systems operating rule 2, principal-governance §4.1).

**Grounding base:** `docs/architecture-revision/PIPELINE-REVISION.md` §2
(layers + C1–C7), the 10 domain memos in `docs/architecture-revision/`,
`docs/planning/ROADMAP.md` §9 (dependency graph), and the actual code on
the `program/architecture-revision` branch, cross-checked against
`origin/main` for the live flow symbols
(`Pipeline.handle_pd`, `_triage`, `_classify_locked`, `Gate.evaluate`,
`RaceRunner`, `DurableForwarder`, `_int_test_page` — all verified present on
`origin/main`).

**How the session ran:** drafts were produced per diagram, then each reviewer
ran an independent adversarial pass arrow-by-arrow against the grounding
sources before any discussion (principal-mindset §9 — independent judgments
before aggregation). Disputes are logged below with their resolution.
Nothing here was merged — branch `program/planning/diagrams` only.

---

## Index

| # | File | Scope | Author chief | Reviewer chief | Status |
|---|------|-------|--------------|----------------|--------|
| 1 | [pipeline-layers.mmd](pipeline-layers.mmd) | Revised pipeline L1–L7 + C1–C7 contracts (what crosses, what never crosses) | Forge (architecture) | Pager (SRE) | reviewed ✓ |
| 2 | [seq-alert-to-page.mmd](seq-alert-to-page.mmd) | Alert ingress → dedup/correlation → race-to-page → deterministic gate → forwarder → PagerDuty, incl. all fail-open paths | Pager (SRE) | Vault (security) | reviewed ✓ |
| 3 | [seq-byok-setup.mmd](seq-byok-setup.mmd) | BYOK key setup (UI → API → store) + test-page flow (UI → API → PD) | Prism (UI/integrations) | Vault (security) | reviewed ✓ |
| 4 | [seq-policy-change.mmd](seq-policy-change.mmd) | Policy change + attestation ceremony: CURRENT theater gap (kernel reads thresholds.json) vs REVISED canonical PolicyVersion.content | Ledger (audit/lifecycle) | Tripwire (red team) | reviewed ✓ |
| 5 | [seq-enqueue-auth.mmd](seq-enqueue-auth.mmd) | /v2/enqueue HMAC auth: CURRENT unauthenticated trigger path vs REVISED R-1 | Vault (security) | Oracle (testing/auth-matrix) | reviewed ✓ |
| 6 | [critical-path.mmd](critical-path.mmd) | 12-hour plan's critical path from ROADMAP §9's dependency graph: phase chain, load-bearing items, human gates | Forge (architecture) | Ledger (audit) | reviewed ✓ |

Every `.mmd` file carries its own header comment: scope, author-chief,
reviewer-chief, grounding sources, date, review status.

---

## Per-diagram review notes

### 1. pipeline-layers.mmd
- Reviewer verified each C1–C7 edge against PIPELINE-REVISION.md §2.2
  (crosses/never-crosses lists), the ports box against §2.3, and the failure
  isolation box against §2.4 (Jev breaker, per-sender bulkheads, cost breaker,
  degraded rung 1).
- Dispute D-1 (resolved): draft's C2 edge text allowed `Alert.source` to
  cross into L3. Reviewer cited C2's NEVER list (PIPELINE-REVISION.md §2.2:
  "`Alert.raw` and `Alert.source` do not cross this boundary") — the author
  had conflated the current code (where raw/source do leak through the core,
  deterministic-gate.md §2.5) with the revised contract. Committed: edge now
  shows the REVISED "never crosses" list, with the current leak flagged only
  in seq-alert-to-page's annotation.

### 2. seq-alert-to-page.mmd
- Reviewer walked every arrow against `receiver.py:237–300` (handle_pd),
  `:433–475` (_triage), `correlator.py:587–731` (_classify_locked), and
  deterministic-gate.md §1.1 (S1 → D6 → C1 → S2 order).
- Dispute D-2 (resolved): draft showed "duplicate → suppress". Reviewer cited
  gate memo §1.1 S1: `duplicate` INHERITS the prior disposition
  (`reason="dedup"`) — the prior may be `page_now`, not only suppress.
  Committed: arrow now reads "inherit prior disposition".
- Dispute D-3 (resolved): draft routed the storm aggregate through
  `gate.evaluate`. Reviewer cited `receiver.py:452` (`gate.digest_storm`) and
  the gate memo: `digest_storm` is a separate code path — never calls
  `evaluate_policy`, never arms a race, cannot suppress by construction.
  Committed: separate swimlane with NO arrow to evaluate_policy. Reviewer
  also forced the R15 F4 honest-exception note (S1 dedup CAN emit suppress
  while degraded, inheriting a pre-degradation prior) — a diagram that hides
  the exception is a wrong diagram.

### 3. seq-byok-setup.mmd
- Reviewer verified endpoints against `platform/server/app.py:176–197` and
  `_int_test_page` (:421–490), and the key precedence against `:455–457`.
- Dispute D-4 (resolved): draft routed the test page through the
  DurableForwarder outbox. Reviewer cited `app.py:477` → `_pd_enqueue`
  (:621–640): a DIRECT single urllib POST to PD (10 s timeout) — no outbox,
  no `PD_RETRY_TABLE`, no retry schedule, no ForwardReceipt. Committed:
  test-page arrow bypasses L5 entirely, labeled as such.
- Dispute D-5 (resolved): draft showed test-page key resolution identical to
  the production path. Reviewer cited forwarder-byok.md §2.1 (split-brain):
  test page resolves per-request → stored → `PD_ROUTING_KEY` env, while the
  durable production path resolves env-only. Committed: the red split-brain
  panel naming the UI "configured" proxy explicitly.

### 4. seq-policy-change.mmd
- Reviewer verified Path A/B against deterministic-gate.md §2.1, the B3
  state machine and attestor ADR against §1.4, and D8 semantics against
  §1.3/§2.1.
- Dispute D-6 (resolved): draft drew only the revised flow (attestation →
  ConfigLoader → kernel). Reviewer (Tripwire) insisted the brief demands
  CURRENT vs REVISED in one diagram and that the current disconnect is the
  entire point — drawing attestation feeding the kernel today would be
  governance-theater-as-diagram. Committed: CURRENT panel shows Path B
  (thresholds.json → ConfigLoader → kernel) as the live path and Path A as a
  box with a dashed "binds nothing the decision path reads" edge; D8's
  fail-open missing-file semantics labeled explicitly.

### 5. seq-enqueue-auth.mmd — the hardest-fought arrow of the session
- Dispute D-7 (resolved): draft (Vault) included an "auth?" decision diamond
  on the CURRENT /v2/enqueue trigger path with a note "onboarding only".
  Reviewer (Oracle, auth-matrix owner) rejected it: `receiver.py:652` calls
  NO auth function on the trigger branch — the headers are passed through
  solely for resolve-claim auth. A diamond implies a check exists where none
  does; in a security diagram that is the precise failure mode the session
  exists to prevent. Committed: the current trigger lane shows NO gate at
  all — a straight arrow labeled "no auth check (M-1); TCP-reachability is
  the only defense" — and the auth gate appears ONLY in the REVISED (R-1)
  panel. Author disagreed on aesthetics (a diamond "invites the eye to the
  gap"); reviewer won on correctness; author committed fully.
- Reviewer also verified the resolve-claim lane (fail-closed in every mode,
  onboarding never applies — `receiver.py:266–284`) and the R-5 replay-cache
  mitigation note (receiver.py:113–117).

### 6. critical-path.mmd
- Reviewer checked every item/edge against ROADMAP.md §9's dependency graph
  and the phase contents (§1–§6).
- Dispute D-8 (resolved): draft put R-11 (post-merge runner) on the critical
  chain ahead of R-12. Reviewer cited §9's "two dependencies that bite
  hardest": R-12 is explicitly named THE load-bearing spine ("three phases'
  falsifiers read it"); R-11 gates merges but is parallel work. Committed:
  R-12 is the spine node on the critical chain; R-11 feeds in as a parallel
  gate. Also confirmed Q2/Q3/Q6/Q8 are drawn as human gates that downstream
  work waits on, never routes around.

---

## Session close

All six diagrams reviewed, all eight disputes resolved by disagree-and-commit
— dissent was mandatory and on the record above; after each verdict the
author executed the committed version without re-litigation. No diagram
claims a check, gate, or contract that the code/docs do not show. The one
place a diagram *looks* wrong on purpose is seq-policy-change.mmd's CURRENT
panel — the disconnected attestation box IS the finding.
