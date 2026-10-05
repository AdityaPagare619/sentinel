# DevOps as Elite Companies Practice It — Research Stream (Phase 1, Stream 3)

**Stream:** Phase-1 Stream 3 (DevOps), Architecture Revision Program
**Date:** 2026-10-05
**Author:** subagent research lane (devops)
**Constraints assumed:** ₹0 budget; GitHub Actions banned by standing order (local test suite is the promotion gate); self-hosted Python on Linux boxes; static GitHub Pages for the console; PagerDuty BYOK.

**Scope note:** `ops/devops-foundation.md` (L3, 2026-10-04) already implements environment tiers, a release process with feature flags, runbooks, and drill cadences. This document does **not** duplicate it — it is the *research foundation behind* those choices: what elite orgs actually do, why the mechanisms work, what Sentinel already covers, and what is genuinely missing. Every factual claim carries a source (URL + access/updated date).

---

## 1. CI/CD — DORA metrics, trunk-based development, and what CI catches that local runs don't

### 1.1 The DORA four metrics and why they are a *balanced* set

**Mechanism.** Google's DORA (DevOps Research and Assessment) program — Forsgren, Humble, Kim, *Accelerate* (2018, based on 23,000+ survey responses from 2,000+ organizations, 2014–2017) — identified four metrics that together predict software delivery performance:

1. **Deployment frequency** — how often code successfully reaches production (throughput).
2. **Lead time for changes** — commit → running in production (throughput).
3. **Change failure rate** — % of deploys causing degraded service, requiring rollback/hotfix (stability).
4. **Failed deployment recovery time** (originally "mean time to restore," MTTR) — how fast service recovers after a deployment failure (stability).

The central research finding: **there is no speed–stability tradeoff.** High performers are fast *and* stable simultaneously — 46× more frequent deploys, 440× faster lead times, 5× lower change failure rates, 170× faster recovery vs low performers (2017 figures). Optimizing one metric alone is the documented failure mode: "Improvement on one at the expense of another is not improvement" — the four exist to prevent single-metric decoupling.

**Why single-metric optimization decouples.** DORA's own later reports show the mechanism breaking: the 2024 State of DevOps report modeled throughput and stability as *separate factors* and found that AI/platform adoption could improve productivity while delivery outcomes declined (2025: throughput turned positive while stability stayed negative). Deploy frequency alone can be gamed upward while change failure rate quietly rises — the number moves while the true objective doesn't. The skill lineage (`principal-mindset`: "a proxy that moves without the truth moving is tuning theater") applies directly: the four-metric set is DORA's anti-theater device.

- Sources:
  - Book overview & methodology (23,000+ responses, 2014–2017, no speed–stability tradeoff): https://github.com/joellarson/knowledge/blob/HEAD/topics/test-driven-development/wiki/sources/accelerate-forsgren-humble-kim.md (accessed 2026-10-05)
  - Four metrics "designed to be read together"; improvement at one's expense is not improvement: https://github.com/edytakucharska/keel/blob/HEAD/frameworks/accelerate-four.md (accessed 2026-10-05)
  - Throughput/stability decoupling in 2024/2025 reports (secondary synthesis citing DORA 2024/2025): https://github.com/tjboudreaux/ready-agent-1/blob/HEAD/references/dora-crosswalk.md (accessed 2026-10-05)

### 1.2 Trunk-based development

**Mechanism.** Trunk-based development (TBD): developers merge small, frequent commits into a single shared trunk (main); feature branches are short-lived (< ~1 day) or nonexistent; incomplete work ships *dark* behind feature flags (see §2). The research finding: "elite performers who meet their reliability targets are **2.3× more likely to use trunk-based development**; low performers are more likely to use long-lived branches and to delay merging" (DORA 2021, quoted verbatim). Why it works: merge conflicts and integration failures scale with *branch lifetime* — a 3-week branch is a 3-week integration debt. Small batches also move all four DORA metrics at once: faster deploys, more frequent, quicker to diagnose/revert, less risk each.

- Sources:
  - DORA 2021 "2.3×" finding, verbatim: https://launchdarkly.com/blog/elite-performance-with-trunk-based-development/ (accessed 2026-10-05)
  - Trunk-topology nuance (stable core: small changes, automated checks, never leave trunk broken): https://github.com/tjboudreaux/ready-agent-1/blob/HEAD/references/dora-crosswalk.md (accessed 2026-10-05)
  - DORA 2021 report benchmarks (elite: deploys on-demand, lead time <1h, CFR 0–15%, restore <1h): https://github.com/trac41799/strata-knowledge/blob/HEAD/evidence/records/S-0163.md (accessed 2026-10-05)

### 1.3 What CI actually catches that local test runs don't

This is the crux question for Sentinel's no-CI reality. CI is not "running the tests elsewhere." It is a **detection system for three classes of defect that a developer's machine cannot reliably see**:

1. **Post-merge integration effects.** Two changes can each pass pre-merge tests independently yet conflict when combined on trunk. CI's *post-merge re-run* of the full deterministic suite against integrated trunk is what catches this. In CD terms: "regression testing" is just the full suite running on every commit; a green run means the artifact has been regression-tested against every behavior the suite encodes — and against the *actual combination* of changes, which no individual developer's local run sees. This is the single most important thing CI adds over local runs.
   - Source: https://github.com/bdfinst/cd-migration/blob/HEAD/content/en/docs/testing/_index.md (accessed 2026-10-05)
2. **Environment drift ("works on my machine" as a class).** CI runs on a clean checkout in a pinned environment — it catches failures caused by uncommitted files, wrong Python/interpreter versions, stale caches, locale/timezone differences, and config the developer forgot to commit. Local runs on a warm machine share the developer's accumulated state; CI's state is born fresh every time.
   - Source (static analysis catching version/config mismatch between developer environments; the regression-blame scenario): https://dev.to/how-to-dev/continuous-integration-ci-and-how-it-can-help-you-m5k (accessed 2026-10-05)
