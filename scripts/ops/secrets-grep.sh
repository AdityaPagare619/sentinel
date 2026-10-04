#!/usr/bin/env bash
# secrets-grep.sh — fail-closed secret scan for the pre-PR gate.
#
# Looks for secret VALUES (not env-var names) in the working tree.
# Exit 0 = clean, exit 1 = hits found (printed with file:line).
#
# Known-name exclusions are explicit below. If you add a real-looking value
# to the repo, this script is supposed to catch it — do not "fix" a hit by
# editing this script unless the value is provably a fixture/placeholder.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

PATTERNS=(
  # assignment-style values: key = "...." with a long opaque value
  '(api[_-]?key|secret|passwd|password|private[_-]?key)[[:space:]]*[:=][[:space:]]*["'\'']?[A-Za-z0-9_./+=-]{20,}["'\'']?'
  # bearer tokens in code/text
  '[Bb]earer[[:space:]]+[A-Za-z0-9_./+=-]{20,}'
  # vendor key prefixes that must never appear literally
  'sk-(live|test)-[A-Za-z0-9]{16,}'
  'ghp_[A-Za-z0-9]{20,}'
  'gho_[A-Za-z0-9]{20,}'
  'xox[baprs]-[A-Za-z0-9-]{10,}'
  # routing keys / webhook secrets with plausible real values (32+ hex)
  '(routing[_-]?key|webhook[_-]?secret)[[:space:]]*[:=][[:space:]]*["'\'']?[0-9a-fA-F]{32,}["'\'']?'
)

# Paths never scanned (vendored, git internals, caches, local state,
# and this script itself — its patterns are meta, not secrets).
PRUNE=( .git __pycache__ .pytest_cache__ node_modules '*.db' '*.db-wal' '*.db-shm'
        scripts/ops/secrets-grep.sh )

find_args=()
for p in "${PRUNE[@]}"; do
  find_args+=( -path "./$p" -prune -o )
done

HITS=0
TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT

for pat in "${PATTERNS[@]}"; do
  # shellcheck disable=SC2086
  find . ${find_args[@]} -type f -print0 2>/dev/null \
    | xargs -0 grep -nHEI -- "$pat" 2>/dev/null >> "$TMP" || true
done

# Explicit allowlist: fixture placeholders that LOOK secret-shaped but are
# provably fake. Each entry: file:regex. Skipped hits are printed as
# ALLOWLISTED (stderr) so the gate report shows what was excused and why.
# Keep this list short and justified; a real secret must never be "fixed"
# by adding it here.
ALLOWLIST=(
  # placeholder auth string in the demo storm runner, not a credential
  'demo/storm-scenario/storm_runner.py:surrogate-auth-via-authd'
  # "secret:pd/control_routing_key" is the codebase's secret REFERENCE
  # convention (a name, never a value) — see forwarder.py CONTROL_ROUTING_KEY_REF
  'src/sentinel/eventlog.py:secret:pd/control_routing_key'
  'src/sentinel/forwarder.py:secret:pd/control_routing_key'
  'tests/test_durable_forwarder.py:secret:pd/control_routing_key'
  # attribute access in code, not a value
  'src/sentinel/receiver.py:self\.pipeline\.config\.webhook_secret'
  # test fixture, named as such
  'tests/test_receiver.py:s3cret-long-enough-for-tests'
)

if [ -s "$TMP" ]; then
  while IFS= read -r line; do
    skip=0
    rel="${line#./}"  # normalize: allowlist entries use bare repo-relative paths
    for rule in "${ALLOWLIST[@]}"; do
      file="${rule%%:*}"; rx="${rule#*:}"
      if [[ "$rel" == "$file:"* ]] && [[ "$rel" =~ $rx ]]; then skip=1; break; fi
    done
    if [ "$skip" -eq 0 ]; then
      echo "SECRET-HIT: $line"
      HITS=1
    else
      echo "ALLOWLISTED: $line" >&2
    fi
  done < "$TMP"
fi

if [ "$HITS" -eq 0 ]; then
  echo "ok: secrets-grep clean"
  exit 0
else
  echo "FAIL: secret-shaped values found above — remove them, never commit them" >&2
  exit 1
fi
