"""Tests for the D9 counterfactual receipt (ADR-023).

The receipt is computed at event-write time in ``Gate._on_answered``, on
the live suppress path only, and written into ``decision_made``'s
``threshold_counterfactual`` body key. It NEVER affects the disposition.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import json
import tempfile
import unittest
from datetime import datetime, timezone

import sentinel.gate as gate_mod
from sentinel.audit import AuditLog
from sentinel.client import MockSystemOneClient
from sentinel.config import ConfigLoader, ConfigRejected
from sentinel.counterfactual import (
    NO_SUPPRESS_LEG,
    normalize_counterfactual_preset,
    validate_counterfactual_presets,
)
from sentinel.gate import Gate, evaluate_policy
from sentinel.models import Thresholds
from sentinel.state import build_state, input_sha256

from tests.helpers import make_alert
from tests.test_corroboration import NOW, _attested_entry
from tests.test_gate import canned, fresh_monitor_for


def _write_thresholds(tmpdir, data):
    path = os.path.join(tmpdir, "thresholds.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


class CounterfactualGateBase(unittest.TestCase):
    """Suppress-eligible gate: dual-attested entry (carries the quantized
    leg-1 interim path AND the allowlist_attestation corroboration) +
    all-fresh proofs + fixed clock."""

    def make_gate(self, presets=(), policy_gate=None, resp=None):
        self.audit = AuditLog(":memory:")
        alert = make_alert()
        state = build_state(alert, {}, {})
        resp = resp if resp is not None else canned(p1=0.0, conf=0.95)
        client = MockSystemOneClient({input_sha256(state): resp})
        t = Thresholds(
            counterfactual_presets=validate_counterfactual_presets(
                list(presets)))
        self.gate = Gate(
            client, t, [_attested_entry(alert.fingerprint)], self.audit,
            pinned_model="jev-1.13.0", org="org-c", clock=lambda: NOW,
            freshness_monitor=fresh_monitor_for([alert.fingerprint]),
            policy_gate=policy_gate)
        self.addCleanup(self.gate.close)
        return alert, state, resp

    def decision_body(self):
        bodies = [p["body"] for p in self.gate.emitted
                  if p.get("type") == "decision_made"]
        self.assertEqual(len(bodies), 1)
        return bodies[0]

    def receipt(self):
        cf = self.decision_body()["threshold_counterfactual"]
        self.assertIsNotNone(cf)
        return cf


class TestReceiptOnSuppressPath(CounterfactualGateBase):
    def test_suppress_writes_receipt_at_event_time(self):
        alert, state, _resp = self.make_gate(presets=[0.99])
        disp, _rec = self.gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        cf = self.receipt()
        for key in ("policy", "evaluated_at", "presets"):
            self.assertIn(key, cf)
        self.assertTrue(cf["evaluated_at"])
        names = [p["name"] for p in cf["presets"]]
        # The definitional counterfactual is always computed first.
        self.assertEqual(names, ["no_suppress_leg", "conf>=0.99"])

    def test_no_suppress_leg_is_load_bearing(self):
        # p3/p4 dominant, conf 0.95: without the suppress branch the alert
        # pages business-hours — the suppress leg earned its keep here
        # (it was NOT a would-be passthrough).
        alert, state, _resp = self.make_gate()
        disp, _rec = self.gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        no_leg = self.receipt()["presets"][0]
        self.assertEqual(no_leg["name"], NO_SUPPRESS_LEG)
        self.assertEqual(no_leg["disposition"], "page_business_hours")
        self.assertEqual(no_leg["reason"], "threshold")
        # The kernel did not say suppress under the preset: the leg never
        # ran, so its verdict is null (not an invented failure).
        self.assertIsNone(no_leg["corroboration"])
        self.assertIsNone(no_leg["unresolvable"])

    def test_strict_preset_falls_through(self):
        alert, state, _resp = self.make_gate(
            presets=[{"name": "strict", "suppress_conf_min": 0.99}])
        disp, _rec = self.gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")  # live decision untouched
        strict = self.receipt()["presets"][1]
        self.assertEqual(strict["name"], "strict")
        # conf 0.95 < 0.99: the suppress conjunction fails under the
        # preset — the alert would have paged business-hours.
        self.assertEqual(strict["disposition"], "page_business_hours")
        self.assertIsNone(strict["corroboration"])

    def test_drop_evidence_preset_pages_uncorroborated(self):
        alert, state, _resp = self.make_gate(
            presets=[{"name": "no_evidence", "drop_evidence": True}])
        disp, _rec = self.gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        preset = self.receipt()["presets"][1]
        self.assertEqual(preset["name"], "no_evidence")
        # The kernel still says suppress, but the leg with no evidence
        # fails closed — exactly the live path's uncorroborated outcome.
        self.assertEqual(preset["disposition"], "page_now")
        self.assertEqual(preset["reason"], "uncorroborated")
        self.assertIsNotNone(preset["corroboration"])
        self.assertFalse(preset["corroboration"]["passed"])

    def test_matching_floor_version_resolves(self):
        alert, state, _resp = self.make_gate(
            presets=[{"name": "floor_v1", "description": "current floor",
                       "silence_floor_version": 1}])
        disp, _rec = self.gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        preset = self.receipt()["presets"][1]
        self.assertIsNone(preset["unresolvable"])
        # Same floor the live decision used: the leg re-passes.
        self.assertEqual(preset["disposition"], "suppress")
        self.assertTrue(preset["corroboration"]["passed"])

    def test_other_floor_version_is_unresolvable(self):
        alert, state, _resp = self.make_gate(
            presets=[{"name": "floor_v9", "silence_floor_version": 9}])
        disp, _rec = self.gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        preset = self.receipt()["presets"][1]
        # Honest limitation, stated in the receipt: the floor store is
        # current-version-only — the receipt never invents a floor.
        self.assertIsNotNone(preset["unresolvable"])
        self.assertIn("not retained", preset["unresolvable"])
        self.assertIsNone(preset["disposition"])
        self.assertIsNone(preset["reason"])
        self.assertIsNone(preset["corroboration"])

    def test_reserved_name_override_replaces_builtin(self):
        alert, state, _resp = self.make_gate(presets=[
            {"name": "no_suppress_leg", "description": "operator's own",
             "suppress_conf_min": 0.5},
        ])
        disp, _rec = self.gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        presets = self.receipt()["presets"]
        self.assertEqual(len(presets), 1)
        self.assertEqual(presets[0]["name"], "no_suppress_leg")
        self.assertEqual(presets[0]["description"], "operator's own")
        # The operator's axes apply: conf 0.95 >= 0.5, suppress branch
        # present — the leg re-passes under the same evidence.
        self.assertEqual(presets[0]["disposition"], "suppress")


class TestPolicyPin(CounterfactualGateBase):
    def test_no_policy_gate_wired_pin_is_named(self):
        alert, state, _resp = self.make_gate()
        disp, _rec = self.gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        pin = self.receipt()["policy"]
        self.assertEqual(pin["policy_id"], "suppression")
        # "No pin" is itself information — named, not omitted.
        self.assertIsNone(pin["version"])
        self.assertIsNone(pin["state"])
        self.assertFalse(pin["frozen"])
        self.assertEqual(pin["pin_error"], "no_policy_gate_wired")

    def test_live_policy_version_pinned(self):
        import sentinel.policy_lifecycle as pl
        from tests.test_watchdog import _walk_to_live
        d = tempfile.mkdtemp(prefix="sentinel-cf-pin-")
        path = os.path.join(d, "policy-state.json")
        store = pl.PolicyStore(path)
        v = _walk_to_live(store)
        store.save()  # PolicyStore persistence is explicit (save/load)
        gate_policy = pl.PolicyGate(path)
        alert, state, _resp = self.make_gate(policy_gate=gate_policy)
        disp, _rec = self.gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        pin = self.receipt()["policy"]
        self.assertEqual(pin["policy_id"], "suppression")
        self.assertEqual(pin["version"], v.version)
        self.assertEqual(pin["state"], "live")
        self.assertFalse(pin["frozen"])
        self.assertNotIn("pin_error", pin)

    def test_missing_policy_file_pin_records_failure(self):
        import sentinel.policy_lifecycle as pl
        gate_policy = pl.PolicyGate("/nonexistent/policy-state.json")
        alert, state, _resp = self.make_gate(policy_gate=gate_policy)
        # can_suppress fails open when the file was never set up — the
        # suppress still flows; the pin says what it couldn't pin.
        disp, _rec = self.gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        pin = self.receipt()["policy"]
        self.assertIsNone(pin["version"])
        self.assertEqual(pin["pin_error"], "policy_gate_not_configured")

    def test_version_info_never_raises(self):
        import sentinel.policy_lifecycle as pl
        gate_policy = pl.PolicyGate("/nonexistent/policy-state.json")
        pin = gate_policy.version_info("suppression")
        self.assertIn("pin_error", pin)
        # A gate double without version_info at all degrades the same way.
        class NoVersionInfo:
            policy_id = "suppression"
            policy_gate = None
        from sentinel.counterfactual import policy_pin
        pin = policy_pin(NoVersionInfo())
        self.assertEqual(pin["pin_error"], "no_policy_gate_wired")


class TestDR26SameKernel(CounterfactualGateBase):
    """DR-26: the receipt IS the kernel evaluated N+1 times — there is no
    mirror. Each preset entry must equal a direct ``evaluate_policy`` call
    with the same inputs and the preset's flags."""

    def _kernel_kwargs(self, alert, resp):
        return dict(
            jev_model="jev-1.13.0",
            q_severity=resp.answers["severity"],
            q_team=resp.answers["owning_team"],
            q_disposition=resp.answers["disposition"],
            thresholds=self.gate.thresholds,
            allowlist={alert.fingerprint},
            latency_ms=0.0,
            prob_lock_pass=True,  # the live decision suppressed: it held
            freshness_report=self.gate._freshness_report())

    def test_preset_entries_equal_direct_kernel_calls(self):
        alert, state, resp = self.make_gate(presets=[
            {"name": "strict", "suppress_conf_min": 0.99},
            0.85,
        ])
        disp, _rec = self.gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        by_name = {p["name"]: p for p in self.receipt()["presets"]}
        kw = self._kernel_kwargs(alert, resp)

        no_leg = evaluate_policy(alert, suppress_leg_enabled=False, **kw)
        self.assertEqual(by_name["no_suppress_leg"]["disposition"],
                         no_leg.action)
        self.assertEqual(by_name["no_suppress_leg"]["reason"], no_leg.reason)

        strict = evaluate_policy(alert, suppress_conf_min_override=0.99,
                                 **kw)
        self.assertEqual(by_name["strict"]["disposition"], strict.action)
        self.assertEqual(by_name["strict"]["reason"], strict.reason)

        loose = evaluate_policy(alert, suppress_conf_min_override=0.85,
                                **kw)
        # conf 0.95 >= 0.85: the kernel still says suppress under this
        # preset — the composition runs the leg and it re-passes.
        self.assertEqual(by_name["conf>=0.85"]["disposition"], "suppress")
        self.assertEqual(by_name["conf>=0.85"]["disposition"], loose.action)
        self.assertTrue(by_name["conf>=0.85"]["corroboration"]["passed"])

    def test_kernel_axes_are_parameters_not_wrappers(self):
        # The pre-mortem's structural guarantee: the counterfactual axes
        # live ON evaluate_policy's signature — no wrapper function exists
        # to drift.
        import inspect
        sig = inspect.signature(evaluate_policy)
        self.assertIn("suppress_leg_enabled", sig.parameters)
        self.assertIn("suppress_conf_min_override", sig.parameters)
        self.assertTrue(sig.parameters["suppress_leg_enabled"].default)


