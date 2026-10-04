/* integrations.test.mjs — BYOK settings surface: five states + honesty law.
 * Pure render helpers from ../assets/views-settings.js.
 * Run: node --test tests/integrations.test.mjs  (from platform/ui/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { keyStatusChip, simBannerHtml, testResultHtml, emptyKeysHtml, ephemeralNoteHtml } from '../assets/views-settings.js';

const LAST4 = 'aa11';
const st = (configured, last4) => ({ configured, last4: configured ? last4 : null });

test('keyStatusChip: configured shows only last4, never a value', () => {
  const html = keyStatusChip(st(true, LAST4));
  assert.match(html, /● set/);
  assert.match(html, new RegExp(LAST4));
  assert.doesNotMatch(html, /aa11aa11/); /* the full key must never render */
});

test('keyStatusChip: empty state is honest, not alarming', () => {
  const html = keyStatusChip(st(false));
  assert.match(html, /○ not set/);
  assert.doesNotMatch(html, /error|missing|required/i);
});

test('simBannerHtml: simulated mode is an in-band banner, not a footnote', () => {
  const html = simBannerHtml(true, false);
  assert.match(html, /SIMULATED PAGING/);
  assert.match(html, /logged, not sent/);
  assert.match(html, /role="note"/);
});

test('simBannerHtml: off renders nothing — no banner, no noise', () => {
  assert.equal(simBannerHtml(false, false), '');
});

test('simBannerHtml: forced demo mode says why', () => {
  const html = simBannerHtml(true, true);
  assert.match(html, /hosted demo/);
});

test('testResultHtml: simulated result is visually distinct from real', () => {
  const sim = testResultHtml({ ok: true, simulated: true, message: 'not sent' });
  const real = testResultHtml({ ok: true, simulated: false, dedup_key: 'd1', message: 'accepted' });
  assert.match(sim, /SIMULATED/);
  assert.doesNotMatch(sim, /SENT/);
  assert.match(real, /SENT/);
  assert.doesNotMatch(real, /SIMULATED/);
  assert.match(real, /d1/); /* dedup key present so the operator can match it in PagerDuty */
});

test('testResultHtml: failure is a labeled failure, never silent', () => {
  const html = testResultHtml({ ok: false, simulated: false, message: 'HTTP 400' });
  assert.match(html, /NOT SENT/);
  assert.match(html, /role="alert"/);
  assert.match(html, /HTTP 400/);
});

test('testResultHtml: null renders nothing', () => {
  assert.equal(testResultHtml(null), '');
});

test('emptyKeysHtml: not-configured state guides, never blames', () => {
  const html = emptyKeysHtml();
  assert.match(html, /No paging keys yet/);
  assert.match(html, /Events API v2/); /* tells the operator WHERE the key lives */
  assert.match(html, /never show it again/);
});

test('ephemeralNoteHtml: session-scoped limitation is stated in-band', () => {
  const html = ephemeralNoteHtml();
  assert.match(html, /session-scoped/);
  assert.match(html, /never stored/);
});
