"""Tests for the platform-tier rotation story (Track 4, contract C4).

Covers: the keystore twin's ceremony, the operator bearer token seam
(C1 — bootstrap-once, dual-accept, rotation), the RotationService stage
dispatch (POST /api/v1/keys/{name}/rotate contract), and twin format
compatibility with the engine implementation.
"""

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.dirname(_HERE)                      # platform/server
_REPO = os.path.dirname(os.path.dirname(_SERVER))     # repo root
sys.path.insert(0, _SERVER)  # _pkg
sys.path.insert(0, os.path.join(_REPO, "src"))  # sentinel.* (engine)
import _pkg

keystore = _pkg.load("keystore")
rotation_api = _pkg.load("rotation_api")
plat_integrations = _pkg.load("integrations")

from sentinel import keystore as eng_keystore  # engine twin, format check

TEST_OLD = "platform-old-secret-01"
TEST_NEW = "platform-new-secret-02"


class AuditCollector:
    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(dict(event))


class PlatformStoreBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t4-plat-")
        self.audit = AuditCollector()

    def make_store(self, name="ks.json", **kw):
        kw.setdefault("audit", self.audit)
        return keystore.RotatingKeyStore(os.path.join(self.tmp, name), **kw)

    def tearDown(self):
        for root, _dirs, files in os.walk(self.tmp):
            for f in files:
                os.unlink(os.path.join(root, f))
        os.rmdir(self.tmp)


class TestTwinCeremony(PlatformStoreBase):
    def test_overlap_and_post_retire(self):
        ks = self.make_store()
        ks.stage_secondary("k", TEST_OLD, actor="op")
        ks.stage_secondary("k", TEST_NEW, actor="op")
        self.assertTrue(ks.verify("k", TEST_OLD)[0])
        self.assertTrue(ks.verify("k", TEST_NEW)[0])
        ks.promote("k", actor="op")
        self.assertEqual(ks.generation("k"), 2)
        ks.retire("k", actor="op")
        self.assertFalse(ks.verify("k", TEST_OLD)[0])
        self.assertTrue(ks.verify("k", TEST_NEW)[0])

    def test_audit_shape(self):
        ks = self.make_store()
        ks.stage_secondary("k", TEST_OLD, actor="carol")
        ks.stage_secondary("k", TEST_NEW, actor="carol")
        ks.promote("k", actor="carol")
        for e in self.audit.events:
            for field in ("ts", "actor", "action", "secret_name",
                          "generation_before", "generation_after"):
                self.assertIn(field, e)
            self.assertEqual(e["actor"], "carol")
        blob = json.dumps(self.audit.events)
        self.assertNotIn(TEST_OLD, blob)
        self.assertNotIn(TEST_NEW, blob)

    def test_breakglass_epoch_binding(self):
        ks = self.make_store()
        ks.stage_secondary("k", TEST_OLD, actor="op")
        token, _tid = ks.issue_breakglass("k", actor="op")
        self.assertTrue(ks.verify("k", token)[0])
        ks.stage_secondary("k", TEST_NEW, actor="op")
        ks.promote("k", actor="op")
        self.assertFalse(ks.verify("k", token)[0])

    def test_twin_format_compatible_with_engine(self):
        """The shared surface: a record written by the ENGINE keystore
        must verify under the PLATFORM twin and vice versa."""
        path = os.path.join(self.tmp, "shared.json")
        eng = eng_keystore.RotatingKeyStore(path, audit=self.audit)
        eng.stage_secondary("k", TEST_OLD, actor="op")
        eng.stage_secondary("k", TEST_NEW, actor="op")
        plat = keystore.RotatingKeyStore(path, audit=self.audit)
        self.assertTrue(plat.verify("k", TEST_OLD)[0])
        self.assertTrue(plat.verify("k", TEST_NEW)[0])
        self.assertEqual(plat.generation("k"), 1)
        plat.promote("k", actor="op")
        eng2 = eng_keystore.RotatingKeyStore(path, audit=self.audit)
        self.assertEqual(eng2.generation("k"), 2)
        self.assertTrue(eng2.verify("k", TEST_NEW)[0])


class TestOperatorToken(PlatformStoreBase):
    def test_bootstrap_once(self):
        path = os.path.join(self.tmp, "op.json")
        store = keystore.operator_token_store(path, audit=self.audit)
        token, is_new = keystore.ensure_operator_token(store)
        self.assertTrue(is_new)
        self.assertGreaterEqual(len(token), 16)
        # second call returns the SAME token, no mint
        token2, is_new2 = keystore.ensure_operator_token(store)
        self.assertFalse(is_new2)
        self.assertEqual(token, token2)
        # bootstrap is audited without the value
        blob = json.dumps(self.audit.events)
        self.assertNotIn(token, blob)
        boots = [e for e in self.audit.events if e["action"] == "bootstrap"]
        self.assertEqual(len(boots), 1)
        self.assertEqual(boots[0]["generation_after"], 1)

    def test_preseed_env(self):
        old = os.environ.get(keystore.OPERATOR_TOKEN_ENV)
        os.environ[keystore.OPERATOR_TOKEN_ENV] = "preseeded-token-value-1"
        try:
            store = keystore.operator_token_store(
                os.path.join(self.tmp, "op2.json"), audit=self.audit)
            token, is_new = keystore.ensure_operator_token(store)
            self.assertTrue(is_new)
            self.assertEqual(token, "preseeded-token-value-1")
        finally:
            if old is None:
                del os.environ[keystore.OPERATOR_TOKEN_ENV]
            else:
                os.environ[keystore.OPERATOR_TOKEN_ENV] = old

    def test_operator_token_rotation(self):
        """C1 seam: Track 1 middleware verifies via dual-accept; rotation
        never breaks the live token."""
        store = keystore.operator_token_store(
            os.path.join(self.tmp, "op3.json"), audit=self.audit)
        old, _ = keystore.ensure_operator_token(store)
        new = "rotated-operator-token-00000002"
        name = keystore.OPERATOR_TOKEN_NAME
        store.stage_secondary(name, new, actor="op")
        self.assertTrue(store.verify(name, old)[0])   # live token works
        self.assertTrue(store.verify(name, new)[0])   # staged works
        store.promote(name, actor="op")
        store.retire(name, actor="op")
        self.assertFalse(store.verify(name, old)[0])
        self.assertTrue(store.verify(name, new)[0])


