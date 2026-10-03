#!/usr/bin/env python3
"""ORACLE latency campaign — real-key Jev measurement (N>=100).

Method (see research/jev-behavior/2026-10-03-latency-campaign.md):
  * Phase 0: TCP/TLS decomposition probe (no HTTP semantics, handshake only).
  * Phase 1: COLD  x10 — first call after >=60s idle, fresh connection each.
  * Phase 2: WARM-FRESH x60 — ~3s spacing, fresh connection each (urllib).
  * Phase 3: WARM-REUSED x40 — ~3s spacing, one keep-alive HTTPSConnection.

Each call records: ts, phase, conn_mode, size_class, payload_bytes,
round-trip ms, HTTP status, disposition, confidence, token usage.
Errors (429/529/5xx/timeout) are recorded as rows, never crash the run.

Credential: custom.typesafe via the authd surrogate helpers. The raw key
never touches this process; only hsurr:* surrogates are sent, and only to
api.typesafe.ai. Nothing credential-like is printed or logged.

Usage:
    python3 latency_campaign.py --out latency-n100.json [--cold 10 --fresh 60 --reused 40]
    python3 latency_campaign.py --dry-run   # 1 call, prints response shape
"""
from __future__ import annotations

import argparse
import http.client
import json
import random
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import (
    add_surrogate_to_request,
    dynamic_credential_entry,
    read_json_response,
)

HOST = "api.typesafe.ai"
URL = f"https://{HOST}/v1/systemone"
MODEL = "jev-1.13.0"
ALLOWED = [HOST]
PER_CALL_TIMEOUT = 90.0
COLD_IDLE_S = 62.0
WARM_GAP_S = 3.0

# --- alert vocabulary (mirrors src/sentinel/synthetic.py on lane/code-mvp-v0.1) ---
_SERVICES = ["checkout-api", "payments-svc", "auth-gateway", "search-api",
             "redis-cache", "postgres-primary", "kafka-ingest", "cdn-edge",
             "k8s-platform", "ci-runners"]
_CHECKS = ["error_rate", "p99_latency", "cpu_util", "conn_pool",
           "queue_depth", "http_5xx", "disk_io", "tls_cert_expiry"]
_REGIONS = ["us-east-1", "eu-west-1", "ap-south-1"]
_TEAMS = {"checkout-api": "product_backend", "payments-svc": "product_backend",
          "auth-gateway": "security", "search-api": "product_backend",
          "redis-cache": "data", "postgres-primary": "data",
          "kafka-ingest": "data", "cdn-edge": "network",
          "k8s-platform": "platform", "ci-runners": "platform"}

_QUESTION = {
    "disposition": {
        "type": "choice",
        "instructions": "Should this alert page a human now, or be suppressed as routine noise?",
        "criteria": {
            "page": "genuine incident needing human attention",
            "suppress": "routine noise, self-clearing or informational",
            "cannot_determine": "not enough signal to decide",
        },
    }
}

_RUNBOOK_SNIPPET = (
    "Runbook RB-1147 (excerpt): 1) Check the service dashboard for correlated "
    "deployments in the last 30m. 2) If error_rate > 5% for >10m, escalate to "
    "on-call; the last three pages from this check were true SEVs. 3) Known "
    "flap pattern: this check auto-clears within 8m during GC pauses on "
    "redis-cache; do NOT page if conn_pool recovers. 4) Verify region "
    "failover state before declaring a region-wide incident. 5) Attach the "
    "last 24h metric window to the incident ticket.\n"
)


