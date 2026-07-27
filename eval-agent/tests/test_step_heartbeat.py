"""StepHeartbeat keepalive for judge HTTP waits."""

from __future__ import annotations

import time
from io import StringIO
from unittest.mock import patch

from eval_agent.client.step_heartbeat import StepHeartbeat


def test_step_heartbeat_emits_step_while_waiting() -> None:
    buf = StringIO()
    with patch("sys.stdout", buf):
        with StepHeartbeat("waiting on test HTTP", interval_s=0.05):
            time.sleep(0.12)
    out = buf.getvalue()
    assert "[STEP] waiting on test HTTP" in out
