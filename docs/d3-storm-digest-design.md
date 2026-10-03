# D3 — Storm-aggregate digest path: design

ADR-016. Status: implements the ratified delta ("separate code path, not a
flag") for the storm-*declaring* aggregate.

## The hole

`Pipeline._triage` routed the storm-declaring aggregate through
`gate.evaluate` — the full race + triple lock. A suppress-shaped aggregate
(all locks green) could SUPPRESS the single page that represents the entire
storm: the worst possible outcome of a suppression decision (one bad call
silences N alerts). The delta's "separate code path" was not what shipped.

## The fix (structural, not a flag)

1. **`sentinel.storm_digest.storm_digest_disposition(*, storm_size,
   storm_counts)`** — the ONLY disposition constructor for the aggregate
   path. It takes **no `action` parameter**: the signature cannot express
   suppression. `suppress` is unreachable by construction, not by a boolean
   a future edit can invert. Returns `page_now` / `storm_digest`.

2. **`Gate.digest_storm(agg, agg_state, *, storm_size, storm_counts)`** —
   orchestration for the aggregate. It NEVER calls `evaluate_policy`,
   never arms a race, never consults freshness. It:
   - computes the disposition via `storm_digest_disposition`,
   - emits `decision_made` (disposition `page_now`, budget_outcome
     `structural_passthrough` — no race ran),
   - submits the advisory Jev call for root-cause candidates,
   - records a `DecisionRecord` in the audit log.

3. **`RaceRunner.submit_advisory(*, state, questions, on_answer)`** — the
   aggregate's Jev call, detached. Returns True/False (submitted/shed);
   never raises. The answer feeds `storm_root_cause_payload` and NOTHING
   else: there is no disposition for it to gate. A shed advisory drops an
   enrichment, never a page.

4. **`storm_root_cause_payload`** — advisory event on the gate's emission
   channel, marked `"advisory": True, "gates_disposition": False`. It is
   NOT a durable EventLog event type (the event-log lane owns that schema;
   an advisory enrichment is not a decision record).

5. **Receiver reroute** — `_triage`'s storm-declaring branch calls
   `gate.digest_storm(...)` instead of `gate.evaluate(...)`. The aggregate
   forward is unconditional: the digest pages by construction.

## The rename

`Disposition(action="suppress", reason="storm")` (storm-continuation fold)
read as *model-driven suppression* to a 3 AM operator. It is now
`action="folded"`: folded into the aggregate page, no individual forward.
Consumers updated: receiver triage (`not in ("suppress", "folded")`
⇒ forward), forwarder (folded ⇒ not forwarded, counted under a separate
`folded` metric — NOT `suppressed`), eventlog `DISPOSITIONS` frozenset,
`models.Disposition` action comment.

## What this deliberately does NOT do

- The digest path does not consult freshness (D1): a storm aggregate pages
  regardless of evidence state. Uncertainty pages — Law 7.
- The digest path does not consult the allowlist: an allowlisted
  fingerprint inside a storm still pages via the aggregate. The aggregate
  is a different decision object than its members.
- No change to the storm-*continuation* fold beyond the rename: members
  still fold without a Jev call.

## Review order

D1 lands first. D3 is independent of D1 (the digest path never calls the
kernel, so freshness wiring cannot interact with it), but D1 is the
smaller change and its review should complete first.

## Tests

`tests/test_storm_digest.py`:
- digest disposition signature cannot express suppression;
- all-locks-green aggregate (allowlisted, green Jev answers) still pages;
- `digest_storm` never routes through `evaluate_policy` (gate.evaluate
  monkeypatched to raise);
- suppress-shaped advisory answers never change the disposition;
- shed advisory still pages;
- receiver `_triage` storm-declaring branch pages via the digest.
`test_storm_continuation_suppresses_without_jev` renamed to
`test_storm_continuation_folds_without_jev` (folded, no Jev call).
