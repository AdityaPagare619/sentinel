#!/usr/bin/env python3
"""C2 load-test harness — MEASUREMENT, not a feature.

Drives receiver -> correlator -> gate -> forwarder over HTTP loopback with a
seeded synthetic vendor (latency model from L6's measured p50~1050/p95~4000/
p99~4350ms) and a stub PagerDuty endpoint. Every number is measured; nothing
is tuned to pass.

Profiles (Petu's decision):
  sustained : 1,000 events/min for 10 min  (10,000 events/run)
  burst     : 10,000 events/min for 2 min  (20,000 events/run)  <- rate, per C2 tier
Each profile runs 3x with fresh pipeline+DB per run. Seeds fixed.

Usage:
  python3 research/bin/load_c2_harness.py --profile sustained --run 0 --out research/load-c2
  python3 research/bin/load_c2_harness.py --smoke   # 60s sanity run
"""

import argparse
import hashlib
import hmac
import http.client
import http.server
import json
import os
import random
import socketserver
import sqlite3
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sentinel.receiver import (
    Pipeline, ReceiverConfig, Correlator, Gate, Forwarder, AuditLog,
    make_server,
)
from sentinel.config import ConfigLoader, record_restart
from sentinel.client import DecisionResponse, Answer

BASE_SEED = 20261004
WEBHOOK_SECRET = "loadtest-secret-0123456789abcdef"


# --------------------------------------------------------------------------
# Seeded synthetic vendor (L6 latency model)
# --------------------------------------------------------------------------

class LatencyStubClient:
    """Jev double with seeded latency matching L6's measured distribution.

    Model: 90% Normal(1050,200)ms clipped >=150ms; 7% U(2500,4000)ms;
    3% U(4000,5200)ms. Expected p50~1050, p95~3900, p99~4700.
    Records every call's wall duration; the harness reports achieved quantiles.
    """

    timeout_s = 30.0

    def __init__(self, seed: int):
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self.calls: list[tuple[float, float]] = []  # (start, end) perf_counter
        self.n_calls = 0

    def _latency_s(self) -> float:
        r = self._rng.random()
        if r < 0.90:
            return max(0.15, self._rng.gauss(1.05, 0.20))
        if r < 0.97:
            return self._rng.uniform(2.5, 4.0)
        return self._rng.uniform(4.0, 5.2)

    def _answer(self, qid, choice, probs, conf):
        return Answer(qid=qid, qtype="choice", choice=choice, noul=None,
                      probabilities=dict(probs), confidence=conf)

    def _response(self):
        r = self._rng.random()
        if r < 0.70:
            sev, sp, disp, dc = ("p3_medium",
                                 {"p1_critical": 0.02, "p2_high": 0.08,
                                  "p3_medium": 0.80, "p4_low": 0.10,
                                  "known_noise": 0.0, "cannot_determine": 0.0},
                                 "page", 0.88)
        elif r < 0.90:
            sev, sp, disp, dc = ("p4_low",
                                 {"p1_critical": 0.01, "p2_high": 0.04,
                                  "p3_medium": 0.10, "p4_low": 0.85,
                                  "known_noise": 0.0, "cannot_determine": 0.0},
                                 "suppress", 0.90)
        else:
            sev, sp, disp, dc = ("p1_critical",
                                 {"p1_critical": 0.90, "p2_high": 0.06,
                                  "p3_medium": 0.03, "p4_low": 0.01,
                                  "known_noise": 0.0, "cannot_determine": 0.0},
                                 "page", 0.95)
        return DecisionResponse(
            model="jev-stub-1.0",
            answers={
                "severity": self._answer("severity", sev, sp, dc),
                "owning_team": self._answer("owning_team", "platform",
                                            {"platform": 1.0}, 0.99),
                "disposition": self._answer("disposition", disp, {disp: 1.0}, dc),
            },
            input_tokens=100,
        )

    def decide(self, state, questions):
        t0 = time.perf_counter()
        time.sleep(self._latency_s())
        resp = self._response()
        t1 = time.perf_counter()
        with self._lock:
            self.calls.append((t0, t1))
            self.n_calls += 1
        return resp


