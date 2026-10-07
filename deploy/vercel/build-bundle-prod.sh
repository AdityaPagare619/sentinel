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
# PROVENANCE CONTRACT (audit P1-25): the bundle records the exact source
# commit in BUILD_INFO.json and refuses to build from a dirty tree unless
# --allow-dirty is passed (dev only; the flag is recorded in BUILD_INFO).
# A bundle that cannot name its commit is undeployable — bundle-vs-source
# drift is otherwise unverifiable.
#
# Run from the repo root:
#   bash deploy/vercel/build-bundle-prod.sh [--allow-dirty]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO/deploy/vercel"
DIST="$REPO/deploy/dist-prod/vercel"

ALLOW_DIRTY=0
[ "${1:-}" = "--allow-dirty" ] && ALLOW_DIRTY=1

# --- provenance ------------------------------------------------------------
COMMIT="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
    if [ "$ALLOW_DIRTY" = "1" ]; then
      DIRTY=true
      echo "[bundle-prod] WARNING: building from a DIRTY tree (--allow-dirty); recorded in BUILD_INFO.json" >&2
    else
      echo "[bundle-prod] REFUSING: working tree is dirty. Commit or stash" >&2
      echo "  your changes, or pass --allow-dirty for a dev-only bundle." >&2
      echo "  A production bundle must name an exact commit." >&2
      exit 2
    fi
  else
    DIRTY=false
  fi
else
  DIRTY=unknown
fi

echo "[bundle-prod] repo=$REPO commit=$COMMIT dirty=$DIRTY"
rm -rf "$DIST"
mkdir -p "$DIST/api/_srv" "$DIST/api/_eng/sentinel"

# --- provenance record (ships IN the bundle; drift-checkable) ---------------
python3 - "$DIST" "$COMMIT" "$BRANCH" "$DIRTY" <<'EOF'
import json, sys, datetime
dist, commit, branch, dirty = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
info = {
    "commit": commit,
    "branch": branch,
    "dirty": dirty == "true",
    "built_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "script": "deploy/vercel/build-bundle-prod.sh",
}
with open(f"{dist}/BUILD_INFO.json", "w") as f:
    json.dump(info, f, indent=2)
print(f"[bundle-prod] BUILD_INFO.json commit={commit} dirty={info['dirty']}")
EOF

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

# --- deploy record -----------------------------------------------------------
# The operator pastes this block into the deploy note (REPEATABLE-DEPLOY.md
# §5). It is the anti-click-ops artifact: who built what, from which
# commit, and what the next operator must verify.
BUNDLE_SHA="$(find "$DIST" -type f | sort | xargs sha256sum | sha256sum | cut -d' ' -f1)"
cat <<EOF

═══════════════════════════════════════════════════════════════════
DEPLOY RECORD — paste into the deploy note, then verify (§6)
  bundle built : $(date -u +%Y-%m-%dT%H:%M:%SZ)
  source commit: $COMMIT  (branch $BRANCH, dirty=$DIRTY)
  bundle sha256: $BUNDLE_SHA
  built by     : ${USER:-unknown} on $(hostname)
  deploy cmd   : <paste the exact command used — CLI, dashboard redeploy,
                  or MCP upload_file path — see REPEATABLE-DEPLOY.md §4>
  deployment id: <from the deploy output — needed for Instant Rollback>
  verified     : [ ] /health/live 200 unauth   [ ] /ops/health 401 unauth
                 [ ] CORS preflight 204 + ACAO (lowercase origin)
                 [ ] BUILD_INFO.json commit == source commit
═══════════════════════════════════════════════════════════════════
EOF
