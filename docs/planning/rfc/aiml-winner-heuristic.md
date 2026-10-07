# RFC: Winner heuristic — derive the console's judge-vs-timer winner from `budget_outcome`

**Status:** PROPOSED by FAANG principal wave, Team 2 (AI/ML), 2026-10-07.
**Type:** 2 — reversible; additive backend field + console copy fix.
**Law:** judge-down honesty — the console must never label a judge *error* as a
judge *win* (audit P1/C2).

---

## 1. Problem

`platform/ui-v2/index.html:674,677` (source of the served prod/staging consoles):

```js
winner: d.jev_model ? 'judge' : 'timer'
```

When the race takes the `error_passthrough` path — the judge was invoked (so
`jev_model` is recorded) but *raised* (`JevTimeout`, `JevRateLimited`,
`JevBudgetExhausted`) and the gate failed open (paged) — the console labels it a
**judge win**. That is the opposite of what happened: the judge errored and the
deterministic fail-open path owned the decision. The engine's authoritative
verdict is `budget_outcome` (`src/sentinel/race.py:665-667`, vocabulary in
`eventlog.py:113-119`):

| `budget_outcome` | meaning | honest console winner |
|---|---|---|
| `answered_in_time` | a Jev model answered within budget | `judge` |
| `timer_won` / `timer_won_shed` | timer beat the judge | `timer` |
| `error_passthrough` | judge raised; gate failed open (page) | `error` (new third state — neither won) |
| `structural_passthrough` | no race ran (deterministic pre-Jev path) | `timer` (fallback; no judge answer exists) |
| `pre_race_unknown` / `failopen_stepped` / `reaper_redrive` / missing | legacy or stepped-degradation records | old heuristic (`jev_model ? 'judge' : 'timer'`), labeled as fallback in drawer copy |

The `DecisionSummary` projection (`platform/server/store.py:254`) does **not**
currently expose `budget_outcome` — only `jev_model`. The console cannot use what
the backend does not send.

## 2. Anchors (web-verified 2026-10-07)

- **PagerDuty Events API v2:** `202` returns `{"status":"success","dedup_key":...}`
  — *accepted/enqueued, not delivered*. Our `forward_confirmed` honors exactly
  this (202-only). The same discipline applies here: label what the mechanism
  *did* (errored → failed open), not what the presence of a model id suggests.
- **Five whys:** the mislabel exists because the console used a *proxy*
  (`jev_model` presence) for the true variable (race outcome). The proxy
  diverges exactly on the path that matters most for honesty — the error path.

## 3. Alternatives considered

- **A. Derive from `reason` (starts with `error:` on the error path).**
  Rejected: `reason` is display copy; the mapping is lossy (`reaper_redrive` →
  `"error:reaper_redrive"` conflates two mechanisms); parsing display strings is
  the proxy trap again.
- **B. Console-side `error_passthrough` detection via `latency_ms`/`confidence`.**
  Rejected: invents semantics from side-channels; the engine already records the
  verdict — expose it.
- **C (chosen). Expose `budget_outcome` in the `DecisionSummary` projection
  (one additive line) and derive the winner in the console with a three-state
  mapping + legacy fallback.**

## 4. Decision

1. `platform/server/store.py::project`: add `"budget_outcome": body.get("budget_outcome")`
   to the summary (additive; the contract gains an optional field — Postel's law:
   old consoles ignore it).
2. `platform/ui-v2/index.html`: replace both `d.jev_model?'judge':'timer'`
   heuristics with `raceWinner(d)` implementing the table above; render the new
   `error` state as "judge errored — failed open (paged)" in the drawer and
   "judge error" in the river list copy. The `considered` fallback copy
   (`'Timer won or deterministic path — no Jev answer exists.'`) stays for the
   legacy path.
3. Add a regression test: a decision with `jev_model` set + `budget_outcome=
   "error_passthrough"` must project the field and the console mapping must
   return `error`, never `judge`.

## 5. Pre-mortem (it is 2027-10-07 and this failed)

1. **Old decision rows lack `budget_outcome`** → mitigated: fallback row in the
   table (legacy heuristic), honestly labeled in drawer copy as "race outcome
   not recorded (pre-instrumented record)".
2. **A third console (loadtest dashboard) has its own heuristic** → checked:
   `make_dashboard.py` aggregates race outcomes from the harness's own counters,
   not the `jev_model` heuristic — no change needed.
3. **The new `error` state confuses the "timer won" list filter** → mitigated:
   the list copy distinguishes three states explicitly; filters unchanged.

## 6. Verification (failing-before / passing-after)

- **Before:** construct a `decision_made` row with `jev_model="jev-1.13.0"` and
  `budget_outcome="error_passthrough"`; show the console mapping returns
  `'judge'` (the bug) and the projection omits `budget_outcome`.
- **After:** projection carries the field; mapping returns `'error'`; the drawer
  copy reads "judge errored — failed open (paged)".
- Existing suites green: `platform/server/tests/test_store.py`,
  `tests/test_sim_judge.py`.
