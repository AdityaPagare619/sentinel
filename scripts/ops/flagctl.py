#!/usr/bin/env python3
"""flagctl — the only sanctioned writer of flags.json.

Release-control surface for Sentinel (ops/devops-foundation.md §2.1):
atomic, versioned, audited flag flips with a safety asymmetry on the kill
switch (ON = one human; OFF = two named humans + a reason).

    flagctl.py validate --config-dir DIR
    flagctl.py show     --config-dir DIR
    flagctl.py set <flag> <value> --by NAME --reason "..." [--config-dir DIR]
                                   [--two-person "a,b"]
    flagctl.py rollback --by NAME --reason "..." [--config-dir DIR]

After a successful flip, trigger the running receiver to pick it up:
    curl -X POST -H "Authorization: Bearer $SENTINEL_HEALTH_TOKEN" \\
        http://<host>:<port>/-/reload
or send SIGHUP. Then confirm the new config_generation on /healthz.
"""

from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import json
import os
import shutil
import sys
import tempfile

FLAGS_VERSION = 1
KEEP_GENERATIONS = 5

# The flag contract (devops-foundation.md §2.1). Type 1: changing this schema
# is a contract change, reviewed like one.
FLAG_DEFS = {
    "global_kill_switch":   {"type": "bool"},
    "suppress_enabled":     {"type": "bool"},
    "shadow_mode":          {"type": "bool"},
    "canary_severity_bands": {"type": "list"},
    "canary_services":      {"type": "list"},
}

# Flags whose OFF-transition needs two named humans (safety asymmetry).
TWO_PERSON_OFF = {"global_kill_switch"}


class FlagError(Exception):
    pass


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _flags_path(config_dir: str) -> str:
    return os.path.join(config_dir, "flags.json")


def _gen_dir(config_dir: str) -> str:
    return os.path.join(config_dir, "flags-generations")


def load_flags(config_dir: str) -> dict:
    path = _flags_path(config_dir)
    if not os.path.exists(path):
        raise FlagError(f"flags.json not found in {config_dir} "
                        f"(run scripts/ops/env-bootstrap.sh)")
    with open(path) as f:
        data = json.load(f)
    validate_data(data, path)
    return data


def validate_data(data: dict, path: str = "flags.json") -> None:
    if not isinstance(data, dict):
        raise FlagError(f"{path}: top-level must be an object")
    if data.get("version") != FLAGS_VERSION:
        raise FlagError(f"{path}: version must be {FLAGS_VERSION}, "
                        f"got {data.get('version')!r}")
    flags = data.get("flags")
    if not isinstance(flags, dict):
        raise FlagError(f"{path}: 'flags' must be an object")
    unknown = sorted(set(flags) - set(FLAG_DEFS))
    if unknown:
        raise FlagError(f"{path}: unknown flags {unknown} "
                        f"(contract: {sorted(FLAG_DEFS)})")
    missing = sorted(set(FLAG_DEFS) - set(flags))
    if missing:
        raise FlagError(f"{path}: missing flags {missing}")
    for name, spec in flags.items():
        want = FLAG_DEFS[name]["type"]
        if not isinstance(spec, dict) or "value" not in spec:
            raise FlagError(f"{path}: flag {name!r} must be an object "
                            f"with a 'value'")
        v = spec["value"]
        if want == "bool" and not isinstance(v, bool):
            raise FlagError(f"{path}: flag {name!r} must be bool, "
                            f"got {type(v).__name__}")
        if want == "list" and not isinstance(v, list):
            raise FlagError(f"{path}: flag {name!r} must be a list, "
                            f"got {type(v).__name__}")


def _parse_value(flag: str, raw: str):
    want = FLAG_DEFS[flag]["type"]
    if want == "bool":
        low = raw.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise FlagError(f"flag {flag!r} is bool; got {raw!r}")
    # list: JSON array or comma-separated
    raw = raw.strip()
    if raw.startswith("["):
        try:
            v = json.loads(raw)
        except json.JSONDecodeError as e:
            raise FlagError(f"flag {flag!r}: bad JSON list: {e}")
        if not isinstance(v, list):
            raise FlagError(f"flag {flag!r}: must be a list")
        return [str(x) for x in v]
    return [s.strip() for s in raw.split(",") if s.strip()]


def _sha256_of(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True).encode()).hexdigest()


def _atomic_write(path: str, data: dict) -> None:
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".flags.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)  # atomic on POSIX
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _archive_generation(config_dir: str, data: dict) -> None:
    """Keep the last KEEP_GENERATIONS validated flag files (last-good chain)."""
    gdir = _gen_dir(config_dir)
    os.makedirs(gdir, exist_ok=True)
    digest = _sha256_of(data)[:12]
    dest = os.path.join(gdir, f"flags-{digest}.json")
    shutil.copy2(_flags_path(config_dir), dest)
    gens = sorted(os.listdir(gdir))
    for old in gens[:-KEEP_GENERATIONS]:
        os.unlink(os.path.join(gdir, old))


