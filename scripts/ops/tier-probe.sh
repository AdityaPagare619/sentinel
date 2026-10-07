#!/usr/bin/env bash
# tier-probe.sh — the executable spec of the uptime-monitor config.
#
# The 4 unauthenticated checks from ops/watcher-deployment.md, runnable
# from any box. Exit 0 = tier looks right; exit 1 = page the human.
# This script creates no schedule and no watcher: enabling a cron for it
# is the coordinator's / Aditya's decision (standing order: local gates
# only; the external monitor in docs/planning/rfc/platform-observability.md
# is the recommended watcher). The monitor's dashboard config MUST mirror
# these checks — drift between doc, script, and dashboard is a bug.
#
# Usage: scripts/ops/tier-probe.sh [base-url]
#   default base: https://sentinel-platform-adityapagare619s-projects.vercel.app
#
# Checks (all unauthenticated — the probe holds no operator token):
#   1. GET /api/v1/health/live            -> 200
#   2. GET /api/v1/ops/health             -> 401 (auth still fail-closed)
#   3. POST /api/v1/safety/kill           -> 401 fast (no 500, no 200)
#   4. OPTIONS /api/v1/ops/health (CORS preflight, lowercase Pages origin)
#                                         -> 204 + exact ACAO
#   5. OPTIONS with evil origin           -> no ACAO (allowlist, no reflection)
#   6. X-Sentinel-Deployment header       -> production-api (right tier?)
set -euo pipefail

BASE="${1:-https://sentinel-platform-adityapagare619s-projects.vercel.app}"
PASS=0; FAIL=0
pass() { echo "PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@"; }

echo "tier-probe: $BASE ($(date -u +%Y-%m-%dT%H:%M:%SZ))"

[ "$(code "$BASE/api/v1/health/live")" = "200" ] \
  && pass "liveness 200" || fail "liveness != 200"

[ "$(code "$BASE/api/v1/ops/health")" = "401" ] \
  && pass "ops/health unauth 401 (fail-closed)" || fail "ops/health unauth != 401"

[ "$(code -X POST "$BASE/api/v1/safety/kill")" = "401" ] \
  && pass "kill POST unauth 401" || fail "kill POST unauth != 401"

PRE="$(curl -s -D - -o /dev/null --max-time 15 -X OPTIONS "$BASE/api/v1/ops/health" \
  -H "Origin: https://adityapagare619.github.io" \
  -H "Access-Control-Request-Method: GET")"
if echo "$PRE" | grep -qE "^HTTP/[0-9.]+ 204"; then
  if echo "$PRE" | grep -iq "^access-control-allow-origin: https://adityapagare619.github.io"; then
    pass "CORS preflight 204 + exact ACAO (lowercase origin)"
  else
    fail "preflight 204 but ACAO missing/wrong (the 2026-10-07 regression class)"
  fi
else
  fail "preflight != 204"
fi

EVIL_ACAO="$(curl -s -D - -o /dev/null --max-time 15 -X OPTIONS "$BASE/api/v1/ops/health" \
  -H "Origin: https://evil.example" \
  -H "Access-Control-Request-Method: GET" | grep -ci "^access-control-allow-origin" || true)"
[ "$EVIL_ACAO" = "0" ] \
  && pass "evil origin gets no ACAO" || fail "evil origin got ACAO (allowlist broken)"

DEPLOY_HDR="$(curl -s -D - -o /dev/null --max-time 15 "$BASE/api/v1/health/live" \
  | grep -i "^x-sentinel-deployment:" | tr -d '\r' || true)"
if echo "$DEPLOY_HDR" | grep -q "production-api"; then
  pass "X-Sentinel-Deployment: production-api"
else
  fail "deployment header missing/wrong: '$DEPLOY_HDR'"
fi

echo "──────────────────────────────────"
echo "PROBE: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
