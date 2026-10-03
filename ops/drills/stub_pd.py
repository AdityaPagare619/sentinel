#!/usr/bin/env python3
"""Recording stub PagerDuty endpoint (Tripwire drill harness).

POST -> append one line to --out: {"ts", "status", "body_len", "body"}
(short body preview only — no secrets are stored; routing keys stay out).
Replies 202 {"status":"success"} — the intake behavior the forwarder
treats as forward_confirmed (pd_sender.classify).
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    server_version = "stub-pd/0.1"

    def log_message(self, fmt, *args):
        pass

    def _rec(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else b""
        try:
            decoded = body.decode("utf-8", "replace")
        except Exception:
            decoded = "<undecodable>"
        rec = {
            "ts": time.time(),
            "status": None,
            "body_len": len(body),
            "preview": decoded[:200],
            "dedup_key": None,
        }
        try:
            parsed = json.loads(decoded)
            if isinstance(parsed, dict):
                rec["dedup_key"] = parsed.get("dedup_key")
        except Exception:
            pass
        resp = json.dumps({"status": "success",
                           "message": "Event processed",
                           "dedup_key": rec["dedup_key"]}).encode()
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)
        rec["status"] = 202
        with self.server.out_lock:
            with open(self.server.out_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")

    def do_POST(self):
        try:
            self._rec()
        except BrokenPipeError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    open(args.out, "a").close()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.out_path = args.out
    srv.out_lock = threading.Lock()
    srv.serve_forever()


if __name__ == "__main__":
    main()
