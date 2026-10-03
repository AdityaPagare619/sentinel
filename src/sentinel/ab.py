"""A/B experiment harness for Jev question variants — lane L6 (2026-10-04).

Offline measurement only. A *variant* is a versioned JSON artifact
(``research/jev-behavior/variants/*.json``) describing transforms applied to
the frozen contract §3.3 questions. A *run* executes a fixed eval set under
N arms — paired, so every alert sees every arm with per-alert arm order
randomized — through the real production Gate path. A ``VariantClient``
wrapper rewrites the questions dict on its way into the inner client, times
the call, and records (variant_ref, variant_sha, latency, tokens, error).
``gate.py``, ``questions.py`` and ``race.py`` are never touched: variants
cannot leak into production paths.

Reports, per variant pair (B vs control A, with A1/A2 repeats as the
within-variant noise floor):

  * disposition-flip rate with Wilson 95% CI, vs the noise floor
  * McNemar (continuity-corrected) on paired noise/variant flips
  * per-question choice-flip rates, confidence shift, coverage@tau per arm
  * latency distributions per arm (n, min, p50, mean, p95, p99, max)
  * $/decision at observed input-token volumes (our arithmetic)

Reuses evalharness primitives (generate_alerts, script_response, FlipMock,
COVERAGE_TAUS) — this extends the eval harness, it does not duplicate it.

Live-run kill conditions (frozen in the math design doc):
  K1  rolling error rate > 5% over the last 50 calls -> abort
  K2  any error:auth_error (credential dead) -> abort
  K3  any error:rate_limited (429 is a hard stop) -> abort
  K4  B wire-identical to A (pre-live gate, assert_variant_effective)
  K5  rolling median latency of last 20 successes > 2400 ms (3x N=100 p50)
      -> the path is degraded; abort
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sentinel.synthetic import generate_alerts
from sentinel.state import build_state, input_sha256
from sentinel.questions import build_questions
from sentinel.models import Thresholds
from sentinel.quantized import AllowlistEntry, Attestation
from sentinel.gate import Gate
from sentinel.evalharness import FlipMock, script_response, state_key, COVERAGE_TAUS


# ---------------------------------------------------------------------------
# Variant artifacts
# ---------------------------------------------------------------------------

_VARIANT_REQUIRED = ("id", "version", "description", "transforms")
_VALID_OPS = ("shuffle_options", "reword_instructions", "reword_option")


class VariantError(ValueError):
    """Bad variant spec or a transform that violates design law."""


class VariantNoOp(VariantError):
    """K4: the B variant is wire-identical to A — refuse to spend live calls."""


class RunAborted(RuntimeError):
    """A live-run kill condition fired. Partial results are returned."""


@dataclass(frozen=True)
class Variant:
    id: str
    version: str
    description: str
    transforms: tuple
    sha256: str
    source_path: str = ""

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _canonical_ordered(obj) -> str:
    """Canonical JSON that PRESERVES mapping order (unlike _canonical).

    Used where order is the signal — e.g. the K4 wire-payload comparison:
    sort_keys=True would normalize away the criteria permutation the
    shuffle variant exists to test.
    """
    return json.dumps(obj, sort_keys=False, separators=(",", ":"),
                      ensure_ascii=True)


def variant_rng_seed(base_seed: int, ref: str) -> int:
    """Deterministic per-variant RNG seed (stable across processes — unlike
    hash(), which is salted)."""
    import zlib
    return base_seed + 1000 + zlib.crc32(ref.encode("utf-8"))


def load_variant(path: str) -> Variant:
    """Load and validate a versioned variant spec; sha256-stamp it."""
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    for key in _VARIANT_REQUIRED:
        if key not in spec:
            raise VariantError(f"{path}: missing required key {key!r}")
    transforms = spec["transforms"]
    if not isinstance(transforms, list):
        raise VariantError(f"{path}: 'transforms' must be a list")
    for t in transforms:
        if not isinstance(t, dict) or t.get("op") not in _VALID_OPS:
            raise VariantError(f"{path}: bad transform {t!r} "
                               f"(op must be one of {_VALID_OPS})")
        if "qid" not in t:
            raise VariantError(f"{path}: transform missing 'qid': {t!r}")
        op = t["op"]
        if op == "shuffle_options" and "seed" not in t:
            raise VariantError(f"{path}: shuffle_options needs 'seed': {t!r}")
        if op == "reword_instructions" and not str(t.get("instructions", "")).strip():
            raise VariantError(f"{path}: reword_instructions needs non-empty "
                               f"'instructions': {t!r}")
        if op == "reword_option" and (
                not t.get("option") or not str(t.get("description", "")).strip()):
            raise VariantError(f"{path}: reword_option needs 'option' and a "
                               f"non-empty 'description': {t!r}")
    sha = hashlib.sha256(_canonical(spec).encode("utf-8")).hexdigest()
    return Variant(id=str(spec["id"]), version=str(spec["version"]),
                   description=str(spec["description"]),
                   transforms=tuple(transforms), sha256=sha,
                   source_path=path)


def apply_variant(questions: dict, variant: Variant) -> dict:
    """Apply a variant's transforms to a questions dict (pure; no mutation).

    Enforces the frozen design law on the RESULT: every question keeps
    ``cannot_determine`` and every option keeps a non-empty description —
    bare labels stay banned even in experiments.
    """
    out: dict = {}
    for qid, q in questions.items():
        q2 = dict(q)
        crit = q.get("criteria")
        if isinstance(crit, dict):
            q2["criteria"] = dict(crit)
        out[qid] = q2
    for t in variant.transforms:
        op, qid = t["op"], t["qid"]
        if qid not in out:
            raise VariantError(f"variant {variant.ref}: unknown qid {qid!r}")
        target = out[qid]
        if op == "shuffle_options":
            rng = random.Random(t["seed"])
            items = list(target["criteria"].items())
            rng.shuffle(items)
            target["criteria"] = dict(items)
        elif op == "reword_instructions":
            target["instructions"] = t["instructions"]
        elif op == "reword_option":
            opt = t["option"]
            if opt not in target["criteria"]:
                raise VariantError(
                    f"variant {variant.ref}: qid {qid!r} has no option {opt!r}")
            target["criteria"][opt] = t["description"]
    for qid, q in out.items():
        crit = q.get("criteria") or {}
        if "cannot_determine" not in crit:
            raise VariantError(
                f"variant {variant.ref}: qid {qid!r} lost mandatory "
                f"'cannot_determine'")
        for opt, desc in crit.items():
            if not desc or not str(desc).strip():
                raise VariantError(
                    f"variant {variant.ref}: qid {qid!r} option {opt!r} has "
                    f"an empty description (bare labels banned)")
    return out


def assert_variant_effective(variant_a: Variant, variant_b: Variant) -> dict:
    """K4 pre-live gate: B's wire payload must differ from A's somewhere.

    Compares the transformed canonical questions (not live calls): with a
    fixed seed a shuffle is either the identity permutation (no-op — abort)
    or it isn't (every alert's payload differs).
    """
    qa = _canonical_ordered(apply_variant(build_questions(), variant_a))
    qb = _canonical_ordered(apply_variant(build_questions(), variant_b))
    if qa == qb:
        raise VariantNoOp(
            f"K4: B variant {variant_b.ref} is wire-identical to A "
            f"{variant_a.ref} — refusing to spend live calls on a no-op")
    return {"a_questions_sha": hashlib.sha256(qa.encode()).hexdigest()[:16],
            "b_questions_sha": hashlib.sha256(qb.encode()).hexdigest()[:16]}


# ---------------------------------------------------------------------------
# VariantClient — the only injection point (never touches production code)
# ---------------------------------------------------------------------------

class VariantClient:
    """Wraps any Jev client: applies the variant transform, times the call.

    Every call is recorded with the variant ref + spec sha, so result rows
    are self-describing. Works identically for live and scripted clients.
    """

    def __init__(self, inner, variant: Variant):
        self.inner = inner
        self.variant = variant
        self.calls: list[dict] = []

    def decide(self, state, questions):
        vq = apply_variant(questions, self.variant)
        t0 = time.perf_counter()
        resp, error = None, None
        try:
            resp = self.inner.decide(state, vq)
            return resp
        except Exception as exc:  # noqa: BLE001 — record, re-raise; Gate never-raises
            error = type(exc).__name__
            raise
        finally:
            ms = (time.perf_counter() - t0) * 1000.0
            self.calls.append({
                "variant_ref": self.variant.ref,
                "variant_sha": self.variant.sha256,
                "latency_ms": round(ms, 1),
                "input_tokens": (resp.input_tokens if resp is not None else None),
                "model": (resp.model if resp is not None else None),
                "error": error,
            })


# ---------------------------------------------------------------------------
# Fixed eval set
# ---------------------------------------------------------------------------

@dataclass
class EvalItem:
    alert_id: str
    alert: object
    label: dict
    state: dict
    state_sha256: str


def build_evalset(n: int, seed: int) -> list[EvalItem]:
    """Fixed eval set: production build_state (same call as receiver.py:208),
    frozen by (n, seed, sha)."""
    items = []
    for alert, label in generate_alerts(n, seed):
        state = build_state(alert, history={}, context={})
        items.append(EvalItem(alert_id=alert.alert_id, alert=alert, label=label,
                              state=state, state_sha256=input_sha256(state)))
    return items


def evalset_fingerprint(items: list[EvalItem]) -> str:
    canon = _canonical([{"alert_id": it.alert_id,
                         "state_sha256": it.state_sha256} for it in items])
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _allowlist_entries(pairs: list[tuple]) -> list:
    """Dual-attested allowlist entries for allowlist candidates.

    Mirrors evalharness.run_batch (ADR-013: bare fingerprints no longer
    suppress) so the A/B exercises the attestation path identically.
    """
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    return [
        AllowlistEntry(
            fingerprint=a.fingerprint, author="ab-harness",
            attestations=[
                Attestation("alice", now - timedelta(days=1), "lrq-9f2c-41ab", 30),
                Attestation("bob", now - timedelta(days=1), "lrq-9f2c-41ab", 30),
            ])
        for a, lab in pairs if lab.get("allowlist_candidate")
    ]


class _ListAudit:
    def __init__(self):
        self.rows = []

    def record(self, rec) -> int:
        self.rows.append(rec)
        return len(self.rows)


# ---------------------------------------------------------------------------
# Paired run
# ---------------------------------------------------------------------------

# Kill-condition constants (frozen in ab-math-design-2026-10-04.md).
K1_ERROR_WINDOW = 50
K1_MAX_ERROR_RATE = 0.05
K5_LAT_WINDOW = 20
K5_MAX_MEDIAN_MS = 2400.0  # 3x the N=100 warm p50 (816 ms)


@dataclass
class ArmResult:
    arm: str  # "A1" | "A2" | "B" — distinct even when the variant repeats
    alert_id: str
    variant_ref: str
    variant_sha: str
    disposition: str
    reason: str
    choices: dict = field(default_factory=dict)      # qid -> choice
    confidences: dict = field(default_factory=dict)  # qid -> confidence
    jev_latency_ms: float | None = None
    input_tokens: int | None = None
    jev_model: str | None = None
    error: str | None = None  # VariantClient-level exception, if any

    def to_row(self) -> dict:
        return {"arm": self.arm, "alert_id": self.alert_id,
                "variant_ref": self.variant_ref,
                "variant_sha": self.variant_sha, "disposition": self.disposition,
                "reason": self.reason, "choices": self.choices,
                "confidences": self.confidences,
                "jev_latency_ms": self.jev_latency_ms,
                "input_tokens": self.input_tokens, "jev_model": self.jev_model,
                "error": self.error}


def _q_attrs(rec) -> tuple[dict, dict]:
    choices, confs = {}, {}
    for qid, attr in (("severity", "q_severity"), ("owning_team", "q_team"),
                      ("disposition", "q_disposition")):
        ans = getattr(rec, attr, None)
        choices[qid] = ans.choice if ans is not None else None
        confs[qid] = ans.confidence if ans is not None else None
    return choices, confs


def _await_call(vc: VariantClient, mark: int, timeout_s: float = 10.0) -> dict:
    """Return the VariantClient call record for one evaluate.

    Normally exactly one new record exists when evaluate returns. On a
    timer-win the detached inference thread may still be running — poll
    briefly so its latency/tokens still enter the accounting.
    """
    deadline = time.monotonic() + timeout_s
    while len(vc.calls) <= mark and time.monotonic() < deadline:
        time.sleep(0.05)
    if len(vc.calls) <= mark:
        return {"variant_ref": vc.variant.ref, "variant_sha": vc.variant.sha256,
                "latency_ms": None, "input_tokens": None, "model": None,
                "error": "call_record_missing"}
    if len(vc.calls) > mark + 1:
        # Should not happen (one inference task per race); keep the first,
        # flag the anomaly rather than silently merging.
        vc.calls[mark]["error"] = (
            (vc.calls[mark].get("error") or "") + "|extra_calls:"
            + str(len(vc.calls) - mark - 1)).strip("|")
    return vc.calls[mark]


def run_ab(items: list[EvalItem], arms: list[Variant], client_factory,
           *, seed: int = 7, arm_names: list[str] | None = None,
           thresholds: Thresholds | None = None,
           progress=None) -> tuple[list[ArmResult], dict]:
    """Paired A/B run. Returns (results, run_meta).

    ``client_factory(variant, arm_name)`` builds one client per arm. Every
    alert runs under every arm; per-alert arm order is shuffled with a
    seeded RNG (kills time-drift confounding). Arms are keyed by NAME
    ("A1","A2","B") — not by variant ref — so repeated variants (the A1/A2
    noise-floor pair) stay independent. Kill conditions K1/K2/K3/K5 raise
    RunAborted; partial results collected so far are attached as ``.partial``.
    """
    if len(arms) < 2:
        raise ValueError("need at least 2 arms (A + B)")
    if arm_names is None:
        # Convention: the first two arms are the A1/A2 control pair (the
        # noise floor); the rest are B-arms. Matches paired_metrics defaults.
        arm_names = ["A1", "A2"] + [f"B{i}" if i > 1 else "B"
                                    for i in range(1, len(arms) - 1)]
    if len(arm_names) != len(arms) or len(set(arm_names)) != len(arm_names):
        raise ValueError("arm_names must be distinct and match arms")
    rng = random.Random(seed + 999)
    thresholds = thresholds or Thresholds()
    pairs = [(it.alert, it.label) for it in items]
    allowlist = _allowlist_entries(pairs)

    gates: dict[str, tuple[Gate, VariantClient]] = {}
    for name, v in zip(arm_names, arms):
        vc = VariantClient(client_factory(v, name), v)
        gate = Gate(client=vc, thresholds=thresholds, allowlist=allowlist,
                    audit=_ListAudit(), shadow=False)
        gates[name] = (gate, vc)

    results: list[ArmResult] = []
    recent_errors: list[int] = []   # 1/0 over the last K1_ERROR_WINDOW calls
    recent_lat: list[float] = []   # successful-call latencies, last K5_LAT_WINDOW
    aborted: str | None = None

    def note_call(reason: str, lat: float | None) -> str | None:
        """Update kill-condition windows. Returns an abort reason or None."""
        is_err = 1 if reason.startswith("error:") else 0
        recent_errors.append(is_err)
        del recent_errors[:-K1_ERROR_WINDOW]
        if lat is not None and not is_err:
            recent_lat.append(lat)
            del recent_lat[:-K5_LAT_WINDOW]
        if reason == "error:rate_limited":
            return "K3: JevRateLimited observed — 429 is a hard stop"
        if reason == "error:auth_error":
            return "K2: JevAuthError — credential dead, continuing is pointless"
        if (len(recent_errors) >= K1_ERROR_WINDOW
                and sum(recent_errors) / len(recent_errors) > K1_MAX_ERROR_RATE):
            return (f"K1: rolling error rate "
                    f"{sum(recent_errors) / len(recent_errors):.2%} over last "
                    f"{len(recent_errors)} calls exceeds {K1_MAX_ERROR_RATE:.0%}")
        if len(recent_lat) >= K5_LAT_WINDOW:
            med = sorted(recent_lat)[len(recent_lat) // 2]
            if med > K5_MAX_MEDIAN_MS:
                return (f"K5: rolling median latency {med:.0f} ms over last "
                        f"{len(recent_lat)} successes exceeds {K5_MAX_MEDIAN_MS:.0f} ms "
                        f"(3x N=100 p50) — path degraded")
        return None

    try:
        for i, it in enumerate(items):
            order = list(range(len(arms)))
            rng.shuffle(order)
            for ai in order:
                name, v = arm_names[ai], arms[ai]
                gate, vc = gates[name]
                mark = len(vc.calls)
                disp, rec = gate.evaluate(it.alert, it.state, {}, {})
                call = _await_call(vc, mark)
                choices, confs = _q_attrs(rec)
                results.append(ArmResult(
                    arm=name, alert_id=it.alert_id, variant_ref=v.ref,
                    variant_sha=v.sha256, disposition=disp.action,
                    reason=disp.reason, choices=choices, confidences=confs,
                    jev_latency_ms=call["latency_ms"],
                    input_tokens=call["input_tokens"],
                    jev_model=call["model"], error=call["error"]))
                aborted = note_call(disp.reason, call["latency_ms"])
                if aborted:
                    raise RunAborted(aborted)
            if progress is not None:
                progress(i + 1, len(items))
    except RunAborted as exc:
        exc.partial = results  # type: ignore[attr-defined]
        raise
    finally:
        for gate, _ in gates.values():
            close = getattr(gate, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    meta = {"n_alerts": len(items),
            "evalset_sha": evalset_fingerprint(items),
            "arms": [{"arm": name, "ref": v.ref, "sha": v.sha256,
                      "description": v.description}
                     for name, v in zip(arm_names, arms)],
            "aborted": aborted}
    return results, meta


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Wilson score interval. Returns (p_hat, lo, hi)."""
    if n <= 0:
        return 0.0, 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, center - half), min(1.0, center + half)


