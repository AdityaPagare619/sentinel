# ENGINE DOMAIN RESEARCH — Pipeline & Safety Architecture

**Lane:** domain-research-engine · **Branch:** `lane/domain-research-engine`
**Date:** 2026-10-06 IST · **Type:** research, not building
**Standing lens (Aditya, non-negotiable):** Jev is used very normally/casually — the CONTROL sits across the entire architecture. The deterministic gate owns every decision; Jev only advises. Every mechanism below is judged against this: does it keep control in the architecture, or let the AI run blind?

**Sources read first:** `docs/architecture-revision/PIPELINE-REVISION.md` (ADR, R-1..R-20), the deterministic-gate / event-log-audit / forwarder-byok / race-jev-boundary domain memos, `docs/planning/OPERATING-RULES.md`, standing safety laws (uncertainty/stale/overload/vendor-errors/timeouts → page; suppression needs independent deterministic locks; UI never recomputes decisions).

**Method:** web-first. For each topic: what WE do → what the REAL WORLD does (sourced) → the gap → the principled recommendation. Every claim carries a URL or doc name. Theater is named as theater.

---

## 1. Policy governance / attestation — who binds the governed object to the executed object

### What WE do
B3 six-state lifecycle (draft → shadow → canary → live → review-due → frozen), Ed25519 dual attestation (2 distinct human attestors, distinct from the author, binding a preview hash), content freeze on live, automatic expiry, watchdog freeze/unfreeze (unfreeze needs 2 attestors). **The damning finding (R-1):** governance theater — the decision kernel actually reads its numbers from an unattested `thresholds.json`; the attested policy object governs nothing the kernel reads. **Type-1 fix decided:** attested `PolicyVersion.content` becomes the canonical thresholds; the kernel refuses unattested generations.

### What the REAL WORLD does
- **OPA's signed bundles — the exact fix, industry-standard.** A signed bundle carries `.signatures.json`, a JWT recording the SHA hash of every file (openpolicyagent.org/docs/management-bundles): "When OPA receives a new bundle, it checks that it has been properly signed using a (public) key that OPA has been configured with out-of-band. **Only if that verification succeeds does OPA activate the new bundle**; otherwise, OPA continues using its existing bundle and reports an activation failure via the status API and error logging." Three things: verification happens **in the executor at activation time**, not in a sidecar; failure is fail-closed (keep serving last good); failure is **loud** via the status API. Our Type-1 fix is validated as the industry pattern — hold the line on it.
- **Meta's Gatekeeper lesson, learned the hard way.** A public commit in facebook/facebook-ios-sdk (cd100a067a50a30e6006b5fb2d956863d37fb135), titled "Make the GateKeeper a real kill switch for inbound_url": the gate existed but wasn't actually killing anything; the fix gates **at the sink (the reads), not the call sites** — "a kill switch that leaves one of the three data types flowing is not much of one." A gate placed anywhere other than where the effect happens is nominal. (Meta's internal Gatekeeper otherwise: server-side feature gating with targeting by country/age/data-center and gradual rollout — techcrunch.com/2011/05/30/facebook-source-code.)
- **Google SRE config discipline** (sre.google/workbook/configuration-specifics/): config in version control, strict review, historical record of who changed what, easy/reliable rollback; hermetic evaluation of configuration for rollbacks and replayability. SRE book Ch. 8: config changes are "a potential source of instability"; all schemes store config in source control with strict review. SRE's answer to "who binds governed to executed": **the change pipeline itself** — you cannot bypass the reviewed artifact to reach production.
- **Expiry done right: The Update Framework (TUF)** (github.com/theupdateframework/specification/blob/master/tuf-spec.md): every signed metadata role carries an `expires` timestamp; the client's freeze-attack check: "If the new targets metadata file is expired, discard it, abort the update cycle, and report the potential freeze attack." Rollback blocked by version monotonicity. Anything missing, expired, mis-versioned, or insufficiently signed throws — "there is no best effort path." Recommended max lifetimes: timestamp 24h, snapshot 7d, targets 30d, root 366d. The design point: **expiry is enforced by the consumer reading the signed envelope**, not by an external scheduler.
- **Per-decision policy-generation attribution: Styra** (docs.styra.com/das/observability-and-audit/decision-logs/overview): the decision log records per decision `path`, `input`, `result`, `allowed`, `reason`, and crucially **`bundles` — the identifier of the policy version used to make the decision** — plus `decision_id`, `agent_id`, `timestamp`. Every decision carries its policy-generation identity.
- **Two-person rule, the honest version.** Maker-checker: originator + verifier, both user IDs logged. LaunchDarkly productized it for production flags ("Configuring approvals for an environment" — "the flag… state does not change until a reviewer approves and applies the change"). But the sharp warning: "a holder of [one] role can satisfy a four-eyes rule alone. The rule that matters most is… **the decider must differ from the requester**." Four-eyes on self-declared names in one application role is theater; distinctness must be enforced by **key-bound identities**, verified by a component the requester doesn't control.

### The gap
Our Type-1 fix is the right surgery and industry-validated. But governance is still incomplete in four places:
1. **Activation-failure semantics undefined.** OPA keeps serving the last good bundle and alarms. What does our kernel do at boot/reload if no valid attested generation exists — refuse to start, serve stale, or (worst) fall back to unattested? Undefined.
2. **Expiry enforcement location.** If expiry is enforced by an external watchdog/cron flipping state rather than by the kernel reading the signed envelope (TUF pattern), that's the same govern/execute split as the `thresholds.json` failure, one layer up.
3. **Rollback protection.** OPA/TUF never activate an older generation. Our watchdog freeze/unfreeze must not resurrect a stale generation on unfreeze — unfreeze must re-verify the *current* generation.
4. **Per-decision policy-generation attribution missing.** Without Styra's `bundles`-style citation per decision, we can't prove after the fact which attested generation produced a suppression.

### Principled recommendation
1. **The attested bytes must be the loaded bytes — verify at activation, in the executor, fail closed.** No sidecar, no watchdog, no second file between the attested artifact and the kernel.
2. **Refusal must be loud and observable** — the OPA status-API equivalent: unattested/expired generation raises an activation failure wired to alerting, never silent degradation.
3. **Expiry lives inside the signed envelope and is enforced by the consumer.** Expiry computed outside the attestation is a suggestion, not a control.
4. **Generation identity rides on every decision.** Audit logs cite the policy generation hash per decision, or the attestation is forensic theater.
5. **Two-person means key-bound distinctness.** Attestors are Ed25519 public keys; author key ≠ attestor keys; distinctness checked at verification time in the executor — never self-declared role names.

### Honest gaps (child 1/3)
- Meta's internal Gatekeeper approval workflow is proprietary — only targeting mechanics and the "real kill switch" lesson are public.
- No vendor's published latency-measurement methodology for flag propagation claims (LaunchDarkly's 200ms is vendor-stated).
- SRE book "Configuration Design and Best Practices" details beyond the workbook page come from secondary summaries; treat as cited, not exhaustively verified.

