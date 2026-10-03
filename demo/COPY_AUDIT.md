# Prism Copy Audit — Lane 3 — 2026-10-03 ~20:30 IST (rescoped 20:45 IST)

**Authority:** Petu's ruling on the ADR debt register (PR #29, merged):
the demo shows ONLY what is BUILT. Cross-note 4: *"an
adopted-but-unimplemented design must never be described as a property of
the system — in docs, demos, or buyer conversations."*

**Scope (corrected):** the built-only principle (D1/D3) is lane 3's
audit. The threshold-simulator and calibration-dashboard findings below
were flagged for the UI lane — they are recorded here as handoff notes
with the technical evidence, but the cut/relabel decisions belong to the
UI lane and the coordinator, not to lane 3.

**Method:** grepped all demo/ assets for D1/D3 implication terms
(`suppress_precondition`, `storm aggregate`, `never suppress`, `freshness`,
`corroborat`, `simulat`); hand-read every beat's spoken copy; diffed the
simulator's math (`src/sentinel/tuner.py::classify`) against the live
gate kernel (`src/sentinel/gate.py::evaluate_policy` +
`src/sentinel/quantized.py::leg1_prob_lock`).

---

## Finding 1 — D1 (suppress_precondition not wired into the live kernel)

**D1:** wire `suppress_precondition` into the live gate kernel; collapse
`decide_like_gate` (DR-26). Source: ADR-014. Status: ADOPTED, NOT BUILT —
freshness proofs (`fit_as_of`, `allowlist_attested_as_of`) are recorded
as null on every event and are NOT an enforced suppress precondition.

**Audit result: CLEAN.** No demo copy presents freshness as an enforced
precondition:
- DEMO_SCRIPT beat 6's triple-lock copy states only the three BUILT
  locks: reported P(p1) exactly 0.00 (quantized), confidence ≥ 0.90,
  dual-attested allowlist. No "stale proof would have paged" claim.
- The engine's own `lock_evaluation` detail strings say it out loud:
  *"fingerprint in allowlist; attestation freshness not yet enforced
  (ADR-014/freshness lane)"* — the log is honest; the demoist must not
  paraphrase it away.
- The runner's demo attestations are labeled `demo-*` and documented as
  demo mechanism, not production evidence.

**Cut/relabel:** none needed. **Standing rule for the demoist:** if asked
"what happens when an attestation goes stale?", the answer is: "The
freshness precondition is adopted but not yet wired into the live kernel
(D1). Tonight the dual-attestation check runs; the freshness clock lands
next. We don't present it as live."

---

## Finding 2 — D3 (storm aggregate structural digest path)

**D3:** storm aggregate structural digest path (suppress unreachable);
regression test (all-locks-green aggregate still pages); rename folded
disposition. Source: ADR-016. Status: ADOPTED, NOT BUILT — the panel's
verified gap: a storm aggregate can currently still suppress via the
triple lock (the "storm aggregates can never suppress" line is a design
claim, not a code property).

**Audit result: CLEAN.** No demo copy mentions storm aggregates or
claims they cannot suppress. The storm runner drives individual alerts
through the real gate; no aggregate path is exercised or presented.

**Cut/relabel:** none needed. **Standing rule for the UI lane:** any
screen that renders a storm-aggregate row must NOT carry copy like
"aggregates never suppress" until D3 is built. If an aggregate row
appears, its honest label is the folded disposition from the real event.

---

## Finding 3 — Threshold simulator kernel mismatch (UI-LANE HANDOFF)

**Status:** technical finding verified by lane 3; the cut/relabel
decision belongs to the UI lane + coordinator — NOT a lane-3 ruling.

**The evidence (unchanged):** `PLATFORM_ARCHITECTURE.md` §6(c) specifies
the simulator as *"the tuner's projection logic exposed as a pure
function … (same code path as `tuner.py`, not a copy)"*. But
`tuner.py::classify` simulates the **pre-ADR-013 continuous gate**
(`if p1 < p_star …: return "suppress"`), while the **live kernel** is the
quantized ADR-013 gate (integer hundredths == 0, conf ≥ 0.90, dual
attestation until the per-org fit) + the B=2700 ms race (timer-win →
passthrough; the tuner models no race).

**Lane-3 recommendation to the UI lane:** the Sunday simulator screen
should ship ONLY IF its projection engine is the real kernel
(`evaluate_policy` + `leg1_prob_lock` per row over stored probabilities,
race modeled as timer-win → passthrough). Otherwise cut or relabel per
Petu's ruling — a relabel ("old math") would still show the buyer a
wrong tradeoff curve. The thin-data refusal (design 08 §6.2) stays as
specified regardless.

