"""ADR-007 — Muted-not-dropped: platform-tier mute LABEL + governance.

DESIGN (written before code, 2026-10-04, lane D12)

The five ADR-007 conditions, and where each is enforced:

  (a) Engine disposition enum stays fixed.
      "Muted" is a RENDERING LABEL computed at read time over a
      suppress-with-reason decision. It is never stored as a disposition,
      never emitted by the gate, and never crosses the DR-9 process
      boundary into the engine. tests/test_mute_governance.py freezes the
      engine vocabulary (exact-set assertion) and the platform store's
      4-valued DISP_OPTIONS. NOTE: ADR-007's "four-valued" was written
      pre-D3; D3 (#56) ratified "folded" as a 5th engine disposition. The
      invariant protected here is "mute never becomes a disposition" —
      the freeze test pins the exact ratified set.

  (b) Mute transitions are event-logged (reason, attestor, TTL).
      mute_applied / mute_appealed / mute_lifted / mute_expired are
      first-class event types in sentinel.eventlog (append-only, hash
      chained). Every transition carries reason + named human attestor +
      TTL/expiry.

  (c) No auto-mute, ever.
      Structural: apply_mute() takes attestor as a REQUIRED keyword-only
      argument (no default — there is no code path that can omit it), and
      the event log's _validate rejects automated actors as attestors even
      for direct append_event callers. A codebase scan test enumerates
      every apply_mute call site and proves none is an automated path.

  (d) Weekly mute review ritual in the shadow report with a named owner.
      build_mute_section() produces the "What we muted" payload:
      active/expired mutes, mute rates, the review checklist, and
      MUTE_REVIEW_OWNER — a placeholder that MUST be replaced with a
      named human before the design-partner cutover. The shadow report
      renders the section every week, muted rows visible by default.

  (e) Tripwire quarantine invariant in CI.
      quarantined_suppressions() projects every suppression-class
      disposition (suppress — D3's "folded" is explicitly NOT suppression)
      from the append-only log with its alert context, ADR-023 threshold
      counterfactual, and current mute label. The CI test asserts every
      suppression is retrievable within QUARANTINE_STALENESS_BOUND_S of
      commit.

Pre-mortem (top 3 failure modes, from the skill):
  1. Mute widens the gate enum -> freeze test + zero engine routing changes.
  2. A convenience auto-mute path appears -> required attestor + log-layer
     blocklist + scan test.
  3. Muted rows go invisible -> default-visible label, quarantine invariant,
     weekly ritual with named owner.

Type: Type-2 mechanics (platform rendering + event vocabulary) under the
Type-1 ADR-007 governance decision. No engine routing semantics changed.
"""

from __future__ import annotations

import inspect
import json
import os
from dataclasses import dataclass, field

# The engine event log owns the durable mute-transition vocabulary.
# Imported lazily where needed would hide the dependency; import it here —
# platform/server already depends on sentinel (see store.py's engine ties).
from sentinel.eventlog import (HUMAN_ATTESTOR_BLOCKLIST, EventLog,
                               utcnow_iso)

# ---------------------------------------------------------------- constants

# The mute LABEL. A rendering string only — deliberately NOT a member of
# sentinel.eventlog.DISPOSITIONS and never stored as a disposition.
MUTE_LABEL = "muted"

# Suppression-class dispositions (ADR-007 Tripwire condition). "folded" is
# excluded on purpose: D3 defines it as NOT suppression (no model decided
# anything about the alert; it was absorbed into the storm aggregate page).
SUPPRESSION_CLASS = frozenset({"suppress"})

# Mute TTL bounds. A mute is sustained human triage, not a per-alert tweak:
# below 1h the engine's own suppression already covers it; above 7d it must
# survive the weekly review ritual with fresh human attestation.
MIN_MUTE_TTL_S = 3600
MAX_MUTE_TTL_S = 7 * 86400

# Bounded staleness for the quarantine invariant (condition e): a committed
# suppression-class decision must be retrievable from the quarantine
# projection within this many seconds. Generous for CI; production should
# see milliseconds (the projection is a synchronous read over the log).
QUARANTINE_STALENESS_BOUND_S = 5.0

# Condition (d): the weekly review has a NAMED OWNER. This placeholder must
# be replaced with a real human (e.g. "oncall-primary <name>") before the
# design-partner cutover — the review ritual is void without a name.
MUTE_REVIEW_OWNER = "TBD — name the on-call mute-review owner before cutover"

