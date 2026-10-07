"""N-thread hammer test for the fail-open ladder's concurrency contract.

Regression cover for the engine P0 (failopen.py:244 — unsynchronized deque
under the threaded receiver). The millions-scale load test caught
``RuntimeError: deque mutated during iteration`` live; the fix (3fac416:
locks + snapshot iteration + RLock on the monitors; dcf0955: serialized
observe()/decide()/request_step() on the controller) must hold under real
thread contention. The suite never had a threading test — this is it.

Three hammers:
  1. VendorHealthMonitor: N writer threads record() while M reader threads
     iterate via timer_win_rate() — asserts no deque-mutation RuntimeError
     AND no observation loss (final n == N*K).
  2. PageRateMonitor: same pattern for record()/page_rate_per_min().
  3. FailopenController: N threads interleaving observe()/decide()/
     request_step() — asserts no exception escapes and the exact
     observation count survives (the loud crash was the symptom; silent
     dict/deque corruption in the ladder state would be worse).

All timestamps are explicit (no shared mutable clock), so the only shared
mutable state under test is the ladder's own — exactly what the locks
protect. On the pre-fix code hammer 1 raises RuntimeError with near
certainty; on the fixed code every assertion is deterministic.
"""

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import unittest

from sentinel.failopen import (
    FailopenController,
    PageRateMonitor,
    VendorHealthMonitor,
)

_BASE = 1_700_000_000.0  # fixed epoch; every timestamp explicit


class VendorHealthMonitorHammer(unittest.TestCase):
    def test_record_vs_iterate_no_mutation_no_loss(self):
        n_writers, n_readers, k = 16, 4, 250
        total = n_writers * k
        dt = 0.0005
        t_end = _BASE + total * dt
        mon = VendorHealthMonitor()
        barrier = threading.Barrier(n_writers + n_readers)

        def writer(w):
            barrier.wait()  # release all threads at once: maximum contention
            for j in range(k):
                i = w * k + j
                mon.record(unhealthy=(i % 3 == 0), now=_BASE + i * dt)

        def reader(_r):
            barrier.wait()
            for _ in range(k):
                # The pre-fix code raised "deque mutated during iteration"
                # here while writers appended/pruned. Any exception fails
                # the test via future.result() below.
                mon.timer_win_rate(window_s=3600.0, now=t_end)

        with ThreadPoolExecutor(max_workers=n_writers + n_readers) as ex:
            futs = ([ex.submit(writer, w) for w in range(n_writers)] +
                    [ex.submit(reader, r) for r in range(n_readers)])
            for f in futs:
                f.result()

        rate, n = mon.timer_win_rate(window_s=3600.0, now=t_end)
        self.assertEqual(n, total,
                         f"observation loss under contention: {n}/{total}")
        expected_bad = sum(1 for i in range(total) if i % 3 == 0)
        self.assertAlmostEqual(rate, expected_bad / total, places=9)


class PageRateMonitorHammer(unittest.TestCase):
    def test_record_vs_iterate_no_mutation_no_loss(self):
        n_writers, n_readers, k = 16, 4, 250
        total = n_writers * k
        dt = 0.0005
        t_end = _BASE + total * dt
        mon = PageRateMonitor()
        barrier = threading.Barrier(n_writers + n_readers)

        def writer(w):
            barrier.wait()
            for j in range(k):
                i = w * k + j
                mon.record(page_eligible=(i % 2 == 0), now=_BASE + i * dt)

        def reader(_r):
            barrier.wait()
            for _ in range(k):
                mon.page_rate_per_min(window_s=60.0, now=t_end)

        with ThreadPoolExecutor(max_workers=n_writers + n_readers) as ex:
            futs = ([ex.submit(writer, w) for w in range(n_writers)] +
                    [ex.submit(reader, r) for r in range(n_readers)])
            for f in futs:
                f.result()

        rate = mon.page_rate_per_min(window_s=60.0, now=t_end)
        expected = sum(1 for i in range(total) if i % 2 == 0)
        # window 60s -> rate IS the count; exact in float for this magnitude.
        self.assertEqual(rate, float(expected),
                         f"page observation loss under contention")


class _HammerAlert:
    """Minimal shape FailopenController.decide() reads."""

    def __init__(self, i: int):
        self.severity_in = "CRITICAL" if i % 5 == 0 else "warning"
        self.fingerprint = f"hammer-fp-{i % 37}"
        self.service = f"hammer-svc-{i % 11}"


class FailopenControllerHammer(unittest.TestCase):
    def test_observe_decide_request_step_concurrent(self):
        n, k = 12, 120
        total = n * k
        ctl = FailopenController()
        outcomes = ["timer_win", "answered", "unhealthy_error", "structural"]
        actions = ["page_now", "suppress", "page_business_hours"]
        barrier = threading.Barrier(n + 1)

        def worker(w):
            barrier.wait()
            for j in range(k):
                i = w * k + j
                t = _BASE + i * 0.001
                # decide() never raises by contract (degrades to passthrough
                # loudly); if the RLock serialization regresses, corruption
                # shows up as lost observations or an escaped exception.
                ctl.observe(vendor_outcome=outcomes[i % 4],
                            action=actions[i % 3], now=t)
                disp = ctl.decide(_HammerAlert(i), step=(i % 3) + 1, now=t)
                assert disp.action in (
                    "page_now", "page_business_hours", "passthrough", "folded",
                ), f"unexpected disposition {disp.action!r}"
            return True

        def escalator():
            barrier.wait()
            for s in (1, 2, 3, 1, 2):
                ctl.request_step(s, cause=f"hammer-escalation-{s}",
                                 now=_BASE + 10_000 + s)
            return True

        with ThreadPoolExecutor(max_workers=n + 1) as ex:
            futs = [ex.submit(worker, w) for w in range(n)]
            futs.append(ex.submit(escalator))
            for f in futs:
                self.assertTrue(f.result())  # re-raises any escaped exception

        # "structural" outcomes never touch the vendor: not samples.
        expected_samples = sum(
            1 for i in range(total) if outcomes[i % 4] != "structural")
        _rate, nsamples = ctl._health.timer_win_rate(1e9, now=_BASE + total)
        self.assertEqual(nsamples, expected_samples,
                         "controller lost observations under contention")
        self.assertIn(ctl.current_step, (0, 1, 2, 3))
        self.assertIsInstance(ctl.step_history, list)


if __name__ == "__main__":
    unittest.main()