---

## 2. Kill-switch propagation with PROOF — who proves the halt, and how

### What WE do
A `global_kill_switch` flag claimed to halt all suppression in <5s, with a 7/7 drill PASS. **The damning finding (R-10):** the flag config had **zero readers in production code** — the claim was fictional; the drill ran against a mock path. Fix direction: wire the real path or retract the claim. **Known open gap:** `shadow_mode` + kill switch can LOSE kill-switch attribution by rewriting the audit reason to `"shadow"`.

### What the REAL WORLD does
- **Exchange kill switches — the gold standard for proof + attribution, codified in SEC filings.** Nasdaq's options-exchange rules (SR-MRX-2021-10, sec.gov/rules/sro/mrx/2021/34-93004-ex5.pdf; SR-BX-2020-015, sec.gov/file/exhibit-5-5222): a kill switch is a message to the exchange that (1) cancels ALL open orders and (2) prevents entry of new orders/quotes via the affected identifier. Four properties: (1) **the executing system confirms the kill** — "The System will send an automated message to the Member when a Kill Switch request has been processed by the Exchange's System." The kill is proven by the system that did the killing, not the requester asserting it. (2) **Attribution with counts** — the notification includes "the total number of orders cancelled and remaining open" — who, what scope, what effect, in one message. (3) **The kill is sticky — no self-revive** — "The Member will be unable to enter additional orders until the Member has made a verbal request to the Exchange and Exchange staff has set a reentry indicator." Re-enable requires a separate, human, out-of-band action. (4) **Third parties are notified** — the clearing member is told of both the kill and the reentry.
- **LaunchDarkly's propagation model** (launchdarkly.com/blog/what-are-feature-flags/): flag changes stream to SDKs "within 200ms" via persistent streaming connections (SSE), not polling — the stream design "reduces the change latency to a few milliseconds and removes the load caused by continual polling." Every change in an audit log: who, what, when.
- **Circuit breakers emit their own state transitions as events.** Resilience4j: STATE_TRANSITION events plus metrics (openings, half-open attempts, recovery time). Community guidance: "A Circuit Breaker without observability is difficult to operate. Without monitoring, you may know that requests are failing — but not know why the Circuit Breaker opened." The guard's state changes are first-class observable events, not inferred from downstream behavior.
- **Chaos engineering drill discipline** (community sources — caveat noted): game days define a steady-state hypothesis first ("when the kill switch is flipped, no calls go to the recs service"), verify rollback in staging before production, run with abort conditions tied to error budgets, re-test quarterly because "a failover that works in an empty staging environment routinely fails under production load" — including an explicit "Use chaos to verify a kill switch" workflow: confirm the switch was *actually flipped*, confirm the fallback works, document, schedule quarterly re-test.

### The gap
1. **Our drill is fictional by the exchange standard.** 7/7 PASS against a mock path, with zero production readers, is exactly what the exchange rules prohibit by design: **the executing system must be the thing that confirms.**
2. **Attribution is mutable.** Our known gap — `shadow_mode` rewriting kill-switch attribution — is the one thing the exchange pattern forbids: the kill confirmation is its own event with its own counts, written at the kill layer. A downstream mode must never get to edit it.
3. **Re-entry is not sticky-by-design.** The exchange requires a separate out-of-band human action to re-enable. Our watchdog unfreeze needs 2 attestors (consistent), but the kill-switch flag itself needs the same stickiness: whoever flips it must not silently un-flip it.
4. **"Halt in <5s" is asserted, not measured.** No decision-log measurement = no claim. The drill must timestamp flip → timestamp the kernel's decision stream stops suppressing, computed from audit/decision logs.

### Principled recommendation
1. **A kill switch is proven only by a drill on the real path.** The drill flips the real flag and observes the kernel's real decision stream stop suppressing; propagation latency = (kernel-observed halt − flag-flip), read from decision-log timestamps, recorded per drill run. A drill on any other path is theater.
2. **The executor confirms the kill with an automated acknowledgment event** — who flipped it, when, scope, effect counts (suppressions halted, decisions overridden) — modeled on the exchange's automated message. The requester never self-certifies.
3. **Kill attribution is a first-class immutable audit event written at the kill layer.** Downstream stages (shadow mode included) may add context but can never rewrite the kill event. Attribution flows *through* the pipeline, not around it.
4. **The kill is sticky: re-enable is a separate, authorized ceremony** (dual attestation, consistent with watchdog unfreeze), never the same code path as the trigger; re-enable is audited with the same rigor.
5. **Under the control principle: the AI never gets a vote.** Jev advises on whether suppression is warranted; the kill switch is a deterministic gate above Jev's reach — Jev cannot flip it, delay it, or reinterpret it, and a "Jev says keep suppressing" signal must never override or race the kill.

### Honest gaps (child 1/3)
- No vendor's published, primary-source kill-switch drill procedure with a stated latency-measurement methodology found; LaunchDarkly's 200ms is a marketing claim.
- Chaos-engineering kill-switch verification workflow comes from community repos, not Gremlin/Litmus/AWS FIS official docs.
- Exchange kill switches cancel resting orders (trading domain); the analogy transfers on confirmation/attribution/stickiness mechanics, not domain semantics.
- Could not verify from a primary source whether LaunchDarkly's audit log is tamper-evident or merely append-style.

---

## 3. Tamper-evident hash-chained logs at scale — production, not papers

### What WE do
`row_hash = sha256(canonical(envelope-without-hashes + body) || prev_hash)`, genesis seed the constant `"GENESIS"`. An hourly CheckpointJob signs `(head_seq, head_hash, event_count)` and writes the checkpoint **back into the log itself**. Retention tiers seal-and-roll old segments with HMAC'd manifests. **Known gaps:** the CheckpointJob, the crash-recovery Reaper, and the RetentionJob are unit-tested and **never invoked in production** — the hourly seal, the crash-window closer, and the retention policy are prose. Two verifiers disagree on the genesis rule (will cry wolf after the first log roll). The roll can entomb live outbox rows, silently breaking delivery guarantees. "Tamper-evident" covers external attackers only — writer forgery/silence unaddressed.

