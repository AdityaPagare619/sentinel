"""Shared fixtures for the Sentinel unit tests (stdlib unittest)."""

import hashlib
import unittest

from sentinel.correlator import fingerprint_for
from sentinel.models import Alert


def make_alert(service="web", check="http_5xx", severity="critical",
               region="us-east", alert_id="a1", source="pagerduty",
               title="boom", labels=None, raw=None):
    labels = dict(labels or {})
    labels.setdefault("region", region)
    fp = fingerprint_for(service, check, severity, region)
    return Alert(
        alert_id=alert_id,
        received_at="2026-10-02T12:00:00+00:00",
        fingerprint=fp,
        service=service,
        check=check,
        severity_in=severity,
        title=title,
        source=source,
        labels=labels,
        raw=dict(raw or {"summary": title}),
    )


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


# ---------------------------------------------------------------------------
# Loopback HTTP capture server: stands in for the PagerDuty Events API.
# Records every (path, headers, body) POST; always answers 202.

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class CaptureServer:
    def __init__(self, status=202):
        self.requests = []  # list of dicts: path, headers, body
        self.status = status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append({
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": body,
                })
                data = b'{"status":"success"}'
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/v2/enqueue"

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
