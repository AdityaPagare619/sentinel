# Freshness Limitations — the honest-scope table for C-1

The 12 explicit non-defenses from `docs/SECURITY.md` §7, mapped to what
each one means for the freshness regime — plus the freshness-specific
limitations the design doc names honestly. This file exists so no gap is
silent: everything here is a documented, reviewable boundary.

---

## 1. The 12 non-defenses, as they touch freshness

| # | Non-defense (SECURITY.md §7) | Freshness implication |
|---|---|---|
| 1 | Secret custody at the partner | Out of freshness scope — but note the parallel: a proof is only as trustworthy as the pipeline that wrote it. If the tuner host is compromised, `CalibrationFitProof` is attacker-controlled bytes. We bind proofs to the manifest and gate `issued_by` by format; we do not defend the tuner host itself. |
| 2 | Replay of a valid delivery | Closed fully at the receiver; no freshness interaction. |
| 3 | A *perfect* instruction firewall | Orthogonal. Flagged payloads fail closed to page regardless of lock freshness — freshness never overrides the firewall. |
| 4 | Audit history rewrite | The `freshness_transition` audit events inherit the audit log's tamper-evidence (hash chain + triggers + customer-held checkpoints) — and its limit: full-machine compromise (DB *and* checkpoint sink) is incident response, not cryptography. |
| 5 | Stolen paging-provider key | Orthogonal: the provider key bypasses the gate entirely, freshness included. BYOK + misuse alerts are the defense; freshness does not see that path. |
| 6 | Compromised TypeSafe/Jev provider | **Direct interaction.** `model_pin` binds the fit to a version *string the provider reports*. A provider that lies about its model version defeats the pin the way a lying signer defeats a signature check. The pin defends against honest vendor moves (ADR-015), not a backdoored provider — that is a vendor-risk decision, disclosed. |
| 7 | TLS/CA compromise | Orthogonal. Proofs are read from local config files, not fetched over the network at decision time — which is exactly why config-embedded beats sidecar-DB for this design. |
| 8 | Volumetric DDoS | Orthogonal, with one note: the V2 heartbeat and V1 validation are control-plane work and must never share fate with the paging hot path. A DDoS that starves the heartbeat delays stale→page transitions; it cannot cause silence (stale proofs fail *toward* paging only when evaluated — but an unevaluated stale proof keeps its last verdict, see §2.5). |
| 9 | Social engineering of the operator | **Direct interaction — and the design's answer.** The fatigue ratchet (H-2) *is* social-engineering-by-exhaustion of the operator. Freshness answers it with physics, not policy: editing the bar without re-attestation breaks `config_hash` ⇒ lock 2 fails ⇒ everything pages. We defend the *system* against the consequences of persuasion; the 3 AM re-attestation flow still requires two humans and fresh evidence, which is the irreducible human remainder. |
| 10 | Malicious insider with legitimate credentials | **Direct interaction.** Two-person attestation raises the bar from one insider to two colluding insiders for locks 2 and 3. A pair of authorized insiders attesting a malicious bar is indistinguishable from legitimate governance at our boundary — the irreducible residual of "provenance, not truth," applied to governance. The audit trail (who attested, on what evidence) makes it undeniable afterward; it does not prevent it. |
| 11 | Clock attacks / NTP spoofing | **Direct interaction.** Every freshness predicate reads the host clock. We log clock-sync state at startup (per §7); host clock integrity remains the host's job. A clock rolled *backward* keeps stale proofs fresh — this is the one direction that fails toward silence, and it is why the design records the assumption instead of silently depending on it. (Monotonic-clock guarding of the heartbeat interval is follow-up work, not claimed.) |
| 12 | Nation-state with 0-days | Named out of scope for v0.x, unchanged. |

---

## 2. Freshness-specific documented limitations

These are not in the §7 table; they are named here so the safety case
never leans on them silently.

**2.1 The drift check is only as good as its feeds.** Incident linkage and
owning-service major-version rewrites arrive via `drift_state`
(`{"incident_linked": {fp: id}, "rewritten": {fp: True}}`) at V1/V2. Until
the incident feed and the deploy-metadata feed are wired, the drift check
degrades to TTL-only. The backstop that holds without the feeds: TTL expiry
plus the re-attestation rule that `on_evidence` must cite *post-expiry*
occurrences (design §C3). The feeds are required follow-up work, not
assumed infrastructure.

**2.2 `issued_by` is a format gate, not a signature.** The validator
rejects proofs whose `issued_by` is not `<run-id>/<code-version>` shaped —
a human cannot hand-wave a fit into existence. It does *not*
cryptographically verify that the named tuner run produced the named fit.
Machine-signing of fit artifacts is follow-up work; the format gate plus
the `trained_on` pedigree (n, reference class, `history_as_of`) make
re-stamp fraud detectable in review, not impossible in code.

**2.3 Rubber-stamp rejection is split across two enforcers.** "A
re-attestation citing the old backtest is a rubber stamp" — the V1 machine
check enforces evidence *presence and shape*; rejecting a *stale*
backtest requires attestation history and lives in the two-person
governance flow (platform tier). The machine half is tested; the human half
is procedure. Both are named; neither is assumed.

**2.4 Re-fit SLA is operational, not mechanical.** When lock 1 goes stale,
re-training the fit within 7 days is a control-plane work item tracked as
an unsuppressible alert — the *tracking* is mechanical, the *repair* is
human. Staleness is a state the system pages out of and then repairs; the
system does not repair itself.

**2.5 A stalled heartbeat delays stale→page, never causes silence.**
Freshness is state evaluated at V1/V2. If the heartbeat itself stalls
(host down, control plane wedged), verdicts freeze at their last evaluated
values. The failure mode is *delayed paging*, bounded by heartbeat
recovery — and the heartbeat's own liveness is a control-plane health
signal, itself unsuppressible. What this is NOT: a proof that a dead
control plane pages. It doesn't; it says so here.

**2.6 Future-dated proofs are not rejected.** A `fit_trained_at` in the
future yields a negative age, which trivially satisfies the TTL window.
Minor tuner/gate clock skew makes strict rejection a false-staleness
hazard, so the validator does not reject it. Deliberate future-dating to
extend a TTL is tuner-host compromise (§1, row 1) — out of scope, named.

**2.7 The 15-minute heartbeat is a detection bound, not a prevention
bound.** An incident that poisons a fingerprint is reflected at most one
heartbeat after the `drift_state` feed delivers it. Sub-15-minute
poisoning-to-suppress races are not closed by this design; they are
bounded by it, and the bound is the tunable (Type 2).

**2.8 TTL defaults are Type 2 and org-tunable — the *existence* of the
windows is Type 1.** 90-day fit TTL, 180-day allowlist TTL, 30-day
re-validation clock, 15-minute heartbeat: an org may tune the numbers with
versioning and audit, but may not remove the windows. Removing a window
reintroduces the exact silent rot C-1 was created to kill.

**2.9 Proofs attest to the config bundle, not to the world.** A fresh
`CalibrationFitProof` proves the fit is recent, pinned, and trained on the
deployed label pipeline — it does not prove the fit is *good*. Fit
quality is the tuner's job (backtests, shadow reports); freshness is the
liveness job. Conflating the two is how "recent" becomes "correct."

---

*The rule behind this file: we defend the decision path against rot; we
do not defend the universe. Every row above is a deliberate, documented,
reviewable boundary — which is what makes the rows we DO defend
trustworthy.*
