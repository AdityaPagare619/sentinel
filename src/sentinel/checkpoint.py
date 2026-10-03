"""Hourly signed checkpoints — the customer holds the seal (design §5).

The checkpoint job signs (head_seq, head_hash, event_count, window) with
HMAC-SHA256 and pushes to the customer-controlled sink. A `checkpoint`
event is written into the log itself (design §2.7) — the chain covers the
checkpoints; a verifier never trusts an out-of-band manifest.

The signature primitive, stated honestly (design §5):
  * stdlib-only gives us HMAC-SHA256 (no Ed25519). The HMAC key is
    generated ON THE CUSTOMER'S SIDE at onboarding and provided to
    Sentinel; the customer holds the only authoritative copy.
  * What the HMAC buys: (i) integrity of the sink copy in transit/at
    rest — a third party or compromised platform tier cannot forge
    checkpoints; (ii) non-equivocation — we cannot show two different
    heads for the same seq without the customer detecting it.
  * What it does NOT buy: protection against a fully compromised engine
    (which holds the key to sign — but a fully compromised engine could
    simply stop logging; the signature was never the defense against
    that). Ed25519 is the documented future path when stdlib-only is
    relaxed — flagged, not silently substituted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import urllib.request

from .eventlog import EventLog, utcnow_iso

CHECKPOINT_CADENCE_S = 3600.0          # hourly (Type 2)
SINK_FAILURE_PAGE_AFTER_H = 2         # >2 consecutive failed hours -> page (§5)


class SinkError(Exception):
    """The checkpoint push failed."""


class Sink:
    """Customer-controlled checkpoint sink (Type 2: the org's choice; the
    contract — signed, hourly, pushed — is Type 1)."""

    def __init__(self, sink_uri: str):
        self.sink_uri = sink_uri

    def push(self, doc: dict) -> None:
        """Push the signed checkpoint document. Raise SinkError on failure."""
        raise NotImplementedError


class LocalDirSink(Sink):
    """A local directory the customer's own shipper reads."""

    def push(self, doc: dict) -> None:
        try:
            os.makedirs(self.sink_uri, exist_ok=True)
            path = os.path.join(
                self.sink_uri, f"checkpoint-{doc['head_seq']:010d}.json")
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(doc, fh, sort_keys=True)
            os.replace(tmp, path)
        except OSError as exc:
            raise SinkError(f"local sink write failed: {exc}") from exc


class WebhookSink(Sink):
    """An HTTPS webhook to the customer's SIEM."""

    def __init__(self, sink_uri: str, timeout_s: float = 10.0):
        super().__init__(sink_uri)
        self.timeout_s = timeout_s

    def push(self, doc: dict) -> None:
        body = json.dumps(doc, sort_keys=True).encode("utf-8")
        req = urllib.request.Request(
            self.sink_uri, data=body,
            headers={"Content-Type": "application/json",
                     "User-Agent": "sentinel/0.1"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                if not (200 <= resp.status < 300):
                    raise SinkError(f"sink HTTP {resp.status}")
        except OSError as exc:
            raise SinkError(f"sink push failed: {exc}") from exc


def checkpoint_hmac(key: bytes, head_seq: int, head_hash: str,
                    event_count: int, window_start_ts: str,
                    window_end_ts: str) -> str:
    """HMAC-SHA256 over the canonical checkpoint identity."""
    canonical = json.dumps(
        {"head_seq": head_seq, "head_hash": head_hash,
         "event_count": event_count, "window_start_ts": window_start_ts,
         "window_end_ts": window_end_ts},
        sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


class CheckpointJob:
    """Hourly checkpoint job. Owns its background thread lifecycle."""

    def __init__(self, log: EventLog, hmac_key: bytes, sink: Sink,
                 cadence_s: float = CHECKPOINT_CADENCE_S):
        if not hmac_key:
            raise ValueError("checkpoint HMAC key is required — generated on "
                             "the customer's side at onboarding (design §5)")
        self.log = log
        self.hmac_key = hmac_key
        self.sink = sink
        self.cadence_s = cadence_s
        self.consecutive_failures = 0
        self.last_ok: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self, now_iso: str | None = None) -> dict:
        """Sign, push, then write the in-band checkpoint event.

        Order matters: sink_push_ok is recorded IN the event, so the push
        happens first and its outcome is part of the sealed record.
        """
        now_iso = now_iso or utcnow_iso()
        head_seq, head_hash = self.log.head()
        event_count = head_seq  # seq is AUTOINCREMENT from 1: count == head
        window_start_ts = self._last_window_end() or "GENESIS"
        hmac_hex = checkpoint_hmac(self.hmac_key, head_seq, head_hash,
                                   event_count, window_start_ts, now_iso)
        doc = {
            "head_seq": head_seq, "head_hash": head_hash,
            "event_count": event_count,
            "window_start_ts": window_start_ts, "window_end_ts": now_iso,
            "hmac_hex": hmac_hex,
            "sink_uri": self.sink.sink_uri,
        }
        try:
            self.sink.push(doc)
            sink_push_ok = True
            self.consecutive_failures = 0
            self.last_ok = now_iso
        except SinkError:
            sink_push_ok = False
            self.consecutive_failures += 1
        # The chain covers the checkpoints — in-band event either way.
        seq = self.log.append_event(
            "checkpoint", actor="checkpoint", alert_id="checkpoint",
            fingerprint="checkpoint", episode_id="checkpoint",
            body={**doc, "sink_push_ok": sink_push_ok})
        if self.consecutive_failures > SINK_FAILURE_PAGE_AFTER_H:
            # Trust outage: the seal must be verifiable; a broken sink pages
            # (design §5). Control-plane page via the outbox priority lane.
            self.log._enqueue_control_plane_page(
                kind="checkpoint_sink_down",
                summary=("audit degraded: checkpoint sink unreachable for "
                         f"{self.consecutive_failures} consecutive runs"),
                detail={"sink_uri": self.sink.sink_uri,
                        "consecutive_failures": self.consecutive_failures,
                        "ts": now_iso})
            self.log.metrics["evidence_loss_pages"] += 1
        return {"seq": seq, "head_seq": head_seq, "head_hash": head_hash,
                "hmac_hex": hmac_hex, "sink_push_ok": sink_push_ok,
                "consecutive_failures": self.consecutive_failures}

    def _last_window_end(self) -> str | None:
        cps = self.log.events_of_type("checkpoint", limit=1)
        if not cps:
            return None
        import json as _json
        return _json.loads(cps[0]["body"]).get("window_end_ts")

    def run_periodic(self) -> threading.Thread:
        def _loop():
            while not self._stop.wait(self.cadence_s):
                try:
                    self.run_once()
                except Exception:
                    continue  # the checkpoint job never sinks the engine
        self._thread = threading.Thread(target=_loop,
                                        name="sentinel-checkpoint",
                                        daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
