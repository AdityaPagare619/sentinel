"""Attestor identity — ADR attestor-identity (2026-10-04)."""

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sentinel import attestor as att
from sentinel import policy_lifecycle as pl
from sentinel.attestor import (AttestorRegistry, RegistrySealError,
                               generate_keypair, quarantine_revoked,
                               sign_attestation,
                               verify_attestation_signature)

ROOT = b"0" * 32


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _registry(tmp_path, name="attestors.json"):
    return AttestorRegistry(str(tmp_path / name), root_key=ROOT)


def _bootstrap(reg):
    """Founder quorum: alice + bob."""
    ka = generate_keypair()
    kb = generate_keypair()
    reg.bootstrap([("alice", ka[1]), ("bob", kb[1])])
    return {"alice": ka, "bob": kb}


def _onboard(reg, keys, new_id, signers=("alice", "bob")):
    knew = generate_keypair()
    added_at = _now().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    cosigs = {}
    for s in signers:
        priv = keys[s][0]
        from cryptography.hazmat.primitives.asymmetric.ed25519 import \
            Ed25519PrivateKey
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv))
        cosigs[s] = sk.sign(
            att.canonical_onboard_bytes(new_id, knew[1], added_at)).hex()
    reg.onboard(new_id, knew[1], cosignatures=cosigs, added_by="alice",
                added_at=added_at)
    keys[new_id] = knew
    return knew


def _signed(reg_keys_unused, keys, aid, policy_id, version, content_hash,
            from_state, to_state):
    decided = _now().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    sig = sign_attestation(keys[aid][0], policy_id, version, content_hash,
                           from_state, to_state, decided)
    return pl.PolicyAttestation(
        policy_id=policy_id, version=version, from_state=from_state,
        to_state=to_state, preview_hash="ph", attestor_ids=(aid,),
        decided_at=decided, signatures={aid: sig})


def _store_with_registry(tmp_path, keys, reg=None):
    reg = reg or _registry(tmp_path)
    store = pl.PolicyStore(attestor_registry=reg)
    return store, reg


def _promoted_store(tmp_path, monkeypatch=None):
    """Store with alice+bob registry; policy P v1 shadow. Returns
    (store, reg, keys, version)."""
    reg = _registry(tmp_path)
    keys = _bootstrap(reg)
    store = pl.PolicyStore(attestor_registry=reg)
    v = store.create_draft("P", {"threshold": 0.9}, _now())
    store.transition("P", 1, "shadow", now=_now())
    return store, reg, keys, v


# ------------------------------------------------------- registry

class TestRegistry:
    def test_bootstrap_needs_quorum_and_is_one_time(self, tmp_path):
        reg = _registry(tmp_path)
        with pytest.raises(att.AttestorError):
            reg.bootstrap([("solo", generate_keypair()[1])])
        ka, kb = generate_keypair(), generate_keypair()
        reg.bootstrap([("alice", ka[1]), ("bob", kb[1])])
        assert reg.is_active("alice") and reg.is_active("bob")
        with pytest.raises(att.AttestorError):
            reg.bootstrap([("x", ka[1]), ("y", kb[1])])

    def test_onboard_needs_two_distinct_co_signers(self, tmp_path):
        reg = _registry(tmp_path)
        keys = _bootstrap(reg)
        knew = generate_keypair()
        added_at = _now().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import \
            Ed25519PrivateKey
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(keys["alice"][0]))
        one = {"alice": sk.sign(
            att.canonical_onboard_bytes("carol", knew[1], added_at)).hex()}
        with pytest.raises(att.AttestorError):
            reg.onboard("carol", knew[1], cosignatures=one, added_by="alice",
                        added_at=added_at)
        # unknown co-signer
        with pytest.raises(att.AttestorError):
            reg.onboard("carol", knew[1],
                        cosignatures={"mallory": "00" * 64, "alice": one["alice"]},
                        added_by="alice", added_at=added_at)
        _onboard(reg, keys, "carol")
        assert reg.is_active("carol")

    def test_revoke_is_one_way_latch(self, tmp_path):
        reg = _registry(tmp_path)
        keys = _bootstrap(reg)
        _onboard(reg, keys, "carol")
        reg.revoke("carol", "laptop stolen", by="alice")
        assert not reg.is_active("carol")
        with pytest.raises(att.AttestorError):
            reg.reactivate("carol")
        with pytest.raises(att.AttestorError):
            reg.onboard("carol", generate_keypair()[1],
                        cosignatures={}, added_by="alice")

    def test_seal_tamper_fails_closed(self, tmp_path):
        reg = _registry(tmp_path)
        _bootstrap(reg)
        path = str(tmp_path / "attestors.json")
        doc = json.load(open(path))
        doc["attestors"]["mallory"] = {"id": "mallory",
                                       "public_key_hex": "00" * 32,
                                       "status": "active"}
        json.dump(doc, open(path, "w"), sort_keys=True)
        with pytest.raises(RegistrySealError):
            AttestorRegistry(path, root_key=ROOT)

    def test_ceremony_tamper_fails_closed(self, tmp_path):
        reg = _registry(tmp_path)
        _bootstrap(reg)
        cpath = str(tmp_path / "attestors.json.ceremony.jsonl")
        lines = open(cpath).read().strip().split("\n")
        rec = json.loads(lines[-1])
        rec["payload"]["founder"] = False  # tamper
        lines[-1] = json.dumps(rec, sort_keys=True)
        open(cpath, "w").write("\n".join(lines) + "\n")
        with pytest.raises(RegistrySealError):
            AttestorRegistry(str(tmp_path / "attestors.json"), root_key=ROOT)

    def test_missing_root_key_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.delenv(att.ROOT_KEY_ENV, raising=False)
        with pytest.raises(RegistrySealError):
            AttestorRegistry(str(tmp_path / "n.json"))


