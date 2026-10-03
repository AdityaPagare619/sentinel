/* lib.test.mjs — unit tests for assets/lib.js (pure functions, no DOM).
 * Run: node --test tests/lib.test.mjs  (from platform/ui/) */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  SEV_LABEL, DISP_LABEL, fmtInt, fmtPct, ageStr, shortFpr, parseHash, routeHref,
  stripRiver, stripCal, stripSim, stripAudit, stripStart,
  gloss80, overconfidentBins, calVerdict, quartilesFromBins, receiptLine,
  verdictSentence, applyThresholds, DEFAULT_THRESHOLDS, policyDiffYaml, branchName,
  startPlan, jevStateFromParams,
} from '../assets/lib.js';
import { readFileSync } from 'node:fs';

const bins = JSON.parse(readFileSync(new URL('../data/calibration.json', import.meta.url))).data.bins;
const decisions = JSON.parse(readFileSync(new URL('../data/decisions.json', import.meta.url))).data;

/* display mappings follow the contract enums */
test('severity/disposition labels cover the contract enums', () => {
  for (const s of ['p1_critical','p2_high','p3_medium','p4_low','known_noise','cannot_determine'])
    assert.ok(SEV_LABEL[s], s);
  for (const d of ['page_now','page_business_hours','suppress','passthrough'])
    assert.ok(DISP_LABEL[d], d);
  assert.equal(SEV_LABEL.p1_critical, 'SEV1');
  assert.equal(DISP_LABEL.page_now, 'PAGE'); /* red follows the disposition */
});

/* formatters */
test('formatters', () => {
  assert.equal(fmtInt(4096), '4,096');
  assert.equal(fmtPct(0.017), '1.7%');
  assert.equal(shortFpr('a1b2c3d4e5f60718'), 'fpr:a1b2·c3d4');
  assert.match(ageStr(new Date(Date.now() - 90_000).toISOString()), /1m ago/);
});

/* routing */
test('routeHref deep links', () => {
  assert.equal(routeHref('river', { team: 'data', reason: 'triple-lock' }),
    '#/river?team=data&reason=triple-lock');
});

/* strip grammar — machine-generated, fixed slots (narrative §5) */
test('strip grammar', () => {
  assert.match(stripRiver({ nPages: 0, nSuppress: 12, windowLabel: '24h' }), /Nothing on fire/);
  assert.match(stripRiver({ nPages: 2, nSuppress: 5, windowLabel: '1h' }), /2 pages in the last 1h/);
  assert.match(stripRiver({ apiDown: true, apiMsg: 'GET /api/decisions → 503', newestIso: '2026-10-02T18:51:12Z' }),
    /The gate is unaffected/);
  assert.match(stripCal({ ece: 0.031, n: 1840 }), /ECE 0\.031 \(n=1,840\)/);
  assert.match(stripCal({ n: 87, thin: true }), /provisional/);
  assert.match(stripSim({ pageNow: 96 }), /woken 96 times/);
  assert.match(stripSim({ thin: true, n: 87 }), /refusal is the feature/);
  assert.match(stripStart({ mode: 'live' }), /Shadow mode/);
  assert.match(stripStart({ mode: 'recorded' }), /recording/);
});

/* calibration glosses are generated from the actual bins */
test('calibration math on the mock report', () => {
  assert.match(gloss80(bins), /77|74|75|76|78|79|80|81/); /* bin 8 ci95_lo..hi */
  assert.equal(overconfidentBins(bins).length, 2);
  assert.match(calVerdict({ team: 'data', ece: 0.031, bins }), /healthy.*2 bins are overconfident/);
  const qs = quartilesFromBins(bins);
  assert.equal(qs.n, 1840);
  assert.ok(qs.q1 <= qs.q2 && qs.q2 <= qs.q3 && qs.q1 < qs.q3, 'quartiles monotone (q1==q2 is correct for this left-skewed distribution)');
});

