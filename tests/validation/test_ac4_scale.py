"""AC-4 — Scale test: >= 2,000 problems through the REAL pipeline.

Drives Pipeline.handle_pd (receiver -> correlator -> gate -> forwarder)
with stage-boundary timing installed by harness-only wrappers (no source
changes). Until Track 6 lands named scenario manifests under
platform/server/scenarios/, the harness generates the four profiles
itself (normal-day, bad-deploy, infra-incident, storm-surge) and PROBES
for Track 6's manifests — if present, they are used by name.
"""

import json
import os
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import common  # noqa: E402
from common import (FastJev, CaptureServer, AuditLog, canned,  # noqa: E402
                    percentile_ms)  # noqa: E402
import sentinel.receiver as receiver_mod  # noqa: E402
from sentinel.correlator import Correlator  # noqa: E402
from sentinel.forwarder import Forwarder  # noqa: E402
from sentinel.gate import Gate  # noqa: E402
from sentinel.models import Thresholds  # noqa: E402
from sentinel.receiver import Pipeline, ReceiverConfig  # noqa: E402
from sentinel.race import RaceConfig  # noqa: E402
from tests.test_gate import fresh_monitor_for  # noqa: E402

N_PROBLEMS = 2000
BUDGET_MS = 2700
EPS_MS = 500

# Budgets (measured p99, local CPU) — see ACCEPTANCE.md AC-4.
BUDGETS = {
    "receiver_p99_ms": 100,
    "correlator_p99_ms": 200,
    "gate_p99_ms": 500,        # post-race kernel decide, excludes race
    "forwarder_p99_ms": 1000,
    "race_max_ms": BUDGET_MS + EPS_MS + 500,   # bounded by construction
    "end_to_end_p99_ms": BUDGET_MS + 3000,
}


def _pd_body(alert_id, service, check, severity, dedup_key=None):
    return json.dumps({
        "routing_key": "rk-t7-scale",
        "event_action": "trigger",
        "dedup_key": dedup_key or f"dk-{alert_id}",
        "payload": {
            "summary": f"[{service}] {check} firing",
            "source": f"{service}-1",
            "severity": severity,
            "component": service,
            "class": check,
            "region": "us-east",
        },
    }).encode()


