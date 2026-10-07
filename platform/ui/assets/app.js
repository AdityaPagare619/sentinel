/* app.js — Sentinel console v2 shell.
 *
 * Hash router, mode bar (LIVE vs SIMULATED — in-band), the pipeline strip
 * (the design model made visible), kill-switch + health chips, detail
 * drawer, command palette, theme toggle. Views own their screens and talk
 * to the Store, never to the backend directly.
 */
import { Store } from './store.js';
import { esc, sevChip, dispChip, closeGlyph } from './components.js';
import { SEV_LABEL } from './synth.js';
import { renderNow } from './view-now.js';
import { renderPages } from './view-pages.js';
import { renderProofs } from './view-proofs.js';
import { renderRiver } from './view-river.js';
import { renderSafety } from './view-safety.js';
import { renderAudit } from './view-audit.js';
/* production entry: unconfigured live console shows setup, never data */
import { backendUrlConfigured } from './api.js';
import { renderSetup } from './views-setup.js';
/* carried forward (adapted only by nav + labeling): the lab, keys, onboarding */
import { renderSim } from './views-sim.js';
import { renderShadow } from './views-shadow.js';
import { renderSettings } from './views-settings.js';
import { renderStart } from './views-start.js';

const NAV = [
  { id: 'now', code: 'NOW', label: 'Now — what\'s happening' },
  { id: 'pages', code: 'PAGES', label: 'Open pages' },
  { id: 'proofs', code: 'PROOFS', label: 'Suppression proof ledger' },
  { id: 'river', code: 'RIVER', label: 'Decision river' },
  { id: 'safety', code: 'SAFETY', label: 'Kill · policy · auth · race · degraded' },
  { id: 'audit', code: 'AUDIT', label: 'Audit timeline' },
  { id: 'lab', code: 'LAB', label: 'Simulator · shadow' },
  { id: 'keys', code: 'KEYS', label: 'Integrations' },
  { id: 'start', code: 'START', label: 'Onboarding' },
];

const el = {
  view: document.getElementById('view'),
  modebar: document.getElementById('modebar'),
  nav: document.getElementById('nav'),
  pipeline: document.getElementById('pipeline'),
  killChip: document.getElementById('kill-chip'),
  healthChip: document.getElementById('health-chip'),
  drawer: document.getElementById('drawer'),
  palette: document.getElementById('palette'),
  paletteInput: document.getElementById('palette-input'),
  paletteList: document.getElementById('palette-list'),
};
let cleanups = [];
const ctx = {
  registerCleanup(f) { cleanups.push(f); },
  setScreenCode(code) {
    const id = ({ NOW: 'now', PAGES: 'pages', PROOFS: 'proofs', RIVER: 'river', SAFETY: 'safety', AUDIT: 'audit', LAB: 'lab', KEYS: 'keys', START: 'start',
      CAL: 'lab', SIM: 'lab', SHADOW: 'lab', SETTINGS: 'keys' })[code] || 'now';
    el.nav.innerHTML = NAV.map((s) =>
      `<a href="#/${s.id}" class="${s.id === id ? 'on' : ''}" title="${esc(s.label)}">${s.code}</a>`).join('');
  },
  openDrawer(id) { openDrawer(id); },
  /* legacy-view compat: the v2 shell carries source/freshness/version as
   * ambient chrome (mode bar + pipeline strip + health chip), so per-view
   * badges are redundant. These are intentional no-ops, not missing wiring. */
  setSrcBadge() {},
  setStrip() {},
  setDsVersion() {},
  setSseState() {},
};

