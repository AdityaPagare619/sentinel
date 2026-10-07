"""Tests for the durable forwarder (design 03 / ADR-012).

What this battery proves, per the lane's mandate:
  - PD's retry table is copied into an executable fixture (a PD docs change
    becomes a build break, not a 3 AM discovery).
  - Crash safety: kill -9 between "page sent" and "outbox marked delivered"
    neither loses the page nor double-pages (dedup_key + outbox state
    machine). Both residual classes from design §4.3 are tested:
    Case 1 (retry → open alert: appended, one incident) and
    Case 2 (retry after resolve: one new alert, self-describing).
  - Idempotency: the same dedup_key twice → one PD incident.
  - Standby: outbox unavailable → direct-to-PD + spill → replay at startup.
  - Secondary fires at X, primary retries CONTINUE (the pre-mortem rule).
  - 400 → dead_letter + secondary + control-plane page.
  - 429 honors Retry-After and the 60s floor.
  - routing_key_ref pinned across rotation.
  - The wakefulness attestation + cutover gate refuse to start.
  - Claims are atomic across two forwarders.

Determinism: tests drive `run_once_sync()` (identical attempt path, no
threads) except where threading itself is under test (claim atomicity,
start/drain/stop). Timing-sensitive tests use tiny Type-2 values.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sentinel.eventlog import EventLog, utcnow_iso  # noqa: E402
from sentinel.forwarder import (  # noqa: E402
    DurableForwarder,
    ForwarderConfig,
    _iso_to_ts,
    _ts_to_iso,
)
from sentinel.pd_sender import (  # noqa: E402
    PD_RETRY_TABLE,
    classify,
    resolve_routing_key,
    SecretMissing,
)
from sentinel.secondary import (  # noqa: E402
    DrillTracker,
    SecondaryConfig,
    SecondaryConfigError,
    SecondaryNotReady,
    WebhookSecondary,
    require_drilled_secondary,
)
from sentinel.spill import (  # noqa: E402
    archive_spill,
    iter_spills,
    replay_spills,
    write_spill,
)


# ===========================================================================
# doubles


class ScriptedHTTP:
    """Scripted HTTP double. Pops (status, body, headers) from `script`;
    falls back to `_default_response`. Records every request."""

    def __init__(self):
        self.requests = []  # dicts: path, headers, body, json
        self.script = []
        self._lock = threading.RLock()  # RLock: do_POST holds it while the
                                        # PD double computes its response
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    parsed = json.loads(raw.decode("utf-8")) if raw else None
                except ValueError:
                    parsed = None
                with outer._lock:
                    outer.requests.append({
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": raw,
                        "json": parsed,
                    })
                    if outer.script:
                        status, body, headers = outer.script.pop(0)
                    else:
                        status, body, headers = outer._default_response(
                            parsed, dict(self.headers))
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in headers.items():
                    self.send_header(k, str(v))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    def _default_response(self, parsed, headers):
        return 202, {"status": "success"}, {}

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/v2/enqueue"

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class FakePD(ScriptedHTTP):
    """PD Events API v2 double with REAL dedup semantics (design §4.1):

    same dedup_key + same routing_key ⇒ applied to the OPEN alert matching
    that key (a trigger log entry is created on the existing alert — no new
    incident, no fresh escalation). Once resolved: a trigger with the same
    key CREATES A NEW alert.

    Scripted entries are consumed first and BYPASS incident tracking — use
    them for failure injection (500/400/429 create nothing, as in reality).
    Success flows through the default path below, which implements dedup.
    """

    def __init__(self):
        # Incidents as a LIST: PD's post-resolve behavior creates a NEW
        # alert for the same dedup_key, so one key can own two incidents.
        self.incidents = []  # {"dedup_key", "triggers": [...], "resolved"}
        super().__init__()

    def _open_incident(self, key):
        for inc in reversed(self.incidents):
            if inc["dedup_key"] == key and not inc["resolved"]:
                return inc
        return None

    def _default_response(self, parsed, headers):
        key = (parsed or {}).get("dedup_key") or "auto-uuid"
        with self._lock:
            inc = self._open_incident(key)
            if inc is not None:
                inc["triggers"].append(parsed)
                return (202, {"status": "success", "message":
                              "trigger log entry appended to open alert",
                              "dedup_key": key}, {})
            self.incidents.append({"dedup_key": key, "triggers": [parsed],
                                   "resolved": False})
            return (202, {"status": "success", "message": "Event processed",
                          "dedup_key": key}, {})

    @property
    def incident_count(self):
        with self._lock:
            return len(self.incidents)

    def triggers_for(self, dedup_key):
        """Trigger-log entries on the LATEST incident for the key."""
        with self._lock:
            for inc in reversed(self.incidents):
                if inc["dedup_key"] == dedup_key:
                    return len(inc["triggers"])
            return 0

    def resolve(self, dedup_key):
        with self._lock:
            for inc in reversed(self.incidents):
                if inc["dedup_key"] == dedup_key:
                    inc["resolved"] = True
                    return


class FakeClock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


# ===========================================================================
# fixtures


TEST_REF = "secret:pd/test"
TEST_ENV = "SENTINEL_SECRET_PD_TEST"
CONTROL_REF = "secret:pd/control_routing_key"
CONTROL_ENV = "SENTINEL_SECRET_PD_CONTROL_ROUTING_KEY"


def _frozen(summary="boom", severity="critical"):
    return json.dumps({
        "event_action": "trigger",
        "dedup_key": "sentinel/test/fp1/0042",
        "payload": {"summary": summary, "severity": severity,
                    "source": "svc", "component": "svc",
                    "custom_details": {"check": "http_5xx"}},
    }, sort_keys=True)


def _decision_body(disposition="page_now"):
    return {"disposition": disposition, "budget_outcome": "answered_in_time",
            "lock_evaluation": {}, "freshness": {},
            "threshold_counterfactual": {}, "links": {}}


class ForwarderTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "sentinel.db")
        self.spill_dir = os.path.join(self.tmp.name, "spill")
        self.log = EventLog(self.db)
        self.pd = FakePD()
        self.addCleanup(self.pd.close)
        self._old_env = dict(os.environ)
        os.environ[TEST_ENV] = "test-routing-key-abc"
        os.environ[CONTROL_ENV] = "test-control-key-xyz"
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._old_env)

    def _config(self, **kw):
        base = dict(env="test", pd_endpoint=self.pd.url, pd_timeout_s=5.0,
                    workers=2, poll_s=3600.0, backoff_s=(0.05, 0.1, 0.2),
                    jitter=0.0, secondary_fire_after_s=3600.0,
                    spill_dir=self.spill_dir, stage="shadow")
        base.update(kw)
        return ForwarderConfig(**base)

    def _enqueue(self, *, alert_id="a1", fp="fp1", episode="ep1",
                 dedup_key="sentinel/test/fp1/0042", ref=TEST_REF,
                 priority=0, frozen=None, max_age_at=None,
                 next_attempt_at=None, disposition="page_now"):
        frozen = frozen if frozen is not None else _frozen()
        now = utcnow_iso()
        outbox = {
            "alert_id": alert_id, "fingerprint": fp, "episode_id": episode,
            "dedup_key": dedup_key, "routing_key_ref": ref,
            "payload_frozen": frozen,
            "payload_sha256": hashlib.sha256(frozen.encode()).hexdigest(),
            "priority": priority,
            "next_attempt_at": next_attempt_at or now,
            "max_age_at": max_age_at or _ts_to_iso(time.time() + 86400),
        }
        seq, obid = self.log.record_decision_and_enqueue(
            alert_id=alert_id, fingerprint=fp, episode_id=episode,
            body=_decision_body(disposition), outbox=outbox)
        return obid

    def _receipts(self, type_):
        return [json.loads(e["body"])
                for e in self.log.events_of_type(type_)]

    def _row(self, obid):
        return self.log.outbox_row(obid)


# ===========================================================================
# the PD contract, as an executable fixture


class TestPDRetryTable(ForwarderTestBase):
    def test_classifier_matches_documented_table_row_for_row(self):
        # design §4.1, verified against PD's docs 2026-10-03. If PD changes
        # their docs, THIS test fails in CI — the contract drift becomes a
        # build break, not a 3 AM discovery.
        self.assertEqual(PD_RETRY_TABLE[202], "accepted")
        self.assertEqual(PD_RETRY_TABLE[400], "terminal")
        self.assertEqual(PD_RETRY_TABLE[429], "retryable")
        for code in (500, 502, 503, 504):
            self.assertEqual(PD_RETRY_TABLE[code], "retryable")
        self.assertEqual(PD_RETRY_TABLE["network"], "retryable")

        self.assertEqual(classify(202, "success")[0], "accepted")
        self.assertEqual(classify(400)[0], "terminal")
        self.assertEqual(classify(429)[0], "retryable")
        for code in (500, 502, 503, 504, 599):
            self.assertEqual(classify(code)[0], "retryable",
                             f"5xx must be retryable, got {code}")
        self.assertEqual(classify(None, network_error=True)[0], "retryable")

    def test_202_without_success_is_vendor_contract_violation(self):
        # design §4.2: a 202 that doesn't echo status == "success" is a
        # vendor contract violation — retryable + loud, never "accepted".
        outcome, detail = classify(202, "weird")
        self.assertEqual(outcome, "retryable")
        self.assertIn("vendor_contract_violation", detail)

    def test_unknown_4xx_is_terminal_unknown_other_is_retryable(self):
        self.assertEqual(classify(403)[0], "terminal")   # client error: futile
        self.assertEqual(classify(200)[0], "retryable")  # unexpected: safe dir


# ===========================================================================
# happy path + retries


class TestDelivery(ForwarderTestBase):
    def test_happy_path_delivers_and_confirms(self):
        obid = self._enqueue()
        fw = DurableForwarder(self.log, self._config())
        try:
            self.assertEqual(fw.run_once_sync(), 1)
        finally:
            fw._conn.close()
        row = self._row(obid)
        self.assertEqual(row["status"], "delivered")
        self.assertIsNotNone(row["delivered_at"])
        self.assertEqual(len(self.pd.requests), 1)
        wire = self.pd.requests[0]["json"]
        # The frozen payload was keyless; the sender injected the resolved key.
        self.assertEqual(wire["routing_key"], "test-routing-key-abc")
        self.assertEqual(wire["dedup_key"], "sentinel/test/fp1/0042")
        self.assertNotIn("routing_key",
                         json.loads(self._row(obid)["payload_frozen"]))
        # forward_confirmed: 202 + status == "success", nothing less.
        confirmed = self._receipts("forward_confirmed")
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["channel"], "pagerduty")
        self.assertEqual(confirmed[0]["vendor_status"], "success")
        self.assertIn("wire_sha256", confirmed[0])

    def test_retry_then_success_reuses_dedup_key(self):
        # Two failures, then the default path: 202 + real dedup semantics.
        self.pd.script = [(500, {"status": "error"}, {}),
                          (500, {"status": "error"}, {})]
        obid = self._enqueue()
        fw = DurableForwarder(self.log, self._config())
        try:
            for _ in range(3):
                fw.run_once_sync()
                time.sleep(0.15)  # backoff is 0.05/0.1s in this config
        finally:
            fw._conn.close()
        self.assertEqual(self._row(obid)["status"], "delivered")
        self.assertEqual(len(self.pd.requests), 3)
        keys = [r["json"]["dedup_key"] for r in self.pd.requests]
        # Byte-identical dedup_key across all retries: the idempotency
        # property (design §3 — correctness, not convenience).
        self.assertEqual(keys, [keys[0]] * 3)
        self.assertEqual(keys[0], "sentinel/test/fp1/0042")
        # PD saw one incident (dedup appended), not three pages. (The two
        # scripted 500s bypass incident tracking — PD was "down" for them.)
        self.assertEqual(self.pd.incident_count, 1)
        # The retry is self-describing (design §4.3 Case 2).
        retry_bodies = [r["json"] for r in self.pd.requests[1:]]
        for i, body in enumerate(retry_bodies, start=2):
            details = body["payload"]["custom_details"]
            self.assertTrue(details.get("sentinel_retry"))
            self.assertEqual(details.get("attempt_no"), i)

    def test_backoff_schedule_shape(self):
        fw = DurableForwarder(self.log, self._config(
            backoff_s=(5, 15, 45), jitter=0.0))
        try:
            delays = [fw._retry_delay_s(n, None) for n in (1, 2, 3, 4, 99)]
        finally:
            fw._conn.close()
        self.assertEqual(delays, [5, 15, 45, 45, 45])  # capped at the tail

    def test_missing_secret_is_retryable_not_terminal(self):
        del os.environ[TEST_ENV]
        obid = self._enqueue()
        fw = DurableForwarder(self.log, self._config())
        try:
            fw.run_once_sync()
        finally:
            fw._conn.close()
        row = self._row(obid)
        self.assertEqual(row["status"], "queued")  # NOT dead_letter
        failed = self._receipts("forward_failed")
        self.assertEqual(failed[0]["error_class"], "secret_missing")
        self.assertEqual(len(self.pd.requests), 0)  # never sent keyless

    def test_tampered_frozen_payload_is_terminal_and_loud(self):
        obid = self._enqueue()
        # Tamper with the frozen payload behind the sha's back.
        conn = self.log._conn
        with self.log._lock:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("UPDATE outbox SET payload_frozen = ? "
                         "WHERE outbox_id = ?",
                         (_frozen(summary="TAMPERED"), obid))
            conn.execute("COMMIT;")
        # No secondary configured here: terminal still dead-letters + pages
        # the control plane. (Secondary coverage lives in TestSecondary.)
        fw = DurableForwarder(self.log, self._config())
        try:
            fw.run_once_sync()
        finally:
            fw._conn.close()
        row = self._row(obid)
        self.assertEqual(row["status"], "dead_letter")
        failed = self._receipts("forward_failed")
        self.assertEqual(failed[0]["error_class"], "payload_integrity_error")
        self.assertEqual(len(self.pd.requests), 0)  # tampered page never sent
        # The morgue has an alarm: a control-plane page went out.
        cp = [r for r in self.log.undelivered_outbox_rows()
              if r["priority"] == 1]
        self.assertTrue(cp, "expected a control-plane page for the tamper")


# ===========================================================================
# crash safety — kill -9 in the crash window (design §4.3)


class TestCrashSafety(ForwarderTestBase):
    _CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "crash_child.py")

    def _run_crash_child(self):
        env = dict(os.environ)
        env["FWD_CRASH_DB"] = self.db
        env["FWD_CRASH_PD_URL"] = self.pd.url
        env["FWD_CRASH_SPILL"] = self.spill_dir
        proc = subprocess.run([sys.executable, self._CHILD], env=env,
                              timeout=60, capture_output=True, text=True)
        return proc

    def _restart_and_recover(self):
        """Simulate the process restart: fresh EventLog + forwarder.

        The crashed claim's lease must be stale for the startup scan to
        re-queue it (design §2.1: leases protect against a still-alive
        worker). Production uses lease_s=300; the test uses 0.5s.
        """
        time.sleep(0.7)
        log2 = EventLog(self.db)
        fw = DurableForwarder(log2, self._config(lease_s=0.5))
        scan = fw.startup_scan()
        fw.run_once_sync()
        fw._conn.close()
        return log2, fw, scan

    def test_crash_between_send_and_receipt_no_loss_no_double_page(self):
        # Case 1 (design §4.3): retry lands on the OPEN alert → appended,
        # exactly one PD incident, page not lost.
        obid = self._enqueue()
        dk = "sentinel/test/fp1/0042"

        proc = self._run_crash_child()
        self.assertEqual(proc.returncode, 1,
                         f"child should die in the crash window: {proc.stderr[-500:]}")
        # The page WAS sent exactly once before the crash…
        self.assertEqual(len(self.pd.requests), 1)
        self.assertEqual(self.pd.incident_count, 1)
        # …but the outbox was never marked delivered.
        row = self._row(obid)
        self.assertEqual(row["status"], "in_flight")
        self.assertEqual(
            [json.loads(e["body"])["error_class"]
             for e in self.log.events_of_type("forward_confirmed")], [])

        # Restart: the startup scan re-queues the stranded row (honestly —
        # forward_failed/crash_window_requeue), the retry reuses the pinned
        # dedup_key, PD dedups it onto the open alert.
        log2, fw, scan = self._restart_and_recover()
        self.assertEqual(scan["requeued"], 1)
        requeues = [b for b in self._receipts("forward_failed")
                    if b["error_class"] == "crash_window_requeue"]
        self.assertEqual(len(requeues), 1)

        self.assertEqual(len(self.pd.requests), 2)
        keys = [r["json"]["dedup_key"] for r in self.pd.requests]
        self.assertEqual(keys, [dk, dk])  # byte-identical across the crash
        self.assertEqual(self.pd.incident_count, 1)  # NO double page
        self.assertEqual(self.pd.triggers_for(dk), 2)  # appended, not new
        self.assertEqual(log2.outbox_row(obid)["status"], "delivered")
        confirmed = [b for b in
                     [json.loads(e["body"])
                      for e in log2.events_of_type("forward_confirmed")]
                     if b["channel"] == "pagerduty"]
        self.assertEqual(len(confirmed), 1)
        # The audit tells the whole story: crash → retry → confirmed.
        self.assertEqual(fw.metrics["requeued"], 1)
        self.assertEqual(fw.metrics["confirmed"], 1)

    def test_crash_retry_after_resolve_is_one_loud_new_alert(self):
        # Case 2 (design §4.3): the human resolved the alert inside the
        # crash window. The retry creates ONE new alert (PD's documented
        # post-resolve behavior) — bounded, loud-safe, self-describing.
        obid = self._enqueue()
        dk = "sentinel/test/fp1/0042"

        proc = self._run_crash_child()
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.pd.incident_count, 1)

        self.pd.resolve(dk)  # the human resolved inside the crash window

        log2, fw, scan = self._restart_and_recover()
        self.assertEqual(len(self.pd.requests), 2)
        self.assertEqual(self.pd.incident_count, 2)  # the bounded duplicate
        retry_body = self.pd.requests[1]["json"]
        details = retry_body["payload"]["custom_details"]
        self.assertTrue(details.get("sentinel_retry"))
        self.assertEqual(details.get("attempt_no"), 2)
        self.assertEqual(log2.outbox_row(obid)["status"], "delivered")


# ===========================================================================
# standby direct-to-PD + spill replay (design §6 F1)


class TestStandbyAndSpill(ForwarderTestBase):
    def test_send_direct_when_outbox_unavailable(self):
        # The outbox is GONE (disk dead). The page must still go out —
        # send_direct never touches the queue.
        fw = DurableForwarder(self.log, self._config())
        try:
            os.remove(self.db)
            for suffix in ("-wal", "-shm"):
                try:
                    os.remove(self.db + suffix)
                except OSError:
                    pass
            outcome = fw.send_direct(
                {"kind": "evidence_loss", "summary": "disk dead",
                 "alert_id": "a9"}, reason="test")
        finally:
            try:
                fw._conn.close()
            except Exception:
                pass
        self.assertEqual(outcome["pd_outcome"], "accepted")
        self.assertEqual(len(self.pd.requests), 1)
        wire = self.pd.requests[0]["json"]
        self.assertEqual(wire["payload"]["custom_details"]
                         ["sentinel_degraded"], True)
        self.assertTrue(wire["dedup_key"].startswith(
            "sentinel/test/degraded/evidence_loss/"))
        self.assertIsNotNone(outcome["spill"])
        self.assertTrue(os.path.exists(outcome["spill"]))

    def test_send_direct_never_raises(self):
        cfg = self._config(spill_dir=None)
        cfg.pd_endpoint = "http://127.0.0.1:1/v2/enqueue"  # nothing there
        cfg.pd_timeout_s = 1.0
        log = EventLog(os.path.join(self.tmp.name, "d2.db"))
        fw = DurableForwarder(log, cfg)
        try:
            outcome = fw.send_direct({"kind": "x"}, reason="test")
        finally:
            fw._conn.close()
        self.assertEqual(outcome["pd_outcome"], "retryable")
        self.assertIsNone(outcome["spill"])  # no spill dir configured

    def test_spill_replay_restores_the_audit(self):
        spill_path = write_spill(self.spill_dir, {
            "kind": "evidence_loss", "alert_id": "a9",
            "fingerprint": "fp9", "episode_id": "ep9",
            "dedup_key": "sentinel/test/degraded/evidence_loss/20261003",
            "payload_sha256": "abc", "routing_key_ref": CONTROL_REF,
            "reason": "test", "pd_outcome": "accepted", "pd_status": 202,
            "latency_ms": 12.5, "error": None})
        self.assertTrue(os.path.exists(spill_path))
        replayed = replay_spills(self.log, self.spill_dir)
        self.assertEqual(len(replayed), 1)
        self.assertTrue(os.path.exists(spill_path + ".replayed"))
        confirmed = [b for b in self._receipts("forward_confirmed")
                     if b["channel"] == "direct-degraded"]
        self.assertEqual(len(confirmed), 1)
        # Second replay finds nothing (archived).
        self.assertEqual(replay_spills(self.log, self.spill_dir), [])

    def test_watchdog_trip_wires_degraded_send(self):
        # The event-log lane's commit watchdog → our degraded_sender:
        # direct-to-PD + spill + control-plane page, end to end.
        fw = DurableForwarder(self.log, self._config())
        try:
            fw.start()  # wires degraded_sender; runs startup scan
            fw.stop()   # stop threads; the wiring persists on the log
            self.log._test_commit_delay_s = 0.06  # sick disk (>50ms budget)
            try:
                self.log.append_event(
                    "shadow_decision", actor="engine", alert_id="a1",
                    fingerprint="fp1", episode_id="ep1",
                    body={"links": {}})
            finally:
                self.log._test_commit_delay_s = 0.0
        finally:
            try:
                fw._conn.close()
            except Exception:
                pass
        # The degraded page went out direct-to-PD…
        self.assertEqual(len(self.pd.requests), 1)
        wire = self.pd.requests[0]["json"]
        self.assertTrue(wire["dedup_key"].startswith(
            "sentinel/test/degraded/evidence_loss/"))
        # …the spill record exists…
        self.assertTrue(iter_spills(self.spill_dir),
                        "expected a spill record from the watchdog trip")
        # …and the unsuppressible control-plane page was enqueued priority-1.
        cp = [r for r in self.log.undelivered_outbox_rows()
              if r["priority"] == 1]
        self.assertTrue(cp)
        # The forwarder delivers the control-plane page on its next pass.
        fw2 = DurableForwarder(self.log, self._config())
        try:
            fw2.run_once_sync()
        finally:
            fw2._conn.close()
        self.assertEqual(len(self.pd.requests), 2)
        self.assertEqual(self.pd.incident_count, 2)  # distinct dedup_keys


# ===========================================================================
# secondary channel (design §5.2) + the pre-mortem rule


class TestSecondary(ForwarderTestBase):
    def _secondary_cfg(self, url):
        return SecondaryConfig(
            provider="test-webhook", destination=url,
            wakefulness="test double: posts to localhost, wakes nobody — "
                        "this is a test, not a paging path")

    def test_400_is_terminal_secondary_fires_control_plane_pages(self):
        self.pd.script = [(400, {"status": "invalid event data",
                                 "message": "bad payload"}, {})]
        sec = ScriptedHTTP()
        self.addCleanup(sec.close)
        obid = self._enqueue()
        fw = DurableForwarder(
            self.log, self._config(),
            secondary=WebhookSecondary(self._secondary_cfg(sec.url)))
        try:
            fw.run_once_sync()
        finally:
            fw._conn.close()
        row = self._row(obid)
        self.assertEqual(row["status"], "dead_letter")
        failed = self._receipts("forward_failed")
        self.assertEqual(failed[0]["error_class"], "payload_formation_error")
        # The page fired via the secondary with the annotation…
        self.assertEqual(len(sec.requests), 1)
        page = sec.requests[0]["json"]
        self.assertTrue(page["sentinel_secondary"])
        self.assertIn("payload_formation_error", page["annotations"])
        self.assertEqual(page["summary"], "boom")  # same content, other path
        confirmed = [b for b in self._receipts("forward_confirmed")
                     if b["channel"] == "secondary"]
        self.assertEqual(len(confirmed), 1)
        # …and a control-plane page went to the engineering on-call.
        cp = [r for r in self.log.undelivered_outbox_rows()
              if r["priority"] == 1]
        self.assertTrue(cp)

    def test_secondary_fires_at_X_and_primary_retries_continue(self):
        # THE PRE-MORTEM RULE (design §8): secondary fire NEVER stops
        # primary retries. Any implementation that cancels primary retries
        # on secondary-fire is a P0 defect.
        self.pd.script = [(500, {}, {}), (500, {}, {})]
        sec = ScriptedHTTP()
        self.addCleanup(sec.close)
        obid = self._enqueue()
        fw = DurableForwarder(
            self.log, self._config(secondary_fire_after_s=0.3),
            secondary=WebhookSecondary(self._secondary_cfg(sec.url)))
        try:
            fw.run_once_sync()          # attempt 1 → 500, requeued
            time.sleep(0.45)            # past X=0.3s, attempt 2 due
            fw.run_once_sync()          # attempt 2 → 500; secondary fires
            row = self._row(obid)
            self.assertEqual(row["status"], "queued")  # NOT cancelled
            self.assertIsNotNone(row["secondary_fired_at"])
            self.assertEqual(len(sec.requests), 1)
            # PD recovers; the primary retry still goes out and dedups clean.
            self.pd.script = []
            time.sleep(0.15)
            fw.run_once_sync()          # attempt 3 → 202
        finally:
            fw._conn.close()
        row = self._row(obid)
        self.assertEqual(row["status"], "delivered")
        self.assertIsNotNone(row["secondary_fired_at"])  # timestamp kept
        self.assertEqual(self.pd.incident_count, 1)     # exactly one alert
        self.assertEqual(len(self.pd.requests), 3)

    def test_wakefulness_attestation_is_required(self):
        for bad in ("", "  ", "TBD", "sms delivery"):
            with self.assertRaises(SecondaryConfigError, msg=bad):
                SecondaryConfig(provider="x", destination="http://x",
                                wakefulness=bad).validate()
        # A real sentence passes.
        SecondaryConfig(
            provider="twilio", destination="+15551234567",
            wakefulness="SMS via Twilio — does not bypass DND; not "
                        "equivalent to a PD voice call").validate()

    def test_secondary_must_be_vendor_independent(self):
        with self.assertRaises(SecondaryConfigError):
            SecondaryConfig(provider="pagerduty",
                            destination="http://x",
                            wakefulness="real sentence here").validate()

    def test_cutover_gate_refuses_undrilled_secondary(self):
        drills = DrillTracker(None, clock=FakeClock())
        # No secondary at all in stage-2c → refuse.
        with self.assertRaises(SecondaryNotReady):
            require_drilled_secondary("stage-2c", None, drills)
        # Configured but never drilled → refuse.
        cfg = SecondaryConfig(provider="x", destination="http://x",
                              wakefulness="real sentence here")
        with self.assertRaises(SecondaryNotReady):
            require_drilled_secondary("stage-2c", cfg, drills)
        # Shadow mode: no gate.
        require_drilled_secondary("shadow", None, drills)
        # Drilled 31 days ago → stale → refuse.
        drills2 = DrillTracker(os.path.join(self.tmp.name, "drills.jsonl"),
                               clock=FakeClock())
        did = drills2.start_drill()
        self.assertTrue(drills2.ack_drill(did))
        drills2.clock.advance(31 * 86400)
        with self.assertRaises(SecondaryNotReady):
            require_drilled_secondary("stage-2c", cfg, drills2)
        # Fresh drill → pass.
        drills3 = DrillTracker(os.path.join(self.tmp.name, "d3.jsonl"),
                               clock=FakeClock())
        did = drills3.start_drill()
        self.assertTrue(drills3.ack_drill(did))
        require_drilled_secondary("stage-2c", cfg, drills3,
                                  now_ts=drills3.clock())

    def test_drill_passes_only_on_human_ack_in_window(self):
        clock = FakeClock()
        drills = DrillTracker(os.path.join(self.tmp.name, "dr.jsonl"),
                              clock=clock)
        did = drills.start_drill()
        clock.advance(599)
        self.assertTrue(drills.ack_drill(did))
        did2 = drills.start_drill()
        clock.advance(601)
        self.assertFalse(drills.ack_drill(did2))  # too late — theater denied
        expired = drills.expire_drills()
        self.assertIn(did2, expired)
        self.assertNotIn(did, expired)

    def test_missed_drill_is_a_paged_slo_breach(self):
        clock = FakeClock()
        drill_path = os.path.join(self.tmp.name, "dr2.jsonl")
        drills = DrillTracker(drill_path, clock=clock)
        sec = ScriptedHTTP()
        self.addCleanup(sec.close)
        fw = DurableForwarder(
            self.log, self._config(),
            secondary=WebhookSecondary(self._secondary_cfg(sec.url)),
            drills=drills)
        try:
            did = fw.run_secondary_drill()
            self.assertEqual(len(sec.requests), 1)  # the drill was delivered
            clock.advance(601)  # nobody acked
            expired = fw._drill_check()
            self.assertIn(did, expired)
            cp = [r for r in self.log.undelivered_outbox_rows()
                  if r["fingerprint"] == "control-plane"]
            kinds = [json.loads(r["payload_frozen"])["kind"] for r in cp]
            self.assertIn("secondary_drill_missed", kinds)
        finally:
            fw._conn.close()


# ===========================================================================
# 429, max age, pinning, priority


class TestPolicy(ForwarderTestBase):
    def test_429_honors_retry_after_and_floor(self):
        self.pd.script = [(429, {"status": "rate limited"},
                           {"Retry-After": "120"})]
        obid = self._enqueue()
        fw = DurableForwarder(self.log, self._config())
        try:
            fw.run_once_sync()
        finally:
            fw._conn.close()
        row = self._row(obid)
        self.assertEqual(row["status"], "queued")
        wait = _iso_to_ts(row["next_attempt_at"]) - time.time()
        self.assertGreaterEqual(wait, 119)  # Retry-After honored
        # Without Retry-After: the 60s floor applies.
        self.pd.script = [(429, {"status": "rate limited"}, {})]
        obid2 = self._enqueue(alert_id="a2", episode="ep2",
                              dedup_key="sentinel/test/fp2/0042")
        fw2 = DurableForwarder(self.log, self._config())
        try:
            fw2.run_once_sync()
        finally:
            fw2._conn.close()
        row2 = self._row(obid2)
        wait2 = _iso_to_ts(row2["next_attempt_at"]) - time.time()
        self.assertGreaterEqual(wait2, 59)

    def test_max_age_enforced_at_schedule_time(self):
        # A retry that could never run before the deadline dead-letters now.
        self.pd.script = [(500, {}, {})]
        obid = self._enqueue(
            max_age_at=_ts_to_iso(time.time() + 1))  # 1s of max age left
        fw = DurableForwarder(self.log, self._config(backoff_s=(10.0,)))
        try:
            fw.run_once_sync()
        finally:
            fw._conn.close()
        row = self._row(obid)
        self.assertEqual(row["status"], "dead_letter")
        failed = self._receipts("forward_failed")
        self.assertEqual(failed[0]["error_class"], "max_age_exceeded")
        # The morgue has an alarm on BOTH max-age paths: the schedule-time
        # branch must page the control plane exactly like the sweep does
        # (engine P1, RFC quality-dead-letter-alarm).
        cp = [r for r in self.log.undelivered_outbox_rows()
              if r["priority"] == 1]
        self.assertTrue(cp, "schedule-time max-age dead_letter must page "
                            "the control plane")

    def test_max_age_sweep_dead_letters_loudly(self):
        obid = self._enqueue(
            max_age_at=_ts_to_iso(time.time() + 3600))  # 1h max age
        fw = DurableForwarder(self.log, self._config())
        try:
            # Sweep with "now" 2h in the future: past max age, past created.
            swept = fw._max_age_sweep(_ts_to_iso(time.time() + 7200))
        finally:
            fw._conn.close()
        self.assertEqual(swept, 1)
        self.assertEqual(self._row(obid)["status"], "dead_letter")
        cp = [r for r in self.log.undelivered_outbox_rows()
              if r["priority"] == 1]
        self.assertTrue(cp, "dead_letter must page the control plane")

    def test_control_plane_rows_are_immortal_to_the_sweep(self):
        # The event-log lane's control-plane enqueue sets max_age_at ==
        # created_at; the sweep must never kill those rows (see module
        # docstring — flagged cross-lane).
        fw = DurableForwarder(self.log, self._config())
        try:
            obid = fw.enqueue_control_plane_page(
                kind="test", summary="immortal?", detail={})
            row = self._row(obid)
            self.assertLessEqual(row["max_age_at"], row["created_at"])
            swept = fw._max_age_sweep(_ts_to_iso(time.time() + 10**7))
            self.assertEqual(swept, 0)
            self.assertEqual(self._row(obid)["status"], "queued")
        finally:
            fw._conn.close()

    def test_routing_key_pinned_across_rotation(self):
        self.pd.script = [(500, {}, {})]  # then the default 202 path
        obid = self._enqueue()
        fw = DurableForwarder(self.log, self._config())
        try:
            fw.run_once_sync()  # attempt 1 with KEY_A → 500
            time.sleep(0.15)
            os.environ[TEST_ENV] = "ROTATED-key-BBB"  # rotation mid-flight
            fw.run_once_sync()  # attempt 2 → 202
        finally:
            fw._conn.close()
        self.assertEqual(len(self.pd.requests), 2)
        keys = [r["json"]["routing_key"] for r in self.pd.requests]
        # Pinned at first attempt (design §4.3): the retry uses KEY_A even
        # though the vault now holds KEY_B. (Rotation runbook: drain first.)
        self.assertEqual(keys, ["test-routing-key-abc"] * 2)
        self.assertEqual(self._row(obid)["status"], "delivered")

    def test_priority_control_plane_first(self):
        normal = self._enqueue(alert_id="n1", episode="epn",
                               dedup_key="sentinel/test/fpn/0001")
        fw = DurableForwarder(self.log, self._config())
        try:
            cp = fw.enqueue_control_plane_page(
                kind="test", summary="s", detail={})
            claimed = fw.claim_due_rows(utcnow_iso())
            self.assertEqual([r["outbox_id"] for r in claimed], [cp, normal])
        finally:
            fw._conn.close()

    def test_claims_are_atomic_across_two_forwarders(self):
        obids = {self._enqueue(alert_id=f"a{i}", episode=f"ep{i}",
                               dedup_key=f"sentinel/test/fp{i}/0001")
                 for i in range(6)}
        fw1 = DurableForwarder(self.log, self._config())
        log_b = EventLog(self.db)
        fw2 = DurableForwarder(log_b, self._config())
        got = []
        try:
            def grab(fw):
                got.extend(r["outbox_id"]
                           for r in fw.claim_due_rows(utcnow_iso(), limit=10))
            t1 = threading.Thread(target=grab, args=(fw1,))
            t2 = threading.Thread(target=grab, args=(fw2,))
            t1.start(); t2.start(); t1.join(); t2.join()
        finally:
            fw1._conn.close(); fw2._conn.close()
        self.assertEqual(len(got), 6)
        self.assertEqual(set(got), obids)  # no row claimed twice, none lost


# ===========================================================================
# threading lifecycle + config


class TestLifecycle(ForwarderTestBase):
    def test_start_drain_stop(self):
        for i in range(5):
            self._enqueue(alert_id=f"a{i}", episode=f"ep{i}",
                          dedup_key=f"sentinel/test/fp{i}/0001")
        fw = DurableForwarder(self.log, self._config(workers=2, poll_s=0.05))
        fw.start()
        try:
            fw.drain(timeout_s=15)
            # Let the scheduler notice the now-empty queue, then stop.
            time.sleep(0.2)
        finally:
            fw.stop()
        rows = self.log.undelivered_outbox_rows(limit=100)
        self.assertEqual(rows, [])
        self.assertEqual(self.pd.incident_count, 5)
        self.assertEqual(fw.metrics["confirmed"], 5)

    def test_start_refuses_undrilled_secondary_in_stage_2c(self):
        cfg = self._config(stage="stage-2c",
                           secondary=SecondaryConfig(
                               provider="x", destination="http://x",
                               wakefulness="real sentence here"))
        fw = DurableForwarder(self.log, cfg)
        try:
            with self.assertRaises(SecondaryNotReady):
                fw.start()
        finally:
            try:
                fw._conn.close()
            except Exception:
                pass

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            ForwarderConfig(workers=0).validate()
        with self.assertRaises(ValueError):
            ForwarderConfig(stage="nope").validate()
        with self.assertRaises(SecondaryConfigError):
            ForwarderConfig(secondary=SecondaryConfig(
                wakefulness="")).validate()

    def test_memory_db_is_rejected_loudly(self):
        log = EventLog(":memory:")
        with self.assertRaises(ValueError):
            DurableForwarder(log, self._config())

    def test_resolve_routing_key_never_leaks(self):
        with self.assertRaises(SecretMissing) as ctx:
            resolve_routing_key("secret:pd/missing")
        self.assertNotIn("super", str(ctx.exception))
        os.environ["SENTINEL_SECRET_PD_MISSING"] = "shh"
        try:
            self.assertEqual(resolve_routing_key("secret:pd/missing"), "shh")
        finally:
            del os.environ["SENTINEL_SECRET_PD_MISSING"]
        # Bare env-var-name refs work too.
        os.environ["MY_PD_KEY"] = "k"
        try:
            self.assertEqual(resolve_routing_key("MY_PD_KEY"), "k")
        finally:
            del os.environ["MY_PD_KEY"]


if __name__ == "__main__":
    unittest.main()
