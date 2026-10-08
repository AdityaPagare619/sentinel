# External Expert Committee — Sentinel Launch-Readiness Review
**Date:** 2026-10-08 · **Coordinator:** Petu (subagent) · **Ordered by:** Aditya

## Method
Five independent judge subagents, each a distinct industry-veteran persona, reviewed Sentinel from the user's chair: the three live consoles (staging, production, load-test) via page-text fetch plus the GitHub repo README/docs. No logins, no clicks, no purchases. Tone mandated soft and constructive. One judge (Mira) used the jev-web skill for a micro-judgment on the load-test headline (claim-substantiation score: **0.09**); the others judged the pages self-documenting enough to skip it. Judges did not re-derive the internal NOT YET verdict; they judged forward from the live surfaces.

**Honest limits of this review:** text-only console review (JS consoles extract thinly — itself a finding); no live interaction, no code execution, no real-traffic data available to anyone.

## Per-judge summaries

### Mira K. — ex-FAANG SRE, 15 yrs on-call · "Would I bet my sleep on this?" → NO
Understood it in 5 minutes as a drop-in pre-page gate (correlator → gate → forwarder) with a sound triple-lock doctrine. **Biggest blocker:** no real-traffic evidence it never suppresses a real SEV1 — every suppression number is synthetic or modeled. Top gaps: (1) no real-data calibration/shadow report; (2) single-box with in-memory state in the paging path, manual bypass; (3) load-test headline contradicts its own body (Jev: 0.09 substantiation). Docs: README admirably honest; ROAD_TO_LAUNCH/ROADMAP still target Oct 4, unmarked. *"Run the shadow pilot, publish the report, fix the headline, and then ask for my pager number."*

### Devon A. — SaaS founder/CTO, seed stage · "Deploy Monday?" → only as a logging relay
5-minute read: pre-page gate, triple lock, fail-open, ~$66/yr inference — clear and honest. **Biggest blocker:** no Monday path to actual value — suppression needs 50–300 of your own labeled alerts and weeks of shadow mode; Monday's deploy is a pass-through relay. Top gaps: (1) on-ramp is a project, no shadow-exit checklist; (2) single-box v0.1, no HA; (3) no version discipline — zero releases/tags, load-test numbers from an unmerged lane. *"Safe to try, not ready to run my pager on."*

### Sofia R. — enterprise solutions architect · "Passes integration/security review?" → NO (shadow pilot: yes)
5-minute read accurate, including BYOK and shadow-first adoption. **Biggest blocker:** paging path depends on a vendor with no SOC 2, no SLA, US-only hosting; plus the SECURITY.md-admitted credential exposure still awaiting rotation. Top gaps: (1) no real-data evidence; (2) single-box in-memory state; (3) operability incomplete (no gate SLOs, no synthetic probes, no support model). Docs: surprisingly good and honest; one wrinkle — the "production" console labels itself PRODUCTION/Live while carrying EVALUATOR HARNESS text with zero traffic. *"I wouldn't let this into our paging path today, but I'd happily run it in shadow mode."*

### James O. — solutions sales leader · "Can I sell this?" → not yet — blueprint with synthetic proof
5-minute read crisp, including the shadow-mode adoption design. **Biggest blocker:** no real-data proof — "has this run on real alert traffic without suppressing a real incident?" answer is no; unsellable without a shadow-pilot report. Top gaps: (1) no completed shadow pilot; (2) single-box, no HA; (3) honest-caveat machinery is double-edged — headlines travel without caveats. Docs: incoherent — ROAD_TO_LAUNCH/ROADMAP target Oct 4 (past), no supersession, no successor doc. Pitch when ready: *"Sentinel sits in front of PagerDuty, kills your pager noise with a model that can never drop a real incident — because anything it's unsure about pages you anyway, and it proves itself on your traffic in shadow mode before it changes a single page."*

### Priya N. — open-source community lead · "Would users adopt/contribute?" → not until trust basics land
5-minute read accurate; praised fail-open-by-construction. **Biggest blocker:** public surfaces call themselves "production" before anything is production — for a trust product, headlines outrunning caveats loses exactly the users it needs. Top gaps: (1) **no contribution path — no LICENSE (legally all-rights-reserved, not open source), no CONTRIBUTING.md, zero releases/tags**; (2) environment labeling confuses users (PRODUCTION badge vs simulated); (3) no support/ops story. Docs: README excellent; root has two expired master plans, "v0.1" named but never cut, load-test describes unmerged lane code. *"Cut a LICENSE, one v0.1 release, and an honest 'what is this page' label on every console, and the trust the product is selling will finally start accruing."*

