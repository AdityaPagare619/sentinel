# Sentinel — hosted demo deployment (deploy lane)

> **SUPERSEDED (2026-10-04, Aditya's directive):** GitHub Pages replaces Vercel.
> No Vercel project was ever created; nothing was ever deployed here. The two
> environments now live on GitHub Pages: `/staging/` (fully simulated showcase)
> and `/` (production console, DATA_MODE=live). Built by
> `deploy/gh-pages/build-static.py`. This directory is kept for reference only —
> do not build from it.

Live URL for the platform tier (read-only API, frozen contract v1.0.0) +
Prism UI. Vercel free tier, ₹0.

## What's deployed

* `api/index.py` — the REAL `PlatformApp` WSGI app from `platform/server`,
  served as one serverless function. Rewrites in `vercel.json` route every
  `/api/*` to it. Read-only: `demo.db` ships `chmod 444` and is opened
  `mode=ro` + `query_only=ON`, exactly like the demo-box server.
* `/` — Prism's static UI (`platform/ui`), copied verbatim except for the
  injected hosted-demo banner (see build-bundle.sh).
* `/ABOUT-THIS-DEPLOYMENT.md` — the honesty page, served at the URL.

## What's real vs demo (also at the URL)

| Real | Demo / refused |
|---|---|
| The frozen-contract API code — every endpoint is the production read path | The dataset: a synthetic storm the real engine recorded 2026-10-03 (43 events, 42 decisions) + 40 synthetic reference labels |
| `/api/simulate` — real tuner math over hash-pinned `labels-v3` (sha `371ef6f3…`) | `data_source=synthetic` on every envelope; the UI renders it |
| Read-only guarantee (ro DB, zero Jev calls on read paths) | `/api/stream` → `501 stream_unsupported` (serverless); UI falls back to 30s polling |
| | No paging receiver, no PagerDuty key, no Jev key — this URL cannot page |

## Reproducing the bundle

```bash
bash deploy/vercel/build-bundle.sh   # -> deploy/dist/vercel (gitignored)
# build-data.sh (called automatically) regenerates the dataset snapshot
# deterministically — no Jev key, no network.
```

`deploy/dist/` and `deploy/dist-data/` are gitignored: generated, never committed.

## Deploying

The lane deploys the generated bundle with Vercel's file-upload +
`create_deployment` (MCP `vercel` skill). The deployment URL is recorded in
the lane's PR. Re-deploy after merging by re-running the script on `main`
and deploying again — or link the repo to a Vercel project (git deploys)
once Aditya approves a standing integration.

Current status (2026-10-04): Vercel OAuth is connected, but the
`deployments.publish` mutation needs an interactive egress approval that
could not be obtained (Aditya is dark). The bundle is built and tested;
deploying is one approval tap + one command away — see the lane PR.
