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
    pd_outcome ("accepted"|"retryable"|"terminal"|"send_error"|"simulated"),
    pd_status, error, latency_ms, simulated (bool, only on simulated absorbs)

Honesty invariant (P6 — the named simulated-spill audit lie,
``forwarder-byok.md`` §2.5): a spill record whose page was ABSORBED by
simulated paging (``simulated: true`` / ``pd_outcome == "simulated"``) was
never attempted on the wire, so it is NEVER replayed as ``forward_failed``.
``replay_spills`` maps it to ``forward_confirmed`` with
``simulated: true`` in the body (C6: ``simulated:true`` ⇒ outcome
"accepted", ``error_class`` forbidden) — a confirmation of absorption,
never a failure.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import time

SPILL_PREFIX = "spill-"
SPILL_SUFFIX = ".json"
REPLAYED_SUFFIX = ".replayed"

# Per-process monotonic counter, appended to spill filenames. The filename
# used to be spill-<epoch_ms>-<pid>.json — two spills from the same process
# inside one millisecond produced the SAME name and os.replace() silently
# overwrote the first record: silent audit data loss on the exact path the
# event log exists to protect (PR #92 review). The counter closes it:
# next() on itertools.count is atomic under the GIL, so concurrent threads
# in one process still get distinct names. Zero-padded so iter_spills'
# lexicographic sort stays chronological. (Counter resets on restart, but
# the pid and/or millisecond then differ — the triple is unique in practice.)
_spill_seq = itertools.count()


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
                f"-{os.getpid()}-{next(_spill_seq):04d}{SPILL_SUFFIX}")
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

    P6 honesty rule (the simulated-spill audit lie, forwarder-byok.md §2.5):
    a simulated absorption (simulated: true / pd_outcome == "simulated") is
    NOT a failure — the page never touched the wire. It replays as
    forward_confirmed with simulated: true in the body and no error_class
    (C6 ForwardReceipt §2.2: simulated:true ⇒ outcome "accepted",
    error_class forbidden). Any code path that emits forward_failed for a
    page never attempted on the wire falsifies this contract.
    """
    replayed: list[dict] = []
    for path, rec in iter_spills(spill_dir):
        # Detect by the flag FIRST (the pd_outcome is belt-and-braces for
        # records written before the flag was always present).
        simulated = rec.get("simulated") is True or \
            rec.get("pd_outcome") == "simulated"
        if simulated:
            # Confirmation of absorption — never a failure. No error_class:
            # a page that was never attempted cannot carry a wire error.
            event_type = "forward_confirmed"
            body = {
                "outbox_id": None,
                "channel": "direct-degraded",
                "attempt_no": 1,
                "vendor_status": None,
                "latency_ms": rec.get("latency_ms", 0.0),
                "simulated": True,
                "reason": rec.get("reason"),
            }
        else:
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
                body["error_class"] = rec.get("error_class") or \
                    "degraded_send_failed"
        # Structural guarantee, belt-and-braces: a simulated absorption is
        # never recorded as a failure, however the branches above evolve.
        if simulated and event_type == "forward_failed":
            raise AssertionError(
                "P6 honesty invariant violated: a simulated page "
                f"(spill {path}) must never replay as forward_failed")
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
        summary = {"seq": seq, "event": event_type,
                   "dedup_key": rec.get("dedup_key")}
        if simulated:
            summary["simulated"] = True
        replayed.append(summary)
    return replayed
