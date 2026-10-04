/* chain.test.mjs — S4 derived event-log chain: derive + verify.
 * Run: node --test tests/chain.test.mjs (from platform/ui/) */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { deriveChain, verifyChain, sha256Hex } from '../assets/chain.js';

const decisions = JSON.parse(readFileSync(new URL('../data/decisions.json', import.meta.url))).data;

test('deriveChain: links are ordered, hash-linked, and verify clean', async () => {
  const links = await deriveChain(decisions);
  assert.equal(links.length, decisions.length);
  for (let i = 1; i < links.length; i++) assert.equal(links[i].prev, links[i - 1].hash);
  assert.equal(links[0].prev, 'GENESIS');
  const v = await verifyChain(links);
  assert.ok(v.ok);
  assert.equal(v.n, links.length);
});

test('verifyChain: tampering is detected at the altered link', async () => {
  const links = await deriveChain(decisions);
  const tampered = links.map(l => ({ ...l }));
  tampered[3] = { ...tampered[3], input_sha256: 'deadbeef'.repeat(8) };
  const v = await verifyChain(tampered);
  assert.ok(!v.ok);
  assert.equal(v.brokenAt, 3);
});

test('verifyChain: an empty window verifies trivially', async () => {
  const v = await verifyChain([]);
  assert.ok(v.ok);
  assert.equal(v.n, 0);
});

test('sha256Hex: deterministic', async () => {
  assert.equal(await sha256Hex('abc'), await sha256Hex('abc'));
  assert.notEqual(await sha256Hex('abc'), await sha256Hex('abd'));
});
