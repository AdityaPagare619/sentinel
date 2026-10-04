"""ADR-017/D4 — scheme-v2 fingerprints, dual-write migration, derived ban.

Covers the two ratification conditions:
  (a) fingerprint_for includes env+cluster, with dual-write + re-attestation
      migration (bounded window, loud log on legacy resolution);
  (b) the security-category ban is DERIVED at admission from check-name/team
      taxonomy patterns — never a self-asserted boolean.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:  # makes `python -m unittest discover -s tests` work
    sys.path.insert(0, _SRC)  # regardless of import order (stdlib only)

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from sentinel.audit import AuditLog
from sentinel.client import MockSystemOneClient
from sentinel.correlator import (fingerprint_for, legacy_fingerprint_of)
from sentinel.freshness import (AllowlistAttestation, ProofFormatError,
                                SECURITY_BAN_TAXONOMY_VERSION,
                                allowlist_entry_freshness,
                                derive_security_ban)
from sentinel.gate import Gate, _mark_legacy_resolution
from sentinel.models import Thresholds
from sentinel.quantized import FitStore
from sentinel.state import build_state, input_sha256

from tests.freshness_fixtures import make_allowlist_entry
from tests.helpers import make_alert
from test_gate import canned, fresh_monitor_for
from test_quantized_gate import _fit, PINNED

NOW = datetime(2026, 10, 3, 13, 0, 0, tzinfo=timezone.utc)


def _rfc3339(dt):
    return dt.isoformat().replace("+00:00", "Z")


def _load_migration_module():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "scripts", "migrate_fingerprints_v1_v2.py")
    spec = importlib.util.spec_from_file_location("migrate_fingerprints_v1_v2",
                                                  path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _GateHarness:
    """Production Shape-2 gate: config allowlist holds v1 strings (as the
    pre-migration config file does); the legacy map bridges the window."""

    def _gate(self, alert, *, window_ends_at, extra_allowlist=()):
        v1 = legacy_fingerprint_of(alert)
        v2 = alert.fingerprint
        store = FitStore()
        store.put(_fit(n=1351, k=0))
        resp = canned(p1=0.0, conf=0.95)
        state = build_state(alert, {}, {})
        client = MockSystemOneClient({input_sha256(state): resp})
        audit = AuditLog(":memory:")
        gate = Gate(client, Thresholds(), {v1, *extra_allowlist}, audit,
                    fit_store=store, pinned_model=PINNED, org="org-c",
                    clock=lambda: NOW,
                    freshness_monitor=fresh_monitor_for([v2]),
                    legacy_allowlist={v1: v2},
                    legacy_window_ends_at=window_ends_at)
        return gate, state


class TestLegacyWindowResolution(unittest.TestCase, _GateHarness):
    def setUp(self):
        # No env/cluster labels: the v2 fingerprint is estate-qualified with
        # the explicit empty namespace; the v1 is the legacy env-blind hash.
        self.alert = make_alert()
        self.v1 = legacy_fingerprint_of(self.alert)
        self.v2 = self.alert.fingerprint
        self.assertNotEqual(self.v1, self.v2)

    def _plain_gate(self, window_ends_at):
        audit = AuditLog(":memory:")
        return Gate(MockSystemOneClient({}), Thresholds(), {self.v1}, audit,
                    clock=lambda: NOW,
                    legacy_allowlist={self.v1: self.v2},
                    legacy_window_ends_at=window_ends_at)

    def test_open_window_resolves_with_loud_log(self):
        gate = self._plain_gate(_rfc3339(NOW + timedelta(days=29)))
        with self.assertLogs("sentinel.gate", level="WARNING") as logs:
            effective, via_legacy = gate._effective_allowlist(self.alert)
        self.assertTrue(via_legacy)
        self.assertIn(self.alert.fingerprint, effective)
        self.assertIn(self.v1, effective)  # original set preserved
        self.assertEqual(gate.legacy_resolutions, 1)
        self.assertTrue(
            any("LEGACY-FINGERPRINT-RESOLVED" in m for m in logs.output),
            f"expected the loud log, got: {logs.output}")

    def test_closed_window_does_not_resolve(self):
        gate = self._plain_gate(_rfc3339(NOW - timedelta(days=1)))
        with self.assertNoLogs("sentinel.gate", level="WARNING"):
            effective, via_legacy = gate._effective_allowlist(self.alert)
        self.assertFalse(via_legacy)
        self.assertIs(effective, gate.allowlist)
        self.assertEqual(gate.legacy_resolutions, 0)

    def test_unparseable_window_fails_closed(self):
        gate = self._plain_gate("not-a-date")
        effective, via_legacy = gate._effective_allowlist(self.alert)
        self.assertFalse(via_legacy)
        self.assertEqual(gate.legacy_resolutions, 0)

    def test_no_legacy_config_no_resolution(self):
        audit = AuditLog(":memory:")
        gate = Gate(MockSystemOneClient({}), Thresholds(), {self.v1}, audit,
                    clock=lambda: NOW)
        effective, via_legacy = gate._effective_allowlist(self.alert)
        self.assertFalse(via_legacy)

    def test_direct_v2_hit_never_touches_legacy(self):
        audit = AuditLog(":memory:")
        gate = Gate(MockSystemOneClient({}), Thresholds(), {self.v2}, audit,
                    clock=lambda: NOW,
                    legacy_allowlist={self.v1: self.v2},
                    legacy_window_ends_at=_rfc3339(NOW + timedelta(days=29)))
        with self.assertNoLogs("sentinel.gate", level="WARNING"):
            effective, via_legacy = gate._effective_allowlist(self.alert)
        self.assertFalse(via_legacy)
        self.assertEqual(gate.legacy_resolutions, 0)

    def test_mark_legacy_resolution_names_it_in_lock_detail(self):
        class _V:
            lock_evaluation = {"allowlist": {"verdict": "pass",
                                             "detail": "fingerprint in allowlist"}}
        v = _V()
        _mark_legacy_resolution(v)
        self.assertIn("LEGACY", v.lock_evaluation["allowlist"]["detail"])
        self.assertIn("fingerprint in allowlist",
                      v.lock_evaluation["allowlist"]["detail"])


class TestLegacyEndToEndSuppress(unittest.TestCase, _GateHarness):
    def test_legacy_v1_entry_still_suppresses_during_window(self):
        alert = make_alert()
        gate, state = self._gate(
            alert, window_ends_at=_rfc3339(NOW + timedelta(days=29)))
        with self.assertLogs("sentinel.gate", level="WARNING"):
            disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertEqual(disp.action, "suppress")
        self.assertEqual(disp.reason, "allowlist")  # taxonomy unchanged
        self.assertEqual(gate.legacy_resolutions, 1)

    def test_legacy_v1_entry_stops_suppressing_after_window(self):
        alert = make_alert()
        gate, state = self._gate(
            alert, window_ends_at=_rfc3339(NOW - timedelta(days=1)))
        disp, _rec = gate.evaluate(alert, state, {}, {})
        self.assertNotEqual(disp.action, "suppress")
        self.assertEqual(gate.legacy_resolutions, 0)


class TestDerivedSecurityBan(unittest.TestCase):
    def test_banned_check_names(self):
        for check in ("waf_block_rate", "ids_alert_volume", "siem_ingest_lag",
                      "brute_force_logins", "credential_stuffing",
                      "password_spray", "token_leak", "malware_detections",
                      "ransomware_canary", "c2_beacon", "vuln_scan_critical",
                      "cve_2026_1234", "tls_cert_expiry", "exfil_bytes_out"):
            banned, reason = derive_security_ban(check, "platform")
            self.assertTrue(banned, f"{check} should be banned")
            self.assertIn(SECURITY_BAN_TAXONOMY_VERSION, reason)

    def test_banned_teams(self):
        for team in ("security", "infosec", "appsec", "soc", "csirt",
                     "threat_intel", "vuln_mgmt"):
            banned, _ = derive_security_ban("cpu_util", team)
            self.assertTrue(banned, f"team {team} should be banned")

    def test_unbanned_names_pass(self):
        for check, team in (("cpu_util", "platform"),
                            ("cache_evictions", "payments"),
                            ("p99_latency", "checkout"),
                            # "auth" as a substring is NOT a ban (token
                            # boundaries are load-bearing):
                            ("auth_service_cpu", "platform"),
                            ("authorizer_p99", "edge")):
            banned, _ = derive_security_ban(check, team)
            self.assertFalse(banned, f"{check}/{team} should NOT be banned")

    def test_soc_does_not_match_social(self):
        banned, _ = derive_security_ban("cpu_util", "social")
        self.assertFalse(banned)

    def test_banned_check_cannot_enter_allowlist(self):
        # The admission gate: from_dict raises — the entry never exists.
        with self.assertRaises(ProofFormatError) as ctx:
            AllowlistAttestation.from_dict(
                make_allowlist_entry("fp-banned", check="waf_block_rate",
                                     team="platform"))
        self.assertIn("REJECTED", str(ctx.exception))

    def test_banned_team_cannot_enter_allowlist(self):
        with self.assertRaises(ProofFormatError):
            AllowlistAttestation.from_dict(
                make_allowlist_entry("fp-banned", check="cpu_util",
                                     team="soc"))

    def test_unbanned_check_admitted(self):
        entry = AllowlistAttestation.from_dict(
            make_allowlist_entry("fp-ok", check="cpu_util", team="platform"))
        self.assertFalse(entry.security_category_ban)
        self.assertEqual(entry.check, "cpu_util")

    def test_missing_taxonomy_rejected(self):
        d = make_allowlist_entry("fp-x", check="cpu_util", team="platform")
        del d["check"]
        del d["team"]
        with self.assertRaises(ProofFormatError) as ctx:
            AllowlistAttestation.from_dict(d)
        self.assertIn("taxonomy", str(ctx.exception))

    def test_stored_boolean_disagreement_rejected(self):
        # Self-assertion is dead: stored True + derived False => rejected.
        with self.assertRaises(ProofFormatError):
            AllowlistAttestation.from_dict(
                make_allowlist_entry("fp-x", check="cpu_util",
                                     team="platform",
                                     security_category_ban=True))

    def test_derived_ban_is_defense_in_depth_at_validation(self):
        entry = AllowlistAttestation.from_dict(
            make_allowlist_entry("fp-ok", check="cpu_util", team="platform"))
        entry.security_category_ban = True  # simulate a non-admitted entry
        lf = allowlist_entry_freshness(entry, NOW.timestamp())
        self.assertFalse(lf.fresh)
        self.assertIn("banned", lf.reason)

    def test_pending_re_attestation_resolves_only_in_window(self):
        from tests.freshness_fixtures import BASE_EPOCH
        d = make_allowlist_entry("fp-p", check="cpu_util", team="platform")
        d["re_attestation"] = "pending"
        entry = AllowlistAttestation.from_dict(d)
        base = datetime.fromtimestamp(BASE_EPOCH, tz=timezone.utc)
        lf = allowlist_entry_freshness(
            entry, BASE_EPOCH,
            migration_window_ends_at=_rfc3339(base + timedelta(days=5)))
        self.assertTrue(lf.fresh, lf.reason)
        lf = allowlist_entry_freshness(
            entry, BASE_EPOCH,
            migration_window_ends_at=_rfc3339(base - timedelta(days=1)))
        self.assertFalse(lf.fresh)
        self.assertIn("re-attestation", lf.reason)


class TestMigrationScript(unittest.TestCase):
    def setUp(self):
        self.mig = _load_migration_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _components(self, service, check, severity, region, env, cluster,
                    team="platform"):
        return {"service": service, "check": check, "severity_in": severity,
                "region": region, "env": env, "cluster": cluster,
                "team": team}

    def _write_bundle(self, entries):
        """entries: list of (v1_fp, attestation_dict)."""
        bundle = os.path.join(self.tmp.name, "bundle")
        proofs = os.path.join(bundle, "proofs")
        os.makedirs(proofs, exist_ok=True)
        envs: dict = {}
        atts: dict = {}
        for v1, att in entries:
            envs.setdefault("prod", {"entries": []})["entries"].append(v1)
            atts[v1] = att
        with open(os.path.join(bundle, "allowlist.json"), "w") as fh:
            json.dump({"envs": envs}, fh)
        with open(os.path.join(proofs, "allowlist_attestations.json"),
                  "w") as fh:
            json.dump({"entries": atts}, fh)
        return bundle

    def _manifest(self, comps):
        manifest = {}
        for c in comps:
            v1 = self.mig.v1_fingerprint(c["service"], c["check"],
                                         c["severity_in"], c["region"])
            manifest[v1] = c
        return manifest

    def test_dual_write_round_trip(self):
        comps = [
            self._components("web", "http_5xx", "critical", "us-east",
                             "prod", "us-east-1a"),
            self._components("api", "p99_latency", "warning", "eu-west",
                             "prod", "eu-west-1b", team="checkout"),
        ]
        manifest = self._manifest(comps)
        v1s = list(manifest.keys())
        bundle = self._write_bundle(
            [(v1, make_allowlist_entry(v1, check=manifest[v1]["check"],
                                      team=manifest[v1]["team"]))
             for v1 in v1s])
        manifest_path = os.path.join(self.tmp.name, "manifest.json")
        with open(manifest_path, "w") as fh:
            json.dump(manifest, fh)
        out = os.path.join(self.tmp.name, "out")

        with self.assertLogs("sentinel.migrate_fingerprints",
                             level="WARNING") as logs:
            summary = self.mig.migrate(bundle, manifest, 30, out)

        self.assertTrue(summary["migrated"])
        self.assertEqual(summary["carried_over"], 2)
        self.assertEqual(summary["skipped"], 0)
        self.assertEqual(summary["refused_security_ban"], 0)
        # Loud log for every carried-over legacy fingerprint.
        carried = [m for m in logs.output
                   if "LEGACY-FINGERPRINT-CARRIED-OVER" in m]
        self.assertEqual(len(carried), 2)

        with open(os.path.join(out, "allowlist.json")) as fh:
            new_allowlist = json.load(fh)
        self.assertEqual(new_allowlist["fingerprint_scheme"], 2)
        expected_v2 = {self.mig.v2_fingerprint(
            c["service"], c["check"], c["severity_in"], c["region"],
            c["env"], c["cluster"]) for c in comps}
        self.assertEqual(set(new_allowlist["envs"]["prod"]["entries"]),
                         expected_v2)
        self.assertEqual(set(new_allowlist["legacy_v1"]["entries"]),
                         set(v1s))
        window_ends = new_allowlist["legacy_v1"]["window_ends_at"]
        ends_at = datetime.fromisoformat(window_ends.replace("Z", "+00:00"))
        self.assertTrue(timedelta(days=29) < ends_at - NOW
                        < timedelta(days=31))

        with open(os.path.join(out, "legacy_allowlist.json")) as fh:
            legacy = json.load(fh)
        self.assertEqual(set(legacy["entries"].keys()), set(v1s))
        self.assertEqual(set(legacy["entries"].values()), expected_v2)
        self.assertEqual(legacy["window_ends_at"], window_ends)

        with open(os.path.join(out, "proofs",
                               "allowlist_attestations.json")) as fh:
            proofs = json.load(fh)["entries"]
        for v1 in v1s:
            v2 = legacy["entries"][v1]
            self.assertEqual(proofs[v2]["re_attestation"], "pending")
            self.assertEqual(proofs[v2]["check"], manifest[v1]["check"])
            self.assertTrue(proofs[v1]["legacy_v1"])
        # The v2 hash really is env+cluster qualified.
        v2a = legacy["entries"][v1s[0]]
        self.assertEqual(
            v2a, fingerprint_for("web", "http_5xx", "critical", "us-east",
                                 env="prod", cluster="us-east-1a"))

        with open(os.path.join(out, "re_attestation_manifest.json")) as fh:
            re_att = json.load(fh)
        self.assertEqual(len(re_att["entries"]), 2)
        self.assertTrue(all(e["status"] == "pending"
                            for e in re_att["entries"]))

        # Idempotent: re-run is a no-op.
        summary2 = self.mig.migrate(out, manifest, 30, out)
        self.assertFalse(summary2["migrated"])

    def test_security_banned_check_refused_not_carried(self):
        bad = self._components("edge", "waf_block_rate", "critical",
                               "us-east", "prod", "us-east-1a",
                               team="platform")
        good = self._components("web", "http_5xx", "critical", "us-east",
                                "prod", "us-east-1a")
        manifest = self._manifest([bad, good])
        v1s = list(manifest.keys())
        bundle = self._write_bundle(
            [(v1, make_allowlist_entry(v1, check=manifest[v1]["check"],
                                      team=manifest[v1]["team"]))
             for v1 in v1s])
        out = os.path.join(self.tmp.name, "out2")
        summary = self.mig.migrate(bundle, manifest, 30, out)
        self.assertEqual(summary["carried_over"], 1)
        self.assertEqual(summary["refused_security_ban"], 1)
        with open(os.path.join(out, "allowlist.json")) as fh:
            new_allowlist = json.load(fh)
        self.assertEqual(len(new_allowlist["envs"]["prod"]["entries"]), 1)

    def test_missing_manifest_entry_skipped_loudly_not_dropped_silently(self):
        good = self._components("web", "http_5xx", "critical", "us-east",
                                "prod", "us-east-1a")
        manifest = self._manifest([good])
        v1_good = next(iter(manifest))
        v1_orphan = "0" * 16
        bundle = self._write_bundle([
            (v1_good, make_allowlist_entry(v1_good, check="http_5xx")),
            (v1_orphan, make_allowlist_entry(v1_orphan, check="http_5xx")),
        ])
        out = os.path.join(self.tmp.name, "out3")
        with self.assertLogs("sentinel.migrate_fingerprints",
                             level="ERROR") as logs:
            summary = self.mig.migrate(bundle, manifest, 30, out)
        self.assertEqual(summary["carried_over"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.assertTrue(any("LEGACY-FINGERPRINT-SKIPPED" in m
                            for m in logs.output))
        self.assertTrue(any(v1_orphan in e for e in summary["errors"]))

    def test_manifest_integrity_failure_skipped(self):
        good = self._components("web", "http_5xx", "critical", "us-east",
                                "prod", "us-east-1a")
        v1_real = self.mig.v1_fingerprint("web", "http_5xx", "critical",
                                          "us-east")
        lying = dict(good, check="totally_different_check")
        manifest = {v1_real: lying}
        bundle = self._write_bundle(
            [(v1_real, make_allowlist_entry(v1_real, check="http_5xx"))])
        out = os.path.join(self.tmp.name, "out4")
        with self.assertRaises(RuntimeError):
            self.mig.migrate(bundle, manifest, 30, out)

    def test_window_days_bounded(self):
        with self.assertRaises(SystemExit):
            self.mig.main(["--bundle-dir", "x", "--manifest", "y",
                           "--window-days", "0"])
        with self.assertRaises(SystemExit):
            self.mig.main(["--bundle-dir", "x", "--manifest", "y",
                           "--window-days", "91"])


if __name__ == "__main__":
    unittest.main()
