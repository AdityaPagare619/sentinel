"""AC-6 — Shadow audit: the shadow never touches the decision record.

Contract C3 (Track 3, merged). Wired against the REAL implementation:
a Gate in shadow=True mode driven with a deterministic fake Jev, with the
audit rows read back from a real (in-memory) AuditLog — plus the
Disposition write-once machinery and a codebase-wide scan for the
forbidden reason="shadow" assignment.

The guarded violation: shadow_mode rewriting kill-switch (or any causal)
attribution to "shadow". Zero tolerance — one rewritten reason fails.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
SRC = os.path.join(REPO, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
TESTS = os.path.join(REPO, "tests")
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

from common import FastJev, canned, make_alert, make_suppress_rig  # noqa: E402
from sentinel.gate import Gate  # noqa: E402
from sentinel.models import (  # noqa: E402
    Thresholds, Disposition, ReasonRewriteError)
from sentinel.audit import AuditLog, body_of  # noqa: E402
from sentinel.state import build_state  # noqa: E402


def _track3_mode_present():
    from sentinel.models import Disposition as _D
    import dataclasses
    return "mode" in {f.name for f in dataclasses.fields(_D)}


T3_MISSING = ("BLOCKED: Track 3 not merged — no `mode` field on the "
              "disposition record (shadow vs live) on this branch")

# The causal reason vocabulary (models.py contract §3.2 — "shadow" is
# deliberately NOT in it).
CAUSAL_REASONS = {"threshold", "allowlist", "uncertain", "dedup",
                  "change_window", "storm", "storm_digest", "kill_switch",
                  "flap_debounce"}


def _is_causal(reason):
    return (isinstance(reason, str)
            and (reason in CAUSAL_REASONS
                 or reason.startswith("failopen_step")
                 or reason.startswith("error:")))


class _ShadowRig:
    """A real Gate in shadow mode + its real audit log."""

    def __init__(self, model="fakejev-t6-1.0", q3_choice="page_now",
                 p1=0.9):
        self.audit = AuditLog(":memory:")
        self.gate = Gate(
            FastJev(canned(p1=p1, conf=0.95, q3_choice=q3_choice,
                           model=model)),
            Thresholds(), [], self.audit, shadow=True)
        self.fps = []

    def drive(self, tag):
        alert = make_alert(alert_id=f"t6-{tag}", service=f"svc-{tag}",
                           check="http_5xx")
        state = build_state(alert, {}, {})
        disp, rec = self.gate.evaluate(alert, state, {}, {})
        self.fps.append(alert.fingerprint)
        return disp, rec, alert

    def rows(self):
        out = []
        for fp in self.fps:
            out.extend(self.audit.decisions_for_fingerprint(fp, limit=50))
        return out

    def close(self):
        try:
            self.gate.close()
        except Exception:
            pass
        try:
            self.audit.close()
        except Exception:
            pass


class TestAC6ShadowAudit(unittest.TestCase):
    def _require_t3(self):
        if not _track3_mode_present():
            self.skipTest(T3_MISSING)

    def setUp(self):
        self._require_t3()

    # ---------------------------------------------------------------- 6a

    def test_6a_mode_field_correct(self):
        """shadow_decision rows carry mode=shadow; live decision_made rows
        carry mode=live — on the REAL gate and its REAL audit rows."""
        rig = _ShadowRig()
        self.addCleanup(rig.close)
        disp, rec, alert = rig.drive("a")
        rows = rig.audit.decisions_for_fingerprint(alert.fingerprint)
        self.assertTrue(rows, "shadow gate wrote no audit row")
        body = body_of(rows[0])
        self.assertEqual(body["mode"], "shadow",
                         "shadow audit row does not carry mode=shadow")
        self.assertEqual(rec.disposition.mode, "shadow")
        # The returned shell is mode=shadow too (the action is what is
        # forced to passthrough, never the reason).
        self.assertEqual(disp.mode, "shadow")

        # Live side: the common suppress rig reaches a real suppress on a
        # real gate — its rows must be mode=live.
        live = make_suppress_rig(1)
        self.addCleanup(live.close)
        la = make_alert(alert_id="t6-live-1", service="svc-1",
                        check="http_5xx")
        live.add_fingerprint(la.fingerprint)
        ldisp, _, _, _ = live.drive(la)
        lrows = live.audit.decisions_for_fingerprint(la.fingerprint)
        self.assertTrue(lrows, "live gate wrote no audit row")
        self.assertEqual(body_of(lrows[0])["mode"], "live",
                         "live audit row does not carry mode=live")
        print(f"\n[AC-6a] shadow row mode=shadow (would-be "
              f"{rec.disposition.action}/{rec.disposition.reason}); live row "
              f"mode=live ({ldisp.action}/{ldisp.reason})")

    # ---------------------------------------------------------------- 6b

    def test_6b_zero_rewritten_reasons(self):
        """Sample min(50, all) shadow rows: reason is causal, never
        'shadow'. Plus the write-once machinery refuses a rewrite, and a
        codebase scan finds no reason='shadow' assignment anywhere."""
        rig = _ShadowRig()
        self.addCleanup(rig.close)
        for i in range(5):
            rig.drive(f"b{i}")
        rows = rig.rows()[:50]
        self.assertTrue(rows, "no shadow rows to audit")
        rewritten = [r for r in rows
                     if body_of(r)["v01_compat"]["reason"] == "shadow"]
        self.assertEqual(rewritten, [],
                         f"{len(rewritten)} shadow rows had reason "
                         "rewritten to 'shadow'")
        non_causal = [
            body_of(r)["v01_compat"]["reason"] for r in rows
            if not _is_causal(body_of(r)["v01_compat"]["reason"])]
        self.assertEqual(non_causal, [],
                         f"shadow rows with non-causal reasons: {non_causal}")

        # The write-once machinery itself: any post-hoc rewrite raises.
        disp = Disposition(action="page_now", reason="threshold", team=None,
                           confidence=None, latency_ms=1.0)
        with self.assertRaises(ReasonRewriteError):
            disp.reason = "shadow"

        # Codebase scan: no source line assigns reason='shadow'.
        offenders = []
        for root, _, files in os.walk(os.path.join(REPO, "src")):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(root, fn)
                with open(p) as fh:
                    for ln, line in enumerate(fh, 1):
                        stripped = line.strip()
                        if stripped.startswith("#") or stripped.startswith(
                                '"""') or stripped.startswith("'''"):
                            continue
                        if ('reason="shadow"' in line
                                or "reason='shadow'" in line):
                            offenders.append(f"{p}:{ln}: {stripped}")
        self.assertEqual(offenders, [],
                         "reason='shadow' assignments found:\n"
                         + "\n".join(offenders))
        print(f"\n[AC-6b] {len(rows)} shadow rows audited: 0 rewritten "
              f"reasons; ReasonRewriteError confirmed; codebase scan clean")

    # ---------------------------------------------------------------- 6c

    def test_6c_shadow_is_powerless(self):
        """The shadow's would-be page_now is recorded but never executed:
        the returned disposition is forced to passthrough, the audit row
        carries no outbox_id (never enqueued), and no forward_confirmed
        event exists for the shadow episode."""
        rig = _ShadowRig(q3_choice="page_now", p1=0.9)
        self.addCleanup(rig.close)
        disp, rec, alert = rig.drive("c")

        # The would-be verdict is kept for the record...
        self.assertEqual(rec.disposition.action, "page_now",
                         "shadow would-be verdict was not page_now — the "
                         "powerlessness test needs a would-be page")
        self.assertEqual(rec.disposition.reason, "threshold")
        self.assertEqual(rec.disposition.mode, "shadow")
        # ...but the caller receives the forced shell: never executed.
        self.assertEqual(disp.action, "passthrough",
                         "shadow gate returned a non-passthrough action — "
                         "the shadow EXECUTED")

        rows = rig.audit.decisions_for_fingerprint(alert.fingerprint)
        body = body_of(rows[0])
        self.assertIsNone(body["outbox_id"],
                          "shadow audit row carries an outbox_id — the "
                          "shadow page was ENQUEUED for forwarding")
        fwd_confirmed = rig.audit.log.events_by_type("forward_confirmed")
        mine = [e for e in fwd_confirmed
                if e.get("fingerprint") == alert.fingerprint]
        self.assertEqual(mine, [],
                         "forward_confirmed events exist for a shadow "
                         "episode — the shadow forwarded")
        print(f"\n[AC-6c] shadow would-be page_now recorded (mode=shadow), "
              f"returned passthrough, outbox_id None, 0 forward_confirmed")

    # ---------------------------------------------------------------- 6d

    def test_6d_kill_attribution_intact(self):
        """A would-be disposition carrying reason='kill_switch' passes the
        shadow path with its attribution intact — mode becomes shadow, the
        reason is never rewritten."""
        disp = Disposition(action="page_now", reason="kill_switch",
                           team=None, confidence=None, latency_ms=1.0)
        shadow_copy = disp.as_shadow()
        shell = disp.as_shadow_shell()
        for d, name in ((shadow_copy, "as_shadow"),
                        (shell, "as_shadow_shell")):
            self.assertEqual(d.mode, "shadow", f"{name}: mode not shadow")
            self.assertEqual(d.reason, "kill_switch",
                             f"{name}: kill attribution was rewritten to "
                             f"{d.reason!r}")
        self.assertEqual(shell.action, "passthrough",
                         "shadow shell must force passthrough")
        self.assertEqual(shadow_copy.action, "page_now",
                         "as_shadow must keep the would-be action")
        # End-to-end: the real shadow gate's audit rows keep causal
        # reasons even when the causal reason vocabulary is exercised.
        rig = _ShadowRig(q3_choice="page_now", p1=0.9)
        self.addCleanup(rig.close)
        _, rec, alert = rig.drive("d")
        rows = rig.audit.decisions_for_fingerprint(alert.fingerprint)
        reason = body_of(rows[0])["v01_compat"]["reason"]
        self.assertEqual(reason, rec.disposition.reason)
        self.assertNotEqual(reason, "shadow")
        print(f"\n[AC-6d] kill_switch attribution survives the shadow path "
              f"(reason={reason!r}, mode=shadow)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
