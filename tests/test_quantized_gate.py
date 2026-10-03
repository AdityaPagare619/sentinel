"""Tests for ADR-013 — the quantized probability lock
(design/fixes/04-quantized-gate.md).

Written BEFORE the implementation (TDD): the M-1 test must FAIL on the old
`reported < 0.002` logic and pass on the quantized lock. Every number below
was hand-computed independently of the design doc (see the verification
script in the lane report); the tests assert the numbers, not the doc.

Pre-mortem -> regression test mapping:
  PM-1 composition shift      -> TestShiftMonitor (class-conditional trip)
  PM-2 attestation theater    -> TestDualAttestation (4 rejections)
  PM-3 Wilson misimplementation -> TestWilsonBound (hand values, 1350/1351 tripwire,
                                   unlabeled exclusion, wrong reference class)
  PM-4 the n_min ratchet      -> TestContractFrozen (bar/clock/n_min not tunable)
  PM-5 label pipeline freeze  -> TestLabelDiscipline (unlabeled excluded, window
                                   freshness, unlabeled-fraction flag)
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import inspect
import math
import unittest
from datetime import datetime, timedelta, timezone

from sentinel.audit import AuditLog
from sentinel.client import MockSystemOneClient
from sentinel.gate import Gate
from sentinel.models import Thresholds
from sentinel.state import build_state, input_sha256
from sentinel.quantized import (
    SUPPRESS_BAR,
    WILSON_Z,
    REFERENCE_CLASS,
    GATE_FORMULA_VERSION,
    FIT_WINDOW_DAYS,
    FIT_VALID_DAYS,
    FIT_WINDOW_FRESH_DAYS,
    ATTESTATION_TTL_MAX_DAYS,
    CERT_MIN_N,
    CERT_PASS_U,
    CERT_FAIL_U,
    MIN_N_K0,
    AllowlistEntry,
    Attestation,
    Certificate,
    FitArtifact,
    FitIntegrityError,
    FitStore,
    QuantizedValueError,
    ShiftMonitor,
    decision_certificate,
    dual_attestation_valid,
    fit_is_valid,
    is_linkage_run_id,
    leg1_prob_lock,
    min_n_to_clear,
    parse_hundredths,
    wilson_upper_onesided,
)
from sentinel.tuner import fit_r000_class

from tests.helpers import make_alert
from test_gate import canned, fresh_monitor_for  # reuse the fixture builders

NOW = datetime(2026, 10, 3, 13, 0, 0, tzinfo=timezone.utc)
PINNED = "jev-1.13.0"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _att(attestor, days_ago=1, ttl=30, ref="lrq-9f2c-41ab"):
    return Attestation(
        attestor_id=attestor,
        attested_at=NOW - timedelta(days=days_ago),
        evidence_ref=ref,
        ttl_days=ttl,
    )


def _entry(fp, author="carol", a1="alice", a2="bob", **kw):
    return AllowlistEntry(
        fingerprint=fp, author=author,
        attestations=[_att(a1, **kw), _att(a2, **kw)],
    )


def _fit(n, k, org="org-c", model_pin=PINNED, shift="OK",
         computed_days_ago=1, window_end_days_ago=1, reference_class=REFERENCE_CLASS,
         gate_formula_version=GATE_FORMULA_VERSION):
    computed_at = NOW - timedelta(days=computed_days_ago)
    window_end = NOW - timedelta(days=window_end_days_ago)
    window_start = window_end - timedelta(days=FIT_WINDOW_DAYS)
    u = wilson_upper_onesided(k, n) if n > 0 else 1.0
    art = FitArtifact(
        fit_id="",  # bound below
        org=org,
        reference_class=reference_class,
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
        n=n, k=k, m=0,
        p_hat_upper=u,
        z=WILSON_Z,
        model_pin=model_pin,
        gate_formula_version=gate_formula_version,
        computed_at=computed_at.isoformat(),
        valid_until=(computed_at + timedelta(days=FIT_VALID_DAYS)).isoformat(),
        shift_status=shift,
        tuner_version="test",
    )
    return art.bind()


def _gate_with(client_resp=None, *, allowlist=None, fit_store=None,
               org="org-c", alert=None, p1=0.0, conf=0.95,
               freshness_monitor=None):
    alert = alert or make_alert()
    resp = client_resp or canned(p1=p1, conf=conf)
    state = build_state(alert, {}, {})
    client = MockSystemOneClient({input_sha256(state): resp})
    audit = AuditLog(":memory:")
    gate = Gate(client, Thresholds(), allowlist or set(), audit,
                fit_store=fit_store, pinned_model=PINNED, org=org,
                clock=lambda: NOW, freshness_monitor=freshness_monitor)
    return gate, alert, state


# ---------------------------------------------------------------------------
# The M-1 regression: the exact bug being fixed
# ---------------------------------------------------------------------------

class TestM1UnitsError(unittest.TestCase):
    def test_reported_0_00_without_fit_or_attestation_must_not_suppress(self):
        """True p=0.0049 quantizes to reported 0.00. Old logic evaluated
        0.00 < 0.002 -> TRUE -> suppressed (net expected loss $145/decision).
        New logic: reported 0.00 with no fit and no attestation -> leg 1 FALSE
        -> must NOT suppress."""
        alert = make_alert()
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress",
                            "M-1 BUG: reported-0.00 suppressed with no fit/attestation")

    def test_integer_hundredths_predicate_not_float_comparison(self):
        """Step 2 is exact equality on the quantized domain. A reported
        0.01 (hundredths=1) must fail the leg even though 0.01 is 'small'."""
        ok, detail = leg1_prob_lock(0.01, org="org-c", now=NOW,
                                    fit_store=None, pinned_model=PINNED,
                                    entry=None)
        self.assertFalse(ok)
        self.assertEqual(detail["hundredths"], 1)
        self.assertEqual(detail["step"], "point-condition")

    def test_malformed_wire_value_fails_closed(self):
        """A non-hundredths value (0.005) is not a valid wire report: the leg
        fails closed instead of being compared as a float."""
        with self.assertRaises(QuantizedValueError):
            parse_hundredths("0.005")
        ok, detail = leg1_prob_lock(0.005, org="org-c", now=NOW,
                                    fit_store=None, pinned_model=PINNED,
                                    entry=None)
        self.assertFalse(ok)
        self.assertEqual(detail["step"], "parse")


class TestParseHundredths(unittest.TestCase):
    def test_wire_values(self):
        self.assertEqual(parse_hundredths("0.00"), 0)
        self.assertEqual(parse_hundredths("0.01"), 1)
        self.assertEqual(parse_hundredths("0.99"), 99)
        self.assertEqual(parse_hundredths("1.00"), 100)
        self.assertEqual(parse_hundredths(0.0), 0)      # float from client
        self.assertEqual(parse_hundredths(0.01), 1)     # float repr is exact
        self.assertEqual(parse_hundredths(0), 0)

    def test_rejects_non_values(self):
        for bad in ("abc", "", None, "0.005", 0.0049, float("nan")):
            with self.assertRaises(QuantizedValueError, msg=repr(bad)):
                parse_hundredths(bad)


# ---------------------------------------------------------------------------
# PM-3: the Wilson misimplementation — hand-computed values + tripwire
# ---------------------------------------------------------------------------

class TestWilsonBound(unittest.TestCase):
    # Hand-computed with z=1.6449 (the pinned value), full formula:
    #   k=0,n=1351 -> 0.0019987328 ; k=0,n=1350 -> 0.0020002104
    #   k=1,n=1351 -> 0.0033110162 ; k=0,n=541  -> 0.0049763981
    # A two-sided-95% implementation or a z typo fails these.
    def test_k0_n1351_hand_value(self):
        self.assertAlmostEqual(wilson_upper_onesided(0, 1351), 0.0019987328,
                               places=9)

    def test_k0_n1350_hand_value(self):
        self.assertAlmostEqual(wilson_upper_onesided(0, 1350), 0.0020002104,
                               places=9)

    def test_k1_n1351_hand_value(self):
        self.assertAlmostEqual(wilson_upper_onesided(1, 1351), 0.0033110162,
                               places=9)

    def test_k0_n541_hand_value(self):
        self.assertAlmostEqual(wilson_upper_onesided(0, 541), 0.0049763981,
                               places=9)

    def test_1350_1351_boundary_tripwire(self):
        # The boundary is the tripwire: any reimplementation that gets it
        # wrong fails loudly.
        self.assertGreaterEqual(wilson_upper_onesided(0, 1350), SUPPRESS_BAR)
        self.assertLess(wilson_upper_onesided(0, 1351), SUPPRESS_BAR)
        self.assertEqual(min_n_to_clear(0, SUPPRESS_BAR), 1351)
        self.assertEqual(MIN_N_K0, 1351)

    def test_k1_1351_does_not_clear(self):
        self.assertGreaterEqual(wilson_upper_onesided(1, 1351), SUPPRESS_BAR)
        self.assertEqual(min_n_to_clear(1, SUPPRESS_BAR), 2239)

    def test_bound_is_monotone_in_n(self):
        prev = wilson_upper_onesided(0, 100)
        for n in (500, 1000, 1351, 5000):
            cur = wilson_upper_onesided(0, n)
            self.assertLess(cur, prev)
            prev = cur

    def test_rejects_degenerate_inputs(self):
        with self.assertRaises(ValueError):
            wilson_upper_onesided(0, 0)
        with self.assertRaises(ValueError):
            wilson_upper_onesided(-1, 10)
        with self.assertRaises(ValueError):
            wilson_upper_onesided(11, 10)

    def test_z_is_pinned(self):
        self.assertEqual(WILSON_Z, 1.6449)


# ---------------------------------------------------------------------------
# Fit artifact: schema assertion, content-hash binding, validity
# ---------------------------------------------------------------------------

class TestFitArtifact(unittest.TestCase):
    def _store(self, fit):
        s = FitStore()
        s.put(fit)
        return s

    def test_fit_path_suppresses_when_bound_clears(self):
        alert = make_alert()
        store = self._store(_fit(n=1351, k=0))
        # D1: suppress requires fresh evidence (fit-artifact leg).
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       fit_store=store, p1=0.0, conf=0.95,
                                       freshness_monitor=fresh_monitor_for(
                                           [alert.fingerprint]))
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")

    def test_n1350_fit_does_not_suppress(self):
        alert = make_alert()
        store = self._store(_fit(n=1350, k=0))
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       fit_store=store, p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")

    def test_k1_fit_does_not_suppress(self):
        alert = make_alert()
        store = self._store(_fit(n=1351, k=1))
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       fit_store=store, p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")

    def test_wrong_reference_class_rejected(self):
        # PM-3: bound computed over the wrong class must not steer the gate.
        alert = make_alert()
        store = self._store(_fit(n=5000, k=0, reference_class="R(C,W,r=0.01)"))
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       fit_store=store, p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")

    def test_model_pin_mismatch_is_stale(self):
        alert = make_alert()
        store = self._store(_fit(n=5000, k=0, model_pin="jev-1.12.0"))
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       fit_store=store, p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")
        ok, reason = fit_is_valid(store.get("org-c"), pinned_model=PINNED,
                                  now=NOW)
        self.assertFalse(ok)
        self.assertIn("model_pin", reason)

    def test_expired_fit_is_stale(self):
        alert = make_alert()
        store = self._store(_fit(n=5000, k=0, computed_days_ago=31))
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       fit_store=store, p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")

    def test_window_not_fresh_is_stale(self):
        # PM-5: a fit whose window ended 3 weeks ago is a stale claim wearing
        # a fresh timestamp.
        fit = _fit(n=5000, k=0, window_end_days_ago=21, computed_days_ago=1)
        ok, reason = fit_is_valid(fit, pinned_model=PINNED, now=NOW)
        self.assertFalse(ok)
        self.assertIn("window", reason)

    def test_tripped_shift_is_stale(self):
        fit = _fit(n=5000, k=0, shift="TRIPPED")
        ok, reason = fit_is_valid(fit, pinned_model=PINNED, now=NOW)
        self.assertFalse(ok)
        self.assertIn("shift", reason)

    def test_stale_fit_never_degrades_to_point_estimate(self):
        # A stale fit is treated EXACTLY as no fit: the only remaining
        # suppression path is dual attestation. Here there is none.
        alert = make_alert()
        store = self._store(_fit(n=5000, k=0, computed_days_ago=60))
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       fit_store=store, p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")

    def test_fit_id_content_hash_binding(self):
        fit = _fit(n=1351, k=0)
        self.assertTrue(fit.verify_binding())
        self.assertEqual(fit.fit_id, fit.content_hash())
        # Tamper with the bound in place: the store must refuse it on read.
        tampered = FitArtifact(**{**fit.__dict__, "p_hat_upper": 0.0001})
        store = FitStore()
        with self.assertRaises(FitIntegrityError):
            store.put(tampered)

    def test_store_read_reverifies_hash(self):
        fit = _fit(n=1351, k=0)
        store = FitStore()
        store.put(fit)
        store._artifacts[("org-c", REFERENCE_CLASS)] = FitArtifact(
            **{**fit.__dict__, "k": 3})  # corrupt behind the store's back
        with self.assertRaises(FitIntegrityError):
            store.get("org-c")

    def test_gate_treats_corrupt_fit_as_no_fit(self):
        alert = make_alert()
        store = FitStore()
        store.put(_fit(n=5000, k=0))
        store._artifacts[("org-c", REFERENCE_CLASS)] = FitArtifact(
            **{**store.get("org-c").__dict__, "k": 9})
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       fit_store=store, p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})  # must not raise
        self.assertNotEqual(disp.action, "suppress")

    def test_gate_formula_version_change_invalidates(self):
        fit = _fit(n=5000, k=0, gate_formula_version="adr013-v0")
        ok, reason = fit_is_valid(fit, pinned_model=PINNED, now=NOW)
        self.assertFalse(ok)
        self.assertIn("gate_formula_version", reason)

    def test_unpinned_z_rejected(self):
        # The artifact pins z=1.6449; a fit computed with a smaller z would
        # clear the bar with less evidence.
        import dataclasses
        fit = dataclasses.replace(_fit(n=5000, k=0), z=1.0).bind()
        ok, reason = fit_is_valid(fit, pinned_model=PINNED, now=NOW)
        self.assertFalse(ok)
        self.assertIn("z=", reason)


# ---------------------------------------------------------------------------
# PM-2: attestation theater
# ---------------------------------------------------------------------------

class TestDualAttestation(unittest.TestCase):
    def test_dual_attestation_suppresses(self):
        alert = make_alert()
        entry = _entry(alert.fingerprint)
        # D1: suppress requires fresh evidence — wire the monitor the same
        # way production does.
        gate, alert, state = _gate_with(
            allowlist=[entry], p1=0.0, conf=0.95,
            freshness_monitor=fresh_monitor_for([alert.fingerprint]))
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        self.assertEqual(disp.reason, "allowlist")

    def test_same_attestor_twice_rejected(self):
        entry = _entry("fp-x", a1="alice", a2="alice")
        ok, reason = dual_attestation_valid(entry, NOW)
        self.assertFalse(ok)
        self.assertIn("distinct", reason)

    def test_attestor_is_author_rejected(self):
        entry = _entry("fp-x", author="alice", a1="alice", a2="bob")
        ok, reason = dual_attestation_valid(entry, NOW)
        self.assertFalse(ok)
        self.assertIn("author", reason)

    def test_slack_thread_evidence_ref_rejected(self):
        entry = _entry("fp-x")
        entry.attestations[0] = _att("alice", ref="see #ops-chat thread")
        ok, reason = dual_attestation_valid(entry, NOW)
        self.assertFalse(ok)
        self.assertIn("evidence_ref", reason)

    def test_empty_evidence_ref_rejected(self):
        entry = _entry("fp-x")
        entry.attestations[1] = _att("bob", ref="")
        ok, reason = dual_attestation_valid(entry, NOW)
        self.assertFalse(ok)

    def test_expired_attestation_rejected(self):
        entry = _entry("fp-x", days_ago=31, ttl=30)
        ok, reason = dual_attestation_valid(entry, NOW)
        self.assertFalse(ok)
        self.assertIn("expired", reason)

    def test_ttl_above_30d_rejected(self):
        entry = _entry("fp-x", ttl=90)
        ok, reason = dual_attestation_valid(entry, NOW)
        self.assertFalse(ok)
        self.assertIn("ttl", reason)

    def test_single_attestation_rejected(self):
        entry = AllowlistEntry(fingerprint="fp-x", author="carol",
                               attestations=[_att("alice")])
        ok, _reason = dual_attestation_valid(entry, NOW)
        self.assertFalse(ok)

    def test_gate_rejects_theater_entry(self):
        alert = make_alert()
        bad = _entry(alert.fingerprint, a1="alice", a2="alice")  # same human
        gate, alert, state = _gate_with(allowlist=[bad], p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")

    def test_run_id_shapes(self):
        self.assertTrue(is_linkage_run_id("lrq-9f2c-41ab"))
        self.assertTrue(is_linkage_run_id(
            "550e8400-e29b-41d4-a716-446655440000"))
        self.assertFalse(is_linkage_run_id("see #ops-chat"))
        self.assertFalse(is_linkage_run_id(""))
        self.assertFalse(is_linkage_run_id(None))

    def test_never_one_human_plus_stale_fit(self):
        # The two regimes never mix: one attestation + a stale fit is not
        # a suppression path.
        alert = make_alert()
        entry = AllowlistEntry(fingerprint=alert.fingerprint, author="carol",
                               attestations=[_att("alice")])
        store = FitStore()
        store.put(_fit(n=5000, k=0, computed_days_ago=60))  # stale
        gate, alert, state = _gate_with(allowlist=[entry], fit_store=store,
                                       p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")


# ---------------------------------------------------------------------------
# PM-1: composition shift inside the 0.00 class
# ---------------------------------------------------------------------------

class TestShiftMonitor(unittest.TestCase):
    def _monitor(self):
        # Reference: the fit window behind a (k=0, n=1351) fit.
        return ShiftMonitor(reference_n=1351, reference_k=0,
                            reference_features={"svc-a": 1216, "svc-b": 135})

    def test_confirmed_sev1_trips_immediately(self):
        mon = self._monitor()
        self.assertEqual(mon.status, "OK")
        mon.note_outcome(sev1=False)
        self.assertEqual(mon.status, "OK")
        mon.note_outcome(sev1=True)  # a confirmed false member of the class
        self.assertEqual(mon.status, "TRIPPED")

    def test_composition_shift_trips_within_one_cycle(self):
        mon = self._monitor()
        # New service floods the 0.00 class: 50/50 instead of 90/10.
        mon.note_batch(features=["svc-a"] * 100 + ["svc-new"] * 100,
                       labels=[0] * 200)
        self.assertEqual(mon.status, "TRIPPED")

    def test_stable_class_stays_ok(self):
        mon = self._monitor()
        mon.note_batch(features=["svc-a"] * 180 + ["svc-b"] * 20,
                       labels=[0] * 200)
        self.assertEqual(mon.status, "OK")

    def test_tripped_fit_kills_the_lock(self):
        alert = make_alert()
        fit = _fit(n=5000, k=0, shift="TRIPPED")
        store = FitStore()
        store.put(fit)
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       fit_store=store, p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")


# ---------------------------------------------------------------------------
# PM-4: the n_min ratchet — the bar lives in the versioned contract
# ---------------------------------------------------------------------------

class TestContractFrozen(unittest.TestCase):
    def test_bar_is_module_constant_not_config(self):
        self.assertEqual(SUPPRESS_BAR, 0.002)

    def test_leg1_has_no_tunable_bar_or_n_min(self):
        params = inspect.signature(leg1_prob_lock).parameters
        for forbidden in ("bar", "p_bar", "threshold", "n_min", "min_n",
                          "suppress_p1_max"):
            self.assertNotIn(forbidden, params,
                             f"leg1 must not accept a tunable {forbidden}")

    def test_fit_validity_has_no_tunable_clock(self):
        params = inspect.signature(fit_is_valid).parameters
        for forbidden in ("valid_days", "ttl_days", "window_fresh_days",
                          "bar", "n_min"):
            self.assertNotIn(forbidden, params,
                             f"fit validity must not accept tunable {forbidden}")

    def test_clocks_are_constants(self):
        self.assertEqual(FIT_VALID_DAYS, 30)
        self.assertEqual(FIT_WINDOW_FRESH_DAYS, 7)
        self.assertEqual(ATTESTATION_TTL_MAX_DAYS, 30)
        self.assertEqual(GATE_FORMULA_VERSION, "adr013-v1")

    def test_tuner_fit_has_no_bar_override(self):
        params = inspect.signature(fit_r000_class).parameters
        for forbidden in ("bar", "n_min", "min_n"):
            self.assertNotIn(forbidden, params)


# ---------------------------------------------------------------------------
# PM-5: label pipeline discipline — unlabeled excluded, never folded
# ---------------------------------------------------------------------------

class TestLabelDiscipline(unittest.TestCase):
    def _rows(self, n_labeled, k_sev1, m_unlabeled):
        rows = []
        for _ in range(k_sev1):
            rows.append({"reported_p1": "0.00", "label": 1})
        for _ in range(n_labeled - k_sev1):
            rows.append({"reported_p1": "0.00", "label": 0})
        for _ in range(m_unlabeled):
            rows.append({"reported_p1": "0.00", "label": None})
        # rows outside the class must not enter the fit
        rows.append({"reported_p1": "0.01", "label": 1})
        return rows

    def test_unlabeled_excluded_from_n_and_k(self):
        res = fit_r000_class(self._rows(1351, 0, 400), org="org-c",
                             model_pin=PINNED, window_start="2026-07-05",
                             window_end="2026-10-02",
                             computed_at=NOW.isoformat())
        self.assertEqual(res["n"], 1351)
        self.assertEqual(res["k"], 0)
        self.assertEqual(res["m"], 400)
        # PM-3: folding m into n as negatives would give bound(0,1751) and
        # a FALSE sense of more evidence; assert the exact bound for n=1351.
        self.assertAlmostEqual(res["p_hat_upper"], 0.0019987328, places=9)
        self.assertTrue(res["bar_cleared"])

    def test_outside_class_rows_excluded(self):
        res = fit_r000_class(self._rows(10, 0, 0), org="org-c",
                             model_pin=PINNED, window_start="2026-07-05",
                             window_end="2026-10-02",
                             computed_at=NOW.isoformat())
        self.assertEqual(res["n"], 10)
        self.assertEqual(res["k"], 0)  # the 0.01-class SEV1 did not leak in

    def test_unlabeled_fraction_flag(self):
        res = fit_r000_class(self._rows(100, 0, 400), org="org-c",
                             model_pin=PINNED, window_start="2026-07-05",
                             window_end="2026-10-02",
                             computed_at=NOW.isoformat())
        self.assertGreater(res["unlabeled_fraction"], 0.20)
        self.assertTrue(res["unlabeled_flag"])

    def test_zero_labeled_rows_is_an_error_not_a_fit(self):
        with self.assertRaises(ValueError):
            fit_r000_class(self._rows(0, 0, 50), org="org-c",
                           model_pin=PINNED, window_start="2026-07-05",
                           window_end="2026-10-02",
                           computed_at=NOW.isoformat())

    def test_artifact_carries_n_k_m(self):
        res = fit_r000_class(self._rows(1351, 0, 12), org="org-c",
                             model_pin=PINNED, window_start="2026-07-05",
                             window_end="2026-10-02",
                             computed_at=NOW.isoformat())
        art = res["artifact"]
        self.assertEqual((art.n, art.k, art.m), (1351, 0, 12))
        self.assertTrue(art.verify_binding())


# ---------------------------------------------------------------------------
# §3: the decision-calibration certificate, with teeth
# ---------------------------------------------------------------------------

class TestDecisionCertificate(unittest.TestCase):
    def _decisions(self, n, k, m, mode="executed"):
        ds = [(1, mode)] * k + [(0, mode)] * (n - k) + [(None, mode)] * m
        return ds

    def _cert(self, n, k, m, confirmed=41):
        return decision_certificate(
            self._decisions(n, k, m),
            reference_class="R(C,W,suppress)",
            window=("2026-07-05", "2026-10-02"),
            model_pin=PINNED,
            gate_formula_version=GATE_FORMULA_VERSION,
            shift_status="OK",
            now=NOW,
            confirmed_sev1_in_window=confirmed,
        )

    def test_worked_example_is_flag(self):
        # Doc §3.3: n=380, k=0, m=32 -> u=0.0070699 -> FLAG, not PASS.
        cert = self._cert(380, 0, 32)
        self.assertIsInstance(cert, Certificate)
        self.assertEqual(cert.verdict, "FLAG")
        self.assertAlmostEqual(cert.p_hat_upper, 0.0070699131, places=9)
        self.assertIn("0.0071", cert.statement)
        self.assertFalse(cert.label_rot_risk)  # 32/412 = 7.8% < 20%

    def test_pass_boundary_recomputed(self):
        # PASS needs u <= 0.005 at k=0: z^2/(n+z^2) <= 0.005
        #   -> n >= z^2 * 199 = 538.43... -> first clearing n is 539.
        # (The design doc §3.3 says 541; recomputation shows 539 and 540
        # also clear — the doc's arithmetic was not checked downward.)
        cert538 = self._cert(538, 0, 0)
        self.assertEqual(cert538.verdict, "FLAG")
        self.assertGreater(cert538.p_hat_upper, CERT_PASS_U)
        cert539 = self._cert(539, 0, 0)
        self.assertEqual(cert539.verdict, "PASS")
        self.assertLessEqual(cert539.p_hat_upper, CERT_PASS_U)
        self.assertAlmostEqual(cert539.p_hat_upper, 0.0049947712, places=9)
        cert541 = self._cert(541, 0, 0)
        self.assertEqual(cert541.verdict, "PASS")

    def test_any_sev1_is_fail(self):
        cert = self._cert(2000, 1, 0)
        self.assertEqual(cert.verdict, "FAIL")

    def test_bound_above_1pct_is_fail(self):
        cert = self._cert(300, 0, 0)  # u = 0.00894 > 0.01? no: 0.00894<0.01
        # bound(0,300) = 2.7057/302.7057 = 0.00894 -> FLAG band
        self.assertEqual(cert.verdict, "FLAG")
        cert2 = self._cert(300, 5, 0)  # k>=1 -> FAIL regardless
        self.assertEqual(cert2.verdict, "FAIL")

    def test_insufficient_n(self):
        cert = self._cert(299, 0, 0)
        self.assertEqual(cert.verdict, "INSUFFICIENT-N")
        cert2 = self._cert(500, 0, 0, confirmed=24)
        self.assertEqual(cert2.verdict, "INSUFFICIENT-N")

    def test_label_rot_risk_flagged(self):
        cert = self._cert(300, 0, 200)  # 200/500 = 40% unlabeled
        self.assertTrue(cert.label_rot_risk)
        self.assertIn("LABEL-ROT-RISK", cert.statement)

    def test_denominator_is_decisions_not_alerts(self):
        # Storm-collapsed alerts count once: the caller passes one decision
        # per decision; duplicates in the input would be a caller bug the
        # certificate must not silently absorb — n is exactly len(labeled).
        cert = self._cert(300, 0, 0)
        self.assertEqual(cert.n, 300)
        self.assertEqual(cert.m, 0)

    def test_statement_carries_evidence(self):
        cert = self._cert(541, 0, 0)
        for token in ("541", "0", PINNED, GATE_FORMULA_VERSION):
            self.assertIn(str(token), cert.statement)


# ---------------------------------------------------------------------------
# Fit-path / attestation-path composition with the rest of the policy
# ---------------------------------------------------------------------------

class TestLockComposition(unittest.TestCase):
    def test_fit_suppress_still_needs_confidence(self):
        alert = make_alert()
        store = FitStore()
        store.put(_fit(n=5000, k=0))
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       fit_store=store, p1=0.0, conf=0.85)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")  # leg 2 (conf) fails

    def test_fit_suppress_still_needs_allowlist(self):
        alert = make_alert()
        store = FitStore()
        store.put(_fit(n=5000, k=0))
        gate, alert, state = _gate_with(allowlist=set(),  # not listed
                                       fit_store=store, p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")  # leg 3 fails

    def test_leg1_never_raises_the_gate(self):
        alert = make_alert()
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       fit_store="not-a-store",  # corrupt wiring
                                       p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})  # must not raise
        self.assertNotEqual(disp.action, "suppress")

    def test_org_scoping(self):
        alert = make_alert()
        store = FitStore()
        store.put(_fit(n=5000, k=0, org="org-other"))
        gate, alert, state = _gate_with(allowlist={alert.fingerprint},
                                       fit_store=store, org="org-c",
                                       p1=0.0, conf=0.95)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")  # fit is another org's


if __name__ == "__main__":
    unittest.main()
