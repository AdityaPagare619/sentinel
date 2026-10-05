"""Validated configuration loading — fail-closed (design 05, §4).

Four ordered stages per load: parse -> schema -> semantic -> atomic swap.
A failure at any stage rejects the load; the live generation is untouched.

Startup semantics (fail-closed):
  * valid config            -> new generation persisted + served
  * invalid config + last-good available -> serve last-good, emit
    `config_rejected`, keep running
  * invalid config + no last-good        -> ConfigRejected; the caller must
    refuse to start. Sentinel never supervises paging with an unvalidated
    (or defaulted) policy.

Reload semantics (SIGHUP / POST /-/reload): invalid -> ConfigRejected, the
live generation is untouched, `config_rejected` is emitted.

The last-good chain keeps the newest N=5 validated generations on disk,
checksummed, so "last-good" always means "validated", never "whatever was
there before the crash".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time

from .counterfactual import PresetError, validate_counterfactual_presets
from .models import Thresholds

CONFIG_VERSION = 1
LAST_GOOD_KEEP = 5
SUPPRESS_CONF_MIN_FLOOR = 0.85  # ADR-022 governance floor, encoded in schema

# R-10 (devops-foundation.md §2.1): the flags.json contract. Type 1 —
# this schema is the contract; changing it is a contract change. It
# mirrors scripts/ops/flagctl.py's FLAG_DEFS exactly (the writer and the
# reader must agree or the flip procedure lies).
FLAGS_VERSION = 1
_FLAG_DEFS = {
    "global_kill_switch": "bool",
    "suppress_enabled": "bool",
    "shadow_mode": "bool",
    "canary_severity_bands": "list",
    "canary_services": "list",
}
# Effective values when no flags are supplied (e.g. a Gate built without
# the loader in tests): the previous behavior is the safe default.
FLAG_DEFAULTS = {
    "global_kill_switch": False,
    "suppress_enabled": True,
    "shadow_mode": False,
    "canary_severity_bands": [],
    "canary_services": [],
}

_THRESHOLD_FIELDS = (
    "suppress_p1_max",
    "suppress_conf_min",
    "page_p1p2_min",
    "uncertain_conf_max",
    "queue_conf_min",
)
# D9/ADR-023: optional non-threshold keys in thresholds.json. Unknown keys
# are still rejected (typo guard); these are validated by their own schema.
_OPTIONAL_THRESHOLD_KEYS = ("counterfactual_presets",)
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{16}")


class ConfigError(Exception):
    """Base class for config problems."""


class ConfigRejected(ConfigError):
    """A config load was rejected; the live generation is untouched."""


class ConfigMissing(ConfigRejected):
    """A required config file is not present."""


class PolicyConfig:
    """One validated, immutable policy generation."""

    __slots__ = ("thresholds", "allowlist", "flags", "generation",
                 "loaded_at", "source_sha256", "file_mtimes")

    def __init__(self, thresholds: Thresholds, allowlist: set[str],
                 flags: dict, generation: int, loaded_at: float,
                 source_sha256: str, file_mtimes: dict[str, float]):
        self.thresholds = thresholds
        self.allowlist = set(allowlist)
        # R-10: the validated flags generation (flag name -> value).
        # Immutable by convention: readers copy, never mutate.
        self.flags = dict(flags)
        self.generation = generation
        self.loaded_at = loaded_at
        self.source_sha256 = source_sha256
        self.file_mtimes = dict(file_mtimes)

    def to_dict(self) -> dict:
        t = self.thresholds
        return {
            "version": CONFIG_VERSION,
            "generation": self.generation,
            "loaded_at": self.loaded_at,
            "thresholds": {
                **{f: getattr(t, f) for f in _THRESHOLD_FIELDS},
                # D9/ADR-023: presets round-trip through the last-good
                # chain like every other validated policy field.
                "counterfactual_presets": [
                    dict(p) for p in t.counterfactual_presets],
            },
            "allowlist": sorted(self.allowlist),
            # R-10: flags round-trip through the last-good chain like
            # every other validated policy field, persisted in the
            # flags.json shape so restore reuses the same validator.
            "flags": {
                "version": FLAGS_VERSION,
                "flags": {
                    name: {"value": self.flags.get(name,
                                                   FLAG_DEFAULTS[name])}
                    for name in _FLAG_DEFS
                },
            },
            "source_sha256": self.source_sha256,
        }


# ------------------------------------------------------------------ loader

class ConfigLoader:
    """Loads and validates policy config; owns the last-good chain."""

    def __init__(self, config_dir: str = ".",
                 state_dir: str | None = None):
        self.config_dir = config_dir
        self.state_dir = (state_dir or os.environ.get("SENTINEL_STATE_DIR")
                          or "./sentinel-state")
        self._gen_dir = os.path.join(self.state_dir, "generations")
        self._events_path = os.path.join(self.state_dir, "events.jsonl")
        os.makedirs(self._gen_dir, exist_ok=True)
        self.current: PolicyConfig | None = None
        # mtimes of the last load attempt that was evaluated and rejected.
        # A rejected change is "acknowledged": it does not trip config_current
        # (last-good is still serving, and config_rejected was emitted), but
        # any *further* edit does.
        self._acked_mtimes: dict[str, float] | None = None

    # ------------------------------------------------------------ entry points

    def load_startup(self) -> PolicyConfig:
        """Startup load. Raises ConfigRejected when the process must not start.

        Invalid on-disk config falls back to the newest last-good generation;
        with no last-good generation available the rejection propagates and
        the caller refuses to start (fail-closed).
        """
        try:
            policy = self._load_and_validate(self._next_generation())
        except ConfigRejected as exc:
            last_good = self._newest_last_good()
            self._acked_mtimes = self._current_mtimes()
            self._emit_event("config_rejected", {
                "phase": "startup",
                "reason": str(exc),
                "fallback": ("last_good" if last_good is not None else "none"),
            })
            if last_good is None:
                raise ConfigRejected(
                    f"refusing to start: {exc} "
                    "(no last-good generation available)") from exc
            self.current = last_good
            return last_good
        self._persist_generation(policy)
        self._acked_mtimes = None
        self.current = policy
        return policy

    def reload(self) -> PolicyConfig:
        """Reload (SIGHUP / POST /-/reload). Invalid -> ConfigRejected.

        The live generation is untouched on rejection.
        """
        try:
            policy = self._load_and_validate(self._next_generation())
        except ConfigRejected as exc:
            self._acked_mtimes = self._current_mtimes()
            self._emit_event("config_rejected", {
                "phase": "reload",
                "reason": str(exc),
            })
            raise
        self._persist_generation(policy)
        self._acked_mtimes = None
        self.current = policy
        return policy

    def _current_mtimes(self) -> dict[str, float]:
        mtimes: dict[str, float] = {}
        for path in self._policy_paths():
            try:
                mtimes[path] = os.path.getmtime(path)
            except OSError:
                pass
        return mtimes

    def config_files_changed(self) -> bool:
        """True when a config file changed on disk since the live generation.

        Implements the `config_current` predicate's "loaded_at is newer than
        the last config-change signal" check: an operator edit that has not
        been reloaded (and validated) makes the process unhealthy. A change
        that was evaluated and *rejected* is acknowledged — last-good keeps
        serving and config_rejected was already emitted — but any further
        edit trips the predicate again.
        """
        if self.current is None:
            return True
        current = self._current_mtimes()
        live = self.current.file_mtimes
        acked = self._acked_mtimes or {}
        for path in set(live) | set(acked) | set(current):
            mtime = current.get(path)
            if mtime is None:
                return True  # deleted file counts as a change
            if mtime != live.get(path) and mtime != acked.get(path):
                return True
        return False

    # --------------------------------------------------------------- internals

    def _policy_paths(self) -> tuple[str, str, str]:
        return (os.path.join(self.config_dir, "thresholds.json"),
                os.path.join(self.config_dir, "allowlist.json"),
                os.path.join(self.config_dir, "flags.json"))

    def _load_and_validate(self, generation: int) -> PolicyConfig:
        thresholds_path, allowlist_path, flags_path = self._policy_paths()
        # Stage 1: parse.
        try:
            with open(thresholds_path, "r", encoding="utf-8") as fh:
                raw_thresholds = fh.read()
            t_data = json.loads(raw_thresholds)
        except FileNotFoundError:
            raise ConfigMissing(
                f"{thresholds_path}: not found; refusing to run with "
                "default thresholds")
        except (OSError, ValueError) as exc:
            raise ConfigRejected(
                f"{thresholds_path}: unparseable ({exc})") from exc
        try:
            with open(allowlist_path, "r", encoding="utf-8") as fh:
                a_data = json.load(fh)
        except FileNotFoundError:
            a_data = []  # fail-open: nothing allowlisted => nothing suppressed
        except (OSError, ValueError) as exc:
            raise ConfigRejected(
                f"{allowlist_path}: unparseable ({exc})") from exc
        # Stage 2: schema validation.
        thresholds = _validate_thresholds(t_data, thresholds_path)
        allowlist = _validate_allowlist(a_data, allowlist_path)
        flags = _validate_flags(_read_flags_file(flags_path), flags_path)
        # Stage 3: semantic validation.
        _semantic_check(thresholds)
        _flags_semantic_check(flags, flags_path)
        # Stage 4: build the generation (the swap itself is just assigning
        # self.current after persistence — readers never see a torn policy).
        sha = hashlib.sha256(
            json.dumps({"t": t_data, "a": sorted(allowlist),
                        "f": {k: flags[k] for k in sorted(flags)}},
                       sort_keys=True).encode("utf-8")).hexdigest()
        mtimes = {}
        for path in (thresholds_path, allowlist_path, flags_path):
            try:
                mtimes[path] = os.path.getmtime(path)
            except OSError:
                pass
        return PolicyConfig(
            thresholds=thresholds,
            allowlist=allowlist,
            flags=flags,
            generation=generation,
            loaded_at=time.time(),
            source_sha256=sha,
            file_mtimes=mtimes,
        )

    # ---------------------------------------------------------- last-good disk

    def _next_generation(self) -> int:
        best = 0
        for name in os.listdir(self._gen_dir):
            if name.startswith("gen-") and name.endswith(".json"):
                try:
                    best = max(best, int(name[4:-5]))
                except ValueError:
                    continue
        return best + 1

    def _persist_generation(self, policy: PolicyConfig) -> None:
        path = os.path.join(self._gen_dir,
                            f"gen-{policy.generation:04d}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(policy.to_dict(), fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)  # atomic publish
        # Prune to the newest LAST_GOOD_KEEP.
        gens = sorted(
            n for n in os.listdir(self._gen_dir)
            if n.startswith("gen-") and n.endswith(".json"))
        for stale in gens[:-LAST_GOOD_KEEP]:
            try:
                os.remove(os.path.join(self._gen_dir, stale))
            except OSError:
                pass

    def _newest_last_good(self) -> PolicyConfig | None:
        gens = sorted(
            (n for n in os.listdir(self._gen_dir)
             if n.startswith("gen-") and n.endswith(".json")),
            reverse=True)
        for name in gens:
            try:
                with open(os.path.join(self._gen_dir, name),
                          encoding="utf-8") as fh:
                    data = json.load(fh)
                t = Thresholds(
                    **{f: data["thresholds"][f] for f in _THRESHOLD_FIELDS},
                    # D9/ADR-023: presets restore through the last-good
                    # chain; a corrupt entry fails this generation (the
                    # except below moves to the next older one).
                    counterfactual_presets=validate_counterfactual_presets(
                        data["thresholds"].get("counterfactual_presets")))
                _semantic_check(t)  # last-good must still satisfy governance
                # R-10: flags restore through the last-good chain.
                # Generations written before flags existed get the safe
                # defaults (previous behavior); a corrupt flags entry
                # fails this generation (the except below moves on).
                raw_flags = data.get("flags")
                if raw_flags is None:
                    flags = dict(FLAG_DEFAULTS)
                else:
                    flags = _validate_flags(raw_flags, name)
                    _flags_semantic_check(flags, name)
                return PolicyConfig(
                    thresholds=t,
                    allowlist=set(data.get("allowlist", [])),
                    flags=flags,
                    generation=int(data["generation"]),
                    loaded_at=float(data.get("loaded_at", 0.0)),
                    source_sha256=str(data.get("source_sha256", "")),
                    file_mtimes={},  # on-disk files are suspect; any edit
                                     # after fallback trips config_current
                )
            except (OSError, ValueError, KeyError, TypeError,
                    ConfigRejected):
                continue  # corrupt generation: try the next older one
        return None

    # ----------------------------------------------------------------- events

    def _emit_event(self, event_type: str, detail: dict) -> None:
        event = {"ts": time.time(), "type": event_type, "detail": detail}
        try:
            with open(self._events_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True) + "\n")
        except OSError as exc:
            print(f"[sentinel] could not append event log: {exc}",
                  file=sys.stderr)
        print(f"[sentinel] event {event_type}: "
              f"{json.dumps(detail, sort_keys=True)}", file=sys.stderr)


# ------------------------------------------------------- schema + semantic

def _validate_thresholds(data, path: str) -> Thresholds:
    if not isinstance(data, dict):
        raise ConfigRejected(
            f"{path}: top-level JSON must be an object, got "
            f"{type(data).__name__}")
    unknown = sorted(set(data) - set(_THRESHOLD_FIELDS)
                     - set(_OPTIONAL_THRESHOLD_KEYS))
    if unknown:
        raise ConfigRejected(
            f"{path}: unknown keys {unknown} (typo guard: only "
            f"{list(_THRESHOLD_FIELDS) + list(_OPTIONAL_THRESHOLD_KEYS)} "
            "are allowed)")
    defaults = Thresholds()
    vals: dict[str, float] = {}
    for field_name in _THRESHOLD_FIELDS:
        value = data.get(field_name, getattr(defaults, field_name))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigRejected(
                f"{path}: {field_name} must be a number, got "
                f"{type(value).__name__}")
        vals[field_name] = float(value)
    # D9/ADR-023: operator what-if presets for the counterfactual receipt.
    # Invalid presets fail the load (ConfigRejected) — the live generation
    # is untouched.
    try:
        presets = validate_counterfactual_presets(
            data.get("counterfactual_presets"))
    except PresetError as exc:
        raise ConfigRejected(
            f"{path}: counterfactual_presets invalid: {exc}") from exc
    _in_range(path, "suppress_p1_max", vals["suppress_p1_max"], 0.0, 1.0,
              exclusive=True)
    _in_range(path, "suppress_conf_min", vals["suppress_conf_min"],
              SUPPRESS_CONF_MIN_FLOOR, 1.0)
    _in_range(path, "page_p1p2_min", vals["page_p1p2_min"], 0.0, 1.0,
              exclusive=True)
    _in_range(path, "uncertain_conf_max", vals["uncertain_conf_max"], 0.0, 1.0,
              exclusive=True)
    _in_range(path, "queue_conf_min", vals["queue_conf_min"], 0.0, 1.0,
              exclusive=True)
    return Thresholds(**vals, counterfactual_presets=presets)


def _in_range(path: str, name: str, value: float, lo: float, hi: float,
              exclusive: bool = False) -> None:
    ok = (lo < value < hi) if exclusive else (lo <= value <= hi)
    if not ok:
        bound = f"({lo}, {hi})" if exclusive else f"[{lo}, {hi}]"
        raise ConfigRejected(
            f"{path}: {name}={value} outside allowed range {bound}")


def _semantic_check(t: Thresholds) -> None:
    """Governance and coherence checks the schema cannot express."""
    if t.suppress_conf_min < SUPPRESS_CONF_MIN_FLOOR:
        # Design 05 §8.2: the fatigue ratchet's destination is unloadable by
        # hand-edit. Below the floor is a rejection, not a warning.
        raise ConfigRejected(
            f"suppress_conf_min={t.suppress_conf_min} below the ADR-022 "
            f"governance floor {SUPPRESS_CONF_MIN_FLOOR}")
    if not t.page_p1p2_min > t.suppress_p1_max:
        raise ConfigRejected(
            f"page_p1p2_min={t.page_p1p2_min} must exceed "
            f"suppress_p1_max={t.suppress_p1_max} (incoherent policy table)")
    if not t.uncertain_conf_max <= t.suppress_conf_min:
        raise ConfigRejected(
            f"uncertain_conf_max={t.uncertain_conf_max} must not exceed "
            f"suppress_conf_min={t.suppress_conf_min} (incoherent policy table)")


def _read_flags_file(path: str):
    """Read flags.json. Missing => ConfigMissing (fail-closed): a kill
    switch that silently doesn't exist is the rusted-shut failure mode,
    so the loader refuses to run without it — like thresholds.json."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise ConfigMissing(
            f"{path}: not found; refusing to run without a validated "
            "flags generation (run scripts/ops/env-bootstrap.sh)")
    except (OSError, ValueError) as exc:
        raise ConfigRejected(f"{path}: unparseable ({exc})") from exc


