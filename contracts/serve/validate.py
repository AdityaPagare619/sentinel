#!/usr/bin/env python3
"""Validate C7 serve fixtures + api-v1.v0.1.yaml structure. Stdlib only.

Two halves:
1. Fixture validation against the DecisionSummary / ChainResponse schema
   subset, embedded here as Python dicts. These MUST be kept in sync with
   api-v1.v0.1.yaml (see README "machine-checkable"); the T+0 build's
   tests/contracts/ skeleton (qa-2) graduates this to full OpenAPI validation.
2. YAML contract checks against the PARSED document (yaml.safe_load), never
   against the raw text: every required /api/v1 path exists, every old
   /api/* path is marked deprecated:true with Deprecation/Sunset/Link
   headers documented, and no path other than /api/v1/integrations/*
   is a write.

Parse gate (fail-closed): the contract is loaded with yaml.safe_load before
any check runs. If PyYAML is unavailable the gate refuses to validate —
validating a YAML contract by text grep is how a malformed contract passes
silently (the line-336 flow-mapping bug was exactly this class). If the
document raises yaml.YAMLError the validator fails loudly with the parser's
message. No text-grep fallback exists, by design. Imports at module level
are stdlib-only; the yaml import is lazy and its absence is a hard FAIL,
not a fallback.

Usage: python3 contracts/serve/validate.py   (exit 0 = pass)
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
YAML_PATH = HERE / "api-v1.v0.1.yaml"

DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")

# --- Schema subset, kept in sync with api-v1.v0.1.yaml ---------------------
DECISION_SUMMARY = {
    "required": ["id", "time", "title", "service", "severity", "team",
                 "disposition", "confidence", "reason", "prob_map",
                 "fingerprint", "input_sha256", "jev_model", "latency_ms",
                 "trace_id", "freshness_state", "freshness_reason",
                 "policy_version"],
    "patterns": {"fingerprint": r"^[0-9a-f]{16}$",
                 "input_sha256": r"^[0-9a-f]{64}$",
                 "trace_id": r"^[0-9a-f]{32}$"},
    "enums": {"freshness_state": ["fresh", "stale", "degraded", "unknown"],
              "disposition": ["passthrough", "page_now", "page_business_hours",
                              "suppress", "folded"]},
    "datetimes": ["time", "freshness_as_of"],
}

CHAIN_EVENT = {
    "required": ["cursor", "event_id", "ts", "actor", "type", "alert_id",
                 "fingerprint", "episode_id", "outbox_id", "trace_id",
                 "body", "prev_hash", "row_hash"],
    "patterns": {"event_id": r"^[0-9a-f]{32}$",
                 "fingerprint": r"^[0-9a-f]{16}$",
                 "trace_id": r"^[0-9a-f]{32}$",
                 "prev_hash": r"^[0-9a-f]{64}$",
                 "row_hash": r"^[0-9a-f]{64}$"},
    "datetimes": ["ts"],
}

CHECKPOINT_SEAL = {
    "required": ["head_seq", "head_hash", "event_count", "window_start_ts",
                 "window_end_ts", "hmac_hex", "sink_uri", "sink_push_ok"],
    "patterns": {"head_hash": r"^[0-9a-f]{64}$"},
    "datetimes": ["window_start_ts", "window_end_ts"],
}

V1_PATHS = ["/api/v1/decisions", "/api/v1/decision/{id}", "/api/v1/calibration",
            "/api/v1/simulate", "/api/v1/analytics/noise",
            "/api/v1/analytics/flips", "/api/v1/stream", "/api/v1/audit/chain",
            "/api/v1/integrations/test-page"]
DEPRECATED_ALIASES = ["/api/decisions", "/api/decision/{id}", "/api/calibration",
                      "/api/simulate", "/api/analytics/noise",
                      "/api/analytics/flips", "/api/stream"]
WRITE_METHODS = ("post", "put", "patch", "delete")
# The only paths that may carry a write method. Everything else must be
# read-only — the write surface is the trust surface.
ALLOWED_WRITES = {"/api/v1/simulate", "/api/simulate",
                   "/api/v1/integrations/test-page"}


def check_shape(doc, spec, path, errors):
    for key in spec["required"]:
        if key not in doc:
            errors.append(f"{path}: missing required {key!r}")
    for key, pat in spec.get("patterns", {}).items():
        if key in doc and doc[key] is not None and not re.search(pat, doc[key]):
            errors.append(f"{path}.{key}: {doc[key]!r} fails {pat}")
    for key, vals in spec.get("enums", {}).items():
        if key in doc and doc[key] not in vals:
            errors.append(f"{path}.{key}: {doc[key]!r} not in {vals}")
    for key in spec.get("datetimes", []):
        if key in doc and doc[key] is not None and not DT_RE.match(doc[key]):
            errors.append(f"{path}.{key}: {doc[key]!r} not ISO-8601")


def load_contract(errors):
    """Parse gate: load api-v1.v0.1.yaml or fail closed. Returns the parsed
    document, or None (errors appended). Never returns text."""
    try:
        import yaml
    except ImportError:
        errors.append("yaml: PyYAML is not installed — the parse gate cannot "
                      "load api-v1.v0.1.yaml. Refusing to validate a YAML "
                      "contract by text grep; install PyYAML or run where it "
                      "is available.")
        return None
    try:
        doc = yaml.safe_load(YAML_PATH.read_text())
    except yaml.YAMLError as e:
        errors.append(f"yaml: api-v1.v0.1.yaml FAILED TO PARSE: {e}")
        return None
    if not isinstance(doc, dict):
        errors.append(f"yaml: top-level document is {type(doc).__name__}, "
                      "expected a mapping")
        return None
    if not isinstance(doc.get("paths"), dict):
        errors.append("yaml: parsed document has no 'paths' mapping")
        return None
    return doc


def check_yaml_contract(doc, errors):
    """Half 2: structure assertions against the parsed contract document."""
    paths = doc["paths"]

    for p in V1_PATHS:
        if p not in paths:
            errors.append(f"yaml: missing required v1 path {p}")

    for p in DEPRECATED_ALIASES:
        item = paths.get(p)
        if not isinstance(item, dict):
            errors.append(f"yaml: missing deprecated alias {p}")
            continue
        ops = [v for k, v in item.items() if k in
               ("get", "post", "put", "patch", "delete") and isinstance(v, dict)]
        if not ops:
            errors.append(f"yaml: alias {p} has no operations")
            continue
        for op in ops:
            if op.get("deprecated") is not True:
                errors.append(f"yaml: alias {p} not marked deprecated:true")
            for marker in ("x-deprecation-date", "x-sunset-date"):
                if marker not in op:
                    errors.append(f"yaml: alias {p} missing {marker} "
                                  "date marker")
            headers = set()
            for resp in (op.get("responses") or {}).values():
                if isinstance(resp, dict):
                    headers.update((resp.get("headers") or {}).keys())
            for h in ("Deprecation", "Sunset", "Link"):
                if h not in headers:
                    errors.append(f"yaml: alias {p} missing documented "
                                  f"{h} header")

    # No write surface outside the allowed set
    for p, item in paths.items():
        if not isinstance(item, dict):
            continue
        writes = [m for m in WRITE_METHODS if m in item]
        if writes and p not in ALLOWED_WRITES:
            errors.append(f"yaml: unexpected write surface on {p}: "
                          f"{', '.join(writes)}")


def main():
    errors = []

    # --- Half 1: fixtures -------------------------------------------------
    ds = json.loads((HERE / "fixtures" / "decision-summary-v1.json").read_text())
    check_shape(ds["data"], DECISION_SUMMARY, "decision-summary-v1.data", errors)
    for k in ("contract_version", "data_source", "generated_at"):
        if k not in ds["meta"]:
            errors.append(f"decision-summary-v1.meta: missing {k!r}")

    ch = json.loads((HERE / "fixtures" / "audit-chain-v1.json").read_text())
    cdata = ch["data"]
    for key in ("segment_id", "events", "checkpoints", "head"):
        if key not in cdata:
            errors.append(f"audit-chain-v1.data: missing {key!r}")
    for i, ev in enumerate(cdata.get("events", [])):
        check_shape(ev, CHAIN_EVENT, f"audit-chain-v1.events[{i}]", errors)
    for i, ck in enumerate(cdata.get("checkpoints", [])):
        check_shape(ck, CHECKPOINT_SEAL, f"audit-chain-v1.checkpoints[{i}]", errors)

    # --- Half 2: YAML contract, parsed — never text-grepped ---------------
    doc = load_contract(errors)
    if doc is not None:
        check_yaml_contract(doc, errors)

    if errors:
        print("FAIL")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("OK: 2/2 fixtures valid; 9 v1 paths present; "
          f"{len(DEPRECATED_ALIASES)} deprecated aliases carry "
          "Deprecation+Sunset+Link")
    return 0


if __name__ == "__main__":
    sys.exit(main())
