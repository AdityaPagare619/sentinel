# Sentinel — Current Plan (2026-10-08)

The single current plan. Supersedes `ROAD_TO_LAUNCH.md` and `ROADMAP.md`
(Oct 4 targets, marked superseded at root).

## Where we are

- **Open source (MIT).** No pricing, no billing — ever. (Aditya's decision.)
- **v0.1.0 cut.** See `CHANGELOG.md`. Honest scope: the deterministic
  page-or-suppress engine, three consoles, the sim harness, full gates.
- **Launch-readiness: NOT YET.** External committee review (2026-10-08):
  3× NOT YET, 1× SHIP WITH CONDITIONS. Full report:
  `committee/EXTERNAL_COMMITTEE_2026-10-08.md`.
- **Live surfaces:** production console, staging simulation, load-test
  dashboard (gh-pages); platform API on Vercel.

## What gates real users (ranked)

1. **Shadow pilot** — 2 weeks on real labeled traffic; publish the
   suppression report (false-suppress rate on real SEV1s). This is the gate
   every reviewer dead-ends on. Harness is built
   (`docs/planning/shadow-pilot/`); traffic is not.
2. **Engine's production home** — the deployed tier has no paging path; the
   kill flag there guards nothing. A receiver must be deployed somewhere real.
3. **First real page** — zero real PagerDuty pages ever sent. One controlled,
   labeled test page through the real stack.
4. **Key wiring** — `TYPESAFE_API_KEY` provisioned; `key_configured` health
   read fixed to check env; judge-down mode defined in code or boot-failure
   made explicit.
5. **BYOK onboarding** — the path for a real user to connect their PagerDuty.
6. **DurableForwarder** wired into the paging path (legacy forwarder ships
   today: any-2xx, no outbox/retry).
7. **Support story** — community/issues frame, status visibility.
8. **Self-hosting trivial** — one-command deploy + docs (open-source
   distribution is self-host by design).

## What is NOT on the plan

- Pricing, billing, hosted tier, sales. (Open source.)
- Production flip before the shadow-pilot report exists.

## Near-term sequence

1. Shadow-pilot harness proven runnable against sim fixtures. ← now
2. Design partner identified; 2-week shadow run on their traffic.
3. Suppression report published → re-run launch-readiness review.
4. Engine production home + first real page + key wiring (in any order
   the pilot allows).
