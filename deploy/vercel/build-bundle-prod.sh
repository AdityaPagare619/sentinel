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
# STATE CONTRACT: the bundle must NEVER contain runtime state. State lives
# in SENTINEL_STATE_DIR (default ~/.sentinel/state — OUTSIDE the repo
# tree); the bundle script copies only *.py sources, and the
# state-exclusion guard below fails the build if any keystore / token /
# audit file is ever found in the output.
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

# --- state-exclusion guard -------------------------------------------------
# Fail closed: no keystore / token / audit-log file may ever ship in the
# bundle. (State lives in SENTINEL_STATE_DIR, outside the repo tree; the
# copy step above only takes *.py — this is the backstop.)
banned_names = {"operator_token.json", "integrations.json",
                "webhook_secrets.json", "sentinel-state"}
bad = []
for root, dirs, files in os.walk("$DIST"):
    for d in list(dirs):
        if d in banned_names or d == "__pycache__":
            bad.append(os.path.join(root, d))
    for f in files:
        if f in banned_names or f.endswith(".audit.jsonl"):
            bad.append(os.path.join(root, f))
assert not bad, f"bundle contains state/secret files: {bad}"
print("[bundle-prod] state-exclusion guard clean")
print("[bundle-prod] OK -> $DIST")
EOF