### What the REAL WORLD does
- **Certificate Transparency (RFC 6962) — the reference design at internet scale.** Append-only Merkle trees; the anchoring artifact is the **Signed Tree Head (STH)** — (tree size, Merkle root, timestamp) signed by the log's key. Split-brain defense against a malicious log operator: **gossip** — clients share STHs; two STHs with the same tree size but different roots is proof of misbehavior (IETF RFC 6962; CertLedger attack analysis, `arxiv.org/pdf/1806.03914`). Three architectural facts for us: (1) **checkpoints are external artifacts, not log rows** — STHs are published out-of-band and gossiped; the log never signs statements about itself *inside* itself; (2) anchoring is periodic + externally verifiable (Trillian's Log Signer "periodically wakes up and sequences queued entries into the Merkle tree, producing a new Signed Tree Head" — `github.com/google/trillian`, "used in production by multiple organizations"; production logs hold **hundreds of millions of entries** — `github.com/google/certificate-transparency-go/blob/HEAD/trillian/docs/Operation.md`); (3) **the writer is untrusted by design** — trust comes from the audience's cross-checking, not the operator's promises.
- **Sigstore Rekor — the modern gold standard.** Append-only Merkle transparency log on Trillian/Tessera, signed checkpoints, signed entry timestamps, inclusion/consistency proofs; Rekor v2 GA 2025, tile-based (`github.com/formspec-org/trellis/blob/HEAD/thoughts/research/2026-04-10-unified-ledger-technology-survey.md`). Production pattern: use as an *L1 anchor* with synchronous 3s timeout and **graceful degradation** — "Rekor being slow or down silently drops the anchor; trace submission never blocks on Sigstore availability" (`github.com/garl-protocol/garl/commit/1f62ce92fdb1ff7aed9ee3520b9bee715cc4eec4`); witnesses learn only an opaque hash, never the payload (`github.com/techblaze-au/idprova/blob/HEAD/docs/adr/0011-rekor-transparency-anchor.md`).
- **AWS CloudTrail — the closest analog to our exact design, at fleet scale.** Hourly **digest files**, *not* in-log checkpoints: each contains SHA-256 of every log file delivered that hour, is **signed with a rotating published RSA key**, and *contains the hash of the previous digest* — a chain of digests in a **separate folder** from the log files. Validation walks the chain; any missing/changed file or skipped digest is a validation failure ("proves nothing was silently removed from the sequence"). Plus: separate log-archive account, S3 Object Lock (WORM), MFA delete, bucket policy denying writes except from the CloudTrail service principal (`docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-log-file-validation-intro.html`; `criticalcloud.ai/blog/aws/9-best-practices-for-aws-cloudtrail-compliance/`).
- **Azure SQL Ledger** (`learn.microsoft.com/vi-vn/sql/relational-databases/security/ledger/ledger-overview?view=azuresqldb-current`): row versions Merkle-hashed per transaction, transactions into blocks, blocks chained by prev-block hash; root "database digests" **periodically generated and stored OUTSIDE the database** — Blob Storage with immutability policies, Azure Confidential Ledger, or on-prem WORM — verified by recompute-and-compare.
- **AWS QLDB — and why it's dead** (announced July 2024, support ended July 31, 2025): centralized trusted-entity ledger, immutable journal, per-entry hash chains (`thestack.technology/aws-deprecations-services-codecommit/`; `techcommunity.microsoft.com/blog/azuresqlblog/moving-from-amazon-quantum-ledger-database-qldb-to-ledger-in-azure-sql/4246237`). What broke: neither fish nor fowl — as a *database* weak (no ORDER BY/LIMIT, no conditional writes, 5 indices per table, indices only on empty tables, "unknown performance at scale" — `Dev.To/aws-heroes/why-we-didn-t-choose-qldb-for-a-healthcare-app-2ggj`); as a *trust system* the worst of both worlds (centralized trust at blockchain-like prices, proving things back to AWS itself). AWS's own replacement guidance migrates users to Aurora PostgreSQL *which lacks cryptographic verifiability* — the market didn't value the property enough to justify the product. Microsoft's lesson: the survivors build ledger features *into general-purpose databases* (Azure SQL ledger), not standalone exotic services.

### The gap
| Real world | Our design |
|---|---|
| **No one writes the checkpoint back into the log.** CT: STHs out-of-band + gossiped. CloudTrail: digest files in a *separate folder*. Azure: digests *outside the database* in WORM. A chain that verifies itself by itself is a known smell. | CheckpointJob writes the checkpoint **into the log itself** — a self-attesting log. |
| Anchoring is periodic, signed, externally published — the audience cross-checks. | Hourly checkpoint exists on paper only: the jobs **never run in production**. The seals don't run. The chain is prose. |
| Writer forgery handled by *separation of powers*: separate archive account, WORM storage, third-party anchoring, key rotation with forward secrecy. | Writer forgery and writer silence **unaddressed** — same key, same process, same trust domain writes and seals. |
| Retention via *digest continuity*: CloudTrail "backfill digest files" form a separate validation chain; old digests stay valid anchors after old logs roll off. | The roll "can entomb live outbox rows, silently breaking delivery guarantees" — retention designed without proving chain continuity across the roll. |
| Genesis is spec discipline: RFC 6962 pins the tree-hash algorithm exactly — one canonical rule. | Two verifiers **disagree on the genesis rule** — will "cry wolf after the first log roll." The genesis constant is lore, not law. |

### Principled recommendation
1. **Stop writing the checkpoint into the log.** A log that attests to itself is theater. Checkpoints become signed external artifacts (separate store/WORM), published out-of-band, verifiable without trusting the writer. CloudTrail's shape: per-hour signed digests, chained to the previous digest, stored separately. This also makes retention survivable: old *digests* stay valid forever after old *rows* roll off.
2. **The CheckpointJob must run or the property must be deleted from every claim.** An uninvoked sealer is not a security feature; it's a story told in unit tests. If it can't run hourly in production, the chain is *detection-only* — document and verify it as such, never sell it as prevention.
3. **Writer-forgery needs separation of powers, not a stronger hash.** The hash chain detects *external* tampering. A compromised writer forges a perfect chain. Real-world answer: writer writes; an *independent* process (different account, different credentials, ideally different trust domain) signs the digest; anchors go to WORM or a public transparency log. One key, one process, one box = one point of forgery, no matter how many SHA-256 rounds you stack.
4. **Genesis is a spec, not a constant.** Pin the genesis rule in one normative document both verifiers implement against — including rollover behavior, what `event_count` means across segments, and how a verifier proves continuity across a sealed segment boundary.
5. **Never outsource the ledger.** QLDB's death is the warning: own the hash chain in our own store (or DB-native ledger features), not a managed exotic service that can be sunset.

