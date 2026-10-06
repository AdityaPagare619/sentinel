"""D3 regression tests: the storm-aggregate digest path (ADR-016).

The storm-*declaring* aggregate takes a separate code path — never the
race, never the triple lock. These tests prove suppress is unreachable
BY CONSTRUCTION on that path, and that the advisory Jev call (root-cause
candidates) never gates the disposition.
"""

import inspect
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from sentinel.audit import AuditLog
from sentinel.correlator import Correlator, fingerprint_for
from sentinel.eventlog import DISPOSITIONS
from sentinel.forwarder import Forwarder
from sentinel.freshness import FreshnessMonitor, FreshnessValidator
from sentinel.gate import Gate, evaluate_policy
from sentinel.models import Disposition, Thresholds
from sentinel.quantized import AllowlistEntry, Attestation
from sentinel.race import RaceConfig
from sentinel.receiver import Pipeline, ReceiverConfig, _aggregate_alert
from sentinel.state import build_state, input_sha256
from sentinel.storm_digest import (storm_digest_disposition,
                                   storm_root_cause_payload)

from tests.freshness_fixtures import (
    LABEL_PIPELINE,
    FixtureClock,
    build_bundle,
    make_allowlist_entry,
)
from tests.helpers import FakeClock, make_alert
from tests.test_gate import _answer, canned


def _wait_for(fn, timeout_s=8.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(0.02)
    return False


class _AlwaysAnswersClient:
    """Jev double that always answers — with the most suppress-shaped
    response possible. Proves the advisory answer cannot gate the digest."""
    timeout_s = 5.0

    def __init__(self):
        from sentinel.client import MockSystemOneClient
        self._mock = MockSystemOneClient({})
        self._resp = canned(p1=0.0, p2=0.0, p3=0.0, p4=1.0, conf=0.99,
                            q1_choice="p4_low", q3_choice="suppress")

    @property
    def calls(self):
        return self._mock.calls

    def decide(self, state, questions):
        from sentinel.client import MockSystemOneClient
        self._mock._calls.append({"state": state, "questions": questions})
        return self._resp


def _attested_entry(fp):
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    return AllowlistEntry(
        fingerprint=fp, author="carol",
        attestations=[
            Attestation("alice", now - timedelta(days=1), "lrq-9f2c-41ab", 30),
            Attestation("bob", now - timedelta(days=1), "lrq-9f2c-41ab", 30),
        ])


def _digest_gate(client, allowlist=None, **kw):
    audit = AuditLog(":memory:")
    gate = Gate(client, Thresholds(), allowlist or [], audit, **kw)
    return gate, audit


class TestDigestDisposition(unittest.TestCase):
    def test_signature_cannot_express_suppression(self):
        # BY CONSTRUCTION: no `action` parameter exists to set. A future
        # edit adding one is a P0 defect by design (see storm_digest.py).
        params = inspect.signature(storm_digest_disposition).parameters
        self.assertNotIn("action", params)

    def test_always_pages(self):
        disp = storm_digest_disposition(storm_size=42,
                                        storm_counts={"svc-a": 40, "svc-b": 2})
        self.assertEqual(disp.action, "page_now")
        self.assertEqual(disp.reason, "storm_digest")

    def test_folded_is_a_known_disposition(self):
        # The rename's consumers: event log must accept "folded".
        self.assertIn("folded", DISPOSITIONS)


class TestDigestStorm(unittest.TestCase):
    def _aggregate(self, gate_client=None):
        """A maximally suppress-shaped aggregate: allowlisted (dual
        attested) fingerprint, green Jev answers available. The digest
        must page anyway."""
        agg = make_alert(alert_id="storm-1", service="storm-aggregate")
        state = build_state(agg, {}, {})
        client = gate_client or _AlwaysAnswersClient()
        gate, _audit = _digest_gate(client,
                                    allowlist=[_attested_entry(agg.fingerprint)])
        self.addCleanup(gate.close)
        return gate, agg, state

    def test_all_green_aggregate_still_pages(self):
        gate, agg, state = self._aggregate()
        disp, rec = gate.digest_storm(
            agg, state, storm_size=50, storm_counts={"svc-a": 50})
        self.assertEqual(disp.action, "page_now")
        self.assertEqual(disp.reason, "storm_digest")
        self.assertEqual(rec.disposition.action, "page_now")

    def test_digest_never_routes_through_kernel(self):
        gate, agg, state = self._aggregate()
        gate.evaluate = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("digest_storm called evaluate()"))
        disp, _rec = gate.digest_storm(
            agg, state, storm_size=50, storm_counts={"svc-a": 50})
        self.assertEqual(disp.action, "page_now")

    def test_digest_never_calls_evaluate_policy(self):
        import sentinel.gate as gate_mod
        gate, agg, state = self._aggregate()
        called = []
        orig = gate_mod.evaluate_policy

        def spy(*a, **k):
            called.append(True)
            return orig(*a, **k)

        gate_mod.evaluate_policy = spy
        try:
            gate.digest_storm(agg, state, storm_size=50,
                              storm_counts={"svc-a": 50})
        finally:
            gate_mod.evaluate_policy = orig
        self.assertEqual(called, [])

    def test_emits_decision_made_page_now(self):
        gate, agg, state = self._aggregate()
        gate.digest_storm(agg, state, storm_size=50, storm_counts={"svc-a": 50})
        made = [p for p in gate.emitted if p["type"] == "decision_made"]
        self.assertEqual(len(made), 1)
        body = made[0]["body"]
        self.assertEqual(body["disposition"], "page_now")
        self.assertEqual(body["budget_outcome"], "structural_passthrough")

    def test_advisory_answer_never_gates_suppression(self):
        # The advisory Jev call answers "suppress" at conf 0.99 — the
        # disposition must remain page_now, and the advisory payload must
        # be marked as non-gating.
        gate, agg, state = self._aggregate()
        disp, _rec = gate.digest_storm(
            agg, state, storm_size=50, storm_counts={"svc-a": 50})
        self.assertEqual(disp.action, "page_now")
        found = _wait_for(lambda: any(
            p["type"] == "storm_root_cause_payload" for p in gate.emitted))
        self.assertTrue(found, "advisory payload was never emitted")
        adv = next(p for p in gate.emitted
                   if p["type"] == "storm_root_cause_payload")
        body = adv["body"]
        self.assertTrue(body["advisory"])
        self.assertFalse(body["gates_disposition"])
        self.assertEqual(body["storm_size"], 50)
        # The disposition did not move after the advisory landed.
        self.assertEqual(disp.action, "page_now")

    def test_shed_advisory_still_pages(self):
        # Pool queue of zero: the advisory is shed immediately. The page
        # must not care.
        agg = make_alert(alert_id="storm-2", service="storm-aggregate")
        state = build_state(agg, {}, {})
        gate, _audit = _digest_gate(
            _AlwaysAnswersClient(),
            race_config=RaceConfig(pool_size=1, pool_queue=0))
        self.addCleanup(gate.close)
        disp, _rec = gate.digest_storm(
            agg, state, storm_size=10, storm_counts={"svc-a": 10})
        self.assertEqual(disp.action, "page_now")

    def test_advisory_payload_shape(self):
        agg = make_alert()
        sev = _answer("severity", "p1_critical",
                      {"p1_critical": 0.9}, 0.88)
        team = _answer("owning_team", "data", {"data": 1.0}, 0.99)
        payload = storm_root_cause_payload(
            alert=agg, storm_size=7, storm_counts={"db": 7},
            jev_model="jev-1.13.0", severity_answer=sev, team_answer=team,
            latency_ms=123.0)
        self.assertEqual(payload["type"], "storm_root_cause_payload")
        body = payload["body"]
        self.assertTrue(body["advisory"])
        self.assertFalse(body["gates_disposition"])
        self.assertEqual(body["root_cause_candidates"]["severity"], "p1_critical")
        self.assertEqual(body["root_cause_candidates"]["owning_team"], "data")


