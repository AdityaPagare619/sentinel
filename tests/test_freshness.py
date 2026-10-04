"""Unit tests for sentinel.freshness (C-1 freshness proofs).

Covers: canonical-JSON pinning, RFC3339 handling, proof shapes, the three
freshness predicates, manifest verification, V1 validation, the V2 monitor,
suppress_precondition, and the two-point validation discipline.
The (fresh|stale)^3 rot matrix lives in test_freshness_rot_matrix.py.
"""

import json
import os
import sys
import tempfile
import unittest

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sentinel.freshness import (
    ALLOWLIST_TTL_DAYS_DEFAULT,
    FIT_VALIDITY_WINDOW_DAYS_DEFAULT,
    LOCK1_CALIBRATION,
    LOCK2_THRESHOLD,
    LOCK3_ALLOWLIST,
    AllowlistAttestation,
    CalibrationFitProof,
    FreshnessMonitor,
    FreshnessReport,
    FreshnessValidator,
    ManifestError,
    ProofFormatError,
    ThresholdAttestation,
    allowlist_entry_freshness,
    build_manifest,
    canonical_sha256,
    config_hash_for,
    fit_freshness,
    lock1_fallback_offered,
    parse_rfc3339,
    rfc3339_from_epoch,
    suppress_precondition,
    threshold_freshness,
    verify_manifest,
    write_manifest,
)
from tests.freshness_fixtures import (
    BASE_EPOCH,
    LABEL_PIPELINE,
    PINNED_MODEL,
    FixtureClock,
    build_bundle,
    fp,
    make_allowlist_entry,
    make_fit_proof,
    make_threshold_attestation,
    make_thresholds,
    ts_days_ago,
)


