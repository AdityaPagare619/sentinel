"""Calibration eval harness — ARCHITECTURE.md §6.

Replays seeded synthetic alerts through the Gate with a MockSystemOneClient
scripted from the outcome labels, then measures:

  * severity / team-routing / disposition accuracy vs outcome labels
  * ECE (10-bin) on Q1 severity and Q3 disposition
  * coverage@tau for tau in {0.7, 0.8, 0.9}
  * flip rate: 100 repeats on a 200-alert sample (bar < 2% disposition change)
  * option-order shuffle: permuted criteria order on 200 alerts (bar < 3%)
  * false-suppress rate on confirmed SEV1s (the trust metric)

Writes calibration-report.md with tables and an explicit HONEST LIMITATIONS
section. Synthetic data proves plumbing, not production accuracy.

Usage:
    PYTHONPATH=src python -m sentinel.evalharness --n 2000 --seed 7 \\
        [--flip-rate 0.02] [--label-noise 0.0] -o calibration-report.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random

from sentinel.client import Answer, DecisionResponse, MockSystemOneClient
from sentinel.models import Thresholds
from sentinel.quantized import AllowlistEntry, Attestation
from datetime import datetime, timedelta, timezone

try:
    from sentinel.gate import Gate  # real one, when it lands
    _USING_SHIM_GATE = False
except ImportError:  # parallel workstream hasn't landed yet
    from sentinel._shims import Gate
    _USING_SHIM_GATE = True

from sentinel.synthetic import generate_alerts, script_answers

FLIP_BAR = 0.02
SHUFFLE_BAR = 0.03
COVERAGE_TAUS = (0.7, 0.8, 0.9)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _canonical_state_json(state: dict | str) -> str:
    if isinstance(state, str):
        return state
    return json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def state_key(state: dict) -> str:
    """State fingerprint — same canonical encoding the real mock uses."""
    return hashlib.sha256(_canonical_state_json(state).encode("utf-8")).hexdigest()


def build_eval_state(alert) -> dict:
    """Minimal deterministic state for the harness (embeds the fingerprint)."""
    return {
        "fingerprint": alert.fingerprint,
        "service": alert.service,
        "check": alert.check,
        "title": alert.title,
        "severity_in": alert.severity_in,
        "metric": {"value": alert.metric_value, "threshold": alert.metric_threshold,
                   "breach_s": alert.breach_duration_s},
        "labels": alert.labels,
        "history_hint": (alert.raw or {}).get("history_hint", {}),
    }


def script_response(alert, label, rng: random.Random, label_noise: float = 0.0) -> DecisionResponse:
    a = script_answers(alert, label, rng, label_noise=label_noise)
    return DecisionResponse(
        model="jev-mock-1.0",
        answers={
            "severity": Answer(qid="severity", qtype="choice", choice=a["q1_choice"],
                               noul=None, probabilities=a["q1_probs"], confidence=a["q1_conf"]),
            "owning_team": Answer(qid="owning_team", qtype="choice", choice=a["q2_choice"],
                                  noul=None, probabilities=a["q2_probs"],
                                  confidence=round(max(a["q2_probs"].values()), 2)),
            "disposition": Answer(qid="disposition", qtype="choice", choice=a["q3_choice"],
                                  noul=None, probabilities={}, confidence=a["q3_conf"]),
        },
        input_tokens=850,
    )


class FlipMock(MockSystemOneClient):
    """MockSystemOneClient with Jev-like non-determinism injected.

    With probability ``flip_rate`` per answer, the returned choice flips to
    the second-highest-probability option (probabilities/confidence kept —
    this is what a group-calibrated but non-deterministic model looks like).
    ``flip_count`` counts flipped answers for the probe denominator.
    """

    def __init__(self, script=None, flip_rate: float = 0.0, rng: random.Random | None = None):
        super().__init__(script=script)
        self.flip_rate = flip_rate
        self.rng = rng or random.Random()
        self.flip_count = 0

    def decide(self, state, questions):
        resp = super().decide(state, questions)
        if self.flip_rate <= 0:
            return resp
        new_answers = {}
        for qid, ans in resp.answers.items():
            if ans.probabilities and self.rng.random() < self.flip_rate:
                ranked = sorted(ans.probabilities, key=lambda k: ans.probabilities[k], reverse=True)
                if len(ranked) >= 2 and ranked[0] == ans.choice:
                    self.flip_count += 1
                    ans = Answer(qid=ans.qid, qtype=ans.qtype, choice=ranked[1],
                                 noul=ans.noul, probabilities=ans.probabilities,
                                 confidence=ans.confidence)
            new_answers[qid] = ans
        return DecisionResponse(model=resp.model, answers=new_answers,
                                input_tokens=resp.input_tokens)


class OrderShuffleClient:
    """Wraps a client, permuting each question's criteria option order."""

    def __init__(self, inner, rng: random.Random):
        self.inner = inner
        self.rng = rng

    def decide(self, state, questions):
        shuffled = {}
        for qid, q in questions.items():
            q2 = dict(q)
            crit = q.get("criteria")
            if isinstance(crit, dict):
                items = list(crit.items())
                self.rng.shuffle(items)
                q2["criteria"] = dict(items)
            shuffled[qid] = q2
        return self.inner.decide(state, shuffled)


