/* views-settings.js — Integrations: bring-your-own-keys settings surface.
 * Reads/writes: GET/POST /api/v1/integrations/* (always live — keys are never mocked).
 *
 * Interface Principles law applied:
 *   §4 five states — loading skeleton / ready / error (API unreachable) /
 *     empty (no keys yet) / test-result, all in-band.
 *   §5 no modals for critical info — simulated mode and test results render
 *     in the surface, never in a dialog or toast.
 *   §8 operator-language microcopy — no jargon, no blame.
 *   P3 honesty — simulated paging is a banner that is PART of the surface,
 *     not a footnote; simulated pages are visually distinct everywhere.
 *
 * Keys are write-only: the API reports configured + last4, never values.
 * This screen never holds a full key in the DOM longer than the request
 * that carries it (inputs are cleared after save / test).
 */
import { Data } from './api.js';
import { skeletonRows, errorBlock, emptyBlock, esc } from './components.js';

/* ---------------- pure render helpers (unit-tested) ---------------- */

export function keyStatusChip(st) {
  if (st && st.configured)
    return `<span class="mono key-set">● set&nbsp;····${esc(st.last4 || '')}</span>`;
  return `<span class="mono key-unset">○ not set</span>`;
}

export function simBannerHtml(simulated, forced) {
  if (!simulated) return '';
  const why = forced
    ? 'forced on for this hosted demo (no keys here)'
    : 'turned on below';
  return `<div class="sim-banner mono" role="note">
    <span class="sim-banner-tag">SIMULATED PAGING</span>
    Pages are logged, not sent — ${esc(why)}. A page from this box reaches
    nobody until simulated paging is off and a real key is set.
  </div>`;
}

export function testResultHtml(result) {
  /* result: {ok, simulated, dedup_key?, key_source?, message} — in-band, no modal. */
  if (!result) return '';
  if (result.simulated) {
    return `<div class="test-result sim mono" role="status">
      <span class="sim-banner-tag">SIMULATED</span>
      ${esc(result.message || 'No page was sent.')}
    </div>`;
  }
  if (result.ok) {
    return `<div class="test-result ok mono" role="status">
      <span class="ok-tag">SENT</span>
      ${esc(result.message || 'Test page accepted.')}
      ${result.dedup_key ? `<br>dedup key <b>${esc(result.dedup_key)}</b> — match this in PagerDuty.` : ''}
    </div>`;
  }
  return `<div class="test-result fail mono" role="alert">
    <span class="fail-tag">NOT SENT</span>
    ${esc(result.message || 'PagerDuty did not accept the test page.')}
  </div>`;
}

export function emptyKeysHtml() {
  return emptyBlock(
    'No paging keys yet.',
    'Sentinel cannot page anyone until you add your PagerDuty routing key. ' +
    'Find it in PagerDuty: your service → Integrations → Events API v2. ' +
    'Paste it once — we never show it again.');
}

export function ephemeralNoteHtml() {
  return `<p class="mono int-note warn">◈ This hosted demo can't keep keys between restarts — ` +
    `anything you save here is session-scoped. For the test page you can paste a key ` +
    `per request instead; it's used once and never stored.</p>`;
}

/* ---------------- the screen ---------------- */

const PD_NAME = 'pagerduty_routing_key';
const JEV_NAME = 'jev_api_key';

