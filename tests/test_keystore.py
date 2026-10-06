"""Tests for the rotation story (Track 4, contract C4).

Covers: the four-step ceremony (overlap accepted, post-retire rejected),
audit entries (actor, timestamp, generation before/after), generation
increments, break-glass epoch binding, the delete-then-add documented
failure, legacy format migration, webhook dual-accept, BYOK ceremony, and
the no-secret-material-in-logs invariant.

Fake secrets throughout (TEST_OLD/TEST_NEW); nothing real.
"""

import hashlib
import hmac
import json
import os
import stat
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sentinel.keystore import (
    RotatingKeyStore,
    WebhookSecretStore,
    RotationError,
    decode_record,
    utcnow_iso,
)
from sentinel import integrations as eng

TEST_OLD = "old-secret-value-0001"
TEST_NEW = "new-secret-value-0002"
TEST_BG_LABEL = "incident-1234"


class AuditCollector:
    """In-memory audit sink for tests."""

    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(dict(event))

    def actions(self):
        return [e["action"] for e in self.events]

    def for_action(self, action):
        return [e for e in self.events if e["action"] == action]


class KeystoreTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t4-ks-")
        self.audit = AuditCollector()

    def make_store(self, name="s.json", **kw):
        kw.setdefault("audit", self.audit)
        return RotatingKeyStore(os.path.join(self.tmp, name), **kw)

    def tearDown(self):
        for root, _dirs, files in os.walk(self.tmp):
            for f in files:
                os.unlink(os.path.join(root, f))
        os.rmdir(self.tmp)


