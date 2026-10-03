# Sentinel — Fault-Injection Plan (Tripwire)

**Purpose:** prove the do-no-harm laws empirically, not by argument.
**When:** Sat Wave C (first full run), Sun AM (hardening re-run), and **live at
the Sun 20:30 go/no-go** (the ritual — see `ops/acceptance-sunday.md`).
**Owner:** Tripwire. **Rule:** a failed drill becomes a Sun AM task, not a debate.

## The laws under test (PLATFORM_ARCHITECTURE.md §4, quoted verbatim)

- **Law 1 — The platform never adds latency to the paging path.** Separate
  process; the audit log is the only bridge; ZERO Jev calls on any read path;
  storm-collapse fires one Jev call per storm; shed *dashboard* traffic first —
  never paging traffic. The receiver never returns 5xx for a triage failure.
- **Law 2 — Fail-open always; uncertainty pages.** If the platform tier dies,
  alerts still flow — the tiers share nothing but the audit file. Any Jev
  error/timeout → passthrough. Low confidence / `cannot_determine` / malformed
  input → the existing pipeline, byte-identical. The kill-the-client test stays
  a release blocker.
- **Law 3 — Zero *harmful* errors is the target; flips are audited, never
  hidden.** Every decision row carries `input_sha256` + `jev_model` + full
  probability maps. Jev flips 1.3–2.2% of repeat calls (no seed exists); the
  floor is disclosed, not engineered away.
- **Law 4 — Honest scope, stated on the tin.** Every view labels its data
  source (synthetic / shadow / production).

## Test rig (all drills)

```bash
# engine in mock mode, throwaway DB, stub PagerDuty endpoint on :8099
SENTINEL_MOCK=1 SENTINEL_DB=/tmp/sentinel-fault.db \
  PD_EVENTS_URL=http://127.0.0.1:8099/v2/enqueue \
  PYTHONPATH=src python -m sentinel.receiver --port 8080 &
ENG=$!
# stub PD: records every forwarded payload to /tmp/sentinel-fault.fwd
python3 ops/stub-pd.py --port 8099 --out /tmp/sentinel-fault.fwd &
STUB=$!
# synthetic alerts (Ledger's generator; Pager: must include synthetic SEV1s)
PYTHONPATH=src python3 -m sentinel.synthetic --n 50 --seed 11 -o /tmp/storm.jsonl
```

Helper scripts referenced below (`ops/fire.py` — webhook burst sender;
`ops/stub-pd.py` — recording stub PagerDuty endpoint; `ops/stub-jev529.py` —
always-529 stub Jev endpoint with call counter) are Tripwire-owned and land as a
follow-up commit once the engine PR merges to `main` (they import `sentinel.*`,
which arrives with that merge). Until then, the inline `python3` heredocs in
each drill are the executable form.

`ops/stub-pd.py` is a 30-line stdlib recorder (POST → append raw body to the out
file → 202). If it doesn't exist yet, the adapters lane writes it Sat Wave B —
Tripwire does not write feature code (TEAMS.md), but Tripwire defines its
contract: *record raw bytes, reply 202, never fail.*

Cleanup after every drill: `kill $ENG $STUB`; archive `/tmp/sentinel-fault.*`
into `ops/fault-injection-log-2026-10-04.md` (append; the log is the evidence).

---

## Drill 1 — Kill-the-Jev-client mid-demo

**Objective:** Law 2 — with the Jev client dead, every alert passthroughs, zero
pages dropped, every decision audited. (Automated twin:
`TestKillTheClientReleaseBlocker` in `tests/test_gate.py` — release blocker.)

**Run:**

