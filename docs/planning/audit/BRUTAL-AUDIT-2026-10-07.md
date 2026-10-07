# BRUTAL AUDIT — Sentinel Whole-Platform Verdict
**Date:** 2026-10-07 · **Order:** Aditya — "FINAL TAKE EXTREME BRUTAL AUDITS," 2–3h, audits + judgments ONLY
**Scope:** entire platform — engine, pipeline, console (all 4 surfaces), backend deploy, infra, security, Jev integration, simulation, docs — code AND running software
**Ground truth:** repo `~/workspace/jev-builds/sentinel` @ `main 09236e4`; live: prod console `https://adityapagare619.github.io/sentinel/` (backend `https://sentinel-platform-adityapagare619s-projects.vercel.app`), `/staging/`, `/loadtest/`, `/preview-v2/`
**Method:** 8 department chief auditors + UI/UX reinforcement delta + 2 Petu-finding follow-ups (UI/UX adjudication, engine kill-switch ruling), all under the 4 principal skills. Every department: code evidence + live-behavior evidence + real-world anchor → verdict. JUDGE ONLY — zero code changed.
**Coordinator:** this report synthesizes 12 worker verdicts. Disagreements between chiefs are called out, not smoothed over.

---

## WHOLE-PLATFORM VERDICT

**Sentinel is a genuinely well-engineered core wrapped in deployment claims it cannot cash.** The deterministic engine (race, gate kernel, kill-path isolation, cost caps, key hygiene) is real engineering, verified against code, tests, and live probes. But the two properties that make it a *page-or-suppress proof engine* are broken at the wiring level, and the production surfaces assert states the backend falsifies. **Do not call this production-ready. Do not flip production live until the P0 list is empty.**

**Forced confidence: 84/100** — every P0 below is artifact-verified (file:line, live bytes, or git topology), not inferred.

### What is genuinely production-grade
- **Auth perimeter** (security, live-verified): C1 bearer on every `/api/*`, original KEYS P0 confirmed closed on the deployed backend, CORS restricted, constant-time compare, 401-before-404 (no path oracle). Anchored to RFC 6750 / PortSwigger practice.
- **The race** (engine + architecture): real threading, measured timer-wins vs judge-wins, monotonic clock, three-party backstop. Google-SRE deadline pattern, correctly applied.
- **Determinism boundary** (architecture): Jev advises only — suppress requires the full conjunction (triple lock + freshness + D8 + D5); kill path is stdlib-only with an AST test that fails the suite if a Jev import ever appears. Control principle enforced by test.
- **Cost caps** (Jev): hard caps in code, latched, fail-closed at zero budget — stronger than the industry alert-default posture (FinOps hard-limit side).
- **Key hygiene in code** (security + Jev): env-only, never logged, error sanitization, write-only BYOK API. Git history clean.
- **Sim labeling discipline** (sim + docs): in-band SIMULATED banners, seeded reproducibility verified bit-for-bit (FoundationDB-DST-grade), load-test caveats survived retelling to the dashboard — rare.
- **Calibration honesty** (Jev): 109-sample latency table verifies elementwise against raw campaign data; honest boundary stated (timing faithful, judgment semantics not).

### What is honestly-labeled-but-incomplete
- Ops Health API: `not_implemented` / `not_instrumented` rendered literally, never faked — but the console drawer that should show it is empty after auth (P1, root cause narrowed to all-or-nothing sync).
- Kill-switch per-instance state: visible via `instance_id` + `kill_state_scope`, honestly documented — but per-instance safety state is still not a safety mechanism (P0 to externalize or relabel).
- FaithfulJev: the strongest simulation component built — stranded on an unmerged branch.
- Judge-down mode: honestly reported by the backend (`not_instrumented`) — invisible in prod chrome by hardcoded omission.

### What is theater
- **"Every action is real" / "paging is ARMED"** on a backend with no Jev key, FakePD forwarder, and no paging path on the tier at all.
- **The kill switch on the deployed tier**: a flag nothing reads, with copy describing the opposite action and a toast fabricating backend confirmation.
- **"Undoable for 60 seconds"**: no undo implementation exists.
- **"Fail-open proven under pressure"**: proven on lane code; main ships the vulnerable code.
- **Green-healthy FakePD**: a health indicator that cannot display anything but healthy (the AWS green-tick anti-pattern).
- **Dead v1 calibration assets** on the public branch: ECE diagrams and `P(p1)=` notation contradicting the ordinality law.
- **Banner contract / design tokens / honesty invariants**: asserted by substring, defined-but-never-referenced, prose-not-gates.
- **The 2.0ms drill headline**: real measurement, wrong topology, overstated precision.

---

## RANKED MUST-FIX LIST

