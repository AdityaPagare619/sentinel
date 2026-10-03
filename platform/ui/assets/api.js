/* api.js — the data layer. Fetch() against the FROZEN contract (platform/contracts/openapi.yaml v1.0.0).
 * DATA_SOURCE switch: live API base URL ↔ mock files. Default: live.
 * Mocks render ONLY behind the visible MOCK DATA banner, never silently.
 * In mock mode, POST /api/simulate runs a LOCAL recompute over the mock
 * window (lib.applyThresholds) — labeled "mock-mode local recompute" wherever shown.
 */
import { applyThresholds, DEFAULT_THRESHOLDS, CONTRACT_VERSION } from './lib.js';

const base = new URL('.', import.meta.url); /* platform/ui/ — relative, safe under any mount path */
const mockUrl = (f) => new URL('data/' + f, base).toString();

function readModePreference() {
  try {
    const hq = new URLSearchParams((location.hash.split('?')[1] || ''));
    if (hq.get('mock') === '1') return 'mock';
    if (new URLSearchParams(location.search).get('mock') === '1') return 'mock';
    return localStorage.getItem('sentinel.ui.mode') || 'live';
  } catch { return 'live'; }
}

export const Data = {
  mode: readModePreference(),
  apiBase: '', /* same origin; the API lane serves platform/ui statically at / */
  datasetVersion: 'ds:shadow-2026-10-02',
  lastMeta: null,
  listeners: new Set(),

  setMode(m) {
    this.mode = m;
    try { localStorage.setItem('sentinel.ui.mode', m); } catch {}
    for (const f of this.listeners) f(m);
  },
  onModeChange(f) { this.listeners.add(f); },

  /* ---------- live fetch with timeout ---------- */
  async _live(path, opts = {}) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 9000);
    let res;
    try {
      res = await fetch(this.apiBase + path, { ...opts, signal: ctrl.signal });
    } catch (e) {
      clearTimeout(t);
      throw { status: 0, code: 'unreachable', message: `Couldn't reach the API (${(opts.method || 'GET')} ${path}). The gate itself is unaffected — paging behavior doesn't depend on this screen.` };
    } finally { clearTimeout(t); }
    if (!res.ok) {
      let body = null;
      try { body = await res.json(); } catch {}
      const err = body?.error || {};
      throw { status: res.status, code: err.code || 'http_' + res.status, message: err.message || `API ${res.status} on ${path}`, retryable: err.retryable ?? res.status >= 500 };
    }
    return res.json();
  },

  /* ---------- mock fetch (same envelope shape, straight from contract mocks) ---------- */
  async _mock(file) {
    const res = await fetch(mockUrl(file));
    if (!res.ok) throw { status: res.status, code: 'mock_missing', message: `Mock file ${file} missing.` };
    return res.json();
  },
  _touchMeta(env) {
    this.lastMeta = env?.meta || null;
    return env;
  },

  /* ---------- contract endpoints ---------- */
  async getDecisions(params = {}) {
    if (this.mode === 'mock') {
      const env = await this._mock('decisions.json');
      let rows = env.data.slice();
      const q = params;
      if (q.team) { const ts = q.team.split(','); rows = rows.filter(r => ts.includes(r.team)); }
      if (q.action) rows = rows.filter(r => r.disposition === q.action);
      if (q.fingerprint) rows = rows.filter(r => r.fingerprint.startsWith(q.fingerprint.replace(/^fpr:/, '')));
      if (q.reason) rows = rows.filter(r => r.reason === q.reason);
      if (q.sev) { const sv = { 1: 'p1_critical', 2: 'p2_high', 3: 'p3_medium', 4: 'p4_low' }; rows = rows.filter(r => r.severity === sv[q.sev]); }
      if (q.from) rows = rows.filter(r => r.time >= q.from);
      if (q.to) rows = rows.filter(r => r.time <= q.to);
      if (q.since_id) rows = rows.filter(r => r.id > Number(q.since_id));
      if (q.q) { const needle = q.q.toLowerCase(); rows = rows.filter(r => (r.service + ' ' + r.title + ' ' + r.fingerprint + ' ' + r.reason).toLowerCase().includes(needle)); }
      rows.sort((a, b) => b.id - a.id);
      const limit = Math.min(Math.max(Number(q.limit) || 50, 1), 500);
      const page = rows.slice(0, limit);
      return this._touchMeta({ data: page, meta: { ...env.meta, pagination: { limit, next_since_id: page.length ? page[page.length - 1].id : null, has_more: rows.length > limit } } });
    }
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v != null && v !== '') qs.set(k, v);
    return this._touchMeta(await this._live('/api/decisions?' + qs.toString()));
  },

  async getDecision(id) {
    if (this.mode === 'mock') {
      if (Number(id) === 1042) return this._touchMeta(await this._mock('decision-detail.json'));
      /* detail for other mock rows: derived from the summary row (alert parsed from title), outcome unlabeled */
      const env = await this._mock('decisions.json');
      const row = env.data.find(r => r.id === Number(id));
      if (!row) throw { status: 404, code: 'not_found', message: `No decision #${id} in the mock window.` };
      const m = (row.title || '').match(/^(\S+)\s+\S+\s+on\s+(\S+)\s+\(([^)]+)\)/);
      return this._touchMeta({
        data: {
          ...row,
          alert: m ? { check: m[1], severity_in: 'unknown', region: m[3], labels: {}, metric_value: null, metric_threshold: null, breach_seconds: null } : null,
          outcome: null,
          audit: { received_at: row.time, created_at: row.time },
        },
        meta: env.meta,
      });
    }
    return this._touchMeta(await this._live('/api/decision/' + encodeURIComponent(id)));
  },

  async getCalibration(team) {
    if (this.mode === 'mock') {
      const env = await this._mock('calibration.json');
      if (team && team !== 'all' && team !== env.data.team)
        throw { status: 404, code: 'no_shadow_data', team, message: `No shadow evaluations for team=${team} yet. Calibration needs the nightly shadow join — check back after 02:00 IST, or run a guided storm.` };
      this.datasetVersion = 'ds:' + env.data.dataset_version;
      return this._touchMeta(env);
    }
    const qs = team && team !== 'all' ? '?team=' + encodeURIComponent(team) : '';
    const env = await this._live('/api/calibration' + qs);
    this.datasetVersion = 'ds:' + (env.data.dataset_version || 'unknown');
    return this._touchMeta(env);
  },

  async simulate(body) {
    if (this.mode === 'mock') {
      const env = await this._mock('decisions.json');
      const proj = applyThresholds(env.data, body.thresholds, body.cost_model);
      const sim = await this._mock('simulate.json');
      this.datasetVersion = 'ds:' + sim.data.provenance.dataset_version;
      return this._touchMeta({
        data: {
          projection: { ...proj, pages_per_night_avoided: null },
          provenance: { ...sim.data.provenance, tuner_rev: sim.data.provenance.tuner_rev + ' (mock-mode local recompute — NOT tuner math)' },
        },
        meta: { ...env.meta, data_source: 'synthetic' },
      });
    }
    const env = await this._live('/api/simulate', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    this.datasetVersion = 'ds:' + env.data.provenance.dataset_version;
    return this._touchMeta(env);
  },

  async getNoise(window = '24h') {
    if (this.mode === 'mock') return this._touchMeta(await this._mock('noise.json'));
    return this._touchMeta(await this._live('/api/analytics/noise?window=' + encodeURIComponent(window)));
  },
  async getFlips(window = '7d') {
    if (this.mode === 'mock') return this._touchMeta(await this._mock('flips.json'));
    return this._touchMeta(await this._live('/api/analytics/flips?window=' + encodeURIComponent(window)));
  },
};