class TestCanonicalPinning(unittest.TestCase):
    def test_whitespace_reformat_does_not_change_hash(self):
        obj = make_thresholds()
        h1 = config_hash_for(obj)
        # Same object, wildly different serialization.
        blob = json.dumps(obj, indent=8, sort_keys=False)
        h2 = config_hash_for(json.loads(blob))
        self.assertEqual(h1, h2)

    def test_semantic_change_changes_hash(self):
        h1 = config_hash_for(make_thresholds())
        changed = make_thresholds()
        changed["suppress_conf_min"] = 0.89
        self.assertNotEqual(h1, config_hash_for(changed))

    def test_key_order_irrelevant(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        self.assertEqual(canonical_sha256(a), canonical_sha256(b))


class TestRfc3339(unittest.TestCase):
    def test_zulu(self):
        self.assertAlmostEqual(parse_rfc3339("2023-11-14T22:13:20Z"), BASE_EPOCH)

    def test_explicit_offset(self):
        self.assertAlmostEqual(parse_rfc3339("2023-11-14T22:13:20+00:00"), BASE_EPOCH)

    def test_naive_rejected(self):
        with self.assertRaises(ValueError):
            parse_rfc3339("2023-11-14T22:13:20")

    def test_garbage_rejected(self):
        for bad in ("", "yesterday", "2023-13-99T99:99:99Z", None, 123):
            with self.assertRaises(ValueError):
                parse_rfc3339(bad)

    def test_roundtrip(self):
        self.assertAlmostEqual(parse_rfc3339(rfc3339_from_epoch(BASE_EPOCH)), BASE_EPOCH)


class TestProofShapes(unittest.TestCase):
    def test_fit_roundtrip(self):
        p = CalibrationFitProof.from_dict(make_fit_proof())
        d = p.to_dict()
        self.assertEqual(CalibrationFitProof.from_dict(d).to_dict(), d)
        self.assertEqual(p.validity_window_days, FIT_VALIDITY_WINDOW_DAYS_DEFAULT)

    def test_threshold_roundtrip(self):
        t = make_thresholds()
        a = ThresholdAttestation.from_dict(make_threshold_attestation(t))
        self.assertEqual(ThresholdAttestation.from_dict(a.to_dict()).to_dict(),
                         a.to_dict())

    def test_allowlist_roundtrip(self):
        e = AllowlistAttestation.from_dict(make_allowlist_entry(fp("x")))
        self.assertEqual(AllowlistAttestation.from_dict(e.to_dict()).to_dict(),
                         e.to_dict())
        self.assertEqual(e.ttl_days, ALLOWLIST_TTL_DAYS_DEFAULT)

    def test_missing_field_is_format_error(self):
        bad = make_fit_proof()
        del bad["fit_id"]
        with self.assertRaises(ProofFormatError):
            CalibrationFitProof.from_dict(bad)

    def test_single_attester_rejected(self):
        t = make_thresholds()
        bad = make_threshold_attestation(t, attested_by=("op-alice",))
        with self.assertRaises(ProofFormatError):
            ThresholdAttestation.from_dict(bad)


class TestFitFreshness(unittest.TestCase):
    def _proof(self, **kw):
        return CalibrationFitProof.from_dict(make_fit_proof(**kw))

    def test_fresh(self):
        lf = fit_freshness(self._proof(), BASE_EPOCH, PINNED_MODEL, LABEL_PIPELINE)
        self.assertTrue(lf.fresh)

    def test_expired_ttl(self):
        lf = fit_freshness(self._proof(trained_days_ago=120.0), BASE_EPOCH,
                           PINNED_MODEL, LABEL_PIPELINE)
        self.assertFalse(lf.fresh)
        self.assertIn("TTL expired", lf.reason)
        self.assertIn("lock1_stale", lf.reason)
        self.assertTrue(lf.fallback_available)  # dual-attestation interim offered

    def test_model_pin_mismatch(self):
        lf = fit_freshness(self._proof(), BASE_EPOCH, "jev-1.14.0", LABEL_PIPELINE)
        self.assertFalse(lf.fresh)
        self.assertIn("model_pin mismatch", lf.reason)

    def test_label_pipeline_drift(self):
        lf = fit_freshness(self._proof(), BASE_EPOCH, PINNED_MODEL, "label-pipe-v4")
        self.assertFalse(lf.fresh)
        self.assertIn("label pipeline drift", lf.reason)

    def test_human_issued_by_rejected(self):
        lf = fit_freshness(self._proof(issued_by="op-alice"), BASE_EPOCH,
                           PINNED_MODEL, LABEL_PIPELINE)
        self.assertFalse(lf.fresh)
        self.assertIn("issued_by", lf.reason)

    def test_bad_timestamp_is_stale_not_raise(self):
        p = self._proof()
        p.fit_trained_at = "not-a-time"
        lf = fit_freshness(p, BASE_EPOCH, PINNED_MODEL, LABEL_PIPELINE)
        self.assertFalse(lf.fresh)


class TestThresholdFreshness(unittest.TestCase):
    def _att(self, thresholds, **kw):
        return ThresholdAttestation.from_dict(
            make_threshold_attestation(thresholds, **kw))

    def test_fresh(self):
        t = make_thresholds()
        lf = threshold_freshness(self._att(t), BASE_EPOCH, t, t["suppress_conf_min"])
        self.assertTrue(lf.fresh)
        self.assertFalse(lf.fallback_available)  # lock 2: NO fallback, ever

    def test_revalidation_clock_lapsed(self):
        t = make_thresholds()
        lf = threshold_freshness(self._att(t, attested_days_ago=40.0), BASE_EPOCH,
                                 t, t["suppress_conf_min"])
        self.assertFalse(lf.fresh)
        self.assertIn("revalidation clock lapsed", lf.reason)

    def test_bar_drift_without_reattestation(self):
        live = make_thresholds()
        attested = make_thresholds()  # attestation covers the OLD bar value
        att = self._att(attested)
        live["suppress_conf_min"] = 0.89  # fatigued operator edited the file
        lf = threshold_freshness(att, BASE_EPOCH, live, 0.89)
        self.assertFalse(lf.fresh)
        # Two independent staleness causes are both detectable; the bar drift
        # is caught first. Either way: stale ⇒ page.
        self.assertIn("lock2_stale", lf.reason)

    def test_config_hash_mismatch(self):
        live = make_thresholds()
        att = self._att(live)
        tampered = dict(live)
        tampered["page_p1p2_min"] = 0.99  # any semantic edit breaks the binding
        lf = threshold_freshness(att, BASE_EPOCH, tampered,
                                 tampered["suppress_conf_min"])
        self.assertFalse(lf.fresh)
        self.assertIn("config_hash mismatch", lf.reason)

    def test_whitespace_reformat_is_not_staleness(self):
        # Reviewer follow-up: canonical-JSON pinning means pretty-printing
        # thresholds.json must NOT page.
        live = make_thresholds()
        att = self._att(live)
        reformatted = json.loads(json.dumps(live, indent=8))
        lf = threshold_freshness(att, BASE_EPOCH, reformatted,
                                 reformatted["suppress_conf_min"])
        self.assertTrue(lf.fresh, lf.reason)

    def test_subfloor_attestation_is_invalid(self):
        live = make_thresholds(conf_bar=0.90)
        att = self._att(live, threshold=0.82)
        lf = threshold_freshness(att, BASE_EPOCH, live, 0.90)
        self.assertFalse(lf.fresh)
        self.assertIn("below", lf.reason)
        self.assertIn("conf floor", lf.reason)

    def test_single_operator_attestation_rejected(self):
        t = make_thresholds()
        d = make_threshold_attestation(t, attested_by=("op-alice", "op-alice"))
        att = ThresholdAttestation.from_dict(d)
        lf = threshold_freshness(att, BASE_EPOCH, t, t["suppress_conf_min"])
        self.assertFalse(lf.fresh)
        self.assertIn("two distinct", lf.reason)


class TestAllowlistEntryFreshness(unittest.TestCase):
    def _entry(self, fingerprint, **kw):
        return AllowlistAttestation.from_dict(make_allowlist_entry(fingerprint, **kw))

    def test_fresh(self):
        lf = allowlist_entry_freshness(self._entry(fp("a")), BASE_EPOCH)
        self.assertTrue(lf.fresh)

    def test_ttl_expired(self):
        lf = allowlist_entry_freshness(self._entry(fp("a"), attested_days_ago=200.0),
                                       BASE_EPOCH)
        self.assertFalse(lf.fresh)
        self.assertIn("TTL expired", lf.reason)

    def test_incident_linkage_voids_within_ttl(self):
        # TTL is necessary, not sufficient — the drift check dominates.
        e = self._entry(fp("a"), attested_days_ago=5.0)
        drift = {"incident_linked": {fp("a"): "SEV-20231101"}}
        lf = allowlist_entry_freshness(e, BASE_EPOCH, drift_state=drift)
        self.assertFalse(lf.fresh)
        self.assertIn("incident linkage", lf.reason)
        self.assertIn("VOID", lf.reason)

    def test_service_rewrite_fails_drift_check(self):
        e = self._entry(fp("a"), attested_days_ago=5.0)
        drift = {"rewritten": {fp("a"): True}}
        lf = allowlist_entry_freshness(e, BASE_EPOCH, drift_state=drift)
        self.assertFalse(lf.fresh)
        self.assertIn("major-version rewrite", lf.reason)

    def test_security_category_ban(self):
        # ADR-017/D4: the ban is DERIVED at admission. A stored
        # security_category_ban=True that disagrees with the derived
        # taxonomy (check=cache_evictions is unbanned) is itself a
        # rejection — fail closed on disputed ban state.
        with self.assertRaises(ProofFormatError):
            self._entry(fp("a"), security_category_ban=True)


class _BundleCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bundle = os.path.join(self.tmp.name, "bundle")

    def validator(self, **kw):
        kw.setdefault("deployed_label_pipeline_version", LABEL_PIPELINE)
        self.clock = FixtureClock()
        return FreshnessValidator(clock=self.clock, **kw)


class TestManifest(_BundleCase):
    def test_build_verify_roundtrip(self):
        build_bundle(self.bundle)
        sha = verify_manifest(self.bundle)
        self.assertEqual(len(sha), 64)

    def test_tampered_file_fails(self):
        build_bundle(self.bundle)
        with open(os.path.join(self.bundle, "thresholds.json"), "a",
                   encoding="utf-8") as fh:
            fh.write(" ")
        with self.assertRaises(ManifestError):
            verify_manifest(self.bundle)

    def test_missing_file_fails(self):
        build_bundle(self.bundle)
        os.remove(os.path.join(self.bundle, "pinning.json"))
        with self.assertRaises(ManifestError):
            verify_manifest(self.bundle)

    def test_missing_manifest_fails(self):
        build_bundle(self.bundle, skip_manifest=True)
        with self.assertRaises(ManifestError):
            verify_manifest(self.bundle)

    def test_corrupt_manifest_sha_fails(self):
        build_bundle(self.bundle)
        mp = os.path.join(self.bundle, "manifest.json")
        with open(mp, encoding="utf-8") as fh:
            m = json.load(fh)
        m["manifest_sha256"] = "0" * 64
        with open(mp, "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        with self.assertRaises(ManifestError):
            verify_manifest(self.bundle)


class TestValidatorV1(_BundleCase):
    def test_all_fresh_bundle(self):
        build_bundle(self.bundle)
        report = self.validator().validate_bundle(self.bundle)
        self.assertTrue(report.is_fresh(LOCK1_CALIBRATION))
        self.assertTrue(report.is_fresh(LOCK2_THRESHOLD))
        self.assertTrue(report.is_fresh(LOCK3_ALLOWLIST))
        self.assertEqual(report.stale_locks(), [])
        self.assertEqual(len(report.config_manifest_sha256), 64)

    def test_stale_fit(self):
        build_bundle(self.bundle,
                     fit_proof=make_fit_proof(trained_days_ago=200.0))
        report = self.validator().validate_bundle(self.bundle)
        self.assertFalse(report.is_fresh(LOCK1_CALIBRATION))
        self.assertIn(LOCK1_CALIBRATION, report.stale_locks())
        self.assertTrue(report.is_fresh(LOCK2_THRESHOLD))

    def test_missing_proof_is_stale(self):
        build_bundle(self.bundle)
        os.remove(os.path.join(self.bundle, "proofs", "calibration_proof.json"))
        # Manifest no longer matches → ManifestError (invalid bundle ⇒
        # caller's last-good path, never silent).
        with self.assertRaises(ManifestError):
            self.validator().validate_bundle(self.bundle)

    def test_malformed_proof_is_stale_not_raise(self):
        build_bundle(self.bundle)
        bad = os.path.join(self.bundle, "proofs", "threshold_attestation.json")
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump({"threshold": "not-a-number"}, fh)
        write_manifest(self.bundle)  # bundle is well-formed; the proof is bad
        report = self.validator().validate_bundle(self.bundle)
        self.assertFalse(report.is_fresh(LOCK2_THRESHOLD))
        self.assertIn("malformed", report.locks[LOCK2_THRESHOLD].reason)

    def test_unattested_entry_is_stale(self):
        orphan = fp("orphan")
        build_bundle(self.bundle, entries=[
            (fp("cache-evictions"), "prod",
             make_allowlist_entry(fp("cache-evictions"))),
            # orphan listed in allowlist.json but with NO attestation dict —
            # build it by hand below.
        ])
        # Add the orphan to allowlist.json without an attestation.
        ap = os.path.join(self.bundle, "allowlist.json")
        with open(ap, encoding="utf-8") as fh:
            al = json.load(fh)
        al["envs"]["prod"]["entries"].append(orphan)
        with open(ap, "w", encoding="utf-8") as fh:
            json.dump(al, fh)
        write_manifest(self.bundle)
        report = self.validator().validate_bundle(self.bundle)
        self.assertFalse(report.entry_fresh(orphan))
        self.assertTrue(report.entry_fresh(fp("cache-evictions")))
        # Per-entry staleness: the lock-3 summary is stale but the fresh
        # entry still suppresses (blast-radius containment).
        self.assertIn(orphan, report.locks[LOCK3_ALLOWLIST].stale_entries)

    def test_env_namespace_mismatch(self):
        entry_fp = fp("x", env="staging")
        build_bundle(self.bundle, entries=[
            (entry_fp, "prod", make_allowlist_entry(entry_fp, env="staging")),
        ])
        report = self.validator().validate_bundle(self.bundle)
        self.assertFalse(report.entry_fresh(entry_fp))
        self.assertIn("namespace mismatch",
                      report.locks[LOCK3_ALLOWLIST].entries[entry_fp].reason)


class TestSuppressPrecondition(_BundleCase):
    def _report(self, **kw):
        build_bundle(self.bundle, **kw)
        return self.validator().validate_bundle(self.bundle)

    def test_all_fresh_allows(self):
        allowed, reasons = suppress_precondition(
            self._report(), fingerprint=fp("cache-evictions"))
        self.assertTrue(allowed)
        self.assertEqual(reasons, [])

    def test_stale_lock1_blocks_and_names_cause(self):
        report = self._report(fit_proof=make_fit_proof(trained_days_ago=200.0))
        allowed, reasons = suppress_precondition(
            report, fingerprint=fp("cache-evictions"))
        self.assertFalse(allowed)
        self.assertEqual(len(reasons), 1)
        self.assertIn("lock1_stale", reasons[0])
        self.assertIn("TTL expired", reasons[0])
        self.assertTrue(lock1_fallback_offered(report))

    def test_multiple_stale_locks_all_named(self):
        # Short-circuit evaluation must still record ALL stale locks.
        t = make_thresholds()
        report = self._report(
            fit_proof=make_fit_proof(trained_days_ago=200.0),
            threshold_attestation=make_threshold_attestation(t, attested_days_ago=40.0))
        allowed, reasons = suppress_precondition(
            report, fingerprint=fp("cache-evictions"))
        self.assertFalse(allowed)
        self.assertEqual(len(reasons), 2)
        self.assertTrue(any("lock1_stale" in r for r in reasons))
        self.assertTrue(any("lock2_stale" in r for r in reasons))

    def test_lock3_per_entry(self):
        stale_fp = fp("old-noise")
        report = self._report(entries=[
            (fp("cache-evictions"), "prod",
             make_allowlist_entry(fp("cache-evictions"))),
            (stale_fp, "prod",
             make_allowlist_entry(stale_fp, attested_days_ago=200.0)),
        ])
        allowed, reasons = suppress_precondition(report, fingerprint=stale_fp)
        self.assertFalse(allowed)
        self.assertTrue(any(stale_fp in r for r in reasons))
        # The fresh sibling still suppresses.
        allowed2, _ = suppress_precondition(
            report, fingerprint=fp("cache-evictions"))
        self.assertTrue(allowed2)

    def test_unknown_fingerprint_cannot_suppress(self):
        allowed, reasons = suppress_precondition(
            self._report(), fingerprint="prod:x:nope:sig")
        self.assertFalse(allowed)
        self.assertIn("lock3_stale", reasons[0])


class TestMonitorV2(_BundleCase):
    def _monitor(self, **kw):
        v = self.validator(**kw)
        emitted = []
        mon = FreshnessMonitor(v, self.bundle, on_transition=emitted.append)
        return v, mon, emitted

    def test_boot_all_stale_still_serves(self):
        # Rot-matrix row 8: fail-open at the config layer. A validator that
        # refuses to boot on stale proofs converts calendar rot into outage.
        t = make_thresholds()
        build_bundle(
            self.bundle,
            fit_proof=make_fit_proof(trained_days_ago=200.0),
            threshold_attestation=make_threshold_attestation(t, attested_days_ago=40.0),
            entries=[(fp("old"), "prod",
                      make_allowlist_entry(fp("old"), attested_days_ago=200.0))])
        _, mon, _ = self._monitor()
        report = mon.boot()
        self.assertEqual(len(report.stale_locks()), 3)
        # …and it serves: per-alert reads work on the all-stale report.
        self.assertFalse(mon.current_report().is_fresh(LOCK1_CALIBRATION))

    def test_heartbeat_flips_on_ttl_crossing(self):
        build_bundle(self.bundle, entries=[
            (fp("cache-evictions"), "prod",
             make_allowlist_entry(fp("cache-evictions"), attested_days_ago=179.0)),
        ])
        _, mon, emitted = self._monitor()
        mon.boot()
        self.assertTrue(mon.current_report().entry_fresh(fp("cache-evictions")))
        self.clock.advance(2 * 86400.0)  # cross the 180d TTL
        records = mon.heartbeat()
        self.assertFalse(mon.current_report().entry_fresh(fp("cache-evictions")))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].lock, LOCK3_ALLOWLIST)
        self.assertEqual(records[0].entry_fingerprint, fp("cache-evictions"))
        self.assertIn("TTL expired", records[0].reason)
        self.assertEqual(len(emitted), 1)

    def test_transition_emitted_once_not_per_alert(self):
        build_bundle(self.bundle,
                     fit_proof=make_fit_proof(trained_days_ago=89.0))
        _, mon, emitted = self._monitor()
        mon.boot()
        self.clock.advance(2 * 86400.0)
        mon.heartbeat()
        self.assertEqual(len(emitted), 1)
        # 50 per-alert reads: no re-validation, no re-emission.
        for _ in range(50):
            mon.current_report().is_fresh(LOCK1_CALIBRATION)
        self.assertEqual(len(emitted), 1)

    def test_no_transition_when_nothing_changed(self):
        build_bundle(self.bundle)
        _, mon, emitted = self._monitor()
        mon.boot()
        self.assertEqual(mon.heartbeat(), [])
        self.assertEqual(emitted, [])

    def test_incident_linkage_voids_at_heartbeat(self):
        build_bundle(self.bundle)
        _, mon, emitted = self._monitor()
        mon.boot()
        self.assertTrue(mon.current_report().entry_fresh(fp("cache-evictions")))
        drift = {"incident_linked": {fp("cache-evictions"): "SEV-20231101"}}
        records = mon.heartbeat(drift_state=drift)
        self.assertEqual(len(records), 1)
        self.assertIn("VOID", records[0].reason)
        self.assertEqual(len(emitted), 1)

    def test_current_report_before_boot_raises(self):
        build_bundle(self.bundle)
        _, mon, _ = self._monitor()
        with self.assertRaises(RuntimeError):
            mon.current_report()


class TestTwoPointDiscipline(_BundleCase):
    def test_validation_only_at_load_and_refresh(self):
        build_bundle(self.bundle)
        counted: list = []
        v = self.validator(validations_counted=counted)
        mon = FreshnessMonitor(v, self.bundle)
        mon.boot()                      # V1: exactly one validation
        self.assertEqual(len(counted), 1)
        report = mon.current_report()
        clock_reads_after_boot = self.clock.reads
        for _ in range(1000):           # 1000 per-alert reads…
            report.is_fresh(LOCK1_CALIBRATION)
            report.is_fresh(LOCK2_THRESHOLD)
            report.entry_fresh(fp("cache-evictions"))
            suppress_precondition(report, fingerprint=fp("cache-evictions"))
        self.assertEqual(len(counted), 1)  # …zero additional validations
        self.assertEqual(self.clock.reads, clock_reads_after_boot)  # no clock reads
        mon.heartbeat()                 # V2: exactly one more validation
        self.assertEqual(len(counted), 2)


if __name__ == "__main__":
    unittest.main()
