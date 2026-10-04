/* components.js — atomic components per design/DESIGN_SYSTEM.md §3.
 * One source of truth for chips, badges, the confidence bar, river rows,
 * the detail drawer, skeletons, and error blocks. No screen logic here. */
import { SEV_LABEL, DISP_LABEL, DISP_COLOR, SRC_LABEL,
         fmtInt, fmtPct, fmtConf, fmtTime, fmtTimeBoth, ageStr, shortFpr, shortHash,
         binForConf, quartilesFromBins, receiptLine, verdictSentence } from './lib.js';
import { renderPayload, payloadEmptyHtml, payloadSkeletonHtml } from './payload.js';
import { pinnedSectionHtml, pinCellsHtml } from './pins.js';

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
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

/* ---------- §3.2 disposition chip — reason code is MANDATORY ---------- */
export function dispChip(disposition, reason) {
  const label = DISP_LABEL[disposition] || esc(disposition);
  const color = DISP_COLOR[disposition] || 'var(--tx-2)';
  const r = reason ? `<span class="disp-reason">· ${esc(reason)}</span>` : `<span class="disp-reason disp-reason-missing">· reason missing — rendering bug</span>`;
  return `<span class="chip disp-chip" style="color:${color};border-color:${color}"><span class="disp-bar" style="background:${color}"></span>${esc(label)}${r}</span>`;
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
export function confBar({ conf, bins, threshold, thresholdLabel = 'gate', showDenom = true }) {
  const W = 96, H = 14, binW = W / 10;
  const qs = quartilesFromBins(bins || []);
  const maxN = Math.max(1, ...(bins || []).map(b => b.n));
  const total = (bins || []).reduce((a, b) => a + b.n, 0);
  const thin = total < 100;
  const x = v => Math.max(0, Math.min(1, v)) * W;

  let bars = '';
  (bins || []).forEach((b, i) => {
    const bx = i * binW;
    if (b.n === 0) {
      bars += `<rect x="${bx + binW / 2 - 0.5}" y="${H - 1}" width="1" height="1" fill="var(--line-0)"/>`;
    } else {
      const bh = Math.max(1.5, (b.n / maxN) * (H - 2));
      bars += `<rect x="${bx + 0.5}" y="${H - bh}" width="${binW - 1}" height="${bh}" fill="var(--line-1)" opacity="0.6"/>`;
    }
  });

  /* quartile ticks: full height, floating above bars with a 2px gap at bar pixels */
  const ticks = [qs.q1, qs.q2, qs.q3].map((q, i) => {
    const tx = x(q);
    const color = i === 2 ? 'var(--tx-1)' : 'var(--tx-2)';
    /* find bars this tick crosses; split the tick to leave a 2px gap around each */
    const segs = [];
    let cursor = 0;
    const overlaps = (bins || []).map((b, bi) => {
      const bh = Math.max(1.5, (b.n / maxN) * (H - 2));
      const bx0 = bi * binW, bx1 = bx0 + binW;
      return (tx >= bx0 && tx <= bx1 && b.n > 0) ? [H - bh - 2, H] : null;
    });
    overlaps.sort((a, b) => (a ? a[0] : 1e9) - (b ? b[0] : 1e9));
    for (const o of overlaps) {
      if (!o) continue;
      if (o[0] > cursor) segs.push([cursor, o[0]]);
      cursor = Math.max(cursor, o[1]);
    }
    if (cursor < H) segs.push([cursor, H]);
    return segs.map(([y0, y1]) =>
      `<line x1="${tx}" y1="${y0}" x2="${tx}" y2="${y1}" stroke="${color}" stroke-width="1" opacity="0.8"/>`).join('');
  }).join('');

  const mx = x(conf);
  const marker = `<path d="M ${mx - 2.5} -4 L ${mx + 2.5} -4 L ${mx} 2 Z" fill="var(--tx-0)"/>`;
  const thrLine = threshold != null
    ? `<line x1="${x(threshold)}" y1="0" x2="${x(threshold)}" y2="${H}" stroke="var(--disp-page)" stroke-width="1" stroke-dasharray="2,2" opacity="0.7"><title>${esc(thresholdLabel)} ${threshold}</title></line>`
    : '';
  const hatch = thin ? `<rect x="0" y="0" width="${W}" height="${H}" fill="url(#hatch)" opacity="0.5"/>` : '';
  const bin = binForConf(bins || [], conf);
  const denom = thin
    ? `(n=${fmtInt(total)} · below 100 — calibration provisional)`
    : `(n=${fmtInt(total)} · bin ${bin ? bin.predicted_lo.toFixed(2) + '–' + bin.predicted_hi.toFixed(2) : '—'})`;

  return `<span class="confbar" role="img" aria-label="confidence ${fmtConf(conf)}, denominator n=${fmtInt(total)}">
    <svg width="${W}" height="20" viewBox="0 0 ${W} 20" aria-hidden="true">
      <defs><pattern id="hatch" width="4" height="4" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
        <rect width="4" height="4" fill="transparent"/><line x1="0" y1="0" x2="0" y2="4" stroke="var(--tx-2)" stroke-width="1" opacity="0.5"/>
      </pattern></defs>
      <rect x="0" y="4" width="${W}" height="${H}" rx="4" fill="var(--bg-2)"/>
      <g transform="translate(0,4)">${bars}${hatch}<g>${ticks}</g>${thrLine}</g>
      ${marker}
    </svg>
    <span class="confbar-text"><span class="confbar-val mono">${fmtConf(conf)}</span>${showDenom ? `<span class="confbar-denom mono">${esc(denom)}</span>` : ''}</span>
  </span>`;
}

/* ---------- reliability diagram (calibration spec §2): 480×360 canvas ---------- */
export function relDiagram(el, { bins, threshold, thresholdLabel = 'gate' }) {
  const W = 480, H = 360, dpr = window.devicePixelRatio || 1;
  el.width = W * dpr; el.height = H * dpr;
  el.style.width = W + 'px'; el.style.height = H + 'px';
  const ctx = el.getContext('2d');
  ctx.scale(dpr, dpr);
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const pad = { l: 44, r: 12, t: 16, b: 40 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const X = v => pad.l + v * iw, Y = v => pad.t + (1 - v) * ih;

  ctx.strokeStyle = css('--line-0'); ctx.lineWidth = 1;
  ctx.fillStyle = css('--tx-2'); ctx.font = '10px ' + css('--font-mono').split(',')[0];
  for (let g = 0; g <= 10; g += 2) {
    const v = g / 10;
    ctx.beginPath(); ctx.moveTo(X(v), pad.t); ctx.lineTo(X(v), pad.t + ih); ctx.stroke();
    ctx.fillText(v.toFixed(1), X(v) - 8, pad.t + ih + 16);
    ctx.beginPath(); ctx.moveTo(pad.l, Y(v)); ctx.lineTo(pad.l + iw, Y(v)); ctx.stroke();
    ctx.fillText(v.toFixed(1), pad.l - 28, Y(v) + 3);
  }
  /* perfect-calibration diagonal */
  ctx.strokeStyle = css('--line-1'); ctx.setLineDash([5, 4]);
  ctx.beginPath(); ctx.moveTo(X(0), Y(0)); ctx.lineTo(X(1), Y(1)); ctx.stroke();
  ctx.setLineDash([]);

  const bw = iw / 10;
  const hatchFor = (x0, y0, w, h) => {
    ctx.save(); ctx.beginPath(); ctx.rect(x0, y0, w, h); ctx.clip();
    ctx.strokeStyle = css('--tx-2'); ctx.lineWidth = 1; ctx.globalAlpha = 0.5;
    for (let d = -h; d < w; d += 5) { ctx.beginPath(); ctx.moveTo(x0 + d, y0 + h); ctx.lineTo(x0 + d + h, y0); ctx.stroke(); }
    ctx.restore(); ctx.globalAlpha = 1;
  };
  bins.forEach((b, i) => {
    const mid = (b.predicted_lo + b.predicted_hi) / 2;
    const x0 = pad.l + i * bw + 3, w = bw - 6;
    const y1 = Y(b.observed_rate), y0 = pad.t + ih;
    let color;
    if (b.n < 30) color = css('--tx-dim');
    else if (Math.abs(mid - b.observed_rate) <= 0.05) color = css('--src-shadow');
    else if (mid > b.observed_rate) color = css('--sev-2');
    else color = css('--sev-4');
    ctx.globalAlpha = b.n < 30 ? 0.35 : 0.8;
    ctx.fillStyle = color;
    ctx.fillRect(x0, y1, w, Math.max(1, y0 - y1));
    ctx.globalAlpha = 1;
    if (b.n < 30) hatchFor(x0, y1, w, Math.max(1, y0 - y1));
    b._rect = { x0, y0: y1, w, h: Math.max(1, y0 - y1) };
  });

  /* gate threshold line */
  if (threshold != null) {
    ctx.strokeStyle = css('--disp-page'); ctx.setLineDash([4, 3]); ctx.globalAlpha = 0.85;
    ctx.beginPath(); ctx.moveTo(X(threshold), pad.t); ctx.lineTo(X(threshold), pad.t + ih); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha = 1;
    ctx.fillStyle = css('--disp-page');
    ctx.fillText(`${thresholdLabel} ${threshold.toFixed(2)}`, Math.min(X(threshold) + 4, W - 70), pad.t + 10);
  }
  ctx.fillStyle = css('--tx-2');
  ctx.fillText('predicted confidence →', pad.l + iw - 130, H - 6);
  ctx.save(); ctx.translate(12, pad.t + 90); ctx.rotate(-Math.PI / 2);
  ctx.fillText('observed rate →', 0, 0); ctx.restore();

  /* hover: the Bloomberg moment — repriced quote tooltip */
  const tip = document.createElement('div');
  tip.className = 'rel-tip mono'; tip.hidden = true;
  el.parentElement.style.position = 'relative';
  el.parentElement.appendChild(tip);
  el.addEventListener('mousemove', (e) => {
    const r = el.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const b = bins.find(b => b._rect && mx >= b._rect.x0 && mx <= b._rect.x0 + b._rect.w);
    if (!b) { tip.hidden = true; return; }
    const mid = (b.predicted_lo + b.predicted_hi) / 2;
    let verdict;
    if (b.n < 30) verdict = `n=${b.n} — too thin to trust, excluded from ECE`;
    else if (Math.abs(mid - b.observed_rate) <= 0.05) verdict = 'calibrated — the machine knows this band';
    else if (mid > b.observed_rate) verdict = `overconfident — treat ${mid.toFixed(2)}s as ~${b.observed_rate.toFixed(2)}`;
    else verdict = `underconfident — ${mid.toFixed(2)}s behave like ~${b.observed_rate.toFixed(2)}`;
    const eceC = b.n >= 30 ? Math.abs(mid - b.observed_rate) * (b.n / bins.reduce((a, x) => a + x.n, 0)) : 0;
    tip.innerHTML = `bin ${b.predicted_lo.toFixed(2)}–${b.predicted_hi.toFixed(2)}<br>` +
      `predicted ${mid.toFixed(3)} · observed ${b.observed_rate.toFixed(3)}<br>` +
      `n=${fmtInt(b.n)} · ECE contribution ${eceC.toFixed(3)}<br>` +
      `<span class="rel-tip-verdict">${esc(verdict)}</span>`;
    tip.style.left = Math.min(mx + 14, W - 210) + 'px';
    tip.style.top = Math.max(my - 10, 8) + 'px';
    tip.hidden = false;
  });
  el.addEventListener('mouseleave', () => { tip.hidden = true; });
}

/* ---------- river row (shared by river + audit — one row component) ----------
 * The row renders the CLOSED contract vocabulary only (contract.js). Vendor
 * payload never enters row chrome. Field pins append as display-only cells —
 * they are never filter operands (synthesis §3). */
export function decisionRow(d, { flips = {}, density = 'compact', selected = false, thresholds = null, pins = [], pinsOff = false } = {}) {
  const flip = flips[d.input_sha256];
  const flipBadge = flip && flip.flipped
    ? `<span class="flip-badge mono" title="the machine changed its mind — see flip timeline">${esc(DISP_LABEL[d.disposition] || d.disposition)} →(flip ${fmtTime(flip.last_seen)})→ ${esc(DISP_LABEL[flip.decisions[flip.decisions.length - 1].disposition] || '')}</span>`
    : '';
  const receipt = thresholds ? receiptLine(d, thresholds) : null;
  return `<div class="row density-${density}${selected ? ' selected' : ''}${d.disposition === 'page_now' ? ' is-page' : ''}"
      data-id="${d.id}" tabindex="0" role="button" aria-label="decision ${d.id} ${esc(d.disposition)}">
    <span class="row-time mono" title="${esc(fmtTimeBoth(d.time))} · ${esc(ageStr(d.time))}">${esc(fmtTime(d.time))}</span>
    ${sevChip(d.severity)}
    ${dispChip(d.disposition, d.reason)}
    <span class="row-conf"><span class="mono">${fmtConf(d.confidence)}</span> <span class="mono row-denom">(shadow)</span></span>
    <button class="row-team mono" data-team="${esc(d.team)}" title="filter to team">${esc(d.team)}</button>
    ${fprLink(d.fingerprint)}
    ${flipBadge}
    ${srcBadge('shadow')}
    ${receipt ? `<span class="row-receipt mono">${esc(receipt)} <span class="receipt-derived">(derived from gate defaults — live thresholds are not exposed by the read API)</span></span>` : ''}
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

/* ---------- detail drawer (river §5 / audit §1 — locked reading order) ---------- */
export function drawerHtml(d, { bins = null, thresholds = null, datasetVersion = '', flips = {}, pins = [] } = {}) {
  const pm = d.prob_map || {};
  const triple = (t, name) => t ? `<div class="ev-triple"><span class="mono ev-name">${esc(name)}</span><span class="mono">Choice: <b>${esc(t.choice)}</b> · conf ${fmtConf(t.confidence)}</span><span class="mono ev-probs">${Object.entries(t.probs || {}).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([k, v]) => `${esc(k)} ${fmtConf(v)}`).join(' · ')}</span></div>` : '';
  const flip = flips[d.input_sha256];
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
    <button class="drawer-close" data-close aria-label="close (Esc)">✕</button>
  </div>
  <section class="drawer-sec"><h3>Verdict</h3>
    <div class="drawer-verdict">${dispChip(d.disposition, d.reason)} ${sevChip(d.severity)}</div>
    <p class="drawer-sentence">${esc(verdictSentence(d))}</p>
  </section>
  <section class="drawer-sec"><h3>Evidence</h3>
    ${bins ? confBar({ conf: d.confidence, bins, threshold: thresholds?.suppress_conf_min, thresholdLabel: 'suppress floor' }) : '<p class="drawer-note">Team distribution unavailable — confidence shown without its typical band.</p>'}
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
  <section class="drawer-sec"><h3>Vendor payload</h3>
    ${d.alert ? renderPayload(d.alert) : payloadEmptyHtml()}
    <p class="drawer-note">Rendered structurally by the generic payload viewer — it shows the payload's shape, never its meaning. Secret-shaped values are redacted.</p>
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
