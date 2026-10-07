"""Tests for ADR-007 mute governance (lane D12 — muted-not-dropped).

Covers the five ADR-007 conditions:
  (a) engine disposition enum frozen — mute never becomes a disposition;
  (b) mute transitions event-logged with reason + attestor + TTL;
  (c) no auto-mute, ever — structural + log-layer + codebase scan proof;
  (d) weekly "what we muted" review ritual in the shadow report;
  (e) Tripwire quarantine invariant in CI (bounded staleness).

NOTE on (a): ADR-007's "four-valued" was written pre-D3. D3 (#56) ratified
"folded" as a 5th engine disposition (storm-continuation absorbed into the
aggregate page — explicitly NOT suppression). The invariant protected here
is "mute never becomes a disposition": the engine vocabulary is frozen at
exactly the ratified set, and the platform store's DISP_OPTIONS stays
4-valued with mute as a rendering label only.
"""

import inspect
import json
import os
import re
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, os.path.join(_REPO, "platform", "server"))

from sentinel.eventlog import (ACTORS, DISPOSITIONS, EVENT_TYPES, EventLog,
                               HUMAN_ATTESTOR_BLOCKLIST)
from sentinel.shadow import ShadowStore
from sentinel import shadow_report
from sentinel import verify as _verify_mod

import mute as mute_mod


def _decision_body(disposition="page_now", **kw):
    body = {
        "disposition": disposition,
        "budget_outcome": "answered_in_time",
        "lock_evaluation": {"prob": {"verdict": "pass"}},
        "freshness": {"fit_as_of": "2026-10-03T00:00:00.000Z"},
        "threshold_counterfactual": {"strict": "suppress"},
        "links": {"decision_requested_seq": 1},
    }
    body.update(kw)
    return body


def _commit_decision(log, seq_no, disposition):
    return log.record_decision_and_enqueue(
        alert_id=f"alert-{seq_no}", fingerprint=f"fp-{seq_no}",
        episode_id=f"ep-{seq_no}", body=_decision_body(disposition),
        outbox=None)


# ---------------------------------------------------------------- (a)

class TestEngineDispositionEnumFrozen(unittest.TestCase):
    """ADR-007 (a): mute is a platform-tier label, never an engine disposition."""

    def test_engine_vocabulary_frozen_at_ratified_set(self):
        # Exact-set freeze: any new disposition (mute included) breaks loudly
        # and forces an ADR-level discussion instead of slipping in.
        self.assertEqual(DISPOSITIONS,
                         {"page_now", "page_business_hours", "suppress",
                          "passthrough", "folded"})

    def test_mute_is_not_an_engine_disposition(self):
        self.assertNotIn("muted", DISPOSITIONS)
        self.assertNotIn("mute", DISPOSITIONS)

    def test_platform_disposition_options_stay_four_valued(self):
        # The platform store clamps to four dispositions; mute is rendered
        # over suppress-with-reason, never stored as one.
        import store as _store
        self.assertEqual(tuple(_store.DISP_OPTIONS),
                         ("page_now", "page_business_hours", "suppress",
                          "passthrough"))
        self.assertNotIn("muted", _store.DISP_OPTIONS)
        self.assertNotIn("mute", _store.DISP_OPTIONS)

    def test_gate_never_emits_mute_literal(self):
        # Static check: every action= literal in gate.py is in the frozen
        # engine vocabulary, and none is mute/muted.
        path = os.path.join(_REPO, "src", "sentinel", "gate.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        literals = set(re.findall(r"action\s*=\s*[\"']([^\"']+)[\"']", src))
        self.assertTrue(literals, "expected action literals in gate.py")
        self.assertLessEqual(literals, DISPOSITIONS,
                             f"gate emits outside the frozen vocabulary: "
                             f"{literals - DISPOSITIONS}")
        self.assertNotIn("muted", literals)
        self.assertNotIn("mute", literals)

    def test_mute_label_is_not_a_disposition_anywhere(self):
        self.assertNotIn(mute_mod.MUTE_LABEL, DISPOSITIONS)
        self.assertNotIn(mute_mod.MUTE_LABEL, EVENT_TYPES)


# ---------------------------------------------------------------- (b)

