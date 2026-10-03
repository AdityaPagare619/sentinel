#!/usr/bin/env python3
"""gen_contract.py — generate platform/ui/assets/contract.js from the FROZEN
platform contract (platform/contracts/openapi.yaml).

F8 made concrete: the console's typed boundary is GENERATED, never hand-written.
The console cannot drift from the platform: contract.js carries the source
sha256, and tests/conformance.py fails the build if the checked-in file does
not match a fresh generation from the current openapi.yaml.

Run: python3 platform/ui/tools/gen_contract.py   (from repo root)
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SPEC_PATH = ROOT / "platform" / "contracts" / "openapi.yaml"
OUT_PATH = ROOT / "platform" / "ui" / "assets" / "contract.js"

import yaml  # noqa: E402  (pyyaml — already a conformance.py dependency)


def resolve(schemas, ref):
    assert ref.startswith("#/components/schemas/"), ref
    return schemas[ref.split("/")[-1]]


def flatten_required(schemas, schema):
    """required field names, following $ref and allOf."""
    if "$ref" in schema:
        return flatten_required(schemas, resolve(schemas, schema["$ref"]))
    req = set()
    for part in schema.get("allOf", []):
        req |= flatten_required(schemas, part)
    req |= set(schema.get("required", []))
    return req


def flatten_properties(schemas, schema):
    """property names, following $ref and allOf."""
    if "$ref" in schema:
        return flatten_properties(schemas, resolve(schemas, schema["$ref"]))
    props = {}
    for part in schema.get("allOf", []):
        props.update(flatten_properties(schemas, part))
    props.update(schema.get("properties", {}))
    return props


def main():
    spec = yaml.safe_load(SPEC_PATH.read_text())
    schemas = spec["components"]["schemas"]
    sha = hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest()

    enums = {}
    for name in ("Severity", "DispositionAction", "Team", "DataSource"):
        if name in schemas and "enum" in schemas[name]:
            enums[name] = list(schemas[name]["enum"])

    summary_req = sorted(flatten_required(schemas, {"$ref": "#/components/schemas/DecisionSummary"}))
    detail_req = sorted(flatten_required(schemas, {"$ref": "#/components/schemas/DecisionDetail"}))
    river_fields = sorted(flatten_properties(schemas, {"$ref": "#/components/schemas/DecisionSummary"}))

    # The closed filter grammar: the ONLY decision fields the river's
    # filters/sorts may reference. Field pins are display bindings and are
    # NEVER members of this set (the display-only invariant, enforced in
    # tests/pins.test.mjs).
    filterable = ["id", "time", "service", "severity", "team", "disposition",
                  "reason", "fingerprint", "confidence"]

    body = f"""/* contract.js — THE FIXED TYPED DECISION CONTRACT, generated.
 *
 * GENERATED FROM platform/contracts/openapi.yaml — DO NOT EDIT BY HAND.
 * Regenerate: python3 platform/ui/tools/gen_contract.py
 * Source sha256: {sha}
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
export const CONTRACT_SOURCE_SHA = '{sha}';

export const ENUMS = {json.dumps(enums, indent=2)};

export const DECISION_SUMMARY_REQUIRED = {json.dumps(summary_req)};
export const DECISION_DETAIL_REQUIRED = {json.dumps(detail_req)};

/* Closed river-row vocabulary: every field the river may render. */
export const RIVER_FIELDS = {json.dumps(river_fields)};

/* Closed filter grammar: the only decision fields filters/sorts may reference. */
export const FILTERABLE_FIELDS = {json.dumps(filterable)};
export function isQueryableField(name) {{
  return FILTERABLE_FIELDS.includes(name);
}}

/* §4.2 — confidence renders at the engine's quantization. More digits is
 * false precision: it implies a measurement resolution that does not exist. */
export const CONFIDENCE_DP = 2;

/* ---------- runtime Postel validation at the API boundary ----------
 * Liberal in what we accept (unknown extra keys are tolerated), strict on
 * what the contract guarantees (required fields + enum membership).
 * A row that fails validation is NEVER rendered as a decision — the surface
 * renders the honest contract-drift state instead (components.driftRow). */
function _enumCheck(d, errors) {{
  for (const [field, enumName] of [['severity', 'Severity'], ['disposition', 'DispositionAction'], ['team', 'Team']]) {{
    const v = d[field];
    if (v !== undefined && v !== null && !(ENUMS[enumName] || []).includes(v)) {{
      errors.push(`contract drift: ${{field}}=${{JSON.stringify(v)}} is not in the frozen ${{enumName}} enum`);
    }}
  }}
}}

export function validateDecisionSummary(d) {{
  const errors = [];
  if (d === null || typeof d !== 'object' || Array.isArray(d)) {{
    return {{ ok: false, errors: ['contract drift: decision is not an object'] }};
  }}
  for (const k of DECISION_SUMMARY_REQUIRED) {{
    if (d[k] === undefined) errors.push(`contract drift: missing required field '${{k}}'`);
  }}
  _enumCheck(d, errors);
  if (d.confidence !== undefined && (typeof d.confidence !== 'number' || d.confidence < 0 || d.confidence > 1)) {{
    errors.push(`contract drift: confidence=${{JSON.stringify(d.confidence)}} is not a number in [0,1]`);
  }}
  if (d.id !== undefined && !Number.isInteger(d.id)) {{
    errors.push(`contract drift: id=${{JSON.stringify(d.id)}} is not an integer`);
  }}
  return {{ ok: errors.length === 0, errors }};
}}

export function validateDecisionDetail(d) {{
  const base = validateDecisionSummary(d);
  const errors = [...base.errors];
  if (d && typeof d === 'object' && !Array.isArray(d)) {{
    if (d.alert === undefined || d.alert === null || typeof d.alert !== 'object') {{
      errors.push(`contract drift: missing required field 'alert' (alert context as received)`);
    }}
    if (d.audit === undefined) {{
      errors.push(`contract drift: missing required field 'audit'`);
    }}
  }}
  return {{ ok: errors.length === 0, errors }};
}}
"""
    OUT_PATH.write_text(body)
    print(f"wrote {OUT_PATH} ({len(body)} bytes) from {SPEC_PATH.name} sha256:{sha[:12]}…")


if __name__ == "__main__":
    sys.exit(main())