class _ListAudit:
    """In-memory audit stub exercising the gate's record path."""

    def __init__(self):
        self.rows = []

    def record(self, rec) -> int:
        self.rows.append(rec)
        return len(self.rows)


# ---------------------------------------------------------------------------
# Batch evaluation (pure-ish; unit-tested on hand-built fixtures)
# ---------------------------------------------------------------------------

def run_batch(pairs: list[tuple], *, seed: int = 7, flip_rate: float = 0.0,
              label_noise: float = 0.0, thresholds: Thresholds | None = None,
              allowlist: set[str] | None = None,
              freshness_monitor=None) -> list[dict]:
    """Evaluate (alert, label) pairs through the Gate; return per-alert rows.

    freshness_monitor: optional freshness.FreshnessMonitor. The harness
    evaluates the gate *as configured* — D1 wires suppress_precondition
    into the live kernel, so fixtures that expect suppress must supply
    the same evidence production does (a V1 bundle covering the
    allowlisted fingerprints).
    """
    rng = random.Random(seed + 1)
    thresholds = thresholds or Thresholds()
    if allowlist is None:
        # ADR-013: bare fingerprints no longer suppress (M-1 fix). The harness
        # builds dual-attested entries for allowlist candidates so the
        # "known noise -> suppress" fixtures exercise the attestation path.
        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        allowlist = [
            AllowlistEntry(
                fingerprint=a.fingerprint, author="harness",
                attestations=[
                    Attestation("alice", now - timedelta(days=1), "lrq-9f2c-41ab", 30),
                    Attestation("bob", now - timedelta(days=1), "lrq-9f2c-41ab", 30),
                ])
            for a, lab in pairs if lab.get("allowlist_candidate")
        ]

    script = {}
    states = {}
    for alert, label in pairs:
        state = build_eval_state(alert)
        states[alert.alert_id] = state
        script[state_key(state)] = script_response(
            alert, label, rng, label_noise=label_noise)

    client = FlipMock(script=script, flip_rate=flip_rate, rng=random.Random(seed + 2))
    gate = Gate(client=client, thresholds=thresholds, allowlist=allowlist,
                audit=_ListAudit(), shadow=False,
                freshness_monitor=freshness_monitor)

    rows = []
    for alert, label in pairs:
        disp, rec = gate.evaluate(alert, states[alert.alert_id], {}, {})
        rows.append({"alert": alert, "label": label, "disposition": disp, "record": rec})
    return rows


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def accuracy(rows: list[dict]) -> dict[str, float]:
    n = len(rows)
    sev = sum(1 for r in rows
              if r["record"].q_severity is not None
              and r["record"].q_severity.choice == r["label"]["severity_true"])
    team = sum(1 for r in rows
               if r["record"].q_team is not None
               and r["record"].q_team.choice == r["label"]["team_true"])
    disp = sum(1 for r in rows
               if r["disposition"].action == r["label"]["disposition_true"])
    return {"severity": sev / n, "team": team / n, "disposition": disp / n, "n": n}


