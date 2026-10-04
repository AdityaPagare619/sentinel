/* payload.test.mjs — the total safe renderer, fuzzed with hostile payloads.
 * The contract: renderPayload NEVER throws, NEVER leaks secret-shaped values,
 * NEVER emits unescaped markup, and degrades with stated caps — never silently.
 * Run: node --test tests/payload.test.mjs  (from platform/ui/) */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderPayload, payloadEmptyHtml, payloadSkeletonHtml, PAYLOAD_LIMITS, SECRET_KEY_RE } from '../assets/payload.js';

const BIDI = String.fromCharCode(0x202e); /* right-to-left override */

/* Build the hostile corpus once — every case must render without throwing. */
function hostileCorpus() {
  const circular = { a: 1 }; circular.self = circular;
  const deep = {}; let d = deep; for (let i = 0; i < 200; i++) { d.n = {}; d = d.n; }
  const throwing = {}; Object.defineProperty(throwing, 'boom', { enumerable: true, get() { throw new Error('getter bomb'); } });
  const proto = JSON.parse('{"__proto__": {"polluted": true}, "ok": 1}');
  const wide = {}; for (let i = 0; i < 5000; i++) wide['k' + i] = i;
  return [
    ['circular', circular],
    ['200-deep', deep],
    ['throwing getter', throwing],
    ['__proto__ key', proto],
    ['5000-key object', wide],
    ['100k-item array', { arr: new Array(100000).fill(0) }],
    ['200KB string', { s: 'z'.repeat(200000) }],
    ['bidi spoof', { label: 'refund' + BIDI + 'EVIL' }],
    ['xss attempt', { k: '<script>alert(1)</script>', q: '"><img src=x onerror=alert(2)>' }],
    ['secret shapes', { api_token: 'hunter2', PASSWORD: 'hunter3', nested: { auth: 'hunter4', bearerToken: 'hunter5' }, 'author': 'not-a-secret' }],
    ['weird scalars', { nan: NaN, inf: Infinity, ninf: -Infinity, big: 10n ** 40n, undef: undefined, fn: () => 1, sym: Symbol('s') }],
    ['binary', { buf: new Uint8Array(1024), ab: new ArrayBuffer(8) }],
    ['dates', { good: new Date('2026-10-04T00:00:00Z'), bad: new Date('nope') }],
    ['map/set', { m: new Map([['a', 1]]), s: new Set([1, 2]) }],
    ['nested arrays', [[[[[['deep']]]]]]],
    ['empty containers', { o: {}, a: [], s: '', n: null }],
    ['numeric keys', { 0: 'zero', 1: 'one' }],
    ['unicode keys', { 'café ☕': 1, '键': 2 }],
    ['link', { runbook: 'https://example.com/runbook/123' }],
    ['not-a-link', { evil: 'javascript:alert(1)', weird: 'https://' }],
    ['timestamps verbatim', { t: '2026-10-04T00:00:01Z' }],
  ];
}

test('hostile corpus: never throws, always a string', () => {
  for (const [name, v] of hostileCorpus()) {
    let out;
    try { out = renderPayload(v); }
    catch (e) { assert.fail(`${name} threw: ${e && e.message}`); }
    assert.equal(typeof out, 'string', name);
    assert.ok(out.length > 0, name);
  }
});

test('secrets are redacted, never blank, never leaked', () => {
  const out = renderPayload({ api_token: 'hunter2', nested: { db_password: 'hunter3' }, ok: 'visible' });
  assert.ok(!out.includes('hunter2') && !out.includes('hunter3'), 'values absent');
  assert.ok(out.includes('redacted'), 'redaction stated');
  assert.ok(out.includes('api_token'), 'key name still structural');
  assert.ok(out.includes('visible'), 'non-secrets untouched');
  assert.ok(SECRET_KEY_RE.test('api_token') && SECRET_KEY_RE.test('X-Auth-Token'));
  assert.ok(!SECRET_KEY_RE.test('author'), '"author" must not match "auth"');
  assert.ok(!SECRET_KEY_RE.test('total'), '"total" must not match');
});

