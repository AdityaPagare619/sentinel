"""D9 (ADR-023) — the counterfactual receipt on the live suppress path.

A suppression is the gate's most consequential act: choosing silence. The
operator's 3 AM question is never "what did you decide" — it is "how close
was this call, and what would you have done if the rules were slightly
different?" The receipt answers, for one suppressed alert, what the gate
WOULD have decided under nearby what-if scenarios: the definitional
``no_suppress_leg`` (the suppress branch removed from the kernel) plus the
operator's configured presets.

DR-26 — one kernel, evaluated N+1 times: every preset is evaluated
through ``Gate._resolve_suppress_path`` — the kernel + the D8 policy gate
+ the D5 corroboration leg composed once — the same composition the live
decision used. There is no mirror implementation; the old
``shadow.policy_action`` mirror was deleted for exactly this reason.

Computed at event-write time in ``Gate._on_answered``, AFTER the real
disposition is final, and it NEVER affects that disposition: the builder
is fully wrapped — any exception becomes an error receipt, never a
changed disposition, never a raised error. Pure: no I/O beyond what the
live path already did (the preset re-evaluations reuse the live
decision's resolved inputs — the same freshness report, allowlist set,
prob-lock verdict, and corroboration floor/evidences/now — because those
inputs are perishable, which is exactly why the receipt is computed at
event-write time rather than derived at read time).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

# The definitional counterfactual — always computed first, not
# configurable. A configured preset reusing the name replaces the builtin.
NO_SUPPRESS_LEG = "no_suppress_leg"

_ALLOWED_PRESET_KEYS = frozenset({
    "name",
    "description",
    "suppress_conf_min",     # override the suppress confidence floor
    "disable_suppress_leg",  # remove the suppress branch from the kernel
    "drop_evidence",         # evaluate the corroboration leg with no evidence
    "silence_floor_version", # evaluate the corroboration leg at that floor
})


class PresetError(ValueError):
    """A counterfactual preset failed validation."""


def normalize_counterfactual_preset(raw) -> dict:
    """Normalize one preset to its canonical dict form (validated).

    Bare numbers keep the historic ``(0.85, 0.90, 0.95, 0.99)`` config
    shape: ``0.85`` becomes ``{"name": "conf>=0.85",
    "suppress_conf_min": 0.85}``. Dicts are validated key-by-key; unknown
    keys are rejected (typos must not silently pass).
    """
    if isinstance(raw, dict):
        return _normalize_dict_preset(raw)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise PresetError(
            "preset must be a confidence number or an object, got "
            f"{type(raw).__name__}")
    p = float(raw)
    if not 0.0 < p <= 1.0:
        raise PresetError(
            f"bare preset {raw!r}: confidence must be in (0, 1]")
    return {"name": f"conf>={p:.2f}", "suppress_conf_min": p}


def _normalize_dict_preset(raw: dict) -> dict:
    unknown = sorted(set(raw) - _ALLOWED_PRESET_KEYS)
    if unknown:
        raise PresetError(
            f"unknown preset keys {unknown} (typo guard: only "
            f"{sorted(_ALLOWED_PRESET_KEYS)} are allowed)")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PresetError(
            "preset 'name' is required and must be a non-empty string")
    out: dict = {"name": name}
    desc = raw.get("description")
    if desc is not None:
        if not isinstance(desc, str):
            raise PresetError(
                f"preset {name!r}: 'description' must be a string")
        out["description"] = desc
    if "suppress_conf_min" in raw:
        scm = raw["suppress_conf_min"]
        if isinstance(scm, bool) or not isinstance(scm, (int, float)):
            raise PresetError(
                f"preset {name!r}: 'suppress_conf_min' must be a number")
        scm = float(scm)
        if not 0.0 < scm <= 1.0:
            raise PresetError(
                f"preset {name!r}: 'suppress_conf_min' must be in (0, 1]")
        out["suppress_conf_min"] = scm
    for key in ("disable_suppress_leg", "drop_evidence"):
        if key in raw:
            val = raw[key]
            if not isinstance(val, bool):
                raise PresetError(
                    f"preset {name!r}: {key!r} must be a boolean")
            out[key] = val
    if "silence_floor_version" in raw:
        sfv = raw["silence_floor_version"]
        if isinstance(sfv, bool) or not isinstance(sfv, int) or sfv <= 0:
            raise PresetError(
                f"preset {name!r}: 'silence_floor_version' must be a "
                "positive int")
        out["silence_floor_version"] = sfv
    return out


def validate_counterfactual_presets(raw) -> tuple:
    """Validate the ``counterfactual_presets`` config value.

    Returns a tuple of normalized preset dicts (``()`` when absent).
    Raises PresetError on anything invalid — the config loader maps this
    to ConfigRejected, fail-closed like every other policy field.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PresetError(
            "counterfactual_presets must be a list, got "
            f"{type(raw).__name__}")
    # R14 F4: bound the count — each suppress runs one kernel eval per
    # preset (~50µs each); an unbounded list is a latency footgun on the
    # suppress path. 64 is generous for an operator's what-if list.
    if len(raw) > 64:
        raise PresetError(
            f"counterfactual_presets has {len(raw)} entries, max 64")
    out = []
    seen = set()
    for entry in raw:
        preset = normalize_counterfactual_preset(entry)
        if preset["name"] in seen:
            raise PresetError(
                f"duplicate preset name {preset['name']!r}")
        seen.add(preset["name"])
        out.append(preset)
    return tuple(out)


