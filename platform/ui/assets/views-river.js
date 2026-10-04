/* views-river.js — the decision river: a live, queryable tape of gate decisions.
 * Reads: GET /api/decisions (+filters), GET /api/decision/<id>, SSE /api/stream.
 * Freedom 2: "See why this paged you — and why the others didn't." */
import { Data, Stream } from './api.js';
import { decisionRow, driftRow, srcBadge, skeletonRows, errorBlock, emptyBlock, gapMarker,
         drawerHtml, esc, closeGlyph } from './components.js';
import { validateDecisionSummary, isQueryableField } from './contract.js';
import { loadPins, savePins, makePin, validatePinDoc, validatePinAgainstSample,
         resolvePath, exportPins, importPinsJson } from './pins.js';
import { parseHash, routeHref, stripRiver, fmtInt, SEV_LABEL, DISP_LABEL,
         DEFAULT_THRESHOLDS, ageStr, tailState } from './lib.js';
import { freshnessBadge, streamFreshness, FRESHNESS_BUDGETS } from './freshness.js';

const TEAMS = ['platform', 'network', 'data', 'product_backend', 'security', 'cannot_determine'];
const SEVS = [['1', 'p1_critical'], ['2', 'p2_high'], ['3', 'p3_medium'], ['4', 'p4_low']];
const DISPS = ['page_now', 'page_business_hours', 'suppress', 'passthrough'];
const WINDOWS = ['1h', '24h', '7d', '30d'];
const DENSITIES = ['comfortable', 'compact', 'tape'];
/* §6: 60fps at 10,000+ rows — windowed rendering is mandatory, not an
 * optimization. Fixed row heights per density keep the window math O(1);
 * rows are keyed by decision id (stable rows — the tape never re-sorts
 * under you, and focus/selection survive re-renders). */
