# Sentinel — Production Cutover Runbook (RB-7 operational)

**Lane:** D13 · **ADR:** ADR-018 (standby direct-to-PD + schema-validated config)
· **Date:** 2026-10-04 · **Type:** 1 — deployment architecture
(cutover architecture is load-bearing; a silent bad cutover loses pages).

This runbook is the operationalization of ADR-018's cutover gate and of
§6 / RB-7 in `ops/devops-foundation.md` (L3). It was **drilled first**
— `docs/drills/drill-2026-10-05.md` is the executed record. A runbook that
has never been drilled is a rumor; this one has receipts.

Pre-mortem (principal-systems): assume it is 2027 and this cutover story
failed. The three most likely causes:
1. The secondary channel was configured but never actually delivered a
   drill page end-to-end (theater). *Mitigation:* every drill asserts the
   stub/vendor receipt AND a human ack within 10 minutes.
2. The cutover gate (`require_drilled_secondary`) was bypassed under
   schedule pressure. *Mitigation:* the forwarder refuses to start —
   the refusal is the control, not a suggestion.
3. The external watcher had no named owner, so "Sentinel down" was a
   sentence, not a system. *Mitigation:* the watcher requirement has a
   named owner, recorded below — Petu's decision.

---

## 0. Named responsibilities (ADR-018 conditions)

| Responsibility | Owner | Recorded |
|---|---|---|
| External-watcher requirement (watcher armed, duty roster, watcher-down path that never traverses Sentinel) | **Pager** (Petu's decision, 2026-10-04) | ADR-018 Pager condition; `ops/devops-foundation.md` §1.2 |
| Drill cadence + drill records | On-call rota (monthly, gated) | ADR-018 Tripwire condition: "drilled" is verifiable iff drill records with human acks exist within 30 days |
| Retention / disk-guard interim | Vault (ADR-024 RFC) | §6 RB-6 |
| This runbook's maintenance | D13 lane → on-call | this doc |

## 1. Preconditions (all hard — the cutover refuses to start otherwise)

Run `ops/drills/d13_cutover_drill.py`-style checks, or the manual equivalents:

1. **Secondary configured:** `SecondaryConfig` with a provider that is
   vendor-independent of PagerDuty (the config validator rejects
   `provider="pagerduty"` — fate-sharing). Wakefulness attestation present
   in the on-call's own words (placeholders rejected by the validator).
2. **Secondary drilled:** `DrillTracker.last_passed_ts()` within 30 days.
   The drill passed ONLY on human ack within 10 minutes of the drill send
   (a delivery receipt is not an ack).
3. **Cutover gate passes:** `require_drilled_secondary(stage, secondary,
   drills)` raises nothing for `stage` in `("stage-2b", "stage-2c")`.
   In shadow/stage-2a the secondary is optional (nothing pages).
4. **Spill dir writable:** `send_direct` writes an emergency spill record on
   every standby send; if the spill dir is unwritable the page still goes
   out but the audit repair at next startup is lost.
5. **Primary PD reachability baseline:** know the current state of
   `PD_EVENTS_URL` (or the configured `pd_endpoint`) — the trigger
   criterion below needs a "before" state.

Expected output of the gate check (from the drill):
```
CUTOVER GATE PASS: stage=stage-2c, last drill passed <30d ago, human ack within 10 min
```

## 2. Trigger criteria — when this runbook fires

Any ONE of:

- **T1.** The primary PD path is dead: connection-refused or 5xx from the
  configured PD endpoint for >5 minutes, or the PD status page confirms an
  incident affecting Events API v2 ingestion.
- **T2.** The receiver is down and RB-4 step 3 says so: rollback didn't
  restore `/healthz ok:true`, and pages are being lost. Do not keep
  debugging a dead receiver while pages are being lost — paging does not
  wait for debugging.
- **T3.** The outbox is backed up past `SECONDARY_FIRE_AFTER_S` and the
  drain is not recovering (outbox lag keeps climbing on `/healthz`).

## 3. Procedure — step by step

### Step 1 — Declare (timestamped)
The on-call declares in the incident room:

```
CUTOVER: standby direct-to-PD armed — <trigger: T1/T2/T3> — <reason>
operator=<name> ts=<ISO-8601>
```

Write this line into the drill/incident record AND the drill-tracker JSONL
(the store the cutover gate reads — see `require_drilled_secondary`).
Do NOT invent an event-log type: the event-log vocabulary is a closed
Type-1 set, and `append_event` rejects unknown types. The postmortem
starts here.

**Expected output:** one line in the room, one row in the drill-tracker JSONL. (Note: drill/cutover lifecycle records live in the drill
record and the drill-tracker JSONL, not in invented event types. The
standby send's completion IS event-logged via the spill-replay path —
see Step 2.)

### Step 2 — Fire the standby direct-to-PD path
Invoke the standby seam (`Forwarder.send_direct(payload, reason=<trigger>)`):

- It bypasses the outbox entirely (the outbox is presumed dead or the
  vendor is down — that's why you're here).
- It resolves the **control routing key** (pinned at first attempt) and
  POSTs the PD Events API v2 payload with `sentinel/degraded` source,
  `severity: critical`, and `custom_details.sentinel_degraded_reason`.
- It writes the emergency spill record to the spill dir and prints the
  outcome line to stderr.

**Expected output (stderr):**
```
[sentinel] FORWARDER degraded direct-to-PD reason=T1_pd_unreachable outcome=accepted spill=<spill path>
```
and the vendor intake responds `202 {"status":"success"}` —
`forward_confirmed` in PD's own intake semantics (at-least-once acceptance
+ dedup_key idempotency; **not** exactly-once — never claim more).

