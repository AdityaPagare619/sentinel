# Sentinel Fault-Injection Drills — 2026-10-04

**Lane:** LANE 5 (Tripwire) · **Operator:** Petu (subagent, inline)
**Date:** Sat 2026-10-03 ~21:30–23:30 IST · **Branch:** `lane/sun-drills`
**Engine:** real Sentinel on `origin/main` @ `2afbf54` (426/426 tests green),
run locally. **Mock posture:** `SENTINEL_MOCK=1` (mock Jev client raises —
the Jev-down path); drills 1B/2/4/7 drive the real `Gate`/`Pipeline`/
`DurableForwarder` in-process with scripted or dead clients.
**Gate status: 7/7 PASS. No release blockers.**

> Per Aditya's 20:28 IST correction, Sun 21:00 is a progress checkpoint, not
> final deployment. These drills were still run at full rigor; a FAIL would
> have been a release blocker for eventual deployment. None failed.

## Rig

| Piece | What |
|---|---|
| `ops/drills/stub_pd.py` | Fake PagerDuty intake: 202 + `{"status":"success"}`, records every POST (preview only, no routing keys stored) |
| `ops/drills/stub_jev529.py` | Fake Jev: every POST → HTTP 529, with a call-counter file |
| `ops/drills/gen_alerts.py` | PD-shaped JSONL generator (`--sev critical\|mix`, `--fp-mod` for fingerprint cycling) |
| `ops/drills/fire.py` | Burst sender: status-code histogram + p50/p95/p99 latency |
| `ops/drills/hammer_livez.py` | Read-path saturator: N loopers × GET /livez |
| `ops/drills/d1b_kill_client.py` | Drill 1B driver (wedged→dead client, real Gate+Race) |
| `ops/drills/d2_storm529.py` | Drill 2 driver (real Pipeline, real SystemOneClient → 529 stub) |
| `ops/drills/d4_fwd_crash.py` | Drill 4 driver (phase A crash / phase B restart) |
| `ops/drills/d7_showtime.py` | Drill 7 driver (Jev-dead showtime scenario) |
| Config | `ops/drills/config/thresholds.json` (engine defaults), `allowlist.json` (`[]`) |

Helpers are Tripwire-owned and live under `ops/drills/` only. No `src/` touched.

---

## Drill 1 — Kill the Jev client mid-storm → race-to-page pages via timer

**Bar:** no hung alerts, no silent drops; every alert pages via the timer or
fail-open path with an honest reason; every page reaches the vendor.

**1A — full receiver stack** (`SENTINEL_MOCK=1`; the mock raises on every
call = Jev dead from alert 0). 20 mixed-severity alerts, 20 distinct
fingerprints (under the storm threshold), fired at concurrency 4.

- HTTP: 20/20 × 200. Latency p50 49.1ms / p95 766.3ms.
- Audit: 21 `decision_made` rows (20 drill + 1 receiver startup self-test),
  **21/21 `passthrough` / `error_passthrough`**, zero suppressions.
- Stub PD: 20/20 receipts.
- **PASS.**

**1B — real Gate + RaceRunner, client killed mid-stream** (B=400ms drill
budget). Alerts 0–9: client wedged (hangs in `decide`). Client killed at
alert 10 (raises `JevTimeout`). Alerts 10–29 on the dead client. Legacy
`Forwarder` wired exactly as the receiver wires it.

- 30/30 `passthrough`: 10 × `timer_won` (wedged phase), 20 × `error:timeout`
  (dead phase). Max evaluate wall: **406.9ms** (budget 400ms + 1500ms slack).
- 30/30 forwarded (stub receipts), 30/30 audit rows. Zero hangs.
- **PASS.**

## Drill 2 — 529 storm from the model → backoff engages, pages on uncertainty

**Bar:** retries stay inside the retry budget; every alert gets a
disposition; nothing suppressed without storm-fold evidence; Jev calls stay
O(storm declares), not O(alerts).

Real `Pipeline` in-process (real `SystemOneClient` → always-529 stub, real
correlator + gate + forwarder). 60 alerts: 25 distinct fingerprints, then 35
folded into the declared storm.

