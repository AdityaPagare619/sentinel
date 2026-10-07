/* components.js — atomic components per design/DESIGN_SYSTEM.md §3.
 * One source of truth for chips, badges, the confidence bar, river rows,
 * the detail drawer, skeletons, and error blocks. No screen logic here. */
import { SEV_LABEL, DISP_LABEL, DISP_COLOR, SRC_LABEL,
         fmtInt, fmtPct, fmtConf, fmtTime, fmtTimeBoth, fmtTimeDual, tzAbbr, ageStr, shortFpr, shortHash,
         receiptLine, verdictSentence } from './lib.js';
import { renderPayload, payloadEmptyHtml, payloadSkeletonHtml } from './payload.js';
import { pinnedSectionHtml, pinCellsHtml } from './pins.js';
import { freshnessBadge, freshnessState, FRESHNESS_BUDGETS } from './freshness.js';
export { freshnessBadge }; /* the catalog re-exports the shared components */

/* §8.7 CONSTRAINT (R1 ruling, recorded in code): any rule-driven highlight
 * color — headline rules (banked, D2) or any future annotation layer — must
 * resolve to a token in the §2.2 taxonomy. Literal colors are banned here;
 * the anti-slop suite (tests/antislop.py) fails the build on any #hex/rgb()
 * literal in components or views. */

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ---------- <ReconstructionMark> (Appendix A) — §4.4 in code ----------
 * ANY value that is derived, reconstructed, simulated, or backfilled carries
 * this mark IN-BAND at the point of display, with the same visual weight as
 * the value. Footnotes and tooltips do not satisfy the law. */
export function derivedMark(kind, detail = '') {
  const labels = {
    derived: 'derived', reconstructed: 'reconstructed',
    simulated: 'SIMULATED', backfilled: 'backfilled', projected: 'projected',
  };
  return `<span class="derived-mark mono" title="${esc(detail || 'this value was computed from stored data, not measured')}">${esc(labels[kind] || kind)}</span>`;
}

/* ---------- drawn close glyph (§8.3 — no text-glyph icons) ----------
 * Text close-glyphs render inconsistently and are not part of the drawn icon
 * set (R12). Every dismiss control uses this shape. */
export function closeGlyph() {
  return `<svg class="icon-close" width="11" height="11" viewBox="0 0 12 12" aria-hidden="true"><path d="M1.5 1.5 L10.5 10.5 M10.5 1.5 L1.5 10.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;
}

/* ---------- §3.1 severity: signal bars in neutral ink (B1 three-channel separation) ----------
 * Severity is an INPUT claim, not a decision — so it must not reuse the
 * decision color channel. Bars (shape) + text label (never color-only, §7);
 * the disposition chip keeps the color. Four bars, filled count = level. */
const SEV_BARS = { p1_critical: 4, p2_high: 3, p3_medium: 2, p4_low: 1, known_noise: 0, cannot_determine: 0 };
export function sevChip(sev) {
  const label = SEV_LABEL[sev] || sev;
  const n = SEV_BARS[sev] ?? 0;
  const bars = [1, 2, 3, 4].map(i => `<span class="sev-bar${i <= n ? ' on' : ''}"></span>`).join('');
  return `<span class="sev-sig" role="img" aria-label="severity ${esc(String(label))}"><span class="sev-bars" aria-hidden="true">${bars}</span><span class="sev-label">${esc(String(label))}</span></span>`;
}

/* ---------- §3.2 disposition chip — reason code is MANDATORY ----------
 * Decision channel = color + SHAPE (B1, R12 icon discipline): the glyph is a
 * drawn shape distinct per disposition, so disposition survives color-vision
 * deficiency and dimmed screens (§7 — never color-only).
 *   page_now            ▲ triangle   — a human was woken
 *   page_business_hours ◐ half disc  — queued, half-urgent
 *   suppress            ○ hollow     — stood down, deliberate
 *   passthrough         ▮ square     — unchanged by the gate          */
const DISP_GLYPH = {
  page_now: '<path d="M8 1 L15 14 L1 14 Z" fill="currentColor"/>',
  page_business_hours: '<path d="M8 1 A7 7 0 0 1 8 15 Z" fill="currentColor"/><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.5"/>',
  suppress: '<circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="2"/>',
  passthrough: '<rect x="2.5" y="2.5" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2"/>',
};
export function dispGlyph(disposition) {
  const color = DISP_COLOR[disposition] || 'var(--tx-2)';
  const shape = DISP_GLYPH[disposition] || DISP_GLYPH.passthrough;
  return `<svg class="disp-glyph" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" style="color:${color}">${shape}</svg>`;
}
export function dispChip(disposition, reason) {
  const label = DISP_LABEL[disposition] || esc(disposition);
  const color = DISP_COLOR[disposition] || 'var(--tx-2)';
  const r = reason ? `<span class="disp-reason">· ${esc(reason)}</span>` : `<span class="disp-reason disp-reason-missing">· reason missing — rendering bug</span>`;
  return `<span class="chip disp-chip" style="color:${color};border-color:${color}">${dispGlyph(disposition)}<span class="disp-bar" style="background:${color}"></span>${esc(label)}${r}</span>`;
}