test('xss neutralized; bidi shown as a visible marker, never silently stripped (A1)', () => {
  const out = renderPayload({ k: '<script>alert(1)</script>', b: 'a' + BIDI + 'b' });
  assert.ok(!out.includes('<script>'), 'no raw script tag');
  assert.ok(out.includes('&lt;script&gt;'), 'escaped');
  assert.ok(!out.includes(BIDI), 'raw bidi char absent');
  assert.ok(out.includes('[U+202E]'), 'bidi rendered as a visible marker — stripping would hide a spoof');
});
test('A5: strict-ISO timestamps render UTC + local in-band; anything else verbatim', () => {
  const out = renderPayload({ t: '2026-10-04T12:04:11Z' });
  assert.ok(out.includes('2026-10-04 12:04:11 UTC'), 'UTC in-band');
  assert.ok(out.includes('·'), 'local zone alongside, in-band');
  assert.ok(renderPayload({ t: 'yesterday-ish' }).includes('yesterday-ish'), 'non-ISO verbatim');
  assert.ok(renderPayload({ t: '2026-13-99T99:99:99Z' }).includes('2026-13-99'), 'invalid dates never interpreted');
});

test('unknown renders as — with the reason in-band', () => {
  const out = renderPayload({ u: undefined, f: () => 1, n: NaN });
  assert.ok(out.includes('—'), 'dash present');
  assert.ok(out.includes('undefined') && out.includes('not a number'), 'reasons in-band');
});

test('depth cap is stated', () => {
  const deep = {}; let d = deep; for (let i = 0; i < 200; i++) { d.n = {}; d = d.n; }
  const out = renderPayload(deep);
  assert.ok(out.includes(`depth cap ${PAYLOAD_LIMITS.maxDepth}`), 'cap stated in-band');
});

test('wide objects and long arrays are capped with counts', () => {
  const wide = {}; for (let i = 0; i < 5000; i++) wide['k' + i] = i;
  const out = renderPayload(wide);
  assert.ok(out.includes('more keys'), 'key cap stated');
  const arr = renderPayload({ arr: new Array(100000).fill(7) });
  assert.ok(arr.includes('more items'), 'item cap stated');
});

test('byte budget truncates loudly, never silently — and partial output survives', () => {
  /* per-object key caps and per-cell width caps mean the budget only binds on
   * nested breadth: 100 children × 100 capped cells each ≈ 2.6MB of markup */
  const tree = {};
  for (let i = 0; i < 100; i++) {
    const child = {};
    for (let j = 0; j < 100; j++) child['k' + j] = 'v'.repeat(150);
    tree['c' + i] = child;
  }
  const out = renderPayload(tree);
  assert.ok(out.includes('truncated'), 'truncation stated');
  assert.ok(out.includes('>c0<'), 'rows rendered before the budget cut survived');
  assert.ok(!out.includes('>c99<'), 'the tail was cut');
  assert.ok(out.length <= PAYLOAD_LIMITS.maxBytes + 2000, 'budget honored');
});

test('circular refs and throwing getters degrade honestly', () => {
  const c = { a: 1 }; c.self = c;
  assert.ok(renderPayload(c).includes('circular'), 'circular stated');
  const t = {}; Object.defineProperty(t, 'boom', { enumerable: true, get() { throw new Error('x'); } });
  assert.ok(renderPayload(t).includes('getter threw'), 'getter failure stated');
});

test('__proto__ keys cannot pollute and are read as data', () => {
  const p = JSON.parse('{"__proto__": {"x": 1}, "ok": 2}');
  renderPayload(p);
  assert.equal({}.x, undefined, 'prototype unpolluted');
});

test('flat scalar maps render as key/value tables (the PagerDuty rule)', () => {
  const out = renderPayload({ cluster: 'us-east-1a', env: 'prod', region: 'us-east-1' });
  assert.ok(out.includes('<table class="pv-table">'), 'flat table');
  assert.ok(out.includes('us-east-1a'), 'values present');
});

test('links render as links; non-links do not', () => {
  const out = renderPayload({ runbook: 'https://example.com/r/1', evil: 'javascript:alert(1)' });
  assert.ok(out.includes('<a class="pv-link'), 'https link rendered');
  assert.ok(!out.includes('href="javascript:'), 'no javascript: href — the value renders as inert text');
});

test('never infers semantics: keys verbatim, no root-cause guessing', () => {
  const out = renderPayload({ root_cause: 'disk full', severity: 'critical' });
  assert.ok(out.includes('root_cause'), 'key verbatim');
  assert.ok(!/root cause(?!_)/i.test(out.replace(/root_cause/g, '')), 'no semantic labeling');
});

test('five states: skeleton and empty are designed', () => {
  assert.ok(payloadSkeletonHtml().includes('pv-skel'), 'loading skeleton');
  const e = payloadEmptyHtml();
  assert.ok(e.includes('No alert context'), 'empty states the scope');
  assert.ok(!e.includes('<table'), 'empty is not a fake table');
});
