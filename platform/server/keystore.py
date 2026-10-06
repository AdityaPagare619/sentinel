"""Rotatable secret store — platform tier TWIN of src/sentinel/keystore.py.

The platform tier never imports the engine package (tier decoupling), so
this is a deliberate twin: same record format
``{name: {"primary": str, "secondary": str | None, "generation": int}}``,
same four-step ceremony (stage_secondary → verify_secondary → promote →
retire), same dual-accept verification, same audit contract (actor,
timestamp, generation before/after; NEVER secret values). If the format
ever changes, change BOTH modules and note it in the PR — the record
format is the shared surface.

Platform-tier specialization: the operator bearer token (contract C1) —
``ensure_operator_token()`` bootstraps the per-install
``secrets.token_urlsafe(32)`` token exactly once. Track 1's auth
middleware verifies with :meth:`RotatingKeyStore.verify`; rotation goes
through the ceremony (or ``platform/server/rotation_api.py``).

Math/design: MATH_DESIGN_T4.md (repo root of the T4 lane).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone

# Keep in sync with src/sentinel/keystore.py: decode_record, encode_record,
# RotationError, RotatingKeyStore (ceremony + audit semantics), and the
# break-glass generation binding.


def decode_record(raw) -> dict:
    """Normalize a stored entry to {primary, secondary, generation}.

    Back-compat: legacy plain-string entries decode to a generation-1
    record with no secondary.
    """
    if isinstance(raw, str):
        return {"primary": raw, "secondary": None, "generation": 1}
    if isinstance(raw, dict) and isinstance(raw.get("primary"), str):
        gen = raw.get("generation", 1)
        try:
            gen = int(gen)
        except (TypeError, ValueError):
            gen = 1
        sec = raw.get("secondary")
        return {
            "primary": raw["primary"],
            "secondary": sec if isinstance(sec, str) and sec else None,
            "generation": max(1, gen),
        }
    raise ValueError("unrecognized secret record")


def encode_record(primary: str, secondary: str | None,
                  generation: int) -> dict:
    return {"primary": primary, "secondary": secondary,
            "generation": int(generation)}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_AUDIT_LOCK = threading.Lock()


def jsonl_audit_sink(path: str):
    """JSONL audit sink next to the store file — names/generations/actors
    only, NEVER secret values."""
    audit_path = path + ".audit.jsonl"

    def _audit(event: dict) -> None:
        line = json.dumps(event, separators=(",", ":")) + "\n"
        with _AUDIT_LOCK:
            with open(audit_path, "a", encoding="utf-8") as f:
                f.write(line)
        try:
            os.chmod(audit_path, 0o600)
        except OSError:
            pass

    return _audit


class RotationError(Exception):
    """Illegal ceremony transition (e.g. promote with no staged secondary)."""


class RotatingKeyStore:
    """File-backed rotatable secrets with dual-accept verification.

    Twin of src/sentinel/keystore.py::RotatingKeyStore — same semantics.
    `validators`: name -> callable(value) -> cleaned value.
    `audit`: callable(event: dict) -> None; defaults to a JSONL file.
    """

    def __init__(self, path: str, *, validators: dict | None = None,
                 audit=None):
        self.path = path
        self._validators = validators or {}
        self._audit = audit or jsonl_audit_sink(path)
        self._lock = threading.Lock()
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)

    # ------------------------------------------------------------ internals
    def _read_file(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_file(self, data: dict) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".keystore")
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

    def _records(self, data: dict) -> dict:
        recs = data.get("records")
        return recs if isinstance(recs, dict) else {}

    def _issued(self, data: dict) -> dict:
        iss = data.get("issued")
        return iss if isinstance(iss, dict) else {}

    def _validate(self, name: str, value: str) -> str:
        v = (value or "").strip()
        if not v:
            raise ValueError(f"secret {name!r}: empty value rejected")
        fn = self._validators.get(name)
        return fn(v) if fn else v

    def _log(self, *, actor: str, action: str, name: str,
             gen_before: int, gen_after: int, detail: str = "") -> None:
        self._audit({
            "ts": utcnow_iso(),
            "actor": actor,
            "action": action,
            "secret_name": name,
            "generation_before": gen_before,
            "generation_after": gen_after,
            "detail": detail,
        })

    def _get_record(self, name: str) -> dict | None:
        raw = self._records(self._read_file()).get(name)
        if raw is None:
            return None
        return decode_record(raw)

    # ------------------------------------------------------------------ API
    def generation(self, name: str) -> int | None:
        rec = self._get_record(name)
        return rec["generation"] if rec else None

    def candidates(self, name: str) -> list[str]:
        rec = self._get_record(name)
        if not rec:
            return []
        out = [rec["primary"]]
        if rec["secondary"]:
            out.append(rec["secondary"])
        return out

    def verify(self, name: str, candidate: str) -> tuple[bool, str | None]:
        """Dual-accept verification → (ok, via).

        via ∈ {"primary", "secondary", "issued:<token_id>", None}.
        Issued (break-glass) tokens verify only at their issuance
        generation — a promote kills every pre-rotation break-glass token.
        """
        cand = candidate or ""
        data = self._read_file()
        raw = self._records(data).get(name)
        if raw is None:
            return False, None
        rec = decode_record(raw)
        if hmac.compare_digest(cand, rec["primary"]):
            return True, "primary"
        if rec["secondary"] and hmac.compare_digest(cand, rec["secondary"]):
            return True, "secondary"
        for tid, iss in self._issued(data).items():
            if not isinstance(iss, dict) or iss.get("revoked"):
                continue
            if iss.get("name") != name or iss.get("gen") != rec["generation"]:
                continue
            digest = iss.get("hash", "")
            if digest and hmac.compare_digest(
                    hashlib.sha256(cand.encode("utf-8")).hexdigest(), digest):
                return True, f"issued:{tid}"
        return False, None

    def status(self, name: str) -> dict:
        """Public status — safe for API responses. NEVER includes values."""
        rec = self._get_record(name)
        if not rec:
            return {"configured": False, "secondary_staged": False,
                    "generation": None}
        return {"configured": True,
                "secondary_staged": bool(rec["secondary"]),
                "generation": rec["generation"]}

    # ------------------------------------------- ceremony (each step audited)
    def stage_secondary(self, name: str, value: str, actor: str) -> dict:
        clean = self._validate(name, value)
        with self._lock:
            data = self._read_file()
            recs = self._records(data)
            base = decode_record(recs[name]) if name in recs else None
            gen = base["generation"] if base else 1
            if base and hmac.compare_digest(clean, base["primary"]):
                raise RotationError(
                    f"secret {name!r}: staged value identical to primary — "
                    "refusing a no-op rotation")
            primary = base["primary"] if base else clean
            if base is None:
                recs[name] = encode_record(clean, None, 1)
                data["records"] = recs
                self._write_file(data)
                self._log(actor=actor, action="bootstrap", name=name,
                          gen_before=0, gen_after=1,
                          detail="initial secret installed")
                return self.status(name)
            recs[name] = encode_record(primary, clean, gen)
            data["records"] = recs
            self._write_file(data)
            self._log(actor=actor, action="stage_secondary", name=name,
                      gen_before=gen, gen_after=gen,
                      detail="successor staged; dual-accept overlap begins")
            return self.status(name)

    def verify_secondary(self, name: str, candidate: str,
                         actor: str) -> dict:
        rec = self._get_record(name)
        if not rec or not rec["secondary"]:
            raise RotationError(
                f"secret {name!r}: no staged secondary to verify")
        ok = hmac.compare_digest(candidate or "", rec["secondary"])
        self._log(actor=actor, action="verify_secondary", name=name,
                  gen_before=rec["generation"],
                  gen_after=rec["generation"],
                  detail="staged secret proof-of-work "
                         f"{'passed' if ok else 'FAILED'}")
        return {"ok": ok, "name": name, "generation": rec["generation"]}

    def promote(self, name: str, actor: str) -> dict:
        with self._lock:
            data = self._read_file()
            recs = self._records(data)
            if name not in recs:
                raise RotationError(f"secret {name!r}: unknown secret")
            rec = decode_record(recs[name])
            if not rec["secondary"]:
                raise RotationError(
                    f"secret {name!r}: promote requires a staged secondary — "
                    "run stage_secondary first")
            gen_before = rec["generation"]
            gen_after = gen_before + 1
            recs[name] = encode_record(rec["secondary"], rec["primary"],
                                       gen_after)
            data["records"] = recs
            self._write_file(data)
            self._log(actor=actor, action="promote", name=name,
                      gen_before=gen_before, gen_after=gen_after,
                      detail="staged secondary promoted to primary; "
                             "predecessor demoted to grace slot; "
                             "pre-rotation break-glass tokens revoked by epoch")
            return {"name": name, "generation_before": gen_before,
                    "generation_after": gen_after}

    def retire(self, name: str, actor: str) -> dict:
        with self._lock:
            data = self._read_file()
            recs = self._records(data)
            if name not in recs:
                raise RotationError(f"secret {name!r}: unknown secret")
            rec = decode_record(recs[name])
            if not rec["secondary"]:
                raise RotationError(
                    f"secret {name!r}: nothing to retire — no grace slot")
            gen = rec["generation"]
            recs[name] = encode_record(rec["primary"], None, gen)
            data["records"] = recs
            self._write_file(data)
            self._log(actor=actor, action="retire", name=name,
                      gen_before=gen, gen_after=gen,
                      detail="grace slot dropped; predecessor secret revoked")
            return {"name": name, "generation": gen, "retired": True}

    def set_immediate(self, name: str, value: str, actor: str,
                      reason: str = "") -> dict:
        """Non-ceremonial override: replace the primary NOW (gen+1, grace
        cleared). Audited. Prefer the ceremony; escape hatch for incidents."""
        clean = self._validate(name, value)
        with self._lock:
            data = self._read_file()
            recs = self._records(data)
            base = decode_record(recs[name]) if name in recs else None
            gen_before = base["generation"] if base else 0
            gen_after = gen_before + 1
            recs[name] = encode_record(clean, None, gen_after)
            data["records"] = recs
            self._write_file(data)
            self._log(actor=actor, action="set_immediate", name=name,
                      gen_before=gen_before, gen_after=gen_after,
                      detail="immediate primary replacement (no overlap)"
                             + (f": {reason}" if reason else ""))
            return self.status(name)

    def ensure(self, name: str, generator, actor: str = "system") -> tuple[str, bool]:
        """Bootstrap-once: install `generator()` as primary (gen 1) iff no
        record exists. Returns (value, is_new) — the value is returned ONLY
        here and must never be logged."""
        with self._lock:
            data = self._read_file()
            recs = self._records(data)
            if name in recs:
                return decode_record(recs[name])["primary"], False
            clean = self._validate(name, generator())
            recs[name] = encode_record(clean, None, 1)
            data["records"] = recs
            self._write_file(data)
            self._log(actor=actor, action="bootstrap", name=name,
                      gen_before=0, gen_after=1,
                      detail="initial secret installed")
            return clean, True

    # ------------------------------------------------- break-glass tokens
    def issue_breakglass(self, name: str, actor: str,
                         label: str = "") -> tuple[str, str]:
        rec = self._get_record(name)
        if not rec:
            raise RotationError(
                f"secret {name!r}: cannot issue break-glass for unknown secret")
        token = "bg_" + secrets.token_urlsafe(24)
        token_id = secrets.token_hex(4)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            data = self._read_file()
            issued = self._issued(data)
            issued[token_id] = {
                "name": name,
                "hash": digest,
                "gen": rec["generation"],
                "revoked": False,
                "actor": actor,
                "label": label,
                "ts": utcnow_iso(),
            }
            data["issued"] = issued
            self._write_file(data)
            self._log(actor=actor, action="issue_breakglass", name=name,
                      gen_before=rec["generation"],
                      gen_after=rec["generation"],
                      detail=f"break-glass token {token_id} issued "
                             f"(bound to generation {rec['generation']})"
                             + (f": {label}" if label else ""))
            return token, token_id

    def revoke_breakglass(self, name: str, token_id: str,
                          actor: str) -> dict:
        with self._lock:
            data = self._read_file()
            issued = self._issued(data)
            iss = issued.get(token_id)
            if not iss or iss.get("name") != name:
                raise RotationError(
                    f"secret {name!r}: unknown break-glass token {token_id!r}")
            gen = self.generation(name) or 0
            iss["revoked"] = True
            data["issued"] = issued
            self._write_file(data)
            self._log(actor=actor, action="revoke_breakglass", name=name,
                      gen_before=gen, gen_after=gen,
                      detail=f"break-glass token {token_id} revoked")
            return {"name": name, "token_id": token_id, "revoked": True}


# ---------------------------------------------------------------------------
# operator bearer token (contract C1) — the Track 1 seam.
#
# Track 1's auth middleware: `store.verify("operator_bearer", presented)`.
# First boot: `ensure_operator_token()` mints secrets.token_urlsafe(32)
# once, stored server-side 0600, never logged, never in responses.

OPERATOR_TOKEN_NAME = "operator_bearer"
OPERATOR_TOKEN_ENV = "SENTINEL_OPERATOR_TOKEN"


def _operator_token_validator(value: str) -> str:
    v = value.strip()
    if len(v) < 16:
        raise ValueError("operator token must be at least 16 characters")
    return v


def operator_token_store(path: str | None = None, *, audit=None) -> RotatingKeyStore:
    if path is None:
        state_dir = os.environ.get("SENTINEL_STATE_DIR", "./sentinel-state")
        path = os.path.join(state_dir, "operator_token.json")
    return RotatingKeyStore(
        path, validators={OPERATOR_TOKEN_NAME: _operator_token_validator},
        audit=audit)


def ensure_operator_token(store: RotatingKeyStore | None = None,
                          actor: str = "system") -> tuple[str, bool]:
    """First-boot bootstrap (C1): mint the per-install operator token once.

    Pre-seed escape hatch: SENTINEL_OPERATOR_TOKEN installs the given value
    instead of minting (ops-provisioned installs). Returns (token, is_new);
    the token is returned ONLY on first creation — never log it.
    """
    store = store or operator_token_store()
    if store.generation(OPERATOR_TOKEN_NAME) is not None:
        # Already bootstrapped — read back without minting.
        rec = store._get_record(OPERATOR_TOKEN_NAME)
        return rec["primary"], False
    preseed = os.environ.get(OPERATOR_TOKEN_ENV)
    return store.ensure(
        OPERATOR_TOKEN_NAME,
        lambda: preseed or secrets.token_urlsafe(32),
        actor=actor)
