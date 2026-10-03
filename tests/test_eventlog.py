"""Tests for sentinel.eventlog (ADR-011, design/fixes/02-event-log.md).

The executable form of the write-ordering proof: every crash pair A–G
from design §3 gets a fixture — "crash here, assert the river shows
*unconfirmed*, never *paged*."
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import json
import os
import re
import sqlite3
import statistics
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from sentinel import eventlog as ev
from sentinel.checkpoint import (CheckpointJob, LocalDirSink, Sink,
                                 SinkError, checkpoint_hmac)
from sentinel.eventlog import (EventLog, EventLogError, Reaper,
                               derive_dedup_key, detect_flips, utcnow_iso)
from sentinel.migrate import migrate_v01
from sentinel.verify import VerificationFailure, verify


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _now():
    return datetime.now(timezone.utc)


def _decision_body(disposition="page_now", budget_outcome="answered_in_time",
                   outbox_id="obx", req_seq=None, **kw):
    body = {
        "input_sha256": "sh1",
        "fingerprint": "fp1",
        "episode_id": "ep1",
        "jev_model": "jev-1.13.0",
        "q1_reported": 0.9,
        "q2_team": "platform",
        "q3_confidence": 0.95,
        "q3_disposition": "page",
        "disposition": disposition,
        "budget_outcome": budget_outcome,
        "latency_ms": 120.0,
        "timer_fired_at_ms": None,
        "lock_evaluation": {"prob": {"verdict": "pass", "detail": "fit#7"}},
        "freshness": {"fit_as_of": "2026-10-03T00:00:00.000Z"},
        "threshold_counterfactual": {"strict": "suppress"},
        "outbox_id": outbox_id,
        "links": {"decision_requested_seq": req_seq},
    }
    body.update(kw)
    return body


def _outbox_row(episode_id="ep1", outbox_extra=None, **kw):
    row = {
        "alert_id": "a1", "fingerprint": "fp1", "episode_id": episode_id,
        "dedup_key": derive_dedup_key("prod", "fp1", 1),
        "routing_key_ref": "secret:pd/routing_key",
        "payload_frozen": '{"alert":"a1"}',
        "payload_sha256": "deadbeef",
        "priority": 0,
        "next_attempt_at": _iso(_now()),
        "max_age_at": _iso(_now() + timedelta(hours=24)),
    }
    row.update(kw)
    if outbox_extra:
        row.update(outbox_extra)
    return row


def _requested(log, alert_id="a1", fp="fp1", ep="ep1", ts=None, sha="sh1"):
    return log.append_event(
        "decision_requested", actor="engine", alert_id=alert_id,
        fingerprint=fp, episode_id=ep,
        body={"input_sha256": sha, "source_integration": "pd-mirror",
              "severity_in": "critical", "received_ts": _iso(_now()),
              "raw_payload_bytes": 128},
        ts=ts)


class _LogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.log = EventLog(self.tmp.name)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            self.log.close()
        finally:
            os.unlink(self.tmp.name)


# ------------------------------------------------------------------ schema

class TestSchema(_LogTest):
    def test_envelope_columns(self):
        conn = sqlite3.connect(self.tmp.name)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(events)")]
        self.assertEqual(cols, ["seq", "event_id", "schema_v", "ts", "actor",
                                "type", "alert_id", "fingerprint",
                                "episode_id", "outbox_id", "body",
                                "prev_hash", "row_hash"])
        conn.close()

    def test_outbox_columns_match_design_03(self):
        conn = sqlite3.connect(self.tmp.name)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(outbox)")]
        for c in ("outbox_id", "alert_id", "fingerprint", "episode_id",
                  "decision_seq", "dedup_key", "routing_key_ref",
                  "payload_frozen", "payload_sha256", "status", "priority",
                  "attempt_count", "next_attempt_at", "lease_at",
                  "secondary_fired_at", "created_at", "delivered_at",
                  "max_age_at", "last_error"):
            self.assertIn(c, cols, f"outbox missing {c}")
        conn.close()

    def test_indexes(self):
        conn = sqlite3.connect(self.tmp.name)
        idx = {r[0] for r in
               conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for i in ("idx_events_fp", "idx_events_alert", "idx_events_type",
                  "idx_outbox_episode_live", "idx_outbox_due"):
            self.assertIn(i, idx)
        conn.close()

    def test_wal_mode(self):
        conn = sqlite3.connect(self.tmp.name)
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        conn.close()
        self.assertEqual(mode.lower(), "wal")

    def test_event_id_unique(self):
        _requested(self.log)
        conn = sqlite3.connect(self.tmp.name)
        with self.assertRaises(sqlite3.IntegrityError):
            row = conn.execute("SELECT event_id FROM events").fetchone()
            conn.execute(
                "INSERT INTO events (event_id, schema_v, ts, actor, type,"
                " alert_id, fingerprint, episode_id, body, prev_hash,"
                " row_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (row[0], 1, _iso(_now()), "engine", "decision_requested",
                 "a1", "fp1", "ep1", "{}", "x", "y"))
        conn.close()


# ------------------------------------------------------------- validation

class TestValidation(_LogTest):
    def test_rejects_unknown_type(self):
        with self.assertRaises(EventLogError):
            self.log.append_event("page_sent", actor="engine", alert_id="a",
                                  fingerprint="f", episode_id="e", body={})

    def test_rejects_unknown_actor(self):
        with self.assertRaises(EventLogError):
            self.log.append_event("decision_requested", actor="hacker",
                                  alert_id="a", fingerprint="f",
                                  episode_id="e", body={})

    def test_rejects_missing_body_keys(self):
        with self.assertRaises(EventLogError):
            self.log.append_event("decision_made", actor="engine",
                                  alert_id="a", fingerprint="f",
                                  episode_id="e", body={"disposition": "x"})

    def test_rejects_bad_disposition_and_outcome(self):
        good = _decision_body()
        bad = dict(good, disposition="maybe_page")
        with self.assertRaises(EventLogError):
            self.log.record_decision_and_enqueue(
                alert_id="a", fingerprint="f", episode_id="e",
                body=bad, outbox=None)
        bad2 = dict(good, budget_outcome="vibes")
        with self.assertRaises(EventLogError):
            self.log.record_decision_and_enqueue(
                alert_id="a", fingerprint="f", episode_id="e",
                body=bad2, outbox=None)

    def test_all_seven_types_accepted(self):
        bodies = {
            "decision_requested": {"input_sha256": "s",
                                   "source_integration": "x",
                                   "severity_in": "critical",
                                   "received_ts": _iso(_now()),
                                   "raw_payload_bytes": 1},
            "decision_made": _decision_body(),
            "shadow_decision": {"links": {"decision_made_seq": 1}},
            "forward_confirmed": {"outbox_id": "o", "channel": "primary",
                                  "attempt_no": 1, "vendor_status": 202,
                                  "latency_ms": 1.0},
            "forward_failed": {"outbox_id": "o", "channel": "primary",
                               "attempt_no": 1, "error_class": "retryable"},
            "flip_observed": {"input_sha256": "s", "first_seq": 1,
                              "second_seq": 2, "differing_field": "q1",
                              "first_value": 0.9, "second_value": 0.1},
            "checkpoint": {"head_seq": 1, "head_hash": "h",
                           "event_count": 1,
                           "window_start_ts": "GENESIS",
                           "window_end_ts": _iso(_now()),
                           "hmac_hex": "ab", "sink_uri": "u",
                           "sink_push_ok": True},
        }
        for t, body in bodies.items():
            seq = self.log.append_event(
                t, actor="engine", alert_id="a", fingerprint="f",
                episode_id="e", body=body)
            self.assertGreater(seq, 0, t)


# -------------------------------------------------------------- hash chain

class TestHashChain(_LogTest):
    def test_genesis(self):
        seq = _requested(self.log)
        row = self.log.get_event(seq)
        self.assertEqual(row["prev_hash"], "GENESIS")

    def test_chain_links(self):
        s1 = _requested(self.log)
        s2 = self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(req_seq=s1), outbox=None)[0]
        r1, r2 = self.log.get_event(s1), self.log.get_event(s2)
        self.assertEqual(r2["prev_hash"], r1["row_hash"])
        self.assertNotEqual(r1["row_hash"], r2["row_hash"])

    def test_verify_ok(self):
        _requested(self.log)
        self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(), outbox=None)
        result = verify(self.tmp.name)
        self.assertTrue(result["ok"])
        self.assertEqual(result["events"], 2)

    def test_tamper_body_detected(self):
        s1 = _requested(self.log)
        conn = sqlite3.connect(self.tmp.name)
        conn.execute("UPDATE events SET body = '{\"tampered\":true}' "
                     "WHERE seq = ?", (s1,))
        conn.commit()
        conn.close()
        with self.assertRaises(VerificationFailure) as ctx:
            verify(self.tmp.name)
        self.assertIn(f"seq {s1}", str(ctx.exception))

    def test_tamper_prev_hash_detected(self):
        s1 = _requested(self.log)
        s2 = _requested(self.log, alert_id="a2")
        conn = sqlite3.connect(self.tmp.name)
        conn.execute("UPDATE events SET prev_hash = 'FORGED' WHERE seq = ?",
                     (s2,))
        conn.commit()
        conn.close()
        with self.assertRaises(VerificationFailure) as ctx:
            verify(self.tmp.name)
        self.assertIn(f"seq {s2}", str(ctx.exception))

    def test_row_deletion_detected(self):
        _requested(self.log)
        s2 = _requested(self.log, alert_id="a2")
        _requested(self.log, alert_id="a3")
        conn = sqlite3.connect(self.tmp.name)
        conn.execute("DELETE FROM events WHERE seq = ?", (s2,))
        conn.commit()
        conn.close()
        # seq 3's prev_hash no longer matches any predecessor.
        with self.assertRaises(VerificationFailure):
            verify(self.tmp.name)

    def test_verify_from_checkpoint(self):
        _requested(self.log)
        key = b"customer-key"
        job = CheckpointJob(self.log, key,
                            LocalDirSink(tempfile.mkdtemp()))
        cp = job.run_once()
        _requested(self.log, alert_id="a2")
        keyf = self.tmp.name + ".key"
        with open(keyf, "wb") as fh:
            fh.write(key)
        self.addCleanup(os.unlink, keyf)
        result = verify(self.tmp.name)
        self.assertTrue(result["ok"])
        # Anchored verification from the checkpoint event.
        from sentinel.verify import main as vmain
        rc = vmain([self.tmp.name, "--from-seq", str(cp["seq"]),
                    "--hmac-key-file", keyf])
        self.assertEqual(rc, 0)

    def test_verify_from_checkpoint_wrong_key(self):
        _requested(self.log)
        job = CheckpointJob(self.log, b"real-key",
                            LocalDirSink(tempfile.mkdtemp()))
        cp = job.run_once()
        keyf = self.tmp.name + ".key"
        with open(keyf, "wb") as fh:
            fh.write(b"wrong-key")
        self.addCleanup(os.unlink, keyf)
        from sentinel.verify import main as vmain
        rc = vmain([self.tmp.name, "--from-seq", str(cp["seq"]),
                    "--hmac-key-file", keyf])
        self.assertEqual(rc, 1)


# ------------------------------------------------------- no UPDATE, ever

class TestAppendOnly(unittest.TestCase):
    def test_no_update_against_events_in_source(self):
        """The day a builder reaches for UPDATE on events, the design has
        failed to transfer (design §9.1). This grep guard fails the build."""
        srcdir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "src", "sentinel")
        pattern = re.compile(r"(?i)\bupdate\s+events\b")
        offenders = []
        for name in sorted(os.listdir(srcdir)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(srcdir, name)
            with open(path) as fh:
                for lineno, line in enumerate(fh, 1):
                    stripped = line.split("#", 1)[0]
                    if pattern.search(stripped):
                        offenders.append(f"{name}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [],
                         f"UPDATE against events found:\n" +
                         "\n".join(offenders))

    def test_no_update_delete_methods_on_eventlog(self):
        log = EventLog(":memory:")
        for name in ("update", "delete", "remove", "purge", "clear"):
            self.assertFalse(hasattr(log, name))
        log.close()


# ------------------------------------------------------------- crash pairs

class TestCrashPairs(_LogTest):
    """Design §3: crash at the named point; the river shows 'unconfirmed',
    never 'paged'."""

    def test_pair_a_receiver_commit_then_crash_before_202(self):
        # Crash AFTER commit, BEFORE 202: sender retries (timeout). The
        # receiver writes a second decision_requested for the same
        # input_sha256 — an honest duplicate. The projection shows ONE
        # episode; the log shows both receipts.
        s1 = _requested(self.log, sha="dup-sha")
        # ... crash: 202 never sent ...
        s2 = _requested(self.log, sha="dup-sha")  # sender retry
        self.assertNotEqual(s1, s2)
        render = self.log.render_alert("a1")
        self.assertEqual(render["status"], "requested")
        self.assertTrue(render["detail"]["duplicate_receipts"])
        self.assertEqual(render["detail"]["n_requested"], 2)

    def test_pair_b_requested_then_crash_before_decision(self):
        # Orphan requested (older than R=30s, no decision_made): the reaper
        # redrives as passthrough with budget_outcome=reaper_redrive.
        old = _iso(_now() - timedelta(seconds=45))
        s1 = _requested(self.log, ts=old)
        redriven = self.log.startup_sweep()
        self.assertEqual(len(redriven), 1)
        self.assertEqual(redriven[0]["orphan_requested_seq"], s1)
        dec = self.log.events_of_type("decision_made")
        self.assertEqual(len(dec), 1)
        body = json.loads(dec[0]["body"])
        self.assertEqual(body["disposition"], "passthrough")
        self.assertEqual(body["budget_outcome"], "reaper_redrive")
        self.assertEqual(body["links"]["decision_requested_seq"], s1)
        self.assertIsNone(body["jev_model"])
        # The redrive enqueues (I1): the alert pages via the outbox.
        obid = body["outbox_id"]
        self.assertIsNotNone(obid)
        row = self.log.outbox_row(obid)
        self.assertEqual(row["status"], "queued")
        # River: decided but unconfirmed — never "paged".
        render = self.log.render_alert("a1")
        self.assertEqual(render["status"], "passthrough_unconfirmed")
        self.assertNotEqual(render["status"], "paged")

    def test_pair_b_young_request_not_swept(self):
        young = _iso(_now() - timedelta(seconds=10))
        _requested(self.log, ts=young)
        self.assertEqual(self.log.startup_sweep(), [])
        self.assertEqual(self.log.events_of_type("decision_made"), [])

    def test_pair_c_decision_committed_crash_before_pickup(self):
        # Crash AFTER I1 commit, BEFORE forwarder pickup: the outbox row
        # exists. The forwarder's startup scan picks up every non-delivered
        # row. The river shows "decided, delivery unconfirmed".
        s1 = _requested(self.log)
        seq, obid = self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(req_seq=s1), outbox=_outbox_row())
        # ... crash: forwarder never ran ...
        scan = self.log.undelivered_outbox_rows()  # startup scan
        self.assertEqual(len(scan), 1)
        self.assertEqual(scan[0]["outbox_id"], obid)
        render = self.log.render_alert("a1")
        self.assertEqual(render["status"], "decided_unconfirmed")
        self.assertFalse(render["anomaly"])
        self.assertNotEqual(render["status"], "paged")

    def test_pair_d_202_accepted_crash_before_receipt_write(self):
        # Crash AFTER PD 202'd, BEFORE forward_confirmed: restart -> startup
        # scan -> retry with the SAME dedup_key -> PD appends to the open
        # alert (no new page) -> 202 -> forward_confirmed. Paged exactly once.
        s1 = _requested(self.log)
        seq, obid = self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(req_seq=s1), outbox=_outbox_row())
        row = self.log.outbox_row(obid)
        # ... forwarder POSTs, PD 202s, process dies before the I2 txn ...
        scan = self.log.undelivered_outbox_rows()
        self.assertEqual(len(scan), 1)
        # Retry derives the SAME dedup_key (byte-identical across restart).
        retry_key = derive_dedup_key("prod", "fp1", 1)
        self.assertEqual(retry_key, row["dedup_key"])
        self.log.record_receipt_and_update(
            "forward_confirmed", outbox_id=obid,
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body={"outbox_id": obid, "channel": "primary", "attempt_no": 2,
                  "vendor_status": 202, "vendor_message": "Accepted",
                  "vendor_dedup_key_echo": retry_key,
                  "latency_ms": 210.0, "drill": False},
            scheduler={"status": "delivered",
                       "delivered_at": _iso(_now()), "attempt_count": 2})
        render = self.log.render_alert("a1")
        self.assertEqual(render["status"], "paged")

    def test_pair_d_forward_failed_then_retry(self):
        s1 = _requested(self.log)
        seq, obid = self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(req_seq=s1), outbox=_outbox_row())
        self.log.record_receipt_and_update(
            "forward_failed", outbox_id=obid,
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body={"outbox_id": obid, "channel": "primary", "attempt_no": 1,
                  "error_class": "retryable", "http_status": 503,
                  "error_detail": "PD degraded",
                  "next_attempt_at": _iso(_now())},
            scheduler={"status": "queued",
                       "next_attempt_at": _iso(_now()),
                       "attempt_count": 1, "last_error": "PD degraded"})
        render = self.log.render_alert("a1")
        # Decided, delivery unconfirmed — the last error is visible.
        self.assertEqual(render["status"], "decided_unconfirmed")
        self.assertIn("PD degraded",
                      render["detail"].get("last_forward_error", ""))
        row = self.log.outbox_row(obid)
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["attempt_count"], 1)

    def test_pair_e_crash_after_forward_confirmed(self):
        # Terminal: the receipt is durable and the outbox row is delivered
        # in the same transaction. A crash here loses nothing.
        s1 = _requested(self.log)
        seq, obid = self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(req_seq=s1), outbox=_outbox_row())
        self.log.record_receipt_and_update(
            "forward_confirmed", outbox_id=obid,
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body={"outbox_id": obid, "channel": "primary", "attempt_no": 1,
                  "vendor_status": 202, "latency_ms": 180.0},
            scheduler={"status": "delivered",
                       "delivered_at": _iso(_now()), "attempt_count": 1})
        # ... crash ...
        self.assertEqual(self.log.undelivered_outbox_rows(), [])
        render = self.log.render_alert("a1")
        self.assertEqual(render["status"], "paged")

    def test_pair_f_shadow_decision_loss_is_counted(self):
        s1 = _requested(self.log)
        seq, _ = self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(budget_outcome="timer_won", req_seq=s1),
            outbox=_outbox_row())
        # The late answer arrives on the detached worker...
        self.log.append_event(
            "shadow_decision", actor="engine", alert_id="a1",
            fingerprint="fp1", episode_id="ep1",
            body={"jev_model": "jev-1.13.0", "q1_reported": 0.05,
                  "q2_team": "platform", "q3_confidence": 0.9,
                  "q3_disposition": "suppress", "latency_ms": 11400.0,
                  "budget_ms": 1000, "timer_fired_at_ms": 1000,
                  "decided_disposition": "passthrough",
                  "shadow_disposition": "suppress",
                  "would_have_suppressed": True,
                  "lock_evaluation": {}, "links": {"decision_made_seq": seq}})
        # ...or its handoff queue is full and it is dropped: counted, and
        # the paging path never depends on it.
        self.log.note_evidence_drop("shadow")
        self.assertEqual(self.log.metrics["shadow_drops"], 1)
        render = self.log.render_alert("a1")
        self.assertEqual(render["status"], "passthrough_unconfirmed")

    def test_pair_g_flip_rederivable_by_replay(self):
        # flip_observed is a pure function of the log: even if the derived
        # event is lost, replay re-derives it.
        s1 = _requested(self.log, sha="flip-sha")
        seq, _ = self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(budget_outcome="answered_in_time",
                                req_seq=s1, input_sha256="flip-sha"),
            outbox=None)
        self.log.append_event(
            "shadow_decision", actor="engine", alert_id="a1",
            fingerprint="fp1", episode_id="ep1",
            body={"input_sha256": "flip-sha", "jev_model": "jev-1.13.0",
                  "q1_reported": 0.0001, "q3_confidence": 0.99,
                  "latency_ms": 9000.0, "budget_ms": 1000,
                  "timer_fired_at_ms": 1000,
                  "decided_disposition": "passthrough",
                  "shadow_disposition": "suppress",
                  "would_have_suppressed": True,
                  "lock_evaluation": {}, "links": {"decision_made_seq": seq}})
        flips = detect_flips(self.log.events_for_alert("a1"))
        self.assertTrue(any(f["differing_field"] == "q1" for f in flips))
        # The derived flip_observed event records the same finding.
        flip = flips[0]
        fseq = self.log.append_event(
            "flip_observed", actor="calibration", alert_id="a1",
            fingerprint="fp1", episode_id="ep1",
            body={"input_sha256": "flip-sha", "first_seq": flip["first_seq"],
                  "second_seq": flip["second_seq"],
                  "differing_field": flip["differing_field"],
                  "first_value": flip["first_value"],
                  "second_value": flip["second_value"],
                  "jev_model": "jev-1.13.0"})
        self.assertGreater(fseq, 0)


# ------------------------------------------------- invariants as txns

class TestInvariants(_LogTest):
    def test_i1_atomic_decision_and_outbox(self):
        """decision_made + outbox commit in ONE txn: a failed outbox insert
        rolls back the decision event too (no half-written promise)."""
        s1 = _requested(self.log)
        bad_outbox = _outbox_row()
        del bad_outbox["dedup_key"]  # required column -> KeyError before SQL
        with self.assertRaises(KeyError):
            self.log.record_decision_and_enqueue(
                alert_id="a1", fingerprint="fp1", episode_id="ep1",
                body=_decision_body(req_seq=s1), outbox=bad_outbox)
        # Neither half survived.
        self.assertEqual(self.log.events_of_type("decision_made"), [])
        conn = sqlite3.connect(self.tmp.name)
        n = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        conn.close()
        self.assertEqual(n, 0)

    def test_i2_atomic_receipt_and_scheduler(self):
        s1 = _requested(self.log)
        seq, obid = self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(req_seq=s1), outbox=_outbox_row())
        with self.assertRaises(EventLogError):
            self.log.record_receipt_and_update(
                "forward_confirmed", outbox_id="nonexistent",
                alert_id="a1", fingerprint="fp1", episode_id="ep1",
                body={"outbox_id": "nonexistent", "channel": "primary",
                      "attempt_no": 1, "vendor_status": 202,
                      "latency_ms": 1.0},
                scheduler={"status": "delivered"})
        # Receipt rolled back with the scheduler update: no half-state.
        self.assertEqual(self.log.events_of_type("forward_confirmed"), [])
        self.assertEqual(self.log.outbox_row(obid)["status"], "queued")

    def test_i2_rejects_bad_scheduler_keys(self):
        with self.assertRaises(EventLogError):
            self.log.record_receipt_and_update(
                "forward_confirmed", outbox_id="o", alert_id="a",
                fingerprint="f", episode_id="e",
                body={"outbox_id": "o", "channel": "primary",
                      "attempt_no": 1, "vendor_status": 202,
                      "latency_ms": 1.0},
                scheduler={"status": "delivered", "bogus": 1})

    def test_outbox_coalescing_duplicate_episode(self):
        """UNIQUE(episode_id) WHERE live: a duplicate page decision for an
        already-queued episode rides the existing row (design 03 §2.1)."""
        s1 = _requested(self.log)
        seq1, ob1 = self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(req_seq=s1), outbox=_outbox_row())
        s2 = _requested(self.log, alert_id="a1b")
        seq2, ob2 = self.log.record_decision_and_enqueue(
            alert_id="a1b", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(req_seq=s2), outbox=_outbox_row())
        self.assertEqual(ob1, ob2)  # same row — no second PD alert
        self.assertEqual(self.log.metrics["coalesced_duplicates"], 1)
        conn = sqlite3.connect(self.tmp.name)
        n = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)


# ------------------------------------------------------- river rule

class TestRiverRule(_LogTest):
    def _decided_unconfirmed(self, age_s):
        ts = _iso(_now() - timedelta(seconds=age_s))
        s1 = _requested(self.log, ts=ts)
        seq, obid = self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(req_seq=s1), outbox=_outbox_row())
        # Backdate the decision event's ts for the age test.
        conn = sqlite3.connect(self.tmp.name)
        conn.execute("UPDATE events SET ts = ? WHERE seq = ?", (ts, seq))
        conn.commit()
        conn.close()
        return obid

    def test_anomaly_fires_past_sla(self):
        self._decided_unconfirmed(age_s=90)
        render = self.log.render_alert("a1")
        self.assertEqual(render["status"], "PAGED_ANOMALY")
        self.assertTrue(render["anomaly"])

    def test_no_anomaly_within_sla(self):
        self._decided_unconfirmed(age_s=10)
        render = self.log.render_alert("a1")
        self.assertEqual(render["status"], "decided_unconfirmed")
        self.assertFalse(render["anomaly"])

    def test_confirmed_never_anomaly(self):
        obid = self._decided_unconfirmed(age_s=3600)
        self.log.record_receipt_and_update(
            "forward_confirmed", outbox_id=obid,
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body={"outbox_id": obid, "channel": "primary", "attempt_no": 1,
                  "vendor_status": 202, "latency_ms": 180.0},
            scheduler={"status": "delivered",
                       "delivered_at": _iso(_now()), "attempt_count": 1})
        render = self.log.render_alert("a1")
        self.assertEqual(render["status"], "paged")
        self.assertFalse(render["anomaly"])

    def test_suppress_renders_suppressed(self):
        s1 = _requested(self.log)
        self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(disposition="suppress", outbox_id=None),
            outbox=None)
        render = self.log.render_alert("a1")
        self.assertEqual(render["status"], "suppressed")

    def test_unknown_alert(self):
        render = self.log.render_alert("nope")
        self.assertEqual(render["status"], "unknown")


# ------------------------------------------------------------- watchdog

class TestWatchdog(_LogTest):
    def test_slow_commit_trips_degraded_path(self):
        """Fault-inject a sick disk: the watchdog fires, the degraded path
        engages, and the evidence-loss page is enqueued (design §4)."""
        spill = tempfile.mkdtemp()
        self.log.close()
        degraded = []
        self.log = EventLog(self.tmp.name, spillover_dir=spill,
                            degraded_sender=degraded.append)
        self.log._test_commit_delay_s = 0.06  # 60ms > 50ms budget
        try:
            _requested(self.log)
        finally:
            self.log._test_commit_delay_s = 0.0
        # 1. The trip was recorded.
        self.assertEqual(self.log.metrics["watchdog_trips"], 1)
        # 2. The event itself IS durable (commit completed, just slow).
        self.assertEqual(len(self.log.events_of_type("decision_requested")),
                         1)
        # 3. Emergency spillover record written.
        self.assertEqual(len(os.listdir(spill)), 1)
        # 4. Degraded direct inline send attempted.
        self.assertEqual(len(degraded), 1)
        self.assertEqual(degraded[0]["kind"], "evidence_loss")
        # 5. Unsuppressible control-plane page via outbox priority lane.
        pages = [r for r in self.log.undelivered_outbox_rows(limit=10)
                 if r["priority"] == 1]
        self.assertGreaterEqual(len(pages), 1)
        self.assertTrue(any("evidence" in r["payload_frozen"] for r in pages))
        self.assertEqual(self.log.metrics["evidence_loss_pages"], 1)

    def test_watchdog_does_not_recurse(self):
        """The degraded path's own commits must not re-trip the watchdog."""
        self.log._test_commit_delay_s = 0.06
        try:
            _requested(self.log)
        finally:
            self.log._test_commit_delay_s = 0.0
        # Exactly one trip: no infinite recursion on the sick disk.
        self.assertEqual(self.log.metrics["watchdog_trips"], 1)


