# Screen: Audit Explorer — search the machine's memory, investigate its mistakes

**Function code:** `AUDIT` · **Route:** `/audit`
**API:** `GET /api/decisions?fingerprint=&team=&action=&from=&to=` ·
`GET /api/decision/<id>` · `GET /api/analytics/flips?window=7d` ·
`GET /api/analytics/noise?window=24h`

**Purpose:** The evidentiary surface. Postmortems, flip investigations,
compliance questions, and plain curiosity all land here. This is where
Freedom 4 becomes concrete: *"Audit the machine's judgment, not just its
output."* Every automated claim Sentinel ever made is searchable, replayable,
and challengeable from this screen.

---

## 1. Search — find the decision

The search bar is the screen. It accepts, in one input:

- **Fingerprint:** `fpr:9f2c…` or a raw prefix — exact match on the alert
  fingerprint. The primary postmortem entry point ("this alert paged us —
  what did the gate think?").
- **Input hash:** `in:sha256:7d3a…` — finds every decision evaluated on the
  identical input. This is the flip hunter's query: one input, multiple
  decisions, different answers.
- **Free text:** service name, reason code, team.
- **Time bounds:** `from=`/`to=` pickers with presets (`last=1h|24h|7d|30d`).
- **Facets** (left rail): team · disposition · reason code · data source ·
  `flipped only` toggle · `labeled only` toggle.

Results render as river rows (same anatomy — the audit explorer reuses the
river's row component, not a second design). Clicking a row opens the same
detail drawer as the river: verdict · evidence · timeline · provenance · raw.

## 2. Input hashes — what they cover, and why

Each decision carries `input_hash = sha256(...)` over the canonicalized gate
input: alert payload fields, correlator episode state, thresholds in force,
tuner version. The drawer shows:

```
input   sha256:7d3a91f2…c44e
covers  alert fields (12) · episode state · thresholds v14 · tuner v0.3.1
[copy]  [find all decisions on this input]
```

`[find all decisions on this input]` is the flip investigation in one click:
it re-queries with `in:sha256:7d3a…` and, if answers differ, the screen
switches to **timeline mode** (see §3). Hashes make "the machine changed its
mind" a falsifiable, linkable claim — not a feeling.

## 3. Flip investigation timeline

When ≥2 decisions share an input hash with different dispositions or
materially different confidences (Δ > 0.15), the result view offers
`view as timeline`. The timeline is vertical, oldest → newest:

```
02:14:07  SUPPRESS · 0.87   thresholds v14 · tuner v0.3.1
            │ input identical (sha256:7d3a…c44e)
02:14:52  PAGE     · 0.41   thresholds v14 · tuner v0.3.1   ← FLIP
            evidence: Jev answer changed Choice:suppress → Choice:page
            confidence collapsed 0.87 → 0.41 (Δ 0.46)
```

Each flip event annotates **what changed**: same input + same thresholds +
same tuner but a different Jev answer = model non-determinism (our measured
band: 1.3–2.2%); same input but different thresholds = a policy change
explains it (link to the policy diff). The timeline never speculates beyond
what the hashes and versions establish. Unknown cause renders as
*"cause undetermined — input, thresholds, and tuner identical; flagged for
platform review"* — honesty over narrative.

## 4. Noise analytics entry

`GET /api/analytics/noise?window=24h` feeds a summary strip above results:

```
last 24h · 3,204 decisions · 71% suppressed
top absorbers: flap-debounce 38% · self-clear<5m 22% · deploy-churn 11%
```

Each absorber is a link that applies the reason facet. This is the
"did my pages really need me?" question, answered with counts, not claims —
the post-hoc evidence that suppression is earning its keep (or not).

## 5. Export — the audit pack

`Export audit pack` (top right) bundles the current result set:

- decisions (JSON, full records incl. input hashes and reason codes)
- the query that produced them (reproducible: paste the URL back, get the
  same set — dataset version pinned)
- provenance header: dataset version, exporter, timestamp, data-source badge

The pack is the postmortem artifact: attach it to the incident doc and the
"what did the gate do?" question is answered with evidence, not memory.

## 6. States

- **Empty:** per design-system §5.1 — names the query, suggests widening or
  checking the receiver if the alert never reached the gate.
- **Loading:** badge + dataset version first, then skeleton rows.
- **No flip found:** *"One decision on this input — no flip. The machine has
  been consistent here."* Consistency is reported as a finding, not assumed.

## 7. API mapping

| UI need | Endpoint |
|---|---|
| search / facets | `GET /api/decisions?fingerprint=&team=&action=&from=&to=` |
| drawer | `GET /api/decision/<id>` |
| flip rate strip | `GET /api/analytics/flips?window=7d` |
| noise strip | `GET /api/analytics/noise?window=24h` |

Read-only. The audit explorer can never alter a decision — it can only
establish what happened.
