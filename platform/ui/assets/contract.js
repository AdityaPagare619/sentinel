/* contract.js — THE FIXED TYPED DECISION CONTRACT, generated.
 *
 * GENERATED FROM platform/contracts/openapi.yaml — DO NOT EDIT BY HAND.
 * Regenerate: python3 platform/ui/tools/gen_contract.py
 * Source sha256: 006e77c3841d957ffd83d4b17e3a537c0ea6454a77f8d369818d2cf9a3d8f28f
 *
 * This module is the console's typed boundary (F8). The river, views, filters,
 * and sorts may reference ONLY the fields enumerated here. Vendor payload
 * (decision.alert) is arbitrary by contract and is rendered by the generic
 * payload renderer (payload.js) — it is never part of this vocabulary.
 *
 * Honest gap (synthesis §7): the frozen contract v1.0.0 does not carry
 * freshness_state or policy_version on DecisionSummary. The console renders
 * that absence explicitly rather than inventing values (P3).
 */

export const CONTRACT_VERSION = '1.0.0';
export const CONTRACT_SOURCE_SHA = '006e77c3841d957ffd83d4b17e3a537c0ea6454a77f8d369818d2cf9a3d8f28f';

export const ENUMS = {
  "Severity": [
    "p1_critical",
    "p2_high",
    "p3_medium",
    "p4_low",
    "known_noise",
    "cannot_determine"
  ],
  "DispositionAction": [
    "page_now",
    "page_business_hours",
    "suppress",
    "passthrough"
  ],
  "Team": [
    "platform",
    "network",
    "data",
    "product_backend",
    "security",
    "cannot_determine"
  ],
  "DataSource": [
    "synthetic",
    "shadow",
    "production"
  ]
};

export const DECISION_SUMMARY_REQUIRED = ["confidence", "disposition", "fingerprint", "id", "input_sha256", "jev_model", "latency_ms", "prob_map", "reason", "service", "severity", "team", "time", "title"];
export const DECISION_DETAIL_REQUIRED = ["alert", "audit", "confidence", "disposition", "fingerprint", "id", "input_sha256", "jev_model", "latency_ms", "prob_map", "reason", "service", "severity", "team", "time", "title"];

/* Closed river-row vocabulary: every field the river may render. */
export const RIVER_FIELDS = ["alert_id", "confidence", "disposition", "fingerprint", "id", "input_sha256", "jev_model", "latency_ms", "prob_map", "reason", "service", "severity", "shadow", "team", "time", "title"];

/* Closed filter grammar: the only decision fields filters/sorts may reference. */
export const FILTERABLE_FIELDS = ["id", "time", "service", "severity", "team", "disposition", "reason", "fingerprint", "confidence"];
export function isQueryableField(name) {
  return FILTERABLE_FIELDS.includes(name);
}

/* §4.2 — confidence renders at the engine's quantization. More digits is
 * false precision: it implies a measurement resolution that does not exist. */
export const CONFIDENCE_DP = 2;

/* ---------- runtime Postel validation at the API boundary ----------
 * Liberal in what we accept (unknown extra keys are tolerated), strict on
 * what the contract guarantees (required fields + enum membership).
 * A row that fails validation is NEVER rendered as a decision — the surface
 * renders the honest contract-drift state instead (components.driftRow). */
function _enumCheck(d, errors) {
  for (const [field, enumName] of [['severity', 'Severity'], ['disposition', 'DispositionAction'], ['team', 'Team']]) {
    const v = d[field];
    if (v !== undefined && v !== null && !(ENUMS[enumName] || []).includes(v)) {
      errors.push(`contract drift: ${field}=${JSON.stringify(v)} is not in the frozen ${enumName} enum`);
    }
  }
}

export function validateDecisionSummary(d) {
  const errors = [];
  if (d === null || typeof d !== 'object' || Array.isArray(d)) {
    return { ok: false, errors: ['contract drift: decision is not an object'] };
  }
  for (const k of DECISION_SUMMARY_REQUIRED) {
    if (d[k] === undefined) errors.push(`contract drift: missing required field '${k}'`);
  }
  _enumCheck(d, errors);
  if (d.confidence !== undefined && (typeof d.confidence !== 'number' || d.confidence < 0 || d.confidence > 1)) {
    errors.push(`contract drift: confidence=${JSON.stringify(d.confidence)} is not a number in [0,1]`);
  }
  if (d.id !== undefined && !Number.isInteger(d.id)) {
    errors.push(`contract drift: id=${JSON.stringify(d.id)} is not an integer`);
  }
  return { ok: errors.length === 0, errors };
}

export function validateDecisionDetail(d) {
  const base = validateDecisionSummary(d);
  const errors = [...base.errors];
  if (d && typeof d === 'object' && !Array.isArray(d)) {
    if (d.alert === undefined || d.alert === null || typeof d.alert !== 'object') {
      errors.push(`contract drift: missing required field 'alert' (alert context as received)`);
    }
    if (d.audit === undefined) {
      errors.push(`contract drift: missing required field 'audit'`);
    }
  }
  return { ok: errors.length === 0, errors };
}
