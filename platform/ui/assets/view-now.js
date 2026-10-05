/* view-now.js — NOW: what's happening right now.
 *
 * Job: "When my phone goes off at 3 AM, I want to know what's broken, how bad
 * it is, and what to do first" (J-paged). Order follows the cost gradient:
 * state → scope → change → detail (USER-RESEARCH §3).
 *
 * Layout order (R-UI-2, N1):
 *  1. storm/degraded banners (if active — the exception, not the rule)
 *  2. open pages: problems, severity-ordered (R-UI-1 — never the raw stream)
 *  3. active suppressions summary (R-UI-9 — count + top reasons, not the list)
 *  4. what changed recently (N5)
 */
import { Store } from './store.js';
import { esc, sevChip, dispChip } from './components.js';
import { SEV_LABEL } from './synth.js';

function fmtAge(ts) {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.round(m / 60)}h ago`;
}

function pageCard(p, ctx) {
  const latestDec = p.decisions.length
    ? Store.decisionById(p.decisions[p.decisions.length - 1]) : null;
  return `
  <article class="page-card sev-${esc(p.severity)}" data-problem="${esc(p.id)}">
    <div class="p-top">
      <span class="sev-chip sev-${esc(p.severity)}">${esc(SEV_LABEL[p.severity] || p.severity)}</span>
      <span class="p-title">${esc(p.symptom)} — ${esc(p.service)}</span>
      <span class="p-meta">${esc(p.owner)} · ${p.alerts.length} alert${p.alerts.length === 1 ? '' : 's'} · open ${fmtAge(p.createdAt)}</span>
      ${p.state === 'ackd' ? `<span class="p-meta">ack'd by ${esc(p.ackBy || 'someone')}</span>` : ''}
    </div>
    <div class="p-actions">
      ${p.state === 'open'
        ? `<button class="btn primary small" data-ack="${esc(p.id)}">Acknowledge</button>`
        : `<button class="btn small" data-ack="${esc(p.id)}">Take ownership</button>`}
      <button class="btn quiet small" data-evidence="${esc(p.id)}">evidence</button>
    </div>
    <div class="p-evidence" data-evidence-body="${esc(p.id)}" hidden>
      <div class="ev-block"><div class="ev-label">COST OF INACTION</div><div class="ev-body">${esc(p.cost || '—')}</div></div>
      <div class="ev-block"><div class="ev-label">BLAST RADIUS</div><div class="ev-body"><strong>${esc(p.service)}</strong> · owner ${esc(p.owner)}</div></div>
      <div class="ev-block"><div class="ev-label">WHAT CHANGED</div><div class="ev-body">${p.changedRecently.length ? p.changedRecently.map(esc).join('<br>') : 'no recent changes on record'}</div></div>
      <div class="ev-block"><div class="ev-label">MACHINE'S CALL</div><div class="ev-body">${latestDec
        ? `${dispChip(latestDec.disposition, latestDec.reasonCode)} <span class="mono">${esc(latestDec.reasonCode)}</span> · conf ${latestDec.confidence}`
        : 'no decision yet — racing now'}</div></div>
    </div>
  </article>`;
}

export async function renderNow(root, params, ctx) {
  ctx.setScreenCode('NOW');
  const pipe = Store.pipeline();
  const pages = Store.openPages();
  const supps = Store.suppressions(200);

  const byReason = {};
  for (const d of supps) byReason[d.reasonCode] = (byReason[d.reasonCode] || 0) + 1;
  const topReasons = Object.entries(byReason).sort((a, b) => b[1] - a[1]).slice(0, 3);

  const changes = [];
  for (const p of pages) for (const c of p.changedRecently) if (!changes.includes(c)) changes.push(c);

  root.innerHTML = `
    <div class="view-head">
      <h1>Now</h1>
      <div class="sub">What the machine is doing, what needs you, and what it handled on its own — right now.</div>
    </div>
    ${pipe.storm ? `<div class="storm-banner">▲ STORM — ${pipe.storm.size} signals folding into one problem · rule correlator.storm_fold · <a href="#/proofs">see what's suppressed</a></div>` : ''}
    ${pipe.health !== 'live' ? `<div class="degraded-banner">PIPELINE ${pipe.health.toUpperCase()} — lag ${Math.round(pipe.lagMs / 1000)}s. Screens may be stale; the gate pages on uncertainty. <span class="note">Freshness is a safety law — stale evidence never authorizes suppression.</span></div>` : ''}

    <div class="section-label">OPEN PAGES — NEED A HUMAN (${pages.length})</div>
    ${pages.length ? pages.map((p) => pageCard(p, ctx)).join('') : `
      <div class="empty"><div class="e-big">Nothing on fire.</div>
      <div class="note">No open problems. ${supps.length} suppressions handled quietly in the background — <a href="#/proofs">inspect them</a>.</div></div>`}

    <div class="section-label">SUPPRESSED QUIETLY (${supps.length} RECENT)</div>
    <div class="note" style="margin-bottom:12px">Every one carries its proof. ${topReasons.map(([r, n]) => `<span class="mono">${esc(r)} ×${n}</span>`).join(' · ')} — <a href="#/proofs">open the proof ledger →</a></div>

    ${changes.length ? `<div class="section-label">WHAT CHANGED RECENTLY</div>
    <div class="note">${changes.map((c) => `· ${esc(c)}`).join('<br>')}</div>` : ''}
  `;

  root.querySelectorAll('[data-ack]').forEach((b) => b.addEventListener('click', () => {
    Store.ackProblem(b.dataset.ack, 'you');
    renderNow(root, params, ctx); // re-render: ack-first, evidence-second (teardown §3.1)
  }));
  root.querySelectorAll('[data-evidence]').forEach((b) => b.addEventListener('click', () => {
    const body = root.querySelector(`[data-evidence-body="${b.dataset.evidence}"]`);
    if (body) body.hidden = !body.hidden;
  }));
}
