# TICKET — build_showcase.py fails the entrypoint-import gate

**Filed:** 2026-10-07 · **Owner:** TEAM 6 (quality/test) · **Status:** OPEN (quarantined)
**Quarantine ref:** `scripts/ops/gate-entrypoints.quarantine`

## Symptom

`design/showcase/build_showcase.py` raises at import time:

```
AssertionError: tokens missing from tokens.css: ['line-0', 'tx-0', 'tx-dim', 'disp-page', 'disp-supp', ...]
```

The module asserts at import (line 33) that `platform/ui/assets/tokens.css`
contains every token the showcase references. The CSS drifted; the assertion
didn't.

## Why quarantined, not fixed now

- Nothing in the repo imports or invokes `build_showcase.py` (one-off from
  PR #40, "Sunday design showcase"). It is dead code with a live assertion.
- Fixing it means either updating the token list (design-lane call) or
  deleting the file (a lane decision about the showcase artifact).
  Both are another lane's judgment; the quality lane doesn't unilaterally
  delete design-lane artifacts.

## Options for the owner

1. Update the asserted token list to the current `tokens.css` (if the
   showcase is ever rebuilt).
2. Delete `design/showcase/build_showcase.py` if the showcase is retired
   ("code is a liability" — principal-systems).
3. Move the assertion from import time into `main()` so the module at
   least imports (weakest option — hides the drift).

## Gate behavior until closed

The entrypoint gate reports this module as QUARANTINED (not PASS) and exits
0 only while this ticket file exists. If the module starts passing, remove
the quarantine line. If this ticket is deleted while the module still fails,
the gate goes RED.
