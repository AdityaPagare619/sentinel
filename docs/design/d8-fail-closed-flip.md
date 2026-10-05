# D8 fail-closed flip — design v0.1

> **Status:** v0.1 for Oracle review (T+3 checkpoint) · **Lane:** gate-2 (GATE domain)
> **Branch:** `lane/12h-gate-2-d8` · **Date:** 2026-10-05 IST
> **Type:** 1-adjacent — changes what the gate accepts as *authority to
> suppress*; reversing it later re-opens the least-supervised state as the
> most permissive. Labeled explicitly (principal-systems operating rule 3).
> **Single-threaded owner:** gate-2 (this lane) until the T+9 Oracle+Forge
> review assigns the Phase-1 build owner.
> **Reviewers:** Oracle + Forge (gate kernel, per OPERATING-RULES §1.6);
> Tripwire on the policy-change PR (standing — every policy-change design);
> Vault sign-off (policy-change path).

## Traceability

- Q3 decision `docs/decisions/2026-10-05-q3-policy-canonical-content.md` —
  option (a), canonical `PolicyVersion.content`; "The D8 fail-closed flip
  is included" in the decision. D8 is the flip side of Q3: when refusal of
  an unattested generation isn't cleanly expressible on the runtime path,
  ambiguity resolves to fail-closed.
- R-1 (`PIPELINE-REVISION.md` §6): "flip D8's missing-file semantics from
  fail-open (`(True, "policy_gate_not_configured")`) to fail-closed once
  the store is initializable — the 'watchdog never set up' state must page,
  not suppress unsupervised."
- `deterministic-gate.md` P1 (§2.1): the missing-file fail-open called out
  as "an unquantified permanent" degradation; P2 (vocabulary discipline).
- R-1 option (a) build design
  (`origin/lane/prep-r1-policy:docs/design/r1-option-a-canonical-content.md`
  §2.4): "The D8 `PolicyGate.can_suppress` fresh-read of the policy-state
  JSON stays as the *runtime* per-decision guard (never a boot cache — ADR
  Type-1). The missing-file semantics flip (`(False,
  "policy_gate_not_configured")`, fail-closed) is gate-2's scope, designed
  alongside this — the two flips must land together."
- 12H-PLAN §1 (gate-2 row): "D8 fail-closed flip design (part of R-1)".

## 1. The problem, in one paragraph

The gate's suppress conjunction ends with the D8 policy gate —
`PolicyGate.can_suppress(policy_id)` fresh-read per suppress verdict
(`policy_lifecycle.py:523`), consulted by `Gate._policy_allows_suppress`
(`gate.py:815`) inside the single `_resolve_suppress_path` composition
(DR-26). Two of the guard's own states are fail-**open**: a missing
policy-state file returns `(True, "policy_gate_not_configured")`
(`policy_lifecycle.py:525-535`) — one stderr warning, then the triple lock
stands alone and suppression is allowed; and a `Gate` constructed without
any policy hook (`policy_gate=None`, `gate.py:817-818`) returns `(True,
"ok")`. The "watchdog never set up" state — precisely the state a rushed
first deployment ships in — is the state in which suppression is *least*
supervised and *most* permitted (principal-systems eternal friction). The
rest of the guard is already fail-closed (corrupt file, no policy,
expired, frozen all block), which makes the two open states an
inconsistency, not a philosophy: the guard withholds suppression on every
doubt *except* the doubt about whether it exists at all.

Chesterton's fence, examined: `None → (True, "ok")` exists for test
ergonomics (most `Gate` constructions in tests wire no hook), and the
missing-file allow exists so a store-not-yet-initialized dev box doesn't
page-storm. Neither reason is a production semantic — both are
conveniences wearing the costume of a decision. The design below keeps
the conveniences where they belong (explicit test doubles; the R-1(a)
store-initialization ceremony) and removes them from the authority path.

## 2. The flip principle

