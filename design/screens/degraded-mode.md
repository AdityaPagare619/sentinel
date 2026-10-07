# Screen: Degraded Mode — what the operator sees when the machine is hurt

**Function code:** `DEG` · **Route:** `/degraded`
**API:** `GET /api/health` · `GET /api/failopen/state` ·
`GET /api/failopen/history` · `GET /api/healthz`

**Purpose:** The revision's safety architecture fails *toward the human* —
but "fail toward the human" is a mechanism, not an experience. When Jev
is down, when the policy store is unreachable, when the outbox is
backing up, the operator needs to see, in one place: *what is degraded,
which fail-open step we're on, what the machine is doing instead of the
normal path, and what I should do.* This screen is the degradation made
legible. It is also the standing answer to WORKFLOW 4's 3 AM question:
the emergency levers are here, and every one of them pages *more*, never
suppresses more.

---

## 1. The operator's question

*"The system is degraded — what is it doing right now, and what do you
need from me?"*

Secondary: *Which fail-open step is active? What decided the recent
pages? When did we step down, and why? What's the path back to normal?*

## 2. The ladder (above the fold)

The C1 stepped fail-open ladder, rendered as named steps with the active
one unmistakable — P-LIB-4's explicit-ladder lineage (Grafana's
escalation chain as visible steps):

```
NORMAL   the full pipeline: correlator → gate → Jev race → forwarder
STEP 1   last-known-good signed policy (freshness-gated)      ● ACTIVE since 03:12:44
STEP 2   static severity floor
STEP 3   rate-capped paging + digest
```

The active step carries: since-when, why (the triggering condition —
*"Jev error rate 41% over 5m (threshold 25%)"*), and what it means in
operator language: *"Step 1: the gate is deciding from the last attested
policy without consulting Jev. Suppression still requires the full
conjunction; nothing new can be suppressed on model judgment."*

**The honest exception, stated on the screen:** S1 dedup *can* emit
`suppress` while degraded — inheriting a pre-degradation prior earned by
the full conjunction (R15 F4). The screen names it: *"12 suppressions in
the last hour were dedup-inherited priors, not new judgments."* The one
suppression path that survives degradation is labeled, counted, and
explained — never silent.

**What each step does NOT do** is printed under the ladder, because the
3 AM operator's fear is "is it suppressing things it shouldn't":

- No step suppresses on model judgment. The race is not armed while
  stepped down (no Jev calls — the race monitor confirms: link).
- Step transitions are named, event-logged `failopen_step_entered`
  events. The machine does not slide between steps silently.
- Stepping *up* (recovery) requires the triggering condition to clear
  for the full clear-window *and* a freshness re-verification — recovery
  is not a single good poll.

## 3. The degradation banner contract

