# CONSOLE PARITY — one console, two honest modes

**Lane:** production-stages · **Date:** 2026-10-06
**Decision:** the winning console v2 (Aditya's verdict: our pass vs Sonnet
round two) becomes BOTH the simulated showcase AND the production console.
No second-class production UI. No diverging codebases.

## The shape

```
platform/ui-v2/               ← the single console codebase (winner)
  index.html                  ← one file, zero deps (per the build contract)
  adapters/
    sim.js        → local-sim DataAdapter (seeded pipeline, FakePD)
    production.js → production DataAdapter (live /api/*, operator bearer)
  mode.js         → environment as a first-class, honestly-labeled mode
```

The v2 `DataAdapter` seam (documented in UI-V2-NOTES.md) already supports
this: reads (`getPulse/getQueue/getLedger/getSafety/...`) and writes
(`ack/appeal/engageKill/...`) are adapter-owned. The mode switch swaps the
adapter, not the UI.

## The mode contract (structural, not cosmetic)

| | SIMULATED | PRODUCTION |
|---|---|---|
| banner | violet, "SIMULATED — nothing here pages anyone", seed shown | distinct, "PRODUCTION — paging is ARMED", key-source shown |
| data | seeded pipeline | live backend |
| paging | FakePD (faithful) | real PagerDuty, user's BYOK key |
| judge | scripted/faithful/real-per-run | real Jev, cost-capped |
| spend | sim spend meter | cost attribution |
| drills | evaluator harness visible | drill records, measured |

**Invariants (both modes):** identical layout, IA, safety surfaces,
trace-number system, falsifier behavior. A skill learned in sim transfers
to prod 1:1. The mode lives in the DOM, the URL (`?mode=`), and every
export — never as a subtle theme tweak.

## Anti-divergence rules

1. UI changes land in the shared shell; mode differences live ONLY in the
   adapter + the mode banner.
2. The banner contract is asserted at **build time** by
   `deploy/gh-pages/build-v2.py`: `/` gets `data-mode="production"` +
   PRODUCTION chrome injected; `/staging/` ships verbatim from the
   sim-marked source; the build **fails** if either `<html>` element
   carries the wrong mode. This is a substring/tag assert in the build
   script, not a CI job and not a behavior test — it cannot catch a
   dishonest build, only a mislabeled one (UI backstage note, 2026-10-07).
   There is no GitHub Actions CI on this repo (Aditya's standing order —
   local gates only: `scripts/ops/pre-pr-gate.sh` + different-agent
   review).
3. The `/preview-v2/` judgment preview is **kept until Petu's flip
   order** — it is not removed unilaterally at shift (Petu verdict
   2026-10-07: preview-v2 = B). When the flip is ordered, remove it in
   one commit with a gh-pages rebuild.

## Deployment mapping

- `/` → production build (production adapter, prod banner, zero fixtures)
- `/staging/` → sim build (sim adapter, SIMULATED banner with seed)
- `/loadtest/` → the load-test lane's dashboard (placeholder until the
  lane's handoff lands; until then the path says so)
- Both `/` and `/staging/` are built from the same source on `main` by
  `deploy/gh-pages/build-v2.py`, differing only in the mode injection —
  there is no "adapter bundle" split at build time, and no CI job checks
  anything. The build script's banner assert (above) is the enforcement.

---

## Operations Health surface (Aditya's directive, 2026-10-06)

Aditya inspects and validates the whole machine on the live interface
itself. Operations Health is a FIRST-CLASS surface — not a settings page,
not a status footer. In production it reads from REAL backend endpoints;
in sim/preview it reads from the sim, labeled as such. Every health
signal below names its backend source. Where the source does not exist
yet, the gap is named explicitly — the UI must render "not instrumented",
never a fabricated healthy.

### Backend contract (new): GET /api/v1/ops/health (authenticated)

Aggregates the operations picture from the components that own it.
Response shape (every section carries `state` + `stopped_doing`):

