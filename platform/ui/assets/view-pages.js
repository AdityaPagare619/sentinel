/* view-pages.js — PAGES: every open page, triage-ready.
 *
 * R-UI-1: open pages needing human action — never the raw alert stream.
 * R-UI-4: acknowledge and take-ownership are distinct actions (Opsgenie).
 * Problems are the unit, never alerts (N2, A2).
 */
import { Store } from './store.js';
import { esc, sevChip, dispChip } from './components.js';
import { SEV_LABEL } from './synth.js';

function fmtAge(ts) {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m}m` : `${Math.round(m / 60)}h`;
}

export async function renderPages(root, params, ctx) {
  ctx.setScreenCode('PAGES');
  const sevFilter = params.sev || '';
  const pages = Store.openPages().filter((p) => !sevFilter || p.severity === sevFilter);

  root.innerHTML = `
    <div class="view-head">
      <h1>Pages</h1>
      <div class="sub">${pages.length} open problem${pages.length === 1 ? '' : 's'} needing a human. Counted as problems, never alerts.</div>
      <div class="job">Job: triage the queue — ack what's yours, own what isn't, mitigate first.</div>
    </div>
    <div class="river-toolbar">
      <span class="note">severity:</span>
      ${['', 'p1_critical', 'p2_high', 'p3_medium', 'p4_low'].map((s) =>
        `<button class="chip-filter${sevFilter === s ? ' on' : ''}" data-sev="${s}">${s ? SEV_LABEL[s] : 'all'}</button>`).join('')}
    </div>
    <div id="page-list">
      ${pages.length ? pages.map((p) => {
        const decs = p.decisions.map((id) => Store.decisionById(id)).filter(Boolean);
        const latest = decs[decs.length - 1];
        return `
        <article class="page-card sev-${esc(p.severity)}">
          <div class="p-top">
            <span class="sev-chip sev-${esc(p.severity)}">${esc(SEV_LABEL[p.severity] || p.severity)}</span>
            <span class="p-title">${esc(p.symptom)} — ${esc(p.service)}</span>
            <span class="p-meta">${esc(p.owner)} · ${p.alerts.length} alerts · ${p.flaps} flaps · open ${fmtAge(p.createdAt)}</span>
          </div>
          <div class="p-actions">
            ${p.state === 'open'
              ? `<button class="btn primary small" data-ack="${esc(p.id)}">Acknowledge <span class="note">— stops the escalation timer</span></button>
                 <button class="btn small" data-own="${esc(p.id)}">Take ownership</button>`
              : `<button class="btn small" data-own="${esc(p.id)}">Take ownership <span class="note">(ack'd by ${esc(p.ackBy || '?')})</span></button>`}
            <button class="btn quiet small" data-timeline="${esc(p.id)}">timeline</button>
          </div>
          <div class="p-evidence">
            <div class="ev-block"><div class="ev-label">COST OF INACTION</div><div class="ev-body">${esc(p.cost || '—')}</div></div>
            <div class="ev-block"><div class="ev-label">MACHINE'S CALL</div><div class="ev-body">${latest ? dispChip(latest.disposition, latest.reasonCode) : '<span class="note">racing — no decision yet; the timer pages on uncertainty</span>'}</div></div>
            <div class="ev-block"><div class="ev-label">DECISIONS</div><div class="ev-body mono">${decs.length} on this problem</div></div>
          </div>
          <div data-tl="${esc(p.id)}" hidden></div>
        </article>`;
      }).join('') : `<div class="empty"><div class="e-big">Queue clear.</div><div class="note">Every interruption earned its interruption.</div></div>`}
    </div>
  `;

  root.querySelectorAll('[data-sev]').forEach((b) => b.addEventListener('click', () => {
    const h = '#/pages' + (b.dataset.sev ? `?sev=${b.dataset.sev}` : '');
    location.hash = h;
  }));
  const rerender = () => renderPages(root, parseHashParams(), ctx);
  root.querySelectorAll('[data-ack]').forEach((b) => b.addEventListener('click', () => { Store.ackProblem(b.dataset.ack, 'you'); rerender(); }));
  root.querySelectorAll('[data-own]').forEach((b) => b.addEventListener('click', () => { Store.ackProblem(b.dataset.own, 'you (owner)'); rerender(); }));
  root.querySelectorAll('[data-timeline]').forEach((b) => b.addEventListener('click', () => {
    toggleTimeline(root, b.dataset.timeline);
  }));
}

function toggleTimeline(root, problemId) {
  const host = root.querySelector('[data-tl="' + problemId + '"]');
  const p = Store.problemById(problemId);
  if (!host || !p) return;
  if (!host.hidden) { host.hidden = true; return; }
  const decs = p.decisions.map((id) => Store.decisionById(id)).filter(Boolean);
  const rows = decs.map((d) => timelineRow(d));
  if (p.state === 'ackd') rows.push(ackRow(p));
  host.innerHTML = '<div class="ledger" style="margin-top:12px;border-top:1px solid var(--line-1);padding-top:12px">' +
    rows.join('') + '</div>';
  host.hidden = false;
}

function timelineRow(d) {
  return '<div class="l-row"><span class="l-t">' + new Date(d.ts).toLocaleTimeString() + '</span>' +
    '<span><span class="mc-line">' + dispChip(d.disposition, d.reasonCode) + '<span class="mono mc-conf">conf ' + d.confidence + '</span></span>' +
    ' · <span class="note">engine</span></span></div>';
}

function ackRow(p) {
  return '<div class="l-row"><span class="l-t">—</span><span>acknowledged by ' + esc(p.ackBy) +
    ' · <span class="note">human</span></span></div>';
}

function parseHashParams() {
  const h = location.hash || '';
  const q = h.split('?')[1] || '';
  const params = {};
  for (const part of q.split('&')) {
    const [k, v] = part.split('=');
    if (k) params[decodeURIComponent(k)] = decodeURIComponent(v || '');
  }
  return params;
}
