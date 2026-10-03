#!/usr/bin/env bash
# disk-watermark.sh — external disk eye for the watcher (RB-6).
#
# Checks filesystem usage of the state dir's mount. Nagios-style exits:
#   0 = OK, 1 = WARNING, 2 = CRITICAL, 3 = UNKNOWN (couldn't measure)
# Prints a one-line status plus the usage figures for logs.
#
# Intended to run from the external watcher's cron every 5 min. The in-process
# D14 guard (eventlog.py WAL watermark) is the inner eye; this is the outer one.
# Either firing alone is actionable; both firing is RB-6.
#
# Usage: disk-watermark.sh --path DIR [--warn-pct 80] [--crit-pct 90]

set -euo pipefail

PATH_DIR=""
WARN=80
CRIT=90

while [ $# -gt 0 ]; do
  case "$1" in
    --path)     PATH_DIR="$2"; shift 2 ;;
    --warn-pct) WARN="$2";     shift 2 ;;
    --crit-pct) CRIT="$2";     shift 2 ;;
    *) echo "UNKNOWN: unknown arg $1" >&2; exit 3 ;;
  esac
done

[ -n "$PATH_DIR" ] || { echo "UNKNOWN: --path required" >&2; exit 3; }
[ -d "$PATH_DIR" ] || { echo "UNKNOWN: not a directory: $PATH_DIR" >&2; exit 3; }

DF=$(df -P "$PATH_DIR" 2>/dev/null | tail -1) || { echo "UNKNOWN: df failed" >&2; exit 3; }
USE_PCT=$(echo "$DF" | awk '{print $5}' | tr -d '%')
AVAIL_KB=$(echo "$DF" | awk '{print $4}')
MOUNT=$(echo "$DF" | awk '{print $6}')

case "$USE_PCT" in ''|*[!0-9]*) echo "UNKNOWN: unparsable df output" >&2; exit 3;; esac

STATUS="OK"; CODE=0
if [ "$USE_PCT" -ge "$CRIT" ]; then STATUS="CRITICAL"; CODE=2
elif [ "$USE_PCT" -ge "$WARN" ]; then STATUS="WARNING"; CODE=1
fi

echo "$STATUS: $USE_PCT% used on $MOUNT (${AVAIL_KB}KB avail) for $PATH_DIR | usage=${USE_PCT}%;${WARN};${CRIT}"
exit "$CODE"
