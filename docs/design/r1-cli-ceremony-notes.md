# R-1 — Operator CLI ceremony: prototype notes

> **Status: design notes, NOT an implementation.** These notes describe
> the attestation ceremony UX — what the operator types, sees, and is
> protected from — so the build lane can implement the CLI against a
> frozen UX contract. Q3 was decided 2026-10-05 ~20:25 IST (T+0) for
> option (a) (canonical `PolicyVersion.content`); this ceremony drives
> the lifecycle that option (a) wires to the kernel. No `src/` changes
> in this wave.

**Lane:** gate-1 (R-1/Q3 design) · **Branch:** `lane/prep-r1-policy`
**Date:** 2026-10-05 IST · **Type:** 2 — the ceremony transport is
explicitly a Type-2 choice in the attestor ADR ("the signatures are the
trust, not the tool"); the UX below can be revised without invalidating
attestations.

## 1. Design principle: the ceremony is the control

`deterministic-gate.md` P1 names the missing piece bluntly: "ship the
missing operator entry point: a CLI driving `create_draft → … → live`
(execution doctrine: manual first — a 10-line ceremony beats the unbuilt
one)." The B3 lifecycle + Ed25519 attestation exist in
`policy_lifecycle.py`/`attestor.py` but have no non-test callers — the
governance is unwired because there is no handle for a human to hold.
These notes design the handle.

The governing UX rule, from the attestor ADR's own threat model: **the
ceremony that matters is the one that happens at 3 AM during a
compromise** (Pager). Every flow below is designed for a tired operator
under eternal friction (principal-systems law 2): unambiguous commands,
mandatory preview, no silent defaults, and the dangerous actions loud.

## 2. Command surface (UX contract, not implementation)

```
sentinel policy draft --from-live        # new draft seeded from current LIVE content
sentinel policy preview <version>        # render canonical content + semantic diff vs LIVE
sentinel policy sign <version>           # attestor signs (private key via env/file, 600 perms)
sentinel policy transition <version> --to <state>   # 2nd attestor co-signs; verifies both
sentinel policy status                   # state machine position, attestations, clocks
sentinel policy revoke <attestor-id> --reason "..."  # single-operator, immediate, one-way
sentinel policy freeze / unfreeze       # watchdog freeze; unfreeze needs 2 attestors
sentinel policy emergency --ttl 30m --reason "..."   # break-glass override, auto-revert
```

Notes on each:

- **`draft --from-live`.** Drafts are never born empty — they start from
  the current LIVE content, so the diff is always meaningful. An empty
  draft is a footgun (the operator retypes thresholds from memory at
  3 AM); the tool refuses to create one without `--empty --i-know`.
- **`preview`.** Mandatory before `sign`. Shows: the semantic diff
  (threshold-by-threshold, old → new, with the expected-cost constants
  flagged `[ASSUMED]` where touched), the canonical bytes' sha256 (the
  exact hash the signature will bind), and the B3 clock impact
  (review-due date, expiry). The operator signs *the hash they just
  read* — this is the content-hash binding made visible, the closure of
  Tripwire's replay gap at the human layer.
- **`sign`.** The private key never appears on the command line and is
  never echoed. Key sources, in order: `SENTINEL_ATTESTOR_KEY_FILE`
  (600-perm file), hardware ref (`algorithm: hsm-ref` — the ADR's
  future path). After signing, the ceremony log entry is shown
  (hash-chained, per the ADR) — the operator sees their signature
  *recorded*, not just computed. Modeled on the cosign ceremony (see
  §7): identity-bound, short-lived, transparency-logged — our Rekor is
  the chained ceremony log.
- **`transition --to`.** The second attestor's command. It re-verifies
  the *first* signature before accepting the second (fail-toward-paging
  chain: a bad first signature aborts here, loudly, not at load time).
  The `--to` target is explicit; `live` requires the full sign-off
  matrix (2 distinct attestors + preview hash, attestors distinct from
  the author — the ADR's checks, surfaced as CLI errors with reasons,
  not tracebacks).
- **`status`.** One screen: current LIVE version + content hash, every
  version's B3 state, attestation counts per version, clock deadlines
  (review-due in N days, expiry), registry health (seal OK, N active
  attestors). The operator should be able to answer "what is governing
  the kernel right now?" in one command — today that question has no
  answer.
