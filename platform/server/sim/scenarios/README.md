# Track 6 — scenario manifests (contract C6)

Four versioned scenarios under `platform/server/sim/scenarios/`, consumed by
`../sim_runner.py` (the scenario driver + FakePD sink) and referenced by
name in Track 7's acceptance (`docs/validation/ACCEPTANCE.md`).

## Manifest schema

```json
{
  "name": "storm-surge",            // scenario id (Track 7 references this)
  "version": 1,                     // manifest version — MANDATORY, bumps on
                                    // any semantic change to the scenario
  "seed": 6104,                     // determinism seed: same seed = same run
  "duration_s": 900,                // virtual-time span of the scenario
  "simulated_start": "...Z",        // virtual t=0 (ISO-8601)
  "description": "...",
  "change_windows": [               // optional: correlator change windows
    {"service": "*", "start_s": 590, "end_s": 1500}
  ],
  "profiles": [ ... ]               // arrival profiles (below)
}
```

## Profile kinds (arrival primitives)

| kind | params | stresses |
|---|---|---|
| `steady` | `rate_per_min`, `start_s`, `end_s`, `mix{noise,warning,sev,deploy}` | background load; full decision spectrum |
| `storm_burst` | `alerts`, `distinct_fingerprints`, `start_s`, `window_s`, `severity_in` | correlator storm detector; aggregation |
| `flap` | `fingerprints`, `refires`, `gap_s`, `start_s`, `alert_kind` | episode flap-reopen; dedup expiry |
| `duplicate` | `fingerprints`, `dups`, `gap_s`, `start_s`, `alert_kind` | duplicate inheritance (no re-judge) |
| `deploy_churn` | `alerts`, `deploy_at_s`, `window_s`, `sev_rate`, `services` | deploy-adjacent signature; change-window interplay |
| `cascade` | `waves[{at_s, services[], sevs}]`, `wave_gap_s`, `flap_gap_s`, `flap_refires` | multi-service incident; flap storms |

Alert kinds (`noise`, `warning`, `sev`, `deploy`, `deploy_sev`) map to
scripted FakeJev answers in `sim_runner.py`; see that file's header for
the decision contract.

## The four scenarios

| scenario | seed | virtual span | problems | exercises |
|---|---|---|---|---|
| `normal-day` | 6101 | 7200s | ~860 | noise suppression, business-hours queue, SEV pages, flaps, duplicate flood |
| `bad-deploy` | 6102 | 3600s | ~285 | deploy-churn signature, change-window queuing, real SEVs still page |
| `infra-incident` | 6103 | 3600s | ~460 | multi-service cascade waves, flap refires |
| `storm-surge` | 6104 | 900s | ~2580 | storm path: declaration, folding, digest pages |

## Honesty rules (non-negotiable)

- Every synthetic alert carries `labels.simulated=true`, a `[SIMULATED]`
  title prefix, and `raw.generator=sim_runner` with scenario + seed.
- Pages sink to a loopback FakePD acceptor with an explicitly fake routing
  key; the sim can never address pagerduty.com (asserted pre-flight).
- The FakeJev judge is a deterministic scripted mock (`jev-mock-0.0.0`),
  never presented as the real API. The real-Jev path (`--judge real`)
  resolves the key via `integrations.resolve_jev_key()` per contract C2.
