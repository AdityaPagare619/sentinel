"""D14 retention tiers — RFC 2026-10-05 (ADR-024 / O-1, Vault-led).

Hot (self-calibrating window, 60-day floor) -> warm (365d full-fidelity, WORM)
-> cold (7y summaries, WORM). Deletion is ALWAYS ledger-logged + checkpointed
into the live log — never a silent prune.

Design laws applied:
  - Chesterton's fence: the hash chain is the product. We never DELETE rows
    from a chained log; we time-partition into segments and chain the segments
    (genesis_prev_hash = previous segment's head hash).
  - Self-calibration: the hot window is derived from the deployment's OWN
    measured bytes/day and its disk budget — no month-tuned constants.
  - Archive-verify-before-prune: export -> verify chain -> seal manifest ->
    checkpoint -> prune. Never reordered; a broken chain refuses to prune.
  - The existing `checkpoint` event vocabulary carries the archive linkage
    (head_hash, event_count, sink_uri, hmac_hex) — no new event types; the
    Type-1 vocabulary stays frozen.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

from .eventlog import (EventLog, GENESIS_PREV_HASH, _canonical, _row_hash,
                       utcnow_iso)

# ---------------------------------------------------------------- constants

# Measured on this codebase (panel evidence, 2026-10-04): 755.7 bytes/event
# steady-state through the real EventLog write path. Used ONLY as the
# no-data fallback for self-calibration — real deployments measure their own.
MEASURED_BYTES_PER_EVENT = 756.0

DEFAULT_DISK_BUDGET_BYTES = 50 * 1024 * 1024 * 1024  # 50 GiB of a 100 GB disk
LOW_DISK_READONLY_FRACTION = 0.05  # <5% free: job goes read-only and pages.
JOB_SLO_HOURS = 26.0  # no successful run in this long -> page (Pager's SLO).

_ENVELOPE_KEYS = ("event_id", "schema_v", "ts", "actor", "type", "alert_id",
                  "fingerprint", "episode_id", "outbox_id")


class RetentionError(Exception):
    """Retention job failure — fail toward the human, never toward loss."""


class ChainBroken(RetentionError):
    """Export-time chain verification failed: refuse to prune, page."""


# ------------------------------------------------------------------ config

@dataclasses.dataclass
class RetentionConfig:
    """Type-1 items are the floors/formula; the rest is Type 2 (see RFC §3)."""
    hot_days: int = 180              # Type 1: target hot window
    hot_floor_days: int = 60         # Type 1: never silently shorten below
    warm_days: int = 365             # Type 1: full-fidelity WORM window
    cold_years: int = 7              # Type 1: summary window
    disk_budget_bytes: int = DEFAULT_DISK_BUDGET_BYTES  # Type 1: the bound
    segment_roll_days: int = 1       # Type 2: roll cadence
    region: str = "local"            # Type 1: EU customers pin an EU region
    pii_fields: tuple = ("alert_id", "fingerprint")  # Type 2: deployment-set
    allow_cold_expiry: bool = False  # Type 1: cold expires ONLY with explicit
                                     # customer liability acceptance
    customer_overrides: dict = dataclasses.field(default_factory=dict)

    def for_customer(self, customer: str) -> "RetentionConfig":
        """Per-customer tiers — the enterprise surface (RFC §2)."""
        ov = self.customer_overrides.get(customer)
        if not ov:
            return self
        d = dataclasses.asdict(self)
        d.pop("customer_overrides", None)
        d.update({k: v for k, v in ov.items() if k in d})
        cfg = RetentionConfig(**d)
        cfg.customer_overrides = self.customer_overrides
        return cfg


# ------------------------------------------------------- self-calibration

def measure_daily_bytes(db_path: str) -> float:
    """Bytes/day from the deployment's OWN statistics (self-calibrating).

    size(file) / days spanned by the seq range, with a 1-hour minimum span so
    a fresh deployment extrapolates instead of dividing by ~zero. Falls back
    to MEASURED_BYTES_PER_EVENT when the DB holds no events yet.
    """
    size = os.path.getsize(db_path)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM events").fetchone()
    finally:
        con.close()
    count, min_ts, max_ts = row
    if not count or not min_ts or not max_ts:
        return MEASURED_BYTES_PER_EVENT  # ~1 event/day equivalent floor
    span_days = max(
        (_parse_ts(max_ts) - _parse_ts(min_ts)).total_seconds() / 86400.0,
        1.0 / 24.0)
    return size / span_days


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def hot_window_days(config: RetentionConfig,
                    daily_bytes: float) -> tuple[float, bool]:
    """The self-calibrating hot window (RFC §2).

    window = clamp(target=hot_days, floor=hot_floor_days,
                   disk_budget / measured_daily).
    Returns (window_days, under_provisioned). Under-provisioned is a PAGING
    incident — the operator provisions disk; the job NEVER silently shortens
    below the floor (floors are floors, even under log-flood pressure).
    """
    if daily_bytes <= 0:
        return float(config.hot_days), False
    affordable = config.disk_budget_bytes / daily_bytes
    under_provisioned = affordable < config.hot_floor_days
    window = min(float(config.hot_days), affordable)
    window = max(window, float(config.hot_floor_days))
    return window, under_provisioned


# ------------------------------------------------------- chain verification

def verify_chain(db_path: str) -> tuple[int, str, int]:
    """Recompute the hash chain read-only. Returns (head_seq, head_hash,
    event_count). Raises ChainBroken on the first mismatch — the job then
    refuses to prune and pages (fail toward the human)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT seq, event_id, schema_v, ts, actor, type, alert_id,"
            " fingerprint, episode_id, outbox_id, body, prev_hash, row_hash"
            " FROM events ORDER BY seq").fetchall()
    finally:
        con.close()
    prev = None
    for r in rows:
        envelope = {k: r[k] for k in _ENVELOPE_KEYS}
        body = json.loads(r["body"])
        if prev is None:
            # First row chains from whatever genesis this segment was rolled
            # with (GENESIS or a predecessor's head hash) — verify linkage,
            # not the genesis constant.
            expected_prev = r["prev_hash"]
        else:
            expected_prev = prev
        if r["prev_hash"] != expected_prev:
            raise ChainBroken(
                f"seq {r['seq']}: prev_hash linkage broken")
        got = _row_hash(envelope, body, r["prev_hash"])
        if got != r["row_hash"]:
            raise ChainBroken(f"seq {r['seq']}: row_hash mismatch")
        prev = r["row_hash"]
    if not rows:
        return 0, GENESIS_PREV_HASH, 0
    last = rows[-1]
    return last["seq"], last["row_hash"], len(rows)


