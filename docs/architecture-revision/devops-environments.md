# DevOps + Environments — Phase-2 Domain Review

**Reviewer:** Phase-2 domain reviewer (DevOps + environments) · **Date:** 2026-10-05 IST
**Branch:** `program/architecture-revision-dom-devops` (off `program/architecture-revision`)
**Standard:** Phase-1 `research/devops.md` (Stream 3) + `research/swe-discipline.md`; skills: principal-systems, principal-governance, principal-mindset, execution-doctrine.
**Method:** fresh eyes — I did not build any of this. Every claim below was verified by reading the code/docs on this branch, not by trusting summaries. "It's fine" appears nowhere without evidence.

---

## 1. WHAT EXISTS — grounded in the repo

Every DevOps artifact inventoried, with what it actually is versus what it claims to be.

### 1.1 The doctrine: `ops/devops-foundation.md` (L3, 2026-10-04)

A 1,000+-line ops constitution: environment tiers (`local` / `staging-shadow` / `staging-lab` / `prod`) with the **never-pages rule** as Type-1 law; a release process separating *code deploy* from *feature release*; CI discipline under the no-GitHub-Actions standing order; IaC direction; secrets policy; runbooks RB-4 through RB-8; the A3 per-source heartbeat/dead-man's-switch; a pre-mortem; and flagged requests to build lanes (L3-FLAGS-1..3, L3-A3-1, L3-OPS-1). Quality of writing is high; the pre-mortem is honest. **But it is a document, and several of its load-bearing mechanisms are not wired into the running system** — see §2.

### 1.2 The scripts: `scripts/ops/` — real, executed, load-bearing

