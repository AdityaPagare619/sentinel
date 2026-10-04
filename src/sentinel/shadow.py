"""Shadow tap — Stage 0 read-only webhook receiver (design 06, §a).

The shadow tap is the proof engine's adoption surface: an ADDITIVE,
read-only ingress that receives copies of the estate's alert/incident
events (PagerDuty v3 webhooks, Opsgenie outgoing webhooks, Alertmanager
webhook batches) and runs each through the gate in shadow mode. Nothing
the tap does can change the estate's paging behavior:

  * separate HTTP routes from the paging receiver (`/shadow/...`);
  * inbound HMAC/Bearer <redacted> verification only — the tap holds no
    write credentials of any kind (ShadowConfig.write_credentials must be
    empty; a non-empty list REFUSES TO BOOT the shadow profile);
  * the ShadowPipeline object graph contains NO reference to any paging
    or vendor-write code path — enforced by the static import-graph
    guard in tests/test_shadow.py::TestReadOnlyProof;
  * shadow-route handler exceptions return 500 (vendor retries; ingest
    is idempotent) and NEVER fall through to the receiver's
    fail-open-to-page last resort.

Vendor event coverage (design 06 pre-mortem F1 — subscription parity):
  PagerDuty v3: incident.triggered, incident.acknowledged,
    incident.resolved, incident.escalated, incident.priority_updated.
    priority_updated is load-bearing: a severity upgrade on an open
    incident re-runs the gate so the "would have" verdict tracks the
    estate's source of truth.
  Opsgenie: Create / Acknowledge / Close actions, plus any custom
    action whose name contains "priority" (the priority-update
    equivalent on estates that define one).
  Alertmanager: firing / resolved batches. AM carries no native
    signature: Bearer <redacted> over TLS (mTLS is a per-estate option).
    The AM payload has no priority primitive — the estate-severity
    mapping gap is documented on ShadowConfig.

Idempotency (design 06 §a.2 — vendors retry deliveries):
  PD: (x-webhook-subscription, event.id)
  Opsgenie: (alertId, action, actionTimestamp)
  Alertmanager: (fingerprint, status, startsAt) per alert in the batch.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .correlator import Correlator, fingerprint_for
from .counterfactual import normalize_counterfactual_preset
from .gate import evaluate_policy
from .models import Alert, DecisionRecord, Disposition, Thresholds
from .state import build_state, input_sha256
from .storm_digest import storm_digest_disposition


# ---------------------------------------------------------------- constants

# PagerDuty Generic Webhook (v3) incident event types. The mandatory set
# (design 06 F1): triggered + acknowledged + resolved + escalated +
# priority_updated. Forgetting priority_updated is the exact failure the
# pre-mortem names; it is a first-class member here, not an afterthought.
PD_EVENT_TYPES = frozenset({
    "incident.triggered",
    "incident.acknowledged",
    "incident.resolved",
    "incident.escalated",
    "incident.priority_updated",
})
REQUIRED_PD_EVENTS = PD_EVENT_TYPES  # F1 subscription-parity gate

# Opsgenie outgoing-webhook alert actions (case-insensitive match).
OG_ACTION_CREATE = "create"
OG_ACTION_ACK = "acknowledge"
OG_ACTION_CLOSE = "close"

# Alertmanager batch statuses.
AM_STATUS_FIRING = "firing"
AM_STATUS_RESOLVED = "resolved"

# Default buyer-severity mapping. SEV1/SEV2 are the BUYER's taxonomy in
# production (signed scope agreement, design 06 §c.1 step 0); these
# defaults are the starting point, overridden per estate in ShadowConfig.
DEFAULT_SEVERITY_MAP = {
    "P1": "SEV1", "P2": "SEV2", "P3": "SEV3", "P4": "SEV4", "P5": "SEV4",
    "critical": "SEV2", "high": "SEV2", "warning": "SEV3", "info": "SEV4",
}

SEV12 = frozenset({"SEV1", "SEV2"})

# Would-be verdicts that mean "the legacy page would still have fired".
WOULD_PAGE = frozenset({"page_now", "page_business_hours", "passthrough"})
WOULD_SUPPRESS = frozenset({"suppress"})

MAX_BODY_BYTES = 8 * 1024 * 1024


# ------------------------------------------------------------------ config

@dataclass
class ShadowConfig:
    """Stage-0 tap configuration.

    write_credentials: MUST be empty. The shadow profile provisions only
    inbound *verification* material (HMAC secrets, Bearer <redacted>).
    A non-empty list refuses to boot (design 06 §a.4 guarantee 1 —
    fail-closed on its own safety invariant).
    """
    enabled: bool = False
    pd_secret: str | None = None          # HMAC key for x-pagerduty-signature
    opsgenie_token: str | None = None     # Bearer <redacted> for the OG route; REQUIRED
                                          # to serve it — a missing token 401s
                                          # every OG delivery (fail closed,
                                          # consistent with PD and AM).
    alertmanager_token: str | None = None  # Bearer <redacted>; required when the AM route is used
    write_credentials: list = field(default_factory=list)
    severity_map: dict = field(default_factory=lambda: dict(DEFAULT_SEVERITY_MAP))
    # Threshold counterfactual presets (design 06 §b.2, ADR-023).
    counterfactual_presets: tuple = (0.85, 0.90, 0.95, 0.99)

    def validate(self) -> None:
        """Fail closed on the safety invariant. Raises SystemExit."""
        if self.write_credentials:
            raise SystemExit(
                "REFUSING TO BOOT shadow profile: write_credentials is "
                f"non-empty ({len(self.write_credentials)} entries). The "
                "Stage-0 tap may not hold write credentials (design 06 §a.4).")


def shadow_config_from_env() -> ShadowConfig | None:
    """Build the tap config from the environment.

    Returns None when SENTINEL_SHADOW_TAP != 1 (routes stay 404).
    SENTINEL_SHADOW_WRITE_CREDENTIALS: comma-separated names; ANY entry
    refuses to boot via ShadowConfig.validate().
    """
    if os.environ.get("SENTINEL_SHADOW_TAP", "0") != "1":
        return None
    write_creds = [c.strip() for c in
                   os.environ.get("SENTINEL_SHADOW_WRITE_CREDENTIALS", "").split(",")
                   if c.strip()]
    cfg = ShadowConfig(
        enabled=True,
        pd_secret=os.environ.get("SENTINEL_SHADOW_PD_SECRET"),
        opsgenie_token=os.environ.get("SENTINEL_SHADOW_OG_TOKEN"),
        alertmanager_token=os.environ.get("SENTINEL_SHADOW_AM_TOKEN"),
        write_credentials=write_creds,
    )
    cfg.validate()
    return cfg


# ---------------------------------------------------------------- auth

def _headers_lower(headers) -> dict:
    return {str(k).lower(): v for k, v in dict(headers).items()}


def verify_pd_signature(body: bytes, secret: str | None, headers: dict) -> tuple[bool, str]:
    """Verify PagerDuty's x-pagerduty-signature (HMAC-SHA256 over the body).

    Missing or bad signatures are dropped and counted (design 06 §a.2 —
    the drop counter is itself reported; silence about tap health is
    forbidden). When no secret is configured the tap fails closed.
    """
    if not secret:
        return False, "no pd signing secret configured"
    sig = _headers_lower(headers).get("x-pagerduty-signature")
    if not sig:
        return False, "missing x-pagerduty-signature"
    sig = sig.strip()
    for prefix in ("sha256=", "v1="):
        if sig.startswith(prefix):
            sig = sig[len(prefix):]
            break
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False, "bad x-pagerduty-signature"
    return True, "ok"


def verify_bearer(headers: dict, token: str | None, required: bool,
                  what: str) -> tuple[bool, str]:
    """Verify an `Authorization: Bearer <token>` header."""
    if token is None:
        if required:
            return False, f"no {what} token configured"
        return True, "ok (auth not configured)"
    presented = _headers_lower(headers).get("authorization", "")
    want = f"Bearer {token}"
    if not hmac.compare_digest(presented, want):
        return False, f"bad or missing Bearer <redacted> ({what})"
    return True, "ok"


# ---------------------------------------------------------------- events

@dataclass
class VendorEvent:
    """One normalized vendor lifecycle event (trigger-like or lifecycle)."""
    vendor: str            # "pagerduty" | "opsgenie" | "alertmanager"
    event_type: str        # e.g. "incident.triggered", "Create", "firing"
    event_id: str          # vendor event id (idempotency input)
    incident_id: str       # vendor incident/alert id (episode key input)
    occurred_at: str       # ISO-8601
    service: str
    title: str
    severity_raw: str | None   # vendor-native severity/priority label
    estate_severity: str | None  # buyer's taxonomy via ShadowConfig.severity_map
    incident_url: str | None
    # True for trigger-like events (triggered/Create/firing) and for
    # priority updates — these (re-)run the gate.
    gate_relevant: bool
    raw: dict = field(default_factory=dict)


def _estate_severity(severity_map: dict, raw: str | None) -> str | None:
    if not raw:
        return None
    return severity_map.get(str(raw).strip(), severity_map.get(str(raw).strip().lower()))


def parse_pd_event(data: dict, headers: dict, severity_map: dict) -> VendorEvent:
    """PagerDuty Generic Webhook (v3) -> VendorEvent. Raises ValueError."""
    if not isinstance(data, dict) or not isinstance(data.get("event"), dict):
        raise ValueError("pd v3 payload needs an 'event' object")
    event = data["event"]
    event_type = str(event.get("event_type") or "")
    event_id = str(event.get("id") or "")
    if not event_type or not event_id:
        raise ValueError("pd v3 event needs event_type and id")
    payload = event.get("data") or {}
    incident_id = str(payload.get("id") or event_id)
    service = ""
    svc = payload.get("service")
    if isinstance(svc, dict):
        service = str(svc.get("summary") or "")
    priority = payload.get("priority")
    priority_name = priority.get("summary") if isinstance(priority, dict) else None
    urgency = payload.get("urgency")
    severity_raw = priority_name or (str(urgency) if urgency else None)
    title = str(payload.get("title") or payload.get("summary") or incident_id)
    incident_url = payload.get("html_url") if isinstance(payload.get("html_url"), str) else None
    gate_relevant = event_type in ("incident.triggered", "incident.priority_updated")
    return VendorEvent(
        vendor="pagerduty", event_type=event_type, event_id=event_id,
        incident_id=incident_id,
        occurred_at=str(event.get("occurred_at") or _utcnow_iso()),
        service=service or "unknown", title=title,
        severity_raw=severity_raw,
        estate_severity=_estate_severity(severity_map, severity_raw),
        incident_url=incident_url, gate_relevant=gate_relevant, raw=data,
    )


def parse_opsgenie_event(data: dict, severity_map: dict) -> VendorEvent:
    """Opsgenie outgoing webhook -> VendorEvent. Raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("opsgenie payload must be an object")
    alert = data.get("alert") if isinstance(data.get("alert"), dict) else data
    alert_id = str(alert.get("alertId") or data.get("alertId") or "")
    if not alert_id:
        raise ValueError("opsgenie payload needs alertId")
    action = str(data.get("action") or alert.get("action") or "Create")
    action_l = action.lower()
    if action_l == OG_ACTION_CREATE:
        gate_relevant = True
    elif "priority" in action_l:
        # Priority-update-equivalent custom action (design 06 F1).
        gate_relevant = True
    else:
        gate_relevant = False
    teams = alert.get("teams") or []
    service = ""
    if teams and isinstance(teams[0], dict):
        service = str(teams[0].get("name") or "")
    priority = alert.get("priority")
    severity_raw = str(priority) if priority else None
    occurred_at = data.get("actionTimestamp") or alert.get("createdAt") or _utcnow_iso()
    return VendorEvent(
        vendor="opsgenie", event_type=action, event_id=alert_id,
        incident_id=alert_id, occurred_at=str(occurred_at),
        service=service or "opsgenie", title=str(alert.get("message") or alert_id),
        severity_raw=severity_raw,
        estate_severity=_estate_severity(severity_map, severity_raw),
        incident_url=None, gate_relevant=gate_relevant, raw=data,
    )


