"""Shared stdout heartbeat so parent idle-kill does not fire mid-HTTP."""

from __future__ import annotations

import threading


class StepHeartbeat:
    """Print ``[STEP] …`` on a daemon thread until the context exits."""

    def __init__(self, message: str, *, interval_s: float = 45.0) -> None:
        self._message = message
        # Default 45s keeps parent idle-kill (180s) fed during long HTTP.
        # Allow sub-second intervals in tests; never go non-positive.
        self._interval_s = max(0.01, float(interval_s))
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="eval-agent-step-heartbeat",
            daemon=True,
        )

    def __enter__(self) -> "StepHeartbeat":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        # First wait, then emit — short calls should not spam STEP lines.
        while not self._stop.wait(self._interval_s):
            print(f"[STEP] {self._message}", flush=True)