```json
{
  "environment": {"mode": "production|simulated", "judge": "real|faithful|fake",
                  "sim_seed": null, "as_of": "iso8601"},
  "jev": {
    "model_id": "jev-1.13.0", "pinned": true,
    "connection": "ok|down|degraded", "last_error": null,
    "last_judgment": {"at": "iso8601", "latency_ms": 812, "disposition": "page"},
    "race": {"judge_wins": 412, "timer_wins": 9, "errors": 2, "budget_ms": 2700},
    "today": {"judgments": 423, "spend_usd": 0.0018, "cap_usd": 0.50},
    "circuit": "closed|open|half_open",
    "state": "healthy", "stopped_doing": []
  },
  "pipeline": [
    {"stage": "receiver", "state": "healthy", "latency_p50_ms": 3,
     "count_1h": 12034, "stopped_doing": []},
    {"stage": "correlator", "state": "healthy", "latency_p50_ms": 11,
     "count_1h": 12034, "problems_1h": 412, "stopped_doing": []},
    {"stage": "race", "state": "healthy", "latency_p50_ms": 816,
     "count_1h": 412, "stopped_doing": []},
    {"stage": "gate", "state": "healthy", "latency_p50_ms": 2,
     "count_1h": 412, "pages_1h": 38, "suppressions_1h": 374, "stopped_doing": []},
    {"stage": "forwarder", "state": "healthy", "latency_p50_ms": 140,
     "count_1h": 38, "stopped_doing": []}
  ],
  "forwarder": {
    "identity": "pagerduty|fakepd",
    "endpoint_host": "events.pagerduty.com",
    "delivery": {"confirmed": 37, "pending": 1, "failed": 0},
    "last_confirmation_at": "iso8601",
    "state": "healthy", "stopped_doing": []
  },
  "safety": {
    "kill_switch": {"state": "armed|engaged",
                    "last_drill": {"at": "iso8601", "measured_ms": 5}},
    "policy": {"version": "v42", "signed": true, "expires_at": "iso8601"},
    "auth": {"operator_token": "ok", "cors_mode": "allowlist"},
    "state": "healthy", "stopped_doing": []
  }
}
```

### Backend source map (honest — gaps named)

| signal | backend source | status |
|---|---|---|
| `environment` | platform config + sim manifest | ✅ exists |
| `jev.model_id`, `pinned` | `client.py` ADR-015 pin | ✅ exists |
| `jev.connection`, `last_judgment` | client call records | ⚠️ GAP — needs a judge-observation log |
| `jev.race` | `race.py` metrics + watchdog | ✅ exists (needs API exposure) |
| `jev.today.spend_usd/cap_usd` | `SpendMeter` (`advisory.py`); judge spend meter (Track 2) | ⚠️ GAP — judge spend not yet aggregated |
| `jev.circuit` | **circuit breaker** | ❌ GAP — not implemented; field must read `not_implemented`, never fake `closed` |
| `pipeline[].latency/count` | per-stage engine metrics | ⚠️ GAP — receiver/race/forwarder have metrics; correlator/gate need instrumentation |
| `forwarder.identity/delivery` | forwarder config + metrics | ✅ exists (needs API exposure) |
| `safety.kill_switch` | `safety_api` + drill records | ✅ exists |
| `safety.policy` | policy store | ⚠️ verify signed/version/expiry exposed |
| `safety.auth` | auth module | ✅ exists |

### Degraded honesty (the law, applied)

- `state: degraded` REQUIRES a non-empty `stopped_doing` list. A stage
  that cannot say what it stopped doing is `down`, not `degraded`.
- Judge down → race section shows timer-wins climbing, `stopped_doing:
  ["ai judgments"]`, and the gate section shows fail-open paging.
  The UI never renders stale judgments as live.
- Forwarder `identity: fakepd` in a PRODUCTION-mode console is a
  hard error state (loud banner), never a quiet label.

### UI surface rules

- The Jev agent card shows the agent AS WORKING: last judgment ticking
  in, race counts moving, spend meter filling. A static card is a
  black box with better typography — rejected.
- Pipeline strip: five stages, health state each, per-stage latency and
  counts. This is the same strip as the console's pipeline view —
  ONE component, not two.
- Forwarder identity is explicit and unmissable; delivery confirmations
  shown as confirmed/pending/failed — "a 202 is not delivery" is a
  rendered distinction, not a doc footnote.
- Safety at a glance: kill-switch state, policy signed/version/expiry,
  auth status — always visible, never behind a tab.
- Aditya's validation loop: he opens this surface, engages the kill
  switch, watches the pipeline strip go red-to-green, and checks the
  drill record. The surface must support that loop in under 60 seconds.
