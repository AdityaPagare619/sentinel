#!/usr/bin/env bash
# env-bootstrap.sh — build a working Sentinel environment tree from zero.
#
# Creates: <config-dir>/{thresholds.json,allowlist.json,flags.json},
#          <state-dir>/, and an env template (PLACEHOLDERS, never values).
# Idempotent: never overwrites existing files; exits non-zero on a bad tree.
#
# Usage:
#   scripts/ops/env-bootstrap.sh [--config-dir DIR] [--state-dir DIR] [--tier local|staging-shadow|staging-lab|prod]
#
# L3 DevOps foundation. See ops/devops-foundation.md §1.

set -euo pipefail

CONFIG_DIR="./sentinel-config"
STATE_DIR="./sentinel-state"
TIER="local"

while [ $# -gt 0 ]; do
  case "$1" in
    --config-dir) CONFIG_DIR="$2"; shift 2 ;;
    --state-dir)  STATE_DIR="$2";  shift 2 ;;
    --tier)       TIER="$2";       shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

case "$TIER" in
  local|staging-shadow|staging-lab|prod) ;;
  *) echo "unknown tier: $TIER (local|staging-shadow|staging-lab|prod)" >&2; exit 2 ;;
esac

# --- Python check -----------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "FAIL: python3 not found" >&2; exit 1
fi
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYMAJ=$(python3 -c 'import sys; print(sys.version_info.major)')
PYMIN=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PYMAJ" -lt 3 ] || { [ "$PYMAJ" -eq 3 ] && [ "$PYMIN" -lt 12 ]; }; then
  echo "FAIL: python3 >= 3.12 required, found $PYVER" >&2; exit 1
fi
echo "ok: python3 $PYVER (stdlib-only — no pip deps needed)"

# --- Directories ------------------------------------------------------------
mkdir -p "$CONFIG_DIR" "$STATE_DIR"
chmod 700 "$CONFIG_DIR" "$STATE_DIR"

write_once() { # path, content via stdin
  local path="$1"
  if [ -e "$path" ]; then
    echo "keep: $path (already exists)"
  else
    cat > "$path"
    echo "wrote: $path"
  fi
}

# --- thresholds.json (ARCHITECTURE.md §3.2 defaults; validated by ConfigLoader)
write_once "$CONFIG_DIR/thresholds.json" <<'EOF'
{
  "suppress_p1_max": 0.002,
  "suppress_conf_min": 0.90,
  "page_p1p2_min": 0.30,
  "uncertain_conf_max": 0.50,
  "queue_conf_min": 0.70
}
EOF

# --- allowlist.json (customer-verified known-noise fingerprints, 16-hex)
write_once "$CONFIG_DIR/allowlist.json" <<'EOF'
[]
EOF

# --- flags.json (L3 release-control surface, ops/devops-foundation.md §2.1)
write_once "$CONFIG_DIR/flags.json" <<'EOF'
{
  "version": 1,
  "_comment": "Feature flags. Contract: ops/devops-foundation.md §2.1. Edited ONLY via flagctl.py (atomic, versioned, audited).",
  "flags": {
    "global_kill_switch": {
      "type": "bool", "value": false,
      "description": "ON = gate returns passthrough for every alert (instant flag-off). Turning OFF requires two named humans.",
      "owner": "on-call"
    },
    "suppress_enabled": {
      "type": "bool", "value": true,
      "description": "OFF = suppress dispositions become passthrough (never silently queued).",
      "owner": "on-call"
    },
    "shadow_mode": {
      "type": "bool", "value": false,
      "description": "ON = log the would-be disposition, always return passthrough. Mirrors SENTINEL_SHADOW.",
      "owner": "releaser"
    },
    "canary_severity_bands": {
      "type": "list", "value": [],
      "description": "Non-empty = candidate policy applies only to these inbound severity bands (e.g. [\"warning\",\"info\"]).",
      "owner": "releaser"
    },
    "canary_services": {
      "type": "list", "value": [],
      "description": "Non-empty = candidate policy applies only to these services. Canary by blast radius.",
      "owner": "releaser"
    }
  },
  "changed_by": "env-bootstrap",
  "changed_at": null
}
EOF

# --- env template: placeholders only, NEVER values ---------------------------
write_once "$CONFIG_DIR/sentinel.env.example" <<'EOF'
# Sentinel environment — copy to sentinel.env (600 perms), fill in, never commit.
# The real file is loaded by the supervisor, never by git.
#
# Tier: local | staging-shadow | staging-lab | prod
export SENTINEL_TIER="local"
export SENTINEL_CONFIG_DIR="<config-dir>"
export SENTINEL_STATE_DIR="<state-dir>"
export SENTINEL_DB="<state-dir>/sentinel.db"

# --- secrets (REQUIRED in prod; unset in local/staging-lab mock mode) --------
# export TYPESAFE_API_KEY="<set-me>"
# export SENTINEL_WEBHOOK_SECRET="<set-me>"        # empty refuses startup in prod
# export PD_ROUTING_KEY="<set-me>"                 # NEVER set in staging tiers
# export SENTINEL_HEALTH_TOKEN="<set-me>"          # bearer for /healthz and /-/reload

# --- behavior ---------------------------------------------------------------
export SENTINEL_MOCK="1"        # 1 = MockSystemOneClient, no vendor calls (local/lab)
export SENTINEL_SHADOW="0"      # 1 = shadow mode (staging-shadow)
export SENTINEL_PORT="8080"
export SENTINEL_MAX_INFLIGHT="64"
# export SENTINEL_RESTART_CMD="<supervisor restart command>"  # used by deploy.sh
EOF

# --- tier guardrails ----------------------------------------------------------
if [ "$TIER" = "staging-shadow" ] || [ "$TIER" = "staging-lab" ]; then
  echo "note: tier=$TIER — PD_ROUTING_KEY must stay UNSET here (never-pages rule)."
fi
if [ "$TIER" = "prod" ]; then
  echo "note: tier=prod — complete the cutover checklist (devops-foundation.md §1.2) before first start."
fi

# --- validate what we wrote ---------------------------------------------------
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if ! PYTHONPATH="$REPO_ROOT/src" python3 -c "
import json, sys
sys.path.insert(0, '$REPO_ROOT/src')
from sentinel.config import ConfigLoader, ConfigRejected
loader = ConfigLoader(config_dir='$CONFIG_DIR', state_dir='$STATE_DIR')
try:
    p = loader.load_startup()
    print(f'ok: config generation {p.generation} validates (fail-closed loader)')
except ConfigRejected as e:
    print(f'FAIL: {e}'); sys.exit(1)
"; then
  echo "FAIL: generated config did not validate" >&2; exit 1
fi

echo "done: tier=$TIER config=$CONFIG_DIR state=$STATE_DIR"
