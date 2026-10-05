# R-3/Q2 — HMAC Migration Design: `/v2/enqueue` Authentication

> **Q2 DECIDED 2026-10-05 20:25 IST — T+0 FIRED.**
> Aditya released full authority; Q2 was decided as **HMAC enforcement**
> (with Q3 canonical PolicyVersion.content, Q6 split key decision, Q8 wired
> override). This document is the **design-only handoff** for the 12-hour
> execution wave's Phase 1 R-3 build — per the lane brief, no scope
> expansion: nothing here changes `src/`, the live gate, or route behavior.
> The 12H-PLAN T+9 edge is the bar: **no discovery work left** for the
> Phase 1 build.

- **Lane:** recv-2 (12H-PLAN §4, F3 — migration design doc)
- **Expected reviewer:** Vault — veto authority over the auth-matrix accuracy
- **Date:** 2026-10-05 IST · **Branch:** `lane/prep-r3-auth` (from
  `program/architecture-revision` @ `f5469f2`, verified by `git ls-remote`)
- **Companion:** `docs/design/r3-sender-inventory.md` (sender inventory v0)
- **Type:** 1 — the ingress auth contract is a trust-boundary contract
  (ADR-005 was labeled Type-1 for the same reason); the decision itself is
  now made, this design is the implementation contract.

## 1. Problem statement (one paragraph)

`POST /v2/enqueue` — the highest-value ingress route — has **no
authentication on triggers** (receiver.md M-1; PIPELINE-REVISION R-3):
`_handle_alert_post` (receiver.py :649–652) calls `handle_pd` without the
ADR-005 HMAC check that already gates `/webhook/generic`. Any host with a
TCP connection can inject arbitrary `critical` triggers or manipulate
correlator state via crafted `dedup_key`s. The sibling route's asymmetric,
better posture has no written rationale. The fix reuses the existing,
tested pure-function check (`_webhook_sig_failure_reason`, receiver.py
:121–162) — the D10 extraction already paid for the sharing. The hard part
is not the check; it is the **sender cutover**: three documented production
senders (Alertmanager, Datadog, Grafana) cannot sign natively, and the
receiver cannot attribute deliveries to senders today.

## 2. Target auth matrix (every route × required auth)

This is the post-migration contract. Vault's veto applies to this table.