# ------------------------------------------------------------ performance

class TestPerformance(_LogTest):
    def test_p99_event_write_under_5ms(self):
        """CI asserts p99 event-write < 5ms — measured, not assumed (§4)."""
        durations = []
        for i in range(300):
            t0 = time.perf_counter()
            _requested(self.log, alert_id=f"p{i}", sha=f"sh{i}")
            durations.append((time.perf_counter() - t0) * 1000.0)
        durations.sort()
        p99 = durations[int(0.99 * len(durations))]
        p50 = durations[int(0.50 * len(durations))]
        print(f"\nevent-write ms: p50={p50:.3f} p99={p99:.3f} "
              f"(budget p99<{ev.P99_WRITE_BUDGET_MS})")
        self.assertLess(p99, ev.P99_WRITE_BUDGET_MS)


# ---------------------------------------------------------------- outbox

class TestOutbox(_LogTest):
    def test_dedup_key_stable_across_retries_and_restart(self):
        """design 03 §3: the same episode always yields the same key."""
        keys = {derive_dedup_key("prod", "fp1", 42) for _ in range(100)}
        self.assertEqual(len(keys), 1)
        key = keys.pop()
        self.assertTrue(key.startswith("sentinel/prod/fp1/0042"))
        # Across a process restart: the row stores it; re-derivation agrees.
        s1 = _requested(self.log)
        seq, obid = self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(req_seq=s1),
            outbox=_outbox_row(
                dedup_key=derive_dedup_key("prod", "fp1", 42)))
        self.log.close()
        log2 = EventLog(self.tmp.name)
        self.addCleanup(log2.close)
        row = log2.outbox_row(obid)
        self.assertEqual(row["dedup_key"],
                         derive_dedup_key("prod", "fp1", 42))

    def test_dedup_key_long_env_fallback(self):
        key = derive_dedup_key("e" * 300, "fp1", 1)
        self.assertLessEqual(len(key), 64)
        self.assertTrue(key.startswith("sentinel/"))

    def test_due_rows_priority_first(self):
        now = _now()
        s1 = _requested(self.log)
        self.log.record_decision_and_enqueue(
            alert_id="a1", fingerprint="fp1", episode_id="ep1",
            body=_decision_body(req_seq=s1),
            outbox=_outbox_row(next_attempt_at=_iso(now)))
        s2 = _requested(self.log, alert_id="a2", fp="fp2", ep="ep2")
        self.log.record_decision_and_enqueue(
            alert_id="a2", fingerprint="fp2", episode_id="ep2",
            body=_decision_body(req_seq=s2),
            outbox=_outbox_row(episode_id="ep2", priority=1,
                               next_attempt_at=_iso(now)))
        due = self.log.due_outbox_rows(_iso(now + timedelta(seconds=1)))
        self.assertEqual(len(due), 2)
        self.assertEqual(due[0]["episode_id"], "ep2")  # priority first
        # Future rows are not due.
        future = self.log.due_outbox_rows(_iso(now - timedelta(seconds=1)))
        self.assertEqual(future, [])

    def test_control_plane_page_enqueued(self):
        obid = self.log._enqueue_control_plane_page(
            kind="test", summary="s", detail={})
        row = self.log.outbox_row(obid)
        self.assertEqual(row["priority"], 1)
        self.assertEqual(row["status"], "queued")


