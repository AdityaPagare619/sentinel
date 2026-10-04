#!/usr/bin/env python3
"""ADR-017/D4 — scheme-v1 -> scheme-v2 fingerprint migration.

Rewrites an attested allowlist bundle (allowlist.json + proofs/
allowlist_attestations.json) for the env+cluster fingerprint scheme:

  v1: sha256("service|check|severity_in|region")
  v2: sha256("service|check|severity_in|region|env|cluster")

For every carried-over entry the script DUAL-WRITES:

  * the v2 fingerprint into the env-namespaced allowlist entries, with a
    v2 attestation marked ``re_attestation: "pending"`` (same two humans,
    same evidence — the fingerprint is a mechanical re-derivation of what
    they attested, and the pending flag forces the re-attestation ceremony
    before the window lapses);
  * the v1 fingerprint into ``legacy_v1.entries`` with a bounded window
    (``window_ends_at``), so pre-migration entries still resolve at runtime
    during the window — loudly logged as LEGACY-FINGERPRINT.

It also emits:

  * ``legacy_allowlist.json`` — ``{"window_ends_at", "entries": {v1: v2}}``
    for the Gate (SENTINEL_LEGACY_FINGERPRINTS);
  * ``re_attestation_manifest.json`` — the per-entry checklist for the two
    humans (components, original attestors, deadline).

The v1 components are UNRECOVERABLE from the hash — the script is honest
about needing them: ``--manifest`` maps each v1 fingerprint to
{service, check, severity_in, region, env, cluster} (+ team for the
security-ban taxonomy backfill). Entries missing from the manifest are
SKIPPED with a loud error (never invented, never dropped silently).

Security: every carried-over entry is re-derived through the ADR-017
security-category ban at migration time — a banned check is REFUSED, not
carried over.

Bounded window: --window-days defaults to 30, hard maximum 90; the script
refuses non-positive values. The window end is stamped into the bundle and
printed loudly.

Idempotent: re-running against an already-migrated bundle (fingerprint_scheme
== 2) is a no-op unless --force. A timestamped backup of the bundle is kept
next to the output.

Exit codes: 0 migrated (or no-op), 1 usage/config error, 2 migration
aborted (nothing was written).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

from sentinel.freshness import (SECURITY_BAN_TAXONOMY_VERSION,
                                derive_security_ban)

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sentinel.migrate_fingerprints")

SCHEME_VERSION = 2
DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 90

REQUIRED_MANIFEST_FIELDS = ("service", "check", "severity_in", "region",
                            "env", "cluster")


def v1_fingerprint(service: str, check: str, severity_in: str,
                   region: str) -> str:
    return hashlib.sha256(
        f"{service}|{check}|{severity_in}|{region}".encode("utf-8")
    ).hexdigest()[:16]


def v2_fingerprint(service: str, check: str, severity_in: str, region: str,
                   env: str, cluster: str) -> str:
    return hashlib.sha256(
        f"{service}|{check}|{severity_in}|{region}|{env}|{cluster}"
        .encode("utf-8")).hexdigest()[:16]


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def migrate(bundle_dir: str, manifest: dict, window_days: int,
            out_dir: str, force: bool = False) -> dict:
    """Run the migration. Returns a summary dict. Raises on abort."""
    allowlist_path = os.path.join(bundle_dir, "allowlist.json")
    proofs_path = os.path.join(bundle_dir, "proofs",
                               "allowlist_attestations.json")
    allowlist = _load_json(allowlist_path)
    proofs = _load_json(proofs_path)

    if allowlist.get("fingerprint_scheme", 1) == SCHEME_VERSION and not force:
        logger.info("bundle already at fingerprint_scheme=2 — nothing to do "
                    "(use --force to re-run)")
        return {"migrated": False, "reason": "already migrated"}

    window_ends_at = rfc3339(datetime.now(timezone.utc)
                             + timedelta(days=window_days))
    envs = allowlist.get("envs", {})
    if not isinstance(envs, dict):
        raise ValueError("allowlist.json 'envs' is not a mapping")

    attestations = proofs.get("entries", proofs)
    if not isinstance(attestations, dict):
        raise ValueError("allowlist_attestations 'entries' is not a mapping")

    new_envs: dict[str, dict] = {}
    new_attestations: dict[str, dict] = {}
    legacy_entries: list[str] = []
    legacy_map: dict[str, str] = {}
    re_attest: list[dict] = []
    carried = skipped = refused_ban = 0
    errors: list[str] = []

    for env, ns in envs.items():
        fps = (ns or {}).get("entries", [])
        if isinstance(fps, dict):
            fps = list(fps.keys())
        new_fps: list[str] = []
        for v1 in fps:
            comp = manifest.get(v1)
            if comp is None:
                skipped += 1
                errors.append(
                    f"entry {v1} (env {env}): no manifest components — "
                    f"SKIPPED (add it to --manifest; refusing to invent or "
                    f"silently drop)")
                logger.error("LEGACY-FINGERPRINT-SKIPPED fingerprint=%s "
                             "env=%s reason=no-manifest-components", v1, env)
                continue
            missing = [f for f in REQUIRED_MANIFEST_FIELDS if f not in comp]
            if missing:
                skipped += 1
                errors.append(
                    f"entry {v1} (env {env}): manifest missing {missing} — "
                    f"SKIPPED")
                logger.error("LEGACY-FINGERPRINT-SKIPPED fingerprint=%s "
                             "env=%s reason=missing-fields-%s", v1, env,
                             ",".join(missing))
                continue
            service = str(comp["service"])
            check = str(comp["check"])
            team = str(comp.get("team", ""))
            severity_in = str(comp["severity_in"])
            region = str(comp["region"])
            entry_env = str(comp["env"])
            cluster = str(comp["cluster"])

            # The v1 fingerprint in the bundle must actually be the v1 hash
            # of the claimed components — else the manifest is lying.
            if v1_fingerprint(service, check, severity_in, region) != v1:
                skipped += 1
                errors.append(
                    f"entry {v1} (env {env}): manifest components do not "
                    f"reproduce the v1 fingerprint — SKIPPED (manifest "
                    f"integrity failure)")
                logger.error("LEGACY-FINGERPRINT-SKIPPED fingerprint=%s "
                             "env=%s reason=manifest-integrity", v1, env)
                continue

            # ADR-017: the security ban is re-derived at migration — a banned
            # check is REFUSED, never carried over.
            banned, ban_reason = derive_security_ban(check, team)
            if banned:
                refused_ban += 1
                errors.append(f"entry {v1} (env {env}): REFUSED — {ban_reason}")
                logger.error("LEGACY-FINGERPRINT-REFUSED fingerprint=%s "
                             "env=%s reason=security-ban", v1, env)
                continue

            v2 = v2_fingerprint(service, check, severity_in, region,
                                entry_env, cluster)
            raw = attestations.get(v1, {})
            if not isinstance(raw, dict):
                raw = {}
            # Dual-write the attestation: v2 pending re-attestation (same two
            # humans, same evidence — mechanically re-derived), v1 kept as
            # inert legacy history.
            v2_att = dict(raw)
            v2_att.update({
                "fingerprint": v2,
                "env": entry_env,
                "check": check,
                "team": team,
                "security_category_ban": False,
                "re_attestation": "pending",
                "legacy_v1": False,
            })
            v1_att = dict(raw)
            v1_att.update({
                "fingerprint": v1,
                "env": entry_env,
                "check": check,
                "team": team,
                "security_category_ban": False,
                "legacy_v1": True,
            })
            new_attestations[v2] = v2_att
            new_attestations[v1] = v1_att
            new_fps.append(v2)
            legacy_entries.append(v1)
            legacy_map[v1] = v2
            carried += 1
            logger.warning(
                "LEGACY-FINGERPRINT-CARRIED-OVER scheme=v1 fingerprint=%s "
                "successor=%s env=%s service=%s check=%s "
                "window_ends_at=%s (re-attest before the window ends)",
                v1, v2, entry_env, service, check, window_ends_at)
            re_attest.append({
                "v2_fingerprint": v2,
                "v1_fingerprint": v1,
                "service": service, "check": check,
                "severity_in": severity_in, "region": region,
                "env": entry_env, "cluster": cluster, "team": team,
                "originally_attested_by": raw.get("attested_by", []),
                "originally_attested_at": raw.get("attested_at", ""),
                "re_attest_by": window_ends_at,
                "status": "pending",
            })
        new_envs[env] = {"entries": new_fps}

    if errors and carried == 0:
        raise RuntimeError("migration aborted: no entries could be carried "
                           "over:\n  " + "\n  ".join(errors))

    new_allowlist = {
        "fingerprint_scheme": SCHEME_VERSION,
        "security_ban_taxonomy": SECURITY_BAN_TAXONOMY_VERSION,
        "legacy_v1": {
            "window_ends_at": window_ends_at,
            "window_days": window_days,
            "entries": sorted(legacy_entries),
            "note": ("Scheme-v1 fingerprints resolve during the window only; "
                     "after window_ends_at they stop resolving (fail closed). "
                     "Re-attest every pending v2 entry before then."),
        },
        "envs": new_envs,
    }
    new_proofs = {"entries": new_attestations}

    os.makedirs(out_dir, exist_ok=True)
    backup_dir = out_dir + f".bak-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    if os.path.abspath(out_dir) == os.path.abspath(bundle_dir):
        shutil.copytree(bundle_dir, backup_dir)
        logger.info("backup of pre-migration bundle at %s", backup_dir)
        out_allowlist = allowlist_path
        out_proofs_dir = os.path.join(bundle_dir, "proofs")
    else:
        out_allowlist = os.path.join(out_dir, "allowlist.json")
        out_proofs_dir = os.path.join(out_dir, "proofs")
        os.makedirs(out_proofs_dir, exist_ok=True)
    _write_json(out_allowlist, new_allowlist)
    _write_json(os.path.join(out_proofs_dir, "allowlist_attestations.json"),
                new_proofs)
    _write_json(os.path.join(out_dir, "legacy_allowlist.json"), {
        "window_ends_at": window_ends_at,
        "entries": legacy_map,
        "note": ("Gate wiring: SENTINEL_LEGACY_FINGERPRINTS=<this file>. "
                 "Remove the env var (or the file) after the window ends."),
    })
    _write_json(os.path.join(out_dir, "re_attestation_manifest.json"), {
        "window_ends_at": window_ends_at,
        "taxonomy": SECURITY_BAN_TAXONOMY_VERSION,
        "entries": re_attest,
        "instruction": ("Each entry needs two-human re-attestation of the "
                        "v2 fingerprint before window_ends_at; pending "
                        "entries go stale (fail closed) when the window "
                        "lapses."),
    })

    summary = {"migrated": True, "carried_over": carried, "skipped": skipped,
               "refused_security_ban": refused_ban,
               "window_ends_at": window_ends_at,
               "out_dir": out_dir, "errors": errors}
    logger.warning("MIGRATION-COMPLETE carried=%d skipped=%d "
                   "refused_security_ban=%d window_ends_at=%s — "
                   "re-attest %d pending entries before the window ends",
                   carried, skipped, refused_ban, window_ends_at, carried)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Migrate an attested allowlist bundle from scheme-v1 "
                    "(env-blind) to scheme-v2 (env+cluster) fingerprints.")
    ap.add_argument("--bundle-dir", required=True,
                    help="bundle dir with allowlist.json + "
                         "proofs/allowlist_attestations.json")
    ap.add_argument("--manifest", required=True,
                    help="JSON mapping v1 fingerprint -> {service, check, "
                         "severity_in, region, env, cluster[, team]}")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                    help=f"bounded migration window in days (default "
                         f"{DEFAULT_WINDOW_DAYS}, max {MAX_WINDOW_DAYS})")
    ap.add_argument("--out-dir", default=None,
                    help="output dir (default: rewrite the bundle in place "
                         "with a timestamped backup)")
    ap.add_argument("--force", action="store_true",
                    help="re-run on an already-migrated bundle")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate inputs and report without writing")
    args = ap.parse_args(argv)

    if not 1 <= args.window_days <= MAX_WINDOW_DAYS:
        ap.error(f"--window-days must be within [1, {MAX_WINDOW_DAYS}] "
                 f"(bounded window — refusing {args.window_days})")
    manifest = _load_json(args.manifest)
    if not isinstance(manifest, dict):
        ap.error("--manifest must be a JSON object mapping v1 -> components")

    if args.dry_run:
        allowlist = _load_json(os.path.join(args.bundle_dir,
                                            "allowlist.json"))
        envs = allowlist.get("envs", {})
        bundle_fps: list[str] = []
        if isinstance(envs, dict):
            for ns in envs.values():
                entries = (ns or {}).get("entries", [])
                if isinstance(entries, dict):
                    entries = list(entries.keys())
                bundle_fps.extend(entries)
        covered = sum(1 for fp in bundle_fps if fp in manifest)
        print(f"dry-run: {len(bundle_fps)} bundle entries, {covered} "
              f"covered by manifest, {len(bundle_fps) - covered} would be "
              f"SKIPPED")
        return 0

    out_dir = args.out_dir or args.bundle_dir
    try:
        summary = migrate(args.bundle_dir, manifest, args.window_days,
                          out_dir, force=args.force)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.error("migration aborted: %s", exc)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