export async function renderSettings(root, params, ctx) {
  ctx.setScreenCode('KEYS');
  ctx.setSrcBadge('live');
  ctx.setStrip('');

  /* §4 state 1: loading — skeleton first, never a blank screen */
  root.innerHTML = `
  <div class="int-wrap">
    <h2 class="int-title">Integrations <span class="mono int-sub">your keys, your pager</span></h2>
    <div id="int-sim-slot"></div>
    <div id="int-body">${skeletonRows(6)}</div>
  </div>`;
  const simSlot = root.querySelector('#int-sim-slot');
  const body = root.querySelector('#int-body');
  let status = null;

  async function refresh() {
    try {
      const env = await Data.getIntegrations();
      status = env.data.integrations;
      paintReady();
    } catch (e) {
      /* §4 state 3: error — API unreachable, with retry, in-band */
      body.innerHTML = errorBlock({
        what: 'Couldn\u2019t reach the integrations API.',
        detail: (e.message || 'unreachable') +
          ' Your keys are untouched — nothing was saved or lost by this screen.',
        retryLabel: 'Retry',
        retryFn: refresh,
      });
    }
  }

  function paintReady() {
    const sim = !!status.simulated_paging;
    const forced = sim && !status.ephemeral ? isEnvForcedHint() : status.ephemeral && sim;
    simSlot.innerHTML = simBannerHtml(sim, status.ephemeral);
    const pd = status[PD_NAME] || { configured: false };
    const jv = status[JEV_NAME] || { configured: false };
    const noKeys = !pd.configured && !jv.configured;

    body.innerHTML = `
    ${status.ephemeral ? ephemeralNoteHtml() : ''}
    ${noKeys ? emptyKeysHtml() : ''}
    <section class="int-card">
      <h3>PagerDuty routing key <span class="mono int-card-sub">Events API v2</span></h3>
      <p class="mono int-note">This is the key PagerDuty gives your service — paste it once, ` +
      `we never show it again. Pages go to <i>your</i> PagerDuty, not ours.</p>
      <div class="int-row">${keyStatusChip(pd)}
        ${pd.configured ? `<button class="btn danger" id="int-pd-del">Remove</button>` : ''}
      </div>
      <div class="int-row">
        <input type="password" id="int-pd-in" class="mono" autocomplete="off"
               spellcheck="false" placeholder="32-character Events API v2 key"
               aria-label="PagerDuty routing key">
        <button class="btn primary" id="int-pd-save">Save key</button>
      </div>
      <div class="mono int-err" id="int-pd-err" role="alert" hidden></div>
    </section>
    <section class="int-card">
      <h3>Jev API key <span class="mono int-card-sub">optional override</span></h3>
      <p class="mono int-note">Leave empty to use the platform key. Set this only if you ` +
      `want Sentinel to bill decisions to your own Jev account.</p>
      <div class="int-row">${keyStatusChip(jv)}
        ${jv.configured ? `<button class="btn danger" id="int-jev-del">Remove</button>` : ''}
      </div>
      <div class="int-row">
        <input type="password" id="int-jev-in" class="mono" autocomplete="off"
               spellcheck="false" placeholder="your Jev API key (optional)"
               aria-label="Jev API key">
        <button class="btn primary" id="int-jev-save">Save key</button>
      </div>
      <div class="mono int-err" id="int-jev-err" role="alert" hidden></div>
    </section>
    <section class="int-card">
      <h3>Simulated paging</h3>
      <p class="mono int-note">${sim
        ? 'ON — pages are logged, not sent. Nothing from this box reaches a human pager.'
        : 'OFF — pages go to PagerDuty through the key above.'}</p>
      <div class="int-row">
        <button class="btn" id="int-sim-toggle" ${status.ephemeral ? 'disabled' : ''}>
          Turn simulated paging ${sim ? 'off' : 'on'}</button>
      </div>
      ${status.ephemeral ? `<p class="mono int-note warn">Hosted demo: simulated paging is ` +
        `forced on and can't be toggled here.</p>` : ''}
    </section>
    <section class="int-card">
      <h3>Send a test page</h3>
      <p class="mono int-note">Fires a clearly-labeled <b>[SENTINEL TEST]</b> trigger through ` +
      `the real PagerDuty API — safe to acknowledge. Uses your saved key${status.ephemeral ?
        ', or paste one below for this test only (used once, never stored)' : ''}.</p>
      ${status.ephemeral ? `<div class="int-row">
        <input type="password" id="int-test-in" class="mono" autocomplete="off"
               spellcheck="false" placeholder="routing key for this test only (optional)"
               aria-label="Routing key for this test only">
      </div>` : ''}
      <div class="int-row"><button class="btn primary" id="int-test-go">Send test page</button></div>
      <div id="int-test-out" style="margin-top:8px"></div>
    </section>`;

    wire('int-pd-save', 'int-pd-in', 'int-pd-err', PD_NAME);
    wire('int-jev-save', 'int-jev-in', 'int-jev-err', JEV_NAME);
    const pdDel = body.querySelector('#int-pd-del');
    if (pdDel) pdDel.addEventListener('click', () => delKey(PD_NAME));
    const jvDel = body.querySelector('#int-jev-del');
    if (jvDel) jvDel.addEventListener('click', () => delKey(JEV_NAME));
    const tog = body.querySelector('#int-sim-toggle');
    if (tog) tog.addEventListener('click', toggleSim);
    body.querySelector('#int-test-go').addEventListener('click', sendTest);
  }

  function isEnvForcedHint() { return false; /* server can't tell us; ephemeral copy covers the demo */ }

  function showErr(id, msg) {
    const elx = body.querySelector('#' + id);
    elx.textContent = msg;
    elx.hidden = false;
  }
  function clearKeyInputs() {
    ['int-pd-in', 'int-jev-in', 'int-test-in'].forEach(id => {
      const i = body.querySelector('#' + id);
      if (i) i.value = '';
    });
  }

  async function wire(saveId, inId, errId, name) {
    body.querySelector('#' + saveId).addEventListener('click', async () => {
      const inp = body.querySelector('#' + inId);
      body.querySelector('#' + errId).hidden = true;
      try {
        await Data.saveIntegrationKey(name, inp.value);
        clearKeyInputs();
        await refresh();
      } catch (e) {
        showErr(errId, e.code === 'persistence_unavailable'
          ? 'This hosted demo can\u2019t keep keys — use the per-test field below instead.'
          : (e.message || 'Couldn\u2019t save the key.'));
      }
    });
  }

  async function delKey(name) {
    try { await Data.deleteIntegrationKey(name); await refresh(); }
    catch (e) { /* surface stays — the key list re-renders from refresh */ await refresh(); }
  }

  async function toggleSim() {
    try {
      await Data.setSimulatedPaging(!status.simulated_paging);
      await refresh();
    } catch (e) {
      simSlot.innerHTML = errorBlock({
        what: 'Couldn\u2019t change simulated paging.',
        detail: e.message || 'unreachable',
        retryLabel: 'Retry', retryFn: toggleSim,
      });
    }
  }

  async function sendTest() {
    const out = body.querySelector('#int-test-out');
    out.innerHTML = `<p class="mono int-note">Sending test page…</p>`;
    const perReq = body.querySelector('#int-test-in');
    try {
      const env = await Data.testPage(perReq && perReq.value.trim() ? perReq.value.trim() : null);
      /* §4 state 5: test-result — in-band, simulated vs real visually distinct */
      out.innerHTML = testResultHtml(env.data);
      clearKeyInputs();
    } catch (e) {
      out.innerHTML = testResultHtml({ ok: false, simulated: false,
        message: e.message || 'The test page couldn\u2019t be sent.' });
    }
  }

  await refresh();
}
