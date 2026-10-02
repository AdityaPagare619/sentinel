# Sentinel — Lane Registry

Every lane that has ever existed: branch, owner, status, outcome, commits.
A lane is DONE when its branch is merged (or explicitly killed with a logged
reason) and this registry carries the outcome. New lanes are registered here
at creation — a lane that isn't registered doesn't exist.

| Branch | Owner (chief) | Status | Outcome | Commits |
|---|---|---|---|---|
| `base/setup-docs` | Petu (base setup) | done | 10 base docs: charter, methodology, teams, roadmap, local-ops, ledgers, CI, PR template, gitignore | `f4aa005` |
| `lane/code-mvp-v0.1` | Build coordinator (Forge oversight) | done | v0.1 engine end-to-end; 140/140 tests green (Petu-verified); mock dev mode + fixes | `a8a7cf4`, `5c4bcb1` |
| `lane/research-night-1` | Night R&D coordinator | done | standing R&D charter + 5 department agendas + 5 web-verified sprint notes + synthesis with 9 proposed ADRs | `8eda683` |
| `lane/company-v2` | Architect wave (Forge) | pending review — unmerged | 8 per-chief operating files (`chiefs/`) | `afd16dc` |
| `lane/platform-arch` | Architect wave (Forge) | pending review — unmerged | `PLATFORM_ARCHITECTURE.md` frozen + `WAVE_PLAN.md` | `6ed1138` |
| `lane/ops-deepening` | Architect wave (Relay/Vault) | pending review — unmerged | `ops/RUNBOOKS.md`, `ops/STANDING_ORDERS.md`, decision-log + constraint-registry deepening | `5020e5a` |
| `lane/repo-completeness` | Repo completeness coordinator | pending review — unmerged | company-depth docs: log, master plan, lane registry, repo map, team deepening, lane playbook, briefs archive, onboarding, completeness audit | `0ee0839`, `8ea9845`, `076fd8d`, `1cae269`, `64c078c`, `7a4b275`, `88ee47a` (+ this registry update) |

## Rules

- One branch = one reviewable unit = one registry row. Register at creation.
- Statuses: `planned` → `in flight` → `pending review` → `done` (merged) or
  `killed` (reason logged in `ops/decision_log.md` — killed lanes stay in the
  table; history is not rewritten).
- Unmerged branches are normal mid-wave; at wave end every branch is merged or
  killed. No branch rots silently — Relay's blocker sweep treats a 3-hour-quiet
  branch as blocked until proven otherwise.
