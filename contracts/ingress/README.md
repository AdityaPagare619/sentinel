# Contract C1 — L1 INGEST → L2 NORMALIZE

**Status:** `v0.1` · `stability: draft` · **Type-2 decision** (reversible draft;
additive churn permitted under 12H-PLAN F2)
**Consumer-owner:** receiver team — the normalize stage (today folded into
`src/sentinel/receiver.py`; the contract is what lets it become an explicit
stage). Per OPERATING-RULES §1.1 the consumer owns the contract: no provider
(L1) silently widens it; changes need a written amendment + same-hour
re-announcement.

## What crosses

Exactly one envelope — `RawInbound` (`raw-inbound.v0.1.json`):

`RawInbound{route, headers, body_base64, body_size_bytes, received_at,
request_id, auth_outcome}`

- `route`: the ingress route that accepted the delivery
  (`/v2/enqueue` | `/webhook/generic`).
- `headers`: HTTP headers as received, keys lowercased.
- `body_base64` + `body_size_bytes`: the raw request body. Base64 because JSON
  cannot carry arbitrary bytes safely — UTF-8 decode failures are exactly the
  M-9 exception-path class, and the envelope must be total over them.
  Decoded size must equal `body_size_bytes`.
- `received_at`: L1 acceptance time (ISO-8601). Distinct from any
  sender-supplied timestamp.
- `request_id`: the trace ID born at ingress (R-3) — honored from
  `X-Request-ID` when supplied, generated otherwise. Carried through every
  later stage (C5).
- `auth_outcome{status, reason_code, scheme, onboarding?}`: the ADR-005
  verdict attached at the boundary. `status` is `pass` | `fail` |
  `bypassed_onboarding`; `reason_code` is the terse ADR-005 code
  (`ok`, `no_secret`, `missing_signature`, `bad_mac`, `stale_timestamp`, … —
  grounded in `_webhook_sig_failure_reason`, receiver.py:101–137);
  `scheme` is `hmac-adr005` | `none`.

The *shapes of the bodies inside the envelope* are versioned alongside it:

- `pd-trigger.v0.1.json` — PD Events API v2 event shape on `/v2/enqueue`
  (the PD-mirror route). Field requirements grounded twice: PagerDuty's own
  developer docs (see `research/operating-without-vendor-apis.md` §4:
  required `routing_key`/`payload.summary`/`payload.source`/`payload.severity`,
  `dedup_key` ≤ 255 chars, `payload.summary` ≤ 1024 chars, 512 KB limit) and
  the repo's `_normalize_pd` required set (missing any ⇒ `Unparseable`).
  `event_action` ∈ {trigger, acknowledge, resolve}; only `trigger` enters the
  normalize→triage path — ack/resolve are relayed unchanged, untriaged
  (receiver.md §1.3).
- `generic-webhook.v0.1.json` — the shape on `/webhook/generic`. Grounded in
  `_normalize_generic`: only `service` is required (missing ⇒ `Unparseable`);
  everything else degrades to defaults (`severity`→`info`,
  `title`→`<service>:<check>`, metric fields→null).

Fixtures: `fixtures/<schema-name>/*.json` (2 per schema), all validated by
`../validate_contracts.py` (stdlib only, no pip deps).

## What NEVER crosses

1. **Unauthenticated bytes reaching triage.** `auth_outcome.status == fail`
   must never proceed to L2 normalization — the envelope is recorded for
   audit and the delivery is refused (or failed-open forwarded as raw bytes
   per route policy), but no unauthenticated byte reaches the triage path.
   This is the contract-level closure of receiver.md M-1: post-R-1,
   `/v2/enqueue` carries the same ADR-005 HMAC as `/webhook/generic`; until
   then the asymmetry is *recorded in the envelope*, never silent.
2. **Bodies over the route cap.** `/v2/enqueue`: 512 KiB (the PD-mirror cap —
   receiver.md M-3/R-2; today's 8 MiB code cap is 16× the vendor's own limit).
   `/webhook/generic`: 8 MiB until R-2 lands. L1 rejects with 413 before the
   envelope is constructed.
3. **Secrets and signature values.** The presented `X-Sentinel-Signature`
   value, the webhook secret, and the bearer token never enter `RawInbound`
   (nor `headers` as crossed) — the code's own discipline
   ("no secret/signature values in logs", receiver.py:121) extended to the
   contract.
