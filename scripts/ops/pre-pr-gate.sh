#!/usr/bin/env bash
# pre-pr-gate.sh — THE gate. Local-first CI for Sentinel (no GitHub Actions
# workflow files on main, per repo policy — this script IS the CI).
#
# Stages (each must pass before the next starts):
#   1. secrets-grep      — fast fail on secret-shaped values
#   2. full test suite   — `python3 -m unittest discover tests` (baseline 496)
#   3. kill-the-client   — the fail-open invariant, named explicitly in the report
#   4. boot smoke        — receiver-smoke.sh: the process actually starts
#   5. config schemas    — flagctl validate on the example configs
#
# Usage: pre-pr-gate.sh [--fast]   (--fast skips the full suite; for deploys,
#                                   NOT for PRs — a PR gate is never --fast)
# Exit 0 = gate passed. Prints a report suitable for pasting into the PR body.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

PASS=0; FAIL=0
LOGDIR="/tmp/sentinel-gate-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOGDIR"
stage() { # name, command...
  local name="$1"; shift
  echo "── stage: $name ──"
  # Full output goes to a per-run log (failure evidence must survive);
  # only the tail is echoed to the report.
  if "$@" >"$LOGDIR/$name.log" 2>&1; then
    tail -5 "$LOGDIR/$name.log"
    echo "PASS: $name"; PASS=$((PASS+1))
  else
    tail -20 "$LOGDIR/$name.log"
    echo "FAIL: $name (full log: $LOGDIR/$name.log)"; FAIL=$((FAIL+1))
  fi
}

echo "pre-pr-gate: repo=$REPO_ROOT sha=$(git rev-parse --short HEAD 2>/dev/null || echo '?')"

stage "secrets-grep" ./scripts/ops/secrets-grep.sh

if [ "$FAST" = "1" ]; then
  echo "── stage: full-suite SKIPPED (--fast; not valid for PRs) ──"
else
  # Full suite; capture the Ran/OK summary for the report.
  echo "── stage: full-suite ──"
  OUT="$(python3 -m unittest discover tests 2>&1 | grep -E '^(Ran |OK|FAILED)' || true)"
  echo "$OUT"
  # The verdict is the unittest summary line, anchored exactly: test stdout
  # itself prints lines starting with "OK" (e.g. "OK: 1 events verified"),
  # so an unanchored ^OK match green-lights a red suite. FAILED is checked
  # first and is always red.
  if echo "$OUT" | grep -q '^FAILED'; then
    echo "FAIL: full-suite"
    FAIL=$((FAIL+1))
  elif echo "$OUT" | grep -q '^OK$'; then
    N=$(echo "$OUT" | grep -oP '^Ran \K[0-9]+' || echo '?')
    echo "PASS: full-suite ($N tests)"
    PASS=$((PASS+1))
    # Test-count guard: tests must not silently disappear.
    if [ "$N" != "?" ] && [ "$N" -lt 496 ]; then
      echo "FAIL: test count $N < baseline 496 without a logged reason"
      FAIL=$((FAIL+1))
    fi
  else
    echo "FAIL: full-suite"
    FAIL=$((FAIL+1))
  fi
fi

# Kill-the-client, named explicitly: this test failing is a release blocker.
echo "── stage: kill-the-client invariant ──"
if python3 -m unittest tests.test_gate -v 2>&1 | grep -qiE "kill.*(ok|PASS)|ok$"; then
  echo "PASS: kill-the-client (fail-open invariant holds)"
  PASS=$((PASS+1))
else
  # fall back: run the module, judge by exit code
  if python3 -m unittest tests.test_gate >/dev/null 2>&1; then
    echo "PASS: kill-the-client (fail-open invariant holds)"
    PASS=$((PASS+1))
  else
    echo "FAIL: kill-the-client — RELEASE BLOCKER"
    FAIL=$((FAIL+1))
  fi
fi

stage "boot-smoke" ./scripts/ops/receiver-smoke.sh

# Ops scripts self-check: every script we ship must at least parse.
echo "── stage: ops-scripts ──"
OPS_OK=1
for py in ./scripts/ops/*.py; do
  python3 -m py_compile "$py" || OPS_OK=0
done
for sh in ./scripts/ops/*.sh; do
  bash -n "$sh" || OPS_OK=0
done
if [ "$OPS_OK" = "1" ]; then
  echo "PASS: ops-scripts (py_compile + bash -n)"
  PASS=$((PASS+1))
else
  echo "FAIL: ops-scripts"
  FAIL=$((FAIL+1))
fi

# Config schema validation on a fresh bootstrap tree.
echo "── stage: config-schemas ──"
TMPD="$(mktemp -d)"; trap 'rm -rf "$TMPD"' EXIT
./scripts/ops/env-bootstrap.sh --config-dir "$TMPD/c" --state-dir "$TMPD/s" \
  --tier local >/dev/null
if python3 ./scripts/ops/flagctl.py validate --config-dir "$TMPD/c"; then
  echo "PASS: config-schemas"; PASS=$((PASS+1))
else
  echo "FAIL: config-schemas"; FAIL=$((FAIL+1))
fi
rm -rf "$TMPD"; trap - EXIT

echo "═══════════════════════════════════════"
echo "GATE: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "GATE RESULT: RED — no merge, no deploy, no exceptions."
  exit 1
fi
echo "GATE RESULT: GREEN"
