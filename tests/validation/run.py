#!/usr/bin/env python3
"""Track 7 sign-off report generator.

Runs the full validation suite (stdlib unittest) and writes a
per-criterion verdict report to
docs/validation/evidence/<date>-<short-sha>/report.{json,md}.

Usage:
    python3 tests/validation/run.py [--report] [--suite tests.validation]

--report writes the evidence bundle; without it, prints the summary only.
Exit code: 0 iff every EXECUTED check passes (skips are reported, not
hidden — a sign-off still requires zero red).
"""

import argparse
import datetime
import io
import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

AC_OF = {
    "test_ac1_kill_drill": "AC-1 kill-switch drill",
    "test_ac2_race_outcomes": "AC-2 race outcomes",
    "test_ac3_suppression_audit": "AC-3 suppression correctness",
    "test_ac4_scale": "AC-4 scale test",
    "test_ac5_p0_rewalk": "AC-5 P0 re-walk",
    "test_ac6_shadow_audit": "AC-6 shadow audit",
    "test_ac7_falsifiers": "AC-7 falsifiers",
    "test_ac8_honesty": "AC-8 honesty audit",
}


def _sha():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
            capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _branch():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO,
            capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


class _Collector(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.records = []  # (test_id, verdict, detail)

    def _ac(self, test):
        mod = test.__class__.__module__.split(".")[-1]
        return AC_OF.get(mod, mod)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append((self._ac(test), str(test), "PASS", ""))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append(
            (self._ac(test), str(test), "FAIL", self._exc(err)))

    def addError(self, test, err):
        super().addError(test, err)
        self.records.append(
            (self._ac(test), str(test), "ERROR", self._exc(err)))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        reason = str(reason)
        if reason.startswith("BLOCKED"):
            verdict = "BLOCKED"
        elif reason.startswith("DEFERRED"):
            verdict = "DEFERRED"
        elif reason.startswith("CANNOT-VERIFY"):
            verdict = "CANNOT-VERIFY"
        else:
            verdict = "SKIPPED"
        self.records.append((self._ac(test), str(test), verdict, reason))

    @staticmethod
    def _exc(err):
        exctype, value, _tb = err
        return f"{exctype.__name__}: {value}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true",
                    help="write the evidence bundle under docs/validation/evidence/")
    ap.add_argument("--suite", default="tests.validation",
                    help="unittest suite root (default: tests.validation)")
    args = ap.parse_args()

    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=os.path.join(REPO, "tests", "validation"),
        pattern="test_ac*.py",
        top_level_dir=REPO)
    collector = _Collector()
    # Drive the suite directly into our collector (TextTestRunner would
    # build its own result object). Test prints (measured numbers) flow
    # to stdout as evidence.
    suite.run(collector)

    by_ac = {}
    for ac, tid, verdict, detail in collector.records:
        by_ac.setdefault(ac, {"PASS": 0, "FAIL": 0, "ERROR": 0,
                              "BLOCKED": 0, "DEFERRED": 0,
                              "CANNOT-VERIFY": 0, "SKIPPED": 0,
                              "details": []})
        by_ac[ac][verdict] += 1
        if verdict not in ("PASS",):
            by_ac[ac]["details"].append({"test": tid, "verdict": verdict,
                                         "detail": detail[:500]})

    ac_verdicts = {}
    for ac, counts in by_ac.items():
        if counts["FAIL"] or counts["ERROR"]:
            ac_verdicts[ac] = "FAIL"
        elif counts["BLOCKED"]:
            ac_verdicts[ac] = "BLOCKED"
        elif counts["DEFERRED"]:
            ac_verdicts[ac] = "DEFERRED"
        elif counts["CANNOT-VERIFY"]:
            ac_verdicts[ac] = "CANNOT-VERIFY"
        elif counts["PASS"]:
            ac_verdicts[ac] = "PASS (partial)" if counts["SKIPPED"] else "PASS"
        else:
            ac_verdicts[ac] = "SKIPPED"

    n_fail = sum(1 for v in collector.records if v[2] in ("FAIL", "ERROR"))
    report = {
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "branch": _branch(),
        "sha": _sha(),
        "criteria": {ac: {"verdict": ac_verdicts[ac], **counts}
                     for ac, counts in by_ac.items()},
        "totals": {
            "tests": len(collector.records),
            "pass": sum(1 for r in collector.records if r[2] == "PASS"),
            "fail": n_fail,
            "blocked": sum(1 for r in collector.records
                           if r[2] == "BLOCKED"),
            "deferred": sum(1 for r in collector.records
                            if r[2] == "DEFERRED"),
            "cannot_verify": sum(1 for r in collector.records
                                 if r[2] == "CANNOT-VERIFY"),
        },
        "sign_off": ("WITHHELD — red criteria present"
                     if n_fail or any(v in ("BLOCKED", "DEFERRED",
                                            "CANNOT-VERIFY", "FAIL")
                                      for v in ac_verdicts.values())
                     else "Track 7 sign-off candidate — all criteria green"),
    }

    lines = ["# Track 7 validation report",
             f"- generated: {report['generated_at']}",
             f"- branch: {report['branch']} @ {report['sha']}",
             ""]
    for ac in sorted(by_ac):
        c = by_ac[ac]
        lines.append(
            f"## {ac}: {ac_verdicts[ac]}")
        lines.append(
            f"pass={c['PASS']} fail={c['FAIL']+c['ERROR']} "
            f"blocked={c['BLOCKED']} deferred={c['DEFERRED']} "
            f"cannot-verify={c['CANNOT-VERIFY']}")
        for d in c["details"]:
            lines.append(f"- [{d['verdict']}] {d['test']}: {d['detail']}")
        lines.append("")
    lines.append(f"**{report['sign_off']}**")
    md = "\n".join(lines)

    print(md)
    if args.report:
        day = datetime.date.today().isoformat()
        dest = os.path.join(REPO, "docs", "validation", "evidence",
                            f"{day}-{report['sha']}")
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "report.json"), "w") as fh:
            json.dump(report, fh, indent=2)
        with open(os.path.join(dest, "report.md"), "w") as fh:
            fh.write(md + "\n")
        print(f"\nevidence written to {dest}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
