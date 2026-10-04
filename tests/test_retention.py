"""D14 retention tiers — RFC 2026-10-05. Tests the decided mechanics.

stdlib-only: pure unittest, no pytest (frozen repo decision).
"""

import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sentinel.eventlog import EventLog
from sentinel.retention import (
    ChainBroken, RetentionConfig, RetentionJob, SegmentLedger,
    compact_to_summary, export_segment, hot_window_days, measure_daily_bytes,
    pseudonymize_record, pseudonymize_value, seal_and_roll, verify_chain,
)


class _TmpDirTestCase(unittest.TestCase):
    """unittest equivalent of pytest's tmp_path fixture."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = pathlib.Path(self._tmpdir.name)


def _mklog(path, **kw):
    return EventLog(db_path=path, spillover_dir=None, **kw)


def _write_decision(log, i, disposition="suppress"):
    fp = f"fp{i:06d}" + "x" * 24
    log.record_request(alert_id=f"A{i}", fingerprint=fp, episode_id=f"ep{i}",
                       input_sha256="a" * 64, source_integration="pagerduty",
                       severity_in="critical", raw_payload_bytes=2048)
    log.record_decision_and_enqueue(
        alert_id=f"A{i}", fingerprint=fp, episode_id=f"ep{i}",
        body={"disposition": disposition, "budget_outcome": "answered_in_time",
              "lock_evaluation": {"locks": []}, "freshness": {},
              "threshold_counterfactual": {"threshold": 0.9},
              "links": {}}, outbox=None)


# ------------------------------------------------------- self-calibration

class TestSelfCalibration(_TmpDirTestCase):
    def test_measure_daily_bytes_from_own_stats(self):
        db = str(self.tmp_path / "ev.sqlite")
        log = _mklog(db)
        for i in range(10):
            _write_decision(log, i)
        daily = measure_daily_bytes(db)
        # 20 events in ~0 span -> extrapolated from >=1h span; sanity: >0
        assert daily > 0
        # and roughly consistent with file size / span floor
        assert daily >= os.path.getsize(db) / (1.0 / 24.0) * 0.5

    def test_hot_window_clamps_to_target(self):
        cfg = RetentionConfig(disk_budget_bytes=10**12)
        window, under = hot_window_days(cfg, daily_bytes=2.16 * 1e9)
        assert window == 180.0 and not under

    def test_hot_window_self_calibrates_down(self):
        cfg = RetentionConfig(disk_budget_bytes=50 * 1e9)
        # 21.6 GB/day (10k/min measured): 50 GB holds ~2.3d -> floor applies
        window, under = hot_window_days(cfg, daily_bytes=21.6 * 1e9)
        assert window == 60.0 and under  # floor is a floor; page fires

    def test_hot_window_never_below_floor_silently(self):
        cfg = RetentionConfig(disk_budget_bytes=10**9)
        window, under = hot_window_days(cfg, daily_bytes=21.6 * 1e9)
        assert window == cfg.hot_floor_days
        assert under  # caller must page: under-provisioned, not shortened

    def test_hot_window_mid_range(self):
        cfg = RetentionConfig(disk_budget_bytes=200 * 1e9)
        window, under = hot_window_days(cfg, daily_bytes=2.16 * 1e9)
        # was: pytest.approx(200 / 2.16, rel=0.01)
        expected = 200 / 2.16
        assert abs(window - expected) <= 0.01 * abs(expected)
        assert not under


# ------------------------------------------------------- chain verification

class TestVerifyChain(_TmpDirTestCase):
    def test_verify_ok(self):
        db = str(self.tmp_path / "ev.sqlite")
        log = _mklog(db)
        for i in range(5):
            _write_decision(log, i)
        head_seq, head_hash, count = verify_chain(db)
        assert count == 10 and head_seq == 10
        assert head_hash == log.head()[1]

    def test_verify_refuses_tampered_row(self):
        db = str(self.tmp_path / "ev.sqlite")
        log = _mklog(db)
        for i in range(3):
            _write_decision(log, i)
        con = sqlite3.connect(db)
        con.execute("UPDATE events SET body = '{}' WHERE seq = 2")
        con.commit()
        con.close()
        with self.assertRaises(ChainBroken):
            verify_chain(db)

    def test_verify_empty_db(self):
        db = str(self.tmp_path / "ev.sqlite")
        _mklog(db)
        assert verify_chain(db) == (0, "GENESIS", 0)


# ------------------------------------------------------- export + seal

class TestExport(_TmpDirTestCase):
    def test_export_roundtrip_sealed(self):
        db = str(self.tmp_path / "ev.sqlite")
        log = _mklog(db)
        for i in range(4):
            _write_decision(log, i, "page_now" if i % 2 else "suppress")
        dest = str(self.tmp_path / "warm" / "seg1")
        manifest = export_segment(db, dest, hmac_key=b"k" * 32,
                                  segment_id="seg1")
        assert manifest["event_count"] == 8
        assert manifest["head_hash"] == log.head()[1]
        assert len(manifest["hmac_hex"]) == 64
        lines = open(os.path.join(dest, "events.jsonl")).read().strip().split(
            "\n")
        assert len(lines) == 8
        assert json.loads(lines[0])["seq"] == 1

    def test_export_refuses_broken_chain(self):
        db = str(self.tmp_path / "ev.sqlite")
        log = _mklog(db)
        _write_decision(log, 0)
        con = sqlite3.connect(db)
        con.execute("UPDATE events SET row_hash='tampered' WHERE seq=1")
        con.commit()
        con.close()
        with self.assertRaises(ChainBroken):
            export_segment(db, str(self.tmp_path / "w"), hmac_key=b"k" * 32,
                           segment_id="segx")

    def test_export_requires_hmac_key(self):
        db = str(self.tmp_path / "ev.sqlite")
        _write_decision(_mklog(db), 0)
        with self.assertRaises(Exception):
            export_segment(db, str(self.tmp_path / "w"), hmac_key=b"",
                           segment_id="segx")


# ------------------------------------------------------- pseudonymization

class TestPseudonymization(unittest.TestCase):
    def test_tombstone_is_stable_and_irreversible(self):
        a = pseudonymize_value("alert-123", b"salt")
        b = pseudonymize_value("alert-123", b"salt")
        c = pseudonymize_value("alert-123", b"other")
        assert a == b and a != c and "alert-123" not in a
        assert a.startswith("redacted:")

    def test_record_tombstones_configured_fields(self):
        rec = {"alert_id": "A1", "fingerprint": "fp9", "type": "decision_made",
               "body": {"disposition": "suppress", "alert_id": "A1"}}
        out = pseudonymize_record(rec, ("alert_id", "fingerprint"), b"s")
        assert out["alert_id"].startswith("redacted:")
        assert out["fingerprint"].startswith("redacted:")
        assert out["body"]["alert_id"].startswith("redacted:")
        assert out["body"]["disposition"] == "suppress"  # untouched
        assert out["type"] == "decision_made"


# ------------------------------------------------------- roll + job

def _old_segment(tmp_path, days_old, n=4):
    segdir = tmp_path / "segments"
    segdir.mkdir(exist_ok=True)
    db = str(tmp_path / "oldlive.sqlite")
    log = _mklog(db)
    for i in range(n):
        _write_decision(log, i)
    ledger = SegmentLedger(str(segdir / "ledger.json"))
    rolled = seal_and_roll(log, str(segdir), ledger)
    seg_id = next(iter(ledger.segments()))
    seg = ledger.get(seg_id)
    # backdate the seal to simulate age
    old = (datetime.now(timezone.utc) - timedelta(days=days_old)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    seg["sealed_at"] = old
    ledger.add(seg_id, seg)
    return rolled, ledger, seg_id, seg


class TestRoll(_TmpDirTestCase):
    def test_roll_chains_segments(self):
        db = str(self.tmp_path / "live.sqlite")
        log = _mklog(db)
        for i in range(3):
            _write_decision(log, i)
        old_head = log.head()[1]
        ledger = SegmentLedger(str(self.tmp_path / "ledger.json"))
        new_log = seal_and_roll(log, str(self.tmp_path / "segments"), ledger)
        _write_decision(new_log, 99)
        # the new segment's first row chains from the old head hash
        con = sqlite3.connect(db)
        prev = con.execute(
            "SELECT prev_hash FROM events ORDER BY seq LIMIT 1").fetchone()[0]
        con.close()
        assert prev == old_head
        assert ledger.get(next(iter(ledger.segments())))["head_hash"] == \
            old_head


class TestRetentionJob(_TmpDirTestCase):
    def _job(self, live_log, ledger, **cfg_kw):
        cfg = RetentionConfig(disk_budget_bytes=10**15, **cfg_kw)
        return RetentionJob(live_log, cfg, ledger,
                            warm_dir=str(self.tmp_path / "warm"),
                            cold_dir=str(self.tmp_path / "cold"),
                            hmac_key=b"k" * 32)

    def test_dry_run_plans_but_changes_nothing(self):
        live, ledger, seg_id, seg = _old_segment(self.tmp_path, days_old=200)
        job = self._job(live, ledger)
        plan = job.apply(dry_run=True)
        assert plan["hot_to_warm"] == [seg_id]
        assert plan["dry_run"] is True
        assert os.path.exists(seg["path"])  # nothing pruned
        assert plan["checkpoint_seqs"] == []  # nothing checkpointed

    def test_hot_to_warm_promotes_and_checkpoints(self):
        live, ledger, seg_id, seg = _old_segment(self.tmp_path, days_old=200)
        job = self._job(live, ledger)
        plan = job.apply(dry_run=False)
        assert plan["hot_to_warm"] == [seg_id]
        assert len(plan["checkpoint_seqs"]) == 1
        assert not os.path.exists(seg["path"])  # pruned AFTER archive
        assert os.path.exists(os.path.join(
            str(self.tmp_path / "warm"), seg_id, "manifest.json"))
        assert ledger.get(seg_id)["tier"] == "warm"
        # deletion is a logged event: ledger + live chain corroborate
        assert ledger.get(seg_id)["deletions"]
        cseq = plan["checkpoint_seqs"][0]
        row = live._conn.execute(
            "SELECT type FROM events WHERE seq=?", (cseq,)).fetchone()
        assert row["type"] == "checkpoint"

    def test_young_segment_stays_hot(self):
        live, ledger, seg_id, seg = _old_segment(self.tmp_path, days_old=5)
        job = self._job(live, ledger)
        plan = job.apply(dry_run=False)
        assert plan["hot_to_warm"] == []
        assert os.path.exists(seg["path"])

    def test_warm_to_cold_compacts(self):
        live, ledger, seg_id, seg = _old_segment(self.tmp_path, days_old=400)
        job = self._job(live, ledger)
        job.apply(dry_run=False)  # hot -> warm
        plan2 = job.apply(dry_run=False)  # warm -> cold
        assert plan2["warm_to_cold"] == [seg_id]
        seg_year = datetime.fromisoformat(
            ledger.get(seg_id)["sealed_at"].replace("Z", "+00:00")).strftime(
                "%Y")
        summary_path = os.path.join(str(self.tmp_path / "cold"), seg_year,
                                    seg_id + ".summary.json")
        assert os.path.exists(summary_path)
        summary = json.load(open(summary_path))
        assert summary["by_disposition"]["suppress"] == 4
        assert summary["event_count"] == 8
        assert ledger.get(seg_id)["tier"] == "cold"

    def test_cold_does_not_expire_without_acceptance(self):
        live, ledger, seg_id, seg = _old_segment(self.tmp_path, days_old=400)
        job = self._job(live, ledger)
        job.apply(dry_run=False)
        seg["tier"] = "cold"
        seg["sink_uri"] = str(self.tmp_path / "cold" / "x.summary.json")
        os.makedirs(os.path.dirname(seg["sink_uri"]), exist_ok=True)
        open(seg["sink_uri"], "w").write("{}")
        seg["sealed_at"] = (datetime.now(timezone.utc) -
                            timedelta(days=8 * 365)).strftime(
                                "%Y-%m-%dT%H:%M:%S.000Z")
        ledger.add(seg_id, seg)
        plan = job.apply(dry_run=False)
        assert plan["cold_expired"] == [seg_id]
        assert os.path.exists(seg["sink_uri"])  # retained
        assert any("liability acceptance" in p for p in plan["pages"])

    def test_under_provisioned_pages_never_silently_shortens(self):
        live, ledger, seg_id, seg = _old_segment(self.tmp_path, days_old=200)
        import sentinel.retention as ret
        # pin the self-calibration to C2-burst volume: 21.6 GB/day
        # (was: monkeypatch.setattr)
        _orig = ret.measure_daily_bytes
        ret.measure_daily_bytes = lambda p: 21.6e9
        try:
            cfg = RetentionConfig(disk_budget_bytes=10**6)  # 1 MB budget
            job = RetentionJob(live, cfg, ledger,
                               warm_dir=str(self.tmp_path / "warm"),
                               cold_dir=str(self.tmp_path / "cold"),
                               hmac_key=b"k" * 32)
            plan = job.apply(dry_run=True)
        finally:
            ret.measure_daily_bytes = _orig
        assert plan["under_provisioned"] is True
        assert plan["hot_window_days"] == 60.0  # floor, not shorter
        assert any("UNDER-PROVISIONED" in p for p in plan["pages"])

    def test_compact_refuses_tampered_manifest(self):
        warm = self.tmp_path / "warm" / "seg9"
        warm.mkdir(parents=True)
        (warm / "manifest.json").write_text(json.dumps(
            {"segment_id": "seg9", "hmac_hex": "bad"}))
        (warm / "events.jsonl").write_text("")
        with self.assertRaises(Exception):
            compact_to_summary(str(warm), str(self.tmp_path / "c.json"),
                               hmac_key=b"k" * 32)

    def test_per_customer_override(self):
        cfg = RetentionConfig(
            customer_overrides={"acme": {"hot_days": 365, "warm_days": 730}})
        acme = cfg.for_customer("acme")
        assert acme.hot_days == 365 and acme.warm_days == 730
        assert cfg.for_customer("other").hot_days == 180


if __name__ == "__main__":
    unittest.main()
