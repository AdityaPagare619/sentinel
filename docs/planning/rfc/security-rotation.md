# RFC: Rotation ceremony for the serverless tier (dual-accept via two env vars)

**Lane:** `lane/faang-security-20261007` · 2026-10-07 · TEAM 4 (Security)
**Status:** Proposed → implements dual-accept in this lane (auth.py env mode)
**Decision type:** Type 2 (reversible — env-var change; security-sensitive, hence RFC-grade review)
**Replaces:** the flag-day reality documented in `docs/rotation/ROTATION.md` ("Reality amendment")

## 1. Problem

The documented four-step dual-accept ceremony (stage → prove → promote → retire)
is real only for server-persistent deployments. On the Vercel serverless tier,
`OperatorTokenStore` keeps `{"primary": provisioned, "secondary": None}` in
memory (`platform/server/auth.py`), so rotation is flag-day: swapping
`SENTINEL_OPERATOR_TOKEN` in the dashboard cuts over with no overlap, no
proof-of-work, and no rollback — contradicting the ceremony the docs describe.

## 2. Anchors (web-verified 2026-10-07)

- **AWS IAM 2-key cap — CONFIRMED.** Max 2 access keys per IAM user; 2 keys exist
  only *during* the rotation window. Canonical order: create new → test →
  deactivate old → wait → delete old (deactivation is reversible; deletion is
  not). Single active key is the steady state.
- **Stripe overlap — CORRECTED.** The audit cited a "7-day overlap"; no such
  Stripe standard was found. Stripe's dashboard rotation lets the operator
  choose the old key's expiry at rotation time (**Now** = immediate delete, or a
  scheduled expiry). The honest anchor is therefore *operator-chosen overlap
  with a sane default*, not a vendor-mandated 7 days. Recommended default for
  us: **24–72h** (the AWS "deactivate → wait → delete" wait band), covering
  Vercel's env-propagation redeploy plus console-session drain.
- **RFC 6750 §3 / RFC 9110 §15.5.2 — CONFIRMED.** A 401 from a bearer-token
  resource server MUST carry `WWW-Authenticate: Bearer realm="...", error="invalid_token"`.

## 3. Design: two-env-var dual-accept

| Env var | Role | Steady state |
|---|---|---|
| `SENTINEL_OPERATOR_TOKEN` | **primary** (the token operators paste today) | set |
| `SENTINEL_OPERATOR_TOKEN_PREVIOUS` | **grace slot** (the predecessor, dual-accepted) | unset |

Verification accepts a candidate matching **either** value (constant-time
`hmac.compare_digest` per value, as today). This is the serverless encoding of
the keystore's `{primary, secondary}` record — the dashboard is the store, the
env-var pair is the record, Vercel's deployment history is the audit trail.

### The ceremony, mapped to dashboard actions

| Step | Operator action | System state | Generation analog |
|---|---|---|---|
| 1. **Stage** | Set `PREVIOUS` = new token (primary untouched) | both old (primary) and new (grace) verify — **overlap begins** | secondary staged |
| 2. **Prove** | Probe the live tier with the new token: `GET /api/v1/ops/health` with `Authorization: Bearer <new>` → expect 200 | proof-of-work: the successor is shown to authenticate *before* it becomes canonical | `verify_secondary` |
| 3. **Promote** | Set primary = new token, `PREVIOUS` = old token (one dashboard edit) | new canonical; old drains via the grace slot | `promote` (g+1) |
| 4. **Retire** | After the overlap window (24–72h), clear `PREVIOUS` | old token cryptographically dead everywhere | `retire` |

### Why per-instance state is safe here (the key insight)

Vercel applies env changes via redeploy: during propagation, instance A runs
(old primary, new grace) and instance B runs (new primary, old grace). Under
dual-accept the **union of accepted credentials is identical on both** — old
and new clients authenticate everywhere throughout the cutover. The
propagation window, the thing that makes flag-day dangerous, *becomes* the
overlap mechanism. No shared state (KV) is required for correctness — the
overlap is carried in the env vars themselves.

### Rollback (the new key is bad)

If promote reveals a bad new token (bad paste, lost clipboard): **before
retire, one dashboard edit swaps primary/`PREVIOUS` back**. The old token
never stopped verifying (it sat in the grace slot), so rollback is a
non-event, not an incident. The prove step (2) exists to make this path
nearly unreachable: a token that never authenticated cannot be promoted.

