"""Dead-chart guard for the load-test dashboard generator.

Root cause (audit UI C11/P1-4; W9 flag): svg_bars() rendered width=bw-4
with bw=width/n, which goes negative for n>130 — the 360/1440-chunk tiers
painted ~1,800 zero/negative-width rects, i.e. invisible charts that the
dashboard shipped as if they were evidence. This test is the gate: for
every tier size we ever generated, every emitted rect must have positive
width and the chart must stay bounded in bar count.
"""

import importlib.util
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_GEN = os.path.join(_HERE, "..", "loadtest", "make_dashboard.py")


def _load():
    spec = importlib.util.spec_from_file_location(
        "loadtest_make_dashboard", os.path.abspath(_GEN))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


svg_bars = _load().svg_bars


class SvgBarsDeadChartGuard(unittest.TestCase):
    def _widths(self, svg):
        # rect widths only — the <svg> element's own width attr is not a bar.
        return [float(w)
                for w in re.findall(r'<rect[^>]*width="([\d.]+)"', svg)]

    def test_widths_positive_for_every_tier_size(self):
        for n in (8, 130, 131, 360, 1440, 5000):
            vals = [float(i % 7 + 1) for i in range(n)]
            labs = [f"chunk {i}" for i in range(n)]
            svg = svg_bars(vals, labs)
            widths = self._widths(svg)
            self.assertTrue(widths, f"n={n}: no rects emitted at all")
            self.assertTrue(all(w > 0 for w in widths),
                            f"n={n}: dead (zero/negative-width) rects: "
                            f"{[w for w in widths if w <= 0][:5]}")
            self.assertLessEqual(len(widths), 120,
                                 f"n={n}: bar count unbounded")

    def test_bucketing_preserves_scale(self):
        # Bucketing by mean must not distort the chart's vertical scale:
        # the tallest bar still reaches the chart top.
        vals = [10.0] * 1000 + [100.0]
        svg = svg_bars(vals, [f"c{i}" for i in range(len(vals))],
                       width=520, height=140)
        heights = [float(h)
                   for h in re.findall(r'<rect[^>]*height="([\d.]+)"', svg)]
        self.assertAlmostEqual(max(heights), 140 - 24, places=1)

    def test_empty_values_still_safe(self):
        self.assertEqual(svg_bars([], []), "")


if __name__ == "__main__":
    unittest.main()
