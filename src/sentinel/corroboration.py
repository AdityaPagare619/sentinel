"""Corroboration leg for the live suppress conjunction (ADR-019, D5).

"Silence takes two." Suppression currently requires the model's verdict +
the policy gates (value locks, freshness legs, D8 policy gate) — a single
witness for silence. This module adds the explicit, named corroboration leg:
a suppress decision must ALSO be corroborated by an independent signal — not
the same model, not the same data path.

Three kinds of corroboration (design/d5-corroboration.md §2):
  * ``signed_resolve``        — an Ed25519-signed resolve attestation for the
                                same fingerprint, from an ACTIVE attestor in
                                the attestor registry. Unsigned resolve claims
                                NEVER corroborate.
  * ``second_check``          — an independent verifier named in the floor's
                                ``second_check_verifiers`` list (never the Jev
                                model that produced the verdict).
  * ``allowlist_attestation`` — the dual human attestation ceremony on the
                                allowlist entry (two distinct humans, distinct
                                from the entry author). The ceremony is a
                                different witness than list membership.

The silence floor (minimum evidence to stay silent) is a versioned config:
``SilenceFloor`` / ``SilenceFloorStore``. Changes are event-logged (audited),
versions are strictly monotonic, and WEAKENING the floor (lowering the bar
for silence) requires the two-person rule — two distinct active attestors
with content_hash-bound signatures. Strengthening (raising the floor) is
single-operator: raising is the safe direction, so it must be fast.

Corroboration evidence carries freshness: every evidence has a ``decided_at``
and must be within the floor's per-kind ``max_age_s``. Stale evidence does
not count — named in the leg detail, never silently dropped.

Trust rules (fail toward the human — the company-ending bug is silence):
  - No corroborator wired / no evidence / evidence invalid → the leg FAILS →
    the gate pages (``page_now``, reason ``uncorroborated``). Never silent.
  - ``signed_resolve`` evidence is verified IN THE LEG against the attestor
    registry (deterministic crypto, no I/O). A provider that mints evidence
    may pre-verify, but the leg trusts no provider: the strictness lives
    where the decision lives (ADR-019 condition c — the one path where
    fail-open ingest is unacceptable).
  - The receiver's fail-open unsigned ingest path cannot mint
    ``CorroborationEvidence`` — the only minting path for ``signed_resolve``
    is the strict-signature path below.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

from .attestor import check_attestation, _resolve_root_key
from .eventlog import _canonical
from .quantized import dual_attestation_valid

# ---------------------------------------------------------------- constants

FLOOR_FORMAT = 1
FLOOR_POLICY_ID = "silence_floor"      # attestation policy_id for floor changes
RESOLVE_POLICY_ID = "corroboration_resolve"  # audit label for resolve verifies

KIND_SIGNED_RESOLVE = "signed_resolve"
KIND_SECOND_CHECK = "second_check"
KIND_ALLOWLIST_ATTESTATION = "allowlist_attestation"
KNOWN_KINDS = (KIND_SIGNED_RESOLVE, KIND_SECOND_CHECK,
               KIND_ALLOWLIST_ATTESTATION)

# Type 2 defaults (design §4). The floor is config — these are the seeds.
DEFAULT_MAX_AGE_S = {
    KIND_SIGNED_RESOLVE: 24 * 3600,        # a resolve speaks for ~a day
    KIND_SECOND_CHECK: 3600,               # an independent check is perishable
    KIND_ALLOWLIST_ATTESTATION: 30 * 86400,  # aligned with the ADR-022 30d clock
}
DEFAULT_ALLOWED_KINDS = [KIND_SIGNED_RESOLVE, KIND_ALLOWLIST_ATTESTATION]
# second_check is NOT enabled by default: naming a new corroboration kind is
# a trust-model change, so it must pass the two-person bar (design §4).

FUTURE_TOLERANCE = timedelta(minutes=5)  # clock-skew guard on decided_at


class CorroborationError(Exception):
    """Floor store / leg misuse — never raised on the hot path (the leg
    fails toward paging instead)."""


# ------------------------------------------------------------- evidence

@dataclasses.dataclass
class CorroborationEvidence:
    """One candidate corroboration. `signature_hex` is None for unsigned
    claims — those can never corroborate (they stay representable so the
    rejection is visible, not silently absent)."""
    kind: str                        # one of KNOWN_KINDS
    fingerprint: str                 # the fingerprint this evidence is ABOUT
    decided_at: str                  # ISO-8601 wall clock of the claim
    attestor: str | None = None      # who vouched (signed_resolve / attestation)
    verifier_id: str | None = None   # second_check: the independent verifier
    signature_hex: str | None = None # strict-signature material (None = unsigned)
    note: str = ""                   # content bound by the signature

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class CorroborationVerdict:
    """The leg's verdict — VISIBLE in the decision record (design §3)."""
    passed: bool
    kind: str | None          # the kind that fired (first valid), else None
    detail: str               # names what fired or why nothing did
    floor_version: int
    valid_kinds: tuple = ()   # all valid kinds (kind_fired is the first)

    def to_dict(self) -> dict:
        return {"passed": self.passed, "kind": self.kind,
                "detail": self.detail, "floor_version": self.floor_version,
                "valid_kinds": list(self.valid_kinds)}


