# Track 1 — Platform operator auth (P0) — DESIGN

Branch: `lane/build-t1-auth` (from `program/full-build` @ aee5f33).
Contract: C1. Consumers: T4 (rotation), T7 (sign-off re-walk), T8 (UI sign-in).

## Threat (reproduced, not theorized)

`/api/v1/integrations/keys` POST/DELETE, `test-page`, and `/api/simulate`
had ZERO auth; CORS defaulted to `*`. Walked pre-fix against the real
WSGI app (`/tmp/exploit_prefixture.py`): OPTIONS preflight from
`https://evil.example.com` → 204 with `ACAO: *`; POST with an
attacker-controlled 32-hex routing key → 200; stored key read back =
attacker's. After the key swap, every subsequent test-page goes to the
attacker's PagerDuty service. One malicious page visit = silent key
takeover.

## Decisions

1. **Bearer before everything.** `__call__` order: preflight → health →
   auth → admission gate → shed → router. Auth rejects BEFORE the gate so
   unauthenticated floods can't occupy slots, and a 503 can never mask a
   401. Preflights stay unauthenticated (browsers never send Authorization
   on preflight) — preflight alone grants nothing.
2. **401 body is exactly `{"error":"unauthorized"}`**, deliberately NOT the
   house envelope. Track 8 keys off this body verbatim for the
   "operator sign-in required" state. CORS headers still attach so the
   cross-origin console can READ the 401.
3. **Health probes are the ONLY exemption** (`/api/v1/health/live`,
   `/api/v1/health/ready`), answered before the gate so orchestrator
   probes never 503 under load. Everything else under `/api/` — keys,
   test-page, simulate, stream, decisions — is authed.
4. **Token store** (`platform/server/auth.py`): `secrets.token_urlsafe(32)`
   at first boot, atomic write, 0600, corrupt-file self-heals with a fresh
   token (no boot loop). Plaintext exists in-process exactly once
   (`first_boot_token`) for the setup banner; never logged, never in a
   response (swept by `test_token_never_in_responses` + diff grep).
5. **C4 seam now**: file schema `{tokens: {primary, secondary},
   generation}` + constant-time dual-accept `verify()`. Track 4 adds
   rotate (add secondary → verify → promote → retire) without touching
   this module's contract.
6. **CORS default** = `{https://AdityaPagare619.github.io}` (was `*`).
   `*` requires an explicit `--cors-origins=*` (env `SENTINEL_CORS_ORIGINS`
   counts as explicit) AND prints a loud `!`-bordered startup warning.
   Empty string = CORS off (reverse proxy owns policy).
7. **First-boot UX**: `=`-bordered banner with the token, storage path,
   and the `Authorization: Bearer` usage line — printed exactly once,
   when the file is created.

## What was deliberately NOT done

- No password/login UI, no sessions, no expiry — single-operator bearer
  per install is the contract; rotation (T4) is the revocation story.
- No hashing of the stored token: the 0600 file IS the secret store
  (same pattern as `integrations.json`); hashing buys nothing here and
  would complicate T4's dual-accept promotion.
- `/` static UI stays public (static assets carry no capability); every
  `/api/*` behind it is authed.

## Cross-domain touchpoints (whole-system)

- **T4 rotation**: `OperatorTokenStore.verify()` dual-accepts
  primary/secondary; `generation` counter present; file at
  `<state-dir>/operator_token.json` (override: `--token-file` /
  `SENTINEL_OPERATOR_TOKEN_FILE`).
- **Serverless (Vercel, reference-only)**: `SENTINEL_OPERATOR_TOKEN` env
  provisions the token with no file and no banner; unwritable state dir
  degrades to an in-memory token + loud warning (never a boot crash).
  `deploy/vercel/api/index.py` warns when ephemeral; README documents
  the dashboard env var. DEPLOY ACTION: set the env var when the hosted
  demo is (re)built, or the console cannot authenticate.
- **T8 console**: on ANY 401 (`{"error":"unauthorized"}`) show
  "operator sign-in required" + single paste field; store token in
  memory/session scope (never localStorage); send as
  `Authorization: Bearer <token>` on every /api/* fetch incl. SSE.
- **T7 sign-off**: re-walk `/tmp/exploit_prefixture.py` attack chain
  (pre-fix 200/key-overwritten → post-fix 401/key-untouched) + the 17
  committed tests in `platform/server/tests/test_auth.py`.

## Falsifiers

- Exploit test green ≠ auth correct: also assert the stored key is NOT
  the attacker's (not just the 401).
- 401 must not be envelope-shaped (Track 8 contract) — asserted byte-exact.
- Token must not appear in ANY response — `test_token_never_in_responses`
  sweeps status + keys endpoints; diff grepped for log/print leaks.
- Second boot must reuse the token (no lockout); corrupt file must
  self-heal (no boot loop) — both tested.

## Drive-by (separate commit)

`platform/server/tests/fixtures.py` had pre-existing fixture drift:
engine's `fingerprint_for` now requires kw-only `env`/`cluster`, fixtures
passed 4 positional args → every store/server/guard test using
`make_store_db()` errored. Fixed with `env="test", cluster="test"`.
