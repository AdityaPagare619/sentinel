/* components.test.mjs — pure rendering contracts for the shared components.
 * B1: severity is signal bars in neutral ink (decision keeps the color channel).
 * A5: river row titles carry UTC + local. Drift rows never render as decisions.
 * Run: node --test tests/components.test.mjs  (from platform/ui/) */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sevChip, driftRow, decisionRow } from '../assets/components.js';

test('B1: severity renders as signal bars in neutral ink', () => {
  const h = sevChip('p1_critical');
  assert.ok(h.includes('sev-bar'), 'bars present');
  assert.equal((h.match(/sev-bar on/g) || []).length, 4, 'SEV1 fills 4 bars');
  assert.ok(!h.includes('var(--sev-1)') && !h.includes('FF5470'), 'no severity hue — color stays on the decision channel');
  assert.ok(h.includes('aria-label="severity SEV1"'), 'text equivalent: never color-only (§7)');
  const p2 = sevChip('p2_high');
  assert.equal((p2.match(/sev-bar on/g) || []).length, 3, 'SEV2 fills 3 bars');
  const noise = sevChip('known_noise');
  assert.equal((noise.match(/sev-bar on/g) || []).length, 0, 'NOISE fills no bars');
  assert.ok(noise.includes('NOISE'), 'label still present');
  const unk = sevChip('cannot_determine');
  assert.ok(unk.includes('SEV-?'), 'unknown severity labeled, not blank');
});

test('drift rows never render as decisions', () => {
  const h = driftRow({ id: 7 }, ["contract drift: missing required field 'confidence'"]);
  assert.ok(h.includes('not rendered as a decision'), 'honest state');
  assert.ok(h.includes('missing required field'), 'errors listed in-band');
  assert.ok(h.includes('role="alert"'), 'announced to assistive tech');
});

test('A5: river row title carries UTC + local, in-band', () => {
  const d = {
    id: 1, time: '2026-10-04T12:04:11Z', severity: 'p1_critical',
    disposition: 'page_now', reason: 'triple-lock', confidence: 0.9,
    service: 'redis-cache', team: 'data',
  };
  const h = decisionRow(d, {});
  assert.ok(h.includes('12:04:11 UTC ·'), 'dual-zone in the row title');
  assert.ok(h.includes('sev-bar'), 'row uses the bar treatment');
});
