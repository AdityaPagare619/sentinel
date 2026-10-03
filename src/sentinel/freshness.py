"""Freshness proofs (C-1): each lock carries a proof of freshness; any stale proof ⇒ lock fails ⇒ page.

Implements Part A of ``design/fixes/07-freshness-proofs.md`` (Type 1 core
safety invariant, ADR-014). The triple lock's independence assumption was
false — quantization rot, governance decay, and fingerprint collisions rot
silently. The fix: every lock leg carries a machine-checkable proof of
freshness, evaluated at exactly two points off the hot path:

  V1 — config load (boot + every config reload). Manifest verified first,
       then all three freshness predicates evaluated against ``now``.
  V2 — periodic revalidation heartbeat (default 15 min). Catches TTL
       expiry, re-validation-clock lapse, incident-linkage drift, and
       model-pin drift between reloads.

Per-alert validation is explicitly forbidden: at triage time the gate reads
cached booleans from :class:`FreshnessReport` — O(1), no parsing, no clock
arithmetic, no I/O. The race-to-page budget is untouched.

Proofs live config-embedded and manifest-bound (``config/`` bundle), never
as sidecar files — they inherit ADR-018's schema gating, last-good
rollback, and boot self-test. The manifest hash lands in every decision
context (``config_manifest_sha256``) so postmortems can replay exactly
which proofs were live at decision time.

Interface contract for the gate lane (lane/impl-gate): this module produces
:class:`FreshnessReport`; the gate's ``lock_evaluation`` consumes it via
:meth:`FreshnessReport.is_fresh` / :meth:`FreshnessReport.entry_fresh` and
the :func:`suppress_precondition` helper. See ``docs/FRESHNESS_CONTRACT.md``.

Documented limitations (the honest-scope table): ``docs/FRESHNESS_LIMITATIONS.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Tunables (Type 2 unless noted). The *existence* of each window is Type 1.

SCHEMA_VERSION = 1

FIT_VALIDITY_WINDOW_DAYS_DEFAULT = 90      # lock 1: calibration fit TTL
ALLOWLIST_TTL_DAYS_DEFAULT = 180           # lock 3: per-entry attestation TTL
REVALIDATION_DAYS_DEFAULT = 30            # lock 2: governance re-validation clock (Type 1)
CONF_FLOOR = 0.85                          # lock 2: attestations below this are invalid (Type 1)
HEARTBEAT_SECONDS_DEFAULT = 15 * 60        # V2 revalidation cadence
FIT_REFRESH_SLA_DAYS = 7                   # control-plane work item SLA once lock 1 goes stale

SECONDS_PER_DAY = 86400

# `issued_by` must name the tuner run that produced the fit, never a human.
# Convention: "<run-id>/<code-version>", both parts non-empty, no whitespace.
# This is a format gate, not a cryptographic signature (see LIMITATIONS.md).
_ISSUED_BY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")

# Lock identifiers used in FreshnessReport.locks and in page_reason strings.
LOCK1_CALIBRATION = "lock1_calibration"
LOCK2_THRESHOLD = "lock2_threshold"
LOCK3_ALLOWLIST = "lock3_allowlist"


# ---------------------------------------------------------------------------
# Canonical JSON pinning.
#
# config_hash (lock 2) is the sha256 of the *parsed* thresholds.json object
# serialized canonically: sorted keys, no insignificant whitespace. A
# whitespace-only reformat of thresholds.json therefore does NOT break the
# binding (no false staleness); only a semantic change does. This was a
# review follow-up on the design doc — the naive "hash the raw bytes"
# shape would page on every pretty-print.

def canonical_json_bytes(obj) -> bytes:
    """Deterministic serialization: sorted keys, compact separators, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def canonical_sha256(obj) -> str:
    """sha256 hex of the canonical JSON serialization of ``obj``."""
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def config_hash_for(thresholds_obj: dict) -> str:
    """The binding a ThresholdAttestation's ``config_hash`` must equal."""
    return canonical_sha256(thresholds_obj)


# ---------------------------------------------------------------------------
# Time helpers. The validator takes an injectable clock returning epoch
# seconds (tests pin it; production passes time.time). All proof timestamps
# are RFC3339 with an explicit offset; naive timestamps are rejected —
# fail closed (a proof whose time cannot be understood is not fresh).

def parse_rfc3339(s: str) -> float:
    """Parse RFC3339 to epoch seconds. Raises ValueError on any defect."""
    if not isinstance(s, str) or not s.strip():
        raise ValueError("empty timestamp")
    text = s.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"unparseable RFC3339 timestamp {s!r}: {exc}") from exc
    if dt.tzinfo is None:
        raise ValueError(f"naive timestamp {s!r}: RFC3339 requires an explicit offset")
    return dt.timestamp()


def rfc3339_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Proof shapes (design §A1). from_dict/to_dict are the config-embedded JSON
# contract; the gate never sees these directly, only the FreshnessReport.

class ProofFormatError(ValueError):
    """A proof failed structural validation — invalid ⇒ page, same as stale."""


def _req(d: dict, key: str, what: str):
    if not isinstance(d, dict) or key not in d:
        raise ProofFormatError(f"{what}: missing required field {key!r}")
    return d[key]


