# R-10 kill-switch drill plan — `global_kill_switch` end-to-end

**Lane:** dev-1 · **F1 kill condition:** this drill must PASS on
staging-lab by T+9, or the retract ADR lands.
**Drill record:** `ops/drills/2026-10-05-r10-kill-switch-drill.md`
— rewritten by every verified run of the script (latest run wins).
Readiness was verified 2026-10-05 ~16:05 IST on a local lab; the
binding F1 drill re-runs at T+9 on staging-lab (on the shared lab if
Relay provisions one before then, per the blockers.md entry).
**Lab provenance:** built ONLY via the sanctioned
`scripts/ops/env-bootstrap.sh --tier staging-lab` (devops-foundation
§1.1: "Rebuilt from scratch by env-bootstrap.sh each drill cycle").
If Relay provisions a shared staging-lab before T+9, the drill is
re-run there; this plan is lab-agnostic.

## Pass criteria (all must hold; the drill asserts behavior, not belief)

The executable form is `ops/drills/r10_kill_switch_drill.py` (stdlib
only) — its 7 checks below are the criteria; this section is the
human-readable twin. One tap, human ack at the two-person step (the
ceremony is the point — it is not scripted away).

1. Baseline: a synthetic PD trigger alert (unique fingerprint per
   alert — the correlator dedups by fingerprint, and a repeated
   fingerprint would inherit the prior disposition instead of
   exercising the gate fresh) round-trips through the receiver.
   Under the mock client the gate arms the S2 race, the mock Jev
   raises, and the alert lands fail-open: `passthrough`, reason
   `error:error`, `budget_outcome=error_passthrough` in the audit row.
   CORRECTION (dry-run 2026-10-05 ~16:00 IST, verified against the
   running receiver): the original draft of this criterion said "a
   non-passthrough disposition" — that is not what mock mode does,
   and asserting it would fail the drill on a fiction. The honest
   baseline is the fail-open error passthrough WITH the race armed;
   what the kill switch must change (criterion 4) is exactly that:
   no race, no Jev call.
2. `flagctl.py set global_kill_switch true --by <name> --reason <drill>`
   (one human — the ON direction needs one) exits 0; the change is in
   `flags-changes.jsonl`.
3. `POST /-/reload` → 200 with an incremented `config_generation`,
   AND `/healthz` shows the new `config_generation` AND
   `flags.global_kill_switch == true` (checkable, not believed).
4. A fresh-fingerprint synthetic alert round-trips as **`passthrough`**
   with reason **`kill_switch`** and
   `budget_outcome=structural_passthrough`; the audit row is written
   (the drill reads it back — the row's existence is the assertion).
   The zero-Jev-call property is unit-pinned
   (`test_kill_switch_passthrough_no_jev_call` asserts the mock
   client's call counter stays zero); the drill asserts the
   end-to-end observable proxy (S0 is the first branch in `_decide`,
   before the race is armed).
5. Flip OFF with the two-person ceremony: a single-human OFF is
   REFUSED by flagctl (the safety asymmetry is asserted, not
   assumed); then `flagctl.py set global_kill_switch false --by
   <name> --two-person "<a>,<b>" --reason <drill>`; `/-/reload`;
   `/healthz` confirms `flags.global_kill_switch == false`.
6. A fresh-fingerprint synthetic alert round-trips with NO
   `kill_switch` reason (behavior restored — the OFF direction is
   verified, not assumed).
7. Reload-rejection (unit of the drill): push an invalid `flags.json`
   (unknown flag), confirm `/-/reload` → 422, the live generation is
   untouched, and `config_rejected` is emitted (state-dir
   `events.jsonl`); the valid file is restored and reloaded so the
   lab is left clean.

## Failure = the retract ADR

Any criterion failing — including "the flip worked but the disposition
didn't change" (the rusted-shut mode) — is a FAILED drill. The failure
mechanism is written into the drill record verbatim (honesty law), and
`docs/decisions/2026-10-05-r10-retract-DRAFT.md` lands (the DRAFT banner
comes off, the outcome section is filled).

## Script

`ops/drills/r10_kill_switch_drill.py` (stdlib only): builds the lab,
starts the receiver in mock mode, runs criteria 1–8, prints PASS/FAIL
per criterion, and writes the drill record. One tap, human ack at the
two-person step (the ceremony is the point — it is not scripted away).

## Traceability

`ops/devops-foundation.md` §2.2 (flip procedure), §2.5 (kill-switch
drill) · 12H-PLAN §2 T+9 · F1 verdict.
