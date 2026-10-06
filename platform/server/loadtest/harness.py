"""LOAD-TEST lane — concurrent millions-scale driver.

Drives the REAL Sentinel pipeline (receiver.Pipeline._triage: correlator ->
gate(race -> judge) -> forwarder) with concurrent alert injection, like a
real deployment's HTTP receiver would. Virtual time advances per batch;
within a batch, worker threads triage concurrently.

Batches: 60 virtual seconds each. Per batch:
  1. generate chunk events (seeded, deterministic)
  2. build alerts (globally unique ids across chunks)
  3. script the FaithfulJev bulk delegate for this batch's states
     (answers_for semantics — identical to sim_runner's scripting)
  4. mixed.set_faithful(new delegate)  [batch drained: no race in flight]
  5. vclock.set(batch start); submit all alerts to the pool; drain
  6. snapshot metrics

Honesty: every alert carries the [SIMULATED] labeling via _mk_alert;
FakePD sink is loopback-only with the SIM-FAKE key (same guards as sim).
Real PagerDuty is never addressable from this harness.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO_ROOT, "platform", "server", "sim"))
sys.path.insert(0, os.path.join(REPO_ROOT, "platform", "server", "loadtest"))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

import sim_runner as sim  # noqa: E402
from sentinel.client import FaithfulJev  # noqa: E402

from generator import (  # noqa: E402
    build_alerts_for_chunk, build_chunk_manifest, generate_chunk)
from mixed_jev import MixedJevClient, SurrogateJevClient  # noqa: E402


class LoadHarness:
    def __init__(self, *, seed: int, start_epoch: float,
                 sample_rate: float = 0.02, spend_cap_usd: float = 5.0,
                 jev_model: str = "jev-1.13.0", workers: int = 16,
                 batch_virtual_s: float = 60.0,
                 faithful_seed: int | None = None,
                 fault_profile=None):
        self.seed = seed
        self.start_epoch = start_epoch
        self.workers = workers
        self.batch_virtual_s = batch_virtual_s
        self.faithful_seed = faithful_seed if faithful_seed is not None else seed
        self.fault_profile = fault_profile
        # Pressure-phase override: when set, batches use THESE faults
        # (e.g. slow-tail) instead of fault_profile. The harness sets and
        # clears it; the driver decides the window.
        self.pressure_faults = None
        self._alert_seq = 0
        self._seq_lock = threading.Lock()

        # Judge: sampled real + faithful bulk. One MixedJevClient for the
        # whole run (the race caches it); the faithful delegate swaps per
        # drained batch.
        real = SurrogateJevClient(model=jev_model)
        self.mixed = MixedJevClient(
            real=real, faithful=None, sample_rate=sample_rate,
            spend_cap_usd=spend_cap_usd, seed=seed)
        self._faithful_batch = 0

        self.fakepd = sim.FakePDSink()
        self.tmpdir = None
        self.pipeline = None
        self.vclock = None
        self.judge_info = None
        # Page accounting: the sink accumulates full payloads; at millions
        # scale we compact per batch into counts + a bounded sample.
        self._page_counts = {"total": 0, "sim_tagged": 0,
                             "storm_aggregate": 0}
        self._page_sample: list[dict] = []
        self._sample_keep = 200

    # -- setup -----------------------------------------------------------
    def build(self, manifest_name: str = "loadtest"):
        manifest = build_chunk_manifest(manifest_name, self.seed,
                                        self.start_epoch, [])
        self.fakepd.start()
        self.tmpdir = tempfile.mkdtemp(prefix="sentinel-loadtest-")
        judge_tuple = ("mixed-jev", self.mixed, None)
        self.pipeline, self.vclock, self.judge_info = \
            sim.build_sim_pipeline(manifest, judge_tuple,
                                   self.fakepd.url, self.tmpdir)
        # Drift detection is intentionally inactive for the load run
        # (mixed judge); the pinned path was validated by the live run.
        print("[loadtest] gate pinned_model=None (drift inactive, loud "
              "warning expected); mixed judge: "
              + self.mixed.model, file=sys.stderr)
        return self

    def close(self):
        try:
            self.fakepd.stop()
        except Exception:
            pass

    # -- per-batch --------------------------------------------------------
    def _script_batch(self, alerts, kinds):
        script = sim.script_fakejev(alerts, kinds, self.seed)
        # script_fakejev returns a MockSystemOneClient; extract its script
        # to build the faithful delegate.
        faults = (self.pressure_faults
                  if self.pressure_faults is not None
                  else self.fault_profile)
        faithful = FaithfulJev(script._script, seed=self.faithful_seed,
                               faults=faults)
        self.mixed.set_faithful(faithful)
        self._faithful_batch += 1
        return faithful

    def run_batch(self, profiles, chunk_idx: int,
                  chunk_start_s: float, chunk_end_s: float) -> dict:
        from sentinel.state import input_sha256  # noqa
        events, self._alert_seq = generate_chunk(
            profiles, chunk_idx, self.seed, chunk_start_s, chunk_end_s,
            seq_offset=self._alert_seq)
        if not events:
            return {"alerts": 0, "wall_s": 0.0, "actions": {}}
        manifest = build_chunk_manifest("loadtest", self.seed,
                                        self.start_epoch, [])
        alerts = build_alerts_for_chunk(events, manifest)
        # Globally unique alert ids across chunks.
        for i, a in enumerate(alerts):
            a.alert_id = f"load-{self.seed:04d}-{chunk_idx:04d}-{i:06d}"
        kinds = [ev["kind"] for ev in events]
        self._script_batch(alerts, kinds)

        self.vclock.set(self.start_epoch + chunk_start_s)
        metrics_before = dict(self.pipeline.metrics)
        race_before = self.pipeline.gate._race_metrics.snapshot()
        actions: dict[str, int] = {}
        act_lock = threading.Lock()

        def triage(alert):
            try:
                action = self.pipeline._triage(alert, b"")
            except Exception as exc:  # never let one alert kill the batch
                action = f"harness_error:{type(exc).__name__}"
            with act_lock:
                actions[action] = actions.get(action, 0) + 1
            return action

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            list(pool.map(triage, alerts))
        wall_s = time.time() - t0

        metrics_after = dict(self.pipeline.metrics)
        race_after = self.pipeline.gate._race_metrics.snapshot()
        mdelta = {k: metrics_after.get(k, 0) - metrics_before.get(k, 0)
                  for k in metrics_after}
        rdelta = {k: race_after.get(k, 0) - race_before.get(k, 0)
                  for k in race_after}
        self._drain_pages()
        return {
            "alerts": len(alerts),
            "wall_s": round(wall_s, 2),
            "alerts_per_s": round(len(alerts) / wall_s, 1) if wall_s else 0,
            "actions": actions,
            "metrics_delta": mdelta,
            "race_delta": rdelta,
            "chunk_idx": chunk_idx,
        }

    def _drain_pages(self) -> None:
        """Compact the FakePD sink: counts + bounded sample, then clear.

        At millions scale the full payload list would exhaust memory; the
        honesty properties (sim-tagged, loopback-only) are preserved as
        counts, and a bounded sample stays inspectable.
        """
        received = self.fakepd.server.received
        for p in received:
            summary = str((p["payload"].get("payload") or {}).get("summary", ""))
            self._page_counts["total"] += 1
            if "[SIMULATED]" in summary:
                self._page_counts["sim_tagged"] += 1
            if summary.startswith("Alert storm:"):
                self._page_counts["storm_aggregate"] += 1
            if len(self._page_sample) < self._sample_keep:
                self._page_sample.append(
                    {"summary": summary[:160],
                     "dedup_key": p["payload"].get("dedup_key")})
        received.clear()

    def totals(self) -> dict:
        # Drain any pages that arrived after the last batch boundary.
        self._drain_pages()
        pages = self.fakepd.pages
        return {
            "pipeline_metrics": dict(self.pipeline.metrics),
            "race_metrics": self.pipeline.gate._race_metrics.snapshot(),
            "judge": self.mixed.summary(),
            "fakepd_pages": self._page_counts["total"],
            "fakepd_sim_tagged": self._page_counts["sim_tagged"],
            "fakepd_storm_aggregate": self._page_counts["storm_aggregate"],
            "fakepd_sample": self._page_sample[:10],
            "real_pagerduty_contacted": False,
        }
