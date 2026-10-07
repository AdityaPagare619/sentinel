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

Storage classes (2026-10-07, hash-at-rest): ``operator_bearer`` is
VERIFY-ONLY — the server only ever compares a presented candidate — so it
is stored as a SHA-256 hex digest (``{"primary_sha256": ...}``); a leaked
file yields no token. BYOK keys (``pagerduty_routing_key``, ``jev_api_key``
in integrations.py) stay recoverable plaintext in the 600-perm file — the
server must PRESENT them to vendor APIs (the gh ~/.config/gh/hosts.yml
model). See the engine twin's module docstring for the full rationale.

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

# Keep in sync with src/sentinel/keystore.py: default_state_dir,
# _sha256_hex, _encode_stored, decode_record, encode_record,
# RotationError, RotatingKeyStore (ceremony + audit semantics +
# hash-at-rest), and the break-glass generation binding.


ENV_STATE_DIR = "SENTINEL_STATE_DIR"


def default_state_dir() -> str:
    """Default state directory — OUTSIDE the repo tree.

    The old default "./sentinel-state" put live secret files inside the
    source tree (a live-format operator token was created there during an
    audit run). State is deployment data, not source: default to
    ~/.sentinel/state. SENTINEL_STATE_DIR still overrides explicitly.

    Upgrading installs: the store moves on next boot; a fresh operator
    token is minted under the new dir and shown once in the first-boot
    banner — re-paste it into the console. The old ./sentinel-state/ can
    be deleted after confirming, or kept reachable via
    SENTINEL_STATE_DIR=./sentinel-state.
    """
    return os.environ.get(ENV_STATE_DIR) or os.path.expanduser(
        "~/.sentinel/state")