### Honest gaps (child 2/3)
- No published AWS engineering post-mortem on QLDB's retirement — "the market didn't value it" is informed inference from the field report + AWS's own migration guidance, not a cited AWS statement.
- Exact CT log write-throughput numbers / STH signing intervals not verified from an official source in the time available.

---

## 4. Fail-open vs fail-closed semantics — what the industry refuses to automate

### What WE do
Standing safety law: uncertainty, stale data, overload, vendor errors, and timeouts ALL fall toward paging a human. Suppression needs independent deterministic locks; it can never rest on doubt. **Known open gap:** the disposition vocabulary cannot express fail-open-with-digest (`digest:true + error_code`) — MUST-RESOLVE-BEFORE-BUILD.

### What the REAL WORLD does
- **Google SRE doctrine: three verbs, and one banned for hope.** A monitoring system has exactly three outputs — **Page** (a human must act *now*), **Ticket** (a human must act *within days*), **Log** (nobody looks now). "If it's important enough to disturb a human, it should either require immediate action (page) or be treated as a bug." Email alerting is "the moral equivalent of piping them to /dev/null... an attractive nuisance." Target: **max 2 pages per 12-hour shift**. "Every page should be actionable." Monitoring "should never require a human to interpret any part of the alerting domain" — classification is the system's job, not the human's at 3 AM (SRE book monitoring chapters, corroborated via `danluu.com/google-sre-book/` and lecture notes citing Ch. 6 / App. B).
- **SRE Workbook "Alerting on SLOs":** multi-window, multi-burn-rate alerts — fast burn (14.4× over 1h∧5m → **page**) vs slow burn (6×/1× over 6h∧30m → **ticket**) (`sre.google/workbook/alerting-on-slos/`). The principle: **the alert fires on user-visible symptoms (SLO burn), never on the system's uncertainty about itself.** The two-window AND-gate is a *deterministic* confirmation rule ("the burn is real AND still happening") — a lock, not a model judgment. Page on the *symptom*, ticket on the *cause*.
- **The dead-man's switch — "who watches the watchers."** Prometheus/Alertmanager's always-firing **Watchdog** alert (`expr: vector(1)`, fires constantly); an **external** receiver (off-host) raises the alarm when the heartbeat *stops* — the silence is the signal. The watcher's failure **escalates to a human through an independent path** — never to silence, never back through the sick component itself (`github.com/pulseinnovations/prometheus-deadmansswitch/blob/HEAD/README.md`; `github.com/prometheus/alertmanager/discussions/3227`; `github.com/metacraft-labs/nixos-modules/commit/33abcc744b4ae4e6be786affa06eb77c443359a5`).
- **PagerDuty: documented fail-open AND documented fail-closed trap.** Escalation automatically moves to the next level on no-ack, and Repeat rules cycle the whole policy up to 9 times so "no incidents fall through the cracks" (`community.pagerduty.com/pagerduty-user-onboarding-13/notifications-making-your-escalation-policies-meaningful-402`). But the same article documents the trap: **"incidents will not be created if nobody is on-call in an escalation policy"** — an event in a schedule gap creates *nothing*, notifies *nobody*. And: "The PagerDuty Events API does not fail when an invalid Integration Key is provided" — it returns 202 for a key that routes nowhere (`mongodb.com/docs/ops-manager/current/tutorial/pagerduty-integration/`). Control-principle translation: **a 202 is not delivery.**
- **Aviation doctrine** (`sassofia.com/wp-content/uploads/2025/05/Managing-Aviation-System-Safety-A-Sofema-Aviation-Services-White-Paper.pdf`): fail-safe defaults to stable states allowing time for human intervention; degraded modes reduce automation's claims while preserving control authority; "humans remain the final line of defense." The AF447 caution in the other direction: fail-open-to-human fails if the human gets no situational awareness (`strategic-risk-global.com/esg-risks/the-hidden-risks-of-highly-automated-systems/1392511.article`).
- **"Page, but as a digest" — already a solved pattern.** (1) **Alertmanager notification grouping**: `group_by` batches near-simultaneous firings; `group_wait` (30s) collects the first burst; `group_interval` (5m) batches updates; `repeat_interval` (3–12h) re-notifies at digest cadence (`github.com/prometheus/alertmanager/blob/main/README.md`; `docs.aws.amazon.com/prometheus/latest/userguide/AMP-alertmanager-pagerduty-configure-alertmanager.html`). (2) **PagerDuty urgency rules**: incidents at *low urgency* (non-escalating; email/SMS/push, no wake-up) vs *high urgency* (full escalation), with support-hours rules raising unacknowledged low-urgency incidents when business hours start (`dev.to/pdcommunity/better-sleep-with-pagerduty-dynamic-notifications-and-support-hours-4jkp`). That is exactly "notify quietly, get louder if still unhandled."
- **What the industry refuses to automate:** whether a human is needed at all. SRE: routing (page/ticket/log) is deterministic and pre-registered; suppressing a page requires explicit human acknowledgment — nothing auto-resolves into silence. PagerDuty: an unacknowledged incident never stops escalating. Alertmanager: inhibition rules are explicit auditable config — and the Watchdog is deliberately made **inhibit-immune** (the liveness signal can never be suppressed by the thing it watches).

