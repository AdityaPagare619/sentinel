#!/usr/bin/env python3
"""antislop.py — the §8 anti-slop checklist, mechanical edition.

"AI-generated-looking" is not a vibe judgment — it is a checklist, and the
mechanically checkable items are gates, not aspirations:

  1. no emoji as iconography (§8.3) — scan JS/CSS for emoji ranges
  2. typography discipline (§8.6) — exactly two font families, fixed scale
  3. no raw color literals outside tokens (§8.7 + F7) — hex in JS/CSS fails
  4. no gradient-as-identity (§8.2) — no linear-gradient in CSS
  5. no phantom interactivity (§8.9) — no "coming soon" / dead controls in copy
  6. tabular numerals wherever numbers sit near numbers (§5.3)

Run: python3 tests/antislop.py  (from platform/ui/)
"""
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
failures = []

def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)

JS = sorted((ASSETS).glob("*.js"))
CSS = sorted((ASSETS).glob("*.css"))
TOKENS = (ASSETS / "tokens.css").read_text()

# 1. no emoji as iconography — drawn shapes, one set (§8.3)
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
for f in JS + CSS:
    t = f.read_text()
    hits = [c for c in set(EMOJI.findall(t))]
    # the ◈/●/◐ glyphs and ▲/○/▮/◌ shapes are drawn geometric shapes, not emoji
    hits = [c for c in hits if c not in "◈●○◐◑◌▲△▮◄►⬤"]
    check(f"no emoji iconography in {f.name}", not hits, f"found {hits}")

# 2. typography discipline — two families (§5.3, §8.6)
fonts = re.findall(r"--font-([a-z-]+):", TOKENS)
check("exactly two font families in tokens", sorted(fonts) == ["mono", "ui"], str(fonts))
for f in CSS:
    if f.name == "tokens.css":
        continue
    t = f.read_text()
    raw_ff = [m for m in re.finditer(r"font-family\s*:\s*([^;]+);", t)
              if "var(--font-" not in m.group(1)]
    check(f"no raw font-family in {f.name}", not raw_ff, str([m.group(0)[:40] for m in raw_ff]))

# 3. semantic color only — no raw literals outside the token files (F7, §8.7)
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
for f in JS + CSS:
    if f.name == "tokens.css":
        continue
    t = f.read_text()
    hits = HEX.findall(t)
    check(f"no raw hex literals in {f.name}", not hits, str(hits[:5]))
# rgb()/hsl() literals are also banned (rgba overlays excluded — none remain)
for f in CSS:
    t = re.sub(r"/\*.*?\*/", "", f.read_text(), flags=re.S)
    hits = re.findall(r"(?<!var\()rgb[a]?\s*\(", t)
    check(f"no raw rgb() in {f.name}", not hits, str(hits[:3]))

# 4. no gradient-as-identity (§8.2) — flat token colors only
for f in CSS:
    t = f.read_text()
    hits = re.findall(r"(?:linear|radial|conic)-gradient", t)
    check(f"no decorative gradients in {f.name}", not hits, str(hits))

# 5. no phantom interactivity (§8.9) — rendered, so it works
for f in JS:
    t = f.read_text().lower()
    hits = [w for w in ["coming soon", "not yet implemented", "todo: render"] if w in t]
    check(f"no phantom interactivity in {f.name}", not hits, str(hits))

# 6. tabular numerals (§5.3)
check("tabular numerals declared", "font-variant-numeric: tabular-nums" in TOKENS)

print()
if failures:
    print(f"{len(failures)} anti-slop gate(s) FAILED")
    sys.exit(1)
print("all anti-slop gates pass")
