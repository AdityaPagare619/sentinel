# Sentinel — Decision Log

Format: date — **decision** (rationale, who called it). Written at decision time.
Latest decisions on top.

## 2026-10-03 — D11: ADR-005 webhook auth fail-closed (Sunday wave, P0)

- 2026-10-03 ~21:00 IST — **D11 implemented (ADR-005 ADOPT-WITH-CONDITIONS, branch
  `lane/sun-d11-sigfix`). Type: 1** — receiver auth is a trust-boundary contract;
  the old fail-open (`if not sig: return True`) was a silent trust violation, not
  a tuning knob. (a) `_signature_ok` is fail-closed in production: empty secret,
  absent/malformed signature, absent/malformed/stale/future-skewed timestamp,
  bad MAC, and legacy timestamp-less signatures all → 403 `bad signature`
  (the receiver's existing error convention). (b) Empty secret refuses startup
  (SystemExit) unless `SENTINEL_WEBHOOK_ONBOARDING=1`. (c) Onboarding mode is an
  explicit env flag that MAY fail open, but ONLY with a CRITICAL boot-time
  stderr warning, a per-request WARNING, the `webhook_auth_bypassed` metric,
  and `webhook_auth_fail_open: true` surfaced on /healthz. (d) Canonical scheme
  is now timestamped: `X-Sentinel-Timestamp` + `X-Sentinel-Signature:
  sha256=<hex>` over `<ts>.<raw-body>`, |now−ts| ≤ 300s. (e) ARCHITECTURE.md §7
  "optional IP allowlist config" mention struck (panel condition (d); also §3.9
  updated to the fail-closed contract — same frozen-spec edit). **Replay
  analysis** (panel condition (c)): a timestamp-less HMAC over the raw body has
  an INDEFINITE replay window — any captured valid signature replays forever;
  idempotent ingest dedupes identical alerts but cannot distinguish a replay
  from a genuine resend. The 300s bound shrinks the window to 5 minutes;
  Tripwire's caveat stands (5 min without nonces is a 5-minute replay window —
  nonces are a named follow-up, not this lane). Reference:
  `docs/adr-decisions-2026-10-03.md` ADR-005 + D11; design evidence in
  `docs/SECURITY.md` §2.1–§2.4. (Sunday Wave Coordinator; Petu P0 —
  exception to the 20:28 no-crunch order, kept P0 by name.)

## 2026-10-03 — Sunday wave: D14 interim disk guard lands

- 2026-10-03 ~20:30 IST — **D14 interim Type-2 disk guard implemented** (lane/sun-d14-diskguard, PR
  against main). ADR-024/O-1 ruling: the Vault-led retention RFC is the real answer, but the disk
  fills on its own schedule — the interim guard lands before the design partner. **Type 2**
  (reversible, simple, loud — per principal-systems): a WAL-size watermark on the event-log
  database (db + -wal + -shm footprint) checked post-commit in `EventLog._commit()`; when crossed,
  exactly-once per process it (1) pages the operator via the priority-1 control-plane outbox lane
  (the commit-watchdog convention — one pager, prioritized), and (2) writes a durable record via
  spill.py's existing `write_spill` into a dedicated `disk-guard/` subdir (kept apart from
  forward-replay spills so `replay_spills` never misreads it as a PD send), plus an unsuppressible
  stderr line. The guard observes post-commit — triage is already durable — so it can never block,
  delay, or sink a page; proven by test (page decision completes with intact triage artifacts in
  <<2s while the guard fires). Conservative 100 MiB default (`SENTINEL_DISKGUARD_BYTES` env
  override; bad env falls back to default, never refuses start). Alternatives rejected: new event
  type (Type-1 vocabulary frozen by ADR-011), spill into the main spill dir (replay misread),
  background thread (synchronous keeps "page sent before process can die" + trivially testable).
  Reversible: delete the env/constant and it is gone; no schema or vocabulary changes.
  (Fix agent; principal-systems + principal-mindset skills applied.)

## 2026-10-03 — ADR adjudication panel (Forge · Vault · Pager · Tripwire)

