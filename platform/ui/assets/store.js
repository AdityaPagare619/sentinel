/* store.js — one data model for the console.
 *
 * Static (staging) mode: the store wraps the honest synthetic pipeline
 * (synth.js). Live mode: it wraps the real backend API (api.js Data/SSE).
 * Views never touch Data or synth directly — they talk to the store, so the
 * same screens render in both modes with the mode banner as the only
 * difference. This is what makes "simulated but alive" honest: identical
 * information architecture, labeled inputs.
 */
import { createSynth } from './synth.js';
import { Data } from './api.js';

const DATA_MODE = (typeof window !== 'undefined' && window.SENTINEL_DATA_MODE) || 'static';
export const isSimulated = DATA_MODE !== 'live';

let synth = null;
const listeners = {};
const on = (evt, fn) => { (listeners[evt] = listeners[evt] || []).push(fn); };
const emit = (evt, data) => { (listeners[evt] || []).forEach((fn) => { try { fn(data); } catch {} }); };

if (isSimulated) {
  const params = new URLSearchParams(typeof location !== 'undefined' ? location.search : '');
  const seed = parseInt(params.get('seed') || '20261005', 10) || 20261005;
  synth = createSynth(seed);
  // the pipeline runs: a heartbeat every 2.5s keeps every surface alive
  const beat = () => { synth.tick(); };
  const timer = setInterval(beat, 2500);
  if (timer.unref) timer.unref();
  for (const e of ['decision', 'pipeline', 'health', 'storm', 'stormEnd', 'problem', 'undo', 'killswitch'])
    synth.on(e, (d) => emit(e, d));
  // expose for the "why this number" affordance and for tests
  if (typeof window !== 'undefined') window.__synth = synth;
}

export const Store = {
  isSimulated,
  dataMode: DATA_MODE,
  seed: synth ? synth.seed : null,

  /* ---- reads (identical shape in both modes) ---- */
  pipeline() {
    if (synth) {
      const s = synth.state();
      return {
        stages: [
          { key: 'receiver', label: 'RECEIVER', count: s.stages.receiver.count },
          { key: 'correlator', label: 'CORRELATOR', count: s.stages.correlator.count, note: `${s.stages.correlator.groups} problems` },
          { key: 'race', label: 'RACE', count: s.stages.race.count, note: `jev ${s.stages.race.jevWon} · timer ${s.stages.race.timerWon}` },
          { key: 'gate', label: 'GATE', count: s.stages.gate.count, note: `${s.stages.gate.paged} page · ${s.stages.gate.suppressed} supp` },
          { key: 'forwarder', label: 'FORWARDER', count: s.stages.forwarder.count, note: `${s.stages.forwarder.delivered} delivered` },
        ],
        health: s.health, lagMs: s.lagMs, killSwitch: s.killSwitch,
        storm: s.storm,
      };
    }
    // live mode: pipeline strip degrades to what the read API exposes
    return { stages: [], health: 'live', lagMs: null, killSwitch: 'UNKNOWN', storm: null, live: true };
  },
  decisions(n = 50) { return synth ? synth.decisions(n) : []; },
  async decisionsLive(n = 50) {
    if (synth) return synth.decisions(n);
    try { const env = await Data.getDecisions({ limit: n }); return env.data || []; } catch { return []; }
  },
  openPages() { return synth ? synth.openPages() : []; },
  suppressions(n = 50) { return synth ? synth.suppressions(n) : []; },
  problemById(id) { return synth ? synth.problemById(id) : null; },
  decisionById(id) { return synth ? synth.decisionById(id) : null; },
  policy() { return synth ? synth.state().policy : null; },
  auth() { return synth ? synth.state().auth : null; },
  why(key) { return synth ? synth.why(key) : { note: 'live mode: trace via the audit log' }; },

  /* ---- writes (synth: real state changes; live: honest "not wired" states) ---- */
  ackProblem(id, who) { return synth ? synth.ackProblem(id, who) : false; },
  undoSuppression(id) { return synth ? synth.undoSuppression(id) : false; },
  setKillSwitch(engaged) { if (synth) synth.setKillSwitch(engaged); },

  on,
};

/* live-mode SSE bridge: re-emit backend stream events through the store */
if (!isSimulated && typeof window !== 'undefined') {
  import('./api.js').then(({ Stream }) => {
    try {
      const s = new Stream();
      s.on('decision', (e) => emit('decision', e));
      s.on('state', (st) => emit('health', { health: st === 'live' ? 'live' : 'degraded', lagMs: null }));
      s.start();
    } catch {}
  });
}