**Every policy-state ambiguity in the gate resolves to fail-closed:
suppression is withheld, never permitted by default.** Authority to
suppress must be positively established — a LIVE (or REVIEW_DUE),
unfrozen, unexpired, un-revoked version, read from a file the watchdog
writes and the gate only reads — or the suppress verdict does not happen.

### 2.1 The fail-open / fail-closed distinction, stated precisely

The revision kept **fail-open for errors** and D8 is **fail-closed for
policy ambiguity**. These are not contradictory; they gate different
objects:

- **Fail-open (errors):** *signal* failures — Jev timeouts, malformed
  answers, missing monitors, stale freshness legs, drifted models,
  exceptions anywhere on the path. The gate cannot decide confidently, so
  the *disposition* degrades toward the human (page/passthrough). Paging
  is degrade-tolerant: the C1 stepped ladder keeps a degraded-but-serving
  path alive while paging. Fail-open names the direction of the
  *disposition under uncertainty*.
- **Fail-closed (policy ambiguity, D8):** *authority* failures — the gate
  cannot positively establish that suppression is governed by attested
  policy. The *suppress capability* is withdrawn, not degraded.
  Suppression is not a degradable operation: there is no "degraded
  suppress," so ambiguity cannot resolve to a permissive default.
  Fail-closed names the state of the *suppress gate under ungrounded
  authority*.

Both move toward the human (the disposition is `passthrough` +
`policy_blocked:<why>`, never silent). The asymmetry that matters: error
handling degrades the decision; D8 withholds the capability. The old
missing-file semantics were fail-open *on authority* — the exact
inversion of the intended direction, and the hole this flip closes.

*Lineage: principal-systems data/AI constitution — "deterministic
guardrails always"; software constitution — "reliability is policy, not
hope." External consensus: authorization engines default-deny on
ambiguity — "No component should interpret an authorization failure as
permission" (vaelor Default-Deny Model); "Default deny. No matching allow
policy ⇒ deny" (agent-trust-broker IANUA-ATB-v0.1); "If policy evaluation
encounters … ambiguous authorization condition, evaluation SHALL
immediately terminate and return Deny. No partially evaluated
authorization context SHALL resolve to a permissive decision"
(sovereign-os SPEC-POL-001 POL-INV-002). D8 applies the same invariant to
the suppress capability: no partially established policy authority
resolves to permitted suppression.*

## 3. Ambiguity inventory

Each entry: the current behavior with file:line (base @ `3da2622`), the
fail-closed behavior, the evidence recorded, and the operator-visible
signal. Reason codes follow the existing vocabulary (P2 discipline — no
new `action` values; `policy_blocked:<why>` at the composition layer,
`gate.py:919`).

### A1 — missing policy-state file (THE flip)

- **Current:** `PolicyGate.can_suppress` → `(True,
  "policy_gate_not_configured")`, one-time stderr warning
  (`policy_lifecycle.py:525-535`). Pinned by
  `tests/test_watchdog.py:567` (`test_missing_file_allows_with_loud_warning`).
- **Fail-closed:** → `(False, "policy_gate_not_configured")`. Composition
  downgrades to `passthrough` + `policy_blocked:policy_gate_not_configured`
  (existing `gate.py:919` path — nothing new invented on the failure path).
- **Evidence:** decision record `lock_evaluation["policy_gate"] =
  {allowed: false, why: "policy_gate_not_configured", version: null,
  state: null, path: <file>, read_ts}`; named event `policy_gate_blocked`
  (§5); counter `sentinel_policy_gate_blocked_total{why="policy_gate_not_configured"}`.
- **Operator signal:** violet-banner-class payload (same operator channel
  as C1 `failopen_step_entered`); stderr/log warning **rate-limited**
  (e.g. ≤1/5 min), not once-per-process — a once-only warning is a
  normalization-of-deviance vector (the persistent outage becomes
  invisible to the next operator).
- **REQ-D8-001** (provisional; see §10).

### A2 — gate constructed without a policy hook (`policy_gate=None`)

