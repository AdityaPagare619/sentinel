# Sentinel — Lane Registry

Every lane that has ever existed: branch, owner, status, outcome, commits.
A lane is DONE when its branch is merged (or explicitly killed with a logged
reason) and this registry carries the outcome. New lanes are registered here
at creation — a lane that isn't registered doesn't exist.

| Branch | Owner (chief) | Status | Outcome | Commits |
|---|---|---|---|---|
| `base/setup-docs` | Petu (base setup) | done | 10 base docs: charter, methodology, teams, roadmap, local-ops, ledgers, CI, PR template, gitignore | `f4aa005` |
| `lane/code-mvp-v0.1` | Build coordinator (Forge oversight) | done (merged PR #1) | v0.1 engine end-to-end; 140/140 tests green (Petu-verified); mock dev mode + fixes | `a8a7cf4`, `5c4bcb1` |
| `lane/research-night-1` | Night R&D coordinator | done (merged PR #2) | standing R&D charter + 5 department agendas + 5 web-verified sprint notes + synthesis with 9 proposed ADRs | `8eda683` |
| `lane/company-v2` | Architect wave (Forge) | done (merged PR #5) | 8 per-chief operating files (`chiefs/`) | `afd16dc` |
| `lane/platform-arch` | Architect wave (Forge) | done (merged PR #3) | `PLATFORM_ARCHITECTURE.md` frozen + `WAVE_PLAN.md` | `6ed1138` |
| `lane/ops-deepening` | Architect wave (Relay/Vault) | done (merged PR #4) | `ops/RUNBOOKS.md`, `ops/STANDING_ORDERS.md`, decision-log + constraint-registry deepening | `5020e5a` |
| `lane/repo-completeness` | Repo completeness coordinator | done (merged PR #6) | company-depth docs: log, master plan, lane registry, repo map, team deepening, lane playbook, briefs archive, onboarding, completeness audit | `0ee0839`, `8ea9845`, `076fd8d`, `1cae269`, `64c078c`, `7a4b275`, `88ee47a` (+ this registry update) |
| `lane/relay-ops` | Relay (ops) | done (merged PR #7) | ops/milestones.md + ops/blockers.md; independent review APPROVED | `5259afa` |
| `lane/vault-hardening` | Vault (security) | done (merged PR #8) | docs/SECURITY.md; fabricated citation caught in review, fixed (a38fa05) | `827490c`, `a38fa05` |
| `lane/tripwire-acceptance` | Tripwire | done (merged PR #13) | acceptance-sunday.md, fault-injection-plan.md, ci-repair.md (escalated: GitHub ghost-record platform bug) | `6c71d0e` |
| `lane/impl-event-log` | Ledger | in flight | design 02: hash-chained event log + outbox, v0.1 migration, 4 commits | `b2e0005` |
| `lane/impl-race` | Forge | in flight | design 01: race-to-page B=1000ms | — |
| `lane/impl-gate` | Oracle/Tripwire | done (merged PR #15) | design 04: ADR-013 quantized probability lock, M-1 fix; 3 cross-lane tests adopted | `5ada371`, `0cd1356` |
| `lane/impl-freshness` | Vault | done (merged PR #14) | design 07: C-1 freshness proofs, stale⇒page | `3803a72` |
| `lane/impl-shadow-tap` | Prism | in flight | design 06: read-only PD/Opsgenie tap + Shadow Report | — |
| `lane/oracle-latency` | Oracle | in flight | N=100 latency campaign (measurement) | — |
| `lane/impl-forwarder` | Pager | in flight | design 03: durable forwarder, PD dedup_key, standby | — |
| `lane/impl-liveness` | SRE | in flight | design 05: livez/healthz, 503-not-429, config validation | — |
| `lane/remove-ci` | Petu (coordinator) | done (merged PR #20) | removed .github/workflows/ per Aditya 13:42 order; METHODOLOGY.md gate → local tests | `squash-merge` |
| `lane/d7-history-provenance` | Builder D7 | pending review (PR #72) | design D7: 72h single-clock history provenance, label-pipeline staleness SLO with paging, novelty shadow-first | `b0a3d62` |
| `lane/d5-corroboration` | Builder D5 | in flight | D5/ADR-019: corroboration leg in the live suppress conjunction; versioned/audited/two-person silence floor; strict-signature resolve path; freshness on evidence. Holds the gate registry (src/sentinel/gate.py) until PR merges; D9 follows | — |
| `lane/d10-resolve-wiring` | Build coordinator (D10 wiring) | pending review (PR #74) | D10 R9 fix: wire resolve_episode into production — signed PD resolve/ack claims on /v2/enqueue close episodes (verified_resolve, silence-direction fail-closed in every mode); POST /episodes/resolve operator path (operator_resolve, bearer-token required); every close event-logged (episode_resolved); 18 new tests, 741 green | `c18b865` |

## Rules

- One branch = one reviewable unit = one registry row. Register at creation.
- Statuses: `planned` → `in flight` → `pending review` → `done` (merged) or
  `killed` (reason logged in `ops/decision_log.md` — killed lanes stay in the
  table; history is not rewritten).
- Unmerged branches are normal mid-wave; at wave end every branch is merged or
  killed. No branch rots silently — Relay's blocker sweep treats a 3-hour-quiet
  branch as blocked until proven otherwise.
| `lane/c1-stepped-failopen` | Builder C1 | in flight | C1: adaptive storm detector (correlator) + stepped fail-open (gate) as ONE architectural change per design/rfc-c1-stepped-failopen.md. Holds src/sentinel/gate.py + src/sentinel/correlator.py until PR merges | — |
| `lane/12h-dev-1-r10` | dev-1 (devops/environments) | in flight (PR #94 open, unmerged) | R-10 wire-or-retract: flags.json schema + loader wiring into the live gate. T+3 checkpoint: wiring committed+pushed (`b0e979e`/`645f7bc`/`8c07bf2`, verified `8c07bf2` on origin), drill rehearsal 7/7 PASS on local staging-lab (built via env-bootstrap.sh --tier staging-lab; shared lab NOT provisioned by Relay — see blockers.md), drill plan final, retract ADR skeleton pre-written (F1 T+6 edge done early). 21 new tests green; full suite green except 3 pre-existing unrelated failures (documented in PR body). Awaiting: Relay review + Vault sign-off (mandatory); T+9 drill verdict (re-run fresh) owns the merge/retract decision. | `b0e979e`, `645f7bc`, `8c07bf2` |
