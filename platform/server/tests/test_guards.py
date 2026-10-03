"""Tests for platform.server.shed + datasets + import/read-only guards."""

import os
import sys
import tempfile
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # platform/server for _pkg
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
import _pkg

shed_mod = _pkg.load("shed")
AdmissionGate, DegradePolicy = shed_mod.AdmissionGate, shed_mod.DegradePolicy


class TestAdmissionGate(unittest.TestCase):
    def test_bounded_and_fails_fast(self):
        gate = AdmissionGate(max_inflight=2)
        self.assertTrue(gate.try_acquire())
        self.assertTrue(gate.try_acquire())
        self.assertFalse(gate.try_acquire())  # no queueing: fail fast
        self.assertEqual(gate.shed_total, 1)
        gate.release()
        self.assertTrue(gate.try_acquire())
        gate.release()
        gate.release()

    def test_active_count(self):
        gate = AdmissionGate(max_inflight=3)
        gate.try_acquire()
        gate.try_acquire()
        self.assertEqual(gate.active, 2)
        gate.release()
        self.assertEqual(gate.active, 1)
        gate.release()


class TestDegradePolicy(unittest.TestCase):
    def test_expensive_sheds_when_hot(self):
        pol = DegradePolicy(shed_load=1.0)
        shed, reason = pol.should_shed("/api/calibration", load=5.0)
        self.assertTrue(shed)
        self.assertIn("shedding", reason)
        self.assertEqual(pol.shed_total, 1)

    def test_cheap_never_sheds(self):
        pol = DegradePolicy(shed_load=1.0)
        for path in ("/api/decisions", "/api/decision/3", "/api/stream", "/"):
            shed, _ = pol.should_shed(path, load=99.0)
            self.assertFalse(shed, path)

    def test_cool_box_no_shed(self):
        pol = DegradePolicy(shed_load=3.0)
        shed, _ = pol.should_shed("/api/simulate", load=0.2)
        self.assertFalse(shed)


class TestDatasetRegistry(unittest.TestCase):
    def test_labels_v3_deterministic(self):
        datasets_mod = _pkg.load("datasets")
        DatasetRegistry = datasets_mod.DatasetRegistry
        with tempfile.TemporaryDirectory() as d:
            r1 = DatasetRegistry(d)
            rows1, sha1, meta1 = r1.get("labels-v3")
            r2 = DatasetRegistry(d)  # fresh instance, same cache dir
            rows2, sha2, _ = r2.get("labels-v3")
            self.assertEqual(len(rows1), 2000)
            self.assertEqual(sha1, sha2)
            self.assertEqual(len(sha1), 64)
            self.assertEqual(rows1[0], rows2[0])
            self.assertEqual(meta1["n_alerts"], 2000)
            self.assertEqual(meta1["window_days"], 30)
            # tuner-shaped rows
            row = rows1[0]
            for key in ("fingerprint", "q1_probs", "q3_confidence",
                        "became_sev12", "would_page_baseline"):
                self.assertIn(key, row)

    def test_unknown_version(self):
        datasets_mod = _pkg.load("datasets")
        DatasetRegistry = datasets_mod.DatasetRegistry
        UnknownDataset = datasets_mod.UnknownDataset
        with tempfile.TemporaryDirectory() as d:
            reg = DatasetRegistry(d)
            with self.assertRaises(UnknownDataset):
                reg.get("labels-v9")


class TestNoJevClientImport(unittest.TestCase):
    """Grep guard: platform/server must never import the Jev client."""

    def test_no_client_import_anywhere(self):
        server_dir = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        offenders = []
        for name in sorted(os.listdir(server_dir)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(server_dir, name)
            with open(path) as fh:
                src = fh.read()
            for i, line in enumerate(src.splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if ("sentinel.client" in s or "sentinel_client" in s
                        or "from .client" in s or "import client" in s):
                    offenders.append(f"{name}:{i}: {line.strip()}")
        self.assertEqual(offenders, [],
                         f"Jev client import in serving path: {offenders}")

    def test_no_paging_write_path_imports(self):
        server_dir = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        banned = ("forwarder", "pd_sender", "secondary")
        offenders = []
        for name in sorted(os.listdir(server_dir)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(server_dir, name)
            with open(path) as fh:
                src = fh.read()
            for i, line in enumerate(src.splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                for mod in banned:
                    if f"sentinel.{mod}" in s or f"from .{mod}" in s:
                        offenders.append(f"{name}:{i}: {line.strip()}")
        self.assertEqual(offenders, [],
                         f"paging write-path import in serving path: "
                         f"{offenders}")


class TestReadOnlyDb(unittest.TestCase):
    def test_write_attempt_fails(self):
        import sqlite3
        sys.path.insert(0, _HERE)  # tests dir for fixtures
        from fixtures import make_store_db
        store_mod = _pkg.load("store")
        db = make_store_db()
        self.addCleanup(os.unlink, db)
        store = store_mod.ReadStore(db)
        conn = store._connect()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE evil(x)")
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO events (type) VALUES ('x')")
        finally:
            conn.close()

    def test_client_module_never_loaded_by_server_modules(self):
        # Import every server module fresh (via the _pkg alias) and assert
        # sentinel.client was not pulled in transitively.
        import subprocess
        server_dir = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        repo = os.path.dirname(os.path.dirname(server_dir))
        code = (
            "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r);"
            "import _pkg;"
            " _pkg.load('app'); _pkg.load('store');"
            " _pkg.load('datasets'); _pkg.load('simulate');"
            " _pkg.load('shed');"
            "print('client' in sys.modules)"
        )
        out = subprocess.run(
            [sys.executable, "-c",
             code % (server_dir, os.path.join(repo, "src"))],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "False",
                         f"stderr: {out.stderr[-1000:]}")


if __name__ == "__main__":
    unittest.main()
