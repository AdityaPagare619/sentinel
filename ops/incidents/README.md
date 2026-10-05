# ops/incidents — the incident record

Every production incident and near-miss with a lesson gets a blameless
postmortem here within **72 hours** (principal-systems operating rule 5).
The record exists so the next on-call inherits the learning, not just the
scars.

## Rules

1. **Blameless, always.** Assume everyone acted reasonably on the
   information they had. Investigate the systemic reasons people had
   incomplete information — you can't fix people, you can fix systems.
   Blame chills reporting; hidden incidents recur.
2. **Write, review, publish, read.** Postmortems are reviewed like code
   (a senior signs off — an unreviewed postmortem might as well not exist),
   published to all lanes, and used as study material: read other lanes'
   postmortems, replay past incidents in drills (Wheel of Misfortune).
3. **Action items are owned, prioritized, tracked.** Owner + priority +
   tracking id, per item. Action items without owners are deprioritized
   against feature work — that is mechanical, not malicious; the template
   forces the owner line.
4. **"Where we got lucky" is mandatory.** Luck is information about
   exposure. If we survived because it was a quiet night, that belongs in
   the record.
5. **Pattern breach triggers one automatically** (OPERATING-RULES §5.3 —
   a rule's graduated response reaching the fourth step demands a
   postmortem). Near-misses with lessons also get one; writing is cheap,
   recurrence is not.

## File naming

`YYYY-MM-DD-<slug>.md` — e.g. `2026-10-04-receiver-crashloop-disk-full.md`.
One incident per file, written from `POSTMORTEM-TEMPLATE.md`.

## What counts

- Any user-visible degradation of the paging path (missed/late page,
  receiver down, forwarder dead, dead-man trip confirmed).
- Any `BROKEN: checkpoint` / event-log integrity finding.
- Any false suppress on a SEV1-class alert (RB-2 drill outcomes too).
- Any deploy that auto-rolled back with an unclear cause (the forensics
  live in the artifact dir — attach the path).
- Any near-miss where the only thing between us and the above was luck.

## Skill & Evidence

- **principal-systems operating rule 5**: the template is the rule
  implemented — Summary → Impact → Root Causes → Trigger → Resolution →
  Detection → Action Items (owner, priority, tracking) → Lessons (what
  went well / what went wrong / where we got lucky) → Timeline. Same
  lineage: Google SRE postmortem culture (blameless writeups, reviewed
  like code, shared widely; Postmortem of the Month; Wheel of Misfortune).
- **execution-doctrine §7** (every killed bet gets a written blameless
  postmortem with owned action items): the mandate to write is the
  learning mechanism, not paperwork.
- **Web:** Google SRE Book, "Postmortem Culture: Learning from Failure"
  (https://sre.google/sre-book/postmortem-culture/) and the example
  postmortem (https://sre.google/sre-book/example-postmortem/) —
  blameless mandate, the section order this template mirrors, and the
  review-and-share discipline; accessed 2026-10-05. Lunney's
  "Postmortem Action Items" (USENIX ;login:, Spring 2017) for the
  action-item taxonomy (Prevent / Mitigate / Repair / Detect / Process /
  Other), cited via the canonical SRE sources.

## Rejected alternatives (the real choice, recorded)

- **Heavier Jeli-style multi-stage process** (assign → identify → analyze
  → interview → calibrate → meet → report → distribute): rejected. Built
  for dedicated incident-response teams; our on-call rotation is two
  people and a founder-deputy. The weight would make the practice
  ceremonial — and a practice that never says no is theater
  (principal-mindset §11).
- **No written template (free-form markdown)**: rejected. Quality decays
  without the section order; unsearchable records don't compound.
  Written templates are how compound learning happens across incidents.