# ------------------------------------------------------------ checkpoint

class _FailingSink(Sink):
    def push(self, doc):
        raise SinkError("sink down")


class TestCheckpoint(_LogTest):
    def test_hmac_and_sink_file(self):
        sinkdir = tempfile.mkdtemp()
        _requested(self.log)
        key = b"customer-key"
        job = CheckpointJob(self.log, key, LocalDirSink(sinkdir))
        r = job.run_once()
        self.assertTrue(r["sink_push_ok"])
        # The sink file is signed and verifiable by the customer.
        files = os.listdir(sinkdir)
        self.assertEqual(len(files), 1)
        with open(os.path.join(sinkdir, files[0])) as fh:
            doc = json.load(fh)
        expect = checkpoint_hmac(key, doc["head_seq"], doc["head_hash"],
                                 doc["event_count"], doc["window_start_ts"],
                                 doc["window_end_ts"])
        self.assertEqual(doc["hmac_hex"], expect)
        # The checkpoint is in-band: the chain covers it.
        cps = self.log.events_of_type("checkpoint")
        self.assertEqual(len(cps), 1)
        cbody = json.loads(cps[0]["body"])
        self.assertTrue(cbody["sink_push_ok"])
        self.assertEqual(cbody["hmac_hex"], expect)
        self.assertTrue(verify(self.tmp.name)["ok"])

    def test_requires_key(self):
        with self.assertRaises(ValueError):
            CheckpointJob(self.log, b"", LocalDirSink(tempfile.mkdtemp()))

    def test_sink_failure_pages_after_two_hours(self):
        _requested(self.log)
        job = CheckpointJob(self.log, b"k", _FailingSink("http://sink"))
        for _ in range(3):
            r = job.run_once()
            self.assertFalse(r["sink_push_ok"])
        self.assertEqual(job.consecutive_failures, 3)
        pages = [r for r in self.log.undelivered_outbox_rows(limit=10)
                 if r["priority"] == 1]
        self.assertTrue(any("checkpoint" in r["payload_frozen"]
                            for r in pages))
        # ...but a single failure does not page.
        log2 = EventLog(":memory:")
        self.addCleanup(log2.close)
        job2 = CheckpointJob(log2, b"k", _FailingSink("http://sink"))
        job2.run_once()
        self.assertEqual(
            [r for r in log2.undelivered_outbox_rows() if r["priority"] == 1],
            [])