# --------------------------------------------------------------------------
# Stub PagerDuty endpoint (instant 202, records arrivals)
# --------------------------------------------------------------------------

class StubPDServer:
    def __init__(self):
        self.arrivals: list[float] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                with outer._lock:
                    outer.arrivals.append(time.perf_counter())
                data = b'{"status":"accepted"}'
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/v2/enqueue"

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


# --------------------------------------------------------------------------
# Alert synthesis (seeded)
# --------------------------------------------------------------------------

SERVICES = [f"svc-{i:02d}" for i in range(40)]
CHECKS = [f"check-{c}" for c in
          ["cpu", "latency", "errors", "disk", "memory", "queue", "tls", "dns"]]
SEVERITIES = ["critical", "major", "minor", "info"]


def build_fp_pool(rng: random.Random, n: int):
    """n DISTINCT (service, check, severity) triples.

    Fingerprint = sha256(service|check|severity|region); check names are
    suffixed to make each triple distinct (a large estate has thousands of
    distinct checks). Pool sized so each fingerprint recurs less often than
    the 300s dedup window at the profile's rate -> mostly 'new' alerts.
    """
    pool = []
    for i in range(n):
        pool.append((SERVICES[i % 40],
                     f"{CHECKS[(i // 40) % 8]}-{i:06d}",
                     SEVERITIES[i % 4]))
    rng.shuffle(pool)
    return pool


def make_alert_body(rng: random.Random, fp_pool: list,
                    hot_pool: list, hot_frac: float, seq: int):
    if rng.random() < hot_frac:
        svc, chk, sev = rng.choice(hot_pool)
    else:
        svc, chk, sev = rng.choice(fp_pool)
    return {
        "alert_id": f"load-{seq:08d}",
        "service": svc,
        "check": chk,
        "severity": sev,
        "title": f"{svc} {chk} firing",
        "labels": {"env": "loadtest", "region": "r1"},
        "metric": {"value": rng.uniform(0, 100),
                   "threshold": 80.0,
                   "breach_duration_s": rng.randint(60, 900)},
    }


def build_arrivals(rng: random.Random, rate_per_s: float, duration_s: float,
                   storms: list[tuple[float, float, float]]):
    """Precompute (arrival_time, kind) list. storms: (start, dur, mult)."""
    arrivals = []
    t = 0.0
    while t < duration_s:
        t += rng.expovariate(rate_per_s)
        if t >= duration_s:
            break
        arrivals.append((t, "base"))
    for (s0, dur, mult) in storms:
        t = s0
        while t < s0 + dur:
            t += rng.expovariate(rate_per_s * mult)
            if t >= s0 + dur:
                break
            arrivals.append((t, "storm"))
    arrivals.sort(key=lambda x: x[0])
    return arrivals


# --------------------------------------------------------------------------
# One instrumented run
# --------------------------------------------------------------------------

def rss_mb() -> float:
    try:
        with open("/proc/self/statm") as fh:
            parts = fh.read().split()
        return int(parts[1]) * 4096 / 1e6
    except OSError:
        return -1.0


