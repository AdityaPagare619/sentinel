"""D1 regression: freshness wired into the live gate kernel (ADR-014).

These tests exercise the SAME code path as production — Gate.evaluate
with a scripted Jev client — not a model of it (DR-26). Each test pins
one clause of the ratification contract:

  (a) stale attestation ⇒ page (never suppress on rotten data);
  (b) fresh proofs + green locks ⇒ the suppress path is still reachable;
  (c) no monitor wired ⇒ suppress unreachable (fail closed, never
      silently approximated);
  (d) the lock-1 dual-attestation interim is honored only via explicit
      operator evidence on the decision context;
  (e) kernel-level: a missing FreshnessReport fails the freshness legs
      closed even when every value lock passes.
"""

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from datetime import datetime, timedelta, timezone

from sentinel.audit import AuditLog
from sentinel.client import MockSystemOneClient
from sentinel.freshness import FreshnessMonitor, FreshnessValidator
from sentinel.gate import Gate, evaluate_policy
from sentinel.models import Thresholds
from sentinel.quantized import AllowlistEntry, Attestation
from sentinel.state import build_state, input_sha256

from tests.freshness_fixtures import (
    LABEL_PIPELINE,
    FixtureClock,
    build_bundle,
    make_allowlist_entry,
    make_fit_proof,
    make_threshold_attestation,
    make_thresholds,
)
from tests.helpers import make_alert
from tests.test_gate import canned


def _attested_entry(fingerprint):
    """Dual-attested allowlist entry: satisfies the leg-1 VALUE check
    (ADR-013 interim), so only the freshness legs vary across tests."""
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    return AllowlistEntry(
        fingerprint=fingerprint, author="carol",
        attestations=[
            Attestation("alice", now - timedelta(days=1), "lrq-9f2c-41ab", 30),
            Attestation("bob", now - timedelta(days=1), "lrq-9f2c-41ab", 30),
        ])


def _monitor(bundle_dir):
    validator = FreshnessValidator(
        clock=FixtureClock(),
        deployed_label_pipeline_version=LABEL_PIPELINE)
    mon = FreshnessMonitor(validator, bundle_dir)
    mon.boot()
    return mon


def _bundle(**kw):
    tmp = tempfile.mkdtemp(prefix="sentinel-d1-")
    return build_bundle(tmp, **kw), tmp


class FreshnessWiringBase(unittest.TestCase):
    def _wired_gate(self, alert, monitor, context_extra=None):
        state = build_state(alert, {}, {})
        client = MockSystemOneClient(
            {input_sha256(state): canned(p1=0.0, conf=0.95)})
        gate = Gate(client, Thresholds(),
                    [_attested_entry(alert.fingerprint)],
                    AuditLog(":memory:"),
                    freshness_monitor=monitor)
        return gate, state


class TestStalePages(FreshnessWiringBase):
    def test_stale_lock1_pages_with_full_rot_named(self):
        # (a) lock-1 proof rotten (fit TTL expired) ⇒ page_now, never
        # suppress — even though every value lock passes.
        alert = make_alert()
        bundle, _ = _bundle(
            fit_proof=make_fit_proof(trained_days_ago=200.0),
            entries=[(alert.fingerprint, "prod",
                      make_allowlist_entry(alert.fingerprint))])
        gate, state = self._wired_gate(alert, _monitor(bundle))
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "page_now")
        self.assertTrue(disp.reason.startswith("freshness:"),
                        f"veto reason must name the rot, got {disp.reason!r}")
        self.assertIn("lock1_stale", disp.reason)
        self.assertIn("TTL expired", disp.reason)

    def test_stale_lock2_pages_no_interim(self):
        # (a2) lock-2 proof rotten (revalidation clock lapsed) ⇒ page.
        # Lock 2 has no interim path — even explicit dual attestation on
        # the context cannot satisfy it.
        alert = make_alert()
        thresholds = make_thresholds()
        att = make_threshold_attestation(thresholds, attested_days_ago=40.0)
        bundle, _ = _bundle(
            thresholds=thresholds, threshold_attestation=att,
            entries=[(alert.fingerprint, "prod",
                      make_allowlist_entry(alert.fingerprint))])
        gate, state = self._wired_gate(alert, _monitor(bundle))
        disp, _rec = gate.evaluate(alert, state, {},
                                   {"lock1_dual_attested": True})
        self.assertEqual(disp.action, "page_now")
        self.assertIn("lock2_stale", disp.reason)
        self.assertIn("revalidation clock lapsed", disp.reason)


