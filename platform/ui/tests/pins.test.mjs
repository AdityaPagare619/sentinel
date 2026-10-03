/* pins.test.mjs — field pins: the display-only composability mechanism.
 * Invariants: paths resolve safely (never throw); pins NEVER enter the query
 * grammar; export/import round-trips; integration labels ride in-band.
 * Run: node --test tests/pins.test.mjs  (from platform/ui/) */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  parsePinPath, resolvePath, makePin, validatePinDoc, validatePinAgainstSample,
  loadPins, savePins, exportPins, importPinsJson, pinCellsHtml, pinnedSectionHtml,
} from '../assets/pins.js';
import { isQueryableField } from '../assets/contract.js';

const decisions = JSON.parse(readFileSync(new URL('../data/decisions.json', import.meta.url))).data;
const detail = JSON.parse(readFileSync(new URL('../data/decision-detail.json', import.meta.url))).data;

test('path grammar: dot segments, numeric indices, rejects junk', () => {
  assert.deepEqual(parsePinPath('labels.cluster'), ['labels', 'cluster']);
  assert.deepEqual(parsePinPath('annotations.0.url'), ['annotations', '0', 'url']);
  assert.equal(parsePinPath(''), null);
  assert.equal(parsePinPath('a..b'), null);
  assert.equal(parsePinPath('.a'), null);
  assert.equal(parsePinPath('a b'), null);
  assert.equal(parsePinPath('a/b'), null);
  assert.equal(parsePinPath(42), null);
});

test('resolution against decision.alert — never throws', () => {
  const r = resolvePath(detail, 'labels.cluster');
  assert.equal(r.found, true);
  assert.equal(r.value, 'us-east-1-a');
  const miss = resolvePath(detail, 'labels.nope');
  assert.equal(miss.found, false);
  assert.ok(miss.why.includes('nope'), 'reason names the missing key');
  const dead = resolvePath(detail, 'labels.cluster.deeper');
  assert.equal(dead.found, false, 'dead-end on a scalar');
  const noAlert = resolvePath({ id: 1 }, 'labels.cluster');
  assert.equal(noAlert.found, false);
  /* throwing getter in the payload cannot break resolution */
  const evil = { alert: {} };
  Object.defineProperty(evil.alert, 'x', { enumerable: true, get() { throw new Error('bomb'); } });
  const er = resolvePath(evil, 'x');
  assert.equal(er.found, false);
  assert.ok(er.why.includes('unreadable'));
});

test('save-time validation warns on zero-hit paths but allows them', () => {
  const pin = makePin({ name: 'cluster', path: 'labels.cluster', integration: 'prometheus' });
  const v = validatePinAgainstSample(pin, decisions.map(d => ({ ...d, alert: detail.alert })));
  assert.equal(v.ok, true);
  const bad = makePin({ name: 'nope', path: 'nope.nope' });
  const vb = validatePinAgainstSample(bad, [{ alert: { a: 1 } }]);
  assert.equal(vb.ok, true, 'allowed — the vendor may not be in this window');
  assert.ok(vb.warnings.length > 0, 'but warned loudly');
  assert.equal(validatePinDoc({ name: '', path: 'a.b' }).ok, false);
  assert.equal(validatePinDoc(makePin({ name: 'x', path: 'a..b' })).ok, false);
});

test('THE display-only invariant: no pin path is ever a queryable field', async () => {
  const paths = ['labels.cluster', 'labels.region', 'custom_details.az', 'runbook_url', 'alert.labels.env'];
  for (const p of paths) assert.equal(isQueryableField(p), false, p);
  /* and the pins module exposes no filter/sort/query API at all —
   * check the exported function names, not comments */
  const mod = await import('../assets/pins.js');
  const fns = Object.keys(mod).filter(k => typeof mod[k] === 'function');
  for (const f of fns) assert.ok(!/filter|sort|query/i.test(f), `pins.js exports query machinery: ${f}`);
  assert.ok(fns.length > 0, 'exports exist to check');
});

test('river cells carry integration labels in-band', () => {
  const pin = makePin({ name: 'cluster', path: 'labels.cluster', integration: 'prometheus' });
  const html = pinCellsHtml(detail, [pin]);
  assert.ok(html.includes('us-east-1-a'), 'value rendered');
  assert.ok(html.includes('prometheus'), 'integration in-band');
  assert.ok(html.includes('display only, not a filter'), 'display-only stated in the title');
  const unlabeled = pinCellsHtml(detail, [makePin({ name: 'c', path: 'labels.cluster' })]);
  assert.ok(unlabeled.includes('unlabeled'), 'missing integration is labeled, not hidden');
  const miss = pinCellsHtml(detail, [makePin({ name: 'x', path: 'nope.nope' })]);
  assert.ok(miss.includes('—'), 'unresolvable pin renders — with reason');
});

test('evidence section states paths and the display-only rule', () => {
  const pin = makePin({ name: 'cluster', path: 'labels.cluster', integration: 'prometheus', slot: 'evidence' });
  const html = pinnedSectionHtml(detail, [pin]);
  assert.ok(html.includes('Pinned fields'), 'section header');
  assert.ok(html.includes('labels.cluster'), 'path stated');
  assert.ok(html.includes('never filter, sort, or recolor'), 'the invariant in prose');
  assert.equal(pinnedSectionHtml(detail, []), '', 'no pins → no section');
  assert.equal(pinnedSectionHtml(detail, [{ ...pin, slot: 'river' }]), '', 'river pins do not leak into evidence');
});

test('export/import round-trips; invalid docs reported, never silently dropped', () => {
  savePins([]);
  const pin = makePin({ name: 'cluster', path: 'labels.cluster', integration: 'prometheus' });
  savePins([pin]);
  const json = exportPins();
  savePins([]);
  const r = importPinsJson(json);
  assert.equal(r.ok, true);
  assert.deepEqual(r.imported, ['cluster']);
  assert.equal(loadPins().length, 1);
  const bad = importPinsJson(JSON.stringify({ pins: [{ name: 'x', path: 'a..b' }, pin] }));
  assert.equal(bad.ok, false, 'invalid doc reported');
  assert.ok(bad.errors.length > 0);
  assert.equal(bad.imported.length, 1, 'valid doc still imported');
  assert.equal(importPinsJson('not json').ok, false);
  savePins([]);
});
