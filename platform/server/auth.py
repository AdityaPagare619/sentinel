"""Operator bearer-token store (Track 1, program/full-build contract C1).

Per-install operator token for the platform read API. Generated once at
first boot with secrets.token_urlsafe(32), persisted to a 0600 file,
never logged, never returned in any response. Every /api/* request
(except the two health probes) must carry it as
`Authorization: Bearer <token>`.

Track 4 (rotation) touchpoint — built in NOW per C4:
  * the file carries a `generation` counter and a
    `tokens: {primary, secondary}` pair;
  * verification dual-accepts: the presented token is compared
    (constant-time) against primary OR secondary;
  * rotation = Track 4 adds a secondary -> verifies -> promotes ->
    retires, each step audit-logged. This module exposes the seam
    (dual-accept verify + generation) without implementing rotation.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone

ENV_TOKEN_FILE = "SENTINEL_OPERATOR_TOKEN_FILE"
ENV_TOKEN_VALUE = "SENTINEL_OPERATOR_TOKEN"
ENV_STATE_DIR = "SENTINEL_STATE_DIR"

_TOKEN_BYTES = 32  # -> 43-char token_urlsafe string


def _default_path() -> str:
    return os.path.join(
        os.environ.get(ENV_STATE_DIR, "./sentinel-state"),
        "operator_token.json")


class OperatorTokenStore:
    """Holds the operator bearer token. Values are write-never-after-boot:
    the plaintext leaves this process exactly once (the first-boot setup
    banner printed by __main__), and is never logged or embedded in any
    HTTP response."""

    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get(ENV_TOKEN_FILE) or _default_path()
        # first_boot_token is the ONE transient plaintext handle, handed to
        # the first-boot setup banner only. None once the file exists (or
        # when the token is provisioned via env).
        self.first_boot_token: str | None = None
        # ephemeral: no writable file (serverless without a provisioned
        # token) — the token lives for this process only. Loudly degraded;
        # the operator MUST set SENTINEL_OPERATOR_TOKEN there.
        self.ephemeral = False
        provisioned = (os.environ.get(ENV_TOKEN_VALUE) or "").strip()
        if provisioned:
            # Hosted/serverless: the operator provisions the token via env
            # (e.g. the Vercel dashboard). No file, no banner — they
            # already hold the value.
            self._tokens = {"primary": provisioned, "secondary": None}
            self.generation = 1
            return
        data = self._read()
        if data is None:
            token = secrets.token_urlsafe(_TOKEN_BYTES)
            data = {
                "tokens": {"primary": token, "secondary": None},
                "generation": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                self._write(data)
            except OSError:
                self.ephemeral = True
            else:
                self.first_boot_token = token
        self._tokens = data["tokens"]
        self.generation = int(data.get("generation", 1))

    # ------------------------------------------------------------ plumbing

    def _read(self) -> dict | None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, ValueError):
            return None
        if (not isinstance(data, dict)
                or not isinstance(data.get("tokens"), dict)
                or not data["tokens"].get("primary")):
            # Corrupt/unrecognised file: treat as missing so the server
            # self-heals with a fresh token instead of boot-looping.
            return None
        return data

    def _write(self, data: dict) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".operator_token")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, separators=(",", ":"))
                fh.write("\n")
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------ verify

    def verify(self, presented: str | None) -> bool:
        """Dual-accept (C4 seam): accept the primary OR the secondary."""
        if not presented or not isinstance(presented, str):
            return False
        for slot in ("primary", "secondary"):
            candidate = self._tokens.get(slot)
            if candidate and hmac.compare_digest(presented, candidate):
                return True
        return False

    def bearer_from_header(self, value: str | None) -> str | None:
        """Extract the token from an Authorization header value.

        Accepts `Bearer <token>` (scheme case-insensitive, single token).
        Anything else -> None (caller rejects)."""
        if not value or not isinstance(value, str):
            return None
        scheme, _, rest = value.partition(" ")
        if scheme.lower() != "bearer":
            return None
        token = rest.strip()
        return token or None
