# Screen: Kill-Switch Console — the single control that must never lie

**Function code:** `KILL` · **Route:** `/kill-switch`
**API:** `GET /api/flags` · `GET /api/flags/changes` · `GET /api/healthz` ·
`GET /api/drills/kill-switch` · (flip) `POST /api/flags/global_kill_switch` (governed)

**Purpose:** The kill switch is the most consequential control in the system:
one flip and Sentinel stops suppressing — every alert passes through to a
human. The revision's audit found `flags.json` had **zero readers in `src/`**
(PIPELINE-REVISION §4.1 — the `<5s` claim was fiction). This screen is the
answer to that wound: it proves, continuously and on demand, that the wire
is real. An operator lands here with one question — *"if I flip this, does
the machine actually stand down?"* — and leaves with evidence, not a badge.

---

## 1. The operator's question

*"Is the kill switch live right now, and can I trust it to work when I need
it?"*

Secondary questions, in order: *Who can flip it? Who flipped it last, and
when? When was the last drill, and did it pass? How fast does the flip
propagate? What exactly happens to in-flight decisions?*

The screen answers all five above the fold. A safety control that requires
a wiki page to understand is a safety control that will not be used at 3 AM.

## 2. The five facts (above the fold)

Five named facts, fixed order, no tabs, no scroll to find them:

1. **State.** One of three words, large: `ARMED` (live, switch off —
   normal operation) · `ENGAGED` (switch on — the gate is passthrough-only)
   · `UNKNOWN` (flag state unreadable — this is itself a page-worthy
   condition, rendered in the critical register, P-LIB-9 smoke-alarm test).
   Below: the exact flag path, the reader count in the live gate
   (`read by: gate.hot_path` — the anti-theater artifact: the fiction
   died here; the screen shows *which code reads it*, not a marketing
   sentence).
2. **Propagation proof.** The last measured time from flip to effect:
   `flip → passthrough in 1.8s (measured 2026-10-05, drill record)`.
   The measurement is the claim. If no measurement exists within 30 days,
   this slot reads `no measurement on record — the <5s claim is
   UNVERIFIED` in warning register. A stale proof is not a proof.
3. **What it does, in one sentence.** *"While engaged, every alert passes
   through to paging. Nothing is suppressed. Jev is not consulted. The
   forwarder keeps delivering."* Operator language, no internals.
4. **Who can flip it.** The governed flip path: which roles/keys may flip
   (never anonymous; the flip is an audit event with the actor's identity).
   If the flip endpoint is not configured, this reads: *"flip path not
   wired — switch is display-only"* (the K6 phantom-interactivity law,
   generalized to the most dangerous control of all).
5. **Drill record.** Last drill: date, verdict (`7/7 PASS`), the drill
   record link (`ops/drills/2026-10-05-r10-kill-switch-drill.md`), days
   until the next scheduled drill. The drill is monthly by rule; the
   screen counts down to it like a calibration due date.

## 3. The flip ledger — who did what, when (below the fold)

P-LIB-3 lineage: Opsgenie's activity lifecycle log as the trust artifact.
Every flip and every drill is an event in the hash-chained log, rendered
inline — never in a separate audit tab the operator won't open at 3 AM:

```
2026-10-05 19:44:02  ENGAGED    operator: on-call (A. Sharma)   drill: monthly-2026-10
2026-10-05 19:44:31  DISENGAGED operator: on-call (A. Sharma)   reason: drill complete, 7/7
2026-09-05 09:12:44  ENGAGED    operator: platform (R. Iyer)    reason: vendor incident — Jev 500s
```

Each row deep-links to its audit event. The ledger is append-only by
construction (the log is); the screen never offers edit/delete.

**Honesty gap, disclosed in-band:** when `shadow_mode` AND
`global_kill_switch` are both on, the audit reason rewrites to `"shadow"`,
losing the `kill_switch` attribution (the full attribution survives in
`budget_outcome`, `/healthz`, and `flags-changes.jsonl`). The console shows
this as a standing note on the ledger, in warning register, until the
repair lands: *"attribution gap: kill-switch flips during shadow mode are
attributed to shadow in the audit reason. See the open finding."* A safety
console that hides its own attribution hole is the theater we're killing.

## 4. What happens when it's engaged — the engagement contract

The screen states the exact semantics, because "kill switch" means five
different things in five products (the K6 lesson: the control is only as
safe as its defined contract):

