#!/usr/bin/env python3
"""Always-529 stub Jev endpoint with a call counter (Tripwire drill harness).

Every POST to /v1/systemone -> HTTP 529 + increments the counter file.
Exercised through the REAL SystemOneClient (retry policy: 529 -> retry x3
within retry_budget_s -> JevOverloaded).
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    server_version = "stub-jev529/0.1"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = b'{"error":"model overloaded"}'
        self.send_response(529)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        try:
            with open(self.server.count_path, "a+") as fh:
                fh.seek(0)
                n = int((fh.read() or "0").strip() or "0")
            with open(self.server.count_path, "w") as fh:
                fh.write(str(n + 1))
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--count-out", required=True)
    args = ap.parse_args()
    with open(args.count_out, "w") as fh:
        fh.write("0")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.count_path = args.count_out
    srv.serve_forever()


if __name__ == "__main__":
    main()
