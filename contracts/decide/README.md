# Contracts C2 + C3 — the decide-side boundaries

**Status:** both `v0.1` · `stability: draft` (12H-PLAN F2 — explicit draft
marker, v0.x; the vocabulary freezes only once the eval harness R-14 exists)
· **Type-2 decisions** (reversible drafts)

| Contract | Boundary | Consumer-owner |
|---|---|---|
| C2 `alert.v0.1.json` | L2 NORMALIZE → L3 DECIDE-1 | correlator team (draft: corr-1, 12H-PLAN §1) |
| C3 `triage-result.v0.1.json` | L3 DECIDE-1 → L4 DECIDE-2 | gate team (draft: gate-2, 12H-PLAN §1) |

Per OPERATING-RULES §1.1 the consumer owns each contract: the correlator
approves changes to what it consumes (C2), the gate approves changes to what
it consumes (C3). No producer silently widens either.

---

## C2 — L2 NORMALIZE → L3 DECIDE-1: the canonical `Alert`

### What crosses

`Alert{alert_id, received_at, fingerprint, service, check, severity_in,
title, labels, metric_value?, metric_threshold?, breach_duration_s?}`
(`alert.v0.1.json`) — and only this shape.

- Identity: `fingerprint` is ADR-017 scheme v2
  (`sha256("service|check|severity_in|region|env|cluster")[:16]`,
  pattern-enforced `^[0-9a-f]{16}$`). `env`/`cluster` SHOULD be present in
  `labels` — they are fingerprint-identity inputs; an empty value is the
  `unknown` namespace, never a wildcard. (Tightening `env`/`cluster` to
  required is the named v0.2 candidate — correlator.md M9/P8; kept optional
  in v0.1 because the label contract and the eval harness don't exist yet,
  and F2 says don't freeze what you can't evaluate.)
- `received_at`: L1 acceptance time, ISO-8601 — distinct from any
  sender-supplied timestamp.
- `labels`: `map<string,string>`; `metric_*`/`breach_duration_s`: nullable
  numerics, null when the alert is not a metric alert.

### What NEVER crosses

1. **`Alert.raw` — the full vendor payload.** Stripped at the edge.
2. **`Alert.source` — the vendor identity** (`pagerduty`/`alertmanager`/
   `generic`). Stripped at the edge.
3. **Vendor DTOs, infrastructure types, unvalidated label fields.**
   (PIPELINE-REVISION §2.2 C2 — the ADR-017 staging→prod collision,
   resurrected by label drift, is the named failure this prevents.)

#1 and #2 are machine-checked, not prose: the schema carries a `not`
clause rejecting any instance containing `raw` or `source`. The decision
core never sees foreign shapes (hexagonal rule; deterministic-gate.md P4).
The audit path gets what it needs via the audit adapter — not by leaking
the payload into the core.

### Version / stability

`v0.1`, `stability: draft`. Additive inside the version (new optional
fields — the Tolerant Reader rule, `additionalProperties: true`); breaks
(field renames, `raw`/`source` reintroduction, fingerprint-scheme change)
get a new version. The fingerprint scheme itself is versioned by ADR-017,
not by this file — the schema pins the *format*, the ADR pins the *scheme*.

---

## C3 — L3 DECIDE-1 → L4 DECIDE-2: the `TriageResult`

### What crosses

`TriageResult{kind, fingerprint, episode_ref, prior_disposition?,
storm_declared?, flap_count?}` (`triage-result.v0.1.json`).

- **`kind` is a TYPED ENUM** — `new | duplicate | change_window |
  storm_fold | reopened` — not a string. This is the deliberate fix for the
  stringly-typed correlation kinds (`CorrelationResult.kind: str` today,
  correlator.py:300): the untyped structural paths short-circuit the gate
  via an untyped kwarg (deterministic-gate.md P4), and bare-string
  vocabularies are the defect class §2.4 names for dispositions. The
  contract types the vocabulary at the boundary.
- Mapping from today's code strings: code `"storm"` (folded into an active
  storm) → contract `"storm_fold"`; code `"label_stale"` does **not** appear
  — it travels the parallel `page_sink` output (D7: a `Page` record to the
  pipeline owner), never this boundary. An alert whose labels are stale
  still gets its triage kind here (fail-loud: staleness pages the pipeline,
  it never suppresses the alert).
- `episode_ref`: draft-opaque reference (`<fingerprint>:<episode-opened-ts>`
  convention); `prior_disposition` present only when `kind=duplicate`
  (inherits the prior, no Jev call); `storm_declared` only when
  `kind=storm_fold` (true on the declaring ingest); `flap_count` when
  `kind=reopened` (ADR-001: visible relapse count, never auto-promotes
  severity).
- **`prior_disposition` is opaque passthrough in v0.1**: `{action, reason}`
  carried, never reinterpreted. Its vocabulary is pinned by C4
  (pending-R9-ratification); C3 must not pre-empt C4.

### What NEVER crosses

1. **Untyped kinds.** Any `kind` outside the enum is rejected by the schema.
   The gate's structural paths (S1) consume only typed kinds — the revision
   closes the untyped-kwarg short-circuit.
2. **The correlator's internals**: `_seen` entries, baseline state `B(t)`,
   storm counts, shadow logs, label snapshots. The gate gets the *verdict*,
   not the *machinery*.
