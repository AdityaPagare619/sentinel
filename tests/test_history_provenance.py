"""Tests for D7 history provenance (design/d7-history-provenance.md).

- history beyond 72h is IGNORED (not down-weighted): no duplicates, no
  flap-reopens, no "familiarity" from archaeology;
- every history read and every decision carries history_as_of (one clock);
- stale label-pipeline snapshots PAGE (kind="label_stale") instead of being
  silently correlated on;
- novelty runs in shadow by default: logged, disposition untouched; live
  promotion is gated on evidence.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import unittest

from sentinel.correlator import (Correlator, HISTORY_WINDOW_S,
                                 LABEL_PIPELINE_SLO_S, LabelSnapshot,
                                 NoveltyShadowEvent, Page)
from sentinel.models import Disposition

from tests.helpers import FakeClock, make_alert

H72 = 72 * 3600


def _fresh_snapshot(clock, age_s=0, version="lp-v9"):
    return LabelSnapshot(labels={"env": "prod"}, version=version,
                         labels_as_of=clock.t - age_s)


class TestSingleClock(unittest.TestCase):
    def test_read_history_carries_history_as_of(self):
        clock = FakeClock()
        c = Correlator(clock=clock)
        a = make_alert()
        c.ingest(a)
        view = c.read_history(a.fingerprint)
        self.assertEqual(view.history_as_of, clock.t)
        self.assertTrue(view.observed_72h)
        self.assertFalse(view.novel)
        self.assertIsNotNone(view.episode)

    def test_every_decision_carries_history_as_of(self):
        clock = FakeClock()
        c = Correlator(clock=clock)
        r1 = c.ingest(make_alert())
        self.assertEqual(r1.history_as_of, clock.t)
        clock.advance(10)
        r2 = c.ingest(make_alert(alert_id="a2"))
        self.assertEqual(r2.kind, "duplicate")
        self.assertEqual(r2.history_as_of, clock.t)
        # All inputs shared one clock: the read and the decision agree.
        view = c.read_history(r1.fingerprint)
        self.assertEqual(view.history_as_of, r2.history_as_of)

    def test_read_history_matches_design_constant(self):
        self.assertEqual(HISTORY_WINDOW_S, H72)


class TestSeventyTwoHourBound(unittest.TestCase):
    def test_history_beyond_72h_is_ignored_not_downweighted(self):
        # A fingerprint last seen 73h ago must be treated as NOVEL — it must
        # not make the new alert a duplicate, must not flap-reopen, and must
        # not count as "familiar" in any way.
        clock = FakeClock()
        c = Correlator(clock=clock)
        a = make_alert()
        first = c.ingest(a)
        self.assertEqual(first.kind, "new")
        clock.advance(H72 + 3600)  # 73h of silence
        pre = c.read_history(a.fingerprint)
        self.assertTrue(pre.novel)                   # genuinely novel: archaeology
        self.assertFalse(pre.observed_72h)
        self.assertIsNone(pre.episode)               # pruned: gets no vote.
        second = c.ingest(make_alert(alert_id="a2"))
        self.assertEqual(second.kind, "new")          # not duplicate...
        self.assertEqual(second.flap_count, 0)        # ...not a flap-reopen...
        self.assertTrue(second.novel_shadow)          # ...shadow saw the novelty.

    def test_history_inside_72h_still_counts(self):
        clock = FakeClock()
        c = Correlator(clock=clock)
        a = make_alert()
        c.ingest(a)
        clock.advance(H72 - 3600)  # 71h: still inside the bound
        view = c.read_history(a.fingerprint)
        self.assertTrue(view.observed_72h)
        self.assertFalse(view.novel)
        self.assertIsNotNone(view.episode)

    def test_boundary_is_inclusive(self):
        clock = FakeClock()
        c = Correlator(clock=clock)
        a = make_alert()
        c.ingest(a)
        clock.advance(H72)  # exactly 72h: still observed
        self.assertTrue(c.read_history(a.fingerprint).observed_72h)
        clock.advance(1)    # 72h + 1s: archaeology
        view = c.read_history(a.fingerprint)
        self.assertFalse(view.observed_72h)
        self.assertTrue(view.novel)

    def test_old_prior_disposition_cannot_leak_across_bound(self):
        clock = FakeClock()
        c = Correlator(clock=clock)
        a = make_alert()
        c.ingest(a)
        c.note_disposition(a.fingerprint, Disposition(
            action="page_now", reason="threshold", team="platform",
            confidence=0.9, latency_ms=1.0))
        clock.advance(H72 + 1)
        r = c.ingest(make_alert(alert_id="a2"))
        self.assertEqual(r.kind, "new")
        self.assertIsNone(r.prior)  # the 73h-old prior is gone, not inherited.


class TestLabelStalenessSLO(unittest.TestCase):
    def test_stale_labels_page(self):
        clock = FakeClock()
        pages = []
        c = Correlator(clock=clock, page_sink=pages.append)
        snap = _fresh_snapshot(clock, age_s=LABEL_PIPELINE_SLO_S + 60)
        r = c.ingest(make_alert(), label_snapshot=snap)
        self.assertEqual(r.kind, "label_stale")
        self.assertTrue(r.label_stale)
        self.assertTrue(r.page_pipeline)
        self.assertEqual(r.labels_from, "label_pipeline")
        self.assertEqual(r.history_as_of, clock.t)
        # The page is loud and structured: severity is always "page".
        self.assertEqual(len(pages), 1)
        page = pages[0]
        self.assertIsInstance(page, Page)
        self.assertEqual(page.severity, "page")
        self.assertEqual(page.reason, "label_pipeline_stale")
        self.assertEqual(page.service, "web")
        self.assertEqual(page.fingerprint, r.fingerprint)
        self.assertEqual(page.raised_at, clock.t)
        # Not silently correlated: no dedup, no storm, no change-window.
        self.assertNotIn(r.kind, ("new", "duplicate", "storm",
                                  "change_window", "reopened"))

    def test_fresh_labels_do_not_page(self):
        clock = FakeClock()
        pages = []
        c = Correlator(clock=clock, page_sink=pages.append)
        r = c.ingest(make_alert(), label_snapshot=_fresh_snapshot(clock))
        self.assertEqual(r.kind, "new")
        self.assertFalse(r.label_stale)
        self.assertFalse(r.page_pipeline)
        self.assertEqual(r.labels_from, "label_pipeline")
        self.assertEqual(pages, [])

    def test_slo_boundary_is_inclusive(self):
        clock = FakeClock()
        pages = []
        c = Correlator(clock=clock, page_sink=pages.append)
        r = c.ingest(make_alert(),
                     label_snapshot=_fresh_snapshot(clock,
                                                    age_s=LABEL_PIPELINE_SLO_S))
        self.assertEqual(r.kind, "new")
        self.assertEqual(pages, [])
        r2 = c.ingest(make_alert(alert_id="b1", service="db"),
                      label_snapshot=_fresh_snapshot(
                          clock, age_s=LABEL_PIPELINE_SLO_S + 1))
        self.assertEqual(r2.kind, "label_stale")
        self.assertEqual(len(pages), 1)

    def test_missing_snapshot_is_not_stale(self):
        # Absence is visible (labels_from="alert"), not rotten: the alert's
        # intrinsic labels drive the decision, current behavior preserved.
        clock = FakeClock()
        pages = []
        c = Correlator(clock=clock, page_sink=pages.append)
        r = c.ingest(make_alert())
        self.assertEqual(r.kind, "new")
        self.assertEqual(r.labels_from, "alert")
        self.assertFalse(r.label_stale)
        self.assertEqual(pages, [])

    def test_page_emission_cooldown(self):
        # A pipeline outage must not emit a page per alert: per
        # (reason, service) cooldown owns emission discipline.
        clock = FakeClock()
        pages = []
        c = Correlator(clock=clock, page_sink=pages.append,
                       page_cooldown_s=300)
        snap = _fresh_snapshot(clock, age_s=LABEL_PIPELINE_SLO_S + 60)
        c.ingest(make_alert(), label_snapshot=snap)
        c.ingest(make_alert(alert_id="a2"), label_snapshot=snap)
        clock.advance(10)
        c.ingest(make_alert(alert_id="a3"), label_snapshot=snap)
        self.assertEqual(len(pages), 1)
        clock.advance(301)  # cooldown elapsed
        c.ingest(make_alert(alert_id="a4"), label_snapshot=snap)
        self.assertEqual(len(pages), 2)

    def test_stale_labels_never_suppress(self):
        # Even a duplicate-pattern alert with stale labels goes label_stale,
        # never "duplicate": rotten labels must not suppress a re-fire.
        clock = FakeClock()
        pages = []
        c = Correlator(clock=clock, page_sink=pages.append)
        c.ingest(make_alert(), label_snapshot=_fresh_snapshot(clock))
        stale = _fresh_snapshot(clock, age_s=LABEL_PIPELINE_SLO_S + 1)
        r = c.ingest(make_alert(alert_id="a2"), label_snapshot=stale)
        self.assertEqual(r.kind, "label_stale")
        self.assertIsNone(r.prior)


class TestNoveltyShadow(unittest.TestCase):
    def test_novelty_runs_in_shadow_by_default(self):
        clock = FakeClock()
        c = Correlator(clock=clock)  # novelty_mode="shadow" is the default
        r = c.ingest(make_alert())
        # Disposition UNCHANGED: kind is exactly what it would have been.
        self.assertEqual(r.kind, "new")
        self.assertFalse(r.novel)
        # ...but the shadow signal is recorded and visible.
        self.assertTrue(r.novel_shadow)
        log = c.shadow_log()
        self.assertEqual(len(log), 1)
        ev = log[0]
        self.assertIsInstance(ev, NoveltyShadowEvent)
        self.assertEqual(ev.fingerprint, r.fingerprint)
        self.assertEqual(ev.history_as_of, clock.t)
        self.assertEqual(ev.would_do, "flag_review")

    def test_shadow_does_not_touch_known_fingerprints(self):
        clock = FakeClock()
        c = Correlator(clock=clock)
        c.ingest(make_alert())
        r = c.ingest(make_alert(alert_id="a2"))
        self.assertFalse(r.novel_shadow)
        self.assertEqual(len(c.shadow_log()), 1)

    def test_live_novelty_is_flag_only(self):
        clock = FakeClock()
        c = Correlator(clock=clock, novelty_mode="live")
        r = c.ingest(make_alert())
        self.assertTrue(r.novel)
        self.assertFalse(r.novel_shadow)
        # Flag-only: kind is untouched — novelty never suppresses.
        self.assertEqual(r.kind, "new")
        self.assertEqual(len(c.shadow_log()), 0)

    def test_invalid_novelty_mode_rejected(self):
        with self.assertRaises(ValueError):
            Correlator(clock=FakeClock(), novelty_mode="yolo")

    def test_promotion_gate_blocks_thin_evidence(self):
        clock = FakeClock()
        c = Correlator(clock=clock)
        c.ingest(make_alert())  # 1 shadow event
        with self.assertRaises(ValueError):
            c.promote_novelty_to_live(min_shadow_events=100)
        self.assertEqual(c.novelty_mode, "shadow")

    def test_promotion_gate_passes_with_evidence(self):
        clock = FakeClock()
        c = Correlator(clock=clock)
        for i in range(5):
            c.ingest(make_alert(service=f"svc{i}", alert_id=f"n{i}"))
        n = c.promote_novelty_to_live(min_shadow_events=5)
        self.assertEqual(n, 5)
        self.assertEqual(c.novelty_mode, "live")
        # ...and the promoted rule immediately flags the next novel alert.
        r = c.ingest(make_alert(service="svcX", alert_id="nx"))
        self.assertTrue(r.novel)
        self.assertEqual(r.kind, "new")

    def test_novelty_after_72h_silence(self):
        # A fingerprint unseen for >72h is novel again — the shadow log
        # records the re-novelty, the episode is fresh, nothing flaps.
        clock = FakeClock()
        c = Correlator(clock=clock)
        c.ingest(make_alert())
        clock.advance(H72 + 1)
        r = c.ingest(make_alert(alert_id="a2"))
        self.assertTrue(r.novel_shadow)
        self.assertEqual(r.kind, "new")
        self.assertEqual(r.flap_count, 0)


if __name__ == "__main__":
    unittest.main()