| Route | Post-migration auth | Failure mode | Notes |
|---|---|---|---|
| `POST /v2/enqueue` — `trigger` | HMAC (ADR-005 scheme, §3), fail-closed | **403** + no parse, no forward of the alert path; audit event `webhook.rejected` | The change. Enforcement is config-gated (§5) |
| `POST /v2/enqueue` — `acknowledge`/`resolve` relay | relay unchanged, untriaged (today's behavior) | — | BUT any delivery carrying these actions must itself pass the trigger-route HMAC to reach `handle_pd` at all; the **silence direction stays fail-closed in every mode, including onboarding** (existing D10 wiring, receiver.py :302–355) |
| `POST /v2/enqueue` — resolve/ack *claim* → `verified_resolve` | HMAC, fail-closed **in all modes** (unchanged) | unsigned claim refused the close, relay continues | Must not regress: onboarding loud-accept applies to triggers only, never to closes |
| `POST /webhook/generic` | HMAC (ADR-005), fail-closed (unchanged) | 403 | Already enforced; matrix test exists (test_signature_auth.py) |
| `POST /episodes/resolve` | Bearer `SENTINEL_HEALTH_TOKEN`, required even when unset (unchanged) | 401 | Operator control plane |
| `POST /-/reload` | Bearer token (unchanged; the when-unset asymmetry is receiver.md R-7's problem, out of scope) | 401/422 | Out of scope — do not touch in this migration |
| `GET /livez` | none — deliberate (unchanged) | — | Shallow liveness must stay unauthenticated |
| `GET /healthz` | Bearer token, open-when-unset (unchanged); gains `webhook_auth_fail_open` already + new `enqueue_auth_mode` flag (§5) | 503 with `failed` list | Observability of the migration itself |
| `POST /shadow/*` | per-vendor secrets (unchanged) | vendor-specific | Separate route, separate domain — out of scope |
| Platform `:8081` `/api/*` | unchanged | — | platform-api.md domain — out of scope |

**Contract versioning:** the ingress auth contract is a versioned,
machine-checkable artifact (OPERATING-RULES §1.1). The Phase 1 build writes
`contracts/ingress/pd-trigger.v1.json` naming the required headers and
failure semantics, with a contract test that fails when code and contract
disagree (the receiver.md R-6 pattern — this migration is the forcing
function for it on the auth dimension).

## 3. The scheme (reuse, not invention)

The enforced scheme is **exactly ADR-005**, already implemented and tested
for `/webhook/generic` and resolve claims:

- Headers: `X-Sentinel-Timestamp: <unix seconds>` +
  `X-Sentinel-Signature: sha256=<hex>`,
  `hex = HMAC-SHA256(secret, b"<timestamp>.<raw body>")`
- `|now − ts| ≤ 300s` (`SIGNATURE_MAX_SKEW_S`); constant-time
  `hmac.compare_digest`; verify over **raw bytes before parse**
  (SECURITY.md §2.2 rules 1–2)
- Secret floor: ≥16 chars; **startup refuses** (`SystemExit`) on empty or
  weak secret (receiver.py :1176–1183)
- Rejection is an audit event; never log the secret or full signature
  (SECURITY.md §2.2, §2.4)

**One deliberate addition — secret SETs with accept-if-any.** Today
`config.webhook_secret` is a single secret. The migration accepts an
ordered *set* of currently-valid secrets and passes if ANY matches. This
is not invention: it is the convergent industry pattern — Stripe sends
multiple `v1` values while a signing secret rotates and a match against any
one is valid ([Stripe webhook docs](https://docs.stripe.com/webhooks),
accessed 2026-10-05; scheme detail per
[Stripe signature verifier reference](https://github.com/bobhuang1/dotnetcode/blob/HEAD/StripeWebhook/AzureFunction/README.md),
accessed 2026-10-05), and PagerDuty's own webhooks rotate via a dual-secret
window where the verifier accepts a comma-separated list
([qhook provider table](https://github.com/totte-dev/qhook/blob/HEAD/docs/guides/webhook-verification.md),
accessed 2026-10-05). SECURITY.md §2.2 rule 5 already mandates this
rotation procedure ("add new secret to the set → roll out to the sender →
remove old secret"); the code just doesn't implement the set yet. **This
changed the design**: an earlier single-secret draft would have required a
flag day for every rotation; the set makes rotation (and the migration
itself) flag-day-free. Per-source secret sets are **deferred** — the
inventory proved we cannot attribute `/v2/enqueue` deliveries to senders
today (all labeled `source="pagerduty"`), so per-source keys would be
unenforceable fiction. Revisit when the request-ID/attribution work
(receiver.md R-3) lands.

## 4. Onboarding-mode window design (no flag day)

`SENTINEL_WEBHOOK_ONBOARDING=1` already exists with the right loudness
(receiver.md §1.2): CRITICAL boot warning, per-request WARNING,
`webhook_auth_bypassed` metric, `webhook_auth_fail_open=true` on
`/healthz`. The migration extends it into a **three-phase window** rather
than a boolean cliff:

- **Phase A — shadow measurement (7 days, covering a full weekly alert
  cycle).** Enforcement code ships with onboarding=1. Every `/v2/enqueue`
  delivery is verified *and logged* (auth outcome: valid/invalid/missing,
  keyed by routing_key hash — never the key, never the secret) but **none
  is rejected**. This log is the artifact that closes the inventory's
  residual gap: it proves the sender set against real traffic. Exit bar:
  unsigned fraction ≈ 0 for every inventoried routing_key, and no
  uninventoried routing_key with material volume.
- **Phase B — readiness attestation (bounded, default 7 days).** Each
  inventoried sender demonstrates a signed delivery in the log; unsigned
  stragglers get named owners and deadlines. Cannot-sign senders (S1–S3 in
  the inventory) land their relay or their logged risk acceptance here —
  this is the Q2 follow-through, owned by the operator, not the build.
- **Phase C — enforcement.** `SENTINEL_WEBHOOK_ONBOARDING=0`. Unsigned →
  403. **Kill condition (pre-registered):** if unsigned deliveries exceed
  1% of total in any rolling 24h during the first 7 days of enforcement,
  or any heartbeat dead-man trips (`heartbeat-check.py`) correlated with
  the flip, enforcement reverts to Phase A automatically-by-runbook (see
  §7). Hitting the kill condition is a successful outcome — a clean revert
  beats a slow page-loss bleed (principal-mindset §6).

**The onboarding invariant that never bends:** loud-accept applies to
**triggers only**. The silence direction (unsigned resolve/ack claims)
stays fail-closed in *every* mode, including onboarding — this is existing
D10 behavior (receiver.py :302–355) and the migration must carry it
verbatim. A test in the Phase 1 suite asserts it: onboarding=1 +
unsigned resolve → 200 relayed, episode NOT closed.

**New observability (small, required):** `/healthz` gains
`enqueue_auth_mode: {onboarding|enforcing}` and the metrics surface gains
labeled counters `auth_outcome{route, outcome}` (valid/invalid/missing) —
the receiver.md M-5 gap makes these the operator's only window into the
cutover. The 403 body tells an unsigned sender exactly what to do (which
headers, pointer to the onboarding doc) — a 403 that doesn't teach is a
support ticket.

## 5. Cutover plan with sender cutover costs

| Sender (inventory #) | Cutover action | Cost (rough) | Owner |
|---|---|---|---|
| S4 operator/custom senders | Add two headers per the documented scheme; reference signer snippet ships in the onboarding doc | ~20 lines per sender; self-serve | Sender owner |
| S6 test harness / CI | Update suite to the signed matrix (mirrors `test_signature_auth.py` for `/v2/enqueue`); onboarding-mode acceptance test | One PR, mechanical | Phase 1 build lane |
| S5 resolve-claim integrations | None — already signed; regression-test only | 0 | Phase 1 build lane (test) |
| S7 console test sender | Verify whether it posts to `/v2/enqueue`; if yes, sign like S4 | Unknown until verified — red item | Phase 1 build lane (verify first) |
| S1 Alertmanager | **Signing relay/sidecar** per estate (receives unsigned from Alertmanager on loopback, signs, forwards) — OR logged risk acceptance | Relay: new component, deploy + config per estate (~days); acceptance: Aditya's written word, logged per SECURITY.md Opsgenie-note precedent | Operator + (relay = new build scope, flagged, not in this design) |
| S2 Datadog | Same relay-or-acceptance choice | Same | Operator |
| S3 Grafana | Same relay-or-acceptance choice; research spike first on whether `X-Grafana-Alerting-Signature` covers the PD contact point | Spike: hours; relay: same as S1 | Operator |

**Why 403 and not a softer code:** the generic route already returns 403
on bad auth, and Alertmanager's retry/drop semantics (503 retries, 429
drops — receiver.py :630) mean *no* error code is safe for a mis-cut
sender; the safety comes from Phases A/B proving the sender set, not from
the status code. The kill condition (§4) is the backstop.

## 6. Rollback plan

Enforcement is a **config flip, not a code rollback** (execution-doctrine:
decoupled deployment — instant flag-off, no restarts of the wrong kind):

1. **Trigger:** any of — 403 rate spike correlated with known sender
   routing_keys; heartbeat dead-man trip within 1h of the flip; operator
   page-storm report; the 1%-unsigned kill condition (§4).
2. **Action:** set `SENTINEL_WEBHOOK_ONBOARDING=1` + process restart
   (bounded, <5 min). The `/healthz` `enqueue_auth_mode` flag and the
   `webhook_auth_bypassed` metric confirm the revert observably.
3. **Secret-rotation rollback:** because verification accepts a secret
   *set*, a bad new secret is rolled back by removing it from the set —
   senders on the old secret never notice. No sender-side change needed.
4. **What rollback does NOT require:** no code revert, no redeploy, no
   sender reconfiguration. If a rollback ever needs a code revert, the
   migration's deployment design failed and that is a postmortem item.
5. **Post-rollback:** the Phase A log from the enforcement window is
   preserved and analyzed — it names exactly which senders were unsigned,
   which becomes the Phase B work list. Rollback is a return to
   measurement, not a defeat.

## 7. Rejected alternatives (with reasons)

- **mTLS for sender auth:** stronger in theory, but stdlib-only TLS
  client-cert handling plus operator certificate lifecycle management is a
  large UX and ops cost for a bring-your-own-sender product; HMAC reuses
  the tested ADR-005 scheme both routes already share. Revisit if the
  threat model grows to mutual distrust between Sentinel and senders
  (receiver.md appendix — carried verbatim).
- **Accept the risk with network policy (no auth):** rejected — this is
  the status quo dressed as a decision. The route's only defense would
  remain `--bind 127.0.0.1`, a network-layer hope, not a trust boundary
  (principal-systems law 2, eternal friction). PIPELINE-REVISION R-3's
  whole finding is that this hope is insufficient for a page-or-suppress
  product.
- **IP allowlist instead of HMAC:** already REJECTED by the ADR-005
  adjudication (docs/adr-decisions-2026-10-03.md): Forge — egress IPs
  rotate, an opt-in knob failing closed on rotation turns every provider
  IP change into a dropped-alert incident; Vault — the opt-in becomes
  de-facto required and buys nothing HMAC doesn't already buy. Re-litigating
  it here would violate disagree-and-commit.
- **Bearer API key on `/v2/enqueue` instead of HMAC:** rejected per the
  SECURITY.md Opsgenie note — a leaked bearer value allows arbitrary
  spoofing with no replay bound; HMAC binds the signature to the exact
  bytes and the timestamp. Bearer proves possession of a string; HMAC
  proves authorship of *this* delivery.
- **Per-source secrets in Phase 1:** rejected as unenforceable fiction —
  the inventory proved per-sender attribution is absent on this route
  (all deliveries labeled `source="pagerduty"`). Ship the shared set with
  the rotation window now; per-source keys wait on the attribution work.
- **Hard flag-day cutover (no onboarding window):** rejected — Stripe and
  PagerDuty both converge on overlapping-validity windows (dual-secret /
  multiple-`v1`) precisely because flag days drop traffic; the three-phase
  window is the industry pattern, and the 403-drop direction for
  non-retrying senders makes a flag day a page-loss event.

## 8. Author-written risk paragraph

The risk that keeps me up is the **403-drop direction combined with the
attribution gap**. Post-enforcement, an unsigned sender doesn't get a
retryable signal — it gets a 403, which Alertmanager neither retries nor
drops loudly; the alert simply never pages. Phases A and B are supposed to
prove the sender set before that happens, but Phase A can only measure
senders that *send during the window* — a quarterly DR-test sender, or an
estate that onboards mid-window, is invisible. The kill condition catches
volume, not silence: a sender that goes quiet after the flip looks exactly
like a quiet week. The honest mitigation is the heartbeat dead-man's
switch (`heartbeat-check.py`) per `source_integration`: enforcement must
not flip until every inventoried estate has a heartbeat expectation, so
that an estate going silent *pages*. The second risk is the deferred
per-source secrets: a shared secret set means one compromised secret
rotates every sender at once, and the rotation window (§3) is the only
thing standing between a leak and a flag day. That is acceptable for
Phase 1 and must be written down as tech debt with the attribution work
as its retirement plan — not silently absorbed.

## 9. What the Phase 1 build must produce (Definition of Ready, §3.5)

No discovery work left — the build lane starts from this list:

1. Apply `_webhook_sig_failure_reason` in the `/v2/enqueue` branch of
   `_handle_alert_post` (receiver.py :649–652) for **all** deliveries;
   keep the D10 resolve-claim wiring inside `handle_pd` fail-closed in
   every mode (including onboarding).
2. Secret-set support in config (ordered set, accept-if-any, ≥16 chars
   each, startup `SystemExit` on empty set) — replaces the single
   `webhook_secret` for this route; `/webhook/generic` migrates to the
   same set for one scheme everywhere.
3. Onboarding-mode three-phase mechanics (§4): per-delivery auth-outcome
   logging keyed by routing_key hash; `enqueue_auth_mode` on `/healthz`;
   `auth_outcome{route,outcome}` counters; teaching 403 body.
4. `contracts/ingress/pd-trigger.v1.json` + contract test (code↔contract
   agreement enforced in CI).
5. Test matrix: `/v2/enqueue` mirroring `test_signature_auth.py`
   (absent/bad/stale/future/malformed signature → 403, nothing triaged;
   valid → 200); onboarding-mode acceptance (unsigned trigger accepted
   loudly; unsigned resolve still refused the close); secret-set rotation
   test (old+new both valid, old removed → old invalid).
6. Runbook section: the flip procedure, the kill condition, the rollback
   steps (§6), and the S1–S3 relay-or-acceptance tracker.
7. Resolve the S7 red item (console test sender) before enforcement.

**Out of scope for the R-3 build (explicit):** the signing relay for
S1–S3 (new component, separate design), per-source secrets, the
replay-cache (receiver.md R-5), request IDs / `/metrics` (receiver.md R-3)
except the two counters named above, and `/-/reload` auth consistency
(receiver.md R-7).

## Binding skill clauses

- *principal-systems law 2 (eternal friction):* the design assumes a
  hostile or misconfigured sender on the network — the current
  `--bind 127.0.0.1` hope is named as the thing being replaced, not
  relied upon.
- *principal-systems §2 (pre-mortem):* §8 is the written pre-mortem —
  "it is 2027 and the migration dropped pages"; the kill condition, the
  dead-man-gated flip, and the config-level rollback are the mitigations
  designed against it.
- *principal-governance (unforgiving API design + contract first):* the
  ingress auth contract is versioned (`pd-trigger.v1.json`) and
  machine-checked; senders migrate against the contract, and the contract
  test fails the build on drift.
- *principal-governance (decoupled deployment):* enforcement ships dark
  behind the onboarding flag; the flip and the rollback are config
  changes, never code deploys.
- *execution-doctrine §6 (validate → shadow → canary):* Phase A is the
  shadow (verify-and-log, reject nothing), Phase B the readiness gate,
  Phase C the canary-with-kill-condition — in that order, never skipped.
- *principal-mindset §6 (monkey-first + dated kill conditions):* the
  hardest part — the cannot-sign senders S1–S3 — is attacked first
  (relay-or-acceptance must land in Phase B, before enforcement), with the
  1%-unsigned kill condition pre-registered with its measurement window.
- *principal-mindset (proxy-trap audit):* the auth-outcome counters must
  measure true unsigned-sender volume — the receiver.md M-9 lesson (metrics
  that misclassify mislead the next incident review) binds the logging
  design in §4.

## Web sources consulted (what each changed)

1. [Stripe webhook docs](https://docs.stripe.com/webhooks) — accessed
   2026-10-05. The canonical `t=<ts>,v1=<hex>` scheme, signed payload
   `"{t}.{raw body}"`, 300s tolerance, and **multiple `v1` values during
   rotation (match-any)**. Changed the design: single-secret draft →
   secret *set* with accept-if-any (§3); Stripe's rotation practice is the
   precedent for the no-flag-day rotation procedure.
2. [Stripe signature verifier reference](https://github.com/bobhuang1/dotnetcode/blob/HEAD/StripeWebhook/AzureFunction/README.md) — accessed 2026-10-05. Hand-rolled
   HMAC-SHA256 verification without the SDK, constant-time comparison,
   and the precedent that the signature check *is* the authentication on
   an otherwise-anonymous endpoint ("Never remove that check while
   leaving the endpoint anonymous") — quoted because it states the M-1
   lesson in one line.
3. [qhook webhook-verification provider table](https://github.com/totte-dev/qhook/blob/HEAD/docs/guides/webhook-verification.md) — accessed 2026-10-05. Cross-provider
   convergence (PD `X-PagerDuty-Signature: v1=<hex>`, Grafana
   `X-Grafana-Alerting-Signature`, GitHub/Stripe variants — all
   HMAC-SHA256 over raw bytes). Changed the design: confirmed HMAC
   (not mTLS, not bearer) as the convergent choice, and surfaced the
   Grafana-signature research spike for S3.
4. [PagerDuty/Datadog dual-secret rotation precedent](https://github.com/100rd/omniscience/issues/151) — accessed 2026-10-05. PD accepts a
   comma-separated secret list during rotation (match-any); Datadog signs
   `<timestamp>:<raw_body>` with `X-Datadog-Signature` +
   `X-Datadog-Signature-Timestamp`. Changed the design: the migration's
   rotation procedure mirrors PD's own dual-secret window, and the
   Datadog timestamped variant informed the onboarding log's replay
   analysis.

---
*T+0 fired 2026-10-05 20:25 IST — Q2 decided: HMAC enforcement. This is the
complete handoff for the Phase 1 R-3 build: no discovery work left. The
execution wave owns the build; this lane's scope stays design-only.*
