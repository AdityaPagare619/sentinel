"""Tests for the faithful simulated services (production-stages lane).

FaithfulJev: calibrated latencies, fault injection, trace records.
Faithful FakePDSink: delivery state machine, dedup semantics, faults.
Fast: uses tiny synthetic latency samples (no real sleeps beyond ~ms).
"""
import sys
import os
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sim"))

from sentinel.client import (
    FaithfulJev, FaultProfile, LatencyModel, DecisionResponse, Answer,
    JevError, JevRateLimited, JevTimeout, _state_fingerprint,
)
import sim_runner


def _scripted():
    state = {"alert": "cpu high", "service": "web"}
    fp = _state_fingerprint(state)
    script = {fp: DecisionResponse(
        model="x",
        answers={"d": Answer(qid="d", qtype="choice", choice="page",
                             noul=None, probabilities={"page": 0.9},
                             confidence=0.9)},
        input_tokens=100)}
    return state, script


class TestLatencyModel(unittest.TestCase):
    def test_campaign_distribution_shape(self):
        m = LatencyModel.jev_campaign(seed=1)
        d = m.describe()
        self.assertEqual(d["n"], 109)
        self.assertAlmostEqual(d["p50_ms"], 815.6, delta=5)
        self.assertIn("2026-10-03", d["measured_on"])
        self.assertIn("latency-report", d["source"])

    def test_sampling_respects_seed(self):
        a = LatencyModel([10.0, 20.0, 30.0], seed=7)
        b = LatencyModel([10.0, 20.0, 30.0], seed=7)
        self.assertEqual([a.sample_ms() for _ in range(5)],
                         [b.sample_ms() for _ in range(5)])


class TestFaithfulJev(unittest.TestCase):
    def test_decide_sleeps_sampled_latency_and_traces(self):
        state, script = _scripted()
        j = FaithfulJev(script, seed=3,
                        latency=LatencyModel([5.0], seed=3),
                        faults=FaultProfile(transient_520_rate=0.0))
        t0 = time.monotonic()
        r = j.decide(state, {})
        dt_ms = (time.monotonic() - t0) * 1000
        self.assertGreaterEqual(dt_ms, 4.0)  # actually slept
        self.assertEqual(r.answers["d"].choice, "page")
        self.assertEqual(len(j.trace), 1)
        self.assertEqual(j.trace[0]["fault"], None)
        self.assertEqual(j.trace[0]["disposition"], "page")

    def test_honest_model_id(self):
        state, script = _scripted()
        j = FaithfulJev(script, seed=3,
                        latency=LatencyModel([1.0], seed=3),
                        faults=FaultProfile(transient_520_rate=0.0))
        self.assertEqual(j.calibration()["model_id"], "jev-faithful-sim-1.0")
        self.assertNotIn("jev-1", j.calibration()["model_id"])

    def test_fault_injection_raises_real_types(self):
        state, script = _scripted()
        for rate_kw, exc in [(dict(transient_520_rate=1.0), JevError),
                            (dict(rate_limit_429_rate=1.0), JevRateLimited),
                            (dict(timeout_rate=1.0), JevTimeout)]:
            j = FaithfulJev(script, seed=3,
                            latency=LatencyModel([1.0], seed=3),
                            faults=FaultProfile(**rate_kw))
            with self.assertRaises(exc):
                j.decide(state, {})
            self.assertIsNotNone(j.trace[0]["fault"])

    def test_unscripted_state_still_raises(self):
        _, script = _scripted()
        j = FaithfulJev(script, seed=3,
                        latency=LatencyModel([1.0], seed=3),
                        faults=FaultProfile(transient_520_rate=0.0))
        with self.assertRaises(JevError):
            j.decide({"alert": "unknown"}, {})


class TestFaithfulFakePD(unittest.TestCase):
    def test_simple_default_unchanged(self):
        sink = sim_runner.FakePDSink()
        sink.start()
        try:
            import urllib.request, json
            body = json.dumps({"dedup_key": "k1", "event_action": "trigger"}).encode()
            req = urllib.request.Request(sink.url, data=body, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.getcode(), 202)
            self.assertEqual(len(sink.pages), 1)
            self.assertEqual(sink.deliveries, [])
        finally:
            sink.stop()

    def test_faithful_delivery_lifecycle(self):
        sink = sim_runner.FakePDSink(faithful=True, seed=11)
        sink.start()
        try:
            import urllib.request, json
            body = json.dumps({"dedup_key": "dk-1", "event_action": "trigger"}).encode()
            t0 = time.monotonic()
            req = urllib.request.Request(sink.url, data=body, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                dt_ms = (time.monotonic() - t0) * 1000
                self.assertEqual(resp.getcode(), 202)
            self.assertGreaterEqual(dt_ms, 70)  # ack latency is real
            # dedup: same key appends, no new incident
            req2 = urllib.request.Request(sink.url, data=body, method="POST")
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                self.assertEqual(resp2.getcode(), 202)
            time.sleep(2.0)  # let background delivery complete
            ds = sink.deliveries
            self.assertEqual(len(ds), 2)
            self.assertTrue(ds[1]["deduped"])
            states = [s for s, _ in ds[0]["transitions"]]
            self.assertEqual(states, ["RECEIVED", "ACCEPTED", "QUEUED", "DELIVERED"])
        finally:
            sink.stop()

    def test_faithful_429_fault(self):
        sink = sim_runner.FakePDSink(
            faithful=True, seed=11,
            fault_profile={"rate_limit_429": 1.0})
        sink.start()
        try:
            import urllib.request, json, urllib.error
            body = json.dumps({"dedup_key": "dk-9"}).encode()
            req = urllib.request.Request(sink.url, data=body, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(cm.exception.code, 429)
            ds = sink.deliveries
            self.assertEqual(ds[0]["fault"], "rate_limit_429")
            self.assertIn("FAILED", [s for s, _ in ds[0]["transitions"]])
        finally:
            sink.stop()


if __name__ == "__main__":
    unittest.main()
