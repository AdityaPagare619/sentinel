#!/usr/bin/env python3
"""Stdlib-only JSON Schema (draft 2020-12 subset) validator for Sentinel contracts.

Validates every fixture under contracts/*/fixtures/<schema-name>/*.json against
its sibling schema file contracts/*/<schema-name>.v0.1.json, and sanity-checks
the schema files themselves (required metadata, fixture count >= 2).

Usage:
    python3 contracts/validate_contracts.py [contracts-dir]

Exit 0 iff every fixture validates and every schema carries its metadata.
Supported keywords: type (incl. type arrays), enum, const, required,
properties, additionalProperties, items, minItems, maxItems, minLength,
maxLength, pattern, minimum, maximum, exclusiveMinimum, exclusiveMaximum,
anyOf, oneOf, allOf, not, $ref (local "#/$defs/..." and "#"), format:
date-time. Annotation keywords ($schema, $id, title, description, default,
examples, x-*) are ignored.

Deliberate simplifications (documented, not hidden):
- format is assertion-enforced for "date-time" only (the contract depends on
  ISO-8601 timestamps); other formats are ignored.
- enum uses Python equality (1 == True edge case accepted as equal).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ANNOTATIONS = {
    "$schema", "$id", "$comment", "title", "description", "default",
    "examples", "deprecated", "readOnly", "writeOnly",
}

VALIDATORS = {
    "type", "enum", "const", "required", "properties", "additionalProperties",
    "items", "minItems", "maxItems", "minLength", "maxLength", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "anyOf",
    "oneOf", "allOf", "not", "$ref", "$defs", "definitions", "format",
}


def _type_ok(value, tname):
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(tname, False)


def _check_datetime(value):
    try:
        v = value.replace("Z", "+00:00") if value.endswith("Z") else value
        datetime.fromisoformat(v)
        return True
    except (ValueError, AttributeError):
        return False


def _resolve(ref, root, defs):
    if ref == "#":
        return root
    if ref.startswith("#/$defs/"):
        name = ref[len("#/$defs/"):]
        if name in defs:
            return defs[name]
        raise KeyError(f"unresolvable $ref target: {ref}")
    raise KeyError(f"unsupported $ref (only local #/$defs/*): {ref}")


def validate(instance, schema, root=None, path="$"):
    """Return a list of error strings; empty means valid."""
    root = root if root is not None else schema
    defs = root.get("$defs", {})
    if "$ref" in schema:
        return validate(instance, _resolve(schema["$ref"], root, defs), root, path)
    errors = []

    if "type" in schema:
        tnames = schema["type"]
        if isinstance(tnames, str):
            tnames = [tnames]
        if not any(_type_ok(instance, t) for t in tnames):
            errors.append(f"{path}: expected type {tnames}, got {type(instance).__name__}")
            return errors  # further keyword checks are meaningless on wrong type

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']!r}")
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: {instance!r} != const {schema['const']!r}")

    if "not" in schema:
        if not validate(instance, schema["not"], root, path):
            errors.append(f"{path}: instance matches forbidden 'not' schema")

    if isinstance(instance, dict):
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: missing required property {req!r}")
        props = schema.get("properties", {})
        for key, subschema in props.items():
            if key in instance:
                errors.extend(validate(instance[key], subschema, root, f"{path}.{key}"))
        if "additionalProperties" in schema:
            ap = schema["additionalProperties"]
            for key in instance:
                if key not in props:
                    if ap is False:
                        errors.append(f"{path}: additional property {key!r} not allowed")
                    elif isinstance(ap, dict):
                        errors.extend(validate(instance[key], ap, root, f"{path}.{key}"))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: array has {len(instance)} items, minItems={schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: array has {len(instance)} items, maxItems={schema['maxItems']}")
        if "items" in schema:
            for i, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], root, f"{path}[{i}]"))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: string shorter than minLength={schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string longer than maxLength={schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: string does not match pattern {schema['pattern']!r}")
        if schema.get("format") == "date-time" and not _check_datetime(instance):
            errors.append(f"{path}: {instance!r} is not a valid date-time")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: {instance} <= exclusiveMinimum {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: {instance} >= exclusiveMaximum {schema['exclusiveMaximum']}")

    for kw in ("allOf",):
        for subschema in schema.get(kw, []):
            errors.extend(validate(instance, subschema, root, path))
    if "anyOf" in schema:
        if all(validate(instance, s, root, path) for s in schema["anyOf"]):
            errors.append(f"{path}: instance matches none of anyOf")
    if "oneOf" in schema:
        matches = sum(1 for s in schema["oneOf"] if not validate(instance, s, root, path))
        if matches != 1:
            errors.append(f"{path}: instance matches {matches} of oneOf (need exactly 1)")

    unknown = set(schema) - VALIDATORS - ANNOTATIONS
    if unknown and not all(k.startswith("x-") for k in unknown):
        errors.append(f"{path}: schema uses unsupported keywords {sorted(unknown)} — "
                      "extend the validator or remove them")
    return errors


def check_schema_file(path):
    """Metadata sanity checks on the schema file itself. Returns error list."""
    errors = []
    try:
        schema = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON: {exc}"]
    for key in ("$id", "title", "x-contract", "x-version", "x-stability"):
        if key not in schema:
            errors.append(f"{path}: missing metadata key {key!r}")
    if schema.get("x-stability") not in ("draft", "stable", "deprecated"):
        errors.append(f"{path}: x-stability must be draft|stable|deprecated")
    return errors


def main(argv):
    base = Path(argv[1]) if len(argv) > 1 else Path(__file__).parent
    failures = 0
    checked = 0
    for schema_file in sorted(base.rglob("*.v0.1.json")):
        # schema name = stem without the version suffix, e.g. "pd-trigger"
        name = schema_file.name[: -len(".v0.1.json")]
        fixture_dir = schema_file.parent / "fixtures" / name
        for err in check_schema_file(schema_file):
            print(f"SCHEMA-FAIL {err}")
            failures += 1
        try:
            schema = json.loads(schema_file.read_text())
        except json.JSONDecodeError:
            continue
        fixtures = sorted(fixture_dir.glob("*.json")) if fixture_dir.is_dir() else []
        if len(fixtures) < 2:
            print(f"SCHEMA-FAIL {schema_file}: only {len(fixtures)} fixture(s), need >= 2")
            failures += 1
        for fx in fixtures:
            checked += 1
            try:
                instance = json.loads(fx.read_text())
            except json.JSONDecodeError as exc:
                print(f"FAIL {fx.relative_to(base)}: invalid JSON: {exc}")
                failures += 1
                continue
            errs = validate(instance, schema)
            if errs:
                print(f"FAIL {fx.relative_to(base)}:")
                for e in errs:
                    print(f"    {e}")
                failures += 1
            else:
                print(f"ok   {fx.relative_to(base)}")
    print(f"\n{checked} fixture(s) checked, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
