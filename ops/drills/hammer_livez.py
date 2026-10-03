#!/usr/bin/env python3
"""Hammer the receiver read path (GET /livez) with concurrent loopers.

Tripwire drill 5: saturate the platform read path while the paging path
fires alerts, then verify paging latency/throughput are unaffected.
Runs until --duration_s elapses; prints request count + errors.
"""
import argparse
import threading
import time
import urllib.request


def looper(url, stop, stats):
    while not stop.is_set():
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                r.read()
            stats["ok"] += 1
        except Exception:
            stats["err"] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8080/livez")
    ap.add_argument("--loopers", type=int, default=16)
    ap.add_argument("--duration_s", type=float, default=60)
    args = ap.parse_args()
    stop = threading.Event()
    stats = {"ok": 0, "err": 0}
    ts = [threading.Thread(target=looper, args=(args.url, stop, stats))
          for _ in range(args.loopers)]
    t0 = time.monotonic()
    for t in ts:
        t.start()
    time.sleep(args.duration_s)
    stop.set()
    for t in ts:
        t.join()
    dt = time.monotonic() - t0
    print(f"hammer: {stats['ok']} ok, {stats['err']} err in {dt:.1f}s "
          f"({stats['ok']/dt:.0f} req/s across {args.loopers} loopers)")


if __name__ == "__main__":
    main()
