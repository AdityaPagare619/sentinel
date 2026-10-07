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

## 5. Serverless rotation seam (2026-10-07, rotation RFC)

Full ceremony: `docs/planning/rfc/security-rotation.md`. The code seam:

- `SENTINEL_OPERATOR_TOKEN_PREVIOUS` — the grace slot. `OperatorTokenStore`
  dual-accepts it in env mode; `secondary_staged` reports the overlap.
- The four-step ceremony (stage → prove → promote → retire) is a dashboard
  runbook on this tier; the env-var pair is the `{primary, secondary}`
  record and Vercel's deployment history is the audit trail.
- Residuals R1–R3 (break-glass needs shared state; no automated overlap
  nag; webhook/BYOK `*_PREVIOUS` not wired) are documented in the RFC, not
  fixed here.

## 6. Console token lifetime — the sessionStorage residual (OWASP)

**What the console does.** The operator pastes the token into the token
gate; the gate verifies it against the backend (`/api/decisions?limit=1`)
*before* storing — a wrong value never persists. The verified token lives
in `sessionStorage` under `sentinel.op.token`, is sent as
`Authorization: Bearer` on every API call, is dropped on any 401 (the gate
re-opens), and dies with the tab (sessionStorage lifetime). There is no
explicit sign-out button; closing the tab is the sign-out.

**The exposure, stated plainly (OWASP Session Management Cheat Sheet).**
OWASP: *"Do not store authentication tokens, session IDs, JWTs, refresh
tokens, or any credential in `localStorage` or `sessionStorage`. These
APIs are accessible to any JavaScript executing in the origin, so a single
XSS vulnerability discloses every token. Use `HttpOnly; Secure;
SameSite=Strict` cookies (preferred) or a Backend-for-Frontend (BFF)
pattern."* Our posture against that bar:

- **What we have:** sessionStorage (not localStorage) — the token does not
  survive the tab, does not touch disk by design, and is never in the URL,
  logs, or error messages. The console ships zero third-party scripts
  (verified: no remote `<script src>` in the console bundle), which shrinks
  — but does not eliminate — the script-injection surface. No CSP header is
  set today (residual: a strict CSP would further contain inline-injection).
- **What we don't have:** HttpOnly cookie binding. That requires the API to
  `Set-Cookie` and the console to send `credentials: include` — a
  cross-origin cookie flow (`SameSite=None; Secure`) plus CSRF analysis for
  the state-changing endpoints. It is a real hardening step, not a
  one-liner, and it changes the auth contract (Track 1 + Track 8).
- **Decision (this lane):** document, don't pretend. The residual is
  accepted for the current threat model (single operator, static console,
  no third-party JS) and recorded here so a future hardening lane can pick
  it up with the full contract change it deserves. The dishonest version
  would be claiming "session-only" as if it were "XSS-proof" — it is not.

**Operational notes.** The token gate's verify-before-store means a pasted
token is proven live at paste time. Any 401 anywhere drops the token and
re-opens the gate — a rotated-out token fails closed on the next call,
never silently. Operators on shared machines: close the tab when done;
there is no in-app sign-out to click.
