"""Forwarder — relay to PagerDuty/Opsgenie (§3.7).

  page_now            -> POST trigger to PD with the original routing key
  page_business_hours -> POST trigger, severity downgraded to "warning",
                         custom_details["sentinel_queue"] = "business_hours"
  suppress            -> do NOT forward; record only (audit already written)
  passthrough         -> POST the original payload unchanged

Forward errors are logged + counted, never raised, never silently dropped.
Key hygiene: the routing key lives in the request body; we never log request
bodies, and any Authorization header is stripped before request logging.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .models import Alert, Disposition

_USER_AGENT = "sentinel/0.1"
_PD_SEVERITIES = {"critical", "error", "warning", "info"}


@dataclass
class ForwardResult:
    forwarded: bool           # True if an HTTP POST was attempted and accepted
    status_code: int | None
    error: str | None
    action: str               # the disposition action this forward served
    dedup_key: str | None = None


class Forwarder:
    def __init__(self, pd_events_url: str = "https://events.pagerduty.com/v2/enqueue",
                 timeout_s: float = 5.0, default_routing_key: str | None = None):
        self.pd_events_url = pd_events_url
        self.timeout_s = timeout_s
        self.default_routing_key = default_routing_key
        self.metrics: dict[str, int] = {
            "forwarded": 0,   # POSTs accepted (2xx)
            "suppressed": 0,  # suppress dispositions: intentionally not forwarded
            "errors": 0,      # forward attempts that failed
        }

    # ------------------------------------------------------------------ API

    def forward(self, alert: Alert, disposition: Disposition,
                raw_bytes: bytes | None = None) -> ForwardResult:
        """Relay one triaged alert. Never raises."""
        action = disposition.action
        try:
            if action == "suppress":
                self.metrics["suppressed"] += 1
                return ForwardResult(forwarded=False, status_code=None,
                                     error=None, action=action,
                                     dedup_key=_dedup_key_of(alert))
            if action == "page_business_hours":
                body = self._business_hours_body(alert)
            elif _is_pd_shaped(alert) and raw_bytes is not None:
                # PD-shaped alert: relay byte-identical to what arrived.
                body = raw_bytes
            else:
                # Non-PD-shaped alert (or no bytes retained): build the PD
                # trigger event, injecting the routing key.
                event = _pd_event_for(alert, self.default_routing_key)
                if event is None:
                    raise ValueError(
                        "no routing key available for page_now/passthrough")
                body = json.dumps(event, separators=(",", ":")).encode("utf-8")
            return self._post(body, action, _dedup_key_of(alert),
                              alert_id=alert.alert_id)
        except Exception as exc:  # never silently drop, never raise
            return self._fail(action, _dedup_key_of(alert), alert.alert_id, exc)

    def forward_raw(self, raw_bytes: bytes, alert_id: str = "unknown",
                    dedup_key: str | None = None) -> ForwardResult:
        """Best-effort relay of unparseable bytes (receiver fail-open path)."""
        try:
            return self._post(raw_bytes, "passthrough", dedup_key, alert_id=alert_id)
        except Exception as exc:
            return self._fail("passthrough", dedup_key, alert_id, exc)

    # -------------------------------------------------------------- internals

    def _business_hours_body(self, alert: Alert) -> bytes:
        payload = _pd_event_for(alert, self.default_routing_key)
        if payload is None:
            raise ValueError("no routing key available for business-hours queue")
        inner = payload.setdefault("payload", {})
        inner["severity"] = "warning"
        details = inner.setdefault("custom_details", {})
        if isinstance(details, dict):
            details["sentinel_queue"] = "business_hours"
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def _post(self, body: bytes, action: str, dedup_key: str | None,
              alert_id: str) -> ForwardResult:
        req = urllib.request.Request(
            self.pd_events_url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "User-Agent": _USER_AGENT},
        )
        _strip_auth_header(req)  # hygiene: no credentials in any request logging
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                status = resp.getcode()
        except urllib.error.HTTPError as exc:
            # HTTPError is also a response: record the status, count the error.
            return self._fail(action, dedup_key, alert_id, exc,
                              status_code=exc.code)
        if 200 <= status < 300:
            self.metrics["forwarded"] += 1
            return ForwardResult(forwarded=True, status_code=status, error=None,
                                 action=action, dedup_key=dedup_key)
        return self._fail(action, dedup_key, alert_id,
                          RuntimeError(f"unexpected status {status}"),
                          status_code=status)

    def _fail(self, action: str, dedup_key: str | None, alert_id: str,
              exc: BaseException, status_code: int | None = None) -> ForwardResult:
        # The page was already decided; the operator must know the relay failed.
        # Log carries alert_id/action/status only — never the body (routing key).
        self.metrics["errors"] += 1
        print(f"[sentinel] FORWARD FAILED action={action} alert={alert_id} "
              f"status={status_code} error={exc}", file=sys.stderr)
        return ForwardResult(forwarded=False, status_code=status_code,
                             error=str(exc), action=action, dedup_key=dedup_key)


# ------------------------------------------------------------------ helpers

def _strip_auth_header(req: urllib.request.Request) -> None:
    """Remove Authorization before any request logging (§7 HTTP redaction)."""
    try:
        req.remove_header("Authorization")
    except Exception:
        pass
    # urllib capitalizes variants; be thorough.
    for key in list(req.headers.keys()):
        if key.lower() == "authorization":
            del req.headers[key]


def _is_pd_shaped(alert: Alert) -> bool:
    """True when the retained payload is already a PD Events API v2 event."""
    return (alert.source == "pagerduty"
            and isinstance(alert.raw, dict)
            and bool(alert.raw.get("routing_key")))


def _dedup_key_of(alert: Alert) -> str | None:
    raw = alert.raw if isinstance(alert.raw, dict) else {}
    return raw.get("dedup_key") or alert.alert_id or alert.fingerprint


def _pd_event_for(alert: Alert, default_routing_key: str | None) -> dict | None:
    """Build a PD Events API v2 trigger dict for a non-PD-shaped alert.

    Returns None when no routing key is available (caller fails loudly).
    """
    raw = alert.raw if isinstance(alert.raw, dict) else {}
    if alert.source == "pagerduty" and raw.get("routing_key"):
        # Deep copy via JSON so we never mutate the retained original.
        return json.loads(json.dumps(raw))
    routing_key = raw.get("routing_key") or default_routing_key
    if not routing_key:
        return None
    severity = (alert.severity_in or "info").lower()
    if severity not in _PD_SEVERITIES:
        severity = "info"
    return {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": raw.get("dedup_key") or alert.alert_id,
        "payload": {
            "summary": alert.title or alert.alert_id,
            "source": alert.service,
            "severity": severity,
            "component": alert.service,
            "class": alert.check,
            "custom_details": {"sentinel_source": alert.source,
                               "labels": alert.labels or {}},
        },
    }
