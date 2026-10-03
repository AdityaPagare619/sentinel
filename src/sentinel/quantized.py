"""ADR-013 — the quantized probability lock (design/fixes/04-quantized-gate.md).

The bug this fixes (M-1): Jev's wire format quantizes probabilities to 0.01,
but the old leg compared the *reported* value against the continuous bar
``reported_p1 < 0.002``. A true p=0.0049 (2.45x the bar) reports as 0.00 and
wrongly passed the leg — net expected loss $145 per such suppression.

The re-derived leg (§2.1), implemented here as pure, versioned functions:

    suppress on probability  <=>  hundredths(reported_p1) == 0
                                AND (valid_fit_with_p_hat_upper < 0.002
                                     OR dual_human_attestation)

Nothing in this module is tunable at call time: the bar, the Wilson z, the
validity clocks and the minimum-N tripwire live in module constants
(PM-4 — changing them requires an ADR, not a config edit).

Rounding-mode unidentifiability (§1.4, D3): the vendor's rounding mode is
undocumented and unidentifiable from reports alone, so this module never
infers it. Pre-fit, the reported-0.00 class is treated under the most
conservative plausible mode (truncation: true p in [0, 0.01)) — which is why
the interim requires TWO humans. Post-fit, the empirical bound over the
reported-0.00 class is mode-agnostic by construction.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------------------
# Versioned contract constants (Type 1 — ADR-grade; see §5 of the design).
# ---------------------------------------------------------------------------

GATE_FORMULA_VERSION = "adr013-v1"   # aggregation formula id; a fit is bound
                                     # to the formula that produced it
REFERENCE_CLASS = "R(C,W,r=0.00)"    # the only gate-relevant quantized class
SUPPRESS_BAR = 0.002                 # continuous bar from the cost model
                                     # (DR-4); the implementation compares in
                                     # quantized space + the fit's bound
WILSON_Z = 1.6449                    # one-sided 95%, pinned in the artifact
FIT_WINDOW_DAYS = 90                 # trailing window W of the fit
FIT_VALID_DAYS = 30                  # re-validation clock (ADR-022)
FIT_WINDOW_FRESH_DAYS = 7            # window_end must be within this of now
ATTESTATION_TTL_MAX_DAYS = 30        # attestation freshness (ADR-014)
UNLABELED_FRACTION_FLAG = 0.20       # m/(n+m) above this flags the fit
TUNER_VERSION = "0.1.0"

# Minimum labeled n for p_hat_upper < 0.002 at k=0 (one-sided 95% Wilson).
# Derived, not chosen: with k=0 the bound is z^2/(n+z^2) < 0.002  =>
# n > z^2*(1/0.002 - 1) = 1350.14...  =>  first clearing integer is 1351.
# At n=1350 the bound is 0.0020002104 (fails); at n=1351 it is
# 0.0019987328 (clears). This boundary is the PM-3 tripwire.
MIN_N_K0 = 1351

# Decision-calibration certificate (§3): floors and acceptance bands.
CERT_MIN_N = 300                     # min labeled suppress decisions
CERT_MIN_CONFIRMED_SEV1 = 25         # min confirmed SEV1s in window (all sev)
CERT_PASS_U = 0.005                  # 2.5x the design bar (quantization margin)
CERT_FAIL_U = 0.01


class QuantizedValueError(ValueError):
    """A wire value that is not an exact integer-hundredths report."""


class FitIntegrityError(Exception):
    """A fit artifact failed its content-hash binding check."""


# ---------------------------------------------------------------------------
# Step 1 — parse to integer hundredths (never float)
# ---------------------------------------------------------------------------

def parse_hundredths(value) -> int:
    """Parse a wire probability to integer hundredths.

    Accepts str / Decimal / int / float. The value must be *exactly*
    representable as integer hundredths — a non-hundredths value (0.005,
    0.0049) is not a valid wire report and raises QuantizedValueError, so
    the leg fails closed instead of being compared as a float.
    """
    if value is None:
        raise QuantizedValueError("None is not a wire probability")
    if isinstance(value, bool):
        raise QuantizedValueError("bool is not a wire probability")
    try:
        if isinstance(value, float):
            if not math.isfinite(value):
                raise QuantizedValueError(f"non-finite wire value: {value!r}")
            d = Decimal(repr(value))  # shortest round-trip: 0.01 -> '0.01'
        elif isinstance(value, Decimal):
            d = value
        elif isinstance(value, int):
            d = Decimal(value)
        elif isinstance(value, str):
            d = Decimal(value.strip())
        else:
            raise QuantizedValueError(f"unsupported wire type: {type(value)}")
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise QuantizedValueError(f"unparseable wire value: {value!r}") from exc
    if d.is_nan():
        raise QuantizedValueError(f"NaN wire value: {value!r}")
    hundredths = d * 100
    if hundredths != hundredths.to_integral_value():
        raise QuantizedValueError(
            f"wire value {value!r} is not an exact integer-hundredths report")
    h = int(hundredths)
    if not 0 <= h <= 100:
        raise QuantizedValueError(f"wire value {value!r} outside [0, 1]")
    return h


# ---------------------------------------------------------------------------
# The Wilson bound — one versioned pure function (PM-3)
# ---------------------------------------------------------------------------

def wilson_upper_onesided(k: int, n: int, z: float = WILSON_Z) -> float:
    """One-sided `z` upper confidence bound (Wilson score) for a rate.

    The SAME construction feeds the tuner's fit (§2.2) and the decision-
    calibration certificate (§3.2), so the fit and the certificate are
    directly comparable. Do NOT substitute the two-sided interval's upper
    end here — that spends the wrong error budget (PM-3).
    """
    if not isinstance(k, int) or not isinstance(n, int):
        raise ValueError("k and n must be integers")
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= k <= n:
        raise ValueError("k must satisfy 0 <= k <= n")
    p_hat = k / n
    z2 = z * z
    center = p_hat + z2 / (2 * n)
    radius = z * math.sqrt(p_hat * (1 - p_hat) / n + z2 / (4 * n * n))
    return (center + radius) / (1 + z2 / n)


def min_n_to_clear(k: int, bar: float = SUPPRESS_BAR,
                   z: float = WILSON_Z) -> int:
    """Smallest labeled n with wilson_upper_onesided(k, n) < bar.

    For k=0 this is the 1350/1351 tripwire: min_n_to_clear(0) == 1351.
    (bar/z default to the contract constants; they are parameters here so
    the *derivation* is testable, not so callers may retune the gate —
    leg1_prob_lock and fit_is_valid accept no such parameters, PM-4.)
    """
    n = max(k, 1)
    while wilson_upper_onesided(k, n, z) >= bar:
        n += 1
        if n > 10_000_000:
            raise ValueError("bar unreachable")
    return n


# ---------------------------------------------------------------------------
# Fit artifact (versioned; the gate validates, never trusts) — §2.4
# ---------------------------------------------------------------------------

_FIT_PAYLOAD_FIELDS = (
    "org", "reference_class", "window_start", "window_end",
    "n", "k", "m", "p_hat_upper", "z", "model_pin",
    "gate_formula_version", "computed_at", "valid_until",
    "shift_status", "tuner_version",
)


@dataclass
class FitArtifact:
    fit_id: str            # content hash of the payload (bound by .bind())
    org: str
    reference_class: str   # gate asserts exact match with REFERENCE_CLASS
    window_start: str      # ISO-8601; trailing FIT_WINDOW_DAYS window
    window_end: str        # ISO-8601; must be within 7d of now (§2.2.4)
    n: int                 # labeled reported-0.00 alerts in window
    k: int                 # ... adjudicated true-SEV1 among them
    m: int                 # unlabeled-excluded count (never folded into n)
    p_hat_upper: float     # one-sided 95% Wilson upper(k, n)
    z: float               # pinned: 1.6449
    model_pin: str         # exact Jev version string (ADR-015)
    gate_formula_version: str
    computed_at: str       # ISO-8601
    valid_until: str       # ISO-8601 = computed_at + 30d
    shift_status: str      # "OK" | "TRIPPED"
    tuner_version: str

    def payload_bytes(self) -> bytes:
        payload = {f: getattr(self, f) for f in _FIT_PAYLOAD_FIELDS}
        return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          default=str).encode("utf-8")

    def content_hash(self) -> str:
        return hashlib.sha256(self.payload_bytes()).hexdigest()

    def bind(self) -> "FitArtifact":
        """Return a copy with fit_id set to the payload's content hash."""
        import dataclasses
        return dataclasses.replace(self, fit_id=self.content_hash())

    def verify_binding(self) -> bool:
        return bool(self.fit_id) and self.fit_id == self.content_hash()


