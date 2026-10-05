# DRAFT — Pull Request template (qa-1)

> **⚠️ DRAFT — lands post-T+0 only.** Per 12H-PLAN §1 qa-1: T+3 draft ·
> T+6 reviewed · T+9 merged (template live) · T+12 in use by all lane PRs.
> When it lands, it **replaces** `.github/pull_request_template.md`.
> This draft must not be installed early — the merge requirement below is
> meaningless until the interim mechanical gate (§4.2#3 box-side runner)
> exists to satisfy it.

**Purpose (R-11 honesty half):** "full suite green, artifact attached" becomes
a **written merge requirement** — not a checkbox the author ticks from memory.
**Binding skills:** principal-governance §2 (contract is the coordination
mechanism) + RFC culture; principal-systems Op-rule 5 (blameless postmortems)
and the verify-artifact law — the BYOK lane reported "pushed, NOT merged"
with commit hashes and the push never landed (AGENTS.md), so *completion
reports are not evidence*; execution-doctrine §7 (feedback-loop velocity —
a green template that takes three weeks of reviews is a dead loop; both the
template and the runner it names are measured).
**External practice consulted** (OPERATING-RULES §2.1): Kubernetes
`PULL_REQUEST_TEMPLATE.md` convention — required vs optional fields, "all
tests have passed in CI" as an explicit check, linked issue/REQ closure
([ovn-kubernetes template](https://github.com/ovn-kubernetes/ovn-kubernetes/blob/HEAD/.github/PULL_REQUEST_TEMPLATE.md),
[neo4j-kubernetes-operator template](https://github.com/priyolahiri/neo4j-kubernetes-operator/blob/HEAD/.github/pull_request_template.md),
accessed 2026-10-05). What changed in this design from that reading: k8s
templates trust CI as the arbiter ("If not leave a comment as to why the CI
is red"); **we cannot do that** — see the CI-honesty block below — so the
template names the dead CI honestly and requires the artifact instead.

---

<!-- ============ TEMPLATE BODY (install at .github/pull_request_template.md) ============ -->

# Pull Request

## ⚠️ CI honesty block (read first)

GitHub Actions has been **dead with `startup_failure` on every run since
2026-10-02** (PIPELINE-REVISION §5.4). **Every PR merged since Oct 2 landed
with no mechanical verification.** Until the `startup_failure` escalation
is resolved (Aditya's call, PIPELINE-REVISION §9 — it touches the standing
GHA ban):

- The interim gate is the **§4.2#3 box-side post-merge runner** (cron on the
  2-CPU box, order-compliant). "CI green" means: the runner produced a green
  artifact for this change.
- **The suite artifact must be attached to this PR** — a green artifact is
  the written merge requirement, not the author's word that tests passed.

## What / Why

_1–3 lines: what changed, why it needed to change._

## Requirement (REQ-ID or "none because ...")

## Type (1 or 2 — explicit)

_Type 1 (irreversible: public API schema, auth, data architecture) demands
the design-doc link + RFC discipline; Type 2 (reversible) proceeds fast.
Unlabeled decisions default to Type 2 process — that is how Type 1 disasters
happen (principal-systems Op-rule 3)._

## Design doc (link — required for Type-1 and cross-lane changes)

## Alternatives considered and rejected

_No major component starts from a feeling (principal-governance)._

## Skill & Evidence (OPERATING-RULES §2.2)

- **Requirement/REQ-ID** (or why none applies):
- **Skill clauses that bind** — the specific clause, one line each on *how*:
- **Web sources consulted** (URL + what it changed in the design), or the
  prior-art citations (ADR, postmortem, research note):
- **Tool evidence** — the commands run and their outputs (test runs,
  `git ls-remote origin <branch>` for push claims, schema validation
  results). _Verify the artifact, never the report._

## Verification (tests run, outputs; external claims evidenced)

- [ ] **Full suite green, artifact attached.** Suite command, exit code, and
      the artifact (run log / runner receipt) are linked below. "Green" is
      the artifact, not the author's assertion.
- [ ] Contract/schema tests for every inter-stage contract this PR touches
      (fixtures validated against checked-in schemas — §3.3).
- [ ] Kill-the-client fail-open test green (or untouched and still green).
- [ ] No secrets in the diff (`.env`, keys, tokens, `*.pem`, routing keys).

| Claim of completion | Artifact evidence |
|---|---|
| _e.g. "pushed lane/prep-x"_ | _`git ls-remote origin lane/prep-x` output_ |
| _e.g. "deployed to staging"_ | _actual URL / receipt_ |

## Rollout / rollback plan

_How this lands, and how it comes back out. Rollback measured in seconds
where the change is user-visible._

## Reviewers (from the ownership map — OPERATING-RULES §1.6)

_Author ≠ reviewer, always. Required sign-offs are triggered, not assumed._

- [ ] Owner of the touched area (see ownership map)
- [ ] Vault sign-off — REQUIRED if: auth changes (any route/token/HMAC/key
      resolution), anything touching keys/secrets or the `cryptography`
      exception path, any write surface (`/api/v1/integrations/*`), any new
      ingress shape, any tamper-evidence machinery change
- [ ] Tripwire adversarial review — REQUIRED on every policy-change PR, any
      security-property claim ("tamper-evident", "authenticated",
      "attested"), any new external dependency/vendor integration
- [ ] No unresolved review threads (a thread without a reason is not a block;
      "LGTM pending" is not a thread)

## Definition of Done (all must be checked)

- [ ] Full suite green: artifact attached (see above — the GHA
      `startup_failure` era does not count as "CI was green")
- [ ] Claim-Auditor pass: every number asserted in this description has a
      source (file, line, URL, or run id) — or is marked `UNVERIFIED`
- [ ] Docs updated (README and/or the doc this change touches)
- [ ] No phantom interactivity introduced (antislop.py scan covers the
      touched surfaces; R-20 appeal-shape controls must be wired or absent)
- [ ] Lane-boundary crossings: this PR touches only its lane's files, OR the
      crossed files are listed below with the owning lane's acknowledgment:

<!-- ============ END TEMPLATE BODY ============ -->

---

## Rejected alternatives (template design)

1. **Keep the existing template and append a CI note.** Rejected — the
   existing template's "Full test suite green" checkbox is exactly the shape
   that let post-Oct-2 PRs merge with a dead runner; appending a note does
   not convert an assertion into an artifact. The merge requirement must be
   *structural* (artifact attached), not hortatory.
2. **Require `gh run view` green links.** Rejected — the runner list shows
   `startup_failure`; requiring a link that cannot be green would block all
   merges on a condition nobody can satisfy. The template names the honest
   interim (box-side runner artifact) instead of pointing at a dead system.
3. **Make Skill & Evidence optional for small PRs.** Rejected — the fight was
   already had in Session A1 (OPERATING-RULES §0, Prism vs. Petu): the
   *mandatory* part is the skill clause, and the scale of the rest follows
   novelty. Small PRs cite repo prior art; they do not skip the clause.
