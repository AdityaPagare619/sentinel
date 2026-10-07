"""Track-2 judge adapter — the REAL Jev judge in simulation (contract C2).

Aditya's order: even in simulation the REAL Jev judge is called — real
judgments, real latencies, real race outcomes — cost-capped, server-side
only, key never logged/committed/exposed. PagerDuty is structurally FakePD
in sim (audit P0, ruling X-B): enforced by sentinel/sim_pd_guard.py
(loopback sink + SIM-FAKE key, boot refusal on any real endpoint/key) —
not by this module, and not by a toggle.

What this module provides (contract C2, consumed by Tracks 6/7/8):

  * ``build_sim_judge()`` — resolves the real Jev key via
    ``integrations.resolve_jev_key()`` (user store → TYPESAFE_API_KEY env)
    and builds a spend-capped real ``SystemOneClient``. Absent key →
    ``FakeJev``, a deterministic mock honestly labeled "simulated judge"
    (model ``fakejev-0.0.0``) — never fake-real.
  * ``SimJudge.judge()`` — the clean ``judge()`` seam (Track 5 shares this
    instead of duplicating the client + spend infrastructure). Runs the
    vendor call through the REAL race (``race.RaceRunner``: Jev vs the
    bounded timer) and returns the contract ``JudgeResult`` dict.
  * ``JevSpendTracker`` — thread-safe spend accounting behind
    ``GET /api/v1/jev/spend``: ``{session_usd, budget_usd, calls,
    blocked}``. Budget from ``--jev-budget-usd`` (default 0.50). On
    exhaustion the judge degrades to deterministic (ZERO Jev calls),
    honestly labeled "Jev budget exhausted — deterministic mode".
  * ``judge_result()`` — the validated ``JudgeResult`` builder:
    ``{judgment, confidence (ORDINAL 0..1, never a probability),
    latency_ms, source (jev|timer|deterministic), model_version,
    cost_usd (0.0 when source != "jev")}``.

CONTROL PRINCIPLE (every Jev use must pass it): the deterministic gate
owns the decision; Jev advises. A Jev error/timeout/overload — or a spent
budget — fails OPEN (page), never silent. ``JevBudgetExhausted`` is a
``JevError`` subclass precisely so the race's existing
``error_passthrough`` path handles it: no new failure semantics.

KEY HYGIENE (non-negotiable): the key NEVER leaves the server process.
It is never logged, never in responses, never in SSE payloads, never in
error messages, never in the browser. Only the key SOURCE
("user"/"env"/"unconfigured") is ever emitted. The
``sanitize_error()`` boundary in integrations.py strips configured key
values from forwarded error strings.

COST MODEL: Jev bills $0.042 per 1M input tokens; output is free
(standing vendor constant). ``cost_usd`` per call =
``input_tokens * 0.042 / 1e6`` from the response usage block. Calls that
raise (timeout/overload/4xx) record 0.0 cost — we have no usage data for
them, and inventing one would be dishonest. Calls blocked by the budget
make ZERO Jev calls and are not counted in ``calls``.
"""

from __future__ import annotations

import os
import sys
import threading
from time import monotonic

from .client import (Answer, DecisionResponse, FLOATING_MODEL_ALIAS, JevError,
                     SystemOneClient)
from .integrations import resolve_jev_key
from . import race as _race

#: Jev pricing: $0.042 per 1M input tokens; output free (vendor constant).
JEV_PRICE_PER_INPUT_TOKEN_USD = 0.042 / 1_000_000

#: Default sim spend budget (USD) — ``--jev-budget-usd`` overrides.
DEFAULT_JEV_BUDGET_USD = 0.50

#: Env override for the budget when the CLI flag is not passed.
JEV_BUDGET_ENV = "SENTINEL_JEV_BUDGET_USD"

#: Env flag selecting the sim judge path in build_pipeline_from_env.
SIM_MODE_ENV = "SENTINEL_SIM"

#: The honest label for the deterministic mock — never fake-real.
FAKE_JUDGE_LABEL = "simulated judge"

#: FakeJev's pinned model id — test doubles float nothing (ADR-015 spirit).
FAKE_JUDGE_MODEL = "fakejev-0.0.0"

