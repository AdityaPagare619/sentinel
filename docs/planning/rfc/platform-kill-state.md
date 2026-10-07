# RFC — Externalized kill state for the serverless tier

**Status:** PROPOSED · **Type:** 1 (safety architecture — hard to reverse
once operators depend on it; demands this rigor) · **Owner:** Team 3
platform/infra · **Date:** 2026-10-07 · **Lane:**
`lane/faang-platform-20261007`

**Decides:** where the kill-switch flag lives so that an engage is
visible to every instance and every tier that must honor it.

**Does not decide:** kill semantics — already decided at owner level
(Petu's binding ruling, FAANG wave: **kill = HALT all paging,
fail-closed**). This RFC designs the *distribution* of that decision,
not the decision. It also does not implement anything: implementation is
a future lane after Petu's review, and it must not touch the deployed
tier's safety endpoints until the hand-test window closes.

## 1. Problem

On the Vercel tier the kill flag lives in per-instance `/tmp`
(`platform/server/safety_api.py`: `_INSTANCE_ID`, `kill_state_scope:
"per_instance_tmp"`). Live, the project serves from multiple regions
(`cle1`+`sfo1` observed); cold starts wipe the flag. An engage on
instance A does not change instance B, and a cold start silently
disengages — the dangerous direction. The deployed tier hosts no paging
path at all, so today the switch is honestly marked `NO-OP LEVER` in
the console. That marking is the correct interim state; this RFC is the
real fix design.

Anchor: **Knight Capital** — per-instance safety flags kill companies.
Cloudflare's kill-switch literature: keep the switch off the system it
kills, and drill it. Our own audit: "a flag nothing reads, with copy
describing the opposite action."

## 2. Requirements

1. An engage must be readable by every live instance within seconds —
   no per-instance divergence, no silent cold-start disengage.
2. Write failure must be LOUD: `503 kill_store_unreachable`, never a
   200 that changed nothing (the fabricated-toast failure mode, moved
   server-side).
3. Read failure must be HONEST: `engaged: null` + scope
   `external_unreachable` — never a claim of "disengaged" we cannot see.
4. Fencing: a stale instance must never resurrect an older transition
   (Kleppmann-style fencing tokens — monotonic generation, readers honor
   the max).
5. ₹0. No new dependencies (dependency law: stdlib-only default).
6. The paging tier (self-hosted receiver / wherever `DurableForwarder`
   runs) must be able to read the same flag — otherwise externalization
   is a better-labeled no-op. The receiver-side read is engine-team
   follow-on work, named in §8.

## 3. Options considered

### A. Upstash Redis free tier via REST — RECOMMENDED