class FitStore:
    """Versioned tuner artifacts (off the hot path).

    Keyed by (org, reference_class). put() refuses artifacts whose fit_id
    does not match their content hash; get() re-verifies the hash on read —
    a fit tampered with in place fails loudly instead of steering the gate.
    """

    def __init__(self):
        self._artifacts: dict[tuple[str, str], FitArtifact] = {}

    def put(self, artifact: FitArtifact) -> None:
        if not artifact.verify_binding():
            raise FitIntegrityError(
                f"fit_id/content-hash mismatch for org={artifact.org} "
                f"class={artifact.reference_class}")
        self._artifacts[(artifact.org, artifact.reference_class)] = artifact

    def get(self, org: str,
            reference_class: str = REFERENCE_CLASS) -> "FitArtifact | None":
        art = self._artifacts.get((org, reference_class))
        if art is None:
            return None
        if not art.verify_binding():
            raise FitIntegrityError(
                f"stored fit failed content-hash re-verification: "
                f"org={org} class={reference_class}")
        return art


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fit_is_valid(artifact: FitArtifact, *, pinned_model: str,
                 now: datetime) -> tuple[bool, str]:
    """All four validity rules (§2.2) + binding + schema assertion.

    Returns (True, "ok") or (False, reason). A fit failing any rule is
    *stale*: the gate treats it exactly as no fit (Step 4), never as
    last-good, never degraded to the point estimate.
    """
    if not artifact.verify_binding():
        return False, "fit_id content-hash binding failed"
    if artifact.reference_class != REFERENCE_CLASS:
        return False, (f"reference_class mismatch: {artifact.reference_class!r} "
                       f"!= {REFERENCE_CLASS!r}")
    if artifact.gate_formula_version != GATE_FORMULA_VERSION:
        return False, (f"gate_formula_version mismatch: "
                       f"{artifact.gate_formula_version!r}")
    if artifact.z != WILSON_Z:
        return False, f"z={artifact.z!r} is not the pinned {WILSON_Z}"
    if _parse_ts(artifact.valid_until) <= now:
        return False, "valid_until elapsed (30-day re-validation clock)"
    if artifact.model_pin != pinned_model:
        return False, (f"model_pin mismatch: {artifact.model_pin!r} "
                       f"!= {pinned_model!r}")
    if artifact.shift_status != "OK":
        return False, f"shift_status={artifact.shift_status!r}"
    window_end = _parse_ts(artifact.window_end)
    if window_end < now - timedelta(days=FIT_WINDOW_FRESH_DAYS):
        return False, "trailing window not fresh (window_end > 7d old)"
    if artifact.p_hat_upper >= SUPPRESS_BAR:
        return False, (f"p_hat_upper={artifact.p_hat_upper:.7f} "
                       f">= bar {SUPPRESS_BAR}")
    return True, "ok"


