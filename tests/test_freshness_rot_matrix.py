"""The (fresh|stale)^3 rot-matrix CI fixture (design §A5, ADR-014).

This is the executable version of the C-1 finding: Tripwire's compound
scenario proved the triple lock's independence assumption false, and this
matrix proves on every commit that no silent rot can suppress. It is a
permanent Type 1 fixture — changing what row 1 asserts is changing the
product's safety case.

Each row builds a synthetic config bundle with controlled proof ages
(never wall-clock dependent — the validator's clock is pinned), runs V1
validation, and feeds the FreshnessReport through the suppress conjunction
from design §A4 (mirrored here as decide_like_gate; the gate lane wires
the real gate to the same contract — see docs/FRESHNESS_CONTRACT.md).

Mutation discipline: the fixture alert is one that WOULD suppress under
row 1 (passes every value check). setUp asserts the row-1 suppress first —
if the premise breaks, every row fails loudly instead of trivially paging.

Rows:
  1  fresh/fresh/fresh → suppress (the ONLY row that may suppress)
  2  stale/fresh/fresh → page (lock 1; dual-attestation interim offered)
  3  fresh/stale/fresh → page (lock 2; NO fallback)
  4  fresh/fresh/stale → page (lock 3; per-entry blast-radius containment)
  5  stale/stale/fresh → page (C-1 compound shape; BOTH locks named)
  6  stale/fresh/stale → page (control-plane alarm once per lock)
  7  fresh/stale/stale → page (incident-linkage void dominates TTL)
  8  stale/stale/stale → page (total rot; noisy paging, never silence)
"""

import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sentinel.freshness import (
    LOCK1_CALIBRATION,
    LOCK2_THRESHOLD,
    LOCK3_ALLOWLIST,
    FreshnessMonitor,
    FreshnessValidator,
    lock1_fallback_offered,
    suppress_precondition,
)
from tests.freshness_fixtures import (
    BASE_EPOCH,
    LABEL_PIPELINE,
    FixtureClock,
    build_bundle,
    fp,
    make_allowlist_entry,
    make_fit_proof,
    make_threshold_attestation,
    make_thresholds,
)


def decide_like_gate(report, *, fingerprint, dual_attested=False):
    """The design §A4 suppress conjunction for the fixture alert.

    The fixture alert passes every VALUE check (quantized P(p1) = 0.00,
    conf 0.95 ≥ bar, fingerprint in the allowlist, corroboration present,
    not firewall-flagged, not a storm aggregate) — only the freshness legs
    vary by row. Lock 1's stale leg can be satisfied through the
    dual-attestation interim path (synthesis §3.1); locks 2 and 3 have no
    interim path.

    Returns (action, reason, decision_context). The context mirrors what
    the gate lane puts on the audit event: disposition + proof pedigree.
    """
    p1_reported, conf, bar = 0.00, 0.95, 0.90
    in_allowlist = fingerprint in report.locks[LOCK3_ALLOWLIST].entries

    lock1_ok = (report.is_fresh(LOCK1_CALIBRATION)
                or (dual_attested and lock1_fallback_offered(report)))
    value_ok = (p1_reported == 0.00 and conf >= bar and in_allowlist)
    # corroboration present, firewall clean, no storm — the fixture premise.
    if (lock1_ok and value_ok
            and report.is_fresh(LOCK2_THRESHOLD)
            and report.entry_fresh(fingerprint)):
        action, reason = "suppress", "allowlist"
    else:
        _, stale = suppress_precondition(report, fingerprint=fingerprint)
        action = "page_now"
        reason = ("freshness:" + "|".join(stale)) if stale else "page_now:value"

    context = {
        "action": action,
        "reason": reason,
        "config_manifest_sha256": report.config_manifest_sha256,
        "proof_ids": {
            "lock1_calibration": report.locks[LOCK1_CALIBRATION].proof_id,
            "lock2_threshold": report.locks[LOCK2_THRESHOLD].proof_id,
            "lock3_allowlist": fingerprint,
        },
    }
    return action, reason, context


PRIMARY_FP = fp("cache-evictions")
SECOND_FP = fp("dns-noise")


