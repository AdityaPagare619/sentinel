"""Bring-your-own-keys: operator-provided PagerDuty + Jev credentials.

Aditya's BYOK directive (2026-10-04): Sentinel never ships with paging
credentials. The operator provides their own PagerDuty routing key through
the Integrations settings surface; the Jev key defaults to the platform key
with a per-user override. Showcase paths may run simulated — LABELED, never
silent (honesty law P3).

Security properties (see PR body for the full threat model):
  - Keys at rest: 600-perm JSON file under $SENTINEL_STATE_DIR (default
    ./sentinel-state/integrations.json). Never in git (state dir is
    gitignored), never in the demo dataset, never in API responses.
  - Keys in transit: only to PagerDuty's Events API (TLS) at send time.
    Never in logs, audit events, event-log rows, error messages, or traces.
  - Ephemeral mode: when the state dir is not writable (serverless), the
    store is memory-only and says so — writes fail LOUD (501), never
    silently pretend to persist.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile

PD_KEY_NAME = "pagerduty_routing_key"
JEV_KEY_NAME = "jev_api_key"
KNOWN_KEYS = (PD_KEY_NAME, JEV_KEY_NAME)

ENV_INTEGRATIONS_FILE = "SENTINEL_INTEGRATIONS_FILE"
ENV_STATE_DIR = "SENTINEL_STATE_DIR"
ENV_SIMULATED = "SENTINEL_SIMULATED_PAGING"
ENV_PD_ROUTING_KEY = "PD_ROUTING_KEY"
ENV_JEV_KEY = "TYPESAFE_API_KEY"

# PagerDuty Events API v2 integration keys are 32 lowercase hex chars.
_PD_KEY_RE = re.compile(r"^[0-9a-fA-F]{32}$")


class EphemeralStoreError(RuntimeError):
    """Raised when a write is attempted on an ephemeral (memory-only) store."""


def _default_path() -> str:
    state_dir = os.environ.get(ENV_STATE_DIR, "./sentinel-state")
    return os.path.join(state_dir, "integrations.json")


def validate_routing_key(value: str) -> str:
    """Validate a PagerDuty Events API v2 routing key. Returns it stripped.

    Operator-language errors: the message says what's wrong and where the
    key comes from, never echoes the value.
    """
    v = (value or "").strip()
    if not _PD_KEY_RE.fullmatch(v):
        raise ValueError(
            "that doesn't look like a PagerDuty Events API v2 routing key "
            "(32 hexadecimal characters — find it under your PagerDuty "
            "service's Integrations tab, Events API v2).")
    return v


def validate_jev_key(value: str) -> str:
    v = (value or "").strip()
    if len(v) < 8:
        raise ValueError("Jev API key looks too short — check for a copy/paste slip.")
    return v


_VALIDATORS = {PD_KEY_NAME: validate_routing_key, JEV_KEY_NAME: validate_jev_key}


class IntegrationStore:
    """Server-side key storage. Values are write-only: `status()` reports
    configured/last4 only. Reads of values happen exclusively inside the
    process that needs them (forwarder, receiver)."""

    def __init__(self, path: str | None = None, ephemeral: bool = False):
        self.path = path or os.environ.get(ENV_INTEGRATIONS_FILE) or _default_path()
        self._ephemeral = ephemeral
        self._mem: dict = {}
        if not ephemeral:
            try:
                parent = os.path.dirname(os.path.abspath(self.path))
                os.makedirs(parent, exist_ok=True)
                # Prove writability — a read-only fs (serverless) must not
                # silently become a black hole for keys.
                fd, tmp = tempfile.mkstemp(dir=parent, prefix=".wtest")
                os.close(fd)
                os.unlink(tmp)
            except OSError:
                self._ephemeral = True

    @property
    def ephemeral(self) -> bool:
        return self._ephemeral

    # ------------------------------------------------------------ internals
    def _read(self) -> dict:
        if self._ephemeral:
            return dict(self._mem)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        if self._ephemeral:
            raise EphemeralStoreError(
                "key storage is ephemeral here (no writable state dir) — "
                "keys cannot persist across restarts")
        parent = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".integrations")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"))
                f.write("\n")
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------ API
    def get(self, name: str) -> str | None:
        """Return the stored value. Callers must never log/emit it."""
        if name not in KNOWN_KEYS:
            raise KeyError(f"unknown integration key {name!r}")
        v = self._read().get(name)
        return v if isinstance(v, str) and v else None

    def set(self, name: str, value: str) -> dict:
        """Validate + persist. Returns the public status (never the value)."""
        if name not in KNOWN_KEYS:
            raise KeyError(f"unknown integration key {name!r}")
        clean = _VALIDATORS[name](value)
        data = self._read()
        data[name] = clean
        self._write(data)
        return self._public(name, clean)

    def set_many(self, items: dict) -> dict:
        """Validate ALL, then persist ALL. Never partially persists: a bad
        second key must not leave the first one saved (Vault, PR #77)."""
        cleaned = {}
        for name, value in items.items():
            if name not in KNOWN_KEYS:
                raise KeyError(f"unknown integration key {name!r}")
            cleaned[name] = _VALIDATORS[name](value)
        data = self._read()
        data.update(cleaned)
        self._write(data)
        return {name: self._public(name, clean)
                for name, clean in cleaned.items()}

    def delete(self, name: str) -> None:
        if name not in KNOWN_KEYS:
            raise KeyError(f"unknown integration key {name!r}")
        data = self._read()
        data.pop(name, None)
        self._write(data)

    def get_flag(self, name: str) -> bool:
        return bool(self._read().get(name, False))

    def set_flag(self, name: str, value: bool) -> None:
        data = self._read()
        data[name] = bool(value)
        self._write(data)

    def status(self) -> dict:
        """Public status — safe for API responses. NEVER includes values."""
        out = {}
        for name in KNOWN_KEYS:
            out[name] = self._public(name, self.get(name))
        out["simulated_paging"] = simulated_paging(self)
        out["ephemeral"] = self._ephemeral
        return out

    @staticmethod
    def _public(name: str, value: str | None) -> dict:
        if not value:
            return {"configured": False, "last4": None}
        return {"configured": True, "last4": value[-4:]}


# ---------------------------------------------------------------------------
# resolution: user store → env → unconfigured (the source is safe to log)


def resolve_paging_key(store: IntegrationStore | None = None) -> tuple[str | None, str]:
    """Resolve the PagerDuty routing key per decision.

    Returns (key_or_None, source) where source ∈ {"user","env","unconfigured"}.
    The source is safe for logs/metrics; the key is NEVER logged.
    """
    store = store or IntegrationStore()
    try:
        key = store.get(PD_KEY_NAME)
    except Exception:
        key = None  # a broken store must not break paging resolution
    if key:
        return key, "user"
    env = os.environ.get(ENV_PD_ROUTING_KEY)
    if env:
        return env, "env"
    return None, "unconfigured"


def paging_key_source(store: IntegrationStore | None = None) -> str:
    """Which paging key SOURCE applies — without resolving the value.

    Mirrors :func:`resolve_paging_key`'s precedence (user store → env →
    unconfigured) but never returns the secret value: use it wherever a log
    line or honest-label needs the source. A simulated page needs no key at
    all (that is the point of the showcase), so simulated paths must never
    resolve one — this helper is how the code keeps the C6 contract's
    "never resolves a routing key" claim mechanically true.
    """
    store = store or IntegrationStore()
    try:
        if store.get(PD_KEY_NAME):
            return "user"
    except Exception:
        pass  # a broken store must not break source reporting either
    if os.environ.get(ENV_PD_ROUTING_KEY):
        return "env"
    return "unconfigured"


def resolve_jev_key(store: IntegrationStore | None = None) -> tuple[str | None, str]:
    store = store or IntegrationStore()
    try:
        key = store.get(JEV_KEY_NAME)
    except Exception:
        key = None
    if key:
        return key, "user"
    env = os.environ.get(ENV_JEV_KEY)
    if env:
        return env, "env"
    return None, "unconfigured"


def simulated_paging(store: IntegrationStore | None = None) -> bool:
    """Explicit simulated mode. Env forces it on (Vercel demo default);
    otherwise the stored toggle (set in Integrations UI) applies."""
    if os.environ.get(ENV_SIMULATED, "0") == "1":
        return True
    store = store or IntegrationStore()
    try:
        return bool(store.get_flag("simulated_paging"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# error sanitization: the ONE wired-in emission boundary guard.
#
# The deleted redact()/scrub_record()/known_secrets() helpers (PR #77 review)
# had zero production callers — advertised defense-in-depth that didn't exist.
# This replaces them with a single function that IS called, on every forward
# failure path: the forwarder knows the configured key values, so it strips
# those exact values from error strings before they reach stderr/results.
# Realistic transport exceptions never carry the POST body (the key lives in
# the body, not the URL); this covers the hostile/synthetic case too.


def sanitize_error(text: str) -> str:
    """Remove known key VALUES from an error string before emission."""
    out = text if isinstance(text, str) else str(text)
    for secret in _known_key_values():
        if secret and isinstance(secret, str) and len(secret) >= 8:
            out = out.replace(secret, "[REDACTED]")
    return out


def _known_key_values() -> list:
    """Configured key values from store + env. Handle like secrets: the
    returned list is used for replacement only and never logged."""
    vals = []
    try:
        store = IntegrationStore()
        for name in KNOWN_KEYS:
            try:
                v = store.get(name)
            except Exception:
                v = None
            if v:
                vals.append(v)
    except Exception:
        pass
    for env_name in (ENV_PD_ROUTING_KEY, ENV_JEV_KEY):
        v = os.environ.get(env_name)
        if v:
            vals.append(v)
    return vals
