# About this deployment — what is real, what is demo

This URL is Sentinel's **hosted demo**: the platform tier (read-only API)
plus the Prism UI, running on Vercel's free tier. It is a demonstration
instrument, not a production paging path.

## Real

* **The API code is the production read path.** Every `/api/*` endpoint is
  served by the exact `PlatformApp` WSGI application from
  `platform/server` (frozen contract v1.0.0, `platform/contracts/openapi.yaml`).
  Nothing was reimplemented for hosting.
* **Read-only, enforced.** The dataset ships as a read-only file and is
  opened `mode=ro` with `query_only=ON` — the database and the OS both
  refuse writes, exactly like the demo-box server.
* **Zero Jev calls on every read path.** The serving process never calls
  the Jev API (it never could — no key is configured here).
* **`/api/simulate` runs the real tuner math** — the same code path as the
  engine's v0.1 tuner — over the hash-pinned `labels-v3` dataset
  (sha256 `371ef6f32416bdd…`, 2000 rows, deterministic seed 7).

## Demo (labeled, never hidden)

* **The data is synthetic.** 43 events / 42 decisions from a synthetic
  storm that the real engine processed on 2026-10-03, plus 40 synthetic
  reference labels for calibration. Every API envelope carries
  `"data_source": "synthetic"`, and the UI renders that label.
* **Live SSE is polling-only here.** Serverless functions have no
  long-lived connections, so `/api/stream` answers `501 stream_unsupported`;
  the UI degrades to its 30-second polling loop (its designed fallback).
* **Simulated paging is ON by default here** (`SENTINEL_SIMULATED_PAGING=1`).
  Nothing here can page anyone: there is no paging receiver and no stored
  key. The Integrations screen (KEYS) is reachable — you can paste a key
  and hit "Send test page", which uses it for that one request only and
  never stores it. Serverless functions have no writable disk, so keys
  cannot persist across invocations: the settings API answers
  `501 persistence_unavailable` on writes here, and the UI says so in-band
  instead of pretending to save.

Source: https://github.com/AdityaPagare619/sentinel
