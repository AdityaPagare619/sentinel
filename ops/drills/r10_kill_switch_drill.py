#!/usr/bin/env python3
"""R-10 kill-switch drill — F1 kill condition (stdlib only).

Flips global_kill_switch ON in staging-lab via the sanctioned procedure
(flagctl -> POST /-/reload -> /healthz confirmation), asserts a synthetic
alert round-trips as passthrough/reason=kill_switch, flips OFF with the
two-person ceremony, asserts behavior is restored, and runs the
reload-rejection unit (invalid flags.json -> 422, live untouched).

Usage:
  ops/drills/r10_kill_switch_drill.py --by <name> --two-person "a,b"
      [--lab-dir ./staging-lab] [--record ops/drills/2026-10-05-r10-kill-switch-drill.md]

Exit 0 = ALL criteria PASS (the drill passed; F1 kill condition met).
Exit 1 = any criterion FAILED (failed drill -> the retract ADR lands).

The two-person names are the human ack for the OFF ceremony — the
ceremony itself is enforced by flagctl, not scripted away.
"""

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
CRITERIA = []


def check(name):
    def deco(fn):
        CRITERIA.append((name, fn))
        return fn
    return deco


class Drill:
    def __init__(self, args):
        self.args = args
        self.lab = os.path.abspath(args.lab_dir)
        self.cfg = os.path.join(self.lab, "config")
        self.state = os.path.join(self.lab, "state")
        self.db = os.path.join(self.state, "sentinel.db")
        self.port = args.port
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc = None
        self.results = []  # (name, ok, detail)
        self.gen_before = None

    # ------------------------------------------------------------- helpers

    def run(self, *cmd, env=None, check_rc=True):
        e = dict(os.environ)
        e.update(env or {})
        p = subprocess.run(cmd, capture_output=True, text=True, env=e,
                           cwd=REPO)
        if check_rc and p.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed: {p.stderr[-500:]}")
        return p

    def flagctl(self, *argv, **kw):
        return self.run(sys.executable,
                        os.path.join(REPO, "scripts/ops/flagctl.py"),
                        "--config-dir", self.cfg, *argv, **kw)

    def http(self, method, path, body=None):
        req = urllib.request.Request(
            self.base + path, data=body,
            method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.getcode(), resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def latest_decision(self, alert_id):
        # The drill lab is dedicated: the newest decision_made row for
        # this alert is ours.
        deadline = time.time() + 10
        while time.time() < deadline:
            con = sqlite3.connect(self.db)
            try:
                row = con.execute(
                    "SELECT body FROM events WHERE type='decision_made' "
                    "AND alert_id=? ORDER BY seq DESC LIMIT 1",
                    (alert_id,)).fetchone()
            finally:
                con.close()
            if row:
                body = json.loads(row[0])
                return (body.get("disposition"),
                        (body.get("v01_compat") or {}).get("reason"))
            time.sleep(0.25)
        raise RuntimeError(f"no decision_made row for {alert_id} in audit DB")

    def post_alert(self, alert_id):
        body = json.dumps({
            "event_action": "trigger",
            "dedup_key": alert_id,
            # Lab dummy: never a real routing key (the forwarder points at
            # the in-process blackhole; nothing can page). Required by
            # _normalize_pd or the alert is unparseable and never triaged.
            "routing_key": "drill-lab-dummy-routing-key",
            "payload": {
                "summary": "R-10 drill synthetic alert",
                "severity": "critical",
                "source": "drill",
                "service": "drill-svc",
                # Unique per alert: _normalize_pd takes the alert's
                # `check` from payload["class"], and the fingerprint
                # covers service/check/severity/region/env/cluster. The
                # correlator dedups by fingerprint — a repeated
                # fingerprint would inherit the prior disposition (dedup)
                # instead of exercising the gate fresh.
                "class": f"drill-check-{alert_id}",
                "custom_details": {"drill_alert": alert_id},
            },
        }).encode()
        code, _ = self.http("POST", "/v2/enqueue", body)
        assert code == 200, f"/v2/enqueue -> {code}"
        return self.latest_decision(alert_id)

    def healthz(self):
        code, raw = self.http("GET", "/healthz")
        assert code == 200, f"/healthz -> {code}"
        return json.loads(raw)

    # ---------------------------------------------------------------- steps

    def build_lab(self):
        # Fresh lab every run. The tier is freely destroyable, and stale
        # state contaminates the drill: restarts.jsonl accumulates across
        # runs and trips the no_crashloop_signature predicate (/healthz
        # 503), and a leftover flags.json would start the drill mid-flip.
        for path in (self.state, os.path.join(self.cfg, "flags.json")):
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
        self.run("bash", os.path.join(REPO, "scripts/ops/env-bootstrap.sh"),
                 "--tier", "staging-lab",
                 "--config-dir", self.cfg, "--state-dir", self.state)

    def start_blackhole(self):
        """In-process PD blackhole: accepts every forward with 202.

        The forwarder must not error in the lab (3 consecutive forward
        errors trip /healthz to 503). Nothing pages: this server only
        counts requests.
        """
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                outer.blackhole_hits.append(self.path)
                data = b'{"status":"success"}'
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.blackhole_hits = []
        self.blackhole = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.blackhole.daemon_threads = True
        port = self.blackhole.server_address[1]
        threading.Thread(target=self.blackhole.serve_forever,
                         daemon=True).start()
        return f"http://127.0.0.1:{port}/v2/enqueue"

    def stop_blackhole(self):
        bh = getattr(self, "blackhole", None)
        if bh is not None:
            bh.shutdown()
            bh.server_close()

    def start_receiver(self):
        env = {
            "SENTINEL_MOCK": "1",
            "SENTINEL_CONFIG_DIR": self.cfg,
            "SENTINEL_STATE_DIR": self.state,
            "SENTINEL_DB": self.db,
            # Lab-only dummy: the drill never touches /webhook/generic.
            # (Mirrors scripts/ops/receiver-smoke.sh.)
            "SENTINEL_WEBHOOK_SECRET": "drill-lab-dummy-secret-not-a-real-key",
            # staging-lab never-pages rule: NO PD_ROUTING_KEY here, ever.
            # The forwarder points at the in-process blackhole (202s
            # everything, counts requests, pages nothing).
            "PD_EVENTS_URL": self.pd_url,
            "PYTHONPATH": os.path.join(REPO, "src"),
            # Health token UNSET: /healthz and /-/reload are open in the
            # lab (documented asymmetry); the silence-direction routes
            # stay fail-closed regardless.
        }
        for k in ("SENTINEL_HEALTH_TOKEN", "PD_ROUTING_KEY",
                  "TYPESAFE_API_KEY"):
            os.environ.pop(k, None)
        log = os.path.join(self.lab, "receiver.log")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "sentinel.receiver",
             "--port", str(self.port), "--bind", "127.0.0.1"],
            stdout=open(log, "w"), stderr=subprocess.STDOUT,
            env={**os.environ, **env}, cwd=REPO)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                code, _ = self.http("GET", "/livez")
                if code == 200:
                    return
            except Exception:
                pass
            if self.proc.poll() is not None:
                raise RuntimeError("receiver exited during boot; see " + log)
            time.sleep(0.25)
        raise RuntimeError("receiver did not serve /livez in 30s")

    def stop_receiver(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=15)

    # ------------------------------------------------------------- criteria

    @check("C1 baseline alert round-trips (pre-flip)")
    def c1(self):
        disp, reason = self.post_alert("r10-drill-baseline")
        self.gen_before = self.healthz()["config_generation"]
        return True, f"disposition={disp} reason={reason} gen={self.gen_before}"

    @check("C2 kill-switch flip ON via flagctl (one human)")
    def c2(self):
        p = self.flagctl("set", "global_kill_switch", "true",
                         "--by", self.args.by,
                         "--reason", "R-10 T+9 kill-switch drill")
        return True, p.stdout.strip().splitlines()[-1][:120]

    @check("C3 reload applies: generation bumps, /healthz shows the flag")
    def c3(self):
        code, raw = self.http("POST", "/-/reload", b"")
        assert code == 200, f"/-/reload -> {code}: {raw[:200]}"
        body = json.loads(raw)
        gen = body["config_generation"]
        assert gen == self.gen_before + 1, \
            f"generation {self.gen_before} -> {gen} (expected +1)"
        hz = self.healthz()
        assert hz["flags"]["global_kill_switch"] is True, \
            f"/healthz flags: {hz['flags']}"
        self.gen_before = gen
        return True, f"generation={gen} flags.global_kill_switch=true"

    @check("C4 alert round-trips as passthrough/reason=kill_switch")
    def c4(self):
        disp, reason = self.post_alert("r10-drill-kill-on")
        ok = disp == "passthrough" and reason == "kill_switch"
        return ok, f"disposition={disp} reason={reason}"

    @check("C5 kill-switch flip OFF needs the two-person ceremony")
    def c5(self):
        # ON is already true from C2. Attempt OFF with one human only:
        # flagctl must REFUSE (the safety asymmetry).
        p = self.flagctl("set", "global_kill_switch", "false",
                         "--by", self.args.by,
                         "--reason", "drill-probe-single-human",
                         check_rc=False)
        assert p.returncode != 0, \
            "flagctl allowed kill-switch OFF without two-person!"
        # Now the real ceremony.
        p2 = self.flagctl("set", "global_kill_switch", "false",
                          "--by", self.args.by,
                          "--two-person", self.args.two_person,
                          "--reason", "R-10 drill complete")
        assert p2.returncode == 0, f"flagctl OFF failed: {p2.stderr[-300:]}"
        code, _ = self.http("POST", "/-/reload", b"")
        assert code == 200
        hz = self.healthz()
        assert hz["flags"]["global_kill_switch"] is False
        self.gen_before = hz["config_generation"]
        return True, f"single-human OFF refused; two-person=({self.args.two_person}) accepted"

    @check("C6 behavior restored after OFF (no kill_switch reason)")
    def c6(self):
        disp, reason = self.post_alert("r10-drill-kill-off")
        ok = reason != "kill_switch"
        return ok, f"disposition={disp} reason={reason}"

    @check("C7 reload-rejection: invalid flags.json -> 422, live untouched")
    def c7(self):
        path = os.path.join(self.cfg, "flags.json")
        with open(path) as fh:
            good = fh.read()
        try:
            with open(path, "w") as fh:
                fh.write('{"version": 1, "flags": {"bogus": {"value": 1}}}')
            code, raw = self.http("POST", "/-/reload", b"")
            assert code == 422, f"/-/reload -> {code}: {raw[:200]}"
            hz = self.healthz()
            assert hz["config_generation"] == self.gen_before, \
                "live generation moved on a rejected reload"
            detail = f"422 as expected; live gen still {self.gen_before}"
        finally:
            with open(path, "w") as fh:
                fh.write(good)
        code, _ = self.http("POST", "/-/reload", b"")
        assert code == 200, "valid flags did not reload after restore"
        return True, detail

    # ------------------------------------------------------------------ run

    def execute(self):
        self.build_lab()
        self.pd_url = self.start_blackhole()
        try:
            self.start_receiver()
            for name, fn in CRITERIA:
                try:
                    ok, detail = fn(self)
                except AssertionError as exc:
                    ok, detail = False, f"ASSERT: {exc}"
                except Exception as exc:
                    ok, detail = False, f"ERROR: {exc}"
                self.results.append((name, ok, detail))
                print(("PASS " if ok else "FAIL ") + name)
                print("     " + detail)
        finally:
            self.stop_receiver()
            self.stop_blackhole()
        passed = all(ok for _, ok, _ in self.results)
        self.write_record(passed)
        return passed

    def write_record(self, passed):
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        lines = [
            "# R-10 kill-switch drill record",
            "",
            f"**Date:** {now} · **Ran by:** {self.args.by} "
            f"(two-person OFF: {self.args.two_person})",
            f"**Lab:** {self.lab} (built via `scripts/ops/env-bootstrap.sh "
            "--tier staging-lab`); receiver in SENTINEL_MOCK=1, no "
            "PD_ROUTING_KEY (never-pages rule), forwarder at the "
            "in-process 202 blackhole (pages nothing).",
            f"**Verdict:** {'PASS' if passed else 'FAIL'}",
            "",
            "## Criteria",
            "",
        ]
        for name, ok, detail in self.results:
            lines.append(f"- [{'x' if ok else ' '}] **{name}** — {detail}")
        lines += [
            "",
            "## F1 kill-condition linkage",
            "",
            ("PASS: the wire stands; `lane/12h-dev-1-r10` proceeds to PR "
             "(Relay + Vault review)."
             if passed else
             "FAIL: per the F1 verdict the retract ADR lands NOW — "
             "`docs/decisions/2026-10-05-r10-retract-DRAFT.md` is filled in "
             "and landed, the branch is not merged, and a follow-up lane is "
             "registered the same hour. No third option."),
            "",
        ]
        with open(self.args.record, "w") as fh:
            fh.write("\n".join(lines))
        print(f"record: {self.args.record}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--by", required=True)
    ap.add_argument("--two-person", required=True)
    ap.add_argument("--lab-dir", default="./staging-lab")
    ap.add_argument("--port", type=int, default=18081)
    ap.add_argument("--record",
                    default="ops/drills/2026-10-05-r10-kill-switch-drill.md")
    args = ap.parse_args()
    drill = Drill(args)
    ok = drill.execute()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