/* ---------- §3.3 data-source badge — reserved colors, never reused ---------- */
export function srcBadge(source) {
  const label = SRC_LABEL[source] || source || 'unknown';
  return `<span class="src-badge" data-src="${esc(source)}">◈ ${esc(label)}</span>`;
}

/* ---------- §3.4 reason-code tag (click filters the river) ---------- */
export function reasonTag(reason, href) {
  if (!reason) return '';
  return `<a class="reason-tag mono" href="${href}" title="filter river to this reason">${esc(reason)}</a>`;
}

/* ---------- §3.7 fingerprint link ---------- */
export function fprLink(fingerprint) {
  if (!fingerprint) return '<span class="mono" style="color:var(--tx-dim)">fpr:—</span>';
  return `<a class="fpr-link mono" href="#/audit?fpr=${esc(fingerprint)}" title="open in audit explorer · click to copy full fingerprint" data-fpr="${esc(fingerprint)}">${esc(shortFpr(fingerprint))}</a>`;
}

/* ---------- §3.5 confidence bar — EXACT geometry ----------
 * 96×14 bar · 10-bin histogram · Q1/Q2/Q3 ticks (Q3 brighter) · ▼ marker ·
 * dashed red threshold line · value label + denominator (Law L1). */
/* ---------- river row (shared by river + audit — one row component) ----------
 * The row renders the CLOSED contract vocabulary only (contract.js). Vendor
 * payload never enters row chrome. Field pins append as display-only cells —
 * they are never filter operands (synthesis §3). */
export function decisionRow(d, { flips = {}, density = 'compact', selected = false, thresholds = null, pins = [], pinsOff = false, freshness = null, dataSource = 'unknown' } = {}) {
  const flip = flips[d.input_sha256];
  const flipBadge = flip && flip.flipped
    ? `<span class="flip-badge mono" title="the machine changed its mind — see flip timeline">${esc(DISP_LABEL[d.disposition] || d.disposition)} →(flip ${fmtTime(flip.last_seen)})→ ${esc(DISP_LABEL[flip.decisions[flip.decisions.length - 1].disposition] || '')}</span>`
    : '';
  const receipt = thresholds ? receiptLine(d, thresholds) : null;
  return `<div class="row density-${density}${selected ? ' selected' : ''}${d.disposition === 'page_now' ? ' is-page' : ''}"
      data-id="${d.id}" tabindex="0" role="button" aria-label="decision ${d.id} ${esc(d.disposition)}">
    <span class="row-time mono" title="${esc(fmtTimeBoth(d.time))} · ${esc(ageStr(d.time))}">${esc(fmtTimeDual(d.time))}</span>
    ${sevChip(d.severity)}
    ${dispChip(d.disposition, d.reason)}
    <span class="row-conf"><span class="mono">${fmtConf(d.confidence)}</span></span>
    <button class="row-team mono" data-team="${esc(d.team)}" title="filter to team">${esc(d.team)}</button>
    ${fprLink(d.fingerprint)}
    ${flipBadge}
    ${srcBadge(dataSource)}
    ${freshness ? freshnessBadge(freshness) : ''}
    ${receipt ? `<span class="row-receipt mono">${esc(receipt)} ${derivedMark('derived', 'recomputed from gate defaults — the read API does not expose live thresholds')}</span>` : ''}
    ${pinsOff ? '' : pinCellsHtml(d, pins)}
  </div>`;
}