# ---------------------------------------------------------------------------
# Dual human attestation — the honest interim (§2.3)
# ---------------------------------------------------------------------------

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{1,127}$")


def is_linkage_run_id(ref) -> bool:
    """evidence_ref must be a linkage-query *run id* (a verifiable artifact),
    not free text ("see #ops-chat") — PM-2."""
    return isinstance(ref, str) and bool(_RUN_ID_RE.match(ref))


@dataclass
class Attestation:
    attestor_id: str
    attested_at: datetime      # timezone-aware
    evidence_ref: str          # run id of the 30d zero-SEV1 linkage query
    ttl_days: int              # <= ATTESTATION_TTL_MAX_DAYS


@dataclass
class AllowlistEntry:
    fingerprint: str           # env+cluster namespaced (ADR-017)
    author: str                # entry author; attestors must differ from this
    attestations: list = field(default_factory=list)


def dual_attestation_valid(entry: AllowlistEntry,
                           now: datetime) -> tuple[bool, str]:
    """The interim replaces the lock; it never approximates it.

    Requires two attestation tuples with: distinct attestor_ids, both
    distinct from the entry author (enforced in code, not policy), each
    carrying a run-id-shaped evidence_ref, each within its TTL
    (TTL <= 30 days). Post-fit the allowlist leg reverts to the standard
    single tuple — the two regimes never mix.
    """
    atts = list(entry.attestations or [])
    if len(atts) < 2:
        return False, "fewer than two attestations"
    a1, a2 = atts[0], atts[1]
    if a1.attestor_id == a2.attestor_id:
        return False, "attestors must be distinct (attestation theater)"
    for a in (a1, a2):
        if a.attestor_id == entry.author:
            return False, "attestor must be distinct from the entry author"
        if not is_linkage_run_id(a.evidence_ref):
            return False, (f"evidence_ref {a.evidence_ref!r} is not a "
                           f"linkage-query run id")
        if not 0 < a.ttl_days <= ATTESTATION_TTL_MAX_DAYS:
            return False, f"ttl_days={a.ttl_days} outside (0, 30]"
        attested_at = a.attested_at
        if attested_at.tzinfo is None:
            attested_at = attested_at.replace(tzinfo=timezone.utc)
        if attested_at > now:
            return False, "attestation from the future"
        if attested_at + timedelta(days=a.ttl_days) <= now:
            return False, f"attestation by {a.attestor_id} expired"
    return True, "ok"


