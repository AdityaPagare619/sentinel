# Sentinel — Vercel backend (`production-api`)

> **Correction (2026-10-07).** The header that used to sit on this file —
> *"No Vercel project was ever created; nothing was ever deployed here"* —
> was wrong. A Vercel project named **`sentinel-platform`** exists and is
> serving the production backend right now:
> `https://sentinel-platform-adityapagare619s-projects.vercel.app`
> (response header `X-Sentinel-Deployment: production-api`). This file was
> rewritten from scratch to describe that reality. The stale claim is
> struck, not amended — it contradicted the live system in the file an
> operator reads first.

## What this tier is

The **production API tier**: the exact `PlatformApp` WSGI application from
`platform/server` (this branch), running as a single Vercel serverless
function. No reimplementation, no demo data, no demo banner. The static
console (GitHub Pages: `/`, `/staging/`, `/loadtest/`) calls it
cross-origin; CORS allowlist is the Pages origin only.

What is real on this tier: C1 operator bearer auth on every `/api/*`,
`GET /api/v1/ops/health` (Track 8 aggregate), the kill-switch endpoints
(`safety_api`), SSE → `501 stream_unsupported` (serverless, by design).

What is NOT on this tier: the paging receiver, the gate, the forwarder.
This tier is read-path + safety-state only. The kill-switch state file
lives in `/tmp` and is **per-instance** (`kill_state_scope="per_instance_tmp"`
in the status payloads) — under multi-instance load an engage on instance A
does not change instance B. Kill-switch semantics on this tier are
unresolved (fail-open vs fail-closed undecided; see engine ruling
2026-10-07) — **do not rely on the switch as a production mitigation
until it is decided and wired** (P0-1 open).

## How a deployment is built and shipped

1. **Build the bundle** (from the repo root, on a clean tree):
   ```bash
   bash deploy/vercel/build-bundle-prod.sh
   ```
   Copies `platform/server` → `deploy/dist-prod/vercel/api/_srv` and
   `src/sentinel` → `.../api/_eng`, lays
   `deploy/vercel/api/index-prod.py` down as `api/index.py`, and installs
   `deploy/vercel/vercel.prod.json` (rewrites: every `/api/*` → the
   function). `deploy/dist-prod/` is gitignored — generated, never
   committed. The script sanity-checks the tree but does **not**
   pin/record the source commit; nothing today verifies bundle-vs-source
   drift (P1 gap — record the deployed commit in the deploy note by hand
   until the script does it).

2. **Deploy** with the Vercel CLI against project `sentinel-platform`
   (or redeploy from the dashboard). CLI keeps the deploy command in the
   record; dashboard redeploys are click-ops — prefer the CLI.

3. **Smoke the tier**: unauthenticated `GET /api/v1/health/live` → 200;
   `GET /api/v1/ops/health` without bearer → 401; with bearer → 200.

4. **Rollback** (when needed): Vercel dashboard → project →
   Production Deployment tile → **Instant Rollback**. Hobby plan:
   previous deployment **only**; Pro/Enterprise: any eligible deployment.
   Rollback does **not** rebuild and does **not** touch environment
   variables — env vars stay exactly as configured (verified against
   https://vercel.com/docs/instant-rollback). Cron jobs revert to the
   rolled-back deployment's state. Older than the previous deployment:
   revert in Git and redeploy.

## Environment variables (dashboard → project → Settings → Environment Variables)

| Variable | Required | Reality |
|---|---|---|
| `SENTINEL_OPERATOR_TOKEN` | **yes** | The C1 operator bearer. Without it the token store is ephemeral — every cold start mints a token nobody holds, the function logs a loud warning, and the console cannot authenticate (fail-closed). |
| `TYPESAFE_API_KEY` | no | The real Jev key for the judge. **Absent → judge-down mode** (timer-wins, fail-open); the console shows judge-down, never fake judgments. This is the production key path — compliant with the standing law (never committed, never logged, `sanitize_error` strips it). |

### Key paths — which is which (X-E correction)

- **Production / shipped engine:** `TYPESAFE_API_KEY` env var (or
  `resolve_jev_key()`'s user-store → env resolution in the BYOK flow).
- **Agent/research tooling only:** the `custom.typesafe` vault surrogate
  (`add_surrogate_to_request`, `hsurr:*` values) used by research scripts
  (`research/jev-behavior/bin/`). The surrogate is **not** a production
  key path; no doc may claim otherwise.

Neither value is ever committed, logged, or pasted into chat. Values live
in the Vercel dashboard and in Aditya's hands only.

## Files in this directory

| File | Role |
|---|---|
| `api/index-prod.py` | **The** production entrypoint — wire-up for the real `PlatformApp`. Its docstring lists the tier's honest limitations. |
| `vercel.prod.json` | Production rewrites + `X-Sentinel-Deployment: production-api` header. |
| `build-bundle-prod.sh` | The only supported bundle builder for this tier. |
| `api/index.py`, `build-bundle.sh`, `build-data.sh`, `ABOUT-THIS-DEPLOYMENT.md`, `vercel.json` | **Retired 2026-10-04**: the old read-only synthetic-data demo tier (its honesty page is no longer served; the demo dataset is gone from the tier). Kept for reference, never deployed again. |

## Operator notes

- **Logs:** Hobby retention is ~30 minutes and there is no uptime monitor
  (P1 gap). Do not treat absence of alerts as evidence of health.
- **Cold starts** delay the first request (observed 1.97s → 0.54s
  warm-up decay); there is no measured flip latency for this tier.
- **Hobby non-commercial-use clause** applies — latent constraint at
  current usage, stated not hidden.
