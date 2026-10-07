"""LOAD-TEST lane — mixed Jev judge: sampled real + faithful bulk.

Principal approach to millions-scale validation: measure the REAL thing at
statistical significance, simulate the rest faithfully.

- SurrogateJevClient: the real TypeSafe API via the vault surrogate exchange.
  The api_key passed to the constructor is a marker string, NEVER sent on the
  wire, never read from any store by this process. Only hsurr:* surrogates
  leave the process, and only to api.typesafe.ai.
- MixedJevClient: routes decide() to the real client on a sampled fraction
  (default 2%, knob-adjustable, seeded) and to FaithfulJev otherwise.
- SpendCap: hard dollar cap on real calls (default $5). Estimated from
  measured input_tokens per real response at $0.042/M. When the cap trips,
  real routing stops permanently for the run and every further call goes to
  FaithfulJev; the trip is recorded loudly, never silently.
- RatePacer: token-bucket pacing for real calls (default 40/s = vendor bound).
  Real calls never exceed the bound however the run is configured.

Laws: real PagerDuty is never in this file's blast radius (judge only);
no secret is printed, logged, or persisted.
"""
from __future__ import annotations

import json
import random
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import add_surrogate_to_request  # noqa: E402

from sentinel.client import (  # noqa: E402
    DecisionResponse,
    JevError,
    JevRateLimited,
    JevTimeout,
    SystemOneClient,
)

ALLOWED_HOSTS = ["api.typesafe.ai"]
API_URL = "https://api.typesafe.ai/v1/systemone"
USD_PER_M_INPUT_TOKENS = 0.042


class SurrogateJevClient(SystemOneClient):
    """Real Jev; credential attached via authd surrogate, never as a value.

    api_key MUST be the marker "SURROGATE-VAULT" — _post_once is fully
    overridden and never reads self.api_key. If anyone "fixes" this to send
    the marker as a Bearer token, every call 401s loudly.
    """

    MARKER = "SURROGATE-VAULT"

    def __init__(self, *, model: str, timeout_s: float = 30.0):
        super().__init__(api_key=self.MARKER, model=model,
                         timeout_s=timeout_s)
        self._lock = threading.Lock()
        self.calls: list[dict] = []

    def _post_once(self, url, data):
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "User-Agent": "sentinel-loadtest/1.0"},
        )
        add_surrogate_to_request(req, "custom.typesafe",
                                 allowed_hosts=ALLOWED_HOSTS)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            self._raise_for_status(exc)
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise JevTimeout(
                    f"Jev request timed out after {self.timeout_s:.1f}s"
                ) from exc
            raise JevError(f"Jev connection failed: {reason}") from exc
        latency_ms = (time.monotonic() - t0) * 1000.0
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JevError(f"unreadable body: {exc}") from exc
        with self._lock:
            self.calls.append({
                "latency_ms": round(latency_ms, 1),
                "input_tokens": int(((payload.get("usage") or {})
                                     .get("input_tokens", 0))),
            })
        return payload

    def spend_usd(self) -> float:
        with self._lock:
            tokens = sum(c["input_tokens"] for c in self.calls)
        return tokens / 1_000_000 * USD_PER_M_INPUT_TOKENS

    def latencies(self) -> list[float]:
        with self._lock:
            return [c["latency_ms"] for c in self.calls]


class RatePacer:
    """Token bucket: at most `rate_per_s` real calls per second."""

    def __init__(self, rate_per_s: float = 40.0):
        self._rate = float(rate_per_s)
        self._lock = threading.Lock()
        self._tokens = float(rate_per_s)
        self._last = time.monotonic()

    def acquire(self):
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._rate,
                    self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
            time.sleep(deficit / self._rate)


