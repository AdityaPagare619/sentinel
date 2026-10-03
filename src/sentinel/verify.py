"""One-command chain verifier — the seal the customer can check (design §9.5).

Usage:
    python -m sentinel.verify sentinel.db
    python -m sentinel.verify sentinel.db --from-seq 1042 --hmac-key-file key.bin

Verification replays from genesis, or from the last customer-verified
checkpoint (--from-seq + --hmac-key-file): the checkpoint event's HMAC is
verified first, then the chain replays forward from that anchor. The first
broken seq is reported. Corruption and tamper present identically — the
verifier reports, it does not distinguish (design §5).

Exit code 0: chain intact. Exit code 1: broken (or unusable input).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

from sentinel.checkpoint import checkpoint_hmac
from sentinel.eventlog import GENESIS_PREV_HASH, _row_hash


class VerificationFailure(Exception):
    pass


def _envelope(row: sqlite3.Row) -> dict:
    return {
        "event_id": row["event_id"],
        "schema_v": row["schema_v"],
        "ts": row["ts"],
        "actor": row["actor"],
        "type": row["type"],
        "alert_id": row["alert_id"],
        "fingerprint": row["fingerprint"],
        "episode_id": row["episode_id"],
        "outbox_id": row["outbox_id"],
    }


def verify(db_path: str, from_seq: int | None = None,
           hmac_key: bytes | None = None) -> dict:
    """Verify the hash chain. Returns a summary dict; raises
    VerificationFailure on the first broken link."""
    if not os.path.exists(db_path):
        raise VerificationFailure(f"db not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "events" not in tables:
            raise VerificationFailure("no events table — not an event-log db")
        head = conn.execute(
            "SELECT MAX(seq) AS m FROM events").fetchone()["m"] or 0
        if head == 0:
            return {"ok": True, "events": 0, "head_seq": 0,
                    "note": "empty log"}
        if from_seq is not None:
            anchor = conn.execute(
                "SELECT * FROM events WHERE seq = ?", (from_seq,)).fetchone()
            if anchor is None:
                raise VerificationFailure(f"no event at seq {from_seq}")
            if anchor["type"] != "checkpoint" or \
                    anchor["actor"] != "checkpoint":
                raise VerificationFailure(
                    f"seq {from_seq} is a {anchor['type']} event, not a "
                    f"checkpoint — anchored verification starts at a "
                    f"customer-verified checkpoint")
            if hmac_key is None:
                raise VerificationFailure(
                    "anchored verification requires the customer HMAC key "
                    "(--hmac-key-file); without it the anchor is untrusted")
            body = json.loads(anchor["body"])
            expect = checkpoint_hmac(
                hmac_key, body["head_seq"], body["head_hash"],
                body["event_count"], body["window_start_ts"],
                body["window_end_ts"])
            if not hmac_compare(expect, body["hmac_hex"]):
                raise VerificationFailure(
                    f"checkpoint at seq {from_seq} FAILED HMAC verification — "
                    f"the anchor is forged or the key is wrong")
            # The checkpoint's own chain link must also hold.
            _check_link(anchor)
            prev_hash, start = anchor["row_hash"], from_seq + 1
            verified_from = f"checkpoint@{from_seq}"
        else:
            prev_hash, start = GENESIS_PREV_HASH, 1
            verified_from = "genesis"
        cur = conn.execute(
            "SELECT * FROM events WHERE seq >= ? ORDER BY seq", (start,))
        n = 0
        for row in cur:
            n += 1
            if row["prev_hash"] != prev_hash:
                raise VerificationFailure(
                    f"chain broken at seq {row['seq']}: prev_hash "
                    f"{row['prev_hash'][:16]}... != expected "
                    f"{prev_hash[:16]}... (previous row rewritten or "
                    f"a row deleted)")
            recomputed = _row_hash(_envelope(row), json.loads(row["body"]),
                                   row["prev_hash"])
            if recomputed != row["row_hash"]:
                raise VerificationFailure(
                    f"chain broken at seq {row['seq']}: row_hash mismatch — "
                    f"the envelope or body was tampered with after writing")
            prev_hash = row["row_hash"]
        return {"ok": True, "events": n, "head_seq": head,
                "head_hash": prev_hash, "verified_from": verified_from}
    finally:
        conn.close()


def _check_link(row: sqlite3.Row) -> None:
    """Verify one row's own link (prev continuity is checked by the loop)."""
    recomputed = _row_hash(_envelope(row),
                           json.loads(row["body"]), row["prev_hash"])
    if recomputed != row["row_hash"]:
        raise VerificationFailure(
            f"anchor row at seq {row['seq']} fails its own hash check")


def hmac_compare(a: str, b: str) -> bool:
    import hmac as _hmac
    return _hmac.compare_digest(a, b)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify a Sentinel event-log hash chain.")
    ap.add_argument("db", help="path to the engine SQLite file")
    ap.add_argument("--from-seq", type=int, default=None,
                    help="start from a customer-verified checkpoint at this "
                         "seq instead of genesis")
    ap.add_argument("--hmac-key-file", default=None,
                    help="file holding the customer HMAC key (required with "
                         "--from-seq)")
    args = ap.parse_args(argv)
    key = None
    if args.hmac_key_file:
        with open(args.hmac_key_file, "rb") as fh:
            key = fh.read()
    try:
        result = verify(args.db, from_seq=args.from_seq, hmac_key=key)
    except VerificationFailure as exc:
        print(f"BROKEN: {exc}")
        return 1
    print(f"OK: {result['events']} events verified from "
          f"{result['verified_from']}, head seq={result['head_seq']} "
          f"head_hash={result.get('head_hash', '-')[:16]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
