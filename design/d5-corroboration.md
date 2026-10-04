# D5 — Corroboration leg in the live suppress conjunction (ADR-019)

**Lane:** `lane/d5-corroboration` · **Owner:** Builder D5 · **Status:** ADOPTED-DESIGN → implemented
**ADR:** ADR-019 (ratification conditions a–d) · **Type:** 1 — the trust model.
"Silence takes two" is a brand-level promise; getting this leg wrong is how a
company ends up with confident silence.

This doc is the Type-1 record: problem, what corroboration means, placement,
the silence floor, the strict-signature resolve path, freshness, threat model,
alternatives considered, pre-mortem, and the done-checklist.

---

## 1. The problem

Today, suppression = the model's verdict + the policy gates (value locks,
freshness legs, D8 policy gate). That is a *single witness* for silence:
one Jev answer plus tables that a sufficiently privileged operator (or a
sufficiently shaped input) can satisfy alone. A false page costs trust; a
false silence costs the company. Thresholds don't distinguish *kinds* of
evidence — corroboration does.

ADR-019's ratification bar: (a) explicit corroboration leg in the live
suppress conjunction, evaluated from evidence; (b) the silence floor
versioned, audited, two-person-changed; (c) resolve-as-corroboration requires
strict signature verification; (d) corroboration evidence carries freshness.

## 2. What counts as corroboration

A suppress decision must ALSO be corroborated by an independent signal — not
the same model, not the same data path. The leg requires
`≥ floor.min_evidence` pieces of valid evidence naming the SAME fingerprint,
each fresh per the floor's per-kind bound. Three kinds, each a genuinely
different witness than the Jev answer and the threshold table:

| Kind | Witness | Why it's independent |
|---|---|---|
| `signed_resolve` | A human (or authorized automation) attests "this fingerprint resolved-as-benign", authenticated by an Ed25519 signature from an ACTIVE attestor in the attestor registry | Signature-bound human attestation; the Jev model cannot mint it and thresholds.json cannot satisfy it |
| `second_check` | An independent verifier named in the floor's `second_check_verifiers` list (a different model vendor, a deterministic probe — never the Jev call that produced the verdict; the leg rejects `verifier_id == jev_model`) | A separate computation path; independence is established at wiring time, named in config, auditable |
| `allowlist_attestation` | The dual human attestation ceremony already required on allowlist entries (ADR-013 interim): two distinct named humans, distinct from the entry author, each carrying an evidence_ref, within TTL | The *ceremony* (two humans) is a different witness than the *list* (leg-3 membership). A list row can be planted by automation; the ceremony requires two humans |

What does NOT count (deliberately):
- **Unsigned "resolve" claims** (bare strings, `[RESOLVED]` markers in alert
  text, receiver fail-open ingest rows). The receiver's fail-open ingest is
  correct for *paging* (DR-2/DR-11) but must never mint corroboration evidence
  (ADR-019 cross-item note 1). Only the strict-signature path (§5) mints
  `signed_resolve` evidence.
- **The model's own answer, re-sampled.** N samples of one witness are still
  one witness.
- **Historical suppress rate** ("we suppressed this fingerprint before") —
  circular: a bad suppress would self-corroborate and entrench false silence.

## 3. Placement in the conjunction

```
suppress ⟺  model verdict == suppress
          ∧ value locks (prob / conf / allowlist)        [existing]
          ∧ freshness legs fresh                          [ADR-014/D1]
          ∧ D8 policy gate allows                         [existing]
          ∧ CORROBORATION LEG: ≥ floor.min_evidence        [D5 — NEW, LAST]
               fresh, kind-authorized, independently-minted evidence
```

The leg is the LAST check before silence, in `Gate._on_answered`, after the
policy kernel and the D8 gate. Order rationale: everything that can *grant*
suppression must be proven before the final witness is consulted — the leg is
the final fail-safe, not an early filter.

**D6 firewall interplay (placement note).** The firewall hook sits in
`Gate._decide` between S1 (structural) and S2 (the Jev race). A flagged alert
pages `page_now` immediately and never arms the race — it never reaches the
corroboration leg, and the leg never sees firewall input. The two are
complementary, not competing:
- The firewall screens *hostile input* (prompt injection, fake `[RESOLVED]`
  markers) → fail-closed page. It never suppresses.
- Corroboration screens *insufficient evidence for silence* → uncorroborated
  suppress becomes `page_now` (reason `uncorroborated`).
- Injection → page **regardless of corroboration**: a firewall hit short-circuits
  before the race, so even a fully corroborated alert pages if it carries
  injection markers. Corroboration can never grant what the firewall denied.

