"""Tests for the ADR-022 suppression watchdog + dead-man's-switch (D8).

Covers: the B3 six-state lifecycle, SLO-band trips, the absolute guardrail,
review-due tightening, freeze-before-page trip ordering, the gate enforcement
hook, the separate dead-man's-switch watcher, and the adversarial cases:
watchdog killed mid-trip, heartbeat gap during clock skew, policy expiring
mid-incident.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import json
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from sentinel import policy_lifecycle as pl
from sentinel import watchdog as wd
from sentinel.audit import AuditLog
from sentinel.client import MockSystemOneClient
from sentinel.gate import Gate
from sentinel.models import Thresholds
from sentinel.quantized import AllowlistEntry, Attestation
from sentinel.state import build_state, input_sha256

from tests.helpers import make_alert
from tests.test_gate import _answer, canned


def _now():
    return datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


def _atts(policy_id="suppression", version=1, frm="shadow", to="canary"):
    return [pl.PolicyAttestation(
        policy_id=policy_id, version=version, from_state=frm, to_state=to,
        preview_hash="previewhash",
        attestor_ids=(f"att{i}",),
        decided_at=_now().isoformat()) for i in range(2)]


def _walk_to_live(store, pid="suppression"):
    """Drive a policy draft -> shadow -> canary -> live with valid sign-offs."""
    now = _now()
    v = store.create_draft(pid, {"thresholds": {"x": 1}}, now)
    store.transition(pid, v.version, "shadow", now=now, author_id="author")
    store.transition(pid, v.version, "canary", now=now, author_id="author",
                     attestations=_atts(pid, v.version, "shadow", "canary"),
                     preview_hash="previewhash")
    store.transition(pid, v.version, "live", now=now, author_id="author",
                     attestations=_atts(pid, v.version, "canary", "live"),
                     preview_hash="previewhash")
    return store._get(pid, v.version)


class LifecycleTest(unittest.TestCase):
    def test_full_walk_and_enforcement(self):
        store = pl.PolicyStore()
        v = _walk_to_live(store)
        self.assertEqual(v.state, "live")
        allowed, why = store.can_suppress("suppression")
        self.assertTrue(allowed)
        self.assertEqual(why, "ok")

    def test_illegal_transition_rejected(self):
        store = pl.PolicyStore()
        v = store.create_draft("p", {}, _now())
        with self.assertRaises(pl.PolicyTransitionError):
            store.transition("p", v.version, "live", now=_now(),
                             author_id="a")  # draft -> live is illegal

    def test_signoff_requires_two_distinct_attestors(self):
        store = pl.PolicyStore()
        v = store.create_draft("p", {}, _now())
        store.transition("p", v.version, "shadow", now=_now(), author_id="a")
        with self.assertRaises(pl.PolicyTransitionError):
            store.transition("p", v.version, "canary", now=_now(),
                             author_id="a",
                             attestations=_atts("p", v.version)[:1],
                             preview_hash="h")
        # same attestor twice is not two attestors
        same = [_atts("p", v.version, "shadow", "canary")[0],
                _atts("p", v.version, "shadow", "canary")[0]]
        with self.assertRaises(pl.PolicyTransitionError):
            store.transition("p", v.version, "canary", now=_now(),
                             author_id="a", attestations=same,
                             preview_hash="h")
        # author cannot attest their own transition
        sneaky = [pl.PolicyAttestation(
            policy_id="p", version=v.version, from_state="shadow",
            to_state="canary", preview_hash="h",
            attestor_ids=("a",), decided_at=_now().isoformat()),
            pl.PolicyAttestation(
            policy_id="p", version=v.version, from_state="shadow",
            to_state="canary", preview_hash="h",
            attestor_ids=("b",), decided_at=_now().isoformat())]
        with self.assertRaises(pl.PolicyTransitionError):
            store.transition("p", v.version, "canary", now=_now(),
                             author_id="a", attestations=sneaky,
                             preview_hash="h")

    def test_review_due_then_expired_automatic(self):
        store = pl.PolicyStore()
        v = _walk_to_live(store)
        v.review_at = (_now() - timedelta(seconds=1)).isoformat()
        v.grace_until = (_now() + timedelta(days=1)).isoformat()
        emitted = store.tick(_now())
        self.assertEqual(v.state, "review-due")
        self.assertTrue(any(e["type"] == "policy_review_due" for e in emitted))
        # review-due still suppresses (B3: still effective, under enforcement)
        allowed, _ = store.can_suppress("suppression")
        self.assertTrue(allowed)
        # grace end -> expired, loudly
        v.grace_until = (_now() - timedelta(seconds=1)).isoformat()
        emitted = store.tick(_now())
        self.assertEqual(v.state, "expired")
        self.assertTrue(any(e["type"] == "policy_expired" for e in emitted))
        allowed, why = store.can_suppress("suppression")
        self.assertFalse(allowed)
        self.assertEqual(why, "policy_expired")

    def test_no_expired_to_live_shortcut(self):
        store = pl.PolicyStore()
        v = _walk_to_live(store)
        v.state = "expired"
        with self.assertRaises(pl.PolicyTransitionError):
            store.transition("suppression", v.version, "live", now=_now(),
                             author_id="a")

    def test_emergency_override_timeboxed_and_reverts(self):
        store = pl.PolicyStore()
        v = store.create_draft("p", {}, _now())
        store.transition("p", v.version, "live", now=_now(),
                         author_id="incident-commander", emergency=True,
                         override_hours=2.0)
        self.assertEqual(v.state, "live")
        self.assertIsNotNone(v.override_until)
        emitted = store.tick(_now() + timedelta(hours=3))
        self.assertEqual(v.state, "expired")
        self.assertTrue(any(e["type"] == "policy_override_reverted"
                            for e in emitted))

    def test_freeze_blocks_suppress_unfreeze_needs_two(self):
        store = pl.PolicyStore()
        _walk_to_live(store)
        store.freeze("suppression", "watchdog_trip:slo_band")
        allowed, why = store.can_suppress("suppression")
        self.assertFalse(allowed)
        self.assertTrue(why.startswith("policy_frozen"))
        with self.assertRaises(pl.PolicyTransitionError):
            store.unfreeze("suppression", 1, _atts()[:1])
        # N4: attestors must be distinct from the author too.
        author_atts = [pl.PolicyAttestation(
            policy_id="suppression", version=1,
            from_state="live", to_state="live",
            preview_hash=None, attestor_ids=("author",),
            decided_at=_now().isoformat()),
            pl.PolicyAttestation(
            policy_id="suppression", version=1,
            from_state="live", to_state="live",
            preview_hash=None, attestor_ids=("dave",),
            decided_at=_now().isoformat())]
        with self.assertRaises(pl.PolicyTransitionError):
            store.unfreeze("suppression", 1, author_atts, author_id="author")
        store.unfreeze("suppression", 1, _atts(), author_id="author")
        allowed, _ = store.can_suppress("suppression")
        self.assertTrue(allowed)

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "policies.json")
            store = pl.PolicyStore(path)
            _walk_to_live(store)
            store.freeze("suppression", "test")
            store.save()
            store2 = pl.PolicyStore(path)
            allowed, why = store2.can_suppress("suppression")
            self.assertFalse(allowed)
            self.assertTrue(why.startswith("policy_frozen"))


def _baseline(**kw):
    d = dict(policy_id="suppression", version=1, window_s=3600,
             expected_rate=0.10, sigma=0.02, signed_by=("a", "b"),
             signed_at="2026-10-04T00:00:00+00:00")
    d.update(kw)
    return wd.SuppressionBaseline(**d)


class WatchdogKernelTest(unittest.TestCase):
    def _feed(self, w, n, suppressed, now=None):
        now = now if now is not None else time.time()
        for i in range(n):
            w.ingest("suppression", i < suppressed, ts=now - i)

    def test_no_trip_within_band(self):
        w = wd.SuppressionWatchdog({"suppression": _baseline()})
        self._feed(w, 100, 10)  # exactly the expected rate
        self.assertIsNone(w.evaluate("suppression"))

    def test_trip_on_band_deviation(self):
        w = wd.SuppressionWatchdog({"suppression": _baseline()})
        self._feed(w, 100, 40)  # 0.40 vs 0.10 +- 3*0.02
        trip = w.evaluate("suppression")
        self.assertIsNotNone(trip)
        self.assertEqual(trip.reason, "slo_band")
        self.assertGreater(trip.deviation_sigmas, 3.0)

    def test_no_trip_on_tiny_sample(self):
        w = wd.SuppressionWatchdog({"suppression": _baseline()})
        self._feed(w, 10, 10)  # 100% but n < MIN_DECISIONS: noise, not signal
        self.assertIsNone(w.evaluate("suppression"))

    def test_absolute_guardrail_trips_regardless(self):
        w = wd.SuppressionWatchdog(
            {"suppression": _baseline(expected_rate=0.60, sigma=0.01,
                                      absolute_max_rate=0.50)})
        self._feed(w, 100, 60)
        trip = w.evaluate("suppression")
        self.assertIsNotNone(trip)
        self.assertEqual(trip.reason, "absolute_guardrail")

    def test_review_due_tightening(self):
        base = _baseline()
        w = wd.SuppressionWatchdog({"suppression": base})
        # 0.15 = 2.5 sigma: inside the 3-sigma live band, outside 2-sigma
        self._feed(w, 200, 30)
        self.assertIsNone(w.evaluate("suppression", review_due=False))
        trip = w.evaluate("suppression", review_due=True)
        self.assertIsNotNone(trip)
        self.assertEqual(trip.reason, "review_due_drift")

    def test_baseline_requires_two_signers(self):
        w = wd.SuppressionWatchdog()
        with self.assertRaises(ValueError):
            w.set_baseline(_baseline(signed_by=("only-one",)))

    def test_window_pruning(self):
        w = wd.SuppressionWatchdog({"suppression": _baseline(window_s=100)})
        now = 1_000_000.0
        for i in range(50):
            w.ingest("suppression", True, ts=now - 10_000 - i)  # ancient
        self._feed(w, 40, 4, now=now)  # in-window: exactly expected
        self.assertIsNone(w.evaluate("suppression", now=now))


class TripOrderingTest(unittest.TestCase):
    """Belt and suspenders: freeze is durable BEFORE the page goes out."""

    def test_page_dies_stops_heartbeat_and_parks_for_retry(self):
        # B1 capability semantics: the heartbeat attests CAPABILITY, not
        # liveness. The page died (save succeeded, so the freeze IS
        # durable) — the undelivered page parks in _pending and the
        # heartbeat stops. The dead-man's-switch watcher pages on the
        # missing beat. When the page path heals, the next run_once
        # flushes the backlog and the heartbeat resumes.
        with tempfile.TemporaryDirectory() as d:
            store = pl.PolicyStore(os.path.join(d, "policies.json"))
            _walk_to_live(store)
            w = wd.SuppressionWatchdog({"suppression": _baseline()})
            hb = wd.HeartbeatEmitter(os.path.join(d, "heartbeat.json"),
                                     interval_s=3600)
            pages = []

            def dead_page(summary, detail):
                raise RuntimeError("simulated kill mid-trip")

            runner = wd.WatchdogRunner(
                watchdog=w, policy_store=store, heartbeat=hb,
                read_decisions=lambda since: ([], since),
                page_human=dead_page,
                append_event=lambda t, b: None)
            for i in range(100):
                w.ingest("suppression", i < 40)
            report = runner.run_once()
            self.assertEqual(len(report["trips"]), 1)
            status = report["trips"][0]
            # Freeze landed (save worked); the page did not.
            self.assertTrue(status["freeze_durable"])
            self.assertFalse(status["paged"])
            store2 = pl.PolicyStore(os.path.join(d, "policies.json"))
            allowed, why = store2.can_suppress("suppression")
            self.assertFalse(allowed)
            self.assertTrue(why.startswith("policy_frozen"))
            # Capability degraded: heartbeat WITHHELD, not flowing.
            self.assertEqual(hb.seq, 0)
            self.assertFalse(os.path.exists(os.path.join(d, "heartbeat.json")))
            self.assertTrue(any("withheld" in e for e in report["errors"]))
            # Heal the page path: the backlog flushes, the beat resumes.
            # (Clearing the ingested window simulates the realistic
            # sequence: once the freeze bites, suppressions stop and the
            # rate normalizes — otherwise the stale window re-trips.)
            w._windows.clear()
            runner._page_human = lambda s, det: pages.append(s)
            report2 = runner.run_once()
            self.assertEqual(len(runner._pending), 0)
            self.assertEqual(len(pages), 1)
            self.assertGreater(hb.seq, 0)

    def test_trip_save_and_page_fail_nothing_delivered_no_heartbeat(self):
        # B1 exact failing case (Tripwire tw57): persistent save failure
        # (disk full) + page failure (PD down), process stays alive.
        # Before the fix: both swallowed, heartbeat kept beating, freeze
        # never persisted, nobody paged. After: nothing is claimed
        # delivered, the heartbeat stops, and a fresh store load proves
        # the freeze is NOT durable (can_suppress still True).
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "policies.json")
            store = pl.PolicyStore(path)
            _walk_to_live(store)
            store.save()
            w = wd.SuppressionWatchdog({"suppression": _baseline()})
            hb = wd.HeartbeatEmitter(os.path.join(d, "heartbeat.json"),
                                     interval_s=3600)

            real_save = store.save

            def dead_save():
                raise OSError("disk full (persistent)")

            def dead_page(summary, detail):
                raise RuntimeError("PagerDuty down")

            store.save = dead_save  # noqa: E731 — fault injection
            runner = wd.WatchdogRunner(
                watchdog=w, policy_store=store, heartbeat=hb,
                read_decisions=lambda since: ([], since),
                page_human=dead_page,
                append_event=lambda t, b: None)
            for i in range(100):
                w.ingest("suppression", i < 40)
            report = runner.run_once()
            status = report["trips"][0]
            self.assertFalse(status["freeze_durable"])
            self.assertFalse(status["paged"])
            # Heartbeat stopped: capability lost, watcher must page.
            self.assertEqual(hb.seq, 0)
            self.assertFalse(os.path.exists(os.path.join(d, "heartbeat.json")))
            # The freeze is NOT durable: a fresh load still allows suppress.
            fresh = pl.PolicyStore(path)
            allowed, why = fresh.can_suppress("suppression")
            self.assertTrue(allowed)
            self.assertEqual(why, "ok")
            # Heal disk + PD: pending flushes, freeze lands, page goes out,
            # heartbeat resumes. (Clearing the window simulates the rate
            # normalizing once the freeze bites — see above.)
            store.save = real_save
            pages = []
            w._windows.clear()
            runner._page_human = lambda s, det: pages.append(s)
            runner.run_once()
            self.assertEqual(len(runner._pending), 0)
            self.assertEqual(len(pages), 1)
            fresh2 = pl.PolicyStore(path)
            allowed2, why2 = fresh2.can_suppress("suppression")
            self.assertFalse(allowed2)
            self.assertTrue(why2.startswith("policy_frozen"))
            self.assertGreater(hb.seq, 0)


class LifecycleRunnerTest(unittest.TestCase):
    def test_tick_pages_owner_on_review_due_and_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            store = pl.PolicyStore(os.path.join(d, "p.json"))
            v = _walk_to_live(store)
            v.review_at = "2026-10-04T11:59:00+00:00"
            v.grace_until = "2026-10-04T12:30:00+00:00"
            events, pages = [], []
            hb = wd.HeartbeatEmitter(os.path.join(d, "hb.json"),
                                     interval_s=3600)
            runner = wd.WatchdogRunner(
                watchdog=wd.SuppressionWatchdog(), policy_store=store,
                heartbeat=hb,
                read_decisions=lambda since: ([], since),
                page_human=lambda s, det: pages.append(s),
                append_event=lambda t, b: events.append(t),
                clock=lambda: datetime(2026, 10, 4, 12, 0,
                                       tzinfo=timezone.utc).timestamp())
            runner.run_once()
            self.assertIn("policy_review_due", events)
            self.assertTrue(any("review-due" in p for p in pages))
            # now expire it mid-incident: suppression must stop
            v.grace_until = "2026-10-04T11:00:00+00:00"
            runner.run_once()
            self.assertIn("policy_expired", events)
            allowed, why = store.can_suppress("suppression")
            self.assertFalse(allowed)
            self.assertEqual(why, "policy_expired")


class GatePolicyHookTest(unittest.TestCase):
    def _suppressing_gate(self, policy_gate=None):
        alert = make_alert()
        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        entry = AllowlistEntry(
            fingerprint=alert.fingerprint, author="carol",
            attestations=[Attestation("alice", now - timedelta(days=1),
                                      "lrq-9f2c-41ab", 30),
                          Attestation("bob", now - timedelta(days=1),
                                      "lrq-9f2c-41ab", 30)])
        state = build_state(alert, {}, {})
        client = MockSystemOneClient(
            {input_sha256(state): canned(p1=0.0, conf=0.95)})
        audit = AuditLog(":memory:")
        return Gate(client, Thresholds(), [entry], audit,
                    policy_gate=policy_gate), alert, state

    def test_suppress_flows_when_policy_allows(self):
        gate, alert, state = self._suppressing_gate(
            policy_gate=type("G", (), {"can_suppress":
                                       lambda self, pid: (True, "ok")})())
        disp, _ = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")

    def test_frozen_policy_downgrades_to_passthrough(self):
        gate, alert, state = self._suppressing_gate(
            policy_gate=type("G", (), {"can_suppress":
                                       lambda self, pid: (
                                           False,
                                           "policy_frozen:watchdog_trip:slo_band")})())
        disp, _ = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "passthrough")
        self.assertTrue(disp.reason.startswith("policy_blocked:"))

    def test_expired_policy_downgrades_to_passthrough(self):
        with tempfile.TemporaryDirectory() as d:
            store = pl.PolicyStore(os.path.join(d, "p.json"))
            v = _walk_to_live(store)
            v.state = "expired"  # policy expiring mid-incident
            gate, alert, state = self._suppressing_gate(policy_gate=store)
            disp, _ = gate.evaluate(alert, state, {}, {})
            self.assertEqual(disp.action, "passthrough")
            self.assertIn("policy_expired", disp.reason)

    def test_unreadable_policy_state_pages(self):
        class Exploding:
            def can_suppress(self, pid):
                raise RuntimeError("disk gone")

        gate, alert, state = self._suppressing_gate(policy_gate=Exploding())
        disp, _ = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "passthrough")
        self.assertIn("policy_state_unreadable", disp.reason)

    def test_no_hook_preserves_behavior(self):
        gate, alert, state = self._suppressing_gate(policy_gate=None)
        disp, _ = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")


class WatcherScriptTest(unittest.TestCase):
    SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                          "scripts", "ops", "watchdog_watcher.py")

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, self.SCRIPT, *args],
            capture_output=True, text=True, timeout=30)

    def _write_beat(self, path, at_epoch, seq=7):
        with open(path, "w") as fh:
            json.dump({"watchdog_id": "watchdog-1", "seq": seq,
                       "at": datetime.fromtimestamp(
                           at_epoch, timezone.utc).isoformat(),
                       "at_epoch": at_epoch}, fh)

    def test_fresh_heartbeat_ok(self):
        with tempfile.TemporaryDirectory() as d:
            hb = os.path.join(d, "hb.json")
            self._write_beat(hb, time.time() - 5)
            r = self._run("--heartbeat-file", hb, "--no-page")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_stale_heartbeat_pages_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            hb = os.path.join(d, "hb.json")
            self._write_beat(hb, time.time() - 3600)
            r = self._run("--heartbeat-file", hb, "--no-page")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("DRY RUN would page", r.stdout)

    def test_missing_heartbeat_pages(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._run("--heartbeat-file",
                          os.path.join(d, "nope.json"), "--no-page")
            self.assertEqual(r.returncode, 2)

    def test_future_heartbeat_within_skew_tolerance_warns(self):
        # Beat 60s in the future: inside the 120s skew grace -> warning, no page
        with tempfile.TemporaryDirectory() as d:
            hb = os.path.join(d, "hb.json")
            self._write_beat(hb, time.time() + 60)
            r = self._run("--heartbeat-file", hb, "--no-page")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_future_heartbeat_beyond_skew_pages(self):
        # Beat 1h in the future: clocks disagree, dead-man's void -> page
        with tempfile.TemporaryDirectory() as d:
            hb = os.path.join(d, "hb.json")
            self._write_beat(hb, time.time() + 3600)
            r = self._run("--heartbeat-file", hb, "--no-page")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("clock skew", r.stdout)


class PolicyGateTest(unittest.TestCase):
    """B2: the hot-path enforcement view. Fresh reads every decision,
    fail-closed on unreadable, loud-but-allowing on unconfigured."""

    def _live_store(self, d):
        path = os.path.join(d, "policies.json")
        store = pl.PolicyStore(path)
        _walk_to_live(store)
        store.save()
        return path, store

    def test_freeze_lands_within_one_decision_no_restart(self):
        # The accepted deviation: no boot cache — a watchdog freeze is
        # visible to the very next can_suppress call.
        with tempfile.TemporaryDirectory() as d:
            path, store = self._live_store(d)
            gate = pl.PolicyGate(path)
            self.assertEqual(gate.can_suppress("suppression"), (True, "ok"))
            store.freeze("suppression", "watchdog_trip:slo_band")
            store.save()
            allowed, why = gate.can_suppress("suppression")
            self.assertFalse(allowed)
            self.assertTrue(why.startswith("policy_frozen"))

    def test_unreadable_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "policies.json")
            with open(path, "w") as fh:
                fh.write("{corrupt")
            allowed, why = pl.PolicyGate(path).can_suppress("suppression")
            self.assertFalse(allowed)
            self.assertEqual(why, "policy_state_unreadable")

    def test_missing_file_allows_with_loud_warning(self):
        # Not-configured is not unreadable: the triple lock stands alone
        # until the store is initialized — but loudly.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nope.json")
            gate = pl.PolicyGate(path)
            import io
            from contextlib import redirect_stderr
            buf = io.StringIO()
            with redirect_stderr(buf):
                allowed, why = gate.can_suppress("suppression")
            self.assertTrue(allowed)
            self.assertEqual(why, "policy_gate_not_configured")
            self.assertIn("WARNING", buf.getvalue())
            # Warned once: the second call stays quiet.
            buf2 = io.StringIO()
            with redirect_stderr(buf2):
                gate.can_suppress("suppression")
            self.assertEqual(buf2.getvalue(), "")

    def test_gate_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            # A directory is not a readable file.
            allowed, why = pl.PolicyGate(d).can_suppress("suppression")
            self.assertFalse(allowed)
            self.assertEqual(why, "policy_state_unreadable")


class BaselinePersistenceTest(unittest.TestCase):
    """B3: baselines survive restarts; live policies without one alarm LOUD."""

    def _runner(self, d, baselines=None, pages=None, events=None):
        path = os.path.join(d, "policies.json")
        store = pl.PolicyStore(path)
        _walk_to_live(store)
        store.save()
        w = wd.SuppressionWatchdog(baselines)
        hb = wd.HeartbeatEmitter(os.path.join(d, "hb.json"), interval_s=3600)
        runner = wd.WatchdogRunner(
            watchdog=w, policy_store=store, heartbeat=hb,
            read_decisions=lambda since: ([], since),
            page_human=(lambda s, det: pages.append(s)) if pages is not None
            else (lambda s, det: None),
            append_event=(lambda t, b: events.append(t)) if events is not None
            else (lambda t, b: None))
        return runner, store, path

    def test_baselines_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            pages, events = [], []
            runner, store, path = self._runner(d, pages=pages, events=events)
            runner.register_baseline(_baseline())
            # "Restart": brand-new watchdog + runner over the same file.
            w2 = wd.SuppressionWatchdog()
            hb2 = wd.HeartbeatEmitter(os.path.join(d, "hb2.json"),
                                      interval_s=3600)
            runner2 = wd.WatchdogRunner(
                watchdog=w2, policy_store=pl.PolicyStore(path),
                heartbeat=hb2,
                read_decisions=lambda since: ([], since),
                page_human=lambda s, det: pages.append(s),
                append_event=lambda t, b: events.append(t))
            self.assertIn("suppression", w2.baselines)
            # And it still trips: feed a tripping rate, no baseless alarm.
            for i in range(100):
                w2.ingest("suppression", i < 40)
            report = runner2.run_once()
            self.assertEqual(len(report["trips"]), 1)
            self.assertNotIn("watchdog_baseless_policy", events)

    def test_baseless_live_policy_alarms_loud(self):
        # No baseline registered: the policy is UNWATCHED — audited event
        # + page, never silent. (Emergency overrides land here by design.)
        with tempfile.TemporaryDirectory() as d:
            pages, events = [], []
            runner, store, path = self._runner(d, pages=pages, events=events)
            runner.run_once()
            self.assertIn("watchdog_baseless_policy", events)
            self.assertTrue(any("BLIND" in p for p in pages))
            # Throttled: no re-page on the next loop...
            pages.clear()
            events.clear()
            runner.run_once()
            self.assertNotIn("watchdog_baseless_policy", events)
            self.assertEqual(pages, [])
            # ...but a re-baselined-then-lost policy re-arms the alarm.
            runner.register_baseline(_baseline())
            runner.run_once()
            self.assertNotIn("watchdog_baseless_policy", events)

    def test_baseline_version_mismatch_alarms(self):
        # Baseline signed for v1, policy now at v2: the old numbers don't
        # govern the new content — alarm.
        with tempfile.TemporaryDirectory() as d:
            pages, events = [], []
            runner, store, path = self._runner(d, pages=pages, events=events)
            runner.register_baseline(_baseline(version=1))
            store.create_draft("suppression", {"thresholds": {"y": 2}},
                               _now())
            v2 = store._get("suppression", 2)
            v2.state = "live"
            store.save()
            runner.run_once()
            self.assertIn("watchdog_baseless_policy", events)
            self.assertTrue(any("BLIND" in p for p in pages))


class HeartbeatEmitterTest(unittest.TestCase):
    def test_emits_on_interval_with_seq(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hb.json")
            t = [1_000_000.0]
            hb = wd.HeartbeatEmitter(path, interval_s=60,
                                     clock=lambda: t[0])
            self.assertIsNotNone(hb.maybe_emit(force=True))
            self.assertIsNone(hb.maybe_emit())  # too soon
            t[0] += 61
            beat = hb.maybe_emit()
            self.assertIsNotNone(beat)
            self.assertEqual(beat["seq"], 2)
            with open(path) as fh:
                self.assertEqual(json.load(fh)["seq"], 2)


if __name__ == "__main__":
    unittest.main()
