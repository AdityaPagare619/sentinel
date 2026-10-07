"""Kill-switch wiring tests for the TRUE receiver topology (P0-1).

The production receiver pages through the legacy sync ``Forwarder``
(receiver.build_pipeline_from_env), not ``DurableForwarder``. These tests
prove, on the receiver topology: kill engaged -> zero sends; re-arm ->
recovery. The kill check is a thread-safe flag read in ``_post`` — the
single send funnel for forward() and forward_raw() — so it never calls
Jev and never waits on the race (control principle).

All "PagerDuty" traffic goes to a loopback CaptureServer. Zero real
PagerDuty contact, ever.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import json
import tempfile
import threading
import unittest
import urllib.request

from sentinel.audit import AuditLog
from sentinel.config import ConfigLoader, record_restart
from sentinel.correlator import Correlator
from sentinel.forwarder import Forwarder
from sentinel.gate import Gate
from sentinel.models import Disposition
from sentinel.receiver import Pipeline, ReceiverConfig, make_server
from sentinel.safety import KillSwitch

from tests.helpers import CaptureServer, make_alert
from tests.test_gate import ExplodingClient, canned, fresh_monitor_for


def _disp(action, reason="threshold"):
    return Disposition(action=action, reason=reason, team="platform",
                       confidence=0.9, latency_ms=1.0)


def _pd_raw():
    return {"routing_key": "rk-abc", "event_action": "trigger",
            "dedup_key": "dk-1",
            "payload": {"summary": "disk full", "source": "web-1",
                        "severity": "critical", "component": "web",
                        "custom_details": {"disk": "/dev/sda1"}}}


class FixedClient:
    """Deterministic stand-in: returns one canned response for every call."""

    def __init__(self, response=None):
        self.response = response
        self.calls = []

    def decide(self, state, questions):
        self.calls.append({"state": state, "questions": questions})
        return self.response


class TestLegacyForwarderKill(unittest.TestCase):
    """The kill switch halts the legacy sync Forwarder (receiver's forwarder)."""

    def setUp(self):
        self.capture = CaptureServer()
        self.addCleanup(self.capture.close)
        self.ks = KillSwitch()
        self.fw = Forwarder(pd_events_url=self.capture.url,
                            kill_switch=self.ks)

    def test_disengaged_sends(self):
        alert = make_alert(raw=_pd_raw())
        result = self.fw.forward(alert, _disp("page_now"),
                                 raw_bytes=json.dumps(_pd_raw()).encode())
        self.assertTrue(result.forwarded)
        self.assertFalse(result.killed)
        self.assertEqual(len(self.capture.requests), 1)

    def test_kill_engaged_absorbs_forward_never_raises(self):
        self.ks.engage(actor="test", reason="drill")
        alert = make_alert(raw=_pd_raw())
        result = self.fw.forward(alert, _disp("page_now"),
                                 raw_bytes=json.dumps(_pd_raw()).encode())
        self.assertFalse(result.forwarded)
        self.assertTrue(result.killed)
        self.assertEqual(result.error, "kill_switch_engaged")
        self.assertIsNone(result.status_code)
        self.assertEqual(self.capture.requests, [])  # zero sends
        self.assertEqual(self.fw.metrics["killed"], 1)

    def test_kill_engaged_absorbs_forward_raw(self):
        # The fail-open raw path (unparseable deliveries) must halt too:
        # it funnels through _post, the same send boundary.
        self.ks.engage(actor="test", reason="drill")
        result = self.fw.forward_raw(b"not-json{{", alert_id="unparseable",
                                     dedup_key="dk-raw")
        self.assertFalse(result.forwarded)
        self.assertTrue(result.killed)
        self.assertEqual(self.capture.requests, [])

    def test_kill_engaged_absorbs_non_pd_shaped(self):
        # Generic alerts build the PD body from a routing key — the kill
        # check fires before any send regardless of body source.
        self.ks.engage(actor="test", reason="drill")
        alert = make_alert(source="generic", raw={"summary": "cpu hot"},
                           alert_id="g1")
        result = self.fw.forward(alert, _disp("page_now"))
        self.assertTrue(result.killed)
        self.assertEqual(self.capture.requests, [])

    def test_suppress_still_suppresses_under_kill(self):
        # Suppress/folded never send; kill changes nothing about them and
        # they must not count as killed.
        self.ks.engage(actor="test", reason="drill")
        alert = make_alert(raw=_pd_raw())
        result = self.fw.forward(alert, _disp("suppress", "allowlist"))
        self.assertFalse(result.forwarded)
        self.assertFalse(result.killed)
        self.assertEqual(self.fw.metrics["suppressed"], 1)
        self.assertEqual(self.fw.metrics["killed"], 0)

    def test_rearm_resumes(self):
        self.ks.engage(actor="test", reason="drill")
        alert = make_alert(raw=_pd_raw())
        killed = self.fw.forward(alert, _disp("page_now"),
                                 raw_bytes=json.dumps(_pd_raw()).encode())
        self.assertTrue(killed.killed)
        self.ks.disengage(actor="test", confirm=True)
        resumed = self.fw.forward(alert, _disp("page_now"),
                                  raw_bytes=json.dumps(_pd_raw()).encode())
        self.assertTrue(resumed.forwarded)
        self.assertFalse(resumed.killed)
        self.assertEqual(len(self.capture.requests), 1)

    def test_no_kill_switch_backward_compatible(self):
        fw = Forwarder(pd_events_url=self.capture.url)
        alert = make_alert(raw=_pd_raw())
        result = fw.forward(alert, _disp("page_now"),
                            raw_bytes=json.dumps(_pd_raw()).encode())
        self.assertTrue(result.forwarded)
        self.assertEqual(len(self.capture.requests), 1)


class TestReceiverTopologyKillWiring(unittest.TestCase):
    """Full receiver topology: HTTP ingress -> Pipeline -> legacy Forwarder.

    Proves the property on the topology the production receiver uses:
    kill engaged -> zero sends on the receiver path; re-arm -> recovery.
    """

    def _build(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        cfgdir = os.path.join(self._tmp.name, "cfg")
        os.makedirs(cfgdir, exist_ok=True)
        with open(os.path.join(cfgdir, "thresholds.json"), "w") as fh:
            json.dump({}, fh)
        with open(os.path.join(cfgdir, "allowlist.json"), "w") as fh:
            json.dump([], fh)
        statedir = os.path.join(self._tmp.name, "state")
        loader = ConfigLoader(config_dir=cfgdir, state_dir=statedir)
        policy = loader.load_startup()
        record_restart(statedir)
        self.pd = CaptureServer()
        self.addCleanup(self.pd.close)
        audit = AuditLog(":memory:")
        # page_now verdict, like TestPdEnqueue in test_receiver.py
        gate = Gate(FixedClient(canned(p1=0.9, conf=0.95)),
                    policy.thresholds, list(policy.allowlist), audit,
                    freshness_monitor=fresh_monitor_for([]))
        self.ks = KillSwitch(log=audit.log)
        forwarder = Forwarder(pd_events_url=self.pd.url,
                              default_routing_key="rk-default",
                              kill_switch=self.ks)
        self.pipeline = Pipeline(Correlator(), gate, forwarder, audit,
                                 ReceiverConfig(webhook_secret="s" * 16),
                                 policy=policy, config_loader=loader,
                                 state_dir=statedir)
        self.pipeline.kill_switch = self.ks
        self.server = make_server(0, self.pipeline)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _stop(self):
        try:
            self.pipeline.health.stop()
        except Exception:
            pass
        self.server.shutdown()
        self.server.server_close()

    def _post(self, path, obj):
        body = json.dumps(obj).encode()
        req = urllib.request.Request(self.base + path, data=body,
                                     method="POST",
                                     headers={"Content-Type":
                                              "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), json.loads(resp.read().decode())

    def _pd_event(self, dedup_key="dk-kill-1"):
        return {"routing_key": "rk-abc", "event_action": "trigger",
                "dedup_key": dedup_key,
                "payload": {"summary": "disk full", "source": "web-1",
                            "severity": "critical", "component": "web",
                            "class": "http_5xx", "region": "us-east"}}

    def test_kill_engaged_zero_sends_on_receiver_path(self):
        self._build()
        code, _ = self._post("/v2/enqueue", self._pd_event("dk-k1"))
        self.assertEqual(code, 200)
        self.assertEqual(len(self.pd.requests), 1)  # baseline: paging works

        self.ks.engage(actor="operator", reason="receiver-topology-drill",
                       drill=True)
        for i in range(3):
            code, _ = self._post("/v2/enqueue",
                                 self._pd_event(f"dk-k2-{i}"))
            self.assertEqual(code, 200)  # receiver still answers; page held
        self.assertEqual(len(self.pd.requests), 1)  # ZERO new sends
        self.assertEqual(self.pipeline.forwarder.metrics["killed"], 3)

    def test_rearm_recovers_receiver_paging(self):
        self._build()
        self.ks.engage(actor="operator", reason="receiver-topology-drill",
                       drill=True)
        self._post("/v2/enqueue", self._pd_event("dk-k3"))
        self.assertEqual(len(self.pd.requests), 0)
        self.ks.disengage(actor="operator", confirm=True)
        code, _ = self._post("/v2/enqueue", self._pd_event("dk-k4"))
        self.assertEqual(code, 200)
        self.assertEqual(len(self.pd.requests), 1)  # recovery: sends resume

    def test_factory_wires_kill_switch_into_receiver_forwarder(self):
        """build_pipeline_from_env must hand the receiver's forwarder the
        same KillSwitch it exposes on the pipeline — the property these
        tests prove must hold for factory-built pipelines too."""
        import sentinel.receiver as receiver_mod
        self._build_factory_env()
        policy = self._factory_policy
        pipeline = receiver_mod.build_pipeline_from_env(policy=policy)
        self.assertIsNotNone(pipeline.kill_switch)
        self.assertIs(pipeline.kill_switch,
                      pipeline.forwarder._kill_switch)
        # TOPOLOGY PIN (audit X-A): the drill numbers were measured on the
        # legacy sync Forwarder's _post kill check. Exact class — not
        # isinstance: a subclass could drop the kill wiring while still
        # being "a Forwarder". If the factory ever changes the paging
        # forwarder, this fails loudly and the drill artifact is
        # invalidated by name.
        self.assertIs(type(pipeline.forwarder), Forwarder,
                      "receiver topology changed: the kill drill was "
                      "measured on the legacy sync Forwarder")

    def test_drill_artifact_schema_and_topology(self):
        """The committed drill artifact is a contract: passed, within its
        threshold, and its topology claim is code-derived — equal to the
        TOPOLOGY constant in the drill script, not prose in the test."""
        import glob
        import importlib.util
        drills = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "ops", "drills")
        paths = sorted(glob.glob(os.path.join(
            drills, "kill-drill-receiver-topology-*.json")))
        self.assertTrue(paths, "no receiver-topology drill artifact found")
        with open(paths[-1], encoding="utf-8") as fh:
            artifact = json.load(fh)
        script = os.path.join(drills, "kill_drill_receiver_topology.py")
        spec = importlib.util.spec_from_file_location(
            "kill_drill_receiver_topology", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(artifact["passed"], "drill artifact did not pass")
        self.assertLessEqual(artifact["measured_ms"],
                             artifact["threshold_ms"],
                             "drill exceeded its own threshold")
        self.assertEqual(artifact["topology"], mod.TOPOLOGY,
                         "artifact topology claim is not code-derived")

    def _build_factory_env(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        cfgdir = os.path.join(self._tmp.name, "cfg")
        os.makedirs(cfgdir, exist_ok=True)
        with open(os.path.join(cfgdir, "thresholds.json"), "w") as fh:
            json.dump({}, fh)
        with open(os.path.join(cfgdir, "allowlist.json"), "w") as fh:
            json.dump([], fh)
        statedir = os.path.join(self._tmp.name, "state")
        loader = ConfigLoader(config_dir=cfgdir, state_dir=statedir)
        self._factory_policy = loader.load_startup()
        record_restart(statedir)
        self.pd = CaptureServer()
        self.addCleanup(self.pd.close)
        # Factory env: mock Jev client (no network, no key), fake webhook
        # secret, stub PD endpoint. No real secrets anywhere.
        os.environ["SENTINEL_MOCK"] = "1"
        os.environ["SENTINEL_WEBHOOK_SECRET"] = "drill-secret-0123456789"
        os.environ["PD_EVENTS_URL"] = self.pd.url
        os.environ["SENTINEL_DB"] = os.path.join(self._tmp.name, "s.db")
        self.addCleanup(os.environ.pop, "SENTINEL_MOCK", None)
        self.addCleanup(os.environ.pop, "SENTINEL_WEBHOOK_SECRET", None)
        self.addCleanup(os.environ.pop, "PD_EVENTS_URL", None)
        self.addCleanup(os.environ.pop, "SENTINEL_DB", None)


if __name__ == "__main__":
    unittest.main()