def build_state(size: str, rng: random.Random) -> dict:
    svc = rng.choice(_SERVICES)
    chk = rng.choice(_CHECKS)
    region = rng.choice(_REGIONS)
    state = {
        "alert": f"{chk.replace('_', ' ').upper()} breaching on {svc} "
                 f"({region}): value {rng.uniform(2, 9):.1f}x baseline for "
                 f"{rng.randint(2, 14)}m",
        "service": svc,
        "runbook_hits": rng.choice([0, 0, 1, 2, 5]),
    }
    if size in ("medium", "large"):
        state.update({
            "check": chk,
            "region": region,
            "team": _TEAMS[svc],
            "env": "prod",
            "severity_in": rng.choice(["p2_high", "p3_medium", "p4_low", "known_noise"]),
            "fingerprint": f"{abs(hash((svc, chk, region))) % 16**12:012x}",
            "occurrence_count_24h": rng.randint(1, 40),
            "context": ("Fired twice in the last 24h; both auto-cleared. "
                        "No deploy in the last 2h. On-call acked the previous "
                        "occurrence as noise."),
        })
    if size == "large":
        state.update({
            "runbook_excerpt": _RUNBOOK_SNIPPET * 6,
            "annotations": [
                {"k": "dashboard", "v": f"https://grafana.internal/d/{svc}/{chk}"},
                {"k": "slo_burn", "v": f"{rng.uniform(0.5, 6):.2f}x"},
                {"k": "last_sev", "v": f"2026-08-{rng.randint(1,28):02d}"},
            ],
            "related_alerts": [
                f"{abs(hash((svc, chk, i))) % 16**12:012x}" for i in range(8)
            ],
            "metric_window_24h": [round(rng.uniform(0.4, 3.2), 3) for _ in range(48)],
        })
    return state


def parse_answer(data: dict) -> tuple[str | None, float | None, dict | None]:
    """Return (disposition, confidence, usage). Defensive: shape may vary."""
    answers = data.get("answers") or data.get("results") or {}
    disp = answers.get("disposition")
    value, conf = None, None
    if isinstance(disp, dict):
        for k in ("choice", "value", "label", "answer", "decision"):
            if k in disp:
                value = disp[k]
                break
        for k in ("confidence", "probability", "score", "p"):
            v = disp.get(k)
            if isinstance(v, (int, float)):
                conf = float(v)
                break
        if value is None and conf is None:
            # maybe {page: 0.7, suppress: 0.2, ...} style
            probs = {k: v for k, v in disp.items() if isinstance(v, (int, float))}
            if probs:
                value = max(probs, key=probs.get)
                conf = float(probs[value])
    elif isinstance(disp, str):
        value = disp
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
    return value, conf, usage


def tcp_tls_probe(samples: int = 3) -> list[dict]:
    """Decompose connection setup cost: DNS, TCP connect, TLS handshake."""
    out = []
    for i in range(samples):
        row = {"sample": i}
        try:
            t0 = time.monotonic()
            infos = socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)
            row["dns_ms"] = round((time.monotonic() - t0) * 1000, 1)
            row["resolved_ips"] = sorted({info[4][0] for info in infos})
            t0 = time.monotonic()
            sock = socket.create_connection((HOST, 443), timeout=15)
            row["tcp_ms"] = round((time.monotonic() - t0) * 1000, 1)
            t0 = time.monotonic()
            ctx = ssl.create_default_context()
            tls = ctx.wrap_socket(sock, server_hostname=HOST)
            row["tls_ms"] = round((time.monotonic() - t0) * 1000, 1)
            row["tls_version"] = tls.version()
            tls.close()
        except Exception as e:  # noqa: BLE001 — probe must not kill the run
            row["error"] = f"{type(e).__name__}"
        out.append(row)
        time.sleep(1)
    return out


def one_call_urllib(body: bytes) -> tuple[int | None, float, dict | None, dict, str | None]:
    """Fresh-connection call. Returns (status, ms, data, headers_of_interest, err)."""
    req = urllib.request.Request(
        URL, data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "sentinel-oracle-latency/1.0"},
        method="POST",
    )
    add_surrogate_to_request(req, "custom.typesafe", allowed_hosts=ALLOWED)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=PER_CALL_TIMEOUT) as resp:
            ms = (time.monotonic() - t0) * 1000
            data = read_json_response(resp)
            hdrs = interesting_headers(resp.headers)
            return resp.status, ms, data, hdrs, None
    except urllib.error.HTTPError as e:
        ms = (time.monotonic() - t0) * 1000
        return e.code, ms, None, {}, f"HTTPError:{e.code}"
    except Exception as e:  # noqa: BLE001
        ms = (time.monotonic() - t0) * 1000
        return None, ms, None, {}, f"{type(e).__name__}"


