# Sentinel — Charter

**Project:** Sentinel — SRE page-or-suppress triage middleware on TypeSafe AI's Jev
**Status:** base setup · design v0.1 frozen (`ARCHITECTURE.md`, 2026-10-02) · Preview-1 target: **EOD Oct 4, 2026 IST**
**Chairman:** Aditya · **Commander:** Petu
**Repo (source of truth):** `AdityaPagare619/sentinel` (private until Preview-1)
**Working dir (operations):** `~/workspace/jev-builds/sentinel/` — same machine, mirrored

## Mission

Build the drop-in pre-page gate that kills pager noise at the source: a 50-line integration
in front of the PagerDuty/Opsgenie Events API. Jev triages every alert (severity + owning team
+ disposition, calibrated probabilities, 70–500ms); high-confidence known noise is suppressed
with an audit entry; everything uncertain pages a human. The product we sell is not the
classifier — it is the **trust layer**: expected-cost threshold tuning, per-team calibration
dashboards, an immutable decision audit log, and a 2-week shadow pilot that proves savings
before we change a single page.

## Why we win (research numbers, not vibes)

- **Pain is quantified.** 95–98% of alerts are noncritical noise; <40% ever investigated;
  each false page costs ~45 min of sleep + 2–3h of daytime debugging; 30–50% of all pages
  are planned-work artifacts. SRE toil is back to ~30% of working time; ~70% of SREs cite
  on-call stress as a burnout driver. (Research report §5.2)
- **The economics are absurd and favor us.** ~$66/yr in Jev inference per org vs the
  **$8,388/yr PagerDuty AIOps umbrella** (+$41/user/mo seats) — ~99% gross-margin headroom
  at a $199–499/mo price point. We price against *avoided toil*, never against COGS.
- **Latency is the product.** 70–500ms server-side sits *inline* in the alerting path —
  before the page is sent — where a 2–3s LLM call is a scheduling hazard during storms.
  One call returns severity + team/escalation + confidence in parallel. This spec is
  load-bearing; any design that moves triage off the hot path is wrong.
- **Non-adversarial input.** Alerts are self-inflicted noise — nobody is crafting them to
  fool the pager. This is exactly where calibrated probabilities are a feature, not an
  attack surface.
- **The wedge is integration, not rip-and-replace.** Nobody leaves PagerDuty. The shadow-mode
  pilot ("we change nothing for 2 weeks, then show you the savings report") *is* the top
  of funnel.

## Design laws (non-negotiable)

1. **The ONLY autonomous action is "page a human."** Jev may downrank, route, queue, or
   (triple-locked) suppress — it may never create new autonomous remediation.
2. **Uncertainty always pages.** Low confidence, `cannot_determine`, malformed input,
   unparseable payload → the existing pipeline, byte-identical to today.
3. **Fail open on ANY error.** Jev 401/429/529/timeout, network failure, corrupted state —
   every exception path is a pass-through. Enforced by the kill-the-client test: kill the
   Jev client, assert every alert still pages. A gate that drops pages on error is a
   company-ending bug.
4. **Suppression only on high-confidence known-noise with per-team allowlists.**
   Triple lock: P(p1_critical) < 0.2% AND Q3 confidence ≥ 0.90 AND fingerprint ∈
   customer-verified allowlist. Suppress is never the default.
5. **No accuracy claims in marketing or output.** Only calibration curves on the customer's
   own labels. The honesty order is the brand: **a failed experiment is reported as a
   failed experiment, never dressed up.** Synthetic eval proves plumbing, never
   production accuracy.
6. **Audit every decision.** Every alert — including passthroughs and suppressions —
   writes a decision row (input hash + model version + probabilities) to the append-only
   audit log. No silent decisions, ever.

## Non-goals (from the research's negative roadmap, §8.5)

- Nothing that auto-dismisses security alerts or auto-dispositions compliance alerts
  (adversarial + tail-liability).
- Nothing PHI-adjacent until TypeSafe signs a BAA (12+ months).
- No "cheap AI" positioning anywhere — buyers buy defensibility, avoided cost with proof,
  or reclaimed hours; nobody buys price-per-token.
- No raw-API product, no terminal, no consumer inbox, no AI SDR.
- No "Jev incident commander" — Jev cannot read dashboards or reason across a 3-hour
  incident timeline; it is the classifier *behind* the copilot, never the copilot.

## Operating principles

1. **Evidence over eloquence.** Every factual claim carries its source (file, line, URL,
   or run id). A claim without a source is a rumor — the Claim Auditor kills it.
2. **Nothing lives only in chat.** Decisions → `ops/decision_log.md`. Constraints →
   `ops/constraint_registry.md`. If it isn't written, it didn't happen.
3. **Constraints are load-bearing.** The Constraint Sentinel owns them and can veto any
   plan that violates one. No exceptions.
4. **Reproducibility from day one.** Code + seed + data version + environment logged at
   run time. The audit log itself is the product's proof.
5. **Verification is independent.** Builders never grade their own work — wardens and the
   Red Team review every consequential output.
6. **Aditya touches only what only Aditya can touch:** direction calls, the token's
   repo-access approval, design-partner introductions, go/no-go on pilots.