class TestReceiptNeverAltersDisposition(CounterfactualGateBase):
    def test_disposition_identical_with_and_without_presets(self):
        alert1, state1, _r1 = self.make_gate(
            presets=[0.99, {"name": "no_evidence", "drop_evidence": True}])
        disp1, _rec1 = self.gate.evaluate(alert1, state1, {}, {})
        alert2, state2, _r2 = self.make_gate()
        disp2, _rec2 = self.gate.evaluate(alert2, state2, {}, {})
        self.assertEqual(disp1.action, disp2.action)
        self.assertEqual(disp1.reason, disp2.reason)
        self.assertEqual(disp1.action, "suppress")

    def test_throwing_preset_axis_still_emits_suppress(self):
        # Pre-mortem #1: preset evaluation throws on the hot path — the
        # decision stands and the payload carries an error receipt.
        alert, state, _resp = self.make_gate(
            presets=[{"name": "strict", "suppress_conf_min": 0.99}])
        real = self.gate._resolve_suppress_path

        def flaky(*a, **k):
            if k.get("suppress_leg_enabled") is False:
                raise RuntimeError("kernel exploded on the counterfactual")
            return real(*a, **k)

        self.gate._resolve_suppress_path = flaky
        try:
            disp, _rec = self.gate.evaluate(alert, state, {}, {})
        finally:
            self.gate._resolve_suppress_path = real
        self.assertEqual(disp.action, "suppress")
        cf = self.decision_body()["threshold_counterfactual"]
        self.assertEqual(cf["presets"], [])
        self.assertIn("error", cf)
        self.assertIn("kernel exploded", cf["error"])
        self.assertIn("policy", cf)  # the pin is still taken

    def test_throwing_receipt_builder_still_emits_suppress(self):
        real = gate_mod.build_counterfactual_receipt

        def boom(*a, **k):
            raise RuntimeError("receipt builder exploded")

        gate_mod.build_counterfactual_receipt = boom
        try:
            alert, state, _resp = self.make_gate()
            disp, _rec = self.gate.evaluate(alert, state, {}, {})
        finally:
            gate_mod.build_counterfactual_receipt = real
        # Even a total builder failure cannot sink the suppression — the
        # payload carries null (the emit call itself never got a receipt).
        self.assertEqual(disp.action, "suppress")
        body = self.decision_body()
        self.assertIsNone(body["threshold_counterfactual"])


