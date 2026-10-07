# Sentinel — SRE page-or-suppress triage middleware

**One line:** a drop-in pre-page gate in front of PagerDuty/Opsgenie. Every alert is triaged by a decision model (TypeSafe's Jev — typed questions in, typed answers out); high-confidence noise is suppressed, everything uncertain pages exactly as before. **The only autonomous action is paging a human.**

> **Honesty note (matches ARCHITECTURE.md §4):** Jev's outputs are
> group-level calibrated (routing ECE 0.096 measured — good, not perfect)
> and provably non-deterministic (1.3–2.2% flips, no seed). Raw week-1
> probabilities are **uncalibrated** — which is why the 0.90 confidence
> bar is applied only after per-org calibration fitting, and why
> suppression additionally requires the triple lock (probability bar +
> confidence bar + customer-verified allowlist). No single number is
> trustworthy alone.

Point your existing PagerDuty integration at Sentinel instead of `events.pagerduty.com`. Sentinel triages, then relays to PagerDuty. Unplug it any time — the bypass is a webhook URL swap.

## How it works

```
alert webhook → receiver → correlator → gate → forwarder → PagerDuty
                 (dedup/storm)   (Jev + policy)   (page/queue/suppress/passthrough)
                                      ↓
                                  audit log (SQLite, every decision)
```

1. **Correlator** (deterministic, no model cost): fingerprint dedup in 5-min windows, storm-collapse (>20 distinct fingerprints/min → one aggregate page), deploy change-window bypass. Duplicates never reach the model.
2. **Gate**: one Jev call per alert disposition — three parallel questions (severity, owning team, disposition action), each option with a one-line description. Then the expected-cost policy:
   - `suppress` only on a **triple lock**: P(SEV1) < 0.2% **and** confidence ≥ 0.90 **and** fingerprint on the customer-verified known-noise allowlist.
   - `page_now` when P(P1)+P(P2) > 0.30 **or** confidence < 0.50 — uncertainty pages, never drops.
   - **Any error, timeout, or `cannot_determine` → pass-through** to your existing pipeline, byte-identical. The gate fails open, always.
3. **Audit**: every decision (including pass-throughs) is logged with input hash + model version, so any outcome is explainable later.

**Shadow mode** (weeks 1–2 of any deployment): the gate logs what it *would* have done but changes nothing. The weekly shadow report — "we would have suppressed X pages and gotten Y wrong" — is the go-live evidence.

## Quickstart (no API key needed)

Everything runs against a local mock until you add a real key.

```bash
cd ~/workspace/jev-builds/sentinel
export PYTHONPATH=src

# 1. Run the test suite
python3 -m unittest discover -s tests

# 2. Tune thresholds on synthetic labeled data
python3 -m sentinel.synthetic --n 2000 --seed 7 -o labels.jsonl
python3 -m sentinel.tuner --labels labels.jsonl -o thresholds.json

# 3. Run the eval harness (calibration report)
python3 -m sentinel.evalharness --n 2000 --seed 7 -o judgment-fidelity-report.md

# 4. Start the receiver (mock Jev — set SENTINEL_MOCK=1)
SENTINEL_MOCK=1 python3 -m sentinel.receiver --port 8080

# 5. Send a test alert (PagerDuty Events API v2 shape)
curl -s localhost:8080/v2/enqueue -H 'Content-Type: application/json' -d '{
  "routing_key": "test-key",
  "event_action": "trigger",
  "payload": {"summary": "CPU > 95% for 10m", "source": "prometheus",
              "severity": "critical", "component": "api-web",
              "custom_details": {"env": "prod", "region": "us-east-1"}}}'
```

## Going live (BYOK)

```bash
export TYPESAFE_API_KEY="ts_..."      # your TypeSafe key — never hardcode it
export PD_ROUTING_KEY="..."           # PagerDuty integration key to relay pages to
export SENTINEL_DB="./sentinel.db"
# required: SENTINEL_WEBHOOK_SECRET (≥16 chars; timestamped HMAC for /webhook/generic —
#           startup refuses without it), PD_EVENTS_URL, SENTINEL_SHADOW=1 (shadow mode)
PYTHONPATH=src python3 -m sentinel.receiver --port 8080
```

Then point your PagerDuty integration's Events API endpoint at `http://your-host:8080/v2/enqueue`. The receiver mirrors PagerDuty's success response, so existing integrations don't break.

| Env var | Required | Purpose |
|---|---|---|
| `TYPESAFE_API_KEY` | yes (live) | Your TypeSafe key. Without it, use `SENTINEL_MOCK=1` for local dev |
| `SENTINEL_MOCK` | no | `1` = mock Jev client: full pipeline runs, every decision fails open to passthrough, no network calls |
| `PD_ROUTING_KEY` | yes (live) | PagerDuty integration key pages are relayed to |
| `SENTINEL_DB` | no | SQLite path (default `./sentinel.db`) |
| `SENTINEL_WEBHOOK_SECRET` | yes (prod) | Timestamped HMAC-SHA256 for `POST /webhook/generic`: send `X-Sentinel-Timestamp: <unix seconds>` + `X-Sentinel-Signature: sha256=<hex>` where hex = HMAC-SHA256(secret, `<ts>.<raw body>`); \|server_now − ts\| ≤ 300s. Startup refuses when unset (no fail-open default); legacy timestamp-less signatures → 403 |
| `SENTINEL_WEBHOOK_ONBOARDING` | no | `1` = the only fail-open path: unsigned/legacy deliveries accepted loudly (per-request WARNING + `webhook_auth_bypassed` metric, CRITICAL boot warning, `/healthz` shows `webhook_auth_fail_open=true`). Onboarding migration window only — disable after sender migration |
| `PD_EVENTS_URL` | no | Override PagerDuty endpoint (default `https://events.pagerduty.com/v2/enqueue`) |
| `SENTINEL_SHADOW` | no | `1` = shadow mode: log dispositions, change nothing |

## Endpoints

- `POST /v2/enqueue` — PagerDuty Events API v2 shape (`routing_key`, `event_action`, `payload.*`)
- `POST /webhook/generic` — `{service, check, title, severity, labels{}, metric{}}`, timestamped HMAC signature **required** (see env table; unsigned/malformed/stale/legacy → 403)
- `GET /healthz` — `{"ok": true}`

## The math (why the thresholds are what they are)

Suppressing a real SEV1 costs ~$50,000 (SLA + revenue + incident overhead); a false page costs ~$100 (lost sleep + debugging). Suppression is rational only when P(SEV1) × $50,000 < $100, i.e. **P(SEV1) < 0.2%** — and even then only with confidence ≥ 0.90 and a customer-verified allowlist entry. Both cost figures are org-tunable (`tuner.py --c-fp/--c-fn`); the 0.2% bar is re-derived per org, never asserted.

Per-decision cost: ~1,000 input tokens × $0.042/M = **$0.000042**. A mid-size org (4,330 alerts/day) spends ~$66/yr on inference.

## Project layout

```
src/sentinel/
  client.py      System-One wire client (TypeSafe + clone wire format via base_url) + mock
  models.py      Alert, Disposition, DecisionRecord, Thresholds
  questions.py   the 3 Jev questions (severity / owning team / disposition)
  state.py       state shaping with token budget
  correlator.py  fingerprint dedup, storm collapse, change-window bypass
  gate.py        Jev call + expected-cost policy + fail-open (the heart)
  forwarder.py   relay to PagerDuty/Opsgenie
  audit.py       SQLite decision log (Postgres-compatible schema)
  receiver.py    HTTP ingress
  tuner.py       CLI: labels → thresholds + savings projection
  evalharness.py CLI: metrics + calibration report
  synthetic.py   seeded synthetic alert generator
tests/           unittest suite (stdlib)
ARCHITECTURE.md  full design doc — read this before changing the policy
```

## Honest limitations (read before trusting it)

1. **Synthetic eval proves plumbing, not production accuracy.** Real calibration needs 50–300 of *your own* labeled alerts. Run shadow mode first; the tuner + shadow report exist for exactly this.
2. **Jev is non-deterministic** (1.3–2.2% answer flips on identical inputs, no seed parameter). Every audit row stores the input hash + model version so flips are explainable, never mysterious — but they are not eliminable.
3. **Confidence is group-level calibration**, not a per-decision guarantee. The triple lock (probability bar + confidence bar + allowlist) exists because no single number is trustworthy alone.
4. **TypeSafe is a startup dependency**: dynamic rate limits, no SLA, US-only hosting, no SOC 2; its terms forbid distilling model outputs and cap its liability at ~$66. The gate fails open on any provider error — a TypeSafe outage degrades Sentinel to a no-op, never to dropped pages.
5. **Single-box v0.1**: SQLite, in-memory correlation, one API key per deployment. The SaaS hardening (KMS envelope encryption, Postgres, Redis, multi-tenancy) is designed in ARCHITECTURE.md §8, not built.
6. **No LLM adjudicator in v0.1** — deliberate. Hybrid cost/accuracy must be measured on your gray-zone labels before it's a priced tier, not a guess.
7. Threshold defaults (C_FP=$100, C_FN=$50k) are estimates — the first shadow pilots replace them with measurements. If measurements disagree with the doc, the measurements win.
