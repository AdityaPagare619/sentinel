# R-1 / Q3 — Option (b): 2-attestor PR rule with B3 expiry/freeze applied to thresholds.json

> **⛔ GATED ON Q3 — Aditya's decision. UPDATE 2026-10-05 ~20:25 IST: Q3
> DECIDED at T+0 — Aditya selected option (a), canonical
> `PolicyVersion.content` (kernel refuses unattested). This document is
> RETAINED as the recorded weaker alternative: the deputy-authority floor
> design, kept so the decision's rejected path stays on record with its
> guarantees stated honestly. Design only; never built, per the Q3
> verdict.**

**Lane:** gate-1 (R-1/Q3 design) · **Branch:** `lane/prep-r1-policy`
**Date:** 2026-10-05 IST · **Type:** 1 — it governs the same trust surface
as option (a), with weaker enforcement; a "temporary" floor has a way of
becoming permanent, which is why the guarantees are stated bluntly below.
**Single-threaded owner:** this lane (author) until the T+9 Oracle+Forge
review assigns the build owner (if ever — see verdict above).

## 1. The problem, in one paragraph

Same finding as option (a) (`deterministic-gate.md` §2.1): the attested
B3 lifecycle governs an object the kernel never reads, while
`thresholds.json` — the file that actually decides suppression — changes
via unattested edit. Option (b) closes the theater *without* rewiring the
loader: it keeps `thresholds.json` as the kernel's input and wraps the
*change process* in the two-person rule, enforced reviewer-side on the PR
that changes the file, with the B3 lifecycle's expiry/freeze clocks
applied to each thresholds generation as metadata the loader honors.

## 2. The design

### 2.1 The 2-attestor PR rule (reviewer-side enforcement)

`thresholds.json` changes land only through a pull request that satisfies
all of the following, enforced by branch protection + CODEOWNERS
(external practice: GitHub's "Require review from Code Owners" —
see §7):

1. **Two required approvals, from two distinct attestor identities.**
   The repo's CODEOWNERS names the attestor set for
   `**/thresholds.json` (and the allowlist generation file); branch
   protection requires 2 approving reviews from that set. The author
   cannot self-approve (GitHub blocks author approval natively); the two
   approvers must be distinct from the author — mirroring the attestor
   ADR's "attestors distinct from the author" check, but enforced by
   humans reading a diff.
2. **Dismiss stale approvals on new commits.** Any push after approval
   invalidates the approvals — this is the draft-swap defense: without
   it, an attestor can approve benign content and the author can swap in
   malicious content before merge.
3. **No bypass actors.** "Do not allow bypassing the above settings" —
   admins included. (The cautionary exhibit: stephen-c-noh/mat-inspect's
   `CHANGE_MANAGEMENT.md`, checked 2026-08-04 against the live ruleset,
   records the *intent* of 2 approvals while `require_code_owner_review`
   is off — intent without enforcement is exactly the gap this rule must
   not reproduce. We cite it as the failure to avoid, not the model.)
4. **The PR body is the attestation surface.** A required template
   section records: content hash of the proposed thresholds (sha256 of
   the canonical bytes, computed by CI and posted as a check), the two
   attestor identities, and the B3 generation metadata (§2.2). The merge
   commit is the attestation event; the ceremony log mirrors it
   (hash-chained, per the attestor ADR).

### 2.2 B3 expiry/freeze applied to thresholds.json

Each merged thresholds change becomes a **generation** with B3 metadata,
checked into the repo alongside the file (or in a `thresholds.meta.json`
sidecar):

- `generation`: monotonic integer; `content_hash`: sha256 of canonical
  thresholds bytes; `merged_at`; `attestors: [id, id]`;
  `review_due_at` (= merged_at + B3 review interval);
  `expires_at` (= review_due + grace); `state`: one of the B3 six.
- `ConfigLoader` reads the sidecar on load/reload (it already reads
  `thresholds.json` — this adds one sibling read, no new trust root):
  expired generation ⇒ `ConfigRejected` (same fail-closed path as today);
  frozen generation (watchdog freeze) ⇒ `ConfigRejected`.