const ROW_H = { comfortable: 46, compact: 30, tape: 24 };
const OVERSCAN = 12;

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
  let pins = loadPins(), pinsOff = false;

  /* The fixed typed decision contract is the renderer’s authority (contract.js).
   * A row the contract cannot describe renders as drift — never as a decision. */
  function rowHtml(d, i) {
    const v = validateDecisionSummary(d);
    if (!v.ok) return driftRow(d, v.errors);
    return decisionRow(d, { flips, density: f.density, selected: i === selIdx, thresholds: DEFAULT_THRESHOLDS, pins, pinsOff });
  }

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
      <div class="rail-sec pins-rail"><h4>field pins <span class="rail-keys mono">display only</span></h4>
        <div id="pins-list"></div>
        <div class="rail-opts" style="margin-top:6px"><button class="opt" id="pins-toggle" title="one-keypress escape hatch: hide all field pins">pins off</button></div>
        <details class="pin-form" style="margin-top:8px"><summary class="mono" style="cursor:pointer;color:var(--tx-1)">+ pin a field</summary>
          <input id="pin-name" placeholder="name — e.g. cluster" aria-label="pin name">
          <input id="pin-path" placeholder="path — e.g. labels.cluster" aria-label="pin path (dot-separated, from alert)">
          <input id="pin-integ" placeholder="integration — e.g. prometheus" aria-label="pin integration">
          <div class="rail-opts" style="margin-top:6px">
            <button class="opt" id="pin-preview-btn">preview</button>
            <button class="opt" id="pin-save">save pin</button>
          </div>
          <div id="pin-preview" class="pin-preview" hidden></div>
          <div id="pin-msg"></div>
        </details>
        <details style="margin-top:8px"><summary class="mono" style="cursor:pointer;color:var(--tx-1)">export / import</summary>
          <div class="rail-opts" style="margin:6px 0"><button class="opt" id="pins-export">show JSON</button></div>
          <textarea id="pins-io" class="mono" rows="4" style="width:100%;background:var(--bg-0);border:1px solid var(--line-0);color:var(--tx-0);border-radius:var(--radius-chip)" placeholder='paste {"pins":[...]} to import'></textarea>
          <div class="rail-opts" style="margin-top:6px"><button class="opt" id="pins-import">import</button></div>
          <div id="pins-io-msg"></div>
        </details>
        <p class="drawer-note" style="margin-top:8px">Pins are display bindings: they never filter, sort, or recolor the river. Paths are relative to the alert payload.</p>
      </div>
    </aside>
    <section class="tape-col">
      <div id="filter-tokens" class="filter-tokens"></div>
      <div id="tape-fresh" class="tape-fresh mono"></div>
      <div id="jump-pill" class="jump-pill mono" hidden>▲ <span id="jump-n">0</span> new — jump to live <span class="mono-dim">(Shift+G)</span></div>
      <div id="tail-banner" class="tail-banner mono" hidden></div>
      <div id="tape" class="tape" tabindex="0" aria-label="decision tape"><div id="tape-spacer" class="tape-spacer"><div id="tape-win" class="tape-win"></div></div></div>
    </section>
  </div>`;

  const tape = root.querySelector('#tape');
  const spacer = root.querySelector('#tape-spacer');
  const win = root.querySelector('#tape-win');
  const pill = root.querySelector('#jump-pill');
  let rowH = ROW_H[f.density] || ROW_H.compact;

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
      `<button class="token mono" data-k="${k}" title="clear">${esc(k)}=${esc(v)} ${closeGlyph()}</button>`).join('');
    root.querySelectorAll('.token').forEach(t => t.addEventListener('click', () => {
      const k = t.dataset.k;
      if (k === 'team') f.team = ''; else if (k === 'action') f.action = '';
      else if (k === 'reason') f.reason = ''; else if (k === 'sev') f.sev = '';
      else if (k === 'fpr') f.fpr = ''; else if (k === 'q') f.q = '';
      syncRoute(); load();
    }));
  }

  /* ---------- windowed tape: only the visible rows (+overscan) exist in the DOM.
   * Rows are keyed by decision id — selection and focus survive re-renders
   * (stable rows), and the window never moves under the cursor. ---------- */
  function wireRowEvents(scope) {
    scope.querySelectorAll('.row[data-id]').forEach(r => {
      r.addEventListener('click', (e) => {
        if (e.target.closest('a,button')) return;
        ctx.openDrawer(Number(r.dataset.id), { bins: calBins, flips, freshness: freshForDrawer() });
      });
      r.addEventListener('keydown', (e) => { if (e.key === 'Enter') ctx.openDrawer(Number(r.dataset.id), { bins: calBins, flips, freshness: freshForDrawer() }); });
    });
    scope.querySelectorAll('.row-team').forEach(b => b.addEventListener('click', (e) => {
      e.stopPropagation(); f.team = b.dataset.team; syncRoute(); load();
    }));
    scope.querySelectorAll('.fpr-link').forEach(a => a.addEventListener('click', (e) => {
      if (e.altKey || e.metaKey || e.ctrlKey) return;
      e.preventDefault();
      try { navigator.clipboard.writeText(a.dataset.fpr); } catch {}
      location.hash = a.getAttribute('href');
    }));
  }

  function paintRows() {
    if (!rows.length) {
      spacer.style.height = 'auto';
      const desc = [f.team && `team=${f.team}`, f.action && `action=${f.action}`, f.reason && `reason=${f.reason}`].filter(Boolean).join(' ');
      win.innerHTML = emptyBlock(
        `No decisions in the last ${f.last}${desc ? ' for ' + desc : ''}. The gate is armed and watching — widen the window or check another team.`,
        'hint: last=7d');
      return;
    }
    const st = tape.scrollTop, vh = tape.clientHeight || 640;
    const start = Math.max(0, Math.floor(st / rowH) - OVERSCAN);
    const end = Math.min(rows.length, Math.ceil((st + vh) / rowH) + OVERSCAN);
    spacer.style.height = (rows.length * rowH) + 'px';
    let html = '';
    for (let i = start; i < end; i++) {
      html += `<div class="vrow" style="transform:translateY(${i * rowH}px)">${rowHtml(rows[i], i)}</div>`;
    }
    win.innerHTML = html;
    wireRowEvents(win);
    paintTapeFresh();
  }

  function freshForDrawer() {
    const sf = streamFreshness({ streamState: stream.state, lastEventAt, nowMs: Date.now() });
    return { state: sf.state, asOfIso: sf.asOfIso, waitingOn: sf.waitingOn, budgetMs: FRESHNESS_BUDGETS.decision };
  }
  function paintTapeFresh() {
    const sf = streamFreshness({ streamState: stream.state, lastEventAt, nowMs: Date.now() });
    root.querySelector('#tape-fresh').innerHTML =
      freshnessBadge({ state: sf.state, asOfIso: sf.asOfIso, waitingOn: sf.waitingOn, budgetMs: FRESHNESS_BUDGETS.river, compact: true }) +
      `<span class="mono-dim"> ${fmtInt(rows.length)} decisions · virtualized</span>`;
  }

  function paintStrip() {
    const nPages = rows.filter(r => r.disposition === 'page_now').length;
    const nSuppress = rows.filter(r => r.disposition === 'suppress').length;
    ctx.setStrip(stripRiver({ nPages, nSuppress, windowLabel: f.last, newestIso, apiDown, apiMsg }));
  }

  async function load() {
    renderTokens();
    spacer.style.height = 'auto';
    win.innerHTML = skeletonRows(10);
    ctx.setSrcBadge(null); /* badge shows first, skeleton after — Law L2 */
    try {
      const [env, flipEnv] = await Promise.all([
        Data.getDecisions(apiParams()),
        Data.getFlips('7d').catch(() => null),
      ]);
      rows = env.data || [];
      newestIso = rows.length ? rows[0].time : null;
      lastEventAt = newestIso ? new Date(newestIso).getTime() : null;
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
      spacer.style.height = 'auto';
      win.innerHTML = errorBlock({
        what: `Couldn't reach the decisions API (GET /api/decisions → ${e.status || 'unreachable'}).`,
        detail: e.message || '',
        retryFn: load,
      }) + (rows.length ? '<div class="stale-note mono">showing last-known snapshot below — the gate is unaffected.</div>' : '');
      paintStrip();
      return;
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

  /* ---------- field pins: the composability mechanism (synthesis §3) ----------
   * Display-only bindings. The filter grammar (apiParams above) never consults
   * pins — isQueryableField() in contract.js rejects every pin path by
   * construction. The "pins off" toggle is the one-keypress 3 AM escape hatch. */
  function renderPins() {
    const list = root.querySelector('#pins-list');
    list.innerHTML = pins.length
      ? pins.map(p => `<div class="pin-row"><span class="mono">${esc(p.name)}</span>` +
          `<span class="pin-path mono">${esc(p.path)}</span>` +
          `<button class="opt" data-del="${esc(p.id)}" title="remove pin" style="margin-left:auto">${closeGlyph()}</button></div>`).join('')
      : '<span class="mono" style="color:var(--tx-dim)">no pins — the river works without them</span>';
    list.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', () => {
      pins = savePins(pins.filter(p => p.id !== b.dataset.del));
      renderPins(); paintRows();
    }));
    const tgl = root.querySelector('#pins-toggle');
    tgl.textContent = pinsOff ? 'pins on' : 'pins off';
    tgl.classList.toggle('on', pinsOff);
  }
  root.querySelector('#pins-toggle').addEventListener('click', () => { pinsOff = !pinsOff; renderPins(); paintRows(); });

  function pinDraft() {
    return makePin({
      name: root.querySelector('#pin-name').value,
      path: root.querySelector('#pin-path').value,
      integration: root.querySelector('#pin-integ').value,
      slot: 'river',
    });
  }
  function pinMsg(html, cls) {
    root.querySelector('#pin-msg').innerHTML = html ? `<div class="${cls}">${html}</div>` : '';
  }
  root.querySelector('#pin-preview-btn').addEventListener('click', () => {
    const p = pinDraft();
    const v = validatePinDoc(p);
    if (!v.ok) { pinMsg(esc(v.errors.join('; ')), 'pin-err'); return; }
    const sample = rows[0];
    const r = sample ? resolvePath(sample, p.path) : { found: false, why: 'no decisions loaded yet' };
    const prev = root.querySelector('#pin-preview');
    prev.hidden = false;
    prev.innerHTML = r.found
      ? `<span class="mono">preview on decision #${sample.id}:</span> <span class="mono">${esc(JSON.stringify(r.value).slice(0, 120))}</span>`
      : `<span class="mono">— <span class="pv-why">${esc(r.why)}</span></span>`;
    pinMsg('', '');
  });
  root.querySelector('#pin-save').addEventListener('click', () => {
    const p = pinDraft();
    const v = validatePinAgainstSample(p, rows);
    if (!v.ok) { pinMsg(esc(v.errors.join('; ')), 'pin-err'); return; }
    pins = savePins([...pins, p]);
    renderPins(); paintRows();
    pinMsg((v.warnings.length ? `<div class="pin-warn">${esc(v.warnings.join('; '))}</div>` : '') +
      `<div class="mono" style="color:var(--tx-1)">pin saved — display only, never a filter.</div>`, '');
    root.querySelector('#pin-name').value = ''; root.querySelector('#pin-path').value = ''; root.querySelector('#pin-integ').value = '';
  });
  root.querySelector('#pins-export').addEventListener('click', () => {
    root.querySelector('#pins-io').value = exportPins();
    root.querySelector('#pins-io-msg').innerHTML = '<div class="mono" style="color:var(--tx-1)">pin JSON above — diffable, PR-able.</div>';
  });
  root.querySelector('#pins-import').addEventListener('click', () => {
    const r = importPinsJson(root.querySelector('#pins-io').value);
    root.querySelector('#pins-io-msg').innerHTML = r.ok
      ? `<div class="mono" style="color:var(--tx-1)">imported ${r.imported.length} pin(s).</div>`
      : `<div class="pin-err">${esc(r.errors.join('; '))}${r.imported.length ? ` — imported ${r.imported.length} valid pin(s).` : ''}</div>`;
    pins = loadPins(); renderPins(); paintRows();
  });
  renderPins();

  /* pin-on-scroll: the tape never re-sorts under you. Scroll re-renders the
   * visible window (rAF-throttled) — the window position is the only thing
   * scroll is allowed to change. */
  let scrollRaf = 0;
  tape.addEventListener('scroll', () => {
    const atHead = tape.scrollTop < 40;
    if (atHead && pinned) { pinned = false; pendingNew = 0; pill.hidden = true; }
    else if (!atHead && !pinned) pinned = true;
    if (!scrollRaf) scrollRaf = requestAnimationFrame(() => { scrollRaf = 0; paintRows(); });
  });
  pill.addEventListener('click', () => {
    pinned = false; pendingNew = 0; pill.hidden = true;
    tape.scrollTop = 0; paintRows();
  });

  /* A4 live-tail rule (binding): a live tail is allowed only when the filtered
   * stream is slow enough to read. Above LIVE_TAIL_MAX_PER_SEC the tail pauses
   * itself WITH a visible reason — the operator narrows the filter to regain it.
   * The threshold is a Type 2 starting point, tunable with measurement. */
  let evtTimes = [], tooFast = false, lastEventAt = null;
  function paintTailBanner(rate) {
    const b = root.querySelector('#tail-banner');
    if (tooFast) {
      b.hidden = false;
      b.innerHTML = `Too fast to read: ~${Math.round(rate)}/sec match this filter — live tail paused. ` +
        `<span class="mono-dim">Narrow the filter to regain the tail, or open the audit explorer.</span>`;
    } else b.hidden = true;
  }
  /* SSE: prepend to the head; gap markers on drop */
  const stream = new Stream();
  stream.on('decision', (d) => {
    if (rows.some(r => r.id === d.id)) return;
    evtTimes.push(Date.now());
    lastEventAt = Date.now();
    const ts = tailState(evtTimes, Date.now(), tooFast);
    evtTimes = ts.recent; tooFast = ts.tooFast;
    paintTailBanner(ts.rate);
    rows.unshift(d); newestIso = d.time;
    if (tooFast && rows.length > 500) rows.length = 500; /* bound memory while paused */
    if (pinned || tooFast) {
      pendingNew++;
      root.querySelector('#jump-n').textContent = pendingNew;
      pill.hidden = false;
    } else {
      paintRows();
      const first = win.querySelector('.row');
      if (first) { first.classList.add('row-new'); setTimeout(() => first.classList.remove('row-new'), 140); }
    }
    paintStrip();
  });
  stream.on('gap', (g) => {
    const secs = g.quietMs != null ? Math.round(g.quietMs / 1000) : null;
    const marker = document.createElement('div');
    marker.innerHTML = gapMarker({ seconds: secs, attempt: stream.attempt, reason: g.reason || 'SSE down' });
    /* gap markers pin to the head of the visible window — they are part of
     * the honest tape, never silently skipped */
    win.insertAdjacentHTML('afterbegin', marker.innerHTML);
    ctx.setStrip(stripRiver({ nPages: 0, nSuppress: 0, windowLabel: f.last, gap: true }));
  });
  stream.on('state', (s) => { ctx.setSseState(s, stream.attempt); paintTapeFresh(); });
  ctx.registerCleanup(() => stream.stop());

  /* keyboard: the river is fully operable without a mouse */
  const onKey = (e) => {
    if (e.target.matches('input,textarea')) return;
    const move = (dir) => {
      selIdx = Math.max(0, Math.min(rows.length - 1, selIdx + dir));
      /* keep the selection inside the rendered window, then paint */
      const y = selIdx * rowH, vh = tape.clientHeight || 640;
      if (y < tape.scrollTop || y > tape.scrollTop + vh - rowH) tape.scrollTop = Math.max(0, y - vh / 2);
      paintRows();
      const el = win.querySelector(`.row[data-id="${rows[selIdx]?.id}"]`);
      el?.focus({ preventScroll: true });
    };
    if (e.key === 'j') move(1);
    else if (e.key === 'k') move(-1);
    else if (e.key === 'Enter' && selIdx >= 0) ctx.openDrawer(rows[selIdx].id, { bins: calBins, flips, freshness: freshForDrawer() });
    else if (e.key === '/') { e.preventDefault(); ctx.openPalette('q='); }
    else if (e.key === 'G' && e.shiftKey) pill.click();
    else if (e.key === 'x') { /* the one-keypress pins escape hatch */
      pinsOff = !pinsOff; renderPins(); paintRows();
    }
    else if (e.key === 'p' || e.key === 's' || e.key === 'e' || e.key === 'd') {
      const map = { p: 'page_now', s: 'suppress', e: 'page_business_hours', d: 'passthrough' };
      f.action = f.action === map[e.key] ? '' : map[e.key];
      root.querySelectorAll('#f-disps .opt').forEach(o => o.classList.toggle('on', f.action === o.dataset.v));
      syncRoute(); load();
    } else if (e.key >= '1' && e.key <= '3') {
      f.density = DENSITIES[Number(e.key) - 1];
      rowH = ROW_H[f.density];
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