/* ---------- contract-drift row (contract.js validation failed) ----------
 * A decision the frozen contract cannot describe is NEVER rendered as a
 * decision. It renders as drift: what we got, what the contract demands,
 * and what to do. Silent rendering would be a lie (P3). */
export function driftRow(d, errors) {
  const id = d && d.id != null ? `#${esc(d.id)}` : '#?';
  return `<div class="row drift" role="alert" aria-label="contract drift on decision ${esc(id)}">
    <span class="mono drift-msg">[contract drift] ${esc(id)} — not rendered as a decision</span>
    <span class="mono drift-detail">${(errors || []).map(e => esc(e)).join('<br>')}</span>
    <span class="mono drift-detail">The platform sent a row the frozen contract (v${esc('1.0.0')}) cannot describe. ` +
      `Paging is unaffected — this is a display-surface problem, filed against the platform contract.</span>
  </div>`;
}

/* ---------- detail drawer (river §5 / audit §1 — locked reading order) ----------
 * P2 (show the work): every decision carries its five companions —
 *   1. evidence (what the model saw)      2. uncertainty (quantized + calibration)
 *   3. freshness (badge + as-of)           4. policy version that produced it
 *   5. fallback reason (deterministic path, when it decided instead of Jev)
 * Companions 3–5 render their HONEST ABSENCE when the read contract does not
 * expose them (synthesis §7) — never invented (P3). */
export function drawerHtml(d, { thresholds = null, datasetVersion = '', flips = {}, pins = [], freshness = null } = {}) {
  const pm = d.prob_map || {};
  const triple = (t, name) => t ? `<div class="ev-triple"><span class="mono ev-name">${esc(name)}</span><span class="mono">Choice: <b>${esc(t.choice)}</b> · conf ${fmtConf(t.confidence)}</span><span class="mono ev-probs">${Object.entries(t.probs || {}).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([k, v]) => `${esc(k)} ${fmtConf(v)}`).join(' · ')}</span></div>` : '';
  const flip = flips[d.input_sha256];
  const fallback = d.jev_model && d.jev_model !== 'deterministic path'
    ? `Jev model ${d.jev_model} evaluated this alert`
    : 'deterministic path decided — no model call was made for this decision';
  const flipHtml = flip && flip.flipped ? `
    <section class="drawer-sec"><h3>Flip timeline</h3>
      <div class="flip-tl">${flip.decisions.map((fd, i) => `
        <div class="flip-ev"><span class="mono">${esc(fmtTimeBoth(fd.time))}</span> ${dispChip(fd.disposition, null).replace(/·[^<]*<\/span>$/, '</span>')} <span class="mono">${fmtConf(fd.confidence)}</span>${i > 0 ? ' <span class="flip-flag mono">← FLIP</span>' : ''}</div>
        ${i < flip.decisions.length - 1 ? '<div class="flip-link">│ input identical (' + esc(shortHash(flip.input_sha256)) + ')</div>' : ''}`).join('')}
      </div>
      <p class="drawer-note">Same input, thresholds, and tuner — the Jev answer changed. Measured band: 1.3–2.2% of inputs.</p>
    </section>` : '';
  return `
  <div class="drawer-head">
    <span class="mono drawer-id">decision #${d.id}</span>
    <button class="drawer-close" data-close aria-label="close (Esc)">${closeGlyph()}</button>
  </div>
  <section class="drawer-sec"><h3>Verdict</h3>
    <div class="drawer-verdict">${dispChip(d.disposition, d.reason)} ${sevChip(d.severity)}</div>
    <p class="drawer-sentence">${esc(verdictSentence(d))}</p>
    <div class="companions mono">
      <span class="comp"><span class="comp-k">freshness</span> ${freshness ? freshnessBadge(freshness) : freshnessBadge({ state: 'degraded', waitingOn: 'stream state unavailable' })}</span>
      <span class="comp"><span class="comp-k">policy</span> ${d.policy_version ? esc(d.policy_version) : `<span class="comp-absent">not exposed by the read API ${derivedMark('derived', '')}</span>`}</span>
      <span class="comp"><span class="comp-k">fallback</span> ${esc(fallback)}</span>
    </div>
  </section>
  <section class="drawer-sec"><h3>Evidence</h3>
    <p class="drawer-note">Confidence is ordinal — shown as reported, never as a calibrated probability.</p>
    <div class="ev-triples">${triple(pm.disposition, 'Q3 disposition')}${triple(pm.severity, 'Q1 severity')}${triple(pm.owning_team, 'Q2 owning team')}</div>
    <div class="ev-meta mono">jev ${esc(d.jev_model || 'deterministic path')} · latency ${d.latency_ms != null ? Math.round(d.latency_ms) + 'ms' : '—'}</div>
  </section>
  <section class="drawer-sec"><h3>Timeline</h3>
    <div class="tl">
      <div class="tl-ev"><span class="mono">${esc(fmtTimeBoth(d.audit?.received_at || d.time))}</span> alert received</div>
      <div class="tl-ev"><span class="mono">${esc(fmtTimeBoth(d.audit?.created_at || d.time))}</span> gate evaluated → ${esc(DISP_LABEL[d.disposition] || d.disposition)}</div>
    </div>
  </section>
  ${flipHtml}
  ${pinnedSectionHtml(d, pins)}
  ${facetsSectionHtml(d)}
  <section class="drawer-sec"><h3>Unmapped bag — vendor payload</h3>
    ${d.alert ? renderPayload(d.alert) : payloadEmptyHtml()}
    <p class="drawer-note">Rendered structurally by the generic payload viewer — it shows the payload's shape, never its meaning. Secret-shaped values are redacted. Flattened dotted paths; the raw payload is untouched above the viewer.</p>
  </section>
  <section class="drawer-sec"><h3>Provenance</h3>
    <div class="prov mono">input&nbsp;&nbsp; ${esc(shortHash(d.input_sha256, 12))}<br>
    dataset ${esc(datasetVersion)}<br>
    fpr&nbsp;&nbsp;&nbsp;&nbsp; ${esc(d.fingerprint || '—')}</div>
    <div class="prov-actions">
      <button class="btn" data-copy="${esc(d.input_sha256 || '')}">copy input hash</button>
      <a class="btn" href="#/audit?in=${esc(d.input_sha256 || '')}">find all decisions on this input</a>
    </div>
  </section>
  <section class="drawer-sec"><h3>Appeal</h3>
    <div class="prov-actions">
      <button class="btn" data-appeal="page">page me anyway</button>
      <a class="btn" href="#/audit?fpr=${esc(d.fingerprint || '')}">dispute this suppression</a>
    </div>
    <p class="drawer-note">Appeals page through your normal paging path and are audit-logged as overrides. Demo build: no page is sent.</p>
  </section>
  <section class="drawer-sec"><h3>Raw</h3>
    <details class="raw"><summary class="mono">GET /api/decision/${d.id}</summary><pre class="mono">${esc(JSON.stringify(d, null, 2))}</pre></details>
  </section>`;
}

