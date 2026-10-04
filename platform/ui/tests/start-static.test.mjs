/* start-static.test.mjs — F2: the onboarding tour is a demo script.
 * In live (prod) mode its contract copy ("This is a demonstration, not
 * your system … synthetic data") is FALSE, so renderStart must gate it
 * and show a live contract instead. Run: node --test tests/start-static.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

function stubBrowser(dataMode) {
  globalThis.window = { SENTINEL_DATA_MODE: dataMode };
  globalThis.location = { hash: '', search: '' };
  const store = {};
  globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = v; },
  };
}

function fakeRoot() {
  return {
    _html: '',
    set innerHTML(v) { this._html = v; },
    get innerHTML() { return this._html; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
  };
}

const fakeCtx = () => ({
  setScreenCode() {},
  setSrcBadge() {},
  setStrip() {},
  registerCleanup() {},
  openDrawer() {},
});

async function loadStart() {
  const url = new URL('../assets/views-start.js', import.meta.url).href +
    '?t=' + Math.random();
  return import(url);
}

test('static mode: demo tour still renders its contract', async () => {
  stubBrowser('static');
  const { renderStart } = await loadStart();
  const root = fakeRoot();
  await renderStart(root, new URLSearchParams(), fakeCtx());
  assert.match(root.innerHTML, /demonstration, not your system/);
  assert.match(root.innerHTML, /synthetic data/);
});

