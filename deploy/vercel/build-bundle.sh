#!/usr/bin/env bash
# Build the Vercel deployment bundle from repo sources (deploy lane).
#
# The bundle is a GENERATED artifact: assemble it with this script, never
# hand-edit deploy/dist. The script copies (never moves) from:
#   platform/server  -> dist/api/_srv   (the real frozen-contract WSGI app)
#   src/sentinel     -> dist/api/_eng   (the engine package, stdlib-only)
#   platform/ui      -> dist/           (Prism's static files, served at /)
#   deploy/dist-data -> dist/api/_data  (read-only demo snapshot)
#
# Run from the repo root:
#   bash deploy/vercel/build-bundle.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO/deploy/vercel"
DIST="$REPO/deploy/dist/vercel"

echo "[bundle] repo=$REPO"
rm -rf "$DIST"
mkdir -p "$DIST/api/_srv" "$DIST/api/_eng/sentinel" "$DIST/api/_data"

# --- serverless function ------------------------------------------------
cp "$SRC/api/index.py" "$DIST/api/index.py"
cp "$SRC/vercel.json" "$DIST/vercel.json"
cp "$SRC/ABOUT-THIS-DEPLOYMENT.md" "$DIST/ABOUT-THIS-DEPLOYMENT.md"
for f in __init__.py _pkg.py app.py store.py datasets.py shed.py simulate.py; do
  cp "$REPO/platform/server/$f" "$DIST/api/_srv/$f"
done
for f in "$REPO"/src/sentinel/*.py; do
  cp "$f" "$DIST/api/_eng/sentinel/$(basename "$f")"
done

# --- demo dataset snapshot (see deploy/vercel/README.md for provenance) -
if [ ! -f "$REPO/deploy/dist-data/demo.db" ]; then
  echo "[bundle] building demo dataset snapshot..."
  "$SRC/build-data.sh"
fi
cp "$REPO/deploy/dist-data/demo.db"        "$DIST/api/_data/demo.db"
cp "$REPO/deploy/dist-data/context.jsonl"  "$DIST/api/_data/context.jsonl"
mkdir -p "$DIST/api/_data/datasets"
cp "$REPO/deploy/dist-data/datasets/labels-v3.jsonl" \
   "$DIST/api/_data/datasets/labels-v3.jsonl"
chmod 444 "$DIST/api/_data/demo.db"

# --- static UI (Prism's files; we serve, they own) ----------------------
cp "$REPO/platform/ui/index.html" "$DIST/index.html"
cp -r "$REPO/platform/ui/assets" "$DIST/assets"
cp -r "$REPO/platform/ui/data"   "$DIST/data"

# --- honesty banner, injected into the BUNDLED copy only -----------------
# platform/ui is Prism's file; the banner lives in the deploy bundle so the
# hosted URL states what is real and what is demo without touching their source.
python3 - "$DIST/index.html" <<'EOF'
import sys
p = sys.argv[1]
html = open(p, encoding="utf-8").read()
banner = (
  '<div id="sentinel-hosted-banner" style="background:#3a2b00;color:#ffd970;'
  'font:12px/1.5 system-ui,sans-serif;padding:8px 14px;text-align:center;'
  'border-bottom:1px solid #8a6d1c">'
  '&#9672; HOSTED DEMO &mdash; synthetic storm data, read-only API '
  '(frozen contract v1.0.0). Live SSE is polling-only here; nothing on this '
  'URL can page anyone. <a href="/ABOUT-THIS-DEPLOYMENT.md" '
  'style="color:#ffd970">What&rsquo;s real, what&rsquo;s demo</a></div>'
)
html = html.replace("<body>", "<body>" + banner, 1)
open(p, "w", encoding="utf-8").write(html)
print("[bundle] banner injected into bundled index.html")
EOF

# --- sanity ----------------------------------------------------------------
python3 - <<EOF
import os
missing = [p for p in [
  "$DIST/api/index.py", "$DIST/api/_srv/app.py",
  "$DIST/api/_eng/sentinel/tuner.py", "$DIST/api/_data/demo.db",
  "$DIST/api/_data/datasets/labels-v3.jsonl",
  "$DIST/index.html", "$DIST/vercel.json",
] if not os.path.exists(p)]
assert not missing, f"missing: {missing}"
print("[bundle] OK -> $DIST")
EOF
