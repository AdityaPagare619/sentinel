/* view-audit.js — AUDIT: one unified timeline.
 *
 * R-UI-20: human actions, automation decisions, suppressions, near-misses —
 * chronologically earliest-first, every entry attributed (human / engine / rule).
 * R-UI-21: "considered and rejected" entries are first-class.
 * The hash-chained event log is the source of truth; this is its reading room.
 */
import { Store } from './store.js';
import { esc, dispChip } from './components.js';

function fmtTime(ts) { return new Date(ts).toLocaleString(); }

export async function renderAudit(root, params, ctx) {
  ctx.setScreenCode('AUDIT');
  const q = (params.q || '').toLowerCase();
  const decisions = Store.decisions(200).filter((d) =>
    !q || d.reasonCode.toLowerCase().includes(q) || (d.service || '').toLowerCase().includes(q) ||
    d.disposition.includes(q));
  // earliest-first: the diagnostic order (BigPanda's earliest-first, teardown §1.6)
  const ordered = decisions.slice().reverse();

  root.innerHTML = `
    <div class="view-head">
      <h1>Audit</h1>
      <div class="sub">One timeline: what the machine decided, what it considered and rejected, and what humans did —
      earliest-first, every entry attributed. This is the record a postmortem reads.</div>
    </div>
    <div class="river-toolbar">
      <input id="audit-q" class="palette-input mono" style="max-width:340px" placeholder="filter: reason, service, disposition…"
        value="${esc(params.q || '')}" autocomplete="off" spellcheck="false">
      <button class="btn small" id="audit-export">Export timeline</button>
      <span class="note">${ordered.length} entries</span>
    </div>
    <div class="ledger">
      ${ordered.map((d) => `
        <div class="l-row">
          <span class="l-t">${fmtTime(d.ts)}</span>
          <span>${dispChip(d.disposition, d.reasonCode)} <span class="mono">${esc(d.reasonCode)}</span>
            <span class="note">· ${esc(d.service || '')} · conf ${d.confidence}</span>
            <span class="note" style="margin-left:8px">engine</span></span>
        </div>
        ${d.undone ? `<div class="l-row"><span class="l-t">${fmtTime(d.ts)}</span>
          <span>↩ suppression reversed <span class="note">human</span></span></div>` : ''}
        ${(d.proof.consideredRejected || []).map((c) =>
          `<div class="l-row"><span class="l-t">—</span>
            <span class="note">considered &amp; rejected: ${esc(c)} <span class="note">engine</span></span></div>`).join('')}
      `).join('') || `<div class="empty"><div class="e-big">Nothing matches.</div></div>`}
    </div>
  `;

  const input = root.querySelector('#audit-q');
  let t = null;
  input.addEventListener('input', () => {
    clearTimeout(t);
    t = setTimeout(() => { location.hash = '#/audit' + (input.value ? `?q=${encodeURIComponent(input.value)}` : ''); }, 400);
  });
  root.querySelector('#audit-export').addEventListener('click', () => {
    const text = ordered.map((d) =>
      `${new Date(d.ts).toISOString()} [engine] ${d.disposition} ${d.reasonCode} ${d.service} conf=${d.confidence}`).join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'sentinel-timeline.txt';
    a.click();
    URL.revokeObjectURL(a.href);
  });
}
