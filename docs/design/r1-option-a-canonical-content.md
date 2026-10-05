# R-1 / Q3 — Option (a): `PolicyVersion.content` becomes the canonical thresholds.json

> **Q3 STATUS — DECIDED 2026-10-05 ~20:25 IST (T+0).**
> Aditya selected option (a): canonical `PolicyVersion.content`, kernel
> refuses unattested generations. This document is now the **build
> design** for the R-1 Phase-1 build lane. Implementation is NOT this
> wave's scope — this lane is design/docs only; the build lane owns
> `src/` under the 12H plan's Phase 1 gates. The rejected weaker
> alternative is retained on record in
> `r1-option-b-two-attestor-pr.md`.

**Lane:** gate-1 (R-1/Q3 design) · **Branch:** `lane/prep-r1-policy`
**Date:** 2026-10-05 IST · **Type:** 1 — this unifies the trust root with the
decision path; reversing it later invalidates every historical policy
generation. (principal-systems: Type 1 demands RFC-grade rigor.)
**Single-threaded owner:** this lane (author) until the T+9 Oracle+Forge
review assigns the build owner.

## 1. The problem, in one paragraph

`deterministic-gate.md` §2.1 (P1): there are two policy-change paths.
Path A (B3 lifecycle + Ed25519 dual attestation) governs an object the
kernel never reads — `PolicyStore` is never instantiated outside tests.
Path B (`thresholds.json` → `ConfigLoader` → the kernel's `Thresholds`) is
the one that decides who gets paged at 3 AM, and it changes via an
unattested JSON edit with no attestation, no state machine, no expiry, no
two-person rule. The content-hash binding that Tripwire identified as the
replay-gap closure binds an object the decision path never consults. The
governance is theater over an empty stage.

## 2. The design

### 2.1 Single source of truth

`PolicyVersion.content` — the bytes the attestation's `content_hash` binds —
becomes **the** canonical thresholds. There is exactly one thresholds
object in the system: the content of the current LIVE (or REVIEW_DUE)
`PolicyVersion`, serialized canonically (sorted-key JSON, UTF-8, pinned
serializer version recorded in the content envelope). The operator-editable
`thresholds.json` on disk stops being an input.

This is principal-governance §2 **SSOT**: every entity is owned by exactly
one service. Thresholds are owned by the attested lifecycle; `ConfigLoader`
subscribes, it does not read a second source. It is also the proxy-trap
audit (principal-mindset §3): today the B3 lifecycle is a proxy (a
governance process) that does not move the true objective (what the kernel
evaluates). Option (a) deletes the proxy gap rather than tuning it.

### 2.2 The kernel refuses unattested generations

`ConfigLoader`'s load path changes its *source*, not its *contract*:

1. On load/startup/reload, resolve the current LIVE (fallback REVIEW_DUE)
   `PolicyVersion` from the policy store.
2. Verify: state ∈ {live, review_due}; the version carries ≥2 valid Ed25519
   attestations binding `(policy_id, version, content_hash, from_state,
   to_state, decided_at)` per the attestor ADR's verification rules
   (active status, content-hash equality, 7-day freshness window, attestors
   distinct and distinct from the author); `sha256(canonical_bytes(content))`
   equals the attested `content_hash`.
3. Materialize the verified content as the next **generation** in the
   existing generations directory (`ConfigLoader`'s `_gen_dir`, last-good
   chain, `_acked_mtimes` semantics — all kept).
4. On ANY verification failure: `ConfigRejected` — identical to today's
   invalid-config behavior. Fail-closed, fail-toward-paging:
   startup ⇒ fall back to newest last-good generation or refuse to start;
   reload ⇒ live generation untouched (existing semantics, already
   fail-safe).

The existing last-good chain is the mechanism that makes the flip safe:
refusing an unattested generation is *the same code path* as refusing a
malformed one — `ConfigRejected` already knows how to fall toward the
human. Nothing new is invented on the failure path; the failure path is the
property that matters most (deterministic-gate.md §1.5).

### 2.3 What lives inside `content`

`PolicyVersion.content` = `{thresholds, allowlist_generation, serializer_version}`.
Thresholds (`suppress_p1_max`, `suppress_conf_min`, `page_p1p2_min`,
`uncertain_conf_max`, `queue_conf_min` — the `models.py:52-58` set) and the
allowlist generation (the ADR-017 legacy-window resolution included, never
merged into the kernel's set — the P1 proposal names both). Bounded size
by construction; if content ever exceeds a sane bound (say 1 MiB), the
loader rejects — a threshold file that big is a different object
wearing a costume.

### 2.4 D8 interplay (handoff to gate-2)

The D8 `PolicyGate.can_suppress` fresh-read of the policy-state JSON stays
as the *runtime* per-decision guard (never a boot cache — ADR Type-1).
The missing-file semantics flip (`(False, "policy_gate_not_configured")`,
fail-closed) is gate-2's scope, designed alongside this — the two flips
must land together or a store-initialization gap re-opens the fail-open
window. **Do-not-do for this lane:** this doc does not redesign D8; it
names the dependency.

### 2.5 Migration path from today's thresholds.json

1. **Freeze:** take the current on-disk `thresholds.json` (+ current
   allowlist generation), canonicalize, and create it as `PolicyVersion`
   vN content via the operator CLI ceremony (`r1-cli-ceremony-notes.md`),
   driven draft → shadow → canary → live with the full 2-attestor
   ceremony. This is the *only* time an unattested file becomes attested
   content — recorded as a founder-seed-style event in the ceremony log,
   never repeated silently (Pager's bootstrap rule from the attestor ADR).
2. **Dual-read shadow window:** the loader resolves from the store AND
   diffs against the on-disk `thresholds.json`; a diff emits
   `config_rejected`-class events but does not block (honest telemetry
   before enforcement — execution-doctrine: shadow before canary).
3. **Flip:** the loader stops consulting `thresholds.json` entirely. The
   on-disk file is renamed `thresholds.json.retired` (kept for audit,
   never read). Any process still writing it fails loudly — no silent
   dead config.
4. **Expiry/freeze ride free:** B3's review-due/expired clocks and the
   watchdog freeze now govern the kernel's numbers *because they govern
   the content the kernel reads*. An expired version ⇒ `ConfigRejected` ⇒
   last-good or refuse-to-start. The temporary-widening incident from the
   P1 pre-mortem becomes impossible to run silently.

### 2.6 What breaks

- **Every operator workflow that edits `thresholds.json` directly** — the
  current de-facto change path. This is the point, but it must be said
  plainly: the 3 AM "widen the gate" muscle memory stops working. The
  sanctioned emergency path is the B3 emergency-override with auto-revert
  (designed in the ceremony notes) — a break-glass that is loud,
  time-boxed, and self-healing, not a silent edit.
- **`tuner.py`'s output contract:** the tuner prints a tradeoff table but
  never decides (deterministic-gate.md §1.2); under (a) its *input* also
  changes — it proposes candidate content for a draft version, never a
  file to hot-swap. The tuner's write path to `thresholds.json` is
  removed.
- **SIGHUP / `POST /-/reload` semantics:** reload now means "re-resolve
  the LIVE version from the store and re-verify," not "re-read the file."
  An operator who edits the retired file and SIGHUPs gets
  `config_rejected` + the last-good generation — which is the honest
  behavior, and the retired file's name says why.
- **The D8 missing-file flip must land atomically with this** (see §2.4);
  shipping (a) with D8 still fail-open re-opens the "watchdog never set
  up" state as the least-supervised state — the exact inversion of the
  intended direction.

## 3. Pre-mortem (principal-systems §2)

*It is one year later and option (a) has failed catastrophically:*

1. **The 3 AM quorum failure.** An incident needed a threshold change;
   two attestors were unreachable; the emergency-override path had never
   been drilled; pages stormed for 40 minutes. *Mitigation:* the
   emergency-override with auto-revert is a first-class ceremony (not a
   footnote), drilled quarterly like the ADR-018 drill, with a single
   named owner for the drill calendar.
2. **Canonicalization drift.** A serializer change altered canonical
   bytes for identical thresholds; `content_hash` mismatched on every
   load; every reload fell to last-good and paged. *Mitigation:* the
   serializer is pinned, versioned in the content envelope, and covered
   by a checked-in golden-fixture test (canonical bytes of a fixed
   content are byte-compared in CI).
3. **Attestation freshness vs. long incidents.** The 7-day attestation
   window expired mid-incident; the LIVE version became unloadable;
   the loader fell to last-good from a week ago — stale thresholds
   during the incident they were tuned for. *Mitigation:* B3's
   review-due clock is the early warning (it exists precisely for this);
   the loader's fallback emits the `config_rejected` event with the
   version's age, and the D8 fresh-read surfaces it per decision.

## 4. Alternatives considered and rejected

1. **Option (b) — 2-attestor PR rule** (`r1-option-b-two-attestor-pr.md`).
   Honestly weaker: GitHub becomes the trust root (admin bypass, token
   compromise), enforcement is reviewer-side (human) not cryptographic,
   the attestation binds the merge commit rather than the content hash.
   It is the deputy-authority floor if Q3 goes (b), not the target.
2. **A separate "threshold attestation" scheme outside PolicyVersion.**
   Two trust roots for one decision path — the attestor ADR already
   settled identity once; a second scheme re-opens every settled question
   (onboarding, revocation, freshness) and the two will drift.
   Rejected.
3. **Keep `thresholds.json` as the file but sign it with the attestor
   keys (file-level signature).** This binds *a* file but not the
   lifecycle: no B3 states, no expiry, no freeze, no review-due clock —
   it keeps the theater's stage and paints it. Rejected.
4. **Do nothing — document the dual path honestly and rely on operator
   discipline.** The P1 pre-mortem (six silent weeks of a widened gate,
   a suppressed SEV1) is precisely what discipline-alone produces under
   eternal friction (principal-systems law 2: the next operator will be
   tired and will not read the docs). Rejected.

## 5. Author-written risk paragraph (§3.5)

The strongest reason this could go wrong is that option (a) converts a
*soft* failure (an unattested edit the system tolerates) into a *hard*
failure (a refused generation the system falls back from) — and hard
failures during incidents are the ones that page humans at scale. If the
attestation ceremony is slower than the incident, if the quorum is
unreachable, if the serializer drifts, or if the emergency-override was
never drilled, the system will fail toward paging *loudly and
repeatedly* — which is the designed direction, but "designed direction"
is cold comfort to an on-call engineer watching a page storm caused by
governance, not by the incident. The mitigation is not in the crypto —
it is in the drill calendar, the auto-revert, and the honest admission
that (a) trades the risk of silent suppression for the risk of loud
paging. That trade is correct for a paging middleware (a missed page
kills; an extra page annoys), but it must be made with eyes open, and
the emergency path must be rehearsed before the first real 3 AM.

## 6. Verification (what "done" means — build phase, post-Q3)

- A test mutating `thresholds.json` without attestation ⇒ suppress
  unreachable (gate pages); a test with an attested LIVE version
  covering the thresholds ⇒ suppress reachable
  (deterministic-gate.md P1 "verifies by").
- The operator runbook executed end-to-end once (draft → live via the
  CLI) before any design-partner traffic.
- `PolicyStore` instantiated outside tests; the dual-read shadow window
  shows zero diffs for one full B3 review cycle before the flip.

## 7. Skill & Evidence (§2.2)

- **Requirement:** R-1 (`deterministic-gate.md` P1) / PIPELINE-REVISION
  §9 Q3 option (a).
- **Skill clauses that bind:**
  - *principal-governance §2, SSOT:* thresholds owned by exactly one
    entity (the attested `PolicyVersion`); the loader subscribes, never
    reads a second source.
  - *principal-governance §2, unforgiving API design:* the content-hash
    binding is a contract — a generation whose hash is not covered by a
    LIVE attestation is refused, not warned about.
  - *principal-mindset §3, proxy-trap audit:* the B3 lifecycle is
    currently a proxy that does not move the true objective (kernel
    inputs); option (a) closes the proxy gap instead of tuning the proxy.
  - *principal-systems, eternal friction + software constitution
    ("reliability is policy, not hope"):* the failure path is the
    existing `ConfigRejected` → last-good → fail-toward-paging chain;
    nothing new is invented where reliability is decided.
  - *principal-systems §2, pre-mortem:* §3 above, written before any
    build.
- **Tools:** read `src/sentinel/config.py` (`ConfigLoader`: generations
  dir, last-good chain, `_acked_mtimes`, `ConfigRejected` semantics);
  `platform/server/app.py:183-201` (write surface); `docs/adr-attestor-
  identity.md` (attestation verification rules, freshness window).
- **Web sources (accessed 2026-10-05):**
  - Google Cloud Binary Authorization policy reference — the
    `REQUIRE_ATTESTATION` admission rule with
    `ENFORCED_BLOCK_AND_AUDIT_LOG`: "in the case that all required
    attestors have not authorized the image, Binary Authorization
    blocks the deployment and writes to the audit log."
    This is the industry shape of option (a): the admission controller
    (our kernel/loader) refuses the artifact (our generation) unless a
    named attestor authorized *that digest* (our content hash).
    https://docs.cloud.google.com/binary-authorization/docs/policy-yaml-reference
  - Sigstore/cosign keyless signing ceremony — `cosign sign` triggers
    an OIDC flow, generates an ephemeral keypair, obtains a short-lived
    Fulcio certificate binding key to identity, signs the digest, and
    destroys the private key immediately after; the signing event is
    recorded in Rekor. The ceremony UX pattern (identity-bound,
    short-lived, transparency-logged) informs `r1-cli-ceremony-notes.md`.
    https://github.com/theheavenlyd3mon/hermes-profiles/blob/HEAD/profiles/cyber-blue-cloud/skills/Anthropic-Cybersecurity-Skills/skills/implementing-sigstore-for-software-signing/SKILL.md
- **Reviewers:** Oracle + Forge (12H-PLAN gate-1 routing).