**Un-corroborated suppress** → `Disposition(action="page_now",
reason="uncorroborated")`. Uncertainty pages (Law 7): "we want silence but
can't prove it twice" is uncertainty, and the fail direction is toward the
human. Never silent, never `passthrough` (which would look like a decision).

**Visibility.** Every decision record carries the leg's verdict:
- `lock_evaluation["corroboration"] = {"verdict": "pass"|"fail",
  "detail": "<which kind fired, or why none did>", "floor_version": N}`
- the `decision_made` payload body gains a `corroboration` key (extra body keys
  are permitted by the event-log validator).
- the shadow path (`_on_late_answer`) mirrors the leg so `would_have_suppressed`
  is the faithful counterfactual — what the gate *would* have decided.

## 4. The silence floor — versioned, audited, two-person-changed

The "silence floor" (minimum evidence to stay silent) is a versioned config,
not a magic constant. `src/sentinel/corroboration.py`:

```json
{"format": 1, "floor_id": "suppression", "version": 3,
 "min_evidence": 1,
 "allowed_kinds": ["signed_resolve", "allowlist_attestation"],
 "max_age_s": {"signed_resolve": 86400, "second_check": 3600,
               "allowlist_attestation": 2592000},
 "second_check_verifiers": [],
 "updated_at": "...", "updated_by": "...",
 "history": [{"version": 2, "changed_at": "...", "changed_by": "...",
              "direction": "strengthened", "two_person": false, ...}]}
```

- **Versioned:** strictly monotonic versions (v+1 only); history appended,
  never rewritten. Every suppress names the floor version that authorized it.
- **Audited:** every change goes through `SilenceFloorStore.propose()` and is
  emitted to the event sink (`silence_floor_changed`) + appended to the
  floor's own history. A change that isn't event-logged didn't happen.
- **Two-person rule for lowering:** any change that *weakens* the floor —
  `min_evidence` decreased, a `max_age_s` increased, a kind ADDED to
  `allowed_kinds`, a verifier ADDED to `second_check_verifiers` — requires
  two distinct ACTIVE attestors, distinct from the author, with
  content_hash-bound signatures over the floor transition
  (`policy_id="silence_floor"`, `from_state="vN"`, `to_state="vM"`), verified
  through the attestor registry. Pure strengthening (raise `min_evidence`,
  shorten a bound, remove a kind) is single-operator — raising the floor is
  the safe direction, so it must be fast, not ceremonial.
