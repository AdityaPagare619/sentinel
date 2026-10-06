# INFRA WORKER 1 — Domain Research: BYOK Done Right + Ingest-Edge Auth

**Date:** 6 Oct 2026 | **Lane:** `lane/domain-research-infra` (branch `lane/domain-research-infra`, origin/main @ 8c0893b) | **Mode:** research-only, no code changes, ₹0 operating budget

## Adversarial posture (used throughout)

Two mindsets were kept active. **Adversary:** if I stole the user's PagerDuty routing key from our store, what could I do? Answer: page their entire on-call org, trigger incidents, burn trust. If I captured a webhook delivery, could I replay it? Answer: yes, within 300s; idempotent dedup blunts repeat-impact but not the first re-fire. **Operator at 3 AM:** key leaked — I need one button (rotate), proof the old key is dead everywhere, and zero dropped pages during the switch. Every gap below is rated against this.

---

## TOPIC 1 — BYOK DONE RIGHT

### OUR STATE (verified against `src/sentinel/`)

- **Storage:** user-entered PagerDuty routing keys live in a "user integrations store"; resolution chain in `forwarder.py`: explicit `key_resolver` → user integrations store → `PD_ROUTING_KEY` env → ctor default → unconfigured. Per-outbox key pins are supported.
- **Display:** last-four only in the UI; secret values never echoed.
- **Logging:** the key value is never logged; only the *source* of the resolution is logged (`forwarder.py` docstring, line 876-877: "we never log request bodies, and any Authorization header is stripped before request logging"). `integrations.py` has `sanitize_error` for scrubbing keys from error strings.
- **Fail behavior:** simulated mode short-circuits before key resolution/network — so BYOK keys are never touched by simulated deliveries. R-2 (UI keys not reaching the production forwarder) and R-3 (unauthenticated `/v2/enqueue`) are fixed.
- **What we do NOT have:** no encrypted-at-rest story for the store, no rotation UX (no "rotate" button or overlap mechanism), no revocation beyond deleting the value, no key-scoped audit trail ("who used this key, when, for which page" — the forwarder logs the *source* but no audit log of key usage), no compromised-key response playbook, no show-once ceremony at key entry.

### REAL WORLD (sourced)

**Stripe restricted keys — the gold standard for third-party-key onboarding.**
Stripe's restricted keys (`rk_live_…`) carry per-resource read/write/none permissions set in the dashboard; the key is revealed **only once** after creation and must be copied immediately; every API error caused by a missing permission names the missing scope explicitly rather than failing silently. Sources: [GitHub — jpisgeek/vellum-oracle stripe-app-setup SKILL](https://github.com/jpisgeek/vellum-oracle/blob/HEAD/skills/stripe-app-setup/SKILL.md); [GitHub — steve-lomnes/cosimo stripe.md](https://github.com/steve-lomnes/cosimo/blob/HEAD/docs/stripe.md).

