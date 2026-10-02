"""Core data shapes — frozen contract §3.2 of ARCHITECTURE.md."""

from __future__ import annotations

from dataclasses import dataclass, field

from .client import Answer


@dataclass
class Alert:
    alert_id: str
    received_at: str  # ISO-8601
    fingerprint: str  # hash(service, check, severity_in, region)
    service: str
    check: str
    severity_in: str  # source severity label
    title: str
    source: str  # "pagerduty" | "alertmanager" | "generic"
    labels: dict[str, str] = field(default_factory=dict)  # env, region, cluster...
    metric_value: float | None = None
    metric_threshold: float | None = None
    breach_duration_s: int | None = None
    raw: dict = field(default_factory=dict)  # original payload, retained


@dataclass
class Disposition:
    action: str  # "page_now" | "page_business_hours" | "suppress" | "passthrough"
    reason: str  # "threshold" | "allowlist" | "uncertain" | "shadow" |
                 # "dedup" | "change_window" | "storm" | "error:<code>"
    team: str | None
    confidence: float | None
    latency_ms: float


@dataclass
class DecisionRecord:  # one audit row; mirrors the SQL schema in §7
    alert: Alert
    input_sha256: str
    jev_model: str | None
    q_severity: Answer | None
    q_team: Answer | None
    q_disposition: Answer | None
    disposition: Disposition


@dataclass
class Thresholds:
    suppress_p1_max: float = 0.002  # P(p1_critical) must be below this
    suppress_conf_min: float = 0.90
    page_p1p2_min: float = 0.30  # P(p1)+P(p2) above this -> page_now
    uncertain_conf_max: float = 0.50  # below this -> page_now (uncertainty pages)
    queue_conf_min: float = 0.70
    # tuned per org by tuner.py; serialized to thresholds.json
