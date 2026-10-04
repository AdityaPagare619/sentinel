"""ADR-015 (D2) — exact model-version pinning + 7-day re-validation.

Ratification conditions under test:
  (a) hot-path ``response.model == pinned`` assertion producing a NAMED
      ``model_drift`` event (passthrough + drift page);
  (b) the client stops floating ``jev-latest`` by default — pinned model
      required, explicit opt-out only with a loud boot warning;
  (c) the 7-day re-validation as an automated job with a named owner,
      wiring the backtest replay machinery.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sentinel.audit import AuditLog
from sentinel.client import (FLOATING_MODEL_ALIAS, DecisionResponse,
                             MockSystemOneClient, SystemOneClient,
                             _state_fingerprint, client_from_env)
from sentinel.eventlog import EVENT_TYPES, _BODY_REQUIRED
from sentinel.gate import Gate
from sentinel.models import Thresholds
from sentinel.quantized import AllowlistEntry, Attestation
from sentinel.race import LateAnswer
from sentinel.race_payloads import model_drift_payload
from sentinel.revalidation import (JOB_NAME, JOB_OWNER,
                                   REVALIDATION_INTERVAL_DAYS,
                                   RevalidationRefused, load_corpus,
                                   run_weekly_revalidation)
from sentinel.state import build_state, input_sha256

from tests.freshness_fixtures import (LABEL_PIPELINE, FixtureClock,
                                      build_bundle, make_allowlist_entry,
                                      make_fit_proof,
                                      make_threshold_attestation,
                                      make_thresholds)
from tests.helpers import make_alert
from tests.test_gate import _answer, canned

PINNED = "jev-1.13.0"
DRIFTED = "jev-1.14.0"


def _scripted_gate(response, pinned_model=PINNED, **gate_kw):
    alert = make_alert()
    state = build_state(alert, {}, {})
    client = MockSystemOneClient({input_sha256(state): response})
    gate = Gate(client, Thresholds(), [], AuditLog(":memory:"),
                pinned_model=pinned_model, **gate_kw)
    return gate, alert, state


def _drift_events(gate):
    return [p for p in gate.emitted if p.get("type") == "model_drift"]


def _decision_mades(gate):
    return [p for p in gate.emitted if p.get("type") == "decision_made"]


# ---------------------------------------------------------------------------
# (b) The client stops floating jev-latest by default
# ---------------------------------------------------------------------------

class TestClientPinning(unittest.TestCase):
    def test_missing_model_refused(self):
        with self.assertRaises(ValueError) as ctx:
            SystemOneClient(api_key="k")
        self.assertIn("pinned", str(ctx.exception))

    def test_empty_model_refused(self):
        for bad in ("", "   "):
            with self.assertRaises(ValueError, msg=f"model={bad!r}"):
                SystemOneClient(api_key="k", model=bad)

    def test_floating_alias_refused_without_opt_out(self):
        with self.assertRaises(ValueError) as ctx:
            SystemOneClient(api_key="k", model=FLOATING_MODEL_ALIAS)
        self.assertIn("allow_floating_model", str(ctx.exception))

    def test_floating_opt_out_constructs_but_warns_loud(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            client = SystemOneClient(api_key="k", model="jev-latest",
                                     allow_floating_model=True)
        self.assertEqual(client.model, "jev-latest")
        warned = buf.getvalue()
        self.assertIn("CRITICAL", warned)
        self.assertIn("jev-latest", warned)
        self.assertIn("ADR-015", warned)

    def test_pinned_model_accepted(self):
        client = SystemOneClient(api_key="k", model="jev-1.14.0")
        self.assertEqual(client.model, "jev-1.14.0")

    def test_pinned_model_sent_on_wire(self):
        # The wire carries the pinned id — never the floating alias.
        import urllib.request
        from tests.test_client import FakeHTTPResponse, FAKE_RESPONSE

        sent = {}

        class Opener:
            def __init__(self, payload):
                self.payload = payload

            def __call__(self, req, timeout=None):
                sent["data"] = req.data
                return FakeHTTPResponse(self.payload)

        orig = urllib.request.urlopen
        urllib.request.urlopen = Opener(FAKE_RESPONSE)
        try:
            client = SystemOneClient(api_key="k", model="jev-1.14.0",
                                     retry_budget_s=30.0)
            client.decide({"s": 1}, {})
        finally:
            urllib.request.urlopen = orig
        body = json.loads(sent["data"].decode("utf-8"))
        self.assertEqual(body["model"], "jev-1.14.0")

    def test_mock_defaults_to_fake_pin(self):
        self.assertEqual(MockSystemOneClient().model, "jev-mock-0.0.0")

    def test_client_from_env_requires_pin(self):
        os.environ["TYPESAFE_API_KEY"] = "env-key"
        try:
            with self.assertRaises(ValueError):
                client_from_env()
            client = client_from_env(model="jev-1.13.0")
            self.assertEqual(client.model, "jev-1.13.0")
        finally:
            del os.environ["TYPESAFE_API_KEY"]


# ---------------------------------------------------------------------------
# (a) Hot-path response.model == pinned assertion
# ---------------------------------------------------------------------------

class TestModelDriftHotPath(unittest.TestCase):
    def test_mismatch_means_passthrough_and_named_drift_event(self):
        """mismatch ⇒ verdict passthrough + named model_drift event (the
        drift page). Suppression is unreachable on a drifted answer."""
        resp = canned(model=DRIFTED, q3_choice="suppress", conf=0.99)
        gate, alert, state = _scripted_gate(resp)
        disp, rec = gate.evaluate(alert, state, {}, {})
        try:
            self.assertEqual(disp.action, "passthrough")
            self.assertEqual(disp.reason, "model_drift")

            drifts = _drift_events(gate)
            self.assertEqual(len(drifts), 1)
            body = drifts[0]["body"]
            self.assertEqual(body["expected_model"], PINNED)
            self.assertEqual(body["observed_model"], DRIFTED)
            self.assertEqual(body["decision_phase"], "gate")

            # The sibling decision_made pages with the drift named in the
            # reason — the drift page, not a silent passthrough.
            mades = _decision_mades(gate)
            self.assertEqual(len(mades), 1)
            self.assertEqual(mades[0]["body"]["disposition"], "passthrough")
            self.assertIn("untrusted evidence", mades[0]["body"]
                          ["lock_evaluation"]["prob"]["detail"])

            # The decision record still carries the observed model for
            # forensics — Vault's "vendor moved" is distinguishable from
            # "we forgot to re-fit".
            self.assertEqual(rec.jev_model, DRIFTED)
        finally:
            gate.close()

    def test_match_reaches_policy_kernel_no_drift_event(self):
        resp = canned(model=PINNED)  # q3_choice defaults page_business_hours
        gate, alert, state = _scripted_gate(resp)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        try:
            self.assertEqual(_drift_events(gate), [])
            self.assertNotEqual(disp.reason, "model_drift")
        finally:
            gate.close()

    def test_undeclared_model_is_drift(self):
        """A response that declares NO model is untrusted evidence —
        uncertainty pages (Law 7), and the drift event names it."""
        resp = canned(model=PINNED)
        resp.model = None
        gate, alert, state = _scripted_gate(resp)
        disp, _rec = gate.evaluate(alert, state, {}, {})
        try:
            self.assertEqual(disp.action, "passthrough")
            self.assertEqual(disp.reason, "model_drift")
            drifts = _drift_events(gate)
            self.assertEqual(len(drifts), 1)
            self.assertIsNone(drifts[0]["body"]["observed_model"])
        finally:
            gate.close()

    def test_no_pin_means_no_drift_check_but_loud_boot(self):
        """pinned_model=None ⇒ drift detection inactive — and the gate
        says so loudly at boot instead of pretending."""
        import sentinel.gate as gate_mod
        gate_mod._PIN_WARNED = False  # the boot warning is once-per-process
        buf = io.StringIO()
        with redirect_stderr(buf):
            gate, alert, state = _scripted_gate(canned(model=DRIFTED),
                                               pinned_model=None)
        self.assertIn("model_drift detection is INACTIVE", buf.getvalue())
        disp, _rec = gate.evaluate(alert, state, {}, {})
        try:
            self.assertEqual(_drift_events(gate), [])
            self.assertNotEqual(disp.reason, "model_drift")
        finally:
            gate.close()

    def test_late_answer_drift_emits_event_never_pages(self):
        """Drift in a detached late answer: the named event fires
        (decision_phase=late_answer) but the decision — already
        passthrough — is never re-opened."""
        gate, alert, state = _scripted_gate(canned(model=PINNED))
        try:
            late = LateAnswer(
                alert=alert, input_sha256=input_sha256(state),
                episode_id=None,
                response=canned(model=DRIFTED),
                latency_ms=4200.0, budget_ms=2700)
            gate._on_late_answer(late)

            drifts = _drift_events(gate)
            self.assertEqual(len(drifts), 1)
            self.assertEqual(drifts[0]["body"]["decision_phase"],
                             "late_answer")
            self.assertEqual(drifts[0]["body"]["observed_model"], DRIFTED)

            shadows = [p for p in gate.emitted
                       if p.get("type") == "shadow_decision"]
            self.assertEqual(len(shadows), 1)
            self.assertEqual(shadows[0]["body"]["error_class"],
                             "model_drift")
            self.assertFalse(shadows[0]["body"]["would_have_suppressed"])
            # The late answer never pages, never suppresses, never
            # re-opens: no decision_made on this path.
            self.assertEqual(_decision_mades(gate), [])
        finally:
            gate.close()

    def test_model_drift_is_a_registered_event_type(self):
        self.assertIn("model_drift", EVENT_TYPES)
        self.assertEqual(
            set(_BODY_REQUIRED["model_drift"]),
            {"input_sha256", "expected_model", "observed_model",
             "decision_phase", "links"})

    def test_model_drift_payload_shape(self):
        alert = make_alert()
        payload = model_drift_payload(
            alert=alert, input_sha256="abc",
            expected_model=PINNED, observed_model=DRIFTED,
            decision_phase="gate", latency_ms=12.5)
        self.assertEqual(payload["type"], "model_drift")
        self.assertEqual(payload["actor"], "engine")
        body = payload["body"]
        self.assertEqual(body["expected_model"], PINNED)
        self.assertEqual(body["observed_model"], DRIFTED)
        self.assertEqual(body["decision_phase"], "gate")
        self.assertIn("links", body)


# ---------------------------------------------------------------------------
# (c) The 7-day re-validation job
# ---------------------------------------------------------------------------

def _probe_script(pin):
    """Scripted answers for the pin probe + one corpus state."""
    probe_state = {"probe": "model-pin",
                   "instructions": "Reply with one choice."}
    fp = _state_fingerprint(probe_state)
    resp = DecisionResponse(model=pin, answers={}, input_tokens=1)
    return {fp: resp}


class TestRevalidationJob(unittest.TestCase):
    def _job_inputs(self, *, probe_pin=PINNED, suppress_on_sev12=False):
        alert = make_alert(alert_id="job-a1")
        state = build_state(alert, {}, {})
        script = _probe_script(probe_pin)
        if suppress_on_sev12:
            script[input_sha256(state)] = canned(
                model=probe_pin, p1=0.0, conf=0.95, q3_choice="suppress")
        else:
            script[input_sha256(state)] = canned(
                model=probe_pin, q3_choice="page_now", conf=0.5)
        client = MockSystemOneClient(script)
        incident = {
            "incident_id": "INC-001",
            "date": "2026-09-20T03:12:00+00:00",
            "estate_severity": "SEV2",
            "severity_provenance": "pd:incident.priority@final",
            "postmortem_ref": "PM-77",
            "human_handling": [["2026-09-20T03:13:00+00:00", "paged"]],
            "label": "real-SEV1/SEV2",
            "label_provenance": "postmortem tag",
            "alerts": [{
                "alert": {
                    "alert_id": "job-a1",
                    "received_at": "2026-09-20T03:12:00+00:00",
                    "fingerprint": alert.fingerprint,
                    "service": "web", "check": "http_5xx",
                    "severity_in": "critical", "title": "boom",
                    "source": "pagerduty",
                },
                "state": state, "history": {}, "context": {},
            }],
        }
        return client, [incident], alert

    def _wired(self, client, incident_rows, alert):
        """ incidents rows -> (incidents, replay_map) via a corpus file,
        with a fresh bundle + dual-attested allowlist entry so the
        suppress path is reachable (mirrors production wiring)."""
        tmp = tempfile.mkdtemp(prefix="sentinel-d2-")
        corpus_path = os.path.join(tmp, "corpus.jsonl")
        with open(corpus_path, "w", encoding="utf-8") as fh:
            for row in incident_rows:
                fh.write(json.dumps(row) + "\n")
        incidents, replay_map = load_corpus(corpus_path)

        bundle_dir = tempfile.mkdtemp(prefix="sentinel-d2-bundle-")
        build_bundle(
            bundle_dir,
            entries=[(alert.fingerprint, "prod",
                      make_allowlist_entry(alert.fingerprint))])
        from sentinel.freshness import FreshnessMonitor, FreshnessValidator
        mon = FreshnessMonitor(
            FreshnessValidator(clock=FixtureClock(),
                               deployed_label_pipeline_version=LABEL_PIPELINE),
            bundle_dir)
        mon.boot()
        assert mon.current_report().stale_locks() == []

        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        entry = AllowlistEntry(
            fingerprint=alert.fingerprint, author="carol",
            attestations=[
                Attestation("alice", now - timedelta(days=1),
                            "lrq-9f2c-41ab", 30),
                Attestation("bob", now - timedelta(days=1),
                            "lrq-9f2c-41ab", 30),
            ])
        return incidents, replay_map, mon, [entry], tmp

    def test_job_passes_clean_corpus_with_named_owner(self):
        client, rows, alert = self._job_inputs()
        incidents, replay_map, mon, entries, tmp = self._wired(
            client, rows, alert)
        report_path = os.path.join(tmp, "report.json")
        report = run_weekly_revalidation(
            client=client, pinned_model=PINNED,
            incidents=incidents, replay_map=replay_map,
            thresholds=Thresholds(), allowlist=entries,
            freshness_monitor=mon,
            report_path=report_path)

        self.assertEqual(report["job"], JOB_NAME)
        # ADR-015 ratification condition (c): the NAMED owner is in the
        # report, not in a wiki page.
        self.assertEqual(report["owner"], JOB_OWNER)
        self.assertIn("Pager", report["owner"])
        self.assertEqual(report["interval_days"],
                         REVALIDATION_INTERVAL_DAYS)
        self.assertTrue(report["pin_probe"]["match"])
        self.assertFalse(report["drift_detected"])
        self.assertTrue(report["zero_bar_passed"])
        self.assertEqual(report["verdict"], "PASS")
        # The signature loop is visibly OPEN until a human signs.
        self.assertIsNone(report["human_signoff"])
        # The report was written to disk.
        with open(report_path, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["verdict"], "PASS")
        self.assertEqual(on_disk["owner"], JOB_OWNER)

    def test_job_fails_when_pin_probe_drifts(self):
        # The vendor remapped under us: the probe sees jev-1.14.0 while
        # the pin says jev-1.13.0. The job must FAIL — not replay on a
        # drifted model, and not report green.
        client, rows, alert = self._job_inputs(probe_pin=DRIFTED)
        incidents, replay_map, mon, entries, tmp = self._wired(
            client, rows, alert)
        report = run_weekly_revalidation(
            client=client, pinned_model=PINNED,
            incidents=incidents, replay_map=replay_map,
            thresholds=Thresholds(), allowlist=entries,
            freshness_monitor=mon)
        self.assertTrue(report["drift_detected"])
        self.assertEqual(report["pin_probe"]["observed"], DRIFTED)
        self.assertEqual(report["verdict"], "FAIL")

    def test_job_fails_when_zero_bar_breaks(self):
        # The replay suppresses a postmortem-confirmed real SEV1/SEV2 —
        # the one non-negotiable number. Verdict FAIL.
        client, rows, alert = self._job_inputs(suppress_on_sev12=True)
        incidents, replay_map, mon, entries, tmp = self._wired(
            client, rows, alert)
        report = run_weekly_revalidation(
            client=client, pinned_model=PINNED,
            incidents=incidents, replay_map=replay_map,
            thresholds=Thresholds(), allowlist=entries,
            freshness_monitor=mon)
        self.assertFalse(report["drift_detected"])
        self.assertFalse(report["zero_bar_passed"])
        self.assertEqual(
            report["ledger_summary"]["false_suppress_real_sev12"], 1)
        self.assertEqual(report["verdict"], "FAIL")

    def test_zero_evidence_refused_not_green(self):
        client = MockSystemOneClient(_probe_script(PINNED))
        with self.assertRaises(RevalidationRefused):
            run_weekly_revalidation(
                client=client, pinned_model=PINNED,
                incidents=[], replay_map={},
                thresholds=Thresholds())

    def test_unreadable_corpus_refused(self):
        with self.assertRaises(RevalidationRefused):
            load_corpus("/nonexistent/corpus.jsonl")

    def test_empty_corpus_refused(self):
        tmp = tempfile.mkdtemp(prefix="sentinel-d2-")
        path = os.path.join(tmp, "corpus.jsonl")
        open(path, "w").close()
        with self.assertRaises(RevalidationRefused) as ctx:
            load_corpus(path)
        self.assertIn("empty", str(ctx.exception))

    def test_bad_corpus_row_refused(self):
        tmp = tempfile.mkdtemp(prefix="sentinel-d2-")
        path = os.path.join(tmp, "corpus.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"incident_id": "x", "label": "bogus",
                                 "alerts": []}) + "\n")
        with self.assertRaises(RevalidationRefused):
            load_corpus(path)

    def test_no_pin_refused(self):
        client = MockSystemOneClient(_probe_script(PINNED))
        with self.assertRaises(RevalidationRefused):
            run_weekly_revalidation(
                client=client, pinned_model="",
                incidents=["dummy"], replay_map={},
                thresholds=Thresholds())


if __name__ == "__main__":
    unittest.main()
