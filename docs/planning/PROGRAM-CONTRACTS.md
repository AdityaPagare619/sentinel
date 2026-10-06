# FULL-BUILD PROGRAM — INTER-TRACK CONTRACTS

Branch: `program/full-build` (from `main` @ 8c0893b). All track lanes branch from here and merge back via reviewed PRs.
Coordinator owns merge sequencing. Lanes never block on each other — build against these contracts.

## C1 — Operator auth (Track 1 provides; all consume)

- Every `/api/*` request requires `Authorization: Bearer <operator-token>` → 401 JSON `{error: "unauthorized"}` without it.
- EXEMPT (unauthenticated, per architecture law): liveness `/api/v1/health/live` and readiness `/api/v1/health/ready` ONLY. Everything else — including `/api/v1/integrations/keys`, `test-page`, `/api/simulate`, `/_stream` — is authenticated.
- Token: per-install, generated once at first boot (`secrets.token_urlsafe(32)`), stored server-side only (file, 0600, never logged, never in responses). Operator pastes it once into the console; console stores it in memory/session scope (never localStorage).
- CORS: default allowlist = `{https://adityapagare619.github.io}`. `*` requires explicit `--cors-origins=*` AND logs a loud startup warning. Empty string = CORS off (reverse proxy owns it).
- UI contract (Track 8): on 401 the console shows "operator sign-in required" state; token entry is a single paste field.

## C2 — Judge adapter (Track 2 provides; Tracks 6, 7, 8 consume)

```python
JudgeResult = {
  "judgment": "page" | "suppress",   # gate's final call
  "confidence": float,               # ORDINAL 0..1 — never presented as probability
  "latency_ms": float,
  "source": "jev" | "timer" | "deterministic",  # who actually decided
  "model_version": str,
  "cost_usd": float,                 # 0.0 when source != "jev"
}
```

- Sim resolves the real Jev key via `resolve_jev_key()` (user store → `TYPESAFE_API_KEY` env). Key NEVER leaves the server process: never in logs, responses, SSE, or the browser.
- Spend: `GET /api/v1/jev/spend` → `{session_usd, budget_usd, calls, blocked: bool}`. Budget from `--jev-budget-usd` (default e.g. 0.50). When exhausted → `blocked: true`, judge degrades to deterministic (no Jev calls), UI shows "Jev budget exhausted — deterministic mode".
- Race honesty: timer-wins are real. `source: "timer"` means the page happened BECAUSE the judge was too slow — the UI must say so.
- Absent key → FakeJev (deterministic mock), honestly labeled. Never fake-real.

## C3 — Audit record (Track 3 provides; Tracks 7, 8 consume)

- Disposition record gains `mode: "live" | "shadow"`. The `reason` field is ALWAYS the causal reason (`kill_switch`, `duplicate`, `flap_debounce`, …).
- Shadow path: writes `mode: "shadow"`, NEVER rewrites `reason`. The `reason` field is write-once by the decisioning path only.
- Kill switch: `POST /api/v1/safety/kill` (C1-authed) flips immediately. Re-arm = separate deliberate action (`POST /api/v1/safety/rearm` + explicit confirmation), every flip/re-arm in the audit log with actor + timestamp.
- Drill: `scripts/kill_drill.py` measures flip → forwarder-halt from decision-log timestamps and prints the measured ms. No <5s claim without a drill artifact.

## C4 — Rotation (Track 4 provides; Tracks 1, 2 consume)

- Key store: each secret name holds `{primary, secondary: optional, generation: int}`. Verification accepts primary OR secondary (dual-accept). Rotation = add secondary → verify → promote → retire, each step audit-logged.
- Generation counter also gates break-glass: a token trusted on signature alone is unrevocable — all privileged tokens carry the generation.
- Applies to: operator token (C1), webhook HMAC secrets, BYOK stored keys.

## C5 — Jev advisory directions (Track 5)

- DESIGN DOC FIRST, coordinator-reviewed before code. Each direction: `{name, input, output, control_analysis, cost_per_call_usd, timeout_ms, fallback}`.
- IRON RULE: advisory outputs are enrichment attached to records. The gate NEVER reads them for page/suppress. Each direction's control analysis states what it can NEVER do (no paging-path write access, ever).
- Candidate directions: plain-language suppression explanations · storm summarization · retrieval-enriched evidence bundles · RCA hypotheses (advisory) · policy-change impact preview · triage suggestions.

## C6 — Sim scenarios (Track 6 provides; Tracks 7, 8 consume)

- Scenario: `{name, seed, duration_s, profiles: [...]}` under `platform/server/scenarios/`. Deterministic seeds. Profiles: `normal-day`, `bad-deploy`, `infra-incident`, `storm-surge`.
- The sim drives the REAL pipeline (receiver → correlator → race → gate → forwarder). Pages sink to FakePD. No parallel fake pipeline.
- Scenario manifests are versioned; Track 7's scale test references them by name.

## C7 — Validation (Track 7)

- Owns `docs/validation/ACCEPTANCE.md` (written FIRST on its branch): kill-switch drill measured · race outcomes (judge-wins / timer-wins / overload) all exercised · suppression correctness sampled vs gate rules · scale test (≥2,000 problems through the real pipeline; UI no-collapse criteria) · all 9 falsifiers re-run · P0 attack chain re-walked and closed.
- Nothing merges to `main` without Track 7's sign-off on the program branch.

## Merge sequence (coordinator-owned)

T1 → T4 (rebases on T1's key store) → T3 → T2 → T6 → T5 → T7 final sign-off → T8 after Aditya's UI verdict.
Lanes build in parallel against the contracts above; the sequence governs MERGES, not work.
