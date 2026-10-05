"""R-10: flags.json wiring — loader, gate, and receiver tests.

The flags.json kill switch was fictional (zero readers in src/); these
tests pin the wire: the 4-stage load validates flags.json, the live gate
honors the three behavioral flags, and the flip procedure (flagctl ->
/-/reload -> behavior change) is asserted end-to-end, not believed.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sentinel.audit import AuditLog
from sentinel.config import (ConfigLoader, ConfigRejected, FLAG_DEFAULTS,
                             FLAGS_VERSION)
from sentinel.gate import Gate
from sentinel.models import Thresholds
from sentinel.quantized import AllowlistEntry, Attestation
from sentinel.state import build_state, input_sha256

from tests.helpers import make_alert, write_flags_json
from tests.test_gate import (GateTestBase, MockSystemOneClient, canned,
                             fresh_monitor_for)
from tests.test_liveness import LivenessTestBase
from tests.test_receiver import FixedClient


def _write_thresholds(cfgdir, data=None):
    with open(os.path.join(cfgdir, "thresholds.json"), "w") as fh:
        json.dump(data or {}, fh)


def _write_allowlist(cfgdir, fps=()):
    with open(os.path.join(cfgdir, "allowlist.json"), "w") as fh:
        json.dump(sorted(fps), fh)


def _loader(cfgdir=None, **flag_overrides):
    tmp = tempfile.mkdtemp(prefix="sentinel-flags-")
    cfgdir = cfgdir or os.path.join(tmp, "cfg")
    os.makedirs(cfgdir, exist_ok=True)
    _write_thresholds(cfgdir)
    _write_allowlist(cfgdir)
    write_flags_json(cfgdir, **flag_overrides)
    return ConfigLoader(config_dir=cfgdir,
                        state_dir=os.path.join(tmp, "state"))


class TestFlagsLoader(unittest.TestCase):
    def test_valid_flags_load(self):
        policy = _loader().load_startup()
        self.assertEqual(policy.flags["global_kill_switch"], False)
        self.assertEqual(policy.flags["suppress_enabled"], True)
        self.assertEqual(policy.flags["shadow_mode"], False)
        self.assertEqual(policy.flags["canary_severity_bands"], [])
        self.assertEqual(policy.flags["canary_services"], [])

    def test_missing_flags_refuses_start(self):
        # Fail-closed: a kill switch that silently doesn't exist is the
        # rusted-shut failure mode.
        loader = _loader()
        os.remove(os.path.join(loader.config_dir, "flags.json"))
        with self.assertRaises(ConfigRejected):
            loader.load_startup()

    def test_unknown_flag_rejected(self):
        loader = _loader()
        path = os.path.join(loader.config_dir, "flags.json")
        with open(path) as fh:
            data = json.load(fh)
        data["flags"]["nope"] = {"value": True}
        with open(path, "w") as fh:
            json.dump(data, fh)
        with self.assertRaises(ConfigRejected):
            loader.load_startup()

    def test_missing_flag_rejected(self):
        loader = _loader()
        path = os.path.join(loader.config_dir, "flags.json")
        with open(path) as fh:
            data = json.load(fh)
        del data["flags"]["shadow_mode"]
        with open(path, "w") as fh:
            json.dump(data, fh)
        with self.assertRaises(ConfigRejected):
            loader.load_startup()

    def test_wrong_type_rejected(self):
        loader = _loader()
        path = os.path.join(loader.config_dir, "flags.json")
        with open(path) as fh:
            data = json.load(fh)
        data["flags"]["global_kill_switch"]["value"] = "true"  # str, not bool
        with open(path, "w") as fh:
            json.dump(data, fh)
        with self.assertRaises(ConfigRejected):
            loader.load_startup()

    def test_version_mismatch_rejected(self):
        loader = _loader()
        path = os.path.join(loader.config_dir, "flags.json")
        with open(path) as fh:
            data = json.load(fh)
        data["version"] = FLAGS_VERSION + 1
        with open(path, "w") as fh:
            json.dump(data, fh)
        with self.assertRaises(ConfigRejected):
            loader.load_startup()

    def test_invalid_flags_reload_rejected_live_untouched(self):
        # The reload-rejection drill in unit form: invalid flags ->
        # ConfigRejected, the live generation is untouched.
        loader = _loader()
        first = loader.load_startup()
        path = os.path.join(loader.config_dir, "flags.json")
        with open(path, "w") as fh:
            fh.write("{oops")
        with self.assertRaises(ConfigRejected):
            loader.reload()
        self.assertIs(loader.current, first)
        self.assertEqual(loader.current.flags["global_kill_switch"], False)

    def test_nonempty_canary_rejected(self):
        # Fail-closed: arming a canary the kernel cannot honor is
        # rejected, loudly, with the follow-up named — never
        # validate-and-ignore theater.
        loader = _loader()
        path = os.path.join(loader.config_dir, "flags.json")
        with open(path) as fh:
            data = json.load(fh)
        data["flags"]["canary_severity_bands"]["value"] = ["warning"]
        with open(path, "w") as fh:
            json.dump(data, fh)
        with self.assertRaises(ConfigRejected) as ctx:
            loader.load_startup()
        self.assertIn("R-15", str(ctx.exception))

    def test_flags_flip_reload_bumps_generation(self):
        loader = _loader()
        first = loader.load_startup()
        write_flags_json(loader.config_dir, global_kill_switch=True)
        second = loader.reload()
        self.assertEqual(second.generation, first.generation + 1)
        self.assertTrue(second.flags["global_kill_switch"])
        self.assertNotEqual(second.source_sha256, first.source_sha256)

    def test_flags_round_trip_last_good(self):
        loader = _loader()
        loader.load_startup()
        write_flags_json(loader.config_dir, global_kill_switch=True)
        flipped = loader.reload()
        # Lose the flags file: startup falls back to last-good WITH flags.
        os.remove(os.path.join(loader.config_dir, "flags.json"))
        loader2 = ConfigLoader(config_dir=loader.config_dir,
                               state_dir=loader.state_dir)
        restored = loader2.load_startup()
        self.assertEqual(restored.generation, flipped.generation)
        self.assertTrue(restored.flags["global_kill_switch"])

    def test_flags_edit_trips_config_current(self):
        import time
        loader = _loader()
        loader.load_startup()
        self.assertFalse(loader.config_files_changed())
        time.sleep(0.02)
        write_flags_json(loader.config_dir, suppress_enabled=False)
        self.assertTrue(loader.config_files_changed())


def _attested(fingerprint, author="carol"):
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    return AllowlistEntry(
        fingerprint=fingerprint, author=author,
        attestations=[
            Attestation("alice", now - timedelta(days=1),
                        "lrq-9f2c-41ab", 30),
            Attestation("bob", now - timedelta(days=1),
                        "lrq-9f2c-41ab", 30),
        ])


class TestFlagsGate(GateTestBase):
    def _suppress_gate(self, flags=None):
        """A gate whose triple lock HOLDS (would suppress without flags)."""
        alert = make_alert()
        state = build_state(alert, {}, {})
        client = MockSystemOneClient(
            {input_sha256(state): canned(p1=0.0, conf=0.95)})
        self.audit = AuditLog(":memory:")
        gate = Gate(client, Thresholds(), [_attested(alert.fingerprint)],
                    self.audit,
                    freshness_monitor=fresh_monitor_for([alert.fingerprint]),
                    flags=flags)
        return gate, client, alert, state

    def test_defaults_preserve_behavior(self):
        # No flags wired: the triple lock still suppresses (the safe
        # default is the previous behavior).
        gate, _c, alert, state = self._suppress_gate()
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")

    def test_flag_defaults_when_gate_built_without_flags(self):
        gate, _c, alert, state = self._suppress_gate()
        for name, default in FLAG_DEFAULTS.items():
            self.assertEqual(gate._flag(name), default)

    def test_kill_switch_passthrough_no_jev_call(self):
        gate, client, alert, state = self._suppress_gate(
            flags={"global_kill_switch": True})
        disp, rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "passthrough")
        self.assertEqual(disp.reason, "kill_switch")
        # The race is never armed: zero vendor calls.
        self.assertEqual(len(client.calls), 0)
        # The audit invariant holds: the row is still written.
        self.assertEqual(rec.disposition.action, "passthrough")
        self.assertEqual(rec.disposition.reason, "kill_switch")

    def test_kill_switch_beats_suppress_eligible_alert(self):
        # Even an alert that WOULD suppress goes passthrough.
        gate, _c, alert, state = self._suppress_gate(
            flags={"global_kill_switch": True})
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")
        self.assertEqual((disp.action, disp.reason),
                         ("passthrough", "kill_switch"))

    def test_suppress_disabled_downgrades_kernel_suppress(self):
        gate, _c, alert, state = self._suppress_gate(
            flags={"suppress_enabled": False})
        disp, rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "passthrough")
        self.assertEqual(disp.reason, "suppress_disabled")
        self.assertEqual(rec.disposition.reason, "suppress_disabled")

    def test_suppress_disabled_downgrades_dedup(self):
        alert = make_alert()
        state = build_state(alert, {}, {})
        client = MockSystemOneClient({})
        self.audit = AuditLog(":memory:")
        prior = SimpleNamespace(action="suppress", team="platform",
                                confidence=0.95)
        correlation = SimpleNamespace(kind="duplicate", prior=prior)
        gate = Gate(client, Thresholds(), [], self.audit,
                    flags={"suppress_enabled": False})
        disp, _rec = gate.evaluate(alert, state, {}, {},
                                   correlation=correlation)
        self.assertEqual((disp.action, disp.reason),
                         ("passthrough", "suppress_disabled"))
        # Enabled (default): the dedup-inherited suppress still flows.
        gate2 = Gate(client, Thresholds(), [], AuditLog(":memory:"))
        disp2, _rec2 = gate2.evaluate(alert, state, {}, {},
                                      correlation=correlation)
        self.assertEqual((disp2.action, disp2.reason),
                         ("suppress", "dedup"))

    def test_shadow_mode_flag(self):
        alert = make_alert()
        gate, _c, state = self.scripted_gate(
            alert, canned(p1=0.9, conf=0.95))
        gate.flags = {"shadow_mode": True}
        disp, rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "passthrough")
        self.assertEqual(disp.reason, "shadow")
        # The would-be disposition is audited.
        self.assertEqual(rec.disposition.action, "page_now")
        self.assertEqual(rec.disposition.reason, "shadow")

    def test_digest_storm_unaffected_by_kill_switch(self):
        # Documented boundary: digest_storm cannot suppress by
        # construction, so the kill switch (which defeats suppression)
        # does not alter it.
        alert = make_alert()
        state = build_state(alert, {}, {})
        client = MockSystemOneClient({})
        gate = Gate(client, Thresholds(), [], AuditLog(":memory:"),
                    flags={"global_kill_switch": True})
        disp, _rec = gate.digest_storm(alert, state, storm_size=50,
                                       storm_counts={"critical": 50})
        self.assertEqual(disp.action, "page_now")


class TestFlagsReceiver(unittest.TestCase):
    def test_apply_policy_swaps_flags(self):
        # The rusted-shut regression test: apply_policy must propagate
        # flags to the gate, not just thresholds/allowlist.
        from sentinel.receiver import Pipeline
        from sentinel.correlator import Correlator
        from sentinel.forwarder import Forwarder
        from sentinel.receiver import ReceiverConfig
        loader = _loader()
        policy = loader.load_startup()
        gate = SimpleNamespace(thresholds=None, allowlist=None, flags={})
        pipeline = Pipeline(Correlator(), gate,
                            Forwarder(pd_events_url="http://127.0.0.1:1",
                                      default_routing_key="rk"),
                            AuditLog(":memory:"), ReceiverConfig(),
                            policy=policy, config_loader=loader,
                            state_dir=loader.state_dir)
        write_flags_json(loader.config_dir, global_kill_switch=True,
                         suppress_enabled=False)
        new_policy = loader.reload()
        pipeline.apply_policy(new_policy)
        self.assertTrue(gate.flags["global_kill_switch"])
        self.assertFalse(gate.flags["suppress_enabled"])

    def test_healthz_shows_flags_and_reload_swaps_them(self):
        base = LivenessTestBase()
        base._start(FixedClient(canned(p1=0.9, conf=0.95)))
        self.addCleanup(base._stop)
        code, health = base._get_json("/healthz")
        self.assertEqual(code, 200)
        self.assertIn("flags", health)
        self.assertFalse(health["flags"]["global_kill_switch"])
        # Flip through the sanctioned path: flags.json + /-/reload.
        write_flags_json(base.cfgdir, global_kill_switch=True)
        code, body = base._post_json("/-/reload", b"")
        self.assertEqual(code, 200)
        self.assertTrue(base.pipeline.gate.flags["global_kill_switch"])
        code, health = base._get_json("/healthz")
        self.assertEqual(code, 200)
        self.assertTrue(health["flags"]["global_kill_switch"])
        # The live gate honors it: the alert round-trips as passthrough.
        alert = make_alert()
        disp, _rec = base.pipeline.gate.evaluate(
            alert, build_state(alert, {}, {}), {}, {})
        self.assertEqual(disp.reason, "kill_switch")


if __name__ == "__main__":
    unittest.main()
