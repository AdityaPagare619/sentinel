#!/usr/bin/env python3
"""Build the GitHub Pages site from the winning v2 console (Track 8).

One console codebase, environment as structural mode (CONSOLE-PARITY.md):

  /index.html          → production console: <html data-mode="production"
                            data-backend="<vercel-url>">. Static PRODUCTION
                            band in the bytes (honest aria-label, never a
                            JS post-token flip), operator token gate,
                            live /api/*.
  /staging/index.html  → simulated showcase: the v2 file as-is (sim mode).
                            Violet SIMULATED banner, seed shown.
  /loadtest/index.html → load-test dashboard (handoff from lane/loadtest-env;
                            this script reserves the path).
  /preview-v2/         → KEPT until the production shift (judging artifact).

Usage:
  python3 deploy/gh-pages/build-v2.py --out /tmp/gh-pages-v2 \
      --backend https://sentinel-platform-....vercel.app \
      [--loadtest-dashboard /path/to/dashboard.html]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
V2 = os.path.join(REPO, "platform", "ui-v2", "index.html")


def build(out: str, backend: str, loadtest_dashboard: str | None) -> None:
    assert os.path.exists(V2), f"v2 console missing: {V2}"
    html = open(V2, encoding="utf-8").read()

    # --- / : production console -------------------------------------------
    prod = html.replace(
        "<html", '<html data-mode="production" data-backend="' + backend + '"', 1)
    assert 'data-mode="production"' in prod, "mode injection failed"
    assert backend in prod, "backend injection failed"
    # X-H: static prod chrome in the bytes — the mode label must never depend
    # on JS+token cooperation (a JS/fetch failure would mislabel an armed
    # console as simulated, the dangerous direction). Screen readers, curl,
    # and no-JS audits read these bytes.
    _sim_band = re.compile(
        r'<div class="sim-band" role="note" aria-label="Simulated mode">.*?</div>',
        re.DOTALL)
    assert _sim_band.search(prod), "sim-band markup missing from source"
    prod_band = (
        '<div class="sim-band" role="note" '
        'aria-label="Production console: live platform backend" '
        'style="background:#7a1f14">\n'
        '  <span class="tag" style="background:#fff;color:#7a1f14">PRODUCTION</span>\n'
        '  <span id="prodBandText">Live platform backend — '
        'syncing forwarder &amp; judge state…</span>\n'
        '</div>')
    prod = _sim_band.sub(prod_band, prod, count=1)
    assert 'aria-label="Simulated mode"' not in prod, "sim label leaked into prod"
    assert 'aria-label="Production console' in prod, "prod aria-label missing"
    # banner contract: production build must carry the PRODUCTION chrome
    assert "PRODUCTION" in prod and "ProductionAdapter" in prod
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(prod)
    print(f"[v2] /index.html → production (backend={backend})")

    # --- /staging/ : simulated showcase ------------------------------------
    # The v2 file defaults to sim mode (no data-mode) — ship it verbatim.
    assert "SIMULATED" in html and "SimAdapter" in html
    stg = os.path.join(out, "staging")
    os.makedirs(stg, exist_ok=True)
    shutil.copy(V2, os.path.join(stg, "index.html"))
    print("[v2] /staging/index.html → sim showcase (verbatim v2)")

    # --- /loadtest/ : reserved for the load-test lane's dashboard ----------
    lt = os.path.join(out, "loadtest")
    os.makedirs(lt, exist_ok=True)
    if loadtest_dashboard and os.path.exists(loadtest_dashboard):
        shutil.copy(loadtest_dashboard, os.path.join(lt, "index.html"))
        print(f"[v2] /loadtest/index.html → dashboard from {loadtest_dashboard}")
    else:
        # Placeholder reserves the path; replaced at handoff. Honest label.
        open(os.path.join(lt, "index.html"), "w", encoding="utf-8").write(
            "<!doctype html><html><head><meta charset=utf-8>"
            "<title>Sentinel — load test</title></head><body style='font-family:"
            "system-ui;background:#141310;color:#f5f2ea;padding:40px'>"
            "<h1>Load-test environment</h1>"
            "<p>The millions-scale harness dashboard lands here. "
            "The load-test lane is running it now.</p></body></html>")
        print("[v2] /loadtest/ → placeholder (awaiting lane handoff)")

    # --- banner contract verification --------------------------------------
    # Inspect the actual <html> ELEMENT (first tag), not string occurrences
    # in JS comments.
    import re as _re
    def _html_tag(path):
        blob = open(path, encoding="utf-8").read()
        m = _re.search(r"<html[^>]*>", blob)
        return m.group(0) if m else ""
    prod_tag = _html_tag(os.path.join(out, "index.html"))
    stg_tag = _html_tag(os.path.join(stg, "index.html"))
    assert 'data-mode="production"' in prod_tag, "prod <html> missing data-mode"
    assert 'data-backend="' in prod_tag, "prod <html> missing data-backend"
    assert 'data-mode="production"' not in stg_tag, "staging <html> leaked data-mode"
    print("[v2] banner contract verified")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", required=True,
                    help="production backend base URL (Vercel)")
    ap.add_argument("--loadtest-dashboard", default=None)
    args = ap.parse_args()
    build(args.out, args.backend.rstrip("/"), args.loadtest_dashboard)
    print("[v2] DONE")


if __name__ == "__main__":
    main()
