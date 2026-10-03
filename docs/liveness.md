# Liveness — operator notes (design 05 implementation)

## The two probes

| Endpoint | Meaning | Auth | On failure |
|---|---|---|---|
| `GET /livez` | Shallow: the process is alive and answering. No dependencies checked. | Never | Supervisor restarts (backoff, max 5/10 min) |
| `GET /healthz` | Deep: the process can do its job *right now* (five predicates below). | Bearer <redacted> `SENTINEL_HEALTH_TOKEN` (unset = open) | External watcher pages with the failed predicates |

200 body carries evidence, not just the verdict
(`config_generation`, `gate_selftest_age_s`, `forwarder_outbox_lag_s`,
`restarts_10m`, per-predicate details). 503 body: `{"ok": false, "failed":
[<predicate names>]}` — the page carries the *reasons*.

The five `healthz` predicates: `config_current` (validated generation
loaded, no un-reloaded on-disk edit), `gate_constructed` (synthetic alert
through correlator→gate→forwarder-dry-run at startup and every 5 min),
`forwarder_draining` (forward-error tripwire; v0.1 has no outbox yet),
`evidence_flowing` (audit round-trip inside the self-test; v0.1 has no WAL
yet), `no_crashloop_signature` (≤1 restart in 10 min, from the restart file
in the state dir).

`degraded: true` in the 200 body means "healthy but fail-open" (e.g. the
Jev path errored and the gate is passing through). Degraded still pages —
it is not UNHEALTHY.

## `webhook_auth_fail_open` (ADR-005 D11)

`/healthz` carries a top-level `webhook_auth_fail_open` boolean on **every**
response (both the 200 and the 503 body). It is `true` only when the receiver
was started with `SENTINEL_WEBHOOK_ONBOARDING=1` — the explicit flagged
onboarding window in which unsigned/legacy-timestamp-less deliveries are
accepted loudly instead of refused. It is not a predicate failure: the
process can still do its job, so onboarding does not 503. In production the
field must read `false`; a supervisor, external watcher, or design-partner
drill that sees `true` knows webhook auth is fail-open and the onboarding
window has not been closed. Paired with the CRITICAL boot-time warning and
the per-request `webhook_auth_bypassed` metric.

## The 503-not-429 receiver contract (release-blocking)

Alertmanager **drops** alerts on 429 (unrecoverable verdict) but **retries**
on 5xx. Overload is therefore answered `503` with `Retry-After: 1` — never
429. Admission bound: `SENTINEL_MAX_INFLIGHT` (default 64) concurrent alert
handlers. Probes (`/livez`, `/healthz`) bypass admission control so overload
never starves the watchers.

## Config validation (fail-closed)

`thresholds.json` / `allowlist.json` in `--config-dir` (or
`SENTINEL_CONFIG_DIR`) go through parse → schema → semantic → atomic swap:

- unknown keys, wrong types, out-of-range values → rejected (typo guard);
- `suppress_conf_min` below the 0.85 ADR-022 governance floor → rejected,
  not warned;
- incoherent tables (`page_p1p2_min ≤ suppress_p1_max`,
  `uncertain_conf_max > suppress_conf_min`) → rejected;
- allowlist entries must be 16-char hex fingerprints.

Outcomes: invalid startup config + last-good on disk → serve last-good,
emit `config_rejected` to `events.jsonl`. Invalid startup config with **no**
last-good → **refuse to start** (exit 2). Invalid reload (SIGHUP or
`POST /-/reload`, same bearer <redacted> `/healthz`) → 422, live generation untouched.

State dir (`--state-dir` / `SENTINEL_STATE_DIR`, default `./sentinel-state`)
holds `generations/` (newest 5 validated generations), `restarts.jsonl`
(crash-loop accounting) and `events.jsonl` (`config_rejected` events).

## What this lane does NOT cover (named seams)

- The external health watcher, dead-man's switch, standby direct-to-PD
  fallback and the monthly drill runbook live in the deployment layer —
  see design 05 §§2, 4.4, 5.
- The synthetic canary (§1.2.3) and WAL flusher predicate (§1.2.4) are
  honest gaps until the forwarder lane (design 03) and event-log lane
  (design 02) land; the predicates say so in their evidence.
- Correctness-under-liveness (a healthy process making wrong suppressions)
  is ADR-022/H-2's job, not the probes'.
