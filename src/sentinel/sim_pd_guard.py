"""Sim-mode PagerDuty safety — structural, not a toggle (audit P0, ruling X-B).

Type-1 property: when ``SENTINEL_SIM=1``, no Sentinel pipeline may address
real PagerDuty. This is the Stripe ``sk_test`` model: the mode lives in the
endpoint/credential selection itself, and a misconfiguration fails LOUDLY
at boot — never quietly pages a human.

Enforced at three levels, so no wiring path can bypass it:

  1. ``build_pipeline_from_env()`` (receiver.py): under ``SENTINEL_SIM=1``
     the forwarder is hard-wired to a loopback sink with the ``SIM-FAKE``
     routing key. If ``PD_EVENTS_URL`` or ``PD_ROUTING_KEY`` is set to a
     real value, boot REFUSES to start (fail-closed) — a real credential
     in a sim environment is a contradiction, not something to silently
     ignore.
  2. ``Forwarder.__init__`` and ``PagerDutyClient.__init__`` refuse to
     construct under ``SENTINEL_SIM=1`` with a non-loopback endpoint. Even
     an ad-hoc construction outside the receiver cannot page a human in
     sim mode. This is the line a tired maintainer cannot accidentally
     cross (principal-systems: design for the stressed maintainer who
     didn't read the docs).
  3. ``build_sim_pipeline`` (platform/server/sim/sim_runner.py) already
     asserts ``"pagerduty.com" not in fakepd_url``.

Outside sim mode this module is a no-op: production keeps its real
endpoint, reached only via explicit customer BYOK routing key + explicit
operator action (ruling X-B).
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

SIM_MODE_ENV = "SENTINEL_SIM"

# The sim PagerDuty sink: loopback only. Port 9 is the discard service —
# nothing listens there, so a POST fails closed at the socket with zero
# chance of reaching a human. The URL shape is deliberately impossible to
# confuse with the real Events API endpoint.
SIM_FAKE_SINK_URL = "http://127.0.0.1:9/fakepd"

# Deliberately NOT 32 hex chars: no shape check anywhere can mistake this
# for a real PagerDuty Events API v2 routing key.
SIM_FAKE_ROUTING_KEY = "SIM-FAKE-ROUTING-KEY"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def sim_mode() -> bool:
    """True when SENTINEL_SIM=1 (the sim pipeline / sim judge harness)."""
    return os.environ.get(SIM_MODE_ENV, "0") == "1"


def is_sim_safe_url(url: str | None) -> bool:
    """A URL is sim-safe iff it is loopback-only and cannot be PagerDuty.

    The loopback check is on the parsed hostname (not a substring), and
    "pagerduty.com" is rejected anywhere in the URL as belt-and-braces.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        return False
    if "pagerduty.com" in url.lower():
        return False
    return True


def assert_sim_pd_safe(pd_events_url: str | None, *, where: str) -> None:
    """Constructor-level last line of defense.

    Under SENTINEL_SIM=1, raise ValueError unless the endpoint is
    sim-safe. Outside sim mode this is a no-op. Called by
    Forwarder.__init__ and PagerDutyClient.__init__ — there is no way to
    hold a forwarder object wired to real PagerDuty while sim mode is on.
    """
    if sim_mode() and not is_sim_safe_url(pd_events_url):
        raise ValueError(
            f"{where}: SENTINEL_SIM=1 but the PagerDuty endpoint is not "
            f"loopback ({pd_events_url!r}) — sim environments never touch "
            "real PagerDuty. Route through enforce_sim_pd() (loopback sink "
            "+ SIM-FAKE key) or unset SENTINEL_SIM.")


def enforce_sim_pd(pd_events_url: str | None,
                   routing_key: str | None,
                   *, where: str) -> tuple[str, str]:
    """Boot-time sim PagerDuty wiring (fail-closed).

    Outside sim mode: returns (pd_events_url, routing_key) untouched —
    production keeps its real endpoint.

    Under SENTINEL_SIM=1:
      - a real endpoint in PD_EVENTS_URL (anything not loopback-safe) →
        SystemExit: refuse boot;
      - a real routing key in PD_ROUTING_KEY → SystemExit: refuse boot;
      - otherwise returns (SIM_FAKE_SINK_URL, SIM_FAKE_ROUTING_KEY) and
        announces the forced FakePD on stderr.
    """
    if not sim_mode():
        return pd_events_url, routing_key
    if pd_events_url is not None and not is_sim_safe_url(pd_events_url):
        raise SystemExit(
            f"{where}: SENTINEL_SIM=1 with PD_EVENTS_URL={pd_events_url!r} — "
            "a real PagerDuty endpoint in a sim environment is a "
            "contradiction. Unset PD_EVENTS_URL (sim hard-wires FakePD) or "
            "unset SENTINEL_SIM. Refusing to boot.")
    if routing_key is not None and routing_key != SIM_FAKE_ROUTING_KEY:
        raise SystemExit(
            f"{where}: SENTINEL_SIM=1 with PD_ROUTING_KEY set — a real "
            "routing key in a sim environment is a contradiction. Unset "
            "PD_ROUTING_KEY (sim hard-wires the SIM-FAKE key) or unset "
            "SENTINEL_SIM. Refusing to boot.")
    sys.stderr.write(
        "[sentinel] SENTINEL_SIM=1 — PagerDuty structurally forced to "
        f"FakePD (loopback sink {SIM_FAKE_SINK_URL}, key SIM-FAKE). "
        "Real PagerDuty is unreachable in this mode.\n")
    return SIM_FAKE_SINK_URL, SIM_FAKE_ROUTING_KEY