Upstash Redis: free tier = 256 MB, 500,000 commands/month, 10 GB
bandwidth, TLS, single region, no card (verified 2026-10-07 against
Upstash's published pricing and three independent 2026 comparisons).
REST API (`https://<endpoint>/set/<key>/<value>`, `Authorization:
Bearer <token>`) works from serverless Python with **stdlib `urllib`
only** — no new dependency, satisfying the dependency law.

Correction to the audit's framing: "Vercel KV" is dead — sunset
December 2024, existing stores migrated to Upstash, new projects
provision Redis via the Vercel Marketplace (verified 2026-10-07). The
vehicle is Upstash Redis directly, not Vercel KV.

Design:

- One key: `sentinel:kill:v1` → JSON
  `{engaged: bool, generation: int, engaged_at: str|null,
    actor_id: str, decided_semantics: "halt_all_paging_fail_closed"}`.
- **Single writer**: the safety API (`POST /api/v1/safety/kill`,
  `POST /api/v1/safety/rearm`). Generation increments on every
  transition. No write-write conflicts by construction.
- **Fencing**: readers (status endpoint, ops/health, the future
  receiver-side poll) honor the highest generation seen; a response
  carries its generation so the console can display staleness.
  A cold-started instance reads on boot and on every safety call —
  it can never act on an older generation than it has seen.
- **Engage path**: write to Upstash → on success, 200 with
  `{engaged, generation}`; on store failure, **`503
  kill_store_unreachable`** — the engage did not land, and the operator
  is told so. This is the anti-Knight-Capital clause.
- **Read path** (`GET /api/v1/safety/status`, `ops/health`): read-through
  with a short in-process cache (10 s). Store unreachable →
  `kill_state_scope: "external_unreachable"`, `engaged: null`. The
  console renders "unknown", never "disengaged".
- **Absent env vars** (`UPSTASH_REDIS_REST_URL`/`TOKEN` unset) → the
  write endpoints return **`501 kill_not_distributed`** and status
  reports `kill_state_scope: "not_distributed"`. This is the audit's
  honest alternative, built in as the default — never a flag presented
  as a safety control when it isn't one.
- **Free-tier budget** (honest math): a kill flag is write-rare,
  read-often. Status reads: console polls ops/health ~every 30 s per
  operator session; safety/status on demand. 10 operators × 2 reads/min
  × 43,200 min/mo ≈ 864K reads/mo — OVER the 500K budget at that
  cadence. Mitigations, in order: (a) 10 s in-process cache collapses
  concurrent polls to ~6 reads/min/instance; (b) 2 warm instances × 6 ×
  43,200 ≈ 518K — still tight; (c) poll cadence 60 s for the safety
  section (the pipeline strip is the fast one; the kill card is not) →
  ~260K/mo, inside budget with headroom. The RFC mandates the 60 s
  safety-section cadence and an Upstash usage alert at 70%. If usage
  approaches the cap, reads degrade to the cached value with
  `stale_read: true` — loud, not silent.
- **Security**: the Upstash REST token is a Vercel env var, Sensitive
  (write-only). Writes already require the C1 operator bearer; the
  store adds no new principal. Transitions append to the audit log
  (the current `log=None` contradiction in `safety.py` must be fixed
  in the implementation lane — every transition appended, per the
  module's own contract).
- **Cold start**: first safety call in a fresh instance does a
  read-through (one REST round-trip, ~50–150 ms) — acceptable; the
  kill path's latency budget is seconds, and the 2.0 ms drill number
  was the in-process forwarder poll, not this path. Do not conflate
  the two in any future headline.

### B. DB row with fencing (Neon/Supabase free tier) — REJECTED

A single-row table with a generation column satisfies requirements
1–4, and the team already knows Postgres. Rejected for this tier:
heavier operational surface (schema, migrations, connection pooling
from serverless), and the free tiers carry sharper idle/scale-to-zero
cold-start penalties than Upstash's REST path. Revisit if the kill
flag ever needs relational company (audit trail in the same
transaction) — today it doesn't; a key is enough. (Subtract before
adding: the key is the smaller thing that works.)

### C. Gossip / instance-mesh broadcast — REJECTED

Instances notifying each other of engages (e.g., via Vercel's edge or
a fan-out endpoint). Rejected: on serverless there is no stable mesh
membership, no ordering guarantee, and a partitioned instance would
diverge silently — the exact failure mode we're eliminating. This is
the "clever" option; the key-value store is the boring one. Boring
wins for safety controls.

### D. 501 `kill_not_distributed` as the whole answer — REJECTED as the
### end state, ADOPTED as the fallback

Making the flip endpoint always 501 is honest but abandons the safety
control entirely — the product's purpose is paging, and a pager with
no kill switch is the quant_platform failure ("dashboard showed
active:true while the engine kept placing orders"). Adopted as the
*fallback* inside option A (§A, absent-env path), not as the design.

## 4. Residual risks (stated, not hidden)

1. **Upstash free tier is single-region, no SLA.** An Upstash outage
   engages the 503-loud path: the operator cannot flip the switch
   remotely. Mitigation: the paging tier's *local* kill (the
   `DurableForwarder` in-process poll, 2.0 ms drill) remains the fast
   path where a forwarder exists — the external flag is the
   cross-instance/cross-tier substrate, not the only switch. Both must
   exist; neither may claim to be the other.
2. **Token rotation is a Vercel env change** (needs redeploy to take
   effect on serverless — env vars are read at boot). Rotation
   procedure: provision new token in Upstash → set Vercel env →
   redeploy via REPEATABLE-DEPLOY.md → verify → revoke old. The
   dual-accept ceremony the rotation doc describes is the target;
   flag-day is the current reality (audit P1-17) — this RFC does not
   fix rotation, it names the gap.
3. **Hobby non-commercial-use clause** (known latent constraint): the
   tier runs under Vercel Hobby. Commercial use requires a plan
   change; the kill-state design is plan-agnostic but the constraint
   is noted here so nobody is surprised.
4. **The external flag means nothing until the paging tier reads it.**
   Today no paging path runs on Vercel. The self-hosted receiver must
   poll this key (or the kill stays a better-distributed no-op).
   That read is engine-team work — tracked as a follow-on in §8, with
   a dated check below.
5. **Free-tier exhaustion → stale reads.** Budgeted in §A; the
   `stale_read: true` signal and the 70% usage alert are the guardrails.
   If the product outgrows 500K cmds/mo, the paid step is $0.20/100K
   — a money decision for Aditya, not a redesign.

## 5. Pre-mortem — top 3 failure modes

(Assumed it is one year later and the externalized kill failed
catastrophically.)

1. **Engage returned 200; the write never landed; paging continued.**
   Cause: the 200 was sent before the store acknowledged, or a retry
   swallowed the error. Mitigation (in the design, not in hope):
   200 is sent ONLY after the Upstash SET acknowledges with the new
   generation; any store error → 503. The implementation lane must
   include a fault-injection test (store down → engage → expect 503,
   expect zero "engaged" claims).
2. **Operator disengaged from a stale console; an older generation
   overwrote a newer engage.** Cause: last-writer-wins without
   fencing. Mitigation: single writer + generation counter; the
   re-arm path requires the operator to have seen the current
   generation (confirm body carries `generation`; mismatch → 409
   `stale_generation`, re-read required). No silent overwrites.
3. **Upstash free tier exhausted mid-incident; reads go stale during
   the one hour the flag matters most.** Cause: budget math was
   optimistic. Mitigation: the 60 s safety cadence, the 70% alert,
   `stale_read` signaling — and the local in-process kill on the
   paging tier (§4.1) as the independent fast path.

## 6. Alternatives rejected — record

| Alternative | Why rejected |
|---|---|
| Vercel KV | Dead (sunset Dec 2024); the audit's reference is stale. |
| DB row (Neon/Supabase) | Heavier surface for a single key; worse serverless cold starts. |
| Instance gossip | No stable membership on serverless; silent divergence. |
| Always-501 | Honest but abandons the control; adopted as fallback only. |
| In-process flag + docs | The current system; proven insufficient (multi-region, cold start). |

## 7. Verification receipts

- Vercel KV sunset Dec 2024 → Upstash via Marketplace: confirmed via
  Vercel's Redis docs as reported by layerbase.com migration guide
  (2026-08) and 6+ independent 2026 migration commits
  (`@vercel/kv` deprecated on npm).
- Upstash free tier (256 MB / 500K cmds/mo / 10 GB bandwidth / TLS /
  no card): Upstash published pricing + bex.co 2026-09 comparison +
  tech-insider.org 2026-09 table — three-way agreement.
- Upstash REST API works from serverless over HTTPS (no TCP hold):
  Upstash docs pattern, corroborated by danubedata.ro 2026 guide
  ("wrap it in HTTP… works inside Vercel Edge Functions").
- Knight Capital anchor (per-instance flags): the audit's standing
  anchor, undisputed.

## 8. Follow-ons (not this RFC)

1. **Engine team:** receiver-side read of `sentinel:kill:v1`
   (self-hosted receiver / `DurableForwarder` poll path). Dated check:
   if no design exists 14 days after this RFC is accepted, the
   externalized flag stays labeled `not_consumed_by_paging_tier` —
   the honest marking moves with the truth.
2. **Implementation lane** (after Petu's review + hand-test window):
   safety_api store backend, 503/501 paths, 409 stale-generation on
   re-arm, fault-injection tests, audit-log append fix.
3. **Console:** render `generation`, `stale_read`, and the
   `external_unreachable` / `not_distributed` scopes (UI team).

## Monkey-first + dated kill condition

The hardest part is not the store — it is §8.1: the paging tier must
read the flag, or this is theater with better infrastructure. Kill
condition: if the engine team has not produced the receiver-side read
design by **2026-10-21**, this RFC's status flips to ACCEPTED-WITH-
RESIDUAL and the console marking stays `NO-OP LEVER (this tier)` plus
`not_consumed_by_paging_tier` — the labeling tracks reality, not the
plan. Hitting this condition is a successful outcome (clean signal),
not a failure.

---

*Lineage: Amazon 6-page memo (written decision before code); Bezos
Type 1/Type 2 (labeled Type 1, given Type 1 process); Kleppmann,
Designing Data-Intensive Applications (fencing tokens); Google SRE
(error budget thinking applied to the free-tier command budget);
principal-governance (unforgiving API: 501/503/409 are contracts, not
messages).*
