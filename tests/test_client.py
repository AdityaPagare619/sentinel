"""Tests for client.py — System-One wire client + mock (contract §3.1)."""

import io
import json
import os
import sys
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sentinel.client import (  # noqa: E402
    Answer,
    DecisionResponse,
    JevAuthError,
    JevError,
    JevOverloaded,
    JevRateLimited,
    JevTimeout,
    MockSystemOneClient,
    SystemOneClient,
    client_from_env,
)

FAKE_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "severity": {
            "type": "choice",
            "choice": "p1_critical",
            "probabilities": {"p1_critical": 0.91, "p2_high": 0.05},
            "confidence": 0.91,
        },
        "owning_team": {
            "type": "choice",
            "choice": "platform",
            "probabilities": {"platform": 0.72},
            "confidence": 0.72,
        },
        "disposition": {
            "type": "choice",
            "choice": "page_now",
            "probabilities": {"page_now": 0.88},
            "confidence": 0.88,
        },
        "confidence_score": {
            "type": "noul",
            "noul": 0.75,
        },
    },
    "usage": {"input_tokens": 612, "output_tokens": 40},
}


class FakeHTTPResponse:
    """Minimal urllib response stand-in (context manager)."""

    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, retry_after=None):
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError(
        "https://api.typesafe.ai/v1/systemone", code, "error", headers, io.BytesIO(b"{}")
    )


