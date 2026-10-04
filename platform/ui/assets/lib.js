/* lib.js — pure functions. No DOM. Unit-tested by tests/lib.test.mjs.
 * All display mappings are 1:1 with platform/contracts/openapi.yaml enums. */

export const CONTRACT_VERSION = '1.0.0';

/* ---------- contract enum → operator display (contract is the authority) ---------- */
export const SEV_LABEL = {
  p1_critical: 'SEV1', p2_high: 'SEV2', p3_medium: 'SEV3', p4_low: 'SEV4',
  known_noise: 'NOISE', cannot_determine: 'SEV-?',
};
export const SEV_COLOR = {
  p1_critical: 'var(--sev-1)', p2_high: 'var(--sev-2)', p3_medium: 'var(--sev-3)',
  p4_low: 'var(--sev-4)', known_noise: 'var(--tx-2)', cannot_determine: 'var(--tx-2)',
};
/* narrative §7.1: red renders on the disposition PAGE, present or intended-but-failed. */
export const DISP_LABEL = {
  page_now: 'PAGE', page_business_hours: 'PAGE·BIZ-HRS',
  suppress: 'SUPPRESS', passthrough: 'PASS-THRU',
};
export const DISP_COLOR = {
  page_now: 'var(--disp-page)', page_business_hours: 'var(--disp-escalate)',
  suppress: 'var(--disp-suppress)', passthrough: 'var(--disp-defer)',
};
export const SRC_LABEL = { synthetic: 'synthetic', shadow: 'shadow', production: 'production' };

/* ---------- formatting ---------- */
export function fmtInt(n) {
  return n == null ? '—' : Math.round(n).toLocaleString('en-US');
}
export function fmtPct(x, digits = 1) {
  return x == null ? '—' : (x * 100).toFixed(digits) + '%';
}
export function fmtConf(x, digits = 2) {
  return x == null ? '—' : x.toFixed(digits);
}
export function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toISOString().slice(11, 19);
}
export function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toISOString().slice(0, 16).replace('T', ' ');
}
/* A5 (binding): UTC + local on EVERY event display, in-band, no exceptions.
 * Compact time-only form for row titles; the drawer timeline uses the dated
 * form via dualZone in payload.js. Local zone is the operator's own. */
export function tzAbbr() {
  try {
    const p = new Intl.DateTimeFormat('en', { timeZoneName: 'short' }).formatToParts(new Date());
    const t = p.find(x => x.type === 'timeZoneName');
    return t ? t.value : 'local';
  } catch { return 'local'; }
}
export function fmtTimeBoth(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const utc = d.toISOString().slice(11, 19) + ' UTC';
  let local;
  try {
    local = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) + ' ' + tzAbbr();
  } catch { local = 'local time unavailable'; }
  return `${utc} · ${local}`;
}
/* A4 live-tail rule state machine (pure; the view owns the DOM).
 * Returns {rate, recent, tooFast}: tooFast latches when the 5s rolling rate
 * exceeds maxPerSec and releases below half of it (hysteresis — no flapping
 * at the boundary). The threshold is a Type 2 starting point (binding). */
export const LIVE_TAIL_MAX_PER_SEC = 20;
export function tailState(evtTimes, nowMs, tooFast, maxPerSec = LIVE_TAIL_MAX_PER_SEC) {
  const recent = (evtTimes || []).filter(t => nowMs - t < 5000);
  const rate = recent.length / 5;
  if (!tooFast && rate > maxPerSec) tooFast = true;
  else if (tooFast && rate < maxPerSec * 0.5) tooFast = false;
  return { rate, recent, tooFast };
}
export function ageStr(iso, nowMs = Date.now()) {
  if (!iso) return '—';
  const s = Math.max(0, Math.floor((nowMs - new Date(iso).getTime()) / 1000));
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm ago';
  return Math.floor(s / 86400) + 'd ago';
}
export function shortFpr(fpr) { return fpr ? 'fpr:' + fpr.slice(0, 4) + '·' + fpr.slice(4, 8) : '—'; }
export function shortHash(h, n = 8) { return h ? 'sha256:' + h.slice(0, n) + '…' : '—'; }

