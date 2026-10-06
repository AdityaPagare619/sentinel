"""AC-1 — Kill-switch drill: measured, sticky, attributed.

Contract C3 (Track 3, merged). test_1a runs Track 3's scripts/kill_drill.py
and asserts the measured flip->halt ms < 5000. test_1b wires the STICKINESS
harness against the real HTTP surface (POST /api/v1/safety/kill on the real
PlatformApp) plus the real kill-wired DurableForwarder scheduler path.
test_1c wires the re-arm ceremony: separate deliberate action requiring an
explicit {"confirm": true}.

Artifact semantics (the code, not the docstring): while engaged, the
forwarder HALTS — claimed rows re-queue (never dropped) and resume on
re-arm; the gate's decisioning is untouched. Sticky = engaged persists
(in-memory AND in the state file) across repeated flips; nothing but the
explicit re-arm disarms.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
DRILL_SCRIPT = os.path.join(REPO, "scripts", "kill_drill.py")
APP_PY = os.path.join(REPO, "platform", "server", "app.py")
SRC = os.path.join(REPO, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import subprocess  # noqa: E402
import shutil  # noqa: E402

from platform_harness import (  # noqa: E402
    make_platform_app, wsgi_call, auth_headers, json_body)
from sentinel import safety as _safety  # noqa: E402
from sentinel.eventlog import EventLog, utcnow_iso  # noqa: E402
from sentinel.forwarder import DurableForwarder, ForwarderConfig  # noqa: E402

T3_MISSING = ("BLOCKED: Track 3 not merged — no kill endpoint "
              "(/api/v1/safety/kill), no `mode` field on the disposition "
              "record, no scripts/kill_drill.py on this branch")


def _track3_present():
    if not os.path.exists(DRILL_SCRIPT):
        return False, "scripts/kill_drill.py absent"
    with open(APP_PY) as fh:
        src = fh.read()
    if "/api/v1/safety/kill" not in src:
        return False, "kill endpoint absent from platform/server/app.py"
    return True, ""


def _enqueue_page(log, i):
    """One page_now decision + outbox row (shape copied from kill_drill.py)."""
    alert_id, fp = f"t7-ac1-a{i}", f"t7-ac1-fp-{i:04d}"
    frozen = json.dumps({
        "event_action": "trigger",
        "dedup_key": f"sentinel/t7-ac1/{fp}",
        "payload": {"summary": f"[T7] AC-1 page {i}",
                     "severity": "critical", "source": "t7-ac1",
                     "custom_details": {"t7": True}},
    }, sort_keys=True)
    now = utcnow_iso()
    seq, obid = log.record_decision_and_enqueue(
        alert_id=alert_id, fingerprint=fp, episode_id=fp,
        body={"disposition": "page_now",
              "mode": "live",
              "budget_outcome": "answered_in_time",
              "lock_evaluation": {}, "freshness": {},
              "threshold_counterfactual": {}, "links": {},
              "v01_compat": {"reason": "threshold"}},
        outbox={"alert_id": alert_id, "fingerprint": fp, "episode_id": fp,
                "dedup_key": f"sentinel/t7-ac1/{fp}",
                "routing_key_ref": "T7_AC1_DRILL_KEY",
                "payload_frozen": frozen,
                "payload_sha256": hashlib.sha256(
                    frozen.encode()).hexdigest(),
                "priority": 0, "next_attempt_at": now,
                "max_age_at": now})
    return obid


class _KillHarness:
    """Real KillSwitch (file-backed state) + real PlatformApp + real
    kill-wired DurableForwarder scheduler. No threads started, so no
    network: run_once() exercises the scheduler claim path only."""

    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="t7-ac1-")
        self.log = EventLog(os.path.join(self.tmpdir, "engine.db"))
        self.ks = _safety.KillSwitch(
            log=self.log,
            state_path=os.path.join(self.tmpdir, "kill-switch.json"))
        self.app, self.token, self.tokens, _td = make_platform_app(
            kill_switch=self.ks)
        # make_platform_app owns its own tmpdir; we do not use _td's app.
        self._app_tmpdir = _td
        self.fwd = DurableForwarder(self.log, kill_switch=self.ks)

    def call(self, method, path, token=None, body=None, headers=None):
        hdrs = dict(headers or {})
        if token is not None:
            hdrs["Authorization"] = f"Bearer {token}"
        return wsgi_call(self.app, method, path, headers=hdrs, body=body)

    def close(self):
        try:
            self.log.close()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        shutil.rmtree(self._app_tmpdir, ignore_errors=True)


class TestAC1KillDrill(unittest.TestCase):
    """The drill, in full, wired to Track 3's real implementation."""

    def setUp(self):
        present, why = _track3_present()
        if not present:
            self.skipTest(f"{T3_MISSING} ({why})")
        self.h = _KillHarness()
        self.addCleanup(self.h.close)

    # ------------------------------------------------------------- 1a/1d

    def test_1a_drill_artifact_runs_and_measures(self):
        """Track 3's scripts/kill_drill.py runs against the real gate and
        prints the measured flip->halt ms. PASS: ms < 5000."""
        proc = subprocess.run(
            [sys.executable, DRILL_SCRIPT, "--json"],
            cwd=REPO, capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0,
                         f"kill_drill.py failed:\n{proc.stderr[-2000:]}")
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"kill_drill.py did not print JSON:\n"
                      f"{proc.stdout[-2000:]}")
        ms = data.get("measured_ms")
        self.assertIsNotNone(ms, "drill printed no measured_ms")
        self.assertLess(ms, 5000,
                        f"measured flip->halt {ms} ms >= 5000 ms")
        print(f"\n[AC-1a] measured flip->halt: {ms:.0f} ms")

    def test_1d_kill_above_jev_control_principle(self):
        """Code-path inspection: the kill check sits above the race,
        before any Jev call — Jev can never flip, delay, or reinterpret
        the kill."""
        gate_py = os.path.join(REPO, "src", "sentinel", "gate.py")
        with open(gate_py) as fh:
            src = fh.read()
        # The kill gate must be consulted before the race is armed.
        kill_pos = src.find("kill")
        race_pos = src.find("runner.run(")
        self.assertGreater(kill_pos, 0, "no kill reference in gate.py")
        self.assertLess(kill_pos, race_pos,
                        "kill check must precede the race arm in gate.py")

    # ---------------------------------------------------------------- 1b

    def test_1b_sticky_no_self_revive(self):
        """Sticky: after the flip, repeated flips are idempotent no-ops —
        the switch NEVER self-revives into disarmed. The engaged state
        persists in the state file (survives process restart) and the
        kill-wired forwarder halts (claims nothing, drops nothing) while
        engaged."""
        h = self.h
        T = h.token

        # 0. Start disengaged; the kill endpoint needs auth (C1).
        st, _, body = h.call("POST", "/api/v1/safety/kill")
        self.assertEqual(st, 401, "kill endpoint must 401 without a token")
        self.assertEqual(json_body(body), {"error": "unauthorized"})
        st, _, body = h.call("GET", "/api/v1/safety/status", token=T)
        self.assertEqual(json_body(body)["engaged"], False)

        # 1. Prove the forwarder scheduler is hot BEFORE the flip.
        ob_hot = _enqueue_page(h.log, 1)
        claimed_before = h.fwd.run_once()
        self.assertGreater(claimed_before, 0,
                           "forwarder claimed nothing pre-flip — "
                           "hot-path baseline missing")

        # 2. Flip via the real HTTP surface.
        st, _, body = h.call("POST", "/api/v1/safety/kill", token=T)
        data = json_body(body)
        self.assertEqual(st, 200, f"kill flip failed: {body!r}")
        self.assertTrue(data["engaged"])
        self.assertTrue(data["transitioned"],
                        "first flip must be a transition")
        engaged_at = data["engaged_at"]

        # 3. Forwarder halts: a fresh page enqueued AFTER the flip is never
        #    claimed while engaged — and never dropped.
        ob_fresh = _enqueue_page(h.log, 2)
        claimed_during = h.fwd.run_once()
        self.assertEqual(claimed_during, 0,
                         "forwarder claimed rows while the kill was engaged")
        row = h.log.outbox_row(ob_fresh)
        self.assertEqual(row["status"], "queued",
                         "fresh page was not kept queued during the halt")

        # 4. Second and third flips: idempotent, NO self-revive.
        for n in (2, 3):
            st, _, body = h.call("POST", "/api/v1/safety/kill", token=T)
            data = json_body(body)
            self.assertEqual(st, 200)
            self.assertTrue(data["engaged"],
                            f"flip #{n} disarmed the switch — NOT sticky")
            self.assertFalse(data["transitioned"],
                             f"flip #{n} re-transitioned — flip is not "
                             "idempotent")
            self.assertEqual(data["engaged_at"], engaged_at,
                             f"flip #{n} moved engaged_at — the switch "
                             "re-flipped instead of staying sticky")

        # 5. Stickiness survives process restart: a fresh KillSwitch on the
        #    same state file reads engaged.
        fresh = _safety.KillSwitch(
            state_path=os.path.join(h.tmpdir, "kill-switch.json"))
        self.assertTrue(fresh.engaged,
                        "state file does not persist the engaged flag — "
                        "a restarted process would come up disarmed")

        # 6. Audit log records transitions, not button mashes: exactly one
        #    kill_switch_engaged event despite three flips.
        engaged_events = h.log.events_by_type("kill_switch_engaged")
        self.assertEqual(len(engaged_events), 1,
                         f"expected 1 engaged audit event, got "
                         f"{len(engaged_events)}")

        st, _, body = h.call("GET", "/api/v1/safety/status", token=T)
        self.assertTrue(json_body(body)["engaged"],
                        "status shows disarmed while the switch is sticky")
        print(f"\n[AC-1b] sticky verified: 3 flips, 1 transition, "
              f"forwarder halted (0 claims), state-file durable")

    # ---------------------------------------------------------------- 1c

    def test_1c_rearm_separate_deliberate(self):
        """Re-arm is a SEPARATE deliberate action: the kill endpoint never
        disarms (covered by 1b), and /api/v1/safety/rearm requires an
        explicit {"confirm": true} body — anything else 422s. After the
        deliberate re-arm the forwarder resumes the queued backlog."""
        h = self.h
        T = h.token

        # Engage first.
        st, _, body = h.call("POST", "/api/v1/safety/kill", token=T)
        self.assertTrue(json_body(body)["engaged"])

        # Re-arm needs auth too.
        st, _, body = h.call("POST", "/api/v1/safety/rearm",
                             body={"confirm": True})
        self.assertEqual(st, 401)

        # Every non-explicit-confirmation body is refused.
        for bad in (None, {}, {"confirm": False}, {"confirm": "yes"},
                    {"confirmation": True}):
            st, _, body = h.call("POST", "/api/v1/safety/rearm", token=T,
                                 body=bad)
            self.assertEqual(st, 422,
                             f"rearm with body={bad!r} must 422, got {st}")
            self.assertEqual(json_body(body)["error"],
                             "rearm_requires_confirmation")

        # Still engaged after all the refused attempts.
        st, _, body = h.call("GET", "/api/v1/safety/status", token=T)
        self.assertTrue(json_body(body)["engaged"],
                        "refused re-arm attempts changed the engaged state")

        # A page queued during the halt waits for the re-arm.
        ob_wait = _enqueue_page(h.log, 7)
        self.assertEqual(h.fwd.run_once(), 0)

        # The deliberate re-arm.
        st, _, body = h.call("POST", "/api/v1/safety/rearm", token=T,
                             body={"confirm": True})
        data = json_body(body)
        self.assertEqual(st, 200, f"rearm failed: {body!r}")
        self.assertFalse(data["engaged"])
        self.assertTrue(data["transitioned"],
                        "rearm on an engaged switch must transition")

        # Status confirms disarmed; audit records the rearm.
        st, _, body = h.call("GET", "/api/v1/safety/status", token=T)
        self.assertFalse(json_body(body)["engaged"])
        rearmed_events = h.log.events_by_type("kill_switch_rearmed")
        self.assertEqual(len(rearmed_events), 1,
                         "expected exactly 1 kill_switch_rearmed audit event")

        # Re-arm on a disarmed switch is a no-op, not an error.
        st, _, body = h.call("POST", "/api/v1/safety/rearm", token=T,
                             body={"confirm": True})
        data = json_body(body)
        self.assertEqual(st, 200)
        self.assertFalse(data["engaged"])
        self.assertFalse(data["transitioned"])

        # The forwarder resumes the queued backlog after the deliberate
        # re-arm — nothing was dropped by the halt.
        resumed = h.fwd.run_once()
        self.assertGreater(resumed, 0,
                           "forwarder did not resume the queued backlog "
                           "after re-arm")

        # Full cycle still works: kill again after re-arm transitions.
        st, _, body = h.call("POST", "/api/v1/safety/kill", token=T)
        data = json_body(body)
        self.assertTrue(data["engaged"] and data["transitioned"],
                        "kill after re-arm must transition again")
        print(f"\n[AC-1c] re-arm ceremony verified: 5 refused bodies 422'd, "
              f"explicit confirm rearmed, backlog resumed ({resumed} claimed)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
