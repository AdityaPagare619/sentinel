"""Operator kill surface: cross-process flag-flip + audit event.

RFC docs/planning/rfc/engine-operator-kill-surface.md. The receiver's
KillSwitch is state-file backed; this proves the file is the
cross-process source of truth (engage/disengage/status file-aware), the
sticky re-entry rule holds across processes, engaged survives restarts,
and the CLI (scripts/ops/sentinel-kill) flips the same file — never Jev,
never the race (AST purity).
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import ast
import json
import subprocess
import tempfile
import unittest

from sentinel.audit import AuditLog
from sentinel.safety import KillSwitch, RearmRefused

_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "scripts", "ops", "sentinel-kill")


class FileBackedKillCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = os.path.join(self.tmp.name, "kill-switch.json")
        db = os.path.join(self.tmp.name, "audit.db")
        # KillSwitch takes the EventLog (append_event), not the AuditLog
        # wrapper — same wiring as the receiver (audit.log).
        self.log_a = AuditLog(db).log
        self.log_b = AuditLog(db).log
        self.addCleanup(self.log_a.close)
        self.addCleanup(self.log_b.close)

    def _pair(self):
        return (KillSwitch(log=self.log_a, state_path=self.state_path),
                KillSwitch(log=self.log_b, state_path=self.state_path))


class TestCrossProcessSemantics(FileBackedKillCase):
    def test_engage_visible_across_instances(self):
        a, b = self._pair()
        self.assertFalse(b.engaged)
        a.engage(actor_id="op-1")
        self.assertTrue(b.engaged)  # separate instance, same file

    def test_engage_idempotent_across_instances_no_duplicate_audit(self):
        a, b = self._pair()
        a.engage(actor_id="op-1")
        res = b.engage(actor_id="op-2")  # file already engaged
        self.assertFalse(res["transitioned"])
        engaged_events = len(self.log_a.events_by_type(
            "kill_switch_engaged"))
        self.assertEqual(engaged_events, 1)

    def test_disengage_by_other_instance_transitions(self):
        a, b = self._pair()
        a.engage(actor_id="op-1")
        # b's in-memory flag is False, but the file says engaged: the
        # file-aware disengage must transition, not no-op (fail-unsafe
        # direction would be reporting success while still engaged).
        res = b.disengage(actor_id="op-2", confirm=True)
        self.assertTrue(res["transitioned"])
        self.assertFalse(a.engaged)
        self.assertFalse(b.engaged)

    def test_disengage_without_confirm_refused(self):
        a, _b = self._pair()
        a.engage(actor_id="op-1")
        with self.assertRaises(RearmRefused):
            a.disengage(actor_id="op-1")  # confirm defaults to False
        self.assertTrue(a.engaged)  # still engaged

    def test_engaged_survives_restart(self):
        a, _b = self._pair()
        a.engage(actor_id="op-1", reason="incident-42")
        # "Restart": a brand-new instance on the same state path.
        c = KillSwitch(log=self.log_a, state_path=self.state_path)
        self.assertTrue(c.engaged)
        st = c.status()
        self.assertTrue(st["engaged"])

    def test_status_file_aware(self):
        a, b = self._pair()
        a.engage(actor_id="op-1")
        self.assertTrue(b.status()["engaged"])
        b.disengage(actor_id="op-2", confirm=True)
        self.assertFalse(a.status()["engaged"])

    def test_state_file_permissions(self):
        a, _b = self._pair()
        a.engage(actor_id="op-1")
        st = os.stat(self.state_path)
        self.assertEqual(st.st_mode & 0o777, 0o600)

    def test_no_state_path_behavior_unchanged(self):
        # Without a state path everything is in-memory, as before.
        a = KillSwitch()
        b = KillSwitch()
        a.engage(actor_id="op-1")
        self.assertTrue(a.engaged)
        self.assertFalse(b.engaged)


class TestOperatorCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = os.path.join(self.tmp.name, "state")
        os.makedirs(self.state_dir)
        self.env = dict(os.environ)
        self.env["SENTINEL_STATE_DIR"] = self.state_dir
        self.env["SENTINEL_DB"] = os.path.join(self.tmp.name, "audit.db")
        self.env["PYTHONPATH"] = _SRC + os.pathsep + self.env.get(
            "PYTHONPATH", "")

    def _run(self, *args):
        return subprocess.run([sys.executable, _CLI, *args],
                              capture_output=True, text=True, env=self.env,
                              timeout=30)

    def test_status_engage_disengage_roundtrip(self):
        r = self._run("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(json.loads(r.stdout)["engaged"])

        r = self._run("engage", "--reason", "test-incident")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = json.loads(r.stdout)
        self.assertTrue(body["engaged"])
        self.assertTrue(body["transitioned"])

        r = self._run("status")
        self.assertTrue(json.loads(r.stdout)["engaged"])

        # Re-arm without --confirm: refused, nothing changed.
        r = self._run("disengage")
        self.assertEqual(r.returncode, 2)
        r = self._run("status")
        self.assertTrue(json.loads(r.stdout)["engaged"])

        r = self._run("disengage", "--confirm")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = json.loads(r.stdout)
        self.assertFalse(body["engaged"])
        self.assertTrue(body["transitioned"])

    def test_cli_flip_visible_to_receiver_instance(self):
        # The CLI flips the same file the receiver's forwarder reads.
        self._run("engage")
        ks = KillSwitch(
            state_path=os.path.join(self.state_dir, "kill-switch.json"))
        self.assertTrue(ks.engaged)

    def test_cli_refuses_without_state_dir(self):
        env = dict(self.env)
        env["SENTINEL_STATE_DIR"] = os.path.join(self.tmp.name, "nope")
        r = subprocess.run([sys.executable, _CLI, "status"],
                           capture_output=True, text=True, env=env,
                           timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertIn("Refusing", r.stderr)


class TestCliControlPrinciple(unittest.TestCase):
    """The operator surface is a flag-flip + audit event: stdlib + the
    safety/audit/keystore modules only. It must never import Jev, the
    gate, or the race (the AST test that enforces the control principle).
    """

    def test_cli_imports_no_jev_gate_race(self):
        with open(_CLI) as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        for banned in ("client", "gate", "race", "jev", "typesafe"):
            self.assertNotIn(
                banned, {m.split(".")[-1] for m in imported},
                f"sentinel-kill must not import {banned} (control principle)")


if __name__ == "__main__":
    unittest.main()
