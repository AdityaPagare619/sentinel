"""Import bootstrap for the platform server package.

The repo's ``platform/`` directory shadows Python's stdlib ``platform``
module, so ``import platform.server`` can never resolve (stdlib wins the
name). This loader imports the server package under the collision-free
alias ``sentinel_platform`` with proper package semantics — relative
imports (``from . import store``) keep working.

Usage (tests and __main__.py):
    import _pkg
    app = _pkg.load("app")          # -> sentinel_platform.app
    store = _pkg.load("store")      # -> sentinel_platform.store
"""

import importlib
import importlib.util
import os
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
ALIAS = "sentinel_platform"


def ensure():
    """Load the sentinel_platform package (idempotent)."""
    if ALIAS in sys.modules:
        return sys.modules[ALIAS]
    spec = importlib.util.spec_from_file_location(
        ALIAS,
        os.path.join(SERVER_DIR, "__init__.py"),
        submodule_search_locations=[SERVER_DIR],
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[ALIAS] = pkg
    spec.loader.exec_module(pkg)
    return pkg


def load(name):
    """Import and return the ``sentinel_platform.<name>`` module."""
    ensure()
    return importlib.import_module(f"{ALIAS}.{name}")