/* counterfactual receipt is derived, labeled, never an event field */
test('receiptLine', () => {
  const d = decisions.find(r => r.disposition === 'suppress');
  const line = receiptLine(d, DEFAULT_THRESHOLDS);
  assert.match(line, /stands down/);
  assert.match(line, /would page below 0\.90/);
  assert.equal(receiptLine({ ...d, disposition: 'page_now' }, DEFAULT_THRESHOLDS), null);
});

/* verdict sentences are generated from reason + evidence, never LLM prose */
test('verdictSentence', () => {
  const s = verdictSentence(decisions[0]);
  assert.ok(s.includes(decisions[0].reason), 'carries the reason code');
  assert.ok(!/accurate/i.test(s), 'no accuracy marketing (Law L3)');
});

/* mock-mode local simulate recompute is deterministic and sane */
test('applyThresholds', () => {
  const p1 = applyThresholds(decisions, DEFAULT_THRESHOLDS);
  const p2 = applyThresholds(decisions, DEFAULT_THRESHOLDS);
  assert.deepEqual(p1, p2);
  assert.equal(p1.n_alerts, decisions.length);
  assert.equal(p1.suppress + p1.page_now + p1.queue + p1.baseline, decisions.length);
  assert.ok(p1.suppress_rate >= 0 && p1.suppress_rate <= 1);
  const loose = applyThresholds(decisions, { ...DEFAULT_THRESHOLDS, suppress_conf_min: 0.5 });
  assert.ok(loose.suppress >= p1.suppress, 'lowering the floor suppresses at least as much');
});

/* policy diff export */
test('policyDiffYaml', () => {
  const yaml = policyDiffYaml({
    team: 'data', oldT: DEFAULT_THRESHOLDS,
    newT: { ...DEFAULT_THRESHOLDS, suppress_conf_min: 0.95 },
    projection: { suppress: 5, page_now: 3, exp_false_suppresses: 0.2 },
    provenance: { tuner_rev: 't', policy_version: 'p', dataset_sha256: 'ab'.repeat(32) },
    datasetVersion: 'labels-v3',
  });
  assert.match(yaml, /team: data/);
  assert.match(yaml, /0\.900 → 0\.950/);
  assert.match(yaml, /Nothing is applied by the simulator/);
  assert.match(branchName('data'), /^policy\/data-thresholds-/);
});

/* DRILL 7: ?jev=dead → the designed recorded-walkthrough fallback */
test('drill7: jevStateFromParams', () => {
  assert.equal(jevStateFromParams(new URLSearchParams('jev=dead')), 'dead');
  assert.equal(jevStateFromParams(new URLSearchParams('jev=live')), 'live');
  assert.equal(jevStateFromParams(new URLSearchParams('')), 'unknown');
});
test('drill7: degraded plan refuses to fake liveness', () => {
  const plan = startPlan('dead');
  assert.equal(plan.degraded, true);
  assert.match(plan.banner, /recorded walkthrough/i);
  assert.match(plan.banner, /real, they just are not yours/);
  const key = plan.steps.find(s => s.id === 'key');
  assert.match(key.body, /NOT stored/i);
  assert.match(key.body, /NOT verified/i);
  const storm = plan.steps.find(s => s.id === 'storm');
  assert.match(storm.body, /RECORDED STORM/i);
  assert.match(storm.body, /prove the pipeline works, not that the model is good/);
  assert.equal(plan.flipBeat.mode, 'recorded');
  assert.match(plan.flipBeat.label, /RECORDED FLIP/);
  /* the other steps are unaffected — the tour continues */
  assert.equal(plan.steps.filter(s => !s.degraded).length, 3);
});
test('drill7: live plan has no degraded copy', () => {
  const plan = startPlan('live');
  assert.equal(plan.degraded, false);
  assert.equal(plan.banner, null);
  assert.equal(plan.flipBeat.mode, 'live');
});
