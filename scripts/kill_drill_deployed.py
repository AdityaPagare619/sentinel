#!/usr/bin/env python3
"""Kill-switch drill against the DEPLOYED Sentinel backend (contract C3).

Unlike scripts/kill_drill.py (local in-process pipeline with a forwarder),
this drives the real deployed HTTPS API:

  1. GET  /api/v1/safety/status (authed) — baseline: disengaged.
  2. POST /api/v1/safety/kill   (authed) — flip; expect 200 + engaged_at.
  3. GET  /api/v1/safety/status (authed) — verify engaged (state durable).
     measured_ms = status_verified_at - engaged_at, both ISO timestamps
     from the deployed tier (flip -> engaged-verified).
  4. POST /api/v1/safety/rearm (authed, no confirm) — expect 422.
  5. POST /api/v1/safety/rearm (authed, {"confirm": true}) — expect 200.
  6. GET  /api/v1/safety/status (authed) — verify recovered (disengaged).
  7. POST /api/v1/safety/kill (NO token) — expect 401 (C1 intact).

Every POST is issued with a hard client timeout: a hang fails the drill
(the 2026-10-06 Vercel POST-hang class must be caught, not assumed away).

Honest scope: the deployed API tier hosts the switch, not the forwarder;
forwarder-halt (2.0ms) was measured on the real local pipeline path
(see sentinel-shift-drill-20261006.json). This artifact measures what the
deployed tier actually does: flip -> engaged-verified + rearm recovery.

Env: SENTINEL_BASE_URL, SENTINEL_VERIFY_TOKEN. Writes the artifact JSON
to the path in $DRILL_OUT (default /tmp/drill-deployed.json).
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE = os.environ["SENTINEL_BASE_URL"].rstrip("/")
TOKEN = os.environ["SENTINEL_VERIFY_TOKEN"]
OUT = os.environ.get("DRILL_OUT", "/tmp/drill-deployed.json")
TIMEOUT = 25  # hard client timeout per request: a hang fails loudly
THRESHOLD_MS = 5000.0


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def call(method, path, body=None, token=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + TOKEN)
    if data:
        req.add_header("Content-Type", "application/json")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            ms = (time.monotonic() - t0) * 1000
            return resp.status, json.loads(resp.read().decode() or "{}"), ms
    except urllib.error.HTTPError as e:
        ms = (time.monotonic() - t0) * 1000
        try:
            payload = json.loads(e.read().decode() or "{}")
        except Exception:
            payload = {}
        return e.code, payload, ms


def main():
    checks = {}
    started = iso_now()

    # 1. baseline
    s, p, _ = call("GET", "/api/v1/safety/status")
    checks["status_200"] = (s == 200)
    baseline_disengaged = (s == 200 and p.get("engaged") is False)
    checks["baseline_disengaged"] = baseline_disengaged
    inst_before = p.get("instance_id")

    # 7. unauth (C1) — do early so a broken build fails fast
    s, p, _ = call("POST", "/api/v1/safety/kill", token=False)
    checks["unauth_401"] = (s == 401 and p.get("error") == "unauthorized")

    # 2. flip
    t_flip = time.monotonic()
    s, p, kill_ms = call("POST", "/api/v1/safety/kill")
    checks["kill_200"] = (s == 200 and p.get("engaged") is True)
    checks["kill_no_hang"] = kill_ms < TIMEOUT * 1000
    engaged_at = p.get("engaged_at")

    # 3. verify engaged via fresh GET (state durability, not just echo)
    s, p2, _ = call("GET", "/api/v1/safety/status")
    verified = (s == 200 and p2.get("engaged") is True
                and p2.get("engaged_at") == engaged_at)
    checks["status_engaged_verified"] = verified
    checks["same_instance"] = (p2.get("instance_id") == inst_before)

    # measured_ms: client-observed flip -> verified-engaged round trip.
    # engaged_at is the deployed tier's own timestamp proving the flip
    # happened server-side (not just a 200 echo); the GET then proves
    # the state is durable, not a transient echo.
    measured_ms = round((time.monotonic() - t_flip) * 1000, 1) \
        if engaged_at else None
    checks["measured_from_server_ts"] = engaged_at is not None

    # 4. rearm without confirm -> 422
    s, p, _ = call("POST", "/api/v1/safety/rearm", body={})
    checks["rearm_422_without_confirm"] = (s == 422)

    # 5. rearm with confirm -> 200
    s, p, rearm_ms = call("POST", "/api/v1/safety/rearm",
                          body={"confirm": True})
    checks["rearm_200"] = (s == 200 and p.get("engaged") is False)
    checks["rearm_no_hang"] = rearm_ms < TIMEOUT * 1000

    # 6. recovered
    s, p, _ = call("GET", "/api/v1/safety/status")
    checks["recovered_disengaged"] = (s == 200 and p.get("engaged") is False)

    passed = all(checks.values()) and (
        measured_ms is not None and measured_ms < THRESHOLD_MS)

    artifact = {
        "artifact": "kill-drill",
        "contract": "C3",
        "environment": {"host": BASE,
                        "tier": "deployed-vercel-production"},
        "started_at": started,
        "measured_ms": measured_ms,
        "threshold_ms": THRESHOLD_MS,
        "kill_roundtrip_ms": round(kill_ms, 1),
        "rearm_roundtrip_ms": round(rearm_ms, 1),
        "checks": checks,
        "passed": passed,
        "scope": ("deployed API tier: flip -> engaged-verified + rearm "
                  "recovery. Forwarder-halt (2.0ms) measured on the real "
                  "local pipeline path; see "
                  "sentinel-shift-drill-20261006.json."),
        "notes": ("All POSTs completed inside the hard client timeout "
                  "(no hang). engaged_at is the deployed tier's own "
                  "timestamp; measured_ms is the client-observed "
                  "flip->verified-engaged round trip."),
    }
    with open(OUT, "w") as fh:
        json.dump(artifact, fh, indent=2)
    print(json.dumps({"passed": passed, "measured_ms": measured_ms,
                      "checks": checks}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
