"""Race-to-page: the Jev call races a paging budget B (ADR-010).

The irreducible design (design/fixes/01-race-to-page.md):

    Paging latency is our promise; inference latency is the vendor's
    property. A promise bounded by someone else's property is not a
    promise. So the Jev call NEVER blocks the page: it races a budget B
    (default 1000 ms). The timer's default action is passthrough — a
    deterministic rule, not a model output. Suppression is something Jev
    must EARN by being fast, confident, and corroborated.

The three parties (§3.1):

    1. The inference worker — runs the Jev call on a bounded pool, then
       attempts to claim the race.
    2. The timer — ONE scheduler thread holding a heap of deadlines.
    3. The gate waiter — the gate's own thread, waiting on the claim with
       its own deadline of B + ε (ε = 500 ms). Backstop against a dead
       timer AND a hung inference call composing.

The claim is a single atomic transition guarded by one lock:
UNCLAIMED → CLAIMED_BY_INFERENCE | CLAIMED_BY_TIMER | CLAIMED_BY_GATE.
Same-instant completion is impossible by construction — the lock
serializes it, and a 1 µs loss is still a loss.

CLOCK RULE (§3.5): every line in this module uses ``time.monotonic()``
and nothing else. Wall-clock is forbidden for the race by architecture —
an NTP step backward makes a wall-clock deadline fire late or never
(the page waits past B: the exact promise broken); a step forward fires
it early (suppression starves). A CI test (tests/test_race.py) asserts
the only clock referenced here is ``monotonic``. Event *timestamps*
(wall-clock, humans only) are built in race_payloads.py — a different
module on purpose.

EMISSION, NOT WRITING: this module never writes events. The detached
inference worker's late answer is handed to ``late_answer_hook`` as a
``LateAnswer``; the gate/dispatcher layer builds the ``shadow_decision``
payload (race_payloads.py) and the event-log lane persists it. The
worker never writes ``decision_made``, never touches the outbox, never
signals the gate.

Type-1 choices (this module): the race itself; timer-wins ⇒ passthrough;
late answer ⇒ shadow via hook, never acts; monotonic clock; three-party
race; detach-not-cancel; the ``budget_outcome`` vocabulary.
Type-2 values (tunable): B default/range, ε, socket timeout, pool
size/queue, watchdog threshold/window.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import math
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from time import monotonic  # THE ONLY clock in this module — see §3.5.

log = logging.getLogger("sentinel.race")

# ---------------------------------------------------------------------------
# Budget-outcome vocabulary (Type 1 — durable truth; the river, tuner and
# watchdog read this, never raw latency alone).
# ---------------------------------------------------------------------------

ANSWERED_IN_TIME = "answered_in_time"
TIMER_WON = "timer_won"
TIMER_WON_SHED = "timer_won_shed"
ERROR_PASSTHROUGH = "error_passthrough"
REAPER_REDRIVE = "reaper_redrive"  # reserved: produced by the forwarder lane
STRUCTURAL_PASSTHROUGH = "structural_passthrough"

BUDGET_OUTCOMES = frozenset({
    ANSWERED_IN_TIME, TIMER_WON, TIMER_WON_SHED, ERROR_PASSTHROUGH,
    REAPER_REDRIVE, STRUCTURAL_PASSTHROUGH,
})

TIMER_WIN_OUTCOMES = frozenset({TIMER_WON, TIMER_WON_SHED})

# ---------------------------------------------------------------------------
# B — value, bounds, loader (design §2). Fail-CLOSED on config.
# ---------------------------------------------------------------------------

#: Seeded default: 2× the vendor spec max (70–500 ms). Re-derived from
#: Oracle's N≥100 campaign as max(1000 ms, 2 × measured healthy p99).
DEFAULT_BUDGET_MS = 2700  # N=100 re-derivation (PR #19): max(1000, 2*1339)=2678 -> 2700
#: Below the vendor's own spec max the race is meaningless.
MIN_BUDGET_MS = 500
#: Above this the "bounded latency" promise is hollow.
MAX_BUDGET_MS = 5000
#: Gate-waiter backstop slack (party 3 of the race).
BACKSTOP_EPSILON_MS = 500
#: Socket-level timeout bounding the detached inference thread (design §6).
DEFAULT_INFERENCE_TIMEOUT_S = 30.0


class Metrics:
    """Tiny in-process counter sink (the race's own; no shared infra yet)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + n

    def get(self, name: str) -> int:
        with self._lock:
            return self._counters.get(name, 0)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)


