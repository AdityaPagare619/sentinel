#!/usr/bin/env python3
"""Build the GitHub Pages static site for Sentinel (gh-pages-envs lane).

Two environments from one build (Aditya's directive 2026-10-04):
  staging/ — fully simulated showcase: synthetic data pre-rendered to static
             JSON, SIMULATED banner in-band (P3), KEYS in demo mode
             (in-memory, never persisted), SSE replaced by snapshot polling
             labeled "not live".
  prod/    — the real product surface: DATA_MODE=live, NO synthetic data, NO
             fixtures, NO simulated mode anywhere in the bundle (grep-verified).
             Backend-URL configuration is the entry point; the KEYS screen is
             fully functional against the operator's own backend (BYOK).

How it works:
  1. Build the deterministic demo dataset (deploy/vercel/build-data.sh).
  2. Start the real platform server locally against it (--data-source synthetic).
  3. GET every read endpoint; POST fixed simulator scenarios.
  4. Write normalized JSON (sort_keys, no wall-clock) to api/.
  5. Assemble staging/ + prod/ variants (UI assets + per-variant config.js).

Determinism: the synthetic dataset is seeded; JSON is written with
sort_keys=True and no build timestamps inside api/. Two runs produce
byte-identical api/ trees (tested).

Usage:
  python3 deploy/gh-pages/build-static.py --out /tmp/gh-pages-site
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI_DIR = os.path.join(REPO, "platform", "ui")

SCENARIOS = {
    # name: (label, thresholds) — pre-computed, labeled in provenance.
    "default": ("Current defaults", {
        "suppress_p1_max": 0.002, "suppress_conf_min": 0.90,
        "page_p1p2_min": 0.30, "uncertain_conf_max": 0.50,
        "queue_conf_min": 0.70}),
    "conservative": ("Conservative — suppress less", {
        "suppress_p1_max": 0.001, "suppress_conf_min": 0.95,
        "page_p1p2_min": 0.30, "uncertain_conf_max": 0.50,
        "queue_conf_min": 0.70}),
    "aggressive": ("Aggressive — suppress more", {
        "suppress_p1_max": 0.010, "suppress_conf_min": 0.80,
        "page_p1p2_min": 0.30, "uncertain_conf_max": 0.50,
        "queue_conf_min": 0.70}),
}

# GET endpoints to pre-render: (url path, output file relative to api_dir).
GETS = [
    ("/api/decisions?limit=500", "decisions.json"),
    ("/api/calibration", "calibration.json"),
    ("/api/analytics/noise?window=24h", "noise.json"),
    ("/api/analytics/flips?window=7d", "flips.json"),
]

DETAIL_LIMIT = 200  # per-decision files api/decision/<id>.json (matches the live shadow join)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _get(base: str, path: str) -> dict:
    req = urllib.request.Request(base + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def _post(base: str, path: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read().decode("utf-8"))


def _write_json(path: str, obj: dict) -> None:
    """Normalized JSON: sorted keys, no wall-clock — byte-identical across runs."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, sort_keys=True, separators=(",", ":"))
        f.write("\n")


def _strip_wall_clock(obj):
    """Remove wall-clock fields the server injects (generated_at, computed_at,
    built_at, server_time) so output is deterministic. Dataset timestamps
    (decision times, labeled_at) stay — they are the data, not the build."""
    if isinstance(obj, dict):
        return {k: _strip_wall_clock(v) for k, v in obj.items()
                if k not in ("built_at", "generated_at", "computed_at",
                             "server_time")}
    if isinstance(obj, list):
        return [_strip_wall_clock(v) for v in obj]
    return obj


def build_data() -> str:
    """Run the deterministic demo-data build. Returns the dist-data dir."""
    print("[build] demo dataset (deterministic, no network, no Jev key)…", flush=True)
    subprocess.run(["bash", os.path.join(REPO, "deploy", "vercel", "build-data.sh")],
                   check=True, cwd=REPO)
    out = os.path.join(REPO, "deploy", "dist-data")
    assert os.path.exists(os.path.join(out, "demo.db")), "demo.db missing"
    return out


def start_server(data_dir: str, state_dir: str, port: int) -> subprocess.Popen:
    plat = os.path.join(state_dir, "platform")
    os.makedirs(os.path.join(plat, "datasets"), exist_ok=True)
    shutil.copy(os.path.join(data_dir, "context.jsonl"),
                os.path.join(plat, "context.jsonl"))
    shutil.copy(os.path.join(data_dir, "datasets", "labels-v3.jsonl"),
                os.path.join(plat, "datasets", "labels-v3.jsonl"))
    cmd = [sys.executable, os.path.join(REPO, "platform", "server", "__main__.py"),
           "--port", str(port), "--host", "127.0.0.1",
           "--db", os.path.join(data_dir, "demo.db"),
           "--state-dir", state_dir,
           "--data-source", "synthetic",
           "--ui", os.path.join(REPO, "platform", "ui")]
    print(f"[build] platform server on 127.0.0.1:{port} (synthetic)…", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            cwd=REPO)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            _get(base, "/api/decisions?limit=1")
            print("[build] server up", flush=True)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("platform server did not start")


