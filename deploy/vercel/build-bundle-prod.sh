#!/usr/bin/env bash
# Build the PRODUCTION Vercel deployment bundle from repo sources.
#
# The bundle is a GENERATED artifact: assemble it with this script, never
# hand-edit deploy/dist-prod. The script copies (never moves) from:
#   platform/server  -> dist-prod/api/_srv  (the real WSGI app, main branch)
#   src/sentinel     -> dist-prod/api/_eng   (the engine package, stdlib-only)
#
# Production bundle: NO synthetic demo data, NO demo banner. The API
# serves the real (initially empty) production state; ops/health reports
# engine_db.available=false honestly until the engine DB is connected.
#
# Run from the repo root:
#   bash deploy/vercel/build-bundle-prod.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO/deploy/vercel"
DIST="$REPO/deploy/dist-prod/vercel"

echo "[bundle-prod] repo=$REPO"
rm -rf "$DIST"
mkdir -p "$DIST/api/_srv" "$DIST/api/_eng/sentinel"

# --- serverless function ------------------------------------------------
cp "$SRC/api/index-prod.py" "$DIST/api/index.py"
cp "$SRC/vercel.prod.json" "$DIST/vercel.json"
for f in "$REPO"/platform/server/*.py; do
  cp "$f" "$DIST/api/_srv/$(basename "$f")"
done
for f in "$REPO"/src/sentinel/*.py; do
  cp "$f" "$DIST/api/_eng/sentinel/$(basename "$f")"
done

# --- sanity ----------------------------------------------------------------
python3 - <<EOF
import os
missing = [p for p in [
  "$DIST/api/index.py",
  "$DIST/api/_srv/app.py",
  "$DIST/api/_srv/ops_health.py",
  "$DIST/api/_srv/safety_api.py",
  "$DIST/api/_srv/auth.py",
  "$DIST/api/_eng/sentinel/safety.py",
  "$DIST/vercel.json",
] if not os.path.exists(p)]
assert not missing, f"missing: {missing}"
print("[bundle-prod] OK -> $DIST")
EOF
