#!/usr/bin/env python3
"""Rehearsal shim — frozen read API (v1.0.0) served from REAL storm artifacts.

This is a REHEARSAL HARNESS, not the production platform tier. Every byte
it serves is a projection over the real engine's real output:
  * demo/storm-scenario/storm.db ......... the append-only event log
  * demo/storm-scenario/answers.jsonl .... the engine's own DecisionRecords
    (full Jev answers — preserved evidence, never fabricated)
  * synthetic alerts regenerated deterministically (seed 42) for context
    fields (title/service/check) — the same generator the storm used.

Honesty rules (same as the demo):
  * data_source in every envelope names this rig — the UI's source badge
    shows it; nothing pretends to be the production platform tier.
  * Fields the log does not carry are OMITTED, never invented.
  * POST /api/simulate REFUSES (422): this rig does not implement the
    tuner path, and per the copy audit the simulator must run the real
    kernel (UI lane's build). The refusal is the honest state.
  * GET /api/calibration returns the provisional state: no outcome labels
    are joined in a synthetic storm, so ECE cannot be computed — n=0
    labeled, stated on the card.

Usage:
    PYTHONPATH=src python3 demo/rehearsal-shim.py [--port 8080]
Then serve the UI against it:
    cd /tmp/ui && python3 -m http.server 8000   # platform/ui checkout
    # open http://localhost:8000/#/river (same origin as :8080? no — set
    # Data.apiBase or proxy. For curl verification the shim alone suffices.)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
SCEN = HERE / "storm-scenario"
CONTRACT_VERSION = "1.0.0"
DATA_SOURCE = ("rehearsal-shim: real Sentinel engine event log "
               "(synthetic storm, seed 42, live Jev answers)")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.answers: dict[str, dict] = {}
        self.alerts: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        con = sqlite3.connect(f"file:{SCEN/'storm.db'}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        self.events = [dict(r) for r in con.execute(
            "SELECT * FROM events ORDER BY seq")]
        con.close()
        for line in (SCEN / "answers.jsonl").read_text().splitlines():
            if line.strip():
                a = json.loads(line)
                # keyed by decision seq: one alert may be decided several
                # times (the flip beat) — each decision keeps its own answers
                self.answers[a["seq"]] = a
        # Deterministic alert context: the same generator + seed as the storm.
        import sys
        sys.path.insert(0, str(HERE.parent / "src"))
        from sentinel.synthetic import generate_alerts
        for alert, _label in generate_alerts(40, 42):
            self.alerts[alert.alert_id] = {
                "title": alert.title,
                "service": alert.service,
                "check": alert.check,
                "severity_in": alert.severity_in,
                "region": alert.labels.get("region"),
            }

    def body(self, e: dict) -> dict:
        return json.loads(e["body"])

    def decisions(self) -> list[dict]:
        return [e for e in self.events if e["type"] == "decision_made"]


def summary(store: Store, e: dict) -> dict:
    b = store.body(e)
    ans = store.answers.get(e["seq"], {})
    ctx = store.alerts.get(e["alert_id"], {})
    qsev = (ans.get("q_severity") or {})
    qteam = (ans.get("q_team") or {})
    qdisp = (ans.get("q_disposition") or {})
    disp = (ans.get("disposition") or {})
    return {
        "id": e["seq"],
        "alert_id": e["alert_id"],
        "time": e["ts"],
        "title": ctx.get("title"),
        "service": ctx.get("service"),
        "severity": qsev.get("choice"),          # the model's REAL answer
        "team": qteam.get("choice") or disp.get("team"),
        "disposition": b.get("disposition"),
        "confidence": b.get("q3_confidence"),
        "reason": disp.get("reason"),            # the gate's REAL reason code
        "budget_outcome": b.get("budget_outcome"),
        "prob_map": qsev.get("probabilities") or {},
        "fingerprint": e["fingerprint"],
        "input_sha256": b.get("input_sha256"),
        "jev_model": b.get("jev_model"),         # null on timer-win (honest)
        "latency_ms": b.get("latency_ms"),       # null on timer-win (honest)
        "shadow": False,
        "q3_disposition": qdisp.get("choice"),
    }


def envelope(data, extra_meta: dict | None = None) -> dict:
    meta = {
        "contract_version": CONTRACT_VERSION,
        "data_source": DATA_SOURCE,
        "generated_at": utcnow(),
    }
    if extra_meta:
        meta.update(extra_meta)
    return {"data": data, "meta": meta}


class Handler(BaseHTTPRequestHandler):
    store: Store = None  # set on serve

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, payload) -> None:
        if isinstance(payload, str):
            raw = payload.encode()
            ctype = "text/event-stream"
        else:
            raw = json.dumps(payload).encode()
            ctype = "application/json"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _err(self, code: int, errcode: str, message: str) -> None:
        self._send(code, {"error": {"code": errcode, "message": message,
                                   "retryable": code >= 500}})

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        st = self.store
        try:
            if u.path == "/api/decisions":
                rows = [summary(st, e) for e in reversed(st.decisions())]
                fp = q.get("fingerprint", [None])[0]
                team = q.get("team", [None])[0]
                action = q.get("action", [None])[0]
                since = q.get("since_id", [None])[0]
                if fp:
                    rows = [r for r in rows if r["fingerprint"] == fp]
                if team:
                    rows = [r for r in rows if r["team"] == team]
                if action:
                    rows = [r for r in rows if r["disposition"] == action]
                if since:
                    rows = [r for r in rows if r["id"] > int(since)]
                try:
                    limit = int(q.get("limit", ["50"])[0])
                except ValueError:
                    return self._err(400, "bad_limit", "limit must be an integer")
                page = rows[:limit]
                nxt = page[-1]["id"] if page else 0
                self._send(200, envelope(page, {"pagination": {
                    "next_since_id": nxt, "count": len(page)}}))
            elif u.path.startswith("/api/decision/"):
                try:
                    did = int(u.path.rsplit("/", 1)[1])
                except ValueError:
                    return self._err(400, "bad_id", "id must be an integer")
                hit = next((e for e in st.decisions() if e["seq"] == did), None)
                if hit is None:
                    return self._err(404, "not_found",
                                     f"no decision with id {did}")
                det = summary(st, hit)
                det["alert"] = st.alerts.get(hit["alert_id"], {})
                det["audit"] = {
                    "flips": [f for f in self._flips()
                              if f["input_sha256"] == det["input_sha256"]],
                    "note": "flip timelines re-derived from the event log",
                }
                self._send(200, envelope(det))
            elif u.path == "/api/analytics/flips":
                self._send(200, envelope({"flips": self._flips(),
                                          "window": q.get("window", ["7d"])[0]}))
            elif u.path == "/api/analytics/noise":
                disps: dict[str, int] = {}
                reasons: dict[str, int] = {}
                teams: dict[str, int] = {}
                for e in st.decisions():
                    s = summary(st, e)
                    disps[s["disposition"]] = disps.get(s["disposition"], 0) + 1
                    if s["reason"]:
                        reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
                    if s["team"]:
                        teams[s["team"]] = teams.get(s["team"], 0) + 1
                self._send(200, envelope({
                    "decisions": len(st.decisions()),
                    "by_disposition": disps,
                    "by_reason": reasons,
                    "by_team": teams,
                    "window": q.get("window", ["24h"])[0],
                }))
            elif u.path == "/api/calibration":
                # Honest provisional: a synthetic storm joins no outcome
                # labels, so ECE/coverage cannot be computed. n=0 ON the
                # card, per the denominator contract.
                self._send(200, envelope({
                    "status": "provisional",
                    "n_labeled": 0,
                    "note": ("no outcome labels joined in this synthetic "
                             "storm — calibration requires the Ledger "
                             "outcomes join over real incident history"),
                    "bins": [], "ece": None, "coverage": {},
                }))
            elif u.path == "/api/stream":
                self._sse(since_id=q.get("since_id", [None])[0])
            else:
                self._err(404, "not_found", f"unknown path {u.path}")
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001 — never 500 silently
            self._err(500, "shim_error", f"rehearsal shim error: {exc}")

    def _flips(self) -> list[dict]:
        import sys
        sys.path.insert(0, str(HERE.parent / "src"))
        from sentinel.eventlog import detect_flips
        return detect_flips(self.store.events)

    def _sse(self, since_id) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            since = int(since_id) if since_id else 0
            for e in self.store.decisions():
                if e["seq"] > since:
                    payload = json.dumps(summary(self.store, e))
                    self.wfile.write(
                        f"id: {e['seq']}\nevent: decision\ndata: {payload}\n\n"
                        .encode())
            self.wfile.write("retry: 3000\n\n: heartbeat — rehearsal stream replay complete\n\n".encode("utf-8"))
        except BrokenPipeError:
            pass

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/api/simulate":
            # The honest refusal (copy-audit Finding 3): this rehearsal rig
            # does not implement the tuner path, and the simulator must run
            # the real kernel (UI lane's build). A wrong curve is worse
            # than no curve.
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self._err(422, "simulator_not_wired",
                      "POST /api/simulate refused in this rehearsal rig: "
                      "projections must run the live gate kernel "
                      "(quantized integer-hundredths gate + dual attestation "
                      "+ B=2700ms race), not the tuner path. The kernel-true "
                      "implementation is the UI lane's build.")
        else:
            self._err(404, "not_found", f"unknown path {u.path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    for needed in ("storm.db", "answers.jsonl"):
        if not (SCEN / needed).exists():
            raise SystemExit(
                f"missing {SCEN/needed} — run storm_runner.py first")
    Handler.store = Store()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"rehearsal shim on http://127.0.0.1:{args.port} "
          f"({len(Handler.store.events)} events) — {DATA_SOURCE}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
