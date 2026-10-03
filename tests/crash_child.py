"""Subprocess driver for the forwarder kill -9 crash-safety test.

The parent (tests/test_durable_forwarder.py) sets:
    FWD_CRASH_DB    — the SQLite file holding the outbox
    FWD_CRASH_PD_URL — the fake PD endpoint
    FWD_CRASH_SPILL — spill dir (optional)

The child claims one due row and attempts it with crash_after_send=True,
so it os._exit(1)s AFTER PagerDuty accepts the POST and BEFORE the I2
receipt lands — exactly the crash window design 03 §4.3 must survive.

Exit codes: 1 ⇒ the crash window was hit (expected). 2 ⇒ nothing was due
(broken setup). 3 ⇒ the attempt finished without crashing (PD didn't
accept, or the hook didn't fire — broken setup).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "src"))

from sentinel.eventlog import EventLog, utcnow_iso  # noqa: E402
from sentinel.forwarder import DurableForwarder, ForwarderConfig  # noqa: E402


def main() -> int:
    db = os.environ["FWD_CRASH_DB"]
    url = os.environ["FWD_CRASH_PD_URL"]
    spill = os.environ.get("FWD_CRASH_SPILL")
    log = EventLog(db)
    cfg = ForwarderConfig(env="test", pd_endpoint=url, workers=1,
                          crash_after_send=True, spill_dir=spill)
    fw = DurableForwarder(log, cfg)
    try:
        claimed = fw.claim_due_rows(utcnow_iso())
        if not claimed:
            return 2
        for row in claimed:
            fw._attempt(row)  # crash hook fires inside, on accept
        return 3
    finally:
        try:
            fw._conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
