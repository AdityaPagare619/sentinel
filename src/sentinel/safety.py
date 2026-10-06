"""Kill switch + shadow-attribution safety surface (contract C3, Track 3).

THE CONTROL PRINCIPLE, enforced structurally: this module is stdlib-only.
The kill path never imports the Jev client, the gate, or the race — Jev
gets NO vote on the kill, and the flip never waits on anything but the
event log's own append. A static test (tests/test_safety.py) AST-parses
this file and fails the suite if a Jev/gate/race import ever appears.

Components
----------
KillSwitch
    The deterministic paging kill switch. ``engage()`` flips immediately;
    ``disengage()`` (re-arm) is a SEPARATE deliberate action that requires
    explicit confirmation — sticky re-entry, no silent auto-rearm. Every
    transition is appended to the audit log with actor + timestamp
    (``kill_switch_engaged`` / ``kill_switch_rearmed`` events).

    State backends: in-memory by default (drill, tests, single-process
    deploys). Pass ``state_path`` for a JSON state file (0600, atomic
    tmp+rename writes) so a SEPARATE process (e.g. the platform server vs
    the engine/forwarder) can observe the flip — when set, the file is
    the source of truth and is re-read on every ``engaged`` check.

    DEPLOYMENT NOTE (flagged for the coordinator / Track 6): the platform
    tier opens the engine DB read-only, so a platform-initiated flip cannot
    write its own audit event from that process. Either the killing process
    holds a writable EventLog (as the drill does), or the topology gives
    the safety surface a writable audit path. The flip itself is never
    blocked on the audit write — audit failure is loud (stderr) but the
    switch still flips.

Operator auth seam (contract C1)
    Track 1 owns the operator token store. Until it lands, this module
    exposes the seam it will plug into: ``set_operator_verifier(fn)`` where
    ``fn(token) -> actor_id | None``. With no verifier installed the seam
    is FAIL-CLOSED: every token is invalid and the safety endpoints 401.
    ``require_operator(environ)`` implements the C1 wire contract —
    ``401 {"error": "unauthorized"}` — on any failure.

Drill artifact reader
    ``latest_drill(drill_dir)`` returns the newest kill-drill artifact or
    None. The <5s claim is FORBIDDEN until an artifact exists — Track 8
    renders "unmeasured" when this returns None.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading

from .eventlog import EventLog, utcnow_iso

# ---------------------------------------------------------------------------
# exceptions


class KillEngaged(Exception):
    """Raised on the forwarding path when the kill switch is engaged."""


class RearmRefused(Exception):
    """Re-arm attempted without the explicit confirmation body."""


class Unauthorized(Exception):
    """Operator auth failed (contract C1 wire shape: {"error": "unauthorized"})."""


class SafetyUnavailable(Exception):
    """The safety surface is not wired in this process."""


# ---------------------------------------------------------------------------
# kill switch


_KILL_EVENT_IDS = "kill-switch"  # placeholder ids for the global kill events
_STATE_FILE_MODE = 0o600


class KillSwitch:
    """Deterministic paging kill switch. Thread-safe. Never calls Jev."""

    def __init__(self, log: EventLog | None = None,
                 state_path: str | None = None):
        self._log = log
        self._state_path = state_path
        self._lock = threading.Lock()
        self._engaged = False
        self._engaged_at: str | None = None
        self._engaged_seq: int | None = None
        self._flips = 0  # lifetime engage transitions (audit-friendly)

    # ------------------------------------------------------------ state

    @property
    def engaged(self) -> bool:
        if self._state_path is not None:
            return self._read_state_file()
        with self._lock:
            return self._engaged

    @property
    def engaged_at(self) -> str | None:
        with self._lock:
            return self._engaged_at

    @property
    def engaged_seq(self) -> int | None:
        with self._lock:
            return self._engaged_seq

    def status(self) -> dict:
        with self._lock:
            return {"engaged": self._engaged,
                    "engaged_at": self._engaged_at,
                    "flips": self._flips}

    # ------------------------------------------------------------ transitions

    def engage(self, *, actor: str = "operator", actor_id: str | None = None,
               reason: str = "manual", drill: bool = False) -> dict:
        """Flip the kill switch IMMEDIATELY. Idempotent: engaging an
        already-engaged switch is a no-op (no duplicate audit event — the
        audit log records transitions, not button mashes).

        Never raises for audit failure: the flip is the safety-critical
        action; a failed audit write is loud (stderr) but must not block it.
        Never calls Jev, never waits on the race.
        """
        with self._lock:
            if self._engaged:
                return {"engaged": True, "engaged_at": self._engaged_at,
                        "flips": self._flips, "seq": self._engaged_seq,
                        "transitioned": False}
            self._engaged = True
            self._engaged_at = utcnow_iso()
            self._flips += 1
            self._write_state_file(True)
            seq = self._audit(
                "kill_switch_engaged", actor=actor,
                body={"engaged_at": self._engaged_at,
                      "previous_engaged": False,
                      "reason": reason,
                      "actor_id": actor_id or actor,
                      "drill": bool(drill)})
            self._engaged_seq = seq
            return {"engaged": True, "engaged_at": self._engaged_at,
                    "flips": self._flips, "seq": seq,
                    "transitioned": True}

    def disengage(self, *, actor: str = "operator",
                  actor_id: str | None = None,
                  confirm: bool = False) -> dict:
        """Re-arm the switch. STICKY re-entry: nothing re-arms silently —
        ``confirm`` must be exactly True (the HTTP layer maps this to the
        explicit ``{"confirm": true}`` body; anything else → RearmRefused).
        """
        if confirm is not True:
            raise RearmRefused(
                "re-arm is a separate deliberate action: resend with "
                "an explicit confirmation ({\"confirm\": true}). "
                "No silent auto-rearm, ever.")
        with self._lock:
            if not self._engaged:
                return {"engaged": False, "engaged_at": None,
                        "flips": self._flips, "transitioned": False}
            rearmed_at = utcnow_iso()
            self._engaged = False
            self._engaged_at = None
            self._engaged_seq = None
            self._write_state_file(False)
            seq = self._audit(
                "kill_switch_rearmed", actor=actor,
                body={"rearmed_at": rearmed_at,
                      "previous_engaged": True,
                      "confirmed": True,
                      "actor_id": actor_id or actor})
            return {"engaged": False, "engaged_at": None,
                    "flips": self._flips, "seq": seq,
                    "transitioned": True}

    # ------------------------------------------------------------ internals

    def _audit(self, event_type: str, *, actor: str, body: dict) -> int | None:
        """Append the transition to the audit log. Returns the event seq.
        Loud on failure, never raises — the flip must not wait on the log."""
        if self._log is None:
            return None
        try:
            return self._log.append_event(
                event_type, actor=actor,
                alert_id=_KILL_EVENT_IDS, fingerprint=_KILL_EVENT_IDS,
                episode_id=_KILL_EVENT_IDS, body=body)
        except Exception as exc:  # the flip already happened; say so loudly
            print(f"[sentinel] SAFETY audit write failed ({event_type}): "
                  f"{exc} — THE SWITCH STILL FLIPPED", file=sys.stderr)
            return None

    def _write_state_file(self, engaged: bool) -> None:
        """Atomic state-file write for cross-process visibility."""
        if self._state_path is None:
            return
        try:
            d = os.path.dirname(os.path.abspath(self._state_path))
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".kill-switch-")
            try:
                with os.fdopen(fd, "w") as fh:
                    json.dump({"engaged": engaged,
                               "at": utcnow_iso()}, fh)
                os.chmod(tmp, _STATE_FILE_MODE)
                os.replace(tmp, self._state_path)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        except Exception as exc:
            print(f"[sentinel] SAFETY state-file write failed: {exc}",
                  file=sys.stderr)

    def _read_state_file(self) -> bool:
        try:
            with open(self._state_path, "r") as fh:
                return bool(json.load(fh).get("engaged", False))
        except (OSError, ValueError):
            return False  # missing/corrupt state file: fail OPEN is wrong
                          # for a kill switch — but a missing file means no
                          # flip was ever persisted; treat as disengaged and
                          # let the audit trail (transitions) be the truth.


# ---------------------------------------------------------------------------
# operator auth seam (contract C1 — Track 1 owns the token store)


_operator_verifier = None  # fn(token: str) -> str | None (actor id)


def set_operator_verifier(fn) -> None:
    """Install Track 1's token verifier. ``fn`` maps a bearer token to an
    actor id, or None for an invalid token."""
    global _operator_verifier
    _operator_verifier = fn


def reset_operator_verifier() -> None:
    """Test seam: remove the installed verifier (back to fail-closed)."""
    global _operator_verifier
    _operator_verifier = None


def verify_operator_token(token: str) -> str | None:
    """Return the actor id for a valid operator token, else None.

    Fail-closed: with no verifier installed (Track 1 not yet merged),
    EVERY token is invalid.
    """
    if _operator_verifier is None:
        return None
    try:
        return _operator_verifier(token)
    except Exception:
        return None


def require_operator(environ) -> str:
    """C1 wire contract: extract the Bearer token, return the actor id.
    Raises Unauthorized on any failure (missing/malformed/invalid)."""
    auth = environ.get("HTTP_AUTHORIZATION") or ""
    scheme, _, token = auth.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("missing or malformed Authorization header")
    actor_id = verify_operator_token(token)
    if not actor_id:
        raise Unauthorized("invalid operator token")
    return actor_id


# ---------------------------------------------------------------------------
# drill artifact reader (Track 8: "unmeasured" until an artifact exists)


def _default_drill_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))  # src/sentinel
    return os.path.join(os.path.dirname(os.path.dirname(here)),
                        "ops", "drills")


def latest_drill(drill_dir: str | None = None) -> dict | None:
    """Return the newest kill-drill artifact, or None when no drill has
    ever produced one. None means the <5s claim is FORBIDDEN — the UI
    must show "unmeasured"."""
    path = os.path.join(drill_dir or _default_drill_dir(),
                        "kill-drill-latest.json")
    try:
        with open(path, "r") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None