3. **The "when did it break" attribution.** When a failure is finally found, CI's per-commit verdict gives the *exact commit* where green turned red — no `git bisect` archaeology across a week of layered changes. The value is not just detection; it is the timestamped public record of trunk health.
   - Source: same dev.to article above; also https://github.com/haiduongdarkocean/cell/blob/HEAD/.agents/skills/ci-cd-and-automation/SKILL.md (accessed 2026-10-05) — CI's power is the automated feedback loop with failures attached to the change.

CI's standard gate order is: lint → typecheck → unit tests → build → integration → (optional e2e) → security audit — each stage filtering before the expensive stages run.
- Source: https://github.com/haiduongdarkocean/cell/blob/HEAD/.agents/skills/ci-cd-and-automation/SKILL.md (accessed 2026-10-05)

**Relevance to Sentinel.** This is the honest audit:

- ✅ **Covered:** `scripts/ops/pre-pr-gate.sh` is the promotion gate (496 tests, ~108s) — a CI-equivalent *test filter* for the single-merger workflow.
- ❌ **Not covered (no honest substitute exists locally):**
  1. **Post-merge integration effects.** With parallel lanes merging to `main`, two lanes can each be green at their base SHA and conflict at merge. The local gate runs on *the branch*, not on *main-after-merge*. This is the #1 thing CI adds that Sentinel structurally lacks.
  2. **Clean-checkout reproducibility.** A developer's warm box can hide uncommitted deps or stale caches. (Partly mitigated: `scripts/ops/env-bootstrap.sh` builds a fresh tree.)
- **The honest 80% without CI (₹0, order-compliant):**
  - `git` **pre-push hook** running the fast subset (lint/format + changed-area tests) — shifts the filter to zero-effort.
  - **Cron-based post-merge runner on a clean worktree** — a `systemd` timer or cron job that pulls `main` into a throwaway worktree and runs the full suite every N minutes / on each new SHA, posting the result to a status file. This replicates CI's *detection* classes 1–3 (post-merge combination, clean environment, per-commit attribution) without GitHub Actions, for zero cost. It is CI in the mechanism sense even though it isn't hosted on the forge.
  - Self-hosted CI servers (Woodpecker — Apache-2.0, ~100 MB server footprint, pairs with Forgejo/Gitea — are production-validated by Codeberg) exist at ₹0 if Aditya ever revisits the CI ban, but **under the standing order the cron-on-clean-worktree runner is the compliant equivalent**. Do not adopt GitHub Actions without Aditya revisiting the ban.
  - Sources: Woodpecker (lightweight, Apache-2.0, Codeberg adoption): https://prompts.brightcoding.dev/blog/woodpecker-cicd-why-teams-are-ditching-jenkins-for-this-lightweight-beast (updated ~2026-10-04, accessed 2026-10-05); https://opensourcealternative.to/project/woodpecker (accessed 2026-10-05).

---

## 2. GitOps & progressive delivery

### 2.1 GitOps patterns (Argo CD / Flux)

**Mechanism.** GitOps is not "CI/CD stored in Git" — it is a specific operational model defined by the GitOps Working Group's four principles: (1) **declarative** — the whole system described as desired state, not imperative scripts; (2) **versioned & immutable** — desired state in Git, change = new commit; (3) **pulled automatically** — an in-cluster agent (Argo CD, Flux) pulls the declared state; (4) **continuously reconciled** — the agent observes actual state and auto-corrects drift. The critical split is **push CI vs pull CD**: CI builds/tests and writes a new image tag into the *config repo*; the in-cluster agent notices and applies it. CI never holds cluster credentials (attack surface shrinks), drift from manual `kubectl edit` is detected and auto-reverted, rollback is `git revert`, and the audit trail is the Git log. Making CI `kubectl apply` directly is the #1 GitOps mistake — "push CD wearing a GitOps costume."
- Sources:
  - Four principles + push/pull split + drift/self-heal: https://github.com/cloud-byte-consulting/agentic-harness/blob/HEAD/pod-bundle/router-skills/team-alpha/kubernetes-gitops-cicd/SKILL.md (accessed 2026-10-05)
  - Principles table + GitOps-vs-traditional comparison: https://github.com/tibsfox/gsd-skill-creator/blob/HEAD/examples/skills/patterns/gitops-patterns/SKILL.md (accessed 2026-10-05)
  - Promotion is Git-mediated (PR moves image tag dev→staging→prod); secrets via External Secrets Operator/Sealed Secrets, never plaintext: https://github.com/nguyen-mau-anh/springboot-kb/blob/HEAD/09.deployment/16-gitops-argocd.md (accessed 2026-10-05)
  - Landscape: Argo CD (CNCF graduated, v3.x, app-centric UI, ApplicationSet generators incl. PR preview envs); Flux (CNCF graduated, v2.9, modular controllers): https://github.com/quad4-software/ai/blob/HEAD/.agents/skills/devops/SKILL.md (accessed 2026-10-05)

### 2.2 Feature flags — lineage and practice

**Mechanism & lineage.** The core decoupling: *deploying code ≠ releasing features*. Ship code dark behind flags; QA/product test in the live environment on internal accounts; canary 1% → 5% → 100%; instant flag-off on error spikes — no emergency rollbacks, no restarts. Verified lineage: the 2009 Flickr "Flipping Out" post (code.flickr.net, 2 Dec 2009) introduced "flags" and "flippers" for gradual feature rollout; Facebook Chat's 2008 engineering post (Eugene Letuchy, engineering.fb.com, 13 May 2008) introduced the term "dark launch" — the commonly-stated "dark launch at Flickr" attribution is a misattribution, corrected via contemporaneous primaries. LaunchDarkly (2014) productized the practice into the enterprise category leader (~$10/seat/month Foundation plan); self-hosted ₹0 options are Unleash (open-source, self-hostable, free) and Flipt (open-source, self-hosted, single binary).

