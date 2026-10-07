# Sentinel — Sunday Go/No-Go Checklist (Tripwire)

**When:** Sunday 4 Oct 2026, 20:30 IST sharp. **Decider:** Petu.
**Checklist owner:** Tripwire. **Delivery:** 21:00 IST iff GO.

This is the executable form of ROAD_TO_LAUNCH.md §2 and WAVE_PLAN.md §7
(exit bars 1–10). Every row is written so a tired human can run it at 20:30
with no interpretation. If a row cannot be executed as written, the row is RED.

## 0. Setup (do once, before 20:30)

- Demo machine: this VM. Repo: `AdityaPagare619/sentinel`, branch state = `main`
  HEAD as of the 17:00 merge freeze.
- **Port convention (pinned):** engine receiver `8080`, dashboard/platform tier
  `8090`, stub PagerDuty endpoint `8099`. If the platform lane changes these, it
  updates this file before Sun 14:00 — owner: dashboard lane.
- Fresh local-gate signal required: `scripts/ops/pre-pr-gate.sh` must print
  `GATE RESULT: GREEN` on `main` HEAD. Bar 10 is meaningless until the gate
  itself runs green. (GitHub Actions CI was removed by Aditya's order
  2026-10-03 — `.github/workflows/` does not exist; `ops/ci-repair.md` is
  the history, not a repair ticket.)
- No merges after 17:00 except stop-the-line fixes. Verify:
  `git log --since="2026-10-04 17:00" --oneline origin/main` → must be empty
  (or contain only the logged stop-the-line fix).

## Phase A — the non-negotiables (run FIRST; any RED here = automatic NO-GO, stop)

### Bar 10 — `main` green (owner: Tripwire)

| | |
|---|---|
| **Verify** | The local gate is green on `main` HEAD: secrets-grep, full suite, kill-the-client invariant, boot smoke, ops-scripts, config schemas — all green. |
| **Command** | `bash scripts/ops/pre-pr-gate.sh` on `main` HEAD → `GATE RESULT: GREEN`. Record the sha (`git rev-parse HEAD`) with the result. There is no remote CI: do not wait on `gh run`. |
| **Pass** | Gate GREEN on the current `main` HEAD; the recorded sha matches `git rev-parse origin/main`. Re-verify at 21:00 before delivery. |
| **Sign** | Tripwire: ______ |

### Bar 7 — Do-no-harm: kill-the-client green (owner: Tripwire)

| | |
|---|---|
| **Verify** | THE LIVE KILL (see §Ritual). Mid-demo, kill the Jev client: every alert — including synthetic SEV1s — must passthrough, zero dropped, audit row per alert. Then kill the platform-tier process: paging path must be unaffected. |
| **Command (kill-the-client)** | `SENTINEL_MOCK=1 SENTINEL_DB=/tmp/sentinel-gono.db PYTHONPATH=src python -m sentinel.receiver --port 8080 &` — run the demo storm — then execute Drill 1 from `ops/fault-injection-plan.md` (hermetic: real `Gate` + exploding client, 30 alerts incl. critical-severity inputs, client dies mid-stream; inline heredoc, no extra files). |
| **Command (platform-tier death)** | With the full stack up: `kill -9 $(cat /tmp/sentinel-platform.pid)` mid-storm; fire 10 more alerts via `curl`; confirm all forwarded. Restart the tier; confirm it resumes from the WAL. Drill 4. |
| **Pass** | Kill-the-client: 30/30 passthrough, 30/30 forwarded byte-identical, 30/30 audit rows with `reason=error:*`. Platform death: 10/10 alerts paged during the outage; dashboard dark is expected and honest; zero paging-path errors; restart loses zero rows. |
| **Sign** | Tripwire: ______ |

### Bar 8 — Hot-path latency: platform adds zero ms (owners: Forge + Tripwire)

