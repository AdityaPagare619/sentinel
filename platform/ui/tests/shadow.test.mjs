/* shadow.test.mjs — S5 shadow report metrics + the vanity ban.
 * Run: node --test tests/shadow.test.mjs (from platform/ui/) */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  pagePrecision, suppressionRegret, agreement, disagreementKind,
  BANNED_VANITY_PATTERNS,
} from '../assets/shadow.js';
import { shadowCopyIsClean } from '../assets/views-shadow.js';

const L = (acked, acted, joined = false, unsup = false) => ({
  acked, acted, joined_incident: joined, manually_unsuppressed: unsup,
  labeled_at: '2026-10-03T02:00:00Z',
});

test('pagePrecision: acked-and-acted ÷ labeled pages sent', () => {
  const r = pagePrecision([
    { disposition: 'page_now', outcome: L(true, true) },
    { disposition: 'page_now', outcome: L(true, false) },
    { disposition: 'page_now', outcome: null }, /* unlabeled — excluded, stated */
    { disposition: 'suppress', outcome: L(false, false) },
  ]);
  assert.equal(r.sent, 3);
  assert.equal(r.labeled, 2);
  assert.equal(r.unlabeled, 1);
  assert.equal(r.precision, 0.5);
});

test('pagePrecision: no labeled pages → null, never 0-as-fact', () => {
  const r = pagePrecision([{ disposition: 'page_now', outcome: null }]);
  assert.equal(r.precision, null);
});

test('suppressionRegret: incident-joined or manually-unsuppressed ÷ labeled', () => {
  const r = suppressionRegret([
    { disposition: 'suppress', outcome: L(false, false, true) },
    { disposition: 'suppress', outcome: L(false, false, false, true) },
    { disposition: 'suppress', outcome: L(false, false) },
    { disposition: 'suppress', outcome: null },
  ]);
  assert.equal(r.suppressions, 4);
  assert.equal(r.labeled, 3);
  assert.equal(r.regretted, 2);
  assert.ok(Math.abs(r.regret - 2 / 3) < 1e-9);
});

test('agreement: divergences carry both sides', () => {
  const rows = [
    { decision_id: 1, shadow_disposition: 'suppress', actual_disposition: 'suppress' },
    { decision_id: 2, shadow_disposition: 'suppress', actual_disposition: 'page_now' },
    { decision_id: 3, shadow_disposition: 'page_now', actual_disposition: 'suppress' },
  ];
  const a = agreement(rows);
  assert.equal(a.n, 3);
  assert.equal(a.agree, 1);
  assert.equal(a.disagreements.length, 2);
  assert.equal(disagreementKind(a.disagreements[0]), 'shadow-suppressed · actually-paged');
  assert.equal(disagreementKind(a.disagreements[1]), 'shadow-paged · actually-stood-down');
});

test('the fixture joins to real decisions and matches the S5 definitions', () => {
  const fx = JSON.parse(readFileSync(new URL('../data/shadow.json', import.meta.url)));
  const rows = fx.data;
  const pp = pagePrecision(rows.map(r => ({ disposition: r.actual_disposition, outcome: r.outcome })));
  /* suppression regret is over the SHADOW-suppressed set: the suppressor's
   * decisions, judged by what happened next */
  const sr = suppressionRegret(rows.filter(r => r.shadow_disposition === 'suppress').map(r => ({ disposition: 'suppress', outcome: r.outcome })));
  /* fixture: 4 pages sent (incl. 1039, manually paged after the bad suppress),
   * all labeled, all acked+acted → 1.0; 3 shadow-suppressions, all labeled,
   * 1 regretted (INC-4821) → 1/3 */
  assert.equal(pp.precision, 1.0);
  assert.ok(Math.abs(sr.regret - 1 / 3) < 1e-9);
  const a = agreement(rows);
  assert.equal(a.disagreements.length, 2);
});

test('vanity ban: no "noise removed %" style copy on S5', () => {
  let src = readFileSync(new URL('../assets/views-shadow.js', import.meta.url), 'utf8');
  /* the ban applies to operator-visible copy — strip source comments first
   * (the comment that documents the ban names it, which is not a violation) */
  src = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '');
  assert.ok(shadowCopyIsClean(src), 'vanity pattern found in views-shadow.js');
  for (const re of BANNED_VANITY_PATTERNS) assert.ok(!re.test(src), re);
});