# ---------------------------------------------------------------------------
# §2.1 — the leg-1 decision procedure
# ---------------------------------------------------------------------------

def leg1_prob_lock(reported_p1, *, org: str, now: datetime,
                   fit_store: "FitStore | None" = None,
                   pinned_model: str | None = None,
                   entry: "AllowlistEntry | None" = None) -> tuple[bool, dict]:
    """Evaluate the probability lock. Returns (leg1, detail).

    Steps (design §2.1):
      1. parse to integer hundredths (never float);
      2. point condition: hundredths != 0 -> FALSE;
      3. fit path: valid fit with p_hat_upper < 0.002 -> TRUE;
      4. interim: dual attestation on the allowlist entry -> TRUE;
      5. default FALSE.
    Never raises: any internal failure fails the leg closed (the gate's
    outer fail-open still pages on true errors — this leg just doesn't
    grant suppression).
    """
    detail: dict = {}
    try:
        hundredths = parse_hundredths(reported_p1)
    except QuantizedValueError as exc:
        return False, {"step": "parse", "ok": False, "reason": str(exc)}
    detail["hundredths"] = hundredths

    # STEP 2 — exact equality on the quantized domain. This *is* the bar's
    # implementation in quantized space; the continuous 0.002 lives only in
    # the cost model and in the fit's bound. `reported < 0.002` here would be
    # the original bug wearing a new coat.
    if hundredths != 0:
        return False, {"step": "point-condition", "ok": False,
                       "hundredths": hundredths}

    # STEP 3 — the fit path. The < 0.002 compares the fit's UPPER CONFIDENCE
    # BOUND, never a point estimate (fit_is_valid enforces it).
    if fit_store is not None and org and pinned_model:
        try:
            fit = fit_store.get(org)
        except Exception as exc:  # corrupt store -> as if no fit (Step 4)
            return False, {"step": "fit", "ok": False,
                           "reason": f"fit store error: {exc}"}
        if fit is not None:
            valid, reason = fit_is_valid(fit, pinned_model=pinned_model,
                                         now=now)
            detail["fit"] = {"fit_id": fit.fit_id, "valid": valid,
                             "reason": reason, "p_hat_upper": fit.p_hat_upper,
                             "n": fit.n, "k": fit.k}
            if valid:
                return True, {"step": "fit", "ok": True, **detail["fit"]}
        else:
            detail["fit"] = {"valid": False, "reason": "no fit for org/class"}

    # STEP 4 — the honest interim (never an approximation).
    if entry is not None:
        valid, reason = dual_attestation_valid(entry, now)
        detail["attestation"] = {"valid": valid, "reason": reason}
        if valid:
            return True, {"step": "attestation", "ok": True,
                          **detail["attestation"]}

    # STEP 5 — default.
    return False, {"step": "default", "ok": False, **detail}


