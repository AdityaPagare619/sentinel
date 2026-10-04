# D7 — History Provenance for the Correlator

**Lane:** `lane/d7-history-provenance` (Builder D7) · **Type: 2** (reversible)
**Owns:** `src/sentinel/correlator.py` (registry claim), `tests/test_history_provenance.py`

## Problem

The correlator makes pre-Jev deterministic decisions (dedup, storm collapse,
change-window queuing, episode lifecycle) informed by three history inputs:
past alerts, episode records, and the label pipeline's labels. None of these
carry provenance:

1. No timestamp answers *"what did we know and when"* — three inputs read at
   three drifting vintages make any post-incident reconstruction a guess.
2. No bound on lookback — the correlator can, in principle, consult arbitrarily
   old history. Last week's operational context (different on-call, different
   deploy train, different traffic) is not signal about today's alert; it's
   archaeology.
3. The label pipeline can go stale *silently*. A P1 mislabeled P4 because the
   label pipeline stalled for an hour is a fail-silent path — the unforgivable
   failure mode for a paging system.

And the novelty signal ("this fingerprint has never been seen") is the most
dangerous kind of behavioral change: promoting it straight to live alters
dispositions on precisely the alerts we understand least.

## Decision (Type 2 — reversible)

Additive only, behind constructor parameters, no changes to the frozen
`models.py` contract, no new required arguments. Three mechanisms:

1. **Single-clock provenance** — every history read carries `history_as_of`.
2. **72h hard bound** — older history is ignored, not down-weighted.
3. **Label-pipeline staleness SLO that pages**; **novelty in shadow first**.

## The single-clock rule

Every history input the correlator reads — past alerts, episode records, label
snapshots — is read under ONE clock: the read time. Each read returns
`history_as_of = now`, the knowledge cutoff: *"everything the correlator knew
at T"*. All inputs share the same T, so a decision's provenance is a single
timestamp, not three drifting vintages.

Enforcement is structural, not conventional: all history reads flow through
ONE function, `Correlator.read_history(fingerprint) -> HistoryView`. No other
path may consult history (state isolation — the correlator never reaches into
another store directly). Every `CorrelationResult` carries `history_as_of`,
so the decision is auditable end-to-end: alert → history read → disposition,
one clock.

**Late arrivals:** `history_as_of` is the *knowledge cutoff*, not the event
time. A late-arriving alert lands in the NEXT read. Past reads are never
rewritten — backfilling would falsify the audit trail.

## Why 72h (the hard bound)

1. **ADR-001 already chose 72h.** A fingerprint unseen for >72h starts a
   FRESH episode; episode identity expires after 72h of silence. One number,
   one law — no new knob per input.
2. **Incident half-life.** After 72h the operational context that produced the
   alert is gone: on-call rotations (24–48h), deploy trains, traffic patterns,
   change windows. Correlating today's alert with last week's context is
   archaeology, not signal.
3. **A bound beats a weight.** Down-weighting old history (exponential decay,
   recency scores) is a tuning knob that hides staleness behind a curve. A
   hard cutoff is auditable: *"records older than 72h get no vote"* is
   checkable in one grep. The decay constant would drift; the cutoff doesn't.
4. **Bounded memory.** The in-memory store prunes everything past 72h by
   construction — same asymptotic shape as today at 10× scale.

Consequences (all enforced, all tested):
- A fingerprint last ingested 73h ago is **novel** — not "somewhat familiar".
- An episode record 73h old **cannot cause a flap-reopen** (it was pruned;
  the re-fire is fresh by construction).
- A past alert 73h old **cannot make a new alert a duplicate**.

## Label staleness as a paged SLO

> **NOT YET WIRED (honest flag, R11).** No in-repo pipeline produces
> `LabelSnapshot` today — the SLO machinery below is built and tested but
> unreachable until the label pipeline lands and starts passing snapshots at
> ingest. The correlator does not *consume* label snapshots yet; it *accepts*
> them. The "correlator consumes" phrasing elsewhere in this doc is aspirational.

The correlator accepts label snapshots from the label pipeline (when it exists):
`LabelSnapshot(labels, version, labels_as_of)` — passed at ingest by the
pipeline that owns them.

**SLO:** `now - labels_as_of ≤ 900s (15 min)`.

Why 15 min: **assumed** 3× the pipeline's 5-min heartbeat — but the pipeline
doesn't exist in-repo, so the 5-min heartbeat is an unverified external
assumption (R11). Re-derive from the real heartbeat when the pipeline lands.
One missed publish is a blip, a sustained stall pages. And 15 min dominates
the correlator's own short horizons (60s storm window, 5-min dedup): labels
older than the decision horizon they inform are rotten by definition.

**On breach: PAGE.** The correlator emits a structured
`Page(severity="page", reason="label_pipeline_stale", service, fingerprint,
detail, raised_at)` to the injected `page_sink` and returns
`kind="label_stale"` — a new terminal kind meaning *"page the incident AND
page the pipeline owner"*. The alert is **not** silently correlated on rotten
labels, and it is **not** suppressed: `label_stale` bypasses dedup/storm/
change-window classification entirely. Fail-loud on both axes.