/* ---------- SSE stream with gap detection (narrative §3.4, §6.1) ---------- */
export class Stream {
  constructor() {
    this.state = 'paused'; /* live | paused | reconnecting | polling */
    this.attempt = 0;
    this.lastId = 0;
    this.lastEventAt = 0;
    this.handlers = { decision: new Set(), gap: new Set(), state: new Set() };
    this._es = null; this._hb = null; this._poll = null; this._mockTimers = [];
  }
  on(evt, f) { this.handlers[evt].add(f); return () => this.handlers[evt].delete(f); }
  _emit(evt, arg) { for (const f of this.handlers[evt]) f(arg); }
  _setState(s) { this.state = s; this._emit('state', s); }

  start(sinceId = 0) {
    this.lastId = sinceId || this.lastId;
    if (Data.mode === 'mock') return this._startMock();
    this._setState('reconnecting'); this.attempt++;
    this._es = new EventSource(Data.apiBase + '/api/stream' + (this.lastId ? '?since_id=' + this.lastId : ''));
    this._es.addEventListener('decision', (e) => {
      this.lastEventAt = Date.now(); this.attempt = 0;
      try { const d = JSON.parse(e.data); if (d.id) this.lastId = d.id; this._emit('decision', d); } catch {}
      this._setState('live'); this._armHeartbeat();
    });
    this._es.addEventListener('gap', (e) => {
      let g = {}; try { g = JSON.parse(e.data); } catch {}
      this._emit('gap', g);
      this._backfill();
    });
    this._es.onerror = () => {
      this._es.close(); this._setState('reconnecting'); this.attempt++;
      this._emit('gap', { missed: true, reason: 'SSE connection lost', quietMs: Date.now() - (this.lastEventAt || Date.now()) });
      this._pollFallback();
      setTimeout(() => this._es && this.start(this.lastId), 5000);
    };
    this._armHeartbeat();
  }
  _armHeartbeat() {
    clearTimeout(this._hb);
    this._hb = setTimeout(() => {
      /* no event or heartbeat in 30s → gap marker + polling fallback, never silent */
      this._emit('gap', { missed: true, reason: 'SSE quiet > 30s', quietMs: Date.now() - (this.lastEventAt || Date.now()) });
      this._pollFallback();
    }, 30000);
  }
  async _backfill() {
    try {
      const env = await Data.getDecisions({ since_id: this.lastId, limit: 500 });
      for (const d of [...env.data].reverse()) this._emit('decision', d);
      if (env.data.length) this.lastId = Math.max(this.lastId, ...env.data.map(d => d.id));
      this._setState('live'); this._armHeartbeat();
    } catch { this._pollFallback(); }
  }
  _pollFallback() {
    this._setState('polling');
    clearInterval(this._poll);
    this._poll = setInterval(() => this._backfill(), 30000);
  }
  stop() {
    this._es?.close(); clearTimeout(this._hb); clearInterval(this._poll);
    this._mockTimers.forEach(clearTimeout); this._mockTimers = [];
    this._setState('paused');
  }

  /* mock mode: replay the recorded stream-events.jsonl on a timer — labeled, not live */
  async _startMock() {
    this._setState('live');
    let lines;
    try {
      const res = await fetch(mockUrl('stream-events.jsonl'));
      lines = (await res.text()).trim().split('\n');
    } catch { this._setState('paused'); return; }
    let delay = 800;
    for (const ln of lines) {
      const t = setTimeout(() => {
        try {
          const ev = JSON.parse(ln);
          if (ev.event === 'decision') { this.lastId = ev.id; this._emit('decision', ev.data); }
          else if (ev.event === 'gap') this._emit('gap', ev.data);
        } catch {}
      }, delay);
      this._mockTimers.push(t);
      delay += 2600;
    }
    const end = setTimeout(() => this._setState('paused'), delay + 500);
    this._mockTimers.push(end);
  }
}