/* ---------- facets (A1 data architecture): envelope / facets / unmapped bag ----------
 * Facets are PLATFORM-promoted fields (indexed, costed at promotion; see the
 * Field Catalog follow-up). Pins are console-level display bindings and the
 * discovery mechanism; facets are the promotion target (K2 path). The two are
 * different layers, not competitors — a pin NEVER becomes a facet by being
 * pinned (behavior firewall). When the contract carries no facets, the
 * section states that honestly instead of inventing a facet browser. */
export function facetsSectionHtml(d) {
  const facets = d.facets && typeof d.facets === 'object' ? d.facets : null;
  const keys = facets ? Object.keys(facets) : [];
  if (!keys.length) {
    return `<section class="drawer-sec"><h3>Facets</h3>
      <p class="drawer-note">No promoted facets on this decision — the platform has not promoted vendor fields into the contract yet. Field pins (above) are the discovery mechanism; promotion is by RFC with cardinality, coverage, and index-weight cost display.</p>
    </section>`;
  }
  return `<section class="drawer-sec"><h3>Facets <span class="mono" style="color:var(--tx-2)">· promoted by the platform</span></h3>
    <div class="facet-grid">${keys.map(k => `<div class="facet-cell"><span class="mono facet-k">${esc(k)}</span><span class="mono facet-v">${esc(String(facets[k]).slice(0, 120))}</span></div>`).join('')}</div>
  </section>`;
}