#: Human label shown when the budget is spent.
BUDGET_EXHAUSTED_LABEL = "Jev budget exhausted — deterministic mode"

#: Human label shown when the timer beat the judge.
TIMER_WON_LABEL = "timer won — paged because the judge was too slow"

JUDGMENTS = frozenset({"page", "suppress"})
SOURCES = frozenset({"jev", "timer", "deterministic"})


# ---------------------------------------------------------------------------
# Budget errors — a JevError so the race's error_passthrough path handles it.
# ---------------------------------------------------------------------------

class JevBudgetExhausted(JevError):
    """Raised BEFORE any network I/O when the spend budget is exhausted.

    A ``JevError`` subclass on purpose: the race converts it to
    ``error_passthrough`` and the gate fails open (page) — the deterministic
    safe default. Never retried, never silent.
    """


# ---------------------------------------------------------------------------
# FakeJev — the deterministic mock for key-absent sim (honestly labeled).
# ---------------------------------------------------------------------------

class FakeJev:
    """Deterministic stand-in for the Jev wire client. Used ONLY when no
    key resolves (contract C2: absent key → FakeJev).

    Honesty rules (never fake-real):
      * ``model`` is ``fakejev-0.0.0`` — no real vendor id, nothing floatable.
      * ``judge_label`` is "simulated judge" — surfaced in logs/payloads.
      * Every answer is ``cannot_determine`` with no confidence — the gate's
        "uncertainty pages" rule turns this into passthrough by construction,
        so the mock can never suppress (a mock that suppresses would be a
        lie about what simulation proves).
      * Zero network I/O, ever. ``calls`` records every invocation for
        test assertions.

    ``timeout_s`` is a positive number so ``RaceRunner``'s fail-closed
    construction check passes on the mock exactly like the real client.
    """

    model = FAKE_JUDGE_MODEL
    judge_label = FAKE_JUDGE_LABEL
    timeout_s = 30.0

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def decide(self, state, questions) -> DecisionResponse:
        with self._lock:
            self.calls.append({"state": state, "questions": questions})
        answers = {}
        for qid, q in (questions or {}).items():
            qtype = str(q.get("type", "choice"))
            answers[str(qid)] = Answer(
                qid=str(qid), qtype=qtype, choice="cannot_determine",
                noul=None, probabilities={}, confidence=None)
        return DecisionResponse(model=self.model, answers=answers,
                                input_tokens=0)

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self.calls)


# ---------------------------------------------------------------------------
# Spend tracker — the state behind GET /api/v1/jev/spend.
# ---------------------------------------------------------------------------