### The gap
| Real world | Our design |
|---|---|
| Fail-open is *structural*: escalation repeats, silence is the alarm (dead-man's switch), schedule gaps are a named defect class with a fix. | The law is stated but the vocabulary **cannot express** `digest:true + error_code` — the law has no sentence to speak in. |
| Digest semantics are first-class primitives (Alertmanager grouping + repeat cadence; PD low-urgency incidents). | No digest tier — only page vs suppress. Anything not worth a 3 AM wake-up has nowhere to go except suppression, which the law rightly distrusts. |
| Suppression is always explicit, human-owned, auditable (acknowledgment; inhibit rules in config; Watchdog inhibit-immune). | Locks aren't enumerated as config the way inhibit rules are; no liveness/Watchdog analog protects the suppression path itself. |
| Nobody trusts the send: delivery is a confirmed state, not an HTTP code. | Outbox + delivery confirmation is the right instinct; the roll-entombment bug is where it breaks. |

### Principled recommendation
1. **Steal the three-verb vocabulary and add the fourth.** Page (human now) / Ticket (human soon) / **Digest** (human in the next batch — batched, non-escalating, auto-escalates to page if unacknowledged by the digest window) / Log (nobody now). The digest is NOT "suppression-lite": it's a delivery mode with its own deterministic escalation — PD's low-urgency → raised-at-support-hours, Alertmanager's grouping cadence. This resolves `digest:true + error_code` without touching the fail-open law.
2. **Suppression locks must be config, not judgment.** Enumerate every suppression lock as explicit, auditable, versioned rules (Alertmanager `inhibit_rules` style) — and make the engine's own liveness signal inhibit-immune. Nothing suppresses the Watchdog.
3. **Add the dead-man's switch.** The delivery path needs an always-firing heartbeat to an *independent* receiver; silence pages. The only honest way to claim the fail-open law holds when the engine itself is sick.
4. **The disposition decision stays deterministic.** The SRE two-window AND-gate is the template: Jev may advise, but the gate fires on pre-registered deterministic conditions (symptom + confirmation window), and every suppression requires an explicit lock match. The AI's opinion is an input to the evidence bundle, never a vote on the disposition.
5. **A 202 is not delivery.** Any vendor call whose acceptance doesn't prove routing must be verified end-to-end — validate keys at configuration time, confirm receipt at runtime, treat unconfirmed sends as failures that escalate.

### Honest gaps (child 2/3)
- PagerDuty schedule-gap behavior verified from a PagerDuty-hosted community article (~2 years old); UI wording may have changed.
- No public postmortem of a *fail-closed-to-silence* paging outage with attribution found in the time available; PD's schedule-gap docs + Events API 202 behavior are the closest documented cases.
- SRE book quotes corroborated across multiple secondary sources; the official `sre.google` page not opened directly.

---

## 5. Shadow-mode evaluation without attribution loss

### What WE do
Jev races a bounded timer per problem. If the timer wins (Jev too slow/errored/down), the problem pages a human and Jev's LATE answer is recorded as a `shadow_decision` event — persisted for calibration, but it must never page, never suppress, never re-open the decision. **Known open gap:** `shadow_mode` + `global_kill_switch` can LOSE kill-switch attribution by rewriting the audit reason to `"shadow"` — the shadow corrupts the audit trail it was meant to enrich. Shadow calibration itself is unmeasured.

### What the REAL WORLD does
The industry's uniform rule: **the shadow never touches the decision record.**
- **Uber's ML shadow deployments** (https://www.uber.com/us/en/blog/raising-the-bar-on-ml-model-deployment-safety/): "the candidate model runs in parallel with production, processes identical live inputs, and logs outputs for real-time comparison — **without affecting user-facing predictions**." Two shadow modes (endpoint shadowing, deployment shadow), shadow coverage tracked as a platform safety metric across 75%+ of critical use cases. Shadow output goes to a comparison store / observability surface ("Hue") — its own namespace, never the serving path.
- **Twitter's Diffy** (https://github.com/twitter-archive/diffy — the canonical shadow-traffic tool): multicasts requests to candidate + primary + *secondary* instances and compares. The N+1 trick is the attribution-discipline lesson: a second copy of known-good code exists purely to measure non-deterministic noise, and Diffy computes a diff-of-diffs (candidate-vs-primary minus primary-vs-secondary). The shadow's output is a **comparative report**, never a write into the production record.
- **LiteLLM auto-router shadow evaluations** (https://docs.litellm.ai/blog/auto-router-shadow-evaluations): duplicate a slice of live traffic through the router; the caller still gets the real response; "the shadow response is never served to anyone"; a blind LLM judge compares A/B. The shadow has its own sampling rate, its own comparison store, its own consumer — it never writes back into the serving record.
- **Judge calibration is measured, with names and numbers.** The Husain/Shankar school: a judge is "just another model" — measure agreement against a **golden set of ~100+ human-labeled traces** before trusting it, using TPR/TNR and Cohen's kappa; recalibrate regularly. Hamel's technique is literally called **"Critique Shadowing"** (https://hamel.dev/blog/posts/llm-judge/): the judge shadows the domain expert's critiques, and the shadow is judged by agreement with the expert — the shadow's calibration is measured against an **independent authority**, never its own outputs. Shankar's "Who Validates the Validators?" (UIST '24, https://people.eecs.berkeley.edu/~bjoern/papers/shankar-validators-uist2024.pdf) gives the formal foundation: LLM evaluators inherit the problems of the LLMs they evaluate and require human validation. Practitioner bar: hand-label ~50–100 examples, iterate until agreement ≥ ~80–90%.

### The gap
Three, in severity order:
1. **Our shadow writes into the same record it claims to only enrich.** Every real-world design keeps shadow evidence in a separate sink; ours lets the shadow's advisory opinion overwrite the production system's causal attribution. We call it "shadow," but a shadow that can rewrite the audit trail is a **co-decider with deniability** — power to corrupt evidence, none of the responsibility. This directly violates the control principle: the deterministic gate owns every decision, and ownership includes owning the *causal story*.
2. **Our shadow calibration is unmeasured** — the industry's non-negotiable measurement. A `shadow_decision` whose agreement with human judgment is unknown is **not evidence, it's noise with a schema**.
3. **Our shadow has no noise floor.** Diffy's N+1 trick exists because even two identical known-good instances disagree. We cannot distinguish "Jev disagreed because the human was wrong" from "Jev disagrees because Jev is noisy."

### Principled recommendation
1. **The shadow must be physically incapable of touching the decision record.** *Engine truth: a shadow's write capability defines its authority, not its name.* `shadow_decision` events go to a separate table/namespace, written by a separate component, with no shared mutable fields on the decision audit record. The kill-switch attribution bug is an architecture violation — fix it with **separate write paths**, not better discipline. Schema rule: the decision audit record's `reason`/`cause` fields are write-once by the decisioning path only.
2. **Measure the shadow before persisting its opinions.** *Engine truth: uncalibrated evidence is indistinguishable from rumor.* Golden set of real incidents with human outcomes; report kappa/TPR/TNR before any `shadow_decision` influences calibration dashboards. No agreement number, no trust.
3. **Calibrate against a noise floor, not raw disagreement.** Borrow Diffy's diff-of-diffs: measure human-vs-human disagreement alongside Jev-vs-human.

### Honest gaps (child 3/3)
- No public documented case of shadow writes corrupting a production audit trail was found (our exact bug class) — companies don't publish "our shadow corrupted the audit log" postmortems. The *principle* (separate sinks) is verified across designs; the failure case study is not. Our bug class is novel-documented internally, unconfirmed externally.
- No public design describes a *timed race* shadow (bounded timer; loser's answer still logged) — all assume the shadow completes. Our timer-race semantics appear to be our own invention.
- The "recalibrate weekly" / "≥80–90% agreement" figures come from secondary practitioner summaries of the Husain/Shankar courses, not verified against primary course material.

---

## 6. At-least-once forwarding with dedup — no double-paging, no rebilling

### What WE do
A durable outbox delivers pages to PagerDuty via user-supplied BYOK keys, with retries and dedup. **Known open gap:** retries can REBILL guaranteed race losers — a Jev call that already lost the race gets retried and billed again. Key resolution order decided: user store → env → unconfigured. PagerDuty stays mock-only in testing (FakePD).

### What the REAL WORLD does
The industry converged on one formulation: **at-least-once delivery + idempotent receiver = exactly-once EFFECT.** Exactly-once is never achieved by the transport alone.
- **Stripe idempotency keys** (https://docs.stripe.com/api/idempotent_requests): the API saves "the resulting status code and body of the first request made for any given idempotency key, regardless of whether it succeeds or fails." Retries with the same key get a **replay of the first outcome, not a re-execution** — "a retry does not get a second chance at the operation." Key scope: **per logical operation, generated ONCE before the first attempt, reused across every retry.** Generating a new key per attempt is the named anti-pattern ("defeating retry safety"). Internally: key row inserted at `started`, atomic phases with recovery points; crashed retries *resume from the last committed phase* rather than re-executing. Sources: https://github.com/rubyn-ai/skill-packs/blob/HEAD/stripe/idempotency.md, https://github.com/handbook-academy/engineering-handbook/blob/HEAD/content/hld/part-3-distributed-systems-theory/07-idempotency-exactly-once.md
- **Kafka exactly-once** (https://www.conduktor.io/glossary/exactly-once-semantics-in-kafka): idempotent producer (PID + sequence-number broker dedup) + transactions. The honest boundary, stated plainly: it guarantees the record appears exactly once **in the output topic** — "it does *not* guarantee that your database write, HTTP call to an external API, or any side effect outside Kafka happens exactly once. That boundary is yours to own."
- **Transactional outbox** (https://medium.com/@write2munish/the-outbox-pattern-how-you-actually-guarantee-message-delivery-c9eb70e43b30, https://www.conduktor.io/glossary/outbox-pattern-for-reliable-event-publishing): write the intent row and outbox row in one DB transaction; relay publishes; mark published only after ack; relay claims rows with `SELECT ... FOR UPDATE SKIP LOCKED`. The canonical crash-window duplicate (send, crash before mark) is handled by **idempotent consumers** — unique constraint on event ID (the inbox pattern).
- **PagerDuty dedups on a stable per-problem key** (https://github.com/pagerduty/developer-docs/blob/HEAD/docs/events-API-v2/02-Trigger-Events.md): subsequent events with the same `dedup_key` apply to the open alert — **retries with the same dedup_key never create a second page while the alert is open**. Once resolved, the same key opens a *new* alert (correct: new incident). Two hard rules: omit `dedup_key` and PD generates a random UUID per event (every retry pages anew — our double-page failure mode if we ever drop the key); the key must be stable identity (resource + check), never per-attempt (timestamp, random ID) — "a key that changes every time defeats deduplication entirely." Third-party mirror (https://github.com/sorotrail/sorobeacon/blob/HEAD/docs/channels/pagerduty.md): deterministic `dedup_key` for (rule_id, event_id), "so a redelivered alert never opens a second incident." Ordering hazard (secondary source, https://github.com/keeperhub/keeperhub/blob/HEAD/docs/plugins/pagerduty.md): a `resolve` arriving before its `trigger` is dropped — sequence events, never concurrent.
- **Svix / Standard Webhooks** (https://github.com/svix/svix-docs/blob/HEAD/content/idempotency.mdx): sending-side `Idempotency-Key` replays saved status/body for 12h; receiving-side at-least-once with dedup on the `webhook-id` header — "unique per message but reused across retries." Retry schedules documented (Stripe 3-day exponential; Svix immediate → 5s → 5m → 30m → 2h); exhausted messages go to dead-letter.
- **Avoiding double AI spend** (https://github.com/luizssantiago92/llm-router-gateway/blob/HEAD/docs/guide/How-it-works.md, https://github.com/shubham0745/llm-gateway/blob/HEAD/docs/architecture.md): proxy cache keyed on SHA-256 of the canonical request — exact-match hits are free, never re-billed; **errors are never cached**; bounded retries (once); per-branch cost budgets as circuit breakers.

### The gap
1. **Our idempotency key is scoped per-attempt; the industry scopes per-decision.** Every source confirms the diagnosis: Stripe generates once per logical operation; PD's `dedup_key` is stable per problem; Svix's `webhook-id` is stable per message across retries. Worse, the rebilling variant isn't even a dedup problem: **a retried Jev call after the race was lost is not a retry — it's a new AI call that should never have been made.** The fix isn't dedup at the provider; it's never issuing the call.
2. **We fight PagerDuty's dedup instead of cooperating with it.** PD's same-`dedup_key` semantics mean our at-least-once retries are *already safe on PD's side* with a stable per-problem key. Our design should lean in: `dedup_key = f(problem_id)`; the outbox's job is delivery assurance, not page-counting.
3. **Our outbox has the standard crash-window duplicate hole and no stated inbox.** PD's `dedup_key` absorbs duplicates for alerting, but our own side effects (billing records, audit events, Jev-cost accounting) need their own dedup — the inbox pattern (unique constraint on event ID at every state-mutating receiver).
4. **No LLM-cost dedup at all.** The gateways converge on: prompt-hash cache for successes, errors never cached, bounded retries, per-branch budgets. Our lost-race retry is deleted by construction in these designs: **a lost race is terminal; there is no retry of a race that already ended.**

### Principled recommendation
1. **Key per decision, never per attempt — and a lost race is not retryable.** *Engine truth: an idempotency key names the unit of work you refuse to repeat; if it names the attempt, you've named the wrong thing.* Outbox event key `page:{decision_id}`; PD `dedup_key = f(problem_id)`; Jev shadow call keyed `shadow:{problem_id}:{judge_version}` with the rule: **once the race is lost, the key is consumed — the retry returns the recorded outcome ("timer won; human paged"), it doesn't call Jev again.** (Stripe's exact semantic: the retry gets the recorded outcome, not a new execution.)
2. **Let PagerDuty be the second dedup layer, not an adversary.** *Engine truth: exactly-once EFFECT = at-least-once delivery + idempotent receiver; the receiver's idempotency is the real guarantee.* State the forwarder's correctness proof in these terms, not retry counts.
3. **Cache LLM successes by prompt hash; never cache errors; budget every call.** A short-TTL prompt-hash cache in front of Jev (successful judgments only), per-call cost budget failing the shadow branch fast. The retry hits the cache or is refused — never re-billed.
4. **Every state-mutating receiver of outbox events gets an inbox.** *Engine truth: the outbox guarantees delivery; only the receiver can guarantee singularity.* Unique constraint on `(event_id)` at every consumer that mutates state.

### Honest gaps (child 3/3)
- Jev's actual retry/billing behavior unverified: whether Typesafe offers idempotency keys, prompt caching, or bills timed-out-then-retried calls twice. The anti-rebill design assumes worst case; verify against Jev's API docs before sizing the cache.
- PD Events API v2 rate limits / 429 / `Retry-After` behavior not found in docs — treat PD as a passive receiver in the design; verify live during build.
- The resolve-before-trigger drop is from a third-party vendor doc (Keeper), not PD's own docs — probable, secondary source.

**Cross-cutting verdict (child 3/3, kept verbatim):** both open gaps are the same architectural sin wearing two masks — **the advisory path can mutate the authoritative record.** In shadow mode, the late judge rewrites the audit's causal reason. In forwarding, the retry re-spends on a decision already made. The industry's uniform answer is *separation with teeth*: separate write namespaces, separate key scopes, separate measurement of the advisor's worth. Under Aditya's control principle, the deterministic gate must own the decision *and its causal story and its spend* — the AI advises in a sandbox it cannot bill or rewrite.

---

## 7. The smaller gaps — validator honesty, break-glass, CORS

### 7A. JSON Schema `if`/`then` — the validator that certifies lies

**What WE do:** `validate_contracts.py` hand-rolls contract validation and cannot fully interpret JSON Schema `if`/`then`/`else`. The review wave already caught the sibling failure mode (#88: YAML text-grep validator theater). A contract the validator cannot actually evaluate is a contract that passes by inability to fail.

**What the REAL WORLD does:** `if`/`then`/`else` have been standard since draft-07 and are fully specified in 2020-12. The reference `jsonschema` Python library implements them completely (`Draft202012Validator`; `_keywords.if_` handles the `else` branch too) — see https://github.com/goddtriffin/json-schema-rs/blob/HEAD/research/reports/python/python-jsonschema-jsonschema.md (keyword support table: `else` → yes, handled inside the `if_` keyword). There is an official JSON-Schema-Test-Suite used for conformance. Nobody serious hand-rolls conditional semantics with pattern matching; the ecosystem converged on real validators plus the conformance suite decades ago.

**The gap:** we reimplemented a solved problem and got the hard part wrong. Under our stdlib-only constraint we cannot just pip-install the reference library — but that constraint argues for *less* hand-rolled surface, not more.

**Principled recommendation:** two honest options, no middle: (a) implement a proper recursive validator for exactly the keywords our contracts use, tested against the official test suite's `if`/`then`/`else` cases; or (b) BAN `if`/`then` from our contract schemas, express conditionals as separate schema variants, and add a linter that REJECTS schemas using unsupported keywords loudly. **Engine truth: a validator that silently under-validates is worse than no validator — it certifies lies.** Unknown keyword → loud failure, never silent pass. (This is the fail-open philosophy applied to the build: doubt must be visible.)

### 7B. Break-glass revocation — the emergency path must be the most audited path

**What WE do:** unresolved. We have freeze/unfreeze ceremony (unfreeze needs 2 attestors) and a kill switch, but no designed break-glass credential lifecycle: who holds the unfreeze capability, how it's stored, how its use is alarmed, what forces rotation afterward. #85 flagged this as a build-gating design gap.

**What the REAL WORLD does:**
- AWS Well-Architected (SEC10-BP05, https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_incident_response_pre_provision_access.html): pre-provision DEDICATED emergency accounts — least privilege, named per-responder, MFA, **outside** federation/SSO (independent of the thing that broke), no standing access keys. The key sentence: temporarily escalating a normal user's privileges "risks the escalated privileges not being revoked" — use purpose-built accounts instead of privilege escalation.
- Snowflake break-glass model (https://medium.com/snowflake/snowflake-break-glass-account-in-the-mfa-era-how-to-stay-compliant-and-unstuck-2cfc116dbb27): vault-held credentials, dual-control ("two-person rule") retrieval, time-boxed access linked to a change ticket, **rotation after every single use**, immutable audit logs reconciled against login history.
- Azure emergency access (https://learn.microsoft.com/en-us/answers/questions/2086979/mandatory-mfa-for-break-glass-account-vs-condition): excluded from Conditional Access (so a bad policy can't lock out the rescuers), MFA enforced separately.
- The runbook discipline (https://gist.github.com/tashiscool/c5e57e0b32bb6c48b4eb562fc4dd845a): dormant accounts — ANY login is an alarm event; audit every 90 days; rotate credentials after any personnel change and after any use.

**The gap:** our ceremony covers the *happy path* of governance (attestors, states, expiry) but not the *emergency path* lifecycle. An unfreeze capability with no storage discipline, no use-alarm, and no forced rotation is a standing backdoor with good paperwork.

**Principled recommendation:** break-glass = dedicated, dormant, dual-controlled credentials; every use fires an alarm and forces rotation before the incident closes; unfreeze without 2 attestors is impossible EVEN in break-glass. **Engine truth: break-glass bypasses the path, never the quorum.** Emergency changes the *route*, not the *rule* — this is the control principle applied to the worst day. And the emergency path must be the MOST audited path in the system, not the least.

### 7C. CORS is not a control — the beacon bypass

**What WE do:** permissive CORS plus an unauthenticated KEYS surface. #85 named it precisely: "CORS placebo vs beacon exfiltration" — the team tuned CORS while the secret-handling surface had no auth.

**What the REAL WORLD does:** CORS is a *relaxation* of the same-origin policy for legitimate cross-origin callers — it is not access control, and the server must authenticate every request regardless. `navigator.sendBeacon()` (plus img pixels, form posts, DNS prefetch) issues simple requests that never trigger preflight; CSP `connect-src` constrains fetch/XHR/WebSocket/EventSource/**sendBeacon** destinations (https://dev.to/marsou001/content-security-policy-csp-and-how-to-configure-it-against-xss-in-nodejs-ice), but it is a second line of defense — and some channels (notably DNS exfiltration) cannot be blocked by CSP at all (https://github.com/itroyesivan/saker/blob/HEAD/preset/pentest/refs/web/csp-bypass-advanced.md). The exfiltration-channel inventory (https://cellwall.io/en/resources/blog/website-data-exfiltration) lists fetch/XHR, sendBeacon, pixels, forms, frames, WebSocket, navigation — CORS configuration addresses approximately none of them as an attacker boundary.

**The gap:** we treated a UX mechanism (CORS) as a security control while the actual control (authentication on the KEYS surface) was missing. Tuning CORS against beacon-class exfiltration is placebo by construction.

**Principled recommendation:** authenticate every write/secret surface (R-16's bearer tokens are the real fix); set CSP `connect-src` to same-origin-only on the console as defense-in-depth; document that CORS is not a control. **Engine truth: a control that doesn't survive its bypass was never a control.** Name CORS as UX, move the real check server-side, and never let a CORS diff substitute for an auth diff in review.

---

## 8. The 5 engine truths the next build must respect

Synthesized across all seven topics. Each is stated as a law the build is judged against — violate one and the build is wrong no matter how good it looks.

**Truth 1 — The control must be verified at the point of execution, by the executor, with proof the executor itself emits.**
Our `thresholds.json` failure (governance), our mock-path drill (kill switch), and our rewritable kill attribution are three faces of one disease: control that lives in a place the hot path doesn't read. OPA verifies the signed bundle *in the executor at activation time*; Nasdaq's exchange confirms the kill *by the system that did the killing*; Meta learned to gate *at the sink*. No sidecar, no watchdog, no second file, no mock harness stands between the control and the effect. Under Aditya's control principle, this is the whole game: the deterministic gate owns the decision, and ownership is proven at the decision point, not asserted in a doc.

**Truth 2 — The advisory path can never mutate the authoritative record.**
The shadow judge rewrites the audit's causal reason; the retry re-spends on a decision already made. Same sin, two masks. The industry's uniform answer is *separation with teeth*: separate write namespaces (Uber/Diffy/LiteLLM), separate key scopes (Stripe/PD/Svix), separate measurement of the advisor's worth (Hamel/Shankar). The AI advises in a sandbox it cannot bill and cannot rewrite. A shadow's write capability defines its authority, not its name; an idempotency key names the unit of work you refuse to repeat — if it names the attempt, you've named the wrong thing. The gate owns the decision *and its causal story and its spend*.

**Truth 3 — Tamper-evidence is external anchoring plus separation of writer and witness; a self-attesting log is theater.**
Nobody in production writes the checkpoint back into the log: CT gossips STHs out-of-band, CloudTrail chains digests in a separate folder, Azure stores digests outside the database in WORM. The writer is untrusted by design; a compromised writer forges a perfect chain, so the witness must be independent (different account, different credentials, ideally different trust domain). And QLDB's corpse is the warning against outsourcing the ledger to a managed exotic — own the chain, keep it boring. Our failures are all wiring (seals that don't run, genesis as lore, retention that breaks the chain), not choice of primitive.

**Truth 4 — Fail-open is a vocabulary, a dead-man's switch, and confirmed delivery — a law without sentences is a slogan.**
Our fail-open law is philosophically correct and industrially standard, but it can't express `digest:true + error_code`, has no liveness heartbeat on the delivery path, and trusts vendor acceptance codes. Steal the solved patterns: Page/Ticket/**Digest**/Log (the digest is a delivery mode with its own deterministic escalation, not suppression-lite); an always-firing Watchdog to an *independent* receiver whose silence pages; suppression locks as explicit versioned config with the liveness signal inhibit-immune; and end-to-end delivery confirmation, because a 202 is not delivery. AF447's lesson rides along: fail-open-to-human fails if the human gets no situational awareness.

**Truth 5 — Doubt must be loud.**
A validator that silently under-validates certifies lies — fail on unknown keywords, never pass. Break-glass bypasses the *path*, never the *quorum* — and the emergency path must be the most audited path in the system, not the least. A control that doesn't survive its bypass (CORS vs beacons) was never a control — name it UX and move the real check server-side. This is the fail-open philosophy applied to the build itself: uncertainty is always made visible, never absorbed.

---

## 9. Honest gaps — what this research could not verify

Assembled from all lanes. Nothing here is smoothed over.

- **No public documented case of shadow writes corrupting a production audit trail** (our exact bug class) — companies don't publish "our shadow corrupted the audit log" postmortems. The separate-sink principle is verified across designs; the failure case study is not. Our bug class is novel-documented internally, unconfirmed externally.
- **Our timed-race shadow semantics appear to be our own invention** — no public design describes a bounded-timer race where the loser's answer is still logged. All public shadow designs assume the shadow completes.
- **Husel/Shankar calibration figures** ("recalibrate weekly," "≥80–90% agreement") come from secondary practitioner summaries, not verified against primary course material.
- **Jev's actual retry/billing behavior unverified** — whether Typesafe offers idempotency keys, prompt caching, or bills timed-out-then-retried calls twice. The anti-rebill design assumes worst case; verify against Jev's API docs before sizing the cache.
- **PagerDuty Events API v2 rate limits / 429 / `Retry-After` behavior** not found in docs — treat PD as a passive receiver; verify live during build.
- **Resolve-before-trigger drop** documented by a third-party vendor (Keeper), not PagerDuty itself — probable, secondary source.
- **No AWS engineering post-mortem on QLDB's retirement** — "the market didn't value cryptographic verifiability enough" is informed inference from the field report + AWS's own migration guidance, not a cited AWS statement.
- **CT log write-throughput numbers / STH signing intervals** not verified from an official source.
- **PagerDuty schedule-gap behavior** from a PagerDuty-hosted community article ~2 years old; UI wording may have changed.
- **No public postmortem of a fail-closed-to-silence paging outage** with attribution found — PD's schedule-gap docs + Events API 202 behavior are the closest documented cases.
- **SRE book quotes** corroborated across multiple secondary sources; the official `sre.google` monitoring page not opened directly.
- **Meta's internal Gatekeeper approval workflow** is proprietary — only targeting mechanics and the "real kill switch" lesson are public.
- **LaunchDarkly's 200ms propagation claim** is vendor-stated; no published measurement methodology found. Whether their audit log is tamper-evident vs append-style unverified from a primary source.
- **Chaos-engineering kill-switch verification workflow** from community repos, not Gremlin/Litmus/AWS FIS official docs — established practice, not vendor-verified method.
- **Exchange kill-switch analogy** transfers on confirmation/attribution/stickiness mechanics, not domain semantics (they cancel resting orders; we halt suppression).
- **JSON Schema validator claims** (reference library `if_`/ `else` support) from the library's documented keyword table and a third-party research report's support matrix, not from running the conformance suite ourselves.
