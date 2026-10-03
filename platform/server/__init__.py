"""Sentinel platform tier — the read API server (Forge's lane).

Implements platform/contracts/openapi.yaml v1.0.0. Read-only over the
engine's event log; zero Jev calls; sheds its own load before the box
goes hot so the paging receiver (a separate process/port) is never
starved.
"""
