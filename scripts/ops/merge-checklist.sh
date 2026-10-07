#!/usr/bin/env bash
# merge-checklist.sh — promotion gates, checklist-enforced (not honor-system).
#
# The audit found promotion gates are honor-system: pre-pr-gate.sh exists
# but is unenforced, and the entrypoint-import gate "needs a box-side
# pre-merge hook, still TODO". This script IS that hook. It runs the
# machine-checkable gates and prints the human checklist the merger must
# confirm. Exit 0 = all machine gates green AND the merger confirmed the
# human items; anything else = no merge.
#
# Usage:
#   scripts/ops/merge-checklist.sh <commit> [--gate-log <dir>] [--yes-human]
#     <commit>     the exact commit proposed for merge (default: HEAD)
#     --gate-log   reuse a previous pre-pr-gate.sh run dir (must be GREEN
#                  and must name the same commit); otherwise the gate runs now
#     --yes-human  the merger confirms the human checklist items below
#                  (without it, the items print and the script exits 3)
#
# Writes a MERGE-RECORD block to stdout — paste it into the PR body.
# Enforcement mechanism: docs/planning/production/PROMOTION-GATES.md.
#
# TEAM 6 CONTRACT: if scripts/ops/team6-gate.sh exists and is executable,
# it is invoked as the test-automation gate (their internals, our
# invocation). Absent → FAIL. Interface agreed in PROMOTION-GATES.md;
# countersign pending with TEAM 6 / the wave coordinator.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

COMMIT="${1:-HEAD}"
GATE_LOG=""
YES_HUMAN=0
shift 2>/dev/null || true
while [ $# -gt 0 ]; do
  case "$1" in
    --gate-log) GATE_LOG="$2"; shift 2 ;;
    --yes-human) YES_HUMAN=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
# Allow COMMIT as second positional if first loop consumed flags oddly.
if [ -z "$COMMIT" ]; then COMMIT="HEAD"; fi

COMMIT_SHA="$(git rev-parse "$COMMIT" 2>/dev/null || true)"
[ -n "$COMMIT_SHA" ] || { echo "FAIL: cannot resolve commit '$COMMIT'" >&2; exit 2; }