class TestRotationService(PlatformStoreBase):
    def setUp(self):
        super().setUp()
        self.store = self.make_store("svc.json")
        self.name = "operator_bearer"
        self.svc = rotation_api.RotationService({self.name: self.store})

    def test_full_stage_dispatch(self):
        code, body = self.svc.handle(self.name, "stage_secondary",
                                     {"value": TEST_OLD}, actor="op")
        self.assertEqual(code, 200)
        self.assertEqual(body["generation"], 1)
        code, body = self.svc.handle(self.name, "stage_secondary",
                                     {"value": TEST_NEW}, actor="op")
        self.assertEqual(code, 200)
        self.assertTrue(body["secondary_staged"])
        code, body = self.svc.handle(self.name, "verify_secondary",
                                     {"value": TEST_NEW}, actor="op")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        code, body = self.svc.handle(self.name, "verify_secondary",
                                     {"value": "wrong"}, actor="op")
        self.assertEqual(code, 422)
        self.assertFalse(body["ok"])
        code, body = self.svc.handle(self.name, "promote", {}, actor="op")
        self.assertEqual(code, 200)
        self.assertEqual((body["generation_before"],
                          body["generation_after"]), (1, 2))
        code, body = self.svc.handle(self.name, "retire", {}, actor="op")
        self.assertEqual(code, 200)
        self.assertTrue(body["retired"])
        code, body = self.svc.handle(self.name, "status", {}, actor="op")
        self.assertEqual(code, 200)
        self.assertEqual(body["generation"], 2)

    def test_error_shapes(self):
        code, body = self.svc.handle("nope", "status", {}, actor="op")
        self.assertEqual(code, 404)
        self.assertIn("error", body)
        code, body = self.svc.handle(self.name, "bogus", {}, actor="op")
        self.assertEqual(code, 400)
        code, body = self.svc.handle(self.name, "promote", {}, actor="op")
        self.assertEqual(code, 409)  # no staged secondary
        code, body = self.svc.handle(self.name, "stage_secondary",
                                     {}, actor="op")
        self.assertEqual(code, 422)  # missing value
        code, body = self.svc.handle(self.name, "stage_secondary",
                                     {"value": "x"}, actor="op")
        self.assertEqual(code, 200)  # no validator on this store; ok

    def test_no_values_in_responses(self):
        self.svc.handle(self.name, "stage_secondary", {"value": TEST_OLD},
                        actor="op")
        self.svc.handle(self.name, "stage_secondary", {"value": TEST_NEW},
                        actor="op")
        code, body = self.svc.handle(self.name, "status", {}, actor="op")
        self.assertEqual(code, 200)
        blob = json.dumps(body)
        self.assertNotIn(TEST_OLD, blob)
        self.assertNotIn(TEST_NEW, blob)

    def test_status_all(self):
        self.svc.handle(self.name, "stage_secondary", {"value": TEST_OLD},
                        actor="op")
        overview = self.svc.status_all()
        self.assertTrue(overview[self.name]["configured"])
        self.assertNotIn(TEST_OLD, json.dumps(overview))


class TestPlatformBYOKTwin(PlatformStoreBase):
    def test_twin_ceremony_and_format(self):
        path = os.path.join(self.tmp, "integrations.json")
        s = plat_integrations.IntegrationStore(path=path)
        pd = "aa11" * 8
        pd2 = "bb22" * 8
        s.set(plat_integrations.PD_KEY_NAME, pd, actor="op")
        self.assertEqual(s.get(plat_integrations.PD_KEY_NAME), pd)
        s.stage_secondary(plat_integrations.PD_KEY_NAME, pd2, actor="op")
        self.assertTrue(s.verify(plat_integrations.PD_KEY_NAME, pd)[0])
        self.assertTrue(s.verify(plat_integrations.PD_KEY_NAME, pd2)[0])
        s.promote(plat_integrations.PD_KEY_NAME, actor="op")
        s.retire(plat_integrations.PD_KEY_NAME, actor="op")
        self.assertFalse(s.verify(plat_integrations.PD_KEY_NAME, pd)[0])
        st = s.status()[plat_integrations.PD_KEY_NAME]
        self.assertEqual(st["generation"], 2)
        self.assertEqual(st["last4"], pd2[-4:])
        # the engine twin reads the same file (shared format)
        import sys as _sys
        _sys.path.insert(0, os.path.join(_REPO, "src"))
        from sentinel import integrations as eng
        e = eng.IntegrationStore(path=path)
        self.assertEqual(e.get(eng.PD_KEY_NAME), pd2)
        self.assertEqual(e.generation(eng.PD_KEY_NAME), 2)


if __name__ == "__main__":
    unittest.main()
