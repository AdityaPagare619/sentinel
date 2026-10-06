"""Tests for the BYOK lane (sentinel/integrations.py + wiring).

Security contract under test:
  - keys at rest: 600 perms, never in API status responses (last4 only)
  - keys in flight: NEVER in logs, audit events, event log, error messages,
    or traces — redacted at every boundary
  - resolution order: user store -> PD_ROUTING_KEY env -> unconfigured
  - simulated mode: honest, labeled, never silent
  - platform twin (platform/server/integrations.py): same format, same validation

A fake routing key is used throughout ("aa11"*8). It is not real.
"""

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_PLATSRV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "platform", "server")
if _PLATSRV not in sys.path:
    sys.path.insert(0, _PLATSRV)
import _pkg as _plat_pkg  # loads platform/server/* as sentinel_platform.*

from sentinel import integrations as eng
from sentinel.forwarder import Forwarder, ForwardResult
from sentinel.models import Alert, Disposition

plat = _plat_pkg.load("integrations")  # platform/server twin

from tests.helpers import make_alert

TEST_PD_KEY = "aa11" * 8          # 32 hex chars, fake
TEST_PD_KEY_2 = "bb22" * 8       # a different fake key
TEST_JEV_KEY = "jev-test-key-12345"


def _disp(action="page_now"):
    return Disposition(action=action, reason="threshold", team="platform",
                       confidence=0.9, latency_ms=1.0)


def _non_pd_alert():
    # Non-PD-shaped: source != pagerduty so the forwarder builds the event
    # and must inject the routing key.
    a = make_alert(source="webhook", alert_id="byok-1")
    a.raw = {"dedup_key": "byok-dedup-1"}
    return a


class _TmpState:
    """Isolated SENTINEL_STATE_DIR for one test."""
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="sentinel-byok-test")
        self._old = os.environ.get("SENTINEL_STATE_DIR")
        os.environ["SENTINEL_STATE_DIR"] = self.dir

    def close(self):
        if self._old is None:
            os.environ.pop("SENTINEL_STATE_DIR", None)
        else:
            os.environ["SENTINEL_STATE_DIR"] = self._old


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = _TmpState()
        self.store = eng.IntegrationStore()

    def tearDown(self):
        self.tmp.close()

    def test_file_created_with_600_perms(self):
        self.store.set(eng.PD_KEY_NAME, TEST_PD_KEY)
        st = os.stat(self.store.path)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)

    def test_status_never_carries_values(self):
        self.store.set(eng.PD_KEY_NAME, TEST_PD_KEY)
        self.store.set(eng.JEV_KEY_NAME, TEST_JEV_KEY)
        status = self.store.status()
        blob = json.dumps(status)
        self.assertNotIn(TEST_PD_KEY, blob)
        self.assertNotIn(TEST_JEV_KEY, blob)
        self.assertTrue(status[eng.PD_KEY_NAME]["configured"])
        self.assertEqual(status[eng.PD_KEY_NAME]["last4"], TEST_PD_KEY[-4:])
        self.assertTrue(status[eng.JEV_KEY_NAME]["configured"])

    def test_unset_key_status(self):
        status = self.store.status()
        self.assertFalse(status[eng.PD_KEY_NAME]["configured"])
        self.assertIsNone(status[eng.PD_KEY_NAME]["last4"])

    def test_delete(self):
        self.store.set(eng.PD_KEY_NAME, TEST_PD_KEY)
        self.store.delete(eng.PD_KEY_NAME)
        self.assertIsNone(self.store.get(eng.PD_KEY_NAME))
        self.assertFalse(self.store.status()[eng.PD_KEY_NAME]["configured"])

    def test_bad_routing_key_rejected_and_not_echoed(self):
        for bad in ["short", "zz" * 16, "aa11" * 7 + "aa1", "", "  "]:
            with self.assertRaises(ValueError) as cm:
                self.store.set(eng.PD_KEY_NAME, bad)
            # operator-language error, and it never echoes the bad value
            self.assertNotIn(bad.strip() or "x-never", str(cm.exception) or "x-never")
        self.assertIsNone(self.store.get(eng.PD_KEY_NAME))

    def test_unknown_key_name_rejected(self):
        with self.assertRaises(KeyError):
            self.store.set("nope", "x")
        with self.assertRaises(KeyError):
            self.store.get("nope")

    def test_simulated_flag_roundtrip(self):
        self.assertFalse(eng.simulated_paging(self.store))
        self.store.set_flag("simulated_paging", True)
        self.assertTrue(eng.simulated_paging(self.store))

    def test_env_forces_simulated_on(self):
        os.environ["SENTINEL_SIMULATED_PAGING"] = "1"
        try:
            self.assertTrue(eng.simulated_paging(self.store))
        finally:
            del os.environ["SENTINEL_SIMULATED_PAGING"]

    def test_set_many_saves_both_when_valid(self):
        out = self.store.set_many({eng.PD_KEY_NAME: TEST_PD_KEY,
                                   eng.JEV_KEY_NAME: TEST_JEV_KEY})
        self.assertTrue(out[eng.PD_KEY_NAME]["configured"])
        self.assertTrue(out[eng.JEV_KEY_NAME]["configured"])
        self.assertEqual(self.store.get(eng.PD_KEY_NAME), TEST_PD_KEY)

    def test_set_many_is_atomic_bad_second_key_saves_nothing(self):
        # Vault (PR #77): a bad second key must not leave the first saved.
        with self.assertRaises(ValueError):
            self.store.set_many({eng.PD_KEY_NAME: TEST_PD_KEY,
                                 eng.JEV_KEY_NAME: "short"})
        self.assertIsNone(self.store.get(eng.PD_KEY_NAME))
        self.assertIsNone(self.store.get(eng.JEV_KEY_NAME))

    def test_set_many_rejects_unknown_name_before_any_write(self):
        with self.assertRaises(KeyError):
            self.store.set_many({eng.PD_KEY_NAME: TEST_PD_KEY,
                                 "nope": "x"})
        self.assertIsNone(self.store.get(eng.PD_KEY_NAME))


