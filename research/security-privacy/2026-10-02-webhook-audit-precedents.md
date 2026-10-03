# 2026-10-02 — Webhook auth, audit integrity, and residency precedents

**Question:** How do production webhook receivers authenticate senders, how do
audit logs achieve tamper-evidence, and what are the realistic data-residency
answers for a US-hosted inference dependency?

## Observed facts

**Webhook authentication (convergent industry standard):**
- HMAC-SHA256 is the standard: GitHub (`X-Hub-Signature-256`), Stripe
  (`Stripe-Signature: t=…,v1=…`), Shopify (`X-Shopify-Hmac-SHA256`),
  PagerDuty (`X-PagerDuty-Signature`, `v1=…` format), Slack (timestamp + body).
  (github.com/totte-dev/qhook docs/guides/webhook-verification.md;
  github.com/jtarchie/steps docs/webhooks.md; accessed 2026-10-02)
- Hardening rules, consistently repeated: constant-time signature comparison
  (timing attacks); **read the raw request body for verification** — parsed
  JSON may differ from signed bytes; timestamped schemes enforce ~5-minute
  tolerance (replay protection — Stripe, Slack); **empty secret refuses every
  delivery** ("no secret, no check" turns a deployment mistake into an open
  endpoint); never log raw signing secrets.
  (jtarchie/steps docs/webhooks.md; laywill/awesome-claude-code-subagents
  webhook-configurator.md; accessed 2026-10-02)
- IP allowlisting is widely judged low-value vs HMAC: operational brittleness
  (rotating CIDRs) without significant security benefit over verified
  signatures. (github.com/thanigaiv/pagefree planning research; accessed 2026-10-02)
- Delivery robustness: fast ACK (200 OK before heavy processing — providers
  expect ack within ~5s); idempotent handling (dedupe on delivery ID, 200 with
  existing ID for duplicates); exponential backoff with jitter on retries;
  dead-letter queue for exhausted failures with replay tooling.
  (laywill webhook-configurator; h4vzz/awesome-ai-agent-skills; accessed 2026-10-02)

**Audit integrity:**
- Our append-only design (no UPDATE/DELETE paths) matches the baseline. The
  next rung is hash-chained log records (each row commits to the previous
  row's hash) and/or WORM storage — precedents exist but add operational cost;
  staged as post-Sunday hardening, not Sunday scope.

**Residency vs US-only inference:**
- TypeSafe is US-only hosting (research §3.2). Honest mitigations available
  now: minimization (alert metadata crosses the wire, never customer data
  beyond what's needed for triage); the audit log stays customer-side;
  contractual (DPA). The structural answer — customer-VPC relay / regional
  inference — is post-Sunday architecture, listed as such.

## Inferences

1. Our receiver's planned `X-Sentinel-Signature` (HMAC-SHA256) matches the
   industry standard exactly — no exoticism needed. Adopt the full hardening
   checklist: raw-body-before-parse, constant-time compare,
   empty-secret-refuses, 5-min timestamp tolerance on timestamped schemes.
2. No IP allowlisting — precedent says brittleness without benefit.
3. Fast-ACK + idempotent ingest is already our Law 1 posture (never 5xx on
   triage failure); the DLQ concept is worth adding to the receiver's
   recorded-failure path post-Sunday.

## Honest limitations

- Sources are practitioner implementation guides, not formal standards; NIST
  guidance not consulted tonight — queued as a follow-up question.
- PagerDuty's webhook-signing doc was cited second-hand (via qhook/steps);
  direct verification against support.pagerduty.com is queued.

## Implications

- Proposed ADR-005 (webhook auth hardening checklist → receiver spec).