def build_row(bundle_dir, *, l1="fresh", l2="fresh", l3="fresh"):
    """Build one matrix row. Stale causes: l1 ∈ {ttl,pin,pipeline},
    l2 ∈ {clock,hash,ratchet}, l3 ∈ {ttl,incident}. Returns
    (bundle_dir, drift_state)."""
    thresholds = make_thresholds()

    if l1 == "fresh":
        fit = make_fit_proof()
    elif l1 == "ttl":
        fit = make_fit_proof(trained_days_ago=200.0)
    elif l1 == "pin":
        fit = make_fit_proof(model_pin="jev-1.12.0")
    elif l1 == "pipeline":
        fit = make_fit_proof(label_pipeline="label-pipe-v2")
    else:
        raise AssertionError(l1)

    if l2 == "fresh":
        att = make_threshold_attestation(thresholds)
    elif l2 == "clock":
        att = make_threshold_attestation(thresholds, attested_days_ago=40.0)
    elif l2 in ("hash", "ratchet"):
        att = make_threshold_attestation(thresholds)
        # The H-2 fatigue ratchet: operator hand-edits the bar down, no
        # re-attestation. The manifest is re-written (bundle is well-formed)
        # but the attestation binding is broken.
        thresholds = make_thresholds(conf_bar=0.89)
    elif l2 == "floor":
        att = make_threshold_attestation(thresholds, threshold=0.82)
    else:
        raise AssertionError(l2)

    drift_state = None
    if l3 == "fresh":
        entries = [(PRIMARY_FP, "prod", make_allowlist_entry(PRIMARY_FP)),
                   (SECOND_FP, "prod", make_allowlist_entry(SECOND_FP))]
    elif l3 == "ttl":
        entries = [(PRIMARY_FP, "prod",
                    make_allowlist_entry(PRIMARY_FP, attested_days_ago=200.0)),
                   (SECOND_FP, "prod", make_allowlist_entry(SECOND_FP))]
    elif l3 == "incident":
        entries = [(PRIMARY_FP, "prod", make_allowlist_entry(PRIMARY_FP)),
                   (SECOND_FP, "prod", make_allowlist_entry(SECOND_FP))]
        drift_state = {"incident_linked": {PRIMARY_FP: "SEV-20231101"}}
    else:
        raise AssertionError(l3)

    build_bundle(bundle_dir, thresholds=thresholds, fit_proof=fit,
                 threshold_attestation=att, entries=entries)
    return drift_state


class RotMatrixBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FixtureClock()
        self.validator = FreshnessValidator(
            clock=self.clock, deployed_label_pipeline_version=LABEL_PIPELINE)
        # Mutation discipline: the row-1 premise — the fixture alert MUST
        # suppress when everything is fresh, or the matrix proves nothing.
        d = os.path.join(self.tmp.name, "premise")
        os.makedirs(d)
        build_row(d)
        report = self.validator.validate_bundle(d)
        action, _, _ = decide_like_gate(report, fingerprint=PRIMARY_FP)
        self.assertEqual(action, "suppress",
                         "MATRIX PREMISE BROKEN: the fixture alert does not "
                         "suppress under row 1 — every row would trivially page")

    def row(self, **kw):
        d = os.path.join(self.tmp.name, "row-%d" % self._row_n)
        self._row_n += 1
        os.makedirs(d)
        drift = build_row(d, **kw)
        return self.validator.validate_bundle(d, drift_state=drift)

    _row_n = 0


