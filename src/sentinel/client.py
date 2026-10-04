"""System-One (Jev) wire client — frozen contract §3.1 of ARCHITECTURE.md.

Wire format ground truth (do not deviate):
  POST {base_url}/v1/systemone
  Headers: Authorization: Bearer <TYPESAFE_API_KEY>, Content-Type: application/json,
           custom User-Agent (urllib default gets HTTP 403)
  Body: {"state": <dict|str>, "model": "jev-1.13.0",   # PINNED id (ADR-015),
                                                # never the "jev-latest" alias
         "questions": {qid: {"type": "choice"|"noul"|"score",
                             "instructions": str, "criteria": {...}}}}
  Response: {"model": "jev-1.13.0", "answers": {...},
             "usage": {"input_tokens": int, "output_tokens": int}}

ADR-015 model pinning (D2): the client requires a pinned versioned model
id at construction. The floating "jev-latest" alias is refused unless the
caller passes allow_floating_model=True, which fires the loud boot warning
below — floating means model_drift detection has nothing to compare
against, so it is an explicit opt-out, never a default.

Retry policy:
  401 -> JevAuthError (no retry)
  422 -> JevError (no retry, surfaces field)
  429 -> JevRateLimited (backoff, honors Retry-After)
  529 / 5xx / timeout -> retry x3 within retry_budget_s, then JevOverloaded/JevTimeout
  exponential backoff + jitter.

Security: the API key arrives via TYPESAFE_API_KEY env var (client_from_env()).
It is never hardcoded, never logged, and never included in error messages.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("sentinel.client")

_API_PATH = "/v1/systemone"
#: ADR-015 (D2): the vendor's moving alias. Sentinel NEVER floats this by
#: default — the client requires a pinned versioned model id (e.g.
#: "jev-1.13.0") and refuses to construct on the alias unless the caller
#: opts out explicitly via ``allow_floating_model=True``, which fires the
#: loud boot warning below. A silent vendor remap under a floating alias
#: changes the probability mapping under fixed thresholds — that is a
#: supply-chain attack on the safety case.
FLOATING_MODEL_ALIAS = "jev-latest"
_USER_AGENT = "sentinel/0.1"

_BACKOFF_BASE_S = 0.1
_BACKOFF_JITTER_S = 0.05
_DEFAULT_RETRY_AFTER_S = 1.0


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class JevError(Exception):
    """Base for all Jev client errors."""


class JevAuthError(JevError):
    """401 — bad/missing API key. Never retried."""


class JevRateLimited(JevError):
    """429 — dynamic rate limit. Backoff, honors Retry-After."""

    def __init__(self, message: str, retry_after: float = _DEFAULT_RETRY_AFTER_S):
        super().__init__(message)
        self.retry_after = max(float(retry_after), 0.0)


class JevOverloaded(JevError):
    """529 (or other 5xx) — model overloaded. Retryable within budget."""


class JevTimeout(JevError):
    """Request timed out (or connection failed). Retryable within budget."""


# ---------------------------------------------------------------------------
# ADR-015 model pinning (D2) — resolution of the ``model`` argument.
# ---------------------------------------------------------------------------

def _floating_opt_out_warning() -> None:
    """The loud boot warning for the floating-model opt-out (ADR-015).

    ERROR/CRITICAL level + stderr banner, mirroring the ADR-005
    onboarding warning convention: a silently-floating model is a
    configuration the operator must never discover in a postmortem.
    """
    banner = (
        "[sentinel] CRITICAL: allow_floating_model=True — the Jev client "
        f"floats the vendor's moving alias {FLOATING_MODEL_ALIAS!r}. "
        "ADR-015 model pinning is BYPASSED: a silent vendor remap changes "
        "the probability mapping under fixed thresholds and model_drift "
        "detection has nothing to compare against. This is an explicit "
        "opt-out — pass a pinned versioned id (e.g. 'jev-1.13.0', from "
        "pinning.json) via the 'model' argument to re-enable pinning."
    )
    sys.stderr.write(banner + "\n")
    log.critical(banner)


def _resolve_model(model: str | None, *,
                   allow_floating_model: bool) -> str:
    """Resolve and validate the model id (ADR-015, D2).

    Returns the pinned versioned id sent on the wire. Refuses fail-closed:
    ``None``/empty is never accepted; the floating alias is accepted only
    behind the explicit ``allow_floating_model`` opt-out, loudly.
    """
    if model is None or not str(model).strip():
        raise ValueError(
            "ADR-015: a pinned Jev model id is required "
            f"(got {model!r}). Pass model='jev-1.13.0' (the "
            "pinned_model_version from pinning.json), or pass "
            "allow_floating_model=True to float 'jev-latest' as an "
            "explicit, loudly-warned opt-out."
        )
    resolved = str(model).strip()
    if resolved == FLOATING_MODEL_ALIAS:
        if not allow_floating_model:
            raise ValueError(
                f"ADR-015: refusing to float {FLOATING_MODEL_ALIAS!r} "
                "without allow_floating_model=True. A floating alias is a "
                "supply-chain attack on the safety case: the vendor can "
                "remap the probability mapping under fixed thresholds and "
                "no model_drift event will fire. Pass a pinned versioned "
                "id instead (pinning.json), or opt out explicitly."
            )
        _floating_opt_out_warning()
    return resolved


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------

@dataclass
class Answer:
    """One answered question."""

    qid: str
    qtype: str  # "choice" | "noul" | "score"
    choice: str | None
    noul: float | None
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None  # None for noul (API returns none)


@dataclass
class DecisionResponse:
    model: str  # resolved versioned id, e.g. "jev-1.13.0"
    answers: dict[str, Answer]
    input_tokens: int


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SystemOneClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.typesafe.ai",
        # ADR-015 (D2): the model is a REQUIRED pinned versioned id.
        # ``None``/empty is refused fail-closed; the floating alias
        # ("jev-latest") is refused unless ``allow_floating_model=True``
        # opts out explicitly — and that opt-out fires the loud boot
        # warning below, because a floating alias means model_drift
        # detection has nothing to compare against. The pinned id is the
        # same one the gate asserts on the response (pinning.json).
        model: str | None = None,
        allow_floating_model: bool = False,
        # ADR-010 §6: the inference call runs DETACHED on timer-win, so the
        # socket timeout — not the race budget — bounds the thread's
        # lifetime. Default 30 s; None/infinite is refused fail-closed.
        timeout_s: float = 30.0,
        max_retries: int = 3,
        retry_budget_s: float = 2.0,
    ):
        if not api_key:
            raise JevAuthError(
                "No TypeSafe API key provided. Set the TYPESAFE_API_KEY "
                "environment variable or pass api_key explicitly."
            )
        if timeout_s is None or float(timeout_s) <= 0:
            # Fail-CLOSED (design §6, pre-mortem link 2): an unbounded
            # inference call would leak detached threads forever.
            raise ValueError(
                "timeout_s must be a positive number of seconds "
                f"(got {timeout_s!r}); the race detaches — never cancels — "
                "in-flight calls, so the socket timeout is the thread's "
                "only lifetime bound")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = _resolve_model(model,
                                    allow_floating_model=allow_floating_model)
        self.timeout_s = float(timeout_s)
        self.max_retries = int(max_retries)
        self.retry_budget_s = float(retry_budget_s)

    # -- public ---------------------------------------------------------

    def decide(self, state: dict | str, questions: dict) -> DecisionResponse:
        """Send state + questions to Jev, return the typed DecisionResponse."""
        body = {"state": state, "model": self.model, "questions": questions}
        payload = self._post_with_retry(body)
        return self._parse_response(payload)

    # -- transport ------------------------------------------------------

    def _post_with_retry(self, body: dict) -> dict:
        data = json.dumps(body, ensure_ascii=True).encode("utf-8")
        url = self.base_url + _API_PATH
        deadline = time.monotonic() + self.retry_budget_s
        delay = _BACKOFF_BASE_S
        attempt = 0

        while True:
            attempt += 1
            try:
                return self._post_once(url, data)
            except JevRateLimited as exc:
                # 429: honor Retry-After, but never beyond the budget.
                if attempt > self.max_retries:
                    raise
                remaining = deadline - time.monotonic()
                if exc.retry_after > remaining:
                    raise
                time.sleep(exc.retry_after)
            except (JevOverloaded, JevTimeout):
                # Retryable: give up after max_retries or budget exhaustion.
                if attempt >= self.max_retries or time.monotonic() >= deadline:
                    raise
                remaining = deadline - time.monotonic()
                sleep_s = min(delay + random.uniform(0.0, _BACKOFF_JITTER_S), remaining)
                if sleep_s <= 0:
                    raise
                time.sleep(sleep_s)
                delay *= 2
            except JevError:
                raise  # 401/422 and friends: no retry

    def _post_once(self, url: str, data: bytes) -> dict:
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                # urllib's default User-Agent gets HTTP 403 from the API.
                "User-Agent": _USER_AGENT,
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            self._raise_for_status(exc)
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise JevTimeout(
                    f"Jev request timed out after {self.timeout_s:.1f}s"
                ) from exc
            raise JevError(f"Jev connection failed: {reason}") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JevError(f"Jev returned an unreadable response body: {exc}") from exc

    def _raise_for_status(self, exc: urllib.error.HTTPError) -> None:
        code = exc.code
        if code == 401:
            raise JevAuthError(
                "TypeSafe rejected the API key (HTTP 401). "
                "Check the TYPESAFE_API_KEY environment variable."
            ) from exc
        if code == 422:
            detail = self._read_error_body(exc)
            raise JevError(f"Jev rejected the request (HTTP 422): {detail}") from exc
        if code == 429:
            retry_after = self._parse_retry_after(exc)
            raise JevRateLimited(
                f"Jev rate-limited the request (HTTP 429); "
                f"retrying after {retry_after:.2f}s",
                retry_after=retry_after,
            ) from exc
        if code == 529:
            raise JevOverloaded(
                f"Jev model overloaded (HTTP 529); retryable within budget"
            ) from exc
        if 500 <= code <= 599:
            raise JevOverloaded(f"Jev server error (HTTP {code}); retryable within budget") from exc
        raise JevError(f"Jev request failed (HTTP {code})") from exc

    @staticmethod
    def _read_error_body(exc: urllib.error.HTTPError) -> str:
        try:
            return exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            return "(unreadable error body)"

    @staticmethod
    def _parse_retry_after(exc: urllib.error.HTTPError) -> float:
        try:
            value = exc.headers.get("Retry-After")
        except Exception:
            value = None
        if value is None:
            return _DEFAULT_RETRY_AFTER_S
        try:
            return max(float(str(value).strip()), 0.0)
        except (TypeError, ValueError):
            # HTTP-date form is rare for this API; fall back to the default.
            return _DEFAULT_RETRY_AFTER_S

    # -- response parsing -----------------------------------------------

    def _parse_response(self, payload: dict) -> DecisionResponse:
        answers: dict[str, Answer] = {}
        for qid, item in (payload.get("answers") or {}).items():
            qtype = str(item.get("type", "choice"))
            answers[str(qid)] = Answer(
                qid=str(qid),
                qtype=qtype,
                choice=item.get("choice"),
                noul=item.get("noul"),
                probabilities=dict(item.get("probabilities") or {}),
                confidence=item.get("confidence"),
            )
        usage = payload.get("usage") or {}
        try:
            input_tokens = int(usage.get("input_tokens", 0))
        except (TypeError, ValueError):
            input_tokens = 0
        return DecisionResponse(
            model=str(payload.get("model", self.model)),
            answers=answers,
            input_tokens=input_tokens,
        )


def client_from_env(**kwargs) -> SystemOneClient:
    """Build a client from the TYPESAFE_API_KEY environment variable.

    ADR-015 (D2): the model is still REQUIRED and pinned — pass
    ``model=<pinned_model_version>`` (from pinning.json). Floating
    ``jev-latest`` needs the explicit ``allow_floating_model=True``
    opt-out. Raises JevAuthError with a helpful message when the key is
    missing.
    """
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise JevAuthError(
            "TYPESAFE_API_KEY is not set. Export it in the environment; "
            "Sentinel never reads API keys from files, code, or config."
        )
    return SystemOneClient(api_key=key, **kwargs)


# ---------------------------------------------------------------------------
# Mock (for tests, eval harness, offline development)
# ---------------------------------------------------------------------------

def _canonical_state_json(state: dict | str) -> str:
    # Local copy of the canonical encoding used for fingerprints (kept here
    # to avoid a client -> state -> models -> client import cycle).
    if isinstance(state, str):
        return state
    return json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _state_fingerprint(state: dict | str) -> str:
    return hashlib.sha256(_canonical_state_json(state).encode("utf-8")).hexdigest()


class MockSystemOneClient(SystemOneClient):
    """Scripted stand-in for SystemOneClient.

    `script` maps a state fingerprint (sha256 of canonical state JSON, same
    encoding as state.input_sha256) to a canned DecisionResponse. Every call
    is recorded in `calls` for test assertions.

    ADR-015: the mock defaults to a clearly-fake pinned id
    (``jev-mock-0.0.0``) — test doubles float nothing, and the gate's drift
    assertion stays meaningful under the mock.
    """

    def __init__(self, script: dict[str, DecisionResponse] | None = None,
                 *, model: str = "jev-mock-0.0.0"):
        super().__init__(api_key="mock-key", base_url="http://mock.invalid",
                         model=model)
        self._script: dict[str, DecisionResponse] = dict(script or {})
        self._calls: list[dict] = []

    @property
    def calls(self) -> list[dict]:
        return list(self._calls)

    def decide(self, state: dict | str, questions: dict) -> DecisionResponse:
        fingerprint = _state_fingerprint(state)
        self._calls.append(
            {"fingerprint": fingerprint, "state": state, "questions": questions}
        )
        try:
            return self._script[fingerprint]
        except KeyError:
            raise JevError(
                f"MockSystemOneClient has no scripted answer for state "
                f"fingerprint {fingerprint[:16]}..."
            ) from None