4. **Vendor semantics.** The envelope carries bytes, not interpretations.
   `dedup_key` semantics, severity meanings, resolve-claim handling — all of
   that is L2's job. L1 authenticates and admits; it does not understand.

## Versioning

Additive changes inside a version (new optional fields, new `reason_code`
values, new enum members where the consumer rule tolerates them); a new
schema-file version only for true breaks — field renames/removals,
redefinitions of meaning. A field never changes meaning under the same name:
new meaning ⇒ new field, old one through expand → migrate → contract.
(Shaped by the web research below — the Stripe backward/forward-compatible
taxonomy and the kore1 "registries check shape, not meaning" incident.)

Postel split, deliberate: the machine-generated envelope is strict
(`additionalProperties: false` — conservative in what we send); the
vendor-facing ingress shapes are liberal (`additionalProperties: true` —
liberal in what we accept; the Tolerant Reader rule).

## Skill clauses that bind (OPERATING-RULES §2.1 — specific, not badges)

- **principal-governance §2, "Unforgiving API design":** internal interfaces
  are contracts; changing a consumed shape is massively disruptive, so it is
  versioned explicitly with a published additive-vs-breaking rule. This binds
  the v0.1 file naming, the `$id`, and the versioning section above.
- **principal-governance §2, "Postel's law":** conservative in what you send,
  liberal in what you accept. This binds the strict-envelope /
  liberal-ingress split — the asymmetry is the point, not an accident.
- **principal-governance §1.1, "The consumer owns the contract":** binds the
  ownership line above and the freeze discipline (frozen at lane start;
  mid-lane change ⇒ written ADR + same-hour re-announcement).
- **principal-systems law 2, "Eternal friction":** architect for the hostile
  world. This binds never-crosses #1 and #2 — the contract assumes hostile
  or wedged senders (unauthenticated injection, slowloris, 8 MiB bodies),
  not the demo world.
- **principal-mindset §2.2, "Freeze the protocol before the decisive
  measurement":** binds *why this exists as a draft now* — the contract is
  the pre-registered protocol the 12h lanes build against, written before
  any wiring.

## Web research that shaped this (OPERATING-RULES §2.2)

- KORE1, "Schema Evolution Without Downtime: Event Versioning"
  (https://www.kore1.com/event-schema-evolution-versioning/ — last updated
  2026-09-29, accessed 2026-10-05): the enum-addition dead-letter incident
  and the rule "a field never changes meaning under the same name; new
  meaning ⇒ new field via expand/migrate/contract" shaped the Versioning
  section (additive-inside-version, breaks get new file versions) and the
  Tolerant Reader posture on ingress shapes.
- "Building a Custom Webhook Provider: API Design Lessons from Stripe and
  GitHub"
  (https://medium.com/@instawebhook.com/building-a-custom-webhook-provider-api-design-lessons-from-stripe-and-github-10967a4d1bf1 —
  accessed 2026-10-05): "put the version in the envelope; make additive
  changes the default; tell consumers to ignore fields they don't recognize"
  shaped `$id`-in-filename versioning and `additionalProperties: true` on
  vendor shapes.
- Stripe API versioning taxonomy (backward-compatible: new optional fields /
  new event types; breaking: renames, removals — via the above and
  https://docs.stripe.com/changelog.md): shaped the additive-vs-breaking
  rule above.
- In-repo prior art (OPERATING-RULES §2.1 permits prior-art citation):
  `research/operating-without-vendor-apis.md` §4 grounded the PD field
  requirements; `receiver.md` R-6 named these exact two schema files.

## Alternatives considered and rejected

- **One `ingress.v0.1.json` with `oneOf` per route** — rejected: per-route
  files let a sender pin and review one route's contract; matches the
  PIPELINE-REVISION §2.2 naming.
- **Raw body as a JSON string in the envelope** — rejected: not total over
  non-UTF-8 bodies (the M-9 class); base64 keeps the envelope total.
- **Putting auth policy ("which routes need HMAC") in the schema** —
  rejected: policy is R-1's Type-1 decision (Aditya's §9 list); the contract
  records the *outcome* honestly (`scheme: none` where it applies) rather
  than legislating the policy.

## Open interfaces

- C5 (trace-ID envelope, evlog-1/corr-2 lanes): `request_id` is the field
  C5's envelope will carry; per-stage latency attribution is C5's, not C1's.
- qa-2 `tests/contracts/` skeleton (12H-PLAN F5): these schemas + fixtures
  plug into it; `validate_contracts.py` here is the interim machine check.
