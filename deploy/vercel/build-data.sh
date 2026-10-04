#!/usr/bin/env bash
# Build the demo dataset snapshot for the hosted deployment (deploy lane).
#
# Deterministic; needs NO Jev key and NO network.
#   demo.db ......... rebuilt from demo/storm-scenario/event-log.jsonl
#                     (the real engine's recorded synthetic storm, 2026-10-03)
#                     using the engine's own sentinel.eventlog schema, plus
#                     synthetic reference labels via load_outcomes.py --seed 42
#                     (the labels-v3 precedent: "seeded synthetic labels;
#                     data_source=synthetic, loudly")
#   context.jsonl ... the storm's alert-context map (title/service/check)
#   datasets/labels-v3.jsonl ... platform/server DatasetRegistry build
#                     (sentinel.synthetic, seed 7, n=2000; hash-pinned)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$REPO/deploy/dist-data"
mkdir -p "$OUT/datasets"

python3 - "$REPO" "$OUT" <<'EOF'
import json, os, sqlite3, sys
repo, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(repo, "src"))
from sentinel.eventlog import _SCHEMA, _DECISIONS_VIEW

db_path = os.path.join(out, "demo.db")
if os.path.exists(db_path):
    os.remove(db_path)
con = sqlite3.connect(db_path)
con.executescript(_SCHEMA)
con.executescript(_DECISIONS_VIEW)
n = 0
with open(os.path.join(repo, "demo/storm-scenario/event-log.jsonl")) as fh:
    for line in fh:
        e = json.loads(line)
        con.execute(
            """INSERT INTO events (seq,event_id,schema_v,ts,actor,type,alert_id,
               fingerprint,episode_id,outbox_id,body,prev_hash,row_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (e["seq"], e["event_id"], e.get("schema_v", 1), e["ts"], e["actor"],
             e["type"], e["alert_id"], e["fingerprint"], e.get("episode_id", ""),
             e.get("outbox_id"), e["body"], e.get("prev_hash", ""),
             e.get("row_hash", "")))
        n += 1
con.commit()
print(f"[data] events loaded: {n}")
con.close()
EOF

PYTHONPATH="$REPO/src" python3 "$REPO/demo/storm-scenario/load_outcomes.py" \
  --db "$OUT/demo.db" --seed 42

PYTHONPATH="$REPO/platform/server:$REPO/src" python3 - "$REPO" "$OUT" <<'EOF'
import os, sys
repo, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(repo, "platform/server"))
import _pkg
reg = _pkg.load("datasets").DatasetRegistry(os.path.join(out, "datasets"))
rows, sha, meta = reg.get("labels-v3")
print(f"[data] labels-v3: {len(rows)} rows sha={sha[:16]}")
EOF

cp "$REPO/demo/storm-scenario/alert-context.jsonl" "$OUT/context.jsonl"
chmod 444 "$OUT/demo.db"
echo "[data] snapshot complete -> $OUT"
