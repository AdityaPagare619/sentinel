"""Liveness lane tests (design 05): probes, receiver contract, config validation.

All tests hit a real loopback ThreadingHTTPServer in-process. A second
loopback CaptureServer stands in for the PagerDuty Events API.

Required proofs:
  * 503-not-429  — overload the receiver, assert 503 (never 429).
  * livez/healthz split — kill a dependency: /livez stays 200 while
    /healthz goes 503 naming the failed predicate.
  * config validation — bad config with no last-good -> the process
    refuses to start (fail-closed).
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import http.client
import json
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from sentinel.audit import AuditLog
from sentinel.config import (ConfigLoader, ConfigRejected, count_restarts_10m,
                             record_restart)
from sentinel.correlator import Correlator, fingerprint_for
from sentinel.forwarder import Forwarder
from sentinel.gate import Gate
from sentinel.models import Thresholds
from sentinel.receiver import Pipeline, ReceiverConfig, make_server

from tests.helpers import CaptureServer
from tests.test_gate import canned
from tests.test_receiver import FixedClient, _pd_event


class LivenessTestBase(unittest.TestCase):
    def _write_configs(self, cfgdir, thresholds=None, allowlist=None,
                       raw_thresholds=None):
        os.makedirs(cfgdir, exist_ok=True)
        with open(os.path.join(cfgdir, "thresholds.json"), "w") as fh:
            if raw_thresholds is not None:
                fh.write(raw_thresholds)
            else:
                json.dump(thresholds or {}, fh)
        with open(os.path.join(cfgdir, "allowlist.json"), "w") as fh:
            json.dump(sorted(allowlist or []), fh)

    def _start(self, jev_client, thresholds=None, allowlist=None,
               max_inflight=None, raw_thresholds=None):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfgdir = os.path.join(self._tmp.name, "cfg")
        self.statedir = os.path.join(self._tmp.name, "state")
        self._write_configs(self.cfgdir, thresholds, allowlist,
                            raw_thresholds)
        self.loader = ConfigLoader(config_dir=self.cfgdir,
                                   state_dir=self.statedir)
        self.policy = self.loader.load_startup()
        record_restart(self.statedir)
        self.pd = CaptureServer()
        audit = AuditLog(":memory:")
        gate = Gate(jev_client, self.policy.thresholds,
                    set(self.policy.allowlist), audit)
        forwarder = Forwarder(pd_events_url=self.pd.url,
                              default_routing_key="rk-default")
        config = ReceiverConfig()
        self.pipeline = Pipeline(Correlator(), gate, forwarder, audit, config,
                                 policy=self.policy,
                                 config_loader=self.loader,
                                 state_dir=self.statedir)
        self.server = make_server(0, self.pipeline, max_inflight=max_inflight)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _stop(self):
        if getattr(self, "pipeline", None) is not None:
            try:
                self.pipeline.health.stop()
            except Exception:
                pass
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.pd.close()

    def _post(self, path, body, headers=None):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.getcode(), dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def _get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.getcode(), dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def _get_json(self, path, headers=None):
        code, _hdrs, raw = self._get(path, headers)
        return code, json.loads(raw)

    def _post_json(self, path, body, headers=None):
        code, _hdrs, raw = self._post(path, body, headers)
        return code, json.loads(raw)


# ------------------------------------------------------------- the probes

class TestProbes(LivenessTestBase):
    def setUp(self):
        self._start(FixedClient(canned(p1=0.9, conf=0.95)))

    def test_livez_is_shallow(self):
        code, body = self._get_json("/livez")
        self.assertEqual(code, 200)
        self.assertTrue(body["alive"])
        self.assertIn("uptime_s", body)

    def test_healthz_200_when_healthy_names_all_predicates(self):
        code, body = self._get_json("/healthz")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(sorted(body["checks"].keys()), [
            "config_current",
            "evidence_flowing",
            "forwarder_draining",
            "gate_constructed",
            "no_crashloop_signature",
        ])
        for name, check in body["checks"].items():
            self.assertTrue(check["ok"], f"predicate {name} failed: {check}")
        self.assertEqual(body["config_generation"], 1)

    def test_split_kill_forwarder_dependency(self):
        """Kill the PD dependency: /livez stays 200, /healthz goes 503."""
        # Sanity: healthy first.
        code, _ = self._get_json("/healthz")
        self.assertEqual(code, 200)
        # Kill the dependency: the forwarder can no longer reach PagerDuty.
        self.pd.close()
        for _ in range(3):
            code, _body = self._post_json("/v2/enqueue", _pd_event())
            self.assertEqual(code, 200)  # alerts still accepted (fail-open)
        # The split: the process is alive, but it cannot do its job.
        code, livez_body = self._get_json("/livez")
        self.assertEqual(code, 200)
        self.assertTrue(livez_body["alive"])
        code, health_body = self._get_json("/healthz")
        self.assertEqual(code, 503)
        self.assertFalse(health_body["ok"])
        self.assertEqual(health_body["failed"], ["forwarder_draining"])
        check = health_body["checks"]["forwarder_draining"]
        self.assertGreaterEqual(check["recent_forward_errors_60s"], 3)

    def test_livez_survives_total_dependency_loss(self):
        """Even with every dependency dead, /livez answers 200."""
        self.pd.close()
        try:
            self.pipeline.audit.close()
        except Exception:
            pass
        code, body = self._get_json("/livez")
        self.assertEqual(code, 200)
        self.assertTrue(body["alive"])

    def test_crashloop_signature_trips_healthz(self):
        code, _ = self._get_json("/healthz")
        self.assertEqual(code, 200)
        # Two more restarts inside 10 min = crash-loop signature.
        record_restart(self.statedir)
        record_restart(self.statedir)
        self.assertEqual(count_restarts_10m(self.statedir), 3)
        code, body = self._get_json("/healthz")
        self.assertEqual(code, 503)
        self.assertIn("no_crashloop_signature", body["failed"])


# ------------------------------------------------------- 503-not-429 contract

class TestReceiverContract(LivenessTestBase):
    def setUp(self):
        self._start(FixedClient(canned(p1=0.9, conf=0.95)), max_inflight=1)

    def test_overload_returns_503_with_retry_after(self):
        # Saturate the single admission slot directly (same semaphore the
        # handler uses): the next alert must get 503, never 429.
        sem = self.server.inflight_sem
        acquired = sem.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            conn = http.client.HTTPConnection(
                "127.0.0.1", self.server.server_address[1], timeout=10)
            conn.request("POST", "/v2/enqueue",
                         body=json.dumps(_pd_event()),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            raw = resp.read()
            self.assertEqual(resp.status, 503, raw)
            self.assertEqual(resp.getheader("Retry-After"), "1")
            body = json.loads(raw)
            self.assertIn("retry", body["message"].lower())
        finally:
            sem.release()
        # Slot freed: traffic flows again.
        code, _ = self._post_json("/v2/enqueue", _pd_event())
        self.assertEqual(code, 200)

    def test_probes_not_starved_by_overload(self):
        sem = self.server.inflight_sem
        self.assertTrue(sem.acquire(blocking=False))
        try:
            code, _ = self._get_json("/livez")
            self.assertEqual(code, 200)
            code, _ = self._get_json("/healthz")
            self.assertEqual(code, 200)  # deep probe bypasses admission
        finally:
            sem.release()

    def test_429_never_emitted(self):
        """Battery across routes and states: 429 must never appear."""
        seen = set()

        def note(code):
            seen.add(code)

        code, _, _ = self._get("/livez")
        note(code)
        code, _, _ = self._get("/healthz")
        note(code)
        code, _, _ = self._get("/nope")
        note(code)
        code, _, _ = self._post("/v2/enqueue", b"{not json")
        note(code)
        code, _, _ = self._post("/v2/enqueue", _pd_event())
        note(code)
        code, _, _ = self._post("/webhook/generic", b"{}")
        note(code)
        # Saturated:
        sem = self.server.inflight_sem
        self.assertTrue(sem.acquire(blocking=False))
        try:
            code, _, _ = self._post("/v2/enqueue", _pd_event())
            note(code)
            code, _, _ = self._post("/webhook/generic", b"{}")
            note(code)
        finally:
            sem.release()
        self.assertNotIn(429, seen)
        # And no code path emits a 429 status for alert traffic (the word may
        # still appear in comments explaining the contract).
        import pathlib
        src = pathlib.Path(_SRC, "sentinel", "receiver.py").read_text()
        self.assertNotIn("_send_json(429", src)


# ------------------------------------------------------- config validation

class TestConfigValidation(unittest.TestCase):
    def _loader(self, thresholds=None, allowlist=None, raw_thresholds=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfgdir = os.path.join(tmp.name, "cfg")
        os.makedirs(cfgdir, exist_ok=True)
        with open(os.path.join(cfgdir, "thresholds.json"), "w") as fh:
            if raw_thresholds is not None:
                fh.write(raw_thresholds)
            else:
                json.dump(thresholds or {}, fh)
        with open(os.path.join(cfgdir, "allowlist.json"), "w") as fh:
            json.dump(sorted(allowlist or []), fh)
        return ConfigLoader(config_dir=cfgdir,
                            state_dir=os.path.join(tmp.name, "state"))

    def test_trailing_comma_rejected(self):
        loader = self._loader(
            raw_thresholds='{"suppress_conf_min": 0.90,}')
        with self.assertRaises(ConfigRejected):
            loader.load_startup()

    def test_unknown_key_rejected(self):
        loader = self._loader(
            thresholds={"suppress_conf_min": 0.90, "supress_conf_min": 0.9})
        with self.assertRaises(ConfigRejected):
            loader.load_startup()

    def test_governance_floor_rejected(self):
        # suppress_conf_min below the ADR-022 floor is a rejection, not a
        # warning — the fatigue ratchet's destination is unloadable by hand.
        loader = self._loader(thresholds={"suppress_conf_min": 0.80})
        with self.assertRaises(ConfigRejected):
            loader.load_startup()

    def test_incoherent_table_rejected(self):
        loader = self._loader(thresholds={"suppress_p1_max": 0.5,
                                          "page_p1p2_min": 0.3})
        with self.assertRaises(ConfigRejected):
            loader.load_startup()

    def test_bad_allowlist_entry_rejected(self):
        loader = self._loader(allowlist=["not-a-fingerprint"])
        with self.assertRaises(ConfigRejected):
            loader.load_startup()

    def test_missing_thresholds_refuses_start(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfgdir = os.path.join(tmp.name, "cfg")
        os.makedirs(cfgdir, exist_ok=True)  # no thresholds.json at all
        loader = ConfigLoader(config_dir=cfgdir,
                              state_dir=os.path.join(tmp.name, "state"))
        with self.assertRaises(ConfigRejected):
            loader.load_startup()

    def test_last_good_fallback_on_invalid(self):
        loader = self._loader(thresholds={"suppress_conf_min": 0.95})
        first = loader.load_startup()
        self.assertEqual(first.generation, 1)
        # Corrupt the file: startup now serves last-good, and records the event.
        with open(os.path.join(loader.config_dir, "thresholds.json"),
                  "w") as fh:
            fh.write("{oops")
        second = loader.load_startup()
        self.assertEqual(second.thresholds.suppress_conf_min, 0.95)
        self.assertEqual(second.generation, 1)  # no new generation minted
        with open(os.path.join(loader.state_dir, "events.jsonl")) as fh:
            events = [json.loads(line) for line in fh]
        self.assertTrue(any(e["type"] == "config_rejected" for e in events))

    def test_bad_config_refuses_start_subprocess(self):
        """The release-blocking proof: bad config + no last-good -> the
        process exits non-zero instead of serving."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfgdir = os.path.join(tmp.name, "cfg")
        statedir = os.path.join(tmp.name, "state")
        os.makedirs(cfgdir, exist_ok=True)
        with open(os.path.join(cfgdir, "thresholds.json"), "w") as fh:
            fh.write('{"suppress_conf_min": 0.90,}')  # trailing comma
        env = dict(os.environ)
        env["SENTINEL_MOCK"] = "1"
        env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
        # Isolate from the developer's ambient config.
        for key in ("SENTINEL_HEALTH_TOKEN", "SENTINEL_CONFIG_DIR",
                    "SENTINEL_STATE_DIR", "SENTINEL_DB", "TYPESAFE_API_KEY",
                    "PD_ROUTING_KEY"):
            env.pop(key, None)
        proc = subprocess.Popen(
            [sys.executable, "-m", "sentinel.receiver",
             "--port", "0",
             "--config-dir", cfgdir,
             "--state-dir", statedir],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            _out, err = proc.communicate(timeout=25)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail("receiver did not exit on bad config (it must refuse "
                      "to start)")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b"refusing to start", err)

    def test_reload_rejects_invalid_keeps_serving(self):
        base = LivenessTestBase()
        base._start(FixedClient(canned(p1=0.9, conf=0.95)))
        self.addCleanup(base._stop)
        gen1 = base.loader.current.generation
        # Break the on-disk config, then attempt a reload.
        with open(os.path.join(base.cfgdir, "thresholds.json"), "w") as fh:
            fh.write('{"suppress_conf_min": 0.80}')  # below governance floor
        code, body = base._post_json("/-/reload", b"")
        self.assertEqual(code, 422)
        self.assertIn("rejected", body["message"].lower())
        # Live generation untouched; still healthy (rejected change is
        # acknowledged — last-good keeps serving).
        self.assertEqual(base.pipeline.gate.thresholds.suppress_conf_min,
                         0.90)
        self.assertEqual(base.loader.current.generation, gen1)
        code, health = base._get_json("/healthz")
        self.assertEqual(code, 200)
        self.assertTrue(health["ok"])
        # Now a valid change applies atomically with a generation bump.
        with open(os.path.join(base.cfgdir, "thresholds.json"), "w") as fh:
            json.dump({"suppress_conf_min": 0.95}, fh)
        code, body = base._post_json("/-/reload", b"")
        self.assertEqual(code, 200)
        self.assertEqual(body["config_generation"], gen1 + 1)
        self.assertEqual(base.pipeline.gate.thresholds.suppress_conf_min,
                         0.95)

    def test_unreloaded_edit_trips_config_current(self):
        base = LivenessTestBase()
        base._start(FixedClient(canned(p1=0.9, conf=0.95)))
        self.addCleanup(base._stop)
        code, _ = base._get_json("/healthz")
        self.assertEqual(code, 200)
        # Valid edit on disk, but no reload yet: the process is not serving
        # what the operator last wrote -> unhealthy, naming the predicate.
        time.sleep(0.02)  # ensure the mtime actually changes
        with open(os.path.join(base.cfgdir, "thresholds.json"), "w") as fh:
            json.dump({"suppress_conf_min": 0.95}, fh)
        os.utime(os.path.join(base.cfgdir, "thresholds.json"),
                 (time.time() + 2, time.time() + 2))
        code, body = base._get_json("/healthz")
        self.assertEqual(code, 503)
        self.assertEqual(body["failed"], ["config_current"])
        # Reloading the (valid) change restores health.
        code, _ = base._post_json("/-/reload", b"")
        self.assertEqual(code, 200)
        code, body = base._get_json("/healthz")
        self.assertEqual(code, 200)