**Missing snapshot ≠ stale snapshot.** If the pipeline passes no snapshot,
the correlator proceeds on the alert's intrinsic labels (current behavior)
and marks `labels_from="alert"`. Absence is visible, not rotten — the SLO
judges *vintage*, not *presence*.

**Page anti-fatigue.** Emissions are deduped per `(reason, service)` with a
300s cooldown (injectable `page_cooldown_s`). The correlator owns emission
discipline; the sink owns delivery dedup. A 4-hour pipeline outage pages once
per 5 min per service, not once per alert.

## Novelty: shadow first, live only on evidence

Signal: a fingerprint with **zero observations in the 72h window**
(`HistoryView.novel`).

**Shadow mode (default, `novelty_mode="shadow"`):** the correlator computes
what the novelty rule WOULD do — flag for careful treatment — and appends a
`NoveltyShadowEvent(fingerprint, history_as_of, would_do="flag_review")` to
the inspectable shadow log. The disposition is **unchanged**: `kind` is
exactly what it would have been without the rule, `novel=False`,
`novel_shadow=True` as observability only. Shadow is telemetry, not behavior.

**Live mode (`novelty_mode="live"`):** the same event sets
`result.novel=True`. Live novelty is **flag-only**: it never suppresses,
never delays paging, never changes `kind`. Downstream (gate) treats
`novel=True` as *"no prior inheritance, prefer human review on uncertainty"*.
Novelty can make us *louder*, never quieter.

**Promotion gate (shadow → live)** — ALL of:
1. ≥100 shadow events recorded. Enforced in code:
   `promote_novelty_to_live(min_shadow_events=100)` raises otherwise.
2. Spot-audit of the shadow log: the would-do action agrees with the actual
   outcome in ≥95% of sampled events — specifically, no case where
   "flag for review" would have hidden a real P1.
3. An operator signs the promotion (decision log).

**Reversal:** one constructor argument back to `"shadow"`. Type 2.

## Alternatives considered and rejected

- **Down-weight old history** (exponential decay, recency scores): hides
  staleness behind a curve; unauditable; the decay constant is a tuning knob
  that drifts. Rejected.
- **Per-record-type TTLs**: two or three clocks instead of one; harder to
  reason about, harder to test, harder to explain at 3am. Rejected.
- **Block ingest on stale labels**: blocking the correlator blocks paging —
  the worst possible failure. Fail-loud means page AND route through, not
  halt. Rejected.
- **Promote novelty straight to live**: changes dispositions on the
  least-understood alerts with zero evidence. Rejected — shadow-first is
  campaign law for behavior changes.

## Pre-mortem (top 3 failure modes)

1. **`history_as_of` lies because of late-arriving alerts** (read-time ≠
   event-time), and someone "fixes" history by backfilling past reads.
   → Documented above: it is the knowledge cutoff; late data lands in the
   next read; past reads are immutable.
2. **A label-pipeline outage emits a page per alert → page fatigue → the real
   page gets ignored.** → 300s per-`(reason, service)` emission cooldown in
   the correlator; sink owns delivery dedup.
3. **Novelty promoted on thin evidence floods the review queue and delays
   real P1s.** → 100-event minimum + ≥95% audit agreement + signed promotion;
   live novelty is flag-only, never a delay.

## Constitution bindings

- **Software:** explicit versioned boundaries — one read path
  (`read_history`); never leak internals; state isolation (SSOT).
- **Data/AI:** data pedigree first — `history_as_of` is the pedigree stamp
  on every read; deterministic guardrails — novelty is shadow-first, live
  novelty never suppresses.
- **Infrastructure:** observability over monitoring — structured pages
  (`raised_at`, `reason`, `service`) and an inspectable shadow log; greppable
  at 3am.

## Done-checklist

- **10× scale:** in-memory maps bounded by the 72h prune; same asymptotic
  shape as today.
- **Label pipeline down 4h:** every ingest pages (cooldown-deduped) with
  `label_stale`; nothing is silently correlated on rotten labels; incident
  paging continues.
- **Junior deploys a stale label snapshot:** caught by the SLO check at
  ingest — before any decision consumes it — not by an incident review.
- **Global vs local:** one bound (72h) reused from ADR-001 instead of a new
  knob per input; one clock instead of three vintages.
- **Truck factor:** this doc + `read_history` + `tests/test_history_provenance.py`.

## API surface (additive)

```python
c = Correlator(
    ...,                                  # existing params unchanged
    history_window_s=72*3600,             # D7: the single 72h bound
    label_slo_s=900,                      # D7: label-pipeline freshness SLO
    novelty_mode="shadow",                # D7: "shadow" | "live"
    page_sink=None,                       # D7: callable(Page) -> None
    page_cooldown_s=300,                  # D7: per-(reason,service) emission cooldown
)
c.ingest(alert, label_snapshot=None)      # snapshot from the label pipeline
c.read_history(fingerprint) -> HistoryView # the ONE history read path
c.shadow_log() -> [NoveltyShadowEvent]    # inspectable shadow evidence
c.promote_novelty_to_live(min_shadow_events=100)  # gated promotion
```

`CorrelationResult` gains: `history_as_of`, `novel`, `novel_shadow`,
`label_stale`, `page_pipeline`, `labels_from`. New terminal kind:
`"label_stale"`.
