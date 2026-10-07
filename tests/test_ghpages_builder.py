"""Tests for deploy/gh-pages/build-static.py (gh-pages-envs lane).

Covers: builder idempotence (two runs -> byte-identical), prod bundle
cleanliness (no fixtures), staging honesty strings, JSON validity.
Fast (~5s): builds twice against a private copy of the deterministic
demo dataset (--data-dir hermetic; never touches the shared dist-data).
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILDER = os.path.join(REPO, "deploy", "gh-pages", "build-static.py")


def _build(out: str, data_dir: str) -> None:
    subprocess.run(
        [sys.executable, BUILDER, "--out", out, "--data-dir", data_dir],
        check=True, cwd=REPO, capture_output=True, text=True, timeout=300,
    )


def _tree_files(root: str):
    out = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            out.append(os.path.relpath(os.path.join(dirpath, f), root))
    return sorted(out)


class TestGhPagesBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sentinel-ghpages-test-")
        # T1 hermeticity: --skip-data reads the shared deploy/dist-data,
        # which a concurrent full build rebuilds (build-data.sh deletes
        # demo.db) — that corrupted these tests intermittently. Copy the
        # dataset once into the tmp dir and point the builder at the copy.
        dist = os.path.join(REPO, "deploy", "dist-data")
        if not os.path.isdir(dist):
            # dist-data is gitignored build output: rebuild it
            # deterministically (no network, no Jev key) so the test is
            # self-healing on fresh clones instead of erroring.
            spec = importlib.util.spec_from_file_location(
                "build_static_for_test", BUILDER)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.build_data()
        cls.data = os.path.join(cls.tmp, "data")
        shutil.copytree(dist, cls.data)
        cls.a = os.path.join(cls.tmp, "a")
        cls.b = os.path.join(cls.tmp, "b")
        _build(cls.a, cls.data)
        _build(cls.b, cls.data)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_idempotent_two_runs_byte_identical(self):
        fa, fb = _tree_files(self.a), _tree_files(self.b)
        self.assertEqual(fa, fb, "file trees differ")
        for rel in fa:
            pa, pb = os.path.join(self.a, rel), os.path.join(self.b, rel)
            with open(pa, "rb") as f1, open(pb, "rb") as f2:
                self.assertEqual(f1.read(), f2.read(), f"{rel} differs")

    def test_staging_has_prerendered_api(self):
        api = os.path.join(self.a, "staging", "api")
        for rel in ("decisions.json", "calibration.json", "noise.json",
                    "flips.json", "shadow.json", "stream-snapshot.json",
                    "simulate.json"):
            self.assertTrue(os.path.exists(os.path.join(api, rel)), rel)
        details = os.listdir(os.path.join(api, "decision"))
        self.assertGreater(len(details), 10, "expected per-decision files")
        for scenario in ("default", "conservative", "aggressive"):
            p = os.path.join(api, "simulate", f"{scenario}.json")
            self.assertTrue(os.path.exists(p), scenario)
            with open(p) as f:
                env = json.load(f)
            prov = env["data"]["provenance"]
            self.assertTrue(prov.get("precomputed"), scenario)
            self.assertIn("scenario_thresholds", env["data"], scenario)

    def test_staging_config_is_static(self):
        with open(os.path.join(self.a, "staging", "assets", "config.js")) as f:
            self.assertIn("window.SENTINEL_DATA_MODE='static'", f.read())

    def test_staging_honesty_strings_present(self):
        blob = ""
        for root, _, files in os.walk(os.path.join(self.a, "staging")):
            for fn in files:
                if fn.endswith((".js", ".html")):
                    with open(os.path.join(root, fn), encoding="utf-8") as f:
                        blob += f.read()
        for s in ("SIMULATED SHOWCASE", "snapshot — not live"):
            self.assertIn(s, blob, s)

    def test_prod_has_no_fixtures(self):
        prod = os.path.join(self.a, "prod")
        self.assertFalse(os.path.exists(os.path.join(prod, "data")))
        self.assertFalse(os.path.exists(os.path.join(prod, "api")))
        for rel in _tree_files(prod):
            self.assertFalse(rel.endswith(".json"), f"fixture JSON in prod: {rel}")
        with open(os.path.join(prod, "assets", "config.js")) as f:
            self.assertIn("window.SENTINEL_DATA_MODE='live'", f.read())
        self.assertTrue(os.path.exists(
            os.path.join(prod, "assets", "views-setup.js")))

    def test_prod_no_server_env_leak(self):
        for rel in _tree_files(os.path.join(self.a, "prod")):
            p = os.path.join(self.a, "prod", rel)
            try:
                with open(p, encoding="utf-8") as f:
                    text = f.read()
            except (UnicodeDecodeError, ValueError):
                continue
            self.assertNotIn("SENTINEL_SIMULATED_PAGING", text, rel)


if __name__ == "__main__":
    unittest.main()
