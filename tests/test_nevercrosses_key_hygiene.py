"""Vault never-crosses rule for the C4/C6 contracts (2026-10-05).

Secrets must NEVER cross a contract boundary in plaintext: key references,
not key material; hashes, not raw identifiers. This battery mechanically
proves the producer-side half of that rule:

  1. The simulated paging paths never resolve the routing-key VALUE — they
     report the key SOURCE only. C6 claims "the simulated path (which never
     resolves a routing key; the key value is deliberately never
     materialized)"; the tests fail if any simulated path calls
     resolve_paging_key (monkeypatched to raise).
  2. A canary key set in the environment never appears in simulated-path
     stderr output, in the spill record written by the simulated degraded
     path, or in any returned structure.
  3. The enqueue-side mechanical guard: a frozen outbox payload containing
     a routing_key is refused (PayloadNotKeyless) — secrets never freeze
     to disk, so they can never ride a hash or a receipt.
  4. sanitize_error strips configured key VALUES from error strings before
     they can reach the receipt's error/error_class channels.

The schema-side half (no C4/C6 field can carry key material: only
routing_key_ref — a vault-path reference — and one-way hashes cross) is
attested in docs/decisions/2026-10-05-nevercrosses-c4-c6.md.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import sentinel.forwarder as fwd  # noqa: E402
from sentinel.forwarder import DurableForwarder, Forwarder  # noqa: E402
from sentinel.integrations import (  # noqa: E402
    IntegrationStore,
    paging_key_source,
    sanitize_error,
)
from sentinel.pd_sender import (  # noqa: E402
    PayloadNotKeyless,
    build_wire_event,
)

CANARY = "rk-canary-9f8e7d6c5b4a-never-a-real-key"


def _boom(*a, **k):
    raise AssertionError("simulated path must never resolve the key value")


class FakeStore:
    """Minimal stand-in for IntegrationStore.get (truthy branch)."""

    def __init__(self, value):
        self._value = value

    def get(self, name):
        return self._value


class TestPagingKeySource(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_env_source_without_value(self):
        os.environ["PD_ROUTING_KEY"] = CANARY
        src = paging_key_source(IntegrationStore(ephemeral=True))
        self.assertEqual(src, "env")
        self.assertNotEqual(src, CANARY)

    def test_user_source_without_value(self):
        os.environ.pop("PD_ROUTING_KEY", None)
        src = paging_key_source(FakeStore(CANARY))
        self.assertEqual(src, "user")
        self.assertNotEqual(src, CANARY)

    def test_unconfigured(self):
        os.environ.pop("PD_ROUTING_KEY", None)
        src = paging_key_source(IntegrationStore(ephemeral=True))
        self.assertEqual(src, "unconfigured")


class TestSimulatedPathsNeverResolveKey(unittest.TestCase):
    """Mechanical proof of the C6 'never resolves' claim: with
    resolve_paging_key rigged to explode, both simulated paths still work —
    and the canary never surfaces anywhere."""

    def setUp(self):
        self._env = dict(os.environ)
        os.environ["PD_ROUTING_KEY"] = CANARY
        os.environ["SENTINEL_SIMULATED_PAGING"] = "1"
        self._orig = fwd.resolve_paging_key
        fwd.resolve_paging_key = _boom

    def tearDown(self):
        fwd.resolve_paging_key = self._orig
        os.environ.clear()
        os.environ.update(self._env)

    def test_degraded_simulated_path(self):
        with tempfile.TemporaryDirectory() as spill_dir:
            fw = DurableForwarder.__new__(DurableForwarder)
            fw.config = SimpleNamespace(
                env="test", spill_dir=spill_dir,
                control_routing_key_ref="secret:pd/control_routing_key")
            buf = io.StringIO()
            with redirect_stderr(buf):
                outcome = fw.send_direct(
                    {"kind": "disk_dead", "summary": "outbox gone",
                     "alert_id": "a9"}, reason="test")
            self.assertEqual(outcome["pd_outcome"], "simulated")
            self.assertTrue(outcome["simulated"])
            err = buf.getvalue()
            self.assertIn("key_source=env", err)
            self.assertNotIn(CANARY, err)
            # The spill record written by the simulated path.
            spills = os.listdir(spill_dir)
            self.assertEqual(len(spills), 1)
            with open(os.path.join(spill_dir, spills[0])) as fh:
                record = fh.read()
            self.assertNotIn(CANARY, record)
            parsed = json.loads(record)
            self.assertTrue(parsed.get("simulated"))

    def test_legacy_simulated_send(self):
        fw = Forwarder.__new__(Forwarder)
        fw._key_resolver = None
        fw.metrics = {}
        buf = io.StringIO()
        with redirect_stderr(buf):
            res = fw._simulated_send("page_now", "dk-canary", "alert-1")
        self.assertTrue(res.simulated)
        err = buf.getvalue()
        self.assertIn("SIMULATED PAGE", err)
        self.assertIn("key_source=env", err)
        self.assertNotIn(CANARY, err)


class TestEnqueueSideGuards(unittest.TestCase):
    def test_no_c4_c6_fixture_carries_bare_routing_key(self):
        # Structural negative guard: no checked-in C4/C6 fixture may
        # contain a bare "routing_key" field. The only key-adjacent shapes
        # allowed on these contracts are routing_key_ref (a reference) and
        # the one-way hashes (wire_sha256, spill_id).
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "contracts")
        checked = 0
        for dirpath, _, filenames in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            if not (rel.startswith("act") or rel.startswith("observe")):
                continue
            for fn in filenames:
                if not fn.endswith(".json"):
                    continue
                checked += 1
                with open(os.path.join(dirpath, fn)) as fh:
                    doc = json.load(fh)
                found = []

                def walk(node, path="$"):
                    if isinstance(node, dict):
                        for k, v in node.items():
                            if k == "routing_key":
                                found.append(path)
                            walk(v, f"{path}.{k}")
                    elif isinstance(node, list):
                        for i, v in enumerate(node):
                            walk(v, f"{path}[{i}]")

                walk(doc)
                self.assertEqual(
                    found, [],
                    f"bare routing_key field in {rel}/{fn}: {found}")
        self.assertGreater(checked, 0, "expected to scan C4/C6 fixtures")

    def test_frozen_payload_with_key_is_refused(self):
        # The mechanical Vault enforcement: a secret frozen to disk is an
        # enqueue-side bug, refused at send time — so it can never ride a
        # wire hash, a spill, or a receipt.
        frozen = json.dumps({"event_action": "trigger",
                             "routing_key": CANARY}).encode()
        with self.assertRaises(PayloadNotKeyless):
            build_wire_event(frozen, dedup_key="dk", routing_key=CANARY)

    def test_wire_sha256_never_embeds_key(self):
        import hashlib
        from sentinel.pd_sender import PagerDutyClient
        client = PagerDutyClient()
        frozen = json.dumps({"event_action": "trigger",
                             "payload": {"summary": "x"}}).encode()
        # Point at a sink that never opens a socket.
        client.endpoint = "http://127.0.0.1:1/nope"
        res = client.send(payload_frozen=frozen, dedup_key="dk",
                          routing_key=CANARY)
        self.assertRegex(res.wire_sha256, r"^[0-9a-f]{64}$")
        self.assertNotIn(CANARY, res.wire_sha256)
        self.assertNotIn(CANARY, repr(res))
        # The network error must not carry the key either (the body, where
        # the key lives, never enters the error string).
        self.assertNotIn(CANARY, res.error or "")
        self.assertNotIn(CANARY, res.error_class)

    def test_sanitize_error_strips_key_values(self):
        os.environ["PD_ROUTING_KEY"] = CANARY
        try:
            cleaned = sanitize_error(
                f"connection reset while posting {CANARY} to vendor")
            self.assertNotIn(CANARY, cleaned)
            self.assertIn("[REDACTED]", cleaned)
        finally:
            del os.environ["PD_ROUTING_KEY"]


if __name__ == "__main__":
    unittest.main()