def _audit(config_dir: str, entry: dict) -> None:
    entry = {"at": _utcnow(), **entry}
    with open(os.path.join(config_dir, "flags-changes.jsonl"), "a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def cmd_validate(args) -> int:
    try:
        data = load_flags(args.config_dir)
    except FlagError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(f"ok: flags.json valid (version {data['version']}, "
          f"{len(data['flags'])} flags)")
    return 0


def cmd_show(args) -> int:
    try:
        data = load_flags(args.config_dir)
    except FlagError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    for name in sorted(data["flags"]):
        spec = data["flags"][name]
        print(f"{name} = {json.dumps(spec['value'])}")
    print(f"changed_by={data.get('changed_by')} "
          f"changed_at={data.get('changed_at')}")
    return 0


def cmd_set(args) -> int:
    try:
        data = load_flags(args.config_dir)
    except FlagError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    flag = args.flag
    if flag not in FLAG_DEFS:
        print(f"FAIL: unknown flag {flag!r} (contract: "
              f"{sorted(FLAG_DEFS)})", file=sys.stderr)
        return 1
    try:
        new_value = _parse_value(flag, args.value)
    except FlagError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    old_value = data["flags"][flag]["value"]

    # Safety asymmetry: turning the kill switch OFF needs two named humans.
    if (flag in TWO_PERSON_OFF and old_value is True
            and new_value is False):
        people = [p.strip() for p in (args.two_person or "").split(",")
                  if p.strip()]
        if len(people) < 2:
            print("FAIL: turning global_kill_switch OFF requires "
                  "--two-person \"name1,name2\" (safety asymmetry: ON is one "
                  "human, OFF is two)", file=sys.stderr)
            return 1
        by_note = f"{args.by} (+{','.join(people[1:])})"
    else:
        by_note = args.by

    if old_value == new_value:
        print(f"ok: {flag} already {json.dumps(old_value)} (no-op)")
        return 0

    new_data = copy.deepcopy(data)
    new_data["flags"][flag]["value"] = new_value
    new_data["changed_by"] = by_note
    new_data["changed_at"] = _utcnow()
    new_data["sha256"] = _sha256_of(new_data["flags"])

    try:
        _archive_generation(args.config_dir, data)  # archive the OLD one
        _atomic_write(_flags_path(args.config_dir), new_data)
    except OSError as e:
        print(f"FAIL: write failed, nothing changed: {e}", file=sys.stderr)
        return 1
    _audit(args.config_dir, {
        "action": "set", "flag": flag,
        "old": old_value, "new": new_value,
        "by": by_note, "reason": args.reason,
    })
    print(f"ok: {flag}: {json.dumps(old_value)} -> {json.dumps(new_value)}")
    print("next: POST /-/reload (or SIGHUP), then confirm the new "
          "config_generation on /healthz")
    return 0


def cmd_rollback(args) -> int:
    gdir = _gen_dir(args.config_dir)
    if not os.path.isdir(gdir):
        print("FAIL: no archived generations to roll back to",
              file=sys.stderr)
        return 1
    gens = sorted(os.listdir(gdir))
    if not gens:
        print("FAIL: no archived generations to roll back to",
              file=sys.stderr)
        return 1
    prev = os.path.join(gdir, gens[-1])
    try:
        data = load_flags(args.config_dir)  # validates current first
        with open(prev) as f:
            prev_data = json.load(f)
        validate_data(prev_data, prev)
    except FlagError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    try:
        _archive_generation(args.config_dir, data)
        shutil.copy2(prev, _flags_path(args.config_dir))
    except OSError as e:
        print(f"FAIL: rollback write failed: {e}", file=sys.stderr)
        return 1
    _audit(args.config_dir, {
        "action": "rollback", "restored": gens[-1],
        "by": args.by, "reason": args.reason,
    })
    print(f"ok: rolled back to {gens[-1]}")
    print("next: POST /-/reload (or SIGHUP), then confirm on /healthz")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="flagctl — flags.json manager")
    # Accept --config-dir either before or after the subcommand.
    argv = list(sys.argv[1:] if argv is None else argv)
    config_dir = "./sentinel-config"
    rest = []
    it = iter(range(len(argv)))
    i = 0
    while i < len(argv):
        if argv[i] == "--config-dir" and i + 1 < len(argv):
            config_dir = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    ap.add_argument("--config-dir", default=config_dir)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="validate flags.json against the contract")
    sub.add_parser("show", help="print current flag values")

    p_set = sub.add_parser("set", help="flip a flag (audited, atomic)")
    p_set.add_argument("flag")
    p_set.add_argument("value")
    p_set.add_argument("--by", required=True, help="operator name")
    p_set.add_argument("--reason", required=True, help="ticket/incident ref")
    p_set.add_argument("--two-person", default="",
                       help='"name1,name2" — required to turn the kill '
                            'switch OFF')

    p_rb = sub.add_parser("rollback", help="restore previous flag generation")
    p_rb.add_argument("--by", required=True)
    p_rb.add_argument("--reason", required=True)

    args = ap.parse_args(rest)
    args.config_dir = config_dir
    try:
        if args.cmd == "validate":
            return cmd_validate(args)
        if args.cmd == "show":
            return cmd_show(args)
        if args.cmd == "set":
            return cmd_set(args)
        if args.cmd == "rollback":
            return cmd_rollback(args)
    except FlagError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
