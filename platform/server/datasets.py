"""Versioned labeled datasets for /api/simulate (Forge's lane).

The contract pins simulate to a versioned labeled dataset: unknown
``dataset_version`` -> 422, never a silent fallback. This registry is the
pin.

``labels-v3`` is generated deterministically from the engine's own
``sentinel.synthetic`` generator (seed 7, n=2000) — the same shapes the
v0.1 tuner consumes. Generation runs as a SUBPROCESS so the serving path
never imports the Jev client module (not even transitively via
sentinel.synthetic -> sentinel.models). The bytes are hashed; the
provenance block reports the real sha256.

Cache layout (under the platform state dir):
  <state_dir>/platform/datasets/labels-v3.jsonl

New versions are added by extending DATASETS — never by mutating a
shipped version's seed/n (a version's bytes are immutable; its sha256
is its identity).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading

DATASETS = {
    # version: {seed, n, window_days, generator}
    "labels-v3": {
        "seed": 7,
        "n": 2000,
        "window_days": 30,
        "generator": "sentinel.synthetic",
        "note": "seeded synthetic labels; data_source=synthetic, loudly",
    },
}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


class UnknownDataset(KeyError):
    """Raised for an unregistered dataset_version (-> HTTP 422)."""


class DatasetRegistry:
    """Build-once, hash-pinned labeled datasets."""

    def __init__(self, cache_dir: str):
        self.cache_dir = os.path.abspath(cache_dir)
        self._lock = threading.Lock()
        self._rows: dict[str, list[dict]] = {}
        self._sha: dict[str, str] = {}

    def versions(self) -> list[str]:
        return sorted(DATASETS)

    def path_for(self, version: str) -> str:
        if version not in DATASETS:
            raise UnknownDataset(version)
        return os.path.join(self.cache_dir, f"{version}.jsonl")

    def get(self, version: str) -> tuple[list[dict], str, dict]:
        """(rows, sha256, metadata). Builds deterministically on first use."""
        if version not in DATASETS:
            raise UnknownDataset(version)
        with self._lock:
            if version not in self._rows:
                path = self.path_for(version)
                if not os.path.exists(path):
                    self._build(version, path)
                with open(path, "rb") as fh:
                    raw = fh.read()
                sha = hashlib.sha256(raw).hexdigest()
                rows = [json.loads(line) for line in raw.decode("utf-8")
                        .splitlines() if line.strip()]
                self._rows[version] = rows
                self._sha[version] = sha
            meta = dict(DATASETS[version])
            meta["sha256"] = self._sha[version]
            meta["n_alerts"] = len(self._rows[version])
            return self._rows[version], self._sha[version], meta

    def _build(self, version: str, path: str) -> None:
        spec = DATASETS[version]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        env = dict(os.environ)
        src = os.path.join(REPO_ROOT, "src")
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"]
                                   if env.get("PYTHONPATH") else "")
        # Subprocess, not import: the generator pulls sentinel.models ->
        # the Jev client module transitively, and the serving path must never
        # import the Jev client module (zero-Jev-calls law).
        proc = subprocess.run(
            [sys.executable, "-m", spec["generator"],
             "--n", str(spec["n"]), "--seed", str(spec["seed"]),
             "-o", tmp],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
            timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"dataset build failed for {version}: "
                               f"{proc.stderr[-2000:]}")
        os.replace(tmp, path)