def ece(pairs: list[tuple[float, bool]], n_bins: int = 10) -> tuple[float, list]:
    """Expected calibration error. pairs = [(confidence, correct), ...]."""
    bins: list[list] = [[] for _ in range(n_bins)]
    for conf, correct in pairs:
        idx = min(int(conf * n_bins), n_bins - 1)
        bins[idx].append((conf, correct))
    total = len(pairs)
    err = 0.0
    table = []
    for i, b in enumerate(bins):
        if not b:
            table.append({"bin": f"[{i/10:.1f},{(i+1)/10:.1f})", "n": 0,
                          "acc": None, "mean_conf": None})
            continue
        acc = sum(1 for _, c in b if c) / len(b)
        mean_conf = sum(c for c, _ in b) / len(b)
        err += (len(b) / total) * abs(acc - mean_conf)
        table.append({"bin": f"[{i/10:.1f},{(i+1)/10:.1f})", "n": len(b),
                      "acc": round(acc, 4), "mean_conf": round(mean_conf, 4)})
    return err, table


def coverage_at(confs: list[float], taus=COVERAGE_TAUS) -> dict[float, float]:
    n = len(confs)
    return {t: sum(1 for c in confs if c >= t) / n for t in taus}


def false_suppress_rate(rows: list[dict]) -> dict:
    sev1 = [r for r in rows if r["label"]["became_sev12"] == 1]
    bad = [r for r in sev1 if r["disposition"].action == "suppress"]
    return {"n_sev1": len(sev1), "n_false_suppress": len(bad),
            "rate": (len(bad) / len(sev1)) if sev1 else 0.0}


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------

def flip_probe(pairs: list[tuple], *, seed: int, flip_rate: float,
               n: int = 200, repeats: int = 100,
               thresholds: Thresholds | None = None) -> dict:
    """Repeat the same alerts; measure choice flips and disposition changes."""
    sample = pairs[:n]
    thresholds = thresholds or Thresholds()
    allowlist = {a.fingerprint for a, lab in sample if lab.get("allowlist_candidate")}

    base = run_batch(sample, seed=seed, flip_rate=0.0, thresholds=thresholds,
                     allowlist=allowlist)
    base_disp = {r["alert"].alert_id: r["disposition"].action for r in base}
    base_choices = {r["alert"].alert_id: tuple(
        (r["record"].q_severity.choice if r["record"].q_severity else None,
         r["record"].q_team.choice if r["record"].q_team else None,
         r["record"].q_disposition.choice if r["record"].q_disposition else None))
        for r in base}

    disp_changes = 0
    total = 0
    for rep in range(repeats):
        rep_rows = run_batch(sample, seed=seed * 100003 + rep, flip_rate=flip_rate,
                             thresholds=thresholds, allowlist=allowlist)
        for r in rep_rows:
            aid = r["alert"].alert_id
            total += 1
            if r["disposition"].action != base_disp[aid]:
                disp_changes += 1
    # Per-answer choice flip rate, measured on one representative repeat.
    rep1 = run_batch(sample, seed=seed * 100003, flip_rate=flip_rate,
                     thresholds=thresholds, allowlist=allowlist)
    choice_flips = sum(
        1 for r in rep1
        for got, want in zip(
            (r["record"].q_severity.choice if r["record"].q_severity else None,
             r["record"].q_team.choice if r["record"].q_team else None,
             r["record"].q_disposition.choice if r["record"].q_disposition else None),
            base_choices[r["alert"].alert_id])
        if got != want)
    choice_flip_rate = choice_flips / (len(rep1) * 3)
    disp_flip_rate = disp_changes / total if total else 0.0
    return {"n": len(sample), "repeats": repeats,
            "choice_flip_rate": choice_flip_rate,
            "disposition_flip_rate": disp_flip_rate,
            "bar": FLIP_BAR, "pass": disp_flip_rate < FLIP_BAR}