class EphemeralTest(unittest.TestCase):
    def test_unwritable_dir_goes_ephemeral_and_writes_fail_loud(self):
        store = eng.IntegrationStore(path="/proc/1/definitely-not-here/integrations.json")
        self.assertTrue(store.ephemeral)
        with self.assertRaises(eng.EphemeralStoreError):
            store.set(eng.PD_KEY_NAME, TEST_PD_KEY)
        # memory-only reads still work (never pretend-persist)
        self.assertIsNone(store.get(eng.PD_KEY_NAME))


class ResolutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = _TmpState()
        self._old_env = os.environ.pop("PD_ROUTING_KEY", None)
        os.environ.pop("SENTINEL_SIMULATED_PAGING", None)

    def tearDown(self):
        self.tmp.close()
        if self._old_env is not None:
            os.environ["PD_ROUTING_KEY"] = self._old_env
        else:
            os.environ.pop("PD_ROUTING_KEY", None)

    def test_user_key_beats_env(self):
        store = eng.IntegrationStore()
        store.set(eng.PD_KEY_NAME, TEST_PD_KEY)
        os.environ["PD_ROUTING_KEY"] = TEST_PD_KEY_2
        key, source = eng.resolve_paging_key(store)
        self.assertEqual(key, TEST_PD_KEY)
        self.assertEqual(source, "user")

    def test_env_fallback(self):
        store = eng.IntegrationStore()
        os.environ["PD_ROUTING_KEY"] = TEST_PD_KEY_2
        key, source = eng.resolve_paging_key(store)
        self.assertEqual(key, TEST_PD_KEY_2)
        self.assertEqual(source, "env")

    def test_unconfigured(self):
        store = eng.IntegrationStore()
        key, source = eng.resolve_paging_key(store)
        self.assertIsNone(key)
        self.assertEqual(source, "unconfigured")

    def test_jev_resolution_order(self):
        store = eng.IntegrationStore()
        store.set(eng.JEV_KEY_NAME, TEST_JEV_KEY)
        os.environ["TYPESAFE_API_KEY"] = "platform-key"
        try:
            key, source = eng.resolve_jev_key(store)
            self.assertEqual(key, TEST_JEV_KEY)
            self.assertEqual(source, "user")
        finally:
            del os.environ["TYPESAFE_API_KEY"]

    def test_jev_env_fallback(self):
        store = eng.IntegrationStore()
        os.environ["TYPESAFE_API_KEY"] = "platform-key"
        try:
            key, source = eng.resolve_jev_key(store)
            self.assertEqual(key, "platform-key")
            self.assertEqual(source, "env")
        finally:
            del os.environ["TYPESAFE_API_KEY"]