def parse_alertmanager_events(data: dict, severity_map: dict) -> list[VendorEvent]:
    """Alertmanager webhook batch -> one VendorEvent per alert.

    Raises ValueError. Idempotency key per alert:
    (fingerprint, status, startsAt) — design 06 §a.2.
    """
    if not isinstance(data, dict) or not isinstance(data.get("alerts"), list):
        raise ValueError("alertmanager payload needs an 'alerts' list")
    events = []
    for item in data["alerts"]:
        if not isinstance(item, dict):
            continue
        labels = item.get("labels") or {}
        annotations = item.get("annotations") or {}
        fingerprint = str(item.get("fingerprint") or "")
        status = str(item.get("status") or data.get("status") or "")
        starts_at = str(item.get("startsAt") or "")
        service = str(labels.get("service") or labels.get("job")
                      or "alertmanager")
        check = str(labels.get("alertname") or "alert")
        severity_raw = labels.get("severity")
        events.append(VendorEvent(
            vendor="alertmanager", event_type=status,
            event_id=fingerprint or f"{service}:{check}:{starts_at}",
            incident_id=fingerprint or f"{service}:{check}",
            occurred_at=starts_at or _utcnow_iso(),
            service=service, title=str(annotations.get("summary") or check),
            severity_raw=str(severity_raw) if severity_raw else None,
            estate_severity=_estate_severity(severity_map, severity_raw),
            incident_url=None,
            gate_relevant=(status == AM_STATUS_FIRING),
            raw={"alert": item, "commonLabels": data.get("commonLabels", {})},
        ))
    return events


