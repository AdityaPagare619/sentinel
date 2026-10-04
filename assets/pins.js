/* pins.js — FIELD PINS: the operator's composability mechanism (synthesis §3).
 *
 * A pin is a display-only binding of a payload path into a river column or an
 * evidence slot:
 *   { id, name, path, integration, slot: 'river'|'evidence', created_at }
 * Paths are relative to decision.alert ("alert context as received"), dot
 * separated, with numeric segments indexing arrays: "labels.cluster",
 * "annotations.0.runbook_url". No wildcards, no scripting — deliberately.
 *
 * THE DISPLAY-ONLY INVARIANT (synthesis §3, the line that makes the verdict a
 * decision): pins NEVER enter the filter/sort grammar, NEVER drive severity
 * color or dispositions, and switch off with one keypress. isQueryableField()
 * in contract.js rejects every pin path by construction; tests/pins.test.mjs
 * asserts the invariant. If a pinned field needs to be filterable, the path is
 * promotion-by-RFC into the contract — never a pin upgrade.
 *
 * Pre-mortem mitigations (built):
 *  - pins carry the integration name IN-BAND (a pinned value whose meaning
 *    differs across vendors cannot pass as a neutral column);
 *  - unresolvable pins render "—" with the reason in-band, never blank;
 *  - save-time validation warns when a path resolves on zero recent decisions.
 *
 * Pure functions + a small localStorage workspace store. No DOM at import;
 * the rail editor in views-river.js owns the DOM. Tested by
 * tests/pins.test.mjs.
 */

export const PIN_SCHEMA_VERSION = 1;
const STORE_KEY = 'sentinel.ui.pins.v1';
const MAX_PINS = 24; /* more pins than this is a view-design problem, not a pin problem */

let memoryStore = null; /* fallback when localStorage is unavailable (tests, SSR) */

function storage() {
  try {
    if (typeof localStorage !== 'undefined') return localStorage;
  } catch { /* fall through */ }
  return null;
}

/* ---------- path grammar ---------- */

/* "labels.cluster" | "annotations.0.url". Segments: [A-Za-z0-9_-]+ or a
 * non-negative integer. Empty segments, leading/trailing dots rejected. */
const SEG_RE = /^[A-Za-z0-9_-]+$/;
export function parsePinPath(path) {
  if (typeof path !== 'string') return null;
  const trimmed = path.trim();
  if (!trimmed || trimmed.length > 256) return null;
  const segs = trimmed.split('.');
  for (const s of segs) {
    if (!SEG_RE.test(s)) return null;
  }
  return segs;
}

/* Defensive resolution: throwing getters, __proto__, and missing keys are
 * all handled. NEVER throws. Root is decision.alert. */
export function resolvePath(decision, path) {
  const segs = parsePinPath(path);
  if (!segs) return { found: false, why: 'invalid pin path' };
  let cur;
  try {
    cur = decision && typeof decision === 'object' ? decision.alert : undefined;
  } catch {
    return { found: false, why: 'unreadable decision' };
  }
  if (cur === undefined || cur === null || typeof cur !== 'object') {
    return { found: false, why: 'no alert context on this decision' };
  }
  for (const s of segs) {
    try {
      if (cur === null || (typeof cur !== 'object' && typeof cur !== 'function')) {
        return { found: false, why: `path dead-ends at '${s}'` };
      }
      if (!Object.prototype.hasOwnProperty.call(cur, s)) {
        return { found: false, why: `no such key '${s}'` };
      }
      cur = cur[s];
    } catch {
      return { found: false, why: `unreadable at '${s}'` };
    }
  }
  return { found: true, value: cur };
}

/* ---------- pin documents ---------- */

export function makePin({ name, path, integration = '', slot = 'river' }) {
  return {
    id: 'pin_' + Math.random().toString(36).slice(2, 10),
    schema: PIN_SCHEMA_VERSION,
    name: String(name || '').trim(),
    path: String(path || '').trim(),
    integration: String(integration || '').trim(),
    slot: slot === 'evidence' ? 'evidence' : 'river',
    created_at: new Date().toISOString(),
  };
}

export function validatePinDoc(pin) {
  const errors = [];
  if (!pin || typeof pin !== 'object') return { ok: false, errors: ['pin is not an object'] };
  if (!pin.name) errors.push('pin needs a name');
  if (!parsePinPath(pin.path)) errors.push(`invalid path '${pin.path}' — dot-separated segments, e.g. labels.cluster`);
  if (pin.slot !== 'river' && pin.slot !== 'evidence') errors.push(`slot must be 'river' or 'evidence'`);
  return { ok: errors.length === 0, errors };
}

/* Save-time validation against recent decisions: a pin that resolves nowhere
 * is probably a typo — warn loudly, still allow (the vendor may not have
 * appeared in the current window). */
export function validatePinAgainstSample(pin, sampleDecisions) {
  const doc = validatePinDoc(pin);
  const warnings = [];
  if (!doc.ok) return { ok: false, errors: doc.errors, warnings };
  const rows = Array.isArray(sampleDecisions) ? sampleDecisions : [];
  if (!rows.length) {
    warnings.push('no sample decisions to preview against — pin saved unvalidated');
    return { ok: true, errors: [], warnings };
  }
  const hits = rows.filter(d => resolvePath(d, pin.path).found).length;
  if (hits === 0) {
    warnings.push(`path '${pin.path}' resolves on 0 of ${rows.length} recent decisions — check the spelling or the integration`);
  }
  return { ok: true, errors: [], warnings, hits, n: rows.length };
}