### Break-glass on serverless — honest residual, not designed

Break-glass tokens (`issue_breakglass`) need server-side shared state to bind
generation epochs. Serverless instances share nothing. Options were: (a) Vercel
KV/Upstash, (b) dashboard-as-break-glass. (a) is a new dependency + ₹0 risk +
out of this lane's scope; (b) is already true — the dashboard env edit IS the
break-glass path on this tier. **Decision: document (b); do not pretend the
file-backed break-glass works on serverless.** Filed as residual R1.

## 4. What this lane implements (code)

1. `platform/server/auth.py` — `OperatorTokenStore` reads
   `SENTINEL_OPERATOR_TOKEN_PREVIOUS`; env-mode `verify()` dual-accepts;
   `secondary_staged` exposed for status surfaces. (Fail-closed preserved:
   empty/missing `PREVIOUS` = no grace slot, exactly today's behavior.)
2. `platform/server/app.py` — `_unauthorized` adds
   `WWW-Authenticate: Bearer realm="sentinel-operator", error="invalid_token"`
   (body unchanged — the console's C1 contract keys off the body verbatim).
3. `deploy/vercel/api/index-prod.py`, `deploy/vercel/api/index.py` —
   auth moves **before** the `/api/stream` 501 refusal (the main app already
   enforces this order; the wrappers were the exception). Unauthenticated
   `/api/stream` now 401s like every other `/api/*` path: no path oracle.
4. Tests: env dual-accept (primary ok / previous ok / neither 401 /
   `secondary_staged` flag), `WWW-Authenticate` on 401, stream-behind-auth
   in both wrappers.

Not implemented (residuals, documented not hand-waved):
- **R1.** Break-glass on serverless (see §3).
- **R2.** Overlap-window SLA enforcement: nothing on the tier nags the operator
  to retire `PREVIOUS` after 72h (no cron on Hobby). Mitigation: the rotation
  panel (Track 8) should render "overlap active — staged at <deploy time>"
  from the status surface; until then the ceremony checklist carries it.
- **R3.** Same two-var pattern for `SENTINEL_WEBHOOK_SECRET` / BYOK keys is
  designed but not wired (extension path: `*_PREVIOUS` vars + the same
  dual-accept verify loop). Webhook HMAC dual-accept already exists
  server-side via `webhook_secret_store`; the env seam is the gap.

## 5. Pre-mortem ("the rotation failed at 3am — why?")

1. **Operator did flag-day from muscle memory** (swapped primary, never set
   `PREVIOUS`): in-flight console sessions 401 mid-shift. *Mitigation:*
   ceremony checklist in the runbook; status surface shows `secondary_staged`.
2. **New token promoted without the prove step, then lost** (bad paste):
   operator locked out, dashboard is the only recovery. *Mitigation:* prove is
   a checklist gate, not a suggestion; rollback path (§3) documented next to it.
3. **`PREVIOUS` never retired** (overlap becomes permanent, old token lives
   forever — the AWS "forgotten key" failure). *Mitigation:* 24–72h window in
   the checklist; R2 tracks the automated nag.

## 6. Alternatives considered and rejected

- **Single env var + redeploy (status quo):** rejected — it IS the flag-day
  outage this RFC exists to kill; contradicts the documented ceremony.
- **Vercel KV for shared rotation state:** rejected for now — new paid-tier
  surface, ₹0 risk, and unnecessary: §3 shows env vars alone give safe
  dual-accept. Revisit only if break-glass (R1) becomes required.
- **File-backed keystore on serverless (/tmp):** rejected — per-instance,
  cold-start-wiped; the audit already killed this for kill-switch state, same
  physics applies.

## 7. Verification receipts

- Dual-accept unit tests (new): failing-before/passing-after on this branch.
- 20-route unauth 401 probe re-run post-change (verify-before-trust).
- `secrets-grep.sh` green (gate stage 1) + full `pre-pr-gate.sh` before merge.

---
*Verification receipts: Stripe rotation UX — Stripe dashboard docs (operator-chosen
expiry at rotation time); AWS IAM 2-key cap + deactivate→wait→delete —
AWS IAM User Guide + practitioner runbooks (2026-10-07 web search); RFC 6750 §3
challenge grammar — RFC text + gofiber/leodip conformance commits.*
