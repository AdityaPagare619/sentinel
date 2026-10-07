# Sentinel — external watcher deployment

> **Rewritten 2026-10-07 (docs W7).** The previous version described a
> self-hosted Sentinel box (receiver process, SQLite event-log,
> per-source heartbeats, disk watermark) with a watcher on "the
> operator's existing monitoring host." That box was never deployed — the
> shipped tier is Vercel serverless, which has no host to SSH, no local
> event log to tail, and no receiver process. The phantom topology is
> struck. The L3 heartbeats design (`ops/devops-foundation.md`) is kept
> as the **customer self-hosted engine** design, clearly labeled where
> referenced — it is not the story of this tier.

**Reality check, first:** as of 2026-10-07 the shipped tier has **no
watcher at all** — no uptime monitor, no log drain, ~30-min Hobby log
retention. Nobody would know if prod broke (infra P1 #16). This doc
describes what the watcher MUST be on this topology, and what is still
missing. Do not mark any cutover "watcher armed" checkbox until the
arming checklist below is green.

## The one rule (unchanged)

The external watcher **never shares a fate domain with the thing it
watches**. On this topology that means: not a Vercel function in the
same project, not a cron in `vercel.prod.json`, not anything whose
logs, billing, or deployment pipeline is the one being watched. A dead
project cannot report its own death.

## What the watcher watches (this tier)

| Check | How | Why |
|---|---|---|
| Liveness | `GET https://sentinel-platform-adityapagare619s-projects.vercel.app/api/v1/health/live` — 200, unauthenticated | The tier is up at all. |
| Auth still fail-closed | same host, `GET /api/v1/ops/health` without bearer → expect 401 | Catches an auth regression without holding the operator token. |
| Kill-state scope visible | authed `GET /api/v1/ops/health` → `kill_state_scope` present | Cross-instance divergence must stay visible, not assumed away. |
| TLS + latency | the monitor's own TLS/handshake timings | Cold-start decay is the tier's known slow vector. |

What the watcher does NOT check: the paging path (there is none on this
tier), engine DB health (unwired — `engine_db.available=false` is
reported honestly by the endpoint, not by the watcher).

## Where it runs (₹0)

A **free external uptime monitor** (e.g. UptimeRobot / Better Stack free
tier — picked by Aditya, not by a lane; no account exists yet) on a
cadence ≤ 5 minutes, plus a log-drain or scheduled `vercel logs` pull
until a drain exists. Explicitly NOT allowed: anything inside the
`sentinel-platform` project, the operator's browser tab, or a cron that
shares the project's fate domain.

The **alerting path** pages via a channel that never traverses Sentinel:
Aditya's phone (SMS/call from the monitor's own alerting). A watcher
whose alerts route through Sentinel is a sentence, not a system.

## Arming checklist (blocks any production-live declaration)

* [ ] Free uptime monitor chosen and configured with the four checks
      above; first DOWN-path drill completed (redeploy a bad bundle to
      a preview, watch the page arrive).
* [ ] Log retention solved: drain attached or a documented pull cadence
      that beats the ~30-min Hobby window.
* [ ] Named human owner for the watcher (today: Aditya by default).
* [ ] Watcher-down detection: the monitor's own heartbeat/missed-check
      alert is armed, so a silently-stopped watcher pages too.

## What the old doc described (kept for the record)

The self-hosted engine design — receiver process, SQLite event log,
A3 per-source heartbeats, `heartbeat-check.py` / `disk-watermark.sh`,
dead-man's-switch on `heartbeat-check.state` — lives in
`ops/devops-foundation.md` (L3, PR #53, open). That design is for the
**customer self-hosted engine** (`docs/deploy-production.md`), where a
box exists to watch. The scripts were specified but never merged; do
not port them to the serverless tier — they assume a filesystem that
does not exist here.

**Current status (2026-10-07):** nothing above is built. The tier is
unwatched. This is a P1 ship-blocker for the production-live
declaration, stated not hidden.