- **Gate:** `global_kill_switch` is read on the hot path. Engaged ⇒ every
  evaluation short-circuits to `passthrough` (`reason: "kill_switch"`).
  Suppression is unreachable — not "unlikely", unreachable.
- **Jev:** no Jev calls are made while engaged. The race is not armed.
  The inference budget is untouched.
- **In-flight:** decisions already made are not rewritten (history is
  never rewritten — L6 law). Decisions mid-race at engagement time resolve
  to `passthrough` regardless of which leg was winning.
- **Forwarder:** keeps delivering (pages still go out — the switch kills
  *suppression*, not *paging*). The outbox, retries, and secondary channel
  are unaffected.
- **Disengagement:** returns to normal evaluation. The engagement window
  is marked on the decision river as a gap annotation (the river's gap
  markers generalize: `— kill switch engaged 19:44:02–19:44:31 —`), so the
  suppression statistics for that window are never silently read as
  "nothing fired".

## 5. Professional-software lineage

| P-LIB pattern | What it teaches this screen | Why it fits |
|---|---|---|
| P-LIB-9 calm technology / smoke alarm | The switch is the smoke alarm: silent for months, then total attention. `ENGAGED` seizes the center (D4 strip promotes, ambient banner); `ARMED` is periphery — a quiet word in the strip. | A kill switch that screams constantly trains the operator to ignore it; one that can't scream is decoration. |
| P-LIB-3 Opsgenie lifecycle log | "Who did what, when" rendered inline on the control, not buried in an audit tab. | At 3 AM the operator needs the last flip's context in the same glance as the switch state. |
| P-LIB-4 Grafana explicit ladder | Named, visible states with declared semantics (ARMED/ENGAGED/UNKNOWN), like the escalation ladder's explicit steps. | A safety control's states must be a closed, auditable vocabulary — never a toggle whose meaning lives in a tooltip. |

## 6. What it must NEVER show (anti-fatigue rules)

1. **Never a bare toggle.** A switch widget with no state name, no ledger,
   no drill record is a toy. The toggle alone is the phantom-interactivity
   class — it *looks* like control. (INV-U4: a control that cannot act does
   not render; a control that *can* act renders with its full contract.)
2. **Never the flip as a casual action.** Engaging requires confirmation
   with the consequence stated in operator language: *"Engage kill switch?
   All suppression stops. Every alert pages a human until you disengage."*
   No one-click engage from a list row, no keyboard shortcut. (INV-U4's
   twin: the action is consequential by design — confirm with consequences,
   or don't ship the button.)
3. **Never a green "healthy" badge as the headline.** The headline is the
   state word (ARMED/ENGAGED/UNKNOWN) and the propagation measurement. A
   badge that says "kill switch: OK" is exactly the fiction the revision
   killed — it asserts the mechanism works without measuring it.
4. **Never drill records older than the policy allows without flagging.**
   A 6-month-old 7/7 PASS rendered as current is the same lie as the
   fictional flags. The countdown to the next drill is load-bearing UI.
5. **Never hide the attribution gap.** (See §3.) The gap is a finding,
   and findings live on the safety surface until closed.

## 7. States

- **Empty (never deployed):** if the flags contract is not wired, the
  screen says so in the first line: *"Kill switch not wired to the gate.
  The <5s claim is not made."* — the retract half of R-10's "wire or
  retract", rendered honestly.
- **Loading:** state word skeleton first, then the ledger.
- **Flag unreadable:** `UNKNOWN` in the critical register + a page to the
  platform team (this is the one screen whose own failure is a page).
- **Read-only for most roles:** the flip control renders only for roles
  that hold the flip capability; everyone else sees the state, the
  ledger, and the drill record. A missing control is honest; a disabled
  gray button that suggests a capability you don't have is not.

## 8. API mapping

| UI need | Endpoint |
|---|---|
| state + reader proof + propagation measurement | `GET /api/flags` (extends: `readers[]`, `last_propagation_ms`) |
| flip ledger | `GET /api/flags/changes` (hash-chained, actor identity) |
| drill records | `GET /api/drills/kill-switch` |
| healthz cross-check | `GET /api/healthz` (kill-switch field — the attribution-gap cross-reference) |
| governed flip | `POST /api/flags/global_kill_switch` (role-gated, audit-logged) |

Zero Jev on reads (platform law). The console never evaluates, never pages
— engaging the switch is a *governance write* to the flag store, attested
and logged, and the gate (not the UI) executes the semantics.
