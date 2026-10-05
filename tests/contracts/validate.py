#!/usr/bin/env python3
"""Minimal JSON-Schema validator using only the Python stdlib.

Part of the qa-2 ``tests/contracts/`` skeleton (12H-PLAN §1 qa-2, F5):
one validator, one fixture convention, so seven contract domains don't
invent seven validators and the §3.3 contract-test gate dies on arrival.

Supported keyword subset (the full supported contract surface):
    type (object/array/string/integer/number/boolean/null),
    required, properties, additionalProperties (bool or sub-schema),
    enum, pattern (regex, partial-match semantics), minimum / maximum,
    items (uniform sub-schema applied to every element).

Annotation keywords are ignored: $schema, $id, $comment, title,
description, examples, default.

Any OTHER keyword (oneOf, anyOf, allOf, $ref, const, format, minItems,
patternProperties, ...) is reported as an internal SchemaError rather
than silently ignored -- a schema must never over-promise validation it
does not perform. Contract authors: extend this file (with tests) or
write your schema inside the subset.

Pattern lineage: the "declare the subset, fail loud outside it" discipline
follows the minimal-stdlib-validator pattern documented in the wild
(sciforge-oss skills/shared-references/schemas, accessed 2026-10-05):
annotation keywords ignored, unknown keywords an internal error.

Usage:
    python3 tests/contracts/validate.py <schema.json> <instance.json> [...]
Exit 0 when every instance validates; exit 1 on any failure, with
JSON-pointer-ish paths for each violation.
"""

import json
import re
import sys

_ANNOTATION_KEYWORDS = {
    "$schema", "$id", "$comment", "title", "description", "examples", "default",
}

_SUPPORTED_KEYWORDS = {
    "type", "required", "properties", "additionalProperties",
    "enum", "pattern", "minimum", "maximum", "items",
}

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,   # bool excluded explicitly (bool is a subclass of int)
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


class SchemaError(Exception):
    """The schema itself uses something outside the supported subset."""


class ValidationError:
    def __init__(self, path, message):
        self.path = path
        self.message = message

    def __str__(self):
        return f"{self.path}: {self.message}"


def check_schema(schema, path="$"):
    """Reject schemas that use keywords outside the supported subset."""
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: schema must be an object")
    for keyword in schema:
        if keyword in _ANNOTATION_KEYWORDS:
            continue
        if keyword not in _SUPPORTED_KEYWORDS:
            raise SchemaError(
                f"{path}: unsupported keyword {keyword!r} -- "
                "extend validate.py or rewrite the schema inside the subset"
            )
    for prop, subschema in schema.get("properties", {}).items():
        check_schema(subschema, f"{path}/properties/{prop}")
    if "items" in schema:
        check_schema(schema["items"], f"{path}/items")
    ap = schema.get("additionalProperties")
    if isinstance(ap, dict):
        check_schema(ap, f"{path}/additionalProperties")


def _type_matches(instance, typename):
    pytype = _TYPES[typename]
    if typename == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if typename == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    return isinstance(instance, pytype)


def validate(instance, schema, path="$"):
    """Return a list of ValidationError (empty == valid)."""
    errors = []

    if "type" in schema and not _type_matches(instance, schema["type"]):
        errors.append(ValidationError(
            path,
            f"expected type {schema['type']!r}, got "
            f"{type(instance).__name__} ({instance!r:.60})",
        ))
        return errors  # further checks are meaningless on a type mismatch

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(ValidationError(
            path, f"{instance!r:.60} not in enum {schema['enum']!r}"))

    if "pattern" in schema and isinstance(instance, str):
        if not re.search(schema["pattern"], instance):
            errors.append(ValidationError(
                path, f"{instance!r:.60} does not match pattern "
                      f"{schema['pattern']!r}"))

    if "minimum" in schema and isinstance(instance, (int, float)) \
            and not isinstance(instance, bool) and instance < schema["minimum"]:
        errors.append(ValidationError(
            path, f"{instance!r} below minimum {schema['minimum']!r}"))
    if "maximum" in schema and isinstance(instance, (int, float)) \
            and not isinstance(instance, bool) and instance > schema["maximum"]:
        errors.append(ValidationError(
            path, f"{instance!r} above maximum {schema['maximum']!r}"))

    if isinstance(instance, dict):
        for prop in schema.get("required", []):
            if prop not in instance:
                errors.append(ValidationError(
                    path, f"missing required property {prop!r}"))
        props = schema.get("properties", {})
        ap = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child = f"{path}/{key}"
            if key in props:
                errors.extend(validate(value, props[key], child))
            elif ap is False:
                errors.append(ValidationError(
                    path, f"additional property {key!r} not allowed"))
            elif isinstance(ap, dict):
                errors.extend(validate(value, ap, child))

    if isinstance(instance, list) and "items" in schema:
        for i, item in enumerate(instance):
            errors.extend(validate(item, schema["items"], f"{path}/{i}"))

    return errors


def main(argv):
    if len(argv) < 3:
        print("usage: validate.py <schema.json> <instance.json> [...]",
              file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        schema = json.load(f)
    try:
        check_schema(schema)
    except SchemaError as e:
        print(f"SCHEMA ERROR: {e}", file=sys.stderr)
        return 2
    failed = False
    for instance_path in argv[2:]:
        with open(instance_path, encoding="utf-8") as f:
            instance = json.load(f)
        errors = validate(instance, schema)
        if errors:
            failed = True
            print(f"INVALID {instance_path}:")
            for e in errors:
                print(f"  {e}")
        else:
            print(f"VALID   {instance_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