class _RecordingForwarder:
    """Duck-typed forwarder: records, never touches the network."""

    def __init__(self):
        self.calls = []

    def forward(self, alert, disposition, raw_bytes=None):
        self.calls.append((alert, disposition))
        from sentinel.forwarder import ForwardResult
        return ForwardResult(forwarded=True, status_code=202, error=None,
                             action=disposition.action, dedup_key="k")


class TestReceiverStormDigest(unittest.TestCase):
    def test_storm_declaring_aggregate_pages_via_digest(self):
        # End-to-end through Pipeline._triage: drive the correlator to a
        # storm declaration, then assert the aggregate pages (never
        # suppresses) and is forwarded. C1/RFC §2.1: declaration is
        # W >= max(F, k*B) — with F=5 the 5th distinct ingest declares.
        clock = FakeClock()
        correlator = Correlator(storm_fingerprints=5, storm_window_s=60,
                                clock=clock)
        gate, _audit = _digest_gate(_AlwaysAnswersClient())
        self.addCleanup(gate.close)
        forwarder = _RecordingForwarder()
        pipeline = Pipeline(correlator, gate, forwarder, AuditLog(":memory:"),
                            ReceiverConfig())
        disp_action = None
        for i in range(5):
            alert = make_alert(alert_id=f"rx-{i}", service=f"svc{i}")
            clock.advance(1)
            disp_action = pipeline._triage(alert, b"{}")
        # The 5th ingest declared the storm: the aggregate paged.
        self.assertEqual(disp_action, "page_now")
        # 4 member pages (kind="new") + 1 aggregate page via the digest.
        self.assertEqual(len(forwarder.calls), 5)
        agg, agg_disp = forwarder.calls[-1]
        self.assertEqual(agg.service, "storm-aggregate")
        self.assertEqual(agg_disp.action, "page_now")
        self.assertEqual(agg_disp.reason, "storm_digest")

    def test_storm_continuation_folds_and_does_not_forward(self):
        # After declaration, members fold: no forward, action "folded".
        clock = FakeClock()
        correlator = Correlator(storm_fingerprints=5, storm_window_s=60,
                                clock=clock)
        gate, _audit = _digest_gate(_AlwaysAnswersClient())
        self.addCleanup(gate.close)
        forwarder = _RecordingForwarder()
        pipeline = Pipeline(correlator, gate, forwarder, AuditLog(":memory:"),
                            ReceiverConfig())
        for i in range(6):
            clock.advance(1)
            pipeline._triage(make_alert(alert_id=f"rx-{i}", service=f"svc{i}"),
                             b"{}")
        n_forwarded = len(forwarder.calls)
        clock.advance(1)
        action = pipeline._triage(make_alert(alert_id="rx-late", service="svcX"),
                                  b"{}")
        self.assertEqual(action, "folded")
        self.assertEqual(len(forwarder.calls), n_forwarded)  # no new forward