Flag hygiene (the discipline that makes flags work): flag *types* with lifetimes — **release flags** are temporary and must be removed within ~2 sprints of full release (a flag active >30 days is tech debt); **ops flags** (kill switches) are permanent — every feature with external dependencies (Jev, PagerDuty) deserves one; **experiment flags** carry a defined duration; **permission flags** become part of the licensing model. Naming convention `<type>.<domain>.<feature>` (e.g. `ops.payments.stripe-integration`).
- Sources:
  - Attribution corrections (Flickr "flags"/"flippers" vs Facebook Chat "dark launch"): https://github.com/nilesuan/pdlc/blob/HEAD/research/README.md (accessed 2026-10-05)
  - Pricing/options (LaunchDarkly ~$10/seat/mo Foundation; Unleash self-hosted free; Flipt self-hosted): https://launchbuff.com/blog/best-feature-flag-tools-saas-2026 (updated ~2026-09-28, accessed 2026-10-05); https://stackshare.io/stackups/flipt-vs-launchdarkly (accessed 2026-10-05)
  - Flag types, lifetimes, naming convention: https://github.com/mattccc/claude-skills/blob/HEAD/.claude/skills/feature-management/SKILL.md (accessed 2026-10-05)
  - LaunchDarkly's "feature flags for beginners" (deploy≠release; eliminate long-lived branches): https://go.launchdarkly.com/rs/850-KKH-319/images/feature-flags-for-beginners-launchdarkly.pdf?version=0 (accessed 2026-10-05)
  - Linear "Build and Polish" — merge rough versions behind employee-only flags → private beta with 3–10 opt-in companies on "janky" early versions → design polish before general release; quality refined *after* utility is proven, never blocking ship speed: `~/workspace/skills/principal-governance/SKILL.md` §4 (loaded 2026-10-05)

### 2.3 Canary / blue-green as practiced

**Mechanism.** Three strategies, one decision rule:
- **Rolling** — the default for most stateless services; simplest, no extra infra.
- **Blue-green** — two full environments; traffic switches at once after testing; rollback = switch back. Costs double infrastructure during transition; justified where "all traffic hits untested new code simultaneously with no controlled ramp" is unacceptable.
- **Canary** — small % of real traffic to the new version first, ramping as confidence builds. The production-grade form is **automated canary analysis** (Argo Rollouts, Flagger): the controller compares the canary's real metrics (error rate, latency — the RED metrics) against the baseline and **auto-promotes or auto-rolls back** based on whether the canary is actually healthier — turning "does this look okay" from a manual judgment call into an objective metric-driven gate. Typical Argo Rollouts pattern: 10% → pause 5m → metric analysis → 50% → pause 10m → full; on breach, traffic returns to the stable ReplicaSet automatically.
- Sources:
  - Strategy choice + automated analysis mechanism + blue-green cost: https://github.com/vidhya101/k8s-ai-operator/blob/HEAD/.claude/skills/deployment-strategies/SKILL.md (accessed 2026-10-05)
  - Argo Rollouts canary + blue-green worked example (install, trafficRouting, providers, CLI): https://github.com/anikkhob/argocd-notes/blob/HEAD/10_argo_rollouts/README.md (accessed 2026-10-05)
  - Real-world Argo CD + Rollouts flow with auto-rollback and AnalysisTemplates on Prometheus: https://github.com/rohit-1920/argocd (accessed 2026-10-05); https://x-cmd.com/install/argo-rollouts/ (accessed 2026-10-05)

**Relevance to Sentinel — the honest 80% without Kubernetes or spend.** Sentinel runs on systemd, not K8s, so Argo Rollouts is not the tool; the *mechanisms* are portable:
- ✅ **Already covered:** code-deploy ≠ feature-release decoupling (`ops/devops-foundation.md` §2), `flags.json` contract with flag values as reversible Type-2 changes, `global_kill_switch` as the ops flag, and ADR-022's **canary-as-shadow-diff** (candidate runs ≥7 days on `staging-shadow` with a signed disposition diff) — this is the canary *mechanism* (real-traffic evidence before promotion) reimplemented without traffic-splitting.
- ❌ **Gaps:**
  1. **No automated promote/rollback gate on metrics.** Shadow-diff sign-off is manual (two human signatures). The 80%: a script that computes the diff (decision counts, suppression rate, latency p99, forward failures) from the event log and returns PASS/FAIL against thresholds — the metric-driven gate from automated canary analysis, without the Kubernetes machinery.
  2. **No blue-green for the receiver.** Current upgrade path is `git pull && systemctl restart` (docs/deploy-production.md §8) — a restart is a brief blind window for a paging pipeline. The 80%: two systemd units (`sentinel-receiver-a/b`) with the webhook proxy (Caddy) flipping between ports after the new unit passes `/healthz` — blue-green on a single box, seconds to roll back, no Kubernetes.
  3. **Flag lifecycle discipline is unwritten.** `flags.json` exists; the "release flags must die within 2 sprints" rule (from §2.2) is not codified — flag sprawl is the known decay mode.

---

## 3. Observability — metrics/logs/traces, OpenTelemetry, SLOs + error budgets

### 3.1 The three signals and OpenTelemetry

**Mechanism.** Three complementary signals, one diagnostic loop:
- **Traces** — the *map of the trip*: which components a request visited, in what order, how long each stop took. Answers "where did time go?" (tail latency, hidden dependencies).
- **Metrics** — the *dashboard gauges*: counters/histograms aggregated over many requests (rate, error ratio, p99 latency). Great for alerting; useless for explaining one specific request.
- **Logs** — the *notes scribbled along the way*: discrete events with detail.

The real power is **correlation**: metrics *detect* the issue → traces *locate* it → logs *explain* it. Without trace IDs in logs and shared resource identity, three tools are three silos, not observability.