@dataclasses.dataclass
class CounterfactualInputs:
    """The live decision's resolved inputs, captured for preset re-evaluation.

    The receipt re-runs the composition against byte-identical inputs —
    the same freshness report, allowlist set, prob-lock verdict, and
    corroboration (floor, evidences, now) the live decision used. The
    inputs are perishable (the silence floor is fresh-read per decision,
    the policy version can change, evidence ages out), which is why the
    receipt is computed at event-write time rather than derived later.
    """
    jev_model: str | None = None
    q_sev: object | None = None
    q_team: object | None = None
    q_disp: object | None = None
    latency_ms: float = 0.0
    prob_lock_pass: bool = False
    freshness_report: object | None = None
    allowlist_for_kernel: object = None
    via_legacy: bool = False
    lock1_dual_attested: bool = False
    corro_floor: object | None = None
    corro_evidences: list | None = None
    corro_now: object | None = None


def policy_pin(gate) -> dict:
    """The policy version pin for the receipt. Never raises.

    R14 F2 (honest): this is the version READ AT RECEIPT TIME, not the
    version the live decision evaluated — the decision's D8 check is a
    separate earlier read, and each suppressing preset re-reads again. A
    mid-receipt policy flip can make the pin and the per-preset gating
    disagree (narrow window; the receipt is advisory, the disposition is
    already fixed). What the pin guarantees: it names the policy state
    the receipt was computed against, so a postmortem can spot the skew.
    - policy gate wired: ``PolicyGate.version_info`` — a fresh read of the
      policy-state file, the same fresh-read discipline as ``can_suppress``
      (D8). Taken once per receipt.
    - no policy gate wired: version/state null — named, not omitted ("no
      pin" is itself information).
    - unreadable store (or a gate double without ``version_info``): the pin
      records the failure; presets still evaluate against the live kernel
      inputs — the receipt says what it can.
    """
    pid = getattr(gate, "policy_id", "suppression")
    pg = getattr(gate, "policy_gate", None)
    if pg is None:
        return {"policy_id": pid, "version": None, "state": None,
                "frozen": False, "pin_error": "no_policy_gate_wired"}
    version_info = getattr(pg, "version_info", None)
    if version_info is None:
        return {"policy_id": pid, "version": None, "state": None,
                "frozen": False,
                "pin_error": "policy_gate_has_no_version_info"}
    try:
        return version_info(pid)
    except Exception as exc:  # the pin must never sink the receipt
        return {"policy_id": pid, "version": None, "state": None,
                "frozen": False,
                "pin_error": f"{type(exc).__name__}: {exc}"}