@dataclass
class TrainedOn:
    n: int
    reference_class: str
    label_pipeline_version: str
    history_as_of: str  # RFC3339

    @classmethod
    def from_dict(cls, d: dict) -> "TrainedOn":
        return cls(n=int(_req(d, "n", "trained_on")),
                   reference_class=str(_req(d, "reference_class", "trained_on")),
                   label_pipeline_version=str(_req(d, "label_pipeline_version", "trained_on")),
                   history_as_of=str(_req(d, "history_as_of", "trained_on")))

    def to_dict(self) -> dict:
        return {"n": self.n, "reference_class": self.reference_class,
                "label_pipeline_version": self.label_pipeline_version,
                "history_as_of": self.history_as_of}


@dataclass
class CalibrationFitProof:
    """Lock 1 — the fit is the thing that rots (quantization mapping decays
    as the org's incident distribution drifts)."""
    fit_id: str                 # content hash of the fit artifact
    fit_trained_at: str         # RFC3339 UTC
    trained_on: TrainedOn       # pedigree: n, reference class, label pipeline, history_as_of
    p_upper_measured: float     # audit only — the gate reads the fit artifact, not the proof
    validity_window_days: int = FIT_VALIDITY_WINDOW_DAYS_DEFAULT
    issued_by: str = ""         # tuner run id + code version, "<run-id>/<code-version>"
    model_pin: str = ""         # exact Jev model version the fit was trained against

    @classmethod
    def from_dict(cls, d: dict) -> "CalibrationFitProof":
        return cls(fit_id=str(_req(d, "fit_id", "CalibrationFitProof")),
                   fit_trained_at=str(_req(d, "fit_trained_at", "CalibrationFitProof")),
                   trained_on=TrainedOn.from_dict(_req(d, "trained_on", "CalibrationFitProof")),
                   p_upper_measured=float(_req(d, "p_upper_measured", "CalibrationFitProof")),
                   validity_window_days=int(d.get("validity_window_days",
                                                  FIT_VALIDITY_WINDOW_DAYS_DEFAULT)),
                   issued_by=str(d.get("issued_by", "")),
                   model_pin=str(d.get("model_pin", "")))

    def to_dict(self) -> dict:
        return {"fit_id": self.fit_id, "fit_trained_at": self.fit_trained_at,
                "trained_on": self.trained_on.to_dict(),
                "p_upper_measured": self.p_upper_measured,
                "validity_window_days": self.validity_window_days,
                "issued_by": self.issued_by, "model_pin": self.model_pin}


@dataclass
class ThresholdEvidence:
    backtest_id: str
    false_suppress_count: int
    n: int
    shadow_report_id: str

    @classmethod
    def from_dict(cls, d: dict) -> "ThresholdEvidence":
        return cls(backtest_id=str(_req(d, "backtest_id", "on_evidence")),
                   false_suppress_count=int(_req(d, "false_suppress_count", "on_evidence")),
                   n=int(_req(d, "n", "on_evidence")),
                   shadow_report_id=str(_req(d, "shadow_report_id", "on_evidence")))

    def to_dict(self) -> dict:
        return {"backtest_id": self.backtest_id,
                "false_suppress_count": self.false_suppress_count,
                "n": self.n, "shadow_report_id": self.shadow_report_id}


@dataclass
class ThresholdAttestation:
    """Lock 2 — the human-governed leg. Rot vector: governance decay
    (fatigue ratchet, unanswered re-validation clock)."""
    threshold: float            # must equal the live confidence bar
    attested_by: tuple          # two DISTINCT operator identities (ADR-022)
    attested_at: str            # RFC3339 UTC
    on_evidence: ThresholdEvidence
    revalidation_due_at: str    # 30-day clock from attested_at
    config_hash: str            # canonical-sha256 of the exact thresholds.json covered

    @classmethod
    def from_dict(cls, d: dict) -> "ThresholdAttestation":
        by = _req(d, "attested_by", "ThresholdAttestation")
        if not isinstance(by, (list, tuple)) or len(by) != 2:
            raise ProofFormatError("ThresholdAttestation: attested_by must be exactly 2 identities")
        return cls(threshold=float(_req(d, "threshold", "ThresholdAttestation")),
                   attested_by=(str(by[0]), str(by[1])),
                   attested_at=str(_req(d, "attested_at", "ThresholdAttestation")),
                   on_evidence=ThresholdEvidence.from_dict(_req(d, "on_evidence",
                                                                "ThresholdAttestation")),
                   revalidation_due_at=str(_req(d, "revalidation_due_at",
                                                "ThresholdAttestation")),
                   config_hash=str(_req(d, "config_hash", "ThresholdAttestation")))

    def to_dict(self) -> dict:
        return {"threshold": self.threshold, "attested_by": list(self.attested_by),
                "attested_at": self.attested_at, "on_evidence": self.on_evidence.to_dict(),
                "revalidation_due_at": self.revalidation_due_at,
                "config_hash": self.config_hash}


