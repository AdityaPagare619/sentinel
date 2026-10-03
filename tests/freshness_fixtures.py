"""Synthetic config-bundle builders for the freshness test suite.

The rot matrix must test the mechanics, not the calendar: every proof is
synthesized with a *controlled age* against a pinned clock (BASE_EPOCH).
The validator takes an injectable clock; the fixture pins it.

Bundle layout (design §A2 + docs/FRESHNESS_CONTRACT.md):
    <bundle>/
      thresholds.json  allowlist.json  calibration.json  pinning.json
      proofs/calibration_proof.json
      proofs/threshold_attestation.json
      proofs/allowlist_attestations.json
      manifest.json
"""

import json
import os

from sentinel.freshness import (
    config_hash_for,
    rfc3339_from_epoch,
    write_manifest,
)

BASE_EPOCH = 1_700_000_000.0  # pinned "now": 2023-11-14T22:13:20Z
PINNED_MODEL = "jev-1.13.0"
LABEL_PIPELINE = "label-pipe-v3"


def ts_days_ago(days: float) -> str:
    return rfc3339_from_epoch(BASE_EPOCH - days * 86400.0)


class FixtureClock:
    """Injectable clock pinned at BASE_EPOCH; advance() crosses TTL boundaries."""

    def __init__(self, t: float = BASE_EPOCH):
        self.t = t
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        return self.t

    def advance(self, seconds: float):
        self.t += seconds


def make_thresholds(conf_bar: float = 0.90) -> dict:
    return {
        "suppress_p1_max": 0.002,
        "suppress_conf_min": conf_bar,
        "page_p1p2_min": 0.30,
        "uncertain_conf_max": 0.50,
        "queue_conf_min": 0.70,
    }


def make_fit_proof(*, fit_id: str = "fit-" + "a" * 8,
                   trained_days_ago: float = 10.0,
                   window_days: int = 90,
                   model_pin: str = PINNED_MODEL,
                   label_pipeline: str = LABEL_PIPELINE,
                   issued_by: str = "tuner-run-20231030-07/tuner@1.4.2",
                   p_upper_measured: float = 0.0016,
                   n: int = 412) -> dict:
    return {
        "fit_id": fit_id,
        "fit_trained_at": ts_days_ago(trained_days_ago),
        "trained_on": {
            "n": n,
            "reference_class": "org-fintech-partner/p1",
            "label_pipeline_version": label_pipeline,
            "history_as_of": ts_days_ago(trained_days_ago),
        },
        "p_upper_measured": p_upper_measured,
        "validity_window_days": window_days,
        "issued_by": issued_by,
        "model_pin": model_pin,
    }


def make_threshold_attestation(thresholds_obj: dict, *,
                               attested_days_ago: float = 5.0,
                               revalidation_days: int = 30,
                               attested_by=("op-alice", "op-bob"),
                               threshold: float | None = None,
                               backtest_id: str = "bt-20231028-03") -> dict:
    threshold = make_thresholds()["suppress_conf_min"] if threshold is None \
        else threshold
    # NOTE: threshold must equal the LIVE bar; pass the same thresholds_obj
    # the bundle will carry, or the attestation is stale by construction.
    return {
        "threshold": threshold,
        "attested_by": list(attested_by),
        "attested_at": ts_days_ago(attested_days_ago),
        "on_evidence": {
            "backtest_id": backtest_id,
            "false_suppress_count": 0,
            "n": 1200,
            "shadow_report_id": "shadow-20231028-03",
        },
        "revalidation_due_at": rfc3339_from_epoch(
            BASE_EPOCH - attested_days_ago * 86400.0 + revalidation_days * 86400.0),
        "config_hash": config_hash_for(thresholds_obj),
    }


def make_allowlist_entry(fingerprint: str, *, env: str = "prod",
                         attested_days_ago: float = 20.0,
                         ttl_days: int = 180,
                         attested_by=("op-alice", "op-bob"),
                         incident_linkage: str = "zero",
                         occurrences: int = 340,
                         security_category_ban: bool = False) -> dict:
    return {
        "fingerprint": fingerprint,
        "attested_by": list(attested_by),
        "attested_at": ts_days_ago(attested_days_ago),
        "on_evidence": {
            "occurrences": occurrences,
            "incident_linkage": incident_linkage,
            "observed_since": ts_days_ago(attested_days_ago + 30),
            "note": "cache eviction noise, zero incident linkage",
        },
        "ttl_days": ttl_days,
        "env": env,
        "security_category_ban": security_category_ban,
    }


def fp(name: str, env: str = "prod") -> str:
    """Namespaced fingerprint per ADR-017: env:cluster:check_name:signature."""
    return f"{env}:us-east-1:{name}:sig-{name}"


def build_bundle(bundle_dir: str, *,
                 thresholds: dict | None = None,
                 fit_proof: dict | None = None,
                 threshold_attestation: dict | None = None,
                 entries: list[tuple[str, str, dict]] | None = None,
                 pinned_model: str = PINNED_MODEL,
                 skip_manifest: bool = False) -> str:
    """Write a complete bundle and its manifest. ``entries`` is a list of
    (fingerprint, env, attestation_dict). Returns bundle_dir."""
    thresholds = thresholds if thresholds is not None else make_thresholds()
    fit_proof = fit_proof if fit_proof is not None else make_fit_proof()
    if threshold_attestation is None:
        threshold_attestation = make_threshold_attestation(thresholds)
    entries = entries if entries is not None else [
        (fp("cache-evictions"), "prod", make_allowlist_entry(fp("cache-evictions"))),
    ]

    os.makedirs(os.path.join(bundle_dir, "proofs"), exist_ok=True)

    def _w(rel: str, obj: dict):
        full = os.path.join(bundle_dir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2)
            fh.write("\n")

    _w("thresholds.json", thresholds)
    _w("calibration.json", {"fit_id": fit_proof["fit_id"],
                            "p_upper": fit_proof["p_upper_measured"],
                            "artifact": "placeholder"})
    _w("pinning.json", {"pinned_model_version": pinned_model})
    _w("proofs/calibration_proof.json", fit_proof)
    _w("proofs/threshold_attestation.json", threshold_attestation)

    envs: dict[str, dict] = {}
    attestations: dict[str, dict] = {}
    for fingerprint, env, att in entries:
        envs.setdefault(env, {"entries": []})["entries"].append(fingerprint)
        attestations[fingerprint] = att
    _w("allowlist.json", {"envs": envs})
    _w("proofs/allowlist_attestations.json", {"entries": attestations})

    if not skip_manifest:
        write_manifest(bundle_dir)
    return bundle_dir
