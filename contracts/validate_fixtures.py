#!/usr/bin/env python3
"""Validate contract fixtures against the normative rules of each contract's
JSON Schema, using stdlib `json`/`re` only (no third-party validator deps;
the frozen stdlib-only decision applies to this lane's tooling too).

Convention: fixtures/ holds validating examples; any fixture whose filename
starts with "negative_" MUST FAIL validation (the old-shape regression
bait). Every other fixture MUST PASS. Any mismatch => nonzero exit.

The validator reads the enum vocabulary FROM the schema file itself, so the
test cannot silently drift from the contract.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")

SIMPLE_REASONS = {
    "threshold", "allowlist", "uncertain", "shadow", "dedup",
    "change_window", "storm", "storm_digest", "model_drift",
    "verified_resolve", "operator_resolve", "label_pipeline_stale",
    "commit_watchdog_trip",
}


def load(p: Path):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def err(ctx, msg):
    return f"{ctx}: {msg}"


def check_disposition(schema: dict, fx: dict, name: str) -> list[str]:
    e = []
    ctx = f"act/{name}"
    props = schema["properties"]
    # consts
    for k in ("contract", "contract_version"):
        if fx.get(k) != props[k]["const"]:
            e.append(err(ctx, f"{k} must be {props[k]['const']!r}, got {fx.get(k)!r}"))
    # required
    for k in schema["required"]:
        if k not in fx:
            e.append(err(ctx, f"missing required field {k!r}"))
    # no unknown fields
    for k in fx:
        if k not in props and not k.startswith("_"):
            e.append(err(ctx, f"unknown field {k!r} (additionalProperties: false)"))
    # action enum
    if fx.get("action") not in props["action"]["enum"]:
        e.append(err(ctx, f"action {fx.get('action')!r} not in enum"))
    # reason_code: enum + no interpolation
    rc = fx.get("reason_code")
    if rc not in props["reason_code"]["enum"]:
        e.append(err(ctx, f"reason_code {rc!r} not in closed vocabulary"))
    elif not SNAKE.match(rc):
        e.append(err(ctx, f"reason_code {rc!r} carries interpolated data (not snake_case)"))
    # reason_detail per reason_code
    det = fx.get("reason_detail", {})
    if not isinstance(det, dict):
        e.append(err(ctx, "reason_detail must be an object"))
        return e
    if rc in SIMPLE_REASONS:
        if det:
            e.append(err(ctx, f"simple reason {rc!r} must not carry reason_detail fields"))
    elif rc == "error":
        if "error_code" not in det or not SNAKE.match(str(det.get("error_code", ""))):
            e.append(err(ctx, "error requires snake_case error_code field"))
        if set(det) != {"error_code"}:
            e.append(err(ctx, f"error reason_detail has unexpected fields {sorted(det)}"))
    elif rc == "freshness_veto":
        legs = det.get("stale_legs")
        if not isinstance(legs, list) or not legs or not all(isinstance(x, str) for x in legs):
            e.append(err(ctx, "freshness_veto requires non-empty stale_legs[]"))
        if set(det) != {"stale_legs"}:
            e.append(err(ctx, f"freshness_veto reason_detail has unexpected fields {sorted(det)}"))
    elif rc == "firewall_flagged":
        ds = det.get("detectors")
        if not isinstance(ds, list) or not ds or not all(isinstance(x, str) for x in ds):
            e.append(err(ctx, "firewall_flagged requires non-empty detectors[]"))
        if set(det) - {"detectors", "evidence"}:
            e.append(err(ctx, f"firewall_flagged reason_detail has unexpected fields {sorted(det)}"))
    elif rc == "failopen":
        if det.get("step") not in (1, 2, 3):
            e.append(err(ctx, "failopen requires step in {1,2,3}"))
        if not isinstance(det.get("digest"), bool):
            e.append(err(ctx, "failopen requires boolean digest"))
        if "error_code" in det and not SNAKE.match(str(det["error_code"])):
            e.append(err(ctx, "failopen error_code must be snake_case"))
        if set(det) - {"step", "digest", "error_code"}:
            e.append(err(ctx, f"failopen reason_detail has unexpected fields {sorted(det)}"))
    elif rc == "policy_block":
        if not isinstance(det.get("policy_reason"), str) or not det["policy_reason"]:
            e.append(err(ctx, "policy_block requires policy_reason"))
        if set(det) != {"policy_reason"}:
            e.append(err(ctx, f"policy_block reason_detail has unexpected fields {sorted(det)}"))
    # evidence_refs
    er = fx.get("evidence_refs")
    if not isinstance(er, list) or not all(isinstance(x, str) for x in er):
        e.append(err(ctx, "evidence_refs must be an array of strings"))
    return e


def check_forward_receipt(schema: dict, fx: dict, name: str) -> list[str]:
    e = []
    ctx = f"observe/{name}"
    props = schema["properties"]
    for k in ("contract", "contract_version"):
        if fx.get(k) != props[k]["const"]:
            e.append(err(ctx, f"{k} must be {props[k]['const']!r}, got {fx.get(k)!r}"))
    for k in schema["required"]:
        if k not in fx:
            e.append(err(ctx, f"missing required field {k!r}"))
    for k in fx:
        if k not in props and not k.startswith("_") and k != "detail_note":
            e.append(err(ctx, f"unknown field {k!r} (additionalProperties: false)"))
    an = fx.get("attempt_no")
    if not isinstance(an, int) or isinstance(an, bool) or an < 1:
        e.append(err(ctx, f"attempt_no must be integer >= 1, got {an!r}"))
    if not isinstance(fx.get("wire_sha256"), str) or not HEX64.match(fx.get("wire_sha256", "")):
        e.append(err(ctx, "wire_sha256 must be 64 lowercase hex chars"))
    oc = fx.get("outcome")
    if oc not in props["outcome"]["enum"]:
        e.append(err(ctx, f"outcome {oc!r} not in enum"))
    sim = fx.get("simulated")
    if not isinstance(sim, bool):
        e.append(err(ctx, f"simulated must be boolean, got {sim!r}"))
    # The audit lie, killed in the validator: a simulated absorption never
    # went on the wire, so it cannot carry a wire failure outcome.
    if sim is True:
        if oc != "accepted":
            e.append(err(ctx, f"simulated:true REQUIRES outcome 'accepted', got {oc!r}"))
        if fx.get("error_class") is not None:
            e.append(err(ctx, "simulated:true MUST NOT carry error_class"))
    ch = fx.get("channel")
    if ch is not None and ch not in props["channel"]["enum"]:
        e.append(err(ctx, f"channel {ch!r} not in enum"))
    ec = fx.get("error_class")
    if oc in ("retryable", "terminal"):
        if not isinstance(ec, str) or not ec:
            e.append(err(ctx, f"outcome {oc!r} REQUIRES error_class"))
    elif oc == "accepted" and ec is not None:
        e.append(err(ctx, "accepted receipt MUST NOT carry error_class"))
    if not isinstance(fx.get("decision_id"), str) or not fx.get("decision_id"):
        e.append(err(ctx, "decision_id must be a non-empty string"))
    return e


CONTRACTS = {
    "act": ("disposition.v0.1.json", check_disposition),
    "observe": ("forward-receipt.v0.1.json", check_forward_receipt),
}


def main() -> int:
    failures = 0
    checked = 0
    for dirname, (schema_file, checker) in CONTRACTS.items():
        cdir = ROOT / dirname
        schema = load(cdir / schema_file)
        fdir = cdir / "fixtures"
        for fx_path in sorted(fdir.glob("*.json")):
            fx = load(fx_path)
            negative = fx_path.name.startswith("negative_")
            errs = checker(schema, fx, fx_path.name)
            checked += 1
            if negative:
                if errs:
                    print(f"PASS(negative, correctly rejected) {dirname}/{fx_path.name}")
                else:
                    print(f"FAIL(negative VALIDATED — contract widened!) {dirname}/{fx_path.name}")
                    failures += 1
            else:
                if errs:
                    print(f"FAIL {dirname}/{fx_path.name}")
                    for x in errs:
                        print(f"    {x}")
                    failures += 1
                else:
                    print(f"PASS {dirname}/{fx_path.name}")
    print(f"\n{checked} fixtures checked, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
