# Sentinel — Blocker Log (live)

**Owner:** Relay (ops/cadence). Last updated: Sat 2026-10-03 ~00:30 IST.
A lane lead who hits a blocker reports it here (or to Relay) within the hour —
lanes never block silently on each other; they mock the contract and move.

## Standing rules

**1. Update discipline (hard).** Every blocker gets a **born** line and a
**killed** line with a timestamp. Killed blockers move to the KILLED section —
they are **never silently dropped or deleted**. A blocker that disappears
without a killed line is a lie the log caught.

**2. The anti-collision ritual (hard).** This is the one ops ritual that keeps
the parallel lanes from colliding: **the single-owner rule — each lane owns
its files; crossing into another lane's files requires a flagged PR, never a
silent edit; the file owner makes the edit** (LANE_PLAYBOOK.md §6,
LOCAL-OPS.md §2, WAVE_PLAN.md risk #4). On top of it, **Relay's blocker sweep
treats any 3-hour-quiet branch as blocked until proven otherwise**
(lane_registry.md). Parallelism without collisions = single owners +
flagged crossings + sweep.

## ACTIVE

| Blocker | Since | Owner | Impact | Unblock path |
|---|---|---|---|---|
| GitHub Actions `startup_failure` at repo level | Sat 2026-10-03 ~00:22 IST (Saturday wave dispatch) | Tripwire (investigating) | Exit bar 10 (`main` green) unverifiable on the hosted runner; Wave B branch CI can't run remotely until repaired | Tripwire diagnosis → fix → green rerun. Meanwhile lanes hold local CI green (`unittest discover tests` + kill-the-client + secrets-grep) — LANE_PLAYBOOK step 7's "red = no merge" applies to local CI until Actions is back |
| ADR-001 / ADR-005 / ADR-007 awaiting Aditya's decision | Fri 2026-10-02 evening (night R&D sprint, 9 proposed ADRs; ROAD_TO_LAUNCH.md §5) | Aditya | ADR-001 gates correlator craft (flap-debounce, resolved/refired episodes, P1/P2-never-silenced) in the river lane · ADR-005 gates Vault's webhook verification hardening scope for Sunday · ADR-007 gates Prism's muted-not-dropped visibility state | Aditya accept/defer/reject. Lanes proceed against the *proposed* ADR text and log assumptions in `ops/decision_log.md` until the decision lands |
| Oracle latency campaign in flight (gates timeout policy) | Fri 2026-10-02 ~23:30 IST (N≥100 campaign launched after N=1 measurement: 11.4s vs 70–500ms spec) | Oracle | paging-path timeout + fail-open policy and milestone M5 ("latency results in") depend on measured p50/p95/p99 + flip data; dashboard latency view shows the honest caveat until results land | campaign completes → numbers published (research + dashboard). The tight timeout ships *regardless* — fail-open protects the path meanwhile. Demo never depends on vendor availability (mock-backed) |

## KILLED (born + killed, never dropped)

| Blocker | Born | Killed | Resolution |
|---|---|---|---|
| GitHub push 403/404 — fine-grained PAT lacked repo access (`AdityaPagare619/sentinel` not in token's selected repos) | Fri 2026-10-02 ~18:20 IST | Sat 2026-10-03 ~00:25 IST | Aditya supplied an all-access PAT; saved in gh credential store (`~/.config/gh/hosts.yml`, old token backed up); verified push+admin+maintain. All 7 branches pushed natively; remote `main` = `f4aa005`; PRs #1–#6 opened (merges held for cross-agent review). `sentinel-push-watch` cron removed (superseded). Logged in `ops/SENTINEL_LOG.md` (`d465f99`). PAT path is canonical; GitHub App connector is backup only. **Standing note:** the raw key traveled through chat — Aditya should rotate it when convenient and hand over the replacement the same way. |
