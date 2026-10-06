# MATH_DESIGN — Track 4: Rotation Story (C4)

Lane: `lane/build-t4-rotation` · Contract: C4 · 2026-10-06

## 1. The problem, from first principles

A secret is a *capability*: whoever presents it is trusted. Three properties
make a capability system survivable:

1. **Revocability** — a leaked secret must be killable without downtime.
2. **Atomicity of change** — replacing a secret must never pass through a
   state where *no* valid secret exists (outage) or where *two authorities*
   disagree about which secret is valid (split-brain).
3. **Auditability** — every change must be attributable (actor, time) and
   ordered (what the canonical secret was before/after).

The pre-T4 state violates all three: BYOK keys are single strings
(`{name: value}`), the webhook HMAC secret is a single env var, and the
operator bearer token (C1) has no store at all yet. A token trusted on
signature/value alone is unrevocable — rotation means delete-then-add, which
is a *documented failure* (proof in §5).

## 2. The construction

### 2.1 Secret record

Each secret name maps to a record, not a string:

```
{name: {primary: S, secondary: S | null, generation: int}}
```

- `primary` — the canonical secret. Verification **must** accept it.
- `secondary` — the staged successor (or the demoted predecessor during
  grace). Verification **may** accept it (dual-accept).
- `generation` — a monotonically increasing epoch. Incremented **only** at
  promote (the instant the canonical primary changes).

Verification is the disjunction:

```
verify(name, c)  ⟺  cteq(c, primary) ∨ (secondary ≠ null ∧ cteq(c, secondary))
```

with `cteq` = `hmac.compare_digest` (constant-time; no oracle on prefix).

### 2.2 The ceremony (state machine)

```
  ┌─────────┐  stage_secondary   ┌──────────────┐  verify_secondary  ┌──────────────┐
  │ primary │ ────────────────► │ pri + staged │ ────────────────► │ pri + staged │
  │ gen = g │                   │ gen = g      │   (proof of work)  │ gen = g      │
  └─────────┘                   └──────────────┘                    └──────────────┘
                                                                       │ promote
                                                                       ▼
  ┌─────────┐   retire           ┌──────────────┐
  │ primary'│ ◄──────────────── │ primary' +   │
  │ gen = g+1│                  │ primary(grace)│
  └─────────┘                   │ gen = g+1    │
                                └──────────────┘
```

Why this order (and not delete-then-add):

- **stage_secondary**: the new secret exists *alongside* the old. From this
  instant both verify (overlap). No request can fail for "unknown secret"
  during cutover — in-flight retries with the old secret still pass.
- **verify_secondary**: a *proof-of-work* step — the operator proves the new
  secret actually functions (test signature / test auth) *before* it becomes
  canonical. Promoting an untested secret is how you page yourself at 3am.
- **promote**: secondary → primary, old primary → secondary (demoted grace
  slot, so in-flight old-secret requests drain), **generation g → g+1**.
  The generation bump is the revocation epoch (see §2.3).
- **retire**: drops the grace slot. After this instant the old secret is
  cryptographically dead — `verify(old) = false`, provably.

**Invariant (no-outage):** at every ceremony step, at least one valid
credential verifies. Proof by case analysis on the four transitions above:
each transition preserves ≥1 verifying value (stage adds, verify is
read-only, promote swaps primary↔secondary, retire removes only the
demoted predecessor). Delete-then-add violates this (see §5).

**Invariant (no-split-brain):** `primary` is single-valued; the store file
is written atomically (`os.replace`); there is exactly one canonical
secret per name at all times.

### 2.3 Generation as revocation epoch (break-glass)

A break-glass token is minted *bound to a generation*:

```
issue(name) → token t, recorded as (sha256(t), gen = current(name))
verify(name, c):
    cteq(c, primary) ∨ cteq(c, secondary) ∨
    (∃ issued i: cteq(c, i.token) ∧ ¬i.revoked ∧ i.gen == current(name))
```

**Theorem (leak death):** a break-glass token issued at generation g
verifies only while `current(name) == g`. `promote` sets
`current(name) = g+1`, so every pre-rotation break-glass token dies at the
next generation *without any per-token revocation action*. Explicit
`revoke` exists for mid-generation kills.

This is why the contract says "a token trusted on signature alone is
unrevocable": revocation needs an *epoch* the verifier can check, not just
the token's own bits. The generation counter is that epoch.

### 2.4 Audit

Every transition appends one audit record:

```
{ts, actor, action, secret_name, generation_before, generation_after, detail}
```

- `generation_before/after` makes the epoch change *auditable*: promote is
  the only action with `after = before + 1`; all others have `after == before`.
- **No secret material in audit records, ever** — names, generations, actors
  only. (Enforced by test: JSON-serialize the audit trail and assert no
  staged value appears as a substring.)
