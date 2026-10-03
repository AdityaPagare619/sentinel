# Sentinel Prism UI — `platform/ui/`

The SRE decision instrument: five screens, static HTML/CSS/JS, **no build step**.
Serve this directory with any static server; the API lane serves it at `/`.

```bash
cd platform/ui && python3 -m http.server 8000   # → http://localhost:8000/#/start
```

## Screens (hash routes — every state is a deep link)

| Route | Screen |
|---|---|
| `#/start` | Onboarding — beat-0 honesty contract → key → source → storm → why → tune |
| `#/river` | Decision river — live tape, filters, SSE, gap markers, detail drawer |
| `#/calibration?team=data` | Calibration — ECE/coverage/flips, reliability diagram, guided read |
| `#/simulator?team=data` | Threshold simulator — 5 ThresholdSet sliders, live repricing, policy-diff export |
| `#/audit?fpr=…` | Audit explorer — search, flip timelines, noise strip, audit-pack export |

Deep-link grammar is shared: `team=`, `action=`, `reason=`, `sev=`, `fpr=`,
`in=<input_sha256>`, `q=`, `last=1h|24h|7d|30d`. Command palette: `Ctrl+K` / `⌘K`.

## Data source switch

Default is **live** (`fetch('/api/…')` against the frozen contract v1.0.0).
Mock mode renders the contract mocks in `data/` — **only behind the visible
`◈ MOCK DATA` banner, never silently**:

- URL: `#/river?mock=1` (or `?mock=1`), or
- command palette → "toggle data source" (persisted in `localStorage`).

In mock mode, `POST /api/simulate` runs a **local recompute** over the mock
window (`lib.applyThresholds`) — labeled "mock-mode local recompute — NOT
tuner math" in the provenance block. Live mode uses the real tuner math.

## Drill hooks (for the fault-injection drills lane)

- `#/start?jev=dead` — DRILL 7: forces the Jev-path-dead degraded path.
  The flow degrades to the labeled recorded walkthrough; key verification
  refuses with "nothing stored"; the flip beat renders as RECORDED FLIP.
  River/drawer/receipts are unaffected (they never touch Jev — Law L5).

## Contract conformance

The client is checked against `platform/contracts/openapi.yaml` (the type
authority; mocks are scaffolding):

```bash
python3 tests/conformance.py   # endpoint coverage · mock shapes · enum mappings · envelope labels
node --test tests/lib.test.mjs # pure-function unit tests (strips, calibration math, drill-7 plan)
```

## Structure

```
index.html            shell: top bar, verdict strip, drawer, palette, mock banner
assets/tokens.css     design tokens — verbatim from design/DESIGN_SYSTEM.md §2
assets/app.css        components + screens
assets/lib.js         pure functions (mappings, strip grammar, calibration math,
                      mock simulate recompute, policy-diff export, startPlan)
assets/api.js         data layer: live ↔ mock switch, SSE w/ gap detection
assets/components.js  atomic components: chips, badges, confidence bar (§3.5),
                      reliability diagram, river row, drawer, dead states
assets/views-*.js     the five screens
assets/app.js         router + shell wiring
data/                 contract mocks, copied from platform/contracts/mocks/
tests/                conformance.py + lib.test.mjs
```

## Laws honored

L1 provenance on every automated claim · L2 every view labels its data
source · L3 no accuracy marketing (calibration/coverage/flips only) ·
L4 blameless by construction · L5 reads before writes (export = policy diff,
never a mutation; zero Jev on read paths).