# Condition (d): the weekly "what we muted" review checklist. Every item
# must be answerable from the section's data; an unanswered item blocks
# the week's sign-off.
MUTE_REVIEW_CHECKLIST = (
    "Every active mute still has a live reason — re-attest or lift; "
    "no mute survives the week on stale justification.",
    "No mute exceeded its TTL without a fresh human attestation "
    "(expired mutes auto-lifted; check the expired list).",
    "Muted fingerprints show no SEV1/SEV2-class signal in the divergence "
    "list — a mute is triage, never a blindfold.",
    "Appeals reviewed: every mute_appealed event has a named resolver "
    "and a recorded outcome.",
    "Mute rate vs suppression rate sane: mutes are the exception, not "
    "the fatigue ratchet's retirement home.",
)

MUTE_EVENT_TYPES = ("mute_applied", "mute_appealed", "mute_lifted",
                    "mute_expired")


# ---------------------------------------------------------------- data

@dataclass
class MuteRecord:
    """One active mute, folded from the append-only event log."""
    fingerprint: str
    alert_id: str
    reason: str
    attestor: str          # named human — never an automated actor
    ttl_s: int
    muted_at: str          # ISO-8601 UTC
    expires_at: str        # ISO-8601 UTC
    applied_seq: int
    links: dict = field(default_factory=dict)


# ---------------------------------------------------------------- validation

def _require_human_attestor(attestor: str) -> str:
    """Validate the attestor; return the stripped name. Raises ValueError."""
    if not isinstance(attestor, str) or not attestor.strip():
        raise ValueError("mute requires a named human attestor "
                         "(ADR-007: no auto-mute, ever)")
    name = attestor.strip()
    if len(name) < 3:
        raise ValueError(f"attestor {name!r} is not a plausible human name")
    low = name.lower()
    if low in HUMAN_ATTESTOR_BLOCKLIST or "auto" in low:
        raise ValueError(f"attestor {name!r} is not a human — auto-mute is "
                         f"forbidden (ADR-007)")
    return name


def _require_reason(reason: str) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("mute requires a non-empty reason")
    if len(reason) > 500:
        raise ValueError("mute reason must fit in 500 chars "
                         "(write the essay in the review, not the log)")
    return reason.strip()


def _require_ttl(ttl_s: int) -> int:
    if not isinstance(ttl_s, int) or isinstance(ttl_s, bool):
        raise ValueError("ttl_s must be an integer number of seconds")
    if not (MIN_MUTE_TTL_S <= ttl_s <= MAX_MUTE_TTL_S):
        raise ValueError(
            f"ttl_s must be within [{MIN_MUTE_TTL_S}, {MAX_MUTE_TTL_S}] "
            f"(1h..7d); got {ttl_s}")
    return ttl_s


