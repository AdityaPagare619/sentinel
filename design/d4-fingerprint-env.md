# D4 — Fingerprint env+cluster; dual-write migration; derived security ban (ADR-017)

Lane: `lane/d4-fingerprint-env` · Owner: builder D4 · Correlator-registry first slot.
Ratifies ADR-017 conditions: (a) `fingerprint_for` includes env+cluster with
dual-write + re-attestation migration; (b) security-category ban derived at
admission from taxonomy patterns.

## 1. Problem (five whys)

1. Staging fires the same check as prod → same fingerprint
   `sha256(service|check|severity|region)` → the correlator dedups them as one
   episode → a prod SEV1 can inherit a staging disposition (or vice versa).
2. Why does the hash ignore env? v0.1 assumed one estate per deployment.
3. Why is that load-bearing? The fingerprint is the dedup identity AND the
   allowlist key AND the attestation subject — every downstream consumer
   matches the raw hash.
4. Why not just namespace downstream (`derive_dedup_key` already takes env)?
   Vault's point: any future consumer matching the raw fingerprint reopens the
   hole silently. The hash itself must change.
5. Why is the security ban self-asserted? The attestation schema has a boolean
   the author fills in — a liar (or a tired human) checks `false` and a
   security check enters the allowlist. Taxonomy must decide, not the author.

## 2. Decision type

**Type 1** — the fingerprint scheme is the dedup identity of the whole system
(ADR-017 already adopted the design; this lane implements its ratification
conditions). The migration window length and log wording are Type 2.

## 3. Design

### 3a. Fingerprint scheme v2

- `FINGERPRINT_SCHEME_VERSION = 2` (correlator.py).
- `fingerprint_for(service, check, severity_in, region, *, env, cluster)` →
  `sha256("service|check|severity_in|region|env|cluster")[:16]`.
- env/cluster are **required keyword-only**: no call site may silently mint an
  env-blind hash ever again. Empty string is a distinct namespace ("unknown"),
  never a wildcard.
- `legacy_fingerprint_for(service, check, severity_in, region)` keeps the v1
  formula, clearly named and docstring-marked migration-only.
- `fingerprint_of(alert)` reads `env`/`cluster` from `alert.labels`
  (default `""`); `legacy_fingerprint_of(alert)` for the window.
- Field order appends env+cluster at the END (existing prefix structure
  preserved; v1 unrecoverable from v2 by construction — different input).

### 3b. Dual-write + re-attestation migration

State machine per attested entry: `v1 (legacy, resolves during window)` →
`v2-pending (resolves during window, re-attestation outstanding)` →
`v2-attested` / `v2-pending expired → stale (fail closed: pages)`.

- **Migration script** `scripts/migrate_fingerprints_v1_v2.py`:
  input = bundle dir + ops manifest `{v1_fp: {service, check, severity_in,
  region, env, cluster}}` (components are unrecoverable from a hash — the
  script is honest about needing them). Output: `allowlist.json` rewritten
  with `fingerprint_scheme: 2`, env-namespaced v2 entries,
  `legacy_v1: {window_ends_at, entries: [v1…]}`; attestations dual-written
  (v2 marked `re_attestation: "pending"`, v1 kept with `legacy_v1: true`);
  `legacy_allowlist.json` (`{window_ends_at, entries: {v1_fp: v2_fp}}`) for the
  Gate wiring; `re_attestation_manifest.json` for the two humans.
  Bounded window: `--window-days` default 30, hard max 90, refuses ≤0.
  Idempotent (re-run on a scheme-2 bundle skips unless `--force`).
  Every carried-over legacy entry is logged at WARNING with prefix
  `LEGACY-FINGERPRINT`.
- **Runtime** (gate.py, additive only — the pure `evaluate_policy` kernel is
  untouched): `Gate(..., legacy_allowlist: {v1: v2} | None,
  legacy_window_ends_at: str | None)`. Helper `_effective_allowlist(alert)`:
  if window open (parsed RFC3339, `now < ends_at`, clock-injectable) and
  `legacy_fingerprint_of(alert)` is in the map → union `{alert.fingerprint}`
  into the set passed to the kernel, **WARNING log
  `LEGACY-FINGERPRINT-RESOLVED`**, increment `self.legacy_resolutions`, and
  post-fix the kernel's `lock_evaluation["allowlist"]["detail"]` with the
  migration note (the `reason` taxonomy — asserted by tests as `"allowlist"` —
  is unchanged). `_leg1_prob_lock` falls back to the mapped v2
  `AllowlistEntry` so the dual-attestation interim path still works for
  legacy-resolved alerts during the window.
