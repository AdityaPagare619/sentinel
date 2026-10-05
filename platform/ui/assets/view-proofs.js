/* view-proofs.js — PROOFS: the suppression proof ledger.
 *
 * The surface no vendor has built (teardown §4.1). Every suppression carries
 * its decision card: the exact rule, who configured it, the matched condition
 * with values, expiry, and one-tap override (R-UI-7). Suppressed items are
 * first-class, browsable, recoverable (R-UI-8). One unified view (R-UI-9).
 * The undo is always next to the action (N4).
 */
import { Store } from './store.js';
import { esc, sevChip, dispChip } from './components.js';
import { SEV_LABEL } from './synth.js';

function fmtAge(ts) {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m}m ago` : `${Math.round(m / 60)}h ago`;
}

function proofCard(d) {
  const pr = d.proof;
  const pc = pr.perClass;
  return `
  <article class="proof-card" data-dec="${esc(d.id)}">
    <div class="pr-top">
      <span class="sev-chip sev-${esc(d.severity)}">${esc(SEV_LABEL[d.severity] || d.severity)}</span>
      ${dispChip(d.disposition, d.reasonCode)}
      <span class="mono" style="font-size:13px"><strong>${esc(d.reasonCode)}</strong></span>
      <span class="note">${esc(d.service)} · ${fmtAge(d.ts)} · conf ${d.confidence}</span>
    </div>
    <div class="proof-grid">
      <div class="ev-block"><div class="ev-label">RULE</div>
        <div class="ev-body"><span class="pr-rule">${esc(pr.rule)}</span><br>configured by ${esc(pr.configuredBy)}</div></div>
      <div class="ev-block"><div class="ev-label">MATCHED CONDITION</div>
        <div class="ev-body">${esc(pr.detail)}<br><span class="mono">${esc(pr.matchedCondition)}</span></div></div>
      <div class="ev-block"><div class="ev-label">EVIDENCE</div>
        <div class="ev-body">cues: ${pr.cues.map(esc).join(' · ')}<br>evidence age: <strong>${pr.evidenceAgeMs}ms</strong> (fresh)
        ${pr.expiresAt ? `<br>expires: ${new Date(pr.expiresAt).toLocaleTimeString()} — suppression ends by itself` : ''}</div></div>
      <div class="ev-block"><div class="ev-label">TRACK RECORD — THIS RULE, THIS ALERT SHAPE</div>
        <div class="track-record">precision ${(pc.precision * 100).toFixed(1)}% · recall ${(pc.recall * 100).toFixed(1)}% · n=${pc.n}</div>
        <div class="note mt3">Not an aggregate claim — this rule on alerts shaped like this one.</div></div>
      <div class="ev-block"><div class="ev-label">CONSIDERED &amp; REJECTED</div>
        <div class="ev-body">${pr.consideredRejected.map(esc).join('<br>')}</div></div>
      <div class="ev-block"><div class="ev-label">COST OF INACTION</div>
        <div class="ev-body">${esc(d.costOfInaction || '—')}</div></div>
    </div>
    <div class="undo-row">
      ${d.undone
        ? `<span class="undone-banner">↩ suppression reversed by you — this problem will page on its next alert</span>`
        : `<button class="btn small" data-undo="${esc(d.id)}">↩ Undo this suppression</button>
           <span class="note">one action. cheaper than the doubt.</span>`}
    </div>
  </article>`;
}

export async function renderProofs(root, params, ctx) {
  ctx.setScreenCode('PROOFS');
  const reasonFilter = params.reason || '';
  const all = Store.suppressions(200);
  const list = all.filter((d) => !reasonFilter || d.reasonCode === reasonFilter);
  const reasons = [...new Set(all.map((d) => d.reasonCode))];

  root.innerHTML = `
    <div class="view-head">
      <h1>Proof ledger</h1>
      <div class="sub">${all.length} suppressions in the window. Every one shows its proof — the rule, the evidence,
      the track record, and the undo. The removed noise is inspectable, never invisible.</div>
      <div class="job">Job: trust, rebuilt daily — review what the machine suppressed and why.</div>
    </div>
    <div class="river-toolbar">
      <span class="note">reason:</span>
      <button class="chip-filter${!reasonFilter ? ' on' : ''}" data-reason="">all</button>
      ${reasons.map((r) => `<button class="chip-filter${reasonFilter === r ? ' on' : ''}" data-reason="${esc(r)}">${esc(r)}</button>`).join('')}
    </div>
    ${list.length ? list.map(proofCard).join('') : `<div class="empty"><div class="e-big">No suppressions${reasonFilter ? ` with reason ${esc(reasonFilter)}` : ''}.</div></div>`}
  `;

  root.querySelectorAll('[data-reason]').forEach((b) => b.addEventListener('click', () => {
    location.hash = '#/proofs' + (b.dataset.reason ? `?reason=${encodeURIComponent(b.dataset.reason)}` : '');
  }));
  root.querySelectorAll('[data-undo]').forEach((b) => b.addEventListener('click', () => {
    if (Store.undoSuppression(b.dataset.undo)) {
      const card = root.querySelector(`[data-dec="${b.dataset.undo}"]`);
      if (card) card.outerHTML = proofCard(Store.decisionById(b.dataset.undo));
    }
  }));
}