- **Current:** `Gate._policy_allows_suppress` → `(True, "ok")`
  (`gate.py:817-818`). Production wires a hook in `receiver.py:1130,1191`,
  but the constructor default is `None` and tests/tools construct
  hook-less gates freely — an unwired gate suppresses with no enforcement
  at all.
- **Fail-closed:** `None` → `(False, "policy_gate_unwired")` →
  `passthrough` + `policy_blocked:policy_gate_unwired`.
- **Migration cost (named honestly):** every test/tool constructing a
  hook-less `Gate` and expecting suppress-reachability must pass an
  explicit test double (a `PolicyGate` on a fixture file with a LIVE
  version, or a stub returning `(True, "ok")`). The fence was test
  ergonomics; the explicit double preserves ergonomics without the hole.
  The Phase-1 build PR carries the test migration; no production call
  site may rely on the default.
- **Evidence / signal:** as A1, with `why="policy_gate_unwired"`.
  `version_info` unchanged (evidence-only pin).
- **REQ-D8-002.**

### A3 — store exists, unknown `policy_id` / empty policy list

- **Current:** `(False, "no_policy")` (`policy_lifecycle.py:285-289`) —
  already fail-closed. **Ratified, unchanged.** The comment says
  "fail-open loudly" but the return is `(False, …)`; the build renames the
  comment to match the behavior (fail-closed), no semantic change.
- **REQ-D8-003** (ratification).

### A4 — no effective version (all versions expired / none LIVE or REVIEW_DUE)

- **Current:** `(False, "policy_expired")` (`policy_lifecycle.py:290-292`)
  — already fail-closed. **Ratified, with one hardening:** the guard
  evaluates lifecycle clocks at decision time (the guard *interprets*,
  the watchdog *mutates*). A stored `LIVE` with `review_at` in the past
  is treated as REVIEW_DUE (still suppressible); a stored `REVIEW_DUE`
  with `grace_until` in the past is treated as EXPIRED →
  `(False, "policy_expired")`. Rationale: eternal friction — the watchdog
  may be down and `tick()` may not have run; the gate must not depend on
  the watchdog having executed the transition. Clock-derived evaluation
  is strictly more fail-closed than the stored string, never less.
  Clock source: the gate's existing decision-time clock (`_gate_now`
  discipline); the skew budget between watchdog and gate clocks is a
  named ops requirement in the build (shared NTP discipline).
- **Evidence / signal:** as A1, with `version`, `state` (as-read), and
  `effective_state` (clock-derived) both recorded.
- **REQ-D8-004.**

### A5 — frozen version (watchdog freeze)

- **Current:** `(False, f"policy_frozen:{reason}")`
  (`policy_lifecycle.py:293-294`) — already fail-closed. **Ratified,
  unchanged.**
- **REQ-D8-005** (ratification).

### A6 — corrupt / unreadable policy-state file

- **Current:** `(False, "policy_state_unreadable")`
  (`policy_lifecycle.py:540-542`) — already fail-closed. **Ratified, with
  the A1 loudness upgrade:** the re-warning becomes rate-limited rather
  than silent-after-first (a corrupt enforcement file is itself an
  incident; the operator must keep seeing it). Pinned by
  `tests/test_watchdog.py` (`test_gate_never_raises`) — kept.
- **REQ-D8-006** (ratification).

### A7 — unattested generation (Q3 (a) interplay)

- **Current (base):** `ConfigLoader` reads `thresholds.json` with schema
  validation only — no attestation (the R-1 finding). Out of D8's scope
  to fix; in D8's scope to *compose with*.