- 2026-10-03 ~20:50 IST — **ADR adjudication panel verdicts (Aditya's 20:08 order: decided tonight by
  group discussion; Petu ratifies by PR review).** Full record: `docs/adr-decisions-2026-10-03.md`.
  Ratified as implemented: ADR-010/011/012/013 (race B=2700, event log, durable forwarder, quantized
  gate); conditionally ratified: 014 (wire precondition into live gate), 016 (structural aggregate
  bypass + regression), 018 (first drilled ack + named watcher owner before design partner).
  Adopted-as-design, explicitly NOT implemented: 015, 017, 019, 020, 021, 023; partial: 022
  (floors + two-person ack real; canary/watchdog/auto-revert required pre-cutover).
  ADR-001 ADOPTED with conditions (flap-reopen bumps flap count not severity; P1/P2 carve out of
  change windows — reconciles DR-13; Sentinel-side windows only). ADR-005 ADOPTED: HMAC hardening
  required; **IP allowlist REJECTED entirely** (Forge+Vault beat Petu's opt-in prior — opt-in is
  theater-or-footgun; strike ARCHITECTURE.md §7 mention); code must be fixed to refuse on empty
  secret + add 5-min timestamp tolerance (current fail-open contradicts DR-27). ADR-007 ADOPTED:
  mute is a platform-tier rendering label (engine enum stays 4-valued), event-logged transitions
  with reason/attestor/TTL, no auto-mute ever, weekly mute review with named owner. ADR-024/O-1:
  RFC REQUIRED before first design partner, Vault-led, with Type-2 interim disk guard first;
  status OPEN by evidence not vote. **Prior lost:** "ratify 010–024 as implemented" rejected 4–0
  with code evidence — a fictional register is what Tripwire exists to prevent. Implementation debt
  D1–D14 registered in the decision doc; Sunday wave coordinator owns scheduling. (Panel; Petu
  ratifies.)

## 2026-10-03 — Saturday wave (Tripwire)