class Run:
    def __init__(self, profile: str, run_idx: int, out_dir: str):
        self.profile = profile
        self.run_idx = run_idx
        self.out_dir = out_dir
        self.seed = BASE_SEED + {"sustained": 0, "burst": 100,
                                 "smoke": 200, "sustained_uniform": 300,
                                 "burst_uniform": 400}[profile] + run_idx
        self.tmp = tempfile.TemporaryDirectory(prefix="loadc2_")
        # per-request records
        self.req_records: list[dict] = []
        self._rec_lock = threading.Lock()
        self.vendor_calls: list[tuple[float, float]] = []
        self.append_lat: list[float] = []
        self._app_lock = threading.Lock()
        self.samples: list[dict] = []
        self._stop_sampler = threading.Event()

    # ------------------------------------------------------------ setup
    def setup(self):
        cfgdir = os.path.join(self.tmp.name, "cfg")
        os.makedirs(cfgdir, exist_ok=True)
        with open(os.path.join(cfgdir, "thresholds.json"), "w") as fh:
            json.dump({}, fh)
        with open(os.path.join(cfgdir, "allowlist.json"), "w") as fh:
            json.dump([], fh)
        statedir = os.path.join(self.tmp.name, "state")
        loader = ConfigLoader(config_dir=cfgdir, state_dir=statedir)
        policy = loader.load_startup()
        record_restart(statedir)

        self.pd = StubPDServer()
        self.client = LatencyStubClient(self.seed + 7)
        db_path = os.path.join(self.tmp.name, "sentinel.db")
        audit = AuditLog(db_path)
        gate = Gate(self.client, policy.thresholds, list(policy.allowlist),
                    audit)
        forwarder = Forwarder(pd_events_url=self.pd.url,
                              default_routing_key="rk-loadtest",
                              timeout_s=5.0)
        config = ReceiverConfig(webhook_secret=WEBHOOK_SECRET)
        self.pipeline = Pipeline(Correlator(), gate, forwarder, audit, config,
                                 policy=policy, config_loader=loader,
                                 state_dir=statedir)
        self.server = make_server(0, self.pipeline)
        self._sthread = threading.Thread(target=self.server.serve_forever,
                                         daemon=True)
        self._sthread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._instrument()

    def _instrument(self):
        p, g = self.pipeline, self.pipeline.gate

        orig_triage = p._triage

        def triage(alert, raw_bytes):
            t0 = time.perf_counter()
            try:
                return orig_triage(alert, raw_bytes)
            finally:
                t1 = time.perf_counter()
                with self._rec_lock:
                    self.req_records.append(
                        {"kind": "triage", "t0": t0, "t1": t1,
                         "aid": alert.alert_id})
        p._triage = triage

        orig_run = g._runner.run
        outcomes = []

        def run(**kw):
            t0 = time.perf_counter()
            try:
                oc = orig_run(**kw)
                outcomes.append((oc.winner,
                                 getattr(oc, "budget_outcome", "")))
                return oc
            finally:
                t1 = time.perf_counter()
                with self._rec_lock:
                    self.req_records.append(
                        {"kind": "race", "t0": t0, "t1": t1,
                         "aid": kw["alert"].alert_id})
        g._runner.run = run
        self._race_outcomes = outcomes

        orig_forward = self.pipeline.forwarder.forward

        def forward(alert, disp, raw_bytes=None):
            t0 = time.perf_counter()
            try:
                return orig_forward(alert, disp, raw_bytes=raw_bytes)
            finally:
                t1 = time.perf_counter()
                with self._rec_lock:
                    self.req_records.append(
                        {"kind": "forward", "t0": t0, "t1": t1,
                         "action": disp.action, "aid": alert.alert_id})
        self.pipeline.forwarder.forward = forward

        orig_append = self.pipeline.audit._log.append_event

        def append_event(*a, **k):
            t0 = time.perf_counter()
            try:
                return orig_append(*a, **k)
            finally:
                with self._app_lock:
                    self.append_lat.append(
                        (time.perf_counter() - t0) * 1000.0)
        self.pipeline.audit._log.append_event = append_event

        self._race_metrics = g._race_metrics
        self._metrics_before = self._race_metrics.snapshot()

    # ------------------------------------------------------------ sampler
    def _sampler(self):
        while not self._stop_sampler.wait(5.0):
            self.samples.append({
                "t": time.perf_counter(),
                "rss_mb": rss_mb(),
                "threads": threading.active_count(),
                "correlator_seen": len(self.pipeline.correlator._seen),
                "pd_arrivals": len(self.pd.arrivals),
            })

    # ------------------------------------------------------------ driver
    def _post_one(self, body: bytes, tls_state: threading.local):
        ts = str(int(time.time()))
        sig = hmac.new(WEBHOOK_SECRET.encode(),
                       ts.encode("ascii") + b"." + body,
                       hashlib.sha256).hexdigest()
        t_send = time.perf_counter()
        try:
            conn = tls_state.conn
        except AttributeError:
            conn = http.client.HTTPConnection("127.0.0.1",
                                              self.server.server_address[1],
                                              timeout=30)
            tls_state.conn = conn
        try:
            conn.request("POST", "/webhook/generic", body=body, headers={
                "Content-Type": "application/json",
                "X-Sentinel-Signature": f"sha256={sig}",
                "X-Sentinel-Timestamp": ts,
            })
            resp = conn.getresponse()
            data = resp.read()
            t_recv = time.perf_counter()
            try:
                disp = json.loads(data).get("disposition")
            except Exception:
                disp = None
            return {"ok": True, "status": resp.status,
                    "t_send": t_send, "t_recv": t_recv,
                    "disposition": disp}
        except Exception as exc:  # timeout / conn error: data, not crash
            return {"ok": False, "error": type(exc).__name__,
                    "t_send": t_send, "t_recv": time.perf_counter(),
                    "disposition": None}

    def drive(self, rate_per_s: float, duration_s: float,
              storms: list[tuple[float, float, float]],
              fp_pool_size: int, hot_pool_size: int, hot_frac: float):
        rng = random.Random(self.seed + 13)
        fp_pool = build_fp_pool(rng, fp_pool_size)
        hot_pool = build_fp_pool(random.Random(self.seed + 77), hot_pool_size)
        # Storm arrivals use DISTINCT fingerprints so they exercise the
        # storm-fold path (kind="storm") rather than the dedup path.
        storm_pool = build_fp_pool(random.Random(self.seed + 91), 800)
        arrivals = build_arrivals(rng, rate_per_s, duration_s, storms)
        n = len(arrivals)
        results: list[dict] = []
        res_lock = threading.Lock()
        tls = threading.local()
        submit_lags: list[float] = []

        sampler = threading.Thread(target=self._sampler, daemon=True)
        sampler.start()
        t_run0 = time.perf_counter()
        self._metrics_before = self._race_metrics.snapshot()
        rss0 = rss_mb()
        corr0 = len(self.pipeline.correlator._seen)

        # storm arrivals use DISTINCT fingerprints to exercise the
        # storm-declare + storm-fold paths (not the dedup path)

        def task2(item):
            sched_t, kind, seq = item
            arng = random.Random(self.seed + 29 + seq)
            if kind == "storm":
                svc, chk, sev = arng.choice(storm_pool)
                body = json.dumps({
                    "alert_id": f"load-{seq:08d}",
                    "service": svc,
                    "check": chk,
                    "severity": sev,
                    "title": "storm firing",
                    "labels": {"env": "loadtest"},
                    "metric": {"value": 99.0, "threshold": 80.0,
                               "breach_duration_s": 60},
                }).encode()
            else:
                body = json.dumps(make_alert_body(
                    random.Random(self.seed + 29 + seq), fp_pool, hot_pool,
                    hot_frac, seq)).encode()
            r = self._post_one(body, tls)
            r["seq"] = seq
            r["kind"] = kind
            r["sched_t"] = sched_t
            with res_lock:
                results.append(r)

        with ThreadPoolExecutor(max_workers=512,
                                thread_name_prefix="gen") as pool:
            futs = []
            for i, (sched_t, kind) in enumerate(arrivals):
                now = time.perf_counter() - t_run0
                wait = sched_t - now
                if wait > 0:
                    time.sleep(wait)
                actual = time.perf_counter() - t_run0
                submit_lags.append(actual - sched_t)
                futs.append(pool.submit(task2, (sched_t, kind, i)))
            for f in futs:
                f.result()

        t_run1 = time.perf_counter()
        # drain: allow in-flight races/late answers to settle
        time.sleep(8.0)
        self._stop_sampler.set()
        sampler.join(timeout=10)

        metrics_after = self._race_metrics.snapshot()
        return {
            "profile": self.profile,
            "run_idx": self.run_idx,
            "seed": self.seed,
            "offered": n,
            "duration_s": t_run1 - t_run0,
            "rate_per_s_target": rate_per_s,
            "responses": results,
            "submit_lag_s": submit_lags,
            "race_outcomes": list(self._race_outcomes),
            "req_records": list(self.req_records),
            "race_metrics_delta": {k: metrics_after.get(k, 0) -
                                   self._metrics_before.get(k, 0)
                                   for k in set(metrics_after) |
                                   set(self._metrics_before)},
            "vendor_calls": list(self.client.calls),
            "append_lat_ms": list(self.append_lat),
            "pd_arrivals": list(self.pd.arrivals),
            "rss_start_mb": rss0,
            "rss_end_mb": rss_mb(),
            "correlator_seen_start": corr0,
            "correlator_seen_end": len(self.pipeline.correlator._seen),
            "forwarder_metrics": dict(self.pipeline.forwarder.metrics),
            "pipeline_metrics": dict(self.pipeline.metrics),
            "samples": self.samples,
        }

    def teardown(self):
        try:
            self.pipeline.health.stop()
        except Exception:
            pass
        try:
            self.pipeline.gate.close()
        except Exception:
            pass
        self.server.shutdown()
        self.server.server_close()
        self.pd.stop()
        self.tmp.cleanup()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

