"""Tests for the Stage 1 divergence backtest (design 06 §c).

The primary bar — false-suppress count on postmortem-confirmed real
SEV1/SEV2 — must be exactly ZERO, and the test must fail loudly when it
isn't.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import unittest

from sentinel.backtest import (LABEL_AMBIGUOUS, LABEL_NOISE,
                               LABEL_REAL_SEV12, LABEL_REAL_SEV3P,
                               LABELING_RULES_VERSION, BacktestIncident,
                               replay_backtest)
from sentinel.models import Disposition

from tests.helpers import make_alert


def _disp(action, conf=0.95):
    return Disposition(action=action, reason="threshold", team="platform",
                       confidence=conf, latency_ms=1.0)


def _incident(incident_id, label, severity, gate_action,
              human_handling=None, date="2026-09-15T03:00:00+00:00",
              postmortem_ref=None):
    alert = make_alert(service="payments-api", check="HighErrorRate",
                       severity="critical", alert_id=f"al-{incident_id}",
                       title=f"incident {incident_id}")
    return BacktestIncident(
        incident_id=incident_id, date=date, estate_severity=severity,
        severity_provenance="pd.priority at final resolution",
        postmortem_ref=postmortem_ref,
        human_handling=human_handling or [("2026-09-15T03:01:00+00:00", "paged"),
                                          ("2026-09-15T03:20:00+00:00",
                                           "resolved")],
        alerts=[alert], label=label,
        label_provenance="buyer incident review, 2026-10-01"), gate_action


class _Decider:
    """Frozen-gate stand-in: incident_id -> disposition."""

    def __init__(self, by_incident):
        self.by_incident = by_incident
        self.calls = []

    def __call__(self, alert):
        self.calls.append(alert.alert_id)
        incident_id = alert.alert_id.replace("al-", "")
        return self.by_incident[incident_id]


FREEZE = {"model_pin": "jev-1.13.0", "thresholds_version": "t-v3",
          "allowlist_snapshot": "al-2026-10-01", "fit_version": "fit-v3"}
WINDOW = {"start": "2026-04-01", "end": "2026-10-01"}


class TestBacktestZeroBar(unittest.TestCase):
    def test_zero_bar_passes(self):
        incs, actions = [], {}
        for i, sev in enumerate(("SEV1", "SEV2", "SEV2")):
            inc, act = _incident(f"SEV-{i}", LABEL_REAL_SEV12, sev, "page_now",
                                 postmortem_ref=f"pm-{i}")
            incs.append(inc)
            actions[f"SEV-{i}"] = _disp(act)
        ledger = replay_backtest(incs, _Decider(actions),
                                 config_freeze=FREEZE, window=WINDOW)
        s = ledger["summary"]
        self.assertEqual(s["false_suppress_real_sev12"], 0)
        self.assertTrue(s["zero_bar_passed"])
        self.assertEqual(s["sev12_incidents"], 3)
        self.assertEqual(ledger["header"]["labeling_rules_version"],
                         LABELING_RULES_VERSION)
        self.assertEqual(ledger["header"]["config_freeze"], FREEZE)
        # One row per historical SEV1/SEV2 (§f artifact 3).
        self.assertEqual(len(ledger["rows"]), 3)
        self.assertTrue(all(r["agreement"] == "agreement"
                            for r in ledger["rows"]))

    def test_zero_bar_fails_loudly(self):
        # The pre-mortem scenario: a real SEV2 the gate would have suppressed.
        inc, _ = _incident("SEV2-MISS", LABEL_REAL_SEV12, "SEV2", "suppress",
                           postmortem_ref="pm-99")
        ledger = replay_backtest([inc], _Decider({"SEV2-MISS": _disp("suppress")}),
                                 config_freeze=FREEZE, window=WINDOW)
        s = ledger["summary"]
        self.assertEqual(s["false_suppress_real_sev12"], 1)
        self.assertFalse(s["zero_bar_passed"])
        row = ledger["rows"][0]
        self.assertEqual(row["agreement"], "miss")
        self.assertEqual(row["regression_case_id"], "R-001")
        self.assertEqual(row["miss_mechanism"], "pending-review")
        self.assertEqual(len(s["regression_corpus"]), 1)

    def test_noise_precision_and_ambiguous_excluded(self):
        incs, actions = [], {}
        # 2 noise: 1 suppressed (true suppression), 1 paged.
        for i, act in enumerate(("suppress", "page_now")):
            inc, _ = _incident(f"N-{i}", LABEL_NOISE, "SEV4", act,
                               human_handling=[("t", "resolved")])
            incs.append(inc)
            actions[f"N-{i}"] = _disp(act)
        # 1 ambiguous: excluded from both denominators.
        inc, _ = _incident("A-0", LABEL_AMBIGUOUS, "SEV3", "suppress",
                           human_handling=[("t", "paged"), ("t", "resolved")])
        incs.append(inc)
        actions["A-0"] = _disp("suppress")
        ledger = replay_backtest(incs, _Decider(actions),
                                 config_freeze=FREEZE, window=WINDOW)
        s = ledger["summary"]
        self.assertEqual(s["noise_total"], 2)
        self.assertEqual(s["noise_suppressed"], 1)
        self.assertAlmostEqual(s["suppression_precision_on_noise"], 0.5)
        self.assertEqual(s["ambiguous_count"], 1)
        # Ambiguous suppression does NOT touch the zero bar.
        self.assertTrue(s["zero_bar_passed"])

    def test_page_vs_suppress_on_real_listed(self):
        # Humans never paged it; the gate would have — usually good news.
        inc, _ = _incident("REAL-3", LABEL_REAL_SEV3P, "SEV3", "page_now",
                           human_handling=[("t", "resolved")])
        ledger = replay_backtest([inc], _Decider({"REAL-3": _disp("page_now")}),
                                 config_freeze=FREEZE, window=WINDOW)
        s = ledger["summary"]
        self.assertEqual(len(s["page_vs_suppress_on_real"]), 1)
        self.assertEqual(s["page_vs_suppress_on_real"][0]["incident_id"],
                         "REAL-3")
        self.assertTrue(s["zero_bar_passed"])

    def test_bad_label_rejected(self):
        inc, _ = _incident("BAD", "definitely-real", "SEV2", "page_now")
        with self.assertRaises(ValueError):
            replay_backtest([inc], _Decider({"BAD": _disp("page_now")}),
                            config_freeze=FREEZE, window=WINDOW)

    def test_caveats_preserved_verbatim(self):
        inc, _ = _incident("C-1", LABEL_REAL_SEV12, "SEV2", "page_now")
        ledger = replay_backtest(
            [inc], _Decider({"C-1": _disp("page_now")}),
            config_freeze=FREEZE, window=WINDOW,
            caveats=("4.5 months available; 6-month minimum not met — "
                      "window accepted in writing by buyer incident review, "
                      "2026-10-20",))
        self.assertIn("4.5 months available",
                      ledger["header"]["caveats"][0])


if __name__ == "__main__":
    unittest.main()
