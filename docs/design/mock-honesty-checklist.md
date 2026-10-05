# Mock-honesty invariant checklist (R-20, ui-2)

**Status:** DRAFT (pre-T+0 prep; v0 per 12H-PLAN §1 ui-2; frozen at T+9) ·
**Date:** 2026-10-05 IST · **Lane:** prep-ui-qa (doc only — no `src/` changes)
**Why now (F4 verdict):** the staging demo keeps using mocks. If the mock-honesty
invariants are not written *now*, the demo lies in-band — which is exactly the
W1 failure shape the §9.2 honesty invariants were built to kill.
**Binding skills:** execution-doctrine (honesty before polish; no fake demos);
principal-mindset §3 (proxy-trap audit — freshness `live` is a proxy claim that
must rank-order with the truth); OPERATING-RULES §3.4 (honesty law: no
simulated result presented as measured).
**Grounded in:** `platform/ui/assets/freshness.js` (four-state vocabulary
live/cached/stale/degraded; `FRESHNESS_STATES`), `components.js`
(`derivedMark` kinds: `derived` / `reconstructed` / `simulated`), `api.js`
(`Stream._startMock`), `views-river.js` (`paintTapeFresh`), `honesty.test.mjs`
(18 §9.2 invariants), `antislop.py` (mechanical §8 checks).

---

## The invariants (each is a testable law, not a tone)

### M1. Mock replay NEVER maps to freshness `live`
- **Law:** `mock` (replay of recorded `stream-events.jsonl` tapes) maps to
  `cached` — mirroring the static snapshot treatment (`cached — not live`) —
  with the tape badge carrying an in-band **"recorded replay — not live"**
  label and `as-of` set to the recording time. It never maps to `live`,
  `stale`, or `degraded` (those are claims about a real source).
- **The bug it kills** (`prism-ui.md` A4-1): `Stream._startMock()` sets stream
  state `'live'` while replaying canned recordings; `streamFreshness` then
  computes the tape badge as `live` with a *fresh* as-of. The page-level
  `◈ MOCK DATA` banner exists, but §4.4's own text says screen-level labeling
  does not satisfy point-of-display law — and here the freshness badge (the
  B1 health channel) actively contradicts the banner.
- **Test:** new invariant in `honesty.test.mjs` — assert no code path in mock
  mode produces freshness state `live` (behavioral: drive `Stream` in mock
  mode, assert `streamFreshness` output ∈ {`cached`}; static: scan
  `api.js`/`freshness.js` for the mock-mode branch assigning `'live'`).
- **Why `cached`, not a new state:** the four-state vocabulary is closed
  (contract discipline — `principal-governance`: unforgiving API design).
  Adding a fifth state for mock breaks every badge consumer and the visual
  contract the severity/freshness language depends on. `cached` means "served
  from a store while not current" — a recording is exactly that.

### M2. Fabricated payloads carry the reconstruction mark at point of display
- **Law:** any client-fabricated `alert` payload (mock mode's
  `getDecision` title-regex fabrication for non-1042 rows — `severity_in:
  'unknown'`, nulls — `api.js`) renders in the drawer with
  `derivedMark('reconstructed')` **at the section**, and the section note
  "the raw payload is untouched above the viewer" is rewritten to be
  conditional — it is false in mock mode today.
- **The bug it kills** (`prism-ui.md` A4-2): the fabricated bag renders
  through `renderPayload` with no reconstruction mark — the exact W1 shape
  (derived data rendered as if measured) surviving inside the screen with the
  heaviest §4.4 machinery everywhere else.
- **Test:** new invariant in `honesty.test.mjs` — behavioral: mock
  `getDecision` output for a non-1042 row, assert the rendered drawer HTML
  contains the reconstruction mark within the payload section; static: assert
  the section-note copy is mode-conditional (no unconditional "untouched"
  claim).
- **Positive control already in the tree:** mock-mode *simulate* output is
  already labeled "NOT tuner math" in provenance (`views-sim.js`) — M2
  extends that existing standard to payloads.

### M3. Screen-level labeling does NOT satisfy point-of-display law
- **Law:** the `◈ MOCK DATA` page banner is necessary but not sufficient.
  Every derived/simulated/reconstructed *value* carries its mark where it is
  displayed (the §4.4 point-of-display law the UI otherwise enforces well —
  `derivedMark` exists for exactly this). Buyer decks screenshot past banners
  (`prism-ui.md` pre-mortem); marks travel with the value.
- **Test:** extension of INV-2's static scan — every call site that fabricates
  or derives a displayed value must be accompanied by a `derivedMark` /
  `simulated` label at the display site. (Enumeration of the fabrication
  sites is M4.)

### M4. The fabrication-site registry (closed list, reviewed)
Every place the client invents data rather than receiving it, enumerated so
the phantom-scan and INV-2 can be complete:
1. `Stream._startMock()` — replay of `stream-events.jsonl` (covered by M1).
2. `getDecision` mock-mode alert fabrication (covered by M2).
3. Mock-mode `simulate` — already labeled ("NOT tuner math"); the registry
   records it as the positive control.
4. Any *future* fabrication site must be added to this registry in the same
   PR that creates it, with its display mark — a site without a mark is a
   review-blocker (OPERATING-RULES §4.1: author ≠ reviewer).

---

## `antislop.py` phantom-scan extension — seed

The current scan (§8.9 rule 5: "no phantom interactivity — no 'coming soon' /
dead controls in copy") covers *copy*. R-20 extends it to **rendered-but-inert
controls**. Seed patterns for the extension (to be implemented by the Phase-5
lane; this is the design input, not code):

1. **Inert-copy patterns** (regex over rendered copy in `components.js` /
   `views-*.js`): `/demo build/i`, `/no page sent/i`, `/coming soon/i`,
   `/not implemented/i`, `/placeholder/i` — any hit is a merge-blocker in a
   `live` DATA_MODE build.
2. **Handler-body scan**: click handlers whose body only replaces the control
   with inert text (e.g. `textContent = "demo build: no page sent"`) — the
   appeal button's current shape. Pattern: handler assignments on `button`
   elements whose function body contains no `fetch`/`Data.` call and no
   `dispatchEvent` — inert by construction.
3. **Build-conditional visibility assertion**: the appeal control must not be
   present in the `live` build's rendered drawer unless the appeal-endpoint
   binding exists (contract-driven visibility, per `r20-appeal-options.md`
   option (a)). Static form: assert the drawer template gates the control on
   the binding check; behavioral form: render the drawer in `live` mode
   without the binding, assert no appeal button in the DOM.
4. **Freshness-path scan** (supports M1): assert no branch of
   `streamFreshness` / the mock stream path assigns the `'live'` state when
   `Data.mode` is a replay mode.

**Seed, not spec:** the implementing lane extends these patterns after reading
the actual mock-mode code paths (OPERATING-RULES §2.1: tools first — claims
about the repo are verified against the repo). The seed's job is to name the
pattern class so the scan isn't re-invented per PR.
