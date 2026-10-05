# R-3/Q2 — `/v2/enqueue` Sender Inventory

> **GATED ON Q2 — UPDATE 2026-10-05 20:25 IST: T+0 FIRED.**
> Aditya released full authority and decided Q2: **HMAC enforcement** (with
> Q3 canonical PolicyVersion.content, Q6 split Jev-key/FakePD-only, Q8 wired
> override). The gate below is therefore **decided, not awaiting** — but per
> the lane brief, this document remains **design/prep only** and does not
> expand scope: the 12-hour execution wave owns the build. Nothing in this
> document changes `src/`, the live gate, or any route behavior.

- **Lane:** recv-2 (12H-PLAN §4, F3 — recv-2 owns the inventory)
- **Reviewer of record:** Vault — veto authority over the auth-matrix accuracy
  (12H-PLAN F3 verdict)
- **Date:** 2026-10-05 IST · **Branch:** `lane/prep-r3-auth` (from
  `program/architecture-revision` @ `f5469f2`, verified by `git ls-remote`)
- **Type:** 1 (a Type-1 contract change's blast radius is set by this list)

## 1. What this inventory covers

Every sender that can POST to `POST /v2/enqueue` (receiver.py
`SentinelHandler.do_POST` :608–654 → `_handle_alert_post` → `Pipeline.handle_pd`),
with its auth posture **today** and its cutover risk for the HMAC migration.
The sibling route `/webhook/generic` is already HMAC fail-closed (ADR-005);
the shadow tap (`/shadow/*`) is a separate route with its own semantics and is
explicitly **out of scope** here (verified separate in receiver.py :616–620).

## 2. How completeness was verified (and where it can still be wrong)

1. **Route-table grep** (read-only): `do_POST` dispatches only
   `/v2/enqueue`, `/webhook/generic`, `/episodes/resolve`, `/-/reload`,
   `/shadow/*`. No other path reaches `handle_pd`. (receiver.py :608–654)
2. **Repo-wide sender grep**: `grep -rn "v2/enqueue"` across `src/`,
   `tests/`, `docs/`, `scripts/` found **no in-repo HTTP client** that posts
   to the receiver's `/v2/enqueue` — the only hits are the forwarder's
   *outbound* PD endpoint (`forwarder.py:115`, `pd_sender.py:44`,
   `https://events.pagerduty.com/v2/enqueue`), test harnesses, and docs.
3. **Docs grep** for named senders: `USER-WORKFLOWS.md:105` (Alertmanager /
   Datadog / Grafana as sources pointed at the receiver), `docs/liveness.md:42`
   + receiver.py:630–632 comment (Alertmanager retry/drop semantics),
   `heartbeat-check.py` + `ops/devops-foundation.md:519` (`pagerduty:prod`,
   `alertmanager:eu-west` as `source_integration` subjects — i.e., named
   deployment estates whose traffic reaches the receiver).
4. **Explicit exclusions recorded** (§5) — each with the evidence for why it
   is not a sender.

**Residual gap (red item):** there is no running production receiver in this
environment, so **no live access-log sample was possible** — completeness
rests on code + docs, not on traffic evidence. The migration design
(`r3-hmac-migration.md`) mandates a 7-day auth-outcome measurement phase
(Phase A) *before* enforcement precisely to close this gap: it is the
artifact that proves the inventory against real traffic. An unnamed sender
discovered in Phase A is handled by the onboarding-mode mechanics, not by a
flag day.

## 3. The inventory

| # | Sender | Evidence it posts here | Auth today | Cutover risk |
|---|---|---|---|---|
| S1 | **Alertmanager** (Prometheus), pagerduty_config URL pointed at the receiver | receiver.py:630–632 comment; docs/liveness.md:42; USER-WORKFLOWS.md:105; heartbeat estate `alertmanager:eu-west` | **NONE** — no HMAC, no bearer. Alertmanager's PagerDuty receiver cannot attach custom headers; it cannot sign | **HIGH** — cannot sign natively; needs a signing relay/sidecar or documented risk acceptance (Aditya, Q2) |
| S2 | **Datadog**, PagerDuty integration pointed at the receiver | USER-WORKFLOWS.md:105 | **NONE** | **HIGH** — no custom-HMAC path for PD-shaped posts; relay or risk acceptance |
| S3 | **Grafana** (Alerting PagerDuty contact point), pointed at the receiver | USER-WORKFLOWS.md:105 | **NONE** | **HIGH** — PD contact point cannot sign. Note: the qhook provider table lists `X-Grafana-Alerting-Signature` for Grafana's own webhooks ([source](https://github.com/totte-dev/qhook/blob/HEAD/docs/guides/webhook-verification.md), accessed 2026-10-05) — a possible signing path **unverified** for the PD contact point; a research spike must confirm before relying on it |
| S4 | **Operator/custom senders** — scripts, curl, runbooks, internal tooling posting PD Events v2 payloads | deployment practice (deploy-production.md, runbook-cutover.md curl patterns); any host with TCP reach per receiver.md M-1 | **NONE** | **LOW** — they control the code; the signing scheme is documented (SECURITY.md §2.1, receiver.py :52–78) and a reference signer ships with the migration build |
| S5 | **Signed resolve/ack claim integrations** — POST `event_action: resolve/acknowledge` with `X-Sentinel-Signature` to close episodes (`verified_resolve`) | receiver.py :267–284, :302–355; tests/test_resolve_wiring.py | **HMAC already, fail-closed in every mode** (onboarding does NOT apply to the silence direction — receiver.py D10 wiring) | **NONE** — already authenticated; the migration must not regress this path |
| S6 | **Test harness / CI** — tests/helpers.py, test_receiver.py, test_liveness.py, test_resolve_wiring.py post unsigned | grep hits on `/v2/enqueue` in tests/ | **NONE (unsigned)** | **MEDIUM (internal)** — the Phase 1 build must update the suite to the signed matrix (PIPELINE-REVISION R-3 verifies); no production impact |

### Critical attribution finding

`handle_pd` labels **every** `/v2/enqueue` delivery `source="pagerduty"`
(receiver.py :505) regardless of the actual upstream. Today there is **no
per-sender attribution** on this route — Alertmanager, Datadog, and Grafana
deliveries are indistinguishable except by `routing_key` / `payload.source`.
Consequences for the migration:

- Per-sender cutover readiness can only be tracked by **routing_key
  inventory** (operator-supplied), not by anything the receiver logs.
- The onboarding-mode measurement phase must log the auth outcome keyed by
  routing_key hash — that log *is* the sender discovery artifact.
- Per-source secret sets are **deferred** to a later phase (see migration
  doc §4) — Phase 1 uses one secret set with a rotation window, because we
  cannot attribute deliveries to sources yet.

## 4. Cutover risk summary (what makes this Type-1)

- **S1–S3 cannot sign.** This is the hard core of Q2: the three documented
  production senders are external systems with no HMAC capability. Their
  cutover is not a header change — it is a **signing relay/sidecar** (new
  component, new failure modes) or an **explicit, logged risk acceptance**.
  That choice is Aditya's, which is why Q2 is Type-1.
- **403 does not retry.** Post-enforcement, an unsigned sender gets 403.
  Alertmanager **drops on 429 but retries only on 503** (receiver.py :630,
  docs/liveness.md:42) — a 403 is neither; it is a silent page loss. A
  mis-cut sender fails in the exact direction Sentinel exists to prevent.
  The migration's kill conditions (migration doc §6) are built around this.
- **Resolve claims (S5) are the one path that must not move.** Unsigned
  resolves never close episodes today; the migration keeps the silence
  direction fail-closed in all modes, including onboarding.

## 5. Explicit exclusions (verified non-senders)

| Candidate | Why excluded | Evidence |
|---|---|---|
| PagerDuty itself | PD webhooks (v3) arrive on `/shadow/pagerduty`; PD is the receiver's *downstream* (forwarder → events.pagerduty.com). PD has no native path that emits Events API v2 payloads to an arbitrary URL | shadow.py :657 `handle(vendor,...)`; forwarder.py:115 |
| forwarder.py / pd_sender.py | outbound only — they post *to* PagerDuty, never to the local receiver | pd_sender.py:44 `PD_ENDPOINT` |
| scripts/ops/heartbeat-check.py | reads the event-log SQLite directly by design ("the verdict must not traverse the thing it watches") — never an HTTP sender | heartbeat-check.py :1–8 docstring |
| `/webhook/generic` senders | separate route, already HMAC fail-closed (ADR-005); covered by its own auth matrix, not this inventory | receiver.py :654–655 |
| S7 — console "fire test alert" (ex-console sender, verified non-sender 2026-10-05) | the console's `testPage()` (gh-pages `assets/api.js:302`, live mode) POSTs to `/api/v1/integrations/test-page` (api.js:318) → backend `_int_test_page` (`platform/server/app.py:197–198, :421`) → `_pd_enqueue()` (app.py:477, defined :621) → **`https://events.pagerduty.com/v2/enqueue`** (PD's own Events API endpoint). It posts to PagerDuty directly — it never touches the local receiver's `POST /v2/enqueue`. In static/mock console modes nothing leaves the tab at all (api.js:302–315) | assets/api.js:302,318; platform/server/app.py:197–198,421,477,621 |