class ScriptedOpener:
    """Feeds scripted outcomes to urlopen; records every call."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ClientTestBase(unittest.TestCase):
    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen
        self._orig_sleep = time.sleep
        self.sleeps = []
        time.sleep = self.sleeps.append  # record instead of sleeping

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen
        time.sleep = self._orig_sleep

    def install(self, outcomes):
        opener = ScriptedOpener(outcomes)
        urllib.request.urlopen = opener
        return opener

    def make_client(self, **kwargs):
        kwargs.setdefault("api_key", "test-key-123")
        kwargs.setdefault("retry_budget_s", 30.0)
        return SystemOneClient(**kwargs)


class TestRequestShape(ClientTestBase):
    def test_request_body_shape_and_headers(self):
        opener = self.install([FakeHTTPResponse(FAKE_RESPONSE)])
        client = self.make_client()
        state = {"title": "disk full"}
        questions = {"severity": {"type": "choice", "instructions": "x", "criteria": {"p1_critical": "d"}}}

        resp = client.decide(state, questions)

        self.assertEqual(len(opener.requests), 1)
        req = opener.requests[0]
        self.assertEqual(req.full_url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(req.get_method(), "POST")
        # header names are case-insensitive on the wire; urllib may normalize case
        sent = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(sent.get("authorization"), "Bearer test-key-123")
        self.assertEqual(sent.get("content-type"), "application/json")
        ua = sent.get("user-agent")
        self.assertTrue(ua.startswith("sentinel/"), f"unexpected User-Agent: {ua!r}")
        self.assertNotIn("Python-urllib", ua)

        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["state"], state)
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(body["questions"], questions)

        self.assertEqual(resp.model, "jev-1.13.0")
        self.assertEqual(resp.input_tokens, 612)
        sev = resp.answers["severity"]
        self.assertEqual(sev.qtype, "choice")
        self.assertEqual(sev.choice, "p1_critical")
        self.assertAlmostEqual(sev.confidence, 0.91)
        self.assertAlmostEqual(sev.probabilities["p1_critical"], 0.91)

    def test_base_url_override_clone_wire_format(self):
        opener = self.install([FakeHTTPResponse(FAKE_RESPONSE)])
        client = self.make_client(base_url="https://clone.example:8443/")
        client.decide({}, {})
        self.assertEqual(opener.requests[0].full_url, "https://clone.example:8443/v1/systemone")

    def test_noul_answer_has_no_confidence(self):
        self.install([FakeHTTPResponse(FAKE_RESPONSE)])
        resp = self.make_client().decide({}, {})
        ans = resp.answers["confidence_score"]
        self.assertEqual(ans.qtype, "noul")
        self.assertAlmostEqual(ans.noul, 0.75)
        self.assertIsNone(ans.confidence)


class TestRetryPolicy(ClientTestBase):
    def test_retry_on_529_then_success(self):
        opener = self.install([http_error(529), http_error(529), FakeHTTPResponse(FAKE_RESPONSE)])
        resp = self.make_client().decide({}, {})
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(resp.model, "jev-1.13.0")

    def test_529_exhausted_within_budget_raises_overloaded(self):
        opener = self.install([http_error(529)])
        with self.assertRaises(JevOverloaded):
            self.make_client(max_retries=3, retry_budget_s=30.0).decide({}, {})
        self.assertEqual(len(opener.requests), 3)  # at most 3 attempts

    def test_timeout_retries_then_raises_jev_timeout(self):
        import socket

        opener = self.install([urllib.error.URLError(socket.timeout("timed out"))])
        with self.assertRaises(JevTimeout):
            self.make_client(max_retries=3, retry_budget_s=30.0).decide({}, {})
        self.assertEqual(len(opener.requests), 3)

    def test_401_no_retry(self):
        opener = self.install([http_error(401)])
        with self.assertRaises(JevAuthError):
            self.make_client().decide({}, {})
        self.assertEqual(len(opener.requests), 1)

    def test_422_no_retry_surfaces_field(self):
        opener = self.install([http_error(422)])
        with self.assertRaises(JevError) as ctx:
            self.make_client().decide({}, {})
        self.assertNotIsInstance(ctx.exception, (JevAuthError, JevRateLimited, JevOverloaded, JevTimeout))
        self.assertEqual(len(opener.requests), 1)

    def test_429_honors_retry_after(self):
        opener = self.install([http_error(429, retry_after=2), FakeHTTPResponse(FAKE_RESPONSE)])
        resp = self.make_client(retry_budget_s=30.0).decide({}, {})
        self.assertEqual(resp.model, "jev-1.13.0")
        self.assertIn(2, self.sleeps)

    def test_429_retry_after_beyond_budget_raises_immediately(self):
        opener = self.install([http_error(429, retry_after=60)])
        with self.assertRaises(JevRateLimited):
            self.make_client(retry_budget_s=2.0).decide({}, {})
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(self.sleeps, [])

    def test_retry_budget_caps_total_attempts(self):
        # retry_after of 0 would allow many attempts; budget ends it instead.
        opener = self.install([http_error(529)])
        with self.assertRaises(JevOverloaded):
            self.make_client(max_retries=10, retry_budget_s=0.0).decide({}, {})
        self.assertLessEqual(len(opener.requests), 10)

    def test_exponential_backoff_increases(self):
        opener = self.install([http_error(529)])
        with self.assertRaises(JevOverloaded):
            self.make_client(max_retries=3, retry_budget_s=30.0).decide({}, {})
        self.assertEqual(len(self.sleeps), 2)
        self.assertLess(self.sleeps[0], self.sleeps[1])


class TestClientFromEnv(ClientTestBase):
    def test_missing_key_raises_helpful_jev_auth_error(self):
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(JevAuthError) as ctx:
                client_from_env()
        self.assertIn("TYPESAFE_API_KEY", str(ctx.exception))

    def test_key_from_env(self):
        with unittest.mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "env-key"}):
            client = client_from_env()
        self.assertEqual(client.api_key, "env-key")

    def test_api_key_never_leaks_into_error_messages(self):
        opener = self.install([http_error(401)])
        client = self.make_client(api_key="sk-super-secret-xyz")
        with self.assertRaises(JevAuthError) as ctx:
            client.decide({}, {})
        self.assertNotIn("sk-super-secret-xyz", str(ctx.exception))


class TestMockClient(unittest.TestCase):
    def _canned(self):
        return DecisionResponse(
            model="jev-1.13.0",
            answers={
                "severity": Answer(
                    qid="severity", qtype="choice", choice="known_noise",
                    noul=None, probabilities={"known_noise": 0.95}, confidence=0.95,
                )
            },
            input_tokens=100,
        )

    def test_mock_records_calls_and_returns_scripted(self):
        from sentinel.state import input_sha256

        mock = MockSystemOneClient()
        state = {"title": "flapping redis check"}
        questions = {"severity": {"type": "choice"}}
        fp = input_sha256(state)
        canned = self._canned()
        mock._script[fp] = canned

        result = mock.decide(state, questions)

        self.assertIs(result, canned)
        self.assertEqual(len(mock.calls), 1)
        call = mock.calls[0]
        self.assertEqual(call["fingerprint"], fp)
        self.assertEqual(call["state"], state)
        self.assertEqual(call["questions"], questions)

    def test_mock_missing_script_raises_jev_error(self):
        mock = MockSystemOneClient()
        with self.assertRaises(JevError):
            mock.decide({"title": "unscripted"}, {})

    def test_mock_does_not_touch_network(self):
        def boom(*a, **k):
            raise AssertionError("mock must not hit the network")

        orig = urllib.request.urlopen
        urllib.request.urlopen = boom
        try:
            from sentinel.state import input_sha256

            mock = MockSystemOneClient()
            state = {"x": 1}
            mock._script[input_sha256(state)] = self._canned()
            self.assertEqual(mock.decide(state, {}).model, "jev-1.13.0")
        finally:
            urllib.request.urlopen = orig


if __name__ == "__main__":
    unittest.main()
