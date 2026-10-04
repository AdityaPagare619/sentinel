# Weekly Model Re-validation — Type 1 Procedure (ADR-015, D2)

**Owner:** Pager — Sentinel SRE chief (named in `src/sentinel/revalidation.py::JOB_OWNER`).
**Cadence:** every 7 days. **Cron:** `0 6 * * 1` (Mondays 06:00 UTC / 11:30 IST)
→ `python3 scripts/model-revalidation.py`. Non-zero exit pages the owner.
**Status:** machinery shipped; cron registration owned by the wave coordinator.

## Why this exists

The vendor remaps models on the vendor's schedule, not ours. A silent
"improved calibration" release changes the probability mapping under
fixed thresholds — the safety case's thresholds, fit, and drift
assertion all assume the pinned model. The 7-day job is the standing
proof the assumption still holds. It is automated (no team runs a human
protocol per vendor release); the human SIGNS the result.

## What the job does

1. **Pin probe** — one live Jev call through the production client path;
   asserts `response.model == pinned_model` (the authoritative pin from
   the freshness bundle's `pinning.json` — the same pin the gate asserts
   on every answered response). Drift here is a page-the-owner event, not
   a backtest input.
2. **Replay** — the labeled incident corpus (`revalidation/corpus.jsonl`
   in the deploy bundle, or `SENTINEL_REVALIDATION_CORPUS`) through the
   LIVE gate kernel (`backtest.replay_backtest`, DR-26: same code path,
   never a copy) under the frozen production config (model pin,
   thresholds version, allowlist snapshot, fit version — all pinned in
   the ledger header). The bar is the ledger's zero-bar: **ZERO false
   suppressions on postmortem-confirmed real SEV1/SEV2.** Not "low." Zero.
3. **Report** — `reports/model-revalidation-<ts>.json` with
   `pin_probe`, `ledger_summary`, `verdict` (PASS/FAIL), and
   `human_signoff: null`.

## Failure modes (all loud)

| Condition | Job behavior | Human action |
|---|---|---|
| Pin probe drifts (`observed != pinned`) | verdict FAIL, exit 2 | Page owner; freeze threshold changes (ADR-022 watchdog freeze is the model); triage as vendor release — re-pin or hold |
| Zero-bar broken (any false suppress on real SEV1/SEV2) | verdict FAIL, exit 2 | Page owner; every miss becomes a regression case (Step 5 of the backtest procedure); no cutover until the ledger is green |
| Corpus empty / unreadable / schema-bad | `RevalidationRefused`, exit 2 | Page owner; a run on zero evidence is REFUSED, never green — fix the corpus pipeline |
| pinning.json missing (no pin) | refuses at startup, exit 1 | Repair the bundle; the job without a pin is undefined |
| Job crashes | exit 2, "treat as FAILED until a clean run completes" | Page owner; investigate the crash before trusting any green |

## The sign-off ritual

An unsigned PASS is **evidence awaiting sign-off, not a completed
procedure.** Within 24 h of each run the owner signs the report:
`human_signoff: {"by": "<name>", "at": "<ts>", "verdict_accepted": true}`.
A missed signature pages the owner the next cycle — the loop stays open
and visible, never quietly green. Sign-off authority: the designated
on-call SRE (initially Aditya, until a human on-call roster exists).

## Out-of-band runs

Any vendor model-release announcement, any `model_drift` event cluster in
the event log, or any timer-win watchdog trip with a vendor signature
triggers an **immediate out-of-band run** — the 7-day cadence is the
maximum interval, not the only trigger.

## Corpus stewardship

Labeled incidents come from the estate's own records (postmortems,
escalation logs) — the buyer's team owns labeling; Sentinel owns the
replay. Schema: one JSON object per line; fields
`incident_id, date, estate_severity, severity_provenance, postmortem_ref,
human_handling, label, label_provenance, alerts[]`, where each alert entry
carries `alert` (Alert fields), `state`, `history`, `context` for the
replay. Loader: `revalidation.load_corpus` — strict; a bad row refuses
the run, never filters silently.

## What would change this procedure

A vendor contract with machine-readable model-version immutability
guarantees (the ADR-015 "what would change it" clause) — the pin probe
becomes belt-and-braces, but the belt stays and the cadence stays.