| Script | What it actually does (verified) |
|---|---|
| `pre-pr-gate.sh` | Secrets grep → full suite → kill-the-client named → boot smoke → schema validation. Supports `--fast` (skips full suite) for deploys. The order-compliant CI substitute. |
| `deploy.sh` | **Immutable-artifact deploy**: pins a full 40-hex SHA, clones to a timestamped artifact dir, runs the fast gate on the pinned SHA, swaps a `current` symlink, restarts via operator-supplied command, health-gates on `/healthz` (requires `ok:true`, not just HTTP 200), auto-rolls-back the symlink on failure, appends to `deploy.log`. Refuses unpinned refs and dirty state. This is the real deploy story. |
| `rollback.sh` | Symlink swap to previous artifact, restart, verifies `/healthz ok:true`, **refuses to declare success otherwise** (the anti-PR-#65 instinct, applied to ops). Escalates to RB-7 if the previous artifact is also unhealthy. |
| `flagctl.py` | Atomic tmp+rename flag writes, keeps 5 validated generations, appends to `flags-changes.jsonl` (who/when/why), enforces the two-person asymmetry for flipping `global_kill_switch` OFF. |
| `heartbeat-check.py` | A3 dead-man: reads the SQLite log read-only from the external host, per-source `ok → stale → silent` states, exit codes for cron. |
| `env-bootstrap.sh` | Fresh-tree builder for `local` and `staging-lab` rebuilds (config/state dirs, Python ≥3.12 check, example configs, env template). Idempotent. |
| `disk-watermark.sh`, `receiver-smoke.sh`, `secrets-grep.sh`, `watchdog_watcher.py` | Nagios-style disk exits, boot smoke, secret-shape grep, watcher glue. |

The foundation doc's footer states every script was executed against `origin/main @ 605793f` before commit — that is a verifiable claim about that SHA, and the scripts read as written-by-someone-who-ran-them (the rollback.sh top-level-`ok` comment, the deploy.sh 503-is-unhealthy check).

### 1.3 The production guide: `docs/deploy-production.md`

Fresh Ubuntu box → running production: layout (`/opt/sentinel/repo`, `/var/lib/sentinel/{state,config}`, `/etc/sentinel`), the one non-stdlib dep (`cryptography==44.0.3`, ratified), two systemd units (receiver :8080, platform :8081, process isolation so dashboard load can't starve the pager), Caddy reverse proxy with TLS + CORS for the Pages console, BYOK key onboarding, `/livez` + `/healthz` checks, journalctl log guidance (`FORWARD FAILED`, `BROKEN: checkpoint`), a troubleshooting table. Concrete, copy-pasteable, ~20 minutes. **This is the doc a new operator actually follows** — which makes §2's finding #5 dangerous.

Also present: `docs/prod-cutover-checklist.md`, `docs/runbook-cutover.md`, `ops/watcher-deployment.md` (external-watcher host story with explicit fate-domain separation — honest that the watcher host is the operator's existing infra, ₹0).

### 1.4 The running process: verified in `src/sentinel/` + `platform/server/`

- **Endpoints (real):** `/livez` (unauthenticated), `/healthz` (bearer, named predicates incl. `webhook_auth_fail_open`, `config_current`, `gate_constructed`, `forwarder_draining`, `no_crashloop_signature`), `POST /-/reload` (bearer; SIGHUP also), `/shadow/{pagerduty,opsgenie,alertmanager}` ingest routes, platform `:8081` `/api/*` read paths. **No `/metrics` endpoint anywhere** (verified by grep).
- **Config loader:** 4-stage load (parse → schema → semantic → atomic swap); invalid generation → live generation untouched + `config_rejected` emitted; last-good chain of checksummed generations. Verified in `src/sentinel/config.py`.
- **Shadow:** `ShadowPipeline`/`ShadowStore` real module; receiver ingests `/shadow/` routes with separate failure semantics.
- **Flags:** `flags.json` schema exists in the doc; **`flagctl.py` writes it; nothing in `src/` reads it** (zero hits for `flags.json`/`global_kill_switch` in `src/`, verified by grep). The kill switch, the `<5s` flag-off, the two-person ceremony, and the monthly kill-switch drill are **intent on disk, not capability in the process**. The foundation doc discloses this honestly (L3-FLAGS-1: "flags.json is not yet loaded into the live gate") — but the release rollback table (§2.4) still lists flag-off at `<5s`, the drill cadence (§2.5) still treats the flip path as real, and the "no emergency code rollbacks" claim (§2.3) rests on a mechanism that doesn't execute.
- **Instrumentation gap (disclosed):** `decision_requested` exists as a contract in `eventlog.py`; **no call site in `src/`** (verified). Per-source heartbeat subjects all report `unknown`; only `__pipeline__` works. The doc flags this (L3-A3-1) — credit for honesty, but the heartbeat system's headline feature is dormant.
- **Logging:** plain-text lines (`[sentinel] FORWARD FAILED action=... alert=...`), journalctl. No JSON structured logging, no trace fields.
- **Correlation:** **zero** `trace_id`/`correlation_id`/`request_id` anywhere in `src/` or `platform/` (verified). The webhook → decision → forward chain is three unlinkable log streams, exactly as `research/devops.md` §3.3 warns.

### 1.5 The staging/prod story

- **Tiers:** `local` (mock, no secrets), `staging-shadow` (full-fidelity tap copy, never-pages enforced by startup refusal of prod routing keys — build request L3-FLAGS-3), `staging-lab` (stub vendors, fault-injection drills, rebuilt from `env-bootstrap.sh`), `prod` (pinned SHA, real secrets). The "staging is never a second paging pipeline" classification is the `research/devops.md` §4.3 rule implemented as code-adjacent policy — genuinely ahead of many small teams.
- **Console:** `deploy/gh-pages/build-static.py`; static prod console on GitHub Pages (+ `/staging/` simulated showcase per PR #79). Deploy via the `gh-pages` branch.
- **Upgrade path:** see §2 finding #5 — the doc and the scripts disagree.

### 1.6 What the foundation doc explicitly rejected (alternatives considered)

GitHub Actions (standing order), full blue-green with LB (flag-off deemed faster — but flag-off isn't wired), Kubernetes/containers, live 5% traffic canary (ADR-022 shadow-diff instead), LaunchDarkly (flags.json + lifecycle rule). The rejections are reasoned and mostly right-sized for a single-box stdlib process.

---

## 2. WHAT'S MISSING — vs the Phase-1 enterprise standard

Mapped against `research/devops.md`'s 17-item checklist (§6), its named findings, and the governance checklist. Ordered by severity of the claim-vs-reality gap, not by effort.

### 2.1 The flag story is fictional at runtime (checklist: decoupled deployment)

**The damning finding.** `research/devops.md` §2.2–2.3 and the governance skill demand *deploying code ≠ releasing features* with instant flag-off. The foundation doc's §2 builds the entire release-safety architecture on it: flag-off rollback `<5s`, kill-switch drill, canary-by-severity-band, "no emergency code rollbacks." **None of it executes** — `flags.json` has no reader in `src/` (verified). The doc's own honesty (L3-FLAGS-1) is buried in a subsection while the rollback table, drill cadence, and the headline release claim present the capability as live. A new operator reading §2.4 believes they have a 5-second flag-off. They do not. The monthly kill-switch drill, if run today, would flip flags on disk that the receiver ignores — **a drill of a rumor**, violating the doc's own "a runbook that has never been drilled is a rumor" standard in reverse (drilling something that isn't wired).

### 2.2 The operator guide teaches the anti-pattern the scripts reject (checklist: GitOps-for-one-box)

`docs/deploy-production.md` §8 (Upgrading): `git pull --ff-only && sudo systemctl restart`. `ops/devops-foundation.md` §4.2 and `scripts/ops/deploy.sh`: pull-and-restart is "SSH-and-pray"; deploys must be pinned-SHA immutable artifacts with health-gated swap. **The doc every new operator follows teaches exactly the procedure the ops doctrine forbids.** This is worse than having no doctrine: the doctrine exists, and the guide routes around it. The restart blind window in the paging path (`research/devops.md` §2.3 gap #2) is therefore the *documented default*, not a known deviation.

### 2.3 No `/metrics` endpoint (checklist item #1 — the research's highest-leverage gap)

Confirmed absent in code. `research/devops.md` §6 ranks it #1: ~50 lines of stdlib Prometheus text format, unlocking burn-rate math, canary auto-gates, and dashboards. Every downstream observability item (SLO budgets, burn-rate alerts, shadow-diff auto-gate) is blocked on it. The receiver already has `/healthz` predicates and `tap_lag_s`; exposing counters/histograms on a `/metrics` route is the mechanical next step. Without it, "metrics detect → traces locate → logs explain" (`research/devops.md` §3.1) starts at logs.

### 2.4 No correlation ID (checklist item #2; governance checklist)

Verified: zero trace/correlation/request IDs in `src/` and `platform/`. The governance checklist demands "one Trace-ID per user interaction"; the principal-governance skill's unified-telemetry pillar requires frontend errors joined to backend traces. Today a missed page is debugged across three unlinkable streams (webhook log line, decision row, forwarder log line). For a paging pipeline, this is a diagnosis-time multiplier on every SEV1.

### 2.5 No post-merge verification (the research's #1 CI finding)

`research/devops.md` §1.3 names post-merge integration effects as "the single most important thing CI adds over local runs": two lanes green at their base SHAs can conflict at merge, and the local gate runs on the *branch*, not on *main-after-merge*. Nothing re-runs the gate on main after merge — no cron, no systemd timer, no clean-worktree runner. With parallel lanes merging continuously, this is the structurally unguarded hole. The research's compliant answer (cron/systemd-timer post-merge runner on a clean worktree, posting per-commit verdicts — CI in the mechanism sense, no GitHub Actions) is unimplemented.

### 2.6 Config is unversioned and undeployed-with-code (checklist item #8: GitOps-for-one-box)

`deploy.sh` pins the code SHA but snapshots **nothing** of `/var/lib/sentinel/config/` (verified: zero config handling in the script). `rollback.sh` restores the previous *code* artifact — not the config that was live with it. `deploy-production.md` seeds config with `cp` of example defaults. Every production config change is therefore an uncommitted, unaudited, un-rollbackable mutation on the box — the exact failure `research/devops.md` §4.1 (factor III: config in environment) and §2.1 (GitOps principle 2: change = new commit) are designed to prevent. GitOps-for-one-box (§6 items #8/#9) is entirely absent: no config-as-commits, no parity/drift audit script.

### 2.7 No written availability SLO or error budget (checklist item #4)

Fragments exist: the 2700ms gate budget (a latency SLO fragment), `tap_lag_s` with a referenced-but-unwritten SLO threshold (`research/devops.md` §3.2 notes the 2700ms fragment). No document states "X% of real pages forwarded successfully within 30 days" or any availability target, and therefore no error budget exists to govern releases (principal-systems: reliability is policy, not hope; the budget converts the SLO into release policy). The cutover checklist gates on drill freshness and shadow evidence but on no numerical reliability contract.

### 2.8 No page-vs-ticket classification for Sentinel's own signals (checklist item #5)

Runbooks RB-4..RB-8 name signals (`BROKEN: checkpoint`, `FORWARD FAILED`, `webhook_auth_fail_open`, disk watermark, dead-man trip) but there is no single policy table classifying each as *page* vs *ticket* with a linked runbook — the SRE "every page must be immediately actionable" standard (`research/devops.md` §5.2) and the governance "alerts without runbooks" anti-pattern. The watcher is poll-based liveness; nothing computes budget burn (checklist item #16).

### 2.9 No postmortem template; the incidents dir doesn't exist (principal-systems operating rule 5)

RB-4/RB-6/RB-7 all close with "log the incident in `ops/incidents/`" — **the directory does not exist** (verified). No blameless-postmortem template (Summary → Impact → Root Causes → Trigger → Resolution → Detection → Action Items → Lessons incl. "where we got lucky" → Timeline) exists in `ops/`, despite the principal-systems skill mandating postmortems within 72h and the foundation doc's own postmortem culture claims. The mechanism the whole learning loop depends on has no artifact.

### 2.10 Instrumentation and drill gaps (honest but unclosed)

- `record_request()` never called → per-source heartbeats dormant (L3-A3-1 open).
- Kill-switch drill drills an unwired path (§2.1).
- Watcher dead-man is a "weekly on-call review" of a state file — the pre-mortem's own warning ("the watcher was a sentence, not a system") applies one level up, and the doc states it without flinching. Credit for honesty; it remains a gap.
- No structured (JSON) logs; no burn-rate alerting (blocked on #2.3); no pre-push hook shipped (complements #2.5); no toil accounting (the upgrade path is the first toil candidate); flag lifecycle rule unwritten (flag sprawl is the known decay mode, `research/devops.md` §2.2); secrets rotation calendar is "a tracked item," not a repo artifact; `staging-shadow` startup refusal of prod keys is a build request (L3-FLAGS-3), not code.

### What is genuinely fine (evidence-attached)

- `deploy.sh`/`rollback.sh` are the real deal: pinned SHA, health-gated, rollback refuses false success. Better than most small-team deploy stories.
- The never-pages tier classification and the shadow-first evidence chain (ADR-022) are architecturally honest answers to "what breaks when staging lies."
- The config loader's 4-stage validation + last-good chain is a real fail-safe against the junior-deploys-stale-config scenario (principal-systems done-checklist).
- The foundation doc's flagged-request mechanism (L3-FLAGS-1, L3-A3-1) is the right way to track doc-vs-code gaps — the failure is that the headline claims (§2.4's `<5s`, §2.5's drill) don't carry the same flags.

---

## 3. CONCRETE REVISION PROPOSALS — ranked by value-per-effort (₹0)

Ranking = (reliability value × irreversibility of getting it wrong) ÷ effort, per `research/devops.md` §6. All ₹0, all compliant with the no-GitHub-Actions standing order. Each names what would verify it — no proposal is done until the verification runs.

### P1. Close the flags gap: wire `flags.json` into the live gate (or retract the `<5s` claim)

**Rationale:** §2.1 — every release-safety claim (flag-off, kill-switch drill, canary-by-flag, "no emergency code rollbacks") is load-bearing on a mechanism with zero readers in `src/`. This is the single largest claim-vs-reality gap in the ops story, and under the standing honesty order it outranks pure feature value. Two acceptable outcomes: (a) the build lane closes L3-FLAGS-1 (schema + loader + gate semantics are settled in `ops/devops-foundation.md` §2.1 — the wiring is mechanical); (b) the doc retracts the `<5s` row, the drill, and the "no emergency code rollbacks" claim until it is real. (a) is strongly preferred — decoupled deployment is the right architecture.
**Verify:** after merge, on `staging-lab`: `flagctl set global_kill_switch true` → `POST /-/reload` → synthetic alert round-trips as `passthrough` within seconds; `flagctl rollback` + reload restores suppression; the monthly drill runs against this real path and its record lands in `ops/drills/`.

### P2. Add `/metrics` (Prometheus text format, stdlib, ~50 lines)

**Rationale:** `research/devops.md` §6 item #1 — the highest-leverage observability gap. Unlocks burn-rate math (P7), the shadow-diff auto-gate (P8), and any future dashboard. Counters: webhooks in, decisions by disposition, forwards ok/failed, Jev timeouts/fallbacks; histograms: gate latency vs the 2700ms budget, forwarder latency; gauges: `tap_lag_s`, outbox depth, config generation.
**Verify:** `curl :8080/metrics` returns valid Prometheus exposition format; a 5-minute load run shows gate-latency histogram buckets populating; the format parses with a Prometheus text parser (no Prometheus server needed to verify).

### P3. Fix `docs/deploy-production.md` §8 to mandate `deploy.sh` (10-line doc edit)

**Rationale:** §2.2 — the operator-facing guide currently teaches the anti-pattern the ops doctrine forbids. Cheapest proposal on this list; kills the restart-blind-window-as-default. Rewrite §8 as: fetch pinned SHA → `deploy.sh --sha` → health-gated swap; `rollback.sh` for reversal; `git pull` on the prod box forbidden by policy.
**Verify:** a fresh-eyes read of the guide by someone who has never seen `ops/devops-foundation.md` produces only the `deploy.sh` procedure; `grep -n "git pull" docs/deploy-production.md` returns nothing in the upgrade section.

### P4. Correlation ID: one UUID at ingress, propagated end-to-end

**Rationale:** §2.4 — the governance checklist and `research/devops.md` §3.3 demand it; today a missed page is three unlinkable streams. Generate at `/hook` and `/shadow/` ingest; thread through correlator → gate → forwarder → event-log row; include in every log line for that request. This is the trace pillar of "metrics detect → traces locate → logs explain."
**Verify:** post one synthetic alert; `grep <uuid>` across journalctl and the event log returns the complete chain (ingest → decision → forward) in one query; a test asserts the UUID appears in all three.

### P5. Post-merge runner on a clean worktree (cron or systemd timer, order-compliant)

**Rationale:** §2.5 — `research/devops.md` §1.3's #1 CI finding: post-merge integration effects are the one thing the local gate structurally cannot catch. A timer that pulls `main` into a throwaway worktree and runs the full gate on each new SHA, posting per-commit verdicts to a status file, replicates CI's three detection classes (post-merge combination, clean environment, blame attribution) with zero GitHub Actions.
**Verify:** push two individually-green commits that conflict semantically; the runner's status file shows the merged SHA red with the exact commit identified; a dashboard-of-record (even a flat file) shows green/red per SHA.

### P6. Config-as-commits: version prod config in Git; deploy and roll back config with code

**Rationale:** §2.6 — GitOps-for-one-box (`research/devops.md` §6 items #8/#9). Keep `/var/lib/sentinel/config` (+ `/etc/sentinel` non-secret parts) as a Git repo (or a per-release snapshot); `deploy.sh` snapshots the config alongside the artifact; `rollback.sh` restores both. Every production config change becomes a commit — audit trail + `git revert` rollback, ₹0.
**Verify:** change a threshold on the box via the sanctioned path; `git log` in the config repo shows the change with author/reason; `rollback.sh` after a bad config deploy restores the previous config generation and `/healthz` reports it.

### P7. Write the first SLO + error budget (a decision doc, not code)

**Rationale:** §2.7 — principal-systems: an error budget converts the SLO into release policy (launches proceed while budget remains, halt when exhausted). Draft: "99.9% of real pages forwarded successfully within 30 days; 99th-percentile gate latency ≤2700ms." The budget then governs the watcher (P9) and the release cadence.
**Verify:** the doc exists with SLI/SLO/error-budget arithmetic (43.2 min/30d at 99.9%); the cutover checklist references it; the first month's budget consumption is computed from the event log (needs P2).

### P8. Automate the shadow-diff PASS/FAIL computation (keep human sign-off)

**Rationale:** `research/devops.md` §2.3 — automated canary analysis without Kubernetes: a script computing decision counts, suppression rate, latency p99, and forward failures from the event log against thresholds, returning PASS/FAIL. ADR-022's two-human sign-off stays, but humans sign *numbers*, not vibes. Removes the largest manual judgment call in the release path.
**Verify:** run against a known-good and a known-bad shadow week; the script returns PASS and FAIL respectively; the sign-off record cites the script's output hash.

### P9. Page-vs-ticket classification table for Sentinel's own signals

**Rationale:** §2.8 — SRE "every page must be actionable" (`research/devops.md` §5.2). One table: signal → page or ticket → linked runbook → expected ack time. (`BROKEN: checkpoint` = page; `FORWARD FAILED` = page; `webhook_auth_fail_open=true` = ticket; disk watermark = ticket escalating to page; dead-man trip = page.) Feeds the watcher config and kills alert fatigue before it starts.
**Verify:** every signal named in RB-4..RB-8 appears exactly once in the table with a runbook link; a new on-call can answer "do I page for X?" in under 30 seconds.

### P10. Postmortem template in `ops/` + create `ops/incidents/`

**Rationale:** §2.9 — principal-systems operating rule 5 mandates blameless postmortems within 72h; three runbooks already reference the missing directory. Google SRE format: Summary → Impact → Root Causes → Trigger → Resolution → Detection → Action Items (owner, priority, tracking) → Lessons (what went well / wrong / **where we got lucky**) → Timeline.
**Verify:** `ops/incidents/` exists; the template exists; the next drill or incident produces a filled postmortem within 72h that names at least one "where we got lucky."

### P11. Wire `record_request()` at receiver ingest (close L3-A3-1)

**Rationale:** §2.10 — unlocks the per-source heartbeat subjects; without it the A3 dead-man watches only `__pipeline__`. The contract exists in `eventlog.py`; the call site is missing.
**Verify:** synthetic alerts from two sources → `heartbeat-check.py` reports both `source_integration` subjects `ok` instead of `unknown`.

### P12. Parity/drift audit script (the "continuously reconciled" mechanism, no Argo CD)

**Rationale:** `research/devops.md` §2.1 — GitOps principle 4 without the machinery. A script that diffs the prod box against the repo: pinned SHA matches `current`? installed `cryptography` version matches the pin? Caddyfile matches the committed template? config schema validates? Reports drift; the on-call runs it weekly.
**Verify:** introduce deliberate drift in `staging-lab` (edit the Caddyfile, install a wrong dep); the script flags both; clean lab reports no drift.

### P13. Ship an installable pre-push hook (fast subset of the gate)

**Rationale:** complements P5 — shifts the filter to zero effort (lint/format + changed-area tests), catching the cheap failures before they reach the post-merge runner. Tiny.
**Verify:** `scripts/ops/install-hooks.sh` installs it; a commit with a secrets-shaped string is rejected at push time with the grep output.

### P14. Quarterly toil audit + secrets rotation calendar as repo artifacts

**Rationale:** the toil cap (`research/devops.md` §5.1) is enforced by measurement, and the 90-day rotation calendar is currently "a tracked item" — invisible. Two tiny artifacts: a quarterly checklist (list repetitive ops tasks, automate the top one — first candidate: the upgrade path) and a `ops/secrets-rotation.md` calendar with last-rotated dates.
**Verify:** the calendar exists with dates; the first toil audit names the top toil item and files the automation task.

### P15. Burn-rate alerts on the watcher (after P2 + P7)

**Rationale:** SLO-based paging (`research/devops.md` §3.2, multi-window 14.4×/6×/1×) replaces raw threshold polling. Depends on `/metrics` and the written SLO — sequenced last deliberately.
**Verify:** synthetic error injection at 15× burn for 1h pages; 1× burn over 3d tickets; no pages on a quiet week.

### P16. Blue-green receiver on one box (deferred, conditional)

**Rationale:** `research/devops.md` §2.3 gap #2 — the restart blind window. The foundation doc rejected this in favor of flag-off; since flag-off isn't wired (P1), the honest interim is the health-gated `deploy.sh` + `rollback.sh` that already exists. Revisit only if restarts measurably lose pages after P1–P3 land: two systemd units + Caddy port flip after `/healthz`. Explicitly not now — building it before P1 would be local optimization (principal-systems law 1).
**Verify (if ever built):** deploy during synthetic load; zero dropped webhooks across the cutover; rollback completes in seconds.

### Negative list (what NOT to do)

- Don't adopt Kubernetes/Argo CD for a two-process stdlib system — the mechanisms port (P6, P12), the machinery doesn't (`research/devops.md` §6 negative checklist).
- Don't buy LaunchDarkly — `flags.json` + the lifecycle rule is the 80%, once P1 wires it.
- Don't chase DORA deploy frequency as a vanity metric — for a paging pipeline, change failure rate and recovery time are the metrics that matter (`research/devops.md` §1.1).
- Don't build P15/P16 before P1–P3 — sequencing is the proposal.

---

*Reviewer notes: the foundation doc (`ops/devops-foundation.md`) is unusually good for a v0.1 — its pre-mortem, flagged-request mechanism, and stated-vs-hidden gap discipline are the right culture. The revision program's job is to close the flagged gaps (P1, P11, L3-FLAGS-3) and fix the two places where the docs overclaim (P1's `<5s` row, P3's upgrade section). Nothing in this memo requires budget, new infrastructure, or revisiting the no-GitHub-Actions standing order.*