# ---------------------------------------------------------------------------
# Shift monitor — class-conditional, feeding shift_status (§2.4, D11)
# ---------------------------------------------------------------------------

class ShiftMonitor:
    """Watches the r=0.00 class *conditionally*, not pooled (PM-1).

    Two trip rules:
      * D11 (Type 1): ANY confirmed SEV1 among reported-0.00 alerts trips
        the monitor immediately — a confirmed false member of the "safe"
        class is the strongest possible shift signal.
      * Cycle check (Type 2): each observed batch is checked two ways —
        (a) the batch's one-sided 95% Wilson *lower* bound on the SEV1 rate
        exceeds the fit's certified p_hat_upper (the class got worse with
        95% confidence), and (b) the PSI distance of the batch's
        alert-feature distribution vs the fit window exceeds the
        pre-registered 0.25 threshold (composition shift *within* the
        class, which a pooled monitor misses — PM-1).

    Thresholds are pre-registered module constants, not per-call arguments.
    """

    PSI_TRIP = 0.25
    _EPS = 1e-9

    def __init__(self, reference_n: int, reference_k: int,
                 reference_features: dict[str, int] | None = None):
        self.reference_n = reference_n
        self.reference_k = reference_k
        self.reference_features = dict(reference_features or {})
        self.reference_upper = wilson_upper_onesided(reference_k, reference_n)
        self._tripped = False
        self._trip_reason = ""

    @property
    def status(self) -> str:
        return "TRIPPED" if self._tripped else "OK"

    @property
    def trip_reason(self) -> str:
        return self._trip_reason

    def _trip(self, reason: str) -> None:
        self._tripped = True
        self._trip_reason = reason

    def note_outcome(self, sev1: bool) -> None:
        """Record one adjudicated outcome in the r=0.00 class."""
        if self._tripped:
            return
        if sev1:
            # D11: immediate trip, not at the weekly cadence.
            self._trip("confirmed SEV1 among reported-0.00 alerts")

    def note_batch(self, features: list[str], labels: list[int]) -> None:
        """End-of-cycle check over one batch of class observations.

        features: per-alert feature bucket (e.g. service name) for the PSI
        composition check. labels: adjudicated 1/0 in the same order.
        """
        if self._tripped:
            return
        n = len(labels)
        if n == 0:
            return
        k = sum(1 for l in labels if l == 1)
        if k > 0:
            # Any confirmed SEV1 trips immediately (D11) — including inside
            # a batch. The rate check below is for drift the labels alone
            # have not yet confirmed.
            self._trip(f"confirmed SEV1 in batch ({k}/{n})")
            return
        if n >= 30:
            lower = self._wilson_lower(k, n)
            if lower > self.reference_upper:
                self._trip(
                    f"class SEV1-rate lower bound {lower:.6f} exceeds "
                    f"fit-certified upper {self.reference_upper:.6f}")
                return
        psi = self._psi(features)
        if psi is not None and psi > self.PSI_TRIP:
            self._trip(f"composition shift within r=0.00 class (PSI={psi:.3f})")

    @staticmethod
    def _wilson_lower(k: int, n: int, z: float = WILSON_Z) -> float:
        p_hat = k / n
        z2 = z * z
        center = p_hat + z2 / (2 * n)
        radius = z * math.sqrt(p_hat * (1 - p_hat) / n + z2 / (4 * n * n))
        return max(0.0, (center - radius) / (1 + z2 / n))

    def _psi(self, features: list[str]) -> "float | None":
        if not self.reference_features or not features:
            return None
        ref_total = sum(self.reference_features.values())
        act_counts: dict[str, int] = {}
        for f in features:
            act_counts[f] = act_counts.get(f, 0) + 1
        act_total = len(features)
        psi = 0.0
        for bucket in set(self.reference_features) | set(act_counts):
            e = self.reference_features.get(bucket, 0) / ref_total
            a = act_counts.get(bucket, 0) / act_total
            e = max(e, self._EPS)
            a = max(a, self._EPS)
            psi += (a - e) * math.log(a / e)
        return psi


# ---------------------------------------------------------------------------
# §3 — the decision-calibration certificate (one versioned pure function)
# ---------------------------------------------------------------------------

