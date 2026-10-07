"""PagerDuty Events API v2 wire layer — the forwarder's vendor boundary.

Design 03 (ADR-012) §4. This module owns exactly one thing: turning a frozen
outbox payload into an HTTP POST against PD's Events API v2 and classifying
the outcome with PD's own retry table.

HONESTY CONTRACT (Type 1): PD offers at-least-once acceptance plus
``dedup_key`` idempotency — NOT exactly-once. ``forward_confirmed`` means
"accepted by the vendor's durable intake" (202 + ``status == "success"``).
It does NOT mean "the human's phone rang". Any comment in this codebase
claiming exactly-once delivery fails review.

KEY HYGIENE (Vault mandate): the outbox stores ``payload_frozen`` WITHOUT
the routing key (see below) and ``routing_key_ref`` — a reference to a
secrets entry, never the raw key. The raw key is resolved from the
environment at send time and injected into the wire bytes. Secrets never
touch disk, logs, audit rows, or error messages.

The ``payload_frozen`` keyless decision: design 03 §2.1 describes
``payload_frozen`` as "the exact PD-CEF JSON bytes sent on attempt 1".
Taken literally that would put the routing key on disk (the key lives in
the PD-CEF body — there is no header auth on Events API v2). The Vault
chief's mandate ("secrets never touch disk") wins over the literal reading:
``payload_frozen`` is the exact PD-CEF JSON bytes MINUS the routing key.
The sender injects the resolved key at send time and records
``wire_sha256`` on the receipt, so the audit can still prove what went on
the wire. Retries additionally annotate ``custom_details`` with
``sentinel_retry``/``attempt_no`` (design §4.3, Case 2 — the bounded
duplicate class is self-describing).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# Audit P0 + ruling X-B: under SENTINEL_SIM=1 it is structurally impossible
# to wire a PD client to real PagerDuty. See sim_pd_guard.
from .sim_pd_guard import assert_sim_pd_safe

PD_ENDPOINT = "https://events.pagerduty.com/v2/enqueue"
PD_MAX_DEDUP_KEY_LEN = 255  # PD docs, verified 2026-10-03 (design §4.1)
PD_MAX_PAYLOAD_BYTES = 512 * 1024

# The vendor contract, as data (design 03 §4.1 — verified live against PD's
# Events API v2 docs on 2026-10-03). ``test_pd_retry_table`` asserts the
# classifier below matches this table row-for-row, so a PD docs change
# surfaces as a failing test, not a 3 AM discovery.
PD_RETRY_TABLE = {
    202: "accepted",     # "Accepted — the event has been accepted by PagerDuty". No retry.
    400: "terminal",     # "Bad Request — check the JSON". No retry: OUR bug.
    429: "retryable",    # "Too many API calls at a time". Yes — with backoff.
    500: "retryable",    # "Internal Server Error". Yes — after some time.
    502: "retryable",
    503: "retryable",
    504: "retryable",
    "network": "retryable",
}

_USER_AGENT = "sentinel/1.0 (durable-forwarder)"


# ---------------------------------------------------------------------------
# errors


class SecretMissing(Exception):
    """A routing_key_ref could not be resolved. Never carries the secret."""


class PayloadNotKeyless(Exception):
    """payload_frozen contained a routing_key — an enqueue-side contract
    violation (secrets must never be frozen to disk)."""


class DedupKeyInvalid(Exception):
    """The row's dedup_key is missing or exceeds PD's 255-char limit."""


# ---------------------------------------------------------------------------
# classification


def classify(status_code: int | None, vendor_status: str | None = None,
             network_error: bool = False) -> tuple[str, str]:
    """Map a PD response onto (outcome, detail).

    outcome ∈ {"accepted", "retryable", "terminal"}. A 202 whose body does
    NOT echo status == "success" is a vendor contract violation: treated as
    retryable plus a loud log (design §4.2).
    """
    if network_error:
        return "retryable", "network_error"
    if status_code == 202:
        if vendor_status == "success":
            return "accepted", "accepted_by_vendor_intake"
        return "retryable", "vendor_contract_violation_202_without_success"
    if status_code == 400:
        return "terminal", "bad_request_our_bug"
    if status_code == 429:
        return "retryable", "rate_limited"
    if status_code is not None and 500 <= status_code <= 599:
        return "retryable", f"http_{status_code}"
    if status_code is not None and 400 <= status_code <= 499:
        # Any other 4xx is a client error — retrying the identical bytes is
        # futile. Terminal, loud, secondary fires.
        return "terminal", f"http_{status_code}"
    return "retryable", f"unexpected_{status_code}"


def parse_retry_after(headers) -> float | None:
    """Parse a Retry-After header (delta-seconds) — None when absent/garbage."""
    try:
        raw = headers.get("Retry-After") if headers else None
        if raw is None:
            return None
        secs = float(str(raw).strip())
        return secs if secs >= 0 else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# secrets


def resolve_routing_key(routing_key_ref: str | None,
                        mapping: dict[str, str] | None = None) -> str:
    """Resolve a ``routing_key_ref`` to the raw PagerDuty routing key.

    Resolution (env-only, per the Vault mandate):
      - ``"secret:some/name"`` → ``SENTINEL_SECRET_SOME_NAME``
      - ``"PD_ROUTING_KEY"`` (bare env name) → that variable
      - ``mapping`` (test seam): ref → env var name override

    Raises SecretMissing. The exception never carries the secret value.
    """
    if not routing_key_ref:
        raise SecretMissing("empty routing_key_ref")
    env_name = (mapping or {}).get(routing_key_ref)
    if env_name is None:
        if routing_key_ref.startswith("secret:"):
            name = routing_key_ref[len("secret:"):]
            env_name = "SENTINEL_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_",
                                                  name).upper()
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", routing_key_ref):
            env_name = routing_key_ref
        else:
            raise SecretMissing(
                f"unresolvable routing_key_ref {routing_key_ref!r}")
    value = os.environ.get(env_name)
    if not value:
        raise SecretMissing(
            f"secret {env_name} is not set (ref {routing_key_ref!r})")
    return value


# ---------------------------------------------------------------------------
# wire construction


def build_wire_event(payload_frozen: bytes, *, dedup_key: str,
                     routing_key: str, attempt_no: int = 1) -> bytes:
    """Build the exact bytes POSTed to PD from the frozen (keyless) payload.

    - The frozen payload MUST NOT contain a routing key (PayloadNotKeyless).
    - The ROW's dedup_key wins (it is the pinned idempotency property);
      it must satisfy PD's 255-char limit (DedupKeyInvalid).
    - Frozen PD-CEF events (``event_action`` present) get the key injected.
    - Non-CEF frozen dicts (control-plane pages, ``{"kind","summary",
      "detail"}``) are wrapped into a PD trigger.
    - Retries (attempt_no > 1) annotate ``custom_details`` with
      ``sentinel_retry``/``attempt_no`` (design §4.3, Case 2).
    """
    try:
        frozen = json.loads(payload_frozen.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PayloadNotKeyless(f"payload_frozen is not JSON: {exc}") from exc
    if not isinstance(frozen, dict):
        raise PayloadNotKeyless("payload_frozen must be a JSON object")
    if "routing_key" in frozen:
        # A secret was frozen to disk — enqueue-side bug. Refuse to send.
        raise PayloadNotKeyless(
            "payload_frozen contains routing_key; the frozen payload must be "
            "keyless (Vault mandate: secrets never touch disk)")
    if not dedup_key or len(dedup_key) > PD_MAX_DEDUP_KEY_LEN:
        raise DedupKeyInvalid(
            f"dedup_key must be 1..{PD_MAX_DEDUP_KEY_LEN} chars")

    if frozen.get("event_action") in ("trigger", "acknowledge", "resolve"):
        event = dict(frozen)
        event["routing_key"] = routing_key
        event["dedup_key"] = dedup_key  # the row's pinned key wins
        payload = event.setdefault("payload", {})
    else:
        # Control-plane shape {"kind","summary","detail"} → PD trigger.
        summary = (frozen.get("summary") or frozen.get("kind")
                   or "sentinel control-plane page")
        payload = {
            "summary": str(summary)[:1024],
            "severity": "critical",
            "source": "sentinel/control-plane",
            "component": "sentinel",
            "custom_details": {
                "sentinel_control_kind": frozen.get("kind"),
                "sentinel_control_summary": frozen.get("summary"),
                "sentinel_control_detail": frozen.get("detail"),
            },
        }
        event = {
            "routing_key": routing_key,
            "event_action": "trigger",
            "dedup_key": dedup_key,
            "payload": payload,
        }
    if not isinstance(payload, dict):
        raise PayloadNotKeyless("frozen PD-CEF payload.payload must be an object")
    if attempt_no > 1:
        details = payload.setdefault("custom_details", {})
        if isinstance(details, dict):
            # Self-describing retry (design §4.3 Case 2): the bounded
            # duplicate class carries its own explanation.
            details["sentinel_retry"] = True
            details["attempt_no"] = attempt_no
    return json.dumps(event, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# client


@dataclass
class PDSendResult:
    outcome: str            # "accepted" | "retryable" | "terminal"
    status_code: int | None
    vendor_status: str | None   # the 202 body's "status" field
    latency_ms: float
    retry_after_s: float | None
    error: str | None
    error_class: str        # classifier detail, for the audit + backoff
    wire_sha256: str        # sha256 of the exact bytes POSTed
    dedup_key: str


class PagerDutyClient:
    """POSTs PD-CEF events to the Events API v2. No retries here — the retry
    policy lives in the forwarder (design §5.1); this client reports the
    outcome and the forwarder decides."""

    def __init__(self, endpoint: str = PD_ENDPOINT, timeout_s: float = 5.0,
                 user_agent: str = _USER_AGENT):
        # Structural sim safety (audit P0, ruling X-B): under SENTINEL_SIM=1
        # a non-loopback endpoint cannot be held by any PagerDutyClient.
        assert_sim_pd_safe(endpoint, where="PagerDutyClient.__init__")
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.user_agent = user_agent

    def send(self, *, payload_frozen: bytes, dedup_key: str,
             routing_key: str, attempt_no: int = 1) -> PDSendResult:
        """Send one outbox row's frozen payload. May raise PayloadNotKeyless
        / DedupKeyInvalid (enqueue-side bugs — the forwarder treats these as
        terminal). Network/HTTP outcomes are returned, never raised."""
        wire = build_wire_event(payload_frozen, dedup_key=dedup_key,
                                routing_key=routing_key,
                                attempt_no=attempt_no)
        return self._post(wire, dedup_key)

    def send_event(self, *, event: dict, dedup_key: str,
                   routing_key: str) -> PDSendResult:
        """Degraded-ladder send: an already-shaped event dict, posted
        directly (design §6 F1). The caller owns the audit/spill record."""
        wire = build_wire_event(json.dumps(event, sort_keys=True).encode(),
                                dedup_key=dedup_key, routing_key=routing_key,
                                attempt_no=1)
        return self._post(wire, dedup_key)

    # ------------------------------------------------------------ internals

    def _post(self, wire: bytes, dedup_key: str) -> PDSendResult:
        t0 = time.perf_counter()
        req = urllib.request.Request(
            self.endpoint, data=wire, method="POST",
            headers={"Content-Type": "application/json",
                     "User-Agent": self.user_agent})
        # Hygiene: no Authorization header is ever set on this request; strip
        # defensively before any request logging (v0.1 §7 redaction).
        try:
            req.remove_header("Authorization")
        except Exception:
            pass
        status, headers, raw_body, network_error, error = \
            None, None, b"", False, None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                status = resp.getcode()
                headers = resp.headers
                raw_body = resp.read()
        except urllib.error.HTTPError as exc:
            # HTTPError is also a response: record the status, classify it.
            status = exc.code
            headers = exc.headers
            try:
                raw_body = exc.read()
            except Exception:
                raw_body = b""
        except Exception as exc:  # URLError, TimeoutError, ConnectionError…
            network_error = True
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = (time.perf_counter() - t0) * 1000.0

        vendor_status = None
        if raw_body:
            try:
                vendor_status = json.loads(raw_body.decode(
                    "utf-8", "replace")).get("status")
            except ValueError:
                vendor_status = None
        outcome, detail = classify(status, vendor_status,
                                   network_error=network_error)
        if outcome == "retryable" and detail.startswith(
                "vendor_contract_violation"):
            # 202 without status == "success": loud, always.
            print(f"[sentinel] PD VENDOR CONTRACT VIOLATION status={status} "
                  f"vendor_status={vendor_status!r} dedup_key={dedup_key}",
                  file=sys.stderr)
        retry_after_s = parse_retry_after(headers) if status == 429 else None
        if error is None and outcome != "accepted":
            error = (f"PD {detail}"
                     + (f" status={status}" if status else "")
                     + (f" vendor_status={vendor_status!r}"
                        if vendor_status else ""))
        return PDSendResult(
            outcome=outcome, status_code=status, vendor_status=vendor_status,
            latency_ms=latency_ms, retry_after_s=retry_after_s, error=error,
            error_class=detail,
            wire_sha256=hashlib.sha256(wire).hexdigest(),
            dedup_key=dedup_key)