Every other screen renders the degraded state in-band, in the same place
the operator already looks (D4's degradation honesty channel). This
screen defines the banner vocabulary so the whole console speaks one
language:

```
DEGRADED · fail-open step 1 · since 03:12:44 · decisions: policy-only, no Jev
```

- Text, never color-only (freshness is always stated, never inferred —
  cross-workflow refusal 4).
- Names the step, the since-when, and the decision mode in one line.
- Links here (`/degraded`) for the full picture.
- When the kill switch is engaged, the banner reads `KILL SWITCH ENGAGED`
  (not "degraded" — the switch is a deliberate governance act, not a
  failure; the vocabulary keeps them distinct).

## 4. The 3 AM levers (below the fold)

The emergency actions, each with its consequence stated (INV-U4 — every
consequential action confirms with consequences in operator language):

1. **Engage kill switch** → `/kill-switch` (governed flip). *"Stops all
   suppression. Every alert pages until you disengage."*
2. **Storm digest mode** — collapse the river to aggregate pages + the
   advisory digest. *"Fewer decisions to make, not fewer pages."*
3. **Secondary channel** — route pages via the backup path. *"For when
   the primary forwarder path is the thing that's degraded."*
4. **Page-me-anyway (Q8)** — the audit-logged override: *"Page me for
   everything matching this filter for the next N minutes, through the
   real paging path, attested in the log."* This is the wired appeal
   control's emergency form — a governance action, not an incident
   action, and it pages *more*.

What is NOT here: threshold editing (refused — WORKFLOW 4), mute-all
(refused — a mute-all is a suppression decision made in the console),
"restart the service" (refused — K6 killed inline remediation; the
control plane is the operator CLI, not a button in a list).

## 5. The step history

Every `failopen_step_entered` event, newest first — the machine's own
account of its degradation:

```
03:12:44  → STEP 1   trigger: jev_error_rate 41% > 25% (5m window)
02:58:10  → NORMAL   trigger: error rate clear for 15m + freshness re-verified
02:41:33  → STEP 1   trigger: jev_error_rate 38% > 25% (5m window)
```

Each row links to the audit event. Flapping between steps (down-up-down)
renders as a finding — *"3 step transitions in 40m: the trigger threshold
may be mis-tuned"* — because a ladder that can't stay on its rungs is
telling the operator something.

## 6. Professional-software lineage

| P-LIB pattern | What it teaches this screen | Why it fits |
|---|---|---|
| P-LIB-9 calm technology | Degradation is the honesty channel: in-band, in the place the operator already looks, text-first. The D4 strip is this screen's ambassador everywhere else. | The degraded state is when the operator most needs the console to be calm and precise. Panic typography helps no one. |
| P-LIB-4 Grafana explicit ladder | Named steps, visible transitions, declared semantics — the escalation ladder generalized to system state. | "Degraded" as a single boolean is unactionable. Named steps tell the operator what the machine is doing *instead*. |
| P-LIB-6 incident.io newcomer summary | The screen opens with "what's happening and what do I do" — the verdict block (D3's 30-second sheet) applied to system state. | A degraded system is an incident against the machine itself; the operator deserves the same brief a newcomer gets. |

## 7. What it must NEVER show (anti-fatigue rules)

1. **Never "all systems operational" as a state.** When nothing is
   degraded, this screen says: *"No degradation active. The pipeline is
   on its normal path."* — and the D4 strip stays quiet. A green wall
   of "operational" badges is the calm-technology failure (P-LIB-9):
   it taxes attention to report health.
2. **Never auto-refresh the ladder into a blur.** Step transitions are
   rare and important; the screen updates on events, not on a poll
   timer. A ladder that flickers is a ladder nobody trusts.
3. **Never offer the 3 AM threshold edit.** Not as a button, not as a
   link, not as a "advanced" accordion. The refusal is the feature
   (WORKFLOW 4 pre-mortem #4: the six-week silent widening).
4. **Never conflate kill-switch engagement with degradation.** Distinct
   vocabulary, distinct banners, distinct ledger. A deliberate
   governance act and a system failure are different things, and the UI
   must not let the operator confuse them at 3 AM.
5. **Never hide the dedup-inherited suppressions.** The R15 F4 exception
   is the one place suppression survives degradation. If the screen
   doesn't count it, the operator will discover it in a postmortem —
   the worst possible place.

## 8. States

- **Normal (no degradation):** the ladder with NORMAL highlighted, the
  honest-exception line at zero, the 3 AM levers present but quiet.
  The screen is a reference, not an alarm.
- **Stepped down:** the active step in the critical register, the banner
  contract live across the console, the step history leading with the
  current transition.
- **Unknown health:** if `/healthz` is unreachable, the screen says
  `UNKNOWN` — it does not render the last-known ladder state as
  current. A stale ladder is worse than no ladder.

## 9. API mapping

| UI need | Endpoint |
|---|---|
| ladder state | `GET /api/failopen/state` (active step, since, trigger, decision mode) |
| step history | `GET /api/failopen/history` (`failopen_step_entered` events) |
| health cross-check | `GET /api/health` + `GET /api/healthz` |
| 3 AM levers | deep links to `/kill-switch`, river storm-digest mode, secondary channel control, Q8 override |

Zero Jev on reads. The levers are governance writes (attested, logged);
the screen itself never evaluates, never pages, never suppresses.
