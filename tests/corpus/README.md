# Adversarial corpus — instruction firewall (ADR-020 / D6)

**Owner:** Vault · **Red-team:** Tripwire (quarterly) · **Version:** 1.0.0

## What this is

The versioned attack corpus behind the ASR (attack-success-rate) gate for
`sentinel.firewall`. Every attack case MUST be flagged by `screen()`; every
benign case MUST pass clean. The gate test (`tests/test_firewall_corpus_gate.py` —
run by the local gate, `scripts/ops/pre-pr-gate.sh`)
fails the build on any bypass (ASR > 0) or any false positive (FPR > 0).

## Layout

- `manifest.json` — corpus version, owner, red-team, cadence. The gate test
  asserts `manifest.version == sentinel.firewall.CORPUS_VERSION`; a mismatch
  fails loudly (stale config caught automatically, never silently).
- `adversarial_corpus.json` — the cases. Schema `sentinel-firewall-corpus/1`:
  each case has `id`, `category`, `field`, `text`, `expect_flagged`,
  `expect_detectors` (for attack cases: at least one listed detector must
  fire; for benign cases: empty).

Case counts (v1.0.0): 59 attack + 30 benign = 89 total.

| Prefix | Category | n |
|---|---|---|
| IP | instruction_phrase | 15 |
| FR | fake_resolve | 14 |
| DS | delimiter_smuggling | 12 |
| UL | unicode_lookalike | 12 |
| C | composite (multi-shape) | 6 |
| B | benign (must stay clean) | 30 |

## Versioning

- **Minor bump** (1.0 → 1.1): new cases only, same schema. Bump both
  `manifest.json` and `firewall.CORPUS_VERSION` in one commit.
- **Major bump** (1.x → 2.0): schema change. Update the loader in the gate
  test in the same commit.

## Adding a case

1. Give it the next ID in its category prefix.
2. Write the attack in the attacker's voice — a *new style*, not a paraphrase
   of an existing case. The corpus is only as adversarial as its variety.
3. Run `python -m pytest tests/test_firewall_corpus_gate.py -q`. If the case
   bypasses, the screen is wrong — fix the detector, not the case.
4. Benign cases: add any legit alert text that a detector change newly
   threatens (the FPR gate is the false-positive contract).

## Honest limitations (Tripwire's mirror objection)

A corpus written by the team that wrote the firewall is a mirror, not an
adversary. This corpus proves the screen catches *known shapes*; it cannot
prove it catches *unknown* ones. The counterweights, all named in ADR-020:

1. Tripwire red-teams the corpus quarterly with held-out attack styles.
2. Detectors target shapes (imperative verbs, structural markers, role
   delimiters, character-level obfuscation), not memorized strings.
3. Flag-rate alarm (`FlagRateAlarm`) + the shadow-report ASR metric watch
   production for what the corpus missed.

If a real bypass is found anywhere, it lands here as a case first, then the
detector is fixed — corpus first, code second.