def mcnemar(b: int, c: int) -> dict:
    """McNemar on paired binary outcomes (continuity-corrected).

    b = noise_flip=0 & variant_flip=1, c = noise_flip=1 & variant_flip=0.
    b + c < 10 -> underpowered: no p-value claim, descriptive only.
    """
    n_d = b + c
    if n_d == 0:
        return {"b": b, "c": c, "discordant": 0, "chi2": 0.0,
                "p_value": None, "underpowered": True,
                "note": "no discordant pairs — variants agree everywhere"}
    chi2 = (abs(b - c) - 1) ** 2 / n_d
    # chi-square(1) survival: p = erfc(sqrt(chi2/2))
    p = math.erfc(math.sqrt(chi2 / 2.0))
    return {"b": b, "c": c, "discordant": n_d, "chi2": round(chi2, 4),
            "p_value": round(p, 4) if n_d >= 10 else None,
            "underpowered": n_d < 10,
            "note": ("fewer than 10 discordant pairs — descriptive only, "
                     "no significance claim") if n_d < 10 else ""}


def _lat_stats(xs: list[float | None]) -> dict:
    vals = sorted(x for x in xs if x is not None)
    n = len(vals)
    if n == 0:
        return {"n": 0}
    def pct(p: float) -> float:
        k = (n - 1) * p / 100.0
        lo, hi = math.floor(k), math.ceil(k)
        return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)
    return {"n": n, "min": round(vals[0], 1), "p50": round(pct(50), 1),
            "mean": round(sum(vals) / n, 1), "p95": round(pct(95), 1),
            "p99": round(pct(99), 1), "max": round(vals[-1], 1)}


