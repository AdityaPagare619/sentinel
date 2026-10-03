# Sentinel — DevOps Foundation (L3)

**Owner:** L3 (DevOps) · **Date:** 2026-10-04 · **Branch:** `lane/devops-foundation`
**Type labels:** every decision below carries its reversibility Type (principal-systems law).
**Budget:** ₹0 — design + scripts only; no paid services anywhere in this doc.

> Infrastructure constitution: *"If it isn't automated, it doesn't exist."*
> Every manual step in this doc names the script that replaces it, or names the
> follow-up that builds that script. A runbook that has never been drilled is a
> rumor — every runbook here names its drill.

**Reading order for the on-call:** §6 (runbooks) first, then §1–§2, then the rest.

---

## 0. Pre-mortem — how this ops story fails

Assumed: it is 2027, Sentinel is in production, and the ops story below has
failed catastrophically. The three most likely causes:

1. **The external watcher was a sentence, not a system.** ADR-018's Pager note is
   explicit: *"Sentinel down is a SEV1 on a path that doesn't traverse Sentinel"
   is a sentence, not a system, until the watcher has an owner.* Failure mode:
   the receiver dies at 03:12, the watcher cron was never armed or its alerts
   route to a mailbox nobody reads, and the customer discovers the outage at
   09:00 via missed pages. **Mitigation:** the watcher is a named human + a
   named script (`scripts/ops/disk-watermark.sh`, `/livez` pings), armed before
   the first prod cutover, on a written duty roster, with a weekly canary ping
   that must be acked. Watcher-down pages via a path that never traverses
   Sentinel (same dead-man's-switch logic as ADR-022's watchdog).
2. **Shadow evidence was theater.** Staging's traffic mirror diverged from prod
   (tap sampled, replay stale, lab fixtures unrepresentative), so shadow-diff
   sign-offs certified a fiction. **Mitigation:** the tap contract is
   full-fidelity copy with a freshness metric; `staging-lab` fault-injection
   is the second evidence leg and is never allowed to be the only one.
3. **The kill switch rusted shut.** Nobody drilled `global_kill_switch` for six
   months; at 3 AM the operator discovers flagctl writes a generation the old
   receiver build can't parse. **Mitigation:** the monthly gated kill-switch
   drill (§2.5) exercises the real flip path end-to-end.

---

## 1. Environment strategy

Three tiers. The product's own design forces the shape of "staging": Sentinel is
a pre-page gate whose first two weeks are shadow-only, so **staging is never a
second paging pipeline**. A staging box that can page is a second production.

| Tier | What it is | Traffic | Can it page? | Purpose |
|---|---|---|---|---|
| `local` | dev machine, `SENTINEL_MOCK=1` | synthetic fixtures + unit tests | No (no secrets, no routing key) | fastest feedback loop; the gate script IS CI |
| `staging-shadow` | live build, `SENTINEL_SHADOW=1` | full-fidelity copy of prod alerts via the Stage-0 shadow tap (`/shadow/` routes, separate failure semantics — never falls through to the paging last resort) | **Never.** No `PD_ROUTING_KEY` is ever configured here; startup refuses a prod routing key in shadow tier (documented; build-lane request L3-FLAGS-3) | validate → shadow: candidate code/config proved on real traffic shape before it can touch a page |
| `staging-lab` | live build, mock or stub vendors | synthetic + fault-injection (`ops/drills/*`, `stub_pd.py`, `stub_jev529.py`) | No (points at `stub_pd.py`, never PagerDuty) | chaos, kill-switch drills, disk-guard drills, cutover rehearsals; freely destroyable |
| `prod` | pinned SHA, real secrets | real alert webhooks | Yes — this is the only tier that pages | the business |

**Type:** the tier definitions and the never-pages rule are **Type 1** (once
onboarding and drills depend on them, changing them silently re-opens the
missed-page pre-mortem). Drill cadence and fixture sets are Type 2.