- **Fresh-read discipline:** the store re-reads the floor file on every
  `current()` call — no boot cache. A floor change is effective on the next
  decision (mirrors the attestor registry's Type-1 rule).

Defaults (Type 2): `min_evidence=1`; `signed_resolve` ≤ 24h, `second_check`
≤ 1h, `allowlist_attestation` ≤ 30d (aligned with the ADR-022 30-day
re-validation clock). `second_check` is NOT in the default `allowed_kinds`:
enabling a new corroboration kind is a trust-model change, so it must pass
the two-person bar — the floor change itself is the ceremony.

## 5. The strict-signature resolve path

`sign_resolve(private_hex, fingerprint, resolved_by, decided_at, note)`
signs canonical bytes over **(fingerprint, resolved_by, decided_at, note)**.
The bindings close three gaps:
- **fingerprint binding** — a resolve for fingerprint A cannot corroborate
  fingerprint B (the leg matches `evidence.fingerprint == alert.fingerprint`).
- **content binding (note)** — an attestation for a benign note does not
  transfer to a swapped note (same closure as attestor-identity's
  `content_hash` binding).
- **attestor binding** — only ACTIVE attestors in the sealed registry verify;
  revocation is effective on the next decision; unsigned claims
  (`signature_hex=None`) are rejected by construction.

Verification happens **in the leg** (deterministic crypto, no I/O, ~50µs per
evidence, tiny evidence counts): `verify_resolve` against the registry's
public key + `is_active` + freshness. The provider that mints evidence may
pre-verify, but the leg trusts no provider — the strictness lives where the
decision lives (ADR-019 condition (c): this is the one path where fail-open
ingest is unacceptable).

## 6. Freshness on corroboration evidence

Every evidence carries `decided_at`. The leg enforces the floor's per-kind
`max_age_s` bound plus a 5-minute future tolerance (clock-skew guard —
future-dated evidence beyond tolerance is rejected, not trusted). Stale
evidence doesn't count, and the rejection is named in the leg detail
(`stale: age=37h > max_age=24h`). The rationale: a resolve from last month's
incident says nothing about tonight's recurrence — corroboration is a claim
about the present, and the present expires.

## 7. Threat model: what attack does the leg stop that model+policy don't?

1. **Model-answer forgery.** Attacker shapes the Jev answer (injection that
   reads benign to the firewall, or a compromised vendor response) and the
   threshold table is already satisfied → today: silent suppress. With the
   leg: silence additionally requires a signature from an attestor key or a
   named independent verifier — the attacker must win twice, on two different
   trust roots.
2. **Policy-layer compromise.** `thresholds.json` hot-edited, or a fake
   allowlist entry planted without ceremony → value legs pass → today:
   silent. With the leg: `signed_resolve` doesn't read thresholds, and
   `allowlist_attestation` requires the two-human ceremony the attacker can't
   fake without two attestor private keys.
3. **Stale-evidence replay.** Replaying an old signed resolve from a resolved
   incident → the freshness bound kills it (named in the audit trail).
4. **Single-operator silence creep.** An operator quietly lowering the bar for
   silence (floor weakening) → two-person rule + event-logged versions make
   creep visible and bilateral.
5. **Textual fake-resolve downgrade.** `[RESOLVED]` in the alert title trying
   to ride the resolve path → the D6 firewall flags the marker as injection
   (fail-closed page); the real resolve path demands a signature, so there is
   no downgrade from text to evidence.

**Explicitly NOT stopped** (other lanes' domains): two colluding attestors
(attestor compromise response — `quarantine_revoked`); a second_check
verifier that always agrees (wiring-time trust; the floor names it, audits
expose it — Tripwire should red-team it); the same human error minting both
the allowlist entry and the resolve (process control, not code).

## 8. Alternatives considered and rejected

- **Two-model voting (a second Jev call).** Doubles vendor spend, doubles
  latency-budget pressure, and shares the vendor's correlated failure mode.
  Corroboration is about *kind* independence, not more of the same. Rejected.
- **Historical suppress rate** ("we suppressed this before"). Circular — a
  bad suppress self-corroborates. Rejected.
- **Quorum of model samples.** N samples of one witness = one witness.
  Rejected.
- **Reusing the D8 policy gate.** The policy gate freezes/canaries the
  *policy*; it provides no second witness for a *decision*. Complementary,
  not a substitute.

## 9. Pre-mortem (top 3)

1. **The evidence pipeline dies quietly.** The resolve provider stops minting
   → every would-be-suppress pages → alert fatigue → operators demand the leg
   be ripped out at 3 AM. *Mitigation:* the `uncorroborated` page rate is a
   first-class metric (Pager's SLO); disabling a kind is a floor weakening
   (two-person, event-logged) — the pressure has a governed path, not a
   backdoor.
2. **Stale floor file.** Shared-disk skew serves an old floor version.
   *Mitigation:* fresh-read per suppress decision; the floor version is pinned
   in every decision record (audits spot staleness); monotonic versions make
   any rollback visible.
3. **Allowlist attestation as self-corroboration.** The entry author "attests"
   their own entry. *Mitigation:* the leg reuses `dual_attestation_valid` —
   attestors must be distinct from each other AND from the entry author.

## 10. Done-checklist

- **10× scale:** floor read is O(1) JSON per suppress decision (the rare path;
  the hot page/passthrough path never touches it). Signature verification is
  ~50µs × tiny evidence counts. Providers are engine-wired caches, not
  per-alert scans.
- **Third-party down 4h:** no third party in the leg — registry and floor are
  local, sealed files.
- **Junior deploys a stale floor:** monotonic version check rejects it; the
  gate fails toward paging, never silence.
- **Global vs local:** no amount of threshold tuning distinguishes evidence
  kinds — the leg is the global fix (principal-systems law 1).
- **Truck factor:** this doc + the leg's verdict in every decision record +
  the floor's event-logged history. The team runs it from logs + code alone.

## 11. Ratification evidence (for ADR-019)

- `tests/test_corroboration.py`: un-corroborated suppress → `page_now`
  (never silent); unsigned resolve doesn't corroborate; stale evidence
  doesn't corroborate; floor weakening requires two signatures; injection
  pages regardless of corroboration.
- Permanent CI fixture (Tripwire's bar): the model alone, uncorroborated,
  cannot produce a `suppress` disposition — asserted in the suite, not in
  prose.

## 12. Considered edge: S1 dedup replays

The S1 `duplicate` path replays a prior disposition (reason `dedup`) without
re-running the conjunction. This is NOT a new suppression: the replayed
decision already passed the full conjunction — including this leg — at most
one dedup window (default 300s) earlier, and the floor's tightest evidence
bound is 1h. A replay cannot outlive any corroboration evidence, so it mints
no uncorroborated silence. (If the dedup window were ever lengthened past the
tightest evidence bound, this reasoning would need revisiting — the invariant
is `dedup_window < min(max_age_s)`.)
