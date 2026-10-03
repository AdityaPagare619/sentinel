# Screen: Onboarding — from zero to first suppression in 15 minutes

**Function code:** `START` · **Route:** `/start`

**Purpose:** A design partner goes from "I've heard of Sentinel" to "I
watched it suppress a page and understood why" in fifteen minutes, with
their own API key, on synthetic data, having touched every P0 surface. The
flow is the product's first impression of its honesty: we show the machine
being *wrong* on purpose before we show it being right.

**Entry condition:** none. No signup, no sales call. The flow assumes a
skeptical SRE with an alert source and fifteen minutes.

---

## 1. The 15-minute arc

| Min | Step | What happens | What the user learns |
|---|---|---|---|
| 0–2 | **BYOK connect** | Paste TypeSafe API key → `Verify` fires a live typed-answer call (`Choice`, test question) and shows the raw response: disposition, confidence, latency. The key is stored per the vault flow and never displayed again. | "It's really my key, really their API — and I can see the latency myself." (Honest flag from our own measurement: first-call latency vs spec is shown, not hidden.) |
| 2–5 | **Connect an alert source** | Webhook URL + a `Test` button that fires a synthetic alert through receiver → correlator → gate → forwarder, displayed as a live trace. Or: "skip, use the demo source." | The pipeline is four named stages, and I can watch my data move through it. |
| 5–10 | **Guided synthetic storm** | One click launches a 60-second synthetic storm: ~40 alerts, flapping services, a deploy-churn burst, two genuine SEV1s. The river fills live. The user watches suppressions happen with reason codes attached (`flap-debounce`, `self-clear<5m`, `deploy-churn`) and the two SEV1s page. | The machine suppresses *with receipts*, and it still pages the real fires. |
| 10–13 | **First suppression, explained** | The flow pauses on one suppressed decision and walks the drawer: the confidence bar (marker vs Q3 vs threshold), the reason code, the input hash, the timeline. Then it shows the counterfactual: *"At your current thresholds this paged 0 humans. Drag the SEV2 slider to 0.95 and watch it page."* — a 10-second simulator taste. | Freedom 1 and 2 in miniature: I can see why, and I can ask what-if. |
| 13–15 | **Set your thresholds** | The simulator opens prefilled with the storm's shadow data. The user drags one slider, watches the projection reprice, and exports their first policy diff (or skips). The flow ends with the river, live, on their data. | My team's tuning is mine, reversible, and evidenced. |

## 2. Design details

- **Progress is a timeline, not a checklist.** A slim top progress line
  shows `key → source → storm → why → tune` with the current stage named.
  The user can go back; nothing is destructive, so nothing needs confirming.
- **The storm is honest.** Before it runs, a card states: *"Synthetic data.
  The two SEV1s are scripted to page. Suppression counts here prove the
  pipeline works, not that the model is good — calibration is measured on
  shadow data, in the CAL screen."* (Laws L2, L3 — the demo never markets.)
- **Deliberate wrongness.** During the storm, one alert is *designed* to
  flip (same input asked twice, different answer). The flow surfaces it:
  *"This one changed its mind — 1.8% of inputs do. Here's the flip timeline."*
  Trust is built by showing the failure mode on day one, not by hiding it.
- **Skip paths everywhere.** Every step has `skip` — a senior SRE who
  already trusts the architecture can be at the river in 90 seconds. The
  flow never punishes expertise.
- **Exit state.** Completing (or skipping) lands on the river with a
  dismissible banner: *"Storm complete: 38 suppressed, 2 paged, 1 flip.
  Your thresholds are unchanged — tune them in SIM when you're ready."*
  Nothing is auto-applied. Ever.

## 3. Copy tone

Short sentences. No exclamation marks. No "supercharge", "AI-powered",
"seamless". The product speaks like the senior engineer who built it:
precise, a little dry, allergic to marketing. Example error copy:
*"Key verification failed (HTTP 401 from TypeSafe). Check the key — nothing
was stored."*

## 4. Success criteria (what "done" means for this screen)

- Median time-to-first-suppression-explained ≤ 15 min (measured, not hoped).
- ≥ 80% of guided runs reach the simulator taste step (instrumented).
- Zero support tickets of the form "is this real data?" — the source badges
  and storm-honesty card must make it unaskable.

## 5. States

- **Key verify fails:** inline error, key not stored, retry offered. The
  flow does not advance on an unverified key.
- **Storm interrupted (tab closed):** state persists per step; reopening
  `/start` resumes where the user left off, with completed steps marked.
- **No alert source connected:** the demo source is the default, not a
  dead end — the flow is fully completable on synthetic data.