**Cloudflare API tokens — least privilege + show-once + TTL/IP scoping.**
Custom tokens combine a permission, a level (Read/Edit), and resource scoping (specific account/zone), plus optional TTL and client-IP restrictions. The token value is **shown once**; a later GET never returns it. Sources: [GitHub — 0xplayerone/pi-cloudflare SKILL](https://github.com/0xplayerone/pi-cloudflare/blob/HEAD/skills/cloudflare-api-token/SKILL.md); [GitHub — alos-no/cloudflare.net permissions.md](https://github.com/alos-no/cloudflare.net/blob/HEAD/docs/articles/permissions.md).

**GitHub PATs — forced expiry as rotation policy.**
Fine-grained PATs have **no "no expiry" option** (max 1 year), forcing a rotation cadence; values are shown once, and teams run 30–90-day forced rotations deliberately. Source: [GitHub — microsasa/cli-tools token-rotation.md](https://github.com/microsasa/cli-tools/blob/HEAD/docs/operations/token-rotation.md); [GitHub — wisechef-ai/loopskill-api github-federation-token-rotation.md](https://github.com/wisechef-ai/loopskill-api/blob/HEAD/docs/runbooks/github-federation-token-rotation.md).

**Vercel env secrets — write-only after save.**
Vercel encrypts all env values at rest and offers a **Sensitive** type whose value is write-only after saving — not even Vercel's own API can return it — with changes tracked in an Activity Log. Sources: [vercel.com/docs/environment-variables](https://vercel.com/docs/environment-variables?ref=blog-hookdeck-vercel); [infisical.com](https://www.infisical.com/blog/managing-vercel-environment-variables-at-scale).

**1Password Connect / op CLI — secrets never leave the vault.**
The `op run` pattern injects secrets into a subprocess's environment at runtime: never stored on disk, cleared when the process exits. Sources: [GitHub — henrychong-ai/ai 1password SKILL](https://github.com/henrychong-ai/ai/blob/HEAD/claude-code/skills/1password/SKILL.md); [GitHub — spenserhale/skills 1password-cli](https://github.com/spenserhale/skills/blob/HEAD/skills/1password-cli/SKILL.md).

**Storage consensus — envelope encryption.**
Industry consensus: encrypt every credential at rest with AES-256-GCM, never treat base64 as encryption, use envelope encryption (unique DEK per secret, KEK in a KMS/HSM or at minimum loaded from a secrets manager at deploy time — never a plain `.env`), isolate tenants, rotate on schedule and on incident. Source: [dev.to — corsairdev, MCP server credential encryption best practices](http://dev.to/corsairdev/how-to-store-api-credentials-for-ai-agents-without-the-llm-ever-seeing-them-2i5j).

### THE GAP

| # | What we do | What the best do | Gap |
|---|---|---|---|
| G1 | Keys stored in a "user integrations store" (no stated encryption) | AES-256-GCM envelope encryption at rest; KEK in KMS/HSM; ciphertext-only DB dumps | **No at-rest encryption story** — a sqlite file copy = full key recovery |
| G2 | Last-four display | Last-four + show-once ceremony + write-only-after-save (Vercel) | Show-once and write-only are absent; if the store is readable later, so is the key |
| G3 | Delete value to revoke | One-click rotate w/ overlap; forced expiry (GitHub PATs); TTL on tokens (Cloudflare) | **No rotation UX and no forced-expiry nudge** — a 3 AM key leak has no runbook, no "old+new both valid" overlap |
| G4 | Resolution *source* logged | Stripe names missing scopes; Vercel Activity Log tracks every change | No key-scoped **audit trail** (created/rotated/used-for-which-page/deleted) |
| G5 | `sanitize_error` scrubs keys from error strings | 1Password: secret never leaves the vault; never in run history or logs | Good on logs — but no scope-binding: a routing key pasted here could be replayed against any PagerDuty event endpoint |
| G6 | env var / ctor default fallbacks exist (PD_ROUTING_KEY) | Cloudflare: separate tokens per app/env; "Never commit" discipline | Operator-level keys in env/ctor-defaults can outrank user keys; no warning when a fallback fires, so a user can *think* their key is in use when it isn't |

### FIX DIRECTION (all at ₹0)

1. **At-rest encryption (stdlib-only).** Wrap stored keys with AES-256-GCM from the `cryptography` package already approved in `requirements.txt` (the dependency-law single exception) — envelope shape: per-key random DEK, DEK wrapped by KEK; KEK loaded from a single `SENTINEL_MASTER_KEY` env var that is **required in production** (fail-closed if unset). DB backups then contain ciphertext only.
2. **Write-only store contract.** Mirror Vercel Sensitive: once saved, the value is never returned by any read API — UI and API only ever get last-four. Code-level guarantee, no infra needed.
3. **Rotation runbook in the UI (3 AM operator).** (a) "Rotate" flow — paste new key → both old+new accepted for a configurable overlap window (default 24h, per Stripe's rolling-secret window) → old auto-invalidated; (b) explicit "Revoke" that kills immediately; (c) show-once ceremony at entry. All pure code.
4. **Key-scoped audit log.** Append-only entries: key added (by whom/session), rotated, per-outbox pin changed, used-for-page (timestamp + dedup ID, never the value), revoked. Reuses the existing eventlog machinery. Today we cannot prove to a user that their key was only used for their pages.
5. **Fallback firing alarm.** When resolution falls through to `PD_ROUTING_KEY`/ctor default while a user key exists or in production mode, emit a loud, visible warning — silent fallback is how misrouted pages happen.
6. **Compromise playbook (docs, ₹0).** Codify the 3 AM sequence: revoke-at-PagerDuty-side (their dashboard rotates an integration key instantly) → Sentinel-side revoke → audit-log review → new key entry.

**Honest gaps:** PagerDuty's dashboard-side masking of integration keys not verified from public docs; Netlify-specifics not verified (Vercel was the verifiable exemplar); 1Password Connect's server-side rotation story partially verified.

---

## TOPIC 2 — INGEST-ENDPOINT AUTH AT THE EDGE

### OUR STATE (verified against `src/sentinel/receiver.py`)

- Scheme: `X-Sentinel-Timestamp` + `X-Sentinel-Signature: sha256=<hex>`, HMAC-SHA256 over `"<timestamp>.<raw body>"`, checked with `hmac.compare_digest`; `|now − ts| ≤ 300s` (`SIGNATURE_MAX_SKEW_S`). Legacy timestamp-less raw-body HMAC **rejected in production** (onboarding mode accepts it loudly so migrating senders don't go dark).
- Fail-closed in production. Silence/resolve direction fail-closed in every mode.
- `/v2/enqueue` and `/webhook/generic` both carry this; R-3 fixed the previously-unauthenticated enqueue.
- **Known residual:** 300s window, no nonces; relies on idempotent ingest dedup — a captured request is valid-replayable for up to 5 minutes.
- **Unknowns:** missing-`SENTINEL_WEBHOOK_SECRET`-in-production behavior (verify fail-closed → 503, not fail-open); multi-secret rotation story; per-endpoint secret versioning.

### REAL WORLD (sourced)

**Stripe — the timestamped-signature archetype.**
Signed string `"<t>.<raw body>"`, header `Stripe-Signature: t=<unix>,v1=<hex>[,v1=<hex>…]`, HMAC-SHA256 hex. Default tolerance **300s**; `t` is *inside* the signed string so the window is authenticated. Rotation: rolling an endpoint secret keeps the old one live for up to **24h**, and Stripe sends one `v1` per active secret — **any matching `v1` is accepted**, so a roll never drops deliveries. `v0` is test-only and **never** accepted (downgrade-attack prevention). Sources: [duke5am/webhook-signature-verify PROVIDER-SCHEMES.md](https://github.com/duke5am/webhook-signature-verify/blob/HEAD/PROVIDER-SCHEMES.md); [drt-hub/drt-web](https://github.com/drt-hub/drt-web/blob/HEAD/synced-docs/guides/using-webhook-trigger.md); [dsb-117/brainblast](https://github.com/dsb-117/brainblast/blob/HEAD/examples/stripe-privy/components/stripe.md).

**Svix / Standard Webhooks spec — the consensus protocol.**
Headers `webhook-id`, `webhook-timestamp`, `webhook-signature` (space-delimited `v1,<base64>` list — multiple signatures = key rotation built into the wire format). Signed content **`{webhook-id}.{webhook-timestamp}.{raw body}`** — the message ID is signed, which our scheme lacks. Svix libraries reject timestamps more than 5 minutes away. Ed25519 asymmetric mode (`v1a`) exists. Sources: [svix/svix-docs why.mdx](https://github.com/svix/svix-docs/blob/HEAD/content/receiving/verifying-payloads/why.mdx); [samber/developer-platform-skills signing-scheme-reference.md](https://github.com/samber/developer-platform-skills/blob/HEAD/skills/webhook-platform-design/references/signing-scheme-reference.md).

**GitHub — the no-timestamp scheme, and how they compensate.**
`X-Hub-Signature-256: sha256=<hex>` over the **raw body only — no timestamp**, so a captured delivery is replayable indefinitely *unless* the receiver dedupes on `X-GitHub-Delivery` (unique per delivery). "Never use a plain `==` operator". Source: [duke5am PROVIDER-SCHEMES.md](https://github.com/duke5am/webhook-signature-verify/blob/HEAD/PROVIDER-SCHEMES.md).

**Slack — signed base-string with timestamp, 5-min window.**
`v0=HMAC-SHA256(secret, "v0:<ts>:<raw body>")`, `|now − ts| > 300s` → reject. Sources: [hookdeck/webhook-skills slack-webhooks SKILL](https://github.com/hookdeck/webhook-skills/blob/HEAD/skills/slack-webhooks/SKILL.md); [orkspace/orkestra security/09-webhook-verification.md](https://github.com/orkspace/orkestra/blob/HEAD/documentation/security/09-webhook-verification.md).

**PagerDuty v3 webhooks — multi-signature rotation at the wire level.**
`X-PagerDuty-Signature: v1=<hex>,v1=<hex>,…` — HMAC-SHA256 of the **raw body only** (no timestamp), computed per signing secret, comma-concatenated **explicitly "to allow for a zero-downtime secret rotation"**. Source: [pagerduty/developer-docs docs/webhooks/04-Signatures.md](https://github.com/pagerduty/developer-docs/blob/HEAD/docs/webhooks/04-Signatures.md).

**Rotation pattern consensus.** Dual-secret verification during rotation: (1) generate new secret, (2) receiver accepts either, (3) provider switches, (4) drain in-flight (5–10 min), (5) remove old. Multi-signature headers are the wire mechanism. Sources: [intense-visions/harness-engineering api-webhook-security SKILL](https://github.com/intense-visions/harness-engineering/blob/HEAD/agents/skills/codex/api-webhook-security/SKILL.md); [himanshu231204/api-engineering-handbook](https://github.com/himanshu231204/api-engineering-handbook/blob/HEAD/docs/09-realtime-and-webhooks/webhook-signature-verification.md).

**Fail-open vs fail-closed.** The industry failure mode: missing/unset secret → verification silently skipped → endpoint accepts everything. Accepted fix: missing secret in production → **503 + loud log** (never 200, never silent), bad signature → 401, detailed *why* only in internal logs. Sources: [leonagoel/hybrid-recommender#594](https://github.com/leonagoel/hybrid-recommender/issues/594); [rindogatan/deal-room commit 532e1ee](https://github.com/rindogatan/deal-room/commit/532e1ee4067652d17827e4fcadd48596b72d28d6); [n8n advisory GHSA-5m98-cgcr-xx3q](https://github.com/n8n-io/n8n/security/advisories/GHSA-5m98-cgcr-xx3q).

**Secret storage on the receiving side.** "Treat a leaked webhook secret with the same severity as a leaked API key, since it allows an attacker to forge arbitrary events" — secrets manager or env, never code, per-endpoint unique secrets. Source: [himanshu231204/api-engineering-handbook](https://github.com/himanshu231204/api-engineering-handbook/blob/HEAD/docs/09-realtime-and-webhooks/webhook-signature-verification.md).

### THE GAP

| # | What we do | What the best do | Gap |
|---|---|---|---|
| G1 | 300s, no nonce, dedup as the replay backstop | 300s is exactly Stripe/Svix/Slack's standard window; **but** their dedup keys on *signed* IDs | Our 300s is **industry-standard, not weak** — but our dedup keys on alert identity, not on a *signed* delivery ID |
| G2 | Signed content: `<ts>.<raw body>` | Standard Webhooks signs `{id}.{ts}.{body}` — the delivery ID is bound into the MAC | **No signed delivery/nonce ID.** Attacker can replay with a fresh header and walk past naïve dedupe |
| G3 | One active signing secret (unknown rotation story) | Multi-signature headers + dual-accept during rotation + 24h overlap | **No zero-downtime rotation mechanism** |
| G4 | Fail-closed in production (stated) | 503 on missing secret + loud internal log + metric with reason labels | Need to verify the *missing-secret* path returns 503 and is metrics-instrumented; legacy-mode must be unreachable in prod by construction |
| G5 | Legacy timestamp-less HMAC rejected in prod, accepted loudly in onboarding | Stripe *never* accepts `v0` in live mode | Onboarding-accepts-loudly must be provably impossible in production (env-gated, startup-asserted) |
| G6 | Per-endpoint? | Per-endpoint/registration secrets | If one global secret covers both routes, a leak on either forces rotating both |

### FIX DIRECTION (all at ₹0)

1. **Add a signed delivery ID.** Extend the signed string to `<delivery-id>.<timestamp>.<raw body>` with a new `X-Sentinel-Delivery-Id` header, matching the Standard Webhooks construction. Version the scheme explicitly (`v1` prefix) so legacy vs new are never ambiguous. Closes G2, hardens G1's residual, zero infra cost.
2. **Multi-secret rotation (wire-level).** Allow multiple `v1=` values in the signature header; receiver config holds `[current, previous]`; accept if any matches; remove previous after a drain window (24h default). Pure code, closes G3.
3. **Fail-closed verification with metrics.** Assert at startup: production + missing/empty webhook secret → refuse to boot (or 503 on endpoints), loud log. Instrument `signature_failures_total{reason}` (`missing | malformed_timestamp | stale | invalid | missing_secret`). Keep external responses generic. Startup-assert onboarding-legacy mode is off in production.
4. **Per-endpoint secrets.** Support distinct secrets per route via config map; a leak on the generic webhook doesn't force rotating the enqueue path. Pure config/code.
5. **Clock-skew guidance + tolerance as config.** Keep 300s default (industry standard) but env-configurable; document the NTP requirement.
6. **Secret storage on the receiving side.** Same treatment as BYOK keys: at-rest encryption, never in code, treat a leaked webhook secret as a leaked API key.

**Honest gaps:** missing-`SENTINEL_WEBHOOK_SECRET`-in-production path not fully verified from code (verify-before-ship); existing rotation mechanism in config layer not verified.

---

## Cross-cutting principal verdicts

1. **Our 300s-no-nonce window is industry-standard, not weak.** Stripe, Svix, and Slack all use exactly 300s with no nonce. The residual is bounded and standard. The *unstandard* part is that our dedup key is not bound into the MAC — signed delivery ID upgrades us to the Standard Webhooks bar with zero infra.
2. **The sharpest asymmetry between us and the best is rotation, in both topics.** Stripe/PD/Svix built rotation into the wire format; GitHub forces rotation via expiry. We have no rotation story in either topic. That is the single biggest ₹0-implementable upgrade: dual-accept + overlap + audit + one-click UI rotate.
3. **The second asymmetry is the audit trail.** Vercel's Activity Log, Stripe's explicit permission errors, Cloudflare's token model — all assume you can answer "what happened with this key." We cannot, today, on either side.
4. **Threat ranking (adversary's view):** (a) webhook secret leak ⇒ forged pages/incidents — mitigated by per-endpoint secrets + rotation; (b) BYOK store read ⇒ user's PD key — mitigated by at-rest encryption + write-only contract; (c) 5-min replay of a captured delivery ⇒ duplicate page — mitigated by signed delivery ID + dedup. All three fixes are code-only at ₹0.
5. **What NOT to do:** don't build KMS integration, don't add a secrets-manager dependency, don't widen the replay window "for convenience," don't keep the onboarding legacy-accept path reachable in production, and don't treat the 300s window as the whole replay story — the timestamp proves freshness, the signed ID proves uniqueness, you need both.
