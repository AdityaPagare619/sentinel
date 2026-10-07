# RFC: Ordinality sweep — remove the last calibrated-probability presentation from the source tree

**Status:** PROPOSED by FAANG principal wave, Team 2 (AI/ML), 2026-10-07.
**Type:** 2 — reversible (deletions are in git; nothing user-facing changes today).
**Law:** AC-8c — Jev confidence is ORDINAL, never a calibrated probability:
no `%`, no `P(...)`, no ECE/reliability-diagram-as-honesty.
**Scope:** source tree on main. The four served consoles (prod `/`, `/staging/`,
`/loadtest/`, `/preview-v2/` on the public gh-pages branch) were swept 2026-10-07
and are CLEAN — zero banned strings, "Confidence is ORDINAL" stated in-band.

---

## 1. Problem

The fix wave deleted the dead v1 calibration assets from the **public** branch and
reframed `/api/calibration` to rank fidelity (AUC over p1 ranks — verified on main,
`platform/server/store.py:443`). But the **source tree** still carries the banned
presentation in live-routed code, and the build script's protection is a
copy-boundary exclusion list that does not cover the prod bundle:

| # | File:line | Finding | Honest rewording / treatment |
|---|---|---|---|
| 1 | `platform/ui/assets/views-cal.js:64,87,94` | Full banned view: "Expected Calibration Error" headline, reliability diagram, "The diagonal is perfect honesty." Routed at `#/lab?tab=calibration` (`app.js:211,217`), consumes the OLD API shape (`ece`, `bins`) — broken against the reframed backend AND banned. | DELETE the file; remove import/route/nav tab. The rank-fidelity instrument deserves a new honest view later — a UI-lane design decision, not invented here. |
| 2 | `platform/ui/assets/lib.js:153,131,162` | `gloss80()` ("When Sentinel says 80% confident, the outcome matched about X–Y%"), `stripCal()`, `calVerdict()` (ECE gates). Only consumed by views-cal.js. | DELETE (orphaned after #1). |
| 3 | `platform/ui/assets/lib.js:150,172` | `binForConf()`, `quartilesFromBins()` — probability-bin helpers. Only feed `confBar`, which is itself orphaned (no consumers). | DELETE with `confBar`. |
| 4 | `platform/ui/assets/lib.js:191-198` | `receiptLine()` renders `` ` · P(p1)=${p1.toFixed(4)}` `` in the decision receipt (`components.js:266`, river row). | Reword to ordinal: `` ` · p1=${p1.toFixed(4)} (ordinal severity score, not a probability)` `` — the raw value stays (the gate thresholds on it), the probability framing goes. |
| 5 | `platform/ui/assets/components.js:100-160,166` | `confBar()` (confidence bar over probability bins) + reliability-diagram canvas. Zero consumers outside views-cal.js; fed by the dead data shape. | DELETE both. |
| 6 | `platform/ui/data/calibration.json` | Mock fixture for `/api/calibration` in the modular console's mock mode. Checked: already in the reframed shape (`rank_fidelity`, rank `deciles`, honest `interpretation` block) and validated by `conformance.py` against the `CalibrationReport` schema. | KEEP — no change. |
| 7 | `src/sentinel/evalharness.py:229,361-362` | `ece()` + `run_eval()` writes `calibration-report.md` with ECE tables. Runnable CLI (`python -m sentinel.evalharness`, documented in README/ARCHITECTURE). | REFRAME to the same rank-fidelity instrument as the backend (`_rank_auc` over confidence ranks vs correctness) — ECE-as-honesty is banned methodology even offline. Report renamed to `judgment-fidelity-report.md`. |
| 8 | `deploy/gh-pages/build-static.py:257-265` | `_DEAD_CALIBRATION_ASSETS` exclusion covers staging only; `assemble_prod` copies ALL assets — the next prod rebuild reintroduces the banned files. | Remove the exclusion (obsolete after #1–#5); add a banned-string grep gate to BOTH `verify_prod` and `verify_staging` so the build FAILS if ordinality violations re-enter. Durable gate > copy-boundary list. |
| 9 | `platform/ui/README.md:16` | Documents `#/calibration` route (ECE/coverage/flips, reliability diagram). | Update: route removed; point at `/api/calibration` (rank fidelity). |
| 10 | `design/screens/calibration.md` | Design-era spec of the banned screen. History, not console copy. | Add a one-line supersession banner; do not rewrite history. (Already bannered by the fix wave — verified present.) |
| 11 | `platform/ui/assets/views-sim.js:156,158` | `Σ P(p1)` notation in the false-suppress watch copy. The Σ-p1 projection itself is the tuner's methodology (engine lane owns it); the UI copy must not use `P(...)` notation per AC-8c. | Reworded: "Σ reported p1 scores … — a projection, not a count of real mistakes" / "projected cost". Residual for the engine lane: whether Σ-p1-as-expectation survives the ordinality law as methodology (not just copy) — flagged, not decided here. |

**Not findings** (checked, clean): all four public HTML consoles; `platform/ui-v2/index.html`
(their source — only "Confidence is ORDINAL" + data plumbing); backend
`store.calibration` (rank deciles + AUC + Wilson CIs + the honest interpretation
block); `test_store.py:171` already asserts no `ece` in the report.

## 2. Anchors (web-verified 2026-10-07)

- **LLM-as-judge overconfidence is industry-known-bad.** A 2025 evaluation of
  verbalized uncertainty found models clustering stated confidence in 90–100%
  regardless of correctness, with ECE measured at **74.8%** — stated confidence
  almost completely disconnected from real accuracy. Tian et al. 2023: verbalized
  confidence is systematically overconfident but carries rank signal. AFCE
  (arXiv:2506.00582) treats ECE as the thing to *reduce*, never as honesty proof.
  The ordinal treatment (ranks only) is exactly what the literature supports:
  keep the ordering signal, never the absolute number.
- **Chesterton's fence on the triple lock:** `gate.py`'s probability leg consumes
  the *quantized reported* p1 as a threshold input (ADR-013), not as a belief.
  Nothing in this RFC changes the gate — only the presentation layer.

## 3. Alternatives considered

- **A. Rewrite views-cal.js as a rank-fidelity view.** Rejected: invents UI the
  UI lane owns (Aditya's design law — hand over, never prescribe); the backend
  instrument is new and its honest visualization deserves design, not a retrofit.
- **B. Keep the files, extend the build exclusion to prod.** Rejected: dead-banned
  code in the source tree is a landmine; the next person to inline the modular
  console ships the banned view. Delete at the source.
- **C. Leave evalharness alone (offline tooling).** Rejected: the law says the
  WHOLE codebase; a runnable CLI producing ECE tables contradicts the reframed
  backend instrument. Reframe, don't delete — the fidelity signal is genuinely
  useful for prompt-fragility work (1.3–2.2% flips).

## 4. Decision

Execute #1–#10 as above. Type 2: all reversible via git; no served surface changes
(the modular console is not deployed anywhere).

## 5. Pre-mortem (it is 2027-10-07 and this failed)

1. **We deleted a component the loadtest dashboard needed** → mitigated: `confBar`
   has zero consumers (verified by grep); the loadtest dashboard is generated by
   `make_dashboard.py`, not the modular console. Verify by building both after.
2. **The evalharness reframe broke `test_evalharness.py` expectations** →
   mitigated: tests updated in the same commit; the suite runs green before push.
3. **A future lane re-adds a calibration view with ECE** → mitigated: the
   banned-string grep gate in `verify_prod`/`verify_staging` fails the build.

## 6. Verification

- `grep -ri "expected calibration\|reliability diagram\|diagonal is perfect\|P(p1)=\|80% confident" platform/ src/ deploy/` → empty (modulo this RFC's own history section and `sim_runner.py` comments naming the wire-protocol variable — see note).
- Build gates: `build-v2.py::_assert_ordinality` (served consoles) and
  `build-static.py::_assert_ordinality` (prod/staging bundles) fail the build on
  the banned strings. Deliberately NOT banned: `P(SEV` — the honest
  `/api/calibration` interpretation block denies `P(SEV | p1 = x)` in words, and
  a substring ban would false-positive on the disclaimer itself.
- `node --check` (or import smoke) on edited JS modules; `python -m unittest` on
  touched suites; `build-static.py --help` smoke (full build not run — no republish
  per wave prohibitions).
- Failing-before/passing-after: the new build gate is tested by temporarily
  re-adding a banned string to a scratch asset and watching the build fail.

**Note on `P(p1)` in engine internals:** `gate.py`, `models.py`, `tuner.py`,
`sim_runner.py` use `P(p1)` as the *wire-protocol variable name* for the judge's
reported p1 value (the quantized lock input). That is engine vocabulary, not
user-facing probability presentation, and the triple lock treats it as an ordinal
threshold input. Left as-is deliberately; the ban targets presentation.
