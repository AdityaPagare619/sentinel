"""Tests for sentinel.correlator (§3.5)."""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import hashlib
import unittest

from sentinel.correlator import (Correlator, fingerprint_for, fingerprint_of,
                                   legacy_fingerprint_for)
from sentinel.models import Disposition

from tests.helpers import FakeClock, make_alert


class TestFingerprint(unittest.TestCase):
    def test_format_and_determinism(self):
        fp = fingerprint_for("web", "http_5xx", "critical", "us-east",
                             env="prod", cluster="us-east-1a")
        self.assertEqual(len(fp), 16)
        int(fp, 16)  # hex
        expected = hashlib.sha256(
            b"web|http_5xx|critical|us-east|prod|us-east-1a").hexdigest()[:16]
        self.assertEqual(fp, expected)
        self.assertEqual(fingerprint_for("web", "http_5xx", "critical",
                                         "us-east", env="prod",
                                         cluster="us-east-1a"), fp)

    def test_env_cluster_are_in_the_hash(self):
        # ADR-017: the staging→prod collision dies here. Same
        # service/check/severity/region, different env => different
        # fingerprint. Same for cluster.
        base = dict(service="web", check="http_5xx", severity_in="critical",
                    region="us-east")
        prod = fingerprint_for(env="prod", cluster="us-east-1a", **base)
        staging = fingerprint_for(env="staging", cluster="us-east-1a", **base)
        other_cluster = fingerprint_for(env="prod", cluster="us-east-1b",
                                        **base)
        self.assertNotEqual(prod, staging)
        self.assertNotEqual(prod, other_cluster)
        self.assertNotEqual(staging, other_cluster)

    def test_v2_differs_from_legacy_v1(self):
        v2 = fingerprint_for("web", "http_5xx", "critical", "us-east",
                             env="prod", cluster="us-east-1a")
        v1 = legacy_fingerprint_for("web", "http_5xx", "critical", "us-east")
        self.assertNotEqual(v2, v1)
        self.assertEqual(v1, hashlib.sha256(
            b"web|http_5xx|critical|us-east").hexdigest()[:16])

    def test_env_required_keyword_only(self):
        # No call site may silently mint an env-blind hash.
        with self.assertRaises(TypeError):
            fingerprint_for("web", "http_5xx", "critical", "us-east")

    def test_labels_drive_fingerprint(self):
        a = make_alert(region="eu-west",
                       labels={"env": "staging", "cluster": "eu-west-1a"})
        self.assertEqual(fingerprint_of(a),
                         fingerprint_for("web", "http_5xx", "critical",
                                         "eu-west", env="staging",
                                         cluster="eu-west-1a"))

    def test_distinct_inputs_distinct_fingerprints(self):
        fps = {fingerprint_for(f"svc{i}", "chk", "critical", "r",
                               env="prod", cluster="c") for i in range(50)}
        self.assertEqual(len(fps), 50)


class TestDedup(unittest.TestCase):
    def test_first_ingest_is_new(self):
        c = Correlator(clock=FakeClock())
        r = c.ingest(make_alert())
        self.assertEqual(r.kind, "new")

    def test_duplicate_inside_window(self):
        c = Correlator(window_s=300, clock=FakeClock())
        c.ingest(make_alert())
        r = c.ingest(make_alert(alert_id="a2"))
        self.assertEqual(r.kind, "duplicate")

    def test_duplicate_expires_after_window(self):
        clock = FakeClock()
        c = Correlator(window_s=300, clock=clock)
        c.ingest(make_alert())
        clock.advance(301)
        r = c.ingest(make_alert(alert_id="a3"))
        self.assertEqual(r.kind, "new")

    def test_duplicate_inherits_prior_disposition(self):
        c = Correlator(clock=FakeClock())
        a = make_alert()
        c.ingest(a)
        prior = Disposition(action="page_now", reason="threshold", team="platform",
                            confidence=0.9, latency_ms=1.0)
        c.note_disposition(a.fingerprint, prior)
        r = c.ingest(make_alert(alert_id="a2"))
        self.assertEqual(r.kind, "duplicate")
        self.assertIsNotNone(r.prior)
        self.assertEqual(r.prior.action, "page_now")

    def test_different_fingerprints_are_not_duplicates(self):
        c = Correlator(clock=FakeClock())
        c.ingest(make_alert())
        r = c.ingest(make_alert(service="db", alert_id="b1"))
        self.assertEqual(r.kind, "new")