def _sha256_hex(value: str) -> str:
    """SHA-256 hex digest. Unsalted is safe here: stored values have
    >=128-bit entropy (token_urlsafe(32)); the digest's job is to make a
    leaked file useless, not to slow a password guess."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _encode_stored(primary_stored: str, secondary_stored: str | None,
                   generation: int, hashed: bool) -> dict:
    """Low-level encoder: values are ALREADY in stored form (digests when
    hashed, plaintext otherwise). Ceremony methods use this to carry values
    across generations without re-hashing digests."""
    if hashed:
        return {"primary_sha256": primary_stored,
                "secondary_sha256": secondary_stored,
                "generation": int(generation)}
    return {"primary": primary_stored,
            "secondary": secondary_stored,
            "generation": int(generation)}


def decode_record(raw) -> dict:
    """Normalize a stored entry to {primary, secondary, generation, hashed}.

    Three on-disk formats (all accepted on read):
      - {"primary_sha256": hex, "secondary_sha256": hex|None, "generation": n}
        → verify-only record, hashed at rest (2026-10-07+). "primary" holds
        the hex digest; hashed=True. Plaintext is unrecoverable.
      - {"primary": str, "secondary": str|None, "generation": n}
        → plaintext record (presentation secrets, or pre-hash-at-rest
        verify-only records awaiting migration). hashed=False.
      - "plain-string" → legacy pre-T4 plaintext, generation 1.

    Back-compat: legacy plain-string entries decode to a generation-1
    record with no secondary.
    """
    if isinstance(raw, dict) and isinstance(raw.get("primary_sha256"), str):
        gen = raw.get("generation", 1)
        try:
            gen = int(gen)
        except (TypeError, ValueError):
            gen = 1
        sec = raw.get("secondary_sha256")
        return {
            "primary": raw["primary_sha256"],
            "secondary": sec if isinstance(sec, str) and sec else None,
            "generation": max(1, gen),
            "hashed": True,
        }
    if isinstance(raw, str):
        return {"primary": raw, "secondary": None, "generation": 1,
                "hashed": False}
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
            "hashed": False,
        }
    raise ValueError("unrecognized secret record")


def encode_record(primary: str, secondary: str | None,
                  generation: int, *, hash_at_rest: bool = False) -> dict:
    """Encode a record from PLAINTEXT values. hash_at_rest=True stores
    SHA-256 digests only — the plaintext is unrecoverable from the file
    (verify-only secrets)."""
    if hash_at_rest:
        return _encode_stored(
            _sha256_hex(primary),
            _sha256_hex(secondary) if isinstance(secondary, str)
            and secondary else None,
            generation, True)
    return _encode_stored(primary, secondary, generation, False)


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
    `hash_at_rest`: set of VERIFY-ONLY secret names stored as SHA-256
    digests (see module docstring "Storage classes").
    """

    # Class-level hash-at-rest policy, merged with the constructor arg.
    HASH_AT_REST = frozenset()

    def __init__(self, path: str, *, validators: dict | None = None,
                 audit=None, hash_at_rest=None):
        self.path = path
        self._validators = validators or {}
        self._audit = audit or jsonl_audit_sink(path)
        self._hash_at_rest = (frozenset(hash_at_rest or ())
                              | set(self.HASH_AT_REST))
        self._lock = threading.Lock()
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)

    # ------------------------------------------------------------ internals
    def _hashes(self, name: str) -> bool:
        """Is `name` a verify-only (hash-at-rest) secret under the current
        policy?"""
        policy = getattr(self, "_hash_at_rest", None)
        if policy is None:
            policy = self.HASH_AT_REST
        return name in policy

    def _carry(self, name: str, rec: dict) -> tuple:
        """Normalize a decoded record to this name's CURRENT hash-at-rest
        policy, returning (primary_stored, secondary_stored, hashed).

        Plaintext→digest migrates forward (lazy migration on write);
        digest→plaintext is impossible — fail closed with RotationError
        rather than silently downgrading a hashed record.
        """
        want = self._hashes(name)
        was = bool(rec.get("hashed"))
        if want == was:
            return rec["primary"], rec["secondary"], want
        if want and not was:
            p = _sha256_hex(rec["primary"])
            s = (_sha256_hex(rec["secondary"]) if rec["secondary"]
                 else None)
            return p, s, True
        raise RotationError(
            f"secret {name!r}: stored hashed at rest but the policy no "
            "longer hashes it — refusing to downgrade (fail closed)")

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
        if rec["hashed"]:
            raise TypeError(
                f"secret {name!r} is hashed at rest (verify-only) — "
                "plaintext not recoverable; use verify()")
        out = [rec["primary"]]
        if rec["secondary"]:
            out.append(rec["secondary"])
        return out

    def verify(self, name: str, candidate: str) -> tuple[bool, str | None]:
        """Dual-accept verification → (ok, via).

        via ∈ {"primary", "secondary", "issued:<token_id>", None}.
        Issued (break-glass) tokens verify only at their issuance
        generation — a promote kills every pre-rotation break-glass token.

        For hash-at-rest records the candidate's SHA-256 is compared
        against the stored digests (constant-time); the plaintext never
        exists on disk.
        """
        cand = candidate if isinstance(candidate, str) else ""
        data = self._read_file()
        raw = self._records(data).get(name)
        if raw is None:
            return False, None
        rec = decode_record(raw)
        if rec["hashed"]:
            digest = _sha256_hex(cand)
            if hmac.compare_digest(digest, rec["primary"]):
                return True, "primary"
            if rec["secondary"] and hmac.compare_digest(digest,
                                                       rec["secondary"]):
                return True, "secondary"
        else:
            if hmac.compare_digest(cand, rec["primary"]):
                return True, "primary"
            if rec["secondary"] and hmac.compare_digest(cand,
                                                       rec["secondary"]):
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
            if base:
                if base["hashed"]:
                    same = hmac.compare_digest(_sha256_hex(clean),
                                               base["primary"])
                else:
                    same = hmac.compare_digest(clean, base["primary"])
                if same:
                    raise RotationError(
                        f"secret {name!r}: staged value identical to "
                        "primary — refusing a no-op rotation")
            if base is None:
                recs[name] = encode_record(clean, None, 1,
                                           hash_at_rest=self._hashes(name))
                data["records"] = recs
                self._write_file(data)
                self._log(actor=actor, action="bootstrap", name=name,
                          gen_before=0, gen_after=1,
                          detail="initial secret installed")
                return self.status(name)
            # Carry the stored primary through untouched (a digest when the
            # record is hashed — never re-hash a digest), migrating forward
            # to the current policy if the on-disk form is older.
            p_stored, _s, h_now = self._carry(name, base)
            sec_stored = _sha256_hex(clean) if h_now else clean
            recs[name] = _encode_stored(p_stored, sec_stored, gen, h_now)
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
        if rec["hashed"]:
            ok = hmac.compare_digest(_sha256_hex(candidate or ""),
                                     rec["secondary"])
        else:
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
            # Secondary becomes primary; old primary drops to the grace
            # slot — both carried in stored form (digests when hashed),
            # normalized to the current policy.
            p_stored, s_stored, h_now = self._carry(name, rec)
            recs[name] = _encode_stored(s_stored, p_stored, gen_after, h_now)
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
            p_stored, _s, h_now = self._carry(name, rec)
            recs[name] = _encode_stored(p_stored, None, gen, h_now)
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
            recs[name] = encode_record(clean, None, gen_after,
                                       hash_at_rest=self._hashes(name))
            data["records"] = recs
            self._write_file(data)
            self._log(actor=actor, action="set_immediate", name=name,
                      gen_before=gen_before, gen_after=gen_after,
                      detail="immediate primary replacement (no overlap)"
                             + (f": {reason}" if reason else ""))
            return self.status(name)

    def ensure(self, name: str, generator, actor: str = "system") -> tuple[str, bool]:
        """Bootstrap-once: install `generator()` as primary (gen 1) iff no
        record exists. Returns (value, is_new).

        The value is returned ONLY on first creation — callers must treat
        it as write-once (never log it). For hash-at-rest (verify-only)
        names an existing record returns (None, False): the plaintext is
        unrecoverable by design, and returning the digest would be a lie.
        """
        with self._lock:
            data = self._read_file()
            recs = self._records(data)
            if name in recs:
                if self._hashes(name):
                    return None, False
                return decode_record(recs[name])["primary"], False
            clean = self._validate(name, generator())
            recs[name] = encode_record(clean, None, 1,
                                       hash_at_rest=self._hashes(name))
            data["records"] = recs
            self._write_file(data)
            self._log(actor=actor, action="bootstrap", name=name,
                      gen_before=0, gen_after=1,
                      detail="initial secret installed")
            return clean, True

    def migrate_hash_at_rest(self, name: str,
                             actor: str = "system") -> bool:
        """One-way migration: rewrite a plaintext-stored record for a
        hash-at-rest (verify-only) name into SHA-256-digest form.

        No-op (False) when the name isn't hashed by policy, the record is
        missing, or it's already hashed. Audit-logged when it migrates.
        """
        if not self._hashes(name):
            return False
        with self._lock:
            data = self._read_file()
            recs = self._records(data)
            if name not in recs:
                return False
            rec = decode_record(recs[name])
            if rec["hashed"]:
                return False
            gen = rec["generation"]
            recs[name] = _encode_stored(
                _sha256_hex(rec["primary"]),
                _sha256_hex(rec["secondary"]) if rec["secondary"] else None,
                gen, True)
            data["records"] = recs
            self._write_file(data)
            self._log(actor=actor, action="migrate_hash_at_rest", name=name,
                      gen_before=gen, gen_after=gen,
                      detail="plaintext record rewritten as SHA-256 digests")
            return True

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
        path = os.path.join(default_state_dir(), "operator_token.json")
    return RotatingKeyStore(
        path, validators={OPERATOR_TOKEN_NAME: _operator_token_validator},
        audit=audit,
        # VERIFY-ONLY: the operator token is never presented anywhere by
        # the server — only compared against presented candidates. Store
        # the SHA-256 digest, never the token.
        hash_at_rest={OPERATOR_TOKEN_NAME})


def ensure_operator_token(store: RotatingKeyStore | None = None,
                          actor: str = "system") -> tuple[str | None, bool]:
    """First-boot bootstrap (C1): mint the per-install operator token once.

    Pre-seed escape hatch: SENTINEL_OPERATOR_TOKEN installs the given value
    instead of minting (ops-provisioned installs). Returns (token, is_new);
    the token is returned ONLY on first creation — never log it.

    On later boots returns (None, False): the record is hashed at rest, so
    the plaintext is unrecoverable by design. Any legacy plaintext record
    is migrated to digest form (audit-logged) on the way through.
    """
    store = store or operator_token_store()
    if store.generation(OPERATOR_TOKEN_NAME) is not None:
        # Already bootstrapped. Migrate any pre-hash-at-rest plaintext
        # record forward, then report "not new" without the value.
        store.migrate_hash_at_rest(OPERATOR_TOKEN_NAME, actor=actor)
        return None, False
    preseed = os.environ.get(OPERATOR_TOKEN_ENV)
    return store.ensure(
        OPERATOR_TOKEN_NAME,
        lambda: preseed or secrets.token_urlsafe(32),
        actor=actor)
