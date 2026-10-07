#!/usr/bin/env bash
# gate-entrypoints.sh — the entrypoint-import gate (standing law:
# full compileall + entrypoint imports on every program merge).
#
# Stages:
#   1. compileall — every .py in the repo compiles (excludes: .git,
#      __pycache__, node_modules, generated deploy/dist* trees).
#   2. entrypoint imports — every program entrypoint (files with an
#      `if __name__ == "__main__"` guard) imports cleanly in a hermetic
#      subprocess: sys.path = [entrypoint dir, <repo>/src, <repo>].
#      src/sentinel/*.py modules import as sentinel.* (package form).
#   3. hermeticity — no entrypoint inserts an absolute out-of-repo path
#      into sys.path (works-on-my-machine coupling).
#
# Verdicts per module: PASS | QUARANTINED (ticket ref) | FAIL.
# Exit 0 = no FAIL. Exit 1 = any FAIL (per-module tracebacks printed).
#
# Quarantine: scripts/ops/gate-entrypoints.quarantine — one module per
# line: "<repo-relative path> # ticket: <path> <date> <owner>".
# A quarantine entry is HONEST only while its ticket file exists; a
# dangling reference is itself a FAIL (no silent rot).
#
# Interface contract (RFC quality-gate-interface): exit 0 = GREEN,
# non-zero = RED. No "green with warnings".

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
QUAR="$REPO/scripts/ops/gate-entrypoints.quarantine"

PASS=0; QUAR_N=0; FAIL=0
FAILED_MODS=()

quarantined() { # path, check -> prints ticket ref; rc 1 = not quarantined, 2 = dangling
  [ -f "$QUAR" ] || return 1
  local line
  line="$(grep -E "^${1}[[:space:]]+${2}([[:space:]]|#)" "$QUAR" | head -1 || true)"
  [ -n "$line" ] || return 1
  local ticket
  ticket="$(echo "$line" | sed -E 's/.*ticket:[[:space:]]*([^[:space:]]+).*/\1/')"
  if [ -z "$ticket" ] || [ ! -f "$REPO/$ticket" ]; then
    echo "  QUARANTINE INVALID: $1 [$2] references missing ticket ($ticket)"
    return 2
  fi
  echo "$ticket"
  return 0
}

report() { # verdict, path, detail
  case "$1" in
    PASS) PASS=$((PASS+1)); echo "PASS: $2";;
    QUARANTINED) QUAR_N=$((QUAR_N+1)); echo "QUARANTINED: $2 ($3)";;
    FAIL) FAIL=$((FAIL+1)); FAILED_MODS+=("$2"); echo "FAIL: $2"; [ -n "${3:-}" ] && echo "$3" | tail -5 | sed 's/^/      /';;
  esac
}

echo "── gate-entrypoints: compileall ──"
if python3 -m compileall -q \
    -x '(\.git|__pycache__|node_modules|deploy/dist[^/]*|\.db$)' . 2>/tmp/compileall.err; then
  echo "PASS: compileall (whole tree)"
  PASS=$((PASS+1))
else
  echo "FAIL: compileall"; tail -10 /tmp/compileall.err | sed 's/^/      /'
  FAIL=$((FAIL+1))
fi

echo "── gate-entrypoints: entrypoint imports ──"
TMP_PROBE="$(mktemp -d)"; trap 'rm -rf "$TMP_PROBE"' EXIT
cat > "$TMP_PROBE/probe.py" <<'PYEOF'
import importlib.util, os, sys
ep = sys.argv[1]
repo = sys.argv[2]
d = os.path.dirname(os.path.abspath(ep))
for p in (d, os.path.join(repo, "src"), repo):
    if p not in sys.path:
        sys.path.insert(0, p)
rel = os.path.relpath(os.path.abspath(ep), repo)
if rel.startswith("src" + os.sep):
    # package modules: import as sentinel.* (relative imports require it)
    modname = rel[:-3].replace(os.sep, ".")
    mod = importlib.import_module(modname)
else:
    name = "gateprobe_" + os.path.basename(ep).replace(".", "_").replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, ep)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
print("PROBE-OK")
PYEOF

while IFS= read -r ep; do
  rel="${ep#./}"
  if ticket="$(quarantined "$rel" import)"; then
    report QUARANTINED "$rel [import]" "$ticket"
    continue
  elif [ "$?" = "2" ]; then
    report FAIL "$rel" "dangling quarantine reference"
    continue
  fi
  # NB: the probe is EXPECTED to fail on broken modules — capture rc
  # with set -e suspended, or the first broken module aborts the gate
  # silently instead of being reported (caught by the gate's own
  # failure-proof, 2026-10-07).
  set +e
  out="$(timeout 90 python3 "$TMP_PROBE/probe.py" "$ep" "$REPO" 2>&1)"
  rc=$?
  set -e
  if [ "$rc" = "0" ] && [ "$out" = "PROBE-OK" ]; then
    report PASS "$rel"
  else
    report FAIL "$rel" "$out"
  fi
done < <(grep -rl --include="*.py" -e "^if __name__ == ['\"]__main__['\"]" . \
           | grep -v -e '\.git' -e '__pycache__' -e '^\./tests/' | sort)

echo "── gate-entrypoints: hermeticity (no absolute out-of-repo sys.path) ──"
HERM_FAIL=0
while IFS= read -r ep; do
  rel="${ep#./}"
  hits="$(grep -nE 'sys\.path\.insert[^#]*"/[^"]*"|sys\.path\.insert[^#]*'"'"'/[^'"'"']*'"'"'' "$ep" || true)"
  # inserts resolving inside the repo (via __file__/REPO) are fine; only
  # absolute literals are flagged
  if [ -n "$hits" ]; then
    if ticket="$(quarantined "$rel" hermeticity)"; then
      report QUARANTINED "$rel [hermeticity]" "$ticket"
    elif [ "$?" = "2" ]; then
      report FAIL "$rel" "dangling quarantine reference"
    else
      report FAIL "$rel [hermeticity]" "$hits"
    fi
  fi
done < <(grep -rl --include="*.py" -e "^if __name__ == ['\"]__main__['\"]" . \
           | grep -v -e '\.git' -e '__pycache__' -e '^\./tests/' | sort)
[ "$HERM_FAIL" = "0" ] || true

echo "═══════════════════════════════════════"
echo "GATE-ENTRYPOINTS: $PASS passed, $QUAR_N quarantined, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "GATE RESULT: RED — no merge, no deploy, no exceptions."
  printf 'failed modules:\n  %s\n' "${FAILED_MODS[@]}"
  exit 1
fi
echo "GATE RESULT: GREEN"
