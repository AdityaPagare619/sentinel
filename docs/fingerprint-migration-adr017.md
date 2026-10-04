# ADR-017 fingerprint migration runbook — scheme v1 → v2

## What changed and why

`fingerprint_for` was `sha256("service|check|severity_in|region")` — env-blind.
A staging alert and a prod alert for the same check hashed identically, so the
correlator deduped them as one episode (the staging→prod collision: a prod SEV1
can inherit a staging disposition or be folded into a staging storm).

Scheme v2: `sha256("service|check|severity_in|region|env|cluster")`. The hash
itself changed — not a prefix convention — so no future consumer matching the
raw fingerprint can silently reopen the hole.

`env`/`cluster` are **required keyword-only** arguments: no call site may mint
an env-blind hash. Empty string is a real namespace ("unknown"), never a
wildcard.

## The migration (do this, in order)

**1. Build the manifest.** The v1 components are unrecoverable from the hash —
for every allowlisted v1 fingerprint, ops provides:

```json
{
  "<v1_fp>": {
    "service": "payments-api", "check": "http_5xx",
    "severity_in": "critical", "region": "us-east-1",
    "env": "prod", "cluster": "us-east-1-a",
    "team": "payments"
  }
}
```

`team` backfills the security-ban taxonomy metadata. Source it from your alert
inventory / CMDB — never invent it.

**2. Dry-run.**

```
python scripts/migrate_fingerprints_v1_v2.py \
  --bundle-dir <freshness-bundle> --manifest manifest.json --dry-run
```

**3. Migrate.**

```
python scripts/migrate_fingerprints_v1_v2.py \
  --bundle-dir <freshness-bundle> --manifest manifest.json \
  --window-days 30 --out-dir <freshness-bundle>
```

This dual-writes the bundle (timestamped backup kept beside it):

- `allowlist.json`: `fingerprint_scheme: 2`, env-namespaced **v2** entries,
  plus `legacy_v1: {window_ends_at, entries: [v1…]}`.
- `proofs/allowlist_attestations.json`: v2 attestations marked
  `re_attestation: "pending"` (same two humans, same evidence — mechanically
  re-derived), v1 attestations kept with `legacy_v1: true` (inert history).
- `legacy_allowlist.json`: `{window_ends_at, entries: {v1: v2}}` — point
  `SENTINEL_LEGACY_FINGERPRINTS` at this file.
- `re_attestation_manifest.json`: the per-entry checklist for the two humans.

Entries missing from the manifest are **skipped loudly, never dropped
silently**. Entries whose components don't reproduce their v1 fingerprint are
skipped (manifest integrity failure). Entries matching the security-ban
taxonomy are **refused** — a banned check is never carried over.

The window is bounded: `--window-days` defaults to 30, hard max 90.

**4. Deploy with the env var set.**

```
SENTINEL_LEGACY_FINGERPRINTS=<bundle>/legacy_allowlist.json
```

During the window, an alert whose v2 fingerprint misses the allowlist still
resolves via its v1 fingerprint — every resolution logs at WARNING:

```
LEGACY-FINGERPRINT-RESOLVED scheme=v1 fingerprint=… successor=… …
```

Grep for `LEGACY-FINGERPRINT`: the count must trend to zero as entries are
re-attested. The gate also exposes `gate.legacy_resolutions`.

**5. Re-attest.** The two humans re-attest each v2 fingerprint before
`window_ends_at` (checklist in `re_attestation_manifest.json`). Re-attested
entries clear the `pending` flag. **Pending entries go stale when the window
lapses — fail closed, no silent extension.**

**6. Close the window.** After `window_ends_at`: unset
`SENTINEL_LEGACY_FINGERPRINTS` (or delete the file). Legacy v1 fingerprints
stop resolving. Done.

## Rollback

The script keeps a timestamped backup (`<bundle>.bak-YYYYMMDDHHMMSS`).
Restoring the backup restores scheme-v1 behavior — but only together with the
pre-migration code (v2 fingerprints won't match a v1 bundle). Rollback is a
redeploy of both, not a config flip.

## Security-category ban (derived, not asserted)

`SECURITY_BAN_TAXONOMY_VERSION = "secban-v1"` in `src/sentinel/freshness.py`
defines the check-name/team pattern table. The ban is **derived at admission**
(`AllowlistAttestation.from_dict`): a banned check is rejected with
`ProofFormatError` and can never enter the allowlist — regardless of what the
entry asserts. A stored `security_category_ban` boolean disagreeing with the
derived taxonomy is itself a rejection (fail closed on disputed ban state).
Entries without check/team taxonomy are rejected (the migration backfills it).

Table changes require an ADR (Type 1 — the ban is a safety invariant).

## What "done" looks like

- `SENTINEL_LEGACY_FINGERPRINTS` unset; no `LEGACY-FINGERPRINT-RESOLVED` lines
  in the last 7 days of logs.
- Every allowlist attestation has `re_attestation` cleared.
- `docs/adr-decisions-2026-10-03.md` ADR-017 conditions (a) and (b) ratified on
  Petu's PR review.