def shuffle_probe(pairs: list[tuple], *, seed: int, n: int = 200,
                  thresholds: Thresholds | None = None) -> dict:
    """Permute option order; the scripted mock is order-insensitive by
    construction, so this probes the harness plumbing, not Jev."""
    sample = pairs[:n]
    thresholds = thresholds or Thresholds()
    allowlist = {a.fingerprint for a, lab in sample if lab.get("allowlist_candidate")}
    rng = random.Random(seed + 3)

    script, states = {}, {}
    for alert, label in sample:
        state = build_eval_state(alert)
        states[alert.alert_id] = state
        script[state_key(state)] = script_response(alert, label, rng)

    inner = FlipMock(script=script, flip_rate=0.0)
    gate_plain = Gate(client=inner, thresholds=thresholds, allowlist=allowlist,
                      audit=_ListAudit(), shadow=False)
    gate_shuf = Gate(client=OrderShuffleClient(inner, random.Random(seed + 4)),
                     thresholds=thresholds, allowlist=allowlist,
                     audit=_ListAudit(), shadow=False)
    changed = 0
    for alert, _ in sample:
        d1, _ = gate_plain.evaluate(alert, states[alert.alert_id], {}, {})
        d2, _ = gate_shuf.evaluate(alert, states[alert.alert_id], {}, {})
        if d1.action != d2.action:
            changed += 1
    rate = changed / len(sample) if sample else 0.0
    return {"n": len(sample), "changed": changed, "change_rate": rate,
            "bar": SHUFFLE_BAR, "pass": rate < SHUFFLE_BAR}


# ---------------------------------------------------------------------------
# Full run + report
# ---------------------------------------------------------------------------

def run_eval(n: int = 2000, seed: int = 7, flip_rate: float = 0.02,
             label_noise: float = 0.0, out_path: str = "calibration-report.md",
             flip_n: int = 200, flip_repeats: int = 100,
             shuffle_n: int = 200) -> dict:
    pairs = generate_alerts(n, seed)
    rows = run_batch(pairs, seed=seed, flip_rate=flip_rate, label_noise=label_noise)

    acc = accuracy(rows)
    q1_pairs = [(r["record"].q_severity.confidence, r["record"].q_severity.choice == r["label"]["severity_true"])
                for r in rows if r["record"].q_severity and r["record"].q_severity.confidence is not None]
    q3_pairs = [(r["record"].q_disposition.confidence, r["record"].q_disposition.choice == r["label"]["disposition_true"])
                for r in rows if r["record"].q_disposition and r["record"].q_disposition.confidence is not None]
    ece_q1, ece_q1_table = ece(q1_pairs)
    ece_q3, ece_q3_table = ece(q3_pairs)
    cov_q1 = coverage_at([c for c, _ in q1_pairs])
    cov_q3 = coverage_at([c for c, _ in q3_pairs])
    fs = false_suppress_rate(rows)
    flip = flip_probe(pairs, seed=seed, flip_rate=flip_rate, n=min(flip_n, n),
                      repeats=flip_repeats)
    shuf = shuffle_probe(pairs, seed=seed, n=min(shuffle_n, n))

    metrics = {
        "n": n, "seed": seed, "flip_rate_injected": flip_rate,
        "label_noise": label_noise, "using_shim_gate": _USING_SHIM_GATE,
        "accuracy": acc, "ece_q1": ece_q1, "ece_q3": ece_q3,
        "ece_q1_table": ece_q1_table, "ece_q3_table": ece_q3_table,
        "coverage_q1": cov_q1, "coverage_q3": cov_q3,
        "false_suppress": fs, "flip_probe": flip, "shuffle_probe": shuf,
    }
    write_report(metrics, out_path)
    return metrics