# ------------------------------------------------------- signatures

class TestSignatures:
    def test_sign_verify_roundtrip(self):
        priv, pub = generate_keypair()
        sig = sign_attestation(priv, "P", 1, "ch", "shadow", "canary",
                               "2026-10-04T12:00:00.000Z")
        assert verify_attestation_signature(
            pub, sig, "P", 1, "ch", "shadow", "canary",
            "2026-10-04T12:00:00.000Z")
        # replay onto different content FAILS (the content_hash binding)
        assert not verify_attestation_signature(
            pub, sig, "P", 1, "OTHER", "shadow", "canary",
            "2026-10-04T12:00:00.000Z")

    def test_check_rejects_unknown_revoked_stale(self, tmp_path):
        reg = _registry(tmp_path)
        keys = _bootstrap(reg)
        priv, pub = keys["alice"]
        decided = _now().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        sig = sign_attestation(priv, "P", 1, "ch", "shadow", "canary", decided)
        # unknown attestor
        with pytest.raises(att.AttestorError):
            att.check_attestation(reg, policy_id="P", version=1,
                                  content_hash="ch", from_state="shadow",
                                  to_state="canary", attestor_id="mallory",
                                  signature_hex=sig, decided_at=decided,
                                  now=_now())
        # revoked attestor
        reg.revoke("alice", "test", by="bob")
        with pytest.raises(att.AttestorError):
            att.check_attestation(reg, policy_id="P", version=1,
                                  content_hash="ch", from_state="shadow",
                                  to_state="canary", attestor_id="alice",
                                  signature_hex=sig, decided_at=decided,
                                  now=_now())
        # stale decided_at
        reg2 = _registry(tmp_path, "r2.json")
        keys2 = _bootstrap(reg2)
        old = (_now() - dt.timedelta(days=30)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        sig2 = sign_attestation(keys2["alice"][0], "P", 1, "ch", "shadow",
                                "canary", old)
        with pytest.raises(att.AttestorError):
            att.check_attestation(reg2, policy_id="P", version=1,
                                  content_hash="ch", from_state="shadow",
                                  to_state="canary", attestor_id="alice",
                                  signature_hex=sig2, decided_at=old,
                                  now=_now())


# ------------------------------------------------------- store wiring

class TestStoreWiring:
    def test_dual_attestation_happy_path(self, tmp_path):
        store, reg, keys, v = _promoted_store(tmp_path)
        atts = [_signed(None, keys, "alice", "P", 1, v.content_hash,
                        "shadow", "canary"),
                _signed(None, keys, "bob", "P", 1, v.content_hash,
                        "shadow", "canary")]
        store.transition("P", 1, "canary", now=_now(), attestations=atts,
                         preview_hash="ph")
        assert store._get("P", 1).state == "canary"

    def test_unsigned_attestation_rejected_with_registry(self, tmp_path):
        store, reg, keys, v = _promoted_store(tmp_path)
        atts = [pl.PolicyAttestation(
            policy_id="P", version=1, from_state="shadow", to_state="canary",
            preview_hash="ph", attestor_ids=("alice",),
            decided_at=_now().strftime("%Y-%m-%dT%H:%M:%S.000Z")),
            _signed(None, keys, "bob", "P", 1, v.content_hash,
                    "shadow", "canary")]
        with pytest.raises(pl.PolicyTransitionError):
            store.transition("P", 1, "canary", now=_now(), attestations=atts,
                             preview_hash="ph")
        assert store._get("P", 1).state == "shadow"  # never partially promotes

    def test_revoked_attestor_blocks_transition_and_pages(self, tmp_path):
        # The fail-toward-paging chain: revoked => transition raises =>
        # version never live => can_suppress False => gate pages.
        store, reg, keys, v = _promoted_store(tmp_path)
        reg.revoke("alice", "compromised", by="bob")
        # fresh-read propagation: store holds the registry OBJECT here, but
        # the revoke mutated the same file-backed state; re-resolve via path
        # to prove the fresh-read path sees it.
        store2 = pl.PolicyStore(attestor_registry=str(tmp_path /
                                                      "attestors.json"))
        store2._policies = store._policies
        import os as _os
        _os.environ[att.ROOT_KEY_ENV] = ROOT.hex()
        try:
            atts = [_signed(None, keys, "alice", "P", 1, v.content_hash,
                            "shadow", "canary"),
                    _signed(None, keys, "bob", "P", 1, v.content_hash,
                            "shadow", "canary")]
            with pytest.raises(pl.PolicyTransitionError):
                store2.transition("P", 1, "canary", now=_now(),
                                  attestations=atts, preview_hash="ph")
        finally:
            del _os.environ[att.ROOT_KEY_ENV]
        assert store2._get("P", 1).state == "shadow"
        allowed, why = store2.can_suppress("P")
        assert allowed is False  # fail toward paging

    def test_replay_across_content_rejected(self, tmp_path):
        store, reg, keys, v = _promoted_store(tmp_path)
        # attestation signed for DIFFERENT content does not transfer
        atts = [_signed(None, keys, "alice", "P", 1, "WRONG_HASH",
                        "shadow", "canary"),
                _signed(None, keys, "bob", "P", 1, v.content_hash,
                        "shadow", "canary")]
        with pytest.raises(pl.PolicyTransitionError):
            store.transition("P", 1, "canary", now=_now(), attestations=atts,
                             preview_hash="ph")

    def test_legacy_registry_less_mode_preserved(self, tmp_path):
        store = pl.PolicyStore()  # no registry: legacy structural checks
        v = store.create_draft("P", {"t": 1}, _now())
        store.transition("P", 1, "shadow", now=_now())
        atts = [pl.PolicyAttestation(
            policy_id="P", version=1, from_state="shadow", to_state="canary",
            preview_hash="ph", attestor_ids=("a",),
            decided_at=_now().strftime("%Y-%m-%dT%H:%M:%S.000Z")),
            pl.PolicyAttestation(
            policy_id="P", version=1, from_state="shadow", to_state="canary",
            preview_hash="ph", attestor_ids=("b",),
            decided_at=_now().strftime("%Y-%m-%dT%H:%M:%S.000Z"))]
        store.transition("P", 1, "canary", now=_now(), attestations=atts,
                         preview_hash="ph")
        assert store._get("P", 1).state == "canary"


class TestQuarantine:
    def test_quarantine_freezes_and_pages(self, tmp_path):
        store, reg, keys, v = _promoted_store(tmp_path)
        atts = [_signed(None, keys, "alice", "P", 1, v.content_hash,
                        "shadow", "canary"),
                _signed(None, keys, "bob", "P", 1, v.content_hash,
                        "shadow", "canary")]
        store.transition("P", 1, "canary", now=_now(), attestations=atts,
                         preview_hash="ph")
        atts2 = [_signed(None, keys, "alice", "P", 1, v.content_hash,
                         "canary", "live"),
                 _signed(None, keys, "bob", "P", 1, v.content_hash,
                         "canary", "live")]
        store.transition("P", 1, "live", now=_now(), attestations=atts2,
                         preview_hash="ph")
        assert store.can_suppress("P") == (True, "ok")
        reg.revoke("alice", "key compromise", by="bob")
        events = quarantine_revoked(store, "alice", _now())
        assert len(events) == 1
        assert events[0]["page_owner"] is True
        assert "alice" in events[0]["summary"]
        vv = store._get("P", 1)
        assert vv.frozen and vv.frozen_reason == "attestor-revoked:alice"
        # history NOT rewritten: state is still live, but frozen => pages
        assert vv.state == "live"
        allowed, why = store.can_suppress("P")
        assert allowed is False and why.startswith("policy_frozen")

    def test_quarantine_leaves_unrelated_versions_alone(self, tmp_path):
        store, reg, keys, v = _promoted_store(tmp_path)
        _onboard(reg, keys, "carol")
        keys["carol"] = keys["carol"]  # noqa - already stored by _onboard
        atts = [_signed(None, keys, "carol", "P", 1, v.content_hash,
                        "shadow", "canary"),
                _signed(None, keys, "bob", "P", 1, v.content_hash,
                        "shadow", "canary")]
        store.transition("P", 1, "canary", now=_now(), attestations=atts,
                         preview_hash="ph")
        reg.revoke("alice", "test", by="bob")
        events = quarantine_revoked(store, "alice", _now())
        assert events == []  # canary, not live — and alice wasn't involved

    def test_registry_instance_fresh_read_propagates_revocation(self, tmp_path):
        """R8 sharp: PolicyStore(attestor_registry=<instance>) must not serve
        a boot cache — a revocation written by another process is effective
        on the next decision (ADR Type-1 fresh-read-per-decision)."""
        p = str(tmp_path / "attestors.json")
        reg1 = AttestorRegistry(p, root_key=ROOT)
        ka = generate_keypair(); kb = generate_keypair()
        reg1.bootstrap([("alice", ka[1]), ("bob", kb[1])])
        store = pl.PolicyStore(attestor_registry=reg1)
        assert store._registry().is_active("alice")
        # another process revokes alice
        reg2 = AttestorRegistry(p, root_key=ROOT)
        reg2.revoke("alice", "key compromise", by="bob")
        assert not store._registry().is_active("alice")