class TestFreshSuppresses(FreshnessWiringBase):
    def test_fresh_proofs_suppress_path_reachable(self):
        # (b) all proofs fresh + all value locks green ⇒ suppress.
        alert = make_alert()
        bundle, _ = _bundle(
            entries=[(alert.fingerprint, "prod",
                      make_allowlist_entry(alert.fingerprint))])
        mon = _monitor(bundle)
        self.assertEqual(mon.current_report().stale_locks(), [])
        gate, state = self._wired_gate(alert, mon)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        self.assertEqual(disp.reason, "allowlist")

    def test_lock1_interim_honored_only_via_context(self):
        # (d) stale lock 1 + explicit operator evidence on the decision
        # context ⇒ the interim path satisfies the probability leg.
        alert = make_alert()
        bundle, _ = _bundle(
            fit_proof=make_fit_proof(trained_days_ago=200.0),
            entries=[(alert.fingerprint, "prod",
                      make_allowlist_entry(alert.fingerprint))])
        gate, state = self._wired_gate(alert, _monitor(bundle))
        disp, _rec = gate.evaluate(alert, state, {},
                                   {"lock1_dual_attested": True})
        self.assertEqual(disp.action, "suppress")
        self.assertEqual(disp.reason, "allowlist")

    def test_lock1_interim_absent_without_evidence(self):
        # (d2) same stale bundle, no operator evidence ⇒ the page stands.
        alert = make_alert()
        bundle, _ = _bundle(
            fit_proof=make_fit_proof(trained_days_ago=200.0),
            entries=[(alert.fingerprint, "prod",
                      make_allowlist_entry(alert.fingerprint))])
        gate, state = self._wired_gate(alert, _monitor(bundle))
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "page_now")
        self.assertIn("lock1_stale", disp.reason)


class TestFailClosed(unittest.TestCase):
    def test_no_monitor_suppress_unreachable(self):
        # (c) a Gate with no freshness monitor wired can NEVER suppress —
        # the legs fail closed rather than silently approximating fresh.
        alert = make_alert()
        state = build_state(alert, {}, {})
        client = MockSystemOneClient(
            {input_sha256(state): canned(p1=0.0, conf=0.95)})
        gate = Gate(client, Thresholds(),
                    [_attested_entry(alert.fingerprint)],
                    AuditLog(":memory:"),
                    freshness_monitor=None)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")
        self.assertEqual(disp.action, "page_now")
        self.assertIn("no freshness evidence", disp.reason)

    def test_kernel_fails_closed_without_report(self):
        # (e) kernel-level: every value lock green, no report ⇒ veto.
        from sentinel.client import Answer
        alert = make_alert()
        sev = Answer(qid="severity", qtype="choice", choice="p3_medium",
                     noul=None,
                     probabilities={"p1_critical": 0.0, "p2_high": 0.0,
                                    "p3_medium": 0.9, "p4_low": 0.1},
                     confidence=0.95)
        team = Answer(qid="owning_team", qtype="choice", choice="platform",
                      noul=None, probabilities={"platform": 1.0},
                      confidence=0.99)
        disp_a = Answer(qid="disposition", qtype="choice",
                        choice="page_business_hours", noul=None,
                        probabilities={"page_business_hours": 1.0},
                        confidence=0.95)
        verdict = evaluate_policy(
            alert, jev_model="jev-1.13.0",
            q_severity=sev, q_team=team, q_disposition=disp_a,
            thresholds=Thresholds(), allowlist={alert.fingerprint},
            latency_ms=1.0, prob_lock_pass=True,
            freshness_report=None)
        self.assertEqual(verdict.action, "page_now")
        self.assertTrue(verdict.reason.startswith("freshness:"))


if __name__ == "__main__":
    unittest.main()