class TestNoReceiptOffSuppressPath(CounterfactualGateBase):
    def test_page_and_passthrough_carry_no_receipt(self):
        calls = []
        real = gate_mod.build_counterfactual_receipt

        def counting(*a, **k):
            calls.append(1)
            return real(*a, **k)

        gate_mod.build_counterfactual_receipt = counting
        try:
            # page_now: p1+p2 breaches the page floor.
            alert = make_alert(alert_id="page-1")
            state = build_state(alert, {}, {})
            client = MockSystemOneClient(
                {input_sha256(state): canned(p1=0.9, conf=0.95)})
            gate = Gate(client, Thresholds(), [], AuditLog(":memory:"),
                        freshness_monitor=fresh_monitor_for(
                            [alert.fingerprint]))
            self.addCleanup(gate.close)
            disp, _rec = gate.evaluate(alert, state, {}, {})
            self.assertEqual(disp.action, "page_now")

            # passthrough: the model cannot determine.
            alert2 = make_alert(alert_id="pass-1")
            state2 = build_state(alert2, {}, {})
            client2 = MockSystemOneClient(
                {input_sha256(state2): canned(q3_choice="cannot_determine")})
            gate2 = Gate(client2, Thresholds(), [], AuditLog(":memory:"),
                         freshness_monitor=fresh_monitor_for(
                             [alert2.fingerprint]))
            self.addCleanup(gate2.close)
            disp2, _rec2 = gate2.evaluate(alert2, state2, {}, {})
            self.assertEqual(disp2.action, "passthrough")
        finally:
            gate_mod.build_counterfactual_receipt = real

        for g in (gate, gate2):
            bodies = [p["body"] for p in g.emitted
                      if p.get("type") == "decision_made"]
            self.assertEqual(len(bodies), 1)
            # Zero hot-path cost: the field stays null, the builder never
            # ran.
            self.assertIsNone(bodies[0]["threshold_counterfactual"])
        self.assertEqual(calls, [])