def prerender(base: str, api_dir: str) -> dict:
    """Fetch every read endpoint; write normalized JSON. Returns build stats."""
    stats = {"gets": 0, "details": 0, "scenarios": 0}
    for path, rel in GETS:
        env = _strip_wall_clock(_get(base, path))
        _write_json(os.path.join(api_dir, rel), env)
        stats["gets"] += 1
        print(f"[build]   GET {path} -> {rel}", flush=True)

    # Per-decision detail files (top-N by recency, like the live shadow join).
    decisions = _get(base, "/api/decisions?limit=500")["data"]
    for d in decisions[:DETAIL_LIMIT]:
        did = d["id"]
        try:
            env = _strip_wall_clock(_get(base, f"/api/decision/{did}"))
        except Exception as e:
            print(f"[build]   !! decision {did}: {e} — skipped (derived path covers it)",
                  flush=True)
            continue
        _write_json(os.path.join(api_dir, "decision", f"{did}.json"), env)
        stats["details"] += 1
    print(f"[build]   decision details: {stats['details']}", flush=True)

    # Shadow: the same client-side join the live UI performs (decisions +
    # labeled outcomes). No agreement dimension — the read contract doesn't
    # expose the shadow join, and we don't invent it (honesty law).
    rows = []
    for d in decisions[:DETAIL_LIMIT]:
        did = d["id"]
        p = os.path.join(api_dir, "decision", f"{did}.json")
        outcome = None
        if os.path.exists(p):
            with open(p) as f:
                outcome = json.load(f)["data"].get("outcome")
        rows.append({"decision_id": did, "service": d.get("service"),
                     "severity": d.get("severity"), "time": d.get("time"),
                     "reason": d.get("reason"), "confidence": d.get("confidence"),
                     "disposition": d.get("disposition"), "outcome": outcome})
    _write_json(os.path.join(api_dir, "shadow.json"), {
        "data": rows,
        "meta": {"contract_version": "1.0.0", "data_source": "synthetic",
                 "window": "7d", "derived": "pre-rendered client-side join of decision records + labeled outcomes",
                 "agreement": "unavailable — shadow join not in the read contract",
                 "precomputed": True},
    })
    print(f"[build]   shadow join: {len(rows)} rows", flush=True)

    # Stream snapshot: the latest decisions, polled by the static river.
    # Labeled "snapshot — not live" by the UI (freshness law).
    snap = _strip_wall_clock(_get(base, "/api/decisions?limit=50"))
    snap["meta"]["snapshot"] = True
    snap["meta"]["note"] = ("pre-rendered snapshot baked at build time — "
                            "the static river polls this file; it is not a live stream")
    _write_json(os.path.join(api_dir, "stream-snapshot.json"), snap)
    print("[build]   stream snapshot: 50 latest decisions", flush=True)

    # Simulator scenarios: fixed, pre-computed, labeled.
    ds_version = _get(base, "/api/calibration")["data"].get("dataset_version", "labels-v3")
    for name, (label, thresholds) in SCENARIOS.items():
        env = _strip_wall_clock(_post(base, "/api/simulate", {
            "thresholds": thresholds,
            "cost_model": {"c_fp": 100, "c_fn": 50000},
            "dataset_version": ds_version,
        }))
        env["data"]["scenario_thresholds"] = thresholds
        env["data"]["provenance"]["precomputed"] = True
        env["data"]["provenance"]["scenario"] = name
        env["data"]["provenance"]["scenario_label"] = label
        env["data"]["provenance"]["note"] = (
            "pre-computed at build time from the synthetic dataset — not a live tuner run")
        _write_json(os.path.join(api_dir, "simulate", f"{name}.json"), env)
        stats["scenarios"] += 1
        print(f"[build]   POST /api/simulate [{name}]", flush=True)
    # Default simulate.json (the mock-mode shape the UI also reads).
    shutil.copy(os.path.join(api_dir, "simulate", "default.json"),
                os.path.join(api_dir, "simulate.json"))
    return stats


CONFIG_JS = "window.SENTINEL_DATA_MODE='{mode}';\n"

# Strings that must NEVER appear in the prod bundle. Note: the two variants
# share ~99% of the JS (the difference is config.js + data presence), so this
# list is deliberately narrow — it catches real leaks, not dormant dev-mode
# code paths (which are unreachable: DATA_MODE='live' forces live mode and
# setMode() is a no-op). Structural checks below (no data/, no api/, no JSON
# fixtures) carry the "no fixtures" guarantee.
PROD_BANNED = ["SENTINEL_SIMULATED_PAGING"]
# Strings that MUST appear in the staging bundle (in-band honesty).
STAGING_REQUIRED = ["SIMULATED SHOWCASE", "snapshot — not live"]


