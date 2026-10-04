/* views-setup.js — Production entry point: backend configuration.
 * The production console (GitHub Pages, DATA_MODE='live') is a pure static
 * client: it renders NOTHING until the operator points it at their own
 * Sentinel backend. No fixtures, no synthetic data, no simulated mode —
 * an unconfigured console shows this honest empty state, never invented data.
 * Interface Principles P3 (honesty contract) with full force.
 */
import { setBackendUrl } from './api.js';
import { esc } from './components.js';

const GUIDE_URL = 'https://github.com/AdityaPagare619/sentinel/blob/main/docs/deploy-production.md';

export async function renderSetup(root) {
  root.innerHTML = `
  <div class="setup-wrap">
    <div class="setup-card">
      <h2 class="setup-title">Connect your Sentinel backend</h2>
      <p class="setup-lede">This console is the <b>production</b> surface — it renders live
      decisions from a Sentinel engine <b>you</b> run. There is no demo data here,
      nothing simulated, nothing to browse until a backend is connected.</p>
      <label class="mono setup-label" for="setup-url">Backend URL</label>
      <div class="setup-row">
        <input id="setup-url" class="mono setup-input" type="url"
               placeholder="https://sentinel.example.com"
               autocomplete="off" spellcheck="false"
               aria-label="Sentinel backend URL">
        <button id="setup-go" class="btn primary">Connect</button>
      </div>
      <div class="mono setup-err" id="setup-err" role="alert" hidden></div>
      <p class="mono setup-note" id="setup-checking" hidden>Checking — GET /api/decisions?limit=1 …</p>
      <hr class="setup-hr">
      <p class="mono setup-note">No backend yet? Sentinel runs in <b>your</b> infrastructure —
      the engine never leaves your network. One-command setup:</p>
      <p><a class="mono" href="${GUIDE_URL}" target="_blank" rel="noopener">docs/deploy-production.md — systemd unit, reverse proxy, BYOK keys ↗</a></p>
      <p class="mono setup-note">Want the tour first? The
      <a href="./staging/">simulated showcase</a> runs entirely on synthetic data —
      clearly labeled, nothing real.</p>
    </div>
  </div>`;

  const input = root.querySelector('#setup-url');
  const err = root.querySelector('#setup-err');
  const checking = root.querySelector('#setup-checking');
  const go = async () => {
    err.hidden = true;
    let url;
    try {
      url = setBackendUrl(input.value);
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
      return;
    }
    checking.hidden = false;
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 9000);
      const res = await fetch(url + '/api/decisions?limit=1', { signal: ctrl.signal });
      clearTimeout(t);
      if (!res.ok) throw new Error(`backend answered HTTP ${res.status} — is the platform server running?`);
      await res.json();
    } catch (e) {
      checking.hidden = true;
      err.textContent = `Couldn't reach a Sentinel backend at ${url} (${e.message}). The URL is saved — fix the backend and reload, or check the deploy guide above.`;
      err.hidden = false;
      return;
    }
    location.reload();
  };
  root.querySelector('#setup-go').addEventListener('click', go);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  input.focus();
}
