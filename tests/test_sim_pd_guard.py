"""Audit P0 (sim default) + ruling X-B: SENTINEL_SIM=1 can NEVER page real
PagerDuty.

Structural guarantee, Stripe sk_test-grade: sim mode hard-wires the
forwarder to a loopback sink with the SIM-FAKE routing key, and any real
PD endpoint or key in the environment is a loud boot refusal — not a
toggle, not opt-in protection.

These tests touch no network: the loopback sink (127.0.0.1:9, the discard
port) has nothing listening, and the refusal paths raise before any POST.
"""

import contextlib
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(REPO_ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from sentinel.forwarder import Forwarder  # noqa: E402
from sentinel.pd_sender import PagerDutyClient  # noqa: E402
from sentinel import sim_pd_guard  # noqa: E402
from sentinel.sim_pd_guard import (  # noqa: E402
    SIM_FAKE_ROUTING_KEY,
    SIM_FAKE_SINK_URL,
    SIM_MODE_ENV,
    enforce_sim_pd,
    is_sim_safe_url,
    sim_mode,
)

REAL_PD = "https://events.pagerduty.com/v2/enqueue"
FAKE_REAL_KEY = "a" * 32  # 32 hex chars — shaped like a real PD routing key


@contextlib.contextmanager
def _env(**overrides):
    """Temporarily set/unset env vars (None value unsets)."""
    sentinel = object()
    saved = {k: os.environ.get(k, sentinel) for k in overrides}
    for k, v in overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is sentinel:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _sim_env(**extra):
    base = {
        SIM_MODE_ENV: "1",
        "PD_EVENTS_URL": None,
        "PD_ROUTING_KEY": None,
        "SENTINEL_SIMULATED_PAGING": None,
        "TYPESAFE_API_KEY": None,
    }
    base.update(extra)
    return _env(**base)


class SimPdGuardUnitTests(unittest.TestCase):
    def test_sim_mode_detection(self):
        with _env(**{SIM_MODE_ENV: "1"}):
            self.assertTrue(sim_mode())
        with _env(**{SIM_MODE_ENV: None}):
            self.assertFalse(sim_mode())

    def test_sim_safe_url_accepts_loopback_only(self):
        self.assertTrue(is_sim_safe_url("http://127.0.0.1:9/fakepd"))
        self.assertTrue(is_sim_safe_url("http://localhost:8080/x"))
        self.assertTrue(is_sim_safe_url("http://[::1]:9/x"))
        self.assertFalse(is_sim_safe_url(REAL_PD))
        self.assertFalse(is_sim_safe_url("https://events.pagerduty.com.evil.com/x"))
        self.assertFalse(is_sim_safe_url("https://127.0.0.1.evil.com/x"))
        self.assertFalse(is_sim_safe_url(None))
        self.assertFalse(is_sim_safe_url(""))

    def test_enforce_forces_loopback_and_sim_fake_key(self):
        with _sim_env():
            url, key = enforce_sim_pd(None, None, where="test")
        self.assertEqual(url, SIM_FAKE_SINK_URL)
        self.assertEqual(key, SIM_FAKE_ROUTING_KEY)
        self.assertTrue(is_sim_safe_url(url))
        self.assertNotIn("pagerduty.com", url.lower())
        # SIM-FAKE key is deliberately not 32-hex: it cannot be mistaken
        # for a real routing key by any shape check.
        import re
        self.assertIsNone(re.fullmatch(r"[0-9a-fA-F]{32}", key))

    def test_enforce_refuses_real_endpoint(self):
        with _sim_env(**{"PD_EVENTS_URL": REAL_PD}):
            with self.assertRaises(SystemExit):
                enforce_sim_pd(os.environ.get("PD_EVENTS_URL"),
                               os.environ.get("PD_ROUTING_KEY"),
                               where="test")

    def test_enforce_refuses_real_key(self):
        with _sim_env(**{"PD_ROUTING_KEY": FAKE_REAL_KEY}):
            with self.assertRaises(SystemExit):
                enforce_sim_pd(os.environ.get("PD_EVENTS_URL"),
                               os.environ.get("PD_ROUTING_KEY"),
                               where="test")

    def test_enforce_accepts_preforced_sim_values(self):
        # Idempotent: values already at the forced pair pass through.
        with _sim_env():
            url, key = enforce_sim_pd(SIM_FAKE_SINK_URL, SIM_FAKE_ROUTING_KEY,
                                      where="test")
        self.assertEqual((url, key), (SIM_FAKE_SINK_URL, SIM_FAKE_ROUTING_KEY))

    def test_forwarder_ctor_refuses_real_url_in_sim(self):
        with _sim_env():
            with self.assertRaises(ValueError):
                Forwarder()  # default pd_events_url is the real PD endpoint
            with self.assertRaises(ValueError):
                Forwarder(pd_events_url=REAL_PD)

    def test_forwarder_ctor_accepts_loopback_in_sim(self):
        with _sim_env():
            f = Forwarder(pd_events_url=SIM_FAKE_SINK_URL,
                          default_routing_key=SIM_FAKE_ROUTING_KEY)
        self.assertEqual(f.pd_events_url, SIM_FAKE_SINK_URL)

    def test_pd_client_ctor_refuses_real_url_in_sim(self):
        with _sim_env():
            with self.assertRaises(ValueError):
                PagerDutyClient()

    def test_non_sim_untouched(self):
        with _env(**{SIM_MODE_ENV: None, "PD_EVENTS_URL": None,
                     "PD_ROUTING_KEY": None}):
            url, key = enforce_sim_pd(REAL_PD, "somekey", where="test")
            self.assertEqual((url, key), (REAL_PD, "somekey"))
            # Production wiring unchanged: the real default still constructs.
            f = Forwarder()
            self.assertEqual(f.pd_events_url, REAL_PD)


class SimPipelineBootTests(unittest.TestCase):
    """Boot the REAL receiver pipeline under SENTINEL_SIM=1 and assert no
    non-loopback PagerDuty URL can be wired anywhere in it."""

    def _policy(self, tmpdir):
        from sentinel.config import ConfigLoader
        cfgdir = os.path.join(tmpdir, "cfg")
        os.makedirs(cfgdir, exist_ok=True)
        with open(os.path.join(cfgdir, "thresholds.json"), "w") as fh:
            json.dump({}, fh)
        with open(os.path.join(cfgdir, "allowlist.json"), "w") as fh:
            json.dump([], fh)
        statedir = os.path.join(tmpdir, "state")
        loader = ConfigLoader(config_dir=cfgdir, state_dir=statedir)
        return loader.load_startup(), statedir

    def _boot(self, tmp, **env_extra):
        from sentinel.receiver import build_pipeline_from_env
        policy, statedir = self._policy(tmp)
        env = {
            SIM_MODE_ENV: "1",
            "SENTINEL_DB": ":memory:",
            "SENTINEL_STATE_DIR": statedir,
            "SENTINEL_WEBHOOK_SECRET": "sim-boot-test-secret-0123456789",
            "TYPESAFE_API_KEY": None,  # absent key → FakeJev, no network
            "PD_EVENTS_URL": None,
            "PD_ROUTING_KEY": None,
        }
        env.update(env_extra)
        with _env(**env):
            return build_pipeline_from_env(policy=policy)

    def test_sim_boot_wires_loopback_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = self._boot(tmp)
        fwd = pipeline.forwarder
        # The wired endpoint is the loopback sink — never pagerduty.com.
        self.assertEqual(fwd.pd_events_url, SIM_FAKE_SINK_URL)
        self.assertTrue(is_sim_safe_url(fwd.pd_events_url))
        self.assertNotIn("pagerduty.com", fwd.pd_events_url.lower())
        # The wired routing key is the SIM-FAKE placeholder.
        self.assertEqual(fwd.default_routing_key, SIM_FAKE_ROUTING_KEY)
        # Sweep the forwarder's string surface for any real-PD address.
        for attr in ("pd_events_url",):
            self.assertNotIn("pagerduty.com",
                             getattr(fwd, attr, "").lower())

    def test_sim_boot_refuses_real_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                self._boot(tmp, PD_EVENTS_URL=REAL_PD)

    def test_sim_boot_refuses_real_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                self._boot(tmp, PD_ROUTING_KEY=FAKE_REAL_KEY)

    def test_sim_boot_announces_forced_fakepd(self):
        import io
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                self._boot(tmp)
        self.assertIn("FakePD", buf.getvalue())
        self.assertIn(SIM_FAKE_SINK_URL, buf.getvalue())


def _load_platform_app():
    """Import platform/server/app.py without shadowing stdlib `platform`.

    The platform tier never imports the engine (tier decoupling), so the
    app is loaded under a synthetic package name via importlib.
    """
    pkg_name = "srvtest_platform_pkg"
    srv = os.path.join(REPO_ROOT, "platform", "server")
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [srv]
    sys.modules[pkg_name] = pkg
    spec = importlib.util.spec_from_file_location(pkg_name + ".app",
                                                  os.path.join(srv, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[pkg_name + ".app"] = mod
    spec.loader.exec_module(mod)
    return mod


class PlatformSimSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _load_platform_app()

    def test_pd_enqueue_refuses_in_sim(self):
        with _env(**{SIM_MODE_ENV: "1"}):
            ok, detail = self.app._pd_enqueue(
                {"routing_key": FAKE_REAL_KEY,
                 "event_action": "trigger",
                 "dedup_key": "d",
                 "payload": {"summary": "x", "severity": "info",
                             "source": "test"}},
                timeout_s=0.01)
        self.assertFalse(ok)
        self.assertIn("refused", detail)
        self.assertIn(SIM_MODE_ENV, detail)


if __name__ == "__main__":
    unittest.main()
