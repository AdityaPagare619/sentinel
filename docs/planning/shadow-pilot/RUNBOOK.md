# Runbook — 2-Week Shadow Pilot

## Day 0 — Setup (2–3 hours)

1. **Provision the tap.** Deploy the shadow receiver (read-only). Configure
   inbound verification only: PagerDuty webhook HMAC secret *or* Opsgenie
   Bearer <redacted> *or* Alertmanager Bearer <redacted> The tap holds **no write
   credentials** — `ShadowConfig.write_credentials` must be empty; the
   process refuses to boot otherwise. Verify with:
   `python3 docs/planning/shadow-pilot/run_shadow_pilot.py --n 50`.
2. **Point the vendor at the tap.** PD: create a webhook subscription for
   `incident.triggered`, `incident.acknowledged`, `incident.resolved`,
   `incident.escalated`, `incident.priority_updated` → `https://<tap>/shadow/pagerduty`.
   This is **additive** — the estate's existing paging is untouched.
3. **Confirm first delivery.** Trigger a test incident (or wait for the next
   real alert). Check the ShadowStore: episode created, `shadow_decision`
   logged. Tap health counters: zero drops.
4. **Brief the on-call.** They will label episodes at resolution
   (see `LABELING_PROTOCOL.md`). 5 minutes per episode, at resolution time.

## Days 1–13 — Daily checks (10 minutes)

- [ ] Tap health: deliveries accepted; drops (bad sig / unauth / unparseable)
  at zero. Any drop → investigate before trusting the day's data.
- [ ] Episode count growing; decisions logged for every alert.
- [ ] Labels being assigned at resolution (nudge the on-call if the
  unlabeled backlog grows past 10).
- [ ] Jev spend meter within budget; fail-open count sane (timer wins on
  slow judge = expected, not an incident).
- [ ] No `write_credentials` anywhere near the tap (paranoia check).

## Day 7 — Midpoint

- Generate the interim report. Check N (labeled episodes). If N<15 at
  midpoint, the pilot likely needs extension — raise it now, not day 14.
- Review divergences so far with the on-call. Any SEV1/SEV2 divergence →
  investigate immediately (the zero bar doesn't wait for day 14).

## Day 14 — Report

1. Freeze the window. Generate the final report
   (`generate_shadow_report`).
2. Fill `REPORT_TEMPLATE.md`. Headline first: false-suppress rate on
   real SEV1s.
3. Walk the customer through it: the numbers, the divergence list (both
   directions), the calibration summary, the thin-data caveats if any.
4. **Decision:**
   - **Zero SEV1/SEV2 false-suppresses + N≥30** → stage gate passes;
     proceed to suppression onboarding (conservative thresholds first).
   - **Any SEV1 false-suppress** → gate held. Root-cause, fix, re-run.
   - **Thin data (N<30)** → extend the pilot or accept the caveat in
     writing. No silent promotion.

## After the pilot

The report is published (with customer permission) as the go-live evidence.
The tap stays up — it becomes the ongoing calibration feed.