/* ---------- §4.3 registry: the five states of every data-bearing component ----------
 * Blank is never one of the five. This registry is the automated gate for
 * honesty invariant 4 (§9.2): tests/honesty.test.mjs asserts every data
 * component names a renderer for each state. A component that reaches review
 * with only 'ready' designed is sent back — the registry makes that
 * checkable, not a matter of opinion. */
export const FIVE_STATES = ['loading', 'ready', 'stale_degraded', 'error', 'empty'];
export const COMPONENT_STATE_COVERAGE = {
  DecisionRow:       { loading: 'skeletonRows',  ready: 'decisionRow',    stale_degraded: 'freshnessBadge', error: 'errorBlock', empty: 'emptyBlock' },
  FreshnessBadge:    { loading: 'freshnessBadge', ready: 'freshnessBadge', stale_degraded: 'freshnessBadge', error: 'freshnessBadge', empty: null },
  ConfidenceMeter:   { loading: 'skeletonRows',  ready: null,           stale_degraded: 'freshnessBadge', error: 'errorBlock', empty: 'emptyBlock' }, /* retired 2026-10-07 (ordinality): confBar presented confidence over probability bins */
  DispositionTag:    { loading: 'skeletonRows',  ready: 'dispChip',       stale_degraded: 'freshnessBadge', error: 'errorBlock', empty: 'emptyBlock' },
  EvidencePanel:     { loading: 'skeletonRows',  ready: 'drawerHtml',     stale_degraded: 'freshnessBadge', error: 'errorBlock', empty: 'emptyBlock' },
  ReconstructionMark:{ loading: null,            ready: 'derivedMark',    stale_degraded: null,              error: null,         empty: null },
  TimelineExplorer:  { loading: 'skeletonRows',  ready: 'paintChain',     stale_degraded: 'freshnessBadge', error: 'errorBlock', empty: 'emptyBlock' },
  ThresholdSimulator:{ loading: 'skeletonRows',  ready: 'paintCards',     stale_degraded: 'freshnessBadge', error: 'errorBlock', empty: 'emptyBlock' },
  ShadowDiffTable:   { loading: 'skeletonRows',  ready: 'renderShadow',   stale_degraded: 'freshnessBadge', error: 'errorBlock', empty: 'emptyBlock' },
  DegradedBanner:    { loading: null,            ready: 'freshnessBadge', stale_degraded: 'freshnessBadge', error: 'errorBlock', empty: null },
};
/* A renderer name of null means the state is not applicable to the component
 * (e.g. ReconstructionMark is a pure label) — the test asserts the null is
 * deliberate, i.e. present as a key with value null, not missing. */

/* ---------- dead states (design system §5 — written first, happy path is the enhancement) ---------- */
export function skeletonRows(n = 8) {
  return Array.from({ length: n }, () => '<div class="row skeleton"><span class="sk" style="width:52px"></span><span class="sk" style="width:44px"></span><span class="sk" style="width:180px"></span><span class="sk" style="width:110px"></span></div>').join('');
}
export function errorBlock({ what, detail, retryLabel = 'Retry', retryFn = null, escapeHref = null }) {
  const id = 'err-' + Math.random().toString(36).slice(2, 8);
  setTimeout(() => {
    const b = document.querySelector(`[data-err="${id}"]`);
    if (b && retryFn) b.addEventListener('click', retryFn);
  }, 0);
  return `<div class="error-block" role="alert">
    <div class="error-what">${esc(what)}</div>
    <div class="error-detail mono">${esc(detail)}</div>
    <div class="error-actions"><button class="btn" data-err="${id}">${esc(retryLabel)}</button>${escapeHref ? `<a class="btn" href="${escapeHref}">open incidents ↗</a>` : ''}</div>
  </div>`;
}
export function emptyBlock(text, hint) {
  return `<div class="empty-block"><p>${esc(text)}</p>${hint ? `<p class="mono empty-hint">${esc(hint)}</p>` : ''}</div>`;
}
export function gapMarker({ seconds = null, attempt = 0, reason = '' }) {
  let label;
  if (seconds != null) {
    const m = Math.floor(seconds / 60), s = Math.floor(seconds % 60);
    label = `${m}m ${s}s gap in the tape (reconnecting${attempt ? ', attempt ' + attempt : ''})`;
  } else {
    label = `gap in the tape${reason ? ' — ' + reason : ' (reconnecting)'}`;
  }
  return `<div class="row gap-marker mono">— ${esc(label)} —</div>`;
}