# ------------------------------------------------------------- silence floor

@dataclasses.dataclass
class SilenceFloor:
    """The versioned silence floor. Raising = safer (single-operator OK);
    lowering = weaker (two-person required)."""
    version: int = 1
    min_evidence: int = 1
    allowed_kinds: list = dataclasses.field(
        default_factory=lambda: list(DEFAULT_ALLOWED_KINDS))
    max_age_s: dict = dataclasses.field(
        default_factory=lambda: dict(DEFAULT_MAX_AGE_S))
    second_check_verifiers: list = dataclasses.field(default_factory=list)
    floor_id: str = "suppression"
    format: int = FLOOR_FORMAT
    updated_at: str = ""
    updated_by: str = ""
    history: list = dataclasses.field(default_factory=list)

    @classmethod
    def default(cls) -> "SilenceFloor":
        return cls()

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SilenceFloor":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def content_hash(self) -> str:
        """The hash attestations bind — closes the replay gap: an attestation
        for a benign floor does not transfer to a swapped floor."""
        body = {k: v for k, v in self.to_dict().items()
                if k not in ("history", "updated_at", "updated_by")}
        return hashlib.sha256(_canonical(body)).hexdigest()


def _weaker_than(new: SilenceFloor, cur: SilenceFloor) -> list[str]:
    """Name every dimension on which `new` weakens `cur` (lowers the bar for
    silence). Empty => new is strengthening-or-neutral."""
    reasons = []
    if new.min_evidence < cur.min_evidence:
        reasons.append(f"min_evidence {cur.min_evidence} -> {new.min_evidence}")
    for kind in new.allowed_kinds:
        if kind not in cur.allowed_kinds:
            reasons.append(f"corroboration kind added: {kind}")
    for kind, bound in new.max_age_s.items():
        old = cur.max_age_s.get(kind)
        if old is not None and bound > old:
            reasons.append(f"max_age_s[{kind}] {old}s -> {bound}s (evidence "
                           f"lives longer)")
    for verifier in new.second_check_verifiers:
        if verifier not in cur.second_check_verifiers:
            reasons.append(f"second_check verifier added: {verifier}")
    return reasons


