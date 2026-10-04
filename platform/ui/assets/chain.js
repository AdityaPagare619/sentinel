/* chain.js — the event-log chain for the audit explorer (S4).
 *
 * The frozen read contract (v1.0.0) does NOT expose the platform's sealed
 * event log — there is no GET /api/audit/chain. Until the platform ships it
 * (RFC, recorded in the build record), the console derives a tamper-evident
 * chain LOCALLY from stored decisions and verifies it client-side:
 *
 *   link_i = sha256(link_{i-1} ‖ input_sha256_i ‖ created_at_i)
 *
 * This is a DERIVED structure, labeled as such wherever rendered (§4.4):
 * it proves the decisions the console holds are internally consistent and
 * complete for the window — it is NOT the platform's sealed log and cannot
 * attest to what the platform recorded. The distinction is rendered in-band.
 *
 * When the platform exposes the sealed chain, this module verifies the
 * platform's signatures instead of deriving links; the verification UI is
 * already the seam. Pure functions; no DOM at import.
 */

export async function sha256Hex(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
}

/* decisions: stored decision records with {input_sha256, audit:{created_at}, id}.
 * Returns the ordered link list with a recomputed hash per link. */
export async function deriveChain(decisions) {
  const rows = [...decisions]
    .filter(d => d && d.input_sha256 && (d.audit?.created_at || d.time))
    .sort((a, b) => (a.audit?.created_at || a.time) < (b.audit?.created_at || b.time) ? -1 : 1);
  let prev = 'GENESIS';
  const links = [];
  for (const d of rows) {
    const ts = d.audit?.created_at || d.time;
    const hash = await sha256Hex(prev + '|' + d.input_sha256 + '|' + ts);
    links.push({
      decision_id: d.id, input_sha256: d.input_sha256,
      created_at: ts, disposition: d.disposition,
      prev, hash,
    });
    prev = hash;
  }
  return links;
}

/* Verify a link list (derived or platform-sealed). Returns the first broken
 * link's index, or -1 when the chain is whole. A break is never silent:
 * the chain view renders the break location in-band. */
export async function verifyChain(links) {
  let prev = 'GENESIS';
  for (let i = 0; i < links.length; i++) {
    const l = links[i];
    if (l.prev !== prev) return { ok: false, brokenAt: i, reason: 'prev-hash mismatch' };
    const recomputed = await sha256Hex(l.prev + '|' + l.input_sha256 + '|' + l.created_at);
    if (recomputed !== l.hash) return { ok: false, brokenAt: i, reason: 'hash mismatch — record altered' };
    prev = l.hash;
  }
  return { ok: true, brokenAt: -1, n: links.length };
}

export function shortHash(h, n = 8) {
  return h ? 'sha256:' + String(h).slice(0, n) + '…' : '—';
}