def _validate_flags(data, path: str) -> dict:
    """Schema validation for flags.json (R-10). Mirrors flagctl.py's
    FLAG_DEFS exactly — the writer and the reader must agree. Returns
    {flag_name: value}."""
    if not isinstance(data, dict):
        raise ConfigRejected(
            f"{path}: top-level JSON must be an object, got "
            f"{type(data).__name__}")
    # flagctl writes metadata keys alongside the contract ("_comment",
    # "changed_by", ...); only "version" and "flags" are contractual.
    if data.get("version") != FLAGS_VERSION:
        raise ConfigRejected(
            f"{path}: version must be {FLAGS_VERSION}, got "
            f"{data.get('version')!r}")
    flags = data.get("flags")
    if not isinstance(flags, dict):
        raise ConfigRejected(
            f"{path}: 'flags' must be an object, got "
            f"{type(flags).__name__}")
    unknown = sorted(set(flags) - set(_FLAG_DEFS))
    if unknown:
        raise ConfigRejected(
            f"{path}: unknown flags {unknown} (typo guard: only "
            f"{sorted(_FLAG_DEFS)} are allowed)")
    missing = sorted(set(_FLAG_DEFS) - set(flags))
    if missing:
        raise ConfigRejected(
            f"{path}: missing flags {missing} (all five flags are "
            "required; flagctl.py writes them)")
    values: dict = {}
    for name, want in _FLAG_DEFS.items():
        spec = flags[name]
        if not isinstance(spec, dict) or "value" not in spec:
            raise ConfigRejected(
                f"{path}: flag {name!r} must be an object with a 'value'")
        value = spec["value"]
        if want == "bool" and not isinstance(value, bool):
            raise ConfigRejected(
                f"{path}: flag {name!r} must be a bool, got "
                f"{type(value).__name__}")
        if want == "list" and not isinstance(value, list):
            raise ConfigRejected(
                f"{path}: flag {name!r} must be a list, got "
                f"{type(value).__name__}")
        values[name] = value
    return values