# ---------------------------------------------------------------- episodes

@dataclass
class ShadowEpisode:
    """One incident/alert episode as observed on the read-only tap."""
    key: str                 # e.g. "pd:inc/PABC123", "og:alert/9d1f", "am:fp:11ab"
    vendor: str
    incident_id: str
    service: str
    title: str
    opened_at: str
    incident_url: str | None = None
    human_paged: bool = False
    human_acknowledged: bool = False
    human_resolved: bool = False
    estate_severity: str | None = None          # severity at occurrence
    final_severity: str | None = None           # severity at last observation
    severity_history: list = field(default_factory=list)  # (ts, severity) — F1/F2 input
    event_count: int = 0


def _episode_key(ev: VendorEvent) -> str:
    prefix = {"pagerduty": "pd:inc", "opsgenie": "og:alert",
              "alertmanager": "am:fp"}[ev.vendor]
    return f"{prefix}/{ev.incident_id}"


# ---------------------------------------------------------------- observations

@dataclass
class ShadowObservation:
    """One recorded shadow observation: what the estate did + what the
    gate would have done. Recorded, never executed (design 06 §a.3)."""
    observation_id: str
    received_at: str
    vendor: str
    event_type: str
    event_id: str
    episode_key: str
    human_disposition: str   # "paged" | "unpaged" | "unknown"
    gate_would: str          # "page_now" | "page_business_hours" | "suppress"
                             # | "passthrough" | "not_evaluated"
    gate_confidence: float | None
    gate_reason: str | None
    gate_latency_ms: float | None
    estate_severity: str | None
    evidence: dict = field(default_factory=dict)
    threshold_counterfactual: dict = field(default_factory=dict)


