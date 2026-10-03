/* views-river.js — the decision river: a live, queryable tape of gate decisions.
 * Reads: GET /api/decisions (+filters), GET /api/decision/<id>, SSE /api/stream.
 * Freedom 2: "See why this paged you — and why the others didn't." */
import { Data, Stream } from './api.js';
import { decisionRow, srcBadge, skeletonRows, errorBlock, emptyBlock, gapMarker,
         drawerHtml, esc } from './components.js';
import { parseHash, routeHref, stripRiver, fmtInt, SEV_LABEL, DISP_LABEL,
         DEFAULT_THRESHOLDS, ageStr } from './lib.js';

const TEAMS = ['platform', 'network', 'data', 'product_backend', 'security', 'cannot_determine'];
const SEVS = [['1', 'p1_critical'], ['2', 'p2_high'], ['3', 'p3_medium'], ['4', 'p4_low']];
const DISPS = ['page_now', 'page_business_hours', 'suppress', 'passthrough'];
const WINDOWS = ['1h', '24h', '7d', '30d'];
const DENSITIES = ['comfortable', 'compact', 'tape'];

function windowToIso(win) {
  const m = win.match(/^(\d+)([smhd])$/);
  if (!m) return null;
  const mult = { s: 1, m: 60, h: 3600, d: 86400 }[m[2]];
  return new Date(Date.now() - Number(m[1]) * mult * 1000).toISOString();
}