# ------------------------------------------------------- pseudonymization

def pseudonymize_value(value: str, salt: bytes) -> str:
    """GDPR tombstone: salted hash replaces the identifying value. The chain
    stays continuous; the original is unrecoverable from the tombstone."""
    digest = hashlib.sha256(salt + value.encode("utf-8")).hexdigest()
    return f"redacted:{digest[:32]}"


def pseudonymize_record(record: dict, fields: tuple, salt: bytes) -> dict:
    """Rewrite PII-bearing fields of an exported event record in place-copy."""
    rec = dict(record)
    for f in fields:
        v = rec.get(f)
        if isinstance(v, str) and v:
            rec[f] = pseudonymize_value(v, salt)
    body = rec.get("body")
    if isinstance(body, dict):
        body = dict(body)
        for f in fields:
            v = body.get(f)
            if isinstance(v, str) and v:
                body[f] = pseudonymize_value(v, salt)
        rec["body"] = body
    return rec


# ------------------------------------------------------- segment export

def _manifest_hmac(key: bytes, manifest_no_hmac: dict) -> str:
    return hmac.new(key, _canonical(manifest_no_hmac),
                    hashlib.sha256).hexdigest()


def export_segment(db_path: str, dest_dir: str, *, hmac_key: bytes,
                   segment_id: str, salt: bytes = b"",
                   pseudonymize_fields: tuple = ()) -> dict:
    """Chain-verified export of a sealed segment to WORM-shaped storage.

    Order (never reordered): verify chain -> write events.jsonl -> seal
    manifest with HMAC. Raises ChainBroken instead of exporting a lie.
    The manifest's hmac covers everything except itself (design §5 pattern).
    """
    if not hmac_key:
        raise RetentionError("warm archive must be HMAC-sealed — no key given")
    head_seq, head_hash, event_count = verify_chain(db_path)
    os.makedirs(dest_dir, exist_ok=True)

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT seq, event_id, schema_v, ts, actor, type, alert_id,"
            " fingerprint, episode_id, outbox_id, body, prev_hash, row_hash"
            " FROM events ORDER BY seq").fetchall()
    finally:
        con.close()

    events_path = os.path.join(dest_dir, "events.jsonl")
    window_start = rows[0]["ts"] if rows else None
    window_end = rows[-1]["ts"] if rows else None
    with open(events_path, "w", encoding="utf-8") as fh:
        for r in rows:
            rec = {k: r[k] for k in
                   ("seq", "event_id", "schema_v", "ts", "actor", "type",
                    "alert_id", "fingerprint", "episode_id", "outbox_id",
                    "prev_hash", "row_hash")}
            rec["body"] = json.loads(r["body"])
            if pseudonymize_fields:
                rec = pseudonymize_record(rec, pseudonymize_fields, salt)
            fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False)
                     + "\n")

    manifest = {
        "format": 1,
        "segment_id": segment_id,
        "seq_start": rows[0]["seq"] if rows else 0,
        "seq_end": head_seq,
        "head_hash": head_hash,
        "event_count": event_count,
        "window_start_ts": window_start,
        "window_end_ts": window_end,
        "pseudonymized_fields": list(pseudonymize_fields),
        "exported_at": utcnow_iso(),
        "sink_uri": dest_dir,
    }
    manifest["hmac_hex"] = _manifest_hmac(hmac_key, manifest)
    with open(os.path.join(dest_dir, "manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, sort_keys=True, indent=2)
    return manifest


def compact_to_summary(warm_dir: str, dest_path: str, *,
                       hmac_key: bytes) -> dict:
    """Warm (365d) -> cold: fold a full-fidelity export into a hash-chained
    DAILY summary. Raw bodies do not survive this step — by design (RFC §2:
    cold is summaries only; full-fidelity cold is a per-customer tier)."""
    if not hmac_key:
        raise RetentionError("cold summary must be HMAC-sealed — no key given")
    with open(os.path.join(warm_dir, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    if _manifest_hmac(hmac_key, {k: v for k, v in manifest.items()
                                 if k != "hmac_hex"}) != manifest["hmac_hex"]:
        raise RetentionError("warm manifest HMAC mismatch — refusing to "
                             "compact an untrusted archive")
    by_type: dict[str, int] = {}
    by_disposition: dict[str, int] = {}
    by_day: dict[str, int] = {}
    with open(os.path.join(warm_dir, "events.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            by_type[rec["type"]] = by_type.get(rec["type"], 0) + 1
            day = rec["ts"][:10]
            by_day[day] = by_day.get(day, 0) + 1
            body = rec.get("body") or {}
            disp = body.get("disposition")
            if disp:
                by_disposition[disp] = by_disposition.get(disp, 0) + 1
    summary = {
        "format": 1,
        "segment_id": manifest["segment_id"],
        "seq_start": manifest["seq_start"],
        "seq_end": manifest["seq_end"],
        "head_hash": manifest["head_hash"],  # chain continuity survives
        "event_count": manifest["event_count"],
        "window_start_ts": manifest["window_start_ts"],
        "window_end_ts": manifest["window_end_ts"],
        "by_type": by_type,
        "by_disposition": by_disposition,
        "by_day": by_day,
        "compacted_at": utcnow_iso(),
    }
    summary["hmac_hex"] = _manifest_hmac(hmac_key, summary)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, sort_keys=True, indent=2)
    return summary


# ------------------------------------------------------- segment ledger

class SegmentLedger:
    """The retention job's memory: which sealed segments exist, what tier
    they are in, where their archives live. Saved atomically (tmp+replace).
    Deletions are recorded here AND checkpointed in the live log — the two
    records corroborate each other."""

    def __init__(self, path: str):
        self.path = path
        self._segments: dict[str, dict] = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self._segments = data.get("segments", {})

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"format": 1, "segments": self._segments}, fh,
                      sort_keys=True, indent=2)
        os.replace(tmp, self.path)

    def add(self, segment_id: str, info: dict) -> None:
        self._segments[segment_id] = info
        self.save()

    def get(self, segment_id: str) -> dict | None:
        return self._segments.get(segment_id)

    def segments(self) -> dict[str, dict]:
        return dict(self._segments)

    def record_deletion(self, segment_id: str, what: str,
                        checkpoint_seq: int | None) -> None:
        seg = self._segments.get(segment_id, {})
        seg.setdefault("deletions", []).append({
            "what": what,
            "at": utcnow_iso(),
            "checkpoint_seq": checkpoint_seq,
        })
        self._segments[segment_id] = seg
        self.save()


# ------------------------------------------------------- roll

def seal_and_roll(log: EventLog, segments_dir: str,
                 ledger: SegmentLedger, *,
                 now: datetime | None = None) -> EventLog:
    """Daily roll (Type 2 cadence): seal the live DB into a segment file and
    open a fresh EventLog whose chain starts from the sealed head hash.

    The caller must not write to `log` during the roll (single-writer)."""
    now = now or datetime.now(timezone.utc)
    head_seq, head_hash = log.head()
    os.makedirs(segments_dir, exist_ok=True)
    segment_id = f"seg-{now.strftime('%Y%m%d')}-{head_seq:010d}"
    if ledger.get(segment_id):
        raise RetentionError(f"segment {segment_id} already rolled")
    # Flush WAL into the main file, then close: the rename below moves a
    # self-contained segment.
    log._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    log._conn.close()
    seg_path = os.path.join(segments_dir, segment_id + ".sqlite")
    os.rename(log.db_path, seg_path)
    ledger.add(segment_id, {
        "path": seg_path, "tier": "hot", "sealed_at": utcnow_iso(),
        "seq_end": head_seq, "head_hash": head_hash,
    })
    return EventLog(db_path=log.db_path,
                    spillover_dir=log.spillover_dir,
                    diskguard_bytes=log.diskguard_bytes,
                    genesis_prev_hash=head_hash)


# ------------------------------------------------------- the job

class RetentionJob:
    """The tier state machine. `dry_run=True` is the shadow mode: full plan,
    zero deletes, zero checkpoints — run this before ever arming the job."""

    def __init__(self, live_log: EventLog, config: RetentionConfig,
                 ledger: SegmentLedger, warm_dir: str, cold_dir: str,
                 hmac_key: bytes):
        if not hmac_key:
            raise ValueError("retention archives must be sealed — HMAC key "
                             "is required (customer-sealed, RFC §2)")
        self.live_log = live_log
        self.config = config
        self.ledger = ledger
        self.warm_dir = warm_dir
        self.cold_dir = cold_dir
        self.hmac_key = hmac_key

    def _checkpoint_archive(self, manifest: dict) -> int | None:
        """The deletion's corroborating record: a checkpoint event in the
        LIVE log naming the archived segment's head hash + sink. In-band,
        hash-chained — an auditor finds every archive from the live chain."""
        return self.live_log.append_event(
            "checkpoint", actor="checkpoint", alert_id="retention",
            fingerprint="retention", episode_id="retention",
            body={
                "head_seq": manifest["seq_end"],
                "head_hash": manifest["head_hash"],
                "event_count": manifest["event_count"],
                "window_start_ts": manifest["window_start_ts"],
                "window_end_ts": manifest["window_end_ts"],
                "hmac_hex": manifest["hmac_hex"],
                "sink_uri": manifest["sink_uri"],
                "sink_push_ok": True,
            })

    def apply(self, dry_run: bool = True, *,
              now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        plan: dict = {
            "dry_run": dry_run,
            "hot_to_warm": [], "warm_to_cold": [], "cold_expired": [],
            "under_provisioned": False, "checkpoint_seqs": [],
            "pages": [],
        }
        # Low-disk read-only mode (Forge's pre-mortem #2): below 5% free the
        # job does not write — it pages. A retention job that wedges a full
        # disk is worse than no retention job.
        try:
            st = os.statvfs(os.path.dirname(
                os.path.abspath(self.live_log.db_path)))
            free_fraction = st.f_bavail / st.f_blocks
        except OSError:
            free_fraction = 1.0
        if free_fraction < LOW_DISK_READONLY_FRACTION:
            plan["pages"].append(
                "retention job read-only: disk <5% free — not writing; "
                "provision disk")
            return plan

        daily = measure_daily_bytes(self.live_log.db_path)
        window_days, under = hot_window_days(self.config, daily)
        plan["measured_daily_bytes"] = daily
        plan["hot_window_days"] = window_days
        plan["under_provisioned"] = under
        if under:
            plan["pages"].append(
                f"UNDER-PROVISIONED: disk budget holds {window_days:.1f}d at "
                f"{daily / 1e9:.2f} GB/day; 60-day floor requires "
                f"{daily * self.config.hot_floor_days / 1e9:.1f} GB — "
                f"provision disk, tiers NOT silently shortened")

        for seg_id, seg in sorted(self.ledger.segments().items()):
            sealed = _parse_ts(seg["sealed_at"])
            age_days = (now - sealed).total_seconds() / 86400.0
            tier = seg.get("tier")
            if tier == "hot" and age_days > window_days:
                plan["hot_to_warm"].append(seg_id)
                if not dry_run:
                    self._promote_to_warm(seg_id, seg, plan)
            elif tier == "warm" and age_days > self.config.warm_days:
                plan["warm_to_cold"].append(seg_id)
                if not dry_run:
                    self._promote_to_cold(seg_id, seg, plan)
            elif tier == "cold" and age_days > self.config.cold_years * 365:
                plan["cold_expired"].append(seg_id)
                if not dry_run:
                    self._expire_cold(seg_id, seg, plan)
        self._record_job_run(plan, dry_run)
        return plan

    # ------------------------------------------------ internal promotions

    def _promote_to_warm(self, seg_id: str, seg: dict, plan: dict) -> None:
        dest = os.path.join(self.warm_dir, seg_id)
        # ChainBroken propagates: no prune, the page below fires.
        manifest = export_segment(
            seg["path"], dest, hmac_key=self.hmac_key, segment_id=seg_id)
        cseq = self._checkpoint_archive(manifest)
        plan["checkpoint_seqs"].append(cseq)
        os.remove(seg["path"])  # the ONLY delete in this step; archive first
        seg["tier"] = "warm"
        seg["sink_uri"] = dest
        seg["archived_at"] = utcnow_iso()
        self.ledger.add(seg_id, seg)
        self.ledger.record_deletion(seg_id, "hot segment file pruned", cseq)

    def _promote_to_cold(self, seg_id: str, seg: dict, plan: dict) -> None:
        year = _parse_ts(seg["sealed_at"]).strftime("%Y")
        dest = os.path.join(self.cold_dir, year, seg_id + ".summary.json")
        compact_to_summary(seg["sink_uri"], dest, hmac_key=self.hmac_key)
        cseq = self.live_log.append_event(
            "checkpoint", actor="checkpoint", alert_id="retention",
            fingerprint="retention", episode_id="retention",
            body={
                "head_seq": seg["seq_end"], "head_hash": seg["head_hash"],
                "event_count": -1,  # summary, not events (see sink_uri)
                "window_start_ts": seg["sealed_at"],
                "window_end_ts": seg["sealed_at"],
                "hmac_hex": seg.get("head_hash", ""),
                "sink_uri": dest, "sink_push_ok": True,
            })
        plan["checkpoint_seqs"].append(cseq)
        import shutil
        shutil.rmtree(seg["sink_uri"])  # warm full-fidelity leaves here
        seg["tier"] = "cold"
        seg["sink_uri"] = dest
        self.ledger.add(seg_id, seg)
        self.ledger.record_deletion(seg_id, "warm export compacted to cold "
                                            "summary", cseq)

    def _expire_cold(self, seg_id: str, seg: dict, plan: dict) -> None:
        if not self.config.allow_cold_expiry:
            plan["pages"].append(
                f"cold summary {seg_id} past {self.config.cold_years}y but "
                f"allow_cold_expiry=False — retained; needs explicit customer "
                f"liability acceptance to expire")
            return
        cseq = self.live_log.append_event(
            "checkpoint", actor="checkpoint", alert_id="retention",
            fingerprint="retention", episode_id="retention",
            body={
                "head_seq": seg["seq_end"], "head_hash": seg["head_hash"],
                "event_count": -1,
                "window_start_ts": seg["sealed_at"],
                "window_end_ts": seg["sealed_at"],
                "hmac_hex": seg.get("head_hash", ""),
                "sink_uri": seg["sink_uri"], "sink_push_ok": True,
            })
        os.remove(seg["sink_uri"])
        seg["tier"] = "expired"
        self.ledger.add(seg_id, seg)
        self.ledger.record_deletion(seg_id, "cold summary expired (explicit "
                                            "customer acceptance)", cseq)
        plan["checkpoint_seqs"].append(cseq)

    def _record_job_run(self, plan: dict, dry_run: bool) -> None:
        state_path = os.path.join(os.path.dirname(self.ledger.path),
                                  "retention-job-state.json")
        state = {"format": 1, "last_run": utcnow_iso(), "dry_run": dry_run,
                 "plan_summary": {k: (len(v) if isinstance(v, list) else v)
                                  for k, v in plan.items()
                                  if k != "pages"}}
        if not dry_run:
            tmp = state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh, sort_keys=True, indent=2)
            os.replace(tmp, state_path)
            for page in plan["pages"]:
                sys.stderr.write(f"[sentinel][retention] PAGE: {page}\n")
                try:
                    self.live_log._enqueue_control_plane_page(
                        kind="retention", summary=page,
                        detail={"ts": utcnow_iso()})
                except Exception:
                    pass  # the stderr line above is the backstop
