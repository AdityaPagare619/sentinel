# RFC — Backend observability at ₹0 (Vercel Hobby tier)

**Status:** PROPOSED · **Type:** 2 (reversible — monitor choice is a
vendor pick, not architecture) · **Owner:** Team 3 platform/infra ·
**Date:** 2026-10-07 · **Lane:** `lane/faang-platform-20261007`

**Problem:** nobody would know if prod broke. The shipped tier has
~30-minute Hobby log retention, no uptime monitor, no metrics, and the
old watcher doc described a phantom self-hosted box (struck 2026-10-07;
`ops/watcher-deployment.md` now describes Vercel reality and carries an
arming checklist that blocks the production-live declaration).

This RFC evaluates what is actually possible at ₹0, recommends, and
implements only what is free and real. No phantom boxes.

## 1. What the platform gives us (verified 2026-10-07)

| Capability | Hobby reality | Source |
|---|---|---|
| Runtime logs | ~30 min retention, dashboard/CLI only | audit infra §4; live behavior |
| Log drains | **Pro/Enterprise only** ($0.50/GB) — NOT available | Vercel docs via 2026 practitioner guides (josechifflet drains.md; pi-harness observability guide) |
| Metrics/traces | No free export path | same |
| Cron | Available, but a cron in the watched project shares its fate domain | watcher-deployment.md "one rule" |
| Instant Rollback | Previous deployment only; env vars not rolled back | REPEATABLE-DEPLOY.md §7 |

Conclusion: Vercel will not tell us when it is down, and will not keep
the evidence. The watcher must live outside Vercel entirely.

## 2. Options evaluated

### A. UptimeRobot free — RECOMMENDED