class TestRotMatrix(RotMatrixBase):
    def test_row1_all_fresh_suppresses_with_pedigree(self):
        report = self.row(l1="fresh", l2="fresh", l3="fresh")
        action, reason, ctx = decide_like_gate(report, fingerprint=PRIMARY_FP)
        self.assertEqual(action, "suppress")
        self.assertEqual(reason, "allowlist")
        # Pedigree binding: the decision carries all three proof ids +
        # the manifest hash, so a postmortem can replay exactly which
        # proofs were live at decision time.
        self.assertTrue(ctx["proof_ids"]["lock1_calibration"].startswith("fit-"))
        self.assertIn("threshold-attestation@",
                      ctx["proof_ids"]["lock2_threshold"])
        self.assertEqual(ctx["proof_ids"]["lock3_allowlist"], PRIMARY_FP)
        self.assertEqual(len(ctx["config_manifest_sha256"]), 64)

    def test_row2_stale_fit_pages(self):
        report = self.row(l1="ttl", l2="fresh", l3="fresh")
        action, reason, _ = decide_like_gate(report, fingerprint=PRIMARY_FP)
        self.assertEqual(action, "page_now")
        self.assertIn("lock1_stale", reason)
        self.assertIn("TTL expired", reason)
        # The dual-attestation interim path is OFFERED…
        self.assertTrue(lock1_fallback_offered(report))
        # …but without it, the page stands.
        action2, _, _ = decide_like_gate(report, fingerprint=PRIMARY_FP,
                                          dual_attested=False)
        self.assertEqual(action2, "page_now")
        # …and with it, the probability leg is satisfied through the
        # attestation fallback (synthesis §3.1 interim mechanics).
        action3, _, _ = decide_like_gate(report, fingerprint=PRIMARY_FP,
                                          dual_attested=True)
        self.assertEqual(action3, "suppress")

    def test_row3_stale_threshold_pages_no_fallback(self):
        report = self.row(l1="fresh", l2="clock", l3="fresh")
        action, reason, _ = decide_like_gate(report, fingerprint=PRIMARY_FP)
        self.assertEqual(action, "page_now")
        self.assertIn("lock2_stale", reason)
        self.assertIn("revalidation clock lapsed", reason)
        # Lock 2 has NO interim path — the bar is the human's judgment.
        # Even dual attestation on the allowlist entry cannot satisfy it.
        action2, _, _ = decide_like_gate(report, fingerprint=PRIMARY_FP,
                                          dual_attested=True)
        self.assertEqual(action2, "page_now")

    def test_row3_emits_freshness_transition_audit_event(self):
        # The V2 heartbeat that detects the lapse appends a
        # freshness_transition audit event. Model the audit sink as a list.
        audit_events = []
        d = os.path.join(self.tmp.name, "row3-v2")
        os.makedirs(d)
        build_row(d, l1="fresh", l2="fresh", l3="fresh")
        mon = FreshnessMonitor(self.validator, d,
                               on_transition=audit_events.append)
        mon.boot()
        # 40 days pass: the 30-day re-validation clock lapses between reloads.
        self.clock.advance(40 * 86400.0)
        records = mon.heartbeat()
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.lock, LOCK2_THRESHOLD)
        self.assertIn("revalidation clock lapsed", rec.reason)
        self.assertTrue(rec.proof_id)
        self.assertTrue(rec.detected_at)
        # The control plane appends exactly one audit event per transition.
        self.assertEqual(len(audit_events), 1)

    def test_row4_stale_entry_pages_per_entry(self):
        report = self.row(l1="fresh", l2="fresh", l3="ttl")
        action, reason, _ = decide_like_gate(report, fingerprint=PRIMARY_FP)
        self.assertEqual(action, "page_now")
        self.assertIn("lock3_stale", reason)
        self.assertIn(PRIMARY_FP, reason)
        # Blast-radius containment: the SECOND entry's attestation is fresh,
        # and in the same run it still suppresses — one stale entry does not
        # fail the whole allowlist.
        action2, _, _ = decide_like_gate(report, fingerprint=SECOND_FP)
        self.assertEqual(action2, "suppress")

    def test_row5_compound_rot_both_locks_named(self):
        # The C-1 compound scenario's shape (M-1 quantization rot + H-2
        # governance decay together). The operator must see the FULL rot,
        # not the first failure — evaluation records all stale locks.
        report = self.row(l1="ttl", l2="clock", l3="fresh")
        action, reason, _ = decide_like_gate(report, fingerprint=PRIMARY_FP)
        self.assertEqual(action, "page_now")
        self.assertIn("lock1_stale", reason)
        self.assertIn("lock2_stale", reason)

    def test_row6_alarm_once_per_lock_not_per_alert(self):
        d = os.path.join(self.tmp.name, "row6-v2")
        os.makedirs(d)
        build_bundle(
            d,
            fit_proof=make_fit_proof(trained_days_ago=89.0),  # 1 day from TTL expiry
            entries=[(PRIMARY_FP, "prod",
                      make_allowlist_entry(PRIMARY_FP, attested_days_ago=179.0))],
        )
        alarms = []
        mon = FreshnessMonitor(self.validator, d, on_transition=alarms.append)
        mon.boot()
        self.clock.advance(2 * 86400.0)  # both locks cross into stale
        records = mon.heartbeat()
        fired_locks = {r.lock for r in records}
        self.assertIn(LOCK1_CALIBRATION, fired_locks)
        self.assertIn(LOCK3_ALLOWLIST, fired_locks)
        n_alarms = len(alarms)
        self.assertGreaterEqual(n_alarms, 2)
        # Alert-fatigue discipline on the guard's own alarm: 100 further
        # per-alert reads fire nothing more.
        for _ in range(100):
            mon.current_report().is_fresh(LOCK1_CALIBRATION)
        mon.heartbeat()
        self.assertEqual(len(alarms), n_alarms)

    def test_row7_incident_linkage_voids_within_ttl(self):
        report = self.row(l1="fresh", l2="clock", l3="incident")
        action, reason, _ = decide_like_gate(report, fingerprint=PRIMARY_FP)
        self.assertEqual(action, "page_now")
        self.assertIn("lock2_stale", reason)
        self.assertIn("lock3_stale", reason)
        self.assertIn("VOID", reason)
        self.assertIn("incident linkage", reason)
        # The incident-linked entry is void EVEN THOUGH attested 20 days
        # ago against a 180-day TTL — TTL is necessary, not sufficient.

    def test_row8_total_rot_noisy_paging_never_silence(self):
        report = self.row(l1="ttl", l2="clock", l3="ttl")
        action, reason, _ = decide_like_gate(report, fingerprint=PRIMARY_FP)
        self.assertEqual(action, "page_now")
        for lock in ("lock1_stale", "lock2_stale", "lock3_stale"):
            self.assertIn(lock, reason)
        # The system degrades to "everything pages", never "refuses to
        # decide": V1 boot on the all-stale bundle still serves.
        d = os.path.join(self.tmp.name, "row8-boot")
        os.makedirs(d)
        build_row(d, l1="ttl", l2="clock", l3="ttl")
        mon = FreshnessMonitor(self.validator, d)
        booted = mon.boot()  # must not raise
        self.assertEqual(len(booted.stale_locks()), 3)
        self.assertFalse(mon.current_report().is_fresh(LOCK1_CALIBRATION))


