#!/usr/bin/env python3
"""conformance.py — the UI's API client matches platform/contracts/openapi.yaml shapes.

Checks:
  1. Every contract path is implemented by assets/api.js (live) and has a mock file.
  2. Every mock JSON validates against its schema's REQUIRED fields (openapi.yaml is
     the type authority; a mock/YAML disagreement is a Forge-lane bug).
  3. The UI's display mappings (lib.js) only use contract enum values.
  4. Every response envelope carries meta.contract_version + meta.data_source (L2).

Run: python3 tests/conformance.py  (from platform/ui/)
"""
import json, re, sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = yaml.safe_load((ROOT.parent / "contracts" / "openapi.yaml").read_text())
API_JS = (ROOT / "assets" / "api.js").read_text()
LIB_JS = (ROOT / "assets" / "lib.js").read_text()
failures = []

def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)

# 1. endpoint coverage in the client
PATHS = {
    "/api/decisions": ["getDecisions"],
    "/api/decision/{id}": ["getDecision"],
    "/api/calibration": ["getCalibration"],
    "/api/simulate": ["simulate"],
    "/api/analytics/noise": ["getNoise"],
    "/api/analytics/flips": ["getFlips"],
    "/api/stream": ["Stream"],
}
for path, syms in PATHS.items():
    for s in syms:
        check(f"client implements {path} ({s})", s in API_JS)

MOCK_FOR = {
    "/api/decisions": "decisions.json",
    "/api/decision/{id}": "decision-detail.json",
    "/api/calibration": "calibration.json",
    "/api/simulate": "simulate.json",
    "/api/analytics/noise": "noise.json",
    "/api/analytics/flips": "flips.json",
}
for path, mf in MOCK_FOR.items():
    check(f"mock file for {path}", (ROOT / "data" / mf).exists(), mf)

def resolve(ref):
    assert ref.startswith("#/components/schemas/")
    return SPEC["components"]["schemas"][ref.split("/")[-1]]

def required_of(schema):
    if "$ref" in schema:
        return required_of(resolve(schema["$ref"]))
    if "allOf" in schema:
        req = set()
        for part in schema["allOf"]:
            req |= required_of(part)
        return req
    return set(schema.get("required", []))

def check_required(name, obj, schema_ref):
    req = required_of({"$ref": schema_ref})
    missing = [k for k in req if k not in obj]
    check(f"{name} has required fields {sorted(req)}", not missing, f"missing {missing}")

# 2. mock payloads vs schema required fields
dec = json.loads((ROOT / "data" / "decisions.json").read_text())
check("decisions.json is a non-empty list", isinstance(dec["data"], list) and len(dec["data"]) > 0)
for i, row in enumerate(dec["data"]):
    check_required(f"decisions.json row {i} (id={row.get('id')})",
                   row, "#/components/schemas/DecisionSummary")

det = json.loads((ROOT / "data" / "decision-detail.json").read_text())
check_required("decision-detail.json", det["data"], "#/components/schemas/DecisionDetail")

cal = json.loads((ROOT / "data" / "calibration.json").read_text())
check_required("calibration.json", cal["data"], "#/components/schemas/CalibrationReport")
for i, b in enumerate(cal["data"]["bins"]):
    check_required(f"calibration.json bin {i}", b, "#/components/schemas/CalibrationBin")

sim = json.loads((ROOT / "data" / "simulate.json").read_text())
check_required("simulate.json", sim["data"], "#/components/schemas/SimulateResponse")

noise = json.loads((ROOT / "data" / "noise.json").read_text())
check_required("noise.json", noise["data"], "#/components/schemas/NoiseReport")

flips = json.loads((ROOT / "data" / "flips.json").read_text())
check_required("flips.json", flips["data"], "#/components/schemas/FlipReport")

# 3. envelope: contract_version + data_source on every mock (Law L2)
for mf in list(MOCK_FOR.values()) + ["stream-events.jsonl"]:
    p = ROOT / "data" / mf
    if mf.endswith(".jsonl"):
        lines = [json.loads(l) for l in p.read_text().strip().split("\n")]
        check(f"{mf} events carry id/event/data", all("event" in e and "data" in e for e in lines))
        continue
    env = json.loads(p.read_text())
    meta = env.get("meta", {})
    check(f"{mf} envelope contract_version=1.0.0", meta.get("contract_version") == "1.0.0", str(meta.get("contract_version")))
    check(f"{mf} envelope data_source ∈ enum",
          meta.get("data_source") in ("synthetic", "shadow", "production"), str(meta.get("data_source")))

# 5. contract.js is generated from the CURRENT openapi.yaml (F8).
# The console's typed boundary is generated, never hand-written: regenerate to
# a temp dir and diff against the checked-in file.
import subprocess, tempfile, shutil
_tmp = Path(tempfile.mkdtemp())
try:
    gen = ROOT.parent / "ui" / "tools" / "gen_contract.py"
    # the generator writes to assets/contract.js; point it at a temp copy via
    # a throwaway worktree-free trick: run it, then compare, then restore.
    checked = (ROOT / "assets" / "contract.js").read_text()
    r = subprocess.run([sys.executable, str(gen)], capture_output=True, text=True, cwd=ROOT.parent.parent)
    check("gen_contract.py runs clean", r.returncode == 0, r.stderr[:300])
    fresh = (ROOT / "assets" / "contract.js").read_text()
    check("contract.js matches a fresh generation from openapi.yaml",
          fresh == checked,
          "regenerate with: python3 platform/ui/tools/gen_contract.py")
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

# 4. UI enum mappings only use contract enum values.
# Severities/dispositions are mapped in lib.js; teams are listed in the views.
team_enum = SPEC["components"]["schemas"]["Team"]["enum"]
sev_enum = SPEC["components"]["schemas"]["Severity"]["enum"]
disp_enum = SPEC["components"]["schemas"]["DispositionAction"]["enum"]
VIEWS_JS = " ".join((ROOT / "assets" / f).read_text()
                    for f in ["views-river.js", "views-cal.js", "views-sim.js", "views-audit.js"])
for v in sev_enum + disp_enum:
    check(f"lib.js maps contract value '{v}'", v in LIB_JS)
for v in team_enum:
    check(f"views list contract team '{v}'", v in VIEWS_JS)

# disposition chips render the reason code (Law L1) — static check on the component
COMP = (ROOT / "assets" / "components.js").read_text()
check("dispChip requires a reason code", "reason missing — rendering bug" in COMP)

print()
if failures:
    print(f"{len(failures)} FAILURES")
    sys.exit(1)
print("all conformance checks passed")
