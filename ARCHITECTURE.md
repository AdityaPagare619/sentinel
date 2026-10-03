# Sentinel — Architecture (v0.1 MVP)

**Status:** design frozen for v0.1 · **Date:** 2026-10-02
**Spec parents:** `~/workspace/jev-product-research/notes/phase5-arch1-paging.md` (full product design), `~/workspace/jev-product-research/notes/phase1-jev-deepdive.md` §§2.1–2.7 (wire format ground truth)
**Build env:** `~/workspace/jev-builds/sentinel/` · Python 3.12, **stdlib only** (no pip deps)

---

## 1. What Sentinel is (one paragraph)

Sentinel is a drop-in pre-page gate that sits in front of the PagerDuty/Opsgenie Events API. Customers point their existing alerting integration at Sentinel instead of PagerDuty directly; every alert is triaged by Jev (TypeSafe's System-One decision model: three parallel typed questions — severity, owning team, disposition — with calibrated probabilities), and per an expected-cost policy the gate either pages now, queues for business hours, suppresses known noise, or passes through to the existing pipeline untouched. **The only autonomous action is paging a human; suppression requires a triple lock; any error fails open to the existing pipeline.** Weeks 1–2 of any deployment run shadow-only (log dispositions, change nothing).

### Design principles (non-negotiable)

1. **Fail open, always.** A gate that drops pages on error is a company-ending bug. Every exception path → pass-through. Enforced by a unit test that kills the Jev client and asserts every alert still pages.
2. **Uncertainty pages.** Low confidence, `cannot_determine`, malformed input → the existing pipeline, byte-identical to today.
3. **Never pay Jev for deterministic work.** Dedup, storm-collapse, and change-window suppression happen *before* the Jev call, in plain code.
4. **Descriptions, not bare labels.** Every Choice option carries a one-line description (research: fixes 37/40 hard router tasks; billed as input tokens and worth it).
5. **`cannot_determine` is mandatory** on all three questions (Jev docs rule).
6. **Honest numbers.** No accuracy claims in output; only calibration metrics on the customer's own labels.

---

## 2. Component diagram

```
                    ┌──────────────────────────────────────────────┐
                    │                  SENTINEL v0.1                │
  PagerDuty/        │                                              │
  Alertmanager/     │  ┌──────────┐   ┌─────────────┐   ┌────────┐  │
  generic ──────────┼─▶│ receiver │──▶│ correlator  │──▶│  gate  │──┼──▶ PagerDuty/Opsgenie
  webhooks          │  │(ingress) │   │(dedup/storm)│   │(Jev +  │  │    (forwarder relays
                    │  └──────────┘   └─────────────┘   │policy) │  │     page/queue/suppress)
                    │                                  └───┬────┘  │
                    │                                      │       │
                    │                               ┌──────▼────┐  │
                    │                               │ audit log │  │
                    │                               │ (SQLite)  │  │
                    │                               └───────────┘  │
                    └──────────────────────────────────────────────┘
   Offline (nightly/CLI):  tuner ──▶ thresholds.json
                           evalharness ──▶ calibration-report.md
                           synthetic ──▶ labeled alert fixtures
```

**v0.1 ships:** receiver, correlator, gate, forwarder, audit, tuner CLI, eval harness, synthetic fixtures, System-One client + mock.
**Deferred (documented, not built):** dashboard UI, label joiner automation, SSO, self-hosted relay, LLM adjudicator tier, fine-tuned-encoder exit.

---

## 3. Module layout + API contracts

All code under `src/sentinel/`. Every module below is a build unit with a frozen contract — parallel builders code against these signatures.

```
src/sentinel/
  __init__.py
  client.py        # System-One wire client + mock
  models.py        # Alert, Disposition, DecisionRecord, Thresholds dataclasses
  questions.py     # the 3 Jev questions (option lists + descriptions)
  state.py         # state shaping with token budget
  correlator.py    # fingerprint dedup, storm collapse, change windows
  gate.py          # Jev call + threshold policy + fail-open
  forwarder.py     # relay to PagerDuty/Opsgenie (pass-through on error)
  audit.py         # SQLite decision log (Postgres-compatible schema)
  receiver.py      # HTTP ingress: PD Events API v2 + generic webhook
  tuner.py         # CLI: labeled history -> thresholds + savings projection
  evalharness.py   # CLI: metrics + calibration report
  synthetic.py     # synthetic alert/outcome generator for tests
tests/
  test_client.py, test_questions.py, test_state.py, test_correlator.py,
  test_gate.py, test_forwarder.py, test_audit.py, test_receiver.py,
  test_tuner.py, test_evalharness.py
```

### 3.1 `client.py` — System-One wire client

```python
class JevError(Exception): ...
class JevAuthError(JevError): ...        # 401
class JevRateLimited(JevError): ...      # 429 (honors retry-after)
class JevOverloaded(JevError): ...       # 529
class JevTimeout(JevError): ...

@dataclass
class Answer:  # one answered question
    qid: str
    qtype: str                 # "choice" | "noul" | "score"
    choice: str | None
    noul: float | None
    probabilities: dict[str, float]
    confidence: float | None   # None for noul (API returns none)

@dataclass
class DecisionResponse:
    model: str                 # resolved versioned id, e.g. "jev-1.13.0"
    answers: dict[str, Answer]
    input_tokens: int

class SystemOneClient:
    def __init__(self, api_key: str, base_url: str = "https://api.typesafe.ai",
                 model: str = "jev-latest", timeout_s: float = 8.0,
                 max_retries: int = 3, retry_budget_s: float = 2.0):
        # base_url override = clone wire-format support (same /v1/systemone path)
        # api_key read from TYPESAFE_API_KEY env by the factory below, never hardcoded
    def decide(self, state: dict | str, questions: dict) -> DecisionResponse: ...
    # retry: 3 attempts within retry_budget_s, exponential backoff + jitter;
    # 429 honors Retry-After; 529/5xx/timeout -> JevOverloaded/JevTimeout after budget;
    # 401 -> JevAuthError (no retry); 422 -> JevError (no retry, surfaces field).

def client_from_env() -> SystemOneClient:
    # reads TYPESAFE_API_KEY; raises JevAuthError with a helpful message if missing

class MockSystemOneClient(SystemOneClient):
    # scripted answers for tests: map from a state-fingerprint or question content
    # to canned DecisionResponse; records every call for assertions.
    def __init__(self, script: dict[str, DecisionResponse] | None = None): ...
    @property
    def calls(self) -> list[dict]: ...
```

Wire details (from research — do not deviate): `POST {base_url}/v1/systemone`, headers `Authorization: Bearer <key>`, `Content-Type: application/json`, **custom `User-Agent`** (urllib default gets 403). Body: `{"state": ..., "model": "jev-latest", "questions": {qid: {"type":..., "instructions":..., "criteria":...}}}`. Choice criteria = `{option: description|null}` (≤255 options). Probabilities rounded to 0.01; noul clamped to [0.01, 0.98]; no seed/determinism param exists.

### 3.2 `models.py`

```python
@dataclass
class Alert:
    alert_id: str
    received_at: str            # ISO-8601
    fingerprint: str            # hash(service, check, severity_in, region)
    service: str
    check: str
    severity_in: str            # source severity label
    title: str
    source: str                 # "pagerduty" | "alertmanager" | "generic"
    labels: dict[str, str]      # env, region, cluster...
    metric_value: float | None
    metric_threshold: float | None
    breach_duration_s: int | None
    raw: dict                   # original payload, retained

@dataclass
class Disposition:
    action: str      # "page_now" | "page_business_hours" | "suppress" | "passthrough"
    reason: str      # "threshold" | "allowlist" | "uncertain" | "shadow" |
                     # "dedup" | "change_window" | "storm" | "error:<code>"
    team: str | None
    confidence: float | None
    latency_ms: float

@dataclass
class DecisionRecord:  # one audit row; mirrors the SQL schema in §7
    alert: Alert
    input_sha256: str
    jev_model: str | None
    q_severity: Answer | None
    q_team: Answer | None
    q_disposition: Answer | None
    disposition: Disposition

@dataclass
class Thresholds:
    suppress_p1_max: float = 0.002   # P(p1_critical) must be below this
    suppress_conf_min: float = 0.90
    page_p1p2_min: float = 0.30      # P(p1)+P(p2) above this -> page_now
    uncertain_conf_max: float = 0.50 # below this -> page_now (uncertainty pages)
    queue_conf_min: float = 0.70
    # tuned per org by tuner.py; serialized to thresholds.json
```

### 3.3 `questions.py`

`build_questions(team_options: list[tuple[str, str]] | None) -> dict` returns the three question dicts exactly as specified below. Team options default to the six below; customers supply ≤8 of their own.

**Q1 `severity` (Choice)** — the disposition anchor:
| option | description |
|---|---|
| `p1_critical` | Customer-facing outage or data-loss risk in progress; revenue/SLA actively burning; needs a human in under 5 minutes. |
| `p2_high` | Core function degraded or at imminent risk of full outage; needs a human within about 30 minutes. |
| `p3_medium` | Non-critical degradation or early warning; safe to handle in business hours; no page needed. |
| `p4_low` | Informational; no action required unless it recurs; never pages. |
| `known_noise` | Matches a recurring benign pattern (flap, self-clearing spike, planned-work artifact); historically never became an incident. |
| `cannot_determine` | The alert context is insufficient or contradictory to assign a severity; do not guess. |

**Q2 `owning_team` (Choice)** — default options (customer overrides at onboarding):
| option | description |
|---|---|
| `platform` | Core infra, kubernetes, CI/CD runners, deploy pipeline. |
| `network` | DNS, CDN, load balancers, VPC, transit — connectivity and edge. |
| `data` | Databases, caches, queues, pipelines — persistence and streaming. |
| `product_backend` | Application services and APIs owned by product engineering. |
| `security` | Auth, WAF, intrusion, certificate expiry — the security on-call. |
| `cannot_determine` | No clear owner from the alert context; route to the default escalation policy. |

**Q3 `disposition` (Choice)** — the money question; descriptions embed the conservative policy:
| option | description |
|---|---|
| `page_now` | A human must be woken or paged immediately; this is or may be a real customer-impacting event. |
| `page_business_hours` | Route to the queue for next-business-hours handling; do not wake anyone. |
| `suppress` | Safe to drop: confirmed known noise with a clean historical record; no human needs to see it. Only choose with very high certainty. |
| `cannot_determine` | Not enough evidence to act; default to the existing pipeline — page as before, drop nothing. |

Instructions strings: each question gets a one-sentence `instructions` naming the decision (e.g. `"Classify the severity of this production alert."`).

### 3.4 `state.py`

`build_state(alert: Alert, history: dict, context: dict) -> dict` — returns the JSON `state` object. Field priority with an **~850-token hard cap** (token ≈ chars/4; truncate lowest-priority fields first):

1. `title`, `source`, `check` (~40 tok)
2. `service`, `labels` env/region/cluster (~60 tok)
3. `metric`: value vs threshold, breach duration (~50 tok)
4. `outcome_history`: 30d count for this fingerprint, # paged, # became SEV1/2, median auto-clear time (~120 tok) — **highest-value field**
5. `recent_deploys`: last 6h touching this service, top 3 (~80 tok)
6. `sibling_alerts`: last 1h on sibling services, top 3 (~80 tok)
7. `oncall`: team → current primary, tz (~60 tok)
8. `time`: ISO time + dow + holiday flag (~20 tok)
9. `runbook_title` (~30 tok)
10. `annotations`: customer free text, e.g. "redis spikes on this cluster self-clear" (~310 tok)

`estimate_tokens(text: str) -> int` helper (chars//4). `input_sha256(state) -> str` for the audit row.

### 3.5 `correlator.py`

```python
class Correlator:
    def __init__(self, window_s: int = 300, storm_fingerprints: int = 20,
                 storm_window_s: int = 60, change_windows: list[dict] | None = None): ...
    def ingest(self, alert: Alert) -> CorrelationResult: ...
    # CorrelationResult.kind: "new" | "duplicate" | "storm" | "change_window"
    # "duplicate": same fingerprint inside window_s -> inherit, no Jev call
    # "storm": >storm_fingerprints distinct fingerprints in storm_window_s ->
    #          single aggregate disposition request (counts by service), page once
    # "change_window": deploy/change window open for service -> page_business_hours,
    #          reason="change_window", NO Jev call (deterministic, auditable)
```

Fingerprint = sha256 hex of `service|check|severity_in|region`, first 16 chars. In-memory dict for v0.1 (Redis in SaaS).

### 3.6 `gate.py` — the policy heart

```python
class Gate:
    def __init__(self, client, thresholds: Thresholds,
                 allowlist: set[str],  # customer-verified known-noise fingerprints
                 audit, shadow: bool = False): ...
    def evaluate(self, alert: Alert, state: dict,
                 history: dict, context: dict) -> tuple[Disposition, DecisionRecord]:
        # 1. build questions (questions.py), call client.decide
        # 2. apply threshold policy (§5)
        # 3. write audit row ALWAYS (even on error / passthrough)
        # 4. on ANY exception from the client -> Disposition("passthrough", reason="error:<code>")
        #    NEVER raises to the receiver. This is the fail-open guarantee.
```

### 3.7 `forwarder.py`

```python
class Forwarder:
    def __init__(self, pd_events_url: str = "https://events.pagerduty.com/v2/enqueue",
                 timeout_s: float = 5.0): ...
    def forward(self, alert: Alert, disposition: Disposition) -> ForwardResult:
        # page_now            -> POST trigger to PagerDuty with original routing key
        # page_business_hours -> POST trigger with severity downgraded to "warning"
        #                       + custom_details["sentinel_queue": "business_hours"]
        # suppress            -> do NOT forward; record only (audit already written)
        # passthrough         -> POST original payload unchanged
        # on forward error -> log + metric; the page was already decided, alert the operator
```

### 3.8 `audit.py`

```python
class AuditLog:
    def __init__(self, db_path: str = "sentinel.db"): ...
    def record(self, rec: DecisionRecord) -> int: ...   # returns row id
    def get(self, row_id: int) -> dict: ...
    def decisions_for_fingerprint(self, fp: str, limit: int = 100) -> list[dict]: ...
```

Schema (SQLite; Postgres-compatible types for the SaaS upgrade):

```sql
CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY,
    received_at   TEXT NOT NULL,
    alert_id      TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    input_sha256  TEXT NOT NULL,
    jev_model     TEXT,
    q1_severity   TEXT,          -- chosen option
    q1_probs      TEXT,          -- JSON map option->prob
    q1_conf       REAL,
    q2_team       TEXT,
    q2_probs      TEXT,
    q2_conf       REAL,
    q3_disposition TEXT,
    q3_probs      TEXT,
    q3_conf       REAL,
    action        TEXT NOT NULL, -- page_now|page_business_hours|suppress|passthrough
    reason        TEXT NOT NULL,
    latency_ms    REAL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_fp ON decisions(fingerprint);
CREATE TABLE IF NOT EXISTS outcomes (   -- joined later by the label pipeline
    alert_id     TEXT PRIMARY KEY,
    fingerprint  TEXT NOT NULL,
    became_sev12 INTEGER,        -- 0/1/NULL
    auto_cleared INTEGER,
    mttr_min     REAL,
    labeled_at   TEXT
);
```

### 3.9 `receiver.py`

Stdlib `http.server.ThreadingHTTPServer`. Routes:

- `POST /v2/enqueue` — PagerDuty Events API v2 shape. Required JSON: `routing_key`, `event_action` ("trigger"), `payload.summary`, `payload.source`, `payload.severity`. Responds `{"status":"success","message":"Event processed","dedup_key":"..."}` (mirrors PD so existing integrations don't break). `dedup_key` optional → else generated.
- `POST /webhook/generic` — `{service, check, title, severity, labels{}, metric{}}`; requires `X-Sentinel-Signature: sha256=<hex>` (HMAC-SHA256 of `<unix-ts>.<raw-body>` with `SENTINEL_WEBHOOK_SECRET`) + `X-Sentinel-Timestamp` (unix seconds, |now−ts| ≤ 300s). Fail-closed: absent/invalid/stale → 403; empty secret refuses startup. See §7.
- `GET /healthz` → `{"ok": true}`.

Pipeline per request: parse → normalize to `Alert` (unparseable → **passthrough**: forward original bytes to PagerDuty + metric, never drop) → correlator → (new/storm → gate) → forwarder → respond. Every step wrapped; the receiver never returns 5xx for a triage failure — worst case it forwards the original payload.

---

## 4. Confidence policy (expected-cost math)

Let C_FP = $100 (false page: ~45 min sleep + 2–3h debugging at ~$90/hr loaded) and C_FN = $50,000 (suppressed true SEV1: SLA + revenue + incident overhead) — both org-tunable, defaults [ASSUMED] from research. Suppress is rational iff P(SEV1)·C_FN < (1−P)·C_FP, i.e. **p* < 100/50,100 ≈ 0.002**.

| Action | Gate condition |
|---|---|
| `suppress` | P(`p1_critical`) < 0.002 **AND** Q3 confidence ≥ 0.90 **AND** fingerprint ∈ customer-verified allowlist — **triple lock** |
| `page_now` | P(p1)+P(p2) > 0.30 **OR** Q3 confidence < 0.50 (uncertainty pages) |
| `page_business_hours` | P(p3)+P(p4) dominant **AND** confidence ≥ 0.70 |
| `passthrough` | any client error/timeout, Q3 = `cannot_determine`, or unparseable input |

**Shadow mode:** `Gate(shadow=True)` logs the disposition it *would* have taken but always returns `passthrough`. Weeks 1–2 of any deployment. The weekly shadow report ("we would have suppressed X pages, gotten Y wrong") is the sales collateral.

Honesty note (from research): Jev confidence is group-level calibration (routing ECE 0.096 measured — good, not perfect), and Jev is provably non-deterministic (1.3–2.2% flips, no seed). The 0.90 bar is applied *after* per-org calibration fitting; the allowlist exists because raw week-1 probabilities are uncalibrated. Every decision row stores `input_sha256` + `jev_model` so a flip is auditable, never mysterious.

---

## 5. Threshold tuner CLI (`tuner.py`)

```
python -m sentinel.tuner --labels labels.jsonl [--c-fp 100] [--c-fn 50000] -o thresholds.json
```

Input: JSONL, one object per historical alert: `{fingerprint, q1_probs:{...}, q3_confidence, became_sev12: 0/1, would_page_baseline: 0/1}` (from shadow runs or the eval harness).
Output: recommended `Thresholds` + a printed projection with **real arithmetic**:

```
suppress_p1_max   = 0.0020   (from C_FP/(C_FP+C_FN) = 100/50100)
suppress_conf_min = 0.90
page_p1p2_min     = 0.30
Alerts evaluated: 12,400 | baseline pages: 11,020
Projected: suppress 7,914 (71.8% of baseline pages), page_now 2,488, queue 618
Expected false suppresses: 0.4  |  Expected cost/yr: $20,000  |  Avoided page cost/yr: $791,400
```

Grid-searches `suppress_conf_min` ∈ {0.80, 0.85, 0.90, 0.95} and reports the tradeoff table; never recommends below the expected-cost optimum — it reports, the human decides.

---

## 6. Eval harness (`evalharness.py` + `synthetic.py`)

```
python -m sentinel.evalharness --n 2000 --seed 7 -o calibration-report.md
```

- `synthetic.py` generates N labeled alerts from a seeded mixture: `known_noise` flaps (auto-clear, never SEV), deploy-adjacent spikes, real SEV1/2s (5%), p3/p4 warnings. Labels = outcomes. Deterministic via seed.
- The harness replays them through `Gate` with `MockSystemOneClient` scripted from the labels (with configurable label noise + flip injection to simulate the 1.3–2.2% non-determinism).
- Metrics: severity accuracy, team-routing accuracy, disposition accuracy vs outcome labels; **ECE** (10-bin) on Q1/Q3; **coverage@τ** for τ ∈ {0.7, 0.8, 0.9}; **flip rate** (100 repeats on 200-alert sample; bar < 2%); **option-order shuffle** (permute option order on 200 alerts; bar < 3% disposition change); **false-suppress rate on confirmed SEV1s** (the trust metric).
- Output: `calibration-report.md` with tables + an explicit HONEST LIMITATIONS section (synthetic data proves plumbing, not production accuracy).

---

## 7. Security

- **BYOK:** key arrives only via `TYPESAFE_API_KEY` env var (or `client_from_env()`). Never in code, logs, audit rows, or error messages. HTTP-layer redaction: the `Authorization` header is stripped before any request/response logging. v0.1 runs single-tenant (one key per deployment); per-tenant KMS envelope is the SaaS upgrade (documented, not built).
- **Webhook auth:** generic webhook requires HMAC-SHA256 (`X-Sentinel-Signature` + `X-Sentinel-Timestamp`, |now−ts| ≤ 300s) — fail-closed: empty secret refuses startup, absent/invalid/stale signature is a 403. Flagged onboarding (`SENTINEL_WEBHOOK_ONBOARDING=1`) may fail open only with a CRITICAL boot warning + `/healthz` surfacing. PD path relies on the customer's routing key. *(ADR-005, adjudicated 2026-10-03: the IP allowlist was REJECTED entirely — struck here per panel condition (d). Egress IPs rotate; an unmaintained pinning knob fails closed on rotation and turns PD IP changes into dropped-alert incidents. HMAC is the real authentication; no allowlist knob ships.)*
- **Audit integrity:** append-only writes; no UPDATE/DELETE paths in `AuditLog`.

## 8. Deployment (v0.1)

Single box: `python -m sentinel.receiver --port 8080` (+ SQLite file). Env: `TYPESAFE_API_KEY`, `SENTINEL_WEBHOOK_SECRET` (optional), `PD_EVENTS_URL` (default `https://events.pagerduty.com/v2/enqueue`), `SENTINEL_DB` (default `./sentinel.db`), `SENTINEL_SHADOW` (0/1). Production upgrade path (documented): gunicorn/FastAPI or ASGI receiver, Postgres, Redis correlator, KMS envelope — module boundaries are already drawn for the swap.

## 9. Testing strategy

- `unittest` (stdlib), run via `python -m unittest discover tests`.
- **The kill-the-client test** (`test_gate.py`): mock client raising `JevOverloaded`/`JevTimeout` on every call → assert every alert returns `passthrough` and the forwarder relays the original payload. This test failing is a release blocker.
- Repeatability probes (flip <2%, shuffle <3%) run as part of the eval harness, not unit tests (they need the mock's flip injection).
- Receiver tests use real HTTP against a loopback `ThreadingHTTPServer` in-process.

## 10. Honest limitations (v0.1)

1. Synthetic eval proves plumbing, not production accuracy — real calibration needs 50–300 of the customer's own labels (research finding, baked into the tuner + shadow protocol).
2. Jev is non-deterministic (1.3–2.2% flips, no seed) — mitigated by audit (input hash + model version), never eliminated.
3. Single-box, single-tenant, SQLite — the SaaS hardening (KMS, Postgres, Redis, multi-tenancy) is designed, not built.
4. TypeSafe dependency: dynamic rate limits, no SLA, US-only hosting, MCA forbids distilling outputs. Fail-open + BYOK blast isolation mitigate; the fine-tuned-encoder exit is a designed future, not v0.1.
5. Threshold defaults (C_FP=$100, C_FN=$50k) are founder-grade estimates — the tuner + first shadow pilots replace them with measurements.
6. No LLM adjudicator in v0.1 (deliberate — hybrid cost/accuracy must be measured on gray-zone labels first).
