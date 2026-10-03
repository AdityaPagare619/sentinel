"""Policy lifecycle — the ADR-022 / B3 six-state machine.

draft -> shadow -> canary -> live -> review-due -> expired

This module is pure deterministic code: no I/O, no clocks (callers pass
``now``), no network. The state machine from design/rfc-followups-2026-10-04.md
§B3, adopted by the ADR panel. Type 1: the six states, the sign-off matrix,
the automaticity of live->review-due and review-due->expired, the
content-freeze, the no-expired->live rule.

Enforcement semantics (what the GATE consults, not what the UI shows):
  - can_suppress(policy_id) is True only when a LIVE or REVIEW_DUE version
    exists AND the policy is not frozen by the watchdog.
  - EXPIRED versions never suppress; dispositions fall through to fail-open.
  - REVIEW_DUE versions still suppress but under watchdog tightening
    (see watchdog.py) and the owner gets paged by the lifecycle tick.

Persistence: PolicyStore serializes to a JSON file (the config dir). The
event log carries the audited transition events; this file is the
materialized enforcement view. If the file is unreadable, the gate fails
toward passthrough (never suppress on unknown policy state).
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import os
from enum import Enum


class PolicyState(str, Enum):
    DRAFT = "draft"
    SHADOW = "shadow"
    CANARY = "canary"
    LIVE = "live"
    REVIEW_DUE = "review-due"
    EXPIRED = "expired"


class PolicyTransitionError(Exception):
    """Illegal lifecycle transition or failed sign-off validation."""


# B3.3 sign-off matrix: transition -> (required distinct attestors,
# requires_preview_hash). Attestors must be distinct from the author and
# from each other; the store cannot verify author identity, so callers pass
# author_id and the store enforces distinctness.
_SIGNOFF: dict[tuple[PolicyState, PolicyState], tuple[int, bool]] = {
    (PolicyState.DRAFT, PolicyState.SHADOW): (0, False),   # author only
    (PolicyState.SHADOW, PolicyState.CANARY): (2, True),    # 2 attestors + diff
    (PolicyState.CANARY, PolicyState.LIVE): (2, True),     # 2 attestors + baseline
    (PolicyState.LIVE, PolicyState.REVIEW_DUE): (0, False),  # automatic
    (PolicyState.REVIEW_DUE, PolicyState.LIVE): (2, True),  # re-review
    (PolicyState.REVIEW_DUE, PolicyState.EXPIRED): (0, False),  # automatic/early
    (PolicyState.EXPIRED, PolicyState.DRAFT): (0, False),   # re-draft new version
    # aborts: any -> draft
}
_ABORT_FROM = {PolicyState.SHADOW, PolicyState.CANARY, PolicyState.LIVE,
               PolicyState.REVIEW_DUE}


@dataclasses.dataclass
class PolicyAttestation:
    policy_id: str
    version: int
    from_state: str
    to_state: str
    preview_hash: str | None
    attestor_ids: tuple[str, ...]
    decided_at: str  # RFC3339 UTC

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PolicyAttestation":
        return cls(policy_id=str(d["policy_id"]), version=int(d["version"]),
                   from_state=str(d["from_state"]), to_state=str(d["to_state"]),
                   preview_hash=d.get("preview_hash"),
                   attestor_ids=tuple(d.get("attestor_ids", ())),
                   decided_at=str(d["decided_at"]))


@dataclasses.dataclass
class PolicyVersion:
    policy_id: str
    version: int
    state: str  # PolicyState value
    content_hash: str  # canonical-sha256 of the policy content covered
    created_at: str
    review_at: str | None = None      # live -> review-due at this time
    grace_until: str | None = None    # review-due -> expired at this time
    override_until: str | None = None  # emergency override time-box (B3.4)
    override_from_version: int | None = None  # version to restore on revert
    frozen: bool = False              # watchdog freeze (separate from lifecycle)
    frozen_reason: str | None = None
    attestations: tuple = ()

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["attestations"] = [a.to_dict() if isinstance(a, PolicyAttestation) else a
                             for a in self.attestations]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PolicyVersion":
        atts = tuple(PolicyAttestation.from_dict(a)
                     for a in d.get("attestations", ()))
        return cls(policy_id=str(d["policy_id"]), version=int(d["version"]),
                   state=str(d["state"]), content_hash=str(d["content_hash"]),
                   created_at=str(d["created_at"]),
                   review_at=d.get("review_at"), grace_until=d.get("grace_until"),
                   override_until=d.get("override_until"),
                   override_from_version=d.get("override_from_version"),
                   frozen=bool(d.get("frozen", False)),
                   frozen_reason=d.get("frozen_reason"), attestations=atts)


def _parse_ts(ts: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


class PolicyStore:
    """Holds policy versions, enforces the B3 state machine.

    Pure apart from optional JSON persistence (save/load). tick() performs
    the automatic clock transitions and returns the list of events the
    caller must append to the event log + the pages it must send — the
    store never pages by itself (separation: decision here, effect there).
    """

    # Type 2 defaults (B3.6)
    DEFAULT_REVIEW_DAYS = 30
    DEFAULT_GRACE_DAYS = 14

    def __init__(self, path: str | None = None):
        self.path = path
        # policy_id -> list[PolicyVersion] (ascending version)
        self._policies: dict[str, list[PolicyVersion]] = {}
        if path and os.path.exists(path):
            self.load()

    # ------------------------------------------------------------ persistence
    def save(self) -> None:
        if not self.path:
            return
        data = {pid: [v.to_dict() for v in vs]
                for pid, vs in self._policies.items()}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True)
        os.replace(tmp, self.path)

    def load(self) -> None:
        with open(self.path, encoding="utf-8") as fh:
            data = json.load(fh)
        self._policies = {pid: [PolicyVersion.from_dict(v) for v in vs]
                          for pid, vs in data.items()}

    # ----------------------------------------------------------------- queries
    def versions(self, policy_id: str) -> list[PolicyVersion]:
        return list(self._policies.get(policy_id, []))

    def live_version(self, policy_id: str) -> PolicyVersion | None:
        for v in reversed(self._policies.get(policy_id, [])):
            if v.state == PolicyState.LIVE.value and not v.frozen:
                return v
        return None

    def effective_version(self, policy_id: str) -> PolicyVersion | None:
        """The version whose dispositions are in force (live or review-due)."""
        for v in reversed(self._policies.get(policy_id, [])):
            if v.state in (PolicyState.LIVE.value, PolicyState.REVIEW_DUE.value):
                return v
        return None

    def can_suppress(self, policy_id: str) -> tuple[bool, str]:
        """Enforcement hook for the gate. Returns (allowed, reason)."""
        vs = self._policies.get(policy_id, [])
        if not vs:
            # No policy at all: fail-open loudly, but that is the gate's
            # passthrough default — suppression was never allowed.
            return False, "no_policy"
        v = self.effective_version(policy_id)
        if v is None:
            return False, "policy_expired"
        if v.frozen:
            return False, f"policy_frozen:{v.frozen_reason or 'watchdog'}"
        return True, "ok"

    # --------------------------------------------------------------- mutations
    def create_draft(self, policy_id: str, content: dict,
                     now: _dt.datetime) -> PolicyVersion:
        vs = self._policies.setdefault(policy_id, [])
        version = (max((v.version for v in vs), default=0) + 1)
        content_hash = hashlib.sha256(
            json.dumps(content, sort_keys=True).encode()).hexdigest()
        v = PolicyVersion(policy_id=policy_id, version=version,
                          state=PolicyState.DRAFT.value,
                          content_hash=content_hash,
                          created_at=now.isoformat())
        vs.append(v)
        return v

    def transition(self, policy_id: str, version: int, to_state: str, *,
                   now: _dt.datetime, author_id: str = "",
                   attestations: list[PolicyAttestation] | None = None,
                   preview_hash: str | None = None,
                   emergency: bool = False,
                   override_hours: float = 24.0) -> PolicyVersion:
        """Move a version between states, enforcing the B3.3 sign-off matrix."""
        v = self._get(policy_id, version)
        frm = PolicyState(v.state)
        to = PolicyState(to_state)

        if emergency:
            v = self._emergency_override(v, now, author_id,
                                         hours=override_hours)
            return v

        # abort path: any non-terminal -> draft
        if to == PolicyState.DRAFT and frm in _ABORT_FROM:
            v.state = to.value
            return v

        key = (frm, to)
        if key not in _SIGNOFF:
            raise PolicyTransitionError(
                f"illegal transition {frm.value} -> {to.value}")
        required_attestors, needs_preview = _SIGNOFF[key]

        atts = list(attestations or [])
        if len(atts) < required_attestors:
            raise PolicyTransitionError(
                f"{frm.value} -> {to.value} requires {required_attestors} "
                f"attestors, got {len(atts)}")
        if required_attestors:
            ids = [a for att in atts for a in att.attestor_ids]
            if len(set(ids)) < required_attestors:
                raise PolicyTransitionError(
                    "attestors must be distinct individuals")
            if author_id and author_id in set(ids):
                raise PolicyTransitionError(
                    "attestors must be distinct from the author")
            for att in atts:
                if (att.policy_id != policy_id or att.version != version
                        or att.from_state != frm.value or att.to_state != to.value):
                    raise PolicyTransitionError(
                        "attestation does not match this transition")
        if needs_preview and not preview_hash:
            raise PolicyTransitionError(
                f"{frm.value} -> {to.value} requires a signed preview hash")

        # canary -> live signs the watchdog baseline (B3.3): the caller must
        # have recorded it; we require the preview hash as its pointer.
        v.state = to.value
        v.attestations = tuple(list(v.attestations) + atts)
        if to == PolicyState.LIVE:
            # Content freeze (B3.5a): the live version is immutable from here.
            # review_at starts the 30-day clock (Type 2 default).
            review_at = now + _dt.timedelta(days=self.DEFAULT_REVIEW_DAYS)
            v.review_at = review_at.isoformat()
            v.grace_until = (review_at + _dt.timedelta(
                days=self.DEFAULT_GRACE_DAYS)).isoformat()
        return v

    def _emergency_override(self, v: PolicyVersion, now: _dt.datetime,
                            approver_id: str, hours: float) -> PolicyVersion:
        """B3.4: draft -> live directly, time-boxed, auto-reverting."""
        if PolicyState(v.state) != PolicyState.DRAFT:
            raise PolicyTransitionError(
                "emergency override only applies draft -> live")
        if not approver_id:
            raise PolicyTransitionError(
                "emergency override requires a named approver")
        if hours <= 0 or hours > 24:
            raise PolicyTransitionError(
                "override time-box must be within (0, 24h]")
        # Freeze the currently-live version so the tick can restore it.
        prev = self.effective_version(v.policy_id)
        v.override_from_version = prev.version if prev else None
        v.override_until = (now + _dt.timedelta(hours=hours)).isoformat()
        v.state = PolicyState.LIVE.value
        v.review_at = (now + _dt.timedelta(hours=hours)).isoformat()
        v.grace_until = v.review_at
        return v

    def freeze(self, policy_id: str, reason: str) -> PolicyVersion | None:
        """Watchdog freeze: the effective version stops suppressing. Now."""
        v = self.effective_version(policy_id)
        if v is None:
            return None
        v.frozen = True
        v.frozen_reason = reason
        return v

    def unfreeze(self, policy_id: str, version: int,
                 attestations: list[PolicyAttestation]) -> PolicyVersion:
        """Un-freezing re-enables suppression: requires 2 attestors."""
        v = self._get(policy_id, version)
        if not v.frozen:
            raise PolicyTransitionError("policy is not frozen")
        ids = {a for att in attestations for a in att.attestor_ids}
        if len(ids) < 2:
            raise PolicyTransitionError(
                "unfreeze requires 2 distinct attestors")
        v.frozen = False
        v.frozen_reason = None
        v.attestations = tuple(list(v.attestations) + list(attestations))
        return v

    def tick(self, now: _dt.datetime) -> list[dict]:
        """Automatic clock transitions. Returns event dicts for the caller
        to append to the event log and pages to send. Pure apart from save."""
        emitted: list[dict] = []
        for pid, vs in self._policies.items():
            for v in vs:
                st = PolicyState(v.state)
                # emergency override auto-revert (B3.4)
                if (v.override_until and st == PolicyState.LIVE
                        and _parse_ts(v.override_until) <= now):
                    prev_id = v.override_from_version
                    v.state = PolicyState.EXPIRED.value
                    v.override_until = None
                    emitted.append({
                        "type": "policy_override_reverted",
                        "policy_id": pid, "version": v.version,
                        "restored_version": prev_id,
                        "page": True,
                        "summary": (f"emergency override v{v.version} auto-"
                                    f"reverted; retro-sign missing"),
                    })
                    continue
                if st == PolicyState.LIVE and v.review_at \
                        and _parse_ts(v.review_at) <= now:
                    v.state = PolicyState.REVIEW_DUE.value
                    emitted.append({
                        "type": "policy_review_due",
                        "policy_id": pid, "version": v.version,
                        "page_owner": True,
                        "summary": (f"policy {pid} v{v.version} is review-due; "
                                    f"watchdog tightening engaged"),
                    })
                elif st == PolicyState.REVIEW_DUE and v.grace_until \
                        and _parse_ts(v.grace_until) <= now:
                    v.state = PolicyState.EXPIRED.value
                    emitted.append({
                        "type": "policy_expired",
                        "policy_id": pid, "version": v.version,
                        "page_owner": True,
                        "fail_loud": True,
                        "summary": (f"policy {pid} v{v.version} expired; "
                                    f"dispositions fall through to fail-open"),
                    })
        return emitted

    # ------------------------------------------------------------------ internals
    def _get(self, policy_id: str, version: int) -> PolicyVersion:
        for v in self._policies.get(policy_id, []):
            if v.version == version:
                return v
        raise PolicyTransitionError(
            f"unknown policy version {policy_id} v{version}")