**OpenTelemetry** (CNCF) is the vendor-neutral instrumentation standard: one API, one SDK, one wire protocol (OTLP). It emerged from the OpenCensus + OpenTracing merger and is the second-most-active CNCF project after Kubernetes. The design separates **API** (what app code calls) → **SDK** (the implementation) → **exporter** (transport to a backend) — so switching backends (Prometheus/Grafana Tempo → Datadog → Honeycomb) is a *configuration* change, not a re-instrumentation. The Collector is a standalone process that receives, processes, and routes telemetry. In Python: `opentelemetry-sdk` + auto-instrumentation (`opentelemetry-instrumentation-*`) for spans, OTLP export, and structured logs (e.g. `structlog`) enriched with `trace_id`/`span_id`/`request_id` on every line.
- Sources:
  - Three-signals plain-language model (trip map / gauges / notes): https://github.com/duynhlab/homelab/blob/HEAD/docs/observability/opentelemetry.md (accessed 2026-10-05)
  - Correlation triangle (metrics detect → traces locate → logs explain): https://medium.com/@jothiprakash888/observability-in-distributed-systems-logs-metrics-and-tracing-d34631170305 (published Dec 2025, accessed 2026-10-05)
  - OTel architecture (API/SDK/exporter separation, merger lineage, 2nd-most-active CNCF, "instrument once, export anywhere"): https://github.com/igalhub/devops-study-hub/blob/HEAD/content/monitoring/opentelemetry.md (accessed 2026-10-05)
  - Python reference implementation (auto-instrumentation, Collector, Prometheus exporter, structured JSON logs with trace correlation, per-request request_id via context vars): https://github.com/vinicius-leon/digital-institute/blob/HEAD/docs/02-architecture/decisions/ADR-022-opentelemetry-as-instrumentation-standard.md (accessed 2026-10-05)
  - Anti-patterns (logging everything, no trace IDs in logs, dashboards without alerts, alerts without runbooks, high-cardinality metric misuse): https://medium.com/@jothiprakash888/observability-in-distributed-systems-logs-metrics-and-tracing-d34631170305 (accessed 2026-10-05)

### 3.2 SLOs and error budgets (Google SRE)

**Mechanism.** Three precise terms: **SLI** (a measured number — e.g. fraction of pages forwarded successfully within budget), **SLO** (the target for an SLI — "99.9% over 30 days"), **SLA** (a contract with consequences; the SLO should be *stricter* than the SLA). The **error budget** = 1 − SLO: the amount of unreliability *allowed* per period. 99.9% over 30 days = 43.2 minutes of full outage allowed. The budget is a release-policy mechanism: budget remaining → ship features, take risks; budget exhausted → freeze non-critical changes and work on reliability. This makes product development *self-policing* — teams manage their own risk — but it only works if the reliability side has **the authority to actually stop launches**. Alerting should be on **budget burn rate**, not raw error spikes (SRE workbook multi-window pattern for a 30-day SLO: page at 14.4× burn (1h long / 5m short window, 2% budget in 1h), page at 6× burn (6h/30m, 5% in 6h), ticket at 1× (3d/6h, 10% in 3d)) — fast detection of large incidents, slow detection of chronic ones, few false pages.
- Sources:
  - Error budget mechanism verbatim from the SRE book ("Embracing Risk" — 99.999% SLO → 0.001% failure-rate budget; self-policing; requires launch-stopping authority): https://sre.google/sre-book/embracing-risk/ (accessed 2026-10-05)
  - SLI/SLO/SLA vocabulary + budget arithmetic (43.2 min/30d at 99.9%): https://github.com/isayanpal/last-minute-notes/blob/HEAD/fumadocs-site/content/docs/system-design/hld/reliability-and-operations.md (accessed 2026-10-05)
  - Multi-window burn-rate alert table (14.4×/6×/1×): https://github.com/isayanpal/last-minute-notes/blob/HEAD/fumadocs-site/content/docs/system-design/hld/reliability-and-operations.md (accessed 2026-10-05), citing the Google SRE workbook

### 3.3 What "observable" concretely requires of each Sentinel layer

Mapping the three signals to the pipeline (receiver → correlator → policy gate/Jev → durable forwarder → event log → platform API + UI):

| Layer | Metrics (alert) | Logs (explain) | Traces (locate) |
|---|---|---|---|
| Receiver (`:8080`) | webhook rate, auth failures, queue depth | one JSON line per webhook w/ request ID | trace per alert: hook → decision → forward |
| Correlator | decisions/sec, dedup ratio, storm detections | decision + disposition + policy version | span: correlator window |
| Policy gate / Jev race | gate latency histogram (p50/p99 vs 2700ms budget), judge timeouts, fallback rate | gate outcome + winner + evidence refs | span: Jev call with timeout attr |
| Durable forwarder | forward success/failure rate, retry counts, PagerDuty latency | `FORWARD FAILED` w/ decision ID (already exists) | span: forwarder attempt |
| Platform API (`:8081`) | API latency, error rate, console reads | request log w/ request ID | propagate trace from UI |
| Event log | chain-append rate, `BROKEN: checkpoint` count | tamper events (already exists) | — |

**Relevance to Sentinel.**
- ✅ **Covered:** `/livez` (shallow) + `/healthz` (deep, bearer) endpoints; `journalctl` log lines (`FORWARD FAILED`, `BROKEN: checkpoint`); shadow tap freshness metric `tap_lag_s` (docs/deploy-production.md §6–7; ops/devops-foundation.md).
- ❌ **Gaps (the honest 80%, all stdlib-possible, ₹0):**
  1. **No metrics endpoint.** A `/metrics` route emitting Prometheus text format (counters + latency histograms) costs ~50 lines of stdlib and unlocks everything downstream (burn-rate math, canary auto-gates, dashboards). This is the single highest-leverage observability gap.
  2. **No correlation ID across layers.** The governance checklist already demands "one Trace-ID per user interaction"; Sentinel's webhook → decision → forward chain has no shared request/trace ID in logs. Add one UUID at ingress, propagate through correlator/gate/forwarder, include it in every log line and in the hash-chained event log.
  3. **No SLO/error budget written down.** Sentinel has a 2700ms gate budget (a latency SLO fragment) but no availability/error budget for the paging pipeline. Write the first one: e.g. "99.9% of real pages forwarded successfully within 30 days" — it becomes the release-policy governor (burned budget → feature freeze) and the paging-policy for the external watcher.
  4. **No burn-rate alerting.** The external watcher is poll-based (`/livez` pings). Add budget-burn alerts on the metrics endpoint.

---

## 4. Environment strategy — parity, preview environments, and what breaks when staging lies