# ------------------------------------------------------- healthz auth

class TestHealthzAuth(LivenessTestBase):
    TOKEN = "test-health-token-abc123"

    def setUp(self):
        self._old = os.environ.get("SENTINEL_HEALTH_TOKEN")
        os.environ["SENTINEL_HEALTH_TOKEN"] = self.TOKEN
        self.addCleanup(self._restore_env)
        self._start(FixedClient(canned(p1=0.9, conf=0.95)))

    def _restore_env(self):
        if self._old is None:
            os.environ.pop("SENTINEL_HEALTH_TOKEN", None)
        else:
            os.environ["SENTINEL_HEALTH_TOKEN"] = self._old

    def _auth(self, token=TOKEN):
        return {"Authorization": f"Bearer {token}"}

    def test_healthz_requires_bearer(self):
        code, _, _ = self._get("/healthz")
        self.assertEqual(code, 401)
        code, _, _ = self._get("/healthz", self._auth("wrong"))
        self.assertEqual(code, 401)
        code, body = self._get_json("/healthz", self._auth())
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

    def test_livez_never_requires_auth(self):
        code, _, _ = self._get("/healthz")  # sanity: gated...
        self.assertEqual(code, 401)
        code, body = self._get_json("/livez")  # ...but livez is open
        self.assertEqual(code, 200)
        self.assertTrue(body["alive"])

    def test_reload_requires_bearer(self):
        code, _, _ = self._post("/-/reload", b"")
        self.assertEqual(code, 401)
        code, body = self._post_json("/-/reload", b"", self._auth())
        self.assertEqual(code, 200)