class TestMuteTransitionsEventLogged(unittest.TestCase):
    """ADR-007 (b): mute transitions are event-logged (reason, attestor, TTL)."""

    def setUp(self):
        self.log = EventLog(":memory:")

    def _applied(self, **kw):
        args = dict(alert_id="a1", fingerprint="fp-noisy",
                    episode_id="ep1", reason="flapping check, paging hourly",
                    attestor="Aditya Pagare", ttl_s=86400)
        args.update(kw)
        return mute_mod.apply_mute(self.log, **args)

    def test_apply_writes_mute_applied_with_triple(self):
        seq = self._applied()
        rows = self.log.events_of_type("mute_applied")
        self.assertEqual(len(rows), 1)
        body = json.loads(rows[0]["body"])
        self.assertEqual(rows[0]["seq"], seq)
        self.assertEqual(rows[0]["actor"], "operator")
        self.assertEqual(body["reason"], "flapping check, paging hourly")
        self.assertEqual(body["attestor"], "Aditya Pagare")
        self.assertEqual(body["ttl_s"], 86400)
        self.assertIn("muted_at", body)
        self.assertIn("expires_at", body)
        self.assertEqual(rows[0]["fingerprint"], "fp-noisy")

    def test_operator_is_a_known_actor(self):
        self.assertIn("operator", ACTORS)

    def test_appeal_writes_mute_appealed(self):
        self._applied()
        mute_mod.appeal_mute(self.log, alert_id="a1", fingerprint="fp-noisy",
                             episode_id="ep1", attestor="Oncall Rivera",
                             appeal_reason="check fixed upstream")
        rows = self.log.events_of_type("mute_appealed")
        self.assertEqual(len(rows), 1)
        body = json.loads(rows[0]["body"])
        self.assertEqual(body["attestor"], "Oncall Rivera")
        self.assertEqual(body["appeal_reason"], "check fixed upstream")

    def test_lift_writes_mute_lifted(self):
        self._applied()
        mute_mod.lift_mute(self.log, alert_id="a1", fingerprint="fp-noisy",
                           episode_id="ep1", attestor="Aditya Pagare",
                           lift_reason="noise stopped")
        rows = self.log.events_of_type("mute_lifted")
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["body"])["lift_reason"],
                         "noise stopped")

    def test_expiry_sweep_writes_mute_expired(self):
        # Pin BOTH ends of the clock: apply_mute defaults muted_at to the
        # wall clock, so a pinned sweep time alone is a date time-bomb
        # (it rotted 2026-10-07 when "now" moved past the pinned sweep).
        self._applied(ttl_s=3600, now_iso="2026-10-04T00:00:00.000Z")
        # 2h later: the TTL the human set is honored (auto-UNmute, not auto-mute).
        seqs = mute_mod.expire_mutes(self.log,
                                     now_iso="2026-10-04T02:00:00.000Z")
        self.assertEqual(len(seqs), 1)
        rows = self.log.events_of_type("mute_expired")
        body = json.loads(rows[0]["body"])
        self.assertEqual(body["fingerprint"], "fp-noisy")
        self.assertIn("applied_seq", body)

    def test_active_mutes_folds_the_log(self):
        self._applied()
        active = mute_mod.active_mutes(self.log)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].attestor, "Aditya Pagare")
        mute_mod.lift_mute(self.log, alert_id="a1", fingerprint="fp-noisy",
                           episode_id="ep1", attestor="Aditya Pagare",
                           lift_reason="done")
        self.assertEqual(mute_mod.active_mutes(self.log), [])

    def test_chain_verifies_after_mute_events(self):
        with tempfile.NamedTemporaryFile(suffix=".db",
                                         delete=False) as tmp:
            path = tmp.name
        try:
            log = EventLog(path)
            _commit_decision(log, 1, "suppress")
            mute_mod.apply_mute(log, alert_id="a1", fingerprint="fp-1",
                                episode_id="ep-1", reason="noisy",
                                attestor="Aditya Pagare", ttl_s=7200)
            log.close()
            result = _verify_mod.verify(path)
            self.assertTrue(result["ok"])
        finally:
            os.unlink(path)

    def test_ttl_bounds_rejected(self):
        with self.assertRaises(ValueError):
            self._applied(ttl_s=60)            # below 1h
        with self.assertRaises(ValueError):
            self._applied(ttl_s=8 * 86400)     # above 7d

    def test_empty_reason_rejected(self):
        with self.assertRaises(ValueError):
            self._applied(reason="   ")


# ---------------------------------------------------------------- (c)

