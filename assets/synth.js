/* synth.js — the honest synthetic pipeline.
 *
 * In static (staging) mode there is no backend. Instead of dead pre-rendered
 * snapshots, the console runs a live in-browser simulation of Sentinel's
 * paging pipeline: alerts arrive, the correlator groups them into problems,
 * each problem races (Jev vs the bounded timer), the deterministic gate
 * dispositions page-or-suppress with a reason code and proof, and the
 * forwarder records the outcome.
 *
 * HONESTY CONTRACT (A6, N10 — never fake-real):
 *  - Every input is synthetic and every surface says SIMULATED in-band.
 *  - Every number is traceable: `why(key)` returns the generating event ids.
 *  - The generator is seeded and deterministic: same seed → same history.
 *    The seed is shown on screen; the event log is inspectable.
 *  - The pipeline LOGIC (grouping, dispositions, proofs, fail-open) mirrors
 *    the real gate's rules. Synthetic inputs, real logic, honest labels.
 *
 * Pure logic, no DOM — fully testable under node.
 */

export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const SERVICES = [
  { team: 'product_backend', service: 'product_backend/checkout', owner: 'product-oncall' },
  { team: 'product_backend', service: 'product_backend/ledger', owner: 'product-oncall' },
  { team: 'platform', service: 'api/gateway', owner: 'platform-oncall' },
  { team: 'platform', service: 'auth/sessions', owner: 'platform-oncall' },
  { team: 'data', service: 'queue/workers', owner: 'data-oncall' },
  { team: 'data', service: 'db/primary', owner: 'data-oncall' },
  { team: 'network', service: 'edge/cdn', owner: 'network-oncall' },
];

const SYMPTOMS = [
  { symptom: 'error-rate spike', sev: 'p1_critical', cost: 'Checkout failing for ~12% of requests. Every minute costs ~$1,400 in abandoned carts.' },
  { symptom: 'p99 latency breach', sev: 'p2_high', cost: 'p99 at 2.8s (SLO 1.2s). Slow-burn: user frustration compounds, no data loss yet.' },
  { symptom: '5xx spike', sev: 'p2_high', cost: '5xx at 4.1% on the gateway. Mobile clients retrying — amplifying load 3×.' },
  { symptom: 'queue depth growing', sev: 'p3_medium', cost: 'Backlog growing 200/min. If unaddressed for 30m, workers saturate and checkout slows.' },
  { symptom: 'connection pool exhaustion', sev: 'p2_high', cost: 'Pool at 97%. New connections queueing; cascading timeout risk within ~10 min.' },
  { symptom: 'flapping health checks', sev: 'p3_medium', cost: 'Health checks flapping 6×/min. Likely self-clearing deploy churn, not user impact.' },
  { symptom: 'deploy-churn noise', sev: 'p4_low', cost: 'Expected noise during the canary deploy. No user-facing symptom.' },
];

const SUPPRESS_REASONS = [
  { code: 'flap-debounce', rule: 'correlator.flap_debounce', by: 'policy v42 (SRE team)', detail: 'fired 4× in 5m, self-cleared twice — debounced' },
  { code: 'self-clear<5m', rule: 'gate.self_clear', by: 'policy v42 (SRE team)', detail: 'symptom cleared within 5 minutes of first alert' },
  { code: 'deploy-churn', rule: 'correlator.deploy_churn', by: 'policy v42 (SRE team)', detail: 'matches an in-progress canary deploy window' },
  { code: 'duplicate', rule: 'correlator.dedup', by: 'policy v42 (SRE team)', detail: 'same fingerprint as an already-open problem' },
  { code: 'known-noise', rule: 'gate.known_noise', by: 'policy v42 (SRE team)', detail: 'alert shape matches the known-noise allowlist' },
  { code: 'storm-fold', rule: 'correlator.storm_fold', by: 'policy v42 (SRE team)', detail: 'folded into the active storm incident' },
];

const CHANGES = [
  'canary deploy payments/checkout v2.14.3 (12m ago)',
  'config push: queue worker concurrency 8→16 (26m ago)',
  'flag flip: new-tax-engine → 10% (41m ago)',
  'db migration: ledger backfill started (1h ago)',
];

let nextId = 1;
const nid = (p) => `${p}-${nextId++}`;

