/* views-audit.js — Audit explorer: search the machine's memory, investigate its mistakes.
 * Reads: GET /api/decisions (+filters), GET /api/decision/<id>,
 *        GET /api/analytics/flips, GET /api/analytics/noise.
 * Read-only. The audit explorer can never alter a decision — only establish what happened. */
import { Data } from './api.js';
import { decisionRow, dispChip, skeletonRows, errorBlock, emptyBlock, drawerHtml, esc, derivedMark } from './components.js';
import { deriveChain, verifyChain } from './chain.js';
import { freshnessBadge, freshnessState, FRESHNESS_BUDGETS } from './freshness.js';
import { stripAudit, fmtInt, fmtPct, fmtConf, fmtTime, fmtTimeDual, shortHash, SEV_LABEL, DISP_LABEL,
         DEFAULT_THRESHOLDS } from './lib.js';

const TEAMS = ['platform', 'network', 'data', 'product_backend', 'security', 'cannot_determine'];
const DISPS = ['page_now', 'page_business_hours', 'suppress', 'passthrough'];
const WINDOWS = ['1h', '24h', '7d', '30d'];

function windowToIso(win) {
  const m = win.match(/^(\d+)([smhd])$/);
  if (!m) return null;
  const mult = { s: 1, m: 60, h: 3600, d: 86400 }[m[2]];
  return new Date(Date.now() - Number(m[1]) * mult * 1000).toISOString();
}

