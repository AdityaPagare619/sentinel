/* app.js — shell: hash router, top bar, verdict strip, detail drawer,
 * command palette, SSE badge, MOCK DATA banner. Views own their screens. */
import { Data, backendUrlConfigured, setBackendUrl } from './api.js';
import { drawerHtml, esc, closeGlyph } from './components.js';
import { parseHash, routeHref, SCREENS } from './lib.js';
import { renderRiver } from './views-river.js';
import { renderCal } from './views-cal.js';
import { renderSim } from './views-sim.js';
import { renderAudit } from './views-audit.js';
import { renderShadow } from './views-shadow.js';
import { renderSettings } from './views-settings.js';
import { renderStart } from './views-start.js';
import { renderSetup } from './views-setup.js';
import { loadPins } from './pins.js';

const VIEWS = { river: renderRiver, calibration: renderCal, simulator: renderSim, audit: renderAudit, shadow: renderShadow, settings: renderSettings, start: renderStart };
const CODE = { river: 'RIVER', calibration: 'CAL', simulator: 'SIM', audit: 'AUDIT', shadow: 'SHADOW', settings: 'KEYS', start: 'START' };

const el = {
  view: document.getElementById('view'),
  strip: document.getElementById('strip'),
  nav: document.getElementById('nav'),
  srcBadge: document.getElementById('src-badge'),
  dsVersion: document.getElementById('ds-version'),
  sseState: document.getElementById('sse-state'),
  drawer: document.getElementById('drawer'),
  mockBanner: document.getElementById('mock-banner'),
  palette: document.getElementById('palette'),
  paletteInput: document.getElementById('palette-input'),
  paletteList: document.getElementById('palette-list'),
  paletteHint: document.getElementById('palette-hint'),
};
let cleanups = [];
const ctx = {
  registerCleanup(f) { cleanups.push(f); },
  setScreenCode(code) {
    el.nav.innerHTML = SCREENS.map(s =>
      `<a href="${routeHref(s.id, Data.mode === 'mock' ? { mock: '1' } : {})}" class="${s.code === code ? 'on' : ''}">${s.code}</a>`).join('');
  },
  setStrip(text) { el.strip.textContent = text || ''; el.strip.hidden = !text; },
  setSrcBadge(source) {
    if (!source) return; /* badge shows first — never render a naked view */
    el.srcBadge.dataset.src = source;
    el.srcBadge.textContent = '◈ ' + source;
  },
  setDsVersion(v) { el.dsVersion.textContent = v || 'ds:—'; },
  setSseState(s, attempt) {
    const map = {
      live: ['● live', 'ok'], paused: ['○ paused', ''],
      reconnecting: [`◌ reconnecting…${attempt ? ' (attempt ' + attempt + ')' : ''}`, 'warn'],
      polling: ['◌ polling', 'warn'],
      snapshot: ['◌ snapshot — not live', 'warn'],
    };
    const [txt, cls] = map[s] || [s, ''];
    el.sseState.textContent = txt;
    el.sseState.className = 'sse-state mono ' + cls;
  },
  async openDrawer(id, { bins = null, flips = {} } = {}) {
    try {
      const env = await Data.getDecision(id);
      const d = env.data;
      let b = bins;
      if (!b) {
        try { const cal = await Data.getCalibration(d.team); b = cal.data.bins; } catch {}
      }
      el.drawer.innerHTML = drawerHtml(d, { bins: b, flips, datasetVersion: Data.datasetVersion, pins: loadPins() });
      el.drawer.hidden = false;
      el.drawer.querySelector('[data-close]').addEventListener('click', closeDrawer);
      el.drawer.querySelectorAll('[data-copy]').forEach(btn => btn.addEventListener('click', () => {
        try { navigator.clipboard.writeText(btn.dataset.copy); btn.textContent = 'copied'; } catch {}
      }));
      el.drawer.querySelectorAll('[data-appeal]').forEach(btn => btn.addEventListener('click', () => {
        btn.outerHTML = `<span class="mono" style="color:var(--tx-1)">demo build: no page sent — this would page through your normal paging path, audit-logged as an override.</span>`;
      }));
    } catch (e) {
      el.drawer.innerHTML = `<div class="drawer-head"><span class="mono">decision #${esc(String(id))}</span><button class="drawer-close" aria-label="close">${closeGlyph()}</button></div>
        <p class="drawer-note">Couldn't load this decision (${esc(e.message || 'unreachable')}). The gate is unaffected.</p>`;
      el.drawer.hidden = false;
      el.drawer.querySelector('.drawer-close').addEventListener('click', closeDrawer);
    }
  },
  openPalette(prefill = '') { openPalette(prefill); },
  showKeymap() {
    el.paletteHint.innerHTML = `<b>river keys</b> · j/k move · Enter drawer · Esc close · / filter · Shift+G jump to live · p/s/e/d dispositions · 1/2/3 density · ? this map`;
    el.paletteHint.hidden = false;
    setTimeout(() => { el.paletteHint.hidden = true; }, 6000);
  },
};
function closeDrawer() { el.drawer.hidden = true; el.drawer.innerHTML = ''; }

