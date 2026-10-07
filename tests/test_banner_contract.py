"""Structural banner contract — beyond substring asserts.

The audit's UI P0-1: the served console kept aria-label="Simulated mode"
while JS rewrote the visible text to PRODUCTION — a sighted/screen-reader
contradiction in the dangerous direction. A substring assert ("PRODUCTION"
appears somewhere) cannot catch this class; this test asserts the
STRUCTURAL invariant instead:

  every JS function that rewrites the banner's VISIBLE text must also
  rewrite its ACCESSIBLE name (aria-label) in the same function.

Plus: the static banner's text and aria-label must agree on the mode, and
the production console's #modebar must keep its accessible name
content-derived (role="status", no aria-label anywhere) so the two can
never contradict.

stdlib only. No network, no browser.
"""

import os
import re
import sys
import unittest
from html.parser import HTMLParser

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V2_HTML = os.path.join(REPO, "platform", "ui-v2", "index.html")
PROD_HTML = os.path.join(REPO, "platform", "ui", "index.html")
PROD_JS = os.path.join(REPO, "platform", "ui", "assets", "app.js")


# ---------------------------------------------------------------- helpers

def split_functions(js):
    """Yield (name, body) for each `function name(...) {...}` via brace matching."""
    out = []
    for m in re.finditer(r"function\s+([A-Za-z_$][\w$]*)\s*\(", js):
        name = m.group(1)
        i = js.index("{", m.end())
        depth = 0
        j = i
        while True:
            if js[j] == "{":
                depth += 1
            elif js[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append((name, js[i:j + 1]))
    return out


def banner_violations(js):
    """Functions that write banner visible text without setting aria-label.

    A variable counts as "the banner" if it is assigned from
    querySelector('.sim-band') or getElementById('prodBandText').
    Returns [(func_name, var_name)].
    """
    bad = []
    for name, body in split_functions(js):
        banner_vars = set()
        for vm in re.finditer(
                r"(\w+)\s*=\s*document\.(querySelector\(\s*['\"]\.sim-band['\"]\s*\)"
                r"|getElementById\(\s*['\"]prodBandText['\"]\s*\))", body):
            banner_vars.add(vm.group(1))
        if not banner_vars:
            continue
        writes_text = any(
            re.search(r"\b%s\s*\.\s*(innerHTML|textContent)\s*=" % re.escape(v),
                      body)
            for v in banner_vars)
        sets_aria = any(
            re.search(r"\b%s\s*\.\s*setAttribute\(\s*['\"]aria-label['\"]" % re.escape(v),
                      body)
            for v in banner_vars)
        if writes_text and not sets_aria:
            bad.append((name, sorted(banner_vars)))
    return bad


class _BannerHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.banners = []  # (attrs, text)
        self._cur = None

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "sim-band" in (d.get("class") or "").split():
            self._cur = [d, ""]

    def handle_data(self, data):
        if self._cur is not None:
            self._cur[1] += data

    def handle_endtag(self, tag):
        if self._cur is not None and tag == "div":
            self.banners.append((self._cur[0], self._cur[1]))
            self._cur = None


def script_bodies(html):
    """Extract <script> (non-src) bodies from an HTML document."""
    bodies = []
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                         html, re.S | re.I):
        bodies.append(m.group(1))
    return bodies


# ------------------------------------------------------------------- tests

class TestV2BannerContract(unittest.TestCase):
    def test_static_banner_text_and_aria_label_agree(self):
        with open(V2_HTML, encoding="utf-8") as fh:
            html = fh.read()
        p = _BannerHTMLParser()
        p.feed(html)
        self.assertTrue(p.banners, "no .sim-band element found")
        for attrs, text in p.banners:
            aria = (attrs.get("aria-label") or "").lower()
            self.assertIn("simulated", aria,
                          "banner aria-label must name its mode")
            self.assertIn("SIMULATED", text,
                          "static v2 file is the simulated variant: "
                          "visible text must say SIMULATED")

    def test_every_banner_text_rewrite_also_rewrites_aria_label(self):
        with open(V2_HTML, encoding="utf-8") as fh:
            html = fh.read()
        bad = []
        for js in script_bodies(html):
            bad.extend(banner_violations(js))
        self.assertEqual(
            bad, [],
            "banner text rewritten without aria-label (the P0-1 bug class): "
            f"{bad}")

    def test_checker_catches_the_bug_class(self):
        # Failure-proof: the invariant checker must flag a function that
        # rewrites banner text without touching the accessible name.
        evil = """
        function evilRewrite(){
          const band = document.querySelector('.sim-band');
          band.innerHTML = '<b>PRODUCTION</b> — everything is fine';
        }
        function goodRewrite(){
          const band = document.querySelector('.sim-band');
          band.innerHTML = '<b>PRODUCTION</b>';
          band.setAttribute('aria-label', 'Production console');
        }
        """
        bad = banner_violations(evil)
        self.assertEqual([n for n, _ in bad], ["evilRewrite"])
        # and the real file is clean
        with open(V2_HTML, encoding="utf-8") as fh:
            html = fh.read()
        real_bad = []
        for js in script_bodies(html):
            real_bad.extend(banner_violations(js))
        self.assertEqual(real_bad, [])


class TestProdModebarContract(unittest.TestCase):
    def test_modebar_accessible_name_is_content_derived(self):
        # role="status" + no aria-label: the accessible name IS the visible
        # text, so the two cannot contradict. If anyone adds an aria-label
        # to #modebar, this test forces the contract to be re-examined.
        with open(PROD_HTML, encoding="utf-8") as fh:
            html = fh.read()

        class P(HTMLParser):
            def __init__(self):
                super().__init__()
                self.attrs = None

            def handle_starttag(self, tag, attrs):
                if dict(attrs).get("id") == "modebar":
                    self.attrs = dict(attrs)

        p = P()
        p.feed(html)
        self.assertIsNotNone(p.attrs, "#modebar element missing")
        self.assertEqual(p.attrs.get("role"), "status")
        self.assertNotIn("aria-label", p.attrs,
                         "#modebar must not carry aria-label: its accessible "
                         "name must stay content-derived")

    def test_no_js_sets_aria_label_on_modebar(self):
        with open(PROD_JS, encoding="utf-8") as fh:
            js = fh.read()
        hits = re.findall(
            r"modebar\s*\.\s*setAttribute\(\s*['\"]aria-label['\"]", js)
        self.assertEqual(
            hits, [],
            "aria-label set on #modebar in JS — the accessible name could "
            "contradict the visible text; extend the contract first")


if __name__ == "__main__":
    unittest.main()