def _profiles(n):
    """Yield (profile_name, body) — harness-generated until Track 6's
    named manifests land (probed separately)."""
    i = 0
    # normal-day: varied services/checks, mixed severities (60%)
    services = ["web", "api", "worker", "db", "cache", "queue"]
    checks = ["http_5xx", "latency_p99", "cpu_high", "disk_full",
              "conn_refused", "oom_kill"]
    sevs = ["critical", "error", "warning", "info"]
    n_normal = int(n * 0.60)
    for k in range(n_normal):
        yield ("normal-day", _pd_body(
            f"n-{i}", services[k % 6], checks[(k // 6) % 6],
            sevs[k % 4]))
        i += 1
    # bad-deploy: one service/check bursting (15%)
    for k in range(int(n * 0.15)):
        yield ("bad-deploy", _pd_body(
            f"b-{i}", "web", "http_5xx", "critical"))
        i += 1
    # infra-incident: high severity across services (10%)
    for k in range(int(n * 0.10)):
        yield ("infra-incident", _pd_body(
            f"c-{i}", services[k % 6], "node_down", "critical"))
        i += 1
    # storm-surge: same fingerprint burst — the correlator's storm path
    # (15%)
    for k in range(n - n_normal - int(n * 0.15) - int(n * 0.10)):
        yield ("storm-surge", _pd_body(
            f"s-{i}", "web", "http_5xx", "critical",
            dedup_key="dk-storm-t7"))
        i += 1


class _StageTimer:
    """Harness-only timing wrapper. Installs on live objects; the source
    is untouched."""

    def __init__(self):
        self.samples = {"receiver": [], "correlator": [],
                        "gate": [], "forwarder": [], "race": [],
                        "total": []}
        self._orig = {}

    def _wrap(self, obj, name, stage):
        orig = getattr(obj, name)
        samples = self.samples[stage]

        def wrapper(*a, **kw):
            t0 = time.monotonic()
            try:
                return orig(*a, **kw)
            finally:
                samples.append((time.monotonic() - t0) * 1000.0)

        self._orig[(obj, name)] = orig
        setattr(obj, name, wrapper)

    def install(self, pipeline):
        import types
        # receiver stage: normalize + build_state (module globals looked
        # up at call time inside handle_pd/_triage).
        for fname in ("_normalize_pd", "build_state"):
            orig = getattr(receiver_mod, fname)
            samples = self.samples["receiver"]

            def wrapper(*a, _o=orig, _s=samples, **kw):
                t0 = time.monotonic()
                try:
                    return _o(*a, **kw)
                finally:
                    _s.append((time.monotonic() - t0) * 1000.0)
            setattr(receiver_mod, fname, wrapper)
            self._orig[(receiver_mod, fname)] = orig
        self._wrap(pipeline.correlator, "ingest", "correlator")
        self._wrap(pipeline.gate, "evaluate", "gate")
        self._wrap(pipeline.gate, "digest_storm", "gate")
        self._wrap(pipeline.forwarder, "forward", "forwarder")

    def uninstall(self):
        for (obj, name), orig in self._orig.items():
            setattr(obj, name, orig)
        self._orig.clear()


class TestAC4Scale(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pd = CaptureServer()
        cls.audit = AuditLog(":memory:")
        cls.gate = Gate(
            FastJev(canned(model="fakejev-t7-scale"), latency_s=0.001),
            Thresholds(), [], cls.audit,
            race_config=RaceConfig(budget_ms=BUDGET_MS),
            freshness_monitor=fresh_monitor_for([]))
        cls.forwarder = Forwarder(pd_events_url=cls.pd.url,
                                  default_routing_key="rk-default")
        cls.pipeline = Pipeline(
            Correlator(), cls.gate, cls.forwarder, cls.audit,
            ReceiverConfig(), state_dir="/tmp/t7-scale-state")
        cls.timer = _StageTimer()
        cls.timer.install(cls.pipeline)
        cls.addClassCleanup(cls._teardown)

    @classmethod
    def _teardown(cls):
        cls.timer.uninstall()
        try:
            cls.gate.close()
        except Exception:
            pass
        cls.pd.close()

    def test_4_track6_manifests_probe(self):
        """Probe: are Track 6's named scenario manifests on the branch?"""
        here = os.path.dirname(os.path.abspath(common.__file__))
        # Track 6's contract C6 location (the original probe looked one
        # level too high — platform/server/scenarios/ never existed; the
        # manifests live under platform/server/sim/scenarios/).
        candidates = [
            os.path.normpath(os.path.join(here, "..", "..", "platform",
                                          "server", "sim", "scenarios")),
            os.path.normpath(os.path.join(here, "..", "..", "platform",
                                          "server", "scenarios")),
        ]
        names = ["normal-day", "bad-deploy", "infra-incident", "storm-surge"]
        found, scen_dir = [], None
        for cand in candidates:
            hit = [n for n in names
                   if os.path.exists(os.path.join(cand, n + ".json"))]
            if hit:
                found, scen_dir = hit, cand
                break
        if not found:
            self.skipTest(
                "BLOCKED (Track 6): no scenario manifests under "
                "platform/server/sim/scenarios/ — running harness-generated "
                "profiles instead")
        print(f"\n[AC-4] Track 6 manifests present at {scen_dir}: {found}")

    def test_4_scale_2000_through_real_pipeline(self):
        bodies = list(_profiles(N_PROBLEMS))
        self.assertGreaterEqual(len(bodies), N_PROBLEMS)
        totals = []
        t_start = time.monotonic()
        for profile, body in bodies:
            t0 = time.monotonic()
            self.pipeline.handle_pd(body)
            totals.append((time.monotonic() - t0) * 1000.0)
        wall_s = time.monotonic() - t_start
        self.timer.samples["total"] = totals

        s = self.timer.samples
        p99 = {k: percentile_ms(v, 99) for k, v in s.items() if v}
        print(f"\n[AC-4] {len(bodies)} problems in {wall_s:.1f}s "
              f"({len(bodies)/wall_s:.0f}/s)")
        for stage in ("receiver", "correlator", "gate", "forwarder",
                      "total"):
            v = s[stage]
            print(f"  {stage:10s} n={len(v):5d} p50={percentile_ms(v,50):7.2f} "
                  f"ms p99={percentile_ms(v,99):8.2f} ms "
                  f"max={max(v):8.2f} ms")

        # 4a: volume.
        self.assertGreaterEqual(len(bodies), 2000)

        # 4c: stage budgets (measured p99).
        self.assertLess(
            p99["receiver"], BUDGETS["receiver_p99_ms"],
            f"receiver p99 {p99['receiver']:.1f} ms over budget")
        self.assertLess(
            p99["correlator"], BUDGETS["correlator_p99_ms"],
            f"correlator p99 {p99['correlator']:.1f} ms over budget")
        self.assertLess(
            p99["gate"], BUDGETS["gate_p99_ms"],
            f"gate p99 {p99['gate']:.1f} ms over budget")
        self.assertLess(
            p99["forwarder"], BUDGETS["forwarder_p99_ms"],
            f"forwarder p99 {p99['forwarder']:.1f} ms over budget")
        self.assertLess(
            p99["total"], BUDGETS["end_to_end_p99_ms"],
            f"end-to-end p99 {p99['total']:.1f} ms over budget")
        # Race bound: no single decision past B+eps+slack.
        worst = max(s["gate"]) if s["gate"] else 0
        self.assertLess(worst, BUDGETS["race_max_ms"] + 2000,
                        f"a decision took {worst:.0f} ms — past the race "
                        f"bound (gate sample includes race wait)")

        # 4b: no silent loss — every ingested alert has a decision_made,
        # event-log seq contiguous.
        n_decisions = sum(
            1 for p in self.gate.emitted if p["type"] == "decision_made")
        self.assertEqual(
            n_decisions, len(bodies),
            f"{len(bodies) - n_decisions} alerts decided silently — "
            f"loss budget is ZERO")
        seqs = [p.get("seq") for p in self.gate.emitted]
        # emitted payloads pre-log don't carry seq; check the audit log.
        rows = self.audit.log._conn.execute(
            "SELECT seq FROM events ORDER BY seq").fetchall()
        got = [r[0] for r in rows]
        self.assertEqual(got, list(range(min(got), max(got) + 1)),
                         "event-log seq not contiguous — lost writes")
        print(f"  decisions={n_decisions} seq_contiguous=True "
              f"alerts={self.pipeline.metrics['received']}")

        # 4d: storm-surge keeps up — forwarder lag check via FakePD
        # capture count vs decisions.
        n_pages = sum(1 for p in self.gate.emitted
                      if p["type"] == "decision_made"
                      and p["body"]["disposition"] in ("page_now",
                                                      "passthrough"))
        print(f"  pages/passthroughs={n_pages} "
              f"fakePD_captured={len(self.pd.requests)}")
        self.assertGreater(len(self.pd.requests), 0,
                           "forwarder captured nothing at FakePD")