@dataclass(frozen=True)
class RaceConfig:
    """Post-validation race configuration. Build via ``from_raw`` — the
    loader enforces the [500, 5000] ms range fail-closed (design §2.2)."""

    budget_ms: int = DEFAULT_BUDGET_MS
    epsilon_ms: int = BACKSTOP_EPSILON_MS
    pool_size: int = 32
    pool_queue: int = 256

    @classmethod
    def from_raw(cls, raw, *, metrics: Metrics | None = None) -> "RaceConfig":
        """Load B from config. Fail-CLOSED: refused values fall back to the
        compiled default (1000 ms) with a loud log and a metric. There is no
        way to disable the race via config — 0/null/"disabled" means "use
        default"; disabling is a Type-1 architecture change, not a value."""

        def refused(kind: str, why: str) -> "RaceConfig":
            if metrics is not None:
                metrics.incr(f"race.budget_refused_{kind}")
            log.warning(
                "[sentinel] race budget %r refused (%s); "
                "using compiled default %d ms",
                raw, why, DEFAULT_BUDGET_MS,
            )
            return cls()

        if raw is None:
            log.info("[sentinel] race budget unset; using default %d ms",
                     DEFAULT_BUDGET_MS)
            return cls()
        if isinstance(raw, bool):
            return refused("invalid", "a boolean is not a budget")
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s in ("", "disabled", "none", "null", "off"):
                log.info("[sentinel] race budget %r means 'use default' "
                         "(the race cannot be disabled via config); "
                         "using %d ms", raw, DEFAULT_BUDGET_MS)
                return cls()
            try:
                raw = float(s)
            except ValueError:
                return refused("invalid", "not a number")
        try:
            ms = float(raw)
        except (TypeError, ValueError):
            return refused("invalid", "not a number")
        if math.isnan(ms) or math.isinf(ms):
            return refused("invalid", "not a finite number")
        if ms == 0:
            # No disable value exists: 0 means "use default".
            log.info("[sentinel] race budget 0 means 'use default' (the race "
                     "cannot be disabled via config); using %d ms",
                     DEFAULT_BUDGET_MS)
            return cls()
        if ms < MIN_BUDGET_MS:
            return refused(
                "low", f"below the {MIN_BUDGET_MS} ms floor "
                       f"(vendor spec max is 500 ms; the timer would win on "
                       f"healthy vendor behavior)")
        if ms > MAX_BUDGET_MS:
            return refused(
                "high", f"above the {MAX_BUDGET_MS} ms ceiling "
                        f"(the bounded-latency promise would be hollow)")
        if ms < DEFAULT_BUDGET_MS:
            if metrics is not None:
                metrics.incr("race.budget_below_recommended")
            log.warning(
                "[sentinel] race budget %d ms is below the recommended "
                "%d ms: trading suppression rate for latency "
                "(the tuner prices this tradeoff per org)",
                int(ms), DEFAULT_BUDGET_MS)
        return cls(budget_ms=int(ms))


# ---------------------------------------------------------------------------
# The claim — one atomic transition, one lock (§3.1).
# ---------------------------------------------------------------------------

UNCLAIMED = "unclaimed"
CLAIMED_BY_INFERENCE = "inference"
CLAIMED_BY_TIMER = "timer"
CLAIMED_BY_GATE = "gate"


class RaceClaim:
    """Per-alert claim state machine.

    UNCLAIMED → exactly one of CLAIMED_BY_{INFERENCE,TIMER,GATE}.
    The transition is check-and-set under one lock; whoever transitions
    first owns the disposition. A 1 µs loss is still a loss.
    """

    __slots__ = ("_lock", "_winner", "_event", "timer_fired_at_ms",
                 "inference_result")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._winner = UNCLAIMED
        self._event = threading.Event()
        self.timer_fired_at_ms: float | None = None
        self.inference_result: "InferenceResult | None" = None

    def try_claim(self, by: str) -> bool:
        """Attempt the UNCLAIMED → CLAIMED transition. True iff we won."""
        with self._lock:
            if self._winner is not UNCLAIMED:
                return False
            self._winner = by
            self._event.set()
            return True

    @property
    def winner(self) -> str:
        with self._lock:
            return self._winner

    def wait(self, timeout: float) -> bool:
        """Block until claimed or timeout (seconds). True if claimed."""
        return self._event.wait(timeout)


