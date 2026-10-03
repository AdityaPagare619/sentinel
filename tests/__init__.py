"""Sentinel test package.

Bootstraps `src/` onto sys.path so `python -m unittest discover -s tests`
works from ~/workspace/jev-builds/sentinel with no install step.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