### P0 — ship-blockers (production-live gates)
1. **Kill switch does not halt the paging path.** `receiver.py:1255` wires the legacy sync `Forwarder`; kill checks exist only in `DurableForwarder` (zero kill mentions in receiver/gate/correlator). The 2.0ms drill measured a topology the production receiver doesn't use. → Wire kill checks into the receiver path (or migrate to `DurableForwarder`); re-drill against the *receiver* topology. (arch P0-1, engine ruling)
2. **Deployed kill is a no-op lever + copy inversion.** Engine ruling (c): on the Vercel tier the flag flips and nothing reads it; UI copy ("stop all suppression") describes the opposite of C3 semantics (halt forwarder); toast fabricates "backend confirmed suppression paused." → Owner-level semantics decision FIRST (fail-open vs fail-closed — both cannot be true), then wire, then copy. Interim: disabled button with honest copy. **Aditya must be told before hand-testing: his kill→re-arm sequence will 200 while provably nothing changes.** (engine ruling, UI P0-2)
3. **Fail-open ladder race on main.** `failopen.py:244` unsynchronized deque under the threaded receiver; the validated fix `3fac416` was never merged. "Fail-open proven" is true of the branch, not main. → Merge `3fac416` + add the N-thread hammer test the suite never had. (engine P0)
4. **`SENTINEL_SIM=1` defaults to real PagerDuty.** `receiver.py` build-pipeline-from-env wires `PD_EVENTS_URL` default `https://events.pagerduty.com/v2/enqueue` + `PD_ROUTING_KEY` with opt-in-only FakePD protection — the default pages humans. Comments/docs claim "PagerDuty stays FakePD in sim" — false. → Hard-force FakePD (loopback + SIM-FAKE key + assert) or refuse boot with real endpoint/key. (sim P0)
5. **Live-format operator token in plaintext on disk.** `platform/server/sentinel-state/operator_token.json` (created by a local dev run during this audit wave; gitignored, 0600 — still a live-format credential in a file). → Delete/rotate; default `SENTINEL_STATE_DIR` outside the repo tree. (security P0)
6. **Dead v1 calibration assets on the public gh-pages branch.** `assets/lib.js` (`gloss80`, `stripCal`, `calVerdict`, `receiptLine` with `P(p1)=`), `views-cal.js` (ECE headline, reliability diagram, "the diagonal is perfect honesty") — the exact banned presentation, publicly fetchable, contradicting AC-8c. → `git rm` + republish. (Jev P0)
7. **Banner accessible-name lie.** Served `/index.html:266`: `aria-label="Simulated mode"` persists while `prodApplyChrome()` rewrites visible text to "PRODUCTION — paging is ARMED." Screen readers announce Simulated on a live armed console — the lie runs in the dangerous direction. (UI P0-1, Petu-verified)
8. **`deploy/vercel/README.md` is a triple contradiction in one file.** Header: "No Vercel project was ever created"; body: describes the deployment; footer: token provisioning presupposing the project. The file an operator reads first. → Rewrite from scratch. (docs P0)
9. **Two open lanes, unmerged evidence.** `lane/production-stages` (`d6273d4`: CALIBRATION.md, FaithfulJev, judge modes — docs claim these exist on main) and `lane/loadtest-env` (`a1bd8d2`: report + `3fac416` + `9dfeb96` + `581054d`). → Merge or formally re-plan TODAY; until then downgrade every load-test headline (numbers describe lane code, not shipped code). (docs P0)
10. **`LOADTEST-REPORT.md` not on main.** The public dashboard's evidence backs a file existing only on an unmerged branch. (docs P0)

