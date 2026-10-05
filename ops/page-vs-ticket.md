# Page vs. ticket — the on-call's paging contract

A page wakes a human. The contract below decides which signals are worth
a human's sleep and which become tracked tickets. It binds because the
principal-systems infrastructure constitution binds: **every page must be
actionable and tied to an SLO threat**.

## The paging rule (three gates)

A signal pages **iff** all three hold:

1. **SLO threat or user-visible harm, now.** A degraded paging path loses
   real pages. "Might become a problem" is a ticket.
2. **Actionable within minutes.** There is a runbook line for the on-call
   to execute at 3 AM. A page with no runbook is a bug in *this table*,
   not an incident — file the action item.
3. **Not already auto-remediated.** If the system handled it (deploy
   auto-rollback, fail-open carrying paging, disk spill holding), the
   residue becomes a ticket — the human work is the follow-up, not the
   wake-up.

## Response targets

- **5 minutes** — user-facing / time-critical: the paging path itself.
- **30 minutes** — everything else ticketed.
- Paging medians should sit **near zero**; more than ~2 incidents per
  12-hour shift is unsustainable (that's the load budget, not a target —
  execution-doctrine §8). If a signal pages more than ~once a quarter
  without producing action items, the signal's classification is reworked —
  a page that never changes the system is theater
  (principal-mindset §11: anti-theater audit).

## The table

"Why" is one line — the classification must survive being read at 3 AM.

| Signal | Source | Page or ticket | Why | Target | Runbook |
|---|---|---|---|---|---|
| `/livez` timeout / connection refused | external watcher | **PAGE** | process dead = paging path down; every minute is lost pages | 5 min | RB-4 (`ops/devops-foundation.md` §6) |
| `/healthz` 503, `failed` includes `config_current` or `no_crashloop_signature` | external watcher / healthz poll | **PAGE** | paging path unhealthy or crashlooping | 5 min | RB-4 |
| Dead-man trip: source `silent` | `heartbeat-check.py` exit 2 | **PAGE** | treat as paging-path-down until disambiguated — a silent source loses pages, it doesn't delay them | 5 min | RB-8 |
| `[sentinel] FORWARD FAILED` climbing; `forwarder_draining` | receiver log / healthz | **PAGE** | pages decided but never relayed; RB-4 step 3 escalates to cutover | 5 min | RB-4 → RB-7 if unrecovered |
| `BROKEN: checkpoint` (event-log tamper) | receiver log | **PAGE** | the audit chain is the product's core claim; integrity findings are SEV-class | 5 min | RB-4 (diagnose) → escalate Ledger + Vault (ownership map: event log / audit chain) |
| Unexplained false suppress on SEV1-class alert | eval / shadow / production | **PAGE** | CHARTER design law #4: a false suppress is a SEV1 | 5 min | RB-2 (`ops/RUNBOOKS.md`) |
| Disk guard CRIT: spill + control-plane page | D14 guard (`_enqueue_control_plane_page`) | **PAGE** | SQLite writes dying; the one failure mode that degrades to silence | 5 min | RB-6 |
| Watcher-down (dead-man's-switch on the external watcher) | §10.2 watcher contract | **PAGE** | our pager for the pager is dead — pages via a path that never traverses the receiver | 5 min | `ops/devops-foundation.md` §10.2 |
| Andon stop declared | any lane | **PAGE-equivalent to the room** | the line stops; the stopper is thanked, never blamed | immediate | OPERATING-RULES §5.3 (written reason within the hour; 2 h adjudication) |
| `/healthz` 503 with `gate_constructed` **degraded only** (Jev down) | healthz / audit passthrough ratio | **TICKET + vendor ticket** | fail-open carries paging — every alert pages as before Sentinel existed; designed degradation, not an incident | 30 min | RB-5 |
| Source `stale` | `heartbeat-check.py` exit 1 | **TICKET** | quieter than usual ≠ dead; check the sender side when convenient. If recurring nightly, fix `expected_interval_s` in `heartbeats.json` (Type 2) — don't train the team to ignore it | convenient | RB-8 note |
| `webhook_auth_fail_open: true` on `/healthz` | healthz | **TICKET (Vault-visible)** | signatures not enforced — fix before trusting the pipeline; no pages lost, but a security degradation | 30 min | receiver config fix; see `docs/deploy-production.md` §6 |
| Disk watermark WARN (before CRIT) | `disk-watermark.sh` | **TICKET** | early action per the ADR-024 disk-full arithmetic; the guard hasn't fired | 30 min | RB-6 |
| Deploy failure: gate-red abort or unhealthy auto-rollback | `deploy.sh` output + `deploy.log` | **TICKET** | the box self-healed to the last-known-good artifact; human work is the follow-up, attach the kept-forensics artifact dir | 30 min | `docs/deploy-discipline.md` §5 |
| CI red / flaky CI | `.github/workflows/ci.yml` | **TICKET to Relay** | no production impact, but flaky CI is a §5-reportable incident to Relay (OPERATING-RULES §1.5) — "merge anyway" is never the answer | 30 min | RB-1 |
| Error-budget / toil trends; postmortem action-item hygiene | weekly on-call review | **TICKET** | slow burns don't page; they get tracked and reviewed | weekly review | `ops/devops-foundation.md` §2.5 |
| Anything failing on `staging-shadow` or `staging-lab` | shadow / lab | **NEVER PAGE** | staging is never a paging pipeline (tier table); shadow routes never fall through to the paging last resort | n/a | fix in the tier where it broke |

## Review cadence

The on-call reviews this table monthly alongside the incident log:
paging medians, the page→action-item ratio, and any row whose
classification felt wrong in practice. A classification that survives
contact with reality unchanged is trusted; one that pages wrongly twice
is rewritten. The table is a contract, and contracts are versioned —
changes get a decision-log line (OPERATING-RULES §1.2).

## Skill & Evidence

- **principal-systems · infrastructure constitution**: "Every page must
  be actionable and tied to an SLO threat." The three-gate rule above is
  that clause operationalized; the table is its implementation.
- **execution-doctrine §8**: toil ≤50%, on-call ≤25% of time; every page
  actionable; paging medians near zero; a component paging daily means
  something else is about to break on top of it. The quarterly-page
  threshold and the monthly review come from here.
- **principal-governance · unified telemetry**: a page must arrive with
  the evidence to diagnose it — healthz names the failed predicates,
  the dead-man trip names the silent subject, forward-failure names the
  runbook line. No diagnosis without shared evidence.
- **principal-mindset §11 (anti-theater)**: a paging practice that never
  produces action items is theater — hence the review cadence and the
  rewrite rule.
- **Web:** Google SRE Book, Ch. 11 "Being On-Call" (on-call load budget,
  paging response discipline — chapter catalogued at
  https://github.com/andersonfpcorrea/andersonfpcorrea-skills/blob/HEAD/plugins/book-skills/skills/google-sre/SKILL.md,
  accessed 2026-10-05).
- **Repo evidence:** RB-2 in `ops/RUNBOOKS.md`; RB-4..RB-8 and §10
  (watcher, silent-vs-stale) in `ops/devops-foundation.md`;
  `docs/liveness.md` (the five `healthz` predicates);
  `docs/deploy-production.md` §6–7 (`FORWARD FAILED`, `BROKEN:
  checkpoint`, `webhook_auth_fail_open` semantics).