- **`revoke`.** Single operator, one command, immediate effect, one-way
  latch (the ADR: revocation is fail-safe, it must be fast). The CLI
  prints the quarantine sweep result: which LIVE/REVIEW_DUE versions the
  revoked attestor touched, now frozen, dispositions falling through to
  page. Revocation is the one action that does NOT ask for confirmation
  — confirmation dialogs on the fail-safe path are how compromises get
  slow.
- **`emergency`.** The sanctioned 3 AM path (option (a)'s pre-mortem
  #1). A break-glass transition with a mandatory `--ttl` (default 30m,
  max 4h) and `--reason`; auto-reverts to the prior LIVE version at
  expiry — no human needed to clean up, because the cleanup is what gets
  forgotten. Emits the violet-banner event class (same as C1 step
  entries: impossible to miss). Single-operator allowed (it is the
  incident path), but the ceremony log records it as `emergency` and the
  next `status` shows it in red until a proper attested version replaces
  it. **This command is the monkey-first item** (principal-mindset §6):
  the hardest part of the whole R-1 design is not the crypto, it is the
  legitimate emergency — design it first, drill it quarterly.

## 3. Key handling UX (the part operators will get wrong)

- Keys live in files with 600 permissions or env vars, never in shell
  history, never in the ceremony log, never in `ps` output. The CLI
  refuses a key file with group/world-readable perms (fail-closed on
  the ceremony itself).
- Onboarding a new attestor is a 2-person ceremony at the CLI: two
  active attestors each run `sentinel attestor onboard --id <new> --pubkey
  <hex>`; the second command completes the onboarding and both see the
  chained log entry. This mirrors the ADR's onboarding rule — the CLI
  just makes it typable.
- Rotation: `sentinel attestor rotate --id <me>` generates the new
  keypair, prints the new pubkey for the co-signers, and marks the old
  key `rotated` (not revoked — rotation is planned, revocation is the
  incident; the ceremony log distinguishes them).
- Lost laptop: `revoke` (above). The one-way latch means the operator
  is never asked "are you sure you want to make this permanent" — the
  permanence is the safety property.

## 4. What the ceremony does NOT do

- No web UI. The attestor ADR's Type-2 table already decided: "ceremony
  transport (CLI now, IdP-backed later) — the signatures are the trust,
  not the tool." A web UI for the trust root is a larger attack surface
  for zero ceremony benefit (execution-doctrine §2: manual first).
- No key escrow, no "export the registry private keys" command. There
  are no registry private keys — attestors hold their own. Escrow would
  re-centralize the trust the ADR decentralized.
- No batch/auto-sign mode. A `--yes` flag on `sign` is refused by
  design: unattended signing is how the two-person rule becomes a
  one-person rule with extra steps.

## 5. Pre-mortem for the ceremony (principal-systems §2)

*It is one year later and the ceremony failed:*

1. **The `--i-know` bypass.** Operators routinely used
   `--empty --i-know` to skip the from-live seed, retyping thresholds
   from memory; a typo widened the suppress gate and the diff-vs-live
   showed it, but the signer didn't read the preview. *Mitigation:*
   `preview` output is piped through a mandatory pager hold (the signer
   must scroll to the hash line); the ceremony log records
   `preview_skipped` as a distinct event class that the monthly audit
   flags.
2. **Emergency became routine.** The `emergency` command was used 11
   times in a quarter because the normal ceremony "took too long."
   *Mitigation:* every emergency use opens a tracked follow-up (the
   attested replacement version); the metric "emergencies per quarter"
   is reviewed by the security principal; more than 2/quarter triggers
   a process review, not a bigger TTL.
3. **Key file on the shared jump host.** An attestor left their key
   file on a shared host; another operator "borrowed" it for a quick
   sign. *Mitigation:* none technical — this is why the ceremony log
   chains signers and why revocation is one command. The honest note:
   the two-person rule degrades to the discipline of the two persons.
   The drill calendar and the named attestor set are the control.

## 6. Alternatives considered and rejected

1. **Web UI for the ceremony.** Larger attack surface, session/auth
   complexity, and nobody asked for it. The ADR already decided CLI
   now. Rejected.