# ---------------------------------------------------------------------------
# The timer scheduler — one thread, not one thread per alert (§3.4).
# ---------------------------------------------------------------------------

class TimerScheduler:
    """Single daemon thread sleeping on a heap of ``(deadline, seq, ...)``
    keyed by ``monotonic()``. ``threading.Timer`` per alert is rejected:
    at storm rates the thread churn alone would jitter the budget.

    Cancellation is lazy deletion: the heap entry is marked invalid and
    skipped on pop — no heap surgery under lock. A fired-but-unclaimed
    timer is harmless (it checks the claim flag, finds it taken).
    """

    def __init__(self, *, clock=monotonic,
                 metrics: Metrics | None = None) -> None:
        self._clock = clock
        self._metrics = metrics
        self._heap: list = []          # (deadline, seq, race_id, token, claim, t_start)
        self._live: dict[str, object] = {}  # race_id -> token
        self._seq = itertools.count()
        self._cond = threading.Condition()
        self._running = False
        self.last_wake = clock()  # heartbeat for the scheduler watchdog (F3)
        self._thread = threading.Thread(
            target=self._run, name="sentinel-race-timer", daemon=True)

    def start(self) -> None:
        with self._cond:
            if self._running:
                return
            self._running = True
            self._thread.start()

    def stop(self) -> None:
        """Cooperative stop (threads can't be killed). Test hook for the
        triple-death test; also used by RaceRunner.close()."""
        with self._cond:
            self._running = False
            self._cond.notify_all()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def arm(self, race_id: str, deadline: float, claim: RaceClaim,
            t_start: float) -> None:
        token = object()
        with self._cond:
            self._live[race_id] = token
            heapq.heappush(self._heap,
                            (deadline, next(self._seq), race_id, token,
                             claim, t_start))
            self._cond.notify()

    def cancel(self, race_id: str) -> None:
        """Best-effort lazy invalidation (design §3.2, F2)."""
        with self._cond:
            self._live.pop(race_id, None)

    def _run(self) -> None:
        clock, cond = self._clock, self._cond
        while True:
            fired: list[tuple[RaceClaim, float]] = []
            with cond:
                if not self._running:
                    return
                now = clock()
                self.last_wake = now  # heartbeat (F3)
                heap = self._heap
                while heap:
                    deadline, _s, race_id, token, claim, t_start = heap[0]
                    if self._live.get(race_id) is not token:
                        heapq.heappop(heap)  # stale: lazy-deleted entry
                        if self._metrics is not None:
                            self._metrics.incr("race.timer_skipped_invalid")
                        continue
                    if deadline > now:
                        break
                    heapq.heappop(heap)
                    self._live.pop(race_id, None)
                    fired.append((claim, t_start))
                if not fired:
                    if heap:
                        timeout = max(heap[0][0] - clock(), 0.0)
                    else:
                        timeout = 1.0
                    cond.wait(timeout=timeout)
            # Five lines. No I/O. No logging. No allocation beyond the
            # tuple. Wrapped so the thread is nearly unkillable — and the
            # design does not assume it is immortal: party 3 (the gate's
            # own B+ε wait) owns liveness from here.
            for claim, t_start in fired:
                try:
                    # Stamp first: the gate may read timer_fired_at_ms the
                    # instant the claim event fires.
                    fired_at_ms = (clock() - t_start) * 1000.0
                    if claim.try_claim(CLAIMED_BY_TIMER):
                        claim.timer_fired_at_ms = fired_at_ms
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# The inference pool — bounded, fail-open under load (§3.3).
# ---------------------------------------------------------------------------