class SilenceFloorStore:
    """Versioned, audited, SEALED silence-floor config with the two-person rule.

    Fresh-read discipline (Type 1, mirrors the attestor registry): ``current()``
    re-reads the floor file from disk on EVERY call — no boot cache. A floor
    change is effective on the next decision. ``propose()`` is the ONLY
    mutation path: versions strictly increase, every change is appended to
    history, and every change is emitted to the event sink (audited).

    Seal (R12 F2): the floor file is HMAC-sealed like the attestor registry.
    ``current()`` rejects an unsealed or tampered file — a 3 AM hand-edit
    cannot weaken the floor; only ``propose()`` (two-person on weaken) can
    mint a valid seal. The seal key is the attestor root key: the floor is
    governed by the attestors, one trust root.
    """

    def __init__(self, path: str, *, root_key: bytes | None = None,
                 event_sink=None, clock=None):
        self.path = path
        self._root_key = _resolve_root_key(root_key)
        self._event_sink = event_sink
        self._clock = clock

    def _now(self) -> datetime:
        if self._clock:
            return self._clock()
        return datetime.now(timezone.utc)

    @staticmethod
    def _utcnow_iso() -> str:
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%dT%H:%M:%S.") + \
            f"{now.microsecond // 1000:03d}Z"

    # ------------------------------------------------------------ persistence

    def current(self) -> SilenceFloor:
        """Fresh-read the floor. Missing file => the default floor (the leg
        still fails closed without evidence — an absent floor file can never
        *grant* silence, only the version-1 defaults apply).

        R12 F2: an existing file WITHOUT a valid HMAC seal is REJECTED
        (CorroborationError) — a hand-edited floor cannot weaken silence.
        Only propose() mints valid seals."""
        if not os.path.exists(self.path):
            return SilenceFloor.default()
        with open(self.path, encoding="utf-8") as fh:
            doc = json.load(fh)
        seal = doc.pop("seal", None)
        if doc.get("format") != FLOOR_FORMAT:
            raise CorroborationError(
                f"unknown silence-floor format: {doc.get('format')}")
        if not seal or not hmac.compare_digest(seal, self._seal(doc)):
            raise CorroborationError(
                "silence-floor seal mismatch — the floor file was hand-edited "
                "or corrupted; refusing to load. Weaken the floor only via "
                "propose() (two-person rule). Failing closed: the leg gets "
                "no floor, suppressions become uncorroborated → page.")
        return SilenceFloor.from_dict(doc)

    def _seal(self, body: dict) -> str:
        """HMAC-SHA256 over the canonical body — mirrors AttestorRegistry."""
        return hmac.new(self._root_key, _canonical(body),
                        hashlib.sha256).hexdigest()

    def _save(self, floor: SilenceFloor) -> None:
        body = floor.to_dict()
        doc = {**body, "seal": self._seal(body)}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, sort_keys=True, indent=2)
        os.replace(tmp, self.path)

    # --------------------------------------------------------------- mutation

    def propose(self, new_floor: SilenceFloor, *, actor: str,
                registry=None, attestations: list | None = None,
                now: datetime | None = None) -> SilenceFloor:
        """The ONLY floor mutation path.

        * versions strictly increase (new.version == current.version + 1);
        * weakening changes (lowering the bar for silence) require TWO
          distinct ACTIVE attestors, distinct from the actor, with
          content_hash-bound signatures over the floor transition —
          verified through the attestor registry (raises on ANY failure);
        * strengthening changes are single-operator;
        * every change is appended to history AND emitted to the event sink.

        ``attestations``: list of (attestor_id, signature_hex, decided_at)
        triples; the caller signs with ``sign_floor_attestation``.
        """
        now = now or self._now()
        cur = self.current()
        if new_floor.version != cur.version + 1:
            raise CorroborationError(
                f"floor versions must increase by exactly 1: current "
                f"v{cur.version}, proposed v{new_floor.version} (history is "
                f"never rewritten)")
        for kind in new_floor.allowed_kinds:
            if kind not in KNOWN_KINDS:
                raise CorroborationError(
                    f"unknown corroboration kind: {kind!r}")
        for kind in new_floor.max_age_s:
            if kind not in KNOWN_KINDS:
                raise CorroborationError(
                    f"unknown corroboration kind in max_age_s: {kind!r}")
            if new_floor.max_age_s[kind] <= 0:
                raise CorroborationError(
                    f"max_age_s[{kind}] must be positive")
        if new_floor.min_evidence < 1:
            raise CorroborationError("min_evidence must be >= 1")

        weak_reasons = _weaker_than(new_floor, cur)
        direction = "weakened" if weak_reasons else "strengthened"
        attestors: list[str] = []
        if weak_reasons:
            if registry is None:
                raise CorroborationError(
                    "weakening the silence floor requires the two-person "
                    "rule, but no attestor registry was provided — refusing")
            attestors = self._verify_two_person(
                registry, cur, new_floor, actor,
                list(attestations or []), now)
        # Strengthening is single-operator — raising the floor is the safe
        # direction, so it must be fast, not ceremonial.

        new_floor.updated_at = self._utcnow_iso()
        new_floor.updated_by = actor
        record = {
            "version": new_floor.version,
            "changed_at": new_floor.updated_at,
            "changed_by": actor,
            "direction": direction,
            "two_person": bool(weak_reasons),
            "attestors": attestors,
            "weakened_dimensions": weak_reasons,
        }
        new_floor.history = list(cur.history) + [record]
        self._save(new_floor)
        if self._event_sink is not None:
            try:
                self._event_sink("silence_floor_changed", {
                    "floor_id": new_floor.floor_id,
                    "version": new_floor.version,
                    "direction": direction,
                    "two_person": bool(weak_reasons),
                    "changed_by": actor,
                    "attestors": attestors,
                    "weakened_dimensions": weak_reasons,
                    "min_evidence": new_floor.min_evidence,
                    "allowed_kinds": list(new_floor.allowed_kinds),
                })
            except Exception:
                pass  # the floor change is already persisted + in history;
                # a dead sink must not roll back a safety-affecting change
        return new_floor

    def _verify_two_person(self, registry, cur: SilenceFloor,
                           new_floor: SilenceFloor, actor: str,
                           attestations: list, now: datetime) -> list[str]:
        """Two distinct ACTIVE attestors, distinct from the actor, with
        content_hash-bound signatures over this exact transition. Raises
        CorroborationError on ANY failure."""
        content_hash = new_floor.content_hash()
        seen: set[str] = set()
        for triple in attestations:
            try:
                attestor_id, signature_hex, decided_at = triple
            except (TypeError, ValueError):
                raise CorroborationError(
                    "floor attestation must be an (attestor_id, "
                    "signature_hex, decided_at) triple")
            if attestor_id == actor:
                raise CorroborationError(
                    "floor attestors must be distinct from the author")
            try:
                check_attestation(
                    registry, policy_id=FLOOR_POLICY_ID,
                    version=new_floor.version, content_hash=content_hash,
                    from_state=f"v{cur.version}",
                    to_state=f"v{new_floor.version}",
                    attestor_id=attestor_id, signature_hex=signature_hex,
                    decided_at=decided_at, now=now)
            except Exception as exc:
                raise CorroborationError(
                    f"floor attestation by {attestor_id!r} invalid: {exc}")
            seen.add(attestor_id)
        if len(seen) < 2:
            raise CorroborationError(
                f"weakening the silence floor requires 2 distinct active "
                f"attestors, got {len(seen)}")
        return sorted(seen)


