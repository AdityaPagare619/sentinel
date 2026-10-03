"""Emergency spillover records (design 03 §6 F1 — the degraded ladder).

When the outbox is unavailable, the page still goes out via the standby
direct-to-PD send — and the evidence of that send lands HERE: a JSON record
on disk (stderr is the first record, the spill file is the durable one).
The next forwarder startup replays every spill into the event log, so the
audit trail is complete even though the outbox never saw the page.

The spill file is pre-allocated at startup (the directory is created when
the forwarder starts), so a disk-full crisis still has somewhere to write:
the record goes to stderr first, the file second.

Spill record schema (JSON):
    ts, kind, alert_id, fingerprint, episode_id, dedup_key,
    payload_sha256, routing_key_ref, reason,
    pd_outcome ("accepted"|"retryable"|"terminal"|"send_error"),
    pd_status, error
"""

from __future__ import annotations

import json
import os
import sys
import time

SPILL_PREFIX = "spill-"
SPILL_SUFFIX = ".json"
REPLAYED_SUFFIX = ".replayed"


def _utc_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def write_spill(spill_dir: str | None, record: dict) -> str | None:
    """Append one spill record. Best-effort and NEVER raises — the degraded
    path must not sink the hot path. Returns the path, or None."""
    if not spill_dir:
        return None
    try:
        os.makedirs(spill_dir, exist_ok=True)
        name = (f"{SPILL_PREFIX}{int(time.time() * 1000)}"
                f"-{os.getpid()}{SPILL_SUFFIX}")
        path = os.path.join(spill_dir, name)
        full = {"ts": _utc_ts(), **record}
        # Write-then-rename: a crash mid-write never leaves a half record.
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(full, fh, sort_keys=True)
        os.replace(tmp, path)
        return path
    except OSError as exc:
        print(f"[sentinel] SPILL WRITE FAILED dir={spill_dir} err={exc}",
              file=sys.stderr)
        return None


def iter_spills(spill_dir: str | None) -> list[tuple[str, dict]]:
    """Read all un-replayed spill records, oldest first. Skips corrupt
    files loudly (a corrupt spill is evidence of a sick disk — say so)."""
    out: list[tuple[str, dict]] = []
    if not spill_dir or not os.path.isdir(spill_dir):
        return out
    for name in sorted(os.listdir(spill_dir)):
        if not (name.startswith(SPILL_PREFIX) and name.endswith(SPILL_SUFFIX)):
            continue
        path = os.path.join(spill_dir, name)
        try:
            with open(path) as fh:
                out.append((path, json.load(fh)))
        except (OSError, ValueError) as exc:
            print(f"[sentinel] SPILL CORRUPT path={path} err={exc}",
                  file=sys.stderr)
    return out


def archive_spill(path: str) -> None:
    """Mark a spill replayed (rename, never delete — the morgue keeps
    its records)."""
    try:
        os.replace(path, path + REPLAYED_SUFFIX)
    except OSError as exc:
        print(f"[sentinel] SPILL ARCHIVE FAILED path={path} err={exc}",
              file=sys.stderr)


def replay_spills(log, spill_dir: str | None) -> list[dict]:
    """Replay spill records into the event log at forwarder startup.

    Each spill becomes a forward_confirmed/forward_failed event with
    channel="direct-degraded" and outbox_id=None (no outbox row exists —
    that is the whole point of the spill). Returns the replayed summaries.
    """
    replayed: list[dict] = []
    for path, rec in iter_spills(spill_dir):
        accepted = rec.get("pd_outcome") == "accepted"
        event_type = "forward_confirmed" if accepted else "forward_failed"
        body = {
            "outbox_id": None,
            "channel": "direct-degraded",
            "attempt_no": 1,
            "vendor_status": rec.get("pd_status"),
            "latency_ms": rec.get("latency_ms", 0.0),
        }
        if not accepted:
            body["error_class"] = rec.get("error_class") or "degraded_send_failed"
        try:
            seq = log.append_event(
                event_type, actor="forwarder",
                alert_id=rec.get("alert_id", "unknown"),
                fingerprint=rec.get("fingerprint", "unknown"),
                episode_id=rec.get("episode_id", "unknown"),
                outbox_id=None, body=body)
        except Exception as exc:  # the log itself is sick — keep the spill
            print(f"[sentinel] SPILL REPLAY FAILED path={path} err={exc}",
                  file=sys.stderr)
            continue
        archive_spill(path)
        replayed.append({"seq": seq, "event": event_type,
                         "dedup_key": rec.get("dedup_key")})
    return replayed