### 1.1 What each tier is for (the contract)

- **`local`**: `scripts/ops/env-bootstrap.sh` creates a working tree from zero:
  config dir (`thresholds.json`, `allowlist.json`, `flags.json`), state dir,
  Python ≥3.12 check, and a boot self-test. The local full suite
  (`python3 -m unittest discover tests`, currently 496 tests, ~108s) is the
  promotion gate — no exceptions, no "it'll pass in CI" (there is no other CI).
- **`staging-shadow`**: the ONLY place a release candidate runs before prod.
  Fed exclusively by the shadow tap; the tap's contract is: byte-identical
  alert copies, delivered to `/shadow/` routes, with a `tap_lag_s` freshness
  metric surfaced on `/healthz`. If tap lag exceeds its SLO, shadow-diff
  evidence is marked stale and cannot sign a release (§2.3).
- **`staging-lab`**: where RB-4..RB-7 get drilled monthly and where chaos lives.
  Rebuilt from scratch by `env-bootstrap.sh` each drill cycle — if the lab
  can't be rebuilt in minutes, the bootstrap script is broken and that's the
  finding.
- **`prod`**: single box per org (v0.1), `python -m sentinel.receiver`.
  Cutover checklist (§1.2) gates every first-prod and every re-cutover.

### 1.2 Production cutover checklist (hard gate)

No prod cutover — first or re-cutover — without ALL of:

- [ ] Pinned SHA green: `pre-pr-gate.sh` run on the exact SHA, evidence pasted
      in the release note.
- [ ] Shadow-diff signed: candidate ran ≥7 days on `staging-shadow`; the
      disposition diff vs live was reviewed and signed by two humans (ADR-022
      canary-as-shadow-diff).
- [ ] Secondary drilled: `require_drilled_secondary` passes — configured
      secondary, last drill ≤30 days, **human ack** within 10 min
      (ADR-018/D13). The forwarder refuses to start otherwise; do not bypass.
- [ ] Wakefulness attestation on file for the secondary (a real sentence, not
      a placeholder — `secondary.py` rejects placeholders).
