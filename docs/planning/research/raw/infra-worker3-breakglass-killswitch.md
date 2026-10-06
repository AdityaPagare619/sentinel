# INFRA WORKER 3 — FINAL REPORT: Break-glass access + kill-switch propagation

## TOPIC 5 — Break-glass access

### OUR STATE
- **Zero mentions of break-glass anywhere in `src/` or `docs/`** — confirmed by repo-wide grep on branch `lane/domain-research-infra`. Confirmed, total gap.
- What we *do* have: dual-attestation policy governance (two humans approve, changes expire, kernel refuses unattested policy generations), a hash-chained `EventLog` with sealed checkpoints (`src/sentinel/eventlog.py`), and an Ed25519 identity/attestation stack (`attestor.py`, with `cryptography==44.0.3` as our one pinned dependency). These are the raw materials a break-glass design can reuse — but there is today **no emergency-access path that bypasses normal auth with audit + time bounds + revocation**.

### REAL WORLD (sourced)
The industry has converged on a strikingly consistent shape:

**1. The role exists but is assigned to nobody; access is just-in-time, requested, approved, and self-expiring.**
- **Teleport Access Requests**: a user requests a privileged role (`tsh login --request-roles="kube-member" --request-reason="..."`); an admin approves via `tctl request approve` (or Slack/Web UI); the user gets roughly an hour; after the window expires a new request is required. Sessions are recorded and audit events are shipped off the target machine to the Auth Server/external storage. (https://goteleport.com/blog/granular-seamless-jit-access-with-teleport/ ; https://goteleport.com/learn/just-in-time-access-for-amazon-eks/)
- **Azure PIM / AWS IAM Identity Center / EKS break-glass**: pre-created emergency role, MFA-gated, short max session duration, mandatory incident ID/ticket/approver, alert on `sts:AssumeRole` for the break-glass role, and **revocation of the access entry + ClusterRoleBinding as part of incident closure** — revocation is a runbook step, not an afterthought. (https://github.com/unpredictableprashant/learningk8s/blob/HEAD/sessions/31-case-studies/subsessions/14-break-glass-admin-access/README.md)
- **Shamir-split credentials**: one documented design mints emergency material only after owner approval, holds it in `tmpfs`/short-lived `ssh-agent` (never disk), defaults to a 60-minute TTL, requires a *new* approval (not silent extension) for longer, and destroys keys/temp files/grant records on cleanup. Hard limit: no role escalation, mandatory post-incident review **within 24 hours**, hash-chained audit verified after every session. (https://github.com/rmednitzer/core-graph/blob/HEAD/docs/operations/break-glass.md)

**2. Two tiers: broker-mediated for almost everything; last-resort for the identity plane itself.**
The clearest articulation (https://github.com/mgoodric/mattgoodrich.com/blob/HEAD/content/posts/break-glass-without-the-backdoor/index.md): *Tier one* is the broker-mediated JIT path (role assigned to nobody, approval or justification + mandatory after-the-fact review, bounded window, recorded session, self-expiring) — "the right path for almost every incident, because in almost every incident the identity plane is fine." *Tier two* is the account that can rebuild the identity plane (root/global-admin): hardware key, secret split across two offline safes so no single person can use it alone, **two people present**, and sign-in alarms to a monitoring path **that does not run through the normal SSO or the normal cloud account** — because those may be what's down. The through-line: *"Any authentication as any break-glass identity, tier one or tier two, pages the security team immediately, because a break-glass account used without an alarm is just a privileged account nobody is watching."*

**3. The hard part — revocation — has a canonical answer: don't trust the credential, trust a server-side check at use time.**
- **AWS**: you *cannot* cancel temporary credentials. They die only at expiry. The documented revocation mechanism is an inline DENY policy keyed on `aws:TokenIssueTime` — *"deny all actions for any credentials issued before <cutoff>"* — i.e., you revoke by changing what the credentials are *allowed to do*, evaluated at request time, plus the console's "Revoke sessions" tab which writes exactly such a policy. (https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_revoke-sessions.html)
- **The general principle**: every privileged action must pass through an enforcement point that consults live server-side state (revocation list / issue-time cutoff). A token that is trusted on signature alone is unrevocable — that's the trap.
- **Session recording as a revocation-adjacent control**: the `postern` SSH bastion refuses to start a session it cannot record — recording is a precondition, not a post-hoc log. (https://github.com/warewave-technology/postern)

**4. Break-glass must produce MORE audit data than normal, under a distinct identity.**
- PAM doctrine: "Break glass, done correctly, produces more audit data than a normal session, not less." (https://www.scworld.com/tech-explainer/privileged-access-management-vaulting-session-control-and-break-glass)
- The `kalitka` pattern binds the approval to the credential: the cert's `key-id` is `kalitka:<session-id>` so the target's auth log ties straight back to the audit trail; `force-command` certs carry the *approved* command so the session can run only what was approved, for the grant's TTL. Expiry is intrinsic — "there is no reconcile/orphan problem at all." (https://github.com/everycore-net/kalitka/blob/HEAD/deploy/ssh/README.md)
- **Anti-backdoor discipline**: quarterly review even if unused; rotate credentials after any use and after personnel changes; alarm on *any* use of the dormant account. (https://gist.github.com/tashiscool/c5e57e0b32bb6c48b4eb562fc4dd845a)

### GAP
We have zero of this. Concretely: no emergency role, no JIT grant mechanism, no TTL, no revocation path, no distinct break-glass audit identity, no use-alarm, no mandatory post-incident review. If the dual-attestation path is ever unavailable (approvers unreachable, attestation service down), the operator's only options today are the standing credentials or nothing — and if they use standing credentials there is no mechanism distinguishing the emergency from routine work, no expiry, and no forced review.

### FIX DIRECTION (₹0, minimum principled design)
Reuse what we own: Ed25519 attestation, dual-attestation approval flow, hash-chained event log.

1. **Create `breakglass` role assigned to nobody.** Standing state = dormant. No standing credentials for it exist.
2. **Grant = short-lived Ed25519-signed token, dual-control.** Request carries: incident ID, exact command/action class (force-command analog — approve the *action*, not a shell), requester identity, approver identity (second human — reuse the dual-attestation machinery). TTL default 15–60 min; longer requires a new grant, never extension.
3. **Solve revocation with a generation counter, not per-token tracking.** Maintain a `breakglass_generation` counter in the control plane. Every grant is stamped with the generation it was born in. **Every privileged action re-checks: signature valid + TTL unexpired + grant generation == current generation.** Revocation = one write bumping the counter — O(1), kills all live sessions instantly, including ones mid-flight. This is the AWS `TokenIssueTime` lesson generalized: the enforcement point consults live server-side state at use time; the token is never trusted alone.
4. **Break-glass identity in the audit log.** All break-glass actions logged as `actor="breakglass:<incident-id>"` with *more* fields than normal actions (what was requested, approved, attempted, denied), into the existing hash-chained event log. Never reuse the normal actor identity.
5. **Alarm out-of-band on every use.** The alarm path must not run through Sentinel itself (Sentinel may be the thing on fire) — page out-of-band, tier-two style.
6. **Mandatory post-incident review within 24h, written to the log.** Non-optional; the grant cannot be re-issued for the same incident ID until the review record exists.
7. **Anti-backdoor rules**: no role escalation from break-glass (cannot grant privileges to others); grant material never touches disk (memory/tmpfs only); drill the path quarterly.

**Adversary's view** (how I'd abuse it): (a) get a grant for a "read-only diagnosis" and use it for writes — defeated by command-class binding; (b) use break-glass for convenience to dodge the approval flow — defeated by the alarm + mandatory review making each use expensive and visible; (c) tamper with the audit log from inside the session — defeated by the hash chain + sealed checkpoints; (d) replay an old grant — defeated by generation check + TTL; (e) attack during clock skew — use the control plane's clock for TTL, not the client's.
**Operator's view** (panicking at 3am): one command to request, approval tap on a phone, grant lands in <2 minutes. The kill switch must remain operable with *zero* auth dependencies — it is the tier-two analog.

---

## TOPIC 6 — Kill-switch propagation to the forwarder

### OUR STATE
- A global kill switch stops ALL suppression (fail-open: everything pages). In-flight races resolve as pages when the switch engages.
- Drill-verified forwarder halt in <5s, 7/7 PASS, measured 2026-10-05 (from the handoff brief; the drill doc on this branch, `docs/drills/drill-2026-10-05.md`, covers a different drill — could not independently re-verify the kill-switch drill numbers here; flagged honestly).
- Open concern: `shadow_mode` + `global_kill_switch` interaction can lose kill-switch attribution — the audit `reason` gets rewritten to `"shadow"`. **Verified in code**: `src/sentinel/shadow.py` hardcodes `reason="shadow"` in the shadow audit disposition (the `_evaluate_storm_digest` path builds `audit_disp` with `reason="shadow"`, discarding the would-be reason), so any kill-switch-caused page flowing through shadow mode is recorded as merely "shadow".
- Forwarder anatomy: `DurableForwarder` with an outbox, `claim_due_rows`, `_attempt`, `_scheduler_loop`, `drain(timeout_s=30)`, `stop(timeout_s=30)`, `send_direct` degraded path, `_secondary_scan` secondary sender, and `enqueue_control_plane_page`. Multiple delivery paths exist — the kill switch must reach *all* of them.

### REAL WORLD (sourced)
**1. Propagation speed: streaming beats polling by an order of magnitude.**
- **LaunchDarkly**: server SDKs hold streaming (SSE) connections; flag changes propagate in **<200ms** to all SDKs in an environment. Their own guidance: "kill-switch speed is the requirement → realtime updates, not a polling tool." (https://launchdarkly.com/how-it-works/platform-architecture/ ; https://launchdarkly.com/how-it-works/feature-flags/ ; https://medium.com/@travisw93/feature-flag-tools-compared-2026-launchdarkly-vs-flagsmith-vs-unleash-vs-configbee-vs-statsig-vs-a19b6f11fe8f)
- Contrast: Statsig's server SDKs poll every **10s** by default — fine for experiments, not for kill switches.

**2. Kill switches stop NEW exposure; in-flight work is a separate, explicit problem.**
- "In-flight jobs are unaffected either way: the flag is evaluated when a job is processed, so work already in the queue drains under the old answer. Neither statement cancels anything already generated." (https://github.com/coderxp1/tugpt-nextjs-recovered/blob/HEAD/docs/controlled-rollout.md)
- The principled fix is **snapshot-once-at-entry**: "Snapshotting once in middleware prevents split-brain behavior inside a request. New requests see the new revision; in-flight requests finish under the old decision **unless a separate cancellation mechanism is deliberately invoked**." A rollback runbook must order actions explicitly: disable new entries → observe traffic by decision revision → allow or cancel in-flight work per the transaction contract. (https://dev.to/hwpgsd503817/boolean-middleware-checks-feature-flag-control-for-checkout-api-routes-17g7)
- This snapshot-revision is exactly the **generation/epoch trick**: every work item carries the generation it was born in; the switch bumps the generation; stale-generation work is dropped (or fenced) at every checkpoint. Used for gate/approval epochs in durable execution (https://github.com/forcewake/forge/blob/HEAD/docs/research/2026-09-13-durable-execution.md) and as fencing tokens generally (https://hackernoon.com/the-fencing-gap-why-your-distributed-lock-isnt-safe-and-how-to-fix-it).

**3. Trading kill switches are asymmetric and hard to re-arm — by design.**
- Exchange kill switch (Nasdaq MRX Options 3 §17): a member's request **cancels all existing orders AND restricts entry of additional orders**; the member **cannot re-enter until a verbal request to Exchange staff** sets a reentry indicator. (https://www.sec.gov/rules/sro/mrx/2019/34-87414-ex5.pdf)
- Zerodha's retail kill switch: trading in a segment can be re-enabled **only 12 hours** after disable. (https://www.financialexpress.com/market/zerodha-kill-switch-making-loss-take-a-break-disable-trading-how-to-use-this-new-katie-feature-2276747/lite/)
- Lesson: the kill direction is instant and one-sided; the re-arm direction is slow, manual, and multi-party. Our switch currently has no documented re-arm discipline.

**4. Graceful drain has a mature shape (Kubernetes).**
Pod termination: endpoint deregistration → `preStop` hook → `SIGTERM` → drain in-flight within `terminationGracePeriodSeconds` (default 30s) → `SIGKILL`. Operators size the grace period against the drain budget explicitly (KServe: 15s preStop + 45s drain = 60s). (https://github.com/abdelfattah-hilmi/portfolio/blob/HEAD/src/pages/blog/zero-downtime-kubernetes-deploys-the-details-nobody-tells-you.md ; https://github.com/kserve/kserve/pull/5485 ; https://github.com/nofireai/ravel/commit/1f5c309a3594113d4fd6a48d607e4b61fc935506)
- Our forwarder's `drain(timeout_s=30)` / `stop(timeout_s=30)` is the same SIGTERM/SIGKILL pair — good shape, but the 30s budget should be justified against a measured drain budget, not left as a round number.

**5. PagerDuty's own pattern for delegated emergency action**: runbook automation with guardrails — responders get pre-vetted procedures, not raw access; every outcome is logged. (https://www.pagerduty.com/assets/ebook-roi-guide-runbook-automation-for-incident-management.pdf)

### GAP
1. **Is <5s good enough?** Against industry: LaunchDarkly <200ms (streaming) vs our <5s — we are ~25× slower than the realtime tier, but in the same tier as exchange kill-switch semantics (seconds to cancel-all + restrict). For our failure direction the 5s costs *wrongly-suppressed pages* (suppressions leaking through post-switch), not wrongly-sent ones. Verdict: 5s is acceptable *if and only if* in-flight races provably resolve fail-open (they do today) — but the number should be tightened toward sub-second on the broadcast path, because the dominant risk isn't the 5s, it's **coverage**: the drill verified the forwarder halt, but the kill switch must reach *every* delivery path — `_secondary_scan`, `send_direct` (degraded path), the storm-digest path, control-plane pages. One unenumerated path = the adversary's tunnel.
2. **The "decision made 1ms before, forwarded 1ms after" race** is currently resolved by timing luck + fail-open default, not by construction. The generation-counter pattern makes it principled: stamp every decision record and outbox row with the kill epoch; check at claim time AND at attempt time; stale epoch → page as fail-open with `reason="kill_switch"`.
3. **Kill-switch attribution dies in shadow mode** (verified in code, shadow.py). A kill-switch page recorded as `reason="shadow"` is indistinguishable from routine shadow traffic — exactly the audit failure the real world warns against.
4. **No re-arm discipline**: nothing requires dual attestation, a cooldown, or a human confirmation to re-arm — the trading world treats re-arm as the dangerous direction.

### FIX DIRECTION (₹0, principled propagation pattern)
1. **Kill epoch counter** (the generation trick): a monotonic `kill_epoch` in the control plane. The kill switch = one atomic increment + broadcast. Every `DecisionRecord` and outbox row carries `kill_epoch_born`. Enforcement points — gate evaluation, forwarder `claim_due_rows`, forwarder `_attempt`, `send_direct`, secondary scan — reject-or-fail-open on `born_epoch < current_epoch`. Fail-open direction: stale-epoch suppression work becomes a page, never a silent drop.
2. **Broadcast, not poll, for the fast path**: in-process pub/sub (or file-watch on the epoch file) so engaged switches wake the forwarder loop in milliseconds, not at the next poll tick. Target: sub-second halt on the broadcast path; the epoch check is the correctness backstop even if broadcast is lost.
3. **Enumerate every delivery path in a registry** and extend the drill: primary forwarder, `_secondary_scan`, `send_direct` degraded path, storm-digest path, `enqueue_control_plane_page`. The drill asserts *each* path halts/drains; any path not in the registry is a finding.
4. **Fix shadow-mode attribution**: stop rewriting `reason`. Keep the causal reason (`kill_switch`, `policy`, …) and add a separate `shadow: true/false` field (or `mode` field). The rule: *the mode of observation must never overwrite the cause of the decision.* Composite display (`kill_switch · shadow`) is fine; lossy rewrite is not.
5. **Re-arm discipline**: re-arming requires dual attestation + a mandatory cooldown + an explicit audit record. Instant one-sided kill, slow multi-party re-arm.
6. **Size the drain budget honestly**: measure worst-case forwarder drain (oldest outbox row age at kill time) and set `drain()` timeout against it with headroom — not a round 30s.
7. **Kill switch must not depend on the identity plane**: like the tier-two break-glass account, the switch actuator (file, local CLI) must work when auth/SSO/approvers are down. A kill switch that needs the systems it protects is theater.

**Adversary's view** (how I'd suppress pages past the kill switch): (a) enqueue to the outbox with a forged fresh epoch — defeated if the epoch is stamped by the control plane, not the producer; (b) use `send_direct`/secondary path that skips the epoch check — defeated by the path registry + drill; (c) flip the switch off again quickly (re-arm race) — defeated by re-arm discipline; (d) engage the switch to cause a page storm as DoS — real dual-use risk; mitigation is alerting on switch use + mandatory review, same as break-glass.
**Operator's view**: one command, works with shaking hands, works when the network is partitioned and the approvers are asleep. If it needs more than that, it won't be there when it's needed.

---

## Cross-cutting notes
- **Unify the epoch trick**: break-glass revocation and kill-switch propagation are the *same pattern* (monotonic generation + check-at-use). Implement one `Generation` primitive; use it twice.
- **Audit principle from the real world, applied to both**: emergency actions must be *more* visible than normal ones, under distinct identities (`breakglass:<incident>`, `reason="kill_switch"`), in the hash-chained log. Anything that makes an emergency action look routine is a defect.
- **Honest gaps I could not verify**: (a) PagerDuty's *internal* emergency-access mechanics; (b) Google Borg/oncall break-glass specifics; (c) incident.io's break-glass product specifics; (d) the 7/7 PASS kill-switch drill numbers come from the handoff brief, not from a doc readable on this branch.

**Deliverable note**: no code was changed (research-only lane, as ordered). The report above is the complete structured writeup, ready to paste into the consolidated doc.