def _evaluated_at(gate) -> str:
    """Receipt timestamp — the gate's clock when available (tests inject a
    fixed now), wall-clock otherwise. Human-readable only."""
    try:
        return gate._gate_now().isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def build_counterfactual_receipt(gate, alert, inputs: CounterfactualInputs,
                                 presets) -> dict:
    """Build the ADR-023 receipt for one suppressed alert.

    NEVER raises and NEVER affects the disposition — the disposition is
    computed BEFORE this is called. Any exception inside becomes an error
    receipt (``{"policy": <pin>, "presets": [], "error": ...}``); the
    decision stands.

    Receipt shape (the ``threshold_counterfactual`` body key on
    ``decision_made``)::

        {"policy": {"policy_id": ..., "version": ..., "state": ...,
                    "frozen": ...},
         "evaluated_at": "...",
         "presets": [{"name": ..., "description": ...,
                      "disposition": ..., "reason": ...,
                      "corroboration": {...} | None,
                      "unresolvable": None | "..."}]}
    """
    pin = policy_pin(gate)
    evaluated_at = _evaluated_at(gate)
    try:
        entries = _evaluate_presets(gate, alert, inputs, presets or ())
    except Exception as exc:  # advisory only — the decision stands
        return {"policy": pin, "evaluated_at": evaluated_at,
                "presets": [],
                "error": f"{type(exc).__name__}: {exc}"}
    return {"policy": pin, "evaluated_at": evaluated_at,
            "presets": entries}


def _evaluate_presets(gate, alert, inputs: CounterfactualInputs,
                      presets) -> list:
    """Evaluate the definitional counterfactual first, then each preset."""
    builtin = {
        "name": NO_SUPPRESS_LEG,
        "description": ("with the suppress branch removed from the policy "
                        "kernel, what disposition would this alert have "
                        "received?"),
        "disable_suppress_leg": True,
    }
    # The reserved name is always computed first; a configured preset
    # reusing the name replaces the builtin (its own axes apply).
    override = next((p for p in presets
                     if p.get("name") == NO_SUPPRESS_LEG), None)
    ordered = [override if override is not None else builtin]
    ordered.extend(p for p in presets if p.get("name") != NO_SUPPRESS_LEG)
    return [_evaluate_preset(gate, alert, inputs, p) for p in ordered]


def _evaluate_preset(gate, alert, inputs: CounterfactualInputs,
                     preset: dict) -> dict:
    """One what-if scenario through the shared composition (DR-26)."""
    name = preset["name"]
    description = preset.get("description", "")
    sfv = preset.get("silence_floor_version")
    if sfv is not None:
        current = _current_floor_version(gate, inputs)
        # Honest limitation, stated in the receipt rather than hidden: the
        # floor store is current-version-only by design — reconstructing a
        # past floor from metadata would be fabrication.
        if current != sfv:
            return {"name": name, "description": description,
                    "disposition": None, "reason": None,
                    "corroboration": None,
                    "unresolvable": (
                        f"silence floor v{sfv} not retained — store keeps "
                        f"the current version only (v{current})")}
    action, reason, _verdict, corro, _used = gate._resolve_suppress_path(
        alert,
        jev_model=inputs.jev_model,
        q_sev=inputs.q_sev, q_team=inputs.q_team, q_disp=inputs.q_disp,
        latency_ms=inputs.latency_ms,
        lock1_dual_attested=inputs.lock1_dual_attested,
        freshness_report=inputs.freshness_report,
        prob_lock_pass=inputs.prob_lock_pass,
        allowlist_for_kernel=inputs.allowlist_for_kernel,
        via_legacy=inputs.via_legacy,
        suppress_leg_enabled=not preset.get("disable_suppress_leg", False),
        suppress_conf_min_override=preset.get("suppress_conf_min"),
        drop_evidence=bool(preset.get("drop_evidence", False)),
        corro_inputs=_corro_inputs(gate, alert, inputs))
    return {"name": name, "description": description,
            "disposition": action, "reason": reason,
            # The leg's verdict dict when the leg ran for this preset (the
            # kernel said suppress under the preset), else null.
            "corroboration": (corro.to_dict() if corro is not None else None),
            "unresolvable": None}


def _current_floor_version(gate, inputs: CounterfactualInputs):
    """The silence floor version the live decision evaluated against."""
    floor = inputs.corro_floor
    if floor is None:
        try:
            floor = gate._corroboration_floor()
        except Exception:
            return None
    return getattr(floor, "version", None)


def _corro_inputs(gate, alert, inputs: CounterfactualInputs):
    """The (floor, evidences, now) triple for preset re-evaluation.

    The live decision's resolved triple is reused — no provider I/O, no
    fresh reads per preset. Defensive fallback (unreachable on the live
    suppress path, where the leg always ran): resolve once and share the
    triple across all presets.
    """
    if inputs.corro_floor is not None and inputs.corro_now is not None:
        return (inputs.corro_floor, inputs.corro_evidences or [],
                inputs.corro_now)
    floor = gate._corroboration_floor()
    now = gate._gate_now()
    return (floor, gate._provider_evidence(alert, floor, now), now)
