/* views-start.js — Onboarding: zero to first suppression in 15 minutes.
 * Beat 0: the honesty contract (static, unskippable). Then key → source →
 * storm → why → tune. Skip paths everywhere; nothing is auto-applied, ever.
 * DRILL 7: ?jev=dead forces the designed recorded-walkthrough fallback —
 * the flow refuses to fake liveness (narrative §6.3). */
import { Data } from './api.js';
import { decisionRow, srcBadge, emptyBlock, esc } from './components.js';
import { startPlan, jevStateFromParams, stripStart, routeHref, fmtInt,
         DEFAULT_THRESHOLDS } from './lib.js';

const STEP_IDS = ['contract', 'key', 'source', 'storm', 'why', 'tune'];

export async function renderStart(root, params, ctx) {
  const jevState = jevStateFromParams(params);
  const plan = startPlan(jevState);
  let step = localStorage.getItem('sentinel.start.step') || 'contract';
  if (!STEP_IDS.includes(step)) step = 'contract';
  let stormTimers = [];
  ctx.registerCleanup(() => stormTimers.forEach(clearTimeout));

  ctx.setScreenCode('START');
  ctx.setSrcBadge(Data.mode === 'mock' ? 'synthetic' : 'shadow');
  ctx.setStrip(stripStart({ mode: jevState === 'dead' ? 'recorded' : 'live' }));

  const stepIdx = () => STEP_IDS.indexOf(step);
  function progressHtml() {
    const labels = { contract: 'contract', key: 'key', source: 'source', storm: 'storm', why: 'why', tune: 'tune' };
    return `<div class="start-progress mono">${STEP_IDS.map((id, i) =>
      `<button class="sp-step${id === step ? ' cur' : ''}${i < stepIdx() ? ' done' : ''}" data-step="${id}">${labels[id]}</button>${i < STEP_IDS.length - 1 ? '<span class="sp-sep">→</span>' : ''}`
    ).join('')}</div>`;
  }

  function shell(inner) {
    root.innerHTML = `${plan.banner ? `<div class="deg-banner mono">${esc(plan.banner)}</div>` : ''}${progressHtml()}<div class="start-body">${inner}</div>`;
    root.querySelectorAll('.sp-step').forEach(b => b.addEventListener('click', () => go(b.dataset.step)));
  }
  function go(id) {
    step = id;
    localStorage.setItem('sentinel.start.step', id);
    ({ contract: vContract, key: vKey, source: vSource, storm: vStorm, why: vWhy, tune: vTune })[id]();
  }
  const navBtns = (nextId) => `
    <div class="start-nav">
      ${nextId ? `<button class="btn primary" data-next="${nextId}">continue →</button>` : ''}
      ${stepIdx() > 0 ? `<button class="btn" data-back="1">← back</button>` : ''}
      <button class="btn" data-skip-river="1">skip to river</button>
    </div>`;
  function wireNav(nextId) {
    root.querySelectorAll('[data-next]').forEach(b => b.addEventListener('click', () => go(b.dataset.next)));
    root.querySelectorAll('[data-back]').forEach(b => b.addEventListener('click', () => go(STEP_IDS[stepIdx() - 1])));
    root.querySelectorAll('[data-skip-river]').forEach(b => b.addEventListener('click', () => {
      const p = new URLSearchParams(); if (Data.mode === 'mock') p.set('mock', '1');
      location.hash = routeHref('river', p);
    }));
  }

  /* ---- beat 0: the demo contract ---- */
  function vContract() {
    shell(`
      <div class="contract-card">
        <h2>This is a demonstration, not your system.</h2>
        <p>Everything you are about to see runs on <b>synthetic data</b> and a <b>recorded storm</b> (recorded 2026-10-01, mock engine). The <i>evidence patterns</i> — divergences, receipts, the backtest bar — are real shapes; the <i>data</i> is not yours and proves nothing about your fleet.</p>
        <p>Nothing in this demo can page anyone. The first thing we will show you is the machine being wrong.</p>
        <p class="mono" style="color:var(--tx-2)">demo data only — recorded storm, mock engine. nothing here pages anyone.</p>
      </div>${navBtns('key')}`);
    wireNav();
  }

  /* ---- step 1: BYOK ---- */
  function vKey() {
    const s = plan.steps[0];
    shell(`
      <h2>${esc(s.title)}</h2><p>${esc(s.body)}</p>
      ${s.degraded
        ? `<div class="error-block"><div class="error-what">Key verification unavailable — Jev path down.</div><div class="error-detail mono">Nothing was stored. The tour continues on the recording.</div></div>`
        : `<div class="key-row"><input id="start-key" class="mono" type="password" placeholder="typesafe_…" autocomplete="off"><button class="btn primary" id="start-verify">verify</button></div>
           <div id="start-key-out" class="mono" style="margin-top:8px"></div>
           <p class="drawer-note">Demo build: the read tier cannot reach the Jev path directly. Any key-shaped token continues the tour — nothing is stored or sent.</p>`}
      ${navBtns('source')}`);
    wireNav();
    if (!s.degraded) {
      root.querySelector('#start-verify').addEventListener('click', () => {
        const v = root.querySelector('#start-key').value.trim();
        const out = root.querySelector('#start-key-out');
        if (v.length < 8) { out.innerHTML = `<span style="color:var(--sev-1)">Key verification failed — too short to be a key. Nothing was stored.</span>`; return; }
        out.innerHTML = `demo-mode verify: <span style="color:var(--sev-4)">format ok</span> · not sent anywhere · not stored · continuing`;
        root.querySelector('#start-key').value = '';
      });
    }
  }

  /* ---- step 2: alert source ---- */
  function vSource() {
    shell(`
      <h2>${esc(plan.steps[1].title)}</h2><p>${esc(plan.steps[1].body)}</p>
      <pre class="mono webhook-snippet">POST https://sentinel.yourco.dev/v1/tap
Authorization: Bearer &lt;ops-token&gt;
# point your PagerDuty webhook here ALONGSIDE the existing stack.
# shadow mode: Sentinel logs what it WOULD have done. Your paging path
# is byte-identical — nothing here can suppress, delay, or alter a page.</pre>
      <div class="start-nav"><button class="btn" id="start-test">test: fire a synthetic alert</button></div>
      <div id="start-trace" class="trace"></div>
      ${navBtns('storm')}`);
    wireNav();
    root.querySelector('#start-test').addEventListener('click', () => {
      const tr = root.querySelector('#start-trace');
      const stages = ['receiver: alert accepted (synthetic)', 'correlator: episode formed — flap-debounce window', 'gate: evaluated → SUPPRESS · triple-lock', 'forwarder: n/a — suppressed, nothing forwarded'];
      tr.innerHTML = stages.map(s => `<div class="trace-ev mono pending">○ ${esc(s)}</div>`).join('');
      stages.forEach((_, i) => {
        stormTimers.push(setTimeout(() => {
          const ev = tr.children[i];
          ev.classList.remove('pending'); ev.classList.add('done');
          ev.innerHTML = '● ' + esc(stages[i]);
        }, 600 * (i + 1)));
      });
    });
  }

  /* ---- step 3: the guided storm (recorded — the honest kind) ---- */
  async function vStorm() {
    const s = plan.steps[2];
    shell(`<h2>${esc(s.title)}</h2><p>${esc(s.body)}</p>
      <div class="storm-tape-wrap"><div class="storm-meta mono" id="storm-meta">arming…</div><div id="storm-tape" class="tape storm-tape"></div></div>
      <div id="storm-flip"></div>
      ${navBtns('why')}`);
    wireNav();
    const tape = root.querySelector('#storm-tape');
    const meta = root.querySelector('#storm-meta');
    let rows;
    try {
      const env = await Data.getDecisions({ limit: 50 });
      rows = env.data;
    } catch {
      tape.innerHTML = `<div class="error-block"><div class="error-what">Recorded storm unavailable.</div><div class="error-detail mono">mock data unreachable — the tour continues without the storm replay.</div></div>`;
      return;
    }
    const ordered = [...rows].reverse();
    let i = 0, nSupp = 0, nPage = 0;
    const tick = () => {
      if (i >= ordered.length) {
        meta.textContent = `storm complete: ${nSupp} suppressed · ${nPage} paged · 1 flip · recorded 2026-10-01`;
        const fb = plan.flipBeat;
        root.querySelector('#storm-flip').innerHTML = `<div class="flip-beat${plan.degraded ? ' degraded' : ''}">
          <div class="mono flip-beat-label">${plan.degraded ? '◌ ' : ''}${esc(fb.label)}</div>
          <div class="flip-tl"><div class="flip-ev"><span class="mono">02:14:07</span> SUPPRESS · <span class="mono">0.93</span></div>
          <div class="flip-link">│ input identical (sha256:9f2c…a41d)</div>
          <div class="flip-ev"><span class="mono">02:14:41</span> PAGE · <span class="mono">0.41</span> <span class="flip-flag mono">← FLIP</span></div></div>
        </div>`;
        return;
      }
      const d = ordered[i++];
      if (d.disposition === 'suppress') nSupp++; else if (d.disposition === 'page_now') nPage++;
      tape.insertAdjacentHTML('afterbegin', decisionRow(d, { density: 'tape', thresholds: DEFAULT_THRESHOLDS }));
      const first = tape.firstElementChild;
      if (first) { first.classList.add('row-new'); setTimeout(() => first.classList.remove('row-new'), 140); }
      meta.textContent = `storm running… ${i}/${ordered.length} · ${nSupp} suppressed · ${nPage} paged`;
      stormTimers.push(setTimeout(tick, 650));
    };
    tick();
  }

  /* ---- step 4: first suppression, explained ---- */
  async function vWhy() {
    shell(`<h2>${esc(plan.steps[3].title)}</h2><p>${esc(plan.steps[3].body)}</p>
      <div id="why-body">${esc('loading the suppression…')}</div>${navBtns('tune')}`);
    wireNav();
    try {
      const det = await Data.getDecision(1042);
      const d = det.data;
      root.querySelector('#why-body').innerHTML = `
        <div class="why-row">${decisionRow(d, { density: 'comfortable', thresholds: DEFAULT_THRESHOLDS })}</div>
        <div class="why-walk">
          <div class="why-step"><b>1 · the confidence bar.</b> Marker vs the team's Q3 vs the suppress floor — is 0.96 sure <i>by your standards</i>?</div>
          <div class="why-step"><b>2 · the reason code.</b> <span class="mono">triple-lock</span> — the machine's receipt, not a vibe.</div>
          <div class="why-step"><b>3 · the input hash.</b> <span class="mono">${esc(d.input_sha256.slice(0, 16))}…</span> — ask the same question twice, get a checkable answer.</div>
          <div class="why-step"><b>4 · the counterfactual.</b> At 0.70 this paged; at your 0.90 it stood down. <a href="${routeHref('simulator', { team: d.team, ...(Data.mode === 'mock' ? { mock: '1' } : {}) })}">Drag the SEV2 slider to 0.95 and watch it page →</a></div>
        </div>`;
      root.querySelector('#why-body').insertAdjacentHTML('beforeend',
        `<div class="start-nav"><button class="btn" id="why-drawer">open the full drawer</button></div>`);
      root.querySelector('#why-drawer').addEventListener('click', () => ctx.openDrawer(1042, {}));
    } catch {
      root.querySelector('#why-body').innerHTML = emptyBlock('The example suppression is unavailable right now.', '');
    }
  }

  /* ---- step 5: set your thresholds ---- */
  function vTune() {
    shell(`<h2>${esc(plan.steps[4].title)}</h2><p>${esc(plan.steps[4].body)}</p>
      <div class="tune-teaser">
        <div class="tune-row"><span class="mono">suppress floor</span>
          <input type="range" id="tune-slider" min="0.5" max="1" step="0.01" value="0.90">
          <span class="mono" id="tune-val">0.90</span></div>
        <p class="mono" id="tune-out" style="color:var(--tx-1)">at 0.90, last week's noise stood down — drag to reprice</p>
      </div>
      ${navBtns(null)}
      <div class="start-nav"><a class="btn primary" href="${routeHref('simulator', { ...(Data.mode === 'mock' ? { mock: '1' } : {}) })}">open the full simulator →</a></div>`);
    wireNav();
    const sl = root.querySelector('#tune-slider'), out = root.querySelector('#tune-out'), val = root.querySelector('#tune-val');
    sl.addEventListener('input', () => {
      val.textContent = Number(sl.value).toFixed(2);
      out.textContent = `at ${Number(sl.value).toFixed(2)} you would have been woken ${sl.value > 0.9 ? 'more' : sl.value < 0.9 ? 'fewer' : 'the same number of'} times — the full math is in SIM`;
    });
    const done = document.createElement('div');
    done.className = 'start-done mono';
    done.innerHTML = `tour complete — your thresholds are unchanged. the river is live on your data.`;
    root.querySelector('.start-body').appendChild(done);
    localStorage.setItem('sentinel.start.step', 'tune');
  }

  go(step);
}