def paired_metrics(results: list[ArmResult], arm_a1: str = "A1",
                   arm_a2: str = "A2", arm_b: str = "B") -> dict:
    """Paired comparison: B vs A1, with A1-vs-A2 as the noise floor.

    Arms are addressed by NAME ("A1","A2","B") — never by variant ref — so
    the repeated control variant stays two independent arms.
    """
    by_alert: dict[str, dict[str, ArmResult]] = {}
    for r in results:
        by_alert.setdefault(r.alert_id, {})[r.arm] = r
    # Keep only alerts with all three arms (partial runs stay honest).
    triples = [(d[arm_a1], d[arm_a2], d[arm_b]) for d in by_alert.values()
               if arm_a1 in d and arm_a2 in d and arm_b in d]
    n = len(triples)

    noise_flip = sum(1 for a1, a2, _ in triples
                     if a1.disposition != a2.disposition)
    variant_flip = sum(1 for a1, _, b in triples
                       if a1.disposition != b.disposition)
    _, nf_lo, nf_hi = wilson(noise_flip, n)
    _, vf_lo, vf_hi = wilson(variant_flip, n)
    # McNemar discordant cells on paired (noise_flip, variant_flip).
    b = sum(1 for a1, a2, bb in triples
            if a1.disposition == a2.disposition and a1.disposition != bb.disposition)
    c = sum(1 for a1, a2, bb in triples
            if a1.disposition != a2.disposition and a1.disposition == bb.disposition)
    mc = mcnemar(b, c)

    qids = ("severity", "owning_team", "disposition")
    q_flip_noise, q_flip_var = {}, {}
    for qid in qids:
        q_flip_noise[qid] = (
            sum(1 for a1, a2, _ in triples if a1.choices.get(qid) != a2.choices.get(qid)) / n
            if n else 0.0)
        q_flip_var[qid] = (
            sum(1 for a1, _, bb in triples if a1.choices.get(qid) != bb.choices.get(qid)) / n
            if n else 0.0)

    def conf_shift():
        ds = [abs((bb.confidences.get("disposition") or 0)
                  - (a1.confidences.get("disposition") or 0))
              for a1, _, bb in triples
              if bb.confidences.get("disposition") is not None
              and a1.confidences.get("disposition") is not None]
        return round(sum(ds) / len(ds), 4) if ds else None

    def coverage(arm: str) -> dict:
        out = {}
        for tau in COVERAGE_TAUS:
            vals = [d[arm].confidences["disposition"] for d in by_alert.values()
                    if arm in d and d[arm].confidences.get("disposition") is not None]
            out[tau] = round(sum(1 for x in vals if x >= tau) / len(vals), 4) if vals else None
        return out

    lat = {arm: _lat_stats([d[arm].jev_latency_ms for d in by_alert.values() if arm in d])
           for arm in (arm_a1, arm_a2, arm_b)}
    toks = {}
    for arm in (arm_a1, arm_a2, arm_b):
        vals = [d[arm].input_tokens for d in by_alert.values()
                if arm in d and d[arm].input_tokens]
        toks[arm] = {"n": len(vals),
                     "mean": round(sum(vals) / len(vals), 1) if vals else None}
    err = {arm: sum(1 for d in by_alert.values()
                    if arm in d and d[arm].reason.startswith("error:"))
           for arm in (arm_a1, arm_a2, arm_b)}
    models = {arm: sorted({d[arm].jev_model for d in by_alert.values()
                           if arm in d and d[arm].jev_model})
              for arm in (arm_a1, arm_a2, arm_b)}

    return {
        "n_paired": n,
        "arms": {"a1": arm_a1, "a2": arm_a2, "b": arm_b,
                 "a1_variant": triples[0][0].variant_ref if triples else None,
                 "b_variant": triples[0][2].variant_ref if triples else None},
        "noise_floor": {"arm_a1": arm_a1, "arm_a2": arm_a2,
                        "disposition_flip_rate": round(noise_flip / n, 4) if n else 0.0,
                        "flips": noise_flip, "wilson95": [round(nf_lo, 4), round(nf_hi, 4)]},
        "variant": {"arm_a": arm_a1, "arm_b": arm_b,
                    "disposition_flip_rate": round(variant_flip / n, 4) if n else 0.0,
                    "flips": variant_flip, "wilson95": [round(vf_lo, 4), round(vf_hi, 4)]},
        "mcnemar": mc,
        "choice_flip_per_question": {
            qid: {"noise_a1_a2": round(q_flip_noise[qid], 4),
                  "variant_a1_b": round(q_flip_var[qid], 4)} for qid in qids},
        "mean_abs_confidence_shift_q3": conf_shift(),
        "coverage_at_tau_q3": {arm_a1: coverage(arm_a1), arm_b: coverage(arm_b)},
        "latency_ms_per_arm": lat,
        "input_tokens_per_arm": toks,
        "error_dispositions_per_arm": err,
        "jev_model_echo_per_arm": models,
    }


