/* views-cal.js — Calibration: "is the machine honest about what it knows?"
 * Reads: GET /api/calibration?team=, GET /api/analytics/flips?window=7d.
 * Law L3: no accuracy claims — calibration, coverage, flips, all with denominators. */
import { Data } from './api.js';
import { relDiagram, skeletonRows, errorBlock, emptyBlock, esc } from './components.js';
import { stripCal, gloss80, calVerdict, overconfidentBins, fmtInt, fmtPct, fmtConf,
         routeHref } from './lib.js';

const TEAMS = ['all', 'platform', 'network', 'data', 'product_backend', 'security', 'cannot_determine'];

export async function renderCal(root, params, ctx) {
  const team = params.get('team') || 'data';
  const GATE = 0.05; /* ECE gate */

  root.innerHTML = `
  <div class="cal-wrap">
    <div class="team-tabs" role="tablist">
      ${TEAMS.map(t => `<button class="tab${t === team ? ' on' : ''}" data-team="${t}" role="tab">${t === 'all' ? '+ all' : t}</button>`).join('')}
    </div>
    <div id="cal-body">${skeletonRows(4)}</div>
  </div>`;

  const body = root.querySelector('#cal-body');
  root.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => {
    const p = new URLSearchParams({ team: b.dataset.team });
    if (Data.mode === 'mock') p.set('mock', '1');
    location.hash = routeHref('calibration', p);
  }));

  ctx.setScreenCode('CAL');
  ctx.setSrcBadge(null);

  try {
    const [calEnv, flipEnv] = await Promise.all([
      Data.getCalibration(team === 'all' ? undefined : team),
      Data.getFlips('7d').catch(() => null),
    ]);
    const c = calEnv.data;
    ctx.setSrcBadge(calEnv.meta?.data_source);
    ctx.setDsVersion(Data.datasetVersion);

    const n = c.n_labeled ?? c.n_decisions;
    const thin = n < 100;
    const covTau = '0.9';
    const cov = c.coverage?.[covTau];
    const covCount = cov != null ? Math.round(cov * n) : null;
    const flipRate = flipEnv?.data?.flip_rate ?? c.flip_rate;
    const flipsN = flipEnv?.data?.flips?.filter(f => f.flipped).length ?? c.flips_n;
    const reasked = flipRate ? Math.round((c.flips_n || flipsN || 0) / flipRate) : null;
    const bad = overconfidentBins(c.bins);

    body.innerHTML = `
    <div class="headline-cards${thin ? ' provisional' : ''}">
      <div class="hcard">
        <div class="hcard-k">Expected Calibration Error</div>
        <div class="hcard-v mono">${fmtConf(c.ece, 3)}${thin ? ' <span class="prov-tag">(provisional)</span>' : ''}</div>
        <div class="hcard-sub mono">weighted over 10 bins · n=${fmtInt(n)} · ${esc(c.window)} window</div>
        <p class="hcard-gloss">${esc(gloss80(c.bins))}</p>
        <div class="hcard-sub mono">95% CI [${c.ece_ci95?.map(v => v.toFixed(3)).join(', ') || '—'}] · gate ${GATE}</div>
      </div>
      <div class="hcard">
        <div class="hcard-k">Coverage at current threshold</div>
        <div class="hcard-v mono">${cov != null ? fmtPct(cov) : '—'}</div>
        <div class="hcard-sub mono">threshold ${covTau} · ${covCount != null ? fmtInt(covCount) + ' of ' + fmtInt(n) : '—'} decisions above gate</div>
        <p class="hcard-gloss">Share of evaluated decisions confident enough to clear the gate.</p>
      </div>
      <a class="hcard flip-card" href="${routeHref('audit', { flipped: 'true', team: team === 'all' ? '' : team, ...(Data.mode === 'mock' ? { mock: '1' } : {}) })}">
        <div class="hcard-k">Flip rate (7d)</div>
        <div class="hcard-v mono">${flipRate != null ? fmtPct(flipRate) : '—'}</div>
        <div class="hcard-sub mono">${flipsN != null && reasked != null ? `${fmtInt(flipsN)} of ${fmtInt(reasked)} inputs re-asked → different answer` : '—'}</div>
        <p class="hcard-gloss">Same input, asked twice, Jev changed its mind. Below 3% is our watch band; above it, we page the platform team, not you.</p>
        <div class="hcard-link mono">investigate flips →</div>
      </a>
    </div>
    <p class="verdict-line">${esc(calVerdict({ team: c.team, ece: c.ece, bins: c.bins }))}</p>
    <div class="cal-grid">
      <div class="cal-diagram">
        <h3>Reliability diagram <span class="mono" style="color:var(--tx-2)">· n=${fmtInt(n)}</span></h3>
        <canvas id="rel-canvas"></canvas>
      </div>
      <div class="cal-guide">
        <h3>How a non-ML SRE reads it</h3>
        <details open><summary>3 steps, 30 seconds</summary>
        <ol>
          <li>The diagonal is perfect honesty. Bars near it = the machine knows what it knows.</li>
          <li>Orange bars above your gate line = the dangerous kind of wrong: confident and incorrect. If you see those, tell us before you trust a suppression.</li>
          <li>The flip rate is the machine disagreeing with itself. Small is normal; growing is a platform problem, not your problem.</li>
        </ol></details>
        <div class="bin-table mono">
          ${c.bins.map(b => {
            const mid = (b.predicted_lo + b.predicted_hi) / 2;
            const cls = b.n < 30 ? 'thin' : Math.abs(mid - b.observed_rate) <= 0.05 ? 'ok' : mid > b.observed_rate ? 'over' : 'under';
            return `<div class="bin-row ${cls}"><span>${b.predicted_lo.toFixed(1)}–${b.predicted_hi.toFixed(1)}</span><span>n=${fmtInt(b.n)}</span><span>obs ${b.observed_rate.toFixed(3)}</span></div>`;
          }).join('')}
        </div>
      </div>
    </div>`;
    relDiagram(body.querySelector('#rel-canvas'), { bins: c.bins, threshold: 0.9, thresholdLabel: 'gate' });
    ctx.setStrip(stripCal({ ece: c.ece, n, thin, stale: null }));
  } catch (e) {
    if (e.code === 'no_shadow_data') {
      body.innerHTML = emptyBlock(e.message, 'the CAL screen is honest about thin evidence — it never markets');
    } else {
      body.innerHTML = errorBlock({
        what: `Couldn't reach the calibration API (GET /api/calibration → ${e.status || 'unreachable'}).`,
        detail: e.message || '', retryFn: () => renderCal(root, params, ctx),
      });
    }
    ctx.setStrip('Calibration unreachable — treat tonight\u2019s confidences as uncalibrated until this updates.');
  }
}