# ------------------------------------------------------- poison isolation

class TestPoisonIsolation(LivenessTestBase):
    def setUp(self):
        self._start(FixedClient(canned(p1=0.0, conf=0.95)))

    def _poison_payloads(self):
        yield b"{this is not json"
        yield b"\x00\x01\x02\xff binary garbage"
        yield b""
        yield b"null"
        yield b"[]"
        yield b"42"
        yield b'"just a string"'
        # Deep nesting (below the 8MB cap, above sane depth).
        depth = 400
        yield ("{" * depth + "}" * depth).encode()
        # Half-megabyte blob.
        yield b'{"service": "' + b"x" * (512 * 1024) + b'"}'
        # Type confusions.
        for bad in ({"service": ["web"]}, {"service": None},
                    {"service": 123}, {"service": {"nested": 1}},
                    {"payload": {"summary": ["x"]}, "routing_key": 5},
                    {"event_action": "trigger"}):
            yield json.dumps(bad).encode()
        # Deeply nested labels.
        labels = cur = {}
        for _ in range(300):
            cur["n"] = {}
            cur = cur["n"]
        yield json.dumps({"service": "web", "labels": labels}).encode()
        # Many-key object.
        yield json.dumps(
            {"service": "web", **{f"k{i}": "v" * 100 for i in range(5000)}}
        ).encode()

    def test_poison_alerts_never_kill_the_process(self):
        """Design §8.1, release-blocking shape: pathological payloads may kill
        their own request (as passthrough), never the receiver."""
        payloads = list(self._poison_payloads())
        self.assertGreater(len(payloads), 10)
        for i, raw in enumerate(payloads):
            code, _hdrs, _raw = self._post("/v2/enqueue", raw,
                                           headers={"Content-Type":
                                                    "application/json"})
            # Fail-open: every pathological payload is accepted (200) —
            # a request may die, the process may not.
            self.assertEqual(code, 200, f"payload {i} got {code}")
        code, body = self._get_json("/livez")
        self.assertEqual(code, 200)
        self.assertTrue(body["alive"])
        code, body = self._get_json("/healthz")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])


if __name__ == "__main__":
    unittest.main()
