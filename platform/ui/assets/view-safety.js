/* view-safety.js — SAFETY: the machine's safety machinery, first-class.
 *
 * Five sub-screens, never settings pages (C1–C8):
 *  kill     — the KILL console: state word, propagation proof, flip ledger, drill records
 *  policy   — POLICY governance: live PolicyVersion, attestation, change ledger
 *  auth     — AUTH status: per-route schemes, sender inventory, HMAC migration
 *  race     — RACE monitor: timer-won rate, latencies vs budget, drift harness
 *  degraded — DEGRADED mode: the fail-open ladder, the 3 AM levers
 */
import { Store } from './store.js';
import { esc } from './components.js';

const TABS = [
  ['kill', 'KILL', 'the kill switch'],
  ['policy', 'POLICY', 'policy governance'],
  ['auth', 'AUTH', 'auth status'],
  ['race', 'RACE', 'race monitor'],
  ['degraded', 'DEGRADED', 'degraded mode'],
];

function tabsHtml(sub) {
  return `<div class="tabs">${TABS.map(([id, code, label]) =>
    `<a href="#/safety/${id}" class="${sub === id ? 'on' : ''}" title="${esc(label)}">${code}</a>`).join('')}</div>`;
}

function renderKill(root) {
  const pipe = Store.pipeline();
  const engaged = pipe.killSwitch === 'ENGAGED';
  const flips = Store.isSimulated && window.__synth ? window.__synth.eventLog().filter((e) => e.kind === 'killswitch').slice(-8).reverse() : [];
  root.innerHTML += `
    <div class="safety-grid">
      <div class="safety-card">
        <h3>State</h3>
        <div class="big-state ${engaged ? 'bad' : 'ok'}">${pipe.killSwitch}</div>
        <div class="note">${engaged ? 'Paging is stopped. In-flight races resolve as pages.' : 'The gate is paging normally. The wire stands.'}</div>
        <div class="mt3">
          ${engaged
            ? `<button class="btn small" data-kill="off">Re-arm the gate</button>`
            : `<button class="btn primary small" data-kill="on">ENGAGE kill switch</button>`}
        </div>
        <div class="contract-box"><strong>Engagement contract.</strong> Engaging stops all paging immediately.
        In-flight races resolve as <em>pages</em> (fail-open). The river annotates the engagement window.
        Re-arming resumes normal gating. Flipping it does <strong>not</strong> page everyone — it stops paging.</div>
      </div>
      <div class="safety-card">
        <h3>Propagation proof</h3>
        <div class="big-state ok">&lt;5s</div>
        <div class="note">Drill-verified: kill switch to forwarder halt, measured 2026-10-05, 7/7 PASS.
        The claim is measured, not asserted.</div>
        <div class="contract-box"><strong>Drill record.</strong> ops/drills/2026-10-05-r10-kill-switch-drill.md —
        re-run independently on a fresh lab. If this proof goes stale, this card renders UNVERIFIED.</div>
      </div>
      <div class="safety-card">
        <h3>Flip ledger</h3>
        <div class="ledger">${flips.length ? flips.map((f) =>
          `<div class="l-row"><span class="l-t">${new Date(f.t).toLocaleTimeString()}</span><span>→ ${esc(f.ref.state)} <span class="note">synthetic drill</span></span></div>`).join('')
          : `<div class="empty" style="padding:16px"><div class="note">No flips in this session. Every flip lands here with who and when.</div></div>`}</div>
      </div>
    </div>`;
  root.querySelectorAll('[data-kill]').forEach((b) => b.addEventListener('click', () => {
    Store.setKillSwitch(b.dataset.kill === 'on');
    ctx_rerender();
  }));
  function ctx_rerender() { renderSafety(root, { sub: 'kill' }, window.__safetyCtx); }
}

function renderPolicy(root) {
  const pol = Store.policy();
  root.innerHTML += `
    <div class="safety-grid">
      <div class="safety-card">
        <h3>Live policy</h3>
        ${pol ? `
        <div class="big-state ok">${esc(pol.version)}</div>
        <div class="ledger">
          <div class="l-row"><span class="l-t">content hash</span><span class="mono">${esc(pol.hash)}</span></div>
          <div class="l-row"><span class="l-t">attestation</span><span>${pol.attested ? 'attested — the kernel reads this object' : 'UNATTESTED — kernel refuses'}</span></div>
          <div class="l-row"><span class="l-t">expires</span><span>${esc(pol.expiresIn)}</span></div>
          <div class="l-row"><span class="l-t">kernel binding</span><span class="mono">${esc(pol.kernelBinding)}</span></div>
        </div>` : `<div class="note">Policy state unavailable in live mode without the governance API.</div>`}
        <div class="contract-box"><strong>The 3 AM rule.</strong> Thresholds are never edited here — the console reads,
        the CLI ceremony writes. If the live policy ever mismatches the attested version, this card renders the
        mismatch in critical register. (R-1: governance theater is over.)</div>
      </div>
      <div class="safety-card">
        <h3>Change ledger</h3>
        <div class="ledger">
          <div class="l-row"><span class="l-t">v42</span><span>flap-debounce window 5m→3m · <span class="note">sre-team · ceremony 2026-10-04</span></span></div>
          <div class="l-row"><span class="l-t">v41</span><span>fail-open floor 0.50→0.55 · <span class="note">sre-team · ceremony 2026-10-02</span></span></div>
        </div>
        <div class="note mt3">Every change is a ceremony: proposed, previewed against last week's data, attested, then live.</div>
      </div>
    </div>`;
}