```bash
# 1. demo storm flowing on mock (client alive)
python3 ops/fire.py --port 8080 --in /tmp/storm.jsonl --limit 10   # all triaged normally
# 2. KILL the client mid-stream: swap the gate's client for an exploding one.
#    Hermetic version (no network): drive the real Gate directly —
PYTHONPATH=src python3 - <<'EOF'
from sentinel.gate import Gate
from sentinel.models import Alert, Thresholds
from sentinel.audit import AuditLog
from sentinel.client import JevTimeout

class DeadClient:
    def ask(self, *a, **k): raise JevTimeout("client killed mid-demo")

gate = Gate(client=DeadClient(), thresholds=Thresholds(),
            allowlist=set(), audit=AuditLog("/tmp/sentinel-kill.db"))
alerts = [Alert(alert_id=f"kill-{i}", received_at="2026-10-04T20:30:00+00:00",
                fingerprint=f"fp-{i % 5}", service="api", check="cpu_high",
                severity_in="critical", title=f"kill drill alert {i}",
                source="pagerduty") for i in range(30)]
results = [gate.evaluate(a, {}, {}, {}) for a in alerts]
actions = [d.action for d, _ in results]
assert set(actions) == {"passthrough"}, f"NOT fail-open: {set(actions)}"
assert all("error:" in d.reason for d, _ in results), "reasons must name the error"
rows = AuditLog("/tmp/sentinel-kill.db")  # reopen & count below
print(f"DRILL 1 PASS: {len(results)}/30 passthrough, e.g. reason={results[0][0].reason}")
EOF
# confirm 30 audit rows landed (table: decisions — audit.py schema):
python3 -c "
import sqlite3
n = sqlite3.connect('/tmp/sentinel-kill.db').execute('select count(*) from decisions').fetchone()[0]
assert n == 30, f'audit rows: {n}, expected 30'
print(f'audit rows: {n}/30')"
# 3. Full-stack version (optional, needs network): real client, dead endpoint —
TYPESAFE_API_KEY=dummy PD_EVENTS_URL=http://127.0.0.1:8099/v2/enqueue \
  PYTHONPATH=src python - <<'EOF'
# point SystemOneClient at http://127.0.0.1:9/ (discard) -> connection refused
# -> retries x3 within budget -> JevTimeout -> passthrough. Assert via receiver.
EOF
```

