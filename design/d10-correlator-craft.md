# D10 — Correlator Craft (ADR-001 ratification)

**Type:** 1 — rewrites the frozen engine contract (ARCHITECTURE.md §3.5);
correlation semantics decide which alerts the model ever sees.
**Status:** implemented in `src/sentinel/correlator.py`, this doc.

## 1. Heilmeier exam

- **What:** give the correlator an episode lifecycle — open/closed episodes
  keyed by fingerprint, a visible flap (relapse) count, a 72h freshness bound,
  and two legal close paths — plus carve P1/P2 out of change-window queuing.
- **Why now:** the v0.1 correlator's missing craft is load-bearing (ADR-001
  rationale): bad flap semantics multiply Jev calls and erode the
  one-call-per-storm guarantee (DR-14). Deterministic correlation is the
  global optimization — a better correlator buys more than model tuning.
- **Done looks like:** all four ADR-001 behaviors in code with falsifiable
  tests; DR-13 contradiction resolved; the gate's `change_window →
  page_business_hours` path unreachable for P1/P2 by construction.
- **Kill conditions:**
  1. A flap-storm CI fixture shows reopen full-evaluation raises Jev spend
     per storm with no paging-latency gain → rate-gate reopen evaluations.
  2. A real incident shows a P1 queued to business hours → carve-out
     failed; P0, fix the severity mapping, re-audit.
  3. Operator feedback shows the 72h bound loses useful continuity →
     re-derive the bound from incident data (candidate: 48h/96h).
  4. A vendor source shows auto-promote-on-reopen precedent → revisit the
     never-promote rule (ADR-001 names this explicitly as the changer).

## 2. Episode lifecycle state machine

```
                          ingest (fingerprint fp)
                                  │
                    ┌─────────────┴──────────────┐
                    │ change-window match AND    │──▶ change_window
                    │ NOT P1/P2 (DR-13)          │    (queued, pre-episode)
                    └─────────────┬──────────────┘
                                  ▼
                    ┌─────────────┴──────────────┐
                    │ open episode + same fp     │──▶ duplicate
                    │ inside dedup window        │    (inherit prior, no Jev)
                    └─────────────┬──────────────┘
                                  ▼
                    ┌─────────────┴──────────────┐
                    │ storm active               │──▶ storm (folded)
                    └─────────────┬──────────────┘    episode untouched
                                  ▼
              ┌─────────────────────────────────┐
              │ no episode OR last alert >72h    │──▶ new — FRESH episode
              │ ago → create Episode(open,flap=0)│    (history stale, flap reset)
              ├─────────────────────────────────┤
              │ episode CLOSED, gap ≤72h         │──▶ reopened — flap_count += 1,
              │                                 │    prior DISCARDED, full gate eval
              ├─────────────────────────────────┤
              │ episode OPEN, gap ≤72h           │──▶ new (outside dedup window)
              └─────────────────────────────────┘
                                  │
                    ┌─────────────┴──────────────┐
                    │ distinct fps in storm      │──▶ storm (declared)
                    │ window > threshold        │
                    └────────────────────────────┘

  resolve_episode(fp, reason="operator_resolve" | "verified_resolve")
      OPEN ──▶ CLOSED        (the ONLY close path; silence never closes)

  silence >72h: episode record is PRUNED (memory bound) — this is NOT a
  close. A re-fire starts a FRESH episode, which is the same outcome the
  freshness rule produces for a kept record. Identity expires; close does not.