### 4.1 Dev/prod parity (12-factor, factor X)

**Mechanism.** The Twelve-Factor App (Heroku, 2011) — still the portability baseline that Kubernetes documentation assumes: (I) one codebase, many deploys; (II) explicit isolated dependencies; (III) config in environment, never code; (IV) backing services as attached resources; (V) **build, release, run as strictly separate stages** — the artifact built is the artifact that runs in prod, never re-built per env; (VI–VIII) stateless processes, port binding, process-model concurrency; (IX) disposability — fast startup, graceful SIGTERM handling (the foundation of rolling deploys); (X) **dev/prod parity** — same backing-service types, same OS where possible; the classic violation is `if env == "prod": useRDS() else: useSQLite()`; (XI) logs as event streams to stdout, never managed logfiles; (XII) admin processes as one-off processes in the same release artifact. Factor X names three gaps to close: **time** (code written → deployed in hours), **personnel** (authors deploy and watch it), **tools** (dev and prod as similar as possible). Parity is what makes staging evidence *transferable* to production claims.
- Sources:
  - Factor list + build/release/run + disposability-as-foundation: https://github.com/bachdx2812/dev-learning-hub/blob/HEAD/system-design/part-5-modern-mastery/ch23-cloud-native-serverless.md (accessed 2026-10-05)
  - Violation examples per factor (`if env == "prod"` pattern; SQLite→Postgres swap): https://github.com/frogoai/arcdlc/blob/HEAD/skills/source-map/source/Twelve-Factor%20App.md (accessed 2026-10-05)
  - Time/personnel/tools gaps + logs-as-event-streams: https://gist.github.com/anandtripathi5/118995139602599dab64fddcd147545a (accessed 2026-10-05)
  - Parity as drift-to-be-audited, build-once-promote, ephemeral envs with auto-teardown: https://github.com/syntropic137/harness-app-template/blob/HEAD/.claude/skills/environments/SKILL.md (accessed 2026-10-05)

### 4.2 Preview environments

**Mechanism.** Per-PR ephemeral environments: a PR gets a live URL with its own app + database + config, converged from the same definitions as production, torn down automatically on PR close (Vercel Preview Deployments, Heroku Review Apps, Argo CD ApplicationSet generators are the platform instances). What actually breaks, documented from practice: (1) **shared staging database** — two PRs land migrations in different orders and the third developer gets missing-relation errors from a branch that never ran their migration; (2) **connection exhaustion** — previews multiply *idle* connections, surfacing pooling problems first; (3) **forward-only migrations** — the runner won't un-apply a rewritten migration; recreate rather than reuse; (4) **seed scripts assuming an empty DB** — write every seed as an upsert; (5) **secret inheritance** — preview builds inherit project env vars by default, which is how a preview ends up holding a live payment key: give previews their own secret set, test-mode credentials only. Cost discipline: TTLs, auto-sleep, right-sizing, per-PR eligibility — "unlimited previews are an unlimited budget line item with no owner."
- Sources:
  - Five failure modes of preview envs (shared DB, connections, migrations, seeds, secrets): http://dev.to/libme/preview-environments-per-pull-request-how-to-seed-them-without-cloning-production-1f08 (published ~2026-09-27, accessed 2026-10-05)
  - Cost-control operating model (TTLs, auto-sleep, orphaned-env cleanup): https://www.bibra.dev/en/blog/preview-environments-cost-control (accessed 2026-10-05)
  - Ephemeral PR previews pattern (isolated env per PR, auto-destroy on close): https://github.com/anton-codes-iac/deploy-stack/blob/HEAD/docs/guides/ephemeral-pr-previews.md (accessed 2026-10-05)

### 4.3 What breaks when staging lies

The documented failure mode: a "staging" environment holding **real customer data**, listening on debug ports, reachable through forgotten DNS — "an unmonitored production wearing a costume." The enforceable rule for small teams: **"nothing real lives in a place with pretend rules"** — full parity is unaffordable, but *classification* is free. Synthetic data carries a realism tax (some bugs only bite real-shaped data — the 400-char name, the emoji in the address line); the honest mitigation is a smaller, weirder synthetic set plus occasional scrubbed real samples, reviewed like code.
- Source: https://dev.to/dhruv_malaviya_cdcc71e595/staging-went-down-last-month-our-customers-tweeted-about-it-4hfj (published ~2026-09-27, accessed 2026-10-05)

**Relevance to Sentinel.**
- ✅ **Covered (and this is where Sentinel is ahead of many small teams):** the four-tier strategy (`local` / `staging-shadow` / `staging-lab` / `prod`) with the **never-pages rule** (startup refuses a prod routing key in shadow tier; staging-lab points at stub vendors) — this is the "classification" discipline from §4.3 implemented as code, and it directly answers "what breaks when staging lies" (a staging box that can page *is* a second production). The shadow tap's full-fidelity copy + `tap_lag_s` freshness metric is the realism-tax answer: prove on real traffic *shape* without touching real paging.
- ❌ **Gaps:**
  1. **No parity audit.** dev/prod parity is asserted, not measured. The 80%: a script that diffs the prod box against the repo (pinned SHA? config schema generation? `requirements.txt` vs installed? Caddyfile vs committed template?) and reports drift — the GitOps "continuous reconciliation" mechanism without Argo CD (see §2.1).
  2. **Config drift on `/var/lib/sentinel/config/`** — thresholds/allowlist/flags are edited on the box, not versioned. The 80%: keep `/etc/sentinel` + `/var/lib/sentinel/config` as a Git repo (or a checked-in snapshot per release) so every production config change is a commit — this is GitOps principle (2) "versioned & immutable: change = new commit" applied at the box level, ₹0.
  3. **Preview environments: correctly deprioritized.** With a static console on GitHub Pages, Vercel-style preview deploys are free *for the console* — but the receiver/platform are single-box systemd services where per-PR envs buy little until contributor load grows. Honest ranking: below the other checklist items.

---

## 5. SRE practices — toil budgets, on-call discipline, paging standards

### 5.1 Toil