class MixedJevClient:
    """Sampled-real + faithful-bulk judge.

    decide() routes to the real client with probability `sample_rate`
    (seeded draw per call) and to the faithful client otherwise. The spend
    cap is absolute: once tripped, real routing stops for the rest of the
    run. Every routing decision is recorded in `trace`.
    """

    def __init__(self, *, real: SurrogateJevClient, faithful,
                 sample_rate: float = 0.02, spend_cap_usd: float = 5.0,
                 pace_per_s: float = 40.0, seed=None):
        if not 0.0 <= sample_rate <= 1.0:
            raise ValueError("sample_rate must be in [0,1]")
        self.real = real
        self.faithful = faithful
        self.sample_rate = float(sample_rate)
        self.spend_cap_usd = float(spend_cap_usd)
        self._pacer = RatePacer(pace_per_s)
        self._rng = random.Random(seed)
        self._rng_lock = threading.Lock()
        self._lock = threading.Lock()
        self._faithful_lock = threading.Lock()
        # Routing counts (not a per-call list: at 1M alerts the list would
        # hold ~400K dicts). A bounded recent sample stays for debugging.
        self._route_counts: dict[str, int] = {}
        self._route_errors: dict[str, int] = {}
        self.trace: list[dict] = []  # bounded recent sample (<= 1000)
        self.cap_tripped = False
        # Archived per-batch faithful latencies (delegates are swapped and
        # dropped; their timing evidence must survive for the report).
        self.faithful_latencies: list[float] = []
        self.faithful_faults: dict[str, int] = {}
        # The gate reads .model; be honest that this is a mixture.
        self.model = f"mixed:sampled-real@{sample_rate}+faithful"

    def _archive_trace(self, faithful) -> None:
        # Idempotent per delegate: mark archived so double-flush can't
        # double-count.
        if getattr(faithful, "_lt_archived", False):
            return
        for rec in faithful.trace:
            self.faithful_latencies.append(rec["latency_ms"])
            f = rec.get("fault")
            if f:
                self.faithful_faults[f] = self.faithful_faults.get(f, 0) + 1
        faithful._lt_archived = True

    def set_faithful(self, faithful) -> None:
        """Swap the bulk delegate between drained batches.

        The race caches THIS client for the whole run; only the faithful
        delegate swaps. Call only when no race is in flight (batch drained).
        """
        with self._faithful_lock:
            old = self.faithful
            if old is not None:
                self._archive_trace(old)
            self.faithful = faithful

    def _current_faithful(self):
        with self._faithful_lock:
            return self.faithful

    def _note_route(self, route: str, error: str | None = None) -> None:
        """Count a routing decision; keep a bounded recent sample."""
        with self._lock:
            self._route_counts[route] = self._route_counts.get(route, 0) + 1
            if error:
                self._route_errors[error] = \
                    self._route_errors.get(error, 0) + 1
            if len(self.trace) < 1000:
                rec = {"route": route, "ok": error is None}
                if error:
                    rec["error"] = error
                self.trace.append(rec)

    def _draw_real(self) -> bool:
        with self._rng_lock:
            return self._rng.random() < self.sample_rate

    def decide(self, state, questions) -> DecisionResponse:
        use_real = self._draw_real()
        if use_real:
            with self._lock:
                tripped = self.cap_tripped
            if not tripped and self.real.spend_usd() >= self.spend_cap_usd:
                with self._lock:
                    self.cap_tripped = True
                print("[loadtest] SPEND CAP TRIPPED: real routing stopped; "
                      "faithful bulk carries the rest of the run.",
                      file=sys.stderr, flush=True)
                tripped = True
            if not tripped:
                self._pacer.acquire()
                try:
                    resp = self.real.decide(state, questions)
                except Exception as exc:
                    # A real-call failure must never kill the run: record it
                    # and fall back to the faithful bulk for THIS call.
                    self._note_route("real", error=type(exc).__name__)
                    return self._current_faithful().decide(state, questions)
                self._note_route("real")
                return resp
        faithful = self._current_faithful()
        resp = faithful.decide(state, questions)
        self._note_route("faithful")
        return resp

    def summary(self) -> dict:
        # Flush the current delegate's trace so the final batch counts.
        with self._faithful_lock:
            if self.faithful is not None:
                self._archive_trace(self.faithful)
        with self._lock:
            routes = dict(self._route_counts)
            route_errors = dict(self._route_errors)
        lat = sorted(self.faithful_latencies)
        def q(x):
            return lat[min(int(x * len(lat)), len(lat) - 1)] if lat else 0
        return {
            "sample_rate": self.sample_rate,
            "routed": routes,
            "route_errors": route_errors,
            "cap_tripped": self.cap_tripped,
            "spend_cap_usd": self.spend_cap_usd,
            "real_spend_usd": round(self.real.spend_usd(), 4),
            "real_calls": len(self.real.calls),
            "real_latency_ms": self.real.latencies(),
            "faithful_calls": len(self.faithful_latencies),
            "faithful_latency_p50_ms": round(q(0.50), 1),
            "faithful_latency_p99_ms": round(q(0.99), 1),
            "faithful_faults": dict(self.faithful_faults),
        }
