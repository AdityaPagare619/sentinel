"""Sentinel — SRE page-or-suppress triage middleware (v0.1).

Foundation layer: Jev System-One wire client + data shapes.
Frozen contracts: ARCHITECTURE.md §§3.1–3.4.
"""

from .client import (
    Answer,
    DecisionResponse,
    JevAuthError,
    JevError,
    JevOverloaded,
    JevRateLimited,
    JevTimeout,
    MockSystemOneClient,
    SystemOneClient,
    client_from_env,
)
from .models import Alert, DecisionRecord, Disposition, Thresholds
from .questions import build_questions
from .state import STATE_TOKEN_BUDGET, build_state, estimate_tokens, input_sha256

__version__ = "0.1.0"

__all__ = [
    "Answer",
    "DecisionResponse",
    "JevAuthError",
    "JevError",
    "JevOverloaded",
    "JevRateLimited",
    "JevTimeout",
    "MockSystemOneClient",
    "SystemOneClient",
    "client_from_env",
    "Alert",
    "DecisionRecord",
    "Disposition",
    "Thresholds",
    "build_questions",
    "STATE_TOKEN_BUDGET",
    "build_state",
    "estimate_tokens",
    "input_sha256",
    "__version__",
]