### P1 — fix before any real user base
11. **Ops Health drawer empty after auth** (top P1): all-or-nothing `sync()` + silent catch → one endpoint failure blanks the operator's only inspection surface; token gate accepts any non-empty token client-side (paste ≠ auth). → Per-endpoint failure isolation + visible sync errors + server-side token verification round-trip. (UI follow-up)
12. **Per-instance kill state on serverless** (if not fixed by #1): externalize (KV/DB row with fencing) or make the flip endpoint 501 `kill_not_distributed`. Knight Capital is the anchor for why per-instance safety flags kill companies. (infra P0→P1 after disclosure)
13. **FakePD is an accept-stub**, not the claimed lifecycle model; sim exercises the legacy forwarder, never the production DurableForwarder — zero simulation coverage of the actual paging machinery. → Make FakePD faithful (dedup, 429/5xx, latency, delivered/failed) and point sim at DurableForwarder. (sim P1)
14. **Stale Vercel bundle**: live `production-api` bundle predates JudgeResult observability; nothing verifies bundle-vs-source drift. → Rebuild from main + drift check. (Jev P1)
15. **Keystore plaintext-at-rest by design** for verify-only secrets → hash-at-rest (SHA-256; the break-glass path already proves the pattern). (security P1)
16. **No observability on the deployed backend**: ~30-min Hobby log retention, no uptime monitor, watcher doc describes a phantom self-hosted box. → Free uptime monitor + log drain; rewrite the watcher/rollback docs to Vercel reality (dashboard Instant Rollback; env vars NOT rolled back). (infra P1)
17. **Rotation is flag-day** while docs describe a dual-accept ceremony → wire two-env-var overlap or amend the doc. (infra P1)
18. **Dead charts on `/loadtest/`**: zero canvas; ~1,439 negative-width SVG rects render nothing; headline "16.2M/day sustained" unqualified ("sustained" is a real-time word on virtual-time numbers); 40.2→55.0→187.4/s tier inversion unexplained. → Fix charts, qualify headline in the headline, explain the inversion. (UI P1-4/P1-5)
19. **Judge-down invisible in prod chrome** (`getJudgeDown(){ return false; }` hardcoded) while the key is unset and the banner claims "every action is real." → Wire judge state into the banner or soften the copy until the key lands. (UI C3, docs #6)
20. **Console winner heuristic mislabels** `error_passthrough` as a judge win (uses `jev_model` presence instead of `budget_outcome`). (Jev P1)
21. **Dead-letter alarm inconsistency**: `_retryable` max-age path dead-letters with no control-plane page, violating the module's "morgue with an alarm" invariant. (engine P1)
22. **`/preview-v2/` still live** though the parity doc orders removal at shift — remove or formally keep. (infra/docs P1)
23. **No CI** though the playbook mandates "Green CI required" and "Red = no merge" — stand up workflows or stop mandating; enable GitHub secret scanning + push protection (free, public repo). (Jev C5, arch P2-9)
24. **Promotion gates are honor-system** (entrypoint-import gate has no enforcement). (infra P1)
25. **Deploy is click-ops**: `build-bundle-prod.sh` doesn't pin/record the commit; `vercel.prod.json` pins no runtime. → Embed `BUILD_COMMIT`, refuse dirty trees, record deploy command. (infra P1)

### P2/P3 — polish and hygiene (condensed)
- Rewrite PRODUCTION-STAGES.md backend sections to Vercel reality; fix CONSOLE-PARITY.md deployment mapping (dual-mode bundle, build-script assert — not "CI"); update readiness item 14 (ops/health IS built). (docs P1→P2)
- Delete dead `staging/api/calibration.json` (ECE=0.0 landmine); fix README "calibrated probabilities out" vs ARCHITECTURE.md's honest walk-back. (docs P2/P3)
- Define `pillwarn`/`pillmute` CSS; guard Shift+E handler; fix violet-vs-black drift; create or unreference phantom research docs. (UI P2/P3)
- Lock forwarder `metrics` dict; reset `_canary_count`; freeze `_evaluate` when disabled; schedule `expire_mutes` or document read-time enforcement. (engine P3)
- Reconcile two spend meters before advisory directions wire into prod; align v2 showcase sim latencies with the Oracle campaign (p50 816/p95 1312/B=2700) or label the deviation. (Jev P2/P3)
- Rebuild/delete stale `deploy/dist` demo bundle; fix the 6 `secrets-grep.sh` false positives so the gate is green. (security P2)
- Stamp `policy_version`/`generation` on `decision_made`; add first-class policy rollback; persist correlator or document restart behavior; promote watchdog page from stderr. (arch P2)
- Derive forwarder identity from endpoint URL, not key presence; make `no_real_pd` an egress check or relabel it. (sim P2/P3)
- Threat-model the `_int_test_page` real-PD relay (stolen operator token → real pages). (arch P3)
- Restate drill headline as "~2ms (single local sample, ±1ms instrument), code-identical to main." (engine P2)

---

## CROSS-DEPARTMENT CONTRADICTIONS (must be ruled on)

**X-A. The drill headline vs the drill topology.** "Kill drill PASSES — 2.0ms flip→halt, 11/11" is TRUE of the `DurableForwarder` topology AND the artifact is honest (`host: htch-runtime`, method in notes). But the production receiver wires the legacy `Forwarder` with zero kill checks — the property was proven of a forwarder the pipeline doesn't use. The headline must carry its topology qualifier everywhere it appears, or it becomes the quant_platform failure ("dashboard showed active:true while the engine kept placing orders"). (arch P0-1, engine P0-1, engine ruling)

**X-B. The PagerDuty law, three ways.** The audit brief said "NEVER real PagerDuty." `PRODUCTION-STAGES.md` says "PagerDuty in prod = real, via the user's BYOK key… FakePD is sim-only." The sim chief proved `SENTINEL_SIM=1` *defaults* to real PD. All three cannot stand. Recommended ruling: sim/loadtest NEVER real PD (structural, Stripe-`sk_test`-grade); production MAY page real PD only via explicit customer BYOK + explicit operator action (the test-page precedent). Write the ruling down; the brief's absolute law would outlaw the product's purpose. (arch P2-6, sim P0)

**X-C. "Fail-open proven under pressure" vs main.** True of `lane/loadtest-env` (with `3fac416`); false of `main` as shipped. Every repetition of the claim must name its code revision until the merge lands. (engine P0, docs #10)

**X-D. Kill semantics, three meanings.** UI copy: fail-open ("stop suppression → page everything"). C3 backend where wired: fail-closed ("halt forwarder → page nothing"). Deployed tier: no-op. Pick ONE at owner level — the catastrophic automatic action in this product is *suppression*, which argues fail-open, but that makes the `DurableForwarder` halt implementation wrong (a code change, not copy). (engine ruling, arch P0-1)

**X-E. "Surrogate ONLY" vs production reality.** The brief's "vault surrogate ONLY" describes the agent/research path (`jev_smoke.py`); the shipped engine uses `TYPESAFE_API_KEY` env — compliant with the standing law (never committed/logged) but not the surrogate. Correct the framing; don't weaken the law. (Jev X3)

**X-F. Load-test evidence vs shipped code.** The 16.2M/day numbers, the three bug fixes, FaithfulJev, the full report — all on unmerged lanes. The public dashboard is honest about its caveats but its evidence chain points off-main. Merge or formally defer; no third state. (docs #4/#5/#10, sim backstage, engine P3-7)

**X-G. "CI asserts" vs no CI.** CONSOLE-PARITY.md, LANE_PLAYBOOK.md ("Green CI required"), acceptance criteria — no `.github/workflows/` exists. The banner assert lives in `build-v2.py` (fine, but not what the docs claim). Either stand up CI or rewrite every "CI" sentence. (docs #8, Jev C5, arch P2-9)

**X-H. The `/` surface vs the environment matrix.** Matrix: `/` = production console, real UI, zero fixtures. Shipped: dual-mode bundle whose static bytes are the sim build (sim-band markup, `aria-label="Simulated mode"`), flipped to prod chrome by JS post-token. The design works when JS+token cooperate and mislabels otherwise (P0-7). Decide: static prod chrome in the HTML, or amend the matrix. (UI C5, UI follow-up P0-1, docs #8)

---

## PER-DEPARTMENT VERDICTS

| Department | Verdict | Confidence |
|---|---|---|
| Architecture | CONDITIONAL FAIL — do not call production-ready | 85 |
| Engine | P0: fail-open fix unmerged; drill substantiated w/ caveats | 85 |
| UI/UX design-ethics | 52/100 CONDITIONAL FAIL | 92 |
| Infra/Deploy | 48/100 — NOT production-grade; honest demo-tier in production's clothes | 85 |
| Security/Auth | CONDITIONAL PASS — perimeter holds; P0 token-on-disk; P1 plaintext keystore | 88 |
| Jev integration | Core sound; P0 dead calibration assets public; P1 stale bundle | 82 |
| Simulation fidelity | 84/100 CONDITIONAL PASS — P0 SIM=1→real-PD default | 84 |
| Docs & honesty | CONDITIONAL FAIL — honesty law excellent, bookkeeping drifted | 82 |
| Petu live-browser judging | PRODUCTION **F**, staging B+, loadtest B, preview-v2 B | — |
| Engine kill-switch ruling | (c) deployed kill is a no-op lever; copy also backwards | — |

Department details follow. Each section: verdict → pros → cons → contradictions → suggestions → limitations, with file:line / URL / live-byte evidence and real-world anchors.

---

## 1. ARCHITECTURE — conditional fail (85)

**Pros.** The race is real (three-party: inference worker / single-thread timer scheduler / gate backstop B+ε; one-lock claim serialization; monotonic clock; timer-wins measured from claim transitions — `src/sentinel/race.py`). The determinism boundary is structural: Jev feeds a pure policy table; suppress needs triple-lock + freshness + D8 + D5; the kill path is stdlib-only with an AST test failing the suite on any Jev import (`tests/test_safety.py:490`). Fail-closed discipline live-verified: missing thresholds → receiver refuses boot; no freshness bundle → suppress unreachable. Tamper-evidence honestly scoped (hash-chain + customer-HMAC; docstring states what HMAC doesn't buy — anchored to Sigstore Rekor as the industry bar). Delivery-confirmation honesty: `forward_confirmed` = "202 + status==success", documented "does NOT mean the human's phone rang" — matches PagerDuty's own 202 semantics. Circuit breaker reads `not_implemented` literally (test-asserted).

**Cons / contradictions.**
- **P0-1.** Kill switch doesn't halt the paging path: `receiver.py:1255` wires legacy sync `Forwarder` ("the frozen flaw's shape"); kill checks exist only in `DurableForwarder`; zero kill mentions in receiver/gate/correlator. The 2.0ms drill measured the wrong topology. Anchors: Knight Capital; the `quant_platform` incident ("make the kill switch reach the engine it is supposed to stop"); Cloudflare's "keep the kill switch off the system it kills, and drill it."
- **P0-2.** Prod console claims "paging is ARMED" to a Vercel tier with no receiver/gate/forwarder — its own health endpoint says `not_instrumented`.
- **P1-3.** Receiver hot path: legacy `Forwarder.forward` counts any 2xx as forwarded, no outbox, no retry — a crash between gate decision and PD POST loses the page, contradicting the module's "neither lose nor double-page" promise. Industry answer is the transactional outbox (Debezium / Chris Richardson); the design knows it, the wiring doesn't use it.
- **P1-4.** Deployed kill state: `/tmp` per-instance, `log=None` (contradicting safety.py's "every transition appended to audit log"), cold start silently disengages — the dangerous direction.
- **P2-6.** Law-level: brief's "NEVER real PagerDuty" vs PRODUCTION-STAGES.md "prod PD = real via BYOK" — needs the X-B ruling.
- P2-7..P3-15: correlator in-memory (restart loses episodes); watchdog "page" is stderr print; no CI; no per-decision policy-generation attribution; no first-class policy rollback; R15 F4 dedup can carry pre-degradation suppress (honesty-by-comment); `BUDGET_OUTCOMES` duplicated; deployed tier labeled "production" with empty DB; `_int_test_page` widens stolen-token blast radius to real PD pages.

**Suggestions:** wire DurableForwarder into receiver + re-drill on receiver topology (P0); banner may never claim more than ops/health attests (P0); shared kill-state backend or 501 `kill_not_distributed` (P1); openapi.yaml v1.1.0 covering the write endpoints (P1); rule on X-B (P2); persist correlator, real watchdog channel, CI, policy version stamping, rollback transition (P2); dedupe vocab, external-anchor checkpoints, threat-model test-page (P3).
**Limitations:** authed backend surfaces judged from code only; engine trace used SENTINEL_MOCK=1; 5 test files run (all OK), not the full suite.

## 2. ENGINE — P0 unmerged fix; drill substantiated with caveats (85)

**Pros.** Race is real threading with measured outcomes (loadtest pressure phase: timer-wins 50v1, suppression fell 0.217→0.009). Kill path structurally isolated (AST test). Forwarder kill discipline defense-in-depth (checks at scheduler head, worker head, `_attempt` first line; in-flight rows requeued, never dropped). Fail-open ladder can't suppress by construction (`__post_init__` refuses suppress-capable maps). Tests honest: 126/126 platform (WSGI-level, no mocks) + 106/106 engine, run by the auditor. Mute governance loud (human attestor + reason + TTL; source-scanner for unattested paths).

**Cons / contradictions.**
- **P0.** `failopen.py:244` — unsynchronized deque append vs popleft-prune/iterate under the threaded receiver (`receiver.py:959-981` ThreadingHTTPServer, one shared Pipeline/Gate); `gate.py:497-503` swallows observation loss in stderr print. The loadtest caught `RuntimeError: deque mutated during iteration` live; fix `3fac416` (locks + snapshot iteration + RLock) exists on `lane/loadtest-env` and is NOT in main (`merge-base --is-ancestor` → no). "Fail-open proven under pressure" describes lane code, not shipped code. Anchors: the fintech silent-monitoring pattern ("missing alerts are incidents"); Knight Capital (validated ≠ deployed).
- **P1.** `_retryable` max-age branch dead-letters with no control-plane page — the module's "morgue with an alarm" invariant violated on one path (Google SRE Ch.22 anchor).
- **P2.** Drill 2.0ms SUBSTANTIATED (real PlatformApp, real HTTP, real forwarder threads, decision-log timestamps; kill core byte-identical 71d3021→main) BUT precision-theater: 1ms-quantized instrument displaying one decimal; single sample; 2 of 11 checks vacuous (`measured_from_log_ts` tautological; `status_200` shallow). Restate: "~2ms single local sample (±1ms), two orders under the 5s bar."
- **P2.** Bounded body reader verified code AND live (auth precedes body parse on deployed backend; Slowloris anchor — the textbook mitigation).
- **P2.** Per-instance kill divergence correctly modeled, honestly surfaced (AWS Lambda /tmp semantics anchor).
- P3: forwarder `metrics` unlocked `+=` (same bug class as P0); `should_canary` counter never resets; `enabled=False` doesn't freeze the state machine; `expire_mutes` never scheduled; load-test learnings (`3fac416`, `9dfeb96`, `581054d`, btrfs note) stranded on branch.

**Suggestions:** merge `3fac416` NOW + N-thread hammer test (P0); merge-discipline gate — no lane-branch production fix stays unmerged past the report (P0-process); alarm the max-age dead-letter path (P1); restate drill headline (P2); the P3 small ones; record the btrfs/fsync note on main.
**Limitations:** no authed access; text-only page fetches; no concurrent-load repro against prod; raw load-test audit DBs not in repo (arithmetic verified, not raw rows).

## 3. UI/UX DESIGN-ETHICS — 52/100 conditional fail (92)

**Pros.** Sim surfaces scream SIMULATED (in-band black band, seed shown). Attention units above raw events; grouping provenance explained via trace popovers; fixed-position updates (keyed rows, hover-to-hold stream); reversibility where built (appeal preserves original in append-only ledger; kill two-step with measured ms); load-test dashboard has an honesty spine ("What this test did NOT prove," FakePD-only, "measured, never asserted"); backend health never 500s and never fakes `closed`; prod writes fail honestly ("Decision records are immutable — acknowledge in PagerDuty").

**Cons / contradictions (C1–C15 + follow-up).**
- **C1/P0.** "Every action is real" false: backend is `fakepd` + judge-down. Parity law demands loud red banner for fakepd-in-prod; reality is a green "healthy" pill in a drawer (C2 — the AWS green-tick anti-pattern, anchored).
- **C3.** `getJudgeDown(){ return false; }` hardcoded — judge-down invisible in prod; the amber degraded banner can only fire in sim.
- **C4.** Sim fixtures (`auth 2/3 locked`, policy "Signed by Aditya") render under PRODUCTION chrome when sync fails silently — explains the founder's screenshot.
- **C5.** Shipped `/` bytes contain sim-band markup + "Nothing here pages anyone" comment; PRODUCTION banner is JS-only post-token. First paint says SIMULATED on the production URL.
- **C6.** `/preview-v2/` live, 233 lines behind staging — the exact fork the parity doc forbade.
- **C7.** "Undoable for 60 seconds" — no undo implementation (Gmail-undo anchor: the label is true only because the mechanism defers the action).
- **C8.** `dataAgeSec` computed, zero render sites — backend-down = silently frozen console.
- **C9.** `pillwarn`/`pillmute` CSS undefined — degraded renders as unstyled text.
- **C10.** Ops Health is a static card — the Statuspage anchor: "the only honest status page is automated"; this one asserts.
- **C11+P1-4.** Load-test charts: zero canvas; ~1,439 SVG rects ALL negative-width (invalid, paint nothing). Merged defect.
- **C12.** Violet-vs-black drift → upgraded by delta: `tokens.css` referenced ZERO times by the shipped console and describes a different theme — the token system was never wired.
- **C13.** "Key-source shown" = pointer to a drawer.
- **C14.** Phantom research docs referenced from shipped HTML.
- **C15.** Shift+E throws in prod (evalbar removed, handler remains).
- **P0-1 (Petu-verified, confirmed worse than stated).** `aria-label="Simulated mode"` persists while visible text becomes "PRODUCTION — paging is ARMED" — screen readers announce safety on an armed console.
- **P0-3 (symptom confirmed; all three Petu suspects refuted).** Empty drawer after auth; CORS (204 preflight OK), token-attach (present), payload shape (matches) all refuted. #1 suspect: all-or-nothing `sync()` + silent catch — one endpoint failure blanks the drawer. #2: gate accepts any non-empty token client-side (paste ≠ auth). #3: no catch-all in `PlatformApp.__call__` → bare 500 without CORS headers → swallowed.
- **P1-5 (partially confirmed).** Caveats exist and are findable (credit), but "16.2M/day **sustained**" is unqualified in the headline and the 40.2→55.0→187.4/s inversion is unexplained — Jepsen/SRE-workbook anchor: qualify the headline in the headline.

**Backstage (delta).** Banner contract = substring assert, can't fail a dishonest build; `build-v2.py` has zero test coverage (only obsolete builder tested). Honesty invariants declared "release-blocking automated tests" — no such test exists. "Design review is a gate equal to code review" — no paper trail.
**Suggestions:** P0: rewrite prod banner to backend truth + loud fakepd banner (delete "every action is real"); wire judgeDown + staleness from health payload; init prod S.auth/policy as unknown; remove/rebuild preview-v2; fix aria-label. P1: implement undo or delete claims; define pillwarn/pillmute; render contracted Ops fields; fix charts + provenance + per-tier cost labels. P2: static PRODUCTION chrome in HTML; guard Shift+E; pick one banner color. P3: create or unreference phantom docs.
**Limitations:** no live browser — post-token rendering, drawer contents, interactive states unobserved (Petu's takeover covered the judged paths); C9/C11/C12 remain code-level.

## 4. INFRA/DEPLOY — 48/100, not production-grade (85)

**Pros.** Auth live-verified (401s, `Cache-Control: no-store`, `X-Sentinel-Deployment: production-api`; fail-closed without token). Honest degradation end-to-end (SSE → 501 `stream_unsupported`, documented fallback). Per-instance limitation disclosed in docstrings + API (better than 90% of industry code — still not safe). gh-pages pipeline deterministic; live bytes == built bytes (`data-backend` correct; branch tip = served). Stdlib-only bundle (correct for cold starts). Backstage workflow professional (lane discipline, conventional commits, `pre-pr-gate.sh`, honest POST-hang root-causing to the sandbox proxy).

**Cons / contradictions.**
- **Kill switch cannot function on this infrastructure** (P0): per-instance `/tmp`, multi-region serving observed live (`cle1`+`sfo1`), cold-start wipe → fail-dangerous default, and no forwarder on the tier — a flag with no circuit. Anchors: Knight Capital; 2026 kill-switch literature ("change the value in every running instance"); Sentinel's own "fail OPEN is wrong for a kill switch" comment ships anyway.
- **Cold starts delay the operator's safety action** (1.97s→0.54s warm-up decay observed); no measured flip latency exists for the deployed tier.
- **Nobody would know if prod broke**: ~30-min Hobby log retention, no uptime monitor, no metrics; `ops/watcher-deployment.md` describes a phantom self-hosted box; `vercel.prod.json` has no crons.
- **Click-ops, not IaC**: bundle script copies the working tree without pinning the commit; "assembles from the deployed commit" is aspirational; no runtime/`maxDuration` pins; `deploy/vercel/README.md` still says "No Vercel project was ever created."
- **Rotation is flag-day** (`{"primary": provisioned, "secondary": None}` on serverless) contradicting the documented dual-accept ceremony. Anchors: Stripe 7-day overlap; AWS IAM 2-key cap; Railway cutover invariant.
- **Promotion gates honor-system**: `pre-pr-gate.sh` real but unenforced; entrypoint-import gate "needs a box-side pre-merge hook, still TODO."
- Rollback runbook documents the never-deployed self-hosted topology; real path (Vercel Instant Rollback, Hobby = previous deployment only, env vars NOT rolled back) undocumented. `docs/deploy-production.md` (systemd, :8080) builds the wrong system.
- `/preview-v2/` live contradicting the parity doc. Banner "paging is ARMED" with no paging path. Deployed drill invisible to the API (`drill_dir=None` → `last_drill` permanently null).
- ₹0 check: no hidden cost vector at current usage; latent constraint is the Hobby non-commercial-use clause.

**Suggestions:** P0: externalize kill state (Vercel KV/Upstash free tiers) or stop presenting the switch as a safety control; write the actual rollback runbook. P1: remove preview-v2; uptime monitor + log drain; pin deploy (runtime, BUILD_COMMIT, refuse dirty trees); fix rotation story. P2: server-visible drill artifact. P3: banner copy; README reconciliation.
**Limitations:** authed paths unprobed (no token); page-text reads discarded as cached; Vercel caps from published docs.

## 5. SECURITY/AUTH — conditional pass (88)

**Pros.** C1 bearer centrally enforced (`app.py:190-199`, before admission control; only `/health/live|ready` exempt); 20 routes probed live unauth → 401 (incl. POST `/api/v1/integrations/keys` — the original P0, closed; POST `/api/v1/safety/kill`; unknown paths 401 *before* 404, no path oracle); exact 24-byte body, `no-store`, HSTS; header games rejected; `hmac.compare_digest`. CORS: exact-match allowlist, no reflection, no credentials; evil origin gets nothing. Secret hygiene strong in code: atomic 0600 keystore writes, metadata-only audit log, SHA-256 break-glass tokens, write-only BYOK (last4), `pd_sender` hard-rejects frozen payloads with routing keys, Jev key env-only + `sanitize_error()`, drill token via env not argv, Vercel Sensitive env (write-only). Dependency law holds (`cryptography==44.0.3` only). Git history sweeps clean (only `<redacted>` placeholders + provably-fake fixtures).

**Cons / contradictions.**
- **P0.** `platform/server/sentinel-state/operator_token.json` — live-format 43-char `token_urlsafe(32)` operator bearer in plaintext on disk, created 2026-10-07 07:17 UTC by a local dev run (during this audit wave). Gitignored + 0600, but per standing law any live credential in any file = P0. (Not tested against prod.)
- **P1.** Keystore persists verify-only secrets in plaintext *by design* (`hmac.compare_digest` against stored plaintext). Anchors: Waldur `w_` PATs, Granite ADR-0009, Blooby, yaadegar ADR-0016 — hash-only storage is the industry norm for 256-bit tokens (no stretching needed). Fair counter: BYOK keys must stay plaintext (client-presented, like `gh`'s `hosts.yml`) — that half is correct.
- **P2.** Stale `deploy/dist/vercel/api/_srv/app.py` — zero auth references; anyone deploying the demo without rebuilding reopens the original P0.
- **P2.** `scripts/ops/secrets-grep.sh` exits FAIL on 6 false positives (vault-ref names, `surrogate-never-sent` fixtures, `Bearer <redacted>` in tests) — a red gate trains ignore-the-gate.
- P3: no `WWW-Authenticate: Bearer` (RFC 6750 SHOULD); `/api/stream` 501 precedes auth in the Vercel wrapper; no console sign-out (sessionStorage lifetime); `X-Sentinel-Deployment` header leaks flavor naming.
- Contradiction: `auth.py:12` "plaintext leaves this process exactly once" vs persisted `operator_token.json`; brief's "never persisted" vs server-side disk persistence (console honors it; server doesn't). OWASP goes further than both (HttpOnly Secure SameSite=Strict cookies preferred over sessionStorage) — the residual XSS exposure should be documented.

**Suggestions:** P0: delete/rotate the token file; default `SENTINEL_STATE_DIR` outside the repo tree. P1: SHA-256 at rest for verify-only secrets (both keystore twins). P2: rebuild/delete stale `deploy/dist`; fix the 6 allowlist gaps. P3: `WWW-Authenticate`, stream-behind-auth or contract exception, sign-out.
**Limitations:** all authed paths, Vercel env config, and the deployed drill unverifiable without the token (correctly not used).

## 6. JEV INTEGRATION — core sound, edges wounded (82)

**Pros.** Key hygiene real (env-only entry, source-only logging, `sanitize_error`, spend endpoint carries amounts/counts only). Cost caps ENFORCED in code: `try_begin_call()` gates before network I/O, `blocked` latches permanently, budget 0.0 → zero spend authorized; `JevBudgetExhausted` → race `error_passthrough` → deterministic fail-open page; cost from `input_tokens × $0.042/1M`, "inventing [usage] would be dishonest." Anchor: FinOps hard-limit side — strictly stronger than OpenAI's alert-default; the "$347 weekend" postmortem is the prevented failure. Control principle holds: the only Jev call sites are race pool submissions + detached advisory; suppress needs kernel + D8 + D5; all six advisory directions run through `AdvisoryDispatcher` (never-raise, honest fallbacks) and are unwired in production at main; prompt fragility measured (1.3–2.2% flips, Wilson CI, McNemar) with an honest console flip card. FaithfulJev calibration VERIFIES elementwise: 109/109 samples match raw JSONL within 0.05ms (p50 816/p99 1526); fault injection raises the production exception types (Netflix-FIT anchor: same code paths as real faults). Shadow-attribution fix verified (`shadow_decision` linked, never merged into `decision_made`; DR-26 fixed the "lying mirror"). Ordinality holds in engine and v2 console ("ORDINAL 0..1, never a probability"); anchored to the LLM-as-judge overconfidence literature (ECE up to 74 — treating confidence as calibrated probability is industry-known-bad).

**Cons / contradictions.**
- **P0/X1.** Dead v1 console assets on the PUBLIC gh-pages branch: `assets/lib.js` `gloss80()` ("When Sentinel says 80% confident, the outcome matched about X%"), `stripCal()`/`calVerdict()` (ECE gates), `receiptLine()` (`` `P(p1)=` ``); `views-cal.js` ("Expected Calibration Error," "Reliability diagram," "the diagonal is perfect honesty"). Directly contradicts ACCEPTANCE.md AC-8c ("never a probability — no '%', no 'P='"). Dead-but-public is public.
- **P1/C1.** Deployed Vercel bundle is STALE: `sim_judge.py` absent, zero `_judge_record` references in the bundled `gate.py` — even with the key set tomorrow, no JudgeResult observability. Nothing verifies bundle-vs-source drift.
- **P1/C2.** Console winner heuristic (`jev_model` presence) mislabels the `error_passthrough` path as a judge win — use `budget_outcome`.
- **P1/C5.** No CI; `secrets-grep.sh` fails on fixtures; "Green CI required" / "Red = no merge" unenforceable. Anchor: GitHub secret scanning + push protection is free on public repos — the industry default.
- **P2/C3.** Two spend meters unreconciled (`advisory.SpendMeter` estimated vs `sim_judge.JevSpendTracker` actual) — latent disagreement when advisory wires in.
- **P2/C4.** FaithfulJev not on main — the audited tree doesn't contain the calibrated thing; docs describe it, code doesn't.
- **X2.** Backend computes ECE over Jev's reported p1 at `/api/calibration` while the law bans probability presentation — a loaded gun.
- **X3 (correction).** "Surrogate ONLY" describes the agent path (`jev_smoke.py`); the shipped engine uses `TYPESAFE_API_KEY` env — compliant, not the surrogate. Fix the framing.
- **C6.** v2 showcase sim latencies (p50 520/p95 2400, B=3000) don't match the Oracle campaign (816/1312, B=2700) — labeled SIMULATED, but the faithful-fakes law should reach the showcase.
- C7: preview-v2 live (flagged for infra).

**Suggestions:** P0: delete dead v1 assets from gh-pages; decide `/api/calibration`'s fate (reframe as p1-severity-map or remove). P1: rebuild bundle from main + drift check; fix winner heuristic. P2: stand up CI or stop mandating it (+ secret scanning); reconcile spend meters. P3: align showcase latencies; remove preview-v2.
**Limitations:** authed console unverifiable (L1); "$0.02 of $5" from the lane report, mechanism verified not the figure (L2); no live vendor calls (L3); advisory runtime behavior test-only (L4).

## 7. SIMULATION FIDELITY — 84/100 conditional pass

**Pros.** FakePD sink loopback-guarded; sim labels pervasive (`[SIMULATED]` prefix, `labels.simulated=true`, seed in generator). Determinism empirically verified: two `storm-surge` runs → identical signature/histogram/metrics/92 pages (FoundationDB-DST anchor — and unlike most adopters, the replay is actually verified). FaithfulJev: embedded 109-sample table with source pointer, seeded RNG, real thread-blocking sleep, real exception types (`JevError`/`JevRateLimited`/`JevTimeout`) — the pressure phase is genuine evidence (50v1 timer-wins, suppression fell). `FakeJev` answers `cannot_determine` only (uncertainty pages by construction); spend tracker records 0.0 on failed calls. DurableForwarder's acceptance semantics match the PD vendor contract (202 = accepted, not delivered). Load-test report discloses what it did NOT prove + three real bugs caught — exemplary reporting.

**Cons / contradictions.**
- **P0.** `SENTINEL_SIM=1` receiver path defaults to `https://events.pagerduty.com/v2/enqueue` + `PD_ROUTING_KEY`, protection opt-in only (`SENTINEL_SIMULATED_PAGING`). The comment "PagerDuty stays FakePD in sim — the forwarder wiring below is untouched" is FALSE — the wiring below it is the real URL. `docs/planning/track2-judge-handoff.md` repeats it. Anchor: Stripe `sk_test` / Twilio test credentials — mode in the credential, structural impossibility, not a toggle.
- **P1.** FakePD is an accept-stub (no sleep, no dedup, no faults, no delivered/failed) — the claimed lifecycle "received→accepted→queued→delivered/failed with dedup + failure injection" is not implemented. `LOADTEST-REPORT.md` "the sink models ACK latency and the event state machine" is FALSE.
- **P1.** Sim exercises the legacy sync `Forwarder` (counts any 2xx as forwarded, no body check) — never the production `DurableForwarder`. Zero simulation coverage of the actual paging machinery, incl. the Type-1 `202+success` rule.
- **P1.** FaithfulJev/MixedJevClient stranded on `lane/loadtest-env`; main's sim uses zero-latency mocks — the "FAKES ARE FAITHFUL" law not embodied on main.
- P2: `no_real_pd` "verification" is a hardcoded constant + URL-prefix check (relabel "address-constrained" or do an egress check); `flaps_seen=True` vacuous; dashboard drops three report caveats (FakePD modeling, drift-inactive, rate-based capacity); `/` serves the sim build while the matrix promises the production console; forwarder identity derived from key presence, not endpoint URL.
- Timeout-fault shape caveat: injected `JevTimeout` raises immediately rather than hanging for the real 30s wire timeout (opt-in, documented, but not the same shape — the 2.7s race budget would win first in reality).

**Suggestions:** P0: hard-force FakePD in `SENTINEL_SIM=1` (loopback + SIM-FAKE key + assert) or refuse boot; forbid non-loopback `pd_events_url` under any sim marker (Stripe model). P1: make FakePD faithful (PD dedup behavior, 429/5xx per vendor retry table, ACK latency, delivered/failed) and point sim at DurableForwarder; merge FaithfulJev to main + determinism/cap-trip/fault-rate tests. P2/P3 as above.
**Limitations:** no live browser (console interactions untested beyond text fetch); deployed `SENTINEL_SIMULATED_PAGING` unobservable; branch code read not executed; DurableForwarder-vs-FakePD never run (itself part of the finding).

## 8. DOCS & HONESTY — conditional fail (82)

**Pros.** Honesty machinery works: unauth `/` shows the SIMULATED banner (verified live); token gate blocks prod chrome without auth (IncidentLens "never enable demo mode in production," structurally implemented — the Medium 2.8M-rows wrong-environment postmortem is the prevented failure); backend fail-closed live-verified; `not_implemented`/`not_instrumented` rendered literally per the parity source map; the 16.2M/day claim trail does NOT degrade at the dashboard (all six caveats verbatim in the footer — rare); sim judge-down toast in-band, in the user's language.

**Cons — the contradiction matrix (exact quotes + paths):**
| # | Claim X | Reality Y | Class |
|---|---|---|---|
| 1 | `deploy/vercel/README.md:3` "No Vercel project was ever created; nothing was ever deployed here." | Same file:44 presupposes the project; live backend 200 OK | Stale doc, P0 |
| 2 | PRODUCTION-STAGES.md:104 + CONSOLE-PARITY.md:46 "`/preview-v2/` REMOVED at production shift" | `curl …/preview-v2/` → 200 | Specified-not-built, P2 |
| 3 | PRODUCTION-STAGES.md §5/§8 self-hosted topology; "rollback = stop, check out, restart" | Vercel serverless; no host to SSH | Stale doc, P1 |
| 4 | PRODUCTION-STAGES.md §0 "Built: …`CALIBRATION.md`" | No such file on main; only on open `lane/production-stages` | Specified-not-built, P0 |
| 5 | PRODUCTION-STAGES.md §3 merge plan `lane/production-stages → program/full-build → main` | Branch open; `d6273d4` not in main | Specified-not-built, P0 |
| 6 | PRODUCTION-STAGES.md §7 "banner shows judge-down" | `getJudgeDown(){ return false; }` hardcoded; backend `not_instrumented` | Specified-not-built, P1 |
| 7 | `index.html:784` "paging is ARMED · every action is real" | Key unset → timer-wins fail-open; no judge-down state | Overstated, P1 |
| 8 | CONSOLE-PARITY.md "…differing only in adapter bundle + banner config… CI asserts the banner contract" | Single dual-mode bundle; no CI; assert in build script | Stale doc, P1 |
| 9 | PRODUCTION-STAGES.md item 14 "`GET /api/v1/ops/health` ⏳ not yet implemented" | Implemented on main (`app.py:270`, tests) | Stale (favorable), P1 |
| 10 | "Fail-open proven under pressure" as fact of main | Fix `3fac416` + report `a1bd8d2` only on `lane/loadtest-env` | Unmerged evidence, P0 |
| 11 | README "calibrated probabilities out" | ARCHITECTURE.md:348 "raw week-1 probabilities are uncalibrated… 1.3–2.2% flips" | Oversell, P3 |

Plus: dead `staging/api/calibration.json` (n=0, ece=0.0 — credibility landmine, P2); `EVALUATOR HARNESS` footer on the prod URL (P3); judge-down promise unimplemented (P1).

**Suggestions:** P0: rewrite `deploy/vercel/README.md`; decide the two lanes' fate today; put `LOADTEST-REPORT.md` on main. P1: fix stages §§5/8 to Vercel reality; fix parity deployment mapping; build-or-drop the judge-down banner. P2: preview-v2 decision; remove dead calibration.json. P3: mode-aware copy; README alignment.
**Limitations:** code-only beyond fetches; no live browser (post-token chrome judged from code); unauth probes only; ~45-min timebox; load-test judgment semantics taken from the report's caveats.

---

## ENGINE RULING — kill-switch semantics (P0-2)

**Ruling: (c) — the deployed kill is a no-op lever.** The chain holds button → endpoint → `engage()` → flag flip, then breaks: `PlatformApp` stores the switch (`app.py:98`) and nothing in the process consumes it (`ops_health.py:110-123` report only). The copy is *also* backwards against C3 semantics — but copy-correction alone would replace one lie with another.

**Semantic map:**
| Actor | Action | What actually stops | Evidence |
|---|---|---|---|
| Console kill button (prod) | `POST /api/v1/safety/kill` + token | Nothing — flag flips, `/tmp` written, 200 | `index.html:733-741`; `index-prod.py` (no forwarder/receiver); zero `.engaged` consumers outside reporting |
| `KillSwitch.engage()` | Flag + atomic file + audit event | Nothing by itself — consumers decide | `safety.py:130-170` |
| `DurableForwarder` (drill only) | Polls `kill.engaged` ~50ms | Send threads halt; rows requeued; zero sends | `forwarder.py:202,394-438`; artifact `no_sends_after_halt=true` |
| Receiver's legacy `Forwarder` | Never checks kill | Nothing — sends continue | `forwarder.py:1030` (0 kill mentions); `receiver.py:1255-1259` no `kill_switch` arg |
| UI copy (all prod surfaces) | Renders fail-open semantics | Describes an action never taken: "suppression paused → now page" | `index.html:960-963,987-991,1196-1197`; toast `:737-738` "Suppression paused — backend confirmed" (backend confirmed `{"engaged": true}`) |
| Sim adapter | `S.kill.engaged → decide(PAGE)` | Everything pages (fail-open) — the only place the copy is true | `index.html:485,612-615` |

**"Halt" per topology:** DRILL — DurableForwarder threads (real, 2.0ms). RECEIVER — nothing (no kill checks; receiver doesn't construct a KillSwitch). DEPLOYED — nothing (no forwarder/receiver at all; per-instance flag only).

**Fix direction:** (1) DECIDE fail-open vs fail-closed at owner level — the catastrophic automatic action here is *suppression*, which argues fail-open, but that makes the DurableForwarder halt implementation wrong (code, not copy). No copy ships until decided. (2) Wire the decided semantics on every topology. (3) Interim honest copy on the deployed tier: disabled button — "Kill switch (this tier): flips a per-instance flag only. No paging path on this tier reads it — engaging currently changes nothing (P0-1 open)." (4) Kill the fabricated toast — echo only what the response contained.
**Aditya hand-test implication:** his kill→re-arm sequence will 200/422/200 while provably nothing changes. Tell him before he tests, or the hand-test certifies a fiction.

---

## PETU LIVE-BROWSER JUDGING (7 Oct, all 4 surfaces)

**Production: F.** Staging: B+. Loadtest: B. Preview-v2: B.
Petu-verified, adjudicated by chiefs: (1) banner `aria-label="Simulated mode"` while PRODUCTION armed — CONFIRMED, worse than stated (P0); (2) kill copy inversion — CONFIRMED, ruled (c) above; (3) Ops Health drawer "No backend data yet" after auth — symptom CONFIRMED, all three initial suspects refuted, #1 suspect all-or-nothing `sync()` + silent catch (P1); (4) loadtest zero canvas / negative-width SVG rects — CONFIRMED, merged with C11 (P1); (5) headline-vs-caveats — PARTIALLY CONFIRMED (caveats findable; "sustained" unqualified; inversion unexplained) (P1). Correction applied: the claim "static .github.io can't talk to a live backend" is technically false — cross-origin fetch is the legitimate ProductionAdapter design.

---

## AUDIT LIMITATIONS (whole wave)

- No chief held the operator token: all authed backend surfaces and the deployed drill are unverified by the audit (Aditya hand-tests; Petu's takeover covered the judged console paths).
- Subagents cannot operate a live browser: interactive states judged from code + text fetch + Petu's verified observations.
- The 2–3h timebox bound web-search depth per department (2–5 anchors each); anchors are cited, not exhaustively surveyed.
- Raw load-test audit DBs are not in the repo; arithmetic verified, raw rows not re-run.
- One worker (docs-honesty, first dispatch) returned an empty handoff; it was respawned and delivered in full — noted for process hygiene.

---

## BOTTOM LINE

The honesty *law* is the best thing in this program — literal `not_implemented`, caveat-preserving retellings, fail-closed defaults, shadow-attribution discipline. The honesty *bookkeeping* is where it bleeds: docs describing phantom infrastructure, evidence stranded on unmerged branches, banners outrunning backends, and a kill switch that is a flag nothing reads. **Fix the ten P0s, rule on the eight contradictions, and this becomes the production system the docs already claim it is.** Until then: no production-live declaration, no "every action is real," no hand-test without the no-op-lever warning.

*Judge only. Nothing was changed. Branch `lane/audit-brutal-20261007`.*
