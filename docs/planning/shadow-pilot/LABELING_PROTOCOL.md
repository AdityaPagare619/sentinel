# Labeling Protocol — Shadow Pilot

The pilot's headline metric (false-suppress rate on real SEV1s) needs ground
truth: for each alert the estate paged on, was it a **real incident** or
**noise**? This protocol produces those labels.

## Definitions

| Label | Meaning | Example |
|---|---|---|
| `real-sev1` | A human confirmed a customer-impacting incident; the page was
  necessary. | DB failover with write errors; checkout down. |
| `real-sev2` | Real degradation, page justified, but not SEV1. | p99 breach with conversion impact. |
| `noise` | No incident. The page woke someone for nothing actionable. | CPU spike that self-resolved; flapping monitor. |
| `unclear` | Cannot determine from available evidence. Excluded from the
  headline metric, counted separately. | Alert with no linked incident notes. |

## Who labels

The **customer's on-call**, not Sentinel. They own the ground truth of
whether a page mattered. Sentinel's team may pre-label from incident
metadata; the on-call confirms or corrects.

## When

Labels are assigned at **incident resolution** (not at page time):
- `incident.resolved` webhook arrives → the tap already has the full
  episode (triggered → acknowledged → resolved).
- The labeler reviews the episode: the alert, the ack notes, the resolution
  notes, and the estate's final priority field.
- Labels are written to the ShadowStore observation for that episode.

The estate's **priority field at final resolution** is the tiebreaker:
if the legacy stack paged it AND the final priority is P1/P2 AND a human
confirmed impact, it is `real-sev1`/`real-sev2` regardless of what the
shadow gate said.

## The zero bar

The report's headline: **did the shadow gate ever suggest SUPPRESS on
something labeled `real-sev1`?** The bar is **ZERO**. One false-suppress on
a real SEV1 holds the stage gate — no suppression ships until the cause is
understood and the evidence re-run.

`real-sev2` false-suppresses are reported separately (numbered divergence
list); they inform threshold tuning but do not hold the gate alone.

## Scale

A 2-week pilot typically yields 50–300 labeled episodes. Fewer than 30
labeled `real-sev1`/`real-sev2` episodes → the report prints **THIN — no
claim** on the calibration bands (Wilson 95% bounds too wide to mean
anything). The pilot runs longer until N≥30 or the customer accepts the
thin-data caveat in writing.
