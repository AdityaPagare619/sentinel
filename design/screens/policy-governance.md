# Screen: Policy Governance — which rules are live, and who attested them

**Function code:** `POL` · **Route:** `/policy`
**API:** `GET /api/policy/current` · `GET /api/policy/versions` ·
`GET /api/policy/ceremony-log` · `GET /api/policy/shadow-divergence`

**Purpose:** The revision's damning finding (R-1): the attested policy
lifecycle governed a policy object the kernel never read, while an
unattested `thresholds.json` edit actually moved the numbers. This screen
is the governance made visible: *which policy version is live right now,
is it attested, who signed it, when does it expire, and what stands between
a 3 AM operator and widening the suppress gate.* The console never edits
policy (WORKFLOW 4 refusal — the UI reads, the CLI ceremony writes). Its
job is to make the ceremony's state un-ignorable.

---

## 1. The operator's question

*"What policy is the gate enforcing right now, and can I trust that no one
changed it behind my back?"*

Secondary: *Is the live version attested or expired? What changed in the
last version, and who signed off? Is a new version in shadow/canary, and
what does it disagree about?*

## 2. The live card (above the fold)

One card, the whole truth about the current generation:

```
LIVE  ·  policy v14  ·  content sha256:9f2c…a41d
attested by 2 of 2  ·  expires in 5d 14h  ·  suppress reachable
thresholds: suppress_gate 0.70 · sev1_floor 0.95 · flap_window 300s
kernel binding: VERIFIED (ConfigLoader refused 0 unattested generations in 24h)
```

Each element is load-bearing:

- **Version + content hash.** The hash is the identity — the kernel binds
  to content, not to a version number (R-1 option (a): `PolicyVersion.content`
  is canonical). Two versions with the same number are a finding, not a
  coincidence.
- **Attestation state.** `attested by 2 of 2` with the signers' identities
  and timestamps. If the live version is `REVIEW_DUE` or `EXPIRED`, the
  card says so in warning/critical register — and, crucially, *what the
  kernel does about it*: `EXPIRED` ⇒ suppress unreachable ⇒ pages. The
  screen states the fail direction, always.
- **Expiry countdown.** Policies are mortal. The countdown is the
  anti-pre-mortem device: the six-week silent widening cannot happen to a
  policy whose death date is on the wall.
- **Kernel binding.** `ConfigLoader refused N unattested generations` —
  the mechanism's heartbeat. This is the R-1 verification rendered as a
  live number: the day it reads anything other than the expected count,
  someone tried to route around the ceremony, and the console names it.
- **The thresholds themselves**, in plain numbers. No "optimized", no
  "AI-tuned". The highest-leverage numbers in the system are displayed,
  not summarized.

## 3. The lifecycle lane (below the card)

P-LIB-3 lineage: the B3 state machine
(draft → shadow → canary → live → review-due → expired) rendered as a
horizontal lane, one lane per policy version in flight. A version in
`shadow` shows its divergence numbers inline (agreement rate vs the live
policy, disagreements with bilateral evidence links — the S5 shadow data
the attestors actually review). A version in `canary` shows the canary
bounds (severity band, traffic share).

The lane makes the ceremony's *pipeline* visible the way a CI pipeline
view makes build state visible: no version can be live without having
passed through shadow and canary, and the screen shows the receipts.

**The 3 AM rule, stated on the screen:** *"Thresholds cannot be changed
here. The console reads policy; it never writes it. The change path is the
attested CLI ceremony. At 3 AM, your levers are the fail-open ladder
(link), storm digest, and the kill switch (link) — all of which page more,
never suppress more."* This is WORKFLOW 4's step 7, permanently mounted
where the temptation would live.

## 4. The change ledger

Every version transition is a hash-chained ceremony event: draft created
(author), shadow entered, canary entered, attested (signer identities,
bound `(policy_id, version, content_hash, from_state, to_state,
decided_at)`), went live, review-due, expired. Rendered like the kill
switch's flip ledger (P-LIB-3): who, what, when, with the binding hash.
The author can never be an attestor — the screen enforces the display of
that separation (if author == attestor on any transition, it renders as a
finding, because the ceremony is broken).

## 5. The counterfactual anchor

Each live version links to its simulator projection: *"v14 was simulated
against the last 7 days before going live: would have suppressed 312,
would have paged 41, 2 flips vs v13 (links)."* The simulator taste from
onboarding generalizes here — the governance screen carries the receipt
that the change was previewed (P-LIB-4's routing-preview pattern: no rule
touches production without a preview against recent decisions).

## 6. Professional-software lineage

| P-LIB pattern | What it teaches this screen | Why it fits |
|---|---|---|
| P-LIB-4 Grafana routing-preview | Every version shows its pre-live simulation receipt before it can be trusted. | Governance without a preview is vibes; the preview is the attestors' evidence. |
| P-LIB-3 Opsgenie lifecycle log | The ceremony is a lifecycle; every transition is "who did what, when", inline. | The 3 AM widening died because transitions were invisible JSON edits. Visibility is the control. |
| P-LIB-1 PagerDuty closed vocabulary | Policy states are a closed enum (draft/shadow/canary/live/review-due/expired), never free text. | A shared, auditable language for the thing that decides who gets woken. |

## 7. What it must NEVER show (anti-fatigue rules)

1. **Never a threshold input.** No text box, no slider, no "edit" button.
   The console is read-only for policy (WORKFLOW 4's first refusal). A
   threshold input in the console is governance theater with a text box —
   it suggests a capability that must not exist here.
2. **Never "policy healthy" as the headline.** The headline is the version,
   the attestation state, and the expiry countdown. "Healthy" is the proxy
   that rank-orders with nothing (the R-1 wound was a healthy-looking
   governance story over unattested numbers).
3. **Never the ceremony as a progress bar without the bindings.** A lane
   that shows "shadow → canary → live" as three green dots without the
   attestation bindings and content hashes is a CI theater widget. The
   receipts are the screen.
4. **Never hide an expired or unattested live version.** If the live
   version is `EXPIRED` or the kernel is refusing generations, that is the
   whole screen — critical register, above everything. Degraded governance
   is the highest-severity console state there is.
5. **Never show policy content without its hash.** The numbers without the
   binding are just numbers. The hash is what makes "this is what the
   kernel evaluates" a checkable claim.

## 8. States

- **Empty (no attested version):** *"No attested policy version exists.
  The kernel is paging everything (fail-closed). Run the CLI ceremony to
  draft v1."* — the D8 fail-closed semantics rendered as the onboarding
  state, not as an error.
- **Loading:** live card skeleton first (version + state), then the lane.
- **Kernel-binding mismatch:** critical register — *"ConfigLoader refused
  N generations not covered by an attested version in the last hour"* —
  plus the page to the platform team. Someone is editing around the
  ceremony.

## 9. API mapping

| UI need | Endpoint |
|---|---|
| live card | `GET /api/policy/current` (version, hash, attestation, expiry, thresholds, kernel-binding count) |
| lifecycle lane | `GET /api/policy/versions` (state machine positions) |
| change ledger | `GET /api/policy/ceremony-log` (hash-chained transitions) |
| shadow divergence | `GET /api/policy/shadow-divergence` (agreement rate, disagreement evidence) |

Read-only. The policy ceremony lives in the operator CLI; the console
verifies and displays. (Cross-workflow refusal 1: the console never
evaluates, never changes policy.)
