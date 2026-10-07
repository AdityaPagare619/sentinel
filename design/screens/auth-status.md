# Screen: Auth Status — who is allowed to wake the machine

**Function code:** `AUTH` · **Route:** `/auth-status`
**API:** `GET /api/ingress/routes` · `GET /api/ingress/senders` ·
`GET /api/ingress/auth-matrix` · `GET /api/ingress/migration`

**Purpose:** R-3's damning finding: the highest-value ingress route,
`/v2/enqueue`, was unauthenticated while its sibling was HMAC-gated —
anyone with a TCP connection could inject arbitrary `critical` triggers or
poison correlator state with crafted `dedup_key`s. This screen makes the
ingress trust posture visible and the HMAC migration trackable: *which
routes are authenticated, which senders hold keys, who's migrated, and
what's still exposed.* Authentication is a trust surface; this is its
one-screen accountability view.

---

## 1. The operator's question

*"Can anyone inject alerts into my paging pipeline right now?"*

Secondary: *Which routes require HMAC? Which senders are cut over? Are any
keys stale, revoked, or failing? What's the migration status?*

## 2. The route table (above the fold)

Every ingress route, one row each, closed-vocabulary auth state
(P-LIB-1 — the status badges as a shared language):

```
POST /v2/enqueue        HMAC (ADR-005)   12 senders   12/12 migrated   last reject 3d ago
POST /webhook/generic  HMAC (ADR-005)    8 senders    8/8 migrated     last reject 11h ago
GET  /healthz           none (public)    —            —                by design
GET  /metrics          none (public)    —            —                by design
```

The columns are the whole trust posture: route, scheme, sender count,
migration fraction, last rejection. A route with scheme `none` that is not
`healthz`/`metrics` renders in critical register — an unauthenticated
decision-ingress route is the R-3 finding, and the screen must scream it
the day it reappears.

**The migration tracker.** During the HMAC cutover
(`SENTINEL_WEBHOOK_ONBOARDING=1` loud-accept window), each sender row shows
its migration state: `migrated` (HMAC-verified traffic in the last 24h) ·
`loud-accept` (traffic accepted, flagged, sender not yet cut over) ·
`silent` (no traffic in 7d — the sender may be dead, or may be the attack
surface). The loud-accept window has a date on it: *"loud-accept ends
2026-11-05 — after this, unsigned traffic is rejected."* A migration
without a deadline is a permanent exception.

## 3. The sender inventory

One row per sender (the R-3 "sender inventory" made concrete):

- **Identity:** sender name, key fingerprint (never the key), key age,
  rotation due date.
- **Health:** last successful HMAC verification, rejection count (24h),
  last rejection reason code (`bad_signature`, `stale_timestamp`,
  `unknown_key_id`).
- **Scope:** which routes the key is authorized for. A key authorized for
  `/v2/enqueue` is a key that can inject `critical` — the screen says so
  plainly, because scope is the blast radius.

Keys are write-only everywhere else in the product (BYOK flow); here they
appear as fingerprints and metadata only. **The screen never displays,
echoes, or exports a key** — a key value on this screen is a finding, not
a feature.

## 4. The rejection log (below the fold)

Recent auth rejections, newest first, with reason codes — the canary for
both attacks and misconfigured senders:

```
03:12:44  /v2/enqueue  bad_signature    sender: unknown key_id=k9f2   action: rejected
03:11:02  /v2/enqueue  stale_timestamp  sender: datadog-prod         action: rejected (clock skew 412s)
```

A spike in `bad_signature` from an unknown `key_id` is the shape of a
probing attack; a spike in `stale_timestamp` from a known sender is the
shape of a clock-skew misconfiguration. The screen doesn't diagnose —
it shows the reason codes and lets the operator's judgment work. (The
correlator-poisoning vector R-3 named — crafted `dedup_key`s — is closed
by authentication; the rejection log is where a bypass attempt would
first appear.)

## 5. Professional-software lineage

| P-LIB pattern | What it teaches this screen | Why it fits |
|---|---|---|
| P-LIB-3 Opsgenie one-screen accountability | Routing rules, escalation, on-call in one view — here: routes, senders, keys, migration in one view. | Auth posture scattered across docs and config is posture nobody checks. One screen, one glance. |
| P-LIB-1 PagerDuty status badges | Closed auth vocabulary (HMAC / none / loud-accept / migrated) as badges. | "Is this route authenticated?" must be answerable in one badge, not one paragraph. |
| P-LIB-9 calm technology | Healthy auth posture is periphery — quiet rows, no badges. Only `none`-on-ingress or a rejection spike promotes. | Auth is a trust surface, not an anxiety surface. Silence is the healthy state. |

## 6. What it must NEVER show (anti-fatigue rules)

1. **Never a key value.** Fingerprints, never secrets. (BYOK law; the
   screen that shows a key is the screen that leaks it in a screenshot.)
2. **Never "all secure" as the headline.** The headline is the route
   table with its schemes. "Secure" is the proxy that hid the
   unauthenticated route for months.
3. **Never per-request accept logs as the primary view.** The rejection
   log is the signal; a stream of 200/OK accepts is the firehose wearing
   an auth costume. (INV-U1: no unbounded volume lists.)
4. **Never hide the loud-accept deadline.** The migration window's end
   date is load-bearing UI. An exception without a date is permanent.
5. **Never conflate key health with route health.** A healthy key on a
   route that doesn't require it is not a finding; an unhealthy key on a
   route that does is. The screen keeps the two axes separate.

## 7. States

- **Empty (no senders):** *"No senders registered. Ingress is closed to
  everything except health checks."* — the fail-closed default, stated
  as the onboarding state.
- **Unauthenticated decision-ingress detected:** critical register, full
  width: *"POST /v2/enqueue is accepting unsigned traffic. This is the
  R-3 finding. Rekey and re-enable HMAC."* The screen's smoke-alarm
  moment.
- **Loading:** route table skeleton first.

## 8. API mapping

| UI need | Endpoint |
|---|---|
| route table | `GET /api/ingress/routes` (route, scheme, design rationale for `none`) |
| sender inventory | `GET /api/ingress/senders` (identity, fingerprint, health, scope) |
| auth matrix | `GET /api/ingress/auth-matrix` (the R-3 verification matrix, machine-readable) |
| migration | `GET /api/ingress/migration` (per-sender state, loud-accept deadline) |
| rejection log | `GET /api/ingress/routes` (rejection counters) + event log query |

Read-only. Key provisioning stays in the BYOK KEYS surface; rotation stays
in the operator CLI. This screen verifies and displays.