@dataclass
class Certificate:
    verdict: str            # PASS | FLAG | FAIL | INSUFFICIENT-N
    statement: str          # the priced claim, with n/k/m spelled out
    reference_class: str
    window: tuple
    model_pin: str
    gate_formula_version: str
    shift_status: str
    valid_until: str
    n: int                  # labeled suppress decisions (decision units)
    k: int                  # ... adjudicated true-SEV1
    m: int                  # unlabeled-excluded suppressions
    p_hat_upper: "float | None"
    label_rot_risk: bool    # m/(n+m) > 0.20


def decision_certificate(decisions: list,
                         *, reference_class: str, window: tuple,
                         model_pin: str, gate_formula_version: str,
                         shift_status: str, now: datetime,
                         confirmed_sev1_in_window: int) -> Certificate:
    """Decision calibration: P(true SEV1 | suppress decision), with teeth.

    decisions: list of (label, mode); label is 1 (true SEV1), 0 (not SEV1),
    or None (UNLABELED). Denominator discipline: n counts labeled suppress
    *decisions* (storm-collapsed alerts count once); unlabeled are excluded
    from n and k and reported as m.

    Bands (§3.2): PASS iff u <= 0.005 and k == 0; FAIL iff u > 0.01 or
    k >= 1; else FLAG. Floors: n >= 300 and confirmed SEV1 >= 25, else
    INSUFFICIENT-N. m/(n+m) > 0.20 marks LABEL-ROT-RISK.
    """
    labeled = [d for d in decisions if d[0] is not None]
    n = len(labeled)
    k = sum(1 for d in labeled if d[0] == 1)
    m = len(decisions) - n
    label_rot_risk = (m / (n + m) > UNLABELED_FRACTION_FLAG) if (n + m) else False
    valid_until = (now + timedelta(days=FIT_VALID_DAYS)).isoformat()

    def _cert(verdict, u):
        if u is None:
            bound_txt = "unpriced (below evidence floors)"
        else:
            bound_txt = f"<= {u:.4f} at 95% one-sided Wilson"
        statement = (
            f"P(true SEV1 | suppress) {bound_txt}, "
            f"n={n}, k={k}, unlabeled-excluded m={m}, "
            f"class={reference_class}, model={model_pin}, "
            f"formula={gate_formula_version}"
            + (" [LABEL-ROT-RISK]" if label_rot_risk else ""))
        return Certificate(
            verdict=verdict, statement=statement,
            reference_class=reference_class, window=window,
            model_pin=model_pin, gate_formula_version=gate_formula_version,
            shift_status=shift_status, valid_until=valid_until,
            n=n, k=k, m=m, p_hat_upper=u, label_rot_risk=label_rot_risk)

    if n < CERT_MIN_N or confirmed_sev1_in_window < CERT_MIN_CONFIRMED_SEV1:
        return _cert("INSUFFICIENT-N", None)
    u = wilson_upper_onesided(k, n)
    if u <= CERT_PASS_U and k == 0:
        verdict = "PASS"
    elif u > CERT_FAIL_U or k >= 1:
        verdict = "FAIL"
    else:
        verdict = "FLAG"
    return _cert(verdict, u)


# ---------------------------------------------------------------------------
# Self-check: the module's constants must satisfy the design's identities.
# ---------------------------------------------------------------------------

def _self_check() -> None:
    assert REFERENCE_CLASS == "R(C,W,r=0.00)"
    assert WILSON_Z == 1.6449
    # The tripwire identity: 1351 is the first n clearing the bar at k=0.
    assert wilson_upper_onesided(0, MIN_N_K0) < SUPPRESS_BAR
    assert wilson_upper_onesided(0, MIN_N_K0 - 1) >= SUPPRESS_BAR
    assert min_n_to_clear(0) == MIN_N_K0
    # leg1 and fit_is_valid expose no tunable bar/clock/n_min (PM-4).
    for fn in (leg1_prob_lock, fit_is_valid):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"bar", "p_bar", "threshold", "n_min", "min_n",
                             "suppress_p1_max", "valid_days", "ttl_days",
                             "window_fresh_days"}, params


_self_check()
