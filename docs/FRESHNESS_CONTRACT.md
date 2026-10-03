# Freshness Contract — gate-lane interface (lane/impl-gate)

**Status:** implementation contract for `src/sentinel/freshness.py` on
`lane/impl-freshness`. Shared contract per the wave coordinator: the
freshness lane produces `FreshnessReport`; the gate lane's
`lock_evaluation` consumes it. **Do not drift silently** — any shape change
needed by either side goes through the coordinator first.

Design authority: `design/fixes/07-freshness-proofs.md` Part A (ADR-014).

---

## 1. What the gate lane consumes

```python
from sentinel.freshness import (
    FreshnessMonitor, FreshnessValidator,   # V1/V2 wiring
    FreshnessReport,                         # the cached verdicts
    suppress_precondition,                   # freshness legs of the conjunction
    lock1_fallback_offered,                  # lock-1 interim-path offer
    LOCK1_CALIBRATION, LOCK2_THRESHOLD, LOCK3_ALLOWLIST,
)
```

### FreshnessReport (the ONLY thing the gate reads per-alert)

```json
{
  "evaluated_at": "2026-10-03T01:30:00Z",
  "config_manifest_sha256": "<sha256 of the whole bundle>",
  "locks": {
    "lock1_calibration": {"verdict": "fresh|stale", "reason": "…", "proof_id": "…",
                          "fallback_available": true},
    "lock2_threshold":   {"verdict": "fresh|stale", "reason": "…", "proof_id": "…"},
    "lock3_allowlist":   {"verdict": "fresh|stale", "reason": "…", "proof_id": "…",
                          "entries": {"<fingerprint>": {"verdict": "…", "reason": "…",
                                                        "proof_id": "…"}}}
  }
}
```

Per-alert reads (O(1), no parsing, no clock, no I/O — this is the entire
point of two-point validation):

- `report.is_fresh("lock1_calibration") -> bool`
- `report.is_fresh("lock2_threshold") -> bool`
- `report.entry_fresh(fingerprint) -> bool` — lock 3 is **per-entry**
- `report.stale_locks() -> [lock ids…]` — every stale lock, for `page_reason`
- `report.stale_reasons() -> [reason strings…]` — ordered lock1 → lock2 → lock3

### The freshness precondition

```python
allowed, reasons = suppress_precondition(report, fingerprint=fp)
```

`allowed` is True only if every freshness leg passes. `reasons` names every
stale leg with its cause — prefix these into `page_reason` (e.g.
`page_reason="freshness:lock2_stale: revalidation clock lapsed (due …)"`).
Evaluation records **all** stale locks even though the conjunction
short-circuits (rot-matrix row 5: the operator sees the full rot).

`lock1_fallback_offered(report) -> bool` is True when lock 1 is stale but
the dual-attestation interim path (synthesis §3.1) may satisfy the
probability leg. Locks 2 and 3 offer no fallback — do not invent one.

### Reason-string vocabulary

Every stale reason starts with `lock{N}_stale: ` followed by a cause that
names the repair:

- `lock1_stale: fit TTL expired (age 120.0d > window 90d; fit_id …)`
- `lock1_stale: model_pin mismatch (fit trained against 'jev-1.13.0', pinned model is 'jev-1.14.0') …`
- `lock1_stale: label pipeline drift (fit trained on 'label-pipe-v3', deployed pipeline is 'label-pipe-v4') …`
- `lock2_stale: revalidation clock lapsed (due …)`
- `lock2_stale: config_hash mismatch — thresholds.json was edited without a matching two-person re-attestation …`
- `lock2_stale: invalid attestation — threshold 0.82 below conf floor 0.85 …`
- `lock3_stale: entry <fp> TTL expired …` / `… attestation VOID — incident linkage (SEV-…) …` / `… drift check failed — owning service had a major-version rewrite …`

An *invalid* proof (bad timestamp, single attester, sub-floor bar) yields a
`stale` verdict with an `invalid`-flavored reason — invalid ⇒ page, same as
stale. The verdict vocabulary stays `fresh|stale` per the design sketch.

---

## 2. Bundle layout (config-embedded, manifest-bound)

```
<config-bundle>/
  thresholds.json          # lock 2's live values; config_hash binds here
  allowlist.json           # {"envs": {env: {"entries": [fingerprints…]}}}
  calibration.json         # the fit artifact (gate reads p_upper HERE, not the proof)
  pinning.json             # {"pinned_model_version": "jev-1.13.0"}  ← ADDED (see §5)
  proofs/
    calibration_proof.json
    threshold_attestation.json
    allowlist_attestations.json   # {"entries": {fingerprint: {...}}}
  manifest.json            # {"schema_version": 1, "files": {relpath: sha256},
                           #  "manifest_sha256": sha256(canonical(files))}
```

Manifest verification runs FIRST at V1; `ManifestError` ⇒ invalid bundle ⇒
the caller falls back to last-good + page (ADR-018). The freshness validator
never runs on a malformed bundle. Proof-level defects ⇒ stale verdicts,
never raised (a validator that crashes on a bad proof converts calendar
rot into an outage).

### Mappings the gate lane must know

- `config.confidence_bar` ≡ `thresholds.json["suppress_conf_min"]`.
  The attestation's `threshold` must equal this live value.
- `config.pinned_model_version` ≡ `pinning.json["pinned_model_version"]`.
  The fit's `model_pin` must equal it (ADR-015).