class ForwarderKeyLeakTest(unittest.TestCase):
    """A key in config must NEVER appear in any emitted record.

    The POST body to PagerDuty legitimately carries the key (that's the
    send); everything else — stderr, results, error strings, metrics —
    must not.
    """

    def setUp(self):
        self.tmp = _TmpState()
        self._old_env = os.environ.pop("PD_ROUTING_KEY", None)
        os.environ.pop("SENTINEL_SIMULATED_PAGING", None)
        store = eng.IntegrationStore()
        store.set(eng.PD_KEY_NAME, TEST_PD_KEY)

    def tearDown(self):
        self.tmp.close()
        if self._old_env is not None:
            os.environ["PD_ROUTING_KEY"] = self._old_env
        else:
            os.environ.pop("PD_ROUTING_KEY", None)

    def test_user_key_used_for_send_but_never_emitted(self):
        fwd = Forwarder()
        captured = {}

        def fake_post(self, body, action, dedup_key, alert_id):
            captured["body"] = body
            return ForwardResult(forwarded=True, status_code=202, error=None,
                                 action=action, dedup_key=dedup_key)

        fwd._post = fake_post.__get__(fwd, Forwarder)
        err = io.StringIO()
        with redirect_stderr(err):
            res = fwd.forward(_non_pd_alert(), _disp())

        # the RIGHT key went on the wire...
        body = json.loads(captured["body"].decode())
        self.assertEqual(body["routing_key"], TEST_PD_KEY)
        # ...but no emitted record carries it
        self.assertNotIn(TEST_PD_KEY, err.getvalue())
        self.assertNotIn(TEST_PD_KEY, json.dumps(res.__dict__))
        self.assertNotIn(TEST_PD_KEY, json.dumps(fwd.metrics))
        self.assertTrue(res.forwarded)

    def test_key_never_in_failure_records(self):
        # The forwarder sanitizes the exception string against known key
        # values in _fail (sanitize_error) — so even a HOSTILE exception
        # carrying the key must not echo it in any emitted record. This
        # asserts on RAW outputs: no test-side scrubbing (the old version
        # scrubbed with the helpers under test — circular).
        fwd = Forwarder()

        def boom(self, body, action, dedup_key, alert_id):
            raise RuntimeError(f"conn refused carrying {TEST_PD_KEY}?? no")

        fwd._post = boom.__get__(fwd, Forwarder)
        err = io.StringIO()
        with redirect_stderr(err):
            res = fwd.forward(_non_pd_alert(), _disp())
        # forward() must never raise, and no emitted record carries the key
        self.assertFalse(res.forwarded)
        blob = json.dumps({"stderr": err.getvalue(), "error": res.error,
                           "metrics": fwd.metrics})
        self.assertNotIn(TEST_PD_KEY, blob)
        self.assertIn("[REDACTED]", res.error)

    def test_sanitize_error_leaves_benign_text_alone(self):
        out = eng.sanitize_error("conn refused: timeout after 3s")
        self.assertEqual(out, "conn refused: timeout after 3s")

    def test_per_decision_resolution_picks_up_key_changes(self):
        fwd = Forwarder()
        seen = []

        def fake_post(self, body, action, dedup_key, alert_id):
            seen.append(json.loads(body.decode())["routing_key"])
            return ForwardResult(forwarded=True, status_code=202, error=None,
                                 action=action, dedup_key=dedup_key)

        fwd._post = fake_post.__get__(fwd, Forwarder)
        store = eng.IntegrationStore()
        with redirect_stderr(io.StringIO()):
            fwd.forward(_non_pd_alert(), _disp())
            store.set(eng.PD_KEY_NAME, TEST_PD_KEY_2)
            fwd.forward(_non_pd_alert(), _disp())
        self.assertEqual(seen, [TEST_PD_KEY, TEST_PD_KEY_2])

    def test_no_key_configured_fails_loud_without_key_in_error(self):
        store = eng.IntegrationStore()
        store.delete(eng.PD_KEY_NAME)
        fwd = Forwarder()
        err = io.StringIO()
        with redirect_stderr(err):
            res = fwd.forward(_non_pd_alert(), _disp())
        self.assertFalse(res.forwarded)
        self.assertNotIn(TEST_PD_KEY, res.error or "")
        self.assertNotIn(TEST_PD_KEY, err.getvalue())
        self.assertIn("routing key", (res.error or "").lower())


class SimulatedPagingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = _TmpState()
        os.environ.pop("PD_ROUTING_KEY", None)

    def tearDown(self):
        self.tmp.close()
        os.environ.pop("SENTINEL_SIMULATED_PAGING", None)

    def _unroutable(self):
        # Real _post, but an unroutable URL: if simulated mode failed to
        # absorb the send, urlopen would raise and forwarded would be False.
        return Forwarder(pd_events_url="http://127.0.0.1:1/")

    def test_simulated_env_absorbs_send_and_labels_it(self):
        os.environ["SENTINEL_SIMULATED_PAGING"] = "1"
        fwd = self._unroutable()
        err = io.StringIO()
        with redirect_stderr(err):
            res = fwd.forward(_non_pd_alert(), _disp())
        self.assertTrue(res.forwarded)
        self.assertTrue(res.simulated)
        self.assertIn("SIMULATED PAGE", err.getvalue())
        self.assertIn("nothing was sent to PagerDuty", err.getvalue())

    def test_simulated_flag_absorbs_send(self):
        store = eng.IntegrationStore()
        store.set_flag("simulated_paging", True)
        fwd = self._unroutable()
        err = io.StringIO()
        with redirect_stderr(err):
            res = fwd.forward(_non_pd_alert(), _disp())
        self.assertTrue(res.simulated)
        self.assertIn("SIMULATED", err.getvalue())


class PlatformTwinTest(unittest.TestCase):
    """platform/server/integrations.py: same validation, same file format."""

    def setUp(self):
        self.tmp = _TmpState()

    def tearDown(self):
        self.tmp.close()

    def test_same_validation(self):
        for mod in (eng, plat):
            with self.assertRaises(Exception):
                mod.IntegrationStore().set(
                    mod.PD_KEY_NAME if hasattr(mod, "PD_KEY_NAME") else "pagerduty_routing_key",
                    "not-a-key")
            st = mod.IntegrationStore().set("pagerduty_routing_key", TEST_PD_KEY)
            self.assertTrue(st["configured"])
            self.assertEqual(st["last4"], TEST_PD_KEY[-4:])

    def test_shared_file_format(self):
        # engine writes, platform reads — the file is the contract
        eng.IntegrationStore().set(eng.PD_KEY_NAME, TEST_PD_KEY)
        got = plat.IntegrationStore().get("pagerduty_routing_key")
        self.assertEqual(got, TEST_PD_KEY)
        status = plat.IntegrationStore().status()
        blob = json.dumps(status)
        self.assertNotIn(TEST_PD_KEY, blob)

    def test_twin_600_perms(self):
        plat.IntegrationStore().set("pagerduty_routing_key", TEST_PD_KEY)
        st = os.stat(plat.IntegrationStore().path)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)


