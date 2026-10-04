/* shadow.js — the Shadow Report's headline metrics (synthesis §9 A2, adopted).
 *
 * Two honest metrics, no vanity. Definitions are Type 2 (tunable); the
 * definitions themselves are the contract of the S5 surface.
 *
 *   page_precision     = acknowledged-and-acted-on pages ÷ pages sent
 *   suppression_regret = suppressed events later joined to an incident
 *                        or manually unsuppressed ÷ suppressions
 *
 * Both are computed from the READ CONTRACT's decision records + their
 * outcomes — never from a non-contract endpoint. Every figure is labeled
 * as derived from stored outcomes; a decision without a labeled outcome
 * is excluded from the denominator, and the exclusion is stated, never
 * silently dropped.
 *
 * The one vanity metric that is BANNED on this surface: "noise removed %"
 * (synthesis §9). It measures suppression volume, not correctness — the
 * surface that earns production trust may not headline volume. Enforced
 * by tests/shadow.test.mjs: any "noise removed" or "alerts reduced %"
 * copy on S5 fails the suite.
 */

export const BANNED_VANITY_PATTERNS = [
  /noise\s*removed/i, /alerts?\s*reduced/i, /fatigue\s*reduced/i,
  /triage\s*time\s*saved/i, /pages?\s*eliminated/i,
];

/* Outcome shape (from the read contract's decision detail `outcome`):
 *   { acked: bool, acted: bool, joined_incident: bool,
 *     manually_unsuppressed: bool, labeled_at: iso } */
export function isLabeled(outcome) {
  return !!outcome && outcome.labeled_at != null;
}

export function pagePrecision(pages) {
  /* pages: [{disposition, outcome}] — disposition must be page_now */
  const sent = pages.filter(p => p.disposition === 'page_now');
  const labeled = sent.filter(p => isLabeled(p.outcome));
  const ackedActed = labeled.filter(p => p.outcome.acked && p.outcome.acted).length;
  return {
    sent: sent.length,
    labeled: labeled.length,
    unlabeled: sent.length - labeled.length,
    acked_acted: ackedActed,
    precision: labeled.length ? ackedActed / labeled.length : null,
  };
}

export function suppressionRegret(suppressions) {
  const total = suppressions.length;
  const labeled = suppressions.filter(s => isLabeled(s.outcome));
  const regretted = labeled.filter(s =>
    s.outcome.joined_incident || s.outcome.manually_unsuppressed).length;
  return {
    suppressions: total,
    labeled: labeled.length,
    unlabeled: total - labeled.length,
    regretted,
    regret: labeled.length ? regretted / labeled.length : null,
  };
}

/* Agreement: shadow disposition vs the actual paging outcome.
 * A "disagreement" is a row with evidence on both sides (Appendix A
 * <ShadowDiffTable>) — never a bare number. */
export function agreement(rows) {
  /* rows: [{id, shadow_disposition, actual_disposition}] */
  const n = rows.length;
  const agree = rows.filter(r => r.shadow_disposition === r.actual_disposition).length;
  const disagreements = rows.filter(r => r.shadow_disposition !== r.actual_disposition);
  return { n, agree, agreeRate: n ? agree / n : null, disagreements };
}

/* A disagreement row is renderable only with bilateral evidence links —
 * an agreement number without drill-down is a vanity metric in disguise. */
export function disagreementKind(r) {
  if (r.shadow_disposition === 'suppress' && r.actual_disposition === 'page_now')
    return 'shadow-suppressed · actually-paged';
  if (r.shadow_disposition === 'page_now' && r.actual_disposition !== 'page_now')
    return 'shadow-paged · actually-stood-down';
  return 'disposition drift';
}
