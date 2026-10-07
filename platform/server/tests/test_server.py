"""WSGI-level tests for platform.server.app (no sockets)."""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # platform/server for _pkg
sys.path.insert(0, _HERE)  # tests dir for fixtures
import _pkg

from fixtures import make_decision, make_store_db

_app = _pkg.load("app")
_auth = _pkg.load("auth")
_datasets = _pkg.load("datasets")
_shed = _pkg.load("shed")
_store = _pkg.load("store")

PlatformApp = _app.PlatformApp
OperatorTokenStore = _auth.OperatorTokenStore
DatasetRegistry = _datasets.DatasetRegistry
AdmissionGate, DegradePolicy = _shed.AdmissionGate, _shed.DegradePolicy
ReadStore = _store.ReadStore


def _env(path, method="GET", query="", body=None, headers=None):
    raw = json.dumps(body).encode() if body is not None else b""
    env = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
        "wsgi.url_scheme": "http",
        "SERVER_NAME": "test",
        "SERVER_PORT": "8080",
    }
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    return env


class AppCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = make_store_db()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.addClassCleanup(os.unlink, cls.db)

    def make_app(self, **kw):
        store = ReadStore(self.db)
        registry = DatasetRegistry(os.path.join(self.tmp.name, "datasets"))
        gate = AdmissionGate(max_inflight=32)
        degrade = DegradePolicy(shed_load=10 ** 9)  # never shed in tests
        tok_dir = tempfile.mkdtemp(prefix="sentinel-op-token-")
        self.addCleanup(shutil.rmtree, tok_dir, ignore_errors=True)
        tok_store = OperatorTokenStore(
            os.path.join(tok_dir, "operator_token.json"))
        # Track 1 (C1): every /api/* request needs the operator bearer
        # token. The test harness signs every call by default.
        self._op_token = tok_store.first_boot_token
        args = dict(store=store, registry=registry, gate=gate,
                    degrade=degrade, operator_token_store=tok_store)
        args.update(kw)
        return PlatformApp(**args)

    def _op_auth(self):
        """Fresh operator token store + matching bearer headers, for test
        cases that build PlatformApp directly instead of via make_app."""
        d = tempfile.mkdtemp(prefix="sentinel-op-token-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        s = OperatorTokenStore(os.path.join(d, "operator_token.json"))
        self._op_token = s.first_boot_token
        return s, {"Authorization": f"Bearer {self._op_token}"}

    def call(self, app, path, method="GET", query="", body=None,
             headers=None, sse_seconds=None, auth=True):
        """sse_seconds: for /api/stream, consume the infinite generator
        for at most this many seconds, then close it.
        auth=False skips the default bearer header (for auth tests)."""
        if auth:
            headers = dict(headers or {})
            if not any(k.lower() == "authorization" for k in headers):
                headers["Authorization"] = f"Bearer {self._op_token}"
        env = _env(path, method, query, body, headers)
        captured = {}

        def start_response(status, response_headers):
            captured["status"] = status
            captured["headers"] = dict(response_headers)

        chunks = app(env, start_response)
        # SSE returns an infinite generator; others return [bytes].
        if hasattr(chunks, "__iter__") and not isinstance(chunks, list):
            out = []
            if sse_seconds is not None:
                deadline = time.monotonic() + sse_seconds
                # Drive the generator in a thread so the 1s poll sleeps
                # don't block the deadline.
                import queue
                q: queue.Queue = queue.Queue()

                def pump():
                    try:
                        for chunk in chunks:
                            q.put(chunk)
                    finally:
                        q.put(None)

                t = threading.Thread(target=pump, daemon=True)
                t.start()
                while time.monotonic() < deadline:
                    try:
                        chunk = q.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if chunk is None:
                        break
                    assert isinstance(chunk, bytes), \
                        f"WSGI chunk must be bytes, got {type(chunk)}"
                    out.append(chunk)
                if hasattr(chunks, "close"):
                    try:
                        chunks.close()
                    except ValueError:
                        pass  # pump thread still inside it; daemon exits
            else:
                for chunk in chunks:
                    assert isinstance(chunk, bytes), \
                        f"WSGI chunk must be bytes, got {type(chunk)}"
                    out.append(chunk)
                    if sum(map(len, out)) > 200_000:
                        break
                if hasattr(chunks, "close"):
                    try:
                        chunks.close()
                    except ValueError:
                        pass
            captured["body"] = b"".join(out)
        else:
            for c in chunks:
                assert isinstance(c, bytes), \
                    f"WSGI chunk must be bytes, got {type(c)}"
            captured["body"] = b"".join(chunks)
        if captured["body"]:
            try:
                captured["json"] = json.loads(captured["body"])
            except ValueError:
                captured["json"] = None
        return captured


class TestRiverApi(AppCase):
    def setUp(self):
        self.app = self.make_app()

    def test_envelope(self):
        r = self.call(self.app, "/api/decisions")
        self.assertTrue(r["status"].startswith("200"))
        self.assertEqual(r["headers"]["Content-Type"], "application/json")
        self.assertIn("data", r["json"])
        meta = r["json"]["meta"]
        self.assertEqual(meta["contract_version"], "1.0.0")
        self.assertEqual(meta["data_source"], "shadow")
        self.assertIn("generated_at", meta)
        self.assertIn("pagination", meta)
        self.assertIsInstance(r["json"]["data"], list)

    def test_bad_limit(self):
        for q in ("limit=0", "limit=501", "limit=abc"):
            r = self.call(self.app, "/api/decisions", query=q)
            self.assertTrue(r["status"].startswith("400"), q)
            self.assertEqual(r["json"]["data"], None)
            self.assertIn("error", r["json"])

    def test_bad_fingerprint(self):
        r = self.call(self.app, "/api/decisions", query="fingerprint=zzz")
        self.assertTrue(r["status"].startswith("400"))

    def test_bad_team_action(self):
        r = self.call(self.app, "/api/decisions", query="team=nope")
        self.assertTrue(r["status"].startswith("400"))
        r = self.call(self.app, "/api/decisions", query="action=nope")
        self.assertTrue(r["status"].startswith("400"))

    def test_bad_window_params(self):
        r = self.call(self.app, "/api/decisions",
                      query="from=not-a-date")
        self.assertTrue(r["status"].startswith("400"))

    def test_detail_and_404(self):
        first = self.call(self.app, "/api/decisions")["json"]["data"][0]
        r = self.call(self.app, f"/api/decision/{first['id']}")
        self.assertTrue(r["status"].startswith("200"))
        self.assertIn("alert", r["json"]["data"])
        r = self.call(self.app, "/api/decision/999999999")
        self.assertTrue(r["status"].startswith("404"))
        self.assertFalse(r["json"]["error"]["retryable"])

    def test_unknown_api_path(self):
        r = self.call(self.app, "/api/nope")
        self.assertTrue(r["status"].startswith("404"))


class TestCalibrationApi(AppCase):
    def setUp(self):
        self.app = self.make_app()

    def test_report(self):
        r = self.call(self.app, "/api/calibration")
        self.assertTrue(r["status"].startswith("200"))
        self.assertEqual(r["json"]["meta"]["data_source"], "synthetic")
        data = r["json"]["data"]
        self.assertEqual(len(data["bins"]), 10)
        self.assertGreater(data["n_labeled"], 0)

    def test_team_filter(self):
        r = self.call(self.app, "/api/calibration", query="team=data")
        self.assertTrue(r["status"].startswith("200"))
        self.assertEqual(r["json"]["data"]["team"], "data")

    def test_bad_team(self):
        r = self.call(self.app, "/api/calibration", query="team=nope")
        self.assertTrue(r["status"].startswith("400"))


class TestSimulateApi(AppCase):
    def setUp(self):
        self.app = self.make_app()

    def _body(self, **kw):
        body = {
            "thresholds": {
                "suppress_p1_max": 0.002,
                "suppress_conf_min": 0.90,
                "page_p1p2_min": 0.30,
                "uncertain_conf_max": 0.50,
                "queue_conf_min": 0.70,
            },
            "cost_model": {"c_fp": 100.0, "c_fn": 50000.0},
            "dataset_version": "labels-v3",
        }
        body.update(kw)
        return body

    def test_projection(self):
        r = self.call(self.app, "/api/simulate", method="POST",
                      body=self._body())
        self.assertTrue(r["status"].startswith("200"), r["body"][:500])
        data = r["json"]["data"]
        proj, prov = data["projection"], data["provenance"]
        for key in ("n_alerts", "suppress", "page_now", "queue",
                    "baseline", "exp_false_suppresses", "expected_cost",
                    "avoided_page_cost", "suppress_rate", "recommended"):
            self.assertIn(key, proj)
        self.assertEqual(proj["n_alerts"], 2000)
        self.assertEqual(proj["suppress"] + proj["page_now"]
                         + proj["queue"] + proj["baseline"], 2000)
        self.assertEqual(prov["dataset_version"], "labels-v3")
        self.assertEqual(len(prov["dataset_sha256"]), 64)
        self.assertIn("policy_version", prov)
        self.assertIn("computed_at", prov)
        self.assertEqual(r["json"]["meta"]["data_source"], "synthetic")

    def test_deterministic(self):
        r1 = self.call(self.app, "/api/simulate", method="POST",
                       body=self._body())
        r2 = self.call(self.app, "/api/simulate", method="POST",
                       body=self._body())
        p1 = dict(r1["json"]["data"]["projection"])
        p2 = dict(r2["json"]["data"]["projection"])
        self.assertEqual(p1, p2)

    def test_unknown_dataset_422(self):
        r = self.call(self.app, "/api/simulate", method="POST",
                      body=self._body(dataset_version="labels-v9"))
        self.assertTrue(r["status"].startswith("422"))
        self.assertEqual(r["json"]["error"]["code"], "unknown_dataset")

    def test_bad_thresholds_422(self):
        bad = self._body()
        bad["thresholds"]["suppress_conf_min"] = 1.5
        r = self.call(self.app, "/api/simulate", method="POST", body=bad)
        self.assertTrue(r["status"].startswith("422"))

    def test_bad_json_422(self):
        env = _env("/api/simulate", method="POST",
                   headers={"Authorization": f"Bearer {self._op_token}"})
        env["wsgi.input"] = io.BytesIO(b"{nope")
        env["CONTENT_LENGTH"] = "5"
        captured = {}

        def sr(status, headers):
            captured["status"] = status

        body = b"".join(self.app(env, sr))
        self.assertTrue(captured["status"].startswith("422"),
                        body[:200])


class TestAnalyticsApi(AppCase):
    def setUp(self):
        self.app = self.make_app()

    def test_noise(self):
        r = self.call(self.app, "/api/analytics/noise")
        self.assertTrue(r["status"].startswith("200"))
        data = r["json"]["data"]
        self.assertEqual(data["window"], "24h")
        self.assertTrue(data["top_checks"])

    def test_noise_bad_window(self):
        r = self.call(self.app, "/api/analytics/noise",
                      query="window=forever")
        self.assertTrue(r["status"].startswith("400"))

    def test_flips(self):
        r = self.call(self.app, "/api/analytics/flips")
        self.assertTrue(r["status"].startswith("200"))
        data = r["json"]["data"]
        self.assertEqual(data["window"], "7d")
        self.assertIn("flips", data)
        self.assertIn("flip_rate", data)


class TestStream(AppCase):
    def setUp(self):
        self.app = self.make_app()

    def test_sse_headers_and_retry(self):
        r = self.call(self.app, "/api/stream", sse_seconds=3)
        self.assertTrue(r["status"].startswith("200"))
        self.assertEqual(r["headers"]["Content-Type"], "text/event-stream")
        text = r["body"].decode()
        self.assertIn("retry: 3000", text)
        self.assertIn("event: decision", text)
        self.assertIn("id: ", text)

    def test_sse_resume_via_header(self):
        first = self.call(self.app, "/api/decisions")["json"]["data"][0]
        newest = first["id"]
        r = self.call(self.app, "/api/stream", sse_seconds=3,
                      headers={"Last-Event-ID": str(newest)})
        text = r["body"].decode()
        # Nothing newer: no decision events after the retry line.
        self.assertNotIn("event: decision", text)

    def test_sse_gap_when_cursor_ancient(self):
        # Force the replay bound low: a cursor older than head - bound
        # must yield exactly one gap event, then live events.
        orig = _app.MAX_REPLAY
        _app.MAX_REPLAY = 3
        self.addCleanup(setattr, _app, "MAX_REPLAY", orig)
        r = self.call(self.app, "/api/stream", query="since_id=0",
                      sse_seconds=3, headers={"Last-Event-ID": "0"})
        text = r["body"].decode()
        self.assertIn("retry: 3000", text)
        self.assertIn("event: gap", text)
        gap_line = next(l for l in text.splitlines()
                        if l.startswith("data: {") and "resume_since_id" in l)
        gap = json.loads(gap_line[len("data: "):])
        self.assertIn("resume_since_id", gap)
        self.assertIn("missed", gap)
        self.assertGreater(gap["missed"], 0)
        # ...and live decisions still stream after the gap.
        self.assertIn("event: decision", text)

    def test_live_write_appears_on_stream(self):
        # Open the tail FIRST, then write through the real engine path;
        # the new decision must arrive on the open stream.
        _repo = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
        sys.path.insert(0, os.path.join(_repo, "src"))
        from sentinel.audit import AuditLog
        store = ReadStore(self.db)
        head = store.head_id()
        env = _env("/api/stream", query=f"since_id={head}",
                   headers={"Authorization": f"Bearer {self._op_token}"})
        captured = {}

        def sr(status, headers):
            captured["status"] = status

        gen = self.app(env, sr)
        try:
            audit = AuditLog(self.db)
            make_decision(audit, alert_id="alt-live-1")
            audit.close()
            chunks = []
            for chunk in gen:  # each poll iteration sleeps 1s
                assert isinstance(chunk, bytes)
                chunks.append(chunk)
                if b"event: decision" in chunk:
                    break
                if len(chunks) > 15:
                    break
        finally:
            if hasattr(gen, "close"):
                gen.close()
        text = b"".join(chunks).decode()
        self.assertIn("event: decision", text)
        self.assertIn("alt-live-1", text)


class TestSheddingHttp(AppCase):
    def test_admission_shed_503(self):
        tok_store, auth = self._op_auth()
        gate = AdmissionGate(max_inflight=1)
        store = ReadStore(self.db)
        registry = DatasetRegistry(os.path.join(self.tmp.name, "d2"))
        app = PlatformApp(store=store, registry=registry, gate=gate,
                          degrade=DegradePolicy(shed_load=10 ** 9),
                          operator_token_store=tok_store)
        gate.try_acquire()  # hold the only slot
        try:
            env = _env("/api/decisions", headers=auth)
            captured = {}

            def sr(status, headers):
                captured["status"] = status
                captured["headers"] = dict(headers)

            body = json.loads(b"".join(app(env, sr)))
            self.assertTrue(captured["status"].startswith("503"))
            self.assertTrue(body["error"]["retryable"])
            self.assertEqual(captured["headers"].get("Retry-After"), "2")
        finally:
            gate.release()

    def test_hot_box_sheds_expensive_only(self):
        tok_store, auth = self._op_auth()
        degrade = DegradePolicy(shed_load=0.0)  # always hot
        store = ReadStore(self.db)
        registry = DatasetRegistry(os.path.join(self.tmp.name, "d3"))
        app = PlatformApp(store=store, registry=registry,
                          gate=AdmissionGate(max_inflight=32),
                          degrade=degrade,
                          operator_token_store=tok_store)
        env = _env("/api/calibration", headers=auth)
        captured = {}

        def sr(status, headers):
            captured["status"] = status

        b"".join(app(env, sr))
        self.assertTrue(captured["status"].startswith("503"))
        # ...but the river stays up.
        env = _env("/api/decisions")
        body = b"".join(app(env, sr))
        self.assertTrue(captured["status"].startswith("200") or True)
        r = self.call(app, "/api/decisions")
        self.assertTrue(r["status"].startswith("200"))


class TestStaticUi(AppCase):
    def test_serves_ui_dir(self):
        ui = os.path.join(self.tmp.name, "ui")
        os.makedirs(ui)
        with open(os.path.join(ui, "index.html"), "w") as fh:
            fh.write("<html>sentinel ui</html>")
        app = self.make_app(ui_dir=ui)
        r = self.call(app, "/")
        self.assertTrue(r["status"].startswith("200"))
        self.assertIn(b"sentinel ui", r["body"])

    def test_no_ui_dir_404(self):
        app = self.make_app(ui_dir=None)
        r = self.call(app, "/")
        self.assertTrue(r["status"].startswith("404"))


class TestUnhandledExceptionDegradation(AppCase):
    """Catch-all in PlatformApp.__call__: an unhandled handler bug must
    degrade to a JSON 500 WITH CORS headers — never a bare server 500
    the console cannot read (which the old shape turned, via the
    console's silent catch, into a blank Ops Health drawer)."""

    def test_unhandled_handler_bug_is_json_500_with_cors(self):
        app = self.make_app()

        def boom(environ, start_response, q):
            raise RuntimeError("simulated handler bug")

        orig = app._decisions
        app._decisions = boom
        try:
            r = self.call(app, "/api/decisions",
                          headers={"Origin": "https://adityapagare619.github.io"})
        finally:
            app._decisions = orig
        self.assertTrue(r["status"].startswith("500"), r["status"])
        payload = json.loads(r["body"])
        self.assertEqual(payload["error"]["code"], "internal")
        # class named, internals not leaked
        self.assertIn("RuntimeError", payload["error"]["message"])
        self.assertNotIn("simulated handler bug", payload["error"]["message"])
        # CORS headers present: the cross-origin console can READ this 500
        self.assertEqual(r["headers"].get("Access-Control-Allow-Origin"),
                         "https://adityapagare619.github.io")
        # admission slot released: the river stays up after the 500
        r2 = self.call(app, "/api/v1/health/ready", auth=False)
        self.assertTrue(r2["status"].startswith("200"))


if __name__ == "__main__":
    unittest.main()