@dataclass
class AllowlistEvidence:
    occurrences: int
    incident_linkage: str  # "zero" or an incident id — any linkage voids the attestation
    observed_since: str    # RFC3339
    note: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "AllowlistEvidence":
        return cls(occurrences=int(_req(d, "occurrences", "on_evidence")),
                   incident_linkage=str(_req(d, "incident_linkage", "on_evidence")),
                   observed_since=str(_req(d, "observed_since", "on_evidence")),
                   note=str(d.get("note", "")))

    def to_dict(self) -> dict:
        return {"occurrences": self.occurrences, "incident_linkage": self.incident_linkage,
                "observed_since": self.observed_since, "note": self.note}


@dataclass
class AllowlistAttestation:
    """Lock 3 — per-entry. Rot vectors: silent staleness (TTL), incident
    linkage (poisoned fingerprint), owning-service rewrite (drift)."""
    fingerprint: str       # env:cluster:check_name:signature (ADR-017 namespace, never env-blind)
    attested_by: tuple     # two distinct identities, permanently
    attested_at: str       # RFC3339 UTC
    on_evidence: AllowlistEvidence
    ttl_days: int = ALLOWLIST_TTL_DAYS_DEFAULT
    env: str = ""
    security_category_ban: bool = False  # must be False; asserted at validation

    @classmethod
    def from_dict(cls, d: dict) -> "AllowlistAttestation":
        by = _req(d, "attested_by", "AllowlistAttestation")
        if not isinstance(by, (list, tuple)) or len(by) != 2:
            raise ProofFormatError("AllowlistAttestation: attested_by must be exactly 2 identities")
        return cls(fingerprint=str(_req(d, "fingerprint", "AllowlistAttestation")),
                   attested_by=(str(by[0]), str(by[1])),
                   attested_at=str(_req(d, "attested_at", "AllowlistAttestation")),
                   on_evidence=AllowlistEvidence.from_dict(_req(d, "on_evidence",
                                                                 "AllowlistAttestation")),
                   ttl_days=int(d.get("ttl_days", ALLOWLIST_TTL_DAYS_DEFAULT)),
                   env=str(d.get("env", "")),
                   security_category_ban=bool(d.get("security_category_ban", False)))

    def to_dict(self) -> dict:
        return {"fingerprint": self.fingerprint, "attested_by": list(self.attested_by),
                "attested_at": self.attested_at, "on_evidence": self.on_evidence.to_dict(),
                "ttl_days": self.ttl_days, "env": self.env,
                "security_category_ban": self.security_category_ban}


# ---------------------------------------------------------------------------
# Verdicts. The gate consumes FreshnessReport, never the proofs.

@dataclass
class LockFreshness:
    verdict: str  # "fresh" | "stale"
    reason: str
    proof_id: str
    # Lock 3 extensions (per-entry staleness — A4 blast-radius containment):
    entries: dict = field(default_factory=dict)   # fingerprint -> LockFreshness
    stale_entries: list = field(default_factory=list)
    # Lock 1 extension: stale fit ⇒ dual-attestation interim path is offered
    # (synthesis §3.1); the gate decides, but the offer is named here.
    fallback_available: bool = False

    @property
    def fresh(self) -> bool:
        return self.verdict == "fresh"


@dataclass
class FreshnessReport:
    evaluated_at: str  # RFC3339
    config_manifest_sha256: str
    locks: dict  # lock id -> LockFreshness

    def is_fresh(self, lock_id: str) -> bool:
        """Per-alert read: O(1) dict lookup. No parsing, no clock, no I/O."""
        lf = self.locks.get(lock_id)
        return lf is not None and lf.fresh

    def entry_fresh(self, fingerprint: str) -> bool:
        """Per-alert read for lock 3's per-entry verdict. O(1)."""
        lock3 = self.locks.get(LOCK3_ALLOWLIST)
        if lock3 is None or not lock3.entries:
            return False
        entry = lock3.entries.get(fingerprint)
        return entry is not None and entry.fresh

    def stale_locks(self) -> list:
        """All stale locks — recorded even when evaluation short-circuits,
        so page_reason names the full rot (rot-matrix row 5)."""
        return [lid for lid, lf in self.locks.items() if not lf.fresh]

    def stale_reasons(self) -> list:
        out = []
        for lid in (LOCK1_CALIBRATION, LOCK2_THRESHOLD, LOCK3_ALLOWLIST):
            lf = self.locks.get(lid)
            if lf is None or lf.fresh:
                continue
            if lid == LOCK3_ALLOWLIST and lf.entries:
                for fp in lf.stale_entries:
                    out.append(lf.entries[fp].reason)
            else:
                out.append(lf.reason)
        return out

    def to_dict(self) -> dict:
        def _lf(lf: LockFreshness) -> dict:
            d = {"verdict": lf.verdict, "reason": lf.reason, "proof_id": lf.proof_id}
            if lf.entries:
                d["entries"] = {fp: _lf(e) for fp, e in lf.entries.items()}
            if lf.fallback_available:
                d["fallback_available"] = True
            return d
        return {"evaluated_at": self.evaluated_at,
                "config_manifest_sha256": self.config_manifest_sha256,
                "locks": {lid: _lf(lf) for lid, lf in self.locks.items()}}


# ---------------------------------------------------------------------------
# Freshness predicates — pure functions, each returning a LockFreshness.
# "Stale" and "invalid" both fail the lock; invalid ⇒ page, same as stale.