# ---------------------------------------------------------------------------
# Cost accounting — our arithmetic, not the vendor's
# ---------------------------------------------------------------------------

JEV_USD_PER_M_INPUT = 0.042  # price-list fact (TypeSafe pricing page)
# Named reference: GPT-6 Sol classify+JSON, $2,878 / 1M decisions — independent
# AI/ML API study invoices, via our JEV research report §3.5 correction log.
# Different task, different token profile: the ratio is illustrative.
REF_GPT6_SOL_USD_PER_1M_DECISIONS = 2878.0


def cost_accounting(input_tokens_mean: float | None) -> dict:
    if not input_tokens_mean:
        return {"input_tokens_mean": None,
                "note": "no token data (all calls errored or mock without usage)"}
    usd_per_decision = input_tokens_mean * JEV_USD_PER_M_INPUT / 1e6
    usd_per_1m = input_tokens_mean * JEV_USD_PER_M_INPUT
    return {
        "input_tokens_mean": round(input_tokens_mean, 1),
        "jev_usd_per_m_input_tokens": JEV_USD_PER_M_INPUT,
        "usd_per_decision": round(usd_per_decision, 8),
        "usd_per_1k_decisions": round(usd_per_decision * 1000, 6),
        "usd_per_1m_decisions": round(usd_per_1m, 2),
        "vs_gpt6_sol_2878_per_1m": (
            round(REF_GPT6_SOL_USD_PER_1M_DECISIONS / usd_per_1m, 1)
            if usd_per_1m > 0 else None),
        "reference": "GPT-6 Sol classify+JSON $2,878/1M decisions "
                     "(independent AI/ML API study; DIFFERENT task — "
                     "illustrative ratio, not a workload claim)",
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def write_ab_report(metrics: dict, meta: dict, cost: dict, path: str,
                    conditions: dict) -> None:
    nf, var, mc = metrics["noise_floor"], metrics["variant"], metrics["mcnemar"]
    L = [
        "# Jev question-variant A/B — report",
        "",
        f"Date: {conditions.get('date', '?')} · mode: **{conditions.get('mode', '?')}**",
        "",
        "## Design (pre-registered)",
        "",
        f"Eval set: n={meta['n_alerts_requested']} alerts, seed {meta['seed']}, "
        f"fingerprint `{meta['evalset_sha'][:16]}…` (production `build_state`, "
        "fixed across arms).",
        f"Arms: " + "; ".join(
            f"{a['arm']}={a['ref']} (sha `{a['sha'][:12]}…`)" for a in meta["arms"]),
        "Paired: every alert ran under every arm; per-alert arm order "
        "randomized (seeded). A1/A2 = control twice = within-variant noise floor.",
        "",
        "Conditions: " + conditions.get("conditions_text", "?"),
        "",
        "## Disposition flips — variant vs noise floor",
        "",
        _md_table(["comparison", "n paired", "flips", "flip rate", "Wilson 95% CI"], [
            [f"noise floor ({nf['arm_a1']} vs {nf['arm_a2']})",
             metrics["n_paired"], nf["flips"], f"{nf['disposition_flip_rate']:.4f}",
             f"[{nf['wilson95'][0]:.4f}, {nf['wilson95'][1]:.4f}]"],
            [f"variant ({var['arm_a']} vs {var['arm_b']}; "
             f"{metrics['arms']['a1_variant']} vs {metrics['arms']['b_variant']})",
             metrics["n_paired"], var["flips"], f"{var['disposition_flip_rate']:.4f}",
             f"[{var['wilson95'][0]:.4f}, {var['wilson95'][1]:.4f}]"],
        ]),
        "",
        "## McNemar (paired noise-flip vs variant-flip)",
        "",
        f"Discordant pairs: b={mc['b']} (flip only under variant), "
        f"c={mc['c']} (flip only under noise); n_discordant={mc['discordant']}.",
    ]
    if mc["underpowered"]:
        L += [f"χ²={mc['chi2']:.4f}. **Underpowered — descriptive only.** {mc['note']}"]
    else:
        L += [f"χ²={mc['chi2']:.4f}, p={mc['p_value']:.4f}."]
    L += [
        "",
        "## Choice flips per question",
        "",
        _md_table(["question", "noise A1↔A2", "variant A1↔B"], [
            [qid,
             f"{metrics['choice_flip_per_question'][qid]['noise_a1_a2']:.4f}",
             f"{metrics['choice_flip_per_question'][qid]['variant_a1_b']:.4f}"]
            for qid in ("severity", "owning_team", "disposition")]),
        "",
        "## Calibration-relevant shifts (Q3 disposition)",
        "",
        f"Mean |Δconfidence| A1→B: {metrics['mean_abs_confidence_shift_q3']}",
        "",
        _md_table(["arm", "coverage@0.7", "coverage@0.8", "coverage@0.9"], [
            [arm, cov[0.7], cov[0.8], cov[0.9]]
            for arm, cov in metrics["coverage_at_tau_q3"].items()]),
        "",
        "## Latency per arm (client-side, ms)",
        "",
        _md_table(["arm", "n", "min", "p50", "mean", "p95", "p99", "max"], [
            [arm, s.get("n"), s.get("min"), s.get("p50"), s.get("mean"),
             s.get("p95"), s.get("p99"), s.get("max")]
            for arm, s in metrics["latency_ms_per_arm"].items()]),
        "",
        "Jev model echo per arm: " + "; ".join(
            f"{arm}: {', '.join(v) if v else '—'}"
            for arm, v in metrics["jev_model_echo_per_arm"].items()),
        "",
        "Error dispositions per arm: " + "; ".join(
            f"{arm}: {c}" for arm, c in metrics["error_dispositions_per_arm"].items()),
        "",
        "## Cost accounting (our arithmetic)",
        "",
    ]
    if cost.get("input_tokens_mean") is None:
        L += [cost.get("note", "no token data")]
    else:
        L += [
            f"Mean input tokens/decision: **{cost['input_tokens_mean']}** "
            f"(n={metrics['input_tokens_per_arm'][var['arm_a']]['n']} on arm {var['arm_a']}).",
            f"Jev price-list: ${cost['jev_usd_per_m_input_tokens']}/M input tokens, output free.",
            f"**${cost['usd_per_decision']:.8f} per decision** · "
            f"${cost['usd_per_1k_decisions']:.6f} per 1k · "
            f"**${cost['usd_per_1m_decisions']:.2f} per 1M decisions**.",
            f"Illustrative ratio vs {cost['reference']}: "
            f"**{cost['vs_gpt6_sol_2878_per_1m']}×**.",
            "",
            "Reading this honestly: the ratio's numerator is OUR measured "
            "tokens × the price list; the denominator is a different workload "
            "(classify+JSON on GPT-6 Sol). It answers 'what does Jev cost for "
            "OUR triage call vs what a frontier LLM costs for ITS routing call' "
            "— useful, not a claim that our workload is "
            f"{cost['vs_gpt6_sol_2878_per_1m']}× cheaper on Jev "
            "than on any alternative. Against cheap LLMs the gap collapses "
            "(research report: DeepSeek V4 Flash $77/1M vs Jev $98/1M on "
            "routing) — and Jev bills our long option descriptions every call.",
        ]
    if meta.get("aborted"):
        L += ["", f"## RUN ABORTED — {meta['aborted']}",
              "Numbers above are partial (n_paired counts only alerts with all three arms)."]
    L += [
        "",
        "## HONEST LIMITATIONS",
        "",
        "1. **Flip magnitude is not flip quality.** Without outcome labels on "
        "   the eval alerts, a variant that flips dispositions may flip toward "
        "   or away from correct. This harness measures behavioral sensitivity, "
        "   not improvement.",
        "2. **Synthetic alerts.** The eval set is seeded fixtures; real "
        "   customer alert text may be more or less order-sensitive.",
        "3. **Dry-run mode is plumbing-only.** The scripted mock keys answers "
        "   off state fingerprints, not question text — variant effects are "
        "   zero by construction there. Only live runs measure variants.",
        "4. **Single time window.** Vendor behavior drifts; one night's noise "
        "   floor is not a standing constant. Re-run the A1/A2 calibration "
        "   alongside every future variant.",
        "5. **n=200 is underpowered** for flip deltas under ~3pp at the "
        "   measured noise floor — the CIs are the finding, not the p-value.",
        "",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"wrote A/B report -> {path}")


def write_manifest(results: list[ArmResult], meta: dict, conditions: dict,
                   path: str) -> None:
    manifest = {"meta": meta, "conditions": conditions,
                "rows": [r.to_row() for r in results]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=True)
    print(f"wrote run manifest -> {path}")
