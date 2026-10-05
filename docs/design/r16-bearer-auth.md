# R-16 — Platform write-surface auth + versioning unification

> **Status: design only (deputy authority).** R-16 proceeds under deputy
> authority per the 12H-PLAN (no Type-1 gate — the mechanism, bearer
> token, is the stated recommendation in PIPELINE-REVISION.md). This
> wave is **design/docs only**: no `src/` changes, no behavior changes.
> The build lands in Phase 1 under the Vault-ack review routing.

**Lane:** plat-2 (R-16 design) · **Branch:** `lane/prep-r1-policy`
**Date:** 2026-10-05 IST · **Type:** 1 — the write surface is the
secret-handling tier (PagerDuty key save/delete, simulated toggle, real
test pages); its auth mechanism and version contract are public-surface
decisions. Deputy authority covers the *design*; the build still needs
Vault ack before merge per OPERATING-RULES §4.3.

## 1. The problem, in one paragraph

PIPELINE-REVISION.md R-16 (`platform-api.md` P0-1/P0-2, M1/M4/M5
CRITICAL): **zero auth on the secret-handling surface** — anyone with
network access can rotate the PagerDuty key (pages go nowhere during a
SEV), flip the simulated toggle, or fire real test pages. Verified in
this lane: `platform/server/app.py` has no bearer/token/401 logic at
all — the only occurrence of "Authorization" is in the CORS
*allowed-headers* list (line 144). Compounding: mixed versioning
(unversioned reads + `/api/v1` writes, plus the unversioned write
`POST /api/simulate`) with no migration path for the
independently-deployed Pages console, and the revision program's
companion finding of `CORS *` on the same surface.

## 2. The five write endpoints (verified against `app.py:183-201`)

| # | Method + path | What it does | Versioned today? |
|---|---|---|---|
| 1 | `POST /api/simulate` | Pure recompute over stored probabilities | **No** — unversioned write |
| 2 | `POST /api/v1/integrations/keys` | Save PagerDuty/Jev key (write-only; reads report last4) | Yes |
| 3 | `POST /api/v1/integrations/simulated` | Simulated-mode toggle | Yes |
| 4 | `POST /api/v1/integrations/test-page` | Fire a REAL test page | Yes |
| 5 | `DELETE /api/v1/integrations/keys/<name>` | Delete a stored key | Yes |

Reads (`GET /api/decisions`, `/api/decision/<id>`, `/api/calibration`,
`/api/analytics/*`, `/api/v1/integrations/status`) stay unauthenticated
— the contract README's read-only guarantee stands; auth gates *mutation*,
not observation.

## 3. Bearer-token design

### 3.1 Mechanism (RFC 6750)

- Clients send `Authorization: Bearer <token>` (RFC 6750 §2.1 — the
  method resource servers MUST support). The server accepts the token
  **only** in the header: never in query strings, never in the body
  (RFC 6750 §5.3: tokens in URLs leak via history, logs, and referers —
  and this codebase logs request paths).
- Comparison in constant time. On missing/invalid: `401` with
  `WWW-Authenticate: Bearer` (RFC 6750 §3), terse reason code, no
  distinguisher between "missing" and "wrong" (no oracle for
  brute-force).
- TLS required when the token is in play — bearer over cleartext is a
  credential broadcast (RFC 6750 §5.1). The production self-host guide
  must terminate TLS before the platform tier; the design records this
  as a deployment requirement, not an assumption.
- **No secrets in logs** — the receiver's auth-code standard
  (`receiver.md` appendix: constant-time compare, terse reason codes,
  no secrets in logs) is the bar the platform tier is raised to.

### 3.2 Provisioning and rotation

- Token source: env var (`SENTINEL_API_TOKEN`) or a 600-perm token file;
  never in the repo, never in `platform/contracts/`, never in a mock.
  CI gains a secret-scan on the token's shape if a test fixture ever
  needs one (test fixtures use a fixed dummy the server never accepts
  in non-test mode).
- Rotation without downtime: the token store holds `active` +
  `previous` with a `previous_valid_until` timestamp (default grace
  24h). Rotation runbook: generate → distribute to the console config
  → cutover `active` → old token rides `previous` through the grace
  window → expires. Rotation is a two-command CLI flow, not a restart.
- Auth failures are counted (`platform_auth_rejected_total`, by
  endpoint, no token material in labels) — a brute-force sweep must be
  visible in metrics, and a sustained 401 rate is a paging signal, not
  background noise.

### 3.3 CORS tightening (same surface, same change)

`CORS *` on a bearer-authenticated write surface defeats the auth for
any browser-based attacker (the token rides ambient authority if the
console stores it accessibly). The design: `Access-Control-Allow-Origin`
restricted to the configured console origin(s) for the five write
endpoints; reads keep the current policy. The allowed origin is config,
not code.

### 3.4 UI / R-2 handoff (not designed here)