- Dispositions: **21 passthrough / `error:overloaded`** (fail-open on
  JevOverloaded after 3 retries inside the 2.0s budget), **39 suppress with
  storm-fold evidence** (`"storm"` reason). Zero suppressions via the
  triple lock (impossible while the model errors).
- Storm declared once (21st distinct fingerprint). The declaring ingest
  paged the aggregate once through the gate; the aggregate POST reached the
  stub.
- Jev call counter: **63 before the declare (21 alerts × 3 retries), 0
  after** — folded alerts never touch the model. Backoff-by-design.
- **PASS.**

## Drill 3 — Malformed webhook flood → receiver stays up, paging unaffected

**Bar (as designed):** the receiver never 5xx's; the paging path is
unaffected. Honest note: the plan said "400s returned" — the engine's
fail-open design returns **200** for malformed bodies (forwarded
byte-identical via `forward_raw`) and **413** for oversized bodies. The
substance bar is "stays up, paging unaffected", which is what was tested.

Flooded: empty body, non-JSON, missing fields, bad `event_action`, null
summary, 9MB body. A good alert was fired before and after.

- Status codes: 5 × 200 (malformed), 1 × 413 (9MB). **Zero 5xx.**
- The 5 malformed bodies arrived at the stub **byte-identical**
  (verified: `not json at all`, exact JSON strings, empty body).
- Good-before and good-after both paged (200 + stub receipts). `/livez`
  healthy throughout (uptime kept climbing).
- **PASS** (with the 200-vs-400 design note above).

## Drill 4 — Process death between event-log write and forward

**Bar:** restart replays the outbox; the audit NEVER claims a
`forward_confirmed` that didn't happen.

Two-phase subprocess driver against the real `EventLog` + `DurableForwarder`.

- **Phase A:** decision logged + outbox row enqueued (I1), worker POSTs to
  the stub (vendor 202), then `os._exit(1)` **before** the I2 receipt
  (the `SENTINEL_FWD_CRASH_AFTER_SEND` crash window). Process exit code 1.
  Post-crash state: outbox row **`in_flight`**, events = [`decision_made`]
  only — **zero `forward_confirmed` claimed**. Stub had 1 POST.
- **Phase B (new process):** `startup_scan()` with a **fresh** lease →
  `requeued: 0` (correctly not stolen — a live worker might hold it); with
  the 300s lease expired (simulated +6min restart, the documented seam) →
  `requeued: 1` with an honest `forward_failed` / `crash_window_requeue`
  event. `run_once_sync()` claimed and redelivered.
- Final chain: `decision_made → forward_failed(crash_window_requeue) →
  forward_confirmed`. Outbox: `delivered`. Stub: 2 POSTs, **identical
  dedup_key** (`sentinel/drill/d4/0001`) — at-least-once + dedup, never a
  double page, never a silent claim.
- **PASS.**

## Drill 5 — Paging-path separation under read-path saturation

**Bar:** saturate the read path; paging throughput/delivery unaffected;
paging p95 degradation < 3× at matched concurrency.

50 alerts (10 distinct fingerprints, no storm) fired at concurrency 8 in
three runs: baseline (c1, no load), control (c8, no hammer), load (c8 +
16 loopers × GET /livez).

- Hammer: **17,044 read requests, 0 errors, ~370 req/s** sustained.
- Under load: **50/50 HTTP 200, 50/50 stub receipts, zero 5xx, zero drops.**
- Latency: control p50 59.8ms / p95 933.5ms → load p50 40.4ms / p95
  1612.4ms. **p95 ratio 1.73× < 3×.** p50 unaffected.
- **PASS**, with honest observations (below) — the read flood does inflate
  the paging tail, and the receiver shares one thread pool between reads and
  ingress (no ingress priority). There is no separate platform/dashboard
  tier in this repo, so the cross-process tier-shed variant of this drill
  cannot run here; the receiver's own read-vs-ingress separation is what
  was exercised.

## Drill 6 — Zero SEV1 suppression

**Bar:** every SEV1 pages with a receipt; in a storm, suppressions carry
storm-fold evidence and the aggregate page is delivered.