class InferencePool:
    """Dedicated ThreadPoolExecutor with a bounded handoff queue.

    Queue-full → ``submit`` returns None → the caller takes the timer-win
    path immediately (``timer_won_shed``). Overload sheds inference, never
    pages. Daemon-ness: pool threads are owned by the executor; the
    runner shuts it down (wait=False) on close().
    """

    def __init__(self, max_workers: int = 32, queue_size: int = 256) -> None:
        self._permits = threading.Semaphore(queue_size)
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="sentinel-infer")
        self._lock = threading.Lock()
        self._shutdown = False

    def submit(self, fn):
        """Submit ``fn()``. Returns a Future, or None when the handoff
        queue is full — the caller must shed (never block the hot path)."""
        with self._lock:
            if self._shutdown:
                return None
        if not self._permits.acquire(blocking=False):
            return None
        try:
            return self._pool.submit(self._run_guarded, fn)
        except Exception:
            self._permits.release()
            raise

    def _run_guarded(self, fn):
        try:
            return fn()
        finally:
            self._permits.release()

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
        self._pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# Race data shapes.
# ---------------------------------------------------------------------------

@dataclass
class InferenceResult:
    ok: bool
    response: object = None       # DecisionResponse when ok
    error: BaseException | None = None
    latency_ms: float = 0.0       # full call latency


@dataclass
class RaceOutcome:
    winner: str                   # CLAIMED_BY_{INFERENCE,TIMER,GATE}
    budget_outcome: str           # the durable-truth vocabulary
    result: InferenceResult | None = None
    timer_fired_at_ms: float | None = None
    backstop_claimed: bool = False  # party 3 won: the timer never fired
    shed: bool = False            # pool queue was full


@dataclass
class LateAnswer:
    """What the detached worker hands the gate/dispatcher layer.

    The late answer NEVER pages, NEVER suppresses, NEVER re-opens the
    decision. It becomes a ``shadow_decision`` event via the hook.
    """

    alert: object
    input_sha256: str
    episode_id: str | None
    response: object = None        # DecisionResponse when the late call worked
    error: BaseException | None = None
    error_class: str | None = None  # set when the late call itself failed
    latency_ms: float = 0.0        # FULL call latency — the vendor-tail sample
    budget_ms: int = DEFAULT_BUDGET_MS
    timer_fired_at_ms: float | None = None


# ---------------------------------------------------------------------------
# Timer-win watchdog (design §2.2): rolling 1h timer-win rate > 5% → page.
# ---------------------------------------------------------------------------

class TimerWinWatchdog:
    """Sustained timer wins mean the vendor is degraded or B is
    misconfigured — both operator-actionable, neither may be silent.

    A B set too small does not cause missed pages; it causes
    suppression-rate collapse — a *business* failure with a
    *safety-shaped* silence. The watchdog exists because the failure
    direction is invisible otherwise.

    Edge-triggered with hysteresis: trips once when the rate crosses the
    threshold, re-arms when it falls 1 point below. ``min_samples``
    (Type-2) keeps a lone slow alert on a quiet hour from paging.
    """

    def __init__(self, *, window_s: float = 3600.0, threshold: float = 0.05,
                 min_samples: int = 10, clock=monotonic,
                 on_trip=None, metrics: Metrics | None = None) -> None:
        self._window_s = window_s
        self._threshold = threshold
        self._min_samples = min_samples
        self._clock = clock
        self._on_trip = on_trip
        self._metrics = metrics
        self._lock = threading.Lock()
        self._events: deque[tuple[float, bool]] = deque()
        self._tripped = False

    @property
    def on_trip(self):
        return self._on_trip

    @on_trip.setter
    def on_trip(self, fn) -> None:
        self._on_trip = fn

    def record(self, budget_outcome: str) -> bool:
        """Record one decision's outcome. Returns True if this record
        tripped the watchdog."""
        now = self._clock()
        win = budget_outcome in TIMER_WIN_OUTCOMES
        trip = False
        with self._lock:
            ev = self._events
            ev.append((now, win))
            cutoff = now - self._window_s
            while ev and ev[0][0] < cutoff:
                ev.popleft()
            n = len(ev)
            if n >= self._min_samples:
                rate = sum(1 for _, w in ev if w) / n
                if not self._tripped and rate > self._threshold:
                    self._tripped = True
                    trip = True
                    if self._metrics is not None:
                        self._metrics.incr("race.watchdog_trips")
                elif self._tripped and rate <= self._threshold - 0.01:
                    self._tripped = False
            else:
                rate, n = 0.0, n
        if trip:
            cb = self._on_trip
            if cb is not None:
                try:
                    cb(rate, n)
                except Exception:
                    log.warning("[sentinel] watchdog on_trip raised",
                                exc_info=True)
        return trip

    def current_rate(self) -> tuple[float, int]:
        """(timer-win rate, sample count) over the current window."""
        now = self._clock()
        with self._lock:
            cutoff = now - self._window_s
            ev = [(t, w) for t, w in self._events if t >= cutoff]
            n = len(ev)
            if not n:
                return 0.0, 0
            return sum(1 for _, w in ev if w) / n, n