class TestPresetConfig(CounterfactualGateBase):
    def _load(self, thresholds_data):
        d = tempfile.mkdtemp(prefix="sentinel-cf-cfg-")
        _write_thresholds(d, thresholds_data)
        loader = ConfigLoader(config_dir=d,
                              state_dir=tempfile.mkdtemp(
                                  prefix="sentinel-cf-state-"))
        return loader.load_startup()

    def test_presets_load_from_config(self):
        policy = self._load({"counterfactual_presets": [
            0.95,
            {"name": "strict", "description": "floor higher?",
             "suppress_conf_min": 0.99},
            {"name": "no_evidence", "drop_evidence": True},
            {"name": "floor_v3", "silence_floor_version": 3},
        ]})
        presets = policy.thresholds.counterfactual_presets
        self.assertEqual([p["name"] for p in presets],
                         ["conf>=0.95", "strict", "no_evidence", "floor_v3"])
        self.assertEqual(presets[1]["suppress_conf_min"], 0.99)
        self.assertEqual(presets[1]["description"], "floor higher?")
        self.assertTrue(presets[2]["drop_evidence"])
        self.assertEqual(presets[3]["silence_floor_version"], 3)

    def test_absent_presets_default_empty(self):
        policy = self._load({})
        self.assertEqual(policy.thresholds.counterfactual_presets, ())

    def test_invalid_presets_reject_config(self):
        bad = [
            # no name
            [{"suppress_conf_min": 0.9}],
            # empty name
            [{"name": "  ", "suppress_conf_min": 0.9}],
            # unknown key (typo guard)
            [{"name": "x", "suppress_confidence_min": 0.9}],
            # duplicate names
            [{"name": "a", "suppress_conf_min": 0.9}, {"name": "a"}],
            # conf floor out of range
            [{"name": "x", "suppress_conf_min": 1.5}],
            [{"name": "x", "suppress_conf_min": 0.0}],
            # non-boolean boolean
            [{"name": "x", "drop_evidence": "yes"}],
            # non-positive-int floor version
            [{"name": "x", "silence_floor_version": 0}],
            [{"name": "x", "silence_floor_version": True}],
            # bare number out of range
            [1.5],
            [0.0],
            # not a list
            "0.95",
            # not a number or object
            [None],
        ]
        for raw in bad:
            with self.assertRaises(ConfigRejected,
                                   msg=f"should reject {raw!r}"):
                self._load({"counterfactual_presets": raw})

    def test_unknown_threshold_keys_still_rejected(self):
        with self.assertRaises(ConfigRejected):
            self._load({"bogus_key": 1.0})

    def test_presets_survive_last_good_round_trip(self):
        d = tempfile.mkdtemp(prefix="sentinel-cf-cfg-")
        state_d = tempfile.mkdtemp(prefix="sentinel-cf-state-")
        _write_thresholds(d, {"counterfactual_presets": [0.95]})
        loader = ConfigLoader(config_dir=d, state_dir=state_d)
        first = loader.load_startup()
        # Persisted generation round-trips through to_dict / from_dict.
        restored = loader._newest_last_good()
        self.assertIsNotNone(restored)
        self.assertEqual(
            [p["name"] for p in restored.thresholds.counterfactual_presets],
            [p["name"] for p in first.thresholds.counterfactual_presets])

    def test_normalize_bare_numbers(self):
        self.assertEqual(
            normalize_counterfactual_preset(0.85),
            {"name": "conf>=0.85", "suppress_conf_min": 0.85})


if __name__ == "__main__":
    unittest.main()