class ShadowStore:
    """Thread-safe in-memory store of shadow observations and episodes.

    The event-log lane owns the durable `events` table; this store is the
    tap's working set and the Shadow Report's input. Forwarding of
    `shadow_decision` events to the log happens on ShadowPipeline via its
    `event_sink` constructor arg (exact append_event contract documented
    on ShadowPipeline.__init__) — the tap never depends on it.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.observations: list[ShadowObservation] = []
        self.episodes: dict[str, ShadowEpisode] = {}
        self._seen_idempotency: set[str] = set()
        self.counters: dict[str, int] = {
            "ingested": 0,
            "dropped_bad_signature": 0,
            "dropped_unauthenticated": 0,
            "dropped_unparseable": 0,
            "duplicate_collapsed": 0,
            "unknown_event_type": 0,
        }
        self._payload_total = 0
        self._payload_with_details = 0

    # -- ingest ------------------------------------------------------
    def claim_idempotency(self, key: str) -> bool:
        """True if this delivery key was already seen (duplicate)."""
        with self._lock:
            if key in self._seen_idempotency:
                self.counters["duplicate_collapsed"] += 1
                return True
            self._seen_idempotency.add(key)
            self.counters["ingested"] += 1
            return False

    def drop(self, reason: str) -> None:
        with self._lock:
            self.counters[reason] += 1

    def note_payload(self, has_details: bool) -> None:
        with self._lock:
            self._payload_total += 1
            if has_details:
                self._payload_with_details += 1

    def get_or_create_episode(self, ev: VendorEvent) -> ShadowEpisode:
        key = _episode_key(ev)
        with self._lock:
            ep = self.episodes.get(key)
            if ep is None:
                ep = ShadowEpisode(
                    key=key, vendor=ev.vendor, incident_id=ev.incident_id,
                    service=ev.service, title=ev.title, opened_at=ev.occurred_at,
                    incident_url=ev.incident_url,
                    estate_severity=ev.estate_severity,
                    final_severity=ev.estate_severity)
                self.episodes[key] = ep
            return ep

    def apply_lifecycle(self, ep: ShadowEpisode, ev: VendorEvent) -> None:
        """Fold a lifecycle event into its episode (F1: priority_updated
        moves the estate's source of truth for severity)."""
        with self._lock:
            ep.event_count += 1
            et = ev.event_type
            if et in ("incident.triggered", "Create", "create", AM_STATUS_FIRING):
                ep.human_paged = True
            elif et in ("incident.acknowledged", "Acknowledge", "acknowledge"):
                ep.human_acknowledged = True
                ep.human_paged = True
            elif et in ("incident.escalated",):
                ep.human_paged = True
            elif et in ("incident.resolved", "Close", "close", AM_STATUS_RESOLVED):
                ep.human_resolved = True
            elif et in ("incident.priority_updated",) or "priority" in et.lower():
                if ev.estate_severity and ev.estate_severity != ep.final_severity:
                    ep.severity_history.append((ev.occurred_at, ep.final_severity,
                                                ev.estate_severity))
                    ep.final_severity = ev.estate_severity
            if ev.incident_url and not ep.incident_url:
                ep.incident_url = ev.incident_url

    def record(self, obs: ShadowObservation) -> None:
        with self._lock:
            self.observations.append(obs)

    # -- reads -------------------------------------------------------
    def tap_health(self) -> dict:
        with self._lock:
            counters = dict(self.counters)
            total = self._payload_total
            with_details = self._payload_with_details
        dropped = (counters["dropped_bad_signature"]
                   + counters["dropped_unauthenticated"]
                   + counters["dropped_unparseable"])
        return {
            "ingested": counters["ingested"],
            "dropped": dropped,
            "dropped_by_reason": {
                "bad_signature": counters["dropped_bad_signature"],
                "unauthenticated": counters["dropped_unauthenticated"],
                "unparseable": counters["dropped_unparseable"],
            },
            "duplicate_collapsed": counters["duplicate_collapsed"],
            "unknown_event_type": counters["unknown_event_type"],
            "payload_completeness_pct": (100.0 * with_details / total
                                        if total else 100.0),
            "payload_total": total,
        }

    def observations_for_episode(self, episode_key: str) -> list[ShadowObservation]:
        with self._lock:
            return [o for o in self.observations if o.episode_key == episode_key]


# ---------------------------------------------------------------- normalize

def alert_from_vendor_event(ev: VendorEvent) -> Alert:
    """VendorEvent -> Alert (the gate's input shape). Raises on bad input."""
    region = ""
    raw = ev.raw
    if ev.vendor == "alertmanager":
        labels = (raw.get("alert") or {}).get("labels") or {}
        region = str(labels.get("region") or "")
    elif ev.vendor == "opsgenie":
        details = ((raw.get("alert") or {}).get("details")) or {}
        region = str(details.get("region") or "")
    check = {"pagerduty": "pagerduty.incident",
             "opsgenie": "opsgenie.alert",
             "alertmanager": "alertmanager.alert"}[ev.vendor]
    severity_in = ev.severity_raw or "unknown"
    alert = Alert(
        alert_id=f"{ev.vendor}:{ev.incident_id}",
        received_at=ev.occurred_at,
        fingerprint="",
        service=ev.service,
        check=check,
        severity_in=str(severity_in),
        title=ev.title,
        source=ev.vendor,
        labels={"region": region,
                "estate_severity": ev.estate_severity or "",
                "vendor_event_type": ev.event_type},
        raw={"vendor_event": {"event_type": ev.event_type,
                              "incident_id": ev.incident_id,
                              "severity_raw": ev.severity_raw}},
    )
    alert.fingerprint = fingerprint_for(alert.service, alert.check,
                                        alert.severity_in, region,
                                        env="", cluster="")
    # Vendor events carry no env/cluster labels: the empty namespace is
    # explicit here (a real "unknown" namespace, not a wildcard).
    return alert


# ------------------------------------------------- DR-26: no policy mirror

def threshold_counterfactual(alert, q_severity, q_team, q_disposition,
                             thresholds: Thresholds, presets,
                             *, jev_model=None, prob_lock_pass=False,
                             in_allowlist=False,
                             freshness_report=None) -> dict[str, str]:
    """Per-preset disposition through the REAL policy kernel (DR-26, D9).

    The old ``policy_action`` mirror ("Pure mirror of Gate._decide's policy
    table for counterfactuals") is DELETED: it reimplemented the pre-ADR-013
    table and had already drifted. One kernel, evaluated N+1 times — the
    same ``evaluate_policy`` the live gate calls, so the next policy-table
    change moves every consumer together.

    Each preset is a confidence-floor override: bare numbers keep the
    historic ``(0.85, 0.90, 0.95, 0.99)`` config shape; dicts take
    ``suppress_conf_min``. Normalized + validated by
    ``counterfactual.normalize_counterfactual_preset`` — invalid presets
    raise loudly (a silent default here was the old quiet lie).
    """
    out: dict[str, str] = {}
    for raw in presets or ():
        preset = normalize_counterfactual_preset(raw)
        scm = preset.get("suppress_conf_min")
        if scm is None:
            raise ValueError(
                f"shadow counterfactual preset {preset['name']!r}: the "
                "shadow tap only supports the suppress_conf_min axis")
        verdict = evaluate_policy(
            alert, jev_model=jev_model,
            q_severity=q_severity, q_team=q_team, q_disposition=q_disposition,
            thresholds=thresholds,
            allowlist={alert.fingerprint} if in_allowlist else set(),
            latency_ms=0.0, prob_lock_pass=prob_lock_pass,
            freshness_report=freshness_report,
            suppress_conf_min_override=scm)
        out[preset["name"]] = verdict.action
    return out


# ---------------------------------------------------------------- F1 check

def subscription_coverage_check(observed_event_types: set[str],
                                vendor: str = "pagerduty") -> dict:
    """F1 — subscription parity: the canary's event subscription must cover
    every event type the estate's stack actually emitted in the shadow
    window (enumerated from the ledger's ground truth, not the gate's
    inputs). Returns the missing types; a non-empty set BLOCKS T2ab."""
    required = REQUIRED_PD_EVENTS if vendor == "pagerduty" else set()
    missing = set(required) - set(observed_event_types)
    return {"vendor": vendor, "required": sorted(required),
            "observed": sorted(observed_event_types),
            "missing": sorted(missing),
            "parity_ok": not missing}


# ---------------------------------------------------------------- pipeline

class ShadowPipeline:
    """Stage-0 ingest: verify -> parse -> episode -> gate (shadow mode).

    READ-ONLY BY CONSTRUCTION: this class holds no reference to any
    paging or vendor-write path. It is built with (gate, correlator,
    store, config) only. The static guard in tests/test_shadow.py
    (TestReadOnlyProof) asserts this module never imports or names the
    write path, and the dynamic proof ingests 100 synthetic incidents
    through it with a refusing write-path double attached to the main
    pipeline, asserting zero invocations.
    """

    def __init__(self, gate, correlator: Correlator | None,
                 store: ShadowStore, config: ShadowConfig,
                 allowlist: set[str] | None = None, event_sink=None):
        """event_sink: optional duck-typed event-log sink. EXACT contract
        (must match EventLog.append_event, eventlog.py from the event-log
        lane — checked by tests/test_shadow.py::TestEventSink with a
        strict fake):

            append_event(event_type: str, *, actor: str, alert_id: str,
                         fingerprint: str, episode_id: str,
                         outbox_id: str | None = None, body: dict,
                         ts: str | None = None) -> int

        All kwargs after event_type are keyword-only. The tap calls it as
        append_event("shadow_decision", actor="engine", alert_id=...,
        fingerprint=..., episode_id=..., body={...}) — nothing else. The
        "shadow_decision" body must carry "links" (required by the
        event-log schema). The tap never depends on the sink: exceptions
        are swallowed (stderr) so the sink can never sink the tap.
        """
        if not getattr(gate, "shadow", False):
            raise ValueError("ShadowPipeline requires a gate in shadow mode")
        self.gate = gate
        self.correlator = correlator or Correlator()
        self.store = store
        self.config = config
        self.allowlist = set(allowlist or [])
        self.event_sink = event_sink  # duck-typed per the contract above
        # Fingerprint -> the last full (non-duplicate) evaluation's inputs,
        # so duplicates reuse the original evidence/counterfactual inputs.
        self._eval_cache: dict[str, dict] = {}
        self._last_corr = None

    # ------------------------------------------------------------ entry point

    def handle(self, vendor: str, body: bytes, headers: dict) -> tuple[int, dict]:
        """Ingest one vendor delivery. Returns (http_status, response)."""
        if len(body) > MAX_BODY_BYTES:
            return 413, {"status": "error", "message": "payload too large"}
        ok, reason = self._verify(vendor, body, headers)
        if not ok:
            if "signature" in reason:
                self.store.drop("dropped_bad_signature")
                return 403, {"status": "error", "message": reason}
            self.store.drop("dropped_unauthenticated")
            return 401, {"status": "error", "message": reason}
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.store.drop("dropped_unparseable")
            return 400, {"status": "error", "message": "unparseable json"}
        try:
            if vendor == "pagerduty":
                events = [parse_pd_event(data, headers, self.config.severity_map)]
            elif vendor == "opsgenie":
                events = [parse_opsgenie_event(data, self.config.severity_map)]
            else:
                # alertmanager — _verify 401s any other vendor before we
                # get here, so no else branch is needed.
                events = parse_alertmanager_events(data, self.config.severity_map)
        except ValueError as exc:
            self.store.drop("dropped_unparseable")
            return 400, {"status": "error", "message": str(exc)}
        accepted, duplicates = 0, 0
        for ev in events:
            key = self._idempotency_key(vendor, ev, headers)
            if self.store.claim_idempotency(key):
                duplicates += 1
                continue
            accepted += 1
            self._ingest_event(ev)
        return 202, {"status": "accepted", "events": accepted,
                     "duplicates": duplicates}

    # ------------------------------------------------------------ internals

    def _verify(self, vendor: str, body: bytes, headers: dict) -> tuple[bool, str]:
        if vendor == "pagerduty":
            return verify_pd_signature(body, self.config.pd_secret, headers)
        if vendor == "opsgenie":
            # Fail closed: no token means the OG route refuses every
            # delivery (401), never silently accepts them unauthenticated.
            return verify_bearer(headers, self.config.opsgenie_token,
                                 required=True, what="opsgenie")
        if vendor == "alertmanager":
            return verify_bearer(headers, self.config.alertmanager_token,
                                 required=True, what="alertmanager")
        return False, "unknown vendor"

    def _idempotency_key(self, vendor: str, ev: VendorEvent, headers: dict) -> str:
        h = _headers_lower(headers)
        if vendor == "pagerduty":
            sub = h.get("x-webhook-subscription", "")
            return f"pd:{sub}:{ev.event_id}"
        if vendor == "opsgenie":
            ts = ""
            raw = ev.raw
            alert = raw.get("alert") if isinstance(raw.get("alert"), dict) else raw
            ts = str(raw.get("actionTimestamp") or alert.get("createdAt") or "")
            return f"og:{ev.incident_id}:{ev.event_type}:{ts}"
        # alertmanager: (fingerprint, status, startsAt) — design 06 §a.2
        starts_at = ""
        alert = (ev.raw.get("alert") or {})
        if isinstance(alert, dict):
            starts_at = str(alert.get("startsAt") or "")
        return f"am:{ev.incident_id}:{ev.event_type}:{starts_at}"

    def _ingest_event(self, ev: VendorEvent) -> None:
        self.store.note_payload(self._has_details(ev))
        ep = self.store.get_or_create_episode(ev)
        self.store.apply_lifecycle(ep, ev)
        if ev.event_type not in PD_EVENT_TYPES and ev.vendor == "pagerduty":
            # Unknown PD event type (e.g. webhook test events): recorded,
            # counted, never evaluated.
            self.store.counters["unknown_event_type"] += 1
            self.store.record(ShadowObservation(
                observation_id=f"sh-obs-{uuid.uuid4().hex[:12]}",
                received_at=_utcnow_iso(), vendor=ev.vendor,
                event_type=ev.event_type, event_id=ev.event_id,
                episode_key=ep.key, human_disposition="unknown",
                gate_would="not_evaluated", gate_confidence=None,
                gate_reason=None, gate_latency_ms=None,
                estate_severity=ev.estate_severity, evidence={},
                threshold_counterfactual={}))
            return
        if not ev.gate_relevant:
            self.store.record(ShadowObservation(
                observation_id=f"sh-obs-{uuid.uuid4().hex[:12]}",
                received_at=_utcnow_iso(), vendor=ev.vendor,
                event_type=ev.event_type, event_id=ev.event_id,
                episode_key=ep.key,
                human_disposition=self._human_disposition(ep),
                gate_would="not_evaluated", gate_confidence=None,
                gate_reason=None, gate_latency_ms=None,
                estate_severity=ep.final_severity, evidence={},
                threshold_counterfactual={}))
            return
        disp, rec, detail = self._evaluate(ev)
        probs, conf3, q3_choice = (detail["probs"], detail["conf3"],
                                   detail["q3_choice"])
        in_allowlist = self._fingerprint_of(ev) in self.allowlist
        obs = ShadowObservation(
            observation_id=f"sh-obs-{uuid.uuid4().hex[:12]}",
            received_at=_utcnow_iso(), vendor=ev.vendor,
            event_type=ev.event_type, event_id=ev.event_id,
            episode_key=ep.key,
            human_disposition=self._human_disposition(ep),
            gate_would=rec.disposition.action,   # the WOULD-BE verdict
            gate_confidence=rec.disposition.confidence,
            gate_reason=rec.disposition.reason,   # "shadow"
            gate_latency_ms=rec.disposition.latency_ms,
            estate_severity=ep.final_severity,
            evidence={
                "jev_model": rec.jev_model,
                "severity_probs": probs,
                "disposition_choice": q3_choice,
                "disposition_confidence": conf3,
                "allowlist_member": in_allowlist,
                "correlation_kind": getattr(self._last_corr, "kind", None),
                "incident_url": ev.incident_url,
            },
            threshold_counterfactual=self._shadow_counterfactual(
                ev, rec, in_allowlist),
        )
        self.store.record(obs)
        self._emit_shadow_decision(ep, ev, obs)

    def _evaluate(self, ev: VendorEvent):
        """Run the gate in shadow mode: verdict recorded, never executed.

        The correlator's duplicate inheritance propagates the WOULD-BE
        verdict, not the shell: the tap's job is "what would the gate
        have done", so note_disposition stores rec.disposition (the
        would-be). Evidence/counterfactual inputs are cached per
        fingerprint so duplicates reuse the original evaluation.
        """
        alert = alert_from_vendor_event(ev)
        state = build_state(alert, history={}, context={})
        corr = self.correlator.ingest(alert)
        self._last_corr = corr
        if corr.kind == "storm" and corr.storm_declared:
            # D3: the shadow mirror takes the digest disposition too. The
            # storm-declared aggregate's honest would-be is 'paged via the
            # aggregate' — routing it through gate.evaluate would let the
            # race + triple lock 'suppress' it in shadow while production
            # pages via digest_storm. A lying mirror in the over-trust
            # direction. Recorded, not executed.
            return self._evaluate_storm_digest(alert, state, corr)
        disp, rec = self.gate.evaluate(alert, state, {}, {}, correlation=corr)
        # rec.disposition is the would-be verdict in shadow mode.
        self.correlator.note_disposition(alert.fingerprint, rec.disposition)
        is_dup = corr.kind == "duplicate" and corr.prior is not None
        if not is_dup:
            probs, conf3, q3_choice = _answers_of(rec)
            self._eval_cache[alert.fingerprint] = {
                "probs": probs, "conf3": conf3, "q3_choice": q3_choice,
                # DR-26: the real kernel's inputs for the counterfactual —
                # duplicates reuse the ORIGINAL evaluation's inputs, so the
                # what-if answers from the first evaluation, not a
                # re-derivation.
                "alert": alert, "q_severity": rec.q_severity,
                "q_team": rec.q_team, "q_disposition": rec.q_disposition}
            detail = {"probs": probs, "conf3": conf3, "q3_choice": q3_choice,
                      "from_cache": False}
        else:
            cached = self._eval_cache.get(alert.fingerprint, {})
            detail = {"probs": cached.get("probs", {}),
                      "conf3": cached.get("conf3"),
                      "q3_choice": cached.get("q3_choice"),
                      "from_cache": True}
        return disp, rec, detail

    def _shadow_counterfactual(self, ev: VendorEvent, rec,
                               in_allowlist: bool) -> dict:
        """The tap's counterfactual through the REAL kernel (DR-26).

        Reuses the ORIGINAL evaluation's kernel inputs from the eval cache
        (duplicates answer from the first evaluation, not a re-derivation).
        Empty when the path consulted no Jev answers (storm digest,
        unknown event types) — honest, never invented.

        ``prob_lock_pass`` is recomputed through the gate's own
        ``_leg1_prob_lock`` with the same inputs the shadow evaluation
        used (same alert, same reported p1) — the same function, not a
        second implementation.
        """
        cached = self._eval_cache.get(self._fingerprint_of(ev), {})
        alert = cached.get("alert")
        q_sev = cached.get("q_severity")
        if alert is None or q_sev is None:
            return {}
        p1 = float((cached.get("probs") or {}).get("p1_critical", 0.0))
        return threshold_counterfactual(
            alert, q_sev, cached.get("q_team"), cached.get("q_disposition"),
            self.gate.thresholds, self.config.counterfactual_presets,
            jev_model=rec.jev_model,
            prob_lock_pass=self.gate._leg1_prob_lock(p1, alert, {}),
            in_allowlist=in_allowlist,
            freshness_report=self.gate._freshness_report())

    def _evaluate_storm_digest(self, alert: Alert, state: dict, corr):
        """Shadow mirror of the live digest path (D3, Tripwire probe).

        The live pipeline routes storm-declared aggregates to
        ``Gate.digest_storm`` — never ``evaluate_policy`` — so suppress is
        unreachable by construction. The shadow tap must record the same
        would-be: ``page_now`` via digest. Recorded, not executed, mirroring
        exactly how ``Gate.evaluate()`` wraps would-be verdicts in shadow
        mode (would-be action kept, reason "shadow", returned disposition
        forced to passthrough).

        Deliberately disposition-only: the live digest's advisory Jev call
        and decision_made emission are execution-side effects — the shadow
        tap records what WOULD have been decided, nothing else.
        """
        would_be = storm_digest_disposition(
            storm_size=sum(corr.storm_counts.values()),
            storm_counts=dict(corr.storm_counts))
        audit_disp = Disposition(
            action=would_be.action, reason="shadow", team=would_be.team,
            confidence=would_be.confidence, latency_ms=would_be.latency_ms)
        disp = Disposition(
            action="passthrough", reason="shadow", team=would_be.team,
            confidence=would_be.confidence, latency_ms=would_be.latency_ms)
        rec = DecisionRecord(
            alert=alert,
            input_sha256=input_sha256(state),
            jev_model=None,
            q_severity=None,
            q_team=None,
            q_disposition=None,
            disposition=audit_disp,
        )
        try:
            self.gate.audit.record(rec)
        except Exception as exc:  # audit must never sink the alert
            print(f"[sentinel] shadow audit write failed for "
                  f"{alert.alert_id}: {exc}", file=sys.stderr)
        # The correlator's duplicate inheritance propagates the WOULD-BE
        # verdict, not the shell — same as the evaluate() path above.
        self.correlator.note_disposition(alert.fingerprint, rec.disposition)
        # No Jev answers were consulted on the digest path: the digest is
        # deterministic. Evidence/counterfactual inputs stay empty rather
        # than invented (honesty contract — W1).
        detail = {"probs": {}, "conf3": None, "q3_choice": None,
                  "from_cache": False}
        return disp, rec, detail

    def _fingerprint_of(self, ev: VendorEvent) -> str:
        return alert_from_vendor_event(ev).fingerprint

    def _emit_shadow_decision(self, ep: ShadowEpisode, ev: VendorEvent,
                              obs: ShadowObservation) -> None:
        """Forward a shadow_decision event to the event log IF the
        coordinator wired an event sink. The tap never depends on it.

        The call matches the EventLog.append_event contract exactly:
        positional event_type, everything else keyword-only; actor is
        always "engine"; "shadow_decision" bodies carry "links" per the
        event-log schema.
        """
        if self.event_sink is None:
            return
        alert = alert_from_vendor_event(ev)
        try:
            self.event_sink.append_event(
                "shadow_decision",
                actor="engine",
                alert_id=alert.alert_id,
                fingerprint=alert.fingerprint,
                episode_id=ep.key,
                body={"would": obs.gate_would,
                      "confidence": obs.gate_confidence,
                      "threshold_counterfactual": obs.threshold_counterfactual,
                      "links": {"incident_url": ev.incident_url,
                                "observation_id": obs.observation_id}},
            )
        except Exception as exc:  # the sink must never sink the tap
            print(f"[sentinel] shadow event-sink write failed: {exc}",
                  file=sys.stderr)

    @staticmethod
    def _human_disposition(ep: ShadowEpisode) -> str:
        if ep.human_paged:
            return "paged"
        if ep.human_resolved or ep.human_acknowledged:
            return "unpaged"
        return "unknown"

    @staticmethod
    def _has_details(ev: VendorEvent) -> bool:
        # Payload-completeness line (design 06 §a.2): the onboarding
        # runbook sets "add description/details"; drift shows up here.
        raw = ev.raw
        if ev.vendor == "opsgenie":
            alert = raw.get("alert") if isinstance(raw.get("alert"), dict) else raw
            return bool(alert.get("description") or alert.get("details"))
        if ev.vendor == "alertmanager":
            alert = raw.get("alert") or {}
            return bool(alert.get("annotations"))
        return True  # PD v3 incident payloads are self-describing


def _answers_of(rec):
    """Extract (probs, conf3, q3_choice) from a DecisionRecord."""
    probs, conf3, q3_choice = {}, None, None
    q1 = getattr(rec, "q_severity", None)
    q3 = getattr(rec, "q_disposition", None)
    if q1 is not None and getattr(q1, "probabilities", None):
        probs = dict(q1.probabilities)
    if q3 is not None:
        conf3 = getattr(q3, "confidence", None)
        q3_choice = getattr(q3, "choice", None)
    return probs, conf3, q3_choice


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