class TestCeremony(KeystoreTestBase):
    def test_overlap_both_accepted(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")   # bootstrap
        ks.stage_secondary("api", TEST_NEW, actor="op")   # stage successor
        ok_old, via_old = ks.verify("api", TEST_OLD)
        ok_new, via_new = ks.verify("api", TEST_NEW)
        self.assertTrue(ok_old)
        self.assertEqual(via_old, "primary")
        self.assertTrue(ok_new)
        self.assertEqual(via_new, "secondary")

    def test_full_ceremony_old_rejected_after_retire(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        ks.stage_secondary("api", TEST_NEW, actor="op")
        ks.verify_secondary("api", TEST_NEW, actor="op")
        ks.promote("api", actor="op")
        # during grace both still verify (in-flight drain)
        self.assertTrue(ks.verify("api", TEST_OLD)[0])
        self.assertTrue(ks.verify("api", TEST_NEW)[0])
        ks.retire("api", actor="op")
        ok_old, _ = ks.verify("api", TEST_OLD)
        ok_new, via_new = ks.verify("api", TEST_NEW)
        self.assertFalse(ok_old, "old secret must be dead after retire")
        self.assertTrue(ok_new)
        self.assertEqual(via_new, "primary")

    def test_generation_increments_exactly_once_at_promote(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        self.assertEqual(ks.generation("api"), 1)
        ks.stage_secondary("api", TEST_NEW, actor="op")
        self.assertEqual(ks.generation("api"), 1)
        ks.verify_secondary("api", TEST_NEW, actor="op")
        self.assertEqual(ks.generation("api"), 1)
        ks.promote("api", actor="op")
        self.assertEqual(ks.generation("api"), 2)
        ks.retire("api", actor="op")
        self.assertEqual(ks.generation("api"), 2)

    def test_each_step_audit_logged(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="alice")
        ks.stage_secondary("api", TEST_NEW, actor="alice")
        ks.verify_secondary("api", TEST_NEW, actor="alice")
        ks.promote("api", actor="alice")
        ks.retire("api", actor="alice")
        # bootstrap + 4 ceremony steps
        by_action = {}
        for e in self.audit.events:
            by_action.setdefault(e["action"], []).append(e)
        for action in ("stage_secondary", "verify_secondary", "promote",
                       "retire"):
            self.assertIn(action, by_action, f"missing audit for {action}")
            for e in by_action[action]:
                self.assertEqual(e["actor"], "alice")
                self.assertEqual(e["secret_name"], "api")
                self.assertIn("ts", e)
                self.assertIn("generation_before", e)
                self.assertIn("generation_after", e)
        # only promote moves the generation
        self.assertEqual(by_action["promote"][0]["generation_before"], 1)
        self.assertEqual(by_action["promote"][0]["generation_after"], 2)
        for action in ("stage_secondary", "verify_secondary", "retire"):
            for e in by_action[action]:
                self.assertEqual(e["generation_before"],
                                 e["generation_after"])

    def test_no_secret_material_in_audit(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        ks.stage_secondary("api", TEST_NEW, actor="op")
        ks.verify_secondary("api", TEST_NEW, actor="op")
        ks.promote("api", actor="op")
        token, _tid = ks.issue_breakglass("api", actor="op")
        ks.retire("api", actor="op")
        blob = json.dumps(self.audit.events)
        for secret in (TEST_OLD, TEST_NEW, token):
            self.assertNotIn(secret, blob,
                             "secret material leaked into audit trail")

    def test_promote_requires_staged_secondary(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        with self.assertRaises(RotationError):
            ks.promote("api", actor="op")

    def test_retire_requires_grace_slot(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        with self.assertRaises(RotationError):
            ks.retire("api", actor="op")

    def test_stage_identical_to_primary_rejected(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        with self.assertRaises(RotationError):
            ks.stage_secondary("api", TEST_OLD, actor="op")

    def test_verify_secondary_proof_of_work(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        ks.stage_secondary("api", TEST_NEW, actor="op")
        good = ks.verify_secondary("api", TEST_NEW, actor="op")
        bad = ks.verify_secondary("api", "wrong-value", actor="op")
        self.assertTrue(good["ok"])
        self.assertFalse(bad["ok"])
        # the failed proof is audited too
        fails = [e for e in self.audit.for_action("verify_secondary")
                 if "FAILED" in e["detail"]]
        self.assertEqual(len(fails), 1)

    def test_set_immediate_is_audited_escape_hatch(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        ks.set_immediate("api", TEST_NEW, actor="op", reason="leak")
        self.assertTrue(ks.verify("api", TEST_NEW)[0])
        self.assertFalse(ks.verify("api", TEST_OLD)[0])
        self.assertEqual(ks.generation("api"), 2)
        ev = self.audit.for_action("set_immediate")[0]
        self.assertEqual(ev["generation_before"], 1)
        self.assertEqual(ev["generation_after"], 2)

    def test_candidates_primary_first(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        self.assertEqual(ks.candidates("api"), [TEST_OLD])
        ks.stage_secondary("api", TEST_NEW, actor="op")
        self.assertEqual(ks.candidates("api"), [TEST_OLD, TEST_NEW])

    def test_status_never_includes_values(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        st = ks.status("api")
        self.assertTrue(st["configured"])
        self.assertFalse(st["secondary_staged"])
        self.assertEqual(st["generation"], 1)
        blob = json.dumps(st)
        self.assertNotIn(TEST_OLD, blob)
        ks.stage_secondary("api", TEST_NEW, actor="op")
        self.assertTrue(ks.status("api")["secondary_staged"])

    def test_store_file_is_0600(self):
        path = os.path.join(self.tmp, "perm.json")
        RotatingKeyStore(path, audit=self.audit).stage_secondary(
            "api", TEST_OLD, actor="op")
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o600)


class TestBreakGlass(KeystoreTestBase):
    def test_issued_token_verifies_at_its_generation(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        token, tid = ks.issue_breakglass("api", actor="op",
                                         label=TEST_BG_LABEL)
        self.assertTrue(token.startswith("bg_"))
        ok, via = ks.verify("api", token)
        self.assertTrue(ok)
        self.assertEqual(via, f"issued:{tid}")
        # only the hash is stored — the raw token is shown once
        data = json.load(open(os.path.join(self.tmp, "s.json")))
        self.assertNotIn(token, json.dumps(data))

    def test_leaked_pre_rotation_token_dies_at_next_generation(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        token, _tid = ks.issue_breakglass("api", actor="op")
        self.assertTrue(ks.verify("api", token)[0])
        ks.stage_secondary("api", TEST_NEW, actor="op")
        ks.promote("api", actor="op")          # generation 1 -> 2
        ok, _ = ks.verify("api", token)
        self.assertFalse(ok, "pre-rotation break-glass token must die "
                             "at the next generation")

    def test_explicit_revoke_kills_mid_generation(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        token, tid = ks.issue_breakglass("api", actor="op")
        ks.revoke_breakglass("api", tid, actor="op")
        self.assertFalse(ks.verify("api", token)[0])

    def test_breakglass_audit_has_no_token_value(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        token, _tid = ks.issue_breakglass("api", actor="op")
        blob = json.dumps(self.audit.events)
        self.assertNotIn(token, blob)


class TestDeleteThenAddFailure(KeystoreTestBase):
    def test_delete_then_add_has_total_outage_window(self):
        """The documented failure: delete-then-add passes through a state
        where NO credential verifies — every in-flight request fails."""
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        # operator deletes the old secret before the new one exists
        data = ks._read_file()
        del data["records"]["api"]
        ks._write_file(data)
        # outage window: neither old nor new verifies
        self.assertFalse(ks.verify("api", TEST_OLD)[0])
        self.assertFalse(ks.verify("api", TEST_NEW)[0])
        # ...then the new secret is added (too late for in-flight traffic)
        ks.stage_secondary("api", TEST_NEW, actor="op")
        self.assertTrue(ks.verify("api", TEST_NEW)[0])

    def test_ceremony_never_enters_outage(self):
        """Contrast: at every ceremony step at least one valid credential
        verifies — the overlap invariant."""
        ks = self.make_store()
        valid = {TEST_OLD, TEST_NEW}
        ks.stage_secondary("api", TEST_OLD, actor="op")
        self.assertTrue(any(ks.verify("api", c)[0] for c in valid))
        ks.stage_secondary("api", TEST_NEW, actor="op")
        self.assertTrue(any(ks.verify("api", c)[0] for c in valid))
        ks.verify_secondary("api", TEST_NEW, actor="op")
        self.assertTrue(any(ks.verify("api", c)[0] for c in valid))
        ks.promote("api", actor="op")
        self.assertTrue(any(ks.verify("api", c)[0] for c in valid))
        ks.retire("api", actor="op")
        self.assertTrue(ks.verify("api", TEST_NEW)[0])


class TestLegacyMigration(KeystoreTestBase):
    def test_legacy_plain_string_decodes_to_gen1(self):
        path = os.path.join(self.tmp, "legacy.json")
        with open(path, "w") as f:
            json.dump({"records": {"api": TEST_OLD}}, f)
        ks = RotatingKeyStore(path, audit=self.audit)
        self.assertTrue(ks.verify("api", TEST_OLD)[0])
        self.assertEqual(ks.generation("api"), 1)
        self.assertEqual(decode_record(TEST_OLD)["secondary"], None)

    def test_malformed_record_rejected(self):
        with self.assertRaises(ValueError):
            decode_record({"nope": 1})


class TestConstantTime(KeystoreTestBase):
    def test_wrong_length_and_wrong_value_both_false(self):
        ks = self.make_store()
        ks.stage_secondary("api", TEST_OLD, actor="op")
        for cand in ("", "x", "x" * 500, TEST_OLD + "x", "old-secret-value"):
            ok, via = ks.verify("api", cand)
            self.assertFalse(ok)
            self.assertIsNone(via)


def _sign(secret: str, body: bytes, ts: int) -> dict:
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body,
                   hashlib.sha256).hexdigest()
    return {"X-Sentinel-Signature": f"sha256={mac}",
            "X-Sentinel-Timestamp": str(ts)}


class TestWebhookDualAccept(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t4-wh-")
        self.audit = AuditCollector()
        self.store = WebhookSecretStore(
            os.path.join(self.tmp, "wh.json"), audit=self.audit)

    def tearDown(self):
        for root, _dirs, files in os.walk(self.tmp):
            for f in files:
                os.unlink(os.path.join(root, f))
        os.rmdir(self.tmp)

    def _check(self, body: bytes, headers: dict, secret: str | None = None,
               onboarding: bool = False):
        import time
        from sentinel.receiver import (
            _webhook_sig_failure_reason_any, _webhook_candidates,
            ReceiverConfig,
        )
        cfg = ReceiverConfig(webhook_secret=secret,
                             webhook_secret_store=self.store,
                             webhook_onboarding=onboarding)
        return _webhook_sig_failure_reason_any(
            _webhook_candidates(cfg), onboarding, headers, body)

    def test_overlap_accepts_old_and_new_signatures(self):
        import time
        body = b'{"alert": 1}'
        ts = int(time.time())
        self.store.stage_secondary("webhook_hmac", "old-webhook-secret-00",
                                   actor="op")
        self.store.stage_secondary("webhook_hmac", "new-webhook-secret-00",
                                   actor="op")
        old_h = _sign("old-webhook-secret-00", body, ts)
        new_h = _sign("new-webhook-secret-00", body, ts)
        self.assertIsNone(self._check(body, old_h))
        self.assertIsNone(self._check(body, new_h))
        bad_h = _sign("attacker-secret-000000", body, ts)
        self.assertEqual(self._check(body, bad_h), "bad_mac")

    def test_post_retire_only_new_verifies(self):
        import time
        body = b'{"alert": 1}'
        ts = int(time.time())
        self.store.stage_secondary("webhook_hmac", "old-webhook-secret-00",
                                   actor="op")
        self.store.stage_secondary("webhook_hmac", "new-webhook-secret-00",
                                   actor="op")
        self.store.promote("webhook_hmac", actor="op")
        self.store.retire("webhook_hmac", actor="op")
        old_h = _sign("old-webhook-secret-00", body, ts)
        new_h = _sign("new-webhook-secret-00", body, ts)
        self.assertEqual(self._check(body, old_h), "bad_mac")
        self.assertIsNone(self._check(body, new_h))

    def test_env_bootstrap_fallback(self):
        import time
        body = b'{"alert": 1}'
        ts = int(time.time())
        headers = _sign("env-fallback-secret-0", body, ts)
        # store empty, env-style legacy secret on the config
        self.assertIsNone(self._check(body, headers,
                                      secret="env-fallback-secret-0"))
        # store record wins when present
        self.store.stage_secondary("webhook_hmac", "stored-secret-000000",
                                   actor="op")
        stored_h = _sign("stored-secret-000000", body, ts)
        self.assertIsNone(self._check(body, stored_h,
                                      secret="env-fallback-secret-0"))

    def test_weak_secret_rejected(self):
        with self.assertRaises(ValueError):
            self.store.stage_secondary("webhook_hmac", "short", actor="op")


class TestBYOKCeremony(unittest.TestCase):
    PD = "aa11" * 8
    PD2 = "bb22" * 8

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t4-byok-")
        self.path = os.path.join(self.tmp, "integrations.json")
        self.store = eng.IntegrationStore(path=self.path)

    def tearDown(self):
        for root, _dirs, files in os.walk(self.tmp):
            for f in files:
                os.unlink(os.path.join(root, f))
        os.rmdir(self.tmp)

    def test_byok_rotation_ceremony(self):
        s = self.store
        s.set(eng.PD_KEY_NAME, self.PD, actor="op")
        self.assertEqual(s.get(eng.PD_KEY_NAME), self.PD)
        self.assertEqual(s.generation(eng.PD_KEY_NAME), 1)
        s.stage_secondary(eng.PD_KEY_NAME, self.PD2, actor="op")
        self.assertTrue(s.verify(eng.PD_KEY_NAME, self.PD)[0])
        self.assertTrue(s.verify(eng.PD_KEY_NAME, self.PD2)[0])
        st = s.status()[eng.PD_KEY_NAME]
        self.assertTrue(st["secondary_staged"])
        self.assertEqual(st["generation"], 1)
        self.assertEqual(st["last4"], self.PD[-4:])
        s.verify_secondary(eng.PD_KEY_NAME, self.PD2, actor="op")
        s.promote(eng.PD_KEY_NAME, actor="op")
        self.assertEqual(s.get(eng.PD_KEY_NAME), self.PD2)
        self.assertEqual(s.generation(eng.PD_KEY_NAME), 2)
        s.retire(eng.PD_KEY_NAME, actor="op")
        self.assertFalse(s.verify(eng.PD_KEY_NAME, self.PD)[0])
        self.assertTrue(s.verify(eng.PD_KEY_NAME, self.PD2)[0])
        self.assertFalse(s.status()[eng.PD_KEY_NAME]["secondary_staged"])

    def test_legacy_string_entry_migrates(self):
        with open(self.path, "w") as f:
            json.dump({eng.PD_KEY_NAME: self.PD}, f)
        s = eng.IntegrationStore(path=self.path)
        self.assertEqual(s.get(eng.PD_KEY_NAME), self.PD)
        self.assertEqual(s.generation(eng.PD_KEY_NAME), 1)
        # ceremony works on the migrated record
        s.stage_secondary(eng.PD_KEY_NAME, self.PD2, actor="op")
        self.assertTrue(s.verify(eng.PD_KEY_NAME, self.PD2)[0])

    def test_sanitize_redacts_staged_secondary(self):
        # sanitize_error() resolves the default-path store, so point it at
        # the test store via SENTINEL_INTEGRATIONS_FILE.
        old = os.environ.get(eng.ENV_INTEGRATIONS_FILE)
        os.environ[eng.ENV_INTEGRATIONS_FILE] = self.path
        try:
            s = self.store
            s.set(eng.PD_KEY_NAME, self.PD, actor="op")
            s.stage_secondary(eng.PD_KEY_NAME, self.PD2, actor="op")
            err = (f"boom {self.PD} and also {self.PD2} leaked")
            clean = eng.sanitize_error(err)
            self.assertNotIn(self.PD, clean)
            self.assertNotIn(self.PD2, clean)
        finally:
            if old is None:
                del os.environ[eng.ENV_INTEGRATIONS_FILE]
            else:
                os.environ[eng.ENV_INTEGRATIONS_FILE] = old

    def test_set_many_still_atomic_on_validation(self):
        s = self.store
        with self.assertRaises(Exception):
            s.set_many({eng.PD_KEY_NAME: self.PD,
                        eng.JEV_KEY_NAME: "x"}, actor="op")
        self.assertIsNone(s.get(eng.PD_KEY_NAME),
                          "bad second key must not leave the first saved")

    def test_ephemeral_ceremony_fails_loud(self):
        from sentinel.integrations import EphemeralStoreError
        s = eng.IntegrationStore(path=self.path, ephemeral=True)
        with self.assertRaises(EphemeralStoreError):
            s.stage_secondary(eng.PD_KEY_NAME, self.PD, actor="op")


if __name__ == "__main__":
    unittest.main()