- Default sink: JSONL append to `<store>.audit.jsonl` (0600). The store
  accepts an injected `audit` callable so tiers can redirect (Track 3 owns
  the decision audit vocabulary — rotation audit is key-management audit and
  deliberately does not touch the frozen `EventLog` event types).

## 3. Where it applies (contract C4)

| Secret | Store | Verification seam |
|---|---|---|
| Operator bearer (C1) | `platform/server/keystore.py` — `operator_bearer` record; `ensure_operator_token()` bootstraps `secrets.token_urlsafe(32)` once | Track 1 middleware calls `verify("operator_bearer", presented)` — dual-accept, gen-gated |
| Webhook HMAC | `webhook_secrets.json` via `src/sentinel/keystore.py`; env `SENTINEL_WEBHOOK_SECRET` = bootstrap fallback | `_webhook_sig_failure_reason_any(candidates, …)` tries primary → secondary → env; pure single-secret check untouched |
| BYOK (`pagerduty_routing_key`, `jev_api_key`) | `integrations.json` records (both tiers' twins); legacy `{name: "value"}` strings decode to `{primary: value, secondary: null, generation: 1}` | `get()` returns primary (back-compat); new `verify()` dual-accepts; `set()` = audited immediate override (gen+1, clears secondary) |

Twin rule (established precedent): `src/sentinel/integrations.py` and
`platform/server/integrations.py` share the file format — the record
migration lands in **both**, noted in both docstrings.

## 4. API + console contract (for Track 8; Track 1 wires auth)

`POST /api/v1/keys/{name}/rotate` (C1-authed), body `{stage, value?}`:

- `stage=stage_secondary` + `value` → `200 {name, generation, secondary_staged: true}`
- `stage=verify_secondary` + `value` → `200 {ok: true}` or `422 {ok: false, reason}`
- `stage=promote` → `200 {name, generation_before, generation_after}`
- `stage=retire` → `200 {name, generation, retired: true}`
- `stage=status` → `200 {configured, secondary_staged, generation}` (no values)

Console contract: rotation panel shows `generation` + staged state machine
(`idle → staged → verified → promoted → retired`); promote/retire are
separate deliberate buttons; every transition surfaces its audit row
(actor, ts, gen before→after). Implemented in `platform/server/rotation_api.py`
as a framework-free `RotationService` — one import for app.py wiring
(coordinator/T1 own the wiring; this lane does not touch middleware).

## 5. The documented failure: delete-then-add

Delete-then-add rotation (delete primary, then set new) passes through:

```
{primary: OLD} → {} → {primary: NEW}
```

In the middle state `verify(OLD) = false ∧ verify(NEW) = false`: **total
auth outage** — every in-flight request fails, and the new secret has had
no proof-of-work. If the `set` fails (crash, bad paste, validation
reject), the outage is permanent until manual recovery. The ceremony's
overlap exists precisely to make this state unreachable: the test
`test_delete_then_add_is_a_failure` constructs the middle state and
asserts the outage, while `test_ceremony_never_dead` asserts the ceremony
never enters it.

## 6. Alternatives considered

- **Versioned secrets list (keep N old secrets):** unbounded verification
  set; revocation becomes "remember to prune". Rejected — the grace slot is
  exactly one predecessor, pruned by the explicit retire step.
- **Timestamps/expiry on secrets:** clock dependence in verification;
  expiry is a policy, not a mechanism. Rejected — generation epochs are
  clock-free.
- **Rotation via EventLog audit:** frozen Type-1 vocabulary; rotation is
  key-management, not a decision event. Rejected — separate JSONL audit.

## 7. Test plan

`tests/test_keystore.py` (engine) + `platform/server/tests/test_keystore.py`
(twin) + ceremony/rotation additions in `tests/test_integrations.py`:

1. overlap: after `stage_secondary`, `verify(old)` and `verify(new)` both true.
2. post-retire: after full ceremony, `verify(old)` false, `verify(new)` true.
3. audit: 4 ceremony steps → 4 records, each with actor/ts/gen before/after.
4. generation: increments exactly once, at promote.
5. break-glass: issued at gen g verifies; after promote (gen g+1) it does not.
6. delete-then-add: middle state verifies nothing (documented failure).
7. no secret material in any audit record or log capture.
8. legacy format migration: plain-string values decode to gen-1 records.
9. webhook dual-accept: signature under old OR new secret verifies during
   overlap; only new after retire.
10. constant-time: verification uses `hmac.compare_digest` (assert via
    source inspection is brittle — instead assert *behavioral*: wrong-length
    and wrong-value candidates both return false without exception; plus a
    code-review note).

Full local suite (`python3 -m unittest discover`) green before push.
