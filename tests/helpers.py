"""Shared fixtures for the Sentinel unit tests (stdlib unittest)."""

import hashlib
import json
import os
import unittest

from sentinel.correlator import fingerprint_of
from sentinel.models import Alert


def make_alert(service="web", check="http_5xx", severity="critical",
               region="us-east", alert_id="a1", source="pagerduty",
               title="boom", labels=None, raw=None):
    labels = dict(labels or {})
    labels.setdefault("region", region)
    alert = Alert(
        alert_id=alert_id,
        received_at="2026-10-02T12:00:00+00:00",
        fingerprint="",
        service=service,
        check=check,
        severity_in=severity,
        title=title,
        source=source,
        labels=labels,
        raw=dict(raw or {"summary": title}),
    )
    # ADR-017: the fingerprint is the scheme-v2 hash over the alert's own
    # labels (env/cluster included) — never computed from a subset.
    alert.fingerprint = fingerprint_of(alert)
    return alert


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


# ---------------------------------------------------------------------------
# R-10: flags.json test fixture. The loader requires flags.json
# (fail-closed), so every test that builds a config dir must write one.
# Overrides are flag-name -> value, e.g. write_flags_json(d,
# global_kill_switch=True).

DEFAULT_FLAG_VALUES = {
    "global_kill_switch": False,
    "suppress_enabled": True,
    "shadow_mode": False,
    "canary_severity_bands": [],
    "canary_services": [],
}


def write_flags_json(cfgdir, raw=None, **overrides):
    """Write a valid flags.json into cfgdir. `raw` writes bytes verbatim
    (for invalid-fixture tests); otherwise `overrides` set flag values."""
    os.makedirs(cfgdir, exist_ok=True)
    path = os.path.join(cfgdir, "flags.json")
    if raw is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(raw)
        return path
    values = dict(DEFAULT_FLAG_VALUES)
    values.update(overrides)
    data = {"version": 1,
            "flags": {name: {"value": value}
                      for name, value in values.items()}}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


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