class SettingsApiTest(unittest.TestCase):
    """WSGI-level tests for /api/v1/integrations/* (no sockets)."""

    def setUp(self):
        self.tmp = _TmpState()
        os.environ.pop("SENTINEL_SIMULATED_PAGING", None)
        self.appmod = _plat_pkg.load("app")
        self.shedmod = _plat_pkg.load("shed")

    def tearDown(self):
        self.tmp.close()
        os.environ.pop("SENTINEL_SIMULATED_PAGING", None)

    def _app(self):
        # minimal app: store/registry are None (integrations paths never touch them)
        app = self.appmod.PlatformApp(
            store=None, registry=None,
            gate=self.shedmod.AdmissionGate(max_inflight=64),
            degrade=self.shedmod.DegradePolicy())
        # Track 1 (C1): every /api/* request needs the operator bearer
        # token. The fresh tmp state dir means first boot, so the token
        # is exposed exactly once here for the test harness.
        self._op_token = app.operator_tokens.first_boot_token
        return app

    def _call(self, app, method, path, body=None, auth=True):
        raw = json.dumps(body).encode() if body is not None else b""
        env = {"REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_LENGTH": str(len(raw)),
               "wsgi.input": io.BytesIO(raw)}
        if auth:
            env["HTTP_AUTHORIZATION"] = f"Bearer {self._op_token}"
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = headers

        out = b"".join(app(env, start_response))
        return captured["status"], json.loads(out.decode())

    def test_status_empty(self):
        status, env = self._call(self._app(), "GET", "/api/v1/integrations/status")
        self.assertTrue(status.startswith("200"))
        integ = env["data"]["integrations"]
        self.assertFalse(integ["pagerduty_routing_key"]["configured"])
        self.assertNotIn(TEST_PD_KEY, json.dumps(env))

    def test_save_and_status_last4_only(self):
        app = self._app()
        status, env = self._call(app, "POST", "/api/v1/integrations/keys",
                                 {"pagerduty_routing_key": TEST_PD_KEY})
        self.assertTrue(status.startswith("200"))
        blob = json.dumps(env)
        self.assertNotIn(TEST_PD_KEY, blob)
        self.assertEqual(env["data"]["saved"]["pagerduty_routing_key"]["last4"],
                         TEST_PD_KEY[-4:])
        status, env = self._call(app, "GET", "/api/v1/integrations/status")
        self.assertTrue(env["data"]["integrations"]["pagerduty_routing_key"]["configured"])

    def test_save_bad_key_422_without_echo(self):
        status, env = self._call(self._app(), "POST", "/api/v1/integrations/keys",
                                 {"pagerduty_routing_key": "bogus"})
        self.assertTrue(status.startswith("422"))
        self.assertNotIn("bogus", json.dumps(env))

    def test_delete(self):
        app = self._app()
        self._call(app, "POST", "/api/v1/integrations/keys",
                   {"pagerduty_routing_key": TEST_PD_KEY})
        status, env = self._call(app, "DELETE",
                                 "/api/v1/integrations/keys/pagerduty_routing_key")
        self.assertTrue(status.startswith("200"))
        status, env = self._call(app, "GET", "/api/v1/integrations/status")
        self.assertFalse(env["data"]["integrations"]["pagerduty_routing_key"]["configured"])

    def test_delete_unknown_key_404(self):
        status, env = self._call(self._app(), "DELETE",
                                 "/api/v1/integrations/keys/nope")
        self.assertTrue(status.startswith("404"))

    def test_test_page_simulated_mode(self):
        os.environ["SENTINEL_SIMULATED_PAGING"] = "1"
        status, env = self._call(self._app(), "POST",
                                 "/api/v1/integrations/test-page", {})
        self.assertTrue(status.startswith("200"))
        self.assertTrue(env["data"]["ok"])
        self.assertTrue(env["data"]["simulated"])
        self.assertIn("no page was sent to PagerDuty", env["data"]["message"])

    def test_test_page_real_with_mocked_pd(self):
        app = self._app()
        calls = {}

        def fake_enqueue(event, timeout_s=10.0):
            calls["event"] = event
            return True, "HTTP 202"

        orig = self.appmod._pd_enqueue
        self.appmod._pd_enqueue = fake_enqueue
        try:
            status, env = self._call(app, "POST", "/api/v1/integrations/test-page",
                                     {"routing_key": TEST_PD_KEY})
        finally:
            self.appmod._pd_enqueue = orig
        self.assertTrue(status.startswith("200"))
        self.assertTrue(env["data"]["ok"])
        self.assertFalse(env["data"]["simulated"])
        self.assertEqual(calls["event"]["routing_key"], TEST_PD_KEY)
        self.assertEqual(calls["event"]["event_action"], "trigger")
        self.assertTrue(calls["event"]["payload"]["custom_details"]["sentinel_test"])
        self.assertIn("[SENTINEL TEST]", calls["event"]["payload"]["summary"])
        # the response never carries the key
        self.assertNotIn(TEST_PD_KEY, json.dumps(env))
        self.assertEqual(env["data"]["key_source"], "request")

    def test_test_page_uses_stored_key(self):
        app = self._app()
        self._call(app, "POST", "/api/v1/integrations/keys",
                   {"pagerduty_routing_key": TEST_PD_KEY})
        calls = {}

        def fake_enqueue(event, timeout_s=10.0):
            calls["event"] = event
            return True, "HTTP 202"

        orig = self.appmod._pd_enqueue
        self.appmod._pd_enqueue = fake_enqueue
        try:
            status, env = self._call(app, "POST", "/api/v1/integrations/test-page", {})
        finally:
            self.appmod._pd_enqueue = orig
        self.assertTrue(env["data"]["ok"])
        self.assertEqual(calls["event"]["routing_key"], TEST_PD_KEY)
        self.assertEqual(env["data"]["key_source"], "stored")

    def test_test_page_pd_rejection_reported_honestly(self):
        app = self._app()

        def fake_enqueue(event, timeout_s=10.0):
            return False, "HTTP 400"

        orig = self.appmod._pd_enqueue
        self.appmod._pd_enqueue = fake_enqueue
        try:
            status, env = self._call(app, "POST", "/api/v1/integrations/test-page",
                                     {"routing_key": TEST_PD_KEY})
        finally:
            self.appmod._pd_enqueue = orig
        self.assertTrue(status.startswith("200"))
        self.assertFalse(env["data"]["ok"])
        self.assertNotIn(TEST_PD_KEY, json.dumps(env))

    def test_test_page_no_key_422(self):
        status, env = self._call(self._app(), "POST",
                                 "/api/v1/integrations/test-page", {})
        self.assertTrue(status.startswith("422"))
        self.assertEqual(env["error"]["code"], "no_routing_key")

    def test_ephemeral_writes_501(self):
        app = self._app()
        app.integrations = plat.IntegrationStore(
            path="/proc/1/definitely-not-here/integrations.json")
        self.assertTrue(app.integrations.ephemeral)
        status, env = self._call(app, "POST", "/api/v1/integrations/keys",
                                 {"pagerduty_routing_key": TEST_PD_KEY})
        self.assertTrue(status.startswith("501"))
        self.assertEqual(env["error"]["code"], "persistence_unavailable")

    def test_simulated_toggle(self):
        app = self._app()
        status, env = self._call(app, "POST", "/api/v1/integrations/simulated",
                                 {"enabled": True})
        self.assertTrue(status.startswith("200"))
        self.assertTrue(env["data"]["simulated_paging"])
        status, env = self._call(app, "GET", "/api/v1/integrations/status")
        self.assertTrue(env["data"]["integrations"]["simulated_paging"])


if __name__ == "__main__":
    unittest.main()
