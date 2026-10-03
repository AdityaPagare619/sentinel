#!/usr/bin/env python3
"""Webhook burst sender (Tripwire drill harness).

Fires JSONL payloads at the receiver and reports status-code distribution
and wall latency. Usage:
  fire.py --port 8080 --in storm.jsonl --path /v2/enqueue [--limit N] [--concurrency K]
"""
import argparse
import json
import threading
import time
import urllib.error
import urllib.request


def send_one(url, payload, results, idx):
    t0 = time.perf_counter()
    req = urllib.request.Request(
        url, data=payload.encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            code = resp.getcode()
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:
        code = f"ERR:{type(e).__name__}"
    dt = (time.perf_counter() - t0) * 1000.0
    results[idx] = (code, dt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="webhook URL")
    ap.add_argument("--file", dest="inp", required=True,
                    help="JSONL payload file (one PD webhook per line)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--hist", default="", help="latency histogram CSV out")
    args = ap.parse_args()
    with open(args.inp) as fh:
        payloads = [line.strip() for line in fh if line.strip()]
    if args.limit:
        payloads = payloads[:args.limit]
    url = args.url
    if args.hist:
        with open(args.hist, "w") as fh:
            fh.write("latency_ms\n")
    n = len(payloads)
    results = [None] * n
    sem = threading.Semaphore(args.concurrency)

    def worker(idx):
        with sem:
            send_one(url, payloads[idx], results, idx)

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0

    codes = {}
    lats = []
    for code, dt in results:
        codes[code] = codes.get(code, 0) + 1
        if isinstance(dt, float):
            lats.append(dt)
    if args.hist:
        with open(args.hist, "a") as fh:
            for dt in lats:
                fh.write(f"{dt:.1f}\n")
    lats.sort()
    def pct(p):
        return lats[int(p / 100 * (len(lats) - 1))] if lats else 0.0
    print(json.dumps({
        "n": n, "wall_s": round(wall, 3),
        "codes": {str(k): v for k, v in codes.items()},
        "lat_p50_ms": round(pct(50), 1),
        "lat_p95_ms": round(pct(95), 1),
        "lat_p99_ms": round(pct(99), 1),
        "lat_max_ms": round(lats[-1], 1) if lats else 0.0,
    }, indent=1))


if __name__ == "__main__":
    main()