- The deployed label-pipeline version is a **runtime input** to
  `FreshnessValidator(deployed_label_pipeline_version=…)` — it comes from
  the outcomes pipeline itself, not from config.

---

## 3. Lock 3 is per-entry (design refinement — read this)

The design sketch shows one verdict per lock, but §A4 requires per-entry
staleness for lock 3 (blast-radius containment: one poisoned fingerprint
must not page the entire org's noise, or operators learn to bypass the
freshness system). The report therefore carries:

- `locks.lock3_allowlist.entries[fingerprint]` — the per-entry verdict the
  gate checks alongside `fingerprint ∈ allowlist[env]`;
- `locks.lock3_allowlist.verdict` — the **summary**: `stale` if ANY entry
  is stale (drives monitoring/alerting), but the gate's suppress path uses
  the per-entry verdict, so fresh entries keep suppressing next to stale
  ones (rot-matrix row 4 proves this).

The attestation's `env` is static; the comparison `entry.env ==
alert.labels["env"]` is a **value check owned by the gate lane** (a
cross-env match is impossible by construction, ADR-017). The freshness
module additionally rejects namespace mismatches at validation
(attested-for-staging but listed-under-prod ⇒ stale).

---

## 4. Two-point validation — the gate NEVER validates

| Point | When | Who calls |
|---|---|---|
| V1 | boot + every config reload (inside the boot self-test, after manifest verification) | control plane → `FreshnessValidator.validate_bundle(bundle_dir, drift_state?)` |
| V2 | heartbeat, default every 15 min | control plane → `FreshnessMonitor.heartbeat(drift_state?)` |

`FreshnessMonitor` owns the cached report:

```python
mon = FreshnessMonitor(validator, bundle_dir, on_transition=emit)
report = mon.boot()            # V1
# …per alert:
report = mon.current_report()  # O(1); the gate reads verdicts from this
# …every 15 min:
for t in mon.heartbeat(drift_state):  # V2; flips verdicts, fires transitions
    audit.append(freshness_transition{lock, reason, proof_id})
    control_plane.alert_unsuppressible(t)   # guard's alarm bypasses the guard
```

Rules:

- **Per-alert proof evaluation is forbidden.** No clock arithmetic, JSON
  parsing, or drift-check queries on the race-to-page path.
- A reload that yields a stale verdict takes effect **immediately** (the
  next decision reads the new cached report).
- fresh→stale transitions fire `on_transition` **once per lock** (not once
  per alert). The transition alert is **unsuppressible** (constitution §7
  Choice 3) — enforcement lives in the control plane; the monitor
  guarantees exactly-once emission per transition.
- `drift_state = {"incident_linked": {fp: incident_id}, "rewritten": {fp: True}}`
  is a runtime input to V2 (and V1). The incident feed and the
  deploy-metadata feed are **required integrations** — without them the
  drift check degrades to TTL-only (documented limitation, not a silent gap;
  see `docs/FRESHNESS_LIMITATIONS.md`).
- V1 boot on an all-stale bundle **still boots and serves** — the failure
  mode is noisy paging, never silence, never a crash (rot-matrix row 8).

---

## 5. Deviations from the design doc (all flagged to the coordinator)

1. **`pinning.json` added to the bundle.** The doc's predicate references
   `config.pinned_model_version` but the §A2 layout never says where it
   lives. It now lives in `pinning.json`, covered by the manifest.
2. **Canonical-JSON `config_hash`** (reviewer follow-up, deep-dive wave):
   `config_hash = sha256(canonical_json(parsed thresholds.json))` —
   sorted keys, compact separators. A whitespace-only reformat does NOT
   break the binding (no false staleness); only semantic changes do.
   Covered by `test_whitespace_reformat_is_not_staleness` /
   `test_whitespace_reformat_does_not_change_hash`.
3. **Lock-3 per-entry verdicts in the report** (§3 above) — required by §A4's
   blast-radius rule; the doc's JSON sketch showed one verdict per lock.
4. **`issued_by` format gate**: `<run-id>/<code-version>`, both parts
   non-empty, no whitespace. The doc says "the validator rejects any proof
   whose `issued_by` is not a tuner run" — this is a format check, not a
   cryptographic signature (limitation documented).
5. **Rubber-stamp rule is split**: the validator enforces evidence
   *presence and shape*; rejecting a re-attestation that cites a stale
   backtest requires attestation history and is enforced by the
   two-person governance flow (platform tier), not the V1 machine check.
   Documented as a limitation, not silently dropped.

## 6. D1 wiring note (2026-10-04)

The precondition is now enforced in the live gate kernel
(`sentinel.gate.evaluate_policy`, wired via `Gate(freshness_monitor=…)`):

- The suppress branch requires the freshness legs (`_freshness_legs`):
  stale proof ⇒ `page_now` with reason `freshness:<every stale leg>`
  (the disposition `reason`; the contract's `page_reason` maps to it).
- No report ⇒ the legs fail closed: suppress is unreachable.
- Lock 1's stale leg may be satisfied via the dual-attestation interim
  path only with explicit operator evidence on the decision context
  (`context["lock1_dual_attested"]`); locks 2 and 3 have no interim.
- The rot-matrix fixture (`tests/test_freshness_rot_matrix.py`)
  exercises this same kernel per DR-26 — no test-side model remains.
- The receiver boots the monitor from `SENTINEL_FRESHNESS_BUNDLE`
  (unset ⇒ loud warning, suppress unreachable; corrupt manifest ⇒
  CRITICAL log, boot without the monitor — noisy paging, never silence).
