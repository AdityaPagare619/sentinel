# ENV-MATRIX FIDELITY — dev → staging → loadtest → production

**Owner:** Team 3 platform/infra · **Date:** 2026-10-07 ·
**Lane:** `lane/faang-platform-20261007` · **Verified against live bytes**
2026-10-07 ~16:30 IST.

> One console codebase, environment as structural mode, no divergence.
> This doc verifies each surface against the matrix and rules on the
> audit's X-H.

## The matrix (standing)

| Surface | Mode | Built from | Backend |
|---|---|---|---|
| `/` | PRODUCTION — real UI, zero fixtures | `platform/ui-v2/index.html` + prod mode injection | `sentinel-platform` Vercel (`production-api`) |
| `/staging/` | SIMULATED — seeded pipeline, FakePD | same source, shipped verbatim (sim-marked) | local sim adapter |
| `/loadtest/` | load-test dashboard (evidence surface) | loadtest lane build | none (static report) |
| `/preview-v2/` | judgment preview — KEPT until Petu's flip order | pinned older build | sim adapter |

Build: `deploy/gh-pages/build-v2.py` — `/` gets `<html
data-mode="production" data-backend="…">` + PRODUCTION chrome injected;
`/staging/` ships verbatim from the sim-marked source; the script
**asserts** the mode tags (fails the build on mislabeling). This is a
substring/tag assert — it catches a mislabeled build, not a dishonest
one (UI backstage note).

## Live verification (2026-10-07)

| Surface | Expectation | Live bytes | Verdict |
|---|---|---|---|
| `/` | static PRODUCTION chrome, `data-mode="production"`, no sim bytes | `<html data-mode="production" data-backend="https://sentinel-platform-…">`; red PRODUCTION band static in HTML; `aria-label="Simulated mode"` count = 0 | ✅ matches matrix |
| `/staging/` | sim band, seed shown | `aria-label="Simulated mode"`, SIMULATED band, seed 20261006 | ✅ matches matrix |
| `/loadtest/` | load-test dashboard + honesty caveats | title "Millions/Day Validation"; "did NOT prove"/FakePD caveats present | ✅ matches matrix |
| `/preview-v2/` | live (suspension in force) | 200, older console build | ✅ intentionally kept — Petu's flip order pending |
| backend | 200 / 401 / 204+ACAO | `/health/live` 200 + `X-Sentinel-Deployment: production-api`; `/ops/health` unauth 401; lowercase-origin preflight 204 + exact ACAO; evil origin no ACAO | ✅ matches matrix |

Root vs staging differ only in the mode injection (verified by diff:
`<html>` tag + band div) — one codebase, structural mode, no divergence.

## RULING on X-H ("/" matrix promise vs dual-mode sim bytes)

**The shipped bytes were wrong; the fix wave fixed the bytes; the
matrix stands.**

The audit's X-H found `/` shipping the sim build (sim-band markup,
`aria-label="Simulated mode"`) flipped to prod chrome by JS post-token
— first paint said SIMULATED on the production URL, and the design
mislabeled when JS/token failed. The fix (fix wave, 2026-10-07):
`build-v2.py` now injects static PRODUCTION chrome into `/` at build
time (`data-mode="production"`, red band, honest aria-label), asserted
by the build script. Live bytes confirm: zero sim markers on `/`.

The matrix was right; the build was wrong; the build is fixed. No
amendment to CONSOLE-PARITY.md needed on this point.

## Residuals (not mine to fix — flagged to the owning lanes)

1. **Prod band text** (`/`: "Live platform backend — syncing forwarder
   & judge state…"): the tier hosts no forwarder; ops/health reports
   `forwarder.identity: "fakepd"`. The text describes the console's
   sync action (it reads those sections from ops/health), which is
   defensible — but after sync, the fakepd-in-prod state needs the
   loud banner the audit's UI P0 requires, not a quiet label.
   → UI team (Team 2).
2. **`/staging/` contains the literal string `data-mode="production"`**
   once — inside help text (`?mode=production (or <html
   data-mode="production">)`), not on any element. Harmless; noted so
   nobody "fixes" it into a bug.
3. **`/preview-v2/` is 233 lines behind staging** (audit UI C6) and
   stays live per the suspension note. The fork risk is real but
   removal is Petu's ordered action, not a lane's. The suspension note
   in CONSOLE-PARITY.md is the control; it must not be deleted
   without his order.

---

*Lineage: CONSOLE-PARITY.md (the matrix); the audit's X-H (the
contradiction); principal-governance (state the ruling in writing,
then commit to it).*