# ------------------------------------------------- strict-signature resolves

def canonical_resolve_bytes(fingerprint: str, resolved_by: str,
                            decided_at: str, note: str) -> bytes:
    """The exact bytes a resolve attestation signs. The fingerprint binding
    stops a resolve for alert A corroborating alert B; the note binding
    closes the replay gap (an attestation for a benign note does not
    transfer to a swapped note)."""
    return _canonical({"action": "resolve", "fingerprint": fingerprint,
                       "resolved_by": resolved_by, "decided_at": decided_at,
                       "note": note})


def sign_resolve(private_hex: str, fingerprint: str, resolved_by: str,
                 decided_at: str, note: str = "") -> str:
    """Mint a strict-signature resolve claim. Private keys never touch the
    repo — operators sign with their attestor keys."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    return priv.sign(canonical_resolve_bytes(
        fingerprint, resolved_by, decided_at, note)).hex()


def verify_resolve(public_hex: str, signature_hex: str, fingerprint: str,
                   resolved_by: str, decided_at: str, note: str) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey)
        from cryptography.exceptions import InvalidSignature
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
        pub.verify(bytes.fromhex(signature_hex), canonical_resolve_bytes(
            fingerprint, resolved_by, decided_at, note))
        return True
    except Exception:
        return False


def sign_floor_attestation(private_hex: str, new_floor: SilenceFloor,
                           current_version: int,
                           decided_at: str) -> str:
    """Sign a floor transition for the two-person rule. The signature binds
    the floor's content hash AND the version transition — it cannot be
    replayed onto different floor content or a different transition."""
    from .attestor import sign_attestation
    return sign_attestation(
        private_hex, FLOOR_POLICY_ID, new_floor.version,
        new_floor.content_hash(), f"v{current_version}",
        f"v{new_floor.version}", decided_at)


# ------------------------------------------------------------- the leg itself

def _parse_ts(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _check_freshness(decided_at: str, max_age_s: int,
                     now: datetime, kind: str) -> tuple[bool, str]:
    """Freshness on corroboration evidence: bounded age, future skew guard.
    Stale evidence doesn't count — the rejection is named, never silent."""
    decided = _parse_ts(decided_at)
    if decided is None:
        return False, f"{kind}: undecipherable decided_at {decided_at!r}"
    if decided > now + FUTURE_TOLERANCE:
        return False, (f"{kind}: decided_at {decided_at} is in the future "
                       f"(beyond 5m tolerance) — untrusted")
    age_s = (now - decided).total_seconds()
    if age_s > max_age_s:
        return False, (f"{kind}: stale (age {age_s / 3600:.1f}h > "
                       f"max_age {max_age_s / 3600:.1f}h)")
    return True, ""


