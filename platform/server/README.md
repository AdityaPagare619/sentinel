# Sentinel Platform Server (Forge's lane)

The frozen read API (`platform/contracts/openapi.yaml` v1.0.0) as a real
server. Read-only over the engine's append-only event log. Zero Jev calls
on every read path. Stdlib only.

## Run

From the repo root:

```bash
python3 platform/server/__main__.py \
  --port 18082 \
  --db ./sentinel.db \
  --state-dir ./sentinel-state \
  --ui platform/ui \
  --data-source shadow \
  --labels-version labels-v3 \
  [--context-json <state-dir>/platform/context.jsonl]
```

(`python -m platform.server` cannot work: the repo's `platform/` dir
shadows stdlib `platform`. `_pkg.py` loads the package under the alias
`sentinel_platform` instead — see §import note.)

| Flag | Default | Meaning |
|---|---|---|
| `--port` | 8080 | Own port. **The paging receiver already owns 8080 on the demo box** — run the platform on another port (18082 used in testing). |
| `--db` / `SENTINEL_DB` | `./sentinel.db` | Engine SQLite file. Opened `mode=ro` + `query_only=ON`, fresh connection per query. |
| `--state-dir` / `SENTINEL_STATE_DIR` | `./sentinel-state` | Dataset cache + default context-file location. |
| `--ui` | `platform/ui` | Static UI dir served at `/` (Prism's files — we serve, they own). Missing dir → `/` 404s as JSON. `Cache-Control: no-store`. |
| `--data-source` | `shadow` | Envelope label for river/explorer/analytics. Calibration/simulate always report `synthetic` (labels are synthetic by construction, labeled loudly). |
| `--labels-version` | `labels-v3` | The operator's assertion of which label set seeded the `outcomes` table (see §labels). |
| `--context-json` | `<state-dir>/platform/context.jsonl` if present | Harness-provided alert-context map (see §alert context). |
| `--max-inflight` / `SENTINEL_PLATFORM_MAX_INFLIGHT` | 32 | Admission bound (§shedding). |
| `--shed-load` / `SENTINEL_PLATFORM_SHED_LOAD` | 8.0 | Load/CPU above which expensive endpoints shed (§shedding). |

## Shedding: dashboard load can never starve the pager

Three bulkheads, in order:

1. **Process + port isolation (primary).** The platform server is a
   separate OS process on a separate port from the paging receiver. The
   pager never waits on the platform; the platform never waits on the
   pager. If the dashboard tier wedges, the pager keeps paging.
2. **Admission control (secondary).** A bounded semaphore
   (`--max-inflight`, default 32) gates concurrent requests. When the
   slots are full the server fails FAST — `503` + `Retry-After: 2` +
   `{"error": {"code": "shed_admission", "retryable": true}}` — instead of
   queueing unboundedly. An unbounded queue is how one dashboard stampede
   becomes a box-wide outage.
3. **Load shedding (tertiary).** When 1-min loadavg/CPU exceeds
   `--shed-load`, the expensive endpoints shed first, in this fixed
   order: `/api/analytics/*` → `/api/calibration` → `/api/simulate`
   (`503`, code `shed_hot`, `Retry-After: 5`, retryable). The river
   (`/api/decisions`), decision detail, and the SSE tail are NEVER shed
   by load — only by admission control. The platform degrades its own
   expensive work before the box melts; the pager is untouched because
   it is a different process (bulkhead 1).

The 8.0 default is deliberate: on a shared dev box ambient load sits at
3–5; shedding must mean "the box is actually melting". Lower the knob to
demonstrate the shed path (the fault-injection lane does exactly this).

## Read-only guarantee

- The engine DB is opened `file:...?mode=ro` with `PRAGMA query_only=ON`.
  SQLite + the OS both refuse writes. (Test: a write attempt raises
  `OperationalError`.)
- The serving path never imports the paging write path
  (`forwarder`, `pd_sender`, `secondary`) — grep-guard test fails the
  build if it ever does.
- The serving path never imports the Jev client module — not directly,
  not transitively. `datasets.py` builds labels via a **subprocess**
  (`python -m sentinel.synthetic`) precisely so `sentinel.models`
  (which imports the client) never enters the serving process.
  (Test: fresh subprocess import of every server module asserts
  `'client' not in sys.modules`.)

## What each endpoint reads

| Endpoint | Source |
|---|---|
| `GET /api/decisions`, `GET /api/decision/<id>` | `decisions` VIEW (`decision_made` events), newest-first; filters map to body JSON extracts |
| `GET /api/stream` | Same, oldest-first tail; 1s poll; `: heartbeat` every 25s; `retry: 3000`; gap event when the cursor predates the 10k-event replay bound |
| `GET /api/calibration` | `decisions ⨝ outcomes` (labeled only; denominators always reported); 10 equal-width bins over Q1 P(p1); Wilson 95% CIs; deterministic seeded bootstrap ECE CI |
| `POST /api/simulate` | `sentinel.tuner` called directly (same code path, not a copy) over the pinned `labels-v3` dataset; unknown `dataset_version` → 422, never a silent fallback |
| `GET /api/analytics/noise` | Aggregations over the windowed decision stream; team "before" load is a documented estimate (see store.py) |
| `GET /api/analytics/flips` | `sentinel.eventlog.detect_flips` over repeat `input_sha256`s — the honesty panel |

Pagination: `meta.pagination.next_since_id` is the oldest id on the page;
re-poll with it as `since_id` for the live-river "what's new" loop;
`has_more` says whether older rows exist (infinite scroll back).

## Labels

`/api/calibration` joins the engine's `outcomes` table. Sunday runs on
**versioned synthetic labels, labeled loudly**: `--labels-version`
(default `labels-v3`) is the operator's assertion of which label set the
harness seeded; the envelope's `data_source: synthetic` says it on every
response. `n_decisions` / `n_labeled` are always reported (no chart
without a denominator); with zero labels the bins are empty and ECE is
0.0 — the denominator tells the story, not the chart.

`/api/simulate`'s dataset is built deterministically from the engine's
own `sentinel.synthetic` generator (seed 7, n=2000) and hash-pinned;
provenance reports the real `dataset_sha256`, git `tuner_rev`, and the
engine's `GATE_FORMULA_VERSION` as `policy_version`.

## Alert context (engine gap E2)

The engine's audit does not persist alert context (service/check/region/
title) into the event log — only `alert_id`, `fingerprint`,
`input_sha256`. Resolution order, first hit wins:

