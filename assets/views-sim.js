/* views-sim.js — Threshold simulator: "what would this policy have done last week?"
 * Reads: POST /api/simulate {thresholds, cost_model, dataset_version}.
 * Same math as the tuner — the projection panel reprices live on drag.
 * Law L5: the simulator NEVER writes policy. Commit = exported policy diff for review. */
import { Data } from './api.js';
import { skeletonRows, errorBlock, emptyBlock, esc, derivedMark } from './components.js';
import { freshnessBadge, freshnessState, FRESHNESS_BUDGETS } from './freshness.js';
import { stripSim, fmtInt, fmtPct, fmtConf, THRESHOLD_META, DEFAULT_THRESHOLDS,
         policyDiffYaml, branchName } from './lib.js';

const TEAMS = ['data', 'platform', 'network', 'product_backend', 'security', 'cannot_determine'];
const GRID = [0.75, 0.8, 0.85, 0.9, 0.95];

export async function renderSim(root, params, ctx) {
  const team = params.get('team') || 'data';
  let t = { ...DEFAULT_THRESHOLDS };
  let baseline = null, proj = null, prov = null, bins = null, datasetVersion = null;
  let cost = { c_fp: 100, c_fn: 50000 };
  let acked = false, curvePts = [];

  /* §4.4 with full force: the simulator is the one surface where EVERYTHING
   * is derived — so its labeling must be unmistakable. The banner is in-band,
   * always visible, and cannot be dismissed; every card carries the SIMULATED
   * mark; exports are watermarked. Simulator confusion (pre-mortem #2) is a
   * release-blocking defect, not a copy preference. */
  const SIM_BANNER = `<div class="sim-banner mono" role="note">
    <span class="sim-banner-tag">SIMULATION</span>
    Every number on this surface is a projection — none of it happened.
    Thresholds moved here changed nothing. Nothing is written by this screen.
  </div>`;

  root.innerHTML = `
  ${SIM_BANNER}
  <div class="sim-layout">
    <aside class="sim-controls">
      <div class="sim-team"><label class="mono">team</label>
        <select id="sim-team" class="mono">${TEAMS.map(x => `<option${x === team ? ' selected' : ''}>${x}</option>`).join('')}</select>
      </div>
      <p class="shadow-note mono">◈ shadow — projected on shadow evaluations · <span id="sim-ds">ds:—</span> · no live alerts affected.</p>
      ${Data.dataMode === 'static' ? `
      <div class="sim-presets mono" id="sim-presets">
        <span class="sim-presets-label">pre-computed scenarios</span>
        ${['default', 'conservative', 'aggressive'].map(s =>
          `<button class="btn preset" data-preset="${s}">${s}</button>`).join('')}
        <span class="sim-presets-note">baked at build time from the synthetic dataset — not a live tuner run</span>
      </div>` : ''}
      <div id="sim-sliders"></div>
      <div class="sim-actions">
        <button id="sim-export" class="btn primary" disabled>Review &amp; export</button>
      </div>
      <label class="ack mono" id="sim-ack" hidden><input type="checkbox" id="sim-ack-box">
        I have reviewed the false-suppress watch cases in the audit explorer.</label>
    </aside>
    <section class="sim-projection">
      <div id="sim-strip2" class="strip-inline mono"></div>
      <div id="sim-fresh" class="sim-freshline mono" style="margin-bottom:8px"></div>
      <div id="sim-cards" class="proj-cards">${skeletonRows(3)}</div>
      <div class="curve-wrap"><h3>Tradeoff curve ${derivedMark('simulated', 'each point is a projection with the suppress floor moved; nothing was paged or suppressed')} <span class="mono curve-sub">x = suppression rate · y = expected false suppresses</span></h3>
        <canvas id="sim-curve" width="520" height="300"></canvas></div>
      <div class="prov-block mono" id="sim-prov"></div>
    </section>
  </div>
  <div id="sim-diff" class="diff-modal" hidden></div>`;

  const slidersEl = root.querySelector('#sim-sliders');
  const cardsEl = root.querySelector('#sim-cards');
  const provEl = root.querySelector('#sim-prov');
  const exportBtn = root.querySelector('#sim-export');

  ctx.setScreenCode('SIM');
  ctx.setSrcBadge(null);

  /* slider per ThresholdSet knob; track = the team's confidence histogram */
  function paintSliders() {
    const maxN = Math.max(1, ...(bins || []).map(b => b.n));
    slidersEl.innerHTML = THRESHOLD_META.map(m => {
      const hist = (bins || []).map((b, i) =>
        `<rect x="${i * 10}" y="${14 - Math.max(1, (b.n / maxN) * 12)}" width="8.5" height="${Math.max(1, (b.n / maxN) * 12)}" fill="var(--line-1)" opacity="0.7"/>`).join('');
      const pct = ((t[m.key] - m.min) / (m.max - m.min)) * 100;
      return `<div class="slider-row" data-key="${m.key}">
        <div class="slider-label"><span>${esc(m.label)}</span><span class="mono slider-val">${m.fmt(t[m.key])}</span></div>
        <div class="slider-track"><svg viewBox="0 0 100 14" preserveAspectRatio="none" class="slider-hist">${hist}</svg>
        <input type="range" min="${m.min}" max="${m.max}" step="${m.step}" value="${t[m.key]}" aria-label="${esc(m.label)}"></div>
      </div>`;
    }).join('');
    slidersEl.querySelectorAll('.slider-row').forEach(row => {
      const input = row.querySelector('input');
      input.addEventListener('input', () => {
        t[row.dataset.key] = Number(input.value);
        row.querySelector('.slider-val').textContent = THRESHOLD_META.find(m => m.key === row.dataset.key).fmt(t[row.dataset.key]);
        scheduleSim();
      });
      input.addEventListener('keydown', (e) => {
        if (e.shiftKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
          e.preventDefault();
          const d = e.key === 'ArrowRight' ? 0.05 : -0.05;
          t[row.dataset.key] = Math.min(input.max, Math.max(input.min, t[row.dataset.key] + d));
          input.value = t[row.dataset.key];
          input.dispatchEvent(new Event('input'));
        }
      });
    });
  }

  let simTimer = null, simSeq = 0;
  ctx.registerCleanup(() => clearTimeout(simTimer));
  function scheduleSim() {
    clearTimeout(simTimer);
    simTimer = setTimeout(runSim, 150); /* debounced 150ms — reprices on input, not release */
  }

  async function runSim() {
    const seq = ++simSeq;
    cardsEl.classList.add('stale');
    try {
      const env = await Data.simulate({ thresholds: t, cost_model: cost, dataset_version: datasetVersion });
      if (seq !== simSeq) return;
      proj = env.data.projection; prov = env.data.provenance;
      ctx.setSrcBadge(env.meta?.data_source);
      ctx.setDsVersion('ds:' + prov.dataset_version);
      root.querySelector('#sim-ds').textContent = 'ds:' + prov.dataset_version;
      /* §4.1: the projection's freshness is the dataset's freshness — stated, budgeted */
      root.querySelector('#sim-fresh').innerHTML = freshnessBadge({
        state: freshnessState({ sourceUp: true, asOfMs: prov.computed_at ? new Date(prov.computed_at).getTime() : null, budgetMs: FRESHNESS_BUDGETS.simulator }),
        asOfIso: prov.computed_at, waitingOn: null, budgetMs: FRESHNESS_BUDGETS.simulator,
      }) + ' <span style="color:var(--tx-2)">projection dataset</span>';
      paintCards(); paintProv(); paintStrip(); drawCurve();
    } catch (e) {
      if (seq !== simSeq) return;
      cardsEl.classList.remove('stale');
      cardsEl.innerHTML = errorBlock({
        what: `Simulation failed (POST /api/simulate → ${e.status || 'unreachable'}). Your thresholds are unchanged — nothing was projected.`,
        detail: e.message || '', retryFn: runSim,
      });
    }
  }

  function deltaLine(cur, base) {
    const d = cur - base;
    const cls = d === 0 ? '' : d > 0 ? 'up' : 'down';
    return `<span class="delta ${cls}">${d > 0 ? '+' : ''}${fmtInt(d)}</span>`;
  }

  function paintCards() {
    cardsEl.classList.remove('stale');
    const b = baseline || proj;
    const falseDelta = proj.exp_false_suppresses - (b.exp_false_suppresses ?? 0);
    const danger = falseDelta > 0.05;
    cardsEl.innerHTML = `
      <div class="pcard"><div class="pcard-k">Pages <span class="would">would have paged</span> ${derivedMark('simulated', 'projection over the 7d shadow window with your thresholds')}</div>
        <div class="pcard-v mono">${fmtInt(b.page_now)} → <b>${fmtInt(proj.page_now)}</b> ${deltaLine(proj.page_now, b.page_now)}</div>
        <div class="pcard-sub mono">of ${fmtInt(proj.n_alerts)} evaluated · ${fmtInt(proj.queue)} queued biz-hrs · ${fmtInt(proj.baseline)} unchanged</div></div>
      <div class="pcard"><div class="pcard-k">Suppressions <span class="would">would have stood down</span> ${derivedMark('simulated', 'projection over the 7d shadow window with your thresholds')}</div>
        <div class="pcard-v mono">${fmtInt(b.suppress)} → <b>${fmtInt(proj.suppress)}</b> ${deltaLine(proj.suppress, b.suppress)}</div>
        <div class="pcard-sub mono">suppression rate ${fmtPct(proj.suppress_rate)} · avoided page cost $${fmtInt(proj.avoided_page_cost)}</div></div>
      <div class="pcard${danger ? ' danger' : ''}"><div class="pcard-k">False-suppress watch <span class="would">the conscience</span> ${derivedMark('simulated', 'Σ P(p1) over the would-be-suppressed set — a projection, not a count of real mistakes')}</div>
        <div class="pcard-v mono">${(b.exp_false_suppresses ?? 0).toFixed(1)} → <b>${proj.exp_false_suppresses.toFixed(1)}</b> ${deltaLine(Math.round(proj.exp_false_suppresses * 10) / 10, Math.round((b.exp_false_suppresses ?? 0) * 10) / 10)}</div>
        <div class="pcard-sub mono">Σ P(p1) over suppressed · expected cost $${fmtInt(proj.expected_cost)}</div>
        ${danger ? '<div class="pcard-warn mono">export locked — review each case in the audit explorer first</div>' : ''}</div>`;
    const locked = danger && !acked;
    exportBtn.disabled = locked;
    root.querySelector('#sim-ack').hidden = !danger;
    if (danger) {
      const box = root.querySelector('#sim-ack-box');
      box.checked = acked;
      box.onchange = () => { acked = box.checked; exportBtn.disabled = !(acked); };
    }
  }

  function paintProv() {
    /* M1 honesty: computed_at is stripped from pre-rendered JSON for build
     * determinism, so "evaluated <ts>" would render "undefined" — and
     * "replayed with your thresholds" is false for baked scenario presets
     * (they ran with the scenario's fixed thresholds, not your sliders).
     * Three accurate states: live tuner run, pre-computed preset,
     * mock-mode local recompute (sliders repriced client-side). */
    const pre = !!prov.precomputed;
    provEl.innerHTML = `projection math<br>` +
      `&nbsp;&nbsp;dataset&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; ds:${esc(prov.dataset_version)} (${fmtInt(prov.n_alerts)} decisions · 7d window)<br>` +
      `&nbsp;&nbsp;tuner&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; ${esc(prov.tuner_rev)}<br>` +
      `&nbsp;&nbsp;policy&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; ${esc(prov.policy_version)} · sha ${esc(String(prov.dataset_sha256).slice(0, 12))}…<br>` +
      (pre
        ? `&nbsp;&nbsp;evaluated&nbsp;&nbsp;&nbsp; pre-computed at build time — not a live tuner run${prov.scenario ? ` · scenario "${esc(prov.scenario)}"` : ''}<br>` +
          `&nbsp;&nbsp;note&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; ${esc(prov.note || 'scenario thresholds are baked in; sliders reprice locally from the baked dataset')}<br>`
        : `&nbsp;&nbsp;evaluated&nbsp;&nbsp;&nbsp; ${esc(prov.computed_at || 'live')} — replayed with your thresholds<br>` +
          `&nbsp;&nbsp;reproduce&nbsp;&nbsp;&nbsp; POST /api/simulate with this payload → identical projection<br>`) +
      `&nbsp;&nbsp;<button class="btn" id="sim-copy">copy payload</button>`;
    root.querySelector('#sim-copy').addEventListener('click', () => {
      const payload = JSON.stringify({ thresholds: t, cost_model: cost, dataset_version: datasetVersion }, null, 2);
      try { navigator.clipboard.writeText(payload); } catch {}
    });
  }

  function paintStrip() {
    const thin = (prov?.n_alerts ?? 0) < 100;
    ctx.setStrip(stripSim({ pageNow: proj.page_now, thin, n: prov?.n_alerts ?? 0 }));
    root.querySelector('#sim-strip2').textContent = thin
      ? `Cannot project — ${fmtInt(prov.n_alerts)} cases is below 100. Projections on this little data would be numerology, not math.`
      : '';
  }

  function drawCurve() {
    const cv = root.querySelector('#sim-curve');
    const ctx2d = cv.getContext('2d');
    const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    const W = 520, H = 300, pad = 40;
    ctx2d.clearRect(0, 0, W, H);
    if (!curvePts.length) return;
    const xs = curvePts.map(p => p.suppress_rate), ys = curvePts.map(p => p.exp_false_suppresses);
    const x0 = Math.min(...xs) * 0.98, x1 = Math.max(...xs) * 1.02 || 1;
    const y1 = Math.max(...ys, 0.1) * 1.15;
    const X = v => pad + (v - x0) / (x1 - x0) * (W - pad - 10);
    const Y = v => H - pad - (v / y1) * (H - pad - 20);
    ctx2d.strokeStyle = css('--line-0');
    ctx2d.strokeRect(pad, 10, W - pad - 10, H - pad - 10);
    ctx2d.strokeStyle = css('--sev-4'); ctx2d.lineWidth = 1.5;
    ctx2d.beginPath();
    [...curvePts].sort((a, b) => a.suppress_rate - b.suppress_rate).forEach((p, i) => {
      i ? ctx2d.lineTo(X(p.suppress_rate), Y(p.exp_false_suppresses)) : ctx2d.moveTo(X(p.suppress_rate), Y(p.exp_false_suppresses));
    });
    ctx2d.stroke();
    const dot = (p, color, r) => { ctx2d.fillStyle = color; ctx2d.beginPath(); ctx2d.arc(X(p.suppress_rate), Y(p.exp_false_suppresses), r, 0, 7); ctx2d.fill(); };
    if (baseline) dot(baseline, css('--tx-2'), 4);
    dot(proj, css('--disp-page'), 5);
    if (baseline) {
      ctx2d.strokeStyle = css('--tx-2'); ctx2d.setLineDash([3, 3]);
      ctx2d.beginPath(); ctx2d.moveTo(X(baseline.suppress_rate), Y(baseline.exp_false_suppresses));
      ctx2d.lineTo(X(proj.suppress_rate), Y(proj.exp_false_suppresses)); ctx2d.stroke(); ctx2d.setLineDash([]);
    }
    ctx2d.fillStyle = css('--tx-2'); ctx2d.font = '10px monospace';
    ctx2d.fillText('operating point', X(baseline.suppress_rate) + 8, Y(baseline.exp_false_suppresses) - 6);
    ctx2d.fillStyle = css('--disp-page');
    ctx2d.fillText('dragged', X(proj.suppress_rate) + 8, Y(proj.suppress_rate) + 12);
  }

  async function traceCurve() {
    /* background grid over suppress_conf_min — the tuner's confidence grid */
    for (const g of GRID) {
      try {
        const env = await Data.simulate({ thresholds: { ...t, suppress_conf_min: g }, cost_model: cost, dataset_version: datasetVersion });
        if (!curvePts.some(p => p.conf === g)) curvePts.push({ ...env.data.projection, conf: g });
        drawCurve();
      } catch { /* a failed grid point is a gap in the curve, not a failure */ }
    }
  }

  /* export: policy diff, never a write */
  exportBtn.addEventListener('click', () => {
    const modal = root.querySelector('#sim-diff');
    const yaml = policyDiffYaml({ team, oldT: DEFAULT_THRESHOLDS, newT: t, projection: proj, provenance: prov, datasetVersion: prov.dataset_version });
    modal.innerHTML = `<div class="diff-card">
      <h3>Policy diff — review, then commit in git ${derivedMark('simulated', 'the projection in this diff is simulated, not measured')}</h3>
      <p class="drawer-note">The simulator never writes policy. Applying this is a human code-review decision, with the projection above as the attached evidence.</p>
      <pre class="mono">${esc('# SIMULATED PROJECTION — not a historical measurement\n' + yaml)}</pre>
      <div class="prov-actions">
        <button class="btn" id="diff-copy">copy diff</button>
        <button class="btn" id="diff-branch">copy branch name</button>
        <button class="btn" id="diff-close">close</button>
      </div>
      <p class="mono" style="color:var(--tx-2)">suggested branch: <b>${esc(branchName(team))}</b></p>
    </div>`;
    modal.hidden = false;
    modal.querySelector('#diff-copy').addEventListener('click', () => { try { navigator.clipboard.writeText(yaml); } catch {} });
    modal.querySelector('#diff-branch').addEventListener('click', () => { try { navigator.clipboard.writeText(branchName(team)); } catch {} });
    modal.querySelector('#diff-close').addEventListener('click', () => { modal.hidden = true; });
  });

  root.querySelector('#sim-team').addEventListener('change', (e) => {
    const p = new URLSearchParams({ team: e.target.value });
    if (Data.mode === 'mock') p.set('mock', '1');
    location.hash = '#/simulator?' + p.toString();
  });

  /* boot: calibration for bins + dataset pin, then baseline + live projection */
  try {
    const cal = await Data.getCalibration(team);
    bins = cal.data.bins; datasetVersion = cal.data.dataset_version;
    ctx.setSrcBadge(cal.meta?.data_source);
  } catch (e) {
    root.querySelector('.sim-projection').innerHTML = emptyBlock(
      `No shadow evaluations for team=${team} yet. Run a guided storm (15 min) or wait for the nightly shadow join.`, '');
    ctx.setStrip(stripSim({ pageNow: 0, thin: true, n: 0 }));
    return;
  }
  paintSliders();
  /* static showcase: pre-computed scenario presets. Loading a preset pulls the
   * baked projection AND sets the sliders to the scenario's thresholds, so the
   * local-recompute path below stays consistent with what's displayed. */
  const presetsEl = root.querySelector('#sim-presets');
  if (presetsEl) {
    presetsEl.querySelectorAll('[data-preset]').forEach(btn => btn.addEventListener('click', async () => {
      const name = btn.dataset.preset;
      try {
        const env = await Data.simulate(
          { thresholds: t, cost_model: cost, dataset_version: datasetVersion },
          { scenario: name });
        if (env.data?.scenario_thresholds) {
          t = { ...env.data.scenario_thresholds };
          paintSliders();
        }
        /* render the pre-computed projection through the normal paint path —
         * proj/prov are the closure state paintCards/paintProv/paintStrip read */
        proj = env.data.projection; prov = env.data.provenance;
        ctx.setSrcBadge(env.meta?.data_source);
        ctx.setDsVersion('ds:' + prov.dataset_version);
        root.querySelector('#sim-ds').textContent = 'ds:' + prov.dataset_version;
        paintCards(); paintProv(); paintStrip();
      } catch (e) {
        cardsEl.innerHTML = errorBlock({
          what: `Couldn't load the pre-computed scenario "${esc(name)}".`,
          detail: e.message || '', retryFn: () => renderSim(root, params, ctx),
        });
      }
    }));
  }
  try {
    const benv = await Data.simulate({ thresholds: DEFAULT_THRESHOLDS, cost_model: cost, dataset_version: datasetVersion });
    baseline = benv.data.projection;
    traceCurve(); /* background */
    await runSim();
  } catch (e) {
    cardsEl.innerHTML = errorBlock({
      what: `Simulation failed (POST /api/simulate → ${e.status || 'unreachable'}). Your thresholds are unchanged — nothing was projected.`,
      detail: e.message || '', retryFn: () => renderSim(root, params, ctx),
    });
  }
}
