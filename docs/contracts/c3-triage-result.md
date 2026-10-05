# C3 — `TriageResult` contract, v0.1 (consumer-side draft)

> **Status:** v0.1 for corr-1 (producer) review · **Lane:** gate-2
> **Branch:** `lane/12h-gate-2-d8` · **Date:** 2026-10-05 IST
> **Contract version:** `c3/v0.1` · **Owner (consumer):** gate-2 (the gate
> owns this contract per OPERATING-RULES §1.1 — the consumer owns the
> contract; the correlator (corr-1) is the producer under review)
> **Traceability:** PIPELINE-REVISION §2.2 C3; deterministic-gate.md P4
> (ports for the gate's seams); 12H-PLAN §1 gate-2 row, §2 T+3 drop.

## 1. What crosses / what never crosses

**Crosses (L3 DECIDE-1 → L4 DECIDE-2):** exactly one `TriageResult`
object per alert, described by `c3-triage-result.schema.json` (same
directory — the machine-checkable artifact; this prose documents it).

**Never crosses:**
- Vendor DTOs, infrastructure types, unvalidated label fields (C2's
  anti-corruption rule rides through — the correlator translates at its
  edge; the gate never sees foreign shapes).
- `Alert.raw` / `Alert.source` (already barred at C2).
- An untyped "verdict-ish" object — the current `correlation` kwarg's
  `getattr(correlation, "kind", None)` duck-typing (`gate.py:719`) is
  what this contract replaces.
- Policy re-interpretation — the gate consumes triage; it does not
  re-triage (mirrors C4's "ACT executes, never re-decides").

## 2. The typed shape

```json
{
  "kind": "new | duplicate | change_window | storm_fold | reopened",
  "fingerprint": "<canonical fingerprint string>",
  "episode_ref": "<correlator episode id, or null>",
  "prior_disposition": {
    "action": "<Action>",
    "reason": "<ReasonCode>",
    "team": "<string|null>",
    "confidence": "<number|null>"
  } | null
}
```

- `kind` is a **closed enum**, not a string. `new` = no structural
  short-circuit (the race runs). `reopened` = a previously resolved
  episode re-fires — treated as `new` by the gate today (full
  conjunction applies), but typed distinctly so the producer's intent is
  visible in the decision record.
- `prior_disposition` is **required iff `kind == "duplicate"`** (schema
  enforces via `allOf/if-then`); otherwise it must be `null`.
- Mapping from today's kwarg vocabulary: `"storm"` →
  `storm_fold`; the `storm_declared` flag is retired — a *declared*
  storm never reaches the gate's structural path (it goes to the
  digest path, which never calls `evaluate_policy`); `storm_fold`
  means exactly "undeclared continuation, fold into the aggregate."
- `episode_ref` may be `null` for `new` (no episode yet); required
  otherwise.

## 3. Consumer (gate) semantics per kind — S1 mapping

Grounded in `gate.py:713-740` (`_structural`); the contract pins what
the code does today and closes its gaps:

| `kind` | gate behavior | today |
|---|---|---|
| `new` | no short-circuit; the race runs | `correlation is None` or unrecognized |
| `duplicate` | inherits `prior_disposition` (action/reason/team/confidence), reason recorded as `"dedup"` | `kind == "duplicate"` + `correlation.prior` |
| `change_window` | `page_business_hours`, reason `"change_window"` | same |
| `storm_fold` | `folded`, reason `"storm"` — explicitly **not** suppression (D3) | `kind == "storm"` and not `storm_declared` |
| `reopened` | no short-circuit; the race runs (full conjunction) | unrecognized → race runs |

**Fail-open on malformed input (the error direction, kept):**
- Unknown `kind` value → treated as `new` (race runs; full conjunction
  applies). The structural short-circuits are allow-listed, never
  defaulted — a producer bug cannot invent a new suppress path.
- `duplicate` with `prior_disposition == null` → contract violation →
  treated as `new` + named event `triage_contract_violation{kind,
  missing: "prior_disposition"}`. Inheriting a disposition from nothing
  is the one S1 path that could suppress (`dedup` of a prior
  `suppress`); it requires the evidence present.
- Schema validation failure → `new` + `triage_contract_violation`.
  Validation happens once at the boundary (the gate's S1 entry), not
  per-branch.

This is the *error* direction (fail-open toward the human), deliberately
distinct from D8's *authority* direction (fail-closed on ungrounded
policy) — see `d8-fail-closed-flip.md` §2.1. Triage ambiguity degrades
to the full conjunction; policy-authority ambiguity withholds
suppression.

## 4. Producer obligations (for corr-1's review)

1. Emit exactly one `TriageResult` per alert handed to the gate; `kind`
   is always one of the five enum values — never `null`, never a new
   string without a contract amendment (OPERATING-RULES §1.1: additive
   inside a version, new version for true breaks).
2. `duplicate` is emitted **only** with the prior `Disposition` the
   fingerprint actually earned (the full conjunction ran for it) —
   inheriting a `suppress` is inheriting earned authority, not a
   shortcut (this is the R15 F4 honest exception's precondition).
3. `storm_fold` is emitted only for undeclared continuations; declared
   storms do not produce gate-bound `TriageResult`s at all.
4. `fingerprint` is the canonical fingerprint (the correlator's
   `fingerprint_for`/`fingerprint_of` output), never a vendor key.

## 5. Versioning & compatibility

- `c3/v0.1`. Additive changes inside the version (new optional fields);
  a new enum value or a required field → `c3/v0.2` (new version for
  true breaks, per §1.1).
- Wire form is JSON validated against the checked-in schema; the
  Python surface becomes a frozen dataclass/enum in the Phase-1 build
  (deterministic-gate.md P2's enum direction applies to `kind`).

## 6. Verification

- `c3-triage-result.schema.json` validates the fixtures in
  `tests/contracts/fixtures/c3/` (to be added; qa-2's skeleton plugs
  this in per 12H-PLAN §1).
- Exhaustiveness test (build): every `kind` the correlator can emit
  appears in the enum; every enum value has a consumer row in §3.
- Contract-violation paths (§3, malformed input) are pinned by tests —
  the violation event fires, the race runs.

## 7. Open questions for corr-1 (producer review)

1. Is `episode_ref` available at triage time for all five kinds, or
   only post-`note_disposition`? (v0.1 allows `null` for `new`; confirm.)
2. Does the correlator have a distinct `reopened` signal today, or is
   this a new producer obligation? (v0.1 types it; the gate treats it as
   `new` either way.)
3. `storm_declared` retirement — confirm no other consumer reads the
   flag before we remove it.

---

*Consumer-side v0.1. Producer review (corr-1) may amend via ADR +
same-hour re-announcement per OPERATING-RULES §1.1; unannounced drift is
a §5 violation.*