**Doc fix for the coordinator** (not lane 3's file to edit):
`PLATFORM_ARCHITECTURE.md` §6(c)'s "(same code path as `tuner.py`)"
sentence is false w.r.t. the live kernel once D1 collapses the mirror —
rewrite to point at `gate.evaluate_policy` + `quantized.leg1_prob_lock`.

---

## Finding 4 — Calibration dashboard denominators (UI-LANE HANDOFF)

**Status:** binding copy contract drafted by lane 3 for the UI lane;
enforcement belongs to the UI lane + coordinator.

Every calibration card MUST render, on the card, in the same visual
weight class as the headline number:

- Reliability bins: `n={n} · {window}` on the card, ECE with its bin
  counts. Below n=100: the card renders PROVISIONAL styling and the
  strip refuses tuning: *"n={n} — calibration provisional. Do not tune
  on this."*
- Coverage@τ table: per-τ n shown per row (τ ∈ {0.7, 0.8, 0.9}).
- Flip-rate panel: `n={n} re-asks · {window}`; disposition flips and
  confidence wobbles listed as separate counts (the engine distinguishes
  them — `detect_flips` fields — and the card must too).
- The zero-SEV1/SEV2 bar (design 08 §2.3): `"{c} false-suppresses ·
  {n} joined through {ts} · {u} unreviewed"` — denominator, join
  freshness, and the unreviewed shadow are STRUCTURAL, never tooltip'd.

**Strip grammar (exact, from design 08 §5):**
*"Calibration healthy as of {age}: ECE {ece} (n={n})."* /
*"Calibration as of {age}: cannot refresh — treat tonight's confidences
as uncalibrated until this updates."* / *"n={n} — calibration
provisional."*

A calibration card that ships without its denominator fails the demo's
QA gate, same class as a suppression row without its counterfactual
(design 08 §4 rendering-defect rule).

## Finding 5 — Root DEMO_SCRIPT.md (sibling lane's file, audited in place)

The worktree also carries a root-level `DEMO_SCRIPT.md` (Tripwire lane,
committed in this worktree before lane 3's work began; narrative lane
placeholders reference `demo/storm-scenario/storm_runner.py`). It is not
lane 3's file to edit — but Petu's ruling covers all demo copy, so the
audit records the findings here for coordinator adjudication:

1. **Beat 4 — "The simulator: drag a threshold, watch last week's noise
   re-price."** CONFLICTS with Finding 3. The simulator as specified
   would project via `tuner.classify` (pre-ADR-013 continuous gate), not
   the live quantized kernel. **This line must be cut or relabeled
   before Sunday.** Recommended relabel: *"The simulator projects only
   the live kernel — quantized gate, dual attestation, the real race.
   That wiring lands with D1; tonight the storm is the demo."* If the
   UI lane wires the real kernel before Sunday, the beat is reinstated
   with the kernel named on screen.
2. **Beat 3 — "Click a SUPPRESS row."** Conditional copy. Two full
   rehearsals (42 real Jev calls) produced **zero real suppressions** —
   the triple lock held. The beat must carry the honest branch: if the
   live storm suppresses, click the real row; if not, the demoist reads
   the triple-lock honesty beat (demo/DEMO_SCRIPT.md beat 2) and never
   manufactures a receipt. A SUPPRESS row that is not in the event log
   is a fake, however it is clicked.
3. **Beat 1b recorded fallback** ("recorded real-Jev re-ask from rehearsal
   <timestamp> — live path down right now"). The recording artifact was
   inspected: real Jev answers, timestamped, source-labeled — it is a
   labeled recording of the real engine (design 08 §6.3's principle),
   not a mock. No-fake status: ACCEPTABLE with the verbatim label and
   the LIVE badge withheld. Residual tension with lane 3's runner
   contract (exit 2 on absent credential, never silently mock): the
   runner keeps exit-2 — the fallback is a presentation-layer decision
   for the demoist, not a silent engine substitution. Coordinator to
   confirm the precedence on demo night.
4. **Beat 2** references `demo/storm-scenario/storm_runner.py --n 40
   --seed 42` — consistent with lane 3's rehearsed configuration. The
   "SUPPRESS / QUEUE" river-row language in the beat should match the
   real disposition vocabulary (`page_now`, `suppress`,
   `page_business_hours`, `passthrough`) — cosmetic, flagged.
5. **File consolidation:** two DEMO_SCRIPT.md files now exist (root =
   beat structure + failure choreography; `demo/` = narrative copy per
   lane 3's task contract). Coordinator to designate the single source
   of truth before Sun 09:30 brief; until then both are cited here.

---

## Summary of cuts/relabels — lane-3 scope (built-only principle)

| Location | Issue | Action |
|---|---|---|
| DEMO_SCRIPT.md (demo/) beats 1–6 | D1/D3 implication scan | CLEAN — no change needed; triple-lock copy matches the built kernel |
| storm_runner.py printed copy | D1/D3 implication scan | CLEAN — allowlist labeled `demo-*` |
| DEMO_SCRIPT.md beat 1b | Fresh-minds refinement: live-first flip beat | UPDATED — live-first + labeled recorded fallback + limits beat |
| Threshold simulator (UI lane) | Kernel mismatch (Finding 3) | HANDOFF — UI lane + coordinator decide cut/relabel |
| Calibration cards (UI lane) | Denominators (Finding 4) | HANDOFF — contract drafted, enforcement is UI lane's |
| Root DEMO_SCRIPT.md (sibling lane) | Audit flags (Finding 5) | FLAGGED for coordinator adjudication |

*Lane 3's audit verdict: no demo copy describes D1/D3 as system
properties. The flip beat is live-first with the fallback rehearsed.*
| Calibration dashboard (unmerged UI) | — | Binding denominator contract issued (Finding 4); nothing to cut yet |

*Auditor: Lane 3. No screen implies D1/D3 as live. The simulator is the
only cut.*
