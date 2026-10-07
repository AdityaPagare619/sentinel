"""Operator bearer-token auth (Track 1, program/full-build contract C1).

Thin adapter over the CANONICAL rotating keystore (Track 4,
platform/server/keystore.py). This module owns the operator-token UX:
first-boot banner plumbing, env provisioning, ephemeral degradation, and
the Authorization header parsing that app.py uses. All secret storage,
dual-accept verification, generation epochs, audit, and rotation live in
the keystore — there is exactly one implementation of secret handling.

Per-install operator token for the platform API. Every /api/* request
(except the two health probes) must carry it as
`Authorization: Bearer <token>`. The plaintext leaves this process
exactly once (the first-boot setup banner), and is never logged or
embedded in any HTTP response.
"""

from __future__ import annotations

import hmac
import os
import secrets
import sys

from .keystore import (
    OPERATOR_TOKEN_NAME,
    RotatingKeyStore,
    default_state_dir,
    ensure_operator_token,
    operator_token_store,
)

ENV_TOKEN_FILE = "SENTINEL_OPERATOR_TOKEN_FILE"
ENV_TOKEN_VALUE = "SENTINEL_OPERATOR_TOKEN"
# NOTE: the state-dir env name lives in keystore.ENV_STATE_DIR; the default
# dir itself is keystore.default_state_dir() (outside the repo tree).

_TOKEN_BYTES = 32  # -> 43-char token_urlsafe string


def _default_path() -> str:
    # State lives OUTSIDE the repo tree (default_state_dir); this file is
    # a local boot artifact, never the production token (see
    # docs/planning/security/STATE_AND_OPERATOR_TOKEN.md).
    return os.path.join(default_state_dir(), "operator_token.json")


class OperatorTokenStore:
    """Operator-token facade over the canonical RotatingKeyStore.

    Public API (used by app.py and __main__.py — do not break):
      path, ephemeral, first_boot_token, generation,
      verify(presented) -> bool, bearer_from_header(value).
    """

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
        self._ks: RotatingKeyStore | None = None
        self._mem: dict | None = None

        provisioned = (os.environ.get(ENV_TOKEN_VALUE) or "").strip()
        if provisioned:
            # Hosted/serverless: the operator provisions the token via env
            # (e.g. the Vercel dashboard). No file, no banner — they
            # already hold the value.
            self._mem = {"primary": provisioned, "secondary": None}
            return
        try:
            self._ks = operator_token_store(self.path)
            token, is_new = ensure_operator_token(self._ks)
        except OSError:
            # Unwritable state dir: degrade to an in-memory token rather
            # than crashing the boot. Loud — the operator must provision
            # SENTINEL_OPERATOR_TOKEN for a durable install.
            self.ephemeral = True
            self._mem = {"primary": secrets.token_urlsafe(_TOKEN_BYTES),
                         "secondary": None}
            print("! WARNING: operator token is EPHEMERAL "
                  "(state dir unwritable) — set SENTINEL_OPERATOR_TOKEN",
                  file=sys.stderr)
            return
        if is_new:
            self.first_boot_token = token

    # ------------------------------------------------------------ public

    @property
    def keystore(self) -> RotatingKeyStore | None:
        """The canonical store (None in env/ephemeral mode). Track 4's
        rotation ceremony operates on this object — same file, same truth."""
        return self._ks

    @property
    def generation(self) -> int:
        if self._ks is not None:
            return self._ks.generation(OPERATOR_TOKEN_NAME) or 1
        return 1

    @property
    def _tokens(self) -> dict:
        """White-box shim (tests): the current {primary, secondary} pair.

        Raises TypeError for file-backed hash-at-rest records — the
        plaintext is unrecoverable by design; use first_boot_token (the
        mint-time handle) or verify() instead.
        """
        if self._ks is not None:
            rec = self._ks._get_record(OPERATOR_TOKEN_NAME)
            if rec:
                if rec.get("hashed"):
                    raise TypeError(
                        "operator token is hashed at rest (verify-only) — "
                        "plaintext not recoverable; use verify()")
                return {"primary": rec["primary"],
                        "secondary": rec["secondary"]}
            return {"primary": None, "secondary": None}
        return dict(self._mem or {})

    def verify(self, presented: str | None) -> bool:
        """Dual-accept via the canonical keystore (C4): primary OR
        secondary (post-rotation) verifies. Constant-time compare."""
        if self._ks is not None:
            ok, _via = self._ks.verify(OPERATOR_TOKEN_NAME, presented)
            return bool(ok)
        cand = (self._mem or {}).get("primary")
        return (isinstance(presented, str) and bool(presented)
                and bool(cand) and hmac.compare_digest(presented, cand))

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