def _iso_to_epoch(ts: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _epoch_to_iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------- write API

def apply_mute(log: EventLog, *, alert_id: str, fingerprint: str,
               episode_id: str, reason: str, attestor: str,
               ttl_s: int, now_iso: str | None = None) -> int:
    """Apply a mute. Returns the mute_applied event seq.

    attestor is REQUIRED keyword-only with no default — there is no code
    path that can apply a mute without naming the human (ADR-007 c).
    """
    name = _require_human_attestor(attestor)
    why = _require_reason(reason)
    ttl = _require_ttl(ttl_s)
    now = now_iso or utcnow_iso()
    expires = _epoch_to_iso(_iso_to_epoch(now) + ttl)
    return log.append_event(
        "mute_applied", actor="operator", alert_id=alert_id,
        fingerprint=fingerprint, episode_id=episode_id,
        body={"fingerprint": fingerprint, "reason": why, "attestor": name,
              "ttl_s": ttl, "muted_at": now, "expires_at": expires},
        ts=now)


def appeal_mute(log: EventLog, *, alert_id: str, fingerprint: str,
                episode_id: str, attestor: str, appeal_reason: str,
                now_iso: str | None = None) -> int:
    """Appeal a mute (Tripwire: appeal writes a mute_appealed event)."""
    name = _require_human_attestor(attestor)
    why = _require_reason(appeal_reason)
    now = now_iso or utcnow_iso()
    return log.append_event(
        "mute_appealed", actor="operator", alert_id=alert_id,
        fingerprint=fingerprint, episode_id=episode_id,
        body={"fingerprint": fingerprint, "attestor": name,
              "appeal_reason": why, "appealed_at": now},
        ts=now)


def lift_mute(log: EventLog, *, alert_id: str, fingerprint: str,
              episode_id: str, attestor: str, lift_reason: str,
              now_iso: str | None = None) -> int:
    """Lift a mute early. Human attestor required — unmuting is also a
    governance transition, not a silent row delete."""
    name = _require_human_attestor(attestor)
    why = _require_reason(lift_reason)
    now = now_iso or utcnow_iso()
    return log.append_event(
        "mute_lifted", actor="operator", alert_id=alert_id,
        fingerprint=fingerprint, episode_id=episode_id,
        body={"fingerprint": fingerprint, "attestor": name,
              "lift_reason": why, "lifted_at": now},
        ts=now)


def expire_mutes(log: EventLog, *, now_iso: str | None = None,
                 limit: int = 1000) -> list[int]:
    """Sweep expired mutes; append mute_expired per newly-expired mute.

    This is NOT auto-mute: expiry honors the TTL the human attestor set at
    apply time (auto-UNmute). Actor is "operator" — the mute lifecycle is
    owned by the human-operated control plane.
    """
    now = now_iso or utcnow_iso()
    now_epoch = _iso_to_epoch(now)
    seqs: list[int] = []
    folded = _fold_mute_events(log, now_epoch, drop_past_ttl=False)
    for rec in sorted(folded.values(), key=lambda r: r.expires_at):
        if _iso_to_epoch(rec.expires_at) <= now_epoch:
            seqs.append(log.append_event(
                "mute_expired", actor="operator", alert_id=rec.alert_id,
                fingerprint=rec.fingerprint, episode_id="",
                body={"fingerprint": rec.fingerprint, "expired_at": now,
                      "applied_seq": rec.applied_seq},
                ts=now))
            if len(seqs) >= limit:
                break
    return seqs


# ---------------------------------------------------------------- read API

def _mute_events(log: EventLog, limit: int = 10000) -> list[dict]:
    out: list[dict] = []
    for etype in MUTE_EVENT_TYPES:
        out.extend(log.events_of_type(etype, limit=limit))
    out.sort(key=lambda r: r["seq"])
    return out


def _fold_mute_events(log: EventLog, now_epoch: float,
                      drop_past_ttl: bool) -> dict[str, MuteRecord]:
    """Fold mute events into per-fingerprint records.

    drop_past_ttl=True: the live "active" set (past-TTL mutes never surface).
    drop_past_ttl=False: for the expiry sweep — includes mutes whose TTL has
    lapsed but which have no mute_expired event yet.
    """
    records: dict[str, MuteRecord] = {}
    for row in _mute_events(log):
        body = json.loads(row["body"])
        fp = body["fingerprint"]
        etype = row["type"]
        if etype == "mute_applied":
            if drop_past_ttl and _iso_to_epoch(body["expires_at"]) <= now_epoch:
                continue  # already past TTL — never becomes active
            records[fp] = MuteRecord(
                fingerprint=fp, alert_id=row["alert_id"],
                reason=body["reason"], attestor=body["attestor"],
                ttl_s=body["ttl_s"], muted_at=body["muted_at"],
                expires_at=body["expires_at"], applied_seq=row["seq"],
                links={"applied_seq": row["seq"]})
        elif etype in ("mute_appealed", "mute_lifted", "mute_expired"):
            records.pop(fp, None)
    return records


def active_mutes(log: EventLog, *, now_iso: str | None = None,
                 limit: int = 10000) -> list[MuteRecord]:
    """Fold the append-only mute events into the current active set.

    applied − appealed − lifted − expired − past-TTL = active. A fingerprint
    re-muted after appeal/lift/expiry starts a fresh record.
    """
    now_epoch = _iso_to_epoch(now_iso or utcnow_iso())
    records = _fold_mute_events(log, now_epoch, drop_past_ttl=True)
    return sorted(records.values(), key=lambda r: r.expires_at)


def render_disposition_label(disposition: str,
                             mute: MuteRecord | None) -> str:
    """Platform-tier rendering: suppress + active mute -> "muted".

    This is a LABEL, not a disposition. Every other disposition renders
    unchanged — mute never relabels a page, a passthrough, or a fold.
    """
    if disposition == "suppress" and mute is not None:
        return MUTE_LABEL
    return disposition


def quarantined_suppressions(log: EventLog, *, since_seq: int = 0,
                              limit: int = 5000,
                              now_iso: str | None = None) -> list[dict]:
    """Quarantine projection (ADR-007 condition e).

    Every suppression-class disposition committed to the log, with its
    retrievable context: the originating alert (decision_requested on
    record), the ADR-023 threshold counterfactual, and the current mute
    label. Muted rows are included and labeled — never filter-excluded.
    """
    now = now_iso or utcnow_iso()
    mutes = {m.fingerprint: m for m in active_mutes(log, now_iso=now)}
    rows: list[dict] = []
    for ev in log.events_of_type("decision_made", limit=limit):
        if ev["seq"] <= since_seq:
            continue
        body = json.loads(ev["body"])
        if body.get("disposition") not in SUPPRESSION_CLASS:
            continue  # page_now / page_business_hours / passthrough /
                      # folded (D3: folded is explicitly NOT suppression)
        mute = mutes.get(ev["fingerprint"])
        context = log.events_for_alert(ev["alert_id"], limit=5)
        rows.append({
            "seq": ev["seq"],
            "ts": ev["ts"],
            "alert_id": ev["alert_id"],
            "fingerprint": ev["fingerprint"],
            "episode_id": ev["episode_id"],
            "disposition": body["disposition"],
            "label": render_disposition_label(body["disposition"], mute),
            "muted": mute is not None,
            "mute": ({"reason": mute.reason, "attestor": mute.attestor,
                      "expires_at": mute.expires_at} if mute else None),
            # ADR-023 counterfactual rides with every muted suppression.
            "threshold_counterfactual": body.get("threshold_counterfactual"),
            "links": body.get("links"),
            # Retrievability proof: the originating request is on record.
            "context_retrievable": len(context) > 0,
        })
    return rows


# ---------------------------------------------------------------- weekly ritual

def build_mute_section(log: EventLog, *, owner: str = MUTE_REVIEW_OWNER,
                       week_label: str = "",
                       now_iso: str | None = None) -> dict:
    """Build the shadow report's "What we muted" section (ADR-007 d).

    Pure function of the event log. The section is rendered every week —
    muted rows visible by default, never filter-excluded.
    """
    now = now_iso or utcnow_iso()
    events = _mute_events(log)
    active = active_mutes(log, now_iso=now)
    by_type: dict[str, int] = {}
    for row in events:
        by_type[row["type"]] = by_type.get(row["type"], 0) + 1
    return {
        "owner": owner,
        "owner_is_placeholder": owner == MUTE_REVIEW_OWNER,
        "week_label": week_label,
        "active_mutes": [
            {"fingerprint": m.fingerprint, "reason": m.reason,
             "attestor": m.attestor, "ttl_s": m.ttl_s,
             "muted_at": m.muted_at, "expires_at": m.expires_at}
            for m in active
        ],
        "counts": {
            "active": len(active),
            "applied": by_type.get("mute_applied", 0),
            "appealed": by_type.get("mute_appealed", 0),
            "lifted": by_type.get("mute_lifted", 0),
            "expired": by_type.get("mute_expired", 0),
        },
        "review_checklist": list(MUTE_REVIEW_CHECKLIST),
    }


def empty_mute_section(*, owner: str = MUTE_REVIEW_OWNER,
                       week_label: str = "") -> dict:
    """The section shape with zero mutes — still rendered, still reviewed."""
    return {
        "owner": owner,
        "owner_is_placeholder": owner == MUTE_REVIEW_OWNER,
        "week_label": week_label,
        "active_mutes": [],
        "counts": {"active": 0, "applied": 0, "appealed": 0,
                   "lifted": 0, "expired": 0},
        "review_checklist": list(MUTE_REVIEW_CHECKLIST),
    }


# ---------------------------------------------------------------- self-check

# Mute-writing entry points covered by the no-auto-mute scan (ADR-007 c).
_MUTE_WRITERS = ("apply_mute", "appeal_mute", "lift_mute")


def _call_sites_in_source(source: str) -> list[dict]:
    """Find real CALL sites of the mute writers via tokenize.

    tokenize (not regex) so docstrings, comments, and the `def` lines are
    never mistaken for calls — a regex scan would be theater.
    Returns [{name, line, attestor}] where attestor is the literal passed
    as attestor= (None if not a literal on the call).
    """
    import io
    import tokenize as _tok
    sites: list[dict] = []
    try:
        toks = [t for t in _tok.generate_tokens(io.StringIO(source).readline)]
    except (_tok.TokenError, IndentationError, SyntaxError):
        return sites
    sig = [t for t in toks
           if t.type not in (_tok.NL, _tok.NEWLINE, _tok.COMMENT,
                             _tok.INDENT, _tok.DEDENT, _tok.ENDMARKER)]
    for i, t in enumerate(sig):
        if t.type != _tok.NAME or t.string not in _MUTE_WRITERS:
            continue
        nxt = sig[i + 1] if i + 1 < len(sig) else None
        if not (nxt and nxt.type == _tok.OP and nxt.string == "("):
            continue
        prev = sig[i - 1] if i > 0 else None
        if prev and prev.type == _tok.NAME and prev.string == "def":
            continue  # the definition, not a call
        # Walk to the matching ')' collecting attestor= literal.
        depth = 0
        attestor: str | None = None
        j = i + 1
        while j < len(sig):
            u = sig[j]
            if u.type == _tok.OP:
                if u.string == "(":
                    depth += 1
                elif u.string == ")":
                    depth -= 1
                    if depth == 0:
                        break
                elif u.string == "=" and depth == 1:
                    p = sig[j - 1] if j > 0 else None
                    v = sig[j + 1] if j + 1 < len(sig) else None
                    if (p and p.type == _tok.NAME and p.string == "attestor"
                            and v and v.type == _tok.STRING):
                        attestor = v.string.strip("'\"")
            j += 1
        sites.append({"name": t.string, "line": t.start[0],
                      "attestor": attestor})
    # Direct mute_applied appends outside this module (bypass attempt).
    for i, t in enumerate(sig):
        if t.type != _tok.NAME or t.string != "append_event":
            continue
        nxt = sig[i + 1] if i + 1 < len(sig) else None
        arg = sig[i + 2] if i + 2 < len(sig) else None
        if (nxt and nxt.type == _tok.OP and nxt.string == "("
                and arg and arg.type == _tok.STRING
                and arg.string.strip("'\"") == "mute_applied"):
            sites.append({"name": "append_event:mute_applied",
                          "line": t.start[0], "attestor": None})
    return sites


def scan_for_unattested_mute_paths(repo_root: str) -> dict:
    """Codebase scan backing the no-auto-mute proof (ADR-007 c).

    Walks the repo's Python sources for mute-writer call sites
    (apply_mute / appeal_mute / lift_mute) and classifies each:

      * under tests/ (or the defining module platform/server/mute.py
        itself) — compliant by construction;
      * elsewhere — compliant ONLY with an explicit human attestor=
        literal that is not blocklisted.

    Also flags direct append_event("mute_applied") calls outside this
    module — the log layer rejects automated attestors, but the call
    deserves review.

    Returns {"call_sites": [...], "violations": [...]}.

    Documented scan procedure (re-run by tests/test_mute_governance.py):
      1. walk repo_root/{src,platform,tests,scripts,demo,eval,chiefs,
         rehearsal, ops} for *.py (skip __pycache__);
      2. tokenize each file; record real call sites of the mute writers;
      3. a call site is COMPLIANT iff it lives under tests/, is this
         module's own write path, or passes attestor= with a literal not
         in HUMAN_ATTESTOR_BLOCKLIST (and not containing "auto").
    """
    scan_dirs = ("src", "platform", "tests", "scripts", "demo", "eval",
                 "chiefs", "rehearsal", "ops")
    call_sites: list[dict] = []
    violations: list[dict] = []
    for d in scan_dirs:
        base = os.path.join(repo_root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x != "__pycache__"]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                rel = os.path.relpath(path, repo_root).replace(os.sep, "/")
                try:
                    with open(path, encoding="utf-8") as f:
                        source = f.read()
                except OSError:
                    continue
                in_tests = "/tests/" in rel or rel.startswith("tests/")
                is_self = rel == "platform/server/mute.py"
                for site in _call_sites_in_source(source):
                    entry = {"file": rel, **site, "in_tests": in_tests}
                    call_sites.append(entry)
                    if in_tests or is_self:
                        continue
                    att = site["attestor"]
                    bad = (att is None
                           or att.strip().lower() in HUMAN_ATTESTOR_BLOCKLIST
                           or "auto" in att.strip().lower())
                    if bad:
                        violations.append(
                            {**entry, "reason":
                             "mute-writer call outside tests/ without an "
                             "explicit human attestor literal"})
    return {"call_sites": call_sites, "violations": violations}


# attestor must be keyword-only with no default — assert the contract here
# so a future signature edit breaks loudly instead of silently opening an
# auto-mute path.
_assert_sig = inspect.signature(apply_mute)
_assert_param = _assert_sig.parameters.get("attestor")
assert _assert_param is not None and _assert_param.kind is inspect.Parameter.KEYWORD_ONLY \
    and _assert_param.default is inspect.Parameter.empty, \
    "apply_mute.attestor must stay required keyword-only (ADR-007: no auto-mute)"
del _assert_sig, _assert_param
