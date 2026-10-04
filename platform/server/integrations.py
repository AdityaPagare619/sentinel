"""Integrations key store — platform-tier twin of sentinel/integrations.py.

The platform tier never imports the engine package (tier decoupling), so
this is a deliberate, minimal twin: same file format
($SENTINEL_STATE_DIR/integrations.json, 600 perms), same validation, same
ephemeral detection. If the format ever changes, change BOTH modules and
note it in the PR — the format contract is the shared surface.

What this module does NOT do: resolve keys for the paging path (that's the
engine's job), or echo values (status() reports configured/last4 only).
"""

from __future__ import annotations

import json
import os
import re
import tempfile

PD_KEY_NAME = "pagerduty_routing_key"
JEV_KEY_NAME = "jev_api_key"
KNOWN_KEYS = (PD_KEY_NAME, JEV_KEY_NAME)

ENV_INTEGRATIONS_FILE = "SENTINEL_INTEGRATIONS_FILE"
ENV_STATE_DIR = "SENTINEL_STATE_DIR"
ENV_SIMULATED = "SENTINEL_SIMULATED_PAGING"

_PD_KEY_RE = re.compile(r"^[0-9a-fA-F]{32}$")


class EphemeralStoreError(RuntimeError):
    pass


class BadKey(ValueError):
    pass


def _default_path() -> str:
    return os.path.join(os.environ.get(ENV_STATE_DIR, "./sentinel-state"),
                         "integrations.json")


def validate_routing_key(value: str) -> str:
    v = (value or "").strip()
    if not _PD_KEY_RE.fullmatch(v):
        raise BadKey(
            "that doesn't look like a PagerDuty Events API v2 routing key "
            "(32 hexadecimal characters — find it under your PagerDuty "
            "service's Integrations tab, Events API v2).")
    return v


def validate_jev_key(value: str) -> str:
    v = (value or "").strip()
    if len(v) < 8:
        raise BadKey("Jev API key looks too short — check for a copy/paste slip.")
    return v


_VALIDATORS = {PD_KEY_NAME: validate_routing_key, JEV_KEY_NAME: validate_jev_key}


class IntegrationStore:
    """Values are write-only. status() is the only public read."""

    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get(ENV_INTEGRATIONS_FILE) or _default_path()
        self._ephemeral = False
        try:
            parent = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(parent, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=parent, prefix=".wtest")
            os.close(fd)
            os.unlink(tmp)
        except OSError:
            self._ephemeral = True
        self._mem: dict = {}

    @property
    def ephemeral(self) -> bool:
        return self._ephemeral

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
                "key storage is ephemeral here (no writable state dir)")
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

    def get(self, name: str) -> str | None:
        if name not in KNOWN_KEYS:
            raise KeyError(name)
        v = self._read().get(name)
        return v if isinstance(v, str) and v else None

    def set(self, name: str, value: str) -> dict:
        if name not in KNOWN_KEYS:
            raise KeyError(name)
        clean = _VALIDATORS[name](value)
        data = self._read()
        data[name] = clean
        self._write(data)
        return self._public(clean)

    def set_many(self, items: dict) -> dict:
        """Validate ALL, then persist ALL. Never partially persists."""
        cleaned = {}
        for name, value in items.items():
            if name not in KNOWN_KEYS:
                raise KeyError(name)
            cleaned[name] = _VALIDATORS[name](value)
        data = self._read()
        data.update(cleaned)
        self._write(data)
        return {name: self._public(clean) for name, clean in cleaned.items()}

    def delete(self, name: str) -> None:
        if name not in KNOWN_KEYS:
            raise KeyError(name)
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
        out = {n: self._public(self.get(n)) for n in KNOWN_KEYS}
        out["simulated_paging"] = self.simulated_paging()
        out["ephemeral"] = self._ephemeral
        return out

    def simulated_paging(self) -> bool:
        if os.environ.get(ENV_SIMULATED, "0") == "1":
            return True
        try:
            return bool(self.get_flag("simulated_paging"))
        except Exception:
            return False

    @staticmethod
    def _public(value: str | None) -> dict:
        if not value:
            return {"configured": False, "last4": None}
        return {"configured": True, "last4": value[-4:]}