class TestStorm(unittest.TestCase):
    def test_storm_declared_over_threshold(self):
        clock = FakeClock()
        c = Correlator(storm_fingerprints=5, storm_window_s=60, clock=clock)
        kinds = []
        for i in range(7):
            r = c.ingest(make_alert(service=f"svc{i}", alert_id=f"s{i}"))
            kinds.append(r.kind)
            clock.advance(1)
        self.assertEqual(kinds[:5], ["new"] * 5)
        self.assertEqual(kinds[5], "storm")
        self.assertTrue(kinds[5] == "storm")
        # The declaring ingest carries the counts and the declared flag.
        decl = None
        c2 = Correlator(storm_fingerprints=5, storm_window_s=60, clock=FakeClock())
        for i in range(6):
            decl = c2.ingest(make_alert(service=f"svc{i}", alert_id=f"s{i}"))
        self.assertTrue(decl.storm_declared)
        self.assertEqual(sum(decl.storm_counts.values()), 6)
        self.assertEqual(decl.storm_counts.get("svc0"), 1)

    def test_storm_continuation_folds_in(self):
        clock = FakeClock()
        c = Correlator(storm_fingerprints=3, storm_window_s=60, clock=clock)
        for i in range(4):
            r = c.ingest(make_alert(service=f"svc{i}", alert_id=f"s{i}"))
            clock.advance(1)
        self.assertTrue(r.storm_declared)
        r2 = c.ingest(make_alert(service="svcX", alert_id="sx"))
        self.assertEqual(r2.kind, "storm")
        self.assertFalse(r2.storm_declared)

    def test_storm_window_expires(self):
        clock = FakeClock()
        c = Correlator(storm_fingerprints=3, storm_window_s=60, clock=clock)
        for i in range(4):
            c.ingest(make_alert(service=f"svc{i}", alert_id=f"s{i}"))
        clock.advance(61)
        r = c.ingest(make_alert(service="svcZ", alert_id="sz"))
        self.assertEqual(r.kind, "new")

    def test_no_storm_below_threshold(self):
        c = Correlator(storm_fingerprints=20, storm_window_s=60, clock=FakeClock())
        for i in range(20):
            r = c.ingest(make_alert(service=f"svc{i}", alert_id=f"s{i}"))
        self.assertEqual(r.kind, "new")


class TestChangeWindow(unittest.TestCase):
    def _windows(self):
        return [{"service": "web",
                 "start": "2026-10-02T11:00:00+00:00",
                 "end": "2026-10-02T13:00:00+00:00"}]

    def test_change_window_match(self):
        c = Correlator(change_windows=self._windows(), clock=FakeClock())
        r = c.ingest(make_alert())  # received_at 12:00 UTC, service web
        self.assertEqual(r.kind, "change_window")

    def test_change_window_service_mismatch(self):
        c = Correlator(change_windows=self._windows(), clock=FakeClock())
        r = c.ingest(make_alert(service="db", alert_id="b1"))
        self.assertEqual(r.kind, "new")

    def test_change_window_time_mismatch(self):
        windows = [{"service": "*",
                    "start": "2026-10-02T14:00:00+00:00",
                    "end": "2026-10-02T15:00:00+00:00"}]
        c = Correlator(change_windows=windows, clock=FakeClock())
        r = c.ingest(make_alert())
        self.assertEqual(r.kind, "new")

    def test_change_window_wildcard_service(self):
        windows = [{"service": "*",
                    "start": "2026-10-02T11:00:00+00:00",
                    "end": "2026-10-02T13:00:00+00:00"}]
        c = Correlator(change_windows=windows, clock=FakeClock())
        r = c.ingest(make_alert(service="anything", alert_id="w1"))
        self.assertEqual(r.kind, "change_window")

    def test_bad_change_window_config_fails_open(self):
        windows = [{"service": "web", "start": "not-a-time", "end": "also-bad"}]
        c = Correlator(change_windows=windows, clock=FakeClock())
        r = c.ingest(make_alert())
        self.assertEqual(r.kind, "new")


if __name__ == "__main__":
    unittest.main()
