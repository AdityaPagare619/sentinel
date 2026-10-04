#!/usr/bin/env python3
"""Weekly model re-validation runner (ADR-015, D2).

Runs the Type 1 procedure in docs/MODEL_REVALIDATION.md:
  1. pin probe (live Jev call asserts response.model == pinned_model),
  2. backtest replay of the labeled incident corpus under the frozen config,
  3. signed report written as JSON, human_signoff left None for the owner.

Exit codes:
  0 — verdict PASS (pin holds, zero-bar holds).
  1 — usage/config error (bad args, unreadable bundle).
  2 — verdict FAIL (drift detected or zero-bar broken) OR the job refused
      (zero-evidence corpus). A cron failure alert on non-zero pages the
      job owner (Pager — Sentinel SRE chief); the report JSON is written
      whenever the run produced one.

Fate-domain note (in the spirit of scripts/ops/watchdog_watcher.py): this
script shares only the import contract with the engine — it must never
import receiver.py or forwarder.py. A bug in the engine must not be able
to break its own re-validation.

Schedule: weekly (see docs/MODEL_REVALIDATION.md for the cron line).
Corpus: SENTINEL_REVALIDATION_CORPUS, else revalidation/corpus.jsonl
relative to the repo root. Bundle: SENTINEL_FRESHNESS_BUNDLE (pinning.json
+ thresholds.json — the same authoritative pin the gate asserts on).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))


def _read_pinning(bundle_dir: str) -> str | None:
    try:
        with open(os.path.join(bundle_dir, "pinning.json"),
                  "r", encoding="utf-8") as fh:
            pin = str(json.load(fh).get("pinned_model_version", "") or "").strip()
            return pin or None
    except (OSError, ValueError):
        return None


def _read_thresholds(bundle_dir: str):
    from sentinel.models import Thresholds
    try:
        with open(os.path.join(bundle_dir, "thresholds.json"),
                  "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return Thresholds(**{k: v for k, v in raw.items()
                             if k in Thresholds.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return None


def main() -> int:
    from sentinel.client import client_from_env
    from sentinel.freshness import FreshnessMonitor, FreshnessValidator
    from sentinel.revalidation import (DEFAULT_CORPUS_PATH, JOB_OWNER,
                                       RevalidationRefused, load_corpus,
                                       run_weekly_revalidation)

    ap = argparse.ArgumentParser(
        description="Weekly ADR-015 model re-validation runner.")
    ap.add_argument("--corpus", default=None,
                    help="labeled incident corpus JSONL "
                         f"(default: ${'SENTINEL_REVALIDATION_CORPUS'} or "
                         f"{DEFAULT_CORPUS_PATH})")
    ap.add_argument("--bundle", default=None,
                    help="freshness bundle dir with pinning.json + "
                         "thresholds.json (default: $SENTINEL_FRESHNESS_BUNDLE)")
    ap.add_argument("--report", default=None,
                    help="report JSON output path "
                         "(default: reports/model-revalidation-<ts>.json)")
    ap.add_argument("--allowlist", default=None,
                    help="optional allowlist snapshot JSON (list of "
                         "fingerprints) for the replay freeze")
    args = ap.parse_args()

    bundle_dir = (args.bundle
                  or os.environ.get("SENTINEL_FRESHNESS_BUNDLE"))
    if not bundle_dir:
        print("[sentinel] ERROR: no bundle: pass --bundle or set "
              "SENTINEL_FRESHNESS_BUNDLE", file=sys.stderr)
        return 1
    pinned = _read_pinning(bundle_dir)
    if not pinned:
        print("[sentinel] ERROR: pinning.json missing/unreadable in "
              f"{bundle_dir} — the job refuses to run without a pin",
              file=sys.stderr)
        return 1
    thresholds = _read_thresholds(bundle_dir)
    if thresholds is None:
        print("[sentinel] ERROR: thresholds.json missing/unreadable in "
              f"{bundle_dir}", file=sys.stderr)
        return 1

    corpus_path = (args.corpus
                   or os.environ.get("SENTINEL_REVALIDATION_CORPUS")
                   or os.path.join(REPO_ROOT, DEFAULT_CORPUS_PATH))

    allowlist: list = []
    if args.allowlist:
        try:
            with open(args.allowlist, "r", encoding="utf-8") as fh:
                allowlist = list(json.load(fh))
        except (OSError, ValueError) as exc:
            print(f"[sentinel] ERROR: allowlist snapshot unreadable: {exc}",
                  file=sys.stderr)
            return 1

    try:
        client = client_from_env(model=pinned)
    except Exception as exc:
        print(f"[sentinel] ERROR: cannot build Jev client: {exc}",
              file=sys.stderr)
        return 1

    try:
        incidents, replay_map = load_corpus(corpus_path)
    except RevalidationRefused as exc:
        print(f"[sentinel] REVALIDATION REFUSED: {exc}", file=sys.stderr)
        print(f"[sentinel] CRITICAL: page {JOB_OWNER}: the re-validation "
              "job could not run — zero evidence is not a pass.",
              file=sys.stderr)
        return 2

    # Mirror production's wiring (receiver.build_pipeline_from_env): the
    # replay runs the live kernel against the same freshness proofs. No
    # FitStore is fabricated — production doesn't wire one either today
    # (leg-1 runs on the dual-attestation interim); when that changes,
    # both call sites change together.
    import time as _time
    validator = FreshnessValidator(
        clock=_time.time,
        deployed_label_pipeline_version=os.environ.get(
            "SENTINEL_LABEL_PIPELINE_VERSION", ""))
    freshness_monitor = FreshnessMonitor(validator, bundle_dir)
    try:
        freshness_monitor.boot()
    except Exception as exc:
        print(f"[sentinel] ERROR: freshness bundle failed validation: "
              f"{exc}", file=sys.stderr)
        return 1

    report_path = args.report
    if report_path is None:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = os.path.join(
            REPO_ROOT, "reports", f"model-revalidation-{ts}.json")

    try:
        report = run_weekly_revalidation(
            client=client, pinned_model=pinned,
            incidents=incidents, replay_map=replay_map,
            thresholds=thresholds, allowlist=allowlist,
            freshness_monitor=freshness_monitor,
            config_freeze={"bundle_dir": bundle_dir,
                           "thresholds": "bundle thresholds.json",
                           "allowlist_snapshot": args.allowlist,
                           "freshness_bundle": "SENTINEL_FRESHNESS_BUNDLE",
                           "fit_store": "none (mirrors production)"},
            report_path=report_path)
    except RevalidationRefused as exc:
        print(f"[sentinel] REVALIDATION REFUSED: {exc}", file=sys.stderr)
        print(f"[sentinel] CRITICAL: page {JOB_OWNER}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — the job must never die quiet
        print(f"[sentinel] REVALIDATION ERROR: {exc!r}", file=sys.stderr)
        print(f"[sentinel] CRITICAL: page {JOB_OWNER}: the re-validation "
              "job crashed — treat as FAILED until a clean run completes.",
              file=sys.stderr)
        return 2

    print(f"[sentinel] report written: {report_path}", file=sys.stderr)
    print(f"[sentinel] verdict={report['verdict']} owner={JOB_OWNER} "
          "human_signoff=None (awaiting the owner's signature — see "
          "docs/MODEL_REVALIDATION.md)",
          file=sys.stderr)
    return 0 if report["verdict"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