**6a — 20 distinct SEV1-critical alerts** (under storm threshold): 20/20
HTTP 200, 20/20 stub receipts, 20/20 `passthrough` / `error_passthrough`
in the audit, **zero suppressions**. **PASS.**

**6b — honest SEV1 storm** (60 distinct SEV1): 60/60 HTTP 200.
- 21 paged: 20 individual + **1 storm aggregate** (`storm-1791040001`),
  all 21 with stub receipts.
- 39 suppressed, **all with storm-fold evidence** (`"storm"` reason);
  zero non-storm-fold suppressions.
- **PASS.**

## Drill 7 — Kill the Jev path 5 minutes before showtime (NEW)

**Bar:** with the model unreachable, the demo's critical path continues
without a skipped beat: (1) race-to-page still pages, (2) the flip beat
degrades to its labeled recorded fallback (or an honest limits beat),
(3) river/report/receipts keep working off the local event log.

- **Phase 1 (paging, Jev dead):** 15/15 `passthrough` through the real
  Pipeline, 15/15 stub receipts, max per-alert wall 0.32s. PASS.
- **Phase 2 (flip beat):** dead live path + rehearsal recording → mode
  `recorded` with the **verbatim** label
  (`recorded real-Jev re-ask from rehearsal <ts> — live path down right
  now`); no recording → mode `limits_beat` naming the failure. The
  honesty gate (`assert_honest`) passes on both. PASS.
- **Phase 3 (receipts):** 15/15 decision rows readable from the local event
  log, **hash chain intact** (`prev_hash` linkage verified), disposition
  fold renders, `detect_flips()` runs clean. PASS.
- **DRILL 7: PASS.**

---

## Findings for the coordinator (not redesigns — reported, not fixed)

1. **`JEV_BASE_URL` is not wired.** The fault-injection plan assumed the
   receiver's Jev client could be pointed at a stub via env; `grep` shows
   the variable exists only in the plan doc. Drill 2 drove the real
   `Pipeline` in-process instead. If the demo needs a stub-able Jev path
   through the receiver, that's a wiring change for the engine lane.
2. **The live receiver HTTP path uses the legacy synchronous `Forwarder`**
   (labeled in `forwarder.py` as the frozen flaw's shape). No
   `DurableForwarder`, no `forward_confirmed` events on the webhook path.
   Drill 4's crash-safety was proven on the durable path in-process, not
   on the live HTTP path.
3. **Malformed webhooks return 200, not 400** (fail-open by design);
   oversized returns 413. The plan's "400s returned" does not match the
   engine. Documented, not changed.
4. **Storm aggregate needs `PD_ROUTING_KEY`.** With it unset, the
   aggregate page's forward fails (`no routing key available`, logged as
   FORWARD FAILED) **while the declaring webhook still returns 200** and
   the audit shows `passthrough` — the single most important page in a
   storm is silently lost. Production must have the key set; worth a
   config-time guard in the engine lane.
5. **Drill 5 caveats:** (a) one shared thread pool — an extreme read flood
   inflates paging p95 1.73×; strict "shed platform before paging" needs a
   separate tier or ingress priority (design decision, not a drill
   failure); (b) an unexplained ~1s latency hump appears at concurrency 8
   even *without* the hammer (bimodal histogram) — flagged for the engine
   lane; (c) all tail numbers are pessimistic on this 2-CPU drill box.
6. **Handoff note:** a misrouted coordinator message asked me to update
   `DEMO_SCRIPT.md` (demo lane's file). Before the correction arrived I
   had already written the flip-beat live-first/fallback spec +
   `rehearsal/flip_beat.py` + a built-only (D1/D3) audit section and pushed
   them to `lane/sun-demo` (commits `2128396`, `2f0a973`). The demo lane
   owns that content — adopt or discard; I claim no ownership of `demo/`.

## Rehearsal note

Per the fault-injection plan, drills 1 and 4 are the candidates to re-run
live at the go/no-go table. Today's runs are the supporting evidence; the
drivers are committed and re-runnable (`d1b_kill_client.py`,
`d4_fwd_crash.py A/B`).