**Expected (Law 2):** 30/30 `passthrough`; every reason `error:timeout`-family;
30/30 forwarded byte-identical to the stub; 30/30 audit rows written (the audit
write is inside the never-raises path — `gate.py`: "audit row is ALWAYS written,
even on error / passthrough"); zero exceptions escape `evaluate`.

**Failure looks like:** any disposition other than `passthrough`; any alert with
no audit row; any exception propagating to the caller; any payload not forwarded
(the company-ending bug: a gate that drops pages on error). Also fails if the
drill needs the real TypeSafe API to demonstrate — the hermetic version must pass
with zero network.

---

## Drill 2 — 529 storm (expected: storm-collapse, one call per storm)

**Objective:** Law 1(d) — under a Jev outage storm, the correlator collapses the
storm to (at most) one Jev call per storm; the paging path keeps paging via
fail-open; dashboard/read traffic is shed first.

**Run:**

```bash
# stub Jev endpoint that 529s every call + counts calls
python3 ops/stub-jev529.py --port 8101 --count-out /tmp/jev-calls.count &
J529=$!
# receiver against the 529ing endpoint (real client path, no mock):
# wire base_url to the stub per the client contract (ARCHITECTURE.md §3.1)
SENTINEL_DB=/tmp/sentinel-storm.db PD_EVENTS_URL=http://127.0.0.1:8099/v2/enqueue \
  JEV_BASE_URL=http://127.0.0.1:8101 TYPESAFE_API_KEY=dummy \
  PYTHONPATH=src python -m sentinel.receiver --port 8080 &
ENG=$!
# fire 60 alerts fast: 25 distinct fingerprints (declares a storm at 20/60s),
# then 35 more into the active storm
python3 ops/fire.py --port 8080 --in /tmp/storm.jsonl --burst 60
echo "jev calls: $(cat /tmp/jev-calls.count)  forwarded: $(wc -l < /tmp/sentinel-fault.fwd)"
kill $ENG $J529
```

**Expected (Laws 1+2):** Jev call count is O(storms), NOT O(alerts) — with one
storm declared, expect ≤ a small constant (declare + bounded retries), never ~60.
The correlator folds storm-continuation alerts without Jev calls
(`test_storm_continuation_suppresses_without_jev`). Every alert still pages
(529 → retry x3 within budget → `JevOverloaded` → passthrough). Receiver: zero
5xx responses under the burst. If the platform tier is up, its read traffic is
shed first — paging p95 must not degrade vs the no-storm baseline (reuse the
Bar 8 probe).

**Failure looks like:** Jev calls scaling with alert count (no collapse — the
load-bearing property at 40 req/s is broken); any dropped page; any receiver
5xx; storm alerts suppressed instead of passthrough (suppression requires the
triple lock — a 529 can never satisfy it); dashboard queries starving the engine
(Law 1 violation — shed dashboard first means the *dashboard* degrades, never
paging).

---

## Drill 3 — Malformed webhook payloads (expected: never 5xx, uncertainty pages)

**Objective:** Law 2 — the receiver never returns 5xx for a triage failure;
unparseable input is forwarded byte-identical; uncertainty pages.

**Run:**

```bash
# receiver up on :8080 (rig above), stub PD on :8099
for payload in \
  '' \
  'this is not json' \
  '{"hello":"world"}' \
  '{"routing_key":"x","event_action":"trigger"}' \
  '{"routing_key":"x","event_action":"trigger","payload":{"summary":null}}' \
  '{"routing_key":"x","event_action":"explode","payload":{"summary":"s","severity":"critical","source":"t"}}' \
; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8080/webhook \
    -H 'Content-Type: application/json' --data "$payload")
  echo "payload -> HTTP $code"
done
# oversized body (receiver cap: MAX_BODY_BYTES = 8 MiB)
python3 -c "print('x' * (9 * 1024 * 1024))" | curl -s -o /dev/null -w "9MB -> HTTP %{http_code}\n" \
  -X POST http://127.0.0.1:8080/webhook -H 'Content-Type: application/json' --data-binary @-
```

**Expected (Law 2 + receiver contract):** every response is 2xx/4xx — NEVER 5xx
(the receiver docstring: "The receiver never returns 5xx for a triage failure:
every step is wrapped and the last resort is forwarding the original bytes").
Unparseable payloads are forwarded byte-identical to the stub (check
`/tmp/sentinel-fault.fwd` contains the raw inputs) with a metric incremented.
Well-formed-but-uncertain payloads → paged (uncertainty pages — never
suppressed; suppression needs the triple lock).

**Failure looks like:** any HTTP 5xx; any payload silently dropped (in the fwd
log or neither); any `suppress` disposition on malformed input; an unhandled
traceback in the receiver log; the oversized body crashing the handler instead
of a clean 413/forward.

---

## Drill 4 — Platform-tier process death (expected: paging path unaffected)

**Objective:** Law 2 (platform extension) — "if the platform tier dies, alerts
still flow — the tiers share nothing but the audit file."

**Run:**

```bash
# full stack: engine :8080 + platform tier :8090 (dashboard lane's start command)
# platform tier writes its pid to /tmp/sentinel-platform.pid on boot (contract)
python3 ops/fire.py --port 8080 --in /tmp/storm.jsonl --limit 10   # healthy baseline
kill -9 $(cat /tmp/sentinel-platform.pid)                          # THE KILL
echo "platform tier dead at $(date -u +%FT%TZ)"
python3 ops/fire.py --port 8080 --in /tmp/storm.jsonl --limit 10   # must all page
curl -s -o /dev/null -w "dashboard during outage -> HTTP %{http_code}\n" http://127.0.0.1:8090/
# restart the tier (same start command); it must resume from the WAL, losing nothing
<platform-tier start command> &
sleep 3
curl -s "http://127.0.0.1:8090/api/decisions?limit=5" | python3 -c \
  "import json,sys; rows=json.load(sys.stdin); print(f'resumed: {len(rows)} rows visible')"
```

**Expected (Law 2):** all 10 alerts during the outage paged normally —
identical latencies to baseline within noise, zero errors, zero drops. The
dashboard being unreachable during the outage is EXPECTED and honest (not a
failure). After restart: the tier resumes reading the audit WAL; all 20 alerts
visible; zero rows lost (the engine's audit write is a local WAL insert —
Law 1(b) — independent of the tier's liveness).

**Failure looks like:** any dropped/delayed page during the outage; engine
errors or crash when the tier dies (shared fate = Law 1(a) violation — the
tiers must be separate processes sharing nothing but the audit file); rows
missing after restart (WAL corruption or the tier keeping in-memory-only state);
the dashboard *appearing* healthy while dead (stale cache presented as live —
Law 4 violation).

---

## Evidence & sign-off

- Every drill appends to `ops/fault-injection-log-2026-10-04.md`: timestamp,
  drill, command, observed counts, PASS/FAIL, operator name.
- Sat Wave C: all four drills PASS on the integrated stack. Any FAIL → Sun AM
  task owned by the lane that owns the failing component (Forge: engine/path;
  dashboard lane: tier; Vault: auth-adjacent failures).
- Sun 20:30: Drills 1 and 4 re-run LIVE at the go/no-go table (the ritual).
  The log from Sat/Sun AM is supporting evidence; the live re-run is the vote.