class TestNoAutoMuteEver(unittest.TestCase):
    """ADR-007 (c): no auto-mute, ever — structural + log-layer + scan proof."""

    def setUp(self):
        self.log = EventLog(":memory:")

    def test_attestor_is_required_keyword_only(self):
        sig = inspect.signature(mute_mod.apply_mute)
        p = sig.parameters["attestor"]
        self.assertEqual(p.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(p.default, inspect.Parameter.empty)
        for fn_name in ("appeal_mute", "lift_mute"):
            p = inspect.signature(getattr(mute_mod, fn_name)).parameters[
                "attestor"]
            self.assertEqual(p.kind, inspect.Parameter.KEYWORD_ONLY)
            self.assertIs(p.default, inspect.Parameter.empty)

    def test_missing_attestor_raises(self):
        with self.assertRaises(TypeError):
            mute_mod.apply_mute(self.log, alert_id="a", fingerprint="f",
                                episode_id="e", reason="r", ttl_s=3600)

    def test_automated_attestors_rejected_by_platform(self):
        for bad in ["engine", "forwarder", "watchdog", "system", "auto",
                    "AutoMute", "sentinel", "cron", "operator", "  "]:
            with self.assertRaises(ValueError, msg=f"attestor={bad!r}"):
                mute_mod.apply_mute(self.log, alert_id="a", fingerprint="f",
                                    episode_id="e", reason="r",
                                    attestor=bad, ttl_s=3600)

    def test_automated_attestors_rejected_at_log_layer(self):
        # Even a direct append_event bypass cannot mint an auto-mute: the
        # log's _validate enforces the human-attestor rule.
        from sentinel.eventlog import EventLogError
        for bad in ["engine", "watchdog", "auto"]:
            with self.assertRaises(EventLogError, msg=f"attestor={bad!r}"):
                self.log.append_event(
                    "mute_applied", actor="operator", alert_id="a",
                    fingerprint="f", episode_id="e",
                    body={"fingerprint": "f", "reason": "r",
                          "attestor": bad, "ttl_s": 3600,
                          "muted_at": "2026-10-04T00:00:00.000Z",
                          "expires_at": "2026-10-04T01:00:00.000Z"})

    def test_codebase_scan_finds_no_unattested_mute_paths(self):
        """The documented scan (see mute_mod.scan_for_unattested_mute_paths).

        Procedure: tokenize every *.py under src/, platform/, tests/,
        scripts/, demo/, eval/, chiefs/, rehearsal/, ops/; find real call sites of
        apply_mute/appeal_mute/lift_mute; every non-test call site must pass
        an explicit human attestor literal. tokenize (not regex) so
        docstrings/comments/defs are never mistaken for calls.
        """
        result = mute_mod.scan_for_unattested_mute_paths(_REPO)
        sites = result["call_sites"]
        self.assertTrue(sites,
                        "scan found zero mute-writer call sites — "
                        "the scan itself is broken")
        # Every call site the scan found must be accounted for: tests or
        # this module's own write path.
        for s in sites:
            self.assertTrue(
                s["in_tests"] or s["file"] == "platform/server/mute.py",
                f"unaccounted mute-writer call site: {s}")
        self.assertEqual(result["violations"], [],
                         f"unattested mute paths found: "
                         f"{result['violations']}")
        # Document the scan in the test output for the audit trail.
        print(f"\n[no-auto-mute scan] {len(sites)} call site(s), "
              f"0 violations: "
              + ", ".join(f"{s['file']}:{s['line']}" for s in sites))


# ---------------------------------------------------------------- (d)

class TestWeeklyMuteReviewRitual(unittest.TestCase):
    """ADR-007 (d): weekly 'what we muted' review in the shadow report."""

    def setUp(self):
        self.log = EventLog(":memory:")

    def _report(self, mute_section=None):
        store = ShadowStore()
        return shadow_report.generate_shadow_report(
            store, org="acme", week_label="2026-W40",
            window_start="2026-09-28T00:00:00.000Z",
            window_end="2026-10-04T00:00:00.000Z",
            mute_section=mute_section)

    def test_section_always_present(self):
        report = self._report()
        self.assertIn("mute_section", report)
        md = shadow_report.render_report_markdown(report)
        self.assertIn("## What we muted", md)

    def test_named_owner_placeholder(self):
        report = self._report()
        sec = report["mute_section"]
        self.assertTrue(sec["owner_is_placeholder"])
        self.assertIn("TBD", sec["owner"])
        md = shadow_report.render_report_markdown(report)
        self.assertIn("OWNER UNASSIGNED", md)

    def test_review_checklist_rendered(self):
        report = self._report()
        sec = report["mute_section"]
        self.assertEqual(len(sec["review_checklist"]), 5)
        md = shadow_report.render_report_markdown(report)
        for item in sec["review_checklist"]:
            self.assertIn(item, md)

    def test_mute_rates_and_rows(self):
        mute_mod.apply_mute(self.log, alert_id="a1", fingerprint="fp-noisy",
                            episode_id="ep1", reason="flapping",
                            attestor="Aditya Pagare", ttl_s=86400)
        mute_mod.apply_mute(self.log, alert_id="a2", fingerprint="fp-chatty",
                            episode_id="ep2", reason="known benign",
                            attestor="Oncall Rivera", ttl_s=7200)
        mute_mod.appeal_mute(self.log, alert_id="a2", fingerprint="fp-chatty",
                             episode_id="ep2", attestor="Aditya Pagare",
                             appeal_reason="still paging hourly")
        sec = mute_mod.build_mute_section(self.log, week_label="2026-W40")
        self.assertEqual(sec["counts"]["applied"], 2)
        self.assertEqual(sec["counts"]["appealed"], 1)
        self.assertEqual(sec["counts"]["active"], 1)
        self.assertEqual(sec["active_mutes"][0]["fingerprint"], "fp-noisy")
        report = self._report(mute_section=sec)
        md = shadow_report.render_report_markdown(report)
        self.assertIn("fp-noisy", md)
        self.assertIn("Aditya Pagare", md)
        self.assertIn("flapping", md)

    def test_engine_fallback_shape_matches_platform_contract(self):
        # Contract pin: the engine report's empty-section shape must equal
        # the platform's canonical empty_mute_section().
        self.assertEqual(
            shadow_report._empty_mute_section("2026-W40"),
            mute_mod.empty_mute_section(week_label="2026-W40"))

    def test_mute_rendering_label_only(self):
        rec = mute_mod.active_mutes(self.log)
        mute_mod.apply_mute(self.log, alert_id="a1", fingerprint="fp-noisy",
                            episode_id="ep1", reason="flapping",
                            attestor="Aditya Pagare", ttl_s=86400)
        rec = mute_mod.active_mutes(self.log)[0]
        self.assertEqual(
            mute_mod.render_disposition_label("suppress", rec), "muted")
        self.assertEqual(
            mute_mod.render_disposition_label("suppress", None), "suppress")
        # Mute never relabels anything but suppress-with-reason.
        for d in ("page_now", "page_business_hours", "passthrough", "folded"):
            self.assertEqual(mute_mod.render_disposition_label(d, rec), d)


# ---------------------------------------------------------------- (e)

class TestQuarantineInvariantCI(unittest.TestCase):
    """ADR-007 (e): every suppression-class disposition retrievable from
    quarantine within bounded staleness."""

    def setUp(self):
        self.log = EventLog(":memory:")

    def test_every_suppression_retrievable_within_bound(self):
        plan = ["suppress", "page_now", "suppress", "passthrough",
                "suppress", "page_business_hours"]
        for i, disp in enumerate(plan):
            _commit_decision(self.log, i, disp)
        expected = {f"fp-{i}" for i, d in enumerate(plan) if d == "suppress"}

        t_commit_end = time.monotonic()
        rows = mute_mod.quarantined_suppressions(self.log)
        t_query_end = time.monotonic()

        got = {r["fingerprint"] for r in rows}
        self.assertEqual(got, expected)
        # Bounded staleness: commit -> retrievable within the bound.
        self.assertLess(t_query_end - t_commit_end,
                        mute_mod.QUARANTINE_STALENESS_BOUND_S,
                        "quarantine staleness exceeded the CI bound")
        for r in rows:
            self.assertTrue(r["context_retrievable"],
                            f"alert context missing for {r['alert_id']}")
            # ADR-023: muted-or-not, suppressions carry the counterfactual.
            self.assertIn("threshold_counterfactual", r)
            self.assertIsNotNone(r["threshold_counterfactual"])

    def test_muted_rows_visible_never_excluded(self):
        _commit_decision(self.log, 0, "suppress")
        _commit_decision(self.log, 1, "suppress")
        mute_mod.apply_mute(self.log, alert_id="alert-0", fingerprint="fp-0",
                            episode_id="ep-0", reason="flapping",
                            attestor="Aditya Pagare", ttl_s=86400)
        rows = mute_mod.quarantined_suppressions(self.log)
        by_fp = {r["fingerprint"]: r for r in rows}
        self.assertEqual(len(rows), 2)  # muted row still present
        self.assertEqual(by_fp["fp-0"]["label"], "muted")
        self.assertTrue(by_fp["fp-0"]["muted"])
        self.assertEqual(by_fp["fp-0"]["mute"]["attestor"], "Aditya Pagare")
        self.assertEqual(by_fp["fp-1"]["label"], "suppress")
        self.assertFalse(by_fp["fp-1"]["muted"])

    def test_folded_is_not_suppression_class(self):
        # D3: "folded" is explicitly NOT suppression — it stays out of the
        # quarantine projection (it was absorbed into the aggregate page).
        _commit_decision(self.log, 0, "folded")
        _commit_decision(self.log, 1, "suppress")
        rows = mute_mod.quarantined_suppressions(self.log)
        self.assertEqual([r["disposition"] for r in rows], ["suppress"])

    def test_empty_quarantine(self):
        _commit_decision(self.log, 0, "page_now")
        self.assertEqual(mute_mod.quarantined_suppressions(self.log), [])


if __name__ == "__main__":
    unittest.main()
