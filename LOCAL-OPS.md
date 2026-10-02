# Sentinel — LOCAL-OPS: how agents work in this room

The repo (`AdityaPagare619/sentinel`) is the source of truth for code + docs — but
**agents operate here**, in `~/workspace/jev-builds/sentinel/`. Same professional
machine in both places: this room mirrors the repo, and the repo mirrors this room.

## 1. The repo is initialized from here

- Local git repo lives in this dir, branch `main` (created via `git init -b main`).
- Base-setup work ships on `base/setup-docs`; all lanes branch off `main` as
  `lane/<lane>-<short-desc>`.
- Remote `origin` = `https://github.com/AdityaPagare619/sentinel.git`. Push when the
  token can see the repo (Aditya's 30-second phone step); until then, branches live
  locally and this dir remains the working source of truth.

## 2. Branch-per-lane convention

| Who | Branch | May write |
|---|---|---|
| Base setup | `base/setup-docs` | CHARTER.md, METHODOLOGY.md, TEAMS.md, ROADMAP.md, LOCAL-OPS.md, `ops/`, `.github/`, `.gitignore` |
| Build coordinator | `lane/build-*` (their choice of name) | `src/`, `tests/`, `PROGRESS.md`, `README.md`, `ARCHITECTURE.md` (frozen — edits only via Forge with a logged decision) |
| Other lanes | `lane/<lane>-<short-desc>` | their lane's files only; crossing lanes → flag in the PR, never silent |

**Never commit another lane's files.** If you need a change in someone else's files,
open the PR against your own files and note the request — the owner makes the edit.

## 3. Commit message format (conventional commits)

```
feat(gate): expected-cost threshold policy
fix(receiver): forward original bytes on unparseable payload
docs(readme): zero-to-first-alert quickstart
test(gate): kill-the-client fail-open assertion
chore(ci): add secrets-grep step
```

- Imperative, <72 chars, scope in parens.
- Body: *why* (the decision), one or two lines.
- Footer when a decision was logged: `Decision: ops/decision_log.md#<date>-<slug>`.

## 4. What gets committed when

- **Docs/ops on your branch:** commit freely and often on your own branch. Small,
  reviewable commits > one giant dump.
- **Code commits belong to the build coordinator.** Do NOT commit `src/`, `tests/`,
  `PROGRESS.md`, `ARCHITECTURE.md`, or `_shims.py` yourself — read them for context,
  propose changes via the coordinator or a flagged PR request.
- **Never commit:** secrets/keys/`.env`, `__pycache__/`, `*.pyc`, `.pytest_cache__/`,
  `node_modules/`, SQLite dev DBs (`*.db`, `sentinel.db`), `TYPESAFE_API_KEY` values,
  webhook secrets, routing keys. (`.gitignore` enforces; CI secrets-grep double-enforces.)
- **Commit cadence:** at least one commit per meaningful unit of work; push the branch
  when the remote is reachable. A day's work must never live only on one agent's
  scratch.

## 5. Push + PR flow (once the remote is reachable)

1. `git push -u origin <your-branch>`
2. Open a PR: `main` ← your branch, using `.github/pull_request_template.md`
   (tests green, docs updated, no secrets, Claim-Auditor pass on numbers).
3. Review must come from a **DIFFERENT agent** — no self-merge.
4. CI (`.github/workflows/sentinel-ci.yml`) must be green: full suite, repeatability probes,
   kill-the-client, secrets-grep.
5. Squash-merge → delete the branch. `main` history stays a clean sequence.

## 6. main stays green

- `main` is protected: PR-only, no direct pushes, green CI required.
- If `main` breaks, the breaker (or whoever finds it) owns the fix; all lanes pause
  merges until green. A red `main` is a stop-the-line event.
- Never force-push `main`. Ever.
