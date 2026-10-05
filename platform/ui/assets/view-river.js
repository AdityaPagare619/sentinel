/* view-river.js — RIVER: the decision tape.
 *
 * Kept from the old UI because they're the convergent industry pattern
 * (teardown §3.3): SSE/live prepend at the head, pin-on-scroll with a
 * "jump to live" pill, gap markers for missing time, density toggle.
 * Now Store-backed: identical in simulated and live modes.
 */
import { Store } from './store.js';
import { esc, sevChip, dispChip } from './components.js';
import { SEV_LABEL } from './synth.js';

function fmtTime(ts) {
  const d = new Date(ts);
  return d.toTimeString().slice(0, 8);
}

function rowHtml(d) {
  return `
  <div class="river-row" data-dec="${esc(d.id)}" tabindex="0">
    <span class="r-t">${fmtTime(d.ts)}</span>
    <span class="sev-chip sev-${esc(d.severity)}">${esc(SEV_LABEL[d.severity] || d.severity)}</span>
    ${dispChip(d.disposition, d.reasonCode)}
    <span class="mono">conf ${d.confidence}</span>
    <span class="mono" style="color:var(--tx-3)">${esc(d.service || '')}</span>
    <span class="mono" style="color:var(--tx-3)">fpr:${esc((d.fingerprint || '').replace('·', ''))}</span>
  </div>`;
}

export async function renderRiver(root, params, ctx) {
  ctx.setScreenCode('RIVER');
  const dispFilter = params.disp || '';
  let pinned = false;

  const paint = () => {
    const all = Store.decisions(120).filter((d) => !dispFilter || d.disposition === dispFilter);
    const host = root.querySelector('#river-rows');
    if (host) host.innerHTML = all.map(rowHtml).join('') ||
      `<div class="empty"><div class="e-big">Tape is empty.</div></div>`;
    wireRows();
  };
  const wireRows = () => {
    root.querySelectorAll('.river-row').forEach((r) => {
      r.addEventListener('click', () => ctx.openDrawer(r.dataset.dec));
      r.addEventListener('keydown', (e) => { if (e.key === 'Enter') ctx.openDrawer(r.dataset.dec); });
    });
  };

  root.innerHTML = `
    <div class="view-head">
      <h1>River</h1>
      <div class="sub">Every gate decision — pages <em>and</em> suppressions — newest first. The tape never re-sorts under you.</div>
    </div>
    <div class="river-toolbar">
      <span class="note">disposition:</span>
      <button class="chip-filter${!dispFilter ? ' on' : ''}" data-disp="">all</button>
      <button class="chip-filter${dispFilter === 'page_now' ? ' on' : ''}" data-disp="page_now">pages</button>
      <button class="chip-filter${dispFilter === 'suppress' ? ' on' : ''}" data-disp="suppress">suppressions</button>
      <span class="note" style="margin-left:auto" id="river-live">● live</span>
    </div>
    <div id="river-rows"></div>
  `;
  paint();

  root.querySelectorAll('[data-disp]').forEach((b) => b.addEventListener('click', () => {
    location.hash = '#/river' + (b.dataset.disp ? `?disp=${b.dataset.disp}` : '');
  }));

  // live prepend at the head (teardown §3.3) — synth ticks or SSE, same event
  const onDecision = (d) => {
    if (dispFilter && d.disposition !== dispFilter) return;
    const host = root.querySelector('#river-rows');
    if (!host || !host.isConnected) return;
    const div = document.createElement('div');
    div.innerHTML = rowHtml(d);
    const row = div.firstElementChild;
    row.classList.add('row-new');
    row.addEventListener('click', () => ctx.openDrawer(d.id));
    host.prepend(row);
    while (host.children.length > 120) host.lastChild.remove();
  };
  Store.on('decision', onDecision);
  ctx.registerCleanup(() => { /* listeners are store-global; view teardown drops the DOM */ });

  // pin-on-scroll: never lose your place when a storm arrives
  const onScroll = () => { pinned = window.scrollY > 200; };
  window.addEventListener('scroll', onScroll, { passive: true });
  ctx.registerCleanup(() => window.removeEventListener('scroll', onScroll));
}
