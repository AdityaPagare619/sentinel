"""Tests for sentinel.forwarder (§3.7)."""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import json
import unittest

from sentinel.forwarder import Forwarder, ForwardResult
from sentinel.models import Disposition

from tests.helpers import CaptureServer, make_alert


def _disp(action, reason="threshold"):
    return Disposition(action=action, reason=reason, team="platform",
                       confidence=0.9, latency_ms=1.0)


def _pd_raw():
    return {"routing_key": "rk-abc", "event_action": "trigger",
            "dedup_key": "dk-1",
            "payload": {"summary": "disk full", "source": "web-1",
                        "severity": "critical", "component": "web",
                        "custom_details": {"disk": "/dev/sda1"}}}


class TestForward(unittest.TestCase):
    def setUp(self):
        self.capture = CaptureServer()
        self.addCleanup(self.capture.close)
        self.fw = Forwarder(pd_events_url=self.capture.url)

    def test_page_now_posts_original_payload(self):
        raw = _pd_raw()
        raw_bytes = json.dumps(raw).encode()
        alert = make_alert(raw=raw)
        result = self.fw.forward(alert, _disp("page_now"), raw_bytes=raw_bytes)
        self.assertTrue(result.forwarded)
        self.assertEqual(result.status_code, 202)
        self.assertEqual(len(self.capture.requests), 1)
        self.assertEqual(self.capture.requests[0]["body"], raw_bytes)

    def test_passthrough_posts_original_payload(self):
        raw = _pd_raw()
        raw_bytes = json.dumps(raw).encode()
        alert = make_alert(raw=raw)
        result = self.fw.forward(alert, _disp("passthrough"), raw_bytes=raw_bytes)
        self.assertTrue(result.forwarded)
        sent = json.loads(self.capture.requests[0]["body"])
        self.assertEqual(sent, raw)  # unchanged

    def test_suppress_does_not_forward(self):
        alert = make_alert(raw=_pd_raw())
        result = self.fw.forward(alert, _disp("suppress", "allowlist"))
        self.assertFalse(result.forwarded)
        self.assertIsNone(result.status_code)
        self.assertEqual(self.capture.requests, [])
        self.assertEqual(self.fw.metrics["suppressed"], 1)

    def test_business_hours_downgrades_severity(self):
        raw = _pd_raw()
        alert = make_alert(raw=raw)
        result = self.fw.forward(alert, _disp("page_business_hours"))
        self.assertTrue(result.forwarded)
        sent = json.loads(self.capture.requests[0]["body"])
        self.assertEqual(sent["routing_key"], "rk-abc")  # original key kept
        self.assertEqual(sent["event_action"], "trigger")
        self.assertEqual(sent["payload"]["severity"], "warning")
        self.assertEqual(sent["payload"]["custom_details"]["sentinel_queue"],
                         "business_hours")
        # original raw dict untouched
        self.assertEqual(raw["payload"]["severity"], "critical")

    def test_generic_alert_uses_default_routing_key(self):
        fw = Forwarder(pd_events_url=self.capture.url,
                       default_routing_key="rk-default")
        alert = make_alert(source="generic", title="cpu hot",
                           raw={"service": "web"})
        result = fw.forward(alert, _disp("page_now"))
        self.assertTrue(result.forwarded)
        sent = json.loads(self.capture.requests[0]["body"])
        self.assertEqual(sent["routing_key"], "rk-default")
        self.assertEqual(sent["event_action"], "trigger")

    def test_no_routing_key_is_loud_error_not_raise(self):
        alert = make_alert(source="generic", raw={"service": "web"})
        result = self.fw.forward(alert, _disp("page_now"))  # must not raise
        self.assertFalse(result.forwarded)
        self.assertIsNotNone(result.error)
        self.assertEqual(self.capture.requests, [])
        self.assertEqual(self.fw.metrics["errors"], 1)

    def test_no_authorization_header_sent(self):
        raw = _pd_raw()
        alert = make_alert(raw=raw)
        self.fw.forward(alert, _disp("page_now"),
                        raw_bytes=json.dumps(raw).encode())
        headers = {k.lower(): v for k, v in
                   self.capture.requests[0]["headers"].items()}
        self.assertNotIn("authorization", headers)


class TestForwardErrors(unittest.TestCase):
    def test_http_500_is_recorded_not_raised(self):
        capture = CaptureServer(status=500)
        self.addCleanup(capture.close)
        fw = Forwarder(pd_events_url=capture.url)
        raw = _pd_raw()
        alert = make_alert(raw=raw)
        result = fw.forward(alert, _disp("page_now"),
                            raw_bytes=json.dumps(raw).encode())
        self.assertFalse(result.forwarded)
        self.assertEqual(result.status_code, 500)
        self.assertIsNotNone(result.error)
        self.assertEqual(fw.metrics["errors"], 1)

    def test_connection_refused_is_recorded_not_raised(self):
        fw = Forwarder(pd_events_url="http://127.0.0.1:1/v2/enqueue",
                       timeout_s=1.0)
        alert = make_alert(raw=_pd_raw())
        result = fw.forward(alert, _disp("page_now"))
        self.assertFalse(result.forwarded)
        self.assertIsNone(result.status_code)
        self.assertIsNotNone(result.error)

    def test_forward_raw_unparseable_bytes(self):
        capture = CaptureServer()
        self.addCleanup(capture.close)
        fw = Forwarder(pd_events_url=capture.url)
        body = b"\x00\x01 not json \xff"
        result = fw.forward_raw(body, alert_id="unparseable")
        self.assertTrue(result.forwarded)
        self.assertEqual(capture.requests[0]["body"], body)


if __name__ == "__main__":
    unittest.main()
