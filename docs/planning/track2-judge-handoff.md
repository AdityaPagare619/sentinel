# TRACK 2 HANDOFF — Judge adapter (contract C2)

Branch: `lane/build-t2-jev-sim`. Status: built, tested (32 new tests),
full suite green, leak-grep clean. **Live verification pending key
provisioning** — `TYPESAFE_API_KEY` was absent from the build environment,
so the real path was verified with canned stand-ins + the real race
machinery; zero live vendor calls were made.

## What Track 2 provides

**Module** `src/sentinel/sim_judge.py` (import from `sentinel`):

| Symbol | For | Notes |
|---|---|---|
| `SimJudge` | T5, T6 | The clean `judge()` seam. Owns spend-capped client + real `RaceRunner`. `judge(alert=, state=, questions=, input_sha256=, episode_id=)` → contract `JudgeResult` dict. `decide_advisory(state, questions)` → raw `DecisionResponse` for T5 advisory directions (shares the spend cap, gates nothing). `close()` shuts down race threads. |
| `build_sim_judge(budget_usd=None, store=None)` | entrypoints | Resolves the real key via `resolve_jev_key()` (user store → `TYPESAFE_API_KEY` env). Key → `SpendCappedJevClient(SystemOneClient)` (`"jev-real"`); no key → `FakeJev` (`"fakejev"`). No network at build. |
| `JevSpendTracker` | T8 | Thread-safe `{session_usd, budget_usd, calls, blocked}`. Budget ≤ 0 starts blocked. Latches, never unlatches. |
| `judge_result(...)` | T7, T8 | Validated `JudgeResult` builder: exactly `{judgment, confidence, latency_ms, source, model_version, cost_usd}`. `confidence` ordinal 0..1 (never a probability). `cost_usd` must be 0.0 when `source != "jev"` (enforced). |
| `source_from_budget_outcome(bo)` | T7 | `answered_in_time→jev`, `timer_won*→timer`, everything else→`deterministic`. |
| `is_real_vendor_client(client)` | T7 | `FakeJev`/mocks are never labeled `"jev"`. |
| `FakeJev` | tests | Deterministic mock, model `fakejev-0.0.0`, label `"simulated judge"`. Every answer `cannot_determine` (uncertainty pages — it can never suppress). |
| `JevBudgetExhausted` | — | `JevError` subclass → race `error_passthrough` → deterministic fail-open page. |

**Sim wiring** (`SENTINEL_SIM=1` in `build_pipeline_from_env`): the sim
pipeline resolves the real key and runs the REAL race — no more
`SENTINEL_MOCK` short-circuit. `SENTINEL_MOCK=1` keeps its legacy
behavior when `SENTINEL_SIM` is unset. PagerDuty in sim is **structurally
FakePD** (audit P0, ruling X-B): `build_pipeline_from_env` hard-wires the
forwarder to a loopback sink (`http://127.0.0.1:9/fakepd`) with the
`SIM-FAKE` routing key via `sentinel/sim_pd_guard.py`, and a real
`PD_EVENTS_URL` / `PD_ROUTING_KEY` in the environment is a loud boot
refusal. The `Forwarder`/`PagerDutyClient` constructors themselves refuse
non-loopback endpoints under `SENTINEL_SIM=1`, so no wiring path can
re-arm real paging. The earlier "wiring untouched" phrasing was the false
claim behind the audit P0 — the old default was the real
`events.pagerduty.com` URL.

**Spend meter**: `GET /api/v1/jev/spend` on the receiver →
`{session_usd, budget_usd, calls, blocked}` (exactly these four keys;
never key material). Budget: `--jev-budget-usd` (default 0.50) >
`SENTINEL_JEV_BUDGET_USD` env > 0.50. **Merge note for T1**: this route
must be covered by C1 operator auth when T1 lands — it is currently
operator-localhost only (bind 127.0.0.1 default).

**Decision payloads**: in sim mode the gate attaches
`body.judge` = the contract `JudgeResult` (`_on_answered` both branches,
`_on_timer_won`, `_on_failopen_step`). Outside sim mode
(`jev_tracker=None`) the path is byte-identical — zero risk to existing
tests. Extra body keys are permitted by the event-log validator.

## Honesty rules baked in

- Timer wins are real: `source: "timer"` = "the page happened BECAUSE the
  judge was too slow". Late Jev answers are powerless (shadow only).
- Budget exhausted → `"Jev budget exhausted — deterministic mode"`,
  zero Jev calls, `blocked: true`.
- Absent key → FakeJev, never labeled `"jev"` anywhere (gate record
  forces `"deterministic"` via `is_real_vendor_client`).
- Cost = `input_tokens × $0.042/1M` (output free); failed calls record
  $0.00 (no usage data — never invented); per-decision `cost_usd` is a
  best-effort delta under concurrency, the session meter is exact.
- Key hygiene: only the key *source* (`user`/`env`/`unconfigured`) is
  ever emitted. Leak-grep clean (diff + new files).

## For T7 validation

Race outcomes exercised in `tests/test_sim_judge.py`: judge-wins
(source `jev`, real latency < budget, cost from tokens), timer-wins
(result returned ~500 ms before a 3 s judge answers — late answer
powerless), key-absent (FakeJev → `deterministic`), budget-exhausted
(zero wire calls), error fail-open (page, never silence). Live runbook
once the key is provisioned: `SENTINEL_SIM=1` + key + `--jev-budget-usd
0.50`, drive ≤10 alerts, assert `source: "jev"` records with measured
`latency_ms` and nonzero `cost_usd`, then force one timer-win with a
short `--race-budget`… (no such flag — use `RaceConfig.from_raw(500)`
via the `SimJudge` seam instead).