**Mechanism.** Google SRE defines **toil** as work that is *manual, repetitive, automatable, tactical, devoid of enduring value*, and scales O(n) with service growth — e.g. restarting crashed services by hand, clearing logs without fixing rotation, answering the same alert repeatedly, manual scaling/provisioning. Non-toil: building monitoring, writing automation, architecture improvement, capacity planning. The hard cap: **toil ≤ 50% of SRE time, with ≥50% to engineering work; on-call ≤ 25%**. If operational load exceeds the cap, the SRE team and leadership must put concrete objectives in quarterly planning to return to sustainable levels — the cap is enforced by *renegotiating* on-call responsibilities (even handing the pager back to developers) until the service is in shape. Google's quarterly surveys show average toil ~33% — the 50% is a ceiling, not a target. Measurable via: % of time on repetitive ops, repeat-ticket volume, alert-noise ratio (total vs actionable alerts), incident recurrence rate.
- Sources:
  - Toil definition + ≤50% cap + on-call ≤25% + "hand the pager back" enforcement: `~/workspace/skills/execution-doctrine/SKILL.md` §8 (loaded 2026-10-05); `~/workspace/skills/principal-systems/SKILL.md` (loaded 2026-10-05)
  - Toil Rule + measurement methods (time %, ticket volume, alert noise ratio, recurrence rate) + real-life log-rotation example: https://medium.com/@manojkumar024/how-google-sre-defines-toil-2b259d0c1b41 (published Feb 2026, accessed 2026-10-05)
  - SRE timeline / "hope is not a strategy" + error-budget framing: https://notes.kodekloud.com/docs/Fundamentals-of-SRE/Fundamentals-of-SRE/The-Origin-and-Evolution-of-SRE/page (accessed 2026-10-05)

### 5.2 On-call discipline and paging standards

**Mechanism.** From the Google SRE workbook ("Being On-Call"):
- **Every page must be immediately actionable**: there must be an action the human is expected to take *immediately* that the system cannot take itself. High signal-to-noise; false positives breed alert fatigue, and alert fatigue is how real pages get ignored.
- **Paging targets**: ~5 minutes response for user-facing/time-critical services, ~30 minutes for less critical. Median paging should be **0** — a component paging daily means something else is about to break on top of it.
- **Sustainable load**: max ~2 distinct incidents per 12-hour shift; ~6 hours average per incident end-to-end → ~2 incidents/shift is the sustainable ceiling.
- **SLO-based paging**: page when the error budget burns (see §3.2), not on raw thresholds; alert thresholds aligned to symptoms that threaten SLOs. If paging is frequent, the response is to fix the alerting or the service — "relaxing alert thresholds is rarely an appropriate response to being paged."
- **Operational overload is measurable** (tickets/shift, pages/shift) and triggers the renegotiation above; operational *underload* is also a problem (skills atrophy).
- Sources:
  - Actionable-alert standard + 2-incidents/shift + SLO-based paging + drain-requests: https://sre.google/workbook/on-call/?hl=ko (accessed 2026-10-05)
  - 5-min/30-min targets, median-zero paging, operational overload/underload, quantifiable goals: https://storage.googleapis.com/gweb-research2023-media/pubtools/pdf/44813.pdf ("Being an On-Call Engineer: A Google SRE Perspective," *;login:* Oct 2015, accessed 2026-10-05)
  - Toil time allocation + incident stats (median incident 48 min: 15 context + 20 troubleshoot + 13 docs): https://www.webpronews.com/sre-teams-hand-over-the-pager-how-ai-agents-cut-toil-and-reshape-on-call/ (published ~Aug 2026, accessed 2026-10-05, citing Google SRE book figures)