1. `raw_payloads` table (engine-persisted; populated when the engine
   wires I3 + `store_raw_payload`) — the system of record.
2. `--context-json` — a JSONL map the **demo harness** writes
   (`{alert_id, title, service, check, severity_in, region, labels, ...}`,
   one object per line). The harness knows the alerts it fired; this is
   ground truth, not reconstruction.
3. Honest `"unknown"` markers — never invented service names.

Engine-side fix (filed, not mine to make): persist alert context in the
`decision_made` body (a `v01_compat`-style corner) or wire I3 +
`store_raw_payload` in the receiver.

## Prob maps (engine gap E1)

The audit persists the full Q1 probability map (`v01_compat.q1_probs`)
but only choice+confidence for Q2/Q3. The API reconstructs Q2/Q3
`probs` by keeping the recorded choice/confidence and spreading the
residual mass uniformly over the other enum options. This is a
documented reconstruction, not data. Engine-side fix: persist the full
answer triples in the decision body.

## Import note

`platform/` shadows stdlib `platform`, so `import platform.server` can
never resolve. `_pkg.py` loads this package under the alias
`sentinel_platform` (real package semantics — relative imports work).
Tests import via `_pkg.load(...)`. Do not add a `platform/__init__.py`
to "fix" this: it would shadow the stdlib module for every process with
the repo root on `sys.path`.

## Tests

```bash
cd platform/server/tests && python3 -m unittest discover -s . -p "test_*.py"
```

59 tests: store projections, filters, calibration/noise/flips math,
WSGI endpoint shapes + error codes, SSE framing + live-write roundtrip,
admission/load shedding, dataset determinism, and the guards (no Jev
client import, no paging-write imports, DB read-only).

## Demo-box ports (2026-10-03)

- `8080` — paging receiver (`python3 -m sentinel.receiver`)
- `8099` — fault-injection drill stub (`ops/drills/stub_pd.py`)
- `18081` — demo rehearsal shim (`sentinel-sun-demo/demo/rehearsal-shim.py`)
- `18082` — this server (used for smoke tests)

Coordinate with the demo lane before binding.
