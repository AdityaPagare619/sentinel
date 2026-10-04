# Sentinel — external watcher deployment (deploy lane)

**Source of truth:** `ops/devops-foundation.md` (L3, PR #53) — pre-mortem §0
item 1, §1.2 cutover gate, §10 (A3 per-source heartbeat / dead-man's-switch),
RB-4/RB-6/RB-8 runbooks. This doc is the *host story*: where the watcher
runs, how often, and where its alerts go. The *what-it-checks* contract
lives in the L3 doc.

## The one rule

The external watcher **never shares a fate domain with the thing it
watches**. Not the same process, not the same container, not the same host,
not the same cron daemon — a dead box cannot report its own death. (L3
pre-mortem: *"Sentinel down is a SEV1 on a path that doesn't traverse
Sentinel" is a sentence, not a system, until the watcher has an owner.*)

## Where it runs

| Watcher | Watches | Runs on | Why this host |
|---|---|---|---|
| External watcher (cron) | The Sentinel prod box: receiver process, event-log growth, per-source heartbeats, disk watermark | The operator's **existing monitoring host** — a different machine (or a different cloud account/region) from the Sentinel box. ₹0: no new infrastructure. | If the Sentinel box dies (power, kernel, disk, network partition), the watcher is unaffected and still reports. |
| Dead-man for the watcher | The watcher itself | A **weekly on-call review** of `heartbeat-check.state` freshness (+ the monthly drill, L3 §2.5). No budget for a hosted dead-man service yet — the gap is stated, not hidden. | A watcher that silently stops is the pre-mortem's exact failure mode. |

Explicitly NOT allowed: the watcher as a thread in the receiver, a
sidecar on the same host, or a cron on the Sentinel box itself. Any of
those shares the fate domain and voids the guarantee.

## What the watcher runs (fixed interval)

On the external host, a cron (cadence per subject in `heartbeats.json`,
versioned in the config dir; the A3 example is "expected every 60s, silent
at 7m") invokes:

```bash
scripts/ops/heartbeat-check.py --db <SENTINEL_DB> --heartbeats heartbeats.json
```

plus the disk eye:

```bash
scripts/ops/disk-watermark.sh <state-dir>   # Nagios-style exit codes
```

Key properties (L3 §10.2):

* The check reads the **SQLite file directly** (`mode=ro` URI) — the
  verdict comes from the log file alone. It never traverses the receiver:
  no `/healthz`, no `/livez` in the decision path. If the receiver is
  down, the script still runs and reports the log stopped growing — which
  is exactly the information the on-call needs.
* `/livez` may be *consulted* for diagnosis, never for the trip verdict.
* Subjects: every `source_integration` value on `decision_requested`
  events, plus synthetic `__pipeline__` (any event at all — the pipeline
  is writing) and `__shadow_tap__` (shadow feed freshness).
* States: `ok` → `stale` (warning: ticket/note) → `silent` (dead-man trip:
  **page the on-call** — treat as paging-path-down until proven otherwise).
  `unknown` (no rows yet) never cries wolf.
* The script appends its run timestamp to its own state file
  (`heartbeat-check.state`, next to the DB — never into the event log;
  the engine owns that). The weekly review checks the state file is fresh:
  that is the watcher's own dead-man's-switch.

Disambiguation on `silent` is RB-8's job: `/livez` dead → RB-4 (receiver
down); sender side dead → fix the sender; both alive → the path between
them (webhook auth, routing key, network) is broken. RB-4 and RB-6 both
*start* from "something went silent" — this tripwire tells the on-call
which runbook they're in.

## Alerting path

Watcher verdicts — especially `silent` — page via a path that **never
traverses Sentinel**: the operator's existing paging (PagerDuty /
phone / SMS on the monitoring host), or the secondary channel drilled
under ADR-018/D13. A watcher whose alerts route through Sentinel is
another sentence, not a system.

Arming checklist (blocks prod cutover, L3 §1.2):

* [ ] Named human owner + written duty roster for the watcher.
* [ ] Cron armed on the external host; first `silent`-path drill completed
      (kill the receiver in `staging-lab`, watch the page arrive).
* [ ] Weekly `heartbeat-check.state` freshness review on the on-call
      calendar.
* [ ] Watcher-down alerting path tested end-to-end (does not traverse
      Sentinel).

## Current status (2026-10-04)

`scripts/ops/heartbeat-check.py` and `scripts/ops/disk-watermark.sh` are
specified in L3's `ops/devops-foundation.md` (PR #53, open) but **not yet
merged** — this doc describes the deployment contract they will run under.
Do not mark the cutover "watcher armed" checkbox until the scripts exist,
the cron is armed, and the first drill has paged successfully.
