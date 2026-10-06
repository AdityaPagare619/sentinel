"""Core data shapes — frozen contract §3.2 of ARCHITECTURE.md."""

from __future__ import annotations

from dataclasses import dataclass, field

from .client import Answer


@dataclass
class Alert:
    alert_id: str
    received_at: str  # ISO-8601
    fingerprint: str  # scheme-v2 hash(service, check, severity_in, region, env, cluster)
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


class ReasonRewriteError(AttributeError):
    """Raised on a second write to Disposition.reason.

    The disposition's ``reason`` is WRITE-ONCE (contract C3): only the
    decisioning path (the gate's policy kernel) may set it, exactly once.
    The shadow path must never rewrite it — it records ``mode="shadow"``
    and carries the causal reason through untouched, via
    ``Disposition.as_shadow()`` / ``as_shadow_shell()``.
    """


@dataclass
class Disposition:
    action: str  # "page_now" | "page_business_hours" | "suppress" | "passthrough" | "folded"
                 # "folded" (D3): storm-continuation absorbed into the aggregate
                 # page — intentionally not forwarded, but NOT suppression.
    reason: str  # WRITE-ONCE. Always the CAUSAL reason: "threshold" |
                 # "allowlist" | "uncertain" | "dedup" | "change_window" |
                 # "storm" | "storm_digest" | "kill_switch" | "flap_debounce" |
                 # "failopen_step1" | ... | "error:<code>". NEVER "shadow" —
                 # shadow is a MODE, not a reason. The shadow path sets
                 # mode="shadow" and leaves reason exactly as the
                 # decisioning path wrote it (contract C3).
    team: str | None
    confidence: float | None
    latency_ms: float
    mode: str = "live"  # "live" | "shadow". Written by the path that executes
                        # (or mirrors) the decision — never by decisioning.

    def __setattr__(self, name, value):
        # Write-once enforcement for reason: the first assignment (inside
        # __init__, from the decisioning path) commits it; any later
        # assignment — e.g. a shadow-path rewrite of reason — raises.
        if name == "reason":
            if self.__dict__.get("_reason_committed", False):
                raise ReasonRewriteError(
                    f"Disposition.reason is write-once (contract C3): refusing "
                    f"to rewrite {self.__dict__.get('reason')!r} as {value!r}. "
                    f"Shadow is a mode, not a reason — use as_shadow().")
            object.__setattr__(self, "_reason_committed", True)
        object.__setattr__(self, name, value)

    def __post_init__(self):
        if self.mode not in ("live", "shadow"):
            raise ValueError(
                f"Disposition.mode must be 'live'|'shadow', got {self.mode!r}")

    def as_shadow(self) -> "Disposition":
        """The shadow path's audit copy: the WOULD-BE verdict, causal reason
        carried through untouched, mode="shadow". The shadow path never
        assigns ``reason`` itself — it goes through here."""
        return Disposition(action=self.action, reason=self.reason,
                           team=self.team, confidence=self.confidence,
                           latency_ms=self.latency_ms, mode="shadow")

    def as_shadow_shell(self) -> "Disposition":
        """The disposition RETURNED to the caller in shadow mode: the action
        is forced to "passthrough" (never executed), the causal reason is
        carried through untouched, mode="shadow"."""
        return Disposition(action="passthrough", reason=self.reason,
                           team=self.team, confidence=self.confidence,
                           latency_ms=self.latency_ms, mode="shadow")


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
    # D9/ADR-023: operator what-if presets for the counterfactual receipt —
    # a tuple of normalized preset dicts (see
    # counterfactual.validate_counterfactual_presets). Empty by default:
    # the receipt always carries the definitional no_suppress_leg.
    counterfactual_presets: tuple = ()