PROFILES = {
    # rate/s, duration_s, storms[(start, dur, mult)], fp_pool, hot_pool, hot_frac
    # Primary mix: Zipf-like skew (hot_frac duplicates) models a real noisy
    # estate: most volume is refiring noise (dedup path), the long tail is
    # new (race path), storm episodes fold. Uniform variants are sensitivity
    # probes for the storm detector's fixed 20-distinct/60s threshold.
    "sustained": dict(rate_per_s=1000 / 60.0, duration_s=600.0,
                      storms=[(150.0, 25.0, 5.0), (400.0, 25.0, 5.0)],
                      fp_pool_size=6000, hot_pool_size=60, hot_frac=0.80),
    "burst": dict(rate_per_s=10000 / 60.0, duration_s=120.0,
                  storms=[(5.0, 20.0, 8.0)],
                  fp_pool_size=60000, hot_pool_size=200, hot_frac=0.80),
    "sustained_uniform": dict(rate_per_s=1000 / 60.0, duration_s=600.0,
                              storms=[],
                              fp_pool_size=6000, hot_pool_size=60,
                              hot_frac=0.0),
    "burst_uniform": dict(rate_per_s=10000 / 60.0, duration_s=120.0,
                          storms=[],
                          fp_pool_size=60000, hot_pool_size=200, hot_frac=0.0),
    "smoke": dict(rate_per_s=5.0, duration_s=45.0, storms=[],
                  fp_pool_size=500, hot_pool_size=20, hot_frac=0.15),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=PROFILES, default="smoke")
    ap.add_argument("--run", type=int, default=0)
    ap.add_argument("--out", default="research/load-c2")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--no-storms", action="store_true",
                    help="drop the embedded storm episodes from the profile")
    args = ap.parse_args()
    profile = "smoke" if args.smoke else args.profile
    repo_root = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", ".."))
    out_arg = args.out.strip()
    out_dir = (out_arg if os.path.isabs(out_arg)
               else os.path.join(repo_root, out_arg))
    os.makedirs(out_dir, exist_ok=True)

    run = Run(profile, args.run, out_dir)
    print(f"[loadc2] profile={profile} run={args.run} seed={run.seed}",
          flush=True)
    run.setup()
    try:
        prof = dict(PROFILES[profile])
        if args.no_storms:
            prof["storms"] = []
        res = run.drive(**prof)
    finally:
        run.teardown()
    # JSON-serializable
    res["responses"] = [
        {k: (v if not isinstance(v, float) else round(v, 6))
         for k, v in r.items()} for r in res["responses"]]
    path = os.path.join(out_dir, f"{profile}-run{args.run}.json")
    with open(path, "w") as fh:
        json.dump(res, fh)
    n_ok = sum(1 for r in res["responses"] if r["ok"])
    print(f"[loadc2] done: offered={res['offered']} ok={n_ok} "
          f"vendor_calls={len(res['vendor_calls'])} "
          f"appends={len(res['append_lat_ms'])} -> {path}", flush=True)


if __name__ == "__main__":
    main()