| | |
|---|---|
| **Verify** | Engine p95 alert-to-forward latency with the platform tier RUNNING vs NOT running, same box, same load. Law 1: beyond the single Jev call, the paging path gains zero milliseconds. |
| **Command** | Stub PD endpoint + 200-webhook latency probe (copy-paste block below). Run twice: platform tier down, then up. |
| **Pass** | `p95_platform <= p95_baseline * 1.05` AND absolute delta < 50 ms, N=200 each. If red → NO-GO (Law 1 violation). |
| **Sign** | Forge: ______ Tripwire: ______ |

Copy-paste latency probe (run on the demo machine):

```bash
# stub PagerDuty endpoint (constant, local — removes network noise from the comparison)
python3 - <<'EOF' &
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0)); self.rfile.read(n)
        self.send_response(202); self.end_headers()
    def log_message(self, *a): pass
ThreadingHTTPServer(('127.0.0.1', 8099), H).serve_forever()
EOF
STUB=$!
SENTINEL_MOCK=1 SENTINEL_DB=/tmp/sentinel-lat.db PD_EVENTS_URL=http://127.0.0.1:8099/v2/enqueue \
  PYTHONPATH=src python -m sentinel.receiver --port 8080 &
ENG=$!
python3 - <<'EOF'
import json, time, urllib.request
lats = []
for i in range(200):
    body = json.dumps({"routing_key": "bench", "event_action": "trigger",
        "payload": {"summary": f"bench alert {i}", "severity": "critical",
                    "source": "bench", "custom_details": {"i": i}}}).encode()
    t0 = time.perf_counter()
    req = urllib.request.Request("http://127.0.0.1:8080/webhook",
        data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()
    lats.append((time.perf_counter() - t0) * 1000)
lats.sort()
print(f"n=200 p50={lats[100]:.1f}ms p95={lats[190]:.1f}ms p99={lats[198]:.1f}ms")
EOF
# run once with platform tier DOWN, once with it UP (dashboard lane's start command),
# then compare. Cleanup: kill $ENG $STUB
```

### Bar 9 — Honest data (owner: Prism; reviewer: Oracle)

| | |
|---|---|
| **Verify** | Every dashboard view labels its data source (synthetic / shadow / production). Zero accuracy claims anywhere. The 1.3–2.2% Jev flip floor is disclosed on the dashboard (Law 3). |
| **Command** | Walk the 5 views (river, calibration, simulator, audit explorer, analytics): each must show a source label. Then `grep -rniE '\baccura(cy|te)\b\|100%\|never misses' <demo-script> <dashboard-copy-dir>` → must return nothing. Confirm the flip-floor sentence is visible in the UI. |
| **Pass** | 5/5 views labeled; grep clean; flip floor disclosed; Oracle signs the numbers (every metric has denominator + N). |
| **Sign** | Prism: ______ Oracle: ______ |

## Phase B — the feature spine (all must be green; P1 slips are Petu's logged call only)

### Bar 1 — Decision river live (owner: dashboard lane)

| | |
|---|---|
| **Verify** | River streams decisions with confidence bars during a synthetic storm; click-through shows the full decision detail. |
| **Click-path** | `http://localhost:8090/` → fire the synthetic storm → rows stream in → click any row → detail pane. |
| **Pass** | Rows stream (≥1/sec during storm); detail shows confidence bars + full probability map + data-source label. |

### Bar 2 — Calibration dashboards (owner: Oracle)

| | |
|---|---|
| **Verify** | Reliability diagram + ECE + coverage@τ per team. |
| **Click-path** | Dashboard → Calibration tab. |
| **Pass** | Every chart shows its denominator N; numbers match Oracle's calibration report (source cited in the report); no chart without an N. |

### Bar 3 — Threshold simulator (owner: calib lane; sign-off: Oracle)

