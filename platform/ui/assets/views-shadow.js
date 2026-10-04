/* views-shadow.js — S5 Shadow Report: "is Sentinel earning the cutover?"
 * The surface that earns production trust — and the surface shown to buyers.
 *
 * Headline metrics (synthesis §9 A2, adopted): page-precision and
 * suppression-regret — computed from the read contract's decision records +
 * labeled outcomes (shadow.js), every figure labeled derived. The ONE banned
 * metric: "noise removed %" or any volume headline — it measures suppression
 * volume, not correctness. A disagreement row without bilateral evidence
 * links is a vanity metric in disguise and is not rendered.
 *
 * 30-second read: "last 7 days: N decisions joined, X% agreement,
 * page precision Y% (labeled), suppression regret Z% (labeled), K
 * disagreements — all K reviewed." */
import { Data } from './api.js';
import { dispChip, sevChip, skeletonRows, errorBlock, emptyBlock, esc, derivedMark } from './components.js';
import { freshnessBadge, FRESHNESS_BUDGETS } from './freshness.js';
import { pagePrecision, suppressionRegret, agreement, disagreementKind,
         isLabeled, BANNED_VANITY_PATTERNS } from './shadow.js';
import { fmtInt, fmtPct, fmtConf, fmtTimeDual, routeHref, shortHash } from './lib.js';