export async function renderRiver(root, params, ctx) {
  const f = {
    team: params.get('team') || '', action: params.get('action') || '',
    reason: params.get('reason') || '', sev: params.get('sev') || '',
    fpr: params.get('fpr') || params.get('fingerprint') || '',
    q: params.get('q') || '', last: params.get('last') || '24h',
    density: params.get('density') || 'compact',
  };
  let rows = [], flips = {}, calBins = null, newestIso = null, apiDown = false, apiMsg = '';
  let pinned = false, pendingNew = 0, selIdx = -1;

  root.innerHTML = `
  <div class="river-layout">
    <aside class="filter-rail">
      <div class="rail-sec"><h4>team</h4><div class="rail-opts" id="f-teams">
        ${TEAMS.map(t => `<button class="opt${f.team.split(',').includes(t) ? ' on' : ''}" data-v="${t}">${t}</button>`).join('')}
      </div></div>
      <div class="rail-sec"><h4>severity</h4><div class="rail-opts" id="f-sevs">
        ${SEVS.map(([k, v]) => `<button class="opt${f.sev === k ? ' on' : ''}" data-v="${k}">${SEV_LABEL[v]}</button>`).join('')}
      </div></div>
      <div class="rail-sec"><h4>disposition <span class="rail-keys mono">p·s·e·d</span></h4><div class="rail-opts" id="f-disps">
        ${DISPS.map(d => `<button class="opt${f.action === d ? ' on' : ''}" data-v="${d}">${DISP_LABEL[d]}</button>`).join('')}
      </div></div>
      <div class="rail-sec"><h4>reason codes</h4><div class="rail-opts" id="f-reasons"><span class="mono" style="color:var(--tx-dim)">loading…</span></div></div>
      <div class="rail-sec"><h4>window</h4><div class="rail-opts" id="f-wins">
        ${WINDOWS.map(w => `<button class="opt${f.last === w ? ' on' : ''}" data-v="${w}">last ${w}</button>`).join('')}
      </div></div>
      <div class="rail-sec"><h4>density <span class="rail-keys mono">1·2·3</span></h4><div class="rail-opts" id="f-density">
        ${DENSITIES.map((d, i) => `<button class="opt${f.density === d ? ' on' : ''}" data-v="${d}">${i + 1}</button>`).join('')}
      </div></div>
    </aside>
    <section class="tape-col">
      <div id="filter-tokens" class="filter-tokens"></div>
      <div id="jump-pill" class="jump-pill mono" hidden>▲ <span id="jump-n">0</span> new — jump to live <span class="mono-dim">(Shift+G)</span></div>
      <div id="tape" class="tape" tabindex="0" aria-label="decision tape">${skeletonRows(10)}</div>
    </section>
  </div>`;

  const tape = root.querySelector('#tape');
  const pill = root.querySelector('#jump-pill');

  const apiParams = () => {
    const p = { limit: 50 };
    if (f.team) p.team = f.team;
    if (f.action) p.action = f.action;
    if (f.reason) p.reason = f.reason;
    if (f.sev) p.sev = f.sev;
    if (f.fpr) p.fingerprint = f.fpr.replace(/^fpr:/, '');
    if (f.q) p.q = f.q;
    if (f.last && !f.fpr) { const iso = windowToIso(f.last); if (iso) p.from = iso; }
    return p;
  };

  function renderTokens() {
    const toks = [];
    if (f.team) toks.push(['team', f.team]);
    if (f.action) toks.push(['action', DISP_LABEL[f.action] || f.action]);
    if (f.reason) toks.push(['reason', f.reason]);
    if (f.sev) toks.push(['sev', 'SEV' + f.sev]);
    if (f.fpr) toks.push(['fpr', f.fpr]);
    if (f.q) toks.push(['q', '“' + f.q + '”']);
    toks.push(['window', 'last ' + f.last]);
    root.querySelector('#filter-tokens').innerHTML = toks.map(([k, v]) =>
      `<button class="token mono" data-k="${k}" title="clear">${esc(k)}=${esc(v)} ✕</button>`).join('');
    root.querySelectorAll('.token').forEach(t => t.addEventListener('click', () => {
      const k = t.dataset.k;
      if (k === 'team') f.team = ''; else if (k === 'action') f.action = '';
      else if (k === 'reason') f.reason = ''; else if (k === 'sev') f.sev = '';
      else if (k === 'fpr') f.fpr = ''; else if (k === 'q') f.q = '';
      syncRoute(); load();
    }));
  }

  function paintRows() {
    if (!rows.length) {
      const desc = [f.team && `team=${f.team}`, f.action && `action=${f.action}`, f.reason && `reason=${f.reason}`].filter(Boolean).join(' ');
      tape.innerHTML = emptyBlock(
        `No decisions in the last ${f.last}${desc ? ' for ' + desc : ''}. The gate is armed and watching — widen the window or check another team.`,
        'hint: last=7d');
      return;
    }
    tape.innerHTML = rows.map((d, i) => decisionRow(d, { flips, density: f.density, selected: i === selIdx, thresholds: DEFAULT_THRESHOLDS })).join('');
    tape.querySelectorAll('.row[data-id]').forEach(r => {
      r.addEventListener('click', (e) => {
        if (e.target.closest('a,button')) return;
        ctx.openDrawer(Number(r.dataset.id), { bins: calBins, flips });
      });
      r.addEventListener('keydown', (e) => { if (e.key === 'Enter') ctx.openDrawer(Number(r.dataset.id), { bins: calBins, flips }); });
    });
    tape.querySelectorAll('.row-team').forEach(b => b.addEventListener('click', (e) => {
      e.stopPropagation(); f.team = b.dataset.team; syncRoute(); load();
    }));
    tape.querySelectorAll('.fpr-link').forEach(a => a.addEventListener('click', (e) => {
      if (e.altKey || e.metaKey || e.ctrlKey) return;
      e.preventDefault();
      try { navigator.clipboard.writeText(a.dataset.fpr); } catch {}
      location.hash = a.getAttribute('href');
    }));
  }

  function paintStrip() {
    const nPages = rows.filter(r => r.disposition === 'page_now').length;
    const nSuppress = rows.filter(r => r.disposition === 'suppress').length;
    ctx.setStrip(stripRiver({ nPages, nSuppress, windowLabel: f.last, newestIso, apiDown, apiMsg }));
  }

  async function load() {
    renderTokens();
    tape.innerHTML = skeletonRows(10);
    ctx.setSrcBadge(null); /* badge shows first, skeleton after — Law L2 */
    try {
      const [env, flipEnv] = await Promise.all([
        Data.getDecisions(apiParams()),
        Data.getFlips('7d').catch(() => null),
      ]);
      rows = env.data || [];
      newestIso = rows.length ? rows[0].time : null;
      apiDown = false;
      if (flipEnv?.data?.flips) {
        flips = {};
        for (const fr of flipEnv.data.flips) if (fr.flipped) flips[fr.input_sha256] = fr;
      }
      ctx.setSrcBadge(env.meta?.data_source);
      ctx.setDsVersion(Data.datasetVersion);
      /* team distribution for the confidence bar, cached per team */
      try {
        const cal = await Data.getCalibration(rows[0]?.team || 'data');
        calBins = cal.data.bins;
        ctx.setSrcBadge(cal.meta?.data_source || env.meta?.data_source);
      } catch { calBins = null; }
      /* reason-code chips from the loaded window */
      const reasons = [...new Set(rows.map(r => r.reason).filter(Boolean))];
      root.querySelector('#f-reasons').innerHTML = reasons.map(r =>
        `<button class="opt${f.reason === r ? ' on' : ''}" data-v="${r}">${esc(r)}</button>`).join('') || '<span class="mono" style="color:var(--tx-dim)">none in window</span>';
      root.querySelectorAll('#f-reasons .opt').forEach(b => b.addEventListener('click', () => {
        f.reason = f.reason === b.dataset.v ? '' : b.dataset.v; syncRoute(); load();
      }));
    } catch (e) {
      apiDown = true; apiMsg = `GET /api/decisions → ${e.status || 'unreachable'}`;
      tape.innerHTML = errorBlock({
        what: `Couldn't reach the decisions API (GET /api/decisions → ${e.status || 'unreachable'}).`,
        detail: e.message || '',
        retryFn: load,
      }) + (rows.length ? '<div class="stale-note mono">showing last-known snapshot below — the gate is unaffected.</div>' : '');
    }
    paintRows(); paintStrip();
  }

  function syncRoute() {
    const p = new URLSearchParams();
    if (f.team) p.set('team', f.team); if (f.action) p.set('action', f.action);
    if (f.reason) p.set('reason', f.reason); if (f.sev) p.set('sev', f.sev);
    if (f.fpr) p.set('fpr', f.fpr); if (f.q) p.set('q', f.q);
    if (f.last !== '24h') p.set('last', f.last);
    if (f.density !== 'compact') p.set('density', f.density);
    if (Data.mode === 'mock') p.set('mock', '1');
    history.replaceState(null, '', routeHref('river', p));
  }

  /* rail wiring */
  const optGroup = (id, fn) => root.querySelectorAll('#' + id + ' .opt').forEach(b =>
    b.addEventListener('click', () => { fn(b); syncRoute(); load(); }));
  optGroup('f-teams', b => {
    const cur = f.team.split(',').filter(Boolean);
    f.team = cur.includes(b.dataset.v) ? cur.filter(t => t !== b.dataset.v).join(',') : [...cur, b.dataset.v].join(',');
    root.querySelectorAll('#f-teams .opt').forEach(o => o.classList.toggle('on', f.team.split(',').includes(o.dataset.v)));
  });
  optGroup('f-sevs', b => { f.sev = f.sev === b.dataset.v ? '' : b.dataset.v; });
  optGroup('f-disps', b => { f.action = f.action === b.dataset.v ? '' : b.dataset.v; });
  optGroup('f-wins', b => { f.last = b.dataset.v; });
  optGroup('f-density', b => { f.density = b.dataset.v; paintRows(); });

  /* pin-on-scroll: the tape never re-sorts under you */
  tape.addEventListener('scroll', () => {
    const atHead = tape.scrollTop < 40;
    if (atHead && pinned) { pinned = false; pendingNew = 0; pill.hidden = true; paintRows(); }
    else if (!atHead && !pinned) pinned = true;
  });
  pill.addEventListener('click', () => {
    pinned = false; pendingNew = 0; pill.hidden = true;
    tape.scrollTop = 0; paintRows();
  });

  /* SSE: prepend to the head; gap markers on drop */
  const stream = new Stream();
  stream.on('decision', (d) => {
    if (rows.some(r => r.id === d.id)) return;
    rows.unshift(d); newestIso = d.time;
    if (pinned) {
      pendingNew++;
      root.querySelector('#jump-n').textContent = pendingNew;
      pill.hidden = false;
    } else {
      paintRows();
      const first = tape.querySelector('.row');
      if (first) { first.classList.add('row-new'); setTimeout(() => first.classList.remove('row-new'), 140); }
    }
    paintStrip();
  });
  stream.on('gap', (g) => {
    const secs = g.quietMs != null ? Math.round(g.quietMs / 1000) : null;
    const marker = document.createElement('div');
    marker.innerHTML = gapMarker({ seconds: secs, attempt: stream.attempt, reason: g.reason || 'SSE down' });
    tape.prepend(marker.firstChild);
    ctx.setStrip(stripRiver({ nPages: 0, nSuppress: 0, windowLabel: f.last, gap: true }));
  });
  stream.on('state', (s) => ctx.setSseState(s, stream.attempt));
  ctx.registerCleanup(() => stream.stop());

  /* keyboard: the river is fully operable without a mouse */
  const onKey = (e) => {
    if (e.target.matches('input,textarea')) return;
    const move = (dir) => {
      selIdx = Math.max(0, Math.min(rows.length - 1, selIdx + dir));
      paintRows();
      tape.querySelectorAll('.row')[selIdx]?.scrollIntoView({ block: 'nearest' });
      tape.querySelectorAll('.row')[selIdx]?.focus({ preventScroll: true });
    };
    if (e.key === 'j') move(1);
    else if (e.key === 'k') move(-1);
    else if (e.key === 'Enter' && selIdx >= 0) ctx.openDrawer(rows[selIdx].id, { bins: calBins, flips });
    else if (e.key === '/') { e.preventDefault(); ctx.openPalette('q='); }
    else if (e.key === 'G' && e.shiftKey) pill.click();
    else if (e.key === 'p' || e.key === 's' || e.key === 'e' || e.key === 'd') {
      const map = { p: 'page_now', s: 'suppress', e: 'page_business_hours', d: 'passthrough' };
      f.action = f.action === map[e.key] ? '' : map[e.key];
      root.querySelectorAll('#f-disps .opt').forEach(o => o.classList.toggle('on', f.action === o.dataset.v));
      syncRoute(); load();
    } else if (e.key >= '1' && e.key <= '3') {
      f.density = DENSITIES[Number(e.key) - 1];
      root.querySelectorAll('#f-density .opt').forEach((o, i) => o.classList.toggle('on', i === Number(e.key) - 1));
      syncRoute(); paintRows();
    } else if (e.key === '?') ctx.showKeymap();
  };
  document.addEventListener('keydown', onKey);
  ctx.registerCleanup(() => document.removeEventListener('keydown', onKey));

  ctx.setScreenCode('RIVER');
  await load();
  stream.start(rows.length ? rows[0].id : 0);
}