**Relevance to Sentinel — the sharpest critique.** Sentinel is a *paging pipeline*; it pages on behalf of other people's on-call. That makes its own on-call story Type-1 important:
- ✅ **Covered:** external watcher concept (pre-mortem: "Sentinel down is a SEV1 on a path that doesn't traverse Sentinel"), named-owner requirement, weekly canary ping, dead-man's-switch design, secondary escalation with `require_drilled_secondary` and human-ack ≤10 min, runbooks RB-4..RB-7 with drills (ops/devops-foundation.md).
- ❌ **Gaps:**
  1. **No written paging policy for Sentinel itself.** What is a page vs a ticket *for the Sentinel operator*? The 80%: classify the existing signals — `BROKEN: checkpoint` (event-log tamper) = page immediately; `FORWARD FAILED` = page (a page that didn't reach PagerDuty is a missed-page incident); `webhook_auth_fail_open=true` = ticket (degraded security posture, not user impact); disk-guard watermark = ticket escalating to page. Every alert gets a runbook link (the governance "alerts without runbooks" anti-pattern).
  2. **No toil accounting.** Sentinel's ops toil is currently invisible (manual upgrades via `git pull && systemctl restart`, manual shadow-diff review, manual drill scheduling). The 80%: a quarterly 2-hour toil audit — list repetitive ops tasks, pick the top one, automate it. First candidate: the upgrade path (script the SHA-pin → pull → health-gated restart; see §2.3 blue-green note).
  3. **Blameless postmortem template missing from the repo.** Google SRE's template (Summary → Impact → Root Causes → Trigger → Resolution → Detection → Action Items (owner, priority, tracking) → Lessons (what went well / wrong / where we got lucky) → Timeline) is free and is the culture mechanism; `ops/` has runbooks but no postmortem template. The principal-systems skill mandates blameless postmortems within 72h — the template is the precondition.

---

## 6. DevOps checklist for a serious small system — ranked by value-per-effort (₹0)

Ranked by (reliability value × irreversibility of getting it wrong) ÷ effort. All items are ₹0 and comply with the no-GitHub-Actions order.

| # | Item | Mechanism (why) | Effort | Status in Sentinel |
|---|---|---|---|---|
| 1 | **`/metrics` endpoint (Prometheus text format, stdlib)** | Unlocks burn-rate math, canary auto-gates, dashboards; the foundation every later item builds on | ~50 lines | ❌ Gap |
| 2 | **Correlation/trace ID from webhook → decision → forward → event log** | Turns three silos into one diagnostic story; the governance checklist already demands it | Small (one UUID at ingress, propagate) | ❌ Gap |
| 3 | **Cron post-merge runner on a clean worktree** | Catches the two things the local gate structurally cannot: post-merge integration effects + clean-environment reproducibility (§1.3) | Small (systemd timer + script) | ❌ Gap |
| 4 | **Write the first SLO + error budget** (e.g. 99.9% pages forwarded / 30d; 2700ms gate already exists as a latency SLO fragment) | Converts reliability from hope to release policy; self-policing; governs the watcher | A decision doc | ❌ Gap |
| 5 | **Classify every alert: page vs ticket + runbook link** | Every page actionable; kills alert fatigue before it starts; makes the watcher a system | A table + links | ❌ Gap |
| 6 | **Blue-green receiver on one box** (two systemd units, Caddy flips ports after `/healthz`) | Eliminates the restart blind window in the paging path; rollback in seconds | Small (second unit + proxy rule) | ❌ Gap |
| 7 | **Shadow-diff auto-gate script** (PASS/FAIL vs thresholds on decision/latency/forward metrics) | Automated canary analysis (§2.3) without K8s; makes ADR-022's sign-off objective | Small | ❌ Gap |
| 8 | **Config-as-commits** (`/var/lib/sentinel/config` + `/etc/sentinel` versioned in Git) | GitOps principle (2) at box level: every prod config change is a commit; audit trail + `git revert` rollback | Tiny (init repo, hook or cron commit) | ❌ Gap |
| 9 | **Parity/drift audit script** (SHA pinned? deps match? Caddyfile matches template? config schema valid?) | "Continuously reconciled" without Argo CD; catches the silent drift that kills | Small | ❌ Gap |
| 10 | **Postmortem template in `ops/`** (Google SRE format + "where we got lucky") | The culture mechanism; precondition for the 72h blameless rule | Tiny (one MD file) | ❌ Gap |
| 11 | **Pre-push hook: fast subset of the gate** | Shifts the test filter to zero-effort; complements the cron runner | Tiny | ❌ Gap |
| 12 | **Kill-switch monthly drill** (already designed) — execute and log it | A kill switch never drilled is a rumor (pre-mortem #3 in devops-foundation) | Recurring 15 min | ⚠️ Designed, verify cadence |
| 13 | **Flag lifecycle rule** (release flags die ≤2 sprints; kill-switch flags permanent) | Prevents flag sprawl, the known decay mode of §2.2 | A paragraph in the flags contract | ❌ Gap |
| 14 | **Quarterly toil audit** (list repetitive ops, automate the top one) | The toil cap (§5.1) is a ceiling enforced by measurement | 2 hours/quarter | ❌ Gap |
| 15 | **SQLite + state-dir backup/restore drill** | Eternal friction: disks die; the hash-chained log is worthless without a tested restore | Small (script + one drill) | ❌ Gap |
| 16 | **Burn-rate alerts on the watcher** (page 14.4×, ticket 1×) | SLO-based paging (§3.2); replaces raw threshold polling | Small, after #1 | ❌ Gap |
| 17 | Preview environments for PRs | Real value only when contributor load grows; static console already gets free previews via Pages | Deferred | — |

**What NOT to do (negative checklist):** don't adopt Kubernetes/Argo CD for a two-process Python system (the mechanisms port; the machinery doesn't); don't buy LaunchDarkly when `flags.json` + the lifecycle rule is the 80%; don't chase DORA elite deploy-frequency as a vanity metric — for a paging pipeline, *change failure rate* and *recovery time* are the metrics that matter, frequency is the proxy that decouples (§1.1).

---

## 7. Relevance-to-Sentinel: the honest gap list

Condensed, no duplicates of `ops/devops-foundation.md`:

1. **No metrics endpoint** — the single highest-value gap; unlocks SLOs, burn-rate alerts, canary auto-gates.
2. **No cross-layer correlation ID** — webhook → decision → forward is currently three unlinkable log streams.
3. **No post-merge verification** — parallel lanes can merge green branches into a broken trunk; the cron clean-worktree runner closes it within the standing order.
4. **No written SLO/error budget** — the 2700ms gate budget is a fragment, not a policy.
5. **Restart-based upgrades** (`git pull && systemctl restart`) — a blind window in the paging path; single-box blue-green fixes it.
6. **Manual shadow-diff sign-off** — automate the PASS/FAIL computation (humans still sign, but on numbers).
7. **Unversioned production config** — config-as-commits is GitOps at ₹0.
8. **No parity/drift audit** — parity is asserted, never measured.
9. **No page-vs-ticket classification** for Sentinel's own signals.
10. **No postmortem template** in `ops/`.
11. **No toil accounting** — the upgrade path is the first toil candidate.
12. **Flag lifecycle rule unwritten** — release flags need a death date.

## 8. Sources

Primary and secondary sources cited above, all accessed 2026-10-05 unless noted:

- Google SRE book, "Embracing Risk" (error budgets): https://sre.google/sre-book/embracing-risk/
- Google SRE workbook, "Being On-Call": https://sre.google/workbook/on-call/?hl=ko
- "Being an On-Call Engineer: A Google SRE Perspective," *;login:* Oct 2015: https://storage.googleapis.com/gweb-research2023-media/pubtools/pdf/44813.pdf
- DORA/Accelerate book overview (23,000+ responses, no speed–stability tradeoff): https://github.com/joellarson/knowledge/blob/HEAD/topics/test-driven-development/wiki/sources/accelerate-forsgren-humble-kim.md
- DORA four-metrics reading ("designed to be read together"): https://github.com/edytakucharska/keel/blob/HEAD/frameworks/accelerate-four.md
- DORA 2021 benchmarks (elite thresholds): https://github.com/trac41799/strata-knowledge/blob/HEAD/evidence/records/S-0163.md
- DORA 2021 "2.3× trunk-based" finding: https://launchdarkly.com/blog/elite-performance-with-trunk-based-development/
- DORA 2024/2025 throughput–stability decoupling (secondary synthesis): https://github.com/tjboudreaux/ready-agent-1/blob/HEAD/references/dora-crosswalk.md
- Pre-merge vs post-merge testing (integration effects): https://github.com/bdfinst/cd-migration/blob/HEAD/content/en/docs/testing/_index.md
- CI value (clean checkout, blame attribution): https://dev.to/how-to-dev/continuous-integration-ci-and-how-it-can-help-you-m5k
- CI gate order + feedback loop: https://github.com/haiduongdarkocean/cell/blob/HEAD/.agents/skills/ci-cd-and-automation/SKILL.md
- GitOps principles + push/pull split: https://github.com/cloud-byte-consulting/agentic-harness/blob/HEAD/pod-bundle/router-skills/team-alpha/kubernetes-gitops-cicd/SKILL.md
- GitOps principles table: https://github.com/tibsfox/gsd-skill-creator/blob/HEAD/examples/skills/patterns/gitops-patterns/SKILL.md
- Argo CD/Flux promotion + secrets: https://github.com/nguyen-mau-anh/springboot-kb/blob/HEAD/09.deployment/16-gitops-argocd.md
- Argo CD v3.x / Flux v2.9 landscape: https://github.com/quad4-software/ai/blob/HEAD/.agents/skills/devops/SKILL.md
- Feature-flag lineage corrections (Flickr flags/flippers; Facebook Chat dark launch): https://github.com/nilesuan/pdlc/blob/HEAD/research/README.md
- Feature-flag tools/pricing (LaunchDarkly, Unleash, Flipt): https://launchbuff.com/blog/best-feature-flag-tools-saas-2026 ; https://stackshare.io/stackups/flipt-vs-launchdarkly
- Flag types/lifetimes/naming: https://github.com/mattccc/claude-skills/blob/HEAD/.claude/skills/feature-management/SKILL.md
- LaunchDarkly deploy-vs-release primer: https://go.launchdarkly.com/rs/850-KKH-319/images/feature-flags-for-beginners-launchdarkly.pdf?version=0
- Canary/blue-green strategies + automated analysis: https://github.com/vidhya101/k8s-ai-operator/blob/HEAD/.claude/skills/deployment-strategies/SKILL.md
- Argo Rollouts practice: https://github.com/anikkhob/argocd-notes/blob/HEAD/10_argo_rollouts/README.md ; https://github.com/rohit-1920/argocd ; https://x-cmd.com/install/argo-rollouts/
- Three signals (traces/metrics/logs): https://github.com/duynhlab/homelab/blob/HEAD/docs/observability/opentelemetry.md
- Correlation triangle + observability anti-patterns: https://medium.com/@jothiprakash888/observability-in-distributed-systems-logs-metrics-and-tracing-d34631170305 (Dec 2025)
- OpenTelemetry architecture/lineage: https://github.com/igalhub/devops-study-hub/blob/HEAD/content/monitoring/opentelemetry.md
- Python OTel reference (auto-instrumentation, Collector, structured logs): https://github.com/vinicius-leon/digital-institute/blob/HEAD/docs/02-architecture/decisions/ADR-022-opentelemetry-as-instrumentation-standard.md
- SLI/SLO/SLA + burn-rate alert table: https://github.com/isayanpal/last-minute-notes/blob/HEAD/fumadocs-site/content/docs/system-design/hld/reliability-and-operations.md
- 12-factor factors: https://github.com/bachdx2812/dev-learning-hub/blob/HEAD/system-design/part-5-modern-mastery/ch23-cloud-native-serverless.md ; violations: https://github.com/frogoai/arcdlc/blob/HEAD/skills/source-map/source/Twelve-Factor%20App.md ; time/personnel/tools gaps: https://gist.github.com/anandtripathi5/118995139602599dab64fddcd147545a
- Environments/principles (parity audits, preview auto-teardown): https://github.com/syntropic137/harness-app-template/blob/HEAD/.claude/skills/environments/SKILL.md
- Preview-env failure modes: http://dev.to/libme/preview-environments-per-pull-request-how-to-seed-them-without-cloning-production-1f08
- Preview cost control: https://www.bibra.dev/en/blog/preview-environments-cost-control
- Ephemeral PR previews: https://github.com/anton-codes-iac/deploy-stack/blob/HEAD/docs/guides/ephemeral-pr-previews.md
- "Staging went down" / staging-lies classification rule: https://dev.to/dhruv_malaviya_cdcc71e595/staging-went-down-last-month-our-customers-tweeted-about-it-4hfj
- Toil definition/measurement: https://medium.com/@manojkumar024/how-google-sre-defines-toil-2b259d0c1b41 (Feb 2026)
- SRE origin/evolution ("hope is not a strategy"): https://notes.kodekloud.com/docs/Fundamentals-of-SRE/Fundamentals-of-SRE/The-Origin-and-Evolution-of-SRE/page
- Toil allocation + incident stats: https://www.webpronews.com/sre-teams-hand-over-the-pager-how-ai-agents-cut-toil-and-reshape-on-call/ (Aug 2026)
- Woodpecker CI (self-hosted, ₹0 option if the CI ban is revisited): https://prompts.brightcoding.dev/blog/woodpecker-cicd-why-teams-are-ditching-jenkins-for-this-lightweight-beast ; https://opensourcealternative.to/project/woodpecker

**Prior Sentinel notes consulted (not duplicated):** `ops/devops-foundation.md` (environment tiers, release process, runbooks, pre-mortem), `docs/deploy-production.md` (systemd/Caddy deploy, upgrade path, health endpoints), `research/sre-field.md` (SRE field craft agenda), `ops/ci-repair.md`, `docs/liveness.md`.

*Skill lineage applied:* principal-systems (DORA four-metric balance; error budgets; toil cap; Type-1/2 labeling of tier definitions vs drill cadence), principal-governance (decoupled deployment; Linear Build-and-Polish; unified telemetry trace-ID), principal-mindset (proxy-trap audit on deploy frequency; anti-theater on shadow evidence), execution-doctrine (validate → shadow → canary; toil budget; feedback-loop velocity).