def _flags_semantic_check(flags: dict, path: str) -> None:
    """Governance checks the schema cannot express.

    A non-empty canary list is rejected: no dual-policy ("candidate
    policy") mechanism exists in the gate, so arming a canary the kernel
    cannot honor would repeat the R-1 finding (a governed object the
    kernel never reads). The rejection names the follow-up (R-15,
    Phase 4) — fail-closed, loud, and honest instead of
    validate-and-ignore theater.
    """
    for name in ("canary_severity_bands", "canary_services"):
        if flags.get(name):
            raise ConfigRejected(
                f"{path}: flag {name!r} is non-empty "
                f"({flags[name]!r}) but the gate has no dual-policy "
                "canary mechanism yet (R-15, Phase 4) — refusing to arm "
                "a canary the kernel cannot honor. Clear the list and "
                "reload.")


def _validate_allowlist(data, path: str) -> set[str]:
    if not isinstance(data, list):
        raise ConfigRejected(
            f"{path}: top-level JSON must be an array, got "
            f"{type(data).__name__}")
    out: set[str] = set()
    for i, fp in enumerate(data):
        if not isinstance(fp, str) or not _FINGERPRINT_RE.fullmatch(fp):
            raise ConfigRejected(
                f"{path}[{i}]: must be a 16-char hex fingerprint, got "
                f"{fp!r}")
        out.add(fp)
    return out


# ------------------------------------------------------- restart accounting

def _restarts_path(state_dir: str) -> str:
    return os.path.join(state_dir, "restarts.jsonl")


def record_restart(state_dir: str) -> None:
    """Append one restart record. Owned by the supervisor in production; the
    process maintains it when run directly so the crash-loop predicate works
    standalone."""
    os.makedirs(state_dir, exist_ok=True)
    try:
        with open(_restarts_path(state_dir), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(),
                                 "pid": os.getpid()}) + "\n")
    except OSError as exc:
        print(f"[sentinel] could not record restart: {exc}", file=sys.stderr)


def count_restarts_10m(state_dir: str, clock=time.time,
                       window_s: float = 600.0) -> int:
    """Restarts recorded inside the trailing window (design §1.2.5)."""
    try:
        with open(_restarts_path(state_dir), encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return 0
    cutoff = clock() - window_s
    n = 0
    for line in lines:
        try:
            if float(json.loads(line)["ts"]) > cutoff:
                n += 1
        except (ValueError, KeyError, TypeError):
            continue
    return n