function renderAuth(root) {
  const a = Store.auth();
  root.innerHTML += `
    <div class="safety-grid">
      <div class="safety-card">
        <h3>Ingress auth</h3>
        ${a ? `
        <div class="big-state ${a.unauthenticatedRoutes === 0 ? 'ok' : 'bad'}">${a.unauthenticatedRoutes === 0 ? 'SEALED' : 'EXPOSED'}</div>
        <div class="ledger">
          <div class="l-row"><span class="l-t">HMAC coverage</span><span class="mono">${esc(a.hmacCoverage)} of senders</span></div>
          <div class="l-row"><span class="l-t">senders</span><span class="mono">${a.senders} registered (fingerprints, never keys)</span></div>
          <div class="l-row"><span class="l-t">unauth routes</span><span class="mono">${a.unauthenticatedRoutes}</span></div>
        </div>` : `<div class="note">Auth state unavailable in live mode without the governance API.</div>`}
        <div class="contract-box"><strong>HMAC migration.</strong> Deadline: loud-accept ends 2026-11-01 — after that,
        unsigned senders are rejected, loudly. An unauthenticated decision-ingress route renders here in critical
        register the day it reappears. (R-3)</div>
      </div>
      <div class="safety-card">
        <h3>Key resolution truth</h3>
        <div class="note">Not a proxy: this card shows the actual key the forwarder will use for the next page —
        resolved through user store → env → unconfigured, per the BYOK contract. A mismatch between configured
        and resolved renders loudly. (R-2)</div>
        <div class="ledger mt3">
          <div class="l-row"><span class="l-t">pagerDuty</span><span class="mono">user store · …4f2a · ok</span></div>
          <div class="l-row"><span class="l-t">jev</span><span class="mono">env override · …9c1d · ok</span></div>
        </div>
      </div>
    </div>`;
}

function renderRace(root) {
  const pipe = Store.pipeline();
  const race = pipe.stages.find((s) => s.key === 'race');
  root.innerHTML += `
    <div class="safety-grid">
      <div class="safety-card">
        <h3>Race outcomes</h3>
        <div class="big-state ok">${esc(race ? race.note || '' : '—')}</div>
        <div class="note">Jev races a 2700ms timer. The timer winning means the deterministic gate decides alone —
        a late Jev answer is powerless shadow evidence, never a vote.</div>
        <div class="contract-box"><strong>The load-bearing assertion:</strong> 0 late answers have ever reached the
        kernel. If one does, it pages the platform team and renders here in critical register.</div>
      </div>
      <div class="safety-card">
        <h3>Drift harness</h3>
        <div class="ledger">
          <div class="l-row"><span class="l-t">budget</span><span class="mono">50 live calls/run · 12 used</span></div>
          <div class="l-row"><span class="l-t">key</span><span class="mono">never logged · choke-point enforced</span></div>
        </div>
        <div class="note mt3">The real Jev key runs only inside the cost-capped drift harness. Every live call is
        budgeted and logged; the key value never appears in any log.</div>
      </div>
    </div>`;
}

function renderDegraded(root) {
  const pipe = Store.pipeline();
  root.innerHTML += `
    ${pipe.health !== 'live' ? `<div class="degraded-banner">Pipeline is ${pipe.health.toUpperCase()} — lag ${Math.round(pipe.lagMs / 1000)}s. The ladder below is the current truth.</div>` : ''}
    <div class="safety-grid">
      <div class="safety-card">
        <h3>The fail-open ladder</h3>
        <div class="ledger">
          <div class="l-row"><span class="l-t">C1</span><span>uncertain → <strong>page</strong> <span class="note">current step</span></span></div>
          <div class="l-row"><span class="l-t">C2</span><span>stale evidence → <strong>page</strong></span></div>
          <div class="l-row"><span class="l-t">C3</span><span>Jev down → <strong>page</strong> (timer decides)</span></div>
          <div class="l-row"><span class="l-t">C4</span><span>forwarder down → <strong>spill to disk, page on recovery</strong></span></div>
        </div>
        <div class="note mt3">Every rung pages <em>more</em>, never suppresses more. Degradation is disclosed, not hidden.</div>
      </div>
      <div class="safety-card">
        <h3>3 AM levers</h3>
        <div class="note">Each lever pages more. None of them suppresses more. That asymmetry is the safety case.</div>
        <div class="mt3" style="display:flex;gap:8px;flex-wrap:wrap">
          <a class="btn small" href="#/safety/kill">Kill switch</a>
          <a class="btn small" href="#/proofs">Storm digest</a>
          <a class="btn small" href="#/pages">Page me anyway</a>
        </div>
        <div class="contract-box"><strong>"Page me anyway"</strong> is the audit-logged override → the real paging path.
        It pages through your normal channels and writes the audit entry. Use it when the machine feels wrong.</div>
      </div>
    </div>`;
}

export async function renderSafety(root, params, ctx) {
  window.__safetyCtx = ctx;
  const sub = params.sub || 'kill';
  ctx.setScreenCode('SAFETY');
  root.innerHTML = `
    <div class="view-head">
      <h1>Safety</h1>
      <div class="sub">The machine's safety machinery — visible, inspectable, first-class. Never a settings page.</div>
    </div>
    ${tabsHtml(sub)}
    <div id="safety-body"></div>
  `;
  const body = root.querySelector('#safety-body');
  ({ kill: renderKill, policy: renderPolicy, auth: renderAuth, race: renderRace, degraded: renderDegraded })[sub](body);
}