/* ---------- query parsing (shared grammar: river filters, palette tokens, deep links) ---------- */
export function parseHash() {
  const h = (typeof location !== 'undefined' ? location.hash : '') || '';
  const m = h.match(/^#\/([a-z]+)(\?(.*))?$/);
  if (!m) return { screen: 'river', params: new URLSearchParams() };
  return { screen: m[1], params: new URLSearchParams(m[3] || '') };
}
export function routeHref(screen, params) {
  const q = params instanceof URLSearchParams ? params.toString() : new URLSearchParams(params || {}).toString();
  return '#/' + screen + (q ? '?' + q : '');
}
export const SCREENS = [
  { code: 'RIVER', id: 'river', label: 'Decision river' },
  { code: 'CAL', id: 'calibration', label: 'Calibration' },
  { code: 'SIM', id: 'simulator', label: 'Threshold simulator' },
  { code: 'AUDIT', id: 'audit', label: 'Audit explorer' },
  { code: 'START', id: 'start', label: 'Onboarding' },
];

/* ---------- Standing Verdict Strip grammar (narrative §5). Machine-generated, fixed slots. ---------- */
export function stripRiver({ nPages, nSuppress, windowLabel, newestIso, apiDown, apiMsg, gap }) {
  if (apiDown) return `Decisions API unreachable (${apiMsg}). Showing the tape as of ${fmtTime(newestIso)}. The gate is unaffected — paging behavior does not depend on this screen.`;
  if (gap) return `Tape gap ${gap} — the missing time is marked below, never skipped.`;
  if (nPages > 0) return `${nPages} page${nPages === 1 ? '' : 's'} in the last ${windowLabel}. ${nSuppress} suppressions, all reason-coded.`;
  return `Nothing on fire. ${nSuppress} suppressions in the last ${windowLabel}, all reason-coded. Gate armed.`;
}
export function stripCal({ ece, n, thin, stale }) {
  if (thin) return `n=${fmtInt(n)} — calibration provisional. Do not tune on this.`;
  if (stale) return `Calibration as of ${stale}: cannot refresh — treat tonight's confidences as uncalibrated until this updates.`;
  return `Calibration healthy as of the nightly join: ECE ${fmtConf(ece, 3)} (n=${fmtInt(n)}).`;
}
export function stripSim({ pageNow, thin, n }) {
  if (thin) return `Cannot project — ${fmtInt(n)} cases is below 100. This refusal is the feature, not a bug.`;
  return `At these thresholds you would have been woken ${fmtInt(pageNow)} times in the 7d window.`;
}
export function stripAudit({ queryDesc, n, fpr, reason }) {
  if (fpr && reason != null) return `${fpr} — suppressed ${n} times in 7d, always ${reason}. Never paged.`;
  return queryDesc ? `${queryDesc} — ${n} decisions.` : `${n} decisions in scope.`;
}
export function stripStart({ mode }) {
  if (mode === 'recorded') return 'This is a recording of a real storm from 2026-10-01 — the suppressions are real, they just are not yours.';
  return 'Shadow mode — read-only tap. Your paging is unchanged. Nothing here can suppress a page.';
}

/* ---------- calibration glosses (generated from the actual bins, never canned) ---------- */
export function binForConf(bins, conf) {
  return bins.find(b => conf >= b.predicted_lo && conf < b.predicted_hi) || bins[bins.length - 1];
}
export function gloss80(bins) {
  const b = binForConf(bins, 0.85);
  if (!b || b.n < 30) return 'Too few cases near 0.80–0.90 to say anything honest.';
  const lo = Math.round(b.ci95_lo * 100), hi = Math.round(b.ci95_hi * 100);
  return `When Sentinel says 80% confident, the outcome matched about ${lo}–${hi}% of the time.`;
}
export function overconfidentBins(bins, tol = 0.05) {
  return bins.filter(b => ((b.predicted_lo + b.predicted_hi) / 2) - b.observed_rate > tol && b.n >= 30);
}
export function calVerdict({ team, ece, bins }) {
  const bad = overconfidentBins(bins);
  const ok = ece < 0.05;
  const s = bad.length === 1 ? 'bin is' : 'bins are';
  return ok
    ? `Calibration is healthy for team=${team} (ECE ${ece.toFixed(3)} < 0.05 gate). ${bad.length} ${s} overconfident — see the diagram.`
    : `Calibration is DEGRADED for team=${team} (ECE ${ece.toFixed(3)} ≥ 0.05 gate). ${bad.length} ${s} overconfident — do not trust suppressions until this clears.`;
}

/* ---------- confidence-bar statistics (design system §3.5) ---------- */
export function quartilesFromBins(bins) {
  /* bins: [{predicted_lo, predicted_hi, n}] — the team's confidence values, labeled eval set */
  const total = bins.reduce((a, b) => a + b.n, 0);
  if (!total) return { q1: 0.25, q2: 0.5, q3: 0.75, n: 0 };
  const at = (p) => {
    const target = total * p;
    let acc = 0;
    for (const b of bins) {
      acc += b.n;
      if (acc >= target) return (b.predicted_lo + b.predicted_hi) / 2;
    }
    return 0.95;
  };
  return { q1: at(0.25), q2: at(0.5), q3: at(0.75), n: total };
}

/* ---------- counterfactual receipt (narrative §4).
 * Derived arithmetically from the decision's stored confidence and the team's
 * CURRENT ThresholdSet — labeled as derived, never as an event field. */
export function receiptLine(decision, thresholds) {
  if (decision.disposition !== 'suppress') return null;
  const conf = decision.confidence;
  const floor = thresholds.suppress_conf_min;
  const p1 = decision.prob_map?.severity?.probs?.p1_critical ?? null;
  const margin = conf - floor;
  const p1txt = p1 == null ? '' : ` · P(p1)=${p1.toFixed(4)}`;
  return `stands down · ${margin >= 0 ? '+' : ''}${margin.toFixed(2)} over the suppress floor ${floor.toFixed(2)}${p1txt} — would page below ${floor.toFixed(2)}`;
}

/* ---------- verdict sentence (river drawer §1): generated from reason + evidence ---------- */
export function verdictSentence(d) {
  const sev = (SEV_LABEL[d.severity] || d.severity).toLowerCase();
  const svc = d.service || 'unknown service';
  const conf = fmtConf(d.confidence);
  switch (d.disposition) {
    case 'suppress':
      return `Suppressed: ${sev} alert on ${svc} stood down at confidence ${conf} (${d.reason}). The evidence below shows why.`;
    case 'page_now':
      return `Paged: ${sev} alert on ${svc} cleared the gate at confidence ${conf} (${d.reason}). A human was woken.`;
    case 'page_business_hours':
      return `Queued for business hours: ${sev} alert on ${svc} at confidence ${conf} (${d.reason}). No one was woken.`;
    default:
      return `Passed through unchanged: ${sev} alert on ${svc} at confidence ${conf} (${d.reason}). Sentinel did not alter the pipeline.`;
  }
}

/* ---------- mock-mode local simulate recompute.
 * NOT tuner math. Labeled "mock-mode local recompute" wherever rendered.
 * Live mode POSTs /api/simulate and uses the real projection. */
export const DEFAULT_THRESHOLDS = {
  suppress_p1_max: 0.002, suppress_conf_min: 0.90, page_p1p2_min: 0.30,
  uncertain_conf_max: 0.50, queue_conf_min: 0.70,
};
export const THRESHOLD_META = [
  { key: 'suppress_p1_max', label: 'Suppress: P(p1) must stay below', min: 0, max: 0.05, step: 0.001, fmt: v => v.toFixed(3) },
  { key: 'suppress_conf_min', label: 'Suppress: confidence floor', min: 0.5, max: 1, step: 0.01, fmt: v => v.toFixed(2) },
  { key: 'page_p1p2_min', label: 'Page now: P(p1)+P(p2) above', min: 0, max: 1, step: 0.01, fmt: v => v.toFixed(2) },
  { key: 'uncertain_conf_max', label: 'Page now: uncertainty below', min: 0, max: 1, step: 0.01, fmt: v => v.toFixed(2) },
  { key: 'queue_conf_min', label: 'Queue (biz-hrs): confidence floor', min: 0, max: 1, step: 0.01, fmt: v => v.toFixed(2) },
];
export function applyThresholds(decisions, t, cost = { c_fp: 100, c_fn: 50000 }) {
  let suppress = 0, page_now = 0, queue = 0, baseline = 0, expFalse = 0;
  for (const d of decisions) {
    const probs = d.prob_map?.severity?.probs || {};
    const p1 = probs.p1_critical || 0, p2 = probs.p2_high || 0;
    const conf = d.confidence ?? 0;
    let disp;
    if (p1 < t.suppress_p1_max && conf >= t.suppress_conf_min) disp = 'suppress';
    else if (p1 + p2 >= t.page_p1p2_min) disp = 'page_now';
    else if (conf < t.uncertain_conf_max) disp = 'page_now';
    else if (conf >= t.queue_conf_min) disp = 'page_business_hours';
    else disp = 'passthrough';
    if (disp === 'suppress') { suppress++; expFalse += p1; }
    else if (disp === 'page_now') page_now++;
    else if (disp === 'page_business_hours') queue++;
    else baseline++;
  }
  const n = decisions.length;
  return {
    n_alerts: n, suppress, page_now, queue, baseline,
    exp_false_suppresses: expFalse,
    expected_cost: expFalse * cost.c_fn,
    avoided_page_cost: suppress * cost.c_fp,
    suppress_rate: n ? suppress / n : 0,
    pages_per_night_avoided: null, /* needs a real window — mock mode labels this n/a */
    recommended: { conf_min: 0.9, note: 'mock-mode grid {0.80, 0.85, 0.90, 0.95} on the mock window' },
  };
}

/* ---------- policy diff export (Law L5: reads before writes — export is the artifact) ---------- */
export function policyDiffYaml({ team, oldT, newT, projection, provenance, datasetVersion }) {
  const line = (k) => `  ${k}: ${oldT[k].toFixed(3)} → ${newT[k].toFixed(3)}`;
  return [
    `# Sentinel policy diff — team: ${team}`,
    `# generated ${new Date().toISOString()} · dataset ${datasetVersion}`,
    `# projection: ${projection.suppress} suppress / ${projection.page_now} page_now / exp false-suppresses ${projection.exp_false_suppresses.toFixed(1)}`,
    `# provenance: tuner ${provenance.tuner_rev} · policy ${provenance.policy_version} · sha ${String(provenance.dataset_sha256).slice(0, 12)}…`,
    `# review: attach this file to the PR. Nothing is applied by the simulator.`,
    `team: ${team}`,
    'thresholds:',
    ...Object.keys(newT).map(line),
  ].join('\n');
}
export function branchName(team) {
  const d = new Date().toISOString().slice(0, 10);
  return `policy/${team}-thresholds-${d}`;
}

/* ---------- DRILL 7 (fresh-minds pre-mortem): Jev path dead 5 min before showtime.
 * The UI never calls Jev (Law L5), so the river/drawer/receipts are unaffected —
 * they read the event log. The START flow is the only surface that touches the
 * Jev path (key verify). startPlan() returns the step plan for a given Jev-path
 * state; the degraded plan is the DESIGNED recorded-walkthrough fallback
 * (narrative §6.3) — never fake liveness. Trigger deterministically with
 * ?jev=dead (hash query) so the drill is rehearsable. */
export const JEV_STATES = ['live', 'dead', 'unknown'];
export function jevStateFromParams(params) {
  const v = params.get('jev');
  if (v === 'dead') return 'dead';
  if (v === 'live') return 'live';
  return 'unknown';
}
export function startPlan(jevState) {
  const degraded = jevState === 'dead';
  return {
    jevState,
    degraded,
    banner: degraded
      ? 'Jev path unreachable — this is a recorded walkthrough of a real storm from 2026-10-01. The suppressions are real, they just are not yours. Nothing here pages anyone.'
      : null,
    steps: [
      {
        id: 'key', title: 'Connect your key',
        body: degraded
          ? 'Key verification needs the Jev path, which is down. Your key is NOT stored and NOT verified — the tour continues on the recording. Retry live when the path is back.'
          : 'Paste a TypeSafe API key. Verification fires one live typed-answer call and shows you the raw response — disposition, confidence, latency. The key is never displayed again.',
        degraded,
        skippable: true,
      },
      {
        id: 'source', title: 'Connect an alert source',
        body: 'Point your alert webhook at Sentinel alongside the existing stack, or use the demo source. The pipeline is four named stages — you will watch data move through it.',
        degraded: false, skippable: true,
      },
      {
        id: 'storm', title: 'Guided synthetic storm',
        body: degraded
          ? 'RECORDED STORM (2026-10-01). ~40 alerts, flapping services, a deploy-churn burst, two scripted SEV1s that page. Suppression counts here prove the pipeline works, not that the model is good — calibration is measured on shadow data, in CAL.'
          : 'One click launches a 60-second synthetic storm: ~40 alerts, flapping services, a deploy-churn burst, two genuine SEV1s. Watch suppressions happen with reason codes attached — and the two SEV1s page.',
        degraded,
        skippable: false,
      },
      {
        id: 'why', title: 'First suppression, explained',
        body: 'The flow pauses on one suppressed decision and walks the drawer: the confidence bar (marker vs Q3 vs threshold), the reason code, the input hash, the timeline — then the counterfactual.',
        degraded: false, skippable: true,
      },
      {
        id: 'tune', title: 'Set your thresholds',
        body: 'The simulator opens prefilled with the storm data. Drag one slider, watch the projection reprice, export your first policy diff — or skip. Nothing is auto-applied. Ever.',
        degraded: false, skippable: true,
      },
    ],
    flipBeat: degraded
      ? { mode: 'recorded', label: 'RECORDED FLIP — the live flip feed is down. Same input asked twice, different answer (1.8% of inputs do). Here is the flip timeline from the recording.' }
      : { mode: 'live', label: 'This one changed its mind — 1.8% of inputs do. Here is the flip timeline.' },
  };
}
