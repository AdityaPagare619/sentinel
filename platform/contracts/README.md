# Sentinel Platform Read API — Contract v1.0.0

**Status:** FROZEN for the Sunday 4 Oct 21:00 IST platform build.
**Source of truth:** `openapi.yaml` (this directory). **Reference data:** `mocks/`.

This contract is what every Wave-A feature lane codes against:
the dashboard lane (river, explorer, simulator UI), the calib/eval lane
(compute behind `/api/calibration` and `/api/simulate`), the Ledger lane
(aggregates behind `/api/analytics/*`), Tripwire (acceptance tests), and
Vault (response-content review).

## What the contract guarantees

1. **Read-only, always.** Every endpoint is a pure read over the audit
   database (`decisions` ⨝ `outcomes`, ARCHITECTURE.md §3.8). No endpoint
   writes, mutates thresholds, fires a page, or calls Jev. `POST
   /api/simulate` is a pure recompute over stored probabilities and the
   versioned labeled dataset — same code path as `tuner.py`, not a copy
   (Oracle's rule: simulator math ≡ tuner math).
2. **Zero hot-path coupling.** The platform tier is a view over the audit
   log. The paging path never waits on these endpoints, and these endpoints
   never wait on the paging path.
3. **Every response carries its data source.** The uniform envelope puts
   `meta.data_source` ∈ `synthetic | shadow | production` on every payload.
   Demo data presented as production data is a trust incident
   (PLATFORM_ARCHITECTURE.md §5) — the envelope makes the label structural,
   not a per-lane convention the dashboard has to remember.
4. **Deterministic replay.** Decision `id` is a monotonic integer (SQLite
   rowid). `since_id` cursors, SSE `id:` fields, and flip-audit joins all
   key off it. Same cursor + same database ⇒ byte-identical response.

## How lanes code against it

- **Code to the YAML, not to the mocks.** The mocks are realistic sample
  payloads for UI scaffolding and tests; the YAML is the type authority.
  If a mock and the YAML disagree, the YAML wins and the mock is a bug —
  file it against Forge's lane.
- **Import the mocks directly.** `mocks/*.json` parse as the `data` member
  of the envelope; drop them into a stub server or test fixture without
  transformation. `mocks/stream-events.jsonl` is one JSON object per line;
  each object maps 1:1 to a wire event (`id` → `id:`, `event` → `event:`,
  `data` → `data:` + blank-line terminator).
- **Additive-only until Sunday.** New fields may be added to responses;
  existing fields may not be renamed, retyped, or removed, and no new
  required request parameter may appear. A breaking change needs a new
  major contract version and the Wave Coordinator's sign-off.
- **Unknown fields:** clients MUST ignore unknown response fields
  (forward-compatibility). Servers MUST NOT require unknown request fields.

## Versioning rule

`meta.contract_version` is semver. `1.0.0` is the frozen Sunday build.

- `1.x.y` — additive field additions, wider enums, looser validation.
  Safe to deploy server-first; old clients keep working.
- `2.0.0` — any rename/removal/retype or new required parameter.
  Requires a migration note in this README and coordinator approval.

Mock payloads are versioned with the contract: every mock's `meta`
carries the `contract_version` it was generated against.

## Endpoint map

| Method | Path | Purpose | Key params |
|---|---|---|---|
| GET | `/api/decisions` | Decision river feed (newest first) | `limit` (1–500, default 50), `since_id` |
| GET | `/api/decisions` | Audit explorer search | `fingerprint`, `team`, `action`, `from`, `to` |
| GET | `/api/decision/<id>` | Full decision record | — |
| GET | `/api/calibration` | Reliability bins, ECE, coverage@τ, flip rate | `team` |
| POST | `/api/simulate` | Threshold projection (pure recompute) | body: `thresholds`, `cost_model`, `dataset_version` |
| GET | `/api/analytics/noise` | Top checks, team load, suppression breakdown | `window` (`24h` default) |
| GET | `/api/analytics/flips` | Flip-audit records (same input, different call) | `window` (`7d` default) |
| SSE | `/api/stream` | New decisions as written | `Last-Event-ID` header or `?since_id=` |

`from`/`to` are ISO-8601 timestamps. `window` matches `\d+[smhd]`
(e.g. `24h`, `7d`, `30m`). Filters combine with AND.

## Contract choices made deliberately (Forge's notes for the lanes)

1. **SSE resume is explicit, not hopeful.** Every event carries `id:`
   (= decision id). Clients resume with the standard `Last-Event-ID`
   header (WHATWG SSE); non-EventSource clients use `?since_id=`. If the
   cursor is older than the server's retention, the server sends one
   `event: gap` (`{resume_since_id, missed}`) and continues live — the
   client backfills via `GET /api/decisions?since_id=`. Tripwire's
   reconnect tests are deterministic because "resume" has one meaning.
   (Pattern per MDN/WHATWG Server-Sent Events; the retained-event replay
   policy is ours.)
2. **Simulate responses carry provenance, not just numbers.** Every
   projection ships a `provenance` object: `dataset_version`,
   `dataset_sha256`, `n_alerts`, `tuner_rev`, `policy_version`. The
   dashboard renders the provenance footer straight from the response —
   no second endpoint, no drift when the dataset changes under the UI.
   (Prism's law: provenance on every automated claim.)
3. **The envelope does the labeling work.** `meta.data_source` on every
   response means the dashboard lane reads one field to satisfy "every
   view labels its data source." `meta.pagination.next_since_id` gives
   the river its infinite-scroll cursor for free.

## Error shape

```json
{
  "data": null,
  "meta": {"contract_version": "1.0.0", "data_source": "shadow",
           "generated_at": "2026-10-03T00:00:00Z"},
  "error": {"code": "bad_window", "message": "window must match \\d+[smhd]",
            "retryable": false}
}
```

HTTP status codes: `200` ok, `400` bad parameter, `404` unknown decision
id, `422` simulate body invalid (thresholds outside sane bounds),
`503` read tier unavailable (retryable). `POST /api/simulate` with an
unknown `dataset_version` → `422`, never a silent fallback to another
dataset.

## Auth (out of contract scope, stated once)

v1 demo: the read tier sits behind the platform's own session; the
contract covers payload shape, not auth. Production hardens this
(restricted read-only credentials, per Stripe's restricted-key
precedent) without changing any payload.