def fit_freshness(proof: CalibrationFitProof, now_epoch: float,
                  pinned_model_version: str,
                  deployed_label_pipeline_version: str) -> LockFreshness:
    """Lock 1 predicate: age ≤ window AND model_pin == pinned AND label
    pipeline matches the deployed pipeline (a frozen outcomes pipeline
    poisons the labels the fit trains on — the fit rots through its
    training data even if its date is fresh)."""
    pid = proof.fit_id or "<missing-fit-id>"
    try:
        trained_at = parse_rfc3339(proof.fit_trained_at)
    except ValueError as exc:
        return LockFreshness("stale", f"lock1_stale: invalid fit_trained_at — {exc}",
                             pid, fallback_available=True)
    if not _ISSUED_BY_RE.match(proof.issued_by or ""):
        return LockFreshness(
            "stale",
            "lock1_stale: invalid issued_by %r — the fit is machine-generated; "
            "the proof must name a tuner run as <run-id>/<code-version>" % (proof.issued_by,),
            pid, fallback_available=True)
    window_s = max(int(proof.validity_window_days), 1) * SECONDS_PER_DAY
    age_days = (now_epoch - trained_at) / SECONDS_PER_DAY
    if now_epoch - trained_at > window_s:
        return LockFreshness(
            "stale",
            f"lock1_stale: fit TTL expired (age {age_days:.1f}d > window "
            f"{proof.validity_window_days}d; fit_id {pid})",
            pid, fallback_available=True)
    if (proof.model_pin or "") != (pinned_model_version or ""):
        return LockFreshness(
            "stale",
            f"lock1_stale: model_pin mismatch (fit trained against "
            f"{proof.model_pin!r}, pinned model is {pinned_model_version!r}) — "
            f"vendor model move invalidates the fit mapping (ADR-015)",
            pid, fallback_available=True)
    trained_pipeline = (proof.trained_on.label_pipeline_version or "")
    if trained_pipeline != (deployed_label_pipeline_version or ""):
        return LockFreshness(
            "stale",
            f"lock1_stale: label pipeline drift (fit trained on "
            f"{trained_pipeline!r}, deployed pipeline is "
            f"{deployed_label_pipeline_version!r}) — frozen outcomes pipeline "
            f"poisons the fit's training labels",
            pid, fallback_available=True)
    return LockFreshness("fresh",
                         f"lock1 fresh: fit {pid} age {age_days:.1f}d ≤ "
                         f"{proof.validity_window_days}d, pin and pipeline match",
                         pid)


def threshold_freshness(att: ThresholdAttestation, now_epoch: float,
                        live_thresholds_obj: dict,
                        live_confidence_bar: float) -> LockFreshness:
    """Lock 2 predicate: clock not lapsed AND threshold == live bar AND
    config_hash == canonical-sha256(live thresholds.json) AND bar ≥ floor.
    Lock 2 has NO fallback — the bar is the human's judgment, and a stale
    bar is judgment nobody currently stands behind."""
    pid = f"threshold-attestation@{att.attested_at}"
    if att.threshold < CONF_FLOOR:
        return LockFreshness(
            "stale",
            f"lock2_stale: invalid attestation — threshold {att.threshold} below "
            f"conf floor {CONF_FLOOR} (an attestation *for* a sub-floor bar is "
            f"invalid, not stale; invalid ⇒ page too)",
            pid)
    if len(set(att.attested_by)) != 2 or not all(att.attested_by):
        return LockFreshness(
            "stale",
            "lock2_stale: invalid attestation — requires two distinct operator "
            "identities (ADR-022); the fatigue ratchet is a single-operator "
            "failure mode",
            pid)
    try:
        due = parse_rfc3339(att.revalidation_due_at)
    except ValueError as exc:
        return LockFreshness("stale",
                             f"lock2_stale: invalid revalidation_due_at — {exc}", pid)
    if now_epoch > due:
        return LockFreshness(
            "stale",
            f"lock2_stale: revalidation clock lapsed (due "
            f"{att.revalidation_due_at}, now {rfc3339_from_epoch(now_epoch)}) — "
            f"the bar no longer has two humans standing behind it",
            pid)
    if float(att.threshold) != float(live_confidence_bar):
        return LockFreshness(
            "stale",
            f"lock2_stale: attested threshold {att.threshold} != live confidence "
            f"bar {live_confidence_bar} — the bar moved without re-attestation",
            pid)
    expected_hash = config_hash_for(live_thresholds_obj)
    if (att.config_hash or "") != expected_hash:
        return LockFreshness(
            "stale",
            "lock2_stale: config_hash mismatch — thresholds.json was edited "
            "without a matching two-person re-attestation (the I-2 world); "
            "editing the bar without attestation pages everything",
            pid)
    return LockFreshness("fresh",
                         f"lock2 fresh: bar {att.threshold} attested by "
                         f"{att.attested_by[0]}+{att.attested_by[1]}, clock valid to "
                         f"{att.revalidation_due_at}",
                         pid)