def interesting_headers(hdrs) -> dict:
    """Curated, non-sensitive header subset for region inference."""
    out = {"_keys": sorted(hdrs.keys())}
    for k in ("server", "cf-ray", "x-powered-by", "via", "x-request-id",
              "x-cloud-trace-context", "content-type", "date"):
        v = hdrs.get(k)
        if v:
            out[k] = v[:120]
    return out


class KeepAlive:
    """Single persistent HTTPSConnection; reconnects transparently."""

    def __init__(self):
        entry = dynamic_credential_entry("custom.typesafe")
        self.auth = f"Bearer {str(entry['surrogate']).strip()}"
        self.conn: http.client.HTTPSConnection | None = None
        self.reused = 0

    def call(self, body: bytes):
        if self.conn is None:
            self.conn = http.client.HTTPSConnection(HOST, timeout=PER_CALL_TIMEOUT)
            fresh = True
        else:
            fresh = False
        t0 = time.monotonic()
        try:
            self.conn.request("POST", "/v1/systemone", body=body, headers={
                "Content-Type": "application/json",
                "Authorization": self.auth,
                "User-Agent": "sentinel-oracle-latency/1.0",
                "Connection": "keep-alive",
            })
            resp = self.conn.getresponse()
            raw = resp.read()
            ms = (time.monotonic() - t0) * 1000
            status = resp.status
            data = json.loads(raw.decode("utf-8")) if raw else {}
            if not fresh:
                self.reused += 1
            return status, ms, data, {}, None, (not fresh)
        except Exception as e:  # noqa: BLE001 — reconnect once, then record
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
            return None, (time.monotonic() - t0) * 1000, None, {}, \
                f"KeepAlive:{type(e).__name__}", False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="latency-n100.json")
    ap.add_argument("--cold", type=int, default=10)
    ap.add_argument("--fresh", type=int, default=60)
    ap.add_argument("--reused", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20261003)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    rows: list[dict] = []
    # Incremental JSONL sink — every row is flushed to disk the moment it is
    # recorded, so partial progress survives a killed run. Derived from --out.
    jsonl_path = args.out + ".jsonl" if not args.out.endswith(".jsonl") else args.out
    jsonl_f = open(jsonl_path, "w")
    meta = {
        "campaign": "oracle-latency-2026-10-03",
        "model": MODEL,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "per_call_timeout_s": PER_CALL_TIMEOUT,
        "cold_idle_s": COLD_IDLE_S,
        "warm_gap_s": WARM_GAP_S,
        "probe": tcp_tls_probe(),
    }

    def record(phase, conn_mode, size, payload_bytes, status, ms, data, err):
        disp, conf, usage = (None, None, None)
        model_echo = None
        if data is not None:
            try:
                disp, conf, usage = parse_answer(data)
            except Exception:  # noqa: BLE001
                pass
            try:
                m = data.get("model")
                if isinstance(m, str):
                    model_echo = m[:64]
            except Exception:  # noqa: BLE001
                pass
        row = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "seq": len(rows),
            "phase": phase,
            "conn_mode": conn_mode,
            "size_class": size,
            "payload_bytes": payload_bytes,
            "status": status,
            "latency_ms": round(ms, 1),
            "disposition": disp,
            "confidence": conf,
            "model_echo": model_echo,
            "usage": usage,
            "error": err,
        }
        rows.append(row)
        jsonl_f.write(json.dumps(row) + "\n")
        jsonl_f.flush()

    def gap_sleep(last_end: float, want: float):
        dt = time.monotonic() - last_end
        if dt < want:
            time.sleep(want - dt)

    if args.dry_run:
        state = build_state("small", rng)
        body = json.dumps({"model": MODEL, "state": state,
                           "questions": _QUESTION}).encode()
        status, ms, data, hdrs, err = one_call_urllib(body)
        print(f"status={status} ms={ms:.0f} err={err}")
        print("header_keys:", hdrs.get("_keys"))
        for k, v in hdrs.items():
            if not k.startswith("_"):
                print(f"  {k}: {v}")
        if isinstance(data, dict):
            print("top_keys:", sorted(data.keys()))
            print("answers_preview:", json.dumps(data.get("answers"), default=str)[:500])
            print("usage:", data.get("usage"))
        return 0

    # Phase 1: COLD — >=60s idle before each, fresh connection.
    cold_sizes = (["small"] * 4 + ["medium"] * 3 + ["large"] * 3)
    rng.shuffle(cold_sizes)
    last_end = time.monotonic()  # first cold call also observes the idle window
    ka = KeepAlive()
    for i in range(args.cold):
        gap_sleep(last_end, COLD_IDLE_S)
        size = cold_sizes[i % len(cold_sizes)]
        state = build_state(size, rng)
        body = json.dumps({"model": MODEL, "state": state,
                           "questions": _QUESTION}).encode()
        status, ms, data, hdrs, err = one_call_urllib(body)
        if i == 0:
            meta["sample_headers"] = hdrs
        record("cold", "fresh", size, len(body), status, ms, data, err)
        last_end = time.monotonic()
        print(f"[cold {i+1}/{args.cold}] {size} status={status} "
              f"{ms:.0f}ms disp={rows[-1]['disposition']} err={err}", flush=True)
        if status in (429, 529) or (status and status >= 500):
            time.sleep(30)

    # Phase 2: WARM-FRESH — fresh connection each, ~3s spacing.
    for i in range(args.fresh):
        gap_sleep(last_end, WARM_GAP_S)
        size = ["small", "medium", "large"][i % 3]
        state = build_state(size, rng)
        body = json.dumps({"model": MODEL, "state": state,
                           "questions": _QUESTION}).encode()
        status, ms, data, _, err = one_call_urllib(body)
        record("warm", "fresh", size, len(body), status, ms, data, err)
        last_end = time.monotonic()
        if (i + 1) % 10 == 0:
            print(f"[warm-fresh {i+1}/{args.fresh}] {size} status={status} "
                  f"{ms:.0f}ms err={err}", flush=True)
        if status in (429, 529) or (status and status >= 500):
            time.sleep(30)

    # Phase 3: WARM-REUSED — one keep-alive connection, ~3s spacing.
    for i in range(args.reused):
        gap_sleep(last_end, WARM_GAP_S)
        size = ["small", "medium", "large"][i % 3]
        state = build_state(size, rng)
        body = json.dumps({"model": MODEL, "state": state,
                           "questions": _QUESTION}).encode()
        status, ms, data, _, err, was_reused = ka.call(body)
        record("warm", "reused" if was_reused else "reused-reconnect",
               size, len(body), status, ms, data, err)
        last_end = time.monotonic()
        if (i + 1) % 10 == 0:
            print(f"[warm-reused {i+1}/{args.reused}] {size} status={status} "
                  f"{ms:.0f}ms reused={was_reused} err={err}", flush=True)
        if status in (429, 529) or (status and status >= 500):
            time.sleep(30)

    meta["finished_utc"] = datetime.now(timezone.utc).isoformat()
    meta["n"] = len(rows)
    meta["keepalive_reused"] = ka.reused
    jsonl_f.close()
    with open(args.out, "w") as f:
        json.dump({"meta": meta, "rows": rows}, f, indent=1)
    ok = sum(1 for r in rows if r["status"] == 200)
    print(f"DONE n={len(rows)} ok200={ok} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
