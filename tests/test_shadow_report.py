"""Tests for the Shadow Report generator (design 06 §b).

Scripted incident set with known outcomes -> the report must match the
expected agreement/divergence counts, and the zero-SEV1/SEV2 bar must
fire exactly when the pre-mortem scenario occurs.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import hashlib
import hmac
import json
import unittest

from sentinel.audit import AuditLog
from sentinel.correlator import Correlator, fingerprint_for
from sentinel.gate import Gate
from sentinel.models import Thresholds
from sentinel.shadow import ShadowConfig, ShadowPipeline, ShadowStore
from sentinel.shadow_report import (generate_shadow_report,
                                    render_report_markdown,
                                    zero_sev12_divergence_bar)

from tests.test_gate import canned
from tests.test_shadow import PD_SECRET, ScriptedClient, _pd_sig, _pd_v3


def _build_pipeline(by_title, allowlist):
    audit = AuditLog(":memory:")
    client = ScriptedClient(by_title, default=canned(p1=0.9, conf=0.95))
    gate = Gate(client, Thresholds(), set(allowlist), audit, shadow=True)
    config = ShadowConfig(enabled=True, pd_secret=PD_SECRET)
    return ShadowPipeline(gate=gate, correlator=Correlator(),
                          store=ShadowStore(), config=config,
                          allowlist=set(allowlist))


def _ingest(sp, payload):
    body = json.dumps(payload).encode()
    code, resp = sp.handle("pagerduty", body, _pd_sig(body))
    assert code == 202, (code, resp)
    return resp


def _fp(service, priority):
    return fingerprint_for(service, "pagerduty.incident", priority, "")


class TestShadowReport(unittest.TestCase):
    def _scripted_store(self):
        """11 episodes, fully determined outcomes (see module docstring)."""
        by_title, allowlist = {}, set()
        page = canned(p1=0.9, conf=0.95)
        suppress = canned(p1=0.0, conf=0.95, q3_choice="suppress",
                          q1_choice="p4_low")
        for i in range(6):
            by_title[f"page-agree-{i}"] = page
        for i in range(2):
            by_title[f"suppress-div-{i}"] = suppress
            allowlist.add(_fp("cache-eu", "P4"))
        by_title["sev2-miss"] = suppress
        allowlist.add(_fp("payments-api", "P2"))
        by_title["unseen-page"] = page
        by_title["unseen-suppress"] = suppress
        allowlist.add(_fp("web", "P4"))

        sp = _build_pipeline(by_title, allowlist)
        for i in range(6):
            _ingest(sp, _pd_v3(event_type="incident.triggered",
                               incident_id=f"P-AG-{i}",
                               title=f"page-agree-{i}",
                               priority="P3", service="payments-api"))
        for i in range(2):
            _ingest(sp, _pd_v3(event_type="incident.triggered",
                               incident_id=f"P-DV-{i}",
                               title=f"suppress-div-{i}",
                               priority="P4", service="cache-eu"))
        _ingest(sp, _pd_v3(event_type="incident.triggered",
                            incident_id="P-SEV2", title="sev2-miss",
                            priority="P2", service="payments-api"))
        # Tap connected mid-incident: only the priority update is observed.
        _ingest(sp, _pd_v3(event_type="incident.priority_updated",
                            incident_id="P-U1", title="unseen-page",
                            priority="P3", service="web"))
        _ingest(sp, _pd_v3(event_type="incident.priority_updated",
                            incident_id="P-U2", title="unseen-suppress",
                            priority="P4", service="web"))
        return sp.store

    def test_header_counts(self):
        report = generate_shadow_report(
            self._scripted_store(), org="buyer-acme", week_label="Oct 6",
            window_start="2026-10-01", window_end="2026-10-07")
        h = report["header"]
        self.assertEqual(h["alerts_observed"], 11)
        self.assertEqual(h["episodes_evaluated"], 11)
        self.assertEqual(h["human_paged"], 9)
        self.assertEqual(h["sentinel_would_page"], 7)
        self.assertEqual(h["sentinel_would_suppress"], 4)
        self.assertAlmostEqual(h["agreement_on_pages_pct"], 63.6, places=1)
        self.assertEqual(h["divergences_total"], 4)
        self.assertEqual(h["divergences_suppress_vs_page"], 3)
        self.assertEqual(h["divergences_page_vs_suppress"], 1)
        self.assertAlmostEqual(h["noise_reduction_opportunity_pct"], 36.4,
                               places=1)

    def test_divergence_list_shape(self):
        report = generate_shadow_report(
            self._scripted_store(), org="buyer-acme", week_label="Oct 6",
            window_start="2026-10-01", window_end="2026-10-07")
        divs = report["divergences"]
        self.assertEqual([d["id"] for d in divs],
                         ["D-001", "D-002", "D-003", "D-004"])
        self.assertEqual([d["direction"] for d in divs],
                         ["suppress-vs-page", "suppress-vs-page",
                          "suppress-vs-page", "page-vs-suppress"])
        d3 = divs[2]
        self.assertEqual(d3["severity"], "SEV2")
        self.assertEqual(d3["alert_key"], "pd:inc/P-SEV2")
        self.assertEqual(d3["human_did"], "paged")
        self.assertEqual(d3["gate_would"], "suppress")
        self.assertEqual(d3["status"], "OPEN")
        self.assertEqual(set(d3["threshold_counterfactual"]),
                         {"conf>=0.85", "conf>=0.90",
                          "conf>=0.95", "conf>=0.99"})
        self.assertTrue(d3["evidence_refs"]["incident_url"].endswith(
            "/incidents/P-SEV2"))

    def test_zero_sev12_bar_fires(self):
        report = generate_shadow_report(
            self._scripted_store(), org="buyer-acme", week_label="Oct 6",
            window_start="2026-10-01", window_end="2026-10-07")
        bar = report["zero_sev12_bar"]
        self.assertFalse(bar["passed"])
        self.assertEqual(bar["count"], 1)
        self.assertEqual(bar["rows"][0]["id"], "D-003")
        self.assertFalse(report["header"]["zero_sev12_divergences"])
        # The SEV1/SEV2 block is separate and highlighted (§b.2).
        self.assertEqual(len(report["sev12_divergences"]), 1)
        self.assertEqual(report["sev12_divergences"][0]["id"], "D-003")

    def test_zero_sev12_bar_passes(self):
        by_title = {"a": canned(p1=0.0, conf=0.95, q3_choice="suppress",
                                q1_choice="p4_low")}
        allowlist = {_fp("cache-eu", "P4")}
        sp = _build_pipeline(by_title, allowlist)
        _ingest(sp, _pd_v3(event_type="incident.triggered", incident_id="P-A",
                            title="a", priority="P4", service="cache-eu"))
        report = generate_shadow_report(
            sp.store, org="buyer-acme", week_label="Oct 6",
            window_start="2026-10-01", window_end="2026-10-07")
        self.assertTrue(report["zero_sev12_bar"]["passed"])
        self.assertTrue(report["header"]["zero_sev12_divergences"])
        self.assertEqual(report["sev12_divergences"], [])
        md = render_report_markdown(report)
        self.assertIn("Zero divergences on SEV1/SEV2.", md)

    def test_markdown_renders_doc_format(self):
        report = generate_shadow_report(
            self._scripted_store(), org="buyer-acme", week_label="Oct 6",
            window_start="2026-10-01", window_end="2026-10-07")
        md = render_report_markdown(report)
        self.assertIn("Shadow Report — week of Oct 6", md)
        self.assertIn("read-only, zero stack changes", md)
        self.assertIn("1 divergences on SEV1/SEV2.", md)
        self.assertIn("D-003  direction: suppress-vs-page", md)
        self.assertIn("## SEV1/SEV2 divergences", md)
        self.assertIn("## Calibration summary", md)
        self.assertIn("THIN — no claim", md)  # N=11 < 30 in every band
        self.assertIn("write credentials provisioned: **none**", md)
        self.assertIn("not a promise", md)  # opportunity labeling

    def test_tap_health_and_inventory(self):
        report = generate_shadow_report(
            self._scripted_store(), org="buyer-acme", week_label="Oct 6",
            window_start="2026-10-01", window_end="2026-10-07")
        th = report["tap_health"]
        self.assertEqual(th["ingested"], 11)
        self.assertEqual(th["dropped"], 0)
        self.assertEqual(report["credential_inventory"],
                         {"write_credentials": []})
        self.assertEqual(len(report["credential_inventory_hash"]), 12)

    def test_calibration_hold_fires(self):
        # 40 confident suppressions the humans paged: band 0.95–1.00,
        # N=40 ≥ 30, |Δ| = |0.975 − 1.0|*100 = 2.5pp → no hold.
        # Flip it: gate confident-suppress, humans paged on all 40 →
        # observed P(human paged)=1.0 vs gate 0.975 → 2.5pp. For a HOLD we
        # need >5pp: use conf 0.90 band... simpler: 40 page_now at conf
        # 0.95 with humans paging 40/40 → Δ=2.5pp no hold. Use a band
        # where the gate is badly wrong instead.
        by_title, allowlist = {}, set()
        for i in range(40):
            by_title[f"m{i}"] = canned(p1=0.9, conf=0.95)
        sp = _build_pipeline(by_title, allowlist)
        for i in range(40):
            _ingest(sp, _pd_v3(event_type="incident.priority_updated",
                                incident_id=f"P-M{i}", title=f"m{i}",
                                priority="P3", service="web"))
        # humans never paged these (no trigger observed), gate would page
        # at conf 0.95: observed P(human paged)=0 vs gate 0.975 → 97.5pp.
        report = generate_shadow_report(
            sp.store, org="x", week_label="w", window_start="a",
            window_end="b")
        band = [r for r in report["calibration"]
                if r["band"] == "0.95–1.00"][0]
        self.assertEqual(band["n"], 40)
        self.assertFalse(band["thin"])
        self.assertGreater(band["delta_pp"], 5.0)
        self.assertTrue(report["header"]["calibration_hold"])


if __name__ == "__main__":
    unittest.main()