def _verify_signed_resolve(ev: CorroborationEvidence, registry,
                           now: datetime) -> tuple[bool, str]:
    """Strict verification IN THE LEG (design §5): active attestor + valid
    signature over the bound bytes + freshness. The leg trusts no provider."""
    if ev.signature_hex is None:
        return False, ("signed_resolve: UNSIGNED resolve claim — unsigned "
                       "resolves never corroborate (ADR-019 cross-item 1)")
    if registry is None:
        return False, ("signed_resolve: no attestor registry wired — the "
                       "claim cannot be strictly verified, so it does not "
                       "corroborate (fail closed)")
    if ev.attestor is None or not registry.is_active(ev.attestor):
        return False, (f"signed_resolve: attestor {ev.attestor!r} is not an "
                       f"active attestor (unknown or revoked)")
    try:
        public_hex = registry.public_key(ev.attestor)
    except Exception as exc:
        return False, f"signed_resolve: cannot load attestor key: {exc}"
    if not verify_resolve(public_hex, ev.signature_hex, ev.fingerprint,
                          ev.attestor or "", ev.decided_at, ev.note):
        return False, (f"signed_resolve: signature INVALID for attestor "
                       f"{ev.attestor!r} (bytes bind fingerprint + note — "
                       f"tampering breaks the signature)")
    return True, ""


def _verify_second_check(ev: CorroborationEvidence, floor: SilenceFloor,
                         jev_model: str | None) -> tuple[bool, str]:
    if not ev.verifier_id:
        return False, "second_check: no verifier identity — anonymous second checks do not corroborate"
    if ev.verifier_id not in floor.second_check_verifiers:
        return False, (f"second_check: verifier {ev.verifier_id!r} is not "
                       f"named in the floor's second_check_verifiers — "
                       f"unnamed witnesses do not corroborate")
    if jev_model is not None and ev.verifier_id == jev_model:
        return False, (f"second_check: verifier {ev.verifier_id!r} IS the "
                       f"model that produced the verdict — the same witness "
                       f"twice is not corroboration")
    return True, ""