- **Fail-closed (with Q3 (a)):** `ConfigLoader` refuses any generation
  whose content-hash is not covered by a LIVE/REVIEW_DUE attested
  version → `ConfigRejected` → newest last-good generation, or
  refuse-to-start (r1-option-a §2.2 — "the same code path as refusing a
  malformed one"). D8's runtime fresh-read is authoritative on *policy
  state* (authority), not on *content match*: if the store says the
  effective version is LIVE/unfrozen, D8 allows the suppress path — the
  loader's refusal already prevented the unattested *content* from
  reaching the kernel. The residual the design closes: a store whose
  effective version is gone (all expired after a refusal) → A4
  `(False, "policy_expired")`. No D8 state may read "attested" from the
  retired `thresholds.json` — the retired file is never consulted.
- **Evidence:** the `ConfigRejected` event names the refused generation
  and the attestation gap; D8 records the store state it saw.
- **REQ-D8-007.** Do-not-do for this lane: D8 does not redesign the
  loader (gate-1's R-1 (a) build owns `src/`); this entry is the
  composition contract.

### A8 — lapsed emergency override (time-box elapsed, auto-revert not yet recorded)

- **Current:** `PolicyVersion.override_until` exists
  (`policy_lifecycle.py:103`); the file may still read LIVE with an
  elapsed `override_until` if `tick()` hasn't run the auto-revert — the
  guard today reads the stored state and would allow suppression on a
  should-have-reverted version.
- **Fail-closed:** clock-derived evaluation (as A4): `override_until` in
  the past and no revert recorded → `(False,
  "policy_override_lapsed")` → `passthrough` +
  `policy_blocked:policy_override_lapsed`. The override was time-boxed by
  construction (≤24h); an unreverted lapse is ambiguous authority, and
  ambiguous authority never suppresses. This directly answers the P1
  pre-mortem's "temporary-widening" incident: the widened gate cannot
  outlive its time-box silently.
- **Evidence / signal:** as A1, with `override_until` and the lapsed
  duration recorded. Violet banner: an override lapsing unreverted is an
  ops incident, not a quiet state.
- **REQ-D8-008.**

### A9 — revocation sweep pending (LIVE version touched by a revoked attestor)

- **Current:** the attestor ADR's `quarantine_revoked` freeze-sweep runs
  over live versions a revoked attestor touched; `can_suppress` checks
  only `v.frozen`. Between registry revocation and sweep completion, a
  touched LIVE version still returns `(True, "ok")` — a window in which
  revoked authority suppresses.
- **Fail-closed:** `(False, "policy_revocation_pending")` for any
  effective version marked revocation-pending, until the freeze-sweep
  clears the mark (setting `frozen`, after which A5 applies).
- **Required co-change (named, not hand-waved):** the revocation ceremony
  must write the `revocation_pending` mark into the policy-state file
  **atomically with** the registry revocation — the gate's hot path does
  not consult the attestor registry, and must not. If the ceremony
  cannot write the file, revocation is incomplete (fail-safe direction:
  revocation that doesn't reach the enforcement file didn't happen).
  This is a contract on the attestor/watchdog lane; D8 consumes it.
- **Evidence / signal:** as A1, with the revoked attestor id(s) and the
  pending-since timestamp. Highest-urgency banner: revoked authority is
  the trust-root-compromise shape.
- **REQ-D8-009.**

### A10 — mid-transition snapshot (fresh-read vs watchdog write)

- **Current:** writers use atomic `os.replace` (tmp-file + rename,
  `policy_lifecycle.py:75-78`); a fresh read sees a complete old or new
  file, never torn. Two reads inside one decision (`can_suppress` for
  the verdict, `version_info` for the D9 pin) may straddle a replace.
- **Design:** no change needed to the write path (verified atomic).
  Rule: **the composition's `can_suppress` call is authoritative for the
  decision**; the D9 `version_info` pin is evidence-only and may name an
  adjacent version — recorded as such, never "corrected" after the fact
  (history is never rewritten — the attestor ADR's own Type-1 ruling).
  The build adds a comment pinning this rule at both call sites so a
  future "optimization" doesn't merge the two reads.
- **REQ-D8-010.**

## 4. Disposition & reason vocabulary

No new `action` values (P2 vocabulary discipline). The composition keeps
its existing downgrade: `action = "passthrough"`, `reason =
f"policy_blocked:{why}"` (`gate.py:919`). The `why` set after the flip:

| `why` | meaning | direction |
|---|---|---|
| `policy_gate_not_configured` | file missing (A1) — **flipped to closed** | closed |
| `policy_gate_unwired` | hook is `None` (A2) — **new, closed** | closed |
| `no_policy` | unknown policy id (A3) | closed (ratified) |
| `policy_expired` | no effective version, clock-derived (A4) | closed (ratified+hardened) |
| `policy_frozen:<reason>` | watchdog freeze (A5) | closed (ratified) |
| `policy_state_unreadable` | corrupt file (A6) | closed (ratified) |
| `policy_override_lapsed` | override time-box elapsed, unreverted (A8) — **new** | closed |
| `policy_revocation_pending` | revoked-attestor touch, sweep incomplete (A9) — **new** | closed |
| `ok` | LIVE/REVIEW_DUE, unfrozen, clocks clean, no marks | allow path |

Why `passthrough` and not `page_now`: D8 does not re-decide the alert —
it *withholds suppression*. The passthrough path still fans out per the
policy table (including `page_now` where the kernel's own branches say
so). D8's job is to close the suppress capability, not to choose the
page flavor. (Consistent with the existing frozen/expired/unreadable
downgrade — the flip extends the existing semantics, it doesn't invent
new ones.)

## 5. Evidence recorded (per blocked suppress)

1. **Decision record:** `verdict.lock_evaluation["policy_gate"] =
   {allowed: false, why, version, state, effective_state, frozen,
   override_until, read_ts, path}`. The D9 counterfactual receipt
   replays the same composition (DR-26) — the pin is byte-identical
   evidence, not a second implementation.
2. **Named event:** `policy_gate_blocked{why, policy_id, version?,
   trace_id}` on the event log, violet-banner-class operator payload
   (same channel as C1 `failopen_step_entered` — the operator already
   watches this channel for "the gate changed shape" signals).
3. **Counter:** `sentinel_policy_gate_blocked_total{why}` for dashboards
   and the staging-lab kill-switch drill (dev-1).
4. **Log warning:** rate-limited (≤1/5 min per `why`), never once-per-process.

What is *not* recorded: the policy content itself (content lives in the
store; the decision record carries the `content_hash` pin via D9, not the
thresholds — zero data-model leakage, principal-governance §2).

## 6. Operator-visible signal summary

| state | disposition | banner | warning cadence |
|---|---|---|---|
| A1 missing file | `passthrough` / `policy_blocked:policy_gate_not_configured` | violet, persistent until initialized | rate-limited |
| A2 unwired hook | `passthrough` / `policy_blocked:policy_gate_unwired` | violet (config error — should never occur in prod) | rate-limited |
| A4/A8 clock lapse | `passthrough` / `policy_blocked:policy_expired` or `:policy_override_lapsed` | violet; A8 additionally pages the override as an incident | rate-limited |
| A9 revocation pending | `passthrough` / `policy_blocked:policy_revocation_pending` | highest-urgency (trust-root shape) | rate-limited |

## 7. Q3 (a) interplay & atomic landing

r1-option-a §2.4 and §2.6 name the dependency both ways; this design
records the contract:

1. **D8 and the R-1 (a) loader flip land together.** Shipping (a) with D8
   still fail-open re-opens the "watchdog never set up" state as the
   least-supervised state (r1-option-a §2.6). Shipping D8 alone —
   without the store-initialization ceremony (the R-1 (a) operator CLI,
   `r1-cli-ceremony-notes.md`) — turns every fresh deploy into a
   page-everything-suppressible-alerts state until someone initializes
   the store. That is the *intended* fail-closed behavior, but without
   the ceremony it is an unplanned outage, not a designed one.
2. **Migration order** (execution-doctrine: validate → shadow → canary):
   freeze current `thresholds.json` → CLI ceremony creates the seeded
   LIVE version (founder-seed-style event, per r1-option-a §2.5) →
   dual-read shadow window (loader resolves from the store AND diffs the
   on-disk file; diffs emit `config_rejected`-class telemetry, no
   blocking) → D8 flip + loader flip together → on-disk file renamed
   `thresholds.json.retired` (never read; writers fail loudly).
   **Genesis-validation gate (review amendment, PR #91):** the atomic
   landing is blocked until the initialization ceremony executes
   end-to-end **with the BLOCK-3 genesis-validation rule landed** (R-1(a)
   lane). Rationale: at genesis, seeding vN from the on-disk file makes
   diff-vs-LIVE verification vacuous — a pre-widened `thresholds.json`
   could be attested as the trusted root, and D8's "positively
   established authority" semantics would then rest on a laundered seed.
   The T+9 freeze gate MUST check for the genesis-validation rule, not
   merely that the ceremony ran.
3. **What breaks (said plainly):** the 3 AM "widen the gate in
   `thresholds.json`" muscle memory stops working — that is the point.
   The sanctioned emergency path is the B3 emergency-override with
   auto-revert (loud, time-boxed, self-healing); A8 exists so the
   time-box is enforced by the gate even if the watchdog lags.
4. **Rollback:** the flip is a behavior change behind no flag (it is a
   trust semantic, not a feature — principal-governance operating rule 4
   is satisfied by the shadow window + the last-good chain, not by a
   flag that could itself be flipped silently). Rollback = revert the
   two commits; the store file is append-only history, so revert is safe.

## 8. Pre-mortem

*It is one year later and the D8 flip has failed catastrophically:*

1. **The ceremony never shipped; the flip did.** Fresh deploys
   page-stormed on day one; operators learned to point
   `SENTINEL_POLICY_STATE` at a hand-written "initialized" file to stop
   the noise — governance theater, reborn, now with a blessed bypass.
   *Mitigation:* §7 atomic landing — the flip PR is blocked until the
   initialization ceremony executes end-to-end once (the R-1 verify
   condition); the T+9 freeze gate checks this.
2. **Clock skew paged through a legitimate emergency.** The gate's clock
   ran ahead of the watchdog's; a live override lapsed early by the
   gate's reading during a real SEV1, suppressing nothing and paging the
   team that was already paged. *Mitigation:* the skew budget is a named
   ops requirement (§3 A4); `override_until` comparisons carry an
   explicit grace (documented in the build, e.g. 60s) so sub-minute skew
   cannot flip authority; skew beyond the budget is itself a
   violet-banner incident.
3. **The None-hook flip was migrated with a permissive stub.** A new
   internal tool constructed `Gate()` for batch replay, copied the
   "test double" pattern, and shipped a stub returning `(True, "ok")`
   into a production-adjacent path — the hole moved, wearing a costume.
   *Mitigation:* the stub pattern is named in the build as
   test-only; a CI import-graph assertion (OPERATING-RULES §3.3
   anti-corruption) forbids the stub module outside `tests/`; Tripwire's
   10% spot reviews sample for it.

## 9. Alternatives considered and rejected

- **Keep missing-file fail-open, escalate loudness instead** (violet
  banner + page the on-call to initialize): rejected — loudness is not
  authority. The state needing the most supervision would retain the
  most permissive semantics; `deterministic-gate.md` P1 already
  adjudicated this as "an unquantified permanent" degradation.
  (principal-mindset anti-theater audit: a guard that permits
  suppression when it cannot see the policy is theater.)
- **Refuse-to-start when the file is missing** (hard boot gate):
  rejected — worse than the per-decision close. A missing file at boot
  would take the whole paging path down (availability loss); D8 keeps
  paging alive and withholds only suppression. Eternal friction: the
  gate must page even when governance is broken.
- **Default-allow with audit-only** (record the missing-file suppressions,
  page nobody): rejected — audit is detective, not preventive; a
  suppressed SEV1 is not recoverable by an audit row. (principal-systems
  software constitution: "reliability is policy, not hope.")
- **Keep `policy_gate=None → (True, "ok")` for test ergonomics**:
  rejected — Chesterton's fence examined (§1): the fence exists for test
  ergonomics, not production semantics; the explicit test double
  preserves ergonomics (§3 A2).

## 10. Skill & Evidence

*Binding skill clauses (OPERATING-RULES §2.1 — the specific clause, not
the badge):*

- **principal-systems — eternal friction:** "the network will drop
  packets … architect for that world, not the demo world." Binds A1/A2:
  the "watchdog never set up" state is the state a rushed first
  deployment ships in; the design refuses to make it the most permissive
  state. Binds A4/A8: the watchdog may be down; the guard evaluates
  clocks itself rather than trusting `tick()` ran.
- **principal-systems — data/AI constitution:** "deterministic
  guardrails always (no probabilistic system holds absolute veto over a
  critical path — hardcoded fallback)." Binds §2.1: suppression is not
  a degradable operation, so ambiguity withholds rather than degrades.
- **principal-systems — software constitution:** "reliability is policy,
  not hope." Binds the rejection of loudness-instead-of-flip and
  audit-instead-of-flip (§9).
- **principal-governance — SSOT:** "every entity owned by exactly one
  service." Binds A7: thresholds are owned by the attested lifecycle;
  D8 reads the store's authority state, never the retired file.
- **principal-governance — unforgiving API design:** the `why`
  vocabulary (§4) is a contract — versioned, additive-only, pinned by
  tests (P2 discipline).
- **principal-mindset — anti-theater audit (§11):** "a practice that
  never says no is theater." Binds the whole flip: a suppress guard
  that has never blocked suppression on missing authority is decoration.
  The drill in §5 (counter + staging-lab) is how the audit checks the
  guard still bites.
- **execution-doctrine — validate → shadow → canary (§6):** binds §7's
  migration order; the dual-read shadow window is honest telemetry
  before enforcement.
- **No skill clause invented** where none binds: the violet-banner
  channel choice (§5) is repo prior art (C1 `failopen_step_entered`),
  cited as such.

*Prior art consulted (repo — OPERATING-RULES §2.1 permits prior-art
citation for the design stage):* `deterministic-gate.md` §§1.3(e), 1.5,
2.1, P1; attestor ADR (quarantine sweep, trust-root shape);
`r1-option-a-canonical-content.md` §§2.2, 2.4–2.6;
`r1-cli-ceremony-notes.md` (ceremony shape); `research/architecture-patterns.md`
§6.3 (Envoy panic mode — the quantified-degradation contrast);
`research/swe-discipline.md` (design-doc culture).

*Web sources consulted (accessed 2026-10-05):*

- https://github.com/foxy-prog/vaelor/blob/HEAD/README.md — Default-Deny
  Model: "Unknown capabilities … and ambiguous security state must fail
  closed. No component should interpret an authorization failure as
  permission." *Changed the design:* confirmed the §2.1 invariant is the
  industry consensus for authorization-shaped decisions, and supplied
  the "escalate ≠ allow" framing echoed in §4 (D8 withholds; it does not
  re-decide).
- https://github.com/domannand95-svg/sovereign-os/blob/HEAD/docs/specifications/SPEC-POL-001.md
  — POL-INV-002 Fail-Closed Default Routing: "No partially evaluated
  authorization context SHALL resolve to a permissive decision."
  *Changed the design:* sharpened A10's rule — the D9 pin may name an
  adjacent version but is never "corrected," i.e. no partially evaluated
  context resolves anything.
- https://github.com/irsoctierdt/agent-trust-broker/blob/HEAD/docs/IANUA-ATB-v0.1-Identity-and-Policy-Broker.md
  — "Default deny. No matching allow policy ⇒ deny … Escalate ≠ allow."
  *Changed the design:* the evidence table (§5) records the pin
  (`content_hash` via D9) rather than the content — their auditability
  section's "auditable decision record" list matches §5's four items.

*Tool evidence (read-only reconnaissance on base `3da2622`):*
`PolicyGate.can_suppress` (`src/sentinel/policy_lifecycle.py:523-542`);
`PolicyStore.can_suppress` (`:283-295`); `PolicyVersion` fields
(`:95-126`); atomic write (`:75-78`); `Gate._policy_allows_suppress`
(`src/sentinel/gate.py:815-824`); composition downgrade
(`:919`); `Gate.__init__` hook param (`:313,333,407`); production wiring
(`src/sentinel/receiver.py:1130,1191`); pinning tests
(`tests/test_watchdog.py:567-586`, `test_gate_never_raises`).

## 11. Review, Type, and reversal

- **Type:** 1-adjacent (labeled explicitly). The direction (fail-closed)
  was decided by Aditya in the Q3 decision; this doc designs its
  execution. Reversal conditions: reopen if the atomic-landing ceremony
  (§7) proves unimplementable without stranding fresh deploys — then the
  flip re-opens as flip-vs-loudness with the blocking mechanism named.
  Cost overruns alone do not reverse a trust semantic.
- **Review routing (OPERATING-RULES §1.6):** Oracle + Forge approve;
  Tripwire reviews the design and the build PR (standing — every
  policy-change design); Vault sign-off on the policy-change path.
  Author ≠ reviewer, always.
- **Checkpoints:** T+3 v0.1 (this doc) → T+6 Oracle-reviewed → T+9
  frozen → T+12 merged review. The T+9 freeze gate verifies the §7
  atomicity precondition (ceremony executed once end-to-end).

## 12. Build handoff (Phase-1 build lane)

**Files to change:**

- `src/sentinel/policy_lifecycle.py` — `PolicyGate.can_suppress`
  (:523-542): A1 flip; A4/A8 clock-derived evaluation; A6 rate-limited
  re-warning; A9 revocation-pending mark check; docstring rewrite (the
  current docstring documents the fail-open semantics — it must not
  survive the flip).
- `src/sentinel/policy_lifecycle.py` — `PolicyStore.can_suppress`
  (:283-295): A3 comment fix ("fail-open loudly" → fail-closed
  wording); A9 mark check placement.
- `src/sentinel/gate.py` — `_policy_allows_suppress` (:815-824): A2
  flip (`None` → `(False, "policy_gate_unwired")`); keep the
  never-raises wrapper.
- `src/sentinel/gate.py` — `_resolve_suppress_path` (:919): unchanged
  path, extended `why` set (§4 table).
- `src/sentinel/policy_lifecycle.py` — revocation ceremony (attestor/
  watchdog lane): A9 atomic mark write — **co-change, not this lane**.
- Event/counter emission points (§5 items 2–4) — build wires into the
  existing event-log and metrics surfaces.

**Tests to flip / add:**

- FLIP `tests/test_watchdog.py:567`
  (`test_missing_file_allows_with_loud_warning`) → asserts `(False,
  "policy_gate_not_configured")` + rate-limited warning.
- KEEP `test_gate_never_raises` (A6) — still green.
- ADD: A2 unwired-hook test; A4 clock-derived expiry test (frozen
  wall-clock fixture); A8 lapsed-override test; A9 revocation-pending
  test (fixture file with the mark); A10 straddle test (pin may name
  adjacent version; decision used the composition's read).
- ADD: import-graph assertion forbidding the test stub outside `tests/`
  (pre-mortem item 3).

**Do-not-do for the build lane:** does not redesign C3/C4 (other lanes);
does not implement the Q3 (a) loader refusal (gate-1's R-1 build); does
not touch the attestor registry; does not add new `action` values; does
not "optimize" the two fresh reads into one (A10 rule).

---

*Delivered as the gate-2 T+3 drop. v0.1 — comments to Oracle; rebuttals
in writing per OPERATING-RULES §4.1.*
