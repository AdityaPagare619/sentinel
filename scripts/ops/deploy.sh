#!/usr/bin/env bash
# deploy.sh — immutable-artifact deploy for the single-box v0.1.
#
# Never SSH-fix prod: a deploy is a NEW artifact dir built from a PINNED git
# SHA, health-gated, then swapped in via the `current` symlink. Rollback is a
# symlink, not a revert commit (see rollback.sh).
#
# Layout on the box:  $SENTINEL_DEPLOY_ROOT/
#                       artifacts/20261004-002131-a1b2c3d/   (one per deploy)
#                       current -> artifacts/<latest good>
#
# Usage: deploy.sh --sha <40-hex> [--deploy-root DIR]
# Requires env: SENTINEL_RESTART_CMD (how to restart the receiver, e.g.
#   "sudo systemctl restart sentinel"), SENTINEL_HEALTH_TOKEN,
#   SENTINEL_PORT (default 8080).
#
# Refuses: dirty tree check irrelevant (fresh clone); unpinned ref; gate red.

set -euo pipefail

SHA=""; ROOT="${SENTINEL_DEPLOY_ROOT:-/opt/sentinel}"
while [ $# -gt 0 ]; do
  case "$1" in
    --sha) SHA="$2"; shift 2 ;;
    --deploy-root) ROOT="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "FAIL: --sha must be a full 40-hex SHA (pinned, never a branch)" >&2; exit 2; }
[ -n "${SENTINEL_RESTART_CMD:-}" ] || { echo "FAIL: SENTINEL_RESTART_CMD not set (operator provides the restart mechanism)" >&2; exit 2; }
[ -n "${SENTINEL_HEALTH_TOKEN:-}" ] || { echo "FAIL: SENTINEL_HEALTH_TOKEN not set" >&2; exit 2; }

PORT="${SENTINEL_PORT:-8080}"
TS="$(date -u +%Y%m%d-%H%M%S)"
ART="$ROOT/artifacts/$TS-${SHA:0:7}"
mkdir -p "$ROOT/artifacts"

echo "deploy: fetching $SHA"
git clone -q --no-checkout . "$ART" 2>/dev/null || git clone -q . "$ART"
( cd "$ART" && git checkout -q "$SHA" && git rev-parse HEAD | grep -q "^$SHA" ) \
  || { echo "FAIL: could not pin $SHA" >&2; rm -rf "$ART"; exit 1; }

echo "deploy: running the gate on the pinned SHA"
( cd "$ART" && ./scripts/ops/pre-pr-gate.sh --fast ) \
  || { echo "FAIL: gate red — deploy aborted, artifact kept at $ART for forensics" >&2; exit 1; }

PREV="$(readlink "$ROOT/current" 2>/dev/null || echo none)"
ln -sfn "$ART" "$ROOT/current"
echo "deploy: current: $PREV -> $ART"

echo "deploy: restarting ($SENTINEL_RESTART_CMD)"
eval "$SENTINEL_RESTART_CMD"

echo "deploy: health-gating on :$PORT/healthz"
ok=0
HB="$(mktemp)"; trap 'rm -f "$HB"' EXIT
for _ in $(seq 1 24); do
  code="$(curl -s -m 3 -o "$HB" -w '%{http_code}' \
      -H "Authorization: Bearer $SENTINEL_HEALTH_TOKEN" \
      "http://127.0.0.1:$PORT/healthz")"
  # HTTP 200 AND top-level ok:true — a 503 (failed predicate) is unhealthy.
  if [ "$code" = "200" ] && python3 -c "
import json, sys
sys.exit(0 if json.load(open('$HB')).get('ok') is True else 1)
" 2>/dev/null; then ok=1; break; fi
  sleep 5
done
rm -f "$HB"; trap - EXIT
if [ "$ok" = "1" ]; then
  echo "deploy: GREEN — $SHA live at $ART (previous: $PREV)"
  echo "$TS $SHA $PREV" >> "$ROOT/deploy.log"
else
  echo "FAIL: new artifact unhealthy — rolling back to $PREV" >&2
  [ "$PREV" != "none" ] && ln -sfn "$PREV" "$ROOT/current"
  eval "$SENTINEL_RESTART_CMD" || true
  exit 1
fi