def _verify_allowlist_attestation(entry, now: datetime
                                ) -> tuple[bool, str]:
    if entry is None:
        return False, ("allowlist_attestation: no allowlist entry for this "
                       "fingerprint — membership is leg 3's job; the ceremony "
                       "needs an entry to check")
    ok, why = dual_attestation_valid(entry, now)
    if not ok:
        return False, f"allowlist_attestation: ceremony invalid — {why}"
    return True, ""


def evaluate_corroboration(alert, evidences: list[CorroborationEvidence],
                           floor: SilenceFloor, *, now: datetime,
                           registry=None, allowlist_entries: dict | None = None,
                           jev_model: str | None = None
                           ) -> CorroborationVerdict:
    """Evaluate the corroboration leg: pure, no I/O, no clocks (``now`` is
    injected). Never raises — a broken leg fails CLOSED (no corroboration),
    and the gate pages on the failure.

    ``allowlist_entries``: the gate's own entries (fingerprint -> entry) —
    the built-in ``allowlist_attestation`` evidence is derived here, from the
    dual-attestation ceremony, not from list membership.
    """
    try:
        return _evaluate(alert, evidences, floor, now=now, registry=registry,
                         allowlist_entries=allowlist_entries or {},
                         jev_model=jev_model)
    except Exception as exc:  # the leg itself must never crash the decision
        return CorroborationVerdict(
            passed=False, kind=None, floor_version=floor.version,
            detail=f"corroboration leg error (fail closed): {exc}")


