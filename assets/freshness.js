/* freshness.js — the freshness contract (§4.1) as code.
 *
 * Every component that renders data carries three inseparable companions:
 * the VALUE, the AS-OF TIMESTAMP, and the FRESHNESS STATE. The four states
 * form a closed vocabulary used identically across all five surfaces:
 *
 *   live      — current within the surface's freshness budget; rendered normally
 *   cached    — served from cache while refetching; as-of shown, never hidden
 *   stale     — exceeded the budget; stale treatment + what it's waiting on
 *   degraded  — source unreachable; last-known labeled as last-known, or honest empty
 *
 * "Unknown freshness" is not a fifth state — it is a bug.
 * Freshness budgets are per-surface (§11 R3). Tested by tests/freshness.test.mjs.
 */

export const FRESHNESS_STATES = ['live', 'cached', 'stale', 'degraded'];

/* §11 R3: the budgets each FreshnessBadge renders against. Tunable (Type 2)
 * with measurement — but they are budgets, not observations. */
export const FRESHNESS_BUDGETS = {
  river:      30_000,        /* S1: seconds — the tape is a live surface */
  decision:   30_000,        /* a single decision shares the river's stream */
  calibration: 26 * 3_600_000, /* S2: nightly shadow join; 24h + grace */
  simulator:  26 * 3_600_000, /* S3: dataset-bound, same nightly join */
  audit:      null,          /* S4: the log is immutable — freshness means chain-verified, not recent */
  shadow:     26 * 3_600_000, /* S5: daily shadow join */
};

/* Resolve a freshness state from the stream/source condition + the as-of age.
 * Order matters: a dead source is degraded even when the cached value is young. */
export function freshnessState({ sourceUp = true, refetching = false, asOfMs = null, nowMs = Date.now(), budgetMs = FRESHNESS_BUDGETS.river } = {}) {
  if (!sourceUp) return 'degraded';
  if (budgetMs == null) return 'live'; /* immutable surfaces: chain-verified, not time-fresh */
  if (asOfMs == null) return 'degraded'; /* unknown freshness is a bug — surface as degraded, never silently */
  const age = nowMs - asOfMs;
  if (age <= budgetMs) return refetching ? 'cached' : 'live';
  return 'stale';
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ---------- <FreshnessBadge> (Appendix A) ----------
 * The closed four-state vocabulary with a TEXT LABEL — never color-only (§7).
 * Uses the reserved --state-* roles (§2.2), the system-health channel (B1).
 * Every data-bearing surface renders one; rows render a compact as-of + the
 * tape-level badge carries the state. */
export function freshnessBadge({ state = 'live', asOfIso = null, waitingOn = null, budgetMs = null, compact = false } = {}) {
  if (!FRESHNESS_STATES.includes(state)) state = 'degraded';
  const labels = {
    live: 'live', cached: 'cached · refetching',
    stale: 'stale', degraded: 'degraded',
  };
  const glyphs = { live: '●', cached: '◐', stale: '◑', degraded: '○' };
  const detail = state === 'stale' && waitingOn
    ? ` — waiting on ${waitingOn}`
    : state === 'degraded' && waitingOn
      ? ` — ${waitingOn}`
      : '';
  const title = asOfIso
    ? `as of ${asOfIso}${budgetMs ? ` · budget ${Math.round(budgetMs / 1000)}s` : ''}${waitingOn ? ` · ${waitingOn}` : ''}`
    : 'as-of unknown — treat as degraded';
  return `<span class="freshness mono fr-${esc(state)}" role="status" aria-label="freshness: ${esc(labels[state])}${esc(asOfIso ? ', as of ' + asOfIso : '')}" title="${esc(title)}">` +
    `<span class="fr-glyph" aria-hidden="true">${glyphs[state]}</span>` +
    `<span class="fr-label">${esc(labels[state])}</span>` +
    (asOfIso && !compact ? `<span class="fr-asof">${esc(asOfIso.slice(11, 19))} UTC</span>` : '') +
    (detail ? `<span class="fr-detail">${esc(detail)}</span>` : '') +
    `</span>`;
}

/* Tape-level stream freshness: one badge for the whole river, driven by the
 * SSE/stream state. Rows carry their own as-of timestamps. */
export function streamFreshness({ streamState = 'paused', lastEventAt = null, nowMs = Date.now() } = {}) {
  const map = {
    live: () => freshnessState({ sourceUp: true, asOfMs: lastEventAt, nowMs }),
    polling: () => 'cached',
    reconnecting: () => 'cached',
    /* static showcase: the "stream" is a pre-rendered snapshot baked at build
     * time. It is cached by definition and must never read as live. */
    snapshot: () => 'cached',
    paused: () => lastEventAt ? freshnessState({ sourceUp: true, asOfMs: lastEventAt, nowMs }) : 'degraded',
  };
  const state = (map[streamState] || map.paused)();
  const waitingOn = streamState === 'reconnecting' ? 'SSE reconnect'
    : streamState === 'polling' ? 'poll fallback'
    : streamState === 'snapshot' ? 'static snapshot — not live'
    : state === 'stale' ? 'stream'
    : null;
  const asOfIso = lastEventAt ? new Date(lastEventAt).toISOString() : null;
  return { state, asOfIso, waitingOn };
}