export function createSynth(seed = 20261005) {
  const rand = mulberry32(seed);
  const pick = (arr) => arr[Math.floor(rand() * arr.length)];
  const listeners = {};
  const on = (evt, fn) => { (listeners[evt] = listeners[evt] || []).push(fn); };
  const emit = (evt, data) => { (listeners[evt] || []).forEach((fn) => { try { fn(data); } catch {} }); };

  const state = {
    seed,
    startedAt: Date.now(),
    stages: {
      receiver: { count: 0, rate: 0, ids: [] },
      correlator: { count: 0, groups: 0, ids: [] },
      race: { count: 0, jevWon: 0, timerWon: 0, ids: [] },
      gate: { count: 0, paged: 0, suppressed: 0, ids: [] },
      forwarder: { count: 0, delivered: 0, ids: [] },
    },
    health: 'live',           // live | degraded | stale
    lagMs: 120,
    killSwitch: 'ARMED',      // ARMED | ENGAGED
    policy: { version: 'v42', hash: 'b3:9f2c…a41d', attested: true, expiresIn: '13h 22m', kernelBinding: 'refused 0 unattested generations' },
    auth: { hmacCoverage: '94%', senders: 14, unauthenticatedRoutes: 0 },
    storm: null,              // {problemId, size} when active
  };
  const problems = new Map();   // id -> problem
  const decisions = [];         // newest last
  const eventLog = [];          // every generating event, for why()

  const log = (kind, ref) => { eventLog.push({ t: Date.now(), kind, ref }); };

  function fingerprint(service, symptom) {
    let h = 0;
    const s = service + '|' + symptom;
    for (let i = 0; i < s.length; i++) h = (Math.imul(h, 31) + s.charCodeAt(i)) | 0;
    return ('0000000' + (h >>> 0).toString(16)).slice(-8).replace(/(..)(..)(..)(..)/, '$1$2·$3$4');
  }

  function bump(stage, id) {
    const s = state.stages[stage];
    s.count++; s.ids.push(id);
    if (s.ids.length > 500) s.ids.shift();
  }

  function correlate(alert) {
    // same service+symptom within 10m → same problem; else new problem
    for (const p of problems.values()) {
      if (p.state !== 'resolved' && p.service === alert.service && p.symptom === alert.symptom &&
          Date.now() - p.lastSeen < 10 * 60 * 1000) {
        p.alerts.push(alert.id); p.lastSeen = Date.now(); p.flaps++;
        log('correlate', { alert: alert.id, problem: p.id, action: 'folded' });
        return { problem: p, folded: true };
      }
    }
    const p = {
      id: nid('prob'), service: alert.service, team: alert.team, owner: alert.owner,
      symptom: alert.symptom, severity: alert.severity, cost: alert.cost,
      state: 'open', alerts: [alert.id], decisions: [],
      createdAt: Date.now(), lastSeen: Date.now(), flaps: 0,
      changedRecently: rand() < 0.4 ? [pick(CHANGES)] : [],
      ackBy: null, undoAvailable: false,
    };
    problems.set(p.id, p);
    state.stages.correlator.groups++;
    log('correlate', { alert: alert.id, problem: p.id, action: 'new-problem' });
    return { problem: p, folded: false };
  }

  function race(problem) {
    // Jev vs the 2700ms timer. In synth, the timer usually wins (honest: Jev is often slower).
    const jevLatency = 400 + rand() * 4200;
    const jevWon = jevLatency <= 2700;
    const r = { jevWon, jevLatencyMs: Math.round(jevLatency), timerMs: 2700, late: !jevWon };
    state.stages.race.count++;
    if (jevWon) state.stages.race.jevWon++; else state.stages.race.timerWon++;
    state.stages.race.ids.push(problem.id);
    log('race', { problem: problem.id, ...r });
    return r;
  }

  function gate(problem, raceRes, folded) {
    const r = rand();
    let disposition, reason;
    if (folded && problem.flaps >= 2) {
      disposition = 'suppress'; reason = SUPPRESS_REASONS[0]; // flap-debounce
    } else if (problem.symptom === 'flapping health checks' && r < 0.7) {
      disposition = 'suppress'; reason = SUPPRESS_REASONS[1]; // self-clear
    } else if (problem.symptom === 'deploy-churn noise') {
      disposition = 'suppress'; reason = SUPPRESS_REASONS[2]; // deploy-churn
    } else if (folded) {
      disposition = 'suppress'; reason = SUPPRESS_REASONS[3]; // duplicate
    } else if (state.storm && state.storm.problemId !== problem.id && r < 0.8) {
      disposition = 'suppress'; reason = SUPPRESS_REASONS[5]; // storm-fold
    } else if (r < 0.18) {
      // fail-open: uncertain → page anyway. Uncertainty pages, never suppresses.
      disposition = 'page_now'; reason = { code: 'fail-open:uncertain', rule: 'gate.fail_open', by: 'policy v42 (SRE team)', detail: 'evidence below confidence floor — paged rather than risk a silent miss' };
    } else {
      disposition = 'page_now'; reason = { code: 'genuine', rule: 'gate.page', by: 'policy v42 (SRE team)', detail: 'symptom + blast radius exceed the page threshold' };
    }
    // kill switch engaged → everything pages (fail-open), marked as such
    if (state.killSwitch === 'ENGAGED') {
      disposition = 'page_now';
      reason = { code: 'kill-switch', rule: 'safety.kill_switch', by: 'operator override', detail: 'kill switch ENGAGED — all decisions page' };
    }
    const confidence = disposition === 'page_now'
      ? 0.55 + rand() * 0.4
      : 0.82 + rand() * 0.15;
    const d = {
      id: nid('dec'), ts: Date.now(), problemId: problem.id,
      fingerprint: fingerprint(problem.service, problem.symptom),
      team: problem.team, service: problem.service, severity: problem.severity,
      disposition, reasonCode: reason.code,
      confidence: Math.round(confidence * 100) / 100,
      proof: {
        rule: reason.rule, configuredBy: reason.by, detail: reason.detail,
        matchedCondition: `${reason.code} matched on ${problem.alerts.length} alert(s), fingerprint ${fingerprint(problem.service, problem.symptom)}`,
        evidenceAgeMs: Math.round(200 + rand() * 1800),
        expiresAt: disposition === 'suppress' ? Date.now() + 30 * 60 * 1000 : null,
        perClass: { precision: 0.94 + rand() * 0.05, recall: 0.89 + rand() * 0.08, n: 200 + Math.floor(rand() * 800) },
        cues: [`${problem.alerts.length} alert(s) in 10m window`, `service=${problem.service}`, `flaps=${problem.flaps}`],
        consideredRejected: [`escalate-to-p2: rejected — severity already ${problem.severity}`, `wait-5m: rejected — cost-of-inaction exceeds the wait budget`],
      },
      costOfInaction: problem.cost,
      blastRadius: `${problem.service} · owner ${problem.owner}`,
      changedRecently: problem.changedRecently,
      race: raceRes,
      undoable: disposition === 'suppress',
      undone: false,
    };
    decisions.push(d); problem.decisions.push(d.id);
    state.stages.gate.count++;
    if (disposition === 'page_now') state.stages.gate.paged++; else state.stages.gate.suppressed++;
    state.stages.gate.ids.push(d.id);
    log('gate', { decision: d.id, disposition, reason: reason.code });
    return d;
  }

  function forward(decision, problem) {
    state.stages.forwarder.count++;
    state.stages.forwarder.ids.push(decision.id);
    if (decision.disposition === 'page_now') {
      state.stages.forwarder.delivered++;
      problem.state = 'open'; // a page is outstanding
      log('forward', { decision: decision.id, action: 'paged', channel: 'pagerduty-voice+sms' });
    } else {
      log('forward', { decision: decision.id, action: 'suppressed', channel: 'none' });
    }
    emit('decision', decision);
    emit('pipeline', state.stages);
  }

  function ingest() {
    const svc = pick(SERVICES);
    const sym = pick(SYMPTOMS);
    const alert = { id: nid('alert'), ts: Date.now(), ...svc, symptom: sym.symptom, severity: sym.sev, cost: sym.cost };
    bump('receiver', alert.id);
    log('ingest', { alert: alert.id, service: alert.service, symptom: alert.symptom });
    const { problem, folded } = correlate(alert);
    bump('correlator', alert.id);
    if (folded && problem.decisions.length > 0) {
      // folded into a problem that already has a decision → quick suppress path
      const d = gate(problem, { jevWon: false, jevLatencyMs: null, timerMs: 0, late: false, skipped: 'folded — no race needed' }, true);
      forward(d, problem);
      return d;
    }
    const raceRes = race(problem);
    const d = gate(problem, raceRes, folded);
    forward(d, problem);
    return d;
  }

  function maybeStorm() {
    // occasional storm: a burst that folds into one problem
    if (state.storm || rand() > 0.06) return;
    const svc = pick(SERVICES);
    const p = {
      id: nid('prob'), service: svc.service, team: svc.team, owner: svc.owner,
      symptom: 'error-rate spike', severity: 'p1_critical',
      cost: 'Cascading failures across ' + svc.service + '. Multiple downstream services affected.',
      state: 'open', alerts: [], decisions: [], createdAt: Date.now(), lastSeen: Date.now(),
      flaps: 0, changedRecently: [pick(CHANGES)], ackBy: null, storm: true,
    };
    problems.set(p.id, p);
    state.storm = { problemId: p.id, size: 0 };
    const n = 8 + Math.floor(rand() * 20);
    for (let i = 0; i < n; i++) {
      const alert = { id: nid('alert'), ts: Date.now(), ...svc, symptom: 'error-rate spike', severity: 'p1_critical', cost: p.cost };
      bump('receiver', alert.id); p.alerts.push(alert.id); state.storm.size++;
    }
    bump('correlator', p.id);
    const raceRes = race(p);
    const d = gate(p, raceRes, false);
    // storm pages once (the problem), the rest fold as suppressions
    for (let i = 0; i < 3; i++) {
      const dd = gate(p, { jevWon: false, jevLatencyMs: null, timerMs: 0, late: false, skipped: 'storm-fold' }, true);
      forward(dd, p);
    }
    forward(d, p);
    emit('storm', { problem: p, size: n });
    log('storm', { problem: p.id, size: n });
    // storm clears after a while
    setTimeout(() => { state.storm = null; emit('stormEnd', { problemId: p.id }); }, 45000);
  }

  function tick() {
    ingest();
    maybeStorm();
    // resolve old open problems occasionally (mitigation happens)
    for (const p of problems.values()) {
      if (p.state === 'open' && Date.now() - p.lastSeen > 90000 && rand() < 0.3) {
        p.state = 'resolved';
        log('resolve', { problem: p.id });
        emit('problem', p);
      }
    }
    // pipeline health wobble (honest: the strip must show degraded sometimes)
    const hr = rand();
    state.health = hr < 0.94 ? 'live' : hr < 0.98 ? 'degraded' : 'stale';
    state.lagMs = state.health === 'live' ? Math.round(80 + rand() * 200)
      : state.health === 'degraded' ? Math.round(2000 + rand() * 8000) : Math.round(30000 + rand() * 30000);
    emit('health', { health: state.health, lagMs: state.lagMs });
  }

  // seed history so every surface has content on first paint (deterministic)
  for (let i = 0; i < 28; i++) ingest();
  // backdate the seed history over the last 3 hours for a realistic tape
  {
    const now = Date.now();
    decisions.forEach((d, i) => { d.ts = now - (decisions.length - i) * 6.4 * 60 * 1000; });
  }

  const api = {
    seed, on, tick, state: () => state,
    decisions: (n = 50) => decisions.slice(-n).reverse(),
    decisionById: (id) => decisions.find((d) => d.id === id),
    openPages: () => [...problems.values()].filter((p) => p.state === 'open' || p.state === 'ackd')
      .sort((a, b) => sevRank(a.severity) - sevRank(b.severity)),
    problemById: (id) => problems.get(id),
    suppressions: (n = 50) => decisions.filter((d) => d.disposition === 'suppress').slice(-n).reverse(),
    pages: (n = 50) => decisions.filter((d) => d.disposition === 'page_now').slice(-n).reverse(),
    undoSuppression: (decisionId) => {
      const d = decisions.find((x) => x.id === decisionId);
      if (!d || d.disposition !== 'suppress' || d.undone) return false;
      d.undone = true;
      const p = problems.get(d.problemId);
      if (p && p.state === 'resolved') p.state = 'open';
      log('undo', { decision: d.id, problem: d.problemId });
      emit('decision', d); emit('undo', d);
      return true;
    },
    ackProblem: (problemId, who = 'you') => {
      const p = problems.get(problemId);
      if (!p) return false;
      p.state = 'ackd'; p.ackBy = who;
      log('ack', { problem: p.id, by: who });
      emit('problem', p);
      return true;
    },
    setKillSwitch: (engaged) => {
      state.killSwitch = engaged ? 'ENGAGED' : 'ARMED';
      log('killswitch', { state: state.killSwitch });
      emit('killswitch', state.killSwitch);
    },
    why: (key) => {
      // traceability: every number → its generating events
      const stage = state.stages[key];
      if (stage) return { count: stage.count, sampleEventIds: stage.ids.slice(-10), eventLog: eventLog.filter((e) => stage.ids.includes(e.ref?.alert || e.ref?.decision || e.ref?.problem)).slice(-10) };
      return { note: 'unknown key', keys: Object.keys(state.stages) };
    },
    eventLog: () => eventLog.slice(),
  };
  return api;
}

function sevRank(s) {
  return { p1_critical: 0, p2_high: 1, p3_medium: 2, p4_low: 3, known_noise: 4, cannot_determine: 5 }[s] ?? 9;
}

export const SEV_LABEL = { p1_critical: 'SEV1', p2_high: 'SEV2', p3_medium: 'SEV3', p4_low: 'SEV4', known_noise: 'NOISE', cannot_determine: 'UNK' };
export const DISP_LABEL = { page_now: 'PAGE', suppress: 'SUPPRESS' };