```

## 3. Flap semantics — bump the count, never the severity

On reopen: `flap_count += 1` (visible to the operator on the result), episode
returns to open, the closed episode's prior disposition is **discarded**, and
the fresh alert flows to the gate for full evaluation.

Why not auto-promote severity/disposition (the deviation the panel insisted on):

- **Restatement test:** "reopen auto-promotes" claims the new alert is worse
  than the evidence says. The fresh alert's facts may differ — severity may
  have *dropped*, the breach may be shallower. Escalating stale state is
  promotion theater.
- **No vendor precedent** (Pager's condition): no major vendor auto-promotes
  on flap-reopen; practitioner conventions encode incidents we haven't had
  (Chesterton's fence).
- **The honest path exists:** the full gate evaluation (Jev race) re-derives
  severity from current evidence. If the flap is real, the model pages —
  loudly, with fresh evidence, not with a stale counter.
- **Stale-prior purge:** on both `resolve_episode()` and reopen, the dedup
  record's `prior` is cleared. A duplicate arriving in the dedup window
  after a close can never resurrect a dead episode's disposition.

The most common flap pattern — re-fire seconds after the close — still counts:
the reopen check runs *before* the dedup-window short-circuit for closed
episodes, so a 30s-late flap is `reopened` with `flap_count=1`, not swallowed
as a duplicate.

## 4. Why never-auto-close

Vault's objection stands: auto-close on silence is silent suppression by
another name. An episode is an operator-owned object; silence is evidence of
nothing — flapping sources, dead forwarders, and holiday traffic dips all
produce silence. The only legal closes are:

1. **`operator_resolve`** — explicit human action ("this incident is over").
2. **`verified_resolve`** — an authenticated resolve signal from the source
   (e.g. a signed PagerDuty resolve webhook). Unsigned "resolves" never
   count; the absence of alerts never closes.

Rejected alternative: close-on-silence with an N-hour timer. Killed by the
pre-mortem: a source that stops alerting because *its forwarder died* would
auto-close every open episode — the exact outage shape Sentinel exists to
catch, self-concealing.

The 72h freshness rule is deliberately NOT a close: it expires *identity*,
not the episode. Never-auto-close forbids the open→closed transition on
silence; freshness governs whether a re-fire is a reopen or a fresh start.

## 5. DR-13 reconciliation

**The contradiction (Tripwire):** DR-13 is decided — P1/P2 page now — but
`gate.py` routed `change_window → page_business_hours` unconditionally, so a
P1 firing at 02:14 inside a change window was a silenced P1 with extra steps.

**The reconciliation (panel condition a):** the carve-out happens in the
correlator, pre-Jev, at classification time — not as a gate override:

- `ingest()` matches change windows only when the alert is NOT P1/P2.
- P1/P2 class = source labels `critical`/`high` (also `p1`/`p2`), mapped
  case-insensitively from `severity_in`. This mapping is pre-model and
  deliberately coarse: a source-labeled `critical` pages now even if the
  model would have said P4. **Fail-loud beats fail-silent for P1.**
- Consequence: the gate's `change_window → page_business_hours` path is
  unreachable for P1/P2 by construction. The kind is never minted, so the
  disposition can never be misrouted. (The gate lane owns gate.py; no
  gate.py change was needed or made — single-owner discipline.)

**Known gap, documented honestly:** the carve-out trusts the source's label.
A mislabeled `critical` bypasses the window (loud, visible, operator sees
it). The post-model authority remains the gate's calibrated
P(p1)+P(p2) > 0.30 rule — the carve-out only guarantees the window never
defers.

**Onboarding note (panel condition c):** PagerDuty-side maintenance windows
are invisible to the receiver — Sentinel's change windows are Sentinel-side
config only. Documented in `docs/ONBOARDING.md`.

## 6. Storm interplay (why the ordering is what it is)

Pre-mortem failure #1: flap-reopen full-evaluation during a declared storm
multiplies Jev calls and breaks the one-call-per-storm guarantee (DR-14) —
the exact wound C2 found in the storm detector. Mitigation: the active-storm
fold sits *above* the episode outcome. During a declared storm a re-fire
folds into the digest and the episode record is **untouched** (stays closed,
flap count unchanged); when the storm clears and the fingerprint fires
again, the flap surfaces as a reopen. Flap visibility is deferred, never
lost; the storm guarantee is never pierced.

Pre-mortem failure #2: storm *declaration* must still count reopened alerts —
a burst of flapping fingerprints across distinct services IS a storm. New
and reopened alerts both feed the distinct-fingerprint counter.

Pre-mortem failure #3: unbounded `_episodes` growth in a daemon. The 72h
prune bounds memory and is behavior-preserving (see §2). Eternal friction:
the next maintainer inherits a bounded map, not a leak.

## 7. Alternatives considered and rejected

| Alternative | Why rejected |
|---|---|
| Auto-promote severity on reopen | No vendor precedent; escalates stale state; the race re-derives severity honestly (Pager's condition) |
| Suppress flap re-fires for N minutes | Silent by design — the flap exists, the operator must see it |
| Close episodes on N-hour silence | Vault's objection: silent suppression by another name; forwarder-death self-conceals |
| 24h freshness bound | Weekend-length incidents; 72h aligns with D7's history bound (coordinate, don't diverge) |
| Gate-level P1/P2 override instead of correlator carve-out | Two places minting the same decision = drift risk; the kind is never minted, so the gate path is unreachable by construction |
| Dedup check before reopen check | Would swallow the dominant flap pattern (re-fire seconds after close) |

## 8. Done-checklist (principal-systems)

- **10× scale:** episode map is one record per fingerprint, 72h-bounded;
  ingest is O(1) amortized plus the existing storm-deque scan. No
  architectural rewrite at 10× fingerprints.
- **Dependency down 4h:** no new dependencies — pure in-memory, stdlib only.
- **Junior deploys stale config:** change-window entries fail open (existing);
  `resolve_episode` raises `ValueError` on unknown reasons rather than
  silently mis-closing.
- **Global vs local:** the correlator is the global lever — one deterministic
  classification buys more than model tuning (ADR-001 rationale).
- **Run from docs:** this doc + the state machine in §2 is the whole contract;
  `get_episode()` gives the operator/tests a read-only view.
