"""Tests for ADR-001 correlator craft (D10): episode lifecycle.

  - flap-reopen bumps a visible flap count, never auto-promotes;
  - P1/P2 (critical/high) bypass change-window queuing (DR-13);
  - >72h gap starts a FRESH episode, not a reopen;
  - silence never closes an episode;
  - episodes close only on operator resolve or a verified resolve signal.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import unittest

from sentinel.correlator import (CLOSE_REASONS, EPISODE_FRESHNESS_S,
                                 Correlator)
from sentinel.models import Disposition

from tests.helpers import FakeClock, make_alert


def _windows():
    return [{"service": "web",
             "start": "2026-10-02T11:00:00+00:00",
             "end": "2026-10-02T13:00:00+00:00"}]


class TestFlapReopen(unittest.TestCase):
    def test_reopen_bumps_flap_count(self):
        clock = FakeClock()
        c = Correlator(clock=clock)
        a = make_alert()
        r0 = c.ingest(a)
        self.assertEqual(r0.kind, "new")
        self.assertEqual(r0.flap_count, 0)
        self.assertEqual(r0.episode_state, "open")
        self.assertTrue(c.resolve_episode(a.fingerprint, reason="operator_resolve"))
        r1 = c.ingest(make_alert(alert_id="a2"))
        self.assertEqual(r1.kind, "reopened")
        self.assertEqual(r1.flap_count, 1)
        self.assertEqual(r1.episode_state, "open")

    def test_reopen_never_auto_promotes(self):
        # The fresh alert goes through the full gate evaluation: it must
        # NOT inherit the closed episode's disposition and must NOT arrive
        # pre-paged. kind "reopened" is not a paging decision — the gate
        # decides (its _structural path only fast-tracks duplicate /
        # change_window / storm-continuation).
        c = Correlator(clock=FakeClock())
        a = make_alert()
        c.ingest(a)
        prior = Disposition(action="page_now", reason="threshold",
                            team="platform", confidence=0.95, latency_ms=1.0)
        c.note_disposition(a.fingerprint, prior)
        c.resolve_episode(a.fingerprint, reason="verified_resolve")
        r = c.ingest(make_alert(alert_id="a2"))
        self.assertEqual(r.kind, "reopened")
        self.assertIsNone(r.prior)  # stale disposition discarded at resolve
        self.assertNotIn(r.kind, ("duplicate",))

    def test_flap_count_accumulates_across_resolves(self):
        c = Correlator(clock=FakeClock())
        a = make_alert()
        c.ingest(a)
        c.resolve_episode(a.fingerprint, reason="operator_resolve")
        r1 = c.ingest(make_alert(alert_id="a2"))
        c.resolve_episode(a.fingerprint, reason="operator_resolve")
        r2 = c.ingest(make_alert(alert_id="a3"))
        self.assertEqual((r1.flap_count, r2.flap_count), (1, 2))

    def test_reopen_inside_dedup_window_still_counts(self):
        # The most common flap pattern: re-fire seconds after the close.
        # Dedup-window short-circuit must NOT swallow the flap.
        clock = FakeClock()
        c = Correlator(window_s=300, clock=clock)
        a = make_alert()
        c.ingest(a)
        c.resolve_episode(a.fingerprint, reason="operator_resolve")
        clock.advance(30)
        r = c.ingest(make_alert(alert_id="a2"))
        self.assertEqual(r.kind, "reopened")
        self.assertEqual(r.flap_count, 1)


class TestP1P2CarveOut(unittest.TestCase):
    def test_p1_bypasses_change_window(self):
        # DR-13: a critical alert inside a change window must NOT be
        # queued to business hours — it pages immediately.
        c = Correlator(change_windows=_windows(), clock=FakeClock())
        r = c.ingest(make_alert(severity="critical"))  # 12:00 UTC in window
        self.assertEqual(r.kind, "new")
        self.assertNotEqual(r.kind, "change_window")

    def test_p2_bypasses_change_window(self):
        c = Correlator(change_windows=_windows(), clock=FakeClock())
        r = c.ingest(make_alert(severity="high", alert_id="h1"))
        self.assertEqual(r.kind, "new")

    def test_p3_still_queued(self):
        c = Correlator(change_windows=_windows(), clock=FakeClock())
        r = c.ingest(make_alert(severity="warning", alert_id="w1"))
        self.assertEqual(r.kind, "change_window")

    def test_p1_duplicate_inside_window_not_queued(self):
        clock = FakeClock()
        c = Correlator(change_windows=_windows(), clock=clock)
        c.ingest(make_alert(severity="critical"))
        r = c.ingest(make_alert(severity="critical", alert_id="a2"))
        self.assertEqual(r.kind, "duplicate")  # never change_window

    def test_severity_label_mapping_is_case_insensitive(self):
        c = Correlator(change_windows=_windows(), clock=FakeClock())
        for sev in ("CRITICAL", " High ", "p1", "P2"):
            r = c.ingest(make_alert(severity=sev,
                                   alert_id=f"x-{sev.strip().lower()}"))
            self.assertEqual(r.kind, "new", f"severity {sev!r} must bypass")


class TestEpisodeFreshness(unittest.TestCase):
    def test_gap_over_72h_starts_fresh_episode(self):
        clock = FakeClock()
        c = Correlator(clock=clock)
        a = make_alert()
        c.ingest(a)
        c.resolve_episode(a.fingerprint, reason="operator_resolve")
        clock.advance(EPISODE_FRESHNESS_S + 1)
        r = c.ingest(make_alert(alert_id="a2"))
        # Fresh, not a reopen: history is stale, flap count resets.
        self.assertEqual(r.kind, "new")
        self.assertEqual(r.flap_count, 0)

    def test_gap_under_72h_reopens_closed_episode(self):
        clock = FakeClock()
        c = Correlator(clock=clock)
        a = make_alert()
        c.ingest(a)
        c.resolve_episode(a.fingerprint, reason="operator_resolve")
        clock.advance(EPISODE_FRESHNESS_S - 1)
        r = c.ingest(make_alert(alert_id="a2"))
        self.assertEqual(r.kind, "reopened")
        self.assertEqual(r.flap_count, 1)

    def test_gap_over_72h_on_open_episode_is_fresh_too(self):
        # Even an open episode loses identity after 72h of silence: the
        # operational context is gone, treat the re-fire as brand new.
        clock = FakeClock()
        c = Correlator(clock=clock)
        c.ingest(make_alert())
        clock.advance(EPISODE_FRESHNESS_S + 1)
        r = c.ingest(make_alert(alert_id="a2"))
        self.assertEqual(r.kind, "new")
        self.assertEqual(r.flap_count, 0)


class TestNeverAutoClose(unittest.TestCase):
    def test_silence_never_closes(self):
        # Long silence with no resolve: the episode stays OPEN. A re-fire
        # is a "new" observation on the open episode, never a reopen.
        clock = FakeClock()
        c = Correlator(clock=clock)
        a = make_alert()
        c.ingest(a)
        clock.advance(10 * 3600)  # 10h of silence
        ep = c.get_episode(a.fingerprint)
        self.assertIsNotNone(ep)
        self.assertEqual(ep.state, "open")
        r = c.ingest(make_alert(alert_id="a2"))
        self.assertEqual(r.kind, "new")
        self.assertEqual(r.flap_count, 0)
        self.assertEqual(r.episode_state, "open")

    def test_prune_never_flips_state(self):
        # _prune runs on every ingest; it must never close an episode.
        clock = FakeClock()
        c = Correlator(clock=clock)
        a = make_alert()
        c.ingest(a)
        for _ in range(5):
            clock.advance(3600)
            c.ingest(make_alert(service="other", alert_id="noise"))
        ep = c.get_episode(a.fingerprint)
        self.assertEqual(ep.state, "open")

    def test_close_only_on_resolve_not_silence(self):
        clock = FakeClock()
        c = Correlator(clock=clock)
        a = make_alert()
        c.ingest(a)
        clock.advance(71 * 3600)  # just under the freshness bound
        self.assertEqual(c.get_episode(a.fingerprint).state, "open")
        self.assertTrue(
            c.resolve_episode(a.fingerprint, reason="operator_resolve"))
        self.assertEqual(c.get_episode(a.fingerprint).state, "closed")


class TestCloseConditions(unittest.TestCase):
    def test_close_reasons(self):
        c = Correlator(clock=FakeClock())
        a = make_alert()
        c.ingest(a)
        self.assertTrue(
            c.resolve_episode(a.fingerprint, reason="operator_resolve"))
        self.assertEqual(c.get_episode(a.fingerprint).close_reason,
                         "operator_resolve")
        b = make_alert(service="db", alert_id="b1")
        c.ingest(b)
        self.assertTrue(
            c.resolve_episode(b.fingerprint, reason="verified_resolve"))
        self.assertEqual(c.get_episode(b.fingerprint).close_reason,
                         "verified_resolve")

    def test_unknown_reason_rejected(self):
        c = Correlator(clock=FakeClock())
        a = make_alert()
        c.ingest(a)
        with self.assertRaises(ValueError):
            c.resolve_episode(a.fingerprint, reason="silence")
        with self.assertRaises(ValueError):
            c.resolve_episode(a.fingerprint, reason="")
        # Rejected close leaves the episode open.
        self.assertEqual(c.get_episode(a.fingerprint).state, "open")

    def test_resolve_unknown_or_already_closed_returns_false(self):
        c = Correlator(clock=FakeClock())
        self.assertFalse(
            c.resolve_episode("nope-nope", reason="operator_resolve"))
        a = make_alert()
        c.ingest(a)
        self.assertTrue(
            c.resolve_episode(a.fingerprint, reason="operator_resolve"))
        self.assertFalse(
            c.resolve_episode(a.fingerprint, reason="operator_resolve"))

    def test_close_reasons_are_only_two(self):
        self.assertEqual(set(CLOSE_REASONS),
                         {"operator_resolve", "verified_resolve"})

    def test_resolve_discards_stale_prior(self):
        # A duplicate arriving in the dedup window after a close must not
        # inherit the closed episode's disposition — the reopen path clears
        # it, and resolve itself clears it for late arrivals.
        c = Correlator(clock=FakeClock())
        a = make_alert()
        c.ingest(a)
        c.note_disposition(a.fingerprint,
                           Disposition(action="page_now", reason="threshold",
                                       team="platform", confidence=0.9,
                                       latency_ms=1.0))
        c.resolve_episode(a.fingerprint, reason="operator_resolve")
        r = c.ingest(make_alert(alert_id="a2"))
        self.assertEqual(r.kind, "reopened")
        self.assertIsNone(r.prior)


class TestStormReopenOrdering(unittest.TestCase):
    def test_reopen_during_active_storm_folds(self):
        # One-call-per-storm (DR-14) wins over flap visibility: during a
        # declared storm the re-fire folds into the digest; the flap
        # surfaces as a reopen once the storm clears.
        clock = FakeClock()
        c = Correlator(storm_fingerprints=2, storm_window_s=60, clock=clock)
        a = make_alert(service="web", alert_id="a1")
        c.ingest(a)
        c.resolve_episode(a.fingerprint, reason="operator_resolve")
        for i in range(3):  # declare a storm with other fingerprints
            c.ingest(make_alert(service=f"svc{i}", alert_id=f"s{i}"))
            clock.advance(1)
        r = c.ingest(make_alert(alert_id="a2"))  # the closed fingerprint
        self.assertEqual(r.kind, "storm")
        self.assertFalse(r.storm_declared)
        # Episode untouched by the fold.
        self.assertEqual(c.get_episode(a.fingerprint).state, "closed")
        clock.advance(61)  # storm clears
        r2 = c.ingest(make_alert(alert_id="a3"))
        self.assertEqual(r2.kind, "reopened")
        self.assertEqual(r2.flap_count, 1)


if __name__ == "__main__":
    unittest.main()