/* ---------- workspace store (Type 2: reversible, per-workspace) ---------- */

export function loadPins() {
  const s = storage();
  try {
    const raw = s ? s.getItem(STORE_KEY) : (memoryStore || null);
    if (!raw) return [];
    const docs = JSON.parse(raw);
    if (!Array.isArray(docs)) return [];
    return docs.filter(d => validatePinDoc(d).ok).slice(0, MAX_PINS);
  } catch {
    return [];
  }
}

export function savePins(pins) {
  const clean = (Array.isArray(pins) ? pins : []).filter(d => validatePinDoc(d).ok).slice(0, MAX_PINS);
  const raw = JSON.stringify(clean);
  const s = storage();
  try {
    if (s) s.setItem(STORE_KEY, raw); else memoryStore = raw;
  } catch { memoryStore = raw; }
  return clean;
}

export function exportPins() {
  return JSON.stringify({ schema: PIN_SCHEMA_VERSION, exported_at: new Date().toISOString(), pins: loadPins() }, null, 2);
}

/* Import: diffable JSON (L1b). Invalid docs are reported, never silently dropped. */
export function importPinsJson(text) {
  let doc;
  try { doc = JSON.parse(text); }
  catch { return { ok: false, errors: ['not valid JSON'], imported: [] }; }
  const list = Array.isArray(doc) ? doc : doc.pins;
  if (!Array.isArray(list)) return { ok: false, errors: ['expected a pin array or {pins:[...]}'], imported: [] };
  const imported = [], errors = [];
  for (const p of list) {
    const v = validatePinDoc(p);
    if (v.ok) imported.push({ ...p, id: p.id || makePin(p).id });
    else errors.push(`rejected pin '${p && p.name}': ${v.errors.join('; ')}`);
  }
  const merged = [...loadPins(), ...imported].slice(0, MAX_PINS);
  savePins(merged);
  return { ok: errors.length === 0, errors, imported: imported.map(p => p.name) };
}

/* ---------- rendering (display-only) ---------- */

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* A scalar-ish value renders inline; anything structural gets the one-line
 * summary treatment — pins are glanceable, the payload viewer is the drill-down. */
function pinValueHtml(value) {
  if (value === null || value === undefined) return `<span class="pv-dash mono">—</span>`;
  const t = typeof value;
  if (t === 'string' || t === 'number' || t === 'boolean') {
    let s = String(value);
    if (s.length > 48) s = s.slice(0, 48) + '…';
    if (/^https?:\/\/[^\s<>"']{1,200}$/i.test(s)) {
      return `<a class="pv-link mono" href="${esc(s)}" target="_blank" rel="noopener noreferrer">${esc(s.length > 48 ? s.slice(0, 48) + '…' : s)}</a>`;
    }
    return `<span class="mono">${esc(s)}</span>`;
  }
  if (Array.isArray(value)) return `<span class="mono">${esc(`[${value.length} items]`)}</span>`;
  if (t === 'object') {
    const n = Object.keys(value).length;
    return `<span class="mono">${esc(`{${n} keys}`)}</span>`;
  }
  return `<span class="pv-dash mono">— <span class="pv-why">${esc(t)} — not rendered</span></span>`;
}

/* River cells: the integration label rides IN-BAND (pre-mortem mitigation #1).
 * These cells are display bindings — they are never filter operands. */
export function pinCellsHtml(decision, pins) {
  const river = (pins || []).filter(p => p.slot === 'river');
  if (!river.length) return '';
  return river.map(p => {
    const r = resolvePath(decision, p.path);
    const integ = p.integration ? `<span class="pin-integ mono">${esc(p.integration)}</span>` : `<span class="pin-integ mono pin-integ-unlabeled">unlabeled</span>`;
    const val = r.found ? pinValueHtml(r.value) : `<span class="pv-dash mono">— <span class="pv-why">${esc(r.why)}</span></span>`;
    return `<span class="pin-cell" data-pin="${esc(p.id)}" title="field pin: ${esc(p.path)} — display only, not a filter">${val} ${integ}</span>`;
  }).join('');
}

/* Evidence (S2) section: pinned fields with their paths stated. */
export function pinnedSectionHtml(decision, pins) {
  const ev = (pins || []).filter(p => p.slot === 'evidence');
  if (!ev.length) return '';
  const rows = ev.map(p => {
    const r = resolvePath(decision, p.path);
    const integ = p.integration ? `<span class="pin-integ mono">${esc(p.integration)}</span>` : '';
    return `<div class="pv-row"><span class="pv-k mono">${esc(p.name)} ${integ}<br><span class="pv-why">${esc(p.path)}</span></span>` +
      `<span class="pv-v">${r.found ? pinValueHtml(r.value) : `<span class="pv-dash mono">— <span class="pv-why">${esc(r.why)}</span></span>`}</span></div>`;
  }).join('');
  return `<section class="drawer-sec"><h3>Pinned fields</h3><div class="pv-body">${rows}</div>` +
    `<p class="drawer-note">Field pins are display bindings — they never filter, sort, or recolor the river.</p></section>`;
}

/* The 3 AM escape hatch: one toggle, pins gone. */
export function pinsOffHtml() {
  return `<button class="opt" id="pins-off" title="hide all field pins (display bindings off)">pins off</button>`;
}

export { MAX_PINS };