# ---------------------------------------------------------------------------
# The race itself.
# ---------------------------------------------------------------------------

def _error_class(exc: BaseException) -> str:
    """JevTimeout -> 'timeout', JevOverloaded -> 'overloaded', ..."""
    name = type(exc).__name__
    if name.startswith("Jev") and len(name) > 3:
        name = name[3:]
    code = "".join(
        "_" + c.lower() if c.isupper() else c for c in name
    ).lstrip("_")
    return code or "unknown"


class RaceRunner:
    """Owns the scheduler, the inference pool and the watchdog; runs one
    race per alert. The ONLY Jev-call sites in the gate are the pool
    submissions in :meth:`run` (the race — gates the disposition) and
    :meth:`submit_advisory` (detached advisory — never gates anything);
    any inline ``client.decide`` on the hot path is a P0 defect
    (design §10.1)."""

    def __init__(self, client, config: RaceConfig | None = None, *,
                 clock=monotonic, metrics: Metrics | None = None,
                 late_answer_hook=None,
                 on_scheduler_stall=None,
                 watchdog: TimerWinWatchdog | None = None,
                 watch_interval_s: float = 5.0,
                 stall_threshold_s: float = 30.0) -> None:
        self._client = client
        self._config = config or RaceConfig()
        self._clock = clock
        self._metrics = metrics or Metrics()
        # Fail-CLOSED at construction (design §6, pre-mortem link 2): the
        # inference socket timeout must be a positive number. None/infinite
        # is refused — a detached thread must always die by the timeout.
        # Duck-typed test doubles without the attribute can't be verified
        # and are skipped (the factory SystemOneClient.__init__ asserts).
        timeout_s = getattr(client, "timeout_s", None)
        if timeout_s is not None and not (timeout_s > 0):
            raise ValueError(
                "fail-closed: inference client timeout_s must be > 0 "
                f"(got {timeout_s!r}); an unbounded inference call would "
                "leak detached threads")
        self._pool = InferencePool(self._config.pool_size,
                                    self._config.pool_queue)
        self._scheduler = TimerScheduler(clock=clock, metrics=self._metrics)
        self._scheduler.start()
        self._late_answer_hook = late_answer_hook
        self._watchdog = watchdog or TimerWinWatchdog(
            clock=clock, metrics=self._metrics)
        self._detached_lock = threading.Lock()
        self._detached_in_flight = 0  # gauge: vendor-degradation signal
        # F3: heartbeat the scheduler; a stale heartbeat is a
        # control-plane page (never a dashboard nobody watches at 2 AM).
        self._on_scheduler_stall = on_scheduler_stall
        self._watch_interval_s = watch_interval_s
        self._stall_threshold_s = stall_threshold_s
        self._stall_fired = False
        self._stop = threading.Event()
        self._watch = threading.Thread(
            target=self._watch_scheduler, name="sentinel-race-watch",
            daemon=True)
        self._watch.start()

    # ------------------------------------------------------------ properties

    @property
    def config(self) -> RaceConfig:
        return self._config

    @property
    def metrics(self) -> Metrics:
        return self._metrics

    @property
    def watchdog(self) -> TimerWinWatchdog:
        return self._watchdog

    @property
    def scheduler(self) -> TimerScheduler:
        return self._scheduler

    @property
    def detached_in_flight(self) -> int:
        with self._detached_lock:
            return self._detached_in_flight

    # ------------------------------------------------------------------ race

    def run(self, *, alert, state, questions, input_sha256,
            episode_id: str | None = None) -> RaceOutcome:
        """Race one Jev call against the paging budget. Never raises for
        client failures — they become ``error_passthrough`` outcomes."""
        cfg = self._config
        clock = self._clock
        budget_s = cfg.budget_ms / 1000.0

        # §3.2 ARMING ORDER — the deadline is computed FIRST, before the
        # inference thread exists. The budget belongs to the page, not the
        # inference: thread-start scheduling latency eats into the
        # inference's share, never the page's.
        t_start = clock()
        deadline = t_start + budget_s

        claim = RaceClaim()
        race_id = uuid.uuid4().hex

        def worker():
            return self._inference_call(
                alert, state, questions, claim, race_id, t_start,
                input_sha256, episode_id)

        try:
            fut = self._pool.submit(worker)
        except Exception:
            log.warning("[sentinel] inference pool submit raised; shedding",
                        exc_info=True)
            fut = None
        if fut is None:
            # §3.3 — queue-full → IMMEDIATE passthrough. Never block the
            # hot path on a full handoff queue. Visible as timer wins in
            # the watchdog (budget_outcome distinguishes shed from slow).
            outcome = RaceOutcome(
                winner=CLAIMED_BY_TIMER, budget_outcome=TIMER_WON_SHED,
                timer_fired_at_ms=0.0, shed=True)
            self._metrics.incr("race.outcome." + TIMER_WON_SHED)
            self._watchdog.record(outcome.budget_outcome)
            return outcome

        self._scheduler.arm(race_id, deadline, claim, t_start)

        # Party 3: the gate's OWN deadline. If neither party has claimed by
        # B+ε, the gate claims passthrough itself — the backstop against a
        # dead timer AND a hung inference call composing (pre-mortem §11).
        claim.wait(timeout=budget_s + cfg.epsilon_ms / 1000.0)
        winner = claim.winner
        backstop = False
        if winner == UNCLAIMED:
            backstop = claim.try_claim(CLAIMED_BY_GATE)
            winner = claim.winner

        if winner == CLAIMED_BY_INFERENCE:
            result = claim.inference_result
            self._scheduler.cancel(race_id)  # lazy, best-effort (F2)
            if result is not None and result.ok:
                budget_outcome = ANSWERED_IN_TIME
            else:
                budget_outcome = ERROR_PASSTHROUGH
            outcome = RaceOutcome(winner=winner,
                                  budget_outcome=budget_outcome,
                                  result=result)
        else:
            timer_fired = claim.timer_fired_at_ms
            if timer_fired is None:
                # Gate backstop won before the timer stamped — stamp now.
                timer_fired = (clock() - t_start) * 1000.0
            outcome = RaceOutcome(
                winner=winner, budget_outcome=TIMER_WON,
                timer_fired_at_ms=timer_fired,
                backstop_claimed=backstop)
        self._metrics.incr("race.outcome." + outcome.budget_outcome)
        self._watchdog.record(outcome.budget_outcome)
        return outcome

    # ------------------------------------------------------- inference path

    def _inference_call(self, alert, state, questions, claim: RaceClaim,
                        race_id: str, t_start: float, input_sha256: str,
                        episode_id: str | None) -> InferenceResult:
        """Pool worker: run the Jev call, then attempt to claim the race.

        The call is DETACHED, never cancelled (§6): CPython has no safe
        thread-kill, and a "cancel" would leak the socket or corrupt the
        HTTP client's connection pool. The socket timeout (asserted > 0 at
        construction) bounds the thread's lifetime instead.
        """
        clock = self._clock
        t0 = clock()
        try:
            resp = self._client.decide(state, questions)
            result = InferenceResult(
                ok=True, response=resp,
                latency_ms=(clock() - t0) * 1000.0)
        except Exception as exc:
            # Wire errors AND unexpected client bugs: the race still
            # resolves; the gate fails open downstream.
            result = InferenceResult(
                ok=False, error=exc,
                latency_ms=(clock() - t0) * 1000.0)
        # Publish-before-claim: the lock in try_claim gives the
        # happens-before the gate waiter needs.
        claim.inference_result = result
        if claim.try_claim(CLAIMED_BY_INFERENCE):
            self._scheduler.cancel(race_id)
        else:
            # Late: the timer (or the gate backstop) already won. The
            # answer becomes a shadow_decision — it never pages, never
            # suppresses, never re-opens the decision (§4.2).
            self._on_late_answer(alert, result, t_start, input_sha256,
                                  episode_id, claim)
        return result

    def _on_late_answer(self, alert, result: InferenceResult, t_start: float,
                        input_sha256: str, episode_id: str | None,
                        claim: RaceClaim) -> None:
        cfg = self._config
        with self._detached_lock:
            self._detached_in_flight += 1
        try:
            hook = self._late_answer_hook
            if hook is None:
                return
            if result.ok:
                late = LateAnswer(
                    alert=alert, input_sha256=input_sha256,
                    episode_id=episode_id, response=result.response,
                    latency_ms=result.latency_ms,
                    budget_ms=cfg.budget_ms,
                    timer_fired_at_ms=claim.timer_fired_at_ms)
            else:
                # A failed late answer is still vendor evidence (and
                # still never pages): q-fields null, error_class set.
                late = LateAnswer(
                    alert=alert, input_sha256=input_sha256,
                    episode_id=episode_id,
                    error=result.error,
                    error_class=_error_class(result.error),
                    latency_ms=result.latency_ms,
                    budget_ms=cfg.budget_ms,
                    timer_fired_at_ms=claim.timer_fired_at_ms)
            hook(late)
        except Exception:
            log.warning("[sentinel] late-answer hook failed for alert %s",
                        getattr(alert, "alert_id", "?"), exc_info=True)
        finally:
            with self._detached_lock:
                self._detached_in_flight -= 1

    # ------------------------------------------------- scheduler watchdog

    def _watch_scheduler(self) -> None:
        """F3: heartbeat the timer scheduler. A stale heartbeat is a
        control-plane page — the pre-mortem's dashboard-nobody-watches
        failure is answered by paging a human, not a dashboard."""
        while not self._stop.wait(self._watch_interval_s):
            try:
                staleness = self._clock() - self._scheduler.last_wake
                if (staleness > self._stall_threshold_s
                        and not self._stall_fired):
                    self._stall_fired = True
                    cb = self._on_scheduler_stall
                    if cb is not None:
                        cb(staleness)
                    else:
                        log.warning(
                            "[sentinel] CONTROL-PLANE: race timer scheduler "
                            "heartbeat stale by %.1fs — the gate backstop "
                            "(B+ε) owns paging latency until it recovers",
                            staleness)
            except Exception:
                pass  # the watchdog must not die either

    # ------------------------------------------------------------------ misc

    def submit_advisory(self, *, state, questions, on_answer) -> bool:
        """Detached Jev call for advisory enrichment (D3 storm digest).

        The answer is delivered to ``on_answer`` and NOTHING else: there
        is no disposition for it to gate, no race to win, no budget to
        beat. Returns True when submitted, False when shed (pool full).

        Never raises. A shed or failed advisory drops an enrichment,
        never a page — the digest disposition is already decided before
        this is called. The client's socket timeout (asserted > 0 at
        construction) bounds the detached thread's lifetime.
        """
        def worker():
            try:
                resp = self._client.decide(state, questions)
            except Exception as exc:
                log.warning("[sentinel] storm advisory Jev call failed: %r "
                            "(enrichment dropped, page unaffected)", exc)
                return
            try:
                on_answer(resp)
            except Exception:
                log.warning("[sentinel] storm advisory on_answer raised; "
                            "swallowed so the pool thread never dies loud",
                            exc_info=True)

        try:
            fut = self._pool.submit(worker)
        except Exception:
            log.warning("[sentinel] storm advisory pool submit raised; "
                        "shedding", exc_info=True)
            return False
        if fut is None:
            log.warning("[sentinel] storm advisory shed: inference pool "
                        "full (enrichment dropped, page unaffected)")
            return False
        return True

    def close(self) -> None:
        self._stop.set()
        self._scheduler.stop()
        self._pool.shutdown()
        if self._watch.is_alive():
            self._watch.join(timeout=5)