def _evaluate(alert, evidences, floor, *, now, registry, allowlist_entries,
              jev_model) -> CorroborationVerdict:
    fp = alert.fingerprint
    valid: list[tuple] = []   # (kind, identity_key) — pieces of evidence
    fired: list[str] = []     # human-readable "who fired" per valid piece
    rejected: list[str] = []  # named rejections (never silently dropped)

    # Built-in evidence: the dual-attestation ceremony on the gate's own
    # allowlist entry (design §2 — the ceremony is a different witness than
    # the list). Minted here, verified here.
    if KIND_ALLOWLIST_ATTESTATION in floor.allowed_kinds:
        entry = allowlist_entries.get(fp)
        bound = floor.max_age_s.get(KIND_ALLOWLIST_ATTESTATION, 0)
        if entry is not None:
            atts = list(getattr(entry, "attestations", None) or [])
            attested = [_parse_ts(a.attested_at) for a in atts]
            # Freshness uses the OLDEST attestation: both humans must be
            # recent — the ceremony is only as fresh as its stalest witness.
            oldest = min((d for d in attested if d is not None), default=None)
            ok, why = _verify_allowlist_attestation(entry, now)
            if not ok:
                rejected.append(why)
            elif oldest is None:
                rejected.append("allowlist_attestation: undecipherable "
                                "attestation timestamps")
            elif oldest > now + FUTURE_TOLERANCE:
                rejected.append("allowlist_attestation: attestation from "
                                "the future (beyond 5m tolerance)")
            else:
                age_s = (now - oldest).total_seconds()
                if age_s > bound:
                    rejected.append(
                        f"allowlist_attestation: stale (oldest attestation "
                        f"{age_s / 86400:.1f}d > max_age {bound / 86400:.1f}d)")
                else:
                    ids = sorted({a.attestor_id for a in atts})
                    # One ceremony, one witness-set, one vote — the tuple
                    # form matches provider evidence below.
                    valid.append((KIND_ALLOWLIST_ATTESTATION,
                                  (KIND_ALLOWLIST_ATTESTATION,
                                   "ceremony:" + "+".join(ids))))
                    fired.append(f"allowlist_attestation: dual ceremony by "
                                 f"{'+'.join(ids)} (oldest {age_s / 3600:.1f}h "
                                 f"old)")

    # Provider evidence: signed_resolve is cryptographically verified in the
    # leg, never trusted on arrival. second_check is name-in-list only
    # (unsigned trust-on-provider — R12 N1): it names an independent verifier
    # but cannot prove independence (same weights, different name is
    # undetectable). It counts as a witness, not as proof.
    for ev in evidences or []:
        if ev.kind not in KNOWN_KINDS:
            rejected.append(f"unknown corroboration kind {ev.kind!r} — ignored")
            continue
        if ev.kind not in floor.allowed_kinds:
            rejected.append(f"{ev.kind}: kind not in the floor's allowed_kinds "
                            f"— the floor does not accept this witness")
            continue
        if ev.fingerprint != fp:
            rejected.append(f"{ev.kind}: fingerprint mismatch "
                            f"({ev.fingerprint!r} != alert) — a resolve for "
                            f"another alert corroborates nothing here")
            continue
        bound = floor.max_age_s.get(ev.kind, 0)
        fresh_ok, fresh_why = _check_freshness(ev.decided_at, bound, now,
                                               ev.kind)
        if not fresh_ok:
            rejected.append(fresh_why)
            continue
        if ev.kind == KIND_SIGNED_RESOLVE:
            ok, why = _verify_signed_resolve(ev, registry, now)
        elif ev.kind == KIND_SECOND_CHECK:
            ok, why = _verify_second_check(ev, floor, jev_model)
        else:  # allowlist_attestation from a provider: the gate's own
               # ceremony check above is authoritative; provider copies are
               # redundant — accept only the built-in derivation.
            ok, why = False, ("allowlist_attestation: provider-minted copies "
                              "are not accepted — the ceremony is derived "
                              "from the gate's own allowlist entry")
        if ok:
            who = ev.attestor or ev.verifier_id or "?"
            # R12 F1: independence is (kind, witness-identity). The same
            # witness signing twice (different timestamps/notes/signatures)
            # is ONE witness, not two — byte-level dedupe is not independence.
            # A second distinct witness is a second vote.
            ident = (ev.kind, who)
            valid.append((ev.kind, ident))
            fired.append(f"{ev.kind}: {who}")
        else:
            rejected.append(why)

    # De-dupe by (kind, witness): one witness, one vote. min_evidence counts
    # PIECES of independent evidence, not claims: two distinct attestors
    # resolving is two witnesses; the same attestor twice is one.
    seen: set = set()
    pieces: list[tuple] = []
    fired_pieces: list[str] = []
    for (kind, ident), desc in zip(valid, fired):
        if ident in seen:
            rejected.append(f"{kind}: {ident[1]} already counted — one "
                            f"witness, one vote (self-corroboration rejected)")
            continue
        seen.add(ident)
        pieces.append((kind, ident))
        fired_pieces.append(desc)
    valid_kinds = list(dict.fromkeys(k for k, _ in pieces))  # kinds, in order
    floor_note = (f"floor v{floor.version} requires {floor.min_evidence}: "
                  f"{', '.join(floor.allowed_kinds)}")
    if len(pieces) >= floor.min_evidence:
        detail = f"corroborated by {', '.join(fired_pieces)} ({floor_note})"
        if rejected:
            detail += "; also seen (rejected): " + "; ".join(rejected)
        return CorroborationVerdict(
            passed=True, kind=valid_kinds[0], detail=detail,
            floor_version=floor.version, valid_kinds=tuple(valid_kinds))
    if not rejected:
        detail = f"no corroboration evidence ({floor_note})"
    else:
        detail = f"no valid corroboration: {'; '.join(rejected)} ({floor_note})"
    return CorroborationVerdict(passed=False, kind=None, detail=detail,
                                floor_version=floor.version,
                                valid_kinds=tuple(valid_kinds))


def corroboration_leg_evaluation(
        verdict: CorroborationVerdict) -> dict:
    """The leg's verdict in lock_evaluation shape — merged into the emitted
    decision payload so the leg is VISIBLE in the decision record (which
    corroboration fired, or that none did)."""
    return {"corroboration": {
        "verdict": "pass" if verdict.passed else "fail",
        "detail": verdict.detail,
        "floor_version": verdict.floor_version,
        "kind": verdict.kind,
    }}