class TestStaleCauses(RotMatrixBase):
    """Each stale sub-case is a separate parameterized case under its row —
    the matrix is 8 rows, the suite is ~20 cases."""

    def test_l1_pin_mismatch_pages(self):
        report = self.row(l1="pin", l2="fresh", l3="fresh")
        action, reason, _ = decide_like_gate(report, fingerprint=PRIMARY_FP)
        self.assertEqual(action, "page_now")
        self.assertIn("model_pin mismatch", reason)

    def test_l1_label_pipeline_drift_pages(self):
        report = self.row(l1="pipeline", l2="fresh", l3="fresh")
        action, reason, _ = decide_like_gate(report, fingerprint=PRIMARY_FP)
        self.assertEqual(action, "page_now")
        self.assertIn("label pipeline drift", reason)

    def test_l2_ratcheted_bar_pages(self):
        # H-2 fatigue ratchet, mechanically: the operator edits the bar
        # 0.90 → 0.89 without re-attestation. The binding breaks ⇒ lock 2
        # fails ⇒ page. The cheapest action under fatigue produces the most
        # paging — the gradient is inverted by physics, not policy.
        report = self.row(l1="fresh", l2="ratchet", l3="fresh")
        action, reason, _ = decide_like_gate(report, fingerprint=PRIMARY_FP)
        self.assertEqual(action, "page_now")
        self.assertIn("lock2_stale", reason)

    def test_l2_subfloor_attestation_is_invalid(self):
        report = self.row(l1="fresh", l2="floor", l3="fresh")
        action, reason, _ = decide_like_gate(report, fingerprint=PRIMARY_FP)
        self.assertEqual(action, "page_now")
        self.assertIn("conf floor", reason)


class TestV1V2Wiring(RotMatrixBase):
    def test_v1_boot_row8_produces_all_stale_report(self):
        d = os.path.join(self.tmp.name, "wiring-v1")
        os.makedirs(d)
        build_row(d, l1="ttl", l2="clock", l3="ttl")
        report = self.validator.validate_bundle(d)
        self.assertEqual(report.stale_locks(),
                         [LOCK1_CALIBRATION, LOCK2_THRESHOLD, LOCK3_ALLOWLIST])
        for lid in (LOCK1_CALIBRATION, LOCK2_THRESHOLD, LOCK3_ALLOWLIST):
            self.assertTrue(report.locks[lid].reason)
            self.assertTrue(report.locks[lid].proof_id)

    def test_v2_heartbeat_flips_verdict_on_ttl_crossing(self):
        d = os.path.join(self.tmp.name, "wiring-v2")
        os.makedirs(d)
        build_bundle(d, entries=[(PRIMARY_FP, "prod",
                                  make_allowlist_entry(PRIMARY_FP,
                                                       attested_days_ago=179.0))])
        mon = FreshnessMonitor(self.validator, d)
        mon.boot()
        self.assertTrue(mon.current_report().entry_fresh(PRIMARY_FP))
        self.clock.advance(2 * 86400.0)
        records = mon.heartbeat()
        self.assertFalse(mon.current_report().entry_fresh(PRIMARY_FP))
        self.assertEqual(len(records), 1)
        self.assertIn("TTL expired", records[0].reason)


if __name__ == "__main__":
    unittest.main()
