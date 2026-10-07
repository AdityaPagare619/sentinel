# Rotation — operator ceremony, API & console contract (Track 4 / C4)

> **Reality amendment (2026-10-07, docs W7).** The four-step dual-accept
> ceremony below is the **engine design** — it is real for
> server-persistent deployments where `RotatingKeyStore` holds state
> (`$SENTINEL_STATE_DIR`, 0600 files) and both primary and secondary can
> verify. **On the Vercel serverless tier it is not wired.** Every secret
> there arrives via dashboard env vars, so rotation is currently
> **flag-day**: swapping `SENTINEL_OPERATOR_TOKEN` (or any key) in the
> dashboard cuts over with no overlap (`{"primary": provisioned,
> "secondary": None}` — the serverless cold start cannot hold a staged
> secondary). Two-env-var dual-accept overlap is unwired (code change,
> flagged — not made in this docs lane). Until it is wired: stage the
> new value, verify auth against the tier with it, THEN swap, and confirm
> — never rotate blind. **Do not read the ceremony below as the Vercel
> tier's behavior; it is the design the tier does not yet implement.**

Every privileged secret in Sentinel — the operator bearer token (C1), the
webhook HMAC secret, the BYOK keys (`pagerduty_routing_key`, `jev_api_key`)
— lives as a **record** `{primary, secondary, generation}` and rotates
through an explicit four-step ceremony. Verification is **dual-accept**:
the primary OR the staged secondary verifies, so rotation never breaks
in-flight traffic.

## The ceremony

| Step | Action | Effect | Generation |
|---|---|---|---|
| 1. Stage | `stage_secondary(name, value)` | successor stored alongside the live primary; **both verify** (overlap begins) | unchanged |
| 2. Prove | `verify_secondary(name, value)` | proof-of-work: the staged secret is shown to actually function *before* it becomes canonical; read-only, audited | unchanged |
| 3. Promote | `promote(name)` | secondary → primary; old primary demoted to the grace slot (in-flight drain); **pre-rotation break-glass tokens die here** | **g+1** |
| 4. Retire | `retire(name)` | grace slot dropped; the old secret is cryptographically dead | unchanged |

Each step appends one audit record `{ts, actor, action, secret_name,
generation_before, generation_after, detail}` — names and generations
only, **never secret values**. Default sink: `<store>.audit.jsonl`
(0600). Only `promote` moves the generation; it is the revocation epoch.

Illegal transitions fail loud (`RotationError` → HTTP 409): promote with
no staged secondary, retire with no grace slot, staging a value identical
to the primary.

### The documented failure: delete-then-add

Deleting the old secret and then adding the new one passes through a
state where **nothing verifies** — total auth outage for in-flight
requests, and the new secret has had no proof-of-work. If the add fails
(crash, bad paste, validator reject) the outage is permanent until manual
recovery. The ceremony's overlap exists to make this state unreachable:
at every ceremony step at least one valid credential verifies (proven by
`test_ceremony_never_enters_outage`).

### Break-glass tokens

`issue_breakglass(name)` mints `bg_<random>` bound to the **current**
generation (only the SHA-256 is stored; the token is shown once).
Verification requires the recorded generation to equal the live
generation — so a leaked pre-rotation token dies automatically at the next
`promote`, with no per-token revocation action. `revoke_breakglass` kills
a single token mid-generation.

## HTTP API contract (for Track 8; auth wiring is Track 1's)

`POST /api/v1/keys/{name}/rotate` — C1-authed (`Authorization: Bearer
<operator-token>`; 401 → console "operator sign-in required" state).

| Body | Response |
|---|---|
| `{"stage":"stage_secondary","value":"..."}` | `200 {"name","generation","secondary_staged":true}` |
| `{"stage":"verify_secondary","value":"..."}` | `200 {"ok":true,...}` or `422 {"ok":false,"reason":"staged secret did not verify"}` |
| `{"stage":"promote"}` | `200 {"name","generation_before","generation_after"}` |
| `{"stage":"retire"}` | `200 {"name","generation","retired":true}` |
| `{"stage":"status"}` | `200 {"configured","secondary_staged","generation"}` |

Errors: `400` unknown stage · `404` unknown secret · `409` illegal
transition · `422` bad/missing value — always `{"error":"..."}`.
**No response ever contains a secret value.**

Implemented framework-free in `platform/server/rotation_api.py` as
`RotationService(registry)` where `registry = {secret_name: store}` maps
`operator_bearer`, `pagerduty_routing_key`, `jev_api_key`, `webhook_hmac`
to their stores. Wiring is one import in app.py (Track 1 / coordinator —
this lane does not touch middleware).

## Console contract (Track 8)

- The rotation panel per secret shows its **generation** and the staged
  state machine: `idle → staged → verified → promoted → retired`.
- Promote and retire are **separate deliberate buttons** (never one
  "rotate now" button — the proof-of-work step sits between them).
- Every transition surfaces its audit row: actor, timestamp, generation
  before → after.
- `secondary_staged: true` renders an "overlap active" badge — both old
  and new credentials currently verify.
- On 401 the console shows the C1 "operator sign-in required" state; the
  pasted token lives in memory/session scope, never localStorage.

## Store locations (server-side, 0600, never logged)

| Secret | Store file | Bootstrap |
|---|---|---|
| `operator_bearer` (C1) | `$SENTINEL_STATE_DIR/operator_token.json` | `ensure_operator_token()` mints `secrets.token_urlsafe(32)` once at first boot; `SENTINEL_OPERATOR_TOKEN` pre-seeds |
| `webhook_hmac` | `$SENTINEL_STATE_DIR/webhook_secrets.json` | `SENTINEL_WEBHOOK_SECRET` imported once (`ensure_from_env`); store record wins when present |
| `pagerduty_routing_key`, `jev_api_key` | `$SENTINEL_STATE_DIR/integrations.json` | existing BYOK settings surface; legacy plain-string entries decode to generation-1 records on read |

Webhook verification tries, in order: rotation-store primary →
rotation-store secondary → env bootstrap (`_webhook_sig_failure_reason_any`
in `src/sentinel/receiver.py`; the single-secret check underneath is
unchanged). Startup still refuses to boot with no webhook secret outside
the explicit onboarding flag (ADR-005 fail-closed).
