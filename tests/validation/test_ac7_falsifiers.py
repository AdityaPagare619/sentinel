"""AC-7 — The 9 falsifiers, re-run against the shipped console.

Applies to Track 8's final console artifact on program/full-build
(whichever pass Aditya judges in). Until Track 8 merges, these are
DEFERRED. Checks are split honestly:

  STATIC  — verifiable from the shipped bytes (CSS/HTML/JS) without a
            browser: AI-generic patterns, SIMULATED labels, confidence
            captions, tap-target sizes, media queries, no-theater strings.
  DYNAMIC — requires rendering/driving the console (headless harness or
            a real device). Without a live browser in this environment
            these are CANNOT-VERIFY, never upgraded on reasoning alone.

The earlier "weak pass" on the phone test is NOT carried forward as a
pass — Track 7 re-verifies or marks it red.
"""

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))

T8_MISSING = ("DEFERRED: Track 8 has not merged a console on "
              "program/full-build yet")

# Candidate console locations, in Track-8 merge order of likelihood.
CONSOLE_CANDIDATES = [
    os.path.join(REPO, "platform", "ui-v2", "index.html"),
    os.path.join(REPO, "platform", "ui", "index.html"),
]


def _console_path():
    # A confirmed Track 8 artifact wins (the marker names its path).
    confirmed = _t8_console_confirmed()
    if confirmed:
        p = (confirmed if os.path.isabs(confirmed)
             else os.path.join(REPO, confirmed))
        if os.path.exists(p):
            return p
    for p in CONSOLE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _console_text(path):
    """The console's own bytes + its local CSS/JS (same dir).

    Operator-facing code only: tests/, tools/, data/ fixtures are not
    the UI (a unicode test fixture is not iconography)."""
    base = os.path.dirname(path)
    skip_dirs = {"tests", "tools", "data", "node_modules"}
    parts = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            if f.endswith((".html", ".css", ".js", ".mjs")):
                fp = os.path.join(root, f)
                try:
                    with open(fp, encoding="utf-8",
                              errors="replace") as fh:
                        parts.append(fh.read())
                except OSError:
                    pass
    return "\n".join(parts)


def _t8_console_confirmed():
    """Track 8's final console artifact confirmed on program/full-build.

    The falsifiers apply to the SHIPPED console (whichever pass Aditya
    judges in), not to the pre-program console. Track 7 writes
    docs/validation/T8_CONSOLE when Track 8's merge is verified — until
    then every AC-7 check is DEFERRED, never run against the old UI."""
    marker = os.path.join(REPO, "docs", "validation", "T8_CONSOLE")
    if os.path.exists(marker):
        with open(marker) as fh:
            return fh.read().strip()
    return ""


class TestAC7Falsifiers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.console = _console_path()
        cls.text = _console_text(cls.console) if cls.console else ""

    def _require_t8(self):
        confirmed = _t8_console_confirmed()
        if not confirmed:
            self.skipTest(T8_MISSING)
        if not self.console:
            self.skipTest("DEFERRED: Track 8 confirmed but no console "
                          "artifact found at the expected paths")

    # ---- STATIC (verifiable from bytes) ----

    def test_7_3_honesty_static(self):
        """SIMULATED band strings; confidence ordinal caption; no
        fabricated-drill copy."""
        self._require_t8()
        t = self.text
        self.assertRegex(t, re.compile(r"SIMULATED", re.I),
                         "no SIMULATED label in the console bytes")
        self.assertRegex(
            t, re.compile(r"not a probability|ranks decisions", re.I),
            "confidence shown without the ordinal caption")

    def test_7_4_ai_generic_static(self):
        """No gradients, glassmorphism, purple/blue glow, emoji
        iconography, hero layout."""
        self._require_t8()
        t = self.text.lower()
        for pat, name in [
                (r"linear-gradient|radial-gradient", "gradient"),
                (r"backdrop-filter\s*:\s*blur", "glassmorphism"),
                (r"box-shadow:[^;]*\b(purple|violet|#8[0-9a-f])", "glow")]:
            hits = re.findall(pat, t)
            self.assertEqual(hits, [],
                             f"AI-generic pattern '{name}' in console CSS")
        # Emoji iconography: private-use / pictograph ranges in markup.
        emoji = re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", t)
        self.assertEqual(emoji, [],
                         f"emoji iconography in console: {emoji[:5]}")

    def test_7_6_no_process_theater_static(self):
        """No falsifier buttons, no research claims in operator UI."""
        self._require_t8()
        t = self.text.lower()
        for pat in [r"falsifier", r"self-judgment", r"research notes"]:
            self.assertEqual(re.findall(pat, t), [],
                             f"process-theater string '{pat}' in the UI")

    def test_7_9_phone_static(self):
        """<=640px single-column layout exists; tap targets >= 32px in
        the phone CSS. (Rendered-on-device stays CANNOT-VERIFY.)"""
        self._require_t8()
        t = self.text
        self.assertRegex(t, re.compile(r"max-width\s*:\s*64\dpx"),
                         "no ~640px phone breakpoint in console CSS")
        # Tap targets: look for min-height/min-width < 32px on
        # button-ish selectors — a coarse static net.
        small = re.findall(
            r"(min-height|min-width)\s*:\s*(\d+)px", t)
        too_small = [(p, int(v)) for p, v in small if int(v) < 32]
        self.assertEqual(too_small, [],
                         f"tap targets under 32px: {too_small[:5]}")

    # ---- DYNAMIC (need rendering; honest about the limit) ----

    def test_7_1_3am_dynamic(self):
        self._require_t8()
        self.skipTest(
            "CANNOT-VERIFY in this environment: the 3 AM comprehension "
            "check needs the rendered artifact (no live browser). "
            "Requires an eligible parent to render and judge.")

    def test_7_2_scale_dynamic(self):
        self._require_t8()
        self.skipTest(
            "CANNOT-VERIFY in this environment: driving 400+ problems "
            "through the rendered console needs a headless harness from "
            "Track 8 or a live browser.")

    def test_7_5_hierarchy_grayscale_dynamic(self):
        self._require_t8()
        self.skipTest(
            "CANNOT-VERIFY in this environment: the grayscale hierarchy "
            "check needs a rendered screenshot + luminance collapse.")

    def test_7_7_dynamics_dynamic(self):
        self._require_t8()
        self.skipTest(
            "CANNOT-VERIFY in this environment: storm-build, timer-win, "
            "and suppression-expiry dynamics need the console driven "
            "headless.")

    def test_7_8_appeal_audit_dynamic(self):
        self._require_t8()
        self.skipTest(
            "CANNOT-VERIFY in this environment: appeal/audit-integrity "
            "needs the console driven headless (appeal -> row marked, "
            "reopened, never rewritten).")


if __name__ == "__main__":
    unittest.main(verbosity=2)
