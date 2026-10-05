/* synth.test.mjs — the honest simulation is deterministic and traceable.
 * Run: node --test tests/synth.test.mjs (from platform/ui/) */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createSynth, mulberry32, SEV_LABEL, DISP_LABEL } from '../assets/synth.js';

test('same seed → same history (deterministic)', () => {
  const a = createSynth(42);
  const b = createSynth(42);
  const da = a.decisions(28).map((d) => [d.disposition, d.reasonCode, d.severity, d.service]);
  const db = b.decisions(28).map((d) => [d.disposition, d.reasonCode, d.severity, d.service]);
  assert.deepEqual(da, db);
});

test('different seeds → different histories', () => {
  const a = createSynth(42);
  const b = createSynth(43);
  const da = a.decisions(28).map((d) => d.id).join(',');
  const db = b.decisions(28).map((d) => d.id).join(',');
  assert.notEqual(da, db);
});

test('every decision carries a proof with the five required fields', () => {
  const s = createSynth(7);
  for (const d of s.decisions(28)) {
    assert.ok(d.proof.rule, 'rule');
    assert.ok(d.proof.configuredBy, 'configuredBy');
    assert.ok(d.proof.detail, 'detail');
    assert.ok(d.proof.evidenceAgeMs != null, 'evidenceAgeMs');
    assert.ok(d.proof.perClass && d.proof.perClass.n > 0, 'per-class track record');
    assert.ok(['page_now', 'suppress'].includes(d.disposition), 'disposition');
    assert.ok(d.costOfInaction, 'cost of inaction (R-UI-5)');
  }
});

test('suppressions are time-bounded (R-UI-11)', () => {
  const s = createSynth(7);
  const supps = s.suppressions(100);
  assert.ok(supps.length > 0, 'seed history contains suppressions');
  for (const d of supps) assert.ok(d.proof.expiresAt > d.ts, 'suppression has an expiry');
});

test('fail-open exists: uncertain evidence pages, never suppresses', () => {
  const s = createSynth(99);
  const failopen = s.decisions(200).filter((d) => d.reasonCode === 'fail-open:uncertain');
  for (const d of failopen) assert.equal(d.disposition, 'page_now');
});

test('undo reverses a suppression', () => {
  const s = createSynth(7);
  const target = s.suppressions(100).find((d) => !d.undone);
  assert.ok(target, 'a suppression to undo');
  assert.ok(s.undoSuppression(target.id));
  assert.ok(s.decisionById(target.id).undone);
  assert.ok(!s.undoSuppression(target.id), 'second undo is a no-op');
});

test('ack moves a problem to ackd', () => {
  const s = createSynth(7);
  const p = s.openPages()[0];
  assert.ok(p, 'an open page');
  assert.ok(s.ackProblem(p.id, 'tester'));
  assert.equal(s.problemById(p.id).state, 'ackd');
});

test('why() traces every stage count to generating events', () => {
  const s = createSynth(7);
  for (const key of ['receiver', 'correlator', 'race', 'gate', 'forwarder']) {
    const w = s.why(key);
    assert.ok(w.count > 0, `${key} has events`);
    assert.ok(w.sampleEventIds.length > 0, `${key} traceable to event ids`);
  }
});

test('mulberry32 is a stable PRNG', () => {
  const r1 = mulberry32(1234); const r2 = mulberry32(1234);
  for (let i = 0; i < 10; i++) assert.equal(r1(), r2());
});

test('labels cover the contract enums', () => {
  for (const sev of ['p1_critical', 'p2_high', 'p3_medium', 'p4_low']) assert.ok(SEV_LABEL[sev]);
  assert.equal(DISP_LABEL.page_now, 'PAGE');
  assert.equal(DISP_LABEL.suppress, 'SUPPRESS');
});
