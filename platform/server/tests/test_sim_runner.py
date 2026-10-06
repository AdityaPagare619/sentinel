"""Track 6 verification: the sim drives the real pipeline deterministically.

Run from the repo root:
    PYTHONPATH=src python3 -m pytest platform/server/tests/test_sim_runner.py -q

Covers the C6 verification bar:
  (a) seed determinism — same seed twice => identical problem counts and
      per-alert dispositions;
  (b) every scenario runs end-to-end through the real pipeline with sane
      PAGE/SUPPRESS splits;
  (c) storm-surge triggers the storm path (asserted, not assumed);
  (d) FakePD receives pages and no real PD client/key is ever used;
  (e) manifest schema enforcement (version field mandatory).
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO_ROOT, "platform", "server", "sim"))

import sim_runner as sim  # noqa: E402


class TestManifests(unittest.TestCase):
    def test_all_four_scenarios_load_and_versioned(self):
        for name in ("normal-day", "bad-deploy", "infra-incident",
                     "storm-surge"):
            m = sim.load_manifest(name)
            self.assertEqual(m["name"], name)
            self.assertIsInstance(m["version"], int)
            self.assertIsInstance(m["seed"], int)
            self.assertGreater(m["duration_s"], 0)
            self.assertTrue(m["profiles"])

    def test_unknown_scenario_rejected(self):
        with self.assertRaises(SystemExit):
            sim.load_manifest("does-not-exist")

    def test_generation_is_seed_pure(self):
        m = sim.load_manifest("normal-day")
        e1 = sim.generate_events(m)
        e2 = sim.generate_events(m)
        self.assertEqual(
            [(e["offset_s"], e["seq"], e["kind"], e["service"], e["check"],
              e["severity_in"], e["region"]) for e in e1],
            [(e["offset_s"], e["seq"], e["kind"], e["service"], e["check"],
              e["severity_in"], e["region"]) for e in e2])
        self.assertTrue(len(e1) > 100, "scenarios must stress, not demo")

    def test_all_alerts_labeled_simulated(self):
        m = sim.load_manifest("storm-surge")
        alerts = sim.build_alerts(sim.generate_events(m), m)
        for a in alerts:
            self.assertEqual(a.labels.get("simulated"), "true")
            self.assertTrue(a.title.startswith("[SIMULATED]"),
                            f"in-band honesty missing on {a.alert_id}")
            self.assertEqual(a.raw["generator"], "sim_runner")
            self.assertEqual(a.raw["seed"], m["seed"])


class TestDeterminism(unittest.TestCase):
    def _run(self, name):
        return sim.run_scenario(sim.load_manifest(name), judge="fake")

    def test_same_seed_twice_identical(self):
        for name in ("normal-day", "bad-deploy", "infra-incident",
                     "storm-surge"):
            r1 = self._run(name)
            r2 = self._run(name)
            self.assertEqual(r1["decision_signature"],
                             r2["decision_signature"],
                             f"{name}: not deterministic")
            self.assertEqual(r1["decision_histogram"],
                             r2["decision_histogram"])
            self.assertEqual(r1["fakepd"]["pages_received"],
                             r2["fakepd"]["pages_received"])
            self.assertEqual(r1["pipeline_metrics"], r2["pipeline_metrics"])
            self.assertTrue(all(r1["assertions"].values()))

    def test_different_seed_different_run(self):
        m = sim.load_manifest("normal-day")
        r1 = sim.run_scenario(m, judge="fake")
        m2 = dict(m, seed=m["seed"] + 1)
        r2 = sim.run_scenario(m2, judge="fake")
        self.assertNotEqual(r1["decision_signature"],
                            r2["decision_signature"])


class TestScenarioOutcomes(unittest.TestCase):
    def test_normal_day_splits(self):
        r = sim.run_scenario(sim.load_manifest("normal-day"), judge="fake")
        h = r["decision_histogram"]
        self.assertGreater(h.get("suppress", 0), 0, "noise must suppress")
        self.assertGreater(h.get("page_now", 0), 0, "SEVs must page")
        self.assertGreater(h.get("page_business_hours", 0), 0)
        self.assertGreater(r["pipeline_metrics"].get("deduped", 0), 0)

    def test_bad_deploy_change_window(self):
        r = sim.run_scenario(sim.load_manifest("bad-deploy"), judge="fake")
        self.assertGreater(r["pipeline_metrics"].get("change_window", 0), 0)
        self.assertGreater(r["decision_histogram"].get("page_now", 0), 0)

    def test_infra_incident_pages_sevs(self):
        r = sim.run_scenario(sim.load_manifest("infra-incident"),
                             judge="fake")
        h = r["decision_histogram"]
        self.assertGreater(h.get("page_now", 0), 50, "cascade SEVs page")

    def test_storm_surge_storm_path(self):
        r = sim.run_scenario(sim.load_manifest("storm-surge"), judge="fake")
        m = r["pipeline_metrics"]
        h = r["decision_histogram"]
        fp = r["fakepd"]
        self.assertGreaterEqual(m.get("storms", 0), 1,
                                "storm must declare")
        self.assertGreater(h.get("folded", 0), 0,
                           "storm continuations must fold, not page")
        self.assertEqual(fp["storm_aggregate_pages"], m["storms"],
                         "exactly one digest page per declared storm")
        self.assertGreaterEqual(r["peak_arrival_per_min"], 1000)


class TestFakePDIsolation(unittest.TestCase):
    def test_pages_sink_to_loopback_only(self):
        r = sim.run_scenario(sim.load_manifest("bad-deploy"), judge="fake")
        fp = r["fakepd"]
        self.assertTrue(fp["url"].startswith("http://127.0.0.1"),
                        f"FakePD must be loopback, got {fp['url']}")
        self.assertFalse(fp["real_pagerduty_contacted"])
        self.assertGreater(fp["pages_received"], 0)
        self.assertEqual(fp["sim_tagged_pages"] + fp["storm_aggregate_pages"],
                         fp["pages_received"],
                         "every page is tagged simulated or a storm aggregate")

    def test_forwarder_never_gets_a_real_key(self):
        sink = sim.FakePDSink()
        sink.start()
        try:
            import tempfile
            m = sim.load_manifest("normal-day")
            pipeline, _, _ = sim.build_sim_pipeline(
                m, ("fake-jev", sim.script_fakejev([], [], m["seed"]), None),
                sink.url, tempfile.mkdtemp(prefix="sentinel-sim-test-"))
            key, source = pipeline.forwarder._resolve_key()
            self.assertEqual(key, sim.FAKE_PD_KEY)
            self.assertEqual(source, "sim")
            self.assertNotIn("pagerduty.com", pipeline.forwarder.pd_events_url)
        finally:
            sink.stop()

    def test_no_real_key_even_when_env_has_one(self):
        old = os.environ.get("PD_ROUTING_KEY")
        os.environ["PD_ROUTING_KEY"] = "deadbeef" * 4  # fake "real" key
        try:
            self.test_forwarder_never_gets_a_real_key()
        finally:
            if old is None:
                del os.environ["PD_ROUTING_KEY"]
            else:
                os.environ["PD_ROUTING_KEY"] = old


class TestJudgeAdapter(unittest.TestCase):
    def test_fake_judge_is_mock(self):
        name, client, pin = sim.resolve_judge("fake")
        self.assertEqual(name, "fake-jev")
        self.assertIsNone(client)
        self.assertIsNone(pin)

    def test_real_judge_refuses_without_key(self):
        old = os.environ.pop("TYPESAFE_API_KEY", None)
        try:
            with self.assertRaises(SystemExit):
                sim.resolve_judge("real")
        finally:
            if old is not None:
                os.environ["TYPESAFE_API_KEY"] = old

    def test_real_judge_builds_real_client_with_key(self):
        # Construction only — never calls decide() (no spend, no network).
        old = os.environ.get("TYPESAFE_API_KEY")
        os.environ["TYPESAFE_API_KEY"] = "test-key-not-real"
        try:
            name, client, pin = sim.resolve_judge("real",
                                                  jev_model="jev-9.9.9")
            self.assertEqual(name, "jev")
            self.assertEqual(pin, "jev-9.9.9")
            self.assertIsInstance(client, sim.SystemOneClient)
        finally:
            if old is None:
                del os.environ["TYPESAFE_API_KEY"]
            else:
                os.environ["TYPESAFE_API_KEY"] = old

    def test_unknown_judge_rejected(self):
        with self.assertRaises(SystemExit):
            sim.resolve_judge("sometimes")


if __name__ == "__main__":
    unittest.main()