**Verify:** the page appears in the vendor console (prod) or the stub PD
record file (lab drill). The spill file exists on disk.

### Step 3 — Primary retries CONTINUE
`secondary_fired_at` / a standby send is a timestamp, not a terminal
status. The durable path keeps retrying the outbox rows with the pinned
routing key and pinned dedup_key. Never stop primary retries when the
standby fires — the design §8 pre-mortem line that caused the SEV1 miss.

**Expected output:** outbox rows remain `queued`/`in_flight` with
`attempt_count` climbing; no row is marked delivered until the vendor
confirms it.

### Step 4 — Fire the secondary channel (human-reachable path)
If the outage affects page *delivery* (not just Sentinel), pages must ALSO
go out via the vendor-independent secondary (SMS/webhook/email):

```
drill/secondary send → secondary receipt → HUMAN ACK within 10 min
```

**Expected output:** `SecondarySendResult(ok=True, ...)` AND the human ack
recorded (`DrillTracker.ack_drill` → True). A delivery receipt alone is
not an ack — the drill is theater without it.

### Step 5 — Declare recovery, stand down
When the primary path recovers:

1. Confirm the outbox drains: watch `forwarder_outbox_lag_s` → 0.
2. Confirm the queued pages appear in the PD console.
3. Stand the secondary down; declare `CUTOVER COMPLETE` with timestamps.

**Expected output:** `CUTOVER COMPLETE ts=<...>` line; outbox lag 0.

## 4. Rollback (the cutover's own reverse)

| What | How | Time |
|---|---|---|
| Standby send was wrong (bad payload) | The standby path is fire-and-forget to the vendor; it cannot be recalled. Compensate: post a PD `resolve` for the same `dedup_key`, then page the correct content on the primary path. Log both. | minutes |
| Cutover declared in error (primary was fine) | Declare `CUTOVER ABORTED ts=<...>`, stand the secondary down, let the outbox drain normally. The standby's spill file replays at next startup — no audit gap. | <5 min |
| Secondary misfires (wrong destination) | Fix `secondary.destination`, re-run the secondary drill with human ack before trusting it again. | minutes |

The release-level rollback (flags/config/code) is orthogonal — see
`ops/devops-foundation.md` §2.4. A cutover rollback never involves a code
deploy.

## 5. What "verified" vs "stubbed" means here

The D13 drill (`docs/drills/drill-2026-10-05.md`) executes every step above
against the local stack with a **STUBBED PagerDuty endpoint** and a
**STUBBED secondary webhook**. Explicitly:

- **Verified for real:** the outbox-drain failure mode (connection-refused
  → retryable, row stays undelivered), the `send_direct` standby path
  (outbox bypass, emergency spill write, PD-shaped 202/success handling),
  the standby completion event-logged through the design's own spill-replay
  path (`forward_confirmed` with `channel="direct-degraded"`, read back from
  the log), the secondary drill send + ack window mechanics, the
  `require_drilled_secondary` cutover gate (refuses stale drills, passes
  fresh). The secondary drill completion is recorded in the drill-tracker
  JSONL (the store the cutover gate reads); the event-log vocabulary is a
  closed Type-1 set with no fitting drill event type, so it is not
  shoehorned into the log — a dedicated drill event type is flagged as
  follow-up for the event-log lane.
- **Stubbed (not claimed):** the PagerDuty Events API itself (the stub
  replies `202 {"status":"success"}` like PD's intake; it is not PD),
  the secondary channel's human reachability (the stub records the POST;
  a real human was not paged), and the human ack (SIMULATED by the drill
  operator — a real human-acked drill is required before the first prod
  cutover per ADR-018's conditions).

## 6. Drill cadence

Monthly, gated (a missed drill blocks the next release until it happens —
ADR-018 Pager condition: gated drills, never calendar drills). Each drill
is a <5-minute one-tap exercise: run `ops/drills/d13_cutover_drill.py`,
confirm the record lands in `docs/drills/` with human ack within 10 min.
Stale (>30d) drills refuse cutover — by construction.

---

*Type 1 decision record: this runbook's trigger criteria, gate, and
rollback are architecture. Changing them needs the ADR process. The drill
script's fixtures and timings are Type 2 (tunable).*
