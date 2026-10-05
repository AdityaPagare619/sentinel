# Blameless postmortem template

Copy to `ops/incidents/YYYY-MM-DD-<slug>.md` and fill in. Delete this
line and the italic guidance before publishing. Everything under a
section header is written as fact with evidence — links to logs, commits,
and runbook lines travel with the claims.

> **Blameless ground rules:** assume everyone acted reasonably on the
> information they had. Write about systems and conditions, not people.
> "Where we got lucky" is mandatory — luck is exposure data.

---

## Metadata

| Field | Value |
|---|---|
| Incident date (UTC) | |
| Severity (SEV1–SEV5) | |
| Incident commander | |
| Runbook used (RB-N + link) | |
| Detection source | |
| Status | draft / in review / published |
| Reviewer | |

## Summary

*Two or three sentences: what happened, from the user's perspective, and
what we did about it. A stranger who reads only this section should
understand the incident.*

## Impact

*User-visible harm, measured, not vibes: which alerts/pages were
missed, delayed, or wrong; how many; duration (start–end, UTC); who was
affected. If there was no user impact, say so explicitly and why
(defense-in-depth worked, quiet night, etc. — that last one is luck).*

## Root Causes

*Plural, always — incidents have contributing factors, not a single root
cause. Use five-whys (principal-systems) to push past the first answer;
write the chain, and name the systemic reason people had incomplete
information (the thing we fix is the system, not the person). Reference
the fence: if we touched legacy behavior, say why the old behavior
existed (Chesterton's fence — the old procedure may carry knowledge
nobody wrote down).*

1.
2.

## Trigger

*What set it off this time — distinct from the root causes above. A
trigger can be benign (a deploy, a config change, traffic spike).*

## Resolution

*What stopped the bleeding, in order, with timestamps. Include what
didn't work — the failed fix attempts are the most useful part for the
next on-call.*

## Detection

*How did we find out? Page, watcher, human, deploy gate, customer? How
long between onset and detection? If the detection was luck, say so —
that is the lesson.*

## Action Items

*Owner + priority + tracking id, per item. Lunney's categories in
parentheses help pick the right fix (Prevent / Mitigate / Repair /
Detect / Process / Other). An action item without an owner will be
deprioritized against feature work — mechanically, not maliciously — so
the owner line is not optional.*

| # | Action | Owner | Priority | Tracking id | Due |
|---|---|---|---|---|---|
| 1 | | | | | |

## Lessons

*Three subsections, all three mandatory.*

- **What went well:** behaviors to reinforce in drills.
- **What went wrong:** what the system needs (not who needs training).
- **Where we got lucky:** the incident we avoided this time, stated
  plainly — e.g. "quiet night", "happened during the drill window",
  "the guard caught it before the disk filled". Luck is exposure data.

## Timeline

*Timestamped, UTC. Mark detection, mitigation, and resolution moments
explicitly — they are the events future analysis queries against.*

| Time (UTC) | Event |
|---|---|
| | |
