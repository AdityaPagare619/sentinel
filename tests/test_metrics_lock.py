"""Metrics-lock regression tests (audit engine P3, RFC engine-metrics-lock).

The bug class: ``metrics[...] += 1`` is a read-modify-write — NOT atomic
under threads sharing one Pipeline/Forwarder (ThreadingHTTPServer +
worker pools). Same class as the fail-open ladder race 3fac416 fixed.

Proven real (deterministic interleave demo): two threads splitting the
read and the write lose an increment. The fix: _metric_inc()/bump_metric()
under an RLock (leaf-lock discipline). These tests prove (a) the hammer
passes exactly on the fixed code, and (b) the raw idiom never returns
(source scan — the same "prove the class is gone" pattern as the AST
purity test).
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import re
import tempfile
import threading
import unittest

from sentinel.eventlog import EventLog
from sentinel.forwarder import DurableForwarder, Forwarder, ForwarderConfig
from sentinel.receiver import Pipeline, ReceiverConfig
from sentinel.correlator import Correlator
from sentinel.safety import KillSwitch

from tests.helpers import make_alert
from tests.test_kill_topology import _disp

N_THREADS = 32
N_ITERS = 1000


def _hammer(fn):
    ts = [threading.Thread(target=lambda: [fn() for _ in range(N_ITERS)])
          for _ in range(N_THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()


class TestMetricsHammer(unittest.TestCase):
    def test_legacy_forwarder_metric_inc_exact(self):
        fw = Forwarder(pd_events_url="http://127.0.0.1:1/",
                       kill_switch=KillSwitch())
        _hammer(lambda: fw._metric_inc("forwarded"))
        self.assertEqual(fw.metrics["forwarded"], N_THREADS * N_ITERS)

    def test_legacy_forwarder_forward_killed_exact_under_threads(self):
        # The receiver's real sharing pattern: N handler threads, one
        # Forwarder, kill engaged -> every forward() bumps "killed".
        # Zero PD traffic (absorbed before the wire).
        ks = KillSwitch()
        ks.engage(actor_id="hammer")
        fw = Forwarder(pd_events_url="http://127.0.0.1:1/", kill_switch=ks)
        alert = make_alert(alert_id="hammer-1")
        disp = _disp("page_now")
        _hammer(lambda: fw.forward(alert, disp))
        snap = fw._metrics_snapshot()
        self.assertEqual(snap["killed"], N_THREADS * N_ITERS)
        self.assertEqual(snap["forwarded"], 0)

    def test_durable_forwarder_metric_inc_exact(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = EventLog(os.path.join(tmp.name, "t.db"))
        self.addCleanup(log.close)
        fw = DurableForwarder(
            log,
            ForwarderConfig(env="test", pd_endpoint="http://127.0.0.1:1/",
                            stage="shadow"),
            kill_switch=KillSwitch())
        _hammer(lambda: fw._metric_inc("claimed"))
        self.assertEqual(fw.metrics["claimed"], N_THREADS * N_ITERS)

    def test_pipeline_metric_inc_exact(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fw = Forwarder(pd_events_url="http://127.0.0.1:1/")
        pipeline = Pipeline(Correlator(), None, fw, None,
                            config=ReceiverConfig(), policy=None)
        _hammer(lambda: pipeline._metric_inc("received"))
        self.assertEqual(pipeline.metrics["received"], N_THREADS * N_ITERS)

    def test_eventlog_bump_metric_exact(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = EventLog(os.path.join(tmp.name, "t.db"))
        self.addCleanup(log.close)
        _hammer(lambda: log.bump_metric("events_written"))
        self.assertEqual(log.metrics_snapshot()["events_written"],
                         N_THREADS * N_ITERS)

    def test_snapshot_no_torn_reads(self):
        fw = Forwarder(pd_events_url="http://127.0.0.1:1/")
        stop = threading.Event()

        def writer():
            while not stop.is_set():
                fw._metric_inc("forwarded")

        def reader(results):
            while not stop.is_set():
                s = fw._metrics_snapshot()
                results.append(isinstance(s, dict))

        results = []
        wt = threading.Thread(target=writer)
        rt = threading.Thread(target=reader, args=(results,))
        wt.start()
        rt.start()
        wt.join(timeout=2.0)
        stop.set()
        rt.join(timeout=2.0)
        self.assertTrue(results)  # reads happened
        self.assertTrue(all(results))


class TestMetricsIdiomScan(unittest.TestCase):
    """No raw read-modify-write on shared counters may return.

    Scans the engine files for the pre-fix idiom. The helpers themselves
    (``self.metrics[key] = self.metrics.get(key, 0) + n``) hold the lock
    and do not match.
    """
    FILES = ("forwarder.py", "receiver.py", "eventlog.py", "checkpoint.py")
    # matches: X.metrics["k"] += 1 / X.metrics["k"] = X.metrics.get(...) + 1
    # (the helper uses self.metrics[key] = ... .get(key, 0) — no literal key,
    #  and is excluded by the negative lookahead on `[key]`)
    PAT = re.compile(
        r"""\.metrics\[\s*"(?!\s*key\s*\])[^"\]]+"\s*\]\s*(\+=|=)""")

    def test_no_unlocked_metrics_increments(self):
        src_dir = os.path.join(_SRC, "sentinel")
        offenders = []
        for fname in self.FILES:
            path = os.path.join(src_dir, fname)
            with open(path) as fh:
                for i, line in enumerate(fh, 1):
                    if self.PAT.search(line):
                        offenders.append(f"{fname}:{i}: {line.strip()}")
        self.assertEqual(offenders, [],
                         "unlocked metrics += idiom found (use _metric_inc / "
                         "bump_metric):\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
