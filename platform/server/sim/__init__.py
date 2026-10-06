"""Simulation harness package — NOT the serving path.

The control-principle guards (test_guards.py) scan platform/server/*.py
only. Everything under sim/ is a test/drill harness: it MAY import the
Jev client and the forwarder (to drive the real pipeline with FakePD),
which the serving path MUST NOT do. This directory boundary is the
enforcement mechanism.
"""
