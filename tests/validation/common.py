"""Track 7 validation harness — shared fixtures.

Stdlib unittest only. Every fake below is deterministic and honestly
labeled: no fake ever presents itself as the real Jev vendor.
"""

import json
import os
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.join(_HERE, "..", "..")
_SRC = os.path.join(_REPO, "src")
for _p in (_SRC, os.path.join(_REPO, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sentinel.client import (Answer, DecisionResponse, JevError, JevOverloaded,
                             JevTimeout, MockSystemOneClient)  # noqa: E402
from sentinel.audit import AuditLog  # noqa: E402
from sentinel.correlator import Correlator  # noqa: E402
from sentinel.forwarder import Forwarder  # noqa: E402
from sentinel.gate import Gate, evaluate_policy  # noqa: E402
from sentinel.models import Thresholds  # noqa: E402
from sentinel.quantized import AllowlistEntry, Attestation  # noqa: E402
from sentinel.receiver import Pipeline, ReceiverConfig  # noqa: E402
from sentinel.state import build_state, input_sha256  # noqa: E402

import tests.helpers as _helpers  # noqa: E402
from tests.helpers import make_alert, CaptureServer  # noqa: E402
from tests.test_gate import fresh_monitor_for  # noqa: E402

from datetime import datetime, timedelta, timezone  # noqa: E402

__all__ = [
    "make_alert", "CaptureServer", "AuditLog",
    "answer", "canned", "FastJev", "SlowJev", "ExplodingJev", "ShedJev",
    "SuppressRig", "make_suppress_rig", "timed",
]


def answer(qid, choice, probs, conf):
    return Answer(qid=qid, qtype="choice", choice=choice, noul=None,
                  probabilities=dict(probs), confidence=conf)


def canned(p1=0.0, p2=0.0, p3=0.9, p4=0.1, conf=0.95,
           q1_choice="p3_medium", q3_choice="page_business_hours",
           team="platform", model="jev-1.13.0"):
    """One canned Jev answer. model= names the fake explicitly."""
    return DecisionResponse(
        model=model,
        answers={
            "severity": answer("severity", q1_choice,
                               {"p1_critical": p1, "p2_high": p2,
                                "p3_medium": p3, "p4_low": p4,
                                "known_noise": 0.0, "cannot_determine": 0.0},
                               conf),
            "owning_team": answer("owning_team", team, {team: 1.0}, 0.99),
            "disposition": answer("disposition", q3_choice,
                                  {q3_choice: 1.0}, conf),
        },
        input_tokens=100,
    )


class FastJev:
    """Deterministic fake: answers in ~1ms. model= labels the fake."""

    def __init__(self, response, latency_s=0.001):
        self.response = response
        self.latency_s = latency_s
        self.calls = 0

    def decide(self, state, questions):
        self.calls += 1
        time.sleep(self.latency_s)
        return self.response


class SlowJev(FastJev):
    """Deterministic fake that always exceeds short race budgets."""

    def __init__(self, response, latency_s=1.5):
        super().__init__(response, latency_s=latency_s)


class ExplodingJev:
    """Raises on every call — simulates a dead Jev backend."""

    def __init__(self, exc_factory=JevTimeout):
        self.exc_factory = exc_factory
        self.calls = 0

    def decide(self, state, questions):
        self.calls += 1
        raise self.exc_factory("jev is down (validation fake)")


class ShedJev(FastJev):
    """Fake whose decide() is never reached — used with a pool_size=1 /
    queue_size=0 runner to force the timer_won_shed path."""


def attested_entry(fingerprint, author="t7-auditor"):
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    return AllowlistEntry(
        fingerprint=fingerprint, author=author,
        attestations=[
            Attestation("alice", now - timedelta(days=1), "lrq-t7", 30),
            Attestation("bob", now - timedelta(days=1), "lrq-t7", 30),
        ])


class SuppressRig:
    """A gate wired so the suppress path is genuinely reachable:
    attested allowlist + all-fresh proofs + explicit corroboration
    witness + a canned confident Jev answer. drive(n) returns
    (disp, rec, payload) triples — the payload is the real
    decision_made record the gate emitted."""

    def __init__(self, model="fakejev-t7-1.0"):
        self.model = model
        self.audit = AuditLog(":memory:")
        self.fps = []
        self.gate = None
        self.client = FastJev(canned(p1=0.0, conf=0.95, model=model))

    def add_fingerprint(self, fp):
        self.fps.append(fp)
        return self

    def boot(self):
        from tests.test_corroboration import (
            CorroborationEvidence, KIND_SECOND_CHECK, second_check_floor,
            stub_corroroborator)
        from datetime import timedelta as _td
        entries = [attested_entry(fp) for fp in self.fps]
        now = datetime(2026, 10, 6, tzinfo=timezone.utc)
        decided = (now - _td(minutes=10)).isoformat()
        # One corroborator serving ALL rig fingerprints: returns the
        # second_check evidence matching the alert's own fingerprint.
        ev_by_fp = {
            fp: CorroborationEvidence(
                kind=KIND_SECOND_CHECK, fingerprint=fp,
                decided_at=decided, verifier_id="t7-probe-v1")
            for fp in self.fps
        }

        def corroborator(alert, floor, now_):
            ev = ev_by_fp.get(alert.fingerprint)
            return [ev] if ev is not None else []

        self.gate = Gate(
            self.client, Thresholds(), entries, self.audit,
            freshness_monitor=fresh_monitor_for(self.fps),
            corroborator=corroborator,
            silence_floor=second_check_floor("t7-probe-v1"))
        return self.gate

    def drive(self, alert):
        """Run one alert through the gate; returns (disp, rec, payload)."""
        state = build_state(alert, {}, {})
        disp, rec = self.gate.evaluate(alert, state, {}, {})
        payload = self.gate.emitted[-1] if self.gate.emitted else None
        return disp, rec, payload, state

    def close(self):
        try:
            self.gate.close()
        except Exception:
            pass


def make_suppress_rig(n_fps=3, model="fakejev-t7-1.0"):
    rig = SuppressRig(model=model)
    for i in range(n_fps):
        a = make_alert(alert_id=f"t7-sup-{i}", service=f"svc-{i}",
                       check="http_5xx")
        rig.add_fingerprint(a.fingerprint)
    rig.boot()
    return rig


def timed(fn):
    """Wrap a callable, returning (result, elapsed_ms) with monotonic."""
    t0 = time.monotonic()
    out = fn()
    return out, (time.monotonic() - t0) * 1000.0


def percentile_ms(samples, p):
    if not samples:
        return 0.0
    s = sorted(samples)
    k = min(len(s) - 1, int(p / 100.0 * len(s)))
    return s[k]