## 6. Rejected alternatives (inventory-scope decisions)

- **Treat `/shadow/*` as in-scope:** rejected — separate route, separate
  failure semantics (receiver.py :610–620), owned by the shadow domain.
  Mixing it in would blur the auth matrix the migration build needs.
- **Assume Alertmanager/Datadog/Grafana "can probably sign":** rejected —
  none of the three exposes custom-HMAC signing on their PagerDuty-shaped
  outputs; assuming otherwise is how you get a flag day that drops pages.
  The relay-or-acceptance choice is explicit in the migration doc.
- **Claim completeness from docs alone:** rejected — the 7-day measurement
  phase is the honest completeness check (see §2); this inventory is v0,
  final at T+6 per the 12H-PLAN cadence.

## 7. Author-written risk paragraph

The risk I am least comfortable with is the one this document cannot see:
the true production sender set is attested only by docs and code comments,
never by traffic. `USER-WORKFLOWS.md` names Alertmanager, Datadog, and
Grafana, but the receiver cannot tell them apart today, and any estate that
onboarded by pointing a PD-shaped sender at the route without writing it
down is invisible to this inventory. If such a sender exists and cannot
sign, enforcement-day 403s become silent page losses — the one failure mode
this product must never have. The mitigation is not a better document; it
is the migration's Phase A measurement window, which must run long enough
(7 days, covering a full weekly alert cycle) and must be gated on an
explicit "unsigned fraction ≈ 0 for known routing_keys" bar before anyone
flips enforcement. The second risk is scope creep: S7 (the console "fire test alert") was the
unverified item at inventory v0 — **resolved 2026-10-05** (see §5 exclusions:
it posts to PagerDuty's own endpoint, never the receiver's `/v2/enqueue`), so
it is no longer a red item and does not enter the Phase 1 build's verify list.

## Binding skill clauses

- *principal-governance §1 (single-threaded ownership):* recv-2 owns this
  inventory; Vault holds veto over the auth-matrix accuracy — the F3
  verdict, recorded here so routing is never ambiguous.
- *principal-governance (unforgiving API design):* `/v2/enqueue` and
  `/webhook/generic` have asymmetric trust postures with no written
  rationale — exactly the implicit-contract anti-pattern; this inventory
  makes the asymmetry explicit so Q2 can close it.
- *principal-mindset §2 (don't see the answer while developing):* the
  inventory is written before any enforcement code exists, and the Phase A
  measurement is pre-registered as the completeness check — no
  retro-fitting the sender list to match what the code happens to accept.
- *principal-systems law 2 (eternal friction):* the route's only current
  defense is `--bind 127.0.0.1` — a network-layer hope, not a trust
  boundary; the inventory treats every network-reachable sender as
  in-scope, including the hostile ones.

---
*12H-PLAN cadence: inventory v0 (this doc) → T+6 inventory final →
Vault veto review. T+0 fired 2026-10-05 20:25 IST — Q2 decided: HMAC
enforcement. The 12-hour execution wave owns the Phase 1 build; this
document stays design-only per the lane brief.*
