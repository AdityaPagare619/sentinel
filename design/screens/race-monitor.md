# Screen: Race Monitor — what the model did, and what the timer did about it

**Function code:** `RACE` · **Route:** `/race`
**API:** `GET /api/race/stats?window=24h` · `GET /api/race/recent?limit=50` ·
`GET /api/race/shadow` · `GET /api/drift/status`

**Purpose:** The gate's core mechanism is a race: the Jev model races a
bounded timer, and a late answer is powerless — it becomes shadow evidence,
never a decision. This is the system's most subtle safety property, and it
is currently invisible: the operator sees the decision but never sees
*who won the race*. This screen makes the race observable: timer-won vs
answered rates, latencies, late-answer shadow evidence, model-drift pins,
and the drift harness's coverage verdict. It answers the question the
architecture's safety case depends on: *"is the race actually protecting
us, or is the timer just decoration?"*

---

## 1. The operator's question

*"When the model is slow or wrong, does the system still page correctly?"*

Secondary: *How often does the timer win? How late are the late answers?
What did the shadow evidence say — would the late answer have changed the
decision? Is the model drifting?*

## 2. The race scoreboard (above the fold)

Four numbers, the whole safety case in one glance:

```
last 24h · 3,204 races
  timer won      41  (1.3%)   → deterministic passthrough, no model consulted
  answered      3,163 (98.7%) · p50 180ms · p99 1,900ms · budget 2,700ms
  late answers   12            → shadow evidence only (0 reached the kernel)
  model drift pins  0          → ADR-015 pin asserted, answer discarded
```

Each number carries its safety meaning, because the numbers are only
interesting as a safety argument:

- **Timer-won** is the fail-safe firing. It is not a failure — it is the
  system choosing determinism over waiting. The screen frames it as
  protection working, with the reason breakdown (`timer_won` vs
  `timer_won_shed` — shed = the inference pool was saturated, a capacity
  signal, not just a latency signal).
- **Answered latencies** against the budget. The p99's distance from the
  2,700ms budget is the margin. If p99 crosses 80% of budget, the screen
  flags it in warning register — the margin is the early warning, not the
  breach.
- **Late answers: 0 reached the kernel.** This is the load-bearing
  sentence. The late-answer hook converts tardy responses to
  `shadow_decision` payloads; the screen counts them and asserts the
  invariant. If the count of late answers reaching the kernel is ever
  nonzero, that is the screen's critical-register moment.
- **Model drift pins** (ADR-015): the hot-path model pin asserted before
  every answered path. A drifted model ⇒ `passthrough` with reason
  `model_drift`, and the answer is untrusted evidence that never reaches
  the kernel. Pins are rare and each one links to its event.

## 3. The shadow-evidence view (below the fold)

The 12 late answers, each with the counterfactual: *the timer already
decided `passthrough`; the late answer said `suppress` at 0.87 — the
decision stood.* This is the race's audit trail and the calibration
surface's raw material:

```
03:12:44  timer won (1,204ms over) → PASSTHROUGH
          late answer at +3,904ms: Choice: suppress · 0.87
          counterfactual: would NOT have changed the decision (passthrough ⊇ paged)
          [view input] [compare with answered path]
```

The shadow view also carries the honest aggregate: *"late answers agreed
with the timer's passthrough 9 of 12 times this week; 3 would have
suppressed — all 3 were flap-debounce-class inputs the deterministic path
would have suppressed anyway (S1 dedup)."* The race is not graded on
whether the model was "right" — it's graded on whether the safety
property held.

## 4. Drift harness status

R-13's vendor-boundary honesty, surfaced: the drift harness's last run
against the live Jev shape, the cassette replay verdict, FakePD coverage
gate state:

```
drift harness: last run 04:00 · verdict CLEAN · next 10:00
jev shape: pinned v3 (ADR-015) · 0 unexpected fields in 7d
FakePD coverage: 41/41 documented behaviors tested
```

A harness failure renders here in warning register with the failing
contract named — the operator sees *which* vendor behavior drifted, not
just "drift detected". (The harness runs on the team's own keys per
Aditya's Q6 decision; the screen shows the cost cap: *"12 of 50 budgeted
live calls used this run."*)

## 5. Professional-software lineage

| P-LIB pattern | What it teaches this screen | Why it fits |
|---|---|---|
| P-LIB-2 PagerDuty AIOps triage context | "Outlier? precedent? related?" for incidents — here: is the timer winning unusually often (outlier), has this latency pattern happened before (precedent), is it correlated with a vendor incident (related)? | The race's telemetry is only useful as *judgment context*, not as graphs. |
| P-LIB-9 calm technology | Race outcomes are periphery telemetry. The screen is quiet by default; only a nonzero kernel-reach or a harness failure promotes. | Nobody should watch the race. The race should watch itself and report exceptions. |
| P-LIB-5 Datadog one case view (the steal, not the sprawl) | The late answer + the timer decision + the counterfactual on one view — the case file for one race. | A race is a case: two contenders, one verdict, the evidence for both. |

## 6. What it must NEVER show (anti-fatigue rules)

1. **Never a live race ticker.** A streaming list of every race as it
   happens is the K2-killed infinite ticker wearing a safety costume.
   (INV-U1, INV-U5.) The scoreboard aggregates; the recent list is a
   bounded, queryable 50.
2. **Never per-race latency graphs as the headline.** The headline is the
   four safety numbers. A latency histogram is one click deeper — it
   answers "how fast", not "are we safe".
3. **Never model internals.** No token counts, no prompt text, no raw Jev
   payloads. The screen shows the *race outcome and its safety meaning*,
   not the model's diary. (The model is the advanced controller; the
   screen respects the Simplex boundary — it observes the boundary, it
   doesn't peer inside the controller.)
4. **Never "model accuracy" as a metric.** The model's job in the race is
   to answer in time with a typed, calibrated answer. Grading it on
   accuracy here invites the operator to tune the model instead of
   trusting the race — the wrong optimization target.
5. **Never hide a nonzero kernel-reach.** If a late answer ever reaches
   the kernel, that is not a row in a table — it is the screen's entire
   state, critical register, with the page to the platform team.

## 7. States

- **Empty (no races in window):** *"No races in the last 24h — the
  deterministic paths (S1 dedup, D6 firewall, fail-open ladder) decided
  everything. The race only arms when the gate needs the model."* The
  empty state teaches the architecture.
- **Degraded (fail-open ladder active):** the scoreboard is replaced by
  one line: *"Race not armed — fail-open step 2 active (static severity
  floor). No Jev calls are being made."* with a link to the degraded
  screen. The race monitor must not render race statistics for races
  that aren't happening.
- **Harness failing:** warning register with the failing contract named
  and the last-clean timestamp.

## 8. API mapping

| UI need | Endpoint |
|---|---|
| scoreboard | `GET /api/race/stats?window=24h` (timer-won/answered/late/pins, latency percentiles, budget) |
| recent races | `GET /api/race/recent?limit=50` (bounded, queryable) |
| shadow evidence | `GET /api/race/shadow` (late answers + counterfactuals) |
| drift harness | `GET /api/drift/status` (last run, verdict, cost-cap usage, FakePD coverage) |

Zero Jev on reads (platform law). The monitor observes the race; it never
arms one.
