# Sentinel — ROADMAP: Preview-1

**Target: working demo by EOD Oct 4, 2026 IST.** Not slides — a live, recorded run:
synthetic alert storm → Jev triage (mock) → suppress/escalate per expected-cost thresholds
→ immutable audit log → calibration report. Aditya's bar: "not today, not beyond ~2 days."
Scope = v0.1 milestones **M3–M6** (M1, M2, M4 already landed; see `PROGRESS.md`).

## What ships (scope = M3–M6)

- **M3 — end-to-end pipeline on synthetic alerts:** fire synthetic PagerDuty-format alerts
  at the receiver → correlator (dedup/storm-collapse/change-windows) → gate (mock Jev
  triage: severity/team/disposition) → forwarder → audit row for every decision.
  (Build coordinator's Helper B in flight; do not touch — coordinate via PROGRESS.md.)
- **M4 — tuner CLI produces thresholds + savings projection from labeled data:** DONE
  (smoke: 2,000 synthetic alerts → conf 0.90, 1,123 suppressions / 67.3% of baseline pages).
  Prism packages the output for the demo narrative.
- **M5 — eval harness green with repeatability probes:** harness DONE (severity acc
  0.9765, ECE Q1 0.0956 / Q3 0.0819, false-suppress 0.0000, flip+shuffle PASS).
  Remaining: full-suite green pending Helper B landing.
- **M6 — MVP complete:** README done, full test suite green, demo walkthrough recorded
  in PROGRESS.md, demo transcript committed.

**Demo script (the actual run, in order):**
1. Boot receiver (`python3 -m sentinel.receiver`), show `/healthz`.
2. Fire a synthetic storm (flaps + deploy spikes + 5% real SEV1/2s + warnings) at
   `/v2/enqueue` — watch correlator collapse duplicates, gate triage via mock.
3. Show dispositions live: suppress (known noise, allowlist, conf ≥0.90) / page_now
   (P(p1)+P(p2) > 0.30) / queue / passthrough.
4. Query the audit log: every decision present with input hash, model version, probs.
5. Kill the Jev client mid-run → show fail-open: everything still pages, zero drops.
6. Run the tuner on the run's labels → thresholds + savings projection.
7. Run the eval harness → calibration report with the honest-limitations section.

## Exit bars (Preview-1 is GO iff all green)

| Bar | Target | Owner |
|---|---|---|
| Noise suppression on synthetic storm | ≥60% of baseline pages suppressed | calib/eval lane |
| False suppress on synthetic SEV1s | **ZERO** (any unexplained false suppress = SEV1 incident per CHARTER) | Tripwire |
| Decision latency p95 (mock) | <1s end-to-end receiver→forwarder | engine lane |
| Audit completeness | every decision logged, input hash + model version present | security/audit lane |
| Full suite | `python3 -m unittest discover tests` green, kill-the-client green | Tripwire |
| Repeatability | flip <2%, option-shuffle <3% | calib/eval lane |
| README | zero-to-first-alert in <15 min for a new SRE | Prism |

## Lane assignments

| Milestone | Lead lane | Support |
|---|---|---|
| M3 pipeline E2E | build coordinator (Helper B, in flight) | Forge (contracts), Tripwire (E2E gates) |
| M4 tuner packaging | calib/eval | Prism (demo narrative) |
| M5 harness + probes green | calib/eval | Oracle (metric sign-off), Claim Auditor (numbers) |
| M6 README + demo transcript | Prism | docs/devrel lane, Relay (checklist) |
| CI + secrets-grep + PR flow | Tripwire + Vault | all lanes |

## Top risks

1. **Single-owner collisions:** the build coordinator is writing `src/`/`tests/`/`PROGRESS.md`
   right now. Nobody else creates or edits those paths. Base-setup lanes work in docs/
   ops/.github/ only. (Enforced, not aspirational.)
2. **Jev 529s / rate limits at demo scale:** mock is the Preview-1 demo path; live Jev
   only behind `SENTINEL_SHADOW` in a recorded (not live) segment. The demo must never
   depend on TypeSafe's availability.
3. **Flip-rate surprise in the demo:** repeatability probes run *before* the demo
   recording; if flip ≥2%, the demo states it and the audit log is the story — never
   hide a flip.

## What Preview-1 is NOT

- No real PagerDuty/Opsgenie integration (mock forwarder only).
- No dashboard polish (Preview-2; Prism ships the spec now).
- No public launch, no design-partner outreach (Preview-2).
- No accuracy claims anywhere — the demo's headline is the calibration report, the
  audit log, and the kill-the-client run.

## After Preview-1 (Preview-2, sketched — not committed)

Real-Jev shadow pilot with one friendly org, calibration dashboard v1, per-team
threshold re-tuning, label joiner automation, public repo + pricing page.