## What all five agreed on (strengths)
- The fail-open doctrine and triple-lock suppression design are genuinely good — every judge took the safety architecture seriously *because* of it.
- The README's honesty (limitations up front, quantified Jev non-determinism) is rare and is the project's best asset.
- Shadow-mode-first adoption is the right go-live evidence design — but it remains a design, not a run.

## Ranked launch-readiness checklist (what gates real users, in order)
1. **Run one real shadow pilot** (2 weeks, real labeled traffic) and publish the suppression report — false-suppress rate on real SEV1s. Gates every chair.
2. **Fix trust presentation:** qualify the load-test headline ("YES — 16.2M/day" is virtual-time); label every console honestly — PRODUCTION badge only on real production; mark superseded demos.
3. **Cut v0.1 properly:** add LICENSE (currently all-rights-reserved by default), first release + tag, CHANGELOG, CONTRIBUTING.md.
4. **Retire the stale root plans:** mark ROAD_TO_LAUNCH.md / ROADMAP.md (Oct 4 targets) superseded; one current plan doc.
5. **HA/redundancy story** for the paging path — or an explicit, honest single-box frame with a runbook (fail-open covers errors, not the box dying).
6. **Shadow-mode exit checklist:** define the done-state of the on-ramp (Devon's "project, not a deploy").
7. **Gate observability:** SLOs for the gate itself, synthetic probes, dashboards on drop rates / Jev latency / fail-open frequency.
8. **Rotate the exposed credential;** write the vendor posture (no SLA/SOC 2, US-only) in procurement-ready form.
9. **BYOK onboarding path** on the real topology (still missing per baseline).
10. **Support story:** named responder / status page, or an explicit community-support frame.

## The single most important finding
**Unanimous across all five chairs: there is no real-traffic evidence that Sentinel never suppresses a real incident — and every judge's decision dead-ends on that one missing artifact.** Mira won't bet sleep on it, James can't sell without it, Sofia can't integrate on a theory, Devon gets no Monday value from it, Priya can't build trust around headlines that outrun it. Everything else on the checklist is fixable hygiene; the shadow-pilot report is the gate.

---

## Addendum — live browser evidence (2026-10-08 evening, Petu's live browser)

### Staging (fully explored, read-only)
Confirmed: honest SIMULATED banner ("nothing here pages anyone"), seeded synthetic pipeline, per-number source tooltips, coherent layout (pipeline health strip, "Needs you" risk-ordered list, "Handled quietly" by suppression reason). Kill-switch drawer is genuinely good trust material: READY badge, fail-closed contract ("the machine stops, it does not merely stay silent"), policy governance v2026.10.06-r3 signed 2-of-2 (Aditya + Maya), HMAC-verified auth status, race stats (Judge 35, Timer 1, p50 415ms). **But** sharp edges: vivid SEV1/PAGE language ("SEV1 Database failover — billing") under the SIMULATED badge, a "Paging live" tab directly beneath it, and every-second ticking scripted numbers that feel live. All five judges flagged this as a newcomer-confusion risk — cheap copy fixes (relabel tab "Paging simulation", tone down severity strings in simulated surfaces, add "scripted replay" label).

### Production (BLOCKED — contents UNVERIFIED)
Loads with "PRODUCTION Live platform backend" banner, but an operator-token modal blocks everything; nothing behind it verified. The token gate itself is correct behavior (memory-only bearer token, no bypass attempted — a modest security positive per Sofia and Mira). **But** the "Live platform backend" banner is now itself unverified, and Priya's concern sharpened: a production banner over a black box teaches users to distrust production badges. Fix: banner should read "Production — operator access required" or carry a "gated, unverified" marker until Aditya's hand-test exercises the token path.

### Loadtest (fully explored)
Renders fine with bar charts; the honest caveat section holds up live (100K tier "measures a memory-starved box, not the engine"). **New gap:** the kill switch is claimed "fully operational" in verdict text but is not visible anywhere on this dashboard — every judge flagged it. Either render the control/state indicator or soften the claim.

### What changed in the verdicts
- **Nothing moved on the core verdict:** all five remain NOT production-ready; the shadow-pilot report is still the gate.
- **Demoability strengthened:** James — staging as rendered is a genuinely buyer-safe demo; sell staging + loadtest, keep production a roadmap slide until the hand-test.
- **Security read slightly positive:** Sofia, Mira — token gate is correct behavior; underlying credential/vendor gaps unchanged.
- **Checklist item 2 (trust presentation) now has exact fixes:** relabel "Paging live" → "Paging simulation"; tone down SEV1 language in simulated surfaces; production banner must not claim "Live" until verifiable; make kill switch visible on loadtest or soften "fully operational."
