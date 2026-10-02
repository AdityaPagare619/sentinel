# The Seven Laws — Principal-Grade Working Doctrine

*Adopted 2026-10-03 ~01:10 IST by Aditya's direct order. Every design decision
in the principal re-derivation is judged against these. They are not posters;
they are the review gate.*

## Law 1 — Global vs local optimization
Don't tune the bottleneck; ask whether it should exist. Every optimization
proposal must first answer: "What if this component/queue/retry didn't exist
at all?" A faster correlator is local; questioning whether correlation should
be a separate stage is global. Principals do the second.

## Law 2 — Eternal friction
The network drops. Data corrupts. APIs change unannounced. The next maintainer
is tired, on call, and will not read your docs. Design for hostility, not for
the demo. Every happy-path assumption gets a named adversary. If a design only
works when everything works, it doesn't work.

## Law 3 — Type 1 vs Type 2 decisions
Irreversible choices (core schema, public API, paging-path architecture, data
retention policy) get RFC-grade rigor: written alternatives, explicit tradeoffs,
named dissent. Reversible choices (dashboard copy, internal tooling, doc
structure) get speed. Maintain an explicit decision register marking each —
`design/principal/09-decision-register.md`. A Type 1 decided like a Type 2 is
a future incident.

## Law 4 — Five whys / first principles
Peel every requirement to bedrock before designing. "We need a threshold
slider" → why? → "so teams tune suppression" → why? → "because noise differs
per team" → why? → ... until you hit the irreducible truth. Design from the
bedrock up, not from the feature request down.

## Law 5 — Chesterton's fence
Archaeology of the legacy. Before proposing to change anything about how
paging works, understand WHY PagerDuty/Opsgenie/alerting stacks are built the
way they are. That "ugly" escalation policy, that weird dedup rule, that
annoying rotation scheme — each exists because of a specific outage, a specific
customer scream, a specific 3 AM. Find the outage. Name it. Then — and only
then — propose the change, with the fence's reason addressed, not ignored.

## Law 6 — Pre-mortem
"It is one year from now. Sentinel caused a missed SEV1 that cost a customer
millions. What exactly failed?" Write the failure in concrete, mechanical
detail — not "the model was wrong" but "the allowlist fingerprint collided
across two services because we hashed only the check name, and the 02:14
database failover alert matched a known-noise pattern from the cache cluster."
Design against the answers. Every mitigation gets a test.

## Law 7 — The four constitutions
Every design is reviewed against all four:
- **Software engineering:** explicit boundaries; code is liability — every line
  is a future bug, so the smallest sufficient system wins. Interfaces are
  contracts; contracts are versioned.
- **Infra/DevOps:** ephemeral infra; blast radius thinking — what breaks, how
  far does it spread, how fast can we contain it. No single point of trust.
- **Data/AI:** deterministic guardrails over probabilistic models; data
  pedigree — every number traces to its source; the model advises, it never
  controls.
- **Product:** empathy for the operator (the 3 AM human); graceful degradation
  — every failure mode degrades to something useful, never to silence.

---

*Enforcement: the coordinator runs cross-domain challenge on every chief's
output and operates a brutal review gate. Shallow, early, or thin results are
rejected on the spot and re-worked. If an output couldn't survive a Google
principal's review, it goes back.*