The Prism console must send the bearer token — which converges with the
R-2 key-resolution finding ("UI keys dead-letter while UI reports
configured"). **Handoff to the R-2 lane:** the bearer token becomes THE
platform-tier key contract; the R-2 key-resolution fix must terminate at
this token, not invent a second one. This doc does not redesign R-2; it
names the rendezvous point.

## 4. Versioning unification

### 4.1 Canonical surface

Everything canonical under `/api/v1`. Concretely:

- `POST /api/simulate` → `POST /api/v1/simulate` (the unversioned
  *write* is the highest-priority move — a write without a version is
  the one that can't evolve).
- Unversioned reads → `/api/v1/decisions`, `/api/v1/decision/<id>`,
  `/api/v1/calibration`, `/api/v1/analytics/noise`,
  `/api/v1/analytics/flips`.
- The already-`/api/v1` writes stay put.

### 4.2 Deprecation protocol for the old paths

Old paths remain as aliases during the window, each response carrying
(in-band, machine-readable — the lesson of the deprecation research):

- `Deprecation: @<unix-timestamp>` (RFC 9745 — the date the old path
  was deprecated; structured-field date, not `true`, not ISO-8601),
- `Sunset: <HTTP-date>` (RFC 8594 — the date the old path stops
  responding),
- `Link: <policy-url>; rel="deprecation"` and
  `Link: </api/v1/...>; rel="successor-version"`.

After the Sunset date: the old path returns **410 Gone**, not 404 —
"wrong URL" and "intentionally retired" must be distinguishable to a
confused client at 3 AM. Sunset never earlier than Deprecation.

The industry consensus backing this shape (Google AIP-185: `v1` in the
path; Azure: `?api-version=`; Stripe: date-pinned versions — three
different mechanisms, one agreement: **version only when you can't make
the change backward-compatible**, and retire with `Deprecation`+`Sunset`
headers) is cited in §8.

### 4.3 Window and telemetry

- Window: one full Pages-console release cycle + 30 days, published in
  the contracts README (draft text in the appendix). The console is
  independently deployed — the window must cover *its* cadence, not the
  engine's.
- Per-version usage is instrumented (counter per path, per day) so the
  Sunset decision is made against measured migration, not hope — the
  step the deprecation research says most teams skip.
- CI fails any new endpoint without a version prefix (R-16 "verifies"
  clause) — the check is mechanical: a test enumerates registered
  routes and asserts the `/api/v1` (or later `/api/v2`) prefix.

### 4.4 Appendix: draft deprecation policy for `platform/contracts/README.md`

> **Versioning policy (v1.0).** All platform API paths are versioned as
> `/api/v1/...`. Breaking changes (removal, rename, retype, new
> required auth) ship as a new version; additive changes stay inside
> the current version. A version is deprecated by announcing a Sunset
> date in this README and emitting `Deprecation` (RFC 9745) + `Sunset`
> (RFC 8594) + `Link rel="deprecation"/"successor-version"` on every
> response from the old paths. After the Sunset date the old paths
> return `410 Gone`. At most two versions are served concurrently.
> Deprecation windows cover at least one full console release cycle +
> 30 days; the window is shortened only for security-driven removals,
> documented here with the reason.

## 5. Verification (what "done" means — build phase)

- **401 matrix:** all five write endpoints × {no token, wrong token,
  good token} → 401/401/200; reads unaffected; `WWW-Authenticate:
  Bearer` present on 401s.
- Old paths assert `Deprecation`+`Sunset`+`Link` headers in tests;
  post-Sunset behavior (410) covered by a clock-injected test.
- CI route-enumeration test: every registered endpoint carries a
  version prefix.
- Token-rotation drill executed once (active → previous → expired)
  with zero failed legitimate requests during the grace window.

## 6. Pre-mortem (principal-systems §2)

*It is one year later and the R-16 build failed:*

1. **The token in the repo.** Someone committed the production token
   to a fixture "temporarily" for a demo; it shipped in the Pages
   bundle. *Mitigation:* secret-scan in CI from day one; the token
   shape documented as never-fixturable; rotation runbook exercised
   before the incident, not during.
2. **CORS tightened on paper only.** The allowed-origin config was
   left as `*` "until the console URL is final" and never revisited;
   an XSS in the console exfiltrated the bearer token and rotated the
   PD key silently. *Mitigation:* the CORS value is asserted in the
   same CI test as the 401 matrix — config drift fails the build, not
   a wiki reminder.
3. **The old paths never sunset.** The Sunset date passed; nobody
   wanted to break "that one integration"; the aliases became
   permanent and unversioned again. *Mitigation:* the Sunset is a
   calendar event owned by the security principal, and the 410 flip is
   a one-line config change scheduled in advance — not a decision to
   be re-made under pressure.

## 7. Alternatives considered and rejected

1. **OAuth2/OIDC on the platform API.** PIPELINE-REVISION R-16
   already rejected this as overkill for a single-operator tier: an
   IdP on the write surface adds a third-party dependency to the
   secret-handling path (done-checklist: IdP down 4 hours — what
   happens?). Rejected; bearer is the minimal Type-1-adequate
   mechanism.
2. **Token in query string (for `curl` convenience).** RFC 6750 §5.3
   forbids it; URLs are logged, historied, and referer-leaked. The
   convenience is real and the answer is still no — the CLI ceremony
   notes show the pattern (env/file, never the command line).
   Rejected.
3. **HMAC request signing (like Q2's `/v2/enqueue` answer).** HMAC is
   the right shape for *sender* authentication on the ingress path
   (Q2); the platform console is browser-driven, where bearer is the
   standard and HMAC buys nothing but complexity. Right tool per path.
   Rejected for this surface.
4. **mTLS for the console.** Stronger than bearer, and operationally
   heavier than the single-operator tier justifies: client cert
   issuance, rotation, and revocation for one human. Reconsidered if
   the operator count grows past a team. Rejected for now.

## 8. Author-written risk paragraph (§3.5)

The strongest reason this could go wrong is the oldest one in the
book: a bearer token is a password that travels on every request, and
this design puts the *entire* secret-handling surface — key rotation,
simulated toggle, real test pages — behind a single static string.
Theft of that string is total compromise of the write tier, and the
most likely theft vector is not cryptography but convenience: the
token in a shell history, in a screenshot, in the repo, in a browser
extension's reach. The mitigations (rotation runbook, grace window,
CORS tightening, 401 telemetry, secret-scan) are all *process*, and
process rots — which is why the two mechanical ones (CI route-prefix
test, CI CORS assertion) matter more than the three human ones. The
honest residual risk: until mTLS or a short-lived token scheme
arrives, the write surface's security equals the operator's hygiene
with one string. That is still infinitely better than zero auth — but
it should be written on the tin, not discovered in the postmortem.

## 9. Skill & Evidence (§2.2)

- **Requirement:** R-16 (`platform-api.md` P0-1/P0-2;
  PIPELINE-REVISION R-16) — bearer on five write endpoints +
  versioning unification.
- **Skill clauses that bind:**
  - *principal-governance §2, unforgiving API design:* version
    explicitly, deprecate on a published timeline — the `/api/v1`
    canonical surface, the RFC 9745/8594 alias protocol, the
    two-version cap, and the CI route-prefix gate.
  - *principal-governance §2, Postel's law:* conservative on emit
    (every alias response carries the full header set), strict on
    ingest (token only in the `Authorization` header, constant-time
    compare, no missing/wrong distinguisher).
  - *principal-systems, eternal friction:* the token WILL leak —
    hence rotation with a grace window, no URL tokens, no secrets in
    logs, 401 telemetry as a brute-force signal, and the pre-mortem's
    mechanical mitigations over human ones.
  - *principal-governance §2, APIs as legal contracts:* the
    deprecation policy text (appendix §4.4) is the contract the Pages
    console codes against — published before the build, not after.
- **Tools:** read `platform/server/app.py:183-201` (the five write
  endpoints; zero auth — only "Authorization" hit is the CORS
  allowed-headers line); `platform/contracts/README.md` (read-only
  contract v1.0.0 — the deprecation policy extends it);
  `platform/contracts/openapi.yaml` (type authority the version
  unification must update).
- **Web sources (accessed 2026-10-05):**
  - RFC 6750, *The OAuth 2.0 Authorization Framework: Bearer Token
    Usage* (IETF, October 2012): §2.1 (`Authorization: Bearer`
    is the method resource servers MUST support), §3 (401 +
    `WWW-Authenticate`), §5.3 (never pass tokens in page URLs;
    issue short-lived, scoped tokens).
    https://datatracker.IETF.org/doc/rfc6750/
  - RFC 9745, *The Deprecation HTTP Response Header Field* (IETF,
    published 2025) + RFC 8594, *The Sunset HTTP Header Field*:
    `Deprecation: @<unix-timestamp>` (structured-field date) marks
    when a resource was deprecated; `Sunset: <HTTP-date>` states when
    it stops responding; pair with `Link rel="deprecation"` /
    `rel="successor-version"`. Wire formats confirmed against the
    Linux Foundation Insights ADR-0021 (2026-09-17).
    https://www.rfc-editor.org/rfc/rfc9745
  - Google / Azure / Stripe API versioning consensus (Sept 2026):
    Google AIP-185 (`v1` in path), Azure (`?api-version=`), Stripe
    (date-pinned per-account versions) — "all three agree on one
    thing: version only when you can't make the change
    backward-compatible"; retirement via `Deprecation` (RFC 9745) +
    `Sunset` (RFC 8594).
    https://dev.to/freelance_inspector/google-azure-and-stripe-version-apis-three-different-ways-heres-what-they-agree-on-3mbg
- **Reviewers:** Vault ack required (12H-PLAN plat-2 routing;
  OPERATING-RULES §4.3 — auth changes need Vault sign-off before
  merge). Forge for the versioning/contract half.