def allowlist_entry_freshness(entry: AllowlistAttestation, now_epoch: float,
                              drift_state: dict | None = None) -> LockFreshness:
    """Lock 3 per-entry predicate: TTL not expired AND no incident linkage
    AND no owning-service major rewrite AND security-category ban holds.
    Attestation freshness is necessary but not sufficient — the drift check
    dominates (a fingerprint that appeared in a real SEV1/2 is poisoned even
    inside its TTL)."""
    fp = entry.fingerprint or "<missing-fingerprint>"
    if entry.security_category_ban:
        return LockFreshness(
            "stale",
            f"lock3_stale: entry {fp} rejected at validation — "
            f"security-category fingerprints are banned in code (ADR-017)",
            fp)
    if len(set(entry.attested_by)) != 2 or not all(entry.attested_by):
        return LockFreshness(
            "stale",
            f"lock3_stale: entry {fp} invalid — requires two distinct attesting "
            f"identities",
            fp)
    try:
        attested_at = parse_rfc3339(entry.attested_at)
    except ValueError as exc:
        return LockFreshness("stale",
                             f"lock3_stale: entry {fp} invalid attested_at — {exc}",
                             fp)
    ttl_s = max(int(entry.ttl_days), 1) * SECONDS_PER_DAY
    age_days = (now_epoch - attested_at) / SECONDS_PER_DAY
    if now_epoch - attested_at > ttl_s:
        return LockFreshness(
            "stale",
            f"lock3_stale: entry {fp} TTL expired (age {age_days:.1f}d > "
            f"{entry.ttl_days}d)",
            fp)
    drift = drift_state or {}
    incident = (drift.get("incident_linked") or {}).get(fp)
    if incident:
        return LockFreshness(
            "stale",
            f"lock3_stale: entry {fp} attestation VOID — incident linkage "
            f"({incident}); a fingerprint that appeared in a real SEV1/2 is "
            f"poisoned; entry quarantined",
            fp)
    if (drift.get("rewritten") or {}).get(fp):
        return LockFreshness(
            "stale",
            f"lock3_stale: entry {fp} drift check failed — owning service had a "
            f"major-version rewrite since observed_since; the fingerprint is a "
            f"claim about a service that no longer exists; re-attest on "
            f"post-rewrite evidence",
            fp)
    return LockFreshness("fresh",
                         f"lock3 entry {fp} fresh: age {age_days:.1f}d ≤ "
                         f"{entry.ttl_days}d, no incident linkage, no rewrite",
                         fp)


# ---------------------------------------------------------------------------
# Config bundle: manifest-bound storage (design §A2).
#
#   <bundle>/
#     thresholds.json          # lock 2's live values; config_hash binds here
#     allowlist.json           # per-env namespaces; entries carry fingerprints
#     calibration.json         # the fit artifact (gate reads p_upper from here)
#     pinning.json             # {"pinned_model_version": "..."} (ADR-015)
#     proofs/
#       calibration_proof.json
#       threshold_attestation.json
#       allowlist_attestations.json
#     manifest.json            # sha256 of every file above + schema version
#
# The manifest binds all proofs to one content hash: a partial update (new
# thresholds, old attestation) is detectable structurally.

BUNDLE_FILES = (
    "thresholds.json",
    "allowlist.json",
    "calibration.json",
    "pinning.json",
    "proofs/calibration_proof.json",
    "proofs/threshold_attestation.json",
    "proofs/allowlist_attestations.json",
)


class ManifestError(Exception):
    """The bundle failed manifest verification — invalid bundle, not a stale
    proof. Per ADR-018 the caller falls back to last-good + page; the
    freshness validator never runs on a malformed bundle."""


def _read_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(bundle_dir: str) -> dict:
    """Compute the manifest for a bundle directory (used by tooling/tests)."""
    files = {}
    for rel in BUNDLE_FILES:
        full = os.path.join(bundle_dir, rel)
        if not os.path.isfile(full):
            raise ManifestError(f"bundle missing required file: {rel}")
        files[rel] = _sha256_file(full)
    manifest = {"schema_version": SCHEMA_VERSION, "files": files}
    manifest["manifest_sha256"] = canonical_sha256(
        {"schema_version": SCHEMA_VERSION, "files": files})
    return manifest