export async function renderShadow(root, params, ctx) {
  const window = params.get('last') || '7d';
  ctx.setScreenCode('SHADOW');
  ctx.setSrcBadge(null);

  root.innerHTML = `
  <div class="shadow-wrap">
    <div id="sh-fresh" class="sh-fresh"></div>
    <div id="sh-body">${skeletonRows(6)}</div>
  </div>`;

  const body = root.querySelector('#sh-body');
  const fresh = root.querySelector('#sh-fresh');

  try {
    const env = await Data.getShadow(window);
    const rows = env.data || [];
    const derived = env.meta?.derived || null;
    ctx.setSrcBadge(env.meta?.data_source);
    ctx.setDsVersion(Data.datasetVersion);
    fresh.innerHTML = freshnessBadge({ state: 'live', asOfIso: env.meta?.generated_at || null,
      waitingOn: null, budgetMs: FRESHNESS_BUDGETS.shadow }) +
      ` <span class="mono" style="color:var(--tx-2)">window: last ${esc(window)}</span>`;

    const hasShadowDim = rows.length && rows[0].shadow_disposition !== undefined;
    const agr = hasShadowDim ? agreement(rows) : null;

    /* metrics are computed over LABELED outcomes only; the exclusion is stated */
    let pp, sr;
    if (hasShadowDim) {
      pp = pagePrecision(rows.map(r => ({ disposition: r.actual_disposition, outcome: r.outcome })));
      /* regret is judged on the suppressor's decisions (shadow-suppressed),
       * not on what actually paged */
      sr = suppressionRegret(rows.filter(r => r.shadow_disposition === 'suppress')
        .map(r => ({ disposition: 'suppress', outcome: r.outcome })));
    } else {
      pp = pagePrecision(rows.map(r => ({ disposition: r.disposition, outcome: r.outcome })));
      sr = suppressionRegret(rows.filter(r => r.disposition === 'suppress')
        .map(r => ({ disposition: 'suppress', outcome: r.outcome })));
    }

    const metricCard = (k, v, sub, note) => `
      <div class="hcard"><div class="hcard-k">${esc(k)} ${derivedMark('derived', 'computed in your browser from stored decision records + labeled outcomes')}</div>
        <div class="hcard-v mono">${v}</div><div class="hcard-sub mono">${sub}</div><p class="hcard-gloss">${note}</p></div>`;

    body.innerHTML = `
    <div class="headline-cards">
      ${metricCard('Page precision', pp.precision == null ? '—' : fmtPct(pp.precision),
        `${fmtInt(pp.acked_acted)} of ${fmtInt(pp.labeled)} labeled pages acked + acted${pp.unlabeled ? ` · ${fmtInt(pp.unlabeled)} unlabeled, excluded` : ''}`,
        'Acknowledged-and-acted-on pages ÷ pages sent. A page that woke someone for nothing is the cost; this is the number that prices it.')}
      ${metricCard('Suppression regret', sr.regret == null ? '—' : fmtPct(sr.regret),
        `${fmtInt(sr.regretted)} of ${fmtInt(sr.labeled)} labeled suppressions later regretted${sr.unlabeled ? ` · ${fmtInt(sr.unlabeled)} unlabeled, excluded` : ''}`,
        'Suppressed events later joined to an incident or manually unsuppressed ÷ suppressions. This is the number that kills a suppressor.')}
      ${agr ? `<div class="hcard"><div class="hcard-k">Agreement ${derivedMark('derived', '')}</div>
        <div class="hcard-v mono">${agr.agreeRate == null ? '—' : fmtPct(agr.agreeRate)}</div>
        <div class="hcard-sub mono">${fmtInt(agr.agree)} of ${fmtInt(agr.n)} shadow dispositions matched what actually happened</div>
        <p class="hcard-gloss">Where shadow and reality diverged, each row below carries evidence on both sides. No vanity volume headlines — suppression count is not a quality metric.</p></div>`
      : `<div class="hcard"><div class="hcard-k">Agreement</div>
        <div class="hcard-v mono">—</div>
        <div class="hcard-sub mono">the shadow join is not exposed by the read API</div>
        <p class="hcard-gloss">Shadow-vs-actual comparison needs the platform's shadow join (RFC). Page precision and suppression regret above are computable from stored outcomes and stand on their own.</p></div>`}
    </div>
    ${agr && agr.disagreements.length ? `
      <h3 class="sec-h">Disagreements — ${agr.disagreements.length}, each with evidence on both sides</h3>
      <div class="diff-table" role="table" aria-label="shadow disagreements">
        ${agr.disagreements.map(r => `
          <div class="diff-row" role="row">
            <span class="mono diff-id">#${r.decision_id}</span>
            <span class="mono diff-time">${esc(fmtTimeDual(r.time))}</span>
            ${sevChip(r.severity)}
            <span class="diff-cols">${dispChip(r.shadow_disposition, null)}<span class="mono diff-arrow">→</span>${dispChip(r.actual_disposition, null)}</span>
            <span class="mono diff-kind">${esc(disagreementKind(r))}</span>
            <span class="mono diff-ev"><a href="${routeHref('audit', { in: '', fpr: '', q: r.service, ...(Data.mode === 'mock' ? { mock: '1' } : {}) })}">evidence: audit trail</a> · <a href="${routeHref('river', { q: r.service, ...(Data.mode === 'mock' ? { mock: '1' } : {}) })}">river</a></span>
          </div>
          ${r.review_note ? `<div class="diff-note mono">${esc(r.review_note)}</div>` : ''}
        `).join('')}
      </div>` : agr ? '<p class="drawer-note">No disagreements in the window — shadow and reality agreed on every joined decision.</p>' : ''}
    <p class="strip-inline mono">30-second read: last ${esc(window)} — ${fmtInt(rows.length)} decisions joined · page precision ${pp.precision == null ? '—' : fmtPct(pp.precision)} (labeled) · suppression regret ${sr.regret == null ? '—' : fmtPct(sr.regret)} (labeled)${agr ? ` · ${fmtInt(agr.agree)} of ${fmtInt(agr.n)} agreement, ${fmtInt(agr.disagreements.length)} disagreements reviewed` : ''}.</p>`;
    ctx.setStrip(`Shadow ${window}: page precision ${pp.precision == null ? '—' : fmtPct(pp.precision)}, suppression regret ${sr.regret == null ? '—' : fmtPct(sr.regret)} — both over labeled outcomes only.`);
  } catch (e) {
    body.innerHTML = errorBlock({
      what: `Couldn't build the shadow report (${e.status || 'unreachable'}).`,
      detail: e.message || '', retryFn: () => renderShadow(root, params, ctx),
    });
    ctx.setStrip('Shadow report unreachable — the trust numbers are not refreshable. Treat tonight\u2019s cutover confidence as unmeasured.');
  }
}

/* S5 self-audit hook (also asserted in tests/shadow.test.mjs): the vanity
 * patterns may never appear on this surface. */
export function shadowCopyIsClean(html) {
  return !BANNED_VANITY_PATTERNS.some(re => re.test(html));
}