class TestFoldedForwarder(unittest.TestCase):
    def test_folded_not_forwarded_counted_separately(self):
        fw = Forwarder(pd_events_url="http://127.0.0.1:1/x",
                       default_routing_key="rk")
        alert = make_alert()
        res = fw.forward(alert, Disposition(action="folded", reason="storm",
                                            team=None, confidence=None,
                                            latency_ms=0.0))
        self.assertFalse(res.forwarded)
        self.assertEqual(fw.metrics["folded"], 1)
        self.assertEqual(fw.metrics["suppressed"], 0)


class TestShadowDigestMirror(unittest.TestCase):
    """D3 shadow-mirror regression (Tripwire probe): the shadow tap must
    record the digest would-be for storm-declared aggregates — never route
    them through gate.evaluate, where the race + triple lock could record
    'suppress' for an aggregate that production pages via digest_storm."""

    def _pipeline(self):
        from sentinel.shadow import (ShadowConfig, ShadowPipeline,
                                      ShadowStore, VendorEvent)
        self._VendorEvent = VendorEvent
        audit = AuditLog(":memory:")
        self.client = _AlwaysAnswersClient()
        fps = [fingerprint_for(f"svc-{i}", "pagerduty.incident",
                               "critical", "", env="", cluster="")
               for i in range(3)]
        # D1: suppress is unreachable without a freshness monitor (fail
        # closed). This test needs the pre-storm alerts to suppress, so it
        # runs against an all-fresh bundle — the freshness legs are not
        # the variable under test here.
        bundle_dir = build_bundle(
            tempfile.mkdtemp(prefix="sentinel-sd-"),
            entries=[(fp, "prod", make_allowlist_entry(fp)) for fp in fps])
        validator = FreshnessValidator(
            clock=FixtureClock(),
            deployed_label_pipeline_version=LABEL_PIPELINE)
        monitor = FreshnessMonitor(validator, bundle_dir)
        monitor.boot()
        gate = Gate(self.client, Thresholds(),
                    [_attested_entry(fp) for fp in fps],
                    audit, shadow=True, freshness_monitor=monitor)
        corr = Correlator(storm_fingerprints=2, storm_window_s=3600)
        config = ShadowConfig(enabled=True)
        return ShadowPipeline(gate=gate, correlator=corr,
                              store=ShadowStore(), config=config,
                              allowlist=set())

    def _ev(self, i):
        return self._VendorEvent(
            vendor="pagerduty", event_type="incident.triggered",
            event_id=f"ev-{i}", incident_id=f"INC-{i}",
            occurred_at="2026-10-04T00:00:00+00:00",
            service=f"svc-{i}", title=f"storm member {i}",
            severity_raw="critical", estate_severity="critical",
            incident_url=None, gate_relevant=True, raw={})

    def test_storm_declared_would_be_is_digest_page_not_suppress(self):
        # C1/RFC §2.1: declaration is W >= max(F, k*B) — with F=2 the 2nd
        # distinct event declares.
        pipeline = self._pipeline()
        for i in range(2):
            disp, rec, detail = pipeline._evaluate(self._ev(i))
            if i < 1:
                # The setup CAN suppress: pre-storm alerts go through the
                # gate with suppress-shaped answers and allowlisted
                # fingerprints — so a page_now on the 2nd is the digest
                # branch working, not the setup failing to suppress.
                self.assertEqual(rec.disposition.action, "suppress")
        # The storm-declaring aggregate's honest would-be is 'paged via the
        # aggregate' — recorded with the CAUSAL reason ("storm_digest") and
        # mode="shadow" (contract C3: shadow is a mode, never a reason),
        # never executed.
        self.assertEqual(rec.disposition.action, "page_now")
        self.assertEqual(rec.disposition.reason, "storm_digest")
        self.assertEqual(rec.disposition.mode, "shadow")
        self.assertEqual(disp.action, "passthrough")  # recorded, not executed
        self.assertEqual(disp.reason, "storm_digest")  # causal reason kept
        self.assertEqual(disp.mode, "shadow")
        # The digest path never consults Jev — not even in shadow.
        self.assertEqual(len(self.client.calls), 1)
        # No invented evidence on the digest path (honesty contract — W1).
        self.assertEqual(detail["probs"], {})
        self.assertIsNone(detail["conf3"])


if __name__ == "__main__":
    unittest.main()