Free plan: 50 monitors, 5-minute checks, email + phone-app alerts, no
card required (verified 2026-10-07: buildbyravirai 2026 guide,
ecommerceparadise 2026 comparison, UptimeRobot's own knowledge hub).
SMS/voice are paid extras; the app notification is free and fast.

- 4 monitors, mirroring `ops/watcher-deployment.md`'s check table:
  1. `GET /api/v1/health/live` → 200 (liveness, unauthenticated).
  2. `GET /api/v1/ops/health` without bearer → 401 (auth fail-closed).
  3. Keyword check on the prod console HTML for `data-mode="production"`
     (catches a mislabeled deploy — the X-H class of failure).
  4. TLS/SSL expiry alert on the backend hostname (built into
     UptimeRobot's monitor config).
- Detection latency: ≤5 min + recheck. For a pre-revenue demo tier with
  no paging path, 5 minutes is honest and proportionate. (If the tier
  ever pages real humans, revisit: the cheapest paid tier drops to
  1-minute checks — a money decision, not today's.)
- Alert path: Aditya's phone app + email. The alerts never traverse
  Sentinel (watcher-deployment.md: "a watcher whose alerts route through
  Sentinel is a sentence, not a system").
- Account action: **Aditya's** — he creates the account with an address
  he reads, installs the app, pastes the 4 monitors. No lane can do
  this for him (no shared credentials, ever).

### B. Better Stack free — credible alternative

Free: 10 monitors, 3-minute checks, heartbeats, 1 status page, email +
app alerts (verified 2026-10-07: Better Stack's own comparison page,
ecommerceparadise 2026). Pick this if Aditya wants the dead-man
detection on the watcher itself (heartbeats) or a public status page
for free. 10 monitors is plenty (we need 4). The tradeoff vs A is
fewer monitors for faster checks + heartbeats.

**The RFC does not pick between A and B unilaterally** — the account
is Aditya's; either satisfies the arming checklist. Recommendation: A
for simplicity (50-monitor headroom, no-brainer config), B if he wants
heartbeats/status page. Both are ₹0.

### C. Log drain — REJECTED (not available at ₹0)

Vercel drains require Pro/Enterprise. There is no free drain. Stating
this plainly so no future doc claims otherwise.

### D. Direct-ship logs from runtime (Axiom free tier) — DEFERRED

Practitioner pattern (sarathfrancis90/penny, 2026-04): fire-and-forget
`fetch` from the function to Axiom's ingest endpoint — works on Hobby,
bypasses the drain restriction. Deferred, not rejected: it needs (a) an
Axiom account (Aditya's action), (b) a runtime code change in
`index-prod.py` (new egress path on the safety tier — needs security
review), and (c) a redeploy (prohibited during the hand-test window).
This is a real option for the post-hand-test hardening pass, with the
honest cost noted: one extra HTTP POST per request, fail-silent on
error, and the Axiom token becomes another Vercel env secret to manage.

### E. Scheduled `vercel logs` pull from the box — REJECTED as a solution,
### accepted as a stopgap

A cron pulling `vercel logs` every ~20 min beats the 30-min retention
window, but it is toil (manual, repetitive, O(n)-scaling — Google SRE's
definition), it shares the box's fate domain for the *puller* (if the
box dies, the pulls stop silently), and it produces logs nobody reads.
Accepted only as a stopgap until A/B lands, and the stopgap must be
labeled as such, not as "monitoring."

### F. Self-hosted Uptime Kuma on the box — REJECTED

Free software, but it needs a host the team babysits, and it reintroduces
the fate-domain problem the "one rule" forbids (box watches Vercel is
fine for Vercel outages; box-down means watcher-down with no dead-man
signal unless heartbeats are added — at which point just use B).

## 3. Recommendation

1. **Aditya picks A or B** (account + 4 monitors + phone-app alerts).
   Until he does, the tier is unwatched — stated, not hidden
   (watcher-deployment.md arming checklist stays RED).
2. **This lane ships** `scripts/ops/tier-probe.sh`: the executable spec
   of the monitor config — the exact 4 unauthenticated checks, runnable
   from any box, exit 0/1. The monitor config mirrors it; drift between
   the doc, the script, and the dashboard config is a bug.
3. **No new dashboard.** The minimal health dashboard already exists:
   `GET /api/v1/ops/health` + the console's Ops Health surface. A
   second dashboard is gold-plating. What was missing was someone
   *watching* — the monitor fixes that, not another chart.
4. **Log retention stays unsolved at ₹0** until option D (or a plan
   change). The RFC says so explicitly: the 30-min window means
   post-incident forensics older than 30 minutes rely on the deploy
   records and the audit log, not on Vercel logs. Do not promise
   otherwise in any incident doc.

## 4. Pre-mortem — top 3

1. **The monitor pages; nobody configured the phone app; the alert
   rots in an unread inbox.** Cause: account setup done halfway.
   Mitigation: the arming checklist requires a DOWN-path drill
   (bad bundle to a preview, watch the page arrive) before any
   "watcher armed" box is ticked.
2. **Keyword check false-greens**: UptimeRobot keyword checks don't
   execute JS (verified 2026-10-07 via Better Stack's comparison) —
   our keyword (`data-mode="production"`) is in the static HTML, so
   this is safe *for this check*. Any future check on JS-rendered
   content must not use keyword monitoring. Stated here so the next
   operator doesn't learn it the hard way.
3. **5-minute checks miss a 3-minute outage; the team claims "no
   downtime" from monitor data.** Cause: confusing the instrument with
   the truth. Mitigation: the deploy records + audit log are the
   downtime source of truth; the monitor is the paging source. Never
   compute an SLA from 5-minute checks.

## 5. Verification receipts

- UptimeRobot free (50 monitors / 5-min / email+app, no card):
  buildbyravirai.com 2026 guide + ecommerceparadise 2026 comparison +
  uptimerobot.com knowledge hub — three-way agreement.
- Better Stack free (10 monitors / 3-min / heartbeats / status page):
  betterstack.com comparison (2026) + ecommerceparadise 2026.
- Vercel drains Pro/Enterprise-only ($0.50/GB): josechifflet
  dev-dotfiles drains.md + pi-harness observability guide (both 2026,
  both cite Vercel docs).
- UptimeRobot keyword checks don't run JS: betterstack.com comparison
  (2026), stated as a G2-corroborated limitation.
- Axiom direct-ship pattern on Hobby: sarathfrancis90/penny commit
  8dcf04b8 (2026-04-17), noted as deferred-with-reasons, not adopted.

## 6. What this RFC implements

`s scripts/ops/tier-probe.sh` — the 4 unauthenticated checks, exit
0/1, no token required, no state. It does NOT create a cron job, a
GitHub workflow, or any persistent watcher: the standing order is
local gates only, and enabling a schedule is the coordinator's /
Aditya's decision, not a lane's. The script is the spec the monitor
config mirrors.

---

*Lineage: Google SRE ("hope is not a strategy"; every page actionable;
median paging near zero); the watcher-deployment.md "one rule" (never
share a fate domain); execution-doctrine (the 10-line cron beats the
cluster — here, the 4-check script beats the phantom box).*