def write_manifest(bundle_dir: str) -> dict:
    manifest = build_manifest(bundle_dir)
    with open(os.path.join(bundle_dir, "manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    return manifest


def verify_manifest(bundle_dir: str) -> str:
    """Verify every file hash in manifest.json. Returns manifest_sha256.

    Raises ManifestError on any mismatch / missing file / bad manifest —
    the boot self-test verifies the manifest FIRST, before any proof is
    evaluated."""
    manifest_path = os.path.join(bundle_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise ManifestError("bundle has no manifest.json")
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(
            f"unsupported manifest schema_version {manifest.get('schema_version')!r} "
            f"(expected {SCHEMA_VERSION})")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ManifestError("manifest 'files' is not a mapping")
    for rel in BUNDLE_FILES:
        expected = files.get(rel)
        if not expected:
            raise ManifestError(f"manifest does not cover required file: {rel}")
        full = os.path.join(bundle_dir, rel)
        if not os.path.isfile(full):
            raise ManifestError(f"bundle file missing: {rel}")
        actual = _sha256_file(full)
        if actual != expected:
            raise ManifestError(
                f"manifest hash mismatch for {rel}: expected {expected[:16]}…, "
                f"got {actual[:16]}…")
    recomputed = canonical_sha256({"schema_version": manifest["schema_version"],
                                   "files": files})
    if manifest.get("manifest_sha256") != recomputed:
        raise ManifestError("manifest_sha256 does not match manifest contents")
    return manifest["manifest_sha256"]


# ---------------------------------------------------------------------------
# The validator — V1 (config load) and V2 (heartbeat) run this. NEVER per-alert.

class FreshnessValidator:
    """Evaluates the three freshness predicates against a config bundle.

    ``clock`` returns epoch seconds (injectable for tests).
    ``pinned_model_version`` / ``deployed_label_pipeline_version`` are
    runtime inputs: the pin lives in the bundle (pinning.json) and is
    cross-checked here; the deployed label-pipeline version comes from the
    outcomes pipeline itself (a frozen pipeline poisons the fit's labels).
    """

    def __init__(self, *, clock=None,
                 deployed_label_pipeline_version: str = "",
                 validations_counted: list | None = None):
        self.clock = clock or time.time
        self.deployed_label_pipeline_version = deployed_label_pipeline_version
        # Optional out-param: tests append one entry per validate_bundle call
        # to prove the two-point discipline (never per-alert).
        self._count_sink = validations_counted

    # -- V1 -----------------------------------------------------------------

    def validate_bundle(self, bundle_dir: str,
                        drift_state: dict | None = None) -> FreshnessReport:
        """V1: verify manifest, then evaluate all three predicates.

        Raises ManifestError on bundle-integrity failure (caller: last-good
        + page per ADR-018). Proof-level defects ⇒ stale verdicts, never
        raised — a validator that crashes on a bad proof would convert
        calendar rot into an outage."""
        manifest_sha = verify_manifest(bundle_dir)
        if self._count_sink is not None:
            self._count_sink.append(1)
        now = self.clock()
        evaluated_at = rfc3339_from_epoch(now)

        thresholds_obj = _read_json(os.path.join(bundle_dir, "thresholds.json"))
        pinning = _read_json(os.path.join(bundle_dir, "pinning.json"))
        pinned_model = str(pinning.get("pinned_model_version", ""))

        # Lock 1.
        try:
            proof1 = CalibrationFitProof.from_dict(
                _read_json(os.path.join(bundle_dir, "proofs", "calibration_proof.json")))
            lock1 = fit_freshness(proof1, now, pinned_model,
                                  self.deployed_label_pipeline_version)
        except ProofFormatError as exc:
            lock1 = LockFreshness("stale", f"lock1_stale: malformed proof — {exc}",
                                  "<unparsed>", fallback_available=True)
        except (OSError, ValueError) as exc:
            lock1 = LockFreshness("stale", f"lock1_stale: proof unreadable — {exc}",
                                  "<unparsed>", fallback_available=True)

        # Lock 2. config.confidence_bar ≡ thresholds.json["suppress_conf_min"].
        try:
            att2 = ThresholdAttestation.from_dict(
                _read_json(os.path.join(bundle_dir, "proofs", "threshold_attestation.json")))
            live_bar = float(thresholds_obj["suppress_conf_min"])
            lock2 = threshold_freshness(att2, now, thresholds_obj, live_bar)
        except ProofFormatError as exc:
            lock2 = LockFreshness("stale", f"lock2_stale: malformed attestation — {exc}",
                                  "<unparsed>")
        except (OSError, ValueError, KeyError) as exc:
            lock2 = LockFreshness("stale", f"lock2_stale: attestation unreadable — {exc}",
                                  "<unparsed>")

        # Lock 3 — per-entry (blast-radius containment: one stale entry must
        # not fail the whole allowlist).
        lock3 = self._validate_allowlist(bundle_dir, now, drift_state)

        return FreshnessReport(evaluated_at=evaluated_at,
                               config_manifest_sha256=manifest_sha,
                               locks={LOCK1_CALIBRATION: lock1,
                                      LOCK2_THRESHOLD: lock2,
                                      LOCK3_ALLOWLIST: lock3})

    def _validate_allowlist(self, bundle_dir: str, now_epoch: float,
                            drift_state: dict | None) -> LockFreshness:
        try:
            allowlist_obj = _read_json(os.path.join(bundle_dir, "allowlist.json"))
            attestations_obj = _read_json(os.path.join(bundle_dir, "proofs",
                                                       "allowlist_attestations.json"))
        except (OSError, ValueError) as exc:
            return LockFreshness("stale",
                                 f"lock3_stale: allowlist bundle unreadable — {exc}",
                                 "<unparsed>")
        attestations = attestations_obj.get("entries", attestations_obj)
        if not isinstance(attestations, dict):
            return LockFreshness("stale",
                                 "lock3_stale: allowlist_attestations entries not a mapping",
                                 "<unparsed>")
        entries: dict[str, LockFreshness] = {}
        stale: list[str] = []
        # Walk every env namespace; entries are keyed by fingerprint.
        envs = allowlist_obj.get("envs", {})
        if not isinstance(envs, dict):
            return LockFreshness("stale",
                                 "lock3_stale: allowlist.json 'envs' not a mapping",
                                 "<unparsed>")
        for env, ns in envs.items():
            fps = (ns or {}).get("entries", [])
            if isinstance(fps, dict):  # tolerate {fp: {...}} shape too
                fps = list(fps.keys())
            for fp in fps:
                raw = attestations.get(fp)
                if raw is None:
                    verdict = LockFreshness(
                        "stale",
                        f"lock3_stale: entry {fp} (env {env}) has no attestation — "
                        f"an unattested allowlist entry cannot suppress",
                        fp)
                else:
                    try:
                        entry = AllowlistAttestation.from_dict(raw)
                        if entry.env and entry.env != env:
                            verdict = LockFreshness(
                                "stale",
                                f"lock3_stale: entry {fp} attested for env "
                                f"{entry.env!r} but listed under {env!r} — "
                                f"namespace mismatch",
                                fp)
                        else:
                            verdict = allowlist_entry_freshness(entry, now_epoch,
                                                                drift_state)
                    except ProofFormatError as exc:
                        verdict = LockFreshness(
                            "stale",
                            f"lock3_stale: entry {fp} malformed attestation — {exc}",
                            fp)
                entries[fp] = verdict
                if not verdict.fresh:
                    stale.append(fp)
        if stale:
            reason = (f"lock3_stale: {len(stale)} of {len(entries)} entries stale "
                      f"({', '.join(stale[:3])}{'…' if len(stale) > 3 else ''}); "
                      f"staleness is per-entry — fresh entries still suppress")
        else:
            reason = (f"lock3 fresh: {len(entries)} entries attested, "
                      f"all within TTL, no drift")
        return LockFreshness("stale" if stale else "fresh", reason,
                             "allowlist-attestations",
                             entries=entries, stale_entries=stale)

    # -- V2 -----------------------------------------------------------------

    def revalidate(self, bundle_dir: str, previous: FreshnessReport,
                   drift_state: dict | None = None
                   ) -> tuple[FreshnessReport, list[dict]]:
        """V2: re-run the validator (heartbeat). Returns the new report plus
        the fresh→stale transitions since ``previous`` — the transitions are
        what flip the gate's cached verdicts, fire the unsuppressible
        control-plane alert, and append the audit event."""
        current = self.validate_bundle(bundle_dir, drift_state=drift_state)
        transitions = _diff_reports(previous, current, self.clock())
        return current, transitions


def _diff_reports(previous: FreshnessReport, current: FreshnessReport,
                  now_epoch: float) -> list[dict]:
    """fresh→stale transitions only. stale→fresh is a repair (re-attest /
    re-fit / re-admit) and is logged by the governance flow, not alarmed."""
    detected_at = rfc3339_from_epoch(now_epoch)
    out: list[dict] = []

    def _transition(lock_id: str, prev: LockFreshness, cur: LockFreshness,
                    entry_fp: str | None = None):
        out.append({"lock": lock_id,
                    "entry_fingerprint": entry_fp,
                    "previous_verdict": "fresh",
                    "verdict": "stale",
                    "reason": cur.reason,
                    "proof_id": cur.proof_id,
                    "detected_at": detected_at})

    for lock_id in (LOCK1_CALIBRATION, LOCK2_THRESHOLD):
        prev, cur = previous.locks.get(lock_id), current.locks.get(lock_id)
        if prev is not None and cur is not None and prev.fresh and not cur.fresh:
            _transition(lock_id, prev, cur)

    prev3, cur3 = previous.locks.get(LOCK3_ALLOWLIST), current.locks.get(LOCK3_ALLOWLIST)
    if prev3 is not None and cur3 is not None:
        for fp, cur_entry in cur3.entries.items():
            prev_entry = prev3.entries.get(fp)
            prev_fresh = prev_entry.fresh if prev_entry is not None else True
            # A newly-appearing stale entry is also a transition (fail closed).
            if prev_fresh and not cur_entry.fresh:
                _transition(LOCK3_ALLOWLIST, prev3, cur_entry, entry_fp=fp)
    return out


# ---------------------------------------------------------------------------
# V2 wiring: the monitor holds the cached report the gate reads per-alert.

@dataclass
class TransitionRecord:
    lock: str
    entry_fingerprint: str | None
    reason: str
    proof_id: str
    detected_at: str


class FreshnessMonitor:
    """Owns the cached FreshnessReport across heartbeats.

    Per-alert, the gate calls :meth:`current_report` — a pure attribute read,
    zero validation work. Every ``heartbeat_seconds`` the control plane calls
    :meth:`heartbeat`, which re-runs the validator; on any fresh→stale
    transition the cached report is replaced, ``on_transition`` fires once
    per lock (not once per alert — alert-fatigue discipline on the guard's
    own alarm), and the transition records are returned for the audit event.
    The transition alert is UNSUPPRESSIBLE (constitution §7 Choice 3: the
    guard's alarm bypasses the guard) — that enforcement lives in the
    control plane; the monitor guarantees the emission happens exactly once
    per transition.
    """

    def __init__(self, validator: FreshnessValidator, bundle_dir: str,
                 *, heartbeat_seconds: int = HEARTBEAT_SECONDS_DEFAULT,
                 on_transition=None):
        self.validator = validator
        self.bundle_dir = bundle_dir
        self.heartbeat_seconds = heartbeat_seconds
        self.on_transition = on_transition  # callable(TransitionRecord) -> None
        self._report: FreshnessReport | None = None
        self._emitted: set[tuple] = set()  # (lock, entry_fp, proof_id) already alarmed

    def boot(self, drift_state: dict | None = None) -> FreshnessReport:
        """V1: validate at boot. An all-stale bundle still boots and serves —
        the failure mode is noisy paging, never silence, never a crash
        (rot-matrix row 8; ADR-018's liveness lesson). Manifest failure is
        the only boot refusal, handled by the caller's last-good path."""
        self._report = self.validator.validate_bundle(self.bundle_dir,
                                                      drift_state=drift_state)
        self._emitted = set()
        return self._report

    def current_report(self) -> FreshnessReport:
        """Per-alert read: returns the cached report. O(1). No validation,
        no clock read, no I/O — this is the whole point of two-point."""
        if self._report is None:
            raise RuntimeError("FreshnessMonitor.boot() must run before serving traffic")
        return self._report

    def heartbeat(self, drift_state: dict | None = None) -> list[TransitionRecord]:
        """V2: revalidate; flip cached verdicts; emit each fresh→stale
        transition exactly once."""
        if self._report is None:
            raise RuntimeError("FreshnessMonitor.boot() must run before heartbeat")
        current, raw = self.validator.revalidate(self.bundle_dir, self._report,
                                                 drift_state=drift_state)
        self._report = current
        records: list[TransitionRecord] = []
        for t in raw:
            key = (t["lock"], t["entry_fingerprint"], t["proof_id"], t["reason"])
            if key in self._emitted:
                continue
            self._emitted.add(key)
            rec = TransitionRecord(lock=t["lock"],
                                   entry_fingerprint=t["entry_fingerprint"],
                                   reason=t["reason"], proof_id=t["proof_id"],
                                   detected_at=t["detected_at"])
            records.append(rec)
            if self.on_transition is not None:
                self.on_transition(rec)
        return records


# ---------------------------------------------------------------------------
# The freshness precondition on the suppress conjunction (design §A4).
#
# can_suppress = lock1_fresh AND <lock-1 value checks>
#              AND lock2_fresh AND <lock-2 value checks>
#              AND lock3_fresh(entry) AND <lock-3 value checks>
#              AND corroboration AND NOT firewall-flagged AND NOT storm
#
# This module owns the freshness legs; the gate lane owns the value checks
# and the full conjunction. Any stale leg ⇒ suppress impossible ⇒ page.
# All stale legs are reported (no silent short-circuit) so the page reason
# names the full rot and the repair for each lock.

def suppress_precondition(report: FreshnessReport,
                          fingerprint: str | None = None
                          ) -> tuple[bool, list[str]]:
    """Freshness legs of the suppress conjunction.

    Returns (allowed, reasons). ``allowed`` is True only if every freshness
    leg passes. ``reasons`` names every stale leg with its cause — the gate
    lane prefixes these into page_reason. Lock 3 is evaluated per-entry:
    pass the matching allowlist entry's fingerprint; a stale entry fails
    only that entry, not the lock.

    Fail-closed on absence (ADR-014, D1): a lock MISSING from
    ``report.locks`` is a veto, exactly like a stale lock. An absent leg is
    not a passing leg — 'currently unreachable' is how fail-opens rot.
    """
    reasons: list[str] = []
    lock1 = report.locks.get(LOCK1_CALIBRATION)
    if lock1 is None:
        reasons.append("lock1_missing: no calibration freshness evidence — "
                       "absent leg cannot pass (ADR-014 fail-closed)")
    elif not lock1.fresh:
        reasons.append(lock1.reason)
    lock2 = report.locks.get(LOCK2_THRESHOLD)
    if lock2 is None:
        reasons.append("lock2_missing: no threshold freshness evidence — "
                       "absent leg cannot pass (ADR-014 fail-closed)")
    elif not lock2.fresh:
        reasons.append(lock2.reason)
    if fingerprint is None:
        lock3 = report.locks.get(LOCK3_ALLOWLIST)
        if lock3 is None:
            reasons.append("lock3_missing: no allowlist freshness evidence — "
                           "absent leg cannot pass (ADR-014 fail-closed)")
        elif not lock3.fresh:
            reasons.append(lock3.reason)
    else:
        if not report.entry_fresh(fingerprint):
            lock3 = report.locks.get(LOCK3_ALLOWLIST)
            entry = (lock3.entries.get(fingerprint) if lock3 else None)
            reasons.append(entry.reason if entry is not None
                           else f"lock3_stale: fingerprint {fingerprint} not in "
                                f"attested allowlist")
    # Deduplicate while preserving order (lock-3 summary + entries).
    seen, ordered = set(), []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            ordered.append(r)
    return (len(ordered) == 0), ordered


def lock1_fallback_offered(report: FreshnessReport) -> bool:
    """Lock 1's stale mechanics offer the dual-attestation interim path
    (synthesis §3.1); locks 2 and 3 offer none. The gate decides; the offer
    is named here so the page reason can say what the repair is."""
    lf = report.locks.get(LOCK1_CALIBRATION)
    return bool(lf is not None and not lf.fresh and lf.fallback_available)