class JevSpendTracker:
    """Thread-safe Jev spend accounting with a hard budget cap.

    ``blocked`` latches the first time ``session_usd >= budget_usd`` and
    never unlatches within the process lifetime (the budget is a session
    budget; raising it is a restart, not a runtime toggle — a runtime
    raise would let a runaway sim spend past the operator's cap).
    A budget of 0.0 (or negative) starts blocked: zero Jev calls, ever.
    """

    def __init__(self, budget_usd: float = DEFAULT_JEV_BUDGET_USD) -> None:
        try:
            budget = float(budget_usd)
        except (TypeError, ValueError):
            # Fail-CLOSED on garbage: a misconfigured budget authorizes
            # no spend at all (the env/CLI layer warns and falls back to
            # the default separately — see budget_usd_from_env).
            budget = 0.0
        if not (budget > 0):
            # Zero/negative: no spend is authorized, ever.
            budget = 0.0
        self._lock = threading.Lock()
        self._budget_usd = budget
        self._session_usd = 0.0
        self._calls = 0
        self._blocked = self._session_usd >= self._budget_usd
        self._exhaustion_warned = False

    def warn_exhausted_once(self) -> None:
        """Log the honest exhaustion label exactly once per process.

        Called on the blocked path (where no Jev call is made) so the
        "Jev budget exhausted — deterministic mode" label is visible in
        the server log even when the budget started at 0 (where no
        record_call ever fires the latch message above).
        """
        with self._lock:
            if self._exhaustion_warned:
                return
            self._exhaustion_warned = True
        sys.stderr.write(
            "[sentinel] " + BUDGET_EXHAUSTED_LABEL + " — the deterministic "
            "fail-open path owns every decision from here on; zero Jev "
            "calls will be made this session.\n")

    # ------------------------------------------------------------- accounting

    def try_begin_call(self) -> bool:
        """Pre-call gate. True iff a Jev call is authorized right now.

        When this returns False the caller must make ZERO Jev calls and
        take the deterministic path instead.
        """
        with self._lock:
            return not self._blocked

    def record_call(self, cost_usd: float) -> None:
        """Record one completed wire call. ``cost_usd`` 0.0 when the call
        raised (no usage data — we never invent cost)."""
        try:
            cost = max(float(cost_usd), 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        with self._lock:
            self._calls += 1
            self._session_usd += cost
            if self._session_usd >= self._budget_usd:
                if not self._blocked:
                    sys.stderr.write(
                        "[sentinel] Jev spend budget exhausted "
                        f"(${self._session_usd:.6f} >= ${self._budget_usd:.2f}) "
                        "— judge degraded to deterministic mode; "
                        "zero further Jev calls this session.\n")
                self._blocked = True

    @property
    def blocked(self) -> bool:
        with self._lock:
            return self._blocked

    @property
    def session_usd(self) -> float:
        with self._lock:
            return self._session_usd

    def snapshot(self) -> dict:
        """The ``GET /api/v1/jev/spend`` body. Contains NO key material —
        only amounts, counts, and the blocked flag."""
        with self._lock:
            return {
                "session_usd": round(self._session_usd, 6),
                "budget_usd": round(self._budget_usd, 6),
                "calls": self._calls,
                "blocked": self._blocked,
            }


# ---------------------------------------------------------------------------
# Spend-capped client — the ONLY real-Jev call site in sim.
# ---------------------------------------------------------------------------

class SpendCappedJevClient:
    """Wraps a real ``SystemOneClient`` with the spend gate.

    ``decide()`` checks the tracker BEFORE any network I/O: when blocked it
    raises ``JevBudgetExhausted`` (a ``JevError`` → race error_passthrough
    → deterministic fail-open page). Otherwise it delegates and records
    the call cost from the response's ``input_tokens``.

    ``timeout_s`` mirrors the inner client so ``RaceRunner``'s fail-closed
    construction check sees the real bound.
    """

    def __init__(self, inner: SystemOneClient,
                 tracker: JevSpendTracker) -> None:
        self._inner = inner
        self._tracker = tracker

    @property
    def inner(self) -> SystemOneClient:
        return self._inner

    @property
    def tracker(self) -> JevSpendTracker:
        return self._tracker

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def timeout_s(self) -> float:
        return self._inner.timeout_s

    def decide(self, state, questions) -> DecisionResponse:
        if not self._tracker.try_begin_call():
            self._tracker.warn_exhausted_once()
            raise JevBudgetExhausted(
                f"{BUDGET_EXHAUSTED_LABEL} — refusing the Jev call; "
                "the deterministic fail-open path owns this decision.")
        try:
            resp = self._inner.decide(state, questions)
        except Exception:
            # The call hit the wire (or tried to) but produced no usable
            # response: count it, record 0.0 cost (no usage data), re-raise
            # for the race to fail open on.
            self._tracker.record_call(0.0)
            raise
        self._tracker.record_call(
            _cost_for_response(resp))
        return resp


def _cost_for_response(resp: DecisionResponse) -> float:
    """USD cost of one Jev call from the response usage block."""
    try:
        tokens = int(getattr(resp, "input_tokens", 0) or 0)
    except (TypeError, ValueError):
        tokens = 0
    return max(tokens, 0) * JEV_PRICE_PER_INPUT_TOKEN_USD


# ---------------------------------------------------------------------------
# Factory — resolve the real key, build the sim judge.
# ---------------------------------------------------------------------------

def budget_usd_from_env(explicit: float | None = None) -> float:
    """Resolve the sim Jev budget: explicit flag > env > default."""
    if explicit is not None:
        return explicit
    raw = os.environ.get(JEV_BUDGET_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_JEV_BUDGET_USD
    try:
        return float(raw)
    except (TypeError, ValueError):
        sys.stderr.write(
            f"[sentinel] WARNING: {JEV_BUDGET_ENV}={raw!r} is not a number; "
            f"using default ${DEFAULT_JEV_BUDGET_USD:.2f}.\n")
        return DEFAULT_JEV_BUDGET_USD


def _model_pin_from_env() -> str | None:
    """Read the ADR-015 model pin (pinning.json via SENTINEL_FRESHNESS_BUNDLE).

    Local copy of receiver._model_pin_from_env: this module must not import
    receiver (import weight + cycle risk), and the pin is load-bearing for
    the real path.
    """
    bundle_dir = os.environ.get("SENTINEL_FRESHNESS_BUNDLE")
    if not bundle_dir:
        return None
    try:
        import json
        with open(os.path.join(bundle_dir, "pinning.json"),
                  "r", encoding="utf-8") as fh:
            data = json.load(fh)
        pin = str(data.get("pinned_model_version", "") or "").strip()
        return pin or None
    except (OSError, ValueError) as exc:
        sys.stderr.write(
            f"[sentinel] WARNING: pinning.json unreadable ({exc}) — "
            "treating the model pin as missing (ADR-015).\n")
        return None


def build_sim_judge(*, budget_usd: float | None = None,
                    store=None) -> tuple:
    """Build the sim judge: ``(client, tracker, mode)``.

    Resolves the real Jev key via ``resolve_jev_key()`` (user store →
    TYPESAFE_API_KEY env). Key present → real ``SystemOneClient`` wrapped
    in ``SpendCappedJevClient`` (``mode="jev-real"``). Key absent →
    ``FakeJev`` (``mode="fakejev"``), honestly labeled "simulated judge".

    The key VALUE is never logged — only the source. No network I/O
    happens at build time.
    """
    budget = budget_usd_from_env(budget_usd)
    tracker = JevSpendTracker(budget)
    api_key, jev_source = resolve_jev_key(store)
    if api_key:
        pinned = _model_pin_from_env()
        if pinned:
            inner = SystemOneClient(api_key=api_key, model=pinned)
            sys.stderr.write(
                f"[sentinel] Jev judge: REAL (key source={jev_source}, "
                f"model pinned: {pinned}); spend budget ${budget:.2f}; "
                "real judgments, real latencies, real race outcomes.\n")
        else:
            sys.stderr.write(
                "[sentinel] WARNING: no model pin available "
                "(SENTINEL_FRESHNESS_BUNDLE unset or pinning.json "
                "unreadable) — the sim Jev client floats 'jev-latest' as "
                "an EXPLICIT, loudly-warned opt-out (ADR-015); "
                "model_drift detection is INACTIVE. "
                "Set the bundle before trusting sim verdicts.\n")
            inner = SystemOneClient(api_key=api_key,
                                    model=FLOATING_MODEL_ALIAS,
                                    allow_floating_model=True)
            sys.stderr.write(
                f"[sentinel] Jev judge: REAL (key source={jev_source}); "
                f"spend budget ${budget:.2f}.\n")
        return SpendCappedJevClient(inner, tracker), tracker, "jev-real"
    sys.stderr.write(
        "[sentinel] Jev judge: FakeJev (simulated judge) — no key resolved "
        f"(source={jev_source}); NO real vendor calls will be made; every "
        "verdict is deterministic fail-open. This is a simulated judge, "
        "never the real Jev.\n")
    return FakeJev(), tracker, "fakejev"


# ---------------------------------------------------------------------------
# JudgeResult — the contract dict (C2).
# ---------------------------------------------------------------------------

def is_real_vendor_client(client) -> bool:
    """True iff ``client`` makes real vendor calls (spend-capped real Jev).

    ``FakeJev`` / ``MockSystemOneClient`` / test doubles are NOT real
    vendor clients — a race their "inference" wins must never be labeled
    ``source: "jev"`` (never fake-real).
    """
    return isinstance(client, SpendCappedJevClient)


def source_from_budget_outcome(budget_outcome: str) -> str:
    """Map the race's durable-truth vocabulary to the C2 ``source``."""
    if budget_outcome == _race.ANSWERED_IN_TIME:
        return "jev"
    if budget_outcome in _race.TIMER_WIN_OUTCOMES:
        return "timer"
    # error_passthrough, failopen_stepped, structural_passthrough,
    # reaper_redrive: the deterministic machinery decided.
    return "deterministic"


def judge_result(*, judgment: str, confidence: float, latency_ms: float,
                 source: str, model_version: str,
                 cost_usd: float) -> dict:
    """Build the validated contract ``JudgeResult`` dict.

    ``confidence`` is ORDINAL 0..1 — never presented as a probability.
    ``cost_usd`` is 0.0 whenever ``source != "jev"`` (the vendor may still
    bill a late detached call; that spend lands in the session meter, not
    in the decision that the judge was too slow to make).
    """
    if judgment not in JUDGMENTS:
        raise ValueError(f"judgment must be one of {sorted(JUDGMENTS)} "
                         f"(got {judgment!r})")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {sorted(SOURCES)} "
                         f"(got {source!r})")
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        raise ValueError(f"confidence must be numeric 0..1 (got "
                         f"{confidence!r})")
    if not 0.0 <= conf <= 1.0:
        raise ValueError(f"confidence must be 0..1 (got {conf})")
    try:
        lat = max(float(latency_ms), 0.0)
    except (TypeError, ValueError):
        raise ValueError(f"latency_ms must be numeric (got "
                         f"{latency_ms!r})")
    if source != "jev" and cost_usd:
        raise ValueError("cost_usd must be 0.0 when source != 'jev' "
                         f"(got {cost_usd})")
    try:
        cost = max(float(cost_usd), 0.0)
    except (TypeError, ValueError):
        raise ValueError(f"cost_usd must be numeric (got {cost_usd!r})")
    return {
        "judgment": judgment,
        "confidence": conf,
        "latency_ms": lat,
        "source": source,
        "model_version": str(model_version),
        "cost_usd": round(cost, 6),
    }


def _advisory_disposition(resp: DecisionResponse) -> tuple[str, float]:
    """Advisory mapping of a Jev answer → (judgment, ordinal confidence).

    Used ONLY by the standalone ``SimJudge.judge()`` seam (Track 5
    advisory consumers). The real paging decision always belongs to the
    gate kernel; this mapping is documented as advisory.

    "suppress" requires the model's own disposition answer to be
    "suppress" — anything else, including cannot_determine or a missing
    answer, fails open to "page" (uncertainty pages).
    """
    q_disp = (resp.answers or {}).get("disposition")
    choice = getattr(q_disp, "choice", None)
    conf = getattr(q_disp, "confidence", None)
    try:
        conf_f = float(conf) if conf is not None else 0.0
    except (TypeError, ValueError):
        conf_f = 0.0
    conf_f = min(max(conf_f, 0.0), 1.0)
    if choice == "suppress":
        # 0.0 here means "the judge reported no confidence" — an honest
        # signal, not an invented one.
        return "suppress", conf_f
    return "page", conf_f


# ---------------------------------------------------------------------------
# SimJudge — the clean judge() seam (Tracks 5/6/7/8 consume this).
# ---------------------------------------------------------------------------

class SimJudge:
    """Owns the spend-capped client + the real race; exposes ``judge()``.

    ``judge()`` runs the vendor call through ``race.RaceRunner`` — the
    REAL race: Jev vs the bounded timer. Timer wins are real (``source:
    "timer"`` — the page happened BECAUSE the judge was too slow); late
    Jev answers are powerless (they become shadow evidence inside the
    race, never override the returned result).

    Budget exhaustion → the deterministic record immediately, with ZERO
    Jev calls: ``{judgment: "page", confidence: 1.0, source:
    "deterministic", cost_usd: 0.0}`` ("Jev budget exhausted —
    deterministic mode").

    FakeJev mode (no key): the race still runs against the mock, but the
    record's ``source`` is forced to ``"deterministic"`` — a simulated
    judge is never labeled "jev".

    ``decide_advisory()`` is the raw-decide seam for Track 5's advisory
    directions (plain-language explanations, storm summaries, ...): it
    shares the same client + spend tracker, makes no race, gates nothing.
    """

    def __init__(self, client, tracker: JevSpendTracker, *,
                 mode: str = "jev-real",
                 race_config=None,
                 clock=monotonic,
                 metrics=None) -> None:
        self._client = client
        self._tracker = tracker
        self._mode = mode
        self._runner = _race.RaceRunner(client, race_config, clock=clock,
                                        metrics=metrics)

    @property
    def tracker(self) -> JevSpendTracker:
        return self._tracker

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def runner(self):
        return self._runner

    def _model_label(self) -> str:
        return getattr(self._client, "model",
                       getattr(self._client, "judge_label", "unknown"))

    # ------------------------------------------------------------ the seam

    def judge(self, *, alert, state, questions,
              input_sha256: str, episode_id: str | None = None) -> dict:
        """Run one judged race; return the contract ``JudgeResult`` dict.

        Never raises for judge failures — they become the deterministic
        fail-open record (page). ``alert`` is opaque to the judge (only
        its identity is used for late-answer bookkeeping).
        """
        if self._tracker.blocked:
            # Budget spent: ZERO Jev calls from here on. The deterministic
            # safe default owns the decision.
            self._tracker.warn_exhausted_once()
            return judge_result(
                judgment="page", confidence=1.0, latency_ms=0.0,
                source="deterministic", model_version=self._model_label(),
                cost_usd=0.0)
        mark = self._tracker.session_usd
        try:
            outcome = self._runner.run(
                alert=alert, state=state, questions=questions,
                input_sha256=input_sha256, episode_id=episode_id)
        except Exception:
            # The race never raises for client failures (they become
            # error_passthrough); this is the belt-and-suspenders for a
            # broken runner — fail open, never silent, never a traceback
            # to the caller.
            sys.stderr.write("[sentinel] sim judge race raised; "
                             "deterministic fail-open (page)\n")
            return judge_result(
                judgment="page", confidence=1.0, latency_ms=0.0,
                source="deterministic", model_version=self._model_label(),
                cost_usd=0.0)
        cost = round(self._tracker.session_usd - mark, 6)

        if (outcome.winner == _race.CLAIMED_BY_INFERENCE
                and outcome.result is not None and outcome.result.ok):
            resp = outcome.result.response
            judgment, conf = _advisory_disposition(resp)
            source = "jev" if self._mode == "jev-real" else "deterministic"
            if source != "jev":
                cost = 0.0
            return judge_result(
                judgment=judgment, confidence=conf,
                latency_ms=outcome.result.latency_ms,
                source=source, model_version=self._model_label(),
                cost_usd=cost)

        if outcome.winner == _race.CLAIMED_BY_INFERENCE:
            # Inference claimed but the call failed: deterministic
            # fail-open (page). The race already counted the spend.
            return judge_result(
                judgment="page", confidence=1.0,
                latency_ms=(outcome.result.latency_ms
                            if outcome.result is not None else 0.0),
                source="deterministic", model_version=self._model_label(),
                cost_usd=0.0)

        # Timer (or the gate backstop) won: the page happened BECAUSE the
        # judge was too slow. Real timer win, honestly labeled.
        return judge_result(
            judgment="page", confidence=1.0,
            latency_ms=(outcome.timer_fired_at_ms
                        if outcome.timer_fired_at_ms is not None else 0.0),
            source="timer", model_version=self._model_label(),
            cost_usd=0.0)

    def decide_advisory(self, state, questions):
        """Raw vendor decide() for Track-5 advisory directions.

        Shares the spend cap (raises ``JevBudgetExhausted`` when blocked);
        gates nothing, decides nothing. Never use on the paging path.
        """
        return self._client.decide(state, questions)

    def close(self) -> None:
        self._runner.close()


def build_sim_judge_pair(*, budget_usd: float | None = None,
                         store=None,
                         race_config=None) -> SimJudge:
    """Build a ready-to-use ``SimJudge`` (client + tracker + race)."""
    client, tracker, mode = build_sim_judge(budget_usd=budget_usd,
                                            store=store)
    return SimJudge(client, tracker, mode=mode, race_config=race_config)