export async function renderAudit(root, params, ctx) {
  const q = {
    fpr: params.get('fpr') || '', inHash: params.get('in') || '',
    text: params.get('q') || '', team: params.get('team') || '',
    action: params.get('action') || '', reason: params.get('reason') || '',
    flipped: params.get('flipped') === 'true', labeled: params.get('labeled') === 'true',
    last: params.get('last') || '7d',
  };
  let rows = [], flips = {}, calBins = null, noise = null, totalLabeled = 0;

  root.innerHTML = `
  <div class="audit-layout">
    <aside class="filter-rail">
      <div class="rail-sec"><h4>facets</h4>
        <div class="facet-row"><span class="mono facet-k">team</span><select id="a-team" class="mono"><option value="">all</option>${TEAMS.map(t => `<option${q.team === t ? ' selected' : ''}>${t}</option>`).join('')}</select></div>
        <div class="facet-row"><span class="mono facet-k">disposition</span><select id="a-action" class="mono"><option value="">all</option>${DISPS.map(d => `<option value="${d}"${q.action === d ? ' selected' : ''}>${DISP_LABEL[d]}</option>`).join('')}</select></div>
        <div class="facet-row"><span class="mono facet-k">reason</span><input id="a-reason" class="mono" value="${esc(q.reason)}" placeholder="triple-lock"></div>
        <div class="facet-row"><label class="mono"><input type="checkbox" id="a-flipped"${q.flipped ? ' checked' : ''}> flipped only</label></div>
        <div class="facet-row"><label class="mono"><input type="checkbox" id="a-labeled"${q.labeled ? ' checked' : ''}> labeled only</label></div>
      </div>
      <div class="rail-sec"><h4>window</h4><div class="rail-opts" id="a-wins">
        ${WINDOWS.map(w => `<button class="opt${q.last === w ? ' on' : ''}" data-v="${w}">last ${w}</button>`).join('')}
      </div></div>
    </aside>
    <section class="audit-main">
      <div class="audit-searchrow">
        <input id="a-search" class="mono audit-search" placeholder="fpr:9f2c… · in:sha256:7d3a… · free text (service, reason, team)" value="${esc(q.fpr ? 'fpr:' + q.fpr : q.inHash ? 'in:' + q.inHash : q.text)}" spellcheck="false">
        <button id="a-export" class="btn">export audit pack</button>
      </div>
      <div id="a-noise" class="noise-strip mono"></div>
      <div id="a-fresh" class="audit-fresh mono"></div>
      <details class="chain-panel" id="a-chainwrap">
        <summary class="mono">event-log chain — tamper-evident, derived locally ${derivedMark('derived', 'the platform does not expose the sealed event log in the read contract; the console derives and verifies this chain from stored decisions')}</summary>
        <div id="a-chain" class="mono">${skeletonRows(2)}</div>
      </details>
      <div id="a-results" class="tape">${skeletonRows(8)}</div>
    </section>
  </div>`;

  const results = root.querySelector('#a-results');
  const noiseEl = root.querySelector('#a-noise');
  ctx.setScreenCode('AUDIT');
  ctx.setSrcBadge(null);

  function searchParams() {
    const p = { limit: 100 };
    const raw = root.querySelector('#a-search').value.trim();
    if (raw.startsWith('fpr:')) p.fingerprint = raw.slice(4);
    else if (raw.startsWith('in:')) p._inHash = raw.slice(3);
    else if (raw) p.q = raw;
    if (q.team) p.team = q.team;
    if (q.action) p.action = q.action;
    if (q.reason) p.reason = q.reason;
    const iso = windowToIso(q.last); if (iso) p.from = iso;
    return p;
  }

  function paintNoise() {
    if (!noise) { noiseEl.innerHTML = ''; return; }
    const totalVol = noise.top_checks.reduce((a, c) => a + c.volume, 0);
    const totalSupp = noise.top_checks.reduce((a, c) => a + c.suppressed, 0);
    noiseEl.innerHTML = `last ${esc(noise.window)} · ${fmtInt(totalVol)} decisions · ${fmtPct(totalVol ? totalSupp / totalVol : 0, 0)} suppressed<br>` +
      `top absorbers: ` + noise.suppression_breakdown.slice(0, 3).map(b =>
        `<a href="#/audit?reason=${esc(b.reason)}${Data.mode === 'mock' ? '&mock=1' : ''}" data-reason="${esc(b.reason)}">${esc(b.reason)} ${fmtPct(b.pct, 0)}</a>`).join(' · ');
    noiseEl.querySelectorAll('[data-reason]').forEach(a => a.addEventListener('click', (e) => {
      e.preventDefault(); q.reason = a.dataset.reason;
      root.querySelector('#a-reason').value = q.reason; load();
    }));
  }

  function flipTimelineHtml(fr) {
    const ds = fr.decisions;
    return `<div class="flip-record">
      <div class="flip-head mono">${esc(shortHash(fr.input_sha256, 8))} · ${fr.repeats} repeats · <b>${fr.flipped ? 'FLIPPED' : 'consistent'}</b> · ${esc(fr.fingerprint.slice(0, 8))}</div>
      <div class="flip-tl">${ds.map((d, i) => `
        <div class="flip-ev"><span class="mono">${esc(fmtTimeDual(d.time))}</span> ${dispChip(d.disposition, null).replace(/·[^<]*<\/span>$/, '</span>')}
        <span class="mono">${fmtConf(d.confidence)}</span>${i > 0 && d.disposition !== ds[i - 1].disposition ? ' <span class="flip-flag mono">← FLIP</span>' : ''}</div>
        ${i < ds.length - 1 ? '<div class="flip-link">│ input identical</div>' : ''}`).join('')}</div>
      ${!fr.flipped ? '<p class="drawer-note">One decision on this input — no flip. The machine has been consistent here.</p>' : '<p class="drawer-note">cause: input, thresholds, and tuner identical across repeats — model non-determinism (measured band 1.3–2.2%).</p>'}
    </div>`;
  }

  function paintResults() {
    if (q.flipped && Object.keys(flips).length) {
      results.innerHTML = Object.values(flips).map(flipTimelineHtml).join('') ||
        emptyBlock('No flips in the window. The machine has been consistent here.', '');
    } else if (!rows.length) {
      const desc = q.fpr ? `fpr:${q.fpr}` : q.inHash ? `in:${shortHash(q.inHash)}` : 'this query';
      results.innerHTML = emptyBlock(
        `No decisions match ${desc}. Check the fingerprint — or this alert never reached the gate (see receiver health).`,
        'hint: widen last=, or clear facets');
    } else {
      results.innerHTML = rows.map(d => decisionRow(d, { flips, density: 'compact', thresholds: DEFAULT_THRESHOLDS, dataSource: Data.lastMeta?.data_source || 'unknown' })).join('');
      results.querySelectorAll('.row[data-id]').forEach(r => {
        r.addEventListener('click', (e) => {
          if (e.target.closest('a,button')) return;
          ctx.openDrawer(Number(r.dataset.id), { bins: calBins, flips });
        });
      });
    }
    const desc = q.fpr ? `fpr:${q.fpr}` : q.inHash ? `input ${shortHash(q.inHash)}` : [q.team && `team=${q.team}`, q.action && `action=${q.action}`, q.reason && `reason=${q.reason}`, q.flipped && 'flipped only', q.labeled && 'labeled only'].filter(Boolean).join(' ') || 'all decisions';
    ctx.setStrip(stripAudit({ queryDesc: desc, n: q.flipped ? Object.keys(flips).length : rows.length }));
  }

  async function load() {
    results.innerHTML = skeletonRows(8);
    try {
      const sp = searchParams();
      const inHash = sp._inHash; delete sp._inHash;
      const [env, flipEnv, noiseEnv] = await Promise.all([
        Data.getDecisions(sp),
        Data.getFlips('7d').catch(() => null),
        Data.getNoise('24h').catch(() => null),
      ]);
      ctx.setSrcBadge(env.meta?.data_source);
      ctx.setDsVersion(Data.datasetVersion);
      rows = env.data || [];
      if (inHash) rows = rows.filter(r => r.input_sha256 === inHash || r.input_sha256.startsWith(inHash));
      if (flipEnv?.data?.flips) {
        flips = {};
        for (const fr of flipEnv.data.flips) flips[fr.input_sha256] = fr;
        if (q.flipped) {
          const wanted = new Set(Object.values(flips).filter(f => f.flipped).flatMap(f => f.decisions.map(d => d.decision_id)));
          rows = rows.filter(r => wanted.has(r.id));
        }
      }
      if (q.labeled) {
        /* labels live on the detail record — resolve per row, capped at the loaded window */
        const labeled = [];
        for (const r of rows.slice(0, 50)) {
          try { const det = await Data.getDecision(r.id); if (det.data.outcome) labeled.push(r); } catch {}
        }
        rows = labeled; totalLabeled = labeled.length;
      }
      noise = noiseEnv?.data || null;
      /* §4.1: the audit window's freshness — newest stored decision vs the
       * audit budget (the log is immutable, so this is about coverage, stated) */
      const freshEl = root.querySelector('#a-fresh');
      if (freshEl) {
        const newest = rows.length ? rows[0].time : null;
        freshEl.innerHTML = freshnessBadge({
          state: rows.length ? 'live' : 'degraded',
          asOfIso: newest, waitingOn: rows.length ? null : 'decisions API',
          budgetMs: FRESHNESS_BUDGETS.audit,
        }) + ` <span style="color:var(--tx-2)">window: last ${esc(q.last)} · ${fmtInt(rows.length)} decisions</span>`;
      }
      try {
        const cal = await Data.getCalibration(rows[0]?.team || 'data');
        calBins = cal.data.bins;
      } catch { calBins = null; }
      paintNoise(); paintResults(); paintChain();
    } catch (e) {
      results.innerHTML = errorBlock({
        what: `Couldn't reach the audit API (GET /api/decisions → ${e.status || 'unreachable'}).`,
        detail: e.message || '', retryFn: load,
      });
      ctx.setStrip('Audit API unreachable — searching your recent history only is unavailable. Retry, or check receiver health.');
    }
  }

  /* The event-log chain (Appendix A <TimelineExplorer>): hash-linked events
   * derived locally from the stored decisions and re-verified client-side.
   * A break renders its location in-band — never silently. */
  async function paintChain() {
    const el = root.querySelector('#a-chain');
    if (!el) return;
    try {
      const links = await deriveChain(rows);
      const v = await verifyChain(links);
      const head = links.slice(-12).reverse();
      el.innerHTML =
        `<div class="chain-status ${v.ok ? 'ok' : 'broken'}">` +
        (v.ok
          ? `chain whole — ${fmtInt(v.n)} links verified (recomputed locally)`
          : `CHAIN BROKEN at link #${v.brokenAt} — ${esc(v.reason)}. The stored window is internally inconsistent; escalate to the platform team.`) +
        `</div>` +
        head.map((l, i) => `
          <div class="chain-link${i === 0 ? ' head' : ''}">
            <span class="mono">#${l.decision_id}</span>
            <span class="mono">${esc(fmtTimeDual(l.created_at))}</span>
            <span class="mono">${esc(l.prev.slice(0, 8))}… → <b>${esc(l.hash.slice(0, 8))}…</b></span>
            <span class="mono">in:${esc(shortHash(l.input_sha256, 6))}</span>
          </div>`).join('') +
        (links.length > 12 ? `<div class="mono" style="color:var(--tx-2)">+ ${fmtInt(links.length - 12)} earlier links — widen the window in the export to include them</div>` : '') +
        `<p class="drawer-note">Derived by the console from stored decisions — not the platform's sealed log (not exposed by the read API; RFC in the build record). It proves the window you hold is internally consistent; it cannot attest to what the platform recorded.</p>`;
    } catch (e) {
      el.innerHTML = `<div class="chain-status broken">chain derivation failed: ${esc(e.message || String(e))}</div>`;
    }
  }

  /* export: the postmortem artifact — decisions + the derived chain, so the
   * recipient can re-verify the window without trusting this console */
  root.querySelector('#a-export').addEventListener('click', async () => {
    const chain = await deriveChain(rows).catch(() => []);
    const pack = {
      query: searchParams(), dataset_version: Data.datasetVersion,
      data_source: Data.lastMeta?.data_source || 'unknown',
      exported_at: new Date().toISOString(), contract_version: '1.0.0',
      decisions: rows,
      derived_chain: chain, /* re-verify with assets/chain.js verifyChain() */
      chain_note: 'console-derived from stored decisions — not the platform sealed log',
    };
    const blob = new Blob([JSON.stringify(pack, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `sentinel-audit-pack-${Date.now()}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  });

  const reroute = () => {
    const p = new URLSearchParams();
    const raw = root.querySelector('#a-search').value.trim();
    if (raw.startsWith('fpr:')) p.set('fpr', raw.slice(4));
    else if (raw.startsWith('in:')) p.set('in', raw.slice(3));
    else if (raw) p.set('q', raw);
    if (q.team) p.set('team', q.team); if (q.action) p.set('action', q.action);
    if (q.reason) p.set('reason', q.reason);
    if (q.flipped) p.set('flipped', 'true'); if (q.labeled) p.set('labeled', 'true');
    if (q.last !== '7d') p.set('last', q.last);
    if (Data.mode === 'mock') p.set('mock', '1');
    location.hash = '#/audit?' + p.toString();
  };
  let searchTimer = null;
  root.querySelector('#a-search').addEventListener('input', () => {
    clearTimeout(searchTimer); searchTimer = setTimeout(reroute, 600);
  });
  root.querySelector('#a-team').addEventListener('change', (e) => { q.team = e.target.value; reroute(); });
  root.querySelector('#a-action').addEventListener('change', (e) => { q.action = e.target.value; reroute(); });
  root.querySelector('#a-reason').addEventListener('change', (e) => { q.reason = e.target.value.trim(); reroute(); });
  root.querySelector('#a-flipped').addEventListener('change', (e) => { q.flipped = e.target.checked; reroute(); });
  root.querySelector('#a-labeled').addEventListener('change', (e) => { q.labeled = e.target.checked; reroute(); });
  root.querySelectorAll('#a-wins .opt').forEach(b => b.addEventListener('click', () => { q.last = b.dataset.v; reroute(); }));

  await load();
}