- 2026-10-03 — **Sunday go/no-go checklist owned by Tripwire.**
  `ops/acceptance-sunday.md`: the 10 exit bars as executable rows (bar, exact
  verification step, command/click-path, pass criterion, owner); Phase A
  non-negotiables (bars 10/7/8/9) run first with automatic NO-GO on any red;
  two-key signatures per row (builder "built as specified" + Tripwire "verified
  broken-safe"). (Saturday Wave Coordinator brief; Tripwire.)
- 2026-10-03 — **The live-kill ritual.** Go/no-go requires re-running
  kill-the-client + platform-tier-death LIVE at the 20:30 table (demonstrated,
  not asserted), a named adversarial chair who must concede on the record, and
  the flip-ledger recital (Law 3: flips audited, never hidden).
  (`ops/fault-injection-plan.md`; Tripwire.)
- 2026-10-03 — **CI `startup_failure` root cause: dead workflow record, not the
  file.** GitHub registered the workflow as `BuildFailed` on first ingestion
  (2026-10-02 18:41:07, transient, repo-creation race); all 16 runs bound to
  the dead record 373379524; the file bytes were always valid (active record
  373379827 since 18:41:30). No file fix needed — a fresh run is the
  verification; escalation = forced re-ingest. (`ops/ci-repair.md`; Tripwire.)
- 2026-10-03 — **CI fix, second attempt: workflow renamed to
  `sentinel-ci.yml`.** The "transient" hypothesis was FALSIFIED — fresh runs at
  18:59 UTC still bound to the dead record, so the (repo, path) → record
  mapping is permanently poisoned and re-pushing the same path can never fix
  it. `git mv .github/workflows/ci.yml .github/workflows/sentinel-ci.yml`
  (zero content change); cross-lane path references in METHODOLOGY.md §3 and
  LOCAL-OPS.md §4 updated and flagged. Tripwire owns `ci.yml` test gates
  (TEAMS.md), so the rename is in-lane. (`ops/ci-repair.md`; Tripwire.)

## 2026-10-02 — base setup

- 2026-10-02 — **Repo is the source of truth, the room is the workbench.**
  `AdityaPagare619/sentinel` (private → public at Preview-1) holds code + docs;
  agents operate in `~/workspace/jev-builds/sentinel/`; the two mirror each other.
  (Base setup, Aditya's order.)
- 2026-10-02 — **Base-setup file ownership.** Base setup creates ONLY: CHARTER.md,
  METHODOLOGY.md, TEAMS.md, ROADMAP.md, LOCAL-OPS.md, `ops/decision_log.md`,
  `ops/constraint_registry.md`, `.github/workflows/ci.yml`,
  `.github/pull_request_template.md`, `.gitignore`. The build coordinator owns
  `src/`, `tests/`, `PROGRESS.md`, `ARCHITECTURE.md`, `_shims.py` — single-owner
  rule while helpers are mid-flight. (Base setup, Aditya's order.)
- 2026-10-02 — **`main` protected; PR-only; no self-merge.** Review must come from a
  DIFFERENT agent; green CI required; squash merges; `main` stays green — red main is
  stop-the-line. (METHODOLOGY.md §1.)
- 2026-10-02 — **False-suppress incident rule.** Any unexplained false suppress on a
  SEV1-class alert is treated as a SEV1: blameless postmortem, report derived from
  the audit log. (CHARTER.md design laws.)
- 2026-10-02 — **Preview-1 target: EOD Oct 4, 2026 IST.** Working demo (synthetic
  storm → triage → audit log → calibration report), not slides. Scope = M3–M6.
  Exit bars: ≥60% suppression, ZERO false-suppress on synthetic SEV1s, p95 <1s on
  mock, every decision audited. (ROADMAP.md; Aditya's order.)

## Seeded from PROGRESS.md (build coordinator's decisions, recorded verbatim)

- 2026-10-02 — **Python stdlib only (no pip deps).** `http.server` for the receiver,
  `urllib` for the Jev client (custom User-Agent per the documented 403 quirk),
  `unittest` for tests. Rationale: ₹0 ops, runs anywhere, zero supply-chain surface;
  FastAPI/gunicorn swap documented as the production upgrade path.
- 2026-10-02 — **v0.1 is Jev-only (no LLM adjudicator).** Per the architecture doc —
  hybrid deferred to a measured premium tier.
- 2026-10-02 — **SQLite for the v0.1 audit store** (single-file, zero-ops); schema
  designed Postgres-compatible for the SaaS upgrade.
- 2026-10-02 — **Design frozen for v0.1** (`ARCHITECTURE.md`): receiver → correlator
  → gate → forwarder + audit; tuner CLI; eval harness; System-One client + mock.
  Dashboard UI, label joiner automation, SSO, self-hosted relay, LLM adjudicator,
  fine-tuned-encoder exit all deferred (designed, not built).
- 2026-10-02 — **Fail-open architecture.** Every exception path → pass-through;
  unparseable receiver input is forwarded unchanged, never dropped; kill-the-client
  test is a release blocker.
- 2026-10-02 — **Suppress triple lock.** P(p1_critical) < 0.002 AND Q3 confidence
  ≥ 0.90 AND fingerprint ∈ customer-verified allowlist — from expected-cost math
  with C_FP=$100, C_FN=$50,000 (founder-grade estimates; tuner + shadow pilots
  replace them with measurements).
- 2026-10-02 — **Shadow-first deployment.** Weeks 1–2 of any deployment run
  shadow-only (log dispositions, change nothing); the weekly shadow report is the
  sales collateral.
- 2026-10-02 — **Honest numbers only.** No accuracy claims in marketing or output;
  only calibration curves on the customer's own labels. Synthetic eval proves
  plumbing, never production accuracy.

## 2026-10-02 — evening: the platform pivot (Aditya's correction)

- 2026-10-02 ~18:45 — **Aditya's verdict: engine before platform was the wrong order;
  roster before company was the wrong depth.** The v0.1 middleware is a strong engine
  (fail-open, triple-lock, audit-first, 140/140), but the team built it before freezing
  the PLATFORM architecture, and TEAMS.md was a roster, not an operating company.
  Correction accepted in full. Tonight's work: per-chief operating files
  (`chiefs/*.md`), the frozen `PLATFORM_ARCHITECTURE.md`, and `WAVE_PLAN.md` — no code
  until the direction is frozen. (Aditya's order; this decision.)
- 2026-10-02 ~18:45 — **Sunday 9 PM goal: the entire interactive platform, working.**
  Not a wrapper, not slides — a design partner can click through it and run it:
  live decision river, calibration dashboards, threshold simulator, audit explorer,
  noise analytics, 15-minute onboarding. Error rate extremely low; Jev's speed must
  not be degraded by our architecture; the software must be *interactive* and
  feature-rich *besides* Jev. (Aditya's order; scope boundary in
  PLATFORM_ARCHITECTURE.md §8.)
- 2026-10-02 ~18:45 — **Do-no-harm laws adopted as non-negotiable** (from Aditya's
  "things that shouldn't happen"): (i) the platform never adds latency to the paging
  path beyond the Jev call itself; (ii) fail-open always, uncertainty pages;
  (iii) zero *harmful* errors as the target — triple-lock suppress, flips audited
  never hidden (Jev's 1.3–2.2% flip floor is irreducible and is said plainly);
  (iv) honest scope — Sunday is a clickable working platform, NOT multi-tenancy,
  billing, SSO, or production PagerDuty integration. (PLATFORM_ARCHITECTURE.md §4.)
- 2026-10-02 ~18:45 — **Platform/hot-path separation is architectural, not aspirational.**
  The dashboard tier runs as a SEPARATE process, reads ONLY from the audit log
  (SQLite WAL, read-only connection), and never imports hot-path modules in a way
  that couples deploys. UI traffic can never contend with the audit writer or slow
  a page. (PLATFORM_ARCHITECTURE.md §3; Forge enforces.)
- 2026-10-02 ~13:28 — **Real Jev key connected + verified.** Aditya submitted via
  secure card; connector `custom.typesafe` live. Petu's smoke test: HTTP 200,
  `answers.disposition` = choice "page", confidence 0.9, probabilities
  {page: 0.94, cannot_determine: 0.05, suppress: 0.01} — shape matches the client.
  **Open measurement question:** first-call latency was **~11.4s** vs the 70–500ms
  spec. Could be cold start; Oracle owns the latency measurement campaign
  (p50/p95/p99, cold vs warm) before anyone quotes latency numbers. Until measured,
  the timeout policy (tight timeout, fail open fast) is what protects the paging
  path — not hope. (Petu; skill at `~/workspace/skills/typesafe/`.)
- 2026-10-02 ~13:40 — **Vercel connector available; deploy only when needed.**
  v0.1 is a stdlib middleware — nothing to deploy yet. Vercel is the target for the
  dashboard/marketing surface when it exists. No premature deploys. (Aditya's order.)
- 2026-10-02 ~13:40 — **Reporting = PETU-LABS rhythm.** Detailed morning brief
  09:30 IST, evening debrief at end of working hours, no ad-hoc daytime dumps.
  Working hours 10:00–19:00 IST at full intensity. (Aditya's order.)
- 2026-10-02 ~13:40 — **The bar: harder and broader than the other teams.**
  Sentinel teams out-work the TFB and studio teams — deeper research, more code,
  wider scope. Written as the standard, measured in the briefs. (Aditya's order.)
- 2026-10-02 ~18:45 — **15 minutes of deep reading before direction.** Aditya gave
  Petu 15 minutes to go through everything in depth and come back with the right
  path — including correcting Aditya where he's wrong. The architect's verdict
  (delivered to Petu): (1) the platform-before-engine correction is right and is
  now executed; (2) "zero errors" needs the honest form — zero *unexplained* errors;
  the flip floor is irreducible; (3) Sunday 9 PM is achievable for a clickable
  design-partner platform, NOT for production (the scope boundary protects the goal);
  (4) the 11.4s real-key latency vs "sub-second inline" is the biggest technical
  risk to the thesis — handled by timeout policy + honest measurement, not assumed
  away. (This decision.)
- 2026-10-03 ~13:42 IST — **GitHub is PRs + code maintenance only; local test suite is the gate.**
  Aditya's standing order: GitHub Actions CI is a noise source and goes — the
  `.github/workflows/` directory is removed. ALL real work happens locally on
  our CPUs. PRs gate on LOCAL `python3 -m unittest discover tests` green +
  different-agent review. Never wait on a CI run again. METHODOLOGY.md §1
  updated ("Green CI required" → "Local test suite green required"). The
  ghost-record platform bug (ops/ci-repair.md) is now moot for the gate but
  retained as history. (Aditya's order.)
- 2026-10-04 ~19:45 IST — **RATIFIED (with process correction): `cryptography` as the single named
  exception to the stdlib-only frozen decision.** What happened: PR #65 (attestor identity, merged
  in the 72h wave) imports `cryptography` for Ed25519 at module level via
  gate.py → corroboration.py → attestor.py. The PR body noted the dependency in a parenthetical
  ("add to requirements when the wave assembles deps") and merged without an ADR or a decision
  to amend the frozen 2026-10-02 stdlib-only rule. Consequence found by Petu: in any clean
  environment the ENTIRE engine is unimportable (`import sentinel.gate` → ModuleNotFoundError),
  no requirements.txt existed, and the local-suite gate was silently environment-dependent.
  Ruling (Petu, founder-deputy): the NEED is legitimate — the attestor-identity ADR (panel
  cross-item note 2) requires public-key attestation; stdlib has no Ed25519; pure-Python
  Ed25519 is worse than the dependency; `cryptography` is the maintained, audited standard.
  Reverting would gut a load-bearing trust component. But the PROCESS was wrong: a frozen
  decision was bypassed without deliberation. Correction applied: (1) this entry ratifies the
  exception explicitly, with rationale recorded; (2) `requirements.txt` now pins
  `cryptography==44.0.3`; (3) stdlib-only remains the default — any further dependency needs
  its own decision entry, not a parenthetical. What would reverse it: a stdlib Ed25519
  implementation, or a decision that attestor identity no longer needs public-key crypto.
  (Petu's ruling; wave coordinator's merge process flagged for the retro.)