| | |
|---|---|
| **Verify** | Sliders recompute projections live; simulator math ≡ tuner math. |
| **Click-path** | Dashboard → Simulator tab → drag the P(p1) threshold slider → projections update. Then: `PYTHONPATH=src python3 -m sentinel.tuner` with the same threshold values. |
| **Pass** | Simulator projection == tuner output within 1 pp on suppression rate; Oracle signs off. |

### Bar 4 — Audit explorer (owners: dashboard + Vault)

| | |
|---|---|
| **Verify** | Search + full decision records + flip-audit view. |
| **Click-path** | Dashboard → Audit tab → search `fingerprint=<one from the demo storm>` → open the row → flip-audit view. |
| **Pass** | Search returns the row; full record shows `input_sha256` + `jev_model` + probability map; flip-audit view lists the week's flips with per-flip explanations; audit rows contain zero secrets (Vault). |

### Bar 5 — Noise analytics v1 (owner: analytics lane)

| | |
|---|---|
| **Verify** | Top noisy checks, per-team load, suppression breakdown. |
| **Click-path** | Dashboard → Analytics tab. |
| **Pass** | All three panels render with denominators; suppression breakdown sums to 100% of suppressed alerts. |

### Bar 6 — Onboarding <15 min (owner: Prism)

| | |
|---|---|
| **Verify** | A human who did NOT build it goes from `git clone` to synthetic-storm-in-the-river, stopwatch running. |
| **Command** | Prism runs it live at the table (or certifies the timed run from Sun AM with the recording). |
| **Pass** | Wall clock < 15:00. If it was pre-timed, Prism re-runs the single slowest step live. |

## The ritual — what makes this go/no-go trustworthy

Other teams review fault-injection *logs* at go/no-go. We re-run the kill *live*,
at the table, before anyone votes. Trust is demonstrated, not asserted.

1. **The live kill (20:30).** Tripwire runs Bar 7's two kills live on the demo
   machine, in front of Petu. No slides, no "it passed this morning" — the client
   dies mid-demo and the paging path is watched surviving it.
2. **The adversarial chair.** One named person (default: Red Team lead) is
   assigned to argue NO-GO. GO requires the chair to say, on the record: "I
   cannot find a reason to stop the line." A silent chair is a failed ritual —
   Tripwire re-asks until there is an actual argument or an actual concession.
3. **The flip-ledger recital.** Tripwire reads aloud: this week's flip count, the
   measured rate vs the 1.3–2.2% floor, and ONE flip explained from its audit row
   (input hash, model, probabilities). Law 3 says flips are audited, never hidden —
   the recital is the proof we mean it.
4. **Two-key GO.** Every bar above has two signature lines: the building lane
   chief signs "built as specified"; Tripwire signs "verified broken-safe".
   Petu calls GO only on 10/10 two-key rows. Builders never grade their own work
   (CHARTER.md operating principle 5).
5. **Automatic NO-GO — no override, no "we'll fix it Monday":** any RED in Phase A;
   any do-no-harm law violated; any dropped page in the live kill; any accuracy
   claim found; `main` not green. P1 feature slips are the ONLY thing Petu can
   waive, and the waiver is logged in `ops/decision_log.md` before 21:00.

## After GO (21:00 delivery)

- Re-verify Bar 10 green on `main` HEAD.
- Tag the release commit; delivery is the demo + the repo at that tag.
- Tripwire files the signed checklist + fault-injection log in
  `ops/acceptance-2026-10-04.md` (new file, same PR wave or a docs PR Monday —
  Petu's call).

## Sources

- Bars: ROAD_TO_LAUNCH.md §2; WAVE_PLAN.md §7 (evidence + owners).
- Laws: PLATFORM_ARCHITECTURE.md §4 (quoted verbatim in `ops/fault-injection-plan.md`).
- CI health: `ops/ci-repair.md`.
- Engine contracts: `ARCHITECTURE.md` §3 (receiver §3.9, gate §3.6/§4, client §3.1).