3. **Re-decision rights.** The triage classifies; the gate decides. A
   `duplicate` inherits its prior — the gate does not re-run the Jev race
   for it, and the correlator does not second-guess the gate's disposition.

### The unknown-kind rule (fail-safe, from the web research)

The enum is extensible — new members are additive — but the kore1
enum-addition incident (a producer adding one enum symbol dead-lettered
two consumers whose generated classes had never heard of it) is the exact
failure this boundary must survive. So the contract mandates the consumer
rule: **the gate MUST NOT fail on an unknown `kind`; it MUST map unknown
kinds to `new` semantics (full gate evaluation).** Mapping an unknown kind
to `duplicate` (inherit-and-skip) or to any suppression-adjacent path would
be fail-silent; mapping to `new` is fail-loud, consistent with the gate's
standing doctrine that every error falls toward the human
(deterministic-gate.md §1.5). Old gate + new correlator deploys stay safe
in either order.

### Version / stability

`v0.1`, `stability: draft` (F2). The two known draft-churn candidates are
`episode_ref` normalization and the `env`/`cluster` required-tightening
flowing in from C2 — both wait on the eval harness (R-14), per the F2
verdict.

---

## Skill clauses that bind (OPERATING-RULES §2.1 — specific, not badges)

- **principal-systems §3, software constitution: "Explicit versioned
  boundaries (never leak internals, never direct DB access across
  services)."** This binds C2's `raw`/`source` exclusion — the vendor
  payload is the correlator's "database"; the gate may not read it
  directly — and the machine-checked `not` clause that enforces it.
- **principal-governance §2, "Unforgiving API design":** binds the typed
  `kind` enum (C3) and the explicit additive-vs-breaking versioning rule on
  both contracts.
- **principal-governance §1.1, "The consumer owns the contract":** binds the
  ownership table at the top (correlator owns C2, gate owns C3) and the
  freeze discipline.
- **principal-systems law 2, "Eternal friction":** binds the unknown-kind
  rule — the contract assumes mixed-version deploys and a correlator newer
  than the gate, and fails loud rather than hoping versions stay in lockstep.
- **principal-mindset §3, "Proxy-trap audit":** binds the F2 draft markers.
  The true objective is "triage vocabulary that survives the eval harness";
  a v1.0 stamp today would be a proxy (a number that looks final) for a
  truth that doesn't exist yet. Draft is the honest label.

## Web research that shaped this (OPERATING-RULES §2.2)

- KORE1, "Schema Evolution Without Downtime: Event Versioning"
  (https://www.kore1.com/event-schema-evolution-versioning/ — last updated
  2026-09-29, accessed 2026-10-05): the enum-addition dead-letter incident
  directly shaped the C3 unknown-kind consumer rule; "registries check
  shape, not meaning" shaped the versioning rule (new meaning ⇒ new field,
  never same-name repurposing); the consumer-list discipline ("a reviewer
  can see who a change touches") shaped the consumer-owner table.
- "Building a Custom Webhook Provider: API Design Lessons from Stripe and
  GitHub"
  (https://medium.com/@instawebhook.com/building-a-custom-webhook-provider-api-design-lessons-from-stripe-and-github-10967a4d1bf1 —
  accessed 2026-10-05): "make additive changes the default; tell consumers
  to ignore fields they don't recognize" shaped `additionalProperties: true`
  on both decide schemas (Tolerant Reader).
- In-repo prior art: `models.py` `Alert` (the field set C2 canonicalizes);
  `correlator.py` `CorrelationResult` (the kind strings C3 types);
  `deterministic-gate.md` P4 (the hexagonal anti-corruption mandate);
  `correlator.md` P8/M9 (the label-contract gap C2 deliberately leaves
  open in v0.1).

## Alternatives considered and rejected

- **Carrying `raw`/`source` "for debugging the gate"** — rejected: that is
  exactly the trust issue the D6 firewall exists to mitigate
  (deterministic-gate.md §2.5); audit keeps what it needs via the audit
  adapter (P4).
- **`kind` as a free string (status quo)** — rejected: reproduces the
  disposition-vocabulary defect (§2.4, ~150 literals) one layer earlier;
  the untyped S1 short-circuit is what the revision closes.
- **Folding `label_stale` into the `kind` enum** — rejected: it is a page
  to the pipeline owner, not a triage of the alert; conflating the two
  outputs would let pipeline-health signals suppress alert triage.
- **Tightening `labels.env`/`labels.cluster` to required in v0.1** —
  rejected per F2: freezing the label contract before the eval harness and
  the label-pipeline exist is versioning theater; recorded as the v0.2
  candidate instead.

## Open interfaces

- **C4** (forwarder/gate-2 lane): pins the `prior_disposition` vocabulary;
  C3 carries it opaquely until then.
- **C5** (evlog-1/corr-2 lanes): the trace-ID envelope; `fingerprint` is the
  join key the envelope will reference.
- **qa-2** `tests/contracts/` skeleton (12H-PLAN F5): these schemas +
  fixtures plug into it; `../validate_contracts.py` is the interim
  machine check. All 11 fixtures across C1–C3 validate clean (run:
  `python3 contracts/validate_contracts.py contracts`).
