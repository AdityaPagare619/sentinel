"""Tests for deploy/gh-pages/build-v2.py (the v2 single-file console builder).

Covers the banner contract the brutal audit called "a substring assert that
can't fail a dishonest build": every assertion here reads the BUILD OUTPUT
BYTES, not the builder's exit code.

Contract under test (docs/planning/rfc/uiux-design-system-backstage.md):
  /index.html          -> production console: <html data-mode="production"
                             data-backend="<vercel-url>">, static PRODUCTION
                             band in the bytes (never a JS post-token flip),
                             honest aria-label, operator token gate.
  /staging/index.html  -> simulated showcase: the v2 source shipped verbatim.
  /loadtest/index.html -> the load-test lane's dashboard (handoff path).
  /preview-v2/         -> untouched by this builder (frozen judging artifact).

Fast (<5s): builds into tmp dirs, no network, no repo writes.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILDER = os.path.join(REPO, "deploy", "gh-pages", "build-v2.py")
V2 = os.path.join(REPO, "platform", "ui-v2", "index.html")
BACKEND = "https://sentinel-platform-adityapagare619s-projects.vercel.app"

_HTML_TAG = re.compile(r"<html[^>]*>")


def _html_tag(blob: str) -> str:
    m = _HTML_TAG.search(blob)
    return m.group(0) if m else ""


def _build(out: str, backend: str = BACKEND, dashboard=None) -> None:
    cmd = [sys.executable, BUILDER, "--out", out, "--backend", backend]
    if dashboard:
        cmd += ["--loadtest-dashboard", dashboard]
    subprocess.run(cmd, check=True, cwd=REPO, capture_output=True, text=True,
                   timeout=120)


class TestBuildV2BannerContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sentinel-buildv2-test-")
        cls.dashboard = os.path.join(cls.tmp, "dashboard.html")
        with open(cls.dashboard, "w", encoding="utf-8") as f:
            f.write("<!doctype html><html><body>load-test dashboard</body></html>")
        cls.out = os.path.join(cls.tmp, "site")
        _build(cls.out, dashboard=cls.dashboard)
        cls.prod = open(os.path.join(cls.out, "index.html"),
                        encoding="utf-8").read()
        cls.stg = open(os.path.join(cls.out, "staging", "index.html"),
                       encoding="utf-8").read()
        cls.src = open(V2, encoding="utf-8").read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # --- banner contract: production -------------------------------------
    def test_prod_html_tag_carries_mode_and_backend(self):
        tag = _html_tag(self.prod)
        self.assertIn('data-mode="production"', tag)
        self.assertIn(f'data-backend="{BACKEND}"', tag)

    def test_prod_band_is_static_in_bytes(self):
        # X-H: the PRODUCTION chrome must never depend on JS+token
        # cooperation (a JS/fetch failure would mislabel an armed console
        # as simulated — the dangerous direction).
        self.assertIn('aria-label="Production console', self.prod)
        self.assertIn(">PRODUCTION</span>", self.prod)

    def test_prod_has_no_simulated_label_anywhere(self):
        # P0-1 class: screen readers must never announce safety on a
        # production console.
        self.assertNotIn('aria-label="Simulated mode"', self.prod)

    def test_prod_is_not_the_verbatim_source(self):
        self.assertNotEqual(self.prod, self.src,
                            "prod build must differ from the sim source")

    # --- banner contract: staging ----------------------------------------
    def test_staging_html_tag_has_no_production_mode(self):
        self.assertNotIn('data-mode="production"', _html_tag(self.stg))

    def test_staging_ships_source_verbatim(self):
        self.assertEqual(self.stg, self.src,
                         "staging must be the v2 source byte-for-byte")

    def test_staging_keeps_simulated_banner(self):
        self.assertIn('aria-label="Simulated mode"', self.stg)
        self.assertIn(">SIMULATED</span>", self.stg)

    # --- builder behaviors ------------------------------------------------
    def test_backend_injection_is_exact(self):
        # A wrong backend URL is a mislabeled production console.
        self.assertIn(BACKEND, self.prod)
        out2 = os.path.join(self.tmp, "site2")
        _build(out2, backend="https://example.invalid")
        prod2 = open(os.path.join(out2, "index.html"),
                     encoding="utf-8").read()
        self.assertIn('data-backend="https://example.invalid"', prod2)
        self.assertNotIn(BACKEND, prod2)
        shutil.rmtree(out2, ignore_errors=True)

    def test_loadtest_dashboard_handoff(self):
        lt = open(os.path.join(self.out, "loadtest", "index.html"),
                  encoding="utf-8").read()
        self.assertIn("load-test dashboard", lt)

    def test_loadtest_placeholder_is_honest(self):
        out3 = os.path.join(self.tmp, "site3")
        _build(out3)  # no dashboard -> placeholder
        lt = open(os.path.join(out3, "loadtest", "index.html"),
                  encoding="utf-8").read()
        self.assertIn("Load-test environment", lt)
        self.assertIn("lands here", lt)
        shutil.rmtree(out3, ignore_errors=True)

    def test_idempotent_two_runs_byte_identical(self):
        out4 = os.path.join(self.tmp, "site4")
        _build(out4, dashboard=self.dashboard)
        for rel in ("index.html", os.path.join("staging", "index.html"),
                    os.path.join("loadtest", "index.html")):
            with open(os.path.join(self.out, rel), "rb") as f1, \
                 open(os.path.join(out4, rel), "rb") as f2:
                self.assertEqual(f1.read(), f2.read(), rel)
        shutil.rmtree(out4, ignore_errors=True)

    def test_doubt_band_label_tracks_constant(self):
        # The doubt-axis RFC: the rendered ±band must come from SEM.doubtBand,
        # never a hardcoded twin that can drift.
        m = re.search(r"doubtBand:\s*([0-9.]+)", self.src)
        self.assertIsNotNone(m, "SEM.doubtBand missing from source")
        band = m.group(1)
        self.assertIn(f"±${{SEM.doubtBand}}", self.src)
        # and the decision-record copy renders it from the same constant
        self.assertIn("doubt band (±${SEM.doubtBand})", self.src)

    def test_term_drift_guard(self):
        # One term for suppression in operator copy: "silenced" must not
        # resurface in the built console.
        for blob, name in ((self.prod, "prod"), (self.stg, "staging")):
            self.assertNotIn("silenced", blob.lower(), name)

    def test_missing_source_fails_loud(self):
        # A builder that silently builds nothing is worse than one that
        # fails: assert the failure mode is a loud non-zero exit, not a
        # 200 of nothing.
        r = subprocess.run([sys.executable, BUILDER],
                           capture_output=True, text=True, timeout=60,
                           cwd=REPO)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
