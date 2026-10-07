# State directory & operator_token.json — honest operator notes

**Lane:** `lane/fix-p2-hygiene` · 2026-10-07 (brutal-audit §5 follow-up:
security P1 keystore-at-rest, P2 state-dir hygiene)
**Status:** implemented on this branch

## 1. SENTINEL_STATE_DIR — where runtime state lives

| | |
|---|---|
| Env var | `SENTINEL_STATE_DIR` |
| Default (2026-10-07+) | `~/.sentinel/state` (outside the repo tree) |
| Old default (pre-2026-10-07) | `./sentinel-state` (relative to CWD — inside the repo when run from the repo root) |
| Override | `--state-dir` flag on both servers; `SENTINEL_INTEGRATIONS_FILE` / `SENTINEL_OPERATOR_TOKEN_FILE` for individual files |

**Why the default moved.** The old default put live secret files next to
the source tree. During the 2026-10-07 audit wave a local dev run created
`platform/server/sentinel-state/operator_token.json` — a live-format
credential inside the repo (gitignored, 0600, but in the tree). State is
deployment data, not source; the new default keeps it out of the tree by
construction. The single source of truth is
`default_state_dir()` in `src/sentinel/keystore.py` (engine) and
`platform/server/keystore.py` (platform) — every `--state-dir`,
`WebhookSecretStore`, `operator_token_store()`, and `IntegrationStore`
default flows through it.

**Upgrading installs.** The store moves on next boot: a fresh operator
token is minted under the new dir and shown once in the first-boot banner
— re-paste it into the console sign-in field. The old
`./sentinel-state/` can be deleted after confirming the new token works,
or kept reachable explicitly via `SENTINEL_STATE_DIR=./sentinel-state`.
(This is reversible and announced — no silent invalidation.)

**The state dir is never deployed or bundled.**
- `deploy/vercel/build-bundle-prod.sh` copies only `*.py` sources and now
  carries a **state-exclusion guard**: the build fails if
  `operator_token.json`, `integrations.json`, `webhook_secrets.json`,
  `sentinel-state/`, `__pycache__/`, or any `*.audit.jsonl` is found in
  the output.
- `deploy/gh-pages/build-static.py` pre-renders the static console with a
  `TemporaryDirectory` state dir — nothing persists into the published
  bundle (verified: no state paths are copied to the output).
- `.gitignore` excludes `sentinel-state/`.

## 2. operator_token.json — the local boot artifact (NOT the production token)

**What creates it.** `platform/server/keystore.py::ensure_operator_token()`,
called once per boot by `OperatorTokenStore` (`platform/server/auth.py`).
First boot mints `secrets.token_urlsafe(32)` (or installs the
`SENTINEL_OPERATOR_TOKEN` pre-seed); later boots only verify against it.

**Permissions & write discipline.** Atomic write (tempfile + `os.replace`
in the same directory), `0600` on the file and the audit log. The
plaintext token is returned exactly once — to the first-boot setup banner
printed on stdout — and is never logged, never in an HTTP response, and
(as of 2026-10-07) never stored: the file holds the **SHA-256 digest**
record `{"primary_sha256": hex, "generation": n}`, not the token.

**Why it is NOT the production token.**
- Production (Vercel) provisions the operator token via the
  `SENTINEL_OPERATOR_TOKEN` **environment variable** (set in the Vercel
  dashboard). With the env var set, `OperatorTokenStore` keeps the token
  in memory (`self._mem`) — no file is created, no banner is printed.
- The file is a **per-install local boot artifact**: it authenticates the
  operator of one self-hosted install against that install's own server.
  Copying it to another machine authenticates nothing there (each install
  mints its own).
- If the state dir is unwritable (serverless without the env var), the
  store degrades to an **ephemeral** in-process token and prints a loud
  warning — the operator must provision `SENTINEL_OPERATOR_TOKEN`.

## 3. Storage classes — what is hashed vs what stays plaintext

**VERIFY-ONLY → SHA-256 at rest.** The server only ever *compares* a
presented candidate; it never needs the value back. Today: the
`operator_bearer` token (platform tier) and break-glass tokens (both
tiers — hash-only since Track 4). Opt in per name via
`RotatingKeyStore(..., hash_at_rest={"name"})`. A leaked file yields no
secret; `candidates()` raises `TypeError` for these records (fail closed)
instead of returning digests that a caller might misuse.

**PRESENTATION → plaintext in the 0600 file (deliberate).** The server must
*send* the value outward, so the plaintext must be recoverable:
- BYOK `pagerduty_routing_key` — POSTed to the PagerDuty Events API.
- BYOK `jev_api_key` — sent to the Typesafe API.
- `webhook_hmac` — used as the HMAC **key** for inbound signature checks
  (`receiver._webhook_sig_failure_reason_any`); hashing is
  cryptographically impossible there without changing the wire protocol.

This is the `gh` `~/.config/gh/hosts.yml` model: a client credential the
client must present. Hashing presentation secrets would not change the
threat model (the process needs the value in memory anyway) and would
break the protocols. The distinction is enforced by policy
(`hash_at_rest`), not by hope.

## 4. Residual risks (honest)

- Unsalted SHA-256 is used. This is safe **only** because stored values
  have ≥128-bit entropy (minted `token_urlsafe(32)`; validators enforce
  ≥16-char operator/webhook secrets). If a low-entropy secret is ever
  marked verify-only, add a salt — the format versioning
  (`primary_sha256` keys) gives room for a `primary_sha256_salted` form.
- Migration is lazy: pre-2026-10-07 plaintext records are rewritten as
  digests on the next write/migration pass (`migrate_hash_at_rest`,
  audit-logged), not all at once. A stale plaintext record remains
  readable (and verifiable) until then — by design, to avoid lockouts.
- `SENTINEL_STATE_DIR` pointing at a shared/network path is not defended
  against — 0600 perms are the only control there.
