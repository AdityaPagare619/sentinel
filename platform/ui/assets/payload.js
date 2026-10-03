/* payload.js — the TOTAL, SAFE generic arbitrary-payload renderer.
 *
 * Renders STRUCTURE, never meaning. It cannot throw, cannot leak secrets, and
 * requires no foreknowledge of any key. This is the only component allowed to
 * touch decision.alert ("alert context as received" — arbitrary by contract).
 *
 * Atomic grammar (synthesis §3, L1b): scalar | link | flat-table |
 * nested-collapse | array | binary/redacted | unknown→"—".
 *
 * Laws enforced here:
 *  - depth-capped (6), width-capped per cell, item-capped, byte-budgeted
 *    (64KB, stated truncation) — output degrades incrementally: rows already
 *    rendered survive, the truncation marker states what was cut.
 *  - secret-shaped keys render as "▪▪▪ redacted" — never the value, never blank.
 *  - unknown/missing renders as "—" with the reason IN-BAND (never a tooltip).
 *  - defensive reads: throwing getters, circular refs, __proto__ keys,
 *    bidi/control characters (as visible [U+XXXX] markers), and hostile objects are all handled.
 *  - never infers semantics: keys render verbatim; no field is ever labeled
 *    "root cause" or similar. Timestamps render verbatim, never reinterpreted.
 *
 * Pure functions, no DOM — unit-tested by tests/payload.test.mjs (hostile fuzz).
 */

export const PAYLOAD_LIMITS = {
  maxDepth: 6,       /* nesting beyond this renders as a cap marker */
  maxCellChars: 160, /* per-cell width cap */
  maxItems: 50,      /* array items rendered before "+N more" */
  maxKeys: 100,      /* object keys rendered before "+N more" */
  maxBytes: 65536,   /* output budget; truncation is stated, never silent */
};

/* Secret-shaped key: match on whole segments so "author" does not match "auth". */
export const SECRET_KEY_RE = /(^|[_-])(token|secret|passwd|password|pwd|credentials?|api[_-]?key|auth|bearer|private[_-]?key|session|cookie)($|[_-])/i;

/* Bidi overrides + C0 controls (except \t \n) are a visual-spoofing vector. Stripped. */
/* Bidi overrides + C0 controls (except \t \n) are a visual-spoofing vector.
 * A1 hostile-content contract: they render as VISIBLE markers, never silently
 * applied and never silently stripped — stripping can hide a spoof. */
const MARK_RE_SRC = '[\\u200E\\u200F\\u202A-\\u202E\\u2066-\\u2069\\uFEFF\\u0000-\\u0008\\u000B\\u000C\\u000E-\\u001F\\u007F]';
const MARK_RE = new RegExp(MARK_RE_SRC, 'g');
function markUnsafe(s) {
  return String(s).replace(MARK_RE, c =>
    `[U+${c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')}]`);
}

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function safeStr(s) {
  return esc(markUnsafe(s));
}

function isSecretKey(k) {
  return SECRET_KEY_RE.test(String(k).toLowerCase());
}

/* A value is "scalar" if it renders on one line with no recursion. */
function isScalar(v) {
  if (v === null) return true;
  const t = typeof v;
  if (t === 'string' || t === 'boolean') return true;
  if (t === 'number') return Number.isFinite(v);
  if (t === 'bigint') return true;
  return false;
}

function dash(why) {
  return `<span class="pv-dash mono">— <span class="pv-why">${esc(why)}</span></span>`;
}

function cappedText(s, cap = PAYLOAD_LIMITS.maxCellChars) {
  const clean = markUnsafe(s);
  if (clean.length <= cap) return esc(clean);
  return esc(clean.slice(0, cap)) + `<span class="pv-why"> …(+${(clean.length - cap).toLocaleString('en-US')} chars)</span>`;
}

/* A5: UTC + local on every timestamp display, in-band, no exceptions.
 * Only strict ISO-8601 is interpreted — anything else renders verbatim. */
