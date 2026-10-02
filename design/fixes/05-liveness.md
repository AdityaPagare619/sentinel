# 05 — Liveness: The Dead-Receiver Design

**Lane 3 (Tripwire/Forge) · principal fix-design wave · 2026-10-03**
**Implements:** ADR-018 (12-adr-deltas.md) · resolves I-2 (08-pre-mortem.md) ·
synthesis §3.6 · decision-register T1-09
**Status:** DESIGN ONLY. No code. RFC-grade rigor per Law 3 (Type 1).
**Judged against:** `design/principal/00-laws.md` (the seven laws).

---

## 0. The hole, stated once

Every "never drops a page" promise in the frozen spec is conditioned on a
running process. A hand-edited `thresholds.json` with a trailing comma
crash-loops the receiver; under hard cutover ("point integrations at
Sentinel *instead of* PagerDuty") that is connection-refused for every
alert. The SEV1 never enters the system. Fail-open covers triage failure;
nothing covers the absence of triage. (I-2; synthesis §2 hole #5.)

This document is the implementation-ready liveness design: what "healthy"
means, what the alert sources actually do when the receiver is dead (verified
against live vendor docs — this determines the real slack), schema-validated
config that can never crash-loop, the standby fallback, and why cutover waits
for Stage 2c. It closes with a pre-mortem of the liveness story itself.

**Scope boundary.** This design covers *liveness* (dead, unreachable,
crash-looped). It does not cover *correctness-under-liveness* — a healthy
process making wrong decisions. That is ADR-022's (threshold governance) and
the H-2 watchdog's job, and §6.2 names the seam explicitly. A design that
conflates the two will build a watcher that reports "healthy" while the gate
suppresses the world.

---

## 1. Health semantics — what "healthy" means, exactly

### 1.1 The three components, the two probes

Sentinel exposes **two** endpoints, with K8s-conventional semantics:

| Endpoint | Meaning | Who polls it | On failure |
|---|---|---|---|
| `GET /livez` | **Shallow.** The process is alive and answering. No dependencies checked. | Process supervisor (systemd/K8s) | Restart with backoff |
| `GET /healthz` | **Deep.** The process can do its job *right now* (checks §1.2). | External health watcher (§2) | Page + fallback runbook (§4) |

The frozen spec's `GET /healthz → {"ok": true}` (ARCHITECTURE.md §3.9) is
retired. A constant-true health endpoint is a lie detector that never fires —
Law 2's adversary ("the next maintainer is tired") will trust it at 3 AM.

### 1.2 The deep check — exact predicates

`GET /healthz` returns 200 with a JSON body **iff all of the following hold**;
otherwise 503 with `{"ok": false, "failed": [<named predicates>]}`:

1. **`config_current`** — a config generation is loaded, schema-validated
   (§3), and `loaded_at` is newer than the last config-change signal.
   A rejected config (§3.3) does not fail this predicate (last-good is still
   serving) but *does* emit the `config_rejected` event; staleness beyond
   the operator's ack window is the watcher's business, not this predicate's.
2. **`gate_constructed`** — the gate object exists and its last self-test
   (a synthetic alert through correlator→gate→forwarder-dry-run, run at
   startup and every 5 min) passed. Catches the I-2 constructor-throws class
   *before* it becomes a crash loop.
3. **`forwarder_draining`** — outbox lag (oldest undelivered decided page)
   < 60s, and the last synthetic canary (§1.4) was confirmed in PagerDuty
   within the last 15 min. A forwarder that cannot reach PD is unhealthy —
   ADR-012's "no single notification path" is a design promise, and this
   predicate is its tripwire.
4. **`evidence_flowing`** — WAL flusher lag < 30s (I-1's WAL design). Evidence
   loss is a paged event, but a *wedged* flusher degrades every safety
   monitor at once — that is unhealthiness, not degradation.
5. **`no_crashloop_signature`** — process restart counter (kept in a file
   outside the process, owned by the supervisor) has not increased in the
   last 10 min. A process that keeps dying and being restarted is not
   "healthy between restarts"; it is crash-looping.

Response body (200 case) carries the evidence, not just the verdict:

```json
{"ok": true, "config_generation": 42, "gate_selftest_age_s": 137,
 "forwarder_outbox_lag_s": 4, "last_canary_age_s": 212,
 "wal_flusher_lag_s": 1, "restarts_10m": 0}
```

The 503 body names the failed predicates. The watcher pages the *reasons*,
not just "down" — at 3 AM, "forwarder_draining failed, outbox lag 400s"
sends the operator to the right runbook section.

### 1.3 Who probes whom (the probing graph)

```
supervisor ──/livez──▶ receiver ──in-process──▶ gate ──in-process──▶ forwarder
     (restart)            │                          │
                          │ /healthz (deep)          │ synthetic canary
                          ▼                          ▼
                 external watcher ──own PD key──▶ PagerDuty
                          │ heartbeat every 60s
                          ▼
                    dead-man's switch (SaaS)
```

- **Supervisor → receiver `/livez`:** restart on failure, exponential backoff,
  max 5 restarts/10 min then stop trying (a supervisor that restarts forever
  masks the outage from everything keyed on process-down — Law 2).
- **Receiver → gate/forwarder:** in-process. The gate's health is a section
  of the `/healthz` body, not a separate endpoint — one process, one truth.
- **Forwarder → PagerDuty:** synthetic canary alert every 5 min through the
  *real* forwarder path to a dedicated PD test service. This proves the last
  mile, not just TCP reachability. Missing canary > 15 min ⇒ page (the
  "guard's alarm bypasses the guard" shape from the security study).
- **Watcher → receiver `/healthz`:** the external watcher (§2). Poll every
  15s, timeout 5s, from **two vantage points** in different failure domains.
  UNHEALTHY requires 3 consecutive failures on **both** (≤90s detection,
  flap-resistant).

### 1.4 What "healthy" does NOT mean

- A healthy process may still be in **degraded mode** (I-3: Jev circuit open,
  deterministic digest active). Degraded is a *disposition policy*, reported
  in `/healthz` as `"degraded": true` with the reason — the watcher does not
  treat it as UNHEALTHY, because degraded still pages. Conflating degraded
  with down would trigger the fallback runbook during every provider blip.
- A healthy process may still be **wrong** (§6.2 — the named seam).

---

## 2. The external health watcher

### 2.1 Placement — the non-fate-sharing requirement

The watcher **must not fate-share with Sentinel**. Concretely, it must not
share: the host, the AZ/subnet, the config store, the credential store, the
network path's single points, or the deployment pipeline. A watcher deployed
by the same Terraform, onto the same box, reading the same config, is a
second opinion from the same brain.

The design uses **two watchers**, because the watcher is itself a liveness
problem (§6.3 pre-mortems it):

1. **Poller** — a small customer-hosted binary on a *separate* host (different
   failure domain; documented in the deployment guide as a hard requirement,
   not a suggestion). Polls `/healthz` per §1.3. Holds its **own** PagerDuty
   integration key (customer-owned, stored separately from Sentinel's
   credentials — §3.4 explains why the stores are split).
2. **Dead-man's switch** — a third-party SaaS (customer's choice; e.g. the
   healthchecks.io pattern). The poller emits a heartbeat to it every 60s.
   The SaaS pages on missed heartbeat (poller dead) **or** on the poller's
   reported `sentinel_status: UNHEALTHY`.

Each watches Sentinel; the SaaS additionally watches the poller. There is no
third turtle — the SaaS is the trust root, and its failure mode (SaaS down)
is *fail-silent on heartbeats*, which the poller detects as heartbeat-send
failures and reports through its own direct PD key. Belt and suspenders, all
the way down, exactly two levels.

### 2.2 The watcher's alert path

When UNHEALTHY is declared (§1.3):

1. Page the **Sentinel platform admin** via the watcher's own direct-to-PD
   integration key. This path **does not traverse Sentinel** — I-2's
   explicit requirement ("Sentinel down is itself a SEV1 to the platform
   team, with its own paging path that does not traverse Sentinel").
2. The page carries: the failed predicates from the 503 body, both vantage
   points' last observations, and a deep link to the **fallback runbook**
   (§4.4).
3. The watcher does **not** automatically re-point alert sources (decision
   in §4.2 — human executes, drill-proven).

### 2.3 Watcher authentication to `/healthz`

`/healthz` exposes internals (config generation, lag numbers). It is not
public: mTLS or a bearer token, documented in the deployment guide, with the
token stored in the *watcher's* credential store (separate from Sentinel's —
§2.1). An unauthenticated health endpoint is an info leak and a spoofing
surface (an attacker answering "ok: true" for a dead Sentinel is A-1's cousin).

---

## 3. What the alert source does on connection-refused

This section is load-bearing: the liveness design's timing (detection
windows, drill targets, whether a human may stay in the loop) depends on how
much slack the sources actually give us. Verified against live vendor docs
and source, 2026-10-03. Where a vendor documents nothing, that is stated.

### 3.1 Alertmanager → Sentinel (webhook receiver)

Alertmanager's notification pipeline is, per current source
(`notify/notify.go`, `createReceiverStage`):

```
DedupStage → RetryStage → SetNotifiesStage
```

The notifier returns a **verdict** per attempt — `Retry` (worth another
attempt) or `Unrecoverable` — and the `Retrier` classifies:

- **Connection refused / timeout / DNS failure (transport errors): RETRIED.**
  These are network errors; the `Retrier` treats them as retryable and the
  `RetryStage` keeps attempting. The notification is *not* dropped and the
  notification log is *not* written — `SetNotifiesStage` only runs on success.
  (Source: `prometheus/alertmanager` `notify/notify.go` on main; the
  Retry/Unrecoverable verdict split.)
- **5xx from the receiver: RETRIED.** The generic webhook notifier is built
  with a bare `Retrier` — only 5xx is retried. (Field-observed and
  maintainer-confirmed: "The Webex notifier is built with a bare
  `notify.Retrier{}`, so only 5xx is retried,"
  https://github.com/prometheus/alertmanager/pull/5548.)
- **429 / 4xx from the receiver: UNRECOVERABLE — dropped after one attempt.**
  "A 429 is classified unrecoverable and the notification is dropped"
  (same PR); "A group that fires and resolves inside a run of 429s is never
  delivered" (https://github.com/prometheus/alertmanager/pull/5496).
  Field operators have hit exactly this: "the webhook receiver retries 5xx
  but treats 429 as a permanent failure"
  (https://github.com/jdwlabs/apps/commit/82264a624cbb0c6b5e6ca112af2590f022264a73).
- **2xx from the receiver: RETIRED PERMANENTLY.** `SetNotifiesStage` writes
  the notification log; Alertmanager considers the notification delivered and
  will not resend (until `repeat_interval`). A 2xx from a receiver that did
  *not* durably accept the alert is alert loss wearing a success code.

**Design consequences (interface contract on Sentinel's receiver):**

1. **Never return 2xx for an alert not durably accepted.** The 200/202 goes
   out only after the decision event hits the WAL (ADR-011/I-1). This is now
   a release-blocking contract test.
2. **Return 503 on overload, never 429, for alert traffic.** A 429 tells
   Alertmanager to *drop* the alert; a 503 tells it to *retry*. Sentinel's
   backpressure signal must be 503 (Retry-After honored where the source
   supports it — noted: not all senders honor it; AM's generic handling is
   evolving, PR #5497).
3. **Connection-refused gives us minutes, not seconds.** AM holds and retries
   transport failures; the practical slack before alerts are *lost* (not
   delayed) is large — bounded in practice by `repeat_interval`/operator
   patience rather than by a documented drop timer. **This is what buys the
   human-in-the-loop fallback (§4.2):** a 90s detection + 10-min drill-proven
   recovery loses no Alertmanager-sourced alerts, only delays them.

### 3.2 Generic clients → Sentinel (PD Events API v2-compatible receiver)

PagerDuty's official Events API v2 docs publish the retry contract
(https://developer.pagerduty.com/docs/events-api-v2/overview/,
"Response Codes & Retry Logic"):

| Response | Retry? |
|---|---|
| 202 Accepted | No |
| 400 Bad Request | No |
| 429 Too many | **Yes — retry after some time** (PD asks for backoff "of a few minutes") |
| 500 / 5xx | **Yes — retry after some time** |
| Network error (incl. connection refused) | **Yes — retry after some time** |

The critical reading: **the retry obligation is on the sender.** PD's API
specifies what the *client* must do; the server promises nothing about
holding your event. Generic monitoring integrations vary wildly — some
implement this table faithfully, some retry 3 times and drop, some
fire-and-forget. Sentinel **cannot assume any slack** on this path: for
PD-API-shaped sources, the liveness guarantee comes from the watcher +
fallback, not from source behavior. The onboarding guide documents this
per-integration (the fingerprint-quality audit from D-3's mitigation is the
natural home: measure the integration's actual retry behavior during
onboarding, record the slack, size the drill target accordingly).

Mirror-image contract for Sentinel's *forwarder* (ADR-012): when Sentinel
sends to the real PD Events API v2, it implements PD's table — retry 429
(with minutes-backoff), 5xx, and network errors, with stable `dedup_key`
idempotency. The forwarder is the well-behaved client PD's docs describe.

### 3.3 PagerDuty → Sentinel (v3 webhooks; shadow-mode tap)

PagerDuty's own webhook delivery behavior
(https://github.com/pagerduty/developer-docs/blob/HEAD/docs/webhooks/02-Behavior.md,
"Error Handling and Retries"):

- Retried on: no response/timeout, 5xx, **429**, **connection cannot be
  established** (except most TLS errors), TLS cert expired, DNS errors.
- Retried **periodically for up to 48 hours**; subsequent webhooks for the
  same subscription+resource are **queued** behind the failing one; after
  48h the delivery is dropped.
- Timeouts: PD expects 2xx within **5s** (generic webhooks).

Two consequences: (1) in **shadow mode** the liveness hole does not exist —
PD holds and redelivers for 48h, and Sentinel has no write path anyway
(synthesis §3.6); (2) Sentinel's receiver must answer PD webhooks within
5s — the 11.4s Jev tail (Forge Move 1) must never be on the webhook-ack
path. (It isn't, post-ADR-010 — but the 5s number is now a named constraint
in the constraint map.)

### 3.4 Opsgenie → Sentinel

Verified boundary, stated honestly: Atlassian's official Opsgenie docs
(https://support.atlassian.com/opsgenie/docs/create-a-default-api-integration/)
describe the Alert API as a synchronous REST API and **publish no
server-side retry guarantee on the send path** — retry is the client's
responsibility, same shape as PD Events API v2 §3.2. For Opsgenie-sourced
traffic, Sentinel assumes **zero slack**: watcher + fallback carry the
guarantee, and onboarding records the integration's actual behavior.

### 3.5 The liveness slack table (the design's timing foundation)

| Source path | On connection-refused | Practical slack | Guarantee carried by |
|---|---|---|---|
| Alertmanager webhook → Sentinel | Retried (transport = retryable); 5xx retried; 429/4xx dropped; 2xx retires | Minutes (delay, not loss) | AM retry + watcher + fallback |
| Generic → PD-Events-v2-shaped receiver | Per PD's table, *sender* must retry; implementations vary | **Assume zero** | Watcher + fallback |
| PD v3 webhook → Sentinel (shadow) | PD retries 48h, queues behind failure | 48h | PD itself (shadow needs none) |
| Opsgenie → Sentinel | No documented server-side retry | **Assume zero** | Watcher + fallback |

The table is the answer to "how much liveness slack actually exists": for
the dominant on-prem source (Alertmanager) there is real, verified slack —
enough for a 90s detection and a human-executed, drill-proven recovery. For
generic API-shaped sources there is none, which is why the watcher pages in
≤90s and the fallback is a runbook, not a hope.

---

## 4. Schema-validated config — invalid ⇒ last-good + page, never crash-loop

### 4.1 The validation point

All config loads — startup, SIGHUP/`/-/reload`, API-triggered — go through
one loader with four ordered stages. **A failure at any stage rejects the
load; the live generation is untouched.**

1. **Parse** (JSON/YAML syntax). Catches the I-2 trailing comma.
2. **Schema validation** (JSON Schema, versioned alongside the config
   version). Types, required fields, enums, ranges.
3. **Semantic validation** (the part schemas can't express): threshold
   values within the ADR-022 governance bounds (`suppress_conf_min` ≥ 0.85
   floor, cost floors); allowlist entries carry attestation tuples (ADR-017);
   no contradictory rules (e.g., a fingerprint both allowlisted and
   security-banned); model pin matches a known version (ADR-015).
4. **Atomic swap.** Only after 1–3 pass does the loader publish the new
   generation (RCU-style: readers hold the old generation until the swap;
   no torn reads, no restart required).

This is Chesterton's fence honored, not ignored: Alertmanager's own
documented reload semantic is exactly this — "If the new configuration is
not well-formed, the changes will not be applied and an error is logged"
(https://prometheus.io/docs/alerting/latest/configuration/). We adopt the
precedent and extend it: *not applied* (last-good keeps serving) + *logged*
(the `config_rejected` event in the append-only log) + *paged* (the operator
is told, loudly — a rejected config at 2 AM is a SEV-worthy event, because
the operator's *intent* didn't take effect).

### 4.2 The last-good mechanism

- The loader keeps the last **N=5 validated generations** on disk
   (immutable, checksummed). "Last-good" is the newest validated generation,
   not "whatever was there before the crash."
- The receiver **never constructs the gate from unvalidated config.** The
   I-2 crash-loop mechanism (constructor throws on bad config) is removed
   by construction: the constructor only ever sees validated generations.
- **First boot with no valid config ever:** the receiver starts in
   **passthrough-only safe mode** — every alert forwarded unmodified,
   `/healthz` returns 503 (`config_current` fails: no generation). The box
   screams on day one rather than serving confidently with no policy. (Law 7:
   degrade to something useful — passthrough *is* the useful degradation —
   never to silence, and never to false confidence.)

### 4.3 What "page" means when the pager path itself is misconfigured

The hard case: the rejected config *is* the paging config. The design splits
config into two domains with separate last-good chains:

- **`policy`** (thresholds, allowlist, tuner outputs, model pin): validated
  per §4.1; invalid ⇒ last-good policy + page via the forwarder using
  last-good **credentials**. The page goes out because the credentials are
  a different failure domain from the policy.
- **`credentials`** (PD routing keys, webhook secrets, secondary-channel
  creds): also versioned with last-good. If the *new* credentials are
  invalid, last-good credentials keep the forwarder working and the operator
  is paged about the rejection. If credentials were **never valid**
  (first boot, or the last-good chain is exhausted): the forwarder cannot
  page — and this is exactly what `/healthz`'s `forwarder_draining` +
  `config_current` predicates and the **external watcher** exist for. The
  escalation ladder is:

  1. Forwarder with last-good credentials (covers bad-policy, bad-new-creds).
  2. Customer-configured **secondary channel** (ADR-012; separate credential,
     separate path — e.g., SMS gateway or chat webhook).
  3. **The watcher** declares UNHEALTHY (`/healthz` 503: no serving
     generation / forwarder down) and pages via *its* independent PD key.

  Step 3 is why the watcher's credential store is separate from Sentinel's
  (§2.1): a credential rotation that kills Sentinel must not kill the
  witness. The pre-mortem's "what if the pager path is the thing
  misconfigured" is answered by *not having a single pager path* — ADR-012's
  "no single notification path, ever," extended to the config plane.

### 4.4 Config fuzz — the test

T1-09's test, restated as contract: **1,000 malformed `thresholds.json`
variants** (trailing commas, truncated files, wrong types, out-of-range
thresholds, schema-version mismatches) ⇒ the process **never exits**, always
serves last-good, always emits `config_rejected`, always pages. Plus the
chaos drill: `kill -9` the receiver mid-storm ⇒ alerts reach PD via fallback
within the runbook's measured N seconds (CI + monthly).

---

## 5. Standby direct-to-PD fallback

### 5.1 Integration design

The customer provisions a **standby direct-to-PD integration** that is
configured, credentialed, and *warm* — but receiving no traffic:

- A PD Events API v2 integration key (or Alertmanager `pagerduty_configs`
  receiver) on the **same PD service** as the Sentinel-routed integration.
  Same service matters: PD dedupes `trigger` events by `dedup_key` within
  the service, so a double-sent alert (Sentinel + direct) with the same
  `dedup_key` collapses into one incident rather than two. The dedup
  continuity is mechanical, not hoped-for.
- **Warmth:** the monthly drill (§5.3) sends a real synthetic alert through
  the standby path. A standby that has never fired is a rumor (the Knight
  Capital rule, extended from the kill switch to the fallback).
- The standby integration's escalation policy mirrors production. Drift
  between the two (someone edits one policy) is caught by the drill's
  verify step.

### 5.2 Activation — exact trigger conditions

Fallback activation is **human-executed, watcher-triggered**:

1. Watcher declares UNHEALTHY (3 consecutive failed deep probes on both
   vantage points, §1.3) and pages the Sentinel platform admin with the
   failed predicates + runbook link. Detection ≤90s.
2. The operator runs the runbook (§5.4). Drill-proven recovery target:
   **≤10 min** from page to "fallback active, verified."
3. **No automatic source re-pointing.** Auto-rerouting all alerting is a
   Type-1-grade irreversible action (Law 3): a flapping watcher would
   oscillate routes and *manufacture* the alert loss the fallback exists to
   prevent. The verified slack (§3.5 — Alertmanager retries transport
   failures; generic sources get the 90s detection) affords the human.
   Auto-failover is recorded as a **future option** with its own pre-mortem
   required before adoption (flap-damping, route-lease, and double-page
   accounting would be its design).

### 5.3 The monthly drill — procedure

On the first Tuesday of each month, business hours, announced in the
on-call channel. The drill is a **release gate for the deployment's health
metric**, not a wiki page (I-2).

1. **Declare drill.** Operator flips the drill flag; watcher notified (so
   the drill doesn't read as a real UNHEALTHY).
2. **Execute runbook §§1–4** against the real standby path with a synthetic
   `DRILL`-tagged alert. The synthetic alert uses the **production standby
   integration key and production escalation policy** — the drill tests the
   true path, not a staging double. It is acknowledged immediately by the
   drill operator.
3. **Measure and record:** time from simulated-UNHEALTHY to fallback page
   received in PD; time to switch back; duplicate-page count (target: 0,
   counted via the reconciliation query §5.5); standby escalation-policy
   drift check (diff vs. production).
4. **Write the drill log entry** (append-only, buyer-visible): date,
   timings, duplicates, policy drift, operator name.
5. **Staleness rule:** drill log's last success > 35 days ⇒ deployment
   health metric goes red ⇒ pages the Sentinel platform admin. A stale
   drill is treated as a failed drill (I-2's test requirement, operationalized).

### 5.4 The fallback runbook (sections)

1. **Confirm.** Check `/healthz` from both vantage points; read the failed
   predicates. If Sentinel is actually healthy (watcher network issue),
   stand down and file the watcher incident — the runbook's first step is
   *disconfirm*, because a spurious fallback is its own pre-mortem (§6.4).
2. **Fence Sentinel.** Stop the forwarder (kill-switch flag — the same
   one-flag bypass from the deployment path, Fence 7). *Fence before
   switch*: a Sentinel that can still page, plus sources now pointing at
   PD directly, is the split-brain (§5.5). Order is load-bearing.
3. **Switch sources.** Per-source, enumerated in the customer-specific
   appendix: Alertmanager — update the webhook receiver URL to the standby
   (or flip the route to the `pagerduty_configs` receiver); generic
   Events-API clients — swap routing key to the standby key. Each switch
   is one config value; the runbook lists every source (the onboarding
   inventory from D-3's fingerprint audit is the source list — no
   discovered-at-3AM sources).
4. **Verify.** Synthetic alert through the standby path ⇒ PD incident
   ≤2 min. If verify fails, the runbook's **failure branch** executes:
   escalate to the PD account admin (key rotation suspected), fall back to
   the secondary channel (ADR-012), and declare "paging degraded" on the
   status page. A runbook that assumes its own success is theater (Law 2).
5. **Declare.** "Fallback active" entry in the event log + status page;
   Sentinel stays fenced until the postmortem.

**Re-entry** (after the incident): sources switch back only after the
watcher reports healthy for a **15-minute soak** AND the reconciliation
query (§5.5) shows zero in-flight duplicates AND the operator confirms.
Then: un-fence Sentinel, switch sources back one by one, verify each with
a synthetic, declare "Sentinel active."

### 5.5 Split-brain avoidance

Double-paging (Sentinel pages AND direct-PD pages for the same alert) is
the fallback's characteristic failure. Four layers:

1. **Fence-before-switch ordering** (§5.4.2). The single most important
   line in the runbook. A fenced Sentinel cannot page.
2. **Single-writer source config.** Each source has exactly one active
   destination at a time (one webhook URL / one routing key). The switch
   is a single value change, not "add a second destination." You cannot be
   in both states if the state is one value.
3. **Dedup continuity.** Same PD service + same `dedup_key` ⇒ PD's native
   alert dedup collapses the double. Sources that emit `dedup_key`s keep
   them across the switch (documented per-source in the runbook appendix).
4. **Reconciliation query.** After every fallback (real or drill): query PD
   for incidents in the window carrying both `via: sentinel` and
   `via: direct` tags for the same `dedup_key`. Count and report; target 0;
   any nonzero count opens a postmortem. Sentinel tags every incident it
   creates (`via: sentinel` in custom details); the standby path is
   identifiable by its integration name.

---

## 6. Why cutover is Stage 2c, not Stage 0

The deployment path (03-deployment-path.md) earns the gate's position in
front of the paging stack across stages. The liveness hole is **specific to
series topology** — it only bites when Sentinel sits *in* the paging path
rather than *alongside* it. The ordering argument:

1. **In Stages 0–2b the hole does not exist, mechanically.**
   - Stage 0 (shadow): read-only tap, zero write path — a dead Sentinel
     changes nothing (synthesis §3.6). PD's 48h webhook retry (§3.3) means
     even the tap is lossless.
   - Stage 1 (backtest): offline replay. No live path at all.
   - Stage 2a (advisory): annotations only; routing unchanged. A dead
     annotator loses notes, not pages.
   - Stage 2b (low-urgency): Sentinel is in series *only* for traffic where
     a mistake costs annoyance (suppressed low-urgency alerts are
     muted-not-dropped, receipted, one-click-overridable — ADR-007). The
     blast radius of a liveness failure here is bounded by construction.
2. **Series topology is the most expensive trust the buyer grants, so it is
   granted last.** At 2c the buyer holds: the divergence ledger (weeks of
   inspected disagreements), the signed backtest attestation (zero
   false-suppress on SEV1/SEV2), 60 days of suppression receipts with
   <5% override rates, and quarterly-tested kill-switch logs. The *only*
   new risk taken at 2c is liveness — isolated, named, and covered by this
   document's machinery. Granting series position at Stage 0 would bundle
   *every* risk (model, calibration, liveness, operational) into one
   unproven step. (Law 1: don't optimize the cutover; question whether the
   gate belongs in series before it has evidence. It doesn't.)
3. **The liveness machinery itself needs the earlier stages to be built.**
   The watcher, the standby integration, the drill discipline, the
   per-source runbook appendix (built from D-3's onboarding inventory), the
   operator muscle memory — none of this exists on day one. Stages 2a/2b
   are where it is built and proven; 2c is where it is *relied upon*.
   Deploying the reliance before the machinery is Law 2's definition of a
   design that only works when everything works.
4. **Rollback composes.** 2c's auto-revert (any confirmed SEV1/SEV2
   false-suppress ⇒ back to 2b) returns the system to a topology where the
   liveness hole doesn't exist. The safety net and the deployment order are
   the same shape.

---

## 7. Alternatives considered and rejected

### 7.1 Process supervisor restarts — REJECTED

"Just let systemd/K8s restart it." Restarts don't fix a poisoned config —
the process crash-loops on every start, and each restart is a window of
connection-refused. Worse, aggressive restart backoff eventually *gives up*
(systemd `StartLimitBurst`), converting a crash-loop into a permanently
dead receiver with no further signal. And a supervisor that restarts
forever masks the outage from any monitoring keyed on process-down while
alerts are being refused between restarts. The supervisor keeps its job —
`/livez` restarts for transient panics, capped at 5/10 min (§1.3) — but it
is not the liveness story. It cannot be: **the failure mode is in the
config, and the supervisor doesn't read config.**

### 7.2 Active-active receivers — REJECTED (future option)

Two (or N) receivers behind a load balancer removes the single dead
receiver — but at this stage the cost exceeds the benefit, and it
introduces a worse failure class:

- **Split-brain on the paging path.** Two gates means two dispositions for
  the same alert unless they share state. Shared state (for dedup,
  correlation windows, outbox idempotency) is itself a new single point of
  failure — and a *harder* one than the receiver, because it's stateful.
  Divergent dispositions across instances (one suppresses, one pages) is
  exactly the double-page/single-miss nondeterminism the triple lock was
  built to eliminate.
- **Cost and complexity** for a failure mode the watcher + fallback already
  covers within the verified slack window (§3.5).
- **Kept as a future option**, explicitly: when alert volume or contractual
  SLA demands it, the active-active design gets its own RFC — with the
  shared-state design, the disposition-consensus rule, and the split-brain
  pre-mortem done *then*, not assumed now. (Law 3: irreversible architecture
  gets RFC-grade rigor at decision time.)

---

## 8. Pre-mortem — the liveness story itself, attacked

Law 6 applies to mitigations too. It is October 2027. Sentinel has been at
Stage 2c for six months. A SEV1 was missed — or nearly missed — and the
liveness machinery is on the stand. Five scenarios, each with mechanism,
why the design as written doesn't prevent it, the mitigation, and the test.

### 8.1 The crash-loop that isn't config

**Mechanism.** The config is valid. The crash is a code bug: a nil-pointer
in the correlator on a pathological alert shape (deeply nested labels from
a new integration). The receiver panics on the *first* such alert, the
supervisor restarts it, the *retrying* Alertmanager redelivers the same
alert (transport failures are retryable — §3.1), the receiver panics again.
Poison alert + retrying source = deterministic crash-loop, and §4's
config validation is irrelevant — the config is fine.

**Why the design doesn't prevent it.** The validation point (§4.1) validates
*config*, not *alerts*. The supervisor's restart cap (5/10 min) eventually
stops restarting — correct per §7.1, but now the receiver is dead *and* the
poison alert is still queued at Alertmanager, which keeps retrying against
a dead endpoint.

**Mitigation.** Two layers, both already in the design but now load-bearing
for this case: (1) the receiver's per-request panic isolation — a panicking
*request handler* must not take down the *process* (the frozen spec's
"every step wrapped" from ARCHITECTURE.md §3.9, now a named invariant: a
single alert can kill its own request, never the receiver); the poison alert
becomes a `passthrough` + a paged `handler_panic` event, and Alertmanager's
retry then succeeds against the *recovered* process. (2) The watcher covers
the residual: if the process truly dies, UNHEALTHY in ≤90s, fallback
runbook, AM's retries bridge the gap.

**Test.** Poison-alert fault injection: 1,000 pathological payloads
(deep nesting, null bytes, 512KB-max shapes, type confusions) ⇒ process
never exits; each yields passthrough + event. Release-blocking.

### 8.2 The poisoned config that passes validation

**Mechanism.** An operator hand-edits `policy.json`: `suppress_conf_min`
0.90 → 0.50, and it *passes* schema and semantic validation — because the
validation bounds were set at the schema's floor, not at the safety case's
floor, or because the edit came through the "principal exception" path
(ADR-022) with a rubber-stamped attestation. The process is healthy,
`/healthz` is 200, the watcher is silent — and the gate now suppresses
twice as much. Liveness is perfect; correctness is gone. The liveness
machinery reports "all green" through the entire drift.

**Why the design doesn't prevent it.** Named seam (§0): this design covers
*dead*, not *wrong*. No health predicate can distinguish "confident and
right" from "confident and wrong" — that is the H-2 watchdog's job
(suppression-rate SLO, 30-day re-validation clock), and it is *outside*
this document's scope by design.

**Mitigation (at the seam).** Three things this design *does* own: (1) the
semantic validation bounds (§4.1.3) encode ADR-022's floors *in the schema*
— `suppress_conf_min < 0.85` without a principal-exception flag is a
*rejection*, not a warning; the fatigue ratchet's destination is
unloadable by hand-edit. (2) Every loaded generation is recorded in the
append-only event log with its attestation tuple — the drift is *visible*
even when it's valid. (3) The honest contract, stated in the runbook and
the buyer docs: "the watcher proves the process is alive; the calibration
certificates prove it is right; neither substitutes for the other."

**Test.** Attempt to load `suppress_conf_min: 0.80` without the exception
flag ⇒ rejected, `config_rejected`, page. Attempt *with* a forged
attestation ⇒ rejected (signature check), page. The governance tests live
with ADR-022; the schema-floor tests live here.

### 8.3 The watcher itself fails

**Mechanism (a): the poller dies.** The customer host running the poller is
decommissioned during a migration nobody told the Sentinel team about.
Sentinel later crash-loops at 02:14. No page. The SEV1 never enters the
system — I-2, one level up.

**Mitigation.** The dead-man's switch (§2.1): the poller's 60s heartbeat
stops ⇒ the SaaS pages "watcher dead" — a *distinct* alert from "Sentinel
down," because the response is different (fix the watcher, don't run the
fallback runbook). The poller's liveness is monitored with the same
seriousness as Sentinel's; "who watches the watcher" terminates at the
SaaS, which is the trust root by explicit decision.

**Mechanism (b): the poller is network-partitioned from Sentinel but alive.**
It reports UNHEALTHY (can't reach `/healthz`); the page fires; the operator
runs the runbook — against a healthy Sentinel.

**Mitigation.** Runbook §1 is *disconfirm first*: check `/healthz` from the
second vantage point and from the operator's own machine. Two vantage points
agreeing on UNHEALTHY is the trigger; one vantage point disagreeing is a
network incident, and the runbook stands down. The drill (§5.3) includes a
partition simulation so the operator has *felt* the disconfirm path, not
just read it.

**Test.** Kill the poller ⇒ "watcher dead" page within 5 min. Partition the
poller from Sentinel ⇒ UNHEALTHY page; operator disconfirms via second
vantage point; no fallback executed; incident logged as watcher-network.

### 8.4 The fallback activates spuriously

**Mechanism.** A flapping network path makes both vantage points fail 3
consecutive probes during a real (minor) Sentinel GC pause; the watcher
pages UNHEALTHY; the bleary-eyed operator, fresh from the drill where
"execute fast" was praised, runs the runbook without disconfirming.
Sources are re-pointed to direct PD; then Sentinel recovers. For 20 minutes,
*some* sources point at Sentinel (the operator didn't finish the list) and
some at PD directly — the split-brain (§5.5), manufactured by the safety
system.

**Why the design doesn't prevent it.** The 3×2 probe rule (§1.3) reduces
flap-triggered pages but can't eliminate them; the runbook's disconfirm
step is a *human* step, and humans under page-stress skip steps.

**Mitigation.** (1) The drill measures and rewards the *disconfirm* —
drill scoring includes "time to disconfirm-or-confirm," and a drill that
skips §1 fails the drill. (2) Fence-before-switch bounds the damage: even
in a spurious activation, Sentinel is fenced first, so the worst case is
"all traffic direct-to-PD for 20 minutes" (the pre-Sentinel status quo),
not double-paging. (3) The re-entry soak (15 min healthy + reconciliation)
is mandatory even after spurious activations — no "oh it was nothing,
flip it back." (4) The reconciliation query (§5.5) runs after every
activation, spurious or not; its zero-duplicate target is the scoreboard.

**Test.** Game-day: inject flapping probes, page the operator, assert the
drill log shows disconfirm-first and (if they proceed anyway) assert
fence-before-switch ordering and zero duplicates in reconciliation.

### 8.5 The fallback fails when needed

**Mechanism.** Sentinel is truly down at 02:14. The operator runs the
runbook — and the standby integration key was rotated 3 weeks ago during a
PD admin cleanup; the new key was never put in the runbook. Step 3
(switch sources) succeeds; step 4 (verify: synthetic alert ≤2 min) fails.
The operator is now paging-blind with a dead primary and a dead fallback,
at 02:20, with the SEV1 still burning.

**Why the design doesn't prevent it.** A standby that isn't *continuously*
proven rots exactly like the triple lock's locks (C-1's lesson, applied to
operations): the key, the escalation policy, the source list all age
silently.

**Mitigation.** (1) The monthly drill sends the synthetic through the
*production* standby key (§5.3.2) — a rotated key fails the drill within
30 days, not during the incident. (2) The drill's policy-drift check
catches escalation-policy edits. (3) The runbook's **failure branch**
(§5.4.4) is not "try harder": escalate to the PD account admin, fall back
to the secondary channel (ADR-012), declare "paging degraded" publicly.
The failure branch is itself drilled — the operator has executed "verify
failed, now what" before 02:20 ever happens. (4) Staleness rule: no
successful drill in 35 days ⇒ red health metric ⇒ page. The drill is the
freshness proof for the fallback, the way attestation tuples are the
freshness proof for the allowlist (synthesis §3.5).

**Test.** Rotate the standby key without updating the runbook ⇒ next drill
fails loudly; assert the failure-branch path executes (secondary channel
page fires, "paging degraded" declared). CI asserts the drill log's
freshness on every deploy.

---

## 9. Open items and seams (named, not hidden)

- **Liveness ≠ correctness (§8.2).** The seam with ADR-022/H-2 is explicit:
  this design proves the process is alive and serving a validated config;
  the calibration certificates and the suppression-rate watchdog prove it
  is right. The buyer docs state both, separately.
- **ADR-005 tension** (IP allowlisting for the PD path — synthesis §3.7):
  the watcher's authentication (§2.3) and the receiver's webhook
  verification posture both touch it. Reconciliation belongs in Aditya's
  ADR-005 verdict; this design assumes mTLS-or-bearer on `/healthz` and
  takes no position on IP allowlisting.
- **Auto-failover** is a future option (§5.2), not a rejection of ambition:
  its pre-mortem (flap-damping, route leases, double-page accounting) is
  owed before adoption.
- **Active-active receivers** likewise (§7.2).
- The **per-source runbook appendix** (every source, its switch step, its
  measured retry behavior from §3.5's onboarding audit) is built during
  Stage 2a/2b and is a **2c entry criterion**: no cutover without it.

---

## 10. CREATIVE APPLICATION — the liveness story as product

The deployment path (03-deployment-path.md) argues Sentinel sells evidence,
not suppression. The liveness design generates its own evidence artifacts,
and they are moat-grade for the same reason — no competitor can replicate a
drill log they didn't earn month by month:

1. **The published drill log.** Every monthly fallback drill — timings,
   duplicate counts, policy-drift diffs — is buyer-visible. The Knight
   Capital rule ("a kill switch that has never been pulled is a rumor")
   extends naturally: *a fallback that has never fired is a rumor.* The
   buyer doesn't take our word for the runbook; they read twelve months
   of it.
2. **The liveness slack table (§3.5) as onboarding documentation.** "Here is
   exactly what your Alertmanager does when we're down, what your generic
   webhooks do, and how long you have" — printed per-source, measured at
   onboarding, re-verified at each drill. Nobody else in the AIOps market
   publishes the failure semantics of the customer's own stack back to
   them. It is both honest and disarming.
3. **`/healthz` as a customer-visible liveness certificate.** The deep-check
   JSON (§1.2) — config generation, forwarder lag, canary age — is exposed
   (authenticated) to the buyer's own monitoring. The buyer's NOC can watch
   our liveness the way they watch any vendor SLA. Quiet is a signal
   (Prism's dark-cockpit law); a `/healthz` that degrades *visibly* before
   it fails is the product behaving like infrastructure instead of magic.
4. **The pattern generalizes.** The watcher-watching-the-watcher structure
   (§2.1, §8.3) is the template for H-2's "the watchdog watches the
   watchdog" — the suppression-rate SLO monitor gets the same two-level
   treatment. And the poison-alert isolation (§8.1: a request may die, the
   process may not) is the same blast-radius thinking as the two-tier
   architecture, applied one level down. The laws compose.

**The one-line liveness thesis:** *fail-open is a property of a live
process; liveness is a property of the deployment.* This document moves the
"never drops a page" promise from the process (where it was vacuous) to the
deployment (where it is a watcher, a runbook, a drill log, and a verified
slack table) — and then pre-mortems the deployment too.

---

*Sources verified 2026-10-03: PagerDuty Events API v2 retry table
(https://developer.pagerduty.com/docs/events-api-v2/overview/); PagerDuty
webhook delivery behavior
(https://github.com/pagerduty/developer-docs/blob/HEAD/docs/webhooks/02-Behavior.md);
Alertmanager notify pipeline and Retrier verdicts
(https://github.com/prometheus/alertmanager/blob/main/notify/notify.go);
webhook-receiver retry semantics
(https://github.com/prometheus/alertmanager/pull/5548,
https://github.com/prometheus/alertmanager/pull/5496);
Alertmanager config-reload precedent
(https://prometheus.io/docs/alerting/latest/configuration/);
Opsgenie API integration docs — no server-side retry promise verified
(https://support.atlassian.com/opsgenie/docs/create-a-default-api-integration/).
Field observations on 429-as-drop and 2xx-retires-notification:
https://github.com/jdwlabs/apps/commit/82264a624cbb0c6b5e6ca112af2590f022264a73.*

*Lane 3 sign-off: ADR-018 is implementation-ready. The five pre-mortems
above are the acceptance criteria — any implementation that cannot answer
all five does not ship.*