2. **Signing via the platform console (browser).** Private keys in a
   browser session is the worst key-handling UX available; it also
   couples the trust root to the platform tier's auth (R-16) — the
   ceremony must work when the platform is down. Rejected.
3. **Fully automatic promotion (draft → live on CI green).** Removes
   the human from the two-person rule entirely; CI compromise becomes
   policy compromise. The mechanical checks (hash match, schema) belong
   in CI; the attestation belongs to humans. Rejected.
4. **Hardware keys required from day one.** Correct long-term
   (the ADR's `hsm-ref` path), but a day-one HSM requirement would mean
   the ceremony never gets used — eternal friction says the first
   version must work with a file and 600 perms. Rejected for now,
   migration path kept.

## 7. Author-written risk paragraph (§3.5)

The strongest reason the ceremony could go wrong is that ceremonies rot
into rituals: the preview gets scrolled past, the second attestor signs
what the first signed without reading, the emergency command becomes
the normal command, and within a year the CLI is a more expensive way
to do exactly what the unattested JSON edit did — except now everyone
*believes* it is governed, which is worse than knowing it isn't. The
defense is not in the CLI's code but in the audit practices around it:
the monthly ceremony-log review, the emergency-use metric, the
`preview_skipped` event class, the attestation kill-rate. A ceremony
that never says no is theater with better lighting. The build lane must
treat the audit hooks (the event classes, the metrics, the log
chain) as first-class deliverables, not as logging afterthoughts —
because when the ceremony rots, the hooks are the only thing that will
tell us.

## 8. Skill & Evidence (§2.2)

- **Requirement:** R-1 (`deterministic-gate.md` P1: "ship the missing
  operator entry point: a CLI driving `create_draft → … → live`").
- **Skill clauses that bind:**
  - *execution-doctrine §2, manual first:* the 10-line ceremony beats
    the unbuilt identity microservice; P1 names this explicitly.
  - *principal-systems, product/design constitution ("empathy for the
    operator"):* every flow designed for the tired 3 AM operator —
    mandatory preview, loud emergency path, no silent defaults.
  - *principal-mindset §6, monkey-first:* the emergency/break-glass
    path is the hardest part; it is designed first (§2, `emergency`)
    with a dated drill cadence, not as a footnote.
  - *principal-systems, eternal friction:* keys get lost, laptops die,
    operators borrow key files — revocation is one command, the latch
    is one-way, the log distinguishes rotation from revocation.
  - *principal-governance §1, written decisions before code:* this doc
    is the UX contract the build lane implements against; the CLI's
    surface is frozen here before code.
- **Tools:** read `src/sentinel/policy_lifecycle.py` (B3 state machine,
  `create_draft`/`transition`/`unfreeze` — no non-test callers),
  `src/sentinel/attestor.py` (registry, ceremony log, revoke,
  `quarantine_revoked`), `docs/adr-attestor-identity.md` (§2 decision,
  §3 Type labels — ceremony transport is Type 2).
- **Web sources (accessed 2026-10-05):**
  - Sigstore/cosign keyless signing ceremony: `cosign sign` triggers
    an OIDC flow, generates an ephemeral keypair, obtains a
    short-lived Fulcio certificate binding the key to the identity,
    signs the digest, destroys the private key immediately, and records
    the event in Rekor. The UX pattern — identity-bound, short-lived,
    transparency-logged, with a non-interactive CI variant via
    `SIGSTORE_ID_TOKEN` — is the model for the sign/transition UX
    above (our Rekor = the hash-chained ceremony log).
    https://github.com/theheavenlyd3mon/hermes-profiles/blob/HEAD/profiles/cyber-blue-cloud/skills/Anthropic-Cybersecurity-Skills/skills/implementing-sigstore-for-software-signing/SKILL.md
  - Google Cloud Binary Authorization CLI flow: attestations are
    created in CI *after* tests/vulnerability scans pass
    (`gcloud beta container binauthz attestations sign-and-create`),
    binding the attestation to the artifact digest; the admission
    policy then requires those attestations. The sequencing lesson —
    attest only after the checks pass, bind to the digest — maps to
    our `preview` → `sign` → `transition` order.
    https://docs.cloud.google.com/binary-authorization/docs/getting-started-cli
- **Reviewers:** Oracle + Forge (12H-PLAN gate-1 routing); Vault ack on
  the key-handling UX (§3).