# ------------------------------------------------------------ migration

class TestMigration(unittest.TestCase):
    def _v01_db(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.executescript("""
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY, received_at TEXT, alert_id TEXT,
            fingerprint TEXT, input_sha256 TEXT, jev_model TEXT,
            q1_severity TEXT, q1_probs TEXT, q1_conf REAL,
            q2_team TEXT, q2_probs TEXT, q2_conf REAL,
            q3_disposition TEXT, q3_probs TEXT, q3_conf REAL,
            action TEXT NOT NULL, reason TEXT NOT NULL,
            latency_ms REAL,
            created_at TEXT NOT NULL DEFAULT
                (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
        """)
        conn.execute(
            "INSERT INTO decisions (received_at, alert_id, fingerprint,"
            " input_sha256, jev_model, q1_severity, q1_probs, q1_conf,"
            " q2_team, action, reason, latency_ms) VALUES"
            " ('2026-10-02T12:00:00Z','a1','fp1','sh1','jev-1.13.0',"
            "  'p1_critical','{\"p1_critical\":0.9}',0.9,'platform',"
            "  'page_now','threshold',12.5),"
            " ('2026-10-02T12:01:00Z','a2','fp2','sh2',NULL,"
            "  'p4_low','{\"p4_low\":0.8}',0.8,NULL,"
            "  'suppress','threshold',3.0)")
        conn.commit()
        conn.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_migration_backfills_without_lying(self):
        db = self._v01_db()
        result = migrate_v01(db)
        self.assertTrue(result["migrated"])
        self.assertEqual(result["events_backfilled"], 2)
        log = EventLog(db)
        self.addCleanup(log.close)
        decisions = log.events_of_type("decision_made")
        self.assertEqual(len(decisions), 2)
        for d in decisions:
            self.assertEqual(d["actor"], "migration")
            body = json.loads(d["body"])
            self.assertEqual(body["budget_outcome"], "pre_race_unknown")
            self.assertEqual(body["migrated_from"], "v0.1-decisions")
            self.assertIsNone(body["outbox_id"])
        # NEVER synthesizes forward_confirmed — the exact lie this kills.
        self.assertEqual(log.events_of_type("forward_confirmed"), [])
        # Old table renamed, read-only history kept.
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("decisions_v0_1", tables)
        self.assertNotIn("decisions", tables)
        views = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")}
        self.assertIn("decisions", views)
        n = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        self.assertEqual(n, 2)  # VIEW serves the backfilled events
        conn.close()
        # The chain verifies over migrated history.
        self.assertTrue(verify(db)["ok"])

    def test_migration_idempotent(self):
        db = self._v01_db()
        migrate_v01(db)
        again = migrate_v01(db)
        self.assertFalse(again["migrated"])

    def test_migration_no_v01_table(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        result = migrate_v01(tmp.name)
        self.assertFalse(result["migrated"])


if __name__ == "__main__":
    unittest.main()
