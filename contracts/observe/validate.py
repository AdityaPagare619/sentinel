#!/usr/bin/env python3
"""Validate C5 DecisionRecord fixtures against decision-record.v0.1.json.

Stdlib only (json, re, sys, pathlib). Implements the JSON-Schema subset this
contract uses: type, required, properties, enum, const, pattern, minimum,
maximum, items, additionalProperties, format:date-time. This is the v0.1
machine-checkable gate for the contract; the T+0 build's tests/contracts/
skeleton (qa-2) will graduate it to a full validator.

Usage: python3 contracts/observe/validate.py   (exit 0 = all fixtures valid)
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = json.loads((HERE / "decision-record.v0.1.json").read_text())
FIXTURES = sorted((HERE / "fixtures").glob("*.json"))

DT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def check(node, schema, path, errors):
    if "const" in schema and node != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {node!r}")
        return
    if "enum" in schema and node not in schema["enum"]:
        errors.append(f"{path}: {node!r} not in enum {schema['enum']}")
        return
    types = schema.get("type")
    if types is not None:
        if not isinstance(types, list):
            types = [types]
        ok = False
        for t in types:
            if t == "object" and isinstance(node, dict):
                ok = True
            elif t == "array" and isinstance(node, list):
                ok = True
            elif t == "string" and isinstance(node, str):
                ok = True
            elif t == "integer" and isinstance(node, int) and not isinstance(node, bool):
                ok = True
            elif t == "number" and isinstance(node, (int, float)) and not isinstance(node, bool):
                ok = True
            elif t == "boolean" and isinstance(node, bool):
                ok = True
            elif t == "null" and node is None:
                ok = True
        if not ok:
            errors.append(f"{path}: type mismatch, expected {types}, got {type(node).__name__}")
            return
    if isinstance(node, str):
        if "pattern" in schema and not re.search(schema["pattern"], node):
            errors.append(f"{path}: {node!r} does not match {schema['pattern']}")
        if schema.get("format") == "date-time" and not DT_RE.match(node):
            errors.append(f"{path}: {node!r} is not ISO-8601 UTC")
        if "minLength" in schema and len(node) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        if "minimum" in schema and node < schema["minimum"]:
            errors.append(f"{path}: {node} < minimum {schema['minimum']}")
        if "maximum" in schema and node > schema["maximum"]:
            errors.append(f"{path}: {node} > maximum {schema['maximum']}")
    if isinstance(node, dict):
        for key in schema.get("required", []):
            if key not in node:
                errors.append(f"{path}: missing required key {key!r}")
        props = schema.get("properties", {})
        for key, val in node.items():
            if key in props:
                check(val, props[key], f"{path}.{key}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected key {key!r}")
    if isinstance(node, list) and "items" in schema:
        for i, val in enumerate(node):
            check(val, schema["items"], f"{path}[{i}]", errors)


def main():
    failures = 0
    for fixture in FIXTURES:
        errors = []
        doc = json.loads(fixture.read_text())
        check(doc, SCHEMA, "$", errors)
        if errors:
            failures += 1
            print(f"FAIL {fixture.name}")
            for e in errors:
                print(f"  - {e}")
        else:
            print(f"OK   {fixture.name}")
    # Cross-fixture invariant: trace_id must differ per request (one per L1 ingress).
    traces = [json.loads(f.read_text())["envelope"]["trace_id"] for f in FIXTURES]
    if len(set(traces)) != len(traces):
        failures += 1
        print("FAIL fixtures: duplicate trace_id across fixtures (must be one per request)")
    print(f"\n{len(FIXTURES) - failures}/{len(FIXTURES)} fixtures valid")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
