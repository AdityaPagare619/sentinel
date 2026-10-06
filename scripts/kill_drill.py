#!/usr/bin/env python3
"""Kill-switch drill (contract C3, Track 3).

Measures flip → forwarder-halt on the REAL path — no fictional claims:

  1. Boots an in-process pipeline: EventLog (file) → KillSwitch (log-backed)
     → DurableForwarder (kill-wired, local fake PD endpoint) → PlatformApp
     (the REAL WSGI route POST /api/v1/safety/kill, C1-authed) on 127.0.0.1.
  2. Enqueues page_now decisions; waits for >=1 forward_confirmed (the
     paging path is provably hot).
  3. POSTs /api/v1/safety/kill with a drill operator token.
  4. Polls the decision log for the forwarder_halted event.
  5. measured_ms = forwarder_halted.ts - kill_switch_engaged.ts, both from
     DECISION-LOG timestamps (contract C3).
  6. Verifies: 401 without a token · 422 re-arm without {"confirm": true} ·
     no PD sends after the halt · re-arm recovers the pipeline.
  7. Prints the measured ms; writes ops/drills/kill-drill-<ts>.json and
     ops/drills/kill-drill-latest.json.

Exit 0 iff measured_ms < --threshold-ms (default 5000) and every check
passes. The <5s claim is FORBIDDEN until this artifact exists — the
console must show "unmeasured" when ops/drills/kill-drill-latest.json
is absent (see sentinel.safety.latest_drill).

Local CPU only. No Jev is consulted anywhere in this drill — the kill path
never calls Jev, never waits on the race (control principle).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socketserver
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))          # scripts/
_REPO = os.path.dirname(_HERE)                              # repo root
_SRC = os.path.join(_REPO, "src")
_SERVER = os.path.join(_REPO, "platform", "server")
for _p in (_SRC, _SERVER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _pkg  # noqa: E402  (platform/ shadows stdlib platform)

from sentinel.eventlog import EventLog, utcnow_iso  # noqa: E402
from sentinel.forwarder import DurableForwarder, ForwarderConfig  # noqa: E402
from sentinel import safety as _safety  # noqa: E402

_app_mod = _pkg.load("app")
_datasets_mod = _pkg.load("datasets")
_shed_mod = _pkg.load("shed")
_store_mod = _pkg.load("store")

THRESHOLD_DEFAULT_MS = 5000.0


# ---------------------------------------------------------------- fake PD


class _FakePDHandler(BaseHTTPRequestHandler):
    """202 {"status": "success"} for every POST — the 'accepted' path."""

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = json.dumps({"status": "success",
                           "message": "Event processed",
                           "dedup_key": "drill"}).encode()
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.server.hits += 1

    def log_message(self, *args):  # keep the drill output clean
        pass


class _FakePD(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _FakePDHandler)
        self.hits = 0
        self._thread = threading.Thread(target=self.serve_forever,
                                        daemon=True)
        self._thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}/v2/enqueue"

    def close(self):
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)


# ---------------------------------------------------------------- helpers


def _ts_to_ms(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000.0


def _http(method, url, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode() or "{}")
        except ValueError:
            payload = {}
        return e.code, payload


def _enqueue_page(log: EventLog, i: int) -> str:
    alert_id, fp = f"drill-a{i}", f"drill-fp-{i:04d}"
    frozen = json.dumps({
        "event_action": "trigger",
        "dedup_key": f"sentinel/drill/{fp}",
        "payload": {"summary": f"[DRILL] kill drill page {i}",
                    "severity": "critical", "source": "kill-drill",
                    "custom_details": {"drill": True}},
    }, sort_keys=True)
    now = utcnow_iso()
    seq, obid = log.record_decision_and_enqueue(
        alert_id=alert_id, fingerprint=fp, episode_id=fp,
        body={"disposition": "page_now",
              "mode": "live",
              "budget_outcome": "answered_in_time",
              "lock_evaluation": {}, "freshness": {},
              "threshold_counterfactual": {}, "links": {},
              "v01_compat": {"reason": "threshold"}},
        outbox={"alert_id": alert_id, "fingerprint": fp, "episode_id": fp,
                "dedup_key": f"sentinel/drill/{fp}",
                "routing_key_ref": "DRILL_PD_ROUTING_KEY",
                "payload_frozen": frozen,
                "payload_sha256": hashlib.sha256(
                    frozen.encode()).hexdigest(),
                "priority": 0, "next_attempt_at": now,
                "max_age_at": _ts_plus(now, 86400)})
    return obid


def _ts_plus(iso: str, seconds: float) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    out = dt + timedelta(seconds=seconds)
    return out.strftime("%Y-%m-%dT%H:%M:%S.") + f"{out.microsecond // 1000:03d}Z"


def _wait_for(pred, timeout_s, what, poll_s=0.05):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        val = pred()
        if val:
            return val
        time.sleep(poll_s)
    raise TimeoutError(f"drill timed out waiting for: {what}")


def _git_rev():
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=_REPO, capture_output=True, text=True,
                             timeout=5)
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------- drill


def run_drill(args) -> dict:
    tmp = tempfile.mkdtemp(prefix="sentinel-kill-drill-")
    db = os.path.join(tmp, "sentinel.db")
    artifact_dir = args.artifact_dir
    os.makedirs(artifact_dir, exist_ok=True)

    os.environ["DRILL_PD_ROUTING_KEY"] = "drill-routing-key-not-a-secret"

    log = EventLog(db)
    ks = _safety.KillSwitch(log=log)
    fake_pd = _FakePD()

    # Drill operator identity through the C1 seam (Track 1 plugs the real
    # verifier here; the drill's token is single-use and never leaves the
    # process).
    token = secrets.token_urlsafe(32)
    _safety.set_operator_verifier(
        lambda tok: "kill-drill" if tok == token else None)

    fwd = DurableForwarder(
        log,
        config=ForwarderConfig(
            env="drill", pd_endpoint=fake_pd.url, pd_timeout_s=5.0,
            workers=2, poll_s=0.05, backoff_s=(0.05, 0.1, 0.2), jitter=0.0,
            secondary_fire_after_s=3600.0,
            spill_dir=os.path.join(tmp, "spill"), stage="shadow"),
        kill_switch=ks)

    app = _app_mod.PlatformApp(
        store=_store_mod.ReadStore(db),
        registry=_datasets_mod.DatasetRegistry(os.path.join(tmp, "datasets")),
        gate=_shed_mod.AdmissionGate(), degrade=_shed_mod.DegradePolicy(),
        kill_switch=ks, drill_dir=artifact_dir)

    from wsgiref.simple_server import make_server, WSGIServer
    from socketserver import ThreadingMixIn

    class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    http = make_server("127.0.0.1", 0, app,
                       server_class=ThreadedWSGIServer)
    port = http.server_port
    http_thread = threading.Thread(target=http.serve_forever, daemon=True)
    http_thread.start()
    base = f"http://127.0.0.1:{port}"

    checks = {}
    started_at = utcnow_iso()
    try:
        # 1. Hot path: enqueue pages, prove the forwarder is delivering.
        for i in range(6):
            _enqueue_page(log, i)
        fwd.start()
        _wait_for(lambda: log.events_by_type("forward_confirmed", 10),
                  30, "first forward_confirmed")
        checks["hot_path_confirmed"] = True
        print(f"[drill] paging path hot: {fake_pd.hits} PD sends so far",
              flush=True)

        # 2. C1: no token → 401 {"error": "unauthorized"} (verbatim).
        status, payload = _http("POST", base + "/api/v1/safety/kill")
        checks["unauth_401"] = (status == 401
                                and payload.get("error") == "unauthorized")
        print(f"[drill] unauthenticated kill → {status} {payload}", flush=True)

        # 3. THE FLIP — through the real HTTP endpoint.
        status, payload = _http("POST", base + "/api/v1/safety/kill",
                               token=token)
        assert status == 200, f"kill endpoint failed: {status} {payload}"
        checks["kill_200"] = True
        print(f"[drill] kill flipped via HTTP: {payload}", flush=True)

        # 4. Wait for the forwarder's own halt observation in the log.
        halted = _wait_for(
            lambda: log.events_by_type("forwarder_halted", 5),
            30, "forwarder_halted event")
        halt_row = halted[0]
        engaged = log.events_by_type("kill_switch_engaged", 5)[0]

        # Contract C3: measured from DECISION-LOG timestamps — the event
        # rows' ts columns (the log's own clock), not wall-clock around
        # the HTTP call.
        flip_ms = _ts_to_ms(engaged["ts"])
        halt_ms = _ts_to_ms(halt_row["ts"])
        measured_ms = halt_ms - flip_ms
        checks["measured_from_log_ts"] = True
        print(f"[drill] flip ts={engaged['ts']} halt ts={halt_row['ts']}",
              flush=True)
        print(f"[drill] MEASURED flip → forwarder-halt: "
              f"{measured_ms:.1f} ms", flush=True)

        # 5. Prove the halt is real and total: a FRESH page enqueued AFTER
        #    the halt is never attempted (it stays queued in the outbox),
        #    and the PD double sees no new sends. (A POST already in flight
        #    when the kill flipped may still complete — at-least-once means
        #    a sent page cannot be un-sent; what the kill guarantees is NO
        #    NEW sends.)
        fresh_obid = _enqueue_page(log, 700)
        hits_after_halt = fake_pd.hits
        time.sleep(2.0)
        checks["no_sends_after_halt"] = (fake_pd.hits == hits_after_halt)
        fresh_row = log.outbox_row(fresh_obid)
        checks["fresh_row_stays_queued"] = (fresh_row["status"] == "queued")
        print(f"[drill] PD sends after halt: "
              f"{fake_pd.hits - hits_after_halt} (must be 0); fresh row "
              f"status={fresh_row['status']} (must be queued)", flush=True)

        # 6. Re-arm without confirmation → 422 (no silent auto-rearm).
        status, payload = _http("POST", base + "/api/v1/safety/rearm",
                               token=token, body={})
        checks["rearm_rejected_without_confirm"] = (
            status == 422 and payload.get("error")
            == "rearm_requires_confirmation")
        print(f"[drill] re-arm without confirm → {status} {payload}",
              flush=True)
        status, payload = _http("POST", base + "/api/v1/safety/rearm",
                               token=token, body={"confirm": False})
        checks["rearm_rejected_false_confirm"] = (
            status == 422 and payload.get("error")
            == "rearm_requires_confirmation")

        # 7. Deliberate re-arm recovers the pipeline (sticky re-entry works).
        status, payload = _http("POST", base + "/api/v1/safety/rearm",
                               token=token, body={"confirm": True})
        checks["rearm_200"] = (status == 200
                               and payload.get("engaged") is False)
        fwd.stop()
        fwd.start()
        _enqueue_page(log, 900)
        _wait_for(lambda: [e for e in
                           log.events_by_type("forward_confirmed", 20)
                           if f"drill-fp-0900" in (e["fingerprint"] or "")],
                  30, "post-rearm forward_confirmed")
        checks["recovered_after_rearm"] = True
        print("[drill] pipeline recovered after deliberate re-arm",
              flush=True)

        # 8. Status endpoint carries the drill artifact (Track 8 contract).
        status, payload = _http("GET", base + "/api/v1/safety/status",
                               token=token)
        checks["status_200"] = (status == 200)

        passed = (measured_ms < args.threshold_ms
                  and all(checks.values()))
    finally:
        try:
            fwd.stop()
        except Exception:
            pass
        http.shutdown()
        http.server_close()
        fake_pd.close()
        _safety.reset_operator_verifier()
        try:
            log.close()
        except Exception:
            pass

    artifact = {
        "artifact": "kill-drill",
        "contract": "C3",
        "lane": "lane/build-t3-shadow-kill",
        "started_at": started_at,
        "flip_at": engaged["ts"],
        "flip_seq": engaged["seq"],
        "forwarder_halted_at": halt_row["ts"],
        "halt_seq": halt_row["seq"],
        "measured_ms": round(measured_ms, 1),
        "threshold_ms": args.threshold_ms,
        "passed": bool(passed),
        "actor_id": "kill-drill",
        "checks": checks,
        "environment": {"sentinel_rev": _git_rev(),
                        "python": sys.version.split()[0],
                        "host": os.uname().nodename},
        "notes": ("flip → forwarder-halt measured from decision-log event "
                  "timestamps (kill_switch_engaged.ts → forwarder_halted.ts) "
                  "on the real WSGI + forwarder path. No Jev consulted."),
    }
    stamp = started_at.replace(":", "").replace("-", "")
    name = f"kill-drill-{stamp}.json"
    with open(os.path.join(artifact_dir, name), "w") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)
    latest = os.path.join(artifact_dir, "kill-drill-latest.json")
    tmp_latest = latest + ".tmp"
    with open(tmp_latest, "w") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)
    os.replace(tmp_latest, latest)
    print(f"[drill] artifact: {os.path.join(artifact_dir, name)}",
          flush=True)
    print(f"[drill] {'PASS' if passed else 'FAIL'} "
          f"(measured {measured_ms:.1f} ms vs threshold "
          f"{args.threshold_ms:.0f} ms)", flush=True)
    return artifact


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--threshold-ms", type=float,
                    default=THRESHOLD_DEFAULT_MS,
                    help="PASS iff measured_ms < threshold "
                         "(default: 5000 — the C3 claim bar)")
    ap.add_argument("--artifact-dir", default=os.path.join(_REPO, "ops",
                                                           "drills"),
                    help="where the drill artifact JSON is written")
    args = ap.parse_args(argv)
    artifact = run_drill(args)
    return 0 if artifact["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
