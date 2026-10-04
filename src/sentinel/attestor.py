"""Attestor identity — ADR attestor-identity (2026-10-04).

The unnamed trusted party across ADR-013 (dual attestation), ADR-014 (proofs)
and ADR-022 (two-person rule), now named: attestors are Ed25519 keypairs, the
registry maps attestor IDs to public keys, and an attestation is a SIGNATURE,
not a string.

Trust rules (the ADR):
  - Onboarding: 2 ACTIVE attestors co-sign the new key. Founder bootstrap seeds
    #1 (recorded as such, never repeated silently).
  - Revocation: single-operator, immediate, ONE-WAY latch (revoked -> active is
    forbidden; re-onboard as a new key id). Revoke is fail-safe (it causes
    paging, never suppression), so it must be fast, not ceremonial.
  - The registry file is HMAC-sealed with a root key. Seal failure = registry
    unusable = fail toward paging (never fail open into string-ID trust).
  - Every mutation appends to a hash-chained ceremony log — the audit evidence
    for who trusted whom, when.
  - Attestation signatures bind (policy_id, version, content_hash, from_state,
    to_state, decided_at). The content_hash binding closes the replay gap:
    an attestation for benign content does not transfer to swapped content.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)

from .eventlog import _canonical

# ---------------------------------------------------------------- constants

ROOT_KEY_ENV = "SENTINEL_ATTESTOR_ROOT_KEY"  # hex-encoded 32 bytes
REGISTRY_FORMAT = 1
CEREMONY_FORMAT = 1

# Type 2 tunables (ADR §3).
ATTESTATION_MAX_AGE = timedelta(days=7)
ATTESTATION_FUTURE_TOLERANCE = timedelta(minutes=5)


class AttestorError(Exception):
    """Attestor registry / verification failure — fail toward paging."""


class RegistrySealError(AttestorError):
    """Registry seal mismatch or missing root key: the registry is unusable."""


# ------------------------------------------------------------------ keys

def generate_keypair() -> tuple[str, str]:
    """Returns (private_hex, public_hex). Private keys never touch the repo."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    return (priv.private_bytes_raw().hex(),
            pub.public_bytes_raw().hex())


def canonical_attestation_bytes(policy_id: str, version: int,
                                content_hash: str, from_state: str,
                                to_state: str, decided_at: str) -> bytes:
    """The exact bytes an attestation signs. content_hash binding is the
    replay-gap closure (Tripwire's monkey-first finding)."""
    return _canonical({
        "policy_id": policy_id, "version": version,
        "content_hash": content_hash,
        "from_state": from_state, "to_state": to_state,
        "decided_at": decided_at,
    })


def sign_attestation(private_hex: str, policy_id: str, version: int,
                     content_hash: str, from_state: str, to_state: str,
                     decided_at: str) -> str:
    priv = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(private_hex))
    return priv.sign(canonical_attestation_bytes(
        policy_id, version, content_hash, from_state, to_state,
        decided_at)).hex()


def verify_attestation_signature(public_hex: str, signature_hex: str,
                                policy_id: str, version: int,
                                content_hash: str, from_state: str,
                                to_state: str, decided_at: str) -> bool:
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
        pub.verify(bytes.fromhex(signature_hex), canonical_attestation_bytes(
            policy_id, version, content_hash, from_state, to_state,
            decided_at))
        return True
    except (InvalidSignature, ValueError):
        return False


def canonical_onboard_bytes(new_id: str, public_key_hex: str,
                            added_at: str) -> bytes:
    return _canonical({"action": "onboard", "new_id": new_id,
                       "public_key_hex": public_key_hex,
                       "added_at": added_at})


# ------------------------------------------------------------------ records

@dataclasses.dataclass
class AttestorRecord:
    id: str
    public_key_hex: str
    algorithm: str = "ed25519"       # Type 2: carries the migration path
    status: str = "active"           # active | revoked (one-way latch)
    added_at: str = ""
    added_by: str = ""
    revoked_at: str | None = None
    revoke_reason: str | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AttestorRecord":
        return cls(**{f.name: d.get(f.name, f.default)
                      for f in dataclasses.fields(cls)})


def _utcnow_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _resolve_root_key(explicit: bytes | None = None) -> bytes:
    if explicit:
        return explicit
    raw = os.environ.get(ROOT_KEY_ENV, "")
    if not raw:
        raise RegistrySealError(
            f"attestor registry requires a root key: set {ROOT_KEY_ENV} "
            f"(hex, 32 bytes) — refusing to run unsealed")
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        raise RegistrySealError(f"{ROOT_KEY_ENV} is not valid hex")
    if len(key) != 32:
        raise RegistrySealError(f"{ROOT_KEY_ENV} must be 32 bytes")
    return key


# ------------------------------------------------------------------ registry