def _md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def write_report(m: dict, path: str) -> None:
    acc = m["accuracy"]
    fs = m["false_suppress"]
    flip = m["flip_probe"]
    shuf = m["shuffle_probe"]
    L = [
        "# Sentinel calibration report",
        "",
        f"Alerts evaluated: **{m['n']}** · seed `{m['seed']}` · "
        f"injected flip rate `{m['flip_rate_injected']}` · label noise `{m['label_noise']}` · "
        f"gate: `{'shim (§5)' if m['using_shim_gate'] else 'real gate.py'}`",
        "",
        "## Accuracy vs outcome labels",
        "",
        _md_table(["metric", "value"], [
            ["severity accuracy (Q1 choice vs severity_true)", f"{acc['severity']:.4f}"],
            ["team-routing accuracy (Q2 choice vs team_true)", f"{acc['team']:.4f}"],
            ["disposition accuracy (action vs disposition_true)", f"{acc['disposition']:.4f}"],
        ]),
        "",
        "## Calibration (ECE, 10-bin)",
        "",
        f"Q1 severity ECE: **{m['ece_q1']:.4f}** · Q3 disposition ECE: **{m['ece_q3']:.4f}**",
        "",
        _md_table(["bin", "n", "acc", "mean_conf"],
                  [[t["bin"], t["n"], t["acc"], t["mean_conf"]] for t in m["ece_q1_table"]]),
        "",
        "Q3 bins:",
        "",
        _md_table(["bin", "n", "acc", "mean_conf"],
                  [[t["bin"], t["n"], t["acc"], t["mean_conf"]] for t in m["ece_q3_table"]]),
        "",
        "## Coverage@tau (fraction with confidence >= tau)",
        "",
        _md_table(["tau", "Q1 coverage", "Q3 coverage"],
                  [[f"{t:.1f}", f"{m['coverage_q1'][t]:.4f}", f"{m['coverage_q3'][t]:.4f}"]
                   for t in COVERAGE_TAUS]),
        "",
        "## Repeatability probes",
        "",
        _md_table(["probe", "measured", "bar", "result"], [
            ["flip: per-answer choice flip rate",
             f"{flip['choice_flip_rate']:.4f}", f"< {flip['bar']}", "—"],
            ["flip: disposition change rate "
             f"({flip['n']} alerts x {flip['repeats']} repeats)",
             f"{flip['disposition_flip_rate']:.4f}", f"< {flip['bar']}",
             "PASS" if flip["pass"] else "FAIL"],
            ["option-order shuffle: disposition change rate "
             f"({shuf['n']} alerts)",
             f"{shuf['change_rate']:.4f}", f"< {shuf['bar']}",
             "PASS" if shuf["pass"] else "FAIL"],
        ]),
        "",
        "## False-suppress rate on confirmed SEV1s (trust metric)",
        "",
        f"Confirmed SEV1/2 alerts: {fs['n_sev1']} · falsely suppressed: "
        f"{fs['n_false_suppress']} · rate **{fs['rate']:.4f}**",
        "",
        "## HONEST LIMITATIONS",
        "",
        "1. **Synthetic data proves plumbing, not production accuracy.** Every",
        "   number above was measured against seeded fixtures whose 'Jev answers'",
        "   were scripted from the labels. It validates that the gate, the",
        "   threshold policy, the audit path and the metrics all work end to end —",
        "   it says nothing about how the real Jev model will score on customer",
        "   alerts.",
        "2. **Real calibration needs 50–300 customer labels** (research finding).",
        "   Ship week 1–2 in shadow mode, collect the label join, then re-run",
        "   this harness against the customer's own history before trusting any",
        "   ECE or coverage number.",
        "3. Flip injection is a crude stand-in for Jev's measured 1.3–2.2%",
        "   non-determinism: it flips choices, not the underlying probability",
        "   mass, and it cannot reproduce correlated failure modes.",
        "4. The option-order shuffle probe is vacuous against the scripted mock",
        "   (the mock keys answers off state, not option order). Re-run it",
        "   against live Jev before claiming order-robustness.",
        "5. Threshold defaults (C_FP=$100, C_FN=$50,000) are founder-grade",
        "   estimates until the tuner is run on the customer's shadow data.",
        "",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print(f"wrote calibration report -> {path}")


_AB_REPORT_DEFAULT = "research/jev-behavior/ab-2026-10-04.md"


def _ab_report_path(args) -> str:
    # The --ab passthrough is ALWAYS a dry-run (mock answers). It must never
    # silently overwrite the committed live report with mock data: with the
    # default path and an existing file, refuse loudly. An explicit
    # --ab-report path is the operator's own choice.
    if args.ab_report == _AB_REPORT_DEFAULT and os.path.exists(args.ab_report):
        raise SystemExit(
            f"refusing to overwrite existing report {args.ab_report} with "
            "dry-run output (this is the committed live A/B report); pass an "
            "explicit --ab-report path for dry-run output")
    return args.ab_report


def _run_ab_passthrough(args) -> None:
    """--ab: delegate to the question-variant A/B harness (sentinel.ab).

    Dry-run only (scripted mock, no credentials). Live runs go through
    research/jev-behavior/bin/ab_run.py --live, which shares this core.
    """
    import os
    from datetime import datetime, timezone
    from sentinel import ab as _ab

    stems = [s.strip() for s in args.ab_arms.split(",") if s.strip()]
    if len(stems) < 3:
        raise SystemExit("--ab-arms needs at least 3 variant stems (A1,A2,B)")
    vdir = os.path.join("research", "jev-behavior", "variants")
    arms = [_ab.load_variant(os.path.join(vdir, s + ".json")) for s in stems]
    _ab.assert_variant_effective(arms[0], arms[2])  # K4
    items = _ab.build_evalset(args.ab_n, args.seed)
    script, srng = {}, random.Random(args.seed + 1)
    for it in items:
        script[state_key(it.state)] = script_response(
            it.alert, it.label, srng, label_noise=0.0)

    def factory(variant, arm_name):
        return FlipMock(script=script, flip_rate=0.02,
                        rng=random.Random(
                            _ab.variant_rng_seed(
                                args.seed, arm_name + "|" + variant.ref)))

    results, meta = _ab.run_ab(items, arms, factory, seed=args.seed)
    meta["n_alerts_requested"] = args.ab_n
    meta["seed"] = args.seed
    metrics = _ab.paired_metrics(results)  # A1/A2/B
    toks = metrics["input_tokens_per_arm"]["A1"]
    cost = _ab.cost_accounting(toks["mean"])
    conditions = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "mode": "dry-run (via evalharness --ab)",
        "conditions_text": (
            "dry-run through the evalharness --ab passthrough: scripted "
            "FlipMock answers (flip_rate=0.02, seeded per arm); variant "
            "transforms structurally applied but the mock keys answers off "
            "state fingerprints, so variant effects are zero by construction. "
            "Plumbing + statistics only. Live: bin/ab_run.py --live."),
    }
    _ab.write_ab_report(metrics, meta, cost, _ab_report_path(args), conditions)
    print(f"ab: n_paired={metrics['n_paired']} "
          f"noise_flip={metrics['noise_floor']['disposition_flip_rate']:.4f} "
          f"variant_flip={metrics['variant']['disposition_flip_rate']:.4f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sentinel calibration eval harness")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--flip-rate", type=float, default=0.02)
    ap.add_argument("--label-noise", type=float, default=0.0)
    ap.add_argument("-o", "--output", default="calibration-report.md")
    ap.add_argument("--ab", action="store_true",
                    help="run the question-variant A/B harness (sentinel.ab, "
                         "dry-run) instead of the calibration eval")
    ap.add_argument("--ab-arms", default="control-v1,control-v1,q123-shuffle-v1",
                    help="comma-separated variant file stems (A1,A2,B)")
    ap.add_argument("--ab-n", type=int, default=200)
    ap.add_argument("--ab-report",
                    default=_AB_REPORT_DEFAULT)
    args = ap.parse_args()
    if args.ab:
        _run_ab_passthrough(args)
        return
    m = run_eval(n=args.n, seed=args.seed, flip_rate=args.flip_rate,
                 label_noise=args.label_noise, out_path=args.output)
    print(f"severity_acc={m['accuracy']['severity']:.4f} "
          f"team_acc={m['accuracy']['team']:.4f} "
          f"disp_acc={m['accuracy']['disposition']:.4f} "
          f"ece_q1={m['ece_q1']:.4f} ece_q3={m['ece_q3']:.4f} "
          f"false_suppress={m['false_suppress']['rate']:.4f} "
          f"flip={'PASS' if m['flip_probe']['pass'] else 'FAIL'} "
          f"shuffle={'PASS' if m['shuffle_probe']['pass'] else 'FAIL'}")


if __name__ == "__main__":
    main()