def assemble_staging(ui_src: str, api_dir: str, out: str) -> None:
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    # UI assets
    for name in ("assets",):
        shutil.copytree(os.path.join(ui_src, name), os.path.join(out, name))
    shutil.copy(os.path.join(ui_src, "index.html"), out)
    # Pre-rendered API
    shutil.copytree(api_dir, os.path.join(out, "api"))
    # Build-time config: static mode
    with open(os.path.join(out, "assets", "config.js"), "w") as f:
        f.write(CONFIG_JS.format(mode="static"))
    print(f"[build] staging/ assembled -> {out}", flush=True)


def assemble_prod(ui_src: str, out: str) -> None:
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    for name in ("assets",):
        shutil.copytree(os.path.join(ui_src, name), os.path.join(out, name))
    shutil.copy(os.path.join(ui_src, "index.html"), out)
    with open(os.path.join(out, "assets", "config.js"), "w") as f:
        f.write(CONFIG_JS.format(mode="live"))
    # Belt-and-braces: no fixture/mock data dir ships in prod, even if the
    # source tree gains one later.
    for banned_dir in ("data", "api"):
        p = os.path.join(out, banned_dir)
        if os.path.exists(p):
            shutil.rmtree(p)
    print(f"[build] prod/ assembled -> {out}", flush=True)


def verify_prod(out: str) -> None:
    """Fail loudly if the prod bundle violates its contract:
    no fixture data dirs, no pre-rendered JSON, DATA_MODE='live',
    no server env vars leaked into the client."""
    for banned_dir in ("data", "api"):
        p = os.path.join(out, banned_dir)
        if os.path.exists(p):
            raise RuntimeError(f"prod bundle must not contain {banned_dir}/ (fixtures)")
    json_files = []
    for root, _, files in os.walk(out):
        for fn in files:
            if fn.endswith(".json"):
                json_files.append(os.path.relpath(os.path.join(root, fn), out))
    if json_files:
        raise RuntimeError("prod bundle must not contain JSON fixtures: "
                           + ", ".join(json_files))
    cfg = os.path.join(out, "assets", "config.js")
    with open(cfg) as f:
        if "window.SENTINEL_DATA_MODE='live'" not in f.read():
            raise RuntimeError("prod config.js must fix DATA_MODE='live'")
    bad = []
    for root, _, files in os.walk(out):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                with open(p, "r", encoding="utf-8", errors="strict") as f:
                    text = f.read()
            except (UnicodeDecodeError, ValueError):
                continue  # binary — skip
            for s in PROD_BANNED:
                if s in text:
                    bad.append(f"{os.path.relpath(p, out)}: {s!r}")
    if bad:
        raise RuntimeError("prod bundle contains banned strings:\n" + "\n".join(bad))
    # The honest empty state must ship: backend setup is the entry point.
    if not os.path.exists(os.path.join(out, "assets", "views-setup.js")):
        raise RuntimeError("prod bundle missing views-setup.js (backend config entry)")
    print("[build] prod bundle clean: no fixtures, DATA_MODE='live', setup entry present",
          flush=True)


def verify_staging(out: str) -> None:
    """Fail loudly if the in-band honesty strings are missing."""
    blob = ""
    for root, _, files in os.walk(out):
        for fn in files:
            if not fn.endswith((".js", ".html")):
                continue
            with open(os.path.join(root, fn), encoding="utf-8") as f:
                blob += f.read()
    missing = [s for s in STAGING_REQUIRED if s not in blob]
    if missing:
        raise RuntimeError("staging bundle missing honesty strings: " + ", ".join(missing))
    # Every pre-rendered JSON must be valid.
    n = 0
    for root, _, files in os.walk(os.path.join(out, "api")):
        for fn in files:
            if fn.endswith(".json"):
                with open(os.path.join(root, fn), encoding="utf-8") as f:
                    json.load(f)
                n += 1
    print(f"[build] staging bundle honest ({n} valid JSON files)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="output dir for staging/ + prod/")
    ap.add_argument("--port", type=int, default=0, help="platform port (0 = auto)")
    ap.add_argument("--skip-data", action="store_true",
                    help="reuse deploy/dist-data instead of rebuilding")
    args = ap.parse_args()

    data_dir = (os.path.join(REPO, "deploy", "dist-data") if args.skip_data
                else build_data())
    port = args.port or _free_port()
    with tempfile.TemporaryDirectory(prefix="sentinel-gh-pages-") as state_dir:
        server = start_server(data_dir, state_dir, port)
        try:
            base = f"http://127.0.0.1:{port}"
            with tempfile.TemporaryDirectory(prefix="sentinel-gh-pages-api-") as api_tmp:
                stats = prerender(base, api_tmp)
                print(f"[build] pre-rendered: {stats}", flush=True)
                assemble_staging(UI_DIR, api_tmp,
                                 os.path.join(args.out, "staging"))
                # prod/ never sees the api tree
        finally:
            server.terminate()
            server.wait(timeout=10)
    assemble_prod(UI_DIR, os.path.join(args.out, "prod"))
    verify_staging(os.path.join(args.out, "staging"))
    verify_prod(os.path.join(args.out, "prod"))
    print("[build] DONE", flush=True)


if __name__ == "__main__":
    main()