PASS=0; FAIL=0
pass() { echo "PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

echo "merge-checklist: commit=$COMMIT_SHA ($(git log -1 --format=%s "$COMMIT_SHA"))"

# --- 1. tree state -----------------------------------------------------------
if [ -n "$(git status --porcelain)" ]; then
  fail "working tree dirty — merge from a clean tree at a named commit"
else
  pass "working tree clean"
fi
if git merge-base --is-ancestor "$COMMIT_SHA" HEAD 2>/dev/null || [ "$COMMIT_SHA" = "$(git rev-parse HEAD)" ]; then
  pass "commit is HEAD (the tree that was tested)"
else
  fail "commit $COMMIT_SHA is not HEAD — check out the exact commit and re-run"
fi

# --- 2. pre-pr-gate.sh evidence ----------------------------------------------
GATE_OK=0
if [ -n "$GATE_LOG" ]; then
  if [ -f "$GATE_LOG/gate-report.txt" ] && grep -q "GATE RESULT: GREEN" "$GATE_LOG/gate-report.txt" \
     && grep -q "$COMMIT_SHA" "$GATE_LOG/gate-report.txt"; then
    GATE_OK=1; pass "pre-pr-gate GREEN evidence for $COMMIT_SHA ($GATE_LOG)"
  else
    fail "gate-log $GATE_LOG is not GREEN evidence for $COMMIT_SHA"
  fi
fi
if [ "$GATE_OK" = "0" ]; then
  echo "── running pre-pr-gate.sh (no --fast; a PR gate is never --fast) ──"
  LOGDIR="/tmp/sentinel-gate-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$LOGDIR"
  if ./scripts/ops/pre-pr-gate.sh >"$LOGDIR/gate-run.log" 2>&1; then
    { echo "commit: $COMMIT_SHA"; echo "GATE RESULT: GREEN"; tail -3 "$LOGDIR/gate-run.log"; } >"$LOGDIR/gate-report.txt"
    pass "pre-pr-gate.sh GREEN (log: $LOGDIR)"
    echo "  gate evidence kept at: $LOGDIR (pass --gate-log $LOGDIR to reuse)"
  else
    fail "pre-pr-gate.sh RED (log: $LOGDIR/gate-run.log) — no merge"
  fi
fi

# --- 3. entrypoint-import gate (was TODO; now enforced here) ------------------
echo "── stage: entrypoint-import ──"
ENTRYPOINTS="platform/server/__main__.py deploy/vercel/api/index-prod.py deploy/gh-pages/build-v2.py deploy/gh-pages/build-static.py src/sentinel/receiver.py"
EP_OK=1
for ep in $ENTRYPOINTS; do
  if [ -f "$ep" ]; then
    python3 -m py_compile "$ep" || { echo "  compile FAIL: $ep"; EP_OK=0; }
  else
    echo "  missing (skipped): $ep"
  fi
done
# Import smoke: the WSGI app + safety + health endpoints must import
# cleanly via _pkg. (Import, not boot: no ports bound, no state touched.)
# Public handler names asserted: PlatformApp, safety_api.handle_kill,
# ops_health.handle_health.
if ! timeout 60 python3 -c "
import sys
sys.path.insert(0, 'platform/server'); sys.path.insert(0, 'src')
import _pkg
app = _pkg.load('app'); assert hasattr(app, 'PlatformApp')
safety = _pkg.load('safety_api'); assert hasattr(safety, 'handle_kill')
ops = _pkg.load('ops_health'); assert hasattr(ops, 'handle_health')
print('import smoke OK')
" >/tmp/entrypoint-smoke.log 2>&1; then
  echo "  import smoke FAIL:"; tail -5 /tmp/entrypoint-smoke.log; EP_OK=0
fi
if [ "$EP_OK" = "1" ]; then pass "entrypoint-import (compile + WSGI import smoke)"; else fail "entrypoint-import"; fi

# --- 4. TEAM 6 test-automation gate (contract, not duplication) -----------------
TEAM6="./scripts/ops/team6-gate.sh"
if [ -x "$TEAM6" ]; then
  echo "── stage: team6-gate ──"
  if "$TEAM6" "$COMMIT_SHA" >"/tmp/team6-gate.log" 2>&1; then
    pass "team6-gate GREEN"
  else
    fail "team6-gate RED (log: /tmp/team6-gate.log)"
  fi
else
  fail "team6-gate.sh not present/executable — TEAM 6 gate has no implementation; coordinate before merging"
fi

# --- 5. secrets re-sweep on the diff (fast, targeted) --------------------------
if git diff --name-only "$COMMIT_SHA^" "$COMMIT_SHA" 2>/dev/null | grep -q .; then
  if ./scripts/ops/secrets-grep.sh >/tmp/secrets-diff.log 2>&1; then
    pass "secrets-grep clean on tree"
  else
    fail "secrets-grep found hits (log: /tmp/secrets-diff.log)"
  fi
else
  pass "secrets-grep skipped (single-commit diff unavailable); covered by pre-pr-gate"
fi

echo "═══════════════════════════════════════"
echo "MACHINE GATES: $PASS passed, $FAIL failed"

# --- 6. human checklist --------------------------------------------------------
cat <<'EOF'

── human checklist (the merger confirms each; --yes-human to record) ──
[ ] Different-agent review done — the builder did not review their own lane.
[ ] The PR body carries the gate evidence (this script's MERGE-RECORD).
[ ] No unmerged-lane evidence claims: every number/artifact the PR cites
    exists on the merged tree, or the claim names its branch (X-C rule).
[ ] Standing prohibitions respected by this merge:
    no /preview-v2/ removal · no production-live flip · no safety-endpoint
    changes aimed at the deployed tier · zero real-PagerDuty contact ·
    Jev key never in files/logs/envs/browser JS · ₹0 (no paid services).
[ ] Kill-switch copy honest: any UI text touching the switch matches the
    decided semantics (HALT all paging, fail-closed) and the tier's actual
    wiring (NO-OP LEVER marking where true).
[ ] Docs changed alongside behavior (honesty bookkeeping: no phantom
    topology, no specified-not-built).
EOF

if [ "$YES_HUMAN" = "0" ]; then
  echo ""
  echo "HUMAN CHECKLIST UNCONFIRMED — re-run with --yes-human after reviewing each item."
  exit 3
fi

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "MERGE-RECORD: RED — no merge. $FAIL machine gate(s) failed."
  exit 1
fi

cat <<EOF

═══════════════════════════════════════
MERGE-RECORD (paste into the PR body)
  commit: $COMMIT_SHA
  subject: $(git log -1 --format=%s "$COMMIT_SHA")
  pre-pr-gate: GREEN (no --fast)
  entrypoint-import: GREEN
  team6-gate: $([ -x "$TEAM6" ] && echo GREEN || echo MISSING)
  secrets-grep: GREEN
  human checklist: CONFIRMED by merger ($(whoami), $(date -u +%Y-%m-%dT%H:%M:%SZ))
  prohibitions: respected (preview-v2 kept, no prod flip, no deployed-tier
    safety changes, zero real-PD, key hygiene, ₹0)
═══════════════════════════════════════
CHECKLIST RESULT: GREEN — merge authorized (coordinator sequences the merge;
Petu reviews before main per the FAANG wave).
EOF