- Clock transitions (`tick()`) run in the existing lifecycle module
  against the sidecar — the B3 state machine is *reused*, not
  reimplemented (DR-26's anti-drift lesson: one implementation).

This is the honest core of (b): the expiry/freeze clocks — the machinery
that would have caught the six-silent-weeks incident — apply even though
the signature scheme is human rather than cryptographic.

### 2.3 What (b) deliberately does NOT do

- It does not bind the kernel's numbers to a content hash *cryptographically*.
  The binding is: CI posts the hash; humans compare; the sidecar records.
  A compromised CI or a rubber-stamping attestor breaks the chain
  silently — the failure mode is *quiet*, unlike (a)'s loud refusal.
- It does not change the D8 missing-file fail-open semantics by itself;
  the gate-2 fail-closed flip is still required (same dependency as (a)).
- It does not survive GitHub being unavailable or untrustworthy: GitHub
  is the trust root. Admin token compromise, a malicious org owner, or a
  force-push with bypass re-opens Path B fully.

## 3. Guarantees, stated honestly (the weaker-guarantees section)

| Property | Option (a) | Option (b) — this doc |
|---|---|---|
| Attestation binds content hash | Cryptographic (Ed25519 over canonical bytes) | Procedural (CI posts hash; humans eyeball) |
| Trust root | Attestor keypairs in HMAC-sealed registry | GitHub org: branch protection + CODEOWNERS + admin discipline |
| Draft-swap between approval and merge | Impossible (signature covers content) | Prevented only if stale-approval dismissal is on and stays on |
| Revocation propagation | Next decision (fresh registry read) | Next PR review cycle (human-speed) |
| Enforcement when GitHub is down | N/A (local verification) | None — no merges, but also no verification of past merges |
| Cost | Loader rewire + ceremony CLI + migration | Branch protection config + sidecar + loader sidecar-read |
| Failure mode | Loud (refused generation, pages) | Quiet (rubber-stamp, bypass, stale config) |

The blunt summary: **(b) converts "anyone can edit" into "two specific
humans must approve via a UI whose rules can be toggled by an admin."**
That is a real improvement over today — it kills the silent 3 AM edit —
but it is not the same claim as (a), and this document must never be
cited as if it were.

## 4. Pre-mortem (principal-systems §2)

*It is one year later and option (b) has failed:*

1. **The bypass nobody noticed.** An org admin, debugging a CI outage,
   toggled "allow bypassing" on the ruleset and never toggled it back.
   Three thresholds changes merged with zero attestors. *Mitigation:*
   a scheduled audit (the anti-theater audit, principal-mindset §11)
   diffs the live ruleset against the recorded intent monthly; any drift
   pages the security principal. But note the recursion: the audit is
   itself a human process.
2. **Rubber-stamp attestation.** The two attestors approved 40 PRs in a
   quarter; approval latency dropped to minutes; nobody read a diff after
   the tenth. *Mitigation:* the PR template requires the attestor to
   state *what they checked* (hash match, semantic review of the delta);
   the kill-rate metric from P5 applies — an attestation process that
   never says no is theater.
3. **Sidecar drift.** Someone edited `thresholds.json` without updating
   `thresholds.meta.json`; the loader read a stale generation; the
   mismatch was "acknowledged" and forgotten. *Mitigation:* CI fails the
   PR if the sidecar's `content_hash` doesn't match the file — the check
   is mechanical, not human.

## 5. Alternatives considered and rejected

1. **Option (a)** — selected by Aditya at T+0. Stronger; documented in
   `r1-option-a-canonical-content.md`. This doc exists as its recorded
   alternative.
2. **Single-approver PR rule (today's de-facto state, formalized).**
   One human is not a two-person rule; the attestor ADR's entire premise
   is that one is insufficient. Rejected.
3. **Auto-merge bot with policy checks ("the bot is the second
   attestor").** A bot is not a person; it cannot exercise judgment on
   the semantic delta (is `suppress_p1_max: 0.9` sane?). It also becomes
   a single point of compromise. Rejected — automation checks the
   mechanical (hash match), humans attest the semantic.

## 6. Author-written risk paragraph (§3.5)

The strongest reason option (b) could go wrong is the one its own design
admits: it is a *human* control wearing the *language* of a cryptographic
one. "Two attestors approved" will be cited in postmortems and audits as
if it meant what an Ed25519 signature means, and under pressure — the
incident, the outage, the tired reviewer — the human control degrades
exactly when it is needed most, while the cryptographic control does not
care whether you are tired. If (b) is ever built (it was not selected),
its survival depends on the monthly ruleset audit and the attestation
kill-rate being real practices with named owners, not wiki entries. A
floor that is never inspected becomes the ceiling — and a ceiling made
of branch-protection checkboxes is lower than it looks.

## 7. Skill & Evidence (§2.2)

- **Requirement:** R-1 (`deterministic-gate.md` P1) / PIPELINE-REVISION
  §9 Q3 option (b) — the deputy-authority floor.
- **Skill clauses that bind:**
  - *principal-governance §2, contract as coordination:* CODEOWNERS +
    branch protection as the machine-checked contract for who may change
    the policy file; the PR template as the attestation surface.
  - *principal-systems, Type 1 vs Type 2:* Q3 is Type 1; option (b) is
    explicitly the cheaper, weaker floor — the doc labels the Type and
    refuses to let the weaker option borrow the stronger one's
    authority.
  - *principal-mindset §11, anti-theater audit:* the mitigations in §4
    (monthly ruleset drift audit, attestation kill-rate) are the
    anti-decay instrumentation; a review bar that never says no is
    decoration.
  - *principal-governance §1, written decisions before code:* the PR
    body carries the attestation record — writing is the thinking, and
    the record is the audit trail.
- **Tools:** `src/sentinel/config.py` (`ConfigLoader` sidecar-read
  extension point); `platform/server/app.py:183-201` (write surface —
  unchanged by this option); `docs/adr-attestor-identity.md` (the
  semantic requirements the PR rule mirrors: distinct attestors,
  distinct from author, freshness).
- **Web sources (accessed 2026-10-05):**
  - GitHub CODEOWNERS + branch protection best practices (2026):
    CODEOWNERS "suggests reviewers but does not enforce anything" —
    enforcement requires branch protection with "Require review from
    Code Owners," "Dismiss stale pull request approvals when new commits
    are pushed," and "Restrict who can push"; "Do not allow bypassing
    the above settings" for real protection including admins.
    https://devtoolhub.com/github-codeowners-permissions-best-practices/
  - mat-inspect `CHANGE_MANAGEMENT.md` (checked against the live
    ruleset 2026-08-04): the team's *intent* is 2 approvals on critical
    paths, but `require_code_owner_review` is off, so "a single
    approval, from anyone, can currently merge" — the intent-vs-
    enforcement gap this design must not reproduce.
    https://github.com/stephen-c-noh/mat-inspect/blob/HEAD/docs/CHANGE_MANAGEMENT.md
- **Reviewers:** Oracle + Forge (12H-PLAN gate-1 routing).
