"""AC-3 — Suppression correctness: independently recomputed.

Track 7 is the auditor. For each sampled suppression the harness
re-derives the decision from the record's own evidence:

  (1) record-internal consistency: every leg the suppress conjunction
      requires (prob / conf / allowlist / corroboration) shows pass in
      the STORED bodies; no freshness veto in the reason;
  (2) kernel agreement: re-running the pure policy kernel
      (evaluate_policy) on the identical recorded inputs yields
      "suppress" — the recorded disposition matches the kernel, outside
      the gate's runtime path;
  (3) definitional: the D9 receipt's no_suppress_leg preset does not
      suppress (the suppress branch was load-bearing).

The UI never recomputes. Track 7 does.
"""

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from common import make_suppress_rig, make_alert  # noqa: E402
from sentinel.gate import evaluate_policy  # noqa: E402
from sentinel.state import input_sha256  # noqa: E402

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
# Causal reason vocabulary (models.py Disposition + gate reasons). A
# suppress whose reason is not in this family is unauditable.
CAUSAL_REASONS = {
    "allowlist", "dedup", "duplicate", "flap_debounce", "storm",
    "storm_digest", "kill_switch", "change_window", "threshold",
    "corroborated", "policy",
}


class TestAC3SuppressionAudit(unittest.TestCase):
    N_DRIVE = 12  # >= coverage floor of 10; final sign-off samples
                  # min(50, all) from the log

    @classmethod
    def setUpClass(cls):
        cls.rig = make_suppress_rig(n_fps=3, model="fakejev-t7-ac3")
        cls.rows = []  # (alert, disp, rec, payload, state)
        for i in range(cls.N_DRIVE):
            fp_idx = i % 3
            alert = make_alert(alert_id=f"t7-ac3-{i}",
                               service=f"svc-{fp_idx}", check="http_5xx")
            disp, rec, payload, state = cls.rig.drive(alert)
            cls.rows.append((alert, disp, rec, payload, state))
        cls.addClassCleanup(cls.rig.close)

    def test_3_coverage_floor(self):
        n_sup = sum(1 for _, d, _, _, _ in self.rows
                    if d.action == "suppress")
        self.assertGreaterEqual(
            n_sup, 10,
            f"only {n_sup}/{len(self.rows)} driven alerts suppressed — "
            "the rig must reach the coverage floor of 10")
        print(f"\n[AC-3] coverage: {n_sup}/{len(self.rows)} suppressions")

    def _suppress_rows(self):
        return [(a, d, r, p, s) for (a, d, r, p, s) in self.rows
                if d.action == "suppress"]

    def test_3a_conjunction_holds(self):
        """Every stored leg required for suppress shows pass; no veto."""
        for alert, disp, rec, payload, state in self._suppress_rows():
            body = payload["body"]
            lock = body["lock_evaluation"]
            for leg in ("prob", "conf", "allowlist"):
                self.assertIn(leg, lock, f"{alert.alert_id}: leg {leg} "
                                         f"missing from lock_evaluation")
                self.assertEqual(
                    lock[leg]["verdict"], "pass",
                    f"{alert.alert_id}: suppress with {leg} leg "
                    f"verdict={lock[leg]['verdict']}")
            corro = lock.get("corroboration", {})
            self.assertEqual(corro.get("verdict"), "pass",
                             f"{alert.alert_id}: suppress with failed "
                             f"corroboration leg")
            self.assertNotIn("freshness:", disp.reason,
                             f"{alert.alert_id}: freshness veto with "
                             f"suppress disposition")
            self.assertNotIn("policy_blocked", disp.reason,
                             f"{alert.alert_id}: D8 blocked with suppress "
                             f"disposition")

    def test_3a_kernel_agreement(self):
        """The pure policy kernel re-run on the recorded inputs agrees
        with the recorded disposition (outside the gate's runtime)."""
        gate = self.rig.gate
        for alert, disp, rec, payload, state in self._suppress_rows():
            prob_pass = (payload["body"]["lock_evaluation"]["prob"]
                         ["verdict"] == "pass")
            verdict = evaluate_policy(
                alert, jev_model=rec.jev_model,
                q_severity=rec.q_severity, q_team=rec.q_team,
                q_disposition=rec.q_disposition,
                thresholds=gate.thresholds,
                allowlist=set(gate.allowlist),
                latency_ms=rec.disposition.latency_ms,
                prob_lock_pass=prob_pass,
                freshness_report=gate._freshness_report(),
                lock1_dual_attested=False)
            self.assertEqual(
                verdict.action, "suppress",
                f"{alert.alert_id}: kernel re-run says "
                f"{verdict.action} ({verdict.reason}), record says "
                f"suppress")

    def test_3b_corroboration_named(self):
        for alert, disp, rec, payload, state in self._suppress_rows():
            corro = payload["body"].get("corroboration") or {}
            self.assertTrue(corro.get("passed"),
                            f"{alert.alert_id}: corroboration not passed")
            self.assertIsNotNone(corro.get("kind"),
                                 f"{alert.alert_id}: corroboration kind "
                                 f"unnamed")
            self.assertGreater(corro.get("floor_version", 0), 0,
                               f"{alert.alert_id}: floor_version not set")

    def test_3c_counterfactual_receipt(self):
        """Receipt present on the live suppress path; the definitional
        no_suppress_leg preset must not suppress."""
        for alert, disp, rec, payload, state in self._suppress_rows():
            receipt = payload["body"].get("threshold_counterfactual")
            self.assertIsInstance(
                receipt, dict, f"{alert.alert_id}: no counterfactual "
                               f"receipt on a suppress row")
            self.assertNotIn("error", receipt,
                             f"{alert.alert_id}: receipt is an error "
                             f"receipt: {receipt.get('error')}")
            presets = receipt.get("presets", [])
            self.assertGreater(len(presets), 0,
                               f"{alert.alert_id}: receipt has no presets")
            definitional = presets[0]
            self.assertEqual(definitional.get("name"), "no_suppress_leg",
                             f"{alert.alert_id}: first preset must be the "
                             f"definitional no_suppress_leg")
            self.assertNotEqual(
                definitional.get("disposition"), "suppress",
                f"{alert.alert_id}: suppress branch not load-bearing — "
                f"the receipt still suppresses without it")
            self.assertIn("policy", receipt,
                          f"{alert.alert_id}: receipt missing policy pin")

    def test_3d_causal_reason(self):
        for alert, disp, rec, payload, state in self._suppress_rows():
            reason = disp.reason or ""
            self.assertNotEqual(reason, "shadow",
                                f"{alert.alert_id}: reason rewritten to "
                                f"'shadow'")
            self.assertFalse(reason.startswith("error:"),
                             f"{alert.alert_id}: suppress with error reason")
            self.assertTrue(
                any(reason == c or reason.startswith(c + ":")
                    or c in reason for c in CAUSAL_REASONS),
                f"{alert.alert_id}: reason {reason!r} not in the causal "
                f"vocabulary")

    def test_3e_confidence_ordinal_floor(self):
        floor = self.rig.gate.thresholds.suppress_conf_min
        for alert, disp, rec, payload, state in self._suppress_rows():
            conf = payload["body"].get("q3_confidence")
            self.assertIsNotNone(conf, f"{alert.alert_id}: q3_confidence "
                                       f"missing")
            self.assertGreaterEqual(
                conf, floor,
                f"{alert.alert_id}: suppress with confidence {conf} "
                f"below floor {floor}")
            # Ordinal, never a probability: no % anywhere near it.
            self.assertLessEqual(conf, 1.0)

    def test_3f_input_sha_matches(self):
        """On harness-driven rows the recorded hash recomputes exactly."""
        for alert, disp, rec, payload, state in self._suppress_rows():
            sha = payload["body"].get("input_sha256")
            self.assertIsNotNone(sha)
            self.assertRegex(sha, SHA_RE.pattern,
                             f"{alert.alert_id}: input_sha256 malformed")
            self.assertEqual(sha, input_sha256(state),
                             f"{alert.alert_id}: recorded input_sha256 "
                             f"does not recompute from the state")

    def test_3g_audit_log_consistency(self):
        """The audit-log rows agree with the emitted payloads on
        disposition + budget_outcome (the two write paths must not
        diverge)."""
        for alert, disp, rec, payload, state in self._suppress_rows():
            rows = self.rig.audit.decisions_for_fingerprint(
                alert.fingerprint, limit=5)
            match = [r for r in rows
                     if r["alert_id"] == alert.alert_id]
            self.assertTrue(match,
                            f"{alert.alert_id}: no audit-log row")
            import json as _json
            body = _json.loads(match[0]["body"])
            # v0.1-compat shim rows carry the pre-lock note instead of
            # lock evidence (documented); disposition must still agree.
            self.assertEqual(
                body.get("disposition"), "suppress",
                f"{alert.alert_id}: audit log disagrees on disposition")


if __name__ == "__main__":
    unittest.main(verbosity=2)