/* ---------- hash parsing (supports sub-routes via query params) ---------- */
function parseLocation() {
  const h = location.hash || '#/now';
  const m = h.match(/^#\/([a-z]+)(\?(.*))?$/);
  if (!m) return { screen: 'now', params: {} };
  const params = {};
  for (const part of (m[3] || '').split('&')) {
    if (!part) continue;
    const [k, v] = part.split('=');
    params[decodeURIComponent(k)] = decodeURIComponent(v || '');
  }
  return { screen: m[1], params };
}
function legacyParams(params) { return new URLSearchParams(params); }

/* ---------- mode bar ---------- */
/* "SIMULATED SHOWCASE" + "snapshot — not live" are builder-enforced
 * in-band honesty strings (STAGING_REQUIRED) — keep them visible here. */
function paintModebar() {
  if (Store.isSimulated) {
    el.modebar.className = 'modebar sim';
    el.modebar.innerHTML = `<span><strong>◈ SIMULATED SHOWCASE</strong> — synthetic pipeline, seed <span class="mono">${Store.seed}</span> ·
      snapshot — not live · every number traceable · <span class="why-link" id="why-mode">why these numbers?</span> ·
      <span class="note">not your system · nothing here pages anyone</span></span>`;
    const w = document.getElementById('why-mode');
    if (w) w.addEventListener('click', () => openDrawer('__synth'));
  } else {
    el.modebar.className = 'modebar live';
    el.modebar.innerHTML = `<span><strong>● LIVE</strong> — connected to your Sentinel backend · real decisions · real paging</span>`;
  }
}

/* ---------- pipeline strip ---------- */
function paintPipeline() {
  const p = Store.pipeline();
  el.pipeline.innerHTML = p.stages.map((s, i) => `
    <div class="stage${p.health !== 'live' ? ' degraded' : ''}">
      <div class="s-label">${esc(s.label)}</div>
      <div class="s-count">${s.count.toLocaleString()}</div>
      <div class="s-note">${esc(s.note || '')} ${i < p.stages.length - 1 ? '<span class="s-arrow">→</span>' : ''}</div>
      ${Store.isSimulated ? `<div class="why" data-why="${esc(s.key)}">why this number?</div>` : ''}
    </div>`).join('');
  el.pipeline.querySelectorAll('[data-why]').forEach((w) => w.addEventListener('click', (e) => {
    e.stopPropagation();
    const info = Store.why(w.dataset.why);
    alert(`${w.dataset.why}: ${info.count} events\nsample: ${(info.sampleEventIds || []).slice(0, 5).join(', ')}\n(seed ${Store.seed} — re-runnable, inspectable in synth.js)`);
  }));
  // health chip
  const hc = el.healthChip;
  hc.textContent = p.health === 'live' ? `● pipeline live${p.lagMs != null ? ` · ${p.lagMs}ms` : ''}`
    : p.health === 'degraded' ? `◌ degraded · lag ${Math.round((p.lagMs || 0) / 1000)}s`
    : `○ stale · lag ${Math.round((p.lagMs || 0) / 1000)}s`;
  hc.className = 'health-chip mono ' + (p.health === 'live' ? 'ok' : p.health === 'degraded' ? 'warn' : 'bad');
  // kill chip — the label names what it controls (opens the kill console)
  const kc = el.killChip;
  const engaged = p.killSwitch === 'ENGAGED';
  kc.textContent = engaged ? '◼ KILL ENGAGED' : '◻ kill armed';
  kc.className = 'kill-chip ' + (engaged ? 'engaged' : 'armed');
  kc.setAttribute('aria-label', engaged
    ? 'Kill switch: ENGAGED — paging halted. Opens the kill console.'
    : 'Kill switch: armed — the gate is paging normally. Opens the kill console.');
}

/* ---------- health banner sync ----------
 * The in-view degraded banners (NOW, SAFETY) render at view time, but the sim
 * ticks health every 2.5s — without this, the banner never appears during a
 * tick-driven degraded window. Sync toggles any [data-health-banner] in the
 * current view and refreshes its health word + lag readout. */
function syncHealthBanner() {
  const p = Store.pipeline();
  const live = p.health === 'live';
  el.view.querySelectorAll('[data-health-banner]').forEach((b) => {
    b.hidden = live;
    if (!live) {
      const w = b.querySelector('[data-health-word]');
      if (w) w.textContent = p.health.toUpperCase();
      const l = b.querySelector('[data-lag]');
      if (l) l.textContent = `${Math.round((p.lagMs || 0) / 1000)}s`;
    }
  });
}

/* ---------- drawer ---------- */
function openDrawer(id) {
  if (id === '__synth') {
    el.drawer.innerHTML = `
      <div class="drawer-head"><span class="mono">the synthetic pipeline</span>
      <button class="drawer-close" aria-label="close">${closeGlyph()}</button></div>
      <p style="color:var(--tx-2);font-size:13px">This console is running a <strong>live simulation</strong> of
      Sentinel's paging pipeline in your browser. Inputs are synthetic (seed <span class="mono">${Store.seed}</span>);
      the pipeline logic — grouping, dispositions, proofs, fail-open — is the real logic. Every count on screen
      traces to generating events; ask "why this number?" on any pipeline stage.</p>
      <h4>HONESTY RULES</h4>
      <div class="note">· labeled SIMULATED on every surface · nothing here pages anyone<br>
      · same seed → same history (deterministic, re-runnable)<br>
      · the generator is <span class="mono">assets/synth.js</span> — read it</div>`;
    el.drawer.hidden = false;
    el.drawer.querySelector('.drawer-close').addEventListener('click', closeDrawer);
    return;
  }
  const d = Store.decisionById(id);
  if (!d) { return; }
  const pr = d.proof || {};
  el.drawer.innerHTML = `
    <div class="drawer-head"><span class="mono">decision ${esc(d.id)}</span>
    <button class="drawer-close" aria-label="close">${closeGlyph()}</button></div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px">
      <span class="sev-chip sev-${esc(d.severity)}">${esc(SEV_LABEL[d.severity] || d.severity)}</span>
      ${dispChip(d.disposition, d.reasonCode)}
    </div>
    <h4>PROOF</h4>
    <dl class="kv">
      <dt>rule</dt><dd>${esc(pr.rule || '—')}</dd>
      <dt>configured by</dt><dd>${esc(pr.configuredBy || '—')}</dd>
      <dt>detail</dt><dd>${esc(pr.detail || '—')}</dd>
      <dt>confidence</dt><dd>${d.confidence}</dd>
      <dt>evidence age</dt><dd>${pr.evidenceAgeMs}ms</dd>
      <dt>fingerprint</dt><dd>${esc(d.fingerprint || '—')}</dd>
    </dl>
    <h4>COST OF INACTION</h4>
    <p style="font-size:13px;color:var(--tx-2)">${esc(d.costOfInaction || '—')}</p>
    ${d.disposition === 'suppress' && !d.undone ? `
      <button class="btn small" id="drawer-undo">↩ Undo this suppression</button>` : ''}
    ${d.undone ? `<div class="undone-banner">↩ reversed — will page on next alert</div>` : ''}
  `;
  el.drawer.hidden = false;
  el.drawer.querySelector('.drawer-close').addEventListener('click', closeDrawer);
  const u = el.drawer.querySelector('#drawer-undo');
  if (u) u.addEventListener('click', () => { Store.undoSuppression(d.id); openDrawer(id); });
}
function closeDrawer() { el.drawer.hidden = true; el.drawer.innerHTML = ''; }

/* ---------- lab (carried forward) ---------- */
async function renderLab(root, params, ctx2) {
  ctx2.setScreenCode('LAB');
  const tab = params.tab || 'simulator';
  root.innerHTML = `
    <div class="view-head"><h1>Lab</h1>
    <div class="sub">Tune and review shadow evaluations — analysis, not operations.</div></div>
    <div class="tabs">
      <a href="#/lab?tab=simulator" class="${tab === 'simulator' ? 'on' : ''}">SIMULATOR</a>
      <a href="#/lab?tab=shadow" class="${tab === 'shadow' ? 'on' : ''}">SHADOW</a>
    </div>
    <div id="lab-body"></div>`;
  const body = root.querySelector('#lab-body');
  const lp = legacyParams(params);
  if (tab === 'shadow') await renderShadow(body, lp, ctx2);
  else await renderSim(body, lp, ctx2);
}

/* ---------- router ---------- */
const VIEWS = {
  now: renderNow, pages: renderPages, proofs: renderProofs, river: renderRiver,
  safety: renderSafety, audit: renderAudit, lab: renderLab,
  keys: (r, p, c) => { c.setScreenCode('KEYS'); return renderSettings(r, legacyParams(p), c); },
  start: (r, p, c) => { c.setScreenCode('START'); return renderStart(r, legacyParams(p), c); },
};

async function route() {
  cleanups.forEach((f) => { try { f(); } catch {} });
  cleanups = [];
  closeDrawer();
  /* production entry contract: an unconfigured live console shows the
   * backend-setup screen, never invented data (P3 honesty). */
  if (window.SENTINEL_DATA_MODE === 'live' && !backendUrlConfigured()) {
    ctx.setScreenCode('KEYS');
    await renderSetup(el.view);
    try { window.scrollTo(0, 0); } catch {}
    return;
  }
  const { screen, params } = parseLocation();
  const view = VIEWS[screen] || renderNow;
  try {
    await view(el.view, params, ctx);
  } catch (e) {
    el.view.innerHTML = `<div class="empty"><div class="e-big">Couldn't render this screen.</div>
      <div class="note mono">${esc(e.message || String(e))}</div>
      <div class="note">The gate is unaffected — paging behavior does not depend on this screen.</div></div>`;
  }
  try { window.scrollTo(0, 0); } catch {} /* jsdom and exotic embeds may lack scrollTo */
}

/* ---------- palette ---------- */
function openPalette() {
  el.palette.hidden = false;
  el.paletteInput.value = '';
  el.paletteInput.focus();
  paintPalette('');
  el.paletteInput.oninput = () => paintPalette(el.paletteInput.value);
}
function closePalette() { el.palette.hidden = true; el.paletteInput.value = ''; }
function paintPalette(q) {
  const items = NAV.filter((s) => !q || s.label.toLowerCase().includes(q.toLowerCase()) || s.code.toLowerCase().includes(q.toLowerCase()));
  el.paletteList.innerHTML = items.map((s, i) =>
    `<button class="palette-item mono" data-i="${i}" data-href="#/${s.id}">${s.code} — ${esc(s.label)}</button>`).join('');
  el.paletteList.querySelectorAll('.palette-item').forEach((b) => b.addEventListener('click', () => {
    location.hash = b.dataset.href; closePalette();
  }));
}

/* ---------- boot ---------- */
function boot() {
  // theme
  try {
    if (localStorage.getItem('sentinel.theme') === 'light') document.documentElement.dataset.theme = 'light';
  } catch {}
  document.getElementById('theme-btn').addEventListener('click', () => {
    const light = document.documentElement.dataset.theme === 'light';
    if (light) { delete document.documentElement.dataset.theme; try { localStorage.removeItem('sentinel.theme'); } catch {} }
    else { document.documentElement.dataset.theme = 'light'; try { localStorage.setItem('sentinel.theme', 'light'); } catch {} }
  });
  // kill chip → safety/kill
  el.killChip.addEventListener('click', () => { location.hash = '#/safety?sub=kill'; });
  // palette
  document.getElementById('palette-btn').addEventListener('click', openPalette);
  el.paletteInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closePalette();
    if (e.key === 'Enter') {
      const first = el.paletteList.querySelector('.palette-item');
      if (first) { location.hash = first.dataset.href; closePalette(); }
    }
  });
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
    if (e.key === 'Escape') { closePalette(); closeDrawer(); }
  });
  el.palette.addEventListener('click', (e) => { if (e.target === el.palette) closePalette(); });

  paintModebar();
  paintPipeline();
  // the pipeline strip is alive: re-paint on every pipeline event
  Store.on('pipeline', paintPipeline);
  Store.on('health', () => { paintPipeline(); syncHealthBanner(); });
  Store.on('killswitch', paintPipeline);

  window.addEventListener('hashchange', route);
  route();
}

boot();
