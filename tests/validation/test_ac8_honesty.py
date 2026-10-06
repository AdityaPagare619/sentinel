"""AC-8 — Honesty audit: no fake-real numbers, no unmeasured claims.

Applies to all user-facing copy: console strings, API messages,
evaluator/harness labels. Executable now for API copy + key-leak
checks; console-copy checks go live with Track 8's console.
"""

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
APP_PY = os.path.join(REPO, "platform", "server", "app.py")

# Latency/measured-claim patterns that MUST trace to an artifact.
MEASURED_CLAIM_RES = [
    (re.compile(r"<\s*5\s*s\b", re.I), "<5s claim"),
    (re.compile(r"\bhalted in \d+\s*ms", re.I), "halted-in-Xms claim"),
    (re.compile(r"\bwithin \d+\s*ms\b", re.I), "within-Xms claim"),
    (re.compile(r"\b\d+\s*ms\b.*\b(measured|guaranteed)\b", re.I),
     "measured/guaranteed latency claim"),
]


def _user_facing_text():
    """Console bytes + platform API message strings."""
    parts = []
    for base in (os.path.join(REPO, "platform", "ui"),
                 os.path.join(REPO, "platform", "ui-v2")):
        if not os.path.isdir(base):
            continue
        for root, _d, files in os.walk(base):
            for f in files:
                if f.endswith((".html", ".css", ".js", ".mjs")):
                    fp = os.path.join(root, f)
                    try:
                        with open(fp, encoding="utf-8",
                                  errors="replace") as fh:
                            parts.append(fh.read())
                    except OSError:
                        pass
    with open(APP_PY, encoding="utf-8", errors="replace") as fh:
        parts.append(fh.read())
    return "\n".join(parts)


class TestAC8Honesty(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _user_facing_text()

    def test_8a_simulated_labels(self):
        """Every sim surface carries an in-band SIMULATED label."""
        self.assertRegex(self.text, re.compile(r"SIMULATED"),
                         "no SIMULATED label anywhere in user-facing copy")

    def test_8b_no_fake_real_numbers(self):
        """No fabricated measurement copy without a drill artifact."""
        drill_dirs = [os.path.join(REPO, "docs", "drills"),
                      os.path.join(REPO, "ops", "drills")]
        artifacts = []
        for d in drill_dirs:
            if os.path.isdir(d):
                for f in os.listdir(d):
                    fp = os.path.join(d, f)
                    try:
                        with open(fp, encoding="utf-8",
                                  errors="replace") as fh:
                            artifacts.append(fh.read())
                    except OSError:
                        pass
        art_text = "\n".join(artifacts)
        for rx, name in MEASURED_CLAIM_RES:
            for m in rx.finditer(self.text):
                # A claim is honest iff a drill artifact states a number
                # for the same mechanism (coarse: any measured ms in an
                # artifact). Otherwise it is fake-real.
                self.assertRegex(
                    art_text, re.compile(r"\d+\s*ms"),
                    f"user-facing {name} with no measured artifact: "
                    f"...{m.group(0)[:60]}...")

    def test_8c_confidence_never_probability(self):
        """Confidence is ordinal: no '%' and no 'probability' attached to
        confidence in user-facing copy."""
        hits = re.findall(r"confidence[^\n]{0,40}%", self.text, re.I)
        self.assertEqual(hits, [],
                         f"confidence shown as %: {hits[:3]}")
        hits = re.findall(r"confidence[^\n]{0,60}probabilit",
                          self.text, re.I)
        self.assertEqual(hits, [],
                         f"confidence called a probability: {hits[:3]}")

    def test_8e_key_never_leaks(self):
        """The integrations status surface exposes configured/last4 only
        — never key values. (Track 2's resolve_jev_key server-side check
        goes live with Track 2.)"""
        sys.path.insert(0, os.path.join(REPO, "src"))
        from sentinel.integrations import IntegrationStore
        store = IntegrationStore()
        status = store.status()
        blob = str(status)
        for key_name in ("pagerduty_routing_key", "jev_api_key"):
            entry = status.get(key_name, {})
            self.assertNotIn("value", entry,
                             f"{key_name} status exposes a value field")
            self.assertIn("configured", entry)
            self.assertIn("last4", entry)
        # No 32-hex-looking secrets embedded in the status blob.
        self.assertEqual(
            re.findall(r"\b[0-9a-fA-F]{32}\b", blob), [],
            "key-shaped material in the integrations status blob")

    def test_8e_no_key_in_api_responses_or_logs(self):
        """The platform app's key-handling paths never put key material
        into RESPONSES or logs. (The outbound PagerDuty event legitimately
        carries the routing key to events.pagerduty.com — that is the
        key's purpose, not a leak.)"""
        with open(APP_PY, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        m = re.search(r"def _int_test_page.*?(?=\n    def |\Z)", src, re.S)
        self.assertIsNotNone(m)
        body = m.group(0)
        # Every _ok(...) response dict in this method: none may carry the
        # key VARIABLE as a value (the English word "key" in message
        # strings is fine — key material is not).
        for ok in re.finditer(r"self\._ok\(start_response, \{(.*?)\}\)",
                              body, re.S):
            payload = ok.group(1)
            self.assertIsNone(
                re.search(r":\s*key\b", payload),
                "test-page response carries the key variable: "
                f"{payload[:150]}")
        # No logging of key material on these paths.
        for pat in [r"log.*\bkey\b", r"print.*\bkey\b"]:
            hits = [h for h in re.findall(pat, body, re.I)
                    if "key_source" not in h]
            self.assertEqual(hits, [],
                             f"possible key logging in test-page: {hits}")
        # The key-save path reports saved names + status (last4), never
        # values.
        m2 = re.search(r"def _int_keys_save.*?(?=\n    def |\Z)", src, re.S)
        self.assertIsNotNone(m2)
        self.assertNotIn("integrations.get(", m2.group(0),
                         "key-save path reads a stored value into scope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
