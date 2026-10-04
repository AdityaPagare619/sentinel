# Sentinel — production cutover checklist (deploy lane)

**Source of truth for the gate:** `ops/devops-foundation.md` (L3, PR #53)
§1.2. This doc is the lane's executable copy: no prod cutover — first or
re-cutover — without ALL boxes checked, with evidence pasted in the release
note.

## Hard gate

* [ ] Pinned SHA green: `pre-pr-gate.sh` run on the exact SHA, evidence
      pasted in the release note.
* [ ] Shadow-diff signed: candidate ran ≥7 days on `staging-shadow`; the
      disposition diff vs live was reviewed and signed by two humans
      (ADR-022 canary-as-shadow-diff).
* [ ] Secondary drilled: `require_drilled_secondary` passes — configured
      secondary, last drill ≤30 days, **human ack** within 10 min
      (ADR-018/D13). The forwarder refuses to start otherwise; do not bypass.
* [ ] Wakefulness attestation on file for the secondary (a real sentence,
      not a placeholder — `secondary.py` rejects placeholders).
* [ ] External watcher armed with a **named owner** and a duty roster;
      watcher-down pages via a non-Sentinel path (dead-man's-switch).
      See `ops/watcher-deployment.md`.
* [ ] Disk-guard interim (D14) verified on the prod state dir; retention
      RFC (ADR-024) tracked as the real answer.
* [ ] On-call has read RB-4..RB-7 and drilled RB-4 + RB-7 in `staging-lab`
      within the last 30 days.

## Routing keys — DOCUMENTED PLACEHOLDERS (owned by Aditya)

The production routing keys are **not configured and not invented**. The
deployment ships and runs without them (it cannot page — by construction).
Cutover is blocked until Aditya provides them; nothing else is blocked.

* [ ] TODO(Aditya): provide the production PagerDuty routing key →
      set as `PD_ROUTING_KEY` in the prod secrets store (never in git,
      never in chat logs beyond the handoff).
* [ ] TODO(Aditya): provide the watchdog's routing key →
      set as `SENTINEL_WATCHDOG_ROUTING_KEY` in the prod secrets store.
      (The ADR-022 watchdog's dead-man's-switch heartbeat pages through
      this key on a path that does not traverse Sentinel.)

Until both are set: the receiver runs, the platform serves reads, the
watcher watches — but no page can fire. That is the safe default, and it
is deliberate.

## Tier reminder (L3 §1)

`staging-shadow` **never pages**: no `PD_ROUTING_KEY` is ever configured
there, and startup refuses a prod routing key in the shadow tier. A staging
box that can page is a second production.