- [ ] External watcher armed with a **named owner** and a duty roster;
      watcher-down pages via a non-Sentinel path (dead-man's-switch).
- [ ] Disk-guard interim (D14) verified on the prod state dir; retention RFC
      (ADR-024) tracked as the real answer.
- [ ] On-call has read RB-4..RB-7 and drilled RB-4 + RB-7 in `staging-lab`
      within the last 30 days.

---

## 2. Release process — deploying code ≠ releasing features

Two independent release axes, decoupled by construction
(principal-governance: decoupled deployment):

- **Code deploy** = new artifact (pinned git SHA) on the box. Changes what
  *can* run.
- **Feature release** = flag/config change (thresholds, allowlist, feature
  flags). Changes what *does* run.

You can always roll back a release without touching the code, and you can
always roll back the code without touching the release state. That is the
entire point.

### 2.1 Feature flags — the contract (`flags.json`)

**Type:** the flags schema is **Type 1** (contract; versioned). Individual flag
*values* are Type 2 (reversible by design).

Flags live in the config dir as `flags.json`, versioned (`"version": 1`),
validated by the same 4-stage load as thresholds (parse → schema → semantic →
atomic swap; invalid → live generation untouched). `flagctl`
(`scripts/ops/flagctl.py`) is the only sanctioned writer: atomic tmp+rename
writes, keeps the last 5 flag generations (mirroring the last-good chain), and
appends every change to `flags-changes.jsonl` (who, when, why).

v0.1 flag surface (minimal — every flag must earn its existence):

| Flag | Type | Default | Effect when flipped |
|---|---|---|---|
| `global_kill_switch` | bool | `false` | `true` → the gate returns `passthrough` for every alert. Suppression is off *now*. This is the instant flag-off. |
| `suppress_enabled` | bool | `true` | `false` → suppress dispositions become `passthrough` (never silently downgraded to a queue). Targeted release of the suppression feature. |
| `shadow_mode` | bool | `false` | `true` → `Gate(shadow=True)` semantics: log the would-be disposition, always return `passthrough`. Mirrors `SENTINEL_SHADOW`. |
| `canary_severity_bands` | list | `[]` | Non-empty → the candidate policy applies only to these inbound severity bands (e.g. `["warning","info"]` first); everything else takes the current policy. |
| `canary_services` | list | `[]` | Non-empty → candidate policy applies only to these services. Canary by blast radius. |

**Receiver integration status (honest):** `ConfigLoader` on `origin/main`
validates `thresholds.json`/`allowlist.json`; `flags.json` is not yet loaded
into the live gate. Until the build lane wires it (**flagged request
L3-FLAGS-1**, contract-first per principal-governance operating rule 1),
`flagctl` governs flag *intent* on disk and the operator applies the semantics
through the existing knobs (`SENTINEL_SHADOW`, config reload, allowlist
edits). The schema, the flip procedure, and the kill-switch asymmetry below
are settled now so the wiring is mechanical later.

### 2.2 How a flag flips (the procedure)

1. Operator: `scripts/ops/flagctl.py set <flag> <value> --by <name>
   --reason "<ticket/incident>"` on the prod config dir.
   `flagctl` validates the schema, writes the new generation atomically, keeps
   the previous 5, and logs the change. Exit non-zero = nothing was written.
2. Operator triggers reload: `POST /-/reload` (bearer `SENTINEL_HEALTH_TOKEN`)
   or `SIGHUP` to the receiver. Invalid generation → `config_rejected`, live
   generation untouched — the flip is safe to attempt.
3. Operator confirms: `/healthz` shows the new `config_generation`; the change
   appears in `flags-changes.jsonl`. If not confirmed within 60s, the flip is
   treated as failed and the previous generation is restored.

**Who can flip:** the on-call and the designated releaser. Asymmetry (safety):
flipping `global_kill_switch` **ON** (toward fail-open) needs one human; flipping
it **OFF** again needs two named humans (`--two-person "a,b"`) plus an incident
or release note. `suppress_enabled` changes follow the ADR-022 two-person
threshold-change governance. `flagctl` enforces the asymmetry; it cannot be
bypassed from the CLI.

### 2.3 Dark launch and canary (execution-doctrine pipeline)

`validate → shadow → canary`, mapped onto Sentinel's own machinery:

1. **Validate:** the change proves itself locally (`pre-pr-gate.sh` green) and
   in `staging-lab` against fault-injection.
2. **Shadow:** the candidate runs on `staging-shadow` for ≥7 days. For
   *policy* changes (thresholds/flags), ADR-022's canary-as-shadow-diff: run
   the proposed policy in shadow, diff dispositions against live, sign the
   diff. Cheaper than live splitting, same evidence, zero paging risk.
3. **Canary:** only after a signed shadow-diff. Canary by severity band
   (`canary_severity_bands`: warning/info first — a wrong decision pages
   nothing that wasn't already paging) or by service (`canary_services`:
   lowest-blast-radius service first). The canary cohort's dispositions are
   watched on the live audit log; any unexplained false-suppress suspicion
   → RB-2 (SEV1 drill) and the kill switch goes ON first, questions second.

**No emergency code rollbacks.** The release rollback path is flags and config
generations, measured in seconds (§2.4). Code rollback exists (§2.4) but is
the *second* resort, not the first — principal-governance operating rule 2.

### 2.4 Rollback — measured in seconds

| Layer | Mechanism | Time | Command |
|---|---|---|---|
| Release (flags/config) | `flagctl.py rollback` → previous validated generation → `POST /-/reload` | <5s | `flagctl.py rollback --by <name> --reason <r>` then reload |
| Instant flag-off | `global_kill_switch=true` → everything passthrough | <5s | `flagctl.py set global_kill_switch true --by <name> --reason <incident>` then reload |
| Code | `scripts/ops/rollback.sh` → symlink swap to previous artifact dir → restart → `/healthz` gate | <60s | `rollback.sh` (verifies `/healthz ok:true` or refuses to declare success) |

The ConfigLoader's last-good chain (5 checksummed generations) is the
backstop under all three: a bad write can never strand the process on an
unvalidated policy — it serves last-good and emits `config_rejected`.

### 2.5 Drills (a standby with an undrilled runbook doesn't exist)

Monthly, gated (not calendar — a drill that doesn't happen blocks the next
release until it does), each <5 minutes one-tap where possible, human ack
required where it matters:

- **Kill-switch drill:** flip `global_kill_switch` ON in `staging-lab`,
  confirm a synthetic alert round-trips as `passthrough`, flip OFF with the
  two-person ceremony. Records who ran it.
- **Secondary-channel drill:** per ADR-018/D13 — delivery + **human ack
  within 10 minutes**; stale (>30d) drills refuse cutover.
- **Disk-guard drill:** fill `staging-lab` state dir to the watermark, confirm
  the guard pages + spills, confirm replay on restart.
- **Reload-rejection drill:** push an invalid `flags.json`, confirm the live
  generation is untouched and `config_rejected` fires.

Drill completions are event-logged (ADR-018 condition) and live in
`ops/drills/` next to the existing fault-injection records.

---

## 3. CI discipline — local-first

**Repo policy (binding): no GitHub Actions workflow files on `main`.**
There is no hosted CI to hide behind. The local gate script IS the CI, and it
runs the same commands on a laptop, in a lane worktree, and on the release
box. `local == CI == prod artifact`: stdlib-only, no build step, the release
is a pinned git SHA.

### 3.1 The gate (`scripts/ops/pre-pr-gate.sh`)

Runs in this order — each stage must pass before the next starts:

1. **Secrets grep** (`secrets-grep.sh`) — fast fail. A secret-shaped value in
   the diff fails the gate before we spend 108s on tests.
2. **Full test suite** — `python3 -m unittest discover tests`. Baseline at
   time of writing: **496 tests, OK (~108s)**. The gate records the count;
   a drop in test count without a logged reason fails the gate (tests don't
   silently disappear).
3. **Kill-the-client invariant** — the `test_gate.py` fail-open assertion is
   part of the suite, but the gate names it explicitly in the report: this
   test failing is a release blocker, full stop.
4. **Boot smoke** (`receiver-smoke.sh`) — the receiver boots from a fresh
   `env-bootstrap.sh` tree in mock mode, serves `/livez`, and round-trips a
   synthetic PD trigger. Catches "tests pass but the process doesn't start"
   (config schema drift, import cycles).
5. **Config/flag schema validation** — `flagctl.py validate` on the repo's
   example configs, so a schema change that breaks the loader is caught here.

### 3.2 What runs where

| Where | What | Evidence |
|---|---|---|
| Dev laptop / lane worktree | `pre-pr-gate.sh` before every PR | pasted into the PR body (tests evidence per the PR template) |
| PR review | different-agent review re-runs or spot-checks the gate; red = no merge | review checklist |
| Merge to `main` | coordinator re-runs the gate on the pinned SHA before squash | release note |
| Release box | `pre-pr-gate.sh` (fast subset: secrets + smoke) before every code deploy | deploy log |

Nightly/recorded but NOT gate-blocking: the eval harness
(calibration-report), the Oracle latency campaign (B re-derivation is a
measured protocol, not a gate), the fault-injection suite in `ops/drills/`.
They inform; they don't block — except the kill-the-client test, which does.

---

## 4. IaC direction

### 4.1 With budget (stated, not built)

If funding appears, the managed equivalents: a VM provisioner (Terraform-style:
network, firewall, VM, DNS), a config manager (Ansible-style: users, env
files, systemd units, log rotation), managed Postgres (replacing SQLite),
managed Redis (replacing the in-memory correlator), and a real secret manager
(KMS envelope — the per-tenant upgrade already designed in ARCHITECTURE.md
§7). Module boundaries are drawn at exactly the seams the code already has:
receiver / correlator / gate / forwarder / audit are independently
replaceable units.

### 4.2 Free now (built — `scripts/ops/`)

| Script | Replaces |
|---|---|
| `env-bootstrap.sh` | the "fresh VM" runbook: creates config dir, state dir, example configs, checks Python ≥3.12, prints the env template. Idempotent. |
| `deploy.sh` | SSH-and-pray: fetches the pinned SHA into a new timestamped artifact dir, runs the gate, swaps the `current` symlink, restarts via the operator-provided `SENTINEL_RESTART_CMD`, health-gates on `/healthz`. |
| `rollback.sh` | the 3 AM panic: swaps `current` back to the previous artifact dir, restarts, verifies `/healthz ok:true`, refuses to declare success otherwise. |
| `disk-watermark.sh` | the external watcher's disk eye: Nagios-style exit codes on state-dir filesystem usage. |

**Immutable-artifact rule** (infrastructure constitution): never SSH-fix prod.
A fix is a commit → gate → deploy of a new artifact dir. `deploy.sh` refuses
to deploy a dirty tree or an unpinned ref. Rollback is a symlink, not a
revert commit.

---

## 5. Secrets

### 5.1 What never lives in the repo

`TYPESAFE_API_KEY`, `SENTINEL_WEBHOOK_SECRET`, `PD_ROUTING_KEY`,
`SENTINEL_HEALTH_TOKEN`, secondary-channel credentials (webhook URLs, tokens),
the GitHub PAT, customer `allowlist.json` production contents, any `.env`
file. Transport is **environment variables only**, per deployment,
single-tenant per box (v0.1). Env files on the box are `600`, owned by the
service user. `.gitignore` + `secrets-grep.sh` double-enforce; the gate fails
closed.

### 5.2 Rotation discipline

- **90-day rotation calendar** for all API keys and tokens (Jev key, PD
  routing key, health token, secondary creds). The calendar is a tracked
  item, not a memory — a missed rotation is a finding.
- **Overlap procedure:** generate the new secret → configure the *sender*
  side (customer Alertmanager / PD integration) → cut the receiver over in a
  low-traffic window → revoke the old. For `SENTINEL_WEBHOOK_SECRET`, a
  dual-secret grace window is the correct mechanism; it needs receiver
  support (**flagged request L3-FLAGS-2**). Until then, rotation happens in a
  maintenance window with sender cutover <60s, announced to the on-call.
- **Compromise response:** revoke at the vendor FIRST, then rotate, then audit
  the event log for any use of the old credential. Never the reverse order.
  A compromise is a postmortem, not a quiet rotation.
- **GitHub PAT:** the October incident (key traveled through chat) is closed
  by the vaulting; the discipline going forward: raw key never in
  files/memory/logs/chat, rotation on any exposure or every 90 days, the
  replacement handed over the same secure path. Not re-litigated here —
  encoded as the rule above.

---

## 6. Operational runbooks

RB-1..RB-3 live in `ops/RUNBOOKS.md` (push/PR/CI flow, false-suppress SEV1
drill, demo-day checklist). The incident runbooks below are the on-call's.
**Follow exactly; improve by proposal + logged decision, never by
improvisation.**

Conventions: `/livez` is unauthenticated (supervisors need it unconditionally);
`/healthz` needs `Authorization: Bearer $SENTINEL_HEALTH_TOKEN` and reports
named predicates (`config_current`, `gate_constructed`, `forwarder_draining`,
`no_crashloop_signature`). `SENTINEL_STATE_DIR` holds generations, restarts,
events, spill files.

### RB-4: Receiver down / paging path unhealthy

**Symptoms.** External watcher: `/livez` times out or connection refused; or
`/healthz` returns 503 with `failed: [...]`; or PagerDuty console shows new
alerts stopping while sources are still firing.

**Diagnosis (in order, ~2 min).**
1. `curl -m 5 http://<host>:<port>/livez` → process alive? If yes, the process
   is up and something inside is wrong — go to step 3.
2. If no: `ssh` (or the box console), `ps aux | grep '[s]entinel.receiver'`,
   check `SENTINEL_STATE_DIR/restarts` for a crashloop signature
   (`no_crashloop_signature` predicate tells you on `/healthz` when the
   process is up). Check disk: `scripts/ops/disk-watermark.sh --path
   $SENTINEL_STATE_DIR` — a full disk kills SQLite writes first.
3. `curl -H "Authorization: Bearer $SENTINEL_HEALTH_TOKEN"
   http://<host>:<port>/healthz | python3 -m json.tool` → read `failed`:
   - `config_current` → bad config generation; check `config_rejected`
     events, restore last-good via `flagctl.py rollback` + reload.
   - `gate_constructed` degraded → Jev client failing; see RB-5 (paging still
     works — fail-open).
   - `forwarder_draining` → outbox backed up; check PD reachability, spill
     files accumulating.
   - `no_crashloop_signature` → repeated restarts; read the traceback from
     the supervisor log, then roll back the artifact (`rollback.sh`).

**Actions.**
1. Restart the receiver via the supervisor (`$SENTINEL_RESTART_CMD`).
2. If it crashloops: `scripts/ops/rollback.sh` to the previous artifact dir.
3. If still down after rollback: **invoke RB-7** (secondary-channel cutover) —
   do not keep debugging a dead receiver while pages are being lost. Debugging
   continues in parallel, paging does not wait.

**Escalation.** On-call → Petu (founder-deputy) after 15 min of paging-path
down. Customer notice after 30 min.

**Fixed when.** `/healthz` returns `ok:true` AND a synthetic PD trigger posted
to `/v2/enqueue` appears in the PagerDuty console (or `stub_pd.py` in lab).
Log the incident in `ops/incidents/`.

### RB-5: Jev vendor down / degraded

**Symptoms.** `/healthz` `gate_constructed` shows `degraded` with a client
error; audit rows show `action=passthrough, reason=error:jev_*` climbing;
timer-win (`B=2700ms`) rate spikes; Jev latency `p99` in audit `latency_ms`
blows past budget.

**Diagnosis.**
1. Confirm it's the vendor, not us: `curl` the vendor status page; check
   whether errors are 429 (rate limit — honor Retry-After), 529 (overloaded),
   or timeouts. One failing region vs global matters for the ticket.
2. Measure from our own data: query recent `decisions` rows —
   `SELECT reason, COUNT(*) FROM decisions WHERE received_at > <1h ago> GROUP
   BY reason` — the passthrough/error ratio is the truth, not the vendor page.

**Actions.**
1. **Do nothing to the paging path.** Fail-open carries it: every alert pages
   as before Sentinel existed. Verify this is actually happening — sample 5
   recent `passthrough` rows and confirm the forwarder relayed them.
2. Announce internally: "suppression offline, page volume returning to
   baseline." Expect the on-call load to rise to pre-Sentinel levels; this is
   the designed degradation, not an incident.
3. **Do NOT hand-raise the race budget B.** B=2700ms is a measured safety
   budget (ADR-010); changing it by hand during an outage is exactly the
   "tune around the vendor" reflex the ADR rejected. B changes go through the
   re-derivation protocol with two-person sign-off.
4. Open the vendor ticket with: error codes, start time, our measured
   error/latency distribution, affected model pin.

**Escalation.** Vendor ticket immediately. If outage >4h, schedule the
B re-derivation review (the N≥1000 campaign is the standing plan anyway).

**Fixed when.** `gate_constructed` healthy for 30 consecutive minutes AND
suppression dispositions resume AND a 1-hour shadow-diff of the resumed
policy shows no anomaly. Log the outage window in `ops/incidents/` (it feeds
the vendor-reliability evidence for the fine-tuned-encoder exit discussion).

### RB-6: Disk-full / disk-guard firing (D14)

**Symptoms.** The D14 interim guard fires: control-plane page
(`_enqueue_control_plane_page`) + spill record written; `disk-watermark.sh`
reports CRIT; receiver may start failing SQLite writes (WAL can't grow).

**Diagnosis.**
1. `scripts/ops/disk-watermark.sh --path $SENTINEL_STATE_DIR` → usage %.
2. Size the components: `du -sh $SENTINEL_STATE_DIR/*` — the WAL-mode
   footprint is db + `-wal` + `-shm` (see `eventlog._db_footprint`).
3. Growth rate: compare against the last drill's measured bytes/decision —
   the ADR-024 arithmetic gives a disk-full date; if we're early, something
   changed (traffic spike, stuck WAL checkpoint, spill replay backlog).

**Actions.**
1. Confirm spill is working: new spill files appearing with `SPILL_PREFIX`
   means the degraded ladder is holding — evidence is landing on disk even
   now.
2. Graceful drain: stop the receiver (supervisor stop). A stopped receiver
   with PD pointed at it is connection-refused — **this is the one failure
   mode that degrades to silence**, so immediately confirm RB-7 readiness
   (drilled secondary) before stopping, or keep the receiver up in
   admission-shedding mode (503s) while you work.
3. Checkpoint the WAL (`sqlite3 <db> 'PRAGMA wal_checkpoint(TRUNCATE);'`),
   archive the event log per the interim retention rule, then prune ONLY per
   the logged interim policy — deletion is a logged event, never a silent
   prune (ADR-024 floors: ≥180d full-fidelity for suppress rows, ≥60d for all
   decisions).
4. Expand the disk (or move the state dir), restart, verify spill replay:
   every `.json` spill becomes `.replayed` and its events land in the log.
5. File the postmortem: the Vault-led retention RFC (ADR-024/O-1) is the real
   answer; this runbook is the Type-2 interim. If the RFC hasn't landed and
   we're pruning again, escalate to Petu — the interim is rotting into a
   quiet failure mode.

**Escalation.** On-call → Vault (retention owner) if pruning beyond the
interim policy is needed. Any audit gap >1h → postmortem within 24h.

**Fixed when.** Watermark clears, spill files replayed, new decisions flowing,
and the disk-full date recomputed and logged.

### RB-7: Secondary-channel cutover (D13 / D18)

**Preconditions (the cutover gate — non-negotiable).**
- `require_drilled_secondary` passes: secondary configured, last drill ≤30
  days, **human ack within 10 min** on that drill. The forwarder refuses to
  start without it; do not bypass the refusal.
- Wakefulness attestation on file (the on-call's own words about what the
  channel does and does NOT guarantee).
- Fate-domain separation holds: the secondary is SMS/voice/a different
  vendor — not a second API key on PagerDuty (ADR-012 condition).

**Trigger.** Primary PD path dead: connection-refused or 5xx from
`PD_EVENTS_URL` for >5 min, or the PD status page confirms an incident, or
RB-4 reached step 3.

**Actions.**
1. Declare in the room: `CUTOVER: secondary channel live — <reason>`.
   Timestamp it; the postmortem starts here.
2. The forwarder fires the secondary automatically for outbox rows undelivered
   past `SECONDARY_FIRE_AFTER_S` (15 min). For pages already in flight that
   can't wait 15 min, trigger the secondary explicitly per the channel's
   documented procedure (the drill taught you this — if it didn't, the drill
   was theater; note it in the postmortem).
3. **Primary retries CONTINUE.** `secondary_fired_at` is a timestamp, not a
   terminal status — never stop primary retries when the secondary fires
   (design §8 pre-mortem: that line caused the SEV1 miss).
4. Confirm delivery on the secondary channel with a **human ack** — a
   delivery receipt is not an ack.
5. When PD recovers: confirm the outbox drains (watch
   `forwarder_outbox_lag_s` on `/healthz` → 0), then stand the secondary down
   and declare `CUTOVER COMPLETE` with timestamps.

**Escalation.** On-call → Petu immediately on any cutover. Customer notice
within 30 min: "pages delivered via backup channel."

**Fixed when.** Outbox drained, PD console shows the queued pages, secondary
stood down, and the postmortem (within 24h) records: trigger, timeline,
which pages went via secondary, drill freshness at cutover time.

---

## 7. Done-checklist (principal-systems)

- **10× scale without rewrite?** The ops story assumes single-box v0.1.
  Scaling past one box changes the correlator (Redis), the audit (Postgres),
  and the deploy (multi-host) — the module seams are drawn for exactly that
  swap (ARCHITECTURE.md §8), but this doc does not pretend to cover it.
- **Jev down 4 hours?** RB-5: fail-open carries paging; suppression offline is
  the announced, designed degradation.
- **Junior deploys a stale config?** The 4-stage loader rejects it; live
  generation untouched; `config_rejected` fires. The gate's schema-validation
  stage catches it before merge.
- **Local vs global?** The global optimization here is *not* a fancier
  deploy pipeline — it's the shadow-first evidence chain (ADR-022) and the
  flag-off rollback measured in seconds. A Kubernetes cluster for a
  single-box stdlib process would be resume-driven local optimization.
- **If L3 leaves tomorrow?** This doc + `scripts/ops/` + `ops/RUNBOOKS.md`
  + the code's own docstrings are the runbook. The drill records in
  `ops/drills/` prove the runbooks were exercised, not just written.

## 8. Alternatives considered (and rejected)

- **GitHub Actions for CI:** rejected — repo policy forbids workflow files on
  `main`, and the policy is right for v0.1: a hosted runner adds a network
  dependency and a secrets surface for zero benefit when the suite is 108s
  local and hermetic (stdlib-only). Revisit if the suite passes 10 minutes or
  needs a matrix.
- **Full blue-green with load balancer:** rejected for v0.1 — single box,
  and the flag-off rollback is faster (<5s) than any traffic shift. The
  artifact-dir + symlink swap in `deploy.sh` is the honest middle.
- **Kubernetes / containers:** rejected — ₹0 budget, single static binary
  tree, no build step. Containerizing `python3 -m sentinel.receiver` buys
  nothing until multi-host or dependency pinning matters.
- **Live 5% traffic canary for policy changes:** rejected in favor of
  ADR-022's canary-as-shadow-diff — same evidence, zero paging risk, and the
  panel already decided it.

## 9. Flagged requests to other lanes

- **L3-FLAGS-1 (build coordinator):** wire `flags.json` into `ConfigLoader`
  (schema in §2.1; the kill-switch semantic is `passthrough`-for-all).
  Contract-first: the schema and flip procedure above are settled.
- **L3-FLAGS-2 (build coordinator):** dual-secret grace window for
  `SENTINEL_WEBHOOK_SECRET` rotation (§5.2).
- **L3-FLAGS-3 (build coordinator):** `staging-shadow` tier refuses a
  production `PD_ROUTING_KEY` at startup (the never-pages rule, enforced).
- **L3-OPS-1 (whoever owns the room roster):** name the external-watcher
  owner and the duty roster before the first prod cutover (§1.2) — without
  this, RB-4's first symptom never fires.

---

*Delivered: `ops/devops-foundation.md` + `scripts/ops/` (bootstrap, gate,
secrets-grep, disk-watermark, flagctl, receiver-smoke, deploy, rollback).
Every script below was executed against `origin/main @ 605793f` in this lane's
worktree before commit.*
