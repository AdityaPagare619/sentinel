#!/usr/bin/env bash
# receiver-smoke.sh — "does the process actually start and serve?"
#
# Boots the receiver from a FRESH env-bootstrap.sh tree in mock mode
# (SENTINEL_MOCK=1: no vendor calls, no secrets), waits for /livez, POSTs a
# synthetic PagerDuty trigger to /v2/enqueue, asserts the PD-shaped response,
# then shuts the server down. Catches "tests pass but the process doesn't
# start" (config schema drift, import cycles, port binding).
#
# Usage: receiver-smoke.sh [--port 18080]   (default: random free port)
# Exit 0 = smoke passed. Prints a one-line verdict per stage.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORT="${1:-0}"
[ "${1:-}" = "--port" ] && PORT="$2"

if [ "$PORT" = "0" ] || [ -z "$PORT" ]; then
  PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("",0)); print(s.getsockname()[1]); s.close()')
fi

WORK="$(mktemp -d /tmp/sentinel-smoke.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

say() { echo "smoke[$1]: $2"; }

# --- 1. bootstrap a fresh tree ------------------------------------------------
"$REPO_ROOT/scripts/ops/env-bootstrap.sh" \
  --config-dir "$WORK/config" --state-dir "$WORK/state" --tier local >/dev/null
say "bootstrap" "ok"

# --- 2. boot the receiver (mock mode; PD pointed at a blackhole) --------------
export SENTINEL_MOCK=1
export SENTINEL_WEBHOOK_SECRET="smoke-test-dummy-secret-32chars"
export SENTINEL_CONFIG_DIR="$WORK/config"
export SENTINEL_STATE_DIR="$WORK/state"
export SENTINEL_DB="$WORK/state/sentinel.db"
export SENTINEL_HEALTH_TOKEN="smoke-health-token"
export PD_EVENTS_URL="http://127.0.0.1:9/v2/enqueue"  # discard port: refuse fast
export PD_ROUTING_KEY="smoke-routing-key"
export PYTHONPATH="$REPO_ROOT/src"

python3 -m sentinel.receiver --port "$PORT" --bind 127.0.0.1 \
  >"$WORK/server.log" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; rm -rf "$WORK"' EXIT

# --- 3. wait for /livez --------------------------------------------------------
ok=0
for _ in $(seq 1 40); do
  if curl -sf -m 2 "http://127.0.0.1:$PORT/livez" >/dev/null 2>&1; then ok=1; break; fi
  sleep 0.25
done
[ "$ok" = "1" ] || { say "livez" "FAIL (server log:)"; tail -20 "$WORK/server.log"; exit 1; }
say "livez" "ok (port $PORT, pid $SRV)"

# --- 4. synthetic PD trigger ----------------------------------------------------
RESP=$(curl -s -m 10 -X POST "http://127.0.0.1:$PORT/v2/enqueue" \
  -H 'Content-Type: application/json' -d '{
    "routing_key": "smoke-routing-key",
    "event_action": "trigger",
    "dedup_key": "smoke-001",
    "payload": {
      "summary": "smoke test: CPU high on web-1",
      "source": "web-1",
      "severity": "critical",
      "component": "cpu",
      "custom_details": {"host": "web-1"}
    }
  }')
echo "$RESP" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d.get('status') == 'success', d
assert 'dedup_key' in d, d
print('smoke[enqueue]: ok (status=success, dedup_key=%s)' % d['dedup_key'])
" || { say "enqueue" "FAIL: $RESP"; exit 1; }

# --- 5. deep health -------------------------------------------------------------
HCODE=$(curl -s -m 5 -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $SENTINEL_HEALTH_TOKEN" \
  "http://127.0.0.1:$PORT/healthz")
[ "$HCODE" = "200" ] || { say "healthz" "FAIL (http $HCODE)"; exit 1; }
say "healthz" "ok (http 200)"

# --- 6. audit row landed ----------------------------------------------------------
ROWS=$(python3 -c "
import sqlite3
db = sqlite3.connect('$WORK/state/sentinel.db')
n = db.execute('select count(*) from decisions').fetchone()[0]
print(n)
" 2>/dev/null || echo 0)
[ "${ROWS:-0}" -ge 1 ] || { say "audit" "FAIL (no decision rows)"; exit 1; }
say "audit" "ok ($ROWS decision row(s) written)"

kill $SRV 2>/dev/null; wait $SRV 2>/dev/null || true
trap - EXIT; rm -rf "$WORK"
say "verdict" "PASS — receiver boots, serves, triages, audits, shuts down cleanly"
