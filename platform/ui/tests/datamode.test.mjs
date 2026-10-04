/* datamode.test.mjs — unit tests for the DATA_MODE shim (assets/api.js).
 * Run: node --test tests/datamode.test.mjs  (from platform/ui/)
 *
 * The shim is the contract between the static builder and the UI:
 *   DATA_MODE='static' → Data.mode='mock', fetches ./api/*.json, KEYS in demo
 *   DATA_MODE='live'   → Data.mode='live', hits the configured backend URL
 *   unset (dev)        → legacy ?mock=1 / localStorage switch
 */
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

function stubBrowser({ dataMode = null, hash = '', search = '', storedMode = null, backendUrl = '' }) {
  globalThis.window = dataMode === null ? {} : { SENTINEL_DATA_MODE: dataMode };
  globalThis.location = { hash, search };
  const store = { 'sentinel.ui.mode': storedMode, 'sentinel.backend.url': backendUrl };
  globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = v; },
  };
}

async function loadApi() {
  const url = new URL('../assets/api.js', import.meta.url).href + '?t=' + Math.random();
  return import(url);
}

test('static build: mode forced to mock, modeFixed true', async () => {
  stubBrowser({ dataMode: 'static' });
  const { Data } = await loadApi();
  assert.equal(Data.mode, 'mock');
  assert.equal(Data.modeFixed, true);
  assert.equal(Data.dataMode, 'static');
});

test('static build: ?mock=0 cannot flip it to live', async () => {
  stubBrowser({ dataMode: 'static', search: '?mock=0', storedMode: 'live' });
  const { Data } = await loadApi();
  assert.equal(Data.mode, 'mock');
  Data.setMode('live');
  assert.equal(Data.mode, 'mock', 'setMode is a no-op when build-fixed');
});

test('prod build: mode forced to live, apiBase from localStorage', async () => {
  stubBrowser({ dataMode: 'live', backendUrl: 'https://sentinel.example.com/' });
  const { Data, backendUrlConfigured } = await loadApi();
  assert.equal(Data.mode, 'live');
  assert.equal(Data.modeFixed, true);
  assert.equal(Data.apiBase, 'https://sentinel.example.com');
  assert.equal(backendUrlConfigured(), 'https://sentinel.example.com');
});

test('prod build: no backend URL → empty apiBase (setup screen takes over)', async () => {
  stubBrowser({ dataMode: 'live', backendUrl: '' });
  const { Data, backendUrlConfigured } = await loadApi();
  assert.equal(Data.mode, 'live');
  assert.equal(Data.apiBase, '');
  assert.equal(backendUrlConfigured(), '');
});

test('dev (no config): legacy ?mock=1 switch still works', async () => {
  stubBrowser({ dataMode: null, search: '?mock=1' });
  const { Data } = await loadApi();
  assert.equal(Data.mode, 'mock');
  assert.equal(Data.modeFixed, false);
  Data.setMode('live');
  assert.equal(Data.mode, 'live', 'setMode works when not build-fixed');
});

test('dev (no config): defaults to live', async () => {
  stubBrowser({ dataMode: null });
  const { Data } = await loadApi();
  assert.equal(Data.mode, 'live');
});

test('setBackendUrl validates and strips trailing slashes', async () => {
  stubBrowser({ dataMode: 'live' });
  const { setBackendUrl } = await loadApi();
  assert.equal(setBackendUrl('https://sentinel.example.com///'), 'https://sentinel.example.com');
  assert.throws(() => setBackendUrl('not-a-url'), /doesn.t look like a URL/);
  assert.throws(() => setBackendUrl(''), /doesn.t look like a URL/);
});

test('static demo KEYS: save/read/delete round-trip in memory', async () => {
  stubBrowser({ dataMode: 'static' });
  const { Data } = await loadApi();
  const PD = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'; // 32-hex demo key
  let env = await Data.getIntegrations();
  assert.equal(env.data.integrations.pagerduty_routing_key.configured, false);
  assert.equal(env.data.integrations.simulated_paging, true, 'simulated paging forced');
  assert.equal(env.data.integrations.demo, true);
  env = await Data.saveIntegrationKey('pagerduty_routing_key', PD);
  assert.equal(env.data.integrations.pagerduty_routing_key.configured, true);
  assert.equal(env.data.integrations.pagerduty_routing_key.last4, '8f90');
  env = await Data.deleteIntegrationKey('pagerduty_routing_key');
  assert.equal(env.data.integrations.pagerduty_routing_key.configured, false);
});

test('static demo KEYS: bad keys rejected with operator-language errors', async () => {
  stubBrowser({ dataMode: 'static' });
  const { Data } = await loadApi();
  // The API throws plain {status, code, message} objects (existing convention).
  const pdBad = await Data.saveIntegrationKey('pagerduty_routing_key', 'short').then(() => null, (e) => e);
  assert.match(pdBad.message, /32 hexadecimal/);
  assert.equal(pdBad.code, 'bad_key');
  const pdEmpty = await Data.saveIntegrationKey('pagerduty_routing_key', '').then(() => null, (e) => e);
  assert.match(pdEmpty.message, /empty/);
});

test('static demo test-page is simulated and labeled', async () => {
  stubBrowser({ dataMode: 'static' });
  const { Data } = await loadApi();
  const env = await Data.testPage();
  assert.equal(env.data.simulated, true);
  assert.match(env.data.message, /SIMULATED/);
});

test('static demo: toggling simulated paging is refused honestly', async () => {
  stubBrowser({ dataMode: 'static' });
  const { Data } = await loadApi();
  const err = await Data.setSimulatedPaging(false).then(() => null, (e) => e);
  assert.equal(err.code, 'demo_forced');
  assert.match(err.message, /forced on/);
});