class AttestorRegistry:
    """Sealed attestor registry + chained ceremony log.

    Fresh-read discipline: PolicyStore loads this from `path` on EVERY
    transition/unfreeze call — revocation is effective on the next decision,
    never served from a boot cache (ADR §2, Type 1).
    """

    def __init__(self, path: str, root_key: bytes | None = None):
        self.path = path
        self.ceremony_path = path + ".ceremony.jsonl"
        self._root_key = _resolve_root_key(root_key)
        self._attestors: dict[str, AttestorRecord] = {}
        self._ceremony_head = "GENESIS"
        if os.path.exists(path):
            self._load()
        else:
            self._sealed_write()  # creates the sealed empty registry

    # ------------------------------------------------------------ persistence

    def _seal(self, body: dict) -> str:
        return hmac.new(self._root_key, _canonical(body),
                        hashlib.sha256).hexdigest()

    def _sealed_write(self) -> None:
        body = {"format": REGISTRY_FORMAT,
                "attestors": {aid: r.to_dict()
                              for aid, r in self._attestors.items()}}
        doc = {**body, "seal": self._seal(body)}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, sort_keys=True, indent=2)
        os.replace(tmp, self.path)

    def _load(self) -> None:
        with open(self.path, encoding="utf-8") as fh:
            doc = json.load(fh)
        seal = doc.pop("seal", None)
        if doc.get("format") != REGISTRY_FORMAT:
            raise RegistrySealError(
                f"unknown registry format: {doc.get('format')}")
        if not seal or not hmac.compare_digest(seal, self._seal(doc)):
            raise RegistrySealError(
                "registry seal mismatch — the registry is unusable; "
                "failing toward paging (never falling back to string IDs)")
        self._attestors = {aid: AttestorRecord.from_dict(r)
                           for aid, r in doc.get("attestors", {}).items()}
        self._verify_ceremony_chain()

    # ------------------------------------------------------------ ceremony log

    def _ceremony_record(self, rtype: str, payload: dict,
                         signatures: dict | None = None) -> dict:
        prev = self._ceremony_head
        rec = {"format": CEREMONY_FORMAT, "type": rtype, "payload": payload,
               "signatures": signatures or {}, "prev_hash": prev,
               "at": _utcnow_iso()}
        rec_hash = hashlib.sha256(
            _canonical({k: v for k, v in rec.items()
                        if k != "at"}) + prev.encode()).hexdigest()
        rec["record_hash"] = rec_hash
        with open(self.ceremony_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        self._ceremony_head = rec_hash
        return rec

    def _verify_ceremony_chain(self) -> None:
        if not os.path.exists(self.ceremony_path):
            return
        prev = "GENESIS"
        with open(self.ceremony_path, encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if rec.get("prev_hash") != prev:
                    raise RegistrySealError(
                        "ceremony log chain broken — refusing to trust")
                expect = hashlib.sha256(
                    _canonical({k: v for k, v in rec.items()
                                if k not in ("at", "record_hash")})
                    + prev.encode()).hexdigest()
                # NOTE: record_hash covers the record minus wall-clock `at`
                # (recomputed identically here); mismatch = tamper.
                if rec.get("record_hash") != expect:
                    raise RegistrySealError(
                        "ceremony log tamper detected — refusing to trust")
                prev = rec["record_hash"]
        self._ceremony_head = prev

    # ------------------------------------------------------------ queries

    def is_active(self, attestor_id: str) -> bool:
        r = self._attestors.get(attestor_id)
        return r is not None and r.status == "active"

    def public_key(self, attestor_id: str) -> str:
        r = self._attestors.get(attestor_id)
        if r is None:
            raise AttestorError(f"unknown attestor: {attestor_id!r}")
        return r.public_key_hex

    def attestors(self) -> dict[str, AttestorRecord]:
        return dict(self._attestors)

    # ------------------------------------------------------------ mutations

    def bootstrap(self, founders: list[tuple[str, str]]) -> list[AttestorRecord]:
        """Founder ceremony: seeds the INITIAL QUORUM. One-time — the second
        call raises. A quorum (>=2) is required because onboarding needs 2
        co-signers; seeding a single founder would deadlock onboarding."""
        if self._attestors:
            raise AttestorError("bootstrap is a one-time founder ceremony — "
                                "the registry is not empty")
        if len(founders) < 2:
            raise AttestorError("founder ceremony must seed >= 2 attestors "
                                "(onboarding needs 2 co-signers)")
        recs = []
        for attestor_id, public_key_hex in founders:
            rec = AttestorRecord(id=attestor_id, public_key_hex=public_key_hex,
                                 added_at=_utcnow_iso(), added_by="founder")
            self._attestors[attestor_id] = rec
            recs.append(rec)
        self._ceremony_record("bootstrap",
                              {"founder_quorum": [f[0] for f in founders],
                               "founder": True})
        self._sealed_write()
        return recs

    def onboard(self, new_id: str, public_key_hex: str, *,
                cosignatures: dict[str, str], added_by: str,
                added_at: str | None = None) -> AttestorRecord:
        """Two-person ceremony: >=2 DISTINCT ACTIVE attestors co-sign
        (new_id, public_key, added_at)."""
        if new_id in self._attestors:
            raise AttestorError(f"attestor {new_id!r} already registered")
        added_at = added_at or _utcnow_iso()
        canon = canonical_onboard_bytes(new_id, public_key_hex, added_at)
        signers = set()
        for aid, sig_hex in cosignatures.items():
            if not self.is_active(aid):
                raise AttestorError(
                    f"co-signer {aid!r} is not an active attestor")
            try:
                pub = Ed25519PublicKey.from_public_bytes(
                    bytes.fromhex(self.public_key(aid)))
                pub.verify(bytes.fromhex(sig_hex), canon)
            except (InvalidSignature, ValueError):
                raise AttestorError(
                    f"co-signer {aid!r}: bad onboarding signature")
            signers.add(aid)
        if len(signers) < 2:
            raise AttestorError(
                f"onboarding requires 2 distinct active co-signers, got "
                f"{len(signers)}")
        rec = AttestorRecord(id=new_id, public_key_hex=public_key_hex,
                             added_at=added_at, added_by=added_by)
        self._attestors[new_id] = rec
        self._ceremony_record("onboard",
                              {"new_id": new_id,
                               "public_key_hex": public_key_hex,
                               "added_by": added_by,
                               "co_signers": sorted(signers)},
                              signatures=cosignatures)
        self._sealed_write()
        return rec

    def revoke(self, attestor_id: str, reason: str, *,
               by: str) -> AttestorRecord:
        """Single-operator, immediate, ONE-WAY. Revoked -> active is
        forbidden: re-onboard as a new key id. Fail-safe direction (revoke
        causes paging, never suppression), so no two-person ceremony — speed
        is the safety property at 3 AM."""
        rec = self._attestors.get(attestor_id)
        if rec is None:
            raise AttestorError(f"unknown attestor: {attestor_id!r}")
        if rec.status == "revoked":
            raise AttestorError(f"attestor {attestor_id!r} already revoked")
        rec.status = "revoked"
        rec.revoked_at = _utcnow_iso()
        rec.revoke_reason = reason
        self._ceremony_record("revoke", {"attestor_id": attestor_id,
                                         "reason": reason, "by": by})
        self._sealed_write()
        sys.stderr.write(f"[sentinel][attestor] REVOKED {attestor_id} by {by}:"
                         f" {reason}\n")
        return rec

    def reactivate(self, attestor_id: str, **_) -> AttestorRecord:
        """Deliberately absent as an operation: the one-way latch. Call this
        and you get the error that explains why."""
        raise AttestorError(
            f"attestor {attestor_id!r}: revoked -> active is FORBIDDEN "
            f"(one-way latch, ADR attestor-identity) — re-onboard as a new "
            f"key id")


# ------------------------------------------------------- attestation check

def check_attestation(registry: AttestorRegistry, *, policy_id: str,
                      version: int, content_hash: str, from_state: str,
                      to_state: str, attestor_id: str, signature_hex: str,
                      decided_at: str,
                      now: datetime) -> None:
    """Verify one attestor's signature. Raises AttestorError (a
    PolicyTransitionError upstream) on ANY failure — unknown, revoked, bad
    signature, stale, or future-dated."""
    if not registry.is_active(attestor_id):
        raise AttestorError(
            f"attestor {attestor_id!r} is not active (unknown or revoked) — "
            f"attestation invalid")
    if not verify_attestation_signature(
            registry.public_key(attestor_id), signature_hex, policy_id,
            version, content_hash, from_state, to_state, decided_at):
        raise AttestorError(
            f"attestor {attestor_id!r}: signature invalid for "
            f"{policy_id} v{version} {from_state}->{to_state} "
            f"(content_hash-bound)")
    decided = datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
    if decided < now - ATTESTATION_MAX_AGE:
        raise AttestorError(
            f"attestor {attestor_id!r}: attestation stale "
            f"(decided_at {decided_at})")
    if decided > now + ATTESTATION_FUTURE_TOLERANCE:
        raise AttestorError(
            f"attestor {attestor_id!r}: attestation from the future "
            f"(decided_at {decided_at})")


# ------------------------------------------------------- compromise sweep

def quarantine_revoked(store, attestor_id: str,
                       now: datetime | None = None) -> list[dict]:
    """Vault's compromise response, step 2: freeze every LIVE/REVIEW_DUE
    version the revoked attestor helped promote. History is NOT rewritten —
    the version is frozen, dispositions fall through to page, and the owner
    is paged. Returns page event dicts for the caller to emit.

    `store` is a PolicyStore (duck-typed to avoid an import cycle).
    """
    now = now or datetime.now(timezone.utc)
    events: list[dict] = []
    for pid, versions in store._policies.items():
        for v in versions:
            if v.state not in ("live", "review-due") or v.frozen:
                continue
            ids = {a for att in v.attestations
                   for a in att.attestor_ids}
            if attestor_id not in ids:
                continue
            v.frozen = True
            v.frozen_reason = f"attestor-revoked:{attestor_id}"
            events.append({
                "type": "policy_quarantined",
                "policy_id": pid, "version": v.version,
                "revoked_attestor": attestor_id,
                "page_owner": True,
                "fail_loud": True,
                "summary": (f"policy {pid} v{v.version} FROZEN: promoted with "
                            f"attestation from revoked attestor {attestor_id};"
                            f" dispositions fall through to page"),
            })
    try:
        store.save()
    except Exception:
        pass
    return events