const ISO_TS_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:?\d{2})$/;
function tzAbbr() {
  try {
    const p = new Intl.DateTimeFormat('en', { timeZoneName: 'short' }).formatToParts(new Date());
    const t = p.find(x => x.type === 'timeZoneName');
    return t ? t.value : 'local';
  } catch { return 'local'; }
}
export function dualZone(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const ud = d.toISOString();
  const utc = ud.slice(0, 10) + ' ' + ud.slice(11, 19) + ' UTC';
  let local;
  try {
    local = d.toLocaleDateString('en-CA') + ' ' +
      d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) +
      ' ' + tzAbbr();
  } catch { local = d.toString(); }
  return { utc, local };
}
function renderTimestamp(v) {
  const z = dualZone(v);
  if (!z) return `<span class="pv-str mono">${cappedText(v)}</span>`;
  return `<span class="pv-ts mono" title="${esc(v)}">${esc(z.utc)} · ${esc(z.local)}</span>`;
}

function renderScalar(v) {
  if (v === null) return dash('null');
  const t = typeof v;
  if (t === 'string') {
    if (/^https?:\/\/[^\s<>"']{1,400}$/i.test(v)) {
      const href = esc(markUnsafe(v));
      return `<a class="pv-link mono" href="${href}" target="_blank" rel="noopener noreferrer">${cappedText(v)}</a>`;
    }
    if (ISO_TS_RE.test(v)) return renderTimestamp(v);
    return `<span class="pv-str mono">${cappedText(v) || '<span class="pv-why">empty string</span>'}</span>`;
  }
  if (t === 'number') {
    if (!Number.isFinite(v)) return dash(Number.isNaN(v) ? 'not a number' : 'infinite');
    return `<span class="pv-num mono">${esc(String(v))}</span>`;
  }
  if (t === 'boolean') return `<span class="pv-bool mono">${v ? 'true' : 'false'}</span>`;
  if (t === 'bigint') return `<span class="pv-num mono">${esc(v.toString())}n</span>`;
  if (t === 'undefined') return dash('undefined');
  if (t === 'function') return dash('function — not rendered');
  if (t === 'symbol') return dash('symbol — not rendered');
  return dash(`type ${t} — not rendered`);
}

/* Defensive property read: a getter may throw; __proto__ must not escape. */
function safeGet(obj, key) {
  try {
    if (!Object.prototype.hasOwnProperty.call(obj, key)) return { ok: false, why: 'missing' };
    return { ok: true, value: obj[key] };
  } catch {
    return { ok: false, why: 'unreadable — getter threw' };
  }
}

function isBinary(v) {
  return (typeof ArrayBuffer !== 'undefined' && v instanceof ArrayBuffer) ||
    (typeof DataView !== 'undefined' && v instanceof DataView) ||
    (typeof Uint8Array !== 'undefined' && ArrayBuffer.isView(v));
}

function byteLen(v) {
  try {
    if (v instanceof ArrayBuffer) return v.byteLength;
    if (ArrayBuffer.isView(v)) return v.byteLength;
  } catch { /* fall through */ }
  return null;
}

class StopSignal {} /* internal: byte budget exceeded — unwinds to the truncation marker */

/* Recursive core. Pushes fragments via put(); may throw StopSignal, which the
 * top level converts into the stated truncation marker. Partial output
 * survives — truncation cuts the tail, never the whole render. */
function renderValue(v, depth, st, put, keyHint) {
  /* secret-shaped keys: value replaced, never shown, never blank */
  if (keyHint !== undefined && keyHint !== null && isSecretKey(keyHint)) {
    put(`<span class="pv-redact mono">▪▪▪ <span class="pv-why">redacted — secret-shaped key</span></span>`);
    return;
  }

  if (v === null) { put(dash('null')); return; }
  const t = typeof v;
  if (t === 'string' || t === 'number' || t === 'boolean' || t === 'bigint' ||
      t === 'undefined' || t === 'function' || t === 'symbol') {
    put(renderScalar(v));
    return;
  }
  if (t !== 'object') { put(dash(`type ${t} — not rendered`)); return; }

  if (st.seen.has(v)) { put(`<span class="pv-why mono">[circular — already shown above]</span>`); return; }
  if (depth >= PAYLOAD_LIMITS.maxDepth) {
    const kind = Array.isArray(v) ? 'array' : 'object';
    put(`<span class="pv-cap mono">+ ${kind} <span class="pv-why">(depth cap ${PAYLOAD_LIMITS.maxDepth})</span></span>`);
    return;
  }

  if (v instanceof Date) {
    const iso = Number.isNaN(v.getTime()) ? null : v.toISOString();
    put(iso ? `<span class="pv-ts mono">${esc(iso)}</span>` : dash('invalid date'));
    return;
  }
  if (isBinary(v)) {
    const n = byteLen(v);
    put(`<span class="pv-why mono">binary${n != null ? ` (${n.toLocaleString('en-US')} bytes)` : ''} — not rendered</span>`);
    return;
  }

  st.seen.add(v);
  try {
    if (v instanceof Map) {
      renderEntries(
        Array.from(v.entries()).map(([k, val]) => [safeStr(k), val]),
        `map · ${v.size}`, depth, st, put, null);
      return;
    }
    if (v instanceof Set) {
      renderEntries(
        Array.from(v.values()).map((val, i) => [String(i), val]),
        `set · ${v.size}`, depth, st, put, null);
      return;
    }
    if (Array.isArray(v)) { renderArray(v, depth, st, put); return; }

    /* plain object — own enumerable keys only, never the prototype */
    const keys = Object.keys(v);
    if (keys.length === 0) { put(`<span class="pv-why mono">{{}} empty object</span>`); return; }
    const shown = keys.slice(0, PAYLOAD_LIMITS.maxKeys);
    const rest = keys.length - shown.length;

    /* flat-table fast path: every shown value is scalar (PagerDuty's custom_details rule) */
    let allScalar = true;
    for (const k of shown) {
      const r = safeGet(v, k);
      if (!r.ok || !isScalar(r.value)) { allScalar = false; break; }
    }
    if (allScalar) {
      put(`<table class="pv-table"><tbody>`);
      for (const k of shown) {
        const r = safeGet(v, k);
        put(`<tr><td class="pv-k mono">${safeStr(k)}</td><td>`);
        renderValue(r.ok ? r.value : undefined, depth + 1, st, put, r.ok ? k : null);
        if (!r.ok) put(dash(r.why));
        put(`</td></tr>`);
      }
      put(`</tbody></table>`);
    } else {
      /* nested: collapsed <details> beyond depth 1 — keyboard-accessible, no hover needed */
      const open = depth < 1 ? ' open' : '';
      put(`<details class="pv-nested"${open}><summary class="pv-sum mono">object · ${keys.length} key${keys.length === 1 ? '' : 's'}</summary><div class="pv-body">`);
      for (const k of shown) {
        const r = safeGet(v, k);
        put(`<div class="pv-row"><span class="pv-k mono">${safeStr(k)}</span><span class="pv-v">`);
        if (r.ok) renderValue(r.value, depth + 1, st, put, k);
        else put(dash(r.why));
        put(`</span></div>`);
      }
      put(`</div></details>`);
    }
    if (rest > 0) put(`<div class="pv-row"><span class="pv-why mono">+${rest} more keys — not rendered (cap ${PAYLOAD_LIMITS.maxKeys})</span></div>`);
  } finally {
    st.seen.delete(v);
  }
}

function renderArray(arr, depth, st, put) {
  const L = PAYLOAD_LIMITS;
  const n = arr.length;
  if (n === 0) { put(`<span class="pv-why mono">[] empty array</span>`); return; }
  const shown = Math.min(n, L.maxItems);
  let allScalar = true;
  for (let i = 0; i < shown; i++) {
    const r = safeGet(arr, String(i));
    if (!r.ok || !isScalar(r.value)) { allScalar = false; break; }
  }
  if (allScalar) {
    /* numbered list, one value per line — scannable */
    put(`<ol class="pv-list">`);
    for (let i = 0; i < shown; i++) {
      const r = safeGet(arr, String(i));
      put(`<li value="${i}">`);
      if (r.ok) renderValue(r.value, depth + 1, st, put, null);
      else put(dash(r.why));
      put(`</li>`);
    }
    put(`</ol>`);
  } else {
    const open = depth < 1 ? ' open' : '';
    put(`<details class="pv-nested"${open}><summary class="pv-sum mono">array · ${n} item${n === 1 ? '' : 's'}</summary><div class="pv-body">`);
    for (let i = 0; i < shown; i++) {
      const r = safeGet(arr, String(i));
      put(`<div class="pv-row"><span class="pv-k mono">${i}</span><span class="pv-v">`);
      if (r.ok) renderValue(r.value, depth + 1, st, put, null);
      else put(dash(r.why));
      put(`</div>`);
    }
    put(`</div></details>`);
  }
  if (n > shown) put(`<div class="pv-row"><span class="pv-why mono">+${(n - shown).toLocaleString('en-US')} more items — not rendered (cap ${L.maxItems})</span></div>`);
}

function renderEntries(entries, label, depth, st, put) {
  const shown = entries.slice(0, PAYLOAD_LIMITS.maxItems);
  const open = depth < 1 ? ' open' : '';
  put(`<details class="pv-nested"${open}><summary class="pv-sum mono">${esc(label)}</summary><div class="pv-body">`);
  for (const [k, val] of shown) {
    put(`<div class="pv-row"><span class="pv-k mono">${k}</span><span class="pv-v">`);
    renderValue(val, depth + 1, st, put, null);
    put(`</span></div>`);
  }
  if (entries.length > shown.length) put(`<div class="pv-row"><span class="pv-why mono">+${entries.length - shown.length} more — not rendered</span></div>`);
  put(`</div></details>`);
}

/* ---------- public API ---------- */

/* The total renderer. NEVER throws: any unexpected failure degrades to the
 * honest error state, never a blank and never a partial lie. */
export function renderPayload(value) {
  const st = { bytes: 0, truncated: false, seen: new Set(), out: [] };
  const put = (html) => {
    if (st.bytes + html.length > PAYLOAD_LIMITS.maxBytes) {
      st.truncated = true;
      throw new StopSignal();
    }
    st.bytes += html.length;
    st.out.push(html);
  };
  try {
    put(`<div class="pv" data-depth="0">`);
    renderValue(value, 0, st, put, null);
    put(`</div>`);
  } catch (e) {
    if (!(e instanceof StopSignal)) {
      /* totality guarantee: even our own bug renders as a designed state */
      return `<div class="pv-err mono">payload renderer failed safely (${esc(e && e.message ? e.message : 'unknown error')}). ` +
        `Nothing was rendered rather than something wrong. The raw payload is in the audit explorer.</div>`;
    }
  }
  let h = st.out.join('');
  if (st.truncated) {
    h += `<div class="pv-trunc mono">truncated — ${PAYLOAD_LIMITS.maxBytes.toLocaleString('en-US')} byte budget reached. ` +
      `Rows above are complete; the remainder is not rendered. The full payload is in the audit explorer.</div></div>`;
  }
  return h;
}

/* ---------- the five component states (§4.3) for the payload viewer ---------- */
export function payloadSkeletonHtml() {
  return `<div class="pv-skel" aria-hidden="true"><span class="sk" style="width:40%"></span><span class="sk" style="width:85%"></span><span class="sk" style="width:65%"></span><span class="sk" style="width:75%"></span></div>`;
}
export function payloadEmptyHtml() {
  return `<div class="empty-block"><p>No alert context on this decision.</p><p class="mono empty-hint">decision.alert is absent — the envelope above is the complete record.</p></div>`;
}