- **Freshness** (freshness.py): `AllowlistAttestation` gains `check`, `team`,
  `re_attestation` fields. `re_attestation == "pending"` entries are fresh
  while the bundle's window is open, stale after it lapses (fail closed —
  the re-attestation is enforced, not theater). Window end flows from the
  bundle's `legacy_v1.window_ends_at` through `_validate_allowlist`.
- **Wiring** (receiver.py): `_legacy_allowlist_from_env()` reads
  `SENTINEL_LEGACY_FINGERPRINTS` (path to the script's
  `legacy_allowlist.json`); passed to the live Gate and the shadow Gate.
  Absent file → no legacy resolution (fail closed); malformed → loud warning,
  no legacy resolution.

### 3c. Security-category ban derived at admission

- `SECURITY_BAN_TAXONOMY_VERSION = "secban-v1"` + `SECURITY_BAN_PATTERNS`
  table in freshness.py: `(field, regex, rationale)` rows over check names and
  team names, matched case-insensitively on token boundaries (`_ . : / -` or
  string edges) to avoid false positives (`auth_service_cpu` must NOT match).
  Documented inline; changes require an ADR (Type 1).
- `derive_security_ban(check, team) -> (banned: bool, reason: str | None)`.
- Admission point = `AllowlistAttestation.from_dict` (the attestation bundle
  validator — "an unattested allowlist entry cannot suppress"). After parsing:
  derive the ban from `(check, team)`; banned → `ProofFormatError` (rejected
  at admission — a banned check **cannot enter the allowlist**); stored
  `security_category_ban` disagreeing with derivation → `ProofFormatError`
  (fail closed on tamper); missing taxonomy (both empty) → `ProofFormatError`
  (post-migration entries must carry taxonomy; the migration script backfills
  it from the manifest). The dataclass keeps the derived value for the
  defense-in-depth check in `allowlist_entry_freshness`.
- Boundary (documented): the plain fingerprint set in `config.py` carries no
  check/team metadata, so derivation is impossible there — the attested bundle
  is the admission ceremony; the config set is the legacy shape.

## 4. Pre-mortem — top 3 failure modes

1. **Migration without dual-write** → every existing allowlist entry silently
   stops matching → known-noise pages at 3 AM, trust collapse. *Mitigation:*
   dual-write is the default output of the script; Gate resolves legacy during
   the window; tests prove a v1 entry still suppresses pre-expiry and stops
   post-expiry.
2. **Collision returns through the back door**: an ingest path with no
   env/cluster labels mints `env=""` for both staging and prod. *Mitigation:*
   required kwargs force an explicit decision at every call site; receiver
   normalizers pass labels through; `""` is a real namespace, documented.
3. **Ban table false negative** (a real security check name not in the table)
   → security alert allowlisted. *Mitigation:* table versioned + reviewed;
   derivation fails closed on disagreement; tests pin known-banned names.
   False positives are the safe direction (they page, never silence).

## 5. Alternatives considered and rejected

1. **Prefix-namespacing** (`"prod:abcd…"`) instead of changing the hash —
   rejected (Vault): any future consumer matching the raw fingerprint reopens
   the hole silently.
2. **Namespace only the allowlist, keep v1 for dedup** — rejected: the
   collision is in dedup identity itself; ADR-017 explicitly demands the hash
   change.
3. **Big-bang cutover, no window** — rejected (Pager): every existing entry
   silently breaks; Chesterton's fence (entries exist because humans attested
   them).
4. **Ban enforced in the gate kernel** — rejected: the kernel is pure and takes
   a fingerprint set; check/team metadata doesn't exist there. Admission
   (attestation parsing) is the correct layer.

## 6. Monkey-first + kill conditions

Hardest part: dual-resolution without breaking the 555-green suite or the pure
kernel's contract. Kill conditions: full suite not green after the change;
any `reason == "allowlist"` assertion forced to change; the migration script
unable to round-trip a fixture bundle. A hit is a successful outcome — report,
don't bleed.

## 7. Generalization story

Nothing here is tuned to any month or vendor: the hash change is structural
(env+cluster are identity, not features); the ban derives from a versioned
taxonomy table, not from observed data; the window is wall-clock bounded, not
data-fitted. Works identically on any estate, any month.

## 8. Done-checklist

- 10× scale: sha256 ×2 per alert is O(1); no new state.
- Receiver down 4h: fingerprints are computed at ingest; no external deps.
- Junior deploys stale config: legacy entries stop resolving at window end
  automatically (fail closed); pending re-attestations go stale.
- Global vs local: one scheme change at the identity root, not per-consumer
  patches.
- Truck factor: runbook at `docs/fingerprint-migration-adr017.md`; grep for
  `LEGACY-FINGERPRINT`.