/* ---------- command palette: every state is a deep link ---------- */
function openPalette(prefill = '') {
  el.palette.hidden = false;
  el.paletteInput.value = prefill;
  el.paletteInput.focus();
  paintPalette('');
  el.paletteInput.oninput = () => paintPalette(el.paletteInput.value);
}
function closePalette() { el.palette.hidden = true; el.paletteInput.value = ''; }
function paintPalette(qtext) {
  const q = qtext.trim();
  const items = [];
  const tok = {};
  q.split(/\s+/).forEach(t => { const m = t.match(/^([a-z_]+)[=:](.+)$/); if (m) tok[m[1]] = m[2]; });
  for (const s of SCREENS) {
    if (!q || s.label.toLowerCase().includes(q.toLowerCase()) || tok.screen === s.id)
      items.push({ label: `${s.code} — ${s.label}`, href: routeHref(s.id, { ...(tok.team ? { team: tok.team } : {}), ...(Data.mode === 'mock' ? { mock: '1' } : {}) }) });
  }
  if (tok.team || tok.fpr || tok.reason || tok.last)
    items.unshift({
      label: `river filtered: ${[tok.team && 'team=' + tok.team, tok.fpr && 'fpr=' + tok.fpr, tok.reason && 'reason=' + tok.reason, tok.last && 'last=' + tok.last].filter(Boolean).join(' ')}`,
      href: routeHref('river', { ...(tok.team ? { team: tok.team } : {}), ...(tok.fpr ? { fpr: tok.fpr } : {}), ...(tok.reason ? { reason: tok.reason } : {}), ...(tok.last ? { last: tok.last } : {}), ...(Data.mode === 'mock' ? { mock: '1' } : {}) }),
    });
  /* build-fixed modes have no data source to toggle (staging is static,
   * production is live) — the toggle would be phantom interactivity (§8.9). */
  if (!Data.modeFixed)
    items.push({ label: `toggle data source (now: ${Data.mode})`, action: () => { Data.setMode(Data.mode === 'live' ? 'mock' : 'live'); location.reload(); } });
  el.paletteList.innerHTML = items.map((it, i) =>
    `<button class="palette-item mono" data-i="${i}">${esc(it.label)}</button>`).join('');
  el.paletteList.querySelectorAll('.palette-item').forEach(b => b.addEventListener('click', () => {
    const it = items[Number(b.dataset.i)];
    closePalette();
    if (it.action) it.action(); else location.hash = it.href;
  }));
}
document.getElementById('palette-btn').addEventListener('click', () => openPalette());
el.paletteInput.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closePalette();
  if (e.key === 'Enter') el.paletteList.querySelector('.palette-item')?.click();
});
el.palette.addEventListener('click', (e) => { if (e.target === el.palette) closePalette(); });
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); el.palette.hidden ? openPalette() : closePalette(); }
  if (e.key === 'Escape' && !el.drawer.hidden) closeDrawer();
});

/* ---------- data honesty banner: the build's condition, stated in-band (P3) ---------- */
function paintMockBanner() {
  /* static showcase (GitHub Pages staging): the SIMULATED banner is part of the
   * surface, not a footnote. Synthetic data, simulated paging, pre-rendered
   * snapshots — nothing here is your system. No "go live" escape: there is no
   * live backend behind a static host. */
  if (Data.dataMode === 'static') {
    el.mockBanner.hidden = false;
    el.mockBanner.innerHTML = `◈ SIMULATED SHOWCASE — synthetic data · simulated paging · snapshots, not a live stream · not your system`;
    return;
  }
  if (Data.mode === 'mock') {
    el.mockBanner.hidden = false;
    el.mockBanner.innerHTML = `◈ MOCK DATA — contract mocks v1.0.0 · illustrative, not your system · <button id="mock-off" class="mono">go live</button>`;
    document.getElementById('mock-off').addEventListener('click', () => { Data.setMode('live'); location.reload(); });
  } else el.mockBanner.hidden = true;
}
Data.onModeChange(paintMockBanner);
paintMockBanner();

/* ---------- router ---------- */
async function route() {
  cleanups.forEach(f => { try { f(); } catch {} });
  cleanups = [];
  closeDrawer(); closePalette();
  const { screen, params } = parseHash();
  /* preserve ?mock=1 across hash navigations */
  if (Data.mode === 'mock' && !params.get('mock')) {
    params.set('mock', '1');
    history.replaceState(null, '', '#/' + screen + '?' + params.toString());
  }
  const view = VIEWS[screen] || VIEWS.river;
  ctx.setSseState('paused');
  el.view.innerHTML = '';
  el.strip.textContent = '';
  try {
    await view(el.view, params, ctx);
  } catch (e) {
    el.view.innerHTML = `<div class="error-block"><div class="error-what">This screen failed to render.</div><div class="error-detail mono">${esc(e.message || String(e))}</div></div>`;
  }
}
window.addEventListener('hashchange', route);

/* demo-contract: land on START for first-time visitors.
 * Production (DATA_MODE='live') with no backend configured: the honest empty
 * state — backend setup screen, never invented data (P3). */
if (window.SENTINEL_DATA_MODE === 'live' && !backendUrlConfigured()) {
  el.nav.innerHTML = '';
  el.srcBadge.textContent = '◈ no backend';
  renderSetup(el.view);
} else {
  if (!location.hash) location.hash = '#/start' + (Data.mode === 'mock' ? '?mock=1' : '');
  route();
}
