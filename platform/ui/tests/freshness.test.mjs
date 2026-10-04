/* freshness.test.mjs — §4.1 the freshness contract as tests.
 * Run: node --test tests/freshness.test.mjs (from platform/ui/) */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  FRESHNESS_STATES, FRESHNESS_BUDGETS,
  freshnessState, freshnessBadge, streamFreshness,
} from '../assets/freshness.js';

const NOW = 1_700_000_000_000;

test('freshnessState: live within budget', () => {
  assert.equal(freshnessState({ sourceUp: true, asOfMs: NOW - 10_000, nowMs: NOW, budgetMs: 30_000 }), 'live');
});

test('freshnessState: cached while refetching inside budget', () => {
  assert.equal(freshnessState({ sourceUp: true, refetching: true, asOfMs: NOW - 10_000, nowMs: NOW, budgetMs: 30_000 }), 'cached');
});

test('freshnessState: stale beyond budget', () => {
  assert.equal(freshnessState({ sourceUp: true, asOfMs: NOW - 61_000, nowMs: NOW, budgetMs: 30_000 }), 'stale');
});

test('freshnessState: degraded when the source is down, even with a young cache', () => {
  assert.equal(freshnessState({ sourceUp: false, asOfMs: NOW - 1_000, nowMs: NOW, budgetMs: 30_000 }), 'degraded');
});

test('freshnessState: unknown as-of is degraded, never silent', () => {
  assert.equal(freshnessState({ sourceUp: true, asOfMs: null, nowMs: NOW, budgetMs: 30_000 }), 'degraded');
});

test('freshnessState: immutable surfaces (audit) are chain-verified, not time-fresh', () => {
  assert.equal(freshnessState({ sourceUp: true, asOfMs: null, nowMs: NOW, budgetMs: null }), 'live');
  assert.equal(FRESHNESS_BUDGETS.audit, null);
});

test('budgets exist per surface (§11 R3)', () => {
  for (const s of ['river', 'decision', 'calibration', 'simulator', 'shadow'])
    assert.ok(typeof FRESHNESS_BUDGETS[s] === 'number', s);
});

test('freshnessBadge: closed four-state vocabulary, text label always present', () => {
  for (const state of FRESHNESS_STATES) {
    const html = freshnessBadge({ state, asOfIso: '2026-10-04T13:00:00Z' });
    assert.match(html, new RegExp(`fr-${state}`), `state class fr-${state}`);
    assert.match(html, /fr-label/, 'text label element');
    assert.ok(!/color-only/.test(html));
  }
});

test('freshnessBadge: stale states what it is waiting on', () => {
  const html = freshnessBadge({ state: 'stale', asOfIso: '2026-10-04T13:00:00Z', waitingOn: 'SSE reconnect' });
  assert.match(html, /SSE reconnect/);
});

test('freshnessBadge: unknown state degrades loudly', () => {
  const html = freshnessBadge({ state: 'bogus' });
  assert.match(html, /fr-degraded/);
});

test('streamFreshness: reconnecting is cached, never live', () => {
  const r = streamFreshness({ streamState: 'reconnecting', lastEventAt: NOW - 5_000, nowMs: NOW });
  assert.equal(r.state, 'cached');
  assert.match(r.waitingOn, /SSE reconnect/);
});

test('streamFreshness: quiet stream goes stale past the river budget', () => {
  const r = streamFreshness({ streamState: 'paused', lastEventAt: NOW - 120_000, nowMs: NOW });
  assert.equal(r.state, 'stale');
});

test('streamFreshness: live stream inside budget is live', () => {
  const r = streamFreshness({ streamState: 'live', lastEventAt: NOW - 2_000, nowMs: NOW });
  assert.equal(r.state, 'live');
});
