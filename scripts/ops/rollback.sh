#!/usr/bin/env bash
# rollback.sh — the 3 AM script. Swaps `current` back to the previous artifact
# dir, restarts, and health-gates. Refuses to declare success unless /healthz
# reports ok:true.
#
# Usage: rollback.sh [--deploy-root DIR] [--to DIR]
#   --to: explicit artifact dir (default: the newest artifacts/* that isn't current)

set -euo pipefail

ROOT="${SENTINEL_DEPLOY_ROOT:-/opt/sentinel}"
TO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --deploy-root) ROOT="$2"; shift 2 ;;
    --to) TO="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -n "${SENTINEL_RESTART_CMD:-}" ] || { echo "FAIL: SENTINEL_RESTART_CMD not set" >&2; exit 2; }
[ -n "${SENTINEL_HEALTH_TOKEN:-}" ] || { echo "FAIL: SENTINEL_HEALTH_TOKEN not set" >&2; exit 2; }
PORT="${SENTINEL_PORT:-8080}"

CUR="$(readlink "$ROOT/current" 2>/dev/null || echo none)"
[ "$CUR" != "none" ] || { echo "FAIL: no current symlink at $ROOT/current" >&2; exit 1; }

if [ -z "$TO" ]; then
  TO="$(ls -dt "$ROOT"/artifacts/*/ 2>/dev/null | grep -v "^${CUR%/}/\$" | head -1 || true)"
  TO="${TO%/}"
fi
[ -n "$TO" ] && [ -d "$TO" ] || { echo "FAIL: no previous artifact found" >&2; exit 1; }
[ "$TO" != "${CUR%/}" ] || { echo "FAIL: --to is the current artifact; nothing to roll back to" >&2; exit 1; }

echo "rollback: $CUR -> $TO"
ln -sfn "$TO" "$ROOT/current"
eval "$SENTINEL_RESTART_CMD"

ok=0; body=""
for _ in $(seq 1 24); do
  body="$(curl -s -m 3 -H "Authorization: Bearer $SENTINEL_HEALTH_TOKEN" \
    "http://127.0.0.1:$PORT/healthz" || true)"
  if echo "$body" | grep -q '"ok": *true'; then ok=1; break; fi
  sleep 5
done
if [ "$ok" = "1" ]; then
  echo "rollback: GREEN — serving $(basename "$TO")"
  echo "$(date -u +%Y%m%d-%H%M%S) ROLLBACK to $(basename "$TO")" >> "$ROOT/deploy.log"
else
  echo "FAIL: rolled-back artifact also unhealthy — ESCALATE (RB-4 step 3: RB-7)" >&2
  exit 1
fi
